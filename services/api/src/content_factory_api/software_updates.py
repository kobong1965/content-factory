"""Signed, bounded GitHub update downloads. No account credentials are sent."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import sqlite3
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException

from .deployment_paths import cache_root

REPOSITORY = 'kobong1965/content-factory'
DOWNLOAD_PREFIX = f'https://github.com/{REPOSITORY}/releases/download/'
ROOT = Path(__file__).resolve().parents[4]
router = APIRouter(prefix='/software-updates', tags=['system'])
_lock = threading.Lock()
_state: dict = {'phase': 'idle', 'message': '', 'downloaded': 0, 'total': 0}
_manifest: dict | None = None
_maintenance = False
_active_requests = 0


async def update_gate(request, call_next):
    global _active_requests
    from fastapi.responses import JSONResponse
    if request.url.path.startswith('/software-updates') and request.method == 'POST':
        origin = request.headers.get('origin')
        allowed = {'http://127.0.0.1:1420', 'http://localhost:1420', 'http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost'}
        if (origin and origin not in allowed) or request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
            return JSONResponse(status_code=403, content={'detail': '更新操作仅接受软件内的请求。'})
    exempt = request.url.path.startswith('/software-updates') or request.url.path == '/health'
    if exempt:
        return await call_next(request)
    with _lock:
        if _maintenance:
            return JSONResponse(status_code=503, content={'detail': '正在准备安装更新，请稍候；已有数据已保留。'})
        _active_requests += 1
    try:
        return await call_next(request)
    finally:
        with _lock:
            _active_requests -= 1


def _databases(profile: Path):
    for path in profile.rglob('*'):
        if path.suffix not in {'.sqlite3', '.sqlite', '.db'} or 'cache' in path.relative_to(profile).parts:
            continue
        if not path.resolve().is_relative_to(profile.resolve()):
            raise ValueError('数据库路径超出用户目录')
        yield path


def has_active_tasks(profile: Path) -> bool:
    active = ('pending', 'queued', 'running', 'retry_wait', 'analyzing', 'planning', 'render_pending', 'rendering', 'processing', 'exporting')
    task_tables = {'media_tasks', 'analysis_tasks', 'script_tasks', 'material_import_tasks', 'render_tasks', 'auto_edit_projects'}
    for path in _databases(profile):
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3) as connection:
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                # Parent queues are authoritative. Unprocessed segments can be
                # retained as evidence after their parent has failed.
                if name not in task_tables:
                    continue
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                    raise ValueError('数据库表名无法安全检查')
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')}
                if 'status' in columns and connection.execute(f'SELECT 1 FROM "{name}" WHERE status IN ({",".join("?" for _ in active)}) LIMIT 1', active).fetchone():
                    return True
    return False


def backup_databases(profile: Path, target: Path):
    target.mkdir(parents=True, exist_ok=False)
    for path in _databases(profile):
        destination = target / path.relative_to(profile)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as source, sqlite3.connect(destination) as output:
            source.backup(output)


@router.post('/install')
def prepare_install():
    global _maintenance
    profile_value = os.environ.get('CONTENT_FACTORY_USER_ROOT')
    if not profile_value or ROOT.parent.name != 'versions':
        raise HTTPException(409, '当前是开发/旧启动方式，请先安装新版完整安装包，再使用应用内更新。')
    with _lock:
        if _state['phase'] != 'ready' or _manifest is None:
            raise HTTPException(409, '更新尚未下载完成')
        if _active_requests or _maintenance:
            raise HTTPException(409, '仍有操作未结束，请稍后安装')
        _maintenance = True
        version = _manifest['version']
    try:
        profile = Path(profile_value).resolve()
        if has_active_tasks(profile):
            raise ValueError('仍有排队、分析、转写或剪辑任务；请完成后再安装。')
        backup = profile / 'cache/update-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_databases(profile, backup)
        pending = profile / 'runtime/update-request.json'
        pending.write_text(json.dumps({'version': version, 'backup': str(backup)}), 'utf-8')
        _set(phase='installing', message='已备份数据库，请关闭软件窗口开始安装。')
        return status()
    except Exception as exc:
        with _lock:
            _maintenance = False
        raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '准备更新失败，当前软件和数据已保留。') from exc


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('无效版本号')
    return tuple(map(int, value.split('.')))


def select_release(releases: list, current: str, preview: bool):
    options = []
    for item in releases:
        if item.get('draft') or (item.get('prerelease') and not preview):
            continue
        try:
            number = version_tuple(item.get('tag_name', '').removeprefix('v'))
        except ValueError:
            continue
        if number > version_tuple(current):
            options.append((number, item))
    return max(options, key=lambda entry: entry[0])[1] if options else None


def verify_manifest(raw: bytes, signature: bytes, public_key: bytes) -> dict:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(base64.b64decode(signature, validate=True), raw)
    except (ValueError, InvalidSignature) as exc:
        raise ValueError('更新签名无效，已拒绝下载和安装') from exc
    value = json.loads(raw)
    version_tuple(value['version'])
    files = value.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= 12:
        raise ValueError('更新文件清单无效')
    names = set()
    for part in files:
        name = part['name']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', name) or name in names:
            raise ValueError('更新文件名无效')
        names.add(name)
        if not isinstance(part['bytes'], int) or not 0 < part['bytes'] < 2 * 1024**3:
            raise ValueError('更新文件大小无效')
        if not re.fullmatch(r'[0-9a-f]{64}', part['sha256']):
            raise ValueError('更新校验值无效')
        if not re.fullmatch(re.escape(DOWNLOAD_PREFIX) + r'v\d+\.\d+\.\d+/' + re.escape(name), part['url']):
            raise ValueError('更新地址不属于指定发布仓库')
    if sum(part['bytes'] for part in files) > 6 * 1024**3:
        raise ValueError('更新包超过大小限制')
    return value


def digest(file: Path) -> str:
    with file.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download_file(part: dict, directory: Path, progress, *, opener=urlopen) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / part['name']
    if destination.is_file() and destination.stat().st_size == part['bytes'] and digest(destination) == part['sha256']:
        progress(part['bytes'])
        return destination
    temporary = destination.with_name(destination.name + '.partial')
    count = 0
    try:
        with opener(Request(part['url'], headers={'User-Agent': 'ContentFactory-Updater'}), timeout=45) as response, temporary.open('wb') as output:
            while block := response.read(256 * 1024):
                count += len(block)
                if count > part['bytes']:
                    raise ValueError('下载大小与签名清单不一致')
                output.write(block)
                progress(len(block))
        if count != part['bytes'] or digest(temporary) != part['sha256']:
            raise ValueError('下载不完整或校验失败，请重试')
        temporary.replace(destination)
        return destination
    finally:
        if temporary.exists():
            temporary.unlink()


def _read(url: str, limit: int = 2 * 1024**2) -> bytes:
    with urlopen(Request(url, headers={'User-Agent': 'ContentFactory-Updater', 'Accept': 'application/json'}), timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('更新响应超过大小限制')
    return raw


def _set(**values):
    with _lock:
        _state.update(values)


@router.get('')
def status():
    with _lock:
        return {**_state, 'current_version': json.loads((ROOT / 'package.json').read_text('utf-8'))['version']}


def _check(preview: bool):
    global _manifest
    try:
        current = json.loads((ROOT / 'package.json').read_text('utf-8'))['version']
        release = select_release(json.loads(_read(f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100')), current, preview)
        if release is None:
            _manifest = None
            _set(phase='current', message='当前通道没有更新版本。', version=None, notes='')
            return
        version = release['tag_name'].removeprefix('v')
        prefix = DOWNLOAD_PREFIX + 'v' + version + '/'
        raw = _read(prefix + 'update-manifest.json')
        signature = _read(prefix + 'update-manifest.sig', 1024)
        public_key = base64.b64decode((ROOT / 'resources/update-signing.pub').read_bytes().strip(), validate=True)
        manifest = verify_manifest(raw, signature, public_key)
        if manifest['version'] != version:
            raise ValueError('发布版本与签名清单不一致')
        directory = cache_root('updates') / version
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'update-manifest.json').write_bytes(raw)
        (directory / 'update-manifest.sig').write_bytes(signature)
        _manifest = manifest
        _set(phase='available', message='发现新版本。', version=version, notes=str(release.get('body') or '')[:20000], total=sum(p['bytes'] for p in manifest['files']), downloaded=0)
    except Exception:
        _manifest = None
        _set(phase='error', message='检查更新失败：请检查网络；发布包也可能尚未提供有效签名。当前版本和数据未改变。')


@router.post('/check')
def check(preview: bool = False):
    with _lock:
        if _state['phase'] in {'checking', 'downloading', 'installing'} or _maintenance:
            raise HTTPException(409, '正在检查或下载，请稍候')
        _state.update(phase='checking', message='正在检查 GitHub 发布…', downloaded=0)
    threading.Thread(target=_check, args=(preview,), daemon=True).start()
    return status()


def _download(manifest):
    def advance(amount):
        with _lock:
            _state['downloaded'] += amount
    try:
        directory = cache_root('updates') / manifest['version']
        for part in manifest['files']:
            # Installers retain verified immutable components for subsequent
            # updates. Models/prerequisites do not need another network transfer.
            cached = ROOT.parent.parent / 'component-cache' / part['sha256']
            if ROOT.parent.name == 'versions' and cached.is_file() and cached.stat().st_size == part['bytes'] and digest(cached) == part['sha256']:
                import shutil
                destination = directory / part['name']
                shutil.copyfile(cached, destination)
            download_file(part, directory, advance)
        _set(phase='ready', message='更新包已下载并通过签名清单校验。', directory=str(directory))
    except Exception:
        _set(phase='error', message='下载失败或校验不通过，可重新检查并重试。当前软件未改变。')


@router.post('/download')
def download():
    with _lock:
        if _state['phase'] != 'available' or _manifest is None:
            raise HTTPException(409, '请先检查并选择有效更新')
        value = _manifest
        _state.update(phase='downloading', message='正在下载更新…', downloaded=0)
    threading.Thread(target=_download, args=(value,), daemon=True).start()
    return status()
