"""Read-only scan of tracked/non-ignored files. Never prints secret values."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from content_factory_api.s3_settings import _unprotect_secret

if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    args=p.parse_args()
    config=json.loads(args.config.read_text('utf-8'))
    secrets=[_unprotect_secret(m['protected_api_key']).encode() for m in config['models']]
    files=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=args.repo).decode('utf-8').split('\0')
    count=0
    for name in set(files):
        if not name:continue
        file=args.repo/name
        if not file.is_file():continue
        raw=file.read_bytes()
        if any(secret and secret in raw for secret in secrets):raise ValueError('Configured credential found in '+name)
        if re.search(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----\r?\n)',raw):
            raise ValueError('Credential-shaped content found in '+name)
        count+=1
    print(json.dumps({'files':count,'known_credentials':'not_found','private_key_patterns':'not_found'}))
