"""Read-only logical comparison against an explicit pre-update database backup."""
import json
import sqlite3
import sys
from pathlib import Path

baseline, current = (Path(value).resolve() for value in sys.argv[1:3])
changed = []
count = 0
for previous in baseline.rglob('*.sqlite3'):
    relative = previous.relative_to(baseline)
    with sqlite3.connect(previous.as_uri() + '?mode=ro', uri=True) as old:
        with sqlite3.connect((current / relative).as_uri() + '?mode=ro', uri=True) as new:
            for (table,) in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                query = 'SELECT * FROM "' + table.replace('"', '""') + '"'
                if sorted(map(repr, old.execute(query))) != sorted(map(repr, new.execute(query))):
                    changed.append({'database': str(relative), 'table': table})
    count += 1
print(json.dumps({'databases': count, 'changed_tables': changed}, ensure_ascii=False))
assert count > 0 and not changed
