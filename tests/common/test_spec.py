"""Strategy Specification（v0.6.1）单元测试：Loader / Schema / Hash。"""

from __future__ import annotations

import pytest

from src.common.spec import schema as sch
from src.common.spec.hash import config_hash, universe_hash
from src.common.spec.loaders import (
    load_execution_spec, load_indicator_spec, load_portfolio_spec, load_strategy_spec,
)


class TestLoaders:
    def test_indicator_spec(self):
        s = load_indicator_spec()
        assert s.rps_windows == (15, 20, 60)
        assert s.rps_today_window == 1
        assert s.rps_velocity_window == 5
        assert s.etf_strong_threshold == 80.0
        assert s.confirmation_strong_threshold == 90.0

    def test_stock_selection_spec(self):
        from src.common.spec.loaders import load_stock_selection_spec
        ss = load_stock_selection_spec()
        assert ss.qualified_score == 70.0
        assert ss.allowed_trend_states == ("S", "A")
        assert ss.theme_confirm_states == ("观察", "强势")

    def test_stock_selection_four_stage(self):
        from src.common.spec.loaders import load_stock_selection_spec
        ss = load_stock_selection_spec()
        assert ss.rps15_min == 80.0
        assert ss.leadership.method == "theme_rank"
        assert ss.leadership.leader_rank_max == 3
        assert ss.leadership.core_rank_max == 10
        assert ss.leadership.require_rps_outperform is True
        assert ss.historical_position.enabled is True
        assert ss.historical_position.metric == "ma60_deviation"
        assert ss.historical_position.ma_window == 60
        assert ss.historical_position.breakdown_pct == -15.0
        assert (ss.historical_position.low_below_pct, ss.historical_position.high_above_pct) == (-5.0, 10.0)
        # 信号规则：顺序匹配、先命中先生效
        assert ss.signal_policy.fallback_signal == "WAIT"
        rules = ss.signal_policy.rules
        assert rules[0].signal == "STRONG_BUY"
        assert (rules[0].trend, rules[0].leadership, rules[0].position) == ("QUALIFIED", "LEADER", "LOW")
        assert rules[4].signal == "HOLD" and rules[4].position == "HIGH"
        assert rules[-1].signal == "WAIT" and rules[-1].position == "BREAKDOWN"

    def test_etf_selection_spec(self):
        from src.common.spec.loaders import load_etf_selection_spec
        es = load_etf_selection_spec()
        assert es.allowed_trend_states == ("BUY_CANDIDATE", "STRONG_WATCH")
        assert es.min_amount == 50_000_000
        assert es.ranking_weights == {"rps15": 0.55, "rps20": 0.25, "amount_score": 0.20}
        assert es.amount_score.floor == 50_000_000
        assert es.amount_score.reference == 500_000_000
        assert es.amount_score.cap == 100
        # v0.11 Phase 2 Lane Validation：可靠性硬 gate 默认开启
        assert es.lane_validation.reliability_hard_gate_enabled is True

    def test_etf_selection_four_stage(self):
        from src.common.spec.loaders import load_etf_selection_spec
        es = load_etf_selection_spec()
        # ETF leadership：core_rank_max=1 → LEADER 上界；satellite_rank_max=3 → CORE 上界
        assert es.leadership.leader_rank_max == 1
        assert es.leadership.core_rank_max == 3
        assert es.historical_position.metric == "ma60_deviation"
        assert es.historical_position.ma_window == 60

    def test_strategy_spec(self):
        s = load_strategy_spec("ai_fixed_20")
        assert s.theme == "ai_infrastructure"
        assert s.universe_mode == "configured"
        assert s.entry.rps15_min == 80.0
        assert s.exit.policy == "fixed_horizon"
        assert s.exit.horizon == 20
        # 不同主题策略参数可不同
        hc = load_strategy_spec("hc_fixed_20")
        assert hc.theme == "high_cashflow"
        assert hc.exit.horizon == 20

    def test_execution_spec(self):
        s = load_execution_spec()
        assert s.model == "next_open"
        assert s.fee_pct == 0.05  # 5bp
        assert s.slippage_pct == 0.05
        assert s.no_leverage is True

    def test_portfolio_spec(self):
        s = load_portfolio_spec()
        assert s.max_positions == 5
        assert s.max_weight_per_asset == 0.20
        assert s.deploy_ratio == 1.0
        assert s.allocations["core_quality"].weights["ai_infrastructure_weight"] == 0.60

    def test_frozen_objects(self):
        s = load_indicator_spec()
        with pytest.raises(Exception):
            s.rps_short_window = 10  # frozen


class TestSchemaValidation:
    def _valid_strategy(self):
        return {
            "entry": {"policy": "trend_confirmation", "rps15_min": 80,
                      "trend_score_min": 70, "allowed_trend_states": ["S", "A"]},
            "exit": {"policy": "fixed_horizon", "horizon": 20},
        }

    def test_fixed_horizon_requires_horizon(self):
        cfg = self._valid_strategy()
        cfg["exit"] = {"policy": "fixed_horizon"}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_strategy(cfg, "s1")

    def test_ma_exit_requires_window(self):
        cfg = self._valid_strategy()
        cfg["exit"] = {"policy": "ma_exit"}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_strategy(cfg, "s1")

    def test_bad_threshold(self):
        cfg = self._valid_strategy()
        cfg["entry"]["rps15_min"] = 150
        with pytest.raises(sch.SpecValidationError):
            sch.validate_strategy(cfg, "s1")

    def test_unsupported_policy(self):
        cfg = self._valid_strategy()
        cfg["exit"] = {"policy": "atr_stop", "horizon": 20}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_strategy(cfg, "s1")

    def test_duplicate_strategy_id(self):
        raw = {"strategies": {
            "a": {**self._valid_strategy(), "strategy_id": "dup", "theme": "x", "universe_mode": "configured"},
            "b": {**self._valid_strategy(), "strategy_id": "dup", "theme": "x", "universe_mode": "configured"},
        }}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_strategies(raw, {"x"})

    def test_missing_theme(self):
        raw = {"strategies": {"a": {**self._valid_strategy(), "strategy_id": "a",
                                    "theme": "ghost", "universe_mode": "configured"}}}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_strategies(raw, {"x"})

    def test_bad_universe_mode(self):
        raw = {"strategies": {"a": {**self._valid_strategy(), "strategy_id": "a",
                                    "theme": "x", "universe_mode": "bad"}}}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_strategies(raw, {"x"})

    def _valid_stock_selection(self, **over):
        base = {
            "trend": {"qualified_score": 70, "rps15_min": 80, "allowed_trend_states": ["S", "A"]},
            "theme_confirm_states": ["观察", "强势"],
            "leadership": {"method": "theme_rank", "leader_rank_max": 3, "core_rank_max": 10},
            "historical_position": {"lookback_days": 756, "low_max": 30, "mid_max": 70},
            "signal_policy": {
                "rules": [{"signal": "STRONG_BUY", "trend": "QUALIFIED", "leadership": "LEADER", "position": "LOW"}],
                "fallback_signal": "WAIT",
            },
        }
        base.update(over)
        return base

    def test_stock_selection_nested_trend(self):
        raw = {"stock_selection": self._valid_stock_selection()}
        sch.validate_stock_selection(raw)

    def test_stock_selection_requires_nested_trend(self):
        raw = {"stock_selection": {"qualified_score": 70, "theme_confirm_states": ["观察"]}}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_stock_selection(raw)

    def test_stock_selection_bad_position_bands(self):
        raw = {"stock_selection": self._valid_stock_selection(
            historical_position={"lookback_days": 756, "low_max": 60, "mid_max": 40})}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_stock_selection(raw)

    def test_stock_selection_bad_deviation_bands(self):
        raw = {"stock_selection": self._valid_stock_selection(
            historical_position={"metric": "ma60_deviation", "ma_window": 60,
                                 "low_below_pct": 10, "high_above_pct": 5})}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_stock_selection(raw)

    def test_stock_selection_bad_breakdown_ordering(self):
        """breakdown_pct 必须 < low_below_pct：-5 破位门槛 ≥ -5 回调门槛 → 非法。"""
        raw = {"stock_selection": self._valid_stock_selection(
            historical_position={"metric": "ma60_deviation", "ma_window": 60,
                                 "breakdown_pct": -5.0, "low_below_pct": -5.0, "high_above_pct": 10.0})}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_stock_selection(raw)

    def test_stock_selection_valid_deviation_bands(self):
        raw = {"stock_selection": self._valid_stock_selection(
            historical_position={"metric": "ma60_deviation", "ma_window": 60,
                                 "breakdown_pct": -15.0, "low_below_pct": -5.0, "high_above_pct": 10.0})}
        sch.validate_stock_selection(raw)

    def test_stock_selection_unsupported_position_metric(self):
        raw = {"stock_selection": self._valid_stock_selection(
            historical_position={"metric": "ma99"})}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_stock_selection(raw)

    def test_stock_selection_bad_signal(self):
        raw = {"stock_selection": self._valid_stock_selection(
            signal_policy={"rules": [{"signal": "SELL_ALL"}], "fallback_signal": "WAIT"})}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_stock_selection(raw)

    def test_etf_selection_nested_trend(self):
        raw = {"etf_selection": {
            "trend": {"allowed_trend_states": ["BUY_CANDIDATE"], "watch_allowed_trend_states": ["WATCH"], "min_amount": 5e7},
            "ranking": {"weights": {"rps15": 0.55, "rps20": 0.25, "amount_score": 0.20},
                        "amount_score": {"method": "log_threshold", "floor": 5e7, "reference": 5e8, "cap": 100}},
            "leadership": {"core_rank_max": 1, "satellite_rank_max": 3},
            "historical_position": {"lookback_days": 756, "low_max": 30, "mid_max": 70},
        }}
        sch.validate_etf_selection(raw)

    def test_etf_selection_flat_trend_rejected(self):
        raw = {"etf_selection": {
            "allowed_trend_states": ["BUY_CANDIDATE"], "min_amount": 5e7,
            "ranking": {"weights": {"rps15": 0.55, "rps20": 0.25, "amount_score": 0.20},
                        "amount_score": {"method": "log_threshold", "floor": 5e7, "reference": 5e8, "cap": 100}},
        }}
        with pytest.raises(sch.SpecValidationError):
            sch.validate_etf_selection(raw)


class TestHash:
    def test_config_hash_stable_and_order_independent(self):
        a = config_hash()
        b = config_hash()
        assert a == b
        assert len(a) == 16

    def test_universe_hash_order_independent(self):
        a = universe_hash(["512480", "159819"], mode="configured", theme="ai_infrastructure")
        b = universe_hash(["159819", "512480"], mode="configured", theme="ai_infrastructure")
        assert a == b
        c = universe_hash(["512480", "159819", "561560"], mode="configured", theme="ai_infrastructure")
        assert c != a  # 成员增删 → 改变

    def test_universe_hash_mode_sensitive(self):
        a = universe_hash(["512480"], mode="configured")
        b = universe_hash(["512480"], mode="theme-matched")
        assert a != b
