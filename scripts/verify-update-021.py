"""Read-only content continuity and subtitle delivery audit for 0.1.21."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import argparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'services/api/src'))
from content_factory_api.subtitle_design import caption_text

BASE = Path('E:/Codex工作盘/temp/finished-library-021-native-baseline')
VIDEOS = Path('E:/Codex工作盘/artifacts/latest/千川对标剪辑-20260915-字幕新版/videos')
REPORT = Path('E:/Codex工作盘/temp/finished-library-021-continuity.json')


def sha(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


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
    missing_rows, changed_files, subtitle_mismatches = [], [], []
    for old in (BASE / 'data').rglob('*.sqlite3'):
        relative = old.relative_to(BASE / 'data')
        after = tables(REPO / 'data' / relative)
        for name, rows in tables(old).items():
            if rows - after.get(name, Counter()):
                missing_rows.append(f'{relative}:{name}')
    for item in json.loads((BASE / 'data-files.json').read_text(encoding='utf-8')):
        path = REPO / 'data' / item['path']
        if not path.is_file() or sha(path) != item['sha256']:
            changed_files.append(item['path'])
    captions = 0
    for path in VIDEOS.glob('*/subtitles.srt'):
        expected = [caption_text('\n'.join(block.splitlines()[2:])) for block in path.read_text(encoding='utf-8-sig').strip().split('\n\n')]
        ass = path.with_suffix('.ass').read_text(encoding='utf-8-sig')
        events = [line.split(',', 9) for line in ass.splitlines() if line.startswith('Dialogue:')]
        captions += len(events)
        if expected != [line[9] for line in events] or any(line[3] != 'Default' for line in events):
            subtitle_mismatches.append(path.parent.name)
        assert 'Style: Default,SimHei,68,' in ass and 'Style: Title,' not in ass
        qa = json.loads((path.parent / 'technical-qa.json').read_text(encoding='utf-8'))
        assert qa['full_decode_pass'] and sha(path.parent / 'review.mp4') == qa['sha256']
    report = {'lost_or_changed_existing_tables': missing_rows, 'changed_user_files': changed_files,
              'subtitle_mismatches': subtitle_mismatches, 'caption_count': captions,
              'videos': len(list(VIDEOS.glob('*/review.mp4'))),
              'shortcut_unchanged': sha(Path('D:/桌面/爆款内容工厂.lnk')) == sha(BASE / '爆款内容工厂.lnk')}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    assert not missing_rows and not changed_files and not subtitle_mismatches and report['shortcut_unchanged']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline')
    parser.add_argument('--report')
    args = parser.parse_args()
    if args.baseline:
        BASE = Path(args.baseline).resolve()
    if args.report:
        REPORT = Path(args.report).resolve()
        if not REPORT.is_relative_to(Path('E:/Codex工作盘').resolve()):
            raise SystemExit('Report must stay under E:/Codex工作盘')
    main()
