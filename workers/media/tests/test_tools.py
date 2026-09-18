from __future__ import annotations

import sys

import pytest

from content_factory_media.tools import MediaToolError, run_command


def test_media_command_failure_exposes_pid_exit_code_and_bounded_stderr() -> None:
    marker = "x" * 2_000 + ":damaged-frame"

    with pytest.raises(MediaToolError) as caught:
        run_command(
            [sys.executable, "-c", f"import sys; sys.stderr.write({marker!r}); raise SystemExit(7)"],
            timeout=10,
        )

    assert caught.value.pid is not None and caught.value.pid > 0
    assert caught.value.exit_code == 7
    assert "damaged-frame" in caught.value.stderr_tail
    assert len(caught.value.stderr_tail) <= 1_000


def test_media_command_timeout_terminates_child_and_reports_timeout() -> None:
    with pytest.raises(MediaToolError) as caught:
        run_command(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=1,
        )

    assert caught.value.pid is not None
    assert caught.value.diagnostic_code == "media_timeout"


@pytest.mark.parametrize(
    ("stderr", "diagnostic_code"),
    [
        ("Invalid data found when processing input: corrupt packet", "media_decode_failed"),
        ("No space left on device", "disk_full"),
    ],
)
def test_media_failure_classifies_corrupt_input_and_disk_exhaustion(
    stderr: str,
    diagnostic_code: str,
) -> None:
    with pytest.raises(MediaToolError) as caught:
        run_command(
            [sys.executable, "-c", f"import sys; sys.stderr.write({stderr!r}); raise SystemExit(1)"],
            timeout=10,
        )

    assert caught.value.exit_code == 1
    assert caught.value.diagnostic_code == diagnostic_code
    assert stderr.split(":")[0] in caught.value.stderr_tail
