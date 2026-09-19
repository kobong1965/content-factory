import base64
import json
import hashlib
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from content_factory_api.software_updates import verify_manifest, select_release, download_file
from content_factory_api.software_updates import has_active_tasks, backup_databases


def signed_payload():
    key = Ed25519PrivateKey.generate()
    data = {'version': '0.1.31', 'files': [{'name': 'content-factory-0.1.31-setup.exe', 'bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest(), 'url': 'https://github.com/kobong1965/content-factory/releases/download/v0.1.31/content-factory-0.1.31-setup.exe'}]}
    raw = json.dumps(data).encode()
    return key, raw, base64.b64encode(key.sign(raw))


def test_signed_manifest_rejects_tampering():
    key, raw, sig = signed_payload()
    assert verify_manifest(raw, sig, key.public_key().public_bytes_raw())['version'] == '0.1.31'
    with pytest.raises(ValueError):
        verify_manifest(raw.replace(b'0.1.31', b'0.1.32'), sig, key.public_key().public_bytes_raw())


@pytest.mark.parametrize('name,url', [('../evil.exe', 'https://github.com/kobong1965/content-factory/releases/download/v0.1.31/x'), ('ok.exe', 'https://evil.example/ok.exe')])
def test_even_signed_manifest_must_obey_download_boundary(name, url):
    key, raw, _ = signed_payload()
    value = json.loads(raw); value['files'][0].update(name=name, url=url)
    raw = json.dumps(value).encode()
    with pytest.raises(ValueError):
        verify_manifest(raw, base64.b64encode(key.sign(raw)), key.public_key().public_bytes_raw())


def test_release_selection_does_not_silently_install_prerelease_or_downgrade():
    releases = [{'tag_name':'v0.1.31','prerelease':True,'draft':False}, {'tag_name':'v0.1.30','prerelease':False,'draft':False}]
    assert select_release(releases, '0.1.30', False) is None
    assert select_release(releases, '0.1.30', True)['tag_name'] == 'v0.1.31'
    assert select_release(releases, '0.1.32', True) is None


def test_corrupt_download_does_not_publish_executable(tmp_path):
    import io
    _, raw, _ = signed_payload()
    part = json.loads(raw)['files'][0]
    with pytest.raises(ValueError):
        download_file(part, tmp_path, lambda _: None, opener=lambda *a, **k: io.BytesIO(b'abd'))
    assert not (tmp_path / part['name']).exists()


def test_valid_download_and_reuse_are_content_verified(tmp_path):
    import io
    _, raw, _ = signed_payload()
    part = json.loads(raw)['files'][0]
    file = download_file(part, tmp_path, lambda _: None, opener=lambda *a, **k: io.BytesIO(b'abc'))
    assert file.read_bytes() == b'abc'
    assert download_file(part, tmp_path, lambda _: None, opener=lambda *a, **k: pytest.fail('cached bytes should be reused')) == file


def test_update_blocks_queued_and_running_work_and_keeps_database_backup(tmp_path):
    import sqlite3
    profile = tmp_path / '用户资料'; profile.mkdir()
    db = profile / 'jobs.sqlite3'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE media_tasks (status TEXT, content TEXT)')
        connection.execute('INSERT INTO media_tasks VALUES (?,?)', ('queued', '人工字幕'))
    assert has_active_tasks(profile)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE media_tasks SET status='completed'")
    assert not has_active_tasks(profile)
    backup_databases(profile, tmp_path / 'backup')
    with sqlite3.connect(tmp_path / 'backup/jobs.sqlite3') as connection:
        assert connection.execute('SELECT content FROM media_tasks').fetchone()[0] == '人工字幕'


def test_failed_parent_with_unprocessed_segments_does_not_block_updates_forever(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path/'analysis.sqlite3') as connection:
        connection.execute('CREATE TABLE analysis_tasks (task_id TEXT, status TEXT)')
        connection.execute('CREATE TABLE analysis_segments (task_id TEXT, status TEXT)')
        connection.execute("INSERT INTO analysis_tasks VALUES ('old','failed')")
        connection.execute("INSERT INTO analysis_segments VALUES ('old','pending')")
    assert not has_active_tasks(tmp_path)


def test_launcher_rechecks_signature_and_preserves_old_version_on_installer_failure(tmp_path, monkeypatch):
    from test_installer_delivery import launcher
    from types import SimpleNamespace
    root=tmp_path/'app/versions/0.1.30'; root.mkdir(parents=True)
    (root/'package.json').write_text('{"version":"0.1.30"}', 'utf-8')
    (root/'content-factory-desktop.exe').write_bytes(b'old program')
    profile=tmp_path/'profile'; (profile/'runtime').mkdir(parents=True)
    directory=profile/'cache/updates/0.1.31'; directory.mkdir(parents=True)
    key,raw,signature=signed_payload()
    (root/'resources').mkdir()
    (root/'resources/update-signing.pub').write_bytes(base64.b64encode(key.public_key().public_bytes_raw()))
    (directory/'update-manifest.json').write_bytes(raw)
    (directory/'update-manifest.sig').write_bytes(signature)
    (directory/'content-factory-0.1.31-setup.exe').write_bytes(b'abc')
    (profile/'runtime/update-request.json').write_text('{"version":"0.1.31"}', 'utf-8')
    monkeypatch.setattr(launcher,'show_message',lambda *a,**k: None)
    calls=[]
    monkeypatch.setattr(launcher.subprocess,'run',lambda *a,**k: (calls.append(a),SimpleNamespace(returncode=1))[1])
    assert launcher.apply_pending_update(root,profile)==root
    assert len(calls)==1
    assert (root/'content-factory-desktop.exe').read_bytes()==b'old program'
    # Retry with same-sized tampering: installer must not execute at all.
    (profile/'runtime/update-request.json').write_text('{"version":"0.1.31"}', 'utf-8')
    (directory/'content-factory-0.1.31-setup.exe').write_bytes(b'bad')
    assert launcher.apply_pending_update(root,profile)==root
    assert len(calls)==1
