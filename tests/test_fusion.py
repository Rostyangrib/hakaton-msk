import os
import unittest
from corridor.fusion import weighted_median, confidence, Fusion
from corridor.reference import Reference
from corridor.state import State
from test_state import event, packet


class FusionTests(unittest.TestCase):
    def test_median_confidence(self):
        self.assertEqual(weighted_median([(10,1),(100,0.1),(20,1)]),20)
        self.assertIsNone(weighted_median([]))
        self.assertGreater(confidence(1,[0.5,0.5]),confidence(1,[0.5]))
        self.assertEqual(confidence(1,[]),0.35)

    def test_conflict_excluded_single(self):
        state = State(Reference(os.environ['CORRIDOR_REFERENCE']))
        state.ingest(packet([event(lane_status='CLOSED')]))
        assessments = [dict(source_id='CAM-001',trust_score=0.5)]
        fusion = Fusion(state, assessments,set())
        fusion.road_estimates()
        self.assertEqual(fusion.roads['S005']['state'],'UNKNOWN')
        self.assertEqual(Fusion(state,assessments,{'CAM-001'}).weight(event()),0)
        state.now += 200
        self.assertEqual(fusion.weight(event()),0)
