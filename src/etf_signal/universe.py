"""
ETF Universe 质量门控

职责：
  - U0 → U1：数据质量门控（唯一、完整、可识别）
  - U1 → U2：交易所有效性门控（场内 ETF、正常交易）
  - 不做账户映射（由 account.py 负责）
  - 不做趋势筛选（由 signal.py 负责）

P0-B 交付物
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger("etf_signal.universe")

# 默认流动性阈值
DEFAULT_GATES = {
    "liquidity": {"min_avg_amount_20d": 10_000_000},
    "fund_size": {"min_fund_size": 100_000_000},
    "history": {"min_listed_days": 120, "min_valid_history_days": 60},
    "premium_risk": {"max_premium": 0.05},
}


def filter_u0_u1(master: pd.DataFrame) -> pd.DataFrame:
    """U0 → U1：数据基础质量门控。

    通过条件：
    - 基金代码唯一且不为空
    - 基金名称不为空
    - 交易所可识别（SSE / SZSE）
    - 正常交易状态
    """
    if master.empty:
        return master
    df = master.copy()
    df = df.drop_duplicates(subset=["fund_code"])
    df = df[df["fund_code"].notna() & (df["fund_code"] != "")]
    df = df[df["fund_name"].notna() & (df["fund_name"] != "")]
    df = df[df["exchange"].isin(["SSE", "SZSE"])]
    if "is_active" in df.columns:
        df = df[df["is_active"] == True]
    logger.info("U0(%d) → U1(%d): passed data quality gates", len(master), len(df))
    return df.reset_index(drop=True)


def filter_u1_u2(master: pd.DataFrame) -> pd.DataFrame:
    """U1 → U2：交易所有效性门控。

    通过条件：
    - 确认为沪深场内 ETF
    - 排除已知非 ETF 类型
    """
    if master.empty:
        return master
    df = master.copy()

    if "fund_type" in df.columns:
        df = df[df["fund_type"].str.contains("ETF", na=False) | (df["fund_type"] == "")]

    logger.info("U1(%d) → U2(%d): passed exchange validity gates", len(master), len(df))
    return df.reset_index(drop=True)


def apply_liquidity_gate(
    master: pd.DataFrame,
    daily_data: dict[str, pd.DataFrame],
    min_amount: float = 10_000_000,
) -> pd.DataFrame:
    """流动性门控：20 日均成交额 >= 阈值。"""
    if master.empty:
        return master
    passed: list[str] = []
    for code in master["fund_code"]:
        hist = daily_data.get(code)
        if hist is None or hist.empty:
            continue
        amount_col = next((c for c in hist.columns if "amount" in c.lower() or "成交额" in c), None)
        if amount_col:
            avg = hist[amount_col].tail(20).mean()
            if pd.notna(avg) and avg >= min_amount:
                passed.append(code)
    result = master[master["fund_code"].isin(passed)].reset_index(drop=True)
    logger.info("liquidity gate: %d / %d passed", len(result), len(master))
    return result


def apply_size_gate(master: pd.DataFrame, min_size: float = 100_000_000) -> pd.DataFrame:
    """规模门控：基金规模 >= 阈值。"""
    if master.empty or "fund_size" not in master.columns:
        return master
    result = master[master["fund_size"].fillna(0) >= min_size].reset_index(drop=True)
    logger.info("size gate: %d / %d passed", len(result), len(master))
    return result


def apply_history_gate(
    master: pd.DataFrame,
    daily_data: dict[str, pd.DataFrame],
    min_days: int = 120,
) -> pd.DataFrame:
    """历史长度门控：有足够行情用于趋势计算。"""
    if master.empty:
        return master
    passed: list[str] = []
    for code in master["fund_code"]:
        hist = daily_data.get(code)
        if hist is not None and not hist.empty and len(hist) >= min_days:
            passed.append(code)
    result = master[master["fund_code"].isin(passed)].reset_index(drop=True)
    logger.info("history gate: %d / %d passed", len(result), len(master))
    return result


def apply_premium_gate(
    master: pd.DataFrame,
    daily_data: dict[str, pd.DataFrame],
    max_premium: float = 0.05,
) -> pd.DataFrame:
    """溢价风险门控：折溢价不超过阈值。"""
    if master.empty:
        return master
    failed: list[str] = []
    for _, row in master.iterrows():
        code = row["fund_code"]
        hist = daily_data.get(code)
        if hist is None or hist.empty:
            continue
        premium_col = next((c for c in hist.columns if "溢价" in c or "折溢" in c), None)
        if premium_col:
            latest = hist[premium_col].iloc[-1]
            if pd.notna(latest) and abs(latest) > max_premium:
                failed.append(code)
    result = master[~master["fund_code"].isin(failed)].reset_index(drop=True)
    logger.info("premium gate: %d / %d passed", len(result), len(master))
    return result
