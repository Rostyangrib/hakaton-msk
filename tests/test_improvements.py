import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from corridor.reference import Reference
from corridor.state import State
from corridor.trust import Trust
from corridor.fusion import Fusion
from corridor.memory import SafetyMemory
from corridor.odd import check
from corridor.routing import Router
from corridor.resources import support_selection
from corridor.limits import current_fragment_allowed
from test_odd import WeatherFusion
from test_state import event,packet


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        self.state=State(Reference(os.environ['CORRIDOR_REFERENCE']))

    def test_matching_availability_does_not_certify_speed(self):
        trust=Trust()
        for step in range(3):
            time=f'2026-09-28T00:00:{step*5+10:02d}Z'
            a=event('a'+str(step),time)
            a.update(received_time=time,lane_count_open_estimate=int(self.state.ref.segments['S005']['lanes']),speed_kmh=80)
            b=dict(a,event_id='b'+str(step),source_id='DET-001',speed_kmh=110)
            self.state.ingest(dict(packet([a,b]),decision_time=time))
            trust.assess(self.state)
        self.assertGreater(trust.feature_trust['CAM-001']['availability'],trust.feature_trust['CAM-001']['speed'])

    def test_independent_feature_weight(self):
        self.state.ingest(packet([event()]))
        scores=[dict(source_id='CAM-001',trust_score=.9)]
        fusion=Fusion(self.state,scores,set(),{'CAM-001':{'availability':.9,'speed':.1}})
        self.assertGreater(fusion.weight(event()),fusion.weight(event(),'speed'))

    def test_fresh_rsu_trust_is_not_an_age_reduced_threshold(self):
        from corridor.basic import freshness
        e=event('v',lane_status='OPEN',advisory_speed_kmh=80)
        e.update(event_type='V2X_MESSAGE',source_id='RSU-01')
        self.state.ingest(packet([e]))
        from corridor.state import timestamp
        self.state.now=timestamp(e['event_time'])+freshness(self.state.ref,e)*.9
        fusion=Fusion(self.state,[dict(source_id='RSU-01',trust_score=.9)],set(),{'RSU-01':{'availability':.9}})
        self.assertLess(fusion.weight(e),.7)
        fusion.road_estimates()
        self.assertEqual(fusion.roads[e['segment_id']]['state'],'OPEN')

    def test_zero_availability_weight_is_not_a_second_vote(self):
        a=event('a',lane_status='OPEN',lane_count_open_estimate=int(self.state.ref.segments['S005']['lanes']))
        b=dict(a,event_id='b',source_id='DET-001')
        self.state.ingest(packet([a,b]))
        fusion=Fusion(self.state,[dict(source_id=s,trust_score=.5) for s in ['CAM-001','DET-001']],set(),{'CAM-001':{'availability':.5},'DET-001':{'availability':0,'speed':.5}})
        fusion.road_estimates()
        self.assertEqual(fusion.roads['S005']['state'],'UNKNOWN')

    def test_weather_outage_recovers_without_fake_positive_votes(self):
        trust=Trust()
        def weather(eid,stamp):
            return dict(event_id=eid,scenario_id='test',event_type='WEATHER_OBSERVATION',source_id='WX-01',station_id='WX-01',
                        event_time=stamp,received_time=stamp,delivery_no=1,visibility_m=1000,rain_level=0,wind_mps=1,road_surface='DRY')
        self.state.ingest(packet([weather('first','2026-09-28T00:00:10Z')]))
        trust.assess(self.state)
        self.state.ingest(dict(packet(),decision_time='2026-09-28T00:01:50Z'));trust.assess(self.state)
        self.assertIn('WX-01',trust.excluded)
        for i,stamp in enumerate(['2026-09-28T00:02:00Z','2026-09-28T00:02:10Z','2026-09-28T00:02:20Z']):
            self.state.ingest(dict(packet([weather(str(i),stamp)]),decision_time=stamp));trust.assess(self.state)
            self.assertEqual('WX-01' in trust.excluded,i<2)
        self.assertEqual(sum(e[1] for e in trust.evidence['WX-01']),0)
        self.assertNotIn(('WX-01','visibility'),trust.feature_excluded)

    def test_queue_without_speed_is_not_free_flow(self):
        sid='S005';lanes=int(self.state.ref.segments[sid]['lanes'])
        for i in range(3):
            stamp=f'2026-09-28T00:00:{10+i*5:02d}Z'
            a=event(str(i),stamp,lane_status='OPEN',queue_estimate_m=250)
            a.update(event_type='V2X_MESSAGE',source_id='RSU-01')
            a.pop('speed_kmh');a['received_time']=stamp
            b=dict(a,event_id='t'+str(i),event_type='DIGITAL_TWIN_SEGMENT',source_id='DT-CORE',lane_count_open=lanes)
            b.pop('queue_estimate_m')
            self.state.ingest(dict(packet([a,b]),decision_time=stamp))
        fusion=Fusion(self.state,[dict(source_id=s,trust_score=.8) for s in ['RSU-01','DT-CORE']],set())
        fusion.road_estimates()
        self.assertEqual(fusion.roads[sid]['availability'],'OPEN')
        self.assertEqual(fusion.roads[sid]['state'],'CONGESTED')
        self.assertIsNone(fusion.roads[sid]['speed'])

    def test_weather_warning_requires_original_source_clearance_and_expires(self):
        memory=SafetyMemory();self.state.now=10
        bad=dict(visibility_m=60,rain_level=0,ranges={'visibility_m':(60,60),'rain_level':(0,0)},event_ids=['bad'],sources=['old'],risk_sources_visibility=['old'])
        self.assertFalse(memory.weather_uncertain(self.state,'AV-001',(0,0),bad))
        good=dict(visibility_m=1000,rain_level=0,event_ids=['good'],sources=['new'])
        self.state.now=15
        self.assertTrue(memory.weather_uncertain(self.state,'AV-001',(100,0),good))
        self.state.now=101
        self.assertFalse(memory.weather_uncertain(self.state,'AV-001',(100,0),good))

    def test_confirmed_rsu_twin_partial_is_not_overwritten_by_queue(self):
        sid='S005'
        a=event('rsu',lane_status='PARTIAL_BLOCK',queue_estimate_m=200)
        a.update(event_type='V2X_MESSAGE',source_id='RSU-01')
        b=event('twin',closure_state='PARTIAL_BLOCK',queue_estimate_m=200)
        b.update(event_type='DIGITAL_TWIN_SEGMENT',source_id='DT-CORE')
        self.state.ingest(packet([a,b]))
        fusion=Fusion(self.state,[dict(source_id=s,trust_score=.8) for s in ['RSU-01','DT-CORE']],set())
        fusion.road_estimates()
        self.assertEqual(fusion.roads[sid]['state'],'PARTIAL_BLOCK')
        self.assertEqual(fusion.roads[sid]['load'],'CONGESTED')

    def weather_reading(self,source,eid,stamp,visibility=1000,rain=0):
        return dict(event_id=eid,scenario_id='test',event_type='WEATHER_OBSERVATION',source_id=source,station_id=source,
                    event_time=stamp,received_time=stamp,delivery_no=1,visibility_m=visibility,rain_level=rain,wind_mps=1,road_surface='DRY')

    def test_local_rain_requires_spatial_dominance(self):
        stamp='2026-09-28T00:00:10Z'
        a=self.weather_reading('WX-01','a',stamp,rain=0)
        b=self.weather_reading('WX-02','b',stamp,rain=3)
        self.state.ingest(packet([a,b]))
        fusion=Fusion(self.state,[dict(source_id=s,trust_score=.8) for s in ['WX-01','WX-02']],set())
        self.assertEqual(fusion.weather((20000,0))['ranges']['rain_level'],(0,0))
        self.assertEqual(fusion.weather((25000,0))['ranges']['rain_level'],(0,3))
        self.assertEqual(fusion.weather((30000,0))['ranges']['rain_level'],(3,3))

    def test_visibility_corroboration_needs_new_persistent_measurements(self):
        scores=[dict(source_id=s,trust_score=.8) for s in ['WX-01','WX-02']]
        e=dict(event_id='vehicle',segment_id='S001',offset_m=float(self.state.ref.segments['S001']['length_m']),gnss_quality=1,map_age_min=0,
               autonomy_state='DEGRADED',perception_health=.8)
        memory=SafetyMemory()
        for i,seconds in enumerate([10,20,30]):
            stamp=f'2026-09-28T00:00:{seconds:02d}Z'
            a=self.weather_reading('WX-01','low'+str(i),stamp,visibility=50)
            b=self.weather_reading('WX-02','high'+str(i),stamp)
            self.state.ingest(dict(packet([a,b]),decision_time=stamp))
            e['event_time']=stamp
            fusion=Fusion(self.state,scores,set(),memory=memory)
            result=check(self.state,fusion,'AV-001',e)
            self.assertEqual(result['odd_status'],'VIOLATED' if i==2 else 'UNKNOWN')
        # AUTO is not proof of compliance; a contradictory weather range stays UNKNOWN.
        e['autonomy_state']='AUTO'
        fusion=Fusion(self.state,scores,set())
        self.assertEqual(check(self.state,fusion,'AV-001',e)['odd_status'],'UNKNOWN')

    def test_duplicate_weather_does_not_confirm_visibility(self):
        stamp='2026-09-28T00:00:10Z'
        a=self.weather_reading('WX-01','same',stamp,visibility=50)
        self.state.ingest(packet([a,a,a]))
        fusion=Fusion(self.state,[dict(source_id='WX-01',trust_score=.8)],set())
        self.assertEqual(fusion.visibility_evidence((10000,0),70),(False,True))

    def test_visibility_margin_is_unknown_without_fabricating_violation(self):
        stamp='2026-09-28T00:00:10Z'
        a=self.weather_reading('WX-01','a',stamp,visibility=75)
        self.state.ingest(packet([a]))
        fusion=Fusion(self.state,[dict(source_id='WX-01',trust_score=.8)],set())
        e=dict(event_id='v',segment_id='S001',offset_m=0,event_time=stamp,gnss_quality=1,map_age_min=0,
               autonomy_state='DEGRADED',perception_health=.8)
        result=check(self.state,fusion,'AV-001',e)
        self.assertEqual(result['odd_status'],'UNKNOWN')
        self.assertNotIn('VISIBILITY',result['violation_codes'])

    def test_duplicate_clearance_does_not_release_warning(self):
        memory=SafetyMemory();self.state.now=10
        bad=dict(visibility_m=60,rain_level=0,event_ids=['bad'],sources=['s'],risk_sources_visibility=['s'])
        memory.weather_uncertain(self.state,'AV-001',(0,0),bad)
        good=dict(visibility_m=1000,rain_level=0,event_ids=['same'],sources=['s'])
        for t in [15,20,25]:
            self.state.now=t
            self.assertTrue(memory.weather_uncertain(self.state,'AV-001',(0,0),good))
        for t,eid in [(30,'next'),(35,'third')]:
            self.state.now=t;good['event_ids']=[eid]
            released=memory.weather_uncertain(self.state,'AV-001',(0,0),good)
        self.assertFalse(released)
        self.state.reset('other')
        self.assertFalse(memory.weather_uncertain(self.state,'AV-001',(0,0),good))

    def test_expiration_of_peers_is_not_a_new_clearance(self):
        memory=SafetyMemory();self.state.now=10
        memory.road(self.state,'S001',dict(state='CLOSED'),('bad',))
        self.state.now=15
        self.assertEqual(memory.road(self.state,'S001',dict(state='OPEN'),('good','a','b'))['state'],'UNKNOWN')
        self.state.now=20
        self.assertEqual(memory.road(self.state,'S001',dict(state='OPEN'),('good','a'))['state'],'UNKNOWN')
        self.state.now=25
        self.assertEqual(memory.road(self.state,'S001',dict(state='OPEN'),('good',))['state'],'UNKNOWN')
        self.state.now=30
        self.assertEqual(memory.road(self.state,'S001',dict(state='OPEN'),('next',))['state'],'UNKNOWN')
        self.state.now=35
        self.assertEqual(memory.road(self.state,'S001',dict(state='OPEN'),('third',))['state'],'OPEN')

    def test_v2x_fresh_message_does_not_override_explicit_packet_loss(self):
        a=event('v',signature_valid=True)
        a.update(event_type='V2X_MESSAGE',source_id='RSU-01',segment_id='S001')
        h=event('h',component_id='RSU-01',status='WARN',network_packet_loss_pct=40)
        h.update(event_type='INFRASTRUCTURE_HEALTH',source_id='RSU-01')
        self.state.ingest(packet([a,h]))
        fusion=Fusion(self.state,[dict(source_id='RSU-01',trust_score=.5,status='DEGRADED')],set())
        self.assertEqual(fusion.v2x_status('S001'),'VIOLATED')

    def test_map_age_at_decision_time(self):
        self.state.now=1
        e=dict(segment_id='S001',offset_m=0,event_time='1970-01-01T00:00:00Z',gnss_quality=1,map_age_min=1440)
        fusion=WeatherFusion(dict(visibility_m=1000,rain_level=0,wind_mps=0,confidence=.8))
        self.assertIn('MAP_AGE',check(self.state,fusion,'AV-001',e)['violation_codes'])
        self.state.now=0
        self.assertNotIn('MAP_AGE',check(self.state,fusion,'AV-001',e)['violation_codes'])

    def test_dynamic_time_uses_weather_and_queue_caps(self):
        e=dict(segment_id='S001',offset_m=0,event_time='1970-01-01T00:00:00Z')
        fusion=WeatherFusion(dict(visibility_m=1000,rain_level=0,wind_mps=0,road_surface='WATER_FILM',confidence=.8))
        fusion.roads={s:dict(state='OPEN',confidence=1,lanes=int(r['lanes']),speed=None,queue=0) for s,r in self.state.ref.segments.items()}
        router=Router(self.state,fusion)
        with patch('corridor.routing.telemetry',return_value=e),patch('corridor.routing.check',return_value={'odd_status':'COMPLIANT'}):
            costs=router.costs('AV-001',True)
        length=float(self.state.ref.segments['S001']['length_m'])
        self.assertGreaterEqual(costs['S001'],length/(40/3.6))
        fusion.roads['S001']['queue']=200
        router=Router(self.state,fusion)
        with patch('corridor.routing.telemetry',return_value=e),patch('corridor.routing.check',return_value={'odd_status':'COMPLIANT'}):
            self.assertAlmostEqual(router.costs('AV-001',True)['S001'],length/(30/3.6))

    def test_stop_approach_needs_confirmed_future_odd(self):
        e=dict(segment_id='S001',offset_m=0,event_time='1970-01-01T00:00:00Z')
        fusion=WeatherFusion(None)
        fusion.roads={s:dict(state='OPEN',confidence=1,lanes=int(r['lanes']),speed=None,queue=0) for s,r in self.state.ref.segments.items()}
        with patch('corridor.routing.telemetry',return_value=e),patch('corridor.routing.check',return_value={'odd_status':'UNKNOWN'}):
            router=Router(self.state,fusion)
            self.assertIn('S001',router.costs('AV-001',True))
            self.assertNotIn('S001',router.costs('AV-001',True,confirmed=True))

    def test_current_fragment_not_ignored_for_stop_approach(self):
        e=dict(segment_id='S001',offset_m=0,event_time='1970-01-01T00:00:00Z')
        fusion=WeatherFusion(None);fusion.roads={'S001':dict(state='CLOSED')}
        self.assertFalse(current_fragment_allowed(self.state,fusion,'AV-001',e))
        fusion.roads['S001']['state']='OPEN'
        with patch('corridor.odd.check',return_value={'odd_status':'VIOLATED'}):
            self.assertFalse(current_fragment_allowed(self.state,fusion,'AV-001',e))

    def test_support_eta_does_not_override_risk_or_cargo(self):
        self.assertEqual(support_selection({'a':(2,1,1),'b':(1,1,100)},1),{'b'})
        self.assertEqual(support_selection({'a':(1,1,100),'b':(1,2,1)},1),{'a'})
        self.assertEqual(support_selection({'a':(1,1,100),'b':(1,1,1)},1,['a']),{'b'})
        self.assertEqual(support_selection({'a':(1,1,1),'b':(1,1,1)},1,['b']),{'b'})
