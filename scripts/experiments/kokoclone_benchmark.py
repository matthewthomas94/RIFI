import json
import os
from pathlib import Path
import resource
import sys
import time

import argparse

parser = argparse.ArgumentParser(description='Disposable RR-352 synthetic-reference benchmark; not a production adapter.')
parser.add_argument('--root', type=Path, required=True, help='Prepared isolated environment directory containing upstream checkout')
parser.add_argument('--kokoro-dir', type=Path, required=True)
args = parser.parse_args()
ROOT = args.root.resolve()
os.environ['HF_HOME'] = str(ROOT / 'hf')
os.environ['TORCH_HOME'] = str(ROOT / 'torch')
os.environ['XDG_CACHE_HOME'] = str(ROOT / 'cache')
sys.path.insert(0, str(ROOT / 'upstream'))
os.chdir(ROOT)

import numpy as np
import soundfile as sf
from huggingface_hub import snapshot_download
from kokoro_onnx import Kokoro

def emit(**values):
    values['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print('RESULT ' + json.dumps(values), flush=True)

start = time.perf_counter()
kokoro = Kokoro(str(args.kokoro_dir / 'kokoro-v1.0.onnx'), str(args.kokoro_dir / 'voices-v1.0.bin'))
emit(stage='kokoro_load', seconds=time.perf_counter()-start)
ref = ROOT / 'synthetic-reference.wav'
if not ref.exists():
    wav, sr = kokoro.create('This is a synthetic reference for a local technical experiment. We are checking speech processing speed and memory, not the identity of a real person.', voice='bm_george', speed=1.0, lang='en-us')
    sf.write(ref, wav, sr)
    emit(stage='reference', audio_seconds=len(wav)/sr)

model = snapshot_download('frothywater/kanade-12.5hz', revision='bfc4a8a753ea71394cf98e752ca68c7fbc847f0d', allow_patterns=['config.yaml', 'model.safetensors'])
from core.cloner import KokoClone
start = time.perf_counter()
import torch
from kanade_tokenizer import KanadeModel, load_vocoder
cloner = KokoClone.__new__(KokoClone)
cloner.device = torch.device('cpu')
cloner.kanade = KanadeModel.from_pretrained(config_path=str(Path(model)/'config.yaml'), weights_path=str(Path(model)/'model.safetensors')).to(cloner.device).eval()
cloner.vocoder = load_vocoder(cloner.kanade.config.vocoder_name).to(cloner.device)
cloner.sample_rate = cloner.kanade.config.sample_rate
emit(stage='clone_load', seconds=time.perf_counter()-start, device=str(cloner.device))
prompts = ['I found the issue and the focused tests pass.', 'The worker updated the voice pipeline, preserved replay and cancellation behavior, and verified equivalent Codex and Claude delivery.']
for iteration in range(3):
    for index, text in enumerate(prompts):
        start = time.perf_counter()
        wav, sr = kokoro.create(text, voice='af_bella', speed=1.0, lang='en-us')
        source = ROOT / 'source.wav'
        sf.write(source, wav, sr)
        base_seconds = time.perf_counter()-start
        output = ROOT / f'converted-{iteration}-{index}.wav'
        cpu = time.process_time()
        convert_start = time.perf_counter()
        cloner.convert(str(source), str(ref), str(output))
        convert_seconds = time.perf_counter()-convert_start
        result, result_sr = sf.read(output)
        assert len(result) and np.isfinite(result).all() and np.max(np.abs(result)) > 0
        duration = len(result)/result_sr
        emit(stage='generation', iteration=iteration, prompt=index, base_seconds=base_seconds, conversion_seconds=convert_seconds, total_seconds=base_seconds+convert_seconds, audio_seconds=duration, rtf=(base_seconds+convert_seconds)/duration, conversion_cpu_seconds=time.process_time()-cpu)
