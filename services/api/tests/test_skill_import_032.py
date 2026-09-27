import io
import zipfile
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from content_factory_api.main import app
from content_factory_api.skill_bundle import export_bundle
from content_factory_api.s3_skills import ViralSkillStore
from test_skill_bundle import populated


def test_uploaded_bundle_is_previewed_then_explicitly_approved(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTENT_FACTORY_ANALYSIS_ROOT', str(tmp_path / 'target'))
    source, skill = populated(tmp_path / 'source')
    package = tmp_path / 'team.cfskills'
    export_bundle(source.database_path, package)
    client = TestClient(app)
    preview = client.post('/s3/skill-packages/preview', files={'package':('team.cfskills', package.read_bytes())})
    assert preview.status_code == 200, preview.text
    target = ViralSkillStore(tmp_path / 'target/skills/viral-skills.sqlite3')
    assert target.list_skills() == []
    value = preview.json()
    assert value['skills'][0]['name'] == skill['name']
    rejected = client.post('/s3/skill-packages/approve', json={'package_id':value['package_id'], 'confirmed':False})
    assert rejected.status_code == 422
    response = client.post('/s3/skill-packages/approve', json={'package_id':value['package_id'], 'confirmed':True})
    assert response.status_code == 200, response.text
    assert response.json()['imported'] == 1
    assert target.get_skill(skill['skill_id'])['status'] == 'approved'
    assert client.post('/s3/skill-packages/approve', json={'package_id':value['package_id'], 'confirmed':True}).json()['imported'] == 0


def test_zip_rejects_unsafe_paths_and_unrecognized_markdown(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTENT_FACTORY_ANALYSIS_ROOT', str(tmp_path / 'target'))
    client = TestClient(app)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('../evil.cfskills', '{}')
    response = client.post('/s3/skill-packages/preview', files={'package':('bad.zip', stream.getvalue())})
    assert response.status_code == 422
    assert client.post('/s3/skill-packages/preview', files={'package':('SKILL.md', b'# text')}).status_code == 422


@pytest.mark.parametrize('payload', [[], None, {'schema':1, 'skills':[], 'candidates':None},
    {'schema':1, 'skills':[], 'candidates':[None]},
    {'schema':1, 'skills':[], 'candidates':[]}])
def test_malformed_or_empty_skill_package_is_rejected_without_import(tmp_path, monkeypatch, payload):
    monkeypatch.setenv('CONTENT_FACTORY_ANALYSIS_ROOT', str(tmp_path / 'target'))
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    raw = json.dumps({'payload':payload, 'sha256':hashlib.sha256(canonical).hexdigest()}).encode()
    client = TestClient(app, raise_server_exceptions=False)
    result = client.post('/s3/skill-packages/preview', files={'package':('bad.cfskills',raw)})
    assert result.status_code == 422, result.text
    assert ViralSkillStore(tmp_path / 'target/skills/viral-skills.sqlite3').list_skills() == []
