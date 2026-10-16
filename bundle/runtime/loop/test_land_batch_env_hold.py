#!/usr/bin/env python3
"""G14: a landing red the build did not cause holds the build, with no diagnosis note, no repeat count and no park.

WHAT WAS WRONG (G10 builder report, 2026-10-16): land_apply.py ends a red that is red on the unchanged copy too with
VERDICT BLOCKED-ENVIRONMENT (exit 3), and a base run that never finished with VERDICT NO-DATA (exit 4). land_batch's
after_build sent every nonzero exit down the drop branch, and main() then called reject_status, which writes the refusal
as the sub unit's diagnosis note, counts it toward REJECTION_REPEAT_LIMIT and parks the sub unit on the repeat. A machine
defect cost the unit its attempts and parked it.

THE ENTRY POINT. Every case runs land_batch.py's main as the loop runs it, through test_land_batch_landings.Fixture (a
scratch tree with a bare upstream, a scratch HOME and TMPDIR, a scratch bin holding a fresh copy of land_batch.py). The
bin's land_apply.py is replaced by a stub that prints the verdict the build JSON names and exits with its code, and logs
every run beside the build, so a case can tell whether the build went to land_apply at all.

ONE CONDITION PER FIXTURE: each case changes the verdict pair, the crash count or the landing tree, nothing else.
Run: python3 -B scripts/loop/test_land_batch_env_hold.py
"""
import json, os, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diag_notes as DN                     # noqa: E402
import land_batch as LB                     # noqa: E402
import test_land_batch_landings as LL       # noqa: E402

SUB, UNIT, BRANCH = LL.SUB, LL.UNIT, LL.BRANCH
LANDED_FILE = "docs/landed-u1a.txt"

# land_apply as G10 left it, reduced to its output contract: the suites' lines, the FUZZ summary, ONE VERDICT line, the
# exit code. It writes the landing tree only on a green verdict, as the real one does.
APPLY_STUB = '''import json, os, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
open(os.path.join(os.path.dirname(sys.argv[1]), "apply-runs.log"), "a").write("ran\\n")
s = b["stub"]
print("py3     scripts/test_u1a.py exit=%d" % (0 if s["rc"] == 0 else 1))
print("FUZZ    new modules 0 | crashes %d | calls that returned instead of refusing 0" % s.get("crashes", 0))
if s["rc"] == 0:
    for e in b["edits"]:
        os.makedirs(os.path.dirname(e["path"]) or ".", exist_ok=True)
        open(e["path"], "w", encoding="utf-8").write(e["new_file_content"])
print("VERDICT %s | touched: %s" % (s["verdict"], ", ".join(e["path"] for e in b["edits"])))
sys.exit(s["rc"])
'''
ENVDEF = "BLOCKED-ENVIRONMENT | environment defect: scripts/test_u1a.py on py3 (base exit 1, build exit 1)"
NODATA = "NO-DATA | base run did not complete: scripts/test_u1a.py on py3 (base exit None, build exit 1)"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class EnvHold(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="g14-hold-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.f = LL.Fixture(os.path.join(self.root, "f"))
        LL.write(os.path.join(self.f.bin, "land_apply.py"), APPLY_STUB)
        self.runs_log = os.path.join(os.path.dirname(self.f.build), "apply-runs.log")

    def land(self, rc, verdict, crashes=0, status=None):
        self.f.edits({"edits": [{"path": LANDED_FILE, "new_file_content": "landed\n"}], "tests": [],
                      "stub": {"rc": rc, "verdict": verdict, "crashes": crashes}})
        if status is not None:
            LL.write(self.f.status, status)
        return self.f.land()

    def tree(self):
        return self.f.git("rev-parse", "HEAD^{tree}").strip()

    def hub(self):
        return self.f.git("rev-parse", BRANCH, cwd=self.f.hub).strip()

    def note(self):
        return DN.note_for(UNIT, SUB, notes=os.path.join(self.f.home, ".claude", "evidence", "diag-notes"))

    def apply_runs(self):
        return read(self.runs_log).count("ran") if os.path.exists(self.runs_log) else 0

    def move_tree(self):
        """Another landing moves the landing tree: one commit, pushed, so the tree is clean and equal to hub again."""
        self.moves = getattr(self, "moves", 0) + 1
        LL.write(os.path.join(self.f.tree, "docs", "other.txt"), "moved %d\n" % self.moves)
        self.f.git("add", "-A"); self.f.git("commit", "-q", "-m", "another landing"); self.f.git("push", "-q", "hub", BRANCH)
        self.f.base = self.f.rev("HEAD")

    def assert_held(self, rc, out, verdict_word):
        st = read(self.f.status)
        self.assertEqual(rc, 1, out)
        self.assertIn("HOLD    %s" % SUB, out)
        self.assertIn(verdict_word, out)
        self.assertTrue(any(l.startswith("nothing landed") for l in out.splitlines()),
                        "loop_pass reads a hold by this line, never as a refused landing:\n" + out)
        self.assertNotIn("DROPPED", out); self.assertNotIn("NOTE    ", out); self.assertNotIn("PARKED", out)
        self.assertTrue(st.startswith("READY %s (round 0) | HELD at landing on tree %s: %s" % (self.f.build, self.tree(), verdict_word)), st)
        self.assertEqual(self.note(), "", "a hold writes no diagnosis note")
        self.assertEqual(self.hub(), self.f.base, "nothing reached hub")
        self.assertFalse(os.path.exists(os.path.join(self.f.tree, LANDED_FILE)), "nothing applied stays in the tree")
        self.assertEqual(self.f.git("status", "--porcelain"), "", "the landing tree is left clean")

    # --- the two verdicts that hold ---------------------------------------------------------------------------------------
    def test_blocked_environment_exit_3_holds_with_no_note_no_count_no_park(self):
        rc, out = self.land(3, ENVDEF)
        self.assert_held(rc, out, "BLOCKED-ENVIRONMENT")

    def test_no_data_exit_4_holds_with_no_note_no_count_no_park(self):
        rc, out = self.land(4, NODATA)
        self.assert_held(rc, out, "NO-DATA")

    # --- the hold is keyed by the landing tree ----------------------------------------------------------------------------
    def test_a_held_build_is_not_reapplied_while_the_landing_tree_is_unchanged(self):
        self.land(3, ENVDEF)
        held = read(self.f.status)
        rc, out = self.f.land()
        self.assertEqual(self.apply_runs(), 1, "the second pass never ran land_apply:\n" + out)
        self.assertIn("held on the unchanged landing tree %s" % self.tree()[:12], out)
        self.assertTrue(any(l.startswith("nothing landed") for l in out.splitlines()), out)
        self.assertEqual(read(self.f.status), held, "the STATUS is untouched by a pass that only reads it")
        self.assertEqual(self.note(), "")

    def test_a_held_build_is_offered_again_once_the_tree_changes_and_lands_when_green(self):
        self.land(3, ENVDEF)
        self.move_tree()
        rc, out = self.land(0, "SUITES GREEN")
        self.assertEqual(self.apply_runs(), 2, out)
        self.assertEqual(rc, 0, out)
        self.assertNotEqual(self.hub(), self.f.base, "the landing reached hub")
        self.assertTrue(read(self.f.status).startswith("LANDED "), read(self.f.status))

    def test_repeated_holds_on_new_trees_never_park_and_never_note(self):
        for _ in range(LB.REJECTION_REPEAT_LIMIT + 1):
            rc, out = self.land(3, ENVDEF)
            self.assert_held(rc, out, "BLOCKED-ENVIRONMENT")
            self.move_tree()
        self.assertEqual(self.apply_runs(), LB.REJECTION_REPEAT_LIMIT + 1)
        self.assertEqual(read(self.f.status).count("HELD at landing"), 1, "an older hold is replaced, never stacked")

    # --- every other exit stays a drop exactly as before ------------------------------------------------------------------
    def assert_dropped(self, rc, out):
        self.assertEqual(rc, 1, out)
        self.assertIn("DROPPED %s" % SUB, out)
        self.assertNotIn("HOLD    ", out)
        self.assertTrue(read(self.f.status).startswith("QUARANTINE dropped at landing"), read(self.f.status))
        self.assertIn("LANDING REFUSAL 1 (dropped)", self.note(), "a drop is still the sub unit's diagnosis note")

    def test_a_verdict_and_exit_that_do_not_pair_are_a_drop(self):
        rc, out = self.land(3, NODATA)
        self.assert_dropped(rc, out)

    def test_a_hold_verdict_with_a_crash_is_a_drop(self):
        rc, out = self.land(3, ENVDEF, crashes=1)
        self.assert_dropped(rc, out)

    def test_the_builds_own_red_is_a_drop(self):
        rc, out = self.land(1, "RED: 1")
        self.assert_dropped(rc, out)

    def test_two_verdict_lines_are_a_drop(self):
        rc, out = self.land(3, ENVDEF + "\nVERDICT SUITES GREEN")
        self.assert_dropped(rc, out)

    def test_a_landing_tree_this_run_could_not_read_keeps_every_hold(self):
        held = LB.hold_status("READY /b/U1.a-r0-build.json (round 0)", "a" * 40, ENVDEF)
        self.assertTrue(LB.held_on(held, ""), "an unknown tree never releases a hold")
        self.assertTrue(LB.held_on(held, "a" * 40))
        self.assertEqual(LB.held_on(held, "b" * 40), "")

    def test_a_hold_mark_on_an_unreadable_tree_is_no_hold(self):
        rc, out = self.land(0, "SUITES GREEN", status="READY %s (round 0)%snot-a-tree: x\n" % (self.f.build, LB.HOLD_MARK))
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.apply_runs(), 1, out)


if __name__ == "__main__":
    unittest.main()
