"""Research Basket CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.common.paths import outputs_dir
from .legacy.calculator import calculate_basket
from .config import load_baskets
from .legacy.fetch import DEFAULT_SKIP_TIERS, fetch_all_basket_sources
from .legacy.report import compare_report, cross_basket_overlap, save_result
from .stage_log import evidence_stage_cn


def _out_dir() -> Path:
    return outputs_dir() / "research" / "baskets"


def _keys(raw: str) -> list[str]:
    return list(load_baskets()) if raw in ("all", "") else [x.strip() for x in raw.split(",") if x.strip()]


def cmd_fetch(args: argparse.Namespace) -> int:
    skip = {s.strip() for s in args.skip_tiers.split(",") if s.strip()} if args.skip_tiers else DEFAULT_SKIP_TIERS
    baskets: dict = {}
    if args.baskets not in ("all", ""):
        wanted = [x.strip() for x in args.baskets.split(",") if x.strip()]
        all_baskets = load_baskets()
        missing = [k for k in wanted if k not in all_baskets]
        if missing:
            print(f"error: unknown basket(s): {', '.join(missing)}")
            return 2
        baskets = {k: all_baskets[k] for k in wanted}
    if args.refresh_benchmark:
        from src.data_refresh.cli import refresh_hs300
        path = refresh_hs300()
        print(f"benchmark refreshed: {path}")
    paths = fetch_all_basket_sources(days=args.days, baskets=baskets or None, skip_tiers=skip)
    for path in paths:
        print(f"prices: {path}")
    return 0


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
        overlap = cross_basket_overlap(results)
        if not overlap.empty:
            print("cross-basket overlap (common constituents):")
            for _, row in overlap.iterrows():
                print(
                    f"  {row['symbol']} {row['name']}: {row['basket_a']}"
                    f" (stage={evidence_stage_cn(str(row['evidence_stage_a'])) or '—'}, contrib={row['contribution_pct_a']})"
                    f" ∩ {row['basket_b']} (stage={evidence_stage_cn(str(row['evidence_stage_b'])) or '—'}, contrib={row['contribution_pct_b']})"
                )
    return 0 if results else 1



def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Research Layer 主题篮子研究")
    sub = p.add_subparsers(dest="command")

    compare = sub.add_parser('compare', aliases=['compare-longbridge'], help='长桥独立收盘价快照：八个研究主题+高现金流')
    compare.add_argument('--start', required=True)
    compare.add_argument('--end', required=True)
    compare.add_argument('--snapshot', help='离线快照目录')
    compare.add_argument('--allow-online-fetch', action='store_true')
    compare.add_argument('--output', required=True)

    run = sub.add_parser("legacy-run", help="计算篮子、输出指标并生成对比报告")
    run.add_argument("--baskets", default="all", help="篮子 key，逗号分隔，默认 all")
    run.add_argument("--start", default="", help="起始日期 YYYY-MM-DD")
    run.add_argument("--end", default="", help="结束日期 YYYY-MM-DD")

    fetch = sub.add_parser("legacy-fetch", help="按篮子来源抓取行情（在线）")
    fetch.add_argument("--baskets", default="all", help="篮子 key，逗号分隔，默认 all")
    fetch.add_argument("--days", type=int, default=400, help="近 N 自然日（默认 400）")
    fetch.add_argument("--skip-tiers", default="", help="逗号分隔的 tier 键（默认主题 ETF 层）")
    fetch.add_argument("--refresh-benchmark", action="store_true", help="同时刷新 HS300 基准缓存")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.command == "legacy-run":
        sys.exit(cmd_run(args))
    if args.command == "legacy-fetch":
        sys.exit(cmd_fetch(args))
    if args.command in ('compare','compare-longbridge'):
        from .comparison import fetch_prices, build_longbridge_comparison
        from .report import render_comparison
        snapshot=fetch_prices(args.start,args.end) if args.allow_online_fetch else args.snapshot
        if not snapshot:
            raise ValueError('离线比较必须提供 --snapshot；联网需显式 --allow-online-fetch')
        results,receipt=build_longbridge_comparison(Path(snapshot),Path(args.output),args.start,args.end)
        path=render_comparison(results,receipt,Path(args.output)/'basket_compare.html')
        from src.common.paths import project_root
        from src.hub.baskets import pin_comparison
        try:
            Path(args.output).resolve().relative_to(project_root().resolve()/'.run_day/runs')
        except ValueError:
            pass
        else:
            pin_comparison(project_root(),Path(args.output))
        print(f'report: {path}')
        return
    build_arg_parser().print_help()
    sys.exit(2)
