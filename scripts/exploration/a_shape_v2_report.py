"""Render the reviewed A-Shape V2 scan as an offline summary page."""
from __future__ import annotations

import argparse
import base64
import csv
from html import escape
import json
from pathlib import Path


CSS = """
:root{color-scheme:light;--blue:#174A7C;--deep:#06213D;--cyan:#7ECDEB;--light:#DDEFF8;--cream:#FFF9EF;--gold:#D79A36;--text:#1F2D3D;--muted:#6B7C8F;--border:#E1E8EC}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(145deg,#F4FAFD 0%,var(--cream) 78%);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
main{width:min(1180px,calc(100% - 40px));margin:0 auto;padding:42px 0 32px}
.eyebrow{color:var(--blue);font-size:12px;font-weight:750;letter-spacing:.12em;text-transform:uppercase}
h1{margin:9px 0 8px;color:var(--deep);font-size:clamp(30px,4.6vw,44px);letter-spacing:-.04em;line-height:1.12}
.subtitle{max-width:760px;margin:0;color:var(--muted);font-size:14px;line-height:1.7}
.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:24px 0}
.metric{background:#FFF;border:1px solid var(--border);border-radius:13px;padding:15px 17px}
.metric strong{display:block;color:var(--blue);font-size:24px;line-height:1.2}
.metric span{color:var(--muted);font-size:11px}
.panel{margin-top:20px;background:#FFF;border:1px solid var(--border);border-radius:16px;padding:18px 20px;box-shadow:0 12px 30px #174A7C0D}
.panel h2{margin:0 0 6px;color:var(--deep);font-size:19px}
.panel p{margin:0 0 14px;color:var(--muted);font-size:12px;line-height:1.65}
.figure{width:100%;height:auto;display:block;border-radius:10px}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:8px 9px;border-bottom:1px solid #EDF1F3;text-align:left;white-space:nowrap}
th{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.04em}
td.num,th.num{text-align:right}
tr:hover td{background:#F8FBFD}
.tag{display:inline-block;padding:2px 7px;border-radius:99px;background:var(--light);color:var(--blue);font-size:10px;font-weight:700}
.note{color:var(--muted);font-size:11px;line-height:1.7}
.links{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.links a{color:var(--blue);font-size:12px;text-decoration:none;border-bottom:1px solid #B9D9E8}
footer{display:flex;justify-content:space-between;gap:14px;margin-top:24px;padding-top:14px;border-top:1px solid var(--border);color:var(--muted);font-size:11px}
@media(max-width:760px){main{width:min(100% - 24px,620px);padding-top:26px}.summary{grid-template-columns:repeat(2,minmax(0,1fr))}footer{flex-direction:column}}
@media(prefers-reduced-motion:reduce){*{transition-duration:.01ms!important}}
"""


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def pct(value: str) -> str:
    return f"{float(value):.1%}"


def number(value: str, digits: int = 2) -> str:
    return f"{float(value):,.{digits}f}"


def table(records: list[dict], limit: int | None = None) -> str:
    selected = records[:limit] if limit else records
    body = []
    for row in selected:
        body.append(
            "<tr>"
            f"<td>{escape(row['index_name'])}</td>"
            f"<td>{escape(row['start_date'])} → {escape(row['peak_date'])} → {escape(row['end_date'])}</td>"
            f"<td class='num'>{number(row['score'])}</td>"
            f"<td class='num'>{pct(row['rise'])}</td>"
            f"<td class='num'>{pct(row['drawdown'])}</td>"
            f"<td class='num'>{number(row['retracement'])}</td>"
            f"<td class='num'>{int(float(row['window_bars']))}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap'><table><thead><tr>"
        "<th>指数</th><th>起点 → 峰值 → 终点</th><th class='num'>形态分</th>"
        "<th class='num'>上涨</th><th class='num'>回落</th><th class='num'>回撤倍数</th><th class='num'>窗口交易日</th>"
        "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/audits/a_shape_v2/official-20261123"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    folder = args.input
    receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
    representatives = rows(folder / "representative_episodes.csv")
    candidates = sorted(rows(folder / "all_candidates.csv"),
                        key=lambda row: float(row["score"]), reverse=True)
    indices = receipt["indices"]
    end = max(scope["end"] for scope in indices.values())
    total_candidates = sum(int(scope["candidates"]) for scope in indices.values())
    total_representatives = sum(int(scope["representatives"]) for scope in indices.values())
    image = folder / "top_patterns.png"
    image_tag = ""
    if image.is_file():
        encoded = base64.b64encode(image.read_bytes()).decode("ascii")
        image_tag = f'<img class="figure" alt="三个指数历史最高分 A 字形态" src="data:image/png;base64,{encoded}">'

    index_rows = "".join(
        f"<tr><td>{escape(scope['index_name'])}</td><td>{escape(scope['start'])} → {escape(scope['end'])}</td>"
        f"<td class='num'>{int(scope['bars']):,}</td><td class='num'>{int(scope['candidates']):,}</td>"
        f"<td class='num'>{int(scope['representatives']):,}</td></tr>"
        for scope in indices.values()
    )
    output = args.output or folder / "a_shape_v2_report.html"
    output.write_text(f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>A-Shape V2 · 历史形态研究</title><style>{CSS}</style></head>
<body><main>
<div class="eyebrow">AKsignal · research / historical price shape</div>
<h1>A-Shape V2 历史形态研究</h1>
<p class="subtitle">对中证500、中证1000 和科创50 的已验收日收盘序列做多尺度 A 字形态扫描。分数是形态相似度，不是概率、预测或交易信号；所有结果均为事后识别。</p>
<div class="summary">
  <div class="metric"><strong>{total_candidates:,}</strong><span>候选形态</span></div>
  <div class="metric"><strong>{total_representatives:,}</strong><span>去重代表形态</span></div>
  <div class="metric"><strong>{len(indices)}</strong><span>指数</span></div>
  <div class="metric"><strong>{escape(end)}</strong><span>数据截至</span></div>
</div>
<section class="panel"><h2>三个指数的最高分代表形态</h2>
<p>各段起点归一为 100；橙点为 V2 识别峰值。图像来自已验收扫描结果，不重新计算。</p>{image_tag}</section>
<section class="panel"><h2>扫描范围</h2>
<div class="table-wrap"><table><thead><tr><th>指数</th><th>已验收区间</th><th class="num">交易日</th><th class="num">候选</th><th class="num">代表</th></tr></thead>
<tbody>{index_rows}</tbody></table></div></section>
<section class="panel"><h2>代表形态</h2><p>按分数排序，并去除同指数内时间区间重叠的形态。</p>
{table(representatives)}</section>
<section class="panel"><h2>候选形态 Top 30</h2><p>仅展示形态分数最高的候选；完整候选见 CSV。</p>
{table(candidates, 30)}</section>
<section class="panel"><h2>口径与限制</h2>
<p class="note">扫描窗口 20 / 40 / 60 / 120 / 200 个交易日；最小上涨和回落 5%；峰值显著性 4%；Savitzky–Golay 平滑 7。价格必须为正、有限、按日期升序；缺失交易日或复权断点不会被填补。分数不是收益概率，不代表未来会重复。当前扫描使用中证官方日收盘指数，历史结果均为 ex-post；用于前瞻判断前必须重新按 as-of 日期评估。</p>
<div class="links"><a href="all_candidates.csv">all_candidates.csv</a><a href="representative_episodes.csv">representative_episodes.csv</a><a href="receipt.json">receipt.json</a><a href="scan.py">scan.py</a></div>
</section>
<footer><span>AKsignal · 本地专题研究 · 非交易建议</span><span>数据版本 {escape(folder.name)}</span></footer>
</main></body></html>
''', encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
