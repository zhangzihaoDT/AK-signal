"""Read-only source probe; never changes production pointers or publications."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from unittest.mock import patch

import akshare as ak
import pandas as pd
import requests

ENDPOINT = 'https://www.csindex.com.cn/csindex-home/perf/index-perf'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    folder = args.output
    folder.mkdir(parents=True, exist_ok=False)
    get = requests.get

    def direct(name, symbol, start, end):
        t = time.monotonic()
        params = dict(indexCode=symbol, startDate=start, endDate=end)
        result = dict(name=name, endpoint=ENDPOINT, params=params)
        try:
            r = get(ENDPOINT, params=params, timeout=25)
            (folder / (name+'.body')).write_bytes(r.content)
            result.update(status_code=r.status_code, content_type=r.headers.get('Content-Type'),
                          sha256=hashlib.sha256(r.content).hexdigest(), bytes=len(r.content))
            j = r.json()
            result['top_keys'] = list(j)
            data = j.get('data')
            if isinstance(data, list):
                result.update(rows=len(data), first=data[0] if data else None, last=data[-1] if data else None)
        except Exception as exc:
            result['error_type'] = type(exc).__name__
        result['elapsed_seconds'] = round(time.monotonic()-t, 2)
        return result

    jobs = [('official_short', 'H30269', '20261020', '20261027'),
            ('official_year', 'H30269', '20251027', '20261027'),
            ('official_full', 'H30269', '20100101', '20261027')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        direct_results = list(pool.map(lambda p: direct(*p), jobs))

    captured = []
    def bounded_get(*args, **kwargs):
        kwargs.setdefault('timeout', 25)
        r = get(*args, **kwargs)
        path = folder / f'akshare_response_{len(captured)}.body'
        path.write_bytes(r.content)
        captured.append(dict(url=r.url, status_code=r.status_code,
                             sha256=hashlib.sha256(r.content).hexdigest(), bytes=len(r.content)))
        return r
    adapter = {'version': ak.__version__, 'function': 'stock_zh_index_hist_csindex'}
    try:
        with patch('requests.get', bounded_get):
            frame = ak.stock_zh_index_hist_csindex(symbol='H30269', start_date='20251027', end_date='20261027')
        frame.to_csv(folder / 'akshare_H30269.csv', index=False)
        adapter.update(rows=len(frame), columns=list(frame.columns),
                       start=str(frame['日期'].min()), end=str(frame['日期'].max()),
                       missing={c:int(frame[c].isna().sum()) for c in frame},
                       names=sorted(set(frame['指数中文全称'].dropna())),
                       symbols=sorted(set(frame['指数代码'].astype(str))))
        direct = json.loads((folder/'official_year.body').read_bytes()).get('data')
        adapter['direct_raw_equals_adapter_raw'] = direct == json.loads((folder/'akshare_response_0.body').read_bytes()).get('data')
    except Exception as exc:
        adapter['error_type'] = type(exc).__name__
    adapter['requests'] = captured

    website = {}
    try:
        r = get('https://www.csindex.com.cn/zh-CN/indices/index-detail/H30269', timeout=25)
        (folder/'official_page.html').write_bytes(r.content)
        website.update(status_code=r.status_code, scripts=re.findall(r'<script[^>]+src=["\']([^"\']+)', r.text))
    except Exception as exc:
        website['error_type'] = type(exc).__name__
    result = dict(generated_at=datetime.now(timezone.utc).isoformat(), direct=direct_results,
                  akshare=adapter, website=website)
    (folder/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
