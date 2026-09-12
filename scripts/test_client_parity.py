import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "scripts", "client_parity.py")


class TestClientParity(unittest.TestCase):
    def test_selftest(self):
        result = subprocess.run(
            [sys.executable, CLIENT, "--selftest"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("selftest OK", result.stdout)

    def test_real_repo(self):
        result = subprocess.run(
            [sys.executable, CLIENT],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
