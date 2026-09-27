import copy
import io
import os
import unittest
from corridor.contract import Contract, skeleton
from corridor.reference import Reference
from corridor.stream import packets


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.ref = Reference(os.environ['CORRIDOR_REFERENCE'])
        self.contract = Contract(self.ref)
        self.decision = skeleton(dict(scenario_id='test', packet_id='p', decision_time='2026-09-28T00:00:00Z'), self.ref)

    def test_complete(self):
        self.contract.validate(self.decision)

    def test_duplicate_missing_and_command_parameters(self):
        d = copy.deepcopy(self.decision)
        d['state_estimates'][1] = d['state_estimates'][0]
        with self.assertRaises(ValueError): self.contract.validate(d)
        for kind in ('LIMIT_SPEED', 'REROUTE', 'SAFE_STOP'):
            d = copy.deepcopy(self.decision)
            d['vehicle_actions'][0]['motion_action'] = kind
            with self.assertRaises(Exception): self.contract.validate(d)

    def test_reader_eof(self):
        self.assertEqual(list(packets(io.StringIO('{}\n\n{}\n'))), [{}, {}])
