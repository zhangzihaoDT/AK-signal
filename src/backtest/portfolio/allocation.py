"""
仓位分配规则（Position Allocation）。

v0.6 资金规则：equal-weight / max_positions / max_weight_per_asset / deploy_ratio；
组合构建实验额外支持 score-weight（按入场分倾斜）。不做 ATR。
"""

from __future__ import annotations


def compute_position_value(
    equity: float,
    weight: float,
    max_positions: int,
    max_weight_per_asset: float,
    score: float | None = None,
    score_reference: float = 50.0,
    deploy_ratio: float = 1.0,
) -> float:
    """目标仓位价值。

    - 等权：equity × weight × deploy_ratio / max_positions
    - score-weight：等权 × (score / score_reference)（RPS15 中位 ≈ 50）
    - 上限：equity × max_weight_per_asset
    - deploy_ratio：现金比例控制（0.6 = 最多动用 60% 资金，留 40% 现金）
    """
    equal_weight = equity * weight * deploy_ratio / max_positions if max_positions > 0 else 0.0
    if score is not None and score > 0 and score_reference > 0:
        equal_weight = equal_weight * (score / score_reference)
    cap = equity * max_weight_per_asset
    return min(equal_weight, cap)


def compute_shares(alloc_value: float, price: float, slippage_pct: float) -> int:
    """按目标市值计算可买股数（整数股，含滑点）。"""
    buy_eff = price * (1.0 + slippage_pct / 100.0)
    return int(alloc_value / buy_eff)
