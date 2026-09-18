"""Structured S6 capture roles with non-destructive legacy migration."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping, MutableMapping
from typing import Any


HOST_TAKE_NOTE_PREFIX = "[主播连续长镜头]"
DETAIL_NOTE_PREFIX = "[同款商品细节镜头]"
CAPTURE_ROLES = {"host_take", "detail", "standard"}


def _legacy_role_and_user_note(note: object) -> tuple[str | None, str]:
    text = str(note or "").strip()
    for prefix, role in (
        (HOST_TAKE_NOTE_PREFIX, "host_take"),
        (DETAIL_NOTE_PREFIX, "detail"),
    ):
        if text.startswith(prefix):
            return role, text[len(prefix):].strip()
    return None, text


def material_capture_role(material_or_archive: Mapping[str, Any]) -> str:
    """Return the immutable structured role, falling back to old note prefixes."""

    role = material_or_archive.get("capture_role")
    if isinstance(role, str) and role in CAPTURE_ROLES:
        return role
    archive = material_or_archive.get("archive")
    if not isinstance(archive, Mapping):
        archive = material_or_archive
    legacy_role, _ = _legacy_role_and_user_note(archive.get("note"))
    return legacy_role or "standard"


def _full_take_clip(profile: Mapping[str, Any], scene_clips: list[Mapping[str, Any]]) -> dict[str, Any]:
    first = scene_clips[0]
    token = hashlib.sha256(str(profile.get("material_id", "material")).encode("utf-8")).hexdigest()[:24]
    transcripts = [str(item.get("transcript") or "").strip() for item in scene_clips]
    transcript = " ".join(dict.fromkeys(item for item in transcripts if item))[:5000]
    return {
        "id": f"clip_{token}_full",
        # The first detected scene supplies the preview keyframe. The interval,
        # not this preview reference, is authoritative for the full take.
        "source_shot_id": first["source_shot_id"],
        "order": len(scene_clips) + 1,
        "start_ms": 0,
        "end_ms": int(profile["file"]["duration_ms"]),
        "keyframe_ref": first["keyframe_ref"],
        "transcript": transcript,
        "purpose_tags": ["broll"],
        "visual_tags": list(first.get("visual_tags", [])),
        "garment_views": list(first.get("garment_views", ["unknown"])),
        "action_tags": list(first.get("action_tags", [])),
        "scene_tags": list(first.get("scene_tags", [])),
        "shot_size": str(first.get("shot_size") or "待识别"),
        "people_count": int(first.get("people_count", 0)),
        "standalone_usable": True,
        "reusable": True,
        "quality": copy.deepcopy(first["quality"]),
        "note": "",
        "capture_scope": "full_take",
    }


def normalize_material_capture_metadata(
    profile: MutableMapping[str, Any], *, explicit_role: str | None = None,
) -> MutableMapping[str, Any]:
    """Upgrade one material in memory without deleting any source scene clips.

    A legacy prefix is consumed only while ``capture_role`` is absent. Once the
    structured field exists, later user note edits can never change the role.
    """

    existing_role = profile.get("capture_role")
    archive = profile.get("archive")
    if not isinstance(archive, MutableMapping):
        return profile

    if explicit_role is not None:
        if explicit_role not in CAPTURE_ROLES:
            raise ValueError("素材角色必须是 host_take、detail 或 standard")
        role = explicit_role
    elif isinstance(existing_role, str) and existing_role in CAPTURE_ROLES:
        role = existing_role
    else:
        legacy_role, user_note = _legacy_role_and_user_note(archive.get("note"))
        role = legacy_role or "standard"
        if legacy_role is not None:
            archive["note"] = user_note
    profile["capture_role"] = role

    raw_clips = profile.get("clips")
    if not isinstance(raw_clips, list) or not raw_clips:
        return profile
    clips = [item for item in raw_clips if isinstance(item, MutableMapping)]
    for clip in clips:
        if "capture_scope" not in clip:
            clip["capture_scope"] = "scene"
    if role == "host_take" and not any(item.get("capture_scope") == "full_take" for item in clips):
        scene_clips = [item for item in clips if item.get("capture_scope") != "full_take"]
        if scene_clips:
            raw_clips.append(_full_take_clip(profile, scene_clips))
    return profile
