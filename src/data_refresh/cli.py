"""
Data 维护命令 — 独立于回测的在线数据刷新。

  data benchmark refresh --symbol sh000300 [--end-date YYYYMMDD]
    刷新 HS300 指数缓存（data/raw/_benchmark_sh000300.csv）。
    回测本身保持离线；此处为手动在线补数。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import raw_dir
from src.trend_engine.fetch_data import FetchConfig, fetch_hs300_daily


def refresh_hs300(end_date: str = "") -> Path:
    cfg = FetchConfig(start_date="19900101", end_date=end_date or "22220101")
    df = fetch_hs300_daily(cfg)
    if df.empty:
        raise RuntimeError("HS300 fetch returned empty")
    if end_date:
        df = df[pd.to_datetime(df["date"]) <= pd.to_datetime(end_date)]
    path = raw_dir() / "_benchmark_sh000300.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def cmd_benchmark_refresh(args: argparse.Namespace) -> int:
    if args.symbol != "sh000300":
        print(f"error: unsupported benchmark symbol: {args.symbol} (only sh000300)")
        return 2
    try:
        path = refresh_hs300(args.end_date)
    except Exception as e:
        print(f"error: refresh failed: {e}")
        return 1
    df = pd.read_csv(path, parse_dates=["date"])
    print(f"benchmark refreshed: {path}")
    print(f"  range: {df['date'].min().date()} -> {df['date'].max().date()} ({len(df)} rows)")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Data 维护命令")
    sub = p.add_subparsers(dest="command")
    p_bm = sub.add_parser("benchmark", help="基准数据维护")
    p_refresh = p_bm.add_subparsers(dest="action")
    p_r = p_refresh.add_parser("refresh", help="在线刷新基准缓存")
    p_r.add_argument("--symbol", default="sh000300", help="基准（仅支持 sh000300）")
    p_r.add_argument("--end-date", default="", help="截止日期 YYYYMMDD（默认最新）")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    command = getattr(args, "command", "")
    action = getattr(args, "action", "")
    if command == "benchmark" and action == "refresh":
        sys.exit(cmd_benchmark_refresh(args))
    else:
        build_arg_parser().print_help()
        sys.exit(2)
