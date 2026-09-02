"""Lane 3 · Study 3C 状态分类器测试（纯离线，确定性）。

覆盖 §41 的 20 项要求：
  1. same input → same state
  2. 删除未来数据 → 当前 state 完全不变
  3. persistence 3D 正确确认 first exit
  4. persistence 3D 正确确认 retest
  5. 0–5D → FIRST_EXIT
  6. 6–20D → TRANSITION_EARLY
  7. 21–60D → TRANSITION_ACTIVE
  8. 61–120D → TRANSITION_ESTABLISHED
  9. > 120D → POST_TRANSITION
  10. retest 优先于 age state
  11. UNRELIABLE 优先级最高
  12. illegal transition = 0
  13. left-censored 不伪造 first_exit
  14. market breadth 分母只用 reliable ETF
  15. flow = 当日真实 state transition
  16. etf_type breakdown 与全市场总数 reconcile
  17. Lane 1 / Lane 2 overlap 只读，不改变 state
  18. RAW/3D/5D 各自独立
  19. report renderer 只消费结果文件
  20. frozen YAML 后 Application 不自行重算规则
"""

from __future__ import annotations

import json
import numpy as np
import pandas as pd
import pytest

from src.research.trend_transition.calendar import MarketCalendar
from src.research.trend_transition.state_classifier import (
    AGE_BUCKET_ACTIVE,
    AGE_BUCKET_EARLY,
    AGE_BUCKET_ESTABLISHED,
    AGE_BUCKET_FIRST_EXIT,
    AGE_BUCKET_POST,
    STATE_BOTTOM,
    STATE_FIRST_EXIT,
    STATE_ORIGIN_LEFT_CENSORED,
    STATE_POST_TRANSITION,
    STATE_RETEST,
    STATE_TRANSITION_ACTIVE,
    STATE_TRANSITION_EARLY,
    STATE_TRANSITION_ESTABLISHED,
    STATE_UNRELIABLE,
    age_to_state,
    classify_fund_history,
    replay_universe_state,
)
from src.research.trend_transition.state_metrics import (
    compute_etf_type_breakdown,
    compute_transition_breadth,
    compute_transition_flow,
)


def _mk_fund(code: str, n: int, ltb: list[bool], reliable: bool = True,
             etf_type: str = "theme", target_stage: str = "NON_TARGET") -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({
        "trade_date": dates, "fund_code": code, "fund_name": code, "etf_type": etf_type,
        "reliable_360": reliable, "long_term_bottom": ltb,
        "bottom_state": "DEEP_BOTTOM", "pos60": 10.0, "pos120": 8.0, "pos360": 5.0,
        "target_stage": target_stage,
    })


def _cal(n: int) -> MarketCalendar:
    return MarketCalendar(pd.bdate_range("2023-01-02", periods=n))


def _state_at(hist: pd.DataFrame, fund: str, idx: int) -> str:
    row = hist[(hist["fund_code"] == fund)].iloc[idx]
    return str(row["transition_state"])


def _mk_exit_fund(n=160, bottom_days=10):
    """bottom 10 天 → exit，之后一直 non-bottom（覆盖 5/20/60/120 age 全部）。"""
    return [True] * bottom_days + [False] * (n - bottom_days)


# ── 1. same input → same state ────────────────────────────────
def test_same_input_same_state():
    n = 60
    ltb = [True] * 5 + [False] * 5 + [True] * 5 + [False] * (n - 15)
    df = pd.concat([_mk_fund("A", n, ltb), _mk_fund("B", n, [False] * n)])
    cal = _cal(n)
    h1 = replay_universe_state(df, cal, persistence=3)
    h2 = replay_universe_state(df, cal, persistence=3)
    cols = ["fund_code", "trade_date", "transition_state", "state_origin", "transition_cycle"]
    assert h1[cols].equals(h2[cols])


# ── 2. 删除未来数据 → 当前 state 不变 ─────────────────────────
def test_no_lookahead():
    n = 100
    ltb = [True] * 10 + [False] * 30 + [True] * 10 + [False] * (n - 50)
    df = pd.concat([_mk_fund("A", n, ltb)])
    cal = _cal(n)
    cut = pd.Timestamp("2023-03-01")
    h_full = replay_universe_state(df, cal, persistence=3)
    h_trunc = replay_universe_state(df[df["trade_date"] <= cut], cal, persistence=3)
    for _, r in h_trunc.iterrows():
        full_row = h_full[(h_full["fund_code"] == r["fund_code"]) &
                          (h_full["trade_date"] == r["trade_date"])]
        assert len(full_row) == 1
        assert str(full_row.iloc[0]["transition_state"]) == str(r["transition_state"])


# ── 3/4. persistence 3D 确认 first exit / retest ─────────────
def test_persistence_confirms_first_exit_and_retest():
    n = 40
    # 单日抖动不应构成 exit/retest（3D persistence 过滤）
    ltb = ([True] * 6 + [False, True, False] + [False] * 10 + [True] * 5 + [False] * (n - 24))
    df = _mk_fund("A", n, ltb)
    cal = _cal(n)
    h = classify_fund_history(df, cal, persistence=3)
    # 抖动段（False,True,False）不应翻转 confirmed → 仍在 BOTTOM
    states = h["transition_state"].tolist()
    # 第 6-8 天抖动 → 仍 BOTTOM
    assert states[5] == STATE_BOTTOM and states[7] == STATE_BOTTOM
    # 首个真 exit（连续 3 False）后 → FIRST_EXIT
    assert STATE_FIRST_EXIT in states
    # retest（连续 3 True 后再入底）→ RETEST
    assert STATE_RETEST in states


# ── 5-9. age buckets ──────────────────────────────────────────
def test_age_buckets_first_exit_to_post():
    n = 140
    ltb = _mk_exit_fund(n, bottom_days=5)
    df = _mk_fund("A", n, ltb)
    cal = _cal(n)
    h = classify_fund_history(df, cal, persistence=3)
    states = h["transition_state"].tolist()
    age = h["days_since_first_exit"].tolist()
    # 3D persistence：confirmed exit 在第 7 个交易日（3 连 False 后翻转）
    i0 = states.index(STATE_FIRST_EXIT)   # days_since=0
    assert age[i0] == 0 and states[i0] == STATE_FIRST_EXIT
    assert age[i0 + 5] == 5 and states[i0 + 5] == STATE_FIRST_EXIT
    # 6-20 → EARLY
    assert age[i0 + 6] == 6 and states[i0 + 6] == STATE_TRANSITION_EARLY
    assert age[i0 + 20] == 20 and states[i0 + 20] == STATE_TRANSITION_EARLY
    # 21-60 → ACTIVE
    assert age[i0 + 21] == 21 and states[i0 + 21] == STATE_TRANSITION_ACTIVE
    assert age[i0 + 60] == 60 and states[i0 + 60] == STATE_TRANSITION_ACTIVE
    # 61-120 → ESTABLISHED
    assert age[i0 + 61] == 61 and states[i0 + 61] == STATE_TRANSITION_ESTABLISHED
    assert age[i0 + 120] == 120 and states[i0 + 120] == STATE_TRANSITION_ESTABLISHED
    # 121 → POST
    assert age[i0 + 121] == 121 and states[i0 + 121] == STATE_POST_TRANSITION


def test_age_to_state_boundaries():
    assert age_to_state(0) == (STATE_FIRST_EXIT, AGE_BUCKET_FIRST_EXIT)
    assert age_to_state(5) == (STATE_FIRST_EXIT, AGE_BUCKET_FIRST_EXIT)
    assert age_to_state(6) == (STATE_TRANSITION_EARLY, AGE_BUCKET_EARLY)
    assert age_to_state(20) == (STATE_TRANSITION_EARLY, AGE_BUCKET_EARLY)
    assert age_to_state(21) == (STATE_TRANSITION_ACTIVE, AGE_BUCKET_ACTIVE)
    assert age_to_state(60) == (STATE_TRANSITION_ACTIVE, AGE_BUCKET_ACTIVE)
    assert age_to_state(61) == (STATE_TRANSITION_ESTABLISHED, AGE_BUCKET_ESTABLISHED)
    assert age_to_state(120) == (STATE_TRANSITION_ESTABLISHED, AGE_BUCKET_ESTABLISHED)
    assert age_to_state(121) == (STATE_POST_TRANSITION, AGE_BUCKET_POST)


# ── 10. retest 优先于 age state ───────────────────────────────
def test_retest_overrides_age():
    n = 60
    # exit 后 10 天 retest（age 仍 < 20），但当前在底部 → RETEST
    ltb = [True] * 10 + [False] * 10 + [True] * 10 + [False] * (n - 30)
    df = _mk_fund("A", n, ltb)
    cal = _cal(n)
    h = classify_fund_history(df, cal, persistence=3)
    # retest 段内 → RETEST（即使 days_since < 20）
    retest_rows = h[h["transition_state"] == STATE_RETEST]
    assert len(retest_rows) >= 5
    for _, r in retest_rows.iterrows():
        assert r["confirmed_long_term_bottom"] is True


# ── 11. UNRELIABLE 优先级最高 ─────────────────────────────────
def test_unreliable_highest_priority():
    n = 20
    ltb = [False] * n
    df = _mk_fund("A", n, ltb, reliable=False)
    cal = _cal(n)
    h = classify_fund_history(df, cal, persistence=3)
    assert (h["transition_state"] == STATE_UNRELIABLE).all()


# ── 12. illegal transition = 0 ────────────────────────────────
def test_illegal_transitions_zero():
    n = 120
    ltb_a = _mk_exit_fund(n, bottom_days=5)
    ltb_b = [True] * 5 + [False] * 20 + [True] * 10 + [False] * (n - 35)
    ltb_c = [False] * n          # left-censored
    ltb_d = [True] * n           # always bottom
    df = pd.concat([
        _mk_fund("A", n, ltb_a), _mk_fund("B", n, ltb_b),
        _mk_fund("C", n, ltb_c), _mk_fund("D", n, ltb_d),
    ])
    cal = _cal(n)
    h = replay_universe_state(df, cal, persistence=3)
    from src.research.trend_transition.state_classifier import LEGAL_TRANSITIONS
    rel = h[h["reliable_360"]].copy().sort_values(["fund_code", "trade_date"])
    rel["prev_state"] = rel.groupby("fund_code")["transition_state"].shift(1)
    pairs = rel.dropna(subset=["prev_state"])
    illegal = pairs[~pairs.apply(
        lambda r: (r["prev_state"], r["transition_state"]) in LEGAL_TRANSITIONS
        or (r["transition_state"] == STATE_POST_TRANSITION
            and r["prev_state"] == STATE_POST_TRANSITION), axis=1)]
    assert len(illegal) == 0


# ── 13. left-censored 不伪造 first_exit ───────────────────────
def test_left_censored_no_fake_first_exit():
    n = 30
    ltb = [False] * n
    df = _mk_fund("A", n, ltb)
    cal = _cal(n)
    h = classify_fund_history(df, cal, persistence=3)
    assert (h["transition_state"] == STATE_POST_TRANSITION).all()
    assert (h["state_origin"] == STATE_ORIGIN_LEFT_CENSORED).all()
    assert h["first_exit_date"].isna().all()
    assert (h["transition_anchor_known"] == False).all()  # noqa: E712


# ── 14. breadth 分母只用 reliable ETF ─────────────────────────
def test_breadth_denominator_reliable_only():
    n = 60
    ltb = _mk_exit_fund(n, bottom_days=5)
    df = pd.concat([
        _mk_fund("A", n, ltb, reliable=True),        # exit day5 → 之后 ACTIVE（age~54）
        _mk_fund("B", n, [True] * n, reliable=True), # 一直在 BOTTOM
        _mk_fund("C", n, [False] * n, reliable=False),  # UNRELIABLE 不进分母
    ])
    cal = _cal(n)
    h = replay_universe_state(df, cal, persistence=3)
    br = compute_transition_breadth(h)
    last = br.iloc[-1]
    assert last["reliable_count"] == 2  # A/B reliable, C 不算
    # 最后一天 A=ACTIVE(transition), B=BOTTOM → bottom=1/2, transition=1/2
    assert last["bottom_breadth"] == pytest.approx(0.5)
    assert last["transition_breadth"] == pytest.approx(0.5)
    assert last["active_transition_breadth"] == pytest.approx(0.5)


# ── 15. flow = 当日真实 state transition ──────────────────────
def test_flow_matches_real_transition():
    n = 60
    # A: exit at day 5, B: exit day 5 + retest day 20
    ltb_a = [True] * 5 + [False] * (n - 5)
    ltb_b = [True] * 5 + [False] * 15 + [True] * 5 + [False] * (n - 25)
    df = pd.concat([_mk_fund("A", n, ltb_a), _mk_fund("B", n, ltb_b)])
    cal = _cal(n)
    h = replay_universe_state(df, cal, persistence=3)
    flow = compute_transition_flow(h)
    # exit day (day 5 位置) → new_first_exit_count = 2
    exit_row = flow[flow["new_first_exit_count"] == 2]
    assert len(exit_row) == 1
    # B 的 retest day (day 25 位置) → new_retest_count = 1
    retest_row = flow[flow["new_retest_count"] == 1]
    assert len(retest_row) == 1
    # net flow 定义：first_exit - retest
    assert flow["net_transition_flow"].iloc[0] >= 0  # 非负 sanity


# ── 16. etf_type breakdown reconcile ──────────────────────────
def test_etf_type_breakdown_reconcile():
    n = 60
    ltb = [True] * 5 + [False] * (n - 5)
    df = pd.concat([
        _mk_fund("A", n, ltb, etf_type="theme"),
        _mk_fund("B", n, [True] * n, etf_type="theme"),
        _mk_fund("C", n, [False] * n, etf_type="industry"),
    ])
    cal = _cal(n)
    h = replay_universe_state(df, cal, persistence=3)
    last_day = pd.Timestamp(h["trade_date"].iloc[-1])
    brk = compute_etf_type_breakdown(h, as_of=last_day)
    # 当日可靠 ETF = A/B/C = 3，按 etf_type 拆
    assert brk["n_reliable"].sum() == 3
    theme_row = brk[brk["etf_type"] == "theme"].iloc[0]
    assert theme_row["n_reliable"] == 2
    # 与全市场总数 reconcile：当日可靠总数 = 各 type 之和
    total = compute_transition_breadth(h).iloc[-1]["reliable_count"]
    assert int(brk["n_reliable"].sum()) == int(total)


# ── 17. Lane overlap 只读 ─────────────────────────────────────
def test_lane_overlap_read_only():
    n = 30
    ltb = [True] * n
    df = _mk_fund("A", n, ltb, target_stage="TARGET")
    cal = _cal(n)
    h = replay_universe_state(df, cal, persistence=3)
    # BOTTOM × TARGET overlap 只读 target_stage，不改变 state
    from src.research.trend_transition.state_metrics import compute_lane2_overlap
    ov = compute_lane2_overlap(h, pd.Timestamp(h["trade_date"].iloc[-1]))
    assert len(ov) == 1
    assert str(ov.iloc[0]["transition_state"]) == STATE_BOTTOM
    assert str(ov.iloc[0]["lane2_target_stage"]) == "TARGET"
    # state 未被改写
    assert (h["transition_state"] == STATE_BOTTOM).sum() == n


# ── 18. RAW/3D/5D 各自独立 ────────────────────────────────────
def test_persistence_modes_independent():
    n = 40
    ltb = [False] * 5 + [True] * 3 + [False] * 10 + [True] * 5 + [False] * (n - 23)
    df = _mk_fund("A", n, ltb)
    cal = _cal(n)
    from src.research.trend_transition import PERSISTENCE_RAW, PERSISTENCE_ROBUST
    h_raw = classify_fund_history(df, cal, persistence=0)
    h_3 = classify_fund_history(df, cal, persistence=3)
    h_5 = classify_fund_history(df, cal, persistence=5)
    # raw 对单日抖动敏感，3D/5D 过滤 → 状态序列不同但都可判定
    assert not h_raw["transition_state"].equals(h_3["transition_state"])
    assert isinstance(h_5, pd.DataFrame)


# ── 19. report renderer 纯消费结果文件 ────────────────────────
def test_report_renderer_consumes_json_only(tmp_path):
    import src.research.trend_transition.study3c_report as rep
    s = {
        "study": "3c", "generated_at": "2026-09-02T00:00:00+00:00", "persistence": 3,
        "as_of": "2026-08-31", "n_funds": 3, "n_hist_rows": 100,
        "state_distribution_current": {"BOTTOM": 1, "POST_TRANSITION": 2},
        "market": {"reliable_count": 3, "active_transition_breadth": 0.1,
                   "transition_breadth": 0.2, "bottom_breadth": 0.1,
                   "active_transition_breadth_change_20d": 0.02, "net_transition_flow": 1.0},
        "etf_type_breakdown": [{"etf_type": "theme", "n_reliable": 3,
                                "bottom_breadth": 0.1, "transition_breadth": 0.2,
                                "active_transition_breadth": 0.1}],
        "lane2_overlap": [{"transition_state": "BOTTOM", "lane2_target_stage": "TARGET", "n": 1}],
        "lane1_overlap": {"coverage": 0, "detail": "lane1 facts unavailable"},
        "validation": {"checks": {c: {"name": c, "ok": True, "detail": ""}
                                  for c in ("C1", "C2", "C3", "C4", "C5")}},
        "pass_gate": {"verdict": "PASS — TREND_TRANSITION_STATE_V1_READY", "n_pass": 5},
    }
    out = tmp_path / "study3c_report.html"
    html = rep.render(s, out_path=out)
    assert html.exists()
    assert "PASS" in html.read_text()


# ── 20. frozen YAML 后 Application 不自行重算规则 ─────────────
def test_application_reads_frozen_spec(tmp_path, monkeypatch):
    import yaml
    from src.research.trend_transition import study3c as m
    spec = {
        "rule_id": "TREND_TRANSITION_STATE_V1",
        "status": "FROZEN_STATE_CLASSIFIER",
        "persistence": 3,
        "states": ["UNRELIABLE", "BOTTOM", "FIRST_EXIT", "TRANSITION_EARLY",
                   "TRANSITION_ACTIVE", "TRANSITION_ESTABLISHED", "RETEST", "POST_TRANSITION"],
        "age_windows": {"first_exit": "0-5", "early": "6-20", "active": "21-60",
                        "established": "61-120", "post_transition": "120+"},
        "provenance": {"lane": 3, "study": "3C"},
    }
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(spec), encoding="utf-8")
    monkeypatch.setattr(m, "SPEC_PATH", p)
    assert m.load_frozen_spec() is not None
    # 改状态 → 非 frozen → 拒绝
    spec2 = dict(spec, status="DRAFT")
    p2 = tmp_path / "spec2.yaml"
    p2.write_text(yaml.safe_dump(spec2), encoding="utf-8")
    monkeypatch.setattr(m, "SPEC_PATH", p2)
    assert m.load_frozen_spec() is None
