"""Counterexamples for safety decisions, using the published reference network."""
import os
import unittest
from unittest.mock import patch

from corridor.basic import inactive
from corridor.contract import skeleton
from corridor.controller import Controller
from corridor.fusion import Fusion
from corridor.guard import errors
from corridor.limits import segment_speed_limit
from corridor.odd import check
from corridor.reference import Reference
from corridor.resources import waiting_zone
from corridor.routing import Router
from corridor.state import State
from corridor.trust import Trust
from test_odd import WeatherFusion
from test_state import event, packet
from tools.demo import make_packet


class SafetyRegressions(unittest.TestCase):
    def setUp(self):
        self.ref=Reference(os.environ['CORRIDOR_REFERENCE'])
        self.state=State(self.ref)
        self.vehicle=dict(event_id='v',event_time='1970-01-01T00:00:00Z',
                          segment_id='S001',offset_m=0,gnss_quality=1,map_age_min=0)
        self.clear=dict(visibility_m=1000,rain_level=0,wind_mps=0,road_surface='DRY',confidence=.8)

    def roads(self,fusion):
        fusion.roads={sid:dict(state='OPEN',confidence=1,lanes=int(row['lanes']),speed=None,queue=0)
                      for sid,row in self.ref.segments.items()}
        return fusion

    def weather_pair(self,**changes):
        readings=[]
        for source in ('WX-01','WX-02'):
            e=event(source)
            e.update(event_type='WEATHER_OBSERVATION',source_id=source,station_id=source,
                     visibility_m=1000,rain_level=0,wind_mps=0,road_surface='DRY')
            readings.append(e)
        readings[1].update(changes)
        self.state.ingest(packet(readings))
        return Fusion(self.state,[dict(source_id=s,trust_score=.8) for s in ('WX-01','WX-02')],set())

    def test_dominant_dry_station_cannot_clear_overlapping_rain_warning(self):
        fusion=self.weather_pair(rain_level=3)
        # ODD-C allows rain <=2; local dominance may refine the estimate,
        # but cannot certify safety against a still-applicable warning.
        vid=next(v for v,r in self.ref.vehicles.items() if r['odd_profile_id']=='ODD-C')
        weather=fusion.weather((20000,0))
        e=dict(self.vehicle,event_time='2026-09-28T00:00:10Z')
        self.assertNotEqual(check(self.state,WeatherFusion(weather),vid,e)['odd_status'],'COMPLIANT')

    def test_wind_range_cannot_be_hidden_by_safe_median(self):
        weather=dict(self.clear,ranges={'wind_mps':(0,30)})
        self.assertEqual(check(self.state,WeatherFusion(weather),'AV-001',self.vehicle)['odd_status'],'UNKNOWN')

    def test_minority_flood_blocks_motion(self):
        fusion=self.weather_pair(road_surface='FLOODED')
        weather=fusion.weather((20000,0))
        capped=self.roads(WeatherFusion(weather))
        self.assertIsNone(segment_speed_limit(self.state,capped,'AV-001',self.vehicle))

    def test_low_visibility_range_sets_speed_cap(self):
        weather=dict(self.clear,ranges={'visibility_m':(75,1000)})
        fusion=self.roads(WeatherFusion(weather))
        self.assertEqual(segment_speed_limit(self.state,fusion,'AV-001',self.vehicle),30)

    def test_fresh_closure_survives_disagreement_based_exclusion(self):
        trust=Trust()
        for i in range(5):
            stamp=f'2026-09-28T00:00:{10+5*i:02d}Z'
            a=event('closed'+str(i),stamp,lane_status='CLOSED',lane_count_open_estimate=0)
            a['received_time']=stamp
            b=dict(a,event_id='open1'+str(i),source_id='RSU-01',event_type='V2X_MESSAGE',
                   signature_valid=True,lane_status='OPEN',lane_count_open_estimate=3)
            c=dict(b,event_id='open2'+str(i),source_id='DT-CORE',event_type='DIGITAL_TWIN_SEGMENT')
            self.state.ingest(dict(packet([a,b,c]),decision_time=stamp))
            assessed=trust.assess(self.state)
            fusion=Fusion(self.state,assessed,trust.excluded,trust.feature_trust)
            fusion.road_estimates()
            self.assertIn(fusion.roads['S005']['state'],('UNKNOWN','CLOSED'))

    def test_repeated_telemetry_does_not_complete_delivery(self):
        hub=self.ref.hubs[self.ref.vehicles['AV-001']['destination_hub_id']]
        sid=next(s for s,r in self.ref.segments.items() if r['to_node']==hub['node_id'])
        e=dict(self.vehicle,segment_id=sid,offset_m=float(self.ref.segments[sid]['length_m']),speed_kmh=0)
        for seconds in (0,5,10):
            self.state.now=seconds
            self.assertFalse(inactive(self.state,'AV-001',e))
        for i,seconds in enumerate((11,12)):
            self.state.now=seconds
            fresh=dict(e,event_id='new'+str(i),event_time=f'1970-01-01T00:00:{seconds:02d}Z')
            self.assertEqual(inactive(self.state,'AV-001',fresh),i==1)

    def test_stop_segment_does_not_prove_vehicle_is_parked(self):
        stop=self.ref.stops['SS-01'];sid=stop['segment_id']
        e=dict(self.vehicle,segment_id=sid,offset_m=float(self.ref.segments[sid]['length_m'])/2,speed_kmh=70)
        self.assertFalse(waiting_zone(self.state,'AV-001',e))

    def test_unknown_future_odd_not_routable(self):
        fusion=self.roads(WeatherFusion(self.clear))
        with patch('corridor.routing.telemetry',return_value=self.vehicle), patch('corridor.routing.check',return_value={'odd_status':'UNKNOWN'}):
            self.assertFalse(Router(self.state,fusion).costs('AV-001',True))

    def test_future_segment_checks_small_warning_zone_away_from_midpoint(self):
        sid='S001';length=float(self.ref.segments[sid]['length_m'])
        position=self.ref.position(sid,length*.1)
        warning=self.ref.weather['WX-02']
        warning.update(x_m=str(position[0]),y_m=str(position[1]),coverage_radius_m='20')
        fusion=self.weather_pair(road_surface='FLOODED')
        self.assertEqual(fusion.weather(self.ref.position(sid,length/2))['road_surface'],'DRY')
        self.roads(fusion)
        self.assertIsNone(segment_speed_limit(self.state,fusion,'AV-001',self.vehicle,sid,True))

    def test_future_segment_requires_weather_coverage_at_entry(self):
        sid='S001';length=float(self.ref.segments[sid]['length_m'])
        position=self.ref.position(sid,length/2)
        self.ref.weather['WX-01'].update(x_m=str(position[0]),y_m=str(position[1]),coverage_radius_m='20')
        self.ref.weather['WX-02'].update(x_m=str(position[0]),y_m=str(position[1]),coverage_radius_m='20')
        fusion=self.weather_pair()
        self.assertIsNotNone(fusion.weather(position))
        e=dict(self.vehicle,event_time='2026-09-28T00:00:10Z')
        self.assertEqual(check(self.state,fusion,'AV-001',e,sid,True)['odd_status'],'UNKNOWN')

    def test_v2x_integrity_fault_blocks_service_before_source_exclusion(self):
        e=event('rsu',signature_valid=True)
        e.update(event_type='V2X_MESSAGE',source_id='RSU-01',segment_id='S001')
        self.state.ingest(packet([e]))
        for fault in ('PACKET_LOSS','TIME_SKEW','DELAY','BYZANTINE'):
            fusion=Fusion(self.state,[dict(source_id='RSU-01',trust_score=.5,status='DEGRADED',fault_types=[fault])],set())
            self.assertNotEqual(fusion.v2x_status('S001'),'COMPLIANT')

    def test_guard_rejects_av_forbidden_current_segment(self):
        p=packet(packet_id='guard')
        self.state.ingest(p)
        e=dict(self.vehicle,event_time=p['decision_time'])
        self.state.current[('VEHICLE_TELEMETRY','AV-001','AV-001')]=e
        self.ref.segments['S001']['av_allowed']='0'
        d=skeleton(p,self.ref)
        d['state_estimates'][0]['state']='OPEN'
        d['vehicle_assessments'][0]['odd_status']='COMPLIANT'
        d['vehicle_actions'][0]['motion_action']='CONTINUE'
        self.assertIn('incompatible_current_segment',errors(self.state,None,d).get('AV-001',[]))

    def test_guard_rejects_excessive_or_missing_speed_cap(self):
        p=packet(packet_id='guard')
        self.state.ingest(p)
        self.state.current[('VEHICLE_TELEMETRY','AV-001','AV-001')]=dict(self.vehicle,event_time=p['decision_time'])
        fusion=self.roads(WeatherFusion(dict(self.clear,road_surface='WATER_FILM')))
        d=skeleton(p,self.ref)
        d['state_estimates'][0]['state']='OPEN'
        d['vehicle_assessments'][0]['odd_status']='COMPLIANT'
        a=d['vehicle_actions'][0]
        a.update(motion_action='LIMIT_SPEED',speed_limit_kmh=80)
        self.assertIn('unsafe_speed_limit',errors(self.state,fusion,d).get('AV-001',[]))
        a.update(motion_action='CONTINUE')
        self.assertIn('missing_speed_constraint',errors(self.state,fusion,d).get('AV-001',[]))

    def test_controller_does_not_discard_cap_for_reroute(self):
        normal=Controller(self.ref)
        normal.process(make_packet(self.ref,'S005'))
        closed=normal.router.paths['AV-001']['static'][0]
        p=make_packet(self.ref,'S005',closed=closed)
        for e in p['events']:
            if e['event_type']=='WEATHER_OBSERVATION': e['road_surface']='WATER_FILM'
        controller=Controller(self.ref)
        d=controller.process(p)
        a=next(a for a in d['vehicle_actions'] if a['vehicle_id']=='AV-001')
        self.assertNotEqual(a['motion_action'],'REROUTE')
        self.assertFalse(errors(controller.state,controller.fusion,d))
        self.assertFalse(any('exception' in str(x) or 'planner_recovery' in str(x) for x in controller.state.diagnostics))

    def test_normal_delivery_still_moves_and_closure_has_usable_detour(self):
        controller=Controller(self.ref)
        d=controller.process(make_packet(self.ref,'S005'))
        self.assertEqual(d['vehicle_actions'][0]['motion_action'],'CONTINUE')
        closed=controller.router.paths['AV-001']['static'][0]
        d=controller.process(make_packet(self.ref,'S005',closed=closed))
        self.assertEqual(d['vehicle_actions'][0]['motion_action'],'REROUTE')
        self.assertNotIn(closed,d['vehicle_actions'][0]['route_segment_ids'])

    def test_seventy_two_vehicles_in_fog_never_move_without_safe_conditions(self):
        controller=Controller(self.ref)
        for step in range(3):
            p=make_packet(self.ref,'S005')
            p.update(is_final=False,packet_id='fog-'+str(step),decision_time=f'2026-09-28T00:00:{5+step*5:02d}Z')
            template=next(e for e in p['events'] if e['event_type']=='VEHICLE_TELEMETRY')
            p['events']=[e for e in p['events'] if e['event_type']!='VEHICLE_TELEMETRY']
            for vid,row in self.ref.vehicles.items():
                p['events'].append(dict(template,event_id=vid,source_id=vid,vehicle_id=vid,
                    odd_profile_id=row['odd_profile_id'],cargo_priority=int(row['cargo_priority']),
                    destination_hub_id=row['destination_hub_id']))
            for e in p['events']:
                e.update(event_id=e['event_id']+'-'+str(step),event_time=p['decision_time'],received_time=p['decision_time'])
                if e['event_type']=='WEATHER_OBSERVATION': e['visibility_m']=20
            p['event_count']=len(p['events'])
            d=controller.process(p)
            self.assertTrue(all(a['motion_action']=='HOLD' for a in d['vehicle_actions']))
            self.assertEqual(sum(a['remote_support_required'] for a in d['vehicle_actions']),6)
            self.assertFalse(errors(controller.state,controller.fusion,d))
            self.assertFalse(any('exception' in str(x) for x in controller.state.diagnostics))


if __name__=='__main__':
    unittest.main()
