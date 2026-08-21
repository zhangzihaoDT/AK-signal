"""
ETF 目标日缺口回填：分类 + checkpoint/resume + 耗时统计。

职责：
  - 发现缺失目标日 K 线的代码（缺口）
  - 逐只回填，失败时按原因分类，而不是笼统记成 fetch failure
  - checkpoint 落盘，中断后可 resume（只处理剩余部分）
  - 结束时输出 requested / fetched / skipped / failed / elapsed 与 per-source 统计

缺口分类（③）：
  - NOT_IN_MASTER   不在 master，孤本/已摘牌，不进旋转宇宙（结构性）
  - TERMINATED      净值停在很久以前，基金已终止/净值停发（结构性）
  - SOURCE_STALE    数据源最新 bar < target，源没跟上（可稍后自动重试）
  - RATE_LIMITED    fetch 被限流/连接中断（可稍后自动重试）
  - TRUE_MISSING    所有源均无 target 数据，新上市/停牌/真缺失（结构性）
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.common.paths import data_dir

from . import data_source
from .cli import _has_request_bar, _load_etf_raw, _merge_incremental, _save_etf_raw

logger = logging.getLogger("etf_signal.backfill")

CATEGORY_NOT_IN_MASTER = "NOT_IN_MASTER"
CATEGORY_TERMINATED = "TERMINATED"
CATEGORY_SOURCE_STALE = "SOURCE_STALE"
CATEGORY_RATE_LIMITED = "RATE_LIMITED"
CATEGORY_TRUE_MISSING = "TRUE_MISSING"

CATEGORY_LABELS = {
    CATEGORY_NOT_IN_MASTER: "不在 master",
    CATEGORY_TERMINATED: "基金已终止",
    CATEGORY_SOURCE_STALE: "数据源未跟上",
    CATEGORY_RATE_LIMITED: "源限流/连接中断",
    CATEGORY_TRUE_MISSING: "真缺失",
}

# 可自动重试的类别：源追上 / 限流解除后重新执行即可恢复
RETRYABLE_CATEGORIES = {CATEGORY_SOURCE_STALE, CATEGORY_RATE_LIMITED}

# 判定「基金已终止」的落后天数（日历日）
TERMINATED_LOOKBACK_DAYS = 30


@dataclass
class GapClassification:
    code: str
    category: str
    detail: str = ""
    latest_bar_date: date | None = None
    nav_latest_date: date | None = None

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE_CATEGORIES


def discover_gaps(raw_dir: Path, codes, target: date) -> list[str]:
    """返回缺少 target 日 K 线的代码（升序）。"""
    missing: list[str] = []
    for code in codes:
        df = _load_etf_raw(raw_dir, str(code))
        if not _has_request_bar(df, target):
            missing.append(str(code))
    return sorted(missing)


def _fetch_was_rate_limited(fetch_errors: list[Exception], fetch_info: dict[str, Any] | None) -> bool:
    """判断本次 fetch 是否因限流/连接中断而失败。

    fetch_etf_hist 内部捕获网络异常返回空 DataFrame 而非抛出，
    因此需要同时检查抛出的异常与 _fetch_stats 里记录的 primary_error_type。
    """
    if fetch_errors and any(data_source.is_rate_limit_error(e) for e in fetch_errors):
        return True
    if not fetch_info:
        return False
    err = str(fetch_info.get("primary_error_type", ""))
    if err and ("rate_limit" in err.lower() or err == "TransientNetworkError"):
        return True
    reason = str(fetch_info.get("fallback_reason", ""))
    if "rate_limit" in reason.lower():
        return True
    if reason and data_source.is_rate_limit_error(RuntimeError(reason)):
        return True
    return False


def classify_gap(
    code: str,
    target: date,
    in_master: bool,
    raw_latest: date | None,
    fetch_errors: list[Exception],
    nav_latest: date | None,
    fetch_info: dict[str, Any] | None = None,
) -> GapClassification:
    """把单个缺口归类到结构化原因，而非笼统的 fetch failure。"""
    if not in_master:
        return GapClassification(
            code, CATEGORY_NOT_IN_MASTER,
            "不在 master（孤本/已摘牌），不进旋转宇宙",
            latest_bar_date=raw_latest, nav_latest_date=nav_latest,
        )

    anchor = nav_latest if nav_latest is not None else raw_latest
    if anchor is not None:
        days_behind = (target - anchor).days
        if days_behind >= TERMINATED_LOOKBACK_DAYS:
            return GapClassification(
                code, CATEGORY_TERMINATED,
                f"净值/行情停在 {anchor}（落后 {days_behind} 天），基金已终止或净值停发",
                latest_bar_date=raw_latest, nav_latest_date=nav_latest,
            )

    # 连接失败时我们并不知道源是否真的过期——先如实标记「没连上源」，
    # 稍后源恢复后重试即可；若源恢复仍无 target bar，才会落到 SOURCE_STALE。
    if _fetch_was_rate_limited(fetch_errors, fetch_info):
        attempts_n = max(len(fetch_errors), 1)
        return GapClassification(
            code, CATEGORY_RATE_LIMITED,
            f"fetch 被限流/连接中断（{attempts_n} 次尝试，网络异常被 fetch 层捕获返回空），源恢复后可重试",
            latest_bar_date=raw_latest, nav_latest_date=nav_latest,
        )

    if raw_latest is not None and raw_latest < target:
        days = (target - raw_latest).days
        return GapClassification(
            code, CATEGORY_SOURCE_STALE,
            f"日K源停在 {raw_latest}（落后 {days} 天），源未跟上 target",
            latest_bar_date=raw_latest, nav_latest_date=nav_latest,
        )

    return GapClassification(
        code, CATEGORY_TRUE_MISSING,
        "所有源均无 target 数据（新上市/停牌/真缺失）",
        latest_bar_date=raw_latest, nav_latest_date=nav_latest,
    )


class BackfillCheckpoint:
    """回填状态落盘，支持中断后 resume。

    结构：
      {
        "target_date": "20260820",
        "updated_at": "...",
        "codes": {
          "588220": {"state": "ok", "source": "sina", "rows": 1, "elapsed_ms": 320},
          "560650": {"state": "failed", "category": "TERMINATED", "detail": "...", "elapsed_ms": 120},
        },
        "stats": {"requested": 207, "ok": 202, "failed": 5, "elapsed_s": 275},
      }
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("checkpoint load failed (%s) — starting fresh", e)
        return {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_done(self, code: str) -> bool:
        return self.data.get("codes", {}).get(code, {}).get("state") == "ok"

    def done_codes(self) -> set[str]:
        return {c for c, v in self.data.get("codes", {}).items() if v.get("state") == "ok"}

    def failed_codes(self) -> set[str]:
        return {c for c, v in self.data.get("codes", {}).items() if v.get("state") == "failed"}

    def mark_ok(self, code: str, source: str, rows: int, elapsed_ms: int) -> None:
        self.data.setdefault("codes", {})[code] = {
            "state": "ok", "source": source, "rows": rows, "elapsed_ms": elapsed_ms,
        }

    def mark_failed(self, code: str, category: str, detail: str, elapsed_ms: int) -> None:
        self.data.setdefault("codes", {})[code] = {
            "state": "failed", "category": category, "detail": detail, "elapsed_ms": elapsed_ms,
        }


@dataclass
class BackfillReport:
    requested: int = 0
    fetched: int = 0
    failed: int = 0
    skipped: int = 0
    elapsed_s: float = 0.0
    by_source: dict[str, int] = field(default_factory=dict)
    by_category: dict[str, int] = field(default_factory=dict)
    fail_details: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return self.fetched + self.failed


def format_elapsed(seconds: float) -> str:
    s = int(seconds)
    m, s = divmod(s, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def run_backfill(
    raw_dir: Path,
    master: pd.DataFrame,
    target: str,
    resume: bool = True,
    attempts: int = 3,
    retryable_only: bool = False,
    pause_min: float = 0.4,
    pause_max: float = 1.2,
    log: Callable = print,
) -> BackfillReport:
    """执行目标日缺口回填。

    - resume=False 时忽略既有 checkpoint，重新处理全部缺口。
    - retryable_only=True 时跳过 checkpoint 中已标记为结构性失败的代码。
    """
    target_date = datetime.strptime(target, "%Y%m%d").date()
    codes = master["fund_code"].astype(str).tolist()
    master_set = set(codes)

    gaps = discover_gaps(raw_dir, codes, target_date)
    if not gaps:
        log(f"backfill: 无缺口，target {target} 已全部覆盖（{len(codes)} 只）")
        return BackfillReport(requested=0)

    cp = BackfillCheckpoint(backfill_dir() / f"backfill_{target}.json")

    if resume:
        already_ok = cp.done_codes()
        remaining = [c for c in gaps if c not in already_ok]
        skipped = len(gaps) - len(remaining)
        if retryable_only:
            structural = {
                c for c in remaining if c in cp.failed_codes()
                and cp.data["codes"][c].get("category", "") not in RETRYABLE_CATEGORIES
            }
            remaining = [c for c in remaining if c not in structural]
            skipped += len(structural)
        if skipped:
            log(f"backfill: resume — {skipped}/{len(gaps)} 已处理，剩余 {len(remaining)}")
    else:
        remaining = gaps
        skipped = 0

    if not remaining:
        log(f"backfill: 全部缺口已处理（checkpoint 命中 {len(gaps)} 只）")
        return BackfillReport(requested=len(gaps), skipped=skipped)

    report = BackfillReport(requested=len(gaps), skipped=skipped)
    t0 = time.perf_counter()

    for i, code in enumerate(remaining, 1):
        data_source.reset_em_circuit_breakers()
        df = _load_etf_raw(raw_dir, code)
        prior = [d for d in pd.to_datetime(df["date"]).dt.date.values if d <= target_date] if not df.empty and "date" in df.columns else []
        last_prior = max(prior) if prior else None
        inc_start = (last_prior + timedelta(days=1)).strftime("%Y%m%d") if last_prior else "20200101"

        fetch_errors: list[Exception] = []
        got = False
        source = "none"
        rows_fetched = 0
        t_code = time.perf_counter()

        for _ in range(max(attempts, 1)):
            try:
                df_new = data_source.fetch_etf_hist(code, start_date=inc_start, end_date=target)
                if not df_new.empty and (pd.to_datetime(df_new["date"]).dt.date == target_date).any():
                    combined = _merge_incremental(df, df_new)
                    _save_etf_raw(combined, raw_dir, code)
                    got = True
                    source = data_source.get_fetch_stats().get(code, {}).get("source", "sina")
                    rows_fetched = int((pd.to_datetime(df_new["date"]).dt.date == target_date).sum())
                    break
                if not df_new.empty:
                    fetch_errors.append(RuntimeError("fetch returned data but missing target bar"))
            except Exception as e:  # noqa: BLE001
                fetch_errors.append(e)
                time.sleep(random.uniform(0.5, 1.5))

        elapsed_ms = round((time.perf_counter() - t_code) * 1000)

        if got:
            report.fetched += 1
            cp.mark_ok(code, source, rows_fetched, elapsed_ms)
            log(f"[{i}/{len(remaining)}] OK   {code}  source={source} rows={rows_fetched} {elapsed_ms}ms")
        else:
            report.failed += 1
            in_master = code in master_set
            raw_latest = max(pd.to_datetime(df["date"]).dt.date) if not df.empty and "date" in df.columns else None
            nav_latest, _ = data_source.fetch_nav_latest(code)
            fetch_info = data_source.get_fetch_stats().get(code, {})
            cls = classify_gap(code, target_date, in_master, raw_latest, fetch_errors, nav_latest, fetch_info)
            cp.mark_failed(code, cls.category, cls.detail, elapsed_ms)
            report.by_category[cls.category] = report.by_category.get(cls.category, 0) + 1
            report.fail_details.append((code, cls.category, cls.detail))
            log(f"[{i}/{len(remaining)}] FAIL {code}  {CATEGORY_LABELS[cls.category]} | {cls.detail} | {elapsed_ms}ms")

        if i % 20 == 0:
            cp.save()
        time.sleep(random.uniform(pause_min, pause_max))

    cp.save()
    report.elapsed_s = time.perf_counter() - t0
    report.by_source = _source_breakdown(remaining)

    _print_summary(report, log)
    return report


def _source_breakdown(codes: list[str]) -> dict[str, int]:
    stats = data_source.get_fetch_stats()
    by_source: dict[str, int] = {}
    for c in codes:
        src = stats.get(c, {}).get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
    return by_source


def _print_summary(report: BackfillReport, log: Callable) -> None:
    log(
        f"backfill summary: requested {report.requested} / fetched {report.fetched} "
        f"/ skipped {report.skipped} / failed {report.failed} / elapsed {format_elapsed(report.elapsed_s)}"
    )
    if report.by_source:
        log(f"  source breakdown: {report.by_source}")
    if report.by_category:
        log(f"  category breakdown: {report.by_category}")
        for code, category, detail in report.fail_details:
            log(f"    {code}  [{CATEGORY_LABELS[category]}] {detail}")


def backfill_dir() -> Path:
    return data_dir() / "etf_signal" / "backfill"
