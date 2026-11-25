"""ETF market-data collection, correction, and factual calculation commands."""

from __future__ import annotations
from src.common.paths import etf_signal_diagnostics_dir

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import date as date_type, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import (
    config_dir,
    etf_signal_raw_dir, etf_signal_master_dir, etf_signal_daily_dir,
)
from . import data_source, master as etf_master, classifier, indicators
from src.market_facts.etf_observation import compute_rotation_metrics
from .fetch_policy import (
    UpdateDecision,
    build_fetch_window,
    decide_update,
    has_target_bar,
    load_market_data_spec,
    validate_append,
)


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("etf_signal")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


# ETF来源由实际价格序列记录；旧缓存无证据时不推断为东财。
ETF_DATA_SOURCE = "unknown_legacy"
ETF_CONFIRMED_COVERAGE = 0.99


def _stamp_output_meta(
    df: pd.DataFrame,
    trade_date: pd.Timestamp,
    data_status: str,
    source: str = ETF_DATA_SOURCE,
) -> pd.DataFrame:
    """为 Layer ① 产物写入统一的日期/状态元数据列。

    trade_date 必须来自输入行情横截面，不得由 datetime.now() 推断；
    run_date / generated_at 仅承担审计语义。
    """
    df = df.copy()
    df["trade_date"] = pd.Timestamp(trade_date)
    df["run_date"] = pd.Timestamp(datetime.now().date())
    df["generated_at"] = pd.Timestamp(datetime.now())
    df["data_status"] = data_status
    if "source" not in df:
        df["source"] = source
    return df


def _coverage_status(covered: int, total: int) -> str:
    """按横截面覆盖判定 confirmed / provisional。"""
    if total <= 0:
        return "provisional"
    return "confirmed" if covered / total >= ETF_CONFIRMED_COVERAGE else "provisional"


def _default_target_date() -> str:
    now = datetime.now()
    if now.hour < 16 or (now.hour == 16 and now.minute < 30):
        days_back = 1
        while True:
            candidate = now - timedelta(days=days_back)
            if candidate.weekday() < 5:
                return candidate.strftime("%Y%m%d")
            days_back += 1
    return now.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# P0-A: Bootstrap — 初始化 ETF Master 并拉取全量历史
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# P0-A: Bootstrap Core — 阶段 A：核心 ETF Universe
# ---------------------------------------------------------------------------

def cmd_bootstrap_core(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("ETF BOOTSTRAP CORE（阶段 A）")
    logger.info("=" * 60)

    raw_dir = etf_signal_raw_dir()
    master_dir = etf_signal_master_dir()

    # Step 1: Load or fetch master
    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.info("no local master, fetching from AKShare...")
        master = data_source.fetch_etf_master()
        if master.empty:
            logger.error("fetch failed")
            return
        # Cross-validate
        ths = data_source.fetch_etf_master_ths()
        if not ths.empty:
            master, _, _ = data_source.cross_validate_master(master, ths)
        etf_master.save_master(master, master_dir)

    logger.info("master: %d ETFs", len(master))

    # Step 2: Classify
    if "primary_bucket" not in master.columns or master["primary_bucket"].isna().all():
        logger.info("classifying ETFs...")
        from . import classifier as clf
        master = clf.classify_all(master)
        etf_master.save_master(master, master_dir)

    # Step 3: Build core universe
    cfg = {
        "max_count": getattr(args, "max_count", 300),
        "min_amount": getattr(args, "min_amount", 10_000_000),
    }
    core = etf_master.build_core_universe(master, config=cfg)
    if core.empty:
        logger.error("core universe empty")
        return

    etf_master.save_core_universe(core, master_dir)
    logger.info("core universe: %d ETFs", len(core))

    # Print summary by bucket
    if "primary_bucket" in core.columns:
        print("\n核心 Universe 资产桶分布:")
        print(f"{'桶':<20} {'数量':>6}")
        print("-" * 28)
        for bucket, count in core["primary_bucket"].value_counts().items():
            print(f"{bucket:<20} {count:>6}")

    # Step 4: Pull history for core ETFs only
    if getattr(args, "skip_history", False):
        logger.info("skip-history set, done")
        return

    codes = core["fund_code"].tolist()
    logger.info("pulling history for %d core ETFs...", len(codes))
    success = 0
    failed: list[dict] = []

    for i, code in enumerate(codes):
        existing = _load_etf_raw(raw_dir, code)
        if not existing.empty and len(existing) >= 250:
            success += 1
            continue
        logger.info("[%d/%d] %s...", i + 1, len(codes), code)
        t0 = time.monotonic()
        try:
            df = data_source.fetch_etf_hist(code, start_date="20200101", max_retries=2, base_delay=1.0)
            elapsed = time.monotonic() - t0
            if not df.empty:
                _save_etf_raw(df, raw_dir, code)
                success += 1
                logger.info("  ok: %d rows (%.1fs)", len(df), elapsed)
            else:
                failed.append({"code": code, "error": "empty", "elapsed": round(elapsed, 1)})
        except Exception as e:
            elapsed = time.monotonic() - t0
            failed.append({"code": code, "error": str(e), "elapsed": round(elapsed, 1)})
            logger.warning("  fail: %s (%.1fs)", e, elapsed)
        time.sleep(random.uniform(0.5, 1.5))

    logger.info("core bootstrap: %d success, %d failed out of %d", success, len(failed), len(codes))
    if failed:
        logger.info("failed codes: %s", [f["code"] for f in failed[:10]])


def cmd_bootstrap(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("ETF BOOTSTRAP")
    logger.info("=" * 60)

    raw_dir = etf_signal_raw_dir()
    master_dir = etf_signal_master_dir()

    # Step 1: Fetch ETF master from AKShare (returns normalized format)
    logger.info("fetching ETF master from AKShare...")
    master = data_source.fetch_etf_master()
    if master.empty:
        logger.error("ETF master fetch returned empty — aborting")
        return
    logger.info("AKShare returned %d ETFs", len(master))

    # Step 2: Cross-validate with 同花顺
    logger.info("cross-validating with 同花顺...")
    ths = data_source.fetch_etf_master_ths()
    if not ths.empty:
        master, only_em, only_ths = data_source.cross_validate_master(master, ths)
        logger.info("cross-validation: %d common, %d only in EM, %d only in THS",
                     len(master) - len(only_em), len(only_em), len(only_ths))

    # Step 3: Save master
    etf_master.save_master(master, master_dir)
    logger.info("ETF master saved: %d ETFs", len(master))

    # Step 3: Pull history for each ETF
    codes = master["fund_code"].tolist()
    total = len(codes)
    success = 0
    failed: list[dict] = []

    logger.info("pulling history for %d ETFs...", total)
    for i, code in enumerate(codes):
        existing = _load_etf_raw(raw_dir, code)
        if not existing.empty and len(existing) >= 250:
            continue

        logger.info("[%d/%d] fetching %s...", i + 1, total, code)
        t0 = time.monotonic()
        try:
            df = data_source.fetch_etf_hist(
                code,
                start_date="20200101",
                max_retries=3, base_delay=2.0, max_delay=30.0,
            )
            elapsed = time.monotonic() - t0
            if not df.empty:
                _save_etf_raw(df, raw_dir, code)
                success += 1
                logger.info("  ok: %d rows (%.1fs)", len(df), elapsed)
            else:
                failed.append({"code": code, "error": "empty", "elapsed": round(elapsed, 1)})
        except Exception as e:
            elapsed = time.monotonic() - t0
            failed.append({"code": code, "error": str(e), "elapsed": round(elapsed, 1)})
            logger.warning("  fail: %s (%.1fs)", e, elapsed)
        time.sleep(random.uniform(1.0, 2.0))

    logger.info("bootstrap complete: %d success, %d failed out of %d", success, len(failed), total)


def _load_etf_raw(raw_dir: Path, code: str) -> pd.DataFrame:
    path = raw_dir / f"{code}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame()


def _has_request_bar(df: pd.DataFrame, request_date: date_type) -> bool:
    """判断 raw 中是否真实存在 target 交易日的**完整** K 线。

    不能仅比较 max(date) >= target：盘中 spot 会提前合并当日（> target）的
    未收盘 K 线，导致 max(date) 已越过 target，但 target 当日完整 K 线仍缺失
    （如盘中运行 target=上一完整交易日，而该日 bar 从未从历史源补回）。

    也不能只看日期行是否存在：2026-09-03 事故中全市场 raw 里该日**行存在但
    close 为 NaN**（占位空 bar），旧逻辑把它当成已覆盖，导致 backfill 永远发现
    不了缺口。这里把「该日 close 全为空」一并视为缺失，让 backfill 能回补。
    """
    if df.empty or "date" not in df.columns:
        return False
    try:
        dates = pd.to_datetime(df["date"]).dt.date
        mask = (dates == request_date)
        if not bool(mask.any()):
            return False
        if "close" in df.columns:
            return bool(df.loc[mask, "close"].notna().any())
        return True
    except Exception:
        return False


def _load_all_raw(master: pd.DataFrame) -> pd.DataFrame:
    """合并全市场原始日行情。"""
    raw_dir = etf_signal_raw_dir()
    all_daily: list[pd.DataFrame] = []
    for code in master["fund_code"]:
        df = _load_etf_raw(raw_dir, code)
        if not df.empty:
            df["fund_code"] = code
            all_daily.append(df)
    if not all_daily:
        return pd.DataFrame()
    return pd.concat(all_daily, ignore_index=True)


def _save_etf_raw(df: pd.DataFrame, raw_dir: Path, code: str) -> Path:
    """原子落盘 raw parquet（Performance V1）：先写同目录 tmp，再 os.replace。

    Sina 并发池 worker 各自写不同文件，互不冲突；原子替换避免崩溃时写穿半文件。
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{code}.parquet"
    tmp = raw_dir / f".{code}.tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# P0-A: Update — 增量更新 ETF 日行情（Performance V1：增量 + 源熔断 + Sina 并发）
# ---------------------------------------------------------------------------

@dataclass
class UpdateResult:
    status: str
    requested_target_date: str
    source_latest_common_date: str | None
    target_ready: bool
    raw_covered: int
    active_count: int
    cache_hits: int = 0
    requested: int = 0
    fetched_ok: int = 0
    failed: int = 0
    circuit: dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeFetchOutcome:
    """单只代码在本轮 update 内的处理结果（会话内每个 code 至多一次网络尝试）。"""

    code: str
    ok: bool = False
    had_bar: bool = False        # cache hit（本地已含 target，0 请求）
    mode: str = ""               # skip | incremental | full_refresh
    source: str = ""             # em | sina | none（最终统计口径）
    rows_added: int = 0
    elapsed_ms: int = 0
    reason: str = ""


def _try_window(
    existing: pd.DataFrame,
    raw_dir: Path,
    code: str,
    start: str,
    end: str,
    target: date_type,
    sources: tuple[str, ...],
) -> tuple[bool, str]:
    """单窗口 fetch + merge + validate + 原子保存。

    Returns:
        (ok, reason) — ok=True 表示已含 target bar 并落盘；reason 为失败原因。
    """
    df_new = data_source.fetch_etf_hist(code, start_date=start, end_date=end, sources=sources)
    if df_new.empty:
        return False, "empty_response"
    combined = _merge_incremental(existing, df_new)
    okv, issues = validate_append(existing, combined)
    if not okv:
        return False, "validation:" + ";".join(issues)
    if not has_target_bar(combined, target):
        return False, "missing_target"
    _save_etf_raw(combined, raw_dir, code)
    return True, ""


# 增量失败/出现 gap → 单只 full-refresh slow path 的本地滞后门槛（日历日）
_FULL_REFRESH_LAG_CAL_DAYS = 30


def _fetch_one_code(
    raw_dir: Path,
    code: str,
    target: date_type,
    spec: Any,
    sources: tuple[str, ...] = ("em", "sina"),
) -> CodeFetchOutcome:
    """单只代码拉取：FAST PATH=incremental，缺口/失败降级 SLOW PATH=full refresh。

    三种 guardrail 由 fetch_policy 保证（① append 后 date 唯一 ② close/volume 完整
    ③ 增量失败或出现 gap → 单只 full-refresh slow path）。同一 run 内每个 code 只处理
    一次（会话内失败记忆：TERMINATED / TRUE_MISSING 等不再重复请求；跨日负缓存留 V2）。
    """
    t0 = time.perf_counter()
    existing = _load_etf_raw(raw_dir, code)
    outcome = CodeFetchOutcome(code=code)

    decision = decide_update(existing, target)
    if decision == UpdateDecision.SKIP_UP_TO_DATE:
        outcome.ok = True
        outcome.had_bar = True
        outcome.mode = "skip"
        outcome.elapsed_ms = 0
        return outcome

    window = build_fetch_window(existing, target, spec.full_refresh_start)
    if window is None:
        outcome.had_bar = True
        outcome.ok = True
        outcome.mode = "skip"
        return outcome

    start, end = window
    outcome.mode = "full_refresh" if decision == UpdateDecision.FULL_REFRESH else "incremental"

    ok, reason = _try_window(existing, raw_dir, code, start, end, target, sources)
    if not ok:
        outcome.reason = reason
        latest = _load_etf_raw(raw_dir, code)  # existing 可能在别处被更新，重读一次无妨
        lag = (target - pd.to_datetime(latest["date"]).max().date()).days \
            if not latest.empty and "date" in latest.columns else _FULL_REFRESH_LAG_CAL_DAYS + 1
        # guardrail ③：增量窗口有数据但 target 缺失（gap），或本地明显滞后且响应为空 → slow path
        escalate = (
            reason == "missing_target"
            or (reason == "empty_response" and decision == UpdateDecision.INCREMENTAL and lag >= _FULL_REFRESH_LAG_CAL_DAYS)
        )
        if escalate and outcome.mode != "full_refresh":
            ok2, reason2 = _try_window(
                existing, raw_dir, code, spec.full_refresh_start, end, target, sources)
            if ok2:
                ok, reason, outcome.mode = True, "", "full_refresh"
            else:
                outcome.reason = f"{reason} → full_refresh:{reason2}"

    outcome.elapsed_ms = round((time.perf_counter() - t0) * 1000)
    if ok:
        outcome.ok = True
        outcome.rows_added = max(0, len(_load_etf_raw(raw_dir, code)) - len(existing))
    outcome.source = data_source.get_fetch_stats().get(code, {}).get("source", "none")
    return outcome


def _write_source_audit(
    target_str: str,
    *,
    status: str,
    cache_hits: int,
    requested: int,
    fetched_ok: int,
    failed_codes: list[str],
    full_refresh_codes: list[str],
    spec: Any,
    elapsed_s: float,
) -> Path:
    """落盘 source_audit_{target}.json —— 回答「今天为什么大量切 Sina」而不是从日志猜。"""
    logger = logging.getLogger("etf_signal")
    attempts = data_source.get_attempt_stats()
    em_snap = data_source.em_breaker_snapshot()

    eastmoney: dict[str, Any] = dict(attempts.get("eastmoney", {}))
    eastmoney["circuit_opened"] = bool(em_snap.get("circuit_opened"))
    eastmoney["circuit_opened_after"] = em_snap.get("circuit_opened_after")
    eastmoney["state"] = em_snap.get("state")
    sina: dict[str, Any] = dict(attempts.get("sina", {}))

    diag = etf_signal_diagnostics_dir()
    diag.mkdir(parents=True, exist_ok=True)
    path = diag / f"source_audit_{target_str}.json"
    payload = {
        "target_date": target_str,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_status": status,
        "sources": {"eastmoney": eastmoney, "sina": sina},
        "cache_hits": cache_hits,
        "requested": requested,
        "fetched_ok": fetched_ok,
        "failed": len(failed_codes),
        "failed_codes": sorted(failed_codes),
        "full_refresh_codes": sorted(full_refresh_codes),
        "elapsed_s": round(elapsed_s, 1),
        "config": {
            "full_refresh_start": spec.full_refresh_start,
            "em_backfill_limit": spec.em_backfill_limit,
            "em_request_interval_s": spec.em_request_interval_s,
            "circuit_breaker": {
                "window": spec.eastmoney.circuit_breaker.window,
                "min_requests": spec.eastmoney.circuit_breaker.min_requests,
                "failure_rate_threshold": spec.eastmoney.circuit_breaker.failure_rate_threshold,
            },
            "sina": {"workers": spec.sina.workers, "retry": spec.sina.retry},
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("source audit: %s", path)
    return path


def cmd_update(args: argparse.Namespace) -> UpdateResult:
    logger = build_logger(args.log_level)
    raw_dir = etf_signal_raw_dir()
    master_dir = etf_signal_master_dir()
    explicit_target = getattr(args, "target_date", "") or _default_target_date()

    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return UpdateResult(status="failed", requested_target_date=explicit_target,
                            source_latest_common_date=None, target_ready=False,
                            raw_covered=0, active_count=0)

    codes = master["fund_code"].tolist()
    request_date = pd.Timestamp(explicit_target).date()

    t_start = time.perf_counter()
    spec = load_market_data_spec().etf_fetch

    # run-level 语义：新 run → EM breaker 回 CLOSED + 统计清零
    data_source.reset_em_circuit_breakers()
    data_source.clear_fetch_stats()

    # ── Phase 1: EM spot 全市场快照（1 次请求） ──────────────
    spot_ohlcv = data_source.fetch_ohlcv_from_spot()
    spot_codes: set[str] = set()
    spot_date: date_type | None = None
    spot_covered = 0
    if not spot_ohlcv.empty:
        spot_date = spot_ohlcv["date"].iloc[0].date()
        spot_date_str = spot_date.strftime("%Y-%m-%d")
        logger.info("Phase 1 — spot batch: %d ETFs, date=%s", len(spot_ohlcv), spot_date_str)

        for code, grp in spot_ohlcv.groupby("fund_code"):
            existing = _load_etf_raw(raw_dir, code)
            combined = _merge_incremental(existing, grp)
            _save_etf_raw(combined, raw_dir, code)
            spot_codes.add(str(code))

        if spot_date >= request_date:
            code_set = set(codes)
            spot_covered = len(spot_codes & code_set)
            extra_in_spot = spot_codes - code_set
            if extra_in_spot:
                logger.info("  spot has %d codes not in master: %s", len(extra_in_spot), sorted(extra_in_spot)[:5])
        else:
            logger.info("  spot date %s < target %s — counting individually",
                        spot_date_str, explicit_target)
    else:
        logger.warning("Phase 1 spot empty — falling back to history-only")

    # ── Phase 2: History 残余补缺（增量/跳过决策） ───────────
    pending: list[tuple[str, bool]] = []   # (code, is_empty_backfill)
    cache_hits = 0
    backfill_skipped = 0
    for code in codes:
        cs = str(code)
        df = _load_etf_raw(raw_dir, code)
        decision = decide_update(df, request_date)
        if decision == UpdateDecision.SKIP_UP_TO_DATE:
            cache_hits += 1
            continue
        is_new = df.empty
        if is_new and backfill_skipped + len([p for p in pending if p[1]]) >= spec.em_backfill_limit:
            # 新上市代码每 run 全量请求上限（legacy EM_BACKFILL_LIMIT 语义），超限进 skipped
            backfill_skipped += 1
            continue
        pending.append((cs, is_new))

    logger.info("Phase 2 — history residual: %d covered(cache), %d pending fetch, %d new-listing skipped",
                cache_hits, len(pending), backfill_skipped)

    outcomes: list[CodeFetchOutcome] = []
    attempted: set[str] = set()
    full_refresh_codes: list[str] = []

    # ── Phase 2a: EM 串行单飞（限流敏感源）─────────────────
    # 每次处理前检查 run-level 熔断；OPEN → 剩余缺口整体切 Sina 并发池（Phase 2b）。
    switched_to_sina = False
    i = 0
    while i < len(pending) and not data_source.em_breaker().is_open:
        code, is_new = pending[i]
        i += 1
        outcome = _fetch_one_code(raw_dir, code, request_date, spec, sources=("em", "sina"))
        outcomes.append(outcome)
        attempted.add(code)
        if outcome.ok:
            if outcome.mode == "full_refresh":
                full_refresh_codes.append(code)
        if data_source.em_breaker().is_open:
            switched_to_sina = True
            logger.warning("EM breaker OPEN — switching remaining %d codes to Sina batch",
                           len(pending) - i)
            break
        if i < len(pending):
            time.sleep(max(spec.em_request_interval_s + random.uniform(-1, 1), 0.3) if is_new
                       else random.uniform(0.3, 0.8))

    # ── Phase 2b: Sina 有限并发池（熔断后剩余）─────────────
    if i < len(pending):
        remaining = [c for c, _ in pending[i:]]
        logger.info("Phase 2b — Sina concurrent batch: %d codes, workers=%d",
                    len(remaining), spec.sina.workers)
        codes_windows: list[tuple[str, str, str]] = []
        for code in remaining:
            df = _load_etf_raw(raw_dir, code)
            window = build_fetch_window(df, request_date, spec.full_refresh_start)
            start, end = window if window else (spec.full_refresh_start, explicit_target)
            codes_windows.append((code, start, end))
        results = data_source.fetch_etf_hist_sina_batch(
            codes_windows, workers=spec.sina.workers, retry=spec.sina.retry)
        for code, start, end in codes_windows:
            # 逐只判定：直接走 Sina 并发拉回的结果（batch 已 fetch + 未合并保存）
            existing = _load_etf_raw(raw_dir, code)
            df_new = results.get(code)
            outcome = CodeFetchOutcome(code=code, source="sina", mode="sina_batch")
            if df_new is not None:
                combined = _merge_incremental(existing, df_new)
                okv, issues = validate_append(existing, combined)
                if okv and has_target_bar(combined, request_date):
                    _save_etf_raw(combined, raw_dir, code)
                    outcome.ok = True
                    outcome.rows_added = max(0, len(combined) - len(existing))
                else:
                    outcome.reason = "validation:" + ";".join(issues) if issues else "missing_target"
            else:
                outcome.reason = "empty_response"
            outcome.elapsed_ms = 0
            outcomes.append(outcome)

    # ── 汇总 ─────────────────────────────────────────────
    fetched_ok = sum(1 for o in outcomes if o.ok and not o.had_bar)
    failed_codes = [o.code for o in outcomes if not o.ok]
    history_covered = fetched_ok

    # target 就绪判定：以落盘文件为准（鲁棒，不依赖内存计数）
    covered = 0
    for code in codes:
        df = _load_etf_raw(raw_dir, code)
        if has_target_bar(df, request_date):
            covered += 1
    target_ready = covered == len(codes)
    elapsed_s = time.perf_counter() - t_start

    logger.info("update: %d/%d covered, ready=%s  (spot=%d, cache_hits=%d, fetched=%d, failed=%d, new_skipped=%d%s)",
                covered, len(codes), target_ready, spot_covered, cache_hits, fetched_ok,
                len(failed_codes), backfill_skipped,
                ", sina_switch=True" if switched_to_sina else "")
    data_source.log_fetch_summary()

    audit_path = _write_source_audit(
        explicit_target,
        status="completed" if target_ready else "partial",
        cache_hits=cache_hits,
        requested=len(pending),
        fetched_ok=fetched_ok,
        failed_codes=failed_codes,
        full_refresh_codes=full_refresh_codes,
        spec=spec,
        elapsed_s=elapsed_s,
    )

    return UpdateResult(
        status="completed" if target_ready else "partial",
        requested_target_date=explicit_target,
        source_latest_common_date=explicit_target if target_ready else None,
        target_ready=target_ready,
        raw_covered=covered, active_count=len(codes),
        cache_hits=cache_hits,
        requested=len(pending),
        fetched_ok=fetched_ok,
        failed=len(failed_codes),
        circuit={"breaker": data_source.em_breaker_snapshot(),
                 "audit": str(audit_path),
                 "switched_to_sina": switched_to_sina},
    )


def _merge_incremental(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    from src.market_facts.etf_provenance import check_merge_basis
    check_merge_basis(existing, new)
    if existing.empty:
        return new
    if new.empty:
        return existing
    date_col = [c for c in existing.columns if "日期" in c or "date" in c.lower()]
    code_col = [c for c in new.columns if "代码" in c]
    for c in code_col:
        if c not in existing.columns:
            existing[c] = ""
    combined = pd.concat([existing, new], ignore_index=True)
    dedup_key = date_col[0] if date_col else "date"
    combined = combined.drop_duplicates(subset=[dedup_key], keep="last")
    combined = combined.sort_values(dedup_key).reset_index(drop=True)
    return combined


# ---------------------------------------------------------------------------
# P0-B: Classify
# ---------------------------------------------------------------------------

def cmd_classify(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    master_dir = etf_signal_master_dir()
    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return

    logger.info("classifying %d ETFs...", len(master))
    cfg = classifier.load_config(config_dir())
    classified = classifier.classify_all(master, cfg)
    etf_master.save_master(classified, master_dir)

    unclassified = classified[classified["asset_bucket"] == ""]
    if not unclassified.empty:
        logger.warning("%d ETFs remain unclassified", len(unclassified))
        for _, row in unclassified.head(10).iterrows():
            logger.warning("  unclassified: %s %s", row["fund_code"], row["fund_name"])

    for bucket in classified["asset_bucket"].unique():
        count = len(classified[classified["asset_bucket"] == bucket])
        logger.info("  %s: %d", bucket, count)


# ---------------------------------------------------------------------------
# Layer 1: 全市场资产热度
# ---------------------------------------------------------------------------

def cmd_calculate(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = etf_signal_raw_dir()
    master_dir = etf_signal_master_dir()
    daily_dir = etf_signal_daily_dir()

    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return

    codes = master["fund_code"].tolist()
    all_daily: list[pd.DataFrame] = []

    for code in codes:
        df = _load_etf_raw(raw_dir, code)
        if not df.empty:
            df["fund_code"] = code
            all_daily.append(df)

    if not all_daily:
        logger.error("no ETF daily data")
        return

    combined = pd.concat(all_daily, ignore_index=True)
    logger.info("computing indicators for %d rows across %d ETFs", len(combined), combined["fund_code"].nunique())

    # trade_date = 最近完整交易日（run-day 语义，非当前时刻快照）。
    # 盘中 raw 已含当日未收盘 K 线，必须把横截面锚定到完整交易日。
    # 优先级：--date → --target-date（run-day 传入）→ 按运行时间推断。
    anchor = getattr(args, "date", None) or getattr(args, "target_date", "") or _default_target_date()
    trade_date = pd.Timestamp(anchor)
    combined = combined[pd.to_datetime(combined["date"]) <= trade_date]
    max_avail = pd.to_datetime(combined["date"]).max()
    if pd.isna(max_avail):
        logger.error("no ETF daily data at or before target %s", trade_date.date())
        return
    if max_avail < trade_date and (getattr(args, "date", "") or getattr(args, "target_date", "")):
        raise ValueError(f"ETF 目标日 {anchor} 无行情，禁止回退到 {max_avail.date()}")
    if max_avail < trade_date:
        logger.warning("target %s 无数据，回退到最近可用交易日 %s", trade_date.date(), max_avail.date())
    trade_date = max_avail
    logger.info("rotation cross-section trade_date: %s", trade_date.date())

    indicators_df = indicators.compute_indicators(combined)
    logger.info("indicators computed: %d rows", len(indicators_df))

    # ── 全市场横截面 RPS（真实口径：rps15=15日收益百分位, rps20=20日, rps60=60日）──
    # v0.7.0 Market Pulse：rps1（今日）/ delta_rps15（动量）/ liquidity（流动性）仅 Observation 展示
    rotation_df = compute_rotation_metrics(combined, master)
    if not rotation_df.empty:
        rps_cols = [c for c in ["rps15", "rps20", "rps60", "rps1", "rps5", "delta_rps15", "liquidity",
                                "rank15", "rank15_prev5", "rank_change_5d"] if c in rotation_df.columns]
        indicators_df = indicators_df.merge(
            rotation_df[["fund_code"] + rps_cols].drop_duplicates(subset=["fund_code"]),
            on="fund_code", how="left",
        )
        logger.info(
            "cross-sectional RPS merged: rps15 median=%.1f, rank_change_5d available=%d",
            indicators_df["rps15"].median() if indicators_df["rps15"].notna().any() else float("nan"),
            int(indicators_df["rank_change_5d"].notna().sum()),
        )

        # 保存 Layer ① 轮动数据产物（按 trade_date 命名）
        # 横截面覆盖 = 横截面日期有行情的 ETF / 数据宇宙中出现的 ETF
        daily_dir.mkdir(parents=True, exist_ok=True)
        codes_with_data = combined["fund_code"].unique()
        codes_at_td = combined[combined["date"] == trade_date]["fund_code"].nunique() if len(codes_with_data) else 0
        rotation_status = _coverage_status(int(codes_at_td), len(codes_with_data))
        if getattr(args, "date", "") or getattr(args, "target_date", ""):
            current_codes = set(combined.loc[pd.to_datetime(combined["date"]) == trade_date, "fund_code"])
            rotation_df = rotation_df[rotation_df["fund_code"].isin(current_codes)].copy()
        from src.market_facts.etf_provenance import describe_series
        provenance = describe_series(combined, trade_date)
        rotation_df = rotation_df.drop(columns=[c for c in provenance.columns if c != "fund_code"],errors="ignore").merge(provenance,on="fund_code",how="left",validate="one_to_one")
        rotation_df = _stamp_output_meta(rotation_df, trade_date, rotation_status)
        rotation_path = daily_dir / f"rotation_{trade_date:%Y%m%d}.parquet"
        rotation_df.to_parquet(rotation_path, index=False)
        logger.info("rotation saved: %d ETFs (cross-section coverage=%d/%d, status=%s) -> %s",
                    len(rotation_df), int(codes_at_td), len(codes_with_data), rotation_status, rotation_path)
    else:
        # 兜底：指标缺失时按现有字段回退计算
        if "return_15d" in indicators_df.columns:
            indicators_df["rps15"] = indicators_df["return_15d"].rank(ascending=True, pct=True) * 100
        if "return_20d" in indicators_df.columns:
            indicators_df["rps20"] = indicators_df["return_20d"].rank(ascending=True, pct=True) * 100
        if "return_60d" in indicators_df.columns:
            indicators_df["rps60"] = indicators_df["return_60d"].rank(ascending=True, pct=True) * 100
        logger.warning("rotation metrics empty — fallback to per-date RPS")

    if getattr(args, "date", "") or getattr(args, "target_date", ""):
        indicators_df = indicators_df[pd.to_datetime(indicators_df["date"]) == trade_date].copy()

    # 标记 rps60 验收门控状态
    rps60_nunique = indicators_df["rps60"].nunique(dropna=True) if "rps60" in indicators_df.columns else 0
    if rps60_nunique <= 10:
        logger.warning("rps60_nunique=%d ≤ 10，指标验收未通过", rps60_nunique)
    else:
        logger.info("rps60_nunique=%d，指标验收通过", rps60_nunique)

    # 保存日指标
    daily_dir.mkdir(parents=True, exist_ok=True)
    indicators_df.to_parquet(daily_dir / "daily_indicators.parquet", index=False)
    indicators_df.to_parquet(daily_dir / f"daily_indicators_{trade_date:%Y%m%d}.parquet", index=False)

    logger.info("calculate complete: %d ETFs", len(indicators_df))


# ---------------------------------------------------------------------------
# P0-C: Signal
# ---------------------------------------------------------------------------

def cmd_retry_uncovered(args: argparse.Namespace) -> None:
    """专项重试目标日未覆盖 ETF。

    流程：
      1. EM 探针（510050 / 588000 / 520690）→ 评估接口状态
      2. 两阶段回填：小窗口 10 天探测 → 确认可用后拉 400 天
      3. 冷却退避：批内连续失败时指数退避

    优先级排序：
      1. core universe NOFILE（45 只）
      2. 已有本地数据但行数不足或未到目标日
      3. 科创板 588/589（新浪不覆盖，只走 EM）
      4. 其余非 core NOFILE

    完成后输出 CSV 明细到 diagnostics/。
    """
    logger = build_logger(args.log_level)
    raw_dir = etf_signal_raw_dir()
    master_dir = etf_signal_master_dir()
    diag_dir = etf_signal_diagnostics_dir()
    diag_dir.mkdir(parents=True, exist_ok=True)

    target_date_str = getattr(args, "target_date", "") or _default_target_date()
    target_date = pd.Timestamp(target_date_str).date()
    min_rows = getattr(args, "min_rows", 60)

    # ── 参数 ──
    BATCH_SIZE = getattr(args, "batch_size", 10)
    BATCH_PAUSE = getattr(args, "batch_pause", 30)
    REQUEST_INTERVAL = getattr(args, "interval", 2.0)
    HISTORY_LOOKBACK_DAYS = 400

    # ── Phase 0: EM 探针 ──────────────────────────────────
    logger.info("=" * 60)
    logger.info("RETRY-UNCOVERED — Phase 0: EM Probe")
    probe_results = _run_em_probe(logger, target_date_str)

    all_ok = all(v == "ok" for v in probe_results.values())
    mainstream_ok = probe_results.get("510050") == "ok"
    kcb_ok = probe_results.get("588000") == "ok"

    if not mainstream_ok:
        logger.info("")
        logger.info("✗ EM history API appears unavailable (510050 also failed).")
        logger.info("  This is NOT a per-code issue — deferring entire retry session.")
        logger.info("  Suggestion: run again at a different time window")
        logger.info("    - Tonight 20:30 CST")
        logger.info("    - Tomorrow 06:30 CST before market open")
        logger.info("  Also check: env | grep -i proxy")
        logger.info("=" * 60)
        return

    if not kcb_ok:
        logger.warning("  ⚠ STAR board (588000) probe failed — may need secid check")

    logger.info("  probe verdict: all_ok=%s, mainstream=%s, kcb=%s",
                all_ok, mainstream_ok, kcb_ok)
    logger.info("")

    # ── 读取诊断 CSV（已不需要，改为直接从 master 扫描） ──
    master = etf_master.load_master(master_dir)
    core = pd.read_parquet(master_dir / "core_universe.parquet")
    core_codes = set(core["fund_code"])

    # ── 扫描：互斥分类，只重试真正有修复价值的 ──
    BUFFER = 8  # 容忍 A 股假期与 weekend_count 估算误差
    all_stats: list[dict] = []
    uncovered_codes: list[dict] = []
    for _, row in master.iterrows():
        code = str(row["fund_code"])
        f = raw_dir / f"{code}.parquet"
        is_kcb = code.startswith(("588", "589"))
        is_core = code in core_codes

        if f.exists():
            df = pd.read_parquet(f)
            last_date = df["date"].max().date() if not df.empty and "date" in df.columns else None
            row_count = len(df)
            is_covered = last_date is not None and last_date >= target_date
            hist_ok = row_count >= min_rows
            if is_covered and hist_ok:
                all_stats.append({"code": code, "is_core": is_core, "covered": True, "hist_ok": True, "rows": row_count, "reason": ""})
                continue

            first_date = df["date"].min().date() if not df.empty and "date" in df.columns else None
            has_first = first_date is not None
            if has_first and last_date is not None:
                cal_days = (target_date - first_date).days
                weekday_count = int(np.busday_count(first_date, target_date)) if cal_days >= 0 else 0
            else:
                weekday_count = 0

            latest_str = str(last_date) if last_date else "EMPTY"
        else:
            latest_str = "NOFILE"
            row_count = 0
            is_covered = False
            hist_ok = False
            weekday_count = 0
            has_first = False

        # 互斥分类（判断顺序不可调换）
        if row_count == 0:
            reason = "no_file"
        elif is_kcb and not kcb_ok:
            reason = "source_unavailable"
        elif has_first and weekday_count < min_rows + BUFFER:
            reason = "newly_listed"
        else:
            reason = "source_limit"

        should_retry = reason in ("source_limit", "no_file")

        all_stats.append({"code": code, "is_core": is_core, "covered": is_covered, "hist_ok": hist_ok, "rows": row_count, "reason": reason})
        uncovered_codes.append({
            "code": code,
            "name": row.get("fund_name", ""),
            "is_core": is_core,
            "is_kcb": is_kcb,
            "reason": reason,
            "should_retry": should_retry,
            "has_file": f.exists(),
            "latest_before": latest_str,
            "row_count": row_count,
        })

    # ── 统计快照（互斥校验） ──
    total_etfs = len(master)
    fresh_covered = sum(1 for s in all_stats if s["covered"])
    hist_ok = sum(1 for s in all_stats if s["hist_ok"])
    reason_counts = {}
    for s in all_stats:
        r = s["reason"]
        if r:
            reason_counts[r] = reason_counts.get(r, 0) + 1
    core_hist_ng = sum(1 for s in all_stats if s["is_core"] and not s["hist_ok"])

    classified_total = hist_ok + sum(reason_counts.values())
    if classified_total != total_etfs:
        logger.warning("分类合计 %d ≠ Master %d，请检查 reason 互斥性", classified_total, total_etfs)

    logger.info("")
    logger.info("  %-35s : %d / %d", "当日行情覆盖", fresh_covered, total_etfs)
    logger.info("  %-35s : %d", f"历史深度达标 (≥{min_rows}行)", hist_ok)
    for reason in ("newly_listed", "source_unavailable", "source_limit", "no_file"):
        cnt = reason_counts.get(reason, 0)
        label = {"newly_listed": "上市不足（weekday_count < min_rows + BUFFER）",
                 "source_unavailable": "数据源不可用（EM-only + EM 探针失败）",
                 "source_limit": "数据源能力上限",
                 "no_file": "无本地文件"}.get(reason, reason)
        logger.info("  %-35s : %d", f"  ├ {label}", cnt)
    logger.info("  %-35s : %d", "core 历史不足", core_hist_ng)
    retry_count = sum(1 for u in uncovered_codes if u["should_retry"])
    logger.info("  %-35s : %d", "当前可重试 (source_limit+no_file)", retry_count)

    # 筛选真实重试队列
    retry_queue = [u for u in uncovered_codes if u["should_retry"]]

    if not retry_queue:
        logger.info("")
        if uncovered_codes:
            logger.info("存在历史不足标的，但均属合理原因 — 无需执行历史回填。")
            logger.info("  待 EM 恢复后 EM-only 标的自会进入重试队列；")
            logger.info("  上市不足标的随交易日推移自动满足条件。")
        else:
            logger.info("所有 ETF 均已覆盖且历史深度达标 — 无需执行历史回填。")
        return
    
    uncovered_codes = retry_queue

    # ── 排序：优先级（仅含 should_retry 标的） ──
    def priority(r):
        if r["is_core"] and r["reason"] == "no_file":
            return 0
        if r["reason"] == "no_file":
            return 1
        return 2  # source_limit

    uncovered_codes.sort(key=lambda r: (priority(r), r["code"]))
    total = len(uncovered_codes)

    logger.info("=" * 60)
    logger.info("RETRY-UNCOVERED — Phase 1: %d ETFs to retry (min_rows=%d) for target %s", total, min_rows, target_date_str)
    logger.info("  batch_size=%d  batch_pause=%ds  interval=%.1fs",
                BATCH_SIZE, BATCH_PAUSE, REQUEST_INTERVAL)
    logger.info("  priority: core(no_file) → no_file → source_limit")

    # ── 参数 ──
    BACKFILL_START = (target_date - timedelta(days=HISTORY_LOOKBACK_DAYS)).strftime("%Y%m%d")
    PROBE_WINDOW = 10  # 小窗口探测天数
    PROBE_START = (target_date - timedelta(days=PROBE_WINDOW)).strftime("%Y%m%d")

    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    covered_count = 0
    confirmed_no_data = 0
    transient_fail = 0
    deferred_count = 0
    result_rows: list[dict] = []

    for batch_idx in range(total_batches):
        batch = uncovered_codes[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]
        data_source.reset_em_circuit_breakers()

        logger.info("[Batch %d/%d] %d codes", batch_idx + 1, total_batches, len(batch))
        batch_fail = 0
        batch_aborted = False

        for item in batch:
            if batch_aborted:
                deferred_count += 1
                continue

            code = item["code"]
            existing = _load_etf_raw(raw_dir, code)
            is_backfill = existing.empty or len(existing) < min_rows

            # ── 阶段 A: 小窗口探测（10 天） ──
            logger.info("  [%s] probe %d days...", code, PROBE_WINDOW)
            try:
                df_probe = data_source.fetch_etf_hist(code, start_date=PROBE_START, end_date=target_date_str)
            except Exception as e:
                df_probe = pd.DataFrame()
                logger.warning("  [%s] probe exception: %s", code, e)

            if df_probe.empty:
                batch_fail += 1
                logger.warning("  [%s] ✗ probe failed (consecutive=%d)", code, batch_fail)
                final_status = _finalize_retry_status(code, existing, target_date, data_source)
                result_rows.append(_make_retry_row(item, existing, data_source, final_status, 0, "probe_failed"))
                if batch_fail >= 3:
                    logger.warning("  → 3 consecutive probe failures, aborting batch %d", batch_idx + 1)
                    batch_aborted = True
                time.sleep(REQUEST_INTERVAL + random.uniform(-0.3, 0.5))
                continue

            # 探测成功，重置失败计数
            batch_fail = 0
            probe_rows = len(df_probe)
            logger.info("  [%s] probe ✓ (%d rows)", code, probe_rows)

            # ── 阶段 B: 全量回填（400 天） ──
            if is_backfill:
                logger.info("  [%s] backfill %d days...", code, HISTORY_LOOKBACK_DAYS)
                try:
                    df_full = data_source.fetch_etf_hist(code, start_date=BACKFILL_START, end_date=target_date_str)
                except Exception as e:
                    df_full = pd.DataFrame()
                    logger.warning("  [%s] backfill exception: %s", code, e)

                if not df_full.empty:
                    combined = _merge_incremental(existing, df_full)
                    _save_etf_raw(combined, raw_dir, code)
                    covered_count += 1
                    logger.info("  [%s] ✓ backfilled (+%d rows)", code, len(df_full))
                    final_status = "covered"
                    rows_total = len(df_full)
                else:
                    # probe 成功但全量失败 → 只保存探测数据
                    combined = _merge_incremental(existing, df_probe)
                    _save_etf_raw(combined, raw_dir, code)
                    transient_fail += 1
                    logger.warning("  [%s] partial (probe ok, backfill failed)", code)
                    final_status = "partial_backfill"
                    rows_total = len(df_probe)
            else:
                # 增量更新
                start = (existing["date"].max().date() + timedelta(days=1)).strftime("%Y%m%d")
                try:
                    df_inc = data_source.fetch_etf_hist(code, start_date=start, end_date=target_date_str)
                except Exception as e:
                    df_inc = pd.DataFrame()
                    logger.warning("  [%s] incremental exception: %s", code, e)
                if not df_inc.empty:
                    combined = _merge_incremental(existing, df_inc)
                    _save_etf_raw(combined, raw_dir, code)
                    covered_count += 1
                    final_status = "covered"
                    rows_total = len(df_inc)
                else:
                    transient_fail += 1
                    final_status = "incremental_failed"
                    rows_total = 0

            result_rows.append(_make_retry_row(item, existing, data_source, final_status,
                                               rows_total, ""))

            time.sleep(REQUEST_INTERVAL + random.uniform(-0.3, 0.5))

        # 批次间冷却
        if batch_idx < total_batches - 1:
            actual_pause = BATCH_PAUSE * (1 + batch_fail)  # 失败越多，冷却越长
            logger.info("  batch %d done — pause %.0fs", batch_idx + 1, actual_pause)
            time.sleep(actual_pause)

    # ── 汇总 ──
    core_covered = sum(1 for r in result_rows if r["is_core"] and r["final_status"] == "covered")
    core_total = sum(1 for r in result_rows if r["is_core"])

    logger.info("=" * 60)
    logger.info("RETRY-UNCOVERED SUMMARY")
    logger.info("  total attempted       : %d", len(result_rows))
    logger.info("  successfully covered  : %d", covered_count)
    logger.info("  transient failure     : %d", transient_fail)
    logger.info("  no data confirmed     : %d", confirmed_no_data)
    logger.info("  deferred (batch break) : %d", deferred_count)
    logger.info("  core covered          : %d / %d", core_covered, core_total)

    status_counts = {}
    for r in result_rows:
        s = r["final_status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    for s, c in sorted(status_counts.items()):
        logger.info("    %-30s : %d", s, c)

    out_path = diag_dir / f"etf_retry_uncovered_{target_date_str}.csv"
    out_df = pd.DataFrame(result_rows)
    out_cols = ["code","name","is_core","is_kcb","local_latest_before",
                "retry_source","retry_result","rows_fetched","latest_after",
                "final_status","error_type"]
    out_df[out_cols].to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("detail CSV: %s", out_path)
    logger.info("=" * 60)


def _make_retry_row(item: dict, existing: pd.DataFrame,
                    ds: Any, final_status: str, rows_fetched: int,
                    error_type: str) -> dict:
    latest_before = "NOFILE"
    if not existing.empty and "date" in existing.columns:
        d = existing["date"].max()
        latest_before = str(d.date()) if pd.notna(d) else "EMPTY"
    latest_after = latest_before
    if final_status == "covered":
        df_tmp = _load_etf_raw(etf_signal_raw_dir(), item["code"])
        if not df_tmp.empty:
            latest_after = str(df_tmp["date"].max().date())
    return {
        "code": item["code"],
        "name": item.get("name", ""),
        "is_core": item["is_core"],
        "is_kcb": item["is_kcb"],
        "local_latest_before": latest_before,
        "retry_source": ds.get_fetch_stats().get(item["code"], {}).get("source", ""),
        "retry_result": "covered" if "covered" in final_status else ("partial" if final_status.startswith("partial") else "failed"),
        "rows_fetched": rows_fetched,
        "latest_after": latest_after,
        "final_status": final_status,
        "error_type": error_type or ds.get_fetch_stats().get(item["code"], {}).get("primary_error_type", ""),
    }


def _finalize_retry_status(code: str, existing: pd.DataFrame,
                           target_date: date_type, ds: Any) -> str:
    """根据现有数据和 fetch_stats 判定最终状态。"""
    if not existing.empty and "date" in existing.columns:
        d = existing["date"].max()
        if pd.notna(d) and d.date() >= target_date:
            return "already_covered"
    stats = ds.get_fetch_stats().get(code, {})
    err = stats.get("primary_error_type", "")
    if err == "no_source_for_kcb":
        return "star_board_em_also_failed"
    if err in ("em_rate_limit_circuit", "em_schema_circuit"):
        return "em_circuit_still_active"
    return "no_data_confirmed"


# ---------------------------------------------------------------------------
# Data-source preflight & gap backfill（保护机制）
# ---------------------------------------------------------------------------

# 每前缀组抽样一只代表，覆盖深市/沪市宽基/新代码/科创板等 routing 分支
_PREFLIGHT_PREFIX_GROUPS = ["159", "510", "511", "520", "530", "560", "588", "589"]


def _preflight_sample_codes(master: pd.DataFrame) -> list[str]:
    groups: dict[str, list[str]] = {}
    for code in master["fund_code"].astype(str):
        groups.setdefault(code[:3], []).append(code)
    picked: list[str] = []
    for prefix in _PREFLIGHT_PREFIX_GROUPS:
        if prefix in groups:
            picked.append(groups[prefix][0])
    return picked


def cmd_preflight(args: argparse.Namespace) -> None:
    """数据源抽样探测：30 秒内判断缺口是否 routing / viability 层问题。

    输出：symbol → exchange → sina symbol → viable → latest bar → source。
    """
    logger = build_logger(args.log_level)
    master_dir = etf_signal_master_dir()
    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return

    explicit_raw = getattr(args, "codes", "") or ""
    explicit = [c.strip() for c in explicit_raw.split(",") if c.strip()] if isinstance(explicit_raw, str) else []
    codes = explicit or _preflight_sample_codes(master)
    if not codes:
        logger.error("no sample codes derived from master")
        return

    start_date = getattr(args, "start_date", "") or (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
    data_source.clear_fetch_stats()

    print(f"{'code':<8} {'exchange':<9} {'sina_symbol':<10} {'viable':<8} {'source':<7} {'rows':<6} {'latest_bar':<12} nav_latest")
    print("-" * 80)
    for code in codes:
        exchange = data_source.sina_exchange(code)
        symbol = data_source.sina_symbol(code)
        viable = data_source.is_sina_viable(code)
        df = data_source.fetch_etf_hist(code, start_date=start_date)
        if df is not None and not df.empty:
            latest = df["date"].max().date()
            rows = len(df)
        else:
            latest, rows = None, 0
        info = data_source.get_fetch_stats().get(code, {})
        source = info.get("source", "none")
        nav_latest, _ = data_source.fetch_nav_latest(code)
        print(f"{code:<8} {exchange:<9} {symbol:<10} {str(viable):<8} {source:<7} {rows:<6} {str(latest):<12} {nav_latest}")

    logger.info("preflight done — 若 latest_bar 大面积落后 target 而 nav_latest 正常，多为 routing/viability 层问题")


def cmd_backfill(args: argparse.Namespace) -> None:
    """目标日缺口回填：分类 + checkpoint/resume + 耗时统计。"""
    logger = build_logger(args.log_level)
    from . import backfill

    target = getattr(args, "target_date", "") or _default_target_date()
    master = etf_master.load_master(etf_signal_master_dir())
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return

    report = backfill.run_backfill(
        raw_dir=etf_signal_raw_dir(),
        master=master,
        target=target,
        resume=not getattr(args, "no_resume", False),
        attempts=getattr(args, "attempts", 3),
        retryable_only=getattr(args, "retryable_only", False),
        log=logger.info,
    )
    if report.failed:
        logger.warning("backfill 有 %d 只未补上，请查看 checkpoint：data/sources/etf_em_sina/state/backfill_%s.json",
                       report.failed, target)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ETF 行情采集与市场事实计算")
    sub = p.add_subparsers(dest="command", required=True)

    p_bootstrap = sub.add_parser("bootstrap", help="初始化 ETF Master 并拉取全量历史数据")
    p_bootstrap.add_argument("--log-level", default="INFO")

    p_bootstrap_core = sub.add_parser("bootstrap-core", help="阶段 A：初始化核心 Universe 并拉取历史（200~500 只）")
    p_bootstrap_core.add_argument("--max-count", type=int, default=300, help="核心池最大数量")
    p_bootstrap_core.add_argument("--min-amount", type=float, default=1_000_000, help="成交额门槛")
    p_bootstrap_core.add_argument("--skip-history", action="store_true", help="仅筛选核心池，不拉取历史")
    p_bootstrap_core.add_argument("--log-level", default="INFO")

    p_update = sub.add_parser("update", help="增量更新 ETF 日行情")
    p_update.add_argument("--target-date", default="", help="目标日期 YYYYMMDD")
    p_update.add_argument("--log-level", default="INFO")

    p_classify = sub.add_parser("classify", help="对 ETF 执行资产桶和暴露类型分类")
    p_classify.add_argument("--log-level", default="INFO")

    p_calc = sub.add_parser("calculate", help="计算技术指标")
    p_calc.add_argument("--date", default="", help="目标日期")
    p_calc.add_argument("--log-level", default="INFO")

    p_retry = sub.add_parser("retry-uncovered", help="专项重试目标日未覆盖 ETF（分批+熔断器重置）")
    p_retry.add_argument("--target-date", default="", help="目标日期 YYYYMMDD")
    p_retry.add_argument("--min-rows", type=int, default=60, help="最低历史行数要求（默认 60，与 RPS60 对齐）")
    p_retry.add_argument("--batch-size", type=int, default=10, help="每批数量")
    p_retry.add_argument("--batch-pause", type=int, default=30, help="批次间暂停秒数")
    p_retry.add_argument("--interval", type=float, default=2.0, help="单只请求间隔秒数")
    p_retry.add_argument("--log-level", default="INFO")

    p_preflight = sub.add_parser("preflight", help="数据源抽样探测：routing/viability/最新bar（30 秒判断是否 routing 层问题）")
    p_preflight.add_argument("--codes", default="", help="逗号分隔抽样代码（默认按前缀组自动取代表）")
    p_preflight.add_argument("--start-date", default="", help="探测起始日 YYYYMMDD（默认近 60 天）")
    p_preflight.add_argument("--log-level", default="INFO")

    p_backfill = sub.add_parser("backfill", help="目标日缺口回填：分类 + checkpoint/resume + 耗时统计")
    p_backfill.add_argument("--target-date", default="", help="目标日期 YYYYMMDD")
    p_backfill.add_argument("--no-resume", action="store_true", help="忽略 checkpoint，重新处理全部缺口")
    p_backfill.add_argument("--retryable-only", action="store_true", help="只重试可恢复类别（SOURCE_STALE/RATE_LIMITED），跳过结构性失败")
    p_backfill.add_argument("--attempts", type=int, default=3, help="每只最大请求次数")
    p_backfill.add_argument("--log-level", default="INFO")

    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    dispatch = {
        "bootstrap": cmd_bootstrap,
        "bootstrap-core": cmd_bootstrap_core,
        "update": cmd_update,
        "classify": cmd_classify,
        "calculate": cmd_calculate,
        "retry-uncovered": cmd_retry_uncovered,
        "preflight": cmd_preflight,
        "backfill": cmd_backfill,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
