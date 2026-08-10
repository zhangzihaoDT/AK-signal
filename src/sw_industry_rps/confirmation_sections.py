"""
Layer ② 第三问「我的主题获得哪些行业支撑」的渲染 section（v0.8.1）。

与 Layer① 同构镜像后，主题确认不再独立成页，而是并入主报告第三问：
  ③ 我的主题获得哪些行业支撑
     ├─ 主题概览（状态 + 结论 + RPS15 + 驱动模式，最小证据）
     └─ <details> 完整确认证据
          ├─ ① 主题确认判断（群共振 + bucket/theme 聚合）
          ├─ ② 证据明细 · SW 行业
          ├─ ③ 内部结构（龙头 vs 广泛上涨）
          └─ ④ 跨层对照（ETF—行业背离）

本模块只做消费侧渲染（不制造新事实），所有证据从 confirmation parquet +
metrics + structure parquet 重算（确定性），不联网、不重算 RPS。
"""

from __future__ import annotations

from html import escape
from typing import Any

import pandas as pd

from . import confirmation as conf
from . import drive_labels as _dl
from .tier_confirmation import (
    TIER_STATE_CONFIRMED, TIER_STATE_LEGACY_OBSERVE, TIER_STATE_STRONG,
    TIER_STATE_UNAVAILABLE, TIER_STATE_UNCONFIRMED, TIER_STATE_WATCH,
    TIER_WATCH_REASON_LABELS,
)


STRENGTH_TAG = {
    "强势": "tag-strong", "观察": "tag-observe", "中性": "tag-neutral",
    "弱势": "tag-weak", "无数据": "tag-none",
}
RELEVANCE_TAG = {"核心": "tag-core", "相关": "tag-related"}
RESONANCE_TAG = {"群共振": "tag-strong", "局部走强": "tag-observe", "整体弱势": "tag-weak"}


def _num(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v):.1f}"


def _sign(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    v = float(v)
    if v > 0:
        return f'<span class="up">+{v:.1f}</span>'
    if v < 0:
        return f'<span class="down">{v:.1f}</span>'
    return f"{v:.1f}"


def _pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"{float(v) * 100:.0f}%"


def _format_full(cs: str, bs: str) -> str:
    c = conf.CONTRIBUTION_LABELS.get(cs, cs)
    b = conf.BREADTH_LABELS.get(bs, bs)
    if c and b:
        return f"{c} × {b}"
    return c or b or "—"


# ─────────────────────────────────────────────────────────────
# 证据对象重建（从 confirmation parquet + metrics + structure 重算）
# ─────────────────────────────────────────────────────────────

def build_confirmation_evidence(
    confirmation_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
) -> dict[str, Any]:
    """从确认事实 + 全市场 metrics 重建主报告第三问所需的所有证据。

    全部为确定性重算（纯函数），不联网、不重算 RPS。返回：
      resonance / theme_resonance / bucket_resonance / market_context /
      divergence_map
    """
    focus_df = confirmation_df
    resonance = conf.classify_group_resonance(focus_df)
    theme_resonance = conf.compute_theme_resonance(focus_df)
    bucket_resonance = conf.compute_bucket_resonance(focus_df)
    market_context = conf.compute_market_context(metrics_df)
    divergence_map: dict[str, Any] = {}
    for theme_key in conf.THEMES:
        tdf = focus_df[focus_df["theme"] == theme_key]
        if not tdf.empty:
            divergence_map[theme_key] = conf.classify_divergence(
                tdf, market_context.get("market_median_rps15"))
    return {
        "resonance": resonance,
        "theme_resonance": theme_resonance,
        "bucket_resonance": bucket_resonance,
        "market_context": market_context,
        "divergence_map": divergence_map,
    }


# ─────────────────────────────────────────────────────────────
# 主题概览（最小证据，主视图）
# ─────────────────────────────────────────────────────────────

def render_theme_summary(confirmation_df: pd.DataFrame) -> str:
    """主题概览：状态 + 支撑数 + 一句结论 + RPS15 + 驱动模式（最小证据）。

    不展示 RPS1/RPS5/Δ/轮动状态分布/距门槛差值（进完整证据）。
    """
    if confirmation_df is None or confirmation_df.empty:
        return "<p>无主题数据</p>"
    df = confirmation_df.copy()
    theme_resonance = conf.compute_theme_resonance(df)
    theme_bucket = dict(zip(df["theme"], df["bucket_label"])) \
        if "bucket_label" in df.columns and "theme" in df.columns else {}
    parts: list[str] = []
    for tr in theme_resonance:
        theme_key = tr.get("theme", "")
        tdf = df[df["theme"] == theme_key]
        label = tr.get("theme_label", theme_key)
        bucket = theme_bucket.get(theme_key, tr.get("bucket_label", ""))
        state = tr.get("confirmation_state", "UNCONFIRMED")
        breadth = tr.get("confirmation_breadth", "")
        n_total = len(tdf)
        n_observe = int((tdf["RPS15"] >= 80).sum())
        state_tag = {
            "BROAD_CONFIRMED": "tag-strong", "NARROW_CONFIRMED": "tag-observe",
            "UNCONFIRMED": "tag-weak", "UNAVAILABLE": "tag-none",
        }.get(state, "tag-none")
        parts.append("<div style='margin:18px 0'>")
        parts.append(
            f"<h3 style='border:none;margin:0 0 6px'>{escape(label)} "
            f"<span class='badge badge-blue'>{escape(str(bucket))}</span> "
            f"<span class='tag {state_tag}'>{escape(state)}</span> "
            f"{('<span class=\'badge\'>' + breadth + '</span>') if breadth else ''} "
            f"<span class='badge'>{n_observe}/{n_total} 支撑</span></h3>")
        conclusion = tr.get("verdict") or tr.get("summary") or ""
        if conclusion:
            parts.append(f"<div class='verdict'>{escape(str(conclusion))}</div>")
        parts.append("<table><thead><tr><th>行业</th><th>角色</th>"
                     "<th class='num'>RPS15</th><th>当前状态</th><th>驱动模式</th></tr></thead><tbody>")
        for _, r in tdf.sort_values("RPS15", ascending=False).iterrows():
            rel = _tag(r.get("relevance_label", ""),
                       "tag-core" if r.get("relevance") == "core" else "tag-none")
            strength = r.get("strength_level", "")
            strength_tag = STRENGTH_TAG.get(str(strength), "tag-none")
            # 主视图只显示综合语义标签（展示层映射，不依赖 drive_pattern 原文）
            driver = _dl.composite_drive_label(
                r.get("contribution_structure", ""), r.get("breadth_structure", ""))
            driver_txt = escape(driver)
            rps = r.get("RPS15")
            rps_style = _rps_color(rps)
            parts.append(
                f"<tr><td style='font-weight:600'>{escape(str(r.get('industry_name','')))}</td>"
                f"<td>{rel}</td>"
                f"<td class='num' style='{rps_style}'>{_num(rps)}</td>"
                f"<td><span class='tag {strength_tag}'>{escape(str(strength))}</span></td>"
                f"<td>{driver_txt}</td></tr>")
        parts.append("</tbody></table>")
        parts.append("</div>")
    return "\n".join(parts)


def _theme_judgment(
    state: str,
    n_observe: int,
    top: Any,
    unconfirmed: pd.DataFrame,
) -> str:
    """生成主题级人话判断（非重复计数）。

    基于确认状态 + 最强支撑行业 + 最接近观察门的行业（SW Evidence 语境）生成一句结论。
    """
    if state in ("BROAD_CONFIRMED", "NARROW_CONFIRMED"):
        if n_observe >= 1 and top is not None and not _isna(top.get("RPS15")):
            name = str(top.get("industry_name", ""))
            # 若有未确认行业，指出差距；否则说明支撑充分
            if not unconfirmed.empty:
                c = unconfirmed.sort_values("RPS15", ascending=False).iloc[0]
                gap = max(0.0, 80.0 - float(c.get("RPS15") or 0))
                return (f"{name} 已确认支撑，{c.get('industry_name','')} 距观察门还差 "
                        f"{_num(gap)}")
            return f"{name} 已确认支撑，行业证据充分"
    # UNCONFIRMED（含「未确认 · 接近观察门」：接近与否由结论体现，不新增状态）
    if top is not None and not _isna(top.get("RPS15")):
        name = str(top.get("industry_name", ""))
        rps = _num(top.get("RPS15"))
        if rps.replace(".", "").isdigit() and float(top.get("RPS15")) >= 70:
            gap = max(0.0, 80.0 - float(top.get("RPS15") or 0))
            return f"未确认 · 最强 {name}（{rps}）接近观察门，差 {_num(gap)}，仍需走强确认"
        return f"焦点行业均未进入观察区，最强 {name}（{rps}），尚未形成行业共振"
    return "焦点行业无有效数据"


def render_theme_table(
    confirmation_df: pd.DataFrame,
    structure_df: pd.DataFrame | None = None,
) -> str:
    """③ 主题支撑精简表：每主题一行（主题/状态/支撑/最接近确认/判断）。

    - 状态：确认状态 + 支撑数 合并为一句（如「窄幅确认 · 1/5」）
    - 支撑：该主题最强焦点行业（RPS15 + 驱动模式，structure 存在时）
    - 最接近确认：RPS15 最高但未达观察门的焦点行业
    - 判断：主题级人话结论
    """
    if confirmation_df is None or confirmation_df.empty:
        return "<p>无主题数据</p>"
    df = confirmation_df.copy()
    theme_resonance = conf.compute_theme_resonance(df)
    if not theme_resonance:
        return "<p>无主题数据</p>"

    parts: list[str] = []
    parts.append("<table><thead><tr><th>主题</th><th>状态</th><th>支撑</th>"
                 "<th>最接近确认</th><th>判断</th></tr></thead><tbody>")

    for tr in theme_resonance:
        theme_key = tr.get("theme", "")
        tdf = df[df["theme"] == theme_key]
        label = tr.get("theme_label", theme_key)
        state = tr.get("confirmation_state", "UNCONFIRMED")
        n_total = len(tdf)
        n_observe = int((tdf["RPS15"] >= 80).sum())
        state_label = {
            "BROAD_CONFIRMED": "广泛确认", "NARROW_CONFIRMED": "窄幅确认",
            "UNCONFIRMED": "未确认", "UNAVAILABLE": "数据不可用",
        }.get(state, state)
        state_tag = {
            "BROAD_CONFIRMED": "tag-strong", "NARROW_CONFIRMED": "tag-observe",
            "UNCONFIRMED": "tag-weak", "UNAVAILABLE": "tag-none",
        }.get(state, "tag-none")

        # 支撑：仅当有确认行业（≥观察门）时显示最强支撑行业；未确认显示 —
        sorted_tdf = tdf.sort_values("RPS15", ascending=False)
        top = sorted_tdf.iloc[0] if not sorted_tdf.empty else None
        support = "—"
        if n_observe >= 1 and top is not None and not _isna(top.get("RPS15")):
            name = str(top.get("industry_name", ""))
            rps = _num(top.get("RPS15"))
            driver = ""
            if structure_df is not None and not structure_df.empty:
                sr = structure_df[structure_df["industry_code"] == top.get("industry_code")]
                if not sr.empty:
                    driver = _dl.composite_drive_label(
                        sr.iloc[0].get("contribution_structure", ""),
                        sr.iloc[0].get("breadth_structure", ""))
                    if driver == _dl._UNKNOWN_LABEL:
                        driver = ""
            support = f"{name} {rps}" + (f" · {driver}" if driver else "")

        # 最接近确认：RPS15 最高但未达观察门的行业
        closest = "—"
        unconfirmed = tdf[(tdf["RPS15"] < 80)]
        if not unconfirmed.empty:
            c = unconfirmed.sort_values("RPS15", ascending=False).iloc[0]
            closest = f"{c.get('industry_name','')} {_num(c.get('RPS15'))}"

        # 判断（人话，非重复计数）：基于确认状态 + 支撑/最接近行业生成一句结论
        conclusion = _theme_judgment(state, n_observe, top, unconfirmed)

        parts.append(
            f"<tr><td style='font-weight:600'>{escape(label)}</td>"
            f"<td><span class='tag {state_tag}'>{escape(state_label)} · {n_observe}/{n_total}</span></td>"
            f"<td>{escape(support)}</td>"
            f"<td>{escape(closest)}</td>"
            f"<td>{escape(conclusion)}</td></tr>")

    parts.append("</tbody></table>")
    return "\n".join(parts)


def _isna(v: Any) -> bool:
    try:
        return pd.isna(v)
    except (TypeError, ValueError):
        return True


def _tier_state_label(r: dict[str, Any]) -> tuple[str, str]:
    """Tier 状态标签：返回 (标签, CSS class)。

    v0.9.2 taxonomy：state 只表达观察单元状态，WATCH 的 why 由 reason_code 补充：
      - STRONG           强势
      - CONFIRMED        已确认
      - WATCH + reason   观察 · <reason>（接近确认 / breadth不足 / 趋势启动 / 单点驱动）
      - UNCONFIRMED      未确认
      - UNAVAILABLE      数据不可用
    """
    from . import tier_confirmation as _tc
    state = str(r.get("confirmation_state", TIER_STATE_UNCONFIRMED))
    data_status = str(r.get("data_status", "") or "")
    if state == TIER_STATE_UNAVAILABLE or data_status == "unavailable":
        return "数据不可用", "tag-none"
    if state == TIER_STATE_STRONG:
        return "强势", "tag-strong"
    if state == TIER_STATE_CONFIRMED or state == TIER_STATE_LEGACY_OBSERVE:
        return "已确认", "tag-observe"
    if state == TIER_STATE_WATCH:
        reason_code = str(r.get("reason_code", "") or "")
        why = TIER_WATCH_REASON_LABELS.get(reason_code, "")
        return (f"观察 · {why}" if why else "观察"), "tag-neutral"
    return "未确认", "tag-weak"


def _tier_driver_txt(r: dict[str, Any]) -> str:
    """Tier 驱动文本：龙头（贡献度）；单标的 Tier 标「单标的」。

    仅 STRONG/CONFIRMED/WATCH 状态展示驱动；UNCONFIRMED/UNAVAILABLE 显示 —，
    避免把「跌得最多的龙头」误读为趋势驱动。
    """
    state = str(r.get("confirmation_state", TIER_STATE_UNCONFIRMED))
    data_status = str(r.get("data_status", "") or "")
    if data_status == "unavailable" or state in (TIER_STATE_UNCONFIRMED, TIER_STATE_UNAVAILABLE):
        return "—"
    leader = str(r.get("leader_name", "") or "")
    if not leader:
        return "—"
    contrib = r.get("leader_contribution")
    n_total = int(r.get("n_total", 0) or 0)
    if n_total == 1:
        return f"{escape(leader)} · 单标的"
    if contrib is not None and not pd.isna(contrib):
        return f"{escape(leader)} · {_pct(contrib)}"
    return f"{escape(leader)}"


def render_theme_tier_block(
    tier_df: pd.DataFrame,
    theme_key: str,
    confirmation_df: pd.DataFrame | None = None,
) -> str:
    """③ 某主题的 Tier basket 区块：头部状态 + Tier 表
    （Tier/状态/Strength/上涨比例/Trend中位/强趋势/驱动）+ 判断 + 申万交叉证据。

    统一框架（v0.9.1）：所有配置了 tiers 的主题（AI/汽车/高现金流）同构渲染。
    申万行业是 Evidence（非 Gate）。
    """
    df = tier_df[tier_df["theme"] == theme_key].copy() if "theme" in tier_df.columns else tier_df.copy()
    if df.empty:
        return ""

    from . import tier_confirmation as _tc
    agg = _tc.theme_confirmation_from_tiers(df.to_dict("records"))
    state_lbl = str(agg.get("confirmation_state", TIER_STATE_UNCONFIRMED))
    tag = {"BROAD_CONFIRMED": "tag-strong", "CONFIRMED": "tag-strong",
           "NARROW_CONFIRMED": "tag-observe",
           "UNCONFIRMED": "tag-weak", "UNAVAILABLE": "tag-none"}.get(state_lbl, "tag-none")
    n_obs = int(agg.get("n_observe_tiers", 0) or 0)
    n_watch = int(agg.get("n_watch_tiers", 0) or 0)
    n_tiers = int(agg.get("n_tiers", 0) or 0)

    parts: list[str] = []
    # 头部状态行：主题名｜状态 · N/M Tier（label 从 themes config 取，避免依赖 parquet 列）。
    # Theme 层不使用 WATCH：未确认但有观察 Tier 时，badge 补充「N 个 Tier 进入观察」。
    from src.common import themes as _themes
    theme_label = _themes.theme_label(theme_key)
    badge = f"{n_obs}/{n_tiers} Tier"
    if state_lbl == TIER_STATE_UNCONFIRMED and n_watch:
        badge = f"{badge} · {n_watch} 个 Tier 进入观察"
    parts.append(
        f"<h3>{escape(theme_label)}<span class='tag {tag}' style='margin-left:8px'>{escape(state_lbl)}"
        f"</span><span class='badge'>{escape(badge)}</span></h3>")

    # Tier 表：Tier/状态/Strength/上涨比例/Trend中位/强趋势/驱动
    parts.append("<table><thead><tr><th>Tier</th><th>状态</th>"
                 "<th class='num'>Strength</th><th class='num'>上涨比例</th>"
                 "<th class='num'>Trend 中位</th><th class='num'>强趋势</th>"
                 "<th>驱动</th></tr></thead><tbody>")

    for _, r in df.iterrows():
        tier_label = str(r.get("tier_label", r.get("tier", "")))
        strength = r.get("tier_strength")
        advance = r.get("advance_ratio")
        med_trend = r.get("median_trend_score")
        n_strong = int(r.get("n_strong_trend", 0) or 0)
        n_data = int(r.get("n_with_data", 0) or 0)
        n_total = int(r.get("n_total", 0) or 0)

        s_label, s_tag = _tier_state_label(dict(r))
        strength_txt = _num(strength)
        if strength is not None:
            v = float(strength)
            style = "background:#174A7C;color:#FFF;font-weight:600" if v >= 70 \
                else "background:#D79A36;color:#FFF;font-weight:600" if v >= 55 else ""
            strength_txt = f"<span style='{style}'>{_num(strength)}</span>"

        advance_txt = "—"
        if advance is not None and not (isinstance(advance, float) and pd.isna(advance)):
            advance_txt = f"{float(advance) * 100:.0f}%"
        driver_txt = _tier_driver_txt(dict(r))

        # 强趋势 = n_strong/n_total（unavailable 显示 —）
        strong_txt = "—" if n_data == 0 else f"{n_strong}/{n_total}"

        parts.append(
            f"<tr><td style='font-weight:600'>{escape(tier_label)}</td>"
            f"<td><span class='tag {s_tag}'>{escape(s_label)}</span></td>"
            f"<td class='num'>{strength_txt}</td>"
            f"<td class='num'>{advance_txt}</td>"
            f"<td class='num'>{_num(med_trend)}</td>"
            f"<td class='num'>{strong_txt}</td>"
            f"<td>{driver_txt}</td></tr>")

    parts.append("</tbody></table>")

    # 判断：主题链结构的人话结论
    parts.append(f"<div class='verdict'><b>判断：</b>{_theme_tier_verdict(df.to_dict('records'), agg)}</div>")

    # 申万交叉证据（Evidence，非 Gate）
    parts.append(f"<p class='answer-note'><b>申万交叉证据：</b>{_sw_cross_evidence(confirmation_df, theme_key)}</p>")
    return "\n".join(parts)


def _theme_tier_verdict(tier_rows: list[dict[str, Any]], agg: dict[str, Any]) -> str:
    """主题判断：按 Tier 状态生成一句结论。

    已确认链 / 早期走强链 / 尚未跟随，综合为「局部 vs 产业链共振」。
    """
    strong = [r.get("tier_label", r.get("tier", "")) for r in tier_rows
              if r.get("confirmation_state") in (TIER_STATE_STRONG, TIER_STATE_CONFIRMED)
              and r.get("data_status") != "unavailable"]
    watch = [r.get("tier_label", r.get("tier", "")) for r in tier_rows
             if r.get("confirmation_state") == TIER_STATE_WATCH and r.get("data_status") != "unavailable"]
    unconf = [r.get("tier_label", r.get("tier", "")) for r in tier_rows
              if r.get("confirmation_state") == TIER_STATE_UNCONFIRMED and r.get("data_status") != "unavailable"]

    parts: list[str] = []
    if strong:
        parts.append(f"当前仅 {escape('、'.join(strong))} 形成确认")
        if watch:
            parts.append(f"{escape('、'.join(watch))} 出现早期走强迹象")
        if unconf:
            parts.append("其余环节尚未跟随")
        parts.append("属于局部而非整体共振")
    elif watch:
        parts.append(f"{escape('、'.join(watch))} 出现早期走强迹象，尚未形成正式确认")
        if unconf:
            parts.append("其余环节尚未跟随")
    else:
        parts.append("各 Tier 均未进入观察区，尚无整体共振")
    return "，".join(parts) + "。"


def _sw_cross_evidence(confirmation_df: pd.DataFrame | None, theme_key: str) -> str:
    """申万交叉证据：该主题焦点行业进入观察区数量 + 最强行业 RPS15。

    Observation 展示，不参与确认。
    """
    if confirmation_df is None or confirmation_df.empty or "theme" not in confirmation_df.columns:
        return "数据缺失"
    sub = confirmation_df[confirmation_df["theme"] == theme_key]
    if sub.empty:
        return "无焦点行业数据"
    n_total = len(sub)
    n_observe = int(sub["strength_level"].astype(str).isin(["观察", "强势"]).sum())
    rps = pd.to_numeric(sub["RPS15"], errors="coerce")
    top = sub.loc[rps.idxmax()] if not rps.isna().all() else None
    top_txt = f"最强 {top.get('industry_name','')} RPS15={_num(top.get('RPS15'))}" if top is not None else "无 RPS"
    level = "偏强" if n_observe >= max(1, int(round(n_total * 0.5))) else ("中性" if n_observe >= 1 else "偏弱")
    return f"{level} · {n_observe}/{n_total} 相关行业进入观察区，{top_txt}。"


def _industry_theme_state(tdf: pd.DataFrame) -> tuple[str, str, bool]:
    """行业 Evidence 主题的状态标签（Theme 层，v0.9.2 不用 WATCH）。

    返回 (state, 支撑数, near_threshold)：
      - BROAD_CONFIRMED / NARROW_CONFIRMED  有行业进入观察区
      - UNCONFIRMED                         无行业进入观察区
      - near_threshold                      未确认但最强行业已接近观察门（进 verdict 文本，不进 state）
    """
    n_total = len(tdf)
    n_observe = int((tdf["RPS15"] >= 80).sum())
    max_rps = float(pd.to_numeric(tdf["RPS15"], errors="coerce").max()) if n_total else None
    near = max_rps is not None and max_rps >= conf.CONF_WATCH_PROXIMITY
    if n_observe >= 1:
        broad = n_observe >= max(1, int(round(n_total * conf.CONF_BROAD_FRACTION)))
        return ("BROAD_CONFIRMED" if broad else "NARROW_CONFIRMED"), f"{n_observe}/{n_total}", near
    return "UNCONFIRMED", f"{n_observe}/{n_total}", near


def render_industry_theme_block(
    confirmation_df: pd.DataFrame,
    theme_key: str,
    structure_df: pd.DataFrame | None = None,
) -> str:
    """③ 无 Tier 配置主题（如高现金流资产）的行业确认区块。

    头部状态 + 最强行业（RPS15 + 距观察阈值）+ 结论。
    """
    if confirmation_df is None or confirmation_df.empty or "theme" not in confirmation_df.columns:
        return ""
    tdf = confirmation_df[confirmation_df["theme"] == theme_key].copy()
    if tdf.empty:
        return ""
    theme_label = str(tdf.iloc[0].get("theme_label", theme_key) or theme_key)
    state, sup, near = _industry_theme_state(tdf)
    tag = {"BROAD_CONFIRMED": "tag-strong", "NARROW_CONFIRMED": "tag-observe",
           "UNCONFIRMED": "tag-weak"}.get(state, "tag-none")
    state_lbl = {"BROAD_CONFIRMED": "广泛确认", "NARROW_CONFIRMED": "窄幅确认",
                 "UNCONFIRMED": "未确认"}.get(state, state)

    parts: list[str] = []
    parts.append(
        f"<h3>{escape(theme_label)}<span class='tag {tag}' style='margin-left:8px'>{escape(state_lbl)}"
        f"</span><span class='badge'>{sup} 行业</span></h3>")

    rps = pd.to_numeric(tdf["RPS15"], errors="coerce")
    if not rps.isna().all():
        top = tdf.loc[rps.idxmax()]
        top_name = str(top.get("industry_name", ""))
        top_rps = float(top["RPS15"])
        gap = max(0.0, 80.0 - top_rps)
        if state in ("BROAD_CONFIRMED", "NARROW_CONFIRMED"):
            verdict = f"最强 {top_name} RPS15={_num(top_rps)}，已形成行业确认。"
        elif near:
            # Theme 层 state 不用 WATCH：接近观察门作为 Evidence 描述进入结论
            verdict = f"未确认 · {top_name} RPS15={_num(top_rps)} 接近观察门（距阈值 {_num(gap)}）。"
        else:
            verdict = f"最强：{top_name} RPS15={_num(top_rps)}，距观察阈值 {_num(gap)}；尚无行业进入观察区。"
        parts.append(f"<div class='verdict'>{escape(verdict)}</div>")
    else:
        parts.append("<div class='verdict'>无有效行业数据</div>")
    return "\n".join(parts)


def render_theme_support(
    tier_df: pd.DataFrame | None,
    confirmation_df: pd.DataFrame | None,
    structure_df: pd.DataFrame | None = None,
) -> str:
    """③ 我的主题获得哪些支撑？— 统一入口（v0.9.1）。

    统一框架：所有配置了 tiers 的主题都走「Theme → Tier basket → 个股趋势 →
    Theme confirmation → 申万行业 Evidence」，逐主题渲染 Tier 区块。
    申万行业对全部主题都是 Evidence（非 Gate）。
    """
    from src.common import themes as themes_cfg
    parts: list[str] = []

    if tier_df is not None and not tier_df.empty and "theme" in tier_df.columns:
        for theme_key in sorted(tier_df["theme"].astype(str).unique()):
            block = render_theme_tier_block(tier_df, theme_key, confirmation_df)
            if block:
                parts.append(block)
    elif confirmation_df is not None and not confirmation_df.empty and "theme" in confirmation_df.columns:
        # 无 tier 产物时降级：逐主题渲染行业证据区块（不阻塞报告）
        for theme_key in sorted(confirmation_df["theme"].astype(str).unique()):
            block = render_industry_theme_block(confirmation_df, theme_key, structure_df)
            if block:
                parts.append(block)

    if not parts:
        return "<p>主题确认尚未生成——运行 <code>confirm</code> 后可见主题支撑详情。</p>"
    return "\n".join(parts)


def _rps_color(val: Any) -> str:
    try:
        v = float(val)
        if pd.isna(v):
            return ""
        if v >= 90:
            return "background:#174A7C;color:#FFFFFF;font-weight:600"
        if v >= 80:
            return "background:#D79A36;color:#FFFFFF;font-weight:600"
        if v >= 70:
            return "background:#DDEFF8;color:#174A7C"
        return ""
    except (TypeError, ValueError):
        return ""


def _tag(text: str, cls: str = "") -> str:
    return f"<span class='tag {cls}'>{escape(str(text))}</span>"


# ─────────────────────────────────────────────────────────────
# 完整确认证据（折叠区）
# ─────────────────────────────────────────────────────────────

def render_confirmation_details(
    confirmation_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
    structure_df: pd.DataFrame | None = None,
) -> str:
    """完整确认证据：①②③④ 四段，供主报告第三问 <details> 折叠。"""
    if confirmation_df is None or confirmation_df.empty:
        return "<p>无确认数据</p>"
    ev = build_confirmation_evidence(confirmation_df, metrics_df)
    parts: list[str] = []
    parts.append(_render_section1_judgment(confirmation_df, ev))
    parts.append(_render_section2_evidence(confirmation_df))
    parts.append(_render_section3_driver(confirmation_df, structure_df))
    parts.append(_render_section4_divergence(ev))
    return "\n".join(parts)


def _render_section1_judgment(focus_df: pd.DataFrame, ev: dict[str, Any]) -> str:
    resonance = ev["resonance"]
    bucket_resonance = ev["bucket_resonance"]
    theme_resonance = ev["theme_resonance"]
    n_core = int((focus_df["relevance"] == "core").sum()) if not focus_df.empty else 0
    n_rel = len(focus_df) - n_core
    p: list[str] = []
    p.append("<h3>① 主题确认判断</h3>")
    p.append("<div class='judgment'>")
    p.append(f'焦点行业数：<b>{len(focus_df)}</b>（核心 {n_core} · 相关 {n_rel}）<br>')
    p.append(f'进入强势区(≥90)：<b>{resonance.get("n_strong", 0)}</b> · '
             f'进入观察区(≥80)：<b>{resonance.get("n_observe", 0)}</b> · '
             f'核心强势：<b>{resonance.get("n_core_strong", 0)}</b><br>')
    p.append(f'群 RPS15 中位：<b>{_num(resonance.get("group_median_rps15"))}</b> · '
             f'群 ΔRPS15：{_sign(resonance.get("group_median_delta_rps15"))}</div>')
    p.append(f"<div class='verdict'><b>这一层判断：</b>{resonance.get('verdict', '—')}</div>")
    if bucket_resonance:
        p.append("<table><thead><tr><th>Bucket</th><th>目标</th><th class='num'>行业数</th>"
                 "<th class='num'>强势</th><th class='num'>观察</th><th class='num'>中位RPS15</th>"
                 "<th>状态</th></tr></thead><tbody>")
        for br in bucket_resonance:
            tag = RESONANCE_TAG.get(br["status"], "tag-none")
            p.append(
                f"<tr><td><b>{escape(str(br['bucket_label']))}</b></td><td>{escape(str(br.get('objective','')))}</td>"
                f"<td class='num'>{br['n']}</td><td class='num'>{br['n_strong']}</td>"
                f"<td class='num'>{br['n_observe']}</td><td class='num'>{_num(br['median_rps15'])}</td>"
                f"<td><span class='tag {tag}'>{escape(br['status'])}</span> · {escape(br['summary'])}</td></tr>")
        p.append("</tbody></table>")
    if theme_resonance:
        p.append("<h3>主题共振（每 Theme 独立确认）</h3>")
        p.append("<table><thead><tr><th>主题</th><th class='num'>行业数</th>"
                 "<th class='num'>强势</th><th class='num'>观察</th><th class='num'>中位RPS15</th>"
                 "<th class='num'>中位RPS5</th><th class='num'>中位RPS1</th>"
                 "<th class='num'>Δ5</th><th>状态</th><th>确认广度</th></tr></thead><tbody>")
        for tr in theme_resonance:
            tag = RESONANCE_TAG.get(tr["status"], "tag-none")
            p.append(
                f"<tr><td>{escape(tr['theme_label'])}</td><td class='num'>{tr['n']}</td>"
                f"<td class='num'>{tr['n_strong']}</td><td class='num'>{tr['n_observe']}</td>"
                f"<td class='num'>{_num(tr['median_rps15'])}</td>"
                f"<td class='num'>{_num(tr.get('median_rps5'))}</td>"
                f"<td class='num'>{_num(tr.get('median_rps1'))}</td>"
                f"<td class='num'>{_sign(tr.get('median_delta_rps15_5d'))}</td>"
                f"<td><span class='tag {tag}'>{escape(tr['status'])}</span> · {escape(tr['summary'])}</td>"
                f"<td>{escape(str(tr.get('confirmation_breadth','')))}</td></tr>")
        p.append("</tbody></table>")
    return "\n".join(p)


def _render_section2_evidence(focus_df: pd.DataFrame) -> str:
    p: list[str] = []
    p.append("<h3>② 证据明细 · SW 行业</h3>")
    if focus_df.empty:
        p.append("<p>无重点行业数据。</p>")
        return "\n".join(p)
    p.append("<table><thead><tr><th>行业</th><th>Bucket</th><th>主题</th><th>关联</th>"
             "<th class='num'>RPS1</th><th class='num'>RPS5</th><th class='num'>RPS10</th>"
             "<th class='num'>RPS15</th><th class='num'>Δ5</th><th class='num'>ΔRPS15</th>"
             "<th>轮动状态</th><th>强势层级</th><th>驱动分类</th><th class='num'>参与率</th></tr></thead><tbody>")
    for _, r in focus_df.iterrows():
        rt = RELEVANCE_TAG.get(r["relevance_label"], "")
        st = STRENGTH_TAG.get(r["strength_level"], "")
        drive = _dl.composite_drive_label(
            r.get("contribution_structure", ""), r.get("breadth_structure", ""))
        rot_state = r.get("rotation_state", "—")
        rps = r.get("RPS15")
        p.append(
            f"<tr><td>{escape(str(r['industry_name']))}</td>"
            f"<td>{escape(str(r.get('bucket_label','')))}</td>"
            f"<td>{escape(str(r.get('theme_label','')))}</td>"
            f"<td><span class='tag {rt}'>{escape(str(r['relevance_label']))}</span></td>"
            f"<td class='num'>{_num(r['RPS1'])}</td><td class='num'>{_num(r['RPS5'])}</td>"
            f"<td class='num'>{_num(r['RPS10'])}</td>"
            f"<td class='num' style='{_rps_color(rps)}'>{_num(rps)}</td>"
            f"<td class='num'>{_sign(r.get('delta_rps15_5d'))}</td>"
            f"<td class='num'>{_sign(r['delta_rps15'])}</td>"
            f"<td>{escape(str(rot_state))}</td>"
            f"<td><span class='tag {st}'>{escape(str(r['strength_level']))}</span></td>"
            f"<td>{escape(drive)}</td>"
            f"<td class='num'>{_pct(r.get('participation_rate'))}</td></tr>")
    p.append("</tbody></table>")
    return "\n".join(p)


def _render_section3_driver(
    focus_df: pd.DataFrame,
    structure_df: pd.DataFrame | None,
) -> str:
    p: list[str] = []
    p.append("<h3>③ 内部结构（龙头 vs 广泛上涨）</h3>")
    # 优先从 structure 产物取驱动结构；缺失时回退 confirmation 内嵌结构字段
    if structure_df is not None and not structure_df.empty:
        scope_df = structure_df[structure_df["structure_status"].isin(["available"])]
        if scope_df.empty:
            p.append("<p>无已穿透的重点行业驱动结构。</p>")
            return "\n".join(p)
        p.append("<table><thead><tr><th>行业</th><th>驱动模式</th>"
                 "<th class='num'>参与率</th><th class='num'>HHI</th>"
                 "<th class='num'>Top1占比</th><th class='num'>Top3占比</th>"
                 "<th>重构质量</th><th>结构状态</th></tr></thead><tbody>")
        for _, r in scope_df.iterrows():
            # 详情页：驱动模式列显示综合语义（双维数值已由 参与率/HHI/Top1/Top3 列承载）
            driver_label = _dl.composite_drive_label(
                r.get("contribution_structure", ""), r.get("breadth_structure", ""))
            p.append(
                f"<tr><td style='font-weight:600'>{escape(str(r.get('industry_name','')))}</td>"
                f"<td>{escape(driver_label)}</td>"
                f"<td class='num'>{_pct(r.get('participation_rate'))}</td>"
                f"<td class='num'>{_num(r.get('hhi'))}</td>"
                f"<td class='num'>{_pct(r.get('top1_share'))}</td>"
                f"<td class='num'>{_pct(r.get('top3_share'))}</td>"
                f"<td>{escape(str(r.get('reconstruction_quality','') or '—'))}</td>"
                f"<td><span class='tag tag-neutral'>{escape(str(r.get('structure_status','')))}</span></td></tr>")
        p.append("</tbody></table>")
    else:
        # 回退：confirmation 内嵌结构字段（merge_drilldown 已填充）
        drilled = focus_df[focus_df["contribution_structure"].notna()
                           & (focus_df["contribution_structure"] != "")]
        if drilled.empty:
            p.append("<p>当前无已穿透的重点行业。</p>")
        else:
            p.append("<table><thead><tr><th>行业</th><th>驱动模式</th>"
                     "<th class='num'>参与率</th><th class='num'>HHI</th>"
                     "<th class='num'>Top1占比</th><th class='num'>Top3占比</th></tr></thead><tbody>")
            for _, r in drilled.iterrows():
                driver_label = _dl.composite_drive_label(
                    r.get("contribution_structure", ""), r.get("breadth_structure", ""))
                p.append(
                    f"<tr><td style='font-weight:600'>{escape(str(r.get('industry_name','')))}</td>"
                    f"<td>{escape(driver_label)}</td>"
                    f"<td class='num'>{_pct(r.get('participation_rate'))}</td>"
                    f"<td class='num'>{_num(r.get('hhi'))}</td>"
                    f"<td class='num'>{_pct(r.get('top1_share'))}</td>"
                    f"<td class='num'>{_pct(r.get('top3_share'))}</td></tr>")
            p.append("</tbody></table>")
    return "\n".join(p)


def _render_section4_divergence(ev: dict[str, Any]) -> str:
    divergence_map = ev["divergence_map"]
    market = ev["market_context"]
    p: list[str] = []
    p.append("<h3>④ 跨层对照（ETF—行业背离）</h3>")
    p.append(f"<p>全市场中位 RPS15：{_num(market.get('market_median_rps15'))}（{market.get('market_n', 0)} 个二级行业）。"
             f"行业侧近似，完整「ETF vs 行业」双覆盖需接入 ETF 侧数据。</p>")
    if divergence_map:
        p.append("<table><thead><tr><th>主题</th><th class='num'>行业群中位RPS15</th>"
                 "<th class='num'>全市场中位RPS15</th><th class='num'>差值</th><th>状态</th><th>判断</th></tr></thead><tbody>")
        for theme_key, dv in divergence_map.items():
            tag = {"行业支持": "tag-strong", "行业背离": "tag-weak",
                   "中性": "tag-neutral", "无数据": "tag-none"}.get(dv.get("status", ""), "tag-none")
            label = conf.THEMES.get(theme_key, {}).get("label", theme_key)
            p.append(
                f"<tr><td>{escape(label)}</td>"
                f"<td class='num'>{_num(dv.get('group_median_rps15'))}</td>"
                f"<td class='num'>{_num(dv.get('market_median_rps15'))}</td>"
                f"<td class='num'>{_sign(dv.get('gap'))}</td>"
                f"<td><span class='tag {tag}'>{escape(dv.get('status','—'))}</span></td>"
                f"<td>{escape(dv.get('note','—'))}</td></tr>")
        p.append("</tbody></table>")
    else:
        p.append("<p>无主题背离数据。</p>")
    return "\n".join(p)
