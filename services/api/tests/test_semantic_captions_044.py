from types import SimpleNamespace

import pytest


def _words():
    return [
        {"text": "这条", "start_ms": 0, "end_ms": 240},
        {"text": "裤子", "start_ms": 250, "end_ms": 520},
        {"text": "面料", "start_ms": 700, "end_ms": 900},
        {"text": "很舒服", "start_ms": 910, "end_ms": 1300},
    ]


def test_semantic_grouping_uses_model_boundaries_but_preserves_spoken_words(monkeypatch):
    from content_factory_api import semantic_captions

    def gateway(*_args, **kwargs):
        assert kwargs["keyframe_data_urls"] == []
        return SimpleNamespace(content={"cues": [
            {"start_word_index": 0, "end_word_index": 2, "reason": "商品引入完成", "confidence": 0.94},
            {"start_word_index": 2, "end_word_index": 4, "reason": "卖点解释完成", "confidence": 0.91},
        ]})

    monkeypatch.setattr(semantic_captions, "call_gateway", gateway)
    cues = semantic_captions.group_words_semantically(_words(), gateway_config=object())

    assert [cue["text"] for cue in cues] == ["这条裤子", "面料很舒服"]
    assert [word["text"] for cue in cues for word in cue["words"]] == [word["text"] for word in _words()]
    assert cues[0]["start_ms"] == 0 and cues[0]["end_ms"] == 520
    assert cues[1]["start_ms"] == 700 and cues[1]["end_ms"] == 1300
    assert all(cue["alignment_source"] == "api_word_timestamps+semantic_boundaries" for cue in cues)


def test_semantic_grouping_rejects_model_that_skips_or_reorders_words(monkeypatch):
    from content_factory_api import semantic_captions

    monkeypatch.setattr(
        semantic_captions,
        "call_gateway",
        lambda *_args, **_kwargs: SimpleNamespace(content={"cues": [
            {"start_word_index": 0, "end_word_index": 1, "reason": "不完整", "confidence": 0.9},
            {"start_word_index": 3, "end_word_index": 4, "reason": "跳过字词", "confidence": 0.9},
        ]}),
    )
    with pytest.raises(semantic_captions.SemanticCaptionError, match="跳过、重复或打乱"):
        semantic_captions.group_words_semantically(_words(), gateway_config=object())


def test_semantic_grouping_requires_text_model_instead_of_fixed_random_split():
    from content_factory_api import semantic_captions

    with pytest.raises(semantic_captions.SemanticCaptionError, match="未配置字幕语义分析模型"):
        semantic_captions.group_words_semantically(_words(), gateway_config=None)

