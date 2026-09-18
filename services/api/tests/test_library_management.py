import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from content_factory_api.main import app
from content_factory_api import edit_batches
from content_factory_api.auto_edit_store import AutoEditProjectStore, AutoEditConflictError
from test_auto_edit_projects import _settings, _source
from test_edit_batches import actual_video, manifest, client
import zipfile
import io


def test_project_trash_preserves_source_and_recovers(tmp_path):
    store = AutoEditProjectStore(tmp_path / 'projects.sqlite3')
    project = store.create_project(title='保留原片', source=_source(tmp_path), settings=_settings())
    deleted = store.set_deleted(project['project_id'], expected_revision=1, deleted=True)
    assert store.list_projects() == []
    assert Path(project['source']['path']).read_bytes() == b'video-source'
    assert store.list_projects(deleted=True)[0]['revision'] == 2
    with pytest.raises(AutoEditConflictError):
        store.enqueue(project['project_id'], expected_revision=2)
    restored = store.set_deleted(project['project_id'], expected_revision=2, deleted=False)
    assert restored['status'] == 'draft'
    assert len(store.list_projects()) == 1
    assert deleted['deleted_at']
    with pytest.raises(AutoEditConflictError):
        store.set_deleted(project['project_id'], expected_revision=2, deleted=True)
    queued = store.enqueue(project['project_id'], expected_revision=3)
    with pytest.raises(AutoEditConflictError):
        store.set_deleted(project['project_id'], expected_revision=queued['revision'], deleted=True)


def test_library_management_atomic_and_durable(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR', str(tmp_path))
    with edit_batches._db() as db:
        db.execute('INSERT INTO batches VALUES(?,?,?)', ('batch1', 'hash', json.dumps({'id':'batch1','title':'原名','candidates':[{'id':'one','review_status':'pending'}]})))
    client = TestClient(app)
    root = '/s7/library-management'
    assert client.get(root).status_code == 200
    changed = client.patch(root, json={'revision':0,'keys':['batch1'], 'title':'裤子第一批', 'group':'J85'})
    assert changed.status_code == 200, changed.text
    assert client.get(root).json()['entries']['batch1']['group'] == 'J85'
    assert client.patch(root,json={'revision':0,'keys':['batch1'],'deleted':True}).status_code == 409
    assert client.patch(root,json={'revision':1,'keys':['batch1','missing'],'deleted':True}).status_code == 404
    assert client.get(root).json()['revision'] == 1
    deleted = client.patch(root,json={'revision':1,'keys':['batch1/one'],'deleted':True})
    assert deleted.status_code == 200
    assert client.get(root).json()['entries']['batch1/one']['deleted'] is True
    assert client.patch(root,json={'revision':2,'keys':['batch1/one'],'deleted':False}).status_code == 200
    with edit_batches._db() as db:
        assert edit_batches._read(db,'batch1')['title'] == '原名'
    assert client.patch(root,json={'revision':3,'keys':['batch1'],'title':'   '}).status_code == 422


def test_archive_download_contents_and_deleted_filter(client, manifest, tmp_path, monkeypatch):
    batch = client.post('/s7/footage-batches/import', json={'manifest_path':str(manifest)}).json()
    base = '/s7/library-management'
    response = client.get(f"{base}/{batch['id']}/archive?download=true")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        video_name = next(n for n in archive.namelist() if n.endswith('.mp4'))
        assert archive.read(video_name) == (manifest.parent/'candidate.mp4').read_bytes()
        assert json.loads(archive.read('审核状态.json'))[0]['review_status'] == 'pending'
    monkeypatch.setenv('CONTENT_FACTORY_EXPORT_ROOT', str(tmp_path/'downloads'))
    exported = client.post('/local-exports', json={'resource':f"{base}/{batch['id']}/archive"})
    assert exported.status_code == 200, exported.text
    assert zipfile.is_zipfile(exported.json()['path'])
    key = f"{batch['id']}/candidate-01"
    assert client.patch(base, json={'revision':0,'keys':[key],'deleted':True}).status_code == 200
    assert client.get(f"{base}/{batch['id']}/archive").status_code == 422
    assert (manifest.parent/'candidate.mp4').is_file()
    assert client.patch(base, json={'revision':1,'keys':[key],'deleted':False}).status_code == 200
    assert client.patch(base, json={'revision':2,'keys':[batch['id']],'deleted':True}).status_code == 200
    assert client.get(f"{base}/{batch['id']}/archive").status_code == 404
    assert client.patch(base, json={'revision':3,'keys':[batch['id']],'deleted':False}).status_code == 200
    assert client.get(f"{base}/{batch['id']}/archive").status_code == 200
