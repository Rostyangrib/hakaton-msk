import os
import unittest
from corridor.reference import Reference
from corridor.state import State,timestamp
from corridor.trust import Trust


class FdirResiduals(unittest.TestCase):
    def assess(self,speeds,peer_speeds):
        state=State(Reference(os.environ['CORRIDOR_REFERENCE']))
        for source,values in [('CAM-001',speeds),('DET-001',peer_speeds)]:
            events=[]
            for i,speed in enumerate(values):
                events.append(dict(event_id=f'{source}-{i}',source_id=source,event_type='ROAD_OBSERVATION',segment_id='S005',
                                   event_time=f'2026-09-28T00:00:{i*5:02}Z',received_time=f'2026-09-28T00:00:{i*5:02}Z',
                                   speed_kmh=speed,flow_vph=1000,occupancy_pct=20,queue_estimate_m=0))
            key=('ROAD_OBSERVATION','S005',source); state.history[key]=events; state.current[key]=events[-1]
        state.now=timestamp('2026-09-28T00:00:10Z')
        return {r['source_id']:r for r in Trust().assess(state)}

    def test_unchanged_normal_not_freeze(self):
        result=self.assess([50,50,50],[50,50,50])
        self.assertFalse(result['CAM-001']['fault_types'])

    def test_comparable_bias_and_drift(self):
        result=self.assess([40,40,40],[0,0,0])
        self.assertIn('BIAS',result['CAM-001']['fault_types'])
        result=self.assess([40,48,56],[0,0,0])
        self.assertIn('DRIFT',result['CAM-001']['fault_types'])

    def test_frozen_counter_multiple_fields(self):
        state=State(Reference(os.environ['CORRIDOR_REFERENCE']))
        key=('ROAD_OBSERVATION','S005','CAM-001')
        values=[dict(event_id=str(i),event_type='ROAD_OBSERVATION',source_id='CAM-001',segment_id='S005',
                     event_time=f'2026-09-28T00:00:{i*5:02}Z',received_time=f'2026-09-28T00:00:{i*5:02}Z',
                     sequence_no=1,speed_kmh=50,flow_vph=1000,occupancy_pct=20,queue_estimate_m=0) for i in range(4)]
        state.history[key]=values; state.current[key]=values[-1]; state.now=timestamp(values[-1]['event_time'])
        result={r['source_id']:r for r in Trust().assess(state)}
        self.assertIn('FREEZE',result['CAM-001']['fault_types'])
        for i,e in enumerate(values): e['sequence_no']=i+1
        result={r['source_id']:r for r in Trust().assess(state)}
        self.assertNotIn('FREEZE',result['CAM-001']['fault_types'])
