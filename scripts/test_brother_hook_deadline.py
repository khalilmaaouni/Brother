#!/usr/bin/env python3
"""brother_hook.py deadlines: deny rows first, a deadline on every in process row, a budget for the event.

The frozen harness (test_brother_hook.py) is imported for its throwaway bundle helpers and never edited. Every case
RUNS the dispatcher as a subprocess against a generated bundle whose handlers are small real hook scripts, and judges
the exit code, the output and the process ids the handlers logged. A row's `timeout` is the one hooks.json carries;
the event's budget is 0.8 of the largest of them (the host's own timeout on the one command that replaces them).

    python3 -B scripts/test_brother_hook_deadline.py
"""
import json
import os
import shutil
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bundle_runtime as BR  # noqa: E402
import test_brother_hook as H  # noqa: E402

CORPUS = H.CORPUS
# A handler body that hangs (6 s is long enough to miss every deadline here and short enough to end a red run),
# and one that hangs only while it is being run inside the dispatcher.
H.BODIES["sleep"] = ("", "    import time\n    time.sleep(6)\n")
H.BODIES["slow_inside"] = ("", "    if _inside_dispatcher():\n        import time\n        time.sleep(6)\n" + H._DENY_JSON)


class Deadlines(H.Hermetic):
    def bundle(self, handlers):
        """handlers: [(name, class, kind, timeout)] authored in one product, in order."""
        self._n += 1
        root = os.path.join(self.tmp, "b%d" % self._n)
        product = os.path.join(root, "products", "hd1prod")
        entries = []
        for name, cls, kind, timeout in handlers:
            H.write_text(os.path.join(product, "tools", name + ".py"), H.handler_source(name, kind))
            entries.append({"type": "command", "class": cls, "timeout": timeout,
                            "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/tools/%s.py"' % name})
        H.write_text(os.path.join(product, "hooks", "hooks.json"), json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": entries}]}}))
        bundle = os.path.join(root, "bundle")
        H.write_text(os.path.join(bundle, ".claude-plugin", "plugin.json"), json.dumps({"name": "brother", "version": "1.1.0"}))
        BR.generate_hooks(products=("hd1prod",), products_dir=os.path.join(root, "products"),
                          runtime_dir=os.path.join(bundle, "runtime"))
        return H.Bundle(bundle, os.path.join(bundle, "runtime", "hooks", "brother_hook.py"))

    def timed(self, b, command, **kw):
        start = time.time()
        rc, out, err = self.run_event(b, "PreToolUse", command, **kw)
        return rc, out, err, time.time() - start

    def test_a_hanging_advisory_beside_a_deny_still_answers_deny_inside_the_budget(self):
        b = self.bundle([("adv_hang", "advisory", "sleep", 2), ("guard", "deny", "deny_json", 4)])
        rc, out, err, wall = self.timed(b, CORPUS)
        self.assertTrue(H.is_denied(rc, out), "the deny was lost: rc=%s out=%r err=%r" % (rc, out[-200:], err[-200:]))
        self.assertLess(wall, 3.2, "the event outlived its budget (0.8 of 4 s): %.1f s" % wall)
        self.assertIn("adv_hang", [t for t, _ in self.pids()], "the advisory row never ran, so nothing was cut")

    def test_deny_rows_run_before_advisory_rows(self):
        b = self.bundle([("adv_a", "advisory", "allow", 5), ("guard_b", "deny", "allow", 5)])
        rc, out, err, _ = self.timed(b, H.BENIGN)
        self.assertEqual(0, rc, err)
        self.assertEqual(["guard_b", "adv_a"], [t for t, _ in self.pids()])

    def test_a_deny_row_that_eats_the_budget_exits_two_naming_it(self):
        b = self.bundle([("hang_guard", "deny", "sleep", 3), ("second_guard", "deny", "deny_json", 3)])
        rc, out, err, wall = self.timed(b, CORPUS)
        self.assertEqual(2, rc, "rc=%s out=%r err=%r" % (rc, out[-200:], err[-200:]))
        self.assertIn("hang_guard", err)
        self.assertIn("before every deny guard had answered", err)   # the budget's own refusal, not only the row's raise
        self.assertNotIn("second_guard", [t for t, _ in self.pids()], "a row ran after the budget was gone")
        self.assertLess(wall, 3.2, "%.1f s" % wall)

    def test_a_deny_row_past_its_deadline_is_run_again_as_its_own_process(self):
        b = self.bundle([("slow_guard", "deny", "slow_inside", 1), ("pad", "advisory", "allow", 10)])
        rc, out, err, wall = self.timed(b, CORPUS)
        self.assertTrue(H.is_denied(rc, out) and "exfil.upload" in out, "rc=%s out=%r err=%r" % (rc, out[-200:], err[-200:]))
        self.assertLess(wall, 4.0, "%.1f s" % wall)
        self.assertEqual(2, len(set(p for t, p in self.pids() if t == "slow_guard")), "not run in a second process")

    def test_a_benign_call_is_untouched_by_the_deadlines(self):
        b = self.bundle([("g", "deny", "allow", 2), ("a", "advisory", "allow", 2)])
        rc, out, err, wall = self.timed(b, H.BENIGN)
        self.assertEqual((0, False), (rc, H.is_denied(rc, out)), err)
        self.assertEqual(1, len(set(p for _, p in self.pids())), "the rows should share one process")

    def test_the_spawn_path_is_held_to_the_row_deadline_too(self):
        b = self.bundle([("hang_guard", "deny", "sleep", 2)])
        rc, out, err, wall = self.timed(b, CORPUS, extra_env={"BROTHER_HOOK_DISPATCH": "spawn"})
        self.assertEqual(2, rc, err)
        self.assertLess(wall, 3.5, "%.1f s" % wall)


if __name__ == "__main__":
    unittest.main()
