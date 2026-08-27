"""Strategy Specification — typed model（frozen dataclass）。

只承载「配置事实」，不含计算逻辑。业务代码接收经过校验的不可变配置对象。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RULE_VERSION = "v0.9.0"


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
class LeadershipSpec:
    """② 龙头性：主题内相对地位（Policy）。

    method=theme_rank：按主题内可比分排序取相对名次。
    leader_rank_max / core_rank_max：rank≤leader_rank_max → LEADER；
    rank≤core_rank_max → CORE；其余 NON_CORE。
    require_rps_outperform=true：趋势指标未过线的候选强制 NON_CORE（保险丝，
    通常已被 trend 段挡住，仅作第二道防线上抛）。
    """
    method: str = "theme_rank"
    leader_rank_max: int = 3
    core_rank_max: int = 10
    require_rps_outperform: bool = True


@dataclass(frozen=True)
class HistoricalPositionSpec:
    """③ 历史位置：判断赔率（Policy）。

    metric=price_percentile：当前价在最近 lookback_days 交易日内的分位。
      ≤low_max → LOW；≤mid_max → MID；>mid_max → HIGH。
    metric=ma60_deviation：乖离率 = (现价/MA60 − 1)×100（现价相对 60 日线的偏离百分比）。
      现价低于 MA60 超 breakdown_pct → BREAKDOWN（中期趋势破坏，禁止买入）；
      低于 MA60 超 low_below_pct（且未破位）→ LOW（深度回调，赔率区）；
      高于 MA60 超 high_above_pct → HIGH（追高）；中间 → MID。
      数据不足 → UNKNOWN（信号按中性 MID 匹配）。
    纪律：历史低位只提高赔率，不产生趋势（趋势成立仍是前置条件）；高位（追高）与破位都不买。
    """
    enabled: bool = True
    lookback_days: int = 756
    metric: str = "price_percentile"
    low_max: float = 30.0
    mid_max: float = 70.0
    ma_window: int = 60
    breakdown_pct: float = -15.0
    low_below_pct: float = -5.0
    high_above_pct: float = 10.0


@dataclass(frozen=True)
class SignalRule:
    """④ 信号规则：条件全部命中（缺省=通配）则输出 signal；顺序匹配、先命中先生效。"""
    signal: str
    trend: str | None = None          # QUALIFIED / NOT_QUALIFIED
    leadership: str | None = None     # LEADER / CORE / NON_CORE
    position: str | None = None       # LOW / MID / HIGH（UNKNOWN 按中性 MID 匹配）


@dataclass(frozen=True)
class SignalPolicySpec:
    """④ 最终信号（Policy）：规则顺序匹配；未命中 → fallback_signal。"""
    rules: tuple[SignalRule, ...]
    fallback_signal: str = "WAIT"


@dataclass(frozen=True)
class EtfSelectionSpec:
    """Layer③ ETF 候选「准入—排序—输出」策略（Policy，v0.9.0 四段）。"""
    allowed_trend_states: tuple[str, ...]
    watch_allowed_trend_states: tuple[str, ...]
    min_amount: float
    ranking_weights: dict[str, float]           # {"rps15": 0.55, "rps20": 0.25, "amount_score": 0.20}
    amount_score: AmountScoreSpec
    leadership: LeadershipSpec = field(default_factory=LeadershipSpec)
    historical_position: HistoricalPositionSpec = field(default_factory=HistoricalPositionSpec)


@dataclass(frozen=True)
class StockSelectionSpec:
    """Layer③ 个股准入 + 主题门控 + 四段信号（Policy）。

    qualified_score / allowed_trend_states：个股趋势合格线（≥score 且 watch_level∈{S,A}）；
    theme_confirm_states：哪些 strength_level 视为主题确认（Layer③ 前置 Gate）。
    注意：strength_level 由 Layer② 用 indicators.confirmation.observe_threshold 生成
    （Observation），此处只决定「哪些状态开放主题」，不决定阈值本身。

    v0.9.0 四段：trend（①）→ leadership（②）→ historical_position（③）→ signal_policy（④）。
    """
    qualified_score: float
    allowed_trend_states: tuple[str, ...]
    theme_confirm_states: tuple[str, ...]
    rps15_min: float = 80.0
    leadership: LeadershipSpec = field(default_factory=LeadershipSpec)
    historical_position: HistoricalPositionSpec = field(default_factory=HistoricalPositionSpec)
    signal_policy: SignalPolicySpec = field(default_factory=lambda: SignalPolicySpec(rules=(), fallback_signal="WAIT"))


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
