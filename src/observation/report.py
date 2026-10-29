"""Daily observation report: five sections, three reading layers, no new facts.

Layer 1 summary is a deterministic template over the facts below. Layer 2
default tables show only the contract columns (src/observation/contract.py).
Layer 3 keeps every remaining field, rule state and audit receipt in the detail
expansion. Nothing here selects, re-ranks or recalculates market facts.
"""
from html import escape
import json
import re
from pathlib import Path
from urllib.parse import quote
import pandas as pd
from src.observation import contract as display_contract
from src.observation.group_attention import attention_view

PRODUCT_VERSION = (Path(__file__).resolve().parents[2] / 'VERSION').read_text().strip()
REPORT_LAYOUT_VERSION = 'daily_observation_modules_v13'
PREVIEW_LIMIT = 20

# Embedded geometry keeps the AK mark portable and independent of installed fonts.
FAVICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               '<title>AK</title><rect width="64" height="64" rx="12" fill="#C0392B"/>'
               '<path d="M8 46 20 16 32 46 M12 36H28 M39 16V46 M55 16 39 31 55 46" '
               'fill="none" stroke="#FFFFFF" stroke-width="4.5" stroke-linecap="round" '
               'stroke-linejoin="round"/></svg>')
FAVICON_LINK = '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,' + quote(FAVICON_SVG, safe='') + '">'

PRINCIPLE = 'AKsignal 负责发现和量化市场事实，你负责理解这些事实并作出投资决策。'
TAGLINE = '发现和量化市场事实，不生成买卖决策。'
LABELS = {'rps1':'当日 RPS','rps5':'RPS5','rps15':'RPS15','RPS1':'当日 RPS','daily_return':'当日涨幅','direction':'ETF 分类','median_rps15':'RPS15 中位','median_rps5':'RPS5 中位','median_rps1':'当日 RPS 中位','change_5d':'排名变化中位数','active_ratio':'趋势活跃比例',
          'direction_state':'方向状态','industry_name':'行业','RPS15':'RPS15','RPS5':'RPS5','trend_stage':'阶段',
          'fund_code':'代码','fund_name':'ETF','rps15':'RPS15','price':'价格','ma20':'MA20','ma60':'MA60',
          'market_session_status':'市场状态','source_trade_date':'实际行情日',
          'market':'市场','symbol':'代码','name':'股票','trend_score':'趋势分','return_5d':'5日收益','return_20d':'20日收益',
          'bottom_state':'底部状态','price_pos_60':'60日位置','price_pos_120':'120日位置','price_pos_360':'360日位置',
          'bottom_change':'底部变化','reliability':'窗口可靠性','position_reliability':'窗口可靠性',
          'close':'收盘点位','provider':'来源','status':'数据状态','return_1d':'当日涨跌','return_20d':'20日涨跌',
          'return_60d':'60日涨跌','trend_state':'均线状态','contract':'合约','basis_rate':'基差率',
          'open_interest':'持仓量','settle':'结算点位','volume':'成交量','basis_points':'基差（点）',
          'etf_count':'产品数','parent_industry':'上级行业','valid_count_15d':'15日有效样本','valid_count_5d':'5日有效样本',
          'industry_code':'行业代码','history_bars':'历史条数','data_status':'数据状态','source':'来源',
          'liquidity':'流动性','bucket_label':'产品分类','tracking_index_status':'跟踪指数映射',
          'classification_status':'分类状态','investment_market':'投资市场','investment_direction':'投资方向',
          'classification_basis':'分类依据','window_reason_120':'120日窗口','window_reason_360':'360日窗口',
          'market_classification_status':'市场核验状态','direction_classification_status':'方向核验状态',
          'classification_version':'分类版本',
          'bottom_rule_note':'标签口径','rep_name':'代表产品','open':'开盘','high':'最高','low':'最低',
          'open_interest_change':'持仓变化','previous_close':'前收盘','pre_settle':'前结算',
          'close_vs_pre_settle':'结算与收盘差','previous_source_trade_date':'前一合约日','security_id':'合约标识',
          'quote_unit':'报价单位','volume_unit':'数量单位','contract_multiplier_cny':'合约乘数（元/点）','source_url':'来源文件'}
STATES = {'closed':'休市','open':'开市','EXCLUDED':'暂不观察','DEEP_BOTTOM':'长期深底','RECOVERING_FROM_BOTTOM':'低位恢复','LONG_TERM_LOW':'长期低位',
          'RECENT_BOTTOM':'近期低位','NORMAL':'非低位','UNAVAILABLE':'未知','UNRELIABLE':'未知',
          'ENTERED_BOTTOM':'进入底部','LEFT_BOTTOM':'离开底部','UNCHANGED':'未变化','UNKNOWN':'变化未知',
          'OK':'有效','DATA_GAP':'报价缺口','INSUFFICIENT_HISTORY':'历史不足','FLAT_WINDOW':'无波动',
          'CORPORATE_ACTION':'异常价格跳变','FLAT_PRICE_ASSET':'不适用','MISSING_TARGET':'缺目标日报价',
          'RISING':'上行','FALLING':'下行','MIXED':'交错'}
INDEX_TRENDS = {'RISING': '上行', 'FALLING': '下行', 'MIXED': '交错', 'UNKNOWN': '待数据完整'}
NON_NUMERIC = {'name', 'fund_name', 'contract', 'industry_name', 'trend_state', 'trend_stage',
               'bottom_state', 'bottom_change', 'status', 'provider', 'direction', 'direction_state',
               'market_session_status', 'source_trade_date', 'reliability', 'label', 'phase',
               'observation_change', 'window_reason_120', 'window_reason_360', 'bottom_rule_note'}

CSS = r'''
:root{--bg:#F8F6F0;--panel:#fff;--ink:#263746;--muted:#556270;--line:#dce5eb;--blue:#6BB6E8;--accent:#FF7A2F;--cool:#eaf5fc;--warm:#fff1e7}
*{box-sizing:border-box}[hidden]{display:none!important}html{scroll-behavior:smooth;scroll-padding-top:78px}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 system-ui,-apple-system,sans-serif}
main{max-width:1120px;margin:auto;padding:24px 20px}header{margin-bottom:14px;padding:18px 0 0;border-top:4px solid var(--blue)}h1{font-size:27px;letter-spacing:-.5px;margin:8px 0}h2{font-size:21px;margin:0 0 6px}h3{font-size:15px;margin:0;color:var(--ink)}p{margin:8px 0}.kicker{font-size:12px;letter-spacing:1.6px;color:var(--muted)}.principle{color:var(--muted);font-size:13px;max-width:850px}
nav{position:sticky;top:0;z-index:10;display:flex;gap:6px;flex-wrap:wrap;padding:6px;background:rgba(248,246,240,.97);border:1px solid var(--line);margin:12px 0 18px;backdrop-filter:blur(8px)}nav a{display:inline-flex;gap:6px;align-items:baseline;padding:6px 12px;border-radius:4px;color:#28608a;text-decoration:none}nav a b{font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums}nav a:hover,nav a:focus-visible{background:var(--cool);outline:2px solid var(--blue);outline-offset:1px}a{color:#28608a;text-decoration:none}
section{margin-bottom:22px}.section-head{display:flex;align-items:baseline;gap:10px;margin-bottom:2px}.section-num{font-size:13px;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums;letter-spacing:.5px}.section-note{color:var(--muted);margin:0 0 10px;font-size:12px}.module-panels{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}
.panel{background:var(--panel);border:1px solid var(--line);padding:14px;min-width:0;border-radius:6px}.panel h3{margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}.panel h3:before{content:"";width:4px;height:18px;background:var(--blue);display:block}.source-note{padding:2px 0 8px;border-bottom:1px solid var(--line);margin-bottom:12px}
.summary{padding:16px}.summary h2{font-size:14px;letter-spacing:1.2px;color:var(--muted);font-weight:600;text-transform:none;margin:0 0 10px}.hero{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.hero-card{border:1px solid var(--line);border-radius:6px;padding:10px 12px;background:#fbfdfe;min-width:0}.hero-label{display:block;font-size:12px;color:var(--muted)}.hero-value{display:block;font-size:26px;font-weight:700;line-height:1.25;font-variant-numeric:tabular-nums;letter-spacing:-.5px}.hero-sub{display:block;font-size:11px;color:var(--muted)}
.summary p.brief{margin:12px 0 0;font-size:14px;line-height:1.8}.digest{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:4px}.digest li{font-size:13px;color:var(--ink)}.digest li b{color:var(--muted);font-weight:600;margin-right:6px}
.chips{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-top:12px}.chip{border:1px solid var(--line);border-radius:6px;padding:8px 10px;color:inherit;background:#fff}.chip b{display:block;font-size:12px;color:var(--muted);font-weight:600}.chip span{display:block;font-size:13px;line-height:1.5;overflow-wrap:anywhere}.chip:hover,.chip:focus-visible{background:var(--cool);outline:2px solid var(--blue);outline-offset:1px}
.table-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:8px}.table-toolbar input{font:inherit;font-size:13px;padding:6px 9px;border:1px solid var(--line);border-radius:4px;max-width:100%;width:180px;background:#fbfdfe}.table-toolbar input:focus{outline:2px solid var(--blue);outline-offset:1px}.table-note{font-size:12px;color:var(--muted)}
.table-scroll{overflow-x:auto;overflow-y:visible;max-height:none}table{width:100%;border-collapse:separate;border-spacing:0;white-space:normal;font-size:12px;line-height:1.4}th{text-align:left;color:var(--muted);font-size:12px;font-weight:600;background:#f1f6f9;white-space:normal}th,td{padding:6px 7px;border-bottom:1px solid var(--line);vertical-align:middle}tr:hover td{background:#f7fbfe}.numeric{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.asset-name{white-space:normal;min-width:100px;max-width:180px;line-height:1.4;font-weight:600}.asset-code{display:block;font:11px/1.6 ui-monospace,monospace;letter-spacing:.3px;color:var(--muted);font-weight:400;margin-top:1px}
.badge{display:inline-flex;align-items:center;gap:5px;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;background:#edf1f4;color:var(--muted);white-space:nowrap}.badge:before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor}.badge.cool{background:var(--cool);color:#24608a}.badge.warm{background:var(--warm);color:#a84b14}.badge.quiet{background:#edf1f4;color:#687581}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600;background:#edf1f4;color:#687581;white-space:nowrap}.tag.group-inferred{background:#fff4e8;color:#9a4f16}
.score{display:flex;justify-content:flex-end;align-items:center;gap:5px;font-variant-numeric:tabular-nums}.score-track{width:28px;height:3px;background:#e5edf2;border-radius:3px;overflow:hidden}.score-fill{display:block;height:100%;background:var(--blue)}.score.low .score-fill{background:var(--accent)}.positive{color:#28608a}.negative{color:#a84b14}.missing{color:#8996a0}.empty{padding:28px;text-align:center;color:var(--muted)}
details{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}summary{cursor:pointer;color:#28608a;font-size:13px;padding:4px 0}summary:focus-visible{outline:2px solid var(--blue)}details .table-scroll{margin-top:8px}
.quality{border-top:2px solid var(--line);padding-top:16px}.quality ul{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:1fr 1fr;gap:12px}.quality li{background:#f7f9fa;padding:10px 12px;border-left:3px solid var(--line);font-size:13px}.quality li.attention{border-left-color:var(--accent);background:#fff8f2}.quality-label{display:block;font-weight:600;margin-bottom:5px}.quality-detail{color:var(--muted)}
.coverage{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:0 0 12px}.coverage div{border:1px solid var(--line);border-radius:6px;padding:8px 10px;background:#fbfdfe}.coverage b{display:block;font-size:22px;font-variant-numeric:tabular-nums;line-height:1.3}.coverage span{font-size:12px;color:var(--muted)}
.limits{list-style:none;padding:0;margin:0;display:grid;gap:8px}.limits li{background:#fff8f2;border-left:3px solid var(--accent);padding:8px 12px;font-size:13px}.quality details ul{padding-left:18px;margin:6px 0}.quality details li{margin:4px 0}
.low-note{border-left:3px solid var(--blue);padding:4px 10px;margin:6px 0 10px;background:var(--cool);font-size:12px}

.industry-panel{min-width:0}.index-observation{min-width:640px}.derivative-observation{min-width:520px}
.attention-headline{font-size:18px;line-height:1.8;margin:8px 0 12px;max-width:960px}.attention-summary{border-top:3px solid var(--blue)}.attention-summary h3:before{background:var(--accent)}
.attention-brief{margin:10px 0 16px;padding:2px 12px;border-left:3px solid var(--line)}.attention-brief p{margin:5px 0;font-size:14px}.opportunity-label{display:block;font-size:12px;font-weight:600;margin-bottom:4px;color:var(--muted)}
.group-overview{font-size:13px}.group-overview th:nth-child(1){width:22%}.group-overview th:nth-child(2){width:16%}.group-overview th:nth-child(3){width:18%}.group-overview th:nth-child(4){width:30%}.group-overview th:nth-child(5){width:14%}.group-overview td{padding:11px 8px;overflow-wrap:anywhere}.group-overview .rule-state{display:block;color:var(--muted);font-size:11px;font-weight:400;margin-top:3px}.group-overview .focus-row td:first-child{border-left:3px solid var(--accent)}
.idle-line{font-size:12px;color:var(--muted);margin-top:6px}.idle-line summary{font-size:12px}
.group-evidence{margin:0;padding:14px}.group-evidence>summary{font-size:15px;font-weight:600;color:var(--ink);list-style-position:outside;margin-left:16px}.group-evidence .evidence-body{padding-top:12px}.evidence-text{line-height:1.9;max-width:960px}.group-risk{border-left:3px solid var(--accent);padding-left:10px;color:#864116}.group-risk.note{margin:6px 0 0;font-size:12px}.trace-list{overflow-wrap:anywhere;font-size:12px}.trace-list code{white-space:normal}.group-evidence:target{border-color:var(--blue)}
.evidence-list{list-style:none;padding:0;margin:6px 0;display:grid;gap:6px}.evidence-list li{display:grid;grid-template-columns:110px 1fr;gap:10px;font-size:13px;border-bottom:1px dashed var(--line);padding-bottom:5px}.evidence-list b{color:var(--muted);font-weight:600}.evidence-list code{font:12px/1.6 ui-monospace,monospace;overflow-wrap:anywhere}
.contract-note{font-size:12px;color:var(--muted);margin-top:10px}
@media(max-width:900px){.hero,.coverage,.chips{grid-template-columns:repeat(2,minmax(0,1fr))}.quality ul{grid-template-columns:1fr}.chips .chip:last-child{grid-column:span 2}}
@media(max-width:600px){.attention-headline{font-size:16px}.group-overview,.group-overview tbody,.group-overview tr,.group-overview td{display:block;width:100%}.group-overview thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.group-overview tr{padding:10px 0;border-bottom:1px solid var(--line)}.group-overview td{border:0;padding:3px 8px}.group-overview td[data-label]:before{content:attr(data-label) ' · ';font-size:12px;color:var(--muted)}.group-overview .rule-state{display:inline;margin-left:8px}.group-overview td:first-child{font-weight:600;font-size:15px}.group-overview .focus-row td:first-child{border-left:3px solid var(--accent)}}
@media(max-width:600px){main{padding:18px 12px}header{padding-top:18px}h1{font-size:25px}h2{font-size:21px}.panel{padding:14px}nav{gap:2px;padding:6px}nav a{padding:7px 9px;font-size:12px}th,td{padding:6px 6px}.score-track{width:24px}.asset-name{min-width:100px}.hero-value{font-size:22px}.evidence-list li{grid-template-columns:1fr}}
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
function openTarget(event) {
  const hash = location.hash.slice(1);
  if (!hash) return;
  const target = document.getElementById(decodeURIComponent(hash));
  if (!target) return;
  const detail = target.matches('details') ? target : target.closest('details');
  if (detail) detail.open = true;
  if (!event) target.scrollIntoView();
}
window.addEventListener('hashchange', function() { openTarget(true); });
document.addEventListener('click', function(event) {
  const link = event.target.closest('a[href^="#"]');
  if (!link) return;
  const target = document.getElementById(link.getAttribute('href').slice(1));
  if (target) {
    const detail = target.matches('details') ? target : target.closest('details');
    if (detail) detail.open = true;
  }
});
openTarget(false);
</script>
'''


# --- Section 04 group evidence (unchanged facts; wording only) -------------
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


CLUE_WORDING = {'已有机会': '已有观察线索', '新增机会': '新增观察线索'}
ACTIVE_STATES = ('WATCH', 'EMERGING', 'CONFIRMED')


def active_event(r):
    """The event starts from the recorded rule status; NO_SETUP is not an active episode."""
    event = r['event']
    if str(event).startswith('NO_SETUP'):
        return '本档无启动候选，未形成 WATCH 观察期'
    return event


def _clue_wording(text):
    """Rename display wording only; the underlying opportunity values stay unchanged."""
    text = str(text)
    for old, new in CLUE_WORDING.items():
        text = text.replace(old, new)
    return text


def _mainline_rows(date, activity):
    """Return (summary html, annotations, view, validity) for section 04."""
    from src.market_facts.group_activity import RULE_VERSION
    if (not activity or activity.get('trade_date') != pd.Timestamp(date).strftime('%Y%m%d')
            or activity.get('rule_version') != RULE_VERSION):
        return None
    return activity, {r['key']: r for r in activity.get('groups', [])}, attention_view(activity)


def mainline_summary(date, activity):
    """One table: 主线 | 趋势阶段 | 启动状态 | 观察变化 | 线索。"""
    resolved = _mainline_rows(date, activity)
    if resolved is None:
        return ('<article class="panel"><h3>研究主线 · 分组趋势启动观察</h3>'
                '<p class="section-note">无法判定：数据缺失或日期/规则版本不一致；未作今日判定。</p></article>', {})
    activity, annotations, view = resolved
    rows = activity.get('groups', [])
    overview = []
    for r in view['groups']:
        tone = 'warm' if r['priority'] == '优先研究' else 'cool' if r['priority'] == '重点观察' else 'quiet'
        key = escape(r['key'], quote=True)
        overview.append('<tr'+(' class="focus-row"' if r['key'] in view['highlighted_keys'] else '')+'>'
            '<td><a href="#stock-group-'+key+'" title="'+escape(r['full_label'], quote=True)+'">'+escape(r['label'])+'</a></td>'
            '<td data-label="趋势阶段">'+escape(r['phase'])+'</td>'
            '<td data-label="启动状态">'+('<span class="badge quiet">'+escape(r['status'])+'</span>')+'<span class="rule-state">'+escape(active_event(r))+ '</span></td>'
            '<td data-label="观察变化">'+escape(_clue_wording(r['today']))+'</td>'
            '<td data-label="线索">'+('<span class="opportunity-label">'+escape(_clue_wording(r['opportunity']))+'</span>' if r['opportunity'] != r['priority_label'] else '')+
            '<span class="badge '+tone+'">'+escape(r['priority_label'])+'</span></td></tr>')
    unavailable = sum(r['priority'] == '待数据完整' for r in view['groups'])
    note = f'另有 {unavailable} 组数据或展示依据不齐，未作趋势阶段判定；不可解释为没有机会。' if unavailable else ''
    brief = ''.join('<div class="attention-brief"><span class="opportunity-label">'+escape(_clue_wording(block['kind']))+'</span>'+
                    ''.join('<p><strong>'+escape(r['label'])+'</strong>：'+escape(r['reason'])+'。</p>' for r in block['items'])+
                    '</div>' for block in view['summary_sections'])
    html = ('<article class="panel attention-summary" id="mainline-summary"><h3>研究主线概览</h3>'
            '<p class="attention-headline"><strong>'+escape(view['headline'])+'</strong></p>'
            + brief + '<p class="section-note">“线索”指研究观察线索；新增与已有分别标识，同档并列，仅安排阅读顺序。点击主线查看完整量化证据与成员表。</p>'
            + ('<p class="group-risk note">'+escape(note)+'</p>' if note else '')+
            '<div class="table-scroll"><table class="group-overview" aria-label="研究主线概览">'
            '<thead><tr><th scope="col">研究主线</th><th scope="col">趋势阶段</th><th scope="col">启动状态</th><th scope="col">观察变化 · 较上一有效观察日</th><th scope="col">线索</th></tr></thead>'
            '<tbody>'+''.join(overview)+'</tbody></table></div>'
            '<p class="section-note">趋势阶段描述当前结构；观察变化描述相对上一有效观察日的变化，二者是不同时间尺度。上涨范围指对应窗口参与度；pp 为百分点。NO_SETUP 仅表示无启动候选，不表示弱势趋势已确认；UNAVAILABLE 表示无法判定。</p>'
            + activity_common_notes(activity) + '</article>')
    return html, annotations


# Kept for imports that reference the old name.
activity_summary = mainline_summary


def activity_common_notes(activity):
    rows = activity.get('groups', [])
    shared = set(rows[0].get('limitations', [])) if rows else set()
    for row in rows[1:]:
        shared.intersection_update(row.get('limitations', []))
    limits = sorted(shared) or ['候选参数未验证；当前名单与复权快照重建历史，非历史时点回测。',
        '复权算法未认证；price_basis_verified=false。不含汇率/费用，不代表真实组合收益。']
    text = ''.join('<li>'+escape(line)+'</li>' for line in limits)
    return ('<details class="common-limits"><summary>共性限制与解释口径（参数未验证）</summary><ul>'+text+'</ul>'
            '<p>趋势阶段是事实解释，规则状态保留 V2 原值，阅读优先级只决定阅读顺序。'
            '优先研究：活跃状态下，价格转强结构与初步支持同时成立，或价格止跌、RS改善和参与度扩散同时成立。'
            '重点观察：活跃状态下，价格高于MA5、5日收益为正且RS改善和参与度扩散成立。'
            '其后依次观察单一路径的修复持续性、价格跟踪、尚缺止跌证据的异动及无启动候选。'
            '不新增统计阈值或综合评分，同档不按涨幅或首次触发排序。</p>'
            '<p>新增观察线索为本次首次 WATCH；其余活跃状态作为已有观察线索跟踪，新增不自动优先。'
            '速览先展示首次触发或状态转换，再展示最多两项非零指标变化：先回落，再按10日参与度、5日参与度、相对强弱展示改善。'
            '按两位小数显示为零的变化省略，完整数值留在详情；不代表统计显著性判断。观察线索与阅读顺序不代表买入概率或预期收益。</p>'
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


def mainline_evidence(row, view, activity):
    """Structured quantitative evidence for one mainline (replaces one long sentence)."""
    m = row.get('metrics') or {}
    def pct(key, scale=1, suffix='%'):
        value = m.get(key)
        return '—' if not isinstance(value, (int, float)) else f'{value*scale:+.2f}{suffix}'
    def plain(key, fmt='{:+.2f}'):
        value = m.get(key)
        return '—' if not isinstance(value, (int, float)) else fmt.format(value)
    channels = {'COLLECTIVE':'集体异动', 'RS_IMPROVEMENT':'RS改善',
                'PARTICIPATION_EXPANSION':'参与度扩散', 'RS_AND_PARTICIPATION':'RS与参与度共同改善'}
    changes = row.get('changes') or {}
    items = [
        ('当前结构', f"QR结构指数 {plain('nav')} / MA5 {plain('ma5')} / MA20 {plain('ma20')}；5日 {plain('return_5d', '{:+.1%}')}，20日 {plain('return_20d', '{:+.1%}')}"),
        ('相对强弱', f"RS20Δ {plain('rs_20d_pct')}%（较5日前 {plain('rs_20d_change_5d_pp')}pp）"),
        ('上涨范围', f"5/10/20日参与度 {plain('participation_5d','{:.0%}')} / {plain('participation_10d','{:.0%}')} / {plain('participation_20d','{:.0%}')}"),
        ('较上一观察日', ((f"RS20Δ {changes['rs_20d_pct']:+.2f}pp；") if 'rs_20d_pct' in changes else '') +
                      ((f"5日参与度 {changes['participation_5d']*100:+.2f}pp；") if 'participation_5d' in changes else '') +
                      ((f"10日参与度 {changes['participation_10d']*100:+.2f}pp") if 'participation_10d' in changes else '')
                      if row.get('previous_observation_date') else '无上一有效观察日对照'),
        ('触发与证据', f"事件首次证据：{channels.get(row.get('trigger_channel'), row.get('trigger_channel') or '无')}；当日证据：{channels.get(row.get('evidence_channel'), row.get('evidence_channel') or '无')}"),
        ('规则状态', f"{row.get('status')} · 展示阶段 {escape(view['phase'])} · 阅读档 {escape(view['priority_label'])}"),
        ('启动路径', f"{({'SUDDEN':'突发式','GRADUAL':'渐进式','TRACKING':'已有趋势跟踪'}).get(row.get('path'), '无启动路径')}；组级首次触发：{'是' if row.get('first_trigger') else '否'}"),
    ]
    if row.get('reasons'):
        items.append(('触发原因', escape('；'.join(row['reasons']))))
    if row.get('observation_reason'):
        items.append(('解读', escape(view['observation_reason']) + '（当前结构与上一观察日变化分别陈述）'))
    body = ''.join(f'<li><b>{label}</b><span>{value}</span></li>' for label, value in items)
    return '<ul class="evidence-list">' + body + '</ul>'


def validate_activity_rendering(date, activity, receipt, rendered):
    """Check the receipt and visible section against the accepted facts."""
    expected_states = [{k:r.get(k) for k in ('key','status','first_trigger','path','event_origin','transition','reasons','metrics','leader_clues','evidence_channel','alert_kind','event_active','episode_status','trigger_channel')}
                       for r in activity['groups']]
    if (receipt.get('states') != expected_states or receipt.get('rule_version') != activity['rule_version']
            or receipt.get('input_hash') != activity['input_hash']):
        raise ValueError('日报JSON趋势状态或依据不一致')
    if mainline_summary(date, activity)[0] not in rendered or any(
        'id="stock-group-'+r['key']+'"' not in rendered or escape(activity_detail(r)) not in rendered
        for r in activity['groups']):
        raise ValueError('日报HTML趋势状态、依据或链接不一致')


# --- Cells and tables ------------------------------------------------------
def _cell(value, column, row):
    if value is None or pd.isna(value):
        return '<span class="missing">—</span>'
    text = str(value)
    if column in ('fund_name','name'):
        code = row.get('fund_code') if column=='fund_name' else row.get('symbol')
        market = row.get('market','')
        sub = ' · '.join(str(v) for v in (market,code) if v is not None and pd.notna(v) and str(v))
        return '<div class="asset-name">'+escape(text)+'<span class="asset-code">'+escape(sub)+'</span></div>'
    if column == 'source_trade_date':
        return escape(pd.Timestamp(value).date().isoformat())
    if column == 'provider':
        return escape({'longbridge':'长桥','csindex':'中证官方','cffex':'中金所','tdx':'通达信','em':'东方财富','sina':'新浪'}.get(text, text))
    if column == 'status':
        label = '可用' if text == 'AVAILABLE' else STATES.get(text, text)
        return f'<span class="badge {"cool" if text=="AVAILABLE" else "warm"}">'+escape(label)+'</span>'
    if column == 'trend_state':
        return f'<span class="badge { {"RISING":"cool","FALLING":"warm"}.get(text,"quiet") }">'+escape(INDEX_TRENDS.get(text,text))+'</span>'
    if column == 'tracking_index_status' and text == 'missing':
        return '<span class="tag group-inferred">缺映射</span>'
    if column in ('classification_status', 'market_classification_status', 'direction_classification_status'):
        return '<span class="tag">'+escape({'classified_unverified':'已分类（含推断）','unclassified':'待核实',
                                            'verified':'已核验','inferred':'推断','pending':'待核实'}.get(text,text))+'</span>'
    if column == 'classification_basis':
        return escape({'tracking_index_name':'指数名称推断','fund_name':'基金名称推断','published_config':'已发布分类配置','missing':'缺少依据'}.get(text,text))
    if column in ('market_session_status','bottom_state','bottom_change','reliability','position_reliability','trend_stage','direction_state'):
        label = STATES.get(text,text)
        cool = text in ('OK','DEEP_BOTTOM','LONG_TERM_LOW','已形成趋势','强势','增强','open')
        warm = text in ('ENTERED_BOTTOM','LEFT_BOTTOM','RECOVERING_FROM_BOTTOM','正在启动','DATA_GAP','CORPORATE_ACTION','MISSING_TARGET','closed')
        tone = 'cool' if cool else 'warm' if warm else 'quiet'
        return f'<span class="badge {tone}">'+escape(label)+'</span>'
    if column in ('rps1','RPS1','rps15','RPS15','RPS5','median_rps15','median_rps5','median_rps1','trend_score','price_pos_60','price_pos_120','price_pos_360','active_ratio'):
        number = float(value)
        if column == 'active_ratio':
            return f'{number*100:.1f}%'
        width = max(0,min(100,number))
        low = column.startswith('price_pos_') and number<=20
        return f'<span class="score{ " low" if low else ""}"><span class="score-track" aria-hidden="true"><i class="score-fill" style="width:{width:.2f}%"></i></span><span>{number:.1f}</span></span>'
    if column in ('daily_return','return_5d','return_20d'):
        number = float(value)
        tone = 'positive' if number>0 else 'negative' if number<0 else ''
        return f'<span class="{tone}">{number*100:.1f}%</span>'
    if column in ('return_1d','return_5d_index','return_20d_index','return_60d','basis_rate','return_change'):
        number = float(value)
        tone = 'positive' if number>0 else 'negative' if number<0 else ''
        return f'<span class="{tone}">{number*100:+.2f}%</span>'
    if column in ('open_interest','volume','etf_count','history_bars','valid_count_15d','valid_count_5d'):
        return f'{int(value):,}'
    if isinstance(value,(float,int)):
        return f'{value:.2f}'
    return escape(text)


def table(frame, columns, preview_count=PREVIEW_LIMIT, remainder_label="展开其余观察对象", search=True, names=None):
    columns = [c for c in columns if c in frame]
    if 'fund_name' in columns and 'fund_code' in columns: columns.remove('fund_code')
    if 'name' in columns:
        columns = [c for c in columns if c not in ('symbol','market')]
    def markup(rows):
        head = ''.join('<th scope="col"'+(' class="numeric"' if c not in NON_NUMERIC else '')+'>'+escape(LABELS.get(c,c))+'</th>' for c in columns)
        body = ''
        for _,row in rows.iterrows():
            body += '<tr data-row>'+''.join('<td'+(' class="numeric"' if c not in NON_NUMERIC else '')+'>'+_cell(row.get(c),c,row)+'</td>' for c in columns)+'</tr>'
        if rows.empty: body = f'<tr><td class="empty" colspan="{max(len(columns),1)}">暂无符合当前观察条件的对象</td></tr>'
        return '<div class="table-scroll"><table><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'
    toolbar = ''
    if search:
        toolbar = ('<div class="table-toolbar"><input type="search" data-table-search aria-label="搜索当前观察列表" placeholder="搜索名称、代码或状态">'
                   '<span class="table-note" data-search-result aria-live="polite">名称、代码或状态均可搜索</span></div>')
    body = '<div class="data-table" data-table>'+toolbar+markup(frame.head(preview_count))
    if len(frame)>preview_count:
        body += '<details data-remainder><summary>'+escape(remainder_label)+f' {len(frame)-preview_count} 个</summary>'+markup(frame.iloc[preview_count:])+'</details>'
    return body+'</div>'


CODE_COLUMNS = ('fund_code', 'industry_code', 'symbol', 'code', 'market')
NUMERIC_COLUMNS = ('rps15', 'rps5', 'rps20', 'rps60', 'rps1', 'liquidity', 'trend_score',
                   'return_1d', 'return_5d', 'return_20d', 'return_60d', 'daily_return',
                   'RPS15', 'RPS5', 'RPS1', 'price_pos_60', 'price_pos_120', 'price_pos_360',
                   'median_rps15', 'change_5d', 'active_ratio', '_priority', 'etf_count')


def sort_frame(frame):
    """Stable display frame: codes stay strings, ranking keys are numeric.

    Sorting strings would rank '99.9' above '100.0' and integer codes would lose
    their zero padding, so every ordering decision is normalised here once.
    """
    d = frame.copy()
    for column in CODE_COLUMNS:
        if column in d:
            text = d[column].astype('object').where(d[column].notna())
            d[column] = text.map(lambda v: None if v is None else str(v))
    for column in NUMERIC_COLUMNS:
        if column in d and not pd.api.types.is_numeric_dtype(d[column]):
            d[column] = pd.to_numeric(d[column], errors='coerce')
    return d


UNCLASSIFIED = 'unclassified'


UNCATEGORIZED_DIRECTION = '其他'


def category_rows(assets):
    """Category rows only: the classifier's "其他" catch-all is the unclassified pool.

    It is not a category and is never listed, so the category table reconciles with
    the classified scope. The funds themselves are still computed and shown in the
    trend and position panels; only this summary row is dropped.
    """
    if assets is None or len(assets) == 0 or 'direction' not in assets:
        return assets if assets is not None else pd.DataFrame()
    return assets[assets.direction.astype(str).str.strip().ne(UNCATEGORIZED_DIRECTION)]


def classified_mask(etfs):
    """Row-level classified flag: blank, NaN and "unclassified" markers are one scope.

    The classifier stores "no bucket" as NaN or as a blank string depending on the
    source batch. ``fillna("")`` leaves blanks untouched, and comparing against ""
    therefore used to leak blank-bucket funds into the classified total.
    """
    if etfs is None or len(etfs) == 0:
        return pd.Series(dtype=bool)
    if all(c in etfs for c in ('investment_market', 'investment_direction')):
        # v2 产品分类独立于旧计算桶；任一维度待核实即未完成分类。
        return etfs[['investment_market', 'investment_direction']].apply(
            lambda col: col.notna() & ~col.astype(str).str.strip().isin(('', '待核实', 'nan', 'None', '<NA>'))
        ).all(axis=1)
    status = (etfs['classification_status'] if 'classification_status' in etfs
              else pd.Series('', index=etfs.index))
    if 'asset_bucket' not in etfs:
        # No bucket column: the recorded status is the only evidence available.
        return status.astype(str).eq('classified_unverified')
    bucket = etfs['asset_bucket']
    blank = bucket.isna() | bucket.astype(str).str.strip().isin(('', 'nan', 'None', '<NA>'))
    classified = (~blank) & status.astype(str).ne(UNCLASSIFIED)
    return classified.where(status.ne(''), blank.eq(False))


def classification(etfs):
    """Classified scope as the daily report must state it.

    The classified total is the number of funds with a real bucket -- never the
    whole ETF population and never "total minus NaN", which misses blank buckets.
    """
    if etfs is None or len(etfs) == 0:
        return dict(total=0, classified=0, unclassified=0, buckets={}, blank_marker=0, mismatched_marker=0)
    status = (etfs['classification_status'] if 'classification_status' in etfs
              else pd.Series('', index=etfs.index))
    bucket = (etfs['asset_bucket'] if 'asset_bucket' in etfs else pd.Series('', index=etfs.index))
    blank = bucket.isna() | bucket.astype(str).str.strip().isin(('', 'nan', 'None', '<NA>'))
    classified = classified_mask(etfs)
    if all(c in etfs for c in ('investment_market', 'investment_direction')):
        blank = ~classified
    counts = etfs.loc[classified, 'direction'].value_counts() if 'direction' in etfs else pd.Series(dtype=int)
    return dict(total=len(etfs), classified=int(classified.sum()), unclassified=int((~classified).sum()),
                buckets={str(k): int(v) for k, v in counts.items()},
                blank_marker=int((blank & status.astype(str).ne(UNCLASSIFIED)).sum()),
                mismatched_marker=int((~blank & status.astype(str).eq(UNCLASSIFIED)).sum()))


def ordered(frame, descending=(), identity=()):
    """Display ordering only: numerical evidence first, identity breaks every tie."""
    keys = [key for key in (*descending,*identity) if key in frame]
    if not keys:
        return sort_frame(frame)
    d = sort_frame(frame)
    return d.sort_values(keys, ascending=[key not in descending for key in keys],
                         na_position='last', kind='stable')


def positions(frame,stock=False):
    d = frame.copy()
    if stock: d = d.drop_duplicates(['market','symbol'])
    if 'reliability' not in d:
        def reliability(row):
            reasons = [row.get(f'window_reason_{w}') for w in (120,360)]
            return next((r for r in reasons if r!='OK'),'OK') or 'UNKNOWN'
        d['position_reliability'] = d.apply(reliability,axis=1)
    reliability_column = 'reliability' if 'reliability' in d else 'position_reliability'
    valid = d[reliability_column].eq('OK')
    d['_priority'] = 3
    d.loc[valid,'_priority'] = 2
    d.loc[valid & d.bottom_state.isin(['DEEP_BOTTOM','RECOVERING_FROM_BOTTOM','LONG_TERM_LOW']),'_priority'] = 1
    d.loc[valid & d.bottom_change.isin(['ENTERED_BOTTOM','LEFT_BOTTOM']),'_priority'] = 0
    identity = ('market','symbol') if stock else ('fund_code',)
    # The legacy bottom rule ranks on the 360-day position; tags are unchanged.
    return ordered(d,identity=('_priority','price_pos_360',*identity))


def detail(frame, columns, label='完整指标与口径', audit=None):
    """Layer-3 expansion: every remaining field, verbatim."""
    parts = []
    if columns:
        parts.append(table(frame, columns, preview_count=max(len(frame),1), search=False))
    if audit is not None:
        parts += audit
    if not parts:
        return ''
    return '<details><summary>'+escape(label)+'</summary>'+''.join(parts)+'</details>'


def _kv_table(items, caption=''):
    rows = ''.join(f'<tr><th scope="row">{escape(str(k))}</th><td><code>{escape(str(v))}</code></td></tr>' for k, v in items if v is not None)
    return (f'<p class="section-note">{escape(caption)}</p>' if caption else '')+f'<div class="table-scroll"><table><tbody>{rows}</tbody></table></div>'


# --- 01 Market environment --------------------------------------------------
def market_panel(title, anchor, body, note):
    return ('<article class="panel" id="'+anchor+'"><h3>'+escape(title)+'</h3>'
            '<p class="section-note">'+note+'</p>'+body+'</article>')


def core_index_panel(document, category, title):
    if document is None:
        return market_panel(title, 'core-indices-'+category, '', '本批核心指数事实缺失，未作展示。')
    from src.observation.core_indices import REASONS, PROVIDERS, status_text, number
    rows = [dict(r, alias=r['name'] + (' · 风格参照' if r['category']=='style'
                                       else ' · 同源基准 000300.SH' if r['code'] == '000300' else ''))
            for r in document['rows'] if r['category']==category]
    providers = ' / '.join(sorted({PROVIDERS.get(r['provider'], r['provider']) for r in rows}))
    head = ''.join('<th scope="col"'+(' class="numeric"' if c not in ('alias','trend_state') else '')+'>'+escape(h)+'</th>'
                   for c,h in (('alias','指数'),('return_1d','当日'),('return_5d','5日'),('return_20d','20日'),('return_60d','60日'),('trend_state','均线状态')))
    body = ''
    for r in rows:
        cells = ['<td><div class="asset-name">'+escape(r['alias'])+'<span class="asset-code">'+escape(r['issuer']+' · '+r['code'])+'</span></div></td>']
        for n in (1,5,20,60):
            value, reason = r[f'return_{n}d'], r[f'return_{n}d_status']
            hint = '' if reason=='OK' else REASONS.get(reason, reason)
            tone = 'missing' if value is None else 'positive' if value>0 else 'negative' if value<0 else ''
            cells.append(f'<td class="numeric {tone}" title="{escape(hint, quote=True)}">'+number(value, True)+'</td>')
        cells.append('<td><span class="badge '+{'RISING':'cool','FALLING':'warm'}.get(r['trend_state'],'quiet')+'">'+escape(INDEX_TRENDS.get(r['trend_state'],r['trend_state']))+'</span></td>')
        body += '<tr data-index-key="'+escape(r['key'], quote=True)+'">'+''.join(cells)+'</tr>'
    audit = '<p class="section-note">全部固定行保留；映射/目标日缺失保留 UNAVAILABLE，不用 ETF 替代。</p>'
    detail_cols = ['alias','close','provider','source_trade_date','status']
    detail_body = ('<div class="table-scroll"><table><thead><tr><th>指数</th><th class="numeric">收盘点位</th><th>来源</th><th>实际行情日</th><th>数据状态</th></tr></thead><tbody>'+''.join(
        f'<tr><td>{escape(r["alias"])}</td><td class="numeric">{number(r["close"])}</td><td>{escape(PROVIDERS.get(r["provider"],r["provider"]))}</td>'
        f'<td>{escape(r["source_trade_date"] or "—")}</td><td>{escape(status_text(r))}</td></tr>' for r in rows)+'</tbody></table></div>')
    receipt = document.get('input_receipt') or {}
    manifest = next((value.get('manifest_sha256') for value in receipt.values()
                     if isinstance(value, dict) and value.get('manifest_sha256')), None)
    fields = _kv_table([('rule_version', document.get('rule_version')), ('registry_hash', document.get('registry_hash')),
                        ('snapshot', manifest),
                        ('index_snapshot_id', ' · '.join(f"{r['code']}:{str(r.get('snapshot_id') or '—')[:12]}" for r in rows)),
                        ('calendar', (rows[0].get('calendar_version') if rows else None)),
                        ('price_basis', f"{rows[0].get('price_basis')}；price_basis_verified={str(rows[0].get('price_basis_verified', False)).lower()}" if rows else None),
                        ('identity_verified', ' · '.join(f"{r['code']}={str(r.get('identity_verified')).lower()}" for r in rows))],
                       '指数事实回执（审计字段，普通阅读无需查看）')
    return market_panel(title, 'core-indices-'+category,
        '<div class="table-scroll"><table class="index-observation"><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'
        + ('<details><summary>收盘点位、来源、实际行情日与数据状态</summary>'+detail_body+'</details>' if rows else '')
        + '<details><summary>价格口径与批次日历</summary>'+fields+'</details>',
        '固定清单 · 全部展示；'+escape(providers)+'价格指数日线。趋势口径：价格在 MA20、MA60 同侧，且两条均线近5交易日同向变化；其余为交错。')


def derivative_panel(document, category=None):
    if document is None:
        return ''
    from src.observation.core_indices import number
    from src.observation.derivatives import REASONS
    heads = ('合约','基差率','持仓量（手）')
    body = ''
    for r in document['rows']:
        name = r['contract'] or r['product']
        if r['is_main_open_interest']:
            name += ' · 持仓最多'
        status = '' if r['status']=='AVAILABLE' else ' <span class="badge warm">'+escape(REASONS.get(r['reason'],r['reason']))+'</span>'
        body += ('<tr data-contract="'+escape(r['contract'] or r['product'],quote=True)+'">'
                 '<td><div class="asset-name">'+escape(name)+'</div></td>'
                 '<td class="numeric '+('negative' if (r['basis_rate'] or 0)<0 else 'positive')+'">'+number(r['basis_rate'], True)+status+'</td>'
                 '<td class="numeric">'+('—' if r['open_interest'] is None else f"{r['open_interest']:,}")+'</td></tr>')
    detail_body = ('<div class="table-scroll"><table>'
        '<thead><tr><th>合约</th><th class="numeric">收盘</th><th class="numeric">当日涨跌</th><th class="numeric">结算</th><th class="numeric">成交量（手）</th><th class="numeric">基差（点）</th><th>实际行情日</th><th>状态</th></tr></thead><tbody>'+
        ''.join(f'<tr><td>{escape(r["contract"] or r["product"])}</td><td class="numeric">{number(r["close"])}</td>'
                f'<td class="numeric">{number(r["return_1d"],True)}</td><td class="numeric">{number(r["settle"])}</td>'
                f'<td class="numeric">{"" if r["volume"] is None else format(r["volume"],",")}</td>'
                f'<td class="numeric">{number(r["basis_points"])}</td><td>{escape(r["source_trade_date"] or "—")}</td>'
                f'<td>{escape("可用" if r["status"]=="AVAILABLE" else REASONS.get(r["reason"],r["reason"]))}</td></tr>' for r in document['rows'])+
        '</tbody></table></div>')
    fields = _kv_table([('contract', ' · '.join(r['contract'] or r['product'] for r in document['rows'])),
                        ('source', 'CFFEX 官方日行情；'+str((document.get('input_receipt') or {}).get('cffex', {}).get('manifest_sha256') or '—')),
                        ('spot_reference', (lambda s: f"{s['code']} {s['source_trade_date']} 收盘 {s['close']}（{s['provider']}）" if s else None)(document.get('spot_reference'))),
                        ('contract_multiplier_cny', (document['rows'][0].get('contract_multiplier_cny') if document['rows'] else None)),
                        ('price_basis', (document['rows'][0].get('price_basis') if document['rows'] else None)),
                        ('calendar_basis', (document['rows'][0].get('calendar_basis') if document['rows'] else None)),
                        ('rule_version', document.get('rule_version'))],
                       '合约事实回执（审计字段）')
    return market_panel('IC 衍生品 · 中证500股指期货', 'derivatives-observation',
        '<div class="table-scroll"><table class="derivative-observation"><thead><tr>'+''.join('<th scope="col"'+(' class="numeric"' if i else '')+'>'+h+'</th>' for i,h in enumerate(heads))+'</tr></thead><tbody>'+body+'</tbody></table></div>'
        + '<details><summary>OHLC、结算、成交量与基差点数</summary>'+detail_body+
          '<p class="section-note">当日涨跌按同一合约前收盘计算；基差 = 期货收盘 − 同批中证500现货收盘，基差率 = 基差 / 现货收盘；不作分红调整或年化。'
          '各到期合约的定价差异才是指标信息，因此默认保留全部合约；持仓量用于识别市场关注的主要合约。每点200元是合约乘数，涨跌为报价变化，不是保证金收益。不拼接主力连续行情。</p></details>'
        + '<details><summary>来源、现货基准与日历口径</summary>'+fields+'</details>',
        '来源：中金所官方日行情。IC 为品种代码，下表为当日挂牌的具体到期合约；持仓最多仅标记当日持仓量最大合约。')


# --- 02 Industry ------------------------------------------------------------
def industry_panel(industries, module_status, events=None, category=None):
    state = (module_status or {}).get('industry_rotation') or {}
    unavailable = state.get('status') == 'unavailable'
    if unavailable:
        title = '行业轮动 · 通达信不可用' if state.get('provider') == 'tdx' else '行业轮动 · 本批不可用'
        note = ('UNAVAILABLE：通达信目标日更新或质量门控未通过；本批不展示旧行情，不使用申万替代。' if state.get('provider') == 'tdx'
                else f"本批目标日可核验 {state.get('verified_count', 0)}/{state.get('expected_count', 0)}；不展示旧日期或部分横截面")
        return market_panel(title, 'industry-rotation-table', '', note)
    view = ordered(industries, ('RPS15','RPS5'), ('industry_code','industry_name'))
    default_cols = [c for c in display_contract.INDUSTRY_DEFAULT if c in view]
    rest = [c for c in display_contract.INDUSTRY_DETAIL + display_contract.INDUSTRY_AUDIT if c in view and c not in default_cols]
    return market_panel('行业强势观察', 'industry-rotation-table',
        industry_events_block(events) + table(view, default_cols, preview_count=display_contract.INDUSTRY_PREVIEW,
                                              remainder_label='展开其余行业', search=True)
        + detail(view, rest, '当日 RPS、当日涨幅、样本数与口径'),
        'RPS15 降序 → RPS5 降序 → 行业代码。默认展示前 15 个行业；其余行业可搜索并展开。当日涨幅、当日 RPS 为单日信息，放在详情中。')


KIND_LABEL = {'FIRST_TOP20': '首次进入 RPS15 前20', 'FIRST_FORMED': '首次转入已形成趋势', 'RELAPSE': '趋势退潮'}


def industry_events_block(events):
    if not events:
        return ''
    if not events.get('comparable'):
        reason = escape('；'.join(events.get('problems', [])) or '无前次对照')
        return ('<details class="industry-events"><summary>行业变化观察 · 无法判定</summary>'
                f'<p class="section-note">{reason}。不补造前一日排名或阶段。</p></details>')
    groups = []
    for kind in ('FIRST_TOP20','FIRST_FORMED','RELAPSE'):
        items = [e for e in events['events'] if e['kind']==kind]
        if not items:
            continue
        chips = ''.join(
            f'<li><b>{escape(e["name"])}</b> RPS15 {e["rps15"]:.1f} · RPS5 {e["rps5"]:.1f} · 排名 {int(e["previous_rank15"])}→{int(e["rank15"])}'
            f' · {escape(e["previous_stage"])} → {escape(e["stage"])}</li>' for e in items)
        groups.append(f'<h3 style="margin-top:8px">{escape(KIND_LABEL[kind])} · {len(items)} 个</h3><ul class="evidence-list">{chips}</ul>')
    return ('<details class="industry-events" open><summary>行业变化观察 · 较上一已发布快照 '+escape(events['previous_date'])+'</summary>'
            + ''.join(groups) +
            f'<p class="section-note">变化事件由两份已验收日度快照派生，不是新指标。RPS15 前 {events["top_rank"]} 为排名口径；'
            f'全表 RPS15 排名变化 {events["stage_changes"]} 个行业发生阶段转换，RPS5 发生变化 {events["changed"]}/{len(events["rps5_change"])} 个；'
            '对照快照须行业样本一致且阶段可用现行分类规则复现。</p></details>')


# --- 03 ETF -----------------------------------------------------------------
def etf_panels(assets, etfs, etf_positions, classified_count, unclassified_count):
    etfs = etfs.copy()
    # ETF returns are percentage points; TDX returns are decimal fractions.
    etfs['daily_return'] = etfs.get('return_1d', pd.Series(index=etfs.index,dtype=float))/100
    active = etfs[etfs.trend_active] if 'trend_active' in etfs else etfs.iloc[0:0]
    etf_view = ordered(active, ('rps15','liquidity'), ('fund_code',)) if 'liquidity' in active else ordered(active, ('rps15',), ('fund_code',))
    if len(etf_view):
        # Classified products first, unclassified last: one rule, blank buckets included.
        flag = classified_mask(etf_view)
        etf_view = pd.concat([etf_view[flag], etf_view[~flag]])
    providers = sorted(set(etfs.price_provider.dropna().astype(str))) if 'price_provider' in etfs else []
    trend_cols = [c for c in display_contract.ETF_TREND_DEFAULT if c in etf_view]
    trend_rest = [c for c in display_contract.ETF_TREND_DETAIL if c in etf_view and c not in trend_cols]
    scope = classification(etfs)
    same_topic = ''
    if 'fund_name' in etf_view and not etf_view.empty:
        from collections import Counter
        heads = Counter(re.sub(r'(ETF|etf).*$', '', str(n)) for n in etf_view.fund_name)
        top = [(k, v) for k, v in heads.most_common(3) if k and v >= 3]
        if top:
            same_topic = ('<p class="section-note">同主题产品折叠（名称推断，非跟踪指数映射）：' +
                          '；'.join(f'{escape(k)}×{v}' for k, v in top) +
                          ' 在默认表中连续展示，便于横向比较；底层仍逐产品计算，未合并。</p>')
    pos_view = positions(etf_positions)
    if 'bottom_state' in pos_view:
        pos_view['bottom_rule_note'] = '沿用原 120/360 日低位规则'
    state_counts = pos_view.bottom_state.value_counts()
    low_rows = pos_view[pos_view.bottom_state.isin(['DEEP_BOTTOM','RECOVERING_FROM_BOTTOM','LONG_TERM_LOW'])]
    low_note = (f'<p class="low-note">当前处于长期低位标签 {sum(state_counts.get(s,0) for s in ("DEEP_BOTTOM","RECOVERING_FROM_BOTTOM","LONG_TERM_LOW"))} 只'
                f'（长期深底 {state_counts.get("DEEP_BOTTOM",0)}、低位恢复 {state_counts.get("RECOVERING_FROM_BOTTOM",0)}、长期低位 {state_counts.get("LONG_TERM_LOW",0)}）；'
                f'窗口不可用 {state_counts.get("UNRELIABLE",0)} 只未列入低位判断。底部标签仍按原规则判定，120日位置仅为合并展示的中期位置，不改变底部定义。</p>')
    pos_cols = [c for c in display_contract.ETF_POSITION_DEFAULT if c in pos_view]
    pos_rest = [c for c in display_contract.ETF_POSITION_DETAIL if c in pos_view and c not in pos_cols]
    panels = [
        direction_panel_with_scope(assets, scope),
        market_panel(f'趋势活跃 ETF · {len(active)} 只', 'etf-trends',
            active_scope_note(etf_view) + table(etf_view, trend_cols, preview_count=max(len(etf_view),1), search=True)
            + same_topic + detail(etf_view, trend_rest, '价格、MA20、MA60 与分类明细'),
            'RPS15 降序 → 流动性降序 → ETF 代码；当日指报告交易日。趋势活跃已要求价格高于 MA20/MA60 且两条均线斜率为正，不再重复展示均线数值。'),
        market_panel(f'ETF 位置观察 · 长期低位 {len(low_rows)} 只', 'etf-positions',
            low_note + table(pos_view, pos_cols, preview_count=display_contract.ETF_POSITION_PREVIEW,
                             remainder_label='展开其余 ETF', search=True)
            + detail(pos_view, pos_rest, '60日/360日位置与窗口详情'),
            '位置变化优先；覆盖与窗口可靠性见数据质量。120日位置为中期位置观察窗口，60日/360日位置留在详情；旧底部定义与标签保持不变。'),
    ]
    return ''.join(panels)


def active_scope_note(etf_view):
    """State how much of the active list carries a registered category."""
    if etf_view is None or len(etf_view) == 0:
        return ''
    flag = classified_mask(etf_view)
    unclassified = int((~flag).sum())
    if not unclassified:
        return ''
    return (f'<p class="section-note">本表为逐产品排序，不按主题去重；其中 {unclassified} 只未分类，'
            '保留原位置而不并入“其他”，分类覆盖只指已分类样本。</p>')


def direction_panel_with_scope(assets, scope=None):
    missing = [] if assets is None or assets.empty else [c for c in display_contract.ETF_DIRECTION_DEFAULT if c not in assets]
    if assets is None or assets.empty or missing:
        return ('<article class="panel" id="etf-classification"><h3>ETF 分类概览</h3>'
                '<p class="section-note">本批 ETF 分类横截面缺失' + (escape('（缺少字段：'+'、'.join(missing)+'）') if missing else '') +
                '，未作分类观察；趋势与位置观察独立于分类，不以其他模块替代。</p></article>')
    scope = scope or dict(classified=0, unclassified=0, total=0)
    columns = list(display_contract.ETF_DIRECTION_DEFAULT)
    rows = category_rows(assets)
    if rows.empty:
        return ('<article class="panel" id="etf-classification"><h3>ETF 分类概览</h3>'
                '<p class="section-note">本批没有已分类类别（分类结果全部属于未分类池），未作分类观察；'
                '趋势与位置观察独立于分类。</p></article>')
    # The category table reconciles with the classified scope, not the whole population.
    listed = int(pd.to_numeric(rows['etf_count'], errors='coerce').fillna(0).sum()) if 'etf_count' in rows else 0
    reconcile = ''
    if scope['classified'] and listed != scope['classified']:
        reconcile = (f'<p class="section-note warn">类别产品数合计 {listed:,} 与已分类样本 {scope["classified"]:,} 不一致；'
                     '以本批分类横截面为准，不并入未分类样本。</p>')
    return market_panel('ETF 分类概览 · 已分类样本', 'etf-classification',
        f'<p class="section-note">已分类样本 {scope["classified"]:,} 只（全样本 {scope["total"]:,} 只）；'
        f'未分类样本 {scope["unclassified"]:,} 只另计，不并入任何类别。'
        '按投资市场与投资方向分类；读取已发布分类配置，各维度保留已核验、推断或待核实状态，分类结果不代表全部 ETF 市场。'
        '本表只列已分类类别，名称无法归类的不设“其他”行，统一计入未分类样本；空白或缺失分类同样记为未分类。</p>'
        + reconcile
        + table(rows, columns, preview_count=len(rows), search=False)
        + detail(rows, [c for c in display_contract.ETF_DIRECTION_DETAIL if c in rows], '分类横截面明细'),
        '固定分类顺序；RPS 中位数为该类别产品横截面百分位的中位数，活跃比例按产品数量计算。')


# --- 04 Mainlines -----------------------------------------------------------
def research_panels(date, stocks, stock_positions, group_activity):
    from src.market_facts.group_activity import definitions
    from src.market_facts.policy import source_policy
    policy_excluded = {(str(r['market']),str(r['symbol']).zfill(5 if str(r['market'])=='HK' else 6))
                       for r in source_policy().get('stocks',{}).get('excluded',[])}
    if 'identity_status' in stocks:
        stocks = stocks[stocks.identity_status!='IDENTITY_MISMATCH']
    if 'identity_status' in stock_positions:
        stock_positions = stock_positions[stock_positions.identity_status!='IDENTITY_MISMATCH']
    if 'data_status' in stocks:
        stocks = stocks[stocks.data_status!='excluded']
    if 'reliability' in stock_positions:
        stock_positions = stock_positions[stock_positions.reliability!='EXCLUDED']
    stock_all = ordered(stocks,('trend_score','return_20d'),('market','symbol')).drop_duplicates(['market','symbol'])
    def identity(frame):
        return frame.apply(lambda r: (str(r.market), str(r.symbol).zfill(5 if str(r.market)=='HK' else 6)), axis=1) if not frame.empty else pd.Series(dtype=object)
    stock_all = stock_all.copy()
    stock_all['_identity'] = identity(stock_all)
    position_view = positions(stock_positions, True).copy()
    position_view['_identity'] = identity(position_view)
    fields = [c for c in ('_identity','bottom_state','price_pos_120','price_pos_360','bottom_change','reliability') if c in position_view]
    stock_view = stock_all.merge(position_view[fields],on='_identity',how='left',validate='one_to_one')
    summary_html, activity_rows = mainline_summary(date, group_activity)
    resolved = _mainline_rows(date, group_activity)
    explanations = {r['key']: r for r in (resolved[2]['groups'] if resolved else [])}
    panels = [summary_html]
    covered = set(policy_excluded)
    for basket in definitions():
        key = basket['key']
        identities = {(str(r['market']),str(r['symbol']).zfill(5 if r['market']=='HK' else 6)) for r in basket['members']}
        subset = stock_view[stock_view['_identity'].isin(identities-policy_excluded)]
        covered.update(subset['_identity'])
        row, view = activity_rows.get(key), explanations.get(key)
        excluded_n = len(identities & policy_excluded)
        coverage = (f'已有行情 {len(subset)}/{len(identities-policy_excluded)} 个有效配置成分；'
                    + (f'另有 {excluded_n} 个按观察政策排除；' if excluded_n else '')
                    + '按趋势分降序，同股可跨篮子展示。未覆盖成分不补造行情。')
        evidence = '<p class="evidence-text">'+escape(activity_detail(row))+'</p>' if row else '<p>本组趋势依据缺失，未作今日判定。</p>'
        if row and view:
            evidence = ('<div class="attention-brief" style="border-left-color:var(--accent)"><span class="opportunity-label">昨日/上次解读 → 今日结构 · 变化</span>'
                        f'<p>{escape(view["observation_reason"])}（当前结构：{escape(view["phase"])}；变化：{escape(view["today"])}）</p></div>'
                        + mainline_evidence(row, view, group_activity) + evidence)
        if row:
            evidence += activity_risks(row, group_activity)
        if view:
            evidence += activity_explanation(view)
        panels.append('<details class="panel group-evidence" id="stock-group-'+escape(key, quote=True)+'">'
            '<summary>'+escape(basket['label'])+' · '+escape(row['status'] if row else 'UNAVAILABLE')+' · '+escape(view['phase'] if view else '')+'</summary>'
            '<div class="evidence-body">'+evidence+'<p class="section-note">'+escape(coverage)+'</p>'+
            table(subset,[c for c in display_contract.STOCK_DETAIL if c in subset], search=False)+'</div></details>')
    remainder = stock_view[~stock_view['_identity'].isin(covered)]
    if not remainder.empty:
        panels.append('<details class="panel"><summary>其他固定池观察 · '+str(len(remainder))+' 只（未纳入上述分类）</summary>'
                      + table(remainder,[c for c in display_contract.STOCK_DETAIL if c in remainder], search=False)+'</details>')
    return ''.join(panels)


# --- Layer 1 summary --------------------------------------------------------
def _mainlines_for_summary(date, group_activity):
    resolved = _mainline_rows(date, group_activity)
    if resolved is None:
        return None
    activity, _, view = resolved
    lead = [r for r in view['groups'] if r['priority'] in ('优先研究','重点观察')]
    brief = [item for section in view['summary_sections'] for item in section['items']]
    return dict(groups=activity['groups'], highlighted=view['highlighted_keys'], lead=lead,
                lines=[(s['kind'], r['label'], r['reason']) for s in view['summary_sections'] for r in s['items']],
                headline=view['headline'], count=len(lead))


def summary_panel(date, core_indices, assets, industries, etfs, etf_positions, stocks, group_activity, quality_attention, events, industry_available=True):
    cards = []
    main = None
    if core_indices is not None:
        by_code = {r['code']: r for r in core_indices['rows']}
        main = by_code.get('000300')
        chosen = [r for r in (by_code.get('000300'), by_code.get('932000'), by_code.get('H30269')) if r]
        for r in chosen:
            value, reason = r['return_1d'], r['return_1d_status']
            text = '—' if value is None else f'{value:+.2%}'
            tone = 'missing' if value is None else 'positive' if value > 0 else 'negative' if value < 0 else ''
            sub = '风格参照' if r['category'] == 'style' else ('当日' if reason == 'OK' else (reason or '当日'))
            cards.append(f'<div class="hero-card"><span class="hero-label">{escape(r["name"])}</span>'
                         f'<span class="hero-value {tone}">{text}</span><span class="hero-sub">{escape(sub)}</span></div>')
    if not cards:
        cards = ['<div class="hero-card"><span class="hero-label">核心指数</span><span class="hero-value missing">—</span><span class="hero-sub">本批不可用</span></div>'] * 3
    lines = _mainlines_for_summary(date, group_activity)
    if lines is not None:
        headline_value = f'{len(lines["lead"])} 条'
        headline_sub = '优先观察' if lines['lead'] else '本批无优先组'
    else:
        headline_value, headline_sub = '—', '研究主线不可判定'
    cards.append(f'<div class="hero-card"><span class="hero-label">研究主线</span><span class="hero-value">{escape(headline_value)}</span>'
                 f'<span class="hero-sub">{escape(headline_sub)}</span></div>')
    # Deterministic digest: direction → style, each one clause, no judgement.
    digest = []
    if core_indices is not None:
        by_code = {r['code']: r for r in core_indices['rows']}
        falling = [r for r in core_indices['rows'] if r['category']=='broad' and (r['return_1d'] or 0) < 0 and r['return_1d_status']=='OK']
        rising = [r for r in core_indices['rows'] if r['category']=='broad' and (r['return_1d'] or 0) > 0 and r['return_1d_status']=='OK']
        mixed = falling and rising
        style = by_code.get('H30269')
        style_text = (f'{style["name"]}{style["return_1d"]:+.2%}，{"未跟随下跌" if style["return_1d"] > 0 else "同步下跌"}' if style and style['return_1d'] is not None and style['return_1d_status']=='OK' else None)
        if mixed:
            digest.append(('指数', f'{len(falling)}/{len(falling)+len(rising)} 个宽基下跌、{len(rising)} 个上涨，涨跌方向分化' + (f'；{style_text}' if style_text else '')))
        elif falling:
            digest.append(('指数', f'{len(falling)} 个宽基全部下跌' + (f'；{style_text}' if style_text else '')))
        elif rising:
            digest.append(('指数', f'{len(rising)} 个宽基全部上涨' + (f'；{style_text}' if style_text else '')))
        elif main is not None:
            digest.append(('指数', '宽基当日涨跌数据不齐'))
        trends = [r for r in core_indices['rows'] if r['category']=='broad']
        if trends:
            from collections import Counter
            counts = Counter(INDEX_TRENDS.get(r['trend_state']) for r in trends)
            digest.append(('中期', '宽基均线状态：' + '、'.join(f'{k} {v}' for k, v in counts.items())))
    if industries is not None and not industries.empty and industry_available:
        formed = int(industries.trend_stage.eq('已形成趋势').sum())
        starting = int(industries.trend_stage.eq('正在启动').sum())
        rela = int(industries.trend_stage.eq('正在退潮').sum())
        digest.append(('行业', f'已形成趋势 {formed}、正在启动 {starting}、正在退潮 {rela}（共 {len(industries)} 个行业）'))
    if industries is not None and not industries.empty and industry_available and events and events.get('comparable'):
        # Counts plus the leading names in one row; the event block lists the rest.
        def names(items):
            return '、'.join(str(e['name']) for e in items[:3]) + ('等' if len(items) > 3 else '')
        parts = [f'首次进入 RPS15 前20 {len(events["first_entry"])} 个',
                 f'首次转入已形成趋势 {len(events["first_formed"])} 个',
                 f'趋势退潮 {len(events["relapse"])} 个']
        digest.append(('行业变化', f'较 {events["previous_date"]}：' + '，'.join(parts)))
        if events['first_entry']:
            digest.append(('新进前20', names(events['first_entry'])))
        if events['relapse']:
            digest.append(('退潮', names(events['relapse'])))
    scope = classification(etfs)
    if assets is not None and not assets.empty and 'direction_state' in assets and 'direction' in assets:
        # "其他" is the unclassified pool, not a category: it never drives a reading.
        classified = category_rows(assets)
        strong = classified[classified.direction_state.astype(str).str.contains('强势')]
        if len(strong):
            digest.append(('ETF 分类', '已分类类别中 ' + '、'.join(f'{r.direction} {r.direction_state}' for r in strong.itertuples())))
        else:
            digest.append(('ETF 分类', '已分类类别中无类别呈强势方向状态'))
        if scope['unclassified']:
            digest.append(('ETF 范围', f"分类覆盖：已分类 {scope['classified']} / 全样本 {scope['total']} 只；"
                                      f"{scope['unclassified']} 只未分类不计入任何类别，分类结果不代表全部 ETF 市场"))
    if etf_positions is not None and not etf_positions.empty:
        counts = etf_positions.bottom_state.value_counts()
        long_low = int(sum(counts.get(s, 0) for s in ('DEEP_BOTTOM','LEFT_BOTTOM','RECOVERING_FROM_BOTTOM','LONG_TERM_LOW')))
        moved = int(etf_positions.bottom_change.isin(['ENTERED_BOTTOM','LEFT_BOTTOM']).sum())
        digest.append(('ETF 位置', f'长期低位标签 {long_low} 只，较上一观察日进入/离开底部 {moved} 只'))
    if lines is not None:
        if lines['lines']:
            digest.append(('研究主线', '；'.join(f'{_clue_wording(kind)}：{label}（{reason}）' for kind, label, reason in lines['lines'][:3])))
        else:
            digest.append(('研究主线', '本批无优先观察组'))
    digest_html = ''.join(f'<li><b>{escape(k)}</b>{escape(v)}</li>' for k, v in digest)
    # Prose names the groups once; their reasons live in the 研究主线 row below.
    brief_html = ('<p class="brief">' + escape('研究主线关注 ' + '、'.join(r['label'] for r in lines['lead'][:3]) + '。') + '</p>'
                  if lines is not None and lines['lead'] else '')
    chips = []
    first = quality_attention[0] if quality_attention else '本批无需要关注的异常'
    chips.append('<a class="chip" href="#data-quality"><b>数据质量</b><span>'+escape(first)+'</span></a>')
    for anchor, number, title, subtitle in display_contract.SECTIONS[:-1]:
        chips.append(f'<a class="chip" href="#{anchor}"><b>{number} {escape(title)}</b><span>{escape(subtitle)}</span></a>')
    return ('<section class="summary panel" id="summary" aria-label="今日市场速览">'
            '<h2>今日市场速览</h2><div class="hero">'+''.join(cards)+'</div>'
            + brief_html + '<ul class="digest">'+digest_html+'</ul>'
            + '<div class="chips">'+''.join(chips)+'</div>'
            '<p class="contract-note">展示摘要：由下方 01–05 已验收事实按固定模板生成，不含预测、评分或买卖判断。</p></section>')


# --- 05 Data quality --------------------------------------------------------
def quality_sections(date, quality, modules=None):
    """Split audit lines into an exception-driven summary and a technical detail."""
    attention, normal = [], []
    for line in quality:
        label, separator, detail = line.partition('：')
        if not separator or len(label) > 14:
            label, detail = '数据说明', line
        flagged = any(word in line for word in ('错配','缺失','未统一','未更新','部分不可用','不可用')) or any(
            line.startswith(prefix) for prefix in display_contract.QUALITY_ATTENTION_PREFIXES) or any(
            word in line for word in display_contract.QUALITY_ATTENTION_WORDS)
        (attention if flagged else normal).append((label, detail, line))
    modules = modules or {}
    index_module = modules.get('core_indices') or {}
    etf_trends = modules.get('etf_trends') or {}
    industry = modules.get('industry_rotation') or {}
    stocks = modules.get('stock_trends') or {}
    def count(value):
        return f'{value:,}' if isinstance(value, int) else '—'
    cov = [
        (f"{index_module.get('available_count', '—')}/{index_module.get('expected_count', '—')}", '核心指数可用'),
        (count(industry.get('verified_count')) if industry.get('status') != 'unavailable' else '0', '行业样本'),
        (count(etf_trends.get('rows')), 'ETF 趋势样本'),
        (count(stocks.get('rows')), '固定池股票'),
    ]
    coverage = ''.join(f'<div><b>{escape(v)}</b><span>{escape(l)}</span></div>' for v, l in cov)
    limits = ''.join(f'<li>{escape(line)}</li>' for _, _, line in attention) or '<li>本批未记录需要关注的异常。</li>'
    audit_items = _kv_table([('trade_date', date)] + [(label, detail) for label, detail, _ in normal],
                            '完整来源清单、价格口径与正常状态记录（普通阅读无需查看）')
    return ('<section id="data-quality" class="quality"><div class="section-head"><span class="section-num">05</span><h2>数据质量</h2></div>'
            '<p class="section-note">覆盖率、本次需关注的质量限制；完整来源清单、价格口径与审计摘要保留在技术详情。</p>'
            '<div class="panel"><h3>覆盖率</h3><div class="coverage">'+coverage+'</div>'
            '<h3>本次需关注的质量限制 · '+str(len(attention))+' 项</h3><ul class="limits">'+limits+'</ul>'
            '<p class="section-note">正常来源、正常日期与可用状态不再逐行重复；有缺失、滞后或口径异常时必须在上方明确呈现。'
            '来源、价格依据、分类版本与逐批摘要仍记录在底层事实与审计产物中。</p>'
            '<details class="clauses"><summary>观察口径与计算说明</summary>'
            '<p>ETF 活跃：价格高于 MA20 / MA60，且两条均线斜率为正；个股活跃：趋势分 ≥70。两种标尺不同。ETF 与行业 RPS 的横截面也不同，不直接比较。</p>'
            '<p>位置数值表示窗口内收盘价范围的位置；低位不保证未来不再下跌。进入／离开只在相邻观测日位置均有效时记录；缺口保持未知。</p>'
            '</details>'
            '<details class="audit"><summary>技术详情 · 来源清单、价格口径与审计摘要</summary>'+audit_items+'</details>'
            '</div></section>')


# --- Assembly ---------------------------------------------------------------
def section_block(ident, number, title, subtitle, source, body):
    return ('<section id="'+ident+'"><div class="section-head"><span class="section-num">'+escape(number)+'</span><h2>'+escape(title)+'</h2></div>'
            '<p class="section-note source-note">'+escape(subtitle)+' · '+source+'</p><div class="module-panels">'+body+'</div></section>')


def render_html(date,assets,industries,etfs,stocks,etf_positions,stock_positions,quality,module_status=None,group_activity=None,core_indices=None,derivatives=None,industry_events=None):
    from src.observation.core_indices import source_names as index_sources
    assets = assets.copy() if assets is not None else pd.DataFrame()
    # Single classification rule for the whole report: blank/NaN/"unclassified" is
    # one unclassified scope; classified is what the category table actually lists.
    scope = classification(etfs)
    unclassified, classified_count = scope['unclassified'], scope['classified']
    if not assets.empty:
        assets['alias'] = assets.direction
        from src.etf_signal.rotation import CROSS_ASSET_ORDER
        assets['_direction_order'] = assets.direction.map({v:i for i,v in enumerate(CROSS_ASSET_ORDER)})
        assets = ordered(assets,identity=('_direction_order','direction'))
        assets.attrs['unclassified'] = unclassified
    etf_source = ('ETF 分类 / 趋势：东方财富、新浪（实际序列来源见数据质量）；位置：既有 ETF 行情口径。'
                  f'已分类样本 {classified_count:,} 只（全样本 {scope["total"]:,}），未分类 {unclassified:,} 只另计。')
    industry_state = (module_status or {}).get('industry_rotation') or {}
    industry_available = industry_state.get('status') != 'unavailable' and industries is not None and not industries.empty
    quality_attention = [line for _, _, line in _quality_parts(quality)[1]]
    summary = summary_panel(date, core_indices, assets, industries, etfs, etf_positions, stocks, group_activity,
                            quality_attention, industry_events, industry_available)
    sections = [
        section_block('market-environment','01','市场环境','宽基指数 · 风格 · IC 基差',
                ('核心指数：' + index_sources(core_indices['rows']) + ' 价格指数。' if core_indices is not None else '核心指数事实缺失。'),
                core_index_panel(core_indices,'broad','核心宽基观察') + derivative_panel(derivatives) + core_index_panel(core_indices,'style','风格参照：红利低波')),
        section_block('industry-rotation','02','行业轮动','行业强弱 · 趋势阶段 · 变化',
                '数据来源：通达信官方 .day · TDX Research Industry · 128 个二级行业；AKsignal 独立计算收益率与 RPS。',
                industry_panel(industries, module_status, industry_events)),
        section_block('etf-observation','03','ETF 观察','趋势活跃 · 底部位置 · 分类', etf_source,
                etf_panels(assets, etfs, etf_positions, classified_count, unclassified)),
        section_block('research-mainlines','04','研究主线','研究篮子 · WATCH/EMERGING · 证据',
                f'数据来源：长桥 OpenAPI · Longbridge ForwardAdjust；趋势与位置使用同一行情序列。8 个研究篮子及高现金流参照，仅作观察分类，不合并为股票级单一结论。',
                research_panels(date, stocks, stock_positions, group_activity)),
    ]
    nav = '<nav aria-label="日报区域">' + ''.join(
        f'<a href="#{a}"><b>{n}</b>{escape(t)}</a>' for a, n, t, _ in display_contract.SECTIONS) + '</nav>'
    head = ('<main><header><div class="kicker">AKsignal v' + escape(PRODUCT_VERSION) + ' · DAILY OBSERVATION · ' + escape(date) + '</div>'
            '<h1>市场观察日报</h1><p class="principle">' + escape(TAGLINE) + ' ' + escape(PRINCIPLE) + '</p></header>'
            + nav + summary + ''.join(sections))
    body = head + quality_sections(date, quality, module_status) + '</main>'
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>AKsignal 观察日报</title>' + FAVICON_LINK + '<style>' + CSS + '</style></head><body data-report-layout="'
            + REPORT_LAYOUT_VERSION + '" data-display-contract="' + display_contract.CONTRACT_VERSION + '">' + body + SCRIPT + '</body></html>')


def _quality_parts(quality):
    attention, normal = [], []
    for line in quality:
        label, separator, detail = line.partition('：')
        if not separator or len(label) > 14:
            label, detail = '数据说明', line
        flagged = any(word in line for word in ('错配','缺失','未统一','未更新','部分不可用')) or any(
            line.startswith(prefix) for prefix in display_contract.QUALITY_ATTENTION_PREFIXES) or any(
            word in line for word in display_contract.QUALITY_ATTENTION_WORDS)
        (attention if flagged else normal).append((label, detail, line))
    return None, attention, normal, len(attention)


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
