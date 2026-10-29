from pathlib import Path
import pandas as pd
from src import market_snapshot as m


def frames():
    return {
        'asset_directions': pd.DataFrame([dict(direction='A股宽基',direction_state='弱势下行',active_ratio=.02)]),
        'industry_directions': pd.DataFrame([
            dict(parent_industry='小方向',direction_state='强势上行',active_count=2,industry_count=2,active_ratio=1.,median_rps15=99.),
            dict(parent_industry='广泛方向',direction_state='强势上行',active_count=6,industry_count=8,active_ratio=.75,median_rps15=88.)]),
        'industry_rotation': pd.DataFrame([dict(industry_code='A',industry_name='甲',trend_stage='已形成趋势',source_status='primary',data_status='confirmed',delta_rps15_5d=20,RPS15=90)]),
        'etf_trends': pd.DataFrame([dict(fund_code='A',trend_active=True,source='em',data_status='confirmed')]),
        'etf_positions': pd.DataFrame([dict(fund_code='A',unreliable_360=False,full_360_sample=True,bottom_change='ENTERED_BOTTOM'),dict(fund_code='B',unreliable_360=True,full_360_sample=False,bottom_change='ENTERED_BOTTOM')])}


def test_editorial_priority_and_unknown_comparison(monkeypatch):
    f=frames()
    monkeypatch.setattr(m,'load',lambda stage,stem,date:f[stem].copy())
    monkeypatch.setattr(m.release,'published_batch',lambda *a:None)
    root=Path(__file__).resolve().parents[2]
    receipt={'trade_date':'20261013','run_id':'r','result':{'data_complete':False}}
    a=m.snapshot(root,Path('.'),receipt)
    assert '广泛方向' in a['sections'][1]['rows'][0]
    assert '1 只 ETF 进入底部' in a['sections'][1]['rows'][-1]
    assert '不能判断' in a['sections'][2]['rows'][0]
    for k in f:f[k]=f[k].sample(frac=1,random_state=5)
    assert m.snapshot(root,Path('.'),receipt)==a


def test_common_source_changes_only(monkeypatch):
    f=frames();old={k:v.copy() for k,v in f.items()}
    old['industry_rotation']['trend_stage']='正在启动'
    old['etf_trends']['trend_active']=False
    monkeypatch.setattr(m,'load',lambda stage,stem,date:(old if date=='20261012' else f)[stem].copy())
    monkeypatch.setattr(m.release,'published_batch',lambda *a:{'run_id':'old','result':{'data_complete':False}})
    receipt={'trade_date':'20261013','run_id':'r','result':{'data_complete':False}}
    root=Path(__file__).resolve().parents[2]
    s=m.snapshot(root,Path('.'),receipt)
    assert '新增趋势行业 1' in s['sections'][2]['rows'][0]
    assert '0 → 1' in s['sections'][2]['rows'][1]
    old['etf_trends']['source']='other'
    s=m.snapshot(root,Path('.'),receipt)
    assert '不能判断' in s['sections'][2]['rows'][1]


def test_visuals_preserve_calendar_gaps_and_unknown(monkeypatch):
    f=frames()
    f['industry_rotation']['parent_industry']='广泛方向'
    monkeypatch.setattr(m,'load',lambda stage,stem,date:f[stem].copy())
    monkeypatch.setattr(m.release,'published_batch',lambda *a:None)
    receipt={'trade_date':'20261013','run_id':'r','result':{'data_complete':False}}
    v=m.snapshot(Path(__file__).resolve().parents[2],Path('.'),receipt)['visuals']
    assert len(v['breadth']['history'])==20
    assert sum(p['active'] is not None for p in v['breadth']['history'])==1
    assert v['breadth']['history'][-1]['dataComplete'] is False
    assert v['breadth']['delta'] is None
    assert v['industries'][0]['change'] is None
    assert v['industries'][0]['activeRatio']==.75
    assert sum(s['count'] for s in v['distribution'])==1


def test_native_index_summary_is_separate_from_etf_breadth(monkeypatch):
    f = frames()
    f['core_indices'] = pd.DataFrame([
        dict(category='broad', status='AVAILABLE', trend_state='FALLING', name='沪深300', reason=''),
        dict(category='broad', status='UNAVAILABLE', trend_state='UNKNOWN', name='中证2000', reason='PROVIDER_SYMBOL_UNVERIFIED'),
        dict(category='style', status='UNAVAILABLE', trend_state='UNKNOWN', name='红利低波', reason='PROVIDER_SYMBOL_UNVERIFIED'),
    ])
    monkeypatch.setattr(m, 'load', lambda stage, stem, date: f[stem].copy())
    monkeypatch.setattr(m.release, 'published_batch', lambda *a: None)
    receipt = {'trade_date': '20261013', 'run_id': 'r', 'result': {'data_complete': False,
               'modules': {'core_indices': {'status': 'partial'}}}}
    s = m.snapshot(Path(__file__).resolve().parents[2], Path('.'), receipt)
    rows = s['sections'][0]['rows']
    assert '核心宽基指数：1/2 可用' in rows[0] and '下行 1' in rows[0]
    assert '中证2000' in rows[1] and '红利低波' in rows[1]
    assert any('ETF 汇总' in row for row in rows)
