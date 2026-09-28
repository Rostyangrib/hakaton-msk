"""Shared situational caps for commands and dynamic edge travel times."""
from .basic import telemetry


def segment_speed_limit(state,fusion,vid,event,sid=None,future=False):
    sid=sid or event['segment_id']
    segment=state.ref.segments[sid]
    limits=[float(segment['speed_limit_kmh']),float(state.ref.vehicles[vid]['nominal_max_speed_kmh'])]
    estimate=fusion.roads[sid]
    if estimate['state']=='PARTIAL_BLOCK' or estimate.get('partial_block'): limits.append(40)
    if estimate['state']=='CONGESTED' or estimate.get('load')=='CONGESTED' or (estimate['queue'] or 0)>=100: limits.append(30)
    position=state.ref.position(sid,float(segment['length_m'])/2 if future else event['offset_m'])
    weather=fusion.weather(position)
    if weather:
        if weather['road_surface']=='FLOODED': return None
        if weather['road_surface']=='WET': limits.append(60)
        if weather['road_surface']=='WATER_FILM': limits.append(40)
        minimum=state.ref.profiles[state.ref.vehicles[vid]['odd_profile_id']]['min_visibility_m']
        if minimum <= weather['visibility_m'] <= 1.25*minimum: limits.append(30)
    for e in state.events('V2X_MESSAGE',sid):
        if fusion.weight(e)>0 and fusion.sources[e['source_id']]['trust_score']>=0.4 and e['advisory_speed_kmh']>0:
            limits.append(e['advisory_speed_kmh'])
    return min(limits)


def current_fragment_allowed(state,fusion,vid,event):
    """A stop approach may not silently traverse an unsafe current fragment."""
    if event is None or not state.fresh(event,15): return False
    sid=event['segment_id']
    if not state.ref.compatible(vid,sid) or fusion.roads[sid]['state'] in ('CLOSED','UNKNOWN'): return False
    from .odd import check
    return check(state,fusion,vid,event)['odd_status']=='COMPLIANT' and segment_speed_limit(state,fusion,vid,event) is not None
