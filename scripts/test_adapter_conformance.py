#!/usr/bin/env python3
"""adapter_conformance, pinned without a provider: the verdict rule, the
durable-receipt rule, the Cortex short circuit (NO-DATA on every step, never
PASS, never FAIL) and the separator-free done check a resumed record needs."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import adapter_conformance as AC  # noqa: E402
import provider_adapter as PA  # noqa: E402


class VerdictRule(unittest.TestCase):
    def test_any_fail_is_fail(self):
        results = [AC.Step("a", AC.PASS, ""), AC.Step("b", AC.FAIL, ""), AC.Step("c", AC.NODATA, "")]
        self.assertEqual(AC._verdict_for(results), AC.FAIL)

    def test_no_data_without_fail_is_no_data(self):
        results = [AC.Step("a", AC.PASS, ""), AC.Step("c", AC.NODATA, "")]
        self.assertEqual(AC._verdict_for(results), AC.NODATA)

    def test_all_pass_is_pass(self):
        self.assertEqual(AC._verdict_for([AC.Step("a", AC.PASS, "")]), AC.PASS)

    def test_summary_line_shape(self):
        line = AC._summary_line("codex", [AC.Step("a", AC.PASS, ""), AC.Step("b", AC.NODATA, "")])
        self.assertEqual(line, "provider=codex pass=1 fail=0 no-data=1 verdict=NO-DATA")


class DurableReceipt(unittest.TestCase):
    def test_private_tmp_is_under_temp(self):
        self.assertTrue(AC._under_temp("/private/tmp/brother-add-x/receipt.json"))
        self.assertTrue(AC._under_temp(os.path.join(tempfile.gettempdir(), "receipt.json")))

    def test_codex_home_is_durable(self):
        self.assertFalse(AC._under_temp(os.path.expanduser("~/.codex/brother/runs/receipt.json")))


class CortexShortCircuit(unittest.TestCase):
    def test_every_step_no_data_and_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = AC.run_conformance("cortex", tmp, offline=True)
        self.assertEqual(len(results), len(AC.STEP_ORDER))
        self.assertTrue(all(r.verdict == AC.NODATA for r in results))
        self.assertEqual(AC._verdict_for(results), AC.NODATA)

    def test_fake_binary_still_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "cortex")
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\necho cortex 0.0.1\n")
            os.chmod(fake, 0o755)
            os.environ["BROTHER_CORTEX_BIN"] = fake
            try:
                results = AC.run_conformance("cortex", tmp, offline=True)
            finally:
                del os.environ["BROTHER_CORTEX_BIN"]
        self.assertTrue(all(r.verdict == AC.NODATA for r in results))


class DoneCheckIsOnePlainCommand(unittest.TestCase):
    def test_no_command_separators(self):
        for sep in (";", "&&", "||", "|"):
            self.assertNotIn(sep, AC.DONE_CHECK)


class Refusal(unittest.TestCase):
    def test_lifecycle_verb_no_data_on_refusal(self):
        ctx = {"adapter": PA.CortexAdapter(env={}), "provider": "cortex", "offline": True}
        verdict, reason = AC._lifecycle_verb(ctx, "install")
        self.assertEqual(verdict, AC.NODATA)
        self.assertIn("untested", reason)




class LifecycleRefs(unittest.TestCase):
    def test_previous_tag_is_the_newest_older_v_tag(self):
        import subprocess as sp
        real = sp.run
        def fake(argv, **kw):
            class P: returncode = 0; stdout = "v1.0.10\nv1.0.8\nv1.0.9\nv0.9.6\njunk\n"
            return P()
        sp.run = fake
        try:
            self.assertEqual(AC._previous_tag("v1.0.9"), "v1.0.8")
            self.assertEqual(AC._previous_tag("v1.0.11"), "v1.0.10")
            self.assertIsNone(AC._previous_tag("v0.9.6"))
        finally:
            sp.run = real

    def test_unknown_tags_read_no_data_never_pass(self):
        ctx = AC.Context(ref=None, from_ref=None)
        real = AC._umbrella_tag
        AC._umbrella_tag = lambda root=None: None
        try:
            self.assertIn("could not be read", AC._lifecycle_refs(ctx))
        finally:
            AC._umbrella_tag = real
        ctx2 = AC.Context(ref="v1.0.9", from_ref="v1.0.8")
        self.assertEqual(AC._lifecycle_refs(ctx2), {"previous": "v1.0.8", "current": "v1.0.9"})

if __name__ == "__main__":
    unittest.main()
