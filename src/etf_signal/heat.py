"""
ETF 市场热度模型 — Layer 1

职责：
  - 在 AKShare 全市场数据之上，由 AKsignal 计算全市场热度分布
  - 按资产桶聚合，先分大类（权益/非权益）再拆子桶
  - 不直接对所有 ETF 混排

分层说明：
  AKShare 提供全市场 ETF 数据底座。
  AKsignal heat 是使用者，不是数据源。

  Layer 1 回答：「市场热度在哪类资产集中？当前更适合进攻还是防御？」

每日输出示例：

  资产大类    资产桶       强势占比  中位 RPS  热度变化  当前状态
  权益        港股权益     55%      84        上升      市场热度集中
  权益        风格因子     42%      78        上升      红利、价值占优
  权益        A 股行业     18%      56        上升      局部行业活跃
  非权益      债券         80%      93        高位      防御资产稳定
  非权益      商品         30%      67        平稳      局部强势
  权益        海外权益     25%      61        下降      趋势一般

P0-C 交付物
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger("etf_signal.heat")

# ── 资产大类映射：权益 / 非权益 ──────────────────────────────────
ASSET_CLASS_MAP: dict[str, str] = {
    "cn_equity": "权益",
    "hk_overseas_equity": "权益",
    "commodity": "非权益",
    "bond": "非权益",
    "cash": "非权益",
}

BUCKET_LABELS: dict[str, str] = {
    "cn_equity": "A 股权益",
    "hk_overseas_equity": "港股及海外权益",
    "commodity": "商品",
    "bond": "债券",
    "cash": "货币与现金管理",
}

# Layer 1 下分桶的二级分类（仅用于展示，不参与计算）
EXPOSURE_HIERARCHY: dict[str, list[str]] = {
    "cn_equity": ["broad_market", "industry", "theme", "factor_style"],
    "hk_overseas_equity": ["broad_market", "region_country", "theme"],
    "commodity": ["commodity_spot", "commodity_futures"],
    "bond": ["interest_rate_bond", "credit_bond", "convertible_bond"],
    "cash": ["money_market"],
}


def compute_rps(returns: pd.Series) -> pd.Series:
    """计算百分位排名 RPS（0-100）。

    同一资产桶内从弱到强排序，返回每只 ETF 在桶内的相对强度百分位。
    """
    if len(returns) < 2:
        return pd.Series([50.0] * len(returns), index=returns.index)
    return returns.rank(ascending=True, pct=True) * 100


def _classify_heat_change(
    strong_ratio: float,
    median_rps: float,
    prev_strong_ratio: float | None = None,
) -> str:
    """判断热度变化状态。

    Returns:
        "上升" / "下降" / "高位" / "平稳"
    """
    if median_rps >= 85 and strong_ratio >= 0.5:
        return "高位"
    if prev_strong_ratio is not None:
        diff = strong_ratio - prev_strong_ratio
        if diff > 0.05:
            return "上升"
        if diff < -0.05:
            return "下降"
    return "平稳"


def _describe_state(
    asset_bucket: str,
    strong_ratio: float,
    median_rps: float,
    heat_change: str,
) -> str:
    """为每个资产桶生成可读的状态描述。"""
    if heat_change == "高位":
        if asset_bucket in ("bond", "cash"):
            return "防御资产稳定"
        return "市场热度集中"
    if heat_change == "上升":
        if asset_bucket == "cn_equity":
            if median_rps >= 75:
                return "宽基强势"
            if median_rps >= 65:
                return "局部行业活跃"
            return "结构性回暖"
        if asset_bucket == "hk_overseas_equity":
            return "市场热度集中"
        if asset_bucket == "commodity":
            return "商品升温"
        if asset_bucket in ("bond", "cash"):
            return "防御资金流入"
        return "热度上升"
    if heat_change == "下降":
        if asset_bucket == "hk_overseas_equity":
            return "趋势一般"
        return "热度回落"
    if strong_ratio >= 0.3:
        return "局部强势"
    if strong_ratio >= 0.15:
        return "温和活跃"
    return "整体平淡"


def compute_bucket_heat(
    daily: pd.DataFrame,
    master: pd.DataFrame,
    lookback: int = 20,
    prev_heat_map: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """按资产桶计算全市场热度分布（Layer 1）。

    在全市场数据基础上回答：
    ETF 市场的趋势现在集中在哪类资产？

    Returns:
        热度地图 DataFrame，每行一个资产桶：
        asset_class   资产大类（权益/非权益）
        asset_bucket  资产桶代码
        bucket_label  资产桶中文名
        etf_count     桶内 ETF 数量
        strong_ratio  强势标的占比（RPS >= 80）
        median_rps    中位 RPS（0-100）
        heat_change   热度变化：上升 / 下降 / 平稳 / 高位
        description   可读状态描述
    """
    if daily.empty or master.empty:
        return pd.DataFrame()

    if "fund_code" not in daily.columns:
        logger.warning("daily data missing fund_code column")
        return pd.DataFrame()

    merge_cols = ["fund_code", "asset_bucket"]
    merged = daily.merge(
        master[merge_cols].drop_duplicates(subset=["fund_code"]),
        on="fund_code", how="inner",
    )

    merged = merged[merged["asset_bucket"].notna() & (merged["asset_bucket"] != "")]
    if merged.empty:
        logger.warning("no classified ETFs in daily data")
        return pd.DataFrame()

    merged = merged.sort_values(["fund_code", "date"])
    merged["return"] = merged.groupby("fund_code")["close"].pct_change(lookback)

    latest_date = merged["date"].max()
    latest = merged[merged["date"] == latest_date].dropna(subset=["return"]).copy()
    if latest.empty:
        logger.warning("no valid returns on latest date %s", latest_date)
        return pd.DataFrame()

    prev_strong: dict[str, float] = {}
    if prev_heat_map is not None and not prev_heat_map.empty:
        for _, row in prev_heat_map.iterrows():
            prev_strong[row["asset_bucket"]] = row["strong_ratio"]

    results: list[dict[str, Any]] = []
    for bucket, group in latest.groupby("asset_bucket"):
        etf_count = group["fund_code"].nunique()
        if etf_count < 1:
            continue

        rps = compute_rps(group["return"])
        median_rps = rps.median()
        strong_ratio = (rps >= 80).mean()

        prev = prev_strong.get(bucket)
        heat_change = _classify_heat_change(strong_ratio, median_rps, prev)

        results.append({
            "asset_class": ASSET_CLASS_MAP.get(bucket, "其他"),
            "asset_bucket": bucket,
            "bucket_label": BUCKET_LABELS.get(bucket, bucket),
            "etf_count": etf_count,
            "strong_ratio": round(strong_ratio, 4),
            "median_rps": round(median_rps, 2),
            "heat_change": heat_change,
            "description": _describe_state(bucket, strong_ratio, median_rps, heat_change),
        })

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values(
            ["asset_class", "median_rps"],
            ascending=[True, False],
        ).reset_index(drop=True)
    return df


def assess_market_risk_appetite(heat_map: pd.DataFrame) -> dict[str, Any]:
    """从热度地图判断当前市场风险偏好。

    通过权益类 vs 非权益类的热度对比，判断市场处于：
    - 进攻模式：权益类强势占比高，资金流入权益
    - 防御模式：资金集中在债券/货币
    - 均衡模式：无明显偏向

    Returns:
        {preference, equity_heat, defensive_heat, top_bucket, note}
    """
    if heat_map.empty:
        return {"preference": "unknown", "note": "无热度数据"}

    equity = heat_map[heat_map["asset_class"] == "权益"]
    defensive = heat_map[heat_map["asset_class"] == "非权益"]

    equity_heat = equity["median_rps"].mean() if not equity.empty else 0
    defensive_heat = defensive["median_rps"].mean() if not defensive.empty else 0
    equity_strong = equity["strong_ratio"].max() if not equity.empty else 0

    top = heat_map.sort_values("median_rps", ascending=False).iloc[0]

    if equity_heat >= 70 and equity_strong >= 0.3:
        preference = "进攻"
        note = f"权益类热度集中（{top['bucket_label']} {top['description']}）"
    elif defensive_heat > equity_heat and defensive_heat >= 75:
        preference = "防御"
        note = f"防御类资产占优（{top['bucket_label']} {top['description']}）"
    else:
        preference = "均衡"
        note = "无明显偏向"

    return {
        "preference": preference,
        "equity_heat": round(equity_heat, 1),
        "defensive_heat": round(defensive_heat, 1),
        "top_bucket": top["bucket_label"],
        "note": note,
    }
