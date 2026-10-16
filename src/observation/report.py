"""Four module sections plus separate data quality; renderer creates no facts."""
from html import escape
import json
from pathlib import Path
import pandas as pd

PRODUCT_VERSION = (Path(__file__).resolve().parents[2] / 'VERSION').read_text().strip()
REPORT_LAYOUT_VERSION = 'daily_observation_modules_v5'
PREVIEW_LIMIT = 20

PRINCIPLE = 'AKsignal 负责发现和量化市场事实，你负责理解这些事实并作出投资决策。'
LABELS = {'rps1':'当日 RPS','RPS1':'当日 RPS','daily_return':'当日涨幅','direction':'资产方向','median_rps15':'中位 RPS15','change_5d':'排名变化中位数','active_ratio':'趋势活跃比例',
          'direction_state':'方向状态','industry_name':'行业','RPS15':'RPS15','RPS5':'RPS5','trend_stage':'阶段',
          'fund_code':'代码','fund_name':'ETF','rps15':'RPS15','price':'价格','ma20':'MA20','ma60':'MA60',
          'market':'市场','symbol':'代码','name':'股票','trend_score':'趋势分','return_5d':'5日收益','return_20d':'20日收益',
          'bottom_state':'底部状态','price_pos_60':'60日位置','price_pos_120':'120日位置','price_pos_360':'360日位置',
          'bottom_change':'底部变化','reliability':'窗口可靠性','position_reliability':'窗口可靠性'}
STATES = {'EXCLUDED':'暂不观察','DEEP_BOTTOM':'长期深底','RECOVERING_FROM_BOTTOM':'低位恢复','LONG_TERM_LOW':'长期低位',
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
</script>
'''


def _cell(value,column,row):
    if value is None or pd.isna(value):
        return '<span class="missing">—</span>'
    text=str(value)
    if column in ('fund_name','name'):
        code=row.get('fund_code') if column=='fund_name' else row.get('symbol')
        market=row.get('market','')
        sub=' · '.join(str(v) for v in (market,code) if v is not None and pd.notna(v) and str(v))
        return '<div class="asset-name">'+escape(text)+'<span class="asset-code">'+escape(sub)+'</span></div>'
    if column in ('bottom_state','bottom_change','reliability','position_reliability','trend_stage','direction_state'):
        label=STATES.get(text,text)
        cool=text in ('OK','DEEP_BOTTOM','LONG_TERM_LOW','已形成趋势','强势','增强')
        warm=text in ('ENTERED_BOTTOM','LEFT_BOTTOM','RECOVERING_FROM_BOTTOM','正在启动','DATA_GAP','CORPORATE_ACTION','MISSING_TARGET')
        tone='cool' if cool else 'warm' if warm else 'quiet'
        return f'<span class="badge {tone}">'+escape(label)+'</span>'
    if column in ('rps1','RPS1','rps15','RPS15','RPS5','median_rps15','trend_score','price_pos_60','price_pos_120','price_pos_360'):
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
    numeric={'rps1','RPS1','daily_return','rps15','RPS15','RPS5','median_rps15','trend_score','price_pos_60','price_pos_120','price_pos_360','active_ratio','return_5d','return_20d','price','ma20','ma60','change_5d'}
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


def render_html(date,assets,industries,etfs,stocks,etf_positions,stock_positions,quality,module_status=None):
    def panel(title,body,rule=''):
        return '<article class="panel"><h3>'+escape(title)+'</h3>'+('<p class="section-note">'+escape(rule)+'</p>' if rule else '')+body+'</article>'
    direction_cols=['direction','median_rps15','change_5d','active_ratio','direction_state']
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
    from src.research.baskets.config import load_baskets, expand_constituents
    def identity(frame):
        return frame.apply(lambda r: (str(r.market), str(r.symbol).zfill(5 if str(r.market)=='HK' else 6)), axis=1) if not frame.empty else pd.Series(dtype=object)
    stock_all=stock_all.copy()
    stock_all['_identity']=identity(stock_all)
    position_view=positions(stock_positions,True)
    position_view['_identity']=identity(position_view)
    position_fields=[c for c in ('_identity','bottom_state','price_pos_120','price_pos_360','bottom_change','reliability') if c in position_view]
    stock_view=stock_all.merge(position_view[position_fields],on='_identity',how='left',validate='one_to_one')
    stock_columns=['name','trend_score','return_5d','return_20d','bottom_state','price_pos_120','price_pos_360','reliability']
    stock_panels=[]
    covered=set()
    for key,basket in load_baskets().items():
        members=expand_constituents({**basket,'key':key})
        identities={(str(r['market']),str(r['symbol']).zfill(5 if r['market']=='HK' else 6)) for r in members}
        subset=stock_view[stock_view['_identity'].isin(identities)]
        covered.update(subset['_identity'])
        stock_panels.append(panel(basket['label'],table(subset,stock_columns),
            f'已有行情 {len(subset)}/{len(identities)} 个配置成分；按趋势分降序，同股可跨主题展示。未覆盖成分不补造行情。'))
    from src.selection.universe import load_universe_items
    from src.common.paths import selection_universe_path
    cashflow={(i.asset.market,i.asset.symbol.zfill(5 if i.asset.market=='HK' else 6)) for i in load_universe_items(selection_universe_path()) if i.theme=='high_cashflow' and i.asset.category not in ('theme_etf','sub_industry_etf','watch_etf')}
    cashflow_view=stock_view[stock_view['_identity'].isin(cashflow)]
    stock_panels.append(panel('高现金流资产',table(cashflow_view,stock_columns),f'已有行情 {len(cashflow_view)}/{len(cashflow)} 个配置成分；水电核电、运营商、公路、港口及周期电力观察。'))
    covered.update(cashflow_view['_identity'])
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
        section('market-direction','01 大类资产','数据来源：东方财富 / 新浪 ETF 行情；AKsignal 按资产方向聚合。',panel('资产方向',table(assets,direction_cols),'固定资产顺序；展示相对强度与趋势广度')),
        section('industry-rotation','02 行业轮动','数据来源：通达信官方 .day；TDX Research Industry · 128 个二级行业；AKsignal 独立计算收益率与 RPS。',panel(industry_title,table(industry_view,industry_cols),industry_rule)),
        section('etf-observation','03 ETF',etf_source,
                panel('趋势活跃 ETF',table(etf_view,['fund_code','fund_name','rps15','rps1','daily_return','price','ma20','ma60']),'RPS15 降序 → 流动性降序 → ETF 代码；当日指报告交易日，RPS1 为单日收益横截面排名')+
                panel('ETF 位置观察',table(positions(etf_positions),['fund_code','fund_name','bottom_state','price_pos_60','price_pos_120','price_pos_360','bottom_change','position_reliability']),'位置变化优先；覆盖与窗口可靠性见数据质量')),
        section('stock-observation','04 个股观察','数据来源：长桥 OpenAPI · Longbridge ForwardAdjust；趋势与位置使用同一行情序列。按研究主题及高现金流资产分组，仅作观察分类。',''.join(stock_panels)),
    ]
    body=f'<main><header><div class="kicker">AKsignal v{escape(PRODUCT_VERSION)} · DAILY OBSERVATION REPORT · {escape(date)}</div><h1>市场观察日报</h1><p class="principle">{escape(PRINCIPLE)}</p></header><nav aria-label="日报区域"><a href="#market-direction">大类资产</a><a href="#industry-rotation">行业轮动</a><a href="#etf-observation">ETF</a><a href="#stock-observation">个股观察</a><a href="#data-quality">数据质量</a></nav>'+''.join(sections)
    body+='<section id="data-quality" class="quality"><h2>数据质量</h2><div class="panel"><ul>'
    for line in quality:
        label,separator,detail=line.partition('：')
        if not separator or len(label)>14:label,detail='数据说明',line
        attention=any(word in line for word in ('错配','缺失','未统一','未更新'))
        body+='<li'+(' class="attention"' if attention else '')+'><span class="quality-label">'+escape(label)+'</span><span class="quality-detail">'+escape(detail)+'</span></li>' 
    body+='</ul><details><summary>观察口径</summary><p>ETF 活跃：价格高于 MA20 / MA60，且两条均线斜率为正；个股活跃：趋势分 ≥70。两种标尺不同。ETF 与行业 RPS 的横截面也不同，不直接比较。</p><p>位置数值表示窗口内收盘价范围的位置；低位不保证未来不再下跌。进入／离开只在相邻观测日位置均有效时记录；缺口保持未知。</p></details></div></section></main>'
    return '<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AKsignal 观察日报</title><style>'+CSS+'</style></head><body data-report-layout="'+REPORT_LAYOUT_VERSION+'">'+body+SCRIPT+'</body></html>'


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
