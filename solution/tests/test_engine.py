import copy
import gzip
import json
import os
import unittest
from pathlib import Path
from datetime import datetime, timedelta

from jsonschema import Draft202012Validator, FormatChecker

from corridor.engine import Engine
from corridor.models import Forest
from corridor.reference import Reference


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ["CORRIDOR_DATA"]) if "CORRIDOR_DATA" in os.environ else next(ROOT.glob("*/03_*"))


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = Reference(DATA / "01_reference")
        with gzip.open(DATA / "02_train/TRAIN-001/packets.ndjson.gz", "rt") as handle:
            cls.packet = json.loads(next(handle))
        cls.validator = Draft202012Validator(json.loads((DATA / "04_contract/03_decision.schema.json").read_text()), format_checker=FormatChecker())

    def engine(self):
        return Engine(self.reference)

    def test_complete_snapshot(self):
        result = self.engine().process(self.packet)
        self.validator.validate(result)
        for field, key, expected in [("state_estimates", "segment_id", self.reference.segments), ("source_assessments", "source_id", self.reference.sources), ("vehicle_actions", "vehicle_id", self.reference.vehicles)]:
            self.assertEqual({x[key] for x in result[field]}, set(expected))

    def test_empty_packet_and_scenario_reset(self):
        engine = self.engine()
        engine.process(self.packet)
        packet = dict(self.packet, scenario_id="UNSEEN", packet_id="UNSEEN-1", events=[])
        result = engine.process(packet)
        self.validator.validate(result)
        self.assertTrue(all(x["state"] == "UNKNOWN" for x in result["state_estimates"]))
        self.assertTrue(all(x["motion_action"] == "HOLD" for x in result["vehicle_actions"]))

    def test_duplicate_events_and_idempotency(self):
        engine = self.engine()
        expected = engine.process(self.packet)
        self.assertEqual(expected, engine.process(self.packet))
        packet = copy.deepcopy(self.packet)
        packet["events"] += packet["events"]
        self.assertEqual(expected, self.engine().process(packet))

    def test_future_events_are_unavailable(self):
        packet = copy.deepcopy(self.packet)
        for event in packet["events"]:
            event["received_time"] = "2099-01-01T00:00:00Z"
        result = self.engine().process(packet)
        self.assertTrue(all(x["state"] == "UNKNOWN" for x in result["state_estimates"]))

    def test_input_order_and_unknown_fields(self):
        packet = copy.deepcopy(self.packet)
        packet["future_unused_field"] = {"value": 100}
        packet["events"].reverse()
        engine = self.engine()
        expected = self.engine().process(self.packet)
        actual = engine.process(packet)
        self.assertEqual(expected, actual)

    def test_closed_segment_rule_without_training_class(self):
        packet = copy.deepcopy(self.packet)
        for event in packet["events"]:
            if event.get("segment_id") == "S002" and event["event_type"] == "ROAD_OBSERVATION":
                event.update(lane_count_open_estimate=0, lane_status="CLOSED")
        result = self.engine().process(packet)
        state = next(x for x in result["state_estimates"] if x["segment_id"] == "S002")
        self.assertEqual(state["state"], "CLOSED")

    def test_resources_under_mass_violation(self):
        packet = copy.deepcopy(self.packet)
        for event in packet["events"]:
            if event["event_type"] == "VEHICLE_TELEMETRY":
                event.update(map_age_min=10000, autonomy_state="DEGRADED")
        result = self.engine().process(packet)
        self.validator.validate(result)
        self.assertLessEqual(sum(a["remote_support_required"] for a in result["vehicle_actions"]), self.reference.remote_limit)
        for sid, stop in self.reference.stops.items():
            occupants = [a for a in result["vehicle_actions"] if a.get("safe_stop_id") == sid]
            self.assertLessEqual(len(occupants), stop["capacity_vehicles"])
            self.assertTrue(all(self.reference.vehicles[a["vehicle_id"]]["gross_mass_t"] <= stop["max_vehicle_mass_t"] for a in occupants))
        self.assertFalse(any(a["motion_action"] in {"CONTINUE", "NO_ACTION"} for a in result["vehicle_actions"]))

    def test_routes_are_directed_complete_and_mass_compatible(self):
        engine = self.engine()
        engine.process(self.packet)
        states = engine.estimator.states
        for vid, vehicle in self.reference.vehicles.items():
            event = engine.estimator.telemetry[vid]
            start = self.reference.segments[event["segment_id"]]["to_node"]
            end = self.reference.hubs[vehicle["destination_hub_id"]]["node_id"]
            cost, route = engine.planner.path(start, end, vehicle, states)
            if cost == float("inf"):
                continue
            node = start
            for sid in route:
                segment = self.reference.segments[sid]
                self.assertEqual(segment["from_node"], node)
                self.assertGreaterEqual(segment["weight_limit_t"], vehicle["gross_mass_t"])
                self.assertTrue(segment["av_allowed"])
                node = segment["to_node"]
            self.assertEqual(node, end)

    def test_false_lane_closure_requires_fresh_independent_evidence(self):
        packet = copy.deepcopy(self.packet)
        latest_time = max(e["event_time"] for e in packet["events"] if e.get("source_id") == "RSU-01" and e["event_type"] == "V2X_MESSAGE")
        for event in packet["events"]:
            if event["event_type"] == "V2X_MESSAGE" and event["source_id"] == "RSU-01":
                event.update(segment_id="S002", lane_status="CLOSED")
            if event["event_type"] in {"ROAD_OBSERVATION", "DIGITAL_TWIN_SEGMENT"} and event.get("segment_id") == "S002":
                event["event_time"] = latest_time
                event["received_time"] = latest_time
        result = self.engine().process(packet)
        source = next(x for x in result["source_assessments"] if x["source_id"] == "RSU-01")
        self.assertIn("FALSE_LANE_CLOSURE", source["fault_types"])
        segment = next(x for x in result["state_estimates"] if x["segment_id"] == "S002")
        self.assertNotEqual(segment["state"], "CLOSED")

    def test_time_skew_is_separate_from_delay(self):
        packet = copy.deepcopy(self.packet)
        for event in packet["events"]:
            if event.get("source_id") == "RSU-01" and event["event_type"] == "V2X_MESSAGE":
                event["source_clock_offset_ms"] = 5000
        result = self.engine().process(packet)
        source = next(x for x in result["source_assessments"] if x["source_id"] == "RSU-01")
        self.assertIn("TIME_SKEW", source["fault_types"])

    def test_predictions_do_not_depend_on_scenario_id_or_absolute_time(self):
        packet = copy.deepcopy(self.packet)
        packet["scenario_id"] = "HIDDEN-UNSEEN"
        packet["packet_id"] = "HIDDEN-PACKET"
        for obj in [packet] + packet["events"]:
            for key, value in list(obj.items()):
                if key.endswith("time") or key in {"window_start", "window_end"}:
                    if isinstance(value, str):
                        moment = datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(days=123)
                        obj[key] = moment.isoformat()
                elif key == "scenario_id":
                    obj[key] = "HIDDEN-UNSEEN"
                elif key == "event_id":
                    obj[key] = "renamed-" + value
        expected = self.engine().process(self.packet)
        actual = self.engine().process(packet)
        for field in ["state_estimates", "source_assessments", "vehicle_assessments", "vehicle_actions"]:
            self.assertEqual(expected[field], actual[field])

    def test_reroute_action_avoids_a_blocked_future_segment(self):
        engine = self.engine()
        engine.process(self.packet)
        states = engine.estimator.states
        for vid, vehicle in self.reference.vehicles.items():
            event = engine.estimator.telemetry[vid]
            start = self.reference.segments[event["segment_id"]]["to_node"]
            destination = self.reference.hubs[event["destination_hub_id"]]["node_id"]
            _, base = engine.planner.path(start, destination, vehicle, states)
            for blocked in base:
                modified = copy.deepcopy(states)
                modified[blocked]["state"] = "CLOSED"
                cost, alternative = engine.planner.path(start, destination, vehicle, modified)
                if cost != float("inf") and alternative != base:
                    engine.estimator.states = modified
                    action = engine.planner.plan(engine.estimator)[vid]
                    self.assertEqual(action["motion_action"], "REROUTE")
                    self.assertNotIn(blocked, action["route_segment_ids"])
                    self.assertEqual(self.reference.segments[action["route_segment_ids"][-1]]["to_node"], destination)
                    return
        self.fail("No route-diversion fixture found in the reference graph")

    def test_lost_telemetry_at_hub_does_not_imply_inactivity(self):
        engine = self.engine()
        engine.process(self.packet)
        vid = next(iter(self.reference.vehicles))
        sid = next(s for s, row in self.reference.segments.items() if row["from_node"] in {h["node_id"] for h in self.reference.hubs.values()})
        event = engine.estimator.telemetry[vid]
        event.update(segment_id=sid, offset_m=0, speed_kmh=80, _time=engine.observations.now - 3)
        self.assertFalse(engine.estimator.is_inactive(vid))

    def test_unknown_road_reference_keeps_snapshot_valid(self):
        packet = copy.deepcopy(self.packet)
        for event in packet["events"]:
            if "segment_id" in event:
                event["segment_id"] = "S999"
        result = self.engine().process(packet)
        self.validator.validate(result)
        self.assertTrue(all(a["motion_action"] == "HOLD" for a in result["vehicle_actions"]))

    def test_stale_twin_alone_cannot_prove_an_open_road(self):
        packet = copy.deepcopy(self.packet)
        packet["events"] = [e for e in packet["events"] if e["event_type"] == "DIGITAL_TWIN_SEGMENT"]
        for event in packet["events"]:
            event["snapshot_age_sec"] = 600
        result = self.engine().process(packet)
        self.assertTrue(all(s["state"] == "UNKNOWN" for s in result["state_estimates"]))

    def test_prolonged_observation_outage_produces_safe_complete_snapshot(self):
        engine = self.engine()
        engine.process(self.packet)
        packet = copy.deepcopy(self.packet)
        packet["events"] = []
        packet["packet_id"] = "OUTAGE"
        packet["decision_time"] = (datetime.fromisoformat(packet["decision_time"].replace("Z", "+00:00")) + timedelta(seconds=300)).isoformat()
        result = engine.process(packet)
        self.validator.validate(result)
        self.assertTrue(all(s["state"] == "UNKNOWN" for s in result["state_estimates"]))
        self.assertTrue(all(a["motion_action"] == "HOLD" for a in result["vehicle_actions"]))

    def test_commands_do_not_move_vehicles(self):
        engine = self.engine()
        engine.process(self.packet)
        before = copy.deepcopy(engine.estimator.telemetry)
        engine.planner.previous_routes = {vid: ["S086"] for vid in self.reference.vehicles}
        engine.planner.previous_stops = {vid: "SS-01" for vid in self.reference.vehicles}
        packet = dict(self.packet, packet_id="next", events=[])
        engine.process(packet)
        self.assertEqual(before, engine.estimator.telemetry)


if __name__ == "__main__":
    unittest.main()
