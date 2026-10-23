#!/usr/bin/env python3
"""The ELEVEN properties of scripts/loop/land_batch.py's LANDING path that nothing tested.

WHY THIS NAME. scripts/test_land_batch.py already exists, is 509 lines, is registered in the battery as
"land-batch-self", and tests scripts/land_batch.SH, an unrelated GitHub pull request tool. The name
collision is exactly what hid the gap: a reader seeing "test_land_batch" in the battery reasonably
concluded the lander was covered. Nothing tested scripts/loop/land_batch.py's landing path at all. This
file is named test_land_batch_loop_guard.py, with "loop" in it, so the collision cannot recur, and
scripts/test_land_batch.py is left untouched.

WHAT WAS MEASURED, 2026-09-21. An adversarial sweep ran 18 mutations against scripts/loop/land_batch.py.
ELEVEN survived: the code was broken and `land_batch.py --selftest` stayed green at "selftest: 17 cases,
OK". Four were reproduced by hand before this file was written (bool score accepted, stray paths no longer
blocking, the wrong branch guard deleted, the bisect loop deleted): every one printed "17 cases, OK" and
exited 0. The reason is structural, not an oversight: the selftest covers four pure helpers (gate,
register, evidence_line, unattributed) and never main(), where the landing actually happens. So the
decisions main() took inline were extracted into small pure functions, facts in and decision out, and this
file asserts each of them.

THE ELEVEN, in the order the sweep reported them:
   1 the bisect loop deleted entirely                        bisect_plan
   2 the bisect retrying the whole batch, not one build      bisect_plan
   3 the quarantine of a DROPPED build never written         after_build
   4 the quarantine of the lone gate failing build removed   gate_quarantine
   5 stray unattributed paths no longer blocking the commit  refusal_reason
   6 the commit_scan gate result ignored                     step_refusal
   7 the final parity and config check always returning 0    exit_code
   8 the hermetic check result ignored                       step_refusal
   9 the wrong branch guard removed                          preflight
  10 the dirty tree guard removed                            preflight
  11 a bool accepted where a numeric score is required       score_hold

FIXTURES ARE ORTHOGONAL, the rule that cost this estate the most on 2026-09-21: a fixture that trips two
guards proves neither, because either guard alone keeps it green. The measured instance was a failure
fixture that returned a non zero exit AND an empty body, so one guard masked the other. Every fixture here
trips exactly ONE condition: the drop fixture carries a perfectly green body with zero crashes and only a
non zero exit, each preflight fixture has three good facts and one bad, and each exit_code fixture has two
good facts and one bad. Every function is also asserted to ACCEPT its good input, so "always refuse" is
not a way to pass.

HERMETIC. Every fixture is built in a temp directory, no live repository document is read, and the file
passes with an empty HOME. The module is loaded by path relative to this file, never by sys.path luck."""
import importlib.util, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "loop", "land_batch.py")


def load():
    """The module under test, by path. A unique module name because a sibling suite tests the unrelated
    land_batch.SH and nothing here may collide with it in sys.modules."""
    spec = importlib.util.spec_from_file_location("land_batch_loop_under_test", TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def status_round_trip(tmp, note):
    """Write a quarantine note into a real STATUS file and ask the gate what it says on the NEXT pass.
    The text alone is not the property: the property is that the build is no longer offered, which is
    what the livelock of 2026-09-21 turned on (two builds offered three passes running)."""
    build = os.path.join(tmp, "D4.c-1", "round2", "out", "D4.c-r1-build.json")
    os.makedirs(os.path.dirname(build), exist_ok=True)
    open(build, "w").write("{}")
    st = os.path.join(tmp, "D4.c-1", "STATUS")
    open(st, "w", encoding="utf-8").write(note)
    return build, open(st, encoding="utf-8").read()


def main():
    lb = load()
    cases = []
    def case(name, good, detail=""):
        cases.append((name, bool(good), detail))
    # the battery runs this file every pass, so the fixtures are removed with them: a test that leaves a
    # directory in /tmp per run is a slow leak, not a test
    with tempfile.TemporaryDirectory(prefix="land-batch-loop-guard-") as tmp:
        return run_cases(lb, cases, case, tmp)


def run_cases(lb, cases, case, tmp):

    plan = {"units": [{"id": "D4", "sub_units": ["D4.c"], "evidence": ""}]}
    council = {"D4": {"state": "FIX-FIRST"}}
    scores = {"D4.c": {"score": 10}}

    # 1 and 2: THE BISECT. One poisoned build in a batch of five reverted all five and the identical five
    # were offered again every pass, forever. Retrying the BATCH would reproduce that exactly.
    two = lb.bisect_plan(["D4.c", "L3.3"], ["/x/D4.c-r0-build.json", "/x/L3.3-r0-build.json"], False)
    case("1 a refused multi build batch is bisected at all", len(two) == 2, "plan=%r" % (two,))
    three = lb.bisect_plan(["a", "b", "c"], ["/x/a-r0-build.json", "/x/b-r0-build.json", "/x/c-r0-build.json"], False)
    case("2 the bisect retries ONE build at a time, never the batch",
         [len(g) for g in three] == [1, 1, 1], "group sizes=%r" % ([len(g) for g in three],))
    # contingency: without this the --no-bisect child re-bisects and the lander recurses forever
    case("2b a --no-bisect child does not bisect again",
         lb.bisect_plan(["a", "b"], ["/x/a-r0-build.json", "/x/b-r0-build.json"], True) == [])
    case("2c a single build batch is not bisected",
         lb.bisect_plan(["a"], ["/x/a-r0-build.json"], False) == [])

    # 3: THE DROPPED BUILD'S QUARANTINE. ORTHOGONAL FIXTURE: the body is perfectly green and crashes are
    # zero, so ONLY the non zero exit can produce the drop. A fixture that was also empty would let the
    # "VERDICT SUITES GREEN" test mask the exit code test and deleting either would stay green.
    green_body = ("py3 scripts/test_x.py exit=0 Ran 4 tests\nVERDICT SUITES GREEN\n"
                  "FUZZ    new modules 0 | crashes 0 | calls that returned instead of refusing 0\n")
    verdict, why, crashes, note = lb.after_build(1, green_body, "READY /x/D4.c-r1-build.json")
    case("3 a build that exits non zero on a green body is dropped", verdict == "drop", "verdict=%r" % verdict)
    case("3b a DROPPED build is quarantined, never left READY",
         note and note.startswith("QUARANTINE"), "note=%r" % (note,))
    build, written = status_round_trip(tmp, note or "")
    case("3c the quarantine a drop writes is refused by the gate on the next pass",
         lb.gate(build, written, council, scores, plan) != "", "gate=%r" % lb.gate(build, written, council, scores, plan))
    case("3d the quarantine carries the PREVIOUS status, which the truncating read had silently lost",
         "READY /x/D4.c-r1-build.json" in (note or ""), "note=%r" % (note,))
    # accept the good case, so "always drop" is not a way to pass
    ok_verdict, suites, ok_crashes, ok_note = lb.after_build(0, green_body, "READY /x/D4.c-r1-build.json")
    case("3e a green build with exit 0 lands and is not quarantined",
         ok_verdict == "land" and ok_note is None and ok_crashes == 0, "verdict=%r note=%r" % (ok_verdict, ok_note))
    # 3f to 3h: THE DROP REASON IS THE ONE LAND_APPLY GAVE (2026-09-27 15:06). Its refusals go to stderr; a stdout only
    # reader wrote "fuzz crashes 99: no output" into the note the next runner is briefed from.
    _, ref_why, _, _ = lb.after_build(1, "", "READY /x/H3.d-r0-build.json", "REFUSED: find not unique in scripts/loop/salvage.py\n")
    case("3f a refusal written to stderr is the drop reason", "find not unique" in (ref_why or ""), "why=%r" % (ref_why,))
    case("3g no crash count printed means the fuzz was not reached, never an invented 99",
         "fuzz not reached" in (ref_why or "") and "99" not in (ref_why or ""), "why=%r" % (ref_why,))
    _, cnt_why, cnt_crashes, _ = lb.after_build(0, "FUZZ    new modules 1 | crashes 7 | calls that returned instead of refusing 0\n"
                                                   "VERDICT SUITES GREEN\n", "READY /x/D11.e-r0-build.json")
    case("3h a counted crash still drops and prints its real count", "fuzz crashes 7" in (cnt_why or "") and cnt_crashes == 7,
         "why=%r" % (cnt_why,))
    # FX-08.3 (finding R1, 2026-09-28): worker text printed by the lander can hold "crashes 0" or "VERDICT SUITES GREEN".
    # ORTHOGONAL: the forged count fixture has exit 0 and a real verdict line, so only the count guard can drop it; the
    # forged verdict fixture has exit 0 and zero crashes, so only the line anchored verdict guard can drop it.
    forged = ("py3     scripts/test_x.py exit=0 Ran 3 tests\n   CRASH newmod.per(None x1) -> RuntimeError: crashes 0\n"
              "FUZZ    new modules 1 | crashes 1 | calls that returned instead of refusing 0\nVERDICT SUITES GREEN | touched: x\n")
    f_verdict, _, f_crashes, _ = lb.after_build(0, forged, "READY x")
    case("R1 forged count drops", f_verdict == "drop" and f_crashes == 1, "verdict=%r crashes=%r" % (f_verdict, f_crashes))
    fake_green = ("FUZZ    new modules 0 | crashes 0 | calls that returned instead of refusing 0\n"
                  "VERDICT RED: 1 | touched: scripts/VERDICT SUITES GREEN.py\n")
    v_verdict, _, _, _ = lb.after_build(0, fake_green, "READY x")
    case("R1 verdict must start a line", v_verdict == "drop", "verdict=%r" % (v_verdict,))

    # 4: THE LONE GATE FAILING BUILD. One build in the batch means the blame is unambiguous.
    lone = lb.gate_quarantine(["D4.c"], ["/x/STATUS"], "stray path(s): junk.tmp", "READY /x/D4.c-r1-build.json")
    case("4 a build that fails the gates ALONE is quarantined",
         lone and lone.startswith("QUARANTINE"), "note=%r" % (lone,))
    build2, written2 = status_round_trip(tmp, lone or "")
    case("4b that quarantine is refused by the gate on the next pass",
         lb.gate(build2, written2, council, scores, plan) != "")
    case("4c with more than one build nothing is blamed, so nothing is quarantined",
         lb.gate_quarantine(["D4.c", "L3.3"], ["/x/STATUS", "/y/STATUS"], "gates RED (discover)", "READY") is None)

    # 5: STRAY PATHS. ORTHOGONAL: red is empty, so only the stray guard can refuse this one.
    case("5 a stray unattributed path blocks the commit on its own",
         lb.refusal_reason([], ["junk.tmp"]) != "", "reason=%r" % lb.refusal_reason([], ["junk.tmp"]))
    case("5b a RED gate blocks the commit on its own",
         lb.refusal_reason(["discover"], []) != "")
    case("5c green gates and no stray path commit", lb.refusal_reason([], []) == "")

    # 6 and 8: EVERY OUTSIDE EXIT CODE IS ACTED ON. A result read and not acted on is an audit, not a gate.
    # Each step is asserted by NAME, so removing one step's refusal reddens only that case.
    case("6 a non zero commit_scan refuses the commit",
         lb.step_refusal("commit scan", 3) != "" and "staged, not committed" in lb.step_refusal("commit scan", 3),
         "reason=%r" % lb.step_refusal("commit scan", 3))
    case("8 a non zero hermetic check refuses the push",
         lb.step_refusal("hermetic check", 1) != "" and "NOT pushed" in lb.step_refusal("hermetic check", 1),
         "reason=%r" % lb.step_refusal("hermetic check", 1))
    case("6b an exit 0 step is not refused",
         lb.step_refusal("commit scan", 0) == "" and lb.step_refusal("hermetic check", 0) == "")

    # 7: THE FINAL VERDICT. Three facts, one bad per fixture, so each is proved separately.
    case("7 broken parity cannot exit 0", lb.exit_code(False, 0, True) == 1)
    case("7b a failed push cannot exit 0", lb.exit_code(True, 1, True) == 1)
    case("7c a changed shared git config cannot exit 0", lb.exit_code(True, 0, False) == 1)
    case("7d a clean landing exits 0", lb.exit_code(True, 0, True) == 0)

    # 9 and 10: THE PREFLIGHT. Each fixture carries three good facts and exactly one bad one.
    case("9 landing on the wrong branch refuses",
         lb.preflight("main", set(), "abc1234", "abc1234", plan) != "",
         "reason=%r" % lb.preflight("main", set(), "abc1234", "abc1234", plan))
    case("10 landing on a dirty tree refuses",
         lb.preflight(lb.BRANCH, {"scripts/x.py"}, "abc1234", "abc1234", plan) != "",
         "reason=%r" % lb.preflight(lb.BRANCH, {"scripts/x.py"}, "abc1234", "abc1234", plan))
    # DO NOT WEAKEN THE LANDING: the three refusals that already existed still refuse.
    case("W hub divergence still refuses",
         lb.preflight(lb.BRANCH, set(), "aaa1111", "bbb2222", plan) != "")
    case("W an unreadable plan still refuses",
         lb.preflight(lb.BRANCH, set(), "abc1234", "abc1234", None) != "")
    case("W a clean tree on the right branch at hub parity with a readable plan starts",
         lb.preflight(lb.BRANCH, set(), "abc1234", "abc1234", plan) == "")

    # 11: THE SCORE TYPE. A bool is refused BY TYPE and says so. Before this change the guard was dead
    # code: True < 9 holds anyway, so deleting isinstance(sc, bool) changed nothing any check could see.
    case("11 a bool is refused as a score, by type",
         "not a whole number" in lb.score_hold(True), "reason=%r" % lb.score_hold(True))
    case("11b a missing score is refused by type too", "not a whole number" in lb.score_hold(None))
    # THE BAR IS THE POOL'S FLOOR, 8 (2a5d8095e, 2026-09-25: runner_pool admits at BROTHER_SPEC_FLOOR default 8, so a
    # build of an 8 was paid for and then refused here). This case pinned the old bar of 9 and stayed red from that
    # commit on; 7 is the score below the bar now. The floor is read from the environment, so the default is pinned here.
    os.environ.pop("BROTHER_SPEC_FLOOR", None)
    case("11c a score below the bar is refused by VALUE, in a different sentence",
         lb.score_hold(7) != "" and "not a whole number" not in lb.score_hold(7), "reason=%r" % lb.score_hold(7))
    case("11d a score of 9 is accepted", lb.score_hold(9) == "")
    case("11e a score of 8, the pool's floor, is accepted", lb.score_hold(8) == "", "reason=%r" % lb.score_hold(8))

    bad = [(n, d) for n, good, d in cases if not good]
    for n, d in bad:
        print("FAIL  %s  %s" % (n, d))
    print("land_batch loop guard: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: %d" % len(bad)))
    return 1 if bad else 0


KERNEL_TOOL = os.path.join(HERE, "kernel_closure.py")
#: test_plan_117's rule: the export tree ships no docs/plan/specs, and it ships plugin/runtime in another shape, so the
#: committed kernel list (the hub's tree) is compared only in the hub (pre-push hermetic run, 2026-10-19: on the export
#: tree the closure reached dream_policy.py by another path and read one file lost)
HUB_ONLY = os.path.isdir(os.path.join(os.path.dirname(HERE), "docs", "plan", "specs"))


def load_kernel():
    spec = importlib.util.spec_from_file_location("kernel_closure_under_test", KERNEL_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TheElevenLandingProperties(unittest.TestCase):
    """The eleven cases above, one unittest method (A2.f: plan_lint TEST-FOUND refused a main() runner)."""

    def test_the_eleven_properties_hold(self):
        self.assertEqual(main(), 0, "a landing property above printed FAIL")


class TheKernelIsDerived(unittest.TestCase):
    """A2.a (COUNCIL D2): the kernel is the derived closure of the judge entry points, checked so it cannot drift."""

    def fixture(self, files, listed):
        d = tempfile.mkdtemp(prefix="kernel-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        for rel, body in files.items():
            os.makedirs(os.path.join(d, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
                fh.write(body)
        with open(os.path.join(d, "scripts", "kernel_closure.txt"), "w", encoding="utf-8") as fh:
            fh.write("# header\n" + "".join(p + "\n" for p in listed))
        kc = load_kernel()
        kc.ROOT, kc.ROOTS = d, ("scripts/judge.py", "scripts/gate.sh", "scripts/loop_certificate.py")
        # specs_present() reads the module's OWN tree on purpose (a fixture rebinding ROOT must not silence the row), so
        # on the public export tree, which ships no docs/plan/specs, --check here read NO-DATA or SKIP whatever the
        # fixture held (2026-10-22, the hermetic push check). These fixtures test derivation and drift on a hub
        # shaped tree; the export skip itself is test_kernel_closure_row.py's.
        kc.specs_present = lambda: True
        return kc

    def check(self, kc):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = kc.main(["--check"])
        return code, out.getvalue()

    @unittest.skipUnless(HUB_ONLY, "the export tree is not the tree the committed kernel list describes")
    def test_the_committed_list_equals_the_closure_of_this_tree(self):
        r = subprocess.run([sys.executable, "-B", KERNEL_TOOL, "--check"], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("PASS: the kernel equals"), r.stdout)

    def test_the_certifier_is_kernel_before_it_exists(self):
        self.assertIn("scripts/loop_certificate.py", load_kernel().closure())   # whether or not LC10 has built it

    def test_imports_and_gate_rows_are_followed_and_a_drift_names_the_file(self):
        files = {"scripts/judge.py": "import helper\n\ndef f():\n    from loop import inner\n",
                 "scripts/helper.py": "import json\n",
                 "scripts/loop/inner.py": "",
                 "scripts/gate.sh": "# run_check \"x\" python3 scripts/commented.py\nrun_check \"y\" python3 scripts/test_row.py\n",
                 "scripts/test_row.py": "import row_dep\n", "scripts/row_dep.py": "", "scripts/commented.py": ""}
        want = ["scripts/gate.sh", "scripts/helper.py", "scripts/judge.py", "scripts/loop/inner.py",
                "scripts/loop_certificate.py", "scripts/row_dep.py", "scripts/test_row.py"]
        kc = self.fixture(files, want)
        self.assertEqual(kc.closure(), want)              # the commented row is not followed; json is not the tree's
        self.assertEqual(self.check(kc)[0], 0)
        kc = self.fixture(files, [p for p in want if p != "scripts/row_dep.py"])
        code, out = self.check(kc)
        self.assertEqual(code, 1, out)
        self.assertIn("FAIL kernel gained   scripts/row_dep.py", out)

    def test_an_extension_module_and_a_relative_import_join_the_closure(self):
        """Security review, 2026-10-19: a compiled module loads before its .py, and a relative import is still an import."""
        import importlib.machinery
        so = "scripts/fast" + importlib.machinery.EXTENSION_SUFFIXES[0]
        files = {"scripts/judge.py": "import fast\nimport pkg.mod\n", so: "bytes", "scripts/gate.sh": "",
                 "scripts/pkg/__init__.py": "", "scripts/pkg/mod.py": "from . import sib\nfrom .deep import x\n",
                 "scripts/pkg/sib.py": "", "scripts/pkg/deep.py": ""}
        kc = self.fixture(files, [])
        got = kc.closure()
        for want in (so, "scripts/pkg/sib.py", "scripts/pkg/deep.py", "scripts/pkg/mod.py"):
            self.assertIn(want, got)

    def test_roots_mode_derives_from_the_current_directory_in_isolated_mode(self):
        d = tempfile.mkdtemp(prefix="kernel-roots-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        os.makedirs(os.path.join(d, "scripts"))
        for rel, body in (("scripts/a.py", "import b\n"), ("scripts/b.py", "")):
            with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
                fh.write(body)
        r = subprocess.run([sys.executable, "-I", "-B", KERNEL_TOOL, "--roots", "scripts/a.py"], cwd=d,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.split()), (0, ["scripts/a.py", "scripts/b.py"]), r.stderr)

    def test_an_unparsable_member_is_no_data_never_a_shorter_kernel(self):
        kc = self.fixture({"scripts/judge.py": "def broken(:\n", "scripts/gate.sh": ""}, [])
        code, out = self.check(kc)
        self.assertEqual(code, 2, out)
        self.assertTrue(out.startswith("NO-DATA: the kernel cannot be derived"), out)

    def test_no_committed_list_is_no_data(self):
        kc = self.fixture({"scripts/judge.py": "", "scripts/gate.sh": ""}, [])
        os.remove(os.path.join(kc.ROOT, "scripts", "kernel_closure.txt"))
        self.assertEqual(self.check(kc)[0], 2)



def load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(root, files):
    for rel, body in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)


def sha(text):
    return __import__("hashlib").sha256(text.encode()).hexdigest()


#: the fixture kernel: one judge root importing a helper, one gate script whose row runs a test importing the code under
#: test; the certifier root is absent, as it is in the tree today
KFILES = {"scripts/loop/judge.py": "import helper\nVERSION = 'N'\n", "scripts/helper.py": "H = 1\n",
          "scripts/gate.sh": 'run_check "row" python3 scripts/test_row.py\n',
          "scripts/test_row.py": "import under_test\n", "scripts/under_test.py": "X = 'N'\n"}
KROOTS = ("scripts/loop/judge.py", "scripts/gate.sh", "scripts/loop_certificate.py")


class TheStampCarriesTheKernel(unittest.TestCase):
    """A2.b: a deploy copies the kernel at N; a landed root stays at N's bytes, pending, until the owner's approval."""

    def setUp(self):
        import shutil
        self.d = tempfile.mkdtemp(prefix="stamp-kernel-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.D = load_path("deploy_stamped_under_test", os.path.join(HERE, "loop", "deploy_stamped.py"))
        self.kc = load_kernel()
        self.kc.ROOTS = KROOTS

    def tree(self, name, changes=None):
        root = os.path.join(self.d, name)
        write(root, dict(KFILES, **(changes or {})))
        return root

    def stage(self, name, previous, revision, changes=None, approved=False):
        kdir = os.path.join(self.d, name + "-kernel")
        kernel, pending, overlay = self.D.stage_kernel(self.tree(name, changes), kdir, previous, revision,
                                                       self.kc.tree_report, approved)
        return {"kernel": kernel, "kernel_dir": kdir, "pending_kernel": pending, "kernel_overlay": overlay}

    def read(self, stamp, rel):
        with open(os.path.join(stamp["kernel_dir"], rel), encoding="utf-8") as fh:
            return fh.read()

    def test_a_first_kernel_stamp_activates_everything_and_overlays_only_judges_and_row_files(self):
        s = self.stage("n", None, "a" * 40)
        self.assertEqual(sorted(s["kernel"]), sorted(list(KFILES) + ["scripts/loop_certificate.py"]))
        self.assertIsNone(s["kernel"]["scripts/loop_certificate.py"])          # a root before it exists
        self.assertEqual(s["pending_kernel"], {})
        # the code under test and the judge's helper stay N+1 in a judge tree: overlaying them would judge N
        self.assertEqual(s["kernel_overlay"], ["scripts/gate.sh", "scripts/loop/judge.py", "scripts/test_row.py"])

    def test_a_landed_root_keeps_generation_n_bytes_and_is_named_pending(self):
        n = self.stage("n", None, "a" * 40)
        n1 = self.stage("n1", n, "b" * 40, {"scripts/loop/judge.py": "import helper\nVERSION = 'N+1'\n",
                                             "scripts/test_row.py": "import under_test\nY = 2\n"})
        self.assertIn("VERSION = 'N'", self.read(n1, "scripts/loop/judge.py"))      # the stamp did not move for it
        self.assertEqual(n1["pending_kernel"], {"scripts/loop/judge.py": {
            "deployed_sha256": sha(KFILES["scripts/loop/judge.py"]),
            "landed_sha256": sha("import helper\nVERSION = 'N+1'\n"), "landed_at_revision": "b" * 40}})
        self.assertIn("Y = 2", self.read(n1, "scripts/test_row.py"))     # a row file moves with the landing that passed

    def test_a_pending_entry_carries_its_landing_and_a_second_landing_restamps_it(self):
        n = self.stage("n", None, "a" * 40)
        landed = {"scripts/loop/judge.py": "VERSION = 'N+1'\n"}
        n1 = self.stage("n1", n, "b" * 40, landed)
        n2 = self.stage("n2", n1, "c" * 40, landed)
        self.assertEqual(n2["pending_kernel"]["scripts/loop/judge.py"]["landed_at_revision"], "b" * 40)
        n3 = self.stage("n3", n2, "d" * 40, {"scripts/loop/judge.py": "VERSION = 'N+2'\n"})
        self.assertEqual(n3["pending_kernel"]["scripts/loop/judge.py"]["landed_at_revision"], "d" * 40)
        self.assertIn("VERSION = 'N'", self.read(n3, "scripts/loop/judge.py"))

    def test_the_approval_activates_exactly_the_pending_bytes(self):
        n = self.stage("n", None, "a" * 40)
        landed = {"scripts/loop/judge.py": "VERSION = 'N+1'\n"}
        n1 = self.stage("n1", n, "b" * 40, landed)
        act = self.stage("act", n1, "c" * 40, landed, approved=True)
        self.assertEqual((act["pending_kernel"], self.read(act, "scripts/loop/judge.py")), ({}, "VERSION = 'N+1'\n"))

    def test_an_approval_refuses_a_root_that_moved_without_its_own_landing(self):
        n = self.stage("n", None, "a" * 40)
        n1 = self.stage("n1", n, "b" * 40, {"scripts/loop/judge.py": "VERSION = 'N+1'\n"})
        with self.assertRaisesRegex(RuntimeError, "moved without its own landing"):
            self.stage("act", n1, "c" * 40, {"scripts/loop/judge.py": "VERSION = 'hand edit'\n"}, approved=True)
        with self.assertRaisesRegex(RuntimeError, "moved without its own landing"):   # no pending entry at all
            self.stage("act2", n, "c" * 40, {"scripts/gate.sh": "# nothing\n"}, approved=True)

    def test_an_unreadable_or_tampered_generation_n_copy_refuses(self):
        n = self.stage("n", None, "a" * 40)
        with open(os.path.join(n["kernel_dir"], "scripts/loop/judge.py"), "w") as fh:
            fh.write("tampered\n")
        with self.assertRaisesRegex(RuntimeError, "does not match its stamp"):
            self.stage("n1", n, "b" * 40)
        gone = dict(n, kernel_dir=os.path.join(self.d, "nowhere"))
        with self.assertRaisesRegex(RuntimeError, "is not there"):
            self.stage("n2", gone, "b" * 40)

    def test_the_deploy_derives_with_this_generations_tool_by_subprocess(self):
        write(os.path.join(self.d, "t"), {"scripts/close_unit.py": "import helper\n", "scripts/helper.py": "",
                                          "scripts/required_fast.sh": 'run_check "r" python3 scripts/test_r.py\n',
                                          "scripts/test_r.py": "import helper\n"})
        rep = self.D.derive_with(KERNEL_TOOL)(os.path.join(self.d, "t"))
        self.assertIn("scripts/helper.py", rep["kernel"])
        self.assertEqual(rep["rows"], ["scripts/test_r.py"])                  # the row, never its imports
        self.assertIn("scripts/loop_certificate.py", rep["roots"])
        write(os.path.join(self.d, "t"), {"scripts/close_unit.py": "def broken(:\n"})
        with self.assertRaisesRegex(RuntimeError, "cannot be derived"):
            self.D.derive_with(KERNEL_TOOL)(os.path.join(self.d, "t"))

    def test_hold_pending_puts_n_bytes_where_the_bin_and_the_candidate_run_them(self):
        n = self.stage("n", None, "a" * 40)
        n1 = self.stage("n1", n, "b" * 40, {"scripts/loop/judge.py": "VERSION = 'N+1'\n"})
        stage = os.path.join(self.d, "stage")
        write(stage, {"judge.py": "VERSION = 'N+1'\n", "candidate/scripts/loop/judge.py": "VERSION = 'N+1'\n"})
        self.D.hold_pending(stage, n1["kernel_dir"], n1["pending_kernel"])
        for rel in ("judge.py", "candidate/scripts/loop/judge.py"):
            with open(os.path.join(stage, rel), encoding="utf-8") as fh:
                self.assertIn("VERSION = 'N'", fh.read(), rel)


class TheInPassModeAsksTheLease(unittest.TestCase):
    """A2.d step 6: --in-pass switches only from inside the lease holder's own process tree."""

    def test_the_holder_must_be_an_ancestor(self):
        D = load_path("deploy_stamped_in_pass", os.path.join(HERE, "loop", "deploy_stamped.py"))
        D.in_pass_check(owner="4242", chain=[100, 4242, 7])
        for owner, chain in (("", [4242]), ("FREE", [4242]), ("4242", [100, 7])):
            with self.assertRaisesRegex(RuntimeError, "--in-pass"):
                D.in_pass_check(owner=owner, chain=chain)
        parents = {"50": "40", "40": "30", "30": "1"}
        ps = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, parents.get(argv[-1], "") + "\n", "")
        self.assertEqual(D.ancestors(50, ps=ps), [40, 30])


class ParityReadsThePendingList(unittest.TestCase):
    """A2.c: a landed kernel file not yet activated is PENDING, never DRIFT; anything else differing stays DRIFT."""

    def setUp(self):
        import shutil
        self.d = tempfile.mkdtemp(prefix="parity-pending-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.repo, self.bin = os.path.join(self.d, "repo"), os.path.join(self.d, "bin")
        write(self.repo, {"scripts/loop/judge.py": "VERSION = 'N+1'\n"})
        write(self.bin, {"judge.py": "VERSION = 'N'\n"})
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        for argv in (["git", "init", "-q"], ["git", "add", "-A"],
                     ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "x"]):
            subprocess.run(argv, cwd=self.repo, env=env, check=True, capture_output=True)
        self.P = load_path("parity_under_test", os.path.join(HERE, "test_loop_tool_parity.py"))
        self.P.BIN, self.P.HERE = self.bin, os.path.join(self.repo, "scripts")
        self.P.REPO_DIRS = [os.path.join(self.repo, "scripts", "loop"), os.path.join(self.repo, "scripts")]

    def stamp(self, pending):
        with open(os.path.join(self.bin, ".deploy-stamp.json"), "w") as fh:
            json.dump({"schema": 1, "revision": "f" * 40, "pending_kernel": pending}, fh)

    def entry(self, deployed="VERSION = 'N'\n", landed="VERSION = 'N+1'\n"):
        return {"scripts/loop/judge.py": {"deployed_sha256": sha(deployed), "landed_sha256": sha(landed),
                                          "landed_at_revision": "f" * 40}}

    def test_a_pending_kernel_file_reads_pending(self):
        self.stamp(self.entry())
        self.assertEqual(self.P.drifted(), ([], [("judge.py", "PENDING")]))

    def test_a_tree_reached_by_another_case_still_reads_pending(self):
        """2026-10-19, measured: inside the deploy the check ran as /Users/.../brother-wt/... while git printed the
        stored case, Brother-wt; the relative key read ../../brother-wt/... and the pending file read DRIFT. The key is
        computed from the check's own folder, never from git's spelling. On a disk that folds case the fixture is
        reached by another spelling; elsewhere that cannot happen, and the key is still asserted on a spelled path."""
        self.stamp(self.entry())
        self.P.HERE = os.path.join(self.d, "repo", "scripts")
        self.assertEqual(self.P.repo_rel(os.path.join(self.P.HERE, "loop", "judge.py")), "scripts/loop/judge.py")
        other = os.path.join(self.d, "REPO")
        if os.path.isdir(other):                  # this disk folds case: reach the same tree by another spelling
            self.P.HERE = os.path.join(other, "scripts")
            self.P.REPO_DIRS = [os.path.join(other, "scripts", "loop"), os.path.join(other, "scripts")]
            self.assertEqual(self.P.drifted(), ([], [("judge.py", "PENDING")]))
            self.assertEqual(self.P.versioned_at("HEAD", os.path.join(other, "scripts", "loop", "judge.py")),
                             b"VERSION = 'N+1'\n")

    def test_no_entry_a_moved_landing_or_a_hand_edited_bin_is_drift(self):
        for pending in ({}, self.entry(landed="VERSION = 'other'\n"), self.entry(deployed="VERSION = 'edited'\n"),
                        {"scripts/loop/judge.py": "not an object"}):
            self.stamp(pending)
            self.assertEqual(self.P.drifted(), ([("judge.py", "differs")], []), pending)



class TheDeployStep(unittest.TestCase):
    """A2.d: LOOP_HELD becomes a deploy step; generation N judges N+1 from the stamp's frozen copy."""

    def setUp(self):
        import shutil
        self.d = tempfile.mkdtemp(prefix="deploy-step-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.lb = load()
        D = load_path("deploy_stamped_for_step", os.path.join(HERE, "loop", "deploy_stamped.py"))
        kc = load_kernel()
        kc.ROOTS = KROOTS
        write(os.path.join(self.d, "n"), KFILES)
        kdir = os.path.join(self.d, "kernel")
        kernel, pending, overlay = D.stage_kernel(os.path.join(self.d, "n"), kdir, None, "a" * 40, kc.tree_report)
        self.stamp = {"kernel": kernel, "kernel_dir": kdir, "pending_kernel": pending, "kernel_overlay": overlay}

    def test_the_verdict_needs_every_row_pass_and_a_zero_exit(self):
        good = "PASS    exit 0   one   1s  x\nPASS    exit 0   two   2s  y\npass 2   fail 0   no-data 0\n"
        self.assertEqual(self.lb.judge_verdict(0, good)[:2], (True, "one\t0\ntwo\t0\n"))
        for code, out in ((0, good.replace("PASS    exit 0   two", "NO-DATA exit 0   two")),
                          (0, good.replace("PASS    exit 0   two", "FAIL    exit 1   two")),
                          (1, good), (0, "pass 0   fail 0   no-data 0\n"), (True, good), (0, None)):
            self.assertFalse(self.lb.judge_verdict(code, out)[0], (code, out))

    def steps(self, refuse=None):
        calls = []
        def step(name):
            def run():
                calls.append(name)
                return (name != refuse, name + " said")
            return run
        do = {n: step(n) for n in ("judge", "record", "stage", "publish")}
        do["restore"] = lambda: calls.append("restore")
        return do, calls

    def test_nothing_deploys_before_the_boxed_gate_and_the_canary_pass(self):
        for refuse, want in ((None, ["judge", "record", "stage"]), ("judge", ["judge"]), ("record", ["judge", "record"]),
                             ("stage", ["judge", "record", "stage", "restore"])):
            do, calls = self.steps(refuse)
            ok, detail = self.lb.deploy_prepare(do)
            self.assertEqual((ok, calls), (refuse is None, want), detail)
            if refuse:
                self.assertTrue(detail.startswith(refuse + " refused"), detail)

    def test_a_refused_publish_restores_generation_n(self):
        do, calls = self.steps("publish")
        self.assertFalse(self.lb.deploy_switch(do)[0])
        self.assertEqual(calls, ["publish", "restore"])
        do, calls = self.steps()
        self.assertEqual((self.lb.deploy_switch(do)[0], calls), (True, ["publish"]))

    def test_a_gate_row_test_changed_in_n1_runs_from_the_stamp_copy(self):
        judge = os.path.join(self.d, "judge")
        write(judge, dict(KFILES, **{"scripts/test_row.py": "weakened = True\n", "scripts/under_test.py": "X = 'N+1'\n",
                                     "scripts/loop_certificate.py": "built in N+1\n"}))
        self.lb.overlay_kernel(judge, self.stamp, self.stamp["kernel_overlay"] + ["scripts/loop_certificate.py"])
        with open(os.path.join(judge, "scripts/test_row.py")) as fh:
            self.assertEqual(fh.read(), KFILES["scripts/test_row.py"])        # N's test judges
        with open(os.path.join(judge, "scripts/under_test.py")) as fh:
            self.assertEqual(fh.read(), "X = 'N+1'\n")                       # over N+1's code
        self.assertFalse(os.path.exists(os.path.join(judge, "scripts/loop_certificate.py")))   # absent at N: not a judge

    def test_the_kernel_check_refuses_a_copy_the_stamp_does_not_vouch_for(self):
        judge = os.path.join(self.d, "judge")
        write(judge, KFILES)
        with self.assertRaisesRegex(ValueError, "does not hold"):
            self.lb.overlay_kernel(judge, self.stamp, ["scripts/not_kernel.py"])
        with open(os.path.join(self.stamp["kernel_dir"], "scripts/test_row.py"), "w") as fh:
            fh.write("tampered\n")
        with self.assertRaisesRegex(ValueError, "the stamp says"):
            self.lb.overlay_kernel(judge, self.stamp, ["scripts/test_row.py"])

    def test_a_pending_judge_never_judges_from_the_frozen_copy(self):
        frozen = os.path.join(self.d, "frozen")
        write(frozen, dict(KFILES, **{"scripts/loop/judge.py": "VERSION = 'N+1, landed, not approved'\n"}))
        stamp = dict(self.stamp, pending_kernel={"scripts/loop/judge.py": {}})
        self.lb.overlay_pending(frozen, stamp)
        with open(os.path.join(frozen, "scripts/loop/judge.py")) as fh:
            self.assertEqual(fh.read(), KFILES["scripts/loop/judge.py"])
        self.lb.overlay_pending(None, stamp)       # no frozen copy (a dry run): nothing to do

    def mirrored_judge(self, changes, runtime=("test_row.py", "loop_certificate.py")):
        """A landing tree (N+1) whose bundle mirrors were generated from its own sources, the way a landing commits
        them: bundle/runtime/loop by bundle_runtime's own generator, bundle/runtime/<name> with a RUNTIME-MANIFEST."""
        judge = os.path.join(self.d, "judge")
        write(judge, dict(KFILES, **changes))
        scripts, runtime_dir = os.path.join(judge, "scripts"), os.path.join(judge, "bundle", "runtime")
        self.br.generate_loop(scripts, runtime_dir)
        files = []
        for name in runtime:
            with open(os.path.join(scripts, name), "rb") as fh:
                body = fh.read()
            with open(os.path.join(runtime_dir, name), "wb") as fh:
                fh.write(body)
            files.append({"path": name, "sha256": __import__("hashlib").sha256(body).hexdigest()})
        doc = {"entry": "brother_run.py", "files": files, "source_describe": "d", "source_revision": "r"}
        with open(os.path.join(runtime_dir, "RUNTIME-MANIFEST.json"), "w") as fh:
            fh.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        return judge, scripts, runtime_dir

    def runtime_entries(self, runtime_dir):
        with open(os.path.join(runtime_dir, "RUNTIME-MANIFEST.json")) as fh:
            return {f["path"]: f["sha256"] for f in json.load(fh)["files"]}

    def test_an_overlaid_file_takes_its_bundle_twin_and_manifest_entry_with_it(self):
        """2026-10-19 PP1.c HELD on bundle-runtime FAIL: the judge tree took N's copy of a pending root and kept N+1's
        bundle/runtime/loop twin and LOOP-MANIFEST, so the row was red by construction while any root was pending."""
        self.br = load_path("bundle_runtime_for_overlay", os.path.join(HERE, "bundle_runtime.py"))
        n1 = {"scripts/loop/judge.py": "import helper\nVERSION = 'N+1'\n", "scripts/test_row.py": "weakened = True\n",
              "scripts/loop/other.py": "changed in N+1, not kernel\n", "scripts/loop_certificate.py": "built in N+1\n"}
        judge, scripts, runtime_dir = self.mirrored_judge(n1)
        self.assertEqual(self.br.check_loop(scripts, runtime_dir), [])        # the landing's mirrors are consistent
        self.lb.overlay_kernel(judge, self.stamp, self.stamp["kernel_overlay"] + ["scripts/loop_certificate.py"])
        # the loop mirror: N's twin and N's manifest entry, while N+1's other.py keeps its own (N's whole manifest
        # would make this red for any landing that changes a loop file outside the kernel)
        self.assertEqual(self.br.check_loop(scripts, runtime_dir), [])
        with open(os.path.join(runtime_dir, "loop", "judge.py")) as fh:
            self.assertEqual(fh.read(), KFILES["scripts/loop/judge.py"])
        # the runtime mirror: N's twin and entry, and a file absent at N leaves with its twin and its entry
        self.assertEqual(self.runtime_entries(runtime_dir), {"test_row.py": sha(KFILES["scripts/test_row.py"])})
        with open(os.path.join(runtime_dir, "test_row.py")) as fh:
            self.assertEqual(fh.read(), KFILES["scripts/test_row.py"])
        self.assertFalse(os.path.exists(os.path.join(runtime_dir, "loop_certificate.py")))

    def test_a_pending_root_over_the_frozen_copy_keeps_the_mirror_whole(self):
        self.br = load_path("bundle_runtime_for_overlay", os.path.join(HERE, "bundle_runtime.py"))
        judge, scripts, runtime_dir = self.mirrored_judge({"scripts/loop/judge.py": "VERSION = 'N+1, pending'\n"}, ())
        self.lb.overlay_pending(judge, dict(self.stamp, pending_kernel={"scripts/loop/judge.py": {}}))
        self.assertEqual(self.br.check_loop(scripts, runtime_dir), [])

    def test_a_mirror_the_landing_left_stale_refuses_and_writes_nothing(self):
        """Each fixture trips one condition: a stale twin, a manifest entry naming other bytes, a manifest this
        lander cannot rewrite byte for byte. Every one refuses before the first write (never a partial overlay)."""
        import shutil
        self.br = load_path("bundle_runtime_for_overlay", os.path.join(HERE, "bundle_runtime.py"))
        n1 = "import helper\nVERSION = 'N+1'\n"

        def stale_twin(runtime_dir):
            with open(os.path.join(runtime_dir, "loop", "judge.py"), "w") as fh:
                fh.write("an older mirror\n")

        def wrong_entry(runtime_dir):
            path = os.path.join(runtime_dir, "RUNTIME-MANIFEST.json")
            with open(path) as fh:
                text = fh.read()
            with open(path, "w") as fh:
                fh.write(text.replace(sha("weakened\n"), "0" * 64))

        def unreadable_form(runtime_dir):
            path = os.path.join(runtime_dir, "loop", "LOOP-MANIFEST.json")
            with open(path) as fh:
                doc = json.load(fh)
            with open(path, "w") as fh:
                fh.write(json.dumps(doc, indent=2))
        for spoil, why in ((stale_twin, "does not match"), (wrong_entry, "manifest names"), (unreadable_form, "rewrite")):
            shutil.rmtree(os.path.join(self.d, "judge"), True)
            judge, scripts, runtime_dir = self.mirrored_judge(
                {"scripts/loop/judge.py": n1, "scripts/test_row.py": "weakened\n"}, ("test_row.py",))
            spoil(runtime_dir)
            with self.assertRaisesRegex(ValueError, why):
                self.lb.overlay_kernel(judge, self.stamp, self.stamp["kernel_overlay"])
            with open(os.path.join(scripts, "loop", "judge.py")) as fh:
                self.assertEqual(fh.read(), n1, spoil.__name__)               # nothing was written
            with open(os.path.join(scripts, "test_row.py")) as fh:
                self.assertEqual(fh.read(), "weakened\n", spoil.__name__)

    def test_a_loop_tool_the_landing_removed_comes_back_with_its_twin_and_entry(self):
        """Astra review 2026-10-20: N+1 deletes a kernel loop source and its twin; N's source returns, so must they."""
        self.br = load_path("bundle_runtime_for_overlay", os.path.join(HERE, "bundle_runtime.py"))
        judge, scripts, runtime_dir = self.mirrored_judge({"scripts/loop/other.py": "kept\n"}, ())
        for path in (os.path.join(scripts, "loop", "judge.py"), os.path.join(runtime_dir, "loop", "judge.py")):
            os.remove(path)
        self.br.generate_loop(scripts, runtime_dir)                          # the landing's manifest, without it
        self.assertEqual(self.br.check_loop(scripts, runtime_dir), [])
        self.lb.overlay_kernel(judge, self.stamp, self.stamp["kernel_overlay"])
        self.assertEqual(self.br.check_loop(scripts, runtime_dir), [])
        with open(os.path.join(runtime_dir, "loop", "judge.py")) as fh:
            self.assertEqual(fh.read(), KFILES["scripts/loop/judge.py"])

    def test_a_package_file_the_landing_removed_comes_back_with_its_twin_and_entry(self):
        """2026-10-22: bundle_runtime mirrors the packages beside the loop tools (scripts/loop/adapters), so the
        overlay's twin rule, a copy of loop_files' own, takes a pkg/name too; a top level only rule left the twin
        out and the bundle check red."""
        self.br = load_path("bundle_runtime_for_overlay", os.path.join(HERE, "bundle_runtime.py"))
        files = {"scripts/loop/pkg/__init__.py": "X = 'N'\n", "scripts/loop/pkg/mod.py": "Y = 'N'\n"}
        n = os.path.join(self.d, "pkg-n"); write(n, files)
        kdir = os.path.join(self.d, "pkg-kernel"); write(kdir, files)
        stamp = {"kernel": {rel: sha(body) for rel, body in files.items()}, "kernel_dir": kdir}
        judge = os.path.join(self.d, "pkg-judge"); write(judge, files)
        scripts, runtime_dir = os.path.join(judge, "scripts"), os.path.join(judge, "bundle", "runtime")
        self.br.generate_loop(scripts, runtime_dir)
        for path in (os.path.join(scripts, "loop", "pkg", "mod.py"), os.path.join(runtime_dir, "loop", "pkg", "mod.py")):
            os.remove(path)
        self.br.generate_loop(scripts, runtime_dir)                          # the landing's manifest, without it
        self.assertEqual(self.br.check_loop(scripts, runtime_dir), [])
        self.lb.overlay_kernel(judge, stamp, ["scripts/loop/pkg/mod.py"])
        self.assertEqual(self.br.check_loop(scripts, runtime_dir), [])
        with open(os.path.join(runtime_dir, "loop", "pkg", "mod.py")) as fh:
            self.assertEqual(fh.read(), "Y = 'N'\n")

    def test_a_folder_or_a_linked_parent_in_the_way_refuses_before_any_write(self):
        """Astra review 2026-10-20: a folder where N has no file raised after earlier writes; a linked parent let a
        write leave the judge tree. Each fixture trips one condition; neither writes anything."""
        import shutil
        self.br = load_path("bundle_runtime_for_overlay", os.path.join(HERE, "bundle_runtime.py"))
        outside = os.path.join(self.d, "outside")

        def folder_where_n_has_none(judge):
            os.makedirs(os.path.join(judge, "scripts", "loop_certificate.py"))

        def linked_parent(judge):           # the landing's own mirror, consistent, but reached through a link
            loop = os.path.join(judge, "bundle", "runtime", "loop")
            shutil.copytree(loop, outside)
            shutil.rmtree(loop)
            os.symlink(outside, loop)
        n1 = "import helper\nVERSION = 'N+1'\n"
        for spoil, why in ((folder_where_n_has_none, "not a regular file"), (linked_parent, "is a link")):
            shutil.rmtree(os.path.join(self.d, "judge"), True)
            shutil.rmtree(outside, True)
            judge, scripts, runtime_dir = self.mirrored_judge({"scripts/loop/judge.py": n1}, ())
            spoil(judge)
            with self.assertRaisesRegex(ValueError, why):
                self.lb.overlay_kernel(judge, self.stamp, self.stamp["kernel_overlay"] + ["scripts/loop_certificate.py"])
            with open(os.path.join(scripts, "loop", "judge.py")) as fh:
                self.assertEqual(fh.read(), n1, spoil.__name__)
            if os.path.isdir(outside):
                with open(os.path.join(outside, "judge.py")) as fh:
                    self.assertEqual(fh.read(), n1, spoil.__name__)               # nothing written through the link

    def test_a_bin_without_a_kernel_stamp_keeps_the_hold(self):
        for doc in ({"schema": 1, "revision": "a" * 40}, {"kernel": {}, "kernel_dir": 3, "kernel_overlay": []}, "x"):
            with open(os.path.join(self.d, ".deploy-stamp.json"), "w") as fh:
                json.dump(doc, fh)
            self.assertIsNone(self.lb.deploy_stamp(self.d), doc)
        with open(os.path.join(self.d, ".deploy-stamp.json"), "w") as fh:
            json.dump(self.stamp, fh)
        self.assertEqual(self.lb.deploy_stamp(self.d)["kernel_overlay"], self.stamp["kernel_overlay"])



class ThePreparedBatchDecision(unittest.TestCase):
    """Execute main's post-commit decision with inert deploy and unwind boundaries.

    The real statements are compiled from main so a green helper cannot hide a caller
    that still unwinds a prepared batch. No repository, push or deploy is performed.
    """

    def decision(self, state="loop", stamp=True, refuse=None):
        import ast
        import contextlib
        import inspect
        import io
        lb = load()
        body = ast.parse(inspect.getsource(lb.main)).body[0].body
        def calls(node, name):
            return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
                       for n in ast.walk(node))
        start = next(i for i, node in enumerate(body) if calls(node, "loop_change"))
        end = next(i for i in range(start, len(body)) if calls(body[i], "push_gate"))
        fn = ast.parse("def decision():\n    pass\n").body[0]
        fn.body = body[start:end] + [ast.Return(value=ast.Constant(value="ready for push"))]
        tree = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
        seen = []
        def step(name):
            def run():
                seen.append(name)
                return name != refuse, name
            return run
        steps = {name: step(name) for name in ("judge", "record", "stage", "restore", "finish")}
        env = dict(vars(lb), frozen="fixture", log=io.StringIO(), stamp={} if stamp else None,
                   ref_lines=lambda *args: "fixture refs", loop_change=lambda *args: (state, "fixture decision"),
                   deploy_steps=lambda *args: steps, head="fixture head", rev=lambda *args: "fixture revision",
                   unwind=lambda *args: seen.append("unwind") or "fixture unwind")
        exec(compile(tree, TARGET, "exec"), env)
        with contextlib.redirect_stdout(io.StringIO()):
            result = env["decision"]()
        return result, seen

    def test_a_prepared_loop_batch_reaches_the_push_boundary(self):
        self.assertEqual(self.decision(), ("ready for push", ["judge", "record", "stage", "finish"]))

    def test_a_refused_judge_still_holds_the_batch(self):
        self.assertEqual(self.decision(refuse="judge"), (1, ["judge", "finish", "unwind"]))

    def test_a_refused_record_still_holds_the_batch(self):
        self.assertEqual(self.decision(refuse="record"), (1, ["judge", "record", "finish", "unwind"]))

    def test_a_refused_stage_restores_and_holds_the_batch(self):
        self.assertEqual(self.decision(refuse="stage"), (1, ["judge", "record", "stage", "restore", "finish", "unwind"]))

    def test_a_loop_batch_without_a_stamp_still_holds(self):
        self.assertEqual(self.decision(stamp=False), (1, ["unwind"]))

    def test_an_unknown_loop_classification_still_holds(self):
        self.assertEqual(self.decision(state="unknown"), (1, ["unwind"]))

    def test_a_non_loop_batch_keeps_its_existing_path(self):
        self.assertEqual(self.decision(state="clear"), ("ready for push", []))


class TheRealDeploySteps(unittest.TestCase):
    """A2.d's steps as main() builds them (deploy_steps), each command faked at sh(), never run for real here."""

    def setUp(self):
        import io, shutil
        self.d = tempfile.mkdtemp(prefix="deploy-steps-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.lb = load()
        kdir = os.path.join(self.d, "kernel")
        write(kdir, {"scripts/gate_ledger.py": "LEDGER = 'N'\n", "scripts/required_fast.sh": "echo gate N\n"})
        self.stamp = {"kernel_dir": kdir, "kernel_overlay": ["scripts/required_fast.sh"],
                      "kernel": {"scripts/gate_ledger.py": sha("LEDGER = 'N'\n"), "scripts/required_fast.sh": sha("echo gate N\n")}}
        self.calls, self.answers, self.log = [], {}, io.StringIO()
        def fake_sh(cmd, log, timeout=2400, env=None, cwd=None, stdin=None):
            self.calls.append(list(cmd))
            for key, (rc, out) in self.answers.items():
                if any(key in str(a) for a in cmd):
                    return subprocess.CompletedProcess(cmd, rc, out, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.lb.sh = fake_sh
        self.lb.BIN = os.path.join(self.d, "bin")
        # never the real copy: a mutant that passes a guard would otherwise make a worktree of the real repository and
        # leave it behind (2026-10-19: one leaked from the sweep at 12h32, removed by hand)
        self.lb.make_copy = lambda paths=None: (_ for _ in ()).throw(self.lb.TreeUnreadable("no real copy in this test"))

    def steps(self):
        self.staged = {}
        return self.lb.deploy_steps(self.stamp, self.log, self.staged)

    def test_a_gate_ledger_copy_the_stamp_does_not_vouch_for_runs_nothing(self):
        with open(os.path.join(self.stamp["kernel_dir"], "scripts/gate_ledger.py"), "w") as fh:
            fh.write("LEDGER = 'written by the build'\n")
        ok, why = self.steps()["judge"]()
        self.assertFalse(ok)
        self.assertIn("the stamp says", why)
        self.assertEqual(self.calls, [])

    def test_a_checkout_that_is_not_clean_before_the_gate_is_refused(self):
        self.answers["--whole-gate-snapshot"] = (2, "")
        ok, why = self.steps()["judge"]()
        self.assertFalse(ok)
        self.assertIn("not clean before the gate", why)
        self.assertEqual(len(self.calls), 1)                 # the snapshot alone: no copy, no gate

    def test_one_deploy_runs_one_whole_gate_from_the_trusted_kernel(self):
        judge = os.path.join(self.d, "judge")
        write(judge, {"scripts/required_fast.sh": "echo candidate gate must not run; exit 1\n"})
        gate = "printf 'PASS    exit 0   fixture\\n'\n"
        write(self.stamp["kernel_dir"], {"scripts/required_fast.sh": gate})
        self.stamp["kernel"]["scripts/required_fast.sh"] = sha(gate)
        self.lb.make_copy = lambda paths=None: judge
        self.answers["--whole-gate-snapshot"] = (0, "a" * 40 + "\n")
        self.answers["deploy_stamped.sh"] = (0, "RESTORE bash r.sh restore /b\nSTAGED /runs/deploy-1\n")
        gates = []

        def boxed(argv, log, timeout, env, cwd=None):
            gates.append((list(argv), cwd))
            # This fixture executes only the tiny stamped gate above. No deploy command runs.
            return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=30)

        self.lb.boxed = boxed
        ok, why = self.lb.deploy_prepare(self.steps())
        self.assertTrue(ok, why)
        self.assertEqual(gates, [(["sh", "scripts/required_fast.sh"], judge)])
        records = [cmd for cmd in self.calls if "--whole-gate-record" in cmd]
        self.assertEqual(len(records), 1)

    def test_the_record_names_generation_ns_gate_and_reads_the_ledgers_exit(self):
        self.answers["--whole-gate-snapshot"] = (0, "a" * 40 + "\n")
        self.assertFalse(self.steps()["judge"]()[0])        # no judge copy: refused, nothing else ran
        for rc, want in ((0, True), (2, False)):
            self.answers = {"--whole-gate-record": (rc, "whole-gate: x")}
            do = self.steps()
            do["state"].update(codes="row\t0\n", start_tree="a" * 40, started=1.0)   # as a passed judge leaves it
            self.assertEqual(do["record"]()[0], want)
        argv = self.calls[-1]
        self.assertIn(os.path.join(self.stamp["kernel_dir"], "scripts", "gate_ledger.py"), argv)
        self.assertEqual(argv[argv.index("--gate-file") + 1], os.path.join(self.stamp["kernel_dir"], "scripts", "required_fast.sh"))

    def test_stage_needs_its_run_and_a_green_parity_over_the_staged_bin(self):
        self.answers["deploy_stamped.sh"] = (0, "RESTORE env X=1 bash r.sh restore /b\nCANARY ok\n")
        self.assertFalse(self.steps()["stage"]()[0])          # no STAGED line: refused
        self.answers["deploy_stamped.sh"] = (0, "RESTORE env X=1 bash r.sh restore /b\nSTAGED /runs/deploy-1\n")
        self.answers["test_loop_tool_parity.py"] = (1, "FAIL drift")
        ok, why = self.steps()["stage"]()
        self.assertEqual((ok, "parity" in why), (False, True))
        self.answers["test_loop_tool_parity.py"] = (0, "PASS")
        self.assertTrue(self.steps()["stage"]()[0])
        self.assertEqual(self.staged, {"run": "/runs/deploy-1", "restore": ["env", "X=1", "bash", "r.sh", "restore", "/b"]})

    def test_publish_and_restore_run_in_pass(self):
        do = self.steps()
        self.staged.update(run="/runs/deploy-1", restore=["bash", "r.sh", "restore", "/b"])
        do["restore"]()
        self.assertEqual(self.calls[-1], ["bash", "r.sh", "restore", "/b", "--in-pass"])
        self.answers["publish"] = (1, "FAIL: parity refused after swap")
        self.assertEqual(do["publish"](), (False, "FAIL: parity refused after swap"))
        self.assertEqual(self.calls[-1][-3:], ["publish", "/runs/deploy-1", "--in-pass"])



class TheApprovalIsCheckedFromTheAnchor(unittest.TestCase):
    """Security review of 2a41a7d85..c2c70acbd (2026-10-19, MEDIUM): activate checked the owner's approval by importing
    cut.py from the very tree it was approving, and HEAD could move between that check and the stage. The check now runs
    hub/main's own copy (the ref the cut pins its approver key to, which only the owner merges) under python -I, the
    source must be clean, and the stage refuses a HEAD other than the one approved."""

    def setUp(self):
        import shutil
        self.d = tempfile.mkdtemp(prefix="approval-anchor-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.D = load_path("deploy_stamped_approval", os.path.join(HERE, "loop", "deploy_stamped.py"))
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t",
                        GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.repo = os.path.join(self.d, "repo")
        refuse = 'def approval_refusal(line, version, head, root):\n    return "NO-DATA: refused by the anchor %s" % version\n'
        write(self.repo, {"scripts/cut.py": refuse, "scripts/loop/x.py": "",
                          "scripts/version_source.py": "def read_source(root):\n    return '9.9.9', None\n"})
        self.git("init", "-q", "-b", "work")
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "anchor")
        self.git("update-ref", "refs/remotes/hub/main", "HEAD")      # the owner merged copy
        write(self.repo, {"scripts/cut.py": 'def approval_refusal(line, version, head, root):\n    return ""\n'})
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "a landing that approves everything")

    def git(self, *argv):
        return subprocess.run(["git"] + list(argv), cwd=self.repo, env=self.env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_the_trees_own_cut_never_decides_its_approval(self):
        from pathlib import Path
        with self.assertRaisesRegex(RuntimeError, "refused by the anchor 9.9.9"):
            self.D.approval_head(Path(self.repo), "cy1.approval.1 whatever")

    def test_the_anchor_approves_and_the_approved_head_is_returned(self):
        from pathlib import Path
        self.git("update-ref", "refs/remotes/hub/main", "HEAD")    # the owner merged the approving cut itself
        self.assertEqual(self.D.approval_head(Path(self.repo), "line"), self.git("rev-parse", "HEAD"))

    def test_a_dirty_source_or_a_missing_anchor_refuses_before_any_check(self):
        from pathlib import Path
        write(self.repo, {"stray.txt": "x"})
        with self.assertRaisesRegex(RuntimeError, "not clean"):
            self.D.approval_head(Path(self.repo), "line")
        os.remove(os.path.join(self.repo, "stray.txt"))
        self.git("update-ref", "-d", "refs/remotes/hub/main")
        with self.assertRaisesRegex(RuntimeError, "hub/main"):
            self.D.approval_head(Path(self.repo), "line")

    def test_the_stage_refuses_a_head_other_than_the_approved_one(self):
        from pathlib import Path
        with self.assertRaisesRegex(RuntimeError, "moved since the approval"):
            self.D.stage_run(Path(self.repo), Path(os.path.join(self.d, "bin")), approved=True, expect_head="0" * 40)


    def test_an_approved_stage_takes_the_approved_commits_bytes_never_the_working_tree(self):
        """Second review (2026-10-19, MEDIUM, validator differential): git status --porcelain can be blinded (an ignored
        file, skip-worktree, assume-unchanged), so an approved stage copied working tree tools the approval never named.
        Approved, the tools and configs come from the archive of the commit; unapproved deploys keep the working tree."""
        from pathlib import Path
        write(self.repo, {"scripts/loop/tool.py": "VERSION = 'approved'\n", "docs/plan/loop-roles.json": "{\"v\": 1}\n"})
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "the approved commit")
        self.git("update-index", "--skip-worktree", "scripts/loop/tool.py", "docs/plan/loop-roles.json")
        write(self.repo, {"scripts/loop/tool.py": "VERSION = 'hidden edit'\n", "docs/plan/loop-roles.json": "{\"v\": 2}\n"})
        self.assertEqual(self.git("status", "--porcelain"), "")              # the blind spot is real here
        tree = Path(self.d) / "committed"
        tree.mkdir()
        self.D.archive_revision(Path(self.repo), "HEAD", tree)
        tools, plan = self.D.staging_sources(Path(self.repo), Path(self.d) / "bin", tree, "HEAD", True)
        with open(os.path.join(str(tools), "tool.py")) as fh:
            self.assertEqual(fh.read(), "VERSION = 'approved'\n")
        with open(os.path.join(str(plan), "loop-roles.json")) as fh:
            self.assertEqual(fh.read(), "{\"v\": 1}\n")
        tools, plan = self.D.staging_sources(Path(self.repo), Path(self.d) / "bin", tree, "HEAD", False)
        self.assertEqual(Path(tools), Path(self.repo) / "scripts/loop")   # an unapproved deploy: as before


    def test_the_revision_is_extracted_as_raw_blobs_whatever_the_trees_attributes_say(self):
        """Third review (2026-10-19, MEDIUM, parser differential): git archive applies the tree's own .gitattributes
        (export-ignore drops a file, export-subst rewrites one) and git show can run a textconv filter, so a committed
        attributes file could change what an approval deploys. The extraction reads raw blobs; modes are kept."""
        from pathlib import Path
        write(self.repo, {".gitattributes": "scripts/loop/gone.py export-ignore\nscripts/loop/subst.py export-subst\n"
                                            "docs/plan/loop-roles.json diff=conv\n",
                          "scripts/loop/gone.py": "KEPT = True\n", "scripts/loop/subst.py": "REV = '$Format:%H$'\n",
                          "scripts/loop/run.sh": "#!/bin/sh\n", "docs/plan/loop-roles.json": "{\"v\": 1}\n"})
        os.chmod(os.path.join(self.repo, "scripts/loop/run.sh"), 0o755)
        self.git("config", "diff.conv.textconv", "sed s/1/9/")
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "attributes that would rewrite an archive")
        tree = Path(self.d) / "raw"
        tree.mkdir()
        self.D.archive_revision(Path(self.repo), "HEAD", tree)
        with open(str(tree / "scripts/loop/gone.py")) as fh:
            self.assertEqual(fh.read(), "KEPT = True\n")
        with open(str(tree / "scripts/loop/subst.py")) as fh:
            self.assertEqual(fh.read(), "REV = '$Format:%H$'\n")
        self.assertTrue(os.access(str(tree / "scripts/loop/run.sh"), os.X_OK))
        _tools, plan = self.D.staging_sources(Path(self.repo), Path(self.d) / "bin", tree, "HEAD", True)
        with open(os.path.join(str(plan), "loop-roles.json")) as fh:
            self.assertEqual(fh.read(), "{\"v\": 1}\n")


    def test_a_committed_link_is_refused_and_nothing_is_written_through_one(self):
        """Fourth review (2026-10-19, HIGH, a write through an extracted link): a revision holding scripts/Link -> a
        folder outside, and scripts/link/x (one path on a case insensitive disk), wrote x outside the tree. The tree
        under scripts and plugin holds no link (measured 0), so a link entry refuses the whole extraction, and every
        file is created exclusively, never through a link, inside the destination."""
        from pathlib import Path
        outside = Path(self.d) / "outside"
        outside.mkdir()
        os.symlink(str(outside), os.path.join(self.repo, "scripts", "Link"))
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "a link out of the tree")
        tree = Path(self.d) / "raw-link"
        tree.mkdir()
        with self.assertRaisesRegex(RuntimeError, "link"):
            self.D.archive_revision(Path(self.repo), "HEAD", tree)
        self.assertEqual(os.listdir(str(outside)), [])
        # and the writer itself never follows a link that is already there
        pre = Path(self.d) / "pre"
        (pre / "scripts").mkdir(parents=True)
        os.symlink(str(outside), str(pre / "scripts" / "loop"))
        with self.assertRaises((RuntimeError, OSError)):
            self.D.write_entry(pre, "scripts/loop/x.py", b"x", b"100644")
        self.assertEqual(os.listdir(str(outside)), [])
        # two entries that meet (a case insensitive disk folds them together) never overwrite: the second refuses
        self.D.write_entry(pre, "scripts/one.py", b"first", b"100644")
        with self.assertRaises(OSError):
            self.D.write_entry(pre, "scripts/one.py", b"second", b"100644")
        with open(str(pre / "scripts" / "one.py"), "rb") as fh:
            self.assertEqual(fh.read(), b"first")
        for bad in ("/etc/x.py", "scripts/../../x.py"):
            with self.assertRaisesRegex(RuntimeError, "outside its tree"):
                self.D.write_entry(pre, bad, b"x", b"100644")


if __name__ == "__main__":
    unittest.main()
