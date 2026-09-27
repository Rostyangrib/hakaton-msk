import math
from collections import defaultdict
from .basic import freshness, road_vote, weather_at
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
    def __init__(self, state, assessments, excluded):
        self.state = state
        self.sources = {r['source_id']:r for r in assessments}
        self.excluded = excluded
        self.roads = {}
        self.weather_cache = {}

    def weight(self, event):
        sid = event['source_id']
        if sid in self.excluded or sid not in self.sources or not event.get('signature_valid', True): return 0
        scale = freshness(self.state.ref,event)
        age = self.state.now-timestamp(event['event_time'])
        if not 0 <= age <= scale: return 0
        return self.sources[sid]['trust_score']*math.exp(-age/scale)

    def road_estimates(self):
        result = []
        for sid, segment in sorted(self.state.ref.segments.items()):
            observations = {}
            for kind in ('ROAD_OBSERVATION','V2X_MESSAGE','DIGITAL_TWIN_SEGMENT'):
                for event in self.state.events(kind,sid):
                    weight = self.weight(event)
                    vote = road_vote(event,segment)
                    if weight and vote: observations[event['source_id']] = (event,weight,vote)
            scores = defaultdict(float)
            for _,w,v in observations.values(): scores[v] += w
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
                    if count >= 2 and scores['PARTIAL_BLOCK']/total >= 0.5:
                        chosen,agreement = 'PARTIAL_BLOCK',scores['PARTIAL_BLOCK']/total
                elif scores['CONGESTED']:
                    # Availability evidence OPEN is compatible with measured congestion.
                    chosen,agreement = 'CONGESTED',1.0
                elif len(observations) >= 2:
                    chosen,agreement = 'OPEN',1.0
            weights = [w for _,w,_ in observations.values()]
            conf = confidence(agreement,weights) if chosen != 'UNKNOWN' else 0.35
            lanes = weighted_median([(e.get('lane_count_open',e.get('lane_count_open_estimate')),w)
                                     for e,w,_ in observations.values() if e.get('lane_count_open',e.get('lane_count_open_estimate')) is not None])
            speed = weighted_median([(e['speed_kmh'],w) for e,w,_ in observations.values() if e['event_type'] == 'ROAD_OBSERVATION' and 'speed_kmh' in e])
            queue = weighted_median([(e['queue_estimate_m'],w) for e,w,_ in observations.values() if 'queue_estimate_m' in e])
            self.roads[sid] = dict(state=chosen,confidence=conf,lanes=lanes,speed=speed,queue=queue)
            result.append(dict(segment_id=sid,state=chosen,confidence=round(conf,6),lanes_open_estimate=lanes,
                               rationale_codes=['STATE_'+chosen] + (['LOW_CONFIDENCE'] if chosen == 'UNKNOWN' else [])))
        return result

    def weather(self, position):
        if position in self.weather_cache: return self.weather_cache[position]
        events = [(e,self.weight(e)) for _,e in weather_at(self.state,position)]
        events = [(e,w) for e,w in events if w > 0]
        if not events:
            self.weather_cache[position] = None
            return None
        result = {}
        agreements = []
        for field in ('visibility_m','rain_level','wind_mps'):
            value = weighted_median([(e[field],w) for e,w in events])
            tolerance = max(10,0.1*value) if field == 'visibility_m' else 0 if field == 'rain_level' else 2
            agreement = sum(w for e,w in events if abs(e[field]-value) <= tolerance)/sum(w for _,w in events)
            result[field] = value
            agreements.append(agreement)
        surfaces = defaultdict(float)
        for e,w in events: surfaces[e['road_surface']] += w
        result['road_surface'] = min(surfaces,key=lambda k:(-surfaces[k],k))
        result['confidence'] = confidence(min(agreements),[w for _,w in events])
        result['agreement'] = min(agreements)
        self.weather_cache[position] = result
        return result
