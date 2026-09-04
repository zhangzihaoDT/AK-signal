"""Layer③ 报告 Spec schema 校验测试（fail-fast）。"""

from __future__ import annotations

import copy
import yaml

import pytest

from src.selection.report_spec import (
    REPORT_SPEC_PATH, ReportSpecError, load_report_spec, validate_report_spec,
)


def _raw() -> dict:
    data = yaml.safe_load(REPORT_SPEC_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_load_current_spec():
    spec = load_report_spec()
    assert spec.spec_version == 2
    assert [s.id for s in spec.sections] == [
        "executive", "theme_confirmation", "eligibility", "vehicle",
        "timing", "why_now", "next_trigger", "audit"]


def test_bad_spec_version():
    raw = _raw()
    raw["report"]["spec_version"] = 99
    with pytest.raises(ReportSpecError):
        validate_report_spec(raw)


def test_bad_formatter_rejected():
    raw = _raw()
    raw["report"]["theme_confirmation_columns"][0]["fmt"] = "not_a_formatter"
    with pytest.raises(ReportSpecError):
        validate_report_spec(raw)


def test_bad_unknown_renderer_rejected():
    raw = _raw()
    raw["report"]["sections"][0]["renderer"] = "unknown_renderer"
    with pytest.raises(ReportSpecError):
        validate_report_spec(raw)


def test_bad_display_priority_rejected():
    raw = _raw()
    raw["report"]["display_priority"].append("bogus")
    with pytest.raises(ReportSpecError):
        validate_report_spec(raw)


def test_missing_why_labels_rejected():
    raw = _raw()
    del raw["report"]["why_labels"]
    with pytest.raises(ReportSpecError):
        validate_report_spec(raw)


def test_audit_category_ids_registered():
    spec = load_report_spec()
    ids = [s.id for s in spec.audit["etf_matrix"].summary]
    assert {"eligible", "vehicle", "timing_ready", "unreliably", "below_trend"} <= set(ids)
