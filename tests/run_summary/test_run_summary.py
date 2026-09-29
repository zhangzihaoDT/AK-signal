"""Run-day Summary 单元测试：纯汇总逻辑 + renderer（只读消费，不碰磁盘）。"""

from __future__ import annotations

from src.run_summary.summary import build_summary, render_markdown, render_text


def _selection(*, recommended_actions=None, themes=None):
    if themes is None:
        themes = [
            {
                "theme": "high_cashflow",
                "theme_label": "高现金流资产",
                "bucket": "quality",
                "bucket_label": "质量",
                "confirmed": True,
                "today": {"theme_state": "CONFIRMED", "action": "BUY",
                          "n_recommended_etf": 1, "eligible_etf_count": 4,
                          "etf_pool_total": 32},
                "why": {"confirmation_reason": "4/5 个 Tier 已确认"},
            },
            {
                "theme": "ai_infrastructure",
                "theme_label": "AI 基础设施",
                "bucket": "core",
                "bucket_label": "核心",
                "confirmed": False,
                "today": {"theme_state": "UNCONFIRMED", "action": "WAIT",
                          "n_recommended_etf": 0, "eligible_etf_count": 12,
                          "etf_pool_total": 102},
            },
        ]
    layer3 = {
        "action": {"level": "BUY", "direction_label": "质量",
                   "theme_label": "高现金流资产",
                   "summary": "今日方向：买入 质量 · 高现金流资产"},
        "buckets": [{"bucket": "quality", "bucket_label": "质量", "themes": themes}],
    }
    if recommended_actions is not None:
        layer3["recommended_actions"] = recommended_actions
    else:
        layer3["buckets"][0]["themes"][0]["recommendation"] = {
            "etf": [{"code": "159611", "name": "电力ETF广发", "asset_type": "etf",
                     "recommended": True, "role": "CORE_ETF", "rps15": 82.2,
                     "signal": "BUY", "position_level": "MID"}]
        }
    return {"trade_date": "20260928", "layer3": layer3}


def _radar():
    return {
        "summary": {"opportunity_count": 57, "mapping_gap_count": 0,
                    "unclassified_count": 21, "unmapped_count": 1121,
                    "full_market_count": 1244},
        "candidate_themes": [
            {"candidate_theme_key": "bank.a_share", "label": "银行", "market_scope": "a_share",
             "n_etfs": 1, "median_rps15": 82.8,
             "representative": {"fund_code": "512700", "fund_name": "银行ETF南方"}},
            {"candidate_theme_key": "overseas_tech.overseas", "label": "海外科技",
             "market_scope": "overseas", "n_etfs": 1, "median_rps15": 100.0,
             "representative": {"fund_code": "159509", "fund_name": "纳指科技ETF景顺"}},
        ],
        "broad_beta": [
            {"fund_code": "562660", "fund_name": "中证2000ETF华夏", "rps15": 95.5,
             "trend_state": "STRONG_WATCH"},
            {"fund_code": "159687", "fund_name": "亚太精选ETF南方", "rps15": 93.8,
             "trend_state": "STRONG_WATCH"},
        ],
    }


class TestBuildSummary:
    def test_recommendations_from_recommended_actions(self):
        sel = _selection(recommended_actions=[
            {"code": "159611", "name": "电力ETF广发", "asset_type": "etf", "role": "CORE_ETF",
             "theme": "high_cashflow", "recommended": True, "rps15": 82.2, "signal": "BUY",
             "position_level": "MID", "lane3_transition_state": "POST_TRANSITION"},
        ])
        s = build_summary(trade_date="20260928", selection=sel, radar=_radar())
        assert len(s["recommendations"]) == 1
        rec = s["recommendations"][0]
        assert rec["code"] == "159611"
        assert rec["theme_label"] == "高现金流资产"
        assert rec["signal"] == "BUY"
        # 事实原值不被覆盖
        assert rec["rps15"] == 82.2

    def test_recommendations_fallback_scans_themes(self):
        s = build_summary(trade_date="20260928", selection=_selection(), radar=_radar())
        assert [r["code"] for r in s["recommendations"]] == ["159611"]

    def test_cross_theme_recommendations_dedup(self):
        dup = {"code": "159611", "name": "电力ETF广发", "asset_type": "etf", "recommended": True}
        sel = _selection(recommended_actions=[dup, dict(dup)])
        s = build_summary(trade_date="20260928", selection=sel, radar=_radar())
        assert len(s["recommendations"]) == 1

    def test_theme_states_unconfirmed_flagged(self):
        s = build_summary(trade_date="20260928", selection=_selection(), radar=_radar())
        states = {t["theme"]: t for t in s["theme_states"]}
        assert states["high_cashflow"]["confirmed"] is True
        assert states["ai_infrastructure"]["confirmed"] is False

    def test_opportunities_sorted_by_median_rps_desc(self):
        s = build_summary(trade_date="20260928", selection=_selection(), radar=_radar())
        keys = [t["candidate_theme_key"] for t in s["opportunities"]["candidate_themes"]]
        assert keys == ["overseas_tech.overseas", "bank.a_share"]
        assert s["opportunities"]["candidate_themes"][1]["market_scope_label"] == "A股"

    def test_broad_beta_sorted_by_rps_desc(self):
        s = build_summary(trade_date="20260928", selection=_selection(), radar=_radar())
        codes = [x["code"] for x in s["opportunities"]["broad_beta"]]
        assert codes == ["562660", "159687"]

    def test_missing_artifacts_are_soft(self):
        s = build_summary(trade_date="20260928", selection=None, radar=None)
        assert len(s["missing"]) == 2
        assert s["recommendations"] == []
        assert s["opportunities"]["candidate_themes"] == []


class TestRenderers:
    def test_text_has_three_sections(self):
        s = build_summary(trade_date="20260928", selection=_selection(), radar=_radar())
        txt = render_text(s)
        assert "① 今日推荐" in txt
        assert "② 观察范围外机会" in txt
        assert "③ 宽基指数值得研究" in txt
        assert "电力ETF广发" in txt
        assert "纳指科技ETF景顺" in txt
        assert "中证2000ETF华夏" in txt

    def test_text_strips_redundant_direction_prefix(self):
        s = build_summary(trade_date="20260928", selection=_selection(), radar=_radar())
        txt = render_text(s)
        assert "方向：买入 质量 · 高现金流资产" in txt
        assert "方向：今日方向" not in txt

    def test_markdown_renders_tables(self):
        s = build_summary(trade_date="20260928", selection=_selection(), radar=_radar())
        md = render_markdown(s)
        assert "# Run-day Summary · 20260928" in md
        assert "| 标的 | 角色 | 主题 | RPS15 | 信号 | 位置 |" in md
        assert "| 候选方向 | 市场 | 只数 | 中位 RPS15 | 代表 |" in md
