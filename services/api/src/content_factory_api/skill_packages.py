"""Review-before-import for inert, structured team Skill packages.

Never execute scripts or extract arbitrary archive members. Video references in
these packages are evidence snapshots, not a claim that source media is present.
"""
import os
from pathlib import PurePosixPath
import zipfile
from uuid import uuid4
from typing import Literal
from fastapi import APIRouter, File, UploadFile, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from .s3 import get_viral_skill_store
from .skill_bundle import evidence_archive_payload, inspect_bundle, import_bundle, write_bundle

router = APIRouter(prefix='/s3/skill-packages', tags=['Skill packages'])
MAX_SIZE = 32 * 1024**2
MAX_EVIDENCE_ARCHIVE_SIZE = 1024 * 1024**2
UPLOAD_CHUNK_SIZE = 1024 * 1024


def _inbox():
    root = get_viral_skill_store().database_path.parent / 'incoming'
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.post('/preview')
async def preview_package(package: UploadFile = File(...)):
    temp = _inbox() / (uuid4().hex + '.upload')
    try:
        name = (package.filename or '').lower()
        if not (name.endswith('.zip') or name.endswith('.cfskills')):
            raise ValueError('请选择 .cfskills 或包含它的 ZIP；普通 SKILL.md 尚不能直接作为有证据的剪辑方法导入')

        received = 0
        with temp.open('wb') as output:
            while True:
                chunk = await package.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                received += len(chunk)
                if received > (MAX_SIZE if name.endswith('.cfskills') else MAX_EVIDENCE_ARCHIVE_SIZE):
                    limit = '32 MB' if name.endswith('.cfskills') else '1 GB'
                    raise ValueError(f'上传的 Skill 包不能超过 {limit}；视频与关键帧不会写入 Skill 包')
                output.write(chunk)

        if name.endswith('.zip'):
            converted_raw = None
            converted_payload = None
            with zipfile.ZipFile(temp) as archive:
                members = archive.infolist()
                for member in members:
                    normalized = member.filename.replace('\\', '/')
                    path = PurePosixPath(normalized.rstrip('/'))
                    if not normalized or path.is_absolute() or '..' in path.parts or ':' in normalized:
                        raise ValueError('压缩包包含不安全路径')
                bundles = [i for i in members if i.filename.lower().endswith('.cfskills')]
                if len(bundles) > 1:
                    raise ValueError('ZIP 中只能包含一个 .cfskills 结构化包')
                if bundles:
                    if bundles[0].file_size > MAX_SIZE:
                        raise ValueError('ZIP 中的 .cfskills 结构化包不能超过 32 MB')
                    converted_raw = archive.read(bundles[0])
                else:
                    # Large Codex evidence archives are converted to a small
                    # inert bundle by reading only approved JSON snapshots.
                    converted_payload = evidence_archive_payload(archive)
            # Windows does not permit replacing a file while ZipFile still
            # owns its handle, so all writes happen after the context closes.
            if converted_raw is not None:
                temp.write_bytes(converted_raw)
            elif converted_payload is not None:
                temp.unlink(missing_ok=True)
                temp = _inbox() / (uuid4().hex + '.cfskills')
                write_bundle(converted_payload, temp)
        payload, checksum = inspect_bundle(temp)
        if not payload['skills']:
            raise ValueError('此包没有可导入的 Skill，请先分析并审核方法后重新打包')
        destination = _inbox() / (checksum + '.cfskills')
        os.replace(temp, destination)
        return {'package_id': checksum, 'skills': [
            {'name': s['name'], 'mechanism': s['mechanism'], 'skill_id': s['skill_id'], 'revision': s['revision']}
            for s in payload['skills']], 'source_videos_included': False}
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        temp.unlink(missing_ok=True)
        await package.close()


class Approval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    package_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: Literal[True]


@router.post('/approve')
def approve_package(request: Approval):
    try:
        path = _inbox() / (request.package_id + '.cfskills')
        _, checksum = inspect_bundle(path)
        if checksum != request.package_id:
            raise ValueError('包内容已变化，请重新预览')
        return import_bundle(path, get_viral_skill_store().database_path)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from None
