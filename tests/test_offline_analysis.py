import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'tools'))
from train_analysis import episodes
from evaluate import macro_f1


class OfflineAnalysisTests(unittest.TestCase):
    def row(self,step,flag=True,vehicle='AV-1'):
        return dict(scenario='TEST',vehicle_id=vehicle,step=step,timestamp=f'2026-09-28T00:00:{step*5:02d}Z',
                    packet_id=str(step),gate_odd=flag,true_codes='V2X',pred_odd='COMPLIANT',action='CONTINUE')

    def test_three_steps_one_episode_not_one_per_step(self):
        result=episodes([self.row(i) for i in range(5)])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['steps'],5)
        self.assertEqual(result[0]['span_sec'],20)

    def test_interruption_and_packet_gap_break_episode(self):
        self.assertEqual(episodes([self.row(0),self.row(1),self.row(2,False),self.row(3)]),[])
        self.assertEqual(episodes([self.row(0),self.row(1),self.row(3)]),[])

    def test_interleaved_vehicles_are_independent(self):
        rows=[self.row(i,vehicle=v) for i in range(3) for v in ['AV-1','AV-2']]
        self.assertEqual(len(episodes(rows)),2)

    def test_macro_only_truth_present_unknown_is_false_negative(self):
        from collections import Counter
        result=macro_f1(Counter({('OPEN','OPEN'):3,('OPEN','UNKNOWN'):1}))
        self.assertEqual(set(result['classes']),{'OPEN'})
        self.assertAlmostEqual(result['macro'],6/7)
