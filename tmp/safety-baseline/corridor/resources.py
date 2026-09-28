from collections import Counter
import math
from .basic import telemetry
from .routing import dijkstra
from .limits import segment_speed_limit, current_fragment_allowed
from .odd import check


def min_cost_assignment(candidates, capacities):
    """Maximum cardinality then minimum travel time via residual min-cost flow."""
    vehicles = sorted(candidates)
    stops = sorted(capacities)
    nodes = ['source'] + ['v:'+v for v in vehicles] + ['s:'+s for s in stops] + ['sink']
    graph = {n:[] for n in nodes}
    def add(a,b,capacity,cost):
        graph[a].append([b,len(graph[b]),capacity,cost])
        graph[b].append([a,len(graph[a])-1,0,-cost])
    for v in vehicles:
        add('source','v:'+v,1,0)
        for s,cost in sorted(candidates[v].items()):
            if s in capacities and math.isfinite(cost): add('v:'+v,'s:'+s,1,cost)
    for s in stops: add('s:'+s,'sink',capacities[s],0)
    while True:
        dist = {n:math.inf for n in nodes}; dist['source']=0
        prev = {}
        for _ in range(len(nodes)-1):
            changed=False
            for a in nodes:
                if not math.isfinite(dist[a]): continue
                for i,(b,_,cap,cost) in enumerate(graph[a]):
                    if cap and dist[a]+cost < dist[b]-1e-9:
                        dist[b]=dist[a]+cost; prev[b]=(a,i); changed=True
            if not changed: break
        if 'sink' not in prev: break
        b='sink'
        while b != 'source':
            a,i=prev[b]; edge=graph[a][i]
            edge[2]-=1; graph[b][edge[1]][2]+=1
            b=a
    result={}
    for v in vehicles:
        for b,_,cap,_ in graph['v:'+v]:
            if b.startswith('s:') and cap == 0: result[v]=b[2:]
    return result


def support_selection(requests, limit, previous=()):
    """Risk group, cargo, time to hazard, incumbent on equal terms, id."""
    previous=set(previous)
    return set(sorted(requests,key=lambda v:(requests[v][0],requests[v][1],requests[v][2] if len(requests[v])>2 else math.inf,v not in previous,v))[:limit])


def waiting_zone(state,vid,event):
    if not event or not state.fresh(event,15): return False
    try: pos=state.ref.position(event['segment_id'],event['offset_m'])
    except ValueError: return False
    if any(math.dist(pos,(float(h['x_m']),float(h['y_m']))) <= 50 for h in state.ref.hubs.values()): return True
    return any(s['segment_id'] == event['segment_id'] and float(state.ref.vehicles[vid]['gross_mass_t']) <= float(s['max_vehicle_mass_t'])
               for s in state.ref.stops.values())


def speed_limit(state,fusion,vid,event,route=None):
    limit=segment_speed_limit(state,fusion,vid,event)
    if limit is not None and route:
        first=fusion.roads[route[0]]
        if first['state']=='CONGESTED' or (first['queue'] or 0)>=100: limit=min(limit,30)
    return limit


def hazard_eta(state,fusion,router,vid,event,current_danger=False):
    if current_danger: return 0.0
    if not event or not state.fresh(event,15): return 0.0
    path=router.paths.get(vid,{}).get('static')
    if not path: return math.inf
    first=path[0]
    risk=fusion.roads[first]['state'] in ('CLOSED','UNKNOWN') or check(state,fusion,vid,event,first,True)['odd_status']!='COMPLIANT'
    if not risk: return math.inf
    remaining=max(0,float(state.ref.segments[event['segment_id']]['length_m'])-event['offset_m'])
    speed=max(1,float(event.get('speed_kmh',0)))
    return remaining/(speed/3.6)


def apply(state,fusion,router,decision):
    assessments={r['vehicle_id']:r for r in decision['vehicle_assessments']}
    candidates,requests={},{}
    actions={r['vehicle_id']:r for r in decision['vehicle_actions']}
    for vid,action in actions.items():
        if action['motion_action']=='NO_ACTION': continue
        event=telemetry(state,vid)
        estimate=assessments[vid]
        danger=False
        from .board import uncertain as board_uncertain
        board_risk=bool(event and state.fresh(event,15) and board_uncertain(state,fusion,vid,event))
        if event and state.fresh(event,15):
            road=fusion.roads[event['segment_id']]
            danger=road['state']=='CLOSED'
            weather=fusion.weather(state.ref.position(event['segment_id'],event['offset_m']))
            danger=danger or bool(weather and weather['road_surface']=='FLOODED')
        requires = danger or estimate['odd_status']!='COMPLIANT' or action['motion_action']=='HOLD' or bool(event and event.get('autonomy_state')=='REMOTE_REQUESTED')
        if requires:
            requests[vid]=(0 if danger or board_risk else 1 if estimate['odd_status']=='VIOLATED' else 2,int(state.ref.vehicles[vid]['cargo_priority']),hazard_eta(state,fusion,router,vid,event,danger or board_risk or estimate['odd_status']!='COMPLIANT'))
        unsafe=danger or estimate['odd_status']=='VIOLATED' or action['motion_action']=='HOLD'
        if unsafe:
            action.pop('route_segment_ids',None)
            action.update(motion_action='HOLD',rationale_codes=['ODD_'+c for c in estimate['violation_codes']] or ['LOW_CONFIDENCE'])
            if not waiting_zone(state,vid,event) and current_fragment_allowed(state,fusion,vid,event):
                costs=router.costs(vid,True,confirmed=True)
                options={}
                for ss,stop in state.ref.stops.items():
                    sid=stop['segment_id']
                    if float(state.ref.vehicles[vid]['gross_mass_t']) > float(stop['max_vehicle_mass_t']) or sid not in costs: continue
                    if sid == event['segment_id']:
                        options[ss]=costs[sid]*(1-event['offset_m']/float(state.ref.segments[sid]['length_m']))
                    else:
                        path,cost=dijkstra(state.ref.segments,state.ref.outgoing,state.ref.segments[event['segment_id']]['to_node'],state.ref.segments[sid]['from_node'],costs)
                        if path is not None: options[ss]=cost+costs[sid]+segment_speed_limit(state,fusion,vid,event)**-1*3.6*max(0,float(state.ref.segments[event['segment_id']]['length_m'])-event['offset_m'])
                candidates[vid]=options
            if not waiting_zone(state,vid,event):
                state.diagnostics.append('no_confirmed_waiting_zone:'+vid)
                if vid not in candidates:
                    candidates[vid]={}
                    state.diagnostics.append('unsafe_current_stop_approach:'+vid)
        elif event:
            # A REROUTE cannot also carry LIMIT_SPEED in this single-action protocol.
            # Preserve the agreed route rule; speed constraints still affect route cost.
            limit=speed_limit(state,fusion,vid,event,router.paths.get(vid,{}).get('dynamic'))
            if limit is None:
                action.update(motion_action='HOLD',rationale_codes=['LOW_CONFIDENCE'])
                requests[vid]=(0,int(state.ref.vehicles[vid]['cargo_priority']))
            elif action['motion_action']=='CONTINUE' and limit < min(float(state.ref.segments[event['segment_id']]['speed_limit_kmh']),float(state.ref.vehicles[vid]['nominal_max_speed_kmh'])):
                action.update(motion_action='LIMIT_SPEED',speed_limit_kmh=limit)
        if event:
            hubid=state.ref.vehicles[vid]['destination_hub_id']
            statuses=state.events('HUB_STATUS',hubid)
            blocked=any(state.fresh(e,45) and (not e['accepting_new_arrivals'] or e['capacity_used_pct']>=100) for e in statuses)
            if blocked and waiting_zone(state,vid,event):
                action.pop('route_segment_ids',None); action.pop('speed_limit_kmh',None)
                action.update(motion_action='HOLD',rationale_codes=['ROUTE_HUB_CAPACITY'])
    assignments=min_cost_assignment(candidates,{s:int(r['capacity_vehicles']) for s,r in state.ref.stops.items()})
    for vid,ss in assignments.items():
        actions[vid].update(motion_action='SAFE_STOP',safe_stop_id=ss)
    for vid,options in candidates.items():
        if vid not in assignments:
            state.diagnostics.append('impossible_safe_solution:'+vid)
            if options: actions[vid]['rationale_codes'].append('SAFE_STOP_CAPACITY')
    previous=[]
    if state.recommendations:
        previous=[r['vehicle_id'] for r in state.recommendations[-1][1]['vehicle_actions'] if r['remote_support_required']]
    selected=support_selection(requests,min(6,state.ref.support['max_parallel_sessions']),previous)
    for vid,action in actions.items():
        action['remote_support_required']=vid in selected
        if vid in requests and vid not in selected: action['rationale_codes'].append('REMOTE_SUPPORT_CAPACITY')
        action['rationale_codes']=sorted(set(action['rationale_codes']))
