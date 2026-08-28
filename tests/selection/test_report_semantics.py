"""Layer③ 报告 v0.10 语义列渲染测试。"""

from __future__ import annotations

from src.selection import report


class TestSemanticCells:
    def test_technical_cell_grouped(self):
        a = {"technical_diagnostics": {
            "trend": {"level": "WEAK", "flags": ["below_ma20", "below_ma60"]},
            "momentum": {"level": "WEAK", "flags": ["macd_weak"]},
            "relative_strength": {"level": "STRONG", "flags": []},
        }}
        assert report._technical_cell(a) == "趋势弱：MA20↓·MA60↓ · 动量弱：MACD弱"

    def test_technical_cell_fallback_to_legacy_risk_flags(self):
        """旧 JSON（无 technical_diagnostics）回退 risk_flags 短码，不破坏兼容。"""
        a = {"risk_flags": ["跌破MA20", "MACD转弱"]}
        assert report._technical_cell(a) == "MA20↓ · MACD弱"

    def test_blocking_cell(self):
        assert report._blocking_cell({"blocking_flags": ["BELOW_TREND_GATE", "LOW_LIQUIDITY"]}) \
            == "未达趋势门 · 流动性不足"
        assert report._blocking_cell({"blocking_flags": []}) == "—"
        assert report._blocking_cell({}) == "—"

    def test_data_quality_cell(self):
        assert report._data_quality_cell({"data_quality_flags": ["STALE_DATA"]}) == "数据滞后"
        # 旧 JSON 回退 data_status
        assert report._data_quality_cell({"data_status": "stale"}) == "数据滞后"
        assert report._data_quality_cell({"data_status": "missing"}) == "数据缺失"

    def test_etf_trend_status_cn(self):
        assert report._etf_trend_status_cn({"trend_status": "BUY_CANDIDATE"}) == "趋势达标"
        assert report._etf_trend_status_cn({"trend_status": "BELOW_TREND_GATE"}) == "未达趋势门"
        assert report._etf_trend_status_cn({"trend_status": ""}) == "—"

    def test_trade_state_cn(self):
        assert report._trade_state_cn({"signal": "STRONG_BUY"}) == "买入"
        assert report._trade_state_cn({"signal": "WAIT"}) == "等待"
        assert report._trade_state_cn({"signal": ""}) == "—"
