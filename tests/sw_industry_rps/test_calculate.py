from __future__ import annotations

import argparse

import pandas as pd
import pytest

from src.sw_industry_rps import storage
from src.sw_industry_rps.cli import cmd_calculate


@pytest.fixture
def tiny_universe(tmp_path):
    """3 个行业的极小宇宙 + 路径 mock。"""
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    master = pd.DataFrame({
        "industry_code": ["801016.SI", "801015.SI", "801011.SI"],
        "industry_name": ["种植业", "渔业", "林业Ⅱ"],
        "parent_industry": ["农林牧渔", "农林牧渔", "农林牧渔"],
        "constituent_count": [20, 6, 4],
    })
    storage.save_master(master, raw_dir)
    codes = master["industry_code"].tolist()

    # 每个行业 20 个交易日确认数据，到 07-30
    dates = pd.bdate_range("2026-07-01", periods=20)  # 截止 2026-07-28
    # 手工补 07-29 / 07-30，保证到 07-30
    for code, base in zip(codes, [1000, 500, 250]):
        df = pd.DataFrame({
            "trade_date": dates,
            "close": [base + i * 2 for i in range(len(dates))],
            "source": "swsresearch",
        })
        for d, extra in [("2026-07-29", 1.0), ("2026-07-30", 1.0)]:
            row = pd.DataFrame({
                "trade_date": pd.to_datetime([d]),
                "close": [df["close"].iloc[-1] * (1 + extra * 0.01)],
                "source": ["swsresearch"],
            })
            df = storage.merge_incremental(df, row)
        storage.save_industry_raw(df, raw_dir, code)
        storage.save_active_snapshot(codes, raw_dir)

    def _apply(monkeypatch):
        monkeypatch.setattr("src.sw_industry_rps.cli.sw_industry_raw_dir", lambda: raw_dir)
        monkeypatch.setattr("src.sw_industry_rps.cli.sw_industry_processed_dir", lambda: processed_dir)
        monkeypatch.setattr("src.sw_industry_rps.cli.load_config", lambda: {})

    return codes, raw_dir, processed_dir, _apply


def _append_provisional(raw_dir, codes, target="2026-07-31"):
    """为部分行业写入 provisional 07-31 行。"""
    for i, code in enumerate(codes[:2]):
        df = storage.load_industry_raw(raw_dir, code)
        prev_close = df["close"].iloc[-1]
        row = pd.DataFrame({
            "trade_date": pd.to_datetime([target]),
            "close": [prev_close * 1.05],
            "source": ["realtime"],
            "data_status": ["provisional"],
        })
        storage.save_industry_raw(storage.merge_incremental(df, row), raw_dir, code)


def _append_confirmed(raw_dir, codes, target="2026-07-31"):
    """为全部行业写入 confirmed 07-31 行。"""
    for code in codes:
        df = storage.load_industry_raw(raw_dir, code)
        prev_close = df["close"].iloc[-1]
        row = pd.DataFrame({
            "trade_date": pd.to_datetime([target]),
            "close": [prev_close * 1.03],
            "source": ["swsresearch"],
        })
        storage.save_industry_raw(storage.merge_incremental(df, row), raw_dir, code)


def test_calculate_refreshes_prior_max_partition(monkeypatch, tiny_universe):
    codes, raw_dir, processed_dir, apply = tiny_universe
    apply(monkeypatch)

    # v1: 只有 07-30 确认数据
    cmd_calculate(argparse.Namespace(full=False, date=None, log_level="INFO"))
    m1 = storage.load_metrics(processed_dir)
    assert m1["trade_date"].max() == pd.Timestamp("2026-07-30")
    assert m1[m1["trade_date"] == "2026-07-30"]["industry_code"].nunique() == 3

    # v2: 2/3 行业追加 07-31 provisional
    _append_provisional(raw_dir, codes)
    cmd_calculate(argparse.Namespace(full=False, date=None, log_level="INFO"))
    m2 = storage.load_metrics(processed_dir)
    assert m2["trade_date"].max() == pd.Timestamp("2026-07-31")
    assert m2[m2["trade_date"] == "2026-07-31"]["industry_code"].nunique() == 2

    # v3: 全部行业 07-31 转 confirmed → prior_max 分区必须重算到 3 个
    _append_confirmed(raw_dir, codes)
    cmd_calculate(argparse.Namespace(full=False, date=None, log_level="INFO"))
    m3 = storage.load_metrics(processed_dir)
    assert m3["trade_date"].max() == pd.Timestamp("2026-07-31")
    assert m3[m3["trade_date"] == "2026-07-31"]["industry_code"].nunique() == 3
