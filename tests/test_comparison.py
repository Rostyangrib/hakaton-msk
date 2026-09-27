import gzip
import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'tools'))
from compare_versions import compare,normalized


def sample(packet='p'):
    return dict(scenario_id='s',packet_id=packet,decision_time='2026-09-28T00:00:00Z',
                vehicle_actions=[dict(vehicle_id='v',motion_action='HOLD',remote_support_required=False)],
                vehicle_assessments=[],state_estimates=[],source_assessments=[])


class ComparisonTests(unittest.TestCase):
    def write(self,path,values):
        with gzip.open(path,'wt',encoding='utf-8') as f:
            for d in values: f.write(json.dumps(d)+'\n')

    def test_missing_packet_is_not_silent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.write(root/'a.gz',[sample(),sample('p2')]);self.write(root/'b.gz',[sample()])
            with self.assertRaisesRegex(ValueError,'packet counts'): compare(root/'a.gz',root/'b.gz',root,'test')

    def test_packet_alignment_and_action_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.write(root/'a.gz',[sample()]);self.write(root/'b.gz',[sample('other')])
            with self.assertRaisesRegex(ValueError,'Misaligned'): compare(root/'a.gz',root/'b.gz',root,'test')
            d=sample();d['vehicle_actions'][0]['remote_support_required']=True
            self.write(root/'b.gz',[dict(decision=d)])
            r=compare(root/'a.gz',root/'b.gz',root,'test')
            self.assertEqual(r['counts']['motion_action_changed'],0)
            self.assertEqual(r['counts']['remote_support_required_changed'],1)

    def test_normalization_preserves_confidence_and_time(self):
        a=sample();b=sample('different');b['scenario_id']='another'
        self.assertEqual(normalized(a),normalized(b))
        b['vehicle_actions'][0]['confidence']=.7
        self.assertNotEqual(normalized(a),normalized(b))
