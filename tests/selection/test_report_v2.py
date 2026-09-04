"""Layer③ 报告 v2（Report Spec + Engine）渲染确定性测试。

逐字节 Parity：fixture JSON（canonical = 20260902，Selection V2）+ 固定 now_str →
Report Engine → 与冻结 golden 逐字节一致。改报告内容 = 改 report_spec.yaml 后重新冻结 golden。
20260827 为 V1 语义 legacy fixture，保留作历史对照，不作为 canonical golden。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.selection.report_engine import render_selection_html_v2

FIXTURES = Path(__file__).parent.parent / "fixtures" / "report"
CANONICAL_DATE = "20260902"
FIXED_NOW = "2026-09-03 12:00:00"


def _load(date: str = CANONICAL_DATE) -> tuple[dict, dict, str]:
    d = json.loads((FIXTURES / f"selection_{date}.json").read_text(encoding="utf-8"))
    meta = {k: d.get(k) for k in ("alignment", "layers", "coverage", "config_issues") if d.get(k)}
    return d["layer3"], meta, d["date"]


def test_render_matches_golden():
    rec, meta, date = _load()
    html = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    golden = (FIXTURES / f"selection_{CANONICAL_DATE}.golden.html").read_text(encoding="utf-8")
    assert html == golden


def test_render_deterministic():
    rec, meta, date = _load()
    a = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    b = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    assert a == b


def test_all_sections_present():
    """v0.12.1 8 段 IA：结论 → 主题成立 → ③A → ③B 载体 → ③C 时机 → Why → Next → 审计。"""
    rec, meta, date = _load()
    html = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    for probe in ("01 今日结论", "02 Theme Confirmation", "03 ③A Eligibility",
                  "04 ③B 表达载体", "05 ③C Timing", "06 Why Now",
                  "07 Next Trigger", "08 决策审计"):
        assert probe in html


def test_carrier_and_execution_decoupled():
    """核心拆解：③B 载体 ≠ ③C 可买 —— 载体行存在但执行=等待（9-02 AI 语义）。"""
    rec, meta, date = _load()
    html = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    assert "人工智能ETF易方达" in html          # ③B 载体
    assert "159819" in html
    assert "今日无可执行标的" in html           # 无 recommended
    assert "Lane1 未达趋势门" in html           # ③C 原因


def test_spec_is_source_of_truth():
    """核心 JSON contract 不含任何报告派生字段（不污染）。"""
    rec, meta, _ = _load()
    raw = json.dumps(rec, ensure_ascii=False)
    for banned in ("display_state", "reporting_state", "one_liner", "report_manifest",
                   "why_text", "next_trigger", "triggers"):
        assert banned not in raw
