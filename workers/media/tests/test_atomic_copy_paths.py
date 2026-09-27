from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from content_factory_media import tools


@pytest.mark.parametrize("guard_max_path", [False, True])
def test_atomic_copy_does_not_push_valid_destination_past_windows_limit(tmp_path, monkeypatch, guard_max_path):
    """The reported destination is 240 chars; PID/thread suffix made it 260."""
    source = tmp_path / "source.mp4"
    contents = b"a managed livestream original"
    source.write_bytes(contents)
    # Real filesystem boundary, not a media/model mock. On Windows without the
    # long-path policy this recreates the customer's failing temporary.open().
    parent = tmp_path / ("p" * (171 - len(str(tmp_path)) - 1))
    destination = parent / (hashlib.sha256(contents).hexdigest() + ".mp4")
    assert len(str(destination)) == 240
    monkeypatch.setattr(tools.os, "getpid", lambda: 65660)
    monkeypatch.setattr(tools.threading, "get_ident", lambda: 25688)
    real_open = Path.open
    opened = []

    def check_path(path, *args, **kwargs):
        opened.append(str(path))
        # This portable assertion also guards the issue on Linux / machines
        # with long-path support, where the OS would otherwise hide the defect.
        assert len(str(path)) < 260, "atomic staging name exceeds Windows MAX_PATH"
        return real_open(path, *args, **kwargs)

    if guard_max_path:
        monkeypatch.setattr(Path, "open", check_path)
    tools.copy_atomic(source, destination, expected_sha256=hashlib.sha256(contents).hexdigest())
    assert destination.read_bytes() == contents
    assert source.read_bytes() == contents
    assert list(parent.iterdir()) == [destination]


def test_atomic_copy_failure_preserves_existing_target_and_removes_partial(tmp_path, monkeypatch):
    source, destination = tmp_path / "source.mp4", tmp_path / "stored.mp4"
    source.write_bytes(b"new data")
    destination.write_bytes(b"old data")

    def interrupted(incoming, outgoing, **kwargs):
        outgoing.write(b"partial data")
        raise OSError("simulated disk full")

    monkeypatch.setattr(tools.shutil, "copyfileobj", interrupted)
    with pytest.raises(OSError, match="simulated disk full"):
        tools.copy_atomic(source, destination)
    assert destination.read_bytes() == b"old data"
    assert source.read_bytes() == b"new data"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.mp4", "stored.mp4"]
