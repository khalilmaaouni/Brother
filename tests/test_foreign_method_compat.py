"""SR-08: Brother must not alter a foreign method's artifacts.

THE PROPERTY, AND WHY IT IS NOT DECORATION. The north star chain says the team
uses whichever development method it already has, INTERCHANGEABLE AND NEVER
REQUIRED, and Brother wraps trust around that method rather than replacing it.
The bootstrap skill says the same in its own words: if a method is already
active, that method OWNS the planning. Both of those are PROMISES, and a promise
with no file behind it is advice.

So this asserts the mechanical half of it: run Brother's own read-only checks
over a tree containing another method's artifacts, and those artifacts must be
BYTE IDENTICAL afterwards. Provenance about them may be captured; the artifacts
themselves may not move.

WHAT IT DOES NOT PROVE, stated rather than discovered later. It covers the tools
this repository ships TODAY, at Stage 0, where no product code has moved here.
It says nothing about what BrotherMode or BrotherSBE do inside their own
repositories, and it cannot: those are separate trees with separate suites. When
Stage 1 moves code here, this fixture is where the new writers get added, and a
writer that is not listed below is a writer nobody checked.

It also cannot prove a tool leaves a file alone in every circumstance, only over
this fixture. A tool that mutates only on some input this fixture does not carry
would pass here.
"""
import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))

REPO = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "foreign-method"

# Every check this repository ships that a session would plausibly run over a
# working tree. Read-only by intent; this test is what holds them to it.
READ_ONLY_TOOLS = [
    ["python3", "scripts/coverage_check.py"],
    ["python3", "scripts/leaf_pin_check.py"],
    ["python3", "scripts/authority_path_coverage.py"],
    ["python3", "scripts/probe_repeat_guard_classification.py"],
    ["bash", "scripts/cleanse.sh"],
]


def digest(root):
    """Path plus content hash for every file under root, order-stable."""
    out = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(root))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


class TestForeignMethodArtifactsAreNotTouched(unittest.TestCase):

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.work = self.tmp / "tree"
        shutil.copytree(FIXTURE, self.work)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fixture_is_not_empty(self):
        """A run over nothing would pass vacuously, which is not a pass."""
        before = digest(self.work)
        self.assertGreaterEqual(
            len(before), 3,
            "the foreign-method fixture is empty or missing, so this suite would "
            "prove nothing by passing. NO-DATA is not a pass.",
        )

    def test_every_shipped_read_only_tool_leaves_them_byte_identical(self):
        before = digest(self.work)
        ran, skipped = [], []
        for tool in READ_ONLY_TOOLS:
            script = REPO / tool[1]
            if not script.exists():
                skipped.append(tool[1])
                continue
            result = subprocess.run(
                tool + [str(self.work)],
                cwd=str(REPO), capture_output=True, text=True, timeout=120,
            )
            # The exit code is not asserted here: these tools report PASS,
            # FAIL and NO-DATA on different codes and this suite is not the
            # place to fix that mapping. But a tool that CRASHED before it
            # ever ran its check would also leave the fixture untouched, and
            # a discarded result used to let that pass this test for the
            # wrong reason (it never ran, rather than it ran and behaved).
            # A traceback in stderr is the tell for that, named here instead.
            self.assertNotIn(
                "Traceback (most recent call last)", result.stderr,
                "%s crashed (exit %d) instead of running its check, which "
                "leaves the fixture untouched for the wrong reason:\n%s"
                % (tool[1], result.returncode, result.stderr),
            )
            ran.append(tool[1])
        self.assertTrue(
            ran,
            "no shipped tool was actually run, so this proved nothing. Skipped: %s" % skipped,
        )
        after = digest(self.work)
        changed = sorted(k for k in before if before.get(k) != after.get(k))
        vanished = sorted(set(before) - set(after))
        appeared = sorted(set(after) - set(before))
        self.assertEqual(
            (changed, vanished, appeared), ([], [], []),
            "Brother altered another method's artifacts after running %s.\n"
            "  changed:  %s\n  vanished: %s\n  appeared: %s\n"
            "The chain promises a team's own method is interchangeable and never "
            "required. A tool that rewrites its files breaks that promise."
            % (", ".join(ran), changed, vanished, appeared),
        )

    def test_the_detector_itself_can_fail(self):
        """A check that cannot fail verifies nothing.

        This does NOT test Brother. It tests that the comparison above would
        actually notice a mutation, by making one deliberately.
        """
        before = digest(self.work)
        (self.work / "PLAN.md").write_text("mutated by a hostile tool\n", encoding="utf-8")
        after = digest(self.work)
        changed = sorted(k for k in before if before.get(k) != after.get(k))
        self.assertEqual(
            changed, ["PLAN.md"],
            "the comparison did not notice a deliberate mutation, so a real one "
            "would pass unnoticed too",
        )


if __name__ == "__main__":
    unittest.main()
