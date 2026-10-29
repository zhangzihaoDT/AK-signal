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
