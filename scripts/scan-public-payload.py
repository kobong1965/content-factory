"""Stream-scan public archives for the current internal model secrets."""
import argparse
import json
from pathlib import Path
import zipfile
from content_factory_api.s3_settings import _unprotect_secret

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    args=p.parse_args()
    config=json.loads(args.config.read_text('utf-8'))
    secrets=[_unprotect_secret(m['protected_api_key']).encode() for m in config['models']]
    overlap=max(map(len,secrets))
    count=0
    with zipfile.ZipFile(args.archive) as archive:
        for member in archive.infolist():
            if member.is_dir():continue
            lower=member.filename.lower()
            if any(s in lower for s in ('gateway-config.json','model-config.cfcfg','delivery-password.txt')) or lower.endswith(('.sqlite3','.db','.mp4','.mov','.log')):
                raise ValueError('Private file in public archive: '+member.filename)
            with archive.open(member) as stream:
                tail=b''
                while chunk:=stream.read(1024*1024):
                    value=tail+chunk
                    if any(secret and secret in value for secret in secrets):
                        raise ValueError('Configured key found in archive: '+member.filename)
                    tail=value[-overlap:]
            count+=1
    print(json.dumps({'archive':args.archive.name,'files_scanned':count,'known_secret_scan':'passed'}))

if __name__=='__main__':main()
