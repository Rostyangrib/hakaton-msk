import argparse
import json
import os
import resource
import sys
import time

from .engine import Engine
from .reference import Reference


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", default=os.environ.get("CORRIDOR_REFERENCE", "/data/reference"))
    parser.add_argument("--scenario", default=os.environ.get("CORRIDOR_SCENARIO", "/data/scenario/scenario.json"))
    parser.add_argument("--model")
    args = parser.parse_args()
    started = time.perf_counter()
    engine = Engine(Reference(args.reference), args.model)
    print(json.dumps({"event": "ready", "startup_ms": round((time.perf_counter() - started) * 1000, 3)}), file=sys.stderr, flush=True)
    count = 0
    for line in sys.stdin:
        if not line.strip():
            continue
        started = time.perf_counter()
        packet = json.loads(line)
        decision = engine.process(packet)
        sys.stdout.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        sys.stdout.flush()
        count += 1
        if count == 1 or count % 100 == 0 or packet.get("is_final"):
            print(json.dumps({"event": "packet", "packet_id": packet["packet_id"], "latency_ms": round((time.perf_counter() - started) * 1000, 3), "duplicates": engine.observations.duplicate_count, "late_events": engine.observations.late_count, "rejected_events": engine.observations.rejected_count}), file=sys.stderr, flush=True)
    print(json.dumps({"event": "complete", "packets": count, "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)}), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
