"""Verify installed media pipeline and real encoded export, using synthetic media."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--payload',type=Path,required=True)
    p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--profile',type=Path,required=True)
    args=p.parse_args()
    root=args.payload.resolve();profile=args.profile.resolve()
    profile.mkdir(parents=True,exist_ok=False)
    code='''
import json, os, subprocess
from pathlib import Path
from content_factory_media.pipeline import MediaPipeline
from content_factory_media.tools import probe_media,sha256_file
from content_factory_contracts import validate_or_raise
root=Path(os.environ['CF_PROBE_ROOT']);work=Path(os.environ['CF_PROBE_WORK']);source=Path(os.environ['CF_PROBE_FIXTURE'])
before=sha256_file(source)
result=MediaPipeline(asr_model_path=root/'models/whisper/ggml-tiny.bin').process(source,work/'media','media_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',fixture_data=True)
value=json.loads(result.read_text('utf-8'));validate_or_raise('media_result',value)
assert value['asr']['status']=='completed' and value['asr']['segment_count']>=1
assert len(value['shots'])>=2 and sha256_file(source)==before
assert Path(value['source']['managed_original_path']).is_file()
subtitle=work/'captions.srt';subtitle.write_text('1\\n00:00:00,000 --> 00:00:01,500\\nSynthetic subtitle test\\n',encoding='utf-8')
output=work/'export.mp4'
subprocess.run([str(root/'tools/ffmpeg.exe'),'-v','error','-y','-i',str(source),'-t','3','-vf','subtitles=captions.srt','-c:v','libx264','-c:a','aac',str(output)],cwd=work,check=True)
media=probe_media(output);assert 2900<=media.duration_ms<=3100 and media.has_audio
print(json.dumps({'asr':value['asr']['status'],'shots':len(value['shots']),'export_duration_ms':media.duration_ms,'source_unchanged':True,'synthetic':True}))
'''
    env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','CONTENT_FACTORY_'))}
    env.update(CF_PROBE_ROOT=str(root),CF_PROBE_WORK=str(profile),CF_PROBE_FIXTURE=str(args.fixture.resolve()),
               CONTENT_FACTORY_RUNTIME_ROOT=str(profile/'runtime'),TEMP=str(profile),TMP=str(profile),TMPDIR=str(profile),
               PATH=str(root/'tools')+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'))
    result=subprocess.run([str(root/'runtime/python/python.exe'),'-B','-c',code],env=env,cwd=profile,capture_output=True,timeout=180)
    (profile/'stdout.txt').write_bytes(result.stdout);(profile/'stderr.txt').write_bytes(result.stderr)
    if result.returncode:raise RuntimeError('Installed media probe failed; inspect report directory')
    print(result.stdout.decode('utf-8'))

if __name__=='__main__':main()
