from collections import defaultdict, deque
from datetime import datetime


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class State:
    def __init__(self, reference):
        self.ref = reference
        self.reset(None)

    def reset(self, scenario_id):
        self.scenario_id = scenario_id
        self.current = {}
        self.measurements = {}
        self.history = defaultdict(list)
        self.seen = set()
        self.delivery = defaultdict(list)
        self.recommendations = deque()
        self.final = False
        self.now = 0
        self.diagnostics = []
        self.inactivity = defaultdict(int)

    def key(self, event):
        kind = event['event_type']
        field = {'VEHICLE_TELEMETRY': 'vehicle_id', 'ROAD_OBSERVATION': 'segment_id',
                 'DIGITAL_TWIN_SEGMENT': 'segment_id', 'V2X_MESSAGE': 'segment_id',
                 'WEATHER_OBSERVATION': 'station_id', 'INFRASTRUCTURE_HEALTH': 'component_id',
                 'HUB_STATUS': 'hub_id'}.get(kind)
        if field is None:
            return None
        entity = event.get(field)
        registry = {'vehicle_id': self.ref.vehicles, 'segment_id': self.ref.segments,
                    'station_id': self.ref.weather, 'component_id': self.ref.sources,
                    'hub_id': self.ref.hubs}[field]
        if entity not in registry:
            return None
        if kind not in ('VEHICLE_TELEMETRY', 'HUB_STATUS') and event['source_id'] not in self.ref.sources:
            return None
        return kind, entity, event['source_id']

    def ingest(self, packet):
        if self.scenario_id != packet['scenario_id'] or self.final:
            self.reset(packet['scenario_id'])
        now = timestamp(packet['decision_time'])
        if now < self.now:
            raise ValueError('Decision time moved backwards')
        self.now = now
        self.diagnostics = []
        for event in packet['events']:
            if event['scenario_id'] != self.scenario_id:
                self.diagnostics.append('foreign_scenario')
                continue
            if timestamp(event['received_time']) > now:
                self.diagnostics.append('future_delivery')
                continue
            self.delivery[event['source_id']].append((now, event['event_id'], event['delivery_no']))
            if event['event_id'] in self.seen:
                self.diagnostics.append('duplicate')
                continue
            self.seen.add(event['event_id'])
            key = self.key(event)
            if key is None:
                self.diagnostics.append('unknown_entity_or_source')
                continue
            measured = timestamp(event['event_time'])
            self.history[key].append(event)
            if measured > now:
                self.diagnostics.append('future_measurement')
                continue
            rank = measured, event['event_id']
            previous = self.current.get(key)
            if previous is None or rank > (timestamp(previous['event_time']), previous['event_id']):
                self.current[key] = event
            else:
                self.diagnostics.append('late')
            for field, value in event.items():
                mkey = key + (field,)
                old = self.measurements.get(mkey)
                if old is None or rank > old[0]:
                    self.measurements[mkey] = rank, value
        for key in list(self.history):
            self.history[key] = sorted((e for e in self.history[key] if timestamp(e['event_time']) >= now-120),
                                       key=lambda e: (timestamp(e['event_time']), e['event_id']))
            if not self.history[key]:
                del self.history[key]
        for source in list(self.delivery):
            self.delivery[source] = [d for d in self.delivery[source] if d[0] >= now-120]
        while self.recommendations and self.recommendations[0][0] < now-120:
            self.recommendations.popleft()
        self.final = packet['is_final']

    def events(self, kind=None, entity=None):
        return [event for (k, e, _), event in sorted(self.current.items())
                if (kind is None or kind == k) and (entity is None or entity == e)]

    def fresh(self, event, seconds):
        return 0 <= self.now - timestamp(event['event_time']) <= seconds

    def remember(self, decision):
        self.recommendations.append((self.now, decision))
