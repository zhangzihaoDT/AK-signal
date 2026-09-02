"""Study 3C · 编排：回放 → 市场指标 → 验证 C1-C5 → 产物 →（PASS 时）冻结 YAML。

数据契约（只消费已有事实层，不重新 Discovery）：
  - outputs/research/etf_bottom/backtest_v1/v1_signal_daily.parquet（每日状态事实）
  - data/etf_signal/signals/watchlist_{date}.parquet（Lane 1，辅助验证，仅近期存在）

产物（STUDY_DIR = outputs/research/trend_transition/）：
  study3c_state_history.parquet      逐日 × 逐 ETF 状态历史
  study3c_market_history.parquet     每日市场 breadth/flow/momentum
  study3c_current_state.parquet      as-of 最新日每 ETF 一行
  study3c_transition_flows.csv       每日 flow
  study3c_etf_type_breakdown.csv     ETF Type 分层
  study3c_validation.json            C1-C5
  study3c_summary.json               汇总（报告唯一事实源）
  study3c_report.html                renderer 产物

PASS gate（§28）：C1 Determinism · C2 No look-ahead · C3 Legal transitions ·
C4 Persistence robust · C5 Historical interpretability。全过 →
config/research/trend_transition_state_v1.yaml（FROZEN_STATE_CLASSIFIER）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import PERSISTENCE_PRIMARY, PERSISTENCE_RAW, PERSISTENCE_ROBUST, STUDY_DIR
from .calendar import MarketCalendar
from .state_classifier import (
    LEGAL_TRANSITIONS,
    STATE_POST_TRANSITION,
    replay_universe_state,
)
from .state_metrics import (
    build_market_history,
    compute_etf_type_breakdown,
    compute_lane1_overlap,
    compute_lane2_overlap,
    compute_transition_flow,
)

logger = logging.getLogger(__name__)

V1_PATH = Path("outputs/research/etf_bottom/backtest_v1/v1_signal_daily.parquet")
WATCHLIST_GLOB = "data/etf_signal/signals/watchlist_{date}.parquet"
SPEC_PATH = Path("config/research/trend_transition_state_v1.yaml")
SPEC_ID = "TREND_TRANSITION_STATE_V1"
FROZEN_STATUS = "FROZEN_STATE_CLASSIFIER"

# C4 方向一致性：各 persistence 的 breadth 方向（与主口径的相关性）
C4_MIN_CORR = 0.7
# C5 历史可解释性阈值：不要求拟合 924，只要求方向性结构
C5_MIN_POST924_ACTIVE = 0.05     # post-924 active_transition_breadth 应显著 > 0


def load_v1(path: Path = V1_PATH) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def load_watchlist(as_of: pd.Timestamp) -> pd.DataFrame | None:
    """读取 Lane 1 watchlist（辅助验证；缺文件 → None，不影响 PASS/FAIL）。"""
    p = Path(WATCHLIST_GLOB.format(date=as_of.strftime("%Y%m%d")))
    if not p.exists():
        return None
    return pd.read_parquet(p)


def run_study3c(persistence: int = PERSISTENCE_PRIMARY) -> dict[str, Any]:
    v1 = load_v1()
    cal = MarketCalendar.from_v1(v1)

    logger.info("replay %d funds ...", v1["fund_code"].nunique())
    hist = replay_universe_state(v1, cal, persistence=persistence)
    flow = compute_transition_flow(hist)
    market = build_market_history(hist, flow)
    as_of = hist["trade_date"].max()

    # current state（最新日） + Lane 2 / Lane 1 联动（只读）
    current = hist[hist["trade_date"] == as_of].copy()
    current = current.sort_values(["transition_state", "fund_code"]).reset_index(drop=True)
    lane2 = compute_lane2_overlap(hist, as_of)
    lane1 = compute_lane1_overlap(hist, load_watchlist(as_of), as_of)
    etf_type_brk = compute_etf_type_breakdown(hist, as_of=as_of)

    # C1-C5 validation
    validation = validate_all(v1, cal, hist, market, persistence)
    payload: dict[str, Any] = {
        "study": "3c",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "persistence": persistence,
        "calendar_start": str(cal.start.date()),
        "calendar_end": str(cal.end.date()),
        "as_of": str(as_of.date()),
        "n_funds": int(v1["fund_code"].nunique()),
        "n_hist_rows": int(len(hist)),
        "state_distribution_current": {
            str(k): int(v) for k, v in current["transition_state"].value_counts().to_dict().items()
        },
        "market": _market_latest(market),
        "etf_type_breakdown": etf_type_brk.to_dict("records"),
        "lane2_overlap": lane2.to_dict("records"),
        "lane1_overlap": lane1,
        "validation": validation,
        "pass_gate": {
            "verdict": _verdict(validation),
            "n_pass": int(sum(1 for c in validation["checks"].values() if c["ok"])),
        },
    }

    _write_products(payload, hist, market, current, flow, etf_type_brk, persistence)
    return payload


def _market_latest(market: pd.DataFrame) -> dict[str, Any]:
    if len(market) == 0:
        return {}
    last = market.iloc[-1]
    out: dict[str, Any] = {}
    for k, v in last.to_dict().items():
        if isinstance(v, (pd.Timestamp, str)) or pd.api.types.is_datetime64_any_dtype(pd.Series([v])):
            out[k] = str(v)
        elif pd.notna(v):
            try:
                out[k] = round(float(v), 4)
            except (TypeError, ValueError):
                out[k] = str(v)
        else:
            out[k] = None
    return out


# ── Validation C1-C5 ───────────────────────────────────────────
def validate_all(v1: pd.DataFrame, cal: MarketCalendar, hist: pd.DataFrame,
                 market: pd.DataFrame, persistence: int) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["C1"] = {"name": "Determinism", "ok": _c1_determinism(v1, cal, persistence)}
    checks["C2"] = {"name": "No Look-ahead", "ok": _c2_no_lookahead(v1, cal, hist, persistence)}
    checks["C3"] = {"name": "Legal Transitions", "ok": _c3_legal(hist)[0],
                    "detail": _c3_legal(hist)[1]}
    c4 = _c4_persistence(v1, cal, persistence)
    checks["C4"] = {"name": "Persistence Robust", "ok": c4[0], "detail": c4[1]}
    c5 = _c5_interpretability(market)
    checks["C5"] = {"name": "Historical Interpretability", "ok": c5[0], "detail": c5[1]}
    return {"checks": checks}


def _verdict(validation: dict[str, Any]) -> str:
    checks = validation["checks"]
    ok = {k: v["ok"] for k, v in checks.items()}
    if all(ok.values()):
        return "PASS — TREND_TRANSITION_STATE_V1_READY"
    if not ok["C1"] or not ok["C2"]:
        return "FAIL — LOOKAHEAD_RISK"
    if not ok["C3"]:
        return "FAIL — TRANSITION_INCONSISTENCY"
    if not ok["C4"]:
        return "FAIL — PERSISTENCE_SENSITIVE"
    if not ok["C5"]:
        return "FAIL — HISTORICAL_MISMATCH"
    return "FAIL — STATE_INSTABILITY"


def _c1_determinism(v1: pd.DataFrame, cal: MarketCalendar, persistence: int) -> bool:
    """同一输入 → 同一状态（重跑两次完全一致）。"""
    cols = ["fund_code", "trade_date", "transition_state", "state_origin",
            "first_exit_date", "days_since_first_exit", "transition_cycle"]
    h1 = replay_universe_state(v1, cal, persistence=persistence)[cols]
    h2 = replay_universe_state(v1, cal, persistence=persistence)[cols]
    return bool(h1.equals(h2))


def _c2_no_lookahead(v1: pd.DataFrame, cal: MarketCalendar, hist: pd.DataFrame,
                     persistence: int) -> bool:
    """截掉 as-of 之后未来全部数据 → 当天状态不变（抽查若干日期）。"""
    asofs = pd.to_datetime(v1["trade_date"].unique())
    step = max(1, len(asofs) // 6)
    probes = asofs[::step][1:]   # 去掉首日（无 prior）
    ok_all = True
    for a in probes:
        sub = v1[v1["trade_date"] <= a]
        h_trunc = replay_universe_state(sub, cal, persistence=persistence)
        a_ts = pd.Timestamp(a)
        full_state = hist[(hist["trade_date"] == a_ts)]
        trunc_state = h_trunc[(h_trunc["trade_date"] == a_ts)]
        full_map = dict(zip(full_state["fund_code"], full_state["transition_state"]))
        trunc_map = dict(zip(trunc_state["fund_code"], trunc_state["transition_state"]))
        if not all(full_map.get(k) == v for k, v in trunc_map.items()):
            ok_all = False
            break
    return ok_all


def _c3_legal(hist: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    """所有 ETF 的状态迁移必须符合 §14 合法图。"""
    rel = hist[hist["reliable_360"]].copy().sort_values(["fund_code", "trade_date"])
    rel["prev_state"] = rel.groupby("fund_code")["transition_state"].shift(1)
    pairs = rel.dropna(subset=["prev_state"])[["prev_state", "transition_state"]]
    # LEFT_CENSORED 初始化为 POST_TRANSITION 属于合法（§15）
    illegal = pairs[~pairs.apply(lambda r: (r["prev_state"], r["transition_state"]) in LEGAL_TRANSITIONS
                                 or (r["transition_state"] == STATE_POST_TRANSITION
                                     and r["prev_state"] == STATE_POST_TRANSITION), axis=1)]
    return (int(len(illegal)) == 0, {"n_pairs": int(len(pairs)), "n_illegal": int(len(illegal))})


def _c4_persistence(v1: pd.DataFrame, cal: MarketCalendar,
                    persistence: int) -> tuple[bool, dict[str, Any]]:
    """RAW/3D/5D 对市场 breadth 方向应基本一致（主口径 3D 为 canonical）。"""
    from .state_metrics import compute_transition_breadth

    series: dict[str, pd.Series] = {}
    for p in (PERSISTENCE_RAW, persistence, PERSISTENCE_ROBUST):
        h = replay_universe_state(v1, cal, persistence=p)
        br = compute_transition_breadth(h)
        series[str(p)] = br["active_transition_breadth"]
    df = pd.DataFrame(series).dropna()
    if len(df) < 2:
        return False, {"n": int(len(df))}
    corr = df.corr()
    main = str(persistence)
    corrs = [round(float(corr.loc[main, str(p)]), 4) for p in (PERSISTENCE_RAW, PERSISTENCE_ROBUST)]
    ok = all(c >= C4_MIN_CORR for c in corrs)
    return ok, {"corr_vs_primary": dict(zip(["raw", "robust"], corrs)), "n_days": int(len(df))}


def _c5_interpretability(market: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    """历史 Market Transition Map 应自然呈现 3A 已发现结构：
    2022-23 高 retest / 低 transition persistence；2024 过渡期 transition 上升；
    924 后 escape 强化（retest 率显著下降、transition 持续）。只要求方向性（不拟合 924）。
    """
    if len(market) < 120:
        return False, {"n_days": int(len(market))}
    m = market.copy()
    m["trade_date"] = pd.to_datetime(m["trade_date"])
    seg = {
        "pre_2024": m[m["trade_date"] < "2024-01-01"],
        "2024": m[(m["trade_date"] >= "2024-01-01") & (m["trade_date"] < "2024-09-24")],
        "post_924": m[m["trade_date"] >= "2024-09-24"],
    }
    summary: dict[str, Any] = {}
    for k, s in seg.items():
        if len(s) == 0:
            summary[k] = {"n": 0}
            continue
        summary[k] = {
            "n": int(len(s)),
            "active_transition_breadth": round(float(s["active_transition_breadth"].mean()), 4),
            "transition_breadth": round(float(s["transition_breadth"].mean()), 4),
            "net_flow_mean": round(float(s["net_transition_flow"].mean()), 4) if "net_transition_flow" in s.columns else None,
            "retest_rate_per_day": round(float(s["new_retest_count"].mean()), 4) if "new_retest_count" in s.columns else None,
            "first_exit_rate_per_day": round(float(s["new_first_exit_count"].mean()), 4) if "new_first_exit_count" in s.columns else None,
        }
    pre24 = summary.get("pre_2024", {})
    y2024 = summary.get("2024", {})
    post = summary.get("post_924", {})

    # (a) 2024 transition 强度 > 2022-23 基线（3A：2024 开始 persistence 上升）
    a_ok = bool(y2024.get("transition_breadth", 0) is not None
                and pre24.get("transition_breadth", 0) is not None
                and y2024["transition_breadth"] > pre24["transition_breadth"])
    # (b) post-924 retest 率显著低于 2022-23（3A：924 后 escape/transition 强化）
    b_ok = bool(post.get("retest_rate_per_day") is not None
                and pre24.get("retest_rate_per_day") is not None
                and post["retest_rate_per_day"] < pre24["retest_rate_per_day"] * 0.5)
    ok = bool(a_ok and b_ok)
    return ok, {"segments": summary, "a_transition_up_in_2024": a_ok, "b_retest_drops_post924": b_ok}


def _write_products(payload: dict[str, Any], hist: pd.DataFrame, market: pd.DataFrame,
                    current: pd.DataFrame, flow: pd.DataFrame,
                    etf_type_brk: pd.DataFrame, persistence: int) -> None:
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    hist.to_parquet(STUDY_DIR / "study3c_state_history.parquet", index=False)
    market.to_parquet(STUDY_DIR / "study3c_market_history.parquet", index=False)
    current.to_parquet(STUDY_DIR / "study3c_current_state.parquet", index=False)
    flow.to_csv(STUDY_DIR / "study3c_transition_flows.csv", index=False)
    etf_type_brk.to_csv(STUDY_DIR / "study3c_etf_type_breakdown.csv", index=False)

    with open(STUDY_DIR / "study3c_validation.json", "w", encoding="utf-8") as f:
        json.dump(payload["validation"], f, ensure_ascii=False, indent=2, default=str)

    with open(STUDY_DIR / "study3c_summary.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # PASS 后冻结 V1 Spec（§30）
    if payload["pass_gate"]["verdict"].startswith("PASS"):
        _write_frozen_spec(payload)


def _write_frozen_spec(payload: dict[str, Any]) -> None:
    import yaml

    spec = {
        "rule_id": SPEC_ID,
        "status": FROZEN_STATUS,
        "frozen_at": payload["generated_at"][:10],
        "persistence": payload["persistence"],
        "states": [
            "UNRELIABLE", "BOTTOM", "FIRST_EXIT", "TRANSITION_EARLY",
            "TRANSITION_ACTIVE", "TRANSITION_ESTABLISHED", "RETEST", "POST_TRANSITION",
        ],
        "age_windows": {
            "first_exit": "0-5",
            "early": "6-20",
            "active": "21-60",
            "established": "61-120",
            "post_transition": "120+",
        },
        "provenance": {
            "lane": 3,
            "study": "3C",
            "source_artifact": "outputs/research/trend_transition/study3c_summary.json",
        },
    }
    SPEC_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SPEC_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(spec, f, allow_unicode=True, sort_keys=False)
    logger.info("wrote frozen spec %s", SPEC_PATH)


def load_frozen_spec() -> dict[str, Any] | None:
    """Application 读冻结 YAML；未冻结/缺失 → None。"""
    import yaml

    if not SPEC_PATH.exists():
        return None
    with open(SPEC_PATH, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    if spec.get("rule_id") != SPEC_ID or spec.get("status") != FROZEN_STATUS:
        return None
    return spec


def run_application(as_of: pd.Timestamp | None = None,
                    persistence: int = PERSISTENCE_PRIMARY) -> dict[str, Any]:
    """Application：读冻结 YAML，输出当日状态表（date-stamped，不写 _latest）。

    as_of 缺省 = 最新 raw 交易日（v1_signal_daily 的最大 trade_date，
    run-day 集成：先 refresh v1_signal_daily 再调用本函数）。
    """
    spec = load_frozen_spec()
    if spec is None:
        raise RuntimeError("TREND_TRANSITION_STATE_V1 not frozen — run `study3c` first (must PASS)")
    v1 = load_v1()
    cal = MarketCalendar.from_v1(v1)
    if as_of is None:
        as_of = pd.Timestamp(v1["trade_date"].max())
    as_of = pd.Timestamp(as_of).normalize()
    sub = v1[v1["trade_date"] <= as_of]
    hist = replay_universe_state(sub, cal, persistence=spec.get("persistence", persistence))
    cur = hist[hist["trade_date"] == as_of].copy()
    if len(cur) == 0:
        raise ValueError(f"no state at {as_of.date()}")
    cur = cur.sort_values(["transition_state", "fund_code"]).reset_index(drop=True)
    # Lane 1 / Lane 2 只读联接（辅助验证）
    wl = load_watchlist(as_of)
    lane1 = compute_lane1_overlap(hist, wl, as_of)
    lane2 = compute_lane2_overlap(hist, as_of)
    return {"spec": spec, "as_of": str(as_of.date()), "state": cur,
            "lane1_overlap": lane1, "lane2_overlap": lane2.to_dict("records")}
