from __future__ import annotations

import pytest

from src.selection import recommendation as rec


def _theme_confirmed() -> dict:
    """确认主题：2/5 行业观察区，ETF_PRIORITY，2 ETF + 2 个股推荐。"""
    return {
        "theme": "high_cashflow", "theme_label": "高现金流资产",
        "bucket": "quality", "bucket_label": "质量", "objective": "高现金流防守",
        "confirmed": True,
        "confirmation_state": "BROAD_CONFIRMED", "confirmation_breadth": "广泛确认",
        "confirm_evidence": {"industry": "航运港口", "rps15": 87.1},
        "observing_industries": [
            {"industry": "航运港口", "industry_code": "801992.SI", "rps15": 87.1, "strength_level": "观察"},
            {"industry": "通信服务", "industry_code": "801223.SI", "rps15": 73.0, "strength_level": "观察"},
        ],
        "metrics": {"n_observe": 2, "n_total": 5, "n_strong": 0, "median_participation": 0.86,
                    "median_hhi": 0.08, "median_top3_share": 0.42, "median_rps15": 54.0,
                    "strongest_industry_rps15": 87.1, "etf_median_rps15": 81.0},
        "expression": "ETF_PRIORITY", "expression_label": "优先 ETF（广泛上涨）",
        "expression_reason": "参与率≥60% 且结构分散，ETF 完整承接行业 Beta",
        "core_etf": [{"code": "561560", "name": "电力ETF华泰柏瑞", "asset_type": "etf", "recommended": True,
                      "rps15": 81.0, "liquidity": 129760365.0, "reason": "主题评分最高", "state": "RECOMMENDED"}],
        "sub_industry_etf": [{"code": "159625", "name": "绿色电力ETF嘉实", "asset_type": "etf", "recommended": True,
                              "rps15": 83.3, "liquidity": 53291290.9, "reason": "细分方向代表", "state": "RECOMMENDED"}],
        "etf_pool": [
            {"code": "561560", "name": "电力ETF华泰柏瑞", "asset_type": "etf", "recommended": True,
             "rps15": 81.0, "liquidity": 129760365.0, "selection_score": 72.0, "reason_codes": ["trend_gate_passed", "liquidity_ok"]},
            {"code": "159625", "name": "绿色电力ETF嘉实", "asset_type": "etf", "recommended": True,
             "rps15": 83.3, "liquidity": 53291290.9, "selection_score": 64.1, "reason_codes": ["trend_gate_passed", "liquidity_ok"]},
            {"code": "159536", "name": "绿色电力ETF南方", "asset_type": "etf", "recommended": False,
             "rps15": 66.0, "liquidity": 20_000_000.0, "selection_score": 55.0, "reason_codes": ["below_trend_gate", "low_liquidity"]},
        ],
        "stock_watchlist": {
            "leaders": [
                {"code": "600941", "name": "中国移动", "asset_type": "stock", "recommended": True,
                 "score_trend": 100.0, "trend_status": "A", "state": "RECOMMENDED", "selection_status": "available",
                 "reason": "重点观察", "risk_flags": []},
                {"code": "600900", "name": "长江电力", "asset_type": "stock", "recommended": False,
                 "score_trend": 50.0, "trend_status": "B", "state": "WATCH", "selection_status": "available",
                 "reason": "风险警戒", "risk_gate_passed": False,
                 "reason_codes": ["risk_warning"], "risk_flags": ["跌破MA20", "MACD转弱"]},
            ],
            "high_beta": [], "equipment": [],
        },
        "stock_candidates": [
            {"code": "600941", "name": "中国移动", "asset_type": "stock", "recommended": True,
             "score_trend": 100.0, "trend_status": "A", "state": "RECOMMENDED", "selection_status": "available", "reason": "重点观察"},
            {"code": "601728", "name": "中国电信", "asset_type": "stock", "recommended": True,
             "score_trend": 100.0, "trend_status": "A", "state": "RECOMMENDED", "selection_status": "available", "reason": "重点观察"},
        ],
        "primary_etf": [{"code": "561560", "name": "电力ETF华泰柏瑞", "asset_type": "etf", "recommended": True, "rps15": 81.0}],
        "primary_stock": [{"code": "600941", "name": "中国移动", "asset_type": "stock", "recommended": True, "score_trend": 100.0}],
        "stage": "已确认",
        "strongest_etf": {"code": "561560", "name": "电力ETF华泰柏瑞", "rps15": 81.0},
        "distance_to_industry_confirm": 0,
        "distance_to_etf_strength": -1.0,
    }


def _theme_unconfirmed() -> dict:
    return {
        "theme": "ai_infrastructure", "theme_label": "AI 基础设施",
        "bucket": "core", "bucket_label": "核心", "objective": "AI 算力/芯片/通信基础设施",
        "confirmed": False,
        "confirmation_state": "UNCONFIRMED", "confirmation_breadth": "无支撑",
        "observing_industries": [],
        "metrics": {"n_observe": 0, "n_total": 7, "median_rps15": 7.3, "strongest_industry_rps15": 43.5,
                    "etf_median_rps15": 4.2},
        "expression": "WATCHLIST_ONLY", "expression_label": "仅观察（行业未确认）",
        "expression_reason": "主题行业未确认，仅输出观察候选",
        "core_etf": [], "sub_industry_etf": [],
        "etf_pool": [
            {"code": "516510", "name": "云计算ETF易方达", "asset_type": "etf", "recommended": False,
             "rps15": 61.1, "liquidity": 372028945.0, "selection_score": 72.6, "reason_codes": ["below_trend_gate", "liquidity_ok"]},
        ],
        "stock_watchlist": {"leaders": [], "high_beta": [], "equipment": []},
        "stock_candidates": [],
        "primary_etf": [], "primary_stock": [],
        "stage": "弱势",
        "strongest_etf": {"code": "516510", "name": "云计算ETF易方达", "rps15": 61.1},
        "distance_to_industry_confirm": 36.5,
        "distance_to_etf_strength": 18.9,
    }


def _engine(theme_objs):
    return {
        "version": "0.4.3",
        "direction": {"gate": "PROCEED"},
        "action": {"level": "BUY", "theme_label": "高现金流资产", "summary": "今日方向：买入 质量 · 高现金流资产"},
        "summary": {"recommended_actions": 4},
        "closest_theme": None,
        "recommended_actions": [],
        "buckets": [{"bucket": "quality", "bucket_label": "质量", "objective": "高现金流防守",
                     "n_themes": 1, "n_confirmed": 1, "themes": theme_objs}],
    }


class TestBuildRecommendation:
    def test_role_and_version(self):
        r = rec.build_recommendation(_engine([_theme_confirmed()]))
        assert r["role"] == "recommendation"
        assert r["version"] == "0.5.0"
        assert r["engine"]["version"] == "0.4.3"
        assert r["action"]["level"] == "BUY"

    def test_confirmed_theme_blocks(self):
        r = rec.build_recommendation(_engine([_theme_confirmed()]))
        t = r["buckets"][0]["themes"][0]
        assert t["today"]["action"] == "BUY"
        assert t["today"]["expression_label"] == "优先 ETF（广泛上涨）"
        # ② 为什么：观察区行业明细 + 结构
        assert len(t["why"]["observing_industries"]) == 2
        assert t["why"]["n_observe"] == 2 and t["why"]["n_total"] == 5
        assert t["why"]["median_participation"] == pytest.approx(0.86)
        assert t["why"]["expression_reason"].startswith("参与率")
        # ③ 买什么
        assert len(t["recommendation"]["etf"]) == 2
        assert len(t["recommendation"]["stocks"]) == 2
        # ④ 为什么选它
        assert len(t["rationale"]["etf"]) == 2
        assert t["rationale"]["etf"][0]["rps15"] == pytest.approx(81.0)
        assert t["rationale"]["stocks"][0]["score_trend"] == pytest.approx(100.0)
        # ⑤ 观察：未推荐 ETF（含原因）+ 未推荐个股
        assert len(t["watchlist"]["etf"]) == 1
        assert t["watchlist"]["etf"][0]["code"] == "159536"
        assert "流动性不足" in t["watchlist"]["etf"][0]["reject_reason"]
        assert len(t["watchlist"]["stocks"]) == 1
        assert t["watchlist"]["stocks"][0]["code"] == "600900"
        assert "跌破MA20" in t["watchlist"]["stocks"][0]["reject_reason"]
        # 附录 ETF 监控：推荐 ETF 与动态观察 ETF 分开标注
        assert [a["code"] for a in t["etf_monitoring"]] == ["561560", "159625", "159536"]
        assert t["etf_monitoring"][0]["monitoring_source"] == "recommendation"
        assert t["etf_monitoring"][-1]["monitoring_source"] == "watchlist"

    def test_unconfirmed_theme_blocks(self):
        r = rec.build_recommendation(_engine([_theme_unconfirmed()]))
        t = r["buckets"][0]["themes"][0]
        assert t["today"]["action"] == "WAIT"
        assert t["confirmed"] is False
        assert t["why"]["distance_to_industry_confirm"] == pytest.approx(36.5)
        assert t["why"]["distance_to_etf_strength"] == pytest.approx(18.9)
        assert "observing_industries" in t["why"]
        assert t["recommendation"]["etf"] == []
        assert t["recommendation"]["stocks"] == []
        assert len(t["watchlist"]["etf"]) == 1

    def test_no_new_facts(self):
        """Recommendation Builder 不制造新事实：etf_pool 的原始字段原样透传，不重算。"""
        theme = _theme_confirmed()
        pool_watch = next(a for a in theme["etf_pool"] if not a["recommended"])
        r = rec.build_recommendation(_engine([theme]))
        t = r["buckets"][0]["themes"][0]
        watch_etf = t["watchlist"]["etf"][0]
        assert watch_etf["rps15"] == pool_watch["rps15"]
        assert watch_etf["selection_score"] == pytest.approx(55.0)
        # 推荐 ETF 的事实字段（RPS15/流动性）原值保留
        assert t["recommendation"]["etf"][0]["rps15"] == pytest.approx(81.0)
        assert t["recommendation"]["etf"][0]["liquidity"] == pytest.approx(129760365.0)


def _theme_with_exec(confirmed=True, rec=True, eligible=4, pool_total=32):
    """theme/execution 拆分的构造器：控制 recommended ETF 与 eligible 数。"""
    rec_etf = [{"code": "561560", "name": "电力ETF华泰柏瑞", "asset_type": "etf", "recommended": True}]
    return {
        "theme": "high_cashflow", "theme_label": "高现金流资产",
        "bucket": "quality", "bucket_label": "质量", "objective": "高现金流防守",
        "confirmed": confirmed,
        "confirmation_breadth": "广泛确认", "confirmation_state": "BROAD_CONFIRMED",
        "expression": "ETF_PRIORITY", "expression_label": "优先 ETF（广泛上涨）",
        "expression_reason": "参与率≥60% 且结构分散，ETF 完整承接行业 Beta",
        "core_etf": rec_etf if rec else [],
        "sub_industry_etf": [],
        "etf_pool": [], "etf_pool_total": pool_total,
        "eligible_etf_count": eligible,
        "metrics": {}, "stage": "已确认",
    }


class TestExecutionActionSplit:
    """R1：theme_state（确认面）≠ execution_action（可执行面）。"""

    def _today(self, **kw):
        theme = _theme_with_exec(**kw)
        return rec.build_recommendation(_engine([theme]))["buckets"][0]["themes"][0]["today"]

    def test_confirmed_with_rec_is_buy(self):
        td = self._today(confirmed=True, rec=True, eligible=4)
        assert td["theme_state"] == "CONFIRMED"
        assert td["action"] == "BUY"
        assert td["execution_action"] == "BUY"

    def test_confirmed_eligible_no_rec_is_observe(self):
        # 高现金流：eligible=4（达产品门）但无 BUY 信号 → 观察，绝不显示买入
        td = self._today(confirmed=True, rec=False, eligible=4)
        assert td["theme_state"] == "CONFIRMED"
        assert td["action"] == "OBSERVE"
        assert "未达 BUY" in td["summary"]

    def test_confirmed_no_eligible_is_wait_for_etf(self):
        # 中国汽车：0 合格 ETF → WAIT_FOR_ETF，不显示买入
        td = self._today(confirmed=True, rec=False, eligible=0, pool_total=55)
        assert td["action"] == "WAIT_FOR_ETF"
        assert "等待合格 ETF" in td["summary"]

    def test_unconfirmed_is_wait(self):
        td = self._today(confirmed=False)
        assert td["action"] == "WAIT"
        assert td["theme_state"] == "UNCONFIRMED"


class TestRejectReasons:
    def test_etf_liquidity(self):
        assert "流动性不足" in rec._etf_reject_reason({"reason_codes": ["low_liquidity"]})
        assert "未达趋势门" in rec._etf_reject_reason({"reason_codes": ["below_trend_gate"]})
        assert "同类方向" in rec._etf_reject_reason({"reason_codes": ["dedup_lost"]})

    def test_stock_risk(self):
        a = {"reason_codes": ["risk_warning"], "risk_flags": ["跌破MA20"], "selection_status": "available"}
        assert "跌破MA20" in rec._stock_reject_reason(a)

    def test_stock_missing(self):
        assert "数据缺失" in rec._stock_reject_reason({"selection_status": "unavailable"})

    def test_stock_qualified(self):
        assert "待主题确认" in rec._stock_reject_reason({"state": "QUALIFIED", "selection_status": "available"})

    def test_stock_breakdown(self):
        """趋势/主题都过但破位（BREAKDOWN）→ 中期趋势破坏，暂不买入。"""
        a = {"state": "RECOMMENDED", "signal": "WAIT", "position_level": "BREAKDOWN",
             "position_pct": -37.6, "selection_status": "available"}
        assert "中期趋势破坏" in rec._stock_reject_reason(a)
        assert "37.6" in rec._stock_reject_reason(a)

    def test_etf_breakdown_position_context(self):
        """v0.10：ETF 破位原因带 position 上下文（如「现价深破 60 日线 37.6%」）。"""
        a = {"reason_codes": ["below_trend_gate"], "recommended": False,
             "position_level": "BREAKDOWN", "position_pct": -37.6}
        r = rec._etf_reject_reason(a)
        assert "未达趋势门" in r
        assert "37.6" in r

    def test_etf_hold_position_context(self):
        a = {"reason_codes": ["theme_confirmed"], "recommended": False,
             "position_level": "HIGH", "position_pct": 12.0, "signal": "HOLD"}
        r = rec._etf_reject_reason(a)
        assert "12" in r and "追高" in r

    def test_stock_hold(self):
        """追高（HOLD）文案按 ma60_deviation 措辞。"""
        a = {"state": "RECOMMENDED", "signal": "HOLD", "position_level": "HIGH",
             "position_pct": 18.5, "selection_status": "available"}
        assert "追高不买" in rec._stock_reject_reason(a)


class TestMonitorConclusion:
    """v0.9.0 附录监控表结论：四段信号优先，state 仅作主题门控区分。"""

    def _c(self, **kw):
        base = {"selection_status": "available", "state": "RECOMMENDED", "signal": "BUY",
                "trend_status": "A", "data_status": "current", "reason": "重点观察"}
        base.update(kw)
        return base

    def test_signal_buy_confirmed_recommended(self):
        from src.selection.report import _monitor_conclusion
        assert _monitor_conclusion(self._c(signal="STRONG_BUY")) == "推荐"
        assert _monitor_conclusion(self._c(signal="BUY")) == "推荐"

    def test_signal_buy_but_theme_unconfirmed_qualified(self):
        """信号 BUY 但主题未确认（state=QUALIFIED）→ 合格（待主题确认），不是推荐。"""
        from src.selection.report import _monitor_conclusion
        assert _monitor_conclusion(self._c(signal="BUY", state="QUALIFIED")) == "合格"

    def test_signal_hold(self):
        """趋势成立但历史高位 → 持有，不追高（不是推荐）。"""
        from src.selection.report import _monitor_conclusion
        c = self._c(signal="HOLD", position_level="HIGH", position_pct=98.9)
        assert _monitor_conclusion(c) == "持有"

    def test_signal_breakdown(self):
        """趋势/主题都过但破位（BREAKDOWN）→ 破位，禁止买入。"""
        from src.selection.report import _monitor_conclusion
        c = self._c(signal="WAIT", position_level="BREAKDOWN", position_pct=-37.6)
        assert _monitor_conclusion(c) == "破位"

    def test_signal_watch(self):
        from src.selection.report import _monitor_conclusion
        assert _monitor_conclusion(self._c(signal="WATCH")) == "观察"

    def test_signal_wait_stale_or_weak(self):
        from src.selection.report import _monitor_conclusion
        assert _monitor_conclusion(self._c(signal="WAIT", data_status="stale")) == "等待"
        assert _monitor_conclusion(self._c(signal="WAIT", trend_status="C")) == "等待"
        assert _monitor_conclusion(self._c(signal="WAIT", trend_status="B")) == "观察"

    def test_unavailable_waits(self):
        from src.selection.report import _monitor_conclusion
        assert _monitor_conclusion(self._c(selection_status="unavailable", signal="BUY")) == "等待"

    def test_no_signal_fallback_state(self):
        """无信号（历史/降级路径）→ 回退 state 逻辑。"""
        from src.selection.report import _monitor_conclusion
        assert _monitor_conclusion(self._c(signal="")) == "推荐"
        assert _monitor_conclusion(self._c(signal="", state="QUALIFIED")) == "合格"
