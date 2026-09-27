import os
import unittest
from corridor.reference import Reference
from corridor.state import State
from corridor import basic
from corridor.contract import skeleton
from tools.demo import make_packet


class ReasonIndependence(unittest.TestCase):
    def test_action_does_not_mutate_road_reasons(self):
        ref=Reference(os.environ['CORRIDOR_REFERENCE'])
        state=State(ref); packet=make_packet(ref,'S005'); state.ingest(packet)
        decision=skeleton(packet,ref)
        decision['state_estimates']=basic.roads(state)
        basic.policy(state,decision)
        action=next(a for a in decision['vehicle_actions'] if a['vehicle_id']=='AV-001')
        self.assertEqual(action['motion_action'],'CONTINUE')
        action['rationale_codes'].append('REMOTE_SUPPORT_CAPACITY')
        road=next(r for r in decision['state_estimates'] if r['segment_id']=='S005')
        self.assertNotIn('REMOTE_SUPPORT_CAPACITY',road['rationale_codes'])
