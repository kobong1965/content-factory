from pathlib import Path
import pytest
from content_factory_api.auto_edit_store import AutoEditProjectStore, validate_settings
from content_factory_api.auto_edit_worker import _planner_schema
from test_auto_edit_projects import _settings, _source, _skills


def test_each_candidate_freezes_its_own_skill_and_reopens(tmp_path: Path):
    store = AutoEditProjectStore(tmp_path / 'projects.sqlite3')
    project = store.create_project(title='J85 第一批', sku=' J85 ', source=_source(tmp_path), settings=_settings(target_count=2))
    assert project['sku'] == 'J85'
    skills = [_skills()[0], {**_skills()[0], 'skill_id': 'skill_b', 'name': '细节证据先行', 'revision': 3}]
    store.enqueue(project['project_id'], expected_revision=1)
    claimed = store.claim_next(available_skills=skills, worker_id='test')
    schema = _planner_schema(claimed)['properties']['candidates']['items']
    assert 'skill_id' in schema['required']
    plan = [{'candidate_id':f'c{i}', 'title':f'方法{i}', 'skill_id':s['skill_id'], 'clips':[{'start_ms':0,'end_ms':20000}]} for i,s in enumerate(skills)]
    with pytest.raises(ValueError, match='Skill'):
        store.save_plan(project['project_id'], worker_id='test', selected_skill_id='skill_a', reason='test', plan=[plan[0], {**plan[1], 'skill_id':'unknown'}])
    saved = store.save_plan(project['project_id'], worker_id='test', selected_skill_id='skill_a', reason='test', plan=plan)
    skills[1]['name'] = '后续修改不可改变冻结计划'
    reopened = AutoEditProjectStore(store.database_path).get_project(project['project_id'])
    assert [p['skill_snapshot']['name'] for p in reopened['plan']] == [skills[0]['name'], '细节证据先行']
    assert [p['skill_id'] for p in saved['plan']] == ['skill_a','skill_b']
    assert reopened['sku'] == 'J85'


def test_twenty_outputs_is_supported_but_twenty_one_is_rejected():
    assert validate_settings(_settings(target_count=20))['target_count'] == 20
    with pytest.raises(ValueError):
        validate_settings(_settings(target_count=21))


def test_multiple_skills_render_to_distinct_candidates_with_sku(tmp_path, monkeypatch):
    import subprocess, hashlib, json
    from content_factory_api import auto_edit_worker, speech_captions, edit_batches
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR', str(tmp_path / 's7'))
    monkeypatch.setattr(speech_captions, 'recognize', lambda *args, **kwargs: {'words':[], 'raw_segments':[]})
    source = tmp_path / 'test.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=180x320:r=10','-f','lavfi','-i','sine=frequency=440','-t','6','-c:v','libx264','-c:a','aac',str(source)],check=True,capture_output=True)
    store = AutoEditProjectStore(tmp_path / 's7/auto-edit/projects.sqlite3')
    project = store.create_project(title='多方法真实渲染验收', sku='J85', source={'source_id':'s1','file_name':source.name,'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'duration_ms':6000}, settings=_settings(target_count=2,duration_min_ms=5000,duration_max_ms=6000))
    skills = [_skills()[0], {**_skills()[0], 'skill_id':'skill_b', 'name':'细节证据'}]
    plan = [{'candidate_id':f'v{i}', 'title':f'版本{i}', 'skill_id':s['skill_id'], 'clips':[{'start_ms':0,'end_ms':5000}]} for i,s in enumerate(skills)]
    store.enqueue(project['project_id'],expected_revision=1)
    result = auto_edit_worker.process_one(store, available_skills=skills, worker_id='test',root=tmp_path/'s7/auto-edit',planner=lambda p:('skill_a','对照','测试方案',plan))
    assert result['status'] == 'review', result.get('error')
    batch = edit_batches.list_batches()['batches'][0]
    assert batch['sku'] == 'J85'
    assert [c['benchmark_refs'][0] for c in batch['candidates']] == [s['name'] for s in skills]
    for item in batch['candidates']:
        assert Path(item['resources']['video']['path']).stat().st_size > 1000
        assert 4900 <= item['duration_ms'] <= 5200


def test_purge_keeps_external_and_referenced_sources(tmp_path: Path):
    store = AutoEditProjectStore(tmp_path / 'auto-edit.sqlite3')
    external = _source(tmp_path, '外部原片.mp4')
    project = store.create_project(title='外部文件', source=external, settings=_settings())
    trashed = store.set_deleted(project['project_id'], expected_revision=1, deleted=True)
    preview = store.preview_purge(project['project_id'], expected_revision=trashed['revision'])
    result = store.purge_project(project['project_id'], expected_revision=trashed['revision'], confirmation_token=preview['confirmation_token'])
    assert result['purged'] is True
    assert Path(external['path']).exists()
    assert result['skipped']


def test_purge_keeps_managed_source_referenced_by_other_project(tmp_path: Path):
    db = tmp_path / 'auto-edit.sqlite3'
    store = AutoEditProjectStore(db)
    managed = tmp_path / 'sources' / ('a'*32) / 'shared.mp4'
    managed.parent.mkdir(parents=True)
    managed.write_bytes(b'shared')
    source = {'source_id':'shared', 'file_name':'shared.mp4', 'path':str(managed), 'sha256':'a'*64, 'duration_ms':6000}
    first = store.create_project(title='一项目', source=source, settings=_settings())
    second = store.create_project(title='二项目', source={**source, 'source_id':'shared-2'}, settings=_settings())
    trashed = store.set_deleted(first['project_id'], expected_revision=1, deleted=True)
    preview = store.preview_purge(first['project_id'], expected_revision=trashed['revision'])
    result = store.purge_project(first['project_id'], expected_revision=trashed['revision'], confirmation_token=preview['confirmation_token'])
    assert Path(source['path']).exists()
    assert any('其他项目' in item['reason'] for item in result['skipped'])
    assert store.get_project(second['project_id'])['title'] == '二项目'
