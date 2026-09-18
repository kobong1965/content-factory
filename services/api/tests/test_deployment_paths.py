from pathlib import Path

import pytest

from content_factory_api.deployment_paths import export_root, cache_root


def test_packaged_paths_use_selected_user_directory(monkeypatch, tmp_path):
    selected = tmp_path / '同事 工作资料'
    monkeypatch.setenv('CONTENT_FACTORY_USER_ROOT', str(selected))
    monkeypatch.delenv('CONTENT_FACTORY_EXPORT_ROOT', raising=False)
    assert export_root() == selected.resolve() / 'exports'
    assert cache_root('library-archives') == selected.resolve() / 'cache' / 'library-archives'
    assert not selected.exists()


def test_explicit_download_location_is_preserved(monkeypatch, tmp_path):
    target = tmp_path / '指定 下载'
    monkeypatch.setenv('CONTENT_FACTORY_EXPORT_ROOT', str(target))
    assert export_root() == target.resolve()


def test_development_default_preserves_existing_downloads(monkeypatch):
    monkeypatch.delenv('CONTENT_FACTORY_USER_ROOT', raising=False)
    monkeypatch.delenv('CONTENT_FACTORY_EXPORT_ROOT', raising=False)
    assert export_root() == Path('E:/Codex工作盘/artifacts/latest/男装编剪器下载').resolve()


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'a/b', r'a\b', '..'])
def test_cache_categories_cannot_escape_root(name):
    with pytest.raises(ValueError):
        cache_root(name)


def test_packaged_speech_uses_its_own_interpreter(monkeypatch, tmp_path):
    import json
    import subprocess
    from types import SimpleNamespace
    from content_factory_api.speech_captions import recognize

    model = tmp_path / 'model'
    model.mkdir()
    (model / 'model.bin').write_bytes(b'test-model')
    interpreter = tmp_path / 'asr-python' / 'python.exe'
    monkeypatch.setenv('CONTENT_FACTORY_SPEECH_MODEL', str(model))
    monkeypatch.setenv('CONTENT_FACTORY_SPEECH_PYTHON', str(interpreter))
    monkeypatch.setenv('CONTENT_FACTORY_USER_ROOT', str(tmp_path / '用户 缓存'))

    def run(command, **kwargs):
        assert command[0] == str(interpreter)
        assert Path(kwargs['env']['TEMP']).is_relative_to(tmp_path)
        assert kwargs['env']['TMPDIR'] == kwargs['env']['TEMP']
        assert kwargs['env']['HF_HUB_OFFLINE'] == '1'
        job = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        Path(job['output']).write_text('{"words":[]}', encoding='utf-8')
        return SimpleNamespace(returncode=0, stderr=b'')

    monkeypatch.setattr(subprocess, 'run', run)
    assert recognize(tmp_path / 'audio.wav', tmp_path / 'job')['words'] == []
