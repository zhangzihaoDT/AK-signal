"""Price Bottom Map — 横截面「低位资产地图」（市场状态研究，非赔率研究）。

锚点语义（锁死）：
  as_of_date 显式传入（默认 2026-08-28 作为正式研究快照），所有窗口严格截断至 <= as_of_date。
  last_trade_date / stale_days 输出审计：若某只 ETF as_of 当日停牌，不悄悄漂移研究锚点。

指标：
  price_pos_N      = (close_as_of - low_N) / (high_N - low_N) * 100   # 0-100，0=窗口最低
  distance_to_low_N = close_as_of / low_N - 1                          # 距窗口低点涨幅

折算（窗口级，命名严谨）：
  possible_corporate_action = 单日 |ret| >= 20%（疑似份额折算/公司行为检测器，不作事实断言）。
  折算落在 N 窗口内 → 仅该窗口 price_pos_N / distance_to_low_N = null，标 unreliable_N。
  60⊂120⊂360：60D 内折算则三窗口全污染；360D-only 只污染 360D。
  360D 污染或 360D 历史不足 → bottom_state = UNRELIABLE（其余窗口值保留）。

bottom_state（互斥 5 状态）+ long_term_bottom（独立 bool）：
  判定顺序：insufficient_360 / unreliable_360 → UNRELIABLE
           pos60<=20 & pos120<=20 & pos360<=20          → DEEP_BOTTOM
           pos60>20  & pos120<=20 & pos360<=20          → RECOVERING_FROM_BOTTOM
           pos60<=20 & pos360>20                        → RECENT_BOTTOM
           其他                                          → NORMAL
  long_term_bottom = reliable_120 & reliable_360 & pos120<=20 & pos360<=20
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.paths import etf_signal_master_dir, etf_signal_raw_dir

from .universe import calibrate_etf_type, is_flat_price
from . import STUDY_DIR

logger = logging.getLogger(__name__)

WINDOWS = (60, 120, 360)
POS_LOW_THRESHOLD = 20.0        # price_pos <= 20 视为 LOW
CA_RET_THRESHOLD = 0.20         # 单日 |ret| >= 20% 疑似份额折算/公司行为


def detect_corp_action(d: pd.DataFrame) -> pd.Series:
    """单日 |ret| >= 20% 标记疑似份额折算/公司行为（审计用，不作事实断言）。"""
    return d["close"].pct_change().abs() >= CA_RET_THRESHOLD


def compute_row(code: str, name: str, etf_type: str, d: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, Any]:
    """计算单只 ETF 在 as_of 锚点下的窗口指标与底部状态。

    d 必须含 date/close 且按 date 排序；所有窗口严格截断至 <= as_of。
    """
    df = d[d["date"] <= as_of].sort_values("date").reset_index(drop=True)
    n_hist = len(df)
    if n_hist == 0:
        return {"fund_code": code, "fund_name": name, "etf_type": etf_type,
                "as_of_date": as_of.date(), "last_trade_date": None, "stale_days": None,
                "history_days": 0, "bottom_state": "UNRELIABLE", "long_term_bottom": False}

    close = df["close"]
    last_trade_date = df["date"].max()
    stale_days = int((as_of - last_trade_date).days) if last_trade_date < as_of else 0
    ca = detect_corp_action(df)
    # 折算相对窗口起点偏移：窗口取最近 N 行，折算落在其中则污染
    ca_flags = ca.to_numpy(bool)  # index: 0..n-1

    row: dict[str, Any] = {
        "fund_code": code, "fund_name": name, "etf_type": etf_type,
        "as_of_date": as_of.date(), "last_trade_date": last_trade_date.date(), "stale_days": stale_days,
        "history_days": int(n_hist),
        "close_as_of": float(close.iloc[-1]),
    }
    # 货币/债券近零波动，P 分位无意义（与 Study 1 口径一致）→ 不参与底部状态。
    # 防御 guardrail：即使 taxonomy 漏判（如场内货币名不带动「货币」关键词），
    # is_flat_price 波动率信号也将其判为近零波动，排除出底部状态（flat_price_noise）。
    if etf_type in ("money", "bond") or is_flat_price(df):
        for w in WINDOWS:
            row[f"full_{w}_sample"] = n_hist >= w
            row[f"low_{w}"] = None
            row[f"high_{w}"] = None
            row[f"price_pos_{w}"] = None
            row[f"distance_to_low_{w}"] = None
            row[f"unreliable_{w}"] = False
        row["bottom_state"] = "NORMAL"
        row["long_term_bottom"] = False
        row["data_quality_flag"] = "flat_price_noise"
        return row
    full_flags: dict[int, bool] = {}
    for w in WINDOWS:
        full = n_hist >= w
        full_flags[w] = full
        row[f"full_{w}_sample"] = full
        if not full:
            row[f"low_{w}"] = None
            row[f"high_{w}"] = None
            row[f"price_pos_{w}"] = None
            row[f"distance_to_low_{w}"] = None
            row[f"unreliable_{w}"] = True
            continue
        tail = close.iloc[-w:]
        lo, hi = float(tail.min()), float(tail.max())
        cur = float(close.iloc[-1])
        pos = (cur - lo) / (hi - lo) * 100.0 if hi > lo else 50.0
        # 折算污染：窗口内（最近 w 行，含首行 pct_change）是否有疑似公司行为
        # ca_flags[k] 标记第 k 日相对 k-1 的跳变；窗口 [n_hist-w, n_hist) 含 k>n_hist-w
        window_ca = bool(ca_flags[n_hist - w:].any()) if w > 1 else False
        row[f"low_{w}"] = lo
        row[f"high_{w}"] = hi
        row[f"price_pos_{w}"] = None if window_ca else round(pos, 2)
        row[f"distance_to_low_{w}"] = None if window_ca else round(cur / lo - 1.0, 4)
        row[f"unreliable_{w}"] = bool(window_ca)

    bottom_state, long_term_bottom = classify_state(row)
    row["bottom_state"] = bottom_state
    row["long_term_bottom"] = long_term_bottom
    row["data_quality_flag"] = "possible_corporate_action" if (row.get("unreliable_60") or row.get("unreliable_120") or row.get("unreliable_360")) else ""
    return row


def classify_state(row: dict[str, Any]) -> tuple[str, bool]:
    """由窗口指标推出 bottom_state + long_term_bottom（含折算/历史不足处理）。"""
    p60, p120, p360 = row.get("price_pos_60"), row.get("price_pos_120"), row.get("price_pos_360")
    u60, u120, u360 = row.get("unreliable_60"), row.get("unreliable_120"), row.get("unreliable_360")
    f360 = row.get("full_360_sample")

    # 360D 不可用：历史不足或折算污染 → 整体 UNRELIABLE
    if not f360 or u360 or p360 is None:
        return "UNRELIABLE", False

    lo60 = p60 <= POS_LOW_THRESHOLD
    lo120 = p120 <= POS_LOW_THRESHOLD
    lo360 = p360 <= POS_LOW_THRESHOLD

    # 60/120 窗口如被污染，无法参与判定 → 视为不满足（保守，不造假低位）
    lo60 = lo60 and not u60
    lo120 = lo120 and not u120

    if lo60 and lo120 and lo360:
        state = "DEEP_BOTTOM"
    elif (not lo60) and lo120 and lo360:
        state = "RECOVERING_FROM_BOTTOM"
    elif lo60 and (not lo360):
        state = "RECENT_BOTTOM"
    else:
        state = "NORMAL"

    long_term = (not u120) and (not u360) and (p120 is not None) and (p360 is not None) \
        and p120 <= POS_LOW_THRESHOLD and p360 <= POS_LOW_THRESHOLD
    return state, bool(long_term)


def load_master_names() -> pd.DataFrame:
    master = pd.read_parquet(etf_signal_master_dir() / "etf_master.parquet")
    return master[["fund_code", "fund_name", "primary_bucket"]].copy()


def build_price_map(as_of: date | str = "2026-08-28") -> pd.DataFrame:
    """构建全市场横截面低位地图（主产物）。"""
    as_of_ts = pd.Timestamp(as_of)
    names = load_master_names()
    name_map = {str(r["fund_code"]).zfill(6): (r["fund_name"], r["primary_bucket"]) for _, r in names.iterrows()}

    rows: list[dict[str, Any]] = []
    audit_short: list[dict[str, Any]] = []
    for path in etf_signal_raw_dir().glob("*.parquet"):
        code = path.stem
        try:
            d = pd.read_parquet(path, columns=["date", "close"])
        except Exception as e:
            logger.warning("unreadable raw %s: %s", code, e)
            continue
        if "date" not in d.columns or "close" not in d.columns or d.empty:
            continue
        nm, bucket = name_map.get(code, ("", ""))
        et = calibrate_etf_type(nm or code, "")["calibrated_type"]
        if len(d[d["date"] <= as_of_ts]) < 60:
            audit_short.append({"fund_code": code, "fund_name": nm, "history_days": int(len(d))})
            continue
        rows.append(compute_row(code, nm, et, d, as_of_ts))

    df = pd.DataFrame(rows)
    df = df.sort_values(["bottom_state", "price_pos_360"], na_position="last").reset_index(drop=True)
    df["_audit_short_history"] = None  # 占位：短历史单独 audit，不混入主表
    return df


def write_products(df: pd.DataFrame, as_of: str, study_dir: Path | None = None) -> dict[str, Path]:
    """落盘 CSV / parquet / JSON 结构化产物。"""
    study_dir = study_dir or STUDY_DIR
    study_dir.mkdir(parents=True, exist_ok=True)
    csv_path = study_dir / f"price_map_{as_of.replace('-', '')}.csv"
    pq_path = study_dir / f"price_map_{as_of.replace('-', '')}.parquet"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_parquet(pq_path, index=False)

    long_term = df[df["long_term_bottom"] == True]
    payload = {
        "study": "Price Bottom Map (market state snapshot)",
        "as_of_date": as_of,
        "n_etfs": int(len(df)),
        "state_counts": df["bottom_state"].value_counts().to_dict(),
        "long_term_bottom_total": int(len(long_term)),
        "long_term_bottom_still": int((long_term["bottom_state"] == "DEEP_BOTTOM").sum()),
        "long_term_bottom_recovering": int((long_term["bottom_state"] == "RECOVERING_FROM_BOTTOM").sum()),
        "corp_action_flags": {
            "n_60_unreliable": int(df["unreliable_60"].sum()),
            "n_120_unreliable": int(df["unreliable_120"].sum()),
            "n_360_unreliable": int(df["unreliable_360"].sum()),
            "n_overall_unreliable": int((df["bottom_state"] == "UNRELIABLE").sum()),
        },
        "csv": str(csv_path), "parquet": str(pq_path),
    }
    json_path = study_dir / f"price_map_{as_of.replace('-', '')}.json"
    import json
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv": csv_path, "parquet": pq_path, "json": json_path}
