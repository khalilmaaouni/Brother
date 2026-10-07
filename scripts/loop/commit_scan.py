#!/usr/bin/env python3
"""GATE, not a report: scan the STAGED diff's ADDED lines; exit 1 on any hit so a chained commit cannot run.

usage (repo root): commit_scan.py && git commit ...      Prints counts only, never the matched text.
           check: commit_scan.py --selftest

WHY THE PATTERNS ARE WRITTEN WITH \\x ESCAPES. This file must contain the literals it forbids in
order to forbid them, and a scanner that matches its own source refuses every commit that touches
it. Writing the first character as a hex escape keeps the regex identical at runtime while the
source never carries the bare literal. Never "simplify" these back to plain text.

CASE SENSITIVITY IS THE WHOLE POINT OF THE SPLIT BELOW. Probed 2026-09-21 against this gate as
executed code: a lowercase password assignment was caught, while the same assignment spelled in
capitals and in mixed case both PASSED the gate, and the bearer token prefix was caught in
capitals but passed in lowercase. The secrets pattern carried no ignore case flag although the
attribution pattern did. So the two classes are now separated:

The examples in this file are built from hex escapes for the same reason the patterns are: a
scanner that spells out the strings it forbids refuses every commit that touches itself. That
is not hypothetical, it happened on the first run of this very selftest.

  STRUCTURED tokens keep their case, because their case is part of the format. An AWS key really
  is AKIA in capitals and a GitHub token really is ghp_ in lowercase, so matching them loosely
  buys nothing and costs false positives on ordinary prose.

  WORD-LIKE assignments are matched case insensitively, because `PASSWORD=`, `Password=` and
  `password=` are the same mistake and only a machine thinks otherwise.
"""
import os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade_build as G

# ONE TABLE, ONE MODULE. The secret families, the strict subset, the public example rule and the
# diff extraction now live in scripts/loop/secret_scan.py, which BOTH this commit gate and the
# pre push gate read, so the two definitions can no longer drift apart (WBS unit FX-07, post
# mortem row 23). The import is guarded and the failure is loud: a gate that cannot load its own
# definition has scanned nothing, so it says NO-DATA and exits 2.
try:
    import secret_scan as S
except (ImportError, SyntaxError) as exc:
    S = None
    SECRET_SCAN_ERROR = type(exc).__name__
else:
    SECRET_SCAN_ERROR = None

# The two tables that used to live here are GONE. The one union table, its family names, its
# per alternative case rules, the strict subset, the public example strip and the diff extraction
# now live in secret_scan.py, and both gates read that one file (WBS unit FX-07).

# built from code points, never typed: this file is scanned by its own gate, and the
# no dash law bans the literal characters everywhere, including inside the scanner.
DASHES = re.compile("[" + chr(0x2014) + chr(0x2013) + "]")
ATTRIBUTION = re.compile(r"(?i)\x43o-Authored-By|\x6eoreply@anthropic|\x47enerated with \[")


def scan(added):
    """Counts only, never the matched text: a gate that prints the secret it caught has leaked it
    into the terminal, the log, and whatever scrapes them."""
    return {"secrets": S.count(added),
            "long dashes": len(DASHES.findall(added)),
            "attribution": len(ATTRIBUTION.findall(added)),
            "private terms": G.private_hits(added)}


def selftest():
    """A selftest that RAISES has not reported a verdict, it has abandoned the question.

    MEASURED 2026-09-21 on this exact file: injecting a raise into one case expression killed
    this function with a traceback at EXIT 1, which is the same exit code an honest failure
    returns. A pipeline reading the code and a human reading the text then describe the same run
    differently, and the human gets a stack trace where a verdict belongs.

    The cases below are built EAGERLY, so one bad expression takes the whole run with it. Turning
    every case into a lambda would fix that too, but it is a large diff whose own risk is a
    transcription error in a case nobody would then notice was wrong. Wrapping the body is four
    lines, carries no such risk, and covers the NEXT case someone adds here without its author
    doing anything: whatever escapes is reported as a FAILED case naming the exception, and the
    exit code still says 1, so the verdict and the code agree in every direction.
    """
    if S is None:
        print("selftest: FAILED, secret_scan could not be loaded (%s)" % SECRET_SCAN_ERROR)
        return 1
    try:
        return _selftest_body()
    except Exception as exc:                       # noqa: BLE001 - a verdict beats a traceback
        print("selftest: FAILED before it could finish, %s: %s"
              % (type(exc).__name__, str(exc)[:120]))
        return 1


def _selftest_body():
    def counted(text):
        """Every counter EXCEPT `private terms`, which is a property of the MACHINE and not of the
        text under test. grade_build.private_hits fails closed on purpose and returns 1 for every
        text when ~/.brothersbe-private-names is unreadable, which is right for a gate and fatal
        for a fixture. Measured 2026-09-21 under an empty HOME: three cases below went red for
        that reason alone while the scanner behaved perfectly, so the module could not be verified
        on a machine that is not this one. The orthogonality those cases exist for is untouched:
        the three counters still read are the three a fixture can actually trip.
        """
        return {k: v for k, v in scan(text).items() if k != "private terms"}

    def _D(line):
        """One single line hunk carrying exactly `line`, so a fixture tests the extraction and
        nothing else. The file header is fixed and deliberately boring."""
        return "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n" + line + "\n"

    cases = [
        ("lowercase password is caught", scan("\x70assword=hunter2")["secrets"] == 1),
        ("UPPERCASE PASSWORD is caught", scan("\x50ASSWORD=hunter2")["secrets"] == 1),
        ("Mixed case Password is caught", scan("\x50assword=hunter2")["secrets"] == 1),
        ("passwd is caught", scan("\x70asswd=hunter2")["secrets"] == 1),
        ("capital Bearer is caught", scan("\x42earer abcdefgh12345678")["secrets"] == 1),
        ("lowercase bearer is caught", scan("\x62earer abcdefgh12345678")["secrets"] == 1),
        ("api_key is caught", scan("\x41PI_KEY=abcdefgh12345678")["secrets"] == 1),
        ("an AWS style key is caught", scan("\x41KIA" + "ABCD1234")["secrets"] == 1),
        # THE SHAPES THIS ESTATE ACTUALLY HOLDS. Each was measured passing the old pattern.
        ("an openrouter key is caught", scan("\x73k-or-v1-" + "a" * 40)["secrets"] >= 1),
        ("an openai project key is caught", scan("\x73k-proj-" + "b" * 30)["secrets"] >= 1),
        ("a github fine grained token is caught", scan("\x67ithub_pat_" + "c" * 20)["secrets"] >= 1),
        ("a slack token is caught", scan("\x78oxb-" + "1234567890-abcdef")["secrets"] >= 1),
        # TWO ALTERNATIVES THE PATTERN CARRIED WITH NO CASE OF THEIR OWN. Probed 2026-09-21: both
        # are caught today, so either could be deleted from STRUCTURED with all 19 cases staying
        # green. A guard nothing can turn red is not a guard. Asserted ORTHOGONALLY, the only two
        # cases here that are: each fixture must fire ONE counter, because a fixture that also
        # trips the dash or attribution guard would still pass an assertion that reads one key.
        ("a classic github token is caught",
         counted("\x67hp_" + "c" * 36) == {"secrets": 1, "long dashes": 0, "attribution": 0}),
        ("a private key header is caught",
         counted("\x2d----" + "\x42EGIN" + " RSA " + "\x50RIVATE" + " KEY" + "-----") == {"secrets": 1, "long dashes": 0, "attribution": 0}),
        # ROW 23, the measured drift: the push table matched the private key header WITHOUT its
        # five dash fences, while this gate required the fences, so one build committed clean and
        # the push refused it. The union carries the fence free form and the fenced form is a
        # subset of it.
        ("a fence free private key header is caught",
         counted("\x42EGIN" + " RSA " + "\x50RIVATE" + " KEY") == {"secrets": 1, "long dashes": 0, "attribution": 0}),
        ("ordinary prose with a dash is still clean",
         scan("a well-known and long-standing convention")["secrets"] == 0),
        ("an em dash is caught", scan("a sentence " + chr(0x2014) + " here")["long dashes"] == 1),
        ("an en dash is caught", scan("a range 1" + chr(0x2013) + "2")["long dashes"] == 1),
        ("an attribution trailer is caught", scan("\x43o-Authored-By: someone")["attribution"] == 1),
        ("ordinary prose is clean", sum(counted("a perfectly ordinary line of code").values()) == 0),
        ("the word alone, with no assignment, is not a hit", scan("the \x70assword policy document")["secrets"] == 0),
        ("this file's own source does not trip the gate",
         sum(counted(open(__file__, encoding="utf-8").read()).values()) == 0),
        # THE EXTRACTION, which had no case of its own until 2026-09-21 and carried a real hole.
        # Each fixture below isolates ONE property of added_text, because a fixture that trips two
        # of them proves neither.
        ("added content is extracted from a hunk",
         added_text(_D("+" + "\x61pi_key=abcdefgh12345678")).splitlines()[-1] == "\x61pi_key=abcdefgh12345678"),
        ("added content that itself begins with two plus signs is NOT lost as a header",
         "\x61pi_key" in added_text(_D("+++" + "\x61pi_key=abcdefgh12345678"))),
        ("and the gate therefore catches it",
         scan(added_text(_D("+++" + "\x61pi_key=abcdefgh12345678")))["secrets"] == 1),
        ("a removed line is not treated as added",
         added_text(_D("-" + "\x61pi_key=abcdefgh12345678")) .find("\x61pi_key") == -1),
        ("a context line is not treated as added",
         added_text(_D(" ordinary context")).find("ordinary context") == -1),
        ("the real file header is still not treated as content",
         "dev/null" not in added_text("diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n@@ -0,0 +1 @@\n+ok\n")),
        ("the path of a touched file IS scanned, since a name carries terms too",
         "b/x.py" in added_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n+ok\n")),
        ("a line inside a hunk that looks like a hunk header is still content",
         "@@ not a header" in added_text(_D("+@@ not a header"))),
        ("an empty diff extracts nothing rather than raising", added_text("") == ""),
        # A diff with no hunk still carries its names, and ONLY its names: a hash or a mode on a
        # metadata line is never read as content. Until 2026-09-27 this case expected "", which
        # asserted the very hole finding 5 of the landing audit found.
        ("a diff with no hunk at all extracts only its paths",
         added_text("diff --git a/x b/x\nsimilarity index 100%\nindex 111..222 100644\n") == "a/x b/x"),
        # THE HUNK TRACKING ITSELF, which had no case only it could refuse. Measured
        # 2026-09-21: replacing `if not in_hunk` with `if False` left all 31 cases green,
        # because the header line +++ b/x.py then falls through to the added-line branch
        # and is emitted MANGLED as "++ b/x.py", which still contains the path the path
        # case looks for. So the property that distinguishes the two is that nothing
        # outside a hunk is ever taken RAW. Deliberately uses plain content, since real
        # added content may legitimately begin with a plus sign.
        ("nothing outside a hunk is taken as raw content",
         not any(l.startswith("+") for l in
                 added_text("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n+ok\n").splitlines())),
        # NAMES WITH NO +++ HEADER (finding 5, 2026-09-27). Git prints +++ and --- only for an entry
        # with a text hunk, so a pure rename, a copy, an empty new file and a binary file named a term
        # the scan never saw. Each fixture carries the name on exactly ONE line, so each case can only
        # be refused by the rule that reads that line.
        ("a rename's new name is scanned though git prints no +++ header",
         "NEWNAME" in added_text("diff --git a/x b/x\nsimilarity index 100%\nrename from x\nrename to NEWNAME\n")),
        ("a rename's old name is scanned too",
         "OLDNAME" in added_text("diff --git a/x b/x\nsimilarity index 100%\nrename from OLDNAME\nrename to x\n")),
        ("a copy's new name is scanned",
         "NEWNAME" in added_text("diff --git a/x b/x\nsimilarity index 100%\ncopy from x\ncopy to NEWNAME\n")),
        ("a copy's source name is scanned too",
         "OLDNAME" in added_text("diff --git a/x b/x\nsimilarity index 100%\ncopy from OLDNAME\ncopy to x\n")),
        ("the diff --git line's names are scanned, the only name an empty new file carries",
         "NEWNAME" in added_text("diff --git a/x b/NEWNAME\nnew file mode 100644\nindex 0000000..e69de29\n")),
        ("a header line nobody classified is scanned, never skipped",
         "NEWNAME" in added_text("diff --git a/x b/x\nBinary files /dev/null and b/NEWNAME differ\n")),
    ]
    bad = [n for n, ok in cases if not ok]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


# The extraction and the two header tables now live in the one module both gates read. The names
# stay module level, bound to the shared module, so the selftest and every caller that reaches for
# them still find them, and so there is exactly one copy of the rule (WBS unit FX-07).
if S is not None:
    added_text = S.added_text
    NAMELESS = S.NAMELESS
    NAMED = S.NAMED


def staged_diff():
    """(diff text, None) or (None, why). The read is pinned against the repository's own config: a
    colour setting turned every added line into escape codes (an added credential scanned secrets=0),
    an external diff driver replaces the patch outright, and quoted paths turn a non ASCII name into
    octal escapes no term can match. A git that fails is NO-DATA even when it printed something, since
    a fragment that scans clean is not a clean scan."""
    try:
        r = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--cached", "--no-color", "--no-ext-diff"],
                           capture_output=True, text=True, encoding="utf-8")
    except (OSError, ValueError) as exc:
        return None, "git diff --cached could not run (%s)" % type(exc).__name__
    if r.returncode != 0:
        return None, "git diff --cached exited %d" % r.returncode
    return r.stdout, None


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if S is None:
        print("NO-DATA: secret_scan could not be loaded (%s), so nothing was scanned"
              % SECRET_SCAN_ERROR)
        return 2                                    # never 0: an unscanned change is not a clean scan
    diff, why = staged_diff()
    if why:
        print("NO-DATA: %s, so the staged change was not read" % why)
        return 2                                    # never 0: an unread change is not a clean scan
    if not diff.strip():
        print("NO-DATA: nothing staged")
        return 2                                    # never 0: nothing staged is not a clean scan
    added = added_text(diff)
    hits = scan(added)
    names = S.families(added) if hits["secrets"] > 0 else []
    counts_line = " ".join("%s=%d" % kv for kv in hits.items())
    if names:
        print("secret shapes: " + ", ".join(names))
    print(counts_line)
    return 1 if any(hits.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
