"""Offline partial mutation oracle for one completed synthetic replay version."""
import argparse
import json
from pathlib import Path
from compare_versions import synthetic_case


def check(root, results, out):
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    entries = []
    runtime = None
    for case in manifest['cases']:
        sid = case['scenario_id']
        replay = json.loads((results / (sid + '.json')).read_text(encoding='utf-8'))
        assert replay['input_sha256'] == case['sha256'], sid + ': input changed'
        if runtime is None:
            runtime = replay['runtime_sha256']
        assert replay['runtime_sha256'] == runtime, sid + ': mixed runtime'
        assert replay['guard_failure_packets'] == 0, sid + ': guard failure'
        entry = synthetic_case(case, root, results / (sid + '.ndjson.gz'), results / 'SYN-001.ndjson.gz')
        assert entry['packets'] == replay['packets'] == replay['schema_valid'], sid + ': incomplete replay'
        entries.append(entry)
        print(json.dumps({'scenario': sid, 'failures': entry['failures'], 'risk_indicators': entry['risk_indicators']}), flush=True)
    out.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    if any(e['failures'] for e in entries):
        raise SystemExit('Synthetic property failures: see ' + str(out))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for key in ['synthetic', 'results', 'out']:
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    check(args.synthetic, args.results, args.out)
