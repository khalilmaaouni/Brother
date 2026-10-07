#!/usr/bin/env python3
"""The probe stage must be able to REJECT a build, and what the loop learns must reach the next attempt.

Run: python3 scripts/test_unit_runner_probe_gate.py

Three defects, each reproduced as EXECUTED CODE on 2026-09-21 before it was fixed, each pinned below by a case
that drives real values through the real function rather than by a statement about the source text.

1. THE GATE TESTED SOURCE TEXT, NOT BEHAVIOUR. This file used to walk unit_runner's syntax tree and assert that
   the READY exit sat inside an `if` whose unparsed test string contained the substrings "finds" and "verdict".
   That is a property of the TEXT, so one line upstream defeated it while the gate stayed green. Reproduced:
   replacing `finds = sorted(set(finds))` with `finds = []` left this file printing
       PASS: 1 READY probes-clean exit(s), all inside a guard testing both verdict and finds
       PASS: nothing is stranded after the READY exit
   at exit 0, while the decision it guards, run with those same values, printed
       TOOK: READY (probes clean)  <- with verdict=DIRTY and 1 CRASH finding collected
   The decision now lives in unit_runner.probe_admits(verdict, finds) and is driven here with values.

2. READY-UNPROBED WAS AN UNKNOWN THAT PROCEEDED, and its readers disagreed. runner_pool and loop_done matched
   the PREFIX "READY", so an unprobed build read as "a READY build waits to land": the unit left the work pool
   permanently and the FINISHED verdict was blocked, while land_batch, which compares the status WORD, refused
   to land the very same string. pass_digest already compared the word. All three now do, which makes the state
   one the loop clears by itself: probe_round re-probes the build, and a fresh runner otherwise rebuilds it.

3. THE QUARANTINE REASON WAS WRITTEN FOR A READER THAT DID NOT EXIST. land_batch writes why it dropped a build
   into that run's STATUS "so the next runner knows what to fix". unit_runner read only RUNNER_HINT from the
   environment, set by diag_apply.py alone, which is not in the pass ladder. unit_runner.previous_reason now
   reads that STATUS and the brief carries it, so a repeat attempt is informed instead of identical.

WHY THE CASES ARE SHAPED THE WAY THEY ARE: a fixture that trips two guards proves neither, so every case below
is refusable by exactly ONE guard. A DIRTY verdict carrying a finding is refused only by the findings guard,
because a DIRTY verdict is a usable one; NO-DATA with an empty finding list is refused only by the verdict
guard; a finding list of the wrong type is refused only by the type guard.
"""
import ast
import importlib.util
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LOOP = os.path.join(HERE, "loop")
UNIT_RUNNER = os.path.join(LOOP, "unit_runner.py")
RUNNER_POOL = os.path.join(LOOP, "runner_pool.py")


def _grade_fixture(grade_history):
    import tempfile
    root = tempfile.mkdtemp(prefix="unit-runs-fixture-")
    g = os.path.join(root, "Z.9-010101", "round0", "grades"); os.makedirs(g)
    for name, text in (("a.txt", "x\nPASS\n"), ("b.txt", "FAIL: suite not green\n"), ("c.txt", "FAIL preflight\n"), ("d.txt", "no verdict here\n")):
        with open(os.path.join(g, name), "w") as fh: fh.write(text)
    os.makedirs(os.path.join(root, "Z.8-010101", "round0", "grades"))
    with open(os.path.join(root, "Z.8-010101", "round0", "grades", "e.txt"), "w") as fh: fh.write("PASS\n")
    return grade_history(root, "Z.9")


def _grade_fixture_since(grade_history):
    """2026-09-27: runs last touched at or before the latest new fact are left out of the round cap's history."""
    import tempfile, time
    root = tempfile.mkdtemp(prefix="unit-runs-fixture-")
    g = os.path.join(root, "Z.9-010101", "round0", "grades"); os.makedirs(g)
    with open(os.path.join(g, "b.txt"), "w") as fh: fh.write("FAIL: suite not green\n")
    old = time.time() - 3600
    os.utime(os.path.join(root, "Z.9-010101"), (old, old))
    return grade_history(root, "Z.9", since=old + 1), grade_history(root, "Z.9", since=old - 1)


def load_fn(path, name):
    """The REAL function out of a script that cannot be imported, compiled from that script's own source.

    unit_runner.py and runner_pool.py are scripts: importing either reads sys.argv and opens the live plan, so a
    test cannot import them, and copying the logic here would test the copy. Lifting the one function definition
    out of the parsed module and compiling THAT gives this file the code that actually runs. Returns None when
    the function is absent, which every case below treats as a failure rather than as a skip."""
    try:
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
    except (OSError, SyntaxError):
        return None
    # LIFT EVERY MODULE LEVEL FUNCTION, NOT ONLY THE NAMED ONE. Lifting just the one broke the
    # moment probe_admits was refactored to call a sibling, probe_outcome: the compiled namespace
    # held the caller and not the callee, and the suite died with NameError instead of reporting a
    # verdict. Measured 2026-09-22. A def executes nothing, so compiling the siblings costs nothing
    # and only the path a case actually calls is ever run. This also means the next split of one
    # helper into two does not break this loader again.
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not any(n.name == name for n in fns):
        return None
    # AND THE MODULE LEVEL CONSTANTS THOSE FUNCTIONS READ. probe_outcome reads PROBE_VERDICT, so
    # lifting the functions alone still died, one NameError further along. Only ALL CAPS targets are
    # taken, which is this estate's constant convention and is exactly what excludes the argv
    # parsing and plan opening at module top that make this script unimportable in the first place.
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and all(isinstance(t, ast.Name) and t.id.isupper() for t in n.targets)]
    # __file__ (F2b, 2026-09-26): unit_runner.py's BIN constant is now
    # os.path.dirname(os.path.abspath(__file__)) rather than a fixed string, so lifting it in
    # isolation needs the real name it reads. `path` IS the file being parsed, so the value here
    # is the same one BIN would compute from a real load of unit_runner.py itself.
    ns = {"os": os, "re": re, "sys": sys, "run": "/nonexistent-run-dir", "__file__": path}
    # AND THE TYPING NAMES THE SIGNATURES USE (2026-10-02). ACC3.b gave runner_pool typed signatures
    # (Optional[str]); a def evaluates its annotations, so the lifted copy died with NameError: Optional,
    # and every landing that ran this suite as a neighbour was refused as inherited red, which stopped the
    # loop. `from typing import ...` executes nothing else, so it is lifted with the functions.
    typing_imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "typing"]
    exec(compile(ast.Module(body=typing_imports + consts + fns, type_ignores=[]), path, "exec"), ns)
    return ns[name]


def load_module(path, name):
    """A loop module that IS importable (its body is definitions only), loaded from its path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def guard_names(tree):
    """Every local name bound to a probe_admits(...) call, so the source check below accepts `if admit:`."""
    out = {"probe_admits"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # a direct call, or plan E's `True if probes_off else probe_admits(...)` (2026-10-01): either way the
        # name is the probe gate's answer whenever probes run
        values = [node.value.body, node.value.orelse] if isinstance(node.value, ast.IfExp) else [node.value]
        if any(isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "probe_admits" for v in values):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def source_checks(src):
    """The source half of the gate, kept but no longer the only thing between a finding and a READY build.

    It still earns its place: it is the only check that can see UNREACHABLE code, which was the second half of
    the original defect (the READY-UNPROBED branch and the whole repair round were stranded below an exit that
    ran on every path). What it may no longer do is stand alone."""
    tree = ast.parse(src)
    names = guard_names(tree)
    found = []

    class V(ast.NodeVisitor):
        def __init__(self): self.stack = []
        def visit_If(self, node):
            self.stack.append(ast.unparse(node.test)); self.generic_visit(node); self.stack.pop()
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == "status" and node.args:
                try: text = ast.unparse(node.args[0])
                except Exception: text = ""
                # "probes clean" became a variable when plan E made probes switchable (2026-10-01): the exit is
                # the READY status whose probe word is probe_word, still found by what it is, never by line number
                if "READY %s" in text and ("probes clean" in text or "probe_word" in ast.unparse(node)):
                    found.append(any(any(n in t for n in names) for t in self.stack))
            self.generic_visit(node)

    V().visit(tree)
    stranded = []
    for node in ast.walk(tree):
        for field in ("body", "orelse"):
            blk = getattr(node, field, None)
            if not isinstance(blk, list): continue
            for i, st in enumerate(blk):
                if not ast.unparse(st).startswith("status('READY %s (round %d, grader PASS, probes clean)"): continue
                # sys.exit(0) shares the physical line with status(), so it is a legitimate NEXT statement.
                # What must not exist is anything on a LATER line, which that exit would strand.
                rest = [x for x in blk[i + 1:] if "sys.exit" not in ast.unparse(x) or x.lineno != st.lineno]
                stranded += [x.lineno for x in rest if x.lineno > st.lineno]
    return found, stranded


def unprobed_exit_code(src):
    """The exit code of the silent probe branch (READY for the landing gates since 2026-09-27), or None when it is gone."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "status" and node.args):
            continue
        try: text = ast.unparse(node.args[0])
        except Exception: continue
        if "no answer, so the landing gates decide" not in text: continue
        for other in ast.walk(tree):
            if isinstance(other, ast.Call) and ast.unparse(other.func) == "sys.exit" \
                    and other.lineno == node.lineno and other.args:
                try: return ast.literal_eval(other.args[0])
                except ValueError: return None
    return None


def finds_are_derived(src):
    """True when the finding set handed to the gate is DERIVED from the findings that were collected.

    probe_admits is a pure function, so it is honest about the values it is given and can say nothing about a
    caller that throws the values away. The defeat this whole file exists for is exactly that: `finds = []` one
    line above the guard. So the caller is checked for the one property that makes the defeat visible without
    guessing at its shape. The LAST assignment to `finds` before the decision must mention `finds` on its right
    hand side, which `sorted(set(finds))` does and `[]` does not. Replacement is the defeat; normalisation is
    the legitimate operation. Returns False when there is no such assignment at all."""
    tree = ast.parse(src)
    last = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "finds" for t in node.targets):
            if last is None or node.lineno > last.lineno:
                last = node
    return last is not None and "finds" in ast.unparse(last.value)


def quarantine_fixture(tmp, statuses):
    """A runs directory holding one folder per (name, status text), oldest first by modification time."""
    for i, (name, text) in enumerate(statuses):
        d = os.path.join(tmp, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "STATUS"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        os.utime(d, (1000 + i * 10, 1000 + i * 10))
    return tmp


def cases():
    out = []
    src = open(UNIT_RUNNER, encoding="utf-8").read()

    # ---- defect 1: the decision is a function and it is driven with values ----
    admits = load_fn(UNIT_RUNNER, "probe_admits")
    out.append(("unit_runner exposes probe_admits as a function a test can call", callable(admits)))
    if callable(admits):
        out += [
            ("a DIRTY verdict carrying one CRASH finding does NOT admit a READY build",
             admits("DIRTY", ["CRASH parse_row TypeError: unhashable type: 'dict'"]) is False),
            ("a clean verdict with no findings admits", admits("CLEAN", []) is True),
            ("a repair after NO-PASS starts from the best passing build when one exists", (lambda f: f("/x/best.json", ["/x/r0.json"], isfile=lambda p: True) == ("/x/best.json", True))(load_fn(UNIT_RUNNER, "repair_base"))),
            ("with no passing build yet it starts from the first rejected one, and says so", (lambda f: f(None, ["/x/r0.json", "/x/r1.json"]) == ("/x/r0.json", False))(load_fn(UNIT_RUNNER, "repair_base"))),
            ("a best build that vanished from disk is not offered", (lambda f: f("/x/gone.json", ["/x/r0.json"], isfile=lambda p: False) == ("/x/r0.json", False))(load_fn(UNIT_RUNNER, "repair_base"))),
            ("the script body calls repair_base at the grader rejection", "base, from_best = repair_base(best," in open(UNIT_RUNNER, encoding="utf-8").read()),
            ("a NO-DATA verdict never admits, even with nothing found", admits("NO-DATA", []) is False),
            ("an empty verdict never admits", admits("", []) is False),
            ("a verdict that is not text never admits", admits(None, []) is False),
            ("a finding set that is not a collection never admits", admits("CLEAN", None) is False),
            ("many findings do not admit", admits("DIRTY", ["a", "b", "c"]) is False),
            # 2026-09-27, owner: gates that land. A WRONG-ACCEPT? line is the red team's reading of the spec: advisory.
            ("red team readings alone (WRONG-ACCEPT?) admit, kept as notes beside the build",
             admits("DIRTY", ["WRONG-ACCEPT? parse_row x RETURNED 1"]) is True),
            ("a CRASH beside red team readings still refuses",
             admits("DIRTY", ["WRONG-ACCEPT? parse_row x RETURNED 1", "CRASH parse_row TypeError: boom"]) is False),
            ("a reading beside an unrecognised finding still refuses",
             admits("DIRTY", ["WRONG-ACCEPT? parse_row x RETURNED 1", "junk"]) is False),
        ]
    found, stranded = source_checks(src)
    out += [
        ("the finding set reaching the gate is derived from the findings, never replaced",
         finds_are_derived(src)),
        ("the READY probes-clean exit exists at all", len(found) == 1),
        ("H7: under 30 percent pass over six or more grades the round cap is 3, a smaller caller cap wins", (lambda f: f(5, 1, 6) == 3 and f(5, 3, 6) == 5 and f(2, 0, 10) == 2)(load_fn(UNIT_RUNNER, "adaptive_rounds"))),
        ("H7: fewer than six grades or hostile counts leave the default", (lambda f: f(5, 0, 2) == 5 and f(5, None, "x") == 5 and f(5, 7, 6) == 5)(load_fn(UNIT_RUNNER, "adaptive_rounds"))),
        ("H7: grade history reads PASS and FAIL verdict lines and nothing else", (lambda f: _grade_fixture(f) == (1, 3))(load_fn(UNIT_RUNNER, "grade_history"))),
        ("H7: a run from before the latest new fact is left out of the round cap's history, a later one is counted",
         (lambda f: _grade_fixture_since(f) == ((0, 0), (0, 1)))(load_fn(UNIT_RUNNER, "grade_history"))),
        ("and it sits inside the probe_admits guard", bool(found) and all(found)),
        ("nothing is stranded after the READY exit", not stranded),
    ]

    # ---- defect 2: the three readers agree on the status WORD ----
    waits = load_fn(RUNNER_POOL, "waits_to_land")
    done = load_module(os.path.join(LOOP, "loop_done.py"), "loop_done_under_test")
    digest = load_module(os.path.join(LOOP, "pass_digest.py"), "pass_digest_under_test")
    out.append(("runner_pool exposes its landing predicate as a function", callable(waits)))
    if callable(waits):
        out += [
            ("runner_pool: exactly READY waits to land", waits("READY /x/b.json (round 1)") is True),
            ("runner_pool: READY-UNPROBED does NOT wait to land, so a fresh runner starts on it",
             waits("READY-UNPROBED /x/b.json (round 1)") is False),
            ("runner_pool: a missing status does not wait to land", waits("") is False),
        ]
    out += [
        ("loop_done: exactly READY is a build waiting to land",
         done.ready_builds("/r", listdir=lambda d: ["A.1-101010"],
                           read=lambda p: "READY /x/b.json\n") == {"A.1": "READY"}),
        ("loop_done: READY-UNPROBED is not, so it cannot block the FINISHED verdict forever",
         done.ready_builds("/r", listdir=lambda d: ["A.1-101010"],
                           read=lambda p: "READY-UNPROBED /x/b.json\n") == {}),
        ("pass_digest: exactly READY reaches the landing arguments",
         digest.partition({"A.1": ("READY", "/x/b.json")}, set(), set())[0] == ["/x/b.json"]),
        ("pass_digest: READY-UNPROBED is its own partition and never a landing argument",
         digest.partition({"A.1": ("READY-UNPROBED", "/x/b.json")}, set(), set())[:2] == ([], ["A.1"])),
        # 2026-09-27, owner's yes: a silent probe no longer blocks; the landing's suites, fuzz and spec check decide.
        ("a silent probe writes READY for the landing gates and exits 0",
         unprobed_exit_code(src) == 0),
    ]

    # ---- defect 3: the previous run's rejection reason reaches the brief ----
    reason = load_fn(UNIT_RUNNER, "previous_reason")
    out.append(("unit_runner exposes previous_reason as a function", callable(reason)))
    if callable(reason):
        with tempfile.TemporaryDirectory() as tmp:
            # newest of three is the current run, which has no STATUS yet: only the exclusion is under test here
            quarantine_fixture(tmp, [("D4.c-101010", "EXHAUSTED after 5 rounds"),
                                     ("D4.c-202020", "QUARANTINE dropped at landing, exit 1, fuzz crashes 6"),
                                     ("D4.c-303030", "")])
            out.append(("the newest PREVIOUS run's quarantine reason is returned verbatim",
                        reason("D4.c", runs_dir=tmp, exclude=os.path.join(tmp, "D4.c-303030"))
                        == "QUARANTINE dropped at landing, exit 1, fuzz crashes 6"))
        with tempfile.TemporaryDirectory() as tmp:
            # two rejected runs, both readable: excluding the NEWEST must fall back to the older one, and
            # nothing else in this directory could produce that answer
            quarantine_fixture(tmp, [("D4.c-101010", "EXHAUSTED after 5 rounds"),
                                     ("D4.c-202020", "QUARANTINE dropped at landing, exit 1, fuzz crashes 6")])
            out.append(("this run's own folder is excluded, so a runner never reads the status it just wrote",
                        reason("D4.c", runs_dir=tmp, exclude=os.path.join(tmp, "D4.c-202020"))
                        == "EXHAUSTED after 5 rounds"))
        with tempfile.TemporaryDirectory() as tmp:
            # one readable rejection belonging to ANOTHER sub unit: only the sub unit match can refuse it
            quarantine_fixture(tmp, [("D4.c-101010", "QUARANTINE dropped at landing, exit 1, fuzz crashes 6")])
            out.append(("another sub unit's reason is never borrowed",
                        reason("D9.a", runs_dir=tmp, exclude=os.path.join(tmp, "now")) == ""))
        with tempfile.TemporaryDirectory() as tmp:
            quarantine_fixture(tmp, [("D4.c-101010", "READY /x/b.json (round 2, grader PASS, probes clean)")])
            out.append(("a previous run that was NOT rejected teaches nothing",
                        reason("D4.c", runs_dir=tmp, exclude=os.path.join(tmp, "now")) == ""))
        out.append(("an unreadable runs directory is empty, never an exception",
                    reason("D4.c", runs_dir="/no/such/runs/dir", exclude="/x") == ""))
    # ---- H7.b (REQ-H-CHEAPEST): the planner and the advisor default to the advice line's cheapest valid model ----
    # PRESENCE IS READ BY LIFTING THE FUNCTION OUT OF THE PARSED SOURCE WITH THE LOADER THIS FILE ALREADY SHIPS,
    # NEVER BY A NAME LOOKUP INTO A LIVE MODULE OBJECT: the grader's safety screen refuses that shape inside a build,
    # so no such lookup appears anywhere below. None means the function is absent, which is a FAILING case here,
    # never a skip and never a crash, so every new case is red on the unchanged tree.
    MIX_ADVICE = os.path.join(LOOP, "mix_advice.py")
    cheapest = load_fn(MIX_ADVICE, "cheapest_valid")
    side = load_fn(UNIT_RUNNER, "side_model")
    out.append(("unit_runner exposes side_model", callable(side)))
    out.append(("mix_advice exposes cheapest_valid", callable(cheapest)))

    def lifted_consts(path):
        """Module level ALL CAPS assignments whose right hand side is a literal, read from the same parsed source
        this file already uses. A compiled constant has no literal value and is skipped; a constant that cannot be
        read is left out rather than guessed at. No name is looked up on a live object here."""
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
        except (OSError, SyntaxError):
            return {}
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
                try:
                    consts[node.targets[0].id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):
                    continue
        return consts

    def hostile_side(role, advice, env):
        """Every hostile fixture goes through here: a raise counts as a failure and every result must be the word
        off. A missing function answers RAISED, so an absent side_model can never surface as a raw TypeError."""
        if not callable(side):
            return "RAISED"
        try:
            return side(role, advice, env)
        except (TypeError, ValueError, KeyError, AttributeError):
            return "RAISED"

    def hostile_cheapest(stats, **kw):
        """The same contract on the advice side, so a missing cheapest_valid is a failed case and not a crash."""
        if not callable(cheapest):
            return "RAISED"
        try:
            return cheapest(stats, **kw)
        except (TypeError, ValueError, KeyError, AttributeError):
            return "RAISED"

    def side_wiring(text):
        """(lines of calls that are NOT gated, lines of every BP.plan and RA.advise call). A call is gated when
        some enclosing If or IfExp tests the side model that names it, so a build whose gate was dropped, or whose
        gate tests the wrong side, is caught here rather than by a hopeful reading of the source."""
        ungated, seen = [], []

        class G(ast.NodeVisitor):
            def __init__(self):
                self.stack = []

            def visit_If(self, node):
                self.stack.append(ast.unparse(node.test)); self.generic_visit(node); self.stack.pop()

            def visit_IfExp(self, node):
                self.stack.append(ast.unparse(node.test)); self.generic_visit(node); self.stack.pop()

            def visit_Call(self, node):
                name = ast.unparse(node.func)
                if name in ("BP.plan", "RA.advise"):
                    want = "_plan_model" if name == "BP.plan" else "_repair_model"
                    seen.append(node.lineno)
                    if not any(want in t for t in self.stack):
                        ungated.append(node.lineno)
                self.generic_visit(node)

        G().visit(ast.parse(text))
        return ungated, seen

    if callable(side) and callable(cheapest):
        deep = {"n": 40, "pass": 32, "cost": 0.08, "costed": 1}
        line = {"cheapest": "muse"}
        no_advice = (None, {}, {"cheapest": None})
        for role in ("plan", "repair"):
            out.append(("H7b: the %s side runs on the owner's model when the owner named one" % role,
                        side(role, line, "deepseek") == "deepseek"))
            out.append(("H7b: an unset, empty or blank owner value on the %s side falls to the advice's cheapest" % role,
                        side(role, line, None) == "muse" and side(role, line, "") == "muse" and side(role, line, "   ") == "muse"))
        out.append(("H7b M-H-CHEAPEST-CLAUDE: no advice, an empty advice and a null cheapest all leave the side off, never a Claude model",
                    all(side(r, a, None) == "off" for r in ("plan", "repair") for a in no_advice)
                    and all(side(r, a, None) not in ("sonnet", "claude", "opus", "haiku") for r in ("plan", "repair") for a in no_advice)))
        out.append(("H7b: the owner may say off, and off comes back stripped",
                    side("plan", line, "off") == "off" and side("repair", line, "  off ") == "off"))
        out.append(("H7b: a role that is not text, or is not one of the two sides, is off and cannot raise",
                    all(hostile_side(r, line, "deepseek") == "off" for r in (None, ["plan"], {}, "bogus", "", True, 5))))
        out.append(("H7b: an owner value that is not text is off, never passed on",
                    all(hostile_side("plan", line, v) == "off" for v in (True, 5, ["muse"], b"muse", float("nan"), {"m": 1}))))
        out.append(("H7b: an owner value that is not a bare model name is off and NEVER falls through to the advice",
                    all(hostile_side("plan", line, v) == "off" for v in ("a b", "muse;x", "muse\nx", "x" * 65, "-muse", "."))))
        out.append(("H7b: advice that is not a dict is off, with the owner unset",
                    all(hostile_side("plan", a, None) == "off" for a in ("deepseek", ["cheapest"], 5, float("nan"), True))))
        out.append(("H7b M-H-CHEAPEST-NOFORMAT: the advice's cheapest must be a bare model name, and off is not one",
                    all(hostile_side("plan", {"cheapest": v}, None) == "off"
                        for v in ("", "off", "   ", "a b", "muse;x", 5, True, ["x"], {"m": 1}, None, "x" * 100))))
        out.append(("H7b: the role map names exactly the two owner environment names",
                    lifted_consts(UNIT_RUNNER).get("SIDE_ROLES") == {"plan": "BROTHER_BUILD_PLAN_MODEL", "repair": "BROTHER_REPAIR_ADVISOR_MODEL"}))
        muse_cheap = {"n": 111, "pass": 22, "cost": 0.30, "costed": 1}
        sonnet_dear = {"n": 30, "pass": 27, "cost": 1.00, "costed": 1}
        out.append(("H7b M-H-CHEAPEST-DEAR: the cheapest valid model wins, and a cheaper model under the floor does not qualify",
                    cheapest({"deepseek": deep, "muse": muse_cheap, "sonnet": sonnet_dear}) == "deepseek"))
        out.append(("H7b: a model under the minimum decisions is excluded however cheap it looks",
                    cheapest({"tiny": {"n": 5, "pass": 5, "cost": 0.001, "costed": 1}, "deepseek": deep}) == "deepseek"))
        out.append(("H7b M-H-CHEAPEST-FREE: a NO-DATA cost never wins and is never read as free",
                    cheapest({"nodata": {"n": 40, "pass": 32, "cost": 0.0, "costed": 0}, "deepseek": deep}) == "deepseek"
                    and cheapest({"nodata": {"n": 40, "pass": 32, "cost": 0.0, "costed": 0}}) is None))
        out.append(("H7b: a row whose model name is the string None is not a model",
                    cheapest({"None": {"n": 40, "pass": 32, "cost": 0.001, "costed": 1}, "deepseek": deep}) == "deepseek"))
        out.append(("H7b: an equal cost per valid answer breaks on the higher rate, and then on the name",
                    cheapest({"alpha": {"n": 50, "pass": 32, "cost": 0.08, "costed": 1},
                              "zed": {"n": 40, "pass": 32, "cost": 0.08, "costed": 1}}) == "zed"
                    and cheapest({"alpha": {"n": 40, "pass": 32, "cost": 0.08, "costed": 1},
                                  "zed": {"n": 40, "pass": 32, "cost": 0.08, "costed": 1}}) == "alpha"))
        out.append(("H7b: a hostile stats, minimum or floor returns None, never a model and never a raise",
                    hostile_cheapest(None) is None and hostile_cheapest(["deepseek"]) is None and hostile_cheapest("deepseek") is None
                    and hostile_cheapest({"deepseek": deep}, min_decisions=True) is None
                    and hostile_cheapest({"deepseek": deep}, min_decisions=float("nan")) is None
                    and hostile_cheapest({"deepseek": deep}, min_decisions=None) is None
                    and hostile_cheapest({"deepseek": deep}, floor=True) is None
                    and hostile_cheapest({"deepseek": deep}, floor=float("nan")) is None
                    and hostile_cheapest({"deepseek": deep}, floor="0.6") is None))
        import contextlib, io
        corrupt = {"a": {"n": 40, "pass": 41, "cost": 0.01, "costed": 1},
                   "b": {"n": 40, "pass": -1, "cost": 0.01, "costed": 1},
                   "c": {"n": 40, "pass": True, "cost": 0.01, "costed": 1},
                   "d": {"n": "40", "pass": 32, "cost": 0.01, "costed": 1},
                   "e": "not a dict",
                   "f": {"n": 0, "pass": 0, "cost": 0.01, "costed": 1},
                   "None": {"n": 40, "pass": 32, "cost": 0.001, "costed": 1},
                   "": {"n": 40, "pass": 40, "cost": 0.0, "costed": 1},
                   "nan": {"n": 40, "pass": 32, "cost": float("nan"), "costed": 1},
                   "deepseek": deep}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pick = hostile_cheapest(corrupt)
        out.append(("H7b: corrupt rows are skipped, each named on stderr, never counted and never raising",
                    pick == "deepseek" and err.getvalue().count("skipped:") >= 6))
        ungated, sites = side_wiring(src)
        out.append(("H7b: every BP.plan and RA.advise call sits under a guard naming the side model that gates it",
                    not ungated and len(sites) >= 3 and 'side_model("plan"' in src and 'side_model("repair"' in src
                    and "= side_advice()" in src))
    # the one line of wiring: the reason has to reach the hint the brief is built from
    out.append(("the brief's hint is built from previous_reason, not from the environment alone",
                bool(re.search(r"^hint = .*previous_reason\(", src, re.M))))
    return out


def main():
    rows = cases()
    bad = [n for n, ok in rows if not ok]
    for n in bad:
        print("FAIL: " + n)
    print("%d cases, %s" % (len(rows), "OK" if not bad else "FAILED: %d" % len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
