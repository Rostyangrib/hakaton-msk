import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from corridor.contract import Contract
from corridor.reference import Reference


class CliTests(unittest.TestCase):
    def test_real_packets_stdout_stderr_eof(self):
        reference = Path(os.environ['CORRIDOR_REFERENCE'])
        with gzip.open(reference.parent / '02_train/TRAIN-001/packets.ndjson.gz', 'rt', encoding='utf-8') as stream:
            lines = [next(stream), next(stream)]
        result = subprocess.run([sys.executable, '-m', 'corridor', '--reference', str(reference)],
                                input=''.join(lines), capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout.splitlines()
        self.assertEqual(len(output), 2)
        validator = Contract(Reference(reference))
        for line in output: validator.validate(json.loads(line))
        self.assertEqual(len(result.stderr.splitlines()), 2)
        for line in result.stderr.splitlines():
            self.assertLess(json.loads(line)['elapsed_ms'], 2000)
