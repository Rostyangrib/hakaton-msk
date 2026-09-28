import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker


class Contract:
    def __init__(self, reference, directory=None):
        self.ref = reference
        directory = Path(directory) if directory else Path(__file__).resolve().parent.parent / 'contract'
        self.schema = json.loads((directory / '03_decision.schema.json').read_text(encoding='utf-8'))
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def validate(self, decision):
        self.validator.validate(decision)
        for field, key, expected in (
            ('state_estimates', 'segment_id', self.ref.segments),
            ('source_assessments', 'source_id', self.ref.sources),
            ('vehicle_assessments', 'vehicle_id', self.ref.vehicles),
            ('vehicle_actions', 'vehicle_id', self.ref.vehicles),
        ):
            values = [r[key] for r in decision[field]]
            if len(set(values)) != len(values) or set(values) != set(expected):
                raise ValueError(f'Invalid identifiers: {field}')
        for action in decision['vehicle_actions']:
            kind = action['motion_action']
            if kind == 'LIMIT_SPEED' and not (action.get('speed_limit_kmh') or 0) > 0:
                raise ValueError('LIMIT_SPEED requires positive speed')
            if kind == 'SAFE_STOP' and action.get('safe_stop_id') not in self.ref.stops:
                raise ValueError('SAFE_STOP requires existing stop')
            if kind == 'REROUTE' and not action.get('route_segment_ids'):
                raise ValueError('REROUTE requires nonempty route')


def skeleton(packet, ref):
    return {
        'scenario_id': packet['scenario_id'], 'packet_id': packet['packet_id'], 'decision_time': packet['decision_time'],
        'state_estimates': [dict(segment_id=s, state='UNKNOWN', confidence=0.35, rationale_codes=['STATE_UNKNOWN', 'NO_FRESH_DATA']) for s in sorted(ref.segments)],
        'source_assessments': [dict(source_id=s, status='UNKNOWN', trust_score=0.5, confidence=0.35, fault_types=[]) for s in sorted(ref.sources)],
        'vehicle_assessments': [dict(vehicle_id=v, odd_status='UNKNOWN', violation_codes=[], confidence=0.35) for v in sorted(ref.vehicles)],
        'vehicle_actions': [dict(vehicle_id=v, motion_action='HOLD', remote_support_required=False, confidence=0.35, rationale_codes=['NO_FRESH_DATA', 'LOW_CONFIDENCE']) for v in sorted(ref.vehicles)],
    }
