import os
import unittest
from corridor.odd import check
from corridor.reference import Reference
from corridor.state import State


class WeatherFusion:
    def __init__(self, weather): self.data = weather
    def weather(self, position): return self.data
    def weight(self,event): return 1


class OddTests(unittest.TestCase):
    def setUp(self):
        self.state = State(Reference(os.environ['CORRIDOR_REFERENCE']))
        self.event = dict(segment_id='S001',offset_m=0,event_time='1970-01-01T00:00:00Z',gnss_quality=0.55,map_age_min=1440)
        self.weather = dict(visibility_m=70,rain_level=3,wind_mps=22,confidence=0.8,agreement=1)

    def test_equality_and_thresholds(self):
        fusion = WeatherFusion(self.weather)
        self.assertEqual(check(self.state,fusion,'AV-001',self.event)['odd_status'],'COMPLIANT')
        for field,value,code in [('gnss_quality',0.54,'GNSS'),('map_age_min',1440.01,'MAP_AGE')]:
            event = dict(self.event); event[field] = value
            self.assertIn(code,check(self.state,fusion,'AV-001',event)['violation_codes'])
        self.weather['visibility_m']=69
        self.assertIn('VISIBILITY',check(self.state,fusion,'AV-001',self.event)['violation_codes'])

    def test_unknown_weather_wind(self):
        self.assertEqual(check(self.state,WeatherFusion(None),'AV-001',self.event)['odd_status'],'UNKNOWN')
        self.weather['wind_mps']=23
        result=check(self.state,WeatherFusion(self.weather),'AV-001',self.event)
        self.assertEqual(result['odd_status'],'UNKNOWN')
        self.assertNotIn('WIND',result['violation_codes'])

    def test_onboard_degradation_blocks_current_motion_without_fabricating_visibility(self):
        clear=dict(self.weather,visibility_m=1000,rain_level=0,wind_mps=0)
        e=dict(self.event,gnss_quality=1,map_age_min=0,
               autonomy_state='DEGRADED',perception_health=.8)
        fusion=WeatherFusion(clear)
        current=check(self.state,fusion,'AV-001',e)
        self.assertEqual(current['odd_status'],'UNKNOWN')
        self.assertEqual(current['violation_codes'],[])
        self.assertIn('perception_degraded_odd_uncertain:AV-001',self.state.diagnostics)
        self.assertEqual(check(self.state,fusion,'AV-001',e,future=True)['odd_status'],'COMPLIANT')
        e['perception_health']=0.9
        self.assertEqual(check(self.state,fusion,'AV-001',e)['odd_status'],'COMPLIANT')
        e['perception_health']=0.8
        e['autonomy_state']='REMOTE_REQUESTED'
        self.assertEqual(check(self.state,fusion,'AV-001',e)['odd_status'],'UNKNOWN')
        e['autonomy_state']='AUTO'
        self.assertEqual(check(self.state,fusion,'AV-001',e)['odd_status'],'COMPLIANT')

    def test_all_profile_boundaries_and_v2x(self):
        self.state.current[('V2X_MESSAGE','S001','RSU-01')] = dict(source_id='RSU-01')
        for profile_id,profile in self.state.ref.profiles.items():
            vid = next(v for v,r in self.state.ref.vehicles.items() if r['odd_profile_id']==profile_id)
            event=dict(self.event,gnss_quality=profile['min_gnss_quality'],map_age_min=profile['max_map_age_min'])
            weather=dict(self.weather,visibility_m=profile['min_visibility_m'],rain_level=profile['max_rain_level'],wind_mps=0)
            self.assertEqual(check(self.state,WeatherFusion(weather),vid,event)['odd_status'],'COMPLIANT')
            event['map_age_min'] += 0.001
            self.assertIn('MAP_AGE',check(self.state,WeatherFusion(weather),vid,event)['violation_codes'])
        self.state.current.clear()
        self.state._event_cache.clear()
        vid=next(v for v,r in self.state.ref.vehicles.items() if r['odd_profile_id']=='ODD-D')
        fusion=WeatherFusion(dict(self.weather,visibility_m=100,rain_level=0,wind_mps=0))
        fusion.sources={r:dict(status='FAILED') for r in self.state.ref.rsus}
        result=check(self.state,fusion,vid,dict(self.event,gnss_quality=1,map_age_min=60))
        self.assertIn('V2X',result['violation_codes'])
