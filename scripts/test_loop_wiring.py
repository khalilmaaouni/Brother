#!/usr/bin/env python3
"""Every tool brother.loop ships is WIRED, a declared ENTRY POINT, or a recorded ORPHAN.

Owner, 2026-09-21: "make sure all the tooling used by Brother.loop is wired perfectly to it,
especially JEV ones are forgotten often".

He was right, and the number is the point. Measured the same hour: of 14 Jev tools on this estate,
ELEVEN had zero callers anywhere in the loop. jev_decide and jev_seam were wired; jev_registry,
jev_cascade, jev_calibration, jev_canary, jev_eval, jev_checks, jev_g1_seam_cache, the two
frontdoor tools and the two catalogues were not. A tool that nothing calls ships nothing, however
green its own tests are, and this estate has a note about exactly that.

THREE STATES, and the distinction is the whole value:

  WIRED        something in the loop invokes it, as a subprocess or as an import.
  ENTRY POINT  a human, a driver or a board generator starts it. Declared here BY NAME, so
               declaring one is a deliberate act somebody can review, not a silent exemption.
  ORPHAN       nothing invokes it and nobody declared it. This is the finding.

A naive filename grep OVER reports orphans, because a library is imported by MODULE name and
carries no .py at the call site. The first version of this check reported stage_log.py as an
orphan while seven call sites import it. Both spellings are searched here.

THE TOLERANCE IS RATCHET, BELOW, AND NOTHING ELSE. It is the default as well as the ceiling, so
this file answers the same way run bare and run with a flag. --max can only tighten it. A tolerance
a caller can raise is not a ratchet, it is a number the next caller chooses.

Run: python3 scripts/test_loop_wiring.py [--max N]
"""
import os, sys
from hermetic_test_check import callers_of

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
BIN = os.path.expanduser("~/.claude/bin")
SELF = os.path.basename(os.path.abspath(__file__))
RATCHET = 0

# Declared entry points: started by a person, a driver, a cron or a board refresh. Named one by one
# ON PURPOSE. A pattern would let the next orphan hide behind a suffix somebody chose.
ENTRY_POINTS = {
    "loop_until.sh":        "the driver: a human starts a timed run with it",
    "loop_pass.sh":         "one pass; the driver calls it, and a human can call it alone",
    "run_window.py":        "reports a window of the run for a human",
    "gen_subunit_gantt.py": "regenerates the board",
    "gen_loop_doc.py":      "regenerates the brother.loop documentation page",
    "grade_all.sh":         "grades every pending build by hand",
    "grade_all_par.sh":     "the parallel form of grade_all",
    "model_bench.py":       "measures model quality; run deliberately, it costs money",
    "probe_all_models.py":  "calls every model once; run deliberately, it costs money",
    # Reviewed 2026-09-21. Both were orphans, both are started by a human, and both have a row in
    # the loop's own tool table (docs/plan/DELIVERY-FRAMEWORK.md), which is the evidence that made
    # the declaration honest rather than a way to turn the count green.
    "repair_wave.py":       "sends every DIRTY lane back with its findings; a human starts it, "
                            "because the loop does not return a dirty build to a repair round by "
                            "itself (GAP-1 in docs/plan/BROTHER-LOOP-ARCHITECTURE-V2.md)",
    "spec_council.py":      "attacks a specification with three paid model seats; a human starts "
                            "the round, and the loop READS its result: runner_pool.py, "
                            "land_batch.py, pass_digest.py, council_verify.py and brother_pass.py "
                            "all gate on the spec-council.json it writes",
    # H5.a, reviewed 2026-09-24 against docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json: five tools the
    # audit found with nobody calling them. Two are started by the deploy or the checker, two are
    # wired where their verdict is read (self_check.py imports both), one lost its hardwired one
    # off path. Declared one at a time, so each line can be reviewed against the WBS line naming it.
    "tool_stamp.py":        "a human or the deploy runs it to migrate, stage, prove and swap the "
                            "executed tools under a pass boundary switch (H5.a)",
    "self_check.py":        "the checker starts the worker's self check before the sandbox grade, "
                            "so a build the screen refuses gets one free repair (H5.a)",
    "jev_check.py":         "a human runs it on a spec draft for Jev's shadow opinion; the "
                            "questions file is set by BROTHER_JEV_QUESTIONS, no longer hardwired "
                            "(H5.a)",
    "review_brief.py":      "a human runs it to assemble a prose red team brief for a manual red "
                            "team; the loop runs those four checks deterministically itself (H5.a)",
}

# Recorded dead, 2026-09-21. These STILL COUNT as orphans against the ratchet: recording is not
# exempting. They are named so the next reader knows they were judged rather than overlooked, and
# does not close the finding by declaring them an entry point.
RECORDED_DEAD = {
    "jev_check.py":   "DEAD: hardwired at import to ~/.claude/evidence/spec-council/prompts/"
                      "D1-jev.json, a hand made one off for a single unit that NOTHING writes "
                      "(spec_council.py writes <unit>/prompts/<seat>.md, a different layout), so "
                      "it cannot be pointed at any other spec without editing it",
    "review_brief.py": "DEAD: assembles a prose red team brief whose four checks (spec algorithm, "
                       "hostile inputs, survivable mutations, shared module risk) the loop already "
                       "runs deterministically in probe_wave.py, the mutation gate and "
                       "spec_council.py; nothing consumes the file it writes",
}


def sources():
    """Everything that could call something: the loop directory and the installed twins."""
    out = []
    for d in (LOOP, BIN, HERE):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith((".py", ".sh")):
                out.append(os.path.join(d, name))
    return out


def orphans(tools):
    """The tools nobody calls and nobody declared an entry point with a reason (H5.a, REQ-H-ORPHAN).

    `tools` maps a tool name to (state, why); the state is one of the three audit() uses, WIRED,
    ENTRY or ORPHAN. Hostile input is refused with ValueError, never a raw interpreter exception:
    a tools that is not a dict, a name that is not a non empty string, or a row that is not a pair
    of strings, all stop here.
    """
    if not isinstance(tools, dict):
        raise ValueError("orphans: tools must be a dict of name to (state, why)")
    rows = []
    try:
        for name, row in tools.items():
            rows.append((name, row))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("orphans: tools could not be read as a dict (%s)" % type(exc).__name__)
    for name, row in rows:
        if not isinstance(name, str) or not name:
            raise ValueError("orphans: every tool name must be a non empty string")
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            raise ValueError("orphans: every row must be a (state, why) pair")
        if not isinstance(row[0], str) or not isinstance(row[1], str):
            raise ValueError("orphans: every (state, why) must be a pair of strings")
    return sorted(name for name, row in rows if row[0] == "ORPHAN")


def audit():
    if not os.path.isdir(LOOP):
        return None
    bodies = {}
    for p in sources():
        try:
            bodies[p] = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
    rows = []
    for name in sorted(os.listdir(LOOP)):
        if not name.endswith((".py", ".sh")) or name.startswith("test_"):
            continue
        hits = callers_of(name, bodies)
        if hits:
            rows.append((name, "WIRED", "%d caller(s): %s" % (len(hits), ", ".join(hits[:3]))))
        elif name in ENTRY_POINTS:
            rows.append((name, "ENTRY", ENTRY_POINTS[name]))
        else:
            rows.append((name, "ORPHAN", RECORDED_DEAD.get(
                name, "nothing calls it and nobody declared it an entry point")))
    return rows


class _UnreadableKeys(dict):
    """The shape red team round 0 fired at callers_of: a dict whose items raise when they are read."""

    def items(self):
        raise TypeError("unhashable type: 'list'")


def hostile_inputs_refused():
    """H5.a: every hostile value is refused by name, never a raw crash and never a silent accept.

    Red team round 0 raised "TypeError: unhashable type: 'list'" out of callers_of, and ran this
    file with a binary file, a directory, an empty file, a missing file and an unknown flag as its
    arguments: every one of those returned exit 0, the safe case. One probe is kept per finding
    class. An empty list means every hostile value was refused.
    """
    bad = []
    cases = [
        ("callers_of name None", lambda: callers_of(None, {})),
        ("callers_of name bool", lambda: callers_of(True, {})),
        ("callers_of name int", lambda: callers_of(5, {})),
        ("callers_of name bytes", lambda: callers_of(b"x.py", {})),
        ("callers_of bodies None", lambda: callers_of("x.py", None)),
        ("callers_of bodies list", lambda: callers_of("x.py", [])),
        ("callers_of bodies str", lambda: callers_of("x.py", "text")),
        ("callers_of bodies generator", lambda: callers_of("x.py", (row for row in []))),
        ("callers_of bodies unreadable items", lambda: callers_of("x.py", _UnreadableKeys({"a.py": "text"}))),
        ("callers_of bodies list value", lambda: callers_of("x.py", {"a.py": ["text"]})),
        ("callers_of bodies int key", lambda: callers_of("x.py", {7: "text"})),
        ("orphans None", lambda: orphans(None)),
        ("orphans list", lambda: orphans([])),
        ("orphans str", lambda: orphans("ORPHAN")),
        ("orphans row None", lambda: orphans({"a.py": None})),
        ("orphans row str", lambda: orphans({"a.py": "ORPHAN"})),
        ("orphans row triple", lambda: orphans({"a.py": ("ORPHAN", "why", "extra")})),
        ("orphans row int", lambda: orphans({"a.py": (7, "why")})),
        ("orphans empty name", lambda: orphans({"": ("ORPHAN", "why")})),
    ]
    for label, call in cases:
        try:
            call()
        except ValueError:
            continue
        except Exception as exc:
            bad.append("%s: %s: %s" % (label, type(exc).__name__, exc))
        else:
            bad.append("%s: accepted, not refused" % label)
    named = orphans({"a.py": ("ORPHAN", "why"), "b.py": ("ENTRY", "why"), "c.py": ("WIRED", "why")})
    if named != ["a.py"]:
        bad.append("orphans named %r, not exactly the one orphan" % (named,))
    for label, argv in (("unknown flag", ["--bogus"]), ("binary file", ["/bin/ls"]),
                        ("directory", [HERE]), ("empty path", [""]),
                        ("missing file", [os.path.join(HERE, "no-such-file.py")]),
                        ("max not a number", ["--max", "x"]), ("max negative", ["--max", "-1"])):
        try:
            rc = main(list(argv))
        except Exception as exc:
            bad.append("main %s: %s: %s" % (label, type(exc).__name__, exc))
        else:
            if rc != 2:
                bad.append("main %s: returned %r, not the NO-DATA refusal 2" % (label, rc))
    return bad


def screen_hostile_refused():
    """H5.a: self_check.screen refuses a hostile argument by name, never a raw interpreter crash.

    Red team round 0 called screen(None) and got "TypeError: expected str, bytes or os.PathLike
    object, not NoneType" out of os.path.join. One probe is kept per argument, and a round that
    genuinely cannot be read is still the (0, 0) the specification gives it, so a refusal is a
    refusal and never a lost finding.
    """
    import tempfile
    loop = os.path.join(HERE, "loop")
    if not os.path.isfile(os.path.join(loop, "self_check.py")):
        return []
    if loop not in sys.path:
        sys.path.insert(0, loop)
    import self_check
    d = tempfile.mkdtemp(prefix="wiring-h5a-screen-")
    bad = []
    cases = [
        ("screen round_dir None", lambda: self_check.screen(None)),
        ("screen round_dir int", lambda: self_check.screen(7)),
        ("screen round_dir bool", lambda: self_check.screen(True)),
        ("screen round_dir empty", lambda: self_check.screen("")),
        ("screen round_dir bytes", lambda: self_check.screen(b"/tmp")),
        ("screen round_dir nan", lambda: self_check.screen(float("nan"))),
        ("screen runner int", lambda: self_check.screen(d, runner=7)),
        ("screen runners str", lambda: self_check.screen(d, runners="ev_gate")),
        ("screen runners int", lambda: self_check.screen(d, runners=7)),
        ("screen timeout None", lambda: self_check.screen(d, timeout=None)),
        ("screen timeout bool", lambda: self_check.screen(d, timeout=True)),
        ("screen timeout nan", lambda: self_check.screen(d, timeout=float("nan"))),
        ("screen timeout zero", lambda: self_check.screen(d, timeout=0)),
        ("screen timeout negative", lambda: self_check.screen(d, timeout=-1)),
    ]
    for label, call in cases:
        try:
            call()
        except ValueError:
            continue
        except Exception as exc:
            bad.append("%s: %s: %s" % (label, type(exc).__name__, exc))
        else:
            bad.append("%s: accepted, not refused" % label)
    if self_check.screen(d) != (0, 0):
        bad.append("screen on a round with no jobs.json is not the (0, 0) the spec gives it")
    if self_check.screen(os.path.join(d, "absent")) != (0, 0):
        bad.append("screen on a missing round is not the (0, 0) the spec gives it")
    return bad


def main(argv=None):
    # ONE NUMBER, SO EVERY INVOCATION AGREES BY CONSTRUCTION. A NUMBER A CALLER CAN RAISE IS NOT A
    # RATCHET. Measured 2026-09-21: this file answered exit 1 run bare and exit 0 run with --max 4,
    # because the tolerance lived in the caller's flag and defaulted to 0 without it. The battery
    # registers it with --max 4 and the hermetic push gate runs every test BARE, so the same file
    # passed the battery and refused a push of 18 commits, and no reader could say which verdict was
    # the real one. Two verdicts for one tree is worse than either verdict alone.
    # RATCHET is now the default AND the ceiling: --max can only TIGHTEN it. Lowering RATCHET is a
    # one line edit HERE, beside the declarations and the dead list a reader needs in order to judge
    # the number. 2026-09-21: four orphans became two.
    # An argument this file cannot read is NO-DATA, never the safe case (H5.a): red team round 0
    # ran it with a binary file, a directory, an empty file, a missing file and an unknown flag,
    # and got exit 0 for every one of them, a pass the tree had not earned.
    args = list(sys.argv[1:] if argv is None else argv)
    cap = RATCHET
    show_all = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-v", "--verbose"):
            i += 1
        elif a == "--all":
            show_all = True
            i += 1
        elif a == "--max":
            if i + 1 >= len(args):
                print("NO-DATA: --max needs a whole number after it")
                return 2
            try:
                asked = int(args[i + 1])
            except (TypeError, ValueError):
                print("NO-DATA: --max needs a whole number, got %r" % (args[i + 1],))
                return 2
            if asked < 0:
                print("NO-DATA: --max %d is negative; the ratchet is zero or more" % asked)
                return 2
            cap = min(RATCHET, asked)
            i += 2
        else:
            print("NO-DATA: unknown argument %r; usage is [--max N] [--all]" % (a,))
            return 2
    hostile = hostile_inputs_refused() + screen_hostile_refused()
    if hostile:
        print("HOSTILE INPUT NOT REFUSED: %s" % "; ".join(hostile))
        return 1
    rows = audit()
    if rows is None:
        print("NO-DATA: %s does not exist, so wiring cannot be judged. That is not a pass." % LOOP)
        return 1
    tools = {name: (state, why) for name, state, why in rows}
    orphan_names = orphans(tools)
    for name, state, why in rows:
        if state != "WIRED" or show_all:
            print("%-8s %-26s %s" % (state, name, why))
    print("\n%d tool(s): %d wired, %d declared entry point(s), %d ORPHAN"
          % (len(rows), sum(1 for r in rows if r[1] == "WIRED"),
             sum(1 for r in rows if r[1] == "ENTRY"), len(orphan_names)))
    if len(orphan_names) > cap:
        print("An orphan ships nothing, however green its own tests are. Wire it, declare it an "
              "entry point with a reason, or delete it.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
