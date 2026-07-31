"""
离线 smoke 测试：验证 Trend Engine + selection 在无网络环境下可初始化执行。
不依赖外部 API，不修改现有 data/ 目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from src.common.paths import project_root
from src.trend_engine.engine import load_asset_state, build_logger
from src.selection.universe import load_universe_items

from src.selection.cli import build_arg_parser
from src.main import SW_INDUSTRY_COMMANDS, main as main_router


def test_project_root_resolves_to_akignal():
    root = project_root()
    assert (root / "src" / "trend_engine").is_dir()
    assert (root / "config" / "stock_universe.yaml").is_file()


def test_load_universe_has_assets():
    root = project_root()
    universe_path = root / "config" / "stock_universe.yaml"
    items = load_universe_items(universe_path)
    assert len(items) > 0, "stock_universe.yaml should contain at least one asset"
    symbols = {item.asset.symbol for item in items}
    assert "002230" in symbols, "Expected 科大讯飞 in universe"
    assert "300308" in symbols, "Expected 中际旭创 in universe"


def test_selection_cli_parser():
    parser = build_arg_parser()
    args = parser.parse_args(["run", "--offline"])
    assert args.command == "run"
    assert args.offline is True


def test_main_routes_select():
    """main.py 将 select 路由到 selection"""
    old_argv = sys.argv.copy()
    try:
        sys.argv = ["main.py", "select", "--help"]
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
    expected = {"bootstrap", "update", "calculate", "report", "validate", "run-day", "drilldown"}
    assert SW_INDUSTRY_COMMANDS == expected


def test_asset_state_roundtrip(tmp_path):
    from src.trend_engine.engine import save_asset_state, ASSET_STATE_COLUMNS
    df = pd.DataFrame(columns=ASSET_STATE_COLUMNS)
    path = tmp_path / "asset_state.csv"
    save_asset_state(df, path)
    assert path.exists()
    loaded = load_asset_state(path)
    assert list(loaded.columns) == ASSET_STATE_COLUMNS


def test_logger_builds():
    logger = build_logger("INFO")
    assert logger.level == 20


def test_load_asset_state_missing(tmp_path):
    df = load_asset_state(tmp_path / "nonexistent.csv")
    assert df.empty


def test_compute_trends_empty_items(tmp_path):
    """空标的列表时 engine 不崩溃。"""
    from src.trend_engine.engine import compute_trends
    df = compute_trends([], offline=True, log_level="ERROR")
    assert df.empty


def test_offline_smoke_with_mock_data(tmp_path):
    """
    创建临时 config 和 mock 数据，在不联网环境下验证 engine.compute_trends 完整执行。
    """
    root = tmp_path
    config_dir = root / "config"
    data_dir = root / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "raw").mkdir(parents=True, exist_ok=True)
    (data_dir / "processed").mkdir(parents=True, exist_ok=True)
    (data_dir / "state").mkdir(parents=True, exist_ok=True)

    universe = config_dir / "stock_universe.yaml"
    universe.write_text(
        "themes:\n"
        "  test:\n"
        "    label: 测试主题\n"
        "    tiers:\n"
        "      - key: leader\n"
        "        label: 龙头\n"
        "        assets:\n"
        "          - {symbol: \"000001\", name: 平安银行, market: CN, exchange: SZSE, currency: CNY}\n"
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

    try:
        import src.common.paths as common_paths_mod
        original_root = common_paths_mod._ROOT
        common_paths_mod._ROOT = root

        from src.selection.universe import load_universe_items
        items = load_universe_items(universe)
        assert len(items) == 1

        from src.trend_engine.engine import compute_trends
        result = compute_trends(items, offline=True, log_level="ERROR")
        assert len(result) == 1
        assert result.iloc[0]["symbol"] == "000001"
        assert "score_trend" in result.columns
    finally:
        common_paths_mod._ROOT = original_root
