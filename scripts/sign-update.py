"""Create/retain an offline Ed25519 release key and sign exact component bytes.

Private key must be outside source/public artifacts. Public key ships with app.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--key', required=True, type=Path)
    p.add_argument('--public-key', required=True, type=Path)
    p.add_argument('--delivery', type=Path)
    args = p.parse_args()
    if args.key.exists():
        key = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    else:
        args.key.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        with args.key.open('xb') as output:
            output.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = base64.b64encode(key.public_key().public_bytes_raw())
    args.public_key.parent.mkdir(parents=True, exist_ok=True)
    if args.public_key.exists() and args.public_key.read_bytes().strip() != public:
        raise ValueError('Refusing to silently rotate deployed signing key')
    args.public_key.write_bytes(public + b'\n')
    if not args.delivery:
        print('Signing key ready; private contents not printed.')
        return
    manifest = json.loads((args.delivery/'delivery-manifest.json').read_text('utf-8'))
    version = manifest['version']
    names = [part['name'] for part in manifest['parts']] + ['delivery-manifest.json', f'content-factory-{version}-setup.exe']
    parts = []
    for name in names:
        file = args.delivery/name
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        component=next((p for p in manifest['parts'] if p['name']==name),{})
        parts.append({'name': name, 'bytes': file.stat().st_size, 'sha256': digest, 'url': component.get('url',f'https://github.com/kobong1965/content-factory/releases/download/v{version}/{name}')})
    raw = json.dumps({'version': version, 'files': parts}, sort_keys=True, indent=2).encode()
    (args.delivery/'update-manifest.json').write_bytes(raw)
    (args.delivery/'update-manifest.sig').write_bytes(base64.b64encode(key.sign(raw)))
    checksum_names=names+['update-manifest.json','update-manifest.sig']
    checksums=[]
    for name in checksum_names:
        with (args.delivery/name).open('rb') as stream:
            checksums.append(hashlib.file_digest(stream,'sha256').hexdigest()+'  '+name)
    (args.delivery/'SHA256SUMS.txt').write_text('\n'.join(checksums)+'\n','utf-8')
    print('Update manifest signed:', version)


if __name__ == '__main__': main()
