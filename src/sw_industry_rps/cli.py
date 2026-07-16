"""
sw_industry_rps CLI

日常更新流程（run-day）：
  1. update   → 批量逐行业增量拉取（仅 active 124）
  2. validate → 校验原始数据完整性
  3. calculate → 幂等替换目标日期指标分区
  4. report   → 质量门控 → 原子发布 HTML/CSV + manifest
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.common.paths import (
    project_root, sw_industry_raw_dir, sw_industry_processed_dir,
    sw_industry_rps_output_dir, sw_industry_rps_config_path,
)
from src.common.run_context import RunContext
from src.common.manifest import write_run_manifest
from . import data_source, storage, metrics, regimes, validator, report


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("sw_industry_rps")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def load_config() -> dict:
    import yaml
    cfg_path = sw_industry_rps_config_path()
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Bootstrap (unchanged except atomic save)
# ---------------------------------------------------------------------------

def cmd_bootstrap(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    cfg = load_config()

    started_at = datetime.now(timezone.utc)

    logger.info("fetching industry master list...")
    master = data_source.fetch_industry_master()
    storage.save_master(master, raw_dir)
    logger.info("saved %d industries to master", len(master))

    codes = master["industry_code"].tolist()
    max_industries = cfg.get("bootstrap", {}).get("max_industries")
    min_days = cfg.get("bootstrap", {}).get("min_days", 250)
    start_date = cfg.get("bootstrap", {}).get("start_date", "20200101")
    if max_industries:
        codes = codes[:max_industries]

    name_map = dict(zip(master["industry_code"], master["industry_name"]))

    success = 0
    skipped_existing = 0
    failed: list[dict] = []
    total_rows = 0

    for i, code in enumerate(codes):
        existing = storage.load_industry_raw(raw_dir, code)
        if not existing.empty and len(existing) >= min_days:
            skipped_existing += 1
            total_rows += len(existing)
            continue

        logger.info("[%d/%d] fetching %s (%s)...", i + 1, len(codes), code, name_map.get(code, ""))
        t0 = time.monotonic()
        try:
            df = data_source.fetch_industry_hist(code, start_date=start_date, max_retries=3, base_delay=2.0, max_delay=30.0)
            elapsed = time.monotonic() - t0
            if not df.empty:
                df["industry_name"] = name_map.get(code, "")
                storage.save_industry_raw(df, raw_dir, code)
                success += 1
                total_rows += len(df)
                logger.info("  ok %s: %d rows (%.1fs)", code, len(df), elapsed)
            else:
                failed.append({"code": code, "name": name_map.get(code, ""), "error": "empty", "elapsed": round(elapsed, 1)})
        except Exception as e:
            elapsed = time.monotonic() - t0
            failed.append({"code": code, "name": name_map.get(code, ""), "error": str(e), "elapsed": round(elapsed, 1)})
            logger.warning("  fail %s: %s (%.1fs)", code, e, elapsed)
        time.sleep(random.uniform(2.0, 4.0))

    # Save active universe snapshot
    active, inactive = storage.compute_active_codes(raw_dir, master)[:2]
    storage.save_active_snapshot(active, raw_dir)
    logger.info("active universe snapshot saved: %d active, %d inactive", len(active), len(inactive))

    logger.info("bootstrap complete: %d success, %d skipped, %d failed", success, skipped_existing, len(failed))


# ---------------------------------------------------------------------------
# Update — checkpoint-resumable incremental fetch for active universe
# ---------------------------------------------------------------------------

def cmd_update(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    explicit_target = getattr(args, "target_date", "") or _today_str()

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data, run bootstrap first")
        return

    active_codes, inactive_codes, universe_changed = storage.compute_active_codes(raw_dir, master)
    logger.info("universe: master=%d, active=%d, inactive=%d%s",
                len(master), len(active_codes), len(inactive_codes),
                " UNIVERSE CHANGED" if universe_changed else "")

    if universe_changed:
        if getattr(args, "update_universe", False):
            inferred_active, _, _ = storage.compute_active_codes(raw_dir, master)
            logger.info("updating active universe snapshot: %d industries", len(inferred_active))
            storage.save_active_snapshot(inferred_active, raw_dir)
            active_codes, inactive_codes, _ = storage.compute_active_codes(raw_dir, master)
        else:
            logger.warning("inferred active universe differs from snapshot — run with --update-universe to accept")

    if getattr(args, "list_industries", False):
        name_map = dict(zip(master["industry_code"], master["industry_name"]))
        for code in inactive_codes:
            logger.info("  inactive: %s %s", code, name_map.get(code, ""))
        return

    # Determine effective target date: the latest date that ALL active industries share
    dates = [storage.load_industry_latest_date(raw_dir, c) for c in active_codes]
    valid_dates = [d for d in dates if d is not None]
    if valid_dates:
        latest_common = max(valid_dates)
        min_common = min(valid_dates)
        target_date = min(latest_common, pd.Timestamp(explicit_target).date())
    else:
        target_date = pd.Timestamp(explicit_target).date()
    target_date_str = target_date.strftime("%Y%m%d") if hasattr(target_date, "strftime") else str(target_date)

    logger.info("target date: %s (latest_common=%s, explicit=%s)", target_date, max(valid_dates) if valid_dates else "N/A", explicit_target)

    # Load checkpoint for resume
    cp = storage.load_checkpoint(raw_dir)
    if cp and cp.get("target_date") == target_date_str:
        completed = set(cp.get("completed_codes", []))
        failed_map = cp.get("failed_codes", {})
        logger.info("resuming from checkpoint: %d completed, %d failed", len(completed), len(failed_map))
    else:
        completed = set()
        failed_map: dict[str, int] = {}

    codes_to_fetch = [c for c in active_codes if c not in completed]
    random.shuffle(codes_to_fetch)

    fetch_start = time.monotonic()
    for code in codes_to_fetch:
        last_date = storage.load_industry_latest_date(raw_dir, code)
        if last_date is not None and last_date >= target_date:
            completed.add(code)
            continue

        cached = storage.load_industry_raw(raw_dir, code)
        inc_start = (last_date + timedelta(days=1)).strftime("%Y%m%d") if last_date is not None else "20200101"
        try:
            time.sleep(random.uniform(0.5, 1.5))
            df_new = data_source.fetch_industry_hist(code, start_date=inc_start, end_date=target_date_str)
            if not df_new.empty:
                merged = storage.merge_incremental(cached, df_new)
                storage.save_industry_raw(merged, raw_dir, code)
                completed.add(code)
                failed_map.pop(code, None)
            else:
                failed_map.setdefault(code, 0)
                failed_map[code] += 1
        except Exception as e:
            logger.warning("fetch failed for %s: %s", code, e)
            failed_map.setdefault(code, 0)
            failed_map[code] += 1

        storage.save_checkpoint(raw_dir, {
            "target_date": target_date_str,
            "active_count": len(active_codes),
            "completed_codes": sorted(completed),
            "failed_codes": {k: v for k, v in failed_map.items()},
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    fetch_elapsed = time.monotonic() - fetch_start

    # Summary
    still_missing = [c for c in active_codes if c not in completed]
    raw_covered = len(active_codes) - len(still_missing)
    logger.info("  completed:         %d", len(completed))
    logger.info("  still_missing:     %d", len(still_missing))
    logger.info("  raw_covered:       %d / %d", raw_covered, len(active_codes))
    logger.info("  fetch_duration:    %.1fs", fetch_elapsed)
    if still_missing:
        name_map = dict(zip(master["industry_code"], master["industry_name"]))
        logger.info("  missing codes:")
        for c in still_missing[:10]:
            logger.info("    %s %s", c, name_map.get(c, ""))
    logger.info("=" * 60)

    if raw_covered == len(active_codes):
        storage.clear_checkpoint(raw_dir)


# ---------------------------------------------------------------------------
# Calculate — idempotent target-date partition replacement
# ---------------------------------------------------------------------------

def validate_raw_completeness(raw_dir: Path, target_date: str, active_codes: list[str]) -> tuple[int, int, list[str]]:
    covered = 0
    missing: list[str] = []
    for code in active_codes:
        df = storage.load_industry_raw(raw_dir, code)
        if not df.empty and df["trade_date"].max() >= pd.Timestamp(target_date):
            covered += 1
        else:
            missing.append(code)
    return covered, len(active_codes), missing


def cmd_calculate(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    processed_dir = sw_industry_processed_dir()
    cfg = load_config()
    windows = cfg.get("rps", {}).get("windows", [5, 10, 15])

    full_rebuild = getattr(args, "full", False)
    target_date = getattr(args, "date", None)

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data, run bootstrap first")
        return

    active_codes, inactive_codes, universe_changed = storage.compute_active_codes(raw_dir, master)
    logger.info("universe: master=%d, active=%d, inactive=%d",
                len(master), len(active_codes), len(inactive_codes))

    if universe_changed:
        logger.warning("active universe has changed — run bootstrap or --update-universe to confirm")

    if not target_date:
        dates = [storage.load_industry_latest_date(raw_dir, c) for c in active_codes]
        valid_dates = [d for d in dates if d is not None]
        target_date = min(valid_dates).strftime("%Y-%m-%d") if valid_dates else _today_str()

    covered, expected, missing = validate_raw_completeness(raw_dir, target_date, active_codes)
    if covered < expected:
        logger.error("raw data incomplete: %d/%d active industries, cannot calculate", covered, expected)
        for c in missing[:10]:
            logger.error("  missing: %s", c)
        return

    # Load all active industry raw data
    all_hist: list[pd.DataFrame] = []
    for code in active_codes:
        df = storage.load_industry_raw(raw_dir, code)
        if not df.empty:
            df["industry_code"] = code
            name_row = master[master["industry_code"] == code]
            df["industry_name"] = name_row.iloc[0]["industry_name"] if not name_row.empty else ""
            all_hist.append(df)

    combined = pd.concat(all_hist, ignore_index=True)
    logger.info("computing metrics for %d rows across %d industries", len(combined), combined["industry_code"].nunique())

    result = metrics.compute_all_metrics(combined, windows=windows)

    # Merge with prior metrics: replace target_date partition, keep rest
    prior_metrics = storage.load_metrics(processed_dir)
    if full_rebuild:
        final = result
    elif target_date and not prior_metrics.empty:
        prior_no_target = prior_metrics[prior_metrics["trade_date"] != pd.Timestamp(target_date)]
        target_rows = result[result["trade_date"] == pd.Timestamp(target_date)]
        final = pd.concat([prior_no_target, target_rows], ignore_index=True)
        final = final.drop_duplicates(subset=["trade_date", "industry_code"], keep="last")
        final = final.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)
    elif not prior_metrics.empty:
        prior_max = prior_metrics["trade_date"].max()
        new_rows = result[result["trade_date"] > prior_max]
        final = pd.concat([prior_metrics, new_rows], ignore_index=True)
        final = final.drop_duplicates(subset=["trade_date", "industry_code"], keep="last")
        final = final.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)
    else:
        final = result

    # Apply regimes (needs full history for streak continuity)
    logger.info("applying regime identification...")
    strong_threshold = cfg.get("regimes", {}).get("strong_threshold", 90)
    observe_threshold = cfg.get("regimes", {}).get("observe_threshold", 80)
    strong_streak_min = cfg.get("regimes", {}).get("strong_streak_min_days", 3)
    delta_acc = cfg.get("regimes", {}).get("delta_acceleration", 10)
    final = regimes.identify_all_regimes(
        final,
        strong_threshold=strong_threshold,
        observe_threshold=observe_threshold,
        strong_streak_min=strong_streak_min,
        delta_acceleration=delta_acc,
    )

    storage.save_metrics_atomically(final, processed_dir)
    logger.info("metrics saved: %d rows", len(final))

    latest_date = final["trade_date"].max()
    snapshot = final[final["trade_date"] == latest_date].copy()
    storage.save_snapshot(snapshot, processed_dir)

    rotation_days = cfg.get("report", {}).get("rotation_days", 20)
    pivot = build_rotation_matrix(final, rotation_days)
    storage.save_rotation_matrix(pivot, processed_dir)

    logger.info("calculate complete: latest date=%s", latest_date.date())


def build_rotation_matrix(metrics_df: pd.DataFrame, rotation_days: int = 20) -> pd.DataFrame:
    if metrics_df.empty:
        return pd.DataFrame()
    latest = metrics_df["trade_date"].max()
    cutoff = latest - pd.Timedelta(days=rotation_days * 2)
    recent = metrics_df[metrics_df["trade_date"] >= cutoff].copy()
    pivot = recent.pivot_table(
        index="industry_code", columns="trade_date", values="RPS15", aggfunc="first"
    )
    date_cols = sorted(pivot.columns, reverse=True)[:rotation_days]
    pivot = pivot[list(reversed(date_cols))]
    pivot = pivot.sort_values(pivot.columns[-1], ascending=False)
    return pivot.reset_index()


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    processed_dir = sw_industry_processed_dir()

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data found")
        return

    raw_data: dict[str, pd.DataFrame] = {}
    for code in master["industry_code"].tolist():
        df = storage.load_industry_raw(raw_dir, code)
        if not df.empty:
            raw_data[code] = df

    raw_valid = validator.validate_raw_data(raw_data, master)
    logger.info("raw data: status=%s, valid=%d/%d", raw_valid.status, raw_valid.valid_industries, raw_valid.total_industries)

    metrics_df = storage.load_metrics(processed_dir)
    met_valid = validator.validate_metrics(metrics_df, latest_trade_date=raw_valid.date_max)
    logger.info("metrics: status=%s, rows=%d", met_valid.status, met_valid.total_processed_rows)

    logger.info("validation complete")


# ---------------------------------------------------------------------------
# Report — quality-gated atomic publish
# ---------------------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> None:
    _started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger = build_logger(args.log_level)
    processed_dir = sw_industry_processed_dir()
    reports_dir = sw_industry_rps_output_dir()
    raw_dir = sw_industry_raw_dir()
    cfg = load_config()
    rotation_days = cfg.get("report", {}).get("rotation_days", 20)

    metrics_df = storage.load_metrics(processed_dir)
    if metrics_df.empty:
        logger.error("no metrics data, run calculate first")
        return

    latest_date = metrics_df["trade_date"].max()
    date_str = latest_date.strftime("%Y%m%d")
    snapshot = metrics_df[metrics_df["trade_date"] == latest_date].copy()

    master = storage.load_master(raw_dir)
    active_codes, inactive_codes, universe_changed = storage.compute_active_codes(raw_dir, master)

    latest_industries = snapshot["industry_code"].nunique()
    all_active_covered = latest_industries == len(active_codes)

    if not all_active_covered:
        logger.error("snapshot has %d/%d active industries — refusing to publish", latest_industries, len(active_codes))
        ctx = RunContext(
            subsystem="sw_industry_rps", run_date=latest_date.date(),
            status="failed", offline=True, started_at=_started_at,
            summary={"total_industries": latest_industries, "active": len(active_codes), "date": date_str},
            artifacts=[],
        )
        write_run_manifest(ctx)
        return

    # Build report (staging)
    csv_staging = reports_dir / f".staging_{date_str}.csv"
    html_staging = reports_dir / f".staging_{date_str}.html"
    snapshot.to_csv(csv_staging, index=False, encoding="utf-8-sig")

    metrics_valid = validator.validate_metrics(metrics_df)
    metrics_valid.missing_codes = inactive_codes
    name_map = dict(zip(master["industry_code"], master["industry_name"]))
    metrics_valid.missing_names = [name_map.get(c, c) for c in inactive_codes]

    csv_path, html_path = report.build_html(
        snapshot=snapshot, metrics=metrics_df,
        validator_result=metrics_valid,
        report_date=date_str, reports_dir=reports_dir,
        rotation_days=rotation_days,
    )

    # Verify HTML was produced
    if not html_path.exists() or not csv_path.exists():
        logger.error("report generation failed — files missing")
        ctx = RunContext(
            subsystem="sw_industry_rps", run_date=latest_date.date(),
            status="failed", offline=True, started_at=_started_at,
            summary={"total_industries": latest_industries, "date": date_str},
            artifacts=[],
        )
        write_run_manifest(ctx)
        return

    report.save_latest_html(html_path, reports_dir)
    logger.info("report published: %s", html_path)

    # Cleanup staging
    for p in [csv_staging, html_staging]:
        if p.exists():
            p.unlink()

    ctx = RunContext(
        subsystem="sw_industry_rps", run_date=latest_date.date(),
        status="completed", offline=True, started_at=_started_at,
        summary={
            "total_industries": len(snapshot),
            "active": len(active_codes),
            "inactive": len(inactive_codes),
            "rps15_ge90": int((snapshot["RPS15"] >= 90).sum()) if "RPS15" in snapshot.columns else 0,
            "date": date_str,
        },
        artifacts=[str(csv_path), str(html_path)],
    )
    write_run_manifest(ctx)
    logger.info("manifest updated: completed")


# ---------------------------------------------------------------------------
# Run-day orchestration
# ---------------------------------------------------------------------------

def cmd_run_day(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("RUN-DAY STARTED")
    logger.info("=" * 60)

    t_start = time.monotonic()

    t0 = time.monotonic()
    cmd_update(args)
    fetch_dur = time.monotonic() - t0

    t0 = time.monotonic()
    cmd_calculate(args)
    calc_dur = time.monotonic() - t0

    t0 = time.monotonic()
    cmd_report(args)
    report_dur = time.monotonic() - t0

    total_dur = time.monotonic() - t_start

    logger.info("")
    logger.info("=" * 60)
    logger.info("RUN-DAY SUMMARY")
    logger.info("  fetch:     %.1fs", fetch_dur)
    logger.info("  calculate: %.1fs", calc_dur)
    logger.info("  report:    %.1fs", report_dur)
    logger.info("  total:     %.1fs", total_dur)
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="申万二级行业 RPS 监控")
    sub = p.add_subparsers(dest="command", required=True)

    p_bootstrap = sub.add_parser("bootstrap", help="初始化行业列表并拉取历史数据")
    p_bootstrap.add_argument("--log-level", default="INFO")

    p_update = sub.add_parser("update", help="增量更新行情（仅 active 124）")
    p_update.add_argument("--target-date", default="", help="目标日期 YYYYMMDD（默认今天）")
    p_update.add_argument("--list-industries", action="store_true", help="列出 active/inactive 行业")
    p_update.add_argument("--update-universe", action="store_true", help="确认并更新 active universe snapshot")
    p_update.add_argument("--log-level", default="INFO")

    p_calc = sub.add_parser("calculate", help="计算 RPS 指标（幂等替换目标日期分区）")
    p_calc.add_argument("--date", default="", help="目标日期 YYYY-MM-DD（默认最新）")
    p_calc.add_argument("--full", action="store_true", help="全量重建（慎用）")
    p_calc.add_argument("--log-level", default="INFO")

    p_report = sub.add_parser("report", help="生成 HTML/CSV 报告（质量门控）")
    p_report.add_argument("--log-level", default="INFO")

    p_validate = sub.add_parser("validate", help="检查数据质量")
    p_validate.add_argument("--log-level", default="INFO")

    p_run = sub.add_parser("run-day", help="依次执行 update → calculate → report")
    p_run.add_argument("--target-date", default="", help="目标日期 YYYYMMDD（默认今天）")
    p_run.add_argument("--log-level", default="INFO")

    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    dispatch = {
        "bootstrap": cmd_bootstrap,
        "update": cmd_update,
        "calculate": cmd_calculate,
        "report": cmd_report,
        "validate": cmd_validate,
        "run-day": cmd_run_day,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
