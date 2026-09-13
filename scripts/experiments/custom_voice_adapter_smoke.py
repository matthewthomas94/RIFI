"""Opt-in RR-353 adapter test using an already provisioned RR-352 environment.

No downloads, playback, app config changes, or user reference recordings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services"))
from custom_voice import ConversionClient, load_profile, load_runtime, validate_wav


def write_wav(path, samples, rate):
    import numpy as np
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((np.asarray(samples).clip(-1, 1) * 32767).astype("<i2").tobytes())

parser = argparse.ArgumentParser(description=__doc__)
runtime_source = parser.add_mutually_exclusive_group(required=True)
runtime_source.add_argument("--prepared-root", type=Path)
runtime_source.add_argument("--runtime-manifest", type=Path, help="Check the installed runtime without its temporary source")
parser.add_argument("--kokoro-dir", type=Path, required=True)
args = parser.parse_args()
prepared = args.prepared_root.resolve() if args.prepared_root else None
root = Path(tempfile.mkdtemp(prefix="rr353-adapter-"))
root.chmod(0o700)
if prepared:
    kanade = prepared / "hf/hub/models--frothywater--kanade-12.5hz/snapshots/bfc4a8a753ea71394cf98e752ca68c7fbc847f0d"
    vocos = prepared / "hf/hub/models--charactr--vocos-mel-24khz/snapshots/0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21"
    values = {
        "schema_version": 1,
        "python": str(prepared / "venv/bin/python"),
        "kokoclone_root": str(prepared / "upstream"),
        "torch_home": str(prepared / "torch"),
        "kanade_config": str(kanade / "config.yaml"),
        "kanade_weights": str(kanade / "model.safetensors"),
        "vocos_config": str(vocos / "config.yaml"),
        "vocos_weights": str(vocos / "pytorch_model.bin"),
    }
    assets = {key: Path(values[key]) for key in ("kanade_config", "kanade_weights", "vocos_config", "vocos_weights")}
    assets.update(cloner=prepared / "upstream/core/cloner.py", chunked_convert=prepared / "upstream/core/chunked_convert.py",
                  wavlm=prepared / "torch/hub/checkpoints/wavlm_base_plus.pth")
    values["sha256"] = {}
    for key, path in assets.items():
        with path.open("rb") as source:
            values["sha256"][key] = hashlib.file_digest(source, "sha256").hexdigest()
    manifest_path = root / "runtime.json"
    manifest_path.write_text(json.dumps(values, sort_keys=True, indent=2))
    manifest_path.chmod(0o600)
else:
    manifest_path = args.runtime_manifest.resolve()
runtime = load_runtime(manifest_path, verify_assets=True)

from kokoro_onnx import Kokoro
kokoro = Kokoro(str(args.kokoro_dir / "kokoro-v1.0.onnx"), str(args.kokoro_dir / "voices-v1.0.bin"))
profile_id = "a" * 32
folder = root / "voices" / profile_id
folder.mkdir(parents=True, mode=0o700)
reference = folder / "reference.wav"
if prepared:
    shutil.copyfile(prepared / "synthetic-reference.wav", reference)
else:
    samples, rate = kokoro.create("This is a synthetic voice reference for a local runtime test. No person's recording is used.",
                                 voice="bm_george", speed=1, lang="en-us")
    write_wav(reference, samples[:int(rate * 8)], rate)
reference.chmod(0o600)
frames, rate = validate_wav(reference, reference=True)
(folder / "manifest.json").write_text(json.dumps({
    "schema_version": 1, "id": profile_id, "name": "Synthetic test only",
    "content_hash": hashlib.sha256(reference.read_bytes()).hexdigest(),
    "sample_rate": rate, "duration": frames / rate, "runtime_id": runtime.fingerprint,
    "affirmation_version": 1, "affirmation_at": "synthetic-test", "created_at": "synthetic-test", "base_voice": "bm_george",
}))
(folder / "manifest.json").chmod(0o600)
profile = load_profile(profile_id, root / "voices")

samples, rate = kokoro.create("The custom voice adapter is running locally. This is a synthetic test.", voice="bm_george", speed=1, lang="en-us")
source = root / "source.wav"
write_wav(source, samples, rate)
source.chmod(0o600)
client = ConversionClient(root=root / "voices")
try:
    for attempt in range(3):
        if attempt == 2:
            client.close()  # Fresh offline child, not merely a hot-model repeat.
        start = time.monotonic()
        output = root / f"output-{attempt}.wav"
        client.convert(source, output, profile, runtime, utterance_id=f"smoke-{attempt}", generation=attempt)
        frames, rate = validate_wav(output)
        print(json.dumps({"attempt": attempt, "seconds": round(time.monotonic() - start, 3),
                          "audio_seconds": frames / rate, "sample_rate": rate, "offline": True}), flush=True)
finally:
    client.close()
print(json.dumps({"artifacts": str(root), "jobs_cleaned": not list((root / "voices/.jobs").iterdir())}))
