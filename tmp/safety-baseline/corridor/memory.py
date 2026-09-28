"""Causal warning persistence; no recommendation is treated as executed."""
import math


class SafetyMemory:
    def __init__(self):
        self.generation=None
        self.roads={}
        self.weather={}
        self.boards={}

    def reset_for(self,state):
        if self.generation!=state.generation:
            self.__init__()
            self.generation=state.generation

    def board_uncertain(self,state,vid,event):
        from .board import issues
        from .state import timestamp
        self.reset_for(state)
        bad=issues(event)
        old=self.boards.get(vid)
        stamp=timestamp(event['event_time'])
        if bad:
            self.boards[vid]=dict(clear=0,last_time=stamp,last_id=event.get('event_id'),faults=bad)
            return True
        if old is None:
            return False
        # Re-reading a packet, duplicates and late good measurements are not recovery.
        if stamp>old['last_time'] and event.get('event_id')!=old['last_id']:
            healthy=(event.get('perception_health',0)>=0.9 and event.get('localization_confidence',0)>=0.8
                     and event.get('communication_latency_ms',2000)<1000 and event.get('packet_loss_pct_10s',50)<20)
            old['clear']=old['clear']+1 if healthy else 0
            old['last_time']=stamp
            old['last_id']=event.get('event_id')
            if old['clear']>=3:
                self.boards.pop(vid,None)
                return False
        return True

    def road(self,state,sid,estimate,signature):
        self.reset_for(state)
        previous=self.roads.get(sid)
        hazardous=estimate['state'] in ('CLOSED','PARTIAL_BLOCK','CONGESTED')
        if hazardous:
            self.roads[sid]=dict(value=dict(estimate),time=state.now,clear=0,signature=signature,seen=set(signature))
        elif previous and estimate['state']=='OPEN' and state.now-previous['time']<=60:
            if set(signature)-previous['seen']:
                previous['clear']+=1
                previous['signature']=signature
                previous['seen'].update(signature)
            if previous['clear']<3:
                # Do not carry a fabricated closure or lane count after clearance.
                estimate=dict(estimate,state='UNKNOWN',confidence=0.35,recovery_pending=True)
            else: self.roads.pop(sid,None)
        elif previous and state.now-previous['time']>60:
            self.roads.pop(sid,None)
        return estimate

    def weather_uncertain(self,state,vid,position,weather):
        self.reset_for(state)
        profile=state.ref.profiles[state.ref.vehicles[vid]['odd_profile_id']]
        ranges=weather.get('ranges',{}) if weather else {}
        visibility=ranges.get('visibility_m',(weather['visibility_m'],)*2) if weather else None
        rain=ranges.get('rain_level',(weather['rain_level'],)*2) if weather else None
        signature=tuple(weather.get('event_ids',())) if weather else ()
        bad=bool(weather and (visibility[0]<profile['min_visibility_m'] or rain[1]>profile['max_rain_level']))
        contributors=set()
        if weather and visibility[0]<profile['min_visibility_m']: contributors.update(weather.get('risk_sources_visibility',()))
        if weather and rain[1]>profile['max_rain_level']: contributors.update(weather.get('risk_sources_rain',()))
        old=self.weather.get(vid)
        if bad:
            if old is None or signature!=old['signature']:
                self.weather[vid]=dict(time=state.now,position=position,clear=0,signature=signature,sources=contributors,seen=set(signature))
            return False  # Current range already drives the ordinary ODD check.
        if old is None: return False
        if state.now-old['time']>90 or math.dist(position,old['position'])>5000:
            self.weather.pop(vid,None)
            return False
        observed=set(weather.get('sources',())) if weather else set()
        # New stations outside the old warning's coverage do not prove clearance.
        if weather and (not old['sources'] or old['sources']<=observed) and set(signature)-old['seen']:
            old['clear']+=1
            old['signature']=signature
            old['seen'].update(signature)
        if old['clear']>=3:
            self.weather.pop(vid,None)
            return False
        return True
