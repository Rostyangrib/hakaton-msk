from .basic import telemetry, freshness


def check(state, fusion, vid, event=None, segment_id=None, future=False):
    event = event if event is not None else telemetry(state,vid)
    vehicle = state.ref.vehicles[vid]
    profile = state.ref.profiles[vehicle['odd_profile_id']]
    codes, unknown, certainty = [], False, []
    if event is None or not state.fresh(event,15):
        return dict(vehicle_id=vid,odd_status='UNKNOWN',violation_codes=[],confidence=0.35)
    sid = segment_id or event['segment_id']
    segment = state.ref.segments[sid]
    if segment['structure'] not in profile['allowed_structures']: codes.append('STRUCTURE')
    if float(vehicle['gross_mass_t']) > float(segment['weight_limit_t']): codes.append('WEIGHT_LIMIT')
    for field, threshold, code, minimum in (
        ('gnss_quality',profile['min_gnss_quality'],'GNSS',True),
        ('map_age_min',profile['max_map_age_min'],'MAP_AGE',False),
    ):
        value = event.get(field)
        if value is None:
            unknown = True
            continue
        if (value < threshold if minimum else value > threshold): codes.append(code)
        certainty.append(0.5+0.5*min(1,abs(value-threshold)/max(threshold,0.01)))
    try:
        position = state.ref.position(sid, float(segment['length_m'])/2 if future else event['offset_m'])
        weather = fusion.weather(position)
    except ValueError:
        weather = None
    if weather is None or weather['agreement'] < 0.6:
        unknown = True
    else:
        for field,threshold,code,minimum in (
            ('visibility_m',profile['min_visibility_m'],'VISIBILITY',True),
            ('rain_level',profile['max_rain_level'],'RAIN',False),
        ):
            value = weather[field]
            if (value < threshold if minimum else value > threshold): codes.append(code)
            certainty.append(weather['confidence']*(0.5+0.5*min(1,abs(value-threshold)/max(threshold,1))))
        if weather['wind_mps'] > profile['max_crosswind_mps']:
            unknown = True
            state.diagnostics.append('crosswind_direction_unknown:'+vid)
    if profile['v2x_required']:
        covering = [r for r,covered in state.ref.rsu_segments.items() if sid in covered]
        if not covering:
            codes.append('V2X')
        else:
            valid = [e for e in state.events('V2X_MESSAGE',sid) if e['source_id'] in covering and fusion.weight(e) > 0]
            if valid:
                certainty.append(min(1,sum(fusion.weight(e) for e in valid)))
            elif all(fusion.sources[r]['status'] == 'FAILED' for r in covering):
                codes.append('V2X')
            else:
                unknown = True
    status = 'VIOLATED' if codes else 'UNKNOWN' if unknown else 'COMPLIANT'
    conf = min(certainty,default=0.35) if status != 'UNKNOWN' else 0.35
    return dict(vehicle_id=vid,odd_status=status,violation_codes=sorted(set(codes)),confidence=round(conf,6))


def assessments(state,fusion,decision):
    output = []
    actions = {a['vehicle_id']:a for a in decision['vehicle_actions']}
    for vid in sorted(state.ref.vehicles):
        if actions[vid]['motion_action'] == 'NO_ACTION':
            output.append(dict(vehicle_id=vid,odd_status='UNKNOWN',violation_codes=[],confidence=0.7))
        else:
            estimate = check(state,fusion,vid)
            output.append(estimate)
            if estimate['odd_status'] != 'COMPLIANT':
                actions[vid].update(motion_action='HOLD',confidence=estimate['confidence'],
                                    rationale_codes=['ODD_'+c for c in estimate['violation_codes']] or ['LOW_CONFIDENCE'])
    return output
