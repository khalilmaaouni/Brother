#!/usr/bin/env python3
"""The Acceptance Time benchmark harness (S11, roadmap row S11; protocol
benchmarks/ACCEPTANCE-TIME.md; frozen spec
benchmarks/gauntlets/acceptance-compression.json section 20.3).

This module builds and scores the two halves of the benchmark that do not
need a human being timed:

  prepare <out dir>  writes the three condition packets (raw diff, ordinary
                      agent summary, Brother receipt) for each of the three
                      fixed seeded changes below.
  score <csv path>    reads a human trial's results CSV (reviewer, change,
                      condition, seconds, decision) and prints the median
                      seconds and correctness rate per condition, or
                      NO-DATA when fewer than five reviewers appear.

Nothing here times a human or invents a decision. The trial itself is the
founder's own work (roadmap row S11 why_now); this is only the harness the
trial runs through.
"""
import argparse
import csv
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import receipt_door as RD  # noqa: E402

NODATA = "NO-DATA"
CONDITIONS = ("raw_diff", "ordinary_summary", "brother_receipt")
MIN_REVIEWERS = 5
CORRECT_DECISION = "reject"


def _git(args, cwd):
    return subprocess.run(["git"] + list(args), cwd=cwd,
                          capture_output=True, text=True)


def _write_files(root, files):
    for path, content in files.items():
        full = os.path.join(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)


def build_raw_diff(files):
    """The raw diff condition: a real fixture commit, an empty commit
    followed by one commit adding the given files, torn down after the
    diff is captured. Never a hand typed diff string."""
    if shutil.which("git") is None:
        return NODATA + ": no version-control tool on PATH, cannot build " \
            "a fixture commit"
    tmp = tempfile.mkdtemp(prefix="acceptance-time-")
    try:
        setup = (
            ["init", "-q"],
            ["config", "user.email", "fixture@example.invalid"],
            ["config", "user.name", "Acceptance Time fixture"],
            ["commit", "--allow-empty", "-q", "-m", "before"],
        )
        for cmd in setup:
            result = _git(cmd, tmp)
            if result.returncode != 0:
                return "%s: setup step %s failed: %s" % (
                    NODATA, cmd[0], result.stderr.strip())
        _write_files(tmp, files)
        _git(["add", "-A"], tmp)
        result = _git(["commit", "-q", "-m", "after"], tmp)
        if result.returncode != 0:
            return "%s: commit step failed: %s" % (
                NODATA, result.stderr.strip())
        result = _git(["diff", "HEAD~1", "HEAD"], tmp)
        if result.returncode != 0:
            return "%s: diff step failed: %s" % (NODATA, result.stderr.strip())
        return result.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_receipt(change):
    """The Brother receipt condition, built through receipts_for /
    reading_order / receipt_record, the same seam
    scripts/test_receipt_door.py and scripts/test_acceptance_compression.py
    drive. Nothing here is a hand typed receipt; if the seam raises, the
    packet says NO-DATA and why."""
    try:
        receipts = RD.receipts_for(change["record"], change["claims"], [],
                                   "run.log")
        order = RD.reading_order(change["record"], receipts)
        lines = ["the receipt for this delivery, reading order first:", ""]
        for section in RD.READING_SECTIONS:
            lines.append(section + ":")
            entries = order[section]
            if not entries:
                lines.append("  (none)")
            for entry in entries:
                lines.append("  %s (unit %s): %s" % (
                    entry["path"], entry["unit"], entry["why"]))
            lines.append("")
        view = RD.receipt_record(change["record"], receipts,
                                 log_path="run.log")
        lines.append(RD.receipt_text(view, log_path="run.log"))
        return "\n".join(lines)
    except Exception as exc:
        return "%s: could not build a Brother receipt for change %s: %r" % (
            NODATA, change["id"], exc)


def _row(uid, owns, files, objective):
    return dict(id=uid, objective=objective, done_check="true",
               status="DONE", check_passed_before=False,
               owns=owns, files_changed_by_unit=files)


def _claims(uids):
    return dict((u, dict(state="done",
                         evidence=dict(exit_code=0, output="")))
               for u in uids)


#: The three fixed changes, one per workload family
#: benchmarks/gauntlets/acceptance-compression.json names (n=2 medium
#: multi-file feature, n=4 auth/payment/security, n=5 schema/migration),
#: each with exactly one seeded defect. The auth-security case is the exact
#: middleware/dependency/generated-file mix the spec's own case names
#: (section 20.3, and scripts/test_receipt_door.py's _seeded_diff); the
#: other two are adapted in the same spirit for the two families the spec
#: names but does not give a concrete case for.
CHANGES = [
    dict(
        id="medium-feature",
        files=dict([
            ("src/signup/validate.py",
             "MIN_LENGTH = 8\n\n\n"
             "def is_strong_password(password):\n"
             "    \"\"\"True when the password meets the minimum "
             "length.\"\"\"\n"
             "    # Seeded defect: inverted. A password shorter than\n"
             "    # MIN_LENGTH returns True (strong) instead of False.\n"
             "    if len(password) < MIN_LENGTH:\n"
             "        return True\n"
             "    return len(password) >= MIN_LENGTH\n"),
            ("src/signup/handler.py",
             "from src.signup.validate import is_strong_password\n\n\n"
             "def create_account(email, password):\n"
             "    if not is_strong_password(password):\n"
             "        raise ValueError('password too weak')\n"
             "    return dict(email=email)\n"),
        ]),
        record=dict(
            outcome="add password strength validation to signup",
            work_id="medium-feature",
            rows=[_row(
                "SIGNUP", ["src/signup/handler.py"],
                ["src/signup/handler.py", "src/signup/validate.py"],
                "add password strength validation to signup")]),
        claims=_claims(["SIGNUP"]),
        summary=(
            "This change adds signup handling. handler.py now calls "
            "validate.py's is_strong_password before creating an account. "
            "Existing tests still pass."),
    ),
    dict(
        id="auth-security",
        files=dict([
            ("src/middleware/rate_limit.py",
             "def check(request, limiter):\n"
             "    # Seeded defect: an unconditional bypass for anyone\n"
             "    # who sends this header.\n"
             "    if request.headers.get('X-Debug') == '1':\n"
             "        return True\n"
             "    return limiter.allow(request.client_ip)\n"),
            ("requirements.txt", "ratelimit==2.2.1\n"),
            ("docs/generated/api-index.html",
             "<html><body>generated api index</body></html>\n"),
        ]),
        record=dict(
            outcome="tighten the request middleware",
            work_id="auth-security",
            rows=[_row(
                "M", ["src/", "requirements.txt", "docs/"],
                ["src/middleware/rate_limit.py", "requirements.txt",
                 "docs/generated/api-index.html"],
                "tighten the request middleware")]),
        claims=_claims(["M"]),
        summary=(
            "This change hardens the rate limiter and adds a needed "
            "dependency. Generated docs were refreshed. No functional "
            "regressions expected."),
    ),
    dict(
        id="schema-migration",
        files=dict([
            ("migrations/0007_add_last_login.sql",
             "-- Seeded defect: NOT NULL with no DEFAULT on a table\n"
             "-- that already holds rows.\n"
             "ALTER TABLE users ADD COLUMN last_login TIMESTAMP "
             "NOT NULL;\n"),
            ("src/models.py",
             "class User:\n"
             "    last_login = None  # populated by the new column\n"),
        ]),
        record=dict(
            outcome="track last login time",
            work_id="schema-migration",
            rows=[_row(
                "MIG", ["migrations/", "src/models.py"],
                ["migrations/0007_add_last_login.sql", "src/models.py"],
                "track last login time")]),
        claims=_claims(["MIG"]),
        summary=(
            "This change adds a last_login column to the users table and "
            "updates the model. Migration applies cleanly in the test "
            "database."),
    ),
]


#: E2. One plain-language instruction sheet, written once per prepared
#: directory, never per packet. A reviewer reads this before opening any
#: packet.
INSTRUCTIONS_TEXT = """\
Acceptance Compression: reviewer instructions

You have been given three changes. For each one, you were assigned exactly
one presentation: a raw diff, an ordinary agent summary, or a Brother
receipt. You will never see the same change twice.

What to do, for each change:

1. Note the time before you open the packet.
2. Open the ONE file you were assigned for that change. Do not open the
   other two presentations of the same change, and do not look at the
   other reviewers' packets.
3. Decide one of three things: ACCEPT (ship this change as is), REJECT
   (do not ship this change as written), or ASK (you would need to ask
   the author a question before deciding).
4. Note the time the moment you have made your decision.
5. Write down, in your own words, which lines or parts of the change you
   actually looked at before deciding.

Record all of this in the results CSV you were given: your start time,
your end time (or the elapsed seconds), and your decision, on the row for
that change.

Do not use any tool, search, or outside help beyond what is in the packet
itself. Do not discuss the changes with another reviewer before every
reviewer has finished. There are no trick questions and no reward for
speed over correctness; take the time you actually need.
"""


#: E3. The founder is the one who recruits reviewers, hands out packets,
#: and collects the results CSV, so the exact commands for the two steps
#: this harness does NOT do by itself live beside the packets, not only in
#: a doc nobody opens mid trial.
INSTRUCTIONS_FOUNDER_TEXT = """\
Acceptance Compression: commands for when results arrive

This directory already holds the nine packets (three changes times three
conditions) and INSTRUCTIONS.md for reviewers. The two steps left are
yours: assigning reviewers and, once they finish, checking and scoring
their results. Run these from the repository root.

1. Print the counterbalanced assignment table for your reviewer count
   (five or more; fewer is refused) and write the blank results CSV they
   fill in:

     python3 scripts/acceptance_trial_assign.py assign <N> --seed <SEED> \\
         --out-csv <this directory>/results.csv

   Hand each reviewer only the one packet the table names for them, per
   change, plus INSTRUCTIONS.md. Never let one reviewer see the same
   change under a second condition.

2. Once every reviewer has filled in their rows (seconds, decision) in
   results.csv, validate it before scoring:

     python3 scripts/acceptance_trial_assign.py validate <this
         directory>/results.csv

   Fix whatever it names (a missing time, an impossible time, a reviewer
   seeing a change twice) and validate again until it prints "clean".

3. Only once validate prints clean, score it:

     python3 scripts/acceptance_time.py score <this directory>/results.csv

   Fewer than five distinct reviewers in the CSV prints NO-DATA and exits
   3: that is not a bug, it is the honest floor
   (benchmarks/ACCEPTANCE-TIME.md) refusing to publish a comparison too
   small to mean anything.

4. Read the result against the frozen rule, not against a hunch:
   benchmarks/gauntlets/acceptance-compression/SUCCESS-RULE-FROZEN.md.
   That file is not edited to fit whatever score() prints.
"""


def prepare(out_dir):
    """Write the nine packets (three changes times three conditions) plus
    INSTRUCTIONS.md and INSTRUCTIONS-FOUNDER.md into out_dir. Returns the
    list of paths written."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for change in CHANGES:
        change_dir = os.path.join(out_dir, change["id"])
        os.makedirs(change_dir, exist_ok=True)
        packets = [
            ("raw_diff.txt", build_raw_diff(change["files"])),
            ("ordinary_summary.txt", change["summary"]),
            ("brother_receipt.txt", build_receipt(change)),
        ]
        for name, content in packets:
            path = os.path.join(change_dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(path)
    for name, text in (("INSTRUCTIONS.md", INSTRUCTIONS_TEXT),
                       ("INSTRUCTIONS-FOUNDER.md",
                        INSTRUCTIONS_FOUNDER_TEXT)):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        written.append(path)
    return written


def score(csv_path):
    """Print the median seconds and correctness rate per condition from a
    results CSV (reviewer, change, condition, seconds, decision). Returns
    the process exit code: 0 on a scored comparison, 2 for a malformed
    input, 3 when the honest floor of five reviewers is not met."""
    if not os.path.isfile(csv_path):
        print("%s: %s does not exist" % (NODATA, csv_path))
        return 2
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = set(["reviewer", "change", "condition", "seconds",
                        "decision"])
        missing = required - set(reader.fieldnames or [])
        if missing:
            print("%s: %s is missing column(s) %s" % (
                NODATA, csv_path, ", ".join(sorted(missing))))
            return 2
        for row in reader:
            rows.append(row)
    reviewers = set(row["reviewer"] for row in rows)
    if len(reviewers) < MIN_REVIEWERS:
        print("%s: only %d reviewer(s) in %s, fewer than the %d the "
              "honest floor requires. This is NOT a comparison."
              % (NODATA, len(reviewers), csv_path, MIN_REVIEWERS))
        return 3
    by_condition = dict((name, []) for name in CONDITIONS)
    for row in rows:
        condition = row["condition"]
        if condition not in by_condition:
            print("%s: %s names an unknown condition %r (expected one of "
                  "%s)" % (NODATA, csv_path, condition,
                          ", ".join(CONDITIONS)))
            return 2
        by_condition[condition].append(row)
    for condition in CONDITIONS:
        entries = by_condition[condition]
        if not entries:
            print("%s: condition %s has no rows in %s"
                  % (NODATA, condition, csv_path))
            continue
        seconds = [float(entry["seconds"]) for entry in entries]
        correct = sum(1 for entry in entries
                     if entry["decision"].strip().lower() == CORRECT_DECISION)
        median = statistics.median(seconds)
        rate = correct / len(entries)
        print("%s: median %.1fs, correct %.0f%% (n=%d)"
              % (condition, median, rate * 100, len(entries)))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        description="Acceptance Time benchmark harness (S11)")
    sub = parser.add_subparsers(dest="verb", required=True)
    prepare_parser = sub.add_parser(
        "prepare", help="write the nine condition packets into a directory")
    prepare_parser.add_argument("out_dir")
    score_parser = sub.add_parser(
        "score", help="score a human trial's results CSV")
    score_parser.add_argument("csv_path")
    args = parser.parse_args(argv)
    if args.verb == "prepare":
        for path in prepare(args.out_dir):
            print(path)
        return 0
    if args.verb == "score":
        return score(args.csv_path)
    return 2  # pragma: no cover, argparse already refuses an unknown verb


if __name__ == "__main__":
    sys.exit(main())
