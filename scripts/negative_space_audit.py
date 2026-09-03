#!/usr/bin/env python3
"""negative_space_audit: R27.2, the generated negative-space contract audit.

WHY THIS EXISTS (docs/plan/HARDENING-2026-08-30-CODEX.md, mechanism 2). The
Codex consultation named "observer monoculture": every check this estate had
before the hardening program asks a question someone already thought to ask.
The team and a rival tool kept finding a different class of miss, the
FORGOTTEN CONTRACT FIELD: a durable record type this system creates, that
nobody ever asked the same twelve questions of that every other record type
gets asked. The fix is not one more hand-written check; it is a GENERATOR
that finds every durable noun mechanically and asks it the SAME questions,
so a forgotten field is a missing cell in a table, not a blind spot nobody
noticed.

WHAT COUNTS AS A DURABLE NOUN, mechanically, never by hand-curated list (a
hand-curated list is exactly the monoculture this exists to break). A module
under scripts/ or bundle/runtime/ (never test_*.py) is a noun module when it
BOTH persists state to disk (an `open(..., "w"/"a")` or `json.dump(`) AND
owns at least one lifecycle-shaped function (a name whose "_"-split tokens
hit one of the CREATE/LIST/SHOW/RESUME/CLOSE verb sets below). The noun's
display name is the module's own filename with a known storage-suffix
stripped (_store, _record, _ledger, _lane, _bridge, _loop, _lab, _dial,
_board, _guard, _gate, _sheet); nothing is added or renamed by a human after
the fact.

THE TWELVE QUESTIONS, asked of every noun the same way (the plan's own
wording): create, list, show, resume, close, origin, opened time, closed
time, subject binding, freshness, malformed disposition, producer, consumer.
Each is answered by a MECHANICAL search of the module's own source (a
matching function name or a field-shaped regex hit) or, for consumer, a
grep of every OTHER scanned module for an import of this one. A cell that
matches nothing is NO-DATA, printed by name, never silently dropped and
never guessed into an answer.

EXEMPTIONS are the only way a NO-DATA cell stops being reported as one, and
they are a reviewed table (EXEMPTIONS below), not a flag: each entry names
the noun, the question, and the reason a human accepted, so a future run
cannot blanket-exempt its way to green.

Exit 0 only when every cell is ANSWERED or EXEMPT. Exit 2 (this estate's
NO-DATA convention, scripts/check_all.sh's own contract) when at least one
cell is NO-DATA and --strict was not given; every one of those cells is
still named in the output, never swallowed by the exit code. Exit 1 only
with --strict and at least one NO-DATA cell, or on a hard failure to even
scan the tree.

Python 3, standard library only. No network. No em or en dashes.
"""
import argparse
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = (os.path.join(ROOT, "scripts"), os.path.join(ROOT, "bundle", "runtime"))
NODATA = "NO-DATA"

QUESTIONS = (
    "create", "list", "show", "resume", "close", "origin", "opened_time",
    "closed_time", "subject_binding", "freshness", "malformed_disposition",
    "producer", "consumer",
)

# Verb tokens a function name's "_"-split parts are checked against, for the
# five lifecycle questions a function can directly answer. Deliberately
# generous (this is what makes the extractor see real code's real verbs
# rather than one hand-picked spelling), but every hit still names the exact
# function and line it matched.
_VERB_QUESTIONS = {
    "create": {"create", "acquire", "claim", "start", "begin", "new", "init",
              "register", "add", "record", "open"},
    "list": {"list", "enumerate", "iter", "find", "glob", "scan", "collect"},
    "show": {"show", "get", "read", "load", "view", "display", "report"},
    "resume": {"resume", "continue", "reopen", "renew", "restore"},
    "close": {"close", "release", "finish", "complete", "done", "integrate",
             "reconcile", "end"},
}

# Field-shaped regexes for the questions that live in DATA, not in a
# function name. Matched against the module's own source text.
_FIELD_QUESTIONS = {
    "origin": r"\b(origin|source|created_by)\b",
    "opened_time": r"\b(created_at|opened_at|started_at|start_time)\b|time\.time\(\)",
    "closed_time": r"\b(closed_at|ended_at|finished_at|end_time|done_at|integrated_at)\b",
    "subject_binding": r"\b(work_id|unit_id|subject|digest|sha256|\bsha\b)\b",
    "freshness": r"\b(ttl|expire|expiry|stale|freshness|max_age|lease)\b",
    "malformed_disposition": r"except\s*\(?\s*\(?\s*(json\.JSONDecodeError|ValueError|OSError)|"
                             r"\bNO-DATA\b|\bNODATA\b",
    "producer": r"\b(producer|producer_version|written_by)\b",
}

_STORAGE_SUFFIXES = ("_store", "_record", "_ledger", "_lane", "_bridge",
                     "_loop", "_lab", "_dial", "_board", "_guard", "_gate",
                     "_sheet", "_run", "_pack")

_WRITE_RE = re.compile(r'\.open\(\s*["\'][wa]|open\([^)]*["\'][wa]b?["\']|json\.dump\(')

# This audit does not audit itself: its own module contains the write-shaped
# regex above as a STRING LITERAL, which matches itself, and helper names
# like find_nouns and _load happen to hit the same verb tokens real writers
# do. That is a genuine false positive in the write-detection heuristic
# (this file persists nothing), not a real durable noun, so it is excluded
# by name rather than left in to inflate the grid with a self-referential
# row. Never silently extended to hide a real finding elsewhere.
_SELF_EXCLUDE = {"negative_space_audit"}

# ---------------------------------------------------------------------------
# EXEMPTIONS: a reviewed table, never a bare flag. Each entry names the noun,
# the question, and the reason a human accepted the gap. Empty on purpose at
# R27.2's first run: the brief that ordered this audit is explicit that a
# fresh run finding real NO-DATA is the honest and expected outcome, and a
# blanket exemption written to make the first run green would defeat the
# whole point of building a negative-space observer.
# ---------------------------------------------------------------------------
EXEMPTIONS = {
    # ("noun", "question"): "reason a human reviewed and accepted this gap",
}


def _module_name(path):
    return os.path.basename(path)[:-3]


def _noun_name(modname):
    for suf in _STORAGE_SUFFIXES:
        if modname.endswith(suf):
            return modname[: -len(suf)]
    return modname


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
    except OSError as e:
        print("negative_space_audit: skipping %s, unreadable (%s)" % (path, e),
             file=sys.stderr)
        return None, None
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return source, None
    return source, tree


def _top_level_funcs(tree):
    """[(name, lineno), ...] for every top-level def, never nested ones (a
    nested helper is an implementation detail of the function that owns it,
    not a second lifecycle entry point)."""
    if tree is None:
        return []
    return [(n.name, n.lineno) for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _verb_hit(funcs, verbs):
    """First (name, lineno) whose "_"-split tokens intersect verbs, or None."""
    for name, lineno in funcs:
        tokens = set(name.lower().split("_"))
        if tokens & verbs:
            return name, lineno
    return None


def find_nouns(dirs=SCAN_DIRS):
    """Every durable-noun module under dirs: mechanical, no hand list.

    A module qualifies when it persists to disk (a write-shaped call in its
    own source) AND owns at least one lifecycle-shaped top-level function.
    Returns a list of {"noun", "module", "path", "funcs", "source"} sorted
    by noun name, one row per module (two modules stripping to the same noun
    stay two rows; nothing here merges across files, because they are
    different producers even if the English word is the same).
    """
    rows = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            if _module_name(os.path.join(d, fn)) in _SELF_EXCLUDE:
                continue
            path = os.path.join(d, fn)
            source, tree = _load(path)
            if source is None:
                continue
            if not _WRITE_RE.search(source):
                continue
            funcs = _top_level_funcs(tree)
            if not any(_verb_hit(funcs, verbs) for verbs in _VERB_QUESTIONS.values()):
                continue
            modname = _module_name(path)
            rows.append({
                "noun": _noun_name(modname),
                "module": modname,
                "path": path,
                "funcs": funcs,
                "source": source,
            })
    return sorted(rows, key=lambda r: (r["noun"], r["module"]))


def _consumers(modname, path, dirs):
    """Other scanned modules, in the same dirs this run itself scanned, that
    import this module by name. A durable record with no consumer is a real
    finding (something is written that nothing reads), never assumed
    benign."""
    hits = []
    pattern = re.compile(r"^\s*(from\s+%s\s+import|import\s+%s\b)"
                         % (re.escape(modname), re.escape(modname)), re.MULTILINE)
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py") or _module_name(os.path.join(d, fn)) == modname:
                continue
            other_path = os.path.join(d, fn)
            if other_path == path:
                continue
            try:
                with open(other_path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as e:
                print("negative_space_audit: skipping %s as a consumer candidate, unreadable (%s)"
                     % (other_path, e), file=sys.stderr)
                continue
            if pattern.search(text):
                hits.append(_module_name(other_path))
    return sorted(set(hits))


def answer_cell(row, question, dirs=SCAN_DIRS):
    """(verdict, detail) for one (noun, question) cell. verdict is one of
    ANSWERED, EXEMPT, NO-DATA."""
    key = (row["noun"], question)
    if question in _VERB_QUESTIONS:
        hit = _verb_hit(row["funcs"], _VERB_QUESTIONS[question])
        if hit:
            name, lineno = hit
            return "ANSWERED", "%s:%d def %s" % (row["module"], lineno, name)
    elif question in _FIELD_QUESTIONS:
        m = re.search(_FIELD_QUESTIONS[question], row["source"])
        if m:
            lineno = row["source"][: m.start()].count("\n") + 1
            return "ANSWERED", "%s:%d %r" % (row["module"], lineno, m.group(0))
    elif question == "consumer":
        hits = _consumers(row["module"], row["path"], dirs)
        if hits:
            return "ANSWERED", "read by: %s" % ", ".join(hits)
    else:
        raise ValueError("unknown question %r" % question)

    if key in EXEMPTIONS:
        return "EXEMPT", EXEMPTIONS[key]
    return NODATA, "no %s in %s answers this" % (
        "function" if question in _VERB_QUESTIONS else
        "matching pattern" if question in _FIELD_QUESTIONS else "importer",
        row["module"])


def build_grid(dirs=SCAN_DIRS):
    """rows: [{"noun", "module", "cells": {question: (verdict, detail)}}]"""
    nouns = find_nouns(dirs)
    grid = []
    for row in nouns:
        cells = {q: answer_cell(row, q, dirs) for q in QUESTIONS}
        grid.append({"noun": row["noun"], "module": row["module"], "cells": cells})
    return grid


def render(grid):
    lines = []
    counts = {"ANSWERED": 0, "EXEMPT": 0, NODATA: 0}
    nodata_findings = []
    for row in grid:
        for q in QUESTIONS:
            verdict, detail = row["cells"][q]
            counts[verdict] += 1
            if verdict == NODATA:
                nodata_findings.append("%s (%s) / %s: %s"
                                       % (row["noun"], row["module"], q, detail))

    lines.append("negative_space_audit: %d noun(s), %d question(s), %d cell(s)"
                 % (len(grid), len(QUESTIONS), len(grid) * len(QUESTIONS)))
    lines.append("answered=%d exempt=%d no-data=%d"
                 % (counts["ANSWERED"], counts["EXEMPT"], counts[NODATA]))
    lines.append("")
    for row in grid:
        lines.append("%s (%s)" % (row["noun"], row["module"]))
        for q in QUESTIONS:
            verdict, detail = row["cells"][q]
            lines.append("  %-24s %-8s %s" % (q, verdict, detail))
    lines.append("")
    if nodata_findings:
        lines.append("%s cells, named (%d):" % (NODATA, len(nodata_findings)))
        for f in nodata_findings:
            lines.append("  %s: %s" % (NODATA, f))
    else:
        lines.append("no %s cells" % NODATA)
    return "\n".join(lines) + "\n", counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--strict", action="store_true",
                    help="a NO-DATA cell fails the run instead of being "
                         "reported as NO-DATA")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            print("%s: %s does not exist, nothing to audit" % (NODATA, d))
            return 2

    grid = build_grid()
    text, counts = render(grid)
    print(text, end="")

    if counts[NODATA] == 0:
        return 0
    return 1 if args.strict else 2


if __name__ == "__main__":
    sys.exit(main())
