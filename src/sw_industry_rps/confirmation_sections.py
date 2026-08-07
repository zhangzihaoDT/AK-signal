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
            "WATCH": "tag-neutral", "UNCONFIRMED": "tag-weak",
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

    基于确认状态 + 最强支撑行业 + 最接近确认行业生成一句结论。
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
    if state == "WATCH":
        if top is not None and not _isna(top.get("RPS15")):
            name = str(top.get("industry_name", ""))
            gap = max(0.0, 80.0 - float(top.get("RPS15") or 0))
            return f"最强 {name} 接近观察门，差 {_num(gap)}，仍需走强确认"
    # UNCONFIRMED
    if top is not None and not _isna(top.get("RPS15")):
        name = str(top.get("industry_name", ""))
        rps = _num(top.get("RPS15"))
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
            "WATCH": "接近确认", "UNCONFIRMED": "未确认",
        }.get(state, state)
        state_tag = {
            "BROAD_CONFIRMED": "tag-strong", "NARROW_CONFIRMED": "tag-observe",
            "WATCH": "tag-neutral", "UNCONFIRMED": "tag-weak",
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
