"""Layer③ 报告 05 变化日志（report_changes）。

职责：生成「与昨日相比」的变化条目 + 主题变化标记（changed_themes）。
纪律（冻结于 P0）：
  - 变化 = 历史已落盘事实之间的比较（今日 JSON vs 上一份 Layer③ JSON），
    不联网、不重算、绝不写回 recommendation 对象；只进 ReportViewModel。
  - previous 解析：按文件内 trade_date 取 `date < current.selection_date` 的最近一份，
    不用 mtime。
  - fail-soft：上一份 JSON 缺失/损坏 → comparison_status=UNAVAILABLE，绝不阻塞 HTML；
    version 不兼容 → comparison_status=VERSION_MISMATCH，不做结构 diff。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

COMPARISON_OK = "OK"
COMPARISON_UNAVAILABLE = "UNAVAILABLE"
COMPARISON_VERSION_MISMATCH = "VERSION_MISMATCH"
COMPARISON_NO_PREV = "NO_PREV"


@dataclass
class ChangeEntry:
    severity: str          # up / down / info
    kind: str              # degraded / confirmed / breakdown / recommended / removed / data / fallback ...
    theme_label: str
    text: str


def _flatten_themes(recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in recommendation.get("buckets", []):
        for t in b.get("themes", []):
            out.append(t)
    return out


def _theme_map(recommendation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t.get("theme", ""): t for t in _flatten_themes(recommendation)}


def _recommended_keys(theme: dict[str, Any]) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for a in theme.get("recommendation", {}).get("etf", []) + theme.get("recommendation", {}).get("stocks", []):
        if a.get("recommended"):
            out.add((str(a.get("asset_type", "")), str(a.get("code", ""))))
    return out


def _monitoring_assets(theme: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tier in ("leaders", "high_beta", "equipment"):
        for a in theme.get("monitoring", {}).get(tier, []):
            out.append(a)
    return out


def _within_day_entries(themes: list[dict[str, Any]]) -> list[ChangeEntry]:
    """今日值得注意（无跨日对比也能产出）：表达降级 / 产品端不可执行 / 新增破位。"""
    out: list[ChangeEntry] = []
    for t in themes:
        label = t.get("theme_label", "")
        status = str(t.get("expression_status", "") or "")
        if status == "DEGRADED":
            structural = t.get("structural_expression", "")
            execution = t.get("execution_expression", "")
            out.append(ChangeEntry(
                "down", "degraded", label,
                f"表达降级：结构 {structural} → 实际 {execution}"))
        e0 = int(t.get("eligible_etf_count", 0) or 0)
        tot = int(t.get("etf_pool_total", 0) or 0)
        if tot > 0 and e0 == 0:
            out.append(ChangeEntry("down", "fallback", label,
                                   f"ETF {e0}/{tot} 无一通过交易门"))
        for a in _monitoring_assets(t):
            if a.get("position_level") == "BREAKDOWN":
                out.append(ChangeEntry("down", "breakdown", label,
                                       f"{a.get('name', '')} 处于中期破位（乖离 {_fmt_pct(a.get('position_pct'))}）"))
    return out


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):g}%"
    except (TypeError, ValueError):
        return str(v)


def _cross_day_entries(
    cur: dict[str, Any],
    prev: dict[str, Any],
) -> tuple[list[ChangeEntry], set[str]]:
    """上一份 Layer③ JSON（build_recommendation 结构）→ 主题/资产级变化。"""
    entries: list[ChangeEntry] = []
    changed: set[str] = set()
    cur_map = _theme_map(cur)
    prev_map = _theme_map(prev)

    for theme, t in cur_map.items():
        label = t.get("theme_label", "")
        p = prev_map.get(theme)
        if p is None:
            continue  # 主题不在上一份（配置变化），不误报
        cur_conf = bool(t.get("confirmed", False))
        prev_conf = bool(p.get("confirmed", False))
        if cur_conf and not prev_conf:
            entries.append(ChangeEntry("up", "confirmed", label, "主题进入确认状态"))
            changed.add(theme)
        elif (not cur_conf) and prev_conf:
            entries.append(ChangeEntry("down", "unconfirmed", label, "主题退出确认状态"))
            changed.add(theme)

        cur_status = str(t.get("expression_status", "") or "")
        prev_status = str(p.get("expression_status", "") or "")
        if cur_status == "DEGRADED" and prev_status != "DEGRADED":
            entries.append(ChangeEntry("down", "degraded", label,
                                       f"新增表达降级（结构 {t.get('structural_expression', '')} → 实际 {t.get('execution_expression', '')}）"))
            changed.add(theme)
        elif cur_status != "DEGRADED" and prev_status == "DEGRADED":
            entries.append(ChangeEntry("up", "recovered", label, "表达降级解除"))
            changed.add(theme)
        if str(t.get("execution_expression", "")) != str(p.get("execution_expression", "")):
            entries.append(ChangeEntry("info", "expression_switch", label,
                                       f"实际表达切换：{p.get('execution_expression', '—')} → {t.get('execution_expression', '—')}"))

        cur_rec = _recommended_keys(t)
        prev_rec = _recommended_keys(p)
        new_rec = cur_rec - prev_rec
        gone_rec = prev_rec - cur_rec
        name_by_code = {a.get("code"): a.get("name") for a in _monitoring_assets(t)}
        for asset_type, code in sorted(new_rec):
            nm = name_by_code.get(code, code)
            entries.append(ChangeEntry("up", "recommended", label, f"新增可执行标的 {nm}"))
            changed.add(theme)
        for asset_type, code in sorted(gone_rec):
            entries.append(ChangeEntry("down", "removed", label, f"标的退出推荐（code {code}）"))

        # 新增中期破位（今日破位但昨日未破位）
        prev_breakdown = {a.get("code") for a in _monitoring_assets(p) if a.get("position_level") == "BREAKDOWN"}
        for a in _monitoring_assets(t):
            if a.get("position_level") == "BREAKDOWN" and a.get("code") not in prev_breakdown:
                entries.append(ChangeEntry("down", "breakdown", label,
                                           f"{a.get('name', '')} 新增中期破位（乖离 {_fmt_pct(a.get('position_pct'))}）"))
                changed.add(theme)

    return entries, changed


def build_changes(
    recommendation: dict[str, Any],
    prev: dict[str, Any] | None = None,
) -> tuple[list[ChangeEntry], set[str], str]:
    """生成变化条目 + changed_themes + comparison_status。

    prev：上一份 Layer③ 的 build_recommendation 结构（或 None）。
    跨日 diff 只在 prev 兼容时执行；失败一律 fail-soft。
    """
    themes = _flatten_themes(recommendation)
    entries: list[ChangeEntry] = _within_day_entries(themes)
    changed: set[str] = set()
    status = COMPARISON_NO_PREV

    if prev is not None:
        try:
            if recommendation.get("version") != prev.get("version") or (
                    (recommendation.get("engine") or {}).get("version")
                    != (prev.get("engine") or {}).get("version")):
                status = COMPARISON_VERSION_MISMATCH
            else:
                xd, xchanged = _cross_day_entries(recommendation, prev)
                entries.extend(xd)
                changed.update(xchanged)
                status = COMPARISON_OK
        except Exception:
            status = COMPARISON_UNAVAILABLE  # fail-soft：损坏/结构不符 → 不阻塞 HTML

    return entries, changed, status


def resolve_previous_path(
    output_dir: Path,
    current_date: str,
) -> Path | None:
    """按文件内/文件名 trade_date 选 `date < current_date` 的最近一份（不用 mtime）。"""
    import re as _re

    current = int(current_date)
    best: tuple[int, Path] | None = None
    pattern = _re.compile(r"tradable_candidates_(\d{8})\.json$")
    if output_dir.is_dir():
        for f in output_dir.iterdir():
            m = pattern.search(f.name)
            if not m:
                continue
            try:
                d = int(m.group(1))
            except ValueError:
                continue
            if d < current and (best is None or d > best[0]):
                best = (d, f)
    return best[1] if best else None


def load_previous(recommendation_path: Path) -> dict[str, Any] | None:
    """读取上一份 Layer③ JSON 的 layer3 内容；损坏/缺 key → None（fail-soft）。"""
    try:
        import json

        raw = json.loads(recommendation_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "layer3" in raw and isinstance(raw["layer3"], dict):
            return raw["layer3"]
        if isinstance(raw, dict) and "buckets" in raw:
            return raw  # 已是 build_recommendation 结构
    except Exception:
        return None
    return None
