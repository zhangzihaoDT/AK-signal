"""Expression Regime 研究链路单元测试（v0.10）。"""

from __future__ import annotations

import pandas as pd
import pytest

from src.research.expression_regime import structure as st
from src.research.expression_regime import study as es_study


class TestExpressionFromStructures:
    def test_broad_not_leader(self):
        assert st.expression_from_structures(True, False)[0] == "ETF_PRIORITY"

    def test_leader_dominated(self):
        assert st.expression_from_structures(False, True)[0] == "LEADER_PRIORITY"
        assert st.expression_from_structures(True, True)[0] == "LEADER_PRIORITY"

    def test_diffusion(self):
        assert st.expression_from_structures(False, False)[0] == "ETF_CORE_PLUS_LEADER"

    def test_unknown_falls_back_to_combo(self):
        expr, reason = st.expression_from_structures(None, None)
        assert expr == "ETF_CORE_PLUS_LEADER"
        assert "结构信息不足" in reason


class TestTierStructureInput:
    def _rows(self, advance, contribution):
        return [{"theme": "ai_infrastructure", "data_status": "current",
                 "advance_ratio": advance, "leader_contribution": contribution}]

    def test_broad_high_advance(self):
        inp = st.TierStructureInput()
        feat = inp.features("ai_infrastructure", self._rows(0.8, 0.3))
        assert feat["broad"] is True
        assert feat["leader_dominated"] is False
        assert inp.expression("ai_infrastructure", self._rows(0.8, 0.3))[0] == "ETF_PRIORITY"

    def test_leader_high_contribution(self):
        inp = st.TierStructureInput()
        expr, _, feat = inp.expression("ai_infrastructure", self._rows(0.4, 0.72))
        assert expr == "LEADER_PRIORITY"
        assert feat["leader_dominated"] is True

    def test_unavailable_rows_ignored(self):
        inp = st.TierStructureInput()
        feat = inp.features("ai_infrastructure",
                            [{"theme": "ai_infrastructure", "data_status": "unavailable",
                              "advance_ratio": None, "leader_contribution": None}])
        assert feat["broad"] is None
        assert feat["leader_dominated"] is None


class TestIndustryStructureInput:
    def test_broad_via_participation(self):
        inp = st.IndustryStructureInput()
        meta = {"median_participation": 0.78, "median_hhi": 0.04, "median_top3_share": 0.30}
        assert inp.expression(meta)[0] == "ETF_PRIORITY"

    def test_leader_via_hhi(self):
        inp = st.IndustryStructureInput()
        meta = {"median_participation": 0.30, "median_hhi": 0.22, "median_top3_share": 0.40}
        assert inp.expression(meta)[0] == "LEADER_PRIORITY"

    def test_leader_via_top3(self):
        inp = st.IndustryStructureInput()
        meta = {"median_participation": 0.30, "median_hhi": 0.05, "median_top3_share": 0.72}
        assert inp.expression(meta)[0] == "LEADER_PRIORITY"

    def test_unknown_structure(self):
        inp = st.IndustryStructureInput()
        assert inp.expression({"median_participation": None, "median_hhi": None,
                               "median_top3_share": None})[0] == "ETF_CORE_PLUS_LEADER"


class TestBuildStructureInput:
    def test_sources(self):
        assert isinstance(st.build_structure_input("tier"), st.TierStructureInput)
        assert isinstance(st.build_structure_input("industry"), st.IndustryStructureInput)
        assert isinstance(st.build_structure_input("Tier_Structure"), st.TierStructureInput)
        with pytest.raises(ValueError):
            st.build_structure_input("unknown")


class TestHistoryScore:
    def test_score_row_matches_production(self):
        from src.trend_engine import scoring as tscoring
        from src.research.expression_regime.history import score_row
        row = pd.Series({
            "close": 12.0, "ma20": 11.0, "ma60": 10.5, "ma120": 10.0,
            "ma20_slope": 0.1, "ma60_slope": 0.05, "macd_hist": 0.2,
            "rsi14": 60.0, "relative_strength_20d": 0.03,
        })
        fast = score_row(row)
        prod, _ = tscoring.score_latest_row(pd.DataFrame([row]))
        assert fast == prod == 100


class _FakeBook:
    """合成 PriceBook：按 code 前缀返回固定前向收益。"""

    def __init__(self, etf_ret, stock_ret, bench=0.0):
        self.etf_ret = etf_ret
        self.stock_ret = stock_ret
        self.bench = bench

    def forward_returns(self, entity_type, code, tdate, horizons):
        v = self.etf_ret if entity_type == "etf" else self.stock_ret
        return {h: v for h in horizons}

    def benchmark_forward(self, entity_type, tdate, horizons):
        return {h: self.bench for h in horizons}


def _event(expression, etf="588000", leader="600900"):
    return pd.DataFrame([{
        "trade_date": "20240102", "theme": "ai_infrastructure",
        "theme_label": "AI", "bucket": "core", "bucket_label": "核心",
        "confirmation_state": "CONFIRMED", "expression": expression,
        "expression_reason": "", "broad": None, "leader_dominated": None,
        "median_advance_ratio": None, "median_leader_contribution": None,
        "median_participation": None, "median_hhi": None, "median_top3_share": None,
        "etf_code": etf, "etf_name": "ETF", "etf_rps15": 90.0, "etf_trend_status": "BUY_CANDIDATE",
        "leader_code": leader, "leader_name": "STOCK", "leader_score": 90.0,
        "leader_watch_level": "S",
    }])


class TestAugmentEventReturns:
    def test_etf_priority_hit_when_etf_better(self):
        ev = _event("ETF_PRIORITY")
        out = es_study.augment_event_returns(ev, _FakeBook(etf_ret=0.10, stock_ret=0.02), (20, 60))
        assert bool(out.iloc[0]["hit_20"]) is True
        assert out.iloc[0]["chosen_20"] == pytest.approx(0.10)
        assert out.iloc[0]["best_20"] == pytest.approx(0.10)
        assert out.iloc[0]["delta_best_20"] == pytest.approx(0.0)

    def test_leader_priority_miss_when_etf_better(self):
        ev = _event("LEADER_PRIORITY")
        out = es_study.augment_event_returns(ev, _FakeBook(etf_ret=0.10, stock_ret=0.02), (20,))
        assert bool(out.iloc[0]["hit_20"]) is False
        assert out.iloc[0]["chosen_20"] == pytest.approx(0.02)
        assert out.iloc[0]["best_20"] == pytest.approx(0.10)
        assert out.iloc[0]["delta_best_20"] == pytest.approx(-0.08)
        # 成对指标：ETF − 龙头 > 0（说明 Leader 判定判错了方向）
        assert out.iloc[0]["etf_vs_stock_20"] == pytest.approx(0.08)

    def test_combo_return_is_equal_weight(self):
        ev = _event("ETF_CORE_PLUS_LEADER")
        out = es_study.augment_event_returns(ev, _FakeBook(etf_ret=0.10, stock_ret=0.06), (20,))
        assert out.iloc[0]["combo_20"] == pytest.approx(0.08)

    def test_drops_event_without_leader(self):
        ev = _event("ETF_PRIORITY", leader="")
        out = es_study.augment_event_returns(ev, _FakeBook(0.1, 0.1), (20,))
        assert out.empty


class TestAggregateSummary:
    def _aug(self):
        ev = pd.concat([
            _event("ETF_PRIORITY", etf="a"),
            _event("ETF_PRIORITY", etf="b"),
            _event("LEADER_PRIORITY", etf="c"),
        ], ignore_index=True)
        return es_study.augment_event_returns(ev, _FakeBook(0.10, 0.02), (20,))

    def test_summary_overall(self):
        s = es_study.aggregate_summary(self._aug(), (20,), ())
        assert len(s) == 1
        r = s.iloc[0]
        assert r["n_events"] == 3
        assert r["horizon"] == 20

    def test_summary_by_expression(self):
        s = es_study.aggregate_summary(self._aug(), (20,), ("expression",))
        assert set(s["expression"]) == {"ETF_PRIORITY", "LEADER_PRIORITY"}
        etf_row = s[s["expression"] == "ETF_PRIORITY"].iloc[0]
        leader_row = s[s["expression"] == "LEADER_PRIORITY"].iloc[0]
        assert etf_row["hit_rate"] == pytest.approx(1.0)
        assert leader_row["hit_rate"] == pytest.approx(0.0)
        # 成对：ETF_PRIORITY 组内 etf−stock > 0（结构判断正确方向）
        assert etf_row["etf_vs_stock"]["mean"] == pytest.approx(0.08)
