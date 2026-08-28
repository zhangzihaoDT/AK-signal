"""Layer③ 报告 v2（Report Spec + Engine）渲染确定性测试。

逐字节 Parity：fixture JSON + 固定 now_str → Report Engine → 与冻结 golden 逐字节一致。
改报告内容 = 改 report_spec.yaml 后重新冻结 golden（本测试是「内容改动被显式记录」的门）。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.selection.report_engine import render_selection_html_v2

FIXTURES = Path(__file__).parent.parent / "fixtures" / "report"
FIXED_NOW = "2026-08-28 12:00:00"


def _load(date: str = "20260827") -> tuple[dict, dict, str]:
    d = json.loads((FIXTURES / f"selection_{date}.json").read_text(encoding="utf-8"))
    meta = {k: d.get(k) for k in ("alignment", "layers", "coverage", "config_issues") if d.get(k)}
    return d["layer3"], meta, d["date"]


def test_render_matches_golden():
    rec, meta, date = _load()
    html = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    golden = (FIXTURES / "selection_20260827.golden.html").read_text(encoding="utf-8")
    assert html == golden


def test_render_deterministic():
    rec, meta, date = _load()
    a = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    b = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    assert a == b


def test_all_sections_present():
    rec, meta, date = _load()
    html = render_selection_html_v2(rec, FIXTURES, date, meta, now_str=FIXED_NOW)
    for probe in ("01 今日结论", "02 主题状态", "03 为什么", "04 怎么表达",
                  "05 风险与变化", "06 决策审计"):
        assert probe in html


def test_spec_is_source_of_truth():
    """核心 JSON contract 不含任何报告派生字段（不污染）。"""
    rec, meta, _ = _load()
    raw = json.dumps(rec, ensure_ascii=False)
    for banned in ("display_state", "reporting_state", "one_liner", "report_manifest"):
        assert banned not in raw
