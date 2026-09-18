"""Command line interface for S1 contract checks."""

from __future__ import annotations

import argparse
import json

from .gold_set import read_gold_set_readiness
from .schema_registry import SCHEMA_FILES
from .validation import load_json, validate_document


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="爆款内容工厂 S1 合同校验器")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="校验一个 JSON 文件")
    validate.add_argument("--kind", required=True, choices=SCHEMA_FILES)
    validate.add_argument("--file", required=True)
    validate.add_argument("--related-product")
    validate.add_argument("--related-analysis")

    status = commands.add_parser("gold-set-status", help="查看真实金标准集进度")
    status.add_argument("--manifest", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "gold-set-status":
        readiness = read_gold_set_readiness(args.manifest)
        print(json.dumps(readiness.to_dict(), ensure_ascii=False, indent=2))
        return 0

    document = load_json(args.file)
    related = {}
    if args.related_product:
        related["product"] = load_json(args.related_product)
    if args.related_analysis:
        related["analysis"] = load_json(args.related_analysis)
    issues = validate_document(args.kind, document, related=related)
    result = {"valid": not issues, "kind": args.kind, "file": args.file, "issues": issues}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1
