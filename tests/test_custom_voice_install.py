import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
import custom_voice_install as installer
from custom_voice import CustomVoiceError, load_runtime


class CustomVoiceInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.support = self.root / "Application Support/relay-runner"
        self.source = self.root / "temporary-source"
        self.source.mkdir()
        self.manifest = self.source / "source.json"
        values = {"schema_version": 1, "python": sys.executable,
                  "kokoclone_root": str(self.source / "upstream"), "torch_home": str(self.source / "torch")}
        assets = {"cloner": self.source / "upstream/core/cloner.py",
                  "chunked_convert": self.source / "upstream/core/chunked_convert.py",
                  "wavlm": self.source / "torch/hub/checkpoints/wavlm_base_plus.pth"}
        for key in ("kanade_config", "kanade_weights", "vocos_config", "vocos_weights"):
            # Reproduce Hugging Face snapshots: install must copy real data,
            # not retain symlinks into a temporary source cache.
            blob = self.source / f"{key}-blob"
            blob.write_bytes(key.encode())
            link = self.source / key
            link.symlink_to(blob)
            assets[key] = link
            values[key] = str(link)
        for key, path in assets.items():
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(key.encode())
        (self.source / "upstream/core/__init__.py").touch()
        (self.source / "upstream/LICENSE").write_text("Fixture license")
        (self.source / "private-reference.wav").write_bytes(b"do not install this")
        values["sha256"] = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in assets.items()}
        self.manifest.write_text(json.dumps(values))

    @staticmethod
    def fake_environment(python, destination):
        (destination / "bin").mkdir(parents=True)
        (destination / "bin/python").symlink_to(sys.executable)

    def install(self):
        with patch.object(installer, "relocate_environment", side_effect=self.fake_environment), \
             patch.object(installer, "check_runtime"):
            return installer.install_runtime(self.manifest, root=self.support)

    def test_install_copies_pinned_assets_and_publishes_private_manifest(self):
        self.support.mkdir(parents=True)
        config = self.support / "config.toml"
        config.write_text('[tts]\nvoice = "bm_george"\n')
        result = self.install()
        runtime = load_runtime(Path(result["manifest"]), verify_assets=True)
        self.assertEqual(result["status"], "installed")
        managed = self.support / "services/custom-voice"
        for key in ("python", "kokoclone_root", "torch_home", "kanade_config", "kanade_weights", "vocos_config", "vocos_weights"):
            self.assertTrue(Path(runtime.values[key]).is_relative_to(managed.resolve()))
        self.assertEqual(Path(result["manifest"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(result["runtime"]).stat().st_mode & 0o777, 0o700)
        self.assertEqual(config.read_text(), '[tts]\nvoice = "bm_george"\n')
        self.assertFalse((self.support / "voices").exists())
        self.assertFalse(list(managed.rglob("private-reference.wav")))
        self.assertEqual((Path(runtime.values["kokoclone_root"]) / "LICENSE").read_text(), "Fixture license")
        shutil.rmtree(self.source)
        self.assertEqual(load_runtime(runtime.path, verify_assets=True).fingerprint, runtime.fingerprint)

    def test_repeat_install_preserves_manifest_and_works_after_source_is_gone(self):
        first = self.install()
        manifest = Path(first["manifest"])
        before = manifest.read_bytes()
        shutil.rmtree(self.source)
        with patch.object(installer, "relocate_environment") as copy:
            second = installer.install_runtime(self.manifest, root=self.support)
        self.assertEqual(second["status"], "already_installed")
        copy.assert_not_called()
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(len(list((self.support / "services/custom-voice").glob("runtime-*"))), 1)

    def test_failed_startup_removes_only_attempt_and_never_publishes(self):
        self.support.mkdir(parents=True)
        existing = self.support / "config.toml"
        existing.write_text("preserve")
        with patch.object(installer, "relocate_environment", side_effect=self.fake_environment), \
             patch.object(installer, "check_runtime", side_effect=CustomVoiceError("runtime_not_ready")):
            with self.assertRaisesRegex(CustomVoiceError, "runtime_not_ready"):
                installer.install_runtime(self.manifest, root=self.support)
        self.assertFalse((self.support / "custom-voice-runtime.json").exists())
        self.assertEqual(list((self.support / "services/custom-voice").glob("runtime-*")), [])
        self.assertEqual(existing.read_text(), "preserve")
        self.assertTrue(self.manifest.exists())

    def test_corrupt_source_asset_is_rejected_before_copy(self):
        (self.source / "kanade_weights-blob").write_bytes(b"changed")
        with patch.object(installer, "relocate_environment") as copy:
            with self.assertRaisesRegex(CustomVoiceError, "runtime_asset_changed"):
                installer.install_runtime(self.manifest, root=self.support)
        copy.assert_not_called()
        self.assertFalse((self.support / "custom-voice-runtime.json").exists())

    def test_existing_external_configuration_is_not_overwritten(self):
        self.support.mkdir(parents=True)
        manifest = self.support / "custom-voice-runtime.json"
        original = self.manifest.read_bytes()
        manifest.write_bytes(original)
        with self.assertRaisesRegex(CustomVoiceError, "runtime_already_configured"):
            self.install()
        self.assertEqual(manifest.read_bytes(), original)

    def test_symlink_destination_is_rejected_without_writing_through_it(self):
        self.support.parent.mkdir(parents=True)
        self.support.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(CustomVoiceError, "unsafe_storage"):
            self.install()
        self.assertFalse((self.source / "services").exists())

    def test_concurrent_install_is_rejected(self):
        managed = self.support / "services/custom-voice"
        managed.mkdir(parents=True)
        with (managed / ".install.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(CustomVoiceError, "runtime_install_busy"):
                self.install()

    def test_environment_relocation_uses_new_launchers_and_local_packages(self):
        source = self.root / "source-venv"
        target = self.root / "permanent-venv"
        venv.EnvBuilder(with_pip=False).create(source)
        library = next((source / "lib").glob("python*/site-packages"))
        (library / "relocation_probe.py").write_text("VALUE = 'relocated'\n")
        installer.relocate_environment(source / "bin/python", target)
        shutil.rmtree(source)
        result = subprocess.run([str(target / "bin/python"), "-I", "-c",
                                 "import relocation_probe,sys; print(relocation_probe.VALUE); print(sys.prefix)"],
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(), ["relocated", str(target.resolve())])
        self.assertNotIn(str(source), (target / "pyvenv.cfg").read_text())

    def test_startup_check_closes_child_and_cleans_private_scratch_on_failure(self):
        runtime = load_runtime(self.manifest)
        with patch.object(installer, "ConversionClient") as factory:
            client = factory.return_value
            client._start.side_effect = CustomVoiceError("runtime_not_ready")
            with self.assertRaises(CustomVoiceError):
                installer.check_runtime(runtime)
            client.close.assert_called_once()
        self.assertEqual(list(self.source.glob("check-*")), [])


if __name__ == "__main__":
    unittest.main()
