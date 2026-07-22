"""
ETF 持仓管理

职责：
  - 当前持仓状态维护
  - 目标仓位计算
  - 仓位与信号联动
  - 成交回写

P0-E 交付物
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.portfolio")


@dataclass
class Position:
    fund_code: str
    fund_name: str
    quantity: int
    avg_cost: float
    current_price: float
    current_weight: float
    target_weight: float
    exit_line: float
    trailing_stop: float
    entry_date: str
    updated_at: str


def load_positions(positions_dir: Path) -> pd.DataFrame:
    """从本地加载当前持仓。"""
    path = positions_dir / "current_positions.csv"
    if path.exists():
        return pd.read_csv(path, dtype={"fund_code": str})
    logger.info("no positions file at %s", path)
    return pd.DataFrame()


def save_positions(df: pd.DataFrame, positions_dir: Path) -> Path:
    """保存持仓到本地。"""
    positions_dir.mkdir(parents=True, exist_ok=True)
    path = positions_dir / "current_positions.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("positions saved: %d holdings -> %s", len(df), path)
    return path


def reconcile_positions(
    current: pd.DataFrame,
    order_plan: pd.DataFrame,
    execution_fills: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """根据订单计划和成交回写更新持仓。

    Args:
        current: 当前持仓 DataFrame
        order_plan: 当日订单计划
        execution_fills: 实际成交回写（可选）

    Returns:
        更新后的持仓 DataFrame
    """
    if current.empty and order_plan.empty:
        return pd.DataFrame()

    updated = current.copy() if not current.empty else pd.DataFrame()

    if execution_fills is not None and not execution_fills.empty:
        for _, fill in execution_fills.iterrows():
            code = fill["fund_code"]
            action = fill["planned_action"]
            qty = fill.get("actual_quantity", 0)
            price = fill.get("actual_price", 0.0)

            if action == "BUY":
                idx = updated[updated["fund_code"] == code].index
                if not idx.empty:
                    old_qty = updated.at[idx[0], "quantity"]
                    old_cost = updated.at[idx[0], "avg_cost"]
                    new_qty = old_qty + qty
                    new_cost = (old_cost * old_qty + price * qty) / new_qty if new_qty > 0 else price
                    updated.at[idx[0], "quantity"] = new_qty
                    updated.at[idx[0], "avg_cost"] = new_cost
                else:
                    new_row = pd.DataFrame([{"fund_code": code, "quantity": qty, "avg_cost": price}])
                    updated = pd.concat([updated, new_row], ignore_index=True)
            elif action == "SELL":
                updated = updated[updated["fund_code"] != code]
            elif action == "REDUCE":
                idx = updated[updated["fund_code"] == code].index
                if not idx.empty:
                    updated.at[idx[0], "quantity"] = max(0, updated.at[idx[0], "quantity"] - qty)

    return updated
