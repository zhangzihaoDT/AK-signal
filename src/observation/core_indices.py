"""Presentation of fixed index facts; never selects or recalculates prices."""
from html import escape


REASONS = {
    'PROVIDER_SYMBOL_UNVERIFIED': '供应商代码映射待核验',
    'IDENTITY_UNVERIFIED': '供应商指数身份待核验',
    'FETCH_FAILED': '本批指数行情获取失败',
    'MISSING_TARGET_BATCH': '缺少目标日指数快照',
    'MISSING_TARGET': '缺少目标日报价',
    'INSUFFICIENT_HISTORY': '历史不足',
    'DATA_GAP': '交易日报价缺口',
    'CALENDAR_UNCOVERED': '交易日历未覆盖',
    'TARGET_NOT_SESSION': '目标日非交易日',
    'SESSION_INCOMPLETE': '目标日尚未收盘',
    'SOURCE_DATE_UNVERIFIED': '源交易日待核验',
    'NON_SESSION_BARS': '存在非交易日报价',
}
TRENDS = {'RISING': '均线上行', 'FALLING': '均线下行', 'MIXED': '均线交错', 'UNKNOWN': '待数据完整'}
PROVIDERS = {'longbridge': '长桥 OpenAPI', 'csindex': '中证指数官方'}


def source_names(rows):
    return ' / '.join(PROVIDERS.get(p, p) for p in sorted({r['provider'] for r in rows}))


def status_text(row):
    return '可用' if row['status'] == 'AVAILABLE' else row['status'] + ' · ' + REASONS.get(row['reason'], row['reason'])


def number(value, percent=False):
    if value is None:
        return '—'
    return f'{value:+.2%}' if percent else f'{value:,.2f}'


def panels(document, category_filter=None):
    if document is None:
        return ''
    result = []
    for category, title in (('broad', '核心宽基观察'), ('style', '风格参照')):
        if category_filter is not None and category != category_filter:
            continue
        rows = [r for r in document['rows'] if r['category'] == category]
        body = ''
        for row in rows:
            body += '<tr data-index-key="' + escape(row['key'], quote=True) + '"><td><div class="asset-name">' + escape(row['name']) + '<span class="asset-code">' + escape(row['issuer'] + ' · ' + row['code']) + '</span></div></td>'
            body += '<td class="numeric">' + number(row['close']) + '</td>'
            for n in (1, 5, 20, 60):
                value = row[f'return_{n}d']
                reason = row[f'return_{n}d_status']
                hint = '' if reason == 'OK' else REASONS.get(reason, reason)
                tone = 'missing' if value is None else 'positive' if value > 0 else 'negative' if value < 0 else ''
                body += '<td class="numeric ' + tone + '" title="' + escape(hint, quote=True) + '">' + number(value, True) + '</td>'
            body += '<td>' + TRENDS[row['trend_state']] + '</td><td>' + escape(PROVIDERS.get(row['provider'], row['provider'])) + '</td><td>' + escape(row['source_trade_date'] or '—') + '</td><td>' + escape(status_text(row)) + '</td></tr>'
        heads = ('指数', '收盘点位', '当日涨跌', '5日涨跌', '20日涨跌', '60日涨跌', '均线状态', '来源', '实际行情日', '数据状态')
        result.append('<article class="panel" id="core-indices-' + category + '"><h3>' + title + '</h3>'
                      '<p class="section-note">固定清单 · 全部展示；' + escape(source_names(rows)) + '价格指数日线。缺失保留，不以 ETF 替代。</p>'
                      '<div class="table-scroll"><table class="index-observation"><thead><tr>' + ''.join('<th scope="col">' + h + '</th>' for h in heads) + '</tr></thead><tbody>' + body + '</tbody></table></div></article>')
    return ''.join(result)


def markdown(document, category_filter=None):
    rows = [r for r in document['rows'] if category_filter is None or r['category'] == category_filter]
    title = '风格参照' if category_filter == 'style' else '核心宽基观察'
    lines = ['## '+title, '', '固定指数清单；来源：' + source_names(rows) + '，价格指数。', '',
             '| 类别 | 指数 | 收盘点位 | 当日 | 5日 | 20日 | 60日 | 均线状态 | 来源 | 实际行情日 | 数据状态 |',
             '|---|---|---:|---:|---:|---:|---:|---|---|---|---|']
    for r in rows:
        cells = ['宽基' if r['category'] == 'broad' else '风格参照', r['name'] + ' ' + r['code'], number(r['close']),
                 *[number(r[f'return_{n}d'], True) for n in (1, 5, 20, 60)],
                 TRENDS[r['trend_state']], PROVIDERS.get(r['provider'], r['provider']), r['source_trade_date'] or '—', status_text(r)]
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines) + '\n'


def quality_line(document):
    module = document['module']
    missing = '；'.join(r['name'] + '：' + status_text(r) for r in document['rows'] if r['status'] != 'AVAILABLE')
    return (f"核心指数观察：{module['available_count']}/{module['expected_count']} 可用；"
            f"{module['broad_count']} 个宽基与 {module['style_count']} 个风格参照独立于 ETF 产品池，价格指数不含分红再投资。"
            '均线上行/下行要求价格在 MA20、MA60 同侧，且两条均线近5交易日同向变化；其余为交错。'
            '来源/价格口径、日历与输入摘要见同日 core_indices 产物。' + (' 缺失：' + missing if missing else ''))
