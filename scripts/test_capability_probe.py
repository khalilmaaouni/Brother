#!/usr/bin/env python3
"""test_capability_probe.py: capability_probe.py driven backwards, because
the defect it exists to fix was checking ONE path and declaring a live
capability retired (see capability_probe.py's own WHY docstring: a working
`codex` binary sat at ~/.local/bin/codex the entire time a session reported
the capability gone). Both survey() and compare_inventory() take injectable
inputs, so a fixture never touches this machine's real binaries or the
founder's real vault inventory file.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capability_probe as CP  # noqa: E402

# E100: one sandbox for every temp tree this process makes, removed at exit.
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


class ProbeTriesEveryAlternativeBeforeMissing(unittest.TestCase):
    """The exact defect this module exists to fix, driven backwards: a
    capability reachable only by its SECOND alternative must read PRESENT,
    never MISSING. The original failure stopped at the first path."""

    def test_a_capability_reachable_only_by_its_second_alternative_is_present(self):
        real_file = tempfile.NamedTemporaryFile(delete=False)
        real_file.close()
        self.addCleanup(os.unlink, real_file.name)
        capability = {
            "name": "test-only-capability",
            "why": "test fixture: reachable only by its second alternative",
            "alternatives": [
                {"kind": "binary", "probe": "definitely-not-a-real-binary-xyz"},
                {"kind": "file", "probe": real_file.name},
            ],
        }
        results = CP.survey(capabilities=[capability])
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result["state"], CP.PRESENT,
                         "the first alternative alone was checked, which is "
                         "the original defect this test exists to catch: %r"
                         % result)
        self.assertEqual(result["reached_by"], real_file.name)
        # both alternatives were actually tried, not just the winning one
        tried_probes = [t["probe"] for t in result["tried"]]
        self.assertEqual(tried_probes,
                         ["definitely-not-a-real-binary-xyz", real_file.name])
        self.assertEqual(result["tried"][0]["state"], CP.MISSING)
        self.assertEqual(result["tried"][1]["state"], CP.PRESENT)


class InventoryComparison(unittest.TestCase):
    """A hand maintained inventory does not fail loudly, it quietly stops
    being true (capability_probe.py's own WHY). compare_inventory is the
    only mechanism that can say so; both directions are proven here."""

    def test_an_inventory_that_names_nothing_reports_the_live_capability_as_undocumented(self):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        inventory_path = os.path.join(tmpdir, "installed-tools.md")
        with open(inventory_path, "w", encoding="utf-8") as fh:
            fh.write("# Installed tools\n\nNothing about anything here.\n")

        results = [{"name": "present-capability", "why": "test fixture",
                   "state": CP.PRESENT, "reached_by": "/some/real/path",
                   "tried": [{"probe": "/some/real/path", "state": CP.PRESENT,
                             "detail": "/some/real/path"}]}]
        undocumented = CP.compare_inventory(results, inventory_path)
        self.assertEqual(undocumented, ["present-capability"])

    def test_an_inventory_that_names_the_capability_reports_nothing(self):
        # the other side of the same test: a document that DOES mention the
        # live capability's name is not flagged, so the check only fires on
        # the actual gap rather than on every capability unconditionally.
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmpdir, ignore_errors=True))
        inventory_path = os.path.join(tmpdir, "installed-tools.md")
        with open(inventory_path, "w", encoding="utf-8") as fh:
            fh.write("# Installed tools\n\npresent-capability: yes\n")

        results = [{"name": "present-capability", "why": "test fixture",
                   "state": CP.PRESENT, "reached_by": "/some/real/path",
                   "tried": [{"probe": "/some/real/path", "state": CP.PRESENT,
                             "detail": "/some/real/path"}]}]
        undocumented = CP.compare_inventory(results, inventory_path)
        self.assertEqual(undocumented, [])

    def test_an_unreadable_inventory_is_none_not_a_crash(self):
        undocumented = CP.compare_inventory([], "/no/such/path/xyz-missing")
        self.assertIsNone(undocumented)


if __name__ == "__main__":
    unittest.main(verbosity=2)
