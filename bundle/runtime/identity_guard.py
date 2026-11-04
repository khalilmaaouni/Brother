"""identity_guard: refuse NEW commits whose author or committer email domain
carries a private term, and refuse a git identity configured the same way.

WHY THIS EXISTS. 48 public commits in this estate carried the founder's work
email, because the identity a commit ships with comes from `git config`, not
from any file this repository's other scans read. cleanse.sh and
private_terms_scan.py open files and diffs; neither one opens a commit's
author or committer line. This is the mechanical third of the founder ruling
"Shield now, rewrite later" (docs/decisions/2026-08-30-work-email-in-public-
commits.html): a battery check that stops the leak from recurring while the
history rewrite itself stays a separate, deliberate, founder-only act.

SCOPE, DELIBERATELY NARROW. This guard reads the OUTGOING RANGE only, the
commits HEAD carries that origin's default branch does not: exactly what the
next push would add. Already-pushed history is out of scope on purpose, not
an oversight; scrubbing it is the queued rewrite decision, and a battery
check that tried to re-litigate that here would just be a second, competing
opinion about a call the founder already made once.

TERM LIST, same convention as cleanse.sh. It lives OUTSIDE every repository,
at BROTHER_PRIVATE_TERMS or ~/.brothersbe-private-names by default, and is
never printed by this script. A missing list means the guard did not run,
which is reported as NO-DATA and never as a pass: a control that opened
nothing has proved nothing about the identity it was supposed to check.

MATCHING. Case-insensitive, whole-token: an email domain or a config name
that contains a listed term as a standalone word is a hit, the same
whole-word discipline cleanse.sh uses to stop a short term from matching
inside an unrelated English word.

Findings name only the short SHA and the term's position in the list
(NAME-N), never the address or the term itself, for the same reason the
sibling scanners never print a hit: the terminal, the CI log and this
session's own transcript are all surfaces the law being enforced forbids.

Python 3, standard library only. No network.
"""
import os
import re
import subprocess
import sys

DEFAULT_TERMS_FILE = os.path.join(os.path.expanduser("~"), ".brothersbe-private-names")

EXIT_CLEAN = 0
EXIT_FOUND = 1
EXIT_NO_DATA = 2


def terms_path():
    """BROTHER_PRIVATE_TERMS overrides the default, exactly like cleanse.sh's
    ``TERMS="${BROTHER_PRIVATE_TERMS:-$HOME/.brothersbe-private-names}"``."""
    return os.environ.get("BROTHER_PRIVATE_TERMS") or DEFAULT_TERMS_FILE


def load_terms(path=None):
    """Every non-blank, non-comment line. None means the file is absent,
    which the caller must treat as NO-DATA and never as an empty list: an
    empty list would make every identity look clean."""
    path = path or terms_path()
    if not os.path.exists(path):
        return None
    terms = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(line)
    return terms


def pattern_for(term):
    """Case-insensitive, whole-token: a term must appear as a standalone
    word, not merely as a substring, so a short term does not fire inside an
    unrelated word."""
    return re.compile(r"\b%s\b" % re.escape(term), re.IGNORECASE)


def hit_terms(text, terms):
    """Every term (from `terms`) that appears in `text`. Empty list when
    `text` is clean or empty."""
    text = text or ""
    return [t for t in terms if pattern_for(t).search(text)]


def domain_of(email):
    """The part after '@', or '' when there is no '@' to split on."""
    email = (email or "").strip()
    return email.rsplit("@", 1)[1] if "@" in email else ""


def _git(args, cwd=None, runner=None):
    runner = runner or (lambda cmd: subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True))
    return runner(["git"] + args)


def config_identity(cwd=None, runner=None):
    """(name, email) from `git config` in the repo at `cwd` (process cwd
    when None), the same repo a commit made there would carry."""
    name = _git(["config", "user.name"], cwd, runner).stdout.strip()
    email = _git(["config", "user.email"], cwd, runner).stdout.strip()
    return name, email


def default_remote_branch(remote="origin", cwd=None, runner=None):
    """origin's default branch as 'origin/main', or None when that
    remote-tracking ref has never been set. A clone sets it automatically;
    a hand-built remote needs `git remote set-head` or a fetch that does."""
    proc = _git(["symbolic-ref", "--short", "refs/remotes/%s/HEAD" % remote],
                cwd, runner)
    ref = proc.stdout.strip()
    if proc.returncode != 0 or not ref:
        return None
    return ref


def outgoing_range(remote="origin", cwd=None, runner=None):
    """The commits HEAD carries that origin's default branch does not: what
    the next push would actually add. (None, note) when origin/HEAD was
    never set, so the caller reports NO-DATA instead of guessing a branch or
    silently scanning nothing."""
    default = default_remote_branch(remote, cwd, runner)
    if not default:
        return None, ("no %s/HEAD ref; cannot tell which branch the outgoing "
                       "range would compare against" % remote)
    return "%s..HEAD" % default, ""


def commits_in_range(rev_range, cwd=None, runner=None):
    """(list of (short_sha, author_email, committer_email), '') for every
    commit in `rev_range`, or (None, stderr) when git could not read it. An
    empty list is a valid, clean result: an empty outgoing range."""
    proc = _git(["log", rev_range, "--format=%h%x1f%ae%x1f%ce"], cwd, runner)
    if proc.returncode != 0:
        return None, (proc.stderr or "").strip()
    commits = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append(tuple(parts))
    return commits, ""


def run_guard(cwd=None, runner=None):
    """The full check. Returns (exit_code, [report lines]). Never prints a
    term value or an email address; findings name only NAME-N (the term's
    1-based position in the list) and, for a commit, its short SHA."""
    terms = load_terms()
    if terms is None:
        return EXIT_NO_DATA, [
            "NO-DATA: no private terms file at %s; refusing to certify."
            % terms_path()]
    if not terms:
        return EXIT_NO_DATA, [
            "NO-DATA: the terms file at %s is empty; refusing to certify."
            % terms_path()]

    lines = []
    fail = False

    name, email = config_identity(cwd, runner)
    config_text = "%s\n%s" % (name, domain_of(email))
    for i, term in enumerate(terms, 1):
        if hit_terms(config_text, [term]):
            lines.append("FAIL: the git config identity carries NAME-%d" % i)
            fail = True

    rev_range, note = outgoing_range(cwd=cwd, runner=runner)
    if rev_range is None:
        lines.append("NO-DATA: %s" % note)
    else:
        commits, err = commits_in_range(rev_range, cwd, runner)
        if commits is None:
            lines.append("NO-DATA: could not read %s: %s" % (rev_range, err))
        else:
            for sha, author_email, committer_email in commits:
                combined = "%s\n%s" % (domain_of(author_email), domain_of(committer_email))
                for i, term in enumerate(terms, 1):
                    if hit_terms(combined, [term]):
                        lines.append(
                            "FAIL: commit %s carries NAME-%d" % (sha, i))
                        fail = True

    if fail:
        return EXIT_FOUND, lines
    if rev_range is None:
        lines.append("NO-DATA: identity clean but the outgoing range could "
                      "not be determined, so no commit was actually scanned")
        return EXIT_NO_DATA, lines
    lines.append("PASS: identity clean, %d term(s) checked against %s"
                  % (len(terms), rev_range))
    return EXIT_CLEAN, lines


def main(argv=None):
    del argv  # no flags: check_all.sh and a direct run both take none.
    code, lines = run_guard()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
