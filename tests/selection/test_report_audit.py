"""Layer③ 06 决策审计（Decision Audit）测试：分类器 / 摘要 / audit_reason / theme_role。"""

from __future__ import annotations

import json
from pathlib import Path

from src.selection.report_viewmodel import _audit_category, _etf_audit_category, build_view_model
from src.selection.report_spec import load_report_spec

FIXTURES = Path(__file__).parent.parent / "fixtures" / "report"


def _vm(sort: str = "", etf_sort: str = ""):
    d = json.loads((FIXTURES / "selection_20260827.json").read_text(encoding="utf-8"))
    spec = load_report_spec()
    return build_view_model(
        d["layer3"], {}, d["date"],
        display_priority=spec.display_priority,
        execution_labels=spec.execution_labels,
        audit_summary_spec=[(s.id, s.label) for s in spec.audit["stock_matrix"].summary],
        audit_sort=sort,
        audit_etf_summary_spec=[(s.id, s.label) for s in spec.audit["etf_matrix"].summary],
        audit_etf_sort=etf_sort)


class TestAuditCategory:
    def test_priority_breakdown_beats_monitor_only(self):
        """monitor-only 且破位 → 归 breakdown（provenance 不覆盖风险状态）。"""
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


class TestAnomalyFirstSort:
    _RANK = {"breakdown": 0, "blocked": 1, "qualified_unselected": 2,
             "recommended": 3, "normal": 4, "monitor_only": 5}

    def test_anomaly_first_orders_categories(self):
        vm = _vm(sort="anomaly_first")
        ranks = [self._RANK[r["_audit_category"]] for r in vm.audit_stock]
        assert ranks == sorted(ranks)

    def test_unsorted_differs_from_sorted(self):
        """sort='' 保持主题×tier 原始顺序；anomaly_first 会重排分类序列。"""
        unsorted_ranks = [self._RANK[r["_audit_category"]] for r in _vm(sort="").audit_stock]
        sorted_ranks = [self._RANK[r["_audit_category"]] for r in _vm(sort="anomaly_first").audit_stock]
        assert sorted_ranks != unsorted_ranks
        assert sorted_ranks == sorted(sorted_ranks)

    def test_within_category_sorted_by_trend_desc(self):
        vm = _vm(sort="anomaly_first")
        breakdown = [r for r in vm.audit_stock if r["_audit_category"] == "breakdown"]
        scores = [r.get("score_trend") for r in breakdown]
        assert scores == sorted(scores, reverse=True)


class TestEtfAuditCategory:
    _RANK = {"breakdown": 0, "blocked": 1, "qualified_unselected": 2,
             "recommended": 3, "normal": 4, "monitor_only": 5, "dynamic_watch": 6}

    def test_breakdown_first(self):
        assert _etf_audit_category({"position_level": "BREAKDOWN",
                                    "blocking_flags": ["LOW_LIQUIDITY"]}) == "breakdown"

    def test_pure_below_gate_not_blocked(self):
        """纯 BELOW_TREND_GATE（常态门控）不算物料阻塞。"""
        assert _etf_audit_category({"trend_status": "BELOW_TREND_GATE",
                                    "blocking_flags": ["BELOW_TREND_GATE"]}) == "dynamic_watch"

    def test_material_block(self):
        assert _etf_audit_category({"trend_status": "BELOW_TREND_GATE",
                                    "blocking_flags": ["LOW_LIQUIDITY"]}) == "blocked"
        assert _etf_audit_category({"trend_status": "STRONG_WATCH",
                                    "blocking_flags": ["DEDUP_LOST", "POSITION_HIGH"]}) == "blocked"

    def test_dedup_not_material_goes_qualified(self):
        """DEDUP_LOST 不属物料阻塞；趋势达标的去重 ETF → 合格未选。"""
        assert _etf_audit_category({"trend_status": "WATCH", "recommended": False,
                                    "blocking_flags": ["DEDUP_LOST", "SIGNAL_WATCH"]}) == "qualified_unselected"

    def test_in_gate_not_recommended_qualified(self):
        assert _etf_audit_category({"trend_status": "STRONG_WATCH", "recommended": False,
                                    "blocking_flags": []}) == "qualified_unselected"

    def test_normal_vs_dynamic(self):
        assert _etf_audit_category({"trend_status": "BELOW_TREND_GATE", "recommended": False,
                                    "monitoring_source": "recommendation", "blocking_flags": []}) == "normal"
        assert _etf_audit_category({"trend_status": "BELOW_TREND_GATE", "recommended": False,
                                    "monitoring_source": "watchlist", "blocking_flags": []}) == "dynamic_watch"

    def test_summary_counts_sum(self):
        vm = _vm(etf_sort="anomaly_first")
        assert sum(n for _, _, n in vm.audit_etf_summary) == len(vm.audit_etf)

    def test_summary_maps_one_to_one_to_rows(self):
        """摘要计数 ↔ 列表行一一对应：每个分类的计数必须等于带该分类标记的行数。"""
        from collections import Counter
        vm = _vm(etf_sort="anomaly_first")
        rows = Counter(r["_audit_category"] for r in vm.audit_etf)
        for cid, _, n in vm.audit_etf_summary:
            assert rows.get(cid, 0) == n, f"ETF {cid}: summary={n} rows={rows.get(cid,0)}"
        vm2 = _vm(sort="anomaly_first")
        srows = Counter(r["_audit_category"] for r in vm2.audit_stock)
        for cid, _, n in vm2.audit_summary:
            assert srows.get(cid, 0) == n, f"stock {cid}: summary={n} rows={srows.get(cid,0)}"

    def test_etf_anomaly_first_sort(self):
        vm = _vm(etf_sort="anomaly_first")
        ranks = [self._RANK[r["_audit_category"]] for r in vm.audit_etf]
        assert ranks == sorted(ranks)

    def test_product_availability(self):
        vm = _vm()
        by_label = {label: (e0, tot) for label, e0, tot in vm.etf_product_availability}
        assert by_label["高现金流资产"] == (0, 32)   # 0/32 可交易 ⚠


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
        assert "同主题有更优产品" == fmt_etf_audit_reason({"blocking_flags": ["DEDUP_LOST"]})
        assert "未达趋势门" in fmt_etf_audit_reason({"blocking_flags": ["BELOW_TREND_GATE"], "rps15": 22.0})
        assert "第 3" in fmt_etf_audit_reason({"state": "RECOMMENDED", "recommended": False, "theme_rank": 3})
        assert "动态观察" in fmt_etf_audit_reason({"monitoring_source": "watchlist"})
