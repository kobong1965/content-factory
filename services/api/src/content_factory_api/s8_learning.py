"""Explainable S8 comparisons based only on confirmed local metric snapshots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from content_factory_contracts import validate_or_raise

from .s8_store import now_iso


METRIC_LABELS = {
    "views": "播放量", "retention_3s": "3 秒留存率", "completion_rate": "完播率",
    "engagement_per_1000": "每千播放互动", "product_ctr": "商品点击率",
    "conversion_rate": "转化率", "orders": "订单量", "gmv_cents": "成交额",
}


def _engagement(metrics: Mapping[str, Any]) -> float | None:
    views = metrics.get("views")
    if not isinstance(views, (int, float)) or views <= 0:
        return None
    interactions = sum(float(metrics.get(field, 0)) for field in ("likes", "comments", "favorites", "shares"))
    return round(interactions * 1000 / float(views), 2)


def _value(metric: str, metrics: Mapping[str, Any]) -> float | None:
    if metric == "engagement_per_1000":
        return _engagement(metrics)
    value = metrics.get(metric)
    return float(value) if isinstance(value, (int, float)) else None


def _format_value(metric: str, value: float) -> str:
    if metric in {"retention_3s", "completion_rate", "product_ctr", "conversion_rate"}:
        return f"{value * 100:.2f}%"
    if metric == "gmv_cents":
        return f"¥{value / 100:.2f}"
    return f"{value:.2f}" if value % 1 else str(round(value))


def build_learning_report(
    *, product_id: str, publications: Sequence[Mapping[str, Any]], snapshots: Sequence[Mapping[str, Any]],
    primary_metric: str, target_window_minutes: int,
) -> dict[str, Any]:
    if primary_metric not in METRIC_LABELS:
        raise ValueError("不支持这个复盘指标")
    if target_window_minutes < 1:
        raise ValueError("观察窗口必须大于 0")
    active = [item for item in publications if item.get("product_id") == product_id and item.get("status") == "published"]
    fixture_values = {bool(item.get("fixture_data")) for item in active}
    if len(fixture_values) > 1:
        raise ValueError("复盘不能混用样例和正式作品")
    fixture_data = next(iter(fixture_values), False)
    by_publication: dict[str, list[Mapping[str, Any]]] = {}
    for snapshot in snapshots:
        by_publication.setdefault(str(snapshot.get("publication_id")), []).append(snapshot)

    comparison: list[dict[str, Any]] = []
    for publication in active:
        candidates = [
            item for item in by_publication.get(str(publication["publication_id"]), [])
            if bool(item.get("fixture_data")) == fixture_data
            and abs(int(item.get("observation_minutes", -target_window_minutes)) - target_window_minutes) / target_window_minutes <= 0.2
        ]
        if not candidates:
            continue
        snapshot = min(candidates, key=lambda item: abs(int(item["observation_minutes"]) - target_window_minutes))
        metrics = snapshot.get("metrics", {})
        metric_value = _value(primary_metric, metrics) if isinstance(metrics, Mapping) else None
        if metric_value is None:
            continue
        views = metrics.get("views") if isinstance(metrics, Mapping) else None
        comparison.append({
            "publication_id": publication["publication_id"], "snapshot_id": snapshot["snapshot_id"],
            "variant_id": publication["variant_id"], "observation_minutes": snapshot["observation_minutes"],
            "value": round(metric_value, 6), "views": views if isinstance(views, int) else None,
            "engagement_per_1000": _engagement(metrics) if isinstance(metrics, Mapping) else None,
        })

    ready = len(comparison) >= 2 and len({item["value"] for item in comparison}) > 1
    winner = max(comparison, key=lambda item: item["value"]) if ready else None
    label = METRIC_LABELS[primary_metric]
    observations: list[str] = []
    evidence: list[dict[str, str]] = []
    next_tests: list[str] = []
    limitations = ["这是同商品不同内容版本的相关性对比，不能单独证明某个镜头或话术造成了结果。"]
    if ready and winner:
        observations.append(
            f"在约 {target_window_minutes} 分钟观察窗口内，版本 {winner['variant_id']} 的{label}最高，为 {_format_value(primary_metric, winner['value'])}。"
        )
        for item in comparison:
            evidence.append({
                "publication_id": item["publication_id"], "snapshot_id": item["snapshot_id"],
                "statement": f"版本 {item['variant_id']}：{label} {_format_value(primary_metric, item['value'])}，观察 {item['observation_minutes']} 分钟。",
            })
        next_tests.append(f"下一条保持商品、账号和发布时间尽量接近，只改变一个开场变量，继续观察同一 {target_window_minutes} 分钟窗口的{label}。")
        if any(item["views"] is None or item["views"] < 1000 for item in comparison):
            limitations.append("至少一个版本播放样本低于 1000 或缺少播放量，结论稳定性有限。")
    else:
        limitations.append(f"至少需要两条同商品作品在目标窗口 ±20% 内都有{label}，且数值不能完全相同。")
        next_tests.append(f"补齐至少两条作品在约 {target_window_minutes} 分钟时的{label}快照后重新复盘。")

    report: dict[str, Any] = {
        "schema_version": "1.0.0", "fixture_data": fixture_data,
        "report_id": f"learning_report_{uuid4().hex}", "product_id": product_id,
        "status": "ready" if ready else "insufficient_data", "primary_metric": primary_metric,
        "target_window_minutes": target_window_minutes,
        "publication_ids": [item["publication_id"] for item in active], "comparison": comparison,
        "winner_publication_id": winner["publication_id"] if winner else None,
        "observations": observations, "evidence": evidence, "limitations": limitations,
        "next_tests": next_tests, "generated_at": now_iso(),
    }
    validate_or_raise("learning_report", report)
    return report
