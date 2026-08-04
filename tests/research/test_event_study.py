"""Event Study（v0.5.1）单元测试。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.research.event_study import events, returns, study


def _signals_df():
    rows = []
    # Layer2 行业：801161 观察 → 强势 → 中性（一次 entry、一次 exit）
    for i, (d, st) in enumerate([
        ("20260720", "中性"), ("20260721", "观察"), ("20260722", "观察"),
        ("20260723", "强势"), ("20260724", "中性"),
    ]):
        rows.append({"trade_date": d, "entity_type": "industry", "entity_code": "801161.SI",
                     "theme": "high_cashflow", "layer": "2", "rps15": 85.0,
                     "confirmation_status": st})
    # Layer3 股票：600900 进入 RECOMMENDED（entry）
    rows.append({"trade_date": "20260722", "entity_type": "stock", "entity_code": "600900",
                 "theme": "high_cashflow", "layer": "3", "trend_score": 90.0,
                 "selection_status": "RECOMMENDED"})
    return pd.DataFrame(rows)


class TestExtractEvents:
    def test_entry_and_exit(self):
        df = events.extract_events(_signals_df(), layers="2")
        sub = df[df["entity_code"] == "801161.SI"]
        entries = sub[sub["event_type"] == "entry"]
        exits = sub[sub["event_type"] == "exit"]
        assert entries["trade_date"].tolist() == ["20260721"]   # 首次进入 观察/强势
        assert exits["trade_date"].tolist() == ["20260724"]     # 退出

    def test_stock_entry(self):
        df = events.extract_events(_signals_df(), layers="3")
        assert len(df) == 1
        assert df.iloc[0]["event_type"] == "entry"

    def test_range_filter(self):
        df = events.extract_events(_signals_df(), layers="2",
                                   start_date="20260722", end_date="20260723")
        assert set(df["trade_date"]) == {"20260722"}


class TestPriceBook:
    def _book(self):
        ind = pd.DataFrame({
            "801161.SI": [100.0, 101.0, 103.0, 104.0, 105.0, 108.0],
            "801001.SI": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        }, index=pd.to_datetime(["2026-07-20", "2026-07-21", "2026-07-22",
                                 "2026-07-23", "2026-07-24", "2026-07-27"]))
        return returns.PriceBook({"industry": ind})

    def test_forward_returns(self):
        book = self._book()
        r = book.forward_returns("industry", "801161.SI", "20260720", (1, 5))
        assert abs(r[1] - (101.0 / 100.0 - 1)) < 1e-9
        assert abs(r[5] - (108.0 / 100.0 - 1)) < 1e-9

    def test_missing_date_returns_none(self):
        book = self._book()
        r = book.forward_returns("industry", "801161.SI", "19990101", (1,))
        assert r[1] is None

    def test_benchmark_median(self):
        book = self._book()
        b = book.benchmark_forward("industry", "20260720", (1,))
        # 801161:+1%, 801001:0% → 中位 0.005
        assert abs(b[1] - 0.005) < 1e-9

    def test_excursions(self):
        book = self._book()
        mfe, mae = book.excursions("industry", "801161.SI", "20260720", (5,))[5]
        assert abs(mfe - (108.0 / 100.0 - 1)) < 1e-9
        assert abs(mae - (100.0 / 100.0 - 1)) < 1e-9


class TestAggregate:
    def _events_df(self):
        return pd.DataFrame([
            {"entity_type": "etf", "theme": "ai", "layer": "1", "event_type": "entry",
             "trade_date": "20260720", "entity_code": "512480",
             "ret_5": 0.05, "bench_5": 0.01, "excess_5": 0.04, "mfe_5": 0.06, "mae_5": -0.01,
             "ret_20": 0.10, "bench_20": 0.02, "excess_20": 0.08, "mfe_20": 0.12, "mae_20": -0.02},
            {"entity_type": "etf", "theme": "ai", "layer": "1", "event_type": "entry",
             "trade_date": "20260803", "entity_code": "512480",
             "ret_5": -0.01, "bench_5": 0.01, "excess_5": -0.02, "mfe_5": 0.01, "mae_5": -0.03,
             "ret_20": None, "bench_20": None, "excess_20": None, "mfe_20": None, "mae_20": None},
        ])

    def test_aggregate_stats(self):
        out = study.aggregate(self._events_df(), (5, 20))
        row5 = out[(out["horizon"] == 5) & (out["event_type"] == "entry")].iloc[0]
        assert row5["ret"]["n"] == 2
        assert abs(row5["ret"]["mean"] - 0.02) < 1e-6
        assert row5["ret"]["win_rate"] == 0.5
        assert abs(row5["excess"]["mean"] - 0.01) < 1e-6
        # 20 日仅一个有效
        row20 = out[(out["horizon"] == 20) & (out["event_type"] == "entry")].iloc[0]
        assert row20["ret"]["n"] == 1

    def test_non_overlap(self):
        g = pd.DataFrame({"entity_code": ["512480", "512480"],
                          "trade_date": ["20260720", "20260803"]})
        # 两事件相隔 10 个交易日：h=5 → 不重叠（各计 1）；h=40 → 重叠（只计 1）
        assert study.count_non_overlap(g, 5) == 2
        assert study.count_non_overlap(g, 40) == 1
