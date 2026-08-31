"""Study 1 前向收益 / MAE / MFE / 横截面基准。

基准（全离线、确定性）：同一天所有 FULL ETF 的中位前向收益
（与 event_study 口径一致，避免依赖过期 HS300 缓存）。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class ClosePanel:
    """FULL ETF 全市场 close 面板（date × fund_code）。"""

    def __init__(self, pivot: pd.DataFrame):
        self.pivot = pivot.sort_index()
        self._dates = self.pivot.index
        self._pos = {d: i for i, d in enumerate(self._dates)}
        self._bench_cache: dict[int, pd.Series] = {}

    def date_pos(self, date: Any) -> int | None:
        try:
            return self._pos[pd.Timestamp(date)]
        except KeyError:
            return None

    def forward_returns(self, code: str, date: Any, horizons: tuple[int, ...]) -> dict[int, float | None]:
        if code not in self.pivot.columns:
            return {h: None for h in horizons}
        i = self.date_pos(date)
        if i is None:
            return {h: None for h in horizons}
        base = float(self.pivot[code].iloc[i])
        if not base or np.isnan(base):
            return {h: None for h in horizons}
        out: dict[int, float | None] = {}
        for h in horizons:
            j = i + h
            if j < len(self.pivot) and pd.notna(self.pivot[code].iloc[j]):
                out[h] = float(self.pivot[code].iloc[j] / base - 1.0)
            else:
                out[h] = None
        return out

    def excursions(self, code: str, date: Any, horizons: tuple[int, ...]) -> dict[int, tuple[float | None, float | None]]:
        if code not in self.pivot.columns:
            return {h: (None, None) for h in horizons}
        i = self.date_pos(date)
        if i is None:
            return {h: (None, None) for h in horizons}
        base = float(self.pivot[code].iloc[i])
        if not base or np.isnan(base):
            return {h: (None, None) for h in horizons}
        s = self.pivot[code]
        out: dict[int, tuple[float | None, float | None]] = {}
        for h in horizons:
            window = s.iloc[i:i + h + 1].dropna()
            if len(window) < 2:
                out[h] = (None, None)
                continue
            out[h] = (float(window.max() / base - 1.0), float(window.min() / base - 1.0))
        return out

    def benchmark_forward(self, date: Any, horizons: tuple[int, ...]) -> dict[int, float | None]:
        """同市场横截面中位前向收益。"""
        i = self.date_pos(date)
        if i is None:
            return {h: None for h in horizons}
        out: dict[int, float | None] = {}
        for h in horizons:
            if h not in self._bench_cache:
                med = (self.pivot.shift(-h) / self.pivot - 1.0).median(axis=1)
                self._bench_cache[h] = med
            v = self._bench_cache[h].iloc[i]
            out[h] = float(v) if pd.notna(v) else None
        return out


def augment_events(events: pd.DataFrame, panel: ClosePanel, horizons: tuple[int, ...]) -> pd.DataFrame:
    """为事件追加前向收益 / 基准 / 超额 / MFE / MAE。"""
    rows: list[dict] = []
    for _, ev in events.iterrows():
        row = ev.to_dict()
        code, date = row["fund_code"], row["entry_date"]
        rets = panel.forward_returns(code, date, horizons)
        bench = panel.benchmark_forward(date, horizons)
        excur = panel.excursions(code, date, horizons)
        for h in horizons:
            r, b = rets.get(h), bench.get(h)
            mfe, mae = excur.get(h, (None, None))
            row[f"ret_{h}"] = r
            row[f"bench_{h}"] = b
            row[f"excess_{h}"] = (r - b) if (r is not None and b is not None) else None
            row[f"mfe_{h}"] = mfe
            row[f"mae_{h}"] = mae
        rows.append(row)
    return pd.DataFrame(rows)
