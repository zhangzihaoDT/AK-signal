"""Refresh the local dashboard catalog from the latest available report artifacts."""
from __future__ import annotations

from datetime import date, datetime
from html import escape
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path(__file__).resolve().parent / "index.html"

# Same portable red AK mark used by the formal daily report.
FAVICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               '<title>AK</title><rect width="64" height="64" rx="12" fill="#C0392B"/>'
               '<path d="M8 46 20 16 32 46 M12 36H28 M39 16V46 M55 16 39 31 55 46" '
               'fill="none" stroke="#FFFFFF" stroke-width="4.5" stroke-linecap="round" '
               'stroke-linejoin="round"/></svg>')
FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' + quote(FAVICON_SVG, safe='') + '">'


def format_date(value: str | None) -> str:
    if not value:
        return "暂无数据日期"
    try:
        return date.fromisoformat(value[:10]).strftime("%Y-%m-%d")
    except ValueError:
        return "日期未知"


def modified_label(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def modified_time(path: Path | None) -> float:
    return path.stat().st_mtime if path is not None and path.is_file() else 0.0


def data_date_key(value: str | None) -> int:
    try:
        return date.fromisoformat(str(value)[:10]).toordinal()
    except (TypeError, ValueError):
        return 0


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def date_from_compact(value: str | None) -> str | None:
    if value and re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return value


def daily_entry() -> dict:
    output = ROOT / "outputs"
    publication = output / "run_day/publication.json"
    report = None
    data_date = None
    if publication.is_file():
        payload = json.loads(publication.read_text(encoding="utf-8"))
        data_date = date_from_compact(payload.get("trade_date"))
        if data_date:
            candidate = output / "observation" / f"observation_{data_date.replace('-', '')}.html"
            if candidate.is_file():
                report = candidate
    if report is None:
        candidates = sorted((output / "observation").glob("observation_*.html"))
        if candidates:
            report = max(candidates, key=lambda path: path.stat().st_mtime)
            data_date = re.sub(r"(\d{4})(\d{2})(\d{2})", r"\1-\2-\3", report.stem.split("_")[-1])
    return {
        "id": "market-facts-daily",
        "title": "Market Facts 日报",
        "subtitle": "统一交易日 · 数据质量 · 市场观察",
        "kind": "正式日报",
        "description": "最后一期已发布日报。日报发布暂时暂停，恢复后由目录刷新指向最新批次。",
        "thumbnail": "daily",
        "report": report,
        "data_date": data_date,
        "updated": modified_label(report) if report else None,
        "updated_at": modified_time(report),
        "status_label": "发布暂停" if report else "报告缺失",
        "status_class": "paused" if report else "unavailable",
        "command": "make run-day",
    }


def basket_entry() -> dict:
    pointer = ROOT / "data/hub/basket_comparison.json"
    report = None
    data_date = None
    status_label = "待生成"
    status_class = "unavailable"
    if pointer.is_file():
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        folder = (ROOT / payload["directory"]).resolve()
        candidate = folder / "basket_compare.html"
        manifest_path = folder / "comparison_manifest.json"
        if candidate.is_file() and manifest_path.is_file() \
                and sha256(candidate) == payload.get("report_sha256") \
                and sha256(manifest_path) == payload.get("manifest_sha256"):
            report = candidate
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data_date = manifest.get("end")
            status_label = "研究快照"
            status_class = "ready"
        else:
            status_label = "校验失败"
            status_class = "paused"
    return {
        "id": "basket-compare-v2",
        "title": "Basket Compare V2",
        "subtitle": "研究主线 · 相对强弱与结构",
        "kind": "专题研究",
        "description": "8 个研究篮子 + 高现金流参照；季度再平衡为默认结构口径，买入持有为对照。不是实际组合收益。",
        "thumbnail": "basket",
        "report": report,
        "data_date": data_date,
        "updated": modified_label(report) if report else None,
        "updated_at": modified_time(report),
        "status_label": status_label,
        "status_class": status_class,
        "command": "make basket-refresh START=YYYY-MM-DD END=YYYY-MM-DD",
    }


def ic500_entry() -> dict:
    report = ROOT / "data/audits/ic500_trend/ic500_trend_full.html"
    data_date = None
    if report.is_file():
        head = report.read_text(encoding="utf-8", errors="ignore")[:200_000]
        found = re.findall(r"数据截至\s*(\d{4}-\d{2}-\d{2})", head)
        if found:
            data_date = max(found)
    return {
        "id": "ic500-trend-full",
        "title": "IC500 Full Trend",
        "subtitle": "IC 合约 · 期限结构与基差",
        "kind": "专题研究",
        "description": "中证500 股指期货全历史、四合约期限结构与基差；固定日期专题，不自动更新。",
        "thumbnail": "futures",
        "report": report if report.is_file() else None,
        "data_date": data_date,
        "updated": modified_label(report) if report.is_file() else None,
        "updated_at": modified_time(report),
        "status_label": "固定专题" if report.is_file() else "待生成",
        "status_class": "fixed" if report.is_file() else "unavailable",
        "command": ".venv/bin/python scripts/exploration/ic500_trend_full.py --inline --output data/audits/ic500_trend/ic500_trend_full.html",
    }


def ashape_entry() -> dict:
    base = ROOT / "data/audits/a_shape_v2"
    folders = sorted(base.glob("official-*"))
    folder = folders[-1] if folders else base / "official-20261123"
    report = folder / "a_shape_v2_report.html"
    receipt = folder / "receipt.json"
    data_date = None
    if receipt.is_file():
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        ends = [scope.get("end") for scope in payload.get("indices", {}).values() if scope.get("end")]
        data_date = max(ends) if ends else None
    return {
        "id": "a-shape-v2",
        "title": "A-Shape V2",
        "subtitle": "历史价格形态 · 多尺度扫描",
        "kind": "专题研究",
        "description": "对中证500、中证1000 和科创50 的历史 A 字形态做量化扫描；分数是形态相似度，不是概率或交易信号。",
        "thumbnail": "ashape",
        "report": report if report.is_file() else None,
        "data_date": data_date,
        "updated": modified_label(report) if report.is_file() else None,
        "updated_at": modified_time(report),
        "status_label": "固定专题" if report.is_file() else "待生成",
        "status_class": "fixed" if report.is_file() else "unavailable",
        "command": "重跑扫描后：.venv/bin/python scripts/exploration/a_shape_v2_report.py",
    }


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
        "updated_at": modified_time(report),
        "command": "重建 Data App 后运行 make dashboards-index",
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
        "updated_at": modified_time(report),
        "command": ".venv/bin/python scripts/exploration/a_share_index_technical_analysis/updater.py",
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
    if kind == "basket":
        return '''<svg viewBox="0 0 640 260" role="img" aria-label="研究篮子净值曲线预览">
          <rect width="640" height="260" rx="18" fill="#F7FBFD"/>
          <path d="M48 58H594M48 113H594M48 168H594M48 223H594" stroke="#E4EDF2" stroke-width="2"/>
          <path d="M50 208 100 190 150 196 200 166 250 158 300 132 350 137 400 110 450 103 500 84 550 78 590 61" fill="none" stroke="#174A7C" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M50 214 100 205 150 202 200 184 250 181 300 163 350 160 400 143 450 136 500 127 550 118 590 109" fill="none" stroke="#2D8BBF" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M50 218 100 210 150 213 200 196 250 191 300 184 350 179 400 166 450 169 500 152 550 145 590 135" fill="none" stroke="#80579B" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M50 206 100 198 150 192 200 182 250 174 300 165 350 158 400 146 450 141 500 132 550 121 590 112" fill="none" stroke="#D79A36" stroke-width="3" stroke-dasharray="6 6" stroke-linecap="round"/>
          <circle cx="590" cy="61" r="7" fill="#D79A36" stroke="#FFF" stroke-width="3"/>
        </svg>'''
    if kind == "daily":
        return '''<svg viewBox="0 0 640 260" role="img" aria-label="市场观察日报版面预览">
          <rect width="640" height="260" rx="18" fill="#F7FBFD"/>
          <rect x="28" y="24" width="584" height="212" rx="12" fill="#FFF" stroke="#E4EDF2" stroke-width="2"/>
          <rect x="48" y="42" width="108" height="9" rx="4" fill="#7ECDEB"/>
          <rect x="48" y="62" width="246" height="18" rx="5" fill="#174A7C" opacity=".9"/>
          <rect x="48" y="93" width="160" height="7" rx="3" fill="#DDEFF8"/>
          <rect x="48" y="112" width="168" height="54" rx="8" fill="#DDEFF8"/>
          <rect x="228" y="112" width="168" height="54" rx="8" fill="#DDEFF8"/>
          <rect x="408" y="112" width="168" height="54" rx="8" fill="#FFF9EF" stroke="#F0DFC2"/>
          <rect x="62" y="124" width="52" height="9" rx="4" fill="#174A7C"/>
          <rect x="242" y="124" width="52" height="9" rx="4" fill="#174A7C"/>
          <rect x="422" y="124" width="52" height="9" rx="4" fill="#D79A36"/>
          <path d="M48 186H576M48 207H576" stroke="#EDF1F3" stroke-width="2"/>
          <rect x="48" y="196" width="300" height="6" rx="3" fill="#E1E8EC"/>
          <rect x="48" y="216" width="360" height="6" rx="3" fill="#E1E8EC"/>
        </svg>'''
    if kind == "futures":
        return '''<svg viewBox="0 0 640 260" role="img" aria-label="股指期货期限结构预览">
          <rect width="640" height="260" rx="18" fill="#F7FBFD"/>
          <path d="M54 54H592M54 108H592M54 162H592M54 216H592" stroke="#E4EDF2" stroke-width="2"/>
          <path d="M84 200 196 184 308 162 420 138 532 112" fill="none" stroke="#174A7C" stroke-width="5" stroke-linecap="round"/>
          <path d="M84 184 196 176 308 165 420 149 532 132" fill="none" stroke="#2D8BBF" stroke-width="4" stroke-linecap="round"/>
          <path d="M84 166 196 160 308 151 420 141 532 130" fill="none" stroke="#80579B" stroke-width="4" stroke-linecap="round"/>
          <path d="M84 148 196 145 308 140 420 135 532 128" fill="none" stroke="#D79A36" stroke-width="4" stroke-linecap="round"/>
          <path d="M84 214 196 208 308 201 420 194 532 187" fill="none" stroke="#94A0AA" stroke-width="3" stroke-dasharray="5 8"/>
          <circle cx="84" cy="200" r="5" fill="#174A7C"/><circle cx="532" cy="112" r="6" fill="#174A7C" stroke="#FFF" stroke-width="2"/>
          <circle cx="532" cy="128" r="5" fill="#D79A36"/>
        </svg>'''
    if kind == "ashape":
        return '''<svg viewBox="0 0 640 260" role="img" aria-label="历史 A 字形态扫描预览">
          <rect width="640" height="260" rx="18" fill="#F7FBFD"/>
          <path d="M54 54H592M54 108H592M54 162H592M54 216H592" stroke="#E4EDF2" stroke-width="2"/>
          <path d="M58 198 108 188 158 154 198 86 238 132 288 158 338 206" fill="none" stroke="#174A7C" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="198" cy="86" r="7" fill="#D79A36" stroke="#FFF" stroke-width="3"/>
          <path d="M246 216 296 188 346 162 382 118 418 154 468 186 518 214" fill="none" stroke="#2D8BBF" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="382" cy="118" r="7" fill="#D79A36" stroke="#FFF" stroke-width="3"/>
          <path d="M388 226 438 210 488 194 518 168 548 194 578 214 606 230" fill="none" stroke="#80579B" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="518" cy="168" r="7" fill="#D79A36" stroke="#FFF" stroke-width="3"/>
          <path d="M54 230H606" stroke="#DDE8ED" stroke-width="2"/>
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
    status = entry.get("status_label") or ("可打开" if available else "报告未生成")
    status_class = entry.get("status_class") or ("ready" if available else "unavailable")
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
        <div class="card-kicker"><span>{escape(entry["kind"])}</span><span class="status {escape(status_class, quote=True)}">{escape(status)}</span></div>
        <h2>{escape(entry["title"])}</h2>
        <p class="subtitle">{escape(entry["subtitle"])}</p>
        <p class="description">{escape(entry["description"])}</p>
        <div class="meta"><span>数据截至 <strong>{escape(data_date)}</strong></span><span>页面生成 <strong>{updated}</strong></span></div>
        <div class="card-actions">{report_link}<span class="local-mark">本机文件</span></div>
      </div>
    </article>'''


def render(entries: list[dict]) -> str:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    cards = "\n".join(card(entry) for entry in entries)
    manual_rows = "".join(
        f"<tr><td>{escape(entry['title'])}</td><td><code>{escape(entry.get('command', '见 README'))}</code></td></tr>"
        for entry in entries
    )
    available = sum(entry["report"] is not None and entry["report"].is_file() for entry in entries)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>AKsignal · 本地看板</title>
  {FAVICON_LINK}
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
    .summary {{ display: flex; align-items: center; gap: 10px; margin: 0 0 8px; color: var(--muted); font-size: 13px; }}
    .summary strong {{ color: var(--blue); font-size: 19px; }}
    .summary-dot {{ width: 5px; height: 5px; border-radius: 50%; background: var(--gold); }}
    .hint {{ margin: 0 0 17px; color: var(--muted); font-size: 11px; line-height: 1.6; }}
    .hint code {{ padding: 2px 5px; border-radius: 4px; background: #FFF; border: 1px solid #E1E8EC; color: var(--blue); font-size: 10px; }}
    .hint a {{ color: var(--blue); text-decoration: none; border-bottom: 1px solid #B9D9E8; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 20px; }}
    .manual {{ margin-top: 20px; padding: 14px 18px; border: 1px solid #DDE8ED; border-radius: 14px; background: #FFF; }}
    .manual summary {{ cursor: pointer; color: var(--blue); font-size: 12px; font-weight: 700; }}
    .manual p {{ margin: 10px 0; color: var(--muted); font-size: 11px; line-height: 1.6; }}
    .manual table {{ width: 100%; border-collapse: collapse; font-size: 11px; }}
    .manual td {{ padding: 7px 8px; border-top: 1px solid #EDF1F3; vertical-align: top; }}
    .manual td:first-child {{ width: 150px; color: var(--text); font-weight: 600; }}
    .manual code {{ color: var(--blue); word-break: break-all; }}
    .dashboard-card {{ overflow: hidden; border: 1px solid #DDE8ED; border-radius: 17px; background: var(--card); box-shadow: 0 12px 30px #174A7C0D; transition: transform .18s ease, box-shadow .18s ease; }}
    .dashboard-card:hover {{ transform: translateY(-3px); box-shadow: 0 16px 34px #174A7C17; }}
    .preview {{ height: 184px; padding: 14px 14px 0; background: linear-gradient(180deg, #EDF7FB, #F9FCFD); }}
    .preview svg {{ width: 100%; height: 100%; display: block; }}
    .card-body {{ padding: 17px 19px 18px; }}
    .card-kicker, .meta, .card-actions {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; }}
    .card-kicker {{ color: var(--blue); font-size: 11px; font-weight: 700; letter-spacing: .04em; }}
    .status {{ padding: 4px 8px; border-radius: 99px; font-size: 10px; letter-spacing: 0; }}
    .status.ready {{ background: #EAF5EE; color: #32704C; }}
    .status.paused {{ background: #FFF3E5; color: #8A5B20; }}
    .status.fixed {{ background: #EAF3F8; color: #245B7A; }}
    .status.unavailable {{ background: #F1F3F5; color: #7A8792; }}
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
    @media (max-width: 1040px) {{ .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
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
    <p class="summary"><strong>{available:02d}</strong> 个可用看板 <span class="summary-dot"></span> 按数据截止日降序 · 本机离线入口</p>
    <p class="hint">手动更新：重建对应报告后运行 <code>make dashboards-index</code>；各看板命令见 <a href="README.md">dashboards/README.md</a>。日报当前暂停发布。</p>
    <section class="grid" aria-label="看板列表">
      {cards}
    </section>
    <details class="manual">
      <summary>手动更新命令</summary>
      <p>静态页不执行 shell；在终端运行对应命令后，再执行 <code>make dashboards-index</code> 刷新本页。也可以直接告诉我更新哪个看板。</p>
      <div class="table-wrap"><table><tbody>{manual_rows}</tbody></table></div>
    </details>
    <footer><span>本地目录 · 不上传报告与行情快照</span><span>目录刷新 {escape(now)}</span></footer>
  </main>
</body>
</html>
'''


def main() -> None:
    entries = [
        daily_entry(),
        holdings_entry(),
        csi500_entry(),
        basket_entry(),
        ic500_entry(),
        ashape_entry(),
    ]
    entries.sort(key=lambda entry: (data_date_key(entry.get("data_date")), entry.get("updated_at", 0)), reverse=True)
    CATALOG.write_text(render(entries), encoding="utf-8")
    print(f"Updated {CATALOG.relative_to(ROOT)} ({len(entries)} dashboards)")
    for entry in entries:
        state = "available" if entry["report"] and entry["report"].is_file() else "unavailable"
        print(f"- {entry['id']}: {state}, data through {format_date(entry['data_date'])}")


if __name__ == "__main__":
    main()
