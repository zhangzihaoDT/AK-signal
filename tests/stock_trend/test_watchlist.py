from __future__ import annotations

import pandas as pd

from src.stock_trend.watchlist import (
    WATCHLIST_COLUMNS,
    load_watchlist,
    update_watchlist,
    list_recent_report_paths,
    consecutive_c_days,
)


def test_watchlist_columns():
    assert "ts_code" in WATCHLIST_COLUMNS
    assert "name" in WATCHLIST_COLUMNS


def test_load_watchlist_missing(tmp_path):
    path = tmp_path / "watchlist.csv"
    df = load_watchlist(path)
    assert df.empty
    assert list(df.columns) == WATCHLIST_COLUMNS


def test_load_watchlist_empty(tmp_path):
    path = tmp_path / "watchlist.csv"
    path.write_text("")
    df = load_watchlist(path)
    assert df.empty


def test_list_recent_report_paths_empty(tmp_path):
    paths = list_recent_report_paths(tmp_path)
    assert paths == []


def test_consecutive_c_days_no_reports(tmp_path):
    assert consecutive_c_days("000001", tmp_path) == 0


def test_update_watchlist_basic(tmp_path):
    from datetime import date
    summary = pd.DataFrame({
        "name": ["测试A"],
        "symbol": ["000001"],
        "market": ["CN"],
        "watch_level": ["A"],
        "score": [80],
    })
    reports_dir = tmp_path / "reports"
    watchlist_path = tmp_path / "watchlist.csv"
    result = update_watchlist(
        report_date=date(2026, 7, 15),
        summary_df=summary,
        reports_dir=reports_dir,
        watchlist_path=watchlist_path,
    )
    assert not result.empty
    assert "000001" in result["ts_code"].values
