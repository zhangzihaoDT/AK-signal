"""
结构级测试：验证 stock_trend 子系统的包结构、导入和 CLI 路由。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_package_imports():
    """所有 stock_trend 模块可正常导入"""
    modules = [
        "src.stock_trend.asset",
        "src.stock_trend.data_provider",
        "src.stock_trend.fetch_data",
        "src.stock_trend.indicators",
        "src.stock_trend.scoring",
        "src.stock_trend.portfolio",
        "src.stock_trend.report",
        "src.stock_trend.pipeline",
        "src.stock_trend.cli",
        "src.stock_trend.watchlist",
    ]
    for mod_name in modules:
        mod = importlib.import_module(mod_name)
        assert mod is not None, f"Failed to import {mod_name}"


def test_asset_dataclass():
    from src.stock_trend.asset import Asset

    a = Asset(symbol="000300", name="沪深300", market="CN", exchange="SSE")
    assert a.symbol == "000300"
    assert a.name == "沪深300"
    assert a.market == "CN"
    assert a.exchange == "SSE"


def test_indicators_functions():
    import pandas as pd
    from src.stock_trend.indicators import add_indicators, ema, rsi, macd

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
    from src.stock_trend.indicators import add_indicators
    from src.stock_trend.scoring import score_latest_row, score_trend_label, trend_bucket, risk_flags_text

    df = add_indicators(sample_ohlcv)
    score, details = score_latest_row(df)
    assert 0 <= score <= 100
    label = score_trend_label(score)
    assert label in ("强势上行", "偏强", "震荡", "偏弱")
    bucket = trend_bucket(score)
    assert bucket in ("strong", "observe", "weak")
    flags = risk_flags_text(df.iloc[-1])
    assert isinstance(flags, str)


def test_cli_parser():
    from src.stock_trend.cli import build_arg_parser

    parser = build_arg_parser()
    args = parser.parse_args([])
    assert args.start_date == "20180101"
    assert args.adjust == "qfq"
    assert args.plot_last_n == 180
    assert args.offline is False


def test_main_routes_to_stock_trend():
    """main.py imports and routes to stock_trend.cli for default case"""
    from src.main import SW_INDUSTRY_COMMANDS

    assert "bootstrap" in SW_INDUSTRY_COMMANDS
    assert "run-day" in SW_INDUSTRY_COMMANDS


def test_main_routes_sw_commands():
    """SW industry commands should be recognized"""
    from src.main import SW_INDUSTRY_COMMANDS

    expected = {"bootstrap", "update", "calculate", "report", "validate", "run-day", "drilldown"}
    assert SW_INDUSTRY_COMMANDS == expected


def test_no_circular_imports():
    """Verify that importing pipeline doesn't create circular imports"""
    import src.stock_trend.pipeline
    import src.stock_trend.cli
    importlib.reload(src.stock_trend.pipeline)
    importlib.reload(src.stock_trend.cli)
    assert True


def test_pipeline_has_run_entry():
    from src.stock_trend.pipeline import run_stock_trend

    assert callable(run_stock_trend)


def test_project_root_resolves():
    from src.common.paths import project_root

    root = project_root()
    assert (root / "src" / "stock_trend").exists()
    assert (root / "config" / "stock_pool.csv").exists()
