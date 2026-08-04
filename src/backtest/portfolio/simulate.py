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

from src.common.paths import config_dir, raw_dir
from src.research.replay import engine as replay_engine
from .account import PortfolioAccount, build_close_prices

ANNUALIZATION_METHOD = "calendar_days_365.25"
RISK_FREE_RATE = 0.0


def build_trading_calendar(cache: dict[str, Any], start: str, end: str) -> list[str]:
    """A 股交易日历（来自全市场 ETF 行情日期集合），截取 [start, end]（YYYYMMDD，含边界）。"""
    combined = cache.get("combined")
    if combined is None or combined.empty:
        return []
    dates = pd.to_datetime(combined["date"], errors="coerce").dropna()
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    days = sorted({d for d in dates if s <= d <= e})
    return [d.strftime("%Y%m%d") for d in days]


def requested_range(signals: pd.DataFrame) -> tuple[str, str]:
    """研究区间 = 信号覆盖范围（min/max trade_date）。"""
    dates = signals["trade_date"].astype(str)
    return str(dates.min()), str(dates.max())


def benchmark_series(
    cache: dict[str, Any],
    symbol: str = "sh000300",
    *,
    start: str = "",
    end: str = "",
    fallback: str = "510300",
    allow_fallback: bool = True,
) -> tuple[pd.Series, dict[str, Any]]:
    """基准序列 + 元信息。

    优先级：sh000300 真指数缓存 → 显式 fallback（默认 510300）。
    覆盖不完整时默认不静默切换（allow_fallback 才回退）。
    """
    meta: dict[str, Any] = {"symbol": symbol, "source": "", "coverage_start": None,
                            "coverage_end": None, "fallback_used": False, "covers": False}
    close: pd.Series = pd.Series(dtype=float)

    if symbol == "sh000300":
        path = raw_dir() / "_benchmark_sh000300.csv"
        meta["source"] = "sh000300_index_cache"
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"])
            if not df.empty and "close" in df.columns:
                close = df.set_index("date")["close"].sort_index()
        else:
            close = pd.Series(dtype=float)
    else:
        combined = cache.get("combined", pd.DataFrame())
        meta["source"] = "etf_510300_proxy"
        if not combined.empty:
            sub = combined[combined["fund_code"].astype(str) == symbol].copy()
            if not sub.empty:
                sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
                sub = sub.dropna(subset=["date"]).sort_values("date")
                close = sub.set_index("date")["close"]

    if close.empty:
        meta["reason"] = "no_data"
        return close, meta

    idx = close.index
    meta["coverage_start"] = idx.min().strftime("%Y%m%d")
    meta["coverage_end"] = idx.max().strftime("%Y%m%d")
    meta["covers"] = bool(
        (not start or pd.Timestamp(start) >= idx.min())
        and (not end or pd.Timestamp(end) <= idx.max())
    )

    if not meta["covers"] and allow_fallback and fallback and fallback != symbol:
        fb_close, fb_meta = benchmark_series(
            cache, fallback, start=start, end=end, fallback="", allow_fallback=False)
        if not fb_close.empty:
            fb_meta["fallback_used"] = True
            fb_meta["fallback_from"] = symbol
            return fb_close, fb_meta
    return close, meta


def benchmark_on_calendar(bench: pd.Series, calendar: list[str]) -> pd.Series:
    """基准序列对齐到统一日历（ffill），起始归一化为 1.0。"""
    idx = pd.to_datetime(calendar)
    aligned = bench.reindex(idx).ffill()
    base = aligned.dropna()
    if base.empty:
        return pd.Series(index=idx, dtype=float)
    return aligned / base.iloc[0]


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
    benchmark: str = "sh000300",
    benchmark_fallback: str = "510300",
    allow_benchmark_fallback: bool = True,
    start_date: str = "",
    end_date: str = "",
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """运行三套单账户 + Core+Quality 综合账户（Mode A/B）。

    统一日历：所有账户 + 基准共用 [start, end] 的完整交易日历；
    基准：默认真 HS300 指数缓存，覆盖不足时才允许显式 fallback。
    """
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

    params = dict(initial_capital=initial_capital, max_positions=max_positions,
                  max_weight_per_asset=max_weight_per_asset,
                  fee_pct=fee_pct, slippage_pct=slippage_pct,
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


def nav_metrics(nav: pd.DataFrame, risk_free_rate: float = RISK_FREE_RATE) -> dict[str, Any]:
    """基于完整交易日频净值的绩效指标。

    annualized：按实际自然日跨度 (365.25/elapsed_days)；Sharpe/波动：完整日收益；
    最大回撤 + 起止/恢复/持续；Calmar = 年化 / |最大回撤|。
    """
    base = {"total_return_pct": None, "annualized_pct": None, "max_drawdown_pct": None,
            "sharpe": None, "calmar": None, "volatility_pct": None,
            "trading_days": int(len(nav)), "elapsed_calendar_days": None,
            "annualization_method": ANNUALIZATION_METHOD, "risk_free_rate": risk_free_rate,
            "dd_start": None, "dd_trough": None, "dd_recovery": None, "dd_duration_days": None}
    if nav.empty or len(nav) < 2:
        return base
    nav = nav.sort_values("date").reset_index(drop=True)
    equity = nav["equity"]
    ret = equity.pct_change().dropna()
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    elapsed = (pd.Timestamp(nav["date"].iloc[-1]) - pd.Timestamp(nav["date"].iloc[0])).days
    annual = float((equity.iloc[-1] / equity.iloc[0]) ** (365.25 / elapsed) - 1.0) \
        if equity.iloc[0] > 0 and elapsed > 0 else None

    running_peak = equity.cummax()
    dd = equity / running_peak - 1.0
    dd_frac = float(dd.min())

    vol = float(ret.std() * math.sqrt(252)) if len(ret) > 1 else None
    sharpe = float((ret.mean() - risk_free_rate / 252) / ret.std() * math.sqrt(252)) \
        if ret.std() and ret.std() > 0 and len(ret) > 1 else None
    calmar = round(annual / abs(dd_frac), 2) if annual is not None and dd_frac < 0 else None

    # 回撤区间
    dd_start, dd_trough, dd_recovery, dd_dur = _drawdown_span(nav, equity, dd)

    return {
        "total_return_pct": round(total * 100, 2),
        "annualized_pct": round(annual * 100, 2) if annual is not None else None,
        "max_drawdown_pct": round(dd_frac * 100, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "calmar": calmar,
        "volatility_pct": round(vol * 100, 2) if vol is not None else None,
        "trading_days": int(len(nav)),
        "elapsed_calendar_days": elapsed,
        "annualization_method": ANNUALIZATION_METHOD,
        "risk_free_rate": risk_free_rate,
        "dd_start": dd_start, "dd_trough": dd_trough,
        "dd_recovery": dd_recovery, "dd_duration_days": dd_dur,
    }


def _drawdown_span(nav: pd.DataFrame, equity: pd.Series, dd: pd.Series) -> tuple[str | None, str | None, str | None, int | None]:
    """最大回撤的峰值日 / 谷底日 / 恢复日 / 持续时间（自然日）。"""
    trough_pos = int(dd.idxmin())
    trough_date = str(nav["date"].iloc[trough_pos])
    peak_val = float(equity.iloc[trough_pos] / (1 + dd.min()))  # running peak 值
    pre = nav.iloc[:trough_pos + 1]
    peak_pos = int(pre[pre["equity"] >= peak_val].index[-1])
    peak_date = str(nav["date"].iloc[peak_pos])
    post = nav.iloc[trough_pos:]
    rec = post[post["equity"] >= peak_val]
    rec_date = str(rec["date"].iloc[0]) if not rec.empty else None
    dur = (pd.Timestamp(trough_date) - pd.Timestamp(peak_date)).days
    return peak_date, trough_date, rec_date, dur


def relative_metrics(nav: pd.DataFrame, bench: pd.Series) -> dict[str, Any]:
    """相对基准（统一日历对齐）：基准收益 / 超额 / 日跑赢率 / 20 日滚动跑赢率。"""
    empty = {"bench_total_pct": None, "excess_pct": None,
             "daily_outperformance_rate": None, "rolling_20d_outperformance_rate": None}
    if nav.empty or bench is None or bench.empty:
        return empty
    nav = nav.copy()
    nav["date"] = pd.to_datetime(nav["date"], errors="coerce")
    nav = nav.dropna(subset=["date"]).set_index("date")["equity"]
    common = nav.index.intersection(bench.index)
    if len(common) < 2:
        return empty
    nav_c, bench_c = nav.loc[common], bench.loc[common]
    bench_total = float(bench_c.iloc[-1] / bench_c.iloc[0] - 1.0)
    port_total = float(nav_c.iloc[-1] / nav_c.iloc[0] - 1.0)
    nav_ret = nav_c.pct_change().dropna()
    bench_ret = bench_c.pct_change().dropna()
    daily = float((nav_ret > bench_ret).mean()) if len(nav_ret) else None
    nav_20 = nav_c.pct_change(20).dropna()
    bench_20 = bench_c.pct_change(20).dropna()
    idx = nav_20.index.intersection(bench_20.index)
    roll = float((nav_20.loc[idx] > bench_20.loc[idx]).mean()) if len(idx) else None
    return {
        "bench_total_pct": round(bench_total * 100, 2),
        "excess_pct": round((port_total - bench_total) * 100, 2),
        "daily_outperformance_rate": round(daily, 4) if daily is not None else None,
        "rolling_20d_outperformance_rate": round(roll, 4) if roll is not None else None,
    }
