"""个股趋势产物构建（trend_engine.inputs）单元测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.selection.universe import UniverseItem
from src.trend_engine import inputs
from src.trend_engine.asset import Asset


def _item(symbol: str, tier: str, theme: str = "ai_infrastructure") -> UniverseItem:
    return UniverseItem(
        asset=Asset(symbol=symbol, name=symbol, market="CN", category=tier),
        bucket="core", bucket_label="核心", theme=theme, theme_label="AI 基础设施",
        tier=tier, tier_label=tier,
    )


class TestIsStockItem:
    def test_stock_tiers(self):
        for tier in ("leader", "high_beta", "equipment_upstream"):
            assert inputs.is_stock_item(_item("000001", tier)) is True

    def test_etf_tiers_excluded(self):
        for tier in ("theme_etf", "sub_industry_etf"):
            assert inputs.is_stock_item(_item("512480", tier)) is False

    def test_stock_items_filters_etfs(self):
        items = [_item("000001", "leader"), _item("512480", "theme_etf")]
        assert [it.asset.symbol for it in inputs.stock_items(items)] == ["000001"]


class TestBuildStockMetrics:
    def _fake_trend_df(self):
        return pd.DataFrame([
            {"symbol": "000001", "name": "平安银行", "market": "CN",
             "data_source": "cache", "date": pd.Timestamp("2026-08-03"),
             "close": 100.0, "score_trend": 85, "watch_level": "A",
             "action": "重点观察", "risk_flags": ""},
            {"symbol": "600000", "name": "浦发银行", "market": "CN",
             "data_source": "failed", "date": None,
             "close": None, "score_trend": 0, "watch_level": "",
             "action": "跳过", "risk_flags": ""},
            {"symbol": "600001", "name": "stale", "market": "CN",
             "data_source": "cache", "date": pd.Timestamp("2026-07-31"),
             "close": 50.0, "score_trend": 60, "watch_level": "B",
             "action": "观察等待", "risk_flags": ""},
        ])

    def test_build_offline_normalizes_and_persists(self, monkeypatch, tmp_path):
        monkeypatch.setattr(inputs, "compute_trends", lambda *a, **k: self._fake_trend_df())
        monkeypatch.setattr(inputs, "stock_metrics_dir", lambda: tmp_path)
        items = [_item("000001", "leader"), _item("600000", "leader"),
                 _item("600001", "leader"), _item("512480", "theme_etf")]

        df = inputs.build_stock_metrics(items, trade_date="20260803", offline=True)

        assert list(df["asset_id"]) == ["000001", "600000", "600001"]
        status = dict(zip(df["asset_id"], df["data_status"]))
        assert status["000001"] == "current"
        assert status["600001"] == "stale"
        assert status["600000"] == "missing"
        assert (tmp_path / "stock_metrics_20260803.parquet").exists()
        # schema 完整：统一字段 + selection 匹配列
        for col in ("asset_id", "symbol", "trend_score", "score_trend", "data_status",
                    "source_trade_date", "lag_days"):
            assert col in df.columns

    def test_as_of_truncation_in_compute_trends(self, tmp_path, monkeypatch):
        """as_of_date 截断：上游缓存含目标日之后数据时，评分只用到目标日。"""
        import src.common.paths as common_paths_mod
        original_root = common_paths_mod._ROOT
        try:
            common_paths_mod._ROOT = tmp_path
            (tmp_path / "config").mkdir()
            (tmp_path / "data" / "raw").mkdir(parents=True)
            (tmp_path / "data" / "processed").mkdir(parents=True)
            (tmp_path / "data" / "state").mkdir(parents=True)

            dates = pd.date_range("2026-07-01", periods=25, freq="B")
            rows = []
            for i, d in enumerate(dates):
                rows.append({"date": d, "open": 100 + i, "high": 101 + i,
                             "low": 99 + i, "close": 100 + i, "volume": 1_000_000})
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df.to_csv(tmp_path / "data" / "raw" / "CN_000001.csv", index=False, encoding="utf-8")

            from src.trend_engine.engine import compute_trends
            item = _item("000001", "leader")
            result = compute_trends([item], offline=True, as_of_date="20260731", log_level="ERROR")
            assert len(result) == 1
            assert pd.Timestamp(result.iloc[0]["date"]).strftime("%Y%m%d") == "20260731"
        finally:
            common_paths_mod._ROOT = original_root

    def test_etf_only_universe_yields_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(inputs, "compute_trends", lambda *a, **k: pd.DataFrame())
        monkeypatch.setattr(inputs, "stock_metrics_dir", lambda: tmp_path)
        df = inputs.build_stock_metrics([_item("512480", "theme_etf")],
                                        trade_date="20260803", offline=True)
        assert df.empty


class TestLoadHelpers:
    def test_latest_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr(inputs, "stock_metrics_dir", lambda: tmp_path)
        (tmp_path / "stock_metrics_20260731.parquet").write_bytes(b"x")
        (tmp_path / "stock_metrics_20260803.parquet").write_bytes(b"x")
        assert inputs.latest_stock_metrics_trade_date() == "20260803"
        assert inputs.load_stock_metrics("20260803").empty  # 非法文件 → 空
        assert inputs.load_stock_metrics("20200101").empty  # 不存在 → 空
