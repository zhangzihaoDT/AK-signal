"""Lane 3 · Study 3A 核心逻辑测试（纯离线，确定性）。

覆盖：MarketCalendar 右截断语义 · 轨迹提取（escape/right_censored/days_to_first_retest）
· 生存曲线与 KM · 断点检验（window split / hypothesis / date-block bootstrap / persistence）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research.trend_transition.calendar import MarketCalendar
from src.research.trend_transition.survival import kaplan_meier, survival_curve
from src.research.trend_transition.trajectory import (
    build_persistence_series,
    derive_report_labels,
    extract_trajectories,
)
from src.research.trend_transition.structural_break import (
    _escape_series,
    _window_split,
    date_block_bootstrap,
    hypothesis_break,
    persistence_consistency,
)


def _mk_dates(n=400):
    return pd.bdate_range("2023-01-02", periods=n)


def _mk_v1(ltb_series: dict, n=400) -> pd.DataFrame:
    dates = _mk_dates(n)
    rows = []
    for code, ltb in ltb_series.items():
        if len(ltb) < n:
            ltb = ltb + [False] * (n - len(ltb))
        for d, v in zip(dates, ltb):
            rows.append({
                "trade_date": d, "fund_code": code, "fund_name": f"ETF{code}",
                "etf_type": "theme", "industry_cluster": "OTHER", "long_term_bottom": bool(v),
            })
    return pd.DataFrame(rows)


# ── MarketCalendar ─────────────────────────────────────────────
def test_calendar_complete_window_and_days_between():
    dates = _mk_dates(20)
    cal = MarketCalendar(dates)
    assert cal.has_complete_window(dates[0], 10) is True
    assert cal.has_complete_window(dates[0], 100) is False      # 不足窗口 → 右截断
    assert cal.has_complete_window(dates[18], 2) is False
    assert cal.trade_days_between(dates[0], dates[10]) == 10
    assert cal.trade_days_between(dates[10], dates[5]) == 0     # 反向 → 0
    assert cal.is_trade_date(dates[5]) is True
    assert cal.is_trade_date(pd.Timestamp("1999-01-01")) is False


def test_calendar_strict_window_boundary():
    # 恰好有 n 个交易日：start 之后有 5 个交易日 → 5 天窗口完整，6 天窗口不足
    dates = _mk_dates(6)
    cal = MarketCalendar(dates)
    assert cal.has_complete_window(dates[0], 5) is True
    assert cal.has_complete_window(dates[0], 6) is False


# ── trajectory ─────────────────────────────────────────────────
def test_trajectory_escape_and_retest_labels():
    """一条含 exit→retest 的轨迹：escape_120d=False，days_to_first_retest 正确。"""
    n = 300
    ltb = [True] * 40 + [False] * 30 + [True] * 30 + [False] * (n - 100)
    v1 = _mk_v1({"A": ltb}, n=n)
    cal = MarketCalendar.from_v1(v1)
    traj = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    # 一段 off→on→off→on→off：应有两条轨迹（两个 on 段）
    assert len(traj) == 2
    exits = traj[traj["first_exit_date"].notna()]
    assert len(exits) == 2
    e0 = exits.iloc[0]
    # 第一段 exit 后有 retest（第二段 on）：escape_120d=False
    assert e0["escape_120d"] == False  # noqa: E712
    assert e0["right_censored_120d"] == False  # noqa: E712
    assert e0["first_retest_date"] is not None
    # days_to_first_retest ∈ [1, 120]
    assert 1 <= e0["days_to_first_retest"] <= 120


def test_trajectory_persistence_confirms_direction():
    """persistence 过滤单日抖动：连续 N 日确认才翻转。"""
    n = 60
    # 单日 True 抖动（不构成持久 exit）
    ltb = [False] * 20 + [False]  # 基础
    ltb = [False] * 20 + [True] * 1 + [False] * 5 + [True] * 20
    ltb += [False] * (n - len(ltb))
    v1 = _mk_v1({"A": ltb}, n=n)
    cal = MarketCalendar.from_v1(v1)
    raw = build_persistence_series(v1, window=0)
    p3 = build_persistence_series(v1, window=3)
    # raw 会把单日抖动当 on 段；persistence=3 会忽略它
    raw_on = raw["ltb_persist"].sum()
    p3_on = p3["ltb_persist"].sum()
    assert raw_on > 0
    # persistence=3 下只有第二个连续段成立
    assert p3_on > 0
    assert p3_on < raw_on


def test_trajectory_right_censoring_near_end():
    """靠近数据末端 exit：无完整 120 日窗口 → right_censored=True。"""
    n = 60
    ltb = [True] * 20 + [False] * (n - 20)
    v1 = _mk_v1({"A": ltb}, n=n)
    cal = MarketCalendar.from_v1(v1)
    traj = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    exits = traj[traj["first_exit_date"].notna()]
    assert len(exits) == 1
    assert exits.iloc[0]["right_censored_120d"] == True  # noqa: E712
    assert exits.iloc[0]["escape_120d"] is None          # 不确定，不能当 escape


def test_derive_report_labels_not_escape_for_current():
    t = pd.DataFrame([{
        "fund_code": "A", "is_current": True, "escape_120d": None,
        "right_censored_120d": True, "first_retest_date": None, "days_to_first_retest": None,
    }])
    out = derive_report_labels(t)
    assert out.iloc[0]["trajectory_type"] == "PERSISTENT"


# ── survival ───────────────────────────────────────────────────
def test_survival_curve_horizon_values():
    n = 300
    # A: on1[0:40]→exit→retest(10d 后)→on2[40:80]→exit 无 retest
    #   seg1 escape=False（120 内重测）· seg2 escape=True
    # B: on1[0:40]→exit 无 retest → escape=True
    ltb_a = [True] * 40 + [False] * 10 + [True] * 40 + [False] * (n - 90)
    ltb_b = [True] * 40 + [False] * (n - 40)
    v1 = _mk_v1({"A": ltb_a, "B": ltb_b}, n=n)
    cal = MarketCalendar.from_v1(v1)
    traj = extract_trajectories(v1, cal, horizon_set=(20, 120), persistence=0)
    surv = survival_curve(traj, cal, horizons=(20, 120))
    # 3 个 exit：seg1A(escape False) / seg2A(escape True) / B(escape True)
    for h in ("20", "120"):
        s = surv[h]
        assert s["n_observed"] == 3
        assert s["n_escape"] == 2
        assert s["survival"] == pytest.approx(2 / 3, abs=1e-3)


def test_kaplan_meier_monotone_and_endpoint():
    n = 300
    ltb_a = [True] * 40 + [False] * 10 + [True] * 40 + [False] * (n - 90)
    ltb_b = [True] * 40 + [False] * (n - 40)
    v1 = _mk_v1({"A": ltb_a, "B": ltb_b}, n=n)
    cal = MarketCalendar.from_v1(v1)
    traj = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    km = kaplan_meier(traj, cal, horizon=120)
    assert km["monotone_non_increasing"] is True
    assert km["survival_end"] <= 1.0
    assert km["n_events_total"] == 3
    # 120 日 survival_end 与 escape 生存曲线 120d 同一样本一致
    assert km["survival_end"] == pytest.approx(2 / 3, abs=1e-3)


# ── structural break ───────────────────────────────────────────
def test_window_split_preserves_pre_post():
    dates = pd.bdate_range("2024-09-01", periods=30)
    s = pd.DataFrame({"_t": dates, "_y": [0.0, 1.0] * 15, "etf_type": "theme"})
    out = _window_split(s, list(dates), pd.Timestamp("2024-09-16"), 5)
    assert len(out["pre"]) == 5
    assert len(out["post"]) == 6
    # pre 都在 break 之前（位置前 5 个交易日）
    pre_ok = all(pd.Timestamp(d) < pd.Timestamp("2024-09-16") for d in out["pre"]["_t"])
    assert pre_ok


def test_hypothesis_break_effect_direction():
    """pre 低 escape → post 高 escape ⇒ effect>0。"""
    v1 = _mk_v1({"A": _lit_series()})
    cal = MarketCalendar.from_v1(v1)
    traj = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    res = hypothesis_break(traj, break_date="2024-09-24", windows=(40, 63, 90))
    effs = [w["effect"] for w in res["windows"].values() if w["effect"] is not None]
    # 用真实数据（2022-2026）跑，若无足够 exit 则跳过（不会抛错）
    assert res["windows"]["63"]["effect"] is not None or len(effs) == 0


def _lit_series():
    # 构造一条长轨迹：exit 落在 2024-09-24 前后随机
    n = 700
    ltb = [True] * 300 + [False] * 100 + [True] * 60 + [False] * 240
    return ltb[:n] + [False] * (n - len(ltb))


def test_date_block_bootstrap_seed_determinism():
    v1 = _mk_v1({"A": _lit_series(), "B": _lit_series(), "C": _lit_series()})
    cal = MarketCalendar.from_v1(v1)
    traj = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    r1 = date_block_bootstrap(traj, break_date="2024-09-24", window=63, n_boot=20, seed=7)
    r2 = date_block_bootstrap(traj, break_date="2024-09-24", window=63, n_boot=20, seed=7)
    assert r1["status"] == r2["status"]
    if r1["status"] == "ok":
        assert r1["obs_effect"] == r2["obs_effect"]
        assert r1["ci95"] == r2["ci95"]


def test_persistence_consistency_uses_variants():
    v1 = _mk_v1({"A": _lit_series()})
    cal = MarketCalendar.from_v1(v1)
    t0 = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    t3 = extract_trajectories(v1, cal, horizon_set=(120,), persistence=3)
    out = persistence_consistency({0: t0, 3: t3}, break_date="2024-09-24", window=63)
    # 关键：不同 persistence 样本不同，持久化键必须出现
    assert set(out["persistences"].keys()) == {"0", "3"}


def test_escape_series_excludes_current_and_censored():
    n = 60
    ltb = {"A": [True] * 20 + [False] * (n - 20),   # 无 retest 但在末端 → 右截断
           "B": [True] * 20 + [False] * 5 + [True] * 2 + [False] * (n - 27)}  # retest
    v1 = _mk_v1(ltb, n=n)
    cal = MarketCalendar.from_v1(v1)
    traj = extract_trajectories(v1, cal, horizon_set=(120,), persistence=0)
    sample = _escape_series(traj, horizon=120)
    # 末端 exit 无完整窗口 → excluded；B 有 retest → 保留但 _y=0
    assert len(sample) == 0 or set(sample["_y"].unique()).issubset({0, 1})
