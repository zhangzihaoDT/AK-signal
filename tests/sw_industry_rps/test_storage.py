from __future__ import annotations

import pandas as pd

from src.sw_industry_rps.storage import (
    safe_code,
    save_master,
    load_master,
    save_industry_raw,
    load_industry_raw,
    merge_incremental,
    save_metrics,
    load_metrics,
    save_snapshot,
    save_rotation_matrix,
)


def test_safe_code_dot():
    assert safe_code("801016.SI") == "801016_SI"


def test_safe_code_no_dot():
    assert safe_code("801016") == "801016"


def test_master_roundtrip(tmp_raw_dir, sample_master):
    save_master(sample_master, tmp_raw_dir)
    loaded = load_master(tmp_raw_dir)
    assert len(loaded) == len(sample_master)
    assert list(loaded["industry_code"]) == list(sample_master["industry_code"])


def test_industry_raw_roundtrip(tmp_raw_dir):
    df = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=3, freq="B"),
        "close": [100, 101, 102],
    })
    save_industry_raw(df, tmp_raw_dir, "801016.SI")
    loaded = load_industry_raw(tmp_raw_dir, "801016.SI")
    assert len(loaded) == 3
    assert loaded["close"].iloc[-1] == 102


def test_merge_incremental_no_cached():
    new = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=3, freq="B"),
        "close": [100, 101, 102],
    })
    result = merge_incremental(pd.DataFrame(), new)
    assert len(result) == 3


def test_merge_incremental_no_new():
    cached = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=3, freq="B"),
        "close": [100, 101, 102],
    })
    result = merge_incremental(cached, pd.DataFrame())
    assert len(result) == 3


def test_merge_incremental_dedup(tmp_raw_dir):
    existing = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=5, freq="B"),
        "close": [100, 101, 102, 103, 104],
    })
    new = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-08", periods=3, freq="B"),
        "close": [105.5, 106.5, 107.5],
    })
    result = merge_incremental(existing, new)
    assert len(result) == 8
    last_close = result[result["trade_date"] == result["trade_date"].max()]["close"].iloc[0]
    assert last_close == 107.5


def test_merge_incremental_idempotent(tmp_raw_dir):
    base = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=3, freq="B"),
        "close": [100, 101, 102],
    })
    r1 = merge_incremental(pd.DataFrame(), base)
    r2 = merge_incremental(r1, pd.DataFrame())
    assert len(r2) == 3


def test_metrics_roundtrip(tmp_processed_dir):
    df = pd.DataFrame({
        "trade_date": pd.date_range("2026-01-01", periods=3, freq="B"),
        "industry_code": ["A", "A", "A"],
        "RPS15": [50, 60, 70],
    })
    save_metrics(df, tmp_processed_dir)
    loaded = load_metrics(tmp_processed_dir)
    assert len(loaded) == 3


def test_snapshot_roundtrip(tmp_processed_dir, sample_snapshot):
    save_snapshot(sample_snapshot, tmp_processed_dir)
    loaded = pd.read_csv(tmp_processed_dir / "latest_snapshot.csv")
    assert len(loaded) == 3


def test_rotation_matrix_roundtrip(tmp_processed_dir):
    df = pd.DataFrame({
        "industry_code": ["A", "B"],
        "col1": [90, 50],
        "col2": [95, 55],
    })
    save_rotation_matrix(df, tmp_processed_dir)
    loaded = pd.read_csv(tmp_processed_dir / "rotation_matrix.csv")
    assert len(loaded) == 2
