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
    sw_industry_rps_output_dir, industry_data_config_path,
)
from src.common.run_context import RunContext
from src.common.manifest import write_run_manifest, read_latest_run
from src.common import warnings as run_warnings
from . import data_source, storage, metrics, regimes, validator, report
from . import constituents as sw_constituents
from . import contribution as sw_contribution
from . import confirmation, ths_mapping
from . import structure


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
    cfg_path = industry_data_config_path()
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


def _default_target_date() -> str:
    now = datetime.now()
    if now.hour < 15 or (now.hour == 15 and now.minute < 10):
        days_back = 1
        while True:
            candidate = now - timedelta(days=days_back)
            if candidate.weekday() < 5:
                return candidate.strftime("%Y%m%d")
            days_back += 1
    return now.strftime("%Y%m%d")


@dataclass
class UpdateResult:
    status: str                                    # "completed" | "completed_provisional" | "waiting_for_source"
    requested_target_date: str                     # YYYYMMDD
    source_latest_common_date: str | None           # YYYYMMDD
    target_ready: bool
    update_source: str = ""                        # "analysis_daily" | "realtime" | "hist_sw"
    freshness_probe_performed: bool = False
    freshness_probe_code: str = ""                 # 探针标识
    freshness_probe_source_latest_date: str | None = None
    freshness_probe_duration_seconds: float = 0.0
    raw_covered: int = 0
    active_count: int = 0


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

def _probe_analysis_daily(
    target_date_str: str,
) -> tuple[str, date_type | None, float]:
    """Layer 1 probe: 用 analysis_daily 一次探测目标日期是否有正式数据。

    Returns:
        (status, source_date, duration_seconds)
        status = "confirmed"  (source_date == target_date)
               | "stale"      (source_date < target_date, 上游尚未更新)
               | ""           (无数据或异常)
    """
    logger = logging.getLogger("sw_industry_rps")
    request_date = pd.Timestamp(target_date_str).date()
    logger.info("freshness probe L1: index_analysis_daily_sw for target %s ...", target_date_str)
    t0 = time.monotonic()
    try:
        df = data_source.fetch_industry_analysis_daily(
            symbol="二级行业",
            start_date=target_date_str,
            end_date=target_date_str,
        )
        dur = time.monotonic() - t0
        if not df.empty:
            src_latest = df["trade_date"].max().date()
            count = df["industry_code"].nunique()
            if src_latest == request_date:
                logger.info("  L1 confirmed: %d industries, date=%s (%.1fs)", count, src_latest, dur)
                return "confirmed", src_latest, dur
            else:
                logger.warning("  L1 stale: requested=%s, source=%s (%d industries, %.1fs)",
                               target_date_str, src_latest, count, dur)
                return "stale", src_latest, dur
        logger.info("  L1 empty: no data for %s (%.1fs)", target_date_str, dur)
        return "", None, dur
    except Exception as e:
        dur = time.monotonic() - t0
        logger.warning("  L1 failed: %s (%.1fs)", e, dur)
        return "", None, dur


def _probe_realtime() -> tuple[str, date_type | None, float]:
    """Layer 2 probe: 用 realtime 获取实时行情（收盘后判断是否完整）。"""
    logger = logging.getLogger("sw_industry_rps")
    logger.info("freshness probe L2: index_realtime_sw ...")
    t0 = time.monotonic()
    try:
        df = data_source.fetch_industry_realtime(symbol="二级行业")
        dur = time.monotonic() - t0
        if not df.empty:
            count = df["industry_code"].nunique()
            logger.info("  L2 probe: %d industries (%.1fs)", count, dur)
            return "realtime", None, dur
        logger.info("  L2 probe: empty response (%.1fs)", dur)
        return "", None, dur
    except Exception as e:
        dur = time.monotonic() - t0
        logger.warning("  L2 probe failed: %s (%.1fs)", e, dur)
        return "", None, dur


def _log_reconciliation(raw_dir: Path, active_codes: list[str], target_date_str: str) -> None:
    """比较正式数据到来前 provisional 与现有正式数据的 close 差异。"""
    logger = logging.getLogger("sw_industry_rps")
    target = pd.Timestamp(target_date_str)
    diffs: list[float] = []
    replaced_count = 0
    for code in active_codes:
        df = storage.load_industry_raw(raw_dir, code)
        if df.empty:
            continue
        prov = df[(df["trade_date"] == target) & (df.get("data_status", "") == "provisional")]
        if prov.empty:
            continue
        # 跟上一笔正式日线比
        prev = df[(df["trade_date"] < target) & (df.get("data_status", "") != "provisional")]
        if prev.empty:
            continue
        prev_close = prev.sort_values("trade_date").iloc[-1]["close"]
        prov_close = prov.iloc[0]["close"]
        if pd.notna(prev_close) and pd.notna(prov_close) and prev_close != 0:
            diff_pct = abs(prov_close / prev_close - 1) * 100
            diffs.append(diff_pct)
        replaced_count += 1
    if diffs:
        diffs_sorted = sorted(diffs)
        logger.info("  reconciliation: %d industries replaced", replaced_count)
        logger.info("    max close diff:   %.4f%%", max(diffs))
        logger.info("    median close diff: %.4f%%", diffs_sorted[len(diffs_sorted) // 2])
        logger.info("    industries > 0.05%%: %d", sum(1 for d in diffs if d > 0.05))
    elif replaced_count > 0:
        logger.info("  reconciliation: %d industries replaced (close diff N/A)", replaced_count)


def _build_realtime_provisional(
    rt_df: pd.DataFrame,
    raw_dir: Path,
    active_codes: list[str],
    target_date_str: str,
    now: datetime | None = None,
) -> pd.DataFrame:
    """realtime 基底 → 全部 active 行业的 provisional 收盘。

    realtime 的 close 为申万指数真实值，覆盖全部 124 个申万二级：
      - 目标日 < 今天（上午跑，target=T-1）：用 昨收盘（= T-1 收盘）
      - 目标日 == 今天（收盘后跑，target=T）：用 最新价（= T 收盘）

    Returns:
        df columns: industry_code, trade_date, close, pct_chg, data_status, source, fetched_at
    """
    logger = logging.getLogger("sw_industry_rps")
    target = pd.Timestamp(target_date_str).date()
    today = (now or datetime.now()).date()

    code_col = "industry_code" if "industry_code" in rt_df.columns else "指数代码"
    if code_col not in rt_df.columns:
        logger.warning("  realtime missing code column")
        return pd.DataFrame()
    prev_col = "prev_close" if "prev_close" in rt_df.columns else "昨收盘"
    close_col = "close" if "close" in rt_df.columns else "最新价"
    if prev_col not in rt_df.columns or close_col not in rt_df.columns:
        logger.warning("  realtime missing close columns")
        return pd.DataFrame()

    use_latest = (target == today)
    rows: list[dict[str, Any]] = []
    for _, r in rt_df.iterrows():
        code = str(r[code_col]).strip()
        if not code.endswith(".SI"):
            code = f"{code}.SI"
        if code not in active_codes:
            continue
        raw_close = float(r[close_col]) if use_latest else float(r[prev_col])
        if pd.isna(raw_close) or raw_close <= 0:
            continue
        prev_df = storage.load_industry_raw(raw_dir, code)
        if prev_df.empty or "trade_date" not in prev_df.columns:
            continue
        prev_row = prev_df[prev_df["trade_date"] < pd.Timestamp(target)]
        if prev_row.empty:
            continue
        base_close = float(prev_row["close"].iloc[-1])
        if pd.isna(base_close) or base_close <= 0:
            continue
        pct = raw_close / base_close - 1.0
        rows.append({
            "industry_code": code,
            "trade_date": target,
            "close": round(raw_close, 4),
            "pct_chg": round(pct * 100.0, 4),
            "data_status": "provisional",
            "source": "realtime",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })
    df = pd.DataFrame(rows)
    logger.info("  realtime base provisional: %d industries (use_latest=%s)", len(df), use_latest)
    return df


def _fetch_ths_enrichment(
    raw_dir: Path,
    active_codes: list[str],
    target_date_str: str,
    lookback_days: int = 45,
) -> pd.DataFrame:
    """同花顺行业板块日线 → 成交额/成交量增强字段（best-effort，失败不影响基底）。

    Returns:
        df columns: industry_code, volume, amount, ths_source
    """
    logger = logging.getLogger("sw_industry_rps")
    target = pd.Timestamp(target_date_str).date()
    active_set = set(active_codes)

    rows: list[dict[str, Any]] = []
    boards = [b for b in ths_mapping.mapped_boards()
              if ths_mapping.lookup_sw_code(b) in active_set]
    logger.info("  THS enrichment: %d boards, target=%s", len(boards), target_date_str)

    start = (target - timedelta(days=lookback_days)).strftime("%Y%m%d")
    for board in boards:
        try:
            hist = data_source.fetch_board_industry_index_ths(board, start_date=start, end_date=target_date_str)
            if hist.empty:
                continue
            mask = hist["trade_date"].dt.date == target
            if not mask.any():
                continue
            idx = mask.idxmax()
            code = ths_mapping.lookup_sw_code(board)
            rows.append({
                "industry_code": code,
                "volume": hist.loc[idx, "volume"],
                "amount": hist.loc[idx, "amount"],
                "ths_source": "ths_board",
            })
        except Exception as e:
            logger.debug("  THS board %s enrichment failed: %s", board, e)
    return pd.DataFrame(rows)


def cmd_update(args: argparse.Namespace) -> UpdateResult:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    explicit_target = getattr(args, "target_date", "") or _default_target_date()

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data, run bootstrap first")
        return UpdateResult(status="failed", requested_target_date=explicit_target)

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
        return UpdateResult(status="noop", requested_target_date=explicit_target)

    request_date = pd.Timestamp(explicit_target).date()

    # ═══════════════════════════════════════════════════════════════════
    # Phase 1: index_analysis_daily_sw — 一次获取全部行业 single call
    # ═══════════════════════════════════════════════════════════════════
    probe_status, probe_date, probe_dur = _probe_analysis_daily(explicit_target)
    update_source = ""
    target_ready = False
    target_date_str = explicit_target

    if probe_status == "confirmed":
        # ── Reconciliation: 比较正式数据与 provisional 差异 ─────────────────
        _log_reconciliation(raw_dir, active_codes, target_date_str)

        # 上游已有目标交易日 → 正式数据，一次拉取全部行业
        logger.info("L1 confirmed: batch fetching via index_analysis_daily_sw ...")
        t0 = time.monotonic()
        try:
            df_all = data_source.fetch_industry_analysis_daily(
                symbol="二级行业",
                start_date=target_date_str,
                end_date=target_date_str,
            )
            if not df_all.empty:
                # 只保留 active universe 中的行业
                df_active = df_all[df_all["industry_code"].isin(active_codes)].copy()
                saved, errors = storage.batch_save_industry_data(df_active, raw_dir, target_date_str)
                logger.info("  saved: %d / %d active industries (errors=%d, fetch=%.1fs)",
                            saved, len(active_codes), errors, time.monotonic() - t0)

                if saved >= len(active_codes) * 0.9:
                    update_source = "analysis_daily"
                    storage.save_update_status(raw_dir, "confirmed", target_date_str, "analysis_daily",
                                               confirmed_date=target_date_str)
                    target_ready = True
                    storage.clear_checkpoint(raw_dir)
                    logger.info("  status: confirmed")
                else:
                    logger.warning("  coverage insufficient (%d/%d), falling back to hist_sw", saved, len(active_codes))
            else:
                logger.warning("  analysis_daily returned empty, falling back")
        except Exception as e:
            logger.warning("  analysis_daily batch fetch failed: %s", e)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 2: provisional — realtime 基底（全 124）+ 同花顺成交额/量增强
    # 语义：同一交易日 T。上午跑 target=T-1 用 昨收盘；收盘后跑 target=T 用 最新价。
    # 同花顺 90 板块仅增强 78 个行业，覆盖天花板 ~78/124，故 close 以 realtime 为准。
    # ═══════════════════════════════════════════════════════════════════
    if not target_ready:
        cfg = load_config()
        ths_min_coverage = cfg.get("provisional", {}).get("min_coverage", 0.6)
        logger.info("L2 provisional: realtime 基底 + 同花顺增强 ...")
        t0 = time.monotonic()
        try:
            df_rt = data_source.fetch_industry_realtime(symbol="二级行业")
            if not df_rt.empty:
                prov_df = _build_realtime_provisional(df_rt, raw_dir, active_codes, target_date_str)
                if not prov_df.empty:
                    # 同花顺增强（best-effort）：成交额/成交量
                    try:
                        enr = _fetch_ths_enrichment(raw_dir, active_codes, target_date_str)
                        if not enr.empty:
                            prov_df = prov_df.merge(
                                enr[["industry_code", "volume", "amount", "ths_source"]],
                                on="industry_code", how="left",
                            )
                            logger.info("  THS enrichment: %d/%d industries", enr["industry_code"].nunique(), len(prov_df))
                    except Exception as e:
                        logger.warning("  THS enrichment failed: %s (realtime base still valid)", e)

                    saved, errors = storage.batch_save_industry_data(prov_df, raw_dir, target_date_str)
                    logger.info("  provisional saved: %d/%d active, errors=%d (fetch=%.1fs)",
                                saved, len(active_codes), errors, time.monotonic() - t0)
                    if saved >= len(active_codes) * ths_min_coverage:
                        update_source = "realtime"
                        storage.save_update_status(raw_dir, "provisional", target_date_str, "realtime",
                                                   confirmed_date=None)
                        target_ready = True
                        storage.clear_checkpoint(raw_dir)
                        logger.info("  status: provisional (realtime base), assigned_date=%s, coverage=%d/%d",
                                    target_date_str, saved, len(active_codes))
                    else:
                        logger.warning("  provisional coverage insufficient (%d/%d < %.0f%%)",
                                       saved, len(active_codes), ths_min_coverage * 100)
                else:
                    logger.warning("  realtime base provisional empty, falling back")
            else:
                logger.warning("  realtime returned empty, falling back")
        except Exception as e:
            logger.warning("  provisional fetch failed: %s (silent fallback)", e)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 4: final fallback via index_hist_sw (逐行业, legacy path)
    # ═══════════════════════════════════════════════════════════════════
    if not target_ready:
        logger.info("L4 fallback: per-industry index_hist_sw (legacy path) ...")
        target_date_str = explicit_target

        cp = storage.load_checkpoint(raw_dir)
        if cp and cp.get("target_date") == target_date_str:
            completed = set(cp.get("completed_codes", []))
            failed_map = cp.get("failed_codes", {})
        else:
            completed = set()
            failed_map: dict[str, int] = {}

        codes_to_fetch = [c for c in active_codes if c not in completed]
        random.shuffle(codes_to_fetch)
        fetch_start = time.monotonic()
        for code in codes_to_fetch:
            last_date = storage.load_industry_latest_date(raw_dir, code)
            if last_date is not None and last_date >= pd.Timestamp(target_date_str).date():
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

        raw_covered = len([c for c in active_codes if c in completed])
        if raw_covered >= len(active_codes) * 0.9:
            update_source = "hist_sw"
            storage.save_update_status(raw_dir, "confirmed", target_date_str, "hist_sw",
                                       confirmed_date=target_date_str)
            target_ready = True
            storage.clear_checkpoint(raw_dir)
        else:
            still_missing = [c for c in active_codes if c not in completed]
            logger.info("  L3 still missing: %d / %d", len(still_missing), len(active_codes))

    # ── 汇总 ──────────────────────────────────────────────────────────
    final_latest_dates = [storage.load_industry_latest_date(raw_dir, c) for c in active_codes]
    final_valid = [d for d in final_latest_dates if d is not None]
    source_latest = max(final_valid) if final_valid else None
    source_latest_str = source_latest.strftime("%Y%m%d") if source_latest is not None else None

    status = "completed" if update_source in ("analysis_daily", "hist_sw") else (
        "completed_provisional" if target_ready else "waiting_for_source"
    )

    logger.info("update complete: source=%s, status=%s, covered=%d/%d",
                update_source, status,
                sum(1 for d in final_latest_dates if d is not None and d >= request_date),
                len(active_codes))

    # 汇总写入：用 target_date_str（本次目标日期），而非文件扫描的 source_latest
    confirmed_date_for_status = target_date_str if update_source in ("analysis_daily", "hist_sw") else None
    available_date_for_status = target_date_str if target_ready else (source_latest_str or "")
    storage.save_update_status(raw_dir, status, available_date_for_status, update_source,
                               confirmed_date=confirmed_date_for_status)
    return UpdateResult(
        status=status,
        requested_target_date=explicit_target,
        source_latest_common_date=source_latest_str,
        target_ready=target_ready,
        update_source=update_source,
        raw_covered=len([d for d in final_latest_dates if d is not None and d >= request_date]),
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


def _count_provisional_coverage(raw_dir: Path, target_date: str, active_codes: list[str]) -> int:
    """统计目标日期以 provisional 状态覆盖的 active 行业数。"""
    target = pd.Timestamp(target_date)
    covered = 0
    for code in active_codes:
        df = storage.load_industry_raw(raw_dir, code)
        if df.empty:
            continue
        latest = df[df["trade_date"] >= target]
        if latest.empty:
            continue
        row = latest.iloc[-1]
        if row.get("data_status") == "provisional":
            covered += 1
    return covered


def cmd_calculate(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = sw_industry_raw_dir()
    processed_dir = sw_industry_processed_dir()
    cfg = load_config()
    windows = cfg.get("rps", {}).get("windows", [5, 10, 15])
    today_window = cfg.get("rps", {}).get("today_window", 1)
    velocity_window = cfg.get("rps", {}).get("velocity_window", 5)

    full_rebuild = getattr(args, "full", False)
    # 兼容两种入口：独立 `calculate --date YYYY-MM-DD`（别名）与
    # 由 run-day 透传的 `--target-date YYYYMMDD`。target_date 优先（run-day 锚点）。
    explicit_date = getattr(args, "target_date", "") or getattr(args, "date", None) or None

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data, run bootstrap first")
        return

    active_codes, inactive_codes, universe_changed = storage.compute_active_codes(raw_dir, master)
    logger.info("universe: master=%d, active=%d, inactive=%d",
                len(master), len(active_codes), len(inactive_codes))

    if universe_changed:
        logger.warning("active universe has changed — run bootstrap or --update-universe to confirm")

    if not explicit_date:
        dates = [storage.load_industry_latest_date(raw_dir, c) for c in active_codes]
        valid_dates = [d for d in dates if d is not None]
        target_date = min(valid_dates).strftime("%Y-%m-%d") if valid_dates else _default_target_date()
    else:
        target_date = explicit_date

    covered, expected, missing = validate_raw_completeness(raw_dir, target_date, active_codes)
    if covered < expected:
        # provisional 阶段允许部分覆盖（如同花顺映射未覆盖全部申万二级）
        cfg = load_config()
        min_cov = cfg.get("provisional", {}).get("min_coverage", 0.6)
        prov_covered = _count_provisional_coverage(raw_dir, target_date, active_codes)
        if prov_covered >= expected * min_cov:
            logger.warning("raw data provisional partial coverage: %d/%d (>= %.0f%%) — "
                           "computing RPS on available cross-section",
                           prov_covered, expected, min_cov * 100)
        else:
            logger.error("raw data incomplete: %d/%d active industries, cannot calculate "
                         "(provisional coverage %d/%d < %.0f%%)",
                         covered, expected, prov_covered, expected, min_cov * 100)
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
            df["parent_industry"] = name_row.iloc[0]["parent_industry"] if not name_row.empty and \
                "parent_industry" in name_row.columns else ""
            all_hist.append(df)

    combined = pd.concat(all_hist, ignore_index=True)
    logger.info("computing metrics for %d rows across %d industries", len(combined), combined["industry_code"].nunique())

    result = metrics.compute_all_metrics(combined, windows=windows,
                                         today_window=today_window,
                                         velocity_window=velocity_window)

    # Merge with prior metrics
    prior_metrics = storage.load_metrics(processed_dir)
    if full_rebuild:
        final = result
    elif explicit_date and not prior_metrics.empty:
        # 显式 --date：幂等替换单个日期分区
        prior_no_target = prior_metrics[prior_metrics["trade_date"] != pd.Timestamp(target_date)]
        target_rows = result[result["trade_date"] == pd.Timestamp(target_date)]
        final = pd.concat([prior_no_target, target_rows], ignore_index=True)
        final = final.drop_duplicates(subset=["trade_date", "industry_code"], keep="last")
        final = final.sort_values(["trade_date", "industry_code"]).reset_index(drop=True)
    elif not prior_metrics.empty:
        # 自动计算日期：追加所有新日期（> prior_max），不漏中间交易日；
        # prior_max 分区结果优先重算（provisional → confirmed 覆盖），并保留 prior 独有行业
        prior_max = prior_metrics["trade_date"].max()
        new_rows = result[result["trade_date"] > prior_max]
        refresh = result[result["trade_date"] == prior_max]
        prior_rest = prior_metrics[prior_metrics["trade_date"] != prior_max]
        final = pd.concat([prior_rest, refresh, new_rows], ignore_index=True)
        final = final.drop_duplicates(subset=["trade_date", "industry_code"], keep="first")
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

    # 兼容 run-day 透传：run-day 把锚点写进 args.date，report 独立入口用 args.target_date。
    explicit_target = getattr(args, "target_date", "") or getattr(args, "date", "") or ""
    if explicit_target:
        target_ts = pd.Timestamp(explicit_target)
        if target_ts not in metrics_df["trade_date"].values:
            logger.error("target date %s not found in metrics data", explicit_target)
            return
        latest_date = target_ts
    else:
        latest_date = metrics_df["trade_date"].max()
    date_str = latest_date.strftime("%Y%m%d")
    snapshot = metrics_df[metrics_df["trade_date"] == latest_date].copy()

    master = storage.load_master(raw_dir)
    active_codes, inactive_codes, universe_changed = storage.compute_active_codes(raw_dir, master)

    # 报告消费侧补充观察字段：parent_industry（一级方向）、strength_level、rotation_state。
    # 这些均为 Observation 展示字段，不改任何确认 Policy。
    parent_map = dict(zip(master["industry_code"], master["parent_industry"])) \
        if "parent_industry" in master.columns else {}
    if "parent_industry" not in snapshot.columns:
        snapshot["parent_industry"] = snapshot["industry_code"].map(parent_map).fillna("")
    if "strength_level" not in snapshot.columns:
        snapshot["strength_level"] = snapshot["RPS15"].apply(confirmation.industry_strength_level)
    if "rotation_state" not in snapshot.columns:
        snapshot = confirmation.add_rotation_state_column(snapshot)

    # 判断数据是否为 provisional
    update_status = storage.load_update_status(raw_dir)
    is_provisional = update_status.get("status", "") in ("provisional", "completed_provisional") and \
                     update_status.get("source", "") in ("ths_board", "realtime")

    latest_industries = snapshot["industry_code"].nunique()
    all_active_covered = latest_industries == len(active_codes)

    if not all_active_covered:
        min_cov = cfg.get("provisional", {}).get("min_coverage", 0.6)
        if is_provisional and latest_industries >= len(active_codes) * min_cov:
            logger.warning("provisional snapshot partial coverage: %d/%d (>= %.0f%%) — publishing provisional",
                           latest_industries, len(active_codes), min_cov * 100)
        else:
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
                "contribution_structure": cs,
                "breadth_structure": bs,
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

    # 限制 metrics 范围到目标日期，使轮动矩阵不展示未来数据
    report_metrics = metrics_df[metrics_df["trade_date"] <= latest_date].copy() if explicit_target else metrics_df

    # 消费已有产物：结构 artifact（第二问驱动模式）与 confirmation（第三问主题支撑）。
    # 二者缺省时报告局部降级（第二问驱动模式显示 —，第三问提示未生成），不阻塞整体。
    structure_df = structure.load_structure(processed_dir, date_str)
    confirmation_df = pd.DataFrame()
    confirmation_available = False
    _conf_path = processed_dir / f"confirmation_{date_str}.parquet"
    if _conf_path.exists():
        confirmation_df = pd.read_parquet(_conf_path)
        confirmation_available = not confirmation_df.empty
    tier_df = pd.DataFrame()
    try:
        from . import tier_confirmation as tc
        tier_df = tc.load_tier_confirmation(date_str, processed_dir)
    except Exception as e:
        logger.warning("tier confirmation load failed: %s", e)

    csv_path, html_path = report.build_html(
        snapshot=snapshot, metrics=report_metrics,
        validator_result=metrics_valid,
        report_date=date_str, reports_dir=reports_dir,
        rotation_days=rotation_days,
        drilldown_results=drilldown_results,
        provisional_suffix="_provisional" if is_provisional else "",
        structure_df=structure_df,
        confirmation_df=confirmation_df,
        confirmation_available=confirmation_available,
        tier_df=tier_df,
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

    if is_provisional:
        # provisional 只落主报告，不写 _latest 副本，避免与 confirmed 版本混淆
        logger.info("report published (provisional): %s", html_path)
    else:
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
                    "classification": r.contribution_structure,
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
# Confirm — Layer ② AI/科技/半导体 行业群确认
# ---------------------------------------------------------------------------

def _evidence_source_label(sources: list[str], provisional: bool) -> str:
    """把上游 source 收敛为简短证据来源标签。

    申万日报覆盖 → sw_daily（confirmed）
    realtime 基底 + 同花顺增强 → sw_realtime+ths_enrichment（provisional）
    逐行业 hist 回退 → sw_hist
    """
    if provisional:
        return "sw_realtime+ths_enrichment"
    joined = " ".join(sources).lower()
    if "analysis" in joined:
        return "sw_daily"
    if "hist" in joined:
        return "sw_hist"
    if "realtime" in joined:
        return "sw_realtime"
    return "+".join(sources) or "unknown"


def _observation_falls_on_closed_day(target_date: pd.Timestamp) -> bool:
    """目标交易日是否为「已完整收盘」的交易日。

    兜底源观测是否完整，取决于 target 对应的交易日是否已经收盘：
      - target < 今天         → 已收盘的过去交易日（兜底源用昨收盘）→ 完整
      - target == 今天        → 需在收盘点名（15:10）之后才算完整；盘前/盘中 → partial
      - target > 今天         → 未来日期，视作不完整（防御）
    """
    now = datetime.now()
    today = now.date()
    t = target_date.date()
    if t < today:
        return True
    if t > today:
        return False
    return now.time() >= datetime.strptime("15:10", "%H:%M").time()


def cmd_confirm(args: argparse.Namespace) -> None:
    """Layer ② 主题确认（Theme Confirmation）：主题行业证据 + 龙头/广度 + ETF—行业背离。

    流程：
      1. 计算主题焦点行业（config/theme_registry.yaml）的 RPS 明细（确认/强弱/加速）
      2. 对进入强势区/观察区的重点行业做成分股穿透（驱动分类）
      3. 落结构化明细 confirmation_{date}.parquet（含 bucket/theme 列）
      4. 生成 sw_industry_confirmation_{date}.html（按 Bucket → Theme 展示）
    """
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("LAYER ② THEME CONFIRM: 主题确认")
    logger.info("=" * 60)

    raw_dir = sw_industry_raw_dir()
    processed_dir = sw_industry_processed_dir()
    window = getattr(args, "window", 10)
    max_drill = getattr(args, "max_drill", 5)

    master = storage.load_master(raw_dir)
    if master.empty:
        logger.error("no master data")
        return
    metrics_df = storage.load_metrics(processed_dir)
    if metrics_df.empty:
        logger.error("no metrics data, run calculate first")
        return

    # 兼容两种入口：独立 `confirm --date`（别名）与 run-day 透传的
    # `--target-date YYYYMMDD`。target_date 优先（run-day 锚点）。
    explicit_date = getattr(args, "target_date", "") or getattr(args, "date", "") or ""
    if explicit_date:
        ts = pd.Timestamp(explicit_date)
        if ts not in metrics_df["trade_date"].values:
            logger.error("target date %s not found in metrics", explicit_date)
            return
        latest_date = ts
    else:
        latest_date = metrics_df["trade_date"].max()
    date_str = latest_date.strftime("%Y%m%d")
    logger.info("confirm date: %s", latest_date.date())

    # 1. 重点行业明细 + 群共振 + 市场对照
    focus_df = confirmation.compute_focus_snapshot(metrics_df, date=date_str)
    if focus_df.empty:
        logger.error("no focus industries found")
        return
    resonance = confirmation.classify_group_resonance(focus_df)
    theme_resonance = confirmation.compute_theme_resonance(focus_df)
    bucket_resonance = confirmation.compute_bucket_resonance(focus_df)
    market_context = confirmation.compute_market_context(metrics_df, date=date_str)
    divergence = confirmation.classify_divergence(focus_df, market_context.get("market_median_rps15"))
    # 每主题独立背离（报告按主题展示行业群 vs 全市场）
    divergence_map: dict[str, Any] = {}
    for theme_key in confirmation.THEMES:
        tdf = focus_df[focus_df["theme"] == theme_key]
        if not tdf.empty:
            divergence_map[theme_key] = confirmation.classify_divergence(
                tdf, market_context.get("market_median_rps15"))

    logger.info("群共振: %s | %s", resonance["status"], resonance["verdict"])
    for tr in theme_resonance:
        logger.info("  主题[%s]: %s（%s）中位RPS15=%s", tr["theme_label"], tr["status"], tr["summary"], tr["median_rps15"])
    for br in bucket_resonance:
        logger.info("  bucket[%s]: %s（%s）中位RPS15=%s", br["bucket_label"], br["status"], br["summary"], br["median_rps15"])
    logger.info("背离: %s", divergence["note"])

    # 2. 对需要验证的重点行业做成分股穿透
    #    Layer ② 仅验证进入观察区（RPS15>=80）的行业；行业弱时穿透无意义且触发上游限频
    #    复用 focus_df（calculate 产出的指标筛选），不重算指标
    candidates = focus_df.sort_values("RPS15", ascending=False)
    # 结构穿透不再以 RPS15>=观察阈值为门（弱势市场会全部为空）：
    # 按 RPS15 取 top max_drill，保证 participation/HHI/Top3 进入 Selection。
    # 弱行业结构同样刻画「广泛走弱 vs 少数带动」，对风险判断有效。
    drill_codes = candidates["industry_code"].tolist()[:max_drill]

    drilldown_results: dict[str, Any] = {}
    if not drill_codes:
        logger.info("无重点行业可穿透 — 跳过成分股结构")
    else:
        logger.info("drilldown candidates (%d): %s", len(drill_codes), ", ".join(drill_codes))
        for idx, code in enumerate(drill_codes):
            if idx > 0:
                time.sleep(4)
            row = candidates[candidates["industry_code"] == code].iloc[0]
            name = row["industry_name"]
            logger.info("[%d/%d] drilling %s (%s) RPS15=%.1f ...", idx + 1, len(drill_codes), code, name, row["RPS15"])

            ind_hist = storage.load_industry_raw(raw_dir, code)
            if ind_hist.empty:
                logger.warning("  no industry history for %s", code)
                continue
            const_df = sw_constituents.fetch_constituent_list_cached(code, raw_dir)
            if const_df.empty:
                logger.warning("  no constituent data for %s", code)
                continue

            dd = sw_contribution.compute_drilldown(
                industry_code=code,
                industry_name=name,
                breakout_date=latest_date.strftime("%Y%m%d"),
                constituents=const_df,
                industry_hist=ind_hist,
                window=window,
            )
            drilldown_results[code] = dd
            ql = dd.reconstruction_quality
            logger.info("  %s: actual=%.2f%% proxy=%.2f%% gap=%.2fpp quality=%s | %s",
                        name, dd.industry_return_pct, dd.proxy_return_pct,
                        dd.reconstruction_gap_pct, ql,
                        confirmation._format_drive(dd.contribution_structure, dd.breadth_structure))

    # 记录 drilldown 级警告（个股行情多源抓取失败），供 run-day 末端 Final Validation 汇总
    fetch_failures = sum(getattr(dd, "fetch_failures", 0) for dd in drilldown_results.values())
    if fetch_failures:
        run_warnings.record("drilldown", f"{fetch_failures} stock drilldowns failed")
        logger.warning("drilldown warnings: %d stock drilldowns failed", fetch_failures)
    run_warnings.save_warnings(date_str)

    # 3. 合并 + 落结构化明细
    final_df = confirmation.merge_drilldown(focus_df, drilldown_results)
    final_df = confirmation.add_rotation_state_column(final_df)
    theme_resonance = confirmation.add_theme_heat(theme_resonance, final_df)
    # 证据元数据：从上游 metrics 在 latest_date 的行提取，供 Selection 判断证据等级
    ms = metrics_df[metrics_df["trade_date"] == latest_date] if not metrics_df.empty else pd.DataFrame()
    total_ind = int(metrics_df["industry_code"].nunique()) if not metrics_df.empty else 0
    covered_ind = int(ms["industry_code"].nunique()) if not ms.empty else 0
    coverage = round(covered_ind / total_ind, 4) if total_ind else 0.0
    sources = [str(s) for s in ms["source"].dropna().unique()] if not ms.empty and "source" in ms.columns else []
    # V0.1 来源语义：source_status（primary/fallback）与 data_status（confirmed/complete/partial）分离，
    # 不再用「provisional」把「兜底源」和「观测不完整」混为一谈。
    from . import source_status as _ss
    source_status = _ss.classify_source_status(sources)
    is_complete = _observation_falls_on_closed_day(latest_date)
    data_status = _ss.classify_data_status(source_status, is_complete)
    source_label = _evidence_source_label(sources, provisional=(data_status != "confirmed"))
    final_df = final_df.copy()
    final_df["data_status"] = data_status
    final_df["source_status"] = source_status
    final_df["source"] = source_label
    final_df["coverage"] = coverage
    final_df["generated_at"] = pd.Timestamp(datetime.now())
    logger.info("confirmation evidence: trade_date=%s status=%s source_status=%s source=%s coverage=%.1f%%",
                latest_date.date(), data_status, source_status, source_label, coverage * 100)
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / f"confirmation_{date_str}.parquet"
    final_df.to_parquet(out_path, index=False)
    logger.info("confirmation detail saved: %d industries -> %s", len(final_df), out_path)

    # 4. Tier-level confirmation（v0.9.1）：配置了 tiers 的主题由申万行业 Gate 升级为 Tier basket Gate
    #    Tier 确认消费个股趋势产物（stock_metrics_{trade_date}.parquet，run-day 中由
    #    stock-metrics 提前构建）；缺个股数据 → Tier 全部 unavailable（不阻塞报告发布）。
    from src.trend_engine import inputs as trend_inputs
    stock_metrics = trend_inputs.load_stock_metrics(date_str)
    if stock_metrics.empty:
        # 兜底：尝试最近一份 stock_metrics（可能滞后，标注 data_status=stale）
        latest_md = trend_inputs.latest_stock_metrics_trade_date()
        if latest_md:
            stock_metrics = trend_inputs.load_stock_metrics(latest_md)
            tier_data_status = "stale"
            logger.info("tier confirmation uses latest stock_metrics %s (stale)", latest_md)
        else:
            tier_data_status = "unavailable"
            logger.warning("no stock_metrics for %s — tier confirmation will be unavailable", date_str)
    else:
        tier_data_status = data_status

    tc_build = None
    try:
        from . import tier_confirmation as tc
        tc_build = tc.build_tier_confirmation_parquet(
            trade_date=date_str,
            stock_metrics=stock_metrics,
            data_status=tier_data_status,
            source="stock_metrics" if not stock_metrics.empty else "missing",
        )
        logger.info("tier confirmation published: %s", tc_build)
    except Exception as e:
        logger.warning("tier confirmation failed (non-blocking): %s", e)

    # 注：独立 confirmation HTML 已并入主报告第三问（report 消费本 parquet）。
    # 此处只落盘事实 parquet，不再单独生成 confirmation 页面。


# ---------------------------------------------------------------------------
# Structure artifact — 行业内部结构（趋势 ∪ 焦点）
# ---------------------------------------------------------------------------

def cmd_structure(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("LAYER ② STRUCTURE: 行业内部结构产物（趋势 ∪ 焦点）")
    logger.info("=" * 60)

    raw_dir = sw_industry_raw_dir()
    processed_dir = sw_industry_processed_dir()
    window = getattr(args, "window", 10)
    offline = not getattr(args, "allow_online_fetch", False)
    # 兼容 run-day 透传：run-day 把锚点写进 args.date。
    target_date = getattr(args, "target_date", "") or getattr(args, "date", "") or ""

    metrics_df = storage.load_metrics(processed_dir)
    if metrics_df.empty:
        logger.error("no metrics data, run calculate first")
        return
    if target_date:
        ts = pd.Timestamp(target_date)
        if ts not in metrics_df["trade_date"].values:
            logger.error("target date %s not found in metrics", target_date)
            return
        latest_date = ts
    else:
        latest_date = metrics_df["trade_date"].max()
    date_str = latest_date.strftime("%Y%m%d")
    snapshot = metrics_df[metrics_df["trade_date"] == latest_date].copy()

    scope, trend, focus = structure.compute_structure_scope(snapshot)
    logger.info("structure scope: %d (trend=%d, focus=%d) on %s",
                len(scope), len(trend), len(focus), latest_date.date())

    structure.build_structure_parquet(
        snapshot, raw_dir, processed_dir, date_str,
        window=window, offline=offline, allow_online=not offline,
        sleep_between=0.0 if offline else 4.0,
    )
    logger.info("structure complete: %s", date_str)


# ---------------------------------------------------------------------------
# Run-day orchestration
# ---------------------------------------------------------------------------

def cmd_run_day(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("RUN-DAY STARTED")
    logger.info("=" * 60)

    t_start = time.monotonic()
    requested_target = getattr(args, "target_date", "") or _default_target_date()
    force_report = getattr(args, "force_report", False)

    # 归一化日期透传：run-day 只暴露 --target-date（YYYYMMDD）。
    # calculate / confirm 独立入口用 --date，这里统一把锚点喂给 args.date，
    # 使下游各 cmd_* 能按同一目标日推进，避免 calculate 因读 args.date 而
    # 走隐式 auto-date、confirm 拿不到日期。
    if requested_target:
        args.date = requested_target

    # ── Step 1: Update ────────────────────────────────────────────────
    t0 = time.monotonic()
    result = cmd_update(args)
    fetch_dur = time.monotonic() - t0

    # ── Date gate ────────────────────────────────────────────────────
    # provisional 是新设计下的常态（当天先用更快数据，次日申万确认覆盖）。
    # 默认放行；--require-confirmed 可恢复严格模式（仅 confirmed 才继续）。
    allow_provisional = not getattr(args, "require_confirmed", False)

    if result.status == "completed_provisional":
        logger.info("  update_source:           %s (provisional)", result.update_source)
        logger.info("  source_latest_common_date: %s", result.source_latest_common_date)
        if not allow_provisional:
            logger.info("  provisional data available — use --allow-provisional to proceed with calculate/report")
            logger.info("  latest_confirmed_date: %s",
                        storage.load_update_status(sw_industry_raw_dir()).get("latest_confirmed_date", ""))

    if not result.target_ready or (result.status == "completed_provisional" and not allow_provisional):
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

    # ── Step 2.3: Confirm（Layer② 主题确认）─────────────────────────────
    # 落 confirmation_{date}.parquet + tier_confirmation_{date}.parquet，
    # 供 report 第三问消费。此前 CLI run-day 缺此步，confirm 只在 Makefile
    # 级 sw-rps-run-day 单独出现；这里补上使 CLI run-day 单一入口完整。
    t0 = time.monotonic()
    cmd_confirm(args)
    confirm_dur = time.monotonic() - t0

    # ── Step 2.5: Structure（Enrichment，offline-only，soft-fail）──────
    # Structure 是 Layer② 的 Enrichment（驱动模式解释），非 Core 事实：
    #   缓存够 → 生成 sw_industry_structure_{date}.parquet，日报出现驱动；
    #   缓存不够 → 记 unavailable/insufficient，日报照常发布。
    #   绝不因 Structure 缺数据在 run-day 里自动联网（联网补数走独立 --allow-online-fetch）。
    t0 = time.monotonic()
    try:
        cmd_structure(args)
    except Exception as e:
        logger.warning("structure (offline enrichment) soft-failed: %s", e)
    structure_dur = time.monotonic() - t0

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
    logger.info("  fetch:      %.1fs", fetch_dur)
    logger.info("  calculate:  %.1fs", calc_dur)
    logger.info("  confirm:    %.1fs", confirm_dur)
    logger.info("  structure:  %.1fs (enrichment, soft-fail)", structure_dur)
    logger.info("  report:     %.1fs", report_dur)
    logger.info("  total:      %.1fs", total_dur)
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
    p_update.add_argument("--allow-provisional", action="store_true", help="允许使用 realtime provisional 数据")
    p_update.add_argument("--log-level", default="INFO")

    p_calc = sub.add_parser("calculate", help="计算 RPS 指标（幂等替换目标日期分区）")
    p_calc.add_argument("--target-date", default="", dest="target_date", help="目标日期 YYYYMMDD（推荐，与 update/report 一致）")
    p_calc.add_argument("--date", dest="date", help="目标日期 YYYY-MM-DD（兼容别名；与 --target-date 等价）")
    p_calc.add_argument("--full", action="store_true", help="全量重建（慎用）")
    p_calc.add_argument("--allow-provisional", action="store_true", help="允许对 provisional 数据计算指标")
    p_calc.add_argument("--log-level", default="INFO")

    p_report = sub.add_parser("report", help="生成 HTML/CSV 报告（质量门控）")
    p_report.add_argument("--target-date", default="", help="报告日期 YYYYMMDD（默认最新日期）")
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

    p_confirm = sub.add_parser("confirm", help="[Layer ②] 主题确认（Theme Confirmation：行业证据 + 龙头/广度 + 背离）")
    p_confirm.add_argument("--window", type=int, default=10, help="贡献分析回看窗口（交易日数，默认 10）")
    p_confirm.add_argument("--max-drill", type=int, default=10, help="最多穿透的行业数（默认 10 = 全部重点行业，结构字段全覆盖）")
    p_confirm.add_argument("--target-date", default="", dest="target_date", help="目标日期 YYYYMMDD（推荐，与 update/report 一致）")
    p_confirm.add_argument("--date", dest="date", help="目标日期 YYYYMMDD（兼容别名；与 --target-date 等价）")
    p_confirm.add_argument("--log-level", default="INFO")

    p_struct = sub.add_parser("structure", help="[Layer ②] Enrichment 行业内部结构（offline 读缓存生成，soft-fail；--allow-online-fetch 做 Cache Refresh）")
    p_struct.add_argument("--window", type=int, default=10, help="结构穿透回看窗口（交易日数，默认 10）")
    p_struct.add_argument("--target-date", default="", help="目标日期 YYYYMMDD（默认最新）")
    p_struct.add_argument("--allow-online-fetch", action="store_true", help="Structure Cache Refresh：允许联网补数（默认仅读缓存，缺数据 soft-fail）")
    p_struct.add_argument("--log-level", default="INFO")

    p_run = sub.add_parser("run-day", help="依次执行 update → calculate → confirm → structure → report")
    p_run.add_argument("--target-date", default="", help="目标日期 YYYYMMDD（默认今天）")
    p_run.add_argument("--force-report", action="store_true", help="允许对已有报告的日期重新生成报告")
    p_run.add_argument("--allow-provisional", action="store_true", help="允许使用 provisional 数据运行完整 pipeline（默认已放行）")
    p_run.add_argument("--require-confirmed", action="store_true", help="严格模式：仅 confirmed 数据才继续 calculate/report")
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
        "confirm": cmd_confirm,
        "structure": cmd_structure,
        "run-day": cmd_run_day,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
