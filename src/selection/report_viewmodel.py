"""Layer③ 报告 ReportViewModel（纯内存展示层，v2 → v0.12.1 IA）。

定位：把 recommendation JSON（决策 contract）+ previous JSON（跨日）+ 变化日志，
组装成「为人阅读服务」的展示模型。**绝不写回 recommendation 对象**。

v0.12.1（Selection V2 阅读顺序）：
  Sections 02–07 按决策漏斗组织，数据源为每主题 panel：
    - 02 Theme Confirmation：主题成立吗（Tier/行业确认）
    - 03 ③A Eligibility：可靠可用 ETF（资格计数）
    - 04 ③B 表达载体：选定表达载体（≠今日可买）
    - 05 ③C Timing：载体当前执行时机（Lane1/2/3 → BUY/WATCH/WAIT）
    - 06 Why Now：当前为什么是这个结果
    - 07 Next Trigger：什么变化会改变结果（只列能改 Policy 的条件）
    - 08 决策审计：全 ETF · reason_codes（appendix）
  关键拆解：「③B 表达载体」与「③C 今日执行」是两个独立对象；
  载体 = 推荐源 CORE/SUB（role），执行 = recommended/signal/timing 状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .report_changes import ChangeEntry, build_changes

ACTION_LABELS = {"BUY": "买入", "OBSERVE": "观察", "WAIT": "等待", "WAIT_FOR_ETF": "等待合格 ETF"}

# 口径说明（恒定展示文案，非决策）
CALIBRE_TEXT = (
    "ETF 的 RPS15 是相对全市场 ETF 横截面的百分位（Layer① rotation）；"
    "行业的 RPS15 是相对 124 个申万二级行业横截面的百分位（Layer②）。两者标尺不同，不可直接对比。"
    "主题确认 = 任一 Tier 个股趋势 / 焦点行业 RPS15 达 Layer② 门槛（v0.9.1 起 Tier Gate）。"
    "Selection V2：③A 资格（可靠可用，Lane2 数据可靠前置）→ ③B 表达载体（同方向适配度选代表）→ "
    "③C Timing（载体当前时机 BUY/WATCH/WAIT）。本报告展示的 RPS/趋势分是 Layer①/② 的事实（原值保留）；"
    "「载体 / 执行 / 未执行原因」是 Layer③ 的策略决策。"
)

# ③C Timing 展示口径常量（非决策，仅供 Next Trigger 量化文案）
ETF_TREND_RPS15_GATE = 80.0   # 与 Layer③ ETF 趋势门展示口径一致（策略 gate 在 signal/indicator_spec）


@dataclass
class ReportClassification:
    """多标签事实：一个主题可同时 actionable + changed + degraded（不互斥）。"""
    actionable: bool = False
    changed: bool = False
    degraded: bool = False
    watch: bool = False

    def display_state(self, priority: list[str]) -> str:
        for label in priority:
            if label in ("normal", "confirmed"):
                continue
            if getattr(self, label, False):
                return label
        return "normal"


@dataclass
class ThemeState:
    theme: str
    theme_label: str
    bucket_label: str
    confirmed: bool
    classification: ReportClassification
    display_state: str
    action: str
    primary: str
    change: str
    theme_obj: dict[str, Any]


@dataclass
class ThemePanel:
    """V2 单主题 panel（只读派生，供 02–07 渲染）。"""
    theme_label: str
    confirmed: bool
    confirmation_state: str = ""
    confirmation_breadth: str = ""
    confirm_reason: str = ""
    action_label: str = ""
    # ③A eligibility（主题级计数 + 分组明细来自 etf_monitoring）
    eligible_count: int = 0
    pool_total: int = 0
    eligibility_groups: dict[str, int] = field(default_factory=dict)
    # ③B 表达载体：recommendation 源的 CORE/SUB（≠今日可买）
    vehicles: list[dict[str, Any]] = field(default_factory=list)
    # ③C 执行时机（逐载体）
    timing_rows: list[dict[str, Any]] = field(default_factory=list)
    # 06 Why Now / 07 Next Trigger
    why_text: str = ""
    triggers: list[str] = field(default_factory=list)


@dataclass
class ReportViewModel:
    selection_date: str
    action: dict[str, Any]
    one_liner: str
    actionable: list[dict[str, Any]]
    themes: list[ThemeState]
    panels: list[ThemePanel]
    changes: list[ChangeEntry]
    comparison_status: str
    audit_stock: list[dict[str, Any]]
    audit_etf: list[dict[str, Any]]
    audit_summary: list[tuple[str, str, int]]
    audit_etf_summary: list[tuple[str, str, int]]
    audit_calibre: str
    meta: dict[str, Any]
    audit_etf_note: str = ""
    notice_lines: list[str] = field(default_factory=list)


def _meaningful_blocks(row: dict[str, Any]) -> list[str]:
    """「有意义的阻塞」：排除纯 BELOW_TREND_GATE（未达趋势门是常态门控，非异常）。"""
    flags = [str(f) for f in (row.get("blocking_flags") or [])]
    return [f for f in flags if f != "BELOW_TREND_GATE"]


# ETF 物料阻塞集合（排除常态趋势门 / 去重 / 观察态）
_ETF_MATERIAL_BLOCKS = {"LOW_LIQUIDITY", "POSITION_HIGH", "RISK_WARNING", "LANE2_UNRELIABLE"}
_ETF_TREND_GATES = {"BUY_CANDIDATE", "STRONG_WATCH", "WATCH"}


def _etf_audit_category(row: dict[str, Any]) -> str:
    """ETF 审计分类（v0.12.1，V2 语义——③A 资格 → ③B 载体 → ③C 时机）。

    互斥优先级：③A 资格门（数据不可靠/账户/流动性）→
    物料阻塞（追高/风控）→ ③B 载体 → ③C 时机到 → ③A 合格未入选 →
    ③C 未达趋势 → 破位 → 动态观察。
    V2 关键语义：③A 资格是最上游门控——数据不可靠/不在账户/流动性不足时，
    该 ETF 已被踢出车辆宇宙，其 position_level/trend_status 仅作辅助信息，
    不应决定主分类。破位检查移至 ③C 层以下（仅在 ③A 全部通过后才看 position）。
    """
    codes = [str(c) for c in (row.get("reason_codes") or [])]
    flags = [str(f) for f in (row.get("blocking_flags") or [])]
    # ── ③A 资格门（最上游） ──
    if "lane2_unreliable" in codes or "LANE2_UNRELIABLE" in flags:
        return "unreliably"
    if "below_account" in codes:
        return "below_account"
    if "low_liquidity" in codes or "LOW_LIQUIDITY" in flags:
        return "low_liquidity"
    # ── 物料阻塞（追高/风控） ──
    if any(f in _ETF_MATERIAL_BLOCKS for f in flags):
        return "blocked"
    # ── ③B / ③C ──
    if row.get("recommended"):
        return "timing_ready"
    if str(row.get("monitoring_source", "") or "") == "recommendation":
        return "vehicle"
    if "dedup_lost" in codes:
        return "dedup_lost"
    if "vehicle_eligible" in codes:
        return "eligible"
    if str(row.get("trend_status", "") or "") not in _ETF_TREND_GATES:
        return "below_trend"
    # ── 破位（仅在 ③A 通过后才看 position） ──
    if row.get("position_level") == "BREAKDOWN":
        return "breakdown"
    return "dynamic_watch"


def _audit_category(row: dict[str, Any]) -> str:
    """个股审计分类（沿用 v2 语义；ETF-only 发布无个股行）。"""
    if row.get("position_level") == "BREAKDOWN":
        return "breakdown"
    if _meaningful_blocks(row):
        return "blocked"
    if row.get("state") == "QUALIFIED":
        return "qualified_unselected"
    if row.get("recommended") or row.get("state") == "RECOMMENDED":
        return "recommended"
    if str(row.get("participation", "") or "") != "monitor_only":
        return "normal"
    return "monitor_only"


def _flatten_themes(recommendation: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for b in recommendation.get("buckets", []):
        for t in b.get("themes", []):
            out.append((str(b.get("bucket_label", "")), t))
    return out


def _code_theme_map(recommendation: dict[str, Any]) -> dict[str, str]:
    m: dict[str, str] = {}
    for _, t in _flatten_themes(recommendation):
        label = t.get("theme_label", "")
        for a in t.get("recommendation", {}).get("etf", []) + t.get("recommendation", {}).get("stocks", []):
            m.setdefault(str(a.get("code", "")), label)
        for a in (t.get("etf_monitoring") or []):
            m.setdefault(str(a.get("code", "")), label)
        for tier in ("leaders", "high_beta", "equipment"):
            for a in t.get("monitoring", {}).get(tier, []):
                m.setdefault(str(a.get("code", "")), label)
    return m


def classify_theme(theme: dict[str, Any], *, changed: bool = False) -> ReportClassification:
    confirmed = bool(theme.get("confirmed", False))
    rec = theme.get("recommendation", {})
    assets = rec.get("etf", []) + rec.get("stocks", [])
    actionable = confirmed and any(a.get("recommended") for a in assets)
    return ReportClassification(actionable=actionable, changed=changed, degraded=False, watch=not confirmed)


def _primary_asset(theme: dict[str, Any]) -> dict[str, Any]:
    rec = theme.get("recommendation", {})
    for a in rec.get("etf", []):
        if a.get("recommended"):
            return a
    for a in rec.get("stocks", []):
        if a.get("recommended"):
            return a
    return {}


def _primary_name(theme: dict[str, Any]) -> str:
    a = _primary_asset(theme)
    return str(a.get("name", "")) if a else "—"


def _theme_change_brief(theme: dict[str, Any], classification: ReportClassification) -> str:
    if classification.watch:
        return "观察"
    if classification.actionable:
        return "可交易"
    return "—"


def _one_liner(themes: list[ThemeState], action: dict[str, Any]) -> str:
    parts: list[str] = []
    for t in themes:
        today = (t.theme_obj.get("today") or {})
        exec_action = str(today.get("action") or "")
        if t.classification.watch:
            d = t.theme_obj.get("distance_to_industry_confirm")
            parts.append(f"{t.theme_label} 暂不加仓（观察）" if d is None else f"{t.theme_label} 暂不加仓（观察，差 {d}）")
        elif t.confirmed and exec_action == "BUY":
            parts.append(f"{t.theme_label} 可执行买入")
        elif t.confirmed and exec_action == "WAIT_FOR_ETF":
            parts.append(f"{t.theme_label} 已确认但无合格 ETF（等待）")
        elif t.confirmed and exec_action == "OBSERVE":
            veh = next((v for v in _theme_panel_vehicles(t.theme_obj)), None)
            veh_txt = f"；载体 {veh.get('name')} 时机未到" if veh else ""
            parts.append(f"{t.theme_label} 达产品门但未达 BUY 信号（观察{veh_txt}）")
        elif t.confirmed:
            parts.append(f"{t.theme_label} 观察")
    judgment = f"今日判断：{ACTION_LABELS.get(str(action.get('level', 'WAIT')), str(action.get('level', '')))}"
    if parts:
        return f"{judgment} · " + "；".join(parts)
    return judgment


# ── V2 派生：theme panel 构建 ─────────────────────────────────────────

def _theme_panel_vehicles(theme: dict[str, Any]) -> list[dict[str, Any]]:
    """③B 表达载体 = 推荐源（monitoring_source=recommendation）的 CORE/SUB 行。"""
    out: list[dict[str, Any]] = []
    for a in (theme.get("etf_monitoring") or []):
        if str(a.get("monitoring_source", "") or "") == "recommendation":
            out.append(a)
    return out


_ROLE_CN = {"CORE_ETF": "核心载体", "SUB_INDUSTRY_ETF": "细分载体"}


def _eligibility_groups(theme: dict[str, Any]) -> dict[str, int]:
    """③A 分组计数（仅 etf_monitoring 覆盖到的 ETF，供 03 展示「可见部分」）。"""
    groups: dict[str, int] = {}
    for a in (theme.get("etf_monitoring") or []):
        codes = [str(c) for c in (a.get("reason_codes") or [])]
        if "lane2_unreliable" in codes:
            key = "lane2_unreliable"
        elif "below_account" in codes:
            key = "below_account"
        elif "low_liquidity" in codes:
            key = "low_liquidity"
        elif "vehicle_eligible" in codes:
            key = "vehicle_eligible"
        elif "below_trend_gate" in codes:
            key = "below_trend_gate"
        else:
            key = "other"
        groups[key] = groups.get(key, 0) + 1
    return groups


def _timing_exec_row(theme: dict[str, Any], veh: dict[str, Any]) -> dict[str, Any]:
    """③C 逐载体时机行（供 05 渲染；不动 engine 事实）。"""
    row = {k: veh.get(k) for k in (
        "code", "name", "role", "signal", "trend_status", "position_level", "position_pct",
        "lane2_reliable_360", "lane2_bottom_state", "lane3_transition_state",
        "lane3_days_since_first_exit", "recommended", "reason_codes", "rps15")}
    row["role_label"] = _ROLE_CN.get(str(veh.get("role", "")), str(veh.get("role", "")))
    # 今日执行语义：③C timing
    if veh.get("recommended"):
        row["__exec__"] = "BUY"
    elif "below_trend_gate" in [str(c) for c in (veh.get("reason_codes") or [])]:
        row["__exec__"] = "WAIT"
    elif str(veh.get("signal", "")) in ("HOLD", "WATCH"):
        row["__exec__"] = str(veh.get("signal", ""))
    else:
        row["__exec__"] = str(veh.get("signal", "") or "WAIT")
    row["exec_reason"] = _timing_reason(veh)
    return row


def _timing_reason(veh: dict[str, Any]) -> str:
    """③C 未执行原因（人话，V2 语义；不制造新事实）。"""
    if veh.get("recommended"):
        return "推荐"
    codes = [str(c) for c in (veh.get("reason_codes") or [])]
    pos = str(veh.get("position_level", "") or "")
    if "below_trend_gate" in codes:
        return "Lane1 未达趋势门"
    if pos == "BREAKDOWN":
        return "破位"
    if pos == "HIGH":
        return "高位不追"
    sig = str(veh.get("signal", "") or "")
    if sig in ("HOLD", "WATCH"):
        return f"信号 {sig}"
    if "lane2_unreliable" in codes:
        return "③A 数据不可靠"
    return "时机未到"


def _why_text(theme: dict[str, Any], panel: ThemePanel) -> str:
    """06 Why Now：当前为什么是这个结果（一句话，按优先级展开）。"""
    if not panel.confirmed:
        return f"主题未成立（{panel.confirm_reason or '确认证据不足'}）→ 不买，仅观察"
    vehs = panel.vehicles
    rec = [v for v in vehs if v.get("recommended")]
    if rec:
        return f"载体 {rec[0].get('name')} 已通过 ③C timing（{rec[0].get('signal')}），可执行买入"
    if not vehs:
        return "主题已成立，但无 ③A 合格表达载体（数据不可靠/流动性不足/不在账户）→ 等待合格载体"
    first = vehs[0]
    reason = _timing_reason(first)
    return f"载体 {first.get('name')} 已入选，但 ③C timing=WAIT（{reason}）→ 现在不买"


def _next_triggers(theme: dict[str, Any], panel: ThemePanel) -> list[str]:
    """07 Next Trigger：什么变化会改变 Policy 结果（只列能真正改结果的条件）。

    已确认且无载体 → ③A 门槛；有载体但无 BUY → 只列「核心载体（CORE）进入可执行态」
    一条量化触发（必要时补充不同原因的另一条），不逐只罗列同一趋势门。
    """
    out: list[str] = []
    if not panel.confirmed:
        out.append("主题尚未成立：任一 Tier（或焦点行业）进入确认门后，主题才有资格谈表达载体")
        return out
    vehs = panel.vehicles
    if panel.eligible_count == 0 and not vehs:
        out.append("出现 ≥1 只通过 ③A 资格（可靠 ∧ 流动性 ∧ 账户）的 ETF 作为表达载体")
        return out
    rec = [v for v in vehs if v.get("recommended")]
    if rec:
        out.append("今日已定格（载体已可执行买入）")
        return out
    # 按「核心载体优先」排序，逐类原因各给一条（趋势门 / 破位 / 高位 / 其他信号）
    ordered = sorted(vehs, key=lambda v: 0 if str(v.get("role", "")) == "CORE_ETF" else 1)
    seen: set[str] = set()
    for v in ordered:
        codes = [str(c) for c in (v.get("reason_codes") or [])]
        name = v.get("name", "")
        r15 = v.get("rps15")
        pos = str(v.get("position_level", "") or "")
        if "below_trend_gate" in codes:
            key = "trend"
        elif pos == "BREAKDOWN":
            key = "breakdown"
        elif pos == "HIGH":
            key = "high"
        else:
            key = "signal"
        if key in seen:
            continue
        seen.add(key)
        if key == "trend":
            rtxt = "—" if r15 is None else f"RPS15={float(r15):g}"
            out.append(f"Lane1 进入允许趋势态（BUY_CANDIDATE / STRONG_WATCH）；当前 {rtxt}，未达趋势门")
        elif key == "breakdown":
            out.append(f"载体 {name} 修复破位（站回 MA60）后才回到可执行区")
        elif key == "high":
            out.append(f"载体 {name} 回落到 MID 以下位置后才进可执行区")
        else:
            out.append(f"载体 {name} 的 ③C 信号转为 BUY（当前 {v.get('signal')}）后转可执行")
    return out


def _build_panel(bucket_label: str, theme: dict[str, Any]) -> ThemePanel:
    today = theme.get("today", {}) or {}
    confirmed = bool(theme.get("confirmed", False))
    vehs = _theme_panel_vehicles(theme)
    panel = ThemePanel(
        theme_label=str(theme.get("theme_label", theme.get("theme", ""))),
        confirmed=confirmed,
        confirmation_state=str(today.get("confirmation_state", "") or ""),
        confirmation_breadth=str(today.get("confirmation_breadth", "") or ""),
        confirm_reason=str(theme.get("why", {}).get("confirmation_reason", "")
                           or today.get("summary", "") or ""),
        action_label=str(today.get("action_label", "") or ""),
        eligible_count=int(theme.get("eligible_etf_count", 0) or 0),
        pool_total=int(theme.get("etf_pool_total", 0) or 0),
        eligibility_groups=_eligibility_groups(theme),
        vehicles=vehs,
        timing_rows=[_timing_exec_row(theme, v) for v in vehs],
    )
    panel.why_text = _why_text(theme, panel)
    panel.triggers = _next_triggers(theme, panel)
    return panel


def _theme_change(theme_obj: dict[str, Any], classification: ReportClassification) -> str:
    return _theme_change_brief(theme_obj, classification)


def build_view_model(
    recommendation: dict[str, Any],
    meta: dict[str, Any],
    selection_date: str,
    *,
    display_priority: list[str],
    prev: dict[str, Any] | None = None,
    audit_summary_spec: list[tuple[str, str]] | None = None,
    audit_sort: str = "",
    audit_etf_summary_spec: list[tuple[str, str]] | None = None,
    audit_etf_sort: str = "",
) -> ReportViewModel:
    """从 recommendation JSON + meta (+ 可选 previous) 构建 V2 ReportViewModel。"""
    changes, changed_themes, comp_status = build_changes(recommendation, prev)

    theme_states: list[ThemeState] = []
    panels: list[ThemePanel] = []
    audit_stock: list[dict[str, Any]] = []
    audit_etf: list[dict[str, Any]] = []

    for bucket_label, t in _flatten_themes(recommendation):
        theme = str(t.get("theme", ""))
        label = t.get("theme_label", theme)
        cls = classify_theme(t, changed=theme in changed_themes)
        ds = cls.display_state(display_priority)
        ts = ThemeState(
            theme=theme, theme_label=label, bucket_label=bucket_label,
            confirmed=bool(t.get("confirmed", False)),
            classification=cls, display_state=ds,
            action=ACTION_LABELS.get(str(t.get("today", {}).get("action", "")), "—"),
            primary=_primary_name(t), change=_theme_change(t, cls),
            theme_obj=t,
        )
        theme_states.append(ts)
        panels.append(_build_panel(bucket_label, t))

        for tier in ("leaders", "high_beta", "equipment"):
            for a in t.get("monitoring", {}).get(tier, []):
                audit_stock.append({**a, "_theme_label": label, "_asset_key": str(a.get("code", a.get("symbol", "")))})
        for a in (t.get("etf_monitoring") or []):
            audit_etf.append({**a, "_theme_label": label, "_asset_key": str(a.get("code", a.get("symbol", "")))})

    code_theme = _code_theme_map(recommendation)
    actionable: list[dict[str, Any]] = []
    for a in recommendation.get("recommended_actions", []):
        a = dict(a)
        a.setdefault("theme_label", code_theme.get(str(a.get("code", "")), ""))
        actionable.append(a)

    one_liner = _one_liner(theme_states, recommendation.get("action", {}) or {})

    # 06 审计排序（分类异常优先）
    _AUDIT_RANK = {"breakdown": 0, "blocked": 1, "unreliably": 1, "low_liquidity": 2,
                   "below_account": 2, "qualified_unselected": 2, "eligible": 3,
                   "vehicle": 4, "dedup_lost": 4, "timing_ready": 5, "below_trend": 6,
                   "recommended": 5, "normal": 6, "monitor_only": 7, "dynamic_watch": 8}

    def _audit_sort_key(r: dict[str, Any]):
        score = r.get("score_trend")
        return (_AUDIT_RANK.get(r.get("_audit_category", ""), 9),
                -(score if score is not None else -1e9),
                str(r.get("_asset_key", "")))

    def _etf_sort_key(r: dict[str, Any]):
        rps = r.get("rps15")
        return (_AUDIT_RANK.get(r.get("_audit_category", ""), 9),
                -(rps if rps is not None else -1e9),
                str(r.get("_asset_key", "")))

    for row in audit_stock:
        row["_audit_category"] = _audit_category(row)
    if audit_sort == "anomaly_first":
        audit_stock.sort(key=_audit_sort_key)
    counts: dict[str, int] = {}
    for row in audit_stock:
        counts[row["_audit_category"]] = counts.get(row["_audit_category"], 0) + 1
    audit_summary: list[tuple[str, str, int]] = []
    for cid, clabel in (audit_summary_spec or []):
        audit_summary.append((cid, clabel, counts.get(cid, 0)))

    for row in audit_etf:
        row["_audit_category"] = _etf_audit_category(row)
    if audit_etf_sort == "anomaly_first":
        audit_etf.sort(key=_etf_sort_key)
    etf_counts: dict[str, int] = {}
    for row in audit_etf:
        etf_counts[row["_audit_category"]] = etf_counts.get(row["_audit_category"], 0) + 1
    audit_etf_summary: list[tuple[str, str, int]] = []
    for cid, clabel in (audit_etf_summary_spec or []):
        audit_etf_summary.append((cid, clabel, etf_counts.get(cid, 0)))
    audit_etf_note = ""
    if etf_counts.get("unreliably"):
        audit_etf_note = (
            "「数据不可靠」= Lane2 判定该 ETF 的 360 日价格不可信，已从 ③A 车辆宇宙剔除："
            "① 份额折算 / 除权（单日 |涨跌|≥20%，疑似公司行为污染 360D 位置）；"
            "② 上市未满 360 交易日（历史不足）；③ 近零波动（货币/债券）。"
            "即便当前技术上处于破位，以其历史数据无法可靠判断底部/修复信号，故不作表达载体。"
        )

    notices: list[str] = []
    layer_labels = {"etf": "Layer① ETF", "account_candidates": "Layer① 账户",
                    "sw_industry": "Layer② 行业"}
    prov = []
    for ln, v in ((meta or {}).get("layers", {}) or {}).items():
        if isinstance(v, dict) and str(v.get("data_status", "") or "") == "provisional":
            prov.append(layer_labels.get(ln, ln))
    if prov:
        notices.append(f"上游数据为临时（provisional）：{'/'.join(prov)} —— 本建议基于临时事实，正式数据发布后重跑 run-day 更新")
    if str((recommendation.get("action", {}) or {}).get("level", "")) == "BUY":
        notices.append("BUY 为配置 Universe（AI / 中国汽车 / 高现金流）内最优表达，非全市场最强机会；全市场最强方向以 ① ETF 轮动报告为准")

    return ReportViewModel(
        selection_date=selection_date,
        action=recommendation.get("action", {}) or {},
        one_liner=one_liner,
        actionable=actionable,
        themes=theme_states,
        panels=panels,
        changes=changes,
        comparison_status=comp_status,
        audit_stock=audit_stock,
        audit_etf=audit_etf,
        audit_summary=audit_summary,
        audit_etf_summary=audit_etf_summary,
        audit_calibre=CALIBRE_TEXT,
        audit_etf_note=audit_etf_note,
        meta=meta or {},
        notice_lines=notices,
    )
