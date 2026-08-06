"""
个股趋势产物构建 — Layer ③ 的离线输入（selection_inputs）。

职责：
  把 trend_engine 对 universe 股票池的批量趋势计算，沉淀为统一的
  outputs/selection_inputs/stock_metrics_{trade_date}.parquet，
  让 Layer ③ 只消费已落盘的指标，不再感知新浪 / 东财 / AKShare / 缓存机制。

设计约定（v0.4.3）：
  - 只有股票资产进入此产物；ETF 趋势一律复用 Layer① rotation（Layer ③ 不重复抓取）。
  - 默认离线构建（读缓存，确定性、不联网）；--allow-online-fetch 仅用于手工补数。
  - 缺失数据不阻塞：按行标记 data_status = current / stale / missing。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from src.common.paths import selection_inputs_dir
from src.trend_engine.engine import compute_trends

logger = logging.getLogger("trend_engine.inputs")

# 股票 tier（ETF 的 theme_etf / sub_industry_etf 不进入个股趋势产物）
STOCK_TIERS = {
    "leader", "high_beta", "equipment_upstream",
    "computing_chip", "optical_interconnect", "server_network",
    "semiconductor_equipment", "semiconductor_components",
}

# 统一产物 schema（Layer③ 消费契约；asset_id 与 symbol 同值，symbol 供 selection 匹配）
STOCK_METRICS_COLUMNS = [
    "asset_id", "symbol", "trade_date", "date", "close",
    "return_5d", "return_20d", "trend_score", "score_trend", "watch_level", "action", "risk_flags",
    "volatility_20d", "drawdown_20d",
    "name", "market", "source", "data_status", "source_trade_date", "lag_days",
]


def is_stock_item(item: Any) -> bool:
    """是否股票资产（按 universe 的 tier/category 判定）。"""
    return str(getattr(getattr(item, "asset", None), "category", "")).strip() in STOCK_TIERS


def stock_items(items: Sequence[Any]) -> list[Any]:
    return [it for it in items if is_stock_item(it)]


def stock_metrics_path(trade_date: str) -> Path:
    return selection_inputs_dir() / f"stock_metrics_{trade_date}.parquet"


def latest_stock_metrics_trade_date() -> str | None:
    d = selection_inputs_dir()
    if not d.exists():
        return None
    dates: list[str] = []
    for p in d.glob("stock_metrics_*.parquet"):
        stem = p.name[len("stock_metrics_"):-len(".parquet")]
        if len(stem) == 8 and stem.isdigit():
            dates.append(stem)
    return max(dates) if dates else None


def load_stock_metrics(trade_date: str) -> pd.DataFrame:
    path = stock_metrics_path(trade_date)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning("failed to load stock metrics %s: %s", path, e)
        return pd.DataFrame()


def _to_date_str(v: Any) -> str:
    if v is None or pd.isna(v):
        return ""
    try:
        return pd.Timestamp(v).strftime("%Y%m%d")
    except Exception:
        return str(v).strip()


def _business_days_between(start: Any, end: Any) -> int:
    try:
        s = pd.Timestamp(start).strftime("%Y-%m-%d")
        e = pd.Timestamp(end).strftime("%Y-%m-%d")
        return int(np.busday_count(np.datetime64(s), np.datetime64(e), weekmask="1111100"))
    except Exception:
        return 0


def _processed_metrics(processed_path: Path) -> dict[str, Any]:
    """从 processed CSV（含指标）提取 return/volatility/drawdown 等字段。"""
    out = {"return_5d": None, "return_20d": None, "volatility_20d": None, "drawdown_20d": None}
    if not processed_path.exists():
        return out
    try:
        df = pd.read_csv(processed_path, parse_dates=["date"])
    except Exception:
        return out
    if df.empty:
        return out
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if close.empty:
        return out

    def _last(series: pd.Series) -> float | None:
        v = series.dropna().iloc[-1] if not series.dropna().empty else None
        return round(float(v), 4) if v is not None and np.isfinite(v) else None

    out["return_5d"] = _last(close.pct_change(5))
    if "return_20d" in df.columns:
        out["return_20d"] = _last(pd.to_numeric(df["return_20d"], errors="coerce"))
    else:
        out["return_20d"] = _last(close.pct_change(20))
    out["volatility_20d"] = _last(close.pct_change().rolling(20, min_periods=20).std())
    high = pd.to_numeric(df["high"], errors="coerce")
    if not high.dropna().empty:
        out["drawdown_20d"] = _last(1 - close / high.rolling(20).max())
    return out


def _normalize_row(
    row: pd.Series,
    trade_date: date,
    processed_dir: Path,
) -> dict[str, Any]:
    """把 compute_trends 的一行结果归一化为统一 schema。"""
    symbol = str(row.get("symbol", ""))
    source = str(row.get("data_source", "") or "").strip()
    src_date_raw = row.get("date")
    src_date = None
    if src_date_raw is not None and not (isinstance(src_date_raw, float) and pd.isna(src_date_raw)):
        try:
            src_date = pd.Timestamp(src_date_raw).date()
        except Exception:
            src_date = None

    if source == "failed" or src_date is None:
        data_status = "missing"
    elif src_date < trade_date:
        data_status = "stale"
    else:
        data_status = "current"

    lag = _business_days_between(src_date, trade_date) if (data_status in ("stale", "current") and src_date is not None) else 0

    metrics = _processed_metrics(processed_dir / f"{row.get('market', '')}_{symbol}.csv")

    def _float(v: Any) -> float | None:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try:
            f = float(v)
            return round(f, 4) if np.isfinite(f) else None
        except (TypeError, ValueError):
            return None

    return {
        "asset_id": symbol,
        "symbol": symbol,
        "trade_date": pd.Timestamp(trade_date),
        "date": pd.Timestamp(src_date) if src_date is not None else pd.NaT,
        "close": _float(row.get("close")),
        "return_5d": metrics["return_5d"],
        "return_20d": metrics["return_20d"],
        "trend_score": _float(row.get("score_trend")),
        "score_trend": _float(row.get("score_trend")),
        "watch_level": str(row.get("watch_level", "") or ""),
        "action": str(row.get("action", "") or ""),
        "risk_flags": str(row.get("risk_flags", "") or ""),
        "volatility_20d": metrics["volatility_20d"],
        "drawdown_20d": metrics["drawdown_20d"],
        "name": str(row.get("name", symbol)),
        "market": str(row.get("market", "") or ""),
        "source": source,
        "data_status": data_status,
        "source_trade_date": pd.Timestamp(src_date) if src_date is not None else pd.NaT,
        "lag_days": lag,
    }


def build_stock_metrics(
    items: Sequence[Any],
    *,
    trade_date: str,
    offline: bool = True,
    persist: bool = True,
    log_level: str = "INFO",
) -> pd.DataFrame:
    """构建 outputs/selection_inputs/stock_metrics_{trade_date}.parquet（可内存计算不落盘）。

    Args:
        items: universe 资产列表（内部只处理股票 tier）
        trade_date: 目标 trade_date YYYYMMDD（产物命名 + 行内 trade_date）
        offline: 仅用缓存（确定性，不联网）；False = 允许在线补数（轻量重试、无缓存记 missing）
        persist: False 时只返回 DataFrame，不写 parquet，也不写 processed CSV（Replay 内存调用，
                 避免覆盖正式产物与 data/processed/CN_*.csv）
    """
    from src.common.paths import processed_dir as common_processed_dir

    lgr = logging.getLogger("trend_engine.inputs")
    t0 = datetime.now()
    stock = stock_items(items)
    lgr.info("stock universe: %d assets (excluded %d ETF assets)",
             len(stock), len(items) - len(stock))

    trend_df = compute_trends(
        stock,
        offline=offline,
        as_of_date=trade_date,
        persist_processed=persist,
        log_level=log_level,
    ) if stock else pd.DataFrame()

    trade_d = pd.Timestamp(trade_date).date()
    processed_dir = common_processed_dir()
    rows = [_normalize_row(r, trade_d, processed_dir)
            for _, r in trend_df.iterrows()] if not trend_df.empty else []

    df = pd.DataFrame(rows, columns=STOCK_METRICS_COLUMNS)
    if not df.empty:
        df = df.sort_values("asset_id").reset_index(drop=True)

    if persist:
        path = stock_metrics_path(trade_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
    elapsed = (datetime.now() - t0).total_seconds()

    counts = df["data_status"].value_counts().to_dict() if not df.empty else {}
    lgr.info("stock trend inputs built: %d/%d | current=%d stale=%d missing=%d | online=%s | %.1fs%s",
             len(df), len(stock),
             counts.get("current", 0), counts.get("stale", 0), counts.get("missing", 0),
             "off" if offline else "on", elapsed,
             "" if persist else " (in-memory)")
    return df
