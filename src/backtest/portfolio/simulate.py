"""
Portfolio Simulation 编排 — 单策略账户 + Core+Quality 综合账户。

三套单账户：AI-20 / AI-MA / HC-20（策略配置见 config/strategy_spec.yaml）
综合账户：Core+Quality = AI-20 + HC-20，两种资金模式：
  Mode A：全组合统一等权（weight 全 1）
  Mode B：AI 60% / HC 40%
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.common.paths import config_dir
from src.research.replay import engine as replay_engine
from .engine import PortfolioAccount, build_close_prices
from .nav import (
    ANNUALIZATION_METHOD, benchmark_series, benchmark_on_calendar,
    build_trading_calendar, requested_range, relative_metrics,
)
from .nav import nav_metrics  # noqa: F401  (re-export for callers/report)


def load_strategies(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or (config_dir() / "strategy_spec.yaml")
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("strategies", {})


def strategy_trades(
    signals: pd.DataFrame,
    cfg: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """按策略规格运行逐笔模拟，产出该策略的成交序列（含 Provenance）。

    entry/exit/universe 参数来自统一 Strategy Specification（config/strategy_spec.yaml）。
    """
    from src.common.spec.loaders import load_strategy_spec
    from ..trade.trades import run_backtest

    sid = str(cfg.get("strategy_id", "") or "")
    spec = load_strategy_spec(sid) if sid else None

    if spec is not None:
        exit_ = spec.exit
        if exit_.policy == "fixed_horizon":
            config = {"label": cfg.get("label", sid), "policy": "fixed_horizon",
                      "params": {"horizon": int(exit_.horizon)}}
        elif exit_.policy == "ma_exit":
            config = {"label": cfg.get("label", sid), "policy": "ma_exit",
                      "params": {"window": int(exit_.ma_window)}}
        else:
            config = {"label": cfg.get("label", sid), "policy": exit_.policy, "params": {}}
        entry_params = {"rps15_min": float(spec.entry.rps15_min),
                        "trend_score_min": float(spec.entry.trend_score_min),
                        "allowed_trend_states": list(spec.entry.allowed_trend_states)}
        universe_mode = spec.universe_mode
        weight = spec.weight
    else:  # 兼容旧配置（无 strategy_id）
        config = _legacy_exit_config(cfg)
        entry_params = None
        universe_mode = cfg.get("universe_mode", "configured")
        weight = float(cfg.get("weight", 1.0))

    trades = run_backtest(
        signals, theme=cfg["theme"], entity_type="etf",
        exit_configs=[config], universe_mode=universe_mode,
        entry_params=entry_params, strategy_id=sid, cache=cache)
    trades["weight"] = weight
    trades["strategy"] = cfg.get("label", "")
    return trades


def _legacy_exit_config(cfg: dict[str, Any]) -> dict[str, Any]:
    policy = cfg["exit_policy"]
    if policy == "fixed_horizon":
        return {"label": cfg.get("label", "fixed"), "policy": "fixed_horizon",
                "params": {"horizon": int(cfg.get("horizon", 20))}}
    if policy == "ma_exit":
        return {"label": cfg.get("label", "ma"), "policy": "ma_exit",
                "params": {"window": int(cfg.get("ma_window", 20))}}
    return {"label": cfg.get("label", policy), "policy": policy, "params": {}}


def _account_from_trades(trades: pd.DataFrame, params: dict[str, Any], label: str) -> PortfolioAccount:
    return PortfolioAccount(
        initial_capital=params["initial_capital"],
        max_positions=params["max_positions"],
        max_weight_per_asset=params["max_weight_per_asset"],
        fee_pct=params["fee_pct"], slippage_pct=params["slippage_pct"],
        deploy_ratio=params.get("deploy_ratio", 1.0),
        label=label)


def run_portfolios(
    signals: pd.DataFrame,
    *,
    strategies_path: Path | None = None,
    initial_capital: float | None = None,
    max_positions: int | None = None,
    max_weight_per_asset: float | None = None,
    fee_pct: float | None = None,
    slippage_pct: float | None = None,
    modes: tuple[str, ...] = ("A", "B"),
    benchmark: str = "sh000300",
    benchmark_fallback: str = "510300",
    allow_benchmark_fallback: bool = True,
    start_date: str = "",
    end_date: str = "",
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """运行三套单账户 + Core+Quality 综合账户（Mode A/B）。

    统一日历：所有账户 + 基准共用 [start, end] 的完整交易日历；
    基准：默认真 HS300 指数缓存，覆盖不足时才允许显式 fallback；
    资金参数默认来自统一 Strategy Specification（portfolio.yaml / execution.yaml）。
    """
    from src.common.spec.loaders import load_execution_spec, load_portfolio_spec

    pspec = load_portfolio_spec()
    espec = load_execution_spec()
    strategies = load_strategies(strategies_path)
    cache = cache or replay_engine.build_replay_cache()
    closes = build_close_prices(cache)

    r_start, r_end = requested_range(signals)
    cal_start = start_date or r_start
    cal_end = end_date or r_end
    calendar = build_trading_calendar(cache, cal_start, cal_end)
    if not calendar:
        raise ValueError("empty trading calendar")

    # 基准（统一日历，覆盖检查）
    bench_close, bench_meta = benchmark_series(
        cache, benchmark, start=cal_start, end=cal_end,
        fallback=benchmark_fallback, allow_fallback=allow_benchmark_fallback)
    bench_norm = benchmark_on_calendar(bench_close, calendar)

    params = dict(
        initial_capital=initial_capital if initial_capital is not None else pspec.initial_capital,
        max_positions=max_positions if max_positions is not None else pspec.max_positions,
        max_weight_per_asset=max_weight_per_asset
            if max_weight_per_asset is not None else pspec.max_weight_per_asset,
        fee_pct=fee_pct if fee_pct is not None else espec.fee_pct,
        slippage_pct=slippage_pct if slippage_pct is not None else espec.slippage_pct,
        deploy_ratio=pspec.deploy_ratio,
        calendar_start=cal_start, calendar_end=cal_end,
        trading_days=len(calendar), benchmark_symbol=benchmark,
        benchmark_source=bench_meta.get("source", ""),
        benchmark_fallback_used=bench_meta.get("fallback_used", False),
        benchmark_coverage_start=bench_meta.get("coverage_start"),
        benchmark_coverage_end=bench_meta.get("coverage_end"),
        nav_frequency="daily", annualization_method=ANNUALIZATION_METHOD)

    def _attach_benchmark(account: PortfolioAccount) -> dict[str, Any]:
        nav = account.nav_frame()
        return {"benchmark": bench_norm, "relative": relative_metrics(nav, bench_norm)}

    single = {}
    trades_map: dict[str, pd.DataFrame] = {}
    for key, cfg in strategies.items():
        trades = strategy_trades(signals, cfg, cache)
        trades_map[key] = trades
        account = _account_from_trades(trades, params, cfg.get("label", key))
        account.run(trades, closes, calendar)
        single[key] = {
            "label": cfg.get("label", key), "theme": cfg.get("theme", ""),
            "n_filled": int((trades["entry_status"] == "filled").sum()),
            "n_trades": int(len(trades)),
            "account": account,
        }

    combined = {}
    core_keys = [k for k in ("ai_20", "hc_20") if k in trades_map]
    if len(core_keys) == 2:
        for mode in modes:
            combined_trades = pd.concat(
                [trades_map[k].assign(weight=(1.0 if mode == "A" else
                                              strategies[k].get("weight", 0.5)))
                 for k in core_keys], ignore_index=True)
            account = _account_from_trades(
                combined_trades, params, f"Core+Quality-{mode}")
            account.run(combined_trades, closes, calendar)
            combined[mode] = {
                "label": f"Core+Quality-{mode}",
                "n_filled": int((combined_trades["entry_status"] == "filled").sum()),
                "n_trades": int(len(combined_trades)),
                "account": account,
            }

    for key, v in single.items():
        v.update({f"bench_{k}": val for k, val in _attach_benchmark(v["account"]).items()})
    for mode, v in combined.items():
        v.update({f"bench_{k}": val for k, val in _attach_benchmark(v["account"]).items()})

    return {"single": single, "combined": combined, "params": params,
            "calendar": calendar, "benchmark": bench_norm, "benchmark_meta": bench_meta}
