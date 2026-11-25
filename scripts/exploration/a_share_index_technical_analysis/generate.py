"""Build the offline A 股宽基指数技术分析 page from its audited history snapshot."""
from argparse import ArgumentParser
from bisect import bisect_left, bisect_right
import hashlib
import json
import math
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pandas as pd
import plotly.graph_objects as go

TOPIC_DIR = Path(__file__).resolve().parent
HTML_FILENAME = "csi500_price_channel.html"
UPPER_2025_DATE = "2025-09-28"
LOWER_2025_DATE = "2025-06-23"
CSI1000_EVENT_DATE = "2022-03-28"
INK = "#243442"
GRAPHITE = "#556270"
GRID = "#E1E8EC"
PRICE = "#233B51"
UPPER = "#257AB0"
LOWER = "#73478F"
MID = "#6D7985"

parser = ArgumentParser(description=__doc__)
parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="AKsignal 数据所在的项目根目录")
parser.add_argument("--output", type=Path, help="指定离线 HTML 的输出路径")
parser.add_argument("--package", action="store_true", help="同时生成含图表和复现材料的 ZIP")
args = parser.parse_args()
repo_root = args.repo_root.resolve()
manifest = json.loads((TOPIC_DIR / "sources.json").read_text(encoding="utf-8"))
scopes = manifest["indices"]


def load_history(code):
    scope = scopes[code]
    source = repo_root / scope["source"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != scope["source_sha256"]:
        raise ValueError(f"{code}: input CSV does not match the scan receipt.")
    raw = pd.read_csv(source, dtype={"trade_date": str, "index_code": str})
    frame = raw.loc[raw["trade_date"].between(scope["start"], scope["end"])]
    dates = frame["trade_date"].tolist()
    close = frame["close"].to_numpy()
    if (
        len(dates) != scope["bars"]
        or dates != sorted(set(dates))
        or (dates[0], dates[-1]) != (scope["start"], scope["end"])
        or not frame["index_code"].eq(code).all()
        or not frame["source"].eq(manifest["provider"]).all()
        or not frame["price_basis"].eq(manifest["price_basis"]).all()
        or not all(math.isfinite(price) and price > 0 for price in close)
    ):
        raise ValueError(f"{code}: data do not match the audited scope.")
    return dates, close


def closing_trace(dates, close):
    return go.Scatter(
        x=dates, y=close.tolist(), mode="lines", name="收盘价", legendrank=0,
        line=dict(color=PRICE, width=1.15), opacity=0.64,
        hovertemplate="收盘价 %{y:,.2f}<extra></extra>",
    )


def add_rail(chart, dates, close, name, first_anchor, second_anchor):
    first, first_label = first_anchor
    anchor_date, anchor_price, second_label = second_anchor
    color, rank = (UPPER, 1) if name == "上轨" else (LOWER, 3)
    elapsed_days = np.asarray(dates, dtype="datetime64[D]").astype(int)
    anchor_day = int(np.datetime64(anchor_date, "D").astype(int))
    slope = (anchor_price - close[first]) / (anchor_day - elapsed_days[first])
    rail = close[first] + slope * (elapsed_days - elapsed_days[first])
    rail[:first] = np.nan

    before = slice(first, bisect_left(dates, anchor_date))
    after = slice(bisect_right(dates, anchor_date), None)
    for xs, ys, label, dash in (
        ([*dates[before], anchor_date], [*rail[before], anchor_price], name, "solid"),
        ([anchor_date, *dates[after]], [anchor_price, *rail[after]], f"{name}延长段", "dash"),
    ):
        chart.add_scatter(
            x=xs, y=ys, mode="lines",
            name=label, legendgroup=name, legendrank=rank, showlegend=dash == "solid",
            line=dict(color=color, width=2.8, dash=dash),
            hovertemplate=f"{label} %{{y:,.2f}}<extra></extra>",
        )
    chart.add_scatter(
        x=[dates[first], anchor_date, dates[-1]],
        y=[close[first], anchor_price, rail[-1]],
        text=["", "", f"{name} {rail[-1]:,.0f} 点"],
        textposition="top left" if name == "上轨" else "bottom left",
        textfont=dict(color=color, size=13),
        customdata=[first_label, second_label, f"{name}延长线末点"],
        mode="markers+text", name=f"{name}端点", legendgroup=name, showlegend=False,
        marker=dict(color=color, size=8, line=dict(color="white", width=1)),
        hovertemplate="%{customdata} %{y:,.2f}<extra></extra>",
    )
    print(name, [(dates[first], float(close[first])), (anchor_date, anchor_price),
                 (dates[-1], round(float(rail[-1]), 2))])
    return rail


def add_middle_rail(chart, dates, upper, lower):
    middle = (upper + lower) / 2
    chart.add_scatter(
        x=dates, y=[None if np.isnan(value) else float(value) for value in middle],
        mode="lines", name="中轨", legendrank=2, line=dict(color=MID, width=1.45, dash="dot"),
        hovertemplate="中轨 %{y:,.2f}<extra></extra>",
    )


def recent_range(dates, years=3):
    end = pd.Timestamp(dates[-1])
    start = max(pd.Timestamp(dates[0]), end - pd.DateOffset(years=years))
    return [start.strftime("%Y-%m-%d"), dates[-1]]


dates, close = load_history("000905")
index_2010 = [i for i, day in enumerate(dates) if day.startswith("2010-")]
upper_source = bisect_right(dates, UPPER_2025_DATE) - 1
upper_source_date = dates[upper_source]
upper_price = float(close[upper_source])
lower_price = float(close[dates.index(LOWER_2025_DATE)])
upper_label = f"{UPPER_2025_DATE} 锚点（取 {upper_source_date} 收盘）"

chart = go.Figure(closing_trace(dates, close))
upper = add_rail(
    chart, dates, close, "上轨",
    (max(index_2010, key=close.__getitem__), "2010 年最高收盘"),
    (UPPER_2025_DATE, upper_price, upper_label),
)
lower = add_rail(
    chart, dates, close, "下轨",
    (min(index_2010, key=close.__getitem__), "2010 年最低收盘"),
    (LOWER_2025_DATE, lower_price, f"{LOWER_2025_DATE} 收盘"),
)
add_middle_rail(chart, dates, upper, lower)
chart.add_annotation(
    x=LOWER_2025_DATE, y=lower_price,
    text="情绪震荡结束", showarrow=True, ax=-80, ay=43,
    arrowhead=2, arrowsize=0.85, arrowcolor=LOWER, standoff=5,
    bgcolor="#F3EDF7", bordercolor=LOWER, borderpad=5,
    font=dict(color=INK, size=13),
)
chart.add_annotation(
    x=UPPER_2025_DATE, y=upper_price,
    text="9 月美债暴跌；<br>倬迅说 9 月 28 日以来 ETF 净流入 7600 亿",
    align="left", showarrow=True, ax=-158, ay=-55,
    arrowhead=2, arrowsize=0.85, arrowcolor=UPPER, standoff=5,
    bgcolor="#EDF5FA", bordercolor=UPPER, borderpad=5,
    font=dict(color=INK, size=13),
)
common_layout = dict(
    autosize=True, paper_bgcolor="#FFFFFF", plot_bgcolor="#FFFFFF",
    font=dict(family="system-ui, PingFang SC, Microsoft YaHei, sans-serif", color=INK, size=12),
    margin=dict(l=59, r=21, t=49, b=63),
    hovermode="x unified", dragmode=False,
    hoverlabel=dict(bgcolor="white", bordercolor=GRID, font=dict(color=INK, size=13)),
    legend=dict(orientation="h", x=0, y=-0.07, yanchor="top",
                groupclick="togglegroup"),
    xaxis=dict(
        type="date", showgrid=False,
        tickformat="%Y", hoverformat="%Y-%m-%d", spikemode="across", spikesnap="cursor",
        spikecolor=GRAPHITE, spikethickness=1, spikedash="dot",
        rangeselector=dict(
            x=0, y=1.03, bgcolor="#F0F5F8", activecolor="#D5E9F5",
            buttons=[
                dict(count=1, label="近1年", step="year", stepmode="backward"),
                dict(count=3, label="近3年", step="year", stepmode="backward"),
                dict(count=5, label="近5年", step="year", stepmode="backward"),
                dict(label="全历史", step="all"),
            ],
        ),
        tickformatstops=[
            dict(dtickrange=[None, 86400000 * 7], value="%m-%d"),
            dict(dtickrange=[86400000 * 7, "M12"], value="%Y-%m"),
            dict(dtickrange=["M12", None], value="%Y"),
        ],
        rangeslider=dict(visible=False),
    ),
    yaxis=dict(
        title=dict(text="指数点位", standoff=8), tickformat=",d",
        gridcolor=GRID, zeroline=False, fixedrange=True,
    ),
)
chart.update_layout(common_layout)
chart.update_xaxes(range=recent_range(dates))

dates_1000, close_1000 = load_history("000852")
chart_1000 = go.Figure(closing_trace(dates_1000, close_1000))
upper_start_1000 = max(
    (i for i, day in enumerate(dates_1000) if day.startswith("2019-")), key=close_1000.__getitem__,
)
upper_start_date_1000 = dates_1000[upper_start_1000]
upper_source_1000 = bisect_right(dates_1000, UPPER_2025_DATE) - 1
upper_source_date_1000 = dates_1000[upper_source_1000]
lower_start_1000 = int(np.argmin(close_1000))
lower_anchor_1000 = min(
    (i for i, day in enumerate(dates_1000) if day.startswith("2025-")), key=close_1000.__getitem__,
)
lower_anchor_date_1000 = dates_1000[lower_anchor_1000]
upper_1000 = add_rail(
    chart_1000, dates_1000, close_1000, "上轨",
    (upper_start_1000, "2019 年最高收盘"),
    (UPPER_2025_DATE, float(close_1000[upper_source_1000]),
     f"{UPPER_2025_DATE} 锚点（取 {upper_source_date_1000} 收盘）"),
)
lower_1000 = add_rail(
    chart_1000, dates_1000, close_1000, "下轨",
    (lower_start_1000, "全区间最低收盘"),
    (lower_anchor_date_1000, float(close_1000[lower_anchor_1000]), "2025 年最低收盘"),
)
add_middle_rail(chart_1000, dates_1000, upper_1000, lower_1000)
chart_1000.add_annotation(
    x=CSI1000_EVENT_DATE, y=float(close_1000[dates_1000.index(CSI1000_EVENT_DATE)]),
    text="上海 YQ 管制", showarrow=True, ax=-65, ay=-45,
    arrowhead=2, arrowsize=0.85, arrowcolor=UPPER, standoff=5,
    bgcolor="#EDF5FA", bordercolor=UPPER, borderpad=5,
    font=dict(color=INK, size=13),
)
chart_1000.update_layout(common_layout)
chart_1000.update_xaxes(range=recent_range(dates_1000))

post_script = """
const chart = document.getElementById("{plot_id}");

function adjustY() {
  const visibleRange = chart._fullLayout.xaxis.range;
  const first = visibleRange[0].slice(0, 10);
  const last = visibleRange[1].slice(0, 10);
  let low = Infinity;
  let high = -Infinity;
  for (const trace of chart.data) {
    if (trace.visible === false || trace.visible === 'legendonly') continue;
    for (let i = 0; i < trace.x.length; i++) {
      if (trace.x[i] < first || trace.x[i] > last) continue;
      const value = trace.y[i];
      if (!Number.isFinite(value)) continue;
      low = Math.min(low, value);
      high = Math.max(high, value);
    }
  }
  if (!Number.isFinite(low)) return;
  const pad = Math.max((high - low) * 0.065, Math.abs(low) * 0.002);
  return Plotly.relayout(chart, {'yaxis.range': [low - pad, high + pad]});
}

chart.on('plotly_relayout', update => {
  if (Object.keys(update).some(key => key.startsWith('xaxis.range') || key === 'xaxis.autorange')) {
    adjustY();
  }
});
chart.on('plotly_restyle', adjustY);
adjustY();
"""

def render_chart(figure, div_id, filename, include_plotlyjs=False):
    return figure.to_html(
        full_html=False, include_plotlyjs=include_plotlyjs, div_id=div_id,
        default_height="100%", default_width="100%", post_script=post_script,
        config={
            "responsive": True, "displaylogo": False, "scrollZoom": False,
            "doubleClick": False,
            "toImageButtonOptions": {"filename": filename, "scale": 2},
        },
    )


def history_caption(code):
    scope = scopes[code]
    return (
        f'{code} · {scope["start"]} 至 {scope["end"]} · '
        f'中证官方日收盘指数 · {scope["bars"]:,} 个交易日'
    )


plot = render_chart(chart, "csi500-price-chart", "csi500_price_channel", include_plotlyjs=True)
plot_1000 = render_chart(chart_1000, "csi1000-price-chart", "csi1000_price_channel")
html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{manifest["topic"]}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #F8F6F0; color: {INK}; font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif; }}
  main {{ max-width: 1480px; padding: 30px 32px 22px; margin: 0 auto; }}
  h1 {{ font-size: clamp(26px, 3.1vw, 42px); margin: 0 0 9px; line-height: 1.2; letter-spacing: -0.025em; }}
  h2 {{ font-size: clamp(23px, 2.5vw, 32px); margin: 32px 0 9px; line-height: 1.2; letter-spacing: -0.02em; }}
  .subtitle {{ margin: 0; color: {GRAPHITE}; font-size: 14px; }}
  .panel {{ margin-top: 23px; border: 1px solid {GRID}; border-radius: 8px; background: white; padding: 16px 15px 8px; }}
  .chart {{ height: clamp(475px, 68vh, 660px); margin-top: 5px; }}
  .chart > div {{ height: 100%; }}
  .note {{ padding: 0 8px; margin: 10px 0 6px; color: {GRAPHITE}; font-size: 12px; line-height: 1.7; }}
  @media (max-width: 650px) {{ main {{ padding: 20px 10px; }} .panel {{ padding: 12px 1px 6px; }} .subtitle {{ font-size: 12px; }} }}
</style>
</head>
<body>
<main>
  <h1>中证500全历史价格轨道</h1>
  <p class="subtitle">{history_caption("000905")}</p>
  <section class="panel" aria-label="中证500价格与上下轨">
    <div class="chart">{plot}</div>
    <p class="note">蓝色上轨连接 2010 年最高收盘点与 {UPPER_2025_DATE} 锚点（非交易日，点位取 {upper_source_date} 收盘）。紫色下轨连接 2010 年最低收盘点与 {LOWER_2025_DATE} 收盘点。虚线延长到最后一个交易日，灰色中轨取上下轨在当日的中间位置。</p>
  </section>
  <h2 id="csi1000-heading">中证1000全历史价格轨道</h2>
  <p class="subtitle">{history_caption("000852")}</p>
  <section class="panel" aria-labelledby="csi1000-heading">
    <div class="chart">{plot_1000}</div>
    <p class="note">蓝色上轨连接 2019 年最高收盘点 {upper_start_date_1000} 与 {UPPER_2025_DATE} 锚点（非交易日，点位取 {upper_source_date_1000} 收盘）。紫色下轨连接全区间最低收盘点 {dates_1000[lower_start_1000]} 与 2025 年最低收盘点 {lower_anchor_date_1000}。虚线延长到最后一个交易日，灰色中轨取上下轨在当日的中间位置。</p>
  </section>
</main>
</body>
</html>
"""
dest = args.output or repo_root / "data/audits/a_share_index_technical_analysis" / HTML_FILENAME
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(html, encoding="utf-8")
print(f"{dest} ({dest.stat().st_size:,} bytes)")
if args.package:
    topic_name = manifest["topic"]
    archive_path = dest.with_name(f"{topic_name}.zip")
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(dest, f"{topic_name}/{HTML_FILENAME}")
        for filename in ("README.md", "generate.py", "sources.json"):
            archive.write(TOPIC_DIR / filename, f"{topic_name}/{filename}")
    print(f"{archive_path} ({archive_path.stat().st_size:,} bytes)")
