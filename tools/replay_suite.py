"""Causal replay for PUBLIC and synthetic suites, with version-specific imports."""
import argparse
import gzip
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path


def replay(repo, reference, packets, out, sid, expectations=None):
    # Imported after selecting the checkout; no labels or expectations enter Controller.
    from corridor.reference import Reference
    from corridor.controller import Controller
    from corridor.guard import errors
    from corridor.basic import telemetry
    controller=Controller(Reference(reference))
    actions=Counter(); diagnostics=Counter(); support=Counter(); times=[]; count=0
    structural=0; motion_switches=0; previous={}
    out.mkdir(parents=True,exist_ok=True)
    with gzip.open(packets,'rt',encoding='utf-8') as source, gzip.open(out/(sid+'.ndjson.gz'),'wt',encoding='utf-8') as target:
        for line in source:
            packet=json.loads(line); start=time.perf_counter(); d=controller.process(packet)
            times.append((time.perf_counter()-start)*1000); count+=1
            controller.contract.validate(d)
            structural+=bool(errors(controller.state,controller.fusion,d))
            observations={v:telemetry(controller.state,v) for v in controller.ref.vehicles}
            record=dict(step=packet['step'],decision=d,telemetry=observations,
                        diagnostics=controller.state.diagnostics,excluded=sorted(controller.trust.excluded))
            target.write(json.dumps(record,ensure_ascii=False)+'\n')
            support[sum(a['remote_support_required'] for a in d['vehicle_actions'])]+=1
            for a in d['vehicle_actions']:
                actions[a['motion_action']]+=1
                motion_switches+=a['vehicle_id'] in previous and previous[a['vehicle_id']]!=a['motion_action']
                previous[a['vehicle_id']]=a['motion_action']
            for issue in controller.state.diagnostics:
                diagnostics[issue.split(':')[0] if isinstance(issue,str) else next(iter(issue))]+=1
    ordered=sorted(times)
    summary=dict(scenario=sid,packets=count,schema_valid=count,guard_failure_packets=structural,
                 action_counts=dict(actions),support_histogram=dict(support),motion_switches=motion_switches,
                 diagnostics=dict(diagnostics),latency_ms={k:ordered[min(len(ordered)-1,int((len(ordered)-1)*q))] for k,q in [('p50',.5),('p95',.95),('p99',.99),('max',1)]},
                 timing_over_2000_ms=sum(t>2000 for t in times),input_sha256=hashlib.sha256(packets.read_bytes()).hexdigest(),
                 reference_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(reference.iterdir()) if p.is_file()},
                 runtime_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((repo/'corridor').glob('*.py'))})
    (out/(sid+'.json')).write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(scenario=sid,packets=count,actions=dict(actions),guard_failures=structural)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--synthetic',type=Path)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--scenario',action='append')
    args=parser.parse_args()
    repo=args.repo.resolve();sys.path.insert(0,str(repo))
    if args.synthetic:
        root=args.synthetic.resolve(); manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        for case in manifest['cases']:
            if args.scenario and case['scenario_id'] not in args.scenario: continue
            packets=root/case['folder']/'packets.ndjson.gz'
            assert hashlib.sha256(packets.read_bytes()).hexdigest()==case['sha256']
            replay(repo,root/case['reference'],packets,args.out,case['scenario_id'],case)
    else:
        for sid in args.scenario or ['PUBLIC-101','PUBLIC-102']:
            replay(repo,args.data/'01_reference',args.data/'03_public'/sid/'packets.ndjson.gz',args.out,sid)
