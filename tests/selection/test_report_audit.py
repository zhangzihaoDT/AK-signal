"""Layer③ 08 决策审计（Decision Audit）测试：V2 分类器 / 摘要 / audit_reason / theme_role。"""

from __future__ import annotations

import json
from pathlib import Path

from src.selection.report_viewmodel import _audit_category, _etf_audit_category, build_view_model
from src.selection.report_spec import load_report_spec

FIXTURES = Path(__file__).parent.parent / "fixtures" / "report"
FIX_DATE = "20260902"


def _vm(sort: str = "", etf_sort: str = ""):
    d = json.loads((FIXTURES / f"selection_{FIX_DATE}.json").read_text(encoding="utf-8"))
    spec = load_report_spec()
    return build_view_model(
        d["layer3"], {}, d["date"],
        display_priority=spec.display_priority,
        audit_summary_spec=[(s.id, s.label) for s in spec.audit["stock_matrix"].summary],
        audit_sort=sort,
        audit_etf_summary_spec=[(s.id, s.label) for s in spec.audit["etf_matrix"].summary],
        audit_etf_sort=etf_sort)


class TestAuditCategory:
    def test_priority_breakdown_beats_monitor_only(self):
        assert _audit_category({"position_level": "BREAKDOWN", "participation": "monitor_only"}) == "breakdown"

    def test_breakdown_beats_blocked(self):
        assert _audit_category({"position_level": "BREAKDOWN", "blocking_flags": ["RISK_WARNING"]}) == "breakdown"

    def test_blocked_requires_meaningful_blocks(self):
        assert _audit_category({"blocking_flags": ["BELOW_TREND_GATE"], "state": "WATCH"}) == "normal"
        assert _audit_category({"blocking_flags": ["RISK_WARNING"], "state": "WATCH"}) == "blocked"
        assert _audit_category({"blocking_flags": ["BELOW_TREND_GATE", "LOW_LIQUIDITY"], "state": "WATCH"}) == "blocked"

    def test_qualified_before_recommended(self):
        assert _audit_category({"state": "QUALIFIED", "recommended": False}) == "qualified_unselected"

    def test_monitor_only_alone(self):
        assert _audit_category({"participation": "monitor_only", "state": "WATCH"}) == "monitor_only"

    def test_summary_counts_sum_to_rows(self):
        vm = _vm()
        total = sum(n for _, _, n in vm.audit_summary)
        assert total == len(vm.audit_stock)

    def test_rows_carry_asset_key(self):
        vm = _vm()
        assert all(r.get("_asset_key") for r in vm.audit_stock)


class TestEtfAuditCategoryV2:
    """v0.12.1 V2 分类：资格 → 载体 → 时机（reason_codes 语义）。"""

    def test_breakdown_yields_to_eligibility(self):
        """V2：③A 资格是最上游门控，lane2_unreliable 优先于 BREAKDOWN。"""
        assert _etf_audit_category({"position_level": "BREAKDOWN", "reason_codes": ["lane2_unreliable"]}) == "unreliably"

    def test_breakdown_still_categorized_when_no_eligibility_block(self):
        """V2：无 ③A 阻塞时，BREAKDOWN 仍正常归 breakdown。"""
        assert _etf_audit_category({"position_level": "BREAKDOWN", "reason_codes": [], "trend_status": "WATCH"}) == "breakdown"

    def test_lane2_unreliable_is_eligible_block(self):
        # ③A 数据不可靠（lane2 前置资格层，非后置 veto）
        assert _etf_audit_category({"reason_codes": ["below_trend_gate", "lane2_unreliable"]}) == "unreliably"

    def test_below_account_is_eligibility_block(self):
        assert _etf_audit_category({"reason_codes": ["below_account", "below_trend_gate"]}) == "below_account"

    def test_low_liquidity_is_eligibility_block(self):
        assert _etf_audit_category({"reason_codes": ["low_liquidity"]}) == "low_liquidity"

    def test_position_high_material_block(self):
        assert _etf_audit_category({"position_level": "HIGH", "blocking_flags": ["POSITION_HIGH"]}) == "blocked"

    def test_recommended_is_timing_ready(self):
        assert _etf_audit_category({"recommended": True, "monitoring_source": "recommendation"}) == "timing_ready"

    def test_recommendation_source_is_vehicle(self):
        assert _etf_audit_category({"recommended": False, "monitoring_source": "recommendation",
                                    "reason_codes": ["vehicle_eligible", "below_trend_gate"]}) == "vehicle"

    def test_eligible_but_unselected(self):
        assert _etf_audit_category({"recommended": False, "monitoring_source": "watchlist",
                                    "reason_codes": ["vehicle_eligible"]}) == "eligible"

    def test_dedup_lost_is_vehicle_lost(self):
        assert _etf_audit_category({"recommended": False, "monitoring_source": "watchlist",
                                    "reason_codes": ["vehicle_eligible", "dedup_lost"]}) == "dedup_lost"

    def test_below_trend_gate_is_timing_not_qual(self):
        assert _etf_audit_category({"recommended": False, "monitoring_source": "watchlist",
                                    "reason_codes": ["below_trend_gate"]}) == "below_trend"

    def test_summary_counts_sum(self):
        vm = _vm(etf_sort="anomaly_first")
        assert sum(n for _, _, n in vm.audit_etf_summary) == len(vm.audit_etf)

    def test_summary_maps_one_to_one_to_rows(self):
        from collections import Counter
        vm = _vm(etf_sort="anomaly_first")
        rows = Counter(r["_audit_category"] for r in vm.audit_etf)
        for cid, _, n in vm.audit_etf_summary:
            assert rows.get(cid, 0) == n, f"ETF {cid}: summary={n} rows={rows.get(cid, 0)}"

    def test_etf_anomaly_first_sort(self):
        vm = _vm(etf_sort="anomaly_first")
        rank = {"breakdown": 0, "blocked": 1, "unreliably": 1, "low_liquidity": 2,
                "below_account": 2, "qualified_unselected": 2, "eligible": 3,
                "vehicle": 4, "dedup_lost": 4, "timing_ready": 5, "recommended": 5,
                "below_trend": 6, "normal": 6, "monitor_only": 7, "dynamic_watch": 8}
        ranks = [rank[r["_audit_category"]] for r in vm.audit_etf]
        assert ranks == sorted(ranks)


class TestEtfFormatters:
    def test_etf_theme_role(self):
        from src.selection.report_formatters import fmt_etf_theme_role
        assert fmt_etf_theme_role({"_theme_label": "高现金流资产", "role": "CORE_ETF",
                                   "monitoring_source": "recommendation"}) == "高现金流资产 · 核心ETF"
        assert fmt_etf_theme_role({"_theme_label": "AI 基础设施", "role": "CORE_ETF",
                                   "monitoring_source": "watchlist"}) == "AI 基础设施 · 动态观察"

    def test_etf_audit_reason(self):
        from src.selection.report_formatters import fmt_etf_audit_reason
        assert fmt_etf_audit_reason({"recommended": True}) == "—"
        assert "中期破位" in fmt_etf_audit_reason({"position_level": "BREAKDOWN", "position_pct": -39.2})
        assert "成交额不足" == fmt_etf_audit_reason({"blocking_flags": ["LOW_LIQUIDITY"]})
        assert "未达趋势门" in fmt_etf_audit_reason({"blocking_flags": ["BELOW_TREND_GATE"], "rps15": 22.0})
        assert "动态观察" in fmt_etf_audit_reason({"monitoring_source": "watchlist"})


class TestAuditReason:
    def test_recommended_blank(self):
        from src.selection.report_formatters import fmt_audit_reason
        assert fmt_audit_reason({"recommended": True}) == "—"

    def test_breakdown_with_sign(self):
        from src.selection.report_formatters import fmt_audit_reason
        r = fmt_audit_reason({"position_level": "BREAKDOWN", "position_pct": -18.2})
        assert "中期破位" in r and "-18.2" in r

    def test_watch_context(self):
        from src.selection.report_formatters import fmt_audit_reason
        assert "CORE×MID" in fmt_audit_reason(
            {"signal": "WATCH", "leadership_level": "CORE", "position_level": "MID"})

    def test_monitor_only(self):
        from src.selection.report_formatters import fmt_audit_reason
        assert "不参与" in fmt_audit_reason({"participation": "monitor_only"})


class TestThemeRole:
    def test_role_map(self):
        from src.selection.report_formatters import fmt_theme_role
        assert fmt_theme_role({"_theme_label": "高现金流资产", "role": "LEADER"}) == "高现金流资产 · 龙头"
        assert fmt_theme_role({"_theme_label": "AI 基础设施", "role": "UPSTREAM"}) == "AI 基础设施 · 设备与上游"
