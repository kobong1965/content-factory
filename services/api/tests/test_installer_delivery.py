"""Installer failures must not replace existing software or user files."""
import hashlib
import importlib.util
from pathlib import Path
import socket
import zipfile
import pytest

SCRIPTS=Path(__file__).resolve().parents[3]/'scripts'

def load(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

installer=load('install_payload')
launcher=load('installed_launcher')

@pytest.mark.parametrize('member',['../escape.txt','/absolute.txt','C:/absolute.txt','dir/../../escape.txt','..\\escape.txt'])
def test_extraction_rejects_traversal_before_writing(tmp_path,member):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as output:
        output.writestr('legitimate.txt','not written either')
        output.writestr(member,'unsafe')
    target=tmp_path/'installation'
    target.mkdir()
    with pytest.raises(ValueError):installer.safe_extract(archive,target)
    assert list(target.iterdir())==[]

def test_missing_component_preserves_previous_install_and_data(tmp_path):
    target=tmp_path/'app'
    target.mkdir()
    old=target/'old.exe';old.write_bytes(b'old-version')
    user=target/'user.sqlite3';user.write_bytes(b'user-data')
    manifest={'version':'0.1.30','parts':[{'name':'missing.zip','bytes':5,'sha256':'0'*64,'unpacked_bytes':5}]}
    with pytest.raises(ValueError):installer.install(tmp_path,target,manifest,install_prerequisites=False)
    assert old.read_bytes()==b'old-version'
    assert user.read_bytes()==b'user-data'
    assert not (target/'versions').exists()

def test_same_size_corruption_is_rejected(tmp_path):
    file=tmp_path/'part.zip';file.write_bytes(b'bad')
    with pytest.raises(ValueError):installer.verify_payloads(tmp_path,{'parts':[{'name':'part.zip','bytes':3,'sha256':hashlib.sha256(b'yes').hexdigest()}]})

def test_reinstall_adds_late_internal_package_without_replacing_existing(tmp_path):
    import json
    target=tmp_path/'app';version=target/'versions/0.1.30';version.mkdir(parents=True)
    manifest={'version':'0.1.30','parts':[],'required':[]}
    (version/'installed-manifest.json').write_text(json.dumps(manifest),'utf-8')
    (tmp_path/'model-config.cfcfg').write_bytes(b'encrypted-package')
    assert installer.install(tmp_path,target,manifest,install_prerequisites=False)==version
    assert (version/'internal/model-config.cfcfg').read_bytes()==b'encrypted-package'
    (tmp_path/'model-config.cfcfg').write_bytes(b'new-package')
    installer.install(tmp_path,target,manifest,install_prerequisites=False)
    assert (version/'internal/model-config.cfcfg').read_bytes()==b'encrypted-package'

def test_reinstall_does_not_claim_success_if_existing_binary_is_missing(tmp_path):
    import json
    target=tmp_path/'app';version=target/'versions/0.1.30';version.mkdir(parents=True)
    manifest={'version':'0.1.30','parts':[],'required':['content-factory-desktop.exe']}
    (version/'installed-manifest.json').write_text(json.dumps(manifest),'utf-8')
    with pytest.raises(ValueError):installer.install(tmp_path,target,manifest,install_prerequisites=False)

def test_installer_initializes_empty_readiness_manifest_without_overwriting(tmp_path):
    import json
    installer.ensure_readiness_manifest(tmp_path)
    file=tmp_path/'data/gold-set/manifest.json'
    value=json.loads(file.read_text('utf-8'))
    assert len(value['video_slots'])==20 and len(value['product_slots'])==3
    assert all(slot['case_file'] is None and slot['status']=='pending' for slot in value['video_slots'])
    file.write_text('preserve-existing-resource','utf-8')
    installer.ensure_readiness_manifest(tmp_path)
    assert file.read_text('utf-8')=='preserve-existing-resource'

def test_occupied_default_port_is_not_reused():
    with socket.socket() as blocker:
        try:blocker.bind(('127.0.0.1',8766))
        except OSError:pass  # An existing owner already exercises the same case.
        assert launcher.free_port()!=8766

def test_packaged_environment_removes_developer_paths(tmp_path,monkeypatch):
    monkeypatch.setenv('PYTHONPATH','C:/developer-only')
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR','C:/old-project')
    monkeypatch.setenv('TCL_LIBRARY','C:/old-tcl')
    root=tmp_path/'program';profile=tmp_path/'同事 资料'
    env=launcher.environment_for(root,profile,9999)
    assert 'PYTHONPATH' not in env and 'TCL_LIBRARY' not in env
    assert Path(env['CONTENT_FACTORY_S7_DATA_DIR'])==profile/'s7'
    assert Path(env['CONTENT_FACTORY_SPEECH_PYTHON'])==root/'runtime/asr-python/python.exe'
    assert Path(env['TMPDIR'])==profile/'cache/temp'
