"""Layer③ Lane 输入加载（three_lane 单一 join）对齐语义测试。

锁定语义：exact three_lane_{trade_date} 存在且内部 trade_date 一致 → 对齐消费；
缺失 / 读取失败 / 内部日期不一致 → lane-less（lane_trade_date=None / lane_lag_days=None），
禁止 fallback 到前后日期。
"""

from __future__ import annotations

import pandas as pd

from src.selection.cli import _load_lane_input


def _three_lane_df(trade_date: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "trade_date": pd.Timestamp(trade_date),
        "fund_code": "512480",
        "fund_name": "半导体ETF国联安",
        "lane2_reliable_360": False,
        "lane2_long_term_bottom": False,
        "lane2_target_stage": "NON_TARGET",
        "lane2_bottom_state": "UNRELIABLE",
        "lane3_transition_state": "UNRELIABLE",
        "lane3_days_since_first_exit": None,
    }])


class TestLoadLaneInput:
    def test_aligned(self, monkeypatch, tmp_path):
        from src.selection import cli as sel_cli
        etf_dir = tmp_path / "etf_signal"
        etf_dir.mkdir(parents=True)
        _three_lane_df("2026-09-02").to_parquet(etf_dir / "three_lane_20260902.parquet")
        monkeypatch.setattr(sel_cli, "outputs_dir", lambda: tmp_path)
        df, meta = _load_lane_input("20260902")
        assert meta["status"] == "aligned"
        assert meta["trade_date"] == "20260902"
        assert meta["lane_lag_days"] == 0
        assert len(df) == 1

    def test_missing_file_lane_less(self, monkeypatch, tmp_path):
        from src.selection import cli as sel_cli
        (tmp_path / "etf_signal").mkdir(parents=True)
        monkeypatch.setattr(sel_cli, "outputs_dir", lambda: tmp_path)
        df, meta = _load_lane_input("20260902")
        assert df.empty
        assert meta["status"] == "lane_less"
        assert meta["trade_date"] is None
        assert meta["lane_lag_days"] is None

    def test_mismatched_internal_date_lane_less(self, monkeypatch, tmp_path):
        from src.selection import cli as sel_cli
        etf_dir = tmp_path / "etf_signal"
        etf_dir.mkdir(parents=True)
        # 文件名为 20260902，但内部 trade_date=2026-09-03 → 禁止静默拼错日期
        _three_lane_df("2026-09-03").to_parquet(etf_dir / "three_lane_20260902.parquet")
        monkeypatch.setattr(sel_cli, "outputs_dir", lambda: tmp_path)
        df, meta = _load_lane_input("20260902")
        assert df.empty
        assert meta["status"] == "lane_less"
        assert meta["lane_lag_days"] is None
        assert "不一致" in meta["reason"]

    def test_no_fallback_to_other_dates(self, monkeypatch, tmp_path):
        """前后日期文件存在也不允许用于另一目标日（无降级 fallback）。"""
        from src.selection import cli as sel_cli
        etf_dir = tmp_path / "etf_signal"
        etf_dir.mkdir(parents=True)
        _three_lane_df("2026-09-01").to_parquet(etf_dir / "three_lane_20260901.parquet")
        monkeypatch.setattr(sel_cli, "outputs_dir", lambda: tmp_path)
        df, meta = _load_lane_input("20260902")
        assert df.empty
        assert meta["status"] == "lane_less"
