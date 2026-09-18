"""Build non-sensitive deterministic component archives from a runtime stage."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    groups={'program':[],'speech-model':[],'prerequisites':[]}
    for f in sorted(args.stage.rglob('*')):
        if not f.is_file(): continue
        rel=f.relative_to(args.stage)
        if any(s in rel.parts for s in ('__pycache__','internal','install-temp')) or f.name=='staging-manifest.json': continue
        if f.suffix in ('.pyc','.log','.sqlite3','.cfcfg') or f.name in ('gateway-config.json','delivery-password.txt'):
            raise ValueError('Private/unexpected file in public payload: '+str(rel))
        group='speech-model' if rel.parts[:2]==('models','large-v3-turbo') else 'prerequisites' if rel.parts[0]=='prerequisites' else 'program'
        groups[group].append((f,rel.as_posix()))
    manifest={'version':'0.1.30','parts':[],'required':['content-factory-desktop.exe','runtime/python/pythonw.exe','runtime/asr-python/python.exe','models/large-v3-turbo/model.bin','models/whisper/ggml-tiny.bin','tools/ffmpeg.exe','tools/ffprobe.exe','scripts/installed_launcher.py']}
    for name,files in groups.items():
        target=args.output/f'content-factory-0.1.30-{name}.zip'
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
            for file,rel in files:z.write(file,rel)
        with target.open('rb') as stream: sha=hashlib.file_digest(stream,'sha256').hexdigest()
        if target.stat().st_size>=2*1024**3:raise ValueError('Asset exceeds GitHub size limit')
        manifest['parts'].append({'name':target.name,'bytes':target.stat().st_size,'unpacked_bytes':sum(f.stat().st_size for f,_ in files),'sha256':sha})
        print(name,target.stat().st_size,flush=True)
    (args.output/'delivery-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')


if __name__=='__main__':main()
