from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


SUMMARY_COLUMN_ORDER = [
    "name",
    "symbol",
    "market",
    "exchange",
    "currency",
    "category",
    "data_source",
    "data_freshness_days",
    "date",
    "close",
    "score_trend",
    "label",
    "watch_level",
    "action",
    "change",
    "relative_strength_20d",
    "risk_flags",
    "reason",
    "note",
]


def public_summary_df(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return summary_df
    return summary_df.drop(columns=["score"], errors="ignore").copy()



def reorder_summary_df(summary_df: pd.DataFrame) -> pd.DataFrame:
    if summary_df.empty:
        return summary_df
    cols_in_order = [c for c in SUMMARY_COLUMN_ORDER if c in summary_df.columns]
    rest = [c for c in summary_df.columns if c not in cols_in_order]
    return summary_df[cols_in_order + rest].copy()


def load_previous_summary(out_dir: Path, report_date: date) -> pd.DataFrame:
    if not out_dir.exists():
        return pd.DataFrame()

    candidates: list[tuple[date, Path]] = []
    for p in out_dir.glob("trend_report_*.csv"):
        stem = p.stem
        parts = stem.split("_")
        if not parts:
            continue
        ymd = parts[-1]
        if len(ymd) != 8 or not ymd.isdigit():
            continue
        try:
            d = date(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]))
        except ValueError:
            continue
        if d < report_date:
            candidates.append((d, p))

    if not candidates:
        return pd.DataFrame()

    _, latest_path = sorted(candidates, key=lambda x: x[0])[-1]
    try:
        return pd.read_csv(latest_path, dtype={"symbol": str})
    except Exception:
        return pd.DataFrame()


def build_price_chart(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df["date"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="K线",
            increasing_line_color="#e53935",
            increasing_fillcolor="#e53935",
            decreasing_line_color="#00a65a",
            decreasing_fillcolor="#00a65a",
        )
    )

    for ma_col, name in [("ma20", "MA20"), ("ma60", "MA60"), ("ma120", "MA120")]:
        if ma_col in df.columns:
            fig.add_trace(go.Scatter(x=df["date"], y=df[ma_col], mode="lines", name=name))

    fig.update_layout(
        title=title,
        xaxis_title="Date",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        height=400,
        margin=dict(l=40, r=20, t=40, b=30),
        legend=dict(orientation="h"),
        template="plotly_white",
    )
    return fig


def _watch_level_style(level: str) -> tuple[str, str]:
    mapping = {
        "S": ("#c0392b", "#ffffff"),
        "A": ("#e67e22", "#ffffff"),
        "B": ("#2980b9", "#ffffff"),
        "C": ("#7f8c8d", "#ffffff"),
    }
    bg, fg = mapping.get(level, ("#ecf0f1", "#2c3e50"))
    return bg, fg


def _action_style(action: str) -> tuple[str, str]:
    mapping = {
        "重点观察": ("#8e44ad", "#ffffff"),
        "继续跟踪": ("#f39c12", "#ffffff"),
        "等待突破": ("#2980b9", "#ffffff"),
        "观察等待": ("#95a5a6", "#ffffff"),
        "风险警戒": ("#c0392b", "#ffffff"),
        "剔除观察": ("#7f8c8d", "#ffffff"),
    }
    bg, fg = mapping.get(action, ("#bdc3c7", "#2c3e50"))
    return bg, fg


def _rs_style(rs: float | None) -> tuple[str, str]:
    if rs is None:
        return "#ffffff", "#2c3e50"
    try:
        v = float(rs)
    except Exception:
        return "#ffffff", "#2c3e50"

    if v >= 0.15:
        return "#ffb3b3", "#2c3e50"
    if v >= 0.05:
        return "#ffe0e0", "#2c3e50"
    if v <= -0.15:
        return "#b3d9ff", "#2c3e50"
    if v <= -0.05:
        return "#e6f2ff", "#2c3e50"
    return "#ffffff", "#2c3e50"


def _render_summary_cards(summary: dict[str, object] | None) -> str:
    if not summary:
        return ""

    def _fmt_pct(x: object) -> str:
        if x is None:
            return "—"
        try:
            return f"{float(x) * 100:.2f}%"
        except Exception:
            return "—"

    def _fmt_num(x: object) -> str:
        if x is None:
            return "—"
        try:
            return f"{float(x):.2f}"
        except Exception:
            return "—"

    strongest = summary.get("strongest_stock") or {}
    weakest = summary.get("weakest_stock") or {}
    strongest_text = f"{strongest.get('name','')}({strongest.get('symbol','')})" if strongest else "—"
    weakest_text = f"{weakest.get('name','')}({weakest.get('symbol','')})" if weakest else "—"

    return "\n".join(
        [
            "<div class='cards'>",
            f"<div class='card'><div class='k'>强趋势数量</div><div class='v'>{escape(str(summary.get('strong_count','—')))}</div></div>",
            f"<div class='card'><div class='k'>观察数量</div><div class='v'>{escape(str(summary.get('observe_count','—')))}</div></div>",
            f"<div class='card'><div class='k'>平均 Score</div><div class='v'>{escape(_fmt_num(summary.get('avg_score')))}</div></div>",
            f"<div class='card'><div class='k'>平均相对强度(20D)</div><div class='v'>{escape(_fmt_pct(summary.get('avg_relative_strength_20d')))}</div></div>",
            f"<div class='card'><div class='k'>最强标的</div><div class='v'>{escape(strongest_text)}</div></div>",
            f"<div class='card'><div class='k'>最弱标的</div><div class='v'>{escape(weakest_text)}</div></div>",
            "</div>",
        ]
    )


def _fmt_pct(x: object) -> str:
    if x is None or x == "":
        return "—"
    try:
        v = float(x)
    except Exception:
        return "—"
    if pd.isna(v):
        return "—"
    return f"{v*100:.2f}%"


def _fmt_num(x: object) -> str:
    if x is None or x == "":
        return "—"
    try:
        v = float(x)
    except Exception:
        return "—"
    if pd.isna(v):
        return "—"
    return f"{v:.2f}"


def _short_reason(reason: object, max_len: int = 60) -> str:
    s = "" if reason is None else str(reason)
    s = s.replace("\n", " ").strip()
    if not s:
        return ""
    parts = [p.strip() for p in s.split("；") if p.strip()]
    if parts:
        s2 = "；".join(parts[:2])
    else:
        s2 = s
    if len(s2) <= max_len:
        return s2
    return s2[: max_len - 1] + "…"


def _render_market_overview(summary_df: pd.DataFrame) -> str:
    if summary_df.empty:
        return "<p>无数据</p>"

    df = summary_df.copy()
    wl = df.get("watch_level", pd.Series([], dtype=str)).astype(str)
    strong_count = int(wl.isin(["S", "A"]).sum())
    observe_count = int((wl == "B").sum())

    if "score_trend" in df.columns:
        score = pd.to_numeric(df["score_trend"], errors="coerce")
    elif "score" in df.columns:
        score = pd.to_numeric(df["score"], errors="coerce")
    else:
        score = pd.Series([], dtype="float64")
    avg_score = float(score.dropna().mean()) if score.notna().any() else None

    rs = pd.to_numeric(df.get("relative_strength_20d"), errors="coerce")
    df["_rs"] = rs
    df["_score"] = score
    strongest = df.sort_values(["_score", "_rs"], ascending=[False, False]).iloc[0]
    weakest = df.sort_values(["_score", "_rs"], ascending=[True, True]).iloc[0]

    fresh = pd.to_numeric(df.get("data_freshness_days"), errors="coerce")
    stale = df[(fresh.notna()) & (fresh >= 2)]
    cache = df[df.get("data_source", "").astype(str) == "cache"]

    market = df.get("market", "").astype(str)
    breakdown = market.value_counts().to_dict()
    breakdown_txt = " / ".join([f"{escape(str(k))}:{int(v)}" for k, v in breakdown.items()]) if breakdown else "—"

    freshness_lines: list[str] = []
    if not stale.empty:
        samples = stale.head(5)
        freshness_lines.append(f"存在 {len(stale)} 个标的行情滞后 ≥2 天")
        for _, r in samples.iterrows():
            freshness_lines.append(
                f"{escape(str(r.get('name','')))}({escape(str(r.get('symbol','')))}): {escape(str(int(float(r.get('data_freshness_days')))))} 天"
            )
    if not cache.empty:
        samples = cache.head(5)
        freshness_lines.append(f"存在 {len(cache)} 个标的使用缓存数据")
        for _, r in samples.iterrows():
            freshness_lines.append(f"{escape(str(r.get('name','')))}({escape(str(r.get('symbol','')))}): cache")
    if not freshness_lines:
        freshness_lines.append("数据新鲜度：正常")

    freshness_html = "<br/>".join(freshness_lines)

    return "\n".join(
        [
            "<div class='cards'>",
            f"<div class='card'><div class='k'>强趋势数量</div><div class='v'>{strong_count}</div><div class='s'>{breakdown_txt}</div></div>",
            f"<div class='card'><div class='k'>观察数量</div><div class='v'>{observe_count}</div><div class='s'>B 桶</div></div>",
            f"<div class='card'><div class='k'>平均趋势分</div><div class='v'>{escape('—' if avg_score is None else f'{avg_score:.2f}')}</div><div class='s'>全市场</div></div>",
            f"<div class='card'><div class='k'>最强标的</div><div class='v'>{escape(str(strongest.get('name','')))}</div><div class='s'>{escape(str(strongest.get('symbol','')))} | {escape(str(strongest.get('market','')))} | score {escape(str(strongest.get('score_trend','')))}</div></div>",
            f"<div class='card'><div class='k'>最弱标的</div><div class='v'>{escape(str(weakest.get('name','')))}</div><div class='s'>{escape(str(weakest.get('symbol','')))} | {escape(str(weakest.get('market','')))} | score {escape(str(weakest.get('score_trend','')))}</div></div>",
            f"<div class='card'><div class='k'>数据新鲜度提醒</div><div class='v' style='font-size:13px;line-height:1.45'>{freshness_html}</div></div>",
            "</div>",
        ]
    )


def render_compact_signal_table(summary_df: pd.DataFrame) -> str:
    if summary_df.empty:
        return "<p>无数据</p>"

    df = reorder_summary_df(summary_df).copy()
    df["reason_short"] = df.get("reason", "").map(_short_reason)

    cols = [
        "name",
        "symbol",
        "market",
        "close",
        "score_trend",
        "watch_level",
        "action",
        "relative_strength_20d",
        "data_source",
        "data_freshness_days",
        "reason_short",
    ]
    cols = [c for c in cols if c in df.columns]
    df = df[cols].copy()

    ths = "".join([f"<th>{escape(str(c))}</th>" for c in df.columns.tolist()])
    rows_html: list[str] = []
    for _, r in df.iterrows():
        tds: list[str] = []
        for c in df.columns.tolist():
            val = r.get(c, "")
            if pd.isna(val):
                val = ""
            if c == "watch_level":
                bg, fg = _watch_level_style(str(val))
                tds.append(f"<td><span class='tag' style='background:{bg};color:{fg}'>{escape(str(val))}</span></td>")
                continue
            if c == "action":
                bg, fg = _action_style(str(val))
                tds.append(f"<td><span class='tag' style='background:{bg};color:{fg}'>{escape(str(val))}</span></td>")
                continue
            if c == "relative_strength_20d":
                tds.append(f"<td style='text-align:right'>{escape(_fmt_pct(val))}</td>")
                continue
            if c == "close":
                tds.append(f"<td style='text-align:right'>{escape(_fmt_num(val))}</td>")
                continue
            if c == "score":
                tds.append(f"<td style='text-align:right'>{escape('—' if val == '' else str(val))}</td>")
                continue
            if c == "data_source":
                txt = str(val)
                bg = "#ecf0f1"
                fg = "#2c3e50"
                if txt == "online":
                    bg = "#e8f5e9"
                    fg = "#1b5e20"
                if txt == "cache":
                    bg = "#fff3e0"
                    fg = "#e65100"
                tds.append(f"<td><span class='tag' style='background:{bg};color:{fg}'>{escape(txt)}</span></td>")
                continue
            tds.append(f"<td>{escape('—' if val == '' else str(val))}</td>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")

    return "\n".join(
        [
            "<table class='summary compact'>",
            "<thead><tr>" + ths + "</tr></thead>",
            "<tbody>",
            "\n".join(rows_html),
            "</tbody></table>",
        ]
    )


def render_asset_details(summary_df: pd.DataFrame, per_stock_charts: dict[str, go.Figure]) -> str:
    if summary_df.empty:
        return ""

    df = reorder_summary_df(summary_df).copy()
    parts: list[str] = []
    plotly_included = False
    df["_category"] = df.get("category", "").fillna("").astype(str)
    group_order = ["ai", "auto_oem", "auto_supply"]
    group_label = {
        "ai": "AI 板块",
        "auto_oem": "汽车主机厂",
        "auto_supply": "汽车供应链",
        "consumer": "消费",
    }
    df["_group_rank"] = df["_category"].map({k: i for i, k in enumerate(group_order)}).fillna(999).astype(int)
    df = df.sort_values(["_group_rank"]).drop(columns=["_group_rank"])

    def _render_one(r: pd.Series) -> None:
        nonlocal plotly_included
        name = str(r.get("name", ""))
        symbol = str(r.get("symbol", ""))
        market = str(r.get("market", ""))
        wl = str(r.get("watch_level", ""))
        score_trend = str(r.get("score_trend", ""))
        rs = _fmt_pct(r.get("relative_strength_20d"))

        summary_text = f"{name} {symbol} | {wl} | score {score_trend} | RS {rs}"
        parts.append("<details class='asset'>")
        parts.append(f"<summary>{escape(summary_text)}</summary>")

        meta = " | ".join(
            [
                f"{escape(str(market))}",
                f"{escape(str(r.get('exchange','')))}",
                f"{escape(str(r.get('currency','')))}",
                f"{escape(str(r.get('category','')))}",
                f"date {escape(str(r.get('date','')))}",
                f"fresh {escape(str(r.get('data_freshness_days','')))}d",
                f"source {escape(str(r.get('data_source','')))}",
            ]
        )
        parts.append(f"<div class='meta'>{meta}</div>")

        risk_flags = str(r.get("risk_flags", "") or "")
        if risk_flags:
            parts.append(f"<div class='reason'><div class='k'>risk_flags</div><div class='v'>{escape(risk_flags)}</div></div>")

        reason = str(r.get("reason", "") or "")
        note = str(r.get("note", "") or "")
        if reason:
            parts.append(f"<div class='reason'><div class='k'>reason</div><div class='v'>{escape(reason)}</div></div>")
        if note:
            parts.append(f"<div class='reason'><div class='k'>note</div><div class='v'>{escape(note)}</div></div>")

        chart_key = f"{market}:{symbol}"
        fig = per_stock_charts.get(chart_key)
        if fig is not None:
            parts.append("<div class='chart'>")
            if not plotly_included:
                parts.append(fig.to_html(full_html=False, include_plotlyjs="cdn"))
                plotly_included = True
            else:
                parts.append(fig.to_html(full_html=False, include_plotlyjs=False))
            parts.append("</div>")

        parts.append("</details>")

    for cat, g in df.groupby("_category", dropna=False, sort=False):
        cat = "" if cat is None else str(cat)
        title = group_label.get(cat, cat or "未分组")
        parts.append(f"<h3>{escape(title)}</h3>")
        for _, r in g.iterrows():
            _render_one(r)

    return "\n".join(parts)


def render_summary_table(summary_df: pd.DataFrame) -> str:
    if summary_df.empty:
        return "<p>无数据</p>"

    df = reorder_summary_df(summary_df)
    cols = df.columns.tolist()

    ths = "".join([f"<th>{escape(str(c))}</th>" for c in cols])
    rows_html: list[str] = []
    for _, r in df.iterrows():
        tds: list[str] = []
        for c in cols:
            val = r.get(c, "")
            if pd.isna(val):
                val = ""
            if c == "watch_level":
                bg, fg = _watch_level_style(str(val))
                tds.append(f"<td><span class='tag' style='background:{bg};color:{fg}'>{escape(str(val))}</span></td>")
                continue
            if c == "action":
                bg, fg = _action_style(str(val))
                tds.append(f"<td><span class='tag' style='background:{bg};color:{fg}'>{escape(str(val))}</span></td>")
                continue
            if c == "relative_strength_20d":
                rs_val = None
                try:
                    rs_val = float(val)
                except Exception:
                    rs_val = None
                if rs_val is not None and pd.isna(rs_val):
                    rs_val = None
                bg, fg = _rs_style(rs_val)
                txt = "—" if rs_val is None else f"{rs_val*100:.2f}%"
                tds.append(f"<td style='background:{bg};color:{fg};text-align:right'>{escape(txt)}</td>")
                continue
            if c == "close":
                try:
                    v = float(val)
                    txt = "—" if pd.isna(v) else f"{v:.2f}"
                except Exception:
                    txt = "—" if val == "" else str(val)
                tds.append(f"<td style='text-align:right'>{escape(txt)}</td>")
                continue
            if c == "score":
                tds.append(f"<td style='text-align:right'>{escape('—' if val == '' else str(val))}</td>")
                continue
            tds.append(f"<td>{escape('—' if val == '' else str(val))}</td>")

        rows_html.append("<tr>" + "".join(tds) + "</tr>")

    return "\n".join(
        [
            "<table class='summary'>",
            "<thead><tr>" + ths + "</tr></thead>",
            "<tbody>",
            "\n".join(rows_html),
            "</tbody></table>",
        ]
    )


def write_daily_report(
    report_date: date,
    summary_df: pd.DataFrame,
    per_stock_charts: dict[str, go.Figure],
    out_dir: Path,
    portfolio_summary: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"trend_report_{report_date:%Y%m%d}.csv"
    html_path = out_dir / f"trend_report_{report_date:%Y%m%d}.html"

    ordered_summary = reorder_summary_df(public_summary_df(summary_df))
    ordered_summary.to_csv(csv_path, index=False, encoding="utf-8-sig")

    parts: list[str] = []
    parts.append("<html><head><meta charset='utf-8'><title>AKSignal 趋势报告</title>")
    parts.append(
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;margin:24px;color:#2c3e50}"
        "h1{margin:0 0 16px 0}"
        "h2{margin:18px 0 10px 0}"
        ".cards{display:grid;grid-template-columns:repeat(3,minmax(220px,1fr));gap:12px;margin:12px 0 18px 0}"
        ".card{border:1px solid #ecf0f1;border-radius:10px;padding:12px 14px;background:#ffffff}"
        ".card .k{font-size:12px;color:#7f8c8d;margin-bottom:6px}"
        ".card .v{font-size:18px;font-weight:600}"
        ".card .s{font-size:12px;color:#7f8c8d;margin-top:6px;line-height:1.35}"
        ".summary{border-collapse:collapse;width:100%;font-size:13px}"
        ".summary th,.summary td{border:1px solid #ecf0f1;padding:8px 10px;vertical-align:top}"
        ".summary th{background:#f8f9fb;text-align:left}"
        ".tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}"
        ".compact th,.compact td{padding:7px 8px}"
        "h3{margin:16px 0 8px 0;font-size:16px}"
        "details.asset{border:1px solid #ecf0f1;border-radius:10px;padding:10px 12px;margin:10px 0;background:#ffffff}"
        "details.asset>summary{cursor:pointer;font-weight:600;outline:none}"
        ".meta{margin:8px 0 10px 0;font-size:12px;color:#7f8c8d}"
        ".reason{margin:8px 0}"
        ".reason .k{font-size:12px;color:#7f8c8d;margin-bottom:4px}"
        ".reason .v{font-size:13px;line-height:1.45}"
        ".chart{margin-top:10px}"
        "</style>"
    )
    parts.append("</head><body>")
    parts.append(f"<h1>AKSignal 每日趋势观察与行动 - {report_date:%Y-%m-%d}</h1>")
    parts.append("<h2>市场总览</h2>")
    parts.append(_render_market_overview(ordered_summary))

    parts.append("<h2>今日汇总（压缩版）</h2>")
    parts.append(render_compact_signal_table(ordered_summary))

    parts.append("<h2>按需展开</h2>")
    parts.append(render_asset_details(ordered_summary, per_stock_charts))

    parts.append("</body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")

    return csv_path, html_path


def save_report(summary_df: pd.DataFrame, report_dir: Path, date_str: str) -> tuple[Path, Path]:
    try:
        if "-" in date_str:
            d = date.fromisoformat(date_str)
        else:
            d = date(int(date_str[0:4]), int(date_str[4:6]), int(date_str[6:8]))
    except Exception:
        raise ValueError(f"invalid date_str: {date_str}")
    return write_daily_report(d, summary_df, per_stock_charts={}, out_dir=report_dir, portfolio_summary=None)
