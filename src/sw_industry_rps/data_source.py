from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd


def fetch_industry_master() -> pd.DataFrame:
    df = ak.sw_index_second_info()
    df = df.rename(
        columns={
            "行业代码": "industry_code",
            "行业名称": "industry_name",
            "上级行业": "parent_industry",
            "成份个数": "constituent_count",
        }
    )
    df["industry_code"] = df["industry_code"].astype(str).str.strip()
    df["industry_name"] = df["industry_name"].astype(str).str.strip()
    return df


def fetch_industry_hist(
    industry_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
) -> pd.DataFrame:
    code = industry_code.replace(".SI", "")
    if start_date is None:
        start_date = "20200101"
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            time.sleep(random.uniform(0.5, 1.5))
            df = ak.index_hist_sw(symbol=code, period="day")
            if df is not None and not df.empty:
                return _normalize_sw_hist(df, industry_code)
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                delay = min(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5), max_delay)
                logging.getLogger(__name__).warning(
                    "fetch_industry_hist attempt %d/%d failed for %s: %s. retry in %.1fs",
                    attempt, max_retries, industry_code, e, delay,
                )
                time.sleep(delay)
    raise RuntimeError(
        f"fetch_industry_hist failed for {industry_code} after {max_retries} attempts"
    ) from last_err


def _normalize_sw_hist(df: pd.DataFrame, industry_code: str) -> pd.DataFrame:
    col_map = {
        "日期": "trade_date",
        "收盘": "close",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["industry_code"] = industry_code
    df["source"] = "swsresearch"
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df
