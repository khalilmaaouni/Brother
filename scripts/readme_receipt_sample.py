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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checks", action="store_true",
                    help="print the per-file check list as JSON instead of "
                         "the README block")
    args = ap.parse_args(argv)
    if args.checks:
        print(json.dumps(checks(), indent=1))
    else:
        print(sample_block())
    return 0


if __name__ == "__main__":
    sys.exit(main())
