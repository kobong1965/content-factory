"""One identity contract for planning, artifact paths and batch registration.

Titles and source/artifact filenames are independent Unicode text. Internal
identities are deliberately ASCII so model output cannot become a path.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated

from pydantic import Field

CANDIDATE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,100}$"
CandidateIdentity = Annotated[str, Field(pattern=CANDIDATE_ID_PATTERN)]
_WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def valid_candidate_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(CANDIDATE_ID_PATTERN, value) is not None


def artifact_safe_candidate_id(value: object) -> bool:
    """A logical ID may be historical; new Windows filenames need this guard."""
    return valid_candidate_id(value) and value.casefold() not in _WINDOWS_DEVICE_NAMES


def stable_candidate_id(index: int, raw_id: object) -> str:
    """Preserve file-safe IDs; replace unsafe model labels deterministically.

    ``index`` is the one-based position in the candidate list, not an ID from
    the model. The caller must still reject duplicate valid IDs in one plan.
    JSON is used to distinguish null, strings and numbers without trusting a
    user-controlled path, lossy transliteration, or Python's process hash.
    """
    if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 20:
        raise ValueError("成片序号必须是 1 到 20 的整数")
    if artifact_safe_candidate_id(raw_id):
        return raw_id
    canonical = json.dumps([index, raw_id], ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"candidate_{index:02d}_{digest}"
