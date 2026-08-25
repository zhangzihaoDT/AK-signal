"""
个股趋势历史表 — 对 universe 股票池预计算逐日 score/watch_level/action（v0.10 研究）。

原理：data/processed/{market}_{symbol}.csv 每行是当日指标列（ma20/ma60/rsi14/
relative_strength_20d/volume_ratio 等，全部向后 rolling 计算）。把序列截断到任意
日期取该行 = 该日「当时可得」的指标，无 look-ahead。因此可以一次性预计算全序列的
score/watch_level/action，事件日按日期查表 O(1)，避免对每个历史事件日重算。

score 逻辑与 production 的 scoring.score_latest_row 完全一致（不含 reason 字符串）；
watch_level/action 复用 trend_engine.engine 的 calc_watch_level / calc_action / calc_change。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.common.paths import processed_dir as common_processed_dir
from src.selection.universe import UniverseItem
from src.trend_engine import engine as te

logger = logging.getLogger("research.expression_regime.history")


def _f(row: pd.Series, col: str) -> Any:
    v = row.get(col)
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except TypeError:
        return v
    return v


def score_row(row: pd.Series) -> int:
    """与 scoring.score_latest_row 相同的加权评分（跳过 reason 字符串构建）。

    列依赖：close / ma20 / ma60 / ma120 / ma20_slope / ma60_slope /
    macd_hist / rsi14 / relative_strength_20d。
    """
    close_v = _f(row, "close")
    if close_v is None:
        return 0
    close = float(close_v)
    score = 0
    for ma_col, w in [("ma20", 15), ("ma60", 15), ("ma120", 10)]:
        v = _f(row, ma_col)
        if v is not None and close >= float(v):
            score += w
    for slope_col, w in [("ma20_slope", 10), ("ma60_slope", 10)]:
        v = _f(row, slope_col)
        if v is not None and float(v) > 0:
            score += w
    hist = _f(row, "macd_hist")
    if hist is not None and float(hist) > 0:
        score += 15
    rsi = _f(row, "rsi14")
    if rsi is not None:
        r = float(rsi)
        if 50 <= r <= 70:
            score += 15
        elif r > 70:
            score += 5
        elif 40 <= r < 50:
            score += 5
    rs = _f(row, "relative_strength_20d")
    if rs is not None and float(rs) > 0:
        score += 10
    return score


def _watch_level(row: pd.Series, score: int) -> str:
    rs = _f(row, "relative_strength_20d")
    return te.calc_watch_level(
        score,
        float(rs) if rs is not None else None,
        _as_float(row, "ma20"),
        _as_float(row, "ma60"),
        _as_float(row, "volume_ratio"),
    )


def _action(row: pd.Series, score: int, wl: str, change: str) -> str:
    return te.calc_action(
        score, wl,
        _as_float(row, "relative_strength_20d"),
        _as_float(row, "volume_ratio"),
        _as_bool(row, "price_near_ma20"),
        _as_float(row, "drawdown_from_high"),
        change,
    )


def _as_float(row: pd.Series, col: str) -> float | None:
    v = _f(row, col)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_bool(row: pd.Series, col: str) -> bool | None:
    v = _f(row, col)
    if v is None:
        return None
    try:
        return bool(float(v))
    except (TypeError, ValueError):
        return None


def build_stock_trend_history(
    items: Sequence[UniverseItem],
    processed_dir: Path | None = None,
) -> dict[str, pd.DataFrame]:
    """预计算每只股票（processed CSV 覆盖）的逐日 score/watch_level/action。

    Args:
        items: universe 资产列表（只处理股票 tier）
        processed_dir: data/processed 目录（默认 common paths）

    Returns:
        {symbol: DataFrame}，原指标列 + `_score` / `_watch_level` / `_action` /
        `_is_stock`（股票标记）。缺失 processed CSV 的股票不进入结果。
    """
    processed_dir = processed_dir or common_processed_dir()
    out: dict[str, pd.DataFrame] = {}
    for item in items:
        asset = item.asset
        if str(getattr(asset, "category", "") or "").strip() not in (
            "leader", "high_beta", "equipment_upstream",
            "computing_chip", "optical_interconnect", "server_network",
            "semiconductor_equipment", "semiconductor_components",
            "liquid_cooling", "high_speed_interconnect", "server_power",
            "oem_global", "battery_global", "global_ev_components",
            "global_auto_components", "adas_lidar",
            "hydro_nuclear", "telecom_operator", "toll_road", "port_operator",
            "cyclical_power_watch",
        ):
            continue
        path = processed_dir / f"{asset.market}_{asset.symbol}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception as e:
            logger.warning("read processed %s failed: %s", path.name, e)
            continue
        if df.empty:
            continue
        df = df.sort_values("date").reset_index(drop=True)

        scores: list[int] = []
        watch_levels: list[str] = []
        actions: list[str] = []
        prev_score: int | None = None
        for _, row in df.iterrows():
            s = score_row(row)
            wl = _watch_level(row, s)
            change = te.calc_change(s, prev_score, prev_score is not None)
            act = _action(row, s, wl, change)
            scores.append(s)
            watch_levels.append(wl)
            actions.append(act)
            prev_score = s

        df["_score"] = scores
        df["_watch_level"] = watch_levels
        df["_action"] = actions
        df["_name"] = asset.name
        df["_symbol"] = asset.symbol
        df["_market"] = asset.market
        out[asset.symbol] = df
    logger.info("stock trend history built: %d symbols (processed dir=%s)", len(out), processed_dir)
    return out


def trend_snapshot_at(
    history: dict[str, pd.DataFrame],
    trade_date: Any,
) -> pd.DataFrame:
    """取 trade_date 当日全部股票的实时趋势快照（按 stock_metrics schema 对齐）。

    Returns:
        DataFrame（列与 stock_metrics parquet 对齐：symbol/score_trend/watch_level/
        action/return_20d/close/name/market/data_status），供 tier_metrics_for_theme
        与 select_stock_watchlist 消费。
    """
    cutoff = pd.Timestamp(trade_date)
    _ = cutoff  # pandas Timestamp（pyright 对 Timestamp 构造的重载误报可忽略）
    rows: list[dict[str, Any]] = []
    for symbol, df in history.items():
        sub = df[df["date"] <= cutoff]
        if sub.empty:
            rows.append({"symbol": symbol, "data_status": "missing",
                         "score_trend": None, "watch_level": "", "action": "",
                         "return_20d": None, "close": None, "name": "", "market": "",
                         "risk_flags": ""})
            continue
        row = sub.iloc[-1]
        rows.append({
            "symbol": symbol,
            "score_trend": row["_score"],
            "trend_score": row["_score"],
            "watch_level": row["_watch_level"],
            "action": row["_action"],
            "return_20d": _as_float(row, "return_20d"),
            "close": _as_float(row, "close"),
            "name": str(row.get("_name", symbol)),
            "market": str(row.get("_market", "")),
            "risk_flags": str(row.get("risk_flags", "") or ""),
            "data_status": "current",
            "trade_date": pd.to_datetime(trade_date),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    for c in ("score_trend", "return_20d", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df
