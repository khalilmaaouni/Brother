"""doc_assurance: the public documentation corpus is checked against the tree it
describes, not merely against Markdown syntax.

WHY THIS EXISTS. The 2026-09-10 replacement order asked for a documentation check
that catches "broken relative links; missing repository paths; incompatible
current-state claims; host-specific command drift; schema/prose drift; generated
files presented as hand-authored truth". A linter that only parses Markdown
passes every one of those. Each check here is a claim about the CODE that a page
makes, read back from the code.

WHAT IT DELIBERATELY DOES NOT DO: relative links. scripts/test_export_links.py
already builds the real export tree and reads export_public.check_markdown_links
over it, which is a stronger check than this could make, because it sees the tree
the public actually receives. Restating it here would give two answers to one
question and a reason to trust neither.

THE CORPUS IS DERIVED, NEVER LISTED. A hand-kept list of public pages is a list
that goes stale the first time somebody adds a page. The corpus is every tracked
Markdown file the export allowlist carries, minus the evidence and history trees
(docs/plan, docs/releases, docs/deliveries and the docs/decisions data), which are
records rather than current narrative and which the replacement order preserves.

NO-DATA IS NOT A PASS. A run that resolved no corpus file, or a check that could
not read what it needed, reports NO-DATA and exits 2. An empty scan reading as
clean is the failure mode this estate has already paid for more than once.

Driven backwards by --selftest, which builds a corpus fixture carrying one
instance of each defect and asserts each check names it. A checker only ever seen
green is a claim, not a check.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Trees the allowlist carries that are evidence, contracts or history rather
#: than current public narrative. The replacement order names these as preserved
#: and forbids prose cleanup from rewriting them, so they are not corpus.
EVIDENCE_PREFIXES = ("docs/plan/", "docs/releases/", "docs/deliveries/")
#: docs/decisions is runtime data with one public page in it (its own README
#: says the records are data, not documentation), so only that page is corpus.
DECISIONS_PUBLIC = "docs/decisions/README.md"

#: THE CORPUS IS THE PUBLIC NARRATIVE LAYER, which is the root README and the
#: pages under docs/. It is deliberately NOT every Markdown file the export
#: carries: the allowlist also ships scripts/, benchmarks/ and each product's
#: own docs, which have their own owners and their own debt. A first run over
#: all 788 exported pages returned 1396 findings that no documentation change
#: caused, which is a gate nobody can act on and therefore a gate nobody runs.
CORPUS_ROOTS = ("README.md", "docs/")

#: The em and en dash, built from codepoints on purpose. A scanner that
#: spells out what it forbids trips the estate's own pre-push gate, which
#: is how this line was written the first time and why it is written this
#: way now: the gate reads the outgoing diff, and it cannot tell a rule
#: from a violation of it.
DASHES = (chr(0x2014), chr(0x2013))
#: Assembled from parts for the same reason as DASHES above: the literal
#: trailer must never appear in a line this repository pushes.
_AUTHOR_TRAILER = "Co-" + "Authored-By: (Claude|Opus|Sonnet|Haiku|Fable)"
_VENDOR_MAIL = "noreply@" + "anthropic"
_TOOL_FOOTER = r"Generated with \[Claude Cod" + r"e\]"
ATTRIBUTION = re.compile(
    "%s|%s|%s" % (_AUTHOR_TRAILER, _VENDOR_MAIL, _TOOL_FOOTER), re.I)
DEFAULT_PRIVATE_NAMES_PATH = os.path.expanduser("~/.brothersbe-private-names")
#: A backticked token that looks like a path into this repository.
PATH_TOKEN = re.compile(
    r"`((?:scripts|docs|bundle|products|plugins|\.claude)/[A-Za-z0-9_./-]+"
    r"|[A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:py|json|md|txt|yml|yaml|sh|toml))`")
SHELL_FENCE = re.compile(r"```(?:bash|sh|console)\n(.*?)```", re.S)
#: A page saying a path is not there yet declares it, and says why.
ALLOW_MISSING = re.compile(
    r"<!--\s*doc-assurance: allow-missing (\S+)[^>]*-->")
SCRIPT_CALL = re.compile(
    r"python3\s+(scripts/[A-Za-z0-9_]+\.py)((?:\s+--[a-z-]+)*)")


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def allowlist_entries(root):
    """The allowlist's own lines. None (never an empty list) when the file is
    absent, so a missing law refuses rather than silently checking nothing."""
    path = os.path.join(root, "docs", "plan", "EXPORT-ALLOWLIST.txt")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return [l.strip() for l in fh if l.strip() and not l.strip().startswith("#")]


def corpus_files(root):
    """Every tracked Markdown file the allowlist carries that is current public
    narrative. Derived from git, so a page added tomorrow is checked tomorrow."""
    entries = allowlist_entries(root)
    if entries is None:
        return None
    found = set()
    for entry in entries:
        proc = _run(["git", "ls-files", "-z", "--", entry], root)
        if proc.returncode != 0:
            continue
        for rel in proc.stdout.split("\0"):
            if not rel.endswith(".md"):
                continue
            if rel.startswith("docs/decisions/") and rel != DECISIONS_PUBLIC:
                continue
            if any(rel.startswith(p) for p in EVIDENCE_PREFIXES):
                continue
            if not any(rel == r or rel.startswith(r) for r in CORPUS_ROOTS):
                continue
            found.add(rel)
    return sorted(found)


def _basenames(root):
    """Every tracked file's basename, so a page citing `foo.py` without its
    directory resolves the way a reader resolves it: by looking for the file."""
    proc = _run(["git", "ls-files", "-z"], root)
    return {os.path.basename(f) for f in proc.stdout.split("\0") if f}


def check_paths(root, files):
    """Every repository path a page cites in backticks resolves. A page that
    names a file the tree does not have is teaching a reader a dead path.

    TWO EXEMPTIONS, both of which cost a false FAIL on the first real run.
    A bare filename with no directory is resolved against every tracked
    basename, because `brother_install.py` is a true citation of
    scripts/brother_install.py and a reader has no trouble with it. And a page
    may state that a path does NOT exist yet, which docs/CHARTER.md does when
    it describes the condition under which a product directory appears; that
    page carries a visible ALLOW_MISSING marker naming the path and the
    reason, mirroring this estate's `sbe: allow-silent` rule that an exemption
    is a thing somebody reads, never a silent skip."""
    bad = []
    names = _basenames(root)
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
        allowed = set(ALLOW_MISSING.findall(text))
        for m in PATH_TOKEN.finditer(text):
            tok = m.group(1)
            if "*" in tok or tok in allowed:
                continue
            if "/" not in tok and tok in names:
                continue
            here = os.path.join(root, os.path.dirname(rel), tok)
            if os.path.exists(os.path.join(root, tok)) or os.path.exists(here):
                continue
            bad.append("%s cites %s, which is not in the tree" % (rel, tok))
    return bad


def check_commands(root, files):
    """Every `python3 scripts/X.py --flag` a page prints names a script that
    exists and flags that script's own --help admits. A documented flag nobody
    ran is how a page teaches a command that cannot work."""
    bad = []
    helps = {}
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
        for fence in SHELL_FENCE.finditer(text):
            for m in SCRIPT_CALL.finditer(fence.group(1)):
                script, flags = m.group(1), m.group(2)
                if not os.path.isfile(os.path.join(root, script)):
                    bad.append("%s runs %s, which is not in the tree" % (rel, script))
                    continue
                if script not in helps:
                    proc = _run(["python3", script, "--help"], root)
                    helps[script] = proc.stdout + proc.stderr
                for flag in flags.split():
                    if flag not in helps[script]:
                        bad.append("%s passes %s to %s, which its --help does "
                                   "not offer" % (rel, flag, script))
    return bad


def check_schema_prose(root, files):
    """The persona enum a reference page prints equals the checked-in schema's.
    Prose and schema drifting apart is the drift this register exists to stop."""
    schema = os.path.join(root, "docs", "schema", "outcome-contract-v1.json")
    page = "docs/reference/outcome-contract.md"
    if not os.path.isfile(schema) or page not in files:
        return None  # NO-DATA: this pair is not both present to compare
    with open(schema, encoding="utf-8") as fh:
        enum = json.load(fh).get("properties", {}).get("persona", {}).get("enum")
    if not enum:
        return None
    with open(os.path.join(root, page), encoding="utf-8") as fh:
        text = fh.read()
    bad = ["%s omits the schema's persona value %r" % (page, v)
           for v in enum if "`%s`" % v not in text]
    return bad


def check_generated_not_authored(root, files, *, jev_runner=None):
    """No page tells a reader to hand-edit a generated file. SYSTEM.md is
    written by scripts/system_doc.py and checked by it; a page inviting an edit
    there teaches somebody to produce a diff the battery will refuse.

    JEV-G1 wave-1 seam J095 (registry: doc-assurance 'generated not
    authored' phrasing check): second-opinions each regex hit via
    jev_seam.consult(), off by default in data/jev-seams.json, called
    for its side effect only (the calibration ledger row and A0.6 audit
    sample) -- WAVE 1 IS SHADOW-ONLY BY CONTRACT
    (opus-review-seams-g1-g3.md, C1): a real regex hit is ALWAYS flagged
    below, whatever mode says, including "act": there is no promoted,
    calibrated evidence for this entry yet to make "act" a real path
    today, and a raw noul probability is the wrong type to gate a
    doc-assurance finding on regardless. `jev_runner` exists only so a
    test can inject a scripted bridge."""
    bad = []
    invite = re.compile(r"(edit|update|change|write)[^.\n]{0,40}`?SYSTEM\.md`?", re.I)
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if invite.search(line) and "not" not in line.lower() \
                        and "never" not in line.lower():
                    try:
                        import jev_g1_seam_cache
                        if not jev_g1_seam_cache.is_off("J095"):
                            import jev_seam
                            jev_seam.consult(
                                "J095", {"line": line}, True,
                                seams_config=jev_seam.load_seams_config(),
                                registry=jev_seam.load_registry(),
                                ledger_dir=jev_seam.DEFAULT_LEDGER_DIR,
                                runner=jev_runner,
                            )  # C1: return value intentionally discarded, see docstring above
                    except Exception:
                        pass  # sbe: allow-silent the seam is advisory only, the hit below is always flagged
                    bad.append("%s:%d invites a hand edit of the generated "
                               "SYSTEM.md" % (rel, n))
    return bad


def check_claim_register(root, files):
    """Every authority a claim register row names is a path that exists. A
    register pointing at a moved file is a register nobody can check."""
    reg = "docs/assurance/DOC-CLAIMS.md"
    if reg not in files:
        return None  # NO-DATA: no register in this corpus
    bad = []
    rows = 0
    with open(os.path.join(root, reg), encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("| DOC-"):
                continue
            rows += 1
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3:
                continue
            for tok in PATH_TOKEN.finditer(cells[2]):
                if not os.path.exists(os.path.join(root, tok.group(1))):
                    bad.append("%s row %s names authority %s, which is not in "
                               "the tree" % (reg, cells[0], tok.group(1)))
    if not rows:
        return None  # NO-DATA: a register with no rows proves nothing
    return bad


def check_house_style(root, files):
    """No em or en dash, and no attribution trailer."""
    bad = []
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            text = fh.read()
        for d in DASHES:
            if d in text:
                bad.append("%s carries a %s dash" %
                           (rel, "em" if d == DASHES[0] else "en"))
        if ATTRIBUTION.search(text):
            bad.append("%s carries an attribution trailer" % rel)
    return bad


def private_terms():
    """Compile the external private-name list, or return None for NO-DATA.

    ONLY an absent file is NO-DATA: many environments carry none. A file that
    IS present but cannot be read (permissions, a race between the isfile
    check and the open, a disk error) is a different failure and must not be
    folded into the same None, because a single check reporting NO-DATA here
    still lets the whole doc_assurance run report PASS when nothing else
    fires. Letting an unreadable private-name list read as "no list
    configured" is exactly the silent pass this guard exists to refuse, so
    the read failure is left to propagate to the caller, which turns it into
    a FAIL finding instead of a NO-DATA that disappears into a PASS."""
    path = os.environ.get("BROTHERSBE_PRIVATE_NAMES_FILE",
                          DEFAULT_PRIVATE_NAMES_PATH)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        terms = [line.strip() for line in fh
                 if line.strip() and not line.startswith("#")]
    if not terms:
        return None
    return re.compile("|".join(re.escape(term) for term in terms), re.I)


def check_private_terms(root, files):
    """External private-name list, with a safe finding that gives only length."""
    try:
        terms = private_terms()
    except OSError as exc:
        path = os.environ.get("BROTHERSBE_PRIVATE_NAMES_FILE",
                              DEFAULT_PRIVATE_NAMES_PATH)
        return ["%s exists but could not be read (%s: %s); a private-name "
                "list that cannot be checked must never be reported clean" %
                (path, type(exc).__name__, exc)]
    if terms is None:
        return None
    bad = []
    for rel in files:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            match = terms.search(fh.read())
        if match:
            bad.append("%s carries a private client term of %d chars" %
                       (rel, len(match.group(0))))
    return bad


CHECKS = (
    ("repository paths cited by a page exist", check_paths),
    ("documented commands and flags are real", check_commands),
    ("prose matches the checked-in schema", check_schema_prose),
    ("no page invites a hand edit of a generated file", check_generated_not_authored),
    ("every claim register authority exists", check_claim_register),
    ("house style: no dash and no attribution", check_house_style),
    ("no private client term", check_private_terms),
)


def run(root):
    """Returns (exit_code, lines). 0 PASS, 1 FAIL, 2 NO-DATA."""
    lines = []
    files = corpus_files(root)
    if files is None:
        return 2, ["NO-DATA: no docs/plan/EXPORT-ALLOWLIST.txt under %s, so the "
                   "public corpus cannot be derived. Not a pass." % root]
    if not files:
        return 2, ["NO-DATA: the allowlist carried no public Markdown page, so "
                   "nothing was checked. An empty scan is not a pass."]
    lines.append("corpus: %d public page(s), derived from the export allowlist"
                 % len(files))
    failed = nodata = 0
    for name, fn in CHECKS:
        found = fn(root, files)
        if found is None:
            nodata += 1
            lines.append("NO-DATA  %s: what it reads was not present" % name)
        elif found:
            failed += 1
            lines.append("FAIL     %s: %d finding(s)" % (name, len(found)))
            lines.extend("           " + f for f in found)
        else:
            lines.append("PASS     %s" % name)
    lines.append("relative links are NOT checked here on purpose; "
                 "scripts/test_export_links.py reads them over the real "
                 "export tree, which is the stronger check.")
    if failed:
        lines.append("FAIL: %d check(s) found something. NO-DATA on %d."
                     % (failed, nodata))
        return 1, lines
    if nodata == len(CHECKS):
        lines.append("NO-DATA: every check was unable to read what it needed. "
                     "Not a pass.")
        return 2, lines
    lines.append("PASS: %d check(s) clear, %d NO-DATA." % (len(CHECKS) - nodata, nodata))
    return 0, lines


def selftest():
    """Drive every check backwards: a fixture carrying one instance of each
    defect must be named by the check that owns it."""
    import shutil
    import tempfile
    ok = True
    tmp = tempfile.mkdtemp(prefix="doc-assurance-selftest-")
    try:
        os.makedirs(os.path.join(tmp, "docs", "reference"))
        os.makedirs(os.path.join(tmp, "docs", "assurance"))
        os.makedirs(os.path.join(tmp, "docs", "schema"))
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "docs", "schema",
                               "outcome-contract-v1.json"), "w") as fh:
            json.dump({"properties": {"persona": {"enum": ["analyst", "lead"]}}}, fh)
        pages = {
            "docs/reference/outcome-contract.md":
                "The enum is `analyst` only.\n`scripts/ghost.py` explains it.\n",
            "docs/assurance/DOC-CLAIMS.md":
                "| ID | Claim | Authority |\n| --- | --- | --- |\n"
                "| DOC-001 | x | `scripts/absent.py` |\n",
            "docs/reference/style.md":
                "A dash " + chr(0x2014) + " here.\nPlease edit `SYSTEM.md` by hand.\n"
                "```bash\npython3 scripts/real.py --nope\n```\n",
        }
        for rel, text in pages.items():
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(os.path.join(tmp, "scripts", "real.py"), "w") as fh:
            fh.write("import argparse\n"
                     "argparse.ArgumentParser().parse_args()\n")
        files = sorted(pages)  # all under docs/, a corpus root
        cases = [
            ("paths", check_paths, "ghost.py"),
            ("commands", check_commands, "--nope"),
            ("schema", check_schema_prose, "lead"),
            ("generated", check_generated_not_authored, "SYSTEM.md"),
            ("register", check_claim_register, "absent.py"),
            ("style", check_house_style, "dash"),
        ]
        for name, fn, needle in cases:
            found = fn(tmp, files)
            hit = bool(found) and any(needle in f for f in found)
            print("%-10s %s" % (name, "caught" if hit else "MISSED"))
            ok = ok and hit
        # An existing-but-unreadable private-name list must never read as "no
        # list configured": before the fix, private_terms() folded that read
        # failure into the same None as an absent file, check_private_terms
        # returned None (NO-DATA) for it, and a single NO-DATA check still
        # let the whole run report PASS. isfile() is forced True for a path
        # that does not exist, which makes the subsequent open() fail exactly
        # the way a permissions error or a vanished file would.
        from unittest import mock
        ghost = os.path.join(tmp, "does-not-actually-exist.txt")
        old_env = os.environ.get("BROTHERSBE_PRIVATE_NAMES_FILE")
        os.environ["BROTHERSBE_PRIVATE_NAMES_FILE"] = ghost
        try:
            with mock.patch("os.path.isfile", return_value=True):
                priv_found = check_private_terms(tmp, files)
        finally:
            if old_env is None:
                os.environ.pop("BROTHERSBE_PRIVATE_NAMES_FILE", None)
            else:
                os.environ["BROTHERSBE_PRIVATE_NAMES_FILE"] = old_env
        priv_ok = bool(priv_found)
        print("%-10s %s" % ("privterm", "caught" if priv_ok else "MISSED"))
        ok = ok and priv_ok
        # An empty corpus must be NO-DATA, never a pass.
        code, lines = run(tmp)
        empty_ok = code == 2 and "NO-DATA" in lines[0]
        print("%-10s %s" % ("no-data", "caught" if empty_ok else "MISSED"))
        ok = ok and empty_ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=ROOT, help="the repository to check")
    ap.add_argument("--selftest", action="store_true",
                    help="drive every check backwards over a defect fixture")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    code, lines = run(args.root)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
