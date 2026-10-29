"""Import an existing name-inference audit; never classify or upgrade verification."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from src.common.batch_release import atomic_json
from src.etf_signal.classification_facts import digest


def import_audit(source: Path, taxonomy: Path, output: Path, as_of: str) -> Path:
    rows = list(csv.DictReader(source.open(encoding='utf-8')))
    codes = [r['fund_code'] for r in rows]
    if len(set(codes)) != len(codes):
        raise ValueError('Duplicate input codes')
    output.mkdir(parents=True, exist_ok=False)
    snapshots = output / 'snapshots'
    snapshots.mkdir()
    shutil.copyfile(source, snapshots / 'name_inference.csv')
    shutil.copyfile(taxonomy, snapshots / 'taxonomy.yaml')
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    sources = [{'source_id': 'legacy-name-audit', 'title': source.name, 'publisher': 'AKsignal 名称推断审计',
                'url': None, 'authority': 'local', 'kind': 'local_heuristic', 'access': 'retrieved',
                'retrieved_at': stamp, 'published_at': None, 'effective_from': None, 'effective_to': None,
                'snapshot_path': 'snapshots/name_inference.csv', 'sha256': digest(source.read_bytes()),
                'extraction_method': 'existing_audit_import_no_reclassification'}]
    funds = []
    for number, row in enumerate(rows, start=2):
        def link(claim, observation):
            return {'source_id': 'legacy-name-audit', 'claim': claim, 'locator': f'CSV line {number}; fund_code={row["fund_code"]}', 'observation': observation}
        fund = {'fund_code': row['fund_code'], 'fund_name': row['fund_name'], 'supersedes': None,
                'identity': {'structure': 'unknown', 'index_id': None, 'index_name': None, 'status': 'pending',
                             'reason': '只导入本地基金代码与名称，官方跟踪关系尚未核验',
                             'evidence': [link('fund_identity', row['fund_name'])], 'unresolved': ['缺少官方跟踪关系'],
                             'next_step': '由独立分类维护任务查找官方合同/产品资料'}}
        for key, prefix in (('investment_market', 'market'), ('investment_direction', 'direction')):
            value = row[key]
            status = 'pending' if value == '待核实' else 'inferred'
            if row[f'{prefix}_classification_status'] not in ('pending', 'unverified', 'inferred'):
                raise ValueError('Bootstrap importer only accepts unverified historical audit')
            claim = 'market_scope' if prefix == 'market' else 'industry_coverage'
            fund[key] = {'value': value, 'status': status, 'scope': 'mandate', 'as_of': as_of,
                         'valid_from': None, 'valid_to': None,
                         'applicability_reason': '仅从该时点起按已记录推断消费，不追认更早历史或已核验事实',
                         'reason': '沿用已保存名称推断；命中线索：' + (row[f'{prefix}_classification_evidence'] or '无'),
                         'evidence': [link(claim, f'{key}={value}; legacy status={row[f"{prefix}_classification_status"]}')],
                         'unresolved': ['缺少支持该维度的官方正文核验'],
                         'next_step': '独立取证维护时核验该维度，不在 RunDay 内补证'}
        funds.append(fund)
    document = {'schema_version': 'etf-classification-evidence-v2',
                'run': {'run_id': output.name, 'generated_at': stamp, 'as_of_date': as_of,
                        'input_snapshot': 'snapshots/name_inference.csv', 'input_sha256': digest(source.read_bytes()),
                        'taxonomy_snapshot': 'snapshots/taxonomy.yaml', 'taxonomy_sha256': digest(taxonomy.read_bytes()),
                        'requested_codes': codes, 'unprocessed_codes': [], 'completion': 'complete'},
                'sources': sources, 'funds': funds}
    path = output / 'classification_facts.json'
    atomic_json(path, document)
    with (output / 'audit.jsonl').open('w', encoding='utf-8') as stream:
        for fund in funds:
            for key in ('investment_market', 'investment_direction'):
                stream.write(json.dumps({'run_id': output.name, 'fund_code': fund['fund_code'], 'dimension': key,
                                         'event_at': stamp, 'event': 'import_existing_inference',
                                         'new_value': fund[key]['value'], 'new_status': fund[key]['status'],
                                         'source_ids': ['legacy-name-audit'], 'reason': fund[key]['reason']}, ensure_ascii=False) + '\n')
    counts = {key: {status: sum(f[key]['status'] == status for f in funds) for status in ('verified', 'inferred', 'pending')}
              for key in ('investment_market', 'investment_direction')}
    atomic_json(output / 'summary.json', {'rows': len(funds), 'counts': counts, 'scope': 'existing_audit_import'})
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--taxonomy', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    args = parser.parse_args()
    print(import_audit(args.source, args.taxonomy, args.output, args.as_of))
