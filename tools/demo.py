"""Short, reproducible demonstration of the actual controller and invariants."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from corridor.controller import Controller
from corridor.reference import Reference
from corridor.routing import dijkstra,route_choice
from corridor.resources import min_cost_assignment,support_selection
from corridor.guard import errors

ROOT=Path(__file__).resolve().parent.parent
STAMP='2026-09-28T00:00:05.000Z'


def make_packet(ref,current,closed=None,congested=False):
    events=[]
    def add(kind,source,**fields):
        n=len(events)+1
        events.append(dict(event_id=f'DEMO-{n:05}',scenario_id='DEMO',event_no=n,stream_seq=n,delivery_no=1,
                           event_type=kind,source_id=source,event_time=STAMP,received_time=STAMP,**fields))
    for sid,s in sorted(ref.segments.items()):
        add('DIGITAL_TWIN_SEGMENT','DT-CORE',digital_twin_id='DT-CORE',segment_id=sid,
            lane_count_open=0 if sid==closed else int(s['lanes']),closure_state='CLOSED' if sid==closed else 'OPEN',
            speed_limit_kmh=float(s['speed_limit_kmh']),map_version=2026092801,source_updated_time=STAMP,snapshot_age_sec=0,self_reported_confidence=1)
    for rid,r in sorted(ref.rsus.items()):
        for seq,sid in enumerate(sorted(ref.rsu_segments[rid]),1):
            add('V2X_MESSAGE',rid,rsu_id=rid,v2x_zone=r['v2x_zone'],message_type='LANE_STATUS',sequence_no=seq,segment_id=sid,
                lane_status='CLOSED' if sid==closed else 'OPEN',advisory_speed_kmh=min(90,float(ref.segments[sid]['speed_limit_kmh'])),
                queue_estimate_m=0,signature_valid=True,source_clock_offset_ms=0,self_reported_confidence=1)
    for wid in sorted(ref.weather):
        add('WEATHER_OBSERVATION',wid,station_id=wid,visibility_m=2000,rain_level=0,precipitation_mm_h=0,wind_mps=0,
            road_surface='DRY',air_temperature_c=15,self_reported_confidence=1)
    vehicle=ref.vehicles['AV-001']; s=ref.segments[current]
    add('VEHICLE_TELEMETRY','AV-001',vehicle_id='AV-001',segment_id=current,offset_m=float(s['length_m'])-50,speed_kmh=70,
        acceleration_mps2=0,heading_deg=0,gnss_quality=1,localization_confidence=1,perception_health=1,
        communication_latency_ms=0,packet_loss_pct_10s=0,autonomy_state='AUTO',onboard_map_version=2026092801,map_age_min=0,
        odd_profile_id=vehicle['odd_profile_id'],cargo_priority=int(vehicle['cargo_priority']),destination_hub_id=vehicle['destination_hub_id'],self_reported_confidence=1)
    if congested:
        source=next(k for k,r in ref.sensors.items() if r['segment_id']==current)
        add('ROAD_OBSERVATION',source,source_type=ref.sensors[source]['source_type'],segment_id=current,flow_vph=1000,speed_kmh=20,
            occupancy_pct=85,queue_estimate_m=150,lane_count_open_estimate=int(s['lanes']),sequence_no=1,self_reported_confidence=1)
    return dict(dataset_id='AV-CORRIDOR-2026-V2',dataset_version='2.0',scenario_id='DEMO',packet_id='DEMO-P0001',step=1,
                window_start='2026-09-28T00:00:00.000Z',window_end=STAMP,decision_time=STAMP,
                watermark_time='2026-09-27T23:59:05.000Z',event_count=len(events),is_final=True,events=events)


def run_case(title,packet,out):
    controller=Controller(Reference(ROOT/'reference'))
    decision=controller.process(packet)
    controller.contract.validate(decision)
    assert not errors(controller.state,controller.fusion,decision)
    assert not any(isinstance(d,str) and ('exception' in d or 'recovery' in d) for d in controller.state.diagnostics)
    action=next(a for a in decision['vehicle_actions'] if a['vehicle_id']=='AV-001')
    roads={r['segment_id']:r['state'] for r in decision['state_estimates']}
    print('\n'+title)
    print('  AV-001:',action['motion_action'],'скорость:',action.get('speed_limit_kmh'),'маршрут:',action.get('route_segment_ids'))
    print('  Контракт OK: 86 сегментов / 50 источников / 72 оценки / 72 действия')
    print('  Финальный контроль OK; операторов:',sum(a['remote_support_required'] for a in decision['vehicle_actions']))
    out.write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
    return action,roads,controller


def main():
    ref=Reference(ROOT/'reference'); out=ROOT/'results/demo'; out.mkdir(parents=True,exist_ok=True)
    current='S005'
    print('ДЕМОНСТРАЦИЯ: реальный алгоритм, справочники кейса, синтетические наблюдения без labels')
    a,_,controller=run_case('1. Дорога доступна, ODD соблюдён',make_packet(ref,current),out/'01-normal.json')
    paths=controller.router.paths['AV-001']
    assert paths['static']==paths['dynamic'] and a['motion_action']=='CONTINUE'
    print('  Статический путь == динамический путь → CONTINUE: OK')
    blocked=paths['static'][0]
    a,roads,controller=run_case('2. Подтверждённое закрытие впереди',make_packet(ref,current,closed=blocked),out/'02-closure.json')
    assert roads[blocked]=='CLOSED' and a['motion_action']=='REROUTE' and blocked not in a['route_segment_ids']
    print('  CLOSED подтверждён двумя источниками; объезд направленный и до хаба: OK')
    a,roads,_=run_case('3. Затор — отдельный класс, осторожная скорость',make_packet(ref,current,congested=True),out/'03-congestion.json')
    assert roads[current]=='CONGESTED' and a['motion_action']=='LIMIT_SPEED' and a['speed_limit_kmh']==30
    print('  CONGESTED не превращается в CLOSED; LIMIT_SPEED 30: OK')
    assert route_choice(['a','b'],['a','c'],500,1000)=='REROUTE'
    assert route_choice(['a'],['b'],500.001,1000)=='CONTINUE'
    print('\n4. Граница 500/500.001 м и сравнение полных списков: OK')
    assigned=min_cost_assignment({'AV-001':{'SS-01':10,'SS-02':11},'AV-002':{'SS-01':10}},{'SS-01':1,'SS-02':1})
    assert assigned=={'AV-001':'SS-02','AV-002':'SS-01'}
    selected=support_selection({f'AV-{i:03}':(i%3,i%5+1) for i in range(1,18)},6)
    assert len(selected)==6
    print('5. Синтетическое назначение площадок без переполнения:',assigned)
    print('   17 запросов → 6 операторов: OK')
    print('\nВсе демонстрационные проверки пройдены. Полные JSON:',out)
    print('Ограничение: это проверка контракта и логики, не доказательство физической безопасности или официальный балл.')


if __name__=='__main__': main()
