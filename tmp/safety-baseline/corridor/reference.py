import csv
import json
import math
from collections import defaultdict
from pathlib import Path


def load_csv(path, key):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        records = list(csv.DictReader(stream))
    result = {r[key]: r for r in records}
    if len(result) != len(records):
        raise ValueError(f'Duplicate identifiers: {path}')
    return result


def positive(row, *fields):
    for field in fields:
        value = float(row[field])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'Invalid {field}: {row}')


class Reference:
    def __init__(self, directory):
        directory = Path(directory)
        specs = {
            'nodes': ('01_network_nodes.csv', 'node_id'),
            'segments': ('02_network_segments.csv', 'segment_id'),
            'hubs': ('04_hubs.csv', 'hub_id'),
            'stops': ('05_safe_stops.csv', 'safe_stop_id'),
            'rsus': ('06_rsu_catalog.csv', 'rsu_id'),
            'sensors': ('07_sensor_catalog.csv', 'source_id'),
            'weather': ('08_weather_stations.csv', 'station_id'),
            'vehicles': ('10_vehicles.csv', 'vehicle_id'),
            'orders': ('11_transport_orders.csv', 'order_id'),
            'sources': ('13_source_registry.csv', 'source_id'),
        }
        for name, (filename, key) in specs.items():
            setattr(self, name, load_csv(directory / filename, key))
        self.profiles = json.loads((directory / '09_odd_profiles.json').read_text(encoding='utf-8'))
        self.support = json.loads((directory / '12_remote_support_pool.json').read_text(encoding='utf-8'))
        self.geojson = json.loads((directory / '03_network.geojson').read_text(encoding='utf-8'))
        self.outgoing = defaultdict(list)
        self.validate()
        for sid, segment in sorted(self.segments.items()):
            self.outgoing[segment['from_node']].append(sid)
        self.rsu_segments = {sid: set(row['covered_segments'].split('|')) for sid, row in self.rsus.items()}

    def validate(self):
        for row in self.nodes.values():
            if not all(math.isfinite(float(row[f])) for f in ('x_m', 'y_m')):
                raise ValueError('Invalid coordinates')
        for row in self.segments.values():
            if row['from_node'] not in self.nodes or row['to_node'] not in self.nodes:
                raise ValueError('Unknown edge endpoint')
            positive(row, 'length_m', 'lanes', 'speed_limit_kmh', 'weight_limit_t', 'capacity_vph')
            if row['av_allowed'] not in ('0', '1') or row['structure'] not in ('open', 'bridge', 'tunnel'):
                raise ValueError('Invalid edge restriction')
        for row in self.hubs.values():
            if row['node_id'] not in self.nodes:
                raise ValueError('Unknown hub node')
            positive(row, 'capacity_vehicles', 'service_rate_vph')
        for row in self.stops.values():
            if row['segment_id'] not in self.segments:
                raise ValueError('Unknown stop segment')
            positive(row, 'capacity_vehicles', 'max_vehicle_mass_t')
        for row in self.vehicles.values():
            if row['odd_profile_id'] not in self.profiles or any(row[f] not in self.hubs for f in ('origin_hub_id', 'destination_hub_id')):
                raise ValueError('Invalid vehicle reference')
            positive(row, 'gross_mass_t', 'length_m', 'nominal_max_speed_kmh')
            if not 1 <= int(row['cargo_priority']) <= 5:
                raise ValueError('Invalid cargo priority')
        for row in self.orders.values():
            if row['vehicle_id'] not in self.vehicles or any(row[f] not in self.hubs for f in ('origin_hub_id', 'destination_hub_id')):
                raise ValueError('Invalid order reference')
        for row in self.sensors.values():
            if row['segment_id'] not in self.segments or row['source_id'] not in self.sources:
                raise ValueError('Invalid sensor reference')
            positive(row, 'sampling_period_sec')
        for row in self.rsus.values():
            if row['rsu_id'] not in self.sources or any(s not in self.segments for s in row['covered_segments'].split('|')):
                raise ValueError('Invalid RSU reference')
        for row in self.weather.values():
            if row['node_id'] not in self.nodes or row['station_id'] not in self.sources:
                raise ValueError('Invalid weather reference')
            positive(row, 'sampling_period_sec', 'coverage_radius_m')
        for row in self.sources.values():
            positive(row, 'expected_period_sec')

    def compatible(self, vehicle_id, segment_id):
        vehicle, segment = self.vehicles[vehicle_id], self.segments[segment_id]
        return (segment['av_allowed'] == '1'
                and float(vehicle['gross_mass_t']) <= float(segment['weight_limit_t'])
                and segment['structure'] in self.profiles[vehicle['odd_profile_id']]['allowed_structures'])

    def position(self, segment_id, offset_m):
        segment = self.segments[segment_id]
        length = float(segment['length_m'])
        if not 0 <= offset_m <= length:
            raise ValueError('Offset outside segment')
        start, end = self.nodes[segment['from_node']], self.nodes[segment['to_node']]
        ratio = offset_m / length
        return tuple(float(start[f]) + ratio * (float(end[f]) - float(start[f])) for f in ('x_m', 'y_m'))
