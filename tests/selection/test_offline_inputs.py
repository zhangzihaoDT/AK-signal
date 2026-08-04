"""Layer③ 离线消费逻辑测试：输入加载降级 + 缺失个股标记。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.selection import selection as sel
from src.selection.cli import _ensure_stock_trend_df, _load_stock_metrics_input
from src.selection.universe import UniverseItem
from src.trend_engine.asset import Asset


def _item(symbol: str, tier: str = "leader", theme: str = "ai_infrastructure") -> UniverseItem:
    return UniverseItem(
        asset=Asset(symbol=symbol, name=symbol, market="CN", category=tier),
        bucket="core", bucket_label="核心", theme=theme, theme_label="AI 基础设施",
        tier=tier, tier_label=tier,
    )


class TestEnsureStockTrendDf:
    def test_empty_metrics_all_missing(self):
        stock = [_item("000001"), _item("600000")]
        df = _ensure_stock_trend_df(stock, pd.DataFrame(), "20260803")
        assert set(df["symbol"]) == {"000001", "600000"}
        assert (df["data_status"] == "missing").all()

    def test_metrics_covers_partial(self):
        stock = [_item("000001"), _item("600000")]
        metrics = pd.DataFrame([{
            "symbol": "000001", "data_status": "current", "score_trend": 85.0,
            "watch_level": "A", "action": "重点观察", "risk_flags": "",
        }])
        df = _ensure_stock_trend_df(stock, metrics, "20260803")
        assert set(df["symbol"]) == {"000001", "600000"}
        status = dict(zip(df["symbol"], df["data_status"]))
        assert status["000001"] == "current"
        assert status["600000"] == "missing"


class TestLoadStockMetricsInput:
    def test_missing_input_returns_empty(self, monkeypatch, tmp_path):
        from src.trend_engine import inputs as trend_inputs
        monkeypatch.setattr(trend_inputs, "selection_inputs_dir", lambda: tmp_path)
        df, td = _load_stock_metrics_input("20260803", use_exact=True, requested_td="20260803")
        assert df.empty and td is None

    def test_exact_mode_only_matching_date(self, monkeypatch, tmp_path):
        from src.trend_engine import inputs as trend_inputs
        monkeypatch.setattr(trend_inputs, "selection_inputs_dir", lambda: tmp_path)
        path = tmp_path / "stock_metrics_20260731.parquet"
        pd.DataFrame([{"symbol": "000001"}]).to_parquet(path)
        df, td = _load_stock_metrics_input("20260803", use_exact=True, requested_td="20260803")
        assert df.empty and td is None


class TestMissingAssetInWatchlist:
    def test_missing_trend_marks_unavailable(self):
        items = [_item("000001", theme="ai_infrastructure")]
        trend = pd.DataFrame([{
            "symbol": "000001", "data_status": "missing", "score_trend": 0,
            "watch_level": "", "action": "", "risk_flags": "",
        }])
        leaders, _, _ = sel.select_stock_watchlist(
            items, "ai_infrastructure", trend, theme_confirmed=True)
        c = leaders[0]
        assert c.selection_status == "unavailable"
        assert c.recommended is False
        assert c.state == sel.STOCK_STATE_WATCH
        assert c.reason == "stock_trend_input_missing"

    def test_current_trend_qualified(self):
        items = [_item("000001", theme="ai_infrastructure")]
        trend = pd.DataFrame([{
            "symbol": "000001", "data_status": "current", "score_trend": 85.0,
            "watch_level": "A", "action": "重点观察", "risk_flags": "",
        }])
        leaders, _, _ = sel.select_stock_watchlist(
            items, "ai_infrastructure", trend, theme_confirmed=True)
        c = leaders[0]
        assert c.selection_status == "available"
        assert c.state == sel.STOCK_STATE_RECOMMENDED
        assert c.recommended is True
