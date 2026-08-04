"""
事件研究编排 — 事件提取 + 前向收益计算 + 分组统计（v0.5.1）。

输入：historical_signals 长表（Replay 产物）
输出：事件级明细（含前向收益/超额/MFE/MAE）+ 分组汇总
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from .events import extract_events
from .returns import PriceBook, build_price_book

logger = logging.getLogger("research.event_study.study")

DEFAULT_HORIZONS = (5, 10, 20, 60)


def _num(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _biz_days_between(a: Any, b: Any) -> int:
    try:
        s = pd.Timestamp(a).strftime("%Y-%m-%d")
        e = pd.Timestamp(b).strftime("%Y-%m-%d")
        return int(np.busday_count(
            np.datetime64(s), np.datetime64(e), weekmask="1111100"))
    except Exception:
        return 0


def count_non_overlap(g: pd.DataFrame, horizon: int) -> int:
    """组内（按 entity_code）互不重叠的事件数：相邻事件间隔 ≥ horizon 个交易日。"""
    total = 0
    for _code, sub in g.groupby("entity_code"):
        sub = sub.sort_values("trade_date")
        last: Any = None
        for t in sub["trade_date"]:
            if last is None or _biz_days_between(last, t) >= horizon:
                total += 1
                last = t
    return total


def augment_events(
    events: pd.DataFrame,
    book: PriceBook,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    """为每个事件追加前向收益 / 基准 / 超额 / MFE/MAE。"""
    rows: list[dict[str, Any]] = []
    for _, ev in events.iterrows():
        ety, code, tdate = ev["entity_type"], ev["entity_code"], ev["trade_date"]
        rets = book.forward_returns(ety, code, tdate, horizons)
        bench = book.benchmark_forward(ety, tdate, horizons)
        excur = book.excursions(ety, code, tdate, horizons)
        row: dict[str, Any] = ev.to_dict()
        for h in horizons:
            r, b = rets.get(h), bench.get(h)
            mfe, mae = excur.get(h, (None, None))
            row[f"ret_{h}"] = r
            row[f"bench_{h}"] = b
            row[f"excess_{h}"] = (r - b) if (r is not None and b is not None) else None
            row[f"mfe_{h}"] = mfe
            row[f"mae_{h}"] = mae
        rows.append(row)
    df = pd.DataFrame(rows)
    return df


def _agg_stat(s: pd.Series) -> dict[str, Any]:
    if s.empty:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    vals = s.dropna()
    if vals.empty:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {
        "n": int(len(vals)),
        "mean": round(float(vals.mean()), 4),
        "median": round(float(vals.median()), 4),
        "win_rate": round(float((vals > 0).mean()), 4),
    }


def aggregate(
    events_df: pd.DataFrame,
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    """按 (entity_type, theme, layer, event_type, horizon) 汇总。"""
    if events_df.empty:
        return pd.DataFrame()
    out: list[dict[str, Any]] = []
    for (ety, theme, layer, evt), g in events_df.groupby(
            ["entity_type", "theme", "layer", "event_type"]):
        for h in horizons:
            ret = pd.to_numeric(g.get(f"ret_{h}"), errors="coerce")
            bench = pd.to_numeric(g.get(f"bench_{h}"), errors="coerce")
            exc = pd.to_numeric(g.get(f"excess_{h}"), errors="coerce")
            mfe = pd.to_numeric(g.get(f"mfe_{h}"), errors="coerce")
            mae = pd.to_numeric(g.get(f"mae_{h}"), errors="coerce")
            rec: dict[str, Any] = {
                "entity_type": ety, "theme": theme or "未分类",
                "layer": layer, "event_type": evt, "horizon": h,
                "n_events": int(len(g)),
                "n_non_overlap": count_non_overlap(g, h),
                "ret": _agg_stat(ret),
                "bench": _agg_stat(bench),
                "excess": _agg_stat(exc),
                "mfe": _agg_stat(mfe),
                "mae": _agg_stat(mae),
            }
            out.append(rec)
    return pd.DataFrame(out)


def run_event_study(
    signals: pd.DataFrame,
    *,
    layers: str = "123",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    start_date: str = "",
    end_date: str = "",
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对历史信号长表执行事件研究。

    Returns:
        {"events": 事件明细 df, "summary": 分组汇总 df, "horizons": [...],
         "layers": str, "start_date": str, "end_date": str}
    """
    events = extract_events(signals, layers, start_date=start_date, end_date=end_date)
    if events.empty:
        logger.warning("no events extracted (layers=%s range=%s~%s)", layers, start_date or "*", end_date or "*")
        return {"events": events, "summary": pd.DataFrame(), "horizons": list(horizons),
                "layers": layers, "start_date": start_date, "end_date": end_date}

    book = build_price_book(cache)
    events_df = augment_events(events, book, horizons)
    summary = aggregate(events_df, horizons)
    logger.info("event study: %d events, %d summary rows", len(events_df), len(summary))
    return {"events": events_df, "summary": summary, "horizons": list(horizons),
            "layers": layers, "start_date": start_date, "end_date": end_date}
