"""
退出规则（Exit Policy）— 独立实验，不做策略库。

v0.5.2 退出策略（可参数化）：
  signal_exit    信号退出：趋势信号态 on→off（使用 signals 中的 exit 事件）
  ma_exit        MA 退出：收盘价 < MA{W}（MA 只用判断日当日及以前数据，as-of；W 默认 20）
  fixed_horizon  固定持有 N 个交易日（按交易日，非自然日）

统一成交语义：退出信号日 X → 下一交易日开盘成交（T+1 open）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.research.event_study.events import extract_events

EXIT_POLICIES = ("signal_exit", "ma_exit", "fixed_horizon")


def exit_event_dates(signals: pd.DataFrame, entity_code: str) -> list[str]:
    """该实体的退出事件日期（on→off，升序）。"""
    ev = extract_events(signals, layers="1")
    sub = ev[(ev["event_type"] == "exit") & (ev["entity_code"] == entity_code)]
    return sorted(sub["trade_date"].astype(str).tolist())


def signal_exit_date(exit_dates: list[str], after_date: str) -> str | None:
    """信号退出：after_date 之后的第一个 exit 事件日。"""
    for d in exit_dates:
        if d > after_date:
            return d
    return None


def ma_exit_date(
    close_series: pd.Series,
    entry_fill_date: Any,
    window: int = 20,
) -> str | None:
    """MA 退出：自入场成交日起首个 close < MA{window} 的交易日。

    MA = close 过去 window 日（含当日）均值，只用判断日当日及以前数据。
    """
    if close_series is None or close_series.empty:
        return None
    ma = close_series.rolling(window, min_periods=window).mean()
    mask = close_series < ma
    start = pd.Timestamp(entry_fill_date)
    for d in close_series.index[close_series.index >= start]:
        if mask.get(d, False):
            return d.strftime("%Y%m%d")
    return None


def fixed_horizon_exit_signal_date(
    dates: pd.Index,
    entry_fill_date: Any,
    horizon: int,
) -> str | None:
    """固定持有 horizon 个交易日：退出信号日 = 入场成交日后第 (horizon-1) 个交易日，
    成交于下一交易日开盘 → 实际持有 horizon 个交易日。"""
    try:
        i = dates.get_loc(pd.Timestamp(entry_fill_date))
    except KeyError:
        return None
    j = i + horizon - 1
    if j >= len(dates):
        return None
    return dates[j].strftime("%Y%m%d")
