"""release_note_from_tree.py: the release note's checkable claims, measured, never typed.

THE DEFECT this closes, found by an external critic reading docs/releases/1.0.0.md:
every number in a hand typed release note is a claim about a tree that keeps moving.
The 1.0.0 note said "18 tests passing" for test_brother_run.py after the suite had
grown to 44; it said "Stamped by the exporter at release time" with no revision because
nothing had stamped it; it cited test_author_approves_refused_under_the_policy as the
proof of separation of duties although that test passes SEPARATION_OF_DUTIES_ENFORCED
explicitly and therefore still passes when the flag is flipped off, so it proves
nothing about the default; and it named zero files backing any of its claims. This is
the estate's recurring failure "the delivery record is a template sentence".

SO THE NUMBERS ARE MEASURED HERE, AT PRINT TIME, by actually running each cited suite
and reading unittest's own "Ran N tests" line, never a copy pasted count. A suite that
cannot be run, or a cited test name this script cannot find in the suite's own source,
REFUSES THE WHOLE NOTE (exit 2, NO-DATA) rather than printing five correct paragraphs
and one invented one. The recall hook's live proof line is OMITTED, not guessed: it
depends on a consent flag and a vault both outside this tree (checked below), so no
command in the tree reproduces it deterministically on a machine that has not set both
up by hand.

A SECOND EXTERNAL READ (zero context critic, 2026-09-02) found seven more ways this
generator's own claims were unmeasured, closed here one by one:

  GAP 1, a skip reads as a pass. Every suite is now run with -v so a per test skip
  reason is visible; the parsed count separates tests that RAN from tests unittest
  merely COUNTED, prints "N of M ran, OK" whenever the two differ, and refuses the
  whole note if any skip reason itself contains NO-DATA, because a NO-DATA skip is a
  refusal wearing a green summary line, not a pass.

  GAP 2, the zero-test guard used to read `n is not None`, which a suite reporting
  "Ran 0 tests" satisfies (0 is not None) on interpreters where that still exits 0.
  A parsed count of exactly 0 is now refused explicitly, on every interpreter.

  GAP 3, "the fifteen legacy command shims" was typed, not counted. It is now read
  from products/brothermode/commands/ at print time (shims_count()) and the number in
  the note is the measured one, or the sentence is dropped if the directory cannot be
  read.

  GAP 4, the receipt-sentence paragraph cited both scripts/test_receipt_door.py and
  scripts/test_brother_run.py beside every claim in it, but only test_receipt_door.py
  actually asserts the scoping sentence, the acceptance screen line and the release
  screen line (grepped: RD.SCOPING_SENTENCE, "acceptance screen", "release screen" all
  live only in test_receipt_door.py). test_brother_run.py's own contribution is the
  one-governor-line-per-wait claim (TheGovernorLineDuringAWait), a different sentence
  entirely. The note now cites each suite next to the claim its own assertions back.

  GAP 5, "Stamped by the exporter at release time" bound the previous release to no
  hash, because nothing ever stamps a note until scripts/cut_v1.0.0.sh runs. The note
  now reads the most recent earlier docs/releases/<version>.md and prints its real
  stamp when scripts/export_public.py's own stamp_source_revision() already wrote one,
  or says in plain words that the previous note still carries the placeholder and the
  change set since it cannot be enumerated from the record. Never invents a hash.

  GAP 6, scripts/test_release_note_from_tree.py (new) drives the parsing and citation
  functions directly against canned fixtures, so this file's own claims about itself
  are proven the same way it insists every other claim be proven.

  GAP 7, the scoping sentence was TYPED into this file as a paraphrase ("not that
  everything is proven") that does not match scripts/receipt_door.py's own
  SCOPING_SENTENCE constant ("It does not mean everything is proven: only the checks
  named above ran..."). The note now imports and prints SCOPING_SENTENCE verbatim,
  never a retyped copy, so the two can never drift again.

Python 3, standard library only. No network. Run from anywhere; ROOT is derived from
this file's own location, the same pattern scripts/system_doc.py uses.
"""
import argparse
import ast
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
NODATA = "NO-DATA"
RELEASES_DIR = os.path.join(ROOT, "docs", "releases")
MARKETPLACE_JSON = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
#: The note's own launch version, and the fallback default when
#: marketplace.json cannot be read: this file's original single-version
#: behaviour, kept exactly as it was before --version existed.
FALLBACK_VERSION = "1.0.0"
SHIMS_DIR = os.path.join(ROOT, "products", "brothermode", "commands")
CUT_SCRIPT = os.path.join(ROOT, "scripts", "cut_v1.0.0.sh")

#: (prose label, suite path relative to ROOT). Order matches the note's own
#: paragraph order. Grepped from docs/releases/1.0.0.md itself, never invented.
SUITES = [
    ("receipt door", "scripts/test_receipt_door.py"),
    ("brother_run", "scripts/test_brother_run.py"),
    ("recall hook", "products/brothermode/tools/test_vault_recall_hook.py"),
    ("vault lifecycle", "products/brothermode/tools/test_bm_vault_lifecycle.py"),
    ("vault promotions", "products/brothermode/tools/test_bm_vault_promotions.py"),
    ("vault pane", "products/brothermode/tools/test_bm_vault_pane.py"),
]

VAULT_LIFECYCLE = "products/brothermode/tools/test_bm_vault_lifecycle.py"

#: The two tests that actually go red when SEPARATION_OF_DUTIES_ENFORCED is
#: flipped to False, found by reading the flag's every caller rather than
#: trusting the note's original citation (test_author_approves_refused_under_
#: the_policy passes enforce=True explicitly, so it stays green either way and
#: proves nothing about the default).
SEPARATION_TESTS = [
    "test_the_default_policy_is_on_the_same_actor_is_refused",
    "test_the_same_actor_with_no_enforce_argument_is_refused_by_default",
]

_RAN_RE = re.compile(r"^Ran (\d+) tests?", re.M)
_SKIPPED_RE = re.compile(r"skipped=(\d+)")
_SKIP_REASON_RE = re.compile(r"\.\.\. skipped '([^']*)'", re.M)


def parse_unittest_output(out):
    """Pure parse of one `python3 <suite> -v` run's combined stdout+stderr into
    the facts a claim needs. Never touches a subprocess or a file: every case
    in scripts/test_release_note_from_tree.py feeds this canned text.

    total: the count from unittest's own "Ran N tests" line, None (never 0,
    never guessed) when that line is missing.
    skipped: the count named in a "OK (skipped=N)" or "... (skipped=N, ...)"
    line, 0 when no such marker appears.
    reasons: every per test skip reason unittest -v printed (empty without
    -v, since unittest only prints the reason next to the test in verbose
    mode), so a NO-DATA skip can be caught by its own content.
    said_ok: whether unittest's own summary line began with "OK" (a suite
    that failed prints "FAILED (...)" instead)."""
    m = _RAN_RE.search(out)
    total = int(m.group(1)) if m else None
    sm = _SKIPPED_RE.search(out)
    skipped = int(sm.group(1)) if sm else 0
    reasons = _SKIP_REASON_RE.findall(out)
    said_ok = bool(re.search(r"^OK\b", out, re.M))
    return {"total": total, "skipped": skipped, "reasons": reasons, "said_ok": said_ok}


def format_suite_count(ran, total):
    """"N OK" when every counted test ran, "N of M ran, OK" the moment a skip
    makes the two differ, so a skip can never silently read as a full pass."""
    if ran == total:
        return "%d OK" % total
    return "%d of %d ran, OK" % (ran, total)


def evaluate_suite_output(returncode, out):
    """Pure: turn one suite run's exit code and combined stdout+stderr into
    the verdict a claim needs. Same shape run_suite returns, minus the file
    existence check (that needs the filesystem, this does not), so every
    branch here is driven from canned text in
    scripts/test_release_note_from_tree.py rather than a real subprocess.

    ok is False when: the process exited nonzero, unittest's own "Ran N
    tests" line is missing, N is exactly 0 (refused explicitly, on every
    interpreter, rather than trusting a zero count to also fail the exit
    code check), unittest's own summary did not start with "OK", or any
    skip reason contains NO-DATA (a NO-DATA skip is a refusal wearing a
    green summary line, never a pass)."""
    parsed = parse_unittest_output(out)
    total = parsed["total"]
    skipped = parsed["skipped"]
    nodata_skip = next((r for r in parsed["reasons"] if NODATA in r), None)
    ok = (returncode == 0 and total not in (None, 0)
          and parsed["said_ok"] and nodata_skip is None)
    ran = (total - skipped) if total is not None else None
    lines = [l for l in out.strip().splitlines() if l.strip()]
    tail = lines[-1] if lines else "no output"
    return {"ok": bool(ok), "n": total, "ran": ran, "skipped": skipped,
             "nodata_skip": nodata_skip, "tail": tail}


def run_suite(rel_path):
    """Run one suite with the real interpreter, verbose, cwd at ROOT, and
    hand its exit code and output to evaluate_suite_output(). Returns
    {"ok": False, "n": None, ...} without spawning anything when the suite
    file is not even on disk."""
    path = os.path.join(ROOT, rel_path)
    if not os.path.isfile(path):
        return {"ok": False, "n": None, "ran": None, "skipped": 0,
                 "nodata_skip": None, "tail": "%s does not exist" % rel_path}
    proc = subprocess.run([sys.executable, path, "-v"], cwd=ROOT,
                           capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return evaluate_suite_output(proc.returncode, out)


def confirm_test_names(rel_path, names):
    """Which of `names` are really defined (def <name>() ) in the suite's own
    source. Never assumes a cited test name exists; a name not found here is
    a reason to refuse, not to print it anyway."""
    path = os.path.join(ROOT, rel_path)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    return [n for n in names if re.search(r"def %s\(" % re.escape(n), text)]


def imported_module_names(text):
    """Every top-level module name a suite really imports, from its own
    parsed syntax tree: `import x`, `import x as y`, `import a, b`, and
    `from x import y` (the module half). Dotted names contribute their
    first segment, and a relative `from . import x` contributes nothing,
    because neither names a sibling file this repository ships. Returns a
    sorted list, or [] when the text does not parse.

    AST, never a regular expression, because that is the whole defect this
    replaced: the old reader matched `^import X as Y` (missing the plain
    `import claim_store` and `import decide` that scripts/test_brother_run.
    py really does) and then ALSO swept in every `"<name>.py"` string
    literal anywhere in the file, which is not an import at all. On
    scripts/test_brother_run.py that literal sweep put `scripts/loom.py` in
    a table whose own sentence says the files were read from the suite's
    imports, and the 2026-09-04 delivery-proof skeptic showed the named
    suite stays green with loom.py's behaviour disabled. A table that says
    "imports" must be imports."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import names no sibling file
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return sorted(names)


def subject_files(rel_path):
    """The source file(s) a suite actually exercises, read from the modules
    it really imports (imported_module_names) and kept only when a file of
    that name exists next to the suite. Nothing here is asserted from a
    name: a module imported from the standard library or from anywhere
    other than the suite's own directory contributes no row, and the suite
    itself is never listed as its own subject."""
    path = os.path.join(ROOT, rel_path)
    dirn = os.path.dirname(path)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    base = os.path.basename(rel_path)
    found = []
    for name in imported_module_names(text):
        candidate = name + ".py"
        if candidate == base:
            continue
        full = os.path.join(dirn, candidate)
        if os.path.isfile(full):
            found.append(os.path.relpath(full, ROOT).replace(os.sep, "/"))
    return sorted(found)


def measured_file_rows(claim_suites, perturb=None):
    """([(claim, source_file, suite_or_None)], problem_or_None), one row per
    file, with the suite column MEASURED rather than inferred.

    GAP 8, the defect row E95 closes. The previous version of this table put
    the claim's own suite beside every module that suite imported, under the
    sentence "Named by reading each suite's own imports". An import is not
    coverage: an external critic reading a pristine clone at v1.0.1 found four
    of the eleven files survived a perturbation under the suite named beside
    them, because test_vault_recall_hook.py writes its own fake bm_vault.py
    and shells out to that, and test_brother_run.py does not import loom at
    all. So imports are now only the CANDIDATE list, and every candidate is
    confirmed by actually breaking the file and requiring the suite to go red
    (release_note_perturb.covers, which perturbs behaviour rather than import,
    and restores the file byte-identically).

    A candidate its own claim's suite does not catch falls back to the one
    convention-named sibling, test_<stem>.py beside it: that is how
    scripts/loom.py finds scripts/test_loom.py, the suite the old note never
    named. A file no suite catches reads NO-DATA in the suite column, which
    the perturbation check then reports as NO-DATA and never as a pass.

    `claim_suites` is [(claim label, suite path)]. Slow on purpose: this runs
    one full suite per candidate, at release cut time, so that the table is a
    measurement instead of a paraphrase of the import graph. `perturb` is the
    seam the self test injects through (resolved here, never as a default
    argument, so nothing binds at import time); left None it is the real
    scripts/release_note_perturb.py."""
    if perturb is None:
        sys.path.insert(0, HERE)
        import release_note_perturb as perturb  # noqa: E402
    PERTURB = perturb

    baselines = {}

    def caught_by(suite, rel):
        if suite not in baselines:
            baselines[suite] = PERTURB.run_suite(suite)[0]
        if baselines[suite] != 0:
            # A suite that is red, or that could not be run at all, can prove
            # nothing about any file. Returning here rather than calling
            # covers() with a None baseline also stops it re-running that
            # same unrunnable suite once per candidate.
            return False
        return PERTURB.covers(suite, rel, baseline=baselines[suite])[0] is True

    rows = []
    try:
        for label, suite in claim_suites:
            for rel in subject_files(suite):
                if caught_by(suite, rel):
                    rows.append((label, rel, suite))
                    continue
                sibling = PERTURB.sibling_suite(rel)
                if sibling and sibling != suite and caught_by(sibling, rel):
                    rows.append((label, rel, sibling))
                    continue
                rows.append((label, rel, None))
        # The last file measured has no next step to be caught by.
        PERTURB.check_ledger()
    except PERTURB.RestoreFailed as exc:
        # A file this run could not put back exactly as it found it is a
        # refusal of the whole note, never a table row: the tree is dirty and
        # the reader has to be told that before anything else.
        return [], ("%s: the files table was abandoned mid-measurement and a "
                    "file may still be perturbed: %s" % (NODATA, exc))
    if not rows:
        return [], ("%s: no source file could be measured behind any cited "
                    "suite" % NODATA)
    return rows, None


FILES_TABLE_HEADING = "## Files behind these claims"


def render_files_table(version, file_rows):
    """The whole "Files behind these claims" section as a list of lines, from
    its heading to its trailing blank. One function so build() and
    --write-table can never render two different tables."""
    lines = [FILES_TABLE_HEADING, ""]
    lines.append(
        "One line per file, and the suite beside it is MEASURED, not "
        "inferred from an import. Each file was broken in place (every "
        "function it defines replaced by one that raises when called), the "
        "suite was run, and the suite had to go red; then the file was "
        "restored and re-hashed byte for byte. Where a claim's own suite did "
        "not catch its file, the column names the suite that did. A row "
        "reading %s is a file no suite here catches, which this script would "
        "rather print than name a check that cannot fail. This is call level, "
        "not branch level: a row means breaking the file's functions is "
        "noticed, never that every branch of it is covered. Re-run the whole "
        "table with `python3 scripts/release_note_perturb.py --version %s`."
        % (NODATA, version))
    lines.append("")
    lines.append("| Claim | Source file | Goes red when this file breaks |")
    lines.append("|---|---|---|")
    for label, rel, suite in file_rows:
        lines.append("| %s | `%s` | %s |"
                     % (label, rel, ("`%s`" % suite) if suite else NODATA))
    lines.append("")
    return lines


def replace_files_table(text, table_lines):
    """`text` with its files table swapped for `table_lines`, or None when the
    note carries no such section. Everything else in the note is left byte for
    byte alone: the digest, the revision and the counts in an already cut note
    describe a tree that has moved, and re-deriving them here would replace a
    true claim about the tag with a false one about today."""
    lines = text.split("\n")
    try:
        start = lines.index(FILES_TABLE_HEADING)
    except ValueError:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[:start] + table_lines + lines[end:])


def recall_repro_command():
    """The exact command that reproduces the recall hook's live "Recalled N
    lesson(s)..." line, if one exists that needs nothing outside this tree.
    None: cmd_check() in products/brothermode/tools/vault_recall_hook.py
    gates on scripts/setup.py's own is_consented() (a file under ~/.claude)
    and, once past that, shells out to bm_vault.py which reads the Vault (a
    directory outside this repository, mutable, not pinned to any commit).
    Both are machine state this script cannot promise a reader has, so no
    command here can reproduce the exact wording deterministically; the note
    omits the live-proof clause rather than print an unreproducible one."""
    return None


def head_rev():
    proc = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                           capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def head_describe():
    proc = subprocess.run(["git", "-C", ROOT, "describe", "--tags", "--always"],
                           capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def manifest_version():
    """The version this tree's own manifest currently declares, read with
    the stdlib json module, never copied from a note. None on any read
    failure; the caller treats that as NO-DATA, not as a guessed default."""
    path = os.path.join(ROOT, "bundle", ".claude-plugin", "plugin.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def default_version():
    """The version a note describes when --version is not given: read from
    .claude-plugin/marketplace.json's own metadata.version, never typed.
    Falls back to FALLBACK_VERSION only when that file is unreadable or
    does not declare one, so a stale or unusual checkout still gets a sane
    default instead of a crash; --version always overrides this."""
    try:
        with open(MARKETPLACE_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
        v = (data.get("metadata") or {}).get("version")
        return v or FALLBACK_VERSION
    except (OSError, ValueError, AttributeError):
        return FALLBACK_VERSION


def notes_path_for(version):
    """docs/releases/<version>.md, never a fixed filename: the file
    --write actually writes and --version actually names."""
    return os.path.join(RELEASES_DIR, "%s.md" % version)


def manifest_write_path_for(version):
    """Where --write puts the export manifest ON DISK. Derived from
    RELEASES_DIR, exactly like notes_path_for, so a test that redirects
    RELEASES_DIR redirects BOTH files. Building it from ROOT instead put a
    manifest for an already-cut version into the real docs/releases/ during
    the test suite, which is the very accident the --write test's own
    comment says must never happen. The in-EXPORT relative path stays
    reproduce_export.manifest_path_for(); this is only the local
    destination."""
    return os.path.join(RELEASES_DIR, "%s.export-manifest.txt" % version)


def notes_extra_path_for(version, releases_dir=None):
    """docs/releases/<version>.notes.txt: the one hand written paragraph slot
    this generator reads instead of anyone editing the generated note. The
    note itself stays generator output; a release fact that no command in the
    tree can measure (what a founder-gated row still owes) lives in this file
    and is read from it, never typed into the note by hand."""
    d = releases_dir if releases_dir is not None else RELEASES_DIR
    return os.path.join(d, "%s.notes.txt" % version)


def extra_notes(version, releases_dir=None):
    """The text of that file, stripped: "" when no such file exists (the
    common case, and not a problem), None when the file IS there and cannot
    be read, which build() turns into a NO-DATA refusal rather than printing
    a note that silently dropped a paragraph the cut meant to carry."""
    path = notes_extra_path_for(version, releases_dir)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def cut_script_tag_and_remote(path=None):
    """The (tag, remote) pair scripts/cut_v1.0.0.sh names its own public cut
    as, read from that script's own `TAG=` and `PUBLIC_REMOTE=` shell
    variable lines with a plain regex, never retyped. None when the script
    is unreadable or does not declare both in that exact greppable shape;
    the caller refuses (NO-DATA) rather than invent a tag or a remote."""
    p = path if path is not None else CUT_SCRIPT
    try:
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:  # sbe: allow-silent unreadable script reads as NO-DATA at build's published check, see line 420 below
        return None
    tag_m = re.search(r"^TAG=(\S+)$", text, re.M)
    remote_m = re.search(r"^PUBLIC_REMOTE=(\S+)$", text, re.M)
    if not tag_m or not remote_m:
        return None
    return tag_m.group(1).strip("\"'"), remote_m.group(1).strip("\"'")


def published_as_line(path=None, version=None):
    """The one extra "Published as tag ..." sentence FINDING 1 (zero context
    auditor, 2026-09-02) asked for: the hub commit stamped above is private
    (the hub is not public), so a stranger reading the note on the public
    repository needs the public TAG NAME as the resolvable handle, not the
    hash. Reads the remote (and, when `version` is not given, the tag too)
    from scripts/cut_v1.0.0.sh itself (see cut_script_tag_and_remote); None
    when that script does not declare them, so build() can refuse rather
    than print an invented tag.

    `version`, when given, OVERRIDES the tag with `v<version>` rather than
    trusting the file's own TAG= line: that line is now `TAG=v$VERSION`
    (scripts/cut_v1.0.0.sh takes the version as its own first argument), so
    its raw source text is no longer a resolved tag for any run other than
    the one the shell actually performs. Every caller inside this file that
    already knows the target version (build()) passes it; a caller that
    does not (existing tests against a literal fixture script) keeps the
    old file-read behaviour unchanged."""
    tag_remote = cut_script_tag_and_remote(path)
    if tag_remote is None:
        return None
    tag, remote = tag_remote
    if version is not None:
        tag = "v%s" % version
    host = remote.split("://", 1)[-1].rstrip("/")
    return ("Published as tag %s on %s; that tag is the public hash of "
            "this cut, the hub commit above is for the private record."
            % (tag, host))


#: Memo for export_manifest(). Building the real export tree costs about
#: twenty seconds (1129 files, plus each product's own checksums.sh), and
#: build() is called more than once per process by its own test suite. The
#: manifest is a pure function of the tree, so computing it once is not a
#: staleness risk inside one run.
_MANIFEST_MEMO = {}


def export_manifest():
    """(text, digest, count, problem) for what this tree would export.

    E80: the note's old headline claim was a HUB COMMIT, which a public clone
    cannot resolve at all, so the reproduction could not start from the tag.
    This is the claim a clone CAN check: a manifest of every exported file's
    sha256, its own digest, and the count. Built by running the exporter's
    OWN build_export_tree, never a second implementation of the same file
    selection, so the manifest describes exactly the bytes export_public.py
    ships. `problem` is a named NO-DATA string on any failure and the other
    three are None; build() refuses the whole note on it rather than printing
    a note whose central claim is missing."""
    if "v" in _MANIFEST_MEMO:
        return _MANIFEST_MEMO["v"]
    sys.path.insert(0, HERE)
    import export_public as EP  # noqa: E402
    import reproduce_export as RE  # noqa: E402

    allowlist = EP.load_allowlist()
    if allowlist is None:
        result = (None, None, None,
                  "%s: no export allowlist at %s, so the exported file set "
                  "cannot be listed" % (NODATA, EP.DEFAULT_ALLOWLIST))
    else:
        dest = tempfile.mkdtemp(prefix="release-note-export-")
        try:
            # build_export_tree narrates its manifest regeneration on stdout;
            # this function's caller may be printing the note to stdout, so
            # that narration goes to stderr where it cannot land in a note.
            with contextlib.redirect_stdout(sys.stderr):
                copied = EP.build_export_tree(dest, allowlist)
            if not copied:
                result = (None, None, None,
                          "%s: the allowlist copied nothing from %s"
                          % (NODATA, ROOT))
            else:
                text = RE.manifest_from_dir(dest)
                result = (text, RE.manifest_digest(text),
                          len(text.splitlines()), None)
        except OSError as exc:
            result = (None, None, None,
                      "%s: the export tree could not be read: %s"
                      % (NODATA, exc))
        finally:
            shutil.rmtree(dest, ignore_errors=True)
    _MANIFEST_MEMO["v"] = result
    return result


def shims_count(commands_dir=None):
    """How many products/brothermode/commands/brotherme-*.md files this tree
    actually ships, counted at print time rather than typed. None (never 0
    standing in for "could not read") when the directory cannot be listed."""
    d = commands_dir if commands_dir is not None else SHIMS_DIR
    try:
        names = os.listdir(d)
    except OSError:  # sbe: allow-silent unreadable shims dir reads as NO-DATA at build(), see line 416 below
        return None
    return len([n for n in names if re.match(r"brotherme-.*\.md$", n)])


def _version_tuple(name):
    """"0.9.11" -> (0, 9, 11). None for anything that is not dot separated
    integers, so a stray file in docs/releases/ is skipped rather than
    crashing the sort."""
    try:
        return tuple(int(p) for p in name.split("."))
    except ValueError:  # sbe: allow-silent a non-version filename is skipped by the caller's sort filter, see line 338 below
        return None


def previous_release_note_path(target_version="1.0.0", releases_dir=None):
    """The most recent docs/releases/<version>.md whose own filename version
    sorts below target_version, found by listing the directory and comparing
    parsed version tuples, never assumed to be a fixed name. None when the
    directory is unreadable or holds no earlier release."""
    d = releases_dir if releases_dir is not None else RELEASES_DIR
    target = _version_tuple(target_version)
    try:
        names = os.listdir(d)
    except OSError:  # sbe: allow-silent unreadable releases dir reads as the plain no-earlier-release sentence, see line 356 below
        return None
    best = None
    for name in names:
        if not name.endswith(".md"):
            continue
        v = _version_tuple(name[:-3])
        if v is None or (target is not None and v >= target):
            continue
        if best is None or v > best[0]:
            best = (v, os.path.join(d, name))
    return best[1] if best else None


def previous_release_line(target_version="1.0.0", releases_dir=None):
    """The "Previous release" sentence: the real hub commit the prior release
    note already had stamped into it, read verbatim, or a plain NO-DATA
    sentence when that note still carries the placeholder or none exists.
    Imports scripts/export_public.py's own SOURCE_REVISION_HEADER and
    SOURCE_REVISION_PLACEHOLDER rather than retyping them, so this line can
    never drift from what stamp_source_revision() actually writes."""
    sys.path.insert(0, HERE)
    import export_public as EXP  # noqa: E402

    path = previous_release_note_path(target_version, releases_dir)
    if path is None:
        return ("No earlier release note exists in `docs/releases/` to read "
                "a previous revision from.")
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return "`%s` could not be read; the previous revision is %s." % (rel, NODATA)
    if (EXP.SOURCE_REVISION_HEADER not in text
            or EXP.SOURCE_REVISION_PLACEHOLDER in text):
        return ("`%s` carries no revision stamp (still the placeholder "
                "\"%s\"), so the change set since it cannot be enumerated "
                "from the record." % (rel, EXP.SOURCE_REVISION_PLACEHOLDER))
    m = re.search(r"Cut from hub commit `([0-9a-f]+)`", text)
    if not m:
        # A zero context critic read this branch's old sentence in the
        # shipped 1.0.0 note ("carries a Source revision section in a
        # shape this script does not recognize, so no revision is printed
        # here") and correctly called it a cut script talking to itself: a
        # release note is for a reader of THIS release, not a diagnostic
        # about this generator's own parser. The diagnostic goes to
        # stderr; the note gets nothing where the sentence used to stand.
        print("previous_release_line: %s carries a Source revision "
              "section this generator does not recognize; omitting the "
              "previous-release sentence rather than describing the "
              "generator's own limitation inside the note" % rel,
              file=sys.stderr)
        return ""
    return "Previous release `%s` was cut from hub commit `%s`." % (rel, m.group(1))


def build(version=None):
    """Runs every cited suite and confirms every cited test name, for the
    release note named `version` (default: default_version(), the tree's
    own marketplace.json metadata.version, else FALLBACK_VERSION). Returns
    (body, lines) on success, or (None, lines) with the NO-DATA reason(s) in
    `lines` when anything cited could not be verified. `body` is the full
    note text; nothing in it is printed until every claim it makes has been
    measured."""
    if version is None:
        version = default_version()
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import receipt_door as RD  # noqa: E402
    import reproduce_export as RE_MOD  # noqa: E402

    manifest_rel = RE_MOD.manifest_path_for(version)

    results = {}
    problems = []
    for label, rel in SUITES:
        r = run_suite(rel)
        results[rel] = r
        if not r["ok"]:
            if r["nodata_skip"]:
                problems.append("%s: %s skipped a test for a NO-DATA reason: %s"
                                 % (NODATA, rel, r["nodata_skip"]))
            elif r["n"] == 0:
                problems.append("%s: %s reported 0 tests" % (NODATA, rel))
            else:
                problems.append("%s: %s %s" % (NODATA, rel, r["tail"]))

    found = confirm_test_names(VAULT_LIFECYCLE, SEPARATION_TESTS)
    missing = [n for n in SEPARATION_TESTS if n not in found]
    if missing:
        problems.append("%s: %s does not define %s"
                         % (NODATA, VAULT_LIFECYCLE, ", ".join(missing)))

    rev = head_rev()
    if rev is None:
        problems.append("%s: git rev-parse HEAD failed" % NODATA)
    describe = head_describe()
    manifest_ver = manifest_version()
    if manifest_ver is None:
        problems.append("%s: bundle/.claude-plugin/plugin.json version unreadable" % NODATA)

    shims = shims_count()
    if shims is None:
        problems.append("%s: %s unreadable" % (NODATA, SHIMS_DIR))

    manifest_text_, manifest_hex, manifest_count, manifest_problem = \
        export_manifest()
    if manifest_problem:
        problems.append(manifest_problem)

    extra = extra_notes(version)
    if extra is None:
        problems.append("%s: %s exists but could not be read" %
                         (NODATA, notes_extra_path_for(version)))

    published = published_as_line(version=version)
    if published is None:
        problems.append("%s: %s does not declare TAG= and PUBLIC_REMOTE= "
                         "in a greppable shape" % (NODATA, CUT_SCRIPT))

    if problems:
        return None, problems

    # Measured LAST, and only once every cheaper claim has held: this is the
    # expensive step (one full suite run per candidate file), so a note that
    # was going to be refused anyway is refused before it is paid for.
    file_rows, table_problem = measured_file_rows(SUITES)
    if table_problem:
        return None, [table_problem]

    receipt = results["scripts/test_receipt_door.py"]
    brother_run = results["scripts/test_brother_run.py"]
    recall = results["products/brothermode/tools/test_vault_recall_hook.py"]
    lifecycle = results[VAULT_LIFECYCLE]
    promotions = results["products/brothermode/tools/test_bm_vault_promotions.py"]
    pane = results["products/brothermode/tools/test_bm_vault_pane.py"]

    L = []
    A = L.append
    A("# Brother %s" % version)
    A("")
    A("## Source revision")
    A("")
    A("Cut from hub commit `%s` (hub, private" % rev
      + ("; `git describe --tags --always`: `%s`)." % describe
         if describe else ")."))
    A("A clone of this repository cannot resolve that revision: it names a "
      "commit in the private hub, and `git cat-file` on it exits 128 here. "
      "It is the private audit trail. Nothing a reader has to check depends "
      "on it: the tag and the manifest digest below are the checkable claims.")
    if manifest_ver == version:
        A("The manifests at this commit read `%s`: this is the cut, "
          "regenerated by `scripts/cut_v1.0.0.sh` from the tree itself."
          % version)
    else:
        A("The manifests at this commit read `%s`; this note is regenerated by "
          "`scripts/cut_v1.0.0.sh` once the founder bumps them to %s, and "
          "will read `%s` here when it does." % (manifest_ver, version, version))
    A("")
    A(published)
    A("")
    A("Export manifest digest `%s` over %d exported file(s), named one per "
      "line with its own sha256 in `%s`. That manifest ships inside this tag, "
      "and so does every file it names, so the whole claim is checkable from "
      "a clone with no access to the hub. Files under `%s` are outside the "
      "digest: the note that carries a digest cannot also be hashed by it."
      % (manifest_hex, manifest_count, manifest_rel,
         RE_MOD.MANIFEST_EXCLUDED_PREFIX))
    A("")
    A("Reproduce it from a fresh clone of this repository, at the tag, with "
      "nothing private and no network:")
    A("")
    A("    git checkout v%s" % version)
    A("    python3 scripts/reproduce_export.py --verify-tree --tag v%s"
      % version)
    A("")
    A("That prints PASS when every file the manifest names hashes to the "
      "value it names, FAIL naming each file that does not, and NO-DATA when "
      "the manifest or this digest is not there to read.")
    A("")
    prev_line = previous_release_line(version)
    if prev_line:  # "" when the previous note's shape is unrecognized: the
        A(prev_line)  # note gets nothing there, never a diagnostic about
        A("")         # this generator's own parser (the reason is on stderr)
    A("## What this release carries")
    A("")
    if extra:
        A(extra)
        A("")
    A("Every delivery report ends with one receipt sentence per unit, the "
      "acceptance screen line and, where a risk class was named, the "
      "release screen line, and the scoping sentence `%s` Proven by "
      "`python3 scripts/test_receipt_door.py` at %s." %
      (RD.SCOPING_SENTENCE, format_suite_count(receipt["ran"], receipt["n"])))
    A("")
    A("Each wait during a run prints one governor line when it starts and "
      "one when it ends, never once per poll. Proven by "
      "`python3 scripts/test_brother_run.py` at %s." %
      format_suite_count(brother_run["ran"], brother_run["n"]))
    A("")
    A("The recall hook prints `Recalled N lesson(s) from the Vault for <file>` "
      "before an edit of a file the Vault knows. It prints nothing on a "
      "genuine no-match, and writes nothing before consent. Proven by "
      "`python3 products/brothermode/tools/test_vault_recall_hook.py` at %s." %
      format_suite_count(recall["ran"], recall["n"]))
    A("")
    A("Vault promotion enforces separation of duties. "
      "`SEPARATION_OF_DUTIES_ENFORCED` is `True`, and an author cannot "
      "approve the same candidate: flipping the flag turns "
      "`test_the_default_policy_is_on_the_same_actor_is_refused` and "
      "`test_the_same_actor_with_no_enforce_argument_is_refused_by_default` "
      "red. Proven by "
      "`python3 products/brothermode/tools/test_bm_vault_lifecycle.py` at "
      "%s." % format_suite_count(lifecycle["ran"], lifecycle["n"]))
    A("")
    A("The promote command and the approval pane apply the same rule, proven "
      "by `python3 products/brothermode/tools/test_bm_vault_promotions.py` "
      "at %s and `python3 products/brothermode/tools/test_bm_vault_pane.py` "
      "at %s." % (format_suite_count(promotions["ran"], promotions["n"]),
                   format_suite_count(pane["ran"], pane["n"])))
    A("")
    A("The %d legacy command shims remain in this cut, per decision D2." % shims)
    A("")
    for line in render_files_table(version, file_rows):
        A(line)
    return "\n".join(L), []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                     help="overwrite docs/releases/<version>.md instead of printing")
    ap.add_argument("--version", default=None,
                     help="the release this note describes (default: "
                          ".claude-plugin/marketplace.json's own "
                          "metadata.version)")
    ap.add_argument("--write-table", action="store_true",
                     help="re-measure ONLY the files table and swap it into "
                          "docs/releases/<version>.md, leaving every other "
                          "claim in an already cut note byte for byte alone")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    version = args.version or default_version()

    if args.write_table:
        # A note that has already been cut states a digest and a revision
        # about the tree AT ITS TAG. Re-running the whole generator over
        # today's tree would silently replace those true claims with false
        # ones, so this mode touches the one section that is a measurement of
        # the checks rather than of the tag.
        path = notes_path_for(version)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print("%s: %s unreadable: %s" % (NODATA, path, exc),
                  file=sys.stderr)
            return 2
        file_rows, problem = measured_file_rows(SUITES)
        if problem:
            print(problem, file=sys.stderr)
            return 2
        new_text = replace_files_table(text,
                                       render_files_table(version, file_rows))
        if new_text is None:
            print("%s: %s carries no '%s' section to replace"
                  % (NODATA, path, FILES_TABLE_HEADING), file=sys.stderr)
            return 2
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        print("rewrote the files table in %s (%d row(s))"
              % (path, len(file_rows)))
        return 0

    body, problems = build(version)
    if body is None:
        print("%s: could not generate the release note; not printing a note "
              "with an unmeasured claim in it." % NODATA, file=sys.stderr)
        for p in problems:
            print(p, file=sys.stderr)
        return 2

    if args.write:
        # The manifest is written FIRST and with the same body: the note
        # states its digest, so a note on disk without its manifest beside it
        # would be a claim with nothing behind it. build() already succeeded,
        # so the memo holds a real manifest here.
        manifest_body = export_manifest()[0]
        manifest_path = manifest_write_path_for(version)
        try:
            with open(manifest_path, "w", encoding="utf-8") as fh:
                fh.write(manifest_body)
        except OSError as exc:
            print("%s: could not write %s: %s" % (NODATA, manifest_path, exc),
                  file=sys.stderr)
            return 2
        notes_path = notes_path_for(version)
        with open(notes_path, "w", encoding="utf-8") as fh:
            fh.write(body)
        print("wrote %s" % manifest_path)
        print("wrote %s" % notes_path)
        return 0

    print(body, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
