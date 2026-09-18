"""SQLite product repository and managed S4 source files."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, BinaryIO, Literal
from uuid import uuid4

from content_factory_contracts import validate_or_raise

from .s4_products import activate_product, apply_product_changes, compute_product_completeness, new_product, now_iso

MAX_ASSET_BYTES = 500 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".pdf"}


class ProductNotFoundError(LookupError):
    pass


class ProductConflictError(RuntimeError):
    pass


class ProductAssetError(ValueError):
    pass


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _detect_media_type(extension: str, header: bytes) -> str | None:
    if extension in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if extension == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if extension == ".webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    if extension in {".mp4", ".mov"} and len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/quicktime" if extension == ".mov" else "video/mp4"
    if extension == ".pdf" and header.startswith(b"%PDF-"):
        return "application/pdf"
    return None


class ProductStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.assets_root = (self.root / "assets").resolve()
        self.database_path = self.root / "products.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    product_id TEXT PRIMARY KEY,
                    sku TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS product_versions (
                    product_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    PRIMARY KEY (product_id, revision),
                    FOREIGN KEY (product_id) REFERENCES products(product_id)
                );
                CREATE TABLE IF NOT EXISTS product_assets (
                    asset_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    UNIQUE (product_id, sha256),
                    FOREIGN KEY (product_id) REFERENCES products(product_id)
                );
                """
            )

    @staticmethod
    def _profile(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise ProductNotFoundError("找不到这个商品")
        value = json.loads(row["profile_json"])
        if not isinstance(value, dict):
            raise ValueError("商品档案损坏")
        validate_or_raise("product", value)
        return value

    def get(self, product_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._profile(connection.execute("SELECT profile_json FROM products WHERE product_id=?", (product_id,)).fetchone())

    def list(self, *, query: str = "", status: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[object] = []
        if query.strip():
            clauses.append("(sku LIKE ? OR name LIKE ?)")
            pattern = f"%{query.strip()}%"
            values.extend((pattern, pattern))
        if status:
            clauses.append("status=?")
            values.append(status)
        sql = "SELECT profile_json FROM products"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            profiles = [self._profile(row) for row in connection.execute(sql, values).fetchall()]
        return [self._summary(profile) for profile in profiles]

    @staticmethod
    def _summary(profile: Mapping[str, Any]) -> dict[str, Any]:
        fact_by_id = {
            item.get("id"): item.get("value") for item in profile.get("facts", []) if isinstance(item, Mapping)
        }
        return {
            "product_id": profile["product_id"], "sku": profile["sku"], "name": profile["name"],
            "status": profile["status"], "revision": profile["revision"], "completeness": profile["completeness"],
            "selling_points": [fact_by_id[item] for item in profile.get("selling_point_fact_ids", []) if fact_by_id.get(item)],
            "source_count": len(profile.get("sources", [])), "updated_at": profile["updated_at"],
        }

    def counts(self) -> dict[str, int]:
        result = {"total": 0, "draft": 0, "active": 0, "archived": 0}
        with self._connect() as connection:
            for row in connection.execute("SELECT status, COUNT(*) AS amount FROM products GROUP BY status"):
                result[str(row["status"])] = int(row["amount"])
                result["total"] += int(row["amount"])
        return result

    def _insert_version(
        self, connection: sqlite3.Connection, profile: Mapping[str, Any], action: str, actor: str,
    ) -> None:
        connection.execute(
            "INSERT INTO product_versions(product_id, revision, action, actor, created_at, profile_json) VALUES(?,?,?,?,?,?)",
            (profile["product_id"], profile["revision"], action, actor, profile["updated_at"], _json(profile)),
        )

    def create(self, *, sku: str | None = None, name: str, actor: str) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写操作人")
        profile = new_product(sku=sku, name=name)
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO products VALUES(?,?,?,?,?,?,?,?)",
                    (profile["product_id"], profile["sku"], profile["name"], profile["status"], profile["revision"],
                     _json(profile), profile["created_at"], profile["updated_at"]),
                )
                self._insert_version(connection, profile, "created", clean_actor)
            except sqlite3.IntegrityError as exc:
                raise ValueError("这个款号已经存在，请换一个款号或打开原商品") from exc
        return profile

    def _managed_sources(self, profile: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        return {
            str(item["id"]): item for item in profile.get("sources", [])
            if isinstance(item, Mapping) and item.get("managed") is True
        }

    def _save(
        self, connection: sqlite3.Connection, current: Mapping[str, Any], updated: dict[str, Any], *, action: str, actor: str,
    ) -> dict[str, Any]:
        updated["revision"] = int(current["revision"]) + 1
        updated["updated_at"] = now_iso()
        updated["completeness"] = compute_product_completeness(updated)
        validate_or_raise("product", updated)
        try:
            connection.execute(
                "UPDATE products SET sku=?, name=?, status=?, revision=?, profile_json=?, updated_at=? WHERE product_id=?",
                (updated["sku"], updated["name"], updated["status"], updated["revision"], _json(updated),
                 updated["updated_at"], updated["product_id"]),
            )
            self._insert_version(connection, updated, action, actor)
        except sqlite3.IntegrityError as exc:
            raise ValueError("这个款号已经存在，请换一个款号") from exc
        return updated

    def update(
        self, product_id: str, *, expected_revision: int, changes: Mapping[str, Any], actor: str,
    ) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写操作人")
        with self._lock, self._connect() as connection:
            current = self._profile(connection.execute("SELECT profile_json FROM products WHERE product_id=?", (product_id,)).fetchone())
            if current["revision"] != expected_revision:
                raise ProductConflictError("商品已经被其他人修改，请刷新后再保存")
            if current["status"] == "archived":
                raise ValueError("归档商品不能直接编辑，请先恢复为草稿")
            updated = apply_product_changes(current, changes, managed_sources=self._managed_sources(current))
            return self._save(connection, current, updated, action="updated", actor=clean_actor)

    def set_status(
        self, product_id: str, *, expected_revision: int, status: Literal["draft", "active", "archived"], actor: str,
    ) -> dict[str, Any]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写操作人")
        with self._lock, self._connect() as connection:
            current = self._profile(connection.execute("SELECT profile_json FROM products WHERE product_id=?", (product_id,)).fetchone())
            if current["revision"] != expected_revision:
                raise ProductConflictError("商品状态已经变化，请刷新后再操作")
            if status == current["status"]:
                return current
            updated = activate_product(current) if status == "active" else dict(current)
            updated["status"] = status
            action = {"active": "activated", "archived": "archived", "draft": "updated"}[status]
            return self._save(connection, current, updated, action=action, actor=clean_actor)

    def versions(self, product_id: str) -> list[dict[str, Any]]:
        self.get(product_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT revision, action, actor, created_at FROM product_versions WHERE product_id=? ORDER BY revision DESC",
                (product_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_asset(
        self, product_id: str, *, expected_revision: int, actor: str, file_name: str, stream: BinaryIO,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("请填写操作人")
        safe_name = Path(file_name or "").name
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ProductAssetError("仅支持 JPG、PNG、WebP、MP4、MOV 和 PDF")
        temporary = self.root / f".{uuid4().hex}.upload"
        final_path: Path | None = None
        digest = hashlib.sha256()
        size = 0
        header = b""
        try:
            with temporary.open("wb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if not header:
                        header = chunk[:32]
                    size += len(chunk)
                    if size > MAX_ASSET_BYTES:
                        raise ProductAssetError("单个商品资料不能超过 500 MB")
                    digest.update(chunk)
                    output.write(chunk)
            media_type = _detect_media_type(extension, header)
            if media_type is None:
                raise ProductAssetError("文件内容与扩展名不一致，未保存")
            checksum = digest.hexdigest()
            with self._lock, self._connect() as connection:
                current = self._profile(connection.execute("SELECT profile_json FROM products WHERE product_id=?", (product_id,)).fetchone())
                if current["revision"] != expected_revision:
                    raise ProductConflictError("商品已经被其他人修改，请刷新后再上传")
                duplicate = connection.execute(
                    "SELECT asset_id FROM product_assets WHERE product_id=? AND sha256=?", (product_id, checksum),
                ).fetchone()
                if duplicate is not None:
                    source = next(
                        item for item in current["sources"] if item.get("asset_id") == duplicate["asset_id"]
                    )
                    return current, source, True
                asset_id = f"asset_{uuid4().hex}"
                source_id = f"source_{uuid4().hex}"
                product_dir = (self.assets_root / product_id).resolve()
                if self.assets_root not in product_dir.parents:
                    raise ProductAssetError("商品资料目录不安全")
                product_dir.mkdir(parents=True, exist_ok=True)
                final_path = product_dir / f"{asset_id}{extension}"
                os.replace(temporary, final_path)
                relative = final_path.relative_to(self.root).as_posix()
                source = {
                    "id": source_id,
                    "kind": "image" if media_type.startswith("image/") else "video" if media_type.startswith("video/") else "document",
                    "label": re.sub(r"[\x00-\x1f]", "", safe_name)[:200] or f"商品资料{extension}",
                    "source_ref": f"/s4/products/{product_id}/assets/{asset_id}",
                    "asset_id": asset_id, "mime_type": media_type, "sha256": checksum,
                    "size_bytes": size, "managed": True,
                }
                updated = dict(current)
                updated["sources"] = [*current["sources"], source]
                updated = self._save(connection, current, updated, action="asset_added", actor=clean_actor)
                connection.execute(
                    "INSERT INTO product_assets VALUES(?,?,?,?,?,?,?)",
                    (asset_id, product_id, checksum, source["label"], media_type, size, relative),
                )
                return updated, source, False
        except Exception:
            # The file move happens before the SQLite transaction commits. If
            # validation, insertion, or commit fails, remove the unreferenced
            # managed file so disk state stays consistent with the database.
            if final_path is not None:
                final_path.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def asset(self, product_id: str, asset_id: str) -> tuple[Path, str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT relative_path, mime_type, file_name FROM product_assets WHERE product_id=? AND asset_id=?",
                (product_id, asset_id),
            ).fetchone()
        if row is None:
            raise ProductNotFoundError("找不到这个商品资料")
        path = (self.root / row["relative_path"]).resolve()
        if self.assets_root not in path.parents or not path.is_file():
            raise ProductNotFoundError("商品资料文件不存在")
        return path, str(row["mime_type"]), str(row["file_name"])
