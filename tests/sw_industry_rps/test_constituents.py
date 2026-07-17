from __future__ import annotations

import pandas as pd
import pytest

from src.sw_industry_rps.constituents import _clean_legulegu_table


class TestCleanLeguleguTable:
    def test_clean_normal_table(self):
        raw = pd.DataFrame({
            0: [1, 2],
            1: ["601118.SH", "300189.SZ"],
            2: ["海南橡胶", "神农种业"],
            3: ["2010-01-28", "2011-01-07"],
            4: ["种植业", "种植业"],
            5: [None, None],
            6: [4.81, 4.31],
            7: ["—", 41.19],
            8: ["—", 38.95],
            9: [2.15, 6.41],
            10: ["-1.32%", "-0.94%"],
            11: ["—", "—"],
            12: [205.84, 44.13],
            13: ["-2.83%", "0.70%"],
            14: ["-0.62%", "4.61%"],
            15: ["-18.61%", "-28.29%"],
            16: ["-22.31%", "48.60%"],
            17: ["-20.75%", "-27.92%"],
        })
        df = _clean_legulegu_table(raw)
        assert not df.empty
        assert "股票代码" in df.columns
        assert "股票简称" in df.columns
        assert "市值" in df.columns
        assert df["股票代码"].iloc[0] == "601118.SH"
        assert df["股票简称"].iloc[1] == "神农种业"

    def test_handles_fewer_columns(self):
        raw = pd.DataFrame({
            0: [1],
            1: ["000001.SZ"],
            2: ["平安银行"],
        })
        df = _clean_legulegu_table(raw)
        assert not df.empty

    def test_empty_table(self):
        raw = pd.DataFrame({i: [] for i in range(18)})
        df = _clean_legulegu_table(raw)
        assert df.empty or len(df) == 0

    def test_drops_empty_stock_codes(self):
        raw = pd.DataFrame({
            0: [1, 2],
            1: ["601118.SH", None],
            2: ["海南橡胶", None],
            3: ["2010-01-28", None],
            4: ["种植业", None],
        })
        df = _clean_legulegu_table(raw)
        assert len(df) == 1
