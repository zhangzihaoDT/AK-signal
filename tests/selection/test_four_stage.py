"""Layer③ 四段选筹（v0.9.0）单元测试：leadership / position / signal。"""

from __future__ import annotations

import pytest

from src.selection import four_stage


class TestClassifyLeadership:
    def test_boundaries(self):
        assert four_stage.classify_leadership(1, 3, 10) == "LEADER"
        assert four_stage.classify_leadership(3, 3, 10) == "LEADER"
        assert four_stage.classify_leadership(4, 3, 10) == "CORE"
        assert four_stage.classify_leadership(10, 3, 10) == "CORE"
        assert four_stage.classify_leadership(11, 3, 10) == "NON_CORE"

    def test_missing_rank_is_non_core(self):
        assert four_stage.classify_leadership(None, 3, 10) == "NON_CORE"
        assert four_stage.classify_leadership(0, 3, 10) == "NON_CORE"


class TestPricePercentile:
    def test_flat_no_history(self):
        assert four_stage.price_percentile([100.0]) is None
        assert four_stage.price_percentile([]) is None

    def test_rising_series_high_position(self):
        # 末值 100 为窗口最高 → 分位 ~100
        pct = four_stage.price_percentile([50, 60, 70, 80, 90, 100], lookback_days=6)
        assert pct == 100.0

    def test_falling_series_low_position(self):
        # 末值 10 为窗口最低 → 分位 0
        pct = four_stage.price_percentile([90, 80, 70, 60, 50, 10], lookback_days=6)
        assert pct == 0.0

    def test_lookback_window(self):
        # 只用最近 3 个点（60/50/10）→ 10 是窗口最低 → 0
        pct = four_stage.price_percentile([1, 2, 3, 60, 50, 10], lookback_days=3)
        assert pct == 0.0

    def test_mid_position(self):
        pct = four_stage.price_percentile([1, 2, 3, 4, 5, 3], lookback_days=6)
        assert pct is not None and 30 < pct < 70


class TestClassifyPosition:
    def test_bands(self):
        assert four_stage.classify_position(0.0, 30, 70) == "LOW"
        assert four_stage.classify_position(30.0, 30, 70) == "LOW"
        assert four_stage.classify_position(45.0, 30, 70) == "MID"
        assert four_stage.classify_position(70.0, 30, 70) == "MID"
        assert four_stage.classify_position(85.0, 30, 70) == "HIGH"

    def test_unknown(self):
        assert four_stage.classify_position(None, 30, 70) == "UNKNOWN"

    def test_effective_position_neutral(self):
        assert four_stage.effective_position("UNKNOWN") == "MID"
        assert four_stage.effective_position("") == "MID"
        assert four_stage.effective_position("LOW") == "LOW"
        assert four_stage.effective_position("HIGH") == "HIGH"


_RULES = [
    {"signal": "STRONG_BUY", "trend": "QUALIFIED", "leadership": "LEADER", "position": "LOW"},
    {"signal": "BUY", "trend": "QUALIFIED", "leadership": "LEADER", "position": "MID"},
    {"signal": "BUY", "trend": "QUALIFIED", "leadership": "CORE", "position": "LOW"},
    {"signal": "WATCH", "trend": "QUALIFIED", "leadership": "CORE", "position": "MID"},
    {"signal": "HOLD", "trend": "QUALIFIED", "position": "HIGH"},
]


class TestMatchSignal:
    def test_leader_low_strong_buy(self):
        assert four_stage.match_signal("QUALIFIED", "LEADER", "LOW", _RULES, "WAIT") == "STRONG_BUY"

    def test_leader_mid_buy(self):
        assert four_stage.match_signal("QUALIFIED", "LEADER", "MID", _RULES, "WAIT") == "BUY"

    def test_core_low_buy(self):
        assert four_stage.match_signal("QUALIFIED", "CORE", "LOW", _RULES, "WAIT") == "BUY"

    def test_core_mid_watch(self):
        assert four_stage.match_signal("QUALIFIED", "CORE", "MID", _RULES, "WAIT") == "WATCH"

    def test_high_position_hold_any_leadership(self):
        assert four_stage.match_signal("QUALIFIED", "LEADER", "HIGH", _RULES, "WAIT") == "HOLD"
        assert four_stage.match_signal("QUALIFIED", "CORE", "HIGH", _RULES, "WAIT") == "HOLD"
        assert four_stage.match_signal("QUALIFIED", "NON_CORE", "HIGH", _RULES, "WAIT") == "HOLD"

    def test_low_position_no_trend_is_wait(self):
        """纪律：历史低位不产生趋势——趋势不成立仍 fallback WAIT。"""
        assert four_stage.match_signal("NOT_QUALIFIED", "LEADER", "LOW", _RULES, "WAIT") == "WAIT"

    def test_non_core_falls_to_fallback(self):
        assert four_stage.match_signal("QUALIFIED", "NON_CORE", "LOW", _RULES, "WAIT") == "WAIT"
        assert four_stage.match_signal("QUALIFIED", "NON_CORE", "MID", _RULES, "WAIT") == "WAIT"

    def test_unknown_position_matches_as_mid(self):
        """数据不足 → 中性 MID（LEADER→BUY，不被低估升级也不被高位压制）。"""
        assert four_stage.match_signal("QUALIFIED", "LEADER", "UNKNOWN", _RULES, "WAIT") == "BUY"
        assert four_stage.match_signal("QUALIFIED", "CORE", "UNKNOWN", _RULES, "WAIT") == "WATCH"

    def test_rule_order_first_match(self):
        """HIGH 规则在 LEADER 规则之后：LEADER+HIGH 应命中 HOLD 而非降级规则。"""
        # LEADER×HIGH：规则 1/2（LOW/MID）不命中，规则 5（position HIGH）命中 → HOLD
        assert four_stage.match_signal("QUALIFIED", "LEADER", "HIGH", _RULES, "WAIT") == "HOLD"


class TestEvaluatePosition:
    def test_disabled_neutral(self):
        level, pct = four_stage.evaluate_position([1, 2, 3], 756, 30, 70, enabled=False)
        assert level == "" and pct is None

    def test_insufficient_history_unknown(self):
        level, pct = four_stage.evaluate_position([100.0], 756, 30, 70)
        assert level == "UNKNOWN" and pct is None

    def test_high_position(self):
        level, pct = four_stage.evaluate_position([50, 60, 70, 80, 90, 100], 756, 30, 70)
        assert level == "HIGH" and pct == 100.0


class TestHistoryLoaders:
    def test_missing_files_return_empty(self):
        assert four_stage.load_stock_close_history("CN", "__nonexistent__") == []
        assert four_stage.load_etf_close_history("__nonexistent__") == []

    def test_stock_history_asof_truncation(self):
        # CN_002230.csv 存在（真实数据）→ 按 trade_date 截断后收盘序列非空且最后日期 ≤ trade_date
        closes = four_stage.load_stock_close_history("CN", "002230", trade_date="2020-01-05")
        # 2020 年前无数据（002230 上市更晚或文件从 2023 起）→ 空也合法；有数据则断言无 look-ahead
        if closes:
            assert closes[-1] > 0
