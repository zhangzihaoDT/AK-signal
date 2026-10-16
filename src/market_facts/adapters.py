"""Offline legacy-cache adapters. Unknown provenance stays unknown."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
from pathlib import Path

import pandas as pd

from .contract import SeriesIdentity, build_snapshot


def snapshot_from_frame(frame: pd.DataFrame, identity: SeriesIdentity, target: date,
                        fetched_at: datetime, *, date_column="date",
                        source_dates_verified=False, close_only=False) -> dict:
    """Adapt without filling, deduplicating, joining or certifying legacy dates.

    `fetched_at` is the wrapper creation time, not the legacy acquisition time.
    This distinction is retained in the returned provenance.
    """
    d = frame.copy()
    dates = pd.to_datetime(d[date_column], errors="raise")
    if dates.isna().any() or any(x != x.normalize() for x in dates):
        raise ValueError("legacy dates must be daily labels")
    d[date_column] = dates
    d = d[d[date_column] <= pd.Timestamp(target)]
    rows = []
    for _, r in d.iterrows():
        def number(key):
            v = r.get(key)
            return None if v is None or pd.isna(v) else float(v)
        rows.append({"source_trade_date": r[date_column].date().isoformat(),
                     "date_evidence": "source_dated_daily" if source_dates_verified or bool(r.get("_date_verified", False)) else "legacy_date_unverified",
                     **{k: (None if close_only and k in {"open", "high", "low"} else number(k))
                        for k in ("open", "high", "low", "close", "volume")},
                     "bar_kind": "close_only" if close_only else "ohlcv",
                     "legacy_source": str(r.get("source", "unknown")) if pd.notna(r.get("source")) else "unknown",
                     "turnover": number("turnover") if "turnover" in d else number("amount")})
    result = build_snapshot(identity, rows, target, fetched_at)
    result["provenance"] = {"wrapped_at": fetched_at.isoformat(), "original_fetched_at": "unknown",
                            "source_dates_verified": source_dates_verified,
                            "projection": "close_only" if close_only else "ohlcv",
                            "excluded_fields": ["open", "high", "low"] if close_only else []}
    return result


def snapshot_from_cache(path: Path, identity: SeriesIdentity, target: date,
                        wrapped_at: datetime, *, date_column="date", close_only=False) -> dict:
    # A caller's source label is only a declaration; old files alone do not prove
    # a single provider/basis for their full history.
    frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    if date_column == "trade_date" and "source" in frame:
        from src.sw_industry_rps.source_status import verified_price_dates
        frame["_date_verified"] = verified_price_dates(frame)
    result = snapshot_from_frame(frame, identity, target, wrapped_at, date_column=date_column, close_only=close_only)
    result["provenance"].update(cache_path=str(path), cache_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 provider_history_verified=False)
    return result


def snapshot_from_akshare_frame(frame: pd.DataFrame, asset, provider: str, adjustment: str,
                               target: date, fetched_at: datetime) -> dict:
    """Explicit source response boundary; never route fallback or merge history here.

    Caller must pass the response of the named endpoint, with canonical columns.
    Unlike a legacy cache this is a source-labelled response, still uncertified
    for corporate actions and independent security identity.
    """
    from .longbridge_source import identity_for
    if asset.market != 'CN' or provider not in {'em','sina'}:
        raise ValueError('unsupported source response')
    if adjustment not in {'qfq','none'}:
        raise ValueError('unsupported source adjustment')
    canonical = identity_for(asset)
    from dataclasses import replace
    endpoint = 'stock_zh_a_hist' if provider == 'em' else 'stock_zh_a_daily'
    symbol = str(asset.symbol).zfill(6)
    if provider == 'sina':
        symbol = ('sh' if asset.exchange == 'SSE' else 'sz') + symbol
    identity = replace(canonical,provider=provider,adapter='akshare',endpoint=endpoint,
                       provider_symbol=symbol,price_basis='vendor_forward_adjusted' if adjustment=='qfq' else 'unadjusted')
    return snapshot_from_frame(frame,identity,target,fetched_at,source_dates_verified=True)
