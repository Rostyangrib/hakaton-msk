import os
import unittest
from unittest.mock import patch
from corridor.reference import Reference
from corridor.state import State
from corridor.contract import skeleton, Contract
from corridor.controller import Controller
from corridor.guard import errors
from test_state import packet


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.state=State(Reference(os.environ['CORRIDOR_REFERENCE']))
        self.packet=dict(packet(),packet_id='p')
        self.decision=skeleton(self.packet,self.state.ref)

    def test_invalid_support_and_stop(self):
        for a in self.decision['vehicle_actions'][:7]: a['remote_support_required']=True
        self.assertEqual(len(errors(self.state,None,self.decision)),7)
        a=self.decision['vehicle_actions'][0]
        a.update(motion_action='SAFE_STOP',safe_stop_id='SS-99')
        self.assertIn('unknown_stop',errors(self.state,None,self.decision)[a['vehicle_id']])

    def test_wrong_hub_closed_discontinuous(self):
        event=dict(event_id='e',event_time=self.packet['decision_time'],segment_id='S001')
        self.state.current[('VEHICLE_TELEMETRY','AV-001','AV-001')]=event
        a=self.decision['vehicle_actions'][0]
        a.update(motion_action='REROUTE',route_segment_ids=['S001','S001'])
        problem=errors(self.state,None,self.decision)['AV-001']
        self.assertIn('discontinuous_route',problem)
        self.assertIn('unavailable_edge',problem)
        self.assertIn('wrong_destination',problem)

    def test_exception_contract_complete(self):
        controller=Controller(self.state.ref)
        with patch('corridor.trust.Trust.assess',side_effect=RuntimeError('injected')):
            d=controller.process(self.packet)
        Contract(self.state.ref).validate(d)
        self.assertTrue(all(a['motion_action']=='HOLD' for a in d['vehicle_actions']))
        self.assertEqual(sum(a['remote_support_required'] for a in d['vehicle_actions']),6)
