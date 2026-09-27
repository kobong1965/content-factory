"""Destructive project actions use only synthetic, isolated managed sources."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

import pytest

from content_factory_api.auto_edit_store import (
    AutoEditConflictError, AutoEditNotFoundError, AutoEditProjectStore,
)


SETTINGS = {
    "target_count": 1, "duration_min_ms": 5000, "duration_max_ms": 10000,
    "subtitle_font_size": 68, "keyword_color": "#FFD400", "keyword_scale": 1.3,
    "top_title_enabled": False,
}


def make_store(tmp_path):
    return AutoEditProjectStore(tmp_path / "s7" / "auto-edit" / "auto-edit-projects.sqlite3")


def add_project(store, *, count=1, external=None):
    directory = store.database_path.parent / "sources" / uuid4().hex
    directory.mkdir(parents=True)
    sources = []
    for index in range(count):
        path = external or directory / f"{index:02d}.mp4"
        path.write_bytes(f"isolated video {index}".encode())
        sources.append({"source_id": f"source_{index}", "file_name": f"原片{index}.mp4",
                        "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "duration_ms": 10000})
    project = store.create_project(title="永久删除隔离样例", sources=sources, settings=SETTINGS)
    return store.set_deleted(project["project_id"], expected_revision=project["revision"], deleted=True)


def preview(store, project):
    return store.preview_purge(project["project_id"], expected_revision=project["revision"])


def execute(store, project, plan=None):
    plan = plan or preview(store, project)
    return store.purge_project(project["project_id"], expected_revision=project["revision"],
                              confirmation_token=plan["confirmation_token"])


def test_without_preview_confirmation_never_deletes_a_source(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    with pytest.raises(AutoEditConflictError, match="确认"):
        store.purge_project(project["project_id"], expected_revision=project["revision"])
    assert Path(project["source"]["path"]).is_file()


def test_managed_delete_keeps_tombstone_events_and_does_not_touch_analysis_or_outputs(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    analysis = store.database_path.parent / "analysis" / project["project_id"]
    analysis.mkdir(parents=True)
    artifact = analysis / "keep.json"
    artifact.write_text("{}")
    plan = preview(store, project)
    assert plan["delete_count"] == 1 and plan["delete_bytes"] > 0
    assert Path(project["source"]["path"]).is_file(), "Preview must be read-only"
    result = execute(store, project, plan)
    assert result["purged"] and not Path(project["source"]["path"]).exists()
    assert artifact.is_file(), "Permanent source deletion must not recurse through analysis/output directories"
    assert store.list_projects(deleted=True) == []
    with pytest.raises(AutoEditNotFoundError):
        store.get_project(project["project_id"])
    with sqlite3.connect(store.database_path) as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM auto_edit_projects").fetchone()[0])
        event_types = {row[0] for row in connection.execute("SELECT event_type FROM auto_edit_events")}
    assert payload["status"] == "purged" and payload["source"] == project["source"]
    assert payload["purge"]["removed"] == [project["source"]["path"]]
    assert {"created", "trashed", "purge_started", "purged"} <= event_types


@pytest.mark.parametrize("reference", ["project", "trashed_project", "batch", "subtitle_history"])
def test_referenced_source_is_preserved_and_reported(tmp_path, reference):
    store = make_store(tmp_path)
    project = add_project(store)
    source = project["source"]
    if reference in {"project", "trashed_project"}:
        other = store.create_project(title="引用项目", source=source, settings=SETTINGS)
        if reference == "trashed_project":
            store.set_deleted(other["project_id"], expected_revision=1, deleted=True)
    else:
        s7 = store.database_path.parent.parent
        db = s7 / ("footage-batches.sqlite3" if reference == "batch" else "subtitle-editor/drafts.sqlite3")
        db.parent.mkdir(exist_ok=True)
        with sqlite3.connect(db) as connection:
            if reference == "batch":
                connection.execute("CREATE TABLE batches (id TEXT, payload TEXT)")
                connection.execute("INSERT INTO batches VALUES (?,?)", ("keep", json.dumps({
                    "candidates": [{"source_path": source["path"], "resources": {"source": {"path": source["path"]}}}]
                })))
            else:
                connection.execute("CREATE TABLE history (id TEXT, revision INTEGER, payload TEXT)")
                connection.execute("INSERT INTO history VALUES (?,?,?)", ("keep", 1, json.dumps({"source_path": source["path"]})))
    plan = preview(store, project)
    assert plan["delete_count"] == 0 and plan["preserved_count"] == 1
    assert "引用" in plan["files"][0]["reason"]
    result = execute(store, project, plan)
    assert result["purged"] and not result["removed"] and Path(source["path"]).is_file()


def test_external_original_is_preserved_even_after_project_purge(tmp_path):
    store = make_store(tmp_path)
    original = tmp_path / "相机原片.mp4"
    project = add_project(store, external=original)
    plan = preview(store, project)
    assert plan["delete_count"] == 0 and plan["files"][0]["action"] == "preserve"
    assert execute(store, project, plan)["purged"] and original.is_file()


def test_corrupt_reference_database_fails_closed(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    (store.database_path.parent.parent / "footage-batches.sqlite3").write_bytes(b"not sqlite")
    with pytest.raises(AutoEditConflictError, match="引用"):
        preview(store, project)
    assert Path(project["source"]["path"]).is_file()
    assert store.get_project(project["project_id"])["revision"] == project["revision"]


def test_preview_token_invalidates_when_source_or_references_change(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    plan = preview(store, project)
    store.create_project(title="确认后才出现的引用", source=project["source"], settings=SETTINGS)
    with pytest.raises(AutoEditConflictError, match="变化|过期"):
        execute(store, project, plan)
    assert Path(project["source"]["path"]).is_file()


def test_permission_error_is_retryable_and_cannot_restore_partially_removed_sources(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    project = add_project(store, count=2)
    protected = Path(project["sources"][1]["path"])
    unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == protected:
            raise PermissionError("synthetic sharing violation")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", locked)
        result = execute(store, project)
    assert result["purged"] is False and result["errors"]
    partial = store.get_project(project["project_id"])
    assert partial["status"] == "purge_failed" and partial["revision"] > project["revision"]
    assert not Path(project["sources"][0]["path"]).exists() and protected.is_file()
    with pytest.raises(AutoEditConflictError, match="删除|恢复"):
        store.set_deleted(project["project_id"], expected_revision=partial["revision"], deleted=False)
    result = execute(AutoEditProjectStore(store.database_path), partial)
    assert result["purged"] and not protected.exists()
    assert sorted(result["removed"]) == sorted(source["path"] for source in project["sources"])


def test_process_interruption_after_one_unlink_leaves_committed_journal_and_can_resume(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    project = add_project(store, count=2)
    second = Path(project["sources"][1]["path"])
    unlink = Path.unlink

    def abrupt(path, *args, **kwargs):
        if path == second:
            raise SystemExit("synthetic process exit")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", abrupt)
        with pytest.raises(SystemExit):
            execute(store, project)
    interrupted = AutoEditProjectStore(store.database_path).get_project(project["project_id"])
    assert interrupted["status"] == "purging"
    with pytest.raises(AutoEditConflictError):
        store.set_deleted(project["project_id"], expected_revision=interrupted["revision"], deleted=False)
    assert execute(store, interrupted)["purged"] and not second.exists()


def test_junction_replacement_after_upload_never_deletes_external_target(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    source = Path(project["source"]["path"])
    source.unlink()
    source.parent.rmdir()
    target = tmp_path / "external"
    target.mkdir()
    victim = target / source.name
    victim.write_bytes(b"do not delete")
    # Windows junctions are the actual escape primitive available to ordinary users.
    import os
    import subprocess
    if os.name == "nt":
        command = f'New-Item -ItemType Junction -Path \'{source.parent}\' -Target \'{target}\' | Out-Null'
        subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True)
    else:
        source.parent.symlink_to(target, target_is_directory=True)
    plan = preview(store, project)
    assert plan["files"][0]["action"] == "preserve"
    assert execute(store, project, plan)["purged"] and victim.read_bytes() == b"do not delete"


def test_pending_project_and_stale_revision_cannot_purge(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    with pytest.raises(AutoEditConflictError):
        store.preview_purge(project["project_id"], expected_revision=1)
    restored = store.set_deleted(project["project_id"], expected_revision=project["revision"], deleted=False)
    with pytest.raises(AutoEditConflictError, match="回收站"):
        preview(store, restored)
    assert Path(project["source"]["path"]).is_file()


@pytest.mark.parametrize("folder", ["auto-edit/outputs/pending-batch", "subtitle-editor/pending-edit"])
def test_unregistered_output_manifest_protects_sources_for_retry(tmp_path, folder):
    store = make_store(tmp_path)
    project = add_project(store)
    manifest = store.database_path.parent.parent / folder / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"candidates": [{"source_path": project["source"]["path"]}]}))
    plan = preview(store, project)
    assert plan["delete_count"] == 0 and plan["preserved_count"] == 1
    assert execute(store, project, plan)["purged"] and Path(project["source"]["path"]).is_file()


def test_hardlinked_source_is_never_physically_removed(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    linked = tmp_path / "同一硬链接原文件.mp4"
    linked.hardlink_to(Path(project["source"]["path"]))
    plan = preview(store, project)
    assert plan["delete_count"] == 0 and "硬链接" in plan["files"][0]["reason"]
    assert execute(store, project, plan)["purged"] and linked.is_file()


def test_changed_file_requires_fresh_confirmation(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    plan = preview(store, project)
    path = Path(project["source"]["path"])
    path.write_bytes(b"replaced with different content")
    with pytest.raises(AutoEditConflictError, match="变化|过期"):
        execute(store, project, plan)
    assert path.read_bytes() == b"replaced with different content"


@pytest.mark.parametrize("status", ["draft", "purging", "purge_failed", "purged"])
def test_late_worker_callbacks_cannot_revive_deleted_project(tmp_path, status):
    store = make_store(tmp_path)
    project = add_project(store)
    project["status"] = status
    with store._connect() as connection:
        store._write(connection, project)
    before = json.dumps(project, sort_keys=True)
    failed = store.fail(project["project_id"], message="late worker failure")
    assert json.dumps(failed, sort_keys=True) == before
    with pytest.raises(AutoEditConflictError, match="删除|回收站"):
        store.register_output_batch(project["project_id"], batch_id="late-output")
    with pytest.raises(AutoEditConflictError, match="删除|回收站"):
        store.cancel(project["project_id"], expected_revision=project["revision"])
    with store._connect() as connection:
        actual = store._decode(store._row(connection, project["project_id"]))
    assert json.dumps(actual, sort_keys=True) == before


def test_legacy_render_resource_managed_path_is_protected(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    db = store.database_path.parent.parent / "editing.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE render_resources (resource_ref TEXT, managed_path TEXT)")
        connection.execute("INSERT INTO render_resources VALUES (?,?)", ("legacy-resource", project["source"]["path"]))
    plan = preview(store, project)
    assert plan["delete_count"] == 0 and plan["preserved_count"] == 1


def test_missing_output_registry_never_allows_deleting_completed_project_sources(tmp_path):
    store = make_store(tmp_path)
    project = add_project(store)
    project.update(output_batch_id="previously-registered-batch", status="review")
    with store._connect() as connection:
        store._write(connection, project)
    plan = preview(store, project)
    assert plan["delete_count"] == 0 and plan["preserved_count"] == 1
