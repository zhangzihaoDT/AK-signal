"""Study 1 价格状态与事件提取。

状态（仅用事件日及之前数据，无 look-ahead）：
- P756：当前 close 在过去 756 个交易日（含当日）中的百分位（0-100）
- DD30 / DD120：当前 close 相对窗口内最高 close 的回撤
- MA20 / MA60：滚动均值
- PRICE_LOW = P756 ≤ 20
- PRICE_LOW_DD30 = PRICE_LOW 且 DD30 ≤ -20%
- MA20_RECOVERY / MA60_RECOVERY = 自低位进入后的首个收盘站上对应 MA 的交易日

事件语义：
- 连续低位区间合并：只有 off→on 转换点记为一个 entry，避免每日重复计数
- recovery 事件从同一个 entry 派生，记录 days_low_to_ma20/ma60
- 未恢复（censored）：days_* 为 None，单独统计，不与恢复样本混解释
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import (
    P_WINDOW, DD30_WINDOW, DD120_WINDOW, DD30_THRESHOLD, P_LOW_THRESHOLD,
    MA20_WINDOW, MA60_WINDOW, CORP_ACTION_RET,
)

logger = logging.getLogger(__name__)


def _pct_rank_rolling(close: np.ndarray, window: int) -> np.ndarray:
    """当前值在过去 window 个值（含自身）中的百分位（0-100）。"""
    n = len(close)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = close[i - window + 1:i + 1]
        out[i] = np.count_nonzero(w <= w[-1]) / window * 100.0
    return out


def _corp_action_flags(close: np.ndarray) -> np.ndarray:
    """单日 |ret| ≥ 20% 标记份额折算/异常行情（审计用，不剔除）。"""
    rets = np.diff(close) / close[:-1]
    flags = np.zeros(len(close), dtype=bool)
    flags[1:] = np.abs(rets) >= CORP_ACTION_RET
    return flags


def compute_states(
    d: pd.DataFrame,
    p_low_threshold: float = P_LOW_THRESHOLD,
    dd30_threshold: float = DD30_THRESHOLD,
) -> pd.DataFrame:
    """计算单只 ETF 的每日状态列。

    Args:
        d: 日线 DataFrame（含 date/close/fund_code）
        p_low_threshold: P756 低位阈值（默认 20）
        dd30_threshold: DD30 深跌阈值（默认 -0.20）
    """
    df = d.sort_values("date").reset_index(drop=True).copy()
    close = df["close"].to_numpy(dtype=float)

    df["ma20"] = df["close"].rolling(MA20_WINDOW, min_periods=MA20_WINDOW).mean()
    df["ma60"] = df["close"].rolling(MA60_WINDOW, min_periods=MA60_WINDOW).mean()
    df["p756"] = _pct_rank_rolling(close, P_WINDOW)
    df["dd30"] = df["close"] / df["close"].rolling(DD30_WINDOW, min_periods=DD30_WINDOW).max() - 1.0
    df["dd120"] = df["close"] / df["close"].rolling(DD120_WINDOW, min_periods=DD120_WINDOW).max() - 1.0
    df["corp_action"] = _corp_action_flags(close)

    df["price_low"] = df["p756"] <= p_low_threshold
    df["price_low_dd30"] = df["price_low"] & (df["dd30"] <= dd30_threshold)
    df["above_ma20"] = df["close"] > df["ma20"]
    df["above_ma60"] = df["close"] > df["ma60"]
    return df


def extract_events(states: pd.DataFrame, dd30_threshold: float = DD30_THRESHOLD) -> list[dict[str, Any]]:
    """从状态序列提取 entry / recovery 事件。

    事件类型：
      PRICE_LOW        低位区间进入点（off→on）
      PRICE_LOW_DD30   低位且深跌进入点（同日 PRICE_LOW）
      MA20_RECOVERY    同低位段内首个收盘站上 MA20 的日子（派生自 PRICE_LOW entry）
      MA60_RECOVERY    同低位段内首个收盘站上 MA60 的日子（派生自 PRICE_LOW entry）
    """
    low = states["price_low"].to_numpy(bool)
    n = len(states)
    entries: list[dict[str, Any]] = []

    above20 = states["above_ma20"].to_numpy(bool)
    above60 = states["above_ma60"].to_numpy(bool)

    # 上穿检测：k 日站上均线，且 k-1 日在均线下方（排除 entry 当天已在均线上方的瞬态 V 反转）
    cross_up20 = np.zeros(n, dtype=bool)
    cross_up60 = np.zeros(n, dtype=bool)
    for k in range(1, n):
        if above20[k] and not above20[k - 1]:
            cross_up20[k] = True
        if above60[k] and not above60[k - 1]:
            cross_up60[k] = True

    i = 0
    while i < n:
        if not low[i]:
            i += 1
            continue
        seg_start = i
        # 低位段结束位置
        j = i
        while j < n and low[j]:
            j += 1
        seg_end = j  # exclusive
        # 低位段内首次「从下方上穿」MA20 / MA60（等待市场证明不再恶化）
        rec20 = rec60 = None
        for k in range(seg_start, seg_end):
            if rec20 is None and cross_up20[k]:
                rec20 = k
            if rec60 is None and cross_up60[k]:
                rec60 = k
            if rec20 is not None and rec60 is not None:
                break
        base = {
            "fund_code": states["fund_code"].iloc[0],
            "entry_date": states["date"].iloc[seg_start],
            "seg_start_idx": seg_start,
            "seg_end_idx": seg_end - 1,
            "days_in_low": seg_end - seg_start,
            "left_censored": bool(seg_start == P_WINDOW - 1),
            "dd30_at_entry": float(states["dd30"].iloc[seg_start]) if pd.notna(states["dd30"].iloc[seg_start]) else None,
            "dd120_at_entry": float(states["dd120"].iloc[seg_start]) if pd.notna(states["dd120"].iloc[seg_start]) else None,
            "corp_action_in_seg": bool(states["corp_action"].iloc[seg_start:seg_end].any()),
            "days_low_to_ma20": (rec20 - seg_start) if rec20 is not None else None,
            "days_low_to_ma60": (rec60 - seg_start) if rec60 is not None else None,
        }
        if base["left_censored"]:
            # 起点无法观察真实入场时间，恢复时长未知（左截断），不参与恢复分布
            base["days_low_to_ma20"] = None
            base["days_low_to_ma60"] = None
        entries.append(base)
        i = seg_end

    # 展开为事件行（PRICE_LOW / PRICE_LOW_DD30 / recovery 共用 entry 派生）
    rows: list[dict[str, Any]] = []
    for e in entries:
        rows.append({**e, "event_type": "PRICE_LOW"})
        if e["dd30_at_entry"] is not None and e["dd30_at_entry"] <= dd30_threshold:
            rows.append({**e, "event_type": "PRICE_LOW_DD30"})
        if e["days_low_to_ma20"] is not None:
            rows.append({**e, "event_type": "MA20_RECOVERY"})
        if e["days_low_to_ma60"] is not None:
            rows.append({**e, "event_type": "MA60_RECOVERY"})
    return rows
