"""全市场交易日历（Market Calendar）— right-censor / 窗口判定的唯一基准。

口径（用户锁定，Lane 3 §写死点 ①）：
  - right-censor 判定用「全市场交易日历」，不用单只 ETF 自身报价行数。
  - first_exit + N 交易日 = 该日期之后存在 N 个**全市场交易日**，与个股是否停牌无关。
  - 停牌/缺失不能把 N 日窗口人为拉长；个体 OHLCV 完整度单独用 forward_data_complete 记录。

本模块只做日历推导，不制造任何研究事实；对 v1_signal_daily 的 trade_date 全集取并集，
得出市场统一交易日序列（与 scanner._market_prev_trade_date 同一精神，但面向窗口三段）。

内部以 Python datetime.date 存储（类型稳定、可哈希、可排序）；对外接口统一转 pd.Timestamp。
"""

from __future__ import annotations

import datetime as _dt
from typing import Iterable

import pandas as pd

_Date = _dt.date


class MarketCalendar:
    """全市场统一排序交易日。"""

    def __init__(self, dates: Iterable):
        arr = pd.to_datetime(list(dates)).dropna()  # 丢弃 NaT（真实日历无 NaT）
        idx = pd.DatetimeIndex(arr.unique()).sort_values()
        clean: list[_Date] = []
        for x in idx:
            d = pd.Timestamp(x).date()
            clean.append(d)  # type: ignore[arg-type]
        self._dates = clean
        self._n = len(self._dates)
        self._pos: dict[_Date, int] = {d: i for i, d in enumerate(self._dates)}

    @classmethod
    def from_v1(cls, v1: pd.DataFrame) -> "MarketCalendar":
        return cls(v1["trade_date"].unique())

    @property
    def dates(self) -> list[_Date]:
        return self._dates

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self._dates[0])  # type: ignore[return-value]

    @property
    def end(self) -> pd.Timestamp:
        return pd.Timestamp(self._dates[-1])  # type: ignore[return-value]

    def date_pos(self, d) -> int | None:
        try:
            return self._pos[pd.Timestamp(d).date()]  # type: ignore[index]
        except (KeyError, ValueError):
            return None

    def is_trade_date(self, d) -> bool:
        return self.date_pos(d) is not None

    def trade_days_between(self, start, end) -> int:
        """start 与 end 之间（含 start 不含 end）的市场交易日数。"""
        a, b = self.date_pos(start), self.date_pos(end)
        if a is None or b is None or b < a:
            return 0
        return b - a

    def after_ndays(self, start, n: int) -> pd.Timestamp | None:
        """start 之后第 n 个市场交易日；若不足则 None。"""
        i = self.date_pos(start)
        if i is None:
            return None
        j = i + n
        if j >= self._n:
            return None
        return pd.Timestamp(self._dates[j])  # type: ignore[return-value]

    def has_complete_window(self, start, n: int) -> bool:
        """start 之后是否存在完整的 n 个市场交易日。"""
        i = self.date_pos(start)
        if i is None:
            return False
        return i + n < self._n
