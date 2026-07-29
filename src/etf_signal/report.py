"""
ETF 日报生成

职责：
  - 市场热度地图报告
  - 国金候选列表报告
  - 持仓与订单计划报告
  - HTML / CSV / Markdown 格式支持

P0-E 交付物
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.report")


def build_heat_report(
    heat_map: pd.DataFrame,
    report_date: str,
) -> str:
    """生成市场热度地图 Markdown 报告。"""
    if heat_map.empty:
        return f"# ETF 市场热度地图 — {report_date}\n\n无数据\n"

    lines = [f"# ETF 市场热度地图 — {report_date}\n"]

    lines.append("\n## 全市场热度分布\n")
    lines.append("| 资产大类 | 资产桶 | ETF 数量 | 强势占比 | RPS 中位数 | 热度变化 | 状态描述 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for _, row in heat_map.iterrows():
        lines.append(
            f"| {row.get('asset_class', '')} | {row.get('bucket_label', row.get('asset_bucket', ''))} | "
            f"{row['etf_count']} | {row['strong_ratio']:.1%} | "
            f"{row['median_rps']:.1f} | {row.get('heat_change', '')} | "
            f"{row.get('description', '')} |"
        )

    return "\n".join(lines)


def build_candidate_report(
    candidates: pd.DataFrame,
    report_date: str,
) -> str:
    """生成国金候选列表 Markdown 报告。"""
    if candidates.empty:
        return f"# 国金候选列表 — {report_date}\n\n无候选\n"

    active = candidates[candidates["trend_state"] != "OUT_OF_SCOPE"]
    if active.empty:
        return f"# 国金候选列表 — {report_date}\n\n无非活跃候选\n"

    lines = [f"# 国金候选列表 — {report_date}\n"]
    lines.append(f"\n### 活跃趋势候选（{len(active)} 只）\n")
    lines.append("| 代码 | 名称 | 趋势状态 | RPS15 | RPS60 | 5日收益 | 20日收益 | 趋势变化 | 账户状态 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for _, row in active.iterrows():
        lines.append(
            f"| {row['fund_code']} | {row['fund_name']} | {row['trend_state']} | "
            f"{row['rps15']:.1f} | {row['rps60']:.1f} | "
            f"{row['return_5d']:+.2f}% | {row['return_20d']:+.2f}% | "
            f"{row.get('trend_change', '')} | {row.get('account_status_label', '')} |"
        )

    return "\n".join(lines)


def build_order_report(
    order_plan: pd.DataFrame,
    report_date: str,
) -> str:
    """生成订单计划 Markdown 报告。"""
    if order_plan.empty:
        return f"# 订单计划 — {report_date}\n\n无订单\n"

    lines = [f"# 订单计划 — {report_date}\n"]
    lines.append("| 代码 | 名称 | 动作 | 参考价 | 目标仓位 | 退出线 | 原因 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for _, row in order_plan.iterrows():
        lines.append(
            f"| {row['fund_code']} | {row['fund_name']} | {row['action']} | "
            f"{row['reference_price']:.4f} | {row['target_weight']:.2%} | "
            f"{row['exit_line']:.4f} | {row['reason']} |"
        )

    return "\n".join(lines)


def write_daily_reports(
    heat_map: pd.DataFrame,
    candidates: pd.DataFrame,
    order_plan: pd.DataFrame,
    reports_dir: Path,
    report_date: str,
) -> dict[str, Path]:
    """写出一组每日日报文件。

    Args:
        heat_map: 热度地图 DataFrame
        candidates: 候选列表 DataFrame
        order_plan: 订单计划 DataFrame
        reports_dir: 报告目录（outputs/etf_signal/reports/）
        report_date: 报告日期 YYYYMMDD

    Returns:
        {report_type: file_path} 的字典
    """
    reports_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}

    heat_md = reports_dir / f"{report_date}_etf_market_heat.md"
    heat_md.write_text(build_heat_report(heat_map, report_date), encoding="utf-8")
    paths["heat"] = heat_md

    cand_md = reports_dir / f"{report_date}_etf_candidates.md"
    cand_md.write_text(build_candidate_report(candidates, report_date), encoding="utf-8")
    paths["candidates"] = cand_md

    order_csv = reports_dir / f"{report_date}_order_plan.csv"
    if not order_plan.empty:
        order_plan.to_csv(order_csv, index=False, encoding="utf-8-sig")
        paths["order_plan"] = order_csv

    return paths
