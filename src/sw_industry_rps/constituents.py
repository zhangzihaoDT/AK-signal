from __future__ import annotations

import logging
import re
import time
from typing import Any

import pandas as pd
import requests
from io import StringIO


_LEGULEGU_URL = "https://legulegu.com/stockdata/index-composition?industryCode={code}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

_CLEAN_COLUMNS = [
    "序号", "股票代码", "股票简称", "纳入时间", "申万2级", "细分概念",
    "价格", "市盈率", "市盈率ttm", "市净率", "ROE", "股息率",
    "市值", "近1日涨幅", "近5日涨幅", "今年以来涨幅",
    "净利润增速", "营收增速",
]


def _clean_legulegu_table(raw: pd.DataFrame) -> pd.DataFrame:
    """清理 legulegu HTML 表格：重命名列、去 JSON-LD 污染、处理缺失值。"""
    if raw.empty:
        return raw
    n_cols = raw.shape[1]
    cols = _CLEAN_COLUMNS[:n_cols]
    n_extra = n_cols - len(cols)
    if n_extra > 0:
        cols.extend([f"extra_{i}" for i in range(n_extra)])
    raw.columns = cols
    raw = raw.drop(columns=[c for c in raw.columns if c.startswith("extra_")], errors="ignore")
    if "股票代码" in raw.columns:
        raw = raw.dropna(subset=["股票代码"])
    for c in ["价格", "市盈率", "市盈率ttm", "市净率", "市值"]:
        if c in raw.columns:
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
    for c in ["股息率", "近1日涨幅", "近5日涨幅", "今年以来涨幅", "净利润增速", "营收增速"]:
        if c in raw.columns and raw[c].dtype != "float64":
            raw[c] = raw[c].astype(str).str.strip("%")
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
    if "股票代码" in raw.columns:
        raw["股票代码"] = raw["股票代码"].astype(str).str.strip()
    if "股票简称" in raw.columns:
        raw["股票简称"] = raw["股票简称"].astype(str).str.strip()
    return raw.reset_index(drop=True)


def fetch_constituent_list(
    industry_code: str,
    max_retries: int = 3,
    base_delay: float = 5.0,
) -> pd.DataFrame:
    url = _LEGULEGU_URL.format(code=industry_code)
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = base_delay * (2 ** (attempt - 1))
                logging.getLogger(__name__).warning(
                    "429 rate limited for %s, retry in %.0fs (attempt %d/%d)",
                    industry_code, wait, attempt, max_retries,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            wait = base_delay * (2 ** (attempt - 1))
            logging.getLogger(__name__).warning(
                "fetch failed for %s: %s, retry in %.0fs", industry_code, e, wait,
            )
            time.sleep(wait)
    tables = pd.read_html(StringIO(resp.text))
    if not tables:
        return pd.DataFrame()
    raw = tables[0]
    df = _clean_legulegu_table(raw)
    if df.empty:
        return df
    df["industry_code"] = industry_code
    total_mv = df["市值"].sum()
    df["weight"] = (df["市值"] / total_mv * 100).round(2) if total_mv > 0 else 0.0
    df = df.sort_values("weight", ascending=False).reset_index(drop=True)
    return df


def fetch_constituent_list_cached(
    industry_code: str,
    raw_dir: Any,
) -> pd.DataFrame:
    from pathlib import Path
    from . import storage
    raw_dir = Path(raw_dir) if isinstance(raw_dir, str) else raw_dir
    safe = storage.safe_code(industry_code)
    cache_path = raw_dir / f"constituents_{safe}.csv"
    if cache_path.exists():
        df = pd.read_csv(cache_path)
        if not df.empty:
            return df
    df = fetch_constituent_list(industry_code)
    if not df.empty:
        raw_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        import os
        os.replace(str(tmp), str(cache_path))
    return df
