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
