#!/usr/bin/env python3
"""readme_receipt_sample: the README's per-file receipt sample, rendered by
the shipped code from the toy run's own recorded facts, so it can never
drift from the format string a reader will actually see.

WHY THIS EXISTS. The 2026-09-04 docs honesty audit of the public tag v1.0.1
found the README's sample (its "One small delivery" block) printing a shape
no format string in the shipped code produces any more: the sample had been
edited by hand as the engine grew clauses, so the front page advertised an
older receipt than the product prints. A sample typed by hand goes stale on
the first change to receipt_door.receipt_sentence and nothing notices.

WHAT IT IS. RUN_FACTS below is a verbatim trim of the real run's own two
recorded files (the Work document W-make-add-refuse-nonnumeric-input-with-a-
.json and claims.json under the run directory named in LOG_PATH): the units,
their done_checks, what each one changed, the before-and-after measurement,
and the claim each worker released. Nothing here is invented and nothing is
a result: the sentences are produced by receipt_door.receipts_for and
receipt_door.receipt_sentence reading these facts, exactly as brother_run.py
produces them at the end of a real run.

HOME PATH. LOG_PATH carries the run's own log location with the home
directory written as `~`. The run recorded it absolute; `~` is the standard
rendering of the same path, it is the form the README already read, and it
is machine independent, which the absolute form is not: rendering the
absolute path would make this block, and the test that pins it, agree only
on the one account that produced the run.

USE:
  python3 scripts/readme_receipt_sample.py            # the README block
  python3 scripts/readme_receipt_sample.py --checks   # the per-file check
                                                      # list, as JSON, for
                                                      # accept_delivery.py
                                                      # --checks-file

scripts/test_readme_honesty.py asserts the README carries this block
verbatim and refuses when it does not.

Python 3, standard library only.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import receipt_door  # noqa: E402

#: The engine revision that ran this delivery, as the run recorded it. Not
#: resolvable in a public clone, and receipt_door.harness_label says so in
#: the sentence rather than printing a fragment nobody can look up.
HARNESS_REVISION = "015760192728a32c5055cd7dd741f7d3a3522d0b"

#: Where the run wrote its full output, home abbreviated (see HOME PATH).
LOG_PATH = ("~/.claude/brother-run/docs/plan/runs/"
            "20260903T071356-make-add-refuse-non-numeric-input-with-a/"
            "run.log")

#: The run's own recorded facts, trimmed to the fields receipts_for reads.
RUN_FACTS = {
    "record": {
        "outcome": ("make add() refuse non-numeric input with a clear error "
                    "and cover it with a test"),
        "work_id": "W-make-add-refuse-nonnumeric-input-with-a-",
        "rows": [
            {
                "id": "guard",
                "title": ("Add a type check in add() in mathlib.py that "
                          "raises TypeError with a clear message when either "
                          "argument is not an int or float."),
                "done_check": ("python3 -c \"import mathlib; assert "
                               "mathlib.add(1,2)==3 and "
                               "mathlib.add(1.5,2)==3.5\" && python3 -c "
                               "\"import mathlib; mathlib.add('a','b')\" "
                               "2>&1 | grep -q '^TypeError: .'"),
                "owns": ["mathlib.py"],
                "depends_on": [],
                "status": "DONE",
                "check_passed_before": False,
                "files_changed_by_unit": ["mathlib.py"],
            },
            {
                "id": "test",
                "title": ("Add a test to test_mathlib.py asserting add() "
                          "raises TypeError on non-numeric input, and run "
                          "the full suite green."),
                "done_check": ("python3 -m pytest test_mathlib.py -q -k "
                               "'type or numeric or error or raise'"),
                "owns": ["test_mathlib.py"],
                "depends_on": ["guard"],
                "status": "DONE",
                "check_passed_before": False,
                "files_changed_by_unit": ["test_mathlib.py"],
            },
        ],
    },
    "claims": {
        "guard": {
            "state": "done",
            "unit_id": "guard",
            "evidence": {
                "canonical_rev": "da88480d731ddfa3cb2862066d56a43e5c7cf0db",
                "check_command": ("python3 -c \"import mathlib; assert "
                                  "mathlib.add(1,2)==3 and "
                                  "mathlib.add(1.5,2)==3.5\" && python3 -c "
                                  "\"import mathlib; mathlib.add('a','b')\" "
                                  "2>&1 | grep -q '^TypeError: .'"),
                "exit_code": 0,
                "output": "",
                "output_truncated": False,
            },
        },
        "test": {
            "state": "done",
            "unit_id": "test",
            "evidence": {
                "canonical_rev": "63855a8b385204e03b306d64c9e38c7a4380a9bf",
                "check_command": ("python3 -m pytest test_mathlib.py -q -k "
                                  "'type or numeric or error or raise'"),
                "exit_code": 0,
                "output": (".                                               "
                           "                         [100%]\n1 passed, 1 "
                           "deselected in 0.00s"),
                "output_truncated": False,
            },
        },
    },
}


def receipts():
    """The two receipts this run produced, built by the shipped code from
    RUN_FACTS. The record is copied before the harness revision is stamped
    on it, so importing this module never mutates the literal above."""
    record = json.loads(json.dumps(RUN_FACTS["record"]))
    record["harness_revision"] = HARNESS_REVISION
    return record, receipt_door.receipts_for(
        record, RUN_FACTS["claims"], [], log_path=LOG_PATH)


def checks():
    """The per-file check list for this run, the exact shape
    accept_delivery.record(..., checks=...) stores and
    receipt_door.require_per_file_checks accepts."""
    record, rs = receipts()
    return receipt_door.per_file_checks(record, rs)


#: The harness clause is rendered against a ref that never resolves, so the
#: README block reads the same in every checkout. The name collision that
#: first forced this pin is fixed at the root (E101: receipt_door finds the
#: public export remote by URL, so the hub checkout and a lane worktree now
#: label one commit identically, measured 2026-09-04). The pin STAYS, on its
#: own merits rather than as that workaround: this is a fixed historical
#: sample the README carries verbatim, so its clause must not depend on
#: which remotes the reader clone happens to have, and a fork whose URL
#: names another account would otherwise read "public remote NO-DATA" here
#: and turn the honesty gate red. The private form is the honest one for a
#: hub commit the export never mirrors byte for byte.
SAMPLE_PUBLIC_REF = "refs/readme-sample/never-resolves"


def sample_lines():
    """The README block: one line per changed file, naming the file, the
    unit that changed it, and that unit's whole receipt sentence. Rendered
    with the public ref pinned to SAMPLE_PUBLIC_REF so the block does not
    depend on which remotes the checkout has fetched."""
    record, rs = receipts()
    by_id = {r["id"]: r for r in rs}
    lines = []
    saved = receipt_door.PUBLIC_REMOTE_REF
    receipt_door.PUBLIC_REMOTE_REF = SAMPLE_PUBLIC_REF
    try:
        for entry in receipt_door.per_file_checks(record, rs):
            sentence = receipt_door.receipt_sentence(by_id[entry["unit"]])
            lines.append("%s (unit %s): %s"
                         % (entry["file"], entry["unit"], sentence))
    finally:
        receipt_door.PUBLIC_REMOTE_REF = saved
    return lines


def sample_block():
    """The lines as the README carries them, one blank line between."""
    return "\n\n".join(sample_lines())


#: EVAD unit U1 (release 1.0.13). The router's own words for a bare
#: invocation with no unfinished work: NO_UNFINISHED_RUN_LINE is copied
#: from bundle/commands/brother.md ("None: it prints 'no unfinished run
#: found' at exit 0") and matches the literal string brother_run.py prints
#: at that branch. ASK_OUTCOME_LINE is copied verbatim from
#: bundle/skills/using-brother/SKILL.md's Step 2 ("Say exactly this and
#: nothing else: ..."). Neither is retyped anywhere else: a session that
#: changes either source file and not this constant fails
#: TheFirstRunTranscriptIsWhatTheShippedCodePrints below.
NO_UNFINISHED_RUN_LINE = "no unfinished run found"
ASK_OUTCOME_LINE = (
    "Brother turns AI-assisted work into something checkable instead of "
    "just trusted. What are you trying to do right now: start or check on "
    "a project, or get a change proven safe before it ships?")

#: The two path components brother_run.py joins onto a run directory to
#: get the path its last stdout line names (RECEIPT_DIRNAME and
#: RECEIPT_FILENAME there, same literal strings here).
RECEIPT_DIRNAME = "receipt"
RECEIPT_FILENAME = "receipt.json"


#: CDX-2 (Codex review of f24b7bb3): the transcript's per-file lines
#: printed a bare "exited N", discarding checks()'s own state and reason
#: and hiding that test_mathlib.py's check is NO-DATA (never re-run with
#: mathlib.py's change reverted, so it does not prove the test on its
#: own). "unit" is swapped for "check" in the printed reason because
#: receipt_door's wording uses that word and
#: FIRST_RUN_BANNED_WORDS (test_readme_honesty.py) keeps Brother's
#: internal vocabulary out of this stranger-facing block; the swap
#: changes no fact, only the noun a stranger has not been taught.
def _transcript_verdict(entry):
    """The parenthetical after "exited N" on a per-file line: this file's
    own checks() state, plus its reason when one was recorded, so a
    NO-DATA check never reads as though it had been proven."""
    reason = re.sub(r"\bunit\b", "check", entry.get("reason") or "")
    word = "verified" if entry.get("state") == "verified" else "NO-DATA"
    return "%s: %s" % (word, reason) if reason else word


def first_run_transcript():
    """The README's 'Your first run, start to finish' block: the install
    command README.md already carries, then the toy setup a stranger needs
    before any of the rest of this block means anything (the checks named
    below run against mathlib.py and test_mathlib.py, and neither file
    exists until a reader creates it; the exact starting content is what
    the stored run's own done_checks assume: add() with no type guard yet,
    and a test file with no matching type/numeric/error/raise test yet, so
    both checks are the ones the stored run recorded as failing before its
    two changes landed), the router's own no-unfinished-work and
    ask-outcome sentences, the stored run's own outcome, one line per
    changed file naming its check and captured exit code (the same
    RUN_FACTS checks() reads), and the engine's own last-line shape ending
    in the stored run's receipt path. No line here is invented: a step
    this repository has no recorded output for (the interactive mechanics
    between typing the outcome and the engine's last line, for instance)
    is left out rather than guessed at."""
    run_dir = LOG_PATH.rsplit("/", 1)[0]
    receipt_path = os.path.join(run_dir, RECEIPT_DIRNAME, RECEIPT_FILENAME)
    lines = [
        "mkdir mathlib-toy && cd mathlib-toy",
        "git init -q",
        # A fresh machine has no git identity, and on Linux git refuses the
        # commit below with "empty ident name ... not allowed" (measured on
        # the ubuntu CI runner, exit 128). Scoped to the toy repository with
        # --local, so the reader's global git configuration is never touched.
        "git config --local user.name \"Toy User\"",
        "git config --local user.email \"toy@example.invalid\"",
        "cat > mathlib.py <<'EOF'",
        "def add(a, b):",
        "    return a + b",
        "EOF",
        "cat > test_mathlib.py <<'EOF'",
        "import mathlib",
        "",
        "",
        "def test_add_ints():",
        "    assert mathlib.add(1, 2) == 3",
        "",
        "",
        "def test_add_floats():",
        "    assert mathlib.add(1.5, 2) == 3.5",
        "EOF",
        "git add mathlib.py test_mathlib.py",
        "git commit -q -m \"toy mathlib, before the fix\"",
        "claude plugin marketplace add khalilmaaouni/Brother && "
        "claude plugin install brother@brother",
        "/brother",
        NO_UNFINISHED_RUN_LINE,
        ASK_OUTCOME_LINE,
        RUN_FACTS["record"]["outcome"],
    ]
    for entry in checks():
        lines.append("%s: check %s exited %s (%s)"
                     % (entry["file"], entry["check_command"],
                        entry["exit_code"], _transcript_verdict(entry)))
    lines.append("brother_run: receipt: %s" % receipt_path)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checks", action="store_true",
                    help="print the per-file check list as JSON instead of "
                         "the README block")
    ap.add_argument("--first-run", action="store_true",
                    help="print the 'Your first run, start to finish' "
                         "block instead of the receipt sample")
    args = ap.parse_args(argv)
    if args.first_run:
        print(first_run_transcript())
    elif args.checks:
        print(json.dumps(checks(), indent=1))
    else:
        print(sample_block())
    return 0


if __name__ == "__main__":
    sys.exit(main())
