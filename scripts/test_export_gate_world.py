#!/usr/bin/env python3
"""T1.3 (docs/plan/specs/T1.md, RQ-06 and RQ-07): every export gate runs in
the candidate tree, never at the hub root, and a gate whose world cannot be
built refuses at NO-DATA, exit 2, never a PASS.

The gates themselves are stood in for (run_gate and _run are patched to
record where they would have launched), so this suite proves WHERE a gate
runs and WHAT a refusal says without paying for, or depending on, the real
cleanse.sh and its siblings. Every temp tree, HOME included, is this
suite's own and is removed after each test.

Run: python3 -B scripts/test_export_gate_world.py
"""
import contextlib
import io
import math
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public as EP  # noqa: E402

#: A candidate tree with every gate's program present: cleanse.sh, a Codex
#: surface (so client_parity runs), one product battery, and the three
#: scripts the tag-time checks launch. Contents are inert stand-ins.
CANDIDATE_FILES = {
    "scripts/cleanse.sh": "exit 0\n",
    "scripts/required_fast.sh": "echo 'pass 1 fail 0 no-data 0'\n",
    "scripts/readiness_gate.py": "print('GATE: READY')\n",
    "scripts/test_fixture_prove.py": "print('ok')\n",
    "README.md": "Prove it with `python3 scripts/test_fixture_prove.py`.\n",
    "docs/codex/HOOKS-MAPPING.md": "stand-in\n",
    "products/fakeprod/tools/test_all.py": "print('inventory ok')\n",
    "products/fakeprod/scripts/verify-install.sh": "echo ok\n",
}

EXPORT_GATES = {"cleanse", "private_terms_scan", "client_parity",
                "battery_inventory products/fakeprod"}


def _write(root, files):
    for rel, text in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


def _inside(path, root):
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


class _Proc(object):
    """What _run returns: enough output for every tag-time check to pass."""

    def __init__(self):
        self.returncode = 0
        self.stdout = ("GATE: READY\npass 1 fail 0 no-data 0\n"
                       "verify-install: 1 file(s) match\n")
        self.stderr = ""


class _WorldCase(unittest.TestCase):
    """A fresh HOME, a candidate tree, an identity tree, and recorders for
    every launch, all removed after the test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gate-world-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = mock.patch.dict(os.environ, {"HOME": home})
        env.start()
        self.addCleanup(env.stop)
        self.tree = os.path.join(self.tmp, "candidate")
        _write(self.tree, CANDIDATE_FILES)
        self.identity = os.path.join(self.tmp, "identity")
        os.makedirs(self.identity)
        self.hub = os.path.realpath(EP.ROOT)

    def gates(self, export_dir, identity_dir):
        """run_gates with run_gate recording (name, cmd, cwd) instead of
        launching; the shadow seam is switched off so nothing is written
        outside this test. Returns (all_ok, lines, calls)."""
        calls = []

        def fake_gate(cmd, cwd, name, env=None, timeout=None, load15=None):
            calls.append((name, list(cmd), cwd))
            return True, "%s: exit 0, stub" % name

        with mock.patch.object(EP, "run_gate", fake_gate), \
                mock.patch.object(EP, "jev_checks", None):
            all_ok, lines = EP.run_gates(export_dir, identity_dir)
        return all_ok, lines, calls

    def launches(self, check, *args):
        """`check(*args)` with _run recording (cmd, cwd). Returns
        (result, calls)."""
        calls = []

        def fake_run(cmd, cwd, env=None, timeout=120):
            calls.append((list(cmd), cwd))
            return _Proc()

        with mock.patch.object(EP, "_run", fake_run):
            result = check(*args)
        return result, calls


class GateWorld(_WorldCase):
    """RQ-06: every export gate subprocess runs with its working directory
    inside the candidate export tree, never at the hub root."""

    def test_the_gate_never_runs_at_the_hub_root(self):
        # The hub itself, and anything holding it, is never a gate's world.
        for root in (EP.ROOT, os.path.dirname(EP.ROOT), os.sep):
            with self.assertRaises(ValueError):
                EP.gate_world(root, "cleanse")
        # Refused for BEING the hub root, not only for carrying editions/:
        # a hub directory with no boundary marker at all is still refused.
        bare_hub = os.path.join(self.tmp, "barehub")
        os.makedirs(bare_hub)
        with mock.patch.object(EP, "ROOT", bare_hub):
            with self.assertRaises(ValueError):
                EP.gate_world(bare_hub, "cleanse")
            with self.assertRaises(ValueError):
                EP.gate_world(self.tmp, "cleanse")
            self.assertEqual(EP.gate_world(self.tree, "cleanse")["cwd"],
                             os.path.realpath(self.tree))
        # A real run over a real candidate tree launches nothing at the hub.
        all_ok, lines, calls = self.gates(self.tree, self.identity)
        self.assertTrue(calls, lines)
        for name, cmd, cwd in calls:
            self.assertNotEqual(os.path.realpath(cwd), self.hub,
                                "%s was launched at the hub root" % name)
        # Handed the hub root as its candidate tree, it launches nothing.
        all_ok, lines, calls = self.gates(EP.ROOT, self.identity)
        self.assertEqual(calls, [], lines)
        self.assertFalse(all_ok, lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("exit 2, NO-DATA", lines[0])
        # The tag-time gates refuse the hub too, before launching anything.
        for check in (EP.check_required_fast, EP.check_readiness_gate,
                      EP.check_readme_prove_commands):
            (ok, out), runs = self.launches(check, EP.ROOT)
            self.assertFalse(ok, out)
            self.assertEqual(runs, [], "%s launched at the hub root: %r"
                             % (check.__name__, runs))
            self.assertIn("NO-DATA", out[0])

    def test_cwd_is_inside_the_candidate_tree(self):
        tree_real = os.path.realpath(self.tree)
        self.assertEqual(EP.gate_world(self.tree, "cleanse"),
                         {"gate": "cleanse", "tree_root": tree_real,
                          "cwd": tree_real})
        # Reached through a symlink, the world is the resolved tree: the
        # directory checked is the directory launched in.
        link = os.path.join(self.tmp, "link")
        os.symlink(self.tree, link)
        self.assertEqual(EP.gate_world(link, "cleanse")["cwd"], tree_real)

        all_ok, lines, calls = self.gates(self.tree, self.identity)
        by_name = dict((name, cwd) for name, cmd, cwd in calls)
        self.assertEqual(set(by_name), EXPORT_GATES | {"identity_guard"},
                         lines)
        for name in EXPORT_GATES:
            self.assertTrue(_inside(by_name[name], self.tree),
                            "%s ran at %s, outside the candidate tree %s"
                            % (name, by_name[name], self.tree))
        self.assertEqual(by_name["battery_inventory products/fakeprod"],
                         os.path.join(tree_real, "products", "fakeprod"))
        self.assertEqual(by_name["identity_guard"],
                         os.path.realpath(self.identity))

        # The tag-time gates launch in the same world.
        for check in (EP.check_required_fast, EP.check_readiness_gate,
                      EP.check_readme_prove_commands):
            (ok, out), runs = self.launches(check, self.tree)
            self.assertTrue(ok, out)
            self.assertTrue(runs, check.__name__)
            for cmd, cwd in runs:
                self.assertEqual(cwd, tree_real, (check.__name__, cmd))
        (ok, out), runs = self.launches(EP.tag_time_checks, self.tree,
                                        "9.9.9")
        verifier = [cwd for cmd, cwd in runs if cmd[0] == "bash"]
        self.assertEqual(verifier,
                         [os.path.join(tree_real, "products", "fakeprod")],
                         runs)

    def test_a_second_hub_checkout_is_refused_by_its_boundary(self):
        """A directory carrying the hub's own boundary (editions/ or
        .brother-edition, which an export never carries) is a hub, another
        checkout of it included, and never a candidate tree."""
        for boundary in sorted(EP.HARD_EXCLUDE):
            other_hub = os.path.join(self.tmp, "otherhub-%d" % len(boundary))
            os.makedirs(os.path.join(other_hub, boundary))
            with self.assertRaises(ValueError) as ctx:
                EP.gate_world(other_hub, "cleanse")
            self.assertIn(boundary, str(ctx.exception))


class GateRefusal(_WorldCase):
    """RQ-07: a gate whose candidate tree cannot be built refuses at
    NO-DATA and exit 2, never a PASS."""

    def test_a_missing_clone_refuses_at_exit_two(self):
        missing = os.path.join(self.tmp, "never-cloned")
        with self.assertRaises(ValueError) as ctx:
            EP.gate_world(missing, "cleanse")
        self.assertIn("does not exist", str(ctx.exception))

        code, line = EP.gate_refusal("cleanse", str(ctx.exception))
        self.assertEqual(code, 2)
        self.assertEqual(code, EP.EXIT_NODATA)
        self.assertTrue(line.startswith("cleanse: exit 2, NO-DATA: "), line)
        self.assertIn(missing, line)
        self.assertNotIn("PASS", line)

        # The exporter returns that refusal INSTEAD of a verdict: no gate
        # launched, no in-process check run, one NO-DATA line, not ok.
        all_ok, lines, calls = self.gates(missing, self.identity)
        self.assertFalse(all_ok, lines)
        self.assertEqual(calls, [], lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("exit 2, NO-DATA", lines[0])
        self.assertIn("does not exist", lines[0])
        self.assertFalse([l for l in lines if "PASS" in l], lines)

        # A missing identity tree refuses its own gate the same way.
        all_ok, lines, calls = self.gates(self.tree, missing)
        self.assertFalse(all_ok, lines)
        identity = [l for l in lines if l.startswith("identity_guard:")]
        self.assertEqual(len(identity), 1, lines)
        self.assertIn("exit 2, NO-DATA", identity[0])
        self.assertNotIn("identity_guard", [c[0] for c in calls])

    def test_a_clean_refusal_is_not_a_pass(self):
        code, line = EP.gate_refusal("cleanse", "the tree is gone")
        self.assertNotEqual(code, EP.EXIT_OK)
        self.assertEqual(code, EP.EXIT_NODATA)
        self.assertEqual(line, "cleanse: exit 2, NO-DATA: the tree is gone")
        self.assertEqual(len(line.splitlines()), 1)
        self.assertNotIn("PASS", line)
        # A reason carrying line breaks is still exactly one line, so no
        # second line of it can be read as a verdict of its own.
        code, line = EP.gate_refusal("cleanse", "gone\nPASS: all clear\r\n")
        self.assertEqual(code, EP.EXIT_NODATA)
        self.assertEqual(len(line.splitlines()), 1, line)
        self.assertTrue(line.startswith("cleanse: exit 2, NO-DATA: "), line)
        # A name that is a verdict word never opens the line.
        code, line = EP.gate_refusal("PASS", "x")
        self.assertEqual(code, EP.EXIT_NODATA)
        self.assertFalse(line.startswith("PASS"), line)
        self.assertIn("NO-DATA", line)
        # In run_gates one refused gate refuses the whole export, however
        # clean every other gate reads.
        os.remove(os.path.join(self.tree, "scripts", "cleanse.sh"))
        all_ok, lines, calls = self.gates(self.tree, self.identity)
        self.assertFalse(all_ok, lines)
        cleanse = [l for l in lines if l.startswith("cleanse:")]
        self.assertEqual(len(cleanse), 1, lines)
        self.assertIn("exit 2, NO-DATA", cleanse[0])
        self.assertNotIn("PASS", cleanse[0])

    def test_a_missing_gate_binary_refuses_and_never_crashes(self):
        # The gate's script is not in its world: refused, never launched.
        os.remove(os.path.join(self.tree, "scripts", "cleanse.sh"))
        all_ok, lines, calls = self.gates(self.tree, self.identity)
        self.assertFalse(all_ok, lines)
        self.assertNotIn("cleanse", [c[0] for c in calls])
        _write(self.tree, {"scripts/cleanse.sh": "exit 0\n"})
        # The interpreter the python gates run under is gone.
        gone = os.path.join(self.tmp, "no-such-python")
        with mock.patch.object(sys, "executable", gone):
            all_ok, lines, calls = self.gates(self.tree, self.identity)
        self.assertFalse(all_ok, lines)
        self.assertEqual([c[0] for c in calls], ["cleanse"], lines)
        for name in ("identity_guard", "private_terms_scan", "client_parity",
                     "battery_inventory products/fakeprod"):
            line = [l for l in lines if l.startswith(name + ":")]
            self.assertEqual(len(line), 1, (name, lines))
            self.assertIn("exit 2, NO-DATA", line[0])
            self.assertIn(gone, line[0])
        # No binary on PATH at all: bash is gone too, still no crash.
        empty_path = os.path.join(self.tmp, "empty-path")
        os.makedirs(empty_path)
        with mock.patch.dict(os.environ, {"PATH": empty_path}):
            all_ok, lines, calls = self.gates(self.tree, self.identity)
        self.assertFalse(all_ok, lines)
        self.assertNotIn("cleanse", [c[0] for c in calls])


class HalfBuiltWorld(_WorldCase):
    """The race, stale and already-ran edges: a tree still marked as being
    built is never read, the loser of a claim refuses, and nothing about a
    world is remembered between calls."""

    def test_a_racing_export_never_reads_a_half_built_tree(self):
        marker = EP._claim_world(self.tree, "candidate_tree")  # export A
        self.assertTrue(os.path.isdir(marker))
        # Export B, racing over the same directory, reads nothing of it.
        all_ok, lines, calls = self.gates(self.tree, self.identity)
        self.assertFalse(all_ok, lines)
        self.assertEqual(calls, [], lines)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("exit 2, NO-DATA", lines[0])
        self.assertIn(EP.GATE_WORLD_MARKER, lines[0])
        # And cannot claim it either: the loser refuses.
        with self.assertRaises(ValueError):
            EP._claim_world(self.tree, "candidate_tree")
        # Once A's build is whole, the same tree is gated normally.
        EP._release_world(marker)
        all_ok, lines, calls = self.gates(self.tree, self.identity)
        self.assertEqual(set(c[0] for c in calls),
                         EXPORT_GATES | {"identity_guard"}, lines)

    def test_the_loser_of_a_claim_race_refuses(self):
        """Both exports pass the check, then the other one creates the
        marker first: the exclusive create, not the earlier look, decides."""
        real_gate_world = EP.gate_world

        def winner_claims_in_between(tree_root, gate_name):
            world = real_gate_world(tree_root, gate_name)
            os.mkdir(os.path.join(world["tree_root"], EP.GATE_WORLD_MARKER))
            return world

        with mock.patch.object(EP, "gate_world", winner_claims_in_between):
            with self.assertRaises(ValueError) as ctx:
                EP._claim_world(self.tree, "candidate_tree")
        self.assertIn("another export", str(ctx.exception))

    def test_a_stale_tree_from_a_crashed_run_is_refused_by_its_marker(self):
        # A crashed run claimed the tree and never released it.
        os.mkdir(os.path.join(self.tree, EP.GATE_WORLD_MARKER))
        for where in (self.tree,
                      os.path.join(self.tree, "products", "fakeprod")):
            with self.assertRaises(ValueError) as ctx:
                EP.gate_world(where, "cleanse")
            self.assertIn(EP.GATE_WORLD_MARKER, str(ctx.exception))
        for check in (EP.check_required_fast, EP.check_readiness_gate,
                      EP.check_readme_prove_commands):
            (ok, out), runs = self.launches(check, self.tree)
            self.assertFalse(ok, out)
            self.assertEqual(runs, [], check.__name__)
            self.assertIn("exit 2, NO-DATA", out[0])
        (ok, out), runs = self.launches(EP.tag_time_checks, self.tree,
                                        "9.9.9")
        self.assertFalse(ok, out)
        self.assertNotIn("bash", [cmd[0] for cmd, cwd in runs], runs)
        self.assertTrue([l for l in out if l.startswith(
            "verify_install products/fakeprod: exit 2, NO-DATA")], out)

    def test_a_gate_that_already_ran_is_run_again(self):
        first = self.gates(self.tree, self.identity)[2]
        second = self.gates(self.tree, self.identity)[2]
        self.assertEqual(sorted(c[0] for c in second),
                         sorted(c[0] for c in first))
        self.assertTrue(second)
        # The world is read again, never remembered: gone now, refused now.
        self.assertEqual(EP.gate_world(self.tree, "cleanse")["cwd"],
                         os.path.realpath(self.tree))
        shutil.rmtree(self.tree)
        with self.assertRaises(ValueError):
            EP.gate_world(self.tree, "cleanse")

    def test_the_marker_never_rides_into_the_gated_commit(self):
        """git never tracks an empty directory, so the marker held during
        the build is not in the orphan commit the gates scan."""
        root = os.path.join(self.tmp, "fakehub")
        _write(root, {"public.md": "nothing private here\n"})
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"]):
            proc = EP._run(cmd, root)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        export_dir = os.path.join(self.tmp, "export")
        os.makedirs(export_dir)
        marker = EP._claim_world(export_dir, "candidate_tree")
        copied, committed = EP.build_orphan_commit(export_dir, ["public.md"],
                                                   root=root)
        self.assertTrue(committed, copied)
        tracked = EP._run(["git", "ls-files"], export_dir).stdout.split()
        self.assertEqual(tracked, ["public.md"])
        status = EP._run(["git", "status", "--porcelain"], export_dir)
        self.assertEqual(status.stdout.strip(), "")
        EP._release_world(marker)
        self.assertFalse(os.path.lexists(marker))
        self.assertEqual(EP.gate_world(export_dir, "cleanse")["cwd"],
                         os.path.realpath(export_dir))


class MainMarksItsWorlds(_WorldCase):
    """main() holds both gate worlds marked while it builds them and
    releases them before a single gate reads them; a world it cannot claim
    refuses at NO-DATA before anything is built."""

    def _main(self, **patches):
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                EP, "load_allowlist", return_value=["public.md"]))
            stack.enter_context(mock.patch.object(
                EP, "build_baseline_dir", return_value=False))
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(EP, name, value))
            stack.enter_context(contextlib.redirect_stdout(out))
            code = EP.main(["--dry-run"])
        return code, out.getvalue()

    def test_both_worlds_are_marked_while_built_and_released_before_the_gates(
            self):
        seen = {}

        def marked(tree):
            return os.path.lexists(os.path.join(tree, EP.GATE_WORLD_MARKER))

        def fake_orphan(export_dir, allowlist, root=EP.ROOT):
            seen["export built"] = marked(export_dir)
            return ["public.md"], True

        def fake_identity(identity_dir, allowlist, remote, branch,
                          root=EP.ROOT):
            seen["identity built"] = marked(identity_dir)

        def fake_gates(export_dir, identity_dir, baseline_dir=None):
            seen["export gated"] = marked(export_dir)
            seen["identity gated"] = marked(identity_dir)
            EP.gate_world(export_dir, "cleanse")
            EP.gate_world(identity_dir, "identity_guard")
            return False, ["cleanse: exit 1, stub"]

        code, out = self._main(build_orphan_commit=fake_orphan,
                               build_identity_check_dir=fake_identity,
                               run_gates=fake_gates)
        self.assertEqual(code, EP.EXIT_REFUSED, out)
        self.assertEqual(seen, {"export built": True, "identity built": True,
                                "export gated": False,
                                "identity gated": False}, out)

    def test_a_world_that_cannot_be_claimed_refuses_before_any_build(self):
        build = mock.Mock(return_value=(["public.md"], True))
        claim = mock.Mock(side_effect=ValueError(
            "candidate_tree: claimed by another export first"))
        code, out = self._main(_claim_world=claim, build_orphan_commit=build)
        self.assertEqual(code, EP.EXIT_NODATA, out)
        build.assert_not_called()
        self.assertIn("candidate_tree: exit 2, NO-DATA: candidate_tree: "
                      "claimed by another export first", out)
        self.assertNotIn("CLEAR", out)


class HostileInput(_WorldCase):
    """Wrong types, unhashable values, None, NaN, bools, bytes, relative
    and empty paths: gate_world refuses each with ValueError (never a
    TypeError, never a world), and gate_refusal still refuses at NO-DATA."""

    BAD_TREES = (None, 0, 1, True, False, math.nan, 1.5, [], ["/tmp"], {},
                 {"/tmp": 1}, set(), b"/tmp", bytearray(b"/tmp"), object(),
                 "", "   ", "relative/tree", ".", "/tmp/\x00x")
    BAD_NAMES = (None, 0, True, math.nan, [], ["cleanse"], {"a": 1}, set(),
                 b"cleanse", "", " ", "PASS", "NO-DATA", "Cleanse",
                 "cleanse\n", "cleanse\nPASS", "cleanse\x00", "1cleanse",
                 "cleanse \u2028PASS")

    def test_hostile_input_is_refused_never_a_type_error(self):
        for bad in self.BAD_TREES:
            with self.assertRaises(ValueError, msg=repr(bad)):
                EP.gate_world(bad, "cleanse")
            with self.assertRaises(ValueError, msg=repr(bad)):
                EP._claim_world(bad, "candidate_tree")
            all_ok, lines, calls = self.gates(bad, self.identity)
            self.assertFalse(all_ok, (bad, lines))
            self.assertEqual(calls, [], (bad, lines))
        for bad in self.BAD_NAMES:
            with self.assertRaises(ValueError, msg=repr(bad)):
                EP.gate_world(self.tree, bad)
        for name in self.BAD_NAMES + ("cleanse",):
            for reason in self.BAD_TREES + ("gone",):
                code, line = EP.gate_refusal(name, reason)
                self.assertEqual(code, EP.EXIT_NODATA, (name, reason))
                self.assertIsInstance(line, str)
                self.assertEqual(len(line.splitlines()), 1, line)
                self.assertIn("exit 2, NO-DATA: ", line)
                self.assertFalse(line.startswith(("PASS", "NO-DATA")), line)


if __name__ == "__main__":
    unittest.main()
