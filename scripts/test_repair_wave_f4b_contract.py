"""F4b (MECHANISM-AUDIT item 21/4): repair_wave.py's job dispatch carried a flat estimated_cost
0.02 for every job, whatever the model. The M4.4 dispatcher contract (openrouter_dispatch.py,
DP33 at a80c607bf): a job with no estimated_cost gets the M4.3 per-model estimate, falling back to
a price-catalog worst case, never zero. So repair_wave.py's own job dict must stop passing a flat
figure and let the dispatcher price it.

Reuses test_repair_wave_contract.py's own safe harness (every model, subprocess and fan-out seam
replaced; no network, credential or spend) rather than a second copy of it.
Run from the repository root: python3 -B scripts/test_repair_wave_f4b_contract.py
"""
import json
import tempfile
import unittest
from pathlib import Path

from test_repair_wave_contract import run_wave, waves


class F4bContract(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repair-f4b-"))
        self.bw, self.pw, self.nw = waves(self.root)
        self.tail = [str(self.bw), str(self.pw), str(self.nw)]

    def test_no_repair_job_carries_the_flat_legacy_estimate(self):
        """The M4.4 contract: a job with no estimated_cost gets the dispatcher's own per-model
        estimate, falling back to a price-catalog worst case, never zero and never the old flat
        0.02 figure repair_wave.py used to write for every job regardless of model."""
        code, calls, out = run_wave(self.tail + ["1"])
        self.assertEqual(code, 0, out)
        jobs = json.loads((self.nw / "jobs.json").read_text())
        self.assertTrue(jobs, "fixture produced no jobs to check: " + out)
        for j in jobs:
            self.assertNotEqual(
                j.get("estimated_cost"), 0.02,
                "job %r still carries the flat legacy estimate 0.02" % (j.get("id"),))
            self.assertNotEqual(
                j.get("estimated_cost"), 0,
                "job %r carries 0: the contract is per-model estimate or omitted, never zero either" % (j.get("id"),))

    def test_the_job_dict_has_no_estimated_cost_key_at_all(self):
        """The M4.4 contract is silent on HOW a job asks for the dispatcher's own estimate, only
        that it must not be a flat figure; the simplest, most direct way to ask for it is to omit
        the key entirely, which this case pins down so a future change cannot quietly reintroduce
        a different flat number instead of 0.02."""
        code, calls, out = run_wave(self.tail + ["1"])
        self.assertEqual(code, 0, out)
        jobs = json.loads((self.nw / "jobs.json").read_text())
        self.assertTrue(jobs, out)
        for j in jobs:
            self.assertNotIn("estimated_cost", j, "job %r still carries an estimated_cost key: %r" % (j.get("id"), j))


if __name__ == "__main__":
    unittest.main(verbosity=1)
