"""Exercise isolated runtime imports with no developer Python/PYTHONPATH.

This is a host smoke test, not a clean-Windows deployment acceptance test.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import time
from urllib.request import urlopen
from urllib.error import URLError


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--profile', required=True, type=Path)
    args = parser.parse_args()
    payload, profile = args.payload.resolve(), args.profile.resolve()
    if not profile.is_relative_to(Path('E:/Codex工作盘').resolve()):
        parser.error('Probe profile must stay on the authorized work drive')
    profile.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith(('PYTHON', 'CONTENT_FACTORY_')):
            del environment[key]
    environment.update(TEMP=str(profile), TMP=str(profile), TMPDIR=str(profile), PYTHONDONTWRITEBYTECODE='1',
                       CONTENT_FACTORY_USER_ROOT=str(profile),
                       CONTENT_FACTORY_S3_CONFIG_PATH=str(profile / 'gateway-config.json'))
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32')
    for stage in ('runtime', 'media', 'analysis', 's4', 's5', 's6', 's7', 's8'):
        name = 'CONTENT_FACTORY_' + stage.upper() + ('_ROOT' if stage in ('runtime', 'media', 'analysis') else '_DATA_DIR')
        environment[name] = str(profile / stage)
    code = '''
import json, sys, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
from pathlib import Path
import numpy, cv2, onnxruntime, rapidocr, fastapi, uvicorn
from content_factory_api.main import app
print(json.dumps({'python':sys.version.split()[0], 'executable':sys.executable,
 'import_paths':sys.path, 'api_routes':len(app.routes), 'sqlite':sqlite3.sqlite_version,
 'ocr_package':rapidocr.__file__, 'onnx':onnxruntime.__version__}))
'''
    result = subprocess.run([str(payload / 'runtime/python/python.exe'), '-B', '-c', code],
                            cwd=payload, env=environment, capture_output=True, timeout=90)
    (profile / 'stdout.txt').write_bytes(result.stdout)
    (profile / 'stderr.txt').write_bytes(result.stderr)
    if result.returncode:
        print(result.stderr.decode('utf-8', errors='replace'))
        raise SystemExit(result.returncode)
    report = json.loads(result.stdout)
    for entry in report['import_paths']:
        if entry and not Path(entry).resolve().is_relative_to(payload):
            raise RuntimeError('Runtime imported a path outside its payload')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    with (profile / 'api.log').open('wb') as log:
        process = subprocess.Popen([str(payload / 'runtime/python/python.exe'), '-B', '-m',
                                    'uvicorn', 'content_factory_api.main:app', '--host', '127.0.0.1',
                                    '--port', str(port), '--log-level', 'warning'],
                                   cwd=payload, env=environment, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Packaged service exited; inspect private api.log')
                try:
                    with urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                        health = json.load(response)
                    assert health['status'] == 'ok' and health['service'] == 'api'
                    break
                except (OSError, URLError):
                    time.sleep(.25)
            else:
                raise RuntimeError('Packaged service did not become healthy')
            with urlopen(f'http://127.0.0.1:{port}/s7/library-management', timeout=10) as response:
                library = json.load(response)
            assert library == {'revision': 0, 'entries': {}}
            assert (profile / 's7/library-management.sqlite3').is_file()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
    print(json.dumps({'status':'imports_passed', 'api_routes':report['api_routes'],
                      'python':report['python'], 'http_health':True,
                      'library_persistence':True, 'clean_windows':False}))


if __name__ == '__main__':
    main()
