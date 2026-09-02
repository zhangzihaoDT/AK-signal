"""Repair-Retest V1 触发频率回测（backtest_v1）测试。

覆盖（纯逻辑，尽量不触发 78s 全市场跑批）：
  - 冻结规则真源读取 + _verify_frozen（拒绝自适应性重算 cut）
  - _stage_events 邻接合并（连续同 stage → 1 事件；隔日再入 → 新事件）
  - _stage_events IN_DOMAIN_NON_TARGET 的 stage 标注（回归：曾把 stage 存为
    NON_TARGET 导致 forward 对照 0 事件）
  - _zero_target_streaks 空窗
  - _near_miss_conversion 无 look-ahead
  - _verdict 判据（稀疏 / 集中 / 增量）
  - renderer 是纯 renderer，只消费 v1_incidence_summary.json
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.research.etf_bottom import STUDY_DIR
from src.research.etf_bottom.backtest_v1 import (
    _build_signal_daily,
    _near_miss_conversion,
    _stage_events,
    _verdict,
    _zero_target_streaks,
)
from src.research.etf_bottom.current_eval import load_frozen_cutpoints
from src.research.etf_bottom.scanner import classify_target, _verify_frozen


# ── 1. 冻结规则 ───────────────────────────────────────────────────

def test_backtest_v1_reads_frozen_cutpoints():
    """只能读冻结 cut points，绝不重算。"""
    cut120, cut60 = load_frozen_cutpoints()
    _verify_frozen(cut120, cut60)  # 漂移会抛错
    # 冻结值原样（与 repair_retest_v1.yaml 一致）
    assert cut120[1] == 9.88 and cut120[2] == 15.82
    assert cut60[1] == 14.55 and cut60[2] == 22.12


def test_backtest_v1_classify_uses_frozen_cut():
    """classify_target 用冻结 cut：Q1(pos60)×Q3(pos120) → TARGET，绝不重算。"""
    cut120, cut60 = load_frozen_cutpoints()
    # 域内：p60 Q1 (<14.55)，p120 Q3 (>15.82)
    assert classify_target(1.0, 18.0, cut60, cut120)[0] == "TARGET"
    # Q1×Q2 → NEAR_MISS p120 差一档
    st, near = classify_target(1.0, 12.0, cut60, cut120)
    assert st == "NEAR_MISS" and near == "P120_ONE_BUCKET_AWAY"
    # Q2×Q3 → NEAR_MISS p60 差一档
    st, near = classify_target(18.0, 18.0, cut60, cut120)
    assert st == "NEAR_MISS" and near == "P60_ONE_BUCKET_AWAY"
    # 缺失 → NON_TARGET（不冒充）
    assert classify_target(None, 18.0, cut60, cut120)[0] == "NON_TARGET"


# ── 2. _stage_events 邻接合并 ─────────────────────────────────────

def _mk_daily(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_stage_events_merges_consecutive_and_splits_on_gap():
    """同一 ETF 连续同 stage → 1 事件；隔 ≥1 交易日再入 → 新事件。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07",
                            "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13"])
    stage = ["TARGET", "TARGET", "TARGET", "NON_TARGET", "NON_TARGET", "TARGET", "TARGET"]
    daily = _mk_daily([
        {"trade_date": d, "fund_code": "000001", "target_stage": s,
         "in_domain": i < 5, "pos60": 1.0, "pos120": 18.0, "pos360": 5.0,
         "near_miss_reason": None, "watch_pool": False, "fund_name": "X",
         "industry_cluster": "OTHER", "etf_type": "theme", "bottom_state": "DEEP_BOTTOM"}
        for i, (d, s) in enumerate(zip(dates, stage))
    ])
    events = _stage_events(daily, "TARGET")
    # 3 连续（1 event）+ 隔 2 天 NON_TARGET 后再入（1 event）= 2 个事件
    assert len(events) == 2
    assert events[0]["duration_days"] == 3
    assert events[1]["duration_days"] == 2
    assert events[0]["stage"] == "TARGET" and events[1]["stage"] == "TARGET"


def test_stage_events_in_domain_non_target_labels_stage():
    """回归：IN_DOMAIN_NON_TARGET 事件的 stage 必须标注正确（曾是 NON_TARGET 导致对照 0 事件）。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    daily = _mk_daily([
        {"trade_date": d, "fund_code": "000001", "target_stage": "NON_TARGET",
         "in_domain": True, "pos60": 30.0, "pos120": 18.0, "pos360": 5.0,
         "near_miss_reason": None, "watch_pool": False, "fund_name": "X",
         "industry_cluster": "OTHER", "etf_type": "theme", "bottom_state": "DEEP_BOTTOM"}
        for d in dates
    ])
    events = _stage_events(daily, "IN_DOMAIN_NON_TARGET")
    assert len(events) == 1
    assert events[0]["stage"] == "IN_DOMAIN_NON_TARGET"


def test_stage_events_handles_all_stages_empty():
    """无该 stage 时返回空列表，不报错。"""
    dates = pd.to_datetime(["2026-01-05"])
    daily = _mk_daily([
        {"trade_date": d, "fund_code": "000001", "target_stage": "NON_TARGET",
         "in_domain": False, "pos60": None, "pos120": None, "pos360": None,
         "near_miss_reason": None, "watch_pool": False, "fund_name": "X",
         "industry_cluster": "OTHER", "etf_type": "theme", "bottom_state": "NORMAL"}
        for d in dates
    ])
    assert _stage_events(daily, "TARGET") == []


# ── 3. _zero_target_streaks ───────────────────────────────────────

def test_zero_target_streaks():
    days = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07",
                           "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13"])
    daily = _mk_daily([
        {"trade_date": d, "fund_code": "000001",
         "target_stage": s}
        for d, s in zip(days, ["TARGET", "TARGET", "NON", "NON", "TARGET", "NON", "TARGET"])
    ])
    streaks = _zero_target_streaks(daily, days.tolist())
    last = max(s["trading_days"] for s in streaks)
    assert last >= 1
    # 大多数有 TARGET 的日子不应计入"0 空窗"
    assert len(streaks) == 2  # 01-07..08 与 01-12


# ── 4. _near_miss_conversion 无 look-ahead ────────────────────────

def test_near_miss_conversion_no_lookahead():
    """转化只统计事件起始日之后的数据，绝不使用之前/当日之后未成熟信息。"""
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"])
    daily = _mk_daily([
        {"trade_date": d, "fund_code": "000001",
         "target_stage": "NEAR_MISS" if i == 0 else "TARGET",
         "in_domain": True, "pos60": 18.0, "pos120": 18.0, "pos360": 5.0,
         "near_miss_reason": "P60_ONE_BUCKET_AWAY", "watch_pool": False,
         "fund_name": "X", "industry_cluster": "OTHER", "etf_type": "theme",
         "bottom_state": "DEEP_BOTTOM"}
        for i, d in enumerate(dates)
    ])
    near = _stage_events(daily, "NEAR_MISS")
    conv = _near_miss_conversion(daily, near)
    # 1 个 NEAR_MISS 事件，紧随其后即 TARGET → 5d 转化率 1.0
    assert conv["near_miss_events"] == 1
    assert conv["conversion_5d"] == 1.0


# ── 5. _verdict 判据 ──────────────────────────────────────────────

def _mk_fwd(t_med, t_exc, d_exc, n_target_ev, top_year, top1, clusters):
    return {
        "TARGET": {"n_events": n_target_ev, "horizons": {"120": {"median": t_med, "excess_median": t_exc}}},
        "IN_DOMAIN_NON_TARGET": {"n_events": 999, "horizons": {"120": {"excess_median": d_exc}}},
        "industry_cluster": clusters,
    }


def _mk_inc(rate, total_days, tev):
    return {"target_day_rate": rate, "total_trade_days": total_days}


def _mk_streak(longest):
    return {"longest_zero_target_streak": longest}


def test_verdict_sparse_when_rate_low():
    fwd = _mk_fwd(0.08, 0.02, -0.016, 800, 0.3, 0.01, [{"industry_cluster": "游戏传媒", "event_share": 0.02}])
    assert _verdict(_mk_inc(0.01, 1000, 0), _mk_streak(50), _time_rep(0.3, 0.01, [0.02]), fwd) == "B_TOO_SPARSE"


def test_verdict_sparse_when_long_streak():
    fwd = _mk_fwd(0.08, 0.02, -0.016, 800, 0.3, 0.01, [{"industry_cluster": "游戏传媒", "event_share": 0.02}])
    assert _verdict(_mk_inc(0.2, 1000, 0), _mk_streak(250), _time_rep(0.3, 0.01, [0.02]), fwd) == "B_TOO_SPARSE"


def test_verdict_cluster_dependent_when_concentrated():
    # 单产业簇 90%（非 OTHER）→ 集中
    fwd = _mk_fwd(0.08, 0.02, -0.016, 200, 0.3, 0.01, [{"industry_cluster": "游戏传媒", "event_share": 0.9}])
    assert _verdict(_mk_inc(0.5, 1000, 0), _mk_streak(10), _time_rep(0.3, 0.01, [0.9]), fwd) == "C_TIME_OR_CLUSTER_DEPENDENT"


def test_verdict_other_cluster_not_concentration():
    """'OTHER'（未归入命名产业簇）不算集中度：绝大多数 ETF 都不在 6 个命名簇里。"""
    fwd = _mk_fwd(0.08, 0.02, -0.016, 800, 0.3, 0.01,
                  [{"industry_cluster": "OTHER", "event_share": 0.94},
                   {"industry_cluster": "游戏传媒", "event_share": 0.02}])
    # 0.94 全部落在 OTHER → 不看它；命名簇 top=0.02 < 0.8 → 不判集中
    assert _verdict(_mk_inc(0.5, 1000, 0), _mk_streak(10), _time_rep(0.3, 0.01, [0.02]), fwd) != "C_TIME_OR_CLUSTER_DEPENDENT"


def test_verdict_healthy_low_frequency():
    fwd = _mk_fwd(0.10, 0.06, -0.016, 800, 0.3, 0.01, [{"industry_cluster": "游戏传媒", "event_share": 0.02}])
    # delta = (0.06 - (-0.016))*100 = 7.6pp > 3.0；中位>0 → incr
    assert _verdict(_mk_inc(0.2, 1000, 0), _mk_streak(60), _time_rep(0.3, 0.01, [0.02]), fwd) == "A_HEALTHY_LOW_FREQUENCY"


def test_verdict_no_incremental_odds():
    # delta < 3pp → 不稀疏、不集中但无增量
    fwd = _mk_fwd(0.0832, 0.0011, -0.0162, 866, 0.3, 0.01, [{"industry_cluster": "游戏传媒", "event_share": 0.02}])
    assert _verdict(_mk_inc(0.2, 1000, 0), _mk_streak(60), _time_rep(0.3, 0.01, [0.02]), fwd) == "D_NO_INCREMENTAL_ODDS"


def _time_rep(top_year, top1_etf, cluster_shares):
    return {
        "top_year_share": top_year,
        "etf": {"top1_etf_contribution": top1_etf},
        "industry_cluster": [{"industry_cluster": "OTHER", "event_share": max(0.0, 1 - sum(cluster_shares))}] +
                            [{"industry_cluster": f"C{i}", "event_share": s} for i, s in enumerate(cluster_shares)],
    }


# ── 6. renderer 纯消费 ─────────────────────────────────────────────

def test_backtest_v1_renderer_consumes_json_only():
    """HTML renderer 只消费 v1_incidence_summary.json，不重新计算任何事实。"""
    from src.research.etf_bottom.backtest_v1_report import render_v1_backtest
    path = STUDY_DIR / "backtest_v1" / "v1_incidence_summary.json"
    if not path.exists():
        pytest.skip("backtest_v1 尚未运行，跳过 renderer 消费测试")
    payload = json.loads(path.read_text(encoding="utf-8"))
    html = render_v1_backtest(payload)
    # 纯 renderer：返回 HTML 文件路径，且结果必须包含 verdict（渲染层机器码→人类标签）
    html_content = open(html, encoding="utf-8").read()
    assert "v1_backtest_report.html" in html
    from src.research.etf_bottom.backtest_v1_report import _VERDICT_LABEL
    assert _VERDICT_LABEL[payload["verdict"]] in html_content
    # 报告里出现的核心数字必须来自 payload，而非重算
    inc = payload["incidence"]
    assert str(inc["total_trade_days"]) in html_content


def test_backtest_v1_adjudication_matches_verdict():
    """判据命中表必须与 verdict 自洽（纯 renderer 只重排已有值，不引入新逻辑）。

    D（无增量赔率）→ 增量判据行必须「命中」；稀疏/集中度两行「通过」。
    """
    import re
    from src.research.etf_bottom.backtest_v1_report import render_v1_backtest, _VERDICT_LABEL
    path = STUDY_DIR / "backtest_v1" / "v1_incidence_summary.json"
    if not path.exists():
        pytest.skip("backtest_v1 尚未运行，跳过")
    payload = json.loads(path.read_text(encoding="utf-8"))
    html = render_v1_backtest(payload)
    tbl = open(html, encoding="utf-8").read()
    verdict = payload["verdict"]
    assert verdict in _VERDICT_LABEL
    yes = tbl.count(">命中</span>")
    no = tbl.count(">通过</span>")
    # 3 行判据：根据 verdict 断言命中数
    if verdict == "A_HEALTHY_LOW_FREQUENCY":
        assert yes == 0 and no == 3
    elif verdict in ("B_TOO_SPARSE", "C_TIME_OR_CLUSTER_DEPENDENT"):
        assert yes >= 1
    elif verdict == "D_NO_INCREMENTAL_ODDS":
        assert yes == 1 and no == 2
