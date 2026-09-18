from pathlib import Path
import os

import pytest

from content_factory_media.pipeline import MediaPipeline
from content_factory_media.tools import whisper_relative_model


@pytest.mark.skipif(os.environ.get('CONTENT_FACTORY_S2_INTEGRATION') != '1', reason='Requires real FFmpeg and bundled test model')
def test_unicode_install_and_profile_do_not_need_ascii_user_cache(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    profile = tmp_path / '中文 用户资料'
    monkeypatch.setenv('LOCALAPPDATA', str(profile))
    model = root / '.models/whisper/ggml-tiny.bin'
    result = MediaPipeline(asr_model_path=model).process(
        root / 'output/s2/fixture/s2-fixture.mp4', tmp_path / '中文 工作区',
        'media_dddddddddddddddddddddddddddddddd', fixture_data=True)
    assert result.is_file()
    import json
    payload = json.loads(result.read_text(encoding='utf-8'))
    assert payload['asr']['status'] == 'completed'
    assert not profile.exists()
    assert not list((tmp_path / '中文 工作区').rglob('whisper-model-*.bin'))


@pytest.mark.parametrize('cross_volume', [False, True])
def test_relative_reference_cleans_up_after_worker_failure(tmp_path, monkeypatch, cross_volume):
    source = tmp_path / '模型.bin'
    source.write_bytes(b'unchanged-model')
    working = tmp_path / '中文 工作区'
    working.mkdir()
    if cross_volume:
        def cannot_link(*args):
            raise OSError('simulated different volume')
        monkeypatch.setattr(os, 'link', cannot_link)
    with pytest.raises(RuntimeError, match='worker failed'):
        with whisper_relative_model(source, working) as name:
            name.encode('ascii')
            assert (working / name).read_bytes() == source.read_bytes()
            raise RuntimeError('worker failed')
    assert source.read_bytes() == b'unchanged-model'
    assert not list(working.iterdir())
