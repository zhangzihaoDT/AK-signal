"""Strategy Specification — 统一 Loader。

业务代码不直接读 YAML；通过本模块获取经过校验的 typed config。
生产路径无隐藏默认值：缺失关键参数直接报错。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src.common.paths import config_dir
from . import schema as sch
from .model import (
    AllocationSpec, AmountScoreSpec, EntrySpec, EtfSelectionSpec, ExecutionSpec,
    ExitSpec, IndicatorSpec, PortfolioSpec, StockSelectionSpec, StrategySpec,
)


def _read_yaml(rel: str) -> dict[str, Any]:
    path = config_dir() / rel
    if not path.exists():
        raise FileNotFoundError(f"config/{rel} missing")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _themes_keys() -> set[str]:
    from src.common import themes as themes_cfg
    return set(themes_cfg.load_themes())


@lru_cache(maxsize=None)
def load_indicator_spec() -> IndicatorSpec:
    cfg = _read_yaml("indicators.yaml")
    sch.validate_indicators(cfg)
    rps, ma = cfg["rps"], cfg["moving_average"]
    gates, conf = cfg["signal_gates"], cfg["confirmation"]
    dq = cfg.get("data_quality", {})
    etf = gates["etf"]
    return IndicatorSpec(
        rps_short_window=int(rps["short_window"]),
        rps_medium_window=int(rps["medium_window"]),
        rps_long_window=int(rps["long_window"]),
        rps_today_window=int(rps["today_window"]),
        rps_velocity_window=int(rps["velocity_window"]),
        data_quality_max_single_day_return=float(dq["max_single_day_return"]),
        data_quality_flag_window=int(dq["flag_window"]),
        ma_default_window=int(ma["default_window"]),
        etf_strong_threshold=float(etf["strong_threshold"]),
        etf_watch_threshold=float(etf["watch_threshold"]),
        confirmation_strong_threshold=float(conf["strong_threshold"]),
        confirmation_observe_threshold=float(conf["observe_threshold"]),
        confirmation_neutral_threshold=float(conf["neutral_threshold"]),
        confirmation_broad_fraction=float(conf["broad_fraction"]),
        confirmation_watch_proximity=float(conf["watch_proximity"]),
    )


@lru_cache(maxsize=None)
def load_etf_selection_spec() -> EtfSelectionSpec:
    """Layer③ ETF 候选策略（准入门限 + 排序权重 + amount_score 口径）。"""
    cfg = _read_yaml("strategies.yaml")
    sch.validate_etf_selection(cfg)
    es = cfg["etf_selection"]
    amt = es["ranking"]["amount_score"]
    return EtfSelectionSpec(
        allowed_trend_states=tuple(es["allowed_trend_states"]),
        watch_allowed_trend_states=tuple(es["watch_allowed_trend_states"]),
        min_amount=float(es["min_amount"]),
        ranking_weights={k: float(v) for k, v in es["ranking"]["weights"].items()},
        amount_score=AmountScoreSpec(
            method=str(amt["method"]), floor=float(amt["floor"]),
            reference=float(amt["reference"]), cap=float(amt["cap"]),
        ),
    )


@lru_cache(maxsize=None)
def load_stock_selection_spec() -> StockSelectionSpec:
    """Layer③ 个股准入 + 主题门控（Policy）。"""
    cfg = _read_yaml("strategies.yaml")
    sch.validate_stock_selection(cfg)
    ss = cfg["stock_selection"]
    return StockSelectionSpec(
        qualified_score=float(ss["qualified_score"]),
        allowed_trend_states=tuple(ss["allowed_trend_states"]),
        theme_confirm_states=tuple(ss["theme_confirm_states"]),
    )


@lru_cache(maxsize=None)
def load_strategy_specs() -> dict[str, StrategySpec]:
    cfg = _read_yaml("strategies.yaml")
    sch.validate_strategies(cfg, _themes_keys())
    out: dict[str, StrategySpec] = {}
    for key, s in cfg["strategies"].items():
        entry = EntrySpec(
            policy=str(s["entry"]["policy"]),
            rps15_min=float(s["entry"]["rps15_min"]),
            trend_score_min=float(s["entry"]["trend_score_min"]),
            allowed_trend_states=tuple(s["entry"]["allowed_trend_states"]),
        )
        exit_ = ExitSpec(
            policy=str(s["exit"]["policy"]),
            horizon=int(s["exit"]["horizon"]) if s["exit"].get("horizon") is not None else None,
            ma_window=int(s["exit"]["ma_window"]) if s["exit"].get("ma_window") is not None else None,
        )
        out[s["strategy_id"]] = StrategySpec(
            strategy_id=str(s["strategy_id"]),
            label=str(s.get("label", key)),
            theme=str(s["theme"]),
            universe_mode=str(s["universe_mode"]),
            entry=entry, exit=exit_,
            weight=float(s.get("weight", 1.0)),
        )
    return out


def load_strategy_spec(strategy_id: str) -> StrategySpec:
    specs = load_strategy_specs()
    if strategy_id not in specs:
        raise KeyError(f"unknown strategy_id: {strategy_id} (options: {sorted(specs)})")
    return specs[strategy_id]


@lru_cache(maxsize=None)
def load_execution_spec() -> ExecutionSpec:
    cfg = _read_yaml("execution.yaml")
    sch.validate_execution(cfg)
    ex = cfg["execution"]
    return ExecutionSpec(
        model=str(ex["model"]),
        fee_bps=float(ex["fee_bps"]),
        slippage_bps=float(ex["slippage_bps"]),
        no_leverage=bool(ex["no_leverage"]),
        no_pyramiding=bool(ex["no_pyramiding"]),
    )


@lru_cache(maxsize=None)
def load_portfolio_spec() -> PortfolioSpec:
    cfg = _read_yaml("portfolio.yaml")
    sch.validate_portfolio(cfg)
    p = cfg["portfolio"]
    allocations: dict[str, AllocationSpec] = {}
    for key, w in (cfg.get("allocations") or {}).items():
        allocations[key] = AllocationSpec(weights=dict(w))
    return PortfolioSpec(
        initial_capital=float(p["initial_capital"]),
        max_positions=int(p["max_positions"]),
        position_sizing=str(p["position_sizing"]),
        max_weight_per_asset=float(p["max_weight_per_asset"]),
        deploy_ratio=float(p["deploy_ratio"]),
        allocations=allocations,
    )


def load_sw_industry_config() -> dict[str, Any]:
    """申万行业模块配置（regimes 阈值等）。"""
    return _read_yaml("sw_industry_rps.yaml")


@lru_cache(maxsize=None)
def spec_config_files() -> list[Path]:
    """构成 Strategy Specification 的全部配置文件（供 config_hash）。"""
    names = [
        "themes_two_directions.yaml", "stock_universe.yaml",
        "strategies.yaml", "indicators.yaml", "execution.yaml", "portfolio.yaml",
        "sw_industry_rps.yaml", "guojin_tradable_blacklist.csv",
    ]
    return [config_dir() / n for n in names]
