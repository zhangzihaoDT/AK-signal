"""全市场 Repair-Retest V1 每日扫描器（Application，非研究）。

定位（V1 Freeze → Application 原则）：
  本模块对「通过数据质量门的全市场 reliable ETF」应用冻结的 REPAIR_RETEST_V1
  规则（domain / cut points / target），输出每日三层的市场底部雷达。

  874 只今天的价格数据只能进入规则（算 pos / 判 TARGET），**永不反向改变规则**。
  Q1/Q3 cut points、domain 边界、target 定义、outcome 全部只读自
  config/research/repair_retest_v1.yaml。

  三层输出（各回答一个不同的问题，不混层）：
    Layer A — Market Bottom Map：市场底部有多宽、集中在哪里、今天怎么迁移
    Layer B — Repair-Retest Scanner：哪些 ETF 进入 TARGET / NEAR-1 / NEAR-2，
               以及 DEEP↔RECOVERING 迁移、domain 进出
    Layer C — Historical Odds：in-domain ∪ near-miss ∪ watch 池的结构在冻结的
               历史研究中有没有赔率支持

Cohort（数据驱动，不硬编码）：
    RELIABLE  = full_360_sample ∧ ¬unreliable_360 ∧ ¬flat_price_noise
              （2026-08-31 实测 874 = 原 901 − 27 只真实 flat-price 货币ETF；
                原 29 只 flat_price 中 2 只「现金流ETF」为 taxonomy 误判，修正后不再计入 flat，
                且它们本来就在 901 内，不构成额外加入 universe）
    BASE      = RELIABLE ∧ hist_days ≥ 756（2026-08-31 实测 664）—— V1 原始研究支持范围
    EXTENSION = RELIABLE ∧ 360 ≤ hist_days < 756（2026-08-31 实测 210）—— 规则外推
    flat_price_noise（货币/债券近零波动，价格无底部信号）→ 不进 reliable，单独 audit

字段语义（用户锁定，三个概念不混）：
    long_term_bottom = 市场状态事实（Observation）：pos120≤20 且 pos360≤20（price_map 口径）
    in_domain        = V1 research domain：RELIABLE ∧ long_term_bottom（= 进入 V1 研究域）
    target_stage     = 应用层分类：TARGET / NEAR_MISS / NON_TARGET

TARGET 邻接（单维差一档，frozen cut）：
    TARGET   = pos60 Q1 × pos120 Q3
    NEAR_MISS（near_miss_reason 区分）：
      P120_ONE_BUCKET_AWAY = pos60 Q1 × pos120 Q2（p120 距 Q3 差一档，修复进行中）
      P60_ONE_BUCKET_AWAY  = pos60 Q2 × pos120 Q3（p60 距 Q1 差一档，接近 Q1）
    NON_TARGET = 其余

prev_trade_date（统一语义，用户锁定）：
    采用「统一的 previous market trading date」（as_of 前一交易日，市场日历统一推导），
    不是「该 ETF 自己的最近一个可用交易日」。某 ETF 在统一 prev 日无数据（停牌/缺失）时，
    prev_data_status = missing 显式标记，transition = PREV_MISSING，并保留 prev_actual_trade_date 审计。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.paths import etf_signal_master_dir, etf_signal_raw_dir

from . import STUDY_DIR
from .current_eval import attach_history, load_frozen_cutpoints, load_rule_spec, odds_assessment, tertile
from .episodes import INDUSTRY_CLUSTERS
from .price_map import compute_row
from .universe import calibrate_etf_type

logger = logging.getLogger(__name__)

MIN_HIST_DAYS = 60          # 低于此不进入横截面（与 price_map 一致）
V1_BASE_HIST = 756          # V1 原始研究 universe 的历史下限（与 load_full_etf_universe 一致）

CLUSTER_NAME_BY_CODE = {c: cl for cl, codes in INDUSTRY_CLUSTERS.items() for c in codes}


def _prev_trade_date(d: pd.DataFrame, as_of: pd.Timestamp) -> pd.Timestamp | None:
    """该 ETF 在 as_of 之前的最后一个可用交易日（< as_of 的最大 date）。"""
    dates = d["date"].loc[d["date"] < as_of]
    return dates.max() if len(dates) else None


def _cohort(hist_days: int, reliable: bool) -> str:
    """数据驱动 cohort 标记。reliable=false → 不进任何 cohort（UNRELIABLE audit only）。"""
    if not reliable:
        return "UNRELIABLE"
    return "BASE" if hist_days >= V1_BASE_HIST else "EXTENSION"


def _market_prev_trade_date(raw_dir: Path, as_of: pd.Timestamp) -> pd.Timestamp | None:
    """统一的市场前一交易日：全市场 raw parquet 中 < as_of 的最大 date。

    用户锁定：prev_trade_date 一律采用「统一 previous market trading date」，
    不是各 ETF 自己的最近可用交易日。
    """
    prev: pd.Timestamp | None = None
    for path in raw_dir.glob("*.parquet"):
        try:
            d = pd.read_parquet(path, columns=["date"])
        except Exception:
            continue
        if len(d) == 0:
            continue
        m = d["date"].loc[d["date"] < as_of]
        if len(m):
            mx = m.max()
            if prev is None or mx > prev:
                prev = mx
    return prev


def classify_target(p60: float | None, p120: float | None, cut60: list[float], cut120: list[float]) -> tuple[str, str | None]:
    """由 frozen cut 把 (p60, p120) 分到 (target_stage, near_miss_reason)。

    target_stage ∈ {TARGET, NEAR_MISS, NON_TARGET}
    near_miss_reason ∈ {P120_ONE_BUCKET_AWAY, P60_ONE_BUCKET_AWAY, None}
    只读 frozen cut，永不重算。p 缺失 → NON_TARGET（不冒充）。
    """
    if p60 is None or p120 is None:
        return "NON_TARGET", None
    q60 = tertile(p60, cut60)
    q120 = tertile(p120, cut120)
    if q60 == "Q1" and q120 == "Q3":
        return "TARGET", None
    if q60 == "Q1" and q120 == "Q2":
        return "NEAR_MISS", "P120_ONE_BUCKET_AWAY"
    if q60 == "Q2" and q120 == "Q3":
        return "NEAR_MISS", "P60_ONE_BUCKET_AWAY"
    return "NON_TARGET", None


def transition_kind(prev_state: str, prev_ltb: bool, cur_state: str, cur_ltb: bool) -> str:
    """状态迁移分类（Layer A / B 共用）。

    只对「进入/退出长期底部域」与「DEEP↔RECOVERING」给语义，其余为 NORMAL_*。
    """
    if prev_ltb and cur_ltb:
        if prev_state == "DEEP_BOTTOM" and cur_state == "RECOVERING_FROM_BOTTOM":
            return "DEEP_TO_RECOVERING"
        if prev_state == "RECOVERING_FROM_BOTTOM" and cur_state == "DEEP_BOTTOM":
            return "RECOVERING_TO_DEEP"
        return "STAY_IN_DOMAIN"
    if not prev_ltb and cur_ltb:
        return "ENTER_DOMAIN"
    if prev_ltb and not cur_ltb:
        return "EXIT_DOMAIN"
    return "OUTSIDE_DOMAIN"


def latest_trade_date() -> str:
    """从 raw parquet 推导最新可用交易日（run-day 不传 DATE 时使用）。"""
    latest: pd.Timestamp | None = None
    for path in etf_signal_raw_dir().glob("*.parquet"):
        try:
            d = pd.read_parquet(path, columns=["date"])
        except Exception:
            continue
        if len(d):
            m = d["date"].max()
            if latest is None or m > latest:
                latest = m
    if latest is None:
        raise RuntimeError("raw ETF parquet 目录为空，无法推导最新交易日")
    return str(latest.date())


_FROZEN_120 = [-0.001, 9.88, 15.82, 20.0]
_FROZEN_60 = [-0.001, 14.55, 22.12, 100.0]


def _verify_frozen(cut120: list[float], cut60: list[float]) -> None:
    """防自适应漂移守卫：cut points 必须与 frozen YAML 一致，不得用今日全市场数据重算。"""
    if not (np.allclose(cut120, _FROZEN_120) and np.allclose(cut60, _FROZEN_60)):
        raise RuntimeError(
            f"scan 检测到 frozen V1 cut points 漂移（cut120={cut120}, cut60={cut60}）。"
            "规则只能来自 config/research/repair_retest_v1.yaml；改规则必须新建 V2。")


def run_scan(as_of: str | pd.Timestamp | None = None, study_dir: Path | None = None,
             with_odds: bool = True) -> dict[str, Any]:
    """全市场 Repair-Retest V1 Application 扫描（单锚点，含 prev-trade-date 迁移）。

    as_of 缺省时自动取最新 raw 交易日（run-day 集成用）。
    """
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)
    as_of_ts = pd.Timestamp(as_of or latest_trade_date())
    as_of_str = str(as_of_ts.date())

    spec = load_rule_spec()
    cut120, cut60 = load_frozen_cutpoints()
    domain_120_max = spec["domain"]["price_pos_120_max"]
    domain_360_max = spec["domain"]["price_pos_360_max"]
    _verify_frozen(cut120, cut60)

    master = pd.read_parquet(etf_signal_master_dir() / "etf_master.parquet")
    name_map = {
        str(r["fund_code"]).zfill(6): str(r["fund_name"])
        for _, r in master[["fund_code", "fund_name"]].iterrows()
    }

    # 统一的市场前一交易日（用户锁定：prev_trade_date 不用各 ETF 自己的最近交易日）
    prev_uniform = _market_prev_trade_date(etf_signal_raw_dir(), as_of_ts)
    prev_uniform_str = str(prev_uniform.date()) if prev_uniform is not None else None

    rows: list[dict[str, Any]] = []
    flat_price_codes: list[str] = []
    for path in sorted(etf_signal_raw_dir().glob("*.parquet")):
        code = path.stem
        try:
            d = pd.read_parquet(path, columns=["date", "close"])
        except Exception:
            continue
        if "date" not in d.columns or "close" not in d.columns or d.empty:
            continue
        nm = name_map.get(code, "")
        et = calibrate_etf_type(nm or code, "")["calibrated_type"]
        if len(d[d["date"] <= as_of_ts]) < MIN_HIST_DAYS:
            continue
        cur = compute_row(code, nm, et, d, as_of_ts)

        hist_days = int(cur.get("history_days", 0))
        flat_price = str(cur.get("data_quality_flag", "")) == "flat_price_noise"
        # 货币/债券近零波动：价格无底部信号，排除出 reliable（不进 cohort、不进主表，单独 audit）
        reliable = bool(cur.get("full_360_sample")) and not bool(cur.get("unreliable_360")) and not flat_price
        cohort = _cohort(hist_days, reliable)
        p60, p120, p360 = cur.get("price_pos_60"), cur.get("price_pos_120"), cur.get("price_pos_360")

        # long_term_bottom = 市场状态事实（Observation，price_map 口径）；in_domain = V1 research domain
        ltb = bool(cur.get("long_term_bottom"))
        in_domain = reliable and ltb

        row = {
            "fund_code": code,
            "fund_name": nm,
            "etf_type": et,
            "cohort": cohort,
            "reliable": reliable,
            "hist_days": hist_days,
            "bottom_state": cur.get("bottom_state"),
            "long_term_bottom": ltb,
            "in_domain": in_domain,
            "industry_cluster": CLUSTER_NAME_BY_CODE.get(code, "OTHER"),
            "p60": p60, "p120": p120, "p360": p360,
            "data_quality_flag": cur.get("data_quality_flag", ""),
        }

        # V1 target 邻接（仅 reliable 且 in-domain 才有意义；domain 外一律 NON_TARGET）
        if in_domain:
            row["target_stage"], row["near_miss_reason"] = classify_target(p60, p120, cut60, cut120)
        else:
            row["target_stage"], row["near_miss_reason"] = "NON_TARGET", None

        # 迁移（统一 prev trade date）：prev_trade_date 一律 = 市场前一交易日；
        # 该 ETF 在统一 prev 日无数据（停牌/缺失）→ prev_data_status=missing、transition=PREV_MISSING，
        # prev_actual_trade_date 保留审计（不静默用自己更早日期）
        row["prev_trade_date"] = prev_uniform_str
        row["prev_actual_trade_date"] = None
        if reliable and prev_uniform is not None:
            if (d["date"] == prev_uniform).any():
                prev = compute_row(code, nm, et, d, prev_uniform)
                prev_state = str(prev.get("bottom_state"))
                prev_ltb = bool(prev.get("long_term_bottom"))
                row["transition"] = transition_kind(prev_state, prev_ltb, str(cur.get("bottom_state")), ltb)
                row["prev_data_status"] = "ok"
                row["prev_actual_trade_date"] = prev_uniform_str
            else:
                row["transition"] = "PREV_MISSING"
                row["prev_data_status"] = "missing"
                own_prev = _prev_trade_date(d, as_of_ts)
                row["prev_actual_trade_date"] = str(own_prev.date()) if own_prev is not None else None
        else:
            row["transition"] = "N/A"
            row["prev_data_status"] = "N/A" if not reliable else "not_applicable"

        # Layer C：in-domain ∪ near-miss ∪ 关注池才跑历史赔率（避免全市场全跑）
        # attach_history 返回 {"history": {...}}，解嵌套存内层 stats dict（renderer 读内层）
        if with_odds and row["in_domain"]:
            row["odds"] = attach_history(code)["history"]

        if flat_price:
            flat_price_codes.append(code)
        rows.append(row)

    df = pd.DataFrame(rows)
    # 只保留 reliable（通过数据质量门 ∧ 非 flat_price）进入主表；其余分两桶 audit：
    #   unreliable_df = 数据质量门未过（unreliable_360 / 历史不足）
    #   flat_df       = 货币/债券近零波动（价格无底部信号）—— 与 unreliable 互斥，不重复计数
    reliable_df = df[df["reliable"] == True]
    flat_df = df[df["fund_code"].isin(flat_price_codes)]
    unreliable_df = df[(df["reliable"] == False) & (~df["fund_code"].isin(flat_price_codes))]

    # ── Layer A：市场底部地图（breadth + 集中度 + 迁移）────────────────
    n_base = int((reliable_df["cohort"] == "BASE").sum())
    n_ext = int((reliable_df["cohort"] == "EXTENSION").sum())
    ltb = reliable_df[reliable_df["long_term_bottom"] == True]
    deep = reliable_df[reliable_df["bottom_state"] == "DEEP_BOTTOM"]
    recovering = reliable_df[reliable_df["bottom_state"] == "RECOVERING_FROM_BOTTOM"]
    target = reliable_df[reliable_df["target_stage"] == "TARGET"]
    near_miss = reliable_df[reliable_df["target_stage"] == "NEAR_MISS"]
    near_miss_by_reason = (near_miss["near_miss_reason"].value_counts().to_dict()
                           if len(near_miss) else {})

    transition_counts = reliable_df["transition"].value_counts().to_dict() if len(reliable_df) else {}

    def _cluster_concentration(sel: pd.DataFrame) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, r in sel.iterrows():
            cl = r.get("industry_cluster", "OTHER")
            out[cl] = out.get(cl, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    layer_a = {
        "reliable_total": int(len(reliable_df)),
        "unreliable_total": int(len(unreliable_df)),
        "flat_price_total": int(len(flat_df)),
        "cohort": {"BASE": n_base, "EXTENSION": n_ext},
        "state_counts": reliable_df["bottom_state"].value_counts().to_dict(),
        "long_term_bottom_total": int(len(ltb)),
        "deep_total": int(len(deep)),
        "recovering_total": int(len(recovering)),
        "target_total": int(len(target)),
        "near_miss_total": int(len(near_miss)),
        "near_miss_by_reason": near_miss_by_reason,
        "domain_breadth_ratio": round(len(ltb) / max(len(reliable_df), 1), 4),
        "cluster_concentration": _cluster_concentration(ltb),
        "transition_counts": transition_counts,
        "prev_trade_date": prev_uniform_str,
        "ltb_codes": sorted(ltb["fund_code"].tolist()),
    }

    # ── Layer B：Repair-Retest Scanner（domain 内全部 + 邻接）────────────
    layer_b_rows = reliable_df[reliable_df["in_domain"] == True].to_dict("records")
    layer_b_rows.sort(key=lambda r: (r["target_stage"] != "TARGET", r["target_stage"] != "NEAR_MISS",
                                     -abs(r.get("p120") or 0)))

    # ── Layer C：Historical Odds（in-domain ∪ near-miss ∪ watch 池）─────────
    # in-domain（含 TARGET/NEAR_MISS/域内非 target）已在上方挂 odds；
    # watch 池（selection_universe 主题 ETF）即使不在 domain 也要进赔率表，
    # stage 用 eval_one 得到真实 OUT_OF_DOMAIN / UNRELIABLE（不误判为域内）。
    from .current_eval import eval_one, load_watch_etfs

    layer_c_by_code = {r["fund_code"]: r for r in layer_b_rows if r.get("odds")}
    for _, w in load_watch_etfs().iterrows():
        wc = w["fund_code"]
        if wc in layer_c_by_code:
            continue
        # 不在 in-domain 的关注池标的 → 用 eval_one 得真实 stage（OUT_OF_DOMAIN/UNRELIABLE）
        res = eval_one(wc, w["fund_name"], w["theme"], cut120, cut60,
                       tier=w.get("tier", ""), participation=w.get("participation", "tradeable"),
                       as_of=as_of_str)
        # attach_history 返回 {"history": {...}}，解嵌套存内层 stats dict（renderer 读内层）
        h = attach_history(wc)["history"]
        layer_c_by_code[wc] = {
            "fund_code": wc,
            "fund_name": w["fund_name"],
            "etf_type": "watch",
            "cohort": "WATCH",
            "reliable": res.get("reliable", False),
            "hist_days": None,
            "bottom_state": None,
            "industry_cluster": CLUSTER_NAME_BY_CODE.get(wc, "OTHER"),
            "long_term_bottom": res.get("stage") in ("TARGET", "IN_DOMAIN_NON_TARGET"),
            "in_domain": res.get("stage") in ("TARGET", "IN_DOMAIN_NON_TARGET"),
            "target_stage": res.get("stage"),
            "near_miss_reason": None,
            "p60": res.get("p60"), "p120": res.get("p120"), "p360": res.get("p360"),
            "transition": "N/A",
            "prev_data_status": "N/A",
            "prev_trade_date": prev_uniform_str,
            "prev_actual_trade_date": None,
            "data_quality_flag": res.get("reason", ""),
            "odds": h,
            "evidence_label": h.get("evidence_label", "INSUFFICIENT_HISTORY"),
            "odds_assessment": res.get("odds_assessment") or odds_assessment(res["stage"], h.get("evidence_label", "INSUFFICIENT_HISTORY"), h),
        }
    layer_c = list(layer_c_by_code.values())
    for r in layer_c:
        h = r["odds"]
        r["evidence_label"] = h.get("evidence_label", "INSUFFICIENT_HISTORY")
        # odds_assessment 只认四档 stage；in-domain 的 NEAR_MISS/NON_TARGET → IN_DOMAIN_NON_TARGET
        _s = r["target_stage"]
        if _s in ("NEAR_MISS", "NON_TARGET"):
            _s = "IN_DOMAIN_NON_TARGET"
        r["odds_assessment"] = odds_assessment(_s, r["evidence_label"], h)
    layer_c.sort(key=lambda r: {"strong_observe": 0, "watch_structure": 1, "position_only": 2,
                                "cautious": 3, "unreliable": 4}.get(r["odds_assessment"], 9))

    payload = {
        "study": "Repair-Retest V1 Full-Market Daily Scan (Application)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of_str,
        "rule_id": spec["rule_id"],
        "rule_status": spec["status"],
        "rule_spec_source": "config/research/repair_retest_v1.yaml",
        "cut_points": {"price_pos_120": cut120, "price_pos_60": cut60},
        "domain": {"price_pos_120_max": domain_120_max, "price_pos_360_max": domain_360_max},
        "cohort_definition": {
            "RELIABLE": "full_360_sample ∧ ¬unreliable_360 ∧ ¬flat_price_noise",
            "BASE": f"RELIABLE ∧ hist_days ≥ {V1_BASE_HIST}（V1 原始研究支持范围）",
            "EXTENSION": f"RELIABLE ∧ {MIN_HIST_DAYS} ≤ hist_days < {V1_BASE_HIST}（规则外推）",
        },
        "layer_a_market_bottom_map": layer_a,
        "layer_b_repair_retest_scanner": layer_b_rows,
        "layer_c_historical_odds": layer_c,
        "unreliable_audit": unreliable_df[["fund_code", "fund_name", "hist_days", "data_quality_flag"]].to_dict("records"),
        "flat_price_audit": flat_df[["fund_code", "fund_name", "hist_days", "data_quality_flag"]].to_dict("records"),
    }

    out = study_dir / f"scan_{as_of_str.replace('-', '')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # 扁平 parquet/csv（全 reliable + 关键列，供研究/审计）
    flat_cols = ["fund_code", "fund_name", "etf_type", "cohort", "reliable", "hist_days",
                 "industry_cluster",
                 "bottom_state", "long_term_bottom", "in_domain", "target_stage", "near_miss_reason",
                 "p60", "p120", "p360", "transition", "prev_data_status",
                 "prev_trade_date", "prev_actual_trade_date", "data_quality_flag"]
    flat = reliable_df[flat_cols].copy()
    flat_pq = study_dir / f"scan_{as_of_str.replace('-', '')}.parquet"
    flat_csv = study_dir / f"scan_{as_of_str.replace('-', '')}.csv"
    flat.to_parquet(flat_pq, index=False)
    flat.to_csv(flat_csv, index=False, encoding="utf-8-sig")
    payload["flat_parquet_path"] = str(flat_pq)

    logger.info("scan %s: reliable=%d (BASE=%d/EXT=%d) ltb=%d deep=%d recovering=%d target=%d near_miss=%d",
                as_of_str, layer_a["reliable_total"], n_base, n_ext, layer_a["long_term_bottom_total"],
                layer_a["deep_total"], layer_a["recovering_total"],
                layer_a["target_total"], layer_a["near_miss_total"])
    return payload


if __name__ == "__main__":
    p = run_scan("2026-08-31")
    print(json.dumps(p["layer_a_market_bottom_map"], ensure_ascii=False, indent=2))
