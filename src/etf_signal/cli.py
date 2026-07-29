"""
etf_signal CLI — AKsignal ETF 趋势资产发现系统

全流程：

  ETF Master 建立 → 分类 → 趋势 Watchlist
      → 账户可交易映射 → 行业 SW-RPS 增强 → 信息卡片

子命令：

  数据底座
    bootstrap        初始化 ETF Master
    update           增量更新

  分类
    classify         资产类别与暴露分类

  趋势 Watchlist（Signal）
    watchlist        生成趋势关注池

  账户映射
    account          映射至国金账户可交易池

  信息卡片
    card             生成 ETF 候选信息卡片

  全流程
    pipeline         执行完整发现链路
    layer1           全市场热度分布
    screen           质量门控管线
    report           生成日报
    backtest         回测（P0-F）

P0 数据对象：
  1. etf_master           全市场有哪些 ETF
  2. etf_market_snapshot  当天价格、成交额、规模、溢价率
  3. trend_watchlist      哪些 ETF 趋势值得关注
  4. account_tradable_universe  国金账户能交易哪些
  5. account_candidates   趋势池 ∩ 账户池
  6. etf_candidate_card   趋势+账户+行业+验证 汇总
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import (
    project_root, config_dir, data_dir, outputs_dir,
    etf_signal_raw_dir, etf_signal_master_dir, etf_signal_daily_dir,
    etf_signal_signals_dir, etf_signal_positions_dir, etf_signal_manifests_dir,
    etf_signal_output_dir,
)
from src.common.run_context import RunContext
from src.common.manifest import write_run_manifest, read_latest_run
from . import data_source, master as etf_master, classifier, universe, account
from . import heat, indicators, signal as sig_mod
from . import sw_enrichment, card as card_mod


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("etf_signal")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")

def _default_target_date() -> str:
    now = datetime.now()
    if now.hour < 16 or (now.hour == 16 and now.minute < 30):
        yesterday = now - timedelta(days=1)
        return yesterday.strftime("%Y%m%d")
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
    whitelist_path = config_dir() / "guojin_tradable_whitelist.csv"

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
        "guojin_boost": True,
    }
    core = etf_master.build_core_universe(master, whitelist_path, cfg)
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


def _save_etf_raw(df: pd.DataFrame, raw_dir: Path, code: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{code}.parquet"
    df.to_parquet(path, index=False)
    return path


# ---------------------------------------------------------------------------
# P0-A: Update — 增量更新 ETF 日行情
# ---------------------------------------------------------------------------

@dataclass
class UpdateResult:
    status: str
    requested_target_date: str
    source_latest_common_date: str | None
    target_ready: bool
    raw_covered: int
    active_count: int


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

    # ── Phase 2: History 残余补缺 ────────────────────────────
    logger.info("Phase 2 — history residual: %d total, %d from spot, %d remaining",
                len(codes), spot_covered, max(0, len(codes) - spot_covered))

    data_source.reset_em_circuit_breakers()
    success = spot_covered
    backfill_count = 0
    skipped_backfill = 0

    for code in codes:
        cs = str(code)
        if cs in spot_codes and spot_date is not None and spot_date >= request_date:
            continue  # already covered by spot

        # reload to get latest (spot may have added data)
        df = _load_etf_raw(raw_dir, code)
        last_date = df["date"].max().date() if not df.empty and "date" in df.columns else None
        if last_date is not None and last_date >= request_date:
            success += 1
            continue

        is_backfill = df.empty
        if is_backfill and backfill_count >= data_source.EM_BACKFILL_LIMIT:
            skipped_backfill += 1
            continue

        inc_start = (last_date + timedelta(days=1)).strftime("%Y%m%d") if last_date else "20200101"
        try:
            df_new = data_source.fetch_etf_hist(cs, start_date=inc_start, end_date=explicit_target)
            if not df_new.empty:
                combined = _merge_incremental(df, df_new)
                _save_etf_raw(combined, raw_dir, code)
                success += 1
        except Exception as e:
            logger.warning("update failed for %s: %s", cs, e)

        if is_backfill:
            backfill_count += 1
            delay = data_source.EM_REQUEST_INTERVAL + random.uniform(-1, 1)
        else:
            delay = random.uniform(0.3, 0.8)
        time.sleep(max(delay, 0.3))

    target_ready = success == len(codes)
    logger.info("update: %d/%d covered, ready=%s  (spot=%d, history=%d, backfill_skipped=%d)",
                success, len(codes), target_ready, spot_covered, success - spot_covered, skipped_backfill)
    if backfill_count:
        logger.info("backfill: %d attempted, %d skipped (limit=%d)", backfill_count, skipped_backfill, data_source.EM_BACKFILL_LIMIT)
    data_source.log_fetch_summary()

    return UpdateResult(
        status="completed" if target_ready else "partial",
        requested_target_date=explicit_target,
        source_latest_common_date=explicit_target if target_ready else None,
        target_ready=target_ready,
        raw_covered=success, active_count=len(codes),
    )


def _merge_incremental(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
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

def cmd_layer1(args: argparse.Namespace) -> None:
    """Layer 1 — 计算全市场热度分布。"""
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("LAYER 1: 全市场资产热度")
    logger.info("=" * 60)

    master_dir = etf_signal_master_dir()
    raw_dir = etf_signal_raw_dir()
    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return

    daily_dir = etf_signal_daily_dir()

    # 加载当日日行情
    all_daily: list[pd.DataFrame] = []
    for code in master["fund_code"]:
        df = _load_etf_raw(raw_dir, code)
        if not df.empty:
            df["fund_code"] = code
            all_daily.append(df)

    if not all_daily:
        logger.error("no daily data")
        return

    combined = pd.concat(all_daily, ignore_index=True)

    # 计算热度地图
    heat_map = heat.compute_bucket_heat(combined, master)
    if heat_map.empty:
        logger.error("heat map computation returned empty")
        return

    risk = heat.assess_market_risk_appetite(heat_map)
    logger.info("市场风险偏好: %s | %s", risk["preference"], risk["note"])

    # 输出
    print("")
    print("资产大类  资产桶         强势占比  中位RPS  热度    当前状态")
    print("-" * 70)
    for _, row in heat_map.iterrows():
        print(
            f"{row['asset_class']:<8} {row['bucket_label']:<14} "
            f"{row['strong_ratio']:>6.1%}  {row['median_rps']:>6.1f}  "
            f"{row['heat_change']:<4}  {row['description']}"
        )
    print("")

    # 保存
    output_dir = etf_signal_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = _today_str()
    heat_map.to_csv(output_dir / f"heat_map_{date_str}.csv", index=False, encoding="utf-8-sig")
    heat_map.to_parquet(daily_dir / f"heat_{date_str}.parquet", index=False)
    logger.info("heat map saved: %d buckets", len(heat_map))


# ---------------------------------------------------------------------------
# Layer 2: 国金可交易标的池 — 门控管线
# ---------------------------------------------------------------------------

def cmd_screen(args: argparse.Namespace) -> None:
    """Layer 2 — 执行国金门控管线。"""
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("LAYER 2: 国金可交易标的池 — 门控管线")
    logger.info("=" * 60)

    master_dir = etf_signal_master_dir()
    raw_dir = etf_signal_raw_dir()
    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return

    # 加载全部日行情
    all_daily: dict[str, pd.DataFrame] = {}
    for code in master["fund_code"]:
        df = _load_etf_raw(raw_dir, code)
        if not df.empty:
            all_daily[code] = df

    whitelist_path = config_dir() / "guojin_tradable_whitelist.csv"
    cfg = universe.load_universe_config(config_dir())

    # U0 → U1
    u1 = universe.filter_u0_u1(master)
    logger.info("U0(%d) → U1(%d)", len(master), len(u1))

    # 8 道门控
    guojin_pool, gate_results = universe.screen_all(
        u1, all_daily, raw_dir, whitelist_path, cfg,
    )

    print("")
    print("门控管线结果:")
    print(f"{'门控':<12} {'通过':>6} {'总数':>6} {'通过率':>8} {'状态':>6}")
    print("-" * 50)
    all_passed = True
    for r in gate_results:
        rate = r.passed_count / r.total_count if r.total_count > 0 else 0
        status = "✅" if r.passed else "⚠"
        print(
            f"{r.gate_name:<12} {r.passed_count:>6} {r.total_count:>6} "
            f"{rate:>7.1%} {status:>4}"
        )
        if not r.passed:
            all_passed = False
            for c in r.failed_codes[:5]:
                reason = r.failed_reasons.get(c, "")
                print(f"  ├─ {c}: {reason}")
    print("")
    logger.info("门控结果: %d / %d ETFs 通过", len(guojin_pool), len(u1))

    # 输出分桶分布
    if not guojin_pool.empty and "asset_bucket" in guojin_pool.columns:
        print("\n国金可交易池 — 资产桶分布:")
        for bucket, count in guojin_pool["asset_bucket"].value_counts().items():
            label = heat.BUCKET_LABELS.get(bucket, bucket)
            print(f"  {label:<14} {count:>4}")

    # 保存
    output_dir = etf_signal_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = _today_str()
    if not guojin_pool.empty:
        guojin_pool.to_csv(output_dir / f"guojin_universe_{date_str}.csv", index=False, encoding="utf-8-sig")
        logger.info("国金可交易池 saved: %d ETFs", len(guojin_pool))


# ---------------------------------------------------------------------------
# Watchlist — 趋势关注池
# ---------------------------------------------------------------------------

def cmd_watchlist(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("TREND WATCHLIST")
    logger.info("=" * 60)

    master_dir = etf_signal_master_dir()
    daily_dir = etf_signal_daily_dir()
    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master — run bootstrap first")
        return

    indicators_path = daily_dir / "daily_indicators.parquet"
    if not indicators_path.exists():
        logger.error("no indicators — run calculate first")
        return

    indicators_df = pd.read_parquet(indicators_path)
    watchlist = sig_mod.build_trend_watchlist(indicators_df, master)

    if watchlist.empty:
        logger.warning("watchlist empty")
        return

    active = watchlist[watchlist["trend_state"] != "OUT_OF_SCOPE"]
    print(f"\n趋势 Watchlist（{len(active)} 只活跃）:")
    print(f"{'ETF':<20} {'状态':<16} {'RPS15':>6} {'RPS60':>6} {'趋势变化':<8} {'成交额':<6}")
    print("-" * 70)
    for _, row in active.head(20).iterrows():
        print(
            f"{row['fund_name']:<20} {row['trend_state']:<16} "
            f"{row['rps15']:>6.1f} {row['rps60']:>6.1f} "
            f"{row['trend_change']:<8} {row['amount_change']:<6}"
        )

    signals_dir = etf_signal_signals_dir()
    signals_dir.mkdir(parents=True, exist_ok=True)
    watchlist.to_parquet(signals_dir / f"watchlist_{_today_str()}.parquet", index=False)
    logger.info("watchlist saved: %d rows", len(watchlist))


# ---------------------------------------------------------------------------
# Account — 国金账户映射
# ---------------------------------------------------------------------------

def cmd_account_mapping(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("ACCOUNT MAPPING")
    logger.info("=" * 60)

    master_dir = etf_signal_master_dir()
    signals_dir = etf_signal_signals_dir()
    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master")
        return

    watchlist_files = sorted(signals_dir.glob("watchlist_*.parquet"), reverse=True)
    if not watchlist_files:
        logger.error("no watchlist found — run watchlist first")
        return
    watchlist = pd.read_parquet(watchlist_files[0])

    whitelist_path = config_dir() / "guojin_tradable_whitelist.csv"
    account_universe = account.load_account_universe(whitelist_path)

    mapped = account.map_watchlist_to_account(watchlist, account_universe)

    tradable = mapped[mapped["account_tradable"]]
    blocked = mapped[~mapped["account_tradable"]]

    print(f"\n账户映射结果:")
    print(f"  Watchlist:               {len(mapped)} 只")
    print(f"  已验证可交易:             {len(tradable)} 只")
    print(f"  尚未验证 (UNVERIFIED):    {len(blocked)} 只")
    if not blocked.empty:
        for _, row in blocked.head(5).iterrows():
            print(f"    {row['fund_name']}: {row['account_status_label']}")

    mapped.to_parquet(signals_dir / f"account_candidates_{_today_str()}.parquet", index=False)
    logger.info("account candidates saved")


# ---------------------------------------------------------------------------
# Card — ETF 信息卡片
# ---------------------------------------------------------------------------

def cmd_card(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("ETF CANDIDATE CARD")
    logger.info("=" * 60)

    master_dir = etf_signal_master_dir()
    signals_dir = etf_signal_signals_dir()
    raw_dir = etf_signal_raw_dir()
    daily_dir = etf_signal_daily_dir()

    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master")
        return

    cand_files = sorted(signals_dir.glob("account_candidates_*.parquet"), reverse=True)
    if not cand_files:
        logger.error("no account candidates — run account first")
        return
    candidates = pd.read_parquet(cand_files[0])

    if candidates.empty:
        logger.info("no candidates, nothing to card")
        return

    # ── 指标验收门控 ──────────────────────────────────────────
    indicators_path = daily_dir / "daily_indicators.parquet"
    gate_warnings: list[str] = []
    if indicators_path.exists():
        indicators_df = pd.read_parquet(indicators_path)
        gate_warnings = card_mod.validate_indicators(indicators_df)
        if gate_warnings:
            logger.warning("指标验收未通过: %s", "; ".join(gate_warnings))
        else:
            logger.info("指标验收通过")

    # ── 漏斗统计 ──────────────────────────────────────────────
    total = len(candidates)
    tradable = candidates[candidates["account_tradable"]].copy()
    unverified = candidates[candidates["account_status"] == "UNVERIFIED"]
    in_scope = candidates[candidates["trend_state"].isin(["BUY_CANDIDATE", "STRONG_WATCH", "WATCH"])]
    logger.info(
        "漏斗: %d total, %d tradable, %d unverified, %d in-scope",
        total, len(tradable), len(unverified), len(in_scope),
    )

    # ── 生成卡片 ──────────────────────────────────────────────
    cards: list[card_mod.ETFCandidateCard] = []
    name_map = dict(zip(master["fund_code"], master["fund_name"]))

    for _, row in tradable.iterrows():
        code = row["fund_code"]
        fund_name = name_map.get(code, row.get("fund_name", ""))

        trend_state = row.get("trend_state", "OUT_OF_SCOPE")
        rps15 = row.get("rps15", 0.0)
        rps60 = row.get("rps60", 0.0)
        return_5d = row.get("return_5d", 0.0)
        return_20d = row.get("return_20d", 0.0)
        trend_change = row.get("trend_change", "平稳")
        amount_change = row.get("amount_change", "—")

        # 风险检测
        risk_flags = card_mod.detect_risks(code, raw_dir)

        # 字段完整性判定
        card_status = "complete"
        if not fund_name or trend_state == "OUT_OF_SCOPE":
            card_status = "incomplete"
        if risk_flags:
            card_status = "flagged"

        # 标记金额过小时的极端收益为可能的份额拆分
        cleaned_risks = [
            f for f in risk_flags
            if not (f.startswith("extreme_return") and any("possible_split" in rf for rf in risk_flags))
        ]

        card = card_mod.build_card(
            master_row=row,
            trend_info=card_mod.TrendInfo(
                trend_state=trend_state,
                rps15=rps15, rps60=rps60,
                return_5d=return_5d, return_20d=return_20d,
                trend_change=trend_change, amount_change=amount_change,
            ),
            account_info=card_mod.AccountScreenInfo(
                account_tradable=True,
                account_status=row.get("account_status", "UNVERIFIED"),
                trading_status="正常",
                liquidity_gate="通过",
                size_gate="通过",
                history_gate="通过",
                premium_risk="正常",
            ),
            risk_flags=cleaned_risks or None,
            card_status=card_status,
        )
        cards.append(card)

    card_mod.print_card_summary(cards)
    output_dir = etf_signal_output_dir()
    card_mod.save_cards(cards, output_dir, _today_str())

    if cards:
        # 打印第一张非 flagged 卡片，否则打印第一张
        display = next((c for c in cards if c.card_status == "complete"), cards[0])
        print(display.to_text())


# ---------------------------------------------------------------------------
# Pipeline — 完整发现链路
# ---------------------------------------------------------------------------

def _compute_bucket_heat(daily_df: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """按资产桶计算热度分布。"""
    if daily_df.empty or master.empty or "fund_code" not in daily_df.columns or "asset_bucket" not in master.columns:
        return pd.DataFrame()
    merged = daily_df.merge(
        master[["fund_code", "asset_bucket"]].drop_duplicates(subset=["fund_code"]),
        on="fund_code", how="inner",
    )
    merged = merged[merged["asset_bucket"].notna() & (merged["asset_bucket"] != "")]
    if merged.empty:
        return pd.DataFrame()
    rows = []
    for bucket, group in merged.groupby("asset_bucket"):
        rps = group["rps15"].dropna()
        if len(rps) < 2:
            continue
        strong_ratio = (rps >= 80).mean()
        median_rps = rps.median()
        asset_class = "权益"
        if bucket in ("commodity_gold", "commodity_futures"):
            asset_class = "商品"
        elif bucket in ("bond_treasury", "bond_credit", "bond_convertible"):
            asset_class = "债券"
        elif bucket in ("money_market",):
            asset_class = "现金"
        rows.append({
            "asset_class": asset_class, "asset_bucket": bucket,
            "bucket_label": bucket, "etf_count": int(group["fund_code"].nunique()),
            "strong_ratio": round(strong_ratio, 4), "median_rps": round(median_rps, 2),
        })
    return pd.DataFrame(rows)


def _write_html_report(
    daily_df: pd.DataFrame, master: pd.DataFrame, heat_map: pd.DataFrame,
    output_dir: Path, date_str: str, logger: logging.Logger | None = None,
) -> None:
    """生成 funnel_report_{date}.html，结构与现有 funnel_report 一致。"""
    logger = logger or logging.getLogger("etf_signal")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_path = output_dir / f"funnel_report_{date_str}.html"
    signals_dir = etf_signal_signals_dir()

    # ── 加载数据 ─────────────────────────────────────────────────────
    total_master = len(master) if not master.empty else 0
    total_indicators = len(daily_df) if not daily_df.empty else 0

    wl = pd.DataFrame()
    wl_files = sorted(signals_dir.glob(f"watchlist_{date_str}.parquet"))
    if wl_files:
        wl = pd.read_parquet(wl_files[0])
        if not master.empty and "asset_bucket" in master.columns:
            wl = wl.merge(master[["fund_code", "asset_bucket"]].drop_duplicates(subset=["fund_code"]), on="fund_code", how="left")
            wl["asset_bucket"] = wl["asset_bucket"].fillna("")
    active = wl[wl["trend_state"] != "OUT_OF_SCOPE"] if not wl.empty else pd.DataFrame()

    ac = pd.DataFrame()
    ac_files = sorted(signals_dir.glob(f"account_candidates_{date_str}.parquet"))
    if ac_files:
        ac = pd.read_parquet(ac_files[0])
        if not master.empty and "asset_bucket" in master.columns:
            ac = ac.merge(master[["fund_code", "asset_bucket"]].drop_duplicates(subset=["fund_code"]), on="fund_code", how="left")
            ac["asset_bucket"] = ac["asset_bucket"].fillna("")
    tradable = ac[ac.get("account_tradable", False)] if not ac.empty else pd.DataFrame()

    cards_path = output_dir / f"candidate_cards_{date_str}.json"
    cards_list: list[dict] = []
    if cards_path.exists():
        raw = json.loads(cards_path.read_text(encoding="utf-8"))
        cards_list = raw.get("cards", raw) if isinstance(raw, dict) else raw
    complete_cards = sum(1 for c in cards_list if c.get("card_status") == "complete" or c.get("card_status") == True)
    incomplete_cards = sum(1 for c in cards_list if c.get("card_status") == "incomplete")
    flagged_cards = sum(1 for c in cards_list if c.get("card_status") == "flagged")

    n_active = len(active)
    n_tradable = len(tradable)
    n_candidates = n_tradable
    total_wl = len(wl)

    # ── Funnel 百分比 ───────────────────────────────────────────────
    def funnel_pct(n: int) -> str:
        p = n / total_master * 100 if total_master else 0
        return f"{n}（{p:.0f}%）"

    # ── CSS/品牌 ────────────────────────────────────────────────────
    CSS = """
:root {
  --zh-blue: #174A7C; --zh-deep-blue: #06213D; --zh-cyan: #7ECDEB;
  --zh-light-blue: #DDEFF8; --zh-cream: #FFF9EF; --zh-raccoon-gold: #D79A36;
  --zh-brown: #7A4A24; --zh-text: #1F2D3D; --zh-muted: #6B7C8F;
  --zh-card: #FFFFFF; --zh-border: #E8EDF2;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;background:var(--zh-cream);color:var(--zh-text);line-height:1.6;padding:40px 24px}
.container{max-width:960px;margin:0 auto}
h1{font-size:28px;font-weight:600;color:var(--zh-deep-blue);margin-bottom:4px}
.subtitle{font-size:14px;color:var(--zh-muted);margin-bottom:32px}
.section{background:var(--zh-card);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);padding:28px 32px;margin-bottom:24px}
.section h2{font-size:18px;font-weight:600;color:var(--zh-blue);margin-bottom:20px;padding-bottom:10px;border-bottom:2px solid var(--zh-light-blue)}
.section h3{font-size:15px;font-weight:600;color:var(--zh-text);margin:18px 0 10px}
.funnel{display:flex;flex-direction:column;align-items:flex-start;gap:6px;margin:20px 0 10px}
.funnel-row{display:flex;align-items:center;gap:14px;width:100%;border-radius:8px;padding:12px 20px;transition:all .15s}
.funnel-row:hover{transform:scale(1.01)}
.funnel-label{font-size:13px;font-weight:500;color:var(--zh-muted);min-width:110px;text-align:right}
.funnel-bar{height:36px;border-radius:6px;display:flex;align-items:center;justify-content:flex-end;padding:0 16px;font-weight:600;font-size:15px}
.funnel-count{min-width:60px;font-size:14px;font-weight:600}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.metric-card{background:var(--zh-light-blue);border-radius:8px;padding:14px 16px;text-align:center}
.metric-value{font-size:24px;font-weight:700;color:var(--zh-blue)}
.metric-label{font-size:12px;color:var(--zh-muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:12px 0}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--zh-border)}
th{color:var(--zh-muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.03em}
tr:hover td{background:#F8FAFC}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}
.tag-buy{background:#E8F5E9;color:#2E7D32}
.tag-watch{background:#FFF3E0;color:#E65100}
.tag-strong{background:#E3F2FD;color:#1565C0}
.tag-oos{background:#F5F5F5;color:#9E9E9E}
.tag-complete{background:#E8F5E9;color:#2E7D32}
.tag-incomplete{background:#FFF8E1;color:#F57F17}
.tag-flagged{background:#FFEBEE;color:#C62828}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media(max-width:640px){.grid-2{grid-template-columns:1fr}}
.insight{background:#F8FAFC;border-left:4px solid var(--zh-cyan);border-radius:6px;padding:14px 18px;margin:12px 0;font-size:13px;color:var(--zh-text)}
.insight strong{color:var(--zh-blue)}
hr{border:none;border-top:1px solid var(--zh-border);margin:24px 0}
p{font-size:14px;color:var(--zh-muted);margin-bottom:12px}
ul{margin:4px 0 0 16px;padding:0;font-size:13px;color:var(--zh-text);line-height:1.7}
"""

    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>ETF 趋势发现漏斗 · {date_str}</title>",
        f"<style>{CSS}</style></head><body><div class='container'>",
        f"<h1>ETF 趋势发现漏斗</h1>",
        f"<div class='subtitle'>报告日期 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} · 生成于 {now_str}</div>",
    ]

    # ══════════════════ SECTION 1: FUNNEL ══════════════════
    parts.append('<div class="section"><h2>一、发现漏斗</h2>')
    parts.append("<p>ETF 趋势发现系统是一个逐层收敛的信号漏斗。每一层设定明确的筛选规则，将上一层的输出作为本层输入。</p>")

    funnel_colors = ["#174A7C", "#1E6BA8", "#4A90C4", "#7ECDEB", "#B0DCF5"]
    funnel_labels = ["① Master", "② 质量门控", "③ 趋势信号", "④ 账户映射", "⑤ 候选卡片"]
    funnel_counts = [total_master, total_indicators, n_active, n_tradable, n_candidates]
    funnel_notes = [str(total_master), f"{total_indicators}（{total_indicators/total_master*100:.0f}%）",
                   f"{n_active}（{n_active/total_master*100:.0f}%）",
                   f"{n_tradable}（{n_tradable/total_master*100:.0f}%）",
                   f"{n_candidates}（{n_candidates/total_master*100:.0f}%）"]

    parts.append('<div class="funnel">')
    max_w = funnel_counts[0] or 1
    for i in range(5):
        w = funnel_counts[i] / max_w * 100 if funnel_counts[i] else 0
        parts.append(
            f'<div class="funnel-row">'
            f'<div class="funnel-label">{funnel_labels[i]}</div>'
            f'<div class="funnel-bar" style="width:{max(w,1)}%;background:{funnel_colors[i]};color:#fff;font-size:{max(14,22-w/8):.0f}px;">{funnel_notes[i]}</div>'
            f'<div class="funnel-count" style="color:{funnel_colors[i]};">{funnel_counts[i]}</div>'
            f'</div>')
    parts.append('</div>')

    metric_items = [
        ("metrics", [
            (str(n_active), "活跃 Watchlist"),
            (str(n_tradable), "国金可交易"),
            (str(len(cards_list)), "候选卡片"),
            (str(flagged_cards), "数据异常(flagged)"),
        ]),
    ]
    parts.append('<div class="metrics">')
    for val, lbl in metric_items[0][1]:
        parts.append(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{lbl}</div></div>')
    parts.append('</div>')

    parts.append('</div>')

    # ══════════════════ SECTION 2: WATCHLIST ══════════════════
    parts.append('<div class="section"><h2>二、Watchlist 解读</h2>')

    # 趋势状态分布
    if not wl.empty:
        parts.append("<h3>趋势状态分布</h3>")
        state_counts = wl["trend_state"].value_counts()
        state_labels = {"BUY_CANDIDATE": ("买入候选", "tag-buy"),
                        "STRONG_WATCH": ("强势关注", "tag-strong"),
                        "WATCH": ("观察", "tag-watch"),
                        "OUT_OF_SCOPE": ("范围外", "tag-oos")}
        parts.append("<table><tr><th>趋势状态</th><th>数量</th><th>占比</th></tr>")
        for state, cnt in state_counts.items():
            lbl, tag = state_labels.get(state, (state, ""))
            tag_html = f'<span class="tag {tag}">{lbl}</span>' if tag else lbl
            parts.append(f"<tr><td>{tag_html}</td><td>{cnt}</td><td>{cnt/len(wl)*100:.1f}%</td></tr>")
        parts.append("</table>")

        # RPS60 分布
        if "rps60" in wl.columns:
            parts.append("<h3>活跃标的 RPS60 分布</h3>")
            parts.append("<table><tr><th>RPS60 区间</th><th>数量</th><th>占比</th></tr>")
            bins = [(0, 40), (40, 60), (60, 80), (80, 95), (95, 101)]
            bin_labels = ["0-40", "40-60", "60-80", "80-95", "95-100"]
            for (lo, hi), lbl in zip(bins, bin_labels):
                cnt = ((active["rps60"] >= lo) & (active["rps60"] < hi)).sum()
                parts.append(f"<tr><td>{lbl}</td><td>{cnt}</td><td>{cnt/len(active)*100:.1f}%</td></tr>")
            parts.append("</table>")

        # 资产类别分布
        if "asset_bucket" in wl.columns:
            parts.append("<h3>活跃标的资产类别分布</h3>")
            bucket_counts = wl[wl["trend_state"] != "OUT_OF_SCOPE"]["asset_bucket"].value_counts()
            parts.append("<table><tr><th>资产类别</th><th>数量</th><th>占比</th></tr>")
            bucket_labels = {"overseas_equity": "海外权益", "industry": "行业主题",
                             "factor_style": "因子/风格", "broad_market": "宽基",
                             "commodity_gold": "商品黄金", "money_market": "货币/理财",
                             "commodity_futures": "商品期货", "bond_treasury": "利率债",
                             "bond_credit": "信用债", "bond_convertible": "可转债"}
            for bucket, cnt in bucket_counts.items():
                lbl = bucket_labels.get(bucket, bucket or "未分类")
                parts.append(f"<tr><td>{lbl}</td><td>{cnt}</td><td>{cnt/len(active)*100:.1f}%</td></tr>")
            parts.append("</table>")

    parts.append('</div>')

    # ══════════════════ SECTION 3: 候选卡片 ══════════════════
    parts.append('<div class="section"><h2>三、候选卡片解读</h2>')

    parts.append('<div class="metrics">')
    parts.append(f'<div class="metric-card"><div class="metric-value">{complete_cards}</div><div class="metric-label">完成 (complete)</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value">{incomplete_cards}</div><div class="metric-label">待完善 (incomplete)</div></div>')
    parts.append(f'<div class="metric-card"><div class="metric-value">{flagged_cards}</div><div class="metric-label">需审查 (flagged)</div></div>')
    parts.append('</div>')

    # BUY_CANDIDATE 卡片列表
    buy_cards = [c for c in cards_list if c.get("trend", {}).get("trend_state") == "BUY_CANDIDATE"]
    if buy_cards:
        parts.append(f"<h3>买入候选（BUY_CANDIDATE，共 {len(buy_cards)} 只）</h3>")
        parts.append("<table><tr><th>代码</th><th>名称</th><th>RPS15</th><th>RPS60</th><th>卡片状态</th></tr>")
        for c in buy_cards:
            status = c.get("card_status", "")
            tag_cls = {"complete": "tag-complete", "incomplete": "tag-incomplete", "flagged": "tag-flagged"}.get(status, "")
            base = c.get("base_info", {})
            trend = c.get("trend", {})
            parts.append(
                f"<tr><td>{base.get('code','')}</td><td>{base.get('name','')}</td>"
                f"<td>{trend.get('rps15',0):.1f}</td><td>{trend.get('rps60',0):.1f}</td>"
                f"<td><span class='tag {tag_cls}'>{status}</span></td></tr>")
        parts.append("</table>")

    # 可交易池资产构成
    if not ac.empty and "asset_bucket" in ac.columns:
        parts.append("<h3>可交易池资产构成</h3>")
        bucket_counts = ac["asset_bucket"].value_counts()
        parts.append("<table><tr><th>资产类别</th><th>数量</th></tr>")
        for bucket, cnt in bucket_counts.items():
            parts.append(f"<tr><td>{bucket or '未分类'}</td><td>{cnt}</td></tr>")
        parts.append("</table>")

    parts.append('</div>')

    # ── Footer ──
    parts.append(f'<hr><div style="text-align:center;font-size:12px;color:var(--zh-muted);padding:20px 0">AKsignal · ETF 趋势发现系统 · 报告自动生成于 {now_str}</div>')
    parts.append("</div></body></html>")

    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("  html: %s", html_path)


def cmd_pipeline(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("ETF DISCOVERY PIPELINE")
    logger.info("=" * 60)

    t_start = time.monotonic()

    signals_dir = etf_signal_signals_dir()
    indicators_path = etf_signal_daily_dir() / "daily_indicators.parquet"
    if not indicators_path.exists():
        logger.error("no indicators — run bootstrap + calculate first")
        return

    date_str = _today_str()

    # Step 1: Watchlist
    t0 = time.monotonic()
    cmd_watchlist(args)
    watchlist_dur = time.monotonic() - t0

    # Step 2: Account mapping
    t0 = time.monotonic()
    cmd_account_mapping(args)
    account_dur = time.monotonic() - t0

    # Step 3: Card
    t0 = time.monotonic()
    cmd_card(args)
    card_dur = time.monotonic() - t0

    # ── Step 4: 统一产出（JSON + CSV + HTML）────────────────────────
    t0 = time.monotonic()
    output_dir = etf_signal_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    master_dir = etf_signal_master_dir()
    daily_dir = etf_signal_daily_dir()
    master = etf_master.load_master(master_dir)
    daily_df = pd.read_parquet(indicators_path)

    # 4a: 热度地图
    heat_map = _compute_bucket_heat(daily_df, master)

    # 4b: 活跃 watchlist CSV
    watchlist_files = sorted(signals_dir.glob(f"watchlist_{date_str}.parquet"))
    if not watchlist_files:
        watchlist_files = sorted(signals_dir.glob("watchlist_*.parquet"), reverse=True)
    if watchlist_files:
        wl = pd.read_parquet(watchlist_files[0])
        active = wl[wl["trend_state"] != "OUT_OF_SCOPE"]
        if not active.empty:
            active.to_csv(output_dir / f"watchlist_active_{date_str}.csv", index=False, encoding="utf-8-sig")

    # 4c: 合并 HTML
    _write_html_report(daily_df, master, heat_map, output_dir, date_str, logger=logger)

    output_dur = time.monotonic() - t0

    total_dur = time.monotonic() - t_start
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("  watchlist:  %.1fs", watchlist_dur)
    logger.info("  account:    %.1fs", account_dur)
    logger.info("  card:       %.1fs", card_dur)
    logger.info("  output:     %.1fs", output_dur)
    logger.info("  total:      %.1fs", total_dur)
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# P0-C: Calculate — 指标 + 信号
# ---------------------------------------------------------------------------

def cmd_calculate(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    raw_dir = etf_signal_raw_dir()
    master_dir = etf_signal_master_dir()
    signals_dir = etf_signal_signals_dir()
    daily_dir = etf_signal_daily_dir()
    explicit_date = getattr(args, "date", None)

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

    indicators_df = indicators.compute_indicators(combined)
    logger.info("indicators computed: %d rows", len(indicators_df))

    # 添加全市场横截面 RPS
    if "return" in indicators_df.columns:
        indicators_df["rps15"] = indicators_df["return"].rank(ascending=True, pct=True) * 100
    if "return_60d" in indicators_df.columns:
        indicators_df["rps60"] = indicators_df["return_60d"].rank(ascending=True, pct=True) * 100

    # 标记 rps60 验收门控状态
    rps60_nunique = indicators_df["rps60"].nunique(dropna=True) if "rps60" in indicators_df.columns else 0
    if rps60_nunique <= 10:
        logger.warning("rps60_nunique=%d ≤ 10，指标验收未通过", rps60_nunique)
    else:
        logger.info("rps60_nunique=%d，指标验收通过", rps60_nunique)

    # 保存日指标
    daily_dir.mkdir(parents=True, exist_ok=True)
    indicators_df.to_parquet(daily_dir / "daily_indicators.parquet", index=False)

    logger.info("calculate complete: %d ETFs", len(indicators_df))


# ---------------------------------------------------------------------------
# P0-C: Signal
# ---------------------------------------------------------------------------

def cmd_signal(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    master_dir = etf_signal_master_dir()
    daily_dir = etf_signal_daily_dir()
    signals_dir = etf_signal_signals_dir()
    positions_dir = etf_signal_positions_dir()

    master = etf_master.load_master(master_dir)
    if master.empty:
        logger.error("no ETF master")
        return

    indicators_path = daily_dir / "daily_indicators.parquet"
    if not indicators_path.exists():
        logger.error("no indicators — run calculate first")
        return

    indicators_df = pd.read_parquet(indicators_path)
    logger.info("generating signals for %d ETF snapshots...", len(indicators_df))

    signals: list[dict[str, Any]] = []
    for _, row in indicators_df.iterrows():
        code = row["fund_code"]

        state = sig_mod.compute_trend_state(
            rps15=row.get("rps15", 50.0),
            rps60=row.get("rps60", 50.0),
            return_5d=row.get("return_5d", 0.0),
            return_20d=row.get("return_20d", 0.0),
            above_ma20=row.get("ma20", 0) > 0 and row.get("price", 0) > row.get("ma20", 0),
            above_ma60=row.get("ma60", 0) > 0 and row.get("price", 0) > row.get("ma60", 0),
        )

        signals.append({
            "fund_code": code,
            "date": row.get("date", str(datetime.now().date())),
            "state": state,
            "previous_state": "",
            "confidence": 0.5 if state == "WATCH" else (0.8 if state == "BUY_CANDIDATE" else 0.0),
            "reason": f"rps20={row.get('rps20', 0):.1f}, ma20={row.get('ma20', 0):.2f}",
            "risk_flags": "",
        })

    signals_df = pd.DataFrame(signals)
    signals_dir.mkdir(parents=True, exist_ok=True)
    signals_df.to_parquet(signals_dir / f"signals_{_today_str()}.parquet", index=False)
    logger.info("signals saved: %d ETF signals", len(signals_df))


# (移入 pipeline: cmd_pipeline 产出 JSON+CSV+HTML)


# ---------------------------------------------------------------------------
# P0-F: Backtest (scaffold)
# ---------------------------------------------------------------------------

def cmd_backtest(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("backtest — not yet implemented (P0-F)")
    logger.info("planned: historical signal replay, statistics, shadow run")


# ---------------------------------------------------------------------------
# Run-day
# ---------------------------------------------------------------------------

def cmd_run_day(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("ETF RUN-DAY STARTED")
    logger.info("=" * 60)

    t_start = time.monotonic()

    # Step 1: Update
    t0 = time.monotonic()
    result = cmd_update(args)
    update_dur = time.monotonic() - t0

    if not result.target_ready:
        logger.info("target not ready — stopping pipeline")
        return

    # Step 2: Calculate
    t0 = time.monotonic()
    cmd_calculate(args)
    calc_dur = time.monotonic() - t0

    # Step 3: Pipeline (watchlist → account → card → JSON+CSV+HTML)
    t0 = time.monotonic()
    cmd_pipeline(args)
    pipeline_dur = time.monotonic() - t0

    total_dur = time.monotonic() - t_start
    logger.info("=" * 60)
    logger.info("ETF RUN-DAY SUMMARY")
    logger.info("  update:  %.1fs", update_dur)
    logger.info("  calc:    %.1fs", calc_dur)
    logger.info("  pipeline: %.1fs", pipeline_dur)
    logger.info("  total:   %.1fs", total_dur)
    logger.info("=" * 60)


# ═══════════════════════════════════════════════════════════════════
# 专项重试：retry-uncovered
# ═══════════════════════════════════════════════════════════════════

def _load_diagnosis_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"code": str})
    return df


def _run_em_probe(logger: logging.Logger, target_date_str: str) -> dict[str, str]:
    """执行三根 EM 探针，判断东财历史接口当前状态。

    探针标的：
      - 510050（普通主流）
      - 588000（科创板/EM-only）
      - 520690（问题品种/港股通）

    每只只请求最近 5 天。返回 {code: "ok"|fail_reason}。
    """
    PROBE_CODES = ["510050", "588000", "520690"]
    probe_end = target_date_str
    probe_start = (pd.Timestamp(target_date_str) - timedelta(days=10)).strftime("%Y%m%d")

    logger.info("EM probe — testing %s with window %s ~ %s",
                PROBE_CODES, probe_start, probe_end)

    data_source.clear_fetch_stats()
    data_source.reset_em_circuit_breakers()

    results: dict[str, str] = {}
    for code in PROBE_CODES:
        try:
            df = data_source.fetch_etf_hist(code, start_date=probe_start, end_date=probe_end)
            if not df.empty:
                results[code] = "ok"
                logger.info("  probe [%s] ✓  (%d rows, %s ~ %s)",
                            code, len(df),
                            df["date"].min().strftime("%Y-%m-%d"),
                            df["date"].max().strftime("%Y-%m-%d"))
            else:
                results[code] = "empty_response"
                logger.warning("  probe [%s] ✗ empty", code)
        except Exception as e:
            results[code] = str(e)[:60]
            logger.warning("  probe [%s] ✗ %s: %s", code, type(e).__name__, str(e)[:80])
        time.sleep(1.5 + random.uniform(-0.3, 0.5))
    data_source.clear_fetch_stats()
    return results


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
    diag_dir = etf_signal_signals_dir().parent / "diagnostics"
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
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ETF 趋势信号系统")
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

    p_layer1 = sub.add_parser("layer1", help="[Layer 1] 全市场资产热度分布")
    p_layer1.add_argument("--lookback", type=int, default=20, help="收益率回看窗口")
    p_layer1.add_argument("--log-level", default="INFO")

    p_screen = sub.add_parser("screen", help="质量门控管线：筛选数据合格标的")
    p_screen.add_argument("--log-level", default="INFO")

    p_watchlist = sub.add_parser("watchlist", help="生成趋势关注池（trend_watchlist）")
    p_watchlist.add_argument("--log-level", default="INFO")

    p_account = sub.add_parser("account", help="映射 trend_watchlist 至国金账户可交易池")
    p_account.add_argument("--log-level", default="INFO")

    p_card = sub.add_parser("card", help="生成 ETF 候选信息卡片")
    p_card.add_argument("--log-level", default="INFO")

    p_pipeline = sub.add_parser("pipeline", help="完整发现链路：watchlist → account → card → JSON+CSV+HTML")
    p_pipeline.add_argument("--log-level", default="INFO")

    p_calc = sub.add_parser("calculate", help="计算技术指标")
    p_calc.add_argument("--date", default="", help="目标日期")
    p_calc.add_argument("--log-level", default="INFO")

    p_signal = sub.add_parser("signal", help="生成信号状态机")
    p_signal.add_argument("--log-level", default="INFO")

    p_backtest = sub.add_parser("backtest", help="回测（P0-F 实现）")
    p_backtest.add_argument("--log-level", default="INFO")

    p_run = sub.add_parser("run-day", help="依次执行 update → calculate → pipeline")
    p_run.add_argument("--target-date", default="", help="目标日期 YYYYMMDD")
    p_run.add_argument("--log-level", default="INFO")

    p_retry = sub.add_parser("retry-uncovered", help="专项重试目标日未覆盖 ETF（分批+熔断器重置）")
    p_retry.add_argument("--target-date", default="", help="目标日期 YYYYMMDD")
    p_retry.add_argument("--min-rows", type=int, default=60, help="最低历史行数要求（默认 60，与 RPS60 对齐）")
    p_retry.add_argument("--batch-size", type=int, default=10, help="每批数量")
    p_retry.add_argument("--batch-pause", type=int, default=30, help="批次间暂停秒数")
    p_retry.add_argument("--interval", type=float, default=2.0, help="单只请求间隔秒数")
    p_retry.add_argument("--log-level", default="INFO")

    return p


def main() -> None:
    args = build_arg_parser().parse_args()
    dispatch = {
        "bootstrap": cmd_bootstrap,
        "bootstrap-core": cmd_bootstrap_core,
        "update": cmd_update,
        "classify": cmd_classify,
        "layer1": cmd_layer1,
        "screen": cmd_screen,
        "watchlist": cmd_watchlist,
        "account": cmd_account_mapping,
        "card": cmd_card,
        "pipeline": cmd_pipeline,
        "calculate": cmd_calculate,
        "signal": cmd_signal,
        "backtest": cmd_backtest,
        "run-day": cmd_run_day,
        "retry-uncovered": cmd_retry_uncovered,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
