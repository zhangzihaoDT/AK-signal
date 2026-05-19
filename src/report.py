from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go


SUMMARY_COLUMN_ORDER = [
    "watch_level",
    "action",
    "name",
    "symbol",
    "label",
    "score",
    "relative_strength_20d",
    "close",
    "change",
    "reason",
    "note",
    "date",
]


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
        legend_orientation="h",
        legend_yanchor="bottom",
        legend_y=1.02,
        legend_xanchor="right",
        legend_x=1,
        margin=dict(l=40, r=20, t=60, b=40),
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
        "重点观察": ("#c0392b", "#ffffff"),
        "继续跟踪": ("#e67e22", "#ffffff"),
        "等待突破": ("#2980b9", "#ffffff"),
        "观察等待": ("#3498db", "#ffffff"),
        "风险警戒": ("#b71c1c", "#ffffff"),
        "剔除观察": ("#7f8c8d", "#ffffff"),
    }
    bg, fg = mapping.get(action, ("#ecf0f1", "#2c3e50"))
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

    ordered_summary = reorder_summary_df(summary_df)
    ordered_summary.to_csv(csv_path, index=False, encoding="utf-8-sig")

    parts: list[str] = []
    parts.append("<html><head><meta charset='utf-8'><title>A股趋势报告</title>")
    parts.append(
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;margin:24px;color:#2c3e50}"
        "h1{margin:0 0 16px 0}"
        ".cards{display:grid;grid-template-columns:repeat(3,minmax(180px,1fr));gap:12px;margin:12px 0 18px 0}"
        ".card{border:1px solid #ecf0f1;border-radius:10px;padding:12px 14px;background:#ffffff}"
        ".card .k{font-size:12px;color:#7f8c8d;margin-bottom:6px}"
        ".card .v{font-size:18px;font-weight:600}"
        ".summary{border-collapse:collapse;width:100%;font-size:13px}"
        ".summary th,.summary td{border:1px solid #ecf0f1;padding:8px 10px;vertical-align:top}"
        ".summary th{background:#f8f9fb;text-align:left}"
        ".tag{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}"
        "</style>"
    )
    parts.append("</head><body>")
    parts.append(f"<h1>AKSignal 每日趋势观察与行动 - {report_date:%Y-%m-%d}</h1>")
    parts.append(_render_summary_cards(portfolio_summary))
    parts.append("<h2>今日汇总</h2>")
    parts.append(render_summary_table(ordered_summary))

    for key, fig in per_stock_charts.items():
        parts.append(f"<h2>{key}</h2>")
        parts.append(fig.to_html(full_html=False, include_plotlyjs="cdn"))

    parts.append("</body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")

    return csv_path, html_path
