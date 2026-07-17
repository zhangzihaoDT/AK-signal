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
from dataclasses import dataclass
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import (
    project_root, sw_industry_raw_dir, sw_industry_processed_dir,
    sw_industry_rps_output_dir, sw_industry_rps_config_path,
)
from src.common.run_context import RunContext
from src.common.manifest import write_run_manifest, read_latest_run
from . import data_source, storage, metrics, regimes, validator, report
from . import constituents as sw_constituents
from . import contribution as sw_contribution


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


@dataclass
class UpdateResult:
    status: str                                    # "completed" | "waiting_for_source" | "no_new_data"
    requested_target_date: str                     # YYYYMMDD
    source_latest_common_date: str | None           # YYYYMMDD
    target_ready: bool
    freshness_probe_performed: bool
    freshness_probe_code: str                      # 探针行业代码
    freshness_probe_source_latest_date: str | None  # 上游返回的最新日期
    freshness_probe_duration_seconds: float
    raw_covered: int
    active_count: int


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

def _freshness_probe(
    raw_dir: Path,
    active_codes: list[str],
    target_date_str: str,
) -> tuple[bool, str, date_type | None, float]:
    """对上游执行一次真实请求，探测源端最新日期。

    Returns:
        (performed, probe_code, source_latest_date, duration_seconds)
        performed=False 表示没有可用的探针行业。
    """
    logger = logging.getLogger("sw_industry_rps")
    if not active_codes:
        return False, "", None, 0.0

    probe_code = active_codes[0]
    logger.info("freshness probe: probing %s for target date %s ...", probe_code, target_date_str)
    t0 = time.monotonic()
    try:
        df = data_source.fetch_industry_hist(probe_code, end_date=target_date_str)
        dur = time.monotonic() - t0
        if not df.empty:
            src_latest = df["trade_date"].max().date()
            src_latest_str = src_latest.strftime("%Y%m%d")
            logger.info(
                "freshness probe: %s source_latest=%s (%.1fs)",
                probe_code, src_latest_str, dur,
            )
            return True, probe_code, src_latest, dur
        else:
            logger.info("freshness probe: %s returned empty (%.1fs)", probe_code, dur)
            return True, probe_code, None, dur
    except Exception as e:
        dur = time.monotonic() - t0
        logger.warning("freshness probe failed for %s: %s (%.1fs)", probe_code, e, dur)
        return True, probe_code, None, dur


def cmd_update(args: argparse.Namespace) -> UpdateResult:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    explicit_target = getattr(args, "target_date", "") or _today_str()

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data, run bootstrap first")
        return UpdateResult(
            status="failed", requested_target_date=explicit_target,
            source_latest_common_date=None, target_ready=False,
            freshness_probe_performed=False, freshness_probe_code="",
            freshness_probe_source_latest_date=None,
            freshness_probe_duration_seconds=0.0,
            raw_covered=0, active_count=0,
        )

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
        return UpdateResult(
            status="noop", requested_target_date=explicit_target,
            source_latest_common_date=None, target_ready=False,
            freshness_probe_performed=False, freshness_probe_code="",
            freshness_probe_source_latest_date=None,
            freshness_probe_duration_seconds=0.0,
            raw_covered=0, active_count=len(active_codes),
        )

    # ── Freshness probe ──────────────────────────────────────────────
    probe_performed, probe_code, probe_latest_date_obj, probe_duration = _freshness_probe(
        raw_dir, active_codes, explicit_target,
    )

    probe_latest_str = (
        probe_latest_date_obj.strftime("%Y%m%d")
        if probe_latest_date_obj is not None else None
    )

    request_date = pd.Timestamp(explicit_target).date()

    # 如果用户显式指定了目标日期，但源端尚未到达，立即停止
    if getattr(args, "target_date", "") and probe_latest_date_obj is not None and probe_latest_date_obj < request_date:
        logger.info(
            "source not ready: probe_latest=%s < requested=%s → waiting_for_source",
            probe_latest_str, explicit_target,
        )
        return UpdateResult(
            status="waiting_for_source",
            requested_target_date=explicit_target,
            source_latest_common_date=probe_latest_str,
            target_ready=False,
            freshness_probe_performed=probe_performed,
            freshness_probe_code=probe_code,
            freshness_probe_source_latest_date=probe_latest_str,
            freshness_probe_duration_seconds=round(probe_duration, 2),
            raw_covered=0, active_count=len(active_codes),
        )

    # 确定实际目标日期：由 probe 的最新日期决定（最多到 explicit_target）
    target_date = request_date
    if probe_latest_date_obj is not None and probe_latest_date_obj < request_date:
        target_date = probe_latest_date_obj
    target_date_str = target_date.strftime("%Y%m%d")

    # 检查本地是否已全部覆盖 target_date
    local_latest_dates = [storage.load_industry_latest_date(raw_dir, c) for c in active_codes]
    valid_local = [d for d in local_latest_dates if d is not None]
    local_common = min(valid_local) if valid_local else None

    logger.info(
        "target date: %s (probe_latest=%s, explicit=%s, local_min=%s)",
        target_date_str, probe_latest_str, explicit_target,
        local_common.strftime("%Y%m%d") if local_common else "N/A",
    )

    if local_common is not None and local_common >= target_date:
        logger.info("all %d active industries already have data >= %s — skipping fetch", len(active_codes), target_date_str)
        return UpdateResult(
            status="completed", requested_target_date=explicit_target,
            source_latest_common_date=target_date_str,
            target_ready=True,
            freshness_probe_performed=probe_performed,
            freshness_probe_code=probe_code,
            freshness_probe_source_latest_date=probe_latest_str,
            freshness_probe_duration_seconds=round(probe_duration, 2),
            raw_covered=len(active_codes), active_count=len(active_codes),
        )

    # ── Batch incremental fetch ──────────────────────────────────────
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

    # ── Summary ──────────────────────────────────────────────────────
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

    # 最后确认实际达成的共同日期
    final_latest_dates = [storage.load_industry_latest_date(raw_dir, c) for c in active_codes]
    final_valid = [d for d in final_latest_dates if d is not None]
    source_latest = max(final_valid) if final_valid else probe_latest_date_obj
    source_latest_str = source_latest.strftime("%Y%m%d") if source_latest is not None else None
    target_ready = source_latest is not None and source_latest >= request_date

    return UpdateResult(
        status="completed" if target_ready else "waiting_for_source",
        requested_target_date=explicit_target,
        source_latest_common_date=source_latest_str,
        target_ready=target_ready,
        freshness_probe_performed=probe_performed,
        freshness_probe_code=probe_code,
        freshness_probe_source_latest_date=probe_latest_str,
        freshness_probe_duration_seconds=round(probe_duration, 2),
        raw_covered=raw_covered,
        active_count=len(active_codes),
    )


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

    # 为首次进入强势区的行业计算成分股贡献穿透
    drilldown_results: list[dict] = []
    new_entry_codes = snapshot[snapshot.get("new_entry", 0) == 1]["industry_code"].tolist()
    if new_entry_codes:
        logger.info("computing contribution drilldown for %d new_entry industries ...", len(new_entry_codes))
        name_map = dict(zip(master["industry_code"], master["industry_name"]))
        for code in new_entry_codes:
            const_df = sw_constituents.fetch_constituent_list(code)
            if const_df.empty:
                continue
            ind_hist = storage.load_industry_raw(raw_dir, code)
            if ind_hist.empty:
                continue
            result = sw_contribution.compute_drilldown(
                industry_code=code,
                industry_name=name_map.get(code, ""),
                breakout_date=date_str,
                constituents=const_df,
                industry_hist=ind_hist,
                window=5,
            )
            if result.contribution_structure == "数据不足":
                continue
            cs = result.contribution_structure
            bs = result.breadth_structure
            cl = r"单核主导" if cs == "single_core" else \
                 r"集中领涨" if cs == "leader_concentrated" else \
                 r"多龙头带动" if cs == "multi_leader" else \
                 r"分散上涨" if cs == "distributed" else cs
            bl = r"广泛上涨" if bs == "broad" else \
                 r"中度扩散" if bs == "moderate" else \
                 r"少数带动" if bs == "narrow" else \
                 r"明显分化" if bs == "divergent" else bs
            drilldown_results.append({
                "industry_code": code,
                "window": result.window,
                "industry_return_pct": result.industry_return_pct,
                "proxy_return_pct": result.proxy_return_pct,
                "reconstruction_gap_pct": result.reconstruction_gap_pct,
                "pattern_display": f"{cl} × {bl}",
                "top_contributors": [
                    {"name": c.stock_name, "ret": c.stock_return_pct, "contribution": c.contribution_pct}
                    for c in result.top_contributors[:5]
                ],
            })

    # Build report (staging)
    csv_staging = reports_dir / f".staging_{date_str}.csv"
    html_staging = reports_dir / f".staging_{date_str}.html"
    snapshot.to_csv(csv_staging, index=False, encoding="utf-8-sig")

    metrics_valid = validator.validate_metrics(metrics_df)
    metrics_valid.missing_codes = inactive_codes
    # name_map may already be defined above; compute if drilldown was skipped
    if not new_entry_codes:
        name_map = dict(zip(master["industry_code"], master["industry_name"]))
    metrics_valid.missing_names = [name_map.get(c, c) for c in inactive_codes]

    csv_path, html_path = report.build_html(
        snapshot=snapshot, metrics=metrics_df,
        validator_result=metrics_valid,
        report_date=date_str, reports_dir=reports_dir,
        rotation_days=rotation_days,
        drilldown_results=drilldown_results,
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
# Drilldown — 强势区突破成分股贡献穿透
# ---------------------------------------------------------------------------

def cmd_drilldown(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    processed_dir = sw_industry_processed_dir()
    window = getattr(args, "window", 10)
    limit = getattr(args, "limit", 10)
    force_refresh = getattr(args, "force", False)

    import shutil
    from src.common.market_data import _cache_root as _md_cache_root

    if force_refresh:
        cache_root = _md_cache_root()
        if cache_root.exists():
            shutil.rmtree(str(cache_root))
            logger.info("forced refresh: cleared stock cache at %s", cache_root)

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data")
        return

    metrics_df = storage.load_metrics(processed_dir)
    if metrics_df.empty:
        logger.error("no metrics data, run calculate first")
        return

    latest_date = metrics_df["trade_date"].max()
    date_str = latest_date.strftime("%Y-%m-%d")
    snapshot = metrics_df[metrics_df["trade_date"] == latest_date].copy()

    # --retry-failed：清除上次失败的缓存条目
    retry_failed = getattr(args, "retry_failed", False)
    if retry_failed and not force_refresh:
        cache_root = _md_cache_root()
        date_dir = cache_root / latest_date.strftime("%Y%m%d") / f"window_{window}"
        if date_dir.exists():
            removed = 0
            kept = 0
            for f in date_dir.iterdir():
                if f.suffix != ".csv" or "_legulegu" in f.name:
                    kept += 1
                    continue
                df_small = pd.read_csv(f, nrows=1)
                if df_small.empty or "close" not in df_small.columns or df_small["close"].isna().all():
                    f.unlink()
                    removed += 1
                else:
                    kept += 1
            logger.info("retry-failed: removed %d EM cache entries, kept %d (incl. legulegu)", removed, kept)

    name_map = dict(zip(master["industry_code"], master["industry_name"]))

    # 筛选：new_entry 或 accelerating 且 RPS15 >= 80
    candidates = snapshot[
        ((snapshot.get("new_entry", 0) == 1) | (snapshot.get("accelerating", 0) == 1))
        & (snapshot.get("RPS15", 0) >= 80)
    ].copy()

    if candidates.empty:
        logger.info("no new_entry or accelerating industries found on %s", date_str)
        return

    candidates = candidates.sort_values("RPS15", ascending=False).head(limit)
    logger.info("found %d candidates to drill down on %s", len(candidates), date_str)

    results: list[sw_contribution.DrilldownResult] = []
    for idx, (_, cand) in enumerate(candidates.iterrows()):
        if idx > 0:
            time.sleep(4)

        code = cand["industry_code"]
        name = name_map.get(code, "")
        entry_date = latest_date.strftime("%Y%m%d")

        logger.info("[%d/%d] drilling %s (%s) ...", idx + 1, len(candidates), code, name)

        # 加载该行业历史行情
        ind_hist = storage.load_industry_raw(raw_dir, code)
        if ind_hist.empty:
            logger.warning("  no industry history for %s", code)
            continue

        # 获取成分股
        const_df = sw_constituents.fetch_constituent_list(code)
        if const_df.empty:
            logger.warning("  no constituent data for %s", code)
            continue
        logger.info("  constituents: %d stocks", len(const_df))

        # 计算贡献分解
        result = sw_contribution.compute_drilldown(
            industry_code=code,
            industry_name=name,
            breakout_date=entry_date,
            constituents=const_df,
            industry_hist=ind_hist,
            window=window,
        )
        results.append(result)

        # 简要输出
        ql = result.reconstruction_quality
        if ql in ("good", "moderate", "poor"):
            ql = {"good": "良好", "moderate": "一般", "poor": "较差"}.get(ql, ql)
        logger.info("  industry return (%dd): actual=%.2f%% proxy=%.2f%% gap=%.2fpp quality=%s",
                     window,
                     result.industry_return_pct, result.proxy_return_pct,
                     result.reconstruction_gap_pct, ql)
        logger.info("  coverage: weight=%.1f%% count=%.1f%% (%d stocks)",
                     result.weight_coverage * 100, result.count_coverage * 100,
                     result.num_constituents)
        cl = r"单核主导" if result.contribution_structure == "single_core" else \
             r"集中领涨" if result.contribution_structure == "leader_concentrated" else \
             r"多龙头带动" if result.contribution_structure == "multi_leader" else \
             r"分散上涨" if result.contribution_structure == "distributed" else result.contribution_structure
        bl = r"广泛上涨" if result.breadth_structure == "broad" else \
             r"中度扩散" if result.breadth_structure == "moderate" else \
             r"少数带动" if result.breadth_structure == "narrow" else \
             r"明显分化" if result.breadth_structure == "divergent" else result.breadth_structure
        logger.info("  contribution structure: %s (%s)", cl, result.contribution_structure)
        logger.info("  breadth structure:      %s (%s)", bl, result.breadth_structure)
        logger.info("  participation: %d / %d (%.1f%%)",
                     result.num_positive, result.num_constituents,
                     result.participation_rate * 100)
        logger.info("  HHI: %.4f, top1 weight: %.1f%%, top1 share: %.1f%%, top3 share: %.1f%%",
                     result.hhi, result.top1_weight, result.top1_share * 100, result.top3_share * 100)
        if result.top_contributors:
            logger.info("  top contributors:")
            for i, cr in enumerate(result.top_contributors[:5], 1):
                logger.info("    %d. %s (%s) w=%.1f%% ret=%.2f%% contrib=%.4f%%",
                             i, cr.stock_name, cr.stock_code,
                             cr.weight_pct, cr.stock_return_pct, cr.contribution_pct)

    # 输出汇总表格
    if results:
        print("")
        print("=" * 160)
        print(f"{'行业':<16} {'实际涨%':<8} {'代理涨%':<8} {'误差pp':<8} {'权覆%':<8} {'数覆%':<8} {'质量':<8} {'贡献结构':<14} {'广度结构':<12}")
        print("-" * 160)
        for r in results:
            cl = r"单核主导" if r.contribution_structure == "single_core" else \
                 r"集中领涨" if r.contribution_structure == "leader_concentrated" else \
                 r"多龙头带动" if r.contribution_structure == "multi_leader" else \
                 r"分散上涨" if r.contribution_structure == "distributed" else r.contribution_structure
            bl = r"广泛上涨" if r.breadth_structure == "broad" else \
                 r"中度扩散" if r.breadth_structure == "moderate" else \
                 r"少数带动" if r.breadth_structure == "narrow" else \
                 r"明显分化" if r.breadth_structure == "divergent" else r.breadth_structure
            ql = r.reconstruction_quality
            if ql in ("good", "moderate", "poor"):
                ql = {"good": "良好", "moderate": "一般", "poor": "较差"}.get(ql, ql)
            print(f"{r.industry_name:<16} {r.industry_return_pct:<8.2f} {r.proxy_return_pct:<8.2f} "
                  f"{r.reconstruction_gap_pct:<+8.2f} {r.weight_coverage:<8.1%} {r.count_coverage:<8.1%} "
                  f"{ql:<8} {cl:<14} {bl:<12}")
        print("=" * 160)

    # 可选 CSV 输出
    reports_dir = sw_industry_rps_output_dir()
    if results and getattr(args, "output_csv", False):
        out_path = reports_dir / f"drilldown_{date_str}.csv"
        rows = []
        for r in results:
            for i, cr in enumerate(r.top_contributors, 1):
                rows.append({
                    "industry_code": r.industry_code,
                    "industry_name": r.industry_name,
                    "breakout_date": r.breakout_date,
                    "window": r.window,
                    "industry_return": r.industry_return_pct,
                    "classification": r.classification,
                    "participation_rate": r.participation_rate,
                    "hhi": r.hhi,
                    "rank": i,
                    "stock_code": cr.stock_code,
                    "stock_name": cr.stock_name,
                    "weight": cr.weight_pct,
                    "stock_return": cr.stock_return_pct,
                    "contribution": cr.contribution_pct,
                    "cum_contribution": cr.cum_contribution_pct,
                })
        out_df = pd.DataFrame(rows)
        out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
        logger.info("drilldown CSV saved: %s", out_path)


# ---------------------------------------------------------------------------
# Run-day orchestration
# ---------------------------------------------------------------------------

def cmd_run_day(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("RUN-DAY STARTED")
    logger.info("=" * 60)

    t_start = time.monotonic()
    requested_target = getattr(args, "target_date", "") or _today_str()
    force_report = getattr(args, "force_report", False)

    # ── Step 1: Update ────────────────────────────────────────────────
    t0 = time.monotonic()
    result = cmd_update(args)
    fetch_dur = time.monotonic() - t0

    # ── Date gate ────────────────────────────────────────────────────
    if not result.target_ready:
        logger.info("")
        logger.info("─" * 60)
        logger.info("TARGET NOT READY — stopping pipeline")
        logger.info("  requested_target_date:  %s", result.requested_target_date)
        logger.info("  source_latest_common_date: %s", result.source_latest_common_date)
        logger.info("  freshness_probe_performed: %s", result.freshness_probe_performed)
        logger.info("  freshness_probe_code:    %s", result.freshness_probe_code)
        logger.info("  freshness_probe_source_latest_date: %s", result.freshness_probe_source_latest_date)
        logger.info("  freshness_probe_duration: %.2fs", result.freshness_probe_duration_seconds)
        logger.info("  target_ready:            false")
        logger.info("  status:                  %s", result.status)
        logger.info("  calculate:               skipped")
        logger.info("  report:                  skipped")
        logger.info("  manifest_completed:      not updated")
        logger.info("─" * 60)

        # 写一条非 completed 的 manifest 记录（不覆盖已有 completed）
        ctx = RunContext(
            subsystem="sw_industry_rps",
            run_date=datetime.now().date(),
            status=result.status,
            offline=True,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary={
                "requested_target_date": result.requested_target_date,
                "source_latest_common_date": result.source_latest_common_date or "",
                "freshness_probe_performed": result.freshness_probe_performed,
                "freshness_probe_code": result.freshness_probe_code,
                "freshness_probe_source_latest_date": result.freshness_probe_source_latest_date or "",
                "target_ready": False,
                "status": result.status,
            },
            artifacts=[],
        )
        write_run_manifest(ctx)
        return

    # ── 检查是否已有同日期 completed 报告（免重复发布）─────────────
    if not force_report and result.source_latest_common_date is not None:
        latest_ctx = read_latest_run("sw_industry_rps", require_status="completed")
        if latest_ctx is not None:
            published_date = latest_ctx.run_date.strftime("%Y%m%d")
            if result.source_latest_common_date <= published_date:
                logger.info("")
                logger.info("─" * 60)
                logger.info("NO NEW DATA — stopping pipeline")
                logger.info("  source_latest_common_date: %s", result.source_latest_common_date)
                logger.info("  latest published date:     %s", published_date)
                logger.info("  status:                    no_new_data")
                logger.info("  calculate:                 skipped")
                logger.info("  report:                    skipped")
                logger.info("  manifest_completed:        not updated")
                logger.info("  (use --force-report to override)")
                logger.info("─" * 60)

                ctx = RunContext(
                    subsystem="sw_industry_rps",
                    run_date=datetime.now().date(),
                    status="no_new_data",
                    offline=True,
                    started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    summary={
                        "source_latest_common_date": result.source_latest_common_date,
                        "latest_published_date": published_date,
                    },
                    artifacts=[],
                )
                write_run_manifest(ctx)
                return

    # ── Step 2: Calculate ──────────────────────────────────────────────
    t0 = time.monotonic()
    cmd_calculate(args)
    calc_dur = time.monotonic() - t0

    # ── Step 3: Report ─────────────────────────────────────────────────
    t0 = time.monotonic()
    cmd_report(args)
    report_dur = time.monotonic() - t0

    total_dur = time.monotonic() - t_start

    logger.info("")
    logger.info("=" * 60)
    logger.info("RUN-DAY SUMMARY")
    logger.info("  requested_target_date: %s", requested_target)
    logger.info("  source_latest_common_date: %s", result.source_latest_common_date)
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

    p_drill = sub.add_parser("drilldown", help="强势区突破成分股贡献穿透分析")
    p_drill.add_argument("--window", type=int, default=10, help="贡献分析回看窗口（交易日数，默认 10）")
    p_drill.add_argument("--limit", type=int, default=10, help="最多分析的行业数")
    p_drill.add_argument("--force", action="store_true", help="强制刷新所有缓存，重新获取")
    p_drill.add_argument("--retry-failed", action="store_true", help="仅重试上次失败的股票（不清除成功缓存）")
    p_drill.add_argument("--output-csv", action="store_true", help="输出 CSV 到 outputs/sw_industry_rps/")
    p_drill.add_argument("--log-level", default="INFO")

    p_run = sub.add_parser("run-day", help="依次执行 update → calculate → report")
    p_run.add_argument("--target-date", default="", help="目标日期 YYYYMMDD（默认今天）")
    p_run.add_argument("--force-report", action="store_true", help="允许对已有报告的日期重新生成报告")
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
        "drilldown": cmd_drilldown,
        "run-day": cmd_run_day,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
