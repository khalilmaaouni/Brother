#!/usr/bin/env python3
"""G15, reworked by the security review of 2026-10-16: the loop never certifies a change to its own governance.

WHAT WAS WRONG. G15 made land_batch.py run `sh scripts/required_fast.sh` in the landing checkout, with the real HOME and
the network, after the commit and before the push, so the pre-push gate's whole-gate record could be written for a loop
change. That executed the build's own code unboxed (reviews 8 and 11 forbade exactly that), under a gate the build could
rewrite, writing the PASS record that cleared its own push. Executed proof: with BROTHER_WHOLE_GATE_CMD naming a command
that only appended a PASS record, whole_gate() returned ('pass', ...) and wrote outside the tree.

NOW. A batch the push gate's own rule (the FROZEN pre_push_gate.check_whole_gate, its record lookup answered "never on
file") calls a loop change is unwound before its push and HELD with G14's hold: no note, no count, not offered again
until the landing tree changes, and one line says why. A batch touching no loop file lands exactly as before. Nothing
runs the whole gate and no seam runs a command.

THE ENTRY POINT. Every case runs land_batch.py's main as the loop runs it, through test_land_batch_landings.Fixture. The
landing tree carries the REAL scripts/gate_ledger.py and the REAL pre_push_gate.py (as pre_push_gate_full.py, with its
edition_guard.py and loop/secret_scan.py), behind a scripts/pre_push_gate.py that re-exports the real check_whole_gate
and, run as the push gate, answers with that one check only. scripts/required_fast.sh and BROTHER_WHOLE_GATE_CMD both
name commands that write a marker file if anything runs them; no case may find the marker.

ONE CONDITION PER FIXTURE: each case changes the batch's paths, the deploy list, or a record on disk, nothing else.
Run: python3 -B scripts/loop/test_land_batch_whole_gate.py
"""
import os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import diag_notes as DN                     # noqa: E402
import land_batch as LB                     # noqa: E402
import test_land_batch_landings as LL       # noqa: E402

SUB, UNIT, BRANCH = LL.SUB, LL.UNIT, LL.BRANCH
TOOL = "scripts/loop/g15_tool.py"
NAMED = "scripts/named_data.json"   # outside scripts/loop, named by the deploy list: a loop file to the push gate
DOC = "docs/landed-u1a.txt"

PUSH_GATE_SHIM = '''import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pre_push_gate_full import check_whole_gate, OK  # noqa: E402  the real rule, re-exported
if __name__ == "__main__":
    cwd = sys.argv[sys.argv.index("--cwd") + 1] if "--cwd" in sys.argv else os.getcwd()
    found = check_whole_gate(cwd, None, sys.stdin.read())
    for f in found:
        print("%-8s %-12s %s" % f)
    bad = [f for f in found if f[0] != OK]
    if not bad:
        print("pre-push: clear")
    sys.exit(1 if bad else 0)
'''
# a data file, so the landing's deploy closure gate (which reads only scripts/loop and .py files) is not asked
DEPLOY_LIST = 'CANDIDATE_ENTRIES = ()\nCANDIDATE_DATA = ("%s",)\n' % NAMED
MARK = 'import sys\nopen(sys.argv[1], "a").write("ran\\n")\n'
# the tree's own gate_ledger, asked whether a PASS record is on file for one tree
LEDGER = '''import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
import gate_ledger as L
ok, said = L.whole_gate_pass_for_tree(L.ledger_path(os.getcwd()), sys.argv[2])
print(said); sys.exit(0 if ok else 1)
'''


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def gate_tree(marker):
    files = {TOOL: "import os\n", NAMED: "import os\n",
             "scripts/pre_push_gate.py": PUSH_GATE_SHIM, "scripts/required_fast.sh": 'echo ran >> "%s"\n' % marker}
    for src, dst in (("scripts/pre_push_gate.py", "scripts/pre_push_gate_full.py"), ("scripts/gate_ledger.py", None),
                     ("scripts/edition_guard.py", None), ("scripts/loop/secret_scan.py", None)):
        files[dst or src] = read(os.path.join(REPO, src))
    return files


class LoopChangeHeld(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="g15-held-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.marker = os.path.join(self.root, "something-ran.txt")
        LL.write(os.path.join(self.root, "mark.py"), MARK)
        seam = "%s -B %s %s" % (sys.executable, os.path.join(self.root, "mark.py"), self.marker)
        self.f = LL.Fixture(os.path.join(self.root, "f"), tree_files=gate_tree(self.marker),
                            extra_env={"BROTHER_WHOLE_GATE_CMD": seam})
        LL.write(os.path.join(self.f.tree, ".gitignore"), "logs/\n")
        self.f.git("add", "-A"); self.f.git("commit", "-q", "-m", "ignore the ledger"); self.f.git("push", "-q", "hub", BRANCH)
        self.f.base = self.f.rev("HEAD")

    def land(self, path=TOOL, text="import json\n"):
        self.f.edits({"edits": [{"path": path, "new_file_content": text}], "tests": []})
        return self.f.land()

    def hub(self):
        return self.f.git("rev-parse", BRANCH, cwd=self.f.hub).strip()

    def note(self):
        return DN.note_for(UNIT, SUB, notes=os.path.join(self.f.home, ".claude", "evidence", "diag-notes"))

    def ledger(self, *args):
        return subprocess.run([sys.executable, "-B", "-c", LEDGER] + list(args), cwd=self.f.tree, env=self.f.env,
                              capture_output=True, text=True, timeout=120)

    def assert_nothing_ran(self, out):
        self.assertFalse(os.path.exists(self.marker), "neither the whole gate nor the old seam may run:\n" + out)

    def assert_held(self, rc, out, line, tree):
        self.assertEqual(rc, 1, out)
        self.assertIn(line, out.splitlines(), out)
        self.assertTrue(any(l.startswith("nothing landed: ") for l in out.splitlines()), "loop_pass reads a hold:\n" + out)
        self.assertEqual(self.hub(), self.f.base, "nothing reached hub:\n" + out)
        self.assertNotIn("PUSH    ", out, "the push is never attempted")
        self.assertEqual(self.f.rev("HEAD"), self.f.base, "the landing commit is unwound")
        self.assertEqual(self.f.git("status", "--porcelain"), "", "the landing tree is left clean")
        st = read(self.f.status)
        self.assertTrue(st.startswith("READY %s (round 0) | HELD at landing on tree %s: %s" % (self.f.build, tree, line[:60])), st)
        self.assertEqual(self.note(), "", "a hold writes no diagnosis note and charges no count")
        self.assert_nothing_ran(out)

    # --- a loop change: held before its push, nothing run ------------------------------------------------------------------
    def test_a_batch_changing_a_loop_file_is_held_and_never_pushed(self):
        rc, out = self.land()
        self.assert_held(rc, out, LB.LOOP_HELD, self.f.rev("HEAD^{tree}"))
        self.assertIn("LOOP    loop | ", out)
        self.assertEqual(read(os.path.join(self.f.tree, TOOL)), "import os\n")
        rc2, out2 = self.f.land()
        self.assertIn("HELD    %s" % SUB, out2, "a held build is not offered again on the same tree")
        self.assertIn("held on the unchanged landing tree", out2)
        self.assertEqual(self.hub(), self.f.base, out2)
        self.assert_nothing_ran(out2)

    def commit_deploy_list(self, text):
        LL.write(os.path.join(self.f.tree, "scripts", "loop", "deploy_stamped.py"), text)
        self.f.git("add", "-A"); self.f.git("commit", "-q", "-m", "deploy list"); self.f.git("push", "-q", "hub", BRANCH)
        self.f.base = self.f.rev("HEAD")

    def test_a_file_the_deploy_list_names_outside_scripts_loop_is_a_loop_file_too(self):
        self.commit_deploy_list(DEPLOY_LIST)
        rc, out = self.land(path=NAMED)
        self.assert_held(rc, out, LB.LOOP_HELD, self.f.rev("HEAD^{tree}"))
        self.assertEqual(read(os.path.join(self.f.tree, NAMED)), "import os\n")

    def test_a_pass_record_on_file_for_every_tree_changes_nothing(self):
        # the base's ledger answers "PASS on record" for ANY tree (a forged record, or a session's real one): the push gate
        # itself would let the loop change through, and the loop still holds it, because it never reads a record
        always = read(os.path.join(REPO, "scripts", "gate_ledger.py")) + (
            "\n\ndef whole_gate_pass_for_tree(path, tree_sha):\n    return True, 'a PASS record on file for every tree'\n")
        LL.write(os.path.join(self.f.tree, "scripts", "gate_ledger.py"), always)
        self.f.git("add", "-A"); self.f.git("commit", "-q", "-m", "a record for every tree"); self.f.git("push", "-q", "hub", BRANCH)
        self.f.base = self.f.rev("HEAD")
        r = self.ledger("ask", "0" * 40)
        self.assertEqual(r.returncode, 0, "the push gate would find a record: " + r.stdout + r.stderr)
        rc, out = self.land()
        self.assert_held(rc, out, LB.LOOP_HELD, self.f.rev("HEAD^{tree}"))

    # --- an unanswerable question holds the same way, never clear -----------------------------------------------------------
    def test_an_unreadable_deploy_list_holds_even_a_batch_touching_no_loop_file(self):
        self.commit_deploy_list("this is not python(\n")
        rc, out = self.land(path=DOC, text="landed\n")
        self.assertIn("LOOP    unknown | ", out)
        held = [l for l in out.splitlines() if l.startswith("HELD: whether this batch changes the loop's own tools could not be told")]
        self.assertEqual(len(held), 1, out)
        self.assert_held(rc, out, held[0], self.f.rev("HEAD^{tree}"))

    # --- no loop file: lands exactly as before ------------------------------------------------------------------------------
    def test_a_batch_touching_no_loop_file_lands(self):
        rc, out = self.land(path=DOC, text="landed\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("LOOP    clear | ", out)
        self.assertNotEqual(self.hub(), self.f.base, "the landing reached hub:\n" + out)
        landed = [l for l in out.splitlines() if l.startswith("LANDED  ")]
        self.assertEqual(len(landed), 1, out)
        self.assertNotIn("whole gate", landed[0])
        self.assert_nothing_ran(out)

    def test_a_mixed_batch_lands_its_other_build_and_holds_the_loop_one(self):
        both = "# U1\n\n### %s\n\nDone check: `python3 scripts/test_u1a.py`\n\n### %s\n\nDone check: `python3 scripts/test_u1a.py`\n" % (SUB, LL.SUB_B)
        LL.write(os.path.join(self.f.tree, "docs", "spec-u1.md"), both)
        self.f.edits({"edits": [{"path": TOOL, "new_file_content": "import json\n"}], "tests": []})
        other = self.f.add_second_sub({"edits": [{"path": "docs/landed-u1b.txt", "new_file_content": "landed\n"}], "tests": []})
        rc, out = self.f.land([self.f.build, other])
        self.assertEqual(rc, 0, out)
        self.assertIn("BISECT  retrying", out)
        self.assertIn(LB.LOOP_HELD, out.splitlines(), out)
        self.assertNotEqual(self.hub(), self.f.base, "the build touching no loop file landed alone:\n" + out)
        self.assertEqual(self.f.git("show", "%s:%s" % (BRANCH, TOOL), cwd=self.f.hub), "import os\n", "the loop file never reached hub")
        self.assertIn("HELD at landing on tree", read(self.f.status))
        self.assert_nothing_ran(out)

    def test_no_seam_runs_a_command(self):
        for name in ("whole_gate", "whole_gate_cmd", "WHOLE_GATE_CMD"):
            self.assertFalse(hasattr(LB, name), name)
        self.assertNotIn("BROTHER_WHOLE_GATE_CMD", read(os.path.join(HERE, "land_batch.py")))


if __name__ == "__main__":
    unittest.main()
