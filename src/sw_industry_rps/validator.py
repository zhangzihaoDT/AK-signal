from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass
class ValidationResult:
    status: str = "usable"  # usable | partial | failed
    total_industries: int = 0
    valid_industries: int = 0
    date_min: str | None = None
    date_max: str | None = None
    issues: list[str] = field(default_factory=list)
    failed_industries: list[str] = field(default_factory=list)
    total_raw_rows: int = 0
    total_processed_rows: int = 0
    latest_trade_date: str | None = None
    rps_strong_count: int = 0
    report_path: str | None = None


def validate_raw_data(
    raw_data: dict[str, pd.DataFrame],
    master: pd.DataFrame,
    expected_min_industries: int = 100,
) -> ValidationResult:
    result = ValidationResult()
    issues: list[str] = []
    failed_codes: list[str] = []
    total_rows = 0
    all_dates: list[pd.Timestamp] = []
    valid_count = 0

    for code, df in raw_data.items():
        if df is None or df.empty:
            failed_codes.append(code)
            continue
        total_rows += len(df)
        valid_count += 1
        if "trade_date" in df.columns:
            dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
            all_dates.extend(dates.tolist())
        if "close" in df.columns:
            bad_close = df["close"].isna().sum() + (df["close"] <= 0).sum()
            if bad_close > 0:
                issues.append(f"{code}: {bad_close} invalid close values")
        if "volume" in df.columns:
            bad_vol = df["volume"].isna().sum()
            if bad_vol > 0 and bad_vol > len(df) * 0.5:
                issues.append(f"{code}: {bad_vol}/{len(df)} missing volume")

    result.total_industries = len(master)
    result.valid_industries = valid_count
    result.total_raw_rows = total_rows
    result.failed_industries = failed_codes

    if valid_count < expected_min_industries:
        issues.append(f"valid industries ({valid_count}) < expected ({expected_min_industries})")
        result.status = "partial"

    if failed_codes:
        issues.append(f"failed industries: {len(failed_codes)}")
        if len(failed_codes) > valid_count * 0.3:
            result.status = "failed"

    if all_dates:
        result.date_min = str(min(all_dates).date())
        result.date_max = str(max(all_dates).date())

    if not all_dates:
        issues.append("no valid dates found")
        result.status = "failed"

    dup_codes = master["industry_code"].duplicated().sum()
    if dup_codes > 0:
        issues.append(f"{dup_codes} duplicate industry codes in master")

    result.issues = issues
    return result


def validate_metrics(
    metrics: pd.DataFrame,
    latest_trade_date: str | None = None,
    expected_universe_size: int | None = None,
) -> ValidationResult:
    result = ValidationResult()
    issues: list[str] = []
    stats_notes: list[str] = []
    integrity_ok = True

    if metrics.empty:
        result.status = "failed"
        result.issues = ["metrics dataframe is empty"]
        return result

    result.total_processed_rows = len(metrics)
    result.total_industries = metrics["industry_code"].nunique() if "industry_code" in metrics.columns else 0
    result.date_min = str(metrics["trade_date"].min().date()) if "trade_date" in metrics.columns else None
    result.date_max = str(metrics["trade_date"].max().date()) if "trade_date" in metrics.columns else None
    result.latest_trade_date = result.date_max

    if latest_trade_date and result.date_max:
        if result.date_max < latest_trade_date:
            issues.append(f"metrics max date ({result.date_max}) < expected ({latest_trade_date})")
            integrity_ok = False

    latest = metrics["trade_date"].max()
    latest_industries = metrics[metrics["trade_date"] == latest]["industry_code"].nunique()
    result.valid_industries = latest_industries

    if expected_universe_size and latest_industries < expected_universe_size:
        issues.append(
            f"latest date ({latest.date()}) has {latest_industries}/{expected_universe_size} industries"
        )
        integrity_ok = False
    elif expected_universe_size:
        stats_notes.append(f"latest date ({latest.date()}): {latest_industries}/{expected_universe_size} industries")

    rps_cols = [c for c in metrics.columns if c.startswith("RPS")]
    for col in rps_cols:
        if col not in metrics.columns:
            continue
        vals = metrics[col].dropna()
        if (vals < 0).any():
            issues.append(f"{col} contains negative values")
            integrity_ok = False
        if (vals > 100).any():
            issues.append(f"{col} contains values > 100")
            integrity_ok = False
        max_rps = vals.max()
        min_rps = vals.min()
        if max_rps < 99:
            stats_notes.append(f"{col} max ({max_rps:.1f}) < 99")
        if min_rps > 1:
            stats_notes.append(f"{col} min ({min_rps:.1f}) > 1")

    for col in ["return_5", "return_10", "return_15"]:
        if col in metrics.columns:
            vals = metrics[col].dropna()
            extreme = (vals.abs() > 0.5).sum()
            if extreme > len(metrics) * 0.02:
                stats_notes.append(f"{col}: {extreme} extreme values (> ±50%)")

    if "delta_rps15" in metrics.columns:
        vals = metrics["delta_rps15"].dropna()
        extreme = (vals.abs() > 50).sum()
        if extreme > 0:
            stats_notes.append(f"delta_rps15: {extreme} extreme changes (> ±50)")

    if not integrity_ok:
        result.status = "partial"
    else:
        result.status = "usable"

    result.issues = issues + stats_notes
    return result


def check_duplicate_runs(processed_dir: str, metrics_path: str) -> list[str]:
    from pathlib import Path
    import glob
    import os

    issues = []
    report_dir = os.path.join(processed_dir, "..", "reports", "sw_industry_rps")
    report_dir = os.path.normpath(report_dir)
    pattern = os.path.join(report_dir, "sw_industry_rps_*.csv")
    files = glob.glob(pattern)
    if len(files) > 1:
        dates = set()
        for f in files:
            stem = Path(f).stem
            date_part = stem.split("_")[-1]
            if len(date_part) == 8 and date_part.isdigit():
                dates.add(date_part)
        if len(dates) < len(files):
            issues.append(f"duplicate report dates detected ({len(files)} files, {len(dates)} unique dates)")
    return issues
