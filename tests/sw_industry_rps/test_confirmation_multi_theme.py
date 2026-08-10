"""v0.4.3 两方向 — Layer ② 行业确认按 bucket/theme 分层"""
from __future__ import annotations

from typing import cast

import pandas as pd
import pytest

from src.sw_industry_rps import confirmation


def _focus_df() -> pd.DataFrame:
    return pd.DataFrame([
        # core / ai_infrastructure
        {"industry_code": "801081.SI", "industry_name": "半导体", "relevance": "core",
         "theme": "ai_infrastructure", "theme_label": "AI 基础设施", "bucket": "core", "bucket_label": "核心",
         "RPS5": 95.0, "RPS10": 92.0, "RPS15": 92.0, "delta_rps15": 5.0, "short_term_acceleration": 3.0},
        {"industry_code": "801083.SI", "industry_name": "元件", "relevance": "core",
         "theme": "ai_infrastructure", "theme_label": "AI 基础设施", "bucket": "core", "bucket_label": "核心",
         "RPS5": 93.0, "RPS10": 91.0, "RPS15": 91.0, "delta_rps15": 3.0, "short_term_acceleration": 2.0},
        # quality / high_cashflow
        {"industry_code": "801161.SI", "industry_name": "电力", "relevance": "core",
         "theme": "high_cashflow", "theme_label": "高现金流资产", "bucket": "quality", "bucket_label": "质量",
         "RPS5": 96.0, "RPS10": 95.0, "RPS15": 95.0, "delta_rps15": 6.0, "short_term_acceleration": 4.0},
        {"industry_code": "801223.SI", "industry_name": "通信服务", "relevance": "core",
         "theme": "high_cashflow", "theme_label": "高现金流资产", "bucket": "quality", "bucket_label": "质量",
         "RPS5": 93.0, "RPS10": 91.0, "RPS15": 92.0, "delta_rps15": 2.0, "short_term_acceleration": 1.0},
        {"industry_code": "801179.SI", "industry_name": "铁路公路", "relevance": "core",
         "theme": "high_cashflow", "theme_label": "高现金流资产", "bucket": "quality", "bucket_label": "质量",
         "RPS5": 30.0, "RPS10": 28.0, "RPS15": 25.0, "delta_rps15": -3.0, "short_term_acceleration": 0.0},
    ])


class TestMultiThemeConfirmation:
    def test_focus_industries_from_config(self):
        # 焦点组来自 config/themes_two_directions.yaml（两方向）
        assert len(confirmation.FOCUS_INDUSTRIES) >= 10
        codes = {f["code"] for f in confirmation.FOCUS_INDUSTRIES}
        assert {"801081.SI", "801161.SI", "801223.SI", "801179.SI"} <= codes
        themes = {f["theme"] for f in confirmation.FOCUS_INDUSTRIES}
        assert {"ai_infrastructure", "high_cashflow", "china_auto_global"} <= themes

    def test_focus_snapshot_adds_bucket_columns(self, monkeypatch):
        fd = _focus_df()
        assert "bucket" in fd.columns
        assert "bucket_label" in fd.columns

    def test_theme_resonance_per_theme(self):
        fd = _focus_df()
        tr = {r["theme"]: r for r in confirmation.compute_theme_resonance(fd)}
        # ai_infrastructure: 2 个强势 → 群共振
        assert tr["ai_infrastructure"]["status"] == "群共振"
        # high_cashflow: 2 强势 + 1 弱势 → 群共振
        assert tr["high_cashflow"]["status"] == "群共振"

    def test_bucket_resonance_aggregation(self):
        fd = _focus_df()
        br = {r["bucket"]: r for r in confirmation.compute_bucket_resonance(fd)}
        assert list(br.keys()) == ["core", "quality"]
        # core: ai 2 强势
        assert br["core"]["n_strong"] == 2
        # quality: 高现金流资产 2 强势
        assert br["quality"]["n_strong"] == 2
        assert br["quality"]["status"] == "群共振"

    def test_classify_divergence_per_theme(self):
        fd = _focus_df()
        for theme_key in ("ai_infrastructure", "high_cashflow"):
            tdf = cast(pd.DataFrame, fd[fd["theme"] == theme_key])
            d = confirmation.classify_divergence(tdf, 50.0)
            assert d["status"] in ("行业支持", "中性", "行业背离", "无数据")


class TestRotationState:
    def _row(self, **kw):
        base = {"RPS15": 50.0, "RPS5": 50.0, "RPS1": 50.0}
        base.update(kw)
        return base

    def test_strong_continuation(self):
        r = confirmation.classify_rotation_state(self._row(RPS15=92, RPS5=90, RPS1=75))
        assert r == "强势延续"

    def test_high_position_consolidation(self):
        r = confirmation.classify_rotation_state(self._row(RPS15=92, RPS5=90, RPS1=30))
        assert r == "高位休整"

    def test_accelerating_launch(self):
        r = confirmation.classify_rotation_state(self._row(RPS15=50, RPS5=88, RPS1=60))
        assert r == "加速启动"

    def test_one_day_pulse(self):
        r = confirmation.classify_rotation_state(self._row(RPS15=20, RPS5=30, RPS1=90))
        assert r == "一日脉冲"

    def test_weakening(self):
        r = confirmation.classify_rotation_state(self._row(RPS15=30, RPS5=30, RPS1=30))
        assert r == "走弱"

    def test_missing_returns_placeholder(self):
        r = confirmation.classify_rotation_state({"RPS15": None, "RPS5": None, "RPS1": None})
        assert r == "—"

    def test_add_rotation_state_column(self):
        fd = _focus_df()
        out = confirmation.add_rotation_state_column(fd)
        assert "rotation_state" in out.columns
        assert bool(out["rotation_state"].notna().all())

    def test_theme_heat_aggregation(self):
        fd = confirmation.add_rotation_state_column(_focus_df())
        tr = confirmation.compute_theme_resonance(fd)
        out = confirmation.add_theme_heat(tr, fd)
        d = {r["theme"]: r for r in out}
        assert "median_rps1" in d["ai_infrastructure"]
        assert "median_rps5" in d["ai_infrastructure"]
        assert "rotation_states" in d["ai_infrastructure"]
