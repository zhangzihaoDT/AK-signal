import io
import struct
import zipfile
import numpy as np
import pandas as pd
import pytest
from src.market_facts import tdx_industry as tdx


def test_parser_rejects_corruption_and_duplicate_dates():
    bar = struct.pack('<IIIIIfII', 20261015, 100, 110, 90, 105, 1., 2, 123)
    assert tdx.parse_day(bar).close.iloc[0] == 1.05
    for raw in (bar+b'x', bar+bar):
        with pytest.raises(ValueError):
            tdx.parse_day(raw)


def test_independent_returns_ties_and_insufficient_history(monkeypatch):
    monkeypatch.setattr(tdx, 'universe', lambda: pd.DataFrame({'tdx_code':['a','b','c']}))
    prices = pd.DataFrame([dict(industry_code=code, industry_name=code, parent_industry='p', date=date, close=price)
                          for code, values in [('a',[100,110]),('b',[100,110]),('c',[100,90])]
                          for date, price in zip(pd.date_range('2026-10-14', periods=2),values)])
    result = tdx.calculate(prices, '20261015').set_index('industry_code')
    assert result.loc['a','return_1d'] == pytest.approx(.1)
    assert result.loc['a','RPS1'] == result.loc['b','RPS1'] == 75
    assert result.loc['c','RPS1'] == 0
    assert result.RPS5.isna().all()
    assert result.status_5d.eq('INSUFFICIENT_HISTORY').all()
    with pytest.raises(ValueError):
        tdx.calculate(prices, '20261016')
    with pytest.raises(ValueError):
        tdx.calculate(prices[prices.industry_code != 'a'], '20261015')


def test_online_failure_never_reads_existing_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(tdx, 'root', lambda: tmp_path)
    pd.DataFrame({'old':[1]}).to_parquet(tmp_path/'prices_20261015.parquet')
    def fail(*args):
        raise ConnectionError('offline')
    monkeypatch.setattr(tdx, 'acquire', fail)
    with pytest.raises(ConnectionError):
        tdx.load('20261015', online=True)


def test_ingest_rejects_stale_archive_without_installing(tmp_path, monkeypatch):
    monkeypatch.setattr(tdx, 'root', lambda: tmp_path)
    monkeypatch.setattr(tdx, 'universe', lambda: pd.DataFrame([dict(tdx_code='881001',industry_name='test',parent_industry='parent')]))
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('sh881001.day',struct.pack('<IIIIIfII',20261014,100,110,90,105,1.,2,0))
    archive.seek(0)
    with pytest.raises(ValueError,match='target date'):
        tdx.ingest(archive,'20261015')
    assert not list(tmp_path.glob('*.parquet'))


def test_base_cache_hit_needs_no_network(tmp_path, monkeypatch):
    monkeypatch.setattr(tdx, 'base_dir', lambda: tmp_path/'base')
    monkeypatch.setattr(tdx, 'root', lambda: tmp_path/'facts')
    monkeypatch.setattr(tdx, 'universe', lambda: pd.DataFrame([dict(tdx_code='881001',industry_name='test',parent_industry='parent')]))
    (tmp_path/'base').mkdir()
    raw=struct.pack('<IIIIIfII',20261015,100,110,90,105,1.,2,0)
    (tmp_path/'base/sh881001.day').write_bytes(raw)
    def forbidden(*args):
        raise AssertionError('No request on cache hit')
    monkeypatch.setattr(tdx, 'RemoteArchive', forbidden)
    result=tdx.load('20261015', online=True)
    assert len(result)==1
    assert (tmp_path/'base/sh881001.day').read_bytes()==raw


def test_incremental_appends_and_refuses_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(tdx, 'base_dir', lambda: tmp_path/'base')
    monkeypatch.setattr(tdx, 'root', lambda: tmp_path/'facts')
    monkeypatch.setattr(tdx, 'universe', lambda: pd.DataFrame([dict(tdx_code='881001',industry_name='test',parent_industry='parent')]))
    (tmp_path/'base').mkdir()
    old=struct.pack('<IIIIIfII',20261014,100,110,90,105,1.,2,0)
    new=struct.pack('<IIIIIfII',20261015,105,115,95,110,1.,2,0)
    path=tmp_path/'base/sh881001.day';path.write_bytes(old)
    class Archive(io.BytesIO):
        def __init__(self, _url):
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as z:
                z.writestr('sh881001.day',old+new)
            super().__init__(buffer.getvalue())
            self.etag='test';self.last_modified='test';self.size=len(self.getvalue());self.transferred=self.size
    monkeypatch.setattr(tdx,'RemoteArchive',Archive)
    tdx.acquire('20261015')
    assert path.read_bytes()==old+new
    revised=struct.pack('<IIIIIfII',20261014,100,110,90,104,1.,2,0)
    path.write_bytes(revised)
    with pytest.raises(ValueError,match='historical revision'):
        tdx.acquire('20261015')
    assert path.read_bytes()==revised
