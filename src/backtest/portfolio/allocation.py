"""仓位分配规则（Position Allocation）。

v0.6 第一轮资金规则：equal-weight / max_positions / max_weight_per_asset。
不做 ATR；ATR 仓位留给后续轮次。
"""

from __future__ import annotations

from typing import Any


def compute_position_value(
    equity: float,
    weight: float,
    max_positions: int,
    max_weight_per_asset: float,
) -> float:
    """目标仓位价值 = min(等权分配, 单资产上限)。

    - 等权：equity × weight / max_positions
    - 上限：equity × max_weight_per_asset
    """
    equal_weight = equity * weight / max_positions if max_positions > 0 else 0.0
    cap = equity * max_weight_per_asset
    return min(equal_weight, cap)


def compute_shares(alloc_value: float, price: float, slippage_pct: float) -> int:
    """按目标市值计算可买股数（整数股，含滑点）。"""
    buy_eff = price * (1.0 + slippage_pct / 100.0)
    return int(alloc_value / buy_eff)
