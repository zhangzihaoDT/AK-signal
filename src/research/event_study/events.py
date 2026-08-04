"""
事件提取 — 从历史信号长表（historical_signals.parquet）提取状态转换事件。

以「状态转换」为事件，而非每天状态快照（避免样本重叠）：
  Entry Event   off → on    （首次进入信号态）
  Exit Event    on  → off   （离开信号态）

事件定义按层（layer）→ 状态字段 → on 值集合：
  Layer1  etf      trend_state         BUY_CANDIDATE / STRONG_WATCH（ETF 趋势门）
  Layer2  industry confirmation_status 观察 / 强势（RPS15≥80 确认证据）
  Layer3  stock    selection_status    RECOMMENDED
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger("research.event_study.events")

# 各层的信号态定义（layer → (状态字段, on 值集合)）
EVENT_DEFS: dict[str, dict[str, Any]] = {
    "1": {"field": "trend_state", "on": {"BUY_CANDIDATE", "STRONG_WATCH"},
          "label": "ETF 趋势门（BUY_CANDIDATE/STRONG_WATCH）"},
    "2": {"field": "confirmation_status", "on": {"观察", "强势"},
          "label": "行业确认（观察/强势，RPS15≥80）"},
    "3": {"field": "selection_status", "on": {"RECOMMENDED"},
          "label": "Selection RECOMMENDED"},
}

# 事件输出列
EVENT_COLUMNS = [
    "trade_date", "entity_type", "entity_code", "theme", "layer",
    "state", "event_type",          # entry / exit
    "rps15", "trend_score",
]


def _on_value(row: pd.Series, field: str, on_set: set[str]) -> bool:
    v = str(row.get(field, "") or "").strip()
    return v in on_set


def extract_events(
    signals: pd.DataFrame,
    layers: str = "123",
    *,
    start_date: str = "",
    end_date: str = "",
) -> pd.DataFrame:
    """从历史信号长表提取 entry/exit 事件。

    Args:
        signals: historical_signals DataFrame（SIGNAL_COLUMNS）
        layers: 参与的事件层位，如 "123"
        start_date / end_date: 事件发生日范围（YYYYMMDD，含边界）

    Returns:
        EVENT_COLUMNS 长表；按 (entity_type, entity_code, trade_date) 排序
    """
    if signals.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)

    rows: list[dict[str, Any]] = []
    for layer in "123":
        if layer not in layers:
            continue
        dfn = signals[signals["layer"] == layer]
        if dfn.empty:
            continue
        field = EVENT_DEFS[layer]["field"]
        on_set = EVENT_DEFS[layer]["on"]
        for (ety, code), g in dfn.groupby(["entity_type", "entity_code"]):
            g = g.sort_values("trade_date")
            prev_on = False
            for _, r in g.iterrows():
                tdate = str(r.get("trade_date", "") or "")
                if start_date and tdate < start_date:
                    continue
                if end_date and tdate > end_date:
                    break
                on = _on_value(r, field, on_set)
                if on and not prev_on:
                    rows.append(_event_row(r, layer, field, "entry", on_set))
                elif not on and prev_on:
                    rows.append(_event_row(r, layer, field, "exit", on_set))
                prev_on = on

    df = pd.DataFrame(rows, columns=EVENT_COLUMNS)
    if not df.empty:
        df = df.sort_values(["entity_type", "entity_code", "trade_date"]).reset_index(drop=True)
    logger.info("events extracted: %d rows (layers=%s, range=%s~%s)",
                len(df), layers, start_date or "*", end_date or "*")
    return df


def _event_row(r: pd.Series, layer: str, field: str, event_type: str, on_set: set[str]) -> dict[str, Any]:
    state = str(r.get(field, "") or "")
    return {
        "trade_date": str(r.get("trade_date", "") or ""),
        "entity_type": str(r.get("entity_type", "") or ""),
        "entity_code": str(r.get("entity_code", "") or ""),
        "theme": str(r.get("theme", "") or ""),
        "layer": layer,
        "state": state,
        "event_type": event_type,
        "rps15": _num(r.get("rps15")),
        "trend_score": _num(r.get("trend_score")),
    }


def _num(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None
