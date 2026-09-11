"""Layer ② 报告侧「个股观察」视图（Observability / 展示层，不制造事实）。

把已落盘的 `stock_metrics_{date}.parquet` 及其最近若干交易日快照，转换为主报告的
两层个股展示：
  - 主代表：主题内 trend_score 最高的个股（沿用现有 Tier「驱动」语义）
  - 显著转强：其他在「近期窗口」出现明显状态跃迁的个股
    （score 首次过观察门 / RS20 转正 / 趋势分大幅上升 / 收复 MA60 / 趋势等级上调）

严格边界：
  - 只读 stock_metrics（Observation 产物）与 selection_universe（资产池），不联网；
  - 不重算任何指标、不改变 BUY / Risk Gate / Theme Gate；
  - 所有判断都是「近期状态变化」的展示性分类，不写回任何事实产物。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from src.common.paths import selection_universe_path, stock_metrics_dir
from src.selection.universe import load_universe_items
from src.trend_engine import inputs as trend_inputs

logger = logging.getLogger("sw_industry_rps.stock_emerging")

CONFIRM_SCORE = 70.0          # 观察门（与生产 stock_selection.trend.qualified_score 一致，仅展示语义）
SCORE_JUMP = 20.0             # 趋势分「大幅上升」阈值
LOOKBACK_DAYS = 5             # 「近期」窗口（交易日）
MIN_EMERGING_SCORE = 50.0     # 「显著转强」当前分下限（避免把回落/弱分误标为转强）
MAX_EMERGING_PER_THEME = 5

# 转强原因码 → 人话标签
REASON_LABELS = {
    "cross_70": "首次过观察门",
    "rs_turn": "RS20 转正",
    "score_jump": "趋势分大幅上升",
    "reclaim_ma60": "收复 MA60",
    "watch_up": "趋势等级上调",
}


def _load_diag(v: Any) -> dict[str, Any]:
    if not isinstance(v, str) or not v.strip():
        return {}
    try:
        d = json.loads(v)
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _trend_flags(diag: dict[str, Any]) -> set[str]:
    trend = (diag.get("trend") or {}).get("flags") or []
    return set(str(x) for x in trend)


def _rs_negative(diag: dict[str, Any]) -> bool:
    rs = (diag.get("relative_strength") or {}).get("flags") or []
    return "rs_negative" in {str(x) for x in rs}


def _score_of(r: pd.Series) -> float | None:
    v = r.get("score_trend")
    if v is None or pd.isna(v):
        return None
    return float(v)


def _recent_metric_dates(trade_date: str, n: int) -> list[str]:
    d = stock_metrics_dir()
    dates: list[str] = []
    for p in d.glob("stock_metrics_*.parquet"):
        stem = p.name[len("stock_metrics_"):-len(".parquet")]
        if len(stem) == 8 and stem.isdigit() and stem <= trade_date:
            dates.append(stem)
    return sorted(dates)[-(n + 1):]


def _theme_members() -> dict[str, list[tuple[str, str, str]]]:
    """theme → [(symbol, name, tier)]，仅股票、排除 monitor_only。"""
    items = load_universe_items(selection_universe_path())
    out: dict[str, list[tuple[str, str, str]]] = {}
    seen: dict[str, set[str]] = {}
    for it in trend_inputs.stock_items(items):
        if str(it.participation) == "monitor_only":
            continue
        theme = it.theme
        seen.setdefault(theme, set())
        if it.asset.symbol in seen[theme]:
            continue
        seen[theme].add(it.asset.symbol)
        out.setdefault(theme, []).append((it.asset.symbol, it.asset.name, it.tier))
    return out


def _transitions(rows: list[pd.Series]) -> tuple[list[str], int | None]:
    """在近期窗口 rows（升序，最后一行=当日）内检测状态跃迁。

    Returns (reasons, age_days)：age_days = 最近一次跃迁距当日交易日数（0=当日）。
    """
    if len(rows) < 2:
        return [], None
    cur = rows[-1]
    score = _score_of(cur)
    if score is None:
        return [], None
    reasons: list[str] = []
    ages: list[int] = []
    diag = _load_diag(cur.get("technical_diagnostics"))
    wl = str(cur.get("watch_level", "") or "")
    n = len(rows)

    # 首次过观察门：窗口内某日 <70 且当日 >=70
    if score >= CONFIRM_SCORE:
        for i in range(1, n):
            s_prev = _score_of(rows[i - 1])
            s_cur = _score_of(rows[i])
            if s_prev is not None and s_cur is not None and s_prev < CONFIRM_SCORE <= s_cur:
                reasons.append("cross_70")
                ages.append(n - 1 - i)
                break

    # RS20 转正
    if not _rs_negative(diag) and any(_rs_negative(_load_diag(rows[i].get("technical_diagnostics")))
                                     for i in range(n - 1)):
        reasons.append("rs_turn")
        for i in range(n - 2, -1, -1):
            if _rs_negative(_load_diag(rows[i].get("technical_diagnostics"))):
                ages.append(n - 1 - i)
                break

    # 趋势分大幅上升（单日跳升，且当日未回落）
    for i in range(1, n):
        s_prev = _score_of(rows[i - 1])
        s_cur = _score_of(rows[i])
        if s_prev is not None and s_cur is not None and (s_cur - s_prev) >= SCORE_JUMP \
                and score >= s_cur:
            reasons.append("score_jump")
            ages.append(n - 1 - i)
            break

    # 收复 MA60
    if "below_ma60" not in _trend_flags(diag) and any(
            "below_ma60" in _trend_flags(_load_diag(rows[i].get("technical_diagnostics")))
            for i in range(n - 1)):
        reasons.append("reclaim_ma60")
        for i in range(n - 2, -1, -1):
            if "below_ma60" in _trend_flags(_load_diag(rows[i].get("technical_diagnostics"))):
                ages.append(n - 1 - i)
                break

    # 趋势等级上调
    if wl in ("S", "A") and any(str(rows[i].get("watch_level", "") or "") in ("B", "C")
                                for i in range(n - 1)):
        reasons.append("watch_up")
        for i in range(n - 2, -1, -1):
            if str(rows[i].get("watch_level", "") or "") in ("B", "C"):
                ages.append(n - 1 - i)
                break

    age = min(ages) if ages else None
    return reasons, age


def build_theme_stock_view(trade_date: str) -> dict[str, dict[str, Any]]:
    """构建 theme → {main, emerging}。任何缺失都 fail-soft（返回 {}）。"""
    dates = _recent_metric_dates(trade_date, LOOKBACK_DAYS)
    if not dates:
        return {}
    frames: dict[str, pd.DataFrame] = {}
    for d in dates:
        try:
            df = trend_inputs.load_stock_metrics(d)
        except Exception as e:  # noqa: BLE001
            logger.warning("stock_metrics load failed for %s: %s", d, e)
            continue
        if df is not None and not df.empty:
            frames[d] = df
    if not frames:
        return {}
    cur_date = max(frames)
    cur = frames[cur_date]

    # symbol → 按时间升序的 rows（含当日）
    hist: dict[str, list[pd.Series]] = {}
    for d in sorted(frames):
        for _, r in frames[d].iterrows():
            hist.setdefault(str(r["symbol"]), []).append(r)

    members = _theme_members()
    out: dict[str, dict[str, Any]] = {}
    for theme, mem in members.items():
        cur_idx: dict[str, pd.Series] = {str(r["symbol"]): r for _, r in cur.iterrows()}
        scored: list[tuple[float, str, str, str]] = []
        for sym, name, tier in mem:
            r = cur_idx.get(sym)
            if r is None:
                continue
            sc = _score_of(r)
            if sc is None:
                continue
            scored.append((sc, sym, name, tier))
        if not scored:
            continue
        scored.sort(reverse=True)
        top_score, top_sym, top_name, top_tier = scored[0]
        top_row = cur_idx[top_sym]
        main = {
            "symbol": top_sym, "name": top_name, "tier": top_tier,
            "score": top_score, "watch_level": str(top_row.get("watch_level", "") or ""),
        }
        emerging: list[dict[str, Any]] = []
        for sc, sym, name, tier in scored[1:]:
            if sc < MIN_EMERGING_SCORE:
                continue
            rows = hist.get(sym) or []
            reasons, age = _transitions(rows)
            if not reasons:
                continue
            emerging.append({
                "symbol": sym, "name": name, "tier": tier, "score": sc,
                "watch_level": str(cur_idx[sym].get("watch_level", "") or ""),
                "reasons": reasons, "age_days": age,
            })
        emerging.sort(key=lambda x: ((x["age_days"] if x["age_days"] is not None else 99), -x["score"]))
        emerging = emerging[:MAX_EMERGING_PER_THEME]
        out[theme] = {"main": main, "emerging": emerging}
    logger.info("theme stock view: %d themes (window=%s..%s)", len(out), min(frames), cur_date)
    return out


def render_stock_watch(view: dict[str, Any] | None) -> str:
    """把单个主题的 view 渲染成一行「个股观察」（纯展示）。"""
    if not view:
        return ""
    from html import escape
    parts: list[str] = []
    main = view.get("main")
    if main:
        wl = f"·{main['watch_level']}" if main.get("watch_level") else ""
        parts.append(
            f"<span class='tag tag-strong'>主代表</span> "
            f"{escape(str(main['name']))}（{main['score']:.0f}{escape(wl)}）")
    for e in view.get("emerging", []):
        age = e.get("age_days")
        when = "当日" if age == 0 else (f"近{age}日" if age is not None else "近期")
        labels = "、".join(str(REASON_LABELS.get(r, r) or r) for r in e.get("reasons", []))
        parts.append(
            f"<span class='tag tag-observe' style='background:#FCEFD8;color:#8a5a1d'>显著转强</span> "
            f"{escape(str(e['name']))}（{e['score']:.0f} · {escape(when)} {escape(labels)}）")
    if not parts:
        return ""
    return ("<div class='stock-watch' style='margin-top:8px;font-size:13px'>"
            "<b>个股观察：</b>" + " &nbsp;|&nbsp; ".join(parts) +
            "<div style='color:var(--zh-muted,#6B7C8F);font-size:12px;margin-top:2px'>"
            "仅展示个股趋势变化（Observation），不代表主题确认或买入推荐。</div></div>")
