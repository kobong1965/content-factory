"""Operator-only export; package and password must be delivered separately."""
import argparse
from pathlib import Path
import secrets

from content_factory_api.internal_config_package import export_config, import_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-config', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--password-file', type=Path, required=True)
    parser.add_argument('--verify-config', type=Path, required=True)
    args = parser.parse_args()
    targets = [args.package.resolve(), args.password_file.resolve(), args.verify_config.resolve()]
    allowed = Path('E:/Codex工作盘').resolve()
    if any(not p.is_relative_to(allowed) or p.exists() for p in targets):
        parser.error('Use new target files in E:/Codex工作盘')
    if targets[0].parent == targets[1].parent:
        parser.error('Keep the password outside the package directory')
    password = secrets.token_urlsafe(32)
    package = export_config(args.source_config, password)
    # Validate before publishing. This host test does not claim a different
    # user's DPAPI import or a real provider call has been verified.
    import_config(package, password, targets[2])
    targets[0].parent.mkdir(parents=True, exist_ok=True)
    targets[1].parent.mkdir(parents=True, exist_ok=True)
    with targets[1].open('x', encoding='utf-8') as stream:
        stream.write(password + '\n')
    with targets[0].open('xb') as stream:
        stream.write(package)
    print('Encrypted internal package exported and locally reimported. No secret values printed.')


if __name__ == '__main__':
    main()
