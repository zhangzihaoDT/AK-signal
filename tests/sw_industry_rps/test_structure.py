from __future__ import annotations

import pandas as pd

from src.sw_industry_rps import structure


def test_compute_structure_scope_trend_and_focus():
    snap = pd.DataFrame({
        "industry_code": ["A", "B", "C", "D"],
        "industry_name": ["强1", "观1", "弱1", "焦点"],
        "strength_level": ["强势", "观察", "弱势", "中性"],
    })
    scope, trend, focus = structure.compute_structure_scope(snap)
    # 强势/观察 = 趋势
    assert "A" in trend and "B" in trend and "C" not in trend
    # scope = trend ∪ focus
    assert set(scope) >= set(trend)
    assert set(scope) >= set(focus)


def test_compute_structure_scope_empty_snapshot():
    scope, trend, focus = structure.compute_structure_scope(pd.DataFrame())
    assert scope == sorted(set(focus))
    assert trend == []


def test_structure_status_available_vs_insufficient():
    class FakeDD:
        contribution_structure = "leader_concentrated"
        breadth_structure = "broad"
        reconstruction_quality = "good"

    class FakeDDPoor:
        contribution_structure = "数据不足"
        breadth_structure = ""
        reconstruction_quality = "poor（Top1 未获取）"

    status, _ = structure._structure_status_from_drilldown(FakeDD())
    assert status == "available"

    status2, _ = structure._structure_status_from_drilldown(FakeDDPoor())
    assert status2 == "insufficient"


def test_structure_status_empty_contribution():
    class FakeDD:
        contribution_structure = ""
        breadth_structure = ""
        reconstruction_quality = ""

    status, _ = structure._structure_status_from_drilldown(FakeDD())
    assert status == "insufficient"


def test_focus_codes_matches_config():
    codes = structure.focus_codes()
    assert isinstance(codes, list)
    assert len(codes) > 0
    assert all(isinstance(c, str) for c in codes)
