"""Offline optional runtime. Invoked only by ConversionClient, never a provider."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import threading
import signal
import time

from custom_voice import load_runtime, validate_wav


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    args = parser.parse_args()
    parent_pid = os.getppid()

    def parent_watchdog():
        while True:
            time.sleep(0.25)
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGKILL)

    threading.Thread(target=parent_watchdog, daemon=True).start()
    protocol = sys.stdout
    # Upstream writes filenames and model messages; these are never protocol or diagnostics.
    with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        try:
            runtime = load_runtime(args.manifest, verify_assets=True)
            values = runtime.values
            sys.path.insert(0, values["kokoclone_root"])
            import torch
            from core.cloner import KokoClone
            from kanade_tokenizer import KanadeModel
            from vocos import Vocos

            cloner = KokoClone.__new__(KokoClone)
            cloner.device = torch.device("cpu")
            cloner.kanade = KanadeModel.from_pretrained(
                config_path=values["kanade_config"], weights_path=values["kanade_weights"]
            ).to(cloner.device).eval()
            cloner.vocoder = Vocos.from_hparams(values["vocos_config"])
            cloner.vocoder.load_state_dict(torch.load(values["vocos_weights"], map_location="cpu", weights_only=True))
            cloner.vocoder = cloner.vocoder.to(cloner.device).eval()
            cloner.sample_rate = cloner.kanade.config.sample_rate
            print(json.dumps({"ready": True, "runtime_id": runtime.fingerprint}), file=protocol, flush=True)
        except Exception:
            print('{"ready":false}', file=protocol, flush=True)
            return 1

        while True:
            line = sys.stdin.buffer.readline(16_385)
            if not line:
                return 0
            if len(line) > 16_384:
                return 1
            request = None
            try:
                request = json.loads(line)
                job_name = request.pop("job")
                if not isinstance(job_name, str) or not job_name.startswith("job-") or Path(job_name).name != job_name:
                    return 1
                job = args.jobs / job_name
                if job.is_symlink() or job.resolve().parent != args.jobs.resolve():
                    return 1
                validate_wav(job / "source.wav")
                validate_wav(job / "reference.wav", reference=True)
                cloner.convert(str(job / "source.wav"), str(job / "reference.wav"), str(job / "output.wav"))
                (job / "output.wav").chmod(0o600)
                validate_wav(job / "output.wav")
                response = {**request, "ok": True}
            except Exception:
                response = {**request, "ok": False} if isinstance(request, dict) else {"ok": False}
            print(json.dumps(response), file=protocol, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
