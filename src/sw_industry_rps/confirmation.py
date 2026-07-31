"""
Layer ② 申万二级行业确认 — AI/科技/半导体 行业群趋势质量验证

核心问题：ETF 的强势是否得到了底层行业的支持？
职责：当 ETF 信号指向科技方向时，从中观行业层面验证趋势质量。

输出维度：
  - 群共振：重点行业群中有多少进入强势区（单一 vs 群共振）
  - RPS 强弱与加速：各重点行业 RPS5/10/15、ΔRPS15、加速状态
  - 龙头 vs 广泛上涨：强势行业的驱动分类（贡献集中度 × 广度）
  - ETF—行业背离：行业群相对全市场的强度（ETF 侧接入为后续）
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("sw_industry_rps.confirmation")

# ── AI/科技/半导体 重点行业（硬编码，与 ARCHITECTURE.md ② 对齐） ──────────────
# theme 为子分组键：ai_core / digital_infrastructure / intelligent_manufacturing
FOCUS_INDUSTRIES: list[dict[str, str]] = [
    {"code": "801081.SI", "name": "半导体", "relevance": "core", "theme": "ai_core"},
    {"code": "801083.SI", "name": "元件", "relevance": "core", "theme": "ai_core"},
    {"code": "801085.SI", "name": "消费电子", "relevance": "core", "theme": "ai_core"},
    {"code": "801104.SI", "name": "软件开发", "relevance": "related", "theme": "ai_core"},
    {"code": "801101.SI", "name": "计算机设备", "relevance": "related", "theme": "ai_core"},
    {"code": "801102.SI", "name": "通信设备", "relevance": "related", "theme": "digital_infrastructure"},
    {"code": "801223.SI", "name": "通信服务", "relevance": "related", "theme": "digital_infrastructure"},
    {"code": "801103.SI", "name": "IT服务", "relevance": "related", "theme": "digital_infrastructure"},
    {"code": "801078.SI", "name": "自动化设备", "relevance": "related", "theme": "intelligent_manufacturing"},
    {"code": "801084.SI", "name": "光学光电子", "relevance": "related", "theme": "intelligent_manufacturing"},
]

# 子分组定义（按 code 归属，名称用于展示）
# 设计动机：AI 核心产业链与 TMT 基础设施的产业周期常不同步，
# 未来可据此区分「AI Core 共振」vs「科技整体共振」
THEMES: dict[str, dict[str, Any]] = {
    "ai_core": {
        "label": "AI 核心产业链",
        "codes": ["801081.SI", "801083.SI", "801085.SI", "801104.SI", "801101.SI"],
    },
    "digital_infrastructure": {
        "label": "数字基础设施（TMT）",
        "codes": ["801102.SI", "801223.SI", "801103.SI"],
    },
    "intelligent_manufacturing": {
        "label": "智能制造",
        "codes": ["801078.SI", "801084.SI"],
    },
}

# 强势区 / 观察区阈值（与 regimes 配置一致）
STRONG_THRESHOLD = 90.0
OBSERVE_THRESHOLD = 80.0
NEUTRAL_THRESHOLD = 60.0

RELEVANCE_LABEL = {"core": "核心", "related": "相关"}

# 驱动分类中文标签（复用 contribution.py 的语义）
CONTRIBUTION_LABELS = {
    "single_core": "单核主导",
    "leader_concentrated": "集中领涨",
    "multi_leader": "多龙头带动",
    "distributed": "分散上涨",
}
BREADTH_LABELS = {
    "broad": "广泛上涨",
    "moderate": "中度扩散",
    "narrow": "少数带动",
    "divergent": "明显分化",
}


def industry_strength_level(rps15: float | None) -> str:
    """强势层级：强势 / 观察 / 中性 / 弱势。"""
    if rps15 is None or pd.isna(rps15):
        return "无数据"
    if rps15 >= STRONG_THRESHOLD:
        return "强势"
    if rps15 >= OBSERVE_THRESHOLD:
        return "观察"
    if rps15 >= NEUTRAL_THRESHOLD:
        return "中性"
    return "弱势"


def compute_focus_snapshot(
    metrics_df: pd.DataFrame,
    date: str | None = None,
) -> pd.DataFrame:
    """各重点行业在目标日期的状态明细（不含 drilldown 字段）。"""
    if metrics_df.empty:
        return pd.DataFrame()
    latest_date = metrics_df["trade_date"].max() if date is None else pd.Timestamp(date)
    snap = metrics_df[metrics_df["trade_date"] == latest_date]

    rows: list[dict[str, Any]] = []
    for fi in FOCUS_INDUSTRIES:
        sub = snap[snap["industry_code"] == fi["code"]]
        if sub.empty:
            continue
        r = sub.iloc[0]

        def _f(col: str) -> float:
            v = r.get(col)
            return float(v) if v is not None and pd.notna(v) else np.nan

        rps15 = _f("RPS15")
        theme = fi.get("theme", "")
        theme_label = THEMES.get(theme, {}).get("label", theme)
        rows.append({
            "date": latest_date,
            "industry_code": fi["code"],
            "industry_name": fi["name"],
            "relevance": fi["relevance"],
            "relevance_label": RELEVANCE_LABEL.get(fi["relevance"], fi["relevance"]),
            "theme": theme,
            "theme_label": theme_label,
            "RPS5": _f("RPS5"),
            "RPS10": _f("RPS10"),
            "RPS15": rps15,
            "delta_rps15": _f("delta_rps15"),
            "short_term_acceleration": _f("short_term_acceleration"),
            "streak_90": _f("streak_90"),
            "return_15": _f("return_15"),
            "new_entry": _f("new_entry"),
            "strong_streak": _f("strong_streak"),
            "accelerating": _f("accelerating"),
            "falling_out": _f("falling_out"),
            "strength_level": industry_strength_level(rps15),
            # drilldown 相关字段（由 confirm 命令合并填充）
            "contribution_structure": "",
            "breadth_structure": "",
            "drive_pattern": "",
            "participation_rate": np.nan,
            "hhi": np.nan,
            "top1_share": np.nan,
            "top3_share": np.nan,
            "reconstruction_quality": "",
            "industry_return_pct": np.nan,
            "proxy_return_pct": np.nan,
            "reconstruction_gap_pct": np.nan,
            "weight_coverage": np.nan,
            "count_coverage": np.nan,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("RPS15", ascending=False).reset_index(drop=True)
    return df


def classify_group_resonance(focus_df: pd.DataFrame) -> dict[str, Any]:
    """群共振判定：单一 vs 群共振。

    基于重点行业群中强势区（RPS15>=90）与观察区（RPS15>=80）的行业数量。

    Returns:
        {
            status: 群共振 / 局部走强 / 整体弱势 / 无数据
            n_strong, n_observe, n_core_strong, n_core_observe,
            group_median_rps15, group_median_delta_rps15,
            verdict: 中文判断
        }
    """
    if focus_df.empty or focus_df["RPS15"].isna().all():
        return {"status": "无数据", "verdict": "重点行业无有效数据"}

    rps = focus_df["RPS15"].dropna()
    n_strong = int((rps >= STRONG_THRESHOLD).sum())
    n_observe = int((rps >= OBSERVE_THRESHOLD).sum())
    core = focus_df[focus_df["relevance"] == "core"]
    n_core_strong = int((core["RPS15"] >= STRONG_THRESHOLD).sum())
    n_core_observe = int((core["RPS15"] >= OBSERVE_THRESHOLD).sum())
    group_median = round(float(rps.median()), 1)
    group_median_delta = round(float(focus_df["delta_rps15"].median()), 1) \
        if focus_df["delta_rps15"].notna().any() else None

    # 群共振：≥3 个行业进入强势区且核心行业有强势
    if n_strong >= 3 and n_core_strong >= 1:
        status = "群共振"
        verdict = (f"行业群同步走强：{n_strong} 个进入强势区（含 {n_core_strong} 个核心），"
                   f"群中位 RPS15 {group_median}，属于群体性行情")
    # 局部走强：至少 1 个行业进入观察区（可能为单一行业行情）
    elif n_observe >= 1:
        status = "局部走强"
        verdict = (f"行业群未全面共振：{n_strong} 个强势 / {n_observe} 个进入观察区"
                   f"（核心 {n_core_observe} 个），群中位 RPS15 {group_median}，"
                   f"需区分是单一行业行情还是趋势起点")
    else:
        status = "整体弱势"
        verdict = (f"重点行业群整体弱于市场：无行业进入观察区，群中位 RPS15 {group_median}，"
                   f"行业层面不支撑科技主线")

    return {
        "status": status,
        "n_strong": n_strong,
        "n_observe": n_observe,
        "n_core_strong": n_core_strong,
        "n_core_observe": n_core_observe,
        "group_median_rps15": group_median,
        "group_median_delta_rps15": group_median_delta,
        "verdict": verdict,
    }


def compute_theme_resonance(focus_df: pd.DataFrame) -> list[dict[str, Any]]:
    """子分组（Theme）共振分析。

    将重点行业拆为 3 个子分组，分别计算共振状态，用于区分
    「AI Core 共振」vs「科技整体共振」。

    Returns:
        [
            {
                theme, theme_label, n, n_strong, n_observe, n_core_observe,
                median_rps15, median_delta_rps15, status, summary
            }, ...
        ]，按 status 强弱排序
    """
    if focus_df.empty or "theme" not in focus_df.columns:
        return []

    rows: list[dict[str, Any]] = []
    for theme_key, tdef in THEMES.items():
        sub = focus_df[focus_df["theme"] == theme_key]
        if sub.empty:
            continue
        rps = sub["RPS15"].dropna()
        n = len(sub)
        n_strong = int((rps >= STRONG_THRESHOLD).sum())
        n_observe = int((rps >= OBSERVE_THRESHOLD).sum())
        n_core_observe = int((sub["RPS15"] >= OBSERVE_THRESHOLD).sum())
        median_rps = round(float(rps.median()), 1) if not rps.empty else None
        median_delta = round(float(sub["delta_rps15"].median()), 1) \
            if sub["delta_rps15"].notna().any() else None

        if n_strong >= 2 and n_core_observe >= 1:
            status = "群共振"
            summary = f"子主题群内共振（{n_strong} 个强势）"
        elif n_observe >= 1:
            status = "局部走强"
            summary = f"{n_observe} 个进入观察区"
        else:
            status = "整体弱势"
            summary = "无行业进入观察区"

        rows.append({
            "theme": theme_key,
            "theme_label": tdef["label"],
            "n": n,
            "n_strong": n_strong,
            "n_observe": n_observe,
            "n_core_observe": n_core_observe,
            "median_rps15": median_rps,
            "median_delta_rps15": median_delta,
            "status": status,
            "summary": summary,
        })

    rank = {"群共振": 0, "局部走强": 1, "整体弱势": 2}
    rows.sort(key=lambda r: rank.get(r["status"], 9))
    return rows


def compute_market_context(metrics_df: pd.DataFrame, date: str | None = None) -> dict[str, Any]:
    """全市场（全部 124 行业）对照，用于 ETF—行业背离的行业侧近似。"""
    if metrics_df.empty:
        return {"market_median_rps15": None}
    latest_date = metrics_df["trade_date"].max() if date is None else pd.Timestamp(date)
    snap = metrics_df[metrics_df["trade_date"] == latest_date]
    rps = snap["RPS15"].dropna()
    return {
        "market_median_rps15": round(float(rps.median()), 1) if not rps.empty else None,
        "market_n": int(len(rps)),
    }


def classify_divergence(
    focus_df: pd.DataFrame,
    market_median_rps15: float | None,
) -> dict[str, Any]:
    """ETF—行业背离（行业侧近似）。

    重点行业群中位 RPS15 与全市场中位 RPS15 的对比：
      - 行业群显著强于市场 → 行业层面支持（无背离）
      - 行业群与市场接近   → 中性
      - 行业群显著弱于市场 → 行业层面不支持（若 ETF 强则为背离）

    注：完整的「ETF vs 行业」背离需要 ETF 侧数据（后续接入），
        当前仅基于行业侧相对强度做近似判断。
    """
    if focus_df.empty or focus_df["RPS15"].isna().all() or market_median_rps15 is None:
        return {"status": "无数据", "note": "数据不足"}
    group_median = focus_df["RPS15"].median()
    gap = group_median - market_median_rps15

    if gap >= 15:
        status = "行业支持"
        note = (f"行业群中位 RPS15 {group_median:.1f} 高于全市场中位 {market_median_rps15:.1f}"
                f"（{gap:+.1f}），行业层面确认强度")
    elif gap <= -15:
        status = "行业背离"
        note = (f"行业群中位 RPS15 {group_median:.1f} 显著低于全市场中位 {market_median_rps15:.1f}"
                f"（{gap:+.1f}），行业层面不支撑；若 ETF 侧走强则存在背离")
    else:
        status = "中性"
        note = (f"行业群中位 RPS15 {group_median:.1f} 与全市场中位 {market_median_rps15:.1f} 接近"
                f"（{gap:+.1f}），无显著背离")

    return {
        "status": status,
        "group_median_rps15": round(float(group_median), 1),
        "market_median_rps15": round(float(market_median_rps15), 1),
        "gap": round(float(gap), 1),
        "note": note,
    }


def merge_drilldown(focus_df: pd.DataFrame, drilldown_results: dict[str, Any]) -> pd.DataFrame:
    """将 drilldown 结果合并进重点行业明细。

    Args:
        focus_df: compute_focus_snapshot 输出的明细
        drilldown_results: {industry_code: DrilldownResult}
    """
    if focus_df.empty:
        return focus_df
    df = focus_df.copy()
    for idx, row in df.iterrows():
        code = row["industry_code"]
        dd = drilldown_results.get(code)
        if dd is None:
            continue
        contrib = dd.contribution_structure
        breadth = dd.breadth_structure
        df.at[idx, "contribution_structure"] = contrib
        df.at[idx, "breadth_structure"] = breadth
        df.at[idx, "drive_pattern"] = _format_drive(contrib, breadth)
        df.at[idx, "participation_rate"] = dd.participation_rate
        df.at[idx, "hhi"] = dd.hhi
        df.at[idx, "top1_share"] = dd.top1_share
        df.at[idx, "top3_share"] = dd.top3_share
        df.at[idx, "reconstruction_quality"] = dd.reconstruction_quality
        df.at[idx, "industry_return_pct"] = dd.industry_return_pct
        df.at[idx, "proxy_return_pct"] = dd.proxy_return_pct
        df.at[idx, "reconstruction_gap_pct"] = dd.reconstruction_gap_pct
        df.at[idx, "weight_coverage"] = dd.weight_coverage
        df.at[idx, "count_coverage"] = dd.count_coverage
    return df


def _format_drive(contrib: str, breadth: str) -> str:
    c = CONTRIBUTION_LABELS.get(contrib, contrib or "")
    b = BREADTH_LABELS.get(breadth, breadth or "")
    if not c and not b:
        return ""
    if c and b:
        return f"{c} × {b}"
    return c or b
