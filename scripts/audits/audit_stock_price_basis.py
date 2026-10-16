"""Read-only cache reconciliation against fetched vendor price bases.

Run with the project venv. --fetch downloads public references only; no raw
cache is updated. Without --fetch this replays locally saved evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import numpy as np
import pandas as pd
from src.common.paths import raw_dir, selection_universe_path
from src.selection.universe import load_universe_items
from src.trend_engine.inputs import stock_items
from src.trend_engine.engine import _raw_cache_path, _load_cached_raw

TOLERANCE = 0.010001  # One quoted price cent; retain exact mismatch counts too.
FIELDS = ['open','high','low','close']


def normalized(df):
    df=df.rename(columns={'日期':'date','开盘':'open','最高':'high','最低':'low','收盘':'close'}).copy()
    df['date']=pd.to_datetime(df.date).dt.normalize()
    for c in FIELDS:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    return df[['date',*FIELDS]].sort_values('date')


def reconcile(cache,reference,date):
    a=normalized(cache);b=normalized(reference)
    target=pd.Timestamp(date)
    a=a[a.date<=target];b=b[b.date<=target]
    duplicated=int(a.date.duplicated().sum())
    a=a.drop_duplicates('date',keep='last');b=b.drop_duplicates('date',keep='last')
    joint=a.merge(b,on='date',suffixes=('_cache','_ref'),validate='one_to_one')
    missing=len(set(a.date)-set(b.date))
    diff=pd.DataFrame({c:(joint[c+'_cache']-joint[c+'_ref']).abs() for c in FIELDS})
    bad=diff.gt(TOLERANCE).any(axis=1)|diff.isna().any(axis=1)
    result={'cache_rows':len(a),'reference_rows':len(b),'common_rows':len(joint),
            'cache_duplicates':duplicated,'missing_reference_dates':missing,
            'cache_first':str(a.date.min().date()),'cache_last':str(a.date.max().date()),
            'mismatch_rows':int(bad.sum()),'max_ohlc_abs_diff':float(diff.max().max()) if len(joint) else None,
            'status':'MATCH_CURRENT_QFQ' if len(joint)>0 and not bad.any() and not missing and not duplicated and a.date.max()==target else 'MISMATCH_OR_INCOMPLETE'}
    samples=joint.loc[bad,['date','close_cache','close_ref']].copy()
    samples['date']=samples.date.dt.strftime('%Y-%m-%d')
    result['samples']=samples.head(5).to_dict('records')
    if len(joint):
        result['first_mismatch']=str(joint.loc[bad,'date'].min().date()) if bad.any() else None
        result['last_mismatch']=str(joint.loc[bad,'date'].max().date()) if bad.any() else None
    for w in (60,120,360):
        win=joint.tail(w)
        if len(win)<w:
            result[f'position_delta_{w}']=None;continue
        def pos(s):
            return float((s.iloc[-1]-s.min())/(s.max()-s.min())*100) if s.max()>s.min() else None
        x,y=pos(win.close_cache),pos(win.close_ref)
        result[f'position_delta_{w}']=x-y if x is not None and y is not None else None
        result[f'mismatch_rows_{w}']=int(bad.tail(w).sum())
    return result


def fetch(market,symbol,basis,source,start,date):
    import akshare as ak
    if market=='HK':
        return normalized(ak.stock_hk_daily(symbol=symbol.zfill(5),adjust=basis))
    if source=='em':
        return normalized(ak.stock_zh_a_hist(symbol=symbol,period='daily',start_date=start,end_date=date,adjust=basis,timeout=12))
    prefix='sh' if symbol.startswith(('6','9')) else 'bj' if symbol.startswith(('4','8')) else 'sz'
    return normalized(ak.stock_zh_a_daily(symbol=prefix+symbol,start_date=start,end_date=date,adjust=basis))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',default='20261012')
    parser.add_argument('--out',required=True)
    parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args()
    folder=Path(args.out);folder.mkdir(parents=True,exist_ok=True)
    evidence=folder/'references';evidence.mkdir(exist_ok=True)
    if args.fetch:
        import requests
        original=requests.sessions.Session.request
        def bounded(self,*a,**kw):
            kw.setdefault('timeout',12);return original(self,*a,**kw)
        requests.sessions.Session.request=bounded
    items={(i.asset.market,i.asset.symbol):i for i in stock_items(load_universe_items(selection_universe_path()))}
    findings=[];events=json.loads((folder/'audit.json').read_text()).get('fetch_events',[]) if (folder/'audit.json').exists() else []
    for (market,symbol),item in sorted(items.items()):
        path=_raw_cache_path(raw_dir(),item.asset)
        cached=_load_cached_raw(path)
        cache_hash=hashlib.sha256(path.read_bytes()).hexdigest()
        cached=cached[pd.to_datetime(cached.date)<=pd.Timestamp(args.date)]
        start=pd.Timestamp(cached.date.min()).strftime('%Y%m%d')
        result={'market':market,'symbol':symbol,'name':item.asset.name,'cache_path':str(path),
                'cache_sha256':cache_hash,'provider_requested_basis':'qfq' if market=='CN' else 'unadjusted(default)',
                'checked_at':datetime.now(timezone.utc).isoformat()}
        variants=[('em' if market=='CN' else 'sina','qfq')]
        checked={}
        def reference(source,basis):
            ref_path=evidence/f'{market}_{symbol}_{source}_{basis or "raw"}.parquet'
            if ref_path.exists():
                return pd.read_parquet(ref_path)
            if not args.fetch:
                return None
            try:
                df=fetch(market,symbol,basis,source,start,args.date)
                df=df[(df.date>=pd.Timestamp(start))&(df.date<=pd.Timestamp(args.date))]
                if df.empty:raise ValueError('empty reference')
                df.to_parquet(ref_path,index=False)
                events.append({'market':market,'symbol':symbol,'source':source,'basis':basis,'ok':True,'rows':len(df)})
                return df
            except Exception as exc:
                events.append({'market':market,'symbol':symbol,'source':source,'basis':basis,'ok':False,'error':f'{type(exc).__name__}: {str(exc)[:200]}'})
                return None
            finally:
                time.sleep(.35)
        source,basis=variants[0]
        ref=reference(source,basis)
        if ref is None and market=='CN':
            source='sina';ref=reference(source,basis)
        result['reference_source']=source
        if ref is None:
            result['status']='REFERENCE_UNAVAILABLE'
        else:
            checked[source+'_qfq']=reconcile(cached,ref,args.date)
            result.update(checked[source+'_qfq'])
            if result['status']!='MATCH_CURRENT_QFQ' or market=='HK':
                if market=='CN' and source!='sina':
                    other=reference('sina','qfq')
                    if other is not None:
                        checked['sina_qfq']=reconcile(cached,other,args.date)
                raw=reference(source,'')
                if raw is not None:
                    checked[source+'_raw']=reconcile(cached,raw,args.date)
                if result['status']!='MATCH_CURRENT_QFQ' and any(v['status']=='MATCH_CURRENT_QFQ' for k,v in checked.items() if k.endswith('_qfq')):
                    result['status']='MATCH_ALTERNATE_QFQ'
                elif result['status']!='MATCH_CURRENT_QFQ' and any(v['status']=='MATCH_CURRENT_QFQ' for k,v in checked.items() if k.endswith('_raw')):
                    result['status']='MATCH_UNADJUSTED_NOT_QFQ'
            result['comparisons']=checked
        # Preserve full records and reference hashes after every stock for resume.
        result['reference_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in evidence.glob(f'{market}_{symbol}_*.parquet')}
        result['reference_saved_at']={p.name:datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat() for p in evidence.glob(f'{market}_{symbol}_*.parquet')}
        findings.append(result)
        (folder/'audit.json').write_text(json.dumps({'trade_date':args.date,'verification':'current_vendor_basis_as_of_fetch_time_not_historical_PIT',
            'price_tolerance':TOLERANCE,'findings':findings,'fetch_events':events},ensure_ascii=False,indent=2))
        print(f'{len(findings)}/{len(items)} {market} {symbol} {item.asset.name}: {result["status"]} mismatch={result.get("mismatch_rows","—")}',flush=True)
    flat=pd.DataFrame([{k:v for k,v in r.items() if k not in ('comparisons','samples','reference_hashes')} for r in findings])
    flat.to_csv(folder/'audit.csv',index=False)
    counts=flat.status.value_counts().to_dict();print(counts,flush=True)
    (folder/'summary.json').write_text(json.dumps({'counts':counts,'total':len(findings),'trade_date':args.date},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
