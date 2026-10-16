"""Official TDX industry prices and independent fixed-universe return/RPS facts."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile

import numpy as np
import pandas as pd
import requests

from src.common.paths import project_root

URL = 'https://www.tdx.com.cn/products/data/data/vipdoc/tdxzs_day.zip'
WINDOWS = (1, 5, 10, 15, 20, 60, 120, 250)


def universe():
    frame = pd.read_csv(project_root()/'config/tdx_research_industry_20261016.csv', dtype=str)
    if len(frame) != 128 or frame.tdx_code.duplicated().any():
        raise ValueError('TDX universe must contain 128 unique industries')
    return frame


def root():
    return project_root()/'data/sources/tdx/processed'


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def parse_day(raw):
    if not raw or len(raw) % 32:
        raise ValueError('Invalid .day record length')
    frame = pd.DataFrame(struct.iter_unpack('<IIIIIfII', raw), columns=['date','open','high','low','close','amount','volume','reserved'])
    frame['date'] = pd.to_datetime(frame.date.astype(str), format='%Y%m%d', errors='raise')
    if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise ValueError('Duplicate or unordered source dates')
    if not ((frame.low > 0) & (frame.low <= frame[['open','close']].min(axis=1)) & (frame.high >= frame[['open','close']].max(axis=1)) & np.isfinite(frame.amount) & (frame.amount >= 0)).all():
        raise ValueError('Invalid OHLC or amount')
    frame[['open','high','low','close']] = frame[['open','high','low','close']] / 100
    return frame.drop(columns='reserved')


def ingest(archive, target, source=URL):
    """Validate entire fixed universe before atomically installing one dated snapshot."""
    frames = []
    target = pd.Timestamp(target)
    with zipfile.ZipFile(archive) as z:
        members = {}
        for name in z.namelist():
            base = name.replace('\\','/').split('/')[-1]
            if base in members:
                raise ValueError('Duplicate archive basename')
            members[base] = name
        for row in universe().itertuples():
            frame = parse_day(z.read(members['sh'+row.tdx_code+'.day']))
            frame = frame[frame.date <= target].copy()
            if frame.empty or frame.date.max() != target:
                raise ValueError(f'{row.tdx_code}: target date {target.date()} absent')
            frame['industry_code'] = row.tdx_code
            frame['industry_name'] = row.industry_name
            frame['parent_industry'] = row.parent_industry
            frames.append(frame)
    prices = pd.concat(frames, ignore_index=True)
    prices['source'] = 'tdx_official_day'
    prices['source_url'] = source
    prices['price_basis'] = 'TDX unadjusted industry index'
    prices['classification'] = 'TDX Research Industry'
    prices['classification_version'] = '20261016'
    prices['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    payload = io.BytesIO()
    prices.to_parquet(payload, index=False)
    atomic_bytes(root()/f'prices_{target:%Y%m%d}.parquet', payload.getvalue())
    return prices


class RemoteArchive(io.RawIOBase):
    """Seekable bounded HTTP Range reader, pinned to one archive version."""
    def __init__(self, url):
        self.session = requests.Session()
        self.url = url
        response = self.session.get(url, headers={'Range':'bytes=0-0', 'Accept-Encoding':'identity'}, timeout=(15,60))
        response.raise_for_status()
        if response.status_code != 206 or len(response.content) != 1:
            self.session.close()
            raise ValueError('Official server must support HTTP Range')
        self.size = int(response.headers['Content-Range'].split('/')[-1])
        self.etag = response.headers.get('ETag')
        self.last_modified = response.headers.get('Last-Modified')
        if not self.etag or not 0 < self.size <= 1024**3:
            self.session.close()
            raise ValueError('Missing archive version or invalid size')
        self.position = 0
        self.cache = {}
        self.transferred = 1

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        position = offset if whence == 0 else self.position + offset if whence == 1 else self.size + offset
        if position < 0:
            raise ValueError('Negative archive offset')
        self.position = position
        return position

    def read(self, size=-1):
        end = self.size if size < 0 else min(self.size, self.position+size)
        chunks = []
        while self.position < end:
            start = self.position // (256*1024) * (256*1024)
            stop = min(start+256*1024, self.size)-1
            if start not in self.cache:
                response = self.session.get(self.url, headers={'Range':f'bytes={start}-{stop}', 'If-Match':self.etag, 'Accept-Encoding':'identity'}, timeout=(15,60))
                response.raise_for_status()
                if (response.status_code != 206 or response.headers.get('ETag') != self.etag
                    or response.headers.get('Content-Range') != f'bytes {start}-{stop}/{self.size}'
                    or len(response.content) != stop-start+1):
                    raise ValueError('Archive changed or invalid partial response')
                self.cache[start] = response.content
                self.transferred += len(response.content)
            take = min(end, stop+1)-self.position
            chunks.append(self.cache[start][self.position-start:self.position-start+take])
            self.position += take
        return b''.join(chunks)

    def close(self):
        self.session.close()
        super().close()


def base_dir():
    return project_root()/'data/sources/tdx/raw/day'


def local_archive(target):
    """Pack only selected local files for the shared strict parser; never changes history."""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        for code in universe().tdx_code:
            path = base_dir()/f'sh{code}.day'
            z.writestr(path.name, path.read_bytes())
    archive.seek(0)
    return ingest(archive, target, source='local official .day history base')


def acquire(target):
    """Cache hit needs no request. Missing bars append from a version-pinned official ZIP."""
    target = pd.Timestamp(target)
    codes = list(universe().tdx_code)
    pending = []
    for code in codes:
        path = base_dir()/f'sh{code}.day'
        if not path.exists() or target not in set(parse_day(path.read_bytes()).date):
            pending.append(code)
    audit = dict(source_url=URL, trade_date=str(target.date()), fetched_at=pd.Timestamp.now(tz='UTC').isoformat(), requested_codes=pending, cache_hits=len(codes)-len(pending))
    if pending:
        updates = []
        with RemoteArchive(URL) as remote, zipfile.ZipFile(remote) as z:
            members = {n.replace('\\','/').split('/')[-1]:n for n in z.namelist()}
            for code in pending:
                filename = f'sh{code}.day'
                raw = z.read(members[filename])  # zipfile verifies selected member CRC
                fresh = parse_day(raw)
                if target not in set(fresh.date):
                    raise ValueError(f'{code}: official package missing {target.date()}')
                path = base_dir()/filename
                existing = path.read_bytes() if path.exists() else b''
                if existing:
                    old = parse_day(existing)
                    overlap = old.merge(fresh, on='date', suffixes=('_old','_new'))
                    for field in ('open','high','low','close','amount','volume'):
                        if not overlap[field+'_old'].eq(overlap[field+'_new']).all():
                            raise ValueError(f'{code}: historical revision requires explicit review')
                    if not set(old.date).issubset(set(fresh.date)):
                        raise ValueError(f'{code}: official archive lost existing history')
                    addition = b''.join(raw[i*32:(i+1)*32] for i, date in enumerate(fresh.date) if date > old.date.max())
                    merged = existing + addition
                else:
                    merged = raw
                updates.append((path, merged))
            # Validate all pending files first. Publication remains all-or-nothing.
            for path, merged in updates:
                atomic_bytes(path, merged)
            audit.update(etag=remote.etag, last_modified=remote.last_modified, archive_bytes=remote.size, transferred_bytes=remote.transferred)
    prices = local_archive(target)
    audit['file_sha256'] = {code: hashlib.sha256((base_dir()/f'sh{code}.day').read_bytes()).hexdigest() for code in codes}
    atomic_bytes(root().parent/'audits'/f'acquisition_{target:%Y%m%d}.json', (json.dumps(audit, indent=2)+'\n').encode())
    return prices


def calculate(prices, target):
    target = pd.Timestamp(target)
    expected = set(universe().tdx_code)
    if set(prices.industry_code) != expected:
        raise ValueError('Incomplete TDX universe')
    rows = []
    for code, frame in prices.groupby('industry_code'):
        frame = frame.sort_values('date')
        frame = frame[pd.to_datetime(frame.date) <= target]
        if frame.empty or pd.Timestamp(frame.iloc[-1].date) != target or frame.date.duplicated().any():
            raise ValueError('Source date mismatch or duplicate')
        row = frame.iloc[-1].to_dict()
        row.update(trade_date=target, source_trade_date=target, source_status='primary', data_status='confirmed', history_bars=len(frame))
        for window in WINDOWS:
            enough = len(frame) > window
            row[f'return_{window}d'] = float(frame.iloc[-1].close/frame.iloc[-window-1].close-1) if enough else np.nan
            row[f'status_{window}d'] = 'OK' if enough else 'INSUFFICIENT_HISTORY'
        rows.append(row)
    result = pd.DataFrame(rows)
    for window in WINDOWS:
        values = result[f'return_{window}d']
        count = int(values.notna().sum())
        # Percent rank, average ties. Singleton has no meaningful relative rank.
        result[f'RPS{window}'] = (values.rank(method='average')-1)/(count-1)*100 if count > 1 else np.nan
        result[f'valid_count_{window}d'] = count
    return result


def load(target, online=False):
    prices = acquire(target) if online else local_archive(target)
    result = calculate(prices, target)
    atomic_bytes(root()/f'metrics_{pd.Timestamp(target):%Y%m%d}.csv', result.to_csv(index=False).encode())
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    prices = ingest(args.archive, args.date, source='local official package import') if args.archive else acquire(args.date)
    print(calculate(prices, args.date)[['industry_code','industry_name','RPS5','RPS15']].to_string(index=False))
