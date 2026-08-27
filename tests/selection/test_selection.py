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
        # v0.9.2：Theme 层无 WATCH——接近观察门仍是 UNCONFIRMED，label 标注「未确认 · 接近观察门」
        state, label = classify_confirmation_breadth(False, 0, 5, 75.0)
        assert state == "UNCONFIRMED"
        assert "接近观察门" in label
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


class TestEtfMinAmountConfig:
    """回归保护：ETF_MIN_AMOUNT 必须来自 config/strategies.yaml，禁止硬编码覆盖。

    曾出现 selection.py 在模块加载后硬编码 `ETF_MIN_AMOUNT = 50_000_000`
    覆盖 config 值导致 config drift 的缺陷（改 strategies.yaml 不生效）。
    """

    def test_module_constant_matches_config(self):
        from src.common.spec.loaders import load_etf_selection_spec
        # 静态回归：模块常量与 config 值一致。若未来有人再硬编码覆盖且 config
        # 值不同，此断言失败 → 强制回归到 config 单一来源。
        assert selection.ETF_MIN_AMOUNT == float(load_etf_selection_spec().min_amount)

    def test_etf_min_amount_single_source(self, monkeypatch):
        """_etf_min_amount() 每次从 spec loader 读取（函数内 import，monkeypatch 生效）。"""
        from src.common.spec import loaders
        from src.common.spec.model import AmountScoreSpec, EtfSelectionSpec
        fake = EtfSelectionSpec(
            allowed_trend_states=("BUY_CANDIDATE", "STRONG_WATCH"),
            watch_allowed_trend_states=("BUY_CANDIDATE", "STRONG_WATCH", "WATCH"),
            min_amount=123_000_000,
            ranking_weights={"rps15": 0.55, "rps20": 0.25, "amount_score": 0.20},
            amount_score=AmountScoreSpec(method="log_threshold", floor=5e7, reference=5e8, cap=100.0),
        )
        monkeypatch.setattr(loaders, "load_etf_selection_spec", lambda: fake)
        assert selection._etf_min_amount() == 123_000_000

    def test_theme_pool_liquidity_gate_uses_constant(self, monkeypatch):
        """theme_etf_pool 的流动性标记跟随模块常量（运行时查模块命名空间，非内嵌字面量）。"""
        pool = selection.theme_etf_pool(_sample_rotation(), _sample_account(), _sample_master(),
                                        "ai_infrastructure")
        by_code = {c.code: c for c in pool}
        assert "liquidity_ok" in by_code["512480"].reason_codes
        monkeypatch.setattr(selection, "ETF_MIN_AMOUNT", 9e8)
        pool2 = selection.theme_etf_pool(_sample_rotation(), _sample_account(), _sample_master(),
                                         "ai_infrastructure")
        by_code2 = {c.code: c for c in pool2}
        assert "low_liquidity" in by_code2["512480"].reason_codes

    def test_select_etf_candidates_respects_min_amount_param(self):
        """select_etf_candidates 的 min_amount 参数独立于趋势门控生效。"""
        etf = selection.select_etf_candidates(_sample_rotation(), _sample_account(), _sample_master(),
                                              "ai_infrastructure", min_amount=5.5e8)
        codes = set(etf["fund_code"])
        # 趋势门内三只（512480/159819/588000）：成交额 5e8 < 5.5e8 被流动性过滤，仅 159819(6e8) 保留
        assert codes == {"159819"}


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


class TestFourStageIntegration:
    """v0.9.0 四段信号接入 select_stock_watchlist 的端到端行为。"""

    def _items(self, symbols: list[str]):
        from src.selection.universe import UniverseItem
        from src.trend_engine.asset import Asset
        out = []
        for s in symbols:
            out.append(UniverseItem(asset=Asset(symbol=s, name=s, market="CN", category="leader"),
                                    bucket="core", bucket_label="核心", theme="ai_infrastructure",
                                    theme_label="AI 基础设施", tier="leader", tier_label="龙头"))
        return out

    def _trend(self, symbol: str, score: float):
        return {"symbol": symbol, "data_status": "current", "score_trend": score,
                "watch_level": "A", "action": "重点观察", "risk_flags": ""}

    def test_leader_low_position_strong_buy(self, monkeypatch):
        """LEADER（rank1）× LOW → STRONG_BUY，推荐。"""
        monkeypatch.setattr("src.selection.four_stage.load_stock_close_history",
                            lambda market, symbol, td=None, lb=None: [100.0] * 60 + [90.0])
        items = self._items(["000001"])
        leaders, _, _ = selection.select_stock_watchlist(
            items, "ai_infrastructure", pd.DataFrame([self._trend("000001", 90.0)]), theme_confirmed=True)
        c = leaders[0]
        assert c.theme_rank == 1
        assert c.leadership_level == "LEADER"
        assert c.position_level == "LOW"
        assert c.signal == "STRONG_BUY"
        assert c.recommended is True

    def test_leader_high_position_hold_not_recommended(self, monkeypatch):
        """LEADER × HIGH → HOLD：趋势/主题都成立但偏离 60 日线过高不追高，不推荐。"""
        monkeypatch.setattr("src.selection.four_stage.load_stock_close_history",
                            lambda market, symbol, td=None, lb=None: [100.0] * 60 + [120.0])
        items = self._items(["000001"])
        leaders, _, _ = selection.select_stock_watchlist(
            items, "ai_infrastructure", pd.DataFrame([self._trend("000001", 90.0)]), theme_confirmed=True)
        c = leaders[0]
        assert c.state == selection.STOCK_STATE_RECOMMENDED  # 趋势/主题确认状态保留
        assert c.position_level == "HIGH"
        assert c.signal == "HOLD"
        assert c.recommended is False                          # 信号门控：不追高
        assert "signal_hold" in c.reason_codes

    def test_leader_breakdown_position_not_recommended(self, monkeypatch):
        """LEADER × BREAKDOWN → WAIT：深破 60 日线（趋势破坏），即使 LEADER 也禁止买入。"""
        monkeypatch.setattr("src.selection.four_stage.load_stock_close_history",
                            lambda market, symbol, td=None, lb=None: [100.0] * 60 + [80.0])
        items = self._items(["000001"])
        leaders, _, _ = selection.select_stock_watchlist(
            items, "ai_infrastructure", pd.DataFrame([self._trend("000001", 90.0)]), theme_confirmed=True)
        c = leaders[0]
        assert c.position_level == "BREAKDOWN"
        assert c.signal == "WAIT"          # 破位不给任何买入信号
        assert c.recommended is False
        assert c.position_pct is not None and c.position_pct < -15.0

    def test_low_position_no_trend_still_wait(self, monkeypatch):
        """纪律：历史低位不产生趋势——趋势不成立（BELOW trend）即使 LOW 也 WAIT。"""
        monkeypatch.setattr("src.selection.four_stage.load_stock_close_history",
                            lambda market, symbol, td=None, lb=None: [100.0] * 60 + [90.0])
        items = self._items(["000001"])
        trend = pd.DataFrame([{"symbol": "000001", "data_status": "current", "score_trend": 30.0,
                               "watch_level": "C", "action": "观察", "risk_flags": ""}])
        leaders, _, _ = selection.select_stock_watchlist(
            items, "ai_infrastructure", trend, theme_confirmed=True)
        c = leaders[0]
        assert c.state == selection.STOCK_STATE_WATCH
        assert c.position_level == "LOW"    # 位置确实是低位
        assert c.signal == "WAIT"           # 但无趋势 → WAIT
        assert c.recommended is False

    def test_theme_rank_by_trend_score(self, monkeypatch):
        """主题内排名按 score_trend：rank≤3 → LEADER，rank4+ → CORE（leader_rank_max=3）。"""
        monkeypatch.setattr("src.selection.four_stage.load_stock_close_history",
                            lambda market, symbol, td=None, lb=None: [100.0] * 60 + [90.0])
        items = self._items(["000001", "600001", "600002", "600003"])
        trend = pd.DataFrame([
            self._trend("000001", 95.0), self._trend("600001", 90.0),
            self._trend("600002", 85.0), self._trend("600003", 75.0),
        ])
        leaders, _, _ = selection.select_stock_watchlist(
            items, "ai_infrastructure", trend, theme_confirmed=True)
        by_code = {c.code: c for c in leaders}
        assert by_code["000001"].theme_rank == 1 and by_code["000001"].leadership_level == "LEADER"
        assert by_code["600003"].theme_rank == 4 and by_code["600003"].leadership_level == "CORE"
        assert by_code["000001"].signal == "STRONG_BUY"  # LEADER × LOW
        assert by_code["600003"].signal == "BUY"         # CORE × LOW

