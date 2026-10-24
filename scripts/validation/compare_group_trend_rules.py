"""Same audited snapshots, frozen V1 versus V2. In-sample exploratory only."""
import argparse
import csv
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import pandas as pd
from src.market_facts import group_activity as ga
from scripts.validation.check_group_activity import EVENT_RULE, evaluate_group

MILD_EVENT_RULE = {**EVENT_RULE, 'rise': .04, 'retention_gain': .02}
FIXTURE = Path(__file__).parent/'fixtures/stock_group_trend_start_v1.py'


def frozen_v1():
    spec=importlib.util.spec_from_file_location('frozen_stock_group_trend_v1',FIXTURE)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def aggregate(rows):
    rows=[r for r in rows if r['status']=='EVALUATED']
    events=[e for r in rows for e in r['events']]
    signals=[s for r in rows for s in r['signals']]
    mature=[s for s in signals if s['matured_20d']]
    leads=[e['lead_sessions'] for e in events if e['captured']]
    def average(values):return sum(values)/len(values) if values else None
    return dict(alerts=len(signals),event_count=len(events),captured_events=len(leads),
        capture_rate=len(leads)/len(events) if events else None,
        mean_lead_sessions=average(leads),mature_alerts=len(mature),immature_alerts=len(signals)-len(mature),
        false_starts=sum(s['false_start'] for s in mature),false_start_rate=average([s['false_start'] for s in mature]),
        outcomes=dict(Counter(s['price_outcome'] for s in signals)),
        lifecycle_cross=dict(Counter(s['price_outcome']+' / '+s['lifecycle_outcome'] for s in signals)),
        mean_forward_returns={str(n):average([s['forward_returns'][str(n)] for s in signals if s['forward_returns'][str(n)] is not None]) for n in (5,10,20)},
        mean_mae_20d=average([s['mae_20d'] for s in mature]),
        worst_mae_20d=min([s['mae_20d'] for s in mature],default=None))


def controls(row, common_dates=None):
    nav=pd.Series(row['nav_history'],dtype=float)
    candidates=[q['date'] for q in row['history'] if q['status']=='NO_SETUP'
                and not q.get('first_leader_clue') and (common_dates is None or q['date'] in common_dates)]
    rows=[]
    for date in candidates:
        i=nav.index.get_loc(date)
        rows.append(dict(date=date,forward_returns={str(n):float(nav.iloc[i+n]/nav.iloc[i]-1)
            if i+n<len(nav) else None for n in (5,10,20)},
            mae_20d=float(min(0,nav.iloc[i+1:i+21].min()/nav.iloc[i]-1)) if i+20<len(nav) else None))
    def avg(v):return sum(v)/len(v) if v else None
    return dict(key=row['key'],observations=len(rows),rows=rows,
        mean_forward_returns={str(n):avg([r['forward_returns'][str(n)] for r in rows if r['forward_returns'][str(n)] is not None]) for n in (5,10,20)},
        forward_counts={str(n):sum(r['forward_returns'][str(n)] is not None for r in rows) for n in (5,10,20)},
        mean_mae_20d=avg([r['mae_20d'] for r in rows if r['mae_20d'] is not None]))


def compare(facts_v1, facts_v2):
    result=dict(validation_status='IN_SAMPLE_EXPLORATORY_UNVALIDATED',trade_date=facts_v2['trade_date'],
        input_hash=facts_v2['input_hash'],manifest_sha256=facts_v2['manifest_sha256'],
        baseline_fixture_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        config_hash=facts_v2['config_hash'], calendars=facts_v2['calendars'],
        rules={'v1':facts_v1['rules'],'v2':facts_v2['rules']}, evaluations={},
        limitations=ga.LIMITATIONS+['同一历史样本上提出和检查修订规则；非独立样本外验证',
            '价格扩张窗口可多次标记同一长趋势；首WATCH统计抑制重复，故漏报不全是真实遗漏',
            '无提示对照为两版共同NO_SETUP且V2无新个股线索的可观测日；非随机、未风险或市场状态匹配，重叠窗口相关',
            '事件确认与状态失效分属两条轴；失效后仍可能在20日内出现独立价格确认'])
    for name,rule in [('main_8pct',EVENT_RULE),('mild_4pct',MILD_EVENT_RULE)]:
        versions={key:[evaluate_group(r,rule) for r in facts['groups']]
                  for key,facts in [('v1',facts_v1),('v2',facts_v2)]}
        new_events=[];lost_events=[];new_signals=[]
        for a,b in zip(versions['v1'],versions['v2']):
            ea={e['date']:e for e in a.get('events',[])}
            for e in b.get('events',[]):
                if e['captured'] and not ea[e['date']]['captured']:
                    new_events.append(dict(key=b['key'],label=b['label'],**e))
                if not e['captured'] and ea[e['date']]['captured']:
                    lost_events.append(dict(key=b['key'],label=b['label'],**e,old_first_watch=ea[e['date']]['first_watch']))
            old_dates={s['date'] for s in a.get('signals',[])}
            new_signals.extend(dict(key=b['key'],label=b['label'],**s) for s in b.get('signals',[]) if s['date'] not in old_dates)
        result['evaluations'][name]=dict(event_rule=rule,versions=versions,
            aggregate={k:aggregate(v) for k,v in versions.items()},newly_captured_events=new_events,
            lost_captures=lost_events,new_alerts=new_signals,
            new_alerts_without_confirmation=[s for s in new_signals if s['matured_20d'] and not s['confirmed_events']],
            new_alerts_immature=[s for s in new_signals if not s['matured_20d']])
    common=[]
    for a,b in zip(facts_v1['groups'],facts_v2['groups']):
        d={q['date'] for q in a.get('history',[]) if q['status']=='NO_SETUP'}
        common.append(controls(b,d))
    result['no_alert_controls']=common
    result['member_only_notices']=[dict(key=g['key'],date=q['date'],clues=q['leader_clues'])
        for g in facts_v2['groups'] for q in g.get('history',[])
        if q['status']=='NO_SETUP' and q.get('first_leader_clue')]
    return result


def run(trade_date,metrics_dir,output_dir,reference_v1=None):
    output_dir.mkdir(parents=True,exist_ok=True)
    stocks=pd.read_parquet(metrics_dir/f'stock_metrics_{trade_date}.parquet')
    v2=ga.build_from_daily(trade_date,stocks,metrics_dir)
    ga.validate_document(v2,trade_date,stocks)
    module=frozen_v1()
    manifest_path=Path(v2['snapshot_manifest']);manifest=json.loads(manifest_path.read_text())
    calendars={m:ga.SessionCalendar.load(ga.project_root()/f'config/market_calendars/{c}.json') for m,c in [('CN','XSHG'),('HK','XHKG')]}
    selected={k for row in v2['groups'] for k in row['snapshot_ids']}|{'CN:_benchmark'}
    closes={}
    for record in manifest['records']:
        key=ga.identity(record['market'],record['symbol'])
        if ':'.join(key) in selected and 'file' in record:
            snapshot=ga.validate_snapshot(json.loads((manifest_path.parent/record['file']).read_text()))
            closes[key]=ga._close(snapshot,calendars[key[0]],pd.Timestamp(trade_date),key,key[1]=='_benchmark')
    baseline=[]
    old_reference=json.loads(reference_v1.read_text()) if reference_v1 else None
    if old_reference:
        assert old_reference['input_hash']==v2['input_hash']
        assert old_reference['manifest_sha256']==v2['manifest_sha256']
        assert old_reference['rule_version']==module.RULE_VERSION
        assert old_reference['rules']==module.RULES
    for definition,current in zip(ga.definitions(),v2['groups']):
        history,nav,anchor=ga.group_history(definition,closes,closes[('CN','_benchmark')],calendars,module.reconstruct_states)
        assert current['nav_history']=={d.date().isoformat():float(v) for d,v in nav.basket.items()}
        row=dict(key=current['key'],label=current['label'],structural_anchor=anchor.date().isoformat(),
                 nav_history=current['nav_history'],history=history,**history[-1])
        if old_reference:
            original=next(r for r in old_reference['groups'] if r['key']==row['key'])
            assert original['history']==history,'Frozen V1 differs from retained V1 acceptance artifact'
            assert original['nav_history']==row['nav_history']
        baseline.append(row)
    v1=dict(v2,rule_version=module.RULE_VERSION,rules=module.RULES,groups=baseline)
    result=compare(v1,v2)
    result['baseline_reference_exact_match']=bool(old_reference)
    result['comparison_code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result['event_evaluation_code_sha256']=hashlib.sha256((Path(__file__).parent/'check_group_activity.py').read_bytes()).hexdigest()
    result['v2_source_sha256']=hashlib.sha256(Path(ga.__file__).read_bytes()).hexdigest()
    for filename,value in [('v1_facts.json',v1),('v2_facts.json',v2),('comparison.json',result)]:
        (output_dir/filename).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))
    new_alerts=result['evaluations']['main_8pct']['new_alerts']
    fields=['key','label','date','path','matured_20d','price_outcome','lifecycle_outcome','lifecycle_date','confirmed_events','return_5d','return_10d','return_20d','mae_20d']
    with (output_dir/'new_alert_outcomes.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for alert in new_alerts:
            writer.writerow({**{k:alert.get(k) for k in fields if k not in ('return_5d','return_10d','return_20d')},
                             **{f'return_{n}d':alert['forward_returns'][str(n)] for n in (5,10,20)}})
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--date',required=True)
    p.add_argument('--metrics-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--reference-v1',type=Path)
    a=p.parse_args()
    result=run(a.date,a.metrics_dir,a.output_dir,a.reference_v1)
    print(json.dumps({k:v['aggregate'] for k,v in result['evaluations'].items()},ensure_ascii=False,indent=2))
