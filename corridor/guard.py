from collections import Counter
from .basic import telemetry
from .contract import skeleton
from .resources import support_selection
from .odd import check


def errors(state,fusion,decision):
    ref=state.ref
    estimates={r['segment_id']:r for r in decision['state_estimates']}
    odd={r['vehicle_id']:r for r in decision['vehicle_assessments']}
    issues={}
    assigned=Counter(a.get('safe_stop_id') for a in decision['vehicle_actions'] if a['motion_action']=='SAFE_STOP')
    support=sum(a['remote_support_required'] for a in decision['vehicle_actions'])
    for a in decision['vehicle_actions']:
        vid=a['vehicle_id']; event=telemetry(state,vid); reasons=[]
        if support>min(6,ref.support['max_parallel_sessions']) and a['remote_support_required']: reasons.append('support_capacity')
        if odd[vid]['odd_status']=='VIOLATED' and a['motion_action'] in ('CONTINUE','NO_ACTION') and not a['remote_support_required']: reasons.append('odd_without_support')
        if a['motion_action']=='NO_ACTION' and state.inactivity[vid]<3: reasons.append('unconfirmed_inactivity')
        if a['motion_action'] in ('CONTINUE','LIMIT_SPEED','REROUTE'):
            if event is None or not state.fresh(event,15): reasons.append('missing_position')
            elif estimates[event['segment_id']]['state'] in ('CLOSED','UNKNOWN'): reasons.append('unsafe_current_segment')
            if odd[vid]['odd_status']!='COMPLIANT': reasons.append('unconfirmed_current_odd')
        if a['motion_action']=='REROUTE':
            route=a.get('route_segment_ids',[])
            node=ref.segments[event['segment_id']]['to_node'] if event else None
            if not route: reasons.append('empty_route')
            for sid in route:
                if sid not in ref.segments: reasons.append('unknown_edge'); break
                segment=ref.segments[sid]
                if node != segment['from_node']: reasons.append('discontinuous_route')
                node=segment['to_node']
                if not ref.compatible(vid,sid): reasons.append('incompatible_edge')
                if estimates[sid]['state'] in ('CLOSED','UNKNOWN'): reasons.append('unavailable_edge')
                if fusion and check(state,fusion,vid,event,sid,True)['odd_status']=='VIOLATED': reasons.append('incompatible_future_odd')
            target=ref.hubs[ref.vehicles[vid]['destination_hub_id']]['node_id']
            if node != target: reasons.append('wrong_destination')
        if a['motion_action']=='SAFE_STOP':
            stop=ref.stops.get(a.get('safe_stop_id'))
            if stop is None: reasons.append('unknown_stop')
            else:
                sid=stop['segment_id']
                if assigned[a['safe_stop_id']]>int(stop['capacity_vehicles']): reasons.append('stop_capacity')
                if float(ref.vehicles[vid]['gross_mass_t'])>float(stop['max_vehicle_mass_t']) or not ref.compatible(vid,sid): reasons.append('stop_mass_or_structure')
                if estimates[sid]['state'] in ('CLOSED','UNKNOWN'): reasons.append('unavailable_stop')
                if event is None: reasons.append('stop_missing_position')
                elif sid != event['segment_id']:
                    start=ref.segments[event['segment_id']]['to_node']; target=ref.segments[sid]['from_node']
                    visited={start}; stack=[start]
                    while stack:
                        node=stack.pop()
                        for edge in ref.outgoing.get(node,[]):
                            end=ref.segments[edge]['to_node']
                            if end not in visited and ref.compatible(vid,edge) and estimates[edge]['state'] not in ('CLOSED','UNKNOWN'):
                                if fusion and check(state,fusion,vid,event,edge,True)['odd_status']=='VIOLATED': continue
                                visited.add(end); stack.append(end)
                    if target not in visited: reasons.append('unreachable_stop')
        for code in a['rationale_codes']:
            if code.startswith('ODD_') and code[4:] not in odd[vid]['violation_codes']: reasons.append('unfounded_odd_reason')
            if code=='AT_HUB_OR_INACTIVE' and state.inactivity[vid]<3: reasons.append('unfounded_inactivity_reason')
            if code.startswith('STATE_') and event:
                sid=a['route_segment_ids'][0] if a['motion_action']=='REROUTE' and a.get('route_segment_ids') else event['segment_id']
                if sid in estimates and code[6:] != estimates[sid]['state']: reasons.append('unfounded_state_reason')
        if reasons: issues[vid]=sorted(set(reasons))
    return issues


def hold(action):
    vid=action['vehicle_id']
    action.clear()
    action.update(vehicle_id=vid,motion_action='HOLD',remote_support_required=False,confidence=0.35,rationale_codes=['LOW_CONFIDENCE'])


def diagnostic_support(state,decision):
    estimates={r['vehicle_id']:r for r in decision['vehicle_assessments']}
    requests={a['vehicle_id']:(1 if estimates[a['vehicle_id']]['odd_status']=='VIOLATED' else 2,int(state.ref.vehicles[a['vehicle_id']]['cargo_priority']))
              for a in decision['vehicle_actions'] if a['motion_action']=='HOLD'}
    selected=support_selection(requests,min(6,state.ref.support['max_parallel_sessions']))
    for a in decision['vehicle_actions']:
        a['remote_support_required']=a['vehicle_id'] in selected
        if a['vehicle_id'] in requests and a['vehicle_id'] not in selected:
            a['rationale_codes']=sorted(set(a['rationale_codes']+['REMOTE_SUPPORT_CAPACITY']))


def finalize(state,fusion,router,contract,packet,decision):
    from . import resources
    try:
        contract.validate(decision)
    except Exception as exc:
        state.diagnostics.append('contract_recovery:'+type(exc).__name__)
        decision=skeleton(packet,state.ref)
        diagnostic_support(state,decision)
        contract.validate(decision)
        return decision
    issues=errors(state,fusion,decision)
    if issues:
        state.diagnostics.append({'planner_recovery':issues})
        for a in decision['vehicle_actions']:
            if a['vehicle_id'] in issues: hold(a)
        try:
            resources.apply(state,fusion,router,decision)
            unresolved=errors(state,fusion,decision)
        except Exception as exc:
            state.diagnostics.append('recovery_exception:'+type(exc).__name__)
            unresolved={a['vehicle_id']:['recovery_exception'] for a in decision['vehicle_actions']}
        for a in decision['vehicle_actions']:
            if a['vehicle_id'] in unresolved: hold(a)
        if unresolved: diagnostic_support(state,decision)
    contract.validate(decision)
    if errors(state,fusion,decision): raise ValueError('Final guard could not produce valid snapshot')
    return decision
