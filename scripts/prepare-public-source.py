"""Copy an allowlisted source tree and reject actual configured model secrets.

No user databases, videos, private docs, credentials, or build output are copied.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
from content_factory_api.s3_settings import _unprotect_secret


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--private-config',type=Path,required=True)
    args=p.parse_args()
    payload=json.loads(args.private_config.read_text('utf-8'))
    secrets=[_unprotect_secret(m['protected_api_key']).encode() for m in payload['models']]
    args.output.mkdir(parents=True,exist_ok=False)
    roots=['apps','services','workers','packages','scripts','resources']
    extensions={'.py','.pyw','.ps1','.cjs','.mjs','.js','.ts','.tsx','.json','.toml','.lock','.yaml','.yml','.css','.html','.svg','.png','.ico','.ttf','.woff2','.txt','.md','.nsi','.rs','.xml','.pub'}
    exclude={'node_modules','__pycache__','.pytest_cache','dist','target','gen','.cache'}
    files=[]
    for name in roots:
        for base,dirs,names in __import__('os').walk(args.source/name,followlinks=False):
            dirs[:]=[d for d in dirs if d not in exclude and not (Path(base)/d).is_symlink()]
            for name in names:
                file=Path(base)/name
                if file.suffix in extensions:files.append(file)
    files += [args.source/f for f in ['package.json','pnpm-lock.yaml','pnpm-workspace.yaml','tsconfig.base.json','.editorconfig','.gitattributes','.gitignore']]
    for file in files:
        rel=file.relative_to(args.source)
        content=file.read_bytes()
        if any(secret and secret in content for secret in secrets):
            raise ValueError('Configured secret detected in '+str(rel))
        if re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)',content):
            raise ValueError('Credential pattern detected in '+str(rel))
        target=args.output/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(file,target)
    print(json.dumps({'files':len(files),'actual_model_secret_scan':'passed','business_data':'excluded'}))


if __name__=='__main__':main()
