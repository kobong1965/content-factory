"""Record the actual staged distribution versions, without host environments."""
import argparse
import importlib.metadata
import json
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--payload',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    report={}
    for role in ('python','asr-python'):
        site=args.payload/'runtime'/role/'Lib/site-packages'
        rows=[]
        for dist in importlib.metadata.distributions(path=[str(site)]):
            name=dist.metadata.get('Name')
            if not name:continue
            rows.append({'name':name,'version':dist.version,
                'license':dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See package metadata',
                'license_files':[str(f) for f in (dist.files or []) if 'license' in str(f).lower() or 'copying' in str(f).lower()]})
        rows.sort(key=lambda row:row['name'].lower())
        report[role]=rows
        text='# Installed distribution metadata inventory; some test/build metadata may remain.\n'
        text+='\n'.join(row['name']+'=='+row['version'] for row in rows)+'\n'
        (args.output/(role+'-versions.txt')).write_text(text,encoding='utf-8')
    (args.output/'runtime-packages.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({role:len(rows) for role,rows in report.items()}))

if __name__=='__main__':main()
