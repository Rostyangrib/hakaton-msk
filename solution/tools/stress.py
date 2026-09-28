import copy
import gzip
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corridor.engine import Engine
from corridor.reference import Reference
from evaluate import validate_complete


def main():
    data = Path(sys.argv[1])
    reference = Reference(data / "01_reference")
    with gzip.open(data / "02_train/TRAIN-001/packets.ndjson.gz", "rt") as handle:
        template = json.loads(next(handle))
    validator = Draft202012Validator(json.loads((data / "04_contract/03_decision.schema.json").read_text()), format_checker=FormatChecker())
    engine = Engine(reference)
    randomizer = random.Random(7391)
    latencies = []
    for index in range(240):
        packet = copy.deepcopy(template)
        packet.update(scenario_id="STRESS", packet_id=f"STRESS-{index}", step=index + 1)
        for obj in [packet] + packet["events"]:
            for key, value in list(obj.items()):
                if key.endswith("time") or key in {"window_start", "window_end"}:
                    if isinstance(value, str):
                        obj[key] = (datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(seconds=index * 5)).isoformat()
                elif key == "event_id":
                    obj[key] = f"{index}-{value}"
            if "scenario_id" in obj:
                obj["scenario_id"] = "STRESS"
        if 80 <= index < 130:
            packet["events"] = []
        else:
            packet["events"] = [event for event in packet["events"] if randomizer.random() > 0.65]
            packet["events"] += copy.deepcopy(packet["events"][:20])
            randomizer.shuffle(packet["events"])
            for event in packet["events"]:
                if index % 13 == 0 and "segment_id" in event:
                    event["segment_id"] = "S999"
                if index % 7 == 0 and event["event_type"] == "VEHICLE_TELEMETRY":
                    event["map_age_min"] = 10000
                event["unknown_extension"] = {"value": "ignored"}
        packet["event_count"] = len(packet["events"])
        began = time.perf_counter()
        decision = engine.process(packet)
        json.dumps(decision, allow_nan=False)
        latencies.append((time.perf_counter() - began) * 1000)
        validate_complete(validator, decision, reference)
        assert sum(a["remote_support_required"] for a in decision["vehicle_actions"]) <= reference.remote_limit
        for stop_id, stop in reference.stops.items():
            assert sum(a.get("safe_stop_id") == stop_id for a in decision["vehicle_actions"]) <= stop["capacity_vehicles"]
        for action in decision["vehicle_actions"]:
            if action["motion_action"] == "REROUTE":
                event = engine.estimator.telemetry[action["vehicle_id"]]
                start = reference.segments[event["segment_id"]]["to_node"]
                destination = reference.hubs[event["destination_hub_id"]]["node_id"]
                assert engine.planner.valid_route(action["route_segment_ids"], start, destination, reference.vehicles[action["vehicle_id"]], engine.estimator.states)
        if 150 <= index and index % 7 == 0:
            assert engine.process(packet) == decision
    report = {"packets": len(latencies), "seed": 7391, "schema_valid": True, "resources_valid": True, "reroute_invariants_valid": True, "latency_ms_max": max(latencies), "mutations": ["65% event loss", "250 second complete outage", "duplicates", "shuffled delivery", "unknown entity references", "mass simultaneous ODD violations", "unknown optional fields", "repeated packet"]}
    Path("reports/stress.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
