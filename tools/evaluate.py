"""Offline labels-based analysis. Never imported by the runtime."""
import argparse
import csv
import hashlib
import gzip
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from corridor.reference import Reference
from corridor.controller import Controller
from corridor.contract import skeleton
from corridor.state import State, timestamp
from corridor import basic
from corridor.guard import errors


def csv_rows(path):
    with (gzip.open(path,'rt',encoding='utf-8-sig') if path.suffix=='.gz' else path.open(encoding='utf-8-sig')) as stream:
        return list(csv.DictReader(stream))


def macro_f1(pairs):
    truth_classes=sorted({t for t,p in pairs})
    scores={}
    for label in truth_classes:
        tp=sum(n for (t,p),n in pairs.items() if t==p==label)
        fp=sum(n for (t,p),n in pairs.items() if p==label and t!=label)
        fn=sum(n for (t,p),n in pairs.items() if t==label and p!=label)
        scores[label]=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0
    return dict(macro=sum(scores.values())/len(scores) if scores else None,classes=scores,confusion={t+' → '+p:n for (t,p),n in sorted(pairs.items())})


def quantiles(values):
    ordered=sorted(values)
    return {name:ordered[min(len(ordered)-1,math.ceil(len(ordered)*fraction)-1)] for name,fraction in [('p50',.5),('p95',.95),('p99',.99),('max',1)]}


def evaluate(data,sid,out,details=False):
    ref=Reference(data/'01_reference'); controller=Controller(ref); baseline=State(ref)
    directory=data/'02_train'/sid
    road_truth={(r['timestamp'],r['segment_id']):r for r in csv_rows(directory/'labels/01_segment_state.csv.gz')}
    vehicle_truth={(r['timestamp'],r['vehicle_id']):r for r in csv_rows(directory/'labels/02_vehicle_odd.csv.gz')}
    faults=csv_rows(directory/'labels/03_source_faults.csv')
    pair_counts=defaultdict(Counter); multilabel=defaultdict(Counter); mse=defaultdict(list)
    actions=Counter(); switches=Counter(); previous={}; timing=[]; guard_failures=0; packets=0; diagnostics=Counter(); detection={}
    route_selection=Counter(); critical_streak=Counter(); critical_episodes=0; critical_steps=0
    diagnostic_examples={}; calibration=defaultdict(lambda:defaultdict(lambda:[0,0,0]))
    out.mkdir(parents=True,exist_ok=True)
    detail_stream=gzip.open(out/(sid+'-decisions.ndjson.gz'),'wt',encoding='utf-8') if details else None
    def record_confidence(metric,conf,correct):
        mse[metric].append((conf-int(correct))**2)
        bucket=min(9,int(conf*10)); values=calibration[metric][bucket]
        values[0]+=1; values[1]+=conf; values[2]+=int(correct)
    with gzip.open(directory/'packets.ndjson.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            packet=json.loads(line); packets+=1; start=time.perf_counter()
            decision=controller.process(packet); timing.append((time.perf_counter()-start)*1000)
            if detail_stream is not None:
                detail_stream.write(json.dumps(dict(packet_id=packet['packet_id'],decision_time=packet['decision_time'],
                    decision=decision,observations=controller.state.events(),
                    fused_roads=controller.fusion.roads,weather_cache=[dict(position=pos,value=value) for pos,value in controller.fusion.weather_cache.items()],
                    excluded_sources=sorted(controller.trust.excluded),diagnostics=controller.state.diagnostics),ensure_ascii=False)+'\n')
            for diagnostic in controller.state.diagnostics:
                category=diagnostic.split(':')[0] if isinstance(diagnostic,str) else next(iter(diagnostic))
                diagnostics[category]+=1
                diagnostic_examples.setdefault(category,dict(packet_id=packet['packet_id'],detail=diagnostic))
            guard_failures+=bool(errors(controller.state,controller.fusion,decision))
            stamp=packet['decision_time']
            # Source truth status is a disclosed proxy; labels only expose fault episodes.
            source_truth=defaultdict(set)
            for fault in faults:
                if timestamp(fault['start_time']) <= timestamp(stamp) < timestamp(fault['end_time']): source_truth[fault['source_id']].add(fault['fault_type'])
            for r in decision['source_assessments']:
                true_faults=source_truth[r['source_id']]
                truth='FAILED' if 'OUTAGE' in true_faults else 'DEGRADED' if true_faults else 'OK'
                pair_counts['source_status_proxy'][(truth,r['status'])]+=1
                record_confidence('source_status_proxy',r['confidence'],truth==r['status'])
                for code in set(true_faults)|set(r['fault_types']): multilabel['source_fault_codes'][(code,code in true_faults,code in r['fault_types'])]+=1
                for fault in faults:
                    if fault['source_id']==r['source_id'] and fault['fault_type'] in true_faults and fault['fault_type'] in r['fault_types'] and fault['fault_id'] not in detection:
                        detection[fault['fault_id']]=timestamp(stamp)-timestamp(fault['start_time'])
            for r in decision['state_estimates']:
                truth=road_truth.get((stamp,r['segment_id']))
                if truth:
                    pair_counts['road_final'][(truth['true_state'],r['state'])]+=1
                    pair_counts['road_stage3'][(truth['true_state'],'UNKNOWN')]+=1
                    record_confidence('road',r['confidence'],truth['true_state']==r['state'])
            baseline.ingest(packet); basic_decision=skeleton(packet,ref)
            basic_decision['state_estimates']=basic.roads(baseline); basic.policy(baseline,basic_decision)
            for r in basic_decision['state_estimates']:
                truth=road_truth.get((stamp,r['segment_id']))
                if truth: pair_counts['road_stage4'][(truth['true_state'],r['state'])]+=1
            for r in decision['vehicle_assessments']:
                truth=vehicle_truth.get((stamp,r['vehicle_id']))
                if truth and truth['active']=='1':
                    status='COMPLIANT' if truth['odd_compliant']=='1' else 'VIOLATED'
                    pair_counts['odd_active'][(status,r['odd_status'])]+=1
                    record_confidence('odd_active',r['confidence'],status==r['odd_status'])
                    codes=set(filter(None,truth['violation_codes'].split('|')))
                    for code in codes|set(r['violation_codes']): multilabel['odd_codes'][(code,code in codes,code in r['violation_codes'])]+=1
            for a in decision['vehicle_actions']:
                vid=a['vehicle_id']; actions[a['motion_action']]+=1
                truth=vehicle_truth.get((stamp,vid))
                critical=bool(truth and truth['active']=='1' and truth['odd_compliant']=='0' and a['motion_action'] in ('CONTINUE','NO_ACTION') and not a['remote_support_required'])
                critical_steps+=critical
                critical_streak[vid]=critical_streak[vid]+1 if critical else 0
                if critical_streak[vid]==3: critical_episodes+=1
                signature=(a['motion_action'],tuple(a.get('route_segment_ids',[])),a.get('speed_limit_kmh'),a.get('safe_stop_id'),a['remote_support_required'])
                if vid in previous:
                    switches['motion']+=previous[vid][0]!=signature[0]
                    switches['full_recommendation']+=previous[vid]!=signature
                previous[vid]=signature
                paths=controller.router.paths.get(vid)
                event=basic.telemetry(controller.state,vid)
                if paths and event:
                    segment=ref.segments[event['segment_id']]
                    if float(segment['length_m'])-event['offset_m'] <= min(500,float(segment['length_m'])) and paths['dynamic'] is not None:
                        different=paths['static']!=paths['dynamic']
                        route_selection['eligible_steps']+=1
                        route_selection['different_paths']+=different
                        route_selection['emitted_reroute']+=a['motion_action']=='REROUTE'
            if packets%100==0: print(json.dumps(dict(scenario=sid,packets=packets,latest_ms=timing[-1])),flush=True)
    code_scores={}
    for metric,counts in multilabel.items():
        values={}
        for code in sorted({k[0] for k in counts}):
            tp=counts[code,True,True]; fp=counts[code,False,True]; fn=counts[code,True,False]
            values[code]=dict(f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,tp=tp,fp=fp,fn=fn)
        code_scores[metric]=values
    report=dict(scenario=sid,packets=packets,metrics={k:macro_f1(v) for k,v in pair_counts.items()},codes=code_scores,
                confidence_mse={k:sum(v)/len(v) for k,v in mse.items()},latency_ms=quantiles(timing),
                action_counts=dict(actions),switches=dict(switches),route_selection=dict(route_selection),
                guard_failures=guard_failures,diagnostics=dict(diagnostics),
                detection_delay_sec={f['fault_id']:detection.get(f['fault_id']) for f in faults},
                timing_over_2000_ms=sum(t>2000 for t in timing),
                calibration_bins={metric:[dict(bucket=k,n=v[0],mean_confidence=v[1]/v[0],accuracy=v[2]/v[0]) for k,v in sorted(bins.items())] for metric,bins in calibration.items()},
                diagnostic_examples=diagnostic_examples,
                labelled_odd_unsafe_steps=critical_steps,labelled_odd_critical_episodes=critical_episodes,
                assumptions=['Source status proxy: OUTAGE=FAILED, other labelled fault=DEGRADED, no fault=OK; half-open fault intervals.',
                             'No official action utility or unjustified-switch score. Manual rules reviewed on TRAIN-001/002/003; TRAIN-004 held out from tuning.'])
    if detail_stream is not None: detail_stream.close()
    report['runtime_sha256']={str(p.relative_to(Path(__file__).resolve().parent.parent)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                              for group in ('corridor','contract','reference') for p in sorted((Path(__file__).resolve().parent.parent/group).rglob('*'))
                              if p.is_file() and '__pycache__' not in p.parts}
    report['input_sha256']=hashlib.sha256((directory/'packets.ndjson.gz').read_bytes()).hexdigest()
    report['details_saved']=details
    report['timing_environment']='Sequential local Python; controller.process only, excluding offline labels, detail export and baseline. Not Docker timing.'
    (out/(sid+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--scenario',action='append'); parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--details',action='store_true',help='Save predictions and observed evidence for offline error analysis.')
    args=parser.parse_args()
    for sid in args.scenario or ['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004']: evaluate(args.data,sid,args.out,args.details)
