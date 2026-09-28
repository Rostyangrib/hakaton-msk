import argparse
import json
from collections import defaultdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("reports/holdout.json"))
    parser.add_argument("--model", type=Path, default=Path("models/forest.json"))
    args = parser.parse_args()
    totals = defaultdict(lambda: [0, 0])
    for report in json.loads(args.report.read_text()):
        for key, values in report["confidence_buckets"].items():
            totals[key][0] += values[0]
            totals[key][1] += values[1]
    calibration = {}
    for key, (correct, count) in totals.items():
        prior = (int(key.rsplit(":", 1)[1]) + 0.5) / 10
        calibration[key] = round(min(0.995, max(0.01, (correct + 20 * prior) / (count + 20))), 5)
    model = json.loads(args.model.read_text())
    model["calibration"] = calibration
    args.model.write_text(json.dumps(model, separators=(",", ":")))
    Path("reports/calibration.json").write_text(json.dumps({"method": "Temporal holdout correctness, 10 confidence bins, Beta prior strength 20", "buckets": dict(totals), "confidence": calibration}, indent=2))


if __name__ == "__main__":
    main()
