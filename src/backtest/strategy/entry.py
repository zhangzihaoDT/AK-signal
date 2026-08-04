"""
入场规则（Entry Policy）— v0.5.2 第一轮。

入场条件：
  1. 资产首次进入趋势信号态（trend_state ∈ {BUY_CANDIDATE, STRONG_WATCH} 的 off→on 转换）；
  2. 当日所属主题行业确认成立（theme 焦点行业存在 观察/强势）。

注意：是否开仓由 trades 层持有状态门控（持仓期间再次 entry 不重复开仓）。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.research.event_study.events import extract_events

# entity_type → 信号层
ENTITY_LAYER = {"etf": "1", "industry": "2", "stock": "3"}


def theme_confirmed_dates(signals: pd.DataFrame, theme: str) -> set[str]:
    """主题确认成立日期集：该主题任一焦点行业 confirmation_status ∈ {观察, 强势}。"""
    l2 = signals[signals["layer"] == "2"] if not signals.empty else pd.DataFrame()
    if l2.empty:
        return set()
    t = l2[l2["theme"] == theme]
    return set(t[t["confirmation_status"].isin({"观察", "强势"})]["trade_date"].astype(str))


def entry_candidates(
    signals: pd.DataFrame,
    *,
    entity_type: str = "etf",
    theme: str = "",
    layers: str = "",
) -> pd.DataFrame:
    """入场候选：指定实体类型 + 主题的 entry 事件（趋势信号态 off→on）。"""
    layer = layers or ENTITY_LAYER.get(entity_type, "1")
    ev = extract_events(signals, layers=layer)
    if ev.empty:
        return ev
    ev = ev[(ev["event_type"] == "entry") & (ev["entity_type"] == entity_type)]
    if theme:
        ev = ev[ev["theme"] == theme]
    return ev.reset_index(drop=True)


def apply_theme_confirmation(
    entries: pd.DataFrame,
    signals: pd.DataFrame,
    theme: str,
) -> pd.DataFrame:
    """仅保留「行业确认成立」日期的入场事件。"""
    confirmed = theme_confirmed_dates(signals, theme)
    if not confirmed:
        return pd.DataFrame(columns=entries.columns)
    return entries[entries["trade_date"].astype(str).isin(confirmed)].reset_index(drop=True)
