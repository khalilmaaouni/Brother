#!/usr/bin/env python3
"""QC scaffold: the bridge from a behaviour row to the QC lead's actual work.

Pilot feedback item P13: quality control verifies slower than developers
build, and the bottleneck is verification capacity, not paperwork. This tool
does two small things, nothing else:

1. `cases`   -- read a dossier's 08-behaviour.md and print one draft test
   case per row, so a QC lead opens a page that is already mostly written
   instead of a blank one. Setup, action and expected result are the row's
   own Starting point, Trigger and Required outcome; the suggested proof is
   the row's own Proof text, which in every shipped example already reads as
   a test procedure.
2. `finding` -- append one new row to 08-behaviour.md from an exploratory
   finding (something a tester noticed that no row predicted), so the table
   grows from real testing and not only from design.

REUSE, NOT A SECOND PARSER: the row format (ID | Starting point | Trigger |
Required outcome | Proof) is parsed exactly once in this repository, by
sbe_design.py's _behaviour_rows, which tools/sbe_design.py's own
check_behaviour is built on. This file imports that function rather than
reading the table a second way; this project has a recorded failure where
two parsers of one table format drifted and disagreed with each other
without either side noticing, and this tool declines to become the second
one. Emptiness is read through sbe_checks.answered(), the same
VACUOUS_VALUES-backed test check_behaviour itself uses for a row's Required
outcome and Proof, rather than a locally invented idea of "blank".

WHAT THIS DECLINES TO DO, on purpose (the labour test: cut anything that
costs the QC lead more effort than it saves):
- No new test framework, no config file, no dependency. `cases` prints
  markdown; a QC lead pastes it into whatever they already use.
- No attempt to write the CHECK for a drafted case: this generates the
  starting point of a test, never a claim that it passed one.
- `finding` supports appending after the table's last row (the ordinary
  case) or after the header when the table is genuinely empty; it does not
  attempt inserting into or reformatting a table using anything other than
  the shipped "| a | b | c | d | e |" pipe style. A table already broken in
  some more exotic way is refused by name, never guessed at.

Usage:
  python3 tools/sbe_qc_scaffold.py cases --root DIR [--out PATH]
  python3 tools/sbe_qc_scaffold.py finding --root DIR \\
      --starting-point TEXT --trigger TEXT \\
      --required-outcome TEXT --proof TEXT

`cases` exits 0 for PASS and NO-DATA alike (an honestly reported absence is
not a script error) and 1 only for FAIL (a table this tool could not read or
parse). `finding` exits 0 once the row is written, 1 otherwise.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, mirroring every other tool in this directory (template:
tools/sbe_onepager.py, for its --root/--out CLI shape and its Read-style
result objects instead of ambiguous tuples).
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sbe_design import (_behaviour_rows, read, read_problem, ARTIFACT_FILES,
                        _MARKER_COMMENT, UNFILLED_MARKER, _BEHAVIOUR_COLUMNS)
from sbe_checks import answered, one_line

BEHAVIOUR_REL = ARTIFACT_FILES["08"]  # "08-behaviour.md"
FIELDS = ("starting point", "trigger", "required outcome", "proof")


def warn(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


class Table(object):
    """The result of trying to read 08-behaviour.md as a set of rows.

    An object rather than a bare (verdict, rows, note) tuple: this project's
    honesty meta-test reads any 2-tuple return as a possible (verdict,
    evidence) pair and refuses a function it cannot prove never returns
    PASS, and a 3-tuple invites the same confusion at the call site (same
    reasoning as sbe_onepager.py's Read and ReceiptRef).

    verdict: PASS, NO-DATA or FAIL, the same three this project uses
    everywhere else. kind narrows WHY, only when verdict is not PASS:
    "absent" (no 08-behaviour.md at all), "template" (still the shipped
    example), "empty" (a real table with a header and zero rows),
    "unreadable" or "malformed" (both FAIL). rows is the parsed row list,
    always [] unless verdict is PASS. note is the one sentence printed for
    a human either way.
    """

    def __init__(self, verdict, kind, rows, note):
        self.verdict = verdict
        self.kind = kind
        self.rows = rows
        self.note = note


def load_table(root):
    """Table for the 08-behaviour.md under `root`. Every branch here mirrors
    tools/sbe_design.py's own check_behaviour, deliberately: two tools with
    two different ideas of what counts as NO-DATA for the same file is the
    same drift the parser-reuse rule above exists to prevent, one level up.
    """
    problem = read_problem(root, BEHAVIOUR_REL)
    if problem:
        return Table("FAIL", "unreadable", [],
                     "%s is %s; an artifact that exists and cannot be read is a broken claim, "
                     "not an absent one" % (BEHAVIOUR_REL, problem))
    t = read(root, BEHAVIOUR_REL)
    if t is None:
        return Table("NO-DATA", "absent", [],
                     "no %s under %s: this dossier has no behaviour table to draft cases from "
                     "or add a finding to" % (BEHAVIOUR_REL, root))
    if _MARKER_COMMENT.search(t):
        return Table("NO-DATA", "template", [],
                     "%s still carries its %s marker: this is the shipped example, not this "
                     "dossier's own behaviour, so no cases were drafted from it (delete the "
                     "marker and write the real rows first)" % (BEHAVIOUR_REL, UNFILLED_MARKER))
    rows, malformed = _behaviour_rows(t)
    if malformed:
        return Table("FAIL", "malformed", [],
                     "%d row(s) under the ID | Starting point | Trigger | Required outcome | "
                     "Proof table do not carry 5 cells and could not be read: %s; a row this "
                     "tool cannot parse is a broken claim, not an absent one"
                     % (len(malformed), "; ".join(m[:60] for m in malformed[:4])))
    if not rows:
        return Table("NO-DATA", "empty", [],
                     "%s has a Rules table with a header and zero rows: nothing here states "
                     "what the software must do, so there is nothing to draft a case from"
                     % BEHAVIOUR_REL)
    return Table("PASS", "ok", rows, "%d row(s) read" % len(rows))


def _field_or_todo(row, col):
    v = answered(row.get(col))
    if v is None:
        return "TODO: no %s recorded on this row yet, fill in before the case can run" % col
    return one_line(v)


def draft_case(row):
    rid = one_line(answered(row.get("id")) or "(no id)")
    return [
        "## Draft test case for %s" % rid,
        "",
        "- Setup (Starting point): %s" % _field_or_todo(row, "starting point"),
        "- Action (Trigger): %s" % _field_or_todo(row, "trigger"),
        "- Expected result (Required outcome): %s" % _field_or_todo(row, "required outcome"),
        "- Suggested proof / procedure: %s" % _field_or_todo(row, "proof"),
        "- Actual result: ",
        "- Verdict (pass / fail): ",
        "",
    ]


#: H6: the stamp `render_cases` writes into a generated sheet, an HTML
#: comment (invisible when the sheet is rendered as markdown) recording the
#: exact behaviour row ids the sheet was drafted from, the same "comment a
#: reader never sees" convention `sbe_design.py`'s own `_MARKER_COMMENT` /
#: `UNFILLED_MARKER` already use. `check_stale` reads this back rather than
#: guessing at a sheet's age from its mtime, which a re-save or a copy would
#: change with no bearing on whether the ROWS moved.
_STAMP_PREFIX = "SBE-QC-SCAFFOLD-IDS"
_STAMP_RE = re.compile(r"<!--\s*%s:\s*([^>]*?)\s*-->" % re.escape(_STAMP_PREFIX))


def _stamp_line(rows):
    ids = ",".join((r.get("id") or "").strip() for r in rows if answered(r.get("id")))
    return "<!-- %s: %s -->" % (_STAMP_PREFIX, ids)


def render_cases(root):
    """(lines, exit_code) for the `cases` command."""
    table = load_table(root)
    lines = ["QC DRAFT TEST CASES for %s" % os.path.join(root, BEHAVIOUR_REL), ""]
    if table.verdict != "PASS":
        lines.append("%s: %s" % (table.verdict, table.note))
        return lines, (1 if table.verdict == "FAIL" else 0)
    lines.append(_stamp_line(table.rows))
    lines.append("%d row(s) in the table, %d draft test case(s) generated below "
                 "(one per row)." % (len(table.rows), len(table.rows)))
    lines.append("")
    for row in table.rows:
        lines.extend(draft_case(row))
    return lines, 0


class StaleCheck(object):
    """The result of comparing a generated sheet's own id stamp against the
    behaviour table's CURRENT rows.

    An object rather than a bare tuple, for the identical reason `Table`
    above is one: this project's honesty meta-test refuses a function it
    cannot prove never returns a PASS-shaped 2-tuple.

    verdict: FRESH (the sheet still covers every current row), STALE (the
    table grew rows the sheet does not), NO-DATA (the sheet carries no stamp
    to compare, or the current table itself could not be read), or FAIL (the
    sheet could not be read at all). newIds is the row ids the table carries
    now that the sheet's stamp does not, always [] unless verdict is STALE.
    """

    def __init__(self, verdict, new_ids, note):
        self.verdict = verdict
        self.newIds = new_ids
        self.note = note


def check_stale(root, sheet_path):
    """A sheet `cases --out` wrote, read back and compared against the
    dossier's CURRENT 08-behaviour.md rows. Never a second parser of the
    sheet's own draft-case prose: only the one stamp line `_stamp_line`
    wrote is read, and the current rows come from `load_table`, the exact
    function that generated the sheet in the first place."""
    try:
        with open(sheet_path, "r") as fh:
            sheet_text = fh.read()
    except OSError as e:
        return StaleCheck("FAIL", [], "could not read %s: %s" % (sheet_path, e))
    m = _STAMP_RE.search(sheet_text)
    if not m:
        return StaleCheck(
            "NO-DATA", [],
            "%s carries no %s stamp, so there is no recorded id set to compare against: "
            "either it was not generated by this tool's `cases` command, or it predates "
            "this stamp" % (sheet_path, _STAMP_PREFIX))
    stamped_ids = set(x.strip() for x in m.group(1).split(",") if x.strip())
    table = load_table(root)
    if table.verdict != "PASS":
        return StaleCheck(
            "NO-DATA", [],
            "the current %s could not be read for comparison: %s" % (BEHAVIOUR_REL, table.note))
    current_ids = [(r.get("id") or "").strip() for r in table.rows]
    new_ids = [i for i in current_ids if i and i not in stamped_ids]
    if new_ids:
        return StaleCheck(
            "STALE", new_ids,
            "%s was generated from %d row(s) and %s now carries %d new row(s) it does not "
            "cover: %s" % (sheet_path, len(stamped_ids), BEHAVIOUR_REL, len(new_ids),
                          ", ".join(new_ids)))
    return StaleCheck(
        "FRESH", [],
        "%s still covers every row currently in %s (%d row(s), none added since it was "
        "generated)" % (sheet_path, BEHAVIOUR_REL, len(stamped_ids)))


_ID_RE = re.compile(r"^B(\d+)$")


def next_id(rows):
    nums = []
    for r in rows:
        m = _ID_RE.match((r.get("id") or "").strip())
        if m:
            nums.append(int(m.group(1)))
    return "B%d" % ((max(nums) + 1) if nums else 1)


def _row_line_index(lines, row_id):
    """The index of the line holding the row whose ID cell is `row_id`,
    searched from the bottom (IDs are meant to be unique; the last match
    wins if they are not). Uses the exact same cell-split expression
    _behaviour_rows uses on a matched row, so this locator cannot disagree
    with the parser about what a row's cells are; it only answers WHERE the
    already-parsed row's line sits, which the parser itself does not track.

    ponytail: assumes the shipped "| a | b | c | d | e |" pipe style with no
    escaped pipes inside a cell. A table using some other pipe convention
    is not found here and the caller refuses rather than guessing.
    """
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) == len(_BEHAVIOUR_COLUMNS) and cells[0] == row_id:
            return i
    return None


def _header_sep_index(lines):
    """The index of the header's separator line (the `|---|---|...` line
    right under the fixed column names), for inserting the first row into
    an otherwise-empty table. Compares against _BEHAVIOUR_COLUMNS, the same
    imported constant the shared parser is built from, so "what the header
    looks like" has one definition, not two."""
    for i in range(len(lines) - 1):
        s = lines[i].strip()
        if not s.startswith("|"):
            continue
        header = [c.strip().lower() for c in s.strip("|").split("|")]
        nxt = lines[i + 1].strip()
        is_sep = bool(nxt) and nxt.startswith("|") and not set(nxt) - set("|-: ")
        if is_sep and header == list(_BEHAVIOUR_COLUMNS):
            return i + 1
    return None


def _escape_cell(text):
    """A cell in this table cannot carry a literal "|" or a line break
    without corrupting every column after it: _behaviour_rows (the shared
    parser this tool reuses) splits a row on a bare "|" and does not read a
    backslash escape, so writing `\\|` would still be read as a new column
    boundary by the very check this row has to pass. A pipe is therefore
    swapped for the visually identical fullwidth vertical line (U+FF5C)
    rather than escaped; a line break is flattened by one_line, the same
    choke point every other value this project writes into a report line
    already goes through."""
    return one_line(text).replace("|", "｜").strip()


def add_row(root, starting_point, trigger, required_outcome, proof):
    """Append one new row from an exploratory finding. Returns (verdict,
    note): FAIL when there is no table to append to, the fields are empty,
    or the file cannot be written; PASS once the row is on disk."""
    table = load_table(root)
    if table.verdict == "FAIL" or table.kind in ("absent", "template"):
        return "FAIL", "cannot add a row: %s" % table.note
    supplied = {"starting point": starting_point, "trigger": trigger,
               "required outcome": required_outcome, "proof": proof}
    empty = [k for k in FIELDS if answered(supplied[k]) is None]
    if empty:
        return "FAIL", ("a new behaviour row needs real text in every field; %s carried "
                        "nothing (a placeholder or a blank is not a finding, and this tool "
                        "will not write a decorative row)" % ", ".join(empty))
    path = os.path.join(root, BEHAVIOUR_REL)
    text = read(root, BEHAVIOUR_REL)
    if text is None:
        return "FAIL", "cannot add a row: %s vanished between reading and writing" % BEHAVIOUR_REL
    lines = text.splitlines()
    if table.rows:
        idx = _row_line_index(lines, table.rows[-1].get("id") or "")
    else:
        idx = _header_sep_index(lines)
    if idx is None:
        return "FAIL", ("could not find where to insert the new row in %s; its table may use a "
                        "pipe style this tool does not recognise, so nothing was written "
                        "(add the row by hand)" % BEHAVIOUR_REL)
    new_id = next_id(table.rows)
    new_line = "| %s | %s | %s | %s | %s |" % (
        new_id, _escape_cell(starting_point), _escape_cell(trigger),
        _escape_cell(required_outcome), _escape_cell(proof))
    lines.insert(idx + 1, new_line)
    new_text = "\n".join(lines) + ("\n" if text.endswith("\n") else "\n")
    try:
        with open(path, "w") as fh:
            fh.write(new_text)
    except OSError as e:
        return "FAIL", "could not write %s: %s" % (path, e)
    return "PASS", "%s appended to %s as row %s" % (new_id, BEHAVIOUR_REL, new_id)


def build_parser():
    ap = argparse.ArgumentParser(prog="sbe_qc_scaffold.py",
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd")
    cases = sub.add_parser("cases", help="draft one test case per behaviour row")
    cases.add_argument("--root", default=os.getcwd())
    cases.add_argument("--out", default=None, help="write the page here instead of stdout")
    finding = sub.add_parser("finding", help="append a new behaviour row from a finding")
    finding.add_argument("--root", default=os.getcwd())
    finding.add_argument("--starting-point", required=True)
    finding.add_argument("--trigger", required=True)
    finding.add_argument("--required-outcome", required=True)
    finding.add_argument("--proof", required=True)
    stale = sub.add_parser("stale", help="check whether a sheet `cases --out` wrote still "
                                        "covers every row currently in 08-behaviour.md")
    stale.add_argument("--root", default=os.getcwd())
    stale.add_argument("--sheet", required=True, help="path to a sheet `cases --out` wrote")
    return ap


def main(argv=None):
    ap = build_parser()
    ns = ap.parse_args(argv)
    if ns.cmd is None:
        ap.print_help(sys.stderr)
        return 2
    root = os.path.abspath(ns.root)
    if ns.cmd == "cases":
        lines, code = render_cases(root)
        text = "\n".join(lines) + "\n"
        if ns.out:
            try:
                d = os.path.dirname(os.path.abspath(ns.out))
                if d and not os.path.isdir(d):
                    os.makedirs(d)
                with open(ns.out, "w") as fh:
                    fh.write(text)
            except OSError as e:
                warn("sbe-qc-scaffold: could not write %s (%s)" % (ns.out, e))
                return 1
        else:
            sys.stdout.write(text)
        return code
    if ns.cmd == "stale":
        result = check_stale(root, ns.sheet)
        sys.stdout.write("%s: %s\n" % (result.verdict, result.note))
        return 1 if result.verdict in ("STALE", "FAIL") else 0
    verdict, note = add_row(root, ns.starting_point, ns.trigger,
                            ns.required_outcome, ns.proof)
    sys.stdout.write("%s: %s\n" % (verdict, note))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
