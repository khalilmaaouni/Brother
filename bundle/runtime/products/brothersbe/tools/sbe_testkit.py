#!/usr/bin/env python3
"""sbe testkit: turn 08-behaviour.md into the tester's own working document.

The behaviour table (ID | Starting point | Trigger | Required outcome | Proof)
already exists for everyone but the person re-testing a release by hand. This
tool has two directions:

  sheet <dossier-dir>   read 08-behaviour.md, write one case per row plus an
                         exploratory tail, into <dossier-dir>/.sbe/. The tester
                         fills in RESULT and FINDING (or SAW, for the tail) by
                         hand, on the same file.
  adopt <sheet-path>     read a filled sheet back, and draft new behaviour
                         table rows from every finding that carries text. The
                         output is pasteable, never written into
                         08-behaviour.md: the analyst owns that table.

Nothing here is a gate. Nothing checks whether a sheet was ever run, and
neither command can fail a build or move any other command's exit code.

Row parsing reuses `sbe_design._behaviour_rows`, the one place this project
already parses the ID | Starting point | Trigger | Required outcome | Proof
table, rather than a second parser that could drift from it.

Python 3.9 floor, standard library only.
"""
import io
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbe_design as design  # noqa: E402  (path setup has to come first)
from sbe_checks import one_line, say  # noqa: E402  (same reason)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ERROR = 1

# The five exploratory charters the review named: this is where a tester
# actually finds defects a behaviour row cannot state in advance. id, title
# (printed beside the header so the charter reads on its own), extra field
# labels beyond GIVEN/DO/SAW/EXPECTED.
CHARTERS = (
    ("unnatural-behaviour",
     "unnatural behaviour: the software does something nobody asked it to", ()),
    ("user-experience",
     "user experience: this is awkward, confusing or slow to use", ()),
    ("analyst-developer-gap",
     "a misunderstanding between analyst and developer: the built thing "
     "differs from what was asked", ()),
    ("translated-text",
     "awkward translated text: a string that reads wrong on screen", ("SCREEN", "STRING AS SHOWN")),
    ("limits",
     "limits: the software breaks, degrades or behaves oddly at a rate limit, "
     "size limit, timeout, pagination boundary, numeric overflow or unicode/locale edge",
     ("RATE LIMIT", "SIZE LIMIT", "TIMEOUT", "PAGINATION BOUNDARY", "NUMERIC OVERFLOW",
      "UNICODE/LOCALE")),
)

# The FINDING prompt is one fixed line; a tester's answer is whatever follows
# it, on the same line or the lines under it. Named once so the sheet writer
# and the sheet reader can never drift into two different spellings of it.
FINDING_LABEL = "FINDING (only if fail or blocked): what you saw:"

_CASE_HEADER = re.compile(r"^CASE\s+(\S+)")
_CHARTER_HEADER = re.compile(r"^CHARTER\s+(\S+)")
_LOOSE_CASE = re.compile(r"^CASE\b")
_LOOSE_CHARTER = re.compile(r"^CHARTER\b")


def _sheet_path(dossier_dir):
    return os.path.join(dossier_dir, ".sbe", "test-run-%s.md" % time.strftime("%Y-%m-%d"))


def _row_changed(row):
    """True when this behaviour row changed since the sheet was last
    generated: the signal that puts its case first and marks it RE-CONFIRM.

    Answering this properly needs a per-rule fingerprint, which is Band 1
    item 1, landing in tools/sbe_design.py, a file this task does not own
    and that another lane may still be building. 08-behaviour.md carries no
    per-row change marker of its own either: _BEHAVIOUR_COLUMNS there is a
    fixed 5-cell table (id, starting point, trigger, required outcome,
    proof) with no room for a sixth. So nothing is flagged changed yet. Once
    sbe_design exposes a fingerprint (or the table grows a marker cell),
    read it here and compare it against what the previous sheet recorded
    for this row's id, instead of always returning False.
    """
    return False


def _render_case(row, reconfirm=False):
    rid = row.get("id") or "(no id)"
    marker = "  RE-CONFIRM" if reconfirm else ""
    note = "; this rule changed since the sheet was last generated" if reconfirm else ""
    lines = [
        "CASE %s%s  (draft from behaviour row %s%s)" % (rid, marker, rid, note),
        "GIVEN   %s" % (row.get("starting point") or ""),
        "DO      %s" % (row.get("trigger") or ""),
        "EXPECT  %s" % (row.get("required outcome") or ""),
        "PROOF PLANNED  %s" % (row.get("proof") or ""),
        "RESULT  [ ] pass  [ ] fail  [ ] blocked    tested by ____  date ____",
        FINDING_LABEL,
        "",
    ]
    return "\n".join(lines)


def _render_charter(cid, title, extra):
    lines = [
        "CHARTER %s  (%s)" % (cid, title),
        "GIVEN    ____",
        "DO       ____",
    ]
    for label in extra:
        lines.append("%s  ____" % label)
    lines.append("SAW      ____")
    lines.append("EXPECTED ____")
    # Mirrors CASE's own RESULT line above: without this, a charter nobody
    # reached and a charter somebody filled in and found nothing look like
    # the same blank block, a NO-DATA gap wearing the appearance of coverage.
    lines.append("STATUS  [ ] not reached    tested by ____  date ____")
    lines.append("")
    return "\n".join(lines)


def render_sheet(dossier_dir, rows, malformed):
    parts = [
        "# Test run: %s" % os.path.basename(os.path.normpath(os.path.abspath(dossier_dir))),
        "",
        "Generated by `sbe testkit sheet` from 08-behaviour.md: one case per behaviour",
        "row, plus an exploratory tail. Fill in RESULT and FINDING (or SAW, in the tail)",
        "as you test. Run `sbe testkit adopt` on this file afterward to draft new rows",
        "from what you found; nothing here is written back into 08-behaviour.md.",
        "",
    ]
    if malformed:
        parts.append("MALFORMED ROWS IN 08-behaviour.md (not turned into cases; fix the "
                     "table and rerun sheet):")
        for m in malformed:
            parts.append("  - %s" % m)
        parts.append("")
    # Changed rows first and marked RE-CONFIRM, unchanged rows after, each
    # group in its original table order (sorted() is stable, so this needs
    # no second key). A tester returning to a feature after a requirement
    # moved should not have to hunt the table for what moved.
    ordered_rows = sorted(rows, key=lambda r: not _row_changed(r))
    for row in ordered_rows:
        parts.append(_render_case(row, reconfirm=_row_changed(row)))
    parts.append("## Exploratory tail")
    parts.append("")
    for cid, title, extra in CHARTERS:
        parts.append(_render_charter(cid, title, extra))
    return "\n".join(parts).rstrip("\n") + "\n"


def cmd_sheet(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write("usage: sbe testkit sheet <dossier-dir>\n")
        return EXIT_OK if argv and argv[0] in ("-h", "--help") else EXIT_USAGE
    dossier_dir = argv[0]
    if not os.path.isdir(dossier_dir):
        # dossier_dir is a raw CLI argument: flattened through one_line() before
        # it reaches the terminal, the same rule say() enforces for stdout, so a
        # directory name carrying a newline cannot forge a second stderr line.
        sys.stderr.write(one_line(
            "sbe testkit sheet: '%s' is not a directory." % dossier_dir) + "\n")
        return EXIT_USAGE
    behaviour_path = os.path.join(dossier_dir, design.ARTIFACT_FILES["08"])
    problem = design.read_problem(dossier_dir, design.ARTIFACT_FILES["08"])
    if problem:
        sys.stderr.write(one_line(
            "sbe testkit sheet: %s is %s; a file that exists and cannot be read is a broken "
            "claim, not an absent one." % (behaviour_path, problem)) + "\n")
        return EXIT_ERROR
    text = design.read(dossier_dir, design.ARTIFACT_FILES["08"])
    if text is None:
        # An absent input is not a failure of this command: exactly one line,
        # nothing written, exit 0.
        say("NO-DATA: %s does not exist; nothing to draft a test sheet from" % behaviour_path)
        return EXIT_OK
    rows, malformed = design._behaviour_rows(text)
    out_path = _sheet_path(dossier_dir)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render_sheet(dossier_dir, rows, malformed))
    say("sbe testkit sheet: %d case(s) drafted from %s -> %s" % (len(rows), behaviour_path, out_path))
    if malformed:
        say("sbe testkit sheet: %d row(s) in %s did not carry 5 cells and could not be read; "
              "named in the sheet rather than silently dropped" % (len(malformed), behaviour_path))
    return EXIT_OK


def _split_blocks(text):
    """(blocks, malformed_headers). blocks: [{"kind", "id", "lines"}]. A line
    starting with CASE or CHARTER that does not carry a recognisable id is
    named in malformed_headers rather than folded into whichever block came
    before it: attributing it there would be a guess, not a read."""
    blocks, malformed = [], []
    cur = None
    for raw in text.splitlines():
        stripped = raw.strip()
        mc = _CASE_HEADER.match(stripped)
        mh = _CHARTER_HEADER.match(stripped)
        if mc:
            cur = {"kind": "case", "id": mc.group(1), "lines": []}
            blocks.append(cur)
            continue
        if mh:
            cur = {"kind": "charter", "id": mh.group(1), "lines": []}
            blocks.append(cur)
            continue
        if _LOOSE_CASE.match(stripped) or _LOOSE_CHARTER.match(stripped):
            malformed.append(stripped)
            cur = None
            continue
        if cur is not None:
            cur["lines"].append(raw)
    return blocks, malformed


def _extract_fields(lines, labels):
    """One value per label in `labels`: whatever follows the label on its
    own line, stripped. A label never reached is absent from the result.

    Deliberately single-line, matching the sheet's own format (every field
    is written on the line its label sits on): a first version folded
    trailing non-label lines into whichever field came last, which read the
    blank line after an unfilled FINDING plus the sheet's own next
    `## Exploratory tail` heading as that FINDING's text. A block boundary
    only a heading's WORDING enforces is not a boundary; not guessing past
    the label's own line is.
    """
    values = {}
    for raw in lines:
        stripped = raw.strip()
        for lbl in labels:
            if stripped.startswith(lbl):
                tail = stripped[len(lbl):]
                if tail == "" or tail[0] in " \t:":
                    values[lbl] = tail.lstrip(" \t:").strip()
                break
    return values


def _blank(text):
    """True for empty text and for the sheet's own unfilled placeholder
    (a run of underscores): neither is an answer a tester wrote."""
    t = (text or "").strip()
    return not t or set(t) <= set("_")


def _cell(text):
    """A table-safe cell: no bare pipe to break the row it sits in."""
    return (text or "").strip().replace("|", "\\|") or "(not stated in test sheet)"


_CHARTER_TITLES = {cid: title for cid, title, _extra in CHARTERS}
_CHARTER_LABELS = {
    cid: ("GIVEN", "DO") + extra + ("SAW", "EXPECTED")
    for cid, _title, extra in CHARTERS
}


def _collect_findings(blocks):
    """(findings, problems). findings: [{"starting point","trigger",
    "required outcome","source"}], one per FINDING-with-text or filled
    charter SAW line, in file order. problems: blocks this could not read."""
    findings, problems = [], []
    for block in blocks:
        kind, bid, lines = block["kind"], block["id"], block["lines"]
        if kind == "case":
            fields = _extract_fields(lines, ["GIVEN", "DO", "EXPECT", "PROOF PLANNED",
                                             "RESULT", FINDING_LABEL])
            finding = fields.get(FINDING_LABEL, "")
            if not _blank(finding):
                # What the tester WROTE here is what she SAW, which is the
                # defect, not the rule. Putting it in the Required outcome
                # column would produce a row demanding the bug, and a row
                # nobody notices is wrong is worse than no row. So the observed
                # text is carried verbatim and the outcome is left as an
                # explicit TBD: that token is in sbe_checks.VACUOUS_VALUES, so
                # the behaviour check FAILs the row until a human writes the
                # rule the finding implies. The tool supplies the raw material;
                # the analyst still owns the sentence.
                findings.append({
                    "starting point": fields.get("GIVEN", ""),
                    "trigger": fields.get("DO", ""),
                    # MEASURED, and the first attempt at this failed: a cell
                    # reading "TBD, write the rule. Observed: x" PASSes, because
                    # the vacuity test compares the WHOLE cell against
                    # sbe_checks.VACUOUS_VALUES and that sentence is not in it.
                    # The cell has to be the bare token for the behaviour check
                    # to refuse the row, so the observation travels in a comment
                    # line above it instead, where a human reads it and the
                    # parser does not.
                    "required outcome": "TBD",
                    "observed": finding,
                    "source": "F-%%d: case %s finding" % bid,
                })
        else:
            labels = _CHARTER_LABELS.get(bid)
            if labels is None:
                # A CHARTER id this run never generated (a hand-added block,
                # or a renamed one): read it with the base four labels rather
                # than silently skipping it.
                labels = ("GIVEN", "DO", "SAW", "EXPECTED")
                problems.append("CHARTER %s: not one of this tool's five charters "
                                "(unnatural-behaviour, user-experience, "
                                "analyst-developer-gap, translated-text, limits); read with "
                                "the base GIVEN/DO/SAW/EXPECTED fields only" % bid)
            fields = _extract_fields(lines, labels)
            saw = fields.get("SAW", "")
            if not _blank(saw):
                required = fields.get("EXPECTED", "")
                if _blank(required):
                    required = "not stated as EXPECTED; observed instead: %s" % saw
                findings.append({
                    "starting point": fields.get("GIVEN", ""),
                    "trigger": fields.get("DO", ""),
                    "required outcome": required,
                    "source": "F-%%d: charter %s (%s)" % (bid, _CHARTER_TITLES.get(bid, bid)),
                })
    return findings, problems


def _next_ids(existing_rows):
    prefix, max_n = "B", 0
    for row in existing_rows:
        m = re.match(r"^([A-Za-z]+)(\d+)$", (row.get("id") or "").strip())
        if m:
            prefix = m.group(1)
            max_n = max(max_n, int(m.group(2)))
    n = max_n
    while True:
        n += 1
        yield "%s%d" % (prefix, n)


def cmd_adopt(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write("usage: sbe testkit adopt <sheet-path>\n")
        return EXIT_OK if argv and argv[0] in ("-h", "--help") else EXIT_USAGE
    sheet_path = argv[0]
    if not os.path.isfile(sheet_path):
        say("NO-DATA: %s does not exist; nothing to adopt" % sheet_path)
        return EXIT_OK
    try:
        text = io.open(sheet_path, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        # sheet_path is a raw CLI argument and exc's message can itself embed it
        # (a failing OSError repeats the path); one_line() flattens both, same
        # reason as the dossier_dir write in cmd_sheet above.
        sys.stderr.write(one_line(
            "sbe testkit adopt: %s could not be read (%s)" % (sheet_path, exc)) + "\n")
        return EXIT_ERROR

    blocks, malformed_headers = _split_blocks(text)
    if not blocks and not malformed_headers:
        say("NO-DATA: %s carries no CASE or CHARTER block; nothing to adopt" % sheet_path)
        return EXIT_OK

    findings, problems = _collect_findings(blocks)
    problems = malformed_headers + problems

    if not findings:
        if problems:
            # problems can carry raw lines lifted straight out of the sheet
            # file's own content (a malformed CASE/CHARTER header), so it goes
            # through one_line() for the same reason as the writes above.
            sys.stderr.write(one_line(
                "sbe testkit adopt: %d block(s) in %s could not be read and are named here "
                "rather than silently skipped: %s"
                % (len(problems), sheet_path, "; ".join(problems[:6]))) + "\n")
        say("NO-DATA: %s carries no FINDING with text and no filled charter SAW line; "
              "nothing to adopt" % sheet_path)
        return EXIT_OK

    dossier_dir = os.path.dirname(os.path.dirname(os.path.abspath(sheet_path)))
    existing_rows = []
    behaviour_text = design.read(dossier_dir, design.ARTIFACT_FILES["08"])
    if behaviour_text is not None:
        existing_rows, _ = design._behaviour_rows(behaviour_text)

    out_path = os.path.join(dossier_dir, ".sbe", "proposed-rows.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    id_gen = _next_ids(existing_rows)
    table_lines = [
        "# Proposed behaviour rows",
        "",
        "Drafted by `sbe testkit adopt` from %s." % sheet_path,
        "This is pasteable text, not a table this dossier already has: the analyst owns",
        "08-behaviour.md, and nothing here writes to it. Copy the rows worth keeping,",
        "rewrite what needs it, and paste them into the Rules table yourself.",
        "",
        "| ID | Starting point | Trigger | Required outcome | Proof |",
        "|---|---|---|---|---|",
    ]
    proposed = []
    observations = []
    for i, finding in enumerate(findings, start=1):
        rid = next(id_gen)
        proof = "Manual: repeat the steps in finding F-%d" % i
        source = finding["source"] % i
        observed = finding.get("observed")
        if observed:
            # BELOW the table, never between rows. MEASURED: a comment line
            # placed above a row ends the table for the parser, and the row
            # after it is dropped without a word. A silently dropped row is
            # worse than the TBD it was carrying.
            observations.append("- F-%d observed: %s" % (i, _cell(observed)))
        table_lines.append("| %s | %s | %s | %s | %s |" % (
            rid, _cell(finding["starting point"]), _cell(finding["trigger"]),
            _cell(finding["required outcome"]), proof))
        proposed.append((rid, source))
    if observations:
        table_lines.append("")
        table_lines.append("## What the tester actually saw")
        table_lines.append("")
        table_lines.append("Each row above says TBD where the rule belongs, on purpose: what she")
        table_lines.append("wrote is the defect, and a row stating the defect as the requirement")
        table_lines.append("would demand the bug. Write the rule the observation implies, then the")
        table_lines.append("behaviour check accepts the row. Until then it FAILs, which is correct.")
        table_lines.append("")
        table_lines.extend(observations)
    if problems:
        table_lines.append("")
        table_lines.append("Not read (named, not silently dropped): %s" % "; ".join(problems))
    with io.open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(table_lines) + "\n")
    say("sbe testkit adopt: %d proposed row(s) from %s -> %s" % (len(proposed), sheet_path, out_path))
    for rid, source in proposed:
        say("  %s  <- %s" % (rid, source))
    return EXIT_OK


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write("usage: sbe testkit {sheet <dossier-dir>|adopt <sheet-path>}\n")
        return EXIT_USAGE
    cmd, rest = argv[0], argv[1:]
    if cmd == "sheet":
        return cmd_sheet(rest)
    if cmd == "adopt":
        return cmd_adopt(rest)
    if cmd in ("-h", "--help"):
        sys.stdout.write("usage: sbe testkit {sheet <dossier-dir>|adopt <sheet-path>}\n")
        return EXIT_OK
    sys.stderr.write("sbe testkit: unknown subcommand %r (expected sheet or adopt)\n" % cmd)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
