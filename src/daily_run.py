"""Pinned-date daily batch. Existing subsystems retain all calculation policies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd

from src.common.paths import project_root, stock_metrics_dir, etf_signal_daily_dir, etf_signal_signals_dir, sw_industry_processed_dir, outputs_dir


def resolve_date(value: str = "") -> str:
    if not value:
        from src.etf_signal.cli import _default_target_date
        value = _default_target_date()
    return pd.Timestamp(value).strftime("%Y%m%d")


def steps(trade_date: str, offline: bool = False) -> list[list[str]]:
    target = ["--target-date", trade_date]
    day = ["--date", trade_date]
    acquisition = [] if offline else [["etf", "update", *target], ["industry", "update", *target]]
    return acquisition + [
        ["etf", "calculate", *day], ["industry", "calculate", *target],
        ["etf", "pipeline", *day],
        ["select", "stock-metrics", *day, *([] if offline else ["--allow-online-fetch"])],
        ["industry", "confirm", *target], ["industry", "report", *target],
        ["research", "etf-bottom", "--scan", *day],
        ["research", "etf-bottom", "--refresh-v1", *day],
        ["research", "trend-transition", "state", *day],
        ["etf", "three-lane", *day], ["select", "run", *day],
        ["opportunity-radar", "run", *day], ["run-summary", "run", *day],
        ["final-check", *day],
    ]


def check_stock_date(trade_date: str) -> None:
    path = stock_metrics_dir() / f"stock_metrics_{trade_date}.parquet"
    df = pd.read_parquet(path)
    required = {"trade_date", "source_trade_date", "data_status", "symbol", "market", "name"}
    if df.empty or not required.issubset(df.columns):
        raise ValueError("个股指标缺失或缺少日期证据，停止主题确认")
    target = pd.Timestamp(trade_date)
    bad = df[(pd.to_datetime(df["trade_date"]) != target)
             | (pd.to_datetime(df["source_trade_date"]) != target)
             | (df["data_status"] != "current")]
    for _, row in bad.drop_duplicates(["market", "symbol"]).iterrows():
        print(f"股票未对齐：{row['market']} {row['symbol']} {row['name']} "
              f"行情日={row['source_trade_date']} 状态={row['data_status']}", flush=True)
    if not bad.empty:
        raise ValueError("个股数据未完整对齐目标日，停止主题确认，避免新旧数据混用")
    audit_path = path.with_name(f"stock_update_audit_{trade_date}.json")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    benchmark_date = audit.get("benchmark_trade_date")
    if not benchmark_date or pd.Timestamp(benchmark_date) != target:
        raise ValueError(f"沪深300基准未对齐 {trade_date}（行情日={benchmark_date}），停止主题确认")


def check_partition(path: Path, trade_date: str, column: str = "trade_date") -> None:
    df = pd.read_parquet(path)
    if df.empty or column not in df or not (pd.to_datetime(df[column]) == pd.Timestamp(trade_date)).all():
        raise ValueError(f"产物日期不一致或为空：{path.name}，目标={trade_date}")


def check_step(command: list[str], trade_date: str) -> None:
    kind = tuple(command[:2])
    if kind == ("etf", "calculate"):
        check_partition(etf_signal_daily_dir() / f"rotation_{trade_date}.parquet", trade_date)
        check_partition(etf_signal_daily_dir() / f"daily_indicators_{trade_date}.parquet", trade_date, "date")
    elif kind == ("etf", "pipeline"):
        for stem in ("watchlist", "account_candidates"):
            check_partition(etf_signal_signals_dir() / f"{stem}_{trade_date}.parquet", trade_date)
    elif kind == ("select", "stock-metrics"):
        check_stock_date(trade_date)
    elif kind == ("industry", "calculate"):
        from src.sw_industry_rps import storage
        df = storage.load_metrics(sw_industry_processed_dir())
        if df.empty or not (pd.to_datetime(df["trade_date"]) == pd.Timestamp(trade_date)).any():
            raise ValueError(f"行业指标缺少目标日 {trade_date}")
    elif kind == ("industry", "confirm"):
        for stem in ("confirmation", "tier_confirmation"):
            check_partition(sw_industry_processed_dir() / f"{stem}_{trade_date}.parquet", trade_date)
    elif kind == ("etf", "three-lane"):
        check_partition(outputs_dir() / "etf_signal" / f"three_lane_{trade_date}.parquet", trade_date)
    elif kind == ("research", "trend-transition") and command[2] == "state":
        check_partition(outputs_dir() / "research" / "trend_transition"
                        / f"trend_transition_state_{trade_date}.parquet", trade_date)
    elif kind in (("select", "run"), ("opportunity-radar", "run"), ("run-summary", "run")):
        folder, stem, date_key = {
            ("select", "run"): ("selection", "tradable_candidates", "date"),
            ("opportunity-radar", "run"): ("opportunity_radar", "opportunity_radar", "trade_date"),
            ("run-summary", "run"): ("run_summary", "run_summary", "trade_date"),
        }[kind]
        payload = json.loads((outputs_dir() / folder / f"{stem}_{trade_date}.json").read_text(encoding="utf-8"))
        actual = (payload.get("meta") or payload).get(date_key)
        if actual != trade_date:
            raise ValueError(f"{stem} 日期不一致：{actual}，目标={trade_date}")


def main() -> None:
    parser = argparse.ArgumentParser(description="统一交易日的每日业务批次")
    parser.add_argument("--date", default="", help="目标交易日 YYYYMMDD；缺省最近完整交易日")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    trade_date = resolve_date(args.date)
    env = {**os.environ, "AKSIGNAL_TRADE_DATE": trade_date,
           "AKSIGNAL_OFFLINE": "1" if args.offline else "0"}
    print(f"RUN-DAY · trade_date={trade_date} · {'offline' if args.offline else 'online'}", flush=True)
    for command in steps(trade_date, args.offline):
        print(f"RUN-DAY STEP · {' '.join(command)}", flush=True)
        result = subprocess.run([sys.executable, str(project_root() / "src/main.py"), *command],
                                cwd=project_root(), env=env)
        if result.returncode:
            sys.exit(result.returncode)
        try:
            check_step(command, trade_date)
        except (ValueError, OSError, KeyError) as exc:
            print(f"Run completed with errors: {exc}", flush=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
