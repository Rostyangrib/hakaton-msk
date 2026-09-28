import os
import unittest
from corridor.reference import Reference
from corridor.state import State


def event(eid='a', time='2026-09-28T00:00:05Z', **extra):
    return dict(event_id=eid, scenario_id='test', event_type='ROAD_OBSERVATION', source_id='CAM-001',
                segment_id='S005', event_time=time, received_time='2026-09-28T00:00:10Z',
                delivery_no=1, speed_kmh=30, **extra)


def packet(events=(), **extra):
    return dict(scenario_id='test', decision_time='2026-09-28T00:00:10Z', events=list(events), is_final=False, **extra)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = State(Reference(os.environ['CORRIDOR_REFERENCE']))

    def test_duplicate_late_extra_empty(self):
        s = self.state
        s.ingest(packet([event(extra='ignored')]))
        s.ingest(packet([event(), event('b', '2026-09-28T00:00:01Z')]))
        self.assertEqual(s.events()[0]['event_id'], 'a')
        self.assertEqual(len(next(iter(s.history.values()))), 2)
        self.assertIn('duplicate', s.diagnostics)
        s.ingest(packet())
        self.assertEqual(s.events()[0]['event_id'], 'a')

    def test_order_independence(self):
        events = [event(), event('b', '2026-09-28T00:00:01Z')]
        self.state.ingest(packet(events))
        other = State(self.state.ref)
        other.ingest(packet(reversed(events)))
        self.assertEqual(self.state.current, other.current)
        self.assertEqual(self.state.history, other.history)

    def test_unknown_and_future(self):
        unknown = event(); unknown['segment_id'] = 'S999'
        future = event('future', '2026-09-28T00:00:11Z')
        self.state.ingest(packet([unknown, future]))
        self.assertFalse(self.state.current)

    def test_final_reset_and_recommendations(self):
        p = packet([event()]); p['is_final'] = True
        self.state.ingest(p)
        self.state.remember({'motion_action': 'CONTINUE'})
        self.state.ingest(packet())
        self.assertFalse(self.state.current)
        self.assertFalse(self.state.recommendations)

    def test_support_history_commits_final_snapshot_and_resets_between_scenarios(self):
        from corridor.support import SupportRequest
        self.state.ingest(packet())
        scheduler=self.state.support_scheduler
        scheduler.select({'AV-001':SupportRequest(1,1,0)},1,self.state.now)
        self.state.remember({'vehicle_actions':[{'vehicle_id':'AV-001','remote_support_required':True}]})
        self.assertTrue(scheduler.tickets['AV-001']['selected'])
        self.state.reset('other')
        self.assertFalse(self.state.support_scheduler.tickets)
