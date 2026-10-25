import pandas as pd
from src.observation.report import render_html,PRINCIPLE


def test_four_module_sections_and_separate_quality():
    assets=pd.DataFrame({'direction':['A股'],'median_rps15':[81.0],'median_rps5':[72.0],'median_rps1':[63.0],'active_ratio':[.2]})
    industries=pd.DataFrame({'industry_name':['通信'],'RPS15':[90],'RPS5':[80],'RPS1':[99],'return_1d':[.0234],'trend_stage':['已形成趋势']})
    etfs=pd.DataFrame({'fund_code':['000001'],'fund_name':['ETF'],'rps15':[90],'rps1':[88],'return_1d':[-1.25],'trend_active':[True]})
    stocks=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'trend_score':[80],'return_5d':[.1],'return_20d':[.2]})
    pos=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'fund_code':['000001'],'fund_name':['ETF'],'bottom_state':['DEEP_BOTTOM'],'bottom_change':['UNCHANGED'],'price_pos_360':[10],'reliability':['OK']})
    html=render_html('20261012',assets,industries,etfs,stocks,pos,pos,['复权审计结果','缓存检查结果'])
    assert PRINCIPLE in html
    for ident in ('market-direction','industry-rotation','etf-observation','stock-observation','data-quality'):
        assert f'<section id="{ident}"' in html
    assert '复权审计结果' not in html[:html.index('<section id="data-quality"')]
    assert '10.0%' in html
    assert html.count('>当日 RPS</th>') == 2
    assert html.count('>RPS5</th>') == 1
    assert html.count('>RPS15 中位</th>') == 1
    assert html.count('>RPS5 中位</th>') == 1
    assert html.count('>当日 RPS 中位</th>') == 1
    assert '11/12' not in html
    assert '个有效配置成分' in html
    assert '另有 1 个按观察政策排除' in html
    assert '按 8 个研究篮子及高现金流资产分组' in html
    assert '同股可跨篮子展示' in html
    for value in ('81.0','72.0','63.0'):
        assert value in html
    assert html.count('>当日涨幅</th>') == 2
    assert '2.3%' in html
    assert '-1.2%' in html
    assert '-125.0%' not in html
    assert html.index('01 大类资产') < html.index('02 行业轮动') < html.index('03 ETF') < html.index('04 个股观察')
    for source in ('东方财富 / 新浪','通达信官方 .day','长桥 OpenAPI','Longbridge ForwardAdjust'):
        assert source in html
    from src.research.baskets.config import load_baskets
    from html import escape
    assert len(load_baskets()) == 8
    for basket in load_baskets().values():
        assert escape(basket['label']) in html
    assert '其他固定池观察' in html
    assert '<summary>高现金流资产 · UNAVAILABLE</summary>' in html


def test_identity_mismatch_is_excluded_from_observation_body(monkeypatch):
    from src.observation import report
    monkeypatch.setattr(report,'identity_issues',lambda date:[{'market':'HK','symbol':'02510'}])
    d=report.mark_identity(pd.DataFrame({'market':['HK','CN'],'symbol':['02510','000001']}),'20261012')
    assert d.identity_status.tolist()==['IDENTITY_MISMATCH','NOT_CHECKED']


def test_single_expansion_never_repeats_preview_rows():
    from src.observation.report import table
    d=pd.DataFrame({'symbol':[f'CODE-{i:03d}' for i in range(45)],
                    'name':[f'ASSET-{i:03d}' for i in range(45)],'trend_score':[80]*45})
    html=table(d,['symbol','name','trend_score'],preview_count=12)
    assert html.count('<tr data-row>')==45
    assert html.count('<details data-remainder>')==1
    assert html.count('data-table-search aria-label')==1
    for symbol in d.symbol:
        assert html.count(symbol)==1
    assert '完整固定观察池' not in html


def test_ties_are_deterministic_and_unknown_positions_stay_last():
    from src.observation.report import ordered,positions
    d=pd.DataFrame({'market':['CN']*3,'symbol':['B','A','C'],
                    'trend_score':[80]*3,'return_20d':[.1,.1,.2]})
    first=ordered(d,('trend_score','return_20d'),('market','symbol'))
    second=ordered(d.sample(frac=1,random_state=2),('trend_score','return_20d'),('market','symbol'))
    assert first.symbol.tolist()==second.symbol.tolist()==['C','A','B']
    pos=pd.DataFrame({'market':['CN']*4,'symbol':['unknown','normal','low','changed'],
                      'bottom_state':['DEEP_BOTTOM','NORMAL','DEEP_BOTTOM','NORMAL'],
                      'bottom_change':['UNKNOWN','UNCHANGED','UNCHANGED','LEFT_BOTTOM'],
                      'price_pos_360':[0,60,10,80],
                      'reliability':['DATA_GAP','OK','OK','OK']})
    assert positions(pos,stock=True).symbol.tolist()==['changed','low','normal','unknown']


def test_identity_guard_survives_new_date_without_local_audit(tmp_path,monkeypatch):
    import json
    from src.observation.report import identity_issues
    from src.common import paths
    monkeypatch.setattr(paths,'config_dir',lambda:tmp_path)
    monkeypatch.setattr(paths,'data_dir',lambda:tmp_path/'data')
    (tmp_path/'observation_identity_issues.json').write_text(json.dumps({'issues':[{'market':'HK','symbol':'02510'}]}))
    assert identity_issues('20261014')==[{'market':'HK','symbol':'02510'}]


def test_group_activity_summary_links_evidence_and_unknown_are_distinct():
    from src.observation.report import activity_summary, activity_detail
    from src.market_facts.group_activity import RULE_VERSION
    row = dict(key='ai_capex', label='AI <基础设施>', status='WATCH', first_trigger=True,
               path='GRADUAL', previous_status='NO_SETUP', transition='FIRST_TRIGGER',
               previous_observation_date='2026-10-21', reasons=['价格改善'],
               metrics=dict(nav=105, ma5=104, ma20=106, return_5d=.01, rs_20d_pct=-1,
                            rs_20d_change_5d_pp=.6, participation_5d=.6, participation_10d=.5,
                            participation_20d=.4), changes=dict(rs_20d_pct=.2, participation_10d=.1))
    document = dict(trade_date='20261022', rule_version=RULE_VERSION, groups=[row])
    html, annotations = activity_summary('20261022', document)
    assert 'href="#stock-group-ai_capex"' in html
    assert 'AI &lt;基础设施&gt;' in html
    assert '首次 WATCH' in html
    detail = activity_detail(annotations['ai_capex'])
    assert '渐进式' in detail and '首次触发：是' in detail
    assert 'RS20Δ -1.00%' in detail and '60% / 50% / 40%' in detail
    assert '2026-10-21' in detail and 'NO_SETUP → WATCH' in detail
    row.update(status='NO_SETUP', first_trigger=False, transition='NO_CHANGE')
    assert 'NO_SETUP · 无启动候选' in activity_summary('20261022', document)[0]
    row.update(status='UNAVAILABLE', reason='MARKET_CLOSED')
    html, _ = activity_summary('20261022', document)
    assert '今日未发现新的趋势启动候选' not in html
    assert '无法判定' in html and '未作今日判定' in html
    assert '休市' in activity_detail(row)
    assert activity_summary('20261023', document)[1] == {}
    document['rule_version'] = 'stock_group_activity_v1'
    assert activity_summary('20261022', document)[1] == {}


def test_rendered_trend_receipt_and_anchors_fail_closed():
    import pytest
    from copy import deepcopy
    from src.market_facts.group_activity import RULE_VERSION
    from src.observation.report import activity_summary,activity_detail,validate_activity_rendering
    row=dict(key='g',label='测试',status='UNAVAILABLE',reason='MARKET_CLOSED',first_trigger=False)
    activity=dict(trade_date='20261022',rule_version=RULE_VERSION,input_hash='hash',groups=[row])
    receipt=dict(rule_version=RULE_VERSION,input_hash='hash',states=[{k:row.get(k) for k in
                ('key','status','first_trigger','path','event_origin','transition','reasons','metrics','leader_clues','evidence_channel','alert_kind','event_active','episode_status','trigger_channel')}])
    html=activity_summary('20261022',activity)[0]+'<article id="stock-group-g">'+activity_detail(row)+'</article>'
    validate_activity_rendering('20261022',activity,receipt,html)
    changed=deepcopy(receipt);changed['states'][0]['status']='NO_SETUP'
    with pytest.raises(ValueError,match='JSON'):
        validate_activity_rendering('20261022',activity,changed,html)
    with pytest.raises(ValueError,match='链接'):
        validate_activity_rendering('20261022',activity,receipt,html.replace('id="stock-group-g"','id="wrong"'))


def test_member_lead_is_visible_even_when_group_has_no_setup():
    from src.observation.report import activity_summary, activity_detail
    from src.market_facts.group_activity import RULE_VERSION
    row=dict(key='x',label='单股线索',status='NO_SETUP',first_trigger=False,path=None,event_origin=None,
        metrics=dict(nav=101,ma5=100,ma20=103,return_5d=.01,rs_20d_pct=-1,
                     rs_20d_change_5d_pp=.1,participation_5d=.2,participation_10d=.2,participation_20d=.2),
        leader_clues=[dict(market='CN',symbol='600000',return_1d=.08,first_clue=True)])
    html,_=activity_summary('20261023',dict(trade_date='20261023',rule_version=RULE_VERSION,groups=[row]))
    assert '单股线索' in html and '1 条新增个股线索（非组级启动）' in html
    assert '600000 +8.0%' in activity_detail(row)
    assert '组级首次触发：否' in activity_detail(row)
