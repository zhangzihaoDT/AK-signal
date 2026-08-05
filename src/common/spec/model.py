"""Strategy Specification — typed model（frozen dataclass）。

只承载「配置事实」，不含计算逻辑。业务代码接收经过校验的不可变配置对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RULE_VERSION = "v0.6.1"


@dataclass(frozen=True)
class IndicatorSpec:
    """指标与信号门限（参数进 config，算法留代码）。"""
    rps_short_window: int
    rps_medium_window: int
    rps_long_window: int
    ma_default_window: int
    etf_strong_threshold: float
    etf_watch_threshold: float
    etf_gate_states: tuple[str, ...]
    etf_watch_gate_states: tuple[str, ...]
    etf_min_amount: float
    stock_qualified_score: float
    stock_allowed_trend_states: tuple[str, ...]
    confirmation_strong_threshold: float
    confirmation_observe_threshold: float
    confirmation_neutral_threshold: float

    @property
    def rps_windows(self) -> tuple[int, int, int]:
        return (self.rps_short_window, self.rps_medium_window, self.rps_long_window)


@dataclass(frozen=True)
class EntrySpec:
    policy: str
    rps15_min: float
    trend_score_min: float
    allowed_trend_states: tuple[str, ...] = ("S", "A")


@dataclass(frozen=True)
class ExitSpec:
    policy: str
    horizon: int | None = None
    ma_window: int | None = None


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    label: str
    theme: str
    universe_mode: str
    entry: EntrySpec
    exit: ExitSpec
    weight: float = 1.0


@dataclass(frozen=True)
class ExecutionSpec:
    model: str
    fee_bps: float
    slippage_bps: float
    no_leverage: bool
    no_pyramiding: bool

    @property
    def fee_pct(self) -> float:
        return self.fee_bps / 100.0

    @property
    def slippage_pct(self) -> float:
        return self.slippage_bps / 100.0


@dataclass(frozen=True)
class AllocationSpec:
    weights: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioSpec:
    initial_capital: float
    max_positions: int
    position_sizing: str
    max_weight_per_asset: float
    deploy_ratio: float
    allocations: dict[str, AllocationSpec] = field(default_factory=dict)
