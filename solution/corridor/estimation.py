from collections import defaultdict
from statistics import median, pstdev

from .observations import number


GROUPS = ("MAIN", "NORTH", "SOUTH", "CONNECTOR", "ACCESS")
FAULTS = ("OUTAGE", "DELAY", "TIME_SKEW", "FREEZE", "DRIFT", "PACKET_LOSS", "BIAS", "BYZANTINE", "FALSE_LANE_CLOSURE", "STALE")


def spread(values):
    return pstdev(values) if len(values) > 1 else 0.0


def median_or(values, default):
    return median(values) if values else default


class Estimator:
    def __init__(self, reference, observations, model):
        self.ref = reference
        self.obs = observations
        self.model = model
        self.source_features = {}
        self.segment_features = {}
        self.vehicle_features = {}
        self.environment = {}
        self.sources = {}
        self.states = {}
        self.assessments = {}
        self.telemetry = {}
        self.wind_exceeded = set()

    def estimate_sources(self):
        by_source = defaultdict(list)
        for key, event in self.obs.latest.items():
            if key[0] not in {"INFRASTRUCTURE_HEALTH", "VEHICLE_TELEMETRY", "HUB_STATUS"}:
                by_source[key[1]].append(event)
        supporting_open = defaultdict(dict)
        for event in self.obs.events("ROAD_OBSERVATION", 15):
            sid = event.get("segment_id")
            segment = self.ref.segments.get(sid, {})
            if event.get("lane_status") in {None, "OPEN"} and number(event, "lane_count_open_estimate") >= segment.get("lanes", 100):
                supporting_open[sid][event["source_id"]] = event["_time"]
        for event in self.obs.events("DIGITAL_TWIN_SEGMENT", 35):
            if event.get("closure_state") == "OPEN" and number(event, "_snapshot_age") <= 35:
                supporting_open[event["segment_id"]][event["source_id"]] = event["_time"]
        result = {}
        for source, row in self.ref.sources.items():
            events = by_source[source]
            current = max(events, key=lambda e: e["_time"], default={})
            period = row["expected_period_sec"]
            age = self.obs.now - current.get("_time", self.obs.start)
            health = self.obs.get("INFRASTRUCTURE_HEALTH", source, max_age=45) or {}
            kind = current.get("event_type", "")
            history = list(self.obs.history.get((kind, source), []))
            recent = [e for e in history if self.obs.now - e["_time"] <= 60]
            segment = self.ref.segments.get(current.get("segment_id"), {})
            speed_limit = segment.get("speed_limit_kmh", 100)
            signature = ("speed_kmh", "flow_vph", "occupancy_pct", "lane_count_open_estimate") if kind == "ROAD_OBSERVATION" else ("visibility_m", "rain_level", "wind_mps")
            frozen = 0.0
            if kind in {"ROAD_OBSERVATION", "WEATHER_OBSERVATION"} and recent:
                same = tuple(current.get(k) for k in signature)
                for event in reversed(recent):
                    if tuple(event.get(k) for k in signature) != same:
                        break
                    frozen = current["_time"] - event["_time"]
            speeds = [number(e, "speed_kmh") for e in recent]
            delay = number(current, "_delay")
            stale = max((number(e, "_snapshot_age") for e in events if self.obs.now - e["_time"] < 90), default=0)
            skew = max(abs(number(health, "time_sync_offset_ms")), abs(number(current, "source_clock_offset_ms")), max(0, number(current, "_clock")) * 1000)
            types = ("ROAD_DETECTOR", "VIDEO_ANALYTICS", "RSU", "WEATHER", "DIGITAL_TWIN")
            features = [float(row["source_type"] == t) for t in types]
            features += [min(age, 300), period, delay, skew, frozen, stale, number(health, "network_packet_loss_pct"), number(health, "last_heartbeat_age_sec"), float(health.get("status") == "DEGRADED"), float(health.get("status") == "NO_HEARTBEAT"), number(current, "speed_kmh") / speed_limit, number(current, "occupancy_pct"), number(current, "flow_vph") / max(1, segment.get("capacity_vph", 1)), number(current, "queue_estimate_m"), spread(speeds), number(current, "visibility_m", 1000), number(current, "rain_level"), len(recent), number(current, "self_reported_confidence", 0.9), float(current.get("signature_valid", True))]
            self.source_features[source] = features
            faults = []
            if (health.get("status") == "NO_HEARTBEAT" and age > 2 * period) or age > max(4 * period, 45):
                faults.append("OUTAGE")
            if skew > 1000:
                faults.append("TIME_SKEW")
            if frozen >= max(15, 2 * period):
                faults.append("FREEZE")
            if stale > 45:
                faults.append("STALE")
            if health.get("status") == "DEGRADED" and row["source_type"] == "RSU" and number(health, "network_packet_loss_pct") >= 15 and delay < 10:
                faults.append("PACKET_LOSS")
            if delay >= 6 and "PACKET_LOSS" not in faults and "TIME_SKEW" not in faults:
                faults.append("DELAY")
            if kind == "V2X_MESSAGE" and current.get("lane_status") in {"CLOSED", "PARTIAL_BLOCK"} and sum(s != source and t >= current["_time"] for s, t in supporting_open[current["segment_id"]].items()) >= 2:
                faults.append("FALSE_LANE_CLOSURE")
            if current.get("signature_valid") is False:
                faults.append("BYZANTINE")
            learned, certainty = self.model.predict("source", features, "OK")
            if learned != "OK" and certainty >= 0.65 and learned not in faults:
                faults.append(learned)
            if not self.model.data and number(current, "speed_kmh") > speed_limit + 8:
                faults.append("DRIFT")
            status = "FAILED" if "OUTAGE" in faults else "DEGRADED" if faults else "OK" if current else "UNKNOWN"
            if health.get("status") == "DEGRADED" and not faults:
                status = "DEGRADED"
            trust = 0.04 if status == "FAILED" else 0.35 if status == "DEGRADED" else 0.96 if status == "OK" else 0.2
            confidence = 0.96 if status in {"OK", "FAILED"} else 0.86 if faults else 0.65
            result[source] = {"source_id": source, "status": status, "trust_score": trust, "confidence": confidence, "fault_types": sorted(set(faults))}
        self.sources = result
        return result

    def estimate_segments(self):
        road = defaultdict(list)
        v2x = defaultdict(list)
        twin = {}
        group_ratios = defaultdict(list)
        for event in self.obs.events("ROAD_OBSERVATION", 25):
            source = self.sources.get(event["source_id"], {})
            if event["segment_id"] not in self.ref.segments:
                continue
            if source.get("trust_score", 0) >= 0.3 and "FREEZE" not in source.get("fault_types", []):
                road[event["segment_id"]].append(event)
                row = self.ref.segments[event["segment_id"]]
                group_ratios[row["road_group"]].append(number(event, "flow_vph") / row["base_flow_vph"])
        for event in self.obs.events("V2X_MESSAGE", 35):
            if event.get("signature_valid", True) and not set(self.sources.get(event["source_id"], {}).get("fault_types", [])) & {"OUTAGE", "FALSE_LANE_CLOSURE", "BYZANTINE"}:
                v2x[event["segment_id"]].append(event)
        for event in self.obs.events("DIGITAL_TWIN_SEGMENT", 95):
            if number(event, "_snapshot_age") + self.obs.now - event["_time"] <= 95:
                twin[event["segment_id"]] = event
        self.telemetry = {e["vehicle_id"]: e for e in self.obs.events("VEHICLE_TELEMETRY", 15)}
        vehicles = defaultdict(list)
        for event in self.telemetry.values():
            if event.get("autonomy_state") == "AUTO":
                vehicles[event["segment_id"]].append(event)
        states = {}
        for sid, row in self.ref.segments.items():
            roads, messages, dt = road[sid], v2x[sid], twin.get(sid, {})
            lanes = row["lanes"]
            road_lanes = median_or([number(e, "lane_count_open_estimate", lanes) for e in roads], lanes)
            dt_lanes = number(dt, "lane_count_open", lanes)
            dt_age = number(dt, "_snapshot_age", 1000) + max(0, self.obs.now - dt.get("_time", self.obs.now))
            direct_states = [e.get("lane_status") for e in roads + messages]
            road_speed = median_or([number(e, "speed_kmh") / row["speed_limit_kmh"] for e in roads], 1)
            v2x_speed = median_or([number(e, "advisory_speed_kmh") / row["speed_limit_kmh"] for e in messages], 1)
            occupancy = median_or([number(e, "occupancy_pct") for e in roads], 0)
            queue = max([number(e, "queue_estimate_m") for e in roads + messages] or [0])
            flow = median_or([number(e, "flow_vph") / row["capacity_vph"] for e in roads], 0)
            features = [float(row["road_group"] == g) for g in GROUPS]
            features += [len(roads), len(messages), float(bool(dt)), min(dt_age, 1000), road_lanes / lanes, dt_lanes / lanes, float("CLOSED" in direct_states), float("PARTIAL_BLOCK" in direct_states), float(dt.get("closure_state") == "CLOSED"), float(dt.get("closure_state") == "PARTIAL_BLOCK"), road_speed, v2x_speed, occupancy, queue, flow, median_or(group_ratios[row["road_group"]], 1), row["base_flow_vph"] / row["capacity_vph"], median_or([number(e, "speed_kmh") / row["speed_limit_kmh"] for e in vehicles[sid]], 1), len(vehicles[sid])]
            self.segment_features[sid] = features
            state, confidence = self.model.predict("segment", features, "OPEN")
            if not roads and not messages and not dt:
                state, confidence = "UNKNOWN", 0.35
            elif "CLOSED" in direct_states or (dt.get("closure_state") == "CLOSED" and dt_age <= 45):
                state, confidence = "CLOSED", 0.96
            elif "PARTIAL_BLOCK" in direct_states or (dt.get("closure_state") == "PARTIAL_BLOCK" and dt_age <= 45):
                state, confidence = "PARTIAL_BLOCK", 0.96
            elif roads and road_lanes < lanes:
                state, confidence = ("CLOSED" if road_lanes == 0 else "PARTIAL_BLOCK"), 0.94
            elif not self.model.data and (queue > 180 or road_speed < 0.55 or v2x_speed < 0.55):
                state, confidence = "CONGESTED", 0.85
            estimated_lanes = 0 if state == "CLOSED" else max(1, round(min(road_lanes, dt_lanes if dt_age <= 45 else lanes)))
            states[sid] = {"segment_id": sid, "state": state, "lanes_open_estimate": estimated_lanes, "confidence": round(confidence, 5), "rationale_codes": ["STATE_" + state]}
        self.states = states
        return states

    def vehicle_feature(self, vid):
        vehicle = self.ref.vehicles[vid]
        event = self.telemetry.get(vid, {})
        row = self.ref.segments.get(event.get("segment_id"), {})
        profile = self.ref.profiles[vehicle["odd_profile_id"]]
        sid = event.get("segment_id")
        weather = [self.obs.get("WEATHER_OBSERVATION", w, max_age=45) for w in self.ref.nearest_weather.get(sid, [])]
        weather = [e for e in weather if e and self.sources.get(e["source_id"], {}).get("status") != "FAILED"]
        nearest = weather[0] if weather else {}
        visibility = [number(e, "visibility_m", 1000) for e in weather]
        rain = [number(e, "rain_level") for e in weather]
        wind = [number(e, "wind_mps") for e in weather]
        rsu = self.sources.get(self.ref.segment_rsu.get(sid), {})
        features = [float(row.get("road_group") == g) for g in GROUPS]
        features += [profile["min_visibility_m"], profile["max_rain_level"], profile["min_gnss_quality"], profile["max_map_age_min"], float(profile["v2x_required"]), number(event, "gnss_quality", 1), number(event, "localization_confidence", 1), number(event, "perception_health", 1), number(event, "communication_latency_ms"), number(event, "packet_loss_pct_10s"), number(event, "map_age_min"), float(event.get("autonomy_state") == "DEGRADED"), float(event.get("autonomy_state") == "REMOTE_REQUESTED"), number(event, "speed_kmh"), float(rsu.get("status") == "FAILED"), float(rsu.get("status") == "DEGRADED"), min(visibility or [1000]), median_or(visibility, 1000), max(visibility or [1000]), min(rain or [0]), median_or(rain, 0), max(rain or [0]), number(nearest, "visibility_m", 1000), number(nearest, "rain_level"), len(weather)]
        self.environment[vid] = {"visibility": number(nearest, "visibility_m", 1000), "rain": number(nearest, "rain_level"), "wind": max(wind or [0]), "known": bool(weather)}
        return features

    def is_inactive(self, vid):
        event = self.telemetry.get(vid) or self.obs.get("VEHICLE_TELEMETRY", vid, max_age=float("inf"))
        if not event:
            return False
        if "active" in event:
            return not event["active"]
        row = self.ref.segments.get(event.get("segment_id"), {})
        hub = self.ref.hubs.get(event.get("destination_hub_id", self.ref.vehicles[vid]["destination_hub_id"]), {})
        if self.obs.now - event["_time"] > 15:
            return False
        return row.get("to_node") == hub.get("node_id") and number(event, "offset_m") >= row.get("length_m", float("inf")) - 2 and number(event, "speed_kmh") < 1

    def estimate_vehicles(self):
        result = {}
        self.wind_exceeded = set()
        for vid, vehicle in self.ref.vehicles.items():
            event = self.telemetry.get(vid)
            features = self.vehicle_feature(vid)
            self.vehicle_features[vid] = features
            if not event or self.is_inactive(vid):
                result[vid] = {"vehicle_id": vid, "odd_status": "UNKNOWN", "violation_codes": [], "confidence": 0.99 if event else 0.4}
                continue
            profile = self.ref.profiles[vehicle["odd_profile_id"]]
            row = self.ref.segments.get(event.get("segment_id"), {})
            if not row:
                result[vid] = {"vehicle_id": vid, "odd_status": "UNKNOWN", "violation_codes": [], "confidence": 0.4}
                continue
            environment = self.environment[vid]
            codes = []
            for code, default in [("VISIBILITY", environment["visibility"] < profile["min_visibility_m"]), ("RAIN", environment["rain"] > profile["max_rain_level"])]:
                prediction, _ = self.model.predict(code.lower(), features, int(default))
                if prediction:
                    codes.append(code)
            if number(event, "gnss_quality", 0) < profile["min_gnss_quality"]:
                codes.append("GNSS")
            if profile["v2x_required"] and (number(event, "communication_latency_ms") > 500 or number(event, "packet_loss_pct_10s") > 20 or bool(set(self.sources.get(self.ref.segment_rsu.get(event["segment_id"]), {}).get("fault_types", [])) & {"OUTAGE", "PACKET_LOSS", "DELAY", "TIME_SKEW"})):
                codes.append("V2X")
            if row.get("structure") not in profile["allowed_structures"]:
                codes.append("STRUCTURE")
            if vehicle["gross_mass_t"] > row.get("weight_limit_t", 0):
                codes.append("WEIGHT_LIMIT")
            if number(event, "map_age_min") + (self.obs.now - event["_time"]) / 60 > profile["max_map_age_min"] + 0.04:
                codes.append("MAP_AGE")
            if environment["wind"] > profile["max_crosswind_mps"]:
                self.wind_exceeded.add(vid)
            unknown = not row or not environment["known"]
            status = "VIOLATED" if codes or vid in self.wind_exceeded else "UNKNOWN" if unknown else "COMPLIANT"
            confidence = 0.97 if status == "COMPLIANT" else 0.91 if status == "VIOLATED" else 0.45
            result[vid] = {"vehicle_id": vid, "odd_status": status, "violation_codes": sorted(codes), "confidence": confidence}
        self.assessments = result
        return result
