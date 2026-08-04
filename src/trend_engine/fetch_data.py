from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import akshare as ak
import pandas as pd


@dataclass(frozen=True)
class FetchConfig:
    start_date: str = "20180101"
    end_date: str = "22220101"
    adjust: str = "qfq"


def fetch_hs300_daily(cfg: FetchConfig) -> pd.DataFrame:
    try:
        df = ak.stock_zh_index_daily_em(symbol="sh000300")
    except Exception:
        df = ak.stock_zh_index_daily(symbol="sh000300")
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    if cfg.start_date:
        df = df[df["date"] >= pd.to_datetime(cfg.start_date)]
    if cfg.end_date:
        df = df[df["date"] <= pd.to_datetime(cfg.end_date)]
    df = df.reset_index(drop=True)

    for c in ["open", "close", "high", "low", "volume", "amount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_or_fetch_hs300(
    raw_dir: Path,
    cfg: FetchConfig,
    logger: logging.Logger,
    force: bool = False,
    offline: bool = False,
) -> pd.DataFrame:
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "_benchmark_sh000300.csv"

    if raw_path.exists() and not force:
        try:
            df = pd.read_csv(raw_path, parse_dates=["date"])
            if not df.empty:
                return df.sort_values("date").reset_index(drop=True)
        except Exception as e:
            logger.warning("failed reading cached raw %s: %s", raw_path, e)

    if offline:
        # 离线模式：无基准缓存时不联网获取，relative_strength_20d 记为缺失
        logger.warning("offline: no cached hs300 benchmark, skipping fetch")
        return pd.DataFrame()

    try:
        df = fetch_hs300_daily(cfg)
    except Exception as e:
        logger.warning("failed fetching hs300: %s", e)
        return pd.DataFrame()
    if not df.empty:
        df.to_csv(raw_path, index=False, encoding="utf-8")
        logger.info("saved raw data: %s (%d rows)", raw_path, len(df))
    else:
        logger.warning("empty hs300 data fetched")
    return df



