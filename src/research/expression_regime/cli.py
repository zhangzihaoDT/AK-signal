"""
Expression Regime CLI — 表达方式制度研究。

用法：
  research expression-regime --start 20240101 --end 20260301
                            [--structure tier|industry] [--themes ai_infrastructure]
                            [--horizons 20,60,120]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from src.common.paths import outputs_dir
from . import study as es_study
from . import report as es_report


def _out_dir() -> Path:
    return outputs_dir() / "research" / "expression_regime"


def cmd(args: argparse.Namespace) -> int:
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip().isdigit())
    result = es_study.run_expression_regime(
        args.start,
        args.end,
        horizons=horizons or es_study.DEFAULT_HORIZONS,
        structure_source=args.structure,
        themes=args.themes or None,
    )
    out_dir = _out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    label = f"{args.start}_{args.end}"
    json_path = es_report.save_expression_regime_json(result, out_dir, label)
    html_path = es_report.render_expression_regime_html(result, out_dir, label)

    events = result.get("events", pd.DataFrame())
    n = len(events)
    print(f"expression regime: {n} events (structure={result.get('structure_source')}, "
          f"horizons={result.get('horizons')})")
    print(f"  json: {json_path}")
    print(f"  html: {html_path}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="v0.10 Expression Regime Event Study")
    p.add_argument("--start", required=True, help="事件区间起点 YYYYMMDD")
    p.add_argument("--end", required=True, help="事件区间终点 YYYYMMDD（含，需预留 horizon 未来数据）")
    p.add_argument("--structure", default="tier", help="结构输入源：tier（历史重放，默认）/ industry（Enrichment）")
    p.add_argument("--themes", nargs="*", default=[], help="主题过滤（默认全部）")
    p.add_argument("--horizons", default="20,60,120", help="前向收益水平（交易日）")
    p.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    sys.exit(cmd(args))
