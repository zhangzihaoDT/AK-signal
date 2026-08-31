"""Aug2026 研究：Layer A（描述）/ B（特征归因）/ C（组合回测）。

口径：
- 所有特征 ≤ 2026-07-31（无 look-ahead）
- 收益 = 8/28 ÷ 7/31 − 1（qfq）
- 组合构造只用 7/31 信息
- 双 Target：hit_abs_5（>5%）、excess_return（− HS300）、hit_excess_5（超额 >5pp）
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np
import pandas as pd

from . import STUDY_DIR

logger = logging.getLogger("research.aug2026.layers")


def hs300_aug_return() -> float | None:
    """HS300 8 月收益（从刷新后的基准计算）。"""
    try:
        df = pd.read_csv("data/raw/_benchmark_sh000300.csv", parse_dates=["date"])
    except Exception:
        return None
    start = df[df["date"] == pd.Timestamp("2026-07-31")]["close"]
    end = df[df["date"] == pd.Timestamp("2026-08-28")]["close"]
    if start.empty or end.empty:
        return None
    return float(end.iloc[0] / start.iloc[0] - 1.0)


def with_targets(df: pd.DataFrame, bench: float | None) -> pd.DataFrame:
    """添加 hit_abs_5 / excess_return / hit_excess_5 三列。"""
    out = df.copy()
    out["hit_abs_5"] = out["return_aug"] > 0.05
    if bench is not None:
        out["excess_return"] = out["return_aug"] - bench
        out["hit_excess_5"] = out["excess_return"] > 0.05
    else:
        out["excess_return"] = np.nan
        out["hit_excess_5"] = False
    return out


# ── Layer A：描述统计 ──────────────────────────────────────────

def _percentile_report(returns: pd.Series) -> dict[str, float]:
    r = returns.dropna()
    return {
        "n": float(len(r)),
        "mean": float(r.mean()),
        "median": float(r.median()),
        "p10": float(r.quantile(0.10)),
        "p25": float(r.quantile(0.25)),
        "p75": float(r.quantile(0.75)),
        "p90": float(r.quantile(0.90)),
        "max": float(r.max()),
        "min": float(r.min()),
        "pct_pos": float((r > 0).mean()),
        "pct_gt_5": float((r > 0.05).mean()),
        "pct_gt_10": float((r > 0.10).mean()),
        "pct_gt_20": float((r > 0.20).mean()),
    }


def layer_a_distribution(returns: pd.Series, universe_name: str, bench: float | None) -> dict[str, Any]:
    """全市场/固定池横截面分布 + 跑赢 HS300 比例。"""
    r = returns.dropna()
    out = _percentile_report(r)
    out["universe"] = universe_name
    out["beat_hs300_rate"] = float((r > bench).mean()) if bench is not None else None
    out["mean_excess"] = float((r - bench).mean()) if bench is not None else None
    return out


def layer_a_top_bottom(panel: pd.DataFrame, name_col: str = "name", n: int = 50) -> dict[str, list[dict]]:
    p = panel.copy().dropna(subset=["return_aug"]).sort_values("return_aug", ascending=False)
    cols = ["code", name_col, "return_aug"]
    # 去重列名，避免重复列 warning
    if name_col == "code":
        cols = ["code", "return_aug"]
    top = p.iloc[:n].loc[:, cols]
    bottom = p.iloc[-n:].loc[:, cols]
    return {
        "top": top.to_dict("records"),
        "bottom": bottom.to_dict("records"),
    }


# ── Layer B：特征归因 ──────────────────────────────────────────

def _bucket_stats(g: pd.DataFrame, bench: float | None) -> dict[str, Any]:
    r = g["return_aug"].dropna()
    return {
        "n": int(len(r)),
        "mean_return": round(float(r.mean()), 4),
        "median_return": round(float(r.median()), 4),
        "hit_5_rate": round(float((r > 0.05).mean()), 4),
        "beat_hs300_rate": round(float((r > bench).mean()), 4) if bench is not None else None,
        "mean_excess": round(float((r - bench).mean()), 4) if bench is not None else None,
    }


def layer_b_features(panel: pd.DataFrame, bench: float | None) -> dict[str, pd.DataFrame]:
    """按 7/31 特征分桶，输出每组统计。"""
    p = with_targets(panel, bench)
    results: dict[str, pd.DataFrame] = {}

    # 1) trend_score 分档
    def _trend_bucket(v):
        if pd.isna(v):
            return "NA"
        if v >= 80:
            return "T_high(80+)"
        if v >= 60:
            return "T_mid(60-79)"
        if v >= 30:
            return "T_low(30-59)"
        return "T_weak(<30)"

    p["trend_bucket"] = p["trend_score"].map(_trend_bucket)
    results["trend_score"] = p.groupby("trend_bucket", dropna=False).apply(
        lambda g: _bucket_stats(g, bench)).apply(pd.Series).reset_index()

    # 2) watch_level
    results["watch_level"] = p.groupby("watch_level", dropna=False).apply(
        lambda g: _bucket_stats(g, bench)).apply(pd.Series).reset_index()

    # 3) tier
    results["tier"] = p.groupby("tier", dropna=False).apply(
        lambda g: _bucket_stats(g, bench)).apply(pd.Series).reset_index()

    # 4) theme
    results["theme"] = p.groupby("theme", dropna=False).apply(
        lambda g: _bucket_stats(g, bench)).apply(pd.Series).reset_index()

    # 5) position_level（MA60 bias）
    results["position"] = p.groupby("position_level", dropna=False).apply(
        lambda g: _bucket_stats(g, bench)).apply(pd.Series).reset_index()

    # 6) 20D momentum 分档
    def _mom_bucket(v):
        if pd.isna(v):
            return "NA"
        if v >= 0.10:
            return "M_strong(+10%)"
        if v >= 0:
            return "M_positive(0-10%)"
        if v >= -0.10:
            return "M_weak(-10~0%)"
        return "M_neg(<-10%)"

    p["mom_bucket"] = p["return_20d_7_31"].map(_mom_bucket)
    results["momentum_20d"] = p.groupby("mom_bucket", dropna=False).apply(
        lambda g: _bucket_stats(g, bench)).apply(pd.Series).reset_index()

    return results


# ── Layer C：组合回测（只用 7/31 信息选股） ────────────────────

def _equal_weight_return(codes: list[str], panel: pd.DataFrame) -> float | None:
    sub = panel[panel["code"].isin(codes)]
    sub = sub.dropna(subset=["return_aug"])
    if sub.empty:
        return None
    return float(sub["return_aug"].mean())


def layer_c_portfolios(panel: pd.DataFrame, bench: float | None) -> dict[str, Any]:
    """用 7/31 信息构造组合，比较 8 月实际表现。

    固定池面板含特征列（trend_score/watch_level/position_level/theme/tier）；
    全市场面板无特征，仅构造横截面分位组合。
    """
    p = panel.dropna(subset=["return_aug"])
    # 去掉 8 月才上市/次新（不入主样本组合）
    p = p[p["full_month_sample"].fillna(True)]

    portfolios: dict[str, list[str]] = {}
    has_feature = "trend_score" in p.columns

    # Baseline：等权
    portfolios["Baseline_等权"] = p["code"].tolist()

    if has_feature:
        # Trend Top20
        t20 = p.sort_values("trend_score", ascending=False).head(20)
        portfolios["TrendTop20"] = t20["code"].tolist()

        # Trend Top10
        t10 = p.sort_values("trend_score", ascending=False).head(10)
        portfolios["TrendTop10"] = t10["code"].tolist()

        # Leader（watch_level=A/S）
        leader = p[p["watch_level"].isin(["S", "A"])]
        portfolios["Leader(SA)"] = leader["code"].tolist()

        # Leader + Trend>=80
        lt = p[(p["watch_level"].isin(["S", "A"])) & (p["trend_score"] >= 80)]
        portfolios["Leader_Trend80"] = lt["code"].tolist()

        # Leader + Trend>=60
        lt60 = p[(p["watch_level"].isin(["S", "A"])) & (p["trend_score"] >= 60)]
        portfolios["Leader_Trend60"] = lt60["code"].tolist()

        # LOW + Trend>=80（低位 + 强趋势）
        low_t = p[(p["position_level"] == "LOW") & (p["trend_score"] >= 80)]
        portfolios["Low_Trend80"] = low_t["code"].tolist()

        # LOW + Leader
        low_l = p[(p["position_level"] == "LOW") & (p["watch_level"].isin(["S", "A"]))]
        portfolios["Low_Leader"] = low_l["code"].tolist()

        # 每主题 Top3（按 trend_score）
        for theme, g in p.groupby("theme"):
            top3 = g.sort_values("trend_score", ascending=False).head(3)
            portfolios[f"ThemeTop3_{theme}"] = top3["code"].tolist()

        # 每 tier Top1（按 trend_score）
        for tier, g in p.groupby("tier"):
            top1 = g.sort_values("trend_score", ascending=False).head(1)
            portfolios[f"TierTop1_{tier}"] = top1["code"].tolist()
    else:
        # 全市场：横截面分位组合（按 7/31 已知的 20D 动量分档模拟）
        if "return_20d_7_31" in p.columns:
            mom_neg = p[p["return_20d_7_31"] <= -0.10]
            mom_pos = p[p["return_20d_7_31"] >= 0.10]
            portfolios["M_neg20d(<-10%)"] = mom_neg["code"].tolist()
            portfolios["M_pos20d(+10%)"] = mom_pos["code"].tolist()
        # 全市场仅横截面：用 start_close 不可排序（无意义），跳过

    rows = []
    for name, codes in portfolios.items():
        ret = _equal_weight_return(codes, p)
        if ret is None:
            continue
        n = len(codes)
        sub = p[p["code"].isin(codes)]
        min_member = float(sub["return_aug"].min()) if not sub.empty else None
        rows.append({
            "portfolio": name,
            "n": n,
            "aug_return": round(ret, 4),
            "excess": round(ret - bench, 4) if bench is not None else None,
            "hit_abs_5": bool(ret > 0.05),
            "hit_excess_5": bool(ret - bench > 0.05) if bench is not None else None,
            "worst_member": round(min_member, 4) if min_member is not None else None,
        })
    return {"portfolios": rows, "benchmark_hs300": bench}


# ── 保存 ───────────────────────────────────────────────────────

def save_layer(name: str, obj: Any) -> None:
    import json

    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, dict) and all(not isinstance(v, pd.DataFrame) for v in obj.values()):
        path = STUDY_DIR / f"{name}.json"
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    elif isinstance(obj, pd.DataFrame):
        path = STUDY_DIR / f"{name}.csv"
        obj.to_csv(path, index=False, encoding="utf-8")
    elif isinstance(obj, dict):
        # 含 DataFrame 的 dict → 每个值一个 csv
        for k, v in obj.items():
            if isinstance(v, pd.DataFrame):
                v.to_csv(STUDY_DIR / f"{name}_{k}.csv", index=False, encoding="utf-8")
    logger.info("saved %s", name)
