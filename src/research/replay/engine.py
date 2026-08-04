"""
Replay Engine — 单日期历史信号重放（v0.5.0 核心）。

对指定 trade_date，纯离线用现价历史数据 + 共享规则函数重放 Layer①/②/③ 信号，
输出 historical_signals_{trade_date}.parquet。

与 daily pipeline 分离：只调用纯计算函数（rotation / confirmation / selection），
跳过 drilldown（成分股穿透）、报表、provisional 与网络抓取。

设计约束（S0）：
  - 输入显式（trade_date 由调用方给定，不做「最新日期」推断）
  - 严格 as-of：行情截断到 trade_date，避免 look-ahead
  - 不写 daily pipeline 的正式产物（个股趋势内存计算，不覆盖 selection_inputs）
  - 每条记录带 rule_version / config_hash / signal_origin=replayed
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import (
    config_dir,
    etf_signal_master_dir, etf_signal_raw_dir,
    sw_industry_processed_dir, stock_universe_path,
)
from src.etf_signal import indicators as etf_indicators
from src.etf_signal import rotation as etf_rotation
from src.etf_signal import signal as etf_signal
from src.etf_signal import account as etf_account
from src.etf_signal import master as etf_master
from src.sw_industry_rps import storage as sw_storage
from src.sw_industry_rps import confirmation as sw_confirmation
from src.selection import selection as sel_module
from src.selection.universe import load_universe_items
from src.trend_engine import inputs as trend_inputs
from src.research.signals import schema as sch


def build_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("research.replay")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    return logger


def _load_all_etf_raw(master: pd.DataFrame) -> pd.DataFrame:
    raw_dir = etf_signal_raw_dir()
    frames: list[pd.DataFrame] = []
    for code in master["fund_code"]:
        path = raw_dir / f"{code}.parquet"
        if path.exists():
            try:
                df = pd.read_parquet(path)
            except Exception:
                continue
            if not df.empty:
                df["fund_code"] = code
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _truncated_combined(master: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    combined = _load_all_etf_raw(master)
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
    return combined[pd.to_datetime(combined["date"]) <= pd.Timestamp(trade_date)]


def _load_master() -> pd.DataFrame:
    return etf_master.load_master(etf_signal_master_dir())


# ── Layer ①：ETF 横截面 + 趋势状态 ─────────────────────────────────

def _replay_layer1(
    trade_date: str,
    logger: logging.Logger,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """重放 Layer①，返回 (signal_rows, rotation_df, account_df)。"""
    master = _load_master()
    if master.empty:
        logger.error("no ETF master")
        return sch.empty_frame(), pd.DataFrame(), pd.DataFrame()

    combined = _truncated_combined(master, trade_date)
    if combined.empty:
        logger.error("no ETF raw data <= %s", trade_date)
        return sch.empty_frame(), pd.DataFrame(), pd.DataFrame()

    rotation_df = etf_rotation.compute_rotation_metrics(combined, master)
    indicators_df = etf_indicators.compute_indicators(combined)
    rps_cols = [c for c in ["rps15", "rps20", "rps60"] if c in rotation_df.columns]
    if not indicators_df.empty and rps_cols:
        indicators_df = indicators_df.merge(
            rotation_df[["fund_code"] + rps_cols].drop_duplicates(subset=["fund_code"]),
            on="fund_code", how="left",
        )

    watchlist = etf_signal.build_trend_watchlist(indicators_df, master) if not indicators_df.empty else pd.DataFrame()
    blacklist = etf_account.load_account_blacklist(config_dir() / "guojin_tradable_blacklist.csv")
    account_df = etf_account.map_watchlist_to_account(watchlist, blacklist) if not watchlist.empty else pd.DataFrame()

    if account_df.empty:
        logger.warning("layer1 account empty for %s", trade_date)
        return sch.empty_frame(), rotation_df, account_df

    # rps15 以 rotation（全精度）为准；trend_state 以 account_candidates 为准
    rps_map: dict[str, float | None] = {}
    if not rotation_df.empty:
        for _, rr in rotation_df.iterrows():
            rps_map[str(rr.get("fund_code", ""))] = _num(rr.get("rps15"))
    theme_map = dict(zip(rotation_df["fund_code"], rotation_df.get("theme", ""))) if not rotation_df.empty else {}
    rows: list[dict[str, Any]] = []
    for _, r in account_df.iterrows():
        code = str(r.get("fund_code", ""))
        rows.append(sch.new_row(
            trade_date=trade_date, entity_type="etf", entity_code=code,
            theme=str(theme_map.get(code, "") or ""), layer="1",
            rps15=rps_map.get(code),
            trend_state=str(r.get("trend_state", "") or ""),
            signal_reason=str(r.get("reason", "") or ""),
            source_trade_date=trade_date,
            data_status=str(r.get("data_status", "") or "confirmed"),
        ))
    logger.info("layer1 replayed: %d ETF rows", len(rows))
    return pd.DataFrame(rows), rotation_df, account_df


# ── Layer ②：主题行业确认 ─────────────────────────────────────────

def _replay_layer2(
    trade_date: str,
    logger: logging.Logger,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """重放 Layer②，返回 (signal_rows, confirmation_focus_df)。"""
    metrics_df = sw_storage.load_metrics(sw_industry_processed_dir())
    if metrics_df.empty:
        logger.error("no industry metrics")
        return sch.empty_frame(), pd.DataFrame()

    focus = sw_confirmation.compute_focus_snapshot(metrics_df, date=trade_date)
    rows: list[dict[str, Any]] = []
    for _, r in focus.iterrows():
        rows.append(sch.new_row(
            trade_date=trade_date, entity_type="industry", entity_code=str(r.get("industry_code", "")),
            theme=str(r.get("theme", "") or ""), layer="2",
            rps15=_num(r.get("RPS15")),
            confirmation_status=str(r.get("strength_level", "") or ""),
            signal_reason=f"delta_rps15={_num(r.get('delta_rps15'))} new_entry={_int(r.get('new_entry'))}",
            source_trade_date=trade_date,
            data_status=str(r.get("data_status", "") or "confirmed"),
        ))
    logger.info("layer2 replayed: %d industry rows", len(rows))
    return pd.DataFrame(rows), focus


# ── Layer ③：交易候选（Selection） ────────────────────────────────

def _replay_layer3(
    trade_date: str,
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    confirmation_df: pd.DataFrame,
    logger: logging.Logger,
) -> pd.DataFrame:
    universe_items = load_universe_items(stock_universe_path())
    stock_items = trend_inputs.stock_items(universe_items)

    trend_df = trend_inputs.build_stock_metrics(
        stock_items, trade_date=trade_date, offline=True, persist=False,
        log_level=logging.getLevelName(logger.level),
    ) if stock_items else pd.DataFrame()

    master = _load_master()
    candidates = sel_module.build_candidates(
        rotation_df=rotation_df,
        account_df=account_df,
        master_df=master,
        confirmation_df=confirmation_df,
        universe_items=universe_items,
        trend_df=trend_df,
    )
    action = (candidates.get("action") or {}).get("level", "WAIT")

    rows: list[dict[str, Any]] = []
    for bucket in candidates.get("buckets", []):
        for sub in bucket.get("themes", []):
            theme = str(sub.get("theme", "") or "")
            for tier in ("leaders", "high_beta", "equipment"):
                for a in sub.get("stock_watchlist", {}).get(tier, []):
                    rows.append(sch.new_row(
                        trade_date=trade_date, entity_type="stock", entity_code=str(a.get("code", "")),
                        theme=theme, layer="3",
                        trend_score=_num(a.get("score_trend")),
                        trend_state=str(a.get("trend_status", "") or ""),
                        selection_status=str(a.get("selection_status", "") or ""),
                        recommended_action=action,
                        signal_reason=str(a.get("reason", "") or ""),
                        data_status=str(a.get("data_status", "") or "current"),
                    ))
            for a in sub.get("core_etf", []) + sub.get("sub_industry_etf", []):
                rows.append(sch.new_row(
                    trade_date=trade_date, entity_type="etf", entity_code=str(a.get("code", "")),
                    theme=theme, layer="3",
                    rps15=_num(a.get("rps15")),
                    trend_state=str(a.get("trend_status", "") or ""),
                    selection_status=str(a.get("state", "") or ""),
                    recommended_action=action,
                    signal_reason=str(a.get("reason", "") or ""),
                    source_trade_date=trade_date,
                    data_status=str(a.get("data_status", "") or "current"),
                ))
    logger.info("layer3 replayed: %d rows (action=%s)", len(rows), action)
    return pd.DataFrame(rows)


# ── 汇总 ───────────────────────────────────────────────────────────

def replay_single_date(
    trade_date: str,
    *,
    out_dir: Path | None = None,
    log_level: str = "INFO",
) -> pd.DataFrame:
    """对单个 trade_date 纯离线重放 Layer①/②/③ 信号。

    Args:
        trade_date: 目标 trade_date YYYYMMDD
        out_dir: 产物目录（默认 outputs/replay）；None 时不落盘

    Returns:
        historical_signals DataFrame（SIGNAL_COLUMNS）
    """
    logger = build_logger(log_level)
    logger.info("=" * 60)
    logger.info("REPLAY single date: %s", trade_date)
    logger.info("=" * 60)

    l1, rotation_df, account_df = _replay_layer1(trade_date, logger)
    l2, confirmation_df = _replay_layer2(trade_date, logger)

    l3 = sch.empty_frame()
    if not rotation_df.empty:
        l3 = _replay_layer3(trade_date, rotation_df, account_df, confirmation_df, logger)

    parts = [p for p in (l1, l2, l3) if not p.empty]
    df = pd.concat(parts, ignore_index=True) if parts else sch.empty_frame()
    if not df.empty:
        df["signal_origin"] = "replayed"
        df["rule_version"] = sch.RULE_VERSION
        df["config_hash"] = sch.config_hash()
        for c in ("rps15", "trend_score"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df[sch.SIGNAL_COLUMNS].copy()

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"historical_signals_{trade_date}.parquet"
        df.to_parquet(path, index=False)
        logger.info("replay saved: %d rows -> %s", len(df), path)
    logger.info("replay complete: %s | %d rows", trade_date, len(df))
    return df


def _num(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        f = float(v)
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None or (isinstance(v, float) and v != v):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
