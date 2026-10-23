"""Offline HTML renderer for the reviewed comparable-basket dataset."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
from plotly.offline import get_plotlyjs

COLORS = ['#FF7A2F', '#2878A9', '#437C68', '#174A7C', '#8A6BA8', '#7F743E', '#279EAD', '#9D5965', '#596C34']
REFERENCE_COLOR = '#B08A4A'
BENCHMARK_COLOR = '#374957'

SHORT = {
    'hk_resources_shipping': 'AZ 仓位观察',
    'scania_china_watch': '商用车产业链',
    'china_auto_global_ex_oem': '汽车全球化 · 剔除 OEM',
    'ai_capex': 'AI Capex',
    'china_auto_global': '汽车全球化 · 仅 OEM',
    'memory_and_storage': '半导体存储',
    'auto_tier1_ai_infra': '汽车供应链 → AI 液冷',
    'auto_tier1_embodied': '汽车供应链 → Physical AI',
    'high_cashflow': '高现金流资产',
}

STATUS_TONE = {
    '强且广': 'cool',
    '强但窄': 'warm',
    '弱但广': 'quiet',
    '弱且窄': 'quiet',
    '—': 'quiet',
}


def _static_svg(series, dates, limits):
    lo, hi = limits
    pad = max((hi - lo) * .06, 1)
    lo, hi = lo - pad, hi + pad
    x = lambda i: 60 + i / max(len(dates) - 1, 1) * 880
    y = lambda v: 300 - (float(v) - lo) / (hi - lo) * 270
    grid = ''.join(
        f'<line x1="60" x2="940" y1="{y(lo+(hi-lo)*i/4):.2f}" y2="{y(lo+(hi-lo)*i/4):.2f}" stroke="#e6edf1"/>'
        f'<text x="50" y="{y(lo+(hi-lo)*i/4)+4:.2f}" text-anchor="end" font-size="12" fill="#556270">'
        f'{lo+(hi-lo)*i/4:.0f}</text>'
        for i in range(5)
    )
    lines = []
    for label, values, color, dashed in series:
        path = ' '.join(
            f'{"M" if i == 0 else "L"}{x(i):.2f},{y(v):.2f}'
            for i, v in enumerate(values)
        )
        lines.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-dasharray="{7 if dashed else 0}"><title>{html.escape(label)}</title></path>'
        )
    return (
        '<svg class="chart-fallback" viewBox="0 0 960 340" role="img" '
        'aria-label="离线静态走势预览" style="width:100%;height:100%">'
        + grid + ''.join(lines)
        + f'<text x="60" y="330" font-size="12">{dates[0]}</text>'
        + f'<text x="940" y="330" font-size="12" text-anchor="end">{dates[-1]}</text></svg>'
    )


def _pct(value, digits=1, signed=False):
    if value is None or pd.isna(value):
        return '—'
    return f'{value:+.{digits}f}%' if signed else f'{value:.{digits}f}%'


def _pp(value, digits=1):
    if value is None or pd.isna(value):
        return '—'
    return f'{value:+.{digits}f} pp'


def _num(value, digits=1):
    if value is None or pd.isna(value):
        return '—'
    return f'{value:.{digits}f}'


def _tone(value):
    if value is None or pd.isna(value) or value == 0:
        return ''
    return 'pos' if value > 0 else 'neg'


def _participation(value, positive, valid, total=None):
    if value is None or pd.isna(value):
        return '—'
    title = f'有效样本 {valid}/{total}' if total is not None else ''
    return (
        f'<span title="{html.escape(title)}">{value * 100:.1f}% '
        f'<span class="muted">({positive}/{valid})</span></span>'
    )


def _badge(status):
    tone = STATUS_TONE.get(status, 'quiet')
    return f'<span class="badge {tone}">{html.escape(status or "—")}</span>'


def _window_card(results, window, windows):
    definition = windows[window]
    mainlines = [row for row in results if row.get('role') == 'mainline']
    mainlines.sort(key=lambda row: (row['metrics_quarterly'][window].get('rank') is None,
                                    row['metrics_quarterly'][window].get('rank') or 999))
    benchmark_return = results[0]['metrics_quarterly'][window]['benchmark_return_pct']
    hidden = ' hidden' if window != 'all' else ''
    header = (
        '<thead><tr><th>排名</th><th>观察池</th>'
        '<th class="num">QR 区间收益</th><th class="num">BH 区间收益</th>'
        '<th class="num">QR−BH</th><th class="num">QR 超额</th>'
        '<th class="num">QR RS20Δ</th><th class="num">区间参与度</th>'
        '<th class="num">20日参与度</th><th>状态</th></tr></thead>'
    )
    body = []
    for row in mainlines:
        q = row['metrics_quarterly'][window]
        b = row['metrics_buy_and_hold'][window]
        diff = None if q['return_pct'] is None or b['return_pct'] is None else q['return_pct'] - b['return_pct']
        body.append(
            '<tr>'
            f'<td class="rank">{q.get("rank") or "—"}</td>'
            f'<td><b>{html.escape(row["label"])}</b></td>'
            f'<td class="num {_tone(q["return_pct"])}">{_pct(q["return_pct"], signed=True)}</td>'
            f'<td class="num {_tone(b["return_pct"])}">{_pct(b["return_pct"], signed=True)}</td>'
            f'<td class="num {_tone(diff)}">{_pp(diff)}</td>'
            f'<td class="num {_tone(q["excess_pp"])}">{_pp(q["excess_pp"])}</td>'
            f'<td class="num {_tone(q["rs_20d_pct"])}">{_pct(q["rs_20d_pct"], signed=True)}</td>'
            f'<td class="num">{_participation(q["interval_participation"], q["interval_positive"], q["interval_valid"], q["interval_total"])}</td>'
            f'<td class="num">{_participation(q["participation_20d"], q["participation_20d_positive"], q["participation_20d_valid"], q["participation_20d_total"])}</td>'
            f'<td>{_badge(q["status"])}</td>'
            '</tr>'
        )
    reference_header = (
        '<thead><tr><th>观察池</th>'
        '<th class="num">QR 区间收益</th><th class="num">BH 区间收益</th>'
        '<th class="num">QR−BH</th><th class="num">QR 超额</th>'
        '<th class="num">QR RS20Δ</th><th class="num">区间参与度</th>'
        '<th class="num">20日参与度</th><th>状态</th></tr></thead>'
    )
    reference_rows = []
    for row in [item for item in results if item.get('role') == 'reference']:
        q = row['metrics_quarterly'][window]
        b = row['metrics_buy_and_hold'][window]
        diff = None if q['return_pct'] is None or b['return_pct'] is None else q['return_pct'] - b['return_pct']
        reference_rows.append(
            '<tr>'
            f'<td><b>{html.escape(row["label"])}</b> <span class="muted">参照</span></td>'
            f'<td class="num {_tone(q["return_pct"])}">{_pct(q["return_pct"], signed=True)}</td>'
            f'<td class="num {_tone(b["return_pct"])}">{_pct(b["return_pct"], signed=True)}</td>'
            f'<td class="num {_tone(diff)}">{_pp(diff)}</td>'
            f'<td class="num {_tone(q["excess_pp"])}">{_pp(q["excess_pp"])}</td>'
            f'<td class="num {_tone(q["rs_20d_pct"])}">{_pct(q["rs_20d_pct"], signed=True)}</td>'
            f'<td class="num">{_participation(q["interval_participation"], q["interval_positive"], q["interval_valid"], q["interval_total"])}</td>'
            f'<td class="num">{_participation(q["participation_20d"], q["participation_20d_positive"], q["participation_20d_valid"], q["participation_20d_total"])}</td>'
            f'<td>{_badge(q["status"])}</td>'
            '</tr>'
        )
    return (
        f'<section class="card scroll" data-window-table="{window}"{hidden}>'
        f'<h3>主线强弱 · {html.escape(definition["label"])}</h3>'
        f'<p class="caption">沪深300同期 {_pct(benchmark_return, signed=True)} · '
        '表格按季度再平衡口径；排名按 QR 区间超额降序；BH 为买入持有对照；RS20Δ 与区间起点无关。'
        '</p>'
        f'<table class="summary-table">{header}<tbody>{"".join(body)}</tbody></table>'
        '<h3 class="subhead">参照：高现金流资产</h3>'
        f'<table class="summary-table compact">{reference_header}<tbody>{"".join(reference_rows)}</tbody></table>'
        '</section>'
    )


def _structure_table(results, overlap):
    mainlines = [row for row in results if row.get('role') == 'mainline']
    rows = []
    for row in mainlines:
        c = row['concentration']
        o = overlap.get('per_basket', {}).get(row['key'], {})
        cash = c.get('cash_sleeve_weight') or 0
        cash_text = (
            f'{cash * 100:.1f}% <span class="muted">({c.get("cash_stock_count", 0)}只)</span>'
            if cash > 0 else '—'
        )
        single_title = '、'.join(c.get('single_stock_group_labels') or []) or '无'
        largest = c.get('largest_stock') or {}
        max_overlap = (
            f'{html.escape(str(o.get("max_overlap_label", "—")))} · '
            f'{(o.get("max_overlap_weight") or 0) * 100:.1f}%'
            if o.get('max_overlap_key') else '—'
        )
        rows.append(
            '<tr>'
            f'<td><b>{html.escape(row["label"])}</b></td>'
            f'<td class="num">{c.get("group_count", "—")}</td>'
            f'<td class="num" title="其中上市前现金 {c.get("cash_stock_count", 0)} 只">'
            f'{c.get("stock_count", "—")}</td>'
            f'<td class="num" title="最大：{html.escape(str(largest.get("name", "")))}">'
            f'{(c.get("max_target_weight") or 0) * 100:.1f}%</td>'
            f'<td class="num">{_num(c.get("effective_stock_count"))}</td>'
            f'<td class="num" title="{html.escape(single_title)}">{c.get("single_stock_groups", 0)}</td>'
            f'<td class="num">{cash_text}</td>'
            f'<td class="num">{(o.get("unique_weight") or 0) * 100:.1f}%</td>'
            f'<td>{max_overlap}</td>'
            '</tr>'
        )
    header = (
        '<thead><tr><th>观察池</th><th class="num">组数</th>'
        '<th class="num">股票数</th><th class="num">最大目标权重</th>'
        '<th class="num">有效股票数</th><th class="num">单票子组</th>'
        '<th class="num">现金袖套</th><th class="num">独有股票权重</th>'
        '<th>最大重叠</th></tr></thead>'
    )
    return (
        '<section class="card scroll"><h3>结构与集中度</h3>'
        '<p class="caption">最大目标权重和有效股票数按完整观察起点的股票目标权重计算，'
        '剔除现金袖套后归一化；现金袖套单独列示。参与度与目标权重无关。'
        '最大重叠为与另一主线的 Σmin(w_a,w_b)，仅作诊断。</p>'
        f'<table class="summary-table">{header}<tbody>{"".join(rows)}</tbody></table></section>'
    )


def _rebalance_table(results):
    rows = []
    for row in results:
        records = row.get('rebalance_turnover') or []
        if not records:
            continue
        total = sum(float(item.get('turnover') or 0) for item in records)
        last = records[-1]
        reference = ' <span class="muted">参照</span>' if row.get('role') == 'reference' else ''
        rows.append(
            '<tr>'
            f'<td><b>{html.escape(row["label"])}</b>{reference}</td>'
            f'<td class="num">{len(records)}</td>'
            f'<td>{html.escape(str(last.get("date", "—")))}</td>'
            f'<td class="num">{total * 100:.1f}%</td>'
            f'<td class="num">{(last.get("turnover") or 0) * 100:.1f}%</td>'
            '</tr>'
        )
    if not rows:
        return ''
    header = (
        '<thead><tr><th>观察池</th><th class="num">再平衡次数</th>'
        '<th>最近再平衡</th><th class="num">累计单边换手</th>'
        '<th class="num">最近单边换手</th></tr></thead>'
    )
    return (
        '<section class="card scroll"><h3>季度再平衡记录（QR）</h3>'
        '<p class="caption">只有 HS300 日历确认到达自然季度末才执行；数据末日不是季度末时不重置。'
        '换手按单边计算，不含交易费用；上市前现金袖套调仓也计入。</p>'
        f'<table class="summary-table">{header}<tbody>{"".join(rows)}</tbody></table></section>'
    )


def _shared_stock_table(overlap):
    shared = overlap.get('shared_stocks') or []
    if not shared:
        body = '<tr><td colspan="4" class="empty">8 条主线之间没有共同成分。</td></tr>'
    else:
        rows = []
        for row in shared:
            baskets = ' · '.join(
                f'{html.escape(str(entry["label"]))}'
                f'{"（参照）" if entry.get("role") == "reference" else ""} '
                f'<span class="muted">({entry["weight"] * 100:.1f}%)</span>'
                for entry in row['baskets']
            )
            rows.append(
                '<tr>'
                f'<td>{html.escape(str(row["name"]))}</td>'
                f'<td>{html.escape(str(row["market"]))} {html.escape(str(row["symbol"]))}</td>'
                f'<td class="num">{row["basket_count"]}</td>'
                f'<td>{baskets}</td>'
                '</tr>'
            )
        body = ''.join(rows)
    header = (
        '<thead><tr><th>股票</th><th>代码</th>'
        '<th class="num">出现篮子数</th><th>篮子（目标权重）</th></tr></thead>'
    )
    return (
        '<section class="card scroll"><h3>跨篮子重叠</h3>'
        '<p class="caption">重叠只用于解释主线相关性，不调整任何权重；同一股票在不同篮子中分别计入。</p>'
        f'<table class="summary-table">{header}<tbody>{body}</tbody></table></section>'
    )


def _overlap_matrix(results, overlap):
    mainlines = [row for row in results if row.get('role') == 'mainline']
    labels = {row['key']: row.get('short') or row['label'] for row in mainlines}
    pairs = {(row['key_a'], row['key_b']): row for row in overlap.get('pairs', [])}
    header = '<tr><th>观察池</th>' + ''.join(
        f'<th class="num">{html.escape(labels[row["key"]])}</th>' for row in mainlines
    ) + '</tr>'
    body = []
    for row in mainlines:
        cells = [f'<td><b>{html.escape(labels[row["key"]])}</b></td>']
        for column in mainlines:
            if row['key'] == column['key']:
                cells.append('<td class="num muted">—</td>')
                continue
            pair = pairs.get((row['key'], column['key'])) or pairs.get((column['key'], row['key']))
            if not pair:
                cells.append('<td class="num muted">—</td>')
            else:
                cells.append(
                    f'<td class="num">{pair["shared_count"]}'
                    f'<br><span class="muted">{pair["shared_weight"] * 100:.1f}%</span></td>'
                )
        body.append('<tr>' + ''.join(cells) + '</tr>')
    return (
        '<details><summary>两两重叠矩阵（共享股票数 / min 权重）</summary>'
        '<div class="scroll"><table class="summary-table matrix">'
        f'<thead>{header}</thead><tbody>{"".join(body)}</tbody></table></div>'
        '<p class="caption">min 权重 = Σ min(w_a, w_b)，仅作诊断，不是可加总仓位。</p>'
        '</details>'
    )


def _weights_details(results):
    blocks = []
    for row in results:
        q = {(item['market'], item['symbol']): item for item in row['weights_quarterly']}
        b = {(item['market'], item['symbol']): item for item in row['weights_buy_and_hold']}
        keys = sorted(q, key=lambda key: (-(q[key].get('target_weight') or 0), key[0], key[1]))
        body = []
        for key in keys:
            qr = q[key]
            bh = b.get(key, {})
            cash = '<span class="badge quiet">是</span>' if qr.get('cash_until_first_close') else '—'
            body.append(
                '<tr>'
                f'<td>{html.escape(str(qr.get("name", key[1])))}</td>'
                f'<td>{html.escape(key[0])} {html.escape(key[1])}</td>'
                f'<td class="num">{(qr.get("target_weight") or 0) * 100:.2f}%</td>'
                f'<td class="num">{(qr.get("ending_weight") or 0) * 100:.2f}%</td>'
                f'<td class="num">{(bh.get("ending_weight") or 0) * 100:.2f}%</td>'
                f'<td>{html.escape(str(qr.get("first_price_date", "")))}</td>'
                f'<td class="num">{_pp(qr.get("contribution_pp"))}</td>'
                f'<td class="num">{_pp(bh.get("contribution_pp"))}</td>'
                f'<td>{cash}</td>'
                '</tr>'
            )
        reference = '（参照）' if row.get('role') == 'reference' else ''
        blocks.append(
            f'<details><summary>{html.escape(row["label"])}{reference} · 成分、目标/期末权重与区间贡献</summary>'
            '<div class="scroll"><table class="summary-table">'
            '<thead><tr><th>股票</th><th>代码</th><th class="num">目标权重</th>'
            '<th class="num">QR 期末</th><th class="num">BH 期末</th>'
            '<th>首个价格日</th><th class="num">QR 贡献</th>'
            '<th class="num">BH 贡献</th><th>上市前现金处理</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>'
            '<p class="caption">QR 与 BH 共享同一名单、起点、价格快照、现金规则和交易日历；唯一差异是季度再平衡。贡献为逐日 P&amp;L 相加，与净值变动对账。</p>'
            '</details>'
        )
    return ''.join(blocks)


def _policy_html(receipt):
    excluded = receipt.get('excluded') or []
    excluded_text = '、'.join(
        f'{row.get("name", row.get("symbol"))} {row.get("market")} {row.get("symbol")}'
        for row in excluded
    ) or '无'
    return (
        '<h2>计算口径与数据质量</h2><section class="card"><ul>'
        '<li><b>产品定位：</b>Compare 是投资主线的结构化观察系统，不是投资组合回测。'
        '核心目标是识别主线的趋势、相对强弱和上涨广度，并通过集中度与重叠诊断解释结果。</li>'
        '<li><b>权重：</b>组间等权、组内等权；目标是固定结构权重，不是市值加权或基本面加权。</li>'
        '<li><b>再平衡：</b>自然季度最后一个已确认 HS300 交易日收盘重置目标权重，下一交易日生效；'
        '数据只到季度中途时，最后一个数据日不是再平衡日。'
        '换手记录在 manifest，不扣交易费用。</li>'
        '<li><b>QR/BH：</b>两条路径共享同一股票名单、起点、价格快照、上市前现金规则和 HS300 交易日历，'
        '唯一差异是季度再平衡；manifest 的 comparison_control 记录成分、初始权重、快照和日历哈希。</li>'
        '<li><b>上市前现金：</b>目标权重对应的资金在首个真实收盘价前按现金处理；QR 在季度末把现金袖套调回目标权重，'
        'BH 不调仓；不补造上市前价格。</li>'
        '<li><b>区间切换：</b>完整路径只算一次；近一年、YTD、近三个月只把同一路径在起点重新归一化，不重新等权。'
        '区间起点在季度中间时，权重是上次再平衡后漂移的权重。</li>'
        '<li><b>RS20Δ：</b>100 × (RS_t / RS_{t−20} − 1)，使用 20 个 HS300 交易日；'
        '与区间起点无关，取不到 t−20 时显示缺失值。</li>'
        '<li><b>参与度：</b>按股票数量等权计数，不使用组合权重；同时给区间参与度和 20 日参与度。'
        '晚上市或历史不足的股票从分母剔除；停牌沿用最近价格，平盘计为非上涨。</li>'
        '<li><b>集中度：</b>最大目标权重和有效股票数按完整观察起点的股票目标权重计算，'
        '剔除现金袖套后归一化；现金袖套单独列示，不进入股票 HHI。</li>'
        '<li><b>重叠：</b>同一股票在不同篮子中分别计入；重叠诊断只解释相关性，不调整权重。</li>'
        '<li><b>价格与基准：</b>个股 Longbridge ForwardAdjust 收盘价，沪深300 NoAdjust 指数；'
        '本币、不换汇、不计费用；不宣称为已验证含息总回报或历史可执行策略。</li>'
        '<li><b>样本限制：</b>当前名单是静态名单重建，不是历史时点成分回测；'
        '跨篮子同股、晚上市、停牌和供应商复权差异仍需结合数据质量阅读。</li>'
        f'<li><b>排除：</b>{html.escape(excluded_text)}。</li>'
        '<li><b>可复核产物：</b>comparison_nav.csv（季度再平衡默认）、comparison_nav_bh.csv（买入持有对照）、'
        'basket_metrics.csv、basket_participation.csv、basket_overlap.csv、basket_shared_stocks.csv、'
        'comparison_manifest.json、各篮子 nav/weights 文件。</li>'
        '</ul><details><summary>来源与快照</summary>'
        f'<p>快照目录：{html.escape(str(receipt.get("source_snapshot", "")))}。'
        f'方法论版本：{html.escape(str(receipt.get("methodology_version", "")))}。</p>'
        '<p>数据来源：个股全部为长桥 OpenAPI · Longbridge ForwardAdjust；'
        '沪深300同样来自长桥，使用未复权指数值。仅使用源端收盘价，独立快照标记 close_only。</p>'
        '</details></section>'
    )


def render_comparison(results, receipt, output: Path):
    if not results:
        raise ValueError('no comparison results')
    windows = receipt['windows']
    mainlines = [row for row in results if row.get('role') == 'mainline']
    references = [row for row in results if row.get('role') == 'reference']
    colors = {}
    mainline_index = 0
    for row in results:
        if row.get('role') == 'reference':
            colors[row['key']] = REFERENCE_COLOR
        else:
            colors[row['key']] = COLORS[mainline_index % len(COLORS)]
            mainline_index += 1

    dates = results[0]['nav_quarterly'].index.strftime('%Y-%m-%d').tolist()
    benchmark_values = results[0]['nav_quarterly']['benchmark'].tolist()
    payload = {
        'dates': dates,
        'benchmark': benchmark_values,
        'windows': windows,
        'defaultMethod': receipt.get('default_method', 'quarterly'),
        'methods': receipt.get('methods', ['quarterly', 'buy_and_hold']),
        'baskets': [{
            'key': row['key'],
            'label': row['label'],
            'role': row.get('role', 'mainline'),
            'color': colors[row['key']],
            'short': SHORT.get(row['key'], row['label']),
            'series': {
                'quarterly': row['nav_quarterly']['basket'].tolist(),
                'buy_and_hold': row['nav_buy_and_hold']['basket'].tolist(),
            },
            'metrics': {
                'quarterly': row['metrics_quarterly'],
                'buy_and_hold': row['metrics_buy_and_hold'],
            },
            'concentration': row['concentration'],
        } for row in results],
    }

    limits = (
        min(min(row['nav_quarterly']['basket'].min() for row in results),
            min(row['nav_quarterly']['benchmark'].min() for row in results)),
        max(max(row['nav_quarterly']['basket'].max() for row in results),
            max(row['nav_quarterly']['benchmark'].max() for row in results)),
    )
    static_series = []
    for row in mainlines + references:
        static_series.append((
            row['label'] + ('（参照）' if row.get('role') == 'reference' else ''),
            row['nav_quarterly']['basket'].tolist(),
            colors[row['key']],
            row.get('role') == 'reference',
        ))
    static_series.append(('沪深300', benchmark_values, BENCHMARK_COLOR, True))
    static_main = _static_svg(static_series, dates, limits)

    cards = []
    for index, row in enumerate(results):
        card_limits = (
            min(row['nav_quarterly']['basket'].min(), row['nav_quarterly']['benchmark'].min()),
            max(row['nav_quarterly']['basket'].max(), row['nav_quarterly']['benchmark'].max()),
        )
        card_series = [
            (row['label'], row['nav_quarterly']['basket'].tolist(), colors[row['key']],
             row.get('role') == 'reference'),
            ('沪深300', benchmark_values, BENCHMARK_COLOR, True),
        ]
        title = row['label'] + ('（参照）' if row.get('role') == 'reference' else '')
        cards.append(
            f'<section class="card small"><h3>{html.escape(title)}</h3>'
            f'<div id="small-{index}" class="small-chart">'
            f'{_static_svg(card_series, dates, card_limits)}</div></section>'
        )

    table_cards = ''.join(_window_card(results, window, windows) for window in windows)
    structure = _structure_table(results, receipt.get('overlap') or {})
    rebalances = _rebalance_table(results)
    shared = _shared_stock_table(receipt.get('overlap') or {})
    matrix = _overlap_matrix(results, receipt.get('overlap') or {})
    weights = _weights_details(results)
    policy = _policy_html(receipt)

    assets = len({
        (row['market'], row['symbol'])
        for result in results
        for row in (result.get('weights_quarterly') or [])
    })
    document = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>观察篮子 · 主线强弱</title><style>
:root{--bg:#F8F6F0;--text:#253746;--muted:#556270;--line:#dce4e8;--accent:#FF7A2F;--blue:#2878A9;--gold:#B08A4A}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}.wrap{max-width:1360px;margin:auto;padding:38px 28px 64px}.eyebrow{font-size:12px;letter-spacing:.15em;color:var(--muted)}h1{font-size:32px;margin:8px 0}h2{font-size:21px;margin:32px 0 12px}h3{font-size:15px;font-weight:600;margin:2px 10px}.subhead{margin:22px 0 8px;font-size:16px}.muted{color:var(--muted)}.meta{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0 22px;font-variant-numeric:tabular-nums}.meta strong{display:block;font-size:22px}.card{background:white;border:1px solid var(--line);border-radius:10px;padding:20px;margin:16px 0}.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.control-label{font-size:12px;color:var(--muted);letter-spacing:.06em;margin-left:6px}button{background:white;border:1px solid var(--line);border-radius:6px;padding:8px 13px;color:var(--text);font:inherit;cursor:pointer}button.active{background:#e7f1f8;border-color:var(--blue)}button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}.caption{font-size:13px;color:var(--muted);margin:10px 0}.main-chart{height:560px}.scatter-chart{height:470px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.small{min-width:0;margin:0;padding:14px 8px}.small-chart{height:265px}.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle}th{background:#f0f6fa;color:var(--muted);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}tr:hover td{background:#f7fbfe}.rank{font-weight:700;color:var(--muted)}.badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:12px;font-weight:600;background:#edf1f4;color:#687581}.badge.cool{background:#e7f1f8;color:#24608a}.badge.warm{background:#fff1e7;color:#a84b14}.badge.quiet{background:#edf1f4;color:#687581}.pos{color:#28608a}.neg{color:#a84b14}.empty{text-align:center;color:var(--muted);padding:24px}.note{border-left:3px solid var(--accent);padding:8px 16px;background:#fff}details{margin:12px 0;border-top:1px solid var(--line);padding-top:12px}summary{cursor:pointer;font-weight:600}.matrix td,.matrix th{padding:7px 8px;font-size:12px}footer{font-size:12px;color:var(--muted);margin-top:28px}@media(max-width:700px){.wrap{padding:22px 14px}h1{font-size:26px}.card{padding:12px}.grid{grid-template-columns:1fr}.main-chart{height:650px}.scatter-chart{height:520px}.meta{gap:18px}.meta strong{font-size:19px}}
</style><script>__PLOTLY__</script></head><body><main class="wrap"><div class="eyebrow">AKSIGNAL / RESEARCH BASKETS</div><h1>观察篮子 · 主线强弱</h1><p class="muted">8 条研究主线 + 高现金流参照 · 与沪深300比较 · 季度再平衡为默认结构口径</p><div class="meta"><div><span class="muted">完整研究窗口</span><strong>__START__ — __END__</strong></div><div><span class="muted">观察范围</span><strong>__MAINLINE_COUNT__ 主线 + __REFERENCE_COUNT__ 参照 / __ASSETS__ 只股票</strong></div><div><span class="muted">比较交易日</span><strong>__ROWS__ 日</strong></div><div><span class="muted">方法论</span><strong>__METHODOLOGY__</strong></div></div><p class="note">Compare 是投资主线的结构化观察系统，不是投资组合回测。净值用于识别趋势，RS 用于识别相对强弱，参与度用于识别上涨广度；集中度与重叠诊断解释结果。季度再平衡只用于保持主题结构，买入持有仅作对照。</p><section class="card"><div class="controls" aria-label="图表区间"><span class="control-label">区间</span><button class="active" data-range="all">完整区间</button><button data-range="1y">近一年</button><button data-range="ytd">YTD</button><button data-range="3m">近三个月</button><span class="control-label">口径</span><button class="active" data-method="quarterly">季度再平衡</button><button data-method="buy_and_hold">买入持有</button><button id="mode">切换：超额走势</button><button id="reset">显示全部</button></div><p class="caption" id="window-caption"></p><div id="main-chart" class="main-chart">__STATIC__</div><p class="caption">沪深300为深灰虚线；高现金流为金色点线参照。区间切换只把同一净值路径在起点重新归一化，不重新等权；季度中间起点的权重是上次再平衡后漂移的权重。</p></section><h2>主线强弱</h2><p class="caption">表格与散点按季度再平衡口径；BH 列为买入持有对照。超额 = 区间收益 − 沪深300同期收益；RS20Δ = 100 × (RS_t / RS_{t−20} − 1)，与区间起点无关。</p>__TABLES__<h2>主线定位</h2><p class="caption">横轴为季度再平衡区间的相对沪深300超额（pp），纵轴为同一区间的股票参与度（%）。高现金流为空心参照，不参与主线排名。</p><section class="card"><div id="main-scatter" class="scatter-chart"></div></section>__STRUCTURE____REBALANCES____SHARED____MATRIX__<h2>逐篮子对比</h2><p class="caption">与主图使用相同口径；各小图默认按季度再平衡，切换口径时同步更新。</p><div class="grid">__CARDS__</div><h2>成分与权重</h2><section class="card">__WEIGHTS__</section>__POLICY__<footer>研究观察 / 静态名单重建 / 结构指数不是可执行组合收益 · 数据截至 __END__</footer></main><script id="reviewed-data" type="application/json">__DATA__</script><script>
document.querySelectorAll('.chart-fallback').forEach(el=>el.remove());
const data=JSON.parse(document.getElementById('reviewed-data').textContent);
let range='all',method=data.defaultMethod||'quarterly',mode='nav';
const config={responsive:true,displaylogo:false,modeBarButtonsToRemove:['select2d','lasso2d']};
const methodLabel={quarterly:'季度再平衡',buy_and_hold:'买入持有'};
function seriesWindow(){const w=data.windows[range];const dates=data.dates.slice(w.start_index,w.end_index+1);const bmRaw=data.benchmark.slice(w.start_index,w.end_index+1);const bm=bmRaw.map(v=>v/bmRaw[0]*100);const baskets=data.baskets.map(b=>{const raw=b.series[method].slice(w.start_index,w.end_index+1);const norm=raw.map(v=>v/raw[0]*100);return {...b,values:mode==='excess'?norm.map((v,k)=>v-bm[k]):norm}});return {dates,bm:mode==='excess'?bm.map(()=>0):bm,baskets}}
function makeTrace(b){const ref=b.role==='reference';return {x:[],y:[],name:(ref?b.short+'（参照）':b.short),mode:'lines',line:{color:b.color,width:ref?1.8:(b.key==='hk_resources_shipping'?3:2),dash:ref?'dot':'solid'},hovertemplate:'%{y:.2f}<extra>%{fullData.name}</extra>'}}
function draw(){const w=seriesWindow();const traces=w.baskets.map(b=>{const t=makeTrace(b);t.x=w.dates;t.y=b.values;return t});const bm={x:w.dates,y:w.bm,name:'沪深300',mode:'lines',line:{color:'#374957',width:2.6,dash:'dash'},hovertemplate:'%{y:.2f}<extra>沪深300</extra>'};const narrow=document.documentElement.clientWidth<700;const layout={width:document.getElementById('main-chart').clientWidth,height:document.getElementById('main-chart').clientHeight,autosize:true,paper_bgcolor:'#fff',plot_bgcolor:'#fff',font:{family:'-apple-system, PingFang SC, sans-serif',size:13,color:'#556270'},margin:{l:62,r:20,t:15,b:narrow?230:105},hovermode:'x unified',legend:{orientation:'h',y:-.2,x:0,font:{size:12}},xaxis:{gridcolor:'#edf1f4',nticks:7,tickformat:'%Y-%m'},yaxis:{title:{text:mode==='nav'?'归一化净值':'超额（百分点）'},gridcolor:'#e6edf1'},uirevision:method+range+mode};Plotly.react('main-chart',[...traces,bm],layout,config);w.baskets.forEach((b,i)=>{const t=makeTrace(b);t.x=w.dates;t.y=b.values;const el=document.getElementById('small-'+i);if(!el)return;Plotly.react('small-'+i,[t,bm],{...layout,width:el.clientWidth,height:265,margin:{l:55,r:16,t:12,b:40},showlegend:false,yaxis:{...layout.yaxis,title:undefined,autorange:true,range:undefined},xaxis:{...layout.xaxis,nticks:4}},config)});document.getElementById('window-caption').textContent=w.dates[0]+' — '+w.dates.at(-1)+' · '+methodLabel[method]+' · '+(mode==='excess'?'相对沪深300超额（百分点），起点=0':'净值起点=100')+' · 区间切换只重新归一化，不重新等权'}
function drawScatter(){const w=data.windows[range];const rows=data.baskets.map(b=>({...b,m:b.metrics.quarterly[range]}));const main=rows.filter(b=>b.role==='mainline');const ref=rows.filter(b=>b.role==='reference');const colors={'强且广':'#2878A9','强但窄':'#FF7A2F','弱但广':'#279EAD','弱且窄':'#8996A0','—':'#8996A0'};const mainTrace={x:main.map(b=>b.m.excess_pp),y:main.map(b=>b.m.interval_participation==null?null:b.m.interval_participation*100),text:main.map(b=>b.short),mode:'markers+text',textposition:'top center',marker:{size:13,color:main.map(b=>colors[b.m.status]||'#8996A0'),line:{color:'#fff',width:1}},customdata:main.map(b=>[b.m.return_pct,b.m.participation_20d==null?null:b.m.participation_20d*100,b.m.rs_20d_pct]),hovertemplate:'%{text}<br>超额 %{x:+.2f} pp<br>区间参与度 %{y:.1f}%<br>20日参与度 %{customdata[1]:.1f}%<br>RS20Δ %{customdata[2]:+.2f}%<extra></extra>',name:'研究主线'};const refTrace={x:ref.map(b=>b.m.excess_pp),y:ref.map(b=>b.m.interval_participation==null?null:b.m.interval_participation*100),text:ref.map(b=>b.short+'（参照）'),mode:'markers+text',textposition:'top center',marker:{size:15,color:'rgba(0,0,0,0)',line:{color:'#B08A4A',width:2}},hovertemplate:'%{text}<br>超额 %{x:+.2f} pp<br>区间参与度 %{y:.1f}%<extra></extra>',name:'参照'};const layout={height:470,paper_bgcolor:'#fff',plot_bgcolor:'#fff',font:{family:'-apple-system, PingFang SC, sans-serif',size:13,color:'#556270'},margin:{l:60,r:24,t:20,b:55},hovermode:'closest',xaxis:{title:{text:'QR 区间超额（pp）'},zeroline:true,zerolinecolor:'#c9d6de',gridcolor:'#edf1f4'},yaxis:{title:{text:'区间参与度（%）'},range:[0,100],zeroline:false,gridcolor:'#edf1f4'},shapes:[{type:'line',x0:0,x1:0,y0:0,y1:1,yref:'paper',line:{color:'#c9d6de',width:1,dash:'dot'}},{type:'line',x0:0,x1:1,xref:'paper',y0:50,y1:50,line:{color:'#c9d6de',width:1,dash:'dot'}}],annotations:[{x:0.99,y:0.99,xref:'paper',yref:'paper',text:'强且广',showarrow:false,font:{color:'#2878A9',size:12},xanchor:'right'},{x:0.01,y:0.99,xref:'paper',yref:'paper',text:'弱但广',showarrow:false,font:{color:'#279EAD',size:12},xanchor:'left'},{x:0.99,y:0.02,xref:'paper',yref:'paper',text:'强但窄',showarrow:false,font:{color:'#FF7A2F',size:12},xanchor:'right'},{x:0.01,y:0.02,xref:'paper',yref:'paper',text:'弱且窄',showarrow:false,font:{color:'#8996A0',size:12},xanchor:'left'}]};Plotly.react('main-scatter',[mainTrace,refTrace],layout,config)}
function showTables(){document.querySelectorAll('[data-window-table]').forEach(el=>{el.hidden=el.dataset.windowTable!==range});document.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('active',b.dataset.range===range))}
document.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>{range=b.dataset.range;showTables();draw();drawScatter()});
document.querySelectorAll('[data-method]').forEach(b=>b.onclick=()=>{method=b.dataset.method;document.querySelectorAll('[data-method]').forEach(x=>x.classList.toggle('active',x===b));draw()});
document.getElementById('mode').onclick=()=>{mode=mode==='nav'?'excess':'nav';document.getElementById('mode').textContent=mode==='nav'?'切换：超额走势':'切换：净值走势';draw()};
document.getElementById('reset').onclick=()=>{Plotly.restyle('main-chart',{visible:true})};
window.addEventListener('resize',()=>{draw();drawScatter()});
document.querySelectorAll('[data-method]').forEach(x=>x.classList.toggle('active',x.dataset.method===method));
showTables();draw();drawScatter();
</script></body></html>'''
    replacements = {
        '__PLOTLY__': get_plotlyjs(),
        '__DATA__': json.dumps(payload, ensure_ascii=False).replace('<', '\\u003c'),
        '__STATIC__': static_main,
        '__CARDS__': ''.join(cards),
        '__TABLES__': table_cards,
        '__STRUCTURE__': structure,
        '__REBALANCES__': rebalances,
        '__SHARED__': shared,
        '__MATRIX__': matrix,
        '__WEIGHTS__': weights,
        '__POLICY__': policy,
        '__START__': str(receipt['start']),
        '__END__': str(receipt['end']),
        '__ROWS__': str(receipt['rows']),
        '__MAINLINE_COUNT__': str(len(mainlines)),
        '__REFERENCE_COUNT__': str(len(references)),
        '__ASSETS__': str(assets),
        '__METHODOLOGY__': str(receipt.get('methodology_version', '')),
    }
    for key, value in replacements.items():
        document = document.replace(key, value)
    output = Path(output)
    output.write_text(document, encoding='utf-8')
    return output
