"""Asset State 共享语义模块测试（v0.10）。

验证 technical_diagnostics / blocking_flags / data_quality_flags 三语义层：
  - blocking 与 data_quality 是正交接口（STALE_DATA 归数据质量，不混阻塞）
  - technical_text 按维度归组渲染
  - JSON 序列化 roundtrip
"""

from __future__ import annotations

from src.common.asset_state import (
    TECH_DIM_MOMENTUM, TECH_DIM_RELATIVE_STRENGTH, TECH_DIM_TREND,
    blocking_text, compose_blocking_flags, compose_data_quality_flags,
    data_quality_text, tech_diag_from_json, tech_diag_to_json, technical_text,
)


class TestBlockingFlags:
    def test_stale_data_not_blocking(self):
        """STALE_DATA 原生归 data_quality，不与 blocking 混为同一语义。"""
        b = compose_blocking_flags(reason_codes=["stale_data"], position_level="",
                                   state="WATCH", signal="", participation="tradeable")
        assert "STALE_DATA" not in b
        dq = compose_data_quality_flags(data_status="stale")
        assert dq == ["STALE_DATA"]

    def test_gate_and_liquidity(self):
        b = compose_blocking_flags(reason_codes=["below_trend_gate", "low_liquidity"],
                                   position_level="", state="WATCH", signal="",
                                   participation="tradeable", theme_confirmed=True)
        assert "BELOW_TREND_GATE" in b and "LOW_LIQUIDITY" in b

    def test_theme_not_confirmed(self):
        b = compose_blocking_flags(reason_codes=[], position_level="", state="QUALIFIED",
                                   signal="BUY", participation="tradeable", theme_confirmed=False)
        assert b == ["THEME_NOT_CONFIRMED"]

    def test_breakdown_and_signal(self):
        b = compose_blocking_flags(reason_codes=[], position_level="BREAKDOWN",
                                   state="RECOMMENDED", signal="WATCH",
                                   participation="tradeable", theme_confirmed=True)
        assert "BREAKDOWN" in b
        assert "SIGNAL_WATCH" in b

    def test_hold_maps_to_position_high(self):
        b = compose_blocking_flags(reason_codes=[], position_level="HIGH",
                                   state="RECOMMENDED", signal="HOLD",
                                   participation="tradeable", theme_confirmed=True)
        assert "POSITION_HIGH" in b

    def test_monitor_only(self):
        b = compose_blocking_flags(reason_codes=["monitor_only"], participation="monitor_only")
        assert "MONITOR_ONLY" in b

    def test_blocking_text_chinese(self):
        b = ["BELOW_TREND_GATE", "LOW_LIQUIDITY"]
        assert blocking_text(b) == "未达趋势门 · 流动性不足"
        assert blocking_text([]) == "—"


class TestDataQualityFlags:
    def test_stale_missing_insufficient(self):
        assert compose_data_quality_flags(data_status="stale") == ["STALE_DATA"]
        assert compose_data_quality_flags(data_status="missing") == ["MISSING_DATA"]
        assert compose_data_quality_flags(selection_status="unavailable") == ["MISSING_DATA"]
        assert compose_data_quality_flags(insufficient_history=True) == ["INSUFFICIENT_HISTORY"]

    def test_etf_flags_normalized(self):
        dq = compose_data_quality_flags(etf_flags=["corporate_action"])
        assert dq == ["CORPORATE_ACTION"]

    def test_data_quality_text(self):
        assert data_quality_text(["STALE_DATA", "INSUFFICIENT_HISTORY"]) == "数据滞后 · 历史不足"


class TestTechnicalText:
    def test_grouped_issues_only(self):
        diag = {
            TECH_DIM_TREND: {"level": "WEAK", "flags": ["below_ma20", "below_ma60", "below_ma120"]},
            TECH_DIM_MOMENTUM: {"level": "WEAK", "flags": ["macd_weak", "rsi_weak"]},
            TECH_DIM_RELATIVE_STRENGTH: {"level": "WEAK", "flags": ["rs_negative"]},
        }
        assert technical_text(diag) == "趋势弱：MA20↓·MA60↓·MA120↓ · 动量弱：MACD弱·RSI弱 · 相对弱：RS负"

    def test_strong_dims_hidden(self):
        dims = (TECH_DIM_TREND, TECH_DIM_MOMENTUM, TECH_DIM_RELATIVE_STRENGTH)
        diag = {d: {"level": "STRONG", "flags": []} for d in dims}
        assert technical_text(diag) == "—"

    def test_unknown_shows_data_insufficient(self):
        diag = {
            TECH_DIM_TREND: {"level": "UNKNOWN", "flags": []},
            TECH_DIM_MOMENTUM: {"level": "WEAK", "flags": []},
            TECH_DIM_RELATIVE_STRENGTH: {"level": "STRONG", "flags": []},
        }
        assert technical_text(diag) == "趋势：数据不足 · 动量弱"


class TestTechDiagSerialization:
    def test_roundtrip(self):
        diag = {TECH_DIM_TREND: {"level": "WEAK", "flags": ["below_ma20"]}}
        assert tech_diag_from_json(tech_diag_to_json(diag))[TECH_DIM_TREND] == {"level": "WEAK", "flags": ["below_ma20"]}

    def test_empty_and_bad(self):
        assert tech_diag_from_json("") == {}
        assert tech_diag_from_json(None) == {}
        assert tech_diag_from_json("not-json") == {}
        assert tech_diag_from_json("nan") == {}
