"""多源 CN 个股行情服务。

从 stock_trend.data_provider 抽取的独立 CN 行情获取层，
支持 em → sina → tx 故障切换 + 节流 + 退避。

供 stock_trend 和 sw_industry_rps drilldown 共同调用。
"""

from __future__ import annotations

import json
import inspect
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import akshare as ak
import pandas as pd


CN_SOURCES: dict[str, dict[str, Any]] = {
    "em": {
        "factory": lambda: ak.stock_zh_a_hist,
        "throttle_sec": 2.8,
        "extra_params": {"period": "daily", "adjust": "qfq"},
    },
    "sina": {
        "factory": lambda: ak.stock_zh_a_daily,
        "throttle_sec": 2.2,
        "extra_params": {"adjust": "qfq"},
    },
    "tx": {
        "factory": lambda: ak.stock_zh_a_hist_tx,
        "throttle_sec": 2.2,
        "extra_params": {"adjust": "qfq"},
    },
}

_next_allowed: dict[str, float] = {}
_logger = logging.getLogger(__name__)


def _add_exchange_prefix(symbol: str) -> str:
    sym = str(symbol).strip()
    if not sym or sym[:2].lower() in {"sh", "sz", "bj"}:
        return sym
    if sym[0] in {"6", "9"} or sym.startswith(("51", "52", "56", "58")):
        return f"sh{sym}"
    if sym[0] in {"0", "2", "3"}:
        return f"sz{sym}"
    if sym[0] in {"4", "8"}:
        return f"bj{sym}"
    return sym


def _throttle(tag: str, min_interval: float) -> None:
    now = time.monotonic()
    next_ok = _next_allowed.get(tag, 0.0)
    wait = max(0.0, next_ok - now)
    if wait > 0:
        time.sleep(wait)
    jitter = random.uniform(0.0, 0.25 * min_interval)
    _next_allowed[tag] = time.monotonic() + min_interval + jitter


def _call_ak(fn: Callable, **kwargs: Any) -> Any:
    try:
        sig = inspect.signature(fn)
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return fn(**filtered)
    except TypeError:
        return fn(**kwargs)


_COLUMN_MAP = {
    "date": ["日期", "date", "时间", "Date"],
    "open": ["开盘", "open", "Open"],
    "high": ["最高", "high", "High"],
    "low": ["最低", "low", "Low"],
    "close": ["收盘", "close", "Close"],
    "volume": ["成交量", "volume", "Volume", "vol"],
}


def _normalize(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    out: dict[str, Any] = {}
    for canonical, candidates in _COLUMN_MAP.items():
        picked = next((c for c in candidates if c in df.columns), None)
        if picked is None:
            return None
        out[canonical] = pd.to_numeric(df[picked], errors="coerce") if canonical != "date" else pd.to_datetime(df[picked], errors="coerce")
    result = pd.DataFrame(out)
    result = result.dropna(subset=["date", "close"])
    return result.sort_values("date").reset_index(drop=True)


def fetch_cn_daily(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    max_retries: int = 5,
) -> pd.DataFrame | None:
    """多源获取 CN 个股日线。

    每次重试切换数据源（em → sina → tx → em → ...），
    应用节流和指数退避。
    """
    src_keys = list(CN_SOURCES.keys())
    for attempt in range(1, max_retries + 1):
        src_name = src_keys[(attempt - 1) % len(src_keys)]
        src = CN_SOURCES[src_name]
        fn = src["factory"]()
        if fn is None:
            continue
        try:
            _throttle(src_name, src["throttle_sec"])
            sym = symbol if src_name == "em" else _add_exchange_prefix(symbol)
            kw = {"symbol": sym, "start_date": start_date or "20180101",
                  "end_date": end_date or "22220101", **src["extra_params"]}
            df = _call_ak(fn, **kw)
            normalized = _normalize(df)
            if normalized is not None and not normalized.empty:
                return normalized
        except Exception as e:
            if attempt < max_retries:
                delay = 2.0 * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                _logger.debug("source=%s symbol=%s attempt=%d/%d failed: %s, retry in %.1fs",
                              src_name, symbol, attempt, max_retries, e, delay)
                time.sleep(delay)
            else:
                _logger.warning("all sources failed for %s after %d attempts: %s",
                                symbol, max_retries, e)
    return None


def compute_return(prices: pd.Series, window: int) -> float | None:
    if len(prices) < window + 1:
        return None
    return round(float((prices.iloc[-1] / prices.iloc[-(window + 1)] - 1) * 100), 2)


# ---------------------------------------------------------------------------
# 逐股票缓存（data/raw/cn_stock_returns/）
# 缓存键：symbol + target_date + window + adjust + source
# ---------------------------------------------------------------------------

_DEFAULT_ADJUST = "qfq"


def _cache_root() -> Path:
    from .paths import raw_dir
    return raw_dir() / "cn_stock_returns"


def _stock_cache_path(
    symbol: str,
    target_date: str,
    window: int,
    source: str = "legulegu",
) -> Path:
    safe = symbol.replace(".", "_")
    return _cache_root() / target_date / f"window_{window}" / f"{safe}_{source}.csv"


def load_stock_cache(
    symbol: str,
    target_date: str,
    window: int,
) -> pd.DataFrame | None:
    path = _stock_cache_path(symbol, target_date, window, "legulegu")
    if not path.exists():
        path = _stock_cache_path(symbol, target_date, window, "em")
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        return df if not df.empty else None
    except Exception:
        return None


def save_stock_cache(
    df: pd.DataFrame,
    symbol: str,
    target_date: str,
    window: int,
    source: str = "legulegu",
) -> None:
    path = _stock_cache_path(symbol, target_date, window, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------------------
# 断点状态文件
# ---------------------------------------------------------------------------

CHECKPOINT_FILENAME = "checkpoint.json"


def _checkpoint_path(target_date: str, window: int) -> Path:
    return _cache_root() / target_date / f"window_{window}" / CHECKPOINT_FILENAME


def load_checkpoint(target_date: str, window: int) -> dict[str, Any] | None:
    path = _checkpoint_path(target_date, window)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_checkpoint(checkpoint: dict[str, Any], target_date: str, window: int) -> None:
    path = _checkpoint_path(target_date, window)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def clear_checkpoint(target_date: str, window: int) -> None:
    path = _checkpoint_path(target_date, window)
    if path.exists():
        path.unlink()
