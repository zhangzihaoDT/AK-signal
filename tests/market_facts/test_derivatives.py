from copy import deepcopy
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from src.market_facts import derivatives as m
from src.observation.derivatives import panel, markdown

ROOT = Path(__file__).resolve().parents[2]
DATE = '20261027'
HEADER = ['合约代码','今开盘','最高价','最低价','成交量','成交金额','持仓量','持仓变化','今收盘','今结算','前结算','涨跌1','涨跌2','Delta']


@pytest.fixture
def root(tmp_path):
    (tmp_path/'config/market_calendars').mkdir(parents=True)
    for name in ('derivatives.yaml','observation_sources.yaml','market_calendars/XSHG.json'):
        shutil.copyfile(ROOT/'config'/name,tmp_path/'config'/name)
    return tmp_path


def indices():
    return {'rows':[{'key':'csi500','code':'000905','provider':'longbridge','status':'AVAILABLE',
                     'source_trade_date':'2026-10-27','close':100.,'snapshot_id':'a'*64}]}


def csv_bytes(prior=False, mutate=None):
    offset = 0 if prior else 1
    rows = []
    for i, contract in enumerate(('IC2611','IC2612','IC2703','IC2706')):
        rows.append([contract,100+i+offset,104+i+offset,99+i+offset,1000+i,'1234',
                     10000+i*100+offset*10,10,102+i+offset,101+i+offset,100+i+offset,0,0,'--'])
    if mutate:
        mutate(rows)
    stream = StringIO();w=csv.writer(stream);w.writerow(HEADER);w.writerows(rows)
    return stream.getvalue().encode('gb18030')


def acquire(root, monkeypatch, mutation=None):
    def get(url, timeout):
        assert timeout == 25
        assert url in (m.source_url('20261026'),m.source_url(DATE))
        raw = csv_bytes(prior='20261026' in url, mutate=mutation if DATE in url else None)
        return SimpleNamespace(content=raw,status_code=200,raise_for_status=lambda:None)
    monkeypatch.setattr(m.requests,'get',get)
    return m.build(DATE,indices(),online=True,root=root)


def test_concrete_contract_prices_basis_and_same_contract_returns(root,monkeypatch):
    d=acquire(root,monkeypatch)
    assert d['module']['status']=='available' and d['module']['available_contract_count']==4
    assert [r['contract'] for r in d['rows']]==['IC2611','IC2612','IC2703','IC2706']
    r=d['rows'][0]
    assert r['return_1d']==pytest.approx(103/102-1)
    assert r['return_1d'] != pytest.approx(r['close_vs_pre_settle'])
    assert r['basis_points']==3 and r['basis_rate']==pytest.approx(.03)
    assert r['close'] != r['settle']
    assert r['source_trade_date']=='2026-10-27' and r['previous_source_trade_date']=='2026-10-26'
    assert sum(r['is_main_open_interest'] for r in d['rows'])==1
    assert d['rows'][-1]['is_main_open_interest'] is True
    assert all(r['series_kind']=='listed_contracts' for r in d['rows'])
    m.validate_document(d,DATE,indices(),root)
    monkeypatch.setattr(m.requests,'get',lambda *_a,**_kw:pytest.fail('offline network'))
    assert m.build(DATE,indices(),root=root)['rows']==d['rows']
    html=panel(d)
    assert html.count('data-contract=')==4 and '结算点位' in html and '不是保证金收益' in html
    assert all(r['contract'] in markdown(d) for r in d['rows'])


@pytest.mark.parametrize('mutation',[
    lambda rows: rows.pop(),
    lambda rows: rows.append(list(rows[0])),
    lambda rows: rows[0].__setitem__(0,'IC0'),
    lambda rows: rows[0].__setitem__(0,'IC2699'),
    lambda rows: rows[0].__setitem__(0,'IC2609'),
    lambda rows: rows[0].__setitem__(2,1),
    lambda rows: rows[0].__setitem__(4,-1),
    lambda rows: rows[0].__setitem__(6,1.5),
    lambda rows: rows[0].__setitem__(8,float('nan')),
])
def test_invalid_or_partial_daily_cross_section_is_rejected(root,mutation):
    item,_=m.definitions(root)
    with pytest.raises(ValueError):m.parse_daily(csv_bytes(mutate=mutation),DATE,item)


def test_duplicate_columns_and_wrong_schema_rejected(root):
    item,_=m.definitions(root)
    raw=csv_bytes().decode('gb18030').replace('今开盘','今收盘',1).encode('gb18030')
    with pytest.raises(ValueError,match='duplicate'):m.parse_daily(raw,DATE,item)
    with pytest.raises(ValueError,match='schema'):m.parse_daily(b'code,close\nIC2611,10\n',DATE,item)


def test_bad_settlement_or_oi_delta_marks_unavailable(root,monkeypatch):
    for column,reason in ((10,'INCONSISTENT_PRE_SETTLE'),(7,'INCONSISTENT_OPEN_INTEREST_CHANGE')):
        d=acquire(root,monkeypatch,lambda rows:rows[0].__setitem__(column,999))
        assert d['module']['status']=='unavailable' and d['rows'][0]['reason']==reason
        assert d['rows'][0]['close'] is None


def test_stale_spot_does_not_generate_basis(root,monkeypatch):
    acquire(root,monkeypatch)
    stale=indices();stale['rows'][0]['source_trade_date']='2026-10-26'
    d=m.build(DATE,stale,root=root)
    assert d['module']['status']=='available'
    assert all(r['basis_points'] is None and r['basis_status']=='SPOT_UNAVAILABLE' for r in d['rows'])
    m.validate_document(d,DATE,stale,root)


def test_new_contract_does_not_use_a_different_contracts_previous_close(root,monkeypatch):
    def get(url,timeout):
        prior='20261026' in url
        raw=csv_bytes(prior=prior,mutate=(lambda rows:rows[-1].__setitem__(0,'IC2610')) if prior else None)
        return SimpleNamespace(content=raw,status_code=200,raise_for_status=lambda:None)
    monkeypatch.setattr(m.requests,'get',get)
    d=m.build(DATE,indices(),online=True,root=root)
    new=d['rows'][-1]
    assert new['contract']=='IC2706' and new['return_1d'] is None
    assert new['return_status']=='INSUFFICIENT_HISTORY' and new['previous_close'] is None
    assert new['close'] is not None and d['module']['status']=='available'


def test_http_failure_and_missing_batch_do_not_fallback(root,monkeypatch):
    d=m.build(DATE,indices(),root=root)
    assert d['rows'][0]['reason']=='MISSING_TARGET_BATCH'
    def fail(*_a,**_kw):raise m.requests.ConnectionError('source unavailable')
    monkeypatch.setattr(m.requests,'get',fail)
    d=m.build(DATE,indices(),online=True,root=root)
    assert d['rows'][0]['reason']=='FETCH_FAILED'
    assert 'UNAVAILABLE' in panel(d) and '官方日行情获取失败' in panel(d)
    m.validate_document(d,DATE,indices(),root)


def test_raw_tampering_and_spot_mismatch_rejected(root,monkeypatch):
    d=acquire(root,monkeypatch)
    changed=deepcopy(d);changed['rows'][0]['return_1d']=0
    with pytest.raises(ValueError,match='differ'):m.validate_document(changed,DATE,indices(),root)
    changed_spot=indices();changed_spot['rows'][0]['close']=101
    with pytest.raises(ValueError,match='differ'):m.validate_document(d,DATE,changed_spot,root)
    path=Path(d['input_receipt']['snapshot_manifest']).parent/(DATE+'_1.csv')
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='raw file hash'):m.validate_document(d,DATE,indices(),root)


def test_wrong_manifest_date_and_intraday_capture_rejected(root,monkeypatch):
    d=acquire(root,monkeypatch)
    with pytest.raises(ValueError,match='date/registry'):m.validate_document(d,'20261026',indices(),root)
    path=Path(d['input_receipt']['snapshot_manifest']);manifest=json.loads(path.read_bytes())
    manifest['generated_at']='2026-10-27T03:00:00+00:00';path.write_text(json.dumps(manifest))
    receipt={**d['input_receipt'],'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    item,sig=m.definitions(root)
    rows=m.consume(root,DATE,item,sig,receipt,m.spot_reference(indices(),item,DATE))
    assert rows[0]['reason']=='SESSION_INCOMPLETE' and rows[0]['close'] is None


# --- historical backfill -------------------------------------------------

def _fake_backfill_source(monkeypatch, days, fail=()):
    """Serve dated CFFEX files for `days` only; anything else is a 404.

    Levels step by session index so pre_settle[t] == settle[t-1] holds, exactly
    like the real exchange files, and open interest stays chained.
    """
    def get(url, timeout):
        assert timeout == 25
        day = url.split('/')[-1].removesuffix('_1.csv')
        if day not in days or day in fail:
            raise m.requests.HTTPError('404 Not Found')
        step = sorted(days).index(day)
        rows = []
        for i, contract in enumerate(('IC2611', 'IC2612', 'IC2703', 'IC2706')):
            base = 100 + i + step
            rows.append([contract, base, base + 4, base - 1, 1000 + i, '1234',
                         10000 + i * 100 + step * 10, 10, base + 2, base + 1, base, 0, 0, '--'])
        stream = StringIO(); w = csv.writer(stream); w.writerow(HEADER); w.writerows(rows)
        return SimpleNamespace(content=stream.getvalue().encode('gb18030'), status_code=200,
                               raise_for_status=lambda: None)
    monkeypatch.setattr(m.requests, 'get', get)


def test_backfill_refuses_sessions_before_ic_listed(root, monkeypatch):
    # IC listed 2015-04-16; earlier exchange files carry no IC rows at all.
    assert m.PRODUCT_LISTED_DATE == '20150416'
    for start, end in (('20150101', '20150415'), ('20261029', '20261027')):
        with pytest.raises(ValueError, match='no listed-product session'):
            m.backfill_sessions(root, start, end)
    # A range straddling the listing date is clamped forward, never widened back.
    monkeypatch.setattr(m, 'PRODUCT_LISTED_DATE', '20150101')
    _, clamped = m.backfill_sessions(root, '20150101', '20150417')
    monkeypatch.undo()
    _, listed = m.backfill_sessions(root, '20150101', '20150417')
    assert listed[0] == '20150416' and all(d >= '20150416' for d in listed)
    assert clamped[0] == '20150105' and '20150416' in listed


def test_backfill_is_idempotent_and_resumable(root, monkeypatch):
    _fake_backfill_source(monkeypatch, {'20261027', '20261028', '20261029'})
    first = m.backfill(root, '20261027', '20261029')
    assert first['created'] == ['20261027', '20261028', '20261029']
    assert first['failed_count'] == 0
    # Re-running never refetches or rewrites an existing partition.
    monkeypatch.setattr(m.requests, 'get',
                        lambda *_a, **_k: pytest.fail('must not refetch backfilled sessions'))
    again = m.backfill(root, '20261027', '20261029')
    assert again['created'] == [] and len(again['already_present']) == 3


def test_backfill_skips_unavailable_days_without_faking_facts(root, monkeypatch):
    _fake_backfill_source(monkeypatch, {'20261027', '20261029'}, fail={'20261028'})
    report = m.backfill(root, '20261027', '20261029')
    assert report['created'] == ['20261027', '20261029']
    assert [f['date'] for f in report['failed']] == ['20261028']
    # A failed day registers no partition, so it cannot look like available facts.
    assert not (root/'data/sources/cffex/raw/daily/partitions/20261028.json').exists()
    assert (root/'data/sources/cffex/raw/daily/partitions/20261027.json').exists()


def test_backfilled_day_uses_prior_session_own_archive(root, monkeypatch):
    _fake_backfill_source(monkeypatch, {'20261026', '20261027'})
    m.backfill(root, '20261026', '20261027')
    item, sig = m.definitions(root)
    receipt = json.loads((root/'data/sources/cffex/raw/daily/partitions/20261027.json').read_text())
    spot = m.spot_reference(indices(), item, '20261027')
    rows = m.consume(root, '20261027', item, sig, receipt, spot)
    assert rows[0]['status'] == 'AVAILABLE'
    # Prior close is sourced from 20261026's own archived file, not a same-batch fetch,
    # and backfill never claims a previous_date it did not itself fetch.
    assert rows[0]['previous_session_source'] == 'previous_session_archive'
    assert rows[0]['previous_source_url'] == m.source_url('20261026')
    assert rows[0]['previous_source_trade_date'] == '20261026'
    # The prior session is one step lower in the fake ladder.
    assert rows[0]['previous_close'] == pytest.approx(rows[0]['close'] - 1)
    # Pre-settle chains, so the day verifies instead of degrading.
    assert rows[0]['reason'] == '' and rows[0]['return_status'] == 'OK'
    manifest = json.loads(Path(receipt['snapshot_manifest']).read_text())
    assert manifest['acquisition'] == 'historical_backfill'
    assert [r['source_trade_date'] for r in manifest['records']] == ['20261027']


def test_backfilled_day_without_prior_archive_degrades_to_unknown(root, monkeypatch):
    _fake_backfill_source(monkeypatch, {'20261027'})
    m.backfill(root, '20261027', '20261027')
    item, sig = m.definitions(root)
    receipt = json.loads((root/'data/sources/cffex/raw/daily/partitions/20261027.json').read_text())
    spot = m.spot_reference(indices(), item, '20261027')
    rows = m.consume(root, '20261027', item, sig, receipt, spot)
    assert rows[0]['reason'] == 'MISSING_PRIOR_SESSION'
    assert rows[0]['close'] is None and rows[0]['status'] == 'UNAVAILABLE'


def test_backfilled_materialised_history_matches_chain_semantics(root, monkeypatch, tmp_path):
    """Backfilled history and the live chain must agree on every level fact."""
    _fake_backfill_source(monkeypatch, {'20261026', '20261027'})
    m.backfill(root, '20261026', '20261027')
    item, sig = m.definitions(root)
    spot = m.spot_reference(indices(), item, '20261027')
    back = m.consume(root, '20261027', item, sig,
                     json.loads((root/'data/sources/cffex/raw/daily/partitions/20261027.json').read_text()), spot)
    assert back[0]['previous_session_source'] == 'previous_session_archive'
    # The live chain fetches {previous, target} in one batch into a separate root.
    chain_root = tmp_path/'chain'
    for name in ('derivatives.yaml', 'observation_sources.yaml', 'market_calendars/XSHG.json'):
        (chain_root/'config'/name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/'config'/name, chain_root/'config'/name)
    _fake_backfill_source(monkeypatch, {'20261026', '20261027'})
    chained = m.build('20261027', indices(), online=True, root=chain_root)['rows']
    assert all(r['previous_session_source'] == 'same_batch' for r in chained)
    for a, b in zip(back, chained):
        assert a['contract'] == b['contract']
        for key in ('open', 'high', 'low', 'close', 'settle', 'volume', 'open_interest',
                    'is_main_open_interest', 'return_1d', 'previous_close'):
            assert a[key] == b[key]


def test_listing_day_is_distinct_from_a_missing_prior_session(root, monkeypatch):
    """2015-04-16 has no earlier IC session; a stray gap day is a different reason."""
    monkeypatch.setattr(m, 'PRODUCT_LISTED_DATE', '20150416')
    monkeypatch.setattr(m, 'backfill_sessions',
                        lambda r, s, e: (None, ['20150416']))
    _fake_backfill_source(monkeypatch, {'20150416'})
    m.backfill(root, '20150416', '20150417')
    item, sig = m.definitions(root)
    receipt = json.loads((root/'data/sources/cffex/raw/daily/partitions/20150416.json').read_text())
    spot = m.spot_reference(indices(), item, '20150416')
    rows = m.consume(root, '20150416', item, sig, receipt, spot)
    assert rows[0]['reason'] == 'PRODUCT_LISTED' and rows[0]['close'] is None
    assert '上市首日' in panel({'rows': rows})
