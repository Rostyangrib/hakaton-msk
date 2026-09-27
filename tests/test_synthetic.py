import os
import unittest
from corridor.reference import Reference
from corridor.state import State
from corridor.fusion import Fusion
from corridor.routing import Router
from test_state import event,packet


class SyntheticTests(unittest.TestCase):
    def setUp(self): self.ref=Reference(os.environ['CORRIDOR_REFERENCE'])

    def test_confirmed_closed_and_conflict(self):
        a=event(lane_status='CLOSED')
        b=event('b',lane_status='CLOSED'); b['source_id']='RSU-01'; b['event_type']='V2X_MESSAGE'; b['signature_valid']=True
        state=State(self.ref); state.ingest(packet([a,b]))
        assess=[dict(source_id=s,trust_score=0.8) for s in ('CAM-001','RSU-01')]
        fusion=Fusion(state,assess,set()); fusion.road_estimates()
        self.assertEqual(fusion.roads['S005']['state'],'CLOSED')
        b['lane_status']='OPEN'; b['event_id']='c'; state.ingest(packet([b]))
        fusion=Fusion(state,assess,set()); fusion.road_estimates()
        self.assertEqual(fusion.roads['S005']['state'],'UNKNOWN')

    def test_static_mass_tunnel_and_dynamic_unknown(self):
        state=State(self.ref)
        assess=[dict(source_id=s,trust_score=0.5) for s in self.ref.sources]
        fusion=Fusion(state,assess,set()); fusion.road_estimates()
        router=Router(state,fusion)
        self.assertFalse(router.costs('AV-001',True))
        vid=next(v for v,r in self.ref.vehicles.items() if r['odd_profile_id']=='ODD-C')
        self.assertTrue(all(self.ref.segments[s]['structure']!='tunnel' for s in router.costs(vid)))

    def test_future_delivery_no_history(self):
        e=event(); e['received_time']='2026-09-28T00:01:00Z'
        state=State(self.ref); state.ingest(packet([e]))
        self.assertFalse(state.current); self.assertFalse(state.history)
