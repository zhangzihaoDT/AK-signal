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

v0.7.0 Market Pulse（市场脉搏）：
  Layer① 从单维「谁最强」扩展为四维观察：趋势（Level）+ 今日（Today）+ 动量（Velocity）+ 流动性。
    - rps1        RPS1（Today）：最新 1 日收益横截面百分位，观察「今天是不是热点」
    - delta_rps15 ΔRPS15（Velocity）：RPS15 今日 − RPS15 5 个交易日前，观察「趋势有没有加速」
    - liquidity   最新 5 日均成交额横截面百分位，观察流动性强弱
  三者均为 Observation 展示指标：不参与排序（主表仍按 RPS15 排），不进入 Selection（Decision
  仍只看 RPS15 / TrendState / Amount）。

v0.7.0 数据质量（Data Quality）：
  - 回溯窗口（默认 60 日）内任一日 |收益| ≥ max_single_day_return（默认 20%）→ 判定异常
    （份额折算/除权/异常行情）。异常资产不参与对应 RPS 窗口的横截面排名（按窗口分别排除：
    折算发生在 w 日前的单日跳变只污染 w 日以内的累计收益），原值保留并标记 data_quality_flag。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from src.common import themes as themes_cfg
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

# RPS 排名窗口（来自统一 Strategy Specification config/indicator_spec.yaml）
RPS_WINDOWS: tuple[int, ...] = (15, 20, 60)
# 排名变化回溯交易日数
RANK_CHANGE_DAYS = 5
# 横截面样本尾部保留行数
CROSS_SECTIONAL_TAIL = 21
# 流动性观察口径：最近 N 个交易日成交额均值（Observation 展示）
LIQUIDITY_AVG_DAYS = 5


def _rps_windows() -> tuple[int, ...]:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().rps_windows


def _rps_today_window() -> int:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().rps_today_window


def _rps_velocity_window() -> int:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().rps_velocity_window


def _data_quality_threshold() -> float:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().data_quality_max_single_day_return


def _data_quality_window() -> int:
    from src.common.spec.loaders import load_indicator_spec
    return load_indicator_spec().data_quality_flag_window

# 不参与横截面 RPS 排名的资产桶（防御性资产，收益结构不可比）
RANK_EXCLUDED_BUCKETS = {"money_market", "bond_treasury", "bond_credit", "bond_convertible"}


def _pivot_values(
    combined: pd.DataFrame,
    value_col: str,
    date_col: str = "date",
    code_col: str = "fund_code",
) -> pd.DataFrame:
    """宽表：行 = 交易日，列 = fund_code，值 = value_col。"""
    if combined.empty or value_col not in combined.columns:
        return pd.DataFrame()
    pivot = combined.pivot_table(
        index=date_col, columns=code_col, values=value_col, aggfunc="last",
    ).sort_index()
    return pivot


def _pivot_closes(
    combined: pd.DataFrame,
    date_col: str = "date",
    code_col: str = "fund_code",
    price_col: str = "close",
) -> pd.DataFrame:
    """宽表：行 = 交易日，列 = fund_code，值 = close。"""
    return _pivot_values(combined, price_col, date_col=date_col, code_col=code_col)


def compute_cross_sectional(
    combined: pd.DataFrame,
    windows: tuple[int, ...] | None = None,
    tail: int = CROSS_SECTIONAL_TAIL,
    rank_codes: set[str] | None = None,
    rank_codes_by_window: dict[int, set[str]] | None = None,
) -> dict[int, dict[str, pd.DataFrame]]:
    """计算全市场逐日横截面 RPS 与 ordinal rank。

    仅在 rank_codes 指定的一组 ETF 内做横截面排名（默认：除货币/债券外的全部风险资产），
    避免货币类零收益标的结构性占优。

    Args:
        combined: 全市场日行情（date, fund_code, close）
        rank_codes: 参与排名的 fund_code 集合；为 None 时使用全部
        rank_codes_by_window: 按窗口的参与排名集合（v0.7.0 数据质量，窗口内单日异常
            资产不参与该窗口横截面排名）；命中该 dict 的窗口以其为准，否则用 rank_codes
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
    if windows is None:
        windows = _rps_windows()
    closes = _pivot_closes(combined)
    if closes.empty:
        return {}

    out: dict[int, dict[str, pd.DataFrame]] = {}
    for w in windows:
        # 仅保留尾部 tail+w 行，控制内存
        sub = closes.iloc[-(tail + w):]
        ret = sub / sub.shift(w) - 1
        recent = ret.iloc[-tail:]
        codes = rank_codes_by_window.get(w, rank_codes) if rank_codes_by_window else rank_codes
        if codes is not None:
            keep = [c for c in recent.columns if c in codes]
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


def _compute_today_rps(
    closes: pd.DataFrame,
    rank_codes: set[str] | None,
    today_window: int = 1,
    tail: int = CROSS_SECTIONAL_TAIL,
) -> pd.Series:
    """RPS1（Today）：最新交易日 today_window 日收益的横截面百分位（0-100）。

    仅 Observation：标注「今天是不是热点」。不参与排序，也不进入 Selection。
    """
    if closes.empty or len(closes) <= today_window:
        return pd.Series(dtype=float)
    sub = closes.iloc[-(tail + today_window):]
    ret = sub / sub.shift(today_window) - 1
    recent = ret.iloc[-tail:]
    if rank_codes is not None:
        recent = recent[[c for c in recent.columns if c in rank_codes]]
    if recent.empty:
        return pd.Series(dtype=float)
    rps = recent.rank(axis=1, pct=True, ascending=True) * 100
    return rps.iloc[-1]


def _compute_liquidity(
    combined: pd.DataFrame,
    avg_days: int = LIQUIDITY_AVG_DAYS,
) -> pd.Series:
    """Liquidity（Observation）：最近 avg_days 个交易日成交额均值的横截面百分位（0-100）。

    仅展示流动性强弱。不参与排序；Selection（Decision）的 amount_score 独立按
    strategy_spec.yaml 的 log_threshold 口径计算，本列不进 Decision 层。
    """
    amount = _pivot_values(combined, value_col="amount")
    if amount.empty:
        return pd.Series(dtype=float)
    avg = amount.iloc[-min(avg_days, len(amount)):].mean(axis=0)
    return avg.rank(pct=True, ascending=True) * 100


def _detect_anomaly_offsets(
    closes: pd.DataFrame,
    flag_window: int,
    threshold_pct: float,
) -> dict[str, list[int]]:
    """检测回溯窗口内的单日异常收益（份额折算/除权/异常行情）。

    Args:
        closes: 宽表（date × fund_code，close）
        flag_window: 回溯交易日数
        threshold_pct: 单日 |收益| ≥ 该百分比 → 异常

    Returns:
        {fund_code: [offset, ...]}，offset 为距最新交易日的偏移（1=今日，越小越近）。
        例：折算发生在 3 个交易日前 → offset=[3]。
    """
    if len(closes) < 2:
        return {}
    n = min(flag_window + 1, len(closes))
    sub = closes.iloc[-n:]
    ret = sub.pct_change() * 100
    out: dict[str, list[int]] = {}
    for code in ret.columns:
        s = ret[code]
        bad = s.index[s.abs() >= threshold_pct]
        if not len(bad):
            continue
        # 日收益在第 i 行（1..n-1）实现，距最新交易日 offset = n - i
        offsets = sorted({int(n - ret.index.get_loc(i)) for i in bad})
        if offsets:
            out[code] = offsets
    return out


def is_tech_etf(fund_name: str) -> bool:
    """按名称关键词判定是否属于 AI/科技/半导体 焦点组（向后兼容）。"""
    if not fund_name:
        return False
    name = str(fund_name).lower()
    return any(kw in name for kw in TECH_KEYWORDS)


def match_theme(fund_name: str) -> str | None:
    """按 config/theme_registry.yaml 关键词匹配首个主题（v0.4.3 多主题）。"""
    return themes_cfg.match_theme(fund_name)


def compute_rotation_metrics(
    combined: pd.DataFrame,
    master: pd.DataFrame,
    windows: tuple[int, ...] | None = None,
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
            rps1, delta_rps15, liquidity,   # v0.7.0 Market Pulse（仅 Observation 展示）
            rank15, rank15_prev5, rank_change_5d,
            return_1d, return_5d, return_10d, return_15d, return_20d, return_60d,
            data_quality_flag              # 空=正常；corporate_action=单日异常/折算（原值保留）
    """
    if windows is None:
        windows = _rps_windows()
    today_window = _rps_today_window()
    velocity_window = _rps_velocity_window()
    dq_threshold = _data_quality_threshold()
    dq_window = _data_quality_window()
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

    # v0.7.0 数据质量：回溯窗口内单日异常（份额折算/除权/异常行情）不参与对应窗口横截面排名
    anomaly_offsets = _detect_anomaly_offsets(closes, dq_window, dq_threshold)
    if anomaly_offsets:
        logger.warning("data-quality anomalies detected: %d ETFs (max_single_day_return>=%.0f%% in %dd)",
                       len(anomaly_offsets), dq_threshold, dq_window)
    rank_codes_by_window: dict[int, set[str]] = {}
    for w in windows:
        rank_codes_by_window[w] = {
            c for c in (rank_codes or set(closes.columns))
            if not any(o <= w for o in anomaly_offsets.get(c, []))
        }
    today_rank_codes = {
        c for c in (rank_codes or set(closes.columns))
        if not any(o <= today_window for o in anomaly_offsets.get(c, []))
    }

    xs = compute_cross_sectional(
        combined, windows=windows, rank_codes=rank_codes,
        rank_codes_by_window=rank_codes_by_window,
    )
    if not xs:
        return pd.DataFrame()

    returns = _latest_returns(closes, periods=(1, 5, 10, 15, 20, 60))

    # ordinal rank：今日 与 5 个交易日前
    rank_series = xs[windows[0]]["rank"]
    rank_today = rank_series.iloc[-1]
    rank_prev5 = rank_series.shift(RANK_CHANGE_DAYS).iloc[-1] if len(rank_series) > RANK_CHANGE_DAYS else pd.Series(index=rank_series.columns, dtype=float)

    # v0.7.0 Market Pulse 观察指标（仅展示，不参与排序/选择）
    today_rps = _compute_today_rps(closes, today_rank_codes, today_window=today_window)
    rps15_series = xs[windows[0]]["rps"]
    rps15_today = rps15_series.iloc[-1]
    rps15_prev = (
        rps15_series.shift(velocity_window).iloc[-1]
        if len(rps15_series) > velocity_window
        else pd.Series(index=rps15_series.columns, dtype=float)
    )
    liquidity_pct = _compute_liquidity(combined)

    codes = list(closes.columns)
    rows: list[dict[str, Any]] = []
    for code in codes:
        row: dict[str, Any] = {"fund_code": code}
        for w in windows:
            rps = xs[w]["rps"].iloc[-1].get(code)
            row[f"rps{w}"] = None if pd.isna(rps) else float(rps)
        r1 = today_rps.get(code)
        row["rps1"] = None if pd.isna(r1) else float(r1)
        r15v = rps15_today.get(code)
        r15p = rps15_prev.get(code)
        if pd.notna(r15v) and pd.notna(r15p):
            row["delta_rps15"] = float(r15v - r15p)
        else:
            row["delta_rps15"] = None
        liq = liquidity_pct.get(code)
        row["liquidity"] = None if pd.isna(liq) else float(liq)
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
        row["data_quality_flag"] = "corporate_action" if anomaly_offsets.get(code) else ""
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
    # v0.4.3 多主题：按主题关键词打标（兼容旧 parquet 缺列场景）
    etf["theme"] = etf["fund_name"].apply(match_theme)

    numeric_cols = ["rps15", "rps20", "rps60", "rps1", "delta_rps15", "liquidity",
                    "rank15", "rank15_prev5", "rank_change_5d",
                    "return_1d", "return_5d", "return_10d", "return_15d", "return_20d", "return_60d"]
    for c in numeric_cols:
        if c in etf.columns:
            etf[c] = pd.to_numeric(etf[c], errors="coerce")

    logger.info(
        "rotation metrics: %d ETFs (rps15 median=%.1f, tech=%d)",
        len(etf), etf["rps15"].median() if etf["rps15"].notna().any() else float("nan"),
        int(etf["is_tech"].sum()),
    )
    return etf


def coverage(
    rotation: pd.DataFrame,
    watchlist: pd.DataFrame | None = None,
    master_count: int | None = None,
) -> dict[str, int]:
    """Layer① 数据口径统一命名（v0.7.0，P0-3 分母对齐）。

      - master_count        主数据全量 ETF（master / rotation 行数）
      - price_current_count 当前横截面有效：有现价且 RPS15 可算（非防御、非异常）
      - rps_eligible_count  完整指标可算：≥60 交易日（进入 watchlist/卡片链路）
      - trend_active_count  趋势信号活跃（trend_state != OUT_OF_SCOPE）

    报告所有百分比必须以明确分母展示（见 rotation_report 数据口径块与漏斗）。
    """
    total = len(rotation) if not rotation.empty else 0
    mc = master_count if master_count is not None else total
    price_current = int(rotation["rps15"].notna().sum()) if "rps15" in rotation.columns else 0
    if watchlist is not None and not watchlist.empty:
        rps_eligible = len(watchlist)
        if "trend_state" in watchlist.columns:
            trend_active = int((watchlist["trend_state"] != "OUT_OF_SCOPE").sum())
        else:
            trend_active = 0
    else:
        rps_eligible = int(rotation["return_60d"].notna().sum()) if "return_60d" in rotation.columns else 0
        trend_active = 0
    return {
        "master_count": int(mc),
        "price_current_count": int(price_current),
        "rps_eligible_count": int(rps_eligible),
        "trend_active_count": int(trend_active),
    }


def market_summary(etf: pd.DataFrame) -> dict[str, Any]:
    """全市场概览。"""
    valid = etf["rps15"].dropna()
    if valid.empty:
        return {"total": 0}
    total = len(valid)
    ret15 = etf["return_15d"].dropna()
    rps1 = etf["rps1"].dropna() if "rps1" in etf.columns else pd.Series(dtype=float)
    delta = etf["delta_rps15"].dropna() if "delta_rps15" in etf.columns else pd.Series(dtype=float)
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
        # v0.7.0 Market Pulse：今日热度 / 动量 的全市场中位（仅 Observation）
        "median_rps1": round(float(rps1.median()), 1) if not rps1.empty else None,
        "median_delta_rps15": round(float(delta.median()), 1) if not delta.empty else None,
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


def _theme_focus_one(etf: pd.DataFrame, theme_key: str, label: str, bucket_key: str, bucket_label: str) -> dict[str, Any]:
    """单个主题的 ETF 焦点组判断块。"""
    sub = etf[etf["theme"] == theme_key]
    if sub.empty:
        return {"theme": theme_key, "theme_label": label, "bucket": bucket_key,
                "bucket_label": bucket_label, "etf_count": 0}
    rps = sub["rps15"].dropna()
    n = len(sub)
    top10 = int((rps >= 90).sum())
    top20 = int((rps >= 80).sum())
    median_rps = round(float(rps.median()), 1) if not rps.empty else None
    rank_change = _median_round(sub["rank_change_5d"])
    expected_top10 = n * 0.10
    return {
        "theme": theme_key,
        "theme_label": label,
        "bucket": bucket_key,
        "bucket_label": bucket_label,
        "etf_count": n,
        "top10": top10,
        "top20": top20,
        "top10_excess": round(top10 - expected_top10, 1),
        "median_rps15": median_rps,
        "rank_change_5d": rank_change,
        "verdict": _judge_theme_momentum(n, top10, top20, median_rps, rank_change),
    }


def theme_focus_groups(etf: pd.DataFrame) -> list[dict[str, Any]]:
    """v0.4.3 多主题焦点组：对 config/theme_registry.yaml 中每个 theme 输出独立判断块。

    兼容旧 rotation parquet（无 theme 列）：按名称现场匹配。
    """
    if etf.empty or "theme" not in etf.columns:
        etf = etf.copy()
        etf["theme"] = etf["fund_name"].apply(match_theme)
    groups: list[dict[str, Any]] = []
    for b in themes_cfg.load_buckets():
        for th in b.themes:
            groups.append(_theme_focus_one(etf, th.key, th.label, b.key, b.label))
    return groups


def _judge_theme_momentum(
    n: int, top10: int, top20: int, median_rps: float | None, rank_change: float,
) -> str:
    """判断某主题是否正在成为 A 股主线（通用版）。"""
    if n == 0 or median_rps is None:
        return "无有效样本"
    top10_ratio = top10 / n
    if median_rps >= 70 and top10_ratio >= 0.25 and rank_change > 0:
        return "正在成为主线：RPS 中位强势、Top10% 集中度显著、5 日排名整体抬升"
    if median_rps >= 60 and top10_ratio >= 0.15:
        return "初步走强：相对强度高于市场，但集中度或动量尚不足以确认主线"
    if median_rps < 40:
        return "明显弱于市场：该主题当前不是主线方向"
    return "处于市场中位，尚未形成明确主线"


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


# ── 三问三答：跨资产大类 / 方向去重 / 主题池（v0.7.1）─────────────────────────
# 七个跨资产方向（对应 etf_classification.yaml 大类），消费侧只做展示排版，不改排序/选择。
CROSS_ASSET_ORDER = ["A股宽基", "A股行业/主题", "港股", "海外权益", "债券", "商品/黄金", "现金/货币", "其他"]


def _cross_asset_direction(row: Any) -> str:
    """把单只 ETF 归到跨资产方向（消费侧分组，不改任何 Policy）。"""
    asset_class = str(row.get("asset_class", "") or "")
    bucket = str(row.get("asset_bucket", "") or "")
    name = str(row.get("fund_name", "") or "")
    if asset_class == "现金" or bucket == "money_market":
        return "现金/货币"
    if asset_class == "商品" or bucket in ("commodity_gold", "commodity_futures"):
        return "商品/黄金"
    if asset_class == "债券" or bucket in ("bond_treasury", "bond_credit", "bond_convertible"):
        return "债券"
    if asset_class == "海外" or bucket == "overseas_equity":
        if any(k in name for k in ("港股", "恒生", "香港", "h股", "H股", "港通")):
            return "港股"
        return "海外权益"
    if asset_class == "权益" or bucket in ("industry", "theme", "factor_style"):
        if bucket == "broad_market":
            return "A股宽基"
        return "A股行业/主题"
    return "其他"


def _direction_state(median_rps15: float | None, change_5d: float | None) -> str:
    """把 (RPS15中位, 5日变化) 映射为当前方向描述（消费侧展示）。"""
    if median_rps15 is None:
        return "—"
    if median_rps15 >= 75 and change_5d is not None and change_5d >= 5:
        return "强势上行"
    if median_rps15 >= 60 and change_5d is not None and change_5d > 0:
        return "加速"
    if median_rps15 >= 45:
        return "横盘"
    return "弱势下行"


def cross_asset_direction(etf: pd.DataFrame) -> list[dict[str, Any]]:
    """① 全市场大类资产往哪里动：按跨资产方向聚合。

    每行一个方向，字段：direction / etf_count / median_rps15 / change_5d /
    active_ratio / rep_name / direction_state。不展示 RPS1 / 分位数 / 全市场数量。
    """
    if etf.empty:
        return []
    df = etf.copy()
    df["_dir"] = df.apply(_cross_asset_direction, axis=1)
    rows: list[dict[str, Any]] = []
    for d in CROSS_ASSET_ORDER:
        sub = df[df["_dir"] == d]
        if sub.empty:
            continue
        rps = pd.to_numeric(sub["rps15"], errors="coerce").dropna()
        chg = pd.to_numeric(sub["rank_change_5d"], errors="coerce").dropna()
        n = len(sub)
        n_active = int((sub.get("trend_state", pd.Series(dtype=str)).astype(str) != "OUT_OF_SCOPE").sum()) \
            if "trend_state" in sub.columns else 0
        rep = sub.copy()
        if "liquidity" in rep.columns:
            rep = rep[pd.to_numeric(rep["liquidity"], errors="coerce").notna()]
        if not rep.empty:
            rep = rep.sort_values("liquidity", ascending=False)
        median = round(float(rps.median()), 1) if not rps.empty else None
        change = round(float(chg.median()), 1) if not chg.empty else None
        rows.append({
            "direction": d,
            "etf_count": n,
            "median_rps15": median,
            "change_5d": change,
            "active_ratio": round(n_active / n, 2) if n else 0,
            "rep_name": str(rep.iloc[0]["fund_name"]) if not rep.empty else "—",
            "direction_state": _direction_state(median, change),
        })
    return rows


# 方向去重键：优先具体 exposure_name；粗粒度/未分类 → 用基金名去掉公司/ETF 后缀
_GENERIC_EXPOSURES = {"海外", "策略", "周期", "消费", "科技", "金融", "宽基", "地产基建", "新能源", ""}
_ETF_COMPANIES = [
    "华夏", "国泰", "易方达", "南方", "嘉实", "招商", "博时", "广发", "富国", "汇添富",
    "华安", "华宝", "工银", "工银瑞信", "平安", "永赢", "景顺", "银华", "天弘", "华泰柏瑞",
    "鹏华", "浦银", "浦银安盛", "中银", "建信", "兴业", "泰康", "万家", "东财", "海富通",
    "大成", "国联", "融通", "兴全", "中欧", "鹏扬", "浙商", "方正", "华泰", "南方基金",
]


def _direction_key(fund_name: str, exposure_name: str = "") -> str:
    """ETF 方向去重键：具体 exposure_name 优先，否则剥掉基金名公司/ETF 后缀。"""
    exp = str(exposure_name or "").strip()
    if exp and exp not in _GENERIC_EXPOSURES:
        return exp
    s = str(fund_name or "")
    for c in _ETF_COMPANIES:
        if s.endswith(c):
            s = s[: -len(c)]
            break
    for tok in ("ETF", "指数", "基金", "LOF", "联接"):
        s = s.replace(tok, "")
    return s.strip() or str(fund_name or "")


def active_etf_representatives(
    rotation: pd.DataFrame,
    master: pd.DataFrame,
    top_n: int = 40,
) -> tuple[list[dict[str, Any]], int]:
    """② 当前趋势活跃 ETF：按方向去重，每个方向保留一只流动性最好的代表。

    排序：STRONG_WATCH → BUY_CANDIDATE → WATCH → RPS15 → 流动性。
    返回 (代表清单, 总活跃方向数)。完整活跃名单由 watchlist_active.csv 承载。
    """
    if rotation.empty:
        return [], 0
    df = rotation.copy()
    if "trend_state" not in df.columns:
        return [], 0
    active = df[df["trend_state"].astype(str) != "OUT_OF_SCOPE"].copy()
    if active.empty:
        return [], 0
    exp_map = {}
    if not master.empty and "fund_code" in master.columns and "exposure_name" in master.columns:
        exp_map = dict(zip(master["fund_code"], master["exposure_name"]))
    active["_dir"] = active.apply(
        lambda r: _direction_key(str(r.get("fund_name", "")), str(exp_map.get(r.get("fund_code"), ""))),
        axis=1,
    )
    # 每个方向保留流动性最好的代表
    reps: list[dict[str, Any]] = []
    state_rank = {"STRONG_WATCH": 0, "BUY_CANDIDATE": 1, "WATCH": 2}
    for d, g in active.groupby("_dir"):
        liq = pd.to_numeric(g.get("liquidity"), errors="coerce")
        best_idx = liq.idxmax() if liq.notna().any() else g.index[0]
        r = g.loc[best_idx]
        reps.append({
            "direction": d,
            "fund_name": str(r.get("fund_name", "")),
            "fund_code": str(r.get("fund_code", "")),
            "trend_state": str(r.get("trend_state", "")),
            "rps15": _num_round(r.get("rps15")),
            "rps20": _num_round(r.get("rps20")),
            "liquidity": _num_round(r.get("liquidity")),
            "same_count": int(len(g)),
        })
    reps.sort(key=lambda x: (state_rank.get(x["trend_state"], 9),
                             -(x["rps15"] if x["rps15"] is not None else -999),
                             -(x["liquidity"] if x["liquidity"] is not None else -999)))
    total_directions = len(reps)
    return reps[:top_n], total_directions


def _num_round(v: Any) -> float | None:
    try:
        x = float(v)
        return round(x, 1) if not pd.isna(x) else None
    except (TypeError, ValueError):
        return None


# ── 横截面观察（v0.7.0）──────────────────────────────────────────────
