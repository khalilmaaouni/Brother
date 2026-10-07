#!/usr/bin/env python3
"""brief_check: refuse a build brief BEFORE any worker is paid to build against it. Owner order 2026-09-24 ("improve and
enforce clear briefs per units and from Epic"), measured the same day over 249 grade texts: 103 of 223 refusals named
unshown or sliced out source in their own UNKNOWNS, and 22 more ran a done_check that runs no test. Every one of those
is a property of the brief, visible before dispatch, and nothing refused it; eight workers were paid per round instead.
usage (repo root): brief_check.py <unit> <sub> <spec.md> <brief.md>
exit 0 PASS, 1 REFUSED (one line per property, the reason on the line), 2 NO-DATA (an input could not be read: never a pass).
Five properties, each decided on evidence, each with its own line:
 B1 NAMED SHOWN    every file the sub unit's section names in backticks that exists on disk is shown, never NOT SHOWN
 B2 IMPORTS SHOWN  every repository module that a section named test file imports is shown, never NOT SHOWN; a test the
                   section labels NEW (`NEW: p`, `p (NEW)`, `p` NEW, `p` (NEW), NEW `p`) and that is absent has nothing
                   to show yet, and a present one is always opened and read, whatever its label
 B3 BUDGET         the brief is under BUDGET bytes (the dispatcher refuses 200000 and the runner appends up to 16000 after)
 B4 DONE CHECK     the landing gate would run the section's done check (spec_check.gate_refusal: one command, never chained)
 B5 FROM THE WBS   the brief states the unit's WBS row (initiative, unit, objective, closes when) with no NO-DATA field
The path rules here mirror build_brief.paths_in on purpose and independently: a checker that reads the builder's own
claim of what it showed would pass whatever the builder believed. SPEC_PATH is the one reader of a named path and
labelled_paths the one reader of its NEW or existing label (build_brief.stale_claims and spec_score read it here too).
Read only; nothing here writes."""
import os
import re
import sys

# The dispatcher refuses a prompt over 200000 bytes, and the RUNNER appends after the builder is done: its rules, the
# learned hint from the last rejected build, the shadow checker's note. Measured 2026-09-24 14:2x on D1.8: 12459 bytes
# appended to a 196311 byte brief, refused at 208770. So the builder's and this gate's budget reserve 16000 for that.
BUDGET = 184000
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # the copy beside this file, and only that one
try:
    import spec_check as DONE_CHECK                    # ONE verdict: B4 is the landing gate's own gate_refusal (2026-09-29)
except ImportError:
    DONE_CHECK = None                                  # no gate beside this file: B4 is NO-DATA, never a pass
# ONE READER OF "A FILE THIS SPEC NAMES" (2026-09-27): the brief builder, this gate, the pool's touch sets and the spec
# scorer each carried their own copy and they drifted four ways. A label may sit before the path (`NEW: p`, L0.1 on
# 2026-09-21) or after it (`p (NEW)`, all nine paths of D15, which left D15-D's touch set unknown and its brief without
# the files it names). Each reader keeps only its own scope filter.
SPEC_PATH = re.compile(r"`(?:[A-Za-z]+:\s*)?([A-Za-z0-9_./-]+\.(?:py|sh|json|md|jsonl))(?:\s+\((?:NEW|existing)\))?`")
_PATH = SPEC_PATH
_INSIDE_LABEL = re.compile(r"^`(NEW|existing):|\((NEW|existing)\)`$")   # a label inside the backticks, case sensitive
_AFTER_NEW = re.compile(r"\s+\(?NEW\b")                                  # `p` NEW, `p` (NEW)
_IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\b|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))", re.M)
_FILE = re.compile(r"^===== FILE: (.+?) =====\n(\[NOT SHOWN)?", re.M)
_WBS = re.compile(r"^THIS UNIT, FROM THE WBS \((.*?)\):\n((?:  .*\n)+)", re.M)


def section_of(spec, sub):
    """The one section for sub, or None (NO-DATA). A spec or a sub that is not text is NO-DATA too: an
    unknown or corrupt input must never read as the safe case, and must never raise a raw interpreter exception."""
    if not isinstance(spec, str) or not isinstance(sub, str):
        return None
    m = re.search(r"^#{2,4} %s\b.*?(?=^#{2,4} |\Z)" % re.escape(sub), spec, flags=re.M | re.S)
    return m.group(0) if m else None


def named_paths(section):
    """Backticked paths in one section text. A section that is not text is REFUSED by name (ValueError), never
    answered with an empty list: an empty list is the safe answer (this section names no file), and a corrupt
    input must never read as the safe case, nor crash the regex engine with a raw TypeError."""
    if not isinstance(section, str):
        raise ValueError("named_paths: section is not a str: %s" % type(section).__name__)
    return [q for q in dict.fromkeys(_PATH.findall(section)) if not q.startswith("docs/plan/specs/") and not q.endswith(".jsonl")]


def labelled_paths(text):
    """[(path, label, start, end)] for every SPEC_PATH match in text, in order, duplicates kept.
    path: the raw capture (SPEC_PATH group 1). start/end: the match span of the backticked token.
    label: "NEW" or "existing" when the token carries it inside (`NEW: p`, `existing: p`, `p (NEW)`,
    `p (existing)`), else "NEW" when the text right after the token matches \\s+\\(?NEW\\b or the text
    right before it matches \\bNEW\\s+$, else "". Case sensitive: `new: p` is "". Non str: ValueError."""
    if not isinstance(text, str):
        raise ValueError("labelled_paths: text is not a str: %s" % type(text).__name__)
    out = []
    for m in SPEC_PATH.finditer(text):
        start, end = m.span()
        inside = _INSIDE_LABEL.search(m.group(0))
        if inside:
            label = inside.group(1) or inside.group(2)
        elif _AFTER_NEW.match(text, end):
            label = "NEW"
        else:
            # `NEW p` read backwards from the token: whitespace, then NEW as a whole word (never RENEW)
            j = start
            while j > 0 and text[j - 1].isspace():
                j -= 1
            word_before = j >= 4 and (text[j - 4].isalnum() or text[j - 4] == "_")
            label = "NEW" if j < start and text[max(0, j - 3):j] == "NEW" and not word_before else ""
        out.append((m.group(1), label, start, end))
    return out


def test_imports(rel):
    """Repository files a test imports: a package path, a file beside the test, or one under scripts/ (where this
    estate's plugin tests insert into sys.path). A name that resolves to nothing is dropped, never guessed."""
    if not isinstance(rel, str):
        raise ValueError("test_imports: path is not a str: %s" % type(rel).__name__)
    try:
        with open(rel, "rb") as fh:
            data = fh.read()
    except (OSError, ValueError) as exc:
        raise ValueError("test_imports: unreadable: %s" % exc)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("test_imports: not utf-8: %s" % exc)
    out = []
    for m in _IMPORT.finditer(text):
        names = [m.group(1)] if m.group(1) else [n.strip() for n in m.group(2).split(",")]
        for name in names:
            parts = name.split(".")
            for cut in range(len(parts), 0, -1):
                hit = next((c for c in (os.path.normpath(os.path.join(pre, *parts[:cut]) + ".py")
                                        for pre in ("", os.path.dirname(rel), "scripts"))
                            if os.path.isfile(c) and c != rel), None)
                if hit:
                    if hit not in out:
                        out.append(hit)
                    break
    return out


def named_test_readable(path):
    """(True, "") when the file opens and parses; (False, reason) otherwise. A path that is not text, a missing
    file, a directory, bytes that are not utf-8 and a syntax error each refuse WITH the reason, never a raw
    interpreter exception: the gate refuses on False and prints that reason (REQ-H-UNREAD)."""
    if not isinstance(path, str):
        return False, "path is not a str: %s" % type(path).__name__
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except (OSError, ValueError) as exc:
        return False, "unreadable: %s" % exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return False, "not utf-8: %s" % exc
    try:
        compile(text, path, "exec")
    except SyntaxError as exc:
        return False, "syntax error: %s" % exc
    return True, ""


def shown_in(brief):
    """path -> True when its FILE block carries content, False when the block is a NOT SHOWN marker. A brief that
    is not text is REFUSED by name (ValueError), never answered with an empty map: an empty map is the safe
    answer (nothing was shown), and a corrupt input must never read as the safe case."""
    if not isinstance(brief, str):
        raise ValueError("shown_in: brief is not a str: %s" % type(brief).__name__)
    return {os.path.normpath(m.group(1)): not bool(m.group(2)) for m in _FILE.finditer(brief)}


def check(sub, spec, brief):
    """Every property answered, PASS or REFUSED with its reason; None when the section itself is missing
    (NO-DATA). A sub, a spec or a brief that is not text is NO-DATA (None) as well: an unknown or corrupt input
    BLOCKS, it never reads as a pass."""
    if not all(isinstance(x, str) for x in (sub, spec, brief)):
        return None
    section = section_of(spec, sub)
    if section is None:
        return None
    shown = shown_in(brief)
    rows = []

    def verdict(prop, missing, what):
        rows.append((prop, "PASS") if not missing else (prop, "REFUSED: %s not shown: %s" % (what, ", ".join(missing))))

    named_all = [os.path.normpath(p) for p in named_paths(section)]
    named = [p for p in named_all if os.path.isfile(p)]
    verdict("B1 NAMED SHOWN", [p for p in named if not shown.get(p)], "section named file(s)")
    tests = [p for p in named_all if os.path.basename(p).startswith("test_") and p.endswith(".py")]
    # REQ-H-UNREAD: every test the section NAMES is opened and parsed, whether or not a file of that name is there
    # to be listed. named_test_readable is the one place that decides; its reason is refused on the B2 line, by
    # name, and a test that vanished between listing and reading is refused by the same line, never skipped.
    unreadable = []
    imported = []
    # A test the section marks NEW ("`path` NEW") is the builder's to write: when no file is there yet it has no imports
    # to show (2026-09-25: five sub units were WITHHELD before any build because their NEW test "did not exist").
    # An EXISTING test that cannot be read is still refused below (REQ-H-UNREAD).
    # THE SPECS SPELL "NEW" FIVE WAYS (2026-09-27: D14.6, L5a-3 and L5b.3 WITHHELD every pass on the three outside
    # spellings "`p` NEW", "`p` (NEW)" and "NEW `p`"; 2026-09-28: L0.2 WITHHELD four times on `NEW: p`, the label INSIDE
    # the backticks, freed only by a hand written FACTS line). labelled_paths is the one reader of every spelling; a
    # section may name the same new test later by its bare file name, which is the same new file.
    new_tests = {os.path.normpath(p) for p, label, _, _ in labelled_paths(section) if label == "NEW"}
    new_names = {os.path.basename(p) for p in new_tests}
    for t in tests:
        if (t in new_tests or (os.sep not in t and t in new_names)) and not os.path.lexists(t):
            continue
        ok, reason = named_test_readable(t)
        if not ok:
            unreadable.append("%s (%s)" % (t, reason))
            continue
        try:
            imported.extend(test_imports(t))
        except ValueError as exc:
            unreadable.append("%s (%s)" % (t, exc))
    verdict("B2 IMPORTS SHOWN", [q for q in dict.fromkeys(imported) if not shown.get(os.path.normpath(q))] + unreadable, "module(s) a named test imports")
    size = len(brief.encode("utf-8"))
    rows.append(("B3 BUDGET", "PASS") if size <= BUDGET else ("B3 BUDGET", "REFUSED: %d bytes, over %d" % (size, BUDGET)))
    # The landing gate's own verdict on the section (spec_check.gate_refusal: done_check() then grade_build.OK_CMD). Until
    # 2026-09-29 B4 passed when ANY python3 line matched the regex, so a fenced block the gate joins with && passed here
    # and its green build was dropped at landing.
    gate = DONE_CHECK.gate_refusal(spec, sub) if DONE_CHECK else None
    if DONE_CHECK is None:
        rows.append(("B4 DONE CHECK", "NO-DATA: spec_check.py (or the grade_build.py it reads) is not beside brief_check.py, so the landing gate's rule cannot be read"))
    elif not gate:
        rows.append(("B4 DONE CHECK", "PASS"))
    else:
        rows.append(("B4 DONE CHECK", "REFUSED: no done_check the grader runs (one `python3 -B -m unittest x` or `python3 -B scripts/test_x.py`): %s" % gate))
    w = _WBS.search(brief)
    fields = dict((k.strip(), v.strip()) for k, v in (line.split(":", 1) for line in w.group(2).splitlines() if ":" in line)) if w else {}
    bad = [k for k in ("initiative", "objective", "closes when") if not fields.get(k) or fields[k].startswith("NO-DATA")] if w else ["block"]
    bad = bad or [k for k in fields if k.startswith("unit ") and fields[k].startswith("NO-DATA")]
    rows.append(("B5 FROM THE WBS", "PASS") if not bad else ("B5 FROM THE WBS", "REFUSED: the brief does not state the unit's WBS row: %s" % ", ".join(bad)))
    return rows


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not isinstance(argv, (list, tuple)) or not all(isinstance(a, str) for a in argv):
        print("NO-DATA: usage: brief_check.py <unit> <sub> <spec.md> <brief.md>")
        return 2
    flags = [a for a in argv if a.startswith("-")]
    if flags or len(argv) != 4:
        print("NO-DATA: unknown flag(s) or wrong argument count: %s"
              % (", ".join(flags) if flags else len(argv)))
        return 2
    unit, sub, spec_path, brief_path = argv
    try:
        with open(spec_path, encoding="utf-8") as fh:
            spec = fh.read()
        with open(brief_path, encoding="utf-8") as fh:
            brief = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        print("NO-DATA: %s" % exc)
        return 2
    rows = check(sub, spec, brief)
    if rows is None:
        print("NO-DATA: %s has no section for %s" % (spec_path, sub))
        return 2
    for prop, v in rows:
        print("%-17s %s" % (prop, v))
    refused = [p for p, v in rows if v != "PASS"]
    print("BRIEF %s %s: %s" % (unit, sub, "PASS" if not refused else "REFUSED on " + ", ".join(refused)))
    return 0 if not refused else 1


if __name__ == "__main__":
    sys.exit(main())
