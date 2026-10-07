"""The disk premise reaches descendants and leaves real readings intact."""
import json
import os
import shutil
import subprocess
import sys
import unittest

from hermetic_worker_env import worker_environment


class WorkerEnvironment(unittest.TestCase):
    def test_pin_reaches_child_and_grandchild_and_restores_parent(self):
        original = shutil.disk_usage
        before = dict(os.environ)
        read = "import shutil; print(shutil.disk_usage('.').free)"
        child = ("import subprocess, sys; " + read +
                 "; subprocess.run([sys.executable, '-c', %r], check=True)" % read)
        with worker_environment() as env:
            root = os.path.dirname(env["BROTHER_LOAD_REFUSALS_LOG"])
            self.assertEqual(shutil.disk_usage(".").free, 500 * 1024 ** 3)
            proc = subprocess.run([sys.executable, "-c", child], env=env,
                                  capture_output=True, text=True, check=True)
            self.assertEqual(proc.stdout.splitlines(), [str(500 * 1024 ** 3)] * 2)
            for key in ("BROTHER_LOAD_REFUSALS_LOG", "BROTHER_MACHINE_RESERVATION_PATH"):
                self.assertEqual(os.path.dirname(env[key]), root)
                with open(env[key], "w", encoding="utf-8") as fh:
                    fh.write("fixture state\n")
        self.assertIs(shutil.disk_usage, original)
        self.assertEqual(dict(os.environ), before)
        self.assertFalse(os.path.exists(root))

    def test_without_fixture_child_reads_the_real_filesystem(self):
        # Compare in the same process, avoiding free-space races between reads.
        code = """import json, os, shutil
s = os.statvfs('.')
print(json.dumps([shutil.disk_usage('.').free, s.f_bavail * s.f_frsize]))
"""
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, check=True)
        measured, actual = json.loads(proc.stdout)
        self.assertAlmostEqual(measured, actual, delta=16 * 1024 ** 2)


if __name__ == "__main__":
    unittest.main()
