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
from . import heat, indicators, signal as sig_mod, report as etf_report
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
    logger.info("updating %d ETFs for target %s...", len(codes), explicit_target)

    data_source.clear_fetch_stats()
    data_source.reset_em_circuit_breakers()
    request_date = pd.Timestamp(explicit_target).date()
    success = 0
    backfill_count = 0
    skipped_backfill = 0
    for code in codes:
        df = _load_etf_raw(raw_dir, code)
        last_date = df["date"].max().date() if not df.empty and "date" in df.columns else None
        if last_date is not None and last_date >= request_date:
            success += 1
            continue

        is_backfill = df.empty  # 无本地数据 → 历史补缺

        if is_backfill and backfill_count >= data_source.EM_BACKFILL_LIMIT:
            skipped_backfill += 1
            continue

        inc_start = (last_date + timedelta(days=1)).strftime("%Y%m%d") if last_date else "20200101"
        try:
            df_new = data_source.fetch_etf_hist(code, start_date=inc_start, end_date=explicit_target)
            if not df_new.empty:
                combined = _merge_incremental(df, df_new)
                _save_etf_raw(combined, raw_dir, code)
                success += 1
        except Exception as e:
            logger.warning("update failed for %s: %s", code, e)

        if is_backfill:
            backfill_count += 1
            delay = data_source.EM_REQUEST_INTERVAL + random.uniform(-1, 1)
        else:
            delay = random.uniform(0.3, 0.8)
        time.sleep(max(delay, 0.3))

    target_ready = success == len(codes)
    logger.info("update: %d/%d covered, ready=%s", success, len(codes), target_ready)
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
    cfg = classifier.load_bucket_config(config_dir())
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

def cmd_pipeline(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    logger.info("=" * 60)
    logger.info("ETF DISCOVERY PIPELINE")
    logger.info("=" * 60)

    t_start = time.monotonic()

    # Step 1-2: 假设 indicators 已存在
    signals_dir = etf_signal_signals_dir()
    indicators_path = etf_signal_daily_dir() / "daily_indicators.parquet"
    if not indicators_path.exists():
        logger.error("no indicators — run bootstrap + calculate first")
        return

    # Step 3: Watchlist
    t0 = time.monotonic()
    cmd_watchlist(args)
    watchlist_dur = time.monotonic() - t0

    # Step 4: Account mapping
    t0 = time.monotonic()
    cmd_account_mapping(args)
    account_dur = time.monotonic() - t0

    # Step 5: Card
    t0 = time.monotonic()
    cmd_card(args)
    card_dur = time.monotonic() - t0

    total_dur = time.monotonic() - t_start
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("  watchlist:  %.1fs", watchlist_dur)
    logger.info("  account:    %.1fs", account_dur)
    logger.info("  card:       %.1fs", card_dur)
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

        state = sig_mod.determine_initial_state(
            rps20=row.get("rps20", 50.0) if "rps20" in indicators_df.columns else 50.0,
            rps60=row.get("rps60", 50.0) if "rps60" in indicators_df.columns else 50.0,
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


# ---------------------------------------------------------------------------
# P0-E: Report
# ---------------------------------------------------------------------------

def cmd_report(args: argparse.Namespace) -> None:
    logger = build_logger(args.log_level)
    master_dir = etf_signal_master_dir()
    signals_dir = etf_signal_signals_dir()
    reports_dir = project_root() / "reports" / "etf_daily"

    master = etf_master.load_master(master_dir)
    date_str = getattr(args, "target_date", "") or _default_target_date()

    signal_files = sorted(signals_dir.glob(f"signals_{date_str}.parquet"))
    if not signal_files:
        logger.warning("no signals for %s, checking latest...", date_str)
        signal_files = sorted(signals_dir.glob("signals_*.parquet"), reverse=True)[:1]

    if not signal_files:
        logger.error("no signals found — run signal first")
        return

    signals_df = pd.read_parquet(signal_files[0])
    logger.info("generating reports for %s...", date_str)

    heat_map = pd.DataFrame()
    candidates = pd.DataFrame()
    order_plan = pd.DataFrame()

    try:
        paths = etf_report.write_daily_reports(heat_map, candidates, order_plan, reports_dir, date_str)
        logger.info("reports written: %s", date_str)
        for kind, p in paths.items():
            logger.info("  %s: %s", kind, p)
    except Exception as e:
        logger.error("report generation failed: %s", e)


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

    # Step 3: Signal
    t0 = time.monotonic()
    cmd_signal(args)
    signal_dur = time.monotonic() - t0

    # Step 4: Report
    t0 = time.monotonic()
    cmd_report(args)
    report_dur = time.monotonic() - t0

    total_dur = time.monotonic() - t_start
    logger.info("=" * 60)
    logger.info("ETF RUN-DAY SUMMARY")
    logger.info("  update:  %.1fs", update_dur)
    logger.info("  calc:    %.1fs", calc_dur)
    logger.info("  signal:  %.1fs", signal_dur)
    logger.info("  report:  %.1fs", report_dur)
    logger.info("  total:   %.1fs", total_dur)
    logger.info("=" * 60)


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

    p_pipeline = sub.add_parser("pipeline", help="完整发现链路：watchlist → account → card")
    p_pipeline.add_argument("--log-level", default="INFO")

    p_calc = sub.add_parser("calculate", help="计算技术指标")
    p_calc.add_argument("--date", default="", help="目标日期")
    p_calc.add_argument("--log-level", default="INFO")

    p_signal = sub.add_parser("signal", help="生成信号状态机")
    p_signal.add_argument("--log-level", default="INFO")

    p_report = sub.add_parser("report", help="生成日报")
    p_report.add_argument("--target-date", default="", help="报告日期 YYYYMMDD")
    p_report.add_argument("--log-level", default="INFO")

    p_backtest = sub.add_parser("backtest", help="回测（P0-F 实现）")
    p_backtest.add_argument("--log-level", default="INFO")

    p_run = sub.add_parser("run-day", help="依次执行 update → calculate → signal → report")
    p_run.add_argument("--target-date", default="", help="目标日期 YYYYMMDD")
    p_run.add_argument("--log-level", default="INFO")

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
        "report": cmd_report,
        "backtest": cmd_backtest,
        "run-day": cmd_run_day,
    }
    fn = dispatch.get(args.command)
    if fn:
        fn(args)


if __name__ == "__main__":
    main()
