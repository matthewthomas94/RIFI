#!/usr/bin/env python3
"""Render an isolated Settings preview WAV; the app owns cancellable playback."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import wave
from pathlib import Path
import signal
from dataclasses import replace
import threading
import time

import numpy as np
from custom_voice import ConversionClient, load_profile, load_runtime, validate_wav


def _find_kokoro_model() -> tuple[str, str] | None:
    """Find Kokoro model + voices files. Mirrors tts_worker.py search paths."""
    candidates = []
    bundled = os.environ.get("VOICE_MODELS_DIR")
    if bundled:
        candidates.append(os.path.join(bundled, "kokoro"))
    candidates.append(os.path.expanduser("~/.local/share/kokoro"))

    for d in candidates:
        model = os.path.join(d, "kokoro-v1.0.onnx")
        voices = os.path.join(d, "voices-v1.0.bin")
        if os.path.isfile(model) and os.path.isfile(voices):
            return model, voices
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--custom-voice-id", default="")
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args()

    # Own a process group so Settings can cancel this process without touching a session.
    try:
        os.setsid()
    except OSError:
        pass
    converter = ConversionClient() if args.custom_voice_id else None

    def terminate(*_):
        if converter:
            converter.close()
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, terminate)
    parent_pid = os.getppid()

    def parent_watchdog():
        while True:
            time.sleep(0.25)
            if os.getppid() != parent_pid:
                if converter:
                    converter.close()
                os._exit(130)

    threading.Thread(target=parent_watchdog, daemon=True).start()

    paths = _find_kokoro_model()
    if paths is None:
        print("[preview_voice] Kokoro model not found", file=sys.stderr)
        return 1

    try:
        from kokoro_onnx import Kokoro
    except ImportError as e:
        print(f"[preview_voice] kokoro_onnx not installed: {e}", file=sys.stderr)
        return 1

    kokoro = Kokoro(*paths)
    samples, sample_rate = kokoro.create(
        args.text, voice=args.voice, speed=1.0, lang="en-us"
    )
    if samples is None or len(samples) == 0:
        print("[preview_voice] Synthesis returned no samples", file=sys.stderr)
        return 1

    samples = np.asarray(samples)
    if not np.isfinite(samples).all():
        return 1
    int16_audio = (np.clip(samples, -1, 1) * 32767).astype(np.int16)

    fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="relay-preview-")
    os.close(fd)
    try:
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(int16_audio.tobytes())

        if converter:
            profile = load_profile(args.custom_voice_id, draft=args.draft)
            runtime = load_runtime()
            # Preview is the explicit revalidation path after runtime updates.
            # The saved profile remains unchanged until the app accepts Use Voice.
            profile = replace(profile, runtime_id=runtime.fingerprint)
            converter.convert(Path(wav_path), args.output, profile, runtime,
                              utterance_id="settings-preview", generation=0)
        else:
            import shutil
            shutil.copyfile(wav_path, args.output)
            args.output.chmod(0o600)
        validate_wav(args.output)
    except Exception:
        print("[preview_voice] Preview failed; check the reference and local runtime.", file=sys.stderr)
        return 1
    finally:
        if converter:
            converter.close()
        try:
            os.remove(wav_path)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("[preview_voice] Preview unavailable. Check local speech setup.", file=sys.stderr)
        sys.exit(1)
