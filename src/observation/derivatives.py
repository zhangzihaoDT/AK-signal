"""Render listed derivatives without generating prices or selecting a trade."""
from html import escape

from .core_indices import number

REASONS = {'MISSING_TARGET_BATCH':'缺少目标日期货快照', 'FETCH_FAILED':'官方日行情获取失败',
           'INVALID_SOURCE_DATA':'官方行情字段或完整性验收失败',
           'INCONSISTENT_OPEN_INTEREST_CHANGE':'持仓变化与前日不一致',
           'INCONSISTENT_PRE_SETTLE':'前结算价与前日结算不一致',
           'SESSION_INCOMPLETE':'源文件获取时尚未收盘'}


def panel(document):
    if document is None:
        return ''
    rows = ''
    for r in document['rows']:
        name = r['contract'] or r['product']
        if r['is_main_open_interest']:
            name += ' · 持仓最多'
        cells = [escape(name), number(r['close']), number(r['return_1d'], True), number(r['settle']),
                 '—' if r['volume'] is None else f"{r['volume']:,}",
                 '—' if r['open_interest'] is None else f"{r['open_interest']:,}",
                 number(r['basis_points']), number(r['basis_rate'], True),
                 escape(r['source_trade_date'] or '—'),
                 '可用' if r['status']=='AVAILABLE' else 'UNAVAILABLE · '+escape(REASONS.get(r['reason'],r['reason']))]
        rows += '<tr data-contract="'+escape(r['contract'] or r['product'],quote=True)+'">'+''.join('<td'+(' class="numeric"' if 1 <= i <= 7 else '')+'>'+v+'</td>' for i,v in enumerate(cells))+'</tr>'
    heads = ('具体合约','收盘点位','当日涨跌','结算点位','成交量（手）','持仓量（手）','基差（点）','基差率','实际行情日','数据状态')
    return ('<article class="panel" id="derivatives-observation"><h3>衍生品观察 · IC 中证500股指期货</h3>'
            '<p class="section-note">来源：中金所官方日行情。IC 为品种代码，下表为当日挂牌的具体到期合约；持仓最多仅标记当日持仓量最大合约。</p>'
            '<p class="section-note">当日涨跌按同一合约前收盘计算；结算价单列。基差 = 期货收盘 − 同批中证500现货收盘，基差率 = 基差 / 现货收盘；不作分红调整或年化。“—”表示对应数据不足。</p>'
            '<div class="table-scroll"><table class="index-observation"><thead><tr>'+''.join('<th scope="col">'+h+'</th>' for h in heads)+'</tr></thead><tbody>'+rows+'</tbody></table></div>'
            '<p class="section-note">每点200元是合约乘数；表中涨跌为报价变化，不是保证金收益。当前不拼接主力连续行情。</p></article>')


def markdown(document):
    lines = ['## 衍生品观察 · IC 中证500股指期货', '', '来源：中金所官方日行情；具体到期合约，按同一合约前收盘计算当日涨跌。', '',
             '| 具体合约 | 收盘 | 当日涨跌 | 结算 | 成交量（手） | 持仓量（手） | 基差（点） | 基差率 | 实际行情日 | 状态 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---|---|']
    for r in document['rows']:
        cells = [r['contract'] or r['product'], number(r['close']), number(r['return_1d'],True), number(r['settle']),
                 str(r['volume']) if r['volume'] is not None else '—', str(r['open_interest']) if r['open_interest'] is not None else '—',
                 number(r['basis_points']), number(r['basis_rate'],True), r['source_trade_date'] or '—',
                 '可用' if r['status']=='AVAILABLE' else 'UNAVAILABLE · '+REASONS.get(r['reason'],r['reason'])]
        lines.append('| '+' | '.join(cells)+' |')
    lines += ['', '基差=期货收盘−同批中证500现货收盘；基差率=基差/现货收盘。不含分红调整或年化，不是保证金收益。', '']
    return '\n'.join(lines)


def quality_line(document):
    m = document['module']
    return (f"衍生品观察：IC具体合约 {m['available_contract_count']}/{m['expected_contract_count']} 可用；"
            '中金所官方按日期命名的HTTP日行情文件，原始内容摘要可追溯；CN有界独立日历为日期代理，不称中金所全历史认证。'
            '不拼接连续合约，现货基准来源与快照记录在同日derivatives产物。' +
            ('' if m['status']=='available' else ' 本批UNAVAILABLE：'+REASONS.get(document['rows'][0]['reason'],document['rows'][0]['reason'])))
