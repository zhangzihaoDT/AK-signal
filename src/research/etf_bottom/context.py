"""Study 2C — Current Episode Context Matching。

目标：对 2026 当前 6 个产业底部 episode 与历史 episode 做 context 距离匹配，
回答「当前更像成功底部还是失败底部」。核心假设 = 底部是否有效，关键不是「有多低」，
而是「低位时市场/产业是否已开始改善」。

No look-ahead 规则（用户锁定）：
  - 所有 context 只在 episode.start 当日可观察
  - ex-post 字段（final_participation_ratio / episode_duration_days / final_n_etfs）只进审计，
    不进距离、不做解释变量
  - Z-score scaler 只 fit 历史 episode，再 transform historical + current
  - start-date aggregation 的 Universe：
      产业相对强弱 → 当天簇内所有有有效价格数据 ETF（板块整体）
      底部深度/修复 → 当天实际处于 DEEP/RECOVERING 的 ETF（这一轮底部有多深）

五维（等权为主，30/25/20/15/10 sensitivity）：
  Market / Industry relative / Bottom depth / Synchronization / Recovery
"""

from __future__ import annotations

import logging
from datetime import date
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import etf_signal_raw_dir

from .episodes import INDUSTRY_CLUSTERS
from .price_map import compute_row
from .state_odds import daily_state_series

logger = logging.getLogger(__name__)

# ═══ Study 2C Feature Set — 已冻结（2026-08-31）═══
# 五维 context 特征由用户确认锁定，改动必须过 Parity 且显式说明理由，
# 否则禁止新增/删除/重命名任何特征（会改变历史 episode 的 reference space 与匹配结果）。
FEATURE_SET_VERSION = "2C-v1"
FEATURE_SET_FROZEN_AT = "2026-08-31"

DIM_GROUPS = MappingProxyType({
    "market": "市场环境",
    "industry_relative": "产业相对强弱",
    "bottom_depth": "底部深度",
    "synchronization": "同步度",
    "recovery": "修复状态",
})
DIM_GROUP_ORDER = tuple(DIM_GROUPS.keys())

EQUAL_WEIGHTS = MappingProxyType({g: 0.2 for g in DIM_GROUP_ORDER})
SENS_WEIGHTS = MappingProxyType({
    "market": 0.30, "industry_relative": 0.25, "bottom_depth": 0.20,
    "synchronization": 0.15, "recovery": 0.10,
})

# 连续特征 → 维度组（冻结；进入距离的唯一定义）
CONTINUOUS_FEATURES = MappingProxyType({
    "market": ("market_ret_60d", "market_ret_120d", "market_breadth_60d"),
    "industry_relative": ("industry_excess_60d", "industry_excess_120d"),
    "bottom_depth": ("pos60", "pos120", "pos360", "distance_360", "dd60"),
    "synchronization": ("initial_participation_ratio", "entries_last_20d"),
    "recovery": ("deep_ratio", "recovering_ratio"),
})


def load_market_index() -> pd.DataFrame:
    """HS300 日线（覆盖 2002~2026，供 market 维度与 industry excess 基准）。"""
    df = pd.read_csv("data/raw/_benchmark_sh000300.csv", parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_full_etf_codes() -> list[str]:
    """729 FULL ETF 代码（breadth 横截面）。"""
    uni = pd.read_parquet("outputs/research/etf_bottom/universe.parquet")
    return uni["fund_code"].tolist()


def _ret_at(df: pd.DataFrame, dt: pd.Timestamp, days: int) -> float | None:
    """df 需含 date/close 排序；返回 dt 前最近日相对 days 交易日前收益。"""
    sub = df[df["date"] <= dt]
    if len(sub) < days + 1:
        return None
    return float(sub["close"].iloc[-1] / sub["close"].iloc[-1 - days] - 1.0)


def market_context(dt: pd.Timestamp, market: pd.DataFrame, breadth_60d: float | None) -> dict[str, Any]:
    """Market 维度：HS300 60/120D 趋势 + 全市场 breadth 60D。"""
    m60 = _ret_at(market, dt, 60)
    m120 = _ret_at(market, dt, 120)
    regime = None
    if breadth_60d is not None:
        regime = "RISK_ON" if breadth_60d > 0.02 else ("RISK_OFF" if breadth_60d < -0.02 else "NEUTRAL")
    return {
        "market_ret_60d": m60, "market_ret_120d": m120, "market_breadth_60d": breadth_60d,
        "market_regime": regime,
    }


def breadth_at(dt: pd.Timestamp, codes: list[str], cache: dict[str, pd.DataFrame]) -> float | None:
    """729 FULL ETF 在某日的 60D 收益横截面中位数（risk-on/off 判据）。"""
    rets = []
    for code in codes:
        d = cache.get(code) if code in cache else pd.read_parquet(f"data/etf_signal/raw/{code}.parquet",
                                               columns=["date", "close"]).sort_values("date").reset_index(drop=True)
        cache[code] = d
        v = _ret_at(d, dt, 60)
        if v is not None:
            rets.append(v)
    return float(np.median(rets)) if rets else None


def industry_context(dt: pd.Timestamp, cluster: str, market: pd.DataFrame,
                     cache: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Industry relative：簇内全部有效 ETF 相对 HS300 的 60/120D excess（板块整体当时怎么样）。"""
    excess60, excess120 = [], []
    for code in INDUSTRY_CLUSTERS[cluster]:
        d = cache.get(code) if code in cache else pd.read_parquet(f"data/etf_signal/raw/{code}.parquet",
                                               columns=["date", "close"]).sort_values("date").reset_index(drop=True)
        cache[code] = d
        r60 = _ret_at(d, dt, 60)
        r120 = _ret_at(d, dt, 120)
        m60 = _ret_at(market, dt, 60)
        m120 = _ret_at(market, dt, 120)
        if r60 is not None and m60 is not None:
            excess60.append(r60 - m60)
        if r120 is not None and m120 is not None:
            excess120.append(r120 - m120)
    mode = None
    if excess60:
        med = float(np.median(excess60))
        mode = "leading_up" if med > 0.03 else ("own_decline" if med < -0.03 else "following_market")
    return {
        "industry_excess_60d": float(np.median(excess60)) if excess60 else None,
        "industry_excess_120d": float(np.median(excess120)) if excess120 else None,
        "industry_relative_mode": mode,
    }


def bottom_depth_context(dt: pd.Timestamp, cluster: str,
                         cache: dict[str, pd.DataFrame]) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    """Bottom depth / Recovery：当天实际处于 DEEP/RECOVERING 的 ETF（这一轮底部当时有多深）。

    返回 (depth_feats, in_bottom, state_counts)：
      in_bottom = 当天处于 DEEP/RECOVERING 的 ETF 代码（供同步度用）
      state_counts = {"DEEP_BOTTOM": n, "RECOVERING_FROM_BOTTOM": n}（供 recovery ratio 用）
    """
    pos60, pos120, pos360, dist360, dd60 = [], [], [], [], []
    state_counts = {"DEEP_BOTTOM": 0, "RECOVERING_FROM_BOTTOM": 0}
    in_bottom: list[str] = []
    for code in INDUSTRY_CLUSTERS[cluster]:
        d = cache.get(code) if code in cache else pd.read_parquet(
            f"data/etf_signal/raw/{code}.parquet", columns=["date", "close"]).sort_values("date").reset_index(drop=True)
        cache[code] = d
        r = compute_row(code, "", "", d, dt)
        st = r.get("bottom_state")
        if st in ("DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"):
            in_bottom.append(code)
            state_counts[st] = state_counts.get(st, 0) + 1
        if r.get("price_pos_60") is not None:
            pos60.append(r["price_pos_60"])
        if r.get("price_pos_120") is not None:
            pos120.append(r["price_pos_120"])
        if r.get("price_pos_360") is not None:
            pos360.append(r["price_pos_360"])
        if r.get("distance_to_low_360") is not None:
            dist360.append(r["distance_to_low_360"])
        # 60D 回撤：close / rolling60 max - 1（当日）
        sub = d[d["date"] <= dt].reset_index(drop=True)
        if len(sub) >= 60:
            cur = float(sub["close"].iloc[-1])
            hi60 = float(sub["close"].iloc[-60:].max())
            dd60.append(cur / hi60 - 1.0)
    return {
        "pos60": float(np.median(pos60)) if pos60 else None,
        "pos120": float(np.median(pos120)) if pos120 else None,
        "pos360": float(np.median(pos360)) if pos360 else None,
        "distance_360": float(np.median(dist360)) if dist360 else None,
        "dd60": float(np.median(dd60)) if dd60 else None,
    }, in_bottom, state_counts

def synchronization_context(dt: pd.Timestamp, cluster: str, in_bottom: list[str],
                            cache: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Synchronization（start-date observable）：initial participation + entries last 20D。

    initial_participation_ratio = 当天 DEEP/RECOVERING ETF 数 / 当天有效 ETF 数
    entries_last_20d = 截至 start 日过去 20 交易日内簇内 ETF 新进入长期底部的 off→on 次数
    """
    valid = 0
    entries_20d = 0
    for code in INDUSTRY_CLUSTERS[cluster]:
        d = cache.get(code) if code in cache else pd.read_parquet(
            f"data/etf_signal/raw/{code}.parquet", columns=["date", "close"]).sort_values("date").reset_index(drop=True)
        cache[code] = d
        if len(d[d["date"] <= dt]) >= 1:
            valid += 1
        # entries last 20D：对完整历史截断到 dt 算 long_term off→on，再统计最近 20 交易日
        sub = d[d["date"] <= dt].reset_index(drop=True)
        if len(sub) >= 21:
            st = daily_state_series(sub)
            lt = st["long_term_bottom"].to_numpy(bool)
            on = lt & ~np.concatenate([[False], lt[:-1]])
            entries_20d += int(on[-20:].sum())
    denom = max(valid, 1)
    return {
        "initial_n_bottom": len(in_bottom),
        "initial_participation_ratio": round(len(in_bottom) / denom, 4),
        "entries_last_20d": entries_20d,
    }


def recovery_context(state_counts: dict[str, int], cluster: str, in_bottom: list[str]) -> dict[str, Any]:
    """Recovery：deep_ratio / recovering_ratio（分母=当天簇内有效 ETF 数）。

    already_improving = recovering_ratio > deep_ratio（用户口径）。
    """
    valid = max(len(INDUSTRY_CLUSTERS[cluster]), 1)
    deep = state_counts.get("DEEP_BOTTOM", 0)
    recovering = state_counts.get("RECOVERING_FROM_BOTTOM", 0)
    deep_ratio = round(deep / valid, 4)
    recovering_ratio = round(recovering / valid, 4)
    return {
        "deep_ratio": deep_ratio,
        "recovering_ratio": recovering_ratio,
        "already_improving": bool(recovering_ratio > deep_ratio) if (deep_ratio > 0 or recovering_ratio > 0) else None,
        "n_in_bottom": len(in_bottom),
    }
