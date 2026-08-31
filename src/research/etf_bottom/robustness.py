"""Study 1B — Deep Stress Robustness。

对 PRICE_LOW×DD30 Deep 的 +21.3%（120D）结论做三重压力测试：
  1. 年份去集中：去除 2025-2026 事件集中后还剩多少；多年份是否为正
  2. ETF 重复暴露：同一 ETF 多次入场是否主导结果；cluster bootstrap 显著性
  3. 参数选择效应：P756 / DD30 阈值扫描，是否参数单调、还是特定参数才成立

核心口径（与 Study 1 一致）：剔除货币/债券、close→close、同市场横截面基准。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _agg_ret(s: Any) -> dict:
    if s is None:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    if not isinstance(s, pd.Series):
        s = pd.Series(s)
    v = pd.to_numeric(s, errors="coerce").dropna()
    if v.empty:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    return {
        "n": int(len(v)),
        "mean": round(float(v.mean()), 4),
        "median": round(float(v.median()), 4),
        "win_rate": round(float((v > 0).mean()), 4),
    }


def year_breakdown(events: pd.DataFrame, event_type: str = "PRICE_LOW_DD30") -> list[dict]:
    """按入场年份分组的收益（回答多年份是否为正）。"""
    sub = events[(events["event_type"] == event_type) & (~events["etf_type"].isin(["money", "bond"]))]
    sub = sub.copy()
    sub["year"] = pd.to_datetime(sub["entry_date"]).dt.year
    out = []
    for year, g in sub.groupby("year"):
        row = {"year": int(year), "n": int(len(g)), "n_etfs": int(g["fund_code"].nunique())}
        for h in ("20", "60", "120"):
            row[str(h)] = _agg_ret(g.get(f"ret_{h}"))
        out.append(row)
    return sorted(out, key=lambda x: x["year"])


def exclude_recent(events: pd.DataFrame, event_type: str = "PRICE_LOW_DD30", through_year: int = 2024) -> dict:
    """去除指定年份之后的事件，重算收益（回答 2025-26 集中是否撑起结论）。"""
    sub = events[(events["event_type"] == event_type) & (~events["etf_type"].isin(["money", "bond"]))]
    sub = sub.copy()
    sub["year"] = pd.to_datetime(sub["entry_date"]).dt.year
    old = sub[sub["year"] <= through_year]
    new = sub[sub["year"] > through_year]
    return {
        f"<= {through_year}": {h: _agg_ret(old.get(f"ret_{h}")) for h in ("20", "60", "120")} | {"n": int(len(old)), "n_etfs": int(old["fund_code"].nunique())},
        f"> {through_year}": {h: _agg_ret(new.get(f"ret_{h}")) for h in ("20", "60", "120")} | {"n": int(len(new)), "n_etfs": int(new["fund_code"].nunique())},
    }


def concentration(events: pd.DataFrame, event_type: str = "PRICE_LOW_DD30") -> dict:
    """每 ETF 事件数分布 + Top-N ETF 对总收益的贡献（回答重复暴露）。"""
    sub = events[events["event_type"] == event_type]
    counts = sub.groupby("fund_code").size().sort_values(ascending=False)
    total = int(counts.sum())
    out = {
        "total_events": total,
        "n_etfs": int(counts.size),
        "max_events_per_etf": int(counts.max()),
        "median_events_per_etf": float(counts.median()),
        "p90_events_per_etf": float(counts.quantile(0.90)),
        "top_etfs": [{"fund_code": str(c), "n_events": int(n), "fund_name": _name_of(sub, c)} for c, n in counts.head(15).items()],
    }
    # 每 ETF 平均收益贡献（120D，event-level 均值）
    sub = sub.copy()
    sub["ret120"] = pd.to_numeric(sub.get("ret_120"), errors="coerce")
    by_etf = sub.groupby("fund_code").agg(n=("ret120", "count"), mean=("ret120", "mean")).dropna(subset=["mean"])
    by_etf = by_etf.sort_values("mean", ascending=False)
    out["n_etfs_with_120d"] = int(len(by_etf))
    out["share_top10_etfs_by_return"] = float(by_etf["mean"].head(10).sum() / max(by_etf["mean"].sum(), 1e-9)) if len(by_etf) else None
    out["best_etfs"] = [{"fund_code": str(c), "n": int(r["n"]), "mean_ret120": round(float(r["mean"]), 4)} for c, r in by_etf.head(10).iterrows()]
    return out


def _name_of(events: pd.DataFrame, code: str) -> str:
    hit = events[events["fund_code"] == code]
    return str(hit["fund_name"].iloc[0]) if len(hit) and "fund_name" in events.columns else ""


def cluster_bootstrap(
    events: pd.DataFrame,
    event_type: str = "PRICE_LOW_DD30",
    horizon: int = 120,
    n_boot: int = 2000,
    seed: int = 42,
) -> dict:
    """按 fund_code 做 cluster bootstrap，得到均值/胜率的分布与显著性。

    块 = 单只 ETF 的全部事件（保留组内相关），重抽样单位 = ETF。
    回答：cluster-adjusted 后 120D 均值是否显著 > 0、胜率是否显著 > 0.5。
    """
    sub = events[(events["event_type"] == event_type) & (~events["etf_type"].isin(["money", "bond"]))]
    sub = sub.copy()
    col = f"ret_{horizon}"
    sub[col] = pd.to_numeric(sub.get(col), errors="coerce")
    sub = sub.dropna(subset=[col])
    if len(sub) < 5:
        return {"status": "insufficient", "n_events": int(len(sub))}

    etfs = sub["fund_code"].unique()
    by_etf = {c: sub.loc[sub["fund_code"] == c, col].to_numpy() for c in etfs}
    rng = np.random.default_rng(seed)
    boot_means: list[float] = []
    boot_wins: list[float] = []
    n_etfs = len(etfs)
    for _ in range(n_boot):
        sample_codes = rng.choice(etfs, size=n_etfs, replace=True)
        vals = np.concatenate([by_etf[c] for c in sample_codes])
        if len(vals):
            boot_means.append(float(vals.mean()))
            boot_wins.append(float((vals > 0).mean()))

    bm = np.array(boot_means)
    bw = np.array(boot_wins)
    obs_mean = float(np.concatenate([by_etf[c] for c in etfs]).mean())
    obs_win = float((np.concatenate([by_etf[c] for c in etfs]) > 0).mean())

    # 按年份块 bootstrap（块 = 单一年份的全部事件，重抽样单位 = 年份）
    # 回答：结论是否被单一年份（2025）撑起
    year_boot = {}
    sub = sub.copy()
    sub["_year"] = pd.to_datetime(sub["entry_date"]).dt.year
    years = sorted(sub["_year"].unique())
    if len(years) >= 2:
        by_year = {y: sub.loc[sub["_year"] == y, col].to_numpy() for y in years}
        yr = np.random.default_rng(seed + 1)
        yb_means, yb_wins = [], []
        for _ in range(n_boot):
            s = yr.choice(years, size=len(years), replace=True)
            vals = np.concatenate([by_year[y] for y in s])
            if len(vals):
                yb_means.append(float(vals.mean()))
                yb_wins.append(float((vals > 0).mean()))
        ym, yw = np.array(yb_means), np.array(yb_wins)
        year_boot = {
            "years": [int(y) for y in years],
            "year_event_counts": {int(y): int(sub.loc[sub["_year"] == y].shape[0]) for y in years},
            "mean_mean": round(float(ym.mean()), 4),
            "mean_p95_ci": [round(float(np.percentile(ym, 2.5)), 4), round(float(np.percentile(ym, 97.5)), 4)],
            "mean_p_gt0": round(float((ym > 0).mean()), 4),
            "win_mean": round(float(yw.mean()), 4),
            "win_p_gt0p5": round(float((yw > 0.5).mean()), 4),
        }

    return {
        "status": "ok",
        "n_events": int(len(sub)),
        "n_etfs": int(n_etfs),
        "n_boot": n_boot,
        "obs_mean": round(obs_mean, 4),
        "obs_win_rate": round(obs_win, 4),
        "mean_mean": round(float(bm.mean()), 4),
        "mean_p95_ci": [round(float(np.percentile(bm, 2.5)), 4), round(float(np.percentile(bm, 97.5)), 4)],
        "mean_p_gt0": round(float((bm > 0).mean()), 4),
        "win_mean": round(float(bw.mean()), 4),
        "win_p95_ci": [round(float(np.percentile(bw, 2.5)), 4), round(float(np.percentile(bw, 97.5)), 4)],
        "win_p_gt0p5": round(float((bw > 0.5).mean()), 4),
        "year_cluster_bootstrap": year_boot,
    }


def parameter_sensitivity(
    states_by_code: dict[str, pd.DataFrame],
    p_low_values: tuple[float, ...] = (10.0, 15.0, 20.0, 25.0, 30.0),
    dd30_values: tuple[float, ...] = (-0.15, -0.20, -0.25, -0.30),
    horizons: tuple[int, ...] = (20, 60, 120),
) -> dict:
    """P756 × DD30 阈值扫描：每个组合重算 PRICE_LOW_DD30 事件的 120D 收益。

    回答：+21.3% 是特定参数点还是参数单调区域。
    事件在阈值扫描间共享低位段，仅 DD30 门槛改变子集；P756 门槛改变低位段本身。
    """
    from .states import extract_events
    from .returns import ClosePanel

    # 为每个 P756 阈值重算 PRICE_LOW 低位段（DD30 门槛仅过滤子集）
    per_etf_rows: dict[str, dict[float, list]] = {}
    for code, states in states_by_code.items():
        for pl in p_low_values:
            st = states.copy()
            st["price_low"] = st["p756"] <= pl
            st["price_low_dd30"] = st["price_low"] & (st["dd30"] <= dd30_values[0])
            ev = extract_events(st, dd30_threshold=dd30_values[0])
            per_etf_rows.setdefault(code, {})[pl] = ev

    # 面板
    pivots = []
    for code, st in states_by_code.items():
        s = st.set_index("date")["close"]
        pivots.append(pd.DataFrame({code: s}))
    pivot = pd.concat(pivots, axis=1).sort_index()
    panel = ClosePanel(pivot)

    results: list[dict] = []
    for pl in p_low_values:
        for dd in dd30_values:
            rows = []
            for code, data in per_etf_rows.items():
                ev = data[pl]
                for e in ev:
                    if e["event_type"] == "PRICE_LOW_DD30":
                        # 重判定：DD30 门槛在当前组合下
                        if e["dd30_at_entry"] is not None and e["dd30_at_entry"] <= dd:
                            rows.append(e)
            df = pd.DataFrame(rows)
            if df.empty:
                results.append({"p_low": pl, "dd30": dd, "n": 0})
                continue
            rets = {h: [] for h in horizons}
            for _, e in df.iterrows():
                fwd = panel.forward_returns(e["fund_code"], e["entry_date"], horizons)
                for h in horizons:
                    rets[h].append(fwd.get(h))
            rec = {"p_low": pl, "dd30": dd, "n": int(len(df))}
            for h in horizons:
                v = pd.Series(rets[h]).dropna()
                rec[f"{h}_mean"] = round(float(v.mean()), 4) if len(v) else None
                rec[f"{h}_win"] = round(float((v > 0).mean()), 4) if len(v) else None
                rec[f"{h}_n"] = int(len(v))
            results.append(rec)
    return {"scan": results, "p_low_values": list(p_low_values), "dd30_values": list(dd30_values)}


def run_robustness(
    events: pd.DataFrame,
    states_by_code: dict[str, pd.DataFrame] | None = None,
    n_boot: int = 2000,
) -> dict:
    """Study 1B 编排：三重压力测试 + 参数扫描。"""
    return {
        "year_breakdown_dd30": year_breakdown(events, "PRICE_LOW_DD30"),
        "year_breakdown_low": year_breakdown(events, "PRICE_LOW"),
        "exclude_recent_dd30": exclude_recent(events, "PRICE_LOW_DD30"),
        "concentration_dd30": concentration(events, "PRICE_LOW_DD30"),
        "cluster_bootstrap_dd30": cluster_bootstrap(events, "PRICE_LOW_DD30", horizon=120, n_boot=n_boot),
        "cluster_bootstrap_low": cluster_bootstrap(events, "PRICE_LOW", horizon=120, n_boot=n_boot),
        "parameter_sensitivity": parameter_sensitivity(states_by_code) if states_by_code else {},
    }
