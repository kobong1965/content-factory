"""Caption appearance and safe local rerendering, without encoding production files."""

import importlib.util
import json
from pathlib import Path
import re

import pytest

from content_factory_api.subtitle_design import caption_text


YELLOW = r'{\c&H00FFFF&}'
RESET = r'{\rDefault}'


def _visible(value: str) -> str:
    return re.sub(r'\{[^}]*\}', '', value).replace(r'\N', '')


@pytest.mark.parametrize('word', ['弹力', '直筒', '九分', '长裤', '裤型', '腰头', '身高体重'])
def test_actual_chinese_words_highlight_yellow_then_reset(word: str) -> None:
    value = f'看{word}这里'
    output = caption_text(value)
    assert YELLOW + word + RESET in output
    assert output.endswith(RESET + '这里')
    assert _visible(output) == value


def test_unspoken_keywords_are_not_added() -> None:
    assert caption_text('看一下这个颜色') == '看一下这个颜色'


def test_user_ass_commands_cannot_reposition_or_restyle_captions() -> None:
    output = caption_text(r'{\pos(0,0)\c&HFF0000&}弹力{\rTitle}')
    controls = re.findall(r'\{[^}]*\}', output)
    assert all(control in [YELLOW, RESET] for control in controls)
    assert r'\pos' not in output and r'\rTitle' not in output
    assert '弹力' in _visible(output)


def test_long_caption_wraps_without_losing_spoken_characters() -> None:
    value = '这是一句需要换行但每个字都必须保留的直播间原话字幕'
    output = caption_text(value)
    assert r'\N' in output
    assert _visible(output) == value
    assert all(len(_visible(line)) <= 12 for line in output.split(r'\N'))


def test_keyword_crossing_wrap_boundary_still_has_yellow_highlight() -> None:
    value = '看' * 11 + '弹力再看这里'
    output = caption_text(value)
    assert _visible(output) == value
    # Wrapping must not turn a spoken keyword into unhighlighted fragments.
    yellow_text = ''.join(re.findall(re.escape(YELLOW) + r'(.*?)' + re.escape(RESET), output))
    assert '弹力' in yellow_text.replace(r'\N', '')


@pytest.fixture
def renderer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    script = Path(__file__).resolve().parents[3] / 'scripts' / 'render-live-edit-batch.py'
    spec = importlib.util.spec_from_file_location('caption_test_renderer', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path / 'output')
    monkeypatch.setattr(module, 'TEMP', tmp_path / 'work')
    monkeypatch.setattr(module, 'EVIDENCE', tmp_path / 'evidence')
    font = tmp_path / 'font.ttf'
    font.write_bytes(b'isolated-font-placeholder')
    monkeypatch.setattr(module, 'FONT', font)
    return module


def test_generated_ass_contains_only_spoken_bottom_captions(renderer, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = renderer.EVIDENCE / '039' / 'transcript.json'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'segments': [{'start': 0, 'end': 1,
        'text': '看弹力再看这里', 'words': []}]}), encoding='utf-8')
    calls = []

    class StopBeforeEncoding(Exception):
        pass

    def capture(args, **kwargs):
        calls.append(args)
        raise StopBeforeEncoding

    monkeypatch.setattr(renderer.subprocess, 'run', capture)
    decision = ('01', '不能烧入顶部的标题', '039', [(0, 1)], [], '', [])
    with pytest.raises(StopBeforeEncoding):
        renderer.render_one(decision)
    ass = (renderer.ROOT / 'videos/01/subtitles.ass').read_text(encoding='utf-8-sig')
    events = [line.split(',', 9) for line in ass.splitlines() if line.startswith('Dialogue:')]
    assert len(events) == 1
    assert all(event[3] == 'Default' for event in events)
    assert _visible(events[0][9]) == '看弹力再看这里'
    assert '不能烧入顶部的标题' not in ass and 'Style: Title,' not in ass and 'Style: CTA,' not in ass
    style = next(line for line in ass.splitlines() if line.startswith('Style: Default,')).split(',')
    assert style[3] == '&H00FFFFFF', '高亮后的 Default 重置必须回到白色'
    assert style[18] == '2', '真实口播字幕应置于底部居中'
    args = calls[0]
    inputs = [Path(args[index + 1]).resolve() for index, arg in enumerate(args) if arg == '-i']
    output = Path(args[-1]).resolve()
    assert inputs and all(output != source and output.parent != source.parent for source in inputs)
    assert renderer.EVIDENCE.resolve() != renderer.ROOT.resolve()


@pytest.mark.parametrize('existing_name', ['batch.json', 'videos/01/review.mp4'])
def test_existing_complete_or_partial_outputs_are_not_overwritten(renderer, monkeypatch: pytest.MonkeyPatch, existing_name: str) -> None:
    existing = renderer.ROOT / existing_name
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b'keep-existing-user-result')
    original = existing.read_bytes()
    started = []

    def should_not_start(*args, **kwargs):
        started.append(True)
        raise AssertionError('输出目录已有文件时不得启动渲染')

    monkeypatch.setattr(renderer, 'ThreadPoolExecutor', should_not_start)
    with pytest.raises((SystemExit, FileExistsError, ValueError)):
        renderer.main()
    assert started == []
    assert existing.read_bytes() == original
