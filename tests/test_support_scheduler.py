import unittest
from collections import defaultdict
from types import SimpleNamespace
from corridor.support import SupportRequest, SupportScheduler
from corridor.resources import allocate_support
from corridor.guard import diagnostic_support


class SupportSchedulerTests(unittest.TestCase):
    def step(self, scheduler, requests, now, limit=1):
        selected=scheduler.select(requests,limit,now)
        scheduler.commit(selected,now)
        return selected

    def test_waiter_gets_help_without_packet_by_packet_rotation(self):
        scheduler=SupportScheduler();requests={v:SupportRequest(1,1,0) for v in 'ab'}
        self.assertEqual(self.step(scheduler,requests,0),{'a'})
        for now in range(5,30,5):self.assertEqual(self.step(scheduler,requests,now),{'a'})
        self.assertEqual(self.step(scheduler,requests,30),{'b'})
        for now in range(35,60,5):self.assertEqual(self.step(scheduler,requests,now),{'b'})
        self.assertEqual(self.step(scheduler,requests,60),{'a'})

    def test_all_seventeen_equal_requests_eventually_receive_six_slots(self):
        scheduler=SupportScheduler();requests={f'AV-{i:03}':SupportRequest(1,i%5+1,0) for i in range(17)}
        served=set()
        for now in range(0,91,5):
            selected=self.step(scheduler,requests,now,6)
            self.assertEqual(len(selected),6);served.update(selected)
        self.assertEqual(served,set(requests))

    def test_current_danger_preempts_protected_assignment_immediately(self):
        scheduler=SupportScheduler();requests={'a':SupportRequest(1,1,0)}
        self.step(scheduler,requests,0)
        requests['b']=SupportRequest(0,5,0)
        self.assertEqual(self.step(scheduler,requests,5),{'b'})

    def test_waiting_never_overrides_higher_risk_or_earlier_hazard(self):
        for other in (SupportRequest(2,1,0),SupportRequest(1,1,5)):
            scheduler=SupportScheduler();requests={'a':SupportRequest(1,1,0),'b':other}
            self.step(scheduler,requests,0)
            self.assertEqual(self.step(scheduler,requests,300),{'a'})

    def test_long_wait_overrides_cargo_but_does_not_interrupt_first_thirty_seconds(self):
        scheduler=SupportScheduler();requests={'a':SupportRequest(1,1,0),'b':SupportRequest(1,5,0)}
        self.assertEqual(self.step(scheduler,requests,0),{'a'})
        self.assertEqual(self.step(scheduler,requests,25),{'a'})
        self.assertEqual(self.step(scheduler,requests,30),{'b'})
        self.assertEqual(self.step(scheduler,requests,35),{'b'})

    def test_earlier_hazard_preempts_before_service_interval(self):
        scheduler=SupportScheduler();requests={'a':SupportRequest(1,1,10)}
        self.step(scheduler,requests,0)
        requests['b']=SupportRequest(1,5,0)
        self.assertEqual(self.step(scheduler,requests,5),{'b'})

    def test_repeated_planning_does_not_commit_or_age_an_assignment(self):
        scheduler=SupportScheduler();requests={v:SupportRequest(1,1,0) for v in 'ab'}
        self.step(scheduler,requests,0)
        for _ in range(4):self.assertEqual(scheduler.select(requests,1,30),{'b'})
        self.assertTrue(scheduler.tickets['a']['selected'])
        scheduler.commit({'b'},30)
        self.assertEqual(self.step(scheduler,requests,35),{'b'})

    def test_resolution_removes_ticket_and_a_new_episode_starts_fresh(self):
        scheduler=SupportScheduler();request={'a':SupportRequest(1,1,0)}
        self.step(scheduler,request,0,0)
        self.assertEqual(scheduler.waiting_seconds('a',100),100)
        self.step(scheduler,{},100)
        self.assertFalse(scheduler.tickets)
        self.step(scheduler,request,105,0)
        self.assertEqual(scheduler.waiting_seconds('a',105),0)

    def test_no_unnecessary_rotation_and_reduced_capacity(self):
        scheduler=SupportScheduler();requests={v:SupportRequest(1,1,0) for v in 'ab'}
        self.assertEqual(self.step(scheduler,requests,0,2),set('ab'))
        self.assertEqual(self.step(scheduler,requests,90,2),set('ab'))
        self.assertEqual(len(self.step(scheduler,requests,95,1)),1)
        self.assertEqual(self.step(scheduler,requests,100,0),set())


class SupportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.events={v:dict(vehicle_id=v,event_id=v,event_time='1970-01-01T00:00:00Z',segment_id='s',offset_m=x,speed_kmh=0,
                            autonomy_state='AUTO') for v,x in [('a',0),('b',1000)]}
        ref=SimpleNamespace(vehicles={v:dict(cargo_priority=1,gross_mass_t=1) for v in 'ab'},
                            hubs={'h':dict(x_m=0,y_m=0)},stops={},support={'max_parallel_sessions':1},
                            position=lambda sid,offset:(offset,0))
        self.state=SimpleNamespace(ref=ref,now=0,inactivity=defaultdict(int),diagnostics=[],
            support_scheduler=SupportScheduler(),fresh=lambda e,seconds:e.get('fresh',True),
            events=lambda kind,vid:[self.events[vid]] if vid in self.events else [])
        self.fusion=SimpleNamespace(roads={'s':{'state':'OPEN'}},weather=lambda pos:None)
        self.decision=dict(vehicle_assessments=[dict(vehicle_id=v,odd_status='VIOLATED') for v in 'ab'],
            vehicle_actions=[dict(vehicle_id=v,motion_action='HOLD',remote_support_required=False,
                                  rationale_codes=['LOW_CONFIDENCE']) for v in 'ab'])

    def assigned(self):
        return {a['vehicle_id'] for a in self.decision['vehicle_actions'] if a['remote_support_required']}

    def test_road_hold_precedes_stationary_hub_hold(self):
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertEqual(self.assigned(),{'b'})

    def test_missing_or_moving_telemetry_is_not_confirmed_waiting(self):
        self.events['a']['speed_kmh']=10
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertTrue(self.state.support_scheduler.pending['a'].exposed_hold)
        self.events['a']['speed_kmh']=0;self.events['a']['fresh']=False
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertTrue(self.state.support_scheduler.pending['a'].exposed_hold)

    def test_safe_stop_recommendation_does_not_release_unresolved_request(self):
        self.decision['vehicle_actions'][1].update(motion_action='SAFE_STOP',safe_stop_id='ss')
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertIn('b',self.state.support_scheduler.pending)
        # Only fresh observed compliance and absence of other requests releases it.
        self.decision['vehicle_assessments'][1]['odd_status']='COMPLIANT'
        self.events['b']['offset_m']=0
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertNotIn('b',self.state.support_scheduler.pending)

    def test_diagnostic_path_uses_same_priority_and_clears_obsolete_capacity_code(self):
        diagnostic_support(self.state,self.decision,self.fusion)
        self.assertEqual(self.assigned(),{'b'})
        self.assertIn('REMOTE_SUPPORT_CAPACITY',self.decision['vehicle_actions'][0]['rationale_codes'])
        self.events['a']['perception_health']=0
        diagnostic_support(self.state,self.decision,self.fusion)
        self.assertEqual(self.assigned(),{'a'})
        self.assertNotIn('REMOTE_SUPPORT_CAPACITY',self.decision['vehicle_actions'][0]['rationale_codes'])

    def test_inactive_vehicle_releases_support_only_with_fresh_waiting_observation(self):
        action=self.decision['vehicle_actions'][0];action['motion_action']='NO_ACTION'
        self.state.inactivity['a']=3;self.decision['vehicle_assessments'][0]['odd_status']='UNKNOWN'
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertNotIn('a',self.state.support_scheduler.pending)
        self.events['a']['fresh']=False
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertIn('a',self.state.support_scheduler.pending)
        self.events['a'].update(fresh=True,autonomy_state='REMOTE_REQUESTED')
        allocate_support(self.state,self.fusion,None,self.decision)
        self.assertIn('a',self.state.support_scheduler.pending)
