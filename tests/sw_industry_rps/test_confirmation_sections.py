"""Layer ② 第三问渲染 section（confirmation_sections）"""
from __future__ import annotations

import pandas as pd

from src.sw_industry_rps import confirmation_sections as cs


def _sample_conf_df():
    return pd.DataFrame([
        {"industry_code": "801081.SI", "industry_name": "半导体", "relevance": "core",
         "relevance_label": "核心", "theme": "ai_infrastructure", "theme_label": "AI 基础设施",
         "bucket": "core", "bucket_label": "核心",
         "RPS1": 90.0, "RPS5": 95.0, "RPS10": 92.0, "RPS15": 92.0,
         "delta_rps15": 5.0, "delta_rps15_5d": 3.0, "strength_level": "强势",
         "rotation_state": "强势延续", "contribution_structure": "leader_concentrated",
         "breadth_structure": "broad", "drive_pattern": "集中领涨 × 广泛上涨",
         "participation_rate": 0.8, "hhi": 0.05, "top1_share": 0.2, "top3_share": 0.5},
        {"industry_code": "801161.SI", "industry_name": "电力", "relevance": "core",
         "relevance_label": "核心", "theme": "high_cashflow", "theme_label": "高现金流资产",
         "bucket": "quality", "bucket_label": "质量",
         "RPS1": 85.0, "RPS5": 90.0, "RPS10": 88.0, "RPS15": 85.0,
         "delta_rps15": 4.0, "delta_rps15_5d": 2.0, "strength_level": "观察",
         "rotation_state": "加速启动", "contribution_structure": "multi_leader",
         "breadth_structure": "moderate", "drive_pattern": "多龙头带动 × 中度扩散",
         "participation_rate": 0.7, "hhi": 0.08, "top1_share": 0.3, "top3_share": 0.6},
    ])


def _sample_metrics_df():
    return pd.DataFrame({
        "trade_date": [pd.Timestamp("2026-08-06")] * 4,
        "industry_code": ["801081.SI", "801161.SI", "801223.SI", "801179.SI"],
        "RPS15": [92.0, 85.0, 50.0, 30.0],
    })


def test_build_confirmation_evidence():
    ev = cs.build_confirmation_evidence(_sample_conf_df(), _sample_metrics_df())
    assert "resonance" in ev and "theme_resonance" in ev
    assert "bucket_resonance" in ev and "divergence_map" in ev
    assert ev["market_context"].get("market_median_rps15") is not None
    assert "ai_infrastructure" in ev["divergence_map"]
    assert "high_cashflow" in ev["divergence_map"]


def test_render_theme_summary():
    html = cs.render_theme_summary(_sample_conf_df())
    assert "AI 基础设施" in html
    assert "高现金流资产" in html
    assert "半导体" in html
    assert "电力" in html
    assert "BROAD_CONFIRMED" in html or "NARROW_CONFIRMED" in html or "WATCH" in html or "UNCONFIRMED" in html
    # 最小证据不展示 Δ5RPS15（在完整证据里）
    assert "Δ5" not in html


def test_render_confirmation_details():
    html = cs.render_confirmation_details(_sample_conf_df(), _sample_metrics_df())
    # 完整证据含四段标题
    assert "① 主题确认判断" in html
    assert "② 证据明细" in html
    assert "③ 内部结构" in html
    assert "④ 跨层对照" in html
    assert "全市场中位 RPS15" in html


def test_render_confirmation_details_with_structure():
    structure_df = pd.DataFrame([{
        "industry_code": "801081.SI", "industry_name": "半导体",
        "contribution_structure": "leader_concentrated", "breadth_structure": "broad",
        "driver_mode": "集中领涨 × 广泛上涨", "participation_rate": 0.8,
        "hhi": 0.05, "top1_share": 0.2, "top3_share": 0.5,
        "reconstruction_quality": "good", "structure_status": "available",
    }])
    html = cs.render_confirmation_details(_sample_conf_df(), _sample_metrics_df(), structure_df)
    assert "available" in html
    # 详情页显示综合语义标签（machine → human 展示层映射）
    assert "龙头集中普涨" in html


def test_render_theme_summary_empty():
    assert cs.render_theme_summary(pd.DataFrame()) == "<p>无主题数据</p>"


def test_render_theme_table_compact():
    html = cs.render_theme_table(_sample_conf_df())
    assert "AI 基础设施" in html
    assert "高现金流资产" in html
    # 每主题一行，含 状态/支撑/最接近确认/判断
    assert "状态" in html and "支撑" in html and "最接近确认" in html and "判断" in html
    # 半导体 RPS15=92 为支撑
    assert "半导体" in html


def test_render_theme_table_judgment_human():
    # 判断列应是人话，非重复计数（不含「进入观察区」这类计数）
    html = cs.render_theme_table(_sample_conf_df())
    assert "确认支撑" in html or "接近" in html or "未进入" in html


def test_render_theme_table_unconfirmed_support_dash():
    # 未确认主题：支撑列应显示 —（无确认行业支撑）
    html = cs.render_theme_table(_sample_conf_df())
    # AI 基础设施全部 <80，未确认 → 支撑 —
    assert ">—<" in html or ">—</" in html


def test_render_theme_table_with_structure():
    structure_df = pd.DataFrame([{
        "industry_code": "801081.SI", "industry_name": "半导体",
        "contribution_structure": "leader_concentrated", "breadth_structure": "broad",
        "structure_status": "available",
    }])
    html = cs.render_theme_table(_sample_conf_df(), structure_df)
    # 支撑栏附带驱动模式（综合语义）
    assert "龙头集中普涨" in html


def test_render_theme_table_empty():
    assert cs.render_theme_table(pd.DataFrame()) == "<p>无主题数据</p>"


def test_render_confirmation_details_empty():
    assert cs.render_confirmation_details(pd.DataFrame(), _sample_metrics_df()) == "<p>无确认数据</p>"
