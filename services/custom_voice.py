"""Private custom-voice profiles and a bounded, optional conversion process.

No ML packages are imported here. The base Kokoro environment never imports
Torch or Kanade; only an explicitly provisioned, offline child process does.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave

from config import _default_config_path

ID_RE = re.compile(r"^[a-f0-9]{32}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_JSON = 16_384


class CustomVoiceError(Exception):
    """Only privacy-safe, typed error codes cross the runtime boundary."""


class ConversionCancelled(CustomVoiceError):
    pass


def support_root() -> Path:
    return Path(_default_config_path()).parent


def voices_root() -> Path:
    return support_root() / "voices"


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise CustomVoiceError("unsafe_storage")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise CustomVoiceError("unsafe_storage")
    path.chmod(0o700)
    return path.resolve()


def read_private_file(path: Path, limit: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
                raise CustomVoiceError("invalid_asset")
            data = source.read(limit + 1)
        if len(data) > limit:
            raise CustomVoiceError("invalid_asset")
        return data
    except OSError:
        raise CustomVoiceError("asset_unavailable") from None


def read_json(path: Path) -> tuple[dict, str]:
    data = read_private_file(path, MAX_JSON)
    try:
        value = json.loads(data)
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError()
        return value, hashlib.sha256(data).hexdigest()
    except (ValueError, TypeError):
        raise CustomVoiceError("invalid_manifest") from None


def validate_wav(path: Path, *, reference: bool = False) -> tuple[int, int]:
    # Bound the read before the decoder; all managed files are mono PCM16.
    read_private_file(path, 3_000_000)
    try:
        with wave.open(str(path), "rb") as source:
            frames, rate = source.getnframes(), source.getframerate()
            if (source.getnchannels(), source.getsampwidth(), rate) != (1, 2, 24000):
                raise ValueError()
            duration = frames / rate
            if not (5 <= duration <= 10 if reference else 0 < duration <= 60):
                raise ValueError()
            data = source.readframes(frames)
            if len(data) != frames * 2:
                raise ValueError()
            import array
            pcm = array.array("h", data)
            if sys.byteorder != "little":
                pcm.byteswap()
            if not pcm or sum(float(x) ** 2 for x in pcm) / len(pcm) < 32 ** 2:
                raise ValueError()
            return frames, rate
    except (ValueError, wave.Error, EOFError, OSError):
        raise CustomVoiceError("invalid_audio") from None


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    reference: Path
    content_hash: str
    runtime_id: str


def load_profile(profile_id: str, root: Path | None = None, *, draft: bool = False) -> VoiceProfile:
    if not isinstance(profile_id, str) or not ID_RE.fullmatch(profile_id):
        raise CustomVoiceError("invalid_profile_id")
    root = root or voices_root()
    if root.is_symlink():
        raise CustomVoiceError("unsafe_storage")
    parent = root / ".drafts" if draft else root
    folder = parent / profile_id
    if parent.is_symlink() or folder.is_symlink():
        raise CustomVoiceError("unsafe_storage")
    if folder.resolve().parent != parent.resolve():
        raise CustomVoiceError("unsafe_storage")
    manifest, _ = read_json(folder / "manifest.json")
    if manifest.get("id") != profile_id or manifest.get("sample_rate") != 24000:
        raise CustomVoiceError("invalid_profile")
    digest = manifest.get("content_hash", "")
    if not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
        raise CustomVoiceError("invalid_profile")
    reference = folder / "reference.wav"
    data = read_private_file(reference, 500_000)
    if hashlib.sha256(data).hexdigest() != digest:
        raise CustomVoiceError("reference_changed")
    frames, rate = validate_wav(reference, reference=True)
    name = manifest.get("name")
    if (not isinstance(name, str) or not 0 < len(name.strip()) <= 80
            or manifest.get("affirmation_version") != 1
            or not manifest.get("affirmation_at") or not manifest.get("created_at")
            or manifest.get("duration") != frames / rate
            or not isinstance(manifest.get("base_voice"), str) or not manifest["base_voice"]):
        raise CustomVoiceError("invalid_profile")
    runtime_id = manifest.get("runtime_id", "")
    if not isinstance(runtime_id, str) or not HASH_RE.fullmatch(runtime_id):
        raise CustomVoiceError("invalid_profile")
    return VoiceProfile(profile_id, reference, digest, runtime_id)


@dataclass(frozen=True)
class RuntimeManifest:
    path: Path
    fingerprint: str
    values: dict


def load_runtime(path: Path | None = None, *, verify_assets: bool = False) -> RuntimeManifest:
    path = path or support_root() / "custom-voice-runtime.json"
    values, fingerprint = read_json(path)
    for key in ("python", "kokoclone_root", "kanade_config", "kanade_weights",
                "vocos_config", "vocos_weights", "torch_home"):
        value = values.get(key)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise CustomVoiceError("invalid_runtime")
        target = Path(value)
        if key in {"kokoclone_root", "torch_home"}:
            if not target.is_dir():
                raise CustomVoiceError("runtime_unavailable")
        elif not target.is_file():
            raise CustomVoiceError("runtime_unavailable")
    if not os.access(values["python"], os.X_OK):
        raise CustomVoiceError("runtime_unavailable")
    assets = {
        **{key: Path(values[key]) for key in
           ("kanade_config", "kanade_weights", "vocos_config", "vocos_weights")},
        "cloner": Path(values["kokoclone_root"]) / "core/cloner.py",
        "chunked_convert": Path(values["kokoclone_root"]) / "core/chunked_convert.py",
        "wavlm": Path(values["torch_home"]) / "hub/checkpoints/wavlm_base_plus.pth",
    }
    hashes = values.get("sha256", {})
    if not isinstance(hashes, dict):
        raise CustomVoiceError("invalid_runtime")
    for key, asset in assets.items():
        digest = hashes.get(key, "")
        if not isinstance(digest, str) or not HASH_RE.fullmatch(digest) or not asset.is_file():
            raise CustomVoiceError("runtime_unavailable")
        if verify_assets:
            with asset.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != digest:
                    raise CustomVoiceError("runtime_asset_changed")
    return RuntimeManifest(path, fingerprint, values)


class ConversionClient:
    """One request at a time; cancellation can kill a child during model loading."""

    def __init__(self, *, root: Path | None = None, load_timeout: float = 30,
                 chunk_timeout: float = 15):
        self.root = root or voices_root()
        self.load_timeout, self.chunk_timeout = load_timeout, chunk_timeout
        self._request_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._process = None
        self._runtime_id = None
        self._epoch = 0
        self._buffer = b""

    def close(self):
        with self._process_lock:
            self._epoch += 1
            process, self._process = self._process, None
            self._runtime_id = None
        self._terminate(process)

    @staticmethod
    def _terminate(process):
        if process is None:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=1)
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()

    def _read(self, process, timeout, cancelled):
        deadline = time.monotonic() + timeout
        while True:
            if cancelled():
                raise ConversionCancelled("cancelled")
            if b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                try:
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError()
                    return value
                except ValueError:
                    raise CustomVoiceError("invalid_runtime_response") from None
            if time.monotonic() >= deadline:
                raise CustomVoiceError("conversion_timeout")
            if process.poll() is not None:
                raise CustomVoiceError("runtime_exited")
            try:
                readable, _, _ = select.select([process.stdout], [], [], 0.05)
                if readable:
                    data = os.read(process.stdout.fileno(), 4096)
                    if not data:
                        raise CustomVoiceError("runtime_exited")
                    self._buffer += data
                    if len(self._buffer) > MAX_JSON:
                        raise CustomVoiceError("invalid_runtime_response")
            except (ValueError, OSError):
                raise ConversionCancelled("cancelled") from None

    def _start(self, runtime, cancelled):
        with self._process_lock:
            process = self._process
            if process is not None and process.poll() is None and self._runtime_id == runtime.fingerprint:
                return process
        self.close()
        with self._process_lock:
            epoch = self._epoch
        jobs = private_directory(private_directory(self.root) / ".jobs")
        command = [runtime.values["python"], "-u", str(Path(__file__).with_name("custom_voice_worker.py")),
                   "--manifest", str(runtime.path), "--jobs", str(jobs)]
        if sys.platform == "darwin":
            command = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)", *command]
        # Do not pass provider credentials, prompt paths, or user PYTHONPATH.
        environment = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG") if key in os.environ}
        environment.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", PYTHONDONTWRITEBYTECODE="1",
                           TORCH_HOME=runtime.values["torch_home"], TOKENIZERS_PARALLELISM="false")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, cwd=jobs, env=environment,
                                   start_new_session=True, bufsize=0)
        with self._process_lock:
            if epoch != self._epoch or cancelled():
                self._terminate(process)
                raise ConversionCancelled("cancelled")
            self._process, self._runtime_id = process, runtime.fingerprint
            self._buffer = b""
        ready = self._read(process, self.load_timeout, cancelled)
        if ready != {"ready": True, "runtime_id": runtime.fingerprint}:
            raise CustomVoiceError("runtime_not_ready")
        return process

    def convert(self, source: Path, output: Path, profile: VoiceProfile, runtime: RuntimeManifest,
                *, utterance_id: str, generation: int, cancelled=lambda: False):
        # No concurrent speculative conversions against the non-thread-safe model.
        while not self._request_lock.acquire(timeout=0.05):
            if cancelled():
                raise ConversionCancelled("cancelled")
        job = None
        try:
            if cancelled():
                raise ConversionCancelled("cancelled")
            if runtime.fingerprint != profile.runtime_id:
                raise CustomVoiceError("runtime_changed_preview_again")
            process = self._start(runtime, cancelled)
            jobs = private_directory(private_directory(self.root) / ".jobs")
            job = Path(tempfile.mkdtemp(prefix="job-", dir=jobs))
            for name, path in (("source.wav", source), ("reference.wav", profile.reference)):
                shutil.copyfile(path, job / name)
                (job / name).chmod(0o600)
            if hashlib.sha256((job / "reference.wav").read_bytes()).hexdigest() != profile.content_hash:
                raise CustomVoiceError("reference_changed")
            identity = {"request_id": uuid.uuid4().hex, "utterance_id": utterance_id,
                        "generation": generation, "profile_id": profile.id,
                        "reference_hash": profile.content_hash, "runtime_id": runtime.fingerprint}
            request = {**identity, "job": job.name}
            process.stdin.write((json.dumps(request) + "\n").encode())
            response = self._read(process, self.chunk_timeout, cancelled)
            if any(response.get(key) != value for key, value in identity.items()):
                raise CustomVoiceError("stale_runtime_response")
            if not response.get("ok"):
                raise CustomVoiceError("conversion_failed")
            converted = job / "output.wav"
            validate_wav(converted)
            if cancelled() or not profile.reference.is_file():
                raise ConversionCancelled("cancelled")
            shutil.copyfile(converted, output)
            output.chmod(0o600)
        except (OSError, ValueError):
            self.close()
            raise CustomVoiceError("runtime_unavailable") from None
        except CustomVoiceError:
            self.close()
            raise
        finally:
            try:
                if job is not None:
                    shutil.rmtree(job)
            finally:
                self._request_lock.release()
