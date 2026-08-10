"""Tier-level confirmation（v0.9.2 taxonomy）— 状态机 + Theme 聚合"""
from __future__ import annotations

from src.sw_industry_rps import tier_confirmation as tc


def _row(state: str, strength: float | None = 50.0, **kw):
    row = {
        "theme": "ai_infrastructure", "tier": "optical", "tier_label": "光模块",
        "n_total": 3, "n_with_data": 3, "n_missing": 0,
        "tier_strength": strength, "advance_ratio": 0.5,
        "median_trend_score": 50.0, "n_strong_trend": 1,
        "leader_contribution": 0.4, "leader_symbol": "300308", "leader_name": "中际旭创",
        "confirmation_state": state, "confirmation_breadth": "", "reason_code": "", "reason": "",
        "data_status": "current",
    }
    row.update(kw)
    return row


class TestTierStateMachine:
    """Tier 状态枚举：STRONG / CONFIRMED / WATCH / UNCONFIRMED / UNAVAILABLE。

    WATCH 不再承担「接近确认」语义——为什么观察由 reason_code 表达。
    """

    def test_strong_requires_strong_trend(self):
        state, code, _ = tc._tier_state(80.0, n_strong=1, n_with_data=3)
        assert state == "STRONG" and code == ""

    def test_confirmed_at_observe_gate(self):
        # ≥55 进入确认门（原 OBSERVE 更名为 CONFIRMED）
        state, code, _ = tc._tier_state(60.0, n_strong=0, n_with_data=3)
        assert state == "CONFIRMED" and code == ""

    def test_watch_reason_breadth_insufficient(self):
        # 有强趋势股但整 Tier 广度不足 → 观察 · breadth不足（不是「接近确认 · breadth不足」）
        state, code, reason = tc._tier_state(45.0, n_strong=1, n_with_data=3)
        assert state == "WATCH"
        assert code == "breadth_insufficient"
        assert "广度不足" in reason

    def test_watch_reason_near_threshold(self):
        state, code, _ = tc._tier_state(48.0, n_strong=0, n_with_data=3)  # ≥55*0.8
        assert state == "WATCH" and code == "near_threshold"

    def test_watch_reason_trend_emerging(self):
        state, code, _ = tc._tier_state(38.0, n_strong=0, n_with_data=3)  # 0.65~0.8
        assert state == "WATCH" and code == "trend_emerging"

    def test_watch_reason_single_name(self):
        state, code, _ = tc._tier_state(30.0, n_strong=0, n_with_data=3)
        assert state == "WATCH" and code == "single_name_only"

    def test_unconfirmed_below_half_gate(self):
        state, code, _ = tc._tier_state(20.0, n_strong=0, n_with_data=3)
        assert state == "UNCONFIRMED" and code == ""

    def test_unavailable_no_data(self):
        state, code, _ = tc._tier_state(None, n_strong=0, n_with_data=0)
        assert state == "UNAVAILABLE"


class TestThemeAggregation:
    """Theme 层不用 WATCH：观察中 Tier 数单独输出（n_watch_tiers）。"""

    def test_narrow_confirmed_single_tier(self):
        rows = [_row("CONFIRMED"), _row("WATCH"), _row("UNCONFIRMED")]
        agg = tc.theme_confirmation_from_tiers(rows)
        assert agg["confirmation_state"] == "NARROW_CONFIRMED"
        assert agg["n_observe_tiers"] == 1
        assert agg["n_watch_tiers"] == 1
        assert agg["confirmed"] is True

    def test_broad_confirmed(self):
        rows = [_row("CONFIRMED"), _row("STRONG"), _row("CONFIRMED"), _row("WATCH")]
        agg = tc.theme_confirmation_from_tiers(rows)
        assert agg["confirmation_state"] == "BROAD_CONFIRMED"
        assert agg["n_observe_tiers"] == 3

    def test_unconfirmed_with_watch_tiers(self):
        # 汽车全球化场景：0 确认但 2 个 Tier 观察 → Theme 仍是 UNCONFIRMED
        rows = [_row("WATCH"), _row("WATCH"), _row("UNCONFIRMED")]
        agg = tc.theme_confirmation_from_tiers(rows)
        assert agg["confirmation_state"] == "UNCONFIRMED"
        assert agg["confirmed"] is False
        assert agg["n_watch_tiers"] == 2
        assert "2 个 Tier 进入观察" in agg["reason"]

    def test_unavailable_when_no_active_tier(self):
        rows = [_row("UNAVAILABLE", None, data_status="unavailable")] * 2
        agg = tc.theme_confirmation_from_tiers(rows)
        assert agg["confirmation_state"] == "UNAVAILABLE"
        assert agg["confirmed"] is False

    def test_legacy_observe_counted_as_confirmed(self):
        # 兼容旧产物：OBSERVE 视同 CONFIRMED
        rows = [_row("OBSERVE"), _row("WATCH")]
        agg = tc.theme_confirmation_from_tiers(rows)
        assert agg["n_observe_tiers"] == 1
        assert agg["confirmed"] is True
