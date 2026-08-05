"""
ETF 趋势状态生成

职责：
  - 对全市场 ETF Universe 做趋势分析
  - 产出 trend_watchlist：当前哪些 ETF 资产值得进一步关注
  - 形成分层：WATCH < STRONG_WATCH < BUY_CANDIDATE

此处筛选的不是「可以买入」，而是「趋势上进入观察范围」。
Watchlist 本质是趋势合格池，不是账户交易池。

P0-C 交付物
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.signal")

TREND_STATES = {"OUT_OF_SCOPE", "WATCH", "STRONG_WATCH", "BUY_CANDIDATE"}


def compute_rps(series: pd.Series) -> pd.Series:
    """计算百分位排名 RPS（0-100）。"""
    if len(series) < 2:
        return pd.Series([50.0] * len(series), index=series.index)
    return series.rank(ascending=True, pct=True) * 100


def compute_trend_state(
    rps15: float,
    rps60: float,
    return_5d: float,
    return_20d: float,
    above_ma20: bool,
    above_ma60: bool,
    strong_threshold: float | None = None,
    watch_threshold: float | None = None,
) -> str:
    if strong_threshold is None or watch_threshold is None:
        from src.common.spec.loaders import load_indicator_spec
        gates = load_indicator_spec()
        strong_threshold = gates.etf_strong_threshold
        watch_threshold = gates.etf_watch_threshold
    if rps15 >= strong_threshold and above_ma20 and above_ma60:
        return "BUY_CANDIDATE"
    if rps15 >= strong_threshold:
        return "STRONG_WATCH"
    if rps15 >= watch_threshold:
        return "WATCH"
    return "OUT_OF_SCOPE"


def _classify_trend_change(
    current_rps: float,
    prev_rps: float | None,
    return_5d: float,
) -> str:
    if prev_rps is not None:
        diff = current_rps - prev_rps
        if diff > 5 and return_5d > 0:
            return "加速"
        if diff > 0:
            return "延续"
        if diff < -5:
            return "减弱"
        if diff < -10:
            return "转弱"
    if return_5d > 3:
        return "加速"
    if return_5d < -3:
        return "减弱"
    return "平稳"


def _classify_amount_change(amount_ratio: float | None) -> str:
    if amount_ratio is None:
        return "—"
    if amount_ratio > 1.2:
        return "放大"
    if amount_ratio < 0.8:
        return "缩小"
    return "持平"


def build_trend_watchlist(
    indicators: pd.DataFrame,
    master: pd.DataFrame,
) -> pd.DataFrame:
    """对全市场 ETF 进行趋势筛选，产出 trend_watchlist。

    indicators 必须包含字段：
      fund_code, price, ma20, ma60, return_5d, return_20d, return_60d,
      amount_ratio（可选）

    Returns:
        trend_watchlist：
        fund_code, fund_name, trend_state, rps15, rps60,
        return_5d, return_20d, trend_change, amount_change, reason
    """
    if indicators.empty:
        return pd.DataFrame()

    df = indicators.copy()

    # 使用 calculate 已算好的全市场横截面 RPS（真实口径 rps15/rps20/rps60）
    if "rps15" not in df.columns or df["rps15"].isna().all():
        if "return_15d" in df.columns:
            df["rps15"] = compute_rps(df["return_15d"])
        else:
            df["rps15"] = 50.0

    if "rps60" not in df.columns or df["rps60"].isna().all():
        if "return_60d" in df.columns:
            df["rps60"] = compute_rps(df["return_60d"])
        else:
            df["rps60"] = 50.0

    name_map = dict(zip(master["fund_code"], master["fund_name"])) if not master.empty else {}

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        code = row.get("fund_code", "")
        if not code:
            continue

        rps15 = row.get("rps15", 50.0)
        rps60 = row.get("rps60", 50.0)
        return_5d = row.get("return_5d", 0.0)
        return_20d = row.get("return_20d", 0.0)
        above_ma20 = row.get("price", 0) > row.get("ma20", float("inf"))
        above_ma60 = row.get("price", 0) > row.get("ma60", float("inf"))

        state = compute_trend_state(
            rps15=rps15, rps60=rps60,
            return_5d=return_5d, return_20d=return_20d,
            above_ma20=above_ma20, above_ma60=above_ma60,
        )

        trend_change = _classify_trend_change(rps15, None, return_5d)
        amount_change = _classify_amount_change(row.get("amount_ratio"))

        rows.append({
            "fund_code": code,
            "fund_name": name_map.get(code, ""),
            "trend_state": state,
            "rps15": round(rps15, 1),
            "rps60": round(rps60, 1),
            "return_5d": round(return_5d, 2),
            "return_20d": round(return_20d, 2),
            "trend_change": trend_change,
            "amount_change": amount_change,
            "reason": f"RPS15={rps15:.0f}, 5d={return_5d:+.1f}%",
        })

    watchlist = pd.DataFrame(rows)
    state_order = {"BUY_CANDIDATE": 0, "STRONG_WATCH": 1, "WATCH": 2, "OUT_OF_SCOPE": 3}
    watchlist["_order"] = watchlist["trend_state"].map(state_order).fillna(9)
    watchlist = watchlist.sort_values(["_order", "rps15"], ascending=[True, False]).drop(columns=["_order"])
    watchlist = watchlist.reset_index(drop=True)

    active = watchlist[watchlist["trend_state"] != "OUT_OF_SCOPE"]
    logger.info(
        "trend watchlist: %d total, %d active (%d BUY_CANDIDATE, %d STRONG_WATCH, %d WATCH)",
        len(watchlist), len(active),
        len(active[active["trend_state"] == "BUY_CANDIDATE"]),
        len(active[active["trend_state"] == "STRONG_WATCH"]),
        len(active[active["trend_state"] == "WATCH"]),
    )
    return watchlist
