"""Back up SQLite consistently and record non-runtime user files before update."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import argparse

ROOT = Path(__file__).resolve().parents[1]
BACKUP = Path('E:/Codex工作盘/temp/finished-library-021-native-baseline')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    BACKUP.mkdir(exist_ok=False, parents=True)
    files = []
    for file in (ROOT / 'data').rglob('*'):
        if not file.is_file():
            continue
        relative = file.relative_to(ROOT / 'data')
        if relative.parts[0] == 'runtime' or file.name.endswith(('-wal', '-shm')):
            continue
        if file.suffix == '.sqlite3':
            target = BACKUP / 'data' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = sqlite3.connect(file.as_uri() + '?mode=ro', uri=True)
            dest = sqlite3.connect(target)
            try:
                source.backup(dest)
            finally:
                source.close(); dest.close()
        else:
            files.append({'path': str(relative), 'sha256': sha(file)})
    (BACKUP / 'data-files.json').write_text(json.dumps(files, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'dist/windows/content-factory/content-factory-desktop.exe', BACKUP / 'content-factory-desktop.exe')
    shutil.copy2(Path('D:/桌面/爆款内容工厂.lnk'), BACKUP / '爆款内容工厂.lnk')
    print(json.dumps({'files': len(files), 'databases': len(list(BACKUP.rglob('*.sqlite3'))), 'exe_sha': sha(BACKUP / 'content-factory-desktop.exe')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backup-root')
    args = parser.parse_args()
    if args.backup_root:
        BACKUP = Path(args.backup_root).resolve()
        if not BACKUP.is_relative_to(Path('E:/Codex工作盘').resolve()):
            raise SystemExit('Backup must stay under E:/Codex工作盘')
    main()
