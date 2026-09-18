"""Reversible library organization, separate from immutable media and review records."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field
from . import edit_batches
from .deployment_paths import cache_root

router = APIRouter(prefix='/s7/library-management', tags=['library organization'])


@contextmanager
def database():
    root = Path(os.environ.get('CONTENT_FACTORY_S7_DATA_DIR', Path(__file__).resolve().parents[4] / 'data' / 's7'))
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'library-management.sqlite3', timeout=30)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS organization (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)')
        db.execute("INSERT OR IGNORE INTO organization VALUES(1,0,'{}')")
        db.commit()
        with db:
            yield db
    finally:
        db.close()


def inventory():
    folders = {b['id']: b for b in edit_batches.list_batches()['batches']}
    # Legacy script outputs are also shown in the library and must remain manageable.
    for group in edit_batches.finished_library()['groups']:
        for item in group['items']:
            folder = folders.setdefault(item['batch_id'], {'id': item['batch_id'], 'title': item['batch_title'], 'candidates': []})
            if not any(c['id'] == item['candidate']['id'] for c in folder['candidates']):
                folder['candidates'].append({**item['candidate'], '_video_url': item.get('video_url')})
    return folders


@router.get('')
def read_organization():
    with database() as db:
        revision, payload = db.execute('SELECT revision,payload FROM organization WHERE id=1').fetchone()
    return {'revision': revision, 'entries': json.loads(payload)}


class OrganizationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    revision: int = Field(ge=0)
    keys: list[str] = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    group: str | None = Field(default=None, max_length=80)
    deleted: bool | None = None


@router.patch('')
def organize(request: OrganizationRequest):
    folders = inventory()
    valid = set(folders) | {f"{bid}/{c['id']}" for bid, b in folders.items() for c in b['candidates']}
    if any(key not in valid for key in request.keys):
        raise HTTPException(404, '部分文件已不可用，请刷新后重试；本次未修改任何文件')
    if request.title is not None and len(request.keys) != 1:
        raise HTTPException(422, '重命名请每次选择一个文件或批次')
    changes = request.model_dump(exclude_none=True, exclude={'revision', 'keys'})
    if not changes:
        raise HTTPException(422, '请选择整理操作')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        revision, payload = db.execute('SELECT revision,payload FROM organization WHERE id=1').fetchone()
        if revision != request.revision:
            raise HTTPException(409, '素材库已在其他窗口修改，请刷新后重试')
        entries = json.loads(payload)
        for key in request.keys:
            entries[key] = {**entries.get(key, {}), **changes}
        db.execute('UPDATE organization SET revision=?,payload=? WHERE id=1', (revision + 1, json.dumps(entries, ensure_ascii=False)))
    return {'revision': revision + 1, 'entries': entries}


@router.get('/{batch_id}/archive')
def archive(batch_id: str, download: bool = True):
    folders = inventory()
    folder = folders.get(batch_id)
    entries = read_organization()['entries']
    if not folder or entries.get(batch_id, {}).get('deleted'):
        raise HTTPException(404, '批次不存在或已移入回收站')
    candidates = [c for c in folder['candidates'] if not entries.get(f"{batch_id}/{c['id']}", {}).get('deleted')]
    if not candidates:
        raise HTTPException(422, '该批次没有可下载的成片')
    # New archives are disposable cache, never written beside user source videos.
    root = cache_root('library-archives')
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'{uuid4().hex}.zip'
    title = entries.get(batch_id, {}).get('title', folder['title'])
    safe = lambda value: re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', value).strip(' .')[:80] or '成片'
    try:
        with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_STORED) as zipped:
            records = []
            for index, candidate in enumerate(candidates, 1):
                if candidate.get('_video_url'):
                    from .s7 import output_resource
                    match = re.fullmatch(r'/s7/outputs/([A-Za-z0-9_-]+)/resources/([A-Za-z0-9_-]+)', candidate['_video_url'])
                    if not match:
                        raise HTTPException(422, '历史成片地址无效')
                    response = output_resource(*match.groups(), download=True)
                else:
                    response = edit_batches.candidate_media(batch_id, candidate['id'], 'video', download=True)
                name = entries.get(f"{batch_id}/{candidate['id']}", {}).get('title', candidate['title'])
                filename = f'{index:02d}-{safe(name)}.mp4'
                zipped.write(response.path, filename)
                records.append({'file': filename, 'review_status': candidate['review_status']})
            zipped.writestr('审核状态.json', json.dumps(records, ensure_ascii=False, indent=2))
        return FileResponse(target, media_type='application/zip', filename=f'{safe(title)}.zip', background=BackgroundTask(target.unlink, missing_ok=True))
    except Exception:
        target.unlink(missing_ok=True)
        raise
