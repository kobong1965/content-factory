"""Verify GitHub's stored asset digests against local delivery checksums."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--gh',required=True)
    p.add_argument('--repo',required=True)
    p.add_argument('--tag',required=True)
    p.add_argument('--delivery',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    result=subprocess.run([args.gh,'release','view',args.tag,'--repo',args.repo,
        '--json','assets,isDraft,isPrerelease,url,tagName'],capture_output=True,check=True,encoding='utf-8')
    release=json.loads(result.stdout)
    assets={asset['name']:asset for asset in release['assets']}
    checked=[]
    for line in (args.delivery/'SHA256SUMS.txt').read_text('utf-8').splitlines():
        expected,name=line.split('  ',1)
        if Path(name).name!=name:raise ValueError('Invalid checksum filename')
        file=args.delivery/name
        with file.open('rb') as stream:local=hashlib.file_digest(stream,'sha256').hexdigest()
        remote=assets.get(name)
        if not remote or remote['state']!='uploaded':raise ValueError('Asset not uploaded: '+name)
        if local!=expected or remote.get('digest')!='sha256:'+expected or remote['size']!=file.stat().st_size:
            raise ValueError('Asset integrity mismatch: '+name)
        checked.append({'name':name,'bytes':remote['size'],'sha256':expected})
    report={'tag':release['tagName'],'url':release['url'],'draft':release['isDraft'],
            'prerelease':release['isPrerelease'],'assets':checked,'passed':True,
            'publisher_code_signature':False}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'assets':len(checked),'draft':release['isDraft']}))

if __name__=='__main__':main()
