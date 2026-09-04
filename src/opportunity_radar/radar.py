"""
Opportunity Radar — 引擎（纯算法，只读已落盘事实，不联网不重算）

输入 DataFrame 与 Selection/Layer① 同源：
  rotation_df    Layer① rotation_{date}.parquet（全市场横截面 RPS/流动性/数据质量）
  account_df     account_candidates_{date}.parquet（账户宇宙 + trend_state）
  master_df      etf_master.parquet（amount / exposure 分类 / 产品身份）
  lane_df        three_lane_{date}.parquet（Lane2 可靠性+位置 / Lane3 生命周期）

机会判定（Opportunity Gate，V1，无综合评分）：
  no_theme_mapping ∧ lane2_reliable_360 != False ∧ trend_state ∈ {BUY_CANDIDATE, STRONG_WATCH}
  [+ amount >= min_amount（可选流动性门，CLI 默认透传 Selection 的 min_amount）]

classification（在通过 Gate 的候选上判定，POSSIBLE_MAPPING_GAP 优先）：
  POSSIBLE_MAPPING_GAP   已有结构化证据（fixed_pool / exposure_label）指向已注册 Theme
  NEW_THEME_CANDIDATE    无结构化证据 → 值得研究的新方向候选

本模块不改任何 Selection 状态；build_radar 无副作用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import mapping as radar_mapping

logger = logging.getLogger("opportunity_radar")

# Layer① trend_state 活跃集（与 Selection ③C 展示口径一致；BUY_CANDIDATE / STRONG_WATCH）
ACTIVE_TREND_STATES = {"BUY_CANDIDATE", "STRONG_WATCH"}

# 展示排序：trend_state 优先级（BUY_CANDIDATE 高于 STRONG_WATCH）
_TREND_RANK = {"BUY_CANDIDATE": 0, "STRONG_WATCH": 1}

CLASSIFICATION_GAP = "POSSIBLE_MAPPING_GAP"
CLASSIFICATION_NEW = "NEW_THEME_CANDIDATE"


@dataclass
class OpportunityRow:
    fund_code: str
    fund_name: str
    classification: str
    rps15: float | None = None
    rps20: float | None = None
    rps1: float | None = None
    delta_rps15: float | None = None
    trend_state: str | None = None
    amount: float | None = None
    lane2_reliable_360: bool | None = None
    lane2_long_term_bottom: bool | None = None
    lane2_bottom_state: str | None = None
    lane2_target_stage: str | None = None
    lane2_pos120: float | None = None
    lane3_transition_state: str | None = None
    lane3_days_since_first_exit: float | None = None
    mapping_status: str = "NO_THEME"
    reason_codes: list[str] = field(default_factory=list)
    mapping_gap_evidence: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_code": self.fund_code,
            "fund_name": self.fund_name,
            "classification": self.classification,
            "rps15": _fnum(self.rps15),
            "rps20": _fnum(self.rps20),
            "rps1": _fnum(self.rps1),
            "delta_rps15": _fnum(self.delta_rps15),
            "trend_state": self.trend_state,
            "amount": _fnum(self.amount),
            "lane2_reliable_360": self.lane2_reliable_360,
            "lane2_long_term_bottom": self.lane2_long_term_bottom,
            "lane2_bottom_state": self.lane2_bottom_state,
            "lane2_target_stage": self.lane2_target_stage,
            "lane2_pos120": _fnum(self.lane2_pos120),
            "lane3_transition_state": self.lane3_transition_state,
            "lane3_days_since_first_exit": _fnum(self.lane3_days_since_first_exit),
            "mapping_status": self.mapping_status,
            "reason_codes": self.reason_codes,
            "mapping_gap_evidence": self.mapping_gap_evidence,
        }


def _fnum(v: Any) -> float | None:
    """float 规范化：None/NaN → None，否则 round 1dp。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return round(f, 1)


def _col(df: pd.DataFrame, *names: str) -> str | None:
    """返回 df 中首个存在的列名。"""
    for n in names:
        if n in df.columns:
            return n
    return None


def _row_value(row: pd.Series, col: str | None, default: Any = None) -> Any:
    if col is None:
        return default
    v = row.get(col)
    if v is None:
        return default
    try:
        if pd.isna(v):
            return default
    except (TypeError, ValueError):
        pass
    return v


def _as_bool(v: Any) -> bool | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes"}
    return bool(v)


def _lane_row_to_dict(row: pd.Series | None) -> dict[str, Any]:
    """把 three_lane 行的 Lane2/Lane3 字段抽成 dict（缺列/NaN 容忍）。"""
    if row is None:
        return {}
    out: dict[str, Any] = {}
    for src, key in (
        ("lane2_reliable_360", "lane2_reliable_360"),
        ("lane2_long_term_bottom", "lane2_long_term_bottom"),
        ("lane2_bottom_state", "lane2_bottom_state"),
        ("lane2_target_stage", "lane2_target_stage"),
        ("lane2_pos120", "lane2_pos120"),
        ("lane3_transition_state", "lane3_transition_state"),
        ("lane3_days_since_first_exit", "lane3_days_since_first_exit"),
    ):
        if src in row.index:
            out[key] = row.get(src)
    return out


def _build_lane_index(lane_df: pd.DataFrame | None) -> dict[str, dict[str, Any]]:
    if lane_df is None or lane_df.empty or _col(lane_df, "fund_code") is None:
        return {}
    idx: dict[str, dict[str, Any]] = {}
    code_col = _col(lane_df, "fund_code")
    for _, row in lane_df.iterrows():
        idx[str(row.get(code_col))] = _lane_row_to_dict(row)
    return idx


def _master_row_to_dict(master_df: pd.DataFrame | None, code: str) -> dict[str, Any] | None:
    if master_df is None or master_df.empty or _col(master_df, "fund_code") is None:
        return None
    code_col = _col(master_df, "fund_code")
    hit = master_df[master_df[code_col].astype(str) == str(code)]
    if hit.empty:
        return None
    r = hit.iloc[0]
    out: dict[str, Any] = {}
    for k in ("exposure_type", "exposure_name", "exposure_tags", "asset_bucket", "primary_bucket"):
        if k in r.index:
            out[k] = r.get(k)
    return out


def _researchable_base(
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """核心 Universe：全市场 ETF ∩ 账户宇宙（有 trend_state 事实）∩ 指标有效 ∩ data_quality 可用。

    master amount（每日成交额）left-join 到 base，供流动性门使用。
    返回行带 fund_code / fund_name / rps* / trend_state / amount（若有）。
    """
    if rotation_df.empty or _col(rotation_df, "fund_code") is None:
        return pd.DataFrame()
    rot = rotation_df.copy()
    code_col = _col(rot, "fund_code")
    rot["_code"] = rot[code_col].astype(str)

    # 账户宇宙：account_candidates 提供 trend_state（同一 code 每日期一行）
    use_account = not account_df.empty and _col(account_df, "fund_code") is not None
    if use_account:
        acc = account_df.copy()
        acc["_code"] = acc[_col(acc, "fund_code")].astype(str)
        trend_col = _col(acc, "trend_state")
        keep = ["_code"]
        if trend_col:
            keep.append(trend_col)
        acc = acc[keep].drop_duplicates(subset=["_code"], keep="last")
        rot = rot.merge(acc, on="_code", how="inner")
    else:
        rot["trend_state"] = None

    # 指标有效 + 数据质量可用（corporate_action / 异常不进入横截面判定）
    rps_col = _col(rot, "rps15", "rps_15")
    if rps_col is not None:
        rot = rot[rot[rps_col].notna()].copy()
    dq_col = _col(rot, "data_quality_flag")
    if dq_col is not None:
        ok = rot[dq_col].isna() | (rot[dq_col].astype(str).str.strip() == "")
        rot = rot[ok].copy()

    # amount：master（若 rotation 自带 amount 优先，否则 master left-join）
    if master_df is not None and not master_df.empty and _col(master_df, "fund_code") is not None:
        m = master_df.copy()
        mcode = _col(m, "fund_code")
        m["_code"] = m[mcode].astype(str)
        mamount = _col(m, "amount")
        if mamount is not None and "amount" not in rot.columns:
            m = m[["_code", mamount]].rename(columns={mamount: "amount"})
            rot = rot.merge(m, on="_code", how="left")
    return rot.reset_index(drop=True)


def build_radar(
    *,
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame | None = None,
    lane_df: pd.DataFrame | None = None,
    min_amount: float | None = None,
    fixed_pool_map: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """构建 Uncovered Opportunity Radar payload（纯函数，无副作用、不联网）。

    Args:
        rotation_df: Layer① rotation parquet（全市场横截面 RPS 等）
        account_df:  account_candidates parquet（账户宇宙 + trend_state）
        master_df:   etf_master parquet（amount / exposure 分类；可选）
        lane_df:     three_lane parquet（Lane2/Lane3 事实；可选 → lane-less）
        min_amount:  可选流动性门槛；None = 不做流动性过滤（amount 仍透传）
        fixed_pool_map: selection_universe 的 fund_code→[theme keys]（POSSIBLE_MAPPING_GAP 证据）

    Returns:
        payload dict（trade_date 由 CLI 补入 meta；此处不含日期以保证纯函数可测）
    """
    base = _researchable_base(rotation_df, account_df, master_df)
    if base.empty:
        return _empty_payload()

    code_col = _col(base, "fund_code")
    name_col = _col(base, "fund_name", "name")
    rps_col = _col(base, "rps15", "rps_15")
    rps20_col = _col(base, "rps20")
    rps1_col = _col(base, "rps1")
    dcol = _col(base, "delta_rps15", "delta_rps")
    tr_col = _col(base, "trend_state")
    amount_col = _col(base, "amount")

    lane_index = _build_lane_index(lane_df)
    fixed_pool_map = fixed_pool_map or radar_mapping.fixed_pool_theme_map()

    opportunities: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    mapped_count = 0
    unmapped_count = 0

    for _, row in base.iterrows():
        code = str(row.get(code_col))
        name = str(_row_value(row, name_col, "") or "")
        theme = radar_mapping.theme_key(name)

        rps15 = _row_value(row, rps_col)
        lane = lane_index.get(code, {})
        lane2_rel = _as_bool(lane.get("lane2_reliable_360"))
        trend_state = str(_row_value(row, tr_col, "") or "") or None
        amount = _fnum(_row_value(row, amount_col)) if amount_col is not None else None

        common: dict[str, Any] = {
            "fund_code": code,
            "fund_name": name,
            "rps15": _fnum(rps15),
            "rps20": _fnum(_row_value(row, rps20_col)),
            "rps1": _fnum(_row_value(row, rps1_col)),
            "delta_rps15": _fnum(_row_value(row, dcol)),
            "trend_state": trend_state,
            "amount": amount,
            "lane2_reliable_360": lane2_rel,
            "lane2_long_term_bottom": _as_bool(lane.get("lane2_long_term_bottom")),
            "lane2_bottom_state": lane.get("lane2_bottom_state"),
            "lane2_target_stage": lane.get("lane2_target_stage"),
            "lane2_pos120": _fnum(lane.get("lane2_pos120")),
            "lane3_transition_state": lane.get("lane3_transition_state"),
            "lane3_days_since_first_exit": _fnum(lane.get("lane3_days_since_first_exit")),
            "mapping_status": "MAPPED" if theme else "NO_THEME",
        }

        # 已注册 Theme 内 → 不进 Radar（Case 1）
        if theme:
            mapped_count += 1
            continue
        unmapped_count += 1

        # ── Opportunity Gate ─────────────────────────────────────────
        # no_theme_mapping ∧ lane2_reliable != False ∧ trend 活跃
        # (+ amount >= min_amount，CLI 透传 Selection 流动性门)
        reasons: list[str] = []
        if trend_state not in ACTIVE_TREND_STATES:
            reasons.append("trend_not_active")
        if lane2_rel is False:  # noqa: E712 显式 False 才算不可靠（lane-less/None 放行）
            reasons.append("lane2_unreliable")
        if min_amount is not None and (amount is None or amount < min_amount):
            reasons.append("low_liquidity")

        if not reasons:
            # classification：POSSIBLE_MAPPING_GAP 优先（有结构化证据 → 疑似已有 Theme 覆盖漏洞）
            master_row = _master_row_to_dict(master_df, code)
            gap_evidence = radar_mapping.mapping_gap_evidence(
                master_row, fixed_pool_themes=fixed_pool_map.get(code))
            if gap_evidence:
                classification = CLASSIFICATION_GAP
                common["mapping_gap_evidence"] = gap_evidence
                common["reason_codes"] = [e["evidence"] for e in gap_evidence]
            else:
                classification = CLASSIFICATION_NEW
                common["reason_codes"] = ["uncovered_by_registry"]
            common["classification"] = classification
            opportunities.append(common)
        else:
            common["classification"] = "REJECTED"
            common["reason_codes"] = reasons
            rejected.append(common)

    # 排序（仅展示顺序，不代表推荐）：trend_state 优先级 → RPS15 desc → amount desc
    def _sort_key(r: dict[str, Any]) -> tuple[Any, ...]:
        return (
            _TREND_RANK.get(r.get("trend_state") or "", 99),
            -(r.get("rps15") if r.get("rps15") is not None else -1e18),
            -(r.get("amount") if r.get("amount") is not None else -1e18),
        )

    opportunities.sort(key=_sort_key)
    rejected.sort(key=lambda r: -(r.get("rps15") if r.get("rps15") is not None else -1e18))

    full_market = len(base)
    gap_count = sum(1 for o in opportunities if o["classification"] == CLASSIFICATION_GAP)
    return {
        "role": "opportunity_radar",
        "rule_version": "v1",
        "summary": {
            "full_market_count": full_market,          # 研究able 账户宇宙 ∩ 指标有效
            "mapped_count": mapped_count,              # 能映射到已注册 Theme
            "unmapped_count": unmapped_count,
            "opportunity_count": len(opportunities),
            "mapping_gap_count": gap_count,
            "new_theme_count": len(opportunities) - gap_count,
            "rejected_count": len(rejected),
        },
        "opportunities": opportunities,
        "rejected": rejected,
    }


def _empty_payload() -> dict[str, Any]:
    return {
        "role": "opportunity_radar",
        "rule_version": "v1",
        "summary": {
            "full_market_count": 0,
            "mapped_count": 0,
            "unmapped_count": 0,
            "opportunity_count": 0,
            "mapping_gap_count": 0,
            "new_theme_count": 0,
            "rejected_count": 0,
        },
        "opportunities": [],
        "rejected": [],
    }
