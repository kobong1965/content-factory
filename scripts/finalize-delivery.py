"""Promote verified public files without bringing private configuration along."""
import argparse
import hashlib
from pathlib import Path
import shutil

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--documentation',type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    names=['content-factory-0.1.30-setup.exe','content-factory-0.1.30-program.zip',
           'content-factory-0.1.30-speech-model.zip','content-factory-0.1.30-prerequisites.zip','delivery-manifest.json']
    hashes=[]
    for name in names:
        target=args.output/name
        shutil.copyfile(args.source/name,target)
        with target.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
        hashes.append(sha+'  '+name)
    (args.output/'SHA256SUMS.txt').write_text('\n'.join(hashes)+'\n',encoding='utf-8')
    for name in ('README.md','RELEASE-0.1.30.md','COMPONENTS.md','DEPLOYMENT-TESTS.md'):
        shutil.copyfile(args.documentation/name,args.output/name)
    print('Public delivery prepared. Internal configuration and password excluded.')

if __name__=='__main__':main()
