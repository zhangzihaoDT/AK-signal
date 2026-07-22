"""
国金账户标的映射

职责：
  - 维护国金账户可交易标的 Universe（三态：已验证可交易 / 已验证不可交易 / 尚未验证）
  - 判断趋势 Watchlist 中的 ETF 能否通过当前账户实际交易
  - 保留未通过原因和验证记录

账户状态三态：
  VERIFIED_TRADABLE    人工在国金客户端搜索确认可交易
  VERIFIED_UNTRADABLE  人工确认不可交易
  UNVERIFIED           尚未在国金客户端验证

核心逻辑：
  actionable_watchlist = trend_watchlist ∩ VERIFIED_TRADABLE

P0-B 交付物
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.account")

ACCOUNT_STATUS = {
    "UNVERIFIED": "尚未在国金验证",
    "VERIFIED_TRADABLE": "国金可交易",
    "VERIFIED_UNTRADABLE": "国金不可交易",
}

REASON_CODES = {
    "ACCOUNT_UNSUPPORTED": "沪深账户不支持",
    "MARKET_PERMISSION_REQUIRED": "需要额外交易权限",
    "TRADING_SUSPENDED": "暂停交易",
    "NOT_FOUND_IN_BROKER": "券商客户端查不到",
    "MIN_ORDER_TOO_LARGE": "最小交易单位超出资金规模",
    "PREMIUM_TOO_HIGH": "溢价过高",
}


def load_account_universe(whitelist_path: Path) -> pd.DataFrame:
    """加载国金账户可交易标的 Universe。

    白名单由人工在国金客户端实际搜索后维护。
    CSV 需含 account_status 列，三态：
      VERIFIED_TRADABLE / VERIFIED_UNTRADABLE / UNVERIFIED

    Returns:
        DataFrame: fund_code, fund_name, exchange, account_status, verified_date, verification_method, verification_note
    """
    if not whitelist_path.exists():
        logger.warning("whitelist not found at %s", whitelist_path)
        return pd.DataFrame()

    df = pd.read_csv(whitelist_path, dtype={"fund_code": str})
    if "account_status" not in df.columns:
        if "tradable" in df.columns:
            df["account_status"] = df["tradable"].apply(
                lambda x: "VERIFIED_TRADABLE" if str(x).upper() == "TRUE" else "UNVERIFIED"
            )
        else:
            df["account_status"] = "UNVERIFIED"
    return df


def map_watchlist_to_account(
    trend_watchlist: pd.DataFrame,
    account_universe: pd.DataFrame,
) -> pd.DataFrame:
    """将趋势 Watchlist 映射到国金账户可交易池。

    计算 actionable_watchlist = trend_watchlist ∩ VERIFIED_TRADABLE。

    Args:
        trend_watchlist: 趋势关注池（至少含 fund_code）
        account_universe: 国金账户可交易 Universe

    Returns:
        DataFrame，新增字段：
        - account_status: VERIFIED_TRADABLE / VERIFIED_UNTRADABLE / UNVERIFIED
        - account_status_label: 中文说明
        - account_tradable: bool（仅 VERIFIED_TRADABLE 为 True）
    """
    if trend_watchlist.empty:
        return pd.DataFrame()

    result = trend_watchlist.copy()
    universe_codes = set(account_universe["fund_code"]) if not account_universe.empty else set()
    status_map: dict[str, str] = {}
    if not account_universe.empty:
        for _, row in account_universe.iterrows():
            status_map[row["fund_code"]] = row.get("account_status", "UNVERIFIED")

    result["in_account_universe"] = result["fund_code"].isin(universe_codes)
    result["account_status"] = result["fund_code"].map(status_map).fillna("UNVERIFIED")
    result["account_status_label"] = result["account_status"].map(ACCOUNT_STATUS).fillna("尚未在国金验证")
    result["account_tradable"] = result["account_status"] == "VERIFIED_TRADABLE"

    # Summary
    tradable = result[result["account_tradable"]]
    unverified = result[result["account_status"] == "UNVERIFIED"]
    untradable = result[result["account_status"] == "VERIFIED_UNTRADABLE"]
    logger.info(
        "account mapping: %d watchlist → %d tradable, %d unverified, %d untradable",
        len(result), len(tradable), len(unverified), len(untradable),
    )

    return result
