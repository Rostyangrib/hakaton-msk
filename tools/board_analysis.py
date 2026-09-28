"""Offline board-test properties; expectations/latent truth never enter runtime."""
import argparse
import gzip
import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path
from compare_versions import structural
from corridor.reference import Reference
from corridor.state import timestamp

MOVING={'CONTINUE','LIMIT_SPEED','REROUTE'}


def rows(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:yield json.loads(line)


def assess(root,results):
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    control=list(rows(results/'BOARD-001.ndjson.gz'))
    output=[]
    for case in manifest['cases']:
        sid=case['scenario_id'];directory=root/case['folder'];ref=Reference(root/case['reference'])
        assert hashlib.sha256((directory/'packets.ndjson.gz').read_bytes()).hexdigest()==case['sha256']
        failures=Counter();checks=Counter();risks=Counter();actions=Counter();targets=set(case['targets'])
        recovery={};latest={};first_bad={};first_stop={};last={}
        latent=json.loads((directory/'latent_truth.json').read_text(encoding='utf-8')) if (directory/'latent_truth.json').exists() else None
        count=0
        for packet,record,base in itertools.zip_longest(rows(directory/'packets.ndjson.gz'),rows(results/(sid+'.ndjson.gz')),control):
            assert packet and record and base, 'Incomplete '+sid
            count+=1;d=record['decision'];now=timestamp(packet['decision_time'])
            assert d['packet_id']==packet['packet_id'] and d['decision_time']==packet['decision_time']
            for e in packet['events']:
                if e['event_type']=='VEHICLE_TELEMETRY' and timestamp(e['event_time'])<=now and timestamp(e['received_time'])<=now:
                    vid=e['vehicle_id'];old=latest.get(vid)
                    if old is None or (timestamp(e['event_time']),e['event_id'])>(timestamp(old['event_time']),old['event_id']):latest[vid]=e
            tele=record['telemetry'];failures.update(structural(ref,d,tele))
            odd={r['vehicle_id']:r for r in d['vehicle_assessments']};act={r['vehicle_id']:r for r in d['vehicle_actions']}
            baseline={r['vehicle_id']:r for r in base['decision']['vehicle_assessments']}
            for vid in ref.vehicles:
                if vid not in targets and odd[vid]!=baseline[vid]:failures['unrelated_vehicle_assessment_changed']+=1
            for vid in targets:
                e=tele.get(vid);a=act[vid];o=odd[vid];actions[a['motion_action']]+=1;last[vid]=(a['motion_action'],o['odd_status'])
                if e and (vid not in latest or e['event_id']!=latest[vid]['event_id']):failures['telemetry_causality']+=1
                fresh=e and 0<=now-timestamp(e['event_time'])<=15
                if not fresh:
                    checks['stale_checks']+=1
                    if a['motion_action'] in MOVING:failures['moving_with_stale_board']+=1
                    continue
                profile=ref.profiles[ref.vehicles[vid]['odd_profile_id']]
                if e['gnss_quality']<profile['min_gnss_quality']:
                    checks['gnss_checks']+=1
                    if 'GNSS' not in o['violation_codes']:failures['missing_gnss_violation']+=1
                    if a['motion_action'] in MOVING:failures['moving_with_gnss_failure']+=1
                bad=(e['perception_health']<.5 or (e['perception_health']<.9 and e['autonomy_state'] in ('DEGRADED','REMOTE_REQUESTED'))
                     or e['localization_confidence']<.5 or e['communication_latency_ms']>=2000 or e['packet_loss_pct_10s']>=50)
                old=recovery.get(vid)
                if bad:
                    recovery[vid]=dict(clear=0,time=timestamp(e['event_time']),eid=e['event_id'])
                    first_bad.setdefault(vid,packet['step'])
                elif old and timestamp(e['event_time'])>old['time'] and e['event_id']!=old['eid']:
                    healthy=(e['perception_health']>=.9 and e['localization_confidence']>=.8 and e['communication_latency_ms']<1000 and e['packet_loss_pct_10s']<20)
                    old['clear']=old['clear']+1 if healthy else 0;old['time']=timestamp(e['event_time']);old['eid']=e['event_id']
                    if old['clear']>=3:recovery.pop(vid)
                if vid in recovery:
                    checks['board_or_recovery_checks']+=1
                    if a['motion_action'] in MOVING:failures['moving_with_board_or_pending_recovery']+=1
                    if o['odd_status']=='COMPLIANT':failures['board_failure_certified_compliant']+=1
                    if a['motion_action'] not in MOVING:first_stop.setdefault(vid,packet['step'])
                if bad and 'VISIBILITY' in o['violation_codes']:failures['fabricated_visibility_code']+=1
                if latent and 9<=packet['step']<=36 and a['motion_action'] in MOVING:
                    risks['moving_in_latent_window']+=1
            if case['name']=='control_good_weather':
                if d['vehicle_actions']!=base['decision']['vehicle_actions']:failures['control_changed']+=1
        assert count==48
        output.append(dict(scenario=sid,name=case['name'],packets=count,failures=dict(failures),checks=dict(checks),risk_indicators=dict(risks),
                           actions=dict(actions),first_bad=first_bad,first_stopped=first_stop,last_target_states=last,
                           limitation='Manual board-health policy properties, not official utility or a full physical simulator. Latent visibility is not a runtime input.'))
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ['root','old','new','out']:p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();old=assess(a.root,a.old);new=assess(a.root,a.new)
    a.out.write_text(json.dumps(dict(old=old,new=new),ensure_ascii=False,indent=2),encoding='utf-8')
    for x,y in zip(old,new):print(json.dumps(dict(scenario=y['scenario'],old_failures=x['failures'],new_failures=y['failures'],risk_indicators=y['risk_indicators']),ensure_ascii=False))
    if any(x['failures'] for x in new):raise SystemExit('Board properties failed')
