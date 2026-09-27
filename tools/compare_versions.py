"""Offline comparison; never imported by the controller or copied into Docker."""
import argparse
import csv
import gzip
import hashlib
import itertools
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from corridor.reference import Reference
from corridor.state import timestamp

MOVING={'CONTINUE','LIMIT_SPEED','REROUTE'}


def rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream: yield json.loads(line)


def decision(record):
    return record.get('decision',record)


def normalized(value):
    return {k:v for k,v in value.items() if k not in ('scenario_id','packet_id')}


def save_csv(path,values):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        if not values: stream.write('no_records\n');return
        writer=csv.DictWriter(stream,fieldnames=list(values[0]));writer.writeheader();writer.writerows(values)


def compare(old,new,out,sid):
    counts=Counter();transitions=Counter();old_counts=Counter();new_counts=Counter();changes=[]
    details=gzip.open(out/(sid+'-changes.ndjson.gz'),'wt',encoding='utf-8')
    try:
        return _compare(old,new,out,sid,counts,transitions,old_counts,new_counts,changes,details)
    finally:
        details.close()


def _compare(old,new,out,sid,counts,transitions,old_counts,new_counts,changes,details):
    for index,pair in enumerate(itertools.zip_longest(rows(old),rows(new))):
        left,right=pair
        if left is None or right is None: raise ValueError('Different packet counts: '+sid)
        left,right=decision(left),decision(right)
        for key in ('packet_id','decision_time','scenario_id'):
            if left[key]!=right[key]: raise ValueError('Misaligned packets: '+sid)
        counts['packets']+=1;counts['different_packets']+=left!=right
        for field,key in [('vehicle_actions','vehicle_id'),('vehicle_assessments','vehicle_id'),('state_estimates','segment_id'),('source_assessments','source_id')]:
            a={r[key]:r for r in left[field]};b={r[key]:r for r in right[field]}
            if a.keys()!=b.keys(): raise ValueError('Different entities: '+sid)
            for entity in a:
                x,y=a[entity],b[entity]
                counts[field+'_records']+=1
                if x!=y:
                    counts[field+'_changed']+=1
                    details.write(json.dumps(dict(packet_id=left['packet_id'],time=left['decision_time'],field=field,entity=entity,old=x,new=y),ensure_ascii=False)+'\n')
                if field=='vehicle_actions':
                    old_counts[x['motion_action']]+=1;new_counts[y['motion_action']]+=1
                    transitions[x['motion_action']+' → '+y['motion_action']]+=1
                    for item in ('motion_action','remote_support_required','route_segment_ids','speed_limit_kmh','safe_stop_id'):
                        counts[item+'_changed']+=x.get(item)!=y.get(item)
                    if any(x.get(k)!=y.get(k) for k in ('motion_action','remote_support_required','route_segment_ids','speed_limit_kmh','safe_stop_id')):
                        changes.append(dict(scenario=sid,packet_id=left['packet_id'],time=left['decision_time'],vehicle=entity,old_action=x['motion_action'],new_action=y['motion_action'],old_support=x['remote_support_required'],new_support=y['remote_support_required'],old_route='|'.join(x.get('route_segment_ids',[])),new_route='|'.join(y.get('route_segment_ids',[])),old_speed=x.get('speed_limit_kmh'),new_speed=y.get('speed_limit_kmh'),old_stop=x.get('safe_stop_id'),new_stop=y.get('safe_stop_id')))
    details.close();save_csv(out/(sid+'-action-changes.csv'),changes)
    return dict(scenario=sid,counts=dict(counts),old_actions=dict(old_counts),new_actions=dict(new_counts),transitions=dict(transitions),old_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),new_sha256=hashlib.sha256(new.read_bytes()).hexdigest())


def structural(ref,d,telemetry):
    """Independent topology/resource checks, based on the case's own reference."""
    failures=Counter();stops=Counter(a.get('safe_stop_id') for a in d['vehicle_actions'] if a['motion_action']=='SAFE_STOP')
    if sum(a['remote_support_required'] for a in d['vehicle_actions'])>min(6,ref.support['max_parallel_sessions']): failures['support_capacity']+=1
    states={r['segment_id']:r['state'] for r in d['state_estimates']}
    for a in d['vehicle_actions']:
        vid=a['vehicle_id'];e=telemetry.get(vid)
        if a['motion_action']=='REROUTE':
            node=ref.segments[e['segment_id']]['to_node'] if e else None
            for sid in a.get('route_segment_ids',[]):
                if sid not in ref.segments: failures['unknown_route_edge']+=1;break
                s=ref.segments[sid]
                if node!=s['from_node']: failures['route_gap']+=1
                node=s['to_node']
                if not ref.compatible(vid,sid): failures['route_mass_av_structure']+=1
                if states[sid] in ('CLOSED','UNKNOWN'): failures['route_estimated_unavailable']+=1
            if node!=ref.hubs[ref.vehicles[vid]['destination_hub_id']]['node_id']: failures['wrong_hub']+=1
        if a['motion_action']=='SAFE_STOP':
            s=ref.stops.get(a.get('safe_stop_id'))
            if not s: failures['unknown_stop']+=1;continue
            if stops[a['safe_stop_id']]>int(s['capacity_vehicles']): failures['stop_capacity']+=1
            if float(ref.vehicles[vid]['gross_mass_t'])>float(s['max_vehicle_mass_t']) or not ref.compatible(vid,s['segment_id']): failures['stop_mass_av_structure']+=1
            if states[s['segment_id']] in ('CLOSED','UNKNOWN'): failures['stop_estimated_unavailable']+=1
    return failures


def synthetic_case(case,root,path,baseline):
    ref=Reference(root/case['reference'])
    failures=Counter();observed=Counter();risks=Counter();latest={};recovery=[];packets=0;restore_ids={}
    name=case['name'];boundary=re.match(r'(ODD-[ABCD])_(visibility_m|wind_mps|gnss_quality|map_age_min|rain_level)_(.+)',name)
    rain_boundary=re.match(r'(ODD-[ABCD])_rain_(\d+)',name)
    if rain_boundary: boundary=re.match(r'(ODD-[ABCD])_(rain_level)_(.+)',rain_boundary[1]+'_rain_level_'+rain_boundary[2])
    target=re.search(r'AV-\d+',case['description'])
    target=target.group(0) if target else None
    baseline_records=list(rows(baseline))
    for p,record,base in itertools.zip_longest(rows(root/case['folder']/'packets.ndjson.gz'),rows(path),baseline_records):
        if p is None or record is None or base is None: raise ValueError('Incomplete case '+case['scenario_id'])
        d=decision(record);b=decision(base);packets+=1
        assert d['packet_id']==p['packet_id'] and d['decision_time']==p['decision_time']
        # Every replay already validated every output. Here we independently check properties.
        for e in p['events']:
            if timestamp(e['event_time'])<=timestamp(p['decision_time']) and timestamp(e['received_time'])<=timestamp(p['decision_time']):
                entity=e.get('vehicle_id') or e.get('segment_id') or e.get('station_id') or e.get('component_id') or e.get('hub_id')
                key=(e['event_type'],entity,e['source_id']);old=latest.get(key)
                if old is None or (timestamp(e['event_time']),e['event_id'])>(timestamp(old['event_time']),old['event_id']): latest[key]=e
        tele=record['telemetry'];failures.update(structural(ref,d,tele))
        assessments={r['vehicle_id']:r for r in d['vehicle_assessments']};actions={r['vehicle_id']:r for r in d['vehicle_actions']}
        for vid,e in tele.items():
            if e:
                proper=latest.get(('VEHICLE_TELEMETRY',vid,e['source_id']))
                if proper is None or e['event_id']!=proper['event_id']: failures['telemetry_causality']+=1
        if case['relation']=='baseline' or name=='duplicate_burst_20x':
            observed['metamorphic_packets']+=1
            if normalized(d)!=normalized(b): failures['metamorphic_difference']+=1
        if p['step']>36:
            recovery.append(dict(step=p['step'],same_actions=all(x==y for x,y in zip(d['vehicle_actions'],b['vehicle_actions'])),hold=sum(a['motion_action']=='HOLD' for a in actions.values())))
            if name=='blackout_recovery':
                for e in p['events']:
                    if e['event_type']=='WEATHER_OBSERVATION': restore_ids.setdefault(e['source_id'],set()).add(e['event_id'])
                for source in d['source_assessments']:
                    if len(restore_ids.get(source['source_id'],()))>=3 and source['status']=='OK' and not source['fault_types']:
                        observed['weather_recovery_after_3_updates']+=1
                        if source['source_id'] in record['excluded']: failures['weather_still_excluded_after_3_updates']+=1
        if name=='empty_from_start':
            if any(s['state']!='UNKNOWN' for s in d['state_estimates']): failures['empty_invented_road']+=1
            if any(s['odd_status']!='UNKNOWN' for s in assessments.values()): failures['empty_invented_odd']+=1
            if any(a['motion_action']!='HOLD' for a in actions.values()): failures['empty_motion']+=1
        if not 9<=p['step']<=36: continue
        if boundary and target:
            profile,field,side=boundary.groups();odd=assessments[target];a=actions[target];e=tele[target]
            code={'visibility_m':'VISIBILITY','gnss_quality':'GNSS','map_age_min':'MAP_AGE','rain_level':'RAIN'}.get(field)
            expected=None
            if field in ('gnss_quality','map_age_min') and e:
                value=e[field]
                if field=='map_age_min': value+=max(0,timestamp(p['decision_time'])-timestamp(e['event_time']))/60
                threshold=ref.profiles[profile]['min_gnss_quality' if field=='gnss_quality' else 'max_map_age_min']
                expected=value<threshold if field=='gnss_quality' else value>threshold
                if field=='map_age_min' and side in ('equal','below') and expected: observed['raw_in_limit_but_aged_map']+=1
            elif field in ('visibility_m','rain_level','wind_mps'):
                weather=[x for (k,_,_),x in latest.items() if k=='WEATHER_OBSERVATION' and 0<=timestamp(p['decision_time'])-timestamp(x['event_time'])<=30]
                values={x[field] for x in weather}
                # Mutations set every station identically. Mixed/restored readings are not a complete oracle.
                if len(values)==1:
                    value=next(iter(values));threshold=ref.profiles[profile][{'visibility_m':'min_visibility_m','rain_level':'max_rain_level','wind_mps':'max_crosswind_mps'}[field]]
                    expected=value<threshold if field=='visibility_m' else value>threshold
            if expected is not None:
                observed['boundary_checks']+=1
                if field=='wind_mps':
                    if expected:
                        observed['crosswind_ambiguous']+=1
                        if odd['odd_status']=='COMPLIANT' or a['motion_action'] in MOVING: failures['wind_uncertainty_ignored']+=1
                    if 'WIND' in odd['violation_codes']: failures['invented_wind_code']+=1
                elif (code in odd['violation_codes'])!=expected: failures['boundary_'+field]+=1
                if expected and field!='wind_mps' and a['motion_action'] in MOVING: failures['moving_at_boundary_violation']+=1
        if name in ('remote_pool_72_requests','safe_stops_capacity_one'):
            for vid,a in actions.items():
                if tele.get(vid) and tele[vid]['gnss_quality']==0:
                    observed['gnss_zero_checks']+=1
                    if 'GNSS' not in assessments[vid]['violation_codes'] or a['motion_action'] in MOVING: failures['gnss_zero_unsafe']+=1
            selected=[int(ref.vehicles[v]['cargo_priority']) for v,a in actions.items() if a['remote_support_required']]
            waiting=[int(ref.vehicles[v]['cargo_priority']) for v,a in actions.items() if not a['remote_support_required']]
            if len(selected)!=min(6,ref.support['max_parallel_sessions']): failures['unfilled_support_pool']+=1
            if selected and waiting and max(selected)>min(waiting): failures['cargo_priority_inverted']+=1
        if name in ('remote_requests_6','remote_requests_7'):
            requested=[v for v,e in tele.items() if e and e.get('autonomy_state')=='REMOTE_REQUESTED']
            selected=sum(actions[v]['remote_support_required'] for v in requested)
            observed['explicit_remote_request_packets']+=1
            if selected<min(len(requested),6): failures['remote_requests_not_served']+=1
        if name=='v2x_outage_profiles':
            for vid,odd in assessments.items():
                if not ref.profiles[ref.vehicles[vid]['odd_profile_id']]['v2x_required'] and 'V2X' in odd['violation_codes']: failures['unrequired_v2x']+=1
                if ref.profiles[ref.vehicles[vid]['odd_profile_id']]['v2x_required']:
                    observed['required_v2x_checks']+=1
                    if odd['odd_status']=='COMPLIANT' or actions[vid]['motion_action'] in MOVING:
                        risks['v2x_mutation_unrecognized']+=1
                        if actions[vid]['motion_action'] in MOVING: risks['moving_during_v2x_mutation']+=1
                        sid=tele[vid]['segment_id'] if tele.get(vid) else None
                        covering=[r for r,covered in ref.rsu_segments.items() if sid in covered]
                        health=[latest.get(('INFRASTRUCTURE_HEALTH',r,r)) for r in covering]
                        if covering and all(e and (e.get('status')=='NO_HEARTBEAT' or e.get('network_packet_loss_pct',0)>=20) for e in health):
                            failures['observed_failed_v2x_allowed_motion']+=1
        if name=='source_outage' and p['step']>=33:
            source=next(s for s in d['source_assessments'] if s['source_id']=='DET-001')
            observed['source_outage_after_120s_checks']+=1
            if source['status']!='FAILED' or 'OUTAGE' not in source['fault_types']: failures['source_outage_missed']+=1
        if name=='stale_twin_high_confidence':
            source=next(s for s in d['source_assessments'] if s['source_id']=='DT-CORE')
            mutated=[e for (kind,_,_),e in latest.items() if kind=='DIGITAL_TWIN_SEGMENT' and e.get('snapshot_age_sec',0)>=600]
            if mutated:
                observed['stale_twin_checks']+=1
                if 'STALE' not in source['fault_types']: failures['stale_twin_missed']+=1
        if name=='invalid_signature':
            mutated=[e for e in p['events'] if e.get('signature_valid') is False]
            for e in mutated:
                source=next(s for s in d['source_assessments'] if s['source_id']==e['source_id'])
                observed['invalid_signature_checks']+=1
                if 'BYZANTINE' not in source['fault_types']: failures['invalid_signature_missed']+=1
        if name in ('twin_false_open','no_open_route'):
            forbidden={'S014'} if name=='twin_false_open' else set(ref.segments)
            states={x['segment_id']:x['state'] for x in d['state_estimates']}
            known_closed=set()
            for (kind,entity,source),e in latest.items():
                if kind not in ('ROAD_OBSERVATION','DIGITAL_TWIN_SEGMENT','V2X_MESSAGE') or entity not in forbidden or not e.get('signature_valid',True): continue
                cycle=float(ref.sources[source]['expected_period_sec'])*(max(1,len(ref.rsu_segments.get(source,()))) if kind=='V2X_MESSAGE' else 1)
                if timestamp(p['decision_time'])-timestamp(e['event_time'])>3*cycle: continue
                if e.get('closure_state',e.get('lane_status'))=='CLOSED' or e.get('lane_count_open',e.get('lane_count_open_estimate'))==0: known_closed.add(entity)
            for sid in forbidden:
                observed['mutated_closure_checks']+=1
                if states[sid] not in ('CLOSED','UNKNOWN'):
                    risks['mutated_closure_predicted_available']+=1
                    if sid in known_closed: failures['observed_closure_predicted_available']+=1
            for vid,a in actions.items():
                if any(s in forbidden for s in a.get('route_segment_ids',[])): risks['route_through_mutated_closure']+=1
                if any(s in known_closed for s in a.get('route_segment_ids',[])): failures['route_through_observed_closure']+=1
                if a['motion_action']=='SAFE_STOP' and ref.stops[a['safe_stop_id']]['segment_id'] in forbidden:
                    risks['stop_on_mutated_closure']+=1
                    if ref.stops[a['safe_stop_id']]['segment_id'] in known_closed: failures['stop_on_observed_closure']+=1
                if tele.get(vid) and tele[vid]['segment_id'] in forbidden and a['motion_action'] in MOVING:
                    risks['moving_on_mutated_closure']+=1
                    if tele[vid]['segment_id'] in known_closed: failures['moving_on_observed_closure']+=1
        if name=='tunnel_odd_c':
            for vid,odd in assessments.items():
                if ref.vehicles[vid]['odd_profile_id']=='ODD-C':
                    if 'STRUCTURE' not in odd['violation_codes'] or actions[vid]['motion_action'] in MOVING: failures['tunnel_odd_c']+=1
    return dict(scenario=case['scenario_id'],name=name,packets=packets,failures=dict(failures),risk_indicators=dict(risks),checked=dict(observed),last_recovery=recovery[-1] if recovery else None,
                limitation='Partial mutation/property oracle; not full ground truth, no official utility or F1. Recovery window is finite; absence of infinite HOLD cannot be proved.')


def main(args):
    out=args.root/'analysis';out.mkdir(exist_ok=True)
    public=[]
    for sid in ([] if args.scope=='synthetic' else ['PUBLIC-101','PUBLIC-102']):
        r=compare(args.root/'public-old'/(sid+'.ndjson.gz'),args.root/'public-new'/(sid+'.ndjson.gz'),out,sid)
        r['old_container']=json.loads((args.root/'public-old'/(sid+'-container.json')).read_text(encoding='utf-8'))
        r['new_container']=json.loads((args.root/'public-new'/(sid+'-container.json')).read_text(encoding='utf-8'))
        public.append(r)
    if public:
        (out/'public_comparison.json').write_text(json.dumps(public,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.scope=='public': return
    synthetic=[];manifest=json.loads((args.synthetic/'manifest.json').read_text(encoding='utf-8'))
    for case in manifest['cases']:
        sid=case['scenario_id'];entry=dict(scenario=sid,name=case['name'])
        entry['comparison']=compare(args.root/'synthetic-old'/(sid+'.ndjson.gz'),args.root/'synthetic-new'/(sid+'.ndjson.gz'),out,sid)
        for version in ['old','new']:
            entry[version]=synthetic_case(case,args.synthetic,args.root/('synthetic-'+version)/(sid+'.ndjson.gz'),args.root/('synthetic-'+version)/'SYN-001.ndjson.gz')
            entry[version]['replay']=json.loads((args.root/('synthetic-'+version)/(sid+'.json')).read_text(encoding='utf-8'))
        synthetic.append(entry)
        print(json.dumps(dict(case=sid,old=entry['old']['failures'],new=entry['new']['failures'])),flush=True)
    (out/'synthetic_comparison.json').write_text(json.dumps(synthetic,ensure_ascii=False,indent=2),encoding='utf-8')
    save_csv(out/'synthetic_summary.csv',[dict(scenario=x['scenario'],name=x['name'],old_failures=json.dumps(x['old']['failures']),new_failures=json.dumps(x['new']['failures']),changed_actions=x['comparison']['counts'].get('motion_action_changed',0),old_max_ms=x['old']['replay']['latency_ms']['max'],new_max_ms=x['new']['replay']['latency_ms']['max']) for x in synthetic])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--synthetic',type=Path,required=True)
    parser.add_argument('--scope',choices=['all','public','synthetic'],default='all')
    main(parser.parse_args())
