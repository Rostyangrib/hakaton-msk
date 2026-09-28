import os
import unittest
from corridor.board import issues
from corridor.memory import SafetyMemory
from corridor.reference import Reference
from corridor.state import State
from corridor.odd import check
from corridor.guard import errors
from corridor.contract import skeleton
from test_odd import WeatherFusion
from test_state import packet


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.state=State(Reference(os.environ['CORRIDOR_REFERENCE']))
        self.event=dict(event_id='bad',event_time='1970-01-01T00:00:00Z',segment_id='S001',offset_m=0,
                        gnss_quality=1,map_age_min=0,autonomy_state='AUTO',perception_health=1,
                        localization_confidence=1,communication_latency_ms=40,packet_loss_pct_10s=0)

    def test_silent_perception_and_localization_cannot_be_certified_by_weather(self):
        fusion=WeatherFusion(dict(visibility_m=1000,rain_level=0,wind_mps=0,confidence=1))
        for changes in ({'perception_health':.05},{'localization_confidence':.05},
                        {'communication_latency_ms':5000,'packet_loss_pct_10s':100}):
            with self.subTest(changes=changes):
                result=check(self.state,fusion,'AV-001',dict(self.event,**changes))
                self.assertEqual(result['odd_status'],'UNKNOWN')
                self.assertEqual(result['violation_codes'],[])

    def test_recovery_requires_three_new_times_and_all_components(self):
        memory=SafetyMemory();e=dict(self.event,perception_health=.05)
        self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))
        e.update(event_id='g1',event_time='1970-01-01T00:00:05Z',perception_health=1)
        self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))
        for _ in range(4):self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))
        e['event_id']='same_time_different_id'
        self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))
        e.update(event_id='g2',event_time='1970-01-01T00:00:10Z')
        self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))
        e.update(event_id='g3',event_time='1970-01-01T00:00:15Z')
        self.assertFalse(memory.board_uncertain(self.state,'AV-001',e))
        e.update(event_id='bad2',event_time='1970-01-01T00:00:20Z',localization_confidence=.05)
        self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))
        e.update(event_id='half_recovered',event_time='1970-01-01T00:00:25Z',perception_health=.05,localization_confidence=1)
        self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))

    def test_flapping_and_scenario_reset(self):
        memory=SafetyMemory()
        for i in range(8):
            e=dict(self.event,event_id=str(i),event_time=f'1970-01-01T00:00:{i*5:02d}Z',perception_health=.05 if i%2==0 else 1)
            self.assertTrue(memory.board_uncertain(self.state,'AV-001',e))
        self.state.reset('new')
        self.assertFalse(memory.board_uncertain(self.state,'AV-001',dict(self.event)))

    def test_final_guard_rejects_motion_even_if_odd_assessment_is_corrupted(self):
        p=packet(packet_id='guard-test');self.state.ingest(p)
        e=dict(self.event,event_time=p['decision_time'],perception_health=.05)
        self.state.current[('VEHICLE_TELEMETRY','AV-001','AV-001')]=e
        d=skeleton(p,self.state.ref)
        d['vehicle_assessments'][0]['odd_status']='COMPLIANT'
        d['vehicle_actions'][0]['motion_action']='CONTINUE'
        d['state_estimates'][0]['state']='OPEN'
        self.assertIn('unconfirmed_board_health',errors(self.state,None,d)['AV-001'])

    def test_manual_threshold_boundaries(self):
        e=dict(self.event,perception_health=.5,localization_confidence=.5,communication_latency_ms=1999,packet_loss_pct_10s=49)
        self.assertEqual(issues(e),[])
        e['communication_latency_ms']=2000
        self.assertIn('communication',issues(e))
