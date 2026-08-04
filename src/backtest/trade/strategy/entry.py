"""
入场规则（Entry Policy）— v0.5.2 第一轮。

入场条件：
  1. 资产首次进入趋势信号态（trend_state ∈ {BUY_CANDIDATE, STRONG_WATCH} 的 off→on 转换）；
  2. 当日所属主题行业确认成立（theme 焦点行业存在 观察/强势）。

Universe 范围（明确参数，不静默改变原逻辑）：
  configured      限定在 config/stock_universe.yaml 的主题资产池（8 AI + 6 高现金流 ETF）
  theme-matched   全市场按主题关键词命中的 ETF（44 / 12）——广义主题研究基线

注意：是否开仓由 trades 层持有状态门控（持仓期间再次 entry 不重复开仓）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import stock_universe_path, config_dir
from src.research.event_study.events import extract_events
from src.selection.universe import load_universe_items
from src.trend_engine import inputs as trend_inputs

# entity_type → 信号层
ENTITY_LAYER = {"etf": "1", "industry": "2", "stock": "3"}

UNIVERSE_MODES = ("configured", "theme-matched")


def configured_etf_codes(theme: str, universe_path: Path | None = None) -> list[str]:
    """config/stock_universe.yaml 中该主题的 ETF 资产池代码（theme_etf / sub_industry_etf）。"""
    items = load_universe_items(universe_path or stock_universe_path())
    return sorted(
        it.asset.symbol for it in items
        if it.theme == theme and not trend_inputs.is_stock_item(it)
    )


def theme_confirmed_dates(signals: pd.DataFrame, theme: str) -> set[str]:
    """主题确认成立日期集：该主题任一焦点行业 confirmation_status ∈ {观察, 强势}。"""
    l2 = signals[signals["layer"] == "2"] if not signals.empty else pd.DataFrame()
    if l2.empty:
        return set()
    t = l2[l2["theme"] == theme]
    return set(t[t["confirmation_status"].isin({"观察", "强势"})]["trade_date"].astype(str))


def universe_size(signals: pd.DataFrame, theme: str, mode: str) -> int:
    if mode == "configured":
        return len(configured_etf_codes(theme))
    l1 = signals[signals["layer"] == "1"] if not signals.empty else pd.DataFrame()
    if l1.empty:
        return 0
    return int(l1[l1["theme"] == theme]["entity_code"].nunique())


def universe_config_hash(mode: str) -> str:
    """Universe 定义来源的配置指纹：
    configured → stock_universe.yaml；theme-matched → themes_two_directions.yaml（关键词）。"""
    rel = "stock_universe.yaml" if mode == "configured" else "themes_two_directions.yaml"
    path = config_dir() / rel
    h = hashlib.sha256()
    h.update(f"universe_mode:{mode}".encode("utf-8"))
    if path.exists():
        try:
            h.update(path.read_bytes())
        except Exception:
            pass
    else:
        h.update(f"missing:{rel}".encode("utf-8"))
    return h.hexdigest()[:16]


def entry_candidates(
    signals: pd.DataFrame,
    *,
    entity_type: str = "etf",
    theme: str = "",
    layers: str = "",
    universe_mode: str = "theme-matched",
) -> pd.DataFrame:
    """入场候选：指定实体类型 + 主题的 entry 事件（趋势信号态 off→on）。

    universe_mode="configured" 时仅保留主题资产池内的 ETF。
    """
    if universe_mode not in UNIVERSE_MODES:
        raise ValueError(f"unknown universe_mode: {universe_mode} (options: {UNIVERSE_MODES})")
    layer = layers or ENTITY_LAYER.get(entity_type, "1")
    ev = extract_events(signals, layers=layer)
    if ev.empty:
        return ev
    ev = ev[(ev["event_type"] == "entry") & (ev["entity_type"] == entity_type)]
    if theme:
        ev = ev[ev["theme"] == theme]
    if universe_mode == "configured" and theme:
        codes = set(configured_etf_codes(theme))
        ev = ev[ev["entity_code"].astype(str).isin(codes)]
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
