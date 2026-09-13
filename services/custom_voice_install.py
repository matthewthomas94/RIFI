"""Install a trusted, already provisioned cloning runtime into Relay support files.

Developer/local installation only: no downloads, reference recordings, or changes
to the standard speech environment. Publish the internal manifest only after
the relocated runtime passes the same offline startup check as speech playback.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from custom_voice import ConversionClient, CustomVoiceError, load_runtime, private_directory, support_root


def relocate_environment(python: Path, destination: Path) -> None:
    probe = subprocess.run(
        [str(python), "-I", "-c", "import json,sys,sysconfig; print(json.dumps(dict("
         "prefix=sys.prefix,base=sys.base_prefix,base_python=sys._base_executable,purelib=sysconfig.get_path('purelib'),"
         "platlib=sysconfig.get_path('platlib'))))"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    paths = json.loads(probe.stdout)
    prefix = Path(paths["prefix"]).resolve()
    if prefix == Path(paths["base"]).resolve():
        raise CustomVoiceError("source_requires_separate_environment")
    base_python = Path(paths["base_python"]).resolve()
    if base_python.is_relative_to(prefix):
        raise CustomVoiceError("source_environment_not_self_contained")
    libraries = {Path(paths[key]).resolve() for key in ("purelib", "platlib")}
    if any(not path.is_relative_to(prefix) for path in libraries):
        raise CustomVoiceError("source_environment_not_self_contained")
    # Recreate the launcher at its final location. Copying a venv's bin directory
    # would retain temporary shebangs and break when the source is removed.
    subprocess.run([str(base_python), "-I", "-m", "venv", "--without-pip", str(destination)],
                   check=True, capture_output=True, timeout=60)
    for library in libraries:
        shutil.copytree(library, destination / library.relative_to(prefix), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))


def check_runtime(runtime) -> None:
    with tempfile.TemporaryDirectory(prefix="check-", dir=runtime.path.parent) as scratch:
        client = ConversionClient(root=Path(scratch))
        try:
            client._start(runtime, lambda: False)
        finally:
            client.close()


def install_runtime(source_manifest: Path, *, root: Path | None = None) -> dict:
    root = root or support_root()
    if root.is_symlink() or (root / "services").is_symlink():
        raise CustomVoiceError("unsafe_storage")
    managed = private_directory(root / "services/custom-voice")
    manifest = root / "custom-voice-runtime.json"
    lock_fd = os.open(managed / ".install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CustomVoiceError("runtime_install_busy") from None
        if manifest.exists() or manifest.is_symlink():
            current = load_runtime(manifest, verify_assets=True)
            if any(not Path(current.values[key]).is_relative_to(managed) for key in
                   ("python", "kokoclone_root", "torch_home", "kanade_config", "kanade_weights", "vocos_config", "vocos_weights")):
                raise CustomVoiceError("runtime_already_configured_outside_managed_storage")
            return {"status": "already_installed", "manifest": str(manifest)}

        source = load_runtime(source_manifest, verify_assets=True)
        # This directory is final, private, and owned solely by this attempt.
        # A failed attempt removes only this new directory, never existing data.
        destination = Path(tempfile.mkdtemp(prefix="runtime-", dir=managed))
        published = False
        try:
            relocate_environment(Path(source.values["python"]), destination / "venv")
            upstream = destination / "upstream"
            upstream.mkdir(mode=0o700)
            shutil.copytree(Path(source.values["kokoclone_root"]) / "core", upstream / "core",
                            ignore=shutil.ignore_patterns("__pycache__"))
            license_path = Path(source.values["kokoclone_root"]) / "LICENSE"
            if license_path.is_file():
                shutil.copyfile(license_path, upstream / "LICENSE")
            values = {"schema_version": 1, "python": str(destination / "venv/bin/python"),
                      "kokoclone_root": str(upstream), "torch_home": str(destination / "torch"),
                      "sha256": source.values["sha256"]}
            models = private_directory(destination / "models")
            for key, filename in {"kanade_config": "kanade-config.yaml", "kanade_weights": "kanade.safetensors",
                                  "vocos_config": "vocos-config.yaml", "vocos_weights": "vocos.bin"}.items():
                target = models / filename
                shutil.copyfile(source.values[key], target)
                target.chmod(0o600)
                values[key] = str(target)
            torch_cache = private_directory(destination / "torch/hub/checkpoints")
            wavlm = torch_cache / "wavlm_base_plus.pth"
            shutil.copyfile(Path(source.values["torch_home"]) / "hub/checkpoints/wavlm_base_plus.pth", wavlm)
            wavlm.chmod(0o600)
            staged = destination / "runtime.json"
            with staged.open("x") as output:
                staged.chmod(0o600)
                json.dump(values, output, sort_keys=True, indent=2)
            runtime = load_runtime(staged, verify_assets=True)
            check_runtime(runtime)
            # Same-volume link publishes atomically without overwriting another
            # installation or a manifest created concurrently by an older app.
            os.link(staged, manifest)
            published = True
            return {"status": "installed", "manifest": str(manifest), "runtime": str(destination)}
        finally:
            if not published:
                shutil.rmtree(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True,
                        help="Trusted manifest from an already provisioned local environment")
    parser.add_argument("--support-root", type=Path, help="Defaults to Relay Application Support")
    args = parser.parse_args()
    try:
        print(json.dumps(install_runtime(args.source_manifest, root=args.support_root)))
        return 0
    except (CustomVoiceError, OSError, ValueError, subprocess.SubprocessError) as error:
        code = str(error) if isinstance(error, CustomVoiceError) else type(error).__name__
        print(json.dumps({"status": "failed", "error": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
