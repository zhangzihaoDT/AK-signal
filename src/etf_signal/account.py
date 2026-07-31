"""
国金账户标的映射（黑名单机制）

职责：
  - 维护国金账户不可交易标的黑名单
  - 判断趋势 Watchlist 中的 ETF 能否通过当前账户实际交易
  - 默认假设全部可交易，仅在实际交易中发现无法交易后加入黑名单
  - 黑名单条目记录确认日期与原因，供实战维护追溯

账户状态（两态）：
  TRADABLE             默认可交易（不在黑名单）
  VERIFIED_UNTRADABLE  已确认不可交易（黑名单）

核心逻辑：
  actionable_watchlist = trend_watchlist - VERIFIED_UNTRADABLE

维护方式：
  实战中遇到无法交易（下单失败、券商不支持、权限不足等）→
  `python src/main.py etf account-blacklist add <code> --reason "..."` 加入黑名单
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger("etf_signal.account")

ACCOUNT_STATUS = {
    "TRADABLE": "默认可交易",
    "VERIFIED_UNTRADABLE": "国金不可交易（黑名单）",
}

REASON_CODES = {
    "ACCOUNT_UNSUPPORTED": "沪深账户不支持",
    "MARKET_PERMISSION_REQUIRED": "需要额外交易权限",
    "TRADING_SUSPENDED": "暂停交易",
    "NOT_FOUND_IN_BROKER": "券商客户端查不到",
    "MIN_ORDER_TOO_LARGE": "最小交易单位超出资金规模",
    "PREMIUM_TOO_HIGH": "溢价过高",
}

BLACKLIST_COLUMNS = [
    "fund_code", "exchange", "broker", "account_status",
    "verified_date", "verification_method", "verification_note",
]


def load_account_blacklist(blacklist_path: Path) -> pd.DataFrame:
    """加载国金账户不可交易黑名单。

    黑名单由实战中确认无法交易后维护。
    CSV 列：fund_code, exchange, broker, account_status, verified_date,
            verification_method, verification_note
    account_status 恒为 VERIFIED_UNTRADABLE。

    Returns:
        DataFrame: fund_code, exchange, broker, account_status, verified_date,
                   verification_method, verification_note
    """
    if not blacklist_path.exists():
        logger.warning("blacklist not found at %s", blacklist_path)
        return pd.DataFrame()

    df = pd.read_csv(blacklist_path, dtype={"fund_code": str})
    if "fund_code" not in df.columns:
        return pd.DataFrame()

    df["fund_code"] = df["fund_code"].astype(str).str.strip()
    df["account_status"] = "VERIFIED_UNTRADABLE"
    for col in BLACKLIST_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[BLACKLIST_COLUMNS]


def save_account_blacklist(blacklist: pd.DataFrame, blacklist_path: Path) -> Path:
    """写回黑名单 CSV（追加新条目）。"""
    blacklist_path.parent.mkdir(parents=True, exist_ok=True)
    blacklist = blacklist[BLACKLIST_COLUMNS]
    blacklist.to_csv(blacklist_path, index=False, encoding="utf-8-sig")
    return blacklist_path


def add_to_blacklist(
    fund_code: str,
    blacklist_path: Path,
    reason: str = "",
    method: str = "order_rejected",
    exchange: str = "",
    broker: str = "guojin",
) -> Path:
    """向黑名单添加一个不可交易标的（幂等）。

    Args:
        fund_code: ETF 代码
        blacklist_path: 黑名单 CSV 路径
        reason: 不可交易原因
        method: 确认方式（默认 order_rejected 实战下单失败）
        exchange: 交易所（可选）
        broker: 券商（默认 guojin）

    Returns:
        黑名单 CSV 路径
    """
    blacklist = load_account_blacklist(blacklist_path)
    if fund_code in set(blacklist["fund_code"]):
        logger.info("already in blacklist: %s", fund_code)
        return blacklist_path

    row = pd.DataFrame([{
        "fund_code": fund_code,
        "exchange": exchange,
        "broker": broker,
        "account_status": "VERIFIED_UNTRADABLE",
        "verified_date": date.today().isoformat(),
        "verification_method": method,
        "verification_note": reason,
    }])
    blacklist = pd.concat([blacklist, row], ignore_index=True)
    save_account_blacklist(blacklist, blacklist_path)
    logger.info("blacklist +%s: %s", fund_code, reason or method)
    return blacklist_path


def remove_from_blacklist(fund_code: str, blacklist_path: Path) -> Path:
    """从黑名单移除（恢复默认可交易）。"""
    blacklist = load_account_blacklist(blacklist_path)
    mask = blacklist["fund_code"] != fund_code
    removed = len(blacklist) - mask.sum()
    if removed == 0:
        logger.info("not in blacklist: %s", fund_code)
        return blacklist_path
    save_account_blacklist(blacklist[mask].reset_index(drop=True), blacklist_path)
    logger.info("blacklist -%s (%d removed)", fund_code, removed)
    return blacklist_path


def map_watchlist_to_account(
    trend_watchlist: pd.DataFrame,
    account_blacklist: pd.DataFrame,
) -> pd.DataFrame:
    """将趋势 Watchlist 映射到国金账户可交易池（黑名单机制）。

    默认全部可交易，仅黑名单中的标的标记为不可交易。

    Args:
        trend_watchlist: 趋势关注池（至少含 fund_code）
        account_blacklist: 国金账户不可交易黑名单

    Returns:
        DataFrame，新增字段：
        - account_status: TRADABLE / VERIFIED_UNTRADABLE
        - account_status_label: 中文说明
        - account_tradable: bool（仅 TRADABLE 为 True）
    """
    if trend_watchlist.empty:
        return pd.DataFrame()

    result = trend_watchlist.copy()
    blacklist_codes = set(account_blacklist["fund_code"]) if not account_blacklist.empty else set()

    result["in_account_universe"] = ~result["fund_code"].isin(blacklist_codes)
    result["account_status"] = result["fund_code"].apply(
        lambda c: "VERIFIED_UNTRADABLE" if c in blacklist_codes else "TRADABLE"
    )
    result["account_status_label"] = result["account_status"].map(ACCOUNT_STATUS).fillna("默认可交易")
    result["account_tradable"] = result["account_status"] == "TRADABLE"

    # Summary
    tradable = result[result["account_tradable"]]
    untradable = result[result["account_status"] == "VERIFIED_UNTRADABLE"]
    logger.info(
        "account mapping: %d watchlist → %d tradable (default), %d blacklisted",
        len(result), len(tradable), len(untradable),
    )

    return result
