"""Offline dependency/model smoke test, not a speech accuracy acceptance test."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--workdir', required=True, type=Path)
    args = parser.parse_args()
    root, work = args.payload.resolve(), args.workdir.resolve()
    if not work.is_relative_to(Path('E:/Codex工作盘').resolve()):
        parser.error('Use the work drive')
    work.mkdir(parents=True, exist_ok=False)
    audio = work / 'silence.wav'
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\0' * 32000)
    job = work / 'job.json'
    output = work / 'result.json'
    job.write_text(json.dumps({'audio':str(audio),'model':str(root / 'models/large-v3-turbo'),
                               'output':str(output),'hotwords':'裤子 裤脚口 裤腰 尺码'}), encoding='utf-8')
    env = {key:value for key,value in os.environ.items() if not key.startswith(('PYTHON', 'CONTENT_FACTORY_'))}
    env.update(TEMP=str(work), TMP=str(work), TMPDIR=str(work), HF_HOME=str(work / 'hf'), HF_HUB_OFFLINE='1',
               PATH=str(Path(os.environ['SystemRoot']) / 'System32'))
    with (work / 'worker.log').open('wb') as log:
        result = subprocess.run([str(root / 'runtime/asr-python/python.exe'), '-B',
                                  str(root / 'scripts/speech-caption-worker.py'), '--job',str(job)],
                                 cwd=work, env=env, stdout=log, stderr=log, timeout=180,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError('Offline packaged ASR failed; inspect worker.log')
    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['words'] == [], 'Silence must not produce invented dialogue'
    assert payload['duration_ms'] == 1000
    assert (work / 'jieba.cache').is_file(), 'Tokenizer cache must stay in the isolated profile'
    print(json.dumps({'offline_model_loaded':True,'silent_audio_processed':True,
                      'speech_accuracy_test':False,'clean_windows':False}))


if __name__ == '__main__':
    main()
