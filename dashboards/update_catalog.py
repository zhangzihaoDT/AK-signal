"""Refresh the local dashboard catalog from the latest available report artifacts."""
from __future__ import annotations

from datetime import date, datetime
from html import escape
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(__file__).resolve().parent / "index.html"


def format_date(value: str | None) -> str:
    if not value:
        return "暂无数据日期"
    try:
        return date.fromisoformat(value[:10]).strftime("%Y-%m-%d")
    except ValueError:
        return "日期未知"


def modified_label(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def csi500_entry() -> dict:
    manifest_path = ROOT / "scripts/exploration/a_share_index_technical_analysis/sources.json"
    report = ROOT / "data/audits/a_share_index_technical_analysis/csi500_price_channel.html"
    entry = {
        "id": "csi500-price-channel",
        "title": "CSI500 Price Channel",
        "subtitle": "中证500 / 中证1000 · 历史价格轨道",
        "kind": "市场研究",
        "description": "查看宽基指数历史收盘走势、人工选定的上下轨与关键时点。",
        "thumbnail": "channel",
        "report": report,
        "data_date": None,
        "updated": modified_label(report) if report.is_file() else None,
    }
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        indices = manifest.get("indices", {})
        ends = [indices[code].get("end") for code in ("000905", "000852") if code in indices]
        entry["data_date"] = min(ends) if ends else None
    return entry


def holdings_entry() -> dict:
    base = ROOT / "data/audits/holdings_dashboard"
    candidates = []
    if base.is_dir():
        for report in base.glob("[0-9]*/holdings_daily.html"):
            folder_date = report.parent.name
            if not re.fullmatch(r"\d{8}", folder_date) or not report.is_file():
                continue
            candidates.append((folder_date, report))

    report = None
    report_date = None
    if candidates:
        folder_date, report = max(candidates, key=lambda item: item[0])
        report_date = f"{folder_date[:4]}-{folder_date[4:6]}-{folder_date[6:8]}"

    return {
        "id": "holdings-dashboard",
        "title": "Holdings Dashboard",
        "subtitle": "持仓资产 · 日线与成交标记",
        "kind": "个人看板",
        "description": "按资产分组查看收盘走势、最新价格与已记录的成交标记。",
        "thumbnail": "holdings",
        "report": report,
        "data_date": report_date,
        "updated": modified_label(report) if report else None,
    }


def thumbnail(kind: str) -> str:
    if kind == "channel":
        return '''<svg viewBox="0 0 640 260" role="img" aria-label="指数价格轨道图预览">
          <rect width="640" height="260" rx="18" fill="#F7FBFD"/>
          <path d="M48 50H594M48 105H594M48 160H594M48 215H594" stroke="#E4EDF2" stroke-width="2"/>
          <path d="M50 207 105 194 154 180 210 166 259 152 316 138 371 121 423 108 482 90 535 77 590 61" fill="none" stroke="#174A7C" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M72 170 590 53" fill="none" stroke="#2D8BBF" stroke-width="4" stroke-linecap="round"/>
          <path d="M72 222 590 133" fill="none" stroke="#80579B" stroke-width="4" stroke-linecap="round"/>
          <path d="M72 196 590 93" fill="none" stroke="#94A0AA" stroke-width="3" stroke-dasharray="5 9"/>
          <circle cx="590" cy="61" r="7" fill="#D79A36" stroke="#FFF" stroke-width="3"/>
        </svg>'''
    return '''<svg viewBox="0 0 640 260" role="img" aria-label="持仓资产日线图预览">
      <rect width="640" height="260" rx="18" fill="#F7FBFD"/>
      <rect x="24" y="24" width="188" height="212" rx="12" fill="#FFF" stroke="#E4EDF2" stroke-width="2"/>
      <rect x="226" y="24" width="188" height="212" rx="12" fill="#FFF" stroke="#E4EDF2" stroke-width="2"/>
      <rect x="428" y="24" width="188" height="212" rx="12" fill="#FFF" stroke="#E4EDF2" stroke-width="2"/>
      <path d="M42 185 66 172 89 180 111 137 136 148 161 103 193 76" fill="none" stroke="#174A7C" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M244 166 269 154 292 158 316 123 340 133 366 91 395 103" fill="none" stroke="#2D8BBF" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M446 191 471 176 494 181 518 147 542 123 568 132 597 74" fill="none" stroke="#80579B" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="136" cy="148" r="6" fill="#D79A36"/><circle cx="340" cy="133" r="6" fill="#D79A36"/><circle cx="542" cy="123" r="6" fill="#D79A36"/>
    </svg>'''


def card(entry: dict) -> str:
    report = entry["report"]
    available = report is not None and report.is_file()
    status = "可打开" if available else "报告未生成"
    report_link = ""
    if available:
        href = Path(os.path.relpath(report, CATALOG.parent)).as_posix()
        report_link = f'<a class="open-link" href="{escape(href, quote=True)}">打开看板 <span aria-hidden="true">↗</span></a>'
    else:
        report_link = '<span class="open-link disabled" aria-disabled="true">暂不可用</span>'

    data_date = format_date(entry.get("data_date"))
    updated = escape(entry.get("updated") or "尚未生成", quote=True)
    return f'''<article class="dashboard-card" id="{escape(entry["id"], quote=True)}">
      <div class="preview preview-{escape(entry["thumbnail"], quote=True)}">{thumbnail(entry["thumbnail"])}</div>
      <div class="card-body">
        <div class="card-kicker"><span>{escape(entry["kind"])}</span><span class="status {'ready' if available else 'unavailable'}">{status}</span></div>
        <h2>{escape(entry["title"])}</h2>
        <p class="subtitle">{escape(entry["subtitle"])}</p>
        <p class="description">{escape(entry["description"])}</p>
        <div class="meta"><span>数据截至 <strong>{escape(data_date)}</strong></span><span>报告更新 <strong>{updated}</strong></span></div>
        <div class="card-actions">{report_link}<span class="local-mark">本机文件</span></div>
      </div>
    </article>'''


def render(entries: list[dict]) -> str:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    cards = "\n".join(card(entry) for entry in entries)
    available = sum(entry["report"] is not None and entry["report"].is_file() for entry in entries)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>AKsignal · 本地看板</title>
  <style>
    :root {{
      color-scheme: light;
      --blue: #174A7C; --deep-blue: #06213D; --cyan: #7ECDEB;
      --light-blue: #DDEFF8; --cream: #FFF9EF; --gold: #D79A36;
      --text: #1F2D3D; --muted: #6B7C8F; --card: #FFFFFF;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: linear-gradient(145deg, #F4FAFD 0%, var(--cream) 78%); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    .page {{ width: min(1120px, calc(100% - 40px)); margin: 0 auto; padding: 44px 0 32px; }}
    header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 30px; }}
    .eyebrow {{ color: var(--blue); font-size: 12px; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }}
    h1 {{ margin: 9px 0 8px; color: var(--deep-blue); font-size: clamp(32px, 5vw, 48px); letter-spacing: -.04em; line-height: 1.1; }}
    .intro {{ max-width: 650px; margin: 0; color: var(--muted); font-size: 15px; line-height: 1.7; }}
    .brand {{ flex: none; width: 46px; height: 46px; display: grid; place-items: center; border: 1px solid #C8DFEB; border-radius: 15px 12px 16px 11px; background: #FFF; color: var(--blue); font-size: 13px; font-weight: 800; box-shadow: 2px 3px 0 #DDEFF8; transform: rotate(-3deg); }}
    .summary {{ display: flex; align-items: center; gap: 10px; margin: 0 0 17px; color: var(--muted); font-size: 13px; }}
    .summary strong {{ color: var(--blue); font-size: 19px; }}
    .summary-dot {{ width: 5px; height: 5px; border-radius: 50%; background: var(--gold); }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }}
    .dashboard-card {{ overflow: hidden; border: 1px solid #DDE8ED; border-radius: 17px; background: var(--card); box-shadow: 0 12px 30px #174A7C0D; transition: transform .18s ease, box-shadow .18s ease; }}
    .dashboard-card:hover {{ transform: translateY(-3px); box-shadow: 0 16px 34px #174A7C17; }}
    .preview {{ height: 184px; padding: 14px 14px 0; background: linear-gradient(180deg, #EDF7FB, #F9FCFD); }}
    .preview svg {{ width: 100%; height: 100%; display: block; }}
    .card-body {{ padding: 17px 19px 18px; }}
    .card-kicker, .meta, .card-actions {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; }}
    .card-kicker {{ color: var(--blue); font-size: 11px; font-weight: 700; letter-spacing: .04em; }}
    .status {{ padding: 4px 8px; border-radius: 99px; font-size: 10px; letter-spacing: 0; }}
    .status.ready {{ background: #EAF5EE; color: #32704C; }}
    .status.unavailable {{ background: #FFF3E5; color: #8A5B20; }}
    h2 {{ margin: 11px 0 3px; color: var(--deep-blue); font-size: 21px; letter-spacing: -.02em; }}
    .subtitle {{ margin: 0; color: var(--blue); font-size: 13px; font-weight: 600; }}
    .description {{ min-height: 42px; margin: 12px 0 14px; color: var(--muted); font-size: 13px; line-height: 1.6; }}
    .meta {{ justify-content: flex-start; flex-wrap: wrap; padding: 11px 0; border-top: 1px solid #EDF1F3; color: var(--muted); font-size: 11px; }}
    .meta strong {{ margin-left: 3px; color: var(--text); font-weight: 650; }}
    .card-actions {{ margin-top: 3px; }}
    .open-link {{ display: inline-flex; align-items: center; gap: 8px; min-height: 38px; padding: 0 14px; border-radius: 9px; background: var(--blue); color: #FFF; font-size: 12px; font-weight: 700; text-decoration: none; transition: background .15s ease; }}
    .open-link:hover {{ background: var(--deep-blue); }}
    .open-link:focus-visible {{ outline: 3px solid var(--cyan); outline-offset: 3px; }}
    .open-link.disabled {{ background: #9AA9B5; cursor: not-allowed; }}
    .local-mark {{ color: var(--muted); font-size: 11px; }}
    footer {{ display: flex; justify-content: space-between; gap: 16px; margin-top: 25px; padding-top: 14px; border-top: 1px solid #DDE8ED; color: var(--muted); font-size: 11px; }}
    @media (max-width: 720px) {{ .page {{ width: min(100% - 24px, 560px); padding-top: 28px; }} .grid {{ grid-template-columns: 1fr; gap: 14px; }} header {{ margin-bottom: 22px; }} .preview {{ height: 170px; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div>
        <div class="eyebrow">AKsignal · Local dashboards</div>
        <h1>研究看板</h1>
        <p class="intro">常用页面的本地入口。报告数据留在本机，选择卡片即可打开对应页面。</p>
      </div>
      <div class="brand" aria-label="ZH mark">ZH</div>
    </header>
    <p class="summary"><strong>{available:02d}</strong> 个可用看板 <span class="summary-dot"></span> 本机文件 · 离线入口</p>
    <section class="grid" aria-label="看板列表">
      {cards}
    </section>
    <footer><span>本地目录 · 不上传报告与行情快照</span><span>目录刷新 {escape(now)}</span></footer>
  </main>
</body>
</html>
'''


def main() -> None:
    entries = [holdings_entry(), csi500_entry()]
    CATALOG.write_text(render(entries), encoding="utf-8")
    print(f"Updated {CATALOG.relative_to(ROOT)} ({len(entries)} dashboards)")
    for entry in entries:
        state = "available" if entry["report"] and entry["report"].is_file() else "unavailable"
        print(f"- {entry['id']}: {state}, data through {format_date(entry['data_date'])}")


if __name__ == "__main__":
    main()
