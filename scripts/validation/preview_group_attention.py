"""Render accepted facts locally; no fetch, signal replay or publication."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import pandas as pd
from src.observation.group_attention import attention_view
from src.observation.report import render_html, REPORT_LAYOUT_VERSION, validate_activity_rendering, SCRIPT


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def section_preview(html, title, script=SCRIPT):
    section = re.search(r'<section id="stock-observation".*?</section>', html, re.S).group()
    head = html.split('</head>')[0] + '</head>'
    return (head + '<body><main><header><div class="kicker">AKsignal · 2026-10-23 · 本地展示验收</div>'
            '<h1>'+title+'</h1><p class="section-note">仅重渲染已验收事实；未发布，未改变信号。</p>'
            '<nav><a href="section04.html">新版 04</a><a href="section04_before.html">旧版 04</a>'
            '<a href="observation_20261023.html#stock-observation">完整日报</a></nav></header>'
            + section + '</main>' + script + '</body></html>')


def preview(source, destination):
    if destination.exists():
        raise ValueError('预览须使用新的空目录，保留旧预览与验收产物')
    date = '20261023'
    folder = source / 'observation'
    activity_path = folder / f'stock_group_activity_{date}.json'
    summary_path = folder / f'observation_{date}.json'
    verification = json.loads((source / 'verification.json').read_text())
    if not verification.get('check_ok'):
        raise ValueError('来源目录没有通过既有验收')
    activity = json.loads(activity_path.read_text())
    summary = json.loads(summary_path.read_text())
    if summary['group_activity']['sha256'] != digest(activity_path):
        raise ValueError('已验收事实与回执摘要不一致')
    source_files = [p for p in folder.iterdir() if p.is_file()]
    source_files += list((source / 'stock_metrics').glob('*')) + [source / 'verification.json']
    before = {str(p): digest(p) for p in source_files if p.is_file()}
    original = deepcopy(activity)
    frame = lambda stem: pd.read_parquet(folder / f'{stem}_{date}.parquet')
    html = render_html(date, frame('asset_directions'), frame('industry_rotation'), frame('etf_trends'),
        pd.read_parquet(source / 'stock_metrics' / f'stock_metrics_{date}.parquet'),
        frame('etf_positions'), frame('stock_positions'), summary['data_quality'],
        module_status=summary.get('modules'), group_activity=activity)
    validate_activity_rendering(date, activity, summary['group_activity'], html)
    assert activity == original
    summary['report_layout_version'] = REPORT_LAYOUT_VERSION
    summary['group_attention'] = attention_view(activity)
    summary['preview_only'] = True
    destination.mkdir(parents=True)
    (destination / f'observation_{date}.html').write_text(html)
    (destination / f'observation_{date}.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    # Byte-identical copy retains source receipt / evidence references in preview JSON.
    (destination / activity_path.name).write_bytes(activity_path.read_bytes())
    (destination / 'section04.html').write_text(section_preview(html, '研究注意力分配简报'))
    old = (folder / f'observation_{date}.html').read_text()
    (destination / 'section04_before.html').write_text(section_preview(old, '旧版 · 规则结果展示', script=''))
    after = {str(p): digest(p) for p in source_files if p.is_file()}
    assert before == after
    receipt = dict(trade_date=date, source=str(source.resolve()), source_files_sha256=before,
        source_files_unchanged=True, input_facts_unchanged=True,
        activity_sha256=digest(activity_path), preview_activity_sha256=digest(destination / activity_path.name),
        original_json_states_unchanged=summary['group_activity'] == json.loads(summary_path.read_text())['group_activity'],
        rendered_evidence_and_anchors_valid=True, group_count=len(activity['groups']),
        report_layout_version=REPORT_LAYOUT_VERSION,
        presentation=summary['group_attention'])
    (destination / 'render_verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return destination / 'section04.html'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/audits/group_activity/20261023/trend_start_v2'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(preview(args.source, args.output).resolve())
