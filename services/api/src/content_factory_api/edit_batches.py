"""Footage-first review batches, separate from script-approved S7 projects.

Import validates the complete package before committing. Review revisions belong
to SQLite, never to mutable files in an export folder or a browser session.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

router = APIRouter(prefix='/s7/footage-batches', tags=['footage editing'])
Identity = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')]
Text = Annotated[str, Field(min_length=1, max_length=20000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class SourceClip(StrictModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class Candidate(StrictModel):
    id: Identity
    title: str = Field(min_length=1, max_length=200)
    source_path: Text
    source_start_ms: int = Field(ge=0)
    source_end_ms: int = Field(gt=0)
    clips: list[SourceClip] | None = Field(default=None, min_length=1, max_length=30)
    video_path: Text
    subtitle_path: Text
    cover_path: Text
    hook: Text
    benchmark_refs: list[Text] = Field(max_length=100)
    review_notes: list[Text] = Field(max_length=100)


class Manifest(StrictModel):
    schema_version: Literal[1]
    id: Identity
    title: str = Field(min_length=1, max_length=200)
    analysis_summary: Text
    candidates: list[Candidate] = Field(min_length=1, max_length=10)


class ImportRequest(StrictModel):
    manifest_path: Text


class ReviewRequest(StrictModel):
    revision: int = Field(ge=1)
    status: Literal['pending', 'approved', 'changes_requested']
    note: str = Field(max_length=20000)
    reviewed_by: str = Field(default='本机', min_length=1, max_length=200)


class ProductRequest(StrictModel):
    revision: int = Field(ge=1)
    sku: str = Field(max_length=100)


@contextmanager
def _db():
    root = Path(os.environ.get('CONTENT_FACTORY_S7_DATA_DIR', Path(__file__).resolve().parents[4] / 'data' / 's7'))
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'footage-batches.sqlite3', timeout=30)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL)')
        db.commit()
        with db:
            yield db
    finally:
        db.close()


def _sha(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def _probe(path: Path) -> dict:
    if path.suffix.lower() not in {'.mp4', '.mov', '.mkv', '.m4v', '.webm'}:
        raise ValueError('原片和成片必须是视频文件')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            capture_output=True, timeout=30, check=True)
    info = json.loads(result.stdout)
    video = next((s for s in info['streams'] if s.get('codec_type') == 'video'), None)
    duration = float(info['format']['duration'])
    if not video or not any(s.get('codec_type') == 'audio' for s in info['streams']):
        raise ValueError('原片和成片必须包含可识别的视频和原声音轨')
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('视频时长无效')
    return {'duration_ms': round(duration*1000), 'width': video['width'], 'height': video['height']}


def _artifact(root: Path, value: str, suffixes: set[str]) -> Path:
    relative = Path(value)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in suffixes:
        raise ValueError('成片、字幕和封面必须使用批次文件夹内的有效相对路径')
    return path


def _read(db, batch_id):
    row = db.execute('SELECT payload FROM batches WHERE id=?', (batch_id,)).fetchone()
    if not row:
        raise HTTPException(404, '剪辑批次不存在')
    return json.loads(row[0])


def _verify_resources(candidates):
    checked = set()
    for candidate in candidates:
        for resource in candidate['resources'].values():
            key = (resource['path'], resource['sha256'])
            if key in checked:
                continue
            checked.add(key)
            path = Path(resource['path'])
            try:
                intact = path.is_file() and path.stat().st_size == resource['size'] and _sha(path) == resource['sha256']
            except OSError:
                intact = False
            if not intact:
                raise HTTPException(409, '绑定文件已变化或丢失，旧审核不能用于新文件。请恢复文件或以新批次重新导入。')


@router.get('')
def list_batches():
    with _db() as db:
        return {'batches': [json.loads(row[0]) for row in db.execute('SELECT payload FROM batches ORDER BY rowid DESC')]}


@router.post('/import')
def import_batch(request: ImportRequest):
    try:
        path = Path(request.manifest_path)
        if not path.is_absolute() or path.suffix.lower() != '.json' or path.stat().st_size > 4*1024*1024:
            raise ValueError('请选择有效的本地批次 JSON 文件，最大 4 MB')
        data = Manifest.model_validate_json(path.read_text(encoding='utf-8-sig'))
        root = path.resolve().parent
        canonical = json.dumps(data.model_dump(), ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        # Do not overwrite an existing human decision even if files were moved.
        with _db() as db:
            old = db.execute('SELECT digest,payload FROM batches WHERE id=?', (data.id,)).fetchone()
            if old:
                if old[0] != digest:
                    raise HTTPException(409, '同名批次已有不同内容。请用新的批次编号导入，原审核已保留。')
                existing = json.loads(old[1])
                _verify_resources(existing['candidates'])
                return existing
        if len({c.id for c in data.candidates}) != len(data.candidates):
            raise ValueError('同一批次的成片编号不能重复')
        probe_cache, hash_cache = {}, {}
        candidates = []
        for candidate in data.candidates:
            source = Path(candidate.source_path)
            if not source.is_absolute() or not source.is_file():
                raise ValueError('原片必须是可读取的绝对视频路径')
            source = source.resolve()
            video = _artifact(root, candidate.video_path, {'.mp4', '.mov', '.mkv', '.webm'})
            subtitle = _artifact(root, candidate.subtitle_path, {'.srt'})
            cover = _artifact(root, candidate.cover_path, {'.jpg', '.jpeg', '.png', '.webp'})
            for resource in [source, video]:
                if resource not in probe_cache:
                    probe_cache[resource] = _probe(resource)
            clips = candidate.clips or [SourceClip(start_ms=candidate.source_start_ms, end_ms=candidate.source_end_ms)]
            ranges = [SourceClip(start_ms=candidate.source_start_ms, end_ms=candidate.source_end_ms), *clips]
            if any(c.end_ms <= c.start_ms or c.end_ms > probe_cache[source]['duration_ms']+50 for c in ranges):
                raise ValueError('剪辑选段超出原片时长，或结束时间早于开始时间')
            resources = {'source': source, 'video': video, 'subtitle': subtitle, 'cover': cover}
            for resource in resources.values():
                if resource not in hash_cache:
                    hash_cache[resource] = _sha(resource)
            candidates.append({**candidate.model_dump(), 'clips': [c.model_dump() for c in clips],
                               **probe_cache[video], 'review_status': 'pending', 'review_note': '',
                               'reviewed_by': '', 'reviewed_at': None,
                               'resources': {kind: {'path': str(p), 'sha256': hash_cache[p], 'size': p.stat().st_size,
                                                    'mtime_ns': p.stat().st_mtime_ns} for kind,p in resources.items()}})
        batch = {**data.model_dump(), 'candidates': candidates, 'revision': 1,
                 'created_at': datetime.now(timezone.utc).isoformat()}
        with _db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT digest,payload FROM batches WHERE id=?', (data.id,)).fetchone()
            if old:
                if old[0] != digest:
                    raise HTTPException(409, '批次已由另一个窗口导入不同内容')
                return json.loads(old[1])
            db.execute('INSERT INTO batches VALUES (?,?,?)', (data.id, digest, json.dumps(batch, ensure_ascii=False)))
        return batch
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError, KeyError, StopIteration):
        raise HTTPException(422, '批次未导入：请检查清单版本、文件路径、视频音轨和选段时间；原有批次不受影响。') from None


@router.get('/library')
def finished_library():
    with _db() as db:
        batches = [json.loads(row[0]) for row in db.execute('SELECT payload FROM batches ORDER BY rowid DESC')]
    groups, unavailable = {}, []
    for batch in batches:
        for candidate in batch['candidates']:
            if candidate['review_status'] != 'approved':
                continue
            try:
                _verify_resources([candidate])
            except HTTPException as error:
                unavailable.append({'batch_id': batch['id'], 'title': candidate['title'], 'reason': error.detail})
                continue
            sku = candidate.get('sku', batch.get('sku', '')).strip()
            groups.setdefault(sku, []).append({'batch_id': batch['id'], 'batch_title': batch['title'],
                                              'revision': batch['revision'], 'candidate': candidate})
    # Script-based renders belong in the same library, using their actual product.
    from .s7 import get_edit_store
    from .s7_store import EditConflictError
    from .s4 import get_product_store
    store = get_edit_store()
    for output in store.list_outputs():
        if output['review']['status'] != 'approved':
            continue
        try:
            path, _, _ = store.resource(output['output_id'], output['resources']['video_ref'])
            if path.stat().st_size != output['media']['size_bytes'] or _sha(path) != output['media']['sha256']:
                raise ValueError('已审核成片文件发生变化')
            project = store.get_project(output['project_id'])
            product = get_product_store().get(project['product_id'])
            sku = product.get('sku', '') or ''
            variant = next((v for v in project['variants'] if v['id'] == output['variant_id']), {})
            title = variant.get('name', output['media']['filename'])
            prefix = f"/s7/outputs/{output['output_id']}/resources/"
            groups.setdefault(sku, []).append({
                'batch_id': 'script-' + output['project_id'], 'batch_title': product['name'],
                'revision': output['project_revision'],
                'video_url': prefix + output['resources']['video_ref'],
                'subtitle_url': prefix + output['resources']['subtitle_ref'],
                'candidate': {'id': output['output_id'], 'title': title,
                              'duration_ms': output['media']['duration_ms'], 'review_status': 'approved'},
            })
        except (OSError, ValueError, LookupError, EditConflictError) as error:
            unavailable.append({'title': output['media']['filename'], 'reason': str(error)})
    return {'groups': [{'sku': sku, 'count': len(items), 'items': items} for sku, items in sorted(groups.items())],
            'total': sum(map(len, groups.values())), 'unavailable': unavailable}


@router.patch('/{batch_id}/product')
def classify_batch(batch_id: str, request: ProductRequest):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        batch = _read(db, batch_id)
        if batch['revision'] != request.revision:
            raise HTTPException(409, '批次已更新，请刷新后重试；款号输入已保留。')
        batch['sku'] = request.sku
        batch['revision'] += 1
        db.execute('UPDATE batches SET payload=? WHERE id=?', (json.dumps(batch, ensure_ascii=False), batch_id))
    return batch


@router.patch('/{batch_id}/candidates/{candidate_id}/review')
def review_candidate(batch_id: str, candidate_id: str, request: ReviewRequest):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        batch = _read(db, batch_id)
        candidate = next((c for c in batch['candidates'] if c['id'] == candidate_id), None)
        if not candidate:
            raise HTTPException(404, '成片不存在')
        if batch['revision'] != request.revision:
            raise HTTPException(409, '其他窗口已更新审核。请刷新批次后再保存，当前输入已保留。')
        _verify_resources([candidate])
        candidate.update(review_status=request.status, review_note=request.note, reviewed_by=request.reviewed_by,
                         reviewed_at=datetime.now(timezone.utc).isoformat())
        batch['revision'] += 1
        db.execute('UPDATE batches SET payload=? WHERE id=?', (json.dumps(batch, ensure_ascii=False), batch_id))
    return batch


@router.get('/{batch_id}/candidates/{candidate_id}/media/{kind}')
def candidate_media(batch_id: str, candidate_id: str, kind: str, download: bool = False):
    with _db() as db:
        batch = _read(db, batch_id)
    candidate = next((c for c in batch['candidates'] if c['id'] == candidate_id), None)
    if not candidate or kind not in candidate['resources']:
        raise HTTPException(404, '该资源不存在')
    resource = candidate['resources'][kind]
    path = Path(resource['path'])
    if not path.is_file():
        raise HTTPException(404, '本地文件已移动，请恢复原位置后重试')
    _verify_resources([{'resources': {kind: resource}}])
    media_type = ({'.mkv': 'video/x-matroska', '.mov': 'video/quicktime', '.webm': 'video/webm'}.get(path.suffix.lower(), 'video/mp4')
                  if kind in {'source', 'video'} else 'application/x-subrip' if kind == 'subtitle' else None)
    return FileResponse(path, media_type=media_type, filename=f"{candidate['id']}-{candidate['title']}{path.suffix}" if download else None)
