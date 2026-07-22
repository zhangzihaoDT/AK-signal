"""
趋势动物 Pro 外部验证层

职责：
  - 维护类别映射表（AKsignal ETF 类别 → 趋势动物 Pro 类别名）
  - 调用趋势动物 Pro 数据源获取外部验证
  - 验证矩阵：双模型共振 / 分歧标识
  - 置信度修正

P0-D 交付物
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.trend_animal")

# 类别映射（AKsignal ETF 分类 → 趋势动物 Pro 类别名）
CATEGORY_MAP: dict[str, list[str]] = {
    "cn_equity.industry": ["行业", "板块"],
    "cn_equity.broad_market": ["宽基", "大盘"],
    "cn_equity.theme": ["主题", "概念"],
    "cn_equity.factor_style": ["风格"],
    "hk_overseas_equity": ["港股", "海外"],
    "commodity": ["商品"],
    "bond": ["债券"],
    "cash": ["货币"],
}


@dataclass
class TrendAnimalResult:
    category_name: str
    trend_strength_local: float
    trend_strength_global: float
    strength_change: float
    temperature: float
    is_right_side: bool
    as_of_date: str
    validation_status: str = "available"


@dataclass
class ValidationMatrix:
    aksignal_state: str
    animal_state: str  # "strong" | "weak" | "unavailable"
    interpretation: str
    confidence_modifier: float  # -0.2 ~ +0.2


def load_category_map() -> dict[str, list[str]]:
    """加载类别映射表。"""
    return CATEGORY_MAP


def validate_state(
    aksignal_state: str,
    aksignal_rps: float,
    animal_result: TrendAnimalResult | None,
) -> ValidationMatrix:
    """执行外部验证，输出验证矩阵。

    Args:
        aksignal_state: AKsignal 信号主状态
        aksignal_rps: AKsignal RPS 值
        animal_result: 趋势动物 Pro 验证结果

    Returns:
        ValidationMatrix 含置信度修正
    """
    if animal_result is None or animal_result.validation_status == "unavailable":
        return ValidationMatrix(
            aksignal_state=aksignal_state,
            animal_state="unavailable",
            interpretation="外部验证不可用，以 AKsignal 主信号为准",
            confidence_modifier=0.0,
        )

    # 判定外部强弱
    is_animal_strong = (
        animal_result.trend_strength_local >= 60
        and animal_result.is_right_side
    )

    is_ak_strong = aksignal_rps >= 80 and aksignal_state in ("HOLD", "BUY_CANDIDATE")

    if is_ak_strong and is_animal_strong:
        return ValidationMatrix(
            aksignal_state=aksignal_state,
            animal_state="strong",
            interpretation="双模型共振",
            confidence_modifier=+0.2,
        )

    if is_ak_strong and not is_animal_strong:
        return ValidationMatrix(
            aksignal_state=aksignal_state,
            animal_state="weak",
            interpretation="可能为早期启动或假突破",
            confidence_modifier=-0.1,
        )

    if not is_ak_strong and is_animal_strong:
        return ValidationMatrix(
            aksignal_state=aksignal_state,
            animal_state="weak",
            interpretation="外部类别指标可能滞后，不追高",
            confidence_modifier=-0.05,
        )

    return ValidationMatrix(
        aksignal_state=aksignal_state,
        animal_state="weak",
        interpretation="无有效趋势",
        confidence_modifier=0.0,
    )
