"""
Event Study CLI — 状态转换事件的前向收益研究。

用法：
  research event-study --signals outputs/research/historical_signals_{start}_{end}.parquet
                       [--start --end] [--layers 123] [--horizons 5,10,20,60]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from src.common.paths import outputs_dir
from . import study as es_study
from . import report as es_report


def _signals_dir() -> Path:
    return outputs_dir() / "research"


def _latest_signals() -> Path | None:
    files = sorted(_signals_dir().glob("historical_signals_*.parquet"))
    if not files:
        return None
    # 取区间文件（不含单日期 *_date.parquet）优先；回退最新
    ranges = [f for f in files if "_" in f.stem.removeprefix("historical_signals_")]
    return (ranges or files)[-1]


def _load_signals(path: str | None) -> tuple[pd.DataFrame, str]:
    if path:
        p = Path(path)
    else:
        p = _latest_signals()
    if p is None or not p.exists():
        print(f"error: signals file not found: {p}")
        sys.exit(2)
    return pd.read_parquet(p), p.stem.removeprefix("historical_signals_")


def cmd_event_study(args: argparse.Namespace) -> int:
    signals, label = _load_signals(args.signals)
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip().isdigit())
    result = es_study.run_event_study(
        signals,
        layers=args.layers,
        horizons=horizons or es_study.DEFAULT_HORIZONS,
        start_date=args.start,
        end_date=args.end,
    )
    out_dir = _signals_dir() / "event_study"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = es_report.save_event_study_json(result, out_dir, label)
    html_path = es_report.render_event_study_html(result, out_dir, label)

    summary = result.get("summary", pd.DataFrame())
    n_events = len(result.get("events", pd.DataFrame()))
    print(f"event study: {n_events} events, {len(summary)} summary rows")
    print(f"  json: {json_path}")
    print(f"  html: {html_path}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="v0.5.1 Event Study")
    p.add_argument("--signals", default="", help="historical_signals parquet 路径（默认 outputs/research 最新）")
    p.add_argument("--start", default="", help="事件发生日起点 YYYYMMDD")
    p.add_argument("--end", default="", help="事件发生日终点 YYYYMMDD")
    p.add_argument("--layers", default="123", help="事件层位（默认 123）")
    p.add_argument("--horizons", default="5,10,20,60", help="前向收益水平（交易日）")
    p.add_argument("--log-level", default="INFO")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    sys.exit(cmd_event_study(args))
