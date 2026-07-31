from __future__ import annotations

import pandas as pd

from src.trend_engine.indicators import ema, rsi, macd, add_indicators


def test_ema_basic():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ema(s, span=3)
    assert len(result) == 5
    assert pd.notna(result.iloc[-1])


def test_ema_short_span():
    s = pd.Series(range(100, 200, 1), dtype=float)
    result = ema(s, span=5)
    assert len(result) == 100
    assert result.iloc[-1] > 0


def test_rsi_all_up():
    close = pd.Series(range(100, 200, 1), dtype=float)
    result = rsi(close, window=14)
    assert len(result) == 100
    assert result.iloc[-1] > 50


def test_rsi_all_down():
    close = pd.Series(range(200, 100, -1), dtype=float)
    result = rsi(close, window=14)
    assert len(result) == 100
    assert result.iloc[-1] < 50


def test_rsi_flat_data_returns_nan():
    close = pd.Series([100.0] * 30, dtype=float)
    result = rsi(close, window=14)
    assert pd.isna(result.iloc[-1])


def test_macd_components():
    close = pd.Series(range(100, 200, 1), dtype=float)
    dif, dea, hist = macd(close, fast=12, slow=26, signal=9)
    assert len(dif) == len(close)
    assert len(dea) == len(close)
    assert len(hist) == len(close)
    assert (hist == dif - dea).all()


def test_macd_up_trend():
    close = pd.Series(range(100, 200, 1), dtype=float)
    dif, dea, hist = macd(close)
    assert hist.iloc[-1] > 0


def test_add_indicators_columns(sample_ohlcv):
    df = add_indicators(sample_ohlcv)
    expected = {
        "ma20", "ma60", "ma120", "rsi14", "vol_ma20",
        "volume_ratio", "return_20d", "drawdown_from_high",
        "price_near_ma20", "macd_dif", "macd_dea", "macd_hist",
        "ma20_slope", "ma60_slope",
    }
    for col in expected:
        assert col in df.columns, f"Missing indicator column: {col}"


def test_add_indicators_preserves_original(sample_ohlcv):
    df = sample_ohlcv.copy()
    original_cols = list(df.columns)
    result = add_indicators(df)
    for c in original_cols:
        assert c in result.columns
    assert len(result) == len(df)


def test_add_indicators_ma_values(sample_ohlcv):
    df = add_indicators(sample_ohlcv)
    assert df["ma20"].iloc[19] == df["close"].iloc[:20].mean()


def test_volume_ratio_computation(sample_ohlcv):
    df = add_indicators(sample_ohlcv)
    vol = df["volume"]
    vol_ma = df["vol_ma20"]
    expected = vol / vol_ma
    pd.testing.assert_series_equal(df["volume_ratio"], expected, check_dtype=False, check_names=False)


def test_drawdown_non_negative(sample_ohlcv):
    df = add_indicators(sample_ohlcv)
    assert (df["drawdown_from_high"].dropna() >= 0).all()


def test_return_20d_on_flat_data():
    flat = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30, freq="B"),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 1_000_000,
    })
    df = add_indicators(flat)
    assert df["return_20d"].iloc[-1] == 0.0
