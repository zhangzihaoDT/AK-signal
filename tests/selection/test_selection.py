from __future__ import annotations

import pandas as pd

from src.selection import selection


def _sample_rotation():
    return pd.DataFrame({
        "fund_code": ["512480", "159819", "515880", "588000"],
        "fund_name": ["半导体ETF国联安", "人工智能ETF易方达", "通信ETF国泰", "芯片ETF华夏"],
        "is_tech": [True, True, True, True],
        "rps15": [90.0, 85.0, 70.0, 88.0],
        "rps20": [88.0, 80.0, 65.0, 84.0],
        "rps60": [60.0, 55.0, 40.0, 50.0],
        "return_5d": [3.0, 2.0, 1.0, 2.5],
        "return_20d": [10.0, 8.0, 5.0, 9.0],
        "rank_change_5d": [20.0, 15.0, 5.0, 18.0],
    })


def _sample_account():
    return pd.DataFrame({
        "fund_code": ["512480", "159819", "515880", "588000"],
        "trend_state": ["BUY_CANDIDATE", "BUY_CANDIDATE", "WATCH", "STRONG_WATCH"],
        "account_tradable": [True, True, True, True],
    })


def _sample_master():
    return pd.DataFrame({
        "fund_code": ["512480", "159819", "515880", "588000"],
        "amount": [5e8, 6e8, 4e8, 5e8],
    })


def _sample_confirmation(confirmed: bool):
    if not confirmed:
        return pd.DataFrame({
            "industry_code": ["801081.SI", "801104.SI", "801102.SI"],
            "industry_name": ["半导体", "软件开发", "通信设备"],
            "RPS15": [30.0, 40.0, 50.0],
            "strength_level": ["弱势", "弱势", "弱势"],
            "participation_rate": [None, None, None],
            "hhi": [None, None, None],
            "top3_share": [None, None, None],
        })
    return pd.DataFrame({
        "industry_code": ["801081.SI", "801083.SI", "801104.SI", "801102.SI"],
        "industry_name": ["半导体", "元件", "软件开发", "通信设备"],
        "RPS15": [92.0, 88.0, 70.0, 50.0],
        "strength_level": ["强势", "观察", "弱势", "弱势"],
        "participation_rate": [0.78, 0.65, None, None],
        "hhi": [0.05, 0.08, None, None],
        "top3_share": [0.28, 0.40, None, None],
    })


class TestDirectionGate:
    def test_unconfirmed_blocks(self):
        rot = _sample_rotation()
        conf = _sample_confirmation(confirmed=False)
        d = selection.evaluate_direction(rot, conf)
        assert d["gate"] == "WATCHLIST_ONLY"

    def test_confirmed_proceeds(self):
        rot = _sample_rotation()
        conf = _sample_confirmation(confirmed=True)
        d = selection.evaluate_direction(rot, conf)
        assert d["gate"] == "PROCEED"


class TestSubthemeEvaluation:
    def test_unconfirmed(self):
        conf = _sample_confirmation(confirmed=False)
        subs = selection.evaluate_subthemes(conf)
        assert subs["ai_core"]["confirmed"] is False

    def test_confirmed_with_structure(self):
        conf = _sample_confirmation(confirmed=True)
        subs = selection.evaluate_subthemes(conf)
        assert subs["ai_core"]["confirmed"] is True
        assert subs["ai_core"]["n_observe"] == 2
        assert subs["ai_core"]["median_participation"] == 0.7
        assert subs["digital_infrastructure"]["confirmed"] is False


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
        etf = selection.select_etf_candidates(_sample_rotation(), _sample_account(), _sample_master(), "ai_core")
        assert not etf.empty
        # 515880 WATCH 被过滤
        assert "515880" not in etf["fund_code"].tolist()
        # 512480/159819/588000 通过（BUY_CANDIDATE/STRONG_WATCH）
        assert set(etf["fund_code"]).issubset({"512480", "159819", "588000"})

    def test_subtheme_keyword_matching(self):
        etf = selection.select_etf_candidates(_sample_rotation(), _sample_account(), _sample_master(), "digital_infrastructure")
        # 通信ETF国泰 虽被趋势门控过滤，但 keyword 匹配本身工作
        matched = selection._match_subtheme("通信ETF国泰")
        assert matched == "digital_infrastructure"

    def test_dedup_keeps_representative(self):
        rot = pd.concat([_sample_rotation(), pd.DataFrame({
            "fund_code": ["159995"], "fund_name": ["半导体ETF鹏华"], "is_tech": [True],
            "rps15": [95.0], "rps20": [90.0], "rps60": [70.0],
            "return_5d": [4.0], "return_20d": [12.0], "rank_change_5d": [25.0],
        })], ignore_index=True)
        acct = pd.concat([_sample_account(), pd.DataFrame({
            "fund_code": ["159995"], "trend_state": ["BUY_CANDIDATE"], "account_tradable": [True],
        })], ignore_index=True)
        master = pd.concat([_sample_master(), pd.DataFrame({
            "fund_code": ["159995"], "amount": [7e8],
        })], ignore_index=True)
        etf = selection.select_etf_candidates(rot, acct, master, "ai_core")
        dedup = selection._dedup_etf(etf)
        # 两个半导体ETF 去重后只留评分最高一只
        semis = dedup[dedup["fund_name"].str.contains("半导体")]
        assert len(semis) <= 1


class TestBuildCandidates:
    def test_unconfirmed_output(self):
        c = selection.build_candidates(
            rotation_df=_sample_rotation(),
            account_df=_sample_account(),
            master_df=_sample_master(),
            confirmation_df=_sample_confirmation(confirmed=False),
            universe_items=[],
            stock_trend_report=pd.DataFrame(),
        )
        assert c["direction_gate"] == "WATCHLIST_ONLY"
        assert all(s["expression"] == "WATCHLIST_ONLY" for s in c["subthemes"])

    def test_confirmed_expression(self):
        c = selection.build_candidates(
            rotation_df=_sample_rotation(),
            account_df=_sample_account(),
            master_df=_sample_master(),
            confirmation_df=_sample_confirmation(confirmed=True),
            universe_items=[],
            stock_trend_report=pd.DataFrame(),
        )
        ai_core = next(s for s in c["subthemes"] if s["subtheme"] == "ai_core")
        assert ai_core["confirmed"] is True
        assert ai_core["expression"] in {"ETF_PRIORITY", "LEADER_PRIORITY", "ETF_CORE_PLUS_LEADER"}
