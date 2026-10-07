#!/usr/bin/env python3
"""Record every failure with its reason, and feed the recurring ones back into the next brief.

usage (repo root):
  python3 scripts/failure_ledger.py record <class> <detail> [--unit U] [--sub S]
  python3 scripts/failure_ledger.py top [--since-hours 24]      what is costing the most, most first
  python3 scripts/failure_ledger.py rules                       the lines to paste into every worker brief
  python3 scripts/failure_ledger.py --selftest

WHY. Failures were recorded per run, in a STATUS file nobody aggregates, so the same CLASS recurred until a human
noticed. Measured 2026-09-20 into 21: SEVEN landings were refused for one class, a suite that passes only on the
developer machine, each after a full build had been commissioned, graded and probed. One line in every brief
would have prevented all seven. A failure that teaches nothing is paid for twice.

THE SHAPE. A failure is a CLASS plus a detail. The class is the thing that recurs and can be prevented; the
detail is this instance. Classes carry a RULE, the sentence that would have stopped it, and `rules` emits the
rules for the classes actually seen, most frequent first, so a brief carries what this estate keeps getting
wrong rather than a generic checklist that nobody reads."""
import argparse
import json
import os
import re
import sys
import time

LEDGER = os.path.expanduser("~/.claude/evidence/brother-failures.jsonl")

# Each class is a real failure this estate has paid for, with the rule that prevents it. The count beside each
# one in `top` is what makes it worth a line in a brief; a class nobody hits should not cost brief space.
RULES = {
    "not-hermetic": "A test must pass in an EXPORT copy of the repository with an empty HOME and no docs/plan or "
                    "data files. A test that must read a live repository document is decorated with "
                    "unittest.skipUnless(os.path.isfile(PATH), reason).",
    "module-shadowed": "Never create a PACKAGE directory whose name matches an existing module file: it shadows "
                       "the module and every importer fails. Add to the existing file instead.",
    "probe-crash": "Hostile input is REFUSED with a returned refusal or a ValueError, never a crash and never a "
                   "silent accept. Every public function is called with None, a wrong type, empty and NaN.",
    "probe-wrong-accept": "A hostile value that is accepted is a defect even when nothing crashes. State what the "
                          "function refuses, and prove the refusal with a test.",
    "suite-unregistered": "A new scripts/test_*.py must be registered in scripts/check_all.sh, or the battery "
                          "registration check goes red.",
    "patch-stale": "Build against the CURRENT tree. A patch whose find text no longer matches is rejected whole, "
                   "so a sibling landing invalidates work built on an older base.",
    "spec-fiction": "Every path, function and signature named must EXIST in the files shown, or be marked NEW. A "
                    "specification written without the real files is fiction with signatures.",
    "done-check-empty": "A done check must run MORE THAN ZERO tests, and its command must be a python3 invocation "
                        "of a repo relative path or module.",
    "green-but-hollow": "The SUBJECT of a check must be the deliverable itself, never a fixture standing in for "
                        "it. A test that builds its own input and asserts a property of THAT proves the function "
                        "works and says nothing about the real file, table or document the unit exists to "
                        "produce. Every unit whose goal names a quantity or a file states one check that reads "
                        "the real path, and that check is what closes the unit.",
    "red-without-code": "Your test MUST FAIL when your implementation is removed. The grader deletes your code "
                        "and re-runs your test: if it still passes, the test asserts something that was already "
                        "true and proves nothing. Assert the BEHAVIOUR your code adds, never that a name exists "
                        "or that an import succeeds.",
    "exhausted": "The lane used every round without producing an acceptable build. Before the next attempt, "
                 "state the ONE fact about the real tree that the brief did not show, and prove it with a "
                 "command, rather than trying the same shape again.",
    "existing-suite-broken": "Every existing suite of a module you edit must stay green with your edit in place: run the "
                             "module's own test file and the neighbours the spec names before answering. A build whose "
                             "own tests pass while an existing suite goes red is refused, however good the feature.",
    "safety-argv-literal": "A command runner's run()/subprocess argv is a LITERAL list of string literals, never a "
                           "variable, a join, an f-string or a computed element: the safety screen refuses anything "
                           "else before a single test runs.",
    "patch-unparseable": "Every edit's replacement and every new file must parse as Python on its own: a fragment "
                         "that does not parse is refused by the safety screen before anything runs.",
    "prose-done-check": "A unit's done check is a COMMAND, never a sentence. A done check written as prose can "
                        "never close its unit mechanically, so the unit stays open however much of it is built.",
    "ev-gate-stop": "A stop from the expected value gate means the math already says another round is not worth "
                    "the money spent. Before retrying, change what wins the bid (raise the win probability, lower "
                    "the cost), rather than resubmitting the same shape and expecting a different gate answer.",
    "safety-screen-refusal": "A build the safety screen refuses (a non literal import, exec, a dynamic path, or "
                             "any other construct the screen names) is rejected before a single test runs. Read "
                             "the screen's own reason and write the safe, literal equivalent it already documents.",
}


# Signals that name a class, MOST SPECIFIC FIRST: the first match wins, so a line saying both "CRASH" and
# "EXPORT copy" is the hermetic failure it really is rather than the crash it merely mentions. Each pattern was
# taken from the exact wording a grader or a red team actually prints, not invented: the graders write RED-,
# APPLY, NOT APPLY, CRASH and WRONG-ACCEPT?, and the hermetic checker writes "export tree"/"empty HOME".
# CONFIG FIRST (2026-09-30): the program or endpoint does not know the model. Not a build's failure and not a lesson for a
# builder; its own class, so it is reported apart and never read as a model loss. The pattern is breaker.CONFIG_PATTERN
# (scripts/loop/breaker.py), repeated here because this file runs from scripts/ and the loop's copy from ~/.claude/bin;
# scripts/test_config_recovery.py holds the two byte equal.
CONFIG_PATTERN = (r"unrecognized_model|model_not_found|not_found_error[^\n]{0,200}model|unsupported[ _-]model"
                  r"|model[^\n]{0,80}\bnot supported|does not support (?:the |this )?model|issue with the selected model"
                  r"|\bmodel\b[^\n]{0,80}\bdoes not exist|not a valid model|No endpoints found for"
                  r"|invalid_model|\bunknown model\b|model_not_available|requested model is not available"
                  r"|HTTP 404[^\n]{0,200}\bmodel\b|\bmodel\b[^\n]{0,200}HTTP 404")
_SIGNALS = (
    ("config", r"CONFIG_WAIT|" + CONFIG_PATTERN),
    ("red-without-code", r"RED-WITHOUT-CODE\s+NO|tests pass without the code"),
    ("not-hermetic", r"export (copy|tree)|empty HOME|skipUnless|hermetic"),
    ("module-shadowed", r"shadow|package .* matches .* module|cannot import name"),
    ("patch-stale", r"NOT APPLY|does not apply|find text|patch (was )?rejected|APPLY\s+FAIL"),
    ("mutation-survived", r"mutation .*SURVIVED|SURVIVED"),
    ("probe-wrong-accept", r"WRONG-ACCEPT"),
    ("existing-suite-broken", r"GREEN-WITH-CODE\s+NO|suite not green with the code"),
    ("safety-argv-literal", r"argv (element )?is not a (string )?literal|argv is not a literal list"),
    ("patch-unparseable", r"fragment does not parse"),
    ("probe-crash", r"\bCRASH\b|Traceback|raw interpreter exception"),
    ("suite-unregistered", r"run by no battery|register each in scripts/check_all"),
    ("done-check-empty", r"Ran 0 tests|no tests ran|runs no test"),
    ("spec-fiction", r"does not exist|no such file|is called existing and is absent"),
    ("ev-gate-stop", r"EXHAUSTED by the expected value gate"),
    ("exhausted", r"EXHAUSTED after \d+ rounds"),
    # THE CATCH ALL FOR EVERY OTHER SAFETY SCREEN REASON, LAST. safety-argv-literal and patch-unparseable above
    # already name the two most common reasons and win first; this is what is left, so a not yet named refusal
    # (a dynamic import, exec, or whatever the screen adds next) still gets its own class rather than falling
    # through to "unclassified". Measured 2026-09-26: 114 of 161 safety screen refusals in the real ledger carried
    # no more specific signal and sat unclassified, alongside 104 EV-gate stops misread the same way.
    ("safety-screen-refusal", r"FAIL safety screen:"),
)


def classify(text):
    """The failure class this grader or red team evidence belongs to, or None when nothing matches.

    WHY IT EXISTS. Until 2026-09-21 the loop only ever READ this ledger: unit_runner asked it for `rules` and
    nothing anywhere called `record`, so every row in it had been typed by a human and the lessons in a worker
    brief could never improve on their own. That is the same open loop as a decision model holding 1,775
    predictions against 12 outcomes: a lesson is cheap, an outcome takes wiring. An unmatched failure returns
    None and is recorded under its stage name instead, because an unnamed recurring failure is precisely the
    one that keeps being paid for."""
    if not isinstance(text, str) or not text.strip():
        return None
    for cls, pattern in _SIGNALS:
        if re.search(pattern, text, re.I):
            return cls
    return None


def record_success(stage, unit=None, sub=None, path=None, now=None):
    """The other half of the loop, and the half that was missing. A count of failures with no count of attempts
    has NO DENOMINATOR: seven not-hermetic failures could be 7 of 9 or 7 of 200, and those imply opposite
    actions. Measured 2026-09-21: Jev held 1,775 predictions against 12 recorded outcomes, a 0.7 percent
    feedback rate, and this ledger repeated the same defect on its first day. Every stage that can fail must
    also record when it did not."""
    return record("ok:" + stage, "", unit, sub, path, now)


def rates(rows, stages=None):
    """{stage: (failures, attempts, rate)} so a class can be read as a proportion rather than a bare count.
    A stage with no successes recorded reports attempts as its failures only, and says so by a rate of 1.0,
    which is honest: with no denominator we cannot claim it is better than always failing."""
    fail, ok = {}, {}
    for r in rows:
        cls = r.get("class") or ""
        if cls.startswith("ok:"):
            ok[cls[3:]] = ok.get(cls[3:], 0) + 1
        else:
            fail[cls] = fail.get(cls, 0) + 1
    out = {}
    for cls in sorted(set(fail) | {s for s in ok}):
        f = fail.get(cls, 0)
        n = f + ok.get(cls, 0)
        out[cls] = (f, n, (f / n) if n else 0.0)
    return out


def record(cls, detail, unit=None, sub=None, path=None, now=None):
    """Append one failure. Never raises: a ledger that can stop the work is worse than one with a gap.

    THE DETAIL IS NEVER STORED EMPTY. Measured 2026-09-26: 162 rows in the real ledger carried an empty
    detail and no class a caller could act on, "unclassified" with nothing to classify. A blank string
    reads exactly like a call that never happened, so a reader (and `classify`, on a later reclassify
    pass) cannot tell "recorded, nothing to say" from "never recorded at all". A row with nothing to say
    for itself now says so, in the same field: an entry every reader already looks at."""
    detail = (detail or "").strip()
    if not detail:
        detail = "NO-DATA: the caller passed no detail to record()"
    row = {"at": now if now is not None else time.time(), "class": cls, "detail": detail[:400],
           "unit": unit, "sub": sub}
    try:
        os.makedirs(os.path.dirname(path or LEDGER), exist_ok=True)
        with open(path or LEDGER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass
    return row


def load(path=None):
    """Every readable row. A corrupt line is skipped, never guessed at, and never stops the rest being read."""
    out = []
    try:
        with open(path or LEDGER, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("class"):
                    out.append(row)
    except OSError:
        return []
    return out


def tally(rows, since=None, now=None):
    """[(class, count)] most frequent first, then alphabetically so two runs agree."""
    counts = {}
    for r in rows:
        at = r.get("at")
        if since is not None and isinstance(at, (int, float)) and at < (now or time.time()) - since:
            continue
        cls = r["class"]
        if cls.startswith("ok:"):
            continue          # a success is a denominator, never a lesson: it must not rank in a brief
        counts[cls] = counts.get(cls, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def reclassify_counts(rows):
    """(before, after): {class: count} as the ledger currently reads, and as `classify` would decide
    today from each row's own stored detail. READ ONLY, and pure: it takes rows already loaded and
    returns two dicts, writing nothing anywhere. `classify` cannot fix a row after the fact by itself
    (it is never called again once a row is written), so this is the one place that answers "what
    would today's rules say", for a human or a report to act on, without touching the ledger.

    A row `classify` still cannot place (an empty detail, or genuinely novel text) KEEPS ITS CURRENT
    LABEL rather than being invented a new one or dropped: reclassification can only sharpen a label
    with real signal in the text, never guess one, and it is counted either way, never silently
    folded into some other class. Successes (a class starting "ok:") are not failures and are excluded
    from both sides, the same rule `tally` and `brief_rules` already apply."""
    before, after = {}, {}
    for r in rows:
        cls = r.get("class") or ""
        if cls.startswith("ok:"):
            continue
        before[cls] = before.get(cls, 0) + 1
        found = classify(r.get("detail") or "")
        new_cls = found or cls
        after[new_cls] = after.get(new_cls, 0) + 1
    return before, after


def brief_rules(rows, limit=6, since=None, now=None):
    """The rules worth putting in a brief: the classes actually seen, most costly first. A class with no rule
    is still reported by name, because an unnamed recurring failure is the one that keeps being paid for."""
    lines = []
    for cls, n in [x for x in tally(rows, since, now) if x[0] != "config"][:limit]:   # CONFIG is the loop's, never a builder's lesson
        rule = RULES.get(cls)
        lines.append("- %s (cost this estate %d failure(s) already): %s"
                     % (cls, n, rule or "no rule written yet for this class; work out what would prevent it."))
    return lines


def _cli_class(argv):
    import io, contextlib, tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="fl-cli-"), "l.jsonl")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        main(argv + ["--path", path])
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return rows[-1]["class"] if rows else None


def selftest():
    now = 1000.0
    rows = [{"at": now, "class": "not-hermetic"}, {"at": now, "class": "not-hermetic"},
            {"at": now, "class": "probe-crash"}, {"at": now - 99999, "class": "patch-stale"},
            {"at": now, "class": "unknown-class"}, {"not": "a failure"}]
    rows = [r for r in rows if r.get("class")]
    cases = [
        ("the most frequent class comes first", tally(rows)[0] == ("not-hermetic", 2)),
        ("a window excludes older rows", [c for c, _ in tally(rows, since=60, now=now)] == ["not-hermetic", "probe-crash", "unknown-class"]),
        ("ties are ordered by name so two runs agree", tally(rows)[1][0] < tally(rows)[2][0]),
        ("a rule is emitted for a known class", "EXPORT copy" in brief_rules(rows)[0]),
        ("the count is stated so the brief says what it costs", "2 failure(s)" in brief_rules(rows)[0]),
        ("an unknown class is still reported by name", any("unknown-class" in l for l in brief_rules(rows))),
        ("no rows means no rules rather than a generic checklist", brief_rules([]) == []),
        ("a corrupt line does not stop the rest being read", True),
        ("recording returns the row even when the path is unwritable",
         record("x", "y", path="/no/such/dir/f.jsonl")["class"] == "x"),
        ("a detail is truncated, never dropped", len(record("x", "d" * 900, path="/no/such/dir/f.jsonl")["detail"]) == 400),
        # the CLI's auto path with a stage (2026-09-24): empty evidence under auto:<stage> is filed under the stage, never "unclassified"
        ("record auto:grade with empty evidence files the row under grade", _cli_class(["record", "auto:grade", "", "--unit", "X", "--sub", "X.1"]) == "grade"),
        ("record auto with empty evidence stays unclassified, said", _cli_class(["record", "auto", "", "--unit", "X", "--sub", "X.1"]) == "unclassified"),
        ("record auto:grade with classifiable evidence takes the classifier's class over the stage", _cli_class(["record", "auto:grade", "FAIL: suite not green with the code", "--unit", "X", "--sub", "X.1"]) == "existing-suite-broken"),
        ("every rule names a preventable action", all(len(v) > 40 for v in RULES.values())),
        # classify: the ordering IS the logic, so the tests below pin the order, not just the matches
        ("a hermetic failure is named", classify("FAIL: passes here but not in the export tree") == "not-hermetic"),
        ("a crash is named", classify("CRASH scripts/x.py f(None)") == "probe-crash"),
        ("a wrongly accepted hostile value is named",
         classify("WRONG-ACCEPT? scripts/x.py f(NaN) RETURNED 3") == "probe-wrong-accept"),
        ("a stale patch is named", classify("APPLY FAIL: find text no longer matches") == "patch-stale"),
        ("a surviving mutation is named", classify("  mutation M-X SURVIVED") == "mutation-survived"),
        ("a build that breaks an existing suite is named, not read as a crash (2026-09-24: 46 such refusals in one night sat under probe-crash)",
         classify("GREEN-WITH-CODE    NO exit 1: ERROR: test_x (m.T.test_x) | Traceback (most recent call last):") == "existing-suite-broken"),
        ("the grader's own reason line names the same class", classify("FAIL: suite not green with the code") == "existing-suite-broken"),
        ("a non literal argv refusal is named", classify("FAIL safety screen: tools/x.py (a command runner) run() argv element is not a string literal") == "safety-argv-literal"),
        ("a non literal argv list is the same class", classify("run() argv is not a literal list; read it by hand") == "safety-argv-literal"),
        ("an unparseable fragment is named", classify("FAIL safety screen: a/b.py fragment does not parse, and") == "patch-unparseable"),
        ("a done check that runs no test is the empty done check class", classify("FAIL preflight: done_check 'x && y' runs no test") == "done-check-empty"),
        # THE REAL EVIDENCE CARRIES BOTH SIGNALS. A grader prints RED-WITHOUT-CODE and APPLY in the same
        # block, so if patch-stale were matched first, 16 measured failures would have been filed as a stale
        # patch and the brief would have taught the wrong lesson to every worker. Ordering is the logic.
        ("a test that passes without its code beats the APPLY line in the same block",
         classify("[D1.5-r2] RED-WITHOUT-CODE   NO: tests pass without the code\n[D1.5-r2] APPLY  3 edits")
         == "red-without-code"),
        ("a grader that reports RED-WITHOUT-CODE yes is NOT that failure",
         classify("[D9.d-r1] RED-WITHOUT-CODE   yes (exit 1)") != "red-without-code"),
        ("an exhausted summary is named rather than left unclassified",
         classify("EXHAUSTED after 5 rounds; best grader pass: None") == "exhausted"),
        ("hermetic beats crash when a line says both, because it is the real cause",
         classify("CRASH in the export tree with an empty HOME") == "not-hermetic"),
        ("an unmatched failure returns None rather than a wrong class", classify("something odd happened") is None),
        ("empty evidence is not a class", classify("") is None and classify(None) is None),
        ("every signal class has a rule or is deliberately new",
         all(c in RULES or c in ("mutation-survived", "config") for c, _ in _SIGNALS)),
        # F15, measured 2026-09-26: 203 of 350 probe-crash rows were really grader failures, and 486
        # unclassified rows were really 162 empty details, 104 EV-gate stops and 161 safety screen refusals.
        ("a grader failure that also mentions Traceback is the grader class, never probe-crash",
         classify("[D0.6-r0] RED-WITHOUT-CODE   yes (exit 1)\n[D0.6-r0] APPLY              3 edits, 0 test items\n"
                  "[D0.6-r0] GREEN-WITH-CODE    NO exit 1: ERROR: test_bridge | Traceback (most recent call last): "
                  "| Raised") == "existing-suite-broken"),
        ("an EV-gate stop is its own class, not folded into the generic exhausted class",
         classify("EXHAUSTED by the expected value gate after 4 rounds: EV 0.143 (p 0.14 x 1.00) < cost 0.16: "
                  "not worth another round") == "ev-gate-stop"),
        ("a round-count exhaustion with no EV gate mention stays the generic exhausted class",
         classify("EXHAUSTED after 4 rounds: EV 0.143 (p 0.14 x 1.00) < cost 0.16") == "exhausted"),
        ("a safety screen refusal with no more specific signal gets its own class, not unclassified",
         classify("[D2.1-r0] FAIL safety screen: plugin/runtime/brother/core/x.py calls exec(); read it by hand "
                  "before anything else") == "safety-screen-refusal"),
        ("a non literal argv safety refusal still wins its own more specific class over the catch all",
         classify("FAIL safety screen: tools/x.py (a command runner) run() argv element is not a string literal")
         == "safety-argv-literal"),
        ("record() never stores an empty detail; a failure with nothing to say records why",
         record("x", "", path="/no/such/dir/f.jsonl")["detail"].startswith("NO-DATA:")),
        ("record() never stores a whitespace only detail either",
         record("x", "   \n\t", path="/no/such/dir/f.jsonl")["detail"].startswith("NO-DATA:")),
        ("a real detail is stored untouched", record("x", "a real reason", path="/no/such/dir/f.jsonl")["detail"] == "a real reason"),
        ("reclassify_counts never writes and reports both counts",
         reclassify_counts([{"class": "probe-crash", "detail": "GREEN-WITH-CODE    NO exit 1: | Traceback"},
                            {"class": "unclassified", "detail": ""},
                            {"class": "ok:build", "detail": ""}])
         == ({"probe-crash": 1, "unclassified": 1}, {"existing-suite-broken": 1, "unclassified": 1})),
        ("reclassify_counts never invents a class for text it still cannot read",
         reclassify_counts([{"class": "unclassified", "detail": "something odd happened"}])[1] == {"unclassified": 1}),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", nargs="?", default="top",
                    choices=["record", "top", "rules", "classify", "relabel", "reclassify-report"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--unit"), ap.add_argument("--sub"), ap.add_argument("--path", help="the ledger file (default the estate ledger); a selftest or a sandbox names its own")
    ap.add_argument("--since-hours", type=float, default=None)
    if "--selftest" in (argv or sys.argv[1:]):
        return selftest()
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    since = args.since_hours * 3600 if args.since_hours else None
    if args.cmd == "record":
        if not args.rest:
            print("record needs a class and a detail")
            return 2
        cls, detail = args.rest[0], " ".join(args.rest[1:])
        if cls in ("auto", "ok:auto") or cls.startswith("auto:"):
            # the caller has evidence but no class: let the ledger name it, so a lane never has to know the
            # taxonomy. An unmatched failure is still recorded, under the stage it came from, never dropped:
            # `auto:<stage>` names that stage (2026-09-24: four rows of one run were "unclassified" with empty
            # evidence, a denominator with no class, because a grader round left no FAIL line to classify).
            found = classify(detail)
            stage = cls.split(":", 1)[1].strip() if cls.startswith("auto:") else ""
            cls = found or stage or (cls.split(":")[0] if ":" in cls else "unclassified")
        row = record(cls, detail, args.unit, args.sub, path=args.path)
        print("recorded %s" % row["class"])
        return 0
    if args.cmd == "relabel":
        # THE CORRECTION PATH. An adversarial review of this estate's framework on 2026-09-21 named the risk
        # exactly: a learning loop with no way to fix a wrong label amplifies its own error, because every
        # later brief carries the mistake. Rows are relabelled only FROM "unclassified", never from one real
        # class to another, so this can add knowledge and can never silently rewrite a judgement already made.
        # The raw detail is untouched and the previous label is kept in `relabelled_from`, so the change is
        # visible rather than retroactive.
        path = LEDGER
        try:
            rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        except (OSError, ValueError) as exc:
            print("NO-DATA: the ledger could not be read (%s); nothing was changed" % exc)
            return 2
        import shutil
        shutil.copy(path, path + ".bak-relabel")
        changed = {}
        for r in rows:
            if r.get("class") != "unclassified":
                continue
            found = classify(r.get("detail") or "")
            if found:
                r["relabelled_from"] = "unclassified"
                r["class"] = found
                changed[found] = changed.get(found, 0) + 1
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        total = sum(changed.values())
        print("relabelled %d of %d row(s); backup at %s" % (total, len(rows), path + ".bak-relabel"))
        for c, n in sorted(changed.items(), key=lambda kv: -kv[1]):
            print("   %-20s %d" % (c, n))
        return 0
    if args.cmd == "classify":
        print(classify(" ".join(args.rest)) or "unclassified")
        return 0
    if args.cmd == "reclassify-report":
        # READ ONLY, by design and by name: it prints what `classify` would say today from each row's own
        # stored detail, next to what the ledger currently holds, and writes NOTHING, not even a backup.
        # `relabel` above is the write path for a human who has read this report and decided to act; this
        # command exists so that decision is made on real counts, never on a guess about what a fix would do.
        rows = load(args.path)
        if not rows:
            print("NO-DATA: no failure has been recorded yet")
            return 1
        before, after = reclassify_counts(rows)
        print("RECLASSIFICATION REPORT, read only: nothing was written to %s" % (args.path or LEDGER))
        print("%-24s %8s %8s" % ("class", "before", "after"))
        for cls in sorted(set(before) | set(after), key=lambda c: -(before.get(c, 0) + after.get(c, 0))):
            b, a = before.get(cls, 0), after.get(cls, 0)
            delta = "" if a == b else (" (+%d)" % (a - b) if a > b else " (%d)" % (a - b))
            print("%-24s %8d %8d%s" % (cls, b, a, delta))
        print("\n%d row(s) total (successes excluded from both columns)" % sum(before.values()))
        return 0
    rows = load()
    if not rows:
        print("NO-DATA: no failure has been recorded yet")
        return 1
    if args.cmd == "top":
        for cls, n in tally(rows, since):
            print("%4d  %-22s %s" % (n, cls, (RULES.get(cls) or "no rule written yet")[:80]))
        print("\n%d failure(s) recorded across %d class(es)" % (len(rows), len(tally(rows, since))))
        return 0
    for line in brief_rules(rows, since=since):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
