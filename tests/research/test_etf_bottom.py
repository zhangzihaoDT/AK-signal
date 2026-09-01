"""Study 1 Price Bottom 核心逻辑测试（纯离线，确定性）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research.etf_bottom.states import _pct_rank_rolling, compute_states, extract_events
from src.research.etf_bottom.universe import calibrate_etf_type
from src.research.etf_bottom.returns import ClosePanel
from src.research.etf_bottom.study import count_non_overlap, summarize


def _mk_price(n: int = 900, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.001, 0.02, n)
    # 注入一段深跌+低位区域（-2.5%×80 天，保证 P756 跌破 20% 阈值）
    rets[300:380] = -0.025
    close = 100 * np.cumprod(1 + rets)
    dates = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"date": dates, "close": close, "fund_code": "999999"})


def test_pct_rank_rolling_first_values_nan():
    close = np.arange(1.0, 30.0)
    out = _pct_rank_rolling(close, 20)
    assert np.isnan(out[:19]).all()
    assert np.isnan(out[19:]).sum() == 0
    # 单调递增序列，当前值恒为窗口内最高 → 百分位 100
    assert out[19] == 100.0


def test_compute_states_columns_and_low_zone():
    d = _mk_price()
    s = compute_states(d)
    for col in ["ma20", "ma60", "p756", "dd30", "dd120", "price_low", "price_low_dd30", "above_ma20", "above_ma60", "corp_action"]:
        assert col in s.columns
    # 深跌段内应存在低位
    assert s["price_low"].any()
    # 无份额折算（合成数据）
    assert not s["corp_action"].any()


def test_extract_events_merges_consecutive_low():
    d = _mk_price()
    s = compute_states(d)
    ev = extract_events(s)
    assert len(ev) > 0
    # 事件类型覆盖
    types = {e["event_type"] for e in ev}
    assert "PRICE_LOW" in types
    # entry 日必须是 off→on 转换点：前一日不在低位
    for e in ev:
        if e["event_type"] == "PRICE_LOW":
            idx = e["seg_start_idx"]
            assert idx > 0
            assert not bool(s["price_low"].iloc[idx - 1])
            assert bool(s["price_low"].iloc[idx])
    # MA20_RECOVERY 的 days_low_to_ma20 必须 ≥ 1（上穿，非 entry 当天）
    for e in ev:
        if e["event_type"] == "MA20_RECOVERY":
            assert e["days_low_to_ma20"] is not None and e["days_low_to_ma20"] >= 1


def test_extract_events_censored_recovery():
    d = _mk_price()
    # 构造永不恢复的样本：持续下跌到末尾
    n = 900
    rets = np.linspace(0, -0.004, n)
    close = 100 * np.cumprod(1 + rets)
    dates = pd.bdate_range("2023-01-02", periods=n)
    dd = pd.DataFrame({"date": dates, "close": close, "fund_code": "999998"})
    s = compute_states(dd)
    ev = [e for e in extract_events(s) if e["event_type"] == "PRICE_LOW"]
    assert any(e["days_low_to_ma20"] is None for e in ev)


def test_calibrate_etf_type_manager_suffix_not_exposure():
    # 经理名后缀（ETF 之后）不构成行业暴露
    assert calibrate_etf_type("中证500ETF中银证券", "")["calibrated_type"] == "broad"
    assert calibrate_etf_type("创业板ETF中银证券", "")["calibrated_type"] == "broad"
    # 真实行业暴露在 ETF 之前
    assert calibrate_etf_type("创业板新能源ETF富国", "")["calibrated_type"] == "industry"
    assert calibrate_etf_type("证券ETF", "")["calibrated_type"] == "industry"
    assert calibrate_etf_type("港股央企红利ETF永赢", "")["calibrated_type"] == "dividend"


def test_calibrate_etf_type_money_keywords():
    """场内货币基金命名多样（不都带「货币」）→ 通过关键词或 flat-price guardrail 判为 money。"""
    from src.research.etf_bottom.universe import calibrate_etf_type, is_flat_price
    import pandas as pd
    # 关键词层：名字含货币类别 token（不一定有「货币」二字）
    keyword_money = [
        "华安日日鑫ETF", "华宝添益ETF", "银华日利ETF",
        "快钱ETF汇添富", "招商快线ETF", "货币ETF南方", "富国货币ETF",
    ]
    for n in keyword_money:
        assert calibrate_etf_type(n, "")["calibrated_type"] == "money", n
    # 权益/行业/宽基不受影响
    assert calibrate_etf_type("半导体ETF国联安", "")["calibrated_type"] == "industry"
    assert calibrate_etf_type("沪深300ETF", "")["calibrated_type"] == "broad"
    assert calibrate_etf_type("智能汽车ETF富国", "")["calibrated_type"] == "industry"
    # 现金流ETF（自由现金流/现金流指数）是权益资产，不是货币基金
    #（_MONEY_KEYWORDS 的「现金」子串会命中「现金流」，须显式排除，否则被误排除出底部研究）
    for n in ["自由现金流ETF华夏", "现金流ETF长城", "800现金流ETF汇添富"]:
        assert calibrate_etf_type(n, "")["calibrated_type"] != "money", n

    # flat-price guardrail：近零波动资产即使关键词漏判也能被识别
    idx = pd.bdate_range("2023-01-02", periods=400)
    flat = pd.DataFrame({"close": [100.0] * len(idx)}, index=idx)
    assert is_flat_price(flat)
    equity = pd.DataFrame({"close": np.linspace(1.0, 2.5, len(idx))}, index=idx)
    assert not is_flat_price(equity)
    noisy = pd.DataFrame({"close": 1.0 + 0.01 * np.sin(np.arange(len(idx)))}, index=idx)
    assert not is_flat_price(noisy)


def test_close_panel_forward_and_benchmark():
    # 构造两只 ETF 的 close 面板
    idx = pd.bdate_range("2024-01-02", periods=100)
    a = pd.Series(np.arange(100.0, 200.0), index=idx)
    b = pd.Series(np.arange(200.0, 100.0, -1.0), index=idx)
    p = ClosePanel(pd.DataFrame({"A": a, "B": b}))
    fwd = p.forward_returns("A", idx[0], (20, 60))
    assert fwd[20] == pytest.approx(120.0 / 100.0 - 1.0)
    bench = p.benchmark_forward(idx[0], (20,))
    assert bench[20] is not None
    exc = p.excursions("A", idx[0], (20,))
    mfe, mae = exc[20]
    assert mfe is not None and mae is not None


def test_summarize_structure():
    idx = pd.bdate_range("2024-01-02", periods=100)
    a = pd.Series(np.arange(100.0, 200.0), index=idx)
    p = ClosePanel(pd.DataFrame({"A": a}))
    ev = pd.DataFrame([{
        "fund_code": "A", "entry_date": idx[5], "event_type": "PRICE_LOW",
        "days_low_to_ma20": 5, "days_low_to_ma60": None, "etf_type": "broad",
    }])
    aug = pd.DataFrame(ev)
    for h in (20, 60, 120):
        f = p.forward_returns("A", idx[5], (h,))
        aug[f"ret_{h}"] = aug["fund_code"].map(lambda _: f[h])
        aug[f"bench_{h}"] = 0.0
        aug[f"excess_{h}"] = aug[f"ret_{h}"]
        aug[f"mfe_{h}"] = 0.0
        aug[f"mae_{h}"] = 0.0
    summ = summarize(aug)
    assert "PRICE_LOW" in summ
    assert summ["PRICE_LOW"]["_meta"]["n_events"] == 1
    assert count_non_overlap(aug, 20) == 1


# ── Study 1B Deep Stress Robustness ──────────────────────────────

def test_robustness_year_breakdown_and_exclude():
    from src.research.etf_bottom.robustness import year_breakdown, exclude_recent
    # 构造跨两年的事件：2023 正、2024 负
    dates = [pd.Timestamp("2023-06-01"), pd.Timestamp("2024-06-01")]
    ev = pd.DataFrame({
        "fund_code": ["A", "B"],
        "fund_name": ["x", "y"],
        "entry_date": dates,
        "event_type": ["PRICE_LOW_DD30", "PRICE_LOW_DD30"],
        "etf_type": ["industry", "industry"],
        "ret_20": [0.02, -0.02], "ret_60": [0.05, -0.05], "ret_120": [0.10, -0.10],
    })
    yrs = year_breakdown(ev, "PRICE_LOW_DD30")
    assert {y["year"] for y in yrs} == {2023, 2024}
    # exclude_recent：去除 2024 之后（<=2024）应含全部 2 事件
    exc = exclude_recent(ev, "PRICE_LOW_DD30", through_year=2024)
    assert exc["<= 2024"]["n"] == 2
    assert exc["> 2024"]["n"] == 0


def test_robustness_year_cluster_isolates_single_year():
    """构造单一年份正收益、其他年份为 0/负 → 年份块 bootstrap 应显著弱于 ETF 块。"""
    from src.research.etf_bottom.robustness import cluster_bootstrap
    # 2025 年 20 只 ETF 各 1 事件 +0.30；2023/2024 各 2 只 ±0
    rows = []
    for code in [f"E{i:03d}" for i in range(20)]:
        rows.append({"fund_code": code, "entry_date": pd.Timestamp("2025-03-01"),
                     "event_type": "PRICE_LOW_DD30", "etf_type": "industry", "ret_120": 0.30})
    for y in (2023, 2024):
        for code in [f"F{y}{i}" for i in range(2)]:
            rows.append({"fund_code": code, "entry_date": pd.Timestamp(f"{y}-06-01"),
                         "event_type": "PRICE_LOW_DD30", "etf_type": "industry", "ret_120": 0.0})
    ev = pd.DataFrame(rows)
    res = cluster_bootstrap(ev, "PRICE_LOW_DD30", horizon=120, n_boot=200)
    yb = res["year_cluster_bootstrap"]
    # ETF 块（按 ETF 重抽样）显著
    assert res["mean_p_gt0"] >= 0.95
    # 年份块 p 应明显更低（时间聚类暴露单年依赖）
    assert yb["mean_p_gt0"] < res["mean_p_gt0"]


def test_robustness_concentration_counts():
    from src.research.etf_bottom.robustness import concentration
    ev = pd.DataFrame({
        "fund_code": ["A", "A", "B"],
        "fund_name": ["a", "a", "b"],
        "entry_date": pd.to_datetime(["2025-01-01", "2025-02-01", "2025-03-01"]),
        "event_type": ["PRICE_LOW_DD30"] * 3,
        "ret_120": [0.1, 0.2, 0.3],
    })
    c = concentration(ev, "PRICE_LOW_DD30")
    assert c["total_events"] == 3
    assert c["n_etfs"] == 2
    assert c["max_events_per_etf"] == 2
    assert c["top_etfs"][0]["fund_code"] == "A"


def test_robustness_parameter_sensitivity_monotone_signal():
    """参数扫描：更深 DD30 门槛事件数不增加（更严格 → 子集），返回结构完整。"""
    from src.research.etf_bottom.robustness import parameter_sensitivity
    from src.research.etf_bottom.states import compute_states
    # 合成深跌 ETF（先上冲再长下杀，确保 P756 破 20、DD30 深跌发生在段内）
    n = 900
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0005, 0.015, n)
    rets[:200] = 0.004
    rets[300:400] = -0.05
    close = 100 * np.cumprod(1 + rets)
    dates = pd.bdate_range("2023-01-02", periods=n)
    st = compute_states(pd.DataFrame({"date": dates, "close": close, "fund_code": "deep"}))
    assert st["price_low"].sum() > 0, "合成深跌应触发低位"

    res = parameter_sensitivity({"deep": st}, p_low_values=(20.0,), dd30_values=(-0.15, -0.20, -0.25))
    scan = res["scan"]
    assert len(scan) == 3
    # 更深门槛事件数不增加（子集单调）
    counts = [r["n"] for r in scan]
    assert counts[0] >= counts[1] >= counts[2]


# ── Price Bottom Map（横截面低位地图） ───────────────────────────

def _mk_series(prices, start: str = "2025-01-01") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=len(prices)),
        "close": prices,
    })


def test_price_map_deep_bottom_classification():
    """平滑下跌到低位 → DEEP_BOTTOM + long_term_bottom=True。"""
    from src.research.etf_bottom.price_map import compute_row
    import numpy as np
    up = np.linspace(100, 300, 300)
    down = 300 * np.exp(np.linspace(0, np.log(0.5), 80))
    d = _mk_series(np.concatenate([up, down]), "2025-01-01")
    r = compute_row("A", "测试ETF", "broad", d, pd.Timestamp("2026-08-28"))
    assert r["price_pos_60"] <= 20
    assert r["price_pos_120"] <= 20
    assert r["price_pos_360"] <= 20
    assert r["bottom_state"] == "DEEP_BOTTOM"
    assert r["long_term_bottom"] is True


def test_price_map_corp_action_window_level():
    """折算只在 360D 窗口内（120D 外）→ 仅 360D 污染，60/120 保留，整体 UNRELIABLE。"""
    from src.research.etf_bottom.price_map import compute_row
    import numpy as np
    n = 500
    base = np.linspace(100, 160, n)
    base[200] = base[199] * 0.5  # 折算在 idx 200（360D 内、120D 外）
    d = _mk_series(base, "2024-10-01")
    r = compute_row("E", "早前折算", "theme", d, pd.Timestamp("2026-08-28"))
    assert r["unreliable_60"] is False
    assert r["unreliable_120"] is False
    assert r["unreliable_360"] is True
    assert r["price_pos_60"] is not None
    assert r["price_pos_120"] is not None
    assert r["price_pos_360"] is None
    assert r["bottom_state"] == "UNRELIABLE"


def test_price_map_corp_action_in_60d_pollutes_all():
    """折算在 60D 内 → 60⊂120⊂360 三窗口全污染，整体 UNRELIABLE。"""
    from src.research.etf_bottom.price_map import compute_row
    import numpy as np
    n = 500
    base = np.linspace(100, 160, n)
    base[-5] = base[-6] * 0.5  # 折算在最近 60D 内
    d = _mk_series(base, "2024-10-01")
    r = compute_row("F", "近期折算", "theme", d, pd.Timestamp("2026-08-28"))
    assert r["unreliable_60"] is True
    assert r["unreliable_120"] is True
    assert r["unreliable_360"] is True
    assert r["bottom_state"] == "UNRELIABLE"


def test_price_map_states_mutually_exclusive():
    """bottom_state 5 状态互斥：每只 ETF 恰好一个状态，且长期底部=DEEP+RECOVERING。"""
    from src.research.etf_bottom.price_map import build_price_map
    df = build_price_map("2026-08-28")
    assert df["bottom_state"].nunique() <= 5
    # 每行唯一状态
    assert not df.duplicated(subset=["fund_code"]).any()
    # 长期底部 = DEEP + RECOVERING（排除货币/债券）
    lt = df[df["long_term_bottom"] == True]
    allowed = {"DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"}
    assert set(lt["bottom_state"]).issubset(allowed)
    # 货币/债券不出现在底部状态
    bottom = df[df["bottom_state"].isin(allowed)]
    assert not bottom["etf_type"].isin(["money", "bond"]).any()


# ── Study 2 Price Bottom State Odds ──────────────────────────────

def _smooth_deep_series(n: int = 800, start: str = "2023-01-02") -> pd.DataFrame:
    """平滑下跌到底部（无折算），供状态机测试。"""
    import numpy as np
    up = np.linspace(100, 300, n // 2)
    down = 300 * np.exp(np.linspace(0, np.log(0.4), n - n // 2))
    return pd.DataFrame({"date": pd.bdate_range(start, periods=n), "close": np.concatenate([up, down]), "fund_code": "TEST"})


def test_state_odds_daily_series_deep_bottom():
    from src.research.etf_bottom.state_odds import daily_state_series
    d = _smooth_deep_series()
    s = daily_state_series(d)
    assert "bottom_state" in s.columns
    # 平滑序列无折算 → 无 unreliable
    assert not s["unreliable_60"].any()
    assert not s["unreliable_120"].any()
    assert not s["unreliable_360"].any()
    # 尾部应进入 DEEP_BOTTOM
    assert (s["bottom_state"] == "DEEP_BOTTOM").sum() > 0


def test_state_odds_corp_action_recovery_window():
    """折算点后短窗口先恢复、长窗口后恢复（窗口级污染语义）。"""
    from src.research.etf_bottom.state_odds import _window_ca_flags
    import numpy as np
    n = 500
    ca = np.zeros(n, dtype=bool)
    ca[300] = True  # 折算在 idx 300
    u60 = _window_ca_flags(ca, 60)
    u360 = _window_ca_flags(ca, 360)
    # 折算后 60D 窗口：idx 360 起应恢复 False（60D 窗口滑过 300）
    assert not u60[365:400].any()
    # 360D 窗口：idx 300 后仍污染（窗口覆盖折算点）
    assert u360[330:360].any()


def test_state_odds_extract_events_off_on():
    """off→on 转换语义：连续在状态内只记一次 entry。"""
    from src.research.etf_bottom.state_odds import daily_state_series, extract_state_entries
    d = _smooth_deep_series()
    s = daily_state_series(d)
    ev = extract_state_entries(s, "industry")
    # 每个状态的 entry 日必须是 off→on：前一日非该状态
    for e in ev:
        idx = s.index[s["date"] == pd.Timestamp(e["entry_date"])]
        if len(idx):
            i = idx[0]
            assert s["bottom_state"].iloc[i] == e["state"]
            if i > 0:
                assert s["bottom_state"].iloc[i - 1] != e["state"]
    # 事件类型覆盖三种底部状态
    states = {e["state"] for e in ev}
    assert states.issubset({"DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "RECENT_BOTTOM"})


def test_state_odds_money_bond_no_events():
    from src.research.etf_bottom.state_odds import daily_state_series, extract_state_entries
    import numpy as np
    n = 800
    close = np.full(n, 100.0) + np.random.default_rng(0).normal(0, 0.0005, n)
    d = pd.DataFrame({"date": pd.bdate_range("2023-01-02", periods=n), "close": close})
    s = daily_state_series(d)
    ev = extract_state_entries(s, "money")
    assert ev == []
    ev2 = extract_state_entries(s, "bond")
    assert ev2 == []


# ── Study 2A Current Bottom ETF Drilldown ─────────────────────────

def test_drilldown_long_term_entries_off_on():
    """long_term_bottom 的 off→on 转换：连续低位段合并为一次 entry，当前段单独标记。"""
    from src.research.etf_bottom.drilldown import _long_term_entries
    from src.research.etf_bottom.state_odds import daily_state_series
    d = _smooth_deep_series()
    s = daily_state_series(d)
    entries = _long_term_entries(s)
    assert len(entries) > 0
    # 当前仍在低位 → 最后一段是 current episode
    assert any(e["is_current_episode"] for e in entries)
    # 历史 entry 不重叠（每段独立）
    dates = [e["entry_date"] for e in entries]
    assert len(dates) == len(set(dates))


def test_drilldown_support_level_logic():
    """支持级别：先例≥2 且 120D 中位>0 且胜率≥50% → 历史支持；120D 无样本 → 证据不足。

    货币 ETF（华安日日鑫等）已由 taxonomy 关键词 + flat-price guardrail 排除，
    drilldown 只含真实长期底部权益/行业标的（28 只，不硬编码具体名单）。
    """
    from src.research.etf_bottom.drilldown import run_drilldown
    p = run_drilldown()
    assert len(p["etfs"]) == 28
    levels = p["support_summary"]
    assert sum(levels.values()) == 28
    assert set(levels.keys()) == {"历史支持", "历史不支持", "证据不足"}
    # 货币 ETF 不应出现在任何长期底部标的
    for r in p["etfs"]:
        assert r["etf_type"] != "money", r["fund_code"]
    # 证据不足组：要么先例<2，要么 120D 样本 n=0
    for r in p["etfs"]:
        if r["support_level"] == "证据不足":
            assert r["n_hist_entries"] < 2 or r["hist"]["120"]["n"] == 0
    # 历史支持组：120D 中位>0 且胜率≥50%
    for r in p["etfs"]:
        if r["support_level"] == "历史支持":
            h = r["hist"]["120"]
            assert h["median"] > 0 and h["win_rate"] >= 0.5
    # 每只 ETF 的当前段信息存在
    for r in p["etfs"]:
        assert "days_in_current_bottom" in r["current"]
        assert r["current"]["days_in_current_bottom"] >= 0


# ── Study 2B Bottom Episode Clustering ───────────────────────────

def test_episodes_etf_low_periods_merge():
    """ETF 级合并：同一 ETF 相邻 entry 间隔 < merge_days 合并为一个低位期。"""
    from src.research.etf_bottom.episodes import _etf_low_periods
    from src.research.etf_bottom.state_odds import daily_state_series
    d = _smooth_deep_series()
    s = daily_state_series(d)
    periods = _etf_low_periods(s, merge_days=40)
    assert len(periods) >= 1
    # 当前段标记
    assert any(p["is_current"] for p in periods)
    # 期不重叠
    for i in range(1, len(periods)):
        assert periods[i]["start"] > periods[i - 1]["end"]


def test_episodes_cluster_merge():
    """产业级合并：同簇多 ETF 低位期重叠/相邻 → 一个 episode。"""
    from src.research.etf_bottom.episodes import _cluster_episodes
    periods = pd.DataFrame([
        {"fund_code": "A", "start": pd.Timestamp("2024-01-05"), "end": pd.Timestamp("2024-01-20"), "is_current": False},
        {"fund_code": "B", "start": pd.Timestamp("2024-01-12"), "end": pd.Timestamp("2024-02-01"), "is_current": False},
        {"fund_code": "C", "start": pd.Timestamp("2025-06-01"), "end": pd.Timestamp("2025-06-15"), "is_current": True},
    ])
    eps = _cluster_episodes(periods, "测试簇")
    assert len(eps) == 2
    assert eps[0]["n_etfs_participating"] == 2  # A+B 合并
    assert eps[0]["fund_codes"] == ["A", "B"]
    assert eps[1]["is_current"] is True


def test_episodes_up_rule_is_median():
    """episode 上涨判定 = 参与 ETF 的 ret_120 中位 > 0（用户口径）。"""
    from src.research.etf_bottom.episodes import _episode_returns
    import numpy as np
    # 构造 3 只 ETF 的 period 收益：[-0.5, 0.2, 0.3] → 中位 0.2 > 0 → 上涨
    period_returns = {
        ("A", pd.Timestamp("2024-01-05")): {"ret_120": -0.5},
        ("B", pd.Timestamp("2024-01-12")): {"ret_120": 0.2},
        ("C", pd.Timestamp("2024-02-01")): {"ret_120": 0.3},
    }
    ep = {"fund_codes": ["A", "B", "C"], "start": pd.Timestamp("2024-01-01"), "end": pd.Timestamp("2024-03-01")}
    r = _episode_returns(ep, period_returns)
    assert r["episode_up"] is True
    assert r["ret120_median"] == pytest.approx(0.2)


def test_episodes_summary_consistency():
    """episodes 汇总与 episode records 聚合一致（结构性不变量，不锁当前横截面）。

    当前是否处于底部是应用态、会随市场漂移——不要求每个历史簇当前必在底部，
    只验证：① summary 聚合与记录一致；② summary 的 is_current_episode 与记录一致；
    ③ 存在的 current episode 形态完整。
    """
    from src.research.etf_bottom.episodes import run_episodes, INDUSTRY_CLUSTERS
    p = run_episodes()
    assert p["n_etf_low_periods"] > 0
    for cluster, s in p["summary"].items():
        eps = p["clusters"][cluster]
        n_hist = sum(1 for e in eps if not e["is_current"])
        n_current = sum(1 for e in eps if e["is_current"])
        # ① summary 聚合与 records 一致
        assert s["n_episodes_historical"] == n_hist
        assert s["n_episodes_total"] == len(eps)
        assert s["n_episodes_up"] <= n_hist
        # up_ratio_historical 在 episodes.py 按 3 位小数 round（展示口径），用舍入容差核对聚合一致
        assert s["up_ratio_historical"] == pytest.approx(
            (s["n_episodes_up"] / n_hist) if n_hist else 0.0, abs=0.0005)
        # ② is_current_episode 与 records 的 current 标记一致
        assert bool(s["is_current_episode"]) == (n_current >= 1)
        # ③ 存在 current episode 时形态完整（有起始日、参与 ETF 非空）
        for e in eps:
            if e["is_current"]:
                assert e["start"] is not None and e["end"] is not None
                assert e["n_etfs_participating"] >= 1
                assert e["fund_codes"]
    # 所有簇参与 ETF 集合不重叠
    all_codes = [c for codes in INDUSTRY_CLUSTERS.values() for c in codes]
    assert len(all_codes) == len(set(all_codes))


# ── Study 2C Current Episode Context Matching ────────────────────

def test_context_no_lookahead_fields_absent_from_distance():
    """No look-ahead：ex-post 字段（final_participation_ratio/duration）不进距离特征。"""
    from src.research.etf_bottom.context import CONTINUOUS_FEATURES
    feats = {f for grp in CONTINUOUS_FEATURES.values() for f in grp}
    for forbidden in ("final_participation_ratio", "episode_duration_days", "final_n_etfs", "n_etfs_participating"):
        assert forbidden not in feats, f"ex-post 字段 {forbidden} 不应进入距离"


def test_context_scaler_fits_historical_only():
    """Scaler 只 fit 历史 episode（user 锁定）。"""
    from src.research.etf_bottom.context_match import _scaler_historical, compute_episode_contexts
    df = compute_episode_contexts()
    scaler = _scaler_historical(df)
    # scaler 只含历史 episode 的统计量
    hist = df[df["is_current"] == False]
    for f, s in scaler.items():
        vals = pd.to_numeric(hist[f], errors="coerce").dropna()
        if len(vals) > 1:
            assert abs(s["mean"] - float(vals.mean())) < 1e-6


def test_context_distance_formula():
    """距离 = 组内 z² 平均开方，再加权平方和开方（用户口径）。"""
    from src.research.etf_bottom.context_match import _dim_distance, _total_distance
    from src.research.etf_bottom.context import EQUAL_WEIGHTS, CONTINUOUS_FEATURES
    z = {f: 0.0 for grp in CONTINUOUS_FEATURES.values() for f in grp}
    z2 = dict(z)
    for f in CONTINUOUS_FEATURES["market"]:
        z2[f] = 3.0  # 只在 market 维度拉开
    # market 维度距离应为 sqrt(mean(9)) = 3
    d = _dim_distance(z, z2, "market")
    assert d == pytest.approx(3.0)
    # 总距离：market 组 d²=9，w=0.2 → sqrt(0.2*9)=sqrt(1.8)
    D = _total_distance(z, z2, EQUAL_WEIGHTS)
    assert D == pytest.approx(np.sqrt(0.2 * 9))


def test_context_success_fail_distinction():
    """成功/失败区分：成功/失败基于冻结历史 episode，而不是「今天是否仍处于该状态」。

    当前是否处于底部（n_current/哪些簇 current）是应用态、随市场漂移——不锁具体数量，
    只验证结构性不变量：
    ① matches 覆盖所有 current episode，且每只都有 Top3
    ② success/fail distinction 来自历史 episode 的 label_summary（regime/mode/recovery），
       其 n_total 与历史 episode 数一致（成功/失败判定稳定，不依赖当前横截面）
    """
    from src.research.etf_bottom.context_match import run_context_matching
    from src.research.etf_bottom.context import CONTINUOUS_FEATURES
    p = run_context_matching()
    # current episode 数量是可漂移的应用态，只断言其为正、且 matches 一一对应
    assert p["n_current"] >= 1
    assert p["n_current"] == len(p["matches"])
    # 每个 current episode 都有 Top3
    for eid, m in p["matches"].items():
        assert len(m["top3_equal"]) == 3
        assert len(m["top3_sensitivity"]) == 3
    # 成功/失败判定基于冻结历史 episode：label_summary 各组 n_total 与历史数一致（稳定，不随当前漂移）
    for grp, buckets in p["label_summary"].items():
        for label, info in buckets.items():
            assert info["n_total"] > 0
            assert 0 <= info["success_rate"] <= 1
            assert 0 <= info["n_success"] <= info["n_total"]
    # feature_stats 覆盖五维
    for grp, feats in CONTINUOUS_FEATURES.items():
        for f in feats:
            assert f in p["feature_stats"], f"{f} 缺少统计"


def test_context_feature_set_frozen():
    """Feature set 已冻结（2C-v1, 2026-08-31）：五维定义与权重不可变。

    改动 feature set 会改变历史 episode 的 reference space 与匹配结果，
    必须显式解除冻结并说明理由。此测试锁定当前冻结内容。
    """
    from src.research.etf_bottom import context as ctx
    assert ctx.FEATURE_SET_VERSION == "2C-v1"
    assert ctx.FEATURE_SET_FROZEN_AT == "2026-08-31"
    # 五维顺序
    assert ctx.DIM_GROUP_ORDER == ("market", "industry_relative", "bottom_depth", "synchronization", "recovery")
    # 特征集合冻结
    assert dict(ctx.CONTINUOUS_FEATURES) == {
        "market": ("market_ret_60d", "market_ret_120d", "market_breadth_60d"),
        "industry_relative": ("industry_excess_60d", "industry_excess_120d"),
        "bottom_depth": ("pos60", "pos120", "pos360", "distance_360", "dd60"),
        "synchronization": ("initial_participation_ratio", "entries_last_20d"),
        "recovery": ("deep_ratio", "recovering_ratio"),
    }
    # 权重冻结
    assert dict(ctx.EQUAL_WEIGHTS) == {g: 0.2 for g in ctx.DIM_GROUP_ORDER}
    assert dict(ctx.SENS_WEIGHTS) == {"market": 0.30, "industry_relative": 0.25,
                                       "bottom_depth": 0.20, "synchronization": 0.15, "recovery": 0.10}
    # 只读：尝试修改应抛 TypeError
    with pytest.raises(TypeError):
        ctx.CONTINUOUS_FEATURES["market"] = ("x",)


# ── Study 2D Context Broad-Sample Replication ────────────────────

def test_replication_entry_layer_features():
    """entry 层特征增强：asset_excess / price_pos / dd60 / 双 outcome 可计算。"""
    from src.research.etf_bottom.replication import _load_bottom_entries, enhance_entries
    entries = _load_bottom_entries()
    sample = entries.sample(5, random_state=1).copy()
    enh = enhance_entries(sample)
    for _, r in enh.iterrows():
        assert r["asset_excess_60d"] is not None
        assert r["asset_excess_120d"] is not None
        assert r["price_pos_120"] is not None
        assert r["dd60"] is not None
        assert r["market_ret_60d"] is not None
        assert r["excess_vs_etf_market_120d"] is not None
        # no look-ahead：特征全部是 entry 当日（不含未来）
        assert "asset_excess_120d" in r


def test_replication_quintile_labels():
    """绝对 quintile 与年内 quintile 标签正确（Q1=最弱）。"""
    from src.research.etf_bottom.replication import _quintile_labels, _quintile_by_year
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    q = _quintile_labels(s)
    assert q.tolist() == ["Q1", "Q1", "Q2", "Q2", "Q3", "Q3", "Q4", "Q4", "Q5", "Q5"]
    df = pd.DataFrame({"v": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] * 2, "y": [2021] * 10 + [2022] * 10})
    qy = _quintile_by_year(df["v"], df["y"])
    assert qy.isna().sum() == 0
    assert (qy == "Q1").sum() == 4  # 每年 2 个 Q1


def test_replication_asset_excess_direction():
    """asset_excess_60d：全样本 Q1（最超跌）超额应 > Q5（最强），方向与 2C 一致。"""
    from src.research.etf_bottom.replication import run_replication
    p = run_replication()
    ev = p["layer1"]["asset_excess_60d"]["event_weighted"]
    q1 = ev[0]["excess_etf_market"]
    q5 = ev[-1]["excess_etf_market"]
    assert q1 > q5, "相对超跌组（Q1）后续超额应高于最强组（Q5）"
    # ETF-balanced 同向
    bal = p["layer1"]["asset_excess_60d"]["etf_balanced"]
    assert bal[0]["excess_etf_market"] > bal[-1]["excess_etf_market"]


def test_replication_layer3_structure():
    """Layer3 方向一致性表结构完整，2026 不进入主年份判据。"""
    from src.research.etf_bottom.replication import run_replication, YEARS_MAIN
    p = run_replication()
    assert len(p["layer3"]) >= 5
    # 主年份 = 2021-2025，2026 被排除
    assert YEARS_MAIN == [2021, 2022, 2023, 2024, 2025]
    for r in p["layer2"]["asset_excess_60d"]["by_year"]:
        assert r["year"] in YEARS_MAIN + [2026]
        assert r["is_main_year"] == (r["year"] in YEARS_MAIN)


# ── Study 2E Repair Structure Validation ─────────────────────────

def test_repair_q1_composition_structure():
    """Q1：全样本 + DEEP + RECOVERING 三组 pos120 quintile 结构完整。"""
    from src.research.etf_bottom.repair_structure import q1_composition, _load
    df = _load()
    q1 = q1_composition(df)
    for st in ["ALL", "DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"]:
        assert len(q1[st]["quintiles"]) == 5
        for q in q1[st]["quintiles"]:
            assert q["n"] > 0


def test_repair_q2_interaction_target():
    """Q2：target 格（pos60 低 × pos120 高）应存在且样本充足。"""
    from src.research.etf_bottom.repair_structure import q2_interaction, _load
    df = _load()
    q3 = q2_interaction(df, grid=3)
    t = q3["target_cell"]
    assert t is not None and t["n"] > 0
    assert t["pos120_quintile"] == "Q3" and t["pos60_quintile"] == "Q1"


def test_repair_q3_date_weighted():
    """Q3：date-weighted 每日期一票；target 结构 date-weighted 信息存在。"""
    from src.research.etf_bottom.repair_structure import q3_date_weighted, _load
    df = _load()
    q3 = q3_date_weighted(df)
    assert "ALL" in q3 and "DEEP_BOTTOM" in q3 and "RECOVERING_FROM_BOTTOM" in q3
    t = q3["_target_date_weighted"]
    assert t["target_n_dates"] > 0
    assert t["target_median"] is not None
    assert t["all_median"] is not None


def test_repair_adjudication_present():
    """adjudication 四假设裁决存在且 verdict 合法。"""
    from src.research.etf_bottom.repair_structure import run_repair
    p = run_repair()
    ad = p["adjudication"]
    assert ad["verdict"] in ("INTERACTION_STRUCTURE", "CONTINUOUS_SIGNAL", "COMPOSITION_EFFECT", "NO_STABLE_SIGNAL")
    # 证据键完整
    for k in ("q1_all_direction", "q2_3x3_target_lead_mean", "q2_3x3_target_lead_median",
              "q2_2x2_target_lead_mean", "q2_2x2_target_lead_median",
              "q3_target_date_weighted_median", "q3_all_date_weighted_median", "q3_target_date_lead"):
        assert k in ad["evidence"], f"{k} 缺失"


def test_repair_discovery_universe_persisted():
    """repair_structure.json 落盘 discovery_universe + cut_points（供原样读取）。"""
    import json
    from src.research.etf_bottom import STUDY_DIR
    rs = json.loads((STUDY_DIR / "repair_structure.json").read_text(encoding="utf-8"))
    assert "discovery_universe" in rs
    du = rs["discovery_universe"]
    assert "cut_points" in du
    cp120 = du["cut_points"]["price_pos_120"]
    cp60 = du["cut_points"]["price_pos_60"]
    # 切分点边界合理：pos120 上限=20（long_term_bottom 定义），3 tertile
    assert len(cp120) == 4 and len(cp60) == 4
    assert abs(cp120[-1] - 20.0) < 1e-6  # pos120 上限即 domain 边界


def test_current_eval_stage_logic():
    """当前关注 ETF 评估：reliable→domain→target 三级分类。

    关键：p120>20 的标的必须 OUT_OF_DOMAIN（不能误判 target），
    即使 p60 低。这是用户锁定的 no-look-ahead domain 守卫。
    """
    from src.research.etf_bottom.current_eval import (
        load_watch_etfs, load_frozen_cutpoints, eval_one, tertile,
    )
    cut120, cut60 = load_frozen_cutpoints()
    watch = load_watch_etfs()
    assert len(watch) >= 10
    # watch_etf（monitor_only）也必须纳入关注表，不能缺格
    assert any((w["fund_code"] == "159512" and w["tier"] == "watch_etf" and w["participation"] == "monitor_only")
               for w in watch.to_dict("records"))
    # 构造：p120=37.8（离开 domain）但 p60=11.7（低）→ 必须 OUT_OF_DOMAIN 而非 target
    res = eval_one("159819", "人工智能ETF易方达", "ai_infrastructure", cut120, cut60)
    assert res["stage"] == "OUT_OF_DOMAIN", f"159819 p120={res.get('p120')} 应离开 domain（需<=20）"
    # tertile 切分正确：p120=37.8 在 Q3（>15.82），但 domain 守卫优先
    assert tertile(37.8, cut120) == "Q3"
    assert tertile(11.7, cut60) == "Q1"


def test_repair_retest_v1_rule_spec_frozen():
    """规则锁定测试：V1 冻结值逐值锁死，任何修改必须显式升级 V2。

    这是 Repair-Retest V1 的 canonical 真源测试——不允许通过微调测试来
    掩盖规则漂移。如需改规则阈值 → 新建 REPAIR_RETEST_V2，不改此测试。
    """
    import yaml
    from src.common.paths import config_dir
    spec_path = config_dir() / "research" / "repair_retest_v1.yaml"
    with spec_path.open(encoding="utf-8") as f:
        spec = yaml.safe_load(f)

    assert spec["rule_id"] == "REPAIR_RETEST_V1"
    assert spec["status"] == "FROZEN_RESEARCH_HYPOTHESIS"
    assert str(spec["frozen_at"]) == "2026-08-31"  # YAML 解析为 date，比较字符串

    # domain
    assert spec["domain"]["states"] == ["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM"]
    assert spec["domain"]["price_pos_120_max"] == 20.0
    assert spec["domain"]["price_pos_360_max"] == 20.0

    # 冻结 cut points（V1 永不漂移）
    p120 = spec["features"]["price_pos_120"]
    assert p120["q1_upper"] == 9.88
    assert p120["q2_upper"] == 15.82
    assert p120["domain_upper"] == 20.0
    p60 = spec["features"]["price_pos_60"]
    assert p60["q1_upper"] == 14.55
    assert p60["q2_upper"] == 22.12

    # target
    assert spec["target"] == {"price_pos_60_bin": "Q1", "price_pos_120_bin": "Q3"}

    # outcome
    assert spec["outcome"]["metric"] == "excess_vs_etf_market_120d"
    assert spec["outcome"]["horizon_trading_days"] == 120

    # adjudication
    assert spec["adjudication"]["verdict"] == "INTERACTION_STRUCTURE"
    assert "target_leads_date_weighted" in spec["adjudication"]["requirements"]


def test_repair_retest_v1_current_eval_reads_rule_spec():
    """current-eval 消费冻结 YAML（非最新研究输出），确保重跑研究不改规则。"""
    from src.research.etf_bottom.current_eval import load_frozen_cutpoints, load_rule_spec
    spec = load_rule_spec()
    assert spec["rule_id"] == "REPAIR_RETEST_V1"
    cut120, cut60 = load_frozen_cutpoints()
    # 与 YAML 冻结值一致（V1 不因研究重跑而变）
    assert cut120 == pytest.approx([-0.001, 9.88, 15.82, 20.0])
    assert cut60 == pytest.approx([-0.001, 14.55, 22.12, 100.0])


def test_repair_cutpoint_drift_report():
    """研究复现测试：重跑 repair 产出 drift 报告，不覆盖 V1。"""
    import json
    from src.research.etf_bottom import STUDY_DIR
    rs = json.loads((STUDY_DIR / "repair_structure.json").read_text(encoding="utf-8"))
    assert "frozen_cutpoint_drift" in rs
    drift = rs["frozen_cutpoint_drift"]
    assert drift["status"] in ("DRIFT_WITHIN_TOLERANCE", "DRIFT_OUTSIDE_TOLERANCE")
    assert "price_pos_120" in drift["features"] and "price_pos_60" in drift["features"]
    # drift 报告列出 frozen / latest / delta 三者
    for feature, row in drift["features"].items():
        assert "frozen" in row and "latest" in row and "delta" in row
        assert len(row["delta"]) == 4


def test_odds_payoff_ratio():
    """payoff_ratio：正负中位除法；n_neg=0 → None。"""
    from src.research.etf_bottom.current_eval import attach_history
    h = attach_history("515250")["history"]
    assert h["n"] >= 2
    if h["payoff_ratio"] is not None:
        assert h["payoff_ratio"] > 0
    # 159611 全正样本 → n_neg=0 → payoff None
    h2 = attach_history("159611")["history"]
    assert h2["n_negative"] == 0
    assert h2["payoff_ratio"] is None


def test_odds_evidence_label():
    """evidence_label 四分类（NEGATIVE_HISTORY 优先，用户锁定口径）。"""
    from src.research.etf_bottom.current_eval import attach_history
    # 515250：2022 正 + 2024 正 → CROSS_YEAR_SUPPORTED
    assert attach_history("515250")["history"]["evidence_label"] == "CROSS_YEAR_SUPPORTED"
    # 515030：pooled median 负 → NEGATIVE_HISTORY（即使有正年份）
    assert attach_history("515030")["history"]["evidence_label"] == "NEGATIVE_HISTORY"
    # 516110：2022 负、2023/2024 正 → YEAR_DEPENDENT
    assert attach_history("516110")["history"]["evidence_label"] == "YEAR_DEPENDENT"
    # 159512：无 entry → INSUFFICIENT_HISTORY
    assert attach_history("159512")["history"]["evidence_label"] == "INSUFFICIENT_HISTORY"


def test_odds_assessment_mapping():
    """odds_assessment：决策矩阵映射（数据层稳定枚举，无 emoji）。"""
    from src.research.etf_bottom.current_eval import odds_assessment
    good = {"median_120d": 0.05}
    neg = {"median_120d": -0.05}
    empty = {"median_120d": None}
    assert odds_assessment("TARGET", "CROSS_YEAR_SUPPORTED", good) == "strong_observe"
    assert odds_assessment("IN_DOMAIN_NON_TARGET", "CROSS_YEAR_SUPPORTED", good) == "watch_structure"
    assert odds_assessment("IN_DOMAIN_NON_TARGET", "INSUFFICIENT_HISTORY", good) == "position_only"
    assert odds_assessment("IN_DOMAIN_NON_TARGET", "NEGATIVE_HISTORY", neg) == "cautious"
    assert odds_assessment("OUT_OF_DOMAIN", "CROSS_YEAR_SUPPORTED", good) == "out_of_domain_good"
    assert odds_assessment("OUT_OF_DOMAIN", "NEGATIVE_HISTORY", neg) == "out_of_domain_bad"
    # 历史不足 → out_of_domain_unknown（不是「无吸引力」）；即使 pooled median>0 也优先 unknown
    assert odds_assessment("OUT_OF_DOMAIN", "INSUFFICIENT_HISTORY", empty) == "out_of_domain_unknown"
    assert odds_assessment("OUT_OF_DOMAIN", "INSUFFICIENT_HISTORY", good) == "out_of_domain_unknown"
    assert odds_assessment("UNRELIABLE", "NEGATIVE_HISTORY", neg) == "unreliable"


def test_odds_out_of_domain_insufficient_precedes_median():
    """OUT_OF_DOMAIN × INSUFFICIENT_HISTORY 必须判 unknown，即使 median>0（159666 n=1 边界）。"""
    import json
    from src.research.etf_bottom import STUDY_DIR
    from src.research.etf_bottom.current_eval import odds_assessment
    payload = json.loads((STUDY_DIR / "current_watch_eval.json").read_text(encoding="utf-8"))
    e159666 = next(e for e in payload["etfs"] if e["fund_code"] == "159666")
    assert e159666["evidence_label"] == "INSUFFICIENT_HISTORY"
    assert e159666["history"]["median_120d"] is not None and e159666["history"]["median_120d"] > 0
    # 重新映射：即使历史 median>0，INSUFFICIENT 仍应判 unknown
    assert odds_assessment("OUT_OF_DOMAIN", "INSUFFICIENT_HISTORY", e159666["history"]) == "out_of_domain_unknown"


def test_odds_position_only_secondary_label():
    """position_only 二级翻译：YEAR_DEPENDENT 与 INSUFFICIENT 展示不同（底层 enum 不变）。"""
    from src.research.etf_bottom.current_eval_report import _odds_label
    assert "跨年不稳" in _odds_label("position_only", "YEAR_DEPENDENT")
    assert "历史不足" in _odds_label("position_only", "INSUFFICIENT_HISTORY")
    assert _odds_label("position_only", "YEAR_DEPENDENT") != _odds_label("position_only", "INSUFFICIENT_HISTORY")


def test_odds_report_renderer_pure_and_sorted():
    """HTML renderer 是纯 renderer：只消费已有字段，排序确定（odds 序 + median DESC + code ASC）。"""
    import json
    from src.research.etf_bottom import STUDY_DIR
    from src.research.etf_bottom.current_eval_report import (
        _sorted_etfs, render_current_odds, IN_DOMAIN_STAGES, CROSS_YEAR_LABEL, FOCUS_ODDS,
    )
    payload = json.loads((STUDY_DIR / "current_watch_eval.json").read_text(encoding="utf-8"))
    etfs = payload["etfs"]
    # renderer 不重算：输出排序后集合应等于直接按 odds 枚举统计
    order = ["strong_observe", "watch_structure", "position_only", "cautious",
             "out_of_domain_good", "out_of_domain_unknown", "out_of_domain_bad", "unreliable"]
    ranks = {o: i for i, o in enumerate(order)}
    got = [e.get("odds_assessment", "unreliable") for e in _sorted_etfs(etfs)]
    assert all(ranks[got[i]] <= ranks[got[i + 1]] for i in range(len(got) - 1)), "odds 序排序失败"
    # 组内 median DESC：同 odds 组内递减（缺失在最后）
    for o in order:
        group = [e.get("history", {}).get("median_120d") for e in _sorted_etfs(etfs) if e.get("odds_assessment") == o]
        meds = [m for m in group if m is not None]
        assert meds == sorted(meds, reverse=True)
    # 集合定义锁定（口径不得漂移）
    assert IN_DOMAIN_STAGES == ("TARGET", "IN_DOMAIN_NON_TARGET")
    assert CROSS_YEAR_LABEL == "CROSS_YEAR_SUPPORTED"
    assert FOCUS_ODDS == ("strong_observe", "watch_structure")
    # HTML 生成成功且含 8 列表头（ETF 合并 + n 保留）
    html_path = render_current_odds(payload)
    html = html_path.read_text(encoding="utf-8")
    for col in ("RR 阶段", "n</th>", "120D 中位", "胜率", "Payoff", "时间证据", "最终判断"):
        assert col in html
    # TARGET 展示层中文 = 结构触发候选（机器码保留）
    assert "结构触发候选" in html
    # n 必须保留（防止小样本被隐藏）
    assert "· 智能汽车ETF富国" in html
    assert html_path.exists()


# ── Full-Market Repair-Retest V1 Scanner（Lane 2 Application）─────────────

def test_scanner_cohort_data_driven():
    """cohort 数据驱动：reliable=false → UNRELIABLE；hist≥756 → BASE；360~755 → EXTENSION。"""
    from src.research.etf_bottom.scanner import _cohort
    assert _cohort(900, True) == "BASE"
    assert _cohort(756, True) == "BASE"
    assert _cohort(755, True) == "EXTENSION"
    assert _cohort(360, True) == "EXTENSION"
    assert _cohort(900, False) == "UNRELIABLE"
    assert _cohort(400, False) == "UNRELIABLE"


def test_scanner_target_classification_frozen_cuts():
    """TARGET 分类只读 frozen cut：(stage, near_miss_reason)。TARGET=Q1×Q3；
    NEAR_MISS=Q1×Q2（P120_ONE_BUCKET_AWAY）或 Q2×Q3（P60_ONE_BUCKET_AWAY）。"""
    from src.research.etf_bottom.scanner import classify_target
    from src.research.etf_bottom.current_eval import load_frozen_cutpoints
    cut120, cut60 = load_frozen_cutpoints()
    # frozen：pos60 q1<14.55、pos120 q1<9.88 q2<15.82
    assert classify_target(10.0, 18.0, cut60, cut120) == ("TARGET", None)      # Q1 × Q3
    assert classify_target(10.0, 12.0, cut60, cut120) == ("NEAR_MISS", "P120_ONE_BUCKET_AWAY")  # Q1 × Q2
    assert classify_target(17.0, 18.0, cut60, cut120) == ("NEAR_MISS", "P60_ONE_BUCKET_AWAY")   # Q2 × Q3
    assert classify_target(30.0, 18.0, cut60, cut120) == ("NON_TARGET", None)
    assert classify_target(10.0, 6.0, cut60, cut120) == ("NON_TARGET", None)  # Q1 × Q1
    # 缺失 → NON_TARGET（不冒充）
    assert classify_target(None, 18.0, cut60, cut120) == ("NON_TARGET", None)
    assert classify_target(10.0, None, cut60, cut120) == ("NON_TARGET", None)


def test_scanner_transition_kind():
    """状态迁移语义：domain 进出 + DEEP↔RECOVERING，其余 OUTSIDE_DOMAIN。"""
    from src.research.etf_bottom.scanner import transition_kind
    assert transition_kind("DEEP_BOTTOM", True, "RECOVERING_FROM_BOTTOM", True) == "DEEP_TO_RECOVERING"
    assert transition_kind("RECOVERING_FROM_BOTTOM", True, "DEEP_BOTTOM", True) == "RECOVERING_TO_DEEP"
    assert transition_kind("DEEP_BOTTOM", True, "DEEP_BOTTOM", True) == "STAY_IN_DOMAIN"
    assert transition_kind("NORMAL", False, "DEEP_BOTTOM", True) == "ENTER_DOMAIN"
    assert transition_kind("DEEP_BOTTOM", True, "NORMAL", False) == "EXIT_DOMAIN"
    assert transition_kind("NORMAL", False, "NORMAL", False) == "OUTSIDE_DOMAIN"


def test_scanner_run_20260831_baseline():
    """2026-08-31 全市场扫描对照已知基线（用户锁定事实）：
    reliable=874 = 原901 − 27只真实 flat-price 货币ETF（taxonomy 修复后 flat_price 29→27，
    其中 2 只「现金流ETF」误判已修正，本来就在 901 内，不构成额外加入 universe）。
    BASE=664 / EXT=210 / flat_price=27 / ltb=22 / DEEP=6 / RECOVERING=16 / TARGET=0 / NEAR_MISS=0。
    仅对与 base 事实一致且稳定的部分硬编码，不做 snapshot 数量锁死。
    """
    import json
    from src.research.etf_bottom.scanner import run_scan
    p = run_scan("2026-08-31")
    a = p["layer_a_market_bottom_map"]
    # 数据质量门（2026-08-31 时点，排除货币/债券近零波动；现金流ETF 是权益不误排除）
    assert a["reliable_total"] == 874
    assert a["cohort"] == {"BASE": 664, "EXTENSION": 210}
    assert a["flat_price_total"] == 27
    # Provenance 锁定：reliable = 原 901 − 27 真实 flat-price 货币ETF。
    # 现金流ETF 修正不改 reliable 分母——它们本来就在 901 内（2 只 hist≥360 在 reliable，
    # 其余 29 只 hist<360 本就在 unreliable），不构成额外加入 universe。
    flat = pd.read_parquet(p["flat_parquet_path"])
    assert a["reliable_total"] + a["flat_price_total"] == 901
    assert a["flat_price_total"] + a["unreliable_total"] + a["reliable_total"] == 1289
    # 现金流ETF（159201/159399 hist≥360）必须在 reliable 内（非 flat），且不新增行数
    assert (flat["fund_code"] == "159201").any()
    assert (flat["fund_code"] == "159399").any()
    assert (flat.loc[flat["fund_code"] == "159201", "data_quality_flag"] != "flat_price_noise").all()
    # 迁移语义一致性：当前 ltb 计数 = STAY + ENTER + DEEP↔RECOVERING 迁移之和
    t = a["transition_counts"]
    ltb_total = a["long_term_bottom_total"]
    assert ltb_total == t.get("STAY_IN_DOMAIN", 0) + t.get("ENTER_DOMAIN", 0) \
        + t.get("DEEP_TO_RECOVERING", 0) + t.get("RECOVERING_TO_DEEP", 0), \
        "长期底部计数与迁移语义不一致"
    # 统一 prev-trade-date：全部 reliable 行 prev_trade_date 相同（市场前一交易日）
    assert (flat["prev_trade_date"] == "2026-08-28").all(), "prev_trade_date 必须统一为市场前一交易日"
    # 语义契约：in_domain = reliable ∧ long_term_bottom（两个概念不合并）
    # long_term_bottom = 市场状态事实（Observation）；in_domain = V1 research domain。
    # reliable 表内两者数值一致（设计如此），但定义来源不同，不得互相替换。
    assert (flat["in_domain"] == (flat["reliable"] & flat["long_term_bottom"])).all()
    # 每层结构存在
    assert "layer_b_repair_retest_scanner" in p
    assert "layer_c_historical_odds" in p
    assert "flat_price_audit" in p
    assert len(p["flat_price_audit"]) == 27
    # Layer B 只含 in-domain 标的（每只都 long_term_bottom 且 in_domain）
    for r in p["layer_b_repair_retest_scanner"]:
        assert r["long_term_bottom"] is True
        assert r["in_domain"] is True
    # Layer C 历史赔率必须来自真实数据（attach_history 解嵌套）：
    # 517770（游戏传媒）有 9 次先例、median>0、YEAR_DEPENDENT；
    # 159819（AI）CROSS_YEAR_SUPPORTED → out_of_domain_good（非 out_of_domain_unknown）
    lc = {r["fund_code"]: r for r in p["layer_c_historical_odds"]}
    h517770 = lc["517770"].get("odds", {})
    assert h517770.get("n") == 9 and h517770.get("median_120d", 0) > 0
    assert lc["517770"].get("evidence_label") == "YEAR_DEPENDENT"
    assert lc["159819"].get("odds_assessment") == "out_of_domain_good"
    assert lc["159819"].get("evidence_label") == "CROSS_YEAR_SUPPORTED"
    # 规则来自 frozen YAML
    assert p["rule_id"] == "REPAIR_RETEST_V1"
    assert p["rule_status"] == "FROZEN_RESEARCH_HYPOTHESIS"
    assert "cut_points" in p


def test_scanner_prev_trade_date_uniform_and_missing():
    """prev_trade_date 用统一市场前一交易日；某 ETF 当日无数据 → PREV_MISSING + prev_actual_trade_date 审计。
    560650（核心50ETF民生加银）在 2026-08-28 无数据，其 prev_actual_trade_date 应 = 2026-08-21。"""
    import json
    from src.research.etf_bottom.scanner import run_scan
    p = run_scan("2026-08-31")
    a = p["layer_a_market_bottom_map"]
    assert a["prev_trade_date"] == "2026-08-28"
    flat = pd.read_parquet(p["flat_parquet_path"])
    r = flat[flat["fund_code"] == "560650"].iloc[0]
    assert r["prev_trade_date"] == "2026-08-28"          # 统一市场 prev 日，不静默回退
    assert r["prev_actual_trade_date"] == "2026-08-21"   # 审计：该 ETF 自身最近可用交易日
    assert r["prev_data_status"] == "missing"
    assert r["transition"] == "PREV_MISSING"
    # 其余正常行 prev_data_status=ok
    ok_rows = flat[flat["fund_code"] != "560650"]
    assert (ok_rows["prev_data_status"] == "ok").all()
    assert (ok_rows["transition"] != "PREV_MISSING").all()


def test_scanner_report_renderer_consumes_json_only():
    """scanner HTML renderer 是纯 renderer：消费已落盘 scan JSON，不重算。
    且 Layer C 历史赔率数值必须渲染出来（517770 游戏传媒 n=9 / +41.2%），不得全部「—」。
    TARGET 展示层中文 =「结构触发候选」（底层机器码保留 TARGET）。"""
    import json
    from src.research.etf_bottom import STUDY_DIR
    from src.research.etf_bottom.scanner_report import render_scan
    payload = json.loads((STUDY_DIR / "scan_20260831.json").read_text(encoding="utf-8"))
    html_path = render_scan(payload)
    html = html_path.read_text(encoding="utf-8")
    for col in ("Layer A · Market Bottom Map", "Layer B · Repair-Retest Scanner",
                "Layer C · Historical Odds", "NEAR_MISS",
                "REPAIR_RETEST_V1"):
        assert col in html
    # TARGET 展示层中文（机器码 TARGET 仍在口径文本保留）
    assert "结构触发候选" in html
    # 结构触发候选 KPI 必须高亮（金色描边 .kpi-accent，核心指标）
    assert "kpi-accent" in html
    # KPI 决策导向排序：结构触发候选（accent）在前，可靠池在后（决策问题 > 数据管线）
    assert html.find("kpi-accent") < html.find("NEAR_MISS")
    assert html.find("NEAR_MISS") < html.find("<span>reliable</span>")
    # 漏斗说明：观察池总数来自 payload（watch_pool_total），renderer 不硬编码不读 config
    assert payload.get("watch_pool_total") == 18
    assert "漏斗" in html
    assert "观察池" in html
    assert "18" in html
    # 517770 游戏传媒：n=9 / 120D 中位 +41.2% / 胜率 100% 必须出现在 Layer C 表格
    assert "517770" in html
    assert "+41.2%" in html
    assert "游戏传媒" in html
    assert html_path.exists()


def test_scanner_frozen_cutpoint_drift_guard():
    """scan 运行时防自适应漂移：cut points 漂移必须抛错（规则只能来自 frozen YAML）。"""
    import numpy as np
    from src.research.etf_bottom import scanner
    # 正常值通过
    scanner._verify_frozen(scanner._FROZEN_120, scanner._FROZEN_60)
    # 模拟重算导致的漂移（Q1 被今日数据改动）→ 必须抛错
    drifted = [-0.001, 8.0, 15.82, 20.0]
    with pytest.raises(RuntimeError, match="cut points 漂移"):
        scanner._verify_frozen(drifted, scanner._FROZEN_60)
    with pytest.raises(RuntimeError, match="cut points 漂移"):
        scanner._verify_frozen(scanner._FROZEN_120, [-0.001, 13.0, 22.12, 100.0])

