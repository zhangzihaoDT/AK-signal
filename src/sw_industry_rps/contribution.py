from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.market_data import (
    fetch_cn_daily, compute_return,
    load_stock_cache, save_stock_cache,
    load_checkpoint, save_checkpoint, clear_checkpoint,
)


@dataclass
class ContributionRow:
    stock_code: str
    stock_name: str
    weight_pct: float
    stock_return_pct: float
    contribution_pct: float
    cum_contribution_pct: float


@dataclass
class DrilldownResult:
    industry_code: str
    industry_name: str
    breakout_date: str
    window: int
    industry_return_pct: float        # actual Shenwan index return
    proxy_return_pct: float           # Σ(weight × stock_return)
    reconstruction_gap_pct: float     # proxy_return - actual_return
    reconstruction_quality: str       # good | moderate | poor
    weight_coverage: float            # fetched weight / total weight
    count_coverage: float             # fetched count / total count
    num_constituents: int
    num_positive: int
    num_negative: int
    participation_rate: float
    hhi: float
    top1_weight: float
    top1_share: float
    top3_share: float
    contribution_structure: str       # single_core | leader_concentrated | multi_leader | distributed
    breadth_structure: str            # broad | moderate | narrow | divergent
    top_contributors: list[ContributionRow] = field(default_factory=list)


logger = logging.getLogger("sw_industry_rps.contribution")


def compute_industry_return(industry_hist: pd.DataFrame, date_str: str, window: int) -> float | None:
    if industry_hist.empty or "trade_date" not in industry_hist.columns:
        return None
    sub = industry_hist[industry_hist["trade_date"] <= pd.Timestamp(date_str)].copy()
    sub = sub.sort_values("trade_date")
    if len(sub) < window + 1:
        return None
    recent = sub.tail(window + 1)
    ret = (recent["close"].iloc[-1] / recent["close"].iloc[0] - 1) * 100
    return round(ret, 2)


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


def classify_contribution_structure(
    top1_weight: float,
    top1_share: float,
    top3_share: float,
) -> str:
    if top1_share >= 0.5:
        return "single_core"
    if top1_weight < 30 and top3_share >= 0.5:
        return "multi_leader"
    if top3_share >= 0.6:
        return "leader_concentrated"
    if top3_share >= 0.35:
        return "multi_leader"
    return "distributed"


def classify_breadth_structure(
    participation_rate: float,
    num_negative: int,
    total_count: int,
    neg_contrib_sum: float,
    total_abs_contrib: float,
) -> str:
    if total_abs_contrib > 0 and num_negative / total_count >= 0.3:
        neg_share = abs(neg_contrib_sum) / total_abs_contrib
        if neg_share >= 0.25:
            return "divergent"
    if participation_rate >= 0.7:
        return "broad"
    if participation_rate >= 0.4:
        return "moderate"
    return "narrow"


def format_structures(contribution: str, breadth: str) -> str:
    cl = CONTRIBUTION_LABELS.get(contribution, contribution)
    bl = BREADTH_LABELS.get(breadth, breadth)
    return f"{cl} × {bl}"


def compute_drilldown(
    industry_code: str,
    industry_name: str,
    breakout_date: str,
    constituents: pd.DataFrame,
    industry_hist: pd.DataFrame,
    stock_data_dir: Path | None = None,
    window: int = 10,
) -> DrilldownResult:
    lgr = logging.getLogger("sw_industry_rps.contribution")
    ind_ret = compute_industry_return(industry_hist, breakout_date, window)
    if ind_ret is None:
        return DrilldownResult(
            industry_code=industry_code, industry_name=industry_name,
            breakout_date=breakout_date, window=window,
            industry_return_pct=0, proxy_return_pct=0,
            reconstruction_gap_pct=0, reconstruction_quality="数据不足",
            weight_coverage=0, count_coverage=0,
            num_constituents=0, num_positive=0,
            num_negative=0, participation_rate=0, hhi=0,
            top1_weight=0, top1_share=0, top3_share=0,
            contribution_structure="数据不足", breadth_structure="",
        )

    stock_codes = constituents["股票代码"].tolist()
    end_dt = pd.Timestamp(breakout_date)
    start_dt = end_dt - pd.Timedelta(days=(window + 5) * 2)
    start_str = start_dt.strftime("%Y%m%d")
    end_str = end_dt.strftime("%Y%m%d")

    # 检查 legulegu 是否有该窗口的涨幅列
    leg_col = f"近{window}日涨幅"
    has_leg_return = leg_col in constituents.columns

    # 按代理权重降序排列：优先抓取核心股票
    constituents_sorted = constituents.sort_values("weight", ascending=False).reset_index(drop=True)
    total_const_count = len(constituents_sorted)

    # 尝试从缓存加载 legulegu 数据（在 has_leg_return 为 True 时）
    cache_date = breakout_date
    rows: list[ContributionRow] = []
    for _, row in constituents_sorted.iterrows():
        raw_code = row["股票代码"]
        symbol = raw_code.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        weight = float(row.get("weight", 0))

        # 缓存优先：检查逐股票缓存
        cached_df = load_stock_cache(symbol, cache_date, window)
        if cached_df is not None and not cached_df.empty:
            stock_ret_cached = compute_return(cached_df["close"], window)
            if stock_ret_cached is not None:
                contrib = round(weight * stock_ret_cached / 100, 4)
                rows.append(ContributionRow(
                    stock_code=raw_code,
                    stock_name=str(row.get("股票简称", "")),
                    weight_pct=weight,
                    stock_return_pct=stock_ret_cached,
                    contribution_pct=contrib,
                    cum_contribution_pct=0,
                ))
                continue

        # 优先使用 legulegu 返回的涨跌幅（零 API 开销，更高覆盖率）
        if has_leg_return:
            leg_val = row.get(leg_col)
            if leg_val is not None and not (isinstance(leg_val, float) and pd.isna(leg_val)):
                stock_ret = float(leg_val)
                contrib = round(weight * stock_ret / 100, 4)
                rows.append(ContributionRow(
                    stock_code=raw_code,
                    stock_name=str(row.get("股票简称", "")),
                    weight_pct=weight,
                    stock_return_pct=round(stock_ret, 2),
                    contribution_pct=contrib,
                    cum_contribution_pct=0,
                ))
                # 缓存 legulegu 结果
                import pandas as _pd
                cache_df = _pd.DataFrame({
                    "date": [_pd.Timestamp(breakout_date)],
                    "close": [float("nan")],
                })
                save_stock_cache(cache_df, symbol, cache_date, window, source="legulegu")
                continue

        # 回退：多源获取（em → sina → tx + 节流 + 退避），成功后落盘
        df = fetch_cn_daily(symbol, start_str, end_str)
        if df is None or df.empty:
            continue
        stock_ret = compute_return(df["close"], window)
        if stock_ret is None:
            continue
        contrib = round(weight * stock_ret / 100, 4)
        rows.append(ContributionRow(
            stock_code=raw_code,
            stock_name=str(row.get("股票简称", "")),
            weight_pct=weight,
            stock_return_pct=round(stock_ret, 2),
            contribution_pct=contrib,
            cum_contribution_pct=0,
        ))
        save_stock_cache(df, symbol, cache_date, window, source="em")

    if not rows:
        return DrilldownResult(
            industry_code=industry_code, industry_name=industry_name,
            breakout_date=breakout_date, window=window,
            industry_return_pct=ind_ret, proxy_return_pct=0,
            reconstruction_gap_pct=-ind_ret, reconstruction_quality="数据不足",
            weight_coverage=0, count_coverage=0,
            num_constituents=0, num_positive=0,
            num_negative=0, participation_rate=0, hhi=0,
            top1_weight=0, top1_share=0, top3_share=0,
            contribution_structure="数据不足", breadth_structure="",
        )

    # Top1 权重股门控：如果核心股票未获取，不进行贡献分类
    all_fetched_codes = set(r.stock_code for r in rows)
    top1_code = constituents_sorted.iloc[0]["股票代码"]
    if top1_code not in all_fetched_codes:
        return DrilldownResult(
            industry_code=industry_code, industry_name=industry_name,
            breakout_date=breakout_date, window=window,
            industry_return_pct=ind_ret, proxy_return_pct=0,
            reconstruction_gap_pct=-ind_ret, reconstruction_quality=f"poor（Top1 {top1_code} 未获取）",
            weight_coverage=0, count_coverage=0,
            num_constituents=len(rows), num_positive=0,
            num_negative=0, participation_rate=0, hhi=0,
            top1_weight=0, top1_share=0, top3_share=0,
            contribution_structure="数据不足", breadth_structure="",
        )

    rows.sort(key=lambda r: abs(r.contribution_pct), reverse=True)
    cum = 0.0
    for r in rows:
        cum += abs(r.contribution_pct)
        r.cum_contribution_pct = round(cum, 4)

    # 覆盖率（双维度）
    total_const_weight = constituents["weight"].sum()
    covered_weight = sum(r.weight_pct for r in rows)
    weight_cov = covered_weight / total_const_weight if total_const_weight > 0 else 0
    count_cov = len(rows) / total_const_count if total_const_count > 0 else 0

    # 发布门控（按权重检查，非按贡献排序）
    all_fetched_codes = set(r.stock_code for r in rows)
    top1_success = constituents_sorted.iloc[0]["股票代码"] in all_fetched_codes
    top3_weight_codes = set(constituents_sorted.head(3)["股票代码"])
    top3_ok = len(top3_weight_codes & all_fetched_codes) >= 3

    gate_failures: list[str] = []
    if ind_ret is None:
        gate_failures.append("指数收益缺失")
    if weight_cov < 0.95:
        gate_failures.append(f"权重覆盖率{weight_cov:.1%}<95%")
    if count_cov < 0.90:
        gate_failures.append(f"数量覆盖率{count_cov:.1%}<90%")
    if not top1_success:
        gate_failures.append("Top1 未获取")
    if not top3_ok:
        gate_failures.append("Top3 未全部获取")

    proxy_ret = sum(r.contribution_pct for r in rows)
    gap = round(proxy_ret - ind_ret, 4)

    abs_gap = abs(gap)
    denom = max(abs(ind_ret), 2.0)
    quality_score = 1 - min(abs_gap / denom, 1.0)
    if gate_failures:
        q_label = f"poor（{'，'.join(gate_failures)}）"
    else:
        q_label = "good" if quality_score >= 0.8 else "moderate" if quality_score >= 0.5 else "poor"

    top1_weight = rows[0].weight_pct
    total_mv_weight = sum(r.weight_pct for r in rows)
    hhi = sum(r.weight_pct * r.weight_pct for r in rows) / 10000
    num_positive = sum(1 for r in rows if r.contribution_pct > 0)
    num_negative = sum(1 for r in rows if r.contribution_pct < 0)
    participation = num_positive / len(rows)

    abs_contribs = [abs(r.contribution_pct) for r in rows]
    total_abs = sum(abs_contribs)
    top1_share = abs_contribs[0] / total_abs if total_abs > 0 else 0
    top3_share = sum(abs_contribs[:3]) / total_abs if total_abs > 0 else 0
    neg_contrib_sum = sum(r.contribution_pct for r in rows if r.contribution_pct < 0)

    contrib_struct = classify_contribution_structure(top1_weight, top1_share, top3_share)
    breadth_struct = classify_breadth_structure(
        participation, num_negative, len(rows), neg_contrib_sum, total_abs,
    )

    return DrilldownResult(
        industry_code=industry_code,
        industry_name=industry_name,
        breakout_date=breakout_date,
        window=window,
        industry_return_pct=ind_ret,
        proxy_return_pct=round(proxy_ret, 4),
        reconstruction_gap_pct=gap,
        reconstruction_quality=q_label,
        weight_coverage=round(weight_cov, 4),
        count_coverage=round(count_cov, 4),
        num_constituents=len(rows),
        num_positive=num_positive,
        num_negative=num_negative,
        participation_rate=round(participation, 4),
        hhi=round(hhi, 4),
        top1_weight=round(top1_weight, 2),
        top1_share=round(top1_share, 4),
        top3_share=round(top3_share, 4),
        contribution_structure=contrib_struct,
        breadth_structure=breadth_struct,
        top_contributors=rows[:10],
    )
