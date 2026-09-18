"""Read-only continuity audit for the September 15 authorized desktop update."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3

REPO = Path(__file__).resolve().parents[1]
BASE = Path('E:/Codex工作盘/temp/live-edit-20260915/baseline/native-update')
OUTPUT = Path('E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tables(path):
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        result = {}
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = '"' + name.replace('"', '""') + '"'
            result[name] = Counter(db.execute('SELECT * FROM ' + quoted).fetchall())
        return result
    finally:
        db.close()


def main():
    changed_tables, database_count = [], 0
    for old in (BASE / 'data').rglob('*.sqlite3'):
        relative = old.relative_to(BASE / 'data')
        before, after = tables(old), tables(REPO / 'data' / relative)
        database_count += 1
        changed_tables.extend(f'{relative}:{name}' for name in before.keys() | after.keys()
                              if before.get(name) != after.get(name))
    inventory = json.loads((BASE / 'data-files.json').read_text(encoding='utf-8'))
    changes, excluded = [], []
    for entry in inventory:
        relative = Path(entry['path'])
        # Databases are compared transactionally above; launcher locks/logs are operational state.
        if relative.parts[0] == 'runtime' or any(s in relative.name for s in ('.sqlite3', '-wal', '-shm')):
            excluded.append(str(relative))
            continue
        current = REPO / 'data' / relative
        if not current.is_file() or digest(current) != entry['sha256']:
            changes.append(str(relative))
    metadata = [json.loads((OUTPUT / 'evidence' / n / 'metadata.json').read_text(encoding='utf-8'))
                for n in ('039', '040', '041')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        hashes = list(pool.map(lambda m: digest(Path(m['source'])), metadata))
    source_ok = all(actual == expected['sha256'] for actual, expected in zip(hashes, metadata))
    db = sqlite3.connect((REPO / 'data/s7/footage-batches.sqlite3').as_uri() + '?mode=ro', uri=True)
    try:
        batch = json.loads(db.execute('SELECT payload FROM batches WHERE id=?', ('qianchuan-20260915',)).fetchone()[0])
    finally:
        db.close()
    result = {'existing_databases': database_count, 'changed_tables': changed_tables,
              'unchanged_non_runtime_files': len(inventory) - len(excluded) - len(changes),
              'changed_non_runtime_files': changes, 'excluded_operational_files': excluded,
              'three_original_source_hashes_unchanged': source_ok,
              'batch_revision': batch['revision'], 'candidate_statuses': [c['review_status'] for c in batch['candidates']],
              'installed_executable_sha256': digest(REPO / 'dist/windows/content-factory/content-factory-desktop.exe')}
    (OUTPUT / 'continuity-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'excluded_operational_files'}, ensure_ascii=False))
    assert not changed_tables and not changes and source_ok


if __name__ == '__main__':
    main()
