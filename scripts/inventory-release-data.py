"""Read-only private deployment inventory. Never emits database cell contents.

SQLite backup gives a consistent snapshot per database, including its WAL.
Snapshots stay private: queued tasks can contain machine-protected credentials.
This is inventory, not an importable package or a cross-database transaction.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3


def inspect_value(value, location, references, protected):
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r'api.?key|token|secret|password|authorization', str(key), re.I):
                if item:
                    protected.add(location + ':' + str(key))
                continue
            inspect_value(item, location, references, protected)
    elif isinstance(value, list):
        for item in value:
            inspect_value(item, location, references, protected)
    elif isinstance(value, str):
        if re.match(r'^[A-Za-z]:[\\/]', value) and '\n' not in value and len(value) < 4096:
            references.setdefault(value, set()).add(location)
        elif value.lstrip().startswith(('{', '[')):
            try:
                inspect_value(json.loads(value), location, references, protected)
            except (ValueError, RecursionError):
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    allowed = Path('E:/Codex工作盘').resolve()
    if not output.is_relative_to(allowed) or output.is_relative_to(source):
        parser.error('Output must be a new E:/Codex工作盘 directory outside source')
    output.mkdir(parents=True, exist_ok=False)
    references, protected, files, databases, errors = {}, set(), [], [], []
    for path in sorted((source / 'data').rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(source).as_posix()
        files.append({'path': relative, 'bytes': path.stat().st_size})
        try:
            if path.suffix in ('.sqlite', '.sqlite3', '.db'):
                target = output / 'private-db-snapshots' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as incoming:
                    with sqlite3.connect(target) as snapshot:
                        incoming.backup(snapshot)
                with sqlite3.connect(target) as snapshot:
                    tables = []
                    for (name,) in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                        quoted = '"' + name.replace('"', '""') + '"'
                        cursor = snapshot.execute('SELECT * FROM ' + quoted)
                        columns = [column[0] for column in cursor.description]
                        count = 0
                        for row in cursor:
                            count += 1
                            inspect_value(dict(zip(columns, row)), relative + ':' + name, references, protected)
                        tables.append({'table': name, 'rows': count})
                    databases.append({'path': relative, 'tables': tables, 'integrity': snapshot.execute('PRAGMA integrity_check').fetchone()[0]})
            elif path.suffix.lower() == '.json' and path.stat().st_size < 32 * 1024 * 1024:
                inspect_value(json.loads(path.read_text(encoding='utf-8-sig')), relative, references, protected)
        except (OSError, ValueError, sqlite3.Error) as exc:
            errors.append({'path': relative, 'error_type': type(exc).__name__})
    resolved = []
    for value, locations in sorted(references.items()):
        path = Path(value)
        try:
            exists = path.exists()
            kind = 'file' if path.is_file() else 'directory' if path.is_dir() else 'missing'
            resolved.append({'path': value, 'exists': exists, 'kind': kind, 'bytes': path.stat().st_size if kind == 'file' else None,
                             'external': not path.resolve().is_relative_to(source), 'referenced_by': sorted(locations)})
        except OSError as exc:
            errors.append({'path': value, 'error_type': type(exc).__name__})
    report = {'scope': 'PRIVATE inventory; not for public release',
              'consistency': 'Per-database snapshots; final export must pause mutations across stores',
              'files': files, 'databases': databases, 'references': resolved,
              'protected_field_locations': sorted(protected), 'errors': errors}
    (output / 'inventory-private.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(files), 'bytes': sum(f['bytes'] for f in files),
                      'databases': len(databases), 'references': len(resolved),
                      'missing_references': sum(not r['exists'] for r in resolved),
                      'protected_field_locations': len(protected), 'errors': len(errors)}))


if __name__ == '__main__':
    main()
