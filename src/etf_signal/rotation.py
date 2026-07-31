"""
A股全市场 ETF 轮动 — Layer 1

职责：
  - 在全量 ETF 之上做全市场横截面分析，回答：
    「AI / 科技 / 半导体在全部 A 股 ETF 资产中处于什么位置？」
  - 不预设 AI 一定是主线，由数据决定
  - 产出：横截面 RPS15/20/60、5/10/20 日收益、5 日排名变动、
          板块聚合（中位 RPS、强势占比、内部离散度、Top 10%/20% 数量）、
          AI/科技/半导体 焦点组判断块

口径说明：
  - rps15 / rps20 / rps60 分别为 15 / 20 / 60 日收益的全市场百分位排名（0-100）
  - rank15 为全市场 ordinal 排名（1 = 最弱），rank_change_5d 正值表示过去 5 个交易日排名上升
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import classifier

logger = logging.getLogger("etf_signal.rotation")

# ── 资产大类 / 桶标签（对齐 classifier.BUCKET_DEFINITIONS 实际产出） ─────────
ASSET_CLASS_LABEL: dict[str, str] = {
    "equity": "权益",
    "bond": "债券",
    "commodity": "商品",
    "cash": "现金",
    "overseas": "海外",
    "multi_asset": "跨资产",
}

BUCKET_LABELS: dict[str, str] = {
    k: v.get("label", k)
    for k, v in classifier.BUCKET_DEFINITIONS.items()
}
BUCKET_LABELS[""] = "未分类"

# ── AI/科技/半导体 焦点组关键词 ─────────────────────────────────────────────
TECH_KEYWORDS = [
    "人工智能", "ai", "芯片", "半导体", "集成电路", "电子", "消费电子",
    "通信", "计算机", "软件", "大数据", "云计算", "算力", "存储", "光模块",
    "科创", "机器人", "信息技术", "软件服务", "数字经济",
]

# RPS 排名窗口
RPS_WINDOWS = (15, 20, 60)
# 排名变化回溯交易日数
RANK_CHANGE_DAYS = 5
# 横截面样本尾部保留行数
CROSS_SECTIONAL_TAIL = 21

# 不参与横截面 RPS 排名的资产桶（防御性资产，收益结构不可比）
RANK_EXCLUDED_BUCKETS = {"money_market", "bond_treasury", "bond_credit", "bond_convertible"}


def _pivot_closes(
    combined: pd.DataFrame,
    date_col: str = "date",
    code_col: str = "fund_code",
    price_col: str = "close",
) -> pd.DataFrame:
    """宽表：行 = 交易日，列 = fund_code，值 = close。"""
    if combined.empty:
        return pd.DataFrame()
    pivot = combined.pivot_table(
        index=date_col, columns=code_col, values=price_col, aggfunc="last",
    ).sort_index()
    return pivot


def compute_cross_sectional(
    combined: pd.DataFrame,
    windows: tuple[int, ...] = RPS_WINDOWS,
    tail: int = CROSS_SECTIONAL_TAIL,
    rank_codes: set[str] | None = None,
) -> dict[int, dict[str, pd.DataFrame]]:
    """计算全市场逐日横截面 RPS 与 ordinal rank。

    仅在 rank_codes 指定的一组 ETF 内做横截面排名（默认：除货币/债券外的全部风险资产），
    避免货币类零收益标的结构性占优。

    Args:
        combined: 全市场日行情（date, fund_code, close）
        rank_codes: 参与排名的 fund_code 集合；为 None 时使用全部
        windows / tail: 排名窗口 / 尾部保留行数

    Returns:
        {
            window: {
                'returns': DataFrame(date × code)  窗口收益率（最近 tail 行）
                'rps':     DataFrame(date × code)  百分位排名 0-100
                'rank':    DataFrame(date × code)  ordinal 排名（1 = 最弱）
            }
        }
    """
    closes = _pivot_closes(combined)
    if closes.empty:
        return {}

    out: dict[int, dict[str, pd.DataFrame]] = {}
    for w in windows:
        # 仅保留尾部 tail+w 行，控制内存
        sub = closes.iloc[-(tail + w):]
        ret = sub / sub.shift(w) - 1
        recent = ret.iloc[-tail:]
        if rank_codes is not None:
            keep = [c for c in recent.columns if c in rank_codes]
            recent = recent[keep]
        rps = recent.rank(axis=1, pct=True, ascending=True) * 100
        rnk = recent.rank(axis=1, method="min", ascending=True)
        out[w] = {"returns": recent, "rps": rps, "rank": rnk}
    return out


def _latest_returns(closes: pd.DataFrame, periods: tuple[int, ...]) -> dict[str, pd.Series]:
    """最新交易日各窗口收益率（百分比）。"""
    if len(closes) < 2:
        return {}
    last = closes.iloc[-1]
    out: dict[str, pd.Series] = {}
    for p in periods:
        if len(closes) > p:
            out[f"return_{p}d"] = (last / closes.iloc[-1 - p] - 1) * 100
    return out


def is_tech_etf(fund_name: str) -> bool:
    """按名称关键词判定是否属于 AI/科技/半导体 焦点组。"""
    if not fund_name:
        return False
    name = str(fund_name).lower()
    return any(kw in name for kw in TECH_KEYWORDS)


def compute_rotation_metrics(
    combined: pd.DataFrame,
    master: pd.DataFrame,
    windows: tuple[int, ...] = RPS_WINDOWS,
) -> pd.DataFrame:
    """全市场轮动指标（每只 ETF 一行）。

    Args:
        combined: 全市场日行情（date, fund_code, close）
        master:   ETF Master（fund_code, fund_name, asset_bucket, ...）

    Returns:
        DataFrame 列：
            fund_code, fund_name, asset_bucket, bucket_label, asset_class,
            is_tech,
            rps15, rps20, rps60,
            rank15, rank15_prev5, rank_change_5d,
            return_5d, return_10d, return_15d, return_20d, return_60d
    """
    closes = _pivot_closes(combined)
    if closes.empty:
        logger.warning("no close data for rotation")
        return pd.DataFrame()

    # 参与排名的风险资产集合（排除货币/债券）
    rank_codes: set[str] | None = None
    if not master.empty and "asset_bucket" in master.columns:
        excluded = master[master["asset_bucket"].isin(RANK_EXCLUDED_BUCKETS)]["fund_code"]
        rank_codes = set(closes.columns) - set(excluded)
        logger.info("rotation ranking universe: %d / %d ETFs (excluded %d defensive)",
                    len(rank_codes), len(closes.columns), len(excluded))

    xs = compute_cross_sectional(combined, windows=windows, rank_codes=rank_codes)
    if not xs:
        return pd.DataFrame()

    returns = _latest_returns(closes, periods=(5, 10, 15, 20, 60))

    # ordinal rank：今日 与 5 个交易日前
    rank_series = xs[windows[0]]["rank"]
    rank_today = rank_series.iloc[-1]
    rank_prev5 = rank_series.shift(RANK_CHANGE_DAYS).iloc[-1] if len(rank_series) > RANK_CHANGE_DAYS else pd.Series(index=rank_series.columns, dtype=float)

    codes = list(closes.columns)
    rows: list[dict[str, Any]] = []
    for code in codes:
        row: dict[str, Any] = {"fund_code": code}
        for w in windows:
            rps = xs[w]["rps"].iloc[-1].get(code)
            row[f"rps{w}"] = None if pd.isna(rps) else float(rps)
        r15 = rank_today.get(code)
        r15_prev = rank_prev5.get(code)
        row["rank15"] = None if pd.isna(r15) else float(r15)
        row["rank15_prev5"] = None if pd.isna(r15_prev) else float(r15_prev)
        if pd.notna(r15) and pd.notna(r15_prev):
            row["rank_change_5d"] = float(r15_prev - r15)
        else:
            row["rank_change_5d"] = None
        for label, s in returns.items():
            v = s.get(code)
            row[label] = None if pd.isna(v) else float(v)
        rows.append(row)

    etf = pd.DataFrame(rows)

    # 名称 / 桶信息
    if not master.empty and "fund_code" in master.columns:
        m_cols = [c for c in ["fund_code", "fund_name", "asset_bucket"] if c in master.columns]
        etf = etf.merge(master[m_cols].drop_duplicates(subset=["fund_code"]), on="fund_code", how="left")
    else:
        etf["fund_name"] = ""
        etf["asset_bucket"] = ""

    etf["asset_bucket"] = etf["asset_bucket"].fillna("").astype(str)
    etf["fund_name"] = etf["fund_name"].fillna("")
    etf["bucket_label"] = etf["asset_bucket"].map(BUCKET_LABELS).fillna(etf["asset_bucket"])
    asset_class_map = {k: ASSET_CLASS_LABEL.get(v.get("asset_class", ""), "其他")
                       for k, v in classifier.BUCKET_DEFINITIONS.items()}
    etf["asset_class"] = etf["asset_bucket"].map(asset_class_map).fillna("未分类")
    etf["is_tech"] = etf["fund_name"].apply(is_tech_etf)

    numeric_cols = ["rps15", "rps20", "rps60", "rank15", "rank15_prev5", "rank_change_5d",
                    "return_5d", "return_10d", "return_15d", "return_20d", "return_60d"]
    for c in numeric_cols:
        if c in etf.columns:
            etf[c] = pd.to_numeric(etf[c], errors="coerce")

    logger.info(
        "rotation metrics: %d ETFs (rps15 median=%.1f, tech=%d)",
        len(etf), etf["rps15"].median() if etf["rps15"].notna().any() else float("nan"),
        int(etf["is_tech"].sum()),
    )
    return etf


def market_summary(etf: pd.DataFrame) -> dict[str, Any]:
    """全市场概览。"""
    valid = etf["rps15"].dropna()
    if valid.empty:
        return {"total": 0}
    total = len(valid)
    ret15 = etf["return_15d"].dropna()
    return {
        "total": total,
        "median_rps15": round(float(valid.median()), 1),
        "top10_count": int((valid >= 90).sum()),
        "top20_count": int((valid >= 80).sum()),
        "p25_rps15": round(float(valid.quantile(0.25)), 1),
        "p75_rps15": round(float(valid.quantile(0.75)), 1),
        "p90_rps15": round(float(valid.quantile(0.90)), 1),
        "mean_return_15d": round(float(ret15.mean()), 2) if not ret15.empty else None,
        "up_ratio_15d": round(float((ret15 > 0).mean()), 4) if not ret15.empty else None,
        "tech_count": int(etf["is_tech"].sum()),
    }


def build_bucket_table(etf: pd.DataFrame) -> pd.DataFrame:
    """板块（资产桶）聚合：Layer ① 六个观察维度的板块版。

    Returns:
        DataFrame 列：
            asset_class, asset_bucket, bucket_label, etf_count,
            median_rps15, rps15_rank_change_5d, strong_ratio, rps15_std,
            top10_count, top20_count, top10_ratio, top20_ratio
    """
    if etf.empty or "rps15" not in etf.columns:
        return pd.DataFrame()

    valid = etf["rps15"].dropna()
    if valid.empty:
        return pd.DataFrame()
    top10_cutoff = valid.quantile(0.90)
    top20_cutoff = valid.quantile(0.80)

    rows: list[dict[str, Any]] = []
    for bucket, g in etf.groupby("asset_bucket"):
        rps = g["rps15"].dropna()
        if rps.empty:
            # 防御资产（货币/债券）不参与排名，仍展示数量
            rows.append({
                "asset_class": g["asset_class"].iloc[0],
                "asset_bucket": bucket,
                "bucket_label": g["bucket_label"].iloc[0],
                "etf_count": int(g["fund_code"].nunique()),
                "median_rps15": None,
                "rps15_rank_change_5d": _median_round(g["rank_change_5d"]),
                "strong_ratio": None,
                "rps15_std": None,
                "top10_count": 0,
                "top20_count": 0,
                "top10_ratio": None,
                "top20_ratio": None,
            })
            continue
        rows.append({
            "asset_class": g["asset_class"].iloc[0],
            "asset_bucket": bucket,
            "bucket_label": g["bucket_label"].iloc[0],
            "etf_count": int(g["fund_code"].nunique()),
            "median_rps15": round(float(rps.median()), 1),
            "rps15_rank_change_5d": _median_round(g["rank_change_5d"]),
            "strong_ratio": round(float((rps >= 80).mean()), 4),
            "rps15_std": round(float(rps.std()), 1),
            "top10_count": int((rps >= top10_cutoff).sum()),
            "top20_count": int((rps >= top20_cutoff).sum()),
            "top10_ratio": round(float((rps >= top10_cutoff).mean()), 4),
            "top20_ratio": round(float((rps >= top20_cutoff).mean()), 4),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["median_rps15"], ascending=False, na_position="last").reset_index(drop=True)
    return df


def _median_round(series: pd.Series) -> float:
    s = series.dropna()
    if s.empty:
        return 0.0
    return round(float(s.median()), 1)


def focus_group(etf: pd.DataFrame, market: dict[str, Any]) -> dict[str, Any]:
    """AI/科技/半导体 焦点组判断块。

    与 ARCHITECTURE.md ① 的示例输出对齐：
        全市场 ETF 数量 / AI/科技/半导体 ETF 数量 /
        进入 RPS15 Top 10% / Top 20% / 板块 RPS15 中位数 / 5 日排名提升
    """
    tech = etf[etf["is_tech"]].copy()
    if tech.empty:
        return {"tech_count": 0}

    tech_rps = tech["rps15"].dropna()
    n = len(tech)
    top10 = int((tech_rps >= 90).sum())
    top20 = int((tech_rps >= 80).sum())
    median_rps = round(float(tech_rps.median()), 1) if not tech_rps.empty else None
    rank_change = _median_round(tech["rank_change_5d"])

    expected_top10 = n * 0.10
    verdict = _judge_tech_momentum(n, top10, top20, median_rps, rank_change)

    return {
        "tech_count": n,
        "top10": top10,
        "top20": top20,
        "median_rps15": median_rps,
        "rank_change_5d": rank_change,
        "top10_excess": round(top10 - expected_top10, 1),
        "verdict": verdict,
    }


def _judge_tech_momentum(
    n: int, top10: int, top20: int, median_rps: float | None, rank_change: float,
) -> str:
    """判断 AI/科技/半导体 是否正在成为 A 股主线。"""
    if n == 0 or median_rps is None:
        return "无有效样本"
    top10_ratio = top10 / n
    if median_rps >= 70 and top10_ratio >= 0.25 and rank_change > 0:
        return "正在成为 A 股主线：RPS 中位强势、Top10% 集中度显著、5 日排名整体抬升"
    if median_rps >= 60 and top10_ratio >= 0.15:
        return "初步走强：相对强度高于市场，但集中度或动量尚不足以确认主线"
    if median_rps < 40:
        return "明显弱于市场：AI/科技/半导体当前不是主线方向"
    return "处于市场中位，尚未形成明确主线"


def assess_market_regime(bucket_table: pd.DataFrame) -> dict[str, Any]:
    """从板块聚合判断市场风险偏好（进攻 / 防御 / 均衡）。"""
    if bucket_table.empty:
        return {"preference": "unknown", "note": "无数据"}
    equity = bucket_table[bucket_table["asset_class"] == "权益"]
    defensive = bucket_table[bucket_table["asset_class"].isin(["债券", "现金"])]

    equity_median = equity["median_rps15"].mean() if not equity.empty else 0
    equity_strong = equity["strong_ratio"].max() if not equity.empty else 0
    defensive_median = defensive["median_rps15"].mean() if not defensive.empty else 0

    top = bucket_table.iloc[0]
    if equity_median >= 70 and equity_strong >= 0.30:
        preference = "进攻"
        note = f"权益类热度集中（{top['bucket_label']}，中位 RPS {top['median_rps15']}）"
    elif defensive_median > equity_median and defensive_median >= 75:
        preference = "防御"
        note = f"防御类资产占优（{top['bucket_label']}，中位 RPS {top['median_rps15']}）"
    else:
        preference = "均衡"
        note = "无明显偏向"

    return {
        "preference": preference,
        "equity_median_rps15": round(equity_median, 1),
        "defensive_median_rps15": round(defensive_median, 1),
        "top_bucket": top["bucket_label"],
        "note": note,
    }
