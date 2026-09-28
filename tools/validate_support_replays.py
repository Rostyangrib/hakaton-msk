"""Partial synthetic oracle with an explicit changed support-policy criterion.

The legacy strict cargo-only check is retained and reported, but is not the new
policy: comparable danger may take priority through accumulated waiting age.
All remaining legacy safety checks stay mandatory.
"""
import argparse
import json
from pathlib import Path
from compare_versions import synthetic_case


def validate(root,results,out):
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    entries=[];runtime=None
    for case in manifest['cases']:
        sid=case['scenario_id'];replay=json.loads((results/(sid+'.json')).read_text(encoding='utf-8'))
        assert replay['input_sha256']==case['sha256'],sid+': input changed'
        if runtime is None:runtime=replay['runtime_sha256']
        assert runtime==replay['runtime_sha256'],sid+': mixed runtime'
        assert replay['guard_failure_packets']==0,sid+': guard failure'
        entry=synthetic_case(case,root,results/(sid+'.ndjson.gz'),results/'SYN-001.ndjson.gz')
        assert entry['packets']==replay['packets']==replay['schema_valid'],sid+': incomplete replay'
        legacy=dict(entry['failures'])
        entry['legacy_policy_failures']={k:v for k,v in legacy.items() if k=='cargo_priority_inverted'}
        entry['failures']={k:v for k,v in legacy.items() if k!='cargo_priority_inverted'}
        entry['policy_note']='Strict cargo-only priority was replaced by risk/ETA, service protection and waiting age. Legacy inversions are reported separately, not concealed.'
        entries.append(entry)
        print(json.dumps(dict(scenario=sid,failures=entry['failures'],legacy_policy_failures=entry['legacy_policy_failures'],risk_indicators=entry['risk_indicators'])),flush=True)
    out.write_text(json.dumps(entries,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not any(e['failures'] for e in entries),'Synthetic safety property failure; see '+str(out)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ['root','results','out']:parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();validate(args.root,args.results,args.out)
