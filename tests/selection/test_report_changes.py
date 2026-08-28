"""Layer③ 05 跨日变化（report_changes）测试。

纪律验证：跨日 diff 读上一份已落盘 Layer③ JSON，fail-soft、版本检查、
绝不写回 recommendation 对象。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from src.selection.report_changes import (
    COMPARISON_NO_PREV, COMPARISON_OK, COMPARISON_VERSION_MISMATCH,
    build_changes, load_previous, resolve_previous_path,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "report"


def _load_layer3(date: str = "20260827") -> dict:
    d = json.loads((FIXTURES / f"selection_{date}.json").read_text(encoding="utf-8"))
    return d["layer3"]


def _theme(layer3: dict, key: str) -> dict:
    for b in layer3["buckets"]:
        for t in b["themes"]:
            if t.get("theme") == key:
                return t
    raise KeyError(key)


class TestWithinDay:
    def test_no_prev_status(self):
        rec = _load_layer3()
        entries, changed, status = build_changes(rec, prev=None)
        assert status == COMPARISON_NO_PREV
        # 日内 notable：高现金流/汽车表达降级 + ETF 0/xx 无一通过
        texts = " ".join(e.text for e in entries)
        assert "表达降级" in texts
        assert any(e.kind == "fallback" for e in entries)

    def test_breakdown_within_day(self):
        rec = _load_layer3()
        entries, _, _ = build_changes(rec, prev=None)
        breakdown = [e for e in entries if e.kind == "breakdown"]
        assert breakdown and any("破位" in e.text for e in breakdown)


class TestCrossDay:
    def test_new_degradation_detected(self):
        rec = _load_layer3()
        prev = copy.deepcopy(rec)
        # 昨日高现金流为 NORMAL（结构==执行）
        t = _theme(prev, "high_cashflow")
        t["expression_status"] = "NORMAL"
        t["structural_expression"] = "ETF_PRIORITY"
        t["execution_expression"] = "ETF_PRIORITY"
        t["today"]["expression_status"] = "NORMAL"
        entries, changed, status = build_changes(rec, prev)
        assert status == COMPARISON_OK
        degraded = [e for e in entries if e.kind == "degraded" and "高现金流" in e.theme_label]
        assert any("新增表达降级" in e.text for e in degraded)
        assert "high_cashflow" in changed

    def test_new_confirmation_detected(self):
        rec = _load_layer3()
        prev = copy.deepcopy(rec)
        t = _theme(prev, "ai_infrastructure")
        t["confirmed"] = False
        t["today"]["action"] = "WAIT"
        entries, changed, status = build_changes(rec, prev)
        assert status == COMPARISON_OK
        confirmed = [e for e in entries if e.kind == "confirmed"]
        assert confirmed and "进入确认状态" in confirmed[0].text
        assert "ai_infrastructure" in changed

    def test_new_recommended_detected(self):
        rec = _load_layer3()
        prev = copy.deepcopy(rec)
        # 昨日中国移动未推荐
        t = _theme(prev, "high_cashflow")
        rec_list = t["recommendation"]["stocks"]
        for a in rec_list:
            if a.get("code") == "600941":
                a["recommended"] = False
        entries, _, _ = build_changes(rec, prev)
        recd = [e for e in entries if e.kind == "recommended"]
        assert any("中国移动" in e.text for e in recd)

    def test_version_mismatch_no_diff(self):
        rec = _load_layer3()
        prev = copy.deepcopy(rec)
        prev["version"] = "0.4.0"
        entries, _, status = build_changes(rec, prev)
        assert status == COMPARISON_VERSION_MISMATCH
        # 跨日专属措辞（「新增/退出」）不出现；仅日内条目（表达降级）存在
        assert not any("新增表达降级" in e.text for e in entries)
        assert not any("主题进入确认状态" in e.text for e in entries)


class TestPrevResolution:
    def test_load_previous_corrupt_fail_soft(self, tmp_path):
        p = tmp_path / "tradable_candidates_20260826.json"
        p.write_text("{not-json", encoding="utf-8")
        assert load_previous(p) is None

    def test_resolve_previous_picks_nearest(self, tmp_path):
        (tmp_path / "tradable_candidates_20260825.json").write_text("{}", encoding="utf-8")
        (tmp_path / "tradable_candidates_20260826.json").write_text("{}", encoding="utf-8")
        (tmp_path / "tradable_candidates_20260827.json").write_text("{}", encoding="utf-8")
        got = resolve_previous_path(tmp_path, "20260827")
        assert got is not None and got.name == "tradable_candidates_20260826.json"
        prev = resolve_previous_path(tmp_path, "20260826")
        assert prev is not None and prev.name == "tradable_candidates_20260825.json"

    def test_diff_never_mutates_input(self):
        rec = _load_layer3()
        prev = copy.deepcopy(rec)
        snap = json.dumps(rec, ensure_ascii=False)
        build_changes(rec, prev)
        assert json.dumps(rec, ensure_ascii=False) == snap
