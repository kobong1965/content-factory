from __future__ import annotations

import json
import sqlite3
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from content_factory_api import auto_edit_worker

from content_factory_api.auto_edit_store import (
    AutoEditConflictError,
    AutoEditProjectStore,
    validate_edit_plan,
)


def _settings(**changes):
    value = {
        "target_count": 3,
        "duration_min_ms": 20_000,
        "duration_max_ms": 40_000,
        "subtitle_font_size": 68,
        "keyword_color": "#FFD400",
        "keyword_scale": 1.3,
        "top_title_enabled": False,
    }
    value.update(changes)
    return value


def test_production_planner_persists_valid_media_result_on_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real media pipeline before the external model boundary."""
    from content_factory_contracts import validate_or_raise
    from content_factory_media.tools import find_tool

    source = tmp_path / "录播.mp4"
    subprocess.run([
        find_tool("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=96x160:r=10", "-t", "1", "-c:v", "libx264", str(source),
    ], check=True, capture_output=True)
    gateway = SimpleNamespace(has_purpose=lambda purpose: True, for_purpose=lambda purpose: None)
    monkeypatch.setattr(auto_edit_worker, "GatewaySettingsStore", lambda *args: SimpleNamespace(load=lambda: gateway))
    monkeypatch.setattr(auto_edit_worker, "call_gateway", lambda *args, **kwargs: pytest.fail("No-audio input must not call a paid model"))
    identifiers = []
    for suffix in ("a" * 32, "a" * 32, "b" * 32):
        project_id = "auto_edit_" + suffix
        project = {"project_id": project_id, "source": {"source_id": "source_01", "path": str(source), "file_name": source.name}}
        # Silence is deliberate: validation must succeed before the ASR guard rejects it.
        with pytest.raises(ValueError, match="语音识别失败|原素材语音转写未完成"):
            auto_edit_worker.analyze_and_plan_with_gateway(project, root=tmp_path / "work")
        # Cache folders now use a compact project/source identity hash. Find the
        # result by its unchanged deterministic task identity, not folder names.
        import hashlib
        identity = json.dumps([project_id, "source_01"], ensure_ascii=False)
        expected_id = "media_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        results = [json.loads(path.read_text(encoding="utf-8")) for path in (tmp_path / "work" / "analysis").rglob("result.json")]
        matches = [item for item in results if item["task_id"] == expected_id]
        assert len(matches) == 1
        payload = matches[0]
        validate_or_raise("media_result", payload)
        assert Path(payload["artifacts"]["proxy_path"]).is_file()
        identifiers.append(payload["task_id"])
    assert identifiers[0] == identifiers[1]
    assert identifiers[0] != identifiers[2]


def test_original_audio_mode_plans_without_asr_and_keeps_visual_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory_api import speech_captions

    frame = tmp_path / 'frame.jpg'
    frame.write_bytes(b'image-fixture')
    result = tmp_path / 'result.json'
    result.write_text(json.dumps({
        'artifacts': {}, 'asr': {'status': 'model_missing'},
        'shots': [
            {'id': 'shot-1', 'start_ms': 0, 'end_ms': 7000, 'keyframe_path': str(frame)},
            {'id': 'shot-2', 'start_ms': 7000, 'end_ms': 15000, 'keyframe_path': str(frame)},
            {'id': 'shot-3', 'start_ms': 15000, 'end_ms': 24000, 'keyframe_path': str(frame)},
            {'id': 'shot-4', 'start_ms': 24000, 'end_ms': 36000, 'keyframe_path': str(frame)},
        ],
    }), encoding='utf-8')
    gateway = SimpleNamespace(has_purpose=lambda purpose: purpose == 'video_review', for_purpose=lambda purpose: None)
    monkeypatch.setattr(auto_edit_worker, 'GatewaySettingsStore', lambda *args: SimpleNamespace(load=lambda: gateway))
    seen = {}
    monkeypatch.setattr(auto_edit_worker, 'MediaPipeline', lambda **kwargs: (
        seen.setdefault('asr_model_path', kwargs.get('asr_model_path')) or SimpleNamespace(process=lambda *a, **k: result)
    ))
    monkeypatch.setattr(speech_captions, 'recognize', lambda *a, **k: pytest.fail('仅保留原声模式不应调用 ASR'))

    def gateway_call(*args, **kwargs):
        context = json.loads(kwargs['context_json'])
        assert context['sources'][0]['transcript'] == []
        assert context['audio_policy']['preserve_original_audio'] is True
        assert context['audio_policy']['speech_recognition'] == 'not_requested'
        pool = context['sources'][0]['natural_clip_pool']
        return SimpleNamespace(content={
            'selected_skill_id': 'skill_a', 'selection_reason': '依据镜头边界', 'analysis_summary': '保留原声',
            'candidates': [{
                'candidate_id': 'visual-only', 'title': '视觉完整片段', 'source_id': 'source_01', 'skill_id': 'skill_a',
                'clips': [{'start_ms': pool[0]['start_ms'], 'end_ms': pool[0]['end_ms']}],
            }],
        })
    monkeypatch.setattr(auto_edit_worker, 'call_gateway', gateway_call)
    project = {
        'project_id': 'auto_edit_visual_only',
        'source': {'source_id': 'source_01', 'path': str(tmp_path / 'video.mp4'), 'file_name': 'video.mp4', 'duration_ms': 36000},
        'eligible_skill_snapshots': [_skills()[0]],
        'settings': _settings(target_count=1, duration_min_ms=15000, duration_max_ms=30000,
                              duration_policy='bounded_15_30', subtitle_mode='none'),
    }
    result_plan = auto_edit_worker.analyze_and_plan_with_gateway(project, root=tmp_path)[3]
    assert seen['asr_model_path'] is None
    assert result_plan[0]['natural_clip_evidence']['boundary_kind'] == 'visual'
    assert result_plan[0]['subtitle_segments'] == []


def test_original_audio_mode_renders_without_asr_and_keeps_audio_track(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory_api import speech_captions
    from content_factory_media.tools import find_tool

    source = tmp_path / '原声素材.mp4'
    subprocess.run([
        find_tool('ffmpeg'), '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=180x320:r=25',
        '-f', 'lavfi', '-i', 'sine=frequency=440', '-t', '20', '-c:v', 'libx264', '-c:a', 'aac', str(source),
    ], check=True, capture_output=True)
    monkeypatch.setattr(speech_captions, 'recognize', lambda *a, **k: pytest.fail('仅保留原声模式渲染不应调用 ASR'))
    source_info = {
        'source_id': 'source_01', 'file_name': source.name, 'path': str(source.resolve()),
        'sha256': __import__('hashlib').sha256(source.read_bytes()).hexdigest(), 'duration_ms': 20_000,
    }
    store = AutoEditProjectStore(tmp_path / 'projects.sqlite3')
    project = store.create_project(title='仅保留原声', source=source_info, settings=_settings(
        target_count=1, duration_min_ms=15_000, duration_max_ms=30_000,
        duration_policy='bounded_15_30', subtitle_mode='none'))
    store.enqueue(project['project_id'], expected_revision=project['revision'])
    result = auto_edit_worker.process_one(
        store, available_skills=_skills(), worker_id='audio-only', root=tmp_path / 'render',
        planner=lambda p: ('skill_a', '镜头完整', '保留原声', [{
            'candidate_id': 'audio_only', 'title': '原声成片', 'source_id': 'source_01',
            'clips': [{'start_ms': 0, 'end_ms': 20_000}],
            'natural_clip_evidence': {'version': 1, 'boundary_kind': 'visual', 'clips': [{'start_ms': 0, 'end_ms': 20_000}]},
        }]),
    )
    assert result['status'] == 'review', result.get('error')
    output = next((tmp_path / 'render').rglob('audio_only.mp4'))
    probe = subprocess.run([
        find_tool('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json', str(output),
    ], check=True, capture_output=True)
    streams = json.loads(probe.stdout)['streams']
    assert any(stream.get('codec_type') == 'audio' for stream in streams)
    assert output.with_suffix('.srt').read_text(encoding='utf-8-sig') == ''
    captions = json.loads(output.with_suffix('.captions.json').read_text(encoding='utf-8'))
    assert captions['mode'] == 'sentence'
    assert captions['cues'] == []


def _source(tmp_path: Path, name: str = "直播录播.mp4") -> dict:
    path = tmp_path / name
    path.write_bytes(b"video-source")
    return {
        "source_id": "source_01",
        "file_name": name,
        "path": str(path.resolve()),
        "sha256": "9" * 64,
        "duration_ms": 300_000,
    }


def _skills() -> list[dict]:
    return [
        {"skill_id": "skill_a", "revision": 2, "status": "approved", "reuse_mode": "reuse", "name": "强钩子", "mechanism": "开头问题钩子"},
        {"skill_id": "skill_b", "revision": 4, "status": "disabled", "reuse_mode": "reuse", "name": "已停用", "mechanism": "不可选"},
        {"skill_id": "skill_c", "revision": 1, "status": "approved", "reuse_mode": "avoid", "name": "反例", "mechanism": "只用于避坑"},
    ]


def test_multiple_sources_persist_and_plan_uses_own_source_bounds(tmp_path):
    store = AutoEditProjectStore(tmp_path / 'multi.sqlite3')
    first = _source(tmp_path, 'first.mp4')
    second = {**_source(tmp_path, 'second.mp4'), 'source_id': 'source_02', 'duration_ms': 25_000}
    project = store.create_project(title='同一批', sources=[first, second], settings=_settings(target_count=1))
    store.enqueue(project['project_id'], expected_revision=1)
    store.claim_next(available_skills=_skills(), worker_id='multi')
    assert AutoEditProjectStore(store.database_path).get_project(project['project_id'])['sources'] == [first, second]
    def save(source_id, end):
        return store.save_plan(project['project_id'], worker_id='multi', selected_skill_id='skill_a', reason='证据', plan=[{'candidate_id': 'one', 'title': '第二条素材', 'source_id': source_id, 'clips': [{'start_ms': 0, 'end_ms': end}]}])
    with pytest.raises(ValueError, match='范围'):
        save('source_02', 30_000)
    with pytest.raises(ValueError, match='来源'):
        save('unknown', 20_000)
    saved = save('source_02', 20_000)
    assert saved['plan'][0]['source_id'] == 'source_02'
    assert len(saved['plan']) == 1


def test_multi_upload_api_atomic_save_and_legacy_single(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content_factory_api import auto_edit
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(auto_edit, '_probe_video', lambda path: 60_000)
    app = FastAPI()
    app.include_router(auto_edit.router)
    client = TestClient(app)
    fields = {'title': '两条录播', 'settings_json': json.dumps(_settings())}
    response = client.post('/s7/auto-edit-projects', data=fields, files=[('sources', ('同名.mp4', b'first', 'video/mp4')), ('sources', ('同名.mp4', b'second', 'video/mp4'))])
    assert response.status_code == 201, response.text
    project = response.json()
    assert [Path(s['path']).read_bytes() for s in project['sources']] == [b'first', b'second']
    assert client.get('/s7/auto-edit-projects/' + project['project_id']).json()['sources'] == project['sources']
    directories = set((tmp_path / 'auto-edit' / 'sources').iterdir())
    failed = client.post('/s7/auto-edit-projects', data=fields, files=[('sources', ('valid.mp4', b'valid', 'video/mp4')), ('sources', ('empty.mp4', b'', 'video/mp4'))])
    assert failed.status_code == 422
    assert set((tmp_path / 'auto-edit' / 'sources').iterdir()) == directories
    assert client.get('/s7/auto-edit-projects').json()['total'] == 1
    legacy = client.post('/s7/auto-edit-projects', data=fields, files={'source': ('old.mp4', b'old', 'video/mp4')})
    assert legacy.status_code == 201
    assert len(legacy.json()['sources']) == 1


@pytest.mark.parametrize('mode', ['sentence', 'reveal', 'highlight', 'auto'])
def test_multisource_render_uses_selected_file_and_persists_batch(tmp_path, monkeypatch, mode):
    import subprocess
    import shutil
    from content_factory_api import speech_captions, subtitle_editor
    if not shutil.which('ffmpeg'):
        pytest.fail('FFmpeg is required for multi-source rendering acceptance')
    monkeypatch.setenv('CONTENT_FACTORY_S7_DATA_DIR', str(tmp_path / 's7'))
    recognized=[]
    acoustic_words=[
        {'text':'第','start_ms':0,'end_ms':180,'probability':.99},
        {'text':'二','start_ms':200,'end_ms':550,'probability':.99},
        {'text':'条','start_ms':600,'end_ms':850,'probability':.99},
        {'text':'素','start_ms':900,'end_ms':1400,'probability':.99},
        {'text':'材','start_ms':1450,'end_ms':2000,'probability':.99},
    ]
    def recognize(clean,workdir,**kwargs):
        # Only replace expensive model inference; cutting, concatenation,
        # subtitle burning, and output registration below all remain real.
        assert clean.is_file() and clean.name=='blue_result.clean.mp4'
        assert not kwargs.get('word_timestamps_only', False)
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(clean)],capture_output=True,check=True)
        assert 4.9<=float(json.loads(probe.stdout)['format']['duration'])<=5.2
        recognized.append(str(clean))
        return {'engine':'deterministic-acoustic-fixture','words':acoustic_words,'raw_segments':[{'text':'第二条素材','start_ms':0,'end_ms':2000}]}
    monkeypatch.setattr(speech_captions,'recognize',recognize)
    sources = []
    for index, color in enumerate(['red', 'blue']):
        file = tmp_path / f'{color}.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=180x320:r=25','-f','lavfi','-i','sine=frequency=440','-t','6','-c:v','libx264','-c:a','aac',str(file)],check=True,capture_output=True)
        import hashlib
        sources.append({'source_id':f'source_{index}', 'file_name':file.name,'path':str(file.resolve()),'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'duration_ms':6000})
    store = AutoEditProjectStore(tmp_path / 'projects.sqlite3')
    project = store.create_project(title='多来源渲染', sources=sources,settings=_settings(target_count=1,duration_min_ms=5000,duration_max_ms=6000,subtitle_mode=mode))
    store.enqueue(project['project_id'],expected_revision=1)
    result = auto_edit_worker.process_one(store,available_skills=_skills(),worker_id='real',root=tmp_path / 'render',planner=lambda p:('skill_a','选择蓝色源片','测试计划',[{'candidate_id':'blue_result','title':'蓝色成片','source_id':'source_1','clips':[{'start_ms':0,'end_ms':5000}],'subtitle_segments':[{'start_ms':0,'end_ms':2000,'text':'第二条素材'}]}]))
    assert result['status'] == 'review', result.get('error')
    manifest = json.loads(next((tmp_path / 'render').rglob('manifest.json')).read_text(encoding='utf-8'))
    assert manifest['candidates'][0]['source_path'] == sources[1]['path']
    output = next((tmp_path / 'render').rglob('blue_result.mp4'))
    pixel = subprocess.run(['ffmpeg','-v','error','-ss','3','-i',str(output),'-vf','scale=1:1','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
    assert pixel[2] > pixel[0] + 100
    assert len(recognized)==1
    subtitle=output.with_suffix('.srt').read_text(encoding='utf-8-sig')
    assert '第二条素材' in subtitle
    captions=json.loads(output.with_suffix('.captions.json').read_text(encoding='utf-8'))
    assert captions['mode']==('sentence' if mode=='auto' else mode)
    assert captions['cues'][0]['words']==acoustic_words
    inherited=subtitle_editor.get_draft(manifest['id'],'blue_result')
    assert inherited['document']==captions
    assert inherited['revision']==1


def test_project_validation_rejects_invalid_count_and_duration(tmp_path: Path) -> None:
    store = AutoEditProjectStore(tmp_path / "auto-edit.sqlite3")
    with pytest.raises(ValueError, match="成片数量"):
        store.create_project(title="批次", source=_source(tmp_path), settings=_settings(target_count=21))
    with pytest.raises(ValueError, match="时长"):
        store.create_project(title="批次", source=_source(tmp_path), settings=_settings(duration_min_ms=50_000, duration_max_ms=20_000))


def test_multiple_projects_queue_independently_and_revision_prevents_lost_updates(tmp_path: Path) -> None:
    store = AutoEditProjectStore(tmp_path / "auto-edit.sqlite3")
    first = store.create_project(title="上午第一批", source=_source(tmp_path, "a.mp4"), settings=_settings())
    second = store.create_project(title="上午第二批", source=_source(tmp_path, "b.mp4"), settings=_settings(target_count=5))
    queued_first = store.enqueue(first["project_id"], expected_revision=first["revision"])
    queued_second = store.enqueue(second["project_id"], expected_revision=second["revision"])
    assert [item["project_id"] for item in store.list_projects()] == [second["project_id"], first["project_id"]]
    assert queued_first["status"] == queued_second["status"] == "queued"
    with pytest.raises(AutoEditConflictError):
        store.update_project(first["project_id"], expected_revision=first["revision"], settings=_settings(target_count=2))


def test_recycle_bin_restore_then_purge_removes_project_sources(tmp_path: Path) -> None:
    store = AutoEditProjectStore(tmp_path / "auto-edit.sqlite3")
    source = _source(tmp_path, "待删除原片.mp4")
    managed = tmp_path / "sources" / ("a" * 32) / "00.mp4"
    managed.parent.mkdir(parents=True)
    Path(source["path"]).replace(managed)
    source["path"] = str(managed.resolve())
    project = store.create_project(title="可恢复项目", source=source, settings=_settings())
    trashed = store.set_deleted(project["project_id"], expected_revision=project["revision"], deleted=True)
    assert Path(source["path"]).is_file()
    restored = store.set_deleted(project["project_id"], expected_revision=trashed["revision"], deleted=False)
    assert Path(source["path"]).is_file()
    trashed_again = store.set_deleted(project["project_id"], expected_revision=restored["revision"], deleted=True)
    deletion = store.preview_purge(project["project_id"], expected_revision=trashed_again["revision"])
    result = store.purge_project(project["project_id"], expected_revision=trashed_again["revision"],
                                confirmation_token=deletion["confirmation_token"])
    assert result["purged"] is True
    assert not Path(source["path"]).exists()
    assert store.list_projects(deleted=True) == []
    with pytest.raises(Exception):
        store.get_project(project["project_id"])


def test_claim_freezes_only_approved_reusable_skill_and_library_changes_do_not_drift_snapshot(tmp_path: Path) -> None:
    store = AutoEditProjectStore(tmp_path / "auto-edit.sqlite3")
    project = store.create_project(title="自动选方法", source=_source(tmp_path), settings=_settings())
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    claimed = store.claim_next(available_skills=_skills(), worker_id="worker-1")
    assert claimed is not None
    assert claimed["status"] == "analyzing"
    assert [item["skill_id"] for item in claimed["eligible_skill_snapshots"]] == ["skill_a"]
    changed = _skills()
    changed[0]["revision"] = 99
    reopened = store.get_project(project["project_id"])
    assert reopened["eligible_skill_snapshots"][0]["revision"] == 2


def test_claim_without_approved_reusable_skill_fails_explicitly(tmp_path: Path) -> None:
    store = AutoEditProjectStore(tmp_path / "auto-edit.sqlite3")
    project = store.create_project(title="无方法", source=_source(tmp_path), settings=_settings())
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    assert store.claim_next(available_skills=_skills()[1:], worker_id="worker-1") is None
    failed = store.get_project(project["project_id"])
    assert failed["status"] == "failed"
    assert "已审核" in failed["error"]


def test_plan_rejects_invalid_bounds_count_duration_but_allows_source_overlap() -> None:
    base = [
        {"candidate_id": "clip_1", "title": "版本 1", "clips": [{"start_ms": 0, "end_ms": 20_000}]},
        {"candidate_id": "clip_2", "title": "版本 2", "clips": [{"start_ms": 30_000, "end_ms": 55_000}]},
        {"candidate_id": "clip_3", "title": "版本 3", "clips": [{"start_ms": 60_000, "end_ms": 90_000}]},
    ]
    validate_edit_plan(base, source_duration_ms=300_000, settings=_settings())
    with pytest.raises(ValueError, match="数量"):
        validate_edit_plan(base[:2], source_duration_ms=300_000, settings=_settings())
    out_of_range = json.loads(json.dumps(base))
    out_of_range[2]["clips"][0]["end_ms"] = 310_000
    with pytest.raises(ValueError, match="范围"):
        validate_edit_plan(out_of_range, source_duration_ms=300_000, settings=_settings())
    overlapping = json.loads(json.dumps(base))
    overlapping[0]["clips"] = [{"start_ms": 0, "end_ms": 12_000}, {"start_ms": 10_000, "end_ms": 22_000}]
    validated = validate_edit_plan(overlapping, source_duration_ms=300_000, settings=_settings())
    assert validated[0]['clips'] == overlapping[0]['clips']
    assert validated[0]['duration_ms'] == 24000
    too_short = json.loads(json.dumps(base))
    too_short[0]["clips"] = [{"start_ms": 0, "end_ms": 5_000}]
    with pytest.raises(ValueError, match="时长"):
        validate_edit_plan(too_short, source_duration_ms=300_000, settings=_settings())


def test_interrupted_claim_is_recovered_but_completed_checkpoint_is_preserved(tmp_path: Path) -> None:
    database = tmp_path / "auto-edit.sqlite3"
    store = AutoEditProjectStore(database)
    before_commit = store.create_project(title="提交前中断", source=_source(tmp_path, "before.mp4"), settings=_settings())
    after_commit = store.create_project(title="提交后中断", source=_source(tmp_path, "after.mp4"), settings=_settings())
    store.enqueue(before_commit["project_id"], expected_revision=before_commit["revision"])
    store.enqueue(after_commit["project_id"], expected_revision=after_commit["revision"])
    first = store.claim_next(available_skills=_skills(), worker_id="dead-1")
    assert first is not None
    store.save_plan(
        first["project_id"],
        worker_id="dead-1",
        selected_skill_id="skill_a",
        reason="素材包含直接问题式开头",
        plan=[
            {"candidate_id": "clip_1", "title": "版本 1", "clips": [{"start_ms": 0, "end_ms": 20_000}]},
            {"candidate_id": "clip_2", "title": "版本 2", "clips": [{"start_ms": 30_000, "end_ms": 55_000}]},
            {"candidate_id": "clip_3", "title": "版本 3", "clips": [{"start_ms": 60_000, "end_ms": 90_000}]},
        ],
    )
    second = store.claim_next(available_skills=_skills(), worker_id="dead-2")
    assert second is not None
    reopened = AutoEditProjectStore(database)
    recovered = reopened.recover_interrupted()
    assert recovered == [second["project_id"]]
    assert reopened.get_project(first["project_id"])["status"] == "render_pending"
    assert reopened.get_project(first["project_id"])["plan"]
    assert reopened.get_project(second["project_id"])["status"] == "queued"


def test_output_batch_registration_is_idempotent(tmp_path: Path) -> None:
    store = AutoEditProjectStore(tmp_path / "auto-edit.sqlite3")
    project = store.create_project(title="登记批次", source=_source(tmp_path), settings=_settings())
    batch = store.register_output_batch(project["project_id"], batch_id="auto-batch-01")
    repeated = store.register_output_batch(project["project_id"], batch_id="auto-batch-01")
    assert batch == repeated
    assert repeated["output_batch_id"] == "auto-batch-01"
    assert repeated["status"] == "review"
    with sqlite3.connect(store.database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM auto_edit_events WHERE event_type='output_registered'").fetchone()[0]
    assert count == 1


def test_transcript_parser_preserves_ffmpeg_whisper_milliseconds(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.json"
    transcript.write_text(json.dumps({"segments": [{"start": 2944, "end": 5944, "text": "这条裤子看版型"}]}), encoding="utf-8")
    assert auto_edit_worker._transcript_text({"artifacts": {"transcript_path": str(transcript)}}) == [
        {"start_ms": 2944, "end_ms": 5944, "text": "这条裤子看版型"},
    ]


def test_worker_uses_injected_planner_and_freezes_its_selected_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = AutoEditProjectStore(tmp_path / "auto-edit.sqlite3")
    project = store.create_project(title="模型规划", source=_source(tmp_path), settings=_settings())
    store.enqueue(project["project_id"], expected_revision=project["revision"])
    captured = {}

    def planner(claimed):
        captured["skill_revision"] = claimed["eligible_skill_snapshots"][0]["revision"]
        return "skill_a", "ASR 和关键帧与问题钩子匹配", "已依据原素材证据生成方案", [
            {"candidate_id": "clip_1", "title": "版本 1", "clips": [{"start_ms": 0, "end_ms": 20_000}]},
            {"candidate_id": "clip_2", "title": "版本 2", "clips": [{"start_ms": 30_000, "end_ms": 55_000}]},
            {"candidate_id": "clip_3", "title": "版本 3", "clips": [{"start_ms": 60_000, "end_ms": 90_000}]},
        ]

    monkeypatch.setattr(auto_edit_worker, "render_and_register", lambda current_store, current, root: current_store.register_output_batch(current["project_id"], batch_id="planned-batch"))
    result = auto_edit_worker.process_one(store, available_skills=_skills(), worker_id="planner-worker", root=tmp_path, planner=planner)
    assert result is not None and result["status"] == "review"
    assert captured["skill_revision"] == 2
    assert result["selected_skill"]["snapshot"]["skill_id"] == "skill_a"


def test_subtitles_follow_all_source_clips_and_keyword_style(tmp_path: Path) -> None:
    candidate = {
        "clips": [{"start_ms": 1_000, "end_ms": 4_000}, {"start_ms": 8_000, "end_ms": 11_000}],
        "keywords": ["版型"],
    }
    transcript = [
        {"start_ms": 1_500, "end_ms": 3_000, "text": "先看版型"},
        {"start_ms": 8_500, "end_ms": 10_000, "text": "再看裤腿"},
    ]
    segments = auto_edit_worker._subtitle_segments(candidate, transcript)
    assert segments == [
        {"start_ms": 500, "end_ms": 2_000, "text": "先看版型"},
        {"start_ms": 3_500, "end_ms": 5_000, "text": "再看裤腿"},
    ]
    candidate["subtitle_segments"] = segments
    subtitle, ass = tmp_path / "captions.srt", tmp_path / "captions.ass"
    assert auto_edit_worker._write_subtitles(candidate, {"settings": _settings()}, subtitle, ass)
    assert "00:00:03,500 --> 00:00:05,000" in subtitle.read_text(encoding="utf-8-sig")
    style = ass.read_text(encoding="utf-8-sig")
    assert "Style: Default,SimHei,68" in style
    assert "\\c&H0000D4FF&" in style and "版型" in style
