"""Isolated read-only sw_daily probe; writes only the requested Markdown report.

Run with project Python. Read token from TUSHARE_TOKEN / TS_TOKEN, --token-file
(JSON, env-style, CSV token column, or plain text), or SDK default ~/tk.csv.
Never pass a credential as a command-line argument. No retry or provider fallback.
The official HTTP protocol is used over HTTPS: unlike SDK 1.4.29, an HTTP failure
cannot silently turn into an empty DataFrame. No production data writer imported.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.common.paths import sw_industry_raw_dir
from src.sw_industry_rps import storage
from src.sw_industry_rps.source_status import verified_price_dates

TARGET = '20261015'
URL = 'https://api.tushare.pro'
DOC = 'https://tushare.pro/document/2?doc_id=327'
META_DOC = 'https://tushare.pro/document/2?doc_id=181'


class ProbeError(Exception):
    def __init__(self, status, kind, summary):
        self.status, self.kind, self.summary = status, kind, summary
        super().__init__(summary)


def read_token(path=None, key='TUSHARE_TOKEN'):
    if path:
        p = Path(path).expanduser()
        if p.suffix == '.csv':
            frame = pd.read_csv(p)
            return str(frame.loc[frame.token.notna(), 'token'].iloc[0]).strip(), '指定 CSV 文件'
        raw = p.read_text().strip()
        if p.suffix == '.json':
            return str(json.loads(raw)[key]).strip(), '指定 JSON 文件'
        for line in raw.splitlines():
            match = re.match(r'(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*)', line.strip())
            if match and match[1] in (key, 'TUSHARE_TOKEN', 'TS_TOKEN'):
                return match[2].strip().strip('"\''), '指定环境配置文件'
        if len(raw.splitlines()) == 1 and '=' not in raw:
            return raw, '指定纯文本凭证文件'
        return '', '指定文件未找到凭证字段'
    for name in (key, 'TUSHARE_TOKEN', 'TS_TOKEN'):
        if os.environ.get(name, '').strip():
            return os.environ[name].strip(), f'环境变量 {name}'
    standard = Path.home() / 'tk.csv'
    if standard.is_file():
        return read_token(standard)
    return '', '未发现可读取凭证'


def safe_summary(value, token):
    text = str(value).replace(token, '[REDACTED]') if token else str(value)
    text = re.sub(r'[A-Za-z0-9_-]{32,}', '[REDACTED]', text)
    return text.replace('\n', ' ').replace('|', '/')[:260]


def query(api, params, token, audit):
    start = time.monotonic()
    entry = {'api': api, 'params': params, 'status': 'SOURCE_ERROR', 'rows': None,
             'elapsed_s': None, 'exception_type': '', 'summary': ''}
    audit.append(entry)
    try:
        # Token belongs only in this official provider's POST body; no redirects.
        response = requests.post(URL, json={'api_name': api, 'token': token,
                                            'params': params, 'fields': ''}, timeout=30,
                                 allow_redirects=False)
        entry['http_status'] = response.status_code
        if response.status_code != 200:
            state = 'AUTH_FAILED' if response.status_code == 401 else 'PERMISSION_DENIED' if response.status_code == 403 else 'SOURCE_ERROR'
            raise ProbeError(state, 'HTTPStatusError', f'HTTP {response.status_code}')
        body = response.json()
        if not isinstance(body, dict) or 'code' not in body:
            raise ProbeError('SOURCE_ERROR', 'SchemaError', '响应缺少 API code')
        code, msg = body['code'], str(body.get('msg', ''))
        entry['api_code'] = code
        if code != 0:
            low = msg.lower()
            if any(x in low for x in ('token', '令牌', '凭证')) and any(x in low for x in ('无效', '不对', '错误', 'invalid', 'expired', '失效', '不存在')):
                state = 'AUTH_FAILED'
            elif code == 2002 or any(x in low for x in ('权限', '积分', 'permission', 'points', '访问该接口')):
                state = 'PERMISSION_DENIED'
            else:
                state = 'SOURCE_ERROR'
            raise ProbeError(state, 'TushareAPIError', safe_summary(msg, token))
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('fields'), list) or not isinstance(data.get('items'), list):
            raise ProbeError('SOURCE_ERROR', 'SchemaError', '成功响应缺少 fields/items；不视作无数据')
        frame = pd.DataFrame(data['items'], columns=data['fields'])
        required = {'ts_code', 'trade_date', 'open', 'high', 'low', 'close'} if api == 'sw_daily' else {'index_code', 'industry_name', 'level'}
        if not required.issubset(frame.columns):
            raise ProbeError('SOURCE_ERROR', 'SchemaError', '响应缺少必要行情或分类字段')
        if api == 'sw_daily':
            entry['duplicate_keys'] = int(frame.duplicated(['ts_code','trade_date'], keep=False).sum())
            entry['dates'] = sorted(frame.trade_date.astype(str).unique().tolist())
        entry.update(status='SOURCE_EMPTY' if frame.empty else 'SUCCESS', rows=len(frame))
        return frame
    except ProbeError as exc:
        entry.update(status=exc.status, exception_type=exc.kind, summary=exc.summary)
        raise
    except Exception as exc:
        entry.update(status='SOURCE_ERROR', exception_type=type(exc).__name__, summary=safe_summary(str(exc), token))
        raise ProbeError('SOURCE_ERROR', type(exc).__name__, entry['summary']) from None
    finally:
        entry['elapsed_s'] = round(time.monotonic() - start, 3)


def select_samples():
    raw = sw_industry_raw_dir()
    master = storage.load_master(raw)
    active, _, _ = storage.compute_active_codes(raw, master)
    samples, parents = [], set()
    for _, row in master[master.industry_code.isin(active)].sort_values('industry_code').iterrows():
        parent = row.get('parent_industry', '')
        if not parent or parent in parents:
            continue
        hist = storage.load_industry_raw(raw, row.industry_code)
        if hist.empty:
            continue
        hist = hist.loc[verified_price_dates(hist) & (pd.to_datetime(hist.trade_date) <= pd.Timestamp(TARGET))].copy()
        hist = hist.loc[pd.to_numeric(hist.close, errors='coerce').gt(0)].sort_values('trade_date')
        recent = hist.loc[(hist.trade_date < pd.Timestamp(TARGET))].tail(20)
        if len(hist) < 250 or recent.empty or recent.trade_date.max() != pd.Timestamp('20261014'):
            continue
        if not recent.trade_date.reset_index(drop=True).equals(pd.Series(pd.bdate_range(end='20261014', periods=20))):
            # Use the project's exchange calendar, not weekday assumptions across holidays.
            from src.common.trading_calendar import default_calendar
            expected = [pd.Timestamp(d) for d in default_calendar(ROOT).days if pd.Timestamp(d) <= pd.Timestamp('20261014')][-20:]
            if recent.trade_date.tolist() != expected:
                continue
        samples.append({'code': row.industry_code, 'name': row.industry_name, 'parent': parent,
                        'history': hist, 'mapping': None, 'date': None, 'close': None,
                        'date_check': '未执行', 'price_check': 'NOT_VERIFIABLE', 'ohlc': '未执行',
                        'target_row': None, 'comparison': None})
        parents.add(parent)
        if len(samples) == 5:
            break
    if len(samples) != 5:
        raise ValueError('未选出 5 个满足历史连续性条件、跨一级行业的正式池样本')
    return samples, len(active)


def good_number(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def valid_ohlc(row):
    if not all(good_number(row.get(c)) for c in ('open', 'high', 'low', 'close')):
        return False
    o, h, l, c = (float(row[x]) for x in ('open', 'high', 'low', 'close'))
    return l <= min(o, c) <= max(o, c) <= h


def inspect_target(samples, frame, meta):
    for sample in samples:
        # Map by existing local industry name + official SW2021 L2 metadata.
        mapped = meta.loc[(meta.industry_name == sample['name']) & (meta.level == 'L2')]
        if len(mapped) != 1 or str(mapped.iloc[0].index_code) != sample['code']:
            sample['date_check'] = 'FAIL（身份映射未确认）'
            continue
        sample['mapping'] = str(mapped.iloc[0].index_code)
        rows = frame.loc[frame.ts_code == sample['mapping']]
        if len(rows) != 1:
            sample['date_check'] = 'FAIL（缺失或重复）'
            continue
        row = rows.iloc[0]
        sample['date'] = str(row.trade_date)
        sample['date_check'] = 'PASS' if sample['date'] == TARGET else 'FAIL'
        sample['close'] = float(row.close) if good_number(row.close) else None
        sample['ohlc'] = 'PASS' if valid_ohlc(row) else 'FAIL'
        sample['target_row'] = row.to_dict()


def run_probe(samples, token, audit):
    target = query('sw_daily', {'trade_date': TARGET}, token, audit)
    if target.empty:
        return 'SOURCE_EMPTY', 'FAIL', '接口正常返回，但目标日期数据为空。'
    meta = query('index_classify', {'level': 'L2', 'src': 'SW2021'}, token, audit)
    if meta.empty:
        return 'SUCCESS', 'CONDITIONAL', '目标日有行情，但官方分类元数据为空，无法确认样本身份。'
    inspect_target(samples, target, meta)
    if any(s['date'] is not None and s['date_check'] == 'FAIL' for s in samples):
        return 'SUCCESS', 'FAIL', '接口返回的样本日期不等于请求目标日，不能用于目标分区。'
    for sample in samples:
        matches = meta.loc[(meta.industry_name == sample['name']) & (meta.level == 'L2')]
        if len(matches) == 1 and str(matches.iloc[0].index_code) != sample['code']:
            return 'SUCCESS', 'FAIL', '发现同名行业的指数代码身份不一致，禁止假定代码等价。'
    # One prior day shared by the five trusted local histories; no date-filling.
    sets = [set(s['history'].trade_date.dt.strftime('%Y%m%d')) for s in samples]
    common = sorted(set.intersection(*sets), reverse=True)
    compare_day = TARGET if TARGET in common else next((d for d in common if d < TARGET), None)
    compare = target if compare_day == TARGET else query('sw_daily', {'trade_date': compare_day}, token, audit) if compare_day else pd.DataFrame()
    for s in samples:
        if not s['mapping'] or compare.empty:
            continue
        rows = compare.loc[(compare.ts_code == s['mapping']) & (compare.trade_date.astype(str) == compare_day)]
        local = s['history'].loc[s['history'].trade_date.dt.strftime('%Y%m%d') == compare_day]
        if len(rows) != 1 or len(local) != 1 or not good_number(rows.iloc[0].close):
            continue
        r, reference = rows.iloc[0], local.iloc[0]
        # Name and code identities must agree on the comparison date too.
        if 'name' not in r or r['name'] != s['name'] or not valid_ohlc(r):
            continue
        base, actual = float(reference.close), float(r.close)
        deviation = abs(actual - base) / base * 100
        # Local daily close is rounded to .01: permit at most .01 index point.
        s['price_check'] = 'PASS' if abs(actual - base) <= 0.010000001 else 'FAIL'
        s['comparison'] = {'date': compare_day, 'tushare_close': actual, 'local_close': base,
                           'relative_deviation_pct': deviation, 'local_source': str(reference.get('source', ''))}
    valid = [s for s in samples if s['mapping'] and s['date_check'] == 'PASS' and s['close'] is not None and s['ohlc'] == 'PASS']
    if any(s['price_check'] == 'FAIL' or s['ohlc'] == 'FAIL' for s in samples):
        return 'SUCCESS', 'FAIL', '发现收盘价差异或 OHLC 不一致，需要先核实指数身份与价格口径。'
    if len(valid) == 5 and all(s['price_check'] == 'PASS' for s in samples):
        return 'SUCCESS', 'PASS', '5/5 样本有效、日期一致，历史交叉验证未发现超过 .01 点的差异。'
    return 'SUCCESS', 'CONDITIONAL', '目标日期接口有行情，但样本覆盖、映射或可信价格对照尚未全部通过。'


def snapshot_production():
    # Capture read-only hashes; do not put credentials or environment in output.
    paths = list((ROOT / 'src').rglob('*.py')) + list((ROOT / 'config').rglob('*'))
    paths += list(sw_industry_raw_dir().glob('*'))
    paths += [ROOT / 'data/processed/sw_industry/industry_daily_metrics.csv', ROOT / 'outputs/run_day/publication.json']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


def make_report(samples, active_count, token_source, status, verdict, reason, audit, unchanged):
    valid = [s for s in samples if s['mapping'] and s['date_check'] == 'PASS' and s['close'] is not None and s['ohlc'] == 'PASS']
    mapped = sum(s['mapping'] is not None for s in samples)
    dated = sum(s['date_check'] == 'PASS' for s in samples)
    checked = sum(s['comparison'] is not None for s in samples)
    called = bool(audit)
    target_received = bool(audit and audit[0]['status'] in ('SUCCESS', 'SOURCE_EMPTY'))
    lines = ['# Tushare sw_daily · 20261015 独立验证报告', '',
             f'验证时间：{datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}', '',
             f'**最终判定：{verdict}**。{reason}', '',
             '## 范围与环境', '',
             f'- 本次选取 5 个样本，正式池共 {active_count} 个行业；未验证全池。',
             '- 项目原环境未安装 Tushare；SDK 1.4.29 仅安装在 /private/tmp/aksignal-tushare-validation，未修改项目依赖。',
             '- 已检查 SDK 与官方协议；使用官方 HTTPS POST，避免 SDK 对 HTTP 错误返回空 DataFrame 导致误判。每个请求只调用一次，无重试。',
             f'- Token 可读取：{"是" if called else "否"}；读取方式：{token_source}。未保存或输出凭证。',
             f'- 接口验证状态：{status}；未发请求时不认定 AUTH_FAILED 或 PERMISSION_DENIED。',
             f'- 生产代码、配置、原始行业缓存、行业指标和发布回执前后指纹：{"一致" if unchanged else "存在外部变化，需进一步核对；脚本无生产写入操作"}。', '',
             '## 官方接口依据', '',
             f'- [sw_daily 官方文档]({DOC})：默认申万 2021 版，5000 积分，单次最多 4000 行，标示交易日 18:30 更新。文档要求不等于本 Token 权限已通过。',
             f'- [index_classify 官方文档]({META_DOC})：使用 level=L2、src=SW2021 元数据确认映射；需 2000 积分。',
             '- [官方调用协议](https://tushare.pro/document/1?doc_id=40)：API code、msg、data.fields/items 分开检查；异常不得当作无行情。', '',
             '## 样本与映射', '',
             '样本按现有代码排序，选择不同一级行业；最近 20 个正式交易日有可信记录且历史不少于 250 行。代码来自正式 master 和 active snapshot。Tushare 映射必须由官方分类元数据验证，未查询时不假定代码相同。', '',
             '| 行业（一级方向） | AKsignal 代码 | Tushare 代码 | 目标日期（实际返回） | 收盘价 | 日期核验 | 价格核验 |',
             '|---|---|---|---|---:|---|---|']
    for s in samples:
        lines.append(f"| {s['name']}（{s['parent']}） | {s['code']} | {s['mapping'] or '未核验'} | {s['date'] or '未取得（目标 20261015）'} | {s['close'] if s['close'] is not None else '—'} | {s['date_check']} | {s['price_check']} |")
    lines += ['', '未执行不等于日期错误；无返回时不捏造 PASS/FAIL 或 Tushare 代码。', '', '## 质量与覆盖', '',
              f'- 成功映射：{mapped}/5；有效目标日价格：{len(valid)}/5；未取得有效价格：{5-len(valid)}/5。',
              f'- 有效覆盖率：{format(len(valid)/5, ".0%") if target_received else "N/A（未取得行情，已取得有效价格 0/5）"}。',
              f'- 日期一致率：{str(dated)+"/5" if target_received else "N/A（未取得行情）"}；未返回时，返回记录内部一致率为 N/A。',
              f'- 价格可核验样本：{checked}/5；偏差公式 = |Tushare 收盘价 − 本地可信收盘价| / 本地可信收盘价 × 100%。',
              '- 比较日优先目标日，否则最近的本地共同可信交易日；本地 realtime 等无日期记录不作为对照。',
              '- 比较阈值为 0.01 指数点（本地收盘价保留两位小数）；超过即标记 FAIL，需研究差异，不自行改值。',
              '- 成交量与成交额缺失单列为未提供，不拿其他源或其他日期补值。重复样本记录不计入有效价格。',
              '- 代码/名称通过 SW2021 L2 元数据关联后才比较；这是渠道间价格对照，不能证明两渠道底层数据生产过程独立。', '',
              '| 行业 | 对照交易日 | Tushare 收盘 | 本地可信收盘 | 相对偏差 % | 本地来源 |',
              '|---|---|---:|---:|---:|---|']
    for s in samples:
        c = s['comparison']
        lines.append(f"| {s['name']} | {c['date'] if c else 'NOT_VERIFIABLE'} | {c['tushare_close'] if c else '—'} | {c['local_close'] if c else '—'} | {c['relative_deviation_pct'] if c else '—'} | {c['local_source'] if c else '—'} |")
    lines += ['', '## 目标日 OHLC 与成交信息', '',
              '成交量单位为万股，成交额为万元；未转换、未写入正式数据。检查四价有限且为正，low ≤ open/close ≤ high。', '',
              '| 行业 | open | high | low | close | pct_change | vol | amount | OHLC |', '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for s in samples:
        r = s['target_row'] or {}
        lines.append('| ' + s['name'] + ' | ' + ' | '.join(str(r.get(k, '—')) for k in ('open','high','low','close','pct_change','vol','amount')) + ' | ' + s['ohlc'] + ' |')
    lines += ['', '## 请求审计', '', '| 接口 | 参数 | 状态 | 返回行数 | 秒 | 异常类型 | 摘要 |', '|---|---|---|---:|---:|---|---|']
    for e in audit:
        lines.append('| ' + ' | '.join(str(x) for x in (e['api'], json.dumps(e['params'],ensure_ascii=False),e['status'],e['rows'] if e['rows'] is not None else '未取得',e['elapsed_s'],e['exception_type'] or '—', e['summary'] or '—')) + ' |')
    for e in audit:
        lines.append(f"\n{e['api']} HTTP={e.get('http_status','N/A')}，API code={e.get('api_code','N/A')}，重复主键记录数={e.get('duplicate_keys','N/A')}，返回日期={e.get('dates','N/A')}。")
    if not audit:
        lines.append('| sw_daily | trade_date=20261015 | NOT_RUN | 未取得 | N/A | LOCAL_CONFIG_MISSING | 没有可读取 Token，未发送鉴权请求 |')
    lines += ['', f'累计请求耗时：{sum(e["elapsed_s"] for e in audit):.3f} 秒；无请求时此值不是接口响应耗时。', '', '## 是否推进 124 行业验证', '',
              ('值得开展下一阶段，但须额外核验 124 行业映射、覆盖、历史连续性和多日新鲜度；单次小样本通过不代表生产验收。' if verdict == 'PASS' else
               '暂不开展全量请求。先解决本报告列出的凭证、权限、映射或价格证据缺口，再重复这 5 个样本；未申请、购买或升级权限。'), '',
              '本次未接入正式路由、未更新数据库/缓存、未计算 RPS、未触发 Daily Pipeline 或发布日报。', '',
              '## 复现', '',
              '使用项目 Python 执行本目录 validate_tushare_sw_daily.py；凭证通过现有环境变量或 --token-file 指定本地文件传入（参数只含路径）。未设置凭证时只生成未执行报告。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file')
    parser.add_argument('--token-key', default='TUSHARE_TOKEN')
    args = parser.parse_args()
    before = snapshot_production()
    samples, count = select_samples()
    audit, token, source = [], '', '未发现可读取凭证'
    status, verdict, reason = 'NOT_RUN', 'NOT_ASSESSED（前置条件未满足）', '未取得可读取 Token，尚未执行鉴权及行情请求；不能据此判为权限不足或源缺数。'
    try:
        token, source = read_token(args.token_file, args.token_key)
        if token:
            status, verdict, reason = run_probe(samples, token, audit)
    except ProbeError as exc:
        status = exc.status
        verdict = 'CONDITIONAL' if any(s['target_row'] is not None for s in samples) else 'FAIL'
        reason = safe_summary(exc.summary, token)
    except Exception as exc:
        status, verdict = 'SOURCE_ERROR', 'NOT_ASSESSED（本地执行异常）'
        reason = f'{type(exc).__name__}：本地执行失败，未据此推断上游无数据。'
    report = make_report(samples,count,source,status,verdict,reason,audit,before == snapshot_production())
    (Path(__file__).parent / f'tushare_sw_daily_{TARGET}.md').write_text(report)
    print(json.dumps({'status':status,'verdict':verdict,'requests':len(audit),'report':f'docs/validation/tushare_sw_daily_{TARGET}.md'},ensure_ascii=False))


if __name__ == '__main__':
    main()
