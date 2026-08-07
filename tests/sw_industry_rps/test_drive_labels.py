"""驱动模式展示层映射（machine → human），独立于 confirmation Policy"""
from __future__ import annotations

from src.sw_industry_rps import drive_labels as dl


def test_all_16_combinations_have_label():
    contributions = ["single_core", "leader_concentrated", "multi_leader", "distributed"]
    breadths = ["broad", "moderate", "narrow", "divergent"]
    for cs in contributions:
        for bs in breadths:
            label = dl.composite_drive_label(cs, bs)
            assert label, f"empty label for ({cs}, {bs})"
            assert label != dl._UNKNOWN_LABEL, f"missing mapping for ({cs}, {bs})"


def test_composite_labels_semantics():
    assert dl.composite_drive_label("single_core", "broad") == "龙头拉动普涨"
    assert dl.composite_drive_label("single_core", "narrow") == "龙头独涨"
    assert dl.composite_drive_label("multi_leader", "broad") == "多股共振普涨"
    assert dl.composite_drive_label("distributed", "broad") == "分散普涨"
    assert dl.composite_drive_label("leader_concentrated", "divergent") == "龙头集中、内部分化"


def test_composite_unknown_fallback():
    assert dl.composite_drive_label("", "") == dl._UNKNOWN_LABEL
    assert dl.composite_drive_label(None, None) == dl._UNKNOWN_LABEL
    assert dl.composite_drive_label("bogus", "broad") == dl._UNKNOWN_LABEL
    assert dl.composite_drive_label("single_core", "bogus") == dl._UNKNOWN_LABEL
    assert dl.composite_drive_label("数据不足", "broad") == dl._UNKNOWN_LABEL
    assert dl.composite_drive_label("insufficient", "broad") == dl._UNKNOWN_LABEL


def test_drive_detail_dual_dimension():
    detail = dl.drive_detail("single_core", "broad", 0.67, 0.84)
    assert "贡献：单核主导" in detail
    assert "Top1=67%" in detail
    assert "参与：广泛上涨" in detail
    assert "84%" in detail


def test_drive_detail_missing_numeric():
    detail = dl.drive_detail("single_core", "broad")
    assert "Top1=" not in detail
    assert "贡献：单核主导" in detail
    assert "参与：广泛上涨" in detail


def test_drive_detail_unknown_dim():
    detail = dl.drive_detail("", "")
    assert "贡献：—" in detail
    assert "参与：—" in detail
