import math
from collections import defaultdict
from .basic import freshness, availability_vote, weather_at
from .state import timestamp


def weighted_median(values):
    values = sorted((v,w) for v,w in values if w > 0)
    if not values: return None
    halfway = sum(w for _,w in values)/2
    cumulative = 0
    for value, weight in values:
        cumulative += weight
        if cumulative >= halfway: return value


def confidence(agreement, weights):
    return agreement * (1-math.exp(-sum(weights)/0.5)) if weights else 0.35


class Fusion:
    def __init__(self, state, assessments, excluded, feature_trust=None, memory=None):
        self.state = state
        self.sources = {r['source_id']:r for r in assessments}
        self.excluded = excluded
        self.roads = {}
        self.weather_cache = {}
        self.feature_trust=feature_trust or {}
        self.memory=memory
        self.odd_cache={}
        self.service_cache={}

    def weight(self, event, feature='availability'):
        sid = event['source_id']
        if sid in self.excluded or sid not in self.sources or not event.get('signature_valid', True): return 0
        scale = freshness(self.state.ref,event)
        age = self.state.now-timestamp(event['event_time'])
        if not 0 <= age <= scale: return 0
        trust=self.feature_trust.get(sid,{}).get(feature,self.sources[sid]['trust_score'])
        return trust*math.exp(-age/scale)

    def road_estimates(self):
        result = []
        for sid, segment in sorted(self.state.ref.segments.items()):
            observations = {}
            for kind in ('ROAD_OBSERVATION','V2X_MESSAGE','DIGITAL_TWIN_SEGMENT'):
                for event in self.state.events(kind,sid):
                    weight = self.weight(event)
                    vote = availability_vote(event,segment)
                    if weight: observations[event['source_id']] = (event,weight,vote)
            scores = defaultdict(float)
            for _,w,v in observations.values():
                if v: scores[v] += w
            total = sum(scores.values())
            chosen = 'UNKNOWN'
            agreement = 0
            if total:
                if scores['CLOSED']:
                    count = sum(v == 'CLOSED' for _,_,v in observations.values())
                    if count >= 2 and scores['CLOSED']/total >= 0.65:
                        chosen,agreement = 'CLOSED',scores['CLOSED']/total
                elif scores['PARTIAL_BLOCK']:
                    count = sum(v == 'PARTIAL_BLOCK' for _,_,v in observations.values())
                    trusted_single=any(v=='PARTIAL_BLOCK' and self.sources[e['source_id']]['trust_score']>=0.7 for e,w,v in observations.values())
                    if (count>=2 or trusted_single) and scores['PARTIAL_BLOCK']/total >= 0.5:
                        chosen,agreement = 'PARTIAL_BLOCK',scores['PARTIAL_BLOCK']/total
                elif sum(v=='OPEN' for e,w,v in observations.values())>=2 or any(v=='OPEN' and w>=0.7 for e,w,v in observations.values()):
                    chosen,agreement = 'OPEN',1.0
            availability=chosen
            load_weights={}
            for e,w,v in observations.values():
                speed=e.get('speed_kmh') if e['event_type']=='ROAD_OBSERVATION' else None
                q=e.get('queue_estimate_m',0)
                sw=self.weight(e,'speed');qw=self.weight(e,'queue')
                direct=bool(speed is not None and sw>0 and speed<float(segment['speed_limit_kmh'])/2
                            and (e.get('occupancy_pct',0)>=70 or q>=100))
                history=self.state.history.get((e['event_type'],sid,e['source_id']),())
                queue_history={p['event_id'] for p in history if self.state.fresh(p,60) and p.get('queue_estimate_m',0)>=100}
                same_queue_sources=sum(p.get('queue_estimate_m',0)>=100 and self.weight(p,'queue')>=.2 for p,_,_ in observations.values())
                persistent_queue=q>=100 and qw>=.2 and (len(queue_history)>=3 or same_queue_sources>=2)
                if direct or persistent_queue: load_weights[e['source_id']]=max(sw if direct else 0,qw if persistent_queue else 0)
            direct_partial=any(e['event_type']=='ROAD_OBSERVATION' and v=='PARTIAL_BLOCK' for e,w,v in observations.values())
            if availability in ('OPEN','PARTIAL_BLOCK') and load_weights and not direct_partial:
                chosen='CONGESTED';agreement=1
            weights = [w for _,w,_ in observations.values()]
            conf = confidence(agreement,list(load_weights.values()) if chosen=='CONGESTED' else weights) if chosen != 'UNKNOWN' else 0.35
            lanes = weighted_median([(e.get('lane_count_open',e.get('lane_count_open_estimate')),w)
                                     for e,w,_ in observations.values() if e.get('lane_count_open',e.get('lane_count_open_estimate')) is not None])
            speed = weighted_median([(e['speed_kmh'],self.weight(e,'speed')) for e,w,_ in observations.values() if e['event_type'] == 'ROAD_OBSERVATION' and 'speed_kmh' in e])
            queue = weighted_median([(e['queue_estimate_m'],self.weight(e,'queue')) for e,w,_ in observations.values() if 'queue_estimate_m' in e])
            estimate=dict(state=chosen,confidence=conf,lanes=lanes,speed=speed,queue=queue,
                          availability=availability,load='CONGESTED' if load_weights else 'UNKNOWN',partial_block=availability=='PARTIAL_BLOCK')
            signature=tuple(sorted(e['event_id'] for e,w,v in observations.values()))
            if self.memory: estimate=self.memory.road(self.state,sid,estimate,signature)
            self.roads[sid] = estimate
            chosen=estimate['state']
            result.append(dict(segment_id=sid,state=chosen,confidence=round(estimate['confidence'],6),lanes_open_estimate=lanes,
                               rationale_codes=['STATE_'+chosen] + (['LOW_CONFIDENCE'] if chosen == 'UNKNOWN' else [])))
        return result

    def v2x_status(self,sid):
        if sid in self.service_cache: return self.service_cache[sid]
        covering=[r for r,covered in self.state.ref.rsu_segments.items() if sid in covered]
        statuses=[]
        for source in covering:
            assessment=self.sources[source]
            cycle=float(self.state.ref.sources[source]['expected_period_sec'])*max(1,len(self.state.ref.rsu_segments[source]))
            health=[e for e in self.state.events('INFRASTRUCTURE_HEALTH',source) if self.state.fresh(e,max(30,3*cycle))]
            explicit_bad=any(e.get('status')=='NO_HEARTBEAT' or e.get('network_packet_loss_pct',0)>=20 for e in health)
            events=[e for e in self.state.events('V2X_MESSAGE',sid) if e['source_id']==source and self.weight(e)>0]
            history=self.state.history.get(('V2X_MESSAGE',sid,source),())
            recent=[e for e in history if self.state.fresh(e,60) and e.get('signature_valid',True)]
            delayed=[e for e in recent if timestamp(e['received_time'])-timestamp(e['event_time'])>max(5,cycle/2)]
            sustained_delay=len(delayed)>=3 and len(delayed)>len(recent)/2
            latest=max((timestamp(e['event_time']) for e in history if timestamp(e['event_time'])<=self.state.now),default=None)
            missed=latest is not None and self.state.now-latest>3*cycle
            if assessment['status']=='FAILED' or explicit_bad or sustained_delay or missed: statuses.append('VIOLATED')
            elif events: statuses.append('COMPLIANT')
            else: statuses.append('UNKNOWN')
        status='COMPLIANT' if 'COMPLIANT' in statuses else 'VIOLATED' if not statuses or all(s=='VIOLATED' for s in statuses) else 'UNKNOWN'
        self.service_cache[sid]=status
        return status

    def weather(self, position):
        if position in self.weather_cache: return self.weather_cache[position]
        events = [(e,max(self.weight(e,f) for f in ('visibility','rain','wind'))) for _,e in weather_at(self.state,position)]
        events = [(e,w) for e,w in events if w > 0]
        if not events:
            self.weather_cache[position] = None
            return None
        result = {}
        result['sources']=sorted(e['source_id'] for e,w in events)
        result['event_ids']=sorted(e['event_id'] for e,w in events)
        result['ranges']={}
        agreements = []
        for field in ('visibility_m','rain_level','wind_mps'):
            feature={'visibility_m':'visibility','rain_level':'rain','wind_mps':'wind'}[field]
            applicable=[(e,self.weight(e,feature)) for e,w in events if self.weight(e,feature)>0]
            value = weighted_median([(e[field],w) for e,w in applicable])
            if value is None:
                self.weather_cache[position]=None
                return None
            result['ranges'][field]=(min(e[field] for e,w in applicable),max(e[field] for e,w in applicable))
            tolerance = max(10,0.1*value) if field == 'visibility_m' else 0 if field == 'rain_level' else 2
            agreement = sum(w for e,w in applicable if abs(e[field]-value) <= tolerance)/sum(w for _,w in applicable)
            result[field] = value
            agreements.append(agreement)
        surfaces = defaultdict(float)
        for e,w in events: surfaces[e['road_surface']] += w
        result['road_surface'] = min(surfaces,key=lambda k:(-surfaces[k],k))
        result['confidence'] = confidence(min(agreements),[w for _,w in events])
        result['agreement'] = min(agreements)
        result['risk_sources_visibility']=[e['source_id'] for e,w in events if self.weight(e,'visibility')>0 and e['visibility_m']==result['ranges']['visibility_m'][0]]
        result['risk_sources_rain']=[e['source_id'] for e,w in events if self.weight(e,'rain')>0 and e['rain_level']==result['ranges']['rain_level'][1]]
        self.weather_cache[position] = result
        return result
