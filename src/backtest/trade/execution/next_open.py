"""
撮合 — T+1 开盘价成交。

规则：
  - 信号日 T（收盘）→ 下一交易日开盘价成交（实体自身交易日历）；
  - 信号日无下一交易日价格（数据末端 / 停牌 / 缺失开盘价）→ 返回 None，订单标记 unfilled。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def next_open(
    open_series: pd.Series | None,
    date: Any,
) -> tuple[str | None, float | None]:
    """给定信号日，返回 (下一交易日 YYYYMMDD, 开盘价)；无下一交易日则 (None, None)。"""
    if open_series is None or open_series.empty:
        return None, None
    try:
        ts = pd.Timestamp(date)
    except Exception:
        return None, None
    idx = open_series.index.get_indexer([ts], method=None)[0]
    if idx < 0:
        # 信号日不在序列中：取插入点（下一个可用交易日）
        idx = open_series.index.searchsorted(ts) - 1
    j = idx + 1
    if j >= len(open_series):
        return None, None
    fill_date = open_series.index[j]
    price = open_series.iloc[j]
    if pd.isna(price):
        return None, None
    return fill_date.strftime("%Y%m%d"), float(price)
