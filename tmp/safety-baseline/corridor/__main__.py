import argparse
import json
import sys
import time
from .controller import Controller
from .reference import Reference
from .stream import packets


def main():
    parser = argparse.ArgumentParser(description='Corridor controller: NDJSON stdin → NDJSON stdout')
    parser.add_argument('--reference', required=True)
    args = parser.parse_args()
    controller = Controller(Reference(args.reference))
    for packet in packets(sys.stdin):
        start = time.perf_counter()
        decision = controller.process(packet)
        print(json.dumps(decision, ensure_ascii=False, separators=(',', ':'), allow_nan=False), flush=True)
        print(json.dumps(dict(packet_id=packet['packet_id'], elapsed_ms=(time.perf_counter()-start)*1000,
                              diagnostics=controller.state.diagnostics), ensure_ascii=False), file=sys.stderr, flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
