import pandas as pd
from src.observation.report import render_html,PRINCIPLE


def test_four_module_sections_and_separate_quality():
    assets=pd.DataFrame({'direction':['A股'],'median_rps15':[80],'active_ratio':[.2]})
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
    assert '高现金流资产</h3>' in html


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
