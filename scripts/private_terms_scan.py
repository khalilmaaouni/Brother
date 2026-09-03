"""private_terms_scan: refuse a push that would publish a private term.

The estate's NO PRIVATE CONTENT IN PUBLIC law has said "UNENFORCED by hook,
stated plainly" since it was written, and named this as the candidate control
nobody built. This is it.

WHY IT EXISTS RATHER THAN A HABIT. A working tree scan is not enough and never
was: the law binds the HISTORY, because a repository that ever held the content
still holds it in its objects and deleting the file does not remove it. So this
scans the OUTGOING RANGE, every object a push would actually send, not the files
currently on disk.

THE TRAP THIS DESIGN AVOIDS, learned from the thing that prompted it. A scanner
that looks for a forbidden term has to contain that term to test itself, and a
scanner committed WITH its fixtures publishes exactly what it exists to stop.
That already happened here: two commits in a sibling repository carried real
client terms inside this scanner's own test fixtures, and a later commit had to
generalize them back out. Neither reached a remote, so nothing was published,
but the shape of the mistake is permanent.

So the term list lives OUTSIDE every repository, at ~/.brothersbe-private-names
(the ONE file the estate's law names, shared with bm_private_scan.py and the
assurance product's history test since 2026-09-03, readiness row E37; the
earlier ~/.claude/private-terms.txt default was a second copy nothing kept in
step), one term per line, and is never committed anywhere. This file contains
no terms at all, which is why it is safe to publish and why its own tests pass
fake terms in rather than real ones.

MATCHING, and the two ways a naive version gets it wrong:

  * A term matched as a bare substring hits ordinary words: a four letter
    client code sits inside common English, so a match has to be bounded to
    a whole word.
  * A term matched only in the case it happens to be STORED in misses every
    other spelling: a name written in prose appears capitalized, lowercased
    and in a path.

The rule is therefore: the LENGTH of the term decides its strictness, never
its spelling, mirroring scripts/cleanse.sh's own `${#needle} -le 5` branch. A
term of five characters or fewer, or longer, matches as a whole word, case
insensitively.

CORRECTED 2026-09-03 (readiness row E34): the previous rule branched on the
term's STORED spelling (`term.isupper()`), which took the case-sensitive path
for this estate's upper-cased short terms and missed a lowercase occurrence
of one in a candidate export tree that a review had already flagged.

NO-DATA IS NEVER A PASS. No terms file means the scan did not run, and it says
so and exits non-zero rather than reporting a clean tree it never examined. A
missing list is the most likely way this control silently stops working.

Python 3, standard library only. No network.
"""
import argparse
import os
import re
import subprocess
import sys

#: Outside every repository, on purpose. See the module docstring: a term list
#: committed alongside the scanner publishes what the scanner exists to stop.
DEFAULT_TERMS_FILE = os.path.expanduser("~/.brothersbe-private-names")

#: WHOLE WORD means bounded by anything that is not a letter or a digit
#: (E37, 2026-09-03). `\b` counted the underscore as a word character, so a
#: spelling like path_<term>_file walked through; the assurance product's
#: history test (isalnum bounds) caught the same spelling. `[^\W_]` is
#: "letter or digit", so these lookarounds refuse a letter or digit on
#: either side and accept everything else, the underscore included.
_NOT_ALNUM_BEFORE = r"(?<![^\W_])"
_NOT_ALNUM_AFTER = r"(?![^\W_])"

EXIT_CLEAN = 0
EXIT_FOUND = 1
EXIT_NO_DATA = 2


def load_terms(path=None):
    """Every non-blank, non-comment line. Returns None when the file is absent,
    which the caller must treat as NO-DATA and never as an empty list: an empty
    list would make every scan pass."""
    path = path or DEFAULT_TERMS_FILE
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
    """A compiled matcher whose strictness is decided by the term's LENGTH,
    never its spelling.

    Every term matches as a WHOLE WORD, case insensitively: a term glued
    directly to other letters or digits does not match, while a hyphenated,
    underscored or space-separated compound still does, because those are
    word boundaries (the underscore since E37, 2026-09-03: see
    _NOT_ALNUM_BEFORE above). The length check below is kept because it is the
    visible trace of the rule, and it mirrors scripts/cleanse.sh's own
    `${#needle} -le 5` branch, whose two arms both now run `grep -niwF`
    (whole word, case insensitive): cleanse.sh's short arm got that in a
    2026-08-26 correction, its long arm in a SEPARATE 2026-08-30 one (commit
    ea1bb937, "the gate stops matching long terms inside English words").
    That second correction is the current, tested, deliberate behavior of
    both arms; an older comment above the `if` in that file still describes
    the long arm's PRE-correction substring match and was never updated
    after that later fix landed.

    CORRECTED 2026-09-03 (readiness row E34): this function used to branch on
    `term.isupper()`, the term's STORED spelling, so a short term this estate
    keeps in upper case took the case-sensitive branch and missed a
    lowercase occurrence of it in content a scan had already reached.
    """
    bounded = _NOT_ALNUM_BEFORE + re.escape(term) + _NOT_ALNUM_AFTER
    if len(term) <= 5:
        return re.compile(bounded, re.IGNORECASE)
    return re.compile(bounded, re.IGNORECASE)


def scan_text(text, terms):
    """Every term that appears. Returns a list, empty when the text is clean."""
    return [t for t in terms if pattern_for(t).search(text)]


def outgoing_range(remote="origin", branch=None, runner=None):
    """What a push would actually send: the commits on this branch that the
    remote does not have. Falls back to the whole branch when the remote has no
    copy of it yet, which is the first push and the one that matters most."""
    runner = runner or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True))
    branch = branch or runner(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    probe = runner(["git", "rev-parse", "--verify", "--quiet",
                    "%s/%s" % (remote, branch)])
    if probe.returncode != 0 or not probe.stdout.strip():
        return branch          # remote has never seen this branch
    return "%s/%s..%s" % (remote, branch, branch)


def scan_range(rev_range, terms, runner=None):
    """The patch text of every commit in the range, which is what a push sends.

    Reads the diff rather than the tree on purpose: a term deleted in the last
    commit is still in the objects, and this control exists precisely because
    deleting the file does not remove it."""
    runner = runner or (lambda cmd: subprocess.run(
        cmd, capture_output=True, text=True))
    proc = runner(["git", "log", "-p", "--no-color", rev_range])
    if proc.returncode != 0:
        return None, (proc.stderr or "").strip()
    return scan_text(proc.stdout, terms), ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--terms", help="term list (default ~/.brothersbe-private-names)")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch")
    ap.add_argument("--range", dest="rev_range",
                    help="scan this range instead of computing the outgoing one")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.selftest:
        return _selftest()

    terms = load_terms(args.terms)
    if terms is None:
        print("NO-DATA: no terms file at %s, so nothing was scanned. This is "
              "not a pass: a control with no list silently stops working and "
              "keeps reporting green."
              % (args.terms or DEFAULT_TERMS_FILE), file=sys.stderr)
        return EXIT_NO_DATA
    if not terms:
        print("NO-DATA: the terms file is empty, so nothing was scanned.",
              file=sys.stderr)
        return EXIT_NO_DATA

    rev_range = args.rev_range or outgoing_range(args.remote, args.branch)
    found, err = scan_range(rev_range, terms)
    if found is None:
        print("NO-DATA: could not read %s: %s" % (rev_range, err), file=sys.stderr)
        return EXIT_NO_DATA
    if found:
        print("REFUSED: %d private term(s) appear in what this push would send "
              "(%s). The fix is NEVER a scrub of this repository, because the "
              "objects keep it: extract the shippable part into a fresh "
              "repository instead."
              % (len(found), rev_range), file=sys.stderr)
        # The terms themselves are NOT printed. Printing them would put them in
        # a terminal, a CI log and a transcript, which is the thing being
        # prevented. The count and the range are enough to act on.
        return EXIT_FOUND
    print("PASS: %d term(s) checked against %s, none present" % (len(terms), rev_range))
    return EXIT_CLEAN


def _selftest():
    """Fake terms only. Real ones live outside every repository."""
    cases = [
        ("ACME", "we shipped to ACME today", True, "short term, whole word, present"),
        ("ACME", "he was in a acmestic mood", False, "must not match inside a word"),
        ("ACME", "we shipped to acme today", True,
         "CORRECTED 2026-09-03 (E34): a short term stored upper case now "
         "matches a lowercase occurrence too, because strictness follows "
         "LENGTH, never the stored spelling"),
        ("LONGACME", "the sponsor was longacme this year", True,
         "the same E34 fix for a LONG all-caps term: length decides, not "
         "spelling, so this now matches too"),
        ("Lakeside", "the lakeside bottler", True, "mixed case matches any casing"),
        ("Lakeside", "LAKESIDE BOTTLER", True, "mixed case matches upper too"),
        ("Lakeside", "a lake, then a side", False, "no false positive on the parts"),
        ("Lakeside", "xxLakesidexx should not match", False,
         "a long term glued inside another word must not match either, "
         "mirroring cleanse.sh's own whole-word branch for terms over five "
         "characters"),
        ("alisa", "a battery serialisation rule", False,
         "CORRECTED 2026-08-30: a lowercase term must not match inside an ordinary word"),
        ("alisa", "we met alisa yesterday", True,
         "the same lowercase term still matches as its own word, any casing"),
        ("alisa", "the alisa-app stream", True,
         "hyphenated compounds still match, hyphens are word boundaries"),
        ("ACME", "see path_acme_file for it", True,
         "E37 2026-09-03: an underscore is a boundary, not a word character, "
         "so a term joined by underscores is a hit"),
        ("Lakeside", "the lakeside_export dir", True,
         "the same for a long term with an underscore on one side only"),
        ("ACME", "an xxacmexx token", False,
         "a term glued inside a longer run of letters is still not a hit"),
    ]
    bad = []
    for term, text, want, why in cases:
        got = bool(scan_text(text, [term]))
        if got != want:
            bad.append((term, text, want, got, why))
    for term, text, want, got, why in bad:
        print("private_terms_scan: FAIL %r in %r wanted %s got %s (%s)"
              % (term, text, want, got, why), file=sys.stderr)
    if bad:
        return 1
    if load_terms("/no/such/terms/file") is not None:
        print("private_terms_scan: FAIL a missing file must be None, never []",
              file=sys.stderr)
        return 1
    print("private_terms_scan: OK, %d matching case(s) and the missing-file "
          "NO-DATA case" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
