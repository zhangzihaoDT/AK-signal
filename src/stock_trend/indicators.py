from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    dif = ema(close, span=fast) - ema(close, span=slow)
    dea = ema(dif, span=signal)
    hist = dif - dea
    return dif, dea, hist


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ma20"] = out["close"].rolling(20, min_periods=20).mean()
    out["ma60"] = out["close"].rolling(60, min_periods=60).mean()
    out["ma120"] = out["close"].rolling(120, min_periods=120).mean()
    out["rsi14"] = rsi(out["close"], window=14)
    out["vol_ma20"] = out["volume"].rolling(20, min_periods=20).mean()
    out["volume_ratio"] = out["volume"] / out["vol_ma20"]
    out["return_20d"] = out["close"].pct_change(20)
    out["rolling_high_120"] = out["high"].rolling(120, min_periods=120).max()
    out["drawdown_from_high"] = 1 - (out["close"] / out["rolling_high_120"])
    out["price_near_ma20"] = (out["close"] - out["ma20"]).abs() / out["ma20"] <= 0.02

    dif, dea, hist = macd(out["close"])
    out["macd_dif"] = dif
    out["macd_dea"] = dea
    out["macd_hist"] = hist
    out["ma20_slope"] = out["ma20"].diff(5)
    out["ma60_slope"] = out["ma60"].diff(5)
    return out
