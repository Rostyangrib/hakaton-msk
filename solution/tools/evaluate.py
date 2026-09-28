import argparse
import csv
import gzip
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from jsonschema import Draft202012Validator, FormatChecker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corridor.engine import Engine
from corridor.observations import timestamp
from corridor.reference import Reference
from train import label_groups


class Classification:
    def __init__(self):
        self.counts = Counter()

    def add(self, truth, prediction):
        self.counts[truth, prediction] += 1

    def summary(self):
        labels = sorted({a for a, _ in self.counts})
        result = {}
        for label in labels:
            tp = self.counts[label, label]
            fp = sum(n for (a, b), n in self.counts.items() if a != label and b == label)
            fn = sum(n for (a, b), n in self.counts.items() if a == label and b != label)
            result[label] = 2 * tp / max(1, 2 * tp + fp + fn)
        total = sum(self.counts.values())
        return {"macro_f1": mean(result.values()) if result else 1.0, "accuracy": sum(n for (a, b), n in self.counts.items() if a == b) / max(1, total), "per_class_f1": result, "count": total, "confusion": {a: {b: self.counts[a, b] for b in sorted({x[1] for x in self.counts})} for a in labels}}


class Multilabel:
    def __init__(self):
        self.tp = Counter()
        self.fp = Counter()
        self.fn = Counter()
        self.present = set()

    def add(self, truth, predicted):
        truth, predicted = set(truth), set(predicted)
        self.present.update(truth)
        self.tp.update(truth & predicted)
        self.fp.update(predicted - truth)
        self.fn.update(truth - predicted)

    def summary(self):
        labels = sorted(self.present | set(self.fp))
        per = {k: 2 * self.tp[k] / max(1, 2 * self.tp[k] + self.fp[k] + self.fn[k]) for k in labels}
        return {"macro_f1": mean(per.values()) if per else 1.0, "per_label_f1": per, "true_positives": dict(self.tp), "false_positives": dict(self.fp), "false_negatives": dict(self.fn)}


def validate_complete(validator, decision, reference):
    validator.validate(decision)
    for field, key, expected in [("state_estimates", "segment_id", reference.segments), ("source_assessments", "source_id", reference.sources), ("vehicle_assessments", "vehicle_id", reference.vehicles), ("vehicle_actions", "vehicle_id", reference.vehicles)]:
        found = [v[key] for v in decision[field]]
        if len(set(found)) != len(found) or set(found) != set(expected):
            raise ValueError("Incomplete or duplicate entities in " + field)


def valid_route(action, vehicle, truth_vehicle, truth_segments, reference, observed=None):
    position = observed or truth_vehicle
    current = reference.segments.get(position["segment_id"])
    route = action.get("route_segment_ids", [])
    if not route or not current:
        return False
    node = current["to_node"]
    for sid in route:
        row = reference.segments.get(sid)
        if not row or row["from_node"] != node or not row["av_allowed"] or row["weight_limit_t"] < vehicle["gross_mass_t"] or truth_segments[sid]["true_state"] == "CLOSED":
            return False
        node = row["to_node"]
    destination = reference.hubs.get(position.get("destination_hub_id", vehicle["destination_hub_id"]))
    return bool(destination and node == destination["node_id"])


def evaluate(root, scenario, output, model=None, tail=False):
    reference = Reference(root / "01_reference")
    engine = Engine(reference, model)
    validator = Draft202012Validator(json.loads((root / "04_contract/03_decision.schema.json").read_text()), format_checker=FormatChecker())
    labels_exist = (scenario / "labels").exists()
    segments = label_groups(scenario / "labels/01_segment_state.csv.gz", "segment_id") if labels_exist else {}
    vehicles = label_groups(scenario / "labels/02_vehicle_odd.csv.gz", "vehicle_id") if labels_exist else {}
    faults = []
    if labels_exist:
        with (scenario / "labels/03_source_faults.csv").open() as handle:
            for row in csv.DictReader(handle):
                row.update(start=timestamp(row["start_time"]), end=timestamp(row["end_time"]), detected=None)
                faults.append(row)
    segment_score, source_score, odd_score = Classification(), Classification(), Classification()
    fault_score, violation_score = Multilabel(), Multilabel()
    latencies, calibration = [], []
    valid, count, evaluated = 0, 0, 0
    active_count, exact_action_weight, action_weight = 0, 0.0, 0.0
    route_count = route_valid = stop_count = stop_valid = remote_bad = capacity_bad = 0
    critical_episodes = 0
    violation_runs = Counter()
    action_counts = Counter()
    previous_actions = {}
    switches = unjustified = 0
    confidence_buckets = defaultdict(lambda: [0, 0])
    metadata = json.loads((scenario / "scenario.json").read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(scenario / "packets.ndjson.gz", "rt") as incoming, output.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as outgoing:
        for line in incoming:
            packet = json.loads(line)
            began = time.perf_counter()
            decision = engine.process(packet)
            serialized = json.dumps(decision, separators=(",", ":"), allow_nan=False) + "\n"
            latencies.append((time.perf_counter() - began) * 1000)
            validate_complete(validator, decision, reference)
            valid += 1
            count += 1
            outgoing.write(serialized.encode())
            if count % 200 == 0:
                print(json.dumps({"event": "progress", "scenario": scenario.name, "packets": count}), flush=True)
            if not labels_exist or (tail and packet["step"] <= int(metadata["packet_count"] * 0.7)):
                continue
            evaluated += 1
            now = timestamp(packet["decision_time"])
            segtruth, vehtruth = segments[now], vehicles[now]
            active_faults = defaultdict(set)
            for fault in faults:
                if fault["start"] <= now < fault["end"]:
                    active_faults[fault["source_id"]].add(fault["fault_type"])
            for estimate in decision["state_estimates"]:
                truth = segtruth[estimate["segment_id"]]["true_state"]
                segment_score.add(truth, estimate["state"])
                bucket = "segment:" + estimate["state"] + ":" + str(min(9, int(estimate["confidence"] * 10)))
                confidence_buckets[bucket][0] += estimate["state"] == truth
                confidence_buckets[bucket][1] += 1
                calibration.append((estimate["confidence"] - (estimate["state"] == truth)) ** 2)
            for estimate in decision["source_assessments"]:
                truth_faults = active_faults[estimate["source_id"]]
                truth = "FAILED" if "OUTAGE" in truth_faults else "DEGRADED" if truth_faults else "OK"
                source_score.add(truth, estimate["status"])
                bucket = "source:" + estimate["status"] + ":" + str(min(9, int(estimate["confidence"] * 10)))
                confidence_buckets[bucket][0] += estimate["status"] == truth
                confidence_buckets[bucket][1] += 1
                fault_score.add(truth_faults, estimate["fault_types"])
                calibration.append((estimate["confidence"] - (estimate["status"] == truth)) ** 2)
                for fault in faults:
                    if fault["start"] <= now < fault["end"] and fault["source_id"] == estimate["source_id"] and fault["fault_type"] in estimate["fault_types"] and fault["detected"] is None:
                        fault["detected"] = now - fault["start"]
            for estimate in decision["vehicle_assessments"]:
                truth = vehtruth[estimate["vehicle_id"]]
                if truth["active"] != "1":
                    continue
                target = "COMPLIANT" if truth["odd_compliant"] == "1" else "VIOLATED"
                odd_score.add(target, estimate["odd_status"])
                bucket = "odd:" + estimate["odd_status"] + ":" + str(min(9, int(estimate["confidence"] * 10)))
                confidence_buckets[bucket][0] += estimate["odd_status"] == target
                confidence_buckets[bucket][1] += 1
                violation_score.add(filter(None, truth["violation_codes"].split("|")), estimate["violation_codes"])
                calibration.append((estimate["confidence"] - (estimate["odd_status"] == target)) ** 2)
            stops = Counter(a.get("safe_stop_id") for a in decision["vehicle_actions"] if a["motion_action"] == "SAFE_STOP")
            over_capacity = {s for s, n in stops.items() if s in reference.stops and n > reference.stops[s]["capacity_vehicles"]}
            remote_exceeded = sum(a["remote_support_required"] for a in decision["vehicle_actions"]) > reference.remote_limit
            remote_bad += remote_exceeded
            capacity_bad += bool(over_capacity)
            for action in decision["vehicle_actions"]:
                vid = action["vehicle_id"]
                truth = vehtruth[vid]
                vehicle = reference.vehicles[vid]
                motion = action["motion_action"]
                action_counts[motion] += 1
                invalid = False
                if motion == "REROUTE":
                    route_count += 1
                    good = valid_route(action, vehicle, truth, segtruth, reference, engine.estimator.telemetry.get(vid))
                    route_valid += good
                    invalid |= not good
                if motion == "SAFE_STOP":
                    stop_count += 1
                    stop = reference.stops.get(action.get("safe_stop_id"))
                    good = bool(stop and stop["max_vehicle_mass_t"] >= vehicle["gross_mass_t"] and reference.segments[stop["segment_id"]]["weight_limit_t"] >= vehicle["gross_mass_t"] and segtruth[stop["segment_id"]]["true_state"] != "CLOSED" and action["safe_stop_id"] not in over_capacity)
                    stop_valid += good
                    invalid |= not good
                if truth["active"] == "1":
                    active_count += 1
                    weight = 6 - vehicle["cargo_priority"]
                    expected = truth["reference_action_class"]
                    matching = action["remote_support_required"] if expected == "REMOTE_SUPPORT" else motion == expected
                    exact_action_weight += weight * matching
                    action_weight += weight
                    invalid |= truth["odd_compliant"] == "0" and motion in {"CONTINUE", "NO_ACTION"} and not action["remote_support_required"]
                    invalid |= remote_exceeded
                    key = (motion, action.get("safe_stop_id"), tuple(action.get("route_segment_ids", [])))
                    truth_key = (truth["odd_compliant"], truth["violation_codes"], truth["segment_id"], segtruth[truth["segment_id"]]["true_state"])
                    old = previous_actions.get(vid)
                    if old and old[0] != key:
                        switches += 1
                        unjustified += old[1] == truth_key and old[2] == action["rationale_codes"]
                    previous_actions[vid] = (key, truth_key, action["rationale_codes"])
                violation_runs[vid] = violation_runs[vid] + 1 if invalid else 0
                critical_episodes += violation_runs[vid] == 3
    ordered = sorted(latencies)
    report = {"scenario": scenario.name, "packets": count, "schema_valid_packets": valid, "response_completeness": valid / max(1, count), "latency_ms": {"mean": mean(latencies), "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], "max": max(latencies)}, "timeout_packets": sum(x > 2000 for x in latencies), "result_bytes": output.stat().st_size}
    if labels_exist:
        delay = mean(max(0, 1 - (f["detected"] if f["detected"] is not None else 120) / 120) for f in faults) if faults else 1.0
        source, fault = source_score.summary(), fault_score.summary()
        report.update(confidence_buckets=dict(confidence_buckets), evaluated_packets=evaluated, segment_state=segment_score.summary(), source_status=source, source_fault_types=fault, source_detection_delay_score=delay, source_fault_composite=0.5 * source["macro_f1"] + 0.25 * fault["macro_f1"] + 0.25 * delay, source_detection_delays=[{"source_id": f["source_id"], "fault_type": f["fault_type"], "delay_sec": f["detected"]} for f in faults], odd_status=odd_score.summary(), odd_violation_codes=violation_score.summary(), confidence_calibration=1 - mean(calibration), weighted_reference_action_agreement=exact_action_weight / max(1, action_weight), actions=dict(action_counts), reroutes=route_count, full_route_validity=route_valid / max(1, route_count) if route_count else 1.0, safe_stop_assignments=stop_count, safe_stop_constraint_score=stop_valid / max(1, stop_count) if stop_count else 1.0, remote_support_overcapacity_steps=remote_bad, safe_stop_overcapacity_steps=capacity_bad, local_critical_episodes=critical_episodes, action_switches=switches, locally_unjustified_switches=unjustified, local_action_stability=max(0, 1 - unjustified / max(1, 0.1 * active_count)))
    if tail and labels_exist:
        report["source_detection_delay_score"] = None
        report["source_fault_composite"] = None
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--split", choices=["train", "public", "all"], default="all")
    parser.add_argument("--scenario")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--tail", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--report", type=Path, default=Path("reports/metrics.json"))
    args = parser.parse_args()
    scenarios = []
    if args.split in {"train", "all"}:
        scenarios.extend(sorted((args.data / "02_train").iterdir()))
    if args.split in {"public", "all"}:
        scenarios.extend(sorted((args.data / "03_public").iterdir()))
    reports = []
    for scenario in scenarios:
        if not scenario.is_dir() or (args.scenario and scenario.name != args.scenario):
            continue
        report = evaluate(args.data, scenario, args.output / (scenario.name + ".result.ndjson.gz"), args.model, args.tail)
        reports.append(report)
        print(json.dumps({"event": "evaluated", "scenario": scenario.name, "segment_f1": report.get("segment_state", {}).get("macro_f1"), "odd_f1": report.get("odd_status", {}).get("macro_f1"), "source_score": report.get("source_fault_composite"), "critical_episodes": report.get("local_critical_episodes"), "latency_ms": report["latency_ms"]}), flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(reports, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
