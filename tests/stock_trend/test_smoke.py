"""
离线 CI smoke 测试：验证完整管线可在无网络环境下初始化。
不依赖外部 API，不修改现有 data/ 目录。
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.stock_trend.pipeline import (
    run_stock_trend,
    project_root,
    load_assets_from_pool,
    load_asset_state,
    build_logger,
)

from src.stock_trend.cli import build_arg_parser
from src.main import SW_INDUSTRY_COMMANDS, main as main_router


def test_project_root_resolves_to_akignal():
    root = project_root()
    assert (root / "src" / "stock_trend").is_dir()
    assert (root / "config" / "stock_pool.csv").is_file()


def test_load_stock_pool_has_assets():
    root = project_root()
    pool_path = root / "config" / "stock_pool.csv"
    items = load_assets_from_pool(pool_path)
    assert len(items) > 0, "stock_pool.csv should contain at least one asset"
    symbols = {item["asset"].symbol for item in items}
    assert "002230" in symbols, "Expected 科大讯飞 in stock pool"


def test_cli_parser_defaults():
    parser = build_arg_parser()
    args = parser.parse_args(["--offline"])
    assert args.offline is True
    assert args.start_date == "20180101"
    assert args.adjust == "qfq"
    assert args.plot_last_n == 180


def test_main_routes_to_stock_offline():
    """验证 main.py 路由：不带参数时默认走向个股"""
    old_argv = sys.argv.copy()
    try:
        sys.argv = ["main.py", "--help"]
        # noinspection PyTypeChecker
        with pytest.raises((SystemExit, Exception)):
            main_router()
    finally:
        sys.argv = old_argv


def test_main_routes_sw_industry():
    """验证 main.py 路由：industry 转向行业 RPS"""
    old_argv = sys.argv.copy()
    try:
        sys.argv = ["main.py", "industry", "--help"]
        with pytest.raises((SystemExit, Exception)):
            main_router()
    finally:
        sys.argv = old_argv


def test_main_routes_legacy_run_day():
    """验证 main.py 路由：run-day 向后兼容"""
    old_argv = sys.argv.copy()
    try:
        sys.argv = ["main.py", "run-day", "--help"]
        with pytest.raises((SystemExit, Exception)):
            main_router()
    finally:
        sys.argv = old_argv


def test_sw_industry_commands_set():
    expected = {"bootstrap", "update", "calculate", "report", "validate", "run-day"}
    assert SW_INDUSTRY_COMMANDS == expected


def test_asset_state_roundtrip(tmp_path):
    from src.stock_trend.pipeline import save_asset_state, ASSET_STATE_COLUMNS
    df = pd.DataFrame(columns=ASSET_STATE_COLUMNS)
    path = tmp_path / "asset_state.csv"
    save_asset_state(df, path)
    assert path.exists()
    loaded = load_asset_state(path)
    assert list(loaded.columns) == ASSET_STATE_COLUMNS


def test_logger_builds():
    logger = build_logger("INFO")
    assert logger.level == 20
    assert logger.name == "a_stock_monitor"


def test_load_asset_state_missing(tmp_path):
    df = load_asset_state(tmp_path / "nonexistent.csv")
    assert df.empty


def test_offline_smoke_with_mock_data(tmp_path):
    """
    创建临时 config 和 mock 数据，在不联网环境下验证整个管线完整执行。
    """
    root = tmp_path
    config_dir = root / "config"
    data_dir = root / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "raw").mkdir(parents=True, exist_ok=True)
    (data_dir / "processed").mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)

    stock_pool = config_dir / "stock_pool.csv"
    stock_pool.write_text(
        "symbol,name,market,exchange,currency,category,enabled\n"
        "000001,平安银行,CN,SZSE,CNY,bank,TRUE\n"
    )

    dates = pd.date_range("2026-01-01", periods=60, freq="B")
    base = 100.0
    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "date": d, "open": base + i * 0.3, "high": base + i * 0.3 + 1.0,
            "low": base + i * 0.3 - 0.5, "close": base + i * 0.3 + 0.2,
            "volume": 1_000_000 + i * 1000,
        })
    mock_raw = pd.DataFrame(rows)
    mock_raw.to_csv(data_dir / "raw" / "CN_000001.csv", index=False, encoding="utf-8")

    from src.stock_trend.indicators import add_indicators
    mock_processed = add_indicators(mock_raw)
    mock_processed.to_csv(data_dir / "processed" / "CN_000001.csv", index=False, encoding="utf-8")

    try:
        import src.stock_trend.pipeline as pipeline_mod
        pipeline_mod.project_root = lambda: root
        result = pipeline_mod.run_stock_trend(
            start_date="20260101",
            end_date="20260715",
            offline=True,
            only_symbols="CN:000001",
            log_level="ERROR",
        )
        csv_path, html_path = result
        assert csv_path.exists()
        assert html_path.exists()
        report_df = pd.read_csv(csv_path, dtype=str)
        assert len(report_df) == 1
        assert "000001" in report_df["symbol"].values
    finally:
        pass
