"""Frozen spec loader for Leader Breakout Event V1.

业务代码不直接读 YAML；统一经此 loader 拿到校验过的不可变 spec。
冻结纪律：本模块只读 `config/research/leader_breakout_v1.yaml`，绝不写回。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.common.paths import config_dir

from . import RULE_ID, SPEC_SOURCE

FROZEN_STATUS = "FROZEN_RESEARCH_HYPOTHESIS"


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class EventSpec:
    themes: tuple[str, ...]
    asset_type: str
    exclude_participation: tuple[str, ...]
    rank_trend: int
    require_rs20_positive: bool
    score_threshold: float
    cooldown_reset_required: bool
    cooldown_trading_days: int


@dataclass(frozen=True)
class ThemeStateSpec:
    method: str
    confirm_states: tuple[str, ...]
    confirm_lookahead_days: int


@dataclass(frozen=True)
class TagSpec:
    ma60_tag: str
    ma60_cross_window: int
    ma120_tag: str
    high_tag: str
    drawdown_buckets: tuple[float, ...]
    breadth_score_threshold: float


@dataclass(frozen=True)
class ReturnSpec:
    entry: str
    horizons: tuple[int, ...]
    primary_horizon: int
    benchmark: str


@dataclass(frozen=True)
class PortfolioSpec:
    sleeve_weights: tuple[float, ...]
    primary_weight: float
    early_exit: str
    wait_exit: str
    cost_bps: float


@dataclass(frozen=True)
class GatesSpec:
    n_boot: int
    seed: int
    ci: float
    min_positive_years: int
    max_median_mae: float
    min_win_rate: float
    max_single_stock_share: float
    min_sharpe: float
    thresholds: tuple[float, ...]
    cooldowns: tuple[int, ...]


@dataclass(frozen=True)
class LeaderBreakoutSpec:
    rule_id: str
    status: str
    frozen_at: str
    event: EventSpec
    theme_state: ThemeStateSpec
    tags: TagSpec
    returns: ReturnSpec
    portfolio: PortfolioSpec
    gates: GatesSpec
    provenance: dict[str, Any] = field(default_factory=dict)


def _require(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] is None:
        raise SpecError(f"leader_breakout spec: {where}.{key} required")
    return d[key]


def load_spec(path: Path | None = None) -> LeaderBreakoutSpec:
    p = path or (config_dir() / "research" / "leader_breakout_v1.yaml")
    if not p.exists():
        raise SpecError(f"leader_breakout spec not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    if str(raw.get("rule_id")) != RULE_ID:
        raise SpecError(f"leader_breakout spec: rule_id must be {RULE_ID}")
    if str(raw.get("status")) != FROZEN_STATUS:
        raise SpecError(f"leader_breakout spec: status must be {FROZEN_STATUS}")

    ev = _require(raw, "event", "root")
    ts = _require(raw, "theme_state", "root")
    tg = _require(raw, "tags", "root")
    rt = _require(raw, "returns", "root")
    pf = _require(raw, "portfolio", "root")
    gt = _require(raw, "gates", "root")

    event = EventSpec(
        themes=tuple(ev.get("themes") or ()),
        asset_type=str(ev.get("asset_type", "stock")),
        exclude_participation=tuple(ev.get("exclude_participation") or ()),
        rank_trend=int(ev.get("rank_trend", 1)),
        require_rs20_positive=bool(ev.get("require_rs20_positive", True)),
        score_threshold=float(ev.get("score_threshold", 70)),
        cooldown_reset_required=bool(ev.get("cooldown_reset_required", True)),
        cooldown_trading_days=int(ev.get("cooldown_trading_days", 20)),
    )
    theme_state = ThemeStateSpec(
        method=str(ts.get("method", "industry_gate_reconstruction")),
        confirm_states=tuple(ts.get("confirm_states") or ("观察", "强势")),
        confirm_lookahead_days=int(ts.get("confirm_lookahead_days", 120)),
    )
    tags = TagSpec(
        ma60_tag=str(tg.get("ma60_tag", "above_ma60")),
        ma60_cross_window=int(tg.get("ma60_cross_window", 10)),
        ma120_tag=str(tg.get("ma120_tag", "above_ma120")),
        high_tag=str(tg.get("high_tag", "new_high_20d")),
        drawdown_buckets=tuple(float(x) for x in (tg.get("drawdown_buckets") or (0.10, 0.20, 0.30))),
        breadth_score_threshold=float(tg.get("breadth_score_threshold", 70)),
    )
    returns = ReturnSpec(
        entry=str(rt.get("entry", "next_open")),
        horizons=tuple(int(x) for x in (rt.get("horizons") or (5, 10, 20, 60))),
        primary_horizon=int(rt.get("primary_horizon", 20)),
        benchmark=str(rt.get("benchmark", "theme_stock_median")),
    )
    portfolio = PortfolioSpec(
        sleeve_weights=tuple(float(x) for x in (pf.get("sleeve_weights") or (0.25, 0.33, 0.5))),
        primary_weight=float(pf.get("primary_weight", 0.33)),
        early_exit=str(pf.get("early_exit", "fixed_20")),
        wait_exit=str(pf.get("wait_exit", "fixed_20")),
        cost_bps=float(pf.get("cost_bps", 20)),
    )
    l1 = gt.get("L1_bootstrap", {})
    l2 = gt.get("L2_years", {})
    l3 = gt.get("L3_risk", {})
    l4 = gt.get("L4_portfolio", {})
    l5 = gt.get("L5_sensitivity", {})
    gates = GatesSpec(
        n_boot=int(l1.get("n_boot", 2000)),
        seed=int(l1.get("seed", 42)),
        ci=float(l1.get("ci", 0.95)),
        min_positive_years=int(l2.get("min_positive_years", 2)),
        max_median_mae=float(l3.get("max_median_mae", -0.20)),
        min_win_rate=float(l3.get("min_win_rate", 0.45)),
        max_single_stock_share=float(l3.get("max_single_stock_share", 0.40)),
        min_sharpe=float(l4.get("min_sharpe", 0.30)),
        thresholds=tuple(float(x) for x in (l5.get("thresholds") or (65, 70, 75))),
        cooldowns=tuple(int(x) for x in (l5.get("cooldowns") or (10, 20, 30))),
    )

    if not event.themes:
        raise SpecError("leader_breakout spec: event.themes must be non-empty")
    if event.score_threshold <= 0:
        raise SpecError("leader_breakout spec: event.score_threshold must be > 0")

    return LeaderBreakoutSpec(
        rule_id=RULE_ID, status=FROZEN_STATUS, frozen_at=str(raw.get("frozen_at", "")),
        event=event, theme_state=theme_state, tags=tags, returns=returns,
        portfolio=portfolio, gates=gates,
        provenance=dict(raw.get("provenance") or {}),
    )


def spec_source() -> str:
    return SPEC_SOURCE
