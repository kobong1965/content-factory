"""Password-encrypted internal delivery; never expose through public API routes.

Only a separate password can unlock this package. API keys exist in plaintext
in process memory during migration, never in intermediate files. Target storage
is protected again by the existing Windows DPAPI implementation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .s3_settings import GatewaySettingsStore, _protect_secret, _unprotect_secret

MAGIC = b'CF-INTERNAL-CONFIG\x01'
MAX_BYTES = 1024 * 1024


def _key(password: str, salt: bytes) -> bytes:
    if not isinstance(password, str) or not 20 <= len(password) <= 1024:
        raise ValueError('交付口令长度须为 20 至 1024 个字符')
    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode('utf-8'))


def _models(payload):
    if not isinstance(payload, dict) or payload.get('schema_version') not in ('2.0.0', '2.1.0'):
        raise ValueError('不支持的配置版本，请先在原软件确认模型配置')
    models = payload.get('models')
    if not isinstance(models, list) or not 1 <= len(models) <= 8 or not all(isinstance(m, dict) for m in models):
        raise ValueError('模型配置格式无效')
    return models


def export_config(source: Path, password: str) -> bytes:
    if source.stat().st_size > MAX_BYTES:
        raise ValueError('配置文件过大')
    GatewaySettingsStore(source).load()
    payload = json.loads(source.read_text(encoding='utf-8'))
    for model in _models(payload):
        if 'api_key' in model or not isinstance(model.get('protected_api_key'), str):
            raise ValueError('原配置的密钥格式无效')
        model['api_key'] = _unprotect_secret(model.pop('protected_api_key'))
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = MAGIC + salt + nonce
    return header + AESGCM(_key(password, salt)).encrypt(nonce, plaintext, header)


def import_config(package: bytes, password: str, destination: Path) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError('已存在本机模型配置，不重复导入或覆盖')
    header_size = len(MAGIC) + 28
    if not header_size + 16 <= len(package) <= MAX_BYTES or not package.startswith(MAGIC):
        raise ValueError('内部配置包格式无效或不完整')
    header = package[:header_size]
    salt, nonce = header[len(MAGIC):len(MAGIC)+16], header[-12:]
    try:
        plaintext = AESGCM(_key(password, salt)).decrypt(nonce, package[header_size:], header)
        payload = json.loads(plaintext)
        for model in _models(payload):
            if 'protected_api_key' in model or not isinstance(model.get('api_key'), str):
                raise ValueError('配置格式无效')
            model['protected_api_key'] = _protect_secret(model.pop('api_key'))
    except (InvalidTag, UnicodeError, KeyError, TypeError, ValueError):
        raise ValueError('交付口令不正确或配置包损坏，本机配置未修改') from None
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + uuid4().hex + '.config-import')
    try:
        # Windows DPAPI binds each encrypted key to the recipient's user account.
        # Directory permissions are supplied by that user's private profile.
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        GatewaySettingsStore(temporary).load()
        # Exclusive publication also prevents concurrent first-run imports from
        # overwriting a config saved by the recipient while import was running.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
