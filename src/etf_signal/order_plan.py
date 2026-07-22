"""
国金订单计划管理

职责：
  - 从信号状态机和持仓生成每日订单计划
  - 国金订单卡格式
  - 目标仓位和价格区间计算
  - 退出线和移动止盈线管理

P0-E 交付物
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.order_plan")


@dataclass
class OrderCard:
    fund_code: str
    fund_name: str
    exchange: str
    signal_date: str
    execute_date: str
    action: str  # BUY / HOLD / REDUCE / SELL
    reference_price: float
    order_price_band: str
    target_weight: float
    exit_line: float
    trailing_stop: float
    confidence: float
    reason: str
    risk_flags: str
    broker: str = "guojin"


def generate_order_plan(
    signals: pd.DataFrame,
    positions: pd.DataFrame,
    master: pd.DataFrame,
    signal_date: str,
    execute_date: str | None = None,
) -> pd.DataFrame:
    """从信号和持仓生成订单计划。

    Args:
        signals: 信号状态 DataFrame（含 fund_code, state, confidence, reason）
        positions: 当前持仓 DataFrame
        master: ETF Master（用于获取名称和交易所）
        signal_date: 信号日期 YYYYMMDD
        execute_date: 执行日期 YYYYMMDD（默认 signal_date 次交易日）

    Returns:
        订单计划 DataFrame（订单卡格式）
    """
    if execute_date is None:
        execute_date = signal_date

    orders: list[dict[str, Any]] = []
    name_map = dict(zip(master["fund_code"], master["fund_name"]))
    exchange_map = dict(zip(master["fund_code"], master["exchange"]))

    # 当前持仓代码
    held_codes = set(positions["fund_code"].tolist()) if not positions.empty else set()

    for _, sig in signals.iterrows():
        code = sig["fund_code"]
        state = sig.get("state", "OUT_OF_POOL")
        confidence = sig.get("confidence", 0.0)
        reason = sig.get("reason", "")
        price = sig.get("reference_price", 0.0)

        action = "HOLD"
        target_weight = 0.0
        exit_line = 0.0
        trailing_stop = 0.0

        if state == "BUY_CANDIDATE" and code not in held_codes:
            action = "BUY"
            target_weight = 0.1
            exit_line = price * 0.95
            trailing_stop = price * 0.92
        elif state == "HOLD":
            action = "HOLD"
            target_weight = 0.1
            exit_line = price * 0.93
            trailing_stop = price * 0.90
        elif state == "TAKE_PROFIT_WATCH":
            action = "HOLD"
            target_weight = 0.08
            exit_line = price * 0.95
            trailing_stop = price * 0.91
        elif state == "REDUCE":
            action = "REDUCE"
            target_weight = 0.05
        elif state == "EXIT" and code in held_codes:
            action = "SELL"
            target_weight = 0.0

        orders.append({
            "fund_code": code,
            "fund_name": name_map.get(code, ""),
            "exchange": exchange_map.get(code, ""),
            "signal_date": signal_date,
            "execute_date": execute_date,
            "action": action,
            "reference_price": round(price, 4),
            "order_price_band": f"{round(price * 0.99, 4)}-{round(price * 1.01, 4)}",
            "target_weight": target_weight,
            "exit_line": round(exit_line, 4) if exit_line else 0.0,
            "trailing_stop": round(trailing_stop, 4) if trailing_stop else 0.0,
            "confidence": round(confidence, 2),
            "reason": reason,
            "risk_flags": sig.get("risk_flags", ""),
            "broker": "guojin",
        })

    return pd.DataFrame(orders)


def save_order_plan(df: pd.DataFrame, path: Path) -> Path:
    """保存订单计划到 CSV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("order plan saved: %d orders -> %s", len(df), path)
    return path


def load_execution_fills(fills_path: Path) -> pd.DataFrame:
    """加载人工成交回写记录。"""
    if fills_path.exists():
        return pd.read_csv(fills_path, dtype={"fund_code": str})
    return pd.DataFrame()
