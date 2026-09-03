#!/usr/bin/env python3
"""Does docs/plan/PRIVACY-FILTERING-SPEC.md still classify every content
class this estate actually produces, and still state the default rule?

WHY THIS EXISTS. R26.1's done-check is "every content class produced this
week has a classification row." A spec document is prose; nothing stops a
future edit from dropping a row while leaving the rest of the file looking
complete. This is the mechanical gate: it does not judge the QUALITY of a
row, only that the class name and the default rule sentence are still
present in the file, the same shallow-but-load-bearing check
scripts/leaf_pin_check.py runs for version pins.

Exit contract, matching the sibling gates in this repository:
  0  PASS      every listed class name, and the default rule, are present
  1  FAIL      at least one class name or the default rule is missing
  2  NO-DATA   the spec file does not exist at all

NO-DATA IS NOT A PASS. A missing file has proven nothing about its content.

Python 3, standard library only. No network.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "docs" / "plan" / "PRIVACY-FILTERING-SPEC.md"

# Every content class R26.1 requires a row for. Matched case-insensitively
# as a plain substring against the spec's text, so this list is the single
# source of truth for "every class the estate actually produced this week."
CLASSES = [
    "engine and product code",
    "tests",
    "generated surfaces",
    "plans and roadmaps",
    "decision records",
    "founder quoted words",
    "handover packs and night reports",
    "session logs",
    "vault notes",
    "commit messages",
    "commit author identity",
    "evidence files",
    "credentials and keys",
    "client-estate material",
    "team member information",
    "personal founder information",
    "scratch and temp output",
]

DEFAULT_RULE = (
    "when unsure, the most private plausible owner wins; "
    "forgetting must fail safe"
)


def read_spec(path=None):
    """The spec's text, lower-cased for matching, or None if the file is
    absent. None is the caller's signal for NO-DATA, never for a pass."""
    path = path or SPEC_PATH
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return f.read().lower()


def main():
    text = read_spec()
    if text is None:
        print(f"check-filtering-spec: NO-DATA: {SPEC_PATH} does not exist")
        return 2

    missing_classes = [c for c in CLASSES if c not in text]
    missing_rule = DEFAULT_RULE not in text

    if missing_classes or missing_rule:
        if missing_classes:
            print(f"check-filtering-spec: FAIL: {len(missing_classes)} of "
                  f"{len(CLASSES)} content classes missing a row: "
                  + ", ".join(missing_classes))
        if missing_rule:
            print("check-filtering-spec: FAIL: the default rule sentence "
                  f'("{DEFAULT_RULE}") is missing')
        return 1

    print(f"check-filtering-spec: PASSED: all {len(CLASSES)} content "
          f"classes and the default rule are present in {SPEC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
