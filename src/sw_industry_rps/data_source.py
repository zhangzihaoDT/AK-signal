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


def fetch_industry_analysis_daily(
    symbol: str = "二级行业",
    start_date: str | None = None,
    end_date: str | None = None,
    max_retries: int = 3,
) -> pd.DataFrame:
    """一次获取全部申万行业单日分析数据。

    替代逐行业调用 index_hist_sw，单次返回所有行业的：
      收盘指数, 涨跌幅, 换手率, 市盈率, 市净率, 均价, 成交额占比, 流通市值, 股息率

    Returns:
        columns: trade_date, close, pct_chg, turnover_rate, pe, pb,
                 avg_price, amount_ratio, float_market_cap, dividend_rate,
                 industry_code, source
    """
    if start_date is None:
        start_date = "20200101"
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            time.sleep(random.uniform(0.5, 1.5))
            try:
                raw = ak.index_analysis_daily_sw(symbol=symbol, start_date=start_date, end_date=end_date)
            except KeyError:
                # 上游无数据时 AKShare 抛 KeyError('发布日期')，视为空结果
                return pd.DataFrame()
            if raw is not None and not raw.empty and "指数代码" in raw.columns:
                df = raw.copy()
                return _normalize_analysis_daily(df)
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                delay = min(2.0 * (2 ** (attempt - 1)) + random.uniform(0, 0.5), 30.0)
                logging.getLogger(__name__).warning(
                    "fetch_industry_analysis_daily attempt %d/%d failed: %s. retry in %.1fs",
                    attempt, max_retries, e, delay,
                )
                time.sleep(delay)
    raise RuntimeError(
        f"fetch_industry_analysis_daily failed after {max_retries} attempts"
    ) from last_err


def _normalize_analysis_daily(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {
        "指数代码": "industry_code",
        "发布日期": "trade_date",
        "收盘指数": "close",
        "涨跌幅": "pct_chg",
        "成交量": "volume",
        "换手率": "turnover_rate",
        "市盈率": "pe",
        "市净率": "pb",
        "均价": "avg_price",
        "成交额占比": "amount_ratio",
        "流通市值": "float_market_cap",
        "平均流通市值": "avg_float_market_cap",
        "股息率": "dividend_rate",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"])
    numeric_cols = ["close", "pct_chg", "volume", "turnover_rate", "pe", "pb",
                    "avg_price", "amount_ratio", "float_market_cap",
                    "avg_float_market_cap", "dividend_rate"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # 统一 industry_code 格式：添加 .SI 后缀以兼容现有系统
    if "industry_code" in df.columns:
        df["industry_code"] = df["industry_code"].astype(str).str.strip().apply(
            lambda c: c if c.endswith(".SI") else f"{c}.SI"
        )
    df["source"] = "swsresearch_analysis"
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return df


def fetch_industry_realtime(
    symbol: str = "二级行业",
    max_retries: int = 3,
) -> pd.DataFrame:
    """一次获取全部申万行业实时行情。

    收盘后调用时，最新价可视为当日收盘候选值。

    Returns:
        columns: industry_code, industry_name, pre_close, open, price,
                 amount, volume, high, low, source
    """
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            time.sleep(random.uniform(0.3, 0.8))
            df = ak.index_realtime_sw(symbol=symbol)
            if df is not None and not df.empty:
                return _normalize_realtime(df)
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                delay = min(2.0 * (2 ** (attempt - 1)) + random.uniform(0, 0.5), 30.0)
                logging.getLogger(__name__).warning(
                    "fetch_industry_realtime attempt %d/%d failed: %s. retry in %.1fs",
                    attempt, max_retries, e, delay,
                )
                time.sleep(delay)
    raise RuntimeError(
        f"fetch_industry_realtime failed after {max_retries} attempts"
    ) from last_err


def _normalize_realtime(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {
        "指数代码": "industry_code",
        "指数名称": "industry_name",
        "昨收盘": "prev_close",
        "今开盘": "open",
        "最新价": "close",
        "成交额": "amount",
        "成交量": "volume",
        "最高价": "high",
        "最低价": "low",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    numeric_cols = ["prev_close", "open", "close", "amount", "volume", "high", "low"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # 统一 industry_code 格式：添加 .SI 后缀以兼容现有系统
    if "industry_code" in df.columns:
        df["industry_code"] = df["industry_code"].astype(str).str.strip().apply(
            lambda c: c if c.endswith(".SI") else f"{c}.SI"
        )
    df["source"] = "swsresearch_realtime"
    df["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return df


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
