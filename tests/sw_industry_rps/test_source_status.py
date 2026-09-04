"""Layer② 来源/完整性语义（V0.1）— 纯分类函数回归测试。

两个回归场景（用户锁定）：
  - 09-03 完整收盘 + fallback → data_status=complete / source_status=fallback
  - 09-04 盘中未收盘          → data_status=partial / source_status=fallback
"""
from __future__ import annotations

import pytest

from src.sw_industry_rps import source_status as ss


def test_source_status_fallback_realtime():
    """realtime 基底（兜底源）→ fallback，不得误判 primary。"""
    assert ss.classify_source_status(["realtime"]) == "fallback"
    assert ss.classify_source_status(["sw_realtime+ths_enrichment"]) == "fallback"
    assert ss.classify_source_status(["ths_board"]) == "fallback"


def test_source_status_primary_swsresearch():
    """申万官方 L1（swsresearch / analysis_daily / hist_sw）→ primary。"""
    assert ss.classify_source_status(["swsresearch"]) == "primary"
    assert ss.classify_source_status(["swsresearch_analysis"]) == "primary"
    assert ss.classify_source_status(["analysis_daily"]) == "primary"
    assert ss.classify_source_status(["hist_sw"]) == "primary"
    # 主源与兜底共存时仍判主源（realtime 只增强、不构成主源）
    assert ss.classify_source_status(["swsresearch", "ths_board"]) == "primary"


def test_source_status_empty_fallback():
    assert ss.classify_source_status([]) == "fallback"
    assert ss.classify_source_status(None) == "fallback"


def test_scenario_0903_complete_close_fallback():
    """09-03 目标日为完整收盘 + fallback → complete（不产生 provisional）。
    主源缺失、观测落在已完整收盘的交易日 → data_status=complete。
    """
    source_status = ss.classify_source_status(["realtime", "ths_board"])
    assert source_status == "fallback"
    data_status = ss.classify_data_status(source_status, is_complete=True)
    assert data_status == "complete"
    assert data_status != "provisional"


def test_scenario_0904_intraday_not_closed():
    """09-04 盘中未收盘 + fallback → partial（不得标 complete）。"""
    source_status = ss.classify_source_status(["realtime"])
    assert source_status == "fallback"
    data_status = ss.classify_data_status(source_status, is_complete=False)
    assert data_status == "partial"
    assert data_status != "complete"


def test_primary_always_confirmed():
    """主源确认 → confirmed（官方日线必然是完整收盘）。"""
    assert ss.classify_data_status("primary", is_complete=True) == "confirmed"
    # 主源即便在盘中判定也不降级（官方 L1 只在收盘确认后发布）
    assert ss.classify_data_status("primary", is_complete=False) == "confirmed"
