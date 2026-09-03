"""
Layer ③ — 多主题交易标的筛选与表达方式选择（Tradable Selection, v0.4.3）

定位：执行对象压缩层，不是又一层强弱排名。
职责：把 Layer ①（ETF 轮动）与 Layer ②（多主题行业确认）的结论，压缩成
      「这个已确认主题，应当由哪只 ETF、哪类股票来交易」。

多主题框架：bucket（Core/Quality/Tactical，为什么持有） → theme（市场方向）→ 候选资产。
  theme 与 industries / etf_keywords 来自 config/theme_registry.yaml（单一事实源）。

核心输出：候选资产对象（结构化 dict/JSON），HTML 报告只是它的可视化。

流程：
  1. 主题确认     按 theme 的 industries 聚合 Layer② confirmation，判定每个主题是否确认
  2. ETF 候选     动态从 Layer① rotation 全市场按 theme.etf_keywords 选（趋势门控 + 流动性 + 排序 + 去重）
  3. 个股候选     从 universe.yaml（bucket → theme → tier）读取固定观察池
  4. 表达决策     基于上涨结构（参与率 / HHI / Top3）选 ETF vs 个股
  5. 构建候选对象  每主题输出 core_etf / sub_industry_etf / leaders / high_beta / equipment
  6. bucket 聚合  汇总 Core / Quality / Tactical 三个组合意图

Layer 4 边界：本层只回答「买什么」，不回答「买多少 / 何时买 / 何时卖」。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from src.common import themes as themes_cfg
from src.common.asset_state import (
    LEVEL_NORMAL, LEVEL_STRONG, LEVEL_UNKNOWN, LEVEL_WEAK,
    TECH_DIM_MOMENTUM, TECH_DIM_RELATIVE_STRENGTH, TECH_DIM_TREND, TECH_DIMS,
    compose_blocking_flags, compose_data_quality_flags, tech_diag_from_json,
)
from src.selection import four_stage

logger = logging.getLogger("selection")

# 表达方式
EXPRESSION_LABELS = {
    "WATCHLIST_ONLY": "仅观察（行业未确认）",
    "ETF_PRIORITY": "优先 ETF（广泛上涨）",
    "LEADER_PRIORITY": "优先龙头个股（龙头主导）",
    "ETF_CORE_PLUS_LEADER": "ETF 核心 + 龙头卫星（扩散形成）",
    "STOCK_FALLBACK": "转龙头个股（ETF 无合格产品）",
    "ETF_FALLBACK": "转 ETF（个股无合格）",
    "NO_EXECUTABLE": "无可交易标的",
}

# 表达可执行性状态（observability，非决策）
EXPRESSION_STATUS_NORMAL = "NORMAL"
EXPRESSION_STATUS_DEGRADED = "DEGRADED"

# 个股 role 标记
ROLE_LABELS = {
    "CORE_ETF": "核心 ETF",
    "SUB_INDUSTRY_ETF": "细分行业 ETF",
    "LEADER": "行业龙头",
    "HIGH_BETA": "高弹性标的",
    "UPSTREAM": "设备与上游",
}

# 趋势门控：允许进入候选的 ETF 状态（来自统一 Strategy Specification config/strategy_spec.yaml etf_selection）
def _indicator_gates() -> tuple[set[str], set[str], float, set[str], set[str]]:
    from src.common.spec.loaders import load_etf_selection_spec, load_stock_selection_spec
    es = load_etf_selection_spec()
    ss = load_stock_selection_spec()
    return (set(es.allowed_trend_states), set(es.watch_allowed_trend_states),
            float(ss.qualified_score),
            set(ss.allowed_trend_states), set(ss.theme_confirm_states))


(ETF_TREND_GATES, ETF_WATCH_GATES,
 STOCK_QUALIFIED_SCORE, STOCK_ALLOWED_TREND_STATES, THEME_CONFIRM_STATES) = _indicator_gates()


def _etf_min_amount() -> float:
    """ETF 流动性门槛（Policy，config/strategy_spec.yaml etf_selection.min_amount）。

    单一来源：只允许通过 spec loader 读取，禁止模块内硬编码覆盖（曾出现
    `ETF_MIN_AMOUNT = 50_000_000` 覆盖 config 值的 config drift 缺陷）。
    """
    from src.common.spec.loaders import load_etf_selection_spec
    return float(load_etf_selection_spec().min_amount)


ETF_MIN_AMOUNT = _etf_min_amount()
# 观察池（弱势市场兜底）：额外纳入 WATCH，仅作观察候选，recommended=False
# 对外暴露的趋势状态标签：OUT_OF_SCOPE 语义易误读为「不属于主题」，实为「未达趋势门」
ETF_TREND_STATUS_LABELS = {
    "BUY_CANDIDATE": "BUY_CANDIDATE",
    "STRONG_WATCH": "STRONG_WATCH",
    "WATCH": "WATCH",
    "OUT_OF_SCOPE": "BELOW_TREND_GATE",
}

# 个股三层状态：WATCH（固定池内不达标）→ QUALIFIED（趋势合格，行业未确认）→ RECOMMENDED（行业确认+趋势确认）
STOCK_STATE_WATCH = "WATCH"
STOCK_STATE_QUALIFIED = "QUALIFIED"
STOCK_STATE_RECOMMENDED = "RECOMMENDED"

# tier 参与语义（config/selection_universe.yaml tier 级 participation 字段）：
#   tradeable（默认）  正常参与状态机/排名/信号/候选
#   monitor_only      研究迁移 Tier：只进入 stock-metrics 与「核心资产监控」，
#                     不参与主题内排名与四段信号，不因主题确认获得候选资格
PARTICIPATION_TRADEABLE = "tradeable"
PARTICIPATION_MONITOR_ONLY = "monitor_only"
# 个股趋势合格门槛（来自 config/strategy_spec.yaml stock_selection.trend.qualified_score）
# 主题确认门槛（Layer③ 门控 = stock_selection.theme_confirm_states；与 Layer② 生成
# strength_level 的 observe_threshold 无关——那是 Observation，本层只消费状态）

# 四段信号（v0.9.0）：BUY 类 = 可行动推荐；HOLD/WATCH/WAIT = 不推荐
BUY_SIGNALS = ("STRONG_BUY", "BUY")
_SIGNAL_ORDER = {"STRONG_BUY": 0, "BUY": 1, "WATCH": 2, "HOLD": 3, "WAIT": 4}


def _four_stage_specs():
    """四段 Policy（lru_cached spec，不重复读盘）。返回 (stock_spec, etf_spec)。"""
    from src.common.spec.loaders import load_etf_selection_spec, load_stock_selection_spec
    return load_stock_selection_spec(), load_etf_selection_spec()


_STOCK_SPEC, _ETF_SPEC = _four_stage_specs()


def _signal_rules_dicts() -> list[dict[str, Any]]:
    """信号规则（个股与 ETF 共用同一词汇表；share stock_selection.signal_policy）。"""
    return [asdict(r) for r in _STOCK_SPEC.signal_policy.rules]


def _apply_four_stage(
    cand: AssetCandidate,
    *,
    rank: int,
    trend_level: str,
    closes: list[float],
    spec: Any,
    outperform_bar: float | None = None,
) -> str:
    """给候选补齐四段字段（leadership / position / signal），返回 signal。

    纪律落实：
      - position 只调赔率不产生趋势（trend_level=NOT_QUALIFIED → 必为 fallback WAIT 级）
      - HIGH → HOLD（不追高）；require_rps_outperform 且趋势指标低于 outperform_bar 时强制 NON_CORE
    """
    lv = spec.leadership
    lvl = four_stage.classify_leadership(rank, lv.leader_rank_max, lv.core_rank_max)
    if (lv.require_rps_outperform and outperform_bar is not None
            and cand.trend_metric_value is not None and cand.trend_metric_value < outperform_bar):
        lvl = "NON_CORE"
    hp = spec.historical_position
    pos_level, pos_pct = four_stage.evaluate_position(
        closes,
        hp.lookback_days, hp.low_max, hp.mid_max,
        enabled=hp.enabled, metric=hp.metric,
        ma_window=hp.ma_window, breakdown_pct=hp.breakdown_pct,
        low_below_pct=hp.low_below_pct, high_above_pct=hp.high_above_pct)
    signal = four_stage.match_signal(
        trend_level, lvl, pos_level, _signal_rules_dicts(), _STOCK_SPEC.signal_policy.fallback_signal)
    cand.leadership_level = lvl
    cand.theme_rank = rank
    cand.position_level = pos_level
    cand.position_pct = pos_pct
    cand.position_lookback_days = _position_window(hp)
    cand.signal = signal
    return signal


def _position_window(hp: Any) -> int:
    """历史位置生效时的回看窗口（price_percentile=lookback_days；ma60_deviation=ma_window）。"""
    if not hp.enabled:
        return 0
    return hp.ma_window if hp.metric == "ma60_deviation" else hp.lookback_days


def _signal_reason(cand: AssetCandidate) -> str:
    """四段信号的人类可读说明（用于 reason/报告展示，不改变事实）。"""
    if cand.signal in ("", "WAIT"):
        return ""
    parts = [cand.signal]
    if cand.leadership_level:
        parts.append(cand.leadership_level)
    if cand.position_level in ("LOW", "MID", "HIGH", "BREAKDOWN"):
        parts.append(cand.position_level)
    return " · ".join(parts)


def _industry_confirm_threshold() -> float:
    """行业确认门（展示用距离指标）= Layer② observe_threshold（Observation 事实）。"""
    from src.common.spec.loaders import load_indicator_spec
    return float(load_indicator_spec().confirmation_observe_threshold)


INDUSTRY_CONFIRM_RPS15 = _industry_confirm_threshold()


def _primary_stock(stock_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """个股首选（v0.9.0 四段）：被信号门控为 BUY 类（STRONG_BUY > BUY）的候选中
    strategy_score 最高的一只（分赛道，不跨 ETF 比较）。"""
    recs = [c for c in stock_candidates if c.get("recommended") and c.get("signal") in BUY_SIGNALS]
    if not recs:
        return []
    best = max(recs, key=lambda c: (
        _SIGNAL_ORDER.get(c.get("signal", ""), 9),
        -float(c.get("strategy_score")) if c.get("strategy_score") is not None else -1e9))
    return [best]


def _confirmation_breadth_params() -> tuple[float, float]:
    from src.common.spec.loaders import load_indicator_spec
    s = load_indicator_spec()
    return s.confirmation_broad_fraction, s.confirmation_watch_proximity


CONF_BROAD_FRACTION, CONF_WATCH_PROXIMITY = _confirmation_breadth_params()

# 个股 tier → role（AI 基础设施细分赛道 + 高现金流/通用）
TIER_ROLE_MAP = {
    "leader": "LEADER",
    "high_beta": "HIGH_BETA",
    "equipment_upstream": "UPSTREAM",
    "computing_chip": "LEADER",
    "optical_interconnect": "LEADER",
    "server_network": "LEADER",
    "semiconductor_equipment": "UPSTREAM",
    "semiconductor_components": "LEADER",
    "liquid_cooling": "UPSTREAM",
    "auto_thermal_ai_cooling": "UPSTREAM",
    "high_speed_interconnect": "LEADER",
    "server_power": "UPSTREAM",
    "oem_global": "LEADER",
    "battery_global": "LEADER",
    "global_ev_components": "LEADER",
    "global_auto_components": "LEADER",
    "adas_lidar": "LEADER",
    "hydro_nuclear": "LEADER",
    "telecom_operator": "LEADER",
    "toll_road": "LEADER",
    "port_operator": "LEADER",
    "cyclical_power_watch": "WATCH",
}


def _clean_trend_status(v: Any) -> str:
    """趋势状态归一化：缺失/空 → UNKNOWN；OUT_OF_SCOPE → BELOW_TREND_GATE。"""
    if v is None:
        return "UNKNOWN"
    if isinstance(v, float) and pd.isna(v):
        return "UNKNOWN"
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return "UNKNOWN"
    return ETF_TREND_STATUS_LABELS.get(s, s)


# ── v0.10 资产状态语义（technical_diagnostics / blocking_flags / data_quality_flags） ──

def _etf_technical_diagnostics(row: pd.Series) -> dict[str, dict[str, Any]]:
    """ETF 技术诊断（Layer① facts 派生，零新增计算，只读分类不改决策）。

    - trend（趋势）: account_candidates.trend_state 映射
        BUY_CANDIDATE / STRONG_WATCH → STRONG；WATCH → NORMAL；OUT_OF_SCOPE → WEAK；缺失 → UNKNOWN
    - momentum（动量）: ΔRPS15 主判据（velocity 视角）
        Δ≥+5 → STRONG；Δ≤−5 → WEAK；之间 → NORMAL；缺失（历史不足）→ UNKNOWN；rps1 作补充 flag
    - relative_strength（相对强弱）: rps15 全市场横截面分档
        ≥80 → STRONG；≥60 → NORMAL；<60 → WEAK；缺失 → UNKNOWN
    """
    ts = str(row.get("trend_state", "") or "")
    trend_level = {
        "BUY_CANDIDATE": LEVEL_STRONG,
        "STRONG_WATCH": LEVEL_STRONG,
        "WATCH": LEVEL_NORMAL,
        "OUT_OF_SCOPE": LEVEL_WEAK,
    }.get(ts, LEVEL_UNKNOWN)

    delta = row.get("delta_rps15")
    mom_flags: list[str] = []
    if pd.notna(delta):
        d = float(delta)
        if d >= 5:
            mom_level = LEVEL_STRONG
        elif d <= -5:
            mom_level = LEVEL_WEAK
        else:
            mom_level = LEVEL_NORMAL
        mom_flags.append("delta_rps15_positive" if d > 0 else "delta_rps15_negative")
    else:
        mom_level = LEVEL_UNKNOWN
    rps1 = row.get("rps1")
    if pd.notna(rps1):
        r = float(rps1)
        mom_flags.append("rps1_hot" if r >= 80 else "rps1_cold" if r <= 20 else "")
    mom_flags = [f for f in mom_flags if f]

    rps15 = row.get("rps15")
    if pd.notna(rps15):
        r = float(rps15)
        rs_level = LEVEL_STRONG if r >= 80 else (LEVEL_NORMAL if r >= 60 else LEVEL_WEAK)
    else:
        rs_level = LEVEL_UNKNOWN

    return {
        TECH_DIM_TREND: {"level": trend_level, "flags": []},
        TECH_DIM_MOMENTUM: {"level": mom_level, "flags": mom_flags},
        TECH_DIM_RELATIVE_STRENGTH: {"level": rs_level, "flags": []},
    }


def _diag_has_unknown(diag: dict[str, Any]) -> bool:
    """技术诊断任一维度 UNKNOWN（历史不足）→ 用于 data_quality INSUFFICIENT_HISTORY。"""
    if not diag:
        return False
    return any((diag.get(dim) or {}).get("level") == LEVEL_UNKNOWN for dim in TECH_DIMS)


def _etf_data_quality(row: pd.Series) -> list[str]:
    """ETF 数据质量来源（Layer① rotation 已有 facts，规范化透传）。

    当前透传 rotation.data_quality_flag（corporate_action）；卡片侧
    detect_risks（extreme_return / possible_split / stale_data）属 Layer① 独立产物，
    不在 Layer③ 输入范围内，不在此处伪造。
    """
    flags: list[str] = []
    dq = str(row.get("data_quality_flag", "") or "").strip()
    if dq and dq.lower() != "nan":
        flags.append(dq)  # compose_data_quality_flags 内部会 .upper() 规范为 CORPOTATE_ACTION 等码
    return flags


def _apply_etf_semantics(cand: AssetCandidate, row: pd.Series, theme_confirmed: bool | None) -> None:
    """给 ETF 候选补齐 v0.10 语义字段（technical_diagnostics / data_quality / blocking）。

    只读分类与透传，不改决策字段（recommended / signal 等由既有逻辑决定）。
    """
    cand.technical_diagnostics = _etf_technical_diagnostics(row)
    cand.data_quality_flags = compose_data_quality_flags(
        data_status="current", selection_status="available",
        insufficient_history=_diag_has_unknown(cand.technical_diagnostics),
        etf_flags=_etf_data_quality(row))
    cand.blocking_flags = compose_blocking_flags(
        reason_codes=cand.reason_codes, position_level=cand.position_level,
        state=cand.state, signal=cand.signal, participation=cand.participation or "tradeable",
        theme_confirmed=theme_confirmed, risk_gate_passed=cand.risk_gate_passed)


def _candidate_blocking(cand: AssetCandidate, theme_confirmed: bool | None = None) -> list[str]:
    """归集 blocking_flags（共享语义接口）：为什么不能交易。STALE/MISSING 归 data_quality。"""
    return compose_blocking_flags(
        reason_codes=cand.reason_codes,
        position_level=cand.position_level,
        state=cand.state,
        signal=cand.signal,
        participation=cand.participation,
        theme_confirmed=theme_confirmed,
        risk_gate_passed=cand.risk_gate_passed,
    )


def _candidate_data_quality(
    cand: AssetCandidate,
    *,
    insufficient_history: bool = False,
    etf_flags: Any = None,
) -> list[str]:
    """归集 data_quality_flags（共享语义接口）：数据是否可信。"""
    return compose_data_quality_flags(
        data_status=cand.data_status,
        selection_status=cand.selection_status,
        insufficient_history=insufficient_history,
        etf_flags=etf_flags,
    )


@dataclass
class AssetCandidate:
    code: str
    name: str
    role: str                        # CORE_ETF / SUB_INDUSTRY_ETF / LEADER / HIGH_BETA / UPSTREAM
    asset_type: str                  # etf / stock
    bucket: str = ""
    theme: str = ""
    rps15: float | None = None
    rps20: float | None = None
    rps60: float | None = None
    # v0.10.1 Observation factual passthrough：ETF 产品级 Today/Velocity 事实原值透传
    # （Layer① rotation 已算好的 rps1 / delta_rps15，仅复制进候选 dict 供审计详情展示，不参与决策）
    rps1: float | None = None
    delta_rps15: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    trend_status: str = ""
    score_trend: float | None = None   # 0-100 趋势分（个股来自 trend_engine）
    # 统一决策接口（facts + policy，ETF 与个股标尺不同不混榜）
    trend_metric_name: str = ""        # rps15（ETF）/ score_trend（个股）
    trend_metric_value: float | None = None
    metric_scope: str = ""             # etf_cross_section / absolute_technical
    strategy_score: float | None = None  # 本策略内部排序分（不跨 ETF/个股比较）
    reason_codes: list[str] = field(default_factory=list)  # 机器可读策略码
    rank_change_5d: float | None = None
    liquidity: float | None = None   # 成交额（元）
    tradable: bool = True
    recommended: bool = True
    state: str = STOCK_STATE_WATCH
    data_status: str = "current"          # current / stale / missing（来自个股趋势产物）
    selection_status: str = "available"   # available / unavailable（missing → unavailable）
    # 变化跟踪（固定观察池监控用）
    score_change_1d: int | None = None
    state_change: str = ""
    days_in_state: int = 1
    last_trend_qualified_date: str = ""
    # 风险门控：趋势达标 ≠ 可行动，风险警戒/剔除观察等门控单独记录
    risk_gate_passed: bool = True
    risk_flags: list[str] = field(default_factory=list)
    # v0.10 语义重构：technical_diagnostics（技术状态，Stock/ETF 各自实现）+
    # blocking_flags（为什么不能交易，共享语义接口）+ data_quality_flags（数据是否可信）
    # 三者是既有字段的语义投影（分类/归集/透传），不制造新事实；risk_flags/reason_codes 保留为底层事实。
    technical_diagnostics: dict[str, Any] = field(default_factory=dict)
    blocking_flags: list[str] = field(default_factory=list)
    data_quality_flags: list[str] = field(default_factory=list)
    selection_score: float | None = None
    reason: str = ""
    # 资产池 tier 归属（universe 原始赛道标签，如 computing_chip/光模块；role 是其聚合归类）
    tier: str = ""
    tier_label: str = ""
    # tier 参与语义：tradeable（默认，可交易候选）/ monitor_only（研究迁移 Tier，仅监控）
    participation: str = "tradeable"
    # monitor_only tier 的商业化阶段（来自 research_observations.yaml，只读联接展示）
    evidence_stage: str = ""
    revenue_evidence: str = ""
    # 四段信号（v0.9.0）：trend → leadership → position → signal
    leadership_level: str = ""          # LEADER / CORE / NON_CORE（主题内相对地位）
    theme_rank: int | None = None       # 主题内可比分排名（1 起）
    position_level: str = ""            # LOW / MID / HIGH / BREAKDOWN / UNKNOWN（历史位置）
    position_pct: float | None = None   # price_percentile=分位(0-100)；ma60_deviation=乖离率(%,可负)
    position_lookback_days: int = 0     # 位置回看窗口（price_percentile=lookback_days；ma60_deviation=ma_window）
    signal: str = ""                    # STRONG_BUY / BUY / WATCH / HOLD / WAIT
    # v0.11 三 Lane 事实透传（Phase 0：ETF State Fusion，只读 three_lane 单一 join，
    # 对齐后消费；exact 缺失 → 全部 None → to_dict 不落字段。仅 Observation 事实，
    # Phase 0 不参与任何 BUY 门控。Phase 2 起 lane2_reliable_360 作可靠性硬 gate。）
    lane2_reliable_360: bool | None = None
    lane2_long_term_bottom: bool | None = None
    lane2_target_stage: str | None = None     # TARGET / NEAR_MISS / NON_TARGET
    lane2_bottom_state: str | None = None     # DEEP_BOTTOM / RECOVERING / NORMAL / UNRELIABLE
    lane3_transition_state: str | None = None  # FIRST_EXIT/EARLY/ACTIVE/ESTABLISHED/RETEST/POST_TRANSITION/UNRELIABLE
    lane3_days_since_first_exit: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        out: dict[str, Any] = {}
        for k, v in d.items():
            if v is None:
                continue
            if k == "reason_codes" and not v:
                continue
            out[k] = v
        return out


def _round(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return round(float(v), 1)


# ── 三 Lane 事实透传（Phase 0：ETF State Fusion 只读消费，不参与 BUY 门控）──

def _lane_value(lane_row: Any, key: str) -> Any:
    """安全取 three_lane 行字段（缺列 / NaN / None → None）。"""
    if lane_row is None:
        return None
    try:
        if key not in lane_row.index:
            return None
    except AttributeError:
        return None
    v = lane_row[key]
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:  # noqa: BLE001 — 非 pandas 标量
        pass
    return v


def _lane_bool(lane_row: Any, key: str) -> bool | None:
    v = _lane_value(lane_row, key)
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    try:
        return bool(v)
    except Exception:  # noqa: BLE001
        return None


def _lane_str(lane_row: Any, key: str) -> str | None:
    v = _lane_value(lane_row, key)
    return str(v) if v is not None else None


def _lane_float(lane_row: Any, key: str) -> float | None:
    v = _lane_value(lane_row, key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _apply_lane_facts(cand: AssetCandidate, lane_row: Any) -> None:
    """把 three_lane 行的事实透传到 ETF AssetCandidate（缺行/缺列 → 字段留 None）。"""
    if lane_row is None:
        return
    cand.lane2_reliable_360 = _lane_bool(lane_row, "lane2_reliable_360")
    cand.lane2_long_term_bottom = _lane_bool(lane_row, "lane2_long_term_bottom")
    cand.lane2_target_stage = _lane_str(lane_row, "lane2_target_stage")
    cand.lane2_bottom_state = _lane_str(lane_row, "lane2_bottom_state")
    cand.lane3_transition_state = _lane_str(lane_row, "lane3_transition_state")
    cand.lane3_days_since_first_exit = _lane_float(lane_row, "lane3_days_since_first_exit")


def lane_index_from_df(lane_df: pd.DataFrame | None) -> dict[str, Any]:
    """three_lane DataFrame → {fund_code: 行}（空/缺 → {}，消费侧 lane-less）。"""
    if lane_df is None or lane_df.empty or "fund_code" not in lane_df.columns:
        return {}
    out: dict[str, Any] = {}
    for _, row in lane_df.iterrows():
        code = row["fund_code"]
        if code is None:
            continue
        out[str(code)] = row
    return out


# ── 1. 主题确认 ────────────────────────────────────────────────────

def classify_confirmation_breadth(
    confirmed: bool,
    n_observe: int,
    n_total: int,
    max_rps15: float | None,
    *,
    broad_fraction: float = 0.5,
    watch_proximity: float = 70.0,
) -> tuple[str, str]:
    """确认广度分类：区分「多数子行业共同走强」与「少数子行业拉动」。

    v0.9.2 Theme 层 taxonomy：状态只用 BROAD_CONFIRMED / NARROW_CONFIRMED /
    UNCONFIRMED（无 WATCH）；「接近观察门」作为 Evidence 描述进入 label，
    不再作为独立状态。

    Returns:
        (state, label)
        BROAD_CONFIRMED  多数焦点行业进入观察区（≥ broad_fraction）
        NARROW_CONFIRMED 已确认但仅少数行业拉动（窄幅确认）
        UNCONFIRMED      无支撑（最强行业接近门槛时 label 标注「接近观察门」）
    """
    if confirmed:
        broad = n_total > 0 and n_observe >= max(1, int(round(n_total * broad_fraction)))
        return ("BROAD_CONFIRMED", "广泛确认") if broad else ("NARROW_CONFIRMED", "窄幅确认")
    if max_rps15 is not None and max_rps15 >= watch_proximity:
        return ("UNCONFIRMED", "未确认 · 接近观察门")
    return ("UNCONFIRMED", "未确认")


def _col_series(df: pd.DataFrame, col: str) -> pd.Series:
    """安全取列（空 DataFrame / 缺列 → 空 Series），容错 focus/confirmation 数据缺失。"""
    if df is None or df.empty or col not in df.columns:
        return pd.Series(dtype=float)
    return df[col].dropna()


def _confirm_evidence(sub: pd.DataFrame, confirmed: bool) -> dict[str, Any]:
    """确认依据：已确认 → 最强进入观察区行业；未确认 → 最强行业（距门槛）。"""
    obs = sub[sub["strength_level"].astype(str).isin(THEME_CONFIRM_STATES)] if confirmed else sub
    if obs.empty:
        obs = sub
    if obs.empty:
        return {}
    top = obs.sort_values("RPS15", ascending=False).iloc[0]
    rps = float(top["RPS15"]) if pd.notna(top.get("RPS15")) else None
    return {"industry": str(top.get("industry_name", "")),
            "industry_code": str(top.get("industry_code", "")),
            "rps15": _round(rps)}


def evaluate_themes(
    confirmation_df: pd.DataFrame,
    rotation_df: pd.DataFrame,
    tier_confirmation_df: pd.DataFrame | None = None,
) -> dict[str, dict[str, Any]]:
    """按 theme 聚合 Layer② confirmation，判定每个主题是否确认。

    v0.9.1：配置了 tiers 的主题（AI / 中国汽车全球化 / 高现金流）确认 Gate 统一升级为
    Tier basket —— 消费 tier_confirmation parquet 的 Tier 确认状态；申万行业降级为
    Evidence（仍展示，不再单独决定主题 confirmed）。未配置 tiers 的主题（如未来新主题）
    维持原行业 Gate（向后兼容）。

    Returns:
        {theme_key: {label, bucket, bucket_label, confirmed, n_strong, n_observe,
                     median_rps15, strongest_industry_rps15, median_participation,
                     median_hhi, median_top3_share, etf_median_rps15, reason}}
    """
    buckets = themes_cfg.load_buckets()
    themes = {th.key: th for b in buckets for th in b.themes}
    # tier 确认索引：theme_key → 聚合结果
    tier_index: dict[str, dict[str, Any]] = {}
    if tier_confirmation_df is not None and not tier_confirmation_df.empty and "theme" in tier_confirmation_df.columns:
        from src.sw_industry_rps import tier_confirmation as _tc
        for theme_key in {str(t) for t in tier_confirmation_df["theme"].dropna().unique()}:
            sub = tier_confirmation_df[tier_confirmation_df["theme"] == theme_key]
            if not sub.empty:
                tier_index[theme_key] = _tc.theme_confirmation_from_tiers(sub.to_dict("records"))

    out: dict[str, dict[str, Any]] = {}
    for key, th in themes.items():
        sub = confirmation_df[confirmation_df["industry_code"].isin(th.industry_codes())] \
            if not confirmation_df.empty else pd.DataFrame()
        entry: dict[str, Any] = {
            "label": th.label,
            "bucket": next(b.key for b in buckets if key in [t.key for t in b.themes]),
            "bucket_label": next(b.label for b in buckets if key in [t.key for t in b.themes]),
            "industries": th.industry_codes(),
        }
        # ── Tier Gate（v0.9.0/v0.9.1）：主题配置了 tiers 且 tier 确认可用 → 用 Tier 判定 confirmed ──
        tier_gate_used = False
        tier_meta: dict[str, Any] = {}
        if th.tiers and key in tier_index:
            tm = tier_index[key]
            tier_meta = tm
            tier_gate_used = True
            confirmed = bool(tm.get("confirmed", False))
            n_observe = int(tm.get("n_observe_tiers", 0))
            n_watch = int(tm.get("n_watch_tiers", 0) or 0)
            n_strong = int(tm.get("n_strong_tiers", 0))
            n_total = int(tm.get("n_tiers", 0))
            max_rps = None
            evidence = {"industry": "", "rps15": None}
            breadth_state = str(tm.get("confirmation_state", "UNCONFIRMED"))
            breadth_label = {"BROAD_CONFIRMED": "广泛确认", "CONFIRMED": "确认",
                             "NARROW_CONFIRMED": "窄幅确认",
                             "UNCONFIRMED": "未确认", "UNAVAILABLE": "数据不可用"}.get(
                                 breadth_state, breadth_state)
            reason = f"{tm.get('reason', '')}（Tier Gate）"
            observing_industries: list[dict[str, Any]] = []
            # 上涨结构（Enrichment，soft-fail）：Tier 判定「确认」，行业结构判定「表达」。
            # 结构字段（participation/hhi/top3，来自 confirmation 的 drilldown）可用时
            # 恢复 decide_expression 的区分度；Enrichment 缺失时留 None → expression
            # 塌缩为 CORE_PLUS_LEADER（与结构未知的语义一致，不伪造结构）。
            part = _col_series(sub, "participation_rate")
            hhi = _col_series(sub, "hhi")
            top3 = _col_series(sub, "top3_share")
            entry.update({
                "confirmed": confirmed,
                "n_strong": n_strong,
                "n_observe": n_observe,
                "n_watch": n_watch,
                "n_total": n_total,
                "median_rps15": tm.get("tier_strength_median"),
                "strongest_industry_rps15": max_rps,
                "median_participation": float(part.median()) if not part.empty else None,
                "median_hhi": float(hhi.median()) if not hhi.empty else None,
                "median_top3_share": float(top3.median()) if not top3.empty else None,
                "confirmation_state": breadth_state,
                "confirmation_breadth": breadth_label,
                "confirm_evidence": evidence,
                "observing_industries": observing_industries,
                "reason": reason,
                "tier_gate": tier_meta,
            })
        elif sub.empty:
            entry.update({"confirmed": False, "n_strong": 0, "n_observe": 0, "n_total": 0,
                          "median_rps15": None, "reason": "无确认数据"})
        else:
            levels = sub["strength_level"].astype(str)
            n_strong = int(levels.isin(["强势"]).sum())
            n_observe = int(levels.isin(THEME_CONFIRM_STATES).sum())
            rps = sub["RPS15"].dropna()
            # 上涨结构（对已穿透行业取中位）：保留原始值供表达决策，展示时另行保留两位小数
            part = sub["participation_rate"].dropna()
            hhi = sub["hhi"].dropna()
            top3 = sub["top3_share"].dropna()
            confirmed = n_observe > 0
            max_rps = round(float(rps.max()), 1) if not rps.empty else None
            breadth_state, breadth_label = classify_confirmation_breadth(
                confirmed, n_observe, len(sub), max_rps,
                broad_fraction=CONF_BROAD_FRACTION, watch_proximity=CONF_WATCH_PROXIMITY)
            evidence = _confirm_evidence(sub, confirmed)
            reason = (
                f"{n_observe}/{len(sub)} 个行业进入观察区"
                f"（{breadth_label}，依据 {evidence.get('industry', '')} RPS15={evidence.get('rps15')}）"
                if n_observe else "无行业进入观察区")
            # 观察区行业明细（供「为什么」块展示，如 2/5 航运港口87 铁路73）
            observing_industries: list[dict[str, Any]] = []
            if confirmed:
                observing = sub[sub["strength_level"].astype(str).isin(THEME_CONFIRM_STATES)]
                for _, r in observing.sort_values("RPS15", ascending=False).iterrows():
                    rps_v = float(r["RPS15"]) if pd.notna(r.get("RPS15")) else None
                    observing_industries.append({
                        "industry": str(r.get("industry_name", "")),
                        "industry_code": str(r.get("industry_code", "")),
                        "rps15": _round(rps_v),
                        "strength_level": str(r.get("strength_level", "")),
                    })
            entry.update({
                "confirmed": confirmed,
                "n_strong": n_strong,
                "n_observe": n_observe,
                "n_total": len(sub),
                "median_rps15": _round(rps.median()) if not rps.empty else None,
                "strongest_industry_rps15": max_rps,
                "median_participation": float(part.median()) if not part.empty else None,
                "median_hhi": float(hhi.median()) if not hhi.empty else None,
                "median_top3_share": float(top3.median()) if not top3.empty else None,
                "confirmation_state": breadth_state,
                "confirmation_breadth": breadth_label,
                "confirm_evidence": evidence,
                "observing_industries": observing_industries,
                "reason": reason,
            })
        # 主题 ETF 中位 RPS15（Layer① 按关键词匹配，供展示）
        if not rotation_df.empty and "fund_name" in rotation_df.columns:
            matched = rotation_df[rotation_df["fund_name"].apply(
                lambda n: themes_cfg.match_theme(n, buckets) == key)]
            r = matched["rps15"].dropna()
            entry["etf_median_rps15"] = _round(r.median()) if not r.empty else None
        else:
            entry["etf_median_rps15"] = None
        out[key] = entry
        logger.info("主题[%s]: confirmed=%s (%s)", key, entry.get("confirmed"), entry.get("reason"))
    return out


def evaluate_direction(theme_metas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """跨主题方向门控：任一主题行业确认 → PROCEED，否则 WATCHLIST_ONLY。"""
    confirmed = [k for k, m in theme_metas.items() if m.get("confirmed")]
    if confirmed:
        return {"gate": "PROCEED", "n_confirmed_themes": len(confirmed),
                "confirmed_themes": confirmed,
                "reason": f"{len(confirmed)} 个主题行业进入确认状态（{', '.join(THEME_CONFIRM_STATES)}）"}
    return {"gate": "WATCHLIST_ONLY", "n_confirmed_themes": 0,
            "confirmed_themes": [],
            "reason": "无主题进入确认状态，仅输出观察候选"}


# ── 2. ETF 候选（动态从 Layer① rotation 选） ───────────────────────

def match_theme(fund_name: str) -> str | None:
    """按 config/theme_registry.yaml 的 etf_keywords 匹配首个 theme（bucket 顺序优先）。"""
    return themes_cfg.match_theme(fund_name)


def select_etf_candidates(
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame,
    theme: str,
    trend_gates: set[str] = ETF_TREND_GATES,
    min_amount: float = ETF_MIN_AMOUNT,
) -> pd.DataFrame:
    """从全市场 rotation 中筛选某主题的 ETF，附加趋势门控与流动性。

    ETF 归属按主题关键词匹配（不再依赖 Layer① 单一 is_tech 焦点组）。

    Returns:
        DataFrame（含 selection_score，未排序前）
    """
    if rotation_df.empty or "fund_name" not in rotation_df.columns:
        return pd.DataFrame()

    etf = rotation_df.copy()
    etf["_theme"] = etf["fund_name"].apply(match_theme)
    etf = etf[etf["_theme"] == theme]

    # 合并 trend_state（来自 account_candidates / watchlist）
    if not account_df.empty and "trend_state" in account_df.columns:
        etf = etf.merge(account_df[["fund_code", "trend_state", "account_tradable"]].drop_duplicates(subset=["fund_code"]),
                        on="fund_code", how="left")
    else:
        etf["trend_state"] = ""
        etf["account_tradable"] = True

    # 合并流动性（来自 master）
    if not master_df.empty and "amount" in master_df.columns:
        etf = etf.merge(master_df[["fund_code", "amount"]].drop_duplicates(subset=["fund_code"]),
                        on="fund_code", how="left")
        etf["amount"] = pd.to_numeric(etf.get("amount"), errors="coerce")
    else:
        etf["amount"] = pd.NA

    # 趋势门控（None = 不过滤，仅用于观察兜底展示主题代表）
    if trend_gates is not None:
        etf = etf[etf["trend_state"].isin(trend_gates)].copy()
    # 数据完整性：横截面日期无有效 RPS15 的 ETF 不作为候选（剔除数据缺口/历史不足标的）
    if "rps15" in etf.columns:
        etf = etf[etf["rps15"].notna()].copy()
    # 流动性门槛
    etf = etf[etf["amount"].fillna(0) >= min_amount].copy()

    # 选择评分（Policy）：RPS15/20 + 流动性，权重与 amount_score 口径来自
    # config/strategy_spec.yaml etf_selection（固定区间 log 评分，跨期/跨候选池可比）
    from src.common.spec.loaders import load_etf_selection_spec
    es = load_etf_selection_spec()
    w = es.ranking_weights
    amt = es.amount_score
    rps15 = pd.to_numeric(etf.get("rps15"), errors="coerce").fillna(0)
    rps20 = pd.to_numeric(etf.get("rps20"), errors="coerce").fillna(0)
    amount = pd.to_numeric(etf.get("amount"), errors="coerce").fillna(0)
    amount_score = amount.apply(lambda a: _amount_score(a, amt))
    etf["selection_score"] = (
        w.get("rps15", 0.55) * rps15
        + w.get("rps20", 0.25) * rps20
        + w.get("amount_score", 0.20) * amount_score
    ).round(1)
    return etf


def _amount_score(amount: float, amt: Any) -> float:
    """固定区间 log 流动性评分（Policy 口径，不依赖候选集合相对大小）。

    amount <= floor → 0；amount >= reference → cap；中间按 log10 线性插值。
    同一成交额在不同日期/候选池得到同一分数 → 跨期可比。
    """
    if amount <= 0 or amount <= amt.floor:
        return 0.0
    if amount >= amt.reference:
        return float(amt.cap)
    import math
    lo, hi = math.log10(amt.floor), math.log10(amt.reference)
    if hi <= lo:
        return float(amt.cap) if amount > amt.floor else 0.0
    return min((math.log10(amount) - lo) / (hi - lo) * amt.cap, float(amt.cap))


# 常见基金公司名（用于剥离 ETF 名称尾缀，识别方向词）
_FUND_COMPANIES = [
    "国泰", "华夏", "易方达", "鹏华", "富国", "南方", "嘉实", "博时", "华泰柏瑞",
    "天弘", "招商", "广发", "汇添富", "工银", "华安", "万家", "建信", "银华",
    "国联安", "华宝", "景顺", "申万菱信", "方正富邦", "民生加银", "兴业", "泰康",
    "国寿", "中金", "东财", "永赢", "平安", "海富通", "大成", "长信", "前海开源",
    "交银", "浦银安盛", "中欧", "兴全", "上投摩根", "贝莱德", "华富", "浙商",
    "诺安", "国联", "华泰", "信诚", "天弘", "银华", "创金合信", "国投瑞银",
]


def _direction_word(name: str) -> str:
    """从 ETF 名称提取方向词（剥离 ETF 与基金公司名）。"""
    n = str(name or "").replace("ETF", "").strip()
    for company in _FUND_COMPANIES:
        if n.endswith(company):
            n = n[: -len(company)]
            break
        if n.startswith(company):
            n = n[len(company):]
            break
    return n.strip() or name


def _dedup_etf(etf_df: pd.DataFrame) -> pd.DataFrame:
    """同类 ETF（同方向词）只保留代表。"""
    if etf_df.empty:
        return etf_df
    df = etf_df.copy()
    df["_direction"] = df["fund_name"].apply(_direction_word)
    df = df.sort_values(["selection_score"], ascending=False)
    df = df.drop_duplicates(subset=["_direction"], keep="first")
    return df.reset_index(drop=True)


def theme_etf_pool(
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame,
    theme: str,
    trade_date: str | None = None,
    theme_confirmed: bool | None = None,
) -> list[AssetCandidate]:
    """该主题全部关键词命中的 ETF 池（趋势门控与流动性只标注不硬过滤），供观察池展示。

    与 select_etf_candidates 的区别：不做 trend gate / liquidity 过滤，
    逐只打 reason_codes（trend_gate_passed/below_trend_gate/liquidity_ok/low_liquidity），
    去重保留同方向代表后按 selection_score 降序。recommended = 过趋势门 且 流动性达标。
    """
    if rotation_df.empty or "fund_name" not in rotation_df.columns:
        return []
    etf = rotation_df.copy()
    etf["_theme"] = etf["fund_name"].apply(match_theme)
    etf = etf[etf["_theme"] == theme]

    if not account_df.empty and "trend_state" in account_df.columns:
        etf = etf.merge(account_df[["fund_code", "trend_state", "account_tradable"]].drop_duplicates(subset=["fund_code"]),
                        on="fund_code", how="left")
    else:
        etf["trend_state"] = ""
        etf["account_tradable"] = True
    if not master_df.empty and "amount" in master_df.columns:
        etf = etf.merge(master_df[["fund_code", "amount"]].drop_duplicates(subset=["fund_code"]),
                        on="fund_code", how="left")
        etf["amount"] = pd.to_numeric(etf.get("amount"), errors="coerce")
    else:
        etf["amount"] = pd.NA
    if "rps15" in etf.columns:
        etf = etf[etf["rps15"].notna()].copy()

    # 与 select_etf_candidates 相同的选择评分（Policy）
    from src.common.spec.loaders import load_etf_selection_spec
    es = load_etf_selection_spec()
    w = es.ranking_weights
    amt = es.amount_score
    rps15 = pd.to_numeric(etf.get("rps15"), errors="coerce").fillna(0)
    rps20 = pd.to_numeric(etf.get("rps20"), errors="coerce").fillna(0)
    amount = pd.to_numeric(etf.get("amount"), errors="coerce").fillna(0)
    amount_score = amount.apply(lambda a: _amount_score(a, amt))
    etf["selection_score"] = (
        w.get("rps15", 0.55) * rps15
        + w.get("rps20", 0.25) * rps20
        + w.get("amount_score", 0.20) * amount_score
    ).round(1)

    # 全部关键词命中保留，标注 reasons；同方向代表之外的标 dedup_lost（观察池取 top N，不丢事实）
    best_per_dir: dict[str, str] = {}
    for _, r in etf.sort_values("selection_score", ascending=False).iterrows():
        d = _direction_word(str(r.get("fund_name", "")))
        best_per_dir.setdefault(d, str(r["fund_code"]))
    out: list[AssetCandidate] = []
    for i, (_, row) in enumerate(etf.sort_values("selection_score", ascending=False).iterrows()):
        code = str(row["fund_code"])
        in_gate = str(row.get("trend_state", "")) in ETF_TREND_GATES
        amt_v = pd.to_numeric(row.get("amount"), errors="coerce")
        liq_ok = amt_v is not None and not pd.isna(amt_v) and float(amt_v) >= ETF_MIN_AMOUNT
        codes = (["trend_gate_passed"] if in_gate else ["below_trend_gate"]) + \
                (["liquidity_ok"] if liq_ok else ["low_liquidity"])
        if code != best_per_dir.get(_direction_word(str(row.get("fund_name", "")))):
            codes.append("dedup_lost")
        cand = AssetCandidate(
            code=code,
            name=str(row.get("fund_name", "")),
            role="SUB_INDUSTRY_ETF" if in_gate else "CORE_ETF",
            asset_type="etf",
            bucket="",
            theme=theme,
        rps15=_round(row.get("rps15")),
        rps20=_round(row.get("rps20")),
        rps60=_round(row.get("rps60")),
        rps1=_round(row.get("rps1")),
        delta_rps15=_round(row.get("delta_rps15")),
        return_5d=_round(row.get("return_5d")),
        return_20d=_round(row.get("return_20d")),
        trend_status=_clean_trend_status(row.get("trend_state", "")),
        trend_metric_name="rps15",
        trend_metric_value=_round(row.get("rps15")),
        metric_scope="etf_cross_section",
        strategy_score=_round(row.get("selection_score")),
        reason_codes=codes,
            rank_change_5d=_round(row.get("rank_change_5d")),
            liquidity=_round(row.get("amount")),
            tradable=bool(row.get("account_tradable", False)),
            recommended=bool(in_gate and liq_ok),
            state=STOCK_STATE_RECOMMENDED if (in_gate and liq_ok) else STOCK_STATE_WATCH,
            selection_score=_round(row.get("selection_score")),
            reason="观察池 ETF（趋势/流动性未全达标）",
        )
        # 四段信号（观察池同样补 leadership/position/signal；recommended 改由信号门控）
        signal = _apply_etf_four_stage(
            cand, rank=i + 1, trend_qualified=in_gate,
            closes=four_stage.load_etf_close_history(
                code, trade_date,
                _position_window(_ETF_SPEC.historical_position)))
        cand.recommended = bool(in_gate and liq_ok) and signal in BUY_SIGNALS
        if signal in ("HOLD", "WATCH"):
            sig_code = f"signal_{signal.lower()}"
            if sig_code not in cand.reason_codes:
                cand.reason_codes = cand.reason_codes + [sig_code]
        _apply_etf_semantics(cand, row, theme_confirmed)
        out.append(cand)
    return out


# ── 3. 个股固定观察池 ─────────────────────────────────────────────

def _stock_state(
    score_trend: float | None,
    watch_level: str,
    action_txt: str,
    theme_confirmed: bool,
) -> str:
    """个股三层状态判定。

    QUALIFIED = 趋势合格（score≥70 且 S/A 且非剔除观察/风险警戒）
    RECOMMENDED = QUALIFIED 且 所在主题行业确认（+主题归属由调用方保证）
    """
    qualified = (
        score_trend is not None
        and score_trend >= STOCK_QUALIFIED_SCORE
        and watch_level in STOCK_ALLOWED_TREND_STATES
        and action_txt not in ("剔除观察", "风险警戒")
    )
    if not qualified:
        return STOCK_STATE_WATCH
    if theme_confirmed:
        return STOCK_STATE_RECOMMENDED
    return STOCK_STATE_QUALIFIED


def _trend_change_fields(
    symbol: str,
    market: str,
    score_trend: float | None,
    watch_level: str,
) -> dict[str, Any]:
    """从 trend processed CSV 重算最近历史分数/watch_level，给出变化跟踪字段。

    固定观察池的价值在于变化：score_change_1d（一日分数变动）、
    state_change（watch_level 变化，如 A→B）、days_in_state（当前等级持续天数）、
    last_trend_qualified_date（最近一次趋势分数达到资格线 S/A 的日期）。
    注意：这是「趋势达标」日期，不等同于 selection 最终状态 QUALIFIED。
    """
    out = {"score_change_1d": None, "state_change": "", "days_in_state": 1, "last_trend_qualified_date": ""}
    try:
        from src.common.paths import processed_dir
        from src.trend_engine import engine as te
        from src.trend_engine import scoring as tscoring
    except Exception:
        return out
    path = processed_dir() / f"{market}_{symbol}.csv"
    if not path.exists():
        return out
    try:
        df = pd.read_csv(path, parse_dates=["date"]).sort_values("date").tail(40)
    except Exception:
        return out
    if df.empty:
        return out

    hist: list[tuple[pd.Timestamp, int, str]] = []
    for _, row in df.iterrows():
        s, _ = tscoring.score_latest_row(pd.DataFrame([row]))
        rs = row.get("relative_strength_20d")
        wl = te.calc_watch_level(
            s,
            float(rs) if pd.notna(rs) else None,
            float(row["ma20"]) if pd.notna(row.get("ma20")) else None,
            float(row["ma60"]) if pd.notna(row.get("ma60")) else None,
            float(row["volume_ratio"]) if pd.notna(row.get("volume_ratio")) else None,
        )
        hist.append((pd.Timestamp(row["date"]), int(s), wl))
    if not hist:
        return out

    if len(hist) >= 2 and score_trend is not None:
        out["score_change_1d"] = int(score_trend) - hist[-2][1]

    today_wl = str(watch_level)
    days = 0
    for _d, _s, wl in reversed(hist):
        if wl == today_wl:
            days += 1
        else:
            break
    out["days_in_state"] = days
    if len(hist) >= 2:
        prev_wl = hist[-2][2]
        if prev_wl and prev_wl != today_wl:
            out["state_change"] = f"{prev_wl}→{today_wl}"
    for d, _s, wl in reversed(hist):
        if wl in ("S", "A"):
            out["last_trend_qualified_date"] = d.date().isoformat()
            break
    return out


_EVIDENCE_CACHE: dict[str, dict[str, dict[str, str]]] = {}


def _observation_evidence(item: Any) -> dict[str, str]:
    """monitor-only tier 的商业化阶段（research_observations.yaml 只读联接，进程内缓存）。

    单一事实源：阶段字段只在 research_observations.yaml 维护（stage log 守护），
    这里只按 evidence_source 观察组 key 联接展示，不制造新事实。
    """
    src = str(getattr(item, "evidence_source", "") or "")
    if not src:
        return {}
    if src not in _EVIDENCE_CACHE:
        from src.selection.universe import load_observation_evidence
        _EVIDENCE_CACHE[src] = load_observation_evidence(src)
    return _EVIDENCE_CACHE[src].get(str(item.asset.symbol), {})


def select_stock_watchlist(
    universe_items: list[Any],
    theme: str,
    trend_df: pd.DataFrame,
    theme_confirmed: bool = False,
    trade_date: str | None = None,
) -> tuple[list[AssetCandidate], list[AssetCandidate], list[AssetCandidate]]:
    """输出该主题的固定观察池全量（universe 分层池），不按强弱筛选。

    每个标的状态由 _stock_state 判定（WATCH / QUALIFIED / RECOMMENDED），
    降级与风险警戒同样保留，用于监控状态变化。
    趋势数据缺失（data_status=missing）的标的标记 selection_status=unavailable，
    不进入任何候选（不阻塞整体 Selection）。

    monitor-only tier（participation=monitor_only，研究迁移 Tier）：
    进入监控（趋势/变化/风险/商业化阶段照常计算），但不参与主题内排名与
    四段信号，state 恒 WATCH、recommended 恒 False —— 不因主题确认获得候选资格。

    v0.9.0 四段：可用候选先按 score_trend 做主题内排名（theme_rank），再补
    leadership / position / signal；recommended 由信号门控（主题确认 ∧ BUY 类信号）。
    trade_date 用于把价格历史截断到目标交易日，避免 look-ahead。

    Returns:
        (leaders, high_beta, equipment)
    """
    leaders: list[AssetCandidate] = []
    high_beta: list[AssetCandidate] = []
    equipment: list[AssetCandidate] = []
    available: list[AssetCandidate] = []
    available_market: dict[int, str] = {}

    for item in universe_items:
        if item.theme != theme:
            continue
        role = TIER_ROLE_MAP.get(item.tier)
        if role is None:
            continue
        participation = str(getattr(item, "participation", PARTICIPATION_TRADEABLE) or PARTICIPATION_TRADEABLE)
        monitor_only = participation == PARTICIPATION_MONITOR_ONLY

        # 从 Trend Engine 结果读取趋势
        trend_row = None
        if not trend_df.empty:
            m = trend_df[trend_df["symbol"].astype(str) == item.asset.symbol]
            if not m.empty:
                trend_row = m.iloc[0]

        def _tv(col: str, default: Any = None) -> Any:
            if trend_row is None:
                return default
            v = trend_row.get(col)
            return default if v is None or (isinstance(v, float) and pd.isna(v)) else v

        data_status = str(_tv("data_status", "current"))
        if trend_row is None or data_status == "missing":
            # 局部降级：该资产无可用趋势数据，明确标记 unavailable，不阻塞整体
            cand = AssetCandidate(
                code=item.asset.symbol,
                name=item.asset.name,
                role=role,
                asset_type="stock",
                bucket=item.bucket,
                theme=theme,
                score_trend=None,
                trend_status="UNKNOWN",
                trend_metric_name="score_trend",
                trend_metric_value=None,
                metric_scope="absolute_technical",
                strategy_score=None,
                reason_codes=["data_missing"],
                tradable=True,
                recommended=False,
                state=STOCK_STATE_WATCH,
                data_status=data_status if trend_row is not None else "missing",
                selection_status="unavailable",
                risk_gate_passed=False,
                reason="stock_trend_input_missing",
                tier=item.tier,
                tier_label=item.tier_label,
                participation=participation,
            )
            if monitor_only and "monitor_only" not in cand.reason_codes:
                cand.reason_codes = list(cand.reason_codes) + ["monitor_only"]
            # v0.10 语义字段：数据缺失 → data_quality=MISSING_DATA；无技术诊断；blocking 不误报风险
            cand.technical_diagnostics = {}
            cand.data_quality_flags = compose_data_quality_flags(
                data_status=cand.data_status, selection_status=cand.selection_status)
            cand.blocking_flags = compose_blocking_flags(
                reason_codes=cand.reason_codes, position_level="", state=cand.state,
                signal="", participation=cand.participation,
                theme_confirmed=theme_confirmed, risk_gate_passed=None)
            _append_by_role(cand, role, leaders, high_beta, equipment)
            continue

        watch_level = str(_tv("watch_level", ""))
        action_txt = str(_tv("action", ""))
        score_trend = _round(_tv("score_trend"))
        state = _stock_state(score_trend, watch_level, action_txt, theme_confirmed)
        change = _trend_change_fields(item.asset.symbol, item.asset.market, score_trend, watch_level)
        risk_flags_raw = str(_tv("risk_flags", "") or "")
        risk_flags = [f.strip() for f in risk_flags_raw.split("，") if f.strip()] if risk_flags_raw else []
        risk_gate_passed = action_txt not in ("风险警戒", "剔除观察") and not risk_flags
        if monitor_only:
            # 研究迁移 Tier（monitor-only）：只监控不交易 —— 趋势/变化/风险/商业化阶段
            # 照常计算展示，但不参与主题内排名与四段信号，不因主题确认获得候选资格
            # （state 恒 WATCH、recommended 恒 False；leadership/position/signal 留空）。
            ev = _observation_evidence(item)
            cand = AssetCandidate(
                code=item.asset.symbol,
                name=item.asset.name,
                role=role,
                asset_type="stock",
                bucket=item.bucket,
                theme=theme,
                score_trend=score_trend,
                trend_status=_clean_trend_status(watch_level),
                trend_metric_name="score_trend",
                trend_metric_value=score_trend,
                metric_scope="absolute_technical",
                strategy_score=score_trend,
                reason_codes=["monitor_only"],
                tradable=True,
                recommended=False,
                state=STOCK_STATE_WATCH,
                data_status=data_status,
                selection_status="available",
                score_change_1d=change["score_change_1d"],
                state_change=change["state_change"],
                days_in_state=change["days_in_state"],
                last_trend_qualified_date=change["last_trend_qualified_date"],
                risk_gate_passed=risk_gate_passed,
                risk_flags=risk_flags,
                reason="monitor-only tier（仅监控，不参与候选）",
                tier=item.tier,
                tier_label=item.tier_label,
                participation=participation,
                evidence_stage=ev.get("evidence_stage", ""),
                revenue_evidence=ev.get("revenue_evidence", ""),
            )
            # v0.10 语义字段：monitor-only 只监控不交易；技术诊断照常透传
            diag = tech_diag_from_json(_tv("technical_diagnostics"))
            cand.technical_diagnostics = diag
            cand.data_quality_flags = compose_data_quality_flags(
                data_status=data_status, insufficient_history=_diag_has_unknown(diag))
            cand.blocking_flags = compose_blocking_flags(
                reason_codes=cand.reason_codes, position_level="", state=cand.state,
                signal="", participation=cand.participation,
                theme_confirmed=theme_confirmed, risk_gate_passed=risk_gate_passed)
            _append_by_role(cand, role, leaders, high_beta, equipment)
            continue
        reason_codes: list[str] = []
        # stale 降级（Policy）：数据滞后时不给出推荐信号（分数基于过期数据，不代表当前趋势）
        lag = _tv("lag_days")
        if data_status == "stale" and state in (STOCK_STATE_RECOMMENDED, STOCK_STATE_QUALIFIED):
            state = STOCK_STATE_WATCH
            reason_codes = ["stale_data"]
            reason_txt = f"数据滞后 {lag or '?'} 天，信号降级" if lag else "数据滞后，信号降级"
        else:
            if state == STOCK_STATE_RECOMMENDED:
                reason_codes = ["trend_qualified", "theme_confirmed"]
            elif state == STOCK_STATE_QUALIFIED:
                reason_codes = ["trend_qualified"]
            else:
                reason_codes = ["below_trend_gate"] if risk_gate_passed else ["risk_warning"]
            reason_txt = action_txt
        cand = AssetCandidate(
            code=item.asset.symbol,
            name=item.asset.name,
            role=role,
            asset_type="stock",
            bucket=item.bucket,
            theme=theme,
            rps15=None,  # 个股无全市场 RPS 横截面；relative_strength_20d 是相对收益，不映射进 rps15
            score_trend=score_trend,
            trend_status=_clean_trend_status(watch_level),
            trend_metric_name="score_trend",
            trend_metric_value=score_trend,
            metric_scope="absolute_technical",
            strategy_score=score_trend,  # 个股策略内排序 = 自身趋势分（不跨 ETF 比较）
            reason_codes=reason_codes,
            tradable=True,  # 黑名单机制：未确认不可交易即默认可交易
            recommended=state == STOCK_STATE_RECOMMENDED,
            state=state,
            data_status=data_status,
            selection_status="available",
            score_change_1d=change["score_change_1d"],
            state_change=change["state_change"],
            days_in_state=change["days_in_state"],
            last_trend_qualified_date=change["last_trend_qualified_date"],
            risk_gate_passed=risk_gate_passed,
            risk_flags=risk_flags,
            reason=reason_txt,
            tier=item.tier,
            tier_label=item.tier_label,
        )
        # v0.10 语义字段：技术诊断从 stock_metrics parquet 透传；blocking 待四段信号后补全
        diag = tech_diag_from_json(_tv("technical_diagnostics"))
        cand.technical_diagnostics = diag
        cand.data_quality_flags = compose_data_quality_flags(
            data_status=data_status, insufficient_history=_diag_has_unknown(diag))
        cand.blocking_flags = compose_blocking_flags(
            reason_codes=reason_codes, position_level="", state=state,
            signal="", participation=participation,
            theme_confirmed=theme_confirmed, risk_gate_passed=risk_gate_passed)
        available.append(cand)
        available_market[id(cand)] = item.asset.market

    # ── 主题内排名（theme_rank）→ 四段信号（leadership / position / signal） ──
    scored = sorted(
        [c for c in available if c.score_trend is not None],
        key=lambda c: (c.score_trend or -1e9), reverse=True)
    rank_of = {id(c): i + 1 for i, c in enumerate(scored)}
    closes_cache: dict[str, list[float]] = {}
    for cand in available:
        rank = rank_of.get(id(cand))
        trend_level = "QUALIFIED" if cand.state in (STOCK_STATE_QUALIFIED, STOCK_STATE_RECOMMENDED) else "NOT_QUALIFIED"
        if cand.code not in closes_cache:
            hp = _STOCK_SPEC.historical_position
            closes_cache[cand.code] = four_stage.load_stock_close_history(
                available_market.get(id(cand), "CN"), cand.code, trade_date,
                _position_window(hp))
        signal = _apply_four_stage(
            cand, rank=rank or 1, trend_level=trend_level,
            closes=closes_cache[cand.code], spec=_STOCK_SPEC, outperform_bar=_STOCK_SPEC.qualified_score)
        # 信号门控 recommended：主题确认（state=RECOMMENDED）∧ BUY 类信号
        cand.recommended = cand.state == STOCK_STATE_RECOMMENDED and signal in BUY_SIGNALS
        if signal in ("HOLD", "WATCH") and "stale_data" not in cand.reason_codes:
            sig_code = f"signal_{signal.lower()}"
            if sig_code not in cand.reason_codes:
                cand.reason_codes = cand.reason_codes + [sig_code]
            if cand.reason:
                cand.reason = f"{cand.reason} · {_signal_reason(cand)}"
        # v0.10：四段信号后补全 blocking_flags（position_level / signal 此时已确定）
        cand.blocking_flags = compose_blocking_flags(
            reason_codes=cand.reason_codes, position_level=cand.position_level,
            state=cand.state, signal=cand.signal, participation=cand.participation,
            theme_confirmed=theme_confirmed, risk_gate_passed=cand.risk_gate_passed)
        _append_by_role(cand, cand.role, leaders, high_beta, equipment)

    return leaders, high_beta, equipment


def _append_by_role(
    cand: AssetCandidate,
    role: str,
    leaders: list[AssetCandidate],
    high_beta: list[AssetCandidate],
    equipment: list[AssetCandidate],
) -> None:
    if role == "LEADER":
        leaders.append(cand)
    elif role == "HIGH_BETA":
        high_beta.append(cand)
    else:
        equipment.append(cand)


# ── 4. 表达方式决策 ────────────────────────────────────────────────

def decide_expression(theme_meta: dict[str, Any]) -> dict[str, Any]:
    """基于上涨结构（参与率 / HHI / Top3）+ 确认广度判断 ETF vs 个股。

    NARROW_CONFIRMED 语义：主题确认是存在性判定（任一焦点行业进入观察区即开放
    整个主题的资产资格，不做子主题拆解——sub-theme→资产映射不在当前配置范围）。
    但窄幅确认必须显式标注并压低表达强度：只有少数子行业支撑时，不给出
    「广泛承接」类的强表达，落在 reason 与 confirmation_state 中供阅读。
    """
    confirmed = theme_meta.get("confirmed", False)
    if not confirmed:
        return {"expression": "WATCHLIST_ONLY",
                "expression_label": EXPRESSION_LABELS["WATCHLIST_ONLY"],
                "expression_reason": "主题行业未确认，仅输出观察候选"}

    part = theme_meta.get("median_participation")
    hhi = theme_meta.get("median_hhi")
    top3 = theme_meta.get("median_top3_share")
    conf_state = theme_meta.get("confirmation_state", "")
    n_observe = int(theme_meta.get("n_observe", 0) or 0)
    n_total = int(theme_meta.get("n_total", 0) or 0)

    # 龙头主导：HHI 高 或 Top3 贡献集中（单核/集中领涨）
    leader_dominated = (hhi is not None and hhi >= 0.15) or (top3 is not None and top3 >= 0.60)
    broad = part is not None and part >= 0.60

    if broad and not leader_dominated:
        expression, reason = "ETF_PRIORITY", "参与率≥60% 且结构分散，ETF 完整承接行业 Beta"
    elif leader_dominated:
        expression, reason = "LEADER_PRIORITY", "龙头贡献集中（HHI/Top3 高），优先龙头个股，ETF 作低风险替代"
    else:
        expression, reason = "ETF_CORE_PLUS_LEADER", "扩散形成中，ETF 作核心、龙头作卫星"

    # 窄幅确认：主题开放但支撑面窄 → 追加显式标注，压低表达强度
    if conf_state == "NARROW_CONFIRMED":
        note = f"窄幅确认（仅 {n_observe}/{n_total} 个焦点行业支撑），主题开放但承接面窄，宜观察"
        reason = f"{reason}；{note}" if reason else note

    return {"expression": expression,
            "expression_label": EXPRESSION_LABELS.get(expression, expression),
            "expression_reason": reason}


def _etf_trend_eligible(c: AssetCandidate) -> bool:
    """ETF 是否通过趋势门（可执行性统计；仅 observability，不改变决策）。"""
    return "below_trend_gate" not in (c.reason_codes or [])


def resolve_execution_expression(
    structural_expression: str,
    eligible_etf: int,
    eligible_stock: int,
) -> dict[str, Any]:
    """把结构表达解析为可执行表达（observability，不改决策）。

    结构表达（decide_expression，Layer② 行业结构判断）≠ 可执行表达（Layer③ 产品可得性）。
    当结构表达依赖 ETF 但今日无合格 ETF 产品时，降级为个股/无可执行并显式标注
    fallback_reason；结构表达原值保留（不覆盖、不冒充），供审计与研究。

    典型场景（20260826 高现金流）：结构表达 ETF_PRIORITY，但 ETF 0/9 通过趋势门，
    个股 3 只合格 → execution=STOCK_FALLBACK / status=DEGRADED / reason=NO_ELIGIBLE_ETF。
    """
    execution = structural_expression
    status = EXPRESSION_STATUS_NORMAL
    fallback_reason = ""
    if structural_expression in ("ETF_PRIORITY", "ETF_CORE_PLUS_LEADER"):
        # 结构表达依赖 ETF；无合格 ETF 产品时回退个股（策略 fallback，非修复事实）
        if eligible_etf == 0:
            if eligible_stock > 0:
                execution = "STOCK_FALLBACK"
                status = EXPRESSION_STATUS_DEGRADED
                fallback_reason = "NO_ELIGIBLE_ETF"
            else:
                execution = "NO_EXECUTABLE"
                status = EXPRESSION_STATUS_DEGRADED
                fallback_reason = "NO_ELIGIBLE_ETF_AND_STOCK"
    elif structural_expression == "LEADER_PRIORITY":
        # 镜像：结构表达依赖个股；无合格个股但有 ETF 时回退 ETF
        if eligible_stock == 0 and eligible_etf > 0:
            execution = "ETF_FALLBACK"
            status = EXPRESSION_STATUS_DEGRADED
            fallback_reason = "NO_ELIGIBLE_STOCK"
    return {
        "structural_expression": structural_expression,
        "execution_expression": execution,
        "expression_status": status,
        "fallback_reason": fallback_reason,
        "eligible_etf_count": int(eligible_etf),
        "eligible_stock_count": int(eligible_stock),
    }


def build_top_action(theme_objs: list[dict[str, Any]]) -> dict[str, Any]:
    """顶层行动建议：只回答「今天投哪一个方向」，不枚举具体标的。

    输出 BUY / OBSERVE / WAIT + 方向（bucket）与主题；ETF / 股票 / 观察池
    全部落在下层 buckets[].themes[]，不进入顶层 Action。
    """
    confirmed = [s for s in theme_objs if s.get("confirmed")]
    if not confirmed:
        return {"level": "WAIT", "direction": "", "direction_label": "",
                "theme": "", "theme_label": "", "expression": "", "expression_label": "",
                "summary": "今日方向：等待 —— 无主题进入确认状态，不建仓"}
    rank = {"ETF_PRIORITY": 3, "LEADER_PRIORITY": 3, "ETF_CORE_PLUS_LEADER": 2, "WATCHLIST_ONLY": 1}
    best = max(confirmed, key=lambda s: rank.get(s.get("expression", ""), 1))
    expr = best.get("expression", "")
    expr_label = best.get("expression_label", expr)
    theme_label = best.get("theme_label", best.get("theme", ""))
    direction_label = best.get("bucket_label", best.get("bucket", ""))
    base = {
        "direction": best.get("bucket", ""),
        "direction_label": direction_label,
        "theme": best.get("theme", ""),
        "theme_label": theme_label,
        "expression": expr,
        "expression_label": expr_label,
    }
    if expr != "WATCHLIST_ONLY":
        # 有可行动表达（即使暂无推荐标的，标的详情在下层）
        return {"level": "BUY", **base,
                "summary": f"今日方向：买入 {direction_label} · {theme_label}"}
    return {"level": "OBSERVE", **base,
            "summary": f"今日方向：观察 {direction_label} · {theme_label}（已确认但表达为仅观察）"}


# ── 5. 构建候选对象 ────────────────────────────────────────────────

def build_candidates(
    *,
    rotation_df: pd.DataFrame,
    account_df: pd.DataFrame,
    master_df: pd.DataFrame,
    confirmation_df: pd.DataFrame,
    universe_items: list[Any],
    trend_df: pd.DataFrame,
    tier_confirmation_df: pd.DataFrame | None = None,
    trade_date: str | None = None,
    lane_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """构建 Layer ③ 候选资产对象（结构化 dict，可直接落 JSON）。

    v0.4.3 输出结构：buckets[].themes[]，每个 theme 含 ETF 候选 / 个股观察池 / 表达决策。
    v0.9.1：配置了 tiers 的主题（AI / 汽车 / 高现金流）确认 Gate 升级为 Tier basket
    （消费 tier_confirmation parquet）。
    v0.9.0：四段信号（trend→leadership→position→signal）接入 ETF 与个股候选；
    trade_date 用于把价格历史截断到目标交易日（防 look-ahead）。
    v0.11 Phase 0：lane_df（three_lane_{trade_date}，ETF State Fusion 单一 join）→
    ETF 候选/观察对象透传三 Lane 事实；exact 缺失（lane_df=None）→ lane-less，不改任何 BUY 门控。
    """
    theme_metas = evaluate_themes(confirmation_df, rotation_df, tier_confirmation_df)
    direction = evaluate_direction(theme_metas)
    buckets_cfg = themes_cfg.load_buckets()
    lane_index = lane_index_from_df(lane_df)

    def _attach_lane(cand: AssetCandidate) -> None:
        row = lane_index.get(str(cand.code))
        if row is not None:
            _apply_lane_facts(cand, row)

    buckets_out: list[dict[str, Any]] = []
    for bucket_cfg in buckets_cfg:
        theme_objs: list[dict[str, Any]] = []
        for th in bucket_cfg.themes:
            key = th.key
            meta = theme_metas.get(key, {"label": th.label, "confirmed": False,
                                         "industries": th.industry_codes(),
                                         "reason": "无确认数据"})

            # ETF 候选（动态从 Layer① 选）：严格池 → WATCH 观察池 → 主题代表兜底
            etf_pool = select_etf_candidates(rotation_df, account_df, master_df, key,
                                             trend_gates=ETF_TREND_GATES)
            dedup = _dedup_etf(etf_pool)
            if dedup.empty:
                etf_pool = select_etf_candidates(rotation_df, account_df, master_df, key,
                                                 trend_gates=ETF_WATCH_GATES)
                dedup = _dedup_etf(etf_pool)
            if dedup.empty:
                etf_pool = select_etf_candidates(rotation_df, account_df, master_df, key,
                                                 trend_gates=None)
                dedup = _dedup_etf(etf_pool)
            core_etf: list[AssetCandidate] = []
            sub_industry_etf: list[AssetCandidate] = []
            if not dedup.empty:
                top = dedup.sort_values("selection_score", ascending=False)
                # 核心 ETF：主题内评分最高 1 只（rank=1 → LEADER）
                c = top.iloc[0]
                core_etf.append(_to_etf_candidate(
                    c, "CORE_ETF", bucket_cfg.key, key, "主题评分最高", rank=1, trade_date=trade_date,
                    theme_confirmed=meta["confirmed"]))
                # 细分 ETF：其余不同方向各取 1 只（最多 2，rank 2..3 → CORE）
                for i, (_, r) in enumerate(top.iloc[1:].iterrows()):
                    if len(sub_industry_etf) >= 2:
                        break
                    sub_industry_etf.append(_to_etf_candidate(
                        r, "SUB_INDUSTRY_ETF", bucket_cfg.key, key, "细分方向代表",
                        rank=i + 2, trade_date=trade_date, theme_confirmed=meta["confirmed"]))
            for cand in core_etf + sub_industry_etf:
                _attach_lane(cand)

            # 个股固定观察池（全量）+ 动态候选（按状态门控后的子集）
            leaders, high_beta, equipment = select_stock_watchlist(
                universe_items, key, trend_df, theme_confirmed=meta["confirmed"], trade_date=trade_date)
            stock_watchlist = {
                "leaders": [c.to_dict() for c in leaders],
                "high_beta": [c.to_dict() for c in high_beta],
                "equipment": [c.to_dict() for c in equipment],
            }
            stock_candidates = [
                c.to_dict() for c in (leaders + high_beta + equipment)
                if c.state in (STOCK_STATE_QUALIFIED, STOCK_STATE_RECOMMENDED)
            ]

            # 表达决策（基于原始结构中位数，不因展示舍入翻转阈值）
            expr = decide_expression(meta)

            # 展示指标：结构字段保留两位小数，避免 hhi=0.04 被舍为 0.0
            def _fmt_metric(k: str, v: Any) -> Any:
                if v is None:
                    return None
                if k in ("median_participation", "median_hhi", "median_top3_share"):
                    return round(float(v), 2)
                if k in ("median_rps15",):
                    return _round(v)
                return v

            # ETF 观察池（全部关键词命中 + 原因标注；core/sub 逻辑不变，仅补全事实）
            etf_pool = theme_etf_pool(rotation_df, account_df, master_df, key, trade_date=trade_date,
                                       theme_confirmed=meta["confirmed"])
            for cand in etf_pool:
                _attach_lane(cand)

            # 表达可执行性（observability）：结构表达 ≠ 可执行表达（Layer③ 产品可得性）
            eligible_etf_count = sum(1 for e in etf_pool if _etf_trend_eligible(e))
            eligible_stock_count = sum(1 for c in stock_candidates if c.get("recommended"))
            exec_expr = resolve_execution_expression(
                expr["expression"], eligible_etf_count, eligible_stock_count)

            theme_obj = {
                "theme": key,
                "theme_label": th.label,
                "bucket": bucket_cfg.key,
                "bucket_label": bucket_cfg.label,
                "objective": th.objective,
                "signal_model": th.signal_model,
                "maturity": th.maturity,
                "confirmed": meta["confirmed"],
                "confirmation_reason": meta["reason"],
                "confirmation_state": meta.get("confirmation_state", ""),
                "confirmation_breadth": meta.get("confirmation_breadth", ""),
                "confirm_evidence": meta.get("confirm_evidence", {}),
                "observing_industries": meta.get("observing_industries", []),
                "tier_gate": meta.get("tier_gate", {}),
                "metrics": {k: _fmt_metric(k, v) for k, v in meta.items()
                            if k not in ("label", "bucket", "bucket_label", "industries", "confirmed", "reason",
                                         "confirmation_state", "confirmation_breadth", "confirm_evidence",
                                         "observing_industries", "tier_gate")},
                "expression": expr["expression"],
                "expression_label": expr["expression_label"],
                "expression_reason": expr["expression_reason"],
                # 表达可执行性（observability，不改决策）：结构表达 → 可执行表达 → 降级原因
                "structural_expression": exec_expr["structural_expression"],
                "execution_expression": exec_expr["execution_expression"],
                "expression_status": exec_expr["expression_status"],
                "fallback_reason": exec_expr["fallback_reason"],
                "eligible_etf_count": exec_expr["eligible_etf_count"],
                "eligible_stock_count": exec_expr["eligible_stock_count"],
                "etf_pool_total": len(etf_pool),
                "core_etf": [c.to_dict() for c in core_etf],
                "sub_industry_etf": [c.to_dict() for c in sub_industry_etf],
                "etf_pool": [c.to_dict() for c in etf_pool],
                "stock_watchlist": stock_watchlist,
                "stock_candidates": stock_candidates,
                # 分赛道输出：ETF 首选 / 个股首选 / 表达方式（不跨资产混榜）
                "primary_etf": [c.to_dict() for c in core_etf][:1] if core_etf else [],
                "primary_stock": _primary_stock(stock_candidates),
            }
            theme_obj.update(_theme_stage_meta(theme_obj))
            theme_objs.append(theme_obj)

        theme_objs.sort(key=_theme_sort_key)
        bucket_obj: dict[str, Any] = {
            "bucket": bucket_cfg.key,
            "bucket_label": bucket_cfg.label,
            "objective": bucket_cfg.objective,
            "n_themes": len(theme_objs),
            "n_confirmed": sum(1 for t in theme_objs if t.get("confirmed")),
            "themes": theme_objs,
        }
        buckets_out.append(bucket_obj)

    all_themes = [t for b in buckets_out for t in b["themes"]]
    recommended_actions = _collect_recommended_actions(all_themes)
    closest = _closest_theme(all_themes)
    summary = _selection_summary(buckets_out)
    action = build_top_action(all_themes)
    logger.info("candidates built: %d buckets / %d themes (action=%s, recommended=%d)",
                len(buckets_out), len(all_themes), action["level"],
                len(recommended_actions))
    return {
        "version": "0.4.3",
        "direction": direction,
        "buckets": buckets_out,
        "recommended_actions": recommended_actions,
        "closest_theme": closest,
        "summary": summary,
        "action": action,
    }


def _theme_stage_meta(theme_obj: dict[str, Any]) -> dict[str, Any]:
    """主题阶段判断：确认门在行业 strength_level（Layer② 观察/强势），距离以行业口径为准。

    distance_to_industry_confirm = confirm 门 - 主题最强行业 RPS15（真实确认门）
    distance_to_etf_strength     = 80 - 最强 ETF RPS15（ETF 自身强势门槛，仅供参考）
    两者口径不同，分开暴露，不混用。
    """
    strongest = theme_obj.get("core_etf")[0] if theme_obj.get("core_etf") else None
    strongest_rps = strongest.get("rps15") if strongest else None
    m = theme_obj.get("metrics", {})
    ind_rps = m.get("strongest_industry_rps15")
    confirmed = theme_obj.get("confirmed", False)

    if confirmed:
        stage = "已确认"
        d_ind = 0
        d_etf = round(INDUSTRY_CONFIRM_RPS15 - float(strongest_rps), 1) if strongest_rps is not None else None
    else:
        d_ind = round(INDUSTRY_CONFIRM_RPS15 - float(ind_rps), 1) if ind_rps is not None else None
        d_etf = round(INDUSTRY_CONFIRM_RPS15 - float(strongest_rps), 1) if strongest_rps is not None else None
        stage = "修复观察" if (d_ind is not None and d_ind <= 20) else "弱势"
    return {
        "stage": stage,
        "strongest_etf": {
            "code": strongest.get("code", ""),
            "name": strongest.get("name", ""),
            "rps15": strongest_rps,
            "return_5d": strongest.get("return_5d"),
            "return_20d": strongest.get("return_20d"),
        } if strongest else None,
        "distance_to_industry_confirm": d_ind,
        "distance_to_etf_strength": d_etf,
    }


def _theme_sort_key(t: dict[str, Any]) -> tuple[int, int, float, float, str]:
    """主题相对状态排序：确认在前，再按阶段（行业距离离散化）、
    行业确认距离升序、最强 ETF RPS15 降序。保证第一行 = 最接近确认的展开项。"""
    stage_rank = {"已确认": 0, "修复观察": 1, "弱势": 2}
    se = t.get("strongest_etf") or {}
    rps = se.get("rps15")
    d_ind = t.get("distance_to_industry_confirm")
    return (
        0 if t.get("confirmed") else 1,
        stage_rank.get(t.get("stage", ""), 9),
        float(d_ind) if d_ind is not None else 1e9,
        -(float(rps) if rps is not None else -1e9),
        t.get("theme", ""),
    )


def _closest_theme(theme_objs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """最接近转强：排序后第一个未确认主题的轻量摘要（完整内容仍在 themes）。"""
    target = next((s for s in theme_objs if not s.get("confirmed")), None)
    if target is None:
        return None
    return {
        "theme": target.get("theme"),
        "theme_label": target.get("theme_label"),
        "bucket": target.get("bucket"),
        "bucket_label": target.get("bucket_label"),
        "stage": target.get("stage"),
        "strongest_etf": target.get("strongest_etf"),
        "distance_to_industry_confirm": target.get("distance_to_industry_confirm"),
        "distance_to_etf_strength": target.get("distance_to_etf_strength"),
    }


def _selection_summary(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    """首屏四个数字：推荐行动 / 合格候选 / 确认主题 / ETF 表现最强。"""
    theme_objs = [t for b in buckets for t in b.get("themes", [])]
    qualified = sum(
        1 for t in theme_objs
        for c in t.get("stock_candidates", []) if c.get("state") == STOCK_STATE_QUALIFIED
    )

    def _etf_rps(t: dict[str, Any]) -> float:
        se = t.get("strongest_etf") or {}
        return float(se["rps15"]) if se.get("rps15") is not None else -1e9

    etf_best = max(theme_objs, key=_etf_rps) if theme_objs else None
    return {
        "recommended_actions": len(_collect_recommended_actions(theme_objs)),
        "qualified_candidates": qualified,
        "confirmed_themes": f"{sum(1 for t in theme_objs if t.get('confirmed'))}/{len(theme_objs)}",
        "strongest_etf_theme": etf_best.get("theme_label") if etf_best else "",
    }


def _collect_recommended_actions(theme_objs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """跨主题汇总「今日行动候选」：推荐 ETF + RECOMMENDED 个股。

    同一资产在多个 theme 注册时（跨主题归属），按 (asset_type, code) 去重，
    保留首个出现（bucket order 靠前者 = primary 归属）。Position 权重归属属于 Layer 4。
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for t in theme_objs:
        for a in (t.get("core_etf", []) + t.get("sub_industry_etf", [])
                  + t.get("stock_candidates", [])):
            if not a.get("recommended"):
                continue
            key = (str(a.get("asset_type", "")), str(a.get("code", "")))
            if key in seen:
                continue
            seen.add(key)
            out.append(a)
    return out


def _to_etf_candidate(
    row: pd.Series,
    role: str,
    bucket: str,
    theme: str,
    reason: str,
    *,
    rank: int | None = None,
    trade_date: str | None = None,
    theme_confirmed: bool | None = None,
) -> AssetCandidate:
    """ETF 候选（core/sub）：rank 为主题内排名（按 selection_score），据此补四段信号。

    recommended 由信号门控：趋势门达标 ∧ BUY 类信号。
    """
    in_gate = str(row.get("trend_state", "")) in ETF_TREND_GATES
    cand = AssetCandidate(
        code=str(row["fund_code"]),
        name=str(row.get("fund_name", "")),
        role=role,
        asset_type="etf",
        bucket=bucket,
        theme=theme,
        rps15=_round(row.get("rps15")),
        rps20=_round(row.get("rps20")),
        rps60=_round(row.get("rps60")),
        rps1=_round(row.get("rps1")),
        delta_rps15=_round(row.get("delta_rps15")),
        return_5d=_round(row.get("return_5d")),
        return_20d=_round(row.get("return_20d")),
        trend_status=_clean_trend_status(row.get("trend_state", "")),
        trend_metric_name="rps15",
        trend_metric_value=_round(row.get("rps15")),
        metric_scope="etf_cross_section",
        strategy_score=_round(row.get("selection_score")),
        reason_codes=["theme_confirmed", "trend_gate_passed", "liquidity_ok"] if in_gate
        else ["below_trend_gate", "low_liquidity"],
        rank_change_5d=_round(row.get("rank_change_5d")),
        liquidity=_round(row.get("amount")),
        tradable=bool(row.get("account_tradable", False)),
        recommended=in_gate,
        state=STOCK_STATE_RECOMMENDED if in_gate else STOCK_STATE_WATCH,
        selection_score=_round(row.get("selection_score")),
        reason=reason,
    )
    signal = _apply_etf_four_stage(
        cand, rank=rank, trend_qualified=in_gate,
        closes=four_stage.load_etf_close_history(
            cand.code, trade_date,
            _position_window(_ETF_SPEC.historical_position)))
    cand.recommended = in_gate and signal in BUY_SIGNALS
    if signal in ("HOLD", "WATCH"):
        sig_code = f"signal_{signal.lower()}"
        if sig_code not in cand.reason_codes:
            cand.reason_codes = cand.reason_codes + [sig_code]
        if cand.reason:
            cand.reason = f"{cand.reason} · {_signal_reason(cand)}"
    _apply_etf_semantics(cand, row, theme_confirmed)
    return cand


def _apply_etf_four_stage(
    cand: AssetCandidate,
    *,
    rank: int | None,
    trend_qualified: bool,
    closes: list[float],
) -> str:
    """ETF 四段：leadership 由主题内 selection_score 排名定（core_rank_max=1→LEADER，
    satellite_rank_max=3→CORE）；信号复用 stock_selection.signal_policy（ETF 沿用个股思想）。
    """
    trend_level = "QUALIFIED" if trend_qualified else "NOT_QUALIFIED"
    return _apply_four_stage(
        cand, rank=rank or 1, trend_level=trend_level,
        closes=closes, spec=_ETF_SPEC, outperform_bar=None)


# ── 持久化 ─────────────────────────────────────────────────────────

def save_candidates_json(
    candidates: dict[str, Any],
    output_dir: Path,
    date_str: str,
    meta: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"tradable_candidates_{date_str}.json"
    payload: dict[str, Any] = {"date": date_str, "layer3": candidates}
    if meta:
        payload.update(meta)
    path.write_text(_json_dumps(payload), encoding="utf-8")
    logger.info("candidates json: %s", path)
    return path


def _json_dumps(obj: Any) -> str:
    import json

    def _default(o: Any):
        if isinstance(o, (pd.Timestamp, pd.Timedelta)):
            return str(o)
        if isinstance(o, float):
            return o
        return str(o)
    return json.dumps(obj, ensure_ascii=False, indent=2, default=_default)
