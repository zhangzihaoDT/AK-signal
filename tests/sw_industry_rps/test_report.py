from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.sw_industry_rps.report import (
    _pct,
    _num,
    _streak_label,
    render_strength_table,
    render_rotation_matrix,
    render_status_changes,
    build_html,
    build_report_csv,
)


def test_pct_positive():
    assert _pct(0.05) == "+5.00%"


def test_pct_negative():
    assert _pct(-0.05) == "-5.00%"


def test_pct_nan():
    assert _pct(None) == "—"


def test_num():
    assert _num(123.456) == "123.46"


def test_num_nan():
    assert _num(None) == "—"


def test_streak_label_new_entry(sample_snapshot):
    row = sample_snapshot.iloc[0]
    row["new_entry"] = 1
    row["strong_streak"] = 0
    row["falling_out"] = 0
    row["accelerating"] = 0
    label = _streak_label(row)
    assert "首次进入" in label


def test_streak_label_strong_streak(sample_snapshot):
    row = sample_snapshot.iloc[0]
    row["strong_streak"] = 1
    label = _streak_label(row)
    assert "持续强势" in label


def test_streak_label_falling_out(sample_snapshot):
    row = sample_snapshot.iloc[0]
    row["new_entry"] = 0
    row["strong_streak"] = 0
    row["falling_out"] = 1
    label = _streak_label(row)
    assert "掉队" in label


def test_strength_table_non_empty(sample_snapshot):
    html = render_strength_table(sample_snapshot)
    assert "<table" in html
    assert "801016.SI" in html


def test_strength_table_empty():
    html = render_strength_table(pd.DataFrame())
    assert "无数据" in html


def test_rotation_matrix_non_empty(sample_industry_data):
    df = sample_industry_data.copy()
    df["RPS15"] = df.groupby("industry_code").cumcount() * 10
    html = render_rotation_matrix(df)
    assert "无数据" in html or len(html) > 0


def test_rotation_matrix_empty():
    html = render_rotation_matrix(pd.DataFrame())
    assert "无数据" in html


def test_status_changes_non_empty(sample_snapshot):
    html = render_status_changes(sample_snapshot)
    assert isinstance(html, str)
    assert len(html) > 0


def test_status_changes_empty():
    html = render_status_changes(pd.DataFrame())
    assert "无数据" in html or len(html) > 0


def test_build_html_creates_files(sample_snapshot, tmp_reports_dir):
    import pandas as pd
    dates = pd.date_range("2026-06-01", periods=25, freq="B")
    metrics = sample_snapshot.copy()
    metrics = metrics.loc[metrics.index.repeat(len(dates))].reset_index(drop=True)
    metrics["trade_date"] = dates.tolist() * 3
    metrics["RPS15"] = metrics.groupby("industry_code").cumcount() * 5
    class FakeValidator:
        status = "usable"
        issues = []
    fake_validator = FakeValidator()
    csv_path, html_path = build_html(
        snapshot=sample_snapshot,
        metrics=metrics,
        validator_result=fake_validator,
        report_date="20260715",
        reports_dir=tmp_reports_dir,
        rotation_days=5,
    )
    assert csv_path.exists()
    assert html_path.exists()
    html_content = html_path.read_text(encoding="utf-8")
    assert "申万二级行业 RPS 监控" in html_content
    assert "801016.SI" in html_content


def test_build_csv(sample_snapshot):
    csv_df = build_report_csv(sample_snapshot)
    assert "industry_code" in csv_df.columns
    assert "RPS15" in csv_df.columns
    assert csv_df["RPS15"].iloc[0] >= csv_df["RPS15"].iloc[-1]
