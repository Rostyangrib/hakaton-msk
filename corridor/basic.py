import math
from .state import timestamp


def freshness(ref, event):
    period = float(ref.sources.get(event['source_id'], {}).get('expected_period_sec', 5))
    if event['event_type'] == 'V2X_MESSAGE':
        period *= len(ref.rsu_segments.get(event['source_id'], ())) or 1
    return 3 * period


def road_vote(event, segment):
    explicit = event.get('closure_state', event.get('lane_status'))
    lanes = event.get('lane_count_open', event.get('lane_count_open_estimate'))
    if explicit == 'CLOSED' or lanes == 0: return 'CLOSED'
    if explicit == 'PARTIAL_BLOCK' or (lanes is not None and lanes < int(segment['lanes'])): return 'PARTIAL_BLOCK'
    speed = event.get('speed_kmh')
    if speed is not None and speed < float(segment['speed_limit_kmh']) / 2 and (event.get('occupancy_pct', 0) >= 70 or event.get('queue_estimate_m', 0) >= 100):
        return 'CONGESTED'
    if explicit == 'OPEN' or lanes == int(segment['lanes']): return 'OPEN'
    return None


def roads(state):
    output = []
    for sid, segment in sorted(state.ref.segments.items()):
        votes = {}
        for kind in ('ROAD_OBSERVATION', 'V2X_MESSAGE', 'DIGITAL_TWIN_SEGMENT'):
            for event in state.events(kind, sid):
                if state.fresh(event, freshness(state.ref, event)) and event.get('signature_valid', True):
                    vote = road_vote(event, segment)
                    if vote: votes[event['source_id']] = vote
        values = list(votes.values())
        if not values or ('CLOSED' in values and len(set(values)) > 1):
            chosen = 'UNKNOWN'
        elif values.count('CLOSED') >= 2: chosen = 'CLOSED'
        elif 'CLOSED' in values: chosen = 'UNKNOWN'
        elif 'PARTIAL_BLOCK' in values: chosen = 'PARTIAL_BLOCK' if values.count('PARTIAL_BLOCK') >= 2 else 'UNKNOWN'
        elif 'CONGESTED' in values: chosen = 'CONGESTED'
        else: chosen = 'OPEN' if len(values) >= 2 else 'UNKNOWN'
        output.append(dict(segment_id=sid, state=chosen, confidence=0.35 if chosen == 'UNKNOWN' else 0.7,
                           rationale_codes=['STATE_' + chosen]))
    return output


def telemetry(state, vid):
    candidates = state.events('VEHICLE_TELEMETRY', vid)
    return max(candidates, key=lambda e: (timestamp(e['event_time']), e['event_id']), default=None)


def weather_at(state, position):
    result = []
    for event in state.events('WEATHER_OBSERVATION'):
        row = state.ref.weather[event['station_id']]
        if state.fresh(event, 3 * float(row['sampling_period_sec'])):
            distance = math.dist(position, (float(row['x_m']), float(row['y_m'])))
            if distance <= float(row['coverage_radius_m']): result.append((distance, event))
    return sorted(result, key=lambda pair: (pair[0], pair[1]['station_id']))


def inactive(state, vid, event):
    count = 0
    if event and state.fresh(event, 15):
        hub = state.ref.hubs[state.ref.vehicles[vid]['destination_hub_id']]
        try:
            pos = state.ref.position(event['segment_id'], event['offset_m'])
            if math.dist(pos, (float(hub['x_m']), float(hub['y_m']))) <= 50 and event['speed_kmh'] <= 1:
                count = state.inactivity[vid] + 1
        except ValueError:
            pass
    state.inactivity[vid] = count
    return count >= 3


def basic_allowed(state, vid, event, estimates):
    if not event or not state.fresh(event, 15): return False
    if not state.ref.compatible(vid, event['segment_id']) or estimates[event['segment_id']]['state'] not in ('OPEN', 'CONGESTED', 'PARTIAL_BLOCK'): return False
    profile = state.ref.profiles[state.ref.vehicles[vid]['odd_profile_id']]
    if event['gnss_quality'] < profile['min_gnss_quality'] or event['map_age_min'] > profile['max_map_age_min']: return False
    try: weather = weather_at(state, state.ref.position(event['segment_id'], event['offset_m']))
    except ValueError: return False
    if not weather: return False
    if any(e['visibility_m'] < profile['min_visibility_m'] or e['rain_level'] > profile['max_rain_level'] or e['wind_mps'] > profile['max_crosswind_mps'] for _, e in weather): return False
    if profile['v2x_required']:
        if not any(state.fresh(e, freshness(state.ref, e)) and e['signature_valid'] for e in state.events('V2X_MESSAGE', event['segment_id'])): return False
    return True


def policy(state, decision):
    estimates = {r['segment_id']: r for r in decision['state_estimates']}
    for action in decision['vehicle_actions']:
        vid = action['vehicle_id']
        event = telemetry(state, vid)
        if inactive(state, vid, event):
            action.update(motion_action='NO_ACTION', rationale_codes=['AT_HUB_OR_INACTIVE'], confidence=0.7)
        elif basic_allowed(state, vid, event, estimates):
            action.update(motion_action='CONTINUE', rationale_codes=list(estimates[event['segment_id']]['rationale_codes']), confidence=0.7)
        else:
            action.update(motion_action='HOLD', rationale_codes=['LOW_CONFIDENCE'], confidence=0.35)
