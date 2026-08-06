from __future__ import annotations

import pytest
import pandas as pd

from src.selection import selection


def _sample_rotation():
    return pd.DataFrame({
        "fund_code": ["512480", "159819", "515880", "588000", "515210", "561560"],
        "fund_name": ["半导体ETF国联安", "人工智能ETF易方达", "通信ETF国泰", "芯片ETF华夏",
                      "钢铁ETF国泰", "电力ETF华泰柏瑞"],
        "rps15": [90.0, 85.0, 70.0, 88.0, 82.0, 76.0],
        "rps20": [88.0, 80.0, 65.0, 84.0, 78.0, 72.0],
        "rps60": [60.0, 55.0, 40.0, 50.0, 45.0, 42.0],
        "return_5d": [3.0, 2.0, 1.0, 2.5, 2.0, 1.5],
        "return_20d": [10.0, 8.0, 5.0, 9.0, 6.0, 5.0],
        "rank_change_5d": [20.0, 15.0, 5.0, 18.0, 10.0, 8.0],
    })


def _sample_account():
    return pd.DataFrame({
        "fund_code": ["512480", "159819", "515880", "588000", "515210", "561560"],
        "trend_state": ["BUY_CANDIDATE", "BUY_CANDIDATE", "WATCH", "STRONG_WATCH",
                        "BUY_CANDIDATE", "STRONG_WATCH"],
        "account_tradable": [True, True, True, True, True, True],
    })


def _sample_master():
    return pd.DataFrame({
        "fund_code": ["512480", "159819", "515880", "588000", "515210", "561560"],
        "amount": [5e8, 6e8, 4e8, 5e8, 6e8, 4e8],
    })


def _sample_confirmation(confirmed: bool):
    if not confirmed:
        return pd.DataFrame({
            "industry_code": ["801081.SI", "801083.SI", "801102.SI", "801161.SI", "801223.SI"],
            "industry_name": ["半导体", "元件", "通信设备", "电力", "通信服务"],
            "RPS15": [30.0, 40.0, 50.0, 45.0, 35.0],
            "strength_level": ["弱势", "弱势", "弱势", "弱势", "弱势"],
            "participation_rate": [None, None, None, None, None],
            "hhi": [None, None, None, None, None],
            "top3_share": [None, None, None, None, None],
        })
    return pd.DataFrame({
        "industry_code": ["801081.SI", "801083.SI", "801102.SI", "801161.SI", "801223.SI"],
        "industry_name": ["半导体", "元件", "通信设备", "电力", "通信服务"],
        "RPS15": [92.0, 88.0, 70.0, 85.0, 45.0],
        "strength_level": ["强势", "观察", "弱势", "观察", "弱势"],
        "participation_rate": [0.78, 0.65, None, 0.55, None],
        "hhi": [0.05, 0.08, None, 0.12, None],
        "top3_share": [0.28, 0.40, None, 0.50, None],
    })


class TestDirectionGate:
    def test_unconfirmed_blocks(self):
        rot = _sample_rotation()
        conf = _sample_confirmation(confirmed=False)
        metas = selection.evaluate_themes(conf, rot)
        d = selection.evaluate_direction(metas)
        assert d["gate"] == "WATCHLIST_ONLY"

    def test_confirmed_proceeds(self):
        rot = _sample_rotation()
        conf = _sample_confirmation(confirmed=True)
        metas = selection.evaluate_themes(conf, rot)
        d = selection.evaluate_direction(metas)
        assert d["gate"] == "PROCEED"
        assert "ai_infrastructure" in d["confirmed_themes"]


class TestThemeEvaluation:
    def test_unconfirmed(self):
        conf = _sample_confirmation(confirmed=False)
        metas = selection.evaluate_themes(conf, pd.DataFrame())
        assert metas["ai_infrastructure"]["confirmed"] is False
        assert metas["high_cashflow"]["confirmed"] is False

    def test_confirmed_with_structure(self):
        conf = _sample_confirmation(confirmed=True)
        metas = selection.evaluate_themes(conf, pd.DataFrame())
        assert metas["ai_infrastructure"]["confirmed"] is True
        assert metas["ai_infrastructure"]["n_observe"] == 2
        # 原始中位数保留，不因展示舍入而损失精度（[0.78, 0.65] → 0.715）
        assert metas["ai_infrastructure"]["median_participation"] == pytest.approx(0.715)
        assert metas["high_cashflow"]["confirmed"] is True  # 电力观察区

    def test_bucket_annotation(self):
        conf = _sample_confirmation(confirmed=True)
        metas = selection.evaluate_themes(conf, pd.DataFrame())
        assert metas["ai_infrastructure"]["bucket"] == "core"
        assert metas["high_cashflow"]["bucket"] == "quality"


class TestExpressionDecision:
    def test_watchlist_only_when_unconfirmed(self):
        meta = {"confirmed": False}
        assert selection.decide_expression(meta)["expression"] == "WATCHLIST_ONLY"

    def test_etf_priority_broad(self):
        meta = {"confirmed": True, "median_participation": 0.78, "median_hhi": 0.05, "median_top3_share": 0.28}
        assert selection.decide_expression(meta)["expression"] == "ETF_PRIORITY"

    def test_leader_priority_concentrated(self):
        meta = {"confirmed": True, "median_participation": 0.32, "median_hhi": 0.30, "median_top3_share": 0.71}
        assert selection.decide_expression(meta)["expression"] == "LEADER_PRIORITY"

    def test_core_plus_leader_diffusion(self):
        meta = {"confirmed": True, "median_participation": 0.45, "median_hhi": 0.10, "median_top3_share": 0.45}
        assert selection.decide_expression(meta)["expression"] == "ETF_CORE_PLUS_LEADER"


class TestEtfSelection:
    def test_trend_and_liquidity_gate(self):
        etf = selection.select_etf_candidates(_sample_rotation(), _sample_account(), _sample_master(), "ai_infrastructure")
        assert not etf.empty
        # 515880 WATCH 被过滤
        assert "515880" not in etf["fund_code"].tolist()
        # 512480/159819/588000 通过（BUY_CANDIDATE/STRONG_WATCH）
        assert set(etf["fund_code"]).issubset({"512480", "159819", "588000"})

    def test_theme_keyword_matching(self):
        # 电力/电信/公用事业 → high_cashflow；军工/煤炭/券商 已移出两方向
        assert selection.match_theme("电力ETF华泰柏瑞") == "high_cashflow"
        assert selection.match_theme("电信ETF易方达") == "high_cashflow"
        assert selection.match_theme("公用事业ETF华夏") == "high_cashflow"
        assert selection.match_theme("半导体ETF国联安") == "ai_infrastructure"
        assert selection.match_theme("军工ETF国泰") is None
        assert selection.match_theme("证券ETF国泰") is None

    def test_dedup_keeps_representative(self):
        rot = pd.concat([_sample_rotation(), pd.DataFrame({
            "fund_code": ["159995"], "fund_name": ["半导体ETF鹏华"],
            "rps15": [95.0], "rps20": [90.0], "rps60": [70.0],
            "return_5d": [4.0], "return_20d": [12.0], "rank_change_5d": [25.0],
        })], ignore_index=True)
        acct = pd.concat([_sample_account(), pd.DataFrame({
            "fund_code": ["159995"], "trend_state": ["BUY_CANDIDATE"], "account_tradable": [True],
        })], ignore_index=True)
        master = pd.concat([_sample_master(), pd.DataFrame({
            "fund_code": ["159995"], "amount": [7e8],
        })], ignore_index=True)
        etf = selection.select_etf_candidates(rot, acct, master, "ai_infrastructure")
        dedup = selection._dedup_etf(etf)
        # 两个半导体ETF 去重后只留评分最高一只
        semis = dedup[dedup["fund_name"].str.contains("半导体")]
        assert len(semis) <= 1


class TestBuildCandidates:
    def _build(self, confirmed: bool):
        return selection.build_candidates(
            rotation_df=_sample_rotation(),
            account_df=_sample_account(),
            master_df=_sample_master(),
            confirmation_df=_sample_confirmation(confirmed=confirmed),
            universe_items=[],
            trend_df=pd.DataFrame(),
        )

    def test_unconfirmed_output(self):
        c = self._build(confirmed=False)
        assert c["version"] == "0.4.3"
        assert c["direction"]["gate"] == "WATCHLIST_ONLY"
        bucket_keys = [b["bucket"] for b in c["buckets"]]
        assert bucket_keys == ["core", "quality"]
        all_themes = [t for b in c["buckets"] for t in b["themes"]]
        assert all(t["expression"] == "WATCHLIST_ONLY" for t in all_themes)

    def test_confirmed_expression(self):
        c = self._build(confirmed=True)
        assert c["direction"]["gate"] == "PROCEED"
        ai = None
        for b in c["buckets"]:
            for t in b["themes"]:
                if t["theme"] == "ai_infrastructure":
                    ai = t
        assert ai is not None
        assert ai["confirmed"] is True
        assert ai["expression"] in {"ETF_PRIORITY", "LEADER_PRIORITY", "ETF_CORE_PLUS_LEADER"}
        assert ai["bucket"] == "core"

    def test_summary_fields(self):
        c = self._build(confirmed=True)
        s = c["summary"]
        assert "confirmed_themes" in s
        assert "recommended_actions" in s

    def test_action_is_direction_only(self):
        """顶层 Action 只回答方向，不枚举具体标的（标的在下层 themes）。"""
        c = self._build(confirmed=True)
        a = c["action"]
        assert a["level"] in {"BUY", "OBSERVE"}
        assert a["theme"] in {"ai_infrastructure", "high_cashflow"}
        assert a["direction"] in {"core", "quality"}
        assert "direction_label" in a and "theme_label" in a
        assert "expression" in a and "expression_label" in a
        # 不在 Action 里出现具体标的名称 / 推荐计数
        assert "首选" not in a.get("summary", "")
        assert "推荐标的" not in a.get("summary", "")

    def test_action_wait_when_none_confirmed(self):
        c = self._build(confirmed=False)
        a = c["action"]
        assert a["level"] == "WAIT"
        assert a["theme"] == ""
        assert a["summary"].startswith("今日方向")


class TestCrossThemeAssets:
    def test_recommended_dedup_cross_theme(self):
        """同一 ETF 在多个 theme 推荐 → 聚合去重，保留首个（primary=首个 bucket）。"""
        t1 = {"theme": "ai_infrastructure",
              "core_etf": [{"asset_type": "etf", "code": "515880", "name": "通信ETF国泰", "theme": "ai_infrastructure", "recommended": True}]}
        t2 = {"theme": "high_cashflow",
              "core_etf": [{"asset_type": "etf", "code": "515880", "name": "通信ETF国泰", "theme": "high_cashflow", "recommended": True}]}
        t3 = {"theme": "high_cashflow",
              "core_etf": [{"asset_type": "etf", "code": "561560", "name": "电力ETF华泰柏瑞", "theme": "high_cashflow", "recommended": True}]}
        acts = selection._collect_recommended_actions([t1, t2, t3])
        codes = [(a["asset_type"], a["code"]) for a in acts]
        assert len(codes) == len(set(codes))
        assert len(acts) == 2
        assert acts[0]["theme"] == "ai_infrastructure"  # primary 归属保留

    def test_cross_theme_assets_detection(self, tmp_path):
        p = tmp_path / "u.yaml"
        p.write_text(
            "themes:\n"
            "  ai_infrastructure:\n"
            "    label: AI\n"
            "    tiers:\n"
            "      - key: theme_etf\n"
            "        label: T\n"
            "        assets:\n"
            "          - {symbol: 515880, name: 通信ETF, market: CN}\n"
            "  high_cashflow:\n"
            "    label: 高现金流资产\n"
            "    tiers:\n"
            "      - key: theme_etf\n"
            "        label: T\n"
            "        assets:\n"
            "          - {symbol: 515880, name: 通信ETF, market: CN}\n"
            "          - {symbol: 600941, name: 中国移动, market: CN}\n"
        )
        from src.selection.universe import cross_theme_assets
        assert cross_theme_assets(p) == {"515880": ["ai_infrastructure", "high_cashflow"]}

    def test_detect_unregistered_themes(self, tmp_path):
        p = tmp_path / "u.yaml"
        p.write_text(
            "themes:\n"
            "  ai_infrastructure:\n"
            "    label: AI\n"
            "    tiers: []\n"
            "  ghost:\n"
            "    label: 未注册主题\n"
            "    tiers: []\n"
        )
        from src.selection.universe import detect_unregistered_themes
        assert detect_unregistered_themes(p) == ["ghost"]


class TestConfirmationBreadth:
    def test_broad_vs_narrow(self):
        from src.selection.selection import classify_confirmation_breadth
        assert classify_confirmation_breadth(True, 3, 5, 90.0)[0] == "BROAD_CONFIRMED"
        assert classify_confirmation_breadth(True, 1, 5, 82.0)[0] == "NARROW_CONFIRMED"
        assert classify_confirmation_breadth(False, 0, 5, 75.0)[0] == "WATCH"
        assert classify_confirmation_breadth(False, 0, 5, 50.0)[0] == "UNCONFIRMED"

    def test_evidence_picks_observing_industry(self):
        import pandas as pd
        from src.selection.selection import _confirm_evidence
        conf = pd.DataFrame([
            {"industry_name": "航运港口", "RPS15": 82.3, "strength_level": "观察"},
            {"industry_name": "电力", "RPS15": 61.3, "strength_level": "中性"},
        ])
        ev = _confirm_evidence(conf, confirmed=True)
        assert ev["industry"] == "航运港口"
        assert ev["rps15"] == 82.3


class TestAmountScore:
    def test_fixed_threshold_absolute(self):
        from src.common.spec.loaders import load_etf_selection_spec
        from src.selection.selection import _amount_score
        amt = load_etf_selection_spec().amount_score
        assert _amount_score(50_000_000, amt) == 0.0          # floor → 0
        assert _amount_score(500_000_000, amt) == 100.0       # reference → cap
        assert _amount_score(5_000_000_000, amt) == 100.0     # 超过 → cap
        # 同一成交额 → 同一分数（不依赖候选集合）
        a1 = _amount_score(130_000_000, amt)
        a2 = _amount_score(130_000_000, amt)
        assert a1 == a2
        assert 0 < a1 < 100


class TestStaleDegradation:
    def test_stale_does_not_recommend(self):
        from src.selection.selection import select_stock_watchlist
        from src.selection.universe import UniverseItem
        from src.trend_engine.asset import Asset
        item = UniverseItem(asset=Asset(symbol="600900", name="长江电力", market="CN", category="leader"),
                            bucket="core", bucket_label="核心", theme="high_cashflow",
                            theme_label="高现金流资产", tier="leader", tier_label="龙头")
        trend = pd.DataFrame([{
            "symbol": "600900", "data_status": "stale", "score_trend": 100.0,
            "watch_level": "A", "action": "重点观察", "risk_flags": "", "lag_days": 2,
        }])
        leaders, _, _ = select_stock_watchlist([item], "high_cashflow", trend, theme_confirmed=True)
        c = leaders[0]
        assert c.state == "WATCH"          # stale → 降级
        assert c.recommended is False
        assert "stale_data" in c.reason_codes
        assert c.score_trend == 100.0      # 事实原值保留（不改分）
        assert "滞后" in c.reason
