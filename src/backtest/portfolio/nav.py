"""
NAV 与绩效指标（Portfolio Metrics）— 统一交易日历、净值序列、风险指标、相对基准。

口径（v0.6 修复后）：
  - 完整交易日历日频 NAV；无交易日也记录（现金净值不变）
  - 年化按实际自然日跨度 (365.25 / elapsed_days)
  - Sharpe / 波动基于完整日收益（risk_free_rate 显式，默认 0）
  - 最大回撤含峰值 / 谷底 / 恢复日 / 持续天数；Calmar = 年化 / |最大回撤|
  - 基准：默认真 HS300 指数缓存（覆盖检查），显式 fallback 才允许切换
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.common.paths import raw_dir

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


def nav_metrics(nav: pd.DataFrame, risk_free_rate: float = RISK_FREE_RATE) -> dict[str, Any]:
    """基于完整交易日频净值的绩效指标。"""
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
