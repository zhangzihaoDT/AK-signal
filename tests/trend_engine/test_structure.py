"""
结构级测试：验证趋势计算引擎及其观察池配置依赖。
"""

from __future__ import annotations

import importlib


def test_package_imports():
    """趋势引擎和配置加载模块可正常导入。"""
    modules = [
        "src.trend_engine.asset",
        "src.trend_engine.data_provider",
        "src.trend_engine.fetch_data",
        "src.trend_engine.indicators",
        "src.trend_engine.scoring",
        "src.trend_engine.engine",
        "src.selection.universe",
    ]
    for mod_name in modules:
        mod = importlib.import_module(mod_name)
        assert mod is not None, f"Failed to import {mod_name}"


def test_asset_dataclass():
    from src.trend_engine.asset import Asset

    a = Asset(symbol="000300", name="沪深300", market="CN", exchange="SSE")
    assert a.symbol == "000300"
    assert a.name == "沪深300"
    assert a.market == "CN"
    assert a.exchange == "SSE"


def test_indicators_functions():
    import pandas as pd
    from src.trend_engine.indicators import add_indicators, ema, rsi, macd

    dates = pd.date_range("2026-01-01", periods=100, freq="B")
    close = pd.Series(range(100, 200), index=dates, dtype=float)
    ema_result = ema(close, span=20)
    assert len(ema_result) == 100

    rsi_result = rsi(close, window=14)
    assert len(rsi_result) == 100

    dif, dea, hist = macd(close)
    assert len(dif) == 100

    df = pd.DataFrame({
        "date": dates,
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "close": close,
        "volume": 1_000_000,
    })
    result = add_indicators(df)
    for col in ["ma20", "ma60", "rsi14", "volume_ratio", "macd_dif"]:
        assert col in result.columns, f"Missing column: {col}"


def test_scoring_functions(sample_ohlcv):
    from src.trend_engine.indicators import add_indicators
    from src.trend_engine.scoring import score_latest_row, score_trend_label, trend_bucket, risk_flags_text

    df = add_indicators(sample_ohlcv)
    score, details = score_latest_row(df)
    assert 0 <= score <= 100
    label = score_trend_label(score)
    assert label in ("强势上行", "偏强", "震荡", "偏弱")
    bucket = trend_bucket(score)
    assert bucket in ("strong", "observe", "weak")
    flags = risk_flags_text(df.iloc[-1])
    assert isinstance(flags, str)


def test_no_circular_imports():
    """引擎与观察池配置之间无循环导入。"""
    import src.trend_engine.engine
    import src.selection.universe
    importlib.reload(src.trend_engine.engine)
    importlib.reload(src.selection.universe)
    assert True


def test_engine_has_compute_trends():
    from src.trend_engine.engine import compute_trends

    assert callable(compute_trends)


def test_project_root_resolves():
    from src.common.paths import project_root

    root = project_root()
    assert (root / "src" / "trend_engine").exists()
    assert (root / "src" / "selection" / "universe.py").exists()
    assert (root / "config" / "selection_universe.yaml").exists()
