"""Offline support comparison; never imported by runtime or used as labels."""
import argparse
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from compare_versions import compare
from corridor.state import timestamp


def support_metrics(path):
    assignments=Counter();starts=Counter();unserved=Counter();exposed=Counter()
    previous=set();capacities=Counter();waiting=[];tenures=[];sessions={}
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line);decision=row.get('decision',row)
            now=timestamp(decision['decision_time'])
            selected={a['vehicle_id'] for a in decision['vehicle_actions'] if a['remote_support_required']}
            capacities[len(selected)]+=1
            for v in previous-selected:tenures.append(now-sessions.pop(v))
            for v in selected-previous:starts[v]+=1;sessions[v]=now
            assignments.update(selected)
            unserved.update(a['vehicle_id'] for a in decision['vehicle_actions']
                            if a['motion_action']=='HOLD' and not a['remote_support_required'])
            for item in row.get('diagnostics',[]):
                if isinstance(item,dict) and 'support_allocation' in item:
                    diagnostic=item['support_allocation']
                    exposed.update(diagnostic['exposed_holds'])
                    waiting.extend(diagnostic['waiting_seconds'].values())
            previous=selected
    return dict(assignment_steps=dict(sorted(assignments.items())),assignment_starts=dict(sorted(starts.items())),
                unserved_hold_steps=dict(sorted(unserved.items())),support_histogram=dict(capacities),
                observed_exposed_hold_steps=dict(sorted(exposed.items())),
                completed_assignment_tenure_seconds=dict(Counter(tenures)),
                max_recorded_wait_seconds=max(waiting,default=None),
                note='Assignment tenure is recommendation time, not observed operator work. Old version has no waiting diagnostics.')


def analyze(old_train,new_train,old_public,new_public,out):
    out.mkdir(parents=True,exist_ok=True);reports=[]
    for sid in ['TRAIN-001','TRAIN-002','TRAIN-003','TRAIN-004','PUBLIC-101','PUBLIC-102']:
        train=sid.startswith('TRAIN')
        old=(old_train if train else old_public)/(sid+('-decisions' if train else '')+'.ndjson.gz')
        new=(new_train if train else new_public)/(sid+('-decisions' if train else '')+'.ndjson.gz')
        result=compare(old,new,out,sid)
        result.update(old_support=support_metrics(old),new_support=support_metrics(new))
        if train:
            a=json.loads((old_train/(sid+'.json')).read_text(encoding='utf-8'))
            b=json.loads((new_train/(sid+'.json')).read_text(encoding='utf-8'))
            result['f1']={key:dict(old=a['metrics'][key]['macro'],new=b['metrics'][key]['macro'])
                          for key in ['road_final','odd_active']}
            result['guard_failures']=b['guard_failures']
        unchanged=all(result['counts'].get(key,0)==0 for key in (
            'motion_action_changed','route_segment_ids_changed','speed_limit_kmh_changed','safe_stop_id_changed',
            'state_estimates_changed','source_assessments_changed','vehicle_assessments_changed'))
        assert unchanged,'Unexpected non-support behavior change: '+sid
        reports.append(result)
        print(json.dumps(dict(scenario=sid,counts=result['counts']),ensure_ascii=False),flush=True)
    (out/'comparison.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ['old-train','new-train','old-public','new-public','out']:
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    analyze(args.old_train,args.new_train,args.old_public,args.new_public,args.out)
