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
        assert s.etf_strong_threshold == 80.0
        assert s.etf_gate_states == ("BUY_CANDIDATE", "STRONG_WATCH")
        assert s.stock_qualified_score == 70.0
        assert s.confirmation_strong_threshold == 90.0

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
