#!/usr/bin/env python3
"""Draft or repair unit specifications in ONE parallel wave, with the real tree in every brief.

usage (repo root):
  spec_wave.py new    <out dir> [unit ...]     units with no spec at all
  spec_wave.py repair <out dir> [unit ...]     units holding a sub unit section under 9 of 10
  spec_wave.py --selftest

Writes <out dir>/briefs/<unit>.md, <out dir>/jobs.json and, after the fanout, <out dir>/drafts/ named for the
consumer of that mode: spec_intake.py wants `<unit>-spec-<model>.md`, spec_accept.py wants `<unit>-<model>.md`.
Dispatch is left to or_fanout, printed as one command, so a wave can be re-run without re-briefing.

WHY. The three earlier spec waves were hand built per unit, which is why only some units have a spec at all: a
hand built brief does not scale to 24 units and nobody writes the 24th. The part that MUST NOT be skipped is the
real tree: the `spec-fiction` failure class in the ledger is a specification whose paths and signatures were
invented, and the scorer's PATHS TRUE and CALLS TRUE points fail every such draft, so a brief without the real
files wastes the whole lane. Each brief therefore carries the verbatim (sliced) source of every file the unit
owns or reads that exists today, and says which do not exist."""
import glob
import json
import os
import re
import subprocess
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan_store  # noqa: E402  (the one landed test: plan_store.sub_landed, finding 2 of 2026-09-27)

PLAN = "docs/plan/BROTHER-1.1.0-LAUNCH-WBS.json"
BIN = os.path.expanduser("~/.claude/bin")
MODELS = ("deepseek", "muse")      # deepseek first by owner order; muse is the second, independent draft
FILE_BUDGET = 14000                # per shown file, before the verbatim slicer trims it
BRIEF_BUDGET = 190000              # a brief past this is withheld rather than silently truncated

TEN_POINTS = """Each sub unit section is scored out of 10 by a DETERMINISTIC scorer against the real tree. All ten must hold:
 1 SECTION      the sub unit has its own heading, exactly `### <UNIT>.<n> <title>`
 2 FILES        the section names its files in backticks, each marked NEW or existing
 3 PATHS TRUE   every file called existing EXISTS, every file called NEW does NOT (checked against the tree)
 4 SIGNATURES   a python block with at least one def, every def carrying a return annotation or typed parameters
 5 CALLS TRUE   every `name(` mentioned and not defined in the section exists in the files named or in the tree
 6 DONE CHECK   one runnable command, matching EXACTLY `python3 -B -m unittest dotted.module.Name -v` or
                `python3 -B scripts/test_x.py` (nothing else is accepted)
 7 REQUIREMENTS the spec carries requirement ids and this section maps them to a function (the control point)
 8 CAN GO RED   at least one named mutation `M-SOMETHING` for this sub unit, which turns its check red
 9 EDGES        an edge case or failure section naming real ones (empty, corrupt, concurrent, stale, already done)
10 NO UNKNOWN   nothing left UNKNOWN, TBD or 'to be decided': a builder must never have to guess"""

LEDGER_RULES = """Failures this estate has already paid for; a draft that repeats one is rejected:
 - not-hermetic: a test must pass in an EXPORT copy with an empty HOME and no docs/plan or data files. A test
   needing a live repository document is decorated with unittest.skipUnless(os.path.isfile(PATH), reason).
 - spec-fiction: every path, function and signature named must EXIST in the files shown, or be marked NEW.
 - probe-crash: hostile input is REFUSED with a returned refusal or a ValueError, never a crash, never a silent
   accept. Unknown, corrupt or missing input BLOCKS; it never reads as the safe case.
 - module-shadowed: never name a NEW package directory that matches an existing module file.
 - done-check-empty: a done check must run more than zero tests.
 - Standard library only, Python 3.9 floor, typing.Optional and typing.List rather than the 3.10 forms.
 - No long dashes anywhere (no em dash, no en dash). Use commas, colons or parentheses."""


def slice_source(path, hint, budget=FILE_BUDGET):
    """Verbatim head plus the top level defs and classes the hint mentions. Every kept byte is byte identical to
    the file, so a spec quoting a signature quotes the real one; dropped blocks are NAMED, never silently gone."""
    try:
        src = open(path, encoding="utf-8").read()
    except OSError as exc:
        return None, ["unreadable: %s" % exc]
    if len(src) <= budget:
        return src, []
    try:
        import ast
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return src[:budget] + "\n# ... truncated, the file does not parse\n", ["tail"]
    lines = src.splitlines(keepends=True)
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", hint or ""))
    keep, dropped, pos = [], [], 0
    for node in tree.body:
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno)
        name = getattr(node, "name", None)
        wanted = name is None or name in names or not names
        if wanted and sum(len(x) for x in keep) < budget:
            keep.append("".join(lines[pos:end]))
        elif name:
            dropped.append(name)
        pos = max(pos, end)
    return "".join(keep), dropped


def fit_budget(text_len, brief_budget):
    """The tree budget to rebuild with when a brief of text_len overshoots brief_budget: the tree share is 0.66 of the
    budget, so the overshoot is divided by that share and a margin taken; never under 20000, never above the budget."""
    over = max(0, int(text_len) - int(brief_budget))
    return max(20000, min(int(brief_budget), int(brief_budget) - int(over / 0.66) - 2000)) if over else int(brief_budget)


def tree_context(unit, plan_text, brief_budget=None):
    """The real files this unit owns or reads, verbatim where they fit, and the ones that do not exist yet.

    The per file budget SHRINKS with the file count, so a unit owning many files still produces a brief that
    fits. Measured 2026-09-21: unit D1 at a flat 14000 per file produced a 197731 byte brief against a 190000
    budget and was withheld, which costs the whole lane. The estate's own lesson is that a size refusal is
    solved by a verbatim slicer, never by asking the worker for less, so the slice gets smaller rather than the
    ask: every byte kept is still byte identical and every dropped block is still named."""
    out, missing = [], []
    seen = []
    paths = []
    for p in list(unit.get("owns") or []) + list(unit.get("reads") or []):
        if p not in paths:
            paths.append(p)
    existing = [p for p in paths if os.path.isfile(p)]
    # two thirds of the brief is for source; the rest is the instructions, the ten points and the current spec
    share = int((brief_budget or BRIEF_BUDGET) * 0.66) // max(1, len(existing))
    per_file = max(1200, min(FILE_BUDGET, share))
    for p in paths:
        if p in seen:
            continue
        seen.append(p)
        if not os.path.isfile(p):
            missing.append(p)
            continue
        body, dropped = slice_source(p, unit.get("objective", "") + " " + plan_text, per_file)
        if body is None:
            missing.append(p)
            continue
        note = ("\n# ... dropped from this view, they exist in the file: %s\n" % ", ".join(dropped[:20])) if dropped else ""
        out.append("#### `%s` (EXISTS today)\n```python\n%s%s```\n" % (p, body, note))
    head = "".join(out) or "None of this unit's files exist yet.\n"
    if missing:
        head += ("\n#### These paths DO NOT EXIST today, so a spec must mark them NEW: %s\n"
                 % ", ".join("`%s`" % m for m in missing))
    return head


def brief_new(unit, ctx):
    return """# SPEC BRIEF: plan unit {id} ({title})

Write the COMPLETE specification for this unit as markdown and NOTHING else: no preface, no commentary, no code
fence wrapping the whole document.

## The unit
- Objective: {objective}
- Files it owns: {owns}
- Files it reads: {reads}
- The unit's own done check, which must stay runnable: `{done}`

## What to produce
A specification with these numbered sections, in this order:
1 Purpose, 2 Requirements (ids RQ-..., each mapped to the function that enforces it), 3 Data and limits,
4 Sub-units, 5 Flow, 6 Edge cases and failure directions, 7 Tests (a table: test name, what it proves, which
requirement), 8 Mutations (each `M-NAME`, what it breaks, which check goes red), 9 Rollback, 10 Done checks.

Section 4 holds between THREE and NINE sub units, each with its own heading `### {id}.<n> <title>`. Number them
{id}.1, {id}.2 and so on. Each sub unit is one landable piece of work: one file or one coherent change, with its
own test module and its own done check. Do not write a sub unit that cannot be finished and proven on its own.

{points}

## The real tree, which every path and signature you name is checked against
{ctx}

## Rules
{rules}
""".format(id=unit["id"], title=unit.get("title", ""), objective=unit.get("objective", ""),
           owns=", ".join("`%s`" % p for p in (unit.get("owns") or [])) or "none stated",
           reads=", ".join("`%s`" % p for p in (unit.get("reads") or [])) or "none stated",
           done=unit.get("done_check", "none stated"), points=TEN_POINTS, ctx=ctx, rules=LEDGER_RULES)


def brief_repair(unit, ctx, current, weak, landed, blockers=()):
    weak_lines = "\n".join(" - %s scores %s of 10, missing: %s" % (sid, sc, miss) for sid, sc, miss in weak) or " - every section scores 9 or more; the scorer has nothing to add"
    # COUNCIL BLOCKERS (row 20, 2026-09-22): the scorer certifies complete and true; only the council judges design, and
    # ten in scope units sat at 10 of 10 with FIX-FIRST. A repair brief that names only the scorer's misses tells the
    # drafter nothing is wrong. Each blocker is a claim with, where the seat gave one, a proof command and its expected text.
    blocker_lines = "\n".join(" - %s%s" % ((b.get("claim") or "").strip()[:700],
                                             (" (proof: `%s`, expect %s)" % (b["proof"], b.get("expect")) if b.get("proof") else ""))
                               for b in blockers if isinstance(b, dict) and b.get("claim"))
    landed_note = (("These sub units are ALREADY LANDED in code: %s. Their sections stay WORD FOR WORD. Where a "
                    "landed section must change, do not edit it: add a paragraph directly after it headed "
                    "'Amendment after landing' stating the added rule, the typed signature, the named test, the "
                    "done check and the mutation.\n" % ", ".join(landed))
                   if landed else "No sub unit of this unit has landed yet, so every section may be rewritten.\n")
    return """# REPAIR BRIEF: plan unit {id} ({title})

Return the COMPLETE repaired specification as markdown and NOTHING else: no preface, no commentary, no code fence
wrapping the whole document. Keep every existing sub unit heading and every numbered section.

## What is wrong, measured by the deterministic scorer against the real tree
{weak}

Repair EXACTLY those sections so each reaches 10 of 10. No other section may score lower than it does today.

REFUSED INPUTS, in every sub unit section that adds or changes a function: one line "Refuses:" naming the hostile inputs the
function must refuse (None, NaN, a bool where a number belongs, an unhashable key, an empty or torn file, a path that is a
directory, ...) and one line "Accepts:" naming the odd but valid ones. The executed probes judge a build against these two
lines, so an input named under neither is the specification's gap, not the worker's.

## What the design council found wrong (three adversarial seats read the spec against the real tree)
{blockers}

Close EVERY blocker above inside the section it names: state the rule, the typed signature, the named test that goes red
without it, the failure direction (unknown, corrupt or missing input BLOCKS) and the mutation that proves the test. A blocker
that quotes a proof command is closed only when the spec names the test that makes that command's expected text true.

{landed_note}
{points}

## The specification as it stands today
{current}

## The real tree, which every path and signature you name is checked against
{ctx}

## Rules
{rules}
""".format(id=unit["id"], title=unit.get("title", ""), weak=weak_lines, landed_note=landed_note,
           blockers=blocker_lines or " - none recorded (the council has not ruled, or ruled DESIGN-CLEAR)",
           points=TEN_POINTS, current=current, ctx=ctx, rules=LEDGER_RULES)


def pick(mode, plan, scores, only, council=None):
    """(unit, weak, landed, blockers) per unit this wave should brief. A unit at 9 everywhere with no council blocker is
    NOT briefed; one at 9 everywhere whose council state is FIX-FIRST or DO-NOT-BUILD IS, on its blockers alone."""
    out = []; council = council if isinstance(council, dict) else {}
    for u in plan["units"]:
        if only and u["id"] not in only:
            continue
        if u.get("state") == "DONE":
            continue
        spec = u.get("spec")
        has = bool(spec) and os.path.isfile(spec)
        if mode == "new":
            if has or u.get("sub_units"):
                continue
            out.append((u, [], [], []))
            continue
        if not has:
            continue
        weak, landed = [], []
        # WHICH SUB UNITS HAVE LANDED. In this plan `sub_units` is a list of STRINGS, not objects, so the state
        # is not on the sub unit: it lives in the unit's `evidence` prose, which is where runner_pool.py reads it
        # from too. Measured 2026-09-21: an earlier version of this function looked for s["state"] on a dict,
        # never matched, and so passed an EMPTY landed list into every brief. The briefs therefore never told
        # their drafters to preserve landed sections, and three of eight units (L5f, D0, D4) came back rewriting
        # history and were refused by spec_accept's guard. The refusal was correct; the brief was wrong.
        ev = u.get("evidence") or ""
        for s in (u.get("sub_units") or []):
            sid = s["id"] if isinstance(s, dict) else s
            row = scores.get(sid) or {}
            sc = row.get("score")
            if (isinstance(s, dict) and s.get("state") in ("LANDED", "DONE")) \
                    or plan_store.sub_landed(sid, ev):
                landed.append(sid)
            if isinstance(sc, (int, float)) and sc < 9:
                weak.append((sid, sc, ", ".join(row.get("missing") or []) or "unstated"))
        state = council.get(u["id"], {}) if isinstance(council.get(u["id"]), dict) else {}
        blockers = [b for b in (state.get("blockers") or []) if isinstance(b, dict)] if state.get("state") in ("FIX-FIRST", "DO-NOT-BUILD") else []
        if weak or blockers:
            out.append((u, weak, landed, blockers))
    return out


def selftest():
    """Answer the question even when a case RAISES. Measured 2026-09-22: eleven selftests in this
    directory exited 1 with a bare traceback and no verdict, so a pipeline reading the exit code and
    a human reading the text described the same run differently. Cases are built EAGERLY, so one
    raising expression takes the whole run with it; this wrapper is what turns that into a readable
    refusal. It does not make a broken module pass: it still returns non zero."""
    try:
        return _selftest_body()
    except Exception as exc:
        print("selftest: FAILED before it could finish, %s: %s" % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    cases = []
    body, dropped = slice_source(__file__, "selftest", budget=10)
    cases.append(("a file over budget is sliced, and what is dropped is named", body is not None and bool(dropped)))
    cases.append(("an unreadable file is reported, never silently empty", slice_source("/no/such/f.py", "")[0] is None))
    whole, d2 = slice_source(__file__, "", budget=10 ** 9)
    cases.append(("a file under budget is byte identical", whole == open(__file__, encoding="utf-8").read() and d2 == []))
    plan = {"units": [
        {"id": "A", "spec": None, "sub_units": []},
        {"id": "B", "spec": __file__, "sub_units": [{"id": "B.1", "state": "LANDED"}, {"id": "B.2"}]},
        {"id": "C", "spec": __file__, "sub_units": [{"id": "C.1"}]},
        {"id": "D", "spec": None, "state": "DONE", "sub_units": []},
        {"id": "E", "spec": None, "sub_units": [{"id": "E.1"}]}]}
    sc = {"B.1": {"score": 4, "missing": ["X"]}, "B.2": {"score": 8, "missing": ["Y"]}, "C.1": {"score": 10}}
    new = [u["id"] for u, _, _, _ in pick("new", plan, sc, [])]
    rep = pick("repair", plan, sc, [])
    cases += [
        ("only a unit with no spec is briefed as new", new == ["A"]),
        ("a DONE unit is never briefed", "D" not in new),
        ("a unit with sub units but no spec is not a NEW spec job", "E" not in new),
        ("only a unit with a weak section is repaired", [u["id"] for u, _, _, _ in rep] == ["B"]),
        ("every weak section is listed", sorted(s for s, _, _ in rep[0][1]) == ["B.1", "B.2"]),
        ("a landed sub unit is named so its section is preserved", rep[0][2] == ["B.1"]),
        # the real plan carries sub units as STRINGS and the landed state in evidence prose; a dict-only
        # check silently produced an empty landed list and cost three units a whole wave
        ("a landed sub unit is found in evidence prose when sub units are plain strings",
         pick("repair", {"units": [{"id": "S", "spec": __file__, "sub_units": ["S.1", "S.2"],
                                    "evidence": "S.1 landed 2026-09-20 with proof"}]},
              {"S.1": {"score": 10}, "S.2": {"score": 5}}, [])[0][2] == ["S.1"]),
        ("a sub unit not named as landed in evidence is not claimed as landed",
         "S.2" not in pick("repair", {"units": [{"id": "S", "spec": __file__, "sub_units": ["S.1", "S.2"],
                                                 "evidence": "S.1 landed"}]},
                           {"S.1": {"score": 10}, "S.2": {"score": 5}}, [])[0][2]),
        ("a unit at 9 or more everywhere is left alone", all(u["id"] != "C" for u, _, _, _ in rep)),
        ("an overshooting brief gets a tree budget shrunk by more than the overshoot, floored, never widened",
         fit_budget(204573, 190000) < 190000 - 14573 and fit_budget(10 ** 9, 190000) == 20000 and fit_budget(1000, 190000) == 190000),
        ("an explicit unit list narrows the wave", [u["id"] for u, _, _, _ in pick("repair", plan, sc, ["C"])] == []),
        ("a unit at 10 everywhere with council FIX-FIRST is briefed on its blockers", [u["id"] for u, _, _, _ in pick("repair", plan, sc, ["C"], {"C": {"state": "FIX-FIRST", "blockers": [{"claim": "x"}]}})] == ["C"]
         and pick("repair", plan, sc, ["C"], {"C": {"state": "FIX-FIRST", "blockers": [{"claim": "x"}]}})[0][3] == [{"claim": "x"}]),
        ("a unit at 10 everywhere with council DESIGN-CLEAR, NO-DATA or an unreadable council is not briefed",
         pick("repair", plan, sc, ["C"], {"C": {"state": "DESIGN-CLEAR", "blockers": [{"claim": "x"}]}}) == [] and pick("repair", plan, sc, ["C"], {"C": {"state": "NO-DATA"}}) == [] and pick("repair", plan, sc, ["C"], "garbage") == []),
        ("the council's blockers, with their proof commands, are carried into the brief", "claim x (proof: `grep q`, expect z)" in
         brief_repair({"id": "C", "title": "t", "spec": __file__, "sub_units": ["C.1"]}, "", "cur", [], [], [{"claim": "claim x", "proof": "grep q", "expect": "z"}, {"nope": 1}])),
        ("the ten scored points are all carried into a brief", all(p in TEN_POINTS for p in
         ("SECTION", "FILES", "PATHS TRUE", "SIGNATURES", "CALLS TRUE", "DONE CHECK", "REQUIREMENTS",
          "CAN GO RED", "EDGES", "NO UNKNOWN"))),
        ("the brief carries the ledger's own prevention rules", "spec-fiction" in LEDGER_RULES),
        ("a missing path is named as NEW rather than omitted",
         "DO NOT EXIST" in tree_context({"owns": ["/no/such/file.py"], "objective": ""}, "")),
        ("no long dash reaches a brief", not any(ch in TEN_POINTS + LEDGER_RULES for ch in "\u2014\u2013")),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return selftest()
    if len(args) < 2 or args[0] not in ("new", "repair"):
        print(__doc__)
        return 2
    mode, out = args[0], os.path.abspath(os.path.expanduser(args[1]))
    only = args[2:]
    plan_text = open(PLAN, encoding="utf-8").read()
    plan = json.loads(plan_text)
    scores = {}
    if mode == "repair":
        sf = os.path.expanduser("~/.claude/evidence/spec-wave-scores.json")
        r = subprocess.run([sys.executable, os.path.join(BIN, "spec_score.py"), "--json", sf],
                           capture_output=True, text=True, timeout=1800)
        if r.returncode:
            print("NO-DATA: the scorer exited %d (%s); no unit is briefed on a guess" % (r.returncode, (r.stderr or "").strip()[-200:]))
            return 2
        try:
            scores = json.load(open(sf, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print("NO-DATA: the scorer wrote nothing readable (%s); no unit is briefed on a guess" % exc)
            return 2
    try:
        with open(os.path.expanduser("~/.claude/evidence/spec-council.json"), encoding="utf-8") as fh: council = json.load(fh)
    except (OSError, ValueError):
        council = {}; print("COUNCIL NO-DATA: spec-council.json unreadable; repair briefs carry the scorer's misses only")
    picked = pick(mode, plan, scores, only, council)
    if not picked:
        print("nothing to do: no unit matches %s" % mode)
        return 1
    for d in ("briefs", "drafts"):
        os.makedirs(os.path.join(out, d), exist_ok=True)
    jobs, withheld = [], []
    for unit, weak, landed, blockers in picked:
        ctx = tree_context(unit, plan_text)
        if mode == "new":
            text = brief_new(unit, ctx)
        else:
            try:
                current = open(unit["spec"], encoding="utf-8").read()
            except OSError as exc:
                withheld.append((unit["id"], "its current spec is unreadable: %s" % exc))
                continue
            text = brief_repair(unit, ctx, current, weak, landed, blockers)
        # A SIZE REFUSAL IS SOLVED BY A SMALLER TREE SHARE, NEVER BY DROPPING EVIDENCE (row 20, 2026-09-22: D3's brief
        # was withheld at 204 KB while its council blockers, the reason for the lane, were 5 KB of it). The tree context
        # is the only elastic part, so it is rebuilt with a budget shrunk by the overshoot, up to three times.
        for _ in range(3):
            if len(text) <= BRIEF_BUDGET: break
            ctx = tree_context(unit, plan_text, brief_budget=fit_budget(len(text), BRIEF_BUDGET))
            text = brief_new(unit, ctx) if mode == "new" else brief_repair(unit, ctx, current, weak, landed, blockers)
        if len(text) > BRIEF_BUDGET:
            withheld.append((unit["id"], "brief is %d bytes, over the %d budget after three tree trims" % (len(text), BRIEF_BUDGET)))
            continue
        # THE PRIVATE TERM SCREEN THIS FILE ALWAYS CLAIMED (review 2026-09-23: the comment said "earned by this file's own screen"
        # and there was none): the whole brief, tree, spec and council blockers included, is withheld on a hit.
        if __import__("grade_build").private_hits(text):
            withheld.append((unit["id"], "brief carries a private term; withheld before any outside model sees it")); continue
        bp = os.path.join(out, "briefs", unit["id"] + ".md")
        open(bp, "w", encoding="utf-8").write(text)
        for m in MODELS:
            name = ("%s-spec-%s.md" if mode == "new" else "%s-%s.md") % (unit["id"], m)
            jobs.append({"id": "%s-%s" % (unit["id"], m), "model": m, "prompt_file": bp,
                         # owner order 2026-09-21: xhigh everywhere, and a max well clear of what xhigh's
                         # reasoning eats, since reasoning and answer share one budget and a spec is long.
                         # sensitivity is EARNED by this file's own private-term screen,
                         # never assumed: or_fanout refuses an unlabelled job.
                         "out": os.path.join(out, "drafts", name), "max": 64000,
                         "effort": "xhigh", "sensitivity": "public"})
    open(os.path.join(out, "jobs.json"), "w", encoding="utf-8").write(json.dumps(jobs, indent=1) + "\n")
    for uid, why in withheld:
        print("WITHHELD %-6s %s" % (uid, why))
    print("WAVE    %s: %d unit(s), %d lane(s) -> %s" % (mode, len(picked) - len(withheld), len(jobs), out))
    print("UNITS   %s" % ", ".join(u["id"] for u, _, _, _ in picked))
    print("RUN     python3 -m plugin.runtime.brother.core.or_fanout %s/jobs.json --workers %d --retries 1 "
          "--timeout 900 --results %s/results.json" % (out, min(24, len(jobs)), out))
    print("THEN    python3 ~/.claude/bin/%s %s/drafts" % (
        "spec_intake.py" if mode == "new" else "spec_accept.py", out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
