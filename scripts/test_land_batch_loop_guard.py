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
import importlib.util, os, sys, tempfile

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


if __name__ == "__main__":
    sys.exit(main())
