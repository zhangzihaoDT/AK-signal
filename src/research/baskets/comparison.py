"""Explicit Longbridge-only basket comparison, separate from daily stock metrics."""
from datetime import date,datetime
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from src.common.paths import project_root
from src.market_facts.contract import build_snapshot, validate_snapshot
from src.market_facts.longbridge_source import quote_context, identity_for
from src.market_facts.policy import excluded_stock
from src.trend_engine.asset import Asset
from .nav import fixed_weight_nav
from .config import load_baskets, expand_constituents, basket_config_hash


def comparison_baskets():
    baskets=load_baskets()
    baskets['high_cashflow']={'label':'高现金流资产','source':{'type':'theme','key':'high_cashflow','include_groups':['hydro_nuclear','telecom_operator','toll_road','port_operator','cyclical_power_watch']},'weighting':{'method':'group_equal'}}
    return baskets


def constituents(basket):
    members=[];excluded=[]
    for a in expand_constituents(basket):
        a={**a,'symbol':a['symbol'].zfill(5 if a['market']=='HK' else 6)}
        (excluded if excluded_stock(a['market'],a['symbol']) else members).append(a)
    if not members:
        raise ValueError('No included members')
    return members,excluded


def fetch_prices(start,end):
    """Immutable close-only projection; never certifies or repairs supplier OHLC."""
    from longbridge.openapi import Period,AdjustType
    from src.selection.universe import _infer_exchange
    ctx=quote_context(project_root()/'.env')
    members={}
    for key,b in comparison_baskets().items():
        for a in constituents({**b,'key':key})[0]:
            members[(a['market'],a['symbol'])]=a
    folder=project_root()/'data/sources/longbridge/raw/baskets'/end.replace('-','')/uuid4().hex
    folder.mkdir(parents=True)
    records=[]
    requests=[(market,symbol,a['name'],False) for (market,symbol),a in members.items()]+[('CN','000300','沪深300',True)]
    for market,symbol,name,benchmark in requests:
        identity=identity_for(Asset(symbol,name,market,exchange='SSE' if benchmark else 'HKEX' if market=='HK' else _infer_exchange(symbol),currency='HKD' if market=='HK' else 'CNY'))
        bars=ctx.history_candlesticks_by_date(identity.provider_symbol,Period.Day,AdjustType.NoAdjust if benchmark else AdjustType.ForwardAdjust,date.fromisoformat(start),date.fromisoformat(end))
        tz=ZoneInfo('Asia/Hong_Kong' if market=='HK' else 'Asia/Shanghai')
        rows=[{'source_trade_date':b.timestamp.astimezone(tz).date().isoformat(),'source_timestamp':b.timestamp.astimezone(tz).isoformat(),'date_evidence':'source_dated_daily','bar_kind':'close_only','open':None,'high':None,'low':None,'close':float(b.close),'volume':float(b.volume),'turnover':float(b.turnover)} for b in bars]
        if benchmark:
            from dataclasses import replace
            identity=replace(identity,asset_type='index',price_basis='index_level')
        snapshot=build_snapshot(identity,rows,date.fromisoformat(end),datetime.now(tz))
        if not snapshot['quality']['target_source_date_verified']:
            raise ValueError(f'Missing target date: {market} {symbol}')
        filename='benchmark.json' if benchmark else f'{market}_{symbol}.json'
        (folder/filename).write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
        records.append(dict(market=market,symbol=symbol,name=name,benchmark=benchmark,file=filename,snapshot_id=snapshot['snapshot_id']))
        print(f'Longbridge {len(records)}/{len(requests)} {market} {symbol}',flush=True)
        time.sleep(.6)
    (folder/'manifest.json').write_text(json.dumps(dict(start=start,end=end,source='longbridge',price_basis='Longbridge ForwardAdjust; benchmark NoAdjust',projection='source close only; OHLC not used or repaired',records=records),ensure_ascii=False,indent=2))
    return folder


def build_longbridge_comparison(snapshot,output,start,end):
    output=Path(output);snapshot=Path(snapshot)
    manifest=json.loads((snapshot/'manifest.json').read_text())
    if start<manifest['start'] or end>manifest['end'] or manifest['source']!='longbridge':
        raise ValueError('Snapshot range/source mismatch')
    prices={};benchmark=None; calendar_audit=[]
    for r in manifest['records']:
        s=validate_snapshot(json.loads((snapshot/r['file']).read_text()))
        if s['identity']['provider']!='longbridge' or s['snapshot_id']!=r['snapshot_id']:
            raise ValueError('Invalid Longbridge snapshot identity/hash')
        from src.market_facts.calendar import SessionCalendar
        calendar=SessionCalendar.load(project_root()/f"config/market_calendars/{'XHKG' if r['market']=='HK' else 'XSHG'}.json")
        assessment=calendar.assess(s)
        calendar_audit.append(dict(market=r['market'],symbol=r['symbol'],benchmark=r['benchmark'],missing_sessions=assessment['missing_sessions'],unexpected_sessions=assessment['unexpected_sessions'],calendar_authority=assessment['calendar_authority']))
        if r['benchmark'] and (assessment['missing_sessions'] or assessment['unexpected_sessions']):
            raise ValueError('Benchmark calendar gap')
        frame=pd.DataFrame(s['bars']);frame['date']=pd.to_datetime(frame.source_trade_date)
        frame=frame[(frame.date>=start)&(frame.date<=end)][['date','close']]
        if frame.empty or frame.date.max()!=pd.Timestamp(end):
            raise ValueError('Stale prices')
        if r['benchmark']:benchmark=frame
        else:prices[(r['market'],r['symbol'])]=frame
    if benchmark is None or benchmark.date.min()!=pd.Timestamp(start):
        raise ValueError('Benchmark boundary mismatch')
    # Late listing cash is permitted only for the documented IPO, never for arbitrary missing history.
    for identity,frame in prices.items():
        if frame.date.min()>pd.Timestamp(start) and identity!=('CN','688449'):
            raise ValueError(f'Unexplained late history: {identity}')
    results=[];output.mkdir(parents=True,exist_ok=True)
    exclusions=[]
    for key,b in comparison_baskets().items():
        members,excluded=constituents({**b,'key':key});exclusions.extend(excluded)
        nav,weights=fixed_weight_nav(prices,members,benchmark,b['weighting']['method'])
        total=float(nav.basket.iloc[-1]-100)
        if abs(nav.basket.iloc[0]-100)>1e-8 or abs(sum(a['contribution_pp'] for a in weights)-total)>1e-8:
            raise ValueError('NAV/weight reconciliation failed')
        metrics=dict(return_pct=total,benchmark_return_pct=float(nav.benchmark.iloc[-1]-100),excess_pp=float(nav.basket.iloc[-1]-nav.benchmark.iloc[-1]),max_drawdown_pct=float((nav.basket/nav.basket.cummax()-1).min()*100),assets=len(weights),start=start,end=end)
        nav.to_csv(output/f'{key}_nav.csv');pd.DataFrame(weights).to_csv(output/f'{key}_weights.csv',index=False)
        results.append(dict(key=key,label=b['label'],metrics=metrics,weights=weights,config_hash=basket_config_hash(b),nav=nav))
    wide=pd.DataFrame({r['key']:r['nav'].basket for r in results});wide['hs300']=benchmark.set_index('date').close/benchmark.close.iloc[0]*100;wide.to_csv(output/'comparison_nav.csv')
    receipt=dict(start=start,end=end,rows=len(wide),source='longbridge',source_snapshot=str(snapshot),calendar_audit=calendar_audit,excluded=exclusions,price_policy='Longbridge ForwardAdjust close-only projection; local currencies, no FX/costs; not verified total return.',admission_policy='Excluded assets removed before group/asset equal initial weights; then buy and hold. Documented late IPO holds initial capital in cash.',calendar_policy='HS300 dates; last actual HK close carried for valuation, no synthetic source bars.',snapshot_manifest_sha256=hashlib.sha256((snapshot/'manifest.json').read_bytes()).hexdigest(),data_sha256=hashlib.sha256((output/'comparison_nav.csv').read_bytes()).hexdigest(),baskets=[{k:v for k,v in r.items() if k!='nav'} for r in results])
    (output/'comparison_manifest.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    return results,receipt
