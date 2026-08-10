"""
Range Replay — 区间历史信号重放（v0.5.0 P0-7）。

对 [start_date, end_date] 内每个交易日重放 Layer①/②/③ 信号，产出
historical_signals_{start}_{end}.parquet（长表）+ replay_range_*_manifest.json。

实现：复用单日期重放（同一套已通过 parity 的规则路径），通过 cache 预加载
ETF 行情 / 行业 metrics，避免逐日重复读盘。

P0-7 验收：
  - 只遍历交易日（replay_calendar）
  - 每日复用 replay_single_date()（一致性由构造保证）
  - 单日失败不终止区间（per-date try/except → status=failed，继续）
  - --resume 支持：相同 rule_version + config_hash 的已完成日期跳过
  - 汇总无重复主键（(trade_date, layer, entity_type, entity_code) 去重）
  - 失败/降级/跳过日期全部进入 manifest
  - 不覆盖正式 daily pipeline 产物（只写 outputs/research；processed CSV 不写）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import outputs_dir
from src.research.replay import engine as replay_engine
from src.research.signals import schema as sch

# 汇总主键（用于去重）：v0.9.1 起同一行业/资产可跨主题注册（如 801084.SI 属
# china_auto_global 与 ai_infrastructure），theme 是实体的区分维度，须并入主键。
PRIMARY_KEY = ["trade_date", "layer", "entity_type", "entity_code", "theme"]


def build_logger(level: str = "INFO") -> logging.Logger:
    return replay_engine.build_logger(level)


def _research_dir() -> Path:
    return outputs_dir() / "research"


def _stamp(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df["signal_origin"] = "replayed"
    df["rule_version"] = sch.RULE_VERSION
    df["config_hash"] = sch.config_hash()
    for c in ("rps15", "trend_score"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[sch.SIGNAL_COLUMNS].copy()


def _daily_path(out_dir: Path, date_str: str) -> Path:
    return out_dir / "daily" / f"historical_signals_{date_str}.parquet"


def _load_daily_if_matching(
    path: Path,
    rule_version: str,
    config_hash: str,
) -> pd.DataFrame | None:
    """读取单日产物；rule_version / config_hash 不一致时返回 None（需重放）。"""
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df.empty:
        return None
    if "rule_version" not in df.columns or str(df["rule_version"].iloc[0]) != rule_version:
        return None
    if "config_hash" not in df.columns or str(df["config_hash"].iloc[0]) != config_hash:
        return None
    return df


def _expected_layers(layers: str) -> set[str]:
    return {l for l in str(layers) if l in "123"}


def _coverage_table(
    cache: dict[str, Any],
    replayed: pd.DataFrame,
) -> list[dict[str, Any]]:
    """每日横截面覆盖（计划 §9）：eligible = 数据宇宙出现的 ETF，priced = 重放 Layer1 行数。"""
    combined = cache.get("combined", pd.DataFrame())
    eligible = int(combined["fund_code"].nunique()) if not combined.empty else 0
    l1 = replayed[replayed["layer"] == "1"] if not replayed.empty else pd.DataFrame()
    out: list[dict[str, Any]] = []
    if not l1.empty:
        for date, g in l1.groupby("trade_date"):
            priced = int(g["entity_code"].nunique())
            out.append({
                "trade_date": date,
                "eligible_etf_count": eligible,
                "priced_etf_count": priced,
                "coverage_rate": round(priced / eligible, 4) if eligible else 0.0,
            })
    return out


def replay_range(
    start_date: str,
    end_date: str,
    *,
    layers: str = "123",
    out_dir: Path | None = None,
    log_level: str = "INFO",
    cache: dict[str, Any] | None = None,
    resume: bool = True,
) -> pd.DataFrame:
    """对 [start_date, end_date] 区间逐交易日重放三层信号。

    Args:
        start_date / end_date: YYYYMMDD（含边界）
        layers: 参与重放的层位集合，如 "123" / "12"（跳过 Layer③ 慢路径）
        out_dir: 产物目录（默认 outputs/research）；None 时不落盘
        cache: 预加载输入（engine.build_replay_cache()）
        resume: 跳过已按相同 rule_version+config_hash 完成的日期
    """
    logger = build_logger(log_level)
    logger.info("=" * 60)
    logger.info("REPLAY range: %s -> %s (layers=%s, resume=%s)", start_date, end_date, layers, resume)
    logger.info("=" * 60)

    cache = cache or replay_engine.build_replay_cache()
    calendar = replay_engine.replay_calendar(cache, start_date, end_date)
    if not calendar:
        logger.error("no trading days in [%s, %s]", start_date, end_date)
        return sch.empty_frame()
    logger.info("calendar: %d trading days", len(calendar))

    want_l3 = "3" in layers
    expected = _expected_layers(layers)
    rule_version = sch.RULE_VERSION
    config_hash = sch.config_hash()

    daily_dir = out_dir / "daily" if out_dir is not None else None
    if daily_dir is not None:
        daily_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    statuses: dict[str, str] = {}
    t_start = pd.Timestamp.now()

    for i, d in enumerate(calendar, 1):
        # ── resume：已完成且版本一致 → 跳过 ─────────────────────────
        if resume and daily_dir is not None:
            cached = _load_daily_if_matching(_daily_path(daily_dir, d), rule_version, config_hash)
            if cached is not None:
                frames.append(cached)
                statuses[d] = "skipped"
                logger.info("  [%d/%d] %s skipped (resume)", i, len(calendar), d)
                continue

        try:
            df_d = replay_engine.replay_single_date(
                d, out_dir=None, log_level=log_level, cache=cache)
            if not want_l3 and not df_d.empty:
                df_d = df_d[df_d["layer"] != "3"].copy()

            produced = set(df_d["layer"]) if not df_d.empty else set()
            missing = expected - produced
            if missing:
                statuses[d] = "degraded"
                logger.warning("  [%d/%d] %s degraded (missing layers: %s)",
                               i, len(calendar), d, sorted(missing))
            else:
                statuses[d] = "completed"

            if not df_d.empty:
                frames.append(df_d)
                if daily_dir is not None:
                    _daily_path(daily_dir, d).parent.mkdir(parents=True, exist_ok=True)
                    df_d.to_parquet(_daily_path(daily_dir, d), index=False)
        except Exception as e:  # 单日失败不终止区间
            statuses[d] = "failed"
            logger.error("  [%d/%d] %s FAILED: %s", i, len(calendar), d, e)

        if i == 1 or i % 50 == 0 or i == len(calendar):
            el = (pd.Timestamp.now() - t_start).total_seconds()
            logger.info("  progress %d/%d (%.0fs)", i, len(calendar), el)

    # ── 汇总：合并 + 去重主键 ──────────────────────────────────────
    df = pd.concat(frames, ignore_index=True) if frames else sch.empty_frame()
    df = _stamp(df)
    n_before = int(len(df))
    if not df.empty:
        df = df.drop_duplicates(subset=PRIMARY_KEY, keep="last").reset_index(drop=True)
    n_after = int(len(df))
    if n_before != n_after:
        logger.warning("aggregate dedup: %d -> %d rows", n_before, n_after)

    n_status = {s: sum(1 for v in statuses.values() if v == s) for s in
                ("completed", "degraded", "failed", "skipped")}
    manifest = {
        "start_date": start_date,
        "end_date": end_date,
        "layers": layers,
        "n_dates": len(calendar),
        "calendar": calendar,
        "date_status": statuses,
        "status_counts": n_status,
        "n_rows": n_after,
        "rows_before_dedup": n_before,
        "rows_per_layer": df["layer"].value_counts().to_dict() if not df.empty else {},
        "rule_version": rule_version,
        "config_hash": config_hash,
        "coverage": _coverage_table(cache, df),
    }

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"historical_signals_{start_date}_{end_date}.parquet"
        df.to_parquet(path, index=False)
        man_path = out_dir / f"replay_range_{start_date}_{end_date}_manifest.json"
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        logger.info("range saved: %d rows -> %s", len(df), path)
        logger.info("manifest saved: %s", man_path)
    logger.info("range complete: %d dates, %d rows (status=%s)",
                len(calendar), len(df), n_status)
    return df
