import numpy as np
import pandas as pd
from src.observation.cli import position_snapshot, position_change


def prices(values):
    dates = pd.bdate_range('2024-01-01', periods=len(values))
    return pd.DataFrame({'date':dates,'close':values}), dates


def test_stock_position_low_and_exit_are_observations():
    frame, dates = prices(np.linspace(100,10,400))
    before = position_snapshot(frame,str(dates[-1].date()),dates)
    assert before['price_pos_360'] == 0
    assert before['bottom_state'] == 'DEEP_BOTTOM'
    frame.loc[399,'close'] = 90
    after = position_snapshot(frame,str(dates[-1].date()),dates)
    assert after['bottom_state'] == 'NORMAL'
    assert position_change(after,before) == 'LEFT_BOTTOM'


def test_stock_legitimate_twenty_percent_move_not_etf_split():
    frame, dates = prices(np.linspace(20,10,400))
    frame.loc[399,'close'] = frame.loc[398,'close']*1.2
    result = position_snapshot(frame,str(dates[-1].date()),dates)
    assert result['window_reason_360'] == 'OK'


def test_gap_unknown_does_not_manufacture_exit():
    frame, dates = prices(np.linspace(100,10,400))
    before = position_snapshot(frame,str(dates[-1].date()),dates)
    after = position_snapshot(frame.drop(index=380),str(dates[-1].date()),dates)
    assert after['reliability'] == 'DATA_GAP'
    assert after['price_pos_360'] is None
    assert position_change(after,before) == 'UNKNOWN'


def test_stale_short_flat_duplicate_and_future():
    frame, dates = prices(np.linspace(100,10,400))
    assert position_snapshot(frame.iloc[:-1],str(dates[-1].date()),dates)['reliability']=='MISSING_TARGET'
    assert position_snapshot(frame.iloc[-100:],str(dates[-1].date()),dates)['reliability']=='INSUFFICIENT_HISTORY'
    assert position_snapshot(frame.assign(close=10),str(dates[-1].date()),dates)['reliability']=='FLAT_WINDOW'
    assert position_snapshot(pd.concat([frame,frame.iloc[-1:]]),str(dates[-1].date()),dates)['reliability']=='DUPLICATE_DATES'
    target=str(dates[380].date())
    assert position_snapshot(frame,target,dates)==position_snapshot(frame.iloc[:381],target,dates[:381])


def test_publishing_preserves_original_fact_columns_and_metadata(tmp_path, monkeypatch):
    from src.observation import cli
    monkeypatch.setattr(cli,'outputs_dir',lambda:tmp_path)
    source=pd.DataFrame({'fund_code':['000001'],'rps15':[91.0],
                         'trade_date':[pd.Timestamp('2026-10-12')],
                         'run_date':[pd.Timestamp('2026-10-13')],
                         'generated_at':['original-generation-time']})
    cli.write_frame(source,'etf_trends','20261012')
    saved=pd.read_parquet(tmp_path/'observation/etf_trends_20261012.parquet')
    pd.testing.assert_frame_equal(source,saved[source.columns])


def test_top_level_daily_entry_no_longer_routes_to_industry_confirmation(monkeypatch):
    from src import main, daily_run
    import sys
    calls=[]
    monkeypatch.setattr(daily_run,'main',lambda:calls.append(sys.argv[1:]))
    monkeypatch.setattr(sys,'argv',['main.py','run-day','--date','20261012','--offline'])
    main.main()
    assert calls==[['--date','20261012','--offline']]
