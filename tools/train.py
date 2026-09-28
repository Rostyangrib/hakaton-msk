import argparse
import csv
import gzip
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import f1_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corridor.estimation import Estimator
from corridor.models import Forest
from corridor.observations import Observations, timestamp
from corridor.reference import Reference


def label_groups(path, entity):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        result = {}
        for row in csv.DictReader(handle):
            result.setdefault(timestamp(row["timestamp"]), {})[row[entity]] = row
        return result


def export(model):
    trees = []
    for estimator in model.estimators_:
        tree = estimator.tree_
        values = tree.value[:, 0, :]
        values = values / np.maximum(values.sum(axis=1, keepdims=True), 1e-12)
        trees.append({"left": tree.children_left.tolist(), "right": tree.children_right.tolist(), "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(), "value": values.tolist()})
    return {"classes": model.classes_.tolist(), "trees": trees}


def collect(root, model_path, mode):
    reference = Reference(root / "01_reference")
    data = {name: [[], [], [], []] for name in (["source"] if mode == "source" else ["segment", "visibility", "rain"])}
    for scenario in sorted((root / "02_train").iterdir()):
        if not scenario.is_dir():
            continue
        metadata = json.loads((scenario / "scenario.json").read_text())
        cutoff = int(metadata["packet_count"] * 0.7)
        segment_labels = label_groups(scenario / "labels/01_segment_state.csv.gz", "segment_id") if mode != "source" else {}
        vehicle_labels = label_groups(scenario / "labels/02_vehicle_odd.csv.gz", "vehicle_id") if mode != "source" else {}
        with (scenario / "labels/03_source_faults.csv").open() as handle:
            faults = list(csv.DictReader(handle))
        for fault in faults:
            fault["start"] = timestamp(fault["start_time"])
            fault["end"] = timestamp(fault["end_time"])
        obs = Observations()
        estimator = Estimator(reference, obs, Forest(model_path))
        with gzip.open(scenario / "packets.ndjson.gz", "rt") as handle:
            for line in handle:
                packet = json.loads(line)
                obs.ingest(packet)
                estimator.estimate_sources()
                mask = 0 if packet["step"] <= cutoff - 24 else 1 if packet["step"] > cutoff else 2
                if mode == "source":
                    truth = {f["source_id"]: f["fault_type"] for f in faults if f["start"] <= obs.now < f["end"]}
                    for sid, features in estimator.source_features.items():
                        for container, value in zip(data["source"], [features, truth.get(sid, "OK"), mask, scenario.name]):
                            container.append(value)
                    continue
                estimator.estimate_segments()
                estimator.estimate_vehicles()
                for sid, features in estimator.segment_features.items():
                    for container, value in zip(data["segment"], [features, segment_labels[obs.now][sid]["true_state"], mask, scenario.name]):
                        container.append(value)
                for vid, features in estimator.vehicle_features.items():
                    truth = vehicle_labels[obs.now][vid]
                    if truth["active"] != "1":
                        continue
                    codes = truth["violation_codes"].split("|")
                    for name in ["visibility", "rain"]:
                        for container, value in zip(data[name], [features, int(name.upper() in codes), mask, scenario.name]):
                            container.append(value)
        print(json.dumps({"event": "features", "scenario": scenario.name, "mode": mode}), flush=True)
    return data


def fit(data, final_models, holdout_models, report):
    for name, (features, labels, masks, scenarios) in data.items():
        x = np.asarray(features, dtype=np.float32)
        y = np.asarray(labels)
        masks = np.asarray(masks)
        parameters = {"n_estimators": 32, "max_depth": 14, "min_samples_leaf": 3, "max_features": 0.85, "random_state": 271828, "n_jobs": 4}
        holdout = ExtraTreesClassifier(**parameters).fit(x[masks == 0], y[masks == 0])
        prediction = holdout.predict(x[masks == 1])
        validation_labels = sorted(set(y[masks == 1]))
        score = f1_score(y[masks == 1], prediction, labels=validation_labels, average="macro", zero_division=0)
        report[name] = {"samples": len(y), "validation_samples": int(sum(masks == 1)), "temporal_holdout_macro_f1": float(score), "validation_classes": [str(v) for v in validation_labels]}
        holdout_models[name] = export(holdout)
        final = ExtraTreesClassifier(**parameters).fit(x, y)
        final_models[name] = export(final)
        print(json.dumps({"event": "fit", "model": name, **report[name]}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("models/forest.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    final, holdout, report = {}, {}, {}
    empty = args.output.parent / "empty.json"
    empty.write_text("{}")
    fit(collect(args.data, empty, "source"), final, holdout, report)
    args.output.write_text(json.dumps(final, separators=(",", ":")))
    holdout_path = args.output.parent / "holdout.json"
    holdout_path.write_text(json.dumps(holdout, separators=(",", ":")))
    fit(collect(args.data, holdout_path, "state"), final, holdout, report)
    args.output.write_text(json.dumps(final, separators=(",", ":")))
    (args.output.parent / "holdout.json").write_text(json.dumps(holdout, separators=(",", ":")))
    Path("reports/training.json").write_text(json.dumps(report, indent=2))
    empty.unlink()


if __name__ == "__main__":
    main()
