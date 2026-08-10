"""Strategy Specification — typed model（frozen dataclass）。

只承载「配置事实」，不含计算逻辑。业务代码接收经过校验的不可变配置对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RULE_VERSION = "v0.8.0"


@dataclass(frozen=True)
class IndicatorSpec:
    """指标与趋势态生成阈值（Observation；Policy 参数在 EtfSelectionSpec / StockSelectionSpec / StrategySpec）。

    v0.7.0 Market Pulse：
      - rps_today_window    RPS1（Today）：最新 N 日收益横截面百分位，仅展示
      - rps_velocity_window ΔRPS15（Velocity）：RPS15 今日 − RPS15 N 个交易日前，仅展示
    两个窗口均为 Observation 展示指标，不参与排序，也不进入 Selection（Decision）。
    """
    rps_short_window: int
    rps_medium_window: int
    rps_long_window: int
    rps_today_window: int
    rps_velocity_window: int
    data_quality_max_single_day_return: float
    data_quality_flag_window: int
    ma_default_window: int
    etf_strong_threshold: float
    etf_watch_threshold: float
    confirmation_strong_threshold: float
    confirmation_observe_threshold: float
    confirmation_neutral_threshold: float
    confirmation_broad_fraction: float
    confirmation_watch_proximity: float
    tier_gate_strong: float
    tier_gate_observe: float
    tier_broad_fraction: float
    tier_strong_trend_min: float

    @property
    def rps_windows(self) -> tuple[int, int, int]:
        return (self.rps_short_window, self.rps_medium_window, self.rps_long_window)


@dataclass(frozen=True)
class AmountScoreSpec:
    method: str
    floor: float
    reference: float
    cap: float


@dataclass(frozen=True)
class EtfSelectionSpec:
    """Layer③ ETF 候选「准入—排序—输出」策略（Policy）。"""
    allowed_trend_states: tuple[str, ...]
    watch_allowed_trend_states: tuple[str, ...]
    min_amount: float
    ranking_weights: dict[str, float]           # {"rps15": 0.55, "rps20": 0.25, "amount_score": 0.20}
    amount_score: AmountScoreSpec


@dataclass(frozen=True)
class StockSelectionSpec:
    """Layer③ 个股准入 + 主题门控（Policy）。

    qualified_score / allowed_trend_states：个股趋势合格线（≥score 且 watch_level∈{S,A}）；
    theme_confirm_states：哪些 strength_level 视为主题确认（Layer③ 门控）。
    注意：strength_level 由 Layer② 用 indicators.confirmation.observe_threshold 生成
    （Observation），此处只决定「哪些状态开放主题」，不决定阈值本身。
    """
    qualified_score: float
    allowed_trend_states: tuple[str, ...]
    theme_confirm_states: tuple[str, ...]


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
