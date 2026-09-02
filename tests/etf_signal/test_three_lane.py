"""三 Lane 合成表（three_lane）测试。

覆盖：
  - join 确定性 / 字段透传不重算
  - 「Lane 2 底部 = 是/否」用 raw long_term_bottom（不改 3C confirmed）
  - 三条 Lane 各自保留原始枚举（机器层不翻译）
  - 活跃路径子集判定（底部 ∪ 迁移中 ∪ 趋势活跃）
  - 全量折叠 / 展示层翻译在 report renderer（纯消费，不重判）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.etf_signal import three_lane as tl


def _mk_watchlist() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ["A", "B", "C", "D", "E"],
        "fund_name": ["ETF-A", "ETF-B", "ETF-C", "ETF-D", "ETF-E"],
        "trend_state": ["BUY_CANDIDATE", "WATCH", "OUT_OF_SCOPE", "STRONG_WATCH", "WATCH"],
        "_watchlist_date": pd.Timestamp("2026-08-31"),
    })


def _mk_v1() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ["A", "B", "C", "D", "E"],
        "fund_name": ["ETF-A", "ETF-B", "ETF-C", "ETF-D", "ETF-E"],
        "etf_type": ["theme", "industry", "broad", "theme", "dividend"],
        "long_term_bottom": [True, True, False, False, False],
        "bottom_state": ["DEEP_BOTTOM", "RECOVERING_FROM_BOTTOM", "NORMAL", "NORMAL", "NORMAL"],
        "target_stage": ["TARGET", "NEAR_MISS", "NON_TARGET", "NON_TARGET", "NON_TARGET"],
        "reliable_360": [True, True, True, True, True],
        "pos120": [5.0, 12.0, 40.0, 30.0, 60.0],
    })


def _mk_state() -> pd.DataFrame:
    return pd.DataFrame({
        "fund_code": ["A", "B", "C", "D", "E"],
        "transition_state": ["BOTTOM", "FIRST_EXIT", "TRANSITION_ACTIVE", "POST_TRANSITION", "UNRELIABLE"],
        "days_since_first_exit": [np.nan, 3.0, 30.0, 200.0, np.nan],
        "confirmed_long_term_bottom": [True, False, False, False, False],
        "lane1_leadership_state": [None, None, None, None, None],
    })


def test_join_deterministic_and_transparent():
    df1 = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_mk_watchlist(), v1_day=_mk_v1(), state=_mk_state(),
    )
    df2 = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_mk_watchlist(), v1_day=_mk_v1(), state=_mk_state(),
    )
    assert df1.equals(df2)

    # 字段透传：raw long_term_bottom 原样保留（Lane 2 事实），不改成 confirmed
    a = df1[df1["fund_code"] == "A"].iloc[0]
    assert bool(a["lane2_long_term_bottom"]) is True  # raw 底部
    assert a["lane3_transition_state"] == "BOTTOM"    # 状态机
    assert a["lane2_target_stage"] == "TARGET"
    assert a["lane1_trend_state"] == "BUY_CANDIDATE"
    # 机器枚举原样（无中文翻译）
    assert a["lane2_bottom_state"] == "DEEP_BOTTOM"


def test_raw_bottom_not_overwritten_by_confirmed():
    """边缘日：Lane 2 raw 底部 = 是，但 Lane 3 confirmed 已切换 → 允许真实差异。"""
    df = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_mk_watchlist(), v1_day=_mk_v1(), state=_mk_state(),
    )
    b = df[df["fund_code"] == "B"].iloc[0]
    # B: raw long_term_bottom=True，confirmed=False（已确认切换），state=FIRST_EXIT
    assert bool(b["lane2_long_term_bottom"]) is True
    assert bool(b["lane3_confirmed_long_term_bottom"]) is False
    assert b["lane3_transition_state"] == "FIRST_EXIT"
    # 两条 Lane 语义边界不混淆


def test_active_path_subset():
    df = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_mk_watchlist(), v1_day=_mk_v1(), state=_mk_state(),
    )
    active = df[df.apply(tl.is_active_path, axis=1)]
    codes = set(active["fund_code"])
    # A(底部∩BUY) B(底部∩FIRST_EXIT∩WATCH) C(ACTIVE) D(POST 但 STRONG_WATCH→Lane1 活跃) 
    # E(UNRELIABLE 且 WATCH→Lane1 活跃)
    assert codes == {"A", "B", "C", "D", "E"}
    # 纯 OUT_OF_SCOPE + 非底部 + POST → 不活跃
    df2 = df.copy()
    df2.loc[df2["fund_code"] == "D", "lane1_trend_state"] = "OUT_OF_SCOPE"
    df2.loc[df2["fund_code"] == "E", "lane1_trend_state"] = "OUT_OF_SCOPE"
    # D 是 POST（非迁移中），现在 Lane1 也 OUT → 不活跃
    assert not tl.is_active_path(df2[df2["fund_code"] == "D"].iloc[0])
    # E 是 UNRELIABLE + OUT → 不活跃
    assert not tl.is_active_path(df2[df2["fund_code"] == "E"].iloc[0])


def test_full_table_retained_for_audit():
    """全量折叠保留（审计用），活跃只是展示子集。"""
    df = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_mk_watchlist(), v1_day=_mk_v1(), state=_mk_state(),
    )
    assert len(df) == 5          # 全部 fund
    assert len(df[df.apply(tl.is_active_path, axis=1)]) == 5  # 示例全活跃


def test_report_section_renderer_pure(tmp_path):
    """report renderer 只消费 three_lane DataFrame，不重算不 join。"""
    from src.etf_signal import rotation_report as rr

    df = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_mk_watchlist(), v1_day=_mk_v1(), state=_mk_state(),
    )
    html = rr._three_lane_section(df, "20260831")
    assert "④ 三 Lane 路径视图" in html
    assert "趴在底部" in html
    assert "Lane 2 底部" in html and "Lane 3 迁移" in html and "Lane 1 趋势" in html
    # 展示层中文翻译（机器枚举保留在数据层）
    assert "刚离底部" in html
    assert "买入候选" in html
    # 空数据 → 空串（不抛错）
    assert rr._three_lane_section(pd.DataFrame(), "20260831") == ""


def test_report_lane1_lag_flag(tmp_path):
    """对齐（_watchlist_date == trade_date）无 flag；信号日错位必须显式标记 Lane 1 滞后。"""
    from src.etf_signal import rotation_report as rr

    def _wl(dt):
        w = _mk_watchlist().copy()
        w["_watchlist_date"] = pd.Timestamp(dt)
        return w

    aligned = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_wl("2026-08-31"), v1_day=_mk_v1(), state=_mk_state(),
    )
    lagged = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_wl("2026-08-28"), v1_day=_mk_v1(), state=_mk_state(),
    )

    html_aligned = rr._three_lane_section(aligned, "20260831")
    html_lagged = rr._three_lane_section(lagged, "20260831")
    assert "Lane 1 滞后" not in html_aligned
    assert "Lane 1 滞后" in html_lagged
    assert "Lane 1 滞后 3 交易日" in html_lagged  # 08-31 vs 08-28 = 3 自然日
    assert "勿把时间差当状态先后" in html_lagged


def test_write_products_date_stamped(tmp_path):
    df = tl.build_three_lane(
        trade_date=pd.Timestamp("2026-08-31"),
        watchlist=_mk_watchlist(), v1_day=_mk_v1(), state=_mk_state(),
    )
    pq, csv = tl.write_products(df, out_dir=tmp_path)
    assert pq.name == "three_lane_20260831.parquet"
    assert csv.name == "three_lane_20260831.csv"
    back = pd.read_parquet(pq)
    assert len(back) == len(df)
    assert set(back["fund_code"]) == set(df["fund_code"])
    assert back["lane3_transition_state"].tolist() == df["lane3_transition_state"].tolist()
