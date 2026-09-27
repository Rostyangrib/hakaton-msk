"""Offline evidence-based TRAIN analysis; not imported or packaged by runtime."""
import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from corridor.reference import Reference
from corridor.state import timestamp
from evaluate import csv_rows, macro_f1


def save_csv(path, rows):
    if not rows:
        path.write_text('no_records\n', encoding='utf-8-sig')
        return
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def episodes(rows, field='gate_odd', minimum=3):
    """Consecutive flagged packets per vehicle; a gap interrupts an episode."""
    active, output = {}, []
    def finish(vid):
        values = active.pop(vid)
        if len(values) >= minimum:
            output.append(dict(scenario=values[0]['scenario'],vehicle_id=vid,start=values[0]['timestamp'],
                end=values[-1]['timestamp'],steps=len(values),span_sec=timestamp(values[-1]['timestamp'])-timestamp(values[0]['timestamp']),
                codes='|'.join(sorted({c for r in values for c in r['true_codes'].split('|') if c})),
                predicted_statuses=json.dumps(dict(Counter(r['pred_odd'] for r in values))),
                actions=json.dumps(dict(Counter(r['action'] for r in values))),
                first_packet=values[0]['packet_id'],last_packet=values[-1]['packet_id']))
    for r in rows:
        vid=r['vehicle_id']
        if r[field]:
            if vid in active and r['step'] != active[vid][-1]['step']+1: finish(vid)
            active.setdefault(vid,[]).append(r)
        elif vid in active: finish(vid)
    for vid in list(active): finish(vid)
    return output


def analyze(data, results, out):
    out.mkdir(parents=True,exist_ok=True)
    ref=Reference(data/'01_reference')
    all_vehicle, road_errors, source_errors, road_groups = [],[],[],defaultdict(Counter)
    summaries, gate_episodes, moving_episodes, window_rows, confusion_rows = [],[],[],[],[]
    for sid in ['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004']:
        report=json.loads((results/(sid+'.json')).read_text(encoding='utf-8'))
        directory=data/'02_train'/sid
        rt={(r['timestamp'],r['segment_id']):r for r in csv_rows(directory/'labels/01_segment_state.csv.gz')}
        vt={(r['timestamp'],r['vehicle_id']):r for r in csv_rows(directory/'labels/02_vehicle_odd.csv.gz')}
        faults=csv_rows(directory/'labels/03_source_faults.csv')
        windows=csv_rows(directory/'labels/05_evaluation_windows.csv')
        slices={w['window_id']:dict(road=Counter(),odd=Counter(),actions=Counter()) for w in windows}
        vehicles=[]; truth_route_issues=[]; snapshot_issues=[]; packet_support=Counter(); raw_road=Counter()
        with gzip.open(results/(sid+'-decisions.ndjson.gz'),'rt',encoding='utf-8') as stream:
            for step,line in enumerate(stream):
                snapshot=json.loads(line); d=snapshot['decision']; stamp=d['decision_time']
                estimates={r['segment_id']:r for r in d['state_estimates']}
                assessments={r['vehicle_id']:r for r in d['vehicle_assessments']}
                sources={r['source_id']:r for r in d['source_assessments']}
                telemetry={}
                for e in snapshot['observations']:
                    if e['event_type']=='VEHICLE_TELEMETRY':
                        old=telemetry.get(e['vehicle_id'])
                        if old is None or (timestamp(e['event_time']),e['event_id'])>(timestamp(old['event_time']),old['event_id']): telemetry[e['vehicle_id']]=e
                weather={tuple(r['position']):r['value'] for r in snapshot['weather_cache']}
                current_windows=[w for w in windows if timestamp(w['start_time'])<=timestamp(stamp)<timestamp(w['end_time'])]
                packet_support[sum(a['remote_support_required'] for a in d['vehicle_actions'])]+=1
                assigned=Counter(a.get('safe_stop_id') for a in d['vehicle_actions'] if a['motion_action']=='SAFE_STOP')
                for stop,n in assigned.items():
                    if stop not in ref.stops or n>int(ref.stops[stop]['capacity_vehicles']): snapshot_issues.append(dict(packet_id=d['packet_id'],type='stop_capacity',target=stop,count=n))
                if sum(a['remote_support_required'] for a in d['vehicle_actions'])>min(6,ref.support['max_parallel_sessions']):
                    snapshot_issues.append(dict(packet_id=d['packet_id'],type='support_capacity',target='',count=0))
                for segment, pred in estimates.items():
                    truth=rt[stamp,segment]; pair=truth['true_state'],pred['state']; raw_road[pair]+=1
                    road_groups[sid,segment][pair]+=1
                    for w in current_windows: slices[w['window_id']]['road'][pair]+=1
                    if pair[0]!=pair[1]:
                        fused=snapshot['fused_roads'][segment]
                        road_errors.append(dict(scenario=sid,packet_id=d['packet_id'],timestamp=stamp,segment_id=segment,
                            true_state=pair[0],pred_state=pair[1],confidence=pred['confidence'],true_speed=truth['true_speed_kmh'],pred_speed=fused['speed'],
                            true_occupancy=truth['true_occupancy_pct'],true_queue_vehicles=truth['queue_vehicles'],pred_queue_m=fused['queue'],
                            true_lanes=truth['lanes_open'],pred_lanes=fused['lanes'],incident_id=truth['incident_id']))
                for source, pred in sources.items():
                    true_codes={r['fault_type'] for r in faults if r['source_id']==source and timestamp(r['start_time'])<=timestamp(stamp)<timestamp(r['end_time'])}
                    if true_codes!=set(pred['fault_types']):
                        source_errors.append(dict(scenario=sid,packet_id=d['packet_id'],timestamp=stamp,source_id=source,
                            true_codes='|'.join(sorted(true_codes)),pred_codes='|'.join(pred['fault_types']),status=pred['status'],trust=pred['trust_score'],
                            excluded=source in snapshot['excluded_sources']))
                for a in d['vehicle_actions']:
                    vid=a['vehicle_id']; truth=vt[stamp,vid]; pred=assessments[vid]; e=telemetry.get(vid)
                    segment=truth['segment_id']; road=rt[stamp,segment]; observed_weather=None
                    if e:
                        try: observed_weather=weather.get(ref.position(e['segment_id'],e['offset_m']))
                        except ValueError: pass
                    active=truth['active']=='1'; violation=active and truth['odd_compliant']=='0'
                    gate=violation and a['motion_action'] in ('CONTINUE','NO_ACTION') and not a['remote_support_required']
                    moving=violation and a['motion_action'] in ('CONTINUE','LIMIT_SPEED','REROUTE')
                    row=dict(scenario=sid,step=step,packet_id=d['packet_id'],timestamp=stamp,vehicle_id=vid,profile=ref.vehicles[vid]['odd_profile_id'],
                        priority=int(ref.vehicles[vid]['cargo_priority']),active=active,segment_id=segment,
                        true_odd='COMPLIANT' if truth['odd_compliant']=='1' else 'VIOLATED',pred_odd=pred['odd_status'],
                        true_codes=truth['violation_codes'],pred_codes='|'.join(pred['violation_codes']),odd_confidence=pred['confidence'],
                        reference_action=truth['reference_action_class'],action=a['motion_action'],support=a['remote_support_required'],
                        support_count=sum(x['remote_support_required'] for x in d['vehicle_actions']),safe_stop=a.get('safe_stop_id',''),
                        gate_odd=gate,moving_outside_odd=moving,true_visibility=float(road['visibility_m']),true_rain=int(road['rain_level']),
                        true_v2x=road['v2x_available'],true_surface=road['road_surface'],pred_road=estimates[segment]['state'],
                        fused_visibility=observed_weather['visibility_m'] if observed_weather else None,
                        fused_rain=observed_weather['rain_level'] if observed_weather else None,
                        weather_confidence=observed_weather['confidence'] if observed_weather else None,
                        visibility_range=json.dumps(observed_weather['ranges']['visibility_m']) if observed_weather else '',
                        telemetry_age=timestamp(stamp)-timestamp(e['event_time']) if e else None,
                        communication_latency_ms=e.get('communication_latency_ms') if e else None,
                        packet_loss_pct_10s=e.get('packet_loss_pct_10s') if e else None,
                        gnss_quality=e.get('gnss_quality') if e else None,
                        map_age_min=e.get('map_age_min') if e else None,
                        perception_health=e.get('perception_health') if e else None,
                        localization_confidence=e.get('localization_confidence') if e else None,
                        autonomy_state=e.get('autonomy_state') if e else None,
                        reasons='|'.join(a['rationale_codes']))
                    vehicles.append(row)
                    if active:
                        for w in current_windows:
                            slices[w['window_id']]['odd'][row['true_odd'],row['pred_odd']]+=1
                            slices[w['window_id']]['actions'][row['action']]+=1
                    if a['motion_action']=='REROUTE':
                        node=ref.segments[e['segment_id']]['to_node'] if e else None
                        for edge in a['route_segment_ids']:
                            if edge not in ref.segments or node!=ref.segments[edge]['from_node'] or not ref.compatible(vid,edge) or rt[stamp,edge]['true_state']=='CLOSED' or estimates[edge]['state']=='UNKNOWN':
                                truth_route_issues.append(dict(packet_id=d['packet_id'],vehicle_id=vid,type='route',edge=edge))
                            if edge in ref.segments: node=ref.segments[edge]['to_node']
                        if node!=ref.hubs[ref.vehicles[vid]['destination_hub_id']]['node_id']: truth_route_issues.append(dict(packet_id=d['packet_id'],vehicle_id=vid,type='destination',edge=''))
                    if a['motion_action']=='SAFE_STOP':
                        stop=ref.stops.get(a.get('safe_stop_id'))
                        if stop is None or float(ref.vehicles[vid]['gross_mass_t'])>float(stop['max_vehicle_mass_t']) or not ref.compatible(vid,stop['segment_id']) or rt[stamp,stop['segment_id']]['true_state']=='CLOSED':
                            truth_route_issues.append(dict(packet_id=d['packet_id'],vehicle_id=vid,type='safe_stop',edge=''))
        all_vehicle.extend(vehicles)
        gate_episodes.extend(episodes(vehicles))
        moving_episodes.extend(episodes(vehicles,'moving_outside_odd'))
        active_rows=[r for r in vehicles if r['active']]
        violated=[r for r in active_rows if r['true_odd']=='VIOLATED']
        summary=dict(scenario=sid,packets=report['packets'],vehicle_steps=len(vehicles),active_steps=len(active_rows),violated_steps=len(violated),
            inactive_steps=len(vehicles)-len(active_rows),inactive_actions=dict(Counter(r['action'] for r in vehicles if not r['active'])),
            gate_odd_steps=sum(r['gate_odd'] for r in vehicles),gate_odd_episodes=len(episodes(vehicles)),
            moving_outside_odd_steps=sum(r['moving_outside_odd'] for r in vehicles),moving_outside_odd_episodes=len(episodes(vehicles,'moving_outside_odd')),
            gate_vehicles=sorted({r['vehicle_id'] for r in vehicles if r['gate_odd']}),
            violation_actions=dict(Counter(r['action'] for r in violated)),violation_pred_status=dict(Counter(r['pred_odd'] for r in violated)),
            violation_without_support=sum(not r['support'] for r in violated),support_histogram=dict(sorted(packet_support.items())),
            route_stop_truth_issues=truth_route_issues,snapshot_issues=snapshot_issues,
            odd_errors_by_profile={p:dict(Counter((r['true_odd']+' -> '+r['pred_odd']) for r in active_rows if r['profile']==p)) for p in sorted(ref.profiles)},
            reference_action_comparison=dict(Counter(r['reference_action']+' -> '+r['action'] for r in active_rows)),
            remote_reference_steps=sum(r['reference_action']=='REMOTE_SUPPORT' for r in active_rows),
            remote_reference_with_support=sum(r['reference_action']=='REMOTE_SUPPORT' and r['support'] for r in active_rows))
        assert summary['gate_odd_steps']==report['labelled_odd_unsafe_steps']
        assert summary['gate_odd_episodes']==report['labelled_odd_critical_episodes']
        assert len(vehicles)==72*report['packets']
        summaries.append(summary)
        for metric,value in report['metrics'].items():
            for pair,n in value['confusion'].items():
                truth,pred=pair.split(' → '); confusion_rows.append(dict(scenario=sid,metric=metric,truth=truth,prediction=pred,count=n))
        for w in windows:
            values=slices[w['window_id']]
            window_rows.append(dict(scenario=sid,window_id=w['window_id'],target=w['target_id'],start=w['start_time'],end=w['end_time'],
                road_macro_f1=macro_f1(values['road'])['macro'],odd_macro_f1=macro_f1(values['odd'])['macro'],actions=json.dumps(dict(values['actions']))))
    save_csv(out/'vehicle_steps.csv',all_vehicle)
    save_csv(out/'odd_errors.csv',[r for r in all_vehicle if r['active'] and r['true_odd']!=r['pred_odd']])
    save_csv(out/'road_errors.csv',road_errors)
    save_csv(out/'source_errors.csv',source_errors)
    save_csv(out/'critical_odd_episodes.csv',gate_episodes)
    save_csv(out/'moving_outside_odd_episodes.csv',moving_episodes)
    save_csv(out/'confusion.csv',confusion_rows)
    save_csv(out/'evaluation_windows.csv',window_rows)
    segments=[]
    for (sid,segment),counts in sorted(road_groups.items()):
        n=sum(counts.values()); errors=sum(v for (t,p),v in counts.items() if t!=p)
        if errors: segments.append(dict(scenario=sid,segment_id=segment,steps=n,errors=errors,error_share=errors/n,confusion=json.dumps(macro_f1(counts)['confusion'])))
    save_csv(out/'road_errors_by_segment.csv',sorted(segments,key=lambda r:-r['errors']))
    (out/'safety_analysis.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps([dict(scenario=r['scenario'],gate_steps=r['gate_odd_steps'],gate_episodes=r['gate_odd_episodes'],moving_steps=r['moving_outside_odd_steps']) for r in summaries]))
    return summaries


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    analyze(args.data,args.results,args.out)
