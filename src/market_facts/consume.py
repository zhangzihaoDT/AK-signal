"""Pure offline snapshot -> existing trend/position calculations; no publication."""
from __future__ import annotations
from datetime import date

import pandas as pd

from .contract import validate_snapshot
from .calendar import SessionCalendar


def snapshot_frame(snapshot: dict) -> pd.DataFrame:
    s = validate_snapshot(snapshot)
    return pd.DataFrame(s["bars"]).rename(columns={"source_trade_date": "date"}).assign(
        date=lambda d: pd.to_datetime(d["date"]).astype("datetime64[ns]")) if s["bars"] else pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume", "turnover"])


def observe_snapshot(snapshot: dict, calendar: SessionCalendar | None,
                     benchmark: pd.DataFrame | None = None) -> dict:
    from src.trend_engine.indicators import add_indicators
    from src.trend_engine.scoring import score_latest_row
    from src.observation.cli import position_snapshot, position_change

    s = validate_snapshot(snapshot)
    frame = snapshot_frame(s)
    target = date.fromisoformat(s["target_trade_date"])
    assessment = calendar.assess(s) if calendar else None
    result = {"input_snapshot_id": s["snapshot_id"], "security_id": s["identity"]["security_id"],
              "target_trade_date": target.isoformat(), "price_basis": s["identity"]["price_basis"],
              "price_basis_verified": s["identity"]["price_basis_verified"],
              "identity_verified": s["identity"]["identity_verified"],
              "source_dates_verified": s["quality"]["source_dates_verified"],
              "calendar": assessment, "production_eligible": False,
              "trend": {"status": "UNAVAILABLE", "trend_score": None},
              "position": {"reliability": "CALENDAR_UNAVAILABLE", "long_term_bottom": None,
                           "bottom_state": "UNAVAILABLE", "bottom_change": "UNKNOWN"}}
    if not assessment:
        return result
    if not s["quality"]["target_present"] or assessment["target_is_session"] is not True or assessment["session_complete"] is not True:
        result["trend"]["reason"] = "TARGET_UNVERIFIED"
        result["position"]["reliability"] = "TARGET_UNVERIFIED"
        return result
    position = position_snapshot(frame, target.isoformat(), calendar.index())
    for n, reason in assessment["windows"].items():
        if reason != "OK":
            position[f"price_pos_{n}"] = None
            position[f"window_reason_{n}"] = reason
    if any(assessment["windows"][str(n)] != "OK" for n in (120, 360)):
        position.update(long_term_bottom=None, bottom_state="UNAVAILABLE",
                        reliability=next(assessment["windows"][str(n)] for n in (360, 120)
                                         if assessment["windows"][str(n)] != "OK"))
    prior = [d for d in calendar.days if d < target]
    previous = position_snapshot(frame, prior[-1].isoformat(), calendar.index()) if prior else {}
    # Previous window must also be in calendar coverage. An unknown predecessor
    # never manufactures an ENTERED/LEFT event.
    if len([d for d in prior if d >= calendar.start]) < 360:
        previous = {}
    position["bottom_change"] = position_change(position, previous)
    result["position"] = position
    reason = assessment["windows"]["120"]
    if reason != "OK" or frame[["open", "high", "low", "volume"]].isna().any().any():
        result["trend"]["reason"] = reason if reason != "OK" else "OHLCV_MISSING"
        return result
    indicators = add_indicators(frame)
    bench = pd.DataFrame() if benchmark is None else benchmark.copy()
    if not bench.empty:
        bench["date"] = pd.to_datetime(bench["date"]).astype("datetime64[ns]")
        bench = bench[bench.date <= pd.Timestamp(target)].sort_values("date")
        # Never silently reuse yesterday's benchmark as today's RS fact.
        if bench.date.duplicated().any() or not (bench.date == pd.Timestamp(target)).any():
            bench = pd.DataFrame()
    if not bench.empty:
        if "bench_return_20d" not in bench:
            bench["bench_return_20d"] = bench.close.pct_change(20)
        indicators = pd.merge_asof(indicators.sort_values("date"), bench[["date", "bench_return_20d"]],
                                    on="date", direction="backward")
        indicators["relative_strength_20d"] = indicators.return_20d - indicators.bench_return_20d
    else:
        indicators["relative_strength_20d"] = pd.NA
    score, _ = score_latest_row(indicators)
    last = indicators.iloc[-1]
    result["trend"] = {"status": "AVAILABLE", "trend_score": score,
                       "benchmark_status": "AVAILABLE" if pd.notna(last.relative_strength_20d) else "UNAVAILABLE",
                       "rule_version": "existing_stock_trend", **{
                           key: float(last[key]) if pd.notna(last[key]) else None for key in
                           ("close", "ma20", "ma60", "ma120", "ma20_slope", "ma60_slope",
                            "return_20d", "relative_strength_20d")}}
    return result
