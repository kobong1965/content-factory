"""Installer worker: verify all payloads, stage, probe, then publish a new version.

Never edits user data or removes an existing installed version. Invoked by NSIS.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import uuid
import zipfile
from urllib.request import urlopen


def install_vc_runtime(executable, environment):
    """Windows requests elevation only if the required machine runtime is absent."""
    child_env=environment.copy()
    child_env['CF_PREREQUISITE_EXE']=str(executable)
    # No user path is interpolated as PowerShell code. Cancelling UAC fails the
    # install before publication and leaves any old application version intact.
    script="$ErrorActionPreference='Stop'; try { $p=Start-Process -FilePath $env:CF_PREREQUISITE_EXE -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -Wait -PassThru; exit $p.ExitCode } catch { exit 1 }"
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',script],env=child_env,
                          creationflags=subprocess.CREATE_NO_WINDOW).returncode


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_extract(archive, target):
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            name = PurePosixPath(item.filename.replace('\\', '/'))
            if name.is_absolute() or '..' in name.parts or any(':' in part for part in name.parts):
                raise ValueError('Unsafe archive member')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symlinks are not permitted in payloads')
            destination = target.joinpath(*name.parts)
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError('Archive path escapes destination')
        source.extractall(target)


def verify_payloads(source, manifest):
    for part in manifest['parts']:
        name = part['name']
        if Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Invalid payload filename')
        file = source / name
        if not file.is_file() or file.stat().st_size != part['bytes'] or digest(file) != part['sha256']:
            raise ValueError('Missing or damaged component: ' + name)


def copy_internal_config(source, destination):
    config=source/'model-config.cfcfg'
    target=destination/'internal/model-config.cfcfg'
    if config.is_file() and not target.exists():
        target.parent.mkdir(exist_ok=True)
        # Exclusive create preserves any configuration delivered earlier.
        with config.open('rb') as incoming, target.open('xb') as outgoing:
            shutil.copyfileobj(incoming,outgoing)


def install(source, target, manifest, *, install_prerequisites=True):
    verify_payloads(source, manifest)
    version = manifest['version']
    if not isinstance(version, str) or not __import__('re').fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid version')
    target.mkdir(parents=True, exist_ok=True)
    destination = target / 'versions' / version
    if destination.exists():
        if (destination/'installed-manifest.json').is_file() and json.loads((destination/'installed-manifest.json').read_text('utf-8')) == manifest:
            if any(not (destination/name).is_file() for name in manifest['required']):
                raise ValueError('Existing installation is incomplete; preserved for recovery')
            copy_internal_config(source,destination)
            return destination
        raise ValueError('Existing version is different; preserved without overwrite')
    required = sum(part['unpacked_bytes'] for part in manifest['parts']) + 1024**3
    if shutil.disk_usage(target).free < required:
        raise ValueError('Not enough free disk space')
    stage = target / ('.install-' + uuid.uuid4().hex)
    stage.mkdir()
    # Failed staging is kept for diagnostics. Existing versions are never touched.
    for part in manifest['parts']:
        safe_extract(source/part['name'], stage)
    for name in manifest['required']:
        if not (stage/name).is_file():
            raise ValueError('Required application file missing: ' + name)
    # Fetch from the author's pinned revision; do not publicly redistribute
    # third-party material whose license has not been established.
    skill=stage/'bundled-skills/huashu-douyin-script/SKILL.md'
    skill.parent.mkdir(parents=True,exist_ok=True)
    with urlopen('https://raw.githubusercontent.com/alchaincyf/huashu-skills/49a55ba8a975ebda6bb55ea5ca4388942e3f6f18/huashu-douyin-script/SKILL.md',timeout=45) as response:
        content=response.read(128*1024+1)
    if hashlib.sha256(content).hexdigest()!='b6ca5501cecd2ae189cac70de531daa7afe0d24a06402635e99f16b02ca61068':
        raise ValueError('Pinned analysis method verification failed')
    skill.write_bytes(content)
    copy_internal_config(source,stage)
    env = os.environ.copy()
    temporary = stage/'install-temp'
    temporary.mkdir()
    env.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary))
    if install_prerequisites:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64') as key:
                vc_present = winreg.QueryValueEx(key,'Installed')[0] == 1
        except OSError:
            vc_present = False
        if not vc_present:
            code = install_vc_runtime(stage/'prerequisites/vc-redist-x64.exe',env)
            if code not in (0,1638,3010):
                raise RuntimeError('VC runtime installation failed: '+str(code))
        webview = False
        for hive in (winreg.HKEY_LOCAL_MACHINE,winreg.HKEY_CURRENT_USER):
            for base in (r'SOFTWARE\Microsoft\EdgeUpdate\Clients',r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients'):
                try:
                    with winreg.OpenKey(hive,base) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            with winreg.OpenKey(key,winreg.EnumKey(key,i)) as child:
                                try:
                                    label = winreg.QueryValueEx(child,'name')[0]
                                    pv = winreg.QueryValueEx(child,'pv')[0]
                                    webview |= 'webview2' in label.lower() and pv != '0.0.0.0'
                                except OSError:
                                    pass
                except OSError:
                    pass
        if not webview:
            code = subprocess.run([str(stage/'prerequisites/WebView2-x64.exe'),'/silent','/install'],env=env).returncode
            if code not in (0,3010):
                raise RuntimeError('WebView2 installation failed: '+str(code))
    probe = subprocess.run([str(stage/'runtime/python/python.exe'),'-B','-c',
        'import fastapi,uvicorn,rapidocr,cryptography; from content_factory_api.main import app; print(len(app.routes))'],
        cwd=stage,env=env,capture_output=True,timeout=90)
    if probe.returncode:
        raise RuntimeError('Installed service import check failed')
    (stage/'installed-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    destination.parent.mkdir(exist_ok=True)
    stage.rename(destination)
    return destination


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--target',required=True,type=Path)
    parser.add_argument('--manifest',required=True,type=Path)
    args=parser.parse_args()
    try:
        result=install(args.source.resolve(),args.target.resolve(),json.loads(args.manifest.read_text('utf-8')))
        print(str(result))
        return 0
    except Exception as exc:
        # Errors contain component filenames/categories only, never config values.
        args.target.mkdir(parents=True,exist_ok=True)
        (args.target/'installation-error.txt').write_text(type(exc).__name__+': '+str(exc),encoding='utf-8')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
