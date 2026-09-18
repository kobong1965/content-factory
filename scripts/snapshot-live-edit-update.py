"""Take an explicit rollback baseline for the authorized in-place desktop update."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

repo=Path(__file__).resolve().parents[1]
target=Path('E:/Codex工作盘/temp/live-edit-20260915/baseline/native-update')
target.mkdir(parents=True,exist_ok=False)
data=repo/'data'
for source in data.rglob('*.sqlite3'):
    dest=target/'data'/source.relative_to(data); dest.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(dest) as out:
        src.backup(out)
def fingerprint(path):
    with path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    return {'path':str(path.relative_to(data)),'bytes':path.stat().st_size,'sha256':digest}
with ThreadPoolExecutor(max_workers=4) as pool:
    inventory=list(pool.map(fingerprint,[p for p in data.rglob('*') if p.is_file()]))
(target/'data-files.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
exe=repo/'dist/windows/content-factory/content-factory-desktop.exe'
shutil.copy2(exe,target/'content-factory-desktop-0.1.19.exe')
for rel in ['package.json','apps/desktop/package.json','apps/desktop/src-tauri/Cargo.toml','apps/desktop/src-tauri/Cargo.lock','apps/desktop/src-tauri/tauri.conf.json']:
    dest=target/'source'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(repo/rel,dest)
print(json.dumps({'backup':str(target),'data_files':len(inventory)},ensure_ascii=False))
