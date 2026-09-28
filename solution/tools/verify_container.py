import argparse
import gzip
import hashlib
import json
import select
import subprocess
import time
from pathlib import Path
from statistics import mean


def verify(data, scenario, image, timeout):
    command = ["docker", "run", "--rm", "-i", "--platform", "linux/amd64", "--network", "none", "--read-only", "--cpus", "8", "--memory", "16g", "--pids-limit", "64", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "-v", str((data / "01_reference").resolve()) + ":/data/reference:ro", "-v", str((scenario / "scenario.json").resolve()) + ":/data/scenario/scenario.json:ro", image]
    timings = []
    digest = hashlib.sha256()
    startup = None
    expected = hashlib.sha256()
    with gzip.open(Path("results") / (scenario.name + ".result.ndjson.gz"), "rb") as handle:
        for line in handle:
            expected.update(line)
    log_path = Path("reports") / (scenario.name + ".docker.stderr.jsonl")
    with log_path.open("wb") as log:
        began = time.perf_counter()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=-1)
        try:
            with gzip.open(scenario / "packets.ndjson.gz", "rb") as incoming:
                count = 0
                for line in incoming:
                    started = time.perf_counter()
                    view = memoryview(line)
                    while view:
                        written = process.stdin.write(view)
                        if not written:
                            raise RuntimeError("Container input closed")
                        view = view[written:]
                    process.stdin.flush()
                    limit = 60 if count == 0 else timeout
                    if not select.select([process.stdout], [], [], limit)[0]:
                        raise TimeoutError("Container response timeout")
                    response = process.stdout.readline()
                    elapsed = (time.perf_counter() - started) * 1000
                    if not response:
                        raise RuntimeError("Container closed output")
                    if count == 0:
                        startup = time.perf_counter() - began
                    else:
                        if elapsed > timeout * 1000:
                            raise TimeoutError("Packet deadline exceeded")
                    decoded = json.loads(response)
                    packet = json.loads(line)
                    if decoded["packet_id"] != packet["packet_id"] or decoded["scenario_id"] != packet["scenario_id"]:
                        raise ValueError("Incorrect response identity")
                    timings.append(elapsed)
                    digest.update(response)
                    count += 1
                    if count % 200 == 0:
                        print(json.dumps({"event": "docker_progress", "scenario": scenario.name, "packets": count}), flush=True)
            process.stdin.close()
            remaining = process.stdout.read()
            code = process.wait(timeout=10)
            if remaining or code:
                raise RuntimeError("Extra output or nonzero exit")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if digest.digest() != expected.digest():
        raise AssertionError("Container result differs from local reference run")
    steady = sorted(timings[1:] or timings)
    return {"scenario": scenario.name, "packets": count, "startup_and_first_packet_sec": startup, "latency_ms": {"mean": mean(steady), "p95": steady[min(len(steady) - 1, int(len(steady) * 0.95))], "max": max(steady)}, "sha256_uncompressed": digest.hexdigest(), "matches_local": True, "network": "none", "exit_code": code}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--image", default="corridor-solution:final")
    parser.add_argument("--split", choices=["all", "train", "public"], default="all")
    parser.add_argument("--report", type=Path, default=Path("reports/docker_verification.json"))
    args = parser.parse_args()
    reports = []
    for split, folder in [("train", "02_train"), ("public", "03_public")]:
        if args.split not in {"all", split}:
            continue
        for scenario in sorted((args.data / folder).iterdir()):
            report = verify(args.data, scenario, args.image, 2)
            reports.append(report)
            args.report.write_text(json.dumps(reports, indent=2))
            print(json.dumps({"event": "docker_verified", **report}), flush=True)


if __name__ == "__main__":
    main()
