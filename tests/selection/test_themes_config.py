"""v0.4.3 两方向框架 — themes 配置加载与匹配"""
from __future__ import annotations

import pandas as pd
import pytest

from src.common import themes as themes_cfg


class TestThemesConfig:
    def test_buckets_loaded_in_order(self):
        buckets = themes_cfg.load_buckets()
        keys = [b.key for b in buckets]
        assert keys == ["core", "quality"]

    def test_bucket_objectives(self):
        buckets = {b.key: b for b in themes_cfg.load_buckets()}
        assert "长期收益" in buckets["core"].objective
        assert "现金流" in buckets["quality"].objective or "高现金流" in buckets["quality"].objective

    def test_theme_industries(self):
        themes = themes_cfg.load_themes()
        assert "半导体" in [i.name for i in themes["ai_infrastructure"].industries]
        assert "801081.SI" in themes["ai_infrastructure"].industry_codes()
        # 高现金流 = 电力 / 运营商 / 公用事业
        assert "801161.SI" in themes["high_cashflow"].industry_codes()  # 电力
        assert "801223.SI" in themes["high_cashflow"].industry_codes()  # 通信服务
        assert "801179.SI" in themes["high_cashflow"].industry_codes()  # 铁路公路
        assert "801992.SI" in themes["high_cashflow"].industry_codes()  # 航运港口

    def test_ai_application_excluded_from_infrastructure(self):
        # 「AI 基础设施，注意不是 AI 应用」：软件开发 / IT 服务 不在基础设施主题
        themes = themes_cfg.load_themes()
        codes = set(themes["ai_infrastructure"].industry_codes())
        assert "801104.SI" not in codes  # 软件开发
        assert "801103.SI" not in codes  # IT 服务

    def test_high_cashflow_includes_quality_industries(self):
        # 高现金流涵盖电力 / 运营商 / 公用事业（高速·公路·港口）
        codes = set(themes_cfg.load_themes()["high_cashflow"].industry_codes())
        assert {"801161.SI", "801223.SI", "801179.SI", "801992.SI"} <= codes


class TestMatchTheme:
    def test_keyword_matching_order(self):
        assert themes_cfg.match_theme("半导体ETF国联安") == "ai_infrastructure"
        assert themes_cfg.match_theme("通信ETF国泰") == "ai_infrastructure"
        assert themes_cfg.match_theme("电力ETF华泰柏瑞") == "high_cashflow"
        assert themes_cfg.match_theme("电信ETF易方达") == "high_cashflow"
        assert themes_cfg.match_theme("公用事业ETF华夏") == "high_cashflow"
        assert themes_cfg.match_theme("交通运输ETF华夏") == "high_cashflow"
        # 两方向之外（军工/煤炭/券商/化工）不再命中任何主题
        assert themes_cfg.match_theme("军工ETF国泰") is None
        assert themes_cfg.match_theme("煤炭ETF国泰") is None
        assert themes_cfg.match_theme("证券ETF国泰") is None

    def test_empty_name(self):
        assert themes_cfg.match_theme("") is None
        assert themes_cfg.match_theme(None) is None
