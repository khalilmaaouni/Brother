#!/usr/bin/env python3
"""self_check_staged: catch a new dash or a private term before it is even
committed, not only after a manual grep run after the fact.

WHY THIS EXISTS. Tonight a session introduced 2 new em-dashes into its own
newly-written markdown (while separately fixing dashes elsewhere), and
separately spelled out real private client terms in its own tracking-file
prose, twice, on two separate occasions (while writing ABOUT the importance
of never doing that). Both mistakes were self-caught, but only by manually
re-running a grep after the fact. scripts/pre_push_gate.py already blocks a
PUSH carrying either defect over the outgoing range; this script exists
because "before the push" is still a whole commit later than it needs to be.
It scans the STAGED diff at commit time, before either mistake ever gets a
SHA of its own.

REUSE, NOT REINVENTION, per this estate's rule against a second copy of a
check that already exists:

  * The private-terms rule (whole-word matching for short terms, the term
    list at ~/.brothersbe-private-names, NO-DATA rather than a silent pass
    on a missing or empty list) is IMPORTED directly from
    scripts/private_terms_scan.py: load_terms() and scan_text() are plain
    module-level functions with no import-time side effects (they do not
    touch outgoing_range()/scan_range(), which this tool has no use for),
    so importing them pulls in nothing this script does not already need.
    This mirrors the reuse shape scripts/export_public.py already uses for
    scripts/pre_push_gate.py's SECRET_SHAPES tuple.

  * The dash rule (em dash U+2014, en dash U+2013) is REPLICATED rather than
    imported from scripts/pre_push_gate.py. That module's own DASHES is a
    two-character tuple, but importing the module for it also imports
    edition_guard.py (the public-export edition guard), an unrelated,
    heavier dependency a commit-time hygiene check has no business
    carrying. Two named Unicode code points is the smaller, more honest
    dependency, and it is still built from chr() calls rather than written
    as a literal, for the same reason pre_push_gate.py gives for its own
    DASHES: a scanner must not contain what it forbids.

SCOPE, and why it differs from the push gate. This reads ONLY the staged
diff (git diff --cached), and ONLY added ("+") lines: a dash or a term
already committed further back, or one that only ever appeared in a
REMOVED line, is pre_push_gate.py's job over the outgoing range, not this
hook's job over one commit-in-progress. A private term is never printed,
only its length, matching private_terms_scan.py's own discipline; a dash
finding names its file and line only, so both findings read the same shape.

Usable two ways:
  manual command: python3 scripts/self_check_staged.py
  pre-commit hook: python3 scripts/self_check_staged.py --install-hook

Python 3, standard library only. No network.
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from private_terms_scan import load_terms, scan_text  # noqa: E402

#: Built from code points, not literals, for the same reason pre_push_gate.py
#: builds its own DASHES the same way: a scanner must not contain what it
#: forbids. Same two characters as pre_push_gate.py's DASHES; replicated
#: rather than imported (see the module docstring for why).
DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash

EXIT_CLEAN, EXIT_FOUND, EXIT_NO_DATA = 0, 1, 2

#: How this tool recognises a pre-commit hook it installed, to avoid
#: reinstalling on top of itself and to tell its own block apart from
#: somebody else's when composing.
MARK = "# brother-self-check-staged"


def parse_added_lines(diff_text):
    """Yields (path, lineno, content) for every line a staged commit would
    ADD, at that line's number in the NEW file. A removed line, or an
    unchanged context line, never appears here: run with --unified=0, the
    diff carries no context lines at all, so every content line seen is
    either a real addition or a real removal, and only additions consume a
    new-file line number."""
    path = None
    new_line = None
    expect_plus_header = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            path = None
            new_line = None
            expect_plus_header = False
            continue
        if raw.startswith("--- "):
            expect_plus_header = True
            continue
        if expect_plus_header and raw.startswith("+++ "):
            expect_plus_header = False
            p = raw[4:]
            path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            continue
        expect_plus_header = False
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            new_line = int(m.group(1)) if m else None
            continue
        if raw.startswith("+"):
            if path is not None and new_line is not None:
                yield path, new_line, raw[1:]
                new_line += 1
            continue
        if raw.startswith("-"):
            continue
        # "index ...", "Binary files ... differ", "\ No newline at end of
        # file", rename/copy headers: none of these carry added content.


def scan_dashes(added_lines):
    """[(path, lineno)] for every added line carrying an em or en dash."""
    return [(path, lineno) for path, lineno, content in added_lines
            if any(d in content for d in DASHES)]


def scan_private_terms(added_lines, terms):
    """[(path, lineno, [length, ...])] for every added line carrying a
    private term. The matched term is never kept past this call and never
    printed, matching private_terms_scan.py's own discipline: only its
    length is reported."""
    out = []
    for path, lineno, content in added_lines:
        matched = scan_text(content, terms)
        if matched:
            out.append((path, lineno, [len(t) for t in matched]))
    return out


def staged_diff(cwd=None, runner=None):
    runner = runner or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd))
    return runner(["git", "diff", "--cached", "--no-color", "--unified=0"])


def run_scan(cwd=None, terms_path=None, runner=None):
    """Returns (exit_code, [message_lines]). Never raises on a missing terms
    file or an unreadable diff: NO-DATA IS NEVER A PASS, so both come back as
    EXIT_NO_DATA, never EXIT_CLEAN."""
    proc = staged_diff(cwd, runner)
    if proc.returncode != 0:
        return EXIT_NO_DATA, ["NO-DATA: could not read the staged diff: %s"
                               % (proc.stderr or "").strip()]
    added = list(parse_added_lines(proc.stdout or ""))
    dash_hits = scan_dashes(added)
    terms = load_terms(terms_path)

    lines = []
    found = False
    for path, lineno in dash_hits:
        found = True
        lines.append("BLOCK dash: %s:%d" % (path, lineno))

    if terms is None:
        lines.append(
            "NO-DATA: no terms file at %s, so the private-terms check did "
            "not run. This is not a pass."
            % (terms_path or os.path.expanduser("~/.brothersbe-private-names")))
    elif not terms:
        lines.append("NO-DATA: the terms file is empty, so the "
                      "private-terms check did not run.")
    else:
        for path, lineno, lengths in scan_private_terms(added, terms):
            found = True
            # The term itself is NEVER printed, only how long it is.
            lines.append(
                "BLOCK private term: %s:%d (matched term length %s, term "
                "not printed)"
                % (path, lineno, ",".join(str(n) for n in lengths)))

    if found:
        return EXIT_FOUND, lines
    if terms is None or not terms:
        return EXIT_NO_DATA, lines
    lines.append("PASS: %d added line(s) checked, no new dash and no "
                  "private term" % len(added))
    return EXIT_CLEAN, lines


def hooks_dir(cwd=None, runner=None):
    """The real hooks directory for the repository containing cwd, resolved
    with `git rev-parse --git-path hooks` rather than a literal
    "<toplevel>/.git/hooks": inside a git WORKTREE (this task's own repo is
    one), `.git` is a file pointing elsewhere, hooks are shared at the
    common git dir, and a literal join would try to descend into a file.
    None on any git failure (e.g. not a repository)."""
    runner = runner or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd))
    proc = runner(["git", "rev-parse", "--git-path", "hooks"])
    if proc.returncode != 0:
        return None
    path = (proc.stdout or "").strip()
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    return os.path.abspath(path)


def install_hook(cwd=None):
    """Install (or compose into) .git/hooks/pre-commit. Unlike
    install_gate_hook.sh's pre-push installer, which backs up and REPLACES a
    foreign hook wholesale, this COMPOSES with one: an existing pre-commit
    hook is somebody else's work, so the fix is to append our call, never to
    move it aside."""
    hd = hooks_dir(cwd)
    if hd is None:
        print("NO-DATA: not inside a git repository, nothing installed",
              file=sys.stderr)
        return EXIT_NO_DATA
    os.makedirs(hd, exist_ok=True)
    hook_path = os.path.join(hd, "pre-commit")
    call = ('python3 "$(git rev-parse --show-toplevel)/scripts/'
            'self_check_staged.py" || exit 1\n')
    if os.path.exists(hook_path):
        with open(hook_path, encoding="utf-8") as fh:
            existing = fh.read()
        if MARK in existing:
            print("ALREADY INSTALLED: %s already calls "
                  "self_check_staged.py" % hook_path)
            return EXIT_CLEAN
        with open(hook_path, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n%s\n%s" % (MARK, call))
        print("COMPOSED: appended self_check_staged.py to the existing "
              "pre-commit hook at %s" % hook_path)
        return EXIT_CLEAN
    with open(hook_path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n%s\n"
                 "# installed by scripts/self_check_staged.py --install-hook\n"
                 "%s" % (MARK, call))
    os.chmod(hook_path, 0o755)
    print("INSTALLED: %s now runs self_check_staged.py before every commit"
          % hook_path)
    return EXIT_CLEAN


def check_hook(cwd=None):
    hd = hooks_dir(cwd)
    if hd is None:
        print("NO-DATA: not inside a git repository", file=sys.stderr)
        return EXIT_NO_DATA
    hook_path = os.path.join(hd, "pre-commit")
    if not os.path.exists(hook_path):
        print("NOT INSTALLED: %s does not exist" % hook_path)
        return EXIT_FOUND
    with open(hook_path, encoding="utf-8") as fh:
        content = fh.read()
    if MARK in content:
        print("INSTALLED: %s calls self_check_staged.py" % hook_path)
        return EXIT_CLEAN
    print("FOREIGN HOOK: %s exists but does not call self_check_staged.py "
          "yet" % hook_path)
    return EXIT_FOUND


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--terms", help="private-terms file "
                     "(default ~/.brothersbe-private-names)")
    ap.add_argument("--repo", default=None,
                     help="run git diff --cached in this directory "
                          "(default: current directory)")
    ap.add_argument("--install-hook", action="store_true",
                     help="install, or compose into, .git/hooks/pre-commit")
    ap.add_argument("--check-hook", action="store_true",
                     help="report whether the pre-commit hook is installed")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.install_hook:
        return install_hook(args.repo)
    if args.check_hook:
        return check_hook(args.repo)

    code, lines = run_scan(args.repo, args.terms)
    stream = sys.stdout if code == EXIT_CLEAN else sys.stderr
    for line in lines:
        print(line, file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
