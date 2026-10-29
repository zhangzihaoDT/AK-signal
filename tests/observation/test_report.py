import pandas as pd
from src.observation.report import render_html,PRINCIPLE
from src.observation.contract import SECTION_ANCHORS


def fixture():
    assets=pd.DataFrame({'direction':['A股宽基','A股行业/主题'],'etf_count':[10,20],'median_rps15':[81.0,72.0],'median_rps5':[66.0,60.0],
                         'median_rps1':[63.0,55.0],'change_5d':[1.0,2.0],'active_ratio':[.2,.1],'direction_state':['强势上行','横盘']})
    industries=pd.DataFrame({'industry_code':['881001','881002'],'industry_name':['通信','银行'],'RPS15':[90,60],'RPS5':[80,50],
                             'RPS1':[99,40],'return_1d':[.0234,-.001],'trend_stage':['已形成趋势','—']})
    etfs=pd.DataFrame({'fund_code':['000001'],'fund_name':['ETF'],'rps15':[90],'rps5':[70],'rps1':[88],'return_1d':[-1.25],
                       'trend_active':[True],'price':[1.39],'ma20':[1.34],'ma60':[1.31],'liquidity':[5],
                       'classification_status':['classified_unverified']})
    stocks=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'trend_score':[80],'return_5d':[.1],'return_20d':[.2]})
    pos=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'fund_code':['000001'],'fund_name':['ETF'],
                      'bottom_state':['DEEP_BOTTOM'],'bottom_change':['UNCHANGED'],
                      'price_pos_60':[80],'price_pos_120':[70],'price_pos_360':[10],'reliability':['OK']})
    return assets,industries,etfs,stocks,pos


def test_five_sections_summary_and_three_layers():
    assets,industries,etfs,stocks,pos=fixture()
    html=render_html('20261012',assets,industries,etfs,stocks,pos,pos,['复权审计结果','缓存检查结果'])
    assert PRINCIPLE in html
    assert '<section class="summary panel" id="summary"' in html
    assert html.index('id="summary"') < html.index('id="market-environment"')
    for ident in SECTION_ANCHORS:
        assert f'<section id="{ident}"' in html
        assert f'<a href="#{ident}">' in html
    order=[html.index(f'<section id="{a}"') for a in SECTION_ANCHORS]
    assert order==sorted(order)
    assert html.index('01 市场环境') < html.index('02 行业轮动') < html.index('03 ETF 观察') < html.index('04 研究主线')
    assert 'data-display-contract="daily_display_contract_v1"' in html
    # data quality is exception-driven: audit text stays in the technical detail
    quality=html[html.index('<section id="data-quality"'):]
    assert quality.index('复权审计结果') > quality.index('技术详情')


def test_etf_classification_moved_into_etf_section_with_sample_scope():
    assets,industries,etfs,stocks,pos=fixture()
    etfs=pd.concat([etfs, etfs.assign(fund_code='000002', classification_status='unclassified', trend_active=False)], ignore_index=True)
    html=render_html('20261012',assets,industries,etfs,stocks,pos,pos,['说明'])
    etf_section=html[html.index('<section id="etf-observation"'):html.index('<section id="research-mainlines"')]
    market_section=html[html.index('<section id="market-environment"'):html.index('<section id="industry-rotation"')]
    assert 'id="etf-classification"' in etf_section and 'id="etf-classification"' not in market_section
    assert '已分类样本 1 只（全样本 2 只）' in etf_section and '未分类样本 1 只另计' in etf_section
    assert '不代表全部 ETF 市场' in etf_section
    # classification overview keeps the three contract columns only
    overview=etf_section[etf_section.index('id="etf-classification"'):etf_section.index('id="etf-trends"')]
    default_part=overview[:overview.index('<details')]
    assert '>RPS15 中位</th>' in default_part and '>趋势活跃比例</th>' in default_part and '>方向状态</th>' in default_part
    assert '>RPS5 中位</th>' not in default_part and '>当日 RPS 中位</th>' not in default_part and '>排名变化中位数</th>' not in default_part
    assert '>RPS5 中位</th>' in overview


def test_default_tables_show_contract_columns_and_detail_keeps_the_rest():
    assets,industries,etfs,stocks,pos=fixture()
    html=render_html('20261012',assets,industries,etfs,stocks,pos,pos,['说明'])
    body=html[:html.index('<section id="data-quality"')]
    # industry: RPS15/RPS5/stage default; day return and day RPS in detail
    industry=body[body.index('id="industry-rotation-table"'):body.index('<section id="etf-observation"')]
    industry_default=industry[industry.index('<table'):industry.index('<details')]
    assert '>RPS15</th>' in industry_default and '>RPS5</th>' in industry_default and '>阶段</th>' in industry_default
    assert '>当日涨幅</th>' not in industry_default and '>当日 RPS</th>' not in industry_default
    assert '当日 RPS、当日涨幅、样本数与口径' in industry
    # industry return_1d is a decimal fraction; ETF return_1d is percentage points
    assert '+2.34%' in industry
    # ETF trend: no MA prices / MA rule column in the default table
    trend=body[body.index('id="etf-trends"'):body.index('id="etf-positions"')]
    trend_default=trend[trend.index('<table'):trend.index('<details')]
    assert '>当日涨幅</th>' in trend_default and '>RPS5</th>' in trend_default
    assert '>MA20</th>' not in trend_default and '>MA60</th>' not in trend_default and '>价格</th>' not in trend_default
    assert '-1.2%' in trend_default and '-125.0%' not in body
    # ETF position: 120-day default, 60/360 in detail, legacy tag unchanged
    position=body[body.index('id="etf-positions"'):body.index('<section id="research-mainlines"')]
    position_default=position[position.index('<table'):position.index('<details')]
    assert '>120日位置</th>' in position_default and '>底部状态</th>' in position_default
    assert '>60日位置</th>' not in position_default and '>360日位置</th>' not in position_default
    assert '沿用原 120/360 日低位规则' in position and '不改变底部定义' in position


def test_core_index_default_six_columns_and_audit_in_detail():
    from src.observation.report import core_index_panel
    document={'rule_version':'core-index-facts-v1','registry_hash':'hash','rows':[
        dict(key='csi300',name='沪深300',code='000300',issuer='CSI',category='broad',provider='longbridge',status='AVAILABLE',
             reason='',source_trade_date='2026-10-12',close=4567.89,trend_state='FALLING',snapshot_id='abc',identity_verified=False,
             price_basis='index_level',price_basis_verified=False,calendar_version='c1',
             return_1d=-.0132,return_5d=-.0247,return_20d=-.1079,return_60d=-.1388,
             return_1d_status='OK',return_5d_status='OK',return_20d_status='OK',return_60d_status='OK')]}
    panel=core_index_panel(document,'broad','核心宽基观察')
    default=panel[:panel.index('<details')]
    assert '>当日</th>' in default and '>60日</th>' in default and '>均线状态</th>' in default
    assert '>收盘点位</th>' not in default and '>来源</th>' not in default and '>实际行情日</th>' not in default
    assert '-1.32%' in default and '下行' in default
    assert '收盘点位、来源、实际行情日与数据状态' in panel and '4,567.89' in panel
    assert '000300' in default


def test_research_mainlines_rename_clues_and_separate_structure_from_change():
    from src.market_facts.group_activity import RULE_VERSION, definitions
    metrics=dict(nav=101,ma5=100,ma20=103,return_5d=.01,return_20d=-.02,rs_20d_pct=-1.0,rs_20d_change_5d_pp=.1,
                 participation_5d=.2,participation_10d=.55,participation_20d=.4)
    conditions=dict(price_turn=True,structure=False,support=True,rs_early=False,participation_early=True,sudden=False)
    row=dict(key=definitions()[0]['key'],label='测试主线',status='WATCH',first_trigger=False,path='GRADUAL',event_origin='EARLY_CANDIDATE',
             previous_status='WATCH',transition='CONTINUATION',previous_observation_date='2026-10-21',
             reasons=['价格转向'],metrics=metrics,conditions=conditions,data_complete=True,
             changes=dict(rs_20d_pct=-1.68,participation_5d=-.25,participation_10d=.0),
             leader_clues=[])
    activity=dict(trade_date='20261022',rule_version=RULE_VERSION,input_hash='hash',groups=[row])
    from src.observation.report import mainline_summary
    html,_=mainline_summary('20261022',activity)
    assert '>研究主线</th>' in html and '>趋势阶段</th>' in html and '>启动状态</th>' in html and '>观察变化' in html
    assert '已有观察线索' in html and '已有机会' not in html
    assert 'RS20Δ' in html and '参与度' in html
    from src.observation.report import research_panels
    stocks=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'trend_score':[80],'return_5d':[.1],'return_20d':[.2]})
    pos=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'bottom_state':['NORMAL'],'bottom_change':['UNCHANGED'],
                      'price_pos_120':[50.0],'price_pos_360':[10.0],'reliability':['OK']})
    panels=research_panels('20261022',stocks,pos,activity)
    key=definitions()[0]['key']
    evidence=panels[panels.index('id="stock-group-'+key+'"'):]
    assert '当前结构' in evidence and '相对强弱' in evidence and '上涨范围' in evidence and '较上一观察日' in evidence
    # structure and change are stated separately, never merged into one judgement
    assert '当前结构：' in evidence and '；变化：' in evidence
    assert evidence.index('当前结构：') < evidence.index('；变化：')


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
    assert '展开其余观察对象 33 个' in html
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
    from src.observation.report import mainline_summary, activity_detail
    from src.market_facts.group_activity import RULE_VERSION
    row = dict(key='ai_capex', label='AI <基础设施>', status='WATCH', first_trigger=True,
               path='GRADUAL', previous_status='NO_SETUP', transition='FIRST_TRIGGER',
               previous_observation_date='2026-10-21', reasons=['价格改善'],
               metrics=dict(nav=105, ma5=104, ma20=106, return_5d=.01, rs_20d_pct=-1,
                            rs_20d_change_5d_pp=.6, participation_5d=.6, participation_10d=.5,
                            participation_20d=.4), changes=dict(rs_20d_pct=.2, participation_10d=.1))
    document = dict(trade_date='20261022', rule_version=RULE_VERSION, groups=[row])
    html, annotations = mainline_summary('20261022', document)
    assert 'href="#stock-group-ai_capex"' in html
    assert 'AI &lt;基础设施&gt;' in html
    assert '首次 WATCH' in html
    detail = activity_detail(annotations['ai_capex'])
    assert '渐进式' in detail and '首次触发：是' in detail
    assert 'RS20Δ -1.00%' in detail and '60% / 50% / 40%' in detail
    assert '2026-10-21' in detail and 'NO_SETUP → WATCH' in detail
    row.update(status='NO_SETUP', first_trigger=False, transition='NO_CHANGE')
    # NO_SETUP is not an active episode: the event text must not read as one
    summary = mainline_summary('20261022', document)[0]
    assert 'NO_SETUP · 无启动候选' not in summary
    assert '无启动候选，未形成 WATCH 观察期' in summary
    row.update(status='UNAVAILABLE', reason='MARKET_CLOSED')
    html, _ = mainline_summary('20261022', document)
    assert '今日未发现新的趋势启动候选' not in html
    assert '无法判定' in html and '未作今日判定' in html
    assert '休市' in activity_detail(row)
    assert mainline_summary('20261023', document)[1] == {}
    document['rule_version'] = 'stock_group_activity_v1'
    assert mainline_summary('20261022', document)[1] == {}


def test_rendered_trend_receipt_and_anchors_fail_closed():
    import pytest
    from copy import deepcopy
    from src.market_facts.group_activity import RULE_VERSION
    from src.observation.report import mainline_summary,activity_detail,validate_activity_rendering
    row=dict(key='g',label='测试',status='UNAVAILABLE',reason='MARKET_CLOSED',first_trigger=False)
    activity=dict(trade_date='20261022',rule_version=RULE_VERSION,input_hash='hash',groups=[row])
    receipt=dict(rule_version=RULE_VERSION,input_hash='hash',states=[{k:row.get(k) for k in
                ('key','status','first_trigger','path','event_origin','transition','reasons','metrics','leader_clues','evidence_channel','alert_kind','event_active','episode_status','trigger_channel')}])
    html=mainline_summary('20261022',activity)[0]+'<article id="stock-group-g">'+activity_detail(row)+'</article>'
    validate_activity_rendering('20261022',activity,receipt,html)
    changed=deepcopy(receipt);changed['states'][0]['status']='NO_SETUP'
    with pytest.raises(ValueError,match='JSON'):
        validate_activity_rendering('20261022',activity,changed,html)
    with pytest.raises(ValueError,match='链接'):
        validate_activity_rendering('20261022',activity,receipt,html.replace('id="stock-group-g"','id="wrong"'))


def test_member_lead_is_visible_even_when_group_has_no_setup():
    from src.observation.report import mainline_summary, activity_detail
    from src.market_facts.group_activity import RULE_VERSION
    row=dict(key='x',label='单股线索',status='NO_SETUP',first_trigger=False,path=None,event_origin=None,
        metrics=dict(nav=101,ma5=100,ma20=103,return_5d=.01,rs_20d_pct=-1,
                     rs_20d_change_5d_pp=.1,participation_5d=.2,participation_10d=.2,participation_20d=.2),
        leader_clues=[dict(market='CN',symbol='600000',return_1d=.08,first_clue=True)])
    html,_=mainline_summary('20261023',dict(trade_date='20261023',rule_version=RULE_VERSION,groups=[row]))
    assert '单股线索' in html and '1 条新增个股线索（非组级启动）' in html
    assert '600000 +8.0%' in activity_detail(row)
    assert '组级首次触发：否' in activity_detail(row)


# --- v0.7.3 patch: deterministic presentation --------------------------------
def test_blank_bucket_is_unclassified_not_a_category():
    from src.observation.report import classification, classified_mask
    etfs = pd.DataFrame({
        'fund_code': ['1', '2', '3', '4', '5'],
        'asset_bucket': ['industry', 'industry', '', '   ', None],
        # The producer fills NaN with '' but leaves blank strings in place:
        # relying on the status marker alone would report 4 classified, 1 unclassified.
        'classification_status': ['classified_unverified', 'classified_unverified',
                                  'classified_unverified', 'classified_unverified', 'unclassified'],
        'direction': ['A股行业/主题', 'A股行业/主题', '其他', '其他', '其他'],
    })
    scope = classification(etfs)
    assert (scope['total'], scope['classified'], scope['unclassified']) == (5, 2, 3)
    assert scope['blank_marker'] == 2
    assert scope['buckets'] == {'A股行业/主题': 2}
    assert classified_mask(etfs).tolist() == [True, True, False, False, False]


def test_classified_total_never_counts_whole_population():
    from src.observation.report import classification
    etfs = pd.DataFrame({'fund_code': ['1', '2'], 'asset_bucket': ['', ''],
                         'classification_status': ['unclassified', 'unclassified']})
    scope = classification(etfs)
    assert scope['classified'] == 0 and scope['unclassified'] == 2


def test_classification_overview_drops_other_pool_and_reconciles():
    from src.observation.report import direction_panel_with_scope, classification
    assets = pd.DataFrame({'direction': ['A股宽基', '海外权益', '其他'], 'etf_count': [167, 20, 566],
                           'median_rps15': [28.8, 75.1, 56.1], 'active_ratio': [.018, .15, .019],
                           'direction_state': ['弱势下行', '强势上行', '横盘']})
    etfs = pd.DataFrame({'fund_code': [str(i) for i in range(753)],
                         'asset_bucket': ['broad_market'] * 167 + ['overseas_equity'] * 20 + [''] * 566})
    scope = classification(etfs)
    html = direction_panel_with_scope(assets, scope)
    assert '>其他</td>' not in html and '海外权益' in html
    assert scope['classified'] == 187 and scope['unclassified'] == 566
    assert '类别产品数合计' not in html          # 167 + 20 == 187, nothing to reconcile
    assert '不设“其他”行' in html


def test_classification_overview_says_so_when_nothing_is_classified():
    from src.observation.report import direction_panel_with_scope, classification
    assets = pd.DataFrame({'direction': ['其他'], 'etf_count': [10], 'median_rps15': [50.0],
                           'active_ratio': [.1], 'direction_state': ['横盘']})
    etfs = pd.DataFrame({'fund_code': ['1'], 'asset_bucket': ['']})
    html = direction_panel_with_scope(assets, classification(etfs))
    assert '本批没有已分类类别' in html


def test_summary_scope_reading_excludes_other_pool():
    from src.observation.report import summary_panel
    assets = pd.DataFrame({'direction': ['A股宽基', '其他'], 'etf_count': [10, 90],
                           'median_rps15': [50.0, 60.0], 'active_ratio': [.1, .2],
                           'direction_state': ['弱势下行', '强势上行']})
    etfs = pd.DataFrame({'fund_code': [str(i) for i in range(100)],
                         'asset_bucket': [''] * 90 + ['broad_market'] * 10,
                         'classification_status': ['unclassified'] * 90 + ['classified_unverified'] * 10})
    html = summary_panel('20261027', None, assets, None, etfs, None, None, None, [], None)
    assert '已分类类别中无类别呈强势方向状态' in html
    assert '分类覆盖：已分类 10 / 全样本 100 只' in html
    assert '“其他”' not in html.split('class="digest"')[1]


def test_summary_does_not_repeat_the_mainline_reasons():
    from src.market_facts.group_activity import RULE_VERSION
    from src.observation.report import summary_panel
    metrics = dict(nav=101, ma5=100, ma20=103, return_5d=.01, return_20d=-.02, rs_20d_pct=-1.0,
                   rs_20d_change_5d_pp=.1, participation_5d=.2, participation_10d=.55, participation_20d=.4)
    # priority 优先研究: active status with structure and support together
    conditions = dict(price_turn=True, structure=True, support=True, rs_early=False,
                      participation_early=True, sudden=False)
    from src.market_facts.group_activity import definitions
    row = dict(key=definitions()[0]['key'], label='测试主线', status='WATCH', first_trigger=False,
               path='GRADUAL', event_origin='EARLY_CANDIDATE', previous_status='WATCH',
               transition='CONTINUATION', previous_observation_date='2026-10-21', reasons=['价格转向'],
               metrics=metrics, conditions=conditions, data_complete=True,
               changes=dict(rs_20d_pct=-1.68, participation_5d=-.25, participation_10d=.0), leader_clues=[])
    activity = dict(trade_date='20261022', rule_version=RULE_VERSION, input_hash='hash', groups=[row])
    html = summary_panel('20261022', None, None, None, None, None, None, activity, [], None)
    from src.observation.group_attention import attention_view
    view = attention_view(activity)
    assert view['groups'][0]['priority'] == '优先研究'
    assert view['groups'][0]['observation_reason'] in html
    # the prose names the group once; its reason appears only in the digest row
    assert html.count(view['groups'][0]['observation_reason']) == 1
    assert '研究主线关注 测试主线。' in html


def test_ordering_is_numeric_for_ranking_and_text_for_codes():
    from src.observation.report import ordered
    frame = pd.DataFrame({'rps15': ['99.5', '100.0', '99.9'], 'liquidity': [1.0, 2.0, 3.0],
                          'fund_code': [88, 100, 99]})
    assert ordered(frame, ('rps15', 'liquidity'), ('fund_code',))['fund_code'].tolist() == ['100', '99', '88']
    ties = pd.DataFrame({'rps15': [90.0, 90.0, 90.0], 'fund_code': ['9', '100', '88']})
    assert ordered(ties, ('rps15',), ('fund_code',))['fund_code'].tolist() == ['100', '88', '9']


def test_coverage_cards_render_from_module_receipts():
    from src.observation.report import quality_sections
    modules = {'core_indices': {'available_count': 9, 'expected_count': 9},
               'industry_rotation': {'status': 'available', 'verified_count': 128},
               'etf_trends': {'rows': 1339}, 'stock_trends': {'rows': 69}}
    html = quality_sections('20261027', ['说明'], modules)
    for value in ('9/9', '128', '1,339', '69'):
        assert f'<b>{value}</b>' in html
    assert '<b>—</b>' not in html
