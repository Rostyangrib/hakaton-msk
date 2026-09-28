import csv
import json
import math
from pathlib import Path


def table(path, key):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for name, value in row.items():
            try:
                row[name] = float(value) if "." in value else int(value)
            except (ValueError, TypeError):
                pass
    return {row[key]: row for row in rows}


class Reference:
    def __init__(self, directory):
        root = Path(directory)
        self.nodes = table(root / "01_network_nodes.csv", "node_id")
        self.segments = table(root / "02_network_segments.csv", "segment_id")
        self.hubs = table(root / "04_hubs.csv", "hub_id")
        self.stops = table(root / "05_safe_stops.csv", "safe_stop_id")
        self.rsus = table(root / "06_rsu_catalog.csv", "rsu_id")
        self.weather = table(root / "08_weather_stations.csv", "station_id")
        self.vehicles = table(root / "10_vehicles.csv", "vehicle_id")
        self.orders = table(root / "11_transport_orders.csv", "vehicle_id")
        self.sources = table(root / "13_source_registry.csv", "source_id")
        self.profiles = json.loads((root / "09_odd_profiles.json").read_text())
        self.remote_limit = json.loads((root / "12_remote_support_pool.json").read_text())["max_parallel_sessions"]
        self.outgoing = {}
        self.groups = {}
        self.segment_rsu = {}
        self.nearest_weather = {}
        for rsu, row in self.rsus.items():
            for segment in row["covered_segments"].split("|"):
                self.segment_rsu[segment] = rsu
        for key, row in self.segments.items():
            self.outgoing.setdefault(row["from_node"], []).append(key)
            self.groups.setdefault(row["road_group"], []).append(key)
            a, b = self.nodes[row["from_node"]], self.nodes[row["to_node"]]
            x, y = (a["x_m"] + b["x_m"]) / 2, (a["y_m"] + b["y_m"]) / 2
            self.nearest_weather[key] = sorted(self.weather, key=lambda w: math.hypot(self.weather[w]["x_m"] - x, self.weather[w]["y_m"] - y))
