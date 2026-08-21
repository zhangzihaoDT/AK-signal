"""Research Basket CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.common.paths import outputs_dir
from .calculator import calculate_basket
from .config import load_baskets
from .report import compare_report, cross_basket_overlap, save_result


def _out_dir() -> Path:
    return outputs_dir() / "research" / "baskets"


def _keys(raw: str) -> list[str]:
    return list(load_baskets()) if raw in ("all", "") else [x.strip() for x in raw.split(",") if x.strip()]


def cmd_run(args: argparse.Namespace) -> int:
    results = []
    for key in _keys(args.baskets):
        try:
            result = calculate_basket(key, start_date=args.start, end_date=args.end)
            save_result(result, _out_dir())
            results.append(result)
            print(json.dumps({"basket": key, **result["metrics"]}, ensure_ascii=False))
        except Exception as exc:
            print(f"error: {key}: {exc}")
            return 1
    if results:
        path = compare_report(results, _out_dir())
        print(f"report: {path}")
        quarterly_path = compare_report(
            results, _out_dir(), filename="basket_quarterly_compare.html", nav_field="quarterly_nav")
        print(f"quarterly report: {quarterly_path}")
        overlap = cross_basket_overlap(results)
        if not overlap.empty:
            print("cross-basket overlap (common constituents):")
            for _, row in overlap.iterrows():
                print(
                    f"  {row['symbol']} {row['name']}: {row['basket_a']}"
                    f" (stage={row['evidence_stage_a'] or '—'}, contrib={row['contribution_pct_a']})"
                    f" ∩ {row['basket_b']} (stage={row['evidence_stage_b'] or '—'}, contrib={row['contribution_pct_b']})"
                )
    return 0 if results else 1


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Research Layer 主题篮子研究")
    sub = p.add_subparsers(dest="command")
    run = sub.add_parser("run", help="计算篮子、输出指标并生成对比报告")
    run.add_argument("--baskets", default="all", help="篮子 key，逗号分隔，默认 all")
    run.add_argument("--start", default="", help="起始日期 YYYY-MM-DD")
    run.add_argument("--end", default="", help="结束日期 YYYY-MM-DD")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.command == "run":
        sys.exit(cmd_run(args))
    build_arg_parser().print_help()
    sys.exit(2)
