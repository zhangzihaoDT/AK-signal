"""
Range Replay — 区间历史信号重放（v0.5.0 P0-7）。

对 [start_date, end_date] 内每个交易日重放 Layer①/②/③ 信号，产出
historical_signals_{start}_{end}.parquet（长表）+ replay_range_manifest.json。

实现：复用单日期重放（同一套已通过 parity 的规则路径），通过 cache 预加载
ETF 行情 / 行业 metrics，避免逐日重复读盘。Layer③（个股趋势内存重算）为慢路径，
可用 --layers 12 跳过。

一致性：区间在已有正式产物日期的输出与单日期重放一致（而单日期已通过 parity）。
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
) -> pd.DataFrame:
    """对 [start_date, end_date] 区间逐交易日重放三层信号。

    Args:
        start_date / end_date: YYYYMMDD（含边界）
        layers: 参与重放的层位集合，如 "123" / "12"（跳过 Layer③ 慢路径）
        out_dir: 产物目录（默认 outputs/research）；None 时不落盘
        cache: 预加载输入（engine.build_replay_cache()）
    """
    logger = build_logger(log_level)
    logger.info("=" * 60)
    logger.info("REPLAY range: %s -> %s (layers=%s)", start_date, end_date, layers)
    logger.info("=" * 60)

    cache = cache or replay_engine.build_replay_cache()
    calendar = replay_engine.replay_calendar(cache, start_date, end_date)
    if not calendar:
        logger.error("no trading days in [%s, %s]", start_date, end_date)
        return sch.empty_frame()
    logger.info("calendar: %d trading days", len(calendar))

    want_l3 = "3" in layers
    frames: list[pd.DataFrame] = []
    t_start = pd.Timestamp.now()
    for i, d in enumerate(calendar, 1):
        df_d = replay_engine.replay_single_date(d, out_dir=None, log_level=log_level, cache=cache)
        if not want_l3 and not df_d.empty:
            df_d = df_d[df_d["layer"] != "3"].copy()
        if not df_d.empty:
            frames.append(df_d)
        if i == 1 or i % 50 == 0 or i == len(calendar):
            el = (pd.Timestamp.now() - t_start).total_seconds()
            logger.info("  [%d/%d] %s (%.0fs)", i, len(calendar), d, el)

    df = pd.concat(frames, ignore_index=True) if frames else sch.empty_frame()
    df = _stamp(df)

    manifest = {
        "start_date": start_date,
        "end_date": end_date,
        "layers": layers,
        "n_dates": len(calendar),
        "calendar": calendar,
        "n_rows": int(len(df)),
        "rows_per_layer": df["layer"].value_counts().to_dict() if not df.empty else {},
        "rule_version": sch.RULE_VERSION,
        "config_hash": sch.config_hash(),
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
    logger.info("range complete: %d dates, %d rows", len(calendar), len(df))
    return df
