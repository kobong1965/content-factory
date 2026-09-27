"""Regression contracts for importing Codex evidence Skill archives.

The desktop Skill picker accepts a ``.cfskills`` snapshot today, while the
portable Skill packages produced by Codex are evidence folders.  Those folders
contain a reviewed ``SKILL.md`` and one or more
``references/*/software-skill-current.json`` files, plus optional source media.
The media is evidence for a human, not part of the runtime Skill package and
must not make a valid package fail the 32 MiB canonical ``.cfskills`` limit.

These tests deliberately use the public preview/approve API instead of a
private converter helper.  That keeps the contract stable while allowing the
implementation to choose where archive normalisation lives.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from content_factory_api.main import app
from content_factory_api.skill_bundle import export_bundle, inspect_bundle
from test_skill_bundle import populated


MAX_CANONICAL_SIZE = 32 * 1024**2


def _write_repeating_media(archive: zipfile.ZipFile, name: str, size: int) -> None:
    """Write deterministic, uncompressed media without retaining it in memory."""

    # ZIP_STORED is intentional: the source archive is larger than 32 MiB even
    # though the canonical Skill output, which drops media, is tiny.
    with archive.open(name, "w") as output:
        block = bytes((index * 131 + 17) % 256 for index in range(1024 * 1024))
        remaining = size
        while remaining:
            chunk = block if remaining >= len(block) else block[:remaining]
            output.write(chunk)
            remaining -= len(chunk)


def _evidence_archive(tmp_path: Path) -> tuple[Path, dict]:
    """Build a realistic Codex evidence archive and return its source skill."""

    source, _ = populated(tmp_path / "source")
    canonical = tmp_path / "source.cfskills"
    export_bundle(source.database_path, canonical)
    envelope = json.loads(canonical.read_text(encoding="utf-8"))
    skill = envelope["payload"]["skills"][0]
    candidate = envelope["payload"]["candidates"][0]

    archive_path = tmp_path / "codex-evidence-skill.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        prefix = "references/4.20-J85"
        archive.writestr(f"{prefix}/software-skill-current.json", json.dumps(skill, ensure_ascii=False))
        archive.writestr(
            f"{prefix}/SKILL.md",
            "---\nname: 4.20-J85\ndescription: reviewed evidence\n---\n\n# 固定机位结果先行\n",
        )
        # The sidecar is part of the evidence package and gives importers the
        # original candidate row without requiring any source database.
        archive.writestr(f"{prefix}/candidate.json", json.dumps(candidate, ensure_ascii=False))
        archive.writestr(f"{prefix}/analysis-report.json", '{"source_videos_included": true}')
        archive.writestr(f"{prefix}/evidence/frame-0001.jpg", b"not-a-runtime-video")
        _write_repeating_media(archive, "original/4.20 J85.mp4", 33 * 1024**2)
    return archive_path, skill


def test_codex_evidence_archive_is_normalized_to_small_cfskills_without_media(tmp_path, monkeypatch):
    """A >32 MiB evidence ZIP imports as a small, media-free runtime bundle."""

    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "target"))
    archive_path, source_skill = _evidence_archive(tmp_path)
    assert archive_path.stat().st_size > MAX_CANONICAL_SIZE

    client = TestClient(app)
    response = client.post(
        "/s3/skill-packages/preview",
        files={"package": (archive_path.name, archive_path.read_bytes(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["source_videos_included"] is False
    assert preview["skills"][0]["skill_id"] == source_skill["skill_id"]

    canonical_path = tmp_path / "target" / "skills" / "incoming" / f"{preview['package_id']}.cfskills"
    assert canonical_path.is_file()
    assert canonical_path.stat().st_size < MAX_CANONICAL_SIZE
    payload, checksum = inspect_bundle(canonical_path)
    assert checksum == preview["package_id"]
    assert payload["source_videos_included"] is False
    assert payload["skills"][0]["skill_id"] == source_skill["skill_id"]
    # Source media may be mentioned as evidence metadata, but it must not be
    # embedded as a file/blob in the canonical package.
    assert "original/4.20 J85.mp4" not in canonical_path.read_text(encoding="utf-8")
    assert not any(
        path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
        for path in canonical_path.parent.rglob("*")
        if path.is_file()
    )

    approved = client.post(
        "/s3/skill-packages/approve",
        json={"package_id": preview["package_id"], "confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["imported"] == 1


def test_plain_skill_markdown_is_not_treated_as_a_runtime_skill_package(tmp_path, monkeypatch):
    """A lone SKILL.md still requires review evidence and is rejected clearly."""

    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "target"))
    client = TestClient(app)
    response = client.post(
        "/s3/skill-packages/preview",
        files={"package": ("SKILL.md", "# 只有说明，没有来源证据\n".encode("utf-8"))},
    )
    assert response.status_code == 422
    assert "skill" in response.text.lower() or "Skill" in response.text
    incoming = tmp_path / "target" / "skills" / "incoming"
    assert not incoming.exists() or not list(incoming.iterdir())


def test_zip_with_markdown_and_media_but_without_current_skill_json_is_rejected(tmp_path, monkeypatch):
    """A media folder cannot bypass the required software-skill-current record."""

    monkeypatch.setenv("CONTENT_FACTORY_ANALYSIS_ROOT", str(tmp_path / "target"))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("references/method/SKILL.md", "# 只有 Markdown")
        archive.writestr("original/source.mp4", b"video")
    client = TestClient(app)
    response = client.post(
        "/s3/skill-packages/preview",
        files={"package": ("missing-current-json.zip", stream.getvalue(), "application/zip")},
    )
    assert response.status_code == 422, response.text
