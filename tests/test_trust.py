import os
import unittest
from corridor.reference import Reference
from corridor.state import State
from corridor.trust import Trust
from test_state import event, packet


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.state = State(Reference(os.environ['CORRIDOR_REFERENCE']))
        self.trust = Trust()

    def test_cold_start_no_automatic_positive(self):
        self.state.ingest(packet())
        result = self.trust.assess(self.state)
        self.assertTrue(all(s['status'] == 'UNKNOWN' for s in result))
        self.assertTrue(all(not w for w in self.trust.evidence.values()))

    def test_duplicate_bounded(self):
        e = event(snapshot_age_sec=1000)
        self.state.ingest(packet([e,e,e]))
        self.trust.assess(self.state)
        self.assertEqual(len(self.trust.evidence['CAM-001']), 1)
        self.trust.assess(self.state)
        self.assertEqual(len(self.trust.evidence['CAM-001']), 1)

    def test_time_skew_and_outage(self):
        self.state.ingest(packet([event(source_clock_offset_ms=1001)]))
        result = {r['source_id']:r for r in self.trust.assess(self.state)}
        self.assertIn('TIME_SKEW', result['CAM-001']['fault_types'])
        self.state.now += 200
        result = {r['source_id']:r for r in self.trust.assess(self.state)}
        self.assertEqual(result['CAM-001']['status'], 'FAILED')
        self.assertIn('CAM-001', self.trust.excluded)
