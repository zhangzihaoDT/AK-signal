"""Asset State 共享语义模块（v0.10）。

把「资产现在处于什么技术状态 / 为什么不能交易 / 数据是否可信」三件语义正交的事
拆成三个独立概念，Stock 与 ETF 各自实现 technical_diagnostics，但共享
blocking_flags 与 data_quality_flags 的语义接口：

    technical_diagnostics    技术状态诊断（每类资产各自实现，只读分类）
      ├─ trend              均线结构（趋势）
      ├─ momentum           动能（MACD / RSI）
      └─ relative_strength  相对强弱（横截面 RS）
    blocking_flags          阻塞标记：为什么不能交易（共享语义接口）
    data_quality_flags      数据质量标记：数据是否可信（共享语义接口）

纪律：
  - 本模块只做「对既有事实的分类 / 归集 / 展示」，不制造新事实、不重算指标。
  - 底层事实字段（risk_flags / reason_codes / data_status / position_level …）保留不动；
    本模块是它们的语义投影层，新增字段不影响既有消费方。
  - 展示阈值是展示性分类，不进 indicator_spec / strategy_spec（避免全市场展示反向依赖策略配置）。
"""

from __future__ import annotations

import json
from typing import Any

# ── 技术诊断：维度与等级 ────────────────────────────────────────────

TECH_DIM_TREND = "trend"
TECH_DIM_MOMENTUM = "momentum"
TECH_DIM_RELATIVE_STRENGTH = "relative_strength"
TECH_DIMS = (TECH_DIM_TREND, TECH_DIM_MOMENTUM, TECH_DIM_RELATIVE_STRENGTH)

TECH_DIM_LABELS = {
    TECH_DIM_TREND: "趋势",
    TECH_DIM_MOMENTUM: "动量",
    TECH_DIM_RELATIVE_STRENGTH: "相对",
}

LEVEL_STRONG = "STRONG"
LEVEL_NORMAL = "NORMAL"
LEVEL_WEAK = "WEAK"
LEVEL_UNKNOWN = "UNKNOWN"
LEVEL_LABELS = {LEVEL_STRONG: "强", LEVEL_NORMAL: "正常", LEVEL_WEAK: "弱", LEVEL_UNKNOWN: "未知"}

# 技术 flag 机器码 → 短码展示（个股来自 trend_engine；ETF 来自 Layer① facts 派生）
TECH_FLAG_SHORT = {
    "below_ma20": "MA20↓",
    "below_ma60": "MA60↓",
    "below_ma120": "MA120↓",
    "macd_weak": "MACD弱",
    "rsi_weak": "RSI弱",
    "rsi_hot": "RSI热",
    "rs_negative": "RS负",
    # ETF 派生诊断的补充信息（来源 Layer①：rps1 / Δrps15）
    "rps1_hot": "RPS1高",
    "rps1_cold": "RPS1低",
    "delta_rps15_positive": "ΔRPS15↑",
    "delta_rps15_negative": "ΔRPS15↓",
}

# 技术 flag 机器码 → 旧版中文 flag（backward compat：risk_flags 原值不破坏）
TECH_FLAG_LEGACY = {
    "below_ma20": "跌破MA20",
    "below_ma60": "跌破MA60",
    "below_ma120": "跌破MA120",
    "macd_weak": "MACD转弱",
    "rsi_weak": "RSI偏弱",
    "rsi_hot": "RSI偏热",
    "rs_negative": "RS为负",
}

# ── blocking_flags：为什么不能交易（共享语义接口） ────────────────────

BLOCKING_CN = {
    "BELOW_TREND_GATE": "未达趋势门",
    "LOW_LIQUIDITY": "流动性不足",
    "BREAKDOWN": "中期破位",
    "THEME_NOT_CONFIRMED": "主题未确认",
    "RISK_WARNING": "风险警戒",
    "DEDUP_LOST": "同类去重落选",
    "POSITION_HIGH": "高位不追",
    "SIGNAL_WATCH": "暂不买入",
    "MONITOR_ONLY": "仅监控",
    "LANE2_UNRELIABLE": "Lane2 数据不可靠",
}

# 参与四段信号的阻塞语义（信号 → 阻塞码），展示层用
_SIGNAL_TO_BLOCKING = {
    "HOLD": "POSITION_HIGH",
    "WATCH": "SIGNAL_WATCH",
    "WAIT": "SIGNAL_WATCH",
}

# ── data_quality_flags：数据是否可信（共享语义接口） ──────────────────

DATA_QUALITY_CN = {
    "STALE_DATA": "数据滞后",
    "MISSING_DATA": "数据缺失",
    "INSUFFICIENT_HISTORY": "历史不足",
    "POSSIBLE_SPLIT": "疑似份额拆分",
    "CORPORATE_ACTION": "公司行为异常",
    "EXTREME_RETURN": "极端涨幅",
}


# ── technical_diagnostics 序列化 ─────────────────────────────────────

def tech_diag_to_json(diag: dict[str, Any]) -> str:
    """把 {dim: {level, flags}} 序列化为 JSON 字符串（stock_metrics parquet 列）。"""
    if not diag:
        return ""
    return json.dumps(diag, ensure_ascii=False, separators=(",", ":"))


def tech_diag_from_json(raw: Any) -> dict[str, Any]:
    """从 JSON 字符串解析回 {dim: {level, flags}}；空/非法返回空 dict。"""
    if raw is None or str(raw).strip() in ("", "nan"):
        return {}
    try:
        obj = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[str, Any] = {}
    for dim in TECH_DIMS:
        sub = obj.get(dim)
        if not isinstance(sub, dict):
            continue
        out[dim] = {
            "level": str(sub.get("level", "") or ""),
            "flags": [str(f) for f in (sub.get("flags") or [])],
        }
    return out


def empty_technical_diagnostics() -> dict[str, Any]:
    return {dim: {"level": LEVEL_UNKNOWN, "flags": []} for dim in TECH_DIMS}


# ── 展示辅助 ────────────────────────────────────────────────────────

def technical_text(diag: Any, *, issues_only: bool = True) -> str:
    """技术诊断的紧凑中文渲染（如「趋势弱：MA20↓·MA60↓ · 动量弱：MACD弱 · 相对弱：RS负」）。

    issues_only=True（默认）：只展示有问题的维度（WEAK / UNKNOWN，历史不足→数据不足），
    匹配监控表「风险/阻塞」的阅读语义；STRONG/NORMAL 不显示（不是问题）。
    """
    d = diag or {}
    parts: list[str] = []
    for dim in TECH_DIMS:
        sub = d.get(dim)
        if not isinstance(sub, dict):
            continue
        level = str(sub.get("level", "") or "")
        flags = [str(f) for f in (sub.get("flags") or [])]
        label = TECH_DIM_LABELS[dim]
        if level == LEVEL_UNKNOWN:
            parts.append(f"{label}：数据不足")
            continue
        if issues_only and level not in (LEVEL_WEAK,):
            continue
        lvl_txt = LEVEL_LABELS.get(level, "")
        if not flags:
            parts.append(f"{label}{lvl_txt}")
            continue
        short = "·".join(TECH_FLAG_SHORT.get(f, f) for f in flags)
        parts.append(f"{label}{lvl_txt}：{short}")
    return " · ".join(parts) or "—"


def blocking_text(flags: Any) -> str:
    """blocking_flags → 中文（「未达趋势门 · 流动性不足」）。"""
    if not flags:
        return "—"
    return " · ".join(BLOCKING_CN.get(str(f), str(f)) for f in flags)


def data_quality_text(flags: Any) -> str:
    """data_quality_flags → 中文。"""
    if not flags:
        return "—"
    return " · ".join(DATA_QUALITY_CN.get(str(f), str(f)) for f in flags)


def compose_blocking_flags(
    *,
    reason_codes: Any = None,
    position_level: str = "",
    state: str = "",
    signal: str = "",
    participation: str = "tradeable",
    theme_confirmed: bool | None = None,
    risk_gate_passed: bool | None = None,
) -> list[str]:
    """从既有字段归集 blocking_flags（共享语义接口，纯归集不重算）。

    语义：为什么不能交易。STALE/MISSING 属 data_quality，不在此列。
    """
    codes = [str(c) for c in (reason_codes or [])]
    out: list[str] = []
    if participation == "monitor_only":
        out.append("MONITOR_ONLY")
    if "below_trend_gate" in codes:
        out.append("BELOW_TREND_GATE")
    if "low_liquidity" in codes:
        out.append("LOW_LIQUIDITY")
    if "dedup_lost" in codes:
        out.append("DEDUP_LOST")
    if "risk_warning" in codes or risk_gate_passed is False:
        out.append("RISK_WARNING")
    if "lane2_unreliable" in codes:
        out.append("LANE2_UNRELIABLE")
    if position_level == "BREAKDOWN":
        out.append("BREAKDOWN")
    if signal:
        b = _SIGNAL_TO_BLOCKING.get(str(signal).upper())
        if b and b not in out:
            out.append(b)
    if theme_confirmed is False and state in ("QUALIFIED", "WATCH"):
        out.append("THEME_NOT_CONFIRMED")
    return out


def compose_data_quality_flags(
    *,
    data_status: str = "current",
    selection_status: str = "available",
    insufficient_history: bool = False,
    etf_flags: Any = None,
) -> list[str]:
    """从既有字段归集 data_quality_flags（共享语义接口）。

    etf_flags：ETF 侧数据质量（rotation data_quality_flag / card detect_risks），
    作为规范化码传入（如 CORPORATE_ACTION / POSSIBLE_SPLIT / EXTREME_RETURN）。
    """
    out: list[str] = []
    if str(data_status or "") == "stale":
        out.append("STALE_DATA")
    if str(data_status or "") == "missing" or str(selection_status or "") == "unavailable":
        out.append("MISSING_DATA")
    if insufficient_history:
        out.append("INSUFFICIENT_HISTORY")
    for f in (etf_flags or []):
        code = str(f).strip().upper()
        if code and code not in out:
            out.append(code)
    return out
