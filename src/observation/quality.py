"""Read-only acquisition-quality checks consumed by the formal Observation report."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from src.common.paths import etf_signal_diagnostics_dir, stock_metrics_dir
from src.market_facts.policy import aligned_stock_rows


def data_quality_warnings(trade_date: str) -> list[dict[str, Any]]:
    """Read target-date acquisition evidence; never report a missing audit as complete."""
    out: list[dict[str, Any]] = []
    etf_path = etf_signal_diagnostics_dir() / f"source_audit_{trade_date}.json"
    if etf_path.exists():
        audit = json.loads(etf_path.read_text(encoding="utf-8"))
        codes = audit.get("failed_codes") or []
        if codes:
            out.append({"category": "etf_data", "message":
                        f"ETF 未更新到 {trade_date}：{'、'.join(codes)}（{len(codes)} 只）"})
    else:
        out.append({"category": "etf_data", "message": f"ETF 抓取审计缺失：{trade_date}，完整性未知"})

    path = stock_metrics_dir() / f"stock_metrics_{trade_date}.parquet"
    audit_path = path.with_name(f"stock_update_audit_{trade_date}.json")
    if audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        benchmark_date = audit.get("benchmark_trade_date")
        if not benchmark_date or pd.Timestamp(benchmark_date) != pd.Timestamp(trade_date):
            out.append({"category": "benchmark_data", "message":
                        f"沪深300基准未更新到 {trade_date}（行情日={benchmark_date or '无'}）"})
    if path.exists():
        df = pd.read_parquet(path)
        if not df.empty and {"source_trade_date", "data_status", "market", "symbol", "name"}.issubset(df):
            bad = df[~aligned_stock_rows(df, trade_date)].drop_duplicates(["market", "symbol"])
            for _, row in bad.iterrows():
                out.append({"category": "stock_data", "message":
                            f"股票未更新到 {trade_date}：{row['market']} {row['symbol']} {row['name']} "
                            f"（{row['data_status']}，行情日={row['source_trade_date']}）"})
        else:
            out.append({"category": "stock_data", "message": "个股指标无日期证据或为空，完整性未知"})
    else:
        out.append({"category": "stock_data", "message": f"个股指标缺失：{trade_date}"})
    return out
