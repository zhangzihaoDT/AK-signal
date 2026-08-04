"""
v0.6 共享账户模拟编排 — 单策略账户 + Core+Quality 综合账户。

三套单账户：AI-20 / AI-MA / HC-20（策略配置见 config/strategies.yaml）
综合账户：Core+Quality = AI-20 + HC-20，两种资金模式：
  Mode A：全组合统一等权（weight 全 1）
  Mode B：AI 60% / HC 40%
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.common.paths import config_dir
from src.research.replay import engine as replay_engine
from .account import PortfolioAccount, build_close_prices


def load_strategies(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or (config_dir() / "strategies.yaml")
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("strategies", {})


def strategy_trades(
    signals: pd.DataFrame,
    cfg: dict[str, Any],
    cache: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """按策略配置运行 v0.5.2 逐笔模拟，产出该策略的成交序列。"""
    from ..trades import run_backtest
    policy = cfg["exit_policy"]
    if policy == "fixed_horizon":
        config = {"label": cfg.get("label", "fixed"), "policy": "fixed_horizon",
                  "params": {"horizon": int(cfg.get("horizon", 20))}}
    elif policy == "ma_exit":
        config = {"label": cfg.get("label", "ma"), "policy": "ma_exit",
                  "params": {"window": int(cfg.get("ma_window", 20))}}
    else:
        config = {"label": cfg.get("label", policy), "policy": policy, "params": {}}
    trades = run_backtest(
        signals, theme=cfg["theme"], entity_type="etf",
        exit_configs=[config], universe_mode=cfg.get("universe_mode", "configured"),
        cache=cache)
    trades["weight"] = float(cfg.get("weight", 1.0))
    trades["strategy"] = cfg.get("label", "")
    return trades


def _account_from_trades(trades: pd.DataFrame, params: dict[str, Any], label: str) -> PortfolioAccount:
    return PortfolioAccount(
        initial_capital=params["initial_capital"],
        max_positions=params["max_positions"],
        max_weight_per_asset=params["max_weight_per_asset"],
        fee_pct=params["fee_pct"], slippage_pct=params["slippage_pct"],
        label=label)


def run_portfolios(
    signals: pd.DataFrame,
    *,
    strategies_path: Path | None = None,
    initial_capital: float = 1_000_000.0,
    max_positions: int = 5,
    max_weight_per_asset: float = 0.20,
    fee_pct: float = 0.05,
    slippage_pct: float = 0.05,
    modes: tuple[str, ...] = ("A", "B"),
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """运行三套单账户 + Core+Quality 综合账户（Mode A/B）。"""
    strategies = load_strategies(strategies_path)
    cache = cache or replay_engine.build_replay_cache()
    closes = build_close_prices(cache)
    params = dict(initial_capital=initial_capital, max_positions=max_positions,
                  max_weight_per_asset=max_weight_per_asset,
                  fee_pct=fee_pct, slippage_pct=slippage_pct)

    single = {}
    trades_map: dict[str, pd.DataFrame] = {}
    for key, cfg in strategies.items():
        trades = strategy_trades(signals, cfg, cache)
        trades_map[key] = trades
        account = _account_from_trades(trades, params, cfg.get("label", key))
        account.run(trades, closes)
        single[key] = {
            "label": cfg.get("label", key), "theme": cfg.get("theme", ""),
            "n_filled": int((trades["entry_status"] == "filled").sum()),
            "n_trades": int(len(trades)),
            "account": account,
        }

    # 基准（沪深300ETF 510300 代理）——按各账户净值日期对齐
    def _attach_benchmark(account: PortfolioAccount) -> dict[str, Any]:
        nav = account.nav_frame()
        if nav.empty:
            return {"benchmark": None, "relative": None}
        bench = benchmark_nav(cache, nav["date"].astype(str).tolist())
        return {"benchmark": bench, "relative": relative_metrics(nav, bench)}

    # Core + Quality 综合账户
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
            account.run(combined_trades, closes)
            combined[mode] = {
                "label": f"Core+Quality-{mode}",
                "n_filled": int((combined_trades["entry_status"] == "filled").sum()),
                "n_trades": int(len(combined_trades)),
                "account": account,
                **{f"bench_{k}": v for k, v in _attach_benchmark(account).items()},
            }

    for key, v in single.items():
        v.update({f"bench_{k}": val for k, val in _attach_benchmark(v["account"]).items()})

    return {"single": single, "combined": combined, "params": params}


def nav_metrics(nav: pd.DataFrame) -> dict[str, Any]:
    """净值序列指标：总收益 / 年化 / 最大回撤 / Sharpe / 波动 / Calmar。"""
    if nav.empty or len(nav) < 2:
        return {"total_return_pct": None, "max_drawdown_pct": None,
                "annualized_pct": None, "sharpe": None, "calmar": None,
                "n_days": int(len(nav))}
    equity = nav["equity"]
    ret = equity.pct_change().dropna()
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    n = len(equity)
    annual = float((equity.iloc[-1] / equity.iloc[0]) ** (252 / n) - 1.0) if equity.iloc[0] > 0 else None
    dd_frac = float((equity / equity.cummax() - 1.0).min())
    sharpe = float(ret.mean() / ret.std() * math.sqrt(252)) if ret.std() and ret.std() > 0 else None
    vol = float(ret.std() * math.sqrt(252))
    calmar = round(annual / abs(dd_frac), 2) if annual is not None and dd_frac < 0 else None
    return {
        "total_return_pct": round(total * 100, 2),
        "annualized_pct": round(annual * 100, 2) if annual is not None else None,
        "max_drawdown_pct": round(dd_frac * 100, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "calmar": calmar,
        "volatility_pct": round(vol * 100, 2),
        "n_days": int(n),
    }


def benchmark_nav(
    cache: dict[str, Any],
    dates: list[str],
    benchmark_code: str = "510300",
) -> pd.Series:
    """基准净值（默认沪深300ETF 510300），按给定日期序列对齐，起始归一化为 1.0。

    离线、全历史；HS300 指数缓存已过期，用 510300（沪深300ETF）作代理。
    """
    combined = cache.get("combined")
    if combined is None or combined.empty:
        return pd.Series(dtype=float)
    sub = combined[combined["fund_code"].astype(str) == benchmark_code].copy()
    if sub.empty:
        return pd.Series(dtype=float)
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
    sub = sub.dropna(subset=["date"]).sort_values("date")
    close = sub.set_index("date")["close"]
    idx = pd.to_datetime(dates)
    aligned = close.reindex(idx).ffill()
    aligned = aligned / (aligned.dropna().iloc[0] if not aligned.dropna().empty else 1.0)
    return aligned


def relative_metrics(nav: pd.DataFrame, bench: pd.Series) -> dict[str, Any]:
    """相对基准：区间基准收益 / 组合超额收益 / 相对胜率（组合跑赢基准的日占比）。"""
    if nav.empty or bench is None or bench.empty:
        return {"bench_total_pct": None, "excess_pct": None, "win_vs_bench": None}
    nav = nav.copy()
    nav["date"] = pd.to_datetime(nav["date"], errors="coerce")
    nav = nav.dropna(subset=["date"]).set_index("date")["equity"]
    common = nav.index.intersection(bench.index)
    if len(common) < 2:
        return {"bench_total_pct": None, "excess_pct": None, "win_vs_bench": None}
    nav_c = nav.loc[common]
    bench_c = bench.loc[common]
    bench_total = float(bench_c.iloc[-1] / bench_c.iloc[0] - 1.0)
    port_total = float(nav_c.iloc[-1] / nav_c.iloc[0] - 1.0)
    port_ret = nav_c.pct_change().dropna()
    bench_ret = bench_c.pct_change().dropna()
    win = float((port_ret > bench_ret).mean()) if len(port_ret) else None
    return {
        "bench_total_pct": round(bench_total * 100, 2),
        "excess_pct": round((port_total - bench_total) * 100, 2),
        "win_vs_bench": round(win, 4) if win is not None else None,
    }
