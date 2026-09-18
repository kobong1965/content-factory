"""Packaged user paths, with unchanged development defaults.

The installed launcher sets CONTENT_FACTORY_USER_ROOT. Never derive writable
locations from the installation directory, which may be read-only.
"""
import os
import re
from pathlib import Path


def export_root() -> Path:
    explicit = os.environ.get('CONTENT_FACTORY_EXPORT_ROOT')
    user = os.environ.get('CONTENT_FACTORY_USER_ROOT')
    default = Path(user) / 'exports' if user else Path('E:/Codex工作盘/artifacts/latest/男装编剪器下载')
    return Path(explicit or default).expanduser().resolve()


def cache_root(category: str) -> Path:
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', category):
        raise ValueError('Invalid cache category')
    user = os.environ.get('CONTENT_FACTORY_USER_ROOT')
    base = Path(user) / 'cache' if user else Path('E:/Codex工作盘/temp/content-factory')
    return base.resolve() / category
