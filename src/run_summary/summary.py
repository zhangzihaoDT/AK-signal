"""
Run-day Summary 汇总逻辑（纯函数，不依赖磁盘路径，便于测试）

输入：已解析的 Layer③ selection JSON 与 Opportunity Radar JSON（dict）。
输出：结构化 summary dict + 终端文本 / Markdown renderer。

设计原则：
  - 只读消费，不制造任何新事实；缺产物 → 记入 missing，不报错。
  - 所有数值原样透传（recommended 事实保留原值），不在展示层重算/覆盖。
"""

from __future__ import annotations

from typing import Any

_SCOPE_LABEL = {
    "a_share": "A股",
    "hk": "港股",
    "overseas": "海外",
}


# ---------------------------------------------------------------------------
# 输入抽取
# ---------------------------------------------------------------------------

def _theme_label_map(layer3: dict[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for bucket in layer3.get("buckets") or []:
        for theme in bucket.get("themes") or []:
            key = theme.get("theme")
            if key:
                labels[key] = theme.get("theme_label") or key
    return labels


def _theme_states(layer3: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in layer3.get("buckets") or []:
        for theme in bucket.get("themes") or []:
            today = theme.get("today") or {}
            why = theme.get("why") or {}
            rows.append({
                "theme": theme.get("theme"),
                "theme_label": theme.get("theme_label"),
                "bucket": theme.get("bucket"),
                "bucket_label": theme.get("bucket_label"),
                "confirmed": bool(theme.get("confirmed")),
                "state": today.get("theme_state"),
                "state_label": today.get("theme_state_label"),
                "action": today.get("action"),
                "action_label": today.get("action_label"),
                "expression": today.get("expression"),
                "expression_label": today.get("expression_label"),
                "n_recommended_etf": today.get("n_recommended_etf", 0),
                "eligible_etf_count": today.get("eligible_etf_count"),
                "etf_pool_total": today.get("etf_pool_total"),
                "confirmation_reason": why.get("confirmation_reason"),
            })
    return rows


def _recommendations(layer3: dict[str, Any]) -> list[dict[str, Any]]:
    """今日推荐（去重；优先用引擎 recommended_actions，缺失时扫描 themes 回退）。"""
    labels = _theme_label_map(layer3)
    raw: list[dict[str, Any]] = list(layer3.get("recommended_actions") or [])
    if not raw:
        for bucket in layer3.get("buckets") or []:
            for theme in bucket.get("themes") or []:
                for rec in (theme.get("recommendation") or {}).get("etf") or []:
                    if rec.get("recommended"):
                        raw.append({**rec, "theme": rec.get("theme") or theme.get("theme")})

    recs: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for r in raw:
        if not r.get("recommended", True):
            continue
        key = (r.get("asset_type"), r.get("code"))
        if key in seen:
            continue
        seen.add(key)
        theme_key = str(r.get("theme") or "")
        recs.append({
            "asset_type": r.get("asset_type") or "etf",
            "code": r.get("code"),
            "name": r.get("name"),
            "role": r.get("role"),
            "theme": theme_key or None,
            "theme_label": labels.get(theme_key) or theme_key or None,
            "rps15": r.get("rps15"),
            "rps20": r.get("rps20"),
            "delta_rps15": r.get("delta_rps15"),
            "trend_status": r.get("trend_status"),
            "signal": r.get("signal"),
            "position_level": r.get("position_level"),
            "lane2_target_stage": r.get("lane2_target_stage"),
            "lane3_transition_state": r.get("lane3_transition_state"),
            "reason": r.get("reason"),
        })
    return recs


def _opportunities(radar: dict[str, Any]) -> dict[str, Any]:
    summary = radar.get("summary") or {}
    themes: list[dict[str, Any]] = []
    for t in radar.get("candidate_themes") or []:
        rep = t.get("representative") or {}
        themes.append({
            "candidate_theme_key": t.get("candidate_theme_key"),
            "label": t.get("label"),
            "market_scope": t.get("market_scope"),
            "market_scope_label": _SCOPE_LABEL.get(t.get("market_scope"), t.get("market_scope")),
            "n_etfs": t.get("n_etfs"),
            "median_rps15": t.get("median_rps15"),
            "representative_code": rep.get("fund_code"),
            "representative_name": rep.get("fund_name"),
        })
    themes.sort(key=lambda x: x.get("median_rps15") or -1, reverse=True)

    broad_beta: list[dict[str, Any]] = []
    for x in radar.get("broad_beta") or []:
        broad_beta.append({
            "code": x.get("fund_code"),
            "name": x.get("fund_name"),
            "rps15": x.get("rps15"),
            "rps20": x.get("rps20"),
            "delta_rps15": x.get("delta_rps15"),
            "trend_state": x.get("trend_state"),
            "amount": x.get("amount"),
        })
    broad_beta.sort(key=lambda x: x.get("rps15") or -1, reverse=True)

    return {
        "candidate_themes": themes,
        "broad_beta": broad_beta,
        "opportunity_count": summary.get("opportunity_count"),
        "mapping_gap_count": summary.get("mapping_gap_count"),
        "unclassified_count": summary.get("unclassified_count"),
        "unmapped_count": summary.get("unmapped_count"),
        "full_market_count": summary.get("full_market_count"),
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def build_summary(
    *,
    trade_date: str,
    selection: dict[str, Any] | None,
    radar: dict[str, Any] | None,
) -> dict[str, Any]:
    """把已落盘的 selection / radar 事实汇总为当日结论（纯函数）。"""
    missing: list[str] = []
    if selection is None:
        missing.append("selection: tradable_candidates 缺失（select 未产出）")
    if radar is None:
        missing.append("radar: opportunity_radar 缺失（opportunity-radar 未产出）")

    layer3 = (selection or {}).get("layer3") or {}
    action = layer3.get("action") or {}

    return {
        "trade_date": trade_date,
        "action": {
            "level": action.get("level"),
            "direction_label": action.get("direction_label"),
            "theme_label": action.get("theme_label"),
            "expression_label": action.get("expression_label"),
            "summary": action.get("summary"),
        },
        "theme_states": _theme_states(layer3),
        "recommendations": _recommendations(layer3),
        "opportunities": _opportunities(radar or {}),
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _fmt_rps(v: Any) -> str:
    if isinstance(v, (int, float)):
        return f"{v:.1f}"
    return "—"


def _action_summary(summary: dict[str, Any]) -> str:
    action = summary.get("action") or {}
    txt = str(action.get("summary") or "").strip()
    for prefix in ("今日方向：", "今日方向:"):
        if txt.startswith(prefix):
            txt = txt[len(prefix):].strip()
    return txt


def render_text(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    td = summary.get("trade_date", "")
    bar = "═" * 60
    lines.append(bar)
    lines.append(f"RUN-DAY SUMMARY · {td}")
    lines.append(bar)

    # ① 今日推荐
    lines.append("")
    lines.append("① 今日推荐（Layer③ Decision）")
    action = summary.get("action") or {}
    if action.get("summary"):
        lines.append(f"   方向：{_action_summary(summary)}")
    recs = summary.get("recommendations") or []
    if recs:
        for r in recs:
            code = r.get("code")
            name = r.get("name")
            role = r.get("role") or r.get("asset_type") or ""
            sig = r.get("signal") or "—"
            pos = r.get("position_level") or "—"
            lines.append(
                f"   · {name}（{code}）{role} | RPS15={_fmt_rps(r.get('rps15'))} "
                f"| {sig} | 位置 {pos} | {r.get('theme_label') or ''}"
            )
    else:
        lines.append("   今日无推荐（无主题达到可执行条件）")

    unconfirmed = [t for t in (summary.get("theme_states") or []) if not t.get("confirmed")]
    if unconfirmed:
        names = "、".join(t.get("theme_label") or t.get("theme") or "?" for t in unconfirmed)
        lines.append(f"   [未确认，仅观察] {names}")

    # ② 观察范围外机会
    lines.append("")
    lines.append("② 观察范围外机会（Radar Observation / Discovery）")
    opp = summary.get("opportunities") or {}
    themes = opp.get("candidate_themes") or []
    if opp.get("opportunity_count") is not None:
        lines.append(
            f"   未映射 {opp.get('unmapped_count')} 只 → 机会 {opp.get('opportunity_count')} 只 "
            f"| 候选方向 {len(themes)} 个"
        )
    if themes:
        for t in themes:
            rep = ""
            if t.get("representative_name"):
                rep = f" → {t.get('representative_name')}({t.get('representative_code')})"
            lines.append(
                f"   · {t.get('label')} [{t.get('market_scope_label')}] "
                f"{t.get('n_etfs')}只 中位RPS15={_fmt_rps(t.get('median_rps15'))}{rep}"
            )
    else:
        lines.append("   无（未发现 Theme 外强势方向）")
    if opp.get("unclassified_count"):
        lines.append(f"   未分类 {opp.get('unclassified_count')} 只")

    # ③ 宽基指数值得研究
    lines.append("")
    lines.append("③ 宽基指数值得研究（Radar Market Beta）")
    bb = opp.get("broad_beta") or []
    if bb:
        for x in bb:
            lines.append(
                f"   · {x.get('name')}（{x.get('code')}）RPS15={_fmt_rps(x.get('rps15'))} "
                f"| {x.get('trend_state') or '—'}"
            )
    else:
        lines.append("   无（当日无宽基/区域 ETF 通过机会门）")

    if summary.get("missing"):
        lines.append("")
        lines.append("⚠ 缺失产物：" + "；".join(summary["missing"]))
    lines.append("")
    return "\n".join(lines)


def render_markdown(summary: dict[str, Any]) -> str:
    td = summary.get("trade_date", "")
    lines: list[str] = [f"# Run-day Summary · {td}", ""]

    action = summary.get("action") or {}
    lines.append("## ① 今日推荐（Layer③ Decision）")
    lines.append("")
    if action.get("summary"):
        lines.append(f"**方向**：{_action_summary(summary)}")
        lines.append("")
    recs = summary.get("recommendations") or []
    if recs:
        lines.append("| 标的 | 角色 | 主题 | RPS15 | 信号 | 位置 |")
        lines.append("|---|---|---|---|---|---|")
        for r in recs:
            lines.append(
                f"| {r.get('name')}（{r.get('code')}） | {r.get('role') or '-'} "
                f"| {r.get('theme_label') or '-'} | {_fmt_rps(r.get('rps15'))} "
                f"| {r.get('signal') or '-'} | {r.get('position_level') or '-'} |"
            )
    else:
        lines.append("今日无推荐（无主题达到可执行条件）。")
    lines.append("")
    unconfirmed = [t for t in (summary.get("theme_states") or []) if not t.get("confirmed")]
    if unconfirmed:
        names = "、".join(t.get("theme_label") or t.get("theme") or "?" for t in unconfirmed)
        lines.append(f"> 未确认，仅观察：{names}")
        lines.append("")

    opp = summary.get("opportunities") or {}
    lines.append("## ② 观察范围外机会（Radar Observation / Discovery）")
    lines.append("")
    themes = opp.get("candidate_themes") or []
    if themes:
        lines.append("| 候选方向 | 市场 | 只数 | 中位 RPS15 | 代表 |")
        lines.append("|---|---|---|---|---|")
        for t in themes:
            rep = t.get("representative_name") or "-"
            if t.get("representative_code"):
                rep = f"{rep}（{t.get('representative_code')}）"
            lines.append(
                f"| {t.get('label')} | {t.get('market_scope_label')} | {t.get('n_etfs')} "
                f"| {_fmt_rps(t.get('median_rps15'))} | {rep} |"
            )
    else:
        lines.append("无（未发现 Theme 外强势方向）。")
    lines.append("")

    lines.append("## ③ 宽基指数值得研究（Radar Market Beta）")
    lines.append("")
    bb = opp.get("broad_beta") or []
    if bb:
        lines.append("| 代码 | 名称 | RPS15 | 状态 |")
        lines.append("|---|---|---|---|")
        for x in bb:
            lines.append(
                f"| {x.get('code')} | {x.get('name')} | {_fmt_rps(x.get('rps15'))} "
                f"| {x.get('trend_state') or '-'} |"
            )
    else:
        lines.append("无。")
    lines.append("")

    if summary.get("missing"):
        lines.append("## ⚠ 缺失产物")
        lines.append("")
        for m in summary["missing"]:
            lines.append(f"- {m}")
        lines.append("")
    return "\n".join(lines)
