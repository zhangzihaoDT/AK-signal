"""Publish a Skill evidence candidate to config; no quote fetch or daily calculation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
from pathlib import Path

from src.common.batch_release import atomic_json
from src.etf_signal.classification_facts import CURRENT, VERSIONS, SCHEMA, MARKETS, DIRECTIONS, digest, validate_document


def publish(root: Path, candidate: Path, version: str, validator: Path, base_version: str | None = None) -> dict:
    spec = importlib.util.spec_from_file_location('skill_evidence_validator', validator)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = candidate.read_bytes()
    errors = module.validate(candidate)
    if errors:
        raise ValueError('Classification candidate rejected: ' + '; '.join(errors))
    payload = json.loads(original)
    if payload['run']['completion'] != 'complete':
        raise ValueError('Unprocessed candidate scope cannot be published')
    # Candidate and all referenced snapshots are validated by the installed Skill.
    if candidate.read_bytes() != original:
        raise ValueError('Candidate changed during validation')
    relative = str(candidate.resolve().relative_to((root / 'data').resolve()))
    candidate_ref = {'path': 'data/' + relative, 'sha256': digest(original), 'run_id': payload['run']['run_id']}
    audit_dir = root / 'data/reference/etf_classification/publications'
    audit_dir.mkdir(parents=True, exist_ok=True)
    with (audit_dir / '.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current_path = root / CURRENT
        previous = json.loads(current_path.read_bytes()) if current_path.exists() else None
        if previous is not None:
            validate_document(previous)
        # Idempotent retry after current switch, including interrupted receipt write.
        if previous and previous['classification_version'] == version:
            if previous['candidate'] != candidate_ref:
                raise ValueError('Version already published with different evidence')
            archive = root / VERSIONS / f'{version}.json'
            if archive.read_bytes() != current_path.read_bytes():
                raise ValueError('Published version/archive checksum mismatch')
            receipt = {'status': 'published', 'classification_version': version, 'sha256': digest(current_path.read_bytes()), 'candidate': candidate_ref}
            atomic_json(audit_dir / f'{version}.json', receipt)
            return receipt
        if (previous or {}).get('classification_version') != base_version:
            raise ValueError('Classification base version changed; rebase candidate explicitly')
        records = {r['fund_code']: r for r in (previous or {}).get('records', [])}
        for fund in payload['funds']:
            record = {'fund_code': fund['fund_code'], 'fund_name': fund['fund_name'], 'candidate': candidate_ref}
            for key in ('investment_market', 'investment_direction'):
                fact = fund[key]
                record[key] = {field: fact[field] for field in ('value', 'status', 'as_of', 'scope', 'reason')}
                record[key].update(valid_from=fact.get('valid_from'), valid_to=fact.get('valid_to'),
                                   source_ids=sorted({e['source_id'] for e in fact['evidence']}))
            records[fund['fund_code']] = record
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        document = {'schema_version': SCHEMA, 'classification_version': version, 'published_at': stamp,
                    'as_of_date': payload['run'].get('as_of_date', payload['run'].get('target_trade_date')),
                    'previous_version': base_version, 'candidate': candidate_ref,
                    'taxonomy_sha256': payload['run']['taxonomy_sha256'],
                    'investment_markets': MARKETS, 'investment_directions': DIRECTIONS,
                    'records': sorted(records.values(), key=lambda r: r['fund_code'])}
        validate_document(document)
        archive = root / VERSIONS / f'{version}.json'
        if archive.exists():
            saved = json.loads(archive.read_bytes())
            # A previous attempt may have prepared an archive without switching current.
            document['published_at'] = saved['published_at']
            if saved != document:
                raise ValueError('Immutable classification version already exists')
        else:
            atomic_json(archive, document)
        content = archive.read_bytes()
        validate_document(json.loads(content))
        # No latest state changes until the complete archive is readable and validated.
        receipt = {'status': 'prepared', 'classification_version': version, 'sha256': digest(content), 'candidate': candidate_ref}
        atomic_json(audit_dir / f'{version}.json', receipt)
        atomic_json(current_path, document)
        if digest(current_path.read_bytes()) != digest(content):
            raise ValueError('Classification publication readback mismatch')
        receipt['status'] = 'published'
        atomic_json(audit_dir / f'{version}.json', receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--base-version')
    parser.add_argument('--validator', type=Path, required=True, help='Installed Skill scripts/validate_evidence.py')
    args = parser.parse_args()
    print(json.dumps(publish(args.root, args.candidate, args.version, args.validator, args.base_version), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
