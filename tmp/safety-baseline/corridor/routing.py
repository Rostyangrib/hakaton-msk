import heapq
import math
from .basic import telemetry
from .odd import check
from .limits import segment_speed_limit


def dijkstra(segments,outgoing,start,target,costs):
    queue = [(0,(),start)]
    best = {start:(0,())}
    while queue:
        cost,path,node = heapq.heappop(queue)
        if best.get(node) != (cost,path): continue
        if node == target: return list(path),cost
        for sid in outgoing.get(node,[]):
            weight = costs.get(sid)
            if weight is None: continue
            if not math.isfinite(weight) or weight <= 0: raise ValueError('Nonpositive edge cost')
            end = segments[sid]['to_node']
            candidate = cost+weight,path+(sid,)
            if end not in best or candidate < best[end]:
                best[end] = candidate
                heapq.heappush(queue,(*candidate,end))
    return None,math.inf


def route_choice(static,dynamic,remaining,length,early_danger=False):
    if dynamic is None: return 'HOLD'
    if (remaining <= min(500,length) or early_danger) and static != dynamic:
        return 'REROUTE' if dynamic else 'CONTINUE'
    return 'CONTINUE'


class Router:
    def __init__(self,state,fusion):
        self.state,self.fusion = state,fusion
        self.paths = {}
        self.cost_cache = {}

    def costs(self,vid,dynamic=False,confirmed=False):
        key=vid,dynamic,confirmed
        if key in self.cost_cache: return self.cost_cache[key]
        result = {}
        vehicle = self.state.ref.vehicles[vid]
        event = telemetry(self.state,vid)
        for sid,segment in self.state.ref.segments.items():
            if not self.state.ref.compatible(vid,sid): continue
            speed = min(float(segment['speed_limit_kmh']),float(vehicle['nominal_max_speed_kmh']))
            factor = 1
            if dynamic:
                estimate = self.fusion.roads[sid]
                if estimate['state'] in ('CLOSED','UNKNOWN'): continue
                odd=check(self.state,self.fusion,vid,event,sid,True)['odd_status'] if event else 'UNKNOWN'
                if event is None or odd == 'VIOLATED' or (confirmed and odd!='COMPLIANT'): continue
                lanes = estimate['lanes'] if estimate['lanes'] is not None else int(segment['lanes'])
                speed = min(speed,estimate['speed']) if estimate['speed'] is not None else speed*lanes/int(segment['lanes'])
                limit=segment_speed_limit(self.state,self.fusion,vid,event,sid,True)
                if limit is None: continue
                speed=min(speed,limit)
                if speed <= 0: speed = 1  # Positive time, never a division by zero.
                factor = 1+(1-estimate['confidence'])
            result[sid] = float(segment['length_m'])/(speed/3.6)*factor
        self.cost_cache[key] = result
        return result

    def route(self,vid,dynamic=False,target=None):
        event = telemetry(self.state,vid)
        if event is None: return None,math.inf
        start = self.state.ref.segments[event['segment_id']]['to_node']
        target = target or self.state.ref.hubs[self.state.ref.vehicles[vid]['destination_hub_id']]['node_id']
        return dijkstra(self.state.ref.segments,self.state.ref.outgoing,start,target,self.costs(vid,dynamic))

    def apply(self,decision):
        assessments = {r['vehicle_id']:r for r in decision['vehicle_assessments']}
        for action in decision['vehicle_actions']:
            vid = action['vehicle_id']
            event = telemetry(self.state,vid)
            if event is None or action['motion_action'] == 'NO_ACTION' or assessments[vid]['odd_status'] != 'COMPLIANT': continue
            current = self.fusion.roads[event['segment_id']]
            if current['state'] in ('CLOSED','UNKNOWN'): continue
            static,_ = self.route(vid)
            dynamic,_ = self.route(vid,True)
            self.paths[vid] = dict(static=static,dynamic=dynamic)
            early = bool(static) and self.fusion.roads[static[0]]['state'] == 'CLOSED'
            length=float(self.state.ref.segments[event['segment_id']]['length_m'])
            choice = route_choice(static,dynamic,length-event['offset_m'],length,early)
            action.update(motion_action=choice,confidence=min(current['confidence'],assessments[vid]['confidence']))
            if choice == 'REROUTE':
                action['route_segment_ids'] = dynamic
                action['rationale_codes'] = ['ROUTE_CLOSED'] if early else ['STATE_'+self.fusion.roads[dynamic[0]]['state']]
            elif choice == 'HOLD':
                action['rationale_codes'] = ['LOW_CONFIDENCE']
