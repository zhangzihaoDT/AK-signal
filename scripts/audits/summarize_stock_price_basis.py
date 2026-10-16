"""Replay the audit snapshots; classify price-basis evidence, not trading decisions."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import numpy as np
import pandas as pd
from scripts.audits.audit_stock_price_basis import normalized, TOLERANCE


def compare_close(cache,reference,date):
    cache=normalized(cache);reference=normalized(reference)
    target=pd.Timestamp(date)
    cache=cache[cache.date<=target].drop_duplicates('date',keep='last').set_index('date')
    reference=reference[reference.date<=target].drop_duplicates('date',keep='last').set_index('date')
    ref=reference.reindex(cache.index)
    diff=(cache.close-ref.close).abs()
    valid=ref.close.notna()
    mismatch=valid&diff.gt(TOLERANCE)
    result={'common_close_rows':int(valid.sum()),'close_mismatch_rows':int(mismatch.sum()),
            'missing_reference_dates':int((~valid).sum()),'max_close_diff':float(diff.max()),
            'first_close_mismatch':str(cache.index[mismatch].min().date()) if mismatch.any() else None,
            'last_close_mismatch':str(cache.index[mismatch].max().date()) if mismatch.any() else None}
    for w in (60,120,360):
        a=cache.close.tail(w);b=ref.close.tail(w)
        result[f'close_mismatch_rows_{w}']=int(mismatch.tail(w).sum())
        def pos(s):
            return float((s.iloc[-1]-s.min())/(s.max()-s.min())*100) if len(s)==w and s.notna().all() and s.max()>s.min() else None
        x,y=pos(a),pos(b)
        result[f'cache_pos_{w}']=x;result[f'ref_pos_{w}']=y
        result[f'position_delta_{w}']=x-y if x is not None and y is not None else None
    available=all(result[k] is not None for k in ('cache_pos_120','cache_pos_360','ref_pos_120','ref_pos_360'))
    result['long_term_bottom_changed']=(result['cache_pos_120']<=20 and result['cache_pos_360']<=20)!=(result['ref_pos_120']<=20 and result['ref_pos_360']<=20) if available else None
    return result


def summarize(folder):
    folder=Path(folder);original=json.loads((folder/'audit.json').read_text());date=original['trade_date']
    results=[]
    for r in original['findings']:
        p=Path(r['cache_path'])
        unchanged=hashlib.sha256(p.read_bytes()).hexdigest()==r['cache_sha256']
        cache=pd.read_csv(p,parse_dates=['date'])
        comparisons={}
        for source in ('em','sina'):
            for basis in ('qfq','raw'):
                path=folder/'references'/f'{r["market"]}_{r["symbol"]}_{source}_{basis}.parquet'
                if path.exists():comparisons[source+'_'+basis]=compare_close(cache,pd.read_parquet(path),date)
        qfq={k:v for k,v in comparisons.items() if k.endswith('_qfq')}
        out={'market':r['market'],'symbol':r['symbol'],'name':r['name'],'cache_unchanged':unchanged,
             'acquisition_basis':'qfq_requested' if r['market']=='CN' else 'unadjusted_default',
             'ohlc_reconciliation_status':r['status'],'comparisons':comparisons,
             'full_ohlc_match':r['status'] in ('MATCH_CURRENT_QFQ','MATCH_ALTERNATE_QFQ')}
        if not qfq:
            out.update(close_class='UNVERIFIED',best_reference=None)
        else:
            key,best=min(qfq.items(),key=lambda kv:(kv[1]['missing_reference_dates'],kv[1]['close_mismatch_rows'],kv[1]['max_close_diff']))
            n=best['common_close_rows'];bad=best['close_mismatch_rows']
            classification=('INCOMPLETE_REFERENCE' if best['missing_reference_dates'] else
                            'MATCH_QFQ_CLOSE' if bad==0 else
                            'LOCALIZED_CLOSE_DIFFERENCES' if bad<=max(5,int(n*.005)) else 'PERSISTENT_CLOSE_DIFFERENCES')
            out.update(best_reference=key,close_class=classification,**best)
            other={k:v for k,v in comparisons.items() if k.endswith('_raw')}
            out['raw_close_matches']=any(v['close_mismatch_rows']==0 and v['missing_reference_dates']==0 for v in other.values()) if other else None
        results.append(out)
    (folder/'reconciled.json').write_text(json.dumps({'trade_date':date,'price_tolerance':TOLERANCE,
        'localized_rule':'at most max(5, floor(common_rows*0.005)) close differences; classification is descriptive, not a verification pass',
        'findings':results},ensure_ascii=False,indent=2))
    flat=pd.DataFrame([{k:v for k,v in r.items() if k!='comparisons'} for r in results])
    flat.to_csv(folder/'reconciled.csv',index=False)
    counts=flat.close_class.value_counts().to_dict()
    full=int(flat.full_ohlc_match.sum())
    changed=flat[flat.long_term_bottom_changed==True]
    impact=flat[flat.position_delta_360.abs()>=1].sort_values('position_delta_360',key=lambda s:s.abs(),ascending=False)
    table=flat[['market','symbol','name','close_class','best_reference','close_mismatch_rows','position_delta_360','long_term_bottom_changed']]
    md=f'''# 固定观察池股票历史价格复权审计 · {date}

核对 {len(flat)} 只唯一股票（注册身份仍独立保留）。只读取缓存和公开参考历史，不修改行情或观察产物。参考序列是抓取时点的当前供应商基准，不是历史时点的复权快照。

## 结果

- 全历史 OHLC 在一分报价误差内与至少一个当前前复权参考一致：{full} 只。
- 收盘价分类：{json.dumps(counts,ensure_ascii=False)}。
- 360 日位置差绝对值 ≥1 个百分点：{len(impact)} 只。
- 120/360 ≤20 的长期低位判定变化：{len(changed)} 只。
- 全部缓存 SHA256 未变化：{bool(flat.cache_unchanged.all())}。

匹配说明当前缓存与参考序列相容，不能逆向证明历史每次抓取的复权参数。少量局部差异不等于已通过验证；persistent 也不自动证明混合复权，因为供应商前复权算法可能不同。

## 已观察到的工程问题

A 股获取链请求 qfq，但增量窗口按最后报价日之后抓取并直接拼接旧缓存，未保存调整基准、因子或检查历史重标定。两只港股调用 stock_hk_daily 时省略 adjust，而安装版本默认 adjust=""，即不复权；未实现统一的显式复权口径。

## 位置读数影响（按最大差值）

'''+impact[['market','symbol','name','best_reference','position_delta_60','position_delta_120','position_delta_360','long_term_bottom_changed']].to_markdown(index=False)+'''

## 逐只结果

'''+table.to_markdown(index=False)+'''

## 方法与限制

以 (market,symbol,date) 对齐缓存与参考，OHLC 和 close 分别检查，价格差超过 0.010001 才计差异。OHLC 全历史一致性与收盘价窗口位置影响分开报告。位置以缓存最后 60/120/360 个报价日期重索引参考；缺失参考或历史不足不计算，不用交集压缩窗口。局部差异阈值为 max(5, floor(共同报价行数×0.5%))，只用于描述差异范围。

复权审核不等于交易日完整性审核；窗口保留缓存报价日期，此处位置差用于隔离价格口径影响。股票此前的市场日期并集可靠性检查继续独立成立。部分参考前复权与不复权完全一致时，无法靠价格识别模式。没有除权公告或历史因子快照的证据时，不将差异断言为具体公司行为或旧基准拼接。

脚本：scripts/audits/audit_stock_price_basis.py 与 scripts/audits/summarize_stock_price_basis.py。机器证据：audit.json、reconciled.json/CSV、references/*.parquet（哈希在 audit.json）。

建议先统一每个市场的目标复权定义；对 persistent 与局部差异逐只全历史修复，再在抓取时保存 source/adjust/adjustment_anchor 与重标定证据。当前不应把全池 price_basis_verified 改成 true。
'''
    if (folder/'identity_issues.json').exists():
        md+='\n## 标的身份核对补充\n\nHK 02510 被配置为禾赛，但实际为德翔海运；禾赛应为 HK 02525。本审计58条按配置代码核对，该条不能当作禾赛价格验证结果。正确禾赛历史尚未核对；日报正文排除错配条目，原配置与缓存保留供追溯。\n\n依据：[德翔海运中期报告](https://www.hkexnews.hk/listedco/listconews/sehk/2025/0904/2025090400550_c.pdf)、[禾赛港交所公告](https://www.hkexnews.hk/listedco/listconews/sehk/2025/1024/2025102400595_c.pdf)。\n'
    (folder/'report.md').write_text(md)
    print('OHLC matches',full,'close classes',counts,'position impact>=1',len(impact),'bottom changed',len(changed))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('folder');summarize(parser.parse_args().folder)
