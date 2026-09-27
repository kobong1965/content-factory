"""Local generation gates must precede paid planning and never weaken recovery."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory_api import auto_edit, auto_edit_worker as worker
from content_factory_api.auto_edit_store import AutoEditProjectStore
from content_factory_api.main import app
from test_auto_edit_projects import _settings, _skills, _source


def project_fixture(tmp_path, **settings):
    store = AutoEditProjectStore(tmp_path / 'projects.sqlite3')
    project = store.create_project(title='条件检查', source={**_source(tmp_path), 'duration_ms': 5_000},
                                   settings=_settings(target_count=5, **settings))
    return store, project


def test_impossible_duration_can_save_but_cannot_queue_and_does_not_change_revision(tmp_path):
    store, project = project_fixture(tmp_path, duration_min_ms=180000, duration_max_ms=180000)
    with pytest.raises(ValueError, match='最多 30 段.*最长原片.*5.000.*最短成片.*180.000'):
        store.enqueue(project['project_id'], expected_revision=1)
    assert store.get_project(project['project_id']) == project
    assert AutoEditProjectStore(store.database_path).get_project(project['project_id']) == project


def test_short_source_can_be_reused_within_each_output(tmp_path):
    store, project = project_fixture(tmp_path)
    assert store.enqueue(project['project_id'], expected_revision=1)['status'] == 'queued'


def test_capacity_uses_one_source_and_existing_piece_limit_not_source_duration_sum(tmp_path):
    store, project = project_fixture(tmp_path, duration_min_ms=180000, duration_max_ms=180000)
    second = {**_source(tmp_path, 'second.mp4'), 'source_id': 'source_02', 'duration_ms': 5500}
    short = store.create_project(title='不能跨片凑时长', sources=[project['source'], second], settings=project['settings'])
    with pytest.raises(ValueError, match='最长原片'):
        store.enqueue(short['project_id'], expected_revision=1)
    boundary = store.create_project(title='正好30段', source={**second, 'duration_ms': 6000}, settings=project['settings'])
    assert store.enqueue(boundary['project_id'], expected_revision=boundary['revision'])['status'] == 'queued'


def test_missing_source_rejected_before_queue(tmp_path):
    store, project = project_fixture(tmp_path, duration_min_ms=5_000)
    Path(project['source']['path']).unlink()
    with pytest.raises(ValueError, match='第 1 条素材.*无法读取'):
        store.enqueue(project['project_id'], expected_revision=1)
    assert store.get_project(project['project_id']) == project


def test_source_disappears_after_enqueue_worker_does_not_call_planner(tmp_path, monkeypatch):
    store, project = project_fixture(tmp_path, duration_min_ms=5_000)
    store.enqueue(project['project_id'], expected_revision=1)
    Path(project['source']['path']).unlink()
    monkeypatch.setattr(worker, 'analyze_and_plan_with_gateway', lambda *a, **k: pytest.fail('must not call model'))
    result = worker.process_one(store, available_skills=_skills(), worker_id='gate', root=tmp_path / 'work')
    assert result['status'] == 'failed'
    assert '无法读取' in result['error']
    assert not result['output_batch_id']


def test_registration_only_retry_bypasses_new_planning_conditions(tmp_path):
    store, project = project_fixture(tmp_path, duration_min_ms=180000, duration_max_ms=180000)
    checkpoint = {'manifest_path': 'preserved/manifest.json', 'manifest_sha256': 'a' * 64}
    with store._connect() as connection:
        project.update(status='failed', registration_checkpoint=checkpoint)
        store._write(connection, project)
    resumed = store.enqueue(project['project_id'], expected_revision=1)
    assert resumed['status'] == 'render_pending'
    assert resumed['registration_checkpoint'] == checkpoint


def test_api_reports_readiness_and_rejects_bypass_without_background_work(tmp_path, monkeypatch):
    store, project = project_fixture(tmp_path, duration_min_ms=180000, duration_max_ms=180000)
    monkeypatch.setattr(auto_edit, 'get_auto_edit_store', lambda: store)
    monkeypatch.setattr(auto_edit, '_run_queue', lambda: pytest.fail('blocked start must not run queue'))
    client = TestClient(app)
    url = '/s7/auto-edit-projects/' + project['project_id']
    check = client.get(url).json()['generation_check']
    assert check['ready'] is False and check['blockers']
    assert client.get('/s7/auto-edit-projects').json()['projects'][0]['generation_check'] == check
    for action in ('start', 'retry'):
        response = client.post(url + '/' + action, json={'expected_revision': 1})
        assert response.status_code == 422
    assert store.get_project(project['project_id']) == project


def test_failed_project_can_update_conditions_and_returns_fresh_generation_check(tmp_path, monkeypatch):
    store, project = project_fixture(tmp_path, duration_min_ms=20_000, duration_max_ms=40_000)
    failed = store.fail(project['project_id'], message='旧方案时长不符合条件')
    monkeypatch.setattr(auto_edit, 'get_auto_edit_store', lambda: store)
    client = TestClient(app)
    response = client.patch(
        '/s7/auto-edit-projects/' + failed['project_id'],
        json={
            'expected_revision': failed['revision'],
            'title': '调整后的条件',
            'settings': {**failed['settings'], 'duration_min_ms': 15_000},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body['title'] == '调整后的条件'
    assert body['settings']['duration_min_ms'] == 15_000
    assert body['revision'] == failed['revision'] + 1
    assert body['status'] == 'failed'
    assert body['plan'] == []
    assert body['generation_check']['ready'] is True
    assert store.get_project(failed['project_id'])['settings']['duration_min_ms'] == 15_000


@pytest.mark.parametrize('actual, difference', [(30_036, '超过上限 0.036'), (9_999, '低于下限 0.001')])
def test_fifth_plan_rejected_before_render_with_exact_difference(tmp_path, monkeypatch, actual, difference):
    store = AutoEditProjectStore(tmp_path / 'plans.sqlite3')
    project = store.create_project(title='111', source=_source(tmp_path),
                                   settings=_settings(target_count=5, duration_min_ms=10_000, duration_max_ms=30_000))
    store.enqueue(project['project_id'], expected_revision=1)
    plans = [{'candidate_id': f'clip_{index}', 'title': '私密名称不得泄漏',
              'clips': [{'start_ms': 0, 'end_ms': actual if index == 5 else 20_000}]} for index in range(1, 6)]
    monkeypatch.setattr(worker, 'render_and_register', lambda *a, **k: pytest.fail('invalid plan must not render'))
    result = worker.process_one(store, available_skills=_skills(), worker_id='plan', root=tmp_path / 'work',
                               planner=lambda p: ('skill_a', '理由', '摘要', plans))
    assert result['status'] == 'failed'
    assert '第 5 条' in result['error'] and difference in result['error']
    assert '私密名称' not in result['error']
    assert not result['plan'] and not result['output_batch_id']
    assert len(list((tmp_path / 'work' / 'plan-snapshots').glob('*.json'))) == 1
