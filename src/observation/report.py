"""Four module sections plus separate data quality; renderer creates no facts."""
from html import escape
import json
from pathlib import Path
from urllib.parse import quote
import pandas as pd
from src.observation.group_attention import attention_view

PRODUCT_VERSION = (Path(__file__).resolve().parents[2] / 'VERSION').read_text().strip()
REPORT_LAYOUT_VERSION = 'daily_observation_modules_v10'
PREVIEW_LIMIT = 20

# Embedded geometry keeps the AK mark portable and independent of installed fonts.
FAVICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               '<title>AK</title><rect width="64" height="64" rx="12" fill="#C0392B"/>'
               '<path d="M8 46 20 16 32 46 M12 36H28 M39 16V46 M55 16 39 31 55 46" '
               'fill="none" stroke="#FFFFFF" stroke-width="4.5" stroke-linecap="round" '
               'stroke-linejoin="round"/></svg>')
FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' + quote(FAVICON_SVG, safe='') + '">'

PRINCIPLE = 'AKsignal 负责发现和量化市场事实，你负责理解这些事实并作出投资决策。'
LABELS = {'rps1':'当日 RPS','RPS1':'当日 RPS','daily_return':'当日涨幅','direction':'资产方向','median_rps15':'RPS15 中位','median_rps5':'RPS5 中位','median_rps1':'当日 RPS 中位','change_5d':'排名变化中位数','active_ratio':'趋势活跃比例',
          'direction_state':'方向状态','industry_name':'行业','RPS15':'RPS15','RPS5':'RPS5','trend_stage':'阶段',
          'fund_code':'代码','fund_name':'ETF','rps15':'RPS15','price':'价格','ma20':'MA20','ma60':'MA60',
          'market_session_status':'市场状态','source_trade_date':'实际行情日',
          'market':'市场','symbol':'代码','name':'股票','trend_score':'趋势分','return_5d':'5日收益','return_20d':'20日收益',
          'bottom_state':'底部状态','price_pos_60':'60日位置','price_pos_120':'120日位置','price_pos_360':'360日位置',
          'bottom_change':'底部变化','reliability':'窗口可靠性','position_reliability':'窗口可靠性'}
STATES = {'closed':'休市','open':'开市','EXCLUDED':'暂不观察','DEEP_BOTTOM':'长期深底','RECOVERING_FROM_BOTTOM':'低位恢复','LONG_TERM_LOW':'长期低位',
          'RECENT_BOTTOM':'近期低位','NORMAL':'非低位','UNAVAILABLE':'未知','UNRELIABLE':'未知',
          'ENTERED_BOTTOM':'进入底部','LEFT_BOTTOM':'离开底部','UNCHANGED':'未变化','UNKNOWN':'变化未知',
          'OK':'有效','DATA_GAP':'报价缺口','INSUFFICIENT_HISTORY':'历史不足','FLAT_WINDOW':'无波动',
          'CORPORATE_ACTION':'异常价格跳变','FLAT_PRICE_ASSET':'不适用','MISSING_TARGET':'缺目标日报价'}
CSS = r'''
:root{--bg:#F8F6F0;--panel:#fff;--ink:#263746;--muted:#556270;--line:#dce5eb;--blue:#6BB6E8;--accent:#FF7A2F;--cool:#eaf5fc;--warm:#fff1e7}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth;scroll-padding-top:78px}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 system-ui,-apple-system,sans-serif}
main{max-width:1120px;margin:auto;padding:24px 20px}header{margin-bottom:20px;padding:18px 0 0;border-top:4px solid var(--blue)}h1{font-size:27px;letter-spacing:-.5px;margin:8px 0}h2{font-size:21px;margin:0 0 6px}h3{font-size:15px;margin:0;color:var(--ink)}p{margin:8px 0}.kicker{font-size:12px;letter-spacing:1.6px;color:var(--muted)}.principle{color:var(--muted);font-size:13px;max-width:850px}
nav{position:sticky;top:0;z-index:10;display:flex;gap:6px;flex-wrap:wrap;padding:6px;background:rgba(248,246,240,.97);border:1px solid var(--line);margin:16px 0 22px;backdrop-filter:blur(8px)}a{color:#28608a;text-decoration:none}nav a{padding:6px 14px;border-radius:4px}nav a:hover,nav a:focus-visible{background:var(--cool);outline:2px solid var(--blue);outline-offset:1px}
section{margin-bottom:24px}.section-note{color:var(--muted);margin:0 0 10px;font-size:12px}.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}.panel{background:var(--panel);border:1px solid var(--line);padding:14px;min-width:0;border-radius:6px}.panel h3{margin-bottom:10px;display:flex;align-items:center;gap:10px}.panel h3:before{content:"";width:4px;height:18px;background:var(--blue);display:block}.position-grid{grid-template-columns:1fr}
.module-panels{display:grid;gap:14px}.source-note{padding:4px 0 8px;border-bottom:1px solid var(--line);margin-bottom:12px}.table-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px}.table-toolbar input{font:inherit;font-size:13px;padding:6px 9px;border:1px solid var(--line);border-radius:4px;max-width:100%;width:180px;background:#fbfdfe}.table-toolbar input:focus{outline:2px solid var(--blue);outline-offset:1px}.table-note{font-size:12px;color:var(--muted)}
.table-scroll{overflow-x:auto;overflow-y:visible;max-height:none}table{width:100%;border-collapse:separate;border-spacing:0;white-space:normal;font-size:12px;line-height:1.4}th{text-align:left;color:var(--muted);font-size:12px;font-weight:600;background:#f1f6f9;white-space:normal}th,td{padding:6px 7px;border-bottom:1px solid var(--line);vertical-align:middle}tr:hover td{background:#f7fbfe}.numeric{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.asset-name{white-space:normal;min-width:100px;max-width:180px;line-height:1.4;font-weight:600}.asset-code{display:block;font:11px/1.6 ui-monospace,monospace;letter-spacing:.3px;color:var(--muted);font-weight:400;margin-top:1px}
.badge{display:inline-flex;align-items:center;gap:5px;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;background:#edf1f4;color:var(--muted)}.badge:before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor}.badge.cool{background:var(--cool);color:#24608a}.badge.warm{background:var(--warm);color:#a84b14}.badge.quiet{background:#edf1f4;color:#687581}.score{display:flex;justify-content:flex-end;align-items:center;gap:5px;font-variant-numeric:tabular-nums}.score-track{width:28px;height:3px;background:#e5edf2;border-radius:3px;overflow:hidden}.score-fill{display:block;height:100%;background:var(--blue)}.score.low .score-fill{background:var(--accent)}.positive{color:#28608a}.negative{color:#a84b14}.missing{color:#8996a0}.empty{padding:28px;text-align:center;color:var(--muted)}
details{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}summary{cursor:pointer;color:#28608a;font-size:13px;padding:4px 0}summary:focus-visible{outline:2px solid var(--blue)}details .table-scroll{margin-top:8px}.quality{border-top:2px solid var(--line);padding-top:16px}.quality ul{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:1fr 1fr;gap:12px}.quality li{background:#f7f9fa;padding:10px 12px;border-left:3px solid var(--line);font-size:13px}.quality li.attention{border-left-color:var(--accent);background:#fff8f2}.quality-label{display:block;font-weight:600;margin-bottom:5px}.quality-detail{color:var(--muted)}
.panel h3{flex-wrap:wrap}.panel[id]{scroll-margin-top:78px}
.attention-headline{font-size:18px;line-height:1.8;margin:8px 0 12px;max-width:960px}.attention-summary{border-top:3px solid var(--blue)}.attention-summary h3:before{background:var(--accent)}
.attention-brief{margin:10px 0 16px;padding:2px 12px;border-left:3px solid var(--line)}.attention-brief p{margin:5px 0;font-size:14px}.opportunity-label{display:block;font-size:12px;font-weight:600;margin-bottom:4px;color:var(--muted)}
.group-overview{table-layout:fixed;font-size:13px}.group-overview th:nth-child(1){width:20%}.group-overview th:nth-child(2){width:18%}.group-overview th:nth-child(3){width:47%}.group-overview th:nth-child(4){width:15%}.group-overview td{padding:11px 8px;overflow-wrap:anywhere}.group-overview .rule-state{display:block;color:var(--muted);font-size:11px;font-weight:400;margin-top:3px}.group-overview .focus-row td:first-child{border-left:3px solid var(--accent)}
.group-evidence{margin:0;padding:14px}.group-evidence>summary{font-size:15px;font-weight:600;color:var(--ink);list-style-position:outside;margin-left:16px}.group-evidence .evidence-body{padding-top:12px}.evidence-text{line-height:1.9;max-width:960px}.group-risk{border-left:3px solid var(--accent);padding-left:10px;color:#864116}.trace-list{overflow-wrap:anywhere;font-size:12px}.trace-list code{white-space:normal}.group-evidence:target{border-color:var(--blue)}
@media(max-width:600px){.attention-headline{font-size:16px}.group-overview,.group-overview tbody,.group-overview tr,.group-overview td{display:block;width:100%}.group-overview thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.group-overview tr{padding:10px 0;border-bottom:1px solid var(--line)}.group-overview td{border:0;padding:3px 8px}.group-overview td[data-label]:before{content:attr(data-label) ' · ';font-size:12px;color:var(--muted)}.group-overview .rule-state{display:inline;margin-left:8px}.group-overview td:first-child{font-weight:600;font-size:15px}.group-overview .focus-row td:first-child{border-left:3px solid var(--accent)}}
@media(max-width:900px){.grid{grid-template-columns:1fr}.quality ul{grid-template-columns:1fr}}@media(max-width:600px){main{padding:18px 12px}header{padding-top:18px}h1{font-size:25px}h2{font-size:21px}.panel{padding:14px}nav{gap:2px;padding:6px}nav a{padding:7px 10px;font-size:12px}th,td{padding:6px 6px}.score-track{width:24px}.asset-name{min-width:100px}}
'''

SCRIPT = r'''
<script>
document.addEventListener('input', function(event) {
  if (!event.target.matches('[data-table-search]')) return;
  const wrapper = event.target.closest('[data-table]');
  const query = event.target.value.trim().toLocaleLowerCase();
  const rows = Array.from(wrapper.querySelectorAll('tbody tr[data-row]')).filter(row => row.closest('[data-table]') === wrapper);
  let matches = 0;
  rows.forEach(row => { const hit = !query || row.textContent.toLocaleLowerCase().includes(query); row.hidden = !hit; if (hit) matches++; });
  wrapper.querySelectorAll('details[data-remainder]').forEach(detail => {
    if (query && detail.dataset.beforeSearch === undefined) detail.dataset.beforeSearch = String(detail.open);
    if (query) detail.open = true;
    else if (detail.dataset.beforeSearch !== undefined) { detail.open = detail.dataset.beforeSearch === 'true'; delete detail.dataset.beforeSearch; }
  });
  const result = wrapper.querySelector('[data-search-result]');
  if (result) result.textContent = query ? ('匹配 ' + matches + ' 项') : '名称、代码或状态均可搜索';
});
function openGroupEvidence() {
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (target && target.matches('details.group-evidence')) target.open = true;
}
window.addEventListener('hashchange', openGroupEvidence);
document.addEventListener('click', function(event) {
  const link = event.target.closest('a[href^="#stock-group-"]');
  if (!link) return;
  const target = document.getElementById(link.getAttribute('href').slice(1));
  if (target && target.matches('details')) target.open = true;
});
openGroupEvidence();
</script>
'''

def activity_detail(row):
    """Format recorded facts only; never infer a trend in presentation."""
    clues = row.get('leader_clues', [])
    leader_text = (' 个股领先线索（非组级启动）：' + '、'.join(
        f"{r['market']}:{r['symbol']} {r['return_1d']:+.1%}（{'新线索' if r['first_clue'] else '延续'}）" for r in clues) + '。') if clues else ''
    if row.get('status') == 'UNAVAILABLE':
        reason = row.get('reason', '')
        label = {'MARKET_CLOSED': '涉及市场休市', 'INSUFFICIENT_HISTORY': '历史不足',
                 'HISTORY_OR_CLOSED_ENDPOINT': '历史不足或参与度窗口端点休市',
                 'INSUFFICIENT_NATIVE_HISTORY':'原生收益历史不足',
                 'INSUFFICIENT_STRUCTURAL_HISTORY':'结构历史预热不足',
                 'INCOMPLETE_STRUCTURAL_METRICS':'结构指标不完整', 'NATIVE_HISTORY_GAP':'原生收益缺口'}
        return '无法判定：' + label.get(reason, '数据不齐或行情缺口') + '；未作今日判定。' + leader_text
    m = row['metrics']
    path = {'SUDDEN': '突发式', 'GRADUAL': '渐进式', 'TRACKING': '已有趋势跟踪'}.get(row.get('path'), '无启动路径')
    origin = {'EARLY_CANDIDATE':'早期候选事件', 'EXISTING_TREND':'已有趋势观察'}.get(row.get('event_origin'),'无事件来源')
    if row['status']=='NO_SETUP' and row.get('event_origin'):
        origin = '上次已结束事件：' + origin
    text = (f"{row['status']} · {origin} · {path} · 组级首次触发：{'是' if row['first_trigger'] else '否'}；"
            f"QR结构指数 {m['nav']:.2f} / MA5 {m['ma5']:.2f} / MA20 {m['ma20']:.2f}，5日 {m['return_5d']:+.1%}；"
            f"RS20Δ {m['rs_20d_pct']:+.2f}%（RS20Δ 较5日前变化 {m['rs_20d_change_5d_pp']:+.2f}pp）；"
            f"5/10/20日参与度 {m['participation_5d']:.0%} / {m['participation_10d']:.0%} / {m['participation_20d']:.0%}。")
    if row.get('previous_observation_date'):
        c = row['changes']
        text += f" 较上一有效观察日 {row['previous_observation_date']}：{row['previous_status']} → {row['status']}；"
        if 'nav' in c:
            text += f"结构指数 {c['nav']:+.2f}、"
        text += (f"RS20Δ {c['rs_20d_pct']:+.2f}pp、10日参与度 {c['participation_10d']*100:+.2f}pp。")
        if 'participation_5d' in c:
            text += f" 5日参与度较上一有效观察日 {c['participation_5d']*100:+.2f}pp。"
    if row.get('reasons'):
        text += ' ' + '；'.join(row['reasons']) + '。'
    channels = {'COLLECTIVE':'集体异动', 'RS_IMPROVEMENT':'RS改善',
                'PARTICIPATION_EXPANSION':'参与度扩散', 'RS_AND_PARTICIPATION':'RS与参与度共同改善'}
    text += (f" 事件首次证据：{channels.get(row.get('trigger_channel'), row.get('trigger_channel') or '无')}；"
             f"当日证据：{channels.get(row.get('evidence_channel'), row.get('evidence_channel') or '无')}。")
    text += ' 数据完整。' if row.get('data_complete') is True else ' 数据完整性未确认。'
    return text + leader_text


def activity_summary(date, activity):
    from src.market_facts.group_activity import RULE_VERSION
    if (not activity or activity.get('trade_date') != pd.Timestamp(date).strftime('%Y%m%d')
            or activity.get('rule_version') != RULE_VERSION):
        return '<article class="panel"><h3>分组趋势启动观察</h3><p class="section-note">无法判定：数据缺失或日期/规则版本不一致；未作今日判定。</p></article>', {}
    rows = activity.get('groups', [])
    annotations = {r['key']: r for r in rows}
    view = attention_view(activity)
    overview = []
    for r in view['groups']:
        tone = 'warm' if r['priority'] == '优先研究' else 'cool' if r['priority'] == '重点观察' else 'quiet'
        state_label = r['event'] if r['status'] in r['event'] else r['status']+' · '+r['event']
        overview.append('<tr'+(' class="focus-row"' if r['key'] in view['highlighted_keys'] else '')+'>'
            '<td><a href="#stock-group-'+escape(r['key'], quote=True)+'" title="'+escape(r['full_label'], quote=True)+'">'+escape(r['label'])+'</a></td>'
            '<td data-label="趋势阶段">'+escape(r['phase'])+'<span class="rule-state">'+escape(state_label)+'</span></td>'
            '<td data-label="今日变化">'+escape(r['today'])+'</td>'
            '<td data-label="阅读顺序">'+('<span class="opportunity-label">'+escape(r['opportunity'])+'</span>' if r['opportunity'] != r['priority_label'] else '')+
            '<span class="badge '+tone+'">'+escape(r['priority_label'])+'</span></td></tr>')
    unavailable = sum(r['priority'] == '待数据完整' for r in view['groups'])
    note = f'另有 {unavailable} 组数据或展示依据不齐，未作趋势阶段判定；不可解释为没有机会。' if unavailable else ''
    brief = ''.join('<div class="attention-brief"><span class="opportunity-label">'+escape(section['kind'])+'</span>'+
                    ''.join('<p><strong>'+escape(r['label'])+'</strong>：'+escape(r['reason'])+'。</p>' for r in section['items'])+
                    '</div>' for section in view['summary_sections'])
    return ('<article class="panel attention-summary"><h3>今日观察结论</h3>'
            '<p class="attention-headline"><strong>'+escape(view['headline'])+'</strong></p>'
            +brief+'<p class="section-note">“机会”指研究观察线索；新增与已有分别标识，同档并列，仅安排阅读顺序。</p>'
            +('<p class="group-risk">'+escape(note)+'</p>' if note else '')+
            '<h3>'+str(len(rows))+' 个分组速览</h3><table class="group-overview" aria-label="研究分组速览">'
            '<thead><tr><th scope="col">分组</th><th scope="col">趋势阶段 / 规则状态</th><th scope="col">今日变化 · 较上一有效观察日</th><th scope="col">观察优先级</th></tr></thead>'
            '<tbody>'+''.join(overview)+'</tbody></table>'
            '<p class="section-note">上涨范围指对应窗口参与度；pp 为百分点。点击分组查看完整证据。NO_SETUP 仅表示无启动候选，不表示弱势趋势已确认；UNAVAILABLE 表示无法判定。</p>'
            +activity_common_notes(activity)+'</article>'), annotations


def activity_common_notes(activity):
    rows = activity.get('groups', [])
    shared = set(rows[0].get('limitations', [])) if rows else set()
    for row in rows[1:]:
        shared.intersection_update(row.get('limitations', []))
    limits = sorted(shared) or ['候选参数未验证；当前名单与复权快照重建历史，非历史时点回测。',
        '复权算法未认证；price_basis_verified=false。不含汇率/费用，不代表真实组合收益。']
    text = ''.join('<li>'+escape(line)+'</li>' for line in limits)
    return ('<details class="common-limits"><summary>共性限制与解释口径（参数未验证）</summary><ul>'+text+'</ul>'
            '<p>趋势阶段是事实解释，规则状态保留 V2 原值，观察优先级只决定阅读顺序。'
            '优先研究：活跃状态下，价格转强结构与初步支持同时成立，或价格止跌、RS改善和参与度扩散同时成立。'
            '重点观察：活跃状态下，价格高于MA5、5日收益为正且RS改善和参与度扩散成立。'
            '其后依次观察单一路径的修复持续性、价格跟踪、尚缺止跌证据的异动及无启动候选。'
            '不新增统计阈值或综合评分，同档不按涨幅或首次触发排序。</p>'
            '<p>新增机会为本次首次 WATCH；其余活跃状态作为已有机会跟踪，新增不自动优先。'
            '速览先展示首次触发或状态转换，再展示最多两项非零指标变化：先回落，再按10日参与度、5日参与度、相对强弱展示改善。'
            '按两位小数显示为零的变化省略，完整数值留在详情；不代表统计显著性判断。机会与阅读顺序不代表买入概率或预期收益。</p>'
            '<p>所有条件直接读取V2已验收事实；阶段与优先级不能升级 WATCH / EMERGING / CONFIRMED。'
            'pp 为百分点；速览变化相对各组上一有效观察日，详情中“较5日前”是另一时间尺度。'
            '只读本批长桥快照，休市不作零收益观察，单股线索不等于组级启动，不构成交易建议。</p>'
            '<p class="trace-list">规则 '+escape(str(activity.get('rule_version')))+' · 解释 '+escape(attention_view(activity)['display_version'])+
            ' · validation_status='+escape(str(activity.get('validation_status', 'UNVALIDATED')))+
            ' · price_basis_verified='+escape(str(activity.get('price_basis_verified', False)).lower())+
            ' · input_hash='+escape(str(activity.get('input_hash', '未知')))+'</p></details>')


def activity_risks(row, activity):
    """Keep group-specific exceptions next to their evidence, without repeating shared caveats."""
    rows = activity.get('groups', [])
    shared = set(rows[0].get('limitations', [])) if rows else set()
    for other in rows[1:]:
        shared.intersection_update(other.get('limitations', []))
    risks = [line for line in row.get('limitations', []) if line not in shared]
    risks.extend(json.dumps(issue, ensure_ascii=False, sort_keys=True) for issue in row.get('issues', []))
    for identity, limitation in activity.get('history_limitations', {}).items():
        if identity in row.get('snapshot_ids', {}):
            risks.append(identity+' 历史限制：'+str(limitation))
    if row.get('status') == 'UNAVAILABLE':
        risks.insert(0, activity_detail(row))
    return ''.join('<p class="group-risk">'+escape(line)+'</p>' for line in risks)


def activity_explanation(view):
    trace = ''.join('<li><code>'+escape(key)+'</code> = '+escape(json.dumps(value, ensure_ascii=False))+'</li>'
                    for key, value in view['trace'].items())
    return ('<p>'+escape(view['phase']+'：'+view['phase_reason'])+'。</p>'
            '<details><summary>展示标签的事实依据</summary><p>阶段字段：'+escape('、'.join(view['phase_fields']))+
            '；阅读顺序字段：'+escape('、'.join(view['priority_fields']))+'</p><ul class="trace-list">'+trace+'</ul></details>')


def validate_activity_rendering(date, activity, receipt, rendered):
    """Check the receipt and visible section against the accepted facts."""
    expected_states = [{k:r.get(k) for k in ('key','status','first_trigger','path','event_origin','transition','reasons','metrics','leader_clues','evidence_channel','alert_kind','event_active','episode_status','trigger_channel')}
                       for r in activity['groups']]
    if (receipt.get('states') != expected_states or receipt.get('rule_version') != activity['rule_version']
            or receipt.get('input_hash') != activity['input_hash']):
        raise ValueError('日报JSON趋势状态或依据不一致')
    if activity_summary(date, activity)[0] not in rendered or any(
        'id="stock-group-'+r['key']+'"' not in rendered or escape(activity_detail(r)) not in rendered
        for r in activity['groups']):
        raise ValueError('日报HTML趋势状态、依据或链接不一致')


def _cell(value,column,row):
    if value is None or pd.isna(value):
        return '<span class="missing">—</span>'
    text=str(value)
    if column in ('fund_name','name'):
        code=row.get('fund_code') if column=='fund_name' else row.get('symbol')
        market=row.get('market','')
        sub=' · '.join(str(v) for v in (market,code) if v is not None and pd.notna(v) and str(v))
        return '<div class="asset-name">'+escape(text)+'<span class="asset-code">'+escape(sub)+'</span></div>'
    if column == 'source_trade_date':
        return escape(pd.Timestamp(value).date().isoformat())
    if column in ('market_session_status','bottom_state','bottom_change','reliability','position_reliability','trend_stage','direction_state'):
        label=STATES.get(text,text)
        cool=text in ('OK','DEEP_BOTTOM','LONG_TERM_LOW','已形成趋势','强势','增强')
        warm=text in ('ENTERED_BOTTOM','LEFT_BOTTOM','RECOVERING_FROM_BOTTOM','正在启动','DATA_GAP','CORPORATE_ACTION','MISSING_TARGET')
        tone='cool' if cool else 'warm' if warm else 'quiet'
        return f'<span class="badge {tone}">'+escape(label)+'</span>'
    if column in ('rps1','RPS1','rps15','RPS15','RPS5','median_rps15','median_rps5','median_rps1','trend_score','price_pos_60','price_pos_120','price_pos_360'):
        number=float(value);width=max(0,min(100,number))
        low=column.startswith('price_pos_') and number<=20
        return f'<span class="score{ " low" if low else ""}"><span class="score-track" aria-hidden="true"><i class="score-fill" style="width:{width:.2f}%"></i></span><span>{number:.1f}</span></span>'
    if column in ('active_ratio','daily_return','return_5d','return_20d'):
        number=float(value)
        tone=('positive' if number>0 else 'negative' if number<0 else '') if column!='active_ratio' else ''
        return f'<span class="{tone}">{number*100:.1f}%</span>'
    if isinstance(value,(float,int)):
        return f'{value:.2f}'
    return escape(text)


def table(frame,columns,preview_count=PREVIEW_LIMIT,remainder_label="展开其余观察对象"):
    columns=[c for c in columns if c in frame]
    if 'fund_name' in columns and 'fund_code' in columns:columns.remove('fund_code')
    if 'name' in columns:
        columns=[c for c in columns if c not in ('symbol','market')]
    numeric={'rps1','RPS1','daily_return','rps15','RPS15','RPS5','median_rps15','median_rps5','median_rps1','trend_score','price_pos_60','price_pos_120','price_pos_360','active_ratio','return_5d','return_20d','price','ma20','ma60','change_5d'}
    def markup(rows):
        head=''.join('<th scope="col"'+(' class="numeric"' if c in numeric else '')+'>'+escape(LABELS.get(c,c))+'</th>' for c in columns)
        body=''
        for _,row in rows.iterrows():
            body+='<tr data-row>'+''.join('<td'+(' class="numeric"' if c in numeric else '')+'>'+_cell(row.get(c),c,row)+'</td>' for c in columns)+'</tr>'
        if rows.empty:body=f'<tr><td class="empty" colspan="{max(len(columns),1)}">暂无符合当前观察条件的对象</td></tr>'
        return '<div class="table-scroll"><table><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'
    body='<div class="data-table" data-table><div class="table-toolbar"><input type="search" data-table-search aria-label="搜索当前观察列表" placeholder="搜索名称、代码或状态"><span class="table-note" data-search-result aria-live="polite">名称、代码或状态均可搜索</span></div>'+markup(frame.head(preview_count))
    if len(frame)>preview_count:
        body+='<details data-remainder><summary>'+escape(remainder_label)+'</summary>'+markup(frame.iloc[preview_count:])+'</details>'
    return body+'</div>'


def ordered(frame, descending=(), identity=()):
    """Display ordering only: numerical evidence first, identity breaks every tie."""
    keys=[key for key in (*descending,*identity) if key in frame]
    return frame.sort_values(keys,ascending=[key not in descending for key in keys],
                             na_position='last',kind='stable') if keys else frame.copy()


def positions(frame,stock=False):
    d=frame.copy()
    if stock:d=d.drop_duplicates(['market','symbol'])
    if 'reliability' not in d:
        def reliability(row):
            reasons=[row.get(f'window_reason_{w}') for w in (120,360)]
            return next((r for r in reasons if r!='OK'),'OK') or 'UNKNOWN'
        d['position_reliability']=d.apply(reliability,axis=1)
    reliability_column='reliability' if 'reliability' in d else 'position_reliability'
    valid=d[reliability_column].eq('OK')
    d['_priority']=3
    d.loc[valid,'_priority']=2
    d.loc[valid & d.bottom_state.isin(['DEEP_BOTTOM','RECOVERING_FROM_BOTTOM','LONG_TERM_LOW']),'_priority']=1
    d.loc[valid & d.bottom_change.isin(['ENTERED_BOTTOM','LEFT_BOTTOM']),'_priority']=0
    identity=('market','symbol') if stock else ('fund_code',)
    return ordered(d,identity=('_priority','price_pos_360',*identity))


def render_html(date,assets,industries,etfs,stocks,etf_positions,stock_positions,quality,module_status=None,group_activity=None):
    def panel(title,body,rule='',anchor='',activity=None):
        badge = ''
        if activity:
            badge = ' <span class="badge warm">'+escape(activity['status'])+'</span>'
        detail = activity_detail(activity) if activity else ''
        return '<article class="panel"'+(' id="'+escape(anchor, quote=True)+'"' if anchor else '')+'><h3>'+escape(title)+badge+'</h3>'+('<p class="section-note">'+escape(detail)+'</p>' if detail else '')+('<p class="section-note">'+escape(rule)+'</p>' if rule else '')+body+'</article>'
    direction_cols=['direction','median_rps15','median_rps5','median_rps1','change_5d','active_ratio','direction_state']
    industry_cols=['industry_name','RPS15','RPS5','RPS1','daily_return','trend_stage']
    stock_cols=['market','symbol','name','trend_score','return_5d','return_20d']
    if 'identity_status' in stocks:
        stocks=stocks[stocks.identity_status!='IDENTITY_MISMATCH']
    if 'identity_status' in stock_positions:
        stock_positions=stock_positions[stock_positions.identity_status!='IDENTITY_MISMATCH']
    if 'data_status' in stocks:
        stocks=stocks[stocks.data_status!='excluded']
    if 'reliability' in stock_positions:
        stock_positions=stock_positions[stock_positions.reliability!='EXCLUDED']
    stock_all=ordered(stocks,('trend_score','return_20d'),('market','symbol')).drop_duplicates(['market','symbol'])
    stock_preview=min(PREVIEW_LIMIT,int(stock_all.trend_score.ge(70).sum()))
    industries=industries.copy()
    industries['daily_return']=industries.get('return_1d',pd.Series(index=industries.index,dtype=float))
    etfs=etfs.copy()
    # ETF returns are percentage points; TDX returns are decimal fractions.
    etfs['daily_return']=etfs.get('return_1d',pd.Series(index=etfs.index,dtype=float))/100
    industry_view=ordered(industries,('RPS15','RPS5'),('industry_code','industry_name'))
    etf_view=ordered(etfs[etfs.trend_active],('rps15','liquidity'),('fund_code',))
    from src.etf_signal.rotation import CROSS_ASSET_ORDER
    assets=assets.copy()
    assets['_direction_order']=assets.direction.map({v:i for i,v in enumerate(CROSS_ASSET_ORDER)})
    assets=ordered(assets,identity=('_direction_order','direction'))
    industry_state=(module_status or {}).get('industry_rotation') or {}
    industry_unavailable=industry_state.get('status')=='unavailable'
    industry_title=('行业轮动 · 通达信不可用' if industry_state.get('provider')=='tdx' else '行业轮动 · 本批不可用') if industry_unavailable else '行业轮动'
    industry_rule=(f"本批目标日可核验 {industry_state.get('verified_count',0)}/"
                   f"{industry_state.get('expected_count',0)}；不展示旧日期或部分横截面" if industry_unavailable
                   else 'RPS15 降序 → RPS5 降序 → 行业代码；当日指报告交易日，RPS1 为单日收益横截面排名')
    if industry_state.get('provider')=='tdx' and industry_unavailable:
        industry_rule='UNAVAILABLE：通达信目标日更新或质量门控未通过；本批不展示旧行情，不使用申万替代。'
    from src.market_facts.group_activity import definitions
    from src.market_facts.policy import source_policy
    policy_excluded={(str(r['market']),str(r['symbol']).zfill(5 if str(r['market'])=='HK' else 6))
                     for r in source_policy().get('stocks',{}).get('excluded',[])}
    def coverage_note(subset, identities, tail):
        valid=identities-policy_excluded
        excluded=identities&policy_excluded
        note=f'已有行情 {len(subset)}/{len(valid)} 个有效配置成分；'
        if excluded:
            note+=f'另有 {len(excluded)} 个按观察政策排除；'
        return note+tail
    def identity(frame):
        return frame.apply(lambda r: (str(r.market), str(r.symbol).zfill(5 if str(r.market)=='HK' else 6)), axis=1) if not frame.empty else pd.Series(dtype=object)
    stock_all=stock_all.copy()
    stock_all['_identity']=identity(stock_all)
    position_view=positions(stock_positions,True)
    position_view['_identity']=identity(position_view)
    position_fields=[c for c in ('_identity','bottom_state','price_pos_120','price_pos_360','bottom_change','reliability') if c in position_view]
    stock_view=stock_all.merge(position_view[position_fields],on='_identity',how='left',validate='one_to_one')
    stock_columns=['name','market_session_status','source_trade_date','trend_score','return_5d','return_20d','bottom_state','price_pos_120','price_pos_360','reliability']
    activity_html, activity_rows = activity_summary(date, group_activity)
    explanations = {r['key']: r for r in attention_view(group_activity or {}).get('groups', [])}
    stock_panels=[activity_html]
    covered=set(policy_excluded)
    groups=definitions()
    baskets=[g for g in groups if g['role']=='mainline']
    for basket in groups:
        key=basket['key']
        members=basket['members']
        identities={(str(r['market']),str(r['symbol']).zfill(5 if r['market']=='HK' else 6)) for r in members}
        subset=stock_view[stock_view['_identity'].isin(identities-policy_excluded)]
        covered.update(subset['_identity'])
        row = activity_rows.get(key)
        coverage = coverage_note(subset,identities,'按趋势分降序，同股可跨篮子展示。未覆盖成分不补造行情。')
        evidence = ('<p class="evidence-text">'+escape(activity_detail(row))+'</p>'+
                    activity_risks(row, group_activity)+activity_explanation(explanations[key])) if row else '<p>本组趋势依据缺失，未作今日判定。</p>'
        stock_panels.append('<details class="panel group-evidence" id="stock-group-'+escape(key, quote=True)+'">'
            '<summary>'+escape(basket['label'])+' · '+escape(explanations[key]['event'] if row else 'UNAVAILABLE')+'</summary>'
            '<div class="evidence-body">'+evidence+'<p class="section-note">'+escape(coverage)+'</p>'+
            table(subset,stock_columns)+'</div></details>')
    remainder=stock_view[~stock_view['_identity'].isin(covered)]
    if not remainder.empty:
        stock_panels.append(panel('其他固定池观察',table(remainder,stock_columns),'保留未纳入上述分类的固定池个股。'))
    def section(ident,title,source,body):
        return '<section id="'+ident+'"><h2>'+escape(title)+'</h2><p class="section-note source-note">'+escape(source)+'</p><div class="module-panels">'+body+'</div></section>'
    etf_source='数据来源：东方财富 / 新浪（实际序列来源见数据质量）；使用现有 ETF 行情口径。'
    if 'price_provider' in etfs:
        providers=sorted(set(etfs.price_provider.dropna().astype(str)))
        etf_source+=' 本批记录：'+(' / '.join(providers) if providers else '来源待核验')+'。'
    sections=[
        section('market-direction','01 大类资产','数据来源：东方财富 / 新浪 ETF 行情；AKsignal 按资产方向聚合。',panel('资产方向',table(assets,direction_cols),'固定资产顺序；RPS 为方向内 ETF 横截面百分位的中位数；展示相对强度与趋势广度')),
        section('industry-rotation','02 行业轮动','数据来源：通达信官方 .day；TDX Research Industry · 128 个二级行业；AKsignal 独立计算收益率与 RPS。',panel(industry_title,table(industry_view,industry_cols),industry_rule)),
        section('etf-observation','03 ETF',etf_source,
                panel('趋势活跃 ETF',table(etf_view,['fund_code','fund_name','rps15','rps1','daily_return','price','ma20','ma60']),'RPS15 降序 → 流动性降序 → ETF 代码；当日指报告交易日，RPS1 为单日收益横截面排名')+
                panel('ETF 位置观察',table(positions(etf_positions),['fund_code','fund_name','bottom_state','price_pos_60','price_pos_120','price_pos_360','bottom_change','position_reliability']),'位置变化优先；覆盖与窗口可靠性见数据质量')),
        section('stock-observation','04 个股观察',f'数据来源：长桥 OpenAPI · Longbridge ForwardAdjust；趋势与位置使用同一行情序列。按 {len(baskets)} 个研究篮子及高现金流资产分组，仅作观察分类。',''.join(stock_panels)),
    ]
    body=f'<main><header><div class="kicker">AKsignal v{escape(PRODUCT_VERSION)} · DAILY OBSERVATION REPORT · {escape(date)}</div><h1>市场观察日报</h1><p class="principle">{escape(PRINCIPLE)}</p></header><nav aria-label="日报区域"><a href="#market-direction">大类资产</a><a href="#industry-rotation">行业轮动</a><a href="#etf-observation">ETF</a><a href="#stock-observation">个股观察</a><a href="#data-quality">数据质量</a></nav>'+''.join(sections)
    body+='<section id="data-quality" class="quality"><h2>数据质量</h2><div class="panel"><ul>'
    for line in quality:
        label,separator,detail=line.partition('：')
        if not separator or len(label)>14:label,detail='数据说明',line
        attention=any(word in line for word in ('错配','缺失','未统一','未更新'))
        body+='<li'+(' class="attention"' if attention else '')+'><span class="quality-label">'+escape(label)+'</span><span class="quality-detail">'+escape(detail)+'</span></li>' 
    body+='</ul><details><summary>观察口径</summary><p>ETF 活跃：价格高于 MA20 / MA60，且两条均线斜率为正；个股活跃：趋势分 ≥70。两种标尺不同。ETF 与行业 RPS 的横截面也不同，不直接比较。</p><p>位置数值表示窗口内收盘价范围的位置；低位不保证未来不再下跌。进入／离开只在相邻观测日位置均有效时记录；缺口保持未知。</p></details></div></section></main>'
    return '<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AKsignal 观察日报</title>'+FAVICON_LINK+'<style>'+CSS+'</style></head><body data-report-layout="'+REPORT_LAYOUT_VERSION+'">'+body+SCRIPT+'</body></html>'


def quality_lines(date,etfs,industries,stocks):
    from src.common.paths import raw_dir,stock_metrics_dir,data_dir
    from src.final_validation.cli import data_quality_warnings
    from src.market_facts.policy import source_policy
    policy=source_policy()
    lines=[f'统一交易日：{date}。ETF 趋势样本 {len(etfs)} 只；行业 {len(industries)} 个；固定池股票 {len(stocks.drop_duplicates(["market","symbol"]))} 只。']
    if 'history_limitation' in stocks:
        for row in stocks[stocks.history_limitation.fillna('').ne('')].drop_duplicates(['market','symbol']).itertuples():
            lines.append(f'个股历史限制：{row.name} {row.symbol}，长桥完整窗口含异常 OHLC，改用同源近一年有效序列；长窗口不足保持未知。')
    if policy:
        lines.append('模块数据口径：个股=长桥 OpenAPI / ForwardAdjust；ETF=现有东方财富/新浪序列；行业=通达信 TDX Research Industry（价格源未就绪则不可用）；各模块独立，不跨模块回退或统一分类标尺。')
    if 'source' in etfs:
        actual=etfs.source.fillna('unknown_legacy').astype(str).value_counts().to_dict()
        lines.append(f'ETF实际计算序列来源：{actual}；unknown_legacy表示旧缓存未保存来源证据，不推断成东财或新浪。')
    if 'classification_status' in etfs:
        unclassified=int(etfs.classification_status.eq('unclassified').sum())
        tracking_missing=int(etfs.get('tracking_index_status',pd.Series('',index=etfs.index)).eq('missing').sum())
        lines.append(f'ETF 身份元数据：{unclassified}/{len(etfs)} 未分类；{tracking_missing}/{len(etfs)} 缺跟踪指数映射。分类仅作产品名称观察，不据此推断底层指数。')
    if 'data_status' in stocks:
        excluded=int(stocks.drop_duplicates(['market','symbol']).data_status.eq('excluded').sum())
        if excluded: lines.append(f'固定池个股：{excluded}只按配置暂时排除，不参与趋势和底部位置判断。')
    if 'market_session_status' in stocks:
        closed=stocks[stocks.market_session_status.eq('closed')].drop_duplicates(['market','symbol'])
        for (market, source_date), group in closed.groupby(['market','source_trade_date']):
            lines.append(f'市场休市：{market}，报告目标日 {date}，最近实际行情日 {pd.Timestamp(source_date).date()}，{len(group)}只；趋势与位置截至该实际交易日，相对强度基准同日；未补造休市日行情。日历依据为有界独立规则模型。')
    if 'price_basis' in stocks:
        basis_counts=stocks.price_basis.fillna('unknown').astype(str).value_counts().to_dict()
        route_counts=stocks.get('source_route',pd.Series('unknown',index=stocks.index)).fillna('unknown').astype(str).value_counts().to_dict()
        lines.append(f'股票价格口径：basis={basis_counts}；实际来源={route_counts}；price_basis_verified=false（供应商复权算法尚未独立认证）。')
    audit=stock_metrics_dir()/f'stock_update_audit_{date}.json'
    if audit.exists():
        obj=json.loads(audit.read_text());sources=obj.get('sources') or {}
        if obj.get('benchmark_source'):lines.append(f"个股相对强度基准来源：{obj['benchmark_source']}，行情日={obj.get('benchmark_trade_date')}。")
        lines.append('缓存与抓取：'+('本批次只读缓存，未发起个股网络请求。' if not sources else '本批次个股获取审计已落盘；缓存与在线来源分别记录。'))
    reconciled=data_dir()/'audits'/'stock_price_basis'/date/'reconciled.json'
    if reconciled.exists():
        rows=json.loads(reconciled.read_text()).get('findings',[])
        counts={}
        for row in rows:counts[row['close_class']]=counts.get(row['close_class'],0)+1
        from hashlib import sha256
        ledger=json.loads((reconciled.parent/'audit.json').read_text())
        intact=all(Path(row['cache_path']).exists() and sha256(Path(row['cache_path']).read_bytes()).hexdigest()==row['cache_sha256'] for row in ledger['findings'])
        if intact:
            lines.append(f'复权审计：{len(rows)} 只已核对；与当前前复权收盘价相容 {counts.get("MATCH_QFQ_CLOSE",0)} 只、局部差异 {counts.get("LOCALIZED_CLOSE_DIFFERENCES",0)} 只、持续差异 {counts.get("PERSISTENT_CLOSE_DIFFERENCES",0)} 只。全池口径尚未统一。')
        else:
            lines.append('复权审计：缓存在核对后已变化，本日报不复用旧验证结论。')
    else:
        lines.append('复权验证：长桥 ForwardAdjust 为本模块统一指定口径；供应商公司行为算法尚未独立审计，price_basis_verified=false。' if policy.get('stocks',{}).get('provider')=='longbridge' else '复权验证：历史缓存复权一致性尚未完成逐只审计，price_basis_verified=false。')
    for issue in identity_issues(date):
        lines.append(f'身份错配：{issue["market"]} {issue["symbol"]} 配置为 {issue["configured_name"]}，实际为 {issue["observed_name"]}；正确代码 {issue["expected_symbol"]}。已排除观察正文，保留原始配置与缓存供追溯。')
    lines.extend(x['message'] for x in data_quality_warnings(date))
    lines.append('可靠性分开记录：窗口数据有效，不等于供应商复权算法已独立验证。个股长桥链使用独立交易日历检查，不混入旧个股缓存。' if policy.get('stocks',{}).get('provider')=='longbridge' else '可靠性分开记录：窗口数据有效，不等于复权依据已验证；个股市场日期并集也不能检测全池共同缺失的一天。')
    return lines


def identity_issues(date):
    from src.common.paths import data_dir,config_dir
    paths=[config_dir()/'observation_identity_issues.json',
           data_dir()/'audits'/'stock_price_basis'/date/'identity_issues.json']
    issues={}
    for path in paths:
        if path.exists():
            for row in json.loads(path.read_text()).get('issues',[]):
                issues[(row['market'],row['symbol'])]=row
    return list(issues.values())


def mark_identity(frame,date):
    d=frame.copy()
    issues={(r['market'],r['symbol']):r for r in identity_issues(date)}
    d['identity_status']=['IDENTITY_MISMATCH' if (str(r.market),str(r.symbol)) in issues else 'NOT_CHECKED' for r in d.itertuples()]
    return d
