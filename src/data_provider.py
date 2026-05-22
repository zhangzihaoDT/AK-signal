from __future__ import annotations

import inspect
import logging
import random
import time
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
        self._next_allowed_ts: dict[str, float] = {}

    def get_daily(
        self,
        asset: Asset,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> pd.DataFrame:
        df = self.fetch_daily(asset, start_date=start_date, end_date=end_date, source=source)

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

    def fetch_daily(
        self,
        asset: Asset,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> pd.DataFrame:
        if asset.market == "CN":
            return self._get_cn_daily(asset, start_date=start_date, end_date=end_date, source=source)
        if asset.market == "HK":
            return self._get_hk_daily(asset)
        if asset.market == "US":
            return self._get_us_daily(asset)
        raise ValueError(f"Unsupported market: {asset.market}")

    def _throttle(self, tag: str, min_interval_sec: float) -> None:
        now = time.monotonic()
        next_allowed = float(self._next_allowed_ts.get(tag, 0.0))
        wait = max(0.0, next_allowed - now)
        if wait > 0:
            time.sleep(wait)
        self._next_allowed_ts[tag] = time.monotonic() + float(min_interval_sec) + random.uniform(0.0, 0.25 * float(min_interval_sec))

    @staticmethod
    def _call_ak(fn: Any, **kwargs: Any) -> Any:
        try:
            sig = inspect.signature(fn)
            filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
            return fn(**filtered)
        except Exception:
            return fn(**kwargs)

    @staticmethod
    def _cn_symbol_with_exchange(symbol: str) -> str:
        sym = str(symbol).strip()
        if not sym:
            return sym
        if sym[:2].lower() in {"sh", "sz", "bj"}:
            return sym
        if sym.startswith(("60", "68", "90", "51", "52")) or (sym and sym[0] == "6"):
            return f"sh{sym}"
        if sym.startswith(("00", "30", "20")) or (sym and sym[0] in {"0", "2", "3"}):
            return f"sz{sym}"
        if sym and sym[0] in {"4", "8"}:
            return f"bj{sym}"
        return sym

    @staticmethod
    def _is_cn_etf(asset: Asset) -> bool:
        cat = (asset.category or "").strip().lower()
        if "etf" in cat:
            return True
        sym = str(asset.symbol).strip()
        if len(sym) == 6 and sym.isdigit() and sym.startswith(("51", "52", "58", "15", "16", "18")):
            return True
        return False

    @staticmethod
    def _cn_etf_symbol_variants(asset: Asset) -> list[str]:
        sym = str(asset.symbol).strip()
        variants: list[str] = []
        if sym:
            variants.append(sym)
        if len(sym) == 6 and sym.isdigit():
            ex = (asset.exchange or "").strip().upper()
            if ex in {"SSE", "SHSE", "SH"}:
                variants.append(f"sh{sym}")
            elif ex in {"SZSE", "SZ"}:
                variants.append(f"sz{sym}")
            else:
                variants.append(f"sh{sym}")
                variants.append(f"sz{sym}")
        deduped: list[str] = []
        seen: set[str] = set()
        for v in variants:
            if v not in seen:
                seen.add(v)
                deduped.append(v)
        return deduped

    def _get_cn_daily(
        self,
        asset: Asset,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> pd.DataFrame:
        s = start_date or getattr(self.cfg, "start_date", "20180101")
        e = end_date or getattr(self.cfg, "end_date", "22220101") or "22220101"
        src = (source or "em").strip().lower()
        adjust = getattr(self.cfg, "adjust", "qfq")

        if self._is_cn_etf(asset):
            if src in {"tx", "tencent"}:
                src = "em"
            variants = self._cn_etf_symbol_variants(asset)
            last_df = pd.DataFrame()
            if src in {"em", "eastmoney"}:
                fn = getattr(ak, "fund_etf_hist_em", None)
                if fn is None:
                    raise AttributeError("akshare has no attribute 'fund_etf_hist_em'")
                for sym in variants:
                    self._throttle("CN_ETF_EM", 2.8)
                    last_df = self._call_ak(fn, symbol=sym, period="daily", start_date=s, end_date=e)
                    if last_df is not None and not last_df.empty:
                        break
                df = last_df
            elif src in {"sina"}:
                fn = getattr(ak, "fund_etf_hist_sina", None)
                if fn is not None:
                    for sym in variants:
                        self._throttle("CN_ETF_SINA", 2.2)
                        last_df = self._call_ak(fn, symbol=sym, start_date=s, end_date=e)
                        if last_df is not None and not last_df.empty:
                            break
                    df = last_df
                else:
                    fn2 = getattr(ak, "fund_etf_hist_em", None)
                    if fn2 is None:
                        raise AttributeError("akshare has no attribute 'fund_etf_hist_em' or 'fund_etf_hist_sina'")
                    for sym in variants:
                        self._throttle("CN_ETF_EM", 2.8)
                        last_df = self._call_ak(fn2, symbol=sym, period="daily", start_date=s, end_date=e)
                        if last_df is not None and not last_df.empty:
                            break
                    df = last_df
            else:
                raise ValueError(f"Unsupported CN ETF source: {source}")

            if df is None or df.empty:
                fn3 = getattr(ak, "stock_zh_a_hist", None)
                if fn3 is not None:
                    self._throttle("CN_ETF_STOCK_FALLBACK", 3.2)
                    df = self._call_ak(fn3, symbol=asset.symbol, period="daily", start_date=s, end_date=e, adjust=adjust)

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

        if src in {"em", "eastmoney"}:
            self._throttle("CN_EM", 2.8)
            fn = getattr(ak, "stock_zh_a_hist", None)
            if fn is None:
                raise AttributeError("akshare has no attribute 'stock_zh_a_hist'")
            df = self._call_ak(fn, symbol=asset.symbol, period="daily", start_date=s, end_date=e, adjust=adjust)
        elif src in {"sina"}:
            self._throttle("CN_SINA", 2.2)
            fn = getattr(ak, "stock_zh_a_daily", None)
            if fn is None:
                raise AttributeError("akshare has no attribute 'stock_zh_a_daily'")
            df = self._call_ak(fn, symbol=self._cn_symbol_with_exchange(asset.symbol), start_date=s, end_date=e, adjust=adjust)
        elif src in {"tx", "tencent"}:
            self._throttle("CN_TX", 2.2)
            fn = getattr(ak, "stock_zh_a_hist_tx", None)
            if fn is None:
                raise AttributeError("akshare has no attribute 'stock_zh_a_hist_tx'")
            df = self._call_ak(fn, symbol=self._cn_symbol_with_exchange(asset.symbol), start_date=s, end_date=e, adjust=adjust)
        else:
            raise ValueError(f"Unsupported CN source: {source}")

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
