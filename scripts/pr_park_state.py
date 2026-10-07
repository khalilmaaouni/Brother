#!/usr/bin/env python3
"""ACC4.b: the read only check that reads gh AFTER the owner's close.

Spec: docs/plan/specs/ACC4.md. Reads docs/plan/ACC4-PARK-LIST.txt (or the
list given as the first argument), `gh pr list --state open` and
`gh pr view <n> --json state,comments` for every listed number (read verbs
only, through pr_park_triage.gh_pr, which refuses writes). It then re-runs
the triage over every open pull request.

FAIL names: a listed pull request still open; a CLOSED listed pull request
whose last comment is absent or differs from its listed reason (a MERGED or
absent one landed and needs no comment); an open count over MAX_OPEN_PRS;
and any open pull request that reads IDLE-UNCLASSIFIED or NO-DATA without
being on the park list. An unreadable gh, an unreadable list, or a listed
number gh cannot view is NO-DATA, never PASS. The count bar stays and is
never the only check.

Prints the verdict and exits 0 PASS, 1 FAIL, 2 NO-DATA.

Python 3, stdlib only. No em or en dashes anywhere in this file.
"""
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pr_park_triage as T  # noqa: E402

MAX_OPEN_PRS = 12  # type: int


def read_park_list(path):
    # type: (str) -> List[Tuple[int, str]]
    """`<number> <reason>` rows; `#` lines and blanks skipped. Refuses (ValueError)
    a non numeric number, an empty reason, a control character in the reason
    (the closer hands it to gh as one argument) and a number listed twice.
    A missing file raises FileNotFoundError: never an empty list."""
    rows = []  # type: List[Tuple[int, str]]
    seen = set()
    with open(path, encoding="utf-8", newline="") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            number, _, reason = line.strip().partition(" ")
            reason = reason.strip()
            if not number.isdigit():
                raise ValueError("%s:%d: line must start with a pull request number" % (path, lineno))
            if not reason:
                raise ValueError("%s:%d: #%s has a reason of zero length" % (path, lineno, number))
            if any(ord(ch) < 32 or ch == "\x7f" for ch in reason):
                raise ValueError("%s:%d: #%s reason carries a control character" % (path, lineno, number))
            if int(number) in seen:
                raise ValueError("%s:%d: #%s is listed twice" % (path, lineno, number))
            seen.add(int(number))
            rows.append((int(number), reason))
    return rows


def closing_comment(view):
    # type: (Dict[str, object]) -> Optional[str]
    """The last comment's text of `gh pr view --json state,comments` when the
    state is CLOSED, else None (an OPEN or MERGED one has no closing comment)."""
    if not isinstance(view, dict) or view.get("state") != "CLOSED":
        return None
    comments = view.get("comments")
    if not isinstance(comments, list) or not comments:
        return None
    last = comments[-1]
    if not isinstance(last, dict):
        return None
    body = last.get("body")
    return str(body) if body is not None else None


def _same_text(a, b):
    return " ".join(a.split()) == " ".join(b.split())


def park_errors(open_numbers, park_rows, closed_comment):
    # type: (List[int], List[Tuple[int, str]], Dict[int, str]) -> List[str]
    """One named error per failed property; empty means the park list is honoured."""
    errors = []
    open_set = set(open_numbers)
    for number, reason in park_rows:
        if number in open_set:
            errors.append("#%d is listed in the park list but still OPEN" % number)
        elif number in closed_comment:
            comment = closed_comment[number]
            if not comment.strip():
                errors.append("#%d is CLOSED with no closing comment" % number)
            elif not _same_text(comment, reason):
                errors.append("#%d is CLOSED but its last comment differs from its listed reason" % number)
    if len(open_numbers) > MAX_OPEN_PRS:
        errors.append("%d pull requests are open, over the bar of %d" % (len(open_numbers), MAX_OPEN_PRS))
    return errors


def verdict(errors, gh_readable):
    # type: (List[str], bool) -> str
    if not gh_readable:
        return "NO-DATA"
    return "FAIL" if errors else "PASS"


def _view(number, repo):
    code, out, err = T.gh_pr("view", [str(number), "--json", "state,comments"], repo=repo)
    if code != 0:
        return None, "gh pr view %d exit %d: %s" % (number, code, err.strip() or out.strip())
    try:
        view = json.loads(out)
    except ValueError as exc:
        return None, "gh pr view %d printed no JSON: %s" % (number, exc)
    return view, ""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[0] if argv else T.PARK_LIST
    repo = os.environ.get("PR_PARK_REPO", T.REPO)
    main_ref = os.environ.get("PR_PARK_MAIN_REF", "hub/main")
    try:
        park_rows = read_park_list(path)
    except (OSError, ValueError) as exc:
        print("NO-DATA: park list: %s" % exc)
        return 2
    prs, problem = T.fetch_open_prs(repo)
    if prs is None:
        print("NO-DATA: %s" % problem)
        return 2
    open_numbers = [int(p["number"]) for p in prs]
    closed_comment = {}  # type: Dict[int, str]
    for number, _ in park_rows:
        if number in open_numbers:
            continue
        view, problem = _view(number, repo)
        if view is None:
            print("NO-DATA: %s" % problem)
            return 2
        comment = closing_comment(view)
        if view.get("state") == "CLOSED":
            closed_comment[number] = comment if comment is not None else ""
    errors = park_errors(open_numbers, park_rows, closed_comment)
    listed = {number: reason for number, reason in park_rows}
    for row in T.triage(prs, listed, main_ref):
        if row["class"] in ("IDLE-UNCLASSIFIED", "NO-DATA") and int(row["number"]) not in listed:
            errors.append("#%s reads %s and is not on the park list: %s" % (row["number"], row["class"], row["reason"]))
    for error in errors:
        print("ERROR: %s" % error)
    result = verdict(errors, True)
    print("%s: %d open (bar %d), %d listed, %d error(s)" % (result, len(open_numbers), MAX_OPEN_PRS,
                                                          len(park_rows), len(errors)))
    return {"PASS": 0, "FAIL": 1}.get(result, 2)


if __name__ == "__main__":
    sys.exit(main())
