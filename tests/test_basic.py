import gzip
import json
import os
from pathlib import Path
import unittest
from corridor.basic import road_vote, inactive
from corridor.controller import Controller
from corridor.reference import Reference
from corridor.state import State


class BasicTests(unittest.TestCase):
    def setUp(self): self.ref = Reference(os.environ['CORRIDOR_REFERENCE'])

    def test_classes(self):
        segment = self.ref.segments['S001']
        self.assertEqual(road_vote({'speed_kmh': 20, 'occupancy_pct': 80}, segment), 'CONGESTED')
        self.assertEqual(road_vote({'lane_count_open_estimate': 2}, segment), 'PARTIAL_BLOCK')
        self.assertEqual(road_vote({'lane_status': 'CLOSED'}, segment), 'CLOSED')
        self.assertIsNone(road_vote({}, segment))

    def test_train001_movement(self):
        controller = Controller(self.ref)
        path = Path(os.environ['CORRIDOR_REFERENCE']).parent / '02_train/TRAIN-001/packets.ndjson.gz'
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for _ in range(12): decision = controller.process(json.loads(next(stream)))
        self.assertIn('CONTINUE', {a['motion_action'] for a in decision['vehicle_actions']})

    def test_queue_not_inactivity(self):
        state = State(self.ref)
        state.now = 0
        event = dict(segment_id='S001', offset_m=500, speed_kmh=0, event_time='1970-01-01T00:00:00Z')
        for _ in range(4): self.assertFalse(inactive(state, 'AV-001', event))
        self.assertFalse(inactive(state, 'AV-001', None))
