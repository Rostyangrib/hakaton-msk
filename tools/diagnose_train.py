"""Offline evidence for road/ODD errors. Labels never enter the controller."""
import argparse
import csv
import gzip
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from corridor.reference import Reference
from corridor.state import timestamp
from evaluate import csv_rows


def diagnose(data,results,out):
    ref=Reference(data/'01_reference');out.mkdir(parents=True,exist_ok=True)
    for sid in ['TRAIN-003','TRAIN-004']:
        directory=data/'02_train'/sid
        roads={(r['timestamp'],r['segment_id']):r for r in csv_rows(directory/'labels/01_segment_state.csv.gz')}
        vehicles={(r['timestamp'],r['vehicle_id']):r for r in csv_rows(directory/'labels/02_vehicle_odd.csv.gz')}
        counts=Counter();examples=defaultdict(list);detail=[]
        with gzip.open(results/(sid+'-decisions.ndjson.gz'),'rt',encoding='utf-8') as f:
            for line in f:
                s=json.loads(line);d=s['decision'];now=timestamp(d['decision_time']);events=s['observations']
                telemetry={}
                for e in events:
                    if e['event_type']=='VEHICLE_TELEMETRY':
                        old=telemetry.get(e['vehicle_id'])
                        if old is None or timestamp(e['event_time'])>timestamp(old['event_time']): telemetry[e['vehicle_id']]=e
                for estimate in d['state_estimates']:
                    truth=roads[(d['decision_time'],estimate['segment_id'])]
                    if truth['true_state']!=estimate['state']:
                        key='road:'+truth['true_state']+'->'+estimate['state'];counts[key]+=1
                        if len(examples[key])<4:
                            examples[key].append(dict(time=d['decision_time'],truth=truth,fusion=s['fused_roads'][estimate['segment_id']],events=[e for e in events if e.get('segment_id')==estimate['segment_id'] and e['event_type'] in ('ROAD_OBSERVATION','V2X_MESSAGE','DIGITAL_TWIN_SEGMENT')]))
                cache={tuple(w['position']):w['value'] for w in s['weather_cache']}
                for estimate in d['vehicle_assessments']:
                    vid=estimate['vehicle_id'];truth=vehicles[(d['decision_time'],vid)]
                    if truth['active']!='1': continue
                    true='COMPLIANT' if truth['odd_compliant']=='1' else 'VIOLATED'
                    if true==estimate['odd_status']: continue
                    e=telemetry.get(vid)
                    if not e: continue
                    pos=ref.position(e['segment_id'],e['offset_m']);weather=cache.get(pos)
                    wx=[]
                    for w in events:
                        if w['event_type']!='WEATHER_OBSERVATION': continue
                        station=ref.weather[w['station_id']];dist=math.dist(pos,(float(station['x_m']),float(station['y_m'])))
                        if dist<=float(station['coverage_radius_m']) and 0<=now-timestamp(w['event_time'])<=3*float(station['sampling_period_sec']):
                            wx.append(dict(source=w['source_id'],distance=round(dist),visibility=w['visibility_m'],rain=w['rain_level'],wind=w['wind_mps'],excluded=w['source_id'] in s['excluded_sources'],event_id=w['event_id']))
                    wx.sort(key=lambda w:w['distance'])
                    covering=[r for r,segments in ref.rsu_segments.items() if e['segment_id'] in segments]
                    health=[x for x in events if x['event_type']=='INFRASTRUCTURE_HEALTH' and x.get('component_id') in covering]
                    row=dict(time=d['decision_time'],vehicle=vid,profile=ref.vehicles[vid]['odd_profile_id'],segment=e['segment_id'],true=true,pred=estimate['odd_status'],true_codes=truth['violation_codes'],pred_codes=estimate['violation_codes'],telemetry=e,weather=weather,stations=wx,road_truth=roads[(d['decision_time'],e['segment_id'])],health=health,source_status=[x for x in d['source_assessments'] if x['source_id'] in covering],memory=('weather_warning_memory:'+vid) in s['diagnostics'])
                    key='odd:'+true+'->'+estimate['odd_status'];counts[key]+=1
                    if len(examples[key])<4: examples[key].append(row)
                    counts['odd_codes:'+truth['violation_codes']+'->'+'|'.join(estimate['violation_codes'])]+=1
                    if row['memory']: counts['memory:'+key]+=1
                    detail.append(row)
        (out/(sid+'-diagnosis.json')).write_text(json.dumps(dict(counts=dict(counts),examples=dict(examples)),ensure_ascii=False,indent=2),encoding='utf-8')
        with gzip.open(out/(sid+'-odd-errors.ndjson.gz'),'wt',encoding='utf-8') as f:
            for r in detail:f.write(json.dumps(r,ensure_ascii=False)+'\n')
        print(sid,json.dumps(dict(counts),ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--results',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();diagnose(a.data,a.results,a.out)
