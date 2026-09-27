import os
import unittest
from corridor.reference import Reference
from corridor.state import State
from corridor.odd import check
from test_odd import WeatherFusion


class WeatherBoundaries(unittest.TestCase):
    def test_numeric_disagreement_same_odd_side(self):
        state=State(Reference(os.environ['CORRIDOR_REFERENCE']))
        event=dict(segment_id='S001',offset_m=0,event_time='1970-01-01T00:00:00Z',gnss_quality=1,map_age_min=0)
        weather=dict(visibility_m=2000,rain_level=0,wind_mps=0,confidence=0.4,agreement=0.4,
                     ranges={'visibility_m':(1000,3000),'rain_level':(0,0)})
        self.assertEqual(check(state,WeatherFusion(weather),'AV-001',event)['odd_status'],'COMPLIANT')
        weather['ranges']['visibility_m']=(50,2000)
        self.assertEqual(check(state,WeatherFusion(weather),'AV-001',event)['odd_status'],'UNKNOWN')
