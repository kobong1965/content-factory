import json
import sqlite3
from pathlib import Path
import pytest
from content_factory_api.skill_bundle import export_bundle, import_bundle
from content_factory_api.s3_skills import ViralSkillStore, refresh_skill_candidates
from content_factory_api.s3_queue import AnalysisTaskQueue
from test_s3_skills import _accepted_report


def populated(tmp_path):
    queue = AnalysisTaskQueue(tmp_path / 'queue.sqlite3')
    store = ViralSkillStore(tmp_path / 'skills.sqlite3')
    _accepted_report(queue, tmp_path / 'report', marker='a', video_id='video_'+'a'*24)
    refresh_skill_candidates(store, queue)
    candidate = store.list_candidates()[0]
    skill = store.approve_candidate(candidate['candidate_id'], expected_candidate_revision=candidate['revision'], expected_skill_revision=None, reviewer='test', reuse_mode='reuse', name='固定直播间证据法', mechanism='先展示效果，再用动作验证。', note=None)
    return store, skill


def test_bundle_preserves_approved_runtime_skill_and_is_idempotent(tmp_path):
    source, skill = populated(tmp_path / 'source')
    package = tmp_path / 'skills.cfskills'
    export_bundle(source.database_path, package)
    target = ViralSkillStore(tmp_path / 'destination/skills.sqlite3')
    assert import_bundle(package, target.database_path)['imported'] == 1
    assert target.get_skill(skill['skill_id']) == skill
    assert import_bundle(package, target.database_path)['imported'] == 0
    assert len(target.list_skills(status='approved', reuse_mode='reuse')) == 1
    target.reconcile_candidates([])
    assert target.get_skill_view(skill['skill_id'])['source_status'] == 'current'


def test_import_never_overwrites_locally_disabled_skill(tmp_path):
    source, skill = populated(tmp_path / 'source')
    package = tmp_path / 'skills.cfskills'; export_bundle(source.database_path, package)
    target = ViralSkillStore(tmp_path / 'destination/skills.sqlite3')
    import_bundle(package, target.database_path)
    with target._connect() as connection:
        changed = dict(skill, status='disabled')
        connection.execute('UPDATE viral_skills SET status=?, payload_json=? WHERE skill_id=?', ('disabled', json.dumps(changed), skill['skill_id']))
    import_bundle(package, target.database_path)
    assert target.get_skill(skill['skill_id'])['status'] == 'disabled'


def test_corrupt_bundle_cannot_partially_import(tmp_path):
    source, _ = populated(tmp_path / 'source')
    package = tmp_path / 'skills.cfskills'; export_bundle(source.database_path, package)
    data = json.loads(package.read_text('utf-8')); data['payload']['skills'][0]['name'] = 'altered'
    package.write_text(json.dumps(data), 'utf-8')
    target = ViralSkillStore(tmp_path / 'destination/skills.sqlite3')
    with pytest.raises(ValueError): import_bundle(package, target.database_path)
    assert target.list_skills() == []
