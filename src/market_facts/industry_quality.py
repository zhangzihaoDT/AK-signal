"""Source-date and closure checks shared by industry facts and legacy-cache adapters."""
from __future__ import annotations

from typing import Iterable

import pandas as pd


# Kept to validate already-stored SW history. This is not a TDX fallback route.
PRIMARY_SOURCES = frozenset({"swsresearch", "swsresearch_analysis", "analysis_daily", "hist_sw"})


def classify_source_status(sources: Iterable[str] | None) -> str:
    srcs = {str(s).strip().lower() for s in (sources or [])}
    return "primary" if srcs & PRIMARY_SOURCES else "fallback"


def classify_data_status(source_status: str, is_complete: bool) -> str:
    if source_status == "primary":
        return "confirmed"
    return "complete" if is_complete else "partial"


def price_source_dates(frame: pd.DataFrame) -> pd.Series:
    """Return independently evidenced source dates; never infer fallback dates."""
    dates = pd.to_datetime(frame.get("source_trade_date", pd.Series(pd.NaT, index=frame.index)), errors="coerce")
    source = frame.get("source", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    missing = frame.get("source_trade_date", pd.Series(pd.NA, index=frame.index)).isna()
    primary = source.isin(PRIMARY_SOURCES) & missing
    return dates.where(~primary, pd.to_datetime(frame["trade_date"], errors="coerce"))


def verified_price_dates(frame: pd.DataFrame) -> pd.Series:
    """Per-row check that an independently evidenced source date matches trade_date."""
    if "trade_date" not in frame:
        return pd.Series(False, index=frame.index)
    return price_source_dates(frame).eq(pd.to_datetime(frame["trade_date"], errors="coerce"))


def observation_falls_on_closed_day(target_date: pd.Timestamp) -> bool:
    """Whether an observation date is a completed, closed Shanghai-market day.

    This checks session completeness only; it does not prove the price source date.
    """
    from src.common.trading_calendar import shanghai_now
    from datetime import datetime
    now = shanghai_now()
    today = now.date()
    target = target_date.date()
    if target < today:
        return True
    if target > today:
        return False
    return now.time() >= datetime.strptime("15:10", "%H:%M").time()
