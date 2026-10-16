"""主题篮子净值和指标计算（纯离线）。"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import raw_dir
from ..config import basket_config_hash, expand_constituents, load_basket

# A 股收盘点名（与 Layer② SW 口径一致）：此刻前当日 bar 视为未完成。
A_SHARE_CLOSE_CUTOFF = time(15, 10)


def last_complete_trading_day(
    dates,
    *,
    now: datetime | None = None,
    cutoff: time = A_SHARE_CLOSE_CUTOFF,
) -> pd.Timestamp | None:
    """从给定交易日序列中取「最近完整交易日」。

    - max < 今天：过去交易日，已收盘 → max 本身
    - max == 今天 且 now >= cutoff：今日已收盘 → max 本身
    - max == 今天 且 now <  cutoff：今日 bar 未完成 → 退回上一交易日
    - 无更早日期时返回 None（调用方应保留原数据）
    """
    series = pd.to_datetime(pd.Series(list(dates)), errors="coerce").dropna().dt.normalize()
    unique = sorted(series.unique())
    if not unique:
        return None
    now = now or datetime.now()
    last = pd.Timestamp(unique[-1])
    if last.date() < now.date():
        return last
    if last.date() > now.date():
        return last  # 防御：未来日期不裁剪
    if now.time() >= cutoff:
        return last
    return pd.Timestamp(unique[-2]) if len(unique) >= 2 else None


def _price_path(basket: dict[str, Any]) -> str:
    source = basket["source"]
    root = "observation_groups" if source["type"] == "observation_group" else "themes"
    return str(raw_dir() / root / source["key"] / f"{source['key']}_prices.parquet")


def _slice_window(df: pd.DataFrame, start: str = "", end: str = "") -> pd.DataFrame:
    if start:
        df = df[df["date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["date"] <= pd.Timestamp(end)]
    return df


def _align_benchmark(benchmark: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """基准窗口对齐到价格区间，避免从全历史（2002）归一化。"""
    if prices.empty:
        return benchmark
    lo, hi = prices["date"].min(), prices["date"].max()
    return benchmark[(benchmark["date"] >= lo) & (benchmark["date"] <= hi)]


def calculate_basket(
    key: str,
    *,
    start_date: str = "",
    end_date: str = "",
    basket_config=None,
    universe_path=None,
    as_of_now: datetime | None = None,
) -> dict[str, Any]:
    """读取已抓取的篮子价格并计算净值、指标、子组表现。

    买入持有（nav）与季度再平衡（quarterly_nav）共用同一窗口。
    所有窗口统一截断到「最近完整交易日」（`as_of_now` 可注入，供测试复现）。
    """
    basket = load_basket(key, basket_config)
    constituents = expand_constituents(basket, universe_path)
    prices_all = pd.read_parquet(_price_path(basket))
    prices_all["date"] = pd.to_datetime(prices_all["date"]).dt.normalize()
    wanted = {(x["group"], x["symbol"]) for x in constituents}
    prices_all = prices_all[
        prices_all.apply(lambda r: (str(r["group"]), str(r["symbol"])) in wanted, axis=1)].copy()

    benchmark_all = pd.read_csv(raw_dir() / "_benchmark_sh000300.csv", parse_dates=["date"])
    benchmark_all["date"] = pd.to_datetime(benchmark_all["date"]).dt.normalize()

    # 统一截到最近完整交易日：盘中运行时排除当日未收盘 bar，避免各标的/基准末日不一致。
    cap = last_complete_trading_day(benchmark_all["date"], now=as_of_now)
    if cap is not None:
        prices_all = prices_all[prices_all["date"] <= cap]
        benchmark_all = benchmark_all[benchmark_all["date"] <= cap]

    prices = _slice_window(prices_all, start_date, end_date)
    benchmark = _align_benchmark(_slice_window(benchmark_all, start_date, end_date), prices)

    group_order = list(dict.fromkeys(x["group"] for x in constituents))
    weighting_method = basket.get("weighting", {}).get("method", "group_equal")
    nav = build_nav(prices, benchmark, group_order=group_order, weighting_method=weighting_method)
    rolling = rolling_analysis(nav)
    contributions = contribution_analysis(prices, group_order, weighting_method)
    quarterly_nav = quarterly_rebalanced_nav(
        prices, benchmark, group_order=group_order, weighting_method=weighting_method)
    base_metrics = metrics(nav)
    base_metrics.update({
        "top1_contribution_pct_points": round(float(contributions.head(1)["contribution_pct_points"].sum()), 4),
        "top3_contribution_pct_points": round(float(contributions.head(3)["contribution_pct_points"].sum()), 4),
        "quarterly_rebalance_return_pct": round(float((quarterly_nav["basket"].iloc[-1] / quarterly_nav["basket"].iloc[0] - 1) * 100), 4),
        "quarterly_rebalance_excess_pct": round(float((quarterly_nav["basket"].iloc[-1] / quarterly_nav["basket"].iloc[0] - quarterly_nav["benchmark"].iloc[-1] / quarterly_nav["benchmark"].iloc[0]) * 100), 4),
    })
    return {
        "basket": basket,
        "provenance": {
            "basket_key": key,
            "config_hash": basket_config_hash(basket),
            "source": basket["source"],
            "weighting": basket.get("weighting", {}),
            "benchmark": basket.get("benchmark", "sh000300"),
            "n_constituents": len(constituents),
            "groups": group_order,
        },
        "constituents": pd.DataFrame(constituents),
        "prices": prices,
        "nav": nav,
        "metrics": base_metrics,
        "group_metrics": group_metrics(prices, benchmark, group_order),
        "rolling": rolling,
        "contributions": contributions,
        "quarterly_nav": quarterly_nav,
    }


def build_nav(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    group_order: list[str] | None = None,
    weighting_method: str = "group_equal",
) -> pd.DataFrame:
    """构建篮子净值。价格先各标的归一化，再在统一交易日上合成。"""
    if weighting_method not in {"group_equal", "asset_equal"}:
        raise ValueError(f"unsupported weighting method: {weighting_method}")
    prices = prices.copy()
    benchmark = benchmark.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    benchmark["date"] = pd.to_datetime(benchmark["date"]).dt.normalize()
    dates = pd.DatetimeIndex(sorted(benchmark["date"].dropna().unique()))
    groups = group_order or sorted(prices["group"].unique())
    group_curves: dict[str, pd.Series] = {}
    asset_curves: list[pd.Series] = []
    for group in groups:
        sub = prices[prices["group"] == group]
        curves: list[pd.Series] = []
        for symbol, asset in sub.groupby("symbol"):
            close = asset.set_index("date")["close"].sort_index().astype(float)
            if close.empty:
                continue
            curve = (close / close.iloc[0] * 100).reindex(dates).ffill()
            curves.append(curve.rename(str(symbol)))
        if not curves:
            raise ValueError(f"group has no price data: {group}")
        group_curve = pd.concat(curves, axis=1, sort=True).mean(axis=1)
        group_curves[group] = group_curve
        asset_curves.extend(curves)

    if weighting_method == "asset_equal":
        basket = pd.concat(asset_curves, axis=1, sort=True).mean(axis=1)
    else:
        basket = pd.DataFrame(group_curves)[groups].mean(axis=1)
    result = pd.DataFrame({"basket": basket})
    bench = benchmark.set_index("date")["close"].sort_index().reindex(dates)
    result["benchmark"] = bench / bench.dropna().iloc[0] * 100
    result["basket"] = result["basket"].ffill()
    result = result.dropna(subset=["basket", "benchmark"])
    # 篮子与基准都以共同首个有效交易日为 100：基准若含更早历史（未指定 --start），
    # 仅按自身首值归一化会让其起点远高于 100，与篮子不可比。
    result["basket"] = result["basket"] / result["basket"].iloc[0] * 100
    result["benchmark"] = result["benchmark"] / result["benchmark"].iloc[0] * 100
    result.index.name = "date"
    return result


def metrics(nav: pd.DataFrame) -> dict[str, Any]:
    """输出可审计的基础绩效指标，收益字段为百分比。"""
    if nav.empty:
        return {"n_days": 0}
    returns = nav["basket"].pct_change().dropna()
    benchmark_returns = nav["benchmark"].pct_change().dropna()
    drawdown = nav["basket"] / nav["basket"].cummax() - 1
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 365.25)
    annualized = (nav["basket"].iloc[-1] / nav["basket"].iloc[0]) ** (1 / years) - 1
    volatility = returns.std(ddof=1) * np.sqrt(252) if len(returns) > 1 else np.nan
    sharpe = annualized / volatility if volatility and np.isfinite(volatility) else np.nan
    return {
        "start_date": nav.index[0].strftime("%Y-%m-%d"),
        "end_date": nav.index[-1].strftime("%Y-%m-%d"),
        "n_days": int(len(nav)),
        "final_index": round(float(nav["basket"].iloc[-1]), 4),
        "return_pct": round(float((nav["basket"].iloc[-1] / nav["basket"].iloc[0] - 1) * 100), 4),
        "benchmark_return_pct": round(float((nav["benchmark"].iloc[-1] / nav["benchmark"].iloc[0] - 1) * 100), 4),
        "excess_pct": round(float((nav["basket"].iloc[-1] / nav["basket"].iloc[0] - nav["benchmark"].iloc[-1] / nav["benchmark"].iloc[0]) * 100), 4),
        "max_drawdown_pct": round(float(drawdown.min() * 100), 4),
        "annualized_return_pct": round(float(annualized * 100), 4),
        "annualized_volatility_pct": round(float(volatility * 100), 4) if np.isfinite(volatility) else None,
        "sharpe": round(float(sharpe), 4) if np.isfinite(sharpe) else None,
        "win_rate_pct": round(float((returns > 0).mean() * 100), 4) if len(returns) else None,
    }


def group_metrics(prices: pd.DataFrame, benchmark: pd.DataFrame, group_order: list[str]) -> pd.DataFrame:
    """各子组独立归一化后的收益贡献表。"""
    rows = []
    for group in group_order:
        sub = prices[prices["group"] == group]
        vals = []
        for _, asset in sub.groupby("symbol"):
            close = asset.sort_values("date")["close"].astype(float)
            if not close.empty:
                vals.append(float((close.iloc[-1] / close.iloc[0] - 1) * 100))
        rows.append({"group": group, "n_assets": len(vals), "return_pct": round(float(np.mean(vals)), 4) if vals else None})
    return pd.DataFrame(rows)


def rolling_analysis(nav: pd.DataFrame, windows: tuple[int, ...] = (20, 60, 120)) -> pd.DataFrame:
    """计算滚动超额收益、Sharpe 和窗口内最大回撤。"""
    out = pd.DataFrame(index=nav.index)
    basket_ret = nav["basket"].pct_change()
    benchmark_ret = nav["benchmark"].pct_change()

    def window_max_drawdown(values: np.ndarray) -> float:
        peaks = np.maximum.accumulate(values)
        return float(np.min(values / peaks - 1))

    for window in windows:
        out[f"excess_return_{window}d_pct"] = (
            (nav["basket"].pct_change(window) - nav["benchmark"].pct_change(window)) * 100
        )
        mean = basket_ret.rolling(window, min_periods=window).mean()
        std = basket_ret.rolling(window, min_periods=window).std(ddof=1)
        out[f"sharpe_{window}d"] = mean / std * np.sqrt(252)
        out[f"drawdown_{window}d_pct"] = (
            nav["basket"].rolling(window, min_periods=window)
            .apply(window_max_drawdown, raw=True) * 100
        )
    out.index.name = "date"
    return out.reset_index()


def contribution_analysis(
    prices: pd.DataFrame,
    group_order: list[str],
    weighting_method: str = "group_equal",
) -> pd.DataFrame:
    """计算个股对篮子区间收益的贡献百分点，并标出 Top1/Top3。"""
    assets: list[dict[str, Any]] = []
    group_sizes = prices.groupby("group")["symbol"].nunique().to_dict()
    n_assets = prices["symbol"].nunique()
    for group in group_order:
        sub = prices[prices["group"] == group]
        for symbol, asset in sub.groupby("symbol"):
            close = asset.sort_values("date")["close"].astype(float)
            if close.empty:
                continue
            ret = float(close.iloc[-1] / close.iloc[0] - 1)
            if weighting_method == "asset_equal":
                weight = 1 / n_assets
            else:
                weight = 1 / len(group_order) / group_sizes[group]
            assets.append({
                "group": group,
                "symbol": str(symbol),
                "name": str(asset["name"].iloc[0]) if "name" in asset else str(symbol),
                "weight_pct": round(weight * 100, 4),
                "return_pct": round(ret * 100, 4),
                "contribution_pct_points": round(weight * ret * 100, 4),
            })
    out = pd.DataFrame(assets).sort_values("contribution_pct_points", ascending=False).reset_index(drop=True)
    total = float(out["contribution_pct_points"].sum()) if not out.empty else 0.0
    out["contribution_share_pct"] = (out["contribution_pct_points"] / total * 100).round(4) if total else np.nan
    out["rank"] = out.index + 1
    out["top1"] = out["rank"] <= 1
    out["top3"] = out["rank"] <= 3
    return out


def quarterly_rebalanced_nav(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    group_order: list[str],
    weighting_method: str = "group_equal",
) -> pd.DataFrame:
    """按季度首个交易日重置权重，返回季度调仓篮子与 HS300 净值。"""
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(benchmark["date"]).dt.normalize().unique()))
    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()
    pivot = prices.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    pivot = pivot.reindex(dates).ffill()
    groups = prices.drop_duplicates("symbol").set_index("symbol")["group"].to_dict()
    symbols = [str(c) for c in pivot.columns]
    if weighting_method == "asset_equal":
        target = {s: 1 / len(symbols) for s in symbols}
    else:
        target = {}
        for group in group_order:
            members = [s for s in symbols if groups.get(s) == group]
            for symbol in members:
                target[symbol] = 1 / len(group_order) / len(members)
    returns = pivot.pct_change().fillna(0.0)
    quarters = pd.Series(dates.to_period("Q"), index=dates)
    boundary = quarters.ne(quarters.shift(1))
    returns.loc[boundary, :] = 0.0
    basket_daily = returns[[s for s in symbols if s in target]].mul(pd.Series(target)).sum(axis=1)
    basket = (1 + basket_daily).cumprod() * 100
    bench = benchmark.copy()
    bench["date"] = pd.to_datetime(bench["date"]).dt.normalize()
    bench_close = bench.set_index("date")["close"].reindex(dates).ffill()
    result = pd.DataFrame({"basket": basket, "benchmark": bench_close / bench_close.dropna().iloc[0] * 100}, index=dates)
    result = result.dropna()
    result["basket"] = result["basket"] / result["basket"].iloc[0] * 100
    result["benchmark"] = result["benchmark"] / result["benchmark"].iloc[0] * 100
    result.index.name = "date"
    return result
