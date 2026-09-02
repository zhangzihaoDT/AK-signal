"""Repair-Retest V1 历史触发频率回测（Application Backtest / Signal Incidence Test）。

定位：判断冻结后的 REPAIR_RETEST_V1 在历史 Application 中，到底是「健康的低频信号」
还是「稀疏到缺乏实际使用价值」。**不是 Discovery**——规则完全冻结，不做优化。

唯一规则真源：config/research/repair_retest_v1.yaml（严格复用 scanner 语义）：
  - reliable_360 判定（full_360_sample ∧ ¬unreliable_360 ∧ ¬flat_price_noise）
  - long_term_bottom / in_domain 判定（pos120≤20 且 pos360≤20）
  - frozen pos60 / pos120 cut points
  - classify_target()（TARGET / NEAR_MISS / NON_TARGET）

No look-ahead 原则：
  > 每一个历史交易日，都假设当天就是真实的 as-of date，只允许使用当日及以前数据。

实现策略：
  对每只 ETF 逐日计算 daily_state_series（滚动窗口，与 compute_row 在任意锚点逐值一致，
  已由测试锁定），然后按交易日重建横截面：对每个 (ETF, date) 应用 frozen cut 得到
  target_stage。基准 = 同市场横截面中位前向收益（延续 V1 outcome 定义 excess_vs_etf_market）。

产物（outputs/research/etf_bottom/backtest_v1/）：
  - v1_signal_daily.parquet     逐 (ETF, date) 信号明细
  - v1_incidence_summary.json   Signal Incidence 指标 + 空窗 + 年份/ETF/产业簇集中度 + verdict
  - v1_zero_target_streaks.csv  连续 0 TARGET 空窗
  - v1_target_events.csv        TARGET / NEAR_MISS / IN_DOMAIN_NON_TARGET episode
  - v1_forward_odds.csv         TARGET / NEAR_MISS / IN_DOMAIN_NON_TARGET 前向赔率对照
  - v1_backtest_report.html     报告首页只回答 6 个数字 + verdict
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import etf_signal_master_dir, etf_signal_raw_dir

from . import STUDY_DIR
from .current_eval import load_frozen_cutpoints, load_rule_spec
from .episodes import INDUSTRY_CLUSTERS
from .returns import ClosePanel
from .scanner import classify_target, _verify_frozen
from .state_odds import daily_state_series
from .universe import calibrate_etf_type

logger = logging.getLogger(__name__)

MIN_HIST_DAYS = 60          # 低于此不进入横截面（与 price_map / scanner 一致）
V1_BASE_HIST = 756          # V1 原始研究 universe 的历史下限（BASE cohort）

CLUSTER_NAME_BY_CODE = {c: cl for cl, codes in INDUSTRY_CLUSTERS.items() for c in codes}

_HORIZONS = (20, 60, 120)
_FORWARD_INCIDENCE = (5, 10, 20, 40, 60)

DEFAULT_START = "2022-01-01"
DEFAULT_END = "2026-08-31"

STAGE_ORDER = ("TARGET", "NEAR_MISS", "IN_DOMAIN_NON_TARGET")

FLAT_PRICE_MEAN_RET_THRESHOLD = 1e-3


def _backtest_dir() -> Path:
    d = STUDY_DIR / "backtest_v1"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


# ── 加载 / 面板构建 ───────────────────────────────────────────────

def _load_master() -> tuple[dict[str, str], dict[str, str]]:
    master = pd.read_parquet(etf_signal_master_dir() / "etf_master.parquet")
    name = {str(r["fund_code"]).zfill(6): str(r["fund_name"]) for _, r in master[["fund_code", "fund_name"]].iterrows()}
    bucket = {str(r["fund_code"]).zfill(6): str(r["primary_bucket"]) for _, r in master[["fund_code", "primary_bucket"]].iterrows()}
    return name, bucket


def _build_etf_panels() -> dict[str, pd.DataFrame]:
    """对每只 raw ETF 计算逐日状态面板，返回 {fund_code: DataFrame}。

    与 run_scan(as_of=date) 一致性由测试锁定：daily_state_series 在任意锚点的
    pos/unreliable/state 与 compute_row 逐值相等。
    """
    name_map, bucket_map = _load_master()
    panels: dict[str, pd.DataFrame] = {}
    for path in etf_signal_raw_dir().glob("*.parquet"):
        code = path.stem
        try:
            d = pd.read_parquet(path, columns=["date", "close"])
        except Exception:
            continue
        if "date" not in d.columns or "close" not in d.columns or d.empty:
            continue
        nm = name_map.get(code, "")
        et = calibrate_etf_type(nm or code, bucket_map.get(code, ""))["calibrated_type"]
        states = daily_state_series(d)
        states["fund_code"] = code
        states["fund_name"] = nm
        states["etf_type"] = et
        states["hist_days"] = np.arange(1, len(states) + 1)
        ret = states["close"].pct_change().abs()
        states["flat_price"] = ret.rolling(360, min_periods=2).mean() <= FLAT_PRICE_MEAN_RET_THRESHOLD
        states["flat_price"] = states["flat_price"].fillna(False)
        panels[code] = states
    logger.info("ETF panels built: %d", len(panels))
    return panels


def _trade_dates(panels: dict[str, pd.DataFrame], start: str, end: str) -> list[pd.Timestamp]:
    all_dates: set[pd.Timestamp] = set()
    for p in panels.values():
        all_dates.update(pd.to_datetime(p["date"]))
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    return sorted(d for d in all_dates if s <= d <= e)


# ── 横截面构建 ────────────────────────────────────────────────────

def _build_signal_daily(panels: dict[str, pd.DataFrame], start: str, end: str,
                        watch_pool: set[str]) -> pd.DataFrame:
    """跨每日重建可靠横截面，给每个 (ETF, date) 打 target_stage。"""
    cut120, cut60 = load_frozen_cutpoints()
    _verify_frozen(cut120, cut60)

    rows: list[dict[str, Any]] = []
    for code, pfull in panels.items():
        fp = pfull[pfull["hist_days"] >= MIN_HIST_DAYS].copy()
        if fp.empty:
            continue
        reliable = _reliable_flags(fp)
        hist = fp["hist_days"].to_numpy()
        rel_np = reliable.to_numpy(bool)
        cohort = np.where(~rel_np, "UNRELIABLE", np.where(hist >= V1_BASE_HIST, "BASE", "EXTENSION"))
        ltb_np = fp["long_term_bottom"].to_numpy(bool)
        in_domain_np = rel_np & ltb_np
        p60 = fp["price_pos_60"].to_numpy()
        p120 = fp["price_pos_120"].to_numpy()
        p360 = fp["price_pos_360"].to_numpy()
        bottom = fp["bottom_state"].to_numpy()
        dates = fp["date"].to_numpy()
        names = fp["fund_name"].to_numpy()
        etypes = fp["etf_type"].to_numpy()

        for i in range(len(fp)):
            idm = bool(in_domain_np[i])
            if idm and not _nan(p60[i]) and not _nan(p120[i]):
                stage, near = classify_target(float(p60[i]), float(p120[i]), cut60, cut120)
            else:
                stage, near = "NON_TARGET", None
            rows.append({
                "trade_date": dates[i],
                "fund_code": code,
                "fund_name": names[i],
                "etf_type": etypes[i],
                "industry_cluster": CLUSTER_NAME_BY_CODE.get(code, "OTHER"),
                "cohort": cohort[i],
                "reliable_360": bool(rel_np[i]),
                "long_term_bottom": bool(ltb_np[i]),
                "in_domain": idm,
                "bottom_state": bottom[i],
                "pos60": None if _nan(p60[i]) else round(float(p60[i]), 2),
                "pos120": None if _nan(p120[i]) else round(float(p120[i]), 2),
                "pos360": None if _nan(p360[i]) else round(float(p360[i]), 2),
                "target_stage": stage,
                "near_miss_reason": near,
                "watch_pool": bool(code in watch_pool),
            })

    df = pd.DataFrame(rows)
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    df = df[(df["trade_date"] >= s) & (df["trade_date"] <= e)].copy()
    df = df.sort_values(["fund_code", "trade_date"]).reset_index(drop=True)
    df["prev_target_stage"] = df.groupby("fund_code")["target_stage"].shift(1)
    df = df.sort_values(["trade_date", "fund_code"]).reset_index(drop=True)
    return df


def _reliable_flags(p: pd.DataFrame) -> pd.Series:
    """reliable_360 = full_360_sample ∧ ¬unreliable_360 ∧ ¬flat_price_noise。"""
    return p["price_pos_360"].notna() & ~p["unreliable_360"].fillna(True) & ~p["flat_price"]


# ── Signal Incidence 指标 ─────────────────────────────────────────

def _incidence(daily: pd.DataFrame, trade_days: list[pd.Timestamp]) -> dict[str, Any]:
    target_rows = daily[daily["target_stage"] == "TARGET"]
    per_day = target_rows.groupby("trade_date").size()
    total_days = len(trade_days)
    target_days = int(len(per_day))
    total_signal_days = int(len(target_rows))
    nz = int(target_rows["fund_code"].nunique())

    per_day_full = per_day.reindex(pd.DatetimeIndex(trade_days), fill_value=0)
    vals = per_day_full.to_numpy()

    def _pctile(p):
        if total_days == 0:
            return 0
        return int(round(float(np.percentile(vals, p))))

    return {
        "total_trade_days": total_days,
        "target_days": target_days,
        "zero_target_days": total_days - target_days,
        "target_day_rate": round(target_days / total_days, 4) if total_days else 0.0,
        "total_target_signal_days": total_signal_days,
        "avg_targets_per_day": round(total_signal_days / total_days, 4) if total_days else 0.0,
        "avg_targets_per_target_day": round(total_signal_days / target_days, 4) if target_days else 0.0,
        "max_targets_single_day": int(vals.max()) if len(vals) else 0,
        "unique_target_etfs": nz,
        "target_count_distribution": {
            "P50": _pctile(50), "P75": _pctile(75), "P90": _pctile(90), "P95": _pctile(95), "max": _pctile(100),
        },
    }


# ── 0 TARGET 空窗检验 ─────────────────────────────────────────────

def _zero_target_streaks(daily: pd.DataFrame, trade_days: list[pd.Timestamp]) -> list[dict[str, Any]]:
    target_set = set(daily[daily["target_stage"] == "TARGET"]["trade_date"])
    days = list(trade_days)
    streaks: list[dict[str, Any]] = []
    cur_start: pd.Timestamp | None = None
    prev_target_date: pd.Timestamp | None = None
    for d in days:
        if d in target_set:
            if cur_start is not None:
                next_node = d  # 结束当前空窗
                streaks.append(_streak_rec(cur_start, next_node, days))
                cur_start = None
            prev_target_date = d
        else:
            if cur_start is None:
                cur_start = d
    if cur_start is not None:
        next_target = min((d for d in target_set if d > cur_start), default=None)
        streaks.append(_streak_rec(cur_start, None, days, next_target=next_target))
    return streaks


def _streak_rec(start: pd.Timestamp, end: pd.Timestamp | None, days: list[pd.Timestamp],
                next_target: pd.Timestamp | None = None) -> dict[str, Any]:
    if end is None:
        end = days[-1]
        open_ended = True
        next_target_date = next_target.date() if next_target is not None else None
    else:
        # end = 下一个 TARGET 日；空窗结束于该日前一个交易日
        open_ended = False
        next_target_date = end.date()
        end = days[max(0, _pos(days, end) - 1)]
    tr = _count_trading(days, start, end)
    return {
        "start_date": start.date(), "end_date": end.date(), "trading_days": tr,
        "prev_target_date": None, "next_target_date": next_target_date,
        "open_ended": open_ended,
    }


def _pos(days: list[pd.Timestamp], d: pd.Timestamp) -> int:
    for i, x in enumerate(days):
        if x == d:
            return i
    return 0


def _count_trading(days: list[pd.Timestamp], start: pd.Timestamp, end: pd.Timestamp) -> int:
    if end < start:
        return 0
    return sum(1 for d in days if start <= d <= end)


def _streak_summary(streaks: list[dict[str, Any]], trade_days: list[pd.Timestamp]) -> dict[str, Any]:
    lens = [s["trading_days"] for s in streaks]
    longest_rec = max(streaks, key=lambda s: s["trading_days"]) if streaks else None
    current = next((s for s in streaks if s.get("open_ended")), None)
    return {
        "longest_zero_target_streak": longest_rec["trading_days"] if longest_rec else 0,
        "longest_zero_target_start": (longest_rec["start_date"] if longest_rec else None),
        "longest_zero_target_end": (longest_rec["end_date"] if longest_rec else None),
        "median_zero_target_streak": round(float(np.median(lens)), 2) if lens else 0.0,
        "p90_zero_target_streak": round(float(np.percentile(lens, 90)), 2) if lens else 0.0,
        "current_zero_target_streak_as_of_20260831": (current["trading_days"] if current else 0),
        "n_zero_target_streaks": len(streaks),
    }


# ── Stage events（连续同 stage 合并为一个 episode）──────────────

def _stage_events(daily: pd.DataFrame, stage: str) -> list[dict[str, Any]]:
    """同一 ETF 连续处于某 stage → 一个 event（entry=首次进入日，exit=最后一日）。

    用每只 ETF 的完整交易日序列做邻接判断：两次同 stage 之间只要隔了其他
    交易日（缺停或处于其他 stage），就切开为新的 episode，与 scanner/研究口径一致。
    """
    if stage == "IN_DOMAIN_NON_TARGET":
        mask = daily["in_domain"] & (daily["target_stage"] == "NON_TARGET")
    else:
        mask = daily["target_stage"] == stage
    sub = daily[mask]
    if sub.empty:
        return []
    # 每只 ETF 的完整交易日序列（用于邻接判断，只构建一次）
    fund_dates: dict[str, list[pd.Timestamp]] = {}
    for code, g in daily.groupby("fund_code"):
        fund_dates[code] = g.sort_values("trade_date")["trade_date"].tolist()

    events: list[dict[str, Any]] = []
    for code, g in sub.groupby("fund_code"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        fd = fund_dates.get(code, [])
        dates = g["trade_date"].tolist()
        start_i = 0
        for i in range(1, len(dates)):
            if not _adjacent_in(fd, dates[i - 1], dates[i]):
                events.append(_event_rec(g, start_i, i - 1, stage=stage))
                start_i = i
        events.append(_event_rec(g, start_i, len(dates) - 1, stage=stage, is_current=True))
    return events


def _adjacent_in(fd: list[pd.Timestamp], a: pd.Timestamp, b: pd.Timestamp) -> bool:
    """a 的下一个完整交易日是否就是 b（两次同 stage 之间无其他日/其他 stage）。"""
    if a not in fd:
        return False
    i = fd.index(a)
    return i + 1 < len(fd) and fd[i + 1] == b


def _event_rec(g: pd.DataFrame, start_i: int, end_i: int, stage: str = "",
               is_current: bool = False) -> dict[str, Any]:
    s = g.iloc[start_i]
    e = g.iloc[end_i]
    return {
        "fund_code": s["fund_code"],
        "fund_name": s["fund_name"],
        "industry_cluster": s["industry_cluster"],
        "etf_type": s["etf_type"],
        "event_start": s["trade_date"].date(),
        "event_end": e["trade_date"].date(),
        "duration_days": int(end_i - start_i + 1),
        "entry_pos60": s["pos60"],
        "entry_pos120": s["pos120"],
        "entry_pos360": s["pos360"],
        "entry_bottom_state": s["bottom_state"],
        "stage": stage,
        "near_miss_reason": s["near_miss_reason"],
        "watch_pool": bool(s["watch_pool"]),
        "is_current": is_current,
    }


def _entered_from(daily: pd.DataFrame, code: str, event_start: pd.Timestamp) -> str:
    sub = daily[(daily["fund_code"] == code) & (daily["trade_date"] < event_start)]
    if sub.empty:
        return "NONE"
    last = sub.sort_values("trade_date").iloc[-1]
    if bool(last["in_domain"]):
        return last["target_stage"]
    return "OUT_OF_DOMAIN"


def _enrich_entered_from(daily: pd.DataFrame, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for e in events:
        e["entered_from"] = _entered_from(daily, e["fund_code"], pd.Timestamp(e["event_start"]))
    return events


def _event_time_rep(events: list[dict[str, Any]]) -> dict[str, Any]:
    ev = pd.DataFrame(events)
    total = len(ev)
    ev["year"] = pd.to_datetime(ev["event_start"]).dt.year
    by_year = ev.groupby("year").agg(
        target_events=("fund_code", "count"), unique_etfs=("fund_code", "nunique")).reset_index()
    by_year["event_share"] = (by_year["target_events"] / total).round(4) if total else 0.0

    per_etf = ev.groupby("fund_code").size().sort_values(ascending=False)
    per_etf_df = per_etf.reset_index()
    per_etf_df.columns = ["fund_code", "events"]

    top_year = by_year.sort_values("target_events", ascending=False).iloc[0] if len(by_year) else None

    def _share(top_n):
        return round(float(per_etf.head(top_n).sum() / total), 4) if total else 0.0

    by_cluster = ev.groupby("industry_cluster").agg(
        events=("fund_code", "count"), unique_etfs=("fund_code", "nunique")).reset_index()
    by_cluster["event_share"] = (by_cluster["events"] / total).round(4) if total else 0.0

    return {
        "year": by_year.to_dict("records"),
        "top_year_share": round(float(top_year["target_events"] / total), 4) if (total and top_year is not None) else 0.0,
        "top_year": str(top_year["year"]) if top_year is not None else None,
        "etf": {
            "n_etfs": int(ev["fund_code"].nunique()),
            "top1_etf_contribution": round(float(per_etf.iloc[0] / total), 4) if total and len(per_etf) else 0.0,
            "top3_etf_contribution": _share(3),
            "top10_etf_contribution": _share(10),
            "per_etf": per_etf_df.to_dict("records"),
        },
        "industry_cluster": by_cluster.to_dict("records"),
    }


# ── Forward Odds ──────────────────────────────────────────────────

def _build_close_panel(panels: dict[str, pd.DataFrame]) -> ClosePanel:
    pivots = []
    for code, p in panels.items():
        pivots.append(pd.DataFrame({code: p.set_index(pd.to_datetime(p["date"]))["close"]}))
    pivot = pd.concat(pivots, axis=1).sort_index()
    pivot = pivot[pivot.index.notna()]
    return ClosePanel(pivot)


def _forward_odds(events: list[dict[str, Any]], panel: ClosePanel) -> pd.DataFrame:
    rows = []
    for e in events:
        code = e["fund_code"]
        entry = pd.Timestamp(e["event_start"])
        rets = panel.forward_returns(code, entry, _HORIZONS)
        bench = panel.benchmark_forward(entry, _HORIZONS)
        rec = dict(e)
        for h in _HORIZONS:
            r = rets.get(h)
            rec[f"ret_{h}"] = r
            rec[f"excess_{h}"] = (r - bench.get(h)) if (r is not None and bench.get(h) is not None) else None
            rec[f"censored_{h}"] = (r is None)
        rows.append(rec)
    return pd.DataFrame(rows)


def _agg_forward(sub: pd.DataFrame, h: int) -> dict[str, Any]:
    r = pd.to_numeric(sub[f"ret_{h}"], errors="coerce").dropna()
    x = pd.to_numeric(sub[f"excess_{h}"], errors="coerce").dropna()
    n_cens = int((sub[f"censored_{h}"] == True).sum())

    def _s(vals):
        return round(float(vals.median()), 4) if len(vals) else None

    def _m(vals):
        return round(float(vals.mean()), 4) if len(vals) else None

    def _w(vals):
        return round(float((vals > 0).mean()), 4) if len(vals) else None

    return {
        "n": int(len(r)), "n_censored": n_cens,
        "mean": _m(r), "median": _s(r), "win_rate": _w(r),
        "excess_mean": _m(x), "excess_median": _s(x), "excess_win_rate": _w(x),
        "p25": round(float(r.quantile(0.25)), 4) if len(r) else None,
        "p75": round(float(r.quantile(0.75)), 4) if len(r) else None,
    }


def _stage_forward_odds(events: list[dict[str, Any]], panel: ClosePanel) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for stage in STAGE_ORDER:
        sub_list = [e for e in events if e["stage"] == stage]
        if not sub_list:
            out[stage] = {"n_events": 0, "n_etfs": 0, "horizons": {}}
            continue
        df = _forward_odds(sub_list, panel)
        horizons = {str(h): _agg_forward(df, h) for h in _HORIZONS}
        out[stage] = {
            "n_events": int(len(df)),
            "n_etfs": int(df["fund_code"].nunique()),
            "horizons": horizons,
        }
    return out


# ── NEAR_MISS → TARGET 转化 ──────────────────────────────────────

def _near_miss_conversion(daily: pd.DataFrame, near_events: list[dict[str, Any]]) -> dict[str, Any]:
    per_fund = {code: g.sort_values("trade_date").reset_index(drop=True)
                for code, g in daily.groupby("fund_code")}
    rows: list[dict[str, Any]] = []
    for e in near_events:
        code = e["fund_code"]
        g = per_fund.get(code)
        if g is None:
            continue
        entry = pd.Timestamp(e["event_start"])
        dates = pd.to_datetime(g["trade_date"]).tolist()
        if entry not in dates:
            continue
        b = dates.index(entry)
        after = g.iloc[b + 1:]
        days_to_target = None
        converted = {}
        for h in _FORWARD_INCIDENCE:
            window = after.iloc[:h]
            tgt = window[window["target_stage"] == "TARGET"]
            conv = bool(len(tgt))
            converted[str(h)] = conv
            if conv and days_to_target is None:
                # 最近一个 TARGET 出现次数（首个命中）
                first_t = tgt.iloc[0]
                days_to_target = int((pd.Timestamp(first_t["trade_date"]) - entry).days)
        rows.append({
            "fund_code": code, "fund_name": e["fund_name"],
            "near_miss_reason": e["near_miss_reason"],
            "event_start": e["event_start"],
            **{f"converted_{h}d": converted[str(h)] for h in _FORWARD_INCIDENCE},
            "days_to_target": days_to_target,
        })
    df = pd.DataFrame(rows)
    n = len(df)
    conv: dict[str, Any] = {"near_miss_events": n}
    for h in _FORWARD_INCIDENCE:
        conv[f"conversion_{h}d"] = round(float(df[f"converted_{h}d"].mean()), 4) if n else None
    dts = df["days_to_target"].dropna()
    conv["median_days_to_target"] = round(float(dts.median()), 2) if len(dts) else None
    by_reason: dict[str, Any] = {}
    for reason, g in df.groupby("near_miss_reason"):
        rr: dict[str, Any] = {"n": int(len(g))}
        for h in _FORWARD_INCIDENCE:
            rr[f"conversion_{h}d"] = round(float(g[f"converted_{h}d"].mean()), 4) if len(g) else None
        by_reason[str(reason)] = rr
    conv["by_reason"] = by_reason
    return conv


# ── Verdict ───────────────────────────────────────────────────────

def _verdict(inc: dict[str, Any], streak_sum: dict[str, Any], time_rep: dict[str, Any],
             fwd: dict[str, Any]) -> str:
    rate = inc["target_day_rate"]
    longest = streak_sum["longest_zero_target_streak"]
    total_days = inc["total_trade_days"]
    target_ev = fwd.get("TARGET", {}).get("n_events", 0)
    top_year = time_rep.get("top_year_share", 0.0)
    top1_etf = time_rep.get("etf", {}).get("top1_etf_contribution", 0.0)
    cluster_shares = [c["event_share"] for c in time_rep.get("industry_cluster", [])
                      if c.get("industry_cluster") != "OTHER"]
    top_cluster = max(cluster_shares, default=0.0)

    t_exc = fwd.get("TARGET", {}).get("horizons", {}).get("120", {}).get("excess_median")
    c_exc = fwd.get("IN_DOMAIN_NON_TARGET", {}).get("horizons", {}).get("120", {}).get("excess_median")
    t_ret = fwd.get("TARGET", {}).get("horizons", {}).get("120", {}).get("median")
    delta_pp = None
    if t_exc is not None and c_exc is not None:
        delta_pp = round((t_exc - c_exc) * 100, 2)

    sparse = rate < 0.03 or (longest >= 200) or total_days == 0
    concentrated = top_year >= 0.7 or target_ev < 10 or top1_etf >= 0.5 or top_cluster >= 0.8
    incr = (t_ret is not None and t_ret > 0 and delta_pp is not None and delta_pp > 3.0)

    if sparse:
        return "B_TOO_SPARSE"
    if concentrated:
        return "C_TIME_OR_CLUSTER_DEPENDENT"
    if incr:
        return "A_HEALTHY_LOW_FREQUENCY"
    return "D_NO_INCREMENTAL_ODDS"


# ── 主入口 ────────────────────────────────────────────────────────

def run_backtest_v1(start: str = DEFAULT_START, end: str = DEFAULT_END,
                    study_dir: Path | None = None) -> dict[str, Any]:
    study_dir = study_dir or _backtest_dir()
    study_dir.mkdir(parents=True, exist_ok=True)

    from .current_eval import load_watch_etfs
    watch = set(load_watch_etfs()["fund_code"].tolist())

    spec = load_rule_spec()
    cut120, cut60 = load_frozen_cutpoints()
    _verify_frozen(cut120, cut60)

    logger.info("backtest-v1 building per-ETF daily panels ...")
    panels = _build_etf_panels()
    trade_days = _trade_dates(panels, start, end)
    logger.info("trade days in [%s, %s]: %d", start, end, len(trade_days))

    logger.info("backtest-v1 building daily signal table ...")
    daily = _build_signal_daily(panels, start, end, watch)
    daily.to_parquet(study_dir / "v1_signal_daily.parquet", index=False)

    inc = _incidence(daily, trade_days)
    streaks = _zero_target_streaks(daily, trade_days)
    pd.DataFrame(streaks).to_csv(study_dir / "v1_zero_target_streaks.csv", index=False, encoding="utf-8-sig")
    streak_sum = _streak_summary(streaks, trade_days)

    target_events = _enrich_entered_from(daily, _stage_events(daily, "TARGET"))
    near_events = _enrich_entered_from(daily, _stage_events(daily, "NEAR_MISS"))
    in_domain_events = _enrich_entered_from(daily, _stage_events(daily, "IN_DOMAIN_NON_TARGET"))
    ev_df = pd.DataFrame(target_events)
    ev_df.to_csv(study_dir / "v1_target_events.csv", index=False, encoding="utf-8-sig")

    time_rep = _event_time_rep(target_events)

    panel = _build_close_panel(panels)
    all_events = target_events + near_events + in_domain_events
    fwd = _stage_forward_odds(all_events, panel)

    fwd_rows = []
    for stage in STAGE_ORDER:
        st = fwd.get(stage, {})
        h = st.get("horizons", {})
        fwd_rows.append({
            "stage": stage,
            "n_events": st.get("n_events", 0),
            "n_etfs": st.get("n_etfs", 0),
            "ret20_median": h.get("20", {}).get("median"),
            "ret60_median": h.get("60", {}).get("median"),
            "ret120_median": h.get("120", {}).get("median"),
            "win120": h.get("120", {}).get("win_rate"),
            "excess20_median": h.get("20", {}).get("excess_median"),
            "excess60_median": h.get("60", {}).get("excess_median"),
            "excess120_median": h.get("120", {}).get("excess_median"),
            "n_censored120": h.get("120", {}).get("n_censored"),
        })
    pd.DataFrame(fwd_rows).to_csv(study_dir / "v1_forward_odds.csv", index=False, encoding="utf-8-sig")

    conv = _near_miss_conversion(daily, near_events)
    verdict = _verdict(inc, streak_sum, time_rep, fwd)

    payload = {
        "study": "Repair-Retest V1 Historical Trigger Incidence Backtest",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"start": start, "end": end, "rule_id": spec["rule_id"],
                   "rule_status": spec["status"], "rule_spec_source": "config/research/repair_retest_v1.yaml",
                   "horizons": list(_HORIZONS), "forward_incidence": list(_FORWARD_INCIDENCE)},
        "cut_points": {"price_pos_120": cut120, "price_pos_60": cut60},
        "incidence": inc,
        "zero_target_streaks": streak_sum,
        "target_events": {
            "total": len(target_events),
            "unique_etfs": int(ev_df["fund_code"].nunique()) if len(ev_df) else 0,
            "median_event_duration": round(float(ev_df["duration_days"].median()), 2) if len(ev_df) else None,
            "p90_event_duration": round(float(np.percentile(ev_df["duration_days"], 90)), 2) if len(ev_df) else None,
        },
        "time_representation": time_rep,
        "forward_odds": fwd,
        "forward_comparison": fwd_rows,
        "near_miss_conversion": conv,
        "verdict": verdict,
        "watch_pool_total": len(watch),
    }

    out = study_dir / "v1_incidence_summary.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    logger.info("backtest-v1 done: verdict=%s target_events=%d target_day_rate=%s longest_streak=%d",
                verdict, len(target_events), inc["target_day_rate"], streak_sum["longest_zero_target_streak"])
    return payload


def refresh_v1_signal_daily(end: str | None = None) -> Path:
    """轻量刷新 v1_signal_daily.parquet（每日 run-day 用）。

    只重建「每 ETF 逐日状态面板 → 信号日表」（Lane 2 底部全历史事实），
    跳过 forward-odds / incidence / events 等重研究部分（run_backtest_v1 专属）。
    end 缺省 = 最新 raw 交易日（与 scanner.latest_trade_date 同源）。
    确定性：给定相同 raw 缓存，输出逐值一致（可重复运行，无增量 append）。
    """
    from .scanner import latest_trade_date

    study_dir = _backtest_dir()
    study_dir.mkdir(parents=True, exist_ok=True)

    from .current_eval import load_watch_etfs
    watch = set(load_watch_etfs()["fund_code"].tolist())

    end = end or latest_trade_date()
    logger.info("refresh v1_signal_daily -> %s ...", end)
    panels = _build_etf_panels()
    daily = _build_signal_daily(panels, DEFAULT_START, end, watch)
    out = study_dir / "v1_signal_daily.parquet"
    daily.to_parquet(out, index=False)
    logger.info("v1_signal_daily refreshed: %d rows, dates %s..%s",
                len(daily), daily["trade_date"].min().date(), daily["trade_date"].max().date())
    return out


if __name__ == "__main__":
    p = run_backtest_v1()
    print(json.dumps({
        "verdict": p["verdict"],
        "incidence": {k: p["incidence"][k] for k in
                      ("total_trade_days", "target_days", "target_day_rate",
                       "total_target_signal_days", "unique_target_etfs", "max_targets_single_day")},
        "longest_streak": p["zero_target_streaks"]["longest_zero_target_streak"],
        "target_events": p["target_events"],
    }, ensure_ascii=False, indent=2))
