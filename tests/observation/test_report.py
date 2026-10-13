import pandas as pd
from src.observation.report import render_html,PRINCIPLE


def test_three_observation_regions_and_separate_quality():
    assets=pd.DataFrame({'direction':['A股'],'median_rps15':[80],'active_ratio':[.2]})
    industries=pd.DataFrame({'industry_name':['通信'],'RPS15':[90],'RPS5':[80],'trend_stage':['已形成趋势']})
    etfs=pd.DataFrame({'fund_code':['000001'],'fund_name':['ETF'],'rps15':[90],'trend_active':[True]})
    stocks=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'trend_score':[80],'return_5d':[.1],'return_20d':[.2]})
    pos=pd.DataFrame({'market':['CN'],'symbol':['000001'],'name':['股票'],'fund_code':['000001'],'fund_name':['ETF'],'bottom_state':['DEEP_BOTTOM'],'bottom_change':['UNCHANGED'],'price_pos_360':[10],'reliability':['OK']})
    html=render_html('20261012',assets,industries,etfs,stocks,pos,pos,['复权审计结果','缓存检查结果'])
    assert PRINCIPLE in html
    for ident in ('market-direction','active-trends','bottom-position','data-quality'):
        assert f'<section id="{ident}"' in html
    assert '复权审计结果' not in html[:html.index('<section id="data-quality"')]
    assert '10.0%' in html


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
