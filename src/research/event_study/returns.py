"""
价格簿与前向收益 — 为事件计算 5/10/20/60 日前向收益、基准超额、MFE/MAE。

数据源（全离线）：
  industry  行业 close pivot（1999→2026，124 行业）
  etf       全市场 ETF close pivot（2020→）
  stock     universe 个股 close（data/raw/CN_{symbol}.csv）

基准（避免依赖过期 HS300 缓存）：同实体宇宙的横截面中位前向收益
（与确认层「全市场中位」口径一致，全历史可用、确定性）。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import sw_industry_processed_dir, raw_dir
from src.sw_industry_rps import storage as sw_storage
from src.research.replay import engine as replay_engine
from src.selection.universe import load_universe_items
from src.common.paths import selection_universe_path
from src.trend_engine import inputs as trend_inputs

logger = logging.getLogger("research.event_study.returns")


def _stock_symbols() -> list[str]:
    items = load_universe_items(selection_universe_path())
    return [it.asset.symbol for it in trend_inputs.stock_items(items)]


def build_price_book(cache: dict[str, Any] | None = None) -> "PriceBook":
    """加载各实体类型的 close pivot。"""
    cache = cache or replay_engine.build_replay_cache()

    # 行业
    metrics = cache.get("metrics_df")
    if metrics is None or metrics.empty:
        metrics = sw_storage.load_metrics(sw_industry_processed_dir())
    ind_pivot = pd.DataFrame()
    if not metrics.empty:
        ind_pivot = metrics.pivot_table(index="trade_date", columns="industry_code",
                                        values="close", aggfunc="last")

    # ETF
    combined = cache.get("combined", pd.DataFrame())
    etf_pivot = pd.DataFrame()
    if not combined.empty:
        combined = combined.copy()
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        etf_pivot = combined.pivot_table(index="date", columns="fund_code",
                                         values="close", aggfunc="last")

    # 个股
    stock_rows: list[pd.DataFrame] = []
    for symbol in _stock_symbols():
        path = raw_dir() / f"CN_{symbol}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception:
            continue
        if df.empty:
            continue
        s = df.set_index("date")["close"]
        stock_rows.append(pd.DataFrame({symbol: s}))
    stock_pivot = pd.concat(stock_rows, axis=1) if stock_rows else pd.DataFrame()

    book = PriceBook({"industry": ind_pivot, "etf": etf_pivot, "stock": stock_pivot})
    logger.info("price book: industry=%s etf=%s stock=%s",
                ind_pivot.shape if not ind_pivot.empty else (0, 0),
                etf_pivot.shape if not etf_pivot.empty else (0, 0),
                stock_pivot.shape if not stock_pivot.empty else (0, 0))
    return book


class PriceBook:
    def __init__(self, pivots: dict[str, pd.DataFrame]):
        self.pivots = {k: v for k, v in pivots.items() if not v.empty}
        self._bench: dict[tuple[str, int], pd.Series] = {}

    def _index(self, entity_type: str) -> pd.Index:
        return self.pivots[entity_type].index

    def has(self, entity_type: str, code: str) -> bool:
        p = self.pivots.get(entity_type)
        return p is not None and code in p.columns

    def _loc(self, entity_type: str, tdate: Any) -> int | None:
        try:
            return self._index(entity_type).get_loc(pd.Timestamp(tdate))
        except KeyError:
            return None

    def forward_returns(self, entity_type: str, code: str, tdate: Any,
                        horizons: tuple[int, ...]) -> dict[int, float | None]:
        p = self.pivots.get(entity_type)
        if p is None or code not in p.columns:
            return {h: None for h in horizons}
        s = p[code]
        i = self._loc(entity_type, tdate)
        if i is None:
            return {h: None for h in horizons}
        base = float(s.iloc[i])
        if not base or np.isnan(base):
            return {h: None for h in horizons}
        out: dict[int, float | None] = {}
        for h in horizons:
            j = i + h
            if j < len(s) and pd.notna(s.iloc[j]):
                out[h] = float(s.iloc[j] / base - 1.0)
            else:
                out[h] = None
        return out

    def excursions(self, entity_type: str, code: str, tdate: Any,
                   horizons: tuple[int, ...]) -> dict[int, tuple[float | None, float | None]]:
        """close 口径的最大有利/不利变动（MFE/MAE）窗口 [t, t+h]。"""
        p = self.pivots.get(entity_type)
        if p is None or code not in p.columns:
            return {h: (None, None) for h in horizons}
        s = p[code]
        i = self._loc(entity_type, tdate)
        if i is None:
            return {h: (None, None) for h in horizons}
        base = float(s.iloc[i])
        if not base or np.isnan(base):
            return {h: (None, None) for h in horizons}
        out: dict[int, tuple[float | None, float | None]] = {}
        for h in horizons:
            window = s.iloc[i:i + h + 1].dropna()
            if len(window) < 2:
                out[h] = (None, None)
                continue
            out[h] = (float(window.max() / base - 1.0), float(window.min() / base - 1.0))
        return out

    def benchmark_forward(self, entity_type: str, tdate: Any,
                          horizons: tuple[int, ...]) -> dict[int, float | None]:
        """基准（同宇宙横截面中位）前向收益。"""
        p = self.pivots.get(entity_type)
        if p is None or p.empty:
            return {h: None for h in horizons}
        i = self._loc(entity_type, tdate)
        if i is None:
            return {h: None for h in horizons}
        out: dict[int, float | None] = {}
        for h in horizons:
            key = (entity_type, h)
            if key not in self._bench:
                med = (p.shift(-h) / p - 1.0).median(axis=1)
                self._bench[key] = med
            v = self._bench[key].iloc[i]
            out[h] = float(v) if pd.notna(v) else None
        return out
