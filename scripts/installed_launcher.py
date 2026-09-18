"""Installed Windows entry point. Uses only runtimes/resources beside the app."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import time
from urllib.request import urlopen


def environment_for(root: Path, profile: Path, port: int) -> dict[str, str]:
    env = {k:v for k,v in os.environ.items() if not k.startswith(('CONTENT_FACTORY_', 'PYTHON', 'TCL_', 'TK_'))}
    root, profile = root.resolve(), profile.resolve()
    paths = {'RUNTIME_ROOT':'runtime','MEDIA_ROOT':'media','ANALYSIS_ROOT':'analysis',
             'S3_CONFIG_PATH':'gateway-config.json', **{f'S{i}_DATA_DIR':f's{i}' for i in range(4,9)}}
    env.update({f'CONTENT_FACTORY_{key}':str(profile / value) for key,value in paths.items()})
    env.update(CONTENT_FACTORY_USER_ROOT=str(profile), CONTENT_FACTORY_EXPORT_ROOT=str(profile / 'exports'),
               CONTENT_FACTORY_API_PORT=str(port), CONTENT_FACTORY_WHISPER_MODEL=str(root / 'models/whisper/ggml-tiny.bin'),
               CONTENT_FACTORY_SPEECH_MODEL=str(root / 'models/large-v3-turbo'),
               CONTENT_FACTORY_SPEECH_PYTHON=str(root / 'runtime/asr-python/python.exe'),
               CONTENT_FACTORY_SPEECH_RUNTIME=str(root / 'runtime/asr-python/Lib/site-packages'),
               CONTENT_FACTORY_HUASHU_SKILL_DIR=str(profile / 'skills/huashu-douyin-script'),
               HF_HUB_OFFLINE='1', HF_HOME=str(profile / 'cache/hf'),
               TEMP=str(profile / 'cache/temp'), TMP=str(profile / 'cache/temp'), TMPDIR=str(profile / 'cache/temp'),
               PATH=str(root / 'tools') + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32'))
    return env


def free_port() -> int:
    with socket.socket() as candidate:
        try:
            candidate.bind(('127.0.0.1',8766))
        except OSError:
            candidate.bind(('127.0.0.1',0))
        return candidate.getsockname()[1]


def expected_identity(root: Path, env: dict[str,str]) -> dict[str,str]:
    names = ['RUNTIME_ROOT','MEDIA_ROOT','ANALYSIS_ROOT','S3_CONFIG_PATH'] + [f'S{i}_DATA_DIR' for i in range(4,9)]
    normalize = lambda p: os.path.normcase(str(Path(p).resolve())).rstrip('\\/')
    return {'instance_id':hashlib.sha256(normalize(root).encode()).hexdigest()[:16],
            'data_profile_id':hashlib.sha256('\n'.join(normalize(env['CONTENT_FACTORY_'+n]) for n in names).encode()).hexdigest()[:16],
            'build_id':'content-factory-'+json.loads((root/'package.json').read_text(encoding='utf-8'))['version']}


def start_service(root, profile, port):
    env = environment_for(root, profile, port)
    Path(env['TEMP']).mkdir(parents=True, exist_ok=True)
    log = (profile/'runtime/service.log').open('ab')
    try:
        process = subprocess.Popen([str(root/'runtime/python/python.exe'),'-B','-m','uvicorn',
            'content_factory_api.main:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],
            env=env, cwd=root, stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        log.close()
    try:
        expected = expected_identity(root,env)
        for _ in range(160):
            if process.poll() is not None:
                raise RuntimeError('本地服务未能启动。可重试，或导出诊断信息。')
            try:
                with urlopen(f'http://127.0.0.1:{port}/health',timeout=.5) as response:
                    health = json.load(response)
                if health.get('status') == 'ok' and all(health.get(k)==v for k,v in expected.items()):
                    return process,env
            except (OSError,ValueError):
                pass
            time.sleep(.25)
        raise RuntimeError('本地服务启动超时，请重试。')
    except BaseException:
        stop_service(process)
        raise


def stop_service(process):
    if process.poll() is None:
        # Only terminate the process tree owned by this launcher.
        subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),
            '/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,timeout=15)
        process.wait(timeout=15)


def show_message(text, error=False):
    ctypes.windll.user32.MessageBoxW(None,text,'爆款内容工厂',0x10 if error else 0x40)


def import_internal_config(root, profile):
    destination=profile/'gateway-config.json'
    package=root/'internal/model-config.cfcfg'
    if destination.exists() or not package.is_file():
        return
    import tkinter as tk
    from tkinter import simpledialog, messagebox
    from content_factory_api.internal_config_package import import_config
    window=tk.Tk();window.withdraw()
    try:
        while True:
            password=simpledialog.askstring('导入团队模型配置','请输入单独收到的交付口令，无需填写 API Key。',show='*',parent=window)
            if password is None:
                return
            try:
                import_config(package.read_bytes(),password,destination)
                return
            except ValueError:
                messagebox.showerror('导入未完成','口令不正确或配置包损坏，请核对后重试。',parent=window)
    finally:
        window.destroy()


def run(root, profile, *, service_only=False):
    import msvcrt
    profile.mkdir(parents=True,exist_ok=True)
    (profile/'runtime').mkdir(exist_ok=True)
    with (profile/'runtime/desktop.lock').open('a+b') as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b'1');lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            if not service_only: show_message('软件已经运行，请切换到已打开的窗口。')
            return 0
        import_internal_config(root,profile)
        source_skill=root/'bundled-skills/huashu-douyin-script/SKILL.md'
        target_skill=profile/'skills/huashu-douyin-script/SKILL.md'
        if not target_skill.exists() and source_skill.is_file():
            target_skill.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source_skill,target_skill)
        port=free_port()
        for attempt in range(3):
            try:
                server,env=start_service(root,profile,port)
                break
            except RuntimeError:
                if attempt == 2:
                    raise
                port=free_port()
        desktop=None
        try:
            if service_only:
                print(json.dumps({'port':port,**expected_identity(root,env)}),flush=True)
                return 0
            desktop=subprocess.Popen([str(root/'content-factory-desktop.exe')],cwd=root,env=env,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            restarts=0
            while desktop.poll() is None:
                if server.poll() is not None:
                    restarts+=1
                    if restarts>3:
                        raise RuntimeError('本地服务连续退出，任务会在下次启动时恢复。请导出诊断信息。')
                    server,_=start_service(root,profile,port)
                time.sleep(.5)
            return desktop.returncode
        finally:
            stop_service(server)
            if desktop is not None and desktop.poll() is None:
                desktop.terminate()
                desktop.wait(timeout=10)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-root',type=Path)
    parser.add_argument('--service-only',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    profile=(args.data_root or Path(os.environ['LOCALAPPDATA'])/'ContentFactory').resolve()
    try:
        return run(root,profile,service_only=args.service_only)
    except Exception as exc:
        diagnostic=profile/'runtime/startup-diagnostic.json'
        diagnostic.parent.mkdir(parents=True,exist_ok=True)
        diagnostic.write_text(json.dumps({'category':type(exc).__name__,
            'runtime_present':(root/'runtime/python/python.exe').is_file(),
            'desktop_present':(root/'content-factory-desktop.exe').is_file()},indent=2),encoding='utf-8')
        if not args.service_only:
            show_message('软件启动未完成。请重试。\n诊断信息：'+str(diagnostic),error=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
