"""Layer② 第二问趋势阶段分类（Observation 语义：已形成/启动/退潮）"""
from __future__ import annotations

from src.sw_industry_rps.report import classify_trend_stage


def _row(**kw):
    base = {"RPS5": 50.0, "RPS15": 50.0, "falling_out": 0, "new_entry": 0}
    base.update(kw)
    return base


def test_formed_trend_high_rps15():
    # RPS15 站稳观察区且 RPS5 未明显回落 → 已形成趋势
    assert classify_trend_stage(_row(RPS15=90, RPS5=85)) == "已形成趋势"
    assert classify_trend_stage(_row(RPS15=82, RPS5=75)) == "已形成趋势"


def test_starting_rps5_leading_rps15():
    # 元件特征：RPS5=100 但 RPS15 很低 → 正在启动
    assert classify_trend_stage(_row(RPS5=100, RPS15=2.4)) == "正在启动"
    assert classify_trend_stage(_row(RPS5=96, RPS15=0.8)) == "正在启动"
    assert classify_trend_stage(_row(RPS5=85, RPS15=40)) == "正在启动"


def test_fading_falling_out():
    # falling_out=1 → 正在退潮
    assert classify_trend_stage(_row(RPS15=80, RPS5=60, falling_out=1)) == "正在退潮"


def test_fading_high_rps15_low_rps5():
    # 航运港口：RPS15=94 但 RPS5=35（强趋势降温）→ 正在退潮
    assert classify_trend_stage(_row(RPS15=94, RPS5=35)) == "正在退潮"
    assert classify_trend_stage(_row(RPS15=90, RPS5=55)) == "正在退潮"


def test_no_trend():
    # RPS15/RPS5 均低 → 不构成趋势
    assert classify_trend_stage(_row(RPS15=40, RPS5=50)) == "—"


def test_missing_rps15():
    assert classify_trend_stage(_row(RPS15=None)) == "—"
    assert classify_trend_stage(_row(RPS15="abc")) == "—"
