"""Explicit, revisioned deletion of unreferenced managed upload files only.

The durable intent is committed before the first unlink. A failed/interrupted
purge remains a non-restorable tombstone until the user confirms a fresh plan.
Analysis, rendered media, captions and external originals are never cascaded.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .auto_edit_store import AutoEditProjectStore


ALLOWED = {"draft", "failed", "cancelled", "review", "completed", "purging", "purge_failed"}
WARNING = "永久删除不能恢复；仅删除清单中未被引用的软件托管原片。外部原文件、成片、字幕、分析缓存和审计记录保留。"


def _conflict(message: str):
    from .auto_edit_store import AutoEditConflictError
    return AutoEditConflictError(message)


def _eligible(project, revision):
    if project["revision"] != revision:
        raise _conflict("项目已更新，请刷新回收站后重新确认")
    if not project.get("deleted_at"):
        raise _conflict("只能永久删除回收站中的项目")
    if project["status"] not in ALLOWED:
        raise _conflict("当前项目不能永久删除，请等待任务结束")


def _path_key(value):
    try:
        path = Path(value)
        if not path.is_absolute():
            return None
        return os.path.normcase(str(path.resolve(strict=False)))
    except (OSError, RuntimeError, ValueError) as exc:
        raise _conflict("无法确认文件引用路径，已停止永久删除") from exc


def _paths(value):
    """Conservatively protect absolute paths anywhere in stored payloads."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _paths(item)
    elif isinstance(value, str):
        key = _path_key(value)
        if key:
            yield key


def _no_reparse(path: Path):
    """Check lexical ancestors before resolve(), including the managed root."""
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("路径含符号链接或目录联接")


def _source_entry(root: Path, source: dict, references: dict[str, set[str]]):
    raw = str(source.get("path") or "")
    item = {"path": raw, "file_name": str(source.get("file_name") or Path(raw).name),
            "action": "preserve", "reason": "不是软件托管的上传原片，已保留", "size_bytes": 0}
    path = Path(raw)
    try:
        if not path.is_absolute() or ".." in path.parts:
            return item
        relative = path.relative_to(root / "sources")
        if len(relative.parts) != 2 or not re.fullmatch(r"[a-f0-9]{32}", relative.parts[0]):
            return item
        if path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".m4v", ".webm"}:
            return item
        _no_reparse(path)
        if path.resolve(strict=False) != path.absolute():
            return item
        key = _path_key(raw)
        if key in references:
            item["reason"] = "仍被其他项目、成片或字幕引用，已保留"
            item["references"] = sorted(references[key])
            return item
        try:
            info = path.stat()
        except FileNotFoundError:
            item.update(action="missing", reason="文件已不存在")
            return item
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            item["reason"] = "不是独立普通文件或存在硬链接，已保留"
            return item
        # Metadata identity is bound to the preview token; deletion checks it
        # again immediately beforehand. Do not hash multi-GB video on every UI click.
        item.update(action="delete", reason="未被其他项目或成片引用的托管原片",
                    size_bytes=info.st_size,
                    fingerprint=[info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])
    except (OSError, ValueError, RuntimeError):
        item["reason"] = "路径不可安全验证或含符号链接/目录联接，已保留"
    return item


def _decode_reference(value):
    try:
        result = json.loads(value)
        if not isinstance(result, (dict, list)):
            raise ValueError("invalid reference document")
        return result
    except (TypeError, ValueError) as exc:
        raise _conflict("无法读取项目/成片/字幕引用记录，已停止永久删除") from exc


def _manifest_references(root):
    # A render can write its manifest before committing the review database.
    # Keep those references so an interrupted registration remains retryable.
    for parent in (root / "outputs", root.parent / "subtitle-editor"):
        try:
            _no_reparse(parent)
            if not parent.exists():
                continue
            for directory in parent.iterdir():
                _no_reparse(directory)
                if not directory.is_dir():
                    continue
                for filename in ("manifest.json", "manifest.json.tmp"):
                    manifest = directory / filename
                    _no_reparse(manifest)
                    if manifest.is_file():
                        if manifest.stat().st_size > 8 * 1024 * 1024:
                            raise ValueError("manifest too large to verify")
                        yield (f"{parent.name}/{directory.name}/{filename}",
                               _decode_reference(manifest.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError, RuntimeError) as exc:
            raise _conflict("无法确认待登记成片/字幕清单的引用，已停止永久删除") from exc


@contextmanager
def _locked_reference_databases(root: Path):
    """Serialize against existing batch/subtitle/editing writers while unlinking.

    Lock order is always reference databases then the project database. Missing
    databases contain no committed references; no new database is created here.
    """
    specs = [
        (root.parent / "footage-batches.sqlite3", {"batches": {"payload"}}),
        (root.parent / "subtitle-editor" / "drafts.sqlite3", {"drafts": {"payload"}, "history": {"payload"}}),
        (root.parent / "editing.sqlite3", None),
    ]
    with ExitStack() as stack:
        databases = []
        for path, expected in specs:
            try:
                _no_reparse(path)
                if not path.exists():
                    continue
                connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)
                stack.callback(connection.close)
                connection.execute("BEGIN IMMEDIATE")
                tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if expected and not tables.intersection(expected):
                    raise ValueError("required reference table missing")
                rows = []
                for table in sorted(tables):
                    if expected and table not in expected:
                        continue
                    escaped_table = table.replace('"', '""')
                    columns = {r[1] for r in connection.execute(f'PRAGMA table_info("{escaped_table}")')}
                    payload_columns = {c for c in columns if c == "payload" or c.endswith("_json")}
                    if expected and not expected[table].issubset(columns):
                        raise ValueError("required reference field missing")
                    for column in sorted(payload_columns):
                        escaped_column = column.replace('"', '""')
                        for (payload,) in connection.execute(f'SELECT "{escaped_column}" FROM "{escaped_table}"'):
                            if payload is not None:
                                rows.append((f"{path.name}/{table}", _decode_reference(payload)))
                    # Legacy rendering keeps some resource paths in dedicated
                    # columns rather than in its output/project JSON payload.
                    for column in sorted(c for c in columns if c == "path" or c.endswith("_path")):
                        escaped_column = column.replace('"', '""')
                        for (value,) in connection.execute(f'SELECT "{escaped_column}" FROM "{escaped_table}"'):
                            if value is not None:
                                if not isinstance(value, str):
                                    raise ValueError("invalid resource path")
                                rows.append((f"{path.name}/{table}", {"path": value}))
                databases.extend(rows)
            except (OSError, sqlite3.Error, ValueError) as exc:
                raise _conflict("无法确认成片或字幕引用关系，已停止永久删除；请修复引用数据库后重试") from exc
        databases.extend(_manifest_references(root))
        yield databases


def _plan(store, connection, project, external_references):
    references: dict[str, set[str]] = {}
    for row in connection.execute("SELECT project_id,payload_json FROM auto_edit_projects"):
        if row["project_id"] == project["project_id"]:
            continue
        other = _decode_reference(row["payload_json"])
        if not isinstance(other, dict):
            raise _conflict("无法确认其他项目引用，已停止永久删除")
        if other.get("status") != "purged":
            for key in _paths(other):
                references.setdefault(key, set()).add("项目 " + row["project_id"])
    for origin, payload in external_references:
        for key in _paths(payload):
            references.setdefault(key, set()).add(origin)
    sources = project.get("sources") or [project.get("source")]
    if not sources or any(not isinstance(item, dict) or not item.get("path") for item in sources):
        raise _conflict("项目原片清单不完整，已停止永久删除")
    if project.get("output_batch_id"):
        # Registry loss/migration must not make previously exported originals
        # suddenly deletable. They remain useful for review/subtitle recovery.
        for source in sources:
            key = _path_key(source["path"])
            if key:
                references.setdefault(key, set()).add("本项目成片批次 " + str(project["output_batch_id"]))
    files = []
    seen = set()
    for source in sources:
        path = str(source["path"])
        if os.path.normcase(path) not in seen:
            seen.add(os.path.normcase(path))
            files.append(_source_entry(store.database_path.parent, source, references))
    plan = {"project_id": project["project_id"], "revision": project["revision"], "files": files,
            "delete_count": sum(f["action"] == "delete" for f in files),
            "delete_bytes": sum(f["size_bytes"] for f in files if f["action"] == "delete"),
            "preserved_count": sum(f["action"] == "preserve" for f in files), "warning": WARNING}
    plan["confirmation_token"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                                           separators=(",", ":")).encode()).hexdigest()
    return plan


def preview(store: AutoEditProjectStore, project_id: str, *, expected_revision: int):
    with _locked_reference_databases(store.database_path.parent) as references, store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        project = store._decode(store._row(connection, project_id))
        _eligible(project, expected_revision)
        return _plan(store, connection, project, references)


def purge(store: AutoEditProjectStore, project_id: str, *, expected_revision: int, confirmation_token: str | None):
    from .auto_edit_store import _now_iso
    if not isinstance(confirmation_token, str) or len(confirmation_token) != 64:
        raise _conflict("请先查看永久删除清单并二次确认")
    # Commit intent first. Even process termination at any following instruction
    # cannot turn partially deleted sources back into a restorable normal project.
    with _locked_reference_databases(store.database_path.parent) as references, store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        project = store._decode(store._row(connection, project_id))
        _eligible(project, expected_revision)
        plan = _plan(store, connection, project, references)
        if not hmac.compare_digest(confirmation_token, plan["confirmation_token"]):
            raise _conflict("删除清单或引用关系已变化，确认已过期，请重新查看并确认")
        previous = project.get("purge") or {}
        removed = list(previous.get("removed") or [])
        # A previous process may exit after unlink and before result commit.
        planned_before = {f["path"] for f in previous.get("plan", {}).get("files", []) if f["action"] == "delete"}
        for item in plan["files"]:
            if item["action"] == "missing" and item["path"] in planned_before and item["path"] not in removed:
                removed.append(item["path"])
        journal = {"started_at": previous.get("started_at") or _now_iso(), "plan": copy.deepcopy(plan),
                   "removed": removed, "skipped": [], "errors": [], "attempt": int(previous.get("attempt", 0)) + 1}
        project.update(status="purging", revision=project["revision"] + 1, updated_at=_now_iso(), purge=journal)
        store._write(connection, project)
        store._event(connection, project_id, "purge_started", {"revision": project["revision"], "plan": plan})

    # Reacquire both boundaries after the durable commit and recheck all refs.
    # If a new reference was committed between stages, stop before deleting.
    try:
        with _locked_reference_databases(store.database_path.parent) as references, store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = store._decode(store._row(connection, project_id))
            if current["revision"] != project["revision"] or current["status"] != "purging":
                raise _conflict("永久删除任务状态已变化，请刷新后重试")
            live_plan = _plan(store, connection, current, references)
            if live_plan["files"] != plan["files"]:
                raise _conflict("提交后文件或引用关系已变化，已停止删除，请重新预览确认")
            for item in plan["files"]:
                if item["action"] != "delete":
                    journal["skipped"].append({"path": item["path"], "reason": item["reason"]})
                    continue
                path = Path(item["path"])
                try:
                    # Protect against file/junction replacement during the run.
                    checked = _source_entry(store.database_path.parent, {"path": str(path)}, {})
                    if checked["action"] != "delete" or checked.get("fingerprint") != item["fingerprint"]:
                        raise OSError("文件或路径已变化；请重新预览确认")
                    path.unlink()
                    if str(path) not in journal["removed"]:
                        journal["removed"].append(str(path))
                except OSError as exc:
                    journal["errors"].append({"path": str(path), "reason": str(exc)[:500]})
            _finish(store, connection, current, journal)
    except Exception as exc:
        # Leave a durable failed state even when rechecking references fails.
        # BaseException deliberately isn't caught: a real crash leaves purging,
        # whose confirmed plan can be inspected/retried but never restored.
        with store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = store._decode(store._row(connection, project_id))
            if current["revision"] != project["revision"]:
                raise
            journal["errors"].append({"path": "", "reason": str(exc)[:500]})
            _finish(store, connection, current, journal)
    return {"project_id": project_id, "purged": not journal["errors"], "revision": current["revision"],
            "removed": journal["removed"], "skipped": journal["skipped"], "errors": journal["errors"]}


def _finish(store, connection, project, journal):
    from .auto_edit_store import _now_iso
    status = "purge_failed" if journal["errors"] else "purged"
    journal["finished_at"] = _now_iso()
    project.update(status=status, revision=project["revision"] + 1, updated_at=_now_iso(), purge=journal)
    store._write(connection, project)
    store._event(connection, project["project_id"], status, {"revision": project["revision"], **journal})
