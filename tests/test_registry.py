import hashlib
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('experiment_registry',Path(__file__).resolve().parents[1]/'tools/record_experiment.py')
registry=importlib.util.module_from_spec(spec);spec.loader.exec_module(registry)


class RegistryTests(unittest.TestCase):
    def test_mixed_windows_endings_need_observed_bytes_and_same_git_content(self):
        git=b'a=1\nb=2\n';mixed=b'a=1\r\nb=2\n';digest=hashlib.sha256(mixed).hexdigest()
        self.assertTrue(registry.matches_git(git,digest,mixed))
        self.assertFalse(registry.matches_git(git,digest))
        self.assertFalse(registry.matches_git(git,digest,b'a=1\nb=2\n'))
        changed=b'a=9\r\nb=2\n'
        self.assertFalse(registry.matches_git(git,hashlib.sha256(changed).hexdigest(),changed))
