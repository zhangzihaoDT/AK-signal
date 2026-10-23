"""Production fixed-stock observations, Longbridge-only, immutable full windows.

Does not read/write the legacy data/legacy/raw/CN_*.csv or HK_*.csv caches. Re-fetching
an entire analysis window keeps vendor ForwardAdjust anchors in one snapshot.
"""
from __future__ import annotations
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import pandas as pd

from src.common.paths import project_root, stock_metrics_dir
from .contract import validate_snapshot, build_snapshot, SeriesIdentity
from .calendar import SessionCalendar
from .consume import observe_snapshot, snapshot_frame
from .longbridge_source import fetch_snapshot, identity_for, quote_context
from .policy import source_policy, excluded_stock


def build_longbridge_stocks(trade_date, online=False, items=None):
    from src.trend_engine.inputs import stock_items, STOCK_METRICS_COLUMNS
    from src.trend_engine.asset import Asset
    root=project_root();target=pd.Timestamp(trade_date).date()
    from .stock_universe import observation_stock_items
    registrations=observation_stock_items() if items is None else stock_items(items)
    assets={(i.asset.market,i.asset.symbol):i.asset for i in registrations}
    series=root/'data/sources/longbridge/raw/daily'
    pointer=series/'partitions'/f'{trade_date}.json'
    policy=source_policy()['stocks']
    signature=hashlib.sha256(json.dumps({'policy':policy,'assets':[asdict(a) for a in assets.values()]},sort_keys=True).encode()).hexdigest()
    if online:
        from longbridge.openapi import Period, AdjustType
        ctx=quote_context(root/'.env' if (root/'.env').exists() else None)
        folder=series/'runs'/trade_date/uuid4().hex;folder.mkdir(parents=True,exist_ok=False)
        records=[]
        requests=[(a,identity_for(a),AdjustType.ForwardAdjust) for a in assets.values() if not excluded_stock(a.market,a.symbol)]
        benchmark=replace(identity_for(Asset('000300','沪深300','CN',exchange='SSE',currency='CNY')),
                          asset_type='index',price_basis='index_level')
        requests.append((None,benchmark,AdjustType.NoAdjust))
        for asset,identity,adjustment in requests:
            r={'security_id':identity.security_id,'symbol':asset.symbol if asset else '_benchmark',
               'market':asset.market if asset else 'CN'}
            try:
                try:
                    snapshot=fetch_snapshot(ctx,identity,target,Period.Day,adjustment)
                except ValueError as exc:
                    if str(exc) != 'invalid OHLC range':
                        raise
                    # Same-source bounded retry; short history stays explicit in calendar gates.
                    snapshot=fetch_snapshot(ctx,identity,target,Period.Day,adjustment,
                                            start_date=target-timedelta(days=365))
                    r['history_limitation']='full_window_invalid_ohlc; validated_365_calendar_day_window'
                name=identity.provider_symbol+'.json'
                (folder/name).write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
                r.update(file=name,snapshot_id=snapshot['snapshot_id'])
            except Exception as exc:
                r.update(error_type=type(exc).__name__)
            records.append(r)
            time.sleep(0.6)
        manifest={'trade_date':trade_date,'signature':signature,'records':records,
                  'source':'longbridge','price_basis':'Longbridge ForwardAdjust',
                  'generated_at':datetime.now(timezone.utc).isoformat()}
        (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        # Failed acquisitions remain explicit; no old/source fallback is attempted.
        pointer.parent.mkdir(parents=True,exist_ok=True)
        temp=pointer.with_suffix('.tmp')
        temp.write_text(json.dumps({'folder':str(folder),'manifest_sha256':hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest()}))
        temp.replace(pointer)
    if not pointer.exists():
        raise ValueError('缺少目标日长桥快照；离线模式不读取旧EM/Sina股票缓存')
    ref=json.loads(pointer.read_text());folder=Path(ref['folder'])
    content=(folder/'manifest.json').read_bytes()
    if hashlib.sha256(content).hexdigest()!=ref['manifest_sha256']:
        raise ValueError('长桥批次摘要不匹配')
    manifest=json.loads(content)
    if manifest['signature']!=signature or manifest['trade_date']!=trade_date:
        raise ValueError('长桥快照配置或交易日不一致，需要在线重建')
    limitations={r['symbol']:r['history_limitation'] for r in manifest['records'] if r.get('history_limitation')}
    snaps={}
    for r in manifest['records']:
        if 'file' not in r:continue
        s=validate_snapshot(json.loads((folder/r['file']).read_text()))
        if s['snapshot_id']!=r['snapshot_id'] or s['target_trade_date']!=target.isoformat() or s['identity']['provider']!='longbridge':
            raise ValueError('长桥快照来源/日期/摘要不一致')
        snaps[(r['market'],r['symbol'])]=s
    bench_snapshot=snaps.get(('CN','_benchmark'))
    bench=snapshot_frame(bench_snapshot) if bench_snapshot else pd.DataFrame()
    if bench_snapshot:
        benchmark_calendar=SessionCalendar.load(root/'config/market_calendars/XSHG.json')
        ba=benchmark_calendar.assess(bench_snapshot)
        if ba['windows']['60']!='OK' or ba['session_complete'] is not True:
            bench=pd.DataFrame()
    cs={m:SessionCalendar.load(root/f'config/market_calendars/{name}.json') for m,name in [('CN','XSHG'),('HK','XHKG')]}
    rows=[];positions=[];audit_assets=[]
    for key,asset in assets.items():
        calendar=cs[asset.market]
        expected=calendar.expected_session(target)
        closed=expected != target
        evidence={'expected_source_trade_date':expected.isoformat(),
                  'market_session_status':'closed' if closed else 'open',
                  'calendar_version':calendar.version,'calendar_sha256':calendar.digest,
                  'calendar_authority':calendar.authority}
        base={'symbol':asset.symbol,'asset_id':asset.symbol,'market':asset.market,'name':asset.name,
              'trade_date':pd.Timestamp(target),'source':'longbridge','source_route':'longbridge',
              'price_basis':'Longbridge ForwardAdjust','adjustment_requested':'ForwardAdjust',
              'price_basis_verified':False,'snapshot_id':None,'source_trade_date':pd.NaT,
              'trend_score':None,'score_trend':None,'data_status':'missing','lag_days':None,
              'history_limitation':limitations.get(asset.symbol,''),**evidence}
        position={'market':asset.market,'symbol':asset.symbol,'name':asset.name,'trade_date':pd.Timestamp(target),
                  'source':'longbridge','price_basis':'Longbridge ForwardAdjust','price_basis_verified':False,
                  'rule_version':'stock_position_v1','calendar_basis':'independent_rule_model',
                  'bottom_state':'UNAVAILABLE','long_term_bottom':None,'bottom_change':'UNKNOWN',
                  'reliability':'MISSING_TARGET','snapshot_id':None,**evidence,
                  'source_trade_date':pd.NaT}
        if excluded_stock(*key):
            base['data_status']='excluded';position['reliability']='EXCLUDED'
        elif key in snaps and snaps[key]["bars"]:
            s=snaps[key]
            # Analysis projection changes only the evaluation date, never stored bars
            # or the original batch snapshot identity. RS uses the same as-of date.
            analysis=(build_snapshot(SeriesIdentity(**s['identity']),s['bars'],expected,
                                    datetime.fromisoformat(s['fetched_at'])) if closed else s)
            observation=observe_snapshot(analysis,calendar,bench)
            trend=observation['trend'];frame=snapshot_frame(s)
            base.update(snapshot_id=s['snapshot_id'],source_trade_date=pd.Timestamp(frame.date.max()),
                        close=float(frame.close.iloc[-1]),**{k:v for k,v in trend.items() if k in {'trend_score','return_20d','relative_strength_20d'}})
            base['score_trend']=base['trend_score']
            if trend['status']=='AVAILABLE' and trend.get('benchmark_status')=='AVAILABLE' and analysis['quality']['target_source_date_verified']:
                base['data_status']='market_closed' if closed else 'current'
                base['lag_days']=(target-expected).days
            base.update(return_5d=float(frame.close.pct_change(5).iloc[-1]) if len(frame)>5 else None,
                        volatility_20d=float(frame.close.pct_change().rolling(20).std().iloc[-1]) if len(frame)>20 else None,
                        drawdown_20d=float((1-frame.close/frame.high.rolling(20).max()).iloc[-1]) if len(frame)>20 else None)
            position.update(observation['position'],snapshot_id=s['snapshot_id'],source_trade_date=base['source_trade_date'])
        rows.append(base);audit_assets.append({**{k:base[k] for k in ('market','symbol','name','source','source_route','data_status','snapshot_id','history_limitation')},
                                             **evidence,'source_trade_date':None if pd.isna(base['source_trade_date']) else base['source_trade_date'].date().isoformat()})
        for item in (i for i in registrations if (i.asset.market,i.asset.symbol)==key):
            positions.append({**position,'theme':item.theme,'tier':item.tier,'participation':item.participation})
    frame=pd.DataFrame(rows)
    for c in STOCK_METRICS_COLUMNS:
        if c not in frame:frame[c]=None
    frame=frame.drop(columns=['watch_level','action'],errors='ignore')
    out=stock_metrics_dir();out.mkdir(parents=True,exist_ok=True)
    frame.to_parquet(out/f'stock_metrics_{trade_date}.parquet',index=False)
    pd.DataFrame(positions).to_parquet(out/f'stock_position_inputs_{trade_date}.parquet',index=False)
    benchmark_date=str(bench.date.max().date()) if not bench.empty else None
    audit={'trade_date':trade_date,'source':'longbridge','price_basis':'Longbridge ForwardAdjust',
           'cross_provider_fallback':False,'snapshot_manifest':str(folder/'manifest.json'),
           'manifest_sha256':ref['manifest_sha256'],'benchmark_source':'longbridge','benchmark_trade_date':benchmark_date,
           'registrations':len(registrations),'unique_assets':len(assets),'assets':audit_assets,
           'sources':{'longbridge':{'requests':len(manifest['records']) if online else 0}},
           'complete':bool(frame.data_status.isin(['current','market_closed','excluded']).all() and benchmark_date==target.isoformat()),
           'excluded':frame.loc[frame.data_status.eq('excluded'),['market','symbol','name']].to_dict('records'),
           'not_updated':frame.loc[~frame.data_status.isin(['current','market_closed','excluded']),['market','symbol','name','data_status']].to_dict('records')}
    (out/f'stock_update_audit_{trade_date}.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    return frame
