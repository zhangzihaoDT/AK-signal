from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import akshare as ak
import pandas as pd

from asset import Asset


class DataProvider:
    def get_daily(self, asset: Asset, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        raise NotImplementedError


@dataclass(frozen=True)
class ProviderConfig:
    start_date: str = "20180101"
    end_date: str = "22220101"
    adjust: str = "qfq"


def normalize_ohlcv(df: pd.DataFrame, asset: Asset, column_candidates: dict[str, list[str]]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    selected: dict[str, str] = {}
    missing: list[str] = []
    for canonical in ["date", "open", "high", "low", "close", "volume"]:
        candidates = column_candidates.get(canonical, [])
        picked = next((c for c in candidates if c in df.columns), None)
        if not picked:
            missing.append(canonical)
            continue
        selected[canonical] = picked

    if missing:
        raise ValueError(
            "normalize_ohlcv missing required columns: "
            f"asset=({asset.market},{asset.symbol}) missing={missing} available={list(df.columns)}"
        )

    out = df[[selected[k] for k in ["date", "open", "high", "low", "close", "volume"]]].rename(
        columns={v: k for k, v in selected.items()}
    )
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["date", "open", "high", "low", "close", "volume"]).copy()
    out = out.sort_values("date").reset_index(drop=True)
    return out


class AKShareProvider(DataProvider):
    def __init__(
        self,
        cfg: ProviderConfig | Any,
        logger: logging.Logger,
    ) -> None:
        self.cfg = cfg
        self.logger = logger

    def get_daily(self, asset: Asset, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        df = self.fetch_daily(asset, start_date=start_date, end_date=end_date)

        cfg_start = getattr(self.cfg, "start_date", "")
        cfg_end = getattr(self.cfg, "end_date", "")
        start = start_date or cfg_start
        end = end_date or cfg_end

        if not df.empty:
            if start:
                df = df[df["date"] >= pd.to_datetime(start)]
            if end:
                df = df[df["date"] <= pd.to_datetime(end)]
            df = df.reset_index(drop=True)
        return df

    def fetch_daily(self, asset: Asset, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        if asset.market == "CN":
            return self._get_cn_daily(asset, start_date=start_date, end_date=end_date)
        if asset.market == "HK":
            return self._get_hk_daily(asset)
        if asset.market == "US":
            return self._get_us_daily(asset)
        raise ValueError(f"Unsupported market: {asset.market}")

    def _get_cn_daily(self, asset: Asset, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        s = start_date or getattr(self.cfg, "start_date", "20180101")
        e = end_date or getattr(self.cfg, "end_date", "22220101") or "22220101"
        df = ak.stock_zh_a_hist(
            symbol=asset.symbol,
            period="daily",
            start_date=s,
            end_date=e,
            adjust=getattr(self.cfg, "adjust", "qfq"),
        )
        return normalize_ohlcv(
            df,
            asset,
            {
                "date": ["日期", "date", "时间", "Date"],
                "open": ["开盘", "open", "Open"],
                "high": ["最高", "high", "High"],
                "low": ["最低", "low", "Low"],
                "close": ["收盘", "close", "Close"],
                "volume": ["成交量", "volume", "Volume", "vol"],
            },
        )

    def _get_hk_daily(self, asset: Asset) -> pd.DataFrame:
        sym = str(asset.symbol).strip()
        if sym.isdigit() and len(sym) < 5:
            sym = sym.zfill(5)
        df = ak.stock_hk_daily(symbol=sym)
        return normalize_ohlcv(
            df,
            asset,
            {
                "date": ["date", "日期", "时间", "Date"],
                "open": ["open", "开盘", "Open"],
                "high": ["high", "最高", "High"],
                "low": ["low", "最低", "Low"],
                "close": ["close", "收盘", "Close"],
                "volume": ["volume", "成交量", "Volume", "vol"],
            },
        )

    def _get_us_daily(self, asset: Asset) -> pd.DataFrame:
        df = ak.stock_us_daily(symbol=asset.symbol)
        return normalize_ohlcv(
            df,
            asset,
            {
                "date": ["date", "日期", "时间", "Date"],
                "open": ["open", "开盘", "Open"],
                "high": ["high", "最高", "High"],
                "low": ["low", "最低", "Low"],
                "close": ["close", "收盘", "Close"],
                "volume": ["volume", "成交量", "Volume", "vol"],
            },
        )
