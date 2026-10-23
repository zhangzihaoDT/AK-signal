from datetime import date, datetime, timezone
import json
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from src.market_facts import stock_daily
from src.market_facts.contract import build_snapshot
from src.trend_engine.asset import Asset


def setup_root(tmp_path,monkeypatch):
    cfg=tmp_path/'config/market_calendars';cfg.mkdir(parents=True)
    original=Path(__file__).resolve().parents[2]
    for name in ('XSHG','XHKG'):copyfile(original/f'config/market_calendars/{name}.json',cfg/f'{name}.json')
    copyfile(original/'config/observation_sources.yaml',tmp_path/'config/observation_sources.yaml')
    monkeypatch.setattr(stock_daily,'project_root',lambda:tmp_path)
    monkeypatch.setattr('src.market_facts.policy.project_root',lambda:tmp_path)
    monkeypatch.setattr(stock_daily,'stock_metrics_dir',lambda:tmp_path/'outputs/stock_metrics')
    monkeypatch.setattr(stock_daily,'quote_context',lambda *_:object())
    monkeypatch.setattr(stock_daily.time,'sleep',lambda *_:None)
    items=[SimpleNamespace(asset=Asset('600900','长江电力','CN',exchange='SSE',currency='CNY',category='hydro_nuclear'),
                           theme='theme',tier='hydro_nuclear',participation='tradeable'),
           SimpleNamespace(asset=Asset('02525','禾赛','HK',currency='HKD',category='adas_lidar'),
                           theme='theme',tier='adas_lidar',participation='tradeable')]
    calls=[]
    def fetch(ctx,identity,target,*args):
        calls.append(identity.provider_symbol)
        cal=stock_daily.SessionCalendar.load(cfg/'XSHG.json')
        days=[d for d in cal.days if d<=target][-450:]
        values=np.linspace(10,20,len(days))
        rows=[dict(source_trade_date=d.isoformat(),date_evidence='source_dated_daily',
                   open=float(v),high=float(v+1),low=float(v-1),close=float(v),volume=1000.,turnover=10000.)
              for d,v in zip(days,values)]
        return build_snapshot(identity,rows,target,datetime(2026,10,16,tzinfo=timezone.utc))
    monkeypatch.setattr(stock_daily,'fetch_snapshot',fetch)
    return items,calls


def test_single_provider_staged_trends_positions_exclusion_and_offline(tmp_path,monkeypatch):
    items,calls=setup_root(tmp_path,monkeypatch)
    df=stock_daily.build_longbridge_stocks('20261015',online=True,items=items)
    assert calls==['600900.SH','000300.SH']
    assert df.data_status.tolist()==['current','excluded']
    assert set(df.source)=={'longbridge'}
    assert 'action' not in df and 'watch_level' not in df
    path=tmp_path/'outputs/stock_metrics'
    pos=pd.read_parquet(path/'stock_position_inputs_20261015.parquet')
    assert pos.iloc[0].snapshot_id==df.iloc[0].snapshot_id
    audit=json.loads((path/'stock_update_audit_20261015.json').read_text())
    assert audit['complete'] and audit['benchmark_source']=='longbridge'
    pd.testing.assert_frame_equal(df,stock_daily.build_longbridge_stocks('20261015',items=items))
    assert len(calls)==2
    assert not (tmp_path/'data/legacy/raw').exists()


def test_failure_never_falls_back_to_legacy_or_another_day(tmp_path,monkeypatch):
    items,_=setup_root(tmp_path,monkeypatch)
    legacy=tmp_path/'data/legacy/raw';legacy.mkdir(parents=True)
    (legacy/'CN_600900.csv').write_text('date,close\n2026-10-15,10\n')
    def failed(*_):raise RuntimeError('source unavailable')
    monkeypatch.setattr(stock_daily,'fetch_snapshot',failed)
    df=stock_daily.build_longbridge_stocks('20261015',online=True,items=items)
    assert df.iloc[0].data_status=='missing'
    assert pd.isna(df.iloc[0].trend_score)
    with pytest.raises(ValueError,match='离线模式不读取旧'):
        stock_daily.build_longbridge_stocks('20261014',items=items)


def test_exclusion_is_policy_specific_not_blanket_missing_waiver(tmp_path,monkeypatch):
    setup_root(tmp_path,monkeypatch)
    from src.market_facts.policy import aligned_stock_rows
    frame=pd.DataFrame({'market':['HK','CN'],'symbol':['02525','600900'],
                        'trade_date':pd.to_datetime(['2026-10-15']*2),
                        'source_trade_date':[pd.NaT,pd.NaT],'data_status':['excluded','excluded']})
    assert aligned_stock_rows(frame,'20261015').tolist()==[True,False]


def test_missing_policy_fails_closed(tmp_path,monkeypatch):
    monkeypatch.setattr('src.market_facts.policy.project_root',lambda:tmp_path)
    from src.market_facts.policy import source_policy
    with pytest.raises(ValueError,match='禁止隐式切回旧来源'):
        source_policy()


def test_position_reader_rejects_other_snapshot(tmp_path,monkeypatch):
    items,_=setup_root(tmp_path,monkeypatch)
    stock_daily.build_longbridge_stocks('20261015',online=True,items=items)
    from src.observation import cli
    path=tmp_path/'outputs/stock_metrics'
    monkeypatch.setattr(cli,'stock_metrics_dir',lambda:path)
    assert not cli.stock_positions('20261015').empty
    p=path/'stock_position_inputs_20261015.parquet'
    d=pd.read_parquet(p);d.loc[d.symbol.eq('600900'),'snapshot_id']='another-snapshot'
    d.to_parquet(p,index=False)
    with pytest.raises(ValueError,match='同一长桥快照'):
        cli.stock_positions('20261015')


@pytest.mark.parametrize('drop_last', [False, True])
def test_hk_holiday_keeps_real_date_and_rejects_missing_last_session(tmp_path, monkeypatch, drop_last):
    items,_=setup_root(tmp_path,monkeypatch)
    items.append(SimpleNamespace(asset=Asset('00883','中国海洋石油','HK',currency='HKD',category='hydro_nuclear'),
                                 theme='theme',tier='energy',participation='monitor_only'))
    def fetch(ctx,identity,target,*args):
        name='XHKG' if identity.market=='HK' else 'XSHG'
        cal=stock_daily.SessionCalendar.load(tmp_path/f'config/market_calendars/{name}.json')
        days=[d for d in cal.days if d<=target][-450:]
        if identity.market=='HK' and drop_last: days=days[:-1]
        rows=[dict(source_trade_date=d.isoformat(),date_evidence='source_dated_daily',
                   open=20.,high=21.,low=19.,close=20.,volume=1000.,turnover=20000.) for d in days]
        return build_snapshot(identity,rows,target,datetime(2026,10,20,tzinfo=timezone.utc))
    monkeypatch.setattr(stock_daily,'fetch_snapshot',fetch)
    df=stock_daily.build_longbridge_stocks('20261019',online=True,items=items)
    hk=df[df.symbol.eq('00883')].iloc[0]
    from src.market_facts.policy import aligned_stock_rows
    assert hk.trade_date==pd.Timestamp('20261019')
    assert hk.expected_source_trade_date=='2026-10-16'
    assert hk.market_session_status=='closed'
    assert hk.data_status==('missing' if drop_last else 'market_closed')
    assert aligned_stock_rows(df,'20261019').all()==(not drop_last)
    if not drop_last:
        assert hk.source_trade_date==pd.Timestamp('20261016')
        forged=df.copy();forged.loc[forged.symbol.eq('00883'),'calendar_sha256']='wrong'
        assert not aligned_stock_rows(forged,'20261019').all()
        assert not aligned_stock_rows(df,'20261020').all()
        pos=pd.read_parquet(tmp_path/'outputs/stock_metrics/stock_position_inputs_20261019.parquet')
        assert pos[pos.symbol.eq('00883')].iloc[0].snapshot_id==hk.snapshot_id
