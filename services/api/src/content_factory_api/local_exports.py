"""Explicit local delivery for desktop webviews without a browser download UI."""
import hashlib
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .edit_batches import candidate_media
from .s7 import output_resource
from .deployment_paths import export_root

router = APIRouter(tags=['local delivery'])
DEFAULT_ROOT = Path('E:/Codex工作盘/artifacts/latest/男装编剪器下载')


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource: str = Field(min_length=1, max_length=1000)


def _digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


@router.post('/local-exports')
def export_resource(request: ExportRequest):
    footage = re.fullmatch(r'/s7/footage-batches/([A-Za-z0-9_-]+)/candidates/([A-Za-z0-9_-]+)/media/(video|subtitle|cover)', request.resource)
    script = re.fullmatch(r'/s7/outputs/([A-Za-z0-9_-]+)/resources/([A-Za-z0-9_-]+)', request.resource)
    archive_match = re.fullmatch(r'/s7/library-management/([A-Za-z0-9_-]+)/archive', request.resource)
    if footage:
        response = candidate_media(*footage.groups(), download=True)
    elif script:
        response = output_resource(*script.groups(), download=True)
    elif archive_match:
        from .library_management import archive
        response = archive(archive_match.group(1))
    else:
        raise HTTPException(422, '仅支持保存本软件的成片、字幕和剪辑交付文件')
    source = Path(response.path)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', response.filename or source.name).strip(' .')
    name_path = Path(name)
    root = export_root()
    token = uuid4().hex
    target = root / f'{name_path.stem[:100]}-{token[:8]}{name_path.suffix}'
    temporary = root / f'{token}.partial'
    try:
        root.mkdir(parents=True, exist_ok=True)
        expected = _digest(source)
        with source.open('rb') as incoming, temporary.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if _digest(temporary) != expected or _digest(source) != expected:
            raise HTTPException(409, '保存期间源文件发生变化，请重新检查成片后再下载')
        # An atomic, exclusive link makes a partial copy invisible as a completed
        # download, and never overwrites another file even in concurrent requests.
        os.link(temporary, target)
        return {'path': str(target), 'filename': target.name, 'size': target.stat().st_size, 'sha256': expected}
    except OSError:
        raise HTTPException(500, '文件保存失败，请检查下载目录所在磁盘的剩余空间和文件夹权限后重试') from None
    finally:
        if temporary.exists():
            temporary.unlink()
        if archive_match:
            source.unlink(missing_ok=True)
