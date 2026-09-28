import heapq
from collections import Counter

from .observations import number


class Planner:
    def __init__(self, reference):
        self.ref = reference
        self.previous_remote = set()
        self.previous_routes = {}
        self.previous_stops = {}
        self.route_exclusions = {}
        self.route_reasons = {}

    def path(self, start, destination, vehicle, states, reservations=None, unrestricted=False, emergency=False, geometric=False, banned_nodes=()):
        if start == destination:
            return 0.0, []
        profile = self.ref.profiles[vehicle["odd_profile_id"]]
        queue = [(0.0, start, ())]
        distances = {start: 0.0}
        while queue:
            cost, node, route = heapq.heappop(queue)
            if cost > distances[node]:
                continue
            if node == destination:
                return cost, list(route)
            for sid in self.ref.outgoing.get(node, []):
                segment = self.ref.segments[sid]
                state = states[sid]["state"]
                if segment["to_node"] in banned_nodes:
                    continue
                if not geometric and (not segment["av_allowed"] or segment["weight_limit_t"] < vehicle["gross_mass_t"] or segment["structure"] not in profile["allowed_structures"]):
                    continue
                if not unrestricted and not geometric and (state in {"CLOSED", "UNKNOWN"} or states[sid]["confidence"] < 0.6):
                    continue
                if not unrestricted and not geometric and not emergency and sid in self.route_exclusions.get(vehicle["odd_profile_id"], set()):
                    continue
                factor = 1 if unrestricted or geometric else {"OPEN": 1, "CONGESTED": 3.0, "PARTIAL_BLOCK": 2.3}.get(state, 1)
                load = (reservations or {}).get(sid, 0) * 720 / max(1, segment["capacity_vph"])
                travel = segment["length_m"] * 3.6 / min(segment["speed_limit_kmh"], vehicle["nominal_max_speed_kmh"])
                candidate = cost + travel * (factor + 0.2 * load * load)
                end = segment["to_node"]
                if candidate < distances.get(end, float("inf")):
                    distances[end] = candidate
                    heapq.heappush(queue, (candidate, end, route + (sid,)))
        return float("inf"), []

    def route_issues(self, route, vehicle, states):
        issues = set()
        profile = self.ref.profiles[vehicle["odd_profile_id"]]
        for sid in route:
            road = self.ref.segments[sid]
            state = states[sid]
            if state["state"] == "CLOSED":
                issues.add("ROUTE_CLOSED")
            elif state["state"] == "UNKNOWN":
                issues.add("STATE_UNKNOWN")
            elif state["state"] == "CONGESTED":
                issues.add("ROUTE_CONGESTED")
            elif state["state"] == "PARTIAL_BLOCK":
                issues.add("STATE_PARTIAL_BLOCK")
            if state["confidence"] < 0.6:
                issues.add("LOW_CONFIDENCE")
            if not road["av_allowed"]:
                issues.add("ROUTE_AV_FORBIDDEN")
            if road["weight_limit_t"] < vehicle["gross_mass_t"]:
                issues.add("ROUTE_WEIGHT_LIMIT")
            if road["structure"] not in profile["allowed_structures"]:
                issues.add("ODD_STRUCTURE")
            issues.update(self.route_reasons.get((vehicle["odd_profile_id"], sid), []))
        return issues

    def hypotheses(self, current, destination, vehicle, states):
        start = current["to_node"]
        if start == destination:
            return []
        hypotheses = []
        for sid in self.ref.outgoing.get(start, []):
            segment = self.ref.segments[sid]
            if segment["to_node"] == current["from_node"]:
                continue
            cost, tail = self.path(segment["to_node"], destination, vehicle, states, geometric=True, banned_nodes={start})
            if cost != float("inf"):
                hypotheses.append([sid] + tail)
        return hypotheses

    def valid_route(self, route, start, destination, vehicle, states):
        if not route or len(route) > 86:
            return False
        node = start
        visited = {start}
        for sid in route:
            segment = self.ref.segments.get(sid)
            if not segment or segment["from_node"] != node or segment["to_node"] in visited:
                return False
            issues = self.route_issues([sid], vehicle, states) - {"ROUTE_CONGESTED", "STATE_PARTIAL_BLOCK"}
            if issues:
                return False
            node = segment["to_node"]
            visited.add(node)
        return node == destination

    def safe_stop(self, vid, event, vehicle, states, occupied):
        current = self.ref.segments[event["segment_id"]]
        candidates = []
        for stop_id, stop in self.ref.stops.items():
            sid = stop["segment_id"]
            road = self.ref.segments[sid]
            if occupied[stop_id] >= stop["capacity_vehicles"] or vehicle["gross_mass_t"] > min(stop["max_vehicle_mass_t"], road["weight_limit_t"]) or states[sid]["state"] in {"CLOSED", "UNKNOWN"} or not road["av_allowed"]:
                continue
            if sid == event["segment_id"]:
                distance = max(0, road["length_m"] - number(event, "offset_m"))
            else:
                cost, route = self.path(current["to_node"], road["from_node"], vehicle, states, emergency=True)
                if cost == float("inf"):
                    continue
                distance = sum(self.ref.segments[s]["length_m"] for s in route) + max(0, current["length_m"] - number(event, "offset_m")) + road["length_m"]
            if road["structure"] not in self.ref.profiles[vehicle["odd_profile_id"]]["allowed_structures"]:
                continue
            candidates.append((distance - (1500 if self.previous_stops.get(vid) == stop_id else 0), stop_id))
        return min(candidates)[1] if candidates else None

    def plan(self, estimator):
        states, assessments = estimator.states, estimator.assessments
        observations = estimator.obs
        self.route_exclusions = {}
        self.route_reasons = {}
        for profile_id, profile in self.ref.profiles.items():
            excluded = set()
            for sid in self.ref.segments:
                station = next((observations.get("WEATHER_OBSERVATION", w, max_age=45) for w in self.ref.nearest_weather[sid] if observations.get("WEATHER_OBSERVATION", w, max_age=45) and estimator.sources[w]["status"] == "OK"), None)
                source = estimator.sources.get(self.ref.segment_rsu.get(sid), {})
                weather_bad = station and (number(station, "visibility_m", 1000) < profile["min_visibility_m"] or number(station, "rain_level") > profile["max_rain_level"] or number(station, "wind_mps") > profile["max_crosswind_mps"])
                communication_bad = profile["v2x_required"] and (source.get("status", "UNKNOWN") != "OK" or bool(set(source.get("fault_types", [])) & {"OUTAGE", "PACKET_LOSS", "DELAY", "TIME_SKEW"}))
                reasons = []
                if station is None:
                    reasons.append("NO_FRESH_DATA")
                elif weather_bad:
                    if number(station, "visibility_m", 1000) < profile["min_visibility_m"]:
                        reasons.append("ODD_VISIBILITY")
                    if number(station, "rain_level") > profile["max_rain_level"]:
                        reasons.append("ODD_RAIN")
                    if number(station, "wind_mps") > profile["max_crosswind_mps"]:
                        reasons.append("LOW_CONFIDENCE")
                if communication_bad:
                    reasons.append("ODD_V2X")
                if reasons:
                    excluded.add(sid)
                    self.route_reasons[profile_id, sid] = reasons
            self.route_exclusions[profile_id] = excluded
        remote_candidates = []
        for vid, assessment in assessments.items():
            if assessment["odd_status"] == "VIOLATED" and not estimator.is_inactive(vid):
                vehicle = self.ref.vehicles[vid]
                need = 0 if set(assessment["violation_codes"]) <= {"V2X"} else 1
                remote_candidates.append((need, vehicle["cargo_priority"], 0 if vid in self.previous_remote else 1, vid))
        remote = {x[-1] for x in sorted(remote_candidates)[:self.ref.remote_limit]}
        occupied = Counter()
        reservations = Counter()
        actions = {}
        next_routes, next_stops = {}, {}
        vehicles = sorted(self.ref.vehicles, key=lambda v: (self.ref.vehicles[v]["cargo_priority"], self.ref.orders.get(v, {}).get("deadline_offset_min", 10000), v))
        for vid in vehicles:
            vehicle = self.ref.vehicles[vid]
            event = estimator.telemetry.get(vid)
            assessment = assessments[vid]
            codes = ["ODD_" + c for c in assessment["violation_codes"]]
            if vid in estimator.wind_exceeded:
                codes.append("LOW_CONFIDENCE")
            action = {"vehicle_id": vid, "motion_action": "CONTINUE", "remote_support_required": vid in remote, "confidence": assessment["confidence"], "rationale_codes": codes}
            if estimator.is_inactive(vid):
                action["motion_action"] = "NO_ACTION"
                action["remote_support_required"] = False
                codes.append("AT_HUB_OR_INACTIVE")
            elif not event:
                action["motion_action"] = "HOLD"
                codes.append("NO_FRESH_DATA")
            elif event.get("segment_id") not in self.ref.segments:
                action["motion_action"] = "HOLD"
                codes.extend(["STATE_UNKNOWN", "LOW_CONFIDENCE"])
            elif states[event["segment_id"]]["state"] in {"CLOSED", "UNKNOWN"}:
                action["motion_action"] = "HOLD"
                codes.append("ROUTE_CLOSED" if states[event["segment_id"]]["state"] == "CLOSED" else "STATE_UNKNOWN")
            elif not self.ref.segments[event["segment_id"]]["av_allowed"]:
                action["motion_action"] = "HOLD"
                codes.append("ROUTE_AV_FORBIDDEN")
            elif assessment["odd_status"] != "COMPLIANT":
                if assessment["odd_status"] == "UNKNOWN":
                    codes.append("LOW_CONFIDENCE")
                if assessment["violation_codes"] == ["V2X"] and vid in remote:
                    action.update(motion_action="LIMIT_SPEED", speed_limit_kmh=min(30, self.ref.segments[event["segment_id"]]["speed_limit_kmh"]))
                else:
                    stop = self.safe_stop(vid, event, vehicle, states, occupied)
                    if stop:
                        occupied[stop] += 1
                        next_stops[vid] = stop
                        action.update(motion_action="SAFE_STOP", safe_stop_id=stop)
                    else:
                        action["motion_action"] = "HOLD"
                        codes.append("SAFE_STOP_CAPACITY")
                    if vid not in remote:
                        codes.append("REMOTE_SUPPORT_CAPACITY")
            else:
                current = self.ref.segments[event["segment_id"]]
                destination_id = event.get("destination_hub_id", vehicle["destination_hub_id"])
                hub_reference = self.ref.hubs.get(destination_id)
                if hub_reference is None:
                    action["motion_action"] = "HOLD"
                    codes.append("LOW_CONFIDENCE")
                    action["rationale_codes"] = sorted(set(codes))
                    actions[vid] = action
                    continue
                destination = hub_reference["node_id"]
                hypotheses = self.hypotheses(current, destination, vehicle, states)
                issues = set().union(*(self.route_issues(h, vehicle, states) for h in hypotheses))
                unresolved = current["to_node"] != destination and not hypotheses
                if unresolved:
                    issues.add("LOW_CONFIDENCE")
                cost, route = self.path(current["to_node"], destination, vehicle, states, reservations)
                old = list(self.previous_routes.get(vid, []))
                while old and self.ref.segments.get(old[0], {}).get("from_node") != current["to_node"]:
                    old = old[1:]
                if self.valid_route(old, current["to_node"], destination, vehicle, states):
                    old_cost = sum(self.ref.segments[s]["length_m"] * 3.6 / min(self.ref.segments[s]["speed_limit_kmh"], vehicle["nominal_max_speed_kmh"]) for s in old)
                    if old_cost <= cost * 1.15:
                        route = old
                hub = observations.get("HUB_STATUS", destination_id, max_age=90) or {}
                if route and issues and self.valid_route(route, current["to_node"], destination, vehicle, states):
                    action.update(motion_action="REROUTE", route_segment_ids=route)
                    next_routes[vid] = route
                    reservations.update(route)
                    codes.extend(issues)
                elif cost == float("inf") or (issues and current["to_node"] != destination):
                    stop = self.safe_stop(vid, event, vehicle, states, occupied)
                    if stop:
                        occupied[stop] += 1
                        next_stops[vid] = stop
                        action.update(motion_action="SAFE_STOP", safe_stop_id=stop)
                    else:
                        action["motion_action"] = "HOLD"
                        codes.append("SAFE_STOP_CAPACITY")
                    codes.extend(issues or {"LOW_CONFIDENCE"})
                elif states[event["segment_id"]]["state"] in {"PARTIAL_BLOCK", "CONGESTED"}:
                    action.update(motion_action="LIMIT_SPEED", speed_limit_kmh=min(40, current["speed_limit_kmh"]))
                    codes.append("STATE_" + states[event["segment_id"]]["state"])
                elif hub.get("accepting_new_arrivals") is False and current["to_node"] == destination:
                    action.update(motion_action="LIMIT_SPEED", speed_limit_kmh=min(20, current["speed_limit_kmh"]))
                    codes.append("ROUTE_HUB_CAPACITY")
                else:
                    codes.append("STATE_OPEN")
            action["rationale_codes"] = sorted(set(codes))
            actions[vid] = action
        self.previous_remote = remote
        self.previous_routes = next_routes
        self.previous_stops = next_stops
        return actions
