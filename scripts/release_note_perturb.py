#!/usr/bin/env python3
"""release_note_perturb: every file the release note names really goes red.

THE DEFECT this closes, found by an external delivery-proof critic reading a
pristine clone at v1.0.1 (row E95): the note's "Files behind these claims"
table was built by parsing each suite's own imports, and an import is not
evidence of coverage. Four of the eleven files it named survived a
perturbation under the suite named beside them:

  * products/brothermode/tools/bm_vault.py was listed under the recall hook
    suite, which writes its own fake bm_vault.py into a temporary directory
    and shells out to that: the real file is never executed, and two
    independent perturbations left the suite at "Ran 22 tests OK".
  * scripts/loom.py was listed under scripts/test_brother_run.py, which does
    not import loom at all; the suite that does catch a broken loom,
    scripts/test_loom.py, was not named anywhere in the note.
  * products/brothermode/tools/bm_vault_promotions.py and bm_vault_audit.py
    each survived one of the two behaviour lines the critic tried.

The generator's candidate list was fixed separately (it no longer names a
module the suite does not import, which is what removed loom.py and
bm_vault.py from the candidate pool); this tool is the part that stops the
same class returning, by refusing to take an import for a check.

A table like that is worse than no table: it invites a reader to trust a
check that cannot fail.

WHAT "COVERED" MEANS HERE, and the LIMIT of it, both measured on this tree
rather than argued. Inserting `raise RuntimeError` at a module's top proves
only that something in the run LOADS the file, which is barely more than the
import the old table already trusted. So the perturbation applied here is one
step stronger and CALL LEVEL: one injected block, placed above the module's
own `if __name__ == "__main__":` guard, replaces every function and every
non-dunder method the module defines with one that RAISES WHEN CALLED. The
module still imports; the first call into it fails. A suite that merely
imports the file stays green and is reported as not covering it; a suite that
drives it, in this process or through a subprocess, goes red.

That catches the worst of the four rows the critic disproved, measured here:
products/brothermode/tools/bm_vault.py under the recall hook suite stays at
exit 0 "OK", so it reads as NOT covered, exactly as the critic found, while
products/brothermode/tools/test_bm_vault.py takes it to exit 1 with 28
failures and 13 errors.

IT DOES NOT CATCH ALL FOUR, and that is stated here rather than discovered
later. The critic's loom.py finding was BRANCH level: one `"hold"` comparison
altered, with test_brother_run.py staying green. This tool's call-level
perturbation of the same file takes test_brother_run.py to exit 1 with 26
failures and 29 errors, because brother_run.py does call into loom, just not
down that branch. So a row this tool passes means "breaking this file's
functions is noticed by that suite", NOT "every branch of this file is
covered". Full mutation testing is the thing that would say the latter, and
this is not it. What the table can honestly promise, and now does, is that no
row names a check which cannot fail at all.

RESTORE IS PART OF THE VERDICT, not a cleanup step. Every file is read into
memory, hashed, perturbed, and written back from those bytes in a finally
block, then re-hashed. A restore that does not reproduce the original digest
is a FAIL of this tool, printed as such, never a warning: leaving the tree
perturbed would be a worse outcome than any verdict about it.

EXITS, matching scripts/release_invariant.py's convention and check_all.sh's
run_check reading (0 pass, 2 no-data, anything else fail):
  0  every file row that COULD be driven went red under the suite named for
     it, and at least one row was driven.
  1  any driven row stayed green (the named suite does not cover the file),
     or any restore failed.
  2  NO-DATA: no release note, no table in it, or no row that could be driven.
A row whose suite column already reads NO-DATA in the note is printed as a
NO-DATA line and is never counted as a pass.

Python 3, standard library only. No network.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODATA = "NO-DATA"
RELEASES = os.path.join("docs", "releases")
TABLE_HEADING = "## Files behind these claims"
#: Seconds one suite run may take when nothing better is known. A perturbed
#: module turns a bounded loop into an unbounded one often enough that the
#: wall is load bearing: without it a single hung suite stalls the whole
#: table. Measured here, a flat 900 was far too generous, several suites hung
#: on perturbation and each one burned fifteen minutes to report NO-DATA, so
#: the wall is CALIBRATED per suite by wall_for() below and this is only the
#: fallback for a suite nobody has timed yet.
TIMEOUT = int(os.environ.get("BROTHER_PERTURB_TIMEOUT", "300"))
#: How many times its own green runtime a suite gets before the wall. Green is
#: the only honest yardstick: a suite that finishes in 12 seconds untouched
#: has nothing useful to say in the tenth minute. THREE, not more, and the
#: reason is measured: a perturbed suite that is going to fail fails FASTER
#: than green (the first call into the broken module raises), so the only
#: thing a longer wait buys is patience with a suite that has hung. At eight
#: the brother_run block of this tree's own table took over an hour; the
#: separation between "failed" and "hung" needs a small multiple, not a
#: generous one.
WALL_FACTOR = int(os.environ.get("BROTHER_PERTURB_WALL_FACTOR", "3"))
#: suite path -> seconds its last completed run took.
_LAST_DURATION = {}


def wall_for(suite_rel):
    """Seconds to allow this suite, from its own last measured runtime. Falls
    back to TIMEOUT for a suite that has never finished here."""
    seen = _LAST_DURATION.get(suite_rel)
    if seen is None:
        return TIMEOUT
    return max(60, int(seen * WALL_FACTOR) + 1)

#: Injected verbatim. Every name in it carries the same long prefix so the
#: loop that rewrites the module's own globals can skip itself by name rather
#: than by a heuristic.
PERTURB_BLOCK = '''

def _release_note_perturb_install():
    """Injected by scripts/release_note_perturb.py; removed by the same run.

    Replaces every function and non-dunder method this module defines with
    one that raises when CALLED. Import still succeeds on purpose: a suite
    that only imports the module must stay green, so the table can tell an
    import apart from real coverage."""
    import types as _release_note_perturb_types
    _release_note_perturb_scope = globals()

    def _release_note_perturb_raiser(label):
        def _release_note_perturb_call(*args, **kwargs):
            raise RuntimeError("release_note_perturb: %s" % label)
        return _release_note_perturb_call

    for _release_note_perturb_n, _release_note_perturb_v in list(
            _release_note_perturb_scope.items()):
        if _release_note_perturb_n.startswith("_release_note_perturb"):
            continue
        if (isinstance(_release_note_perturb_v,
                       _release_note_perturb_types.FunctionType)
                and getattr(_release_note_perturb_v, "__module__", None)
                == __name__):
            _release_note_perturb_scope[_release_note_perturb_n] = (
                _release_note_perturb_raiser(_release_note_perturb_n))
        elif (isinstance(_release_note_perturb_v, type)
                and getattr(_release_note_perturb_v, "__module__", None)
                == __name__):
            for _release_note_perturb_a, _release_note_perturb_m in list(
                    vars(_release_note_perturb_v).items()):
                if not isinstance(_release_note_perturb_m,
                                  _release_note_perturb_types.FunctionType):
                    continue
                if _release_note_perturb_a.startswith("__"):
                    continue
                try:
                    setattr(_release_note_perturb_v, _release_note_perturb_a,
                            _release_note_perturb_raiser(
                                "%s.%s" % (_release_note_perturb_n,
                                           _release_note_perturb_a)))
                except (AttributeError, TypeError):
                    # A class that refuses attribute assignment (__slots__, a
                    # C type) contributes no perturbation rather than
                    # aborting the whole measurement.
                    pass


_release_note_perturb_install()

'''


class RestoreFailed(Exception):
    """A perturbed file could not be put back exactly as it was found."""


#: path -> sha256 of the bytes this run restored it to. Checked before every
#: perturbation, so a file that goes dirty AFTER the measurement that touched
#: it is caught at the next step instead of at the end of a run nobody is
#: watching. The first full run of this tool restored every file it perturbed
#: and still finished with scripts/decide.py carrying an injected block, which
#: means something wrote it after the restore returned; the process group kill
#: in run_suite removes the likeliest writer, and this ledger makes any
#: remaining one impossible to miss.
_RESTORE_LEDGER = {}


def reset_ledger():
    """Start a fresh run. The ledger is about ONE measurement pass over one
    tree; carrying entries from a previous pass into the next would report a
    file that has legitimately moved since, or one whose tree is gone. The
    measured runtimes go with it: they calibrate a wall for THIS tree."""
    _RESTORE_LEDGER.clear()
    _LAST_DURATION.clear()


def check_ledger():
    """Raise RestoreFailed naming the first file that no longer matches what
    this run restored it to. A no-op before anything has been perturbed."""
    for path, digest in sorted(_RESTORE_LEDGER.items()):
        try:
            with open(path, "rb") as fh:
                now = sha256_bytes(fh.read())
        except OSError as exc:
            raise RestoreFailed("%s could not be re-read after this run "
                                "restored it: %s" % (path, exc))
        if now != digest:
            raise RestoreFailed(
                "%s changed after this run restored it (sha256 %s, expected "
                "%s): something outside this measurement wrote it"
                % (path, now, digest))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def guard_lineno(text):
    """1-based line of the module's first top-level `if __name__ ...` block,
    or None when it has none. The perturbation goes ABOVE that block: a module
    run as a script executes its main() there, and a perturbation appended
    after it would install itself only once main() had already run."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if "__name__" in names:
            return node.lineno
    return None


def perturbed_source(text):
    """`text` with PERTURB_BLOCK inserted, or None when it is not Python."""
    try:
        ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    line = guard_lineno(text)
    if line is None:
        return text.rstrip("\n") + "\n" + PERTURB_BLOCK
    lines = text.split("\n")
    return ("\n".join(lines[:line - 1]) + PERTURB_BLOCK
            + "\n".join(lines[line - 1:]))


def run_suite(rel_path, root=ROOT, timeout=None):
    """(returncode, tail). A suite that cannot be spawned, or that runs past
    `timeout` seconds, returns (None, why): both are a failure to reach a
    verdict, never a pass and never a red. A perturbed module CAN hang a
    suite (a retry loop that never terminates once its helper raises), so
    this boundary needs a wall, not patience."""
    if timeout is None:
        timeout = wall_for(rel_path)
    path = os.path.join(root, rel_path)
    if not os.path.isfile(path):
        return None, "%s does not exist" % rel_path
    started = time.monotonic()
    # start_new_session puts the suite in its own process GROUP, and the
    # timeout path kills the whole group rather than the one child.
    # subprocess.run's own timeout kills only the direct child, and these
    # suites spawn their subject as a subprocess: an orphaned grandchild
    # outliving the wall can write into the tree AFTER this function has
    # restored the perturbed file, which turns a clean run into a dirty one
    # with nothing raised. That is not hypothetical, it is what left
    # scripts/decide.py carrying an injected block on the first full run.
    try:
        proc = subprocess.Popen([sys.executable, path], cwd=root,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                start_new_session=True)
    except OSError as exc:
        return None, "could not run %s: %s" % (rel_path, exc)
    try:
        out_bytes = proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError) as exc:
            return None, ("%s did not finish within %ds and its process group "
                          "could not be killed: %s" % (rel_path, timeout, exc))
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            return None, ("%s did not finish within %ds and did not die when "
                          "its process group was killed" % (rel_path, timeout))
        return None, ("%s did not finish within %ds" % (rel_path, timeout))
    # Only a GREEN run calibrates the wall, and the reason is a runaway this
    # tool actually had: a perturbed run that finished slowly recorded its own
    # duration, which widened the wall for the next perturbation, which was
    # then allowed to run longer still. The brother_run block of this tree's
    # table ran for over an hour on that feedback loop. A perturbed run's
    # duration says nothing about how long the suite takes when it is
    # behaving, and a timed out run says nothing at all.
    if proc.returncode == 0:
        _LAST_DURATION[rel_path] = time.monotonic() - started
    out = out_bytes.decode("utf-8", "replace")
    tail = [ln for ln in out.strip().split("\n") if ln.strip()]
    return proc.returncode, (tail[-1] if tail else "(no output)")


def covers(suite_rel, file_rel, root=ROOT, baseline=None):
    """Does `suite_rel` go red when `file_rel`'s behaviour is broken?

    Returns (verdict, detail). verdict is True (covered), False (the suite
    stayed green with the file perturbed) or None (NO-DATA: a file that
    cannot be read or parsed, a suite that cannot be run, or a suite already
    red before the perturbation). A FAILED RESTORE raises RestoreFailed: it
    is never folded into a verdict about coverage."""
    check_ledger()
    path = os.path.join(root, file_rel)
    try:
        with open(path, "rb") as fh:
            original = fh.read()
    except OSError as exc:
        return None, "%s unreadable: %s" % (file_rel, exc)
    try:
        text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "%s is not utf-8: %s" % (file_rel, exc)
    new_text = perturbed_source(text)
    if new_text is None:
        return None, "%s does not parse as Python" % file_rel

    if baseline is None:
        baseline, base_tail = run_suite(suite_rel, root=root)
        if baseline is None:
            return None, base_tail
    if baseline != 0:
        return None, ("%s is not green before the perturbation (exit %s)"
                      % (suite_rel, baseline))

    digest = sha256_bytes(original)
    try:
        with open(path, "wb") as fh:
            fh.write(new_text.encode("utf-8"))
    except OSError as exc:
        return None, "%s could not be perturbed: %s" % (file_rel, exc)
    try:
        rc, tail = run_suite(suite_rel, root=root)
    finally:
        restore_failure = None
        try:
            with open(path, "wb") as fh:
                fh.write(original)
            with open(path, "rb") as fh:
                back = fh.read()
            if sha256_bytes(back) != digest:
                restore_failure = ("%s did not restore byte-identically "
                                   "(sha256 %s, expected %s)"
                                   % (file_rel, sha256_bytes(back), digest))
            else:
                _RESTORE_LEDGER[path] = digest
        except OSError as exc:
            restore_failure = "%s could not be restored: %s" % (file_rel, exc)
        if restore_failure:
            raise RestoreFailed(restore_failure)
    if rc is None:
        return None, tail
    if rc != 0:
        return True, "%s exit %s: %s" % (suite_rel, rc, tail)
    return False, "%s stayed green (exit 0: %s)" % (suite_rel, tail)


def sibling_suite(file_rel, root=ROOT):
    """`test_<stem>.py` beside the file, this estate's own naming convention,
    or None. This is the ONE fallback the note's generator tries when a
    claim's own suite does not cover a file it imports: scripts/loom.py is
    caught by scripts/test_loom.py, which nothing in the note named."""
    dirn, base = os.path.split(file_rel)
    candidate = os.path.join(dirn, "test_" + base)
    if os.path.isfile(os.path.join(root, candidate)):
        return candidate.replace(os.sep, "/")
    return None


_ROW_RE = re.compile(r"^\|(.+)\|\s*$")


def parse_table(text):
    """[(claim, source_file, suite_or_None)] read from the note's own table.

    suite is None for a row whose suite column reads NO-DATA. Returns [] when
    the heading or the table is not there, which a caller treats as NO-DATA
    and never as an empty pass."""
    lines = text.split("\n")
    try:
        start = lines.index(TABLE_HEADING)
    except ValueError:
        return []
    rows = []
    header_seen = False
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        m = _ROW_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if not header_seen:
            header_seen = True
            continue
        if cells and set("".join(cells)) <= set("- :"):
            continue
        if len(cells) < 3:
            continue
        claim, source, suite = cells[0], cells[1].strip("`"), cells[2]
        if suite == NODATA:
            rows.append((claim, source, None))
        else:
            rows.append((claim, source, suite.strip("`")))
    return rows


def default_version(root=ROOT):
    path = os.path.join(root, ".claude-plugin", "marketplace.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        print("%s: %s unreadable: %s" % (NODATA, path, exc), file=sys.stderr)
        return None
    meta = data.get("metadata") or {}
    return meta.get("version")


def note_path_for(version, root=ROOT):
    return os.path.join(root, RELEASES, "%s.md" % version)


def drive(path, root=ROOT):
    """(exit_code, lines). The whole verdict for one note file, so the self
    test can drive every verdict class against a fixture tree in process."""
    out = []
    reset_ledger()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        out.append("%s: %s unreadable: %s" % (NODATA, path, exc))
        return 2, out

    rows = parse_table(text)
    if not rows:
        out.append("%s: %s carries no '%s' table to drive"
                   % (NODATA, os.path.basename(path), TABLE_HEADING))
        return 2, out

    baselines = {}
    passed, failures, nodata = [], [], []
    for claim, source, suite in rows:
        if suite is None:
            nodata.append("%s: the %s claim names no suite in the note"
                          % (source, claim))
            continue
        if suite not in baselines:
            rc, tail = run_suite(suite, root=root)
            baselines[suite] = rc
            if rc is None:
                nodata.append("%s: %s" % (suite, tail))
            elif rc != 0:
                nodata.append("%s is red before any perturbation (exit %s: %s)"
                              % (suite, rc, tail))
        if baselines[suite] != 0:
            nodata.append("%s under %s not driven: its suite is not green "
                          "first" % (source, suite))
            continue
        try:
            verdict, detail = covers(suite, source, root=root,
                                     baseline=baselines[suite])
        except RestoreFailed as exc:
            out.append("FAIL: %s" % exc)
            out.append("release-note-perturb: a perturbed file was not "
                       "restored; the tree may be dirty, check `git status` "
                       "before anything else")
            return 1, out
        if verdict is True:
            passed.append((source, suite))
            out.append("OK: %s goes red under %s (%s)"
                       % (source, suite, detail))
        elif verdict is False:
            failures.append("%s is named under the %s claim, but %s"
                            % (source, claim, detail))
        else:
            nodata.append("%s under %s: %s" % (source, suite, detail))

    # The last measurement has no next step to be caught by, so the ledger is
    # read once more before any verdict is printed.
    try:
        check_ledger()
    except RestoreFailed as exc:
        out.append("FAIL: %s" % exc)
        out.append("release-note-perturb: the tree did not come back clean; "
                   "check `git status` before trusting anything above")
        return 1, out

    for line in nodata:
        out.append("%s: %s (not a pass, and not a contradiction)"
                   % (NODATA, line))
    if failures:
        for line in failures:
            out.append("FAIL: %s" % line)
        out.append("release-note-perturb: %d of %d driven row(s) did not go "
                   "red" % (len(failures), len(failures) + len(passed)))
        return 1, out
    if not passed:
        out.append("%s: no row in %s could be driven"
                   % (NODATA, os.path.basename(path)))
        return 2, out
    out.append("release-note-perturb: PASS, %d row(s) driven red (%d %s), "
               "rows: %s"
               % (len(passed), len(nodata), NODATA,
                  "; ".join("%s -> %s" % (s, q) for s, q in passed)))
    return 0, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default=None,
                    help="the release note to drive (default: the version in "
                         ".claude-plugin/marketplace.json)")
    ap.add_argument("--note", default=None,
                    help="drive this note file instead of resolving one from "
                         "a version")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.note:
        path = args.note
    else:
        version = args.version or default_version()
        if version is None:
            print("%s: no version to resolve a release note from" % NODATA)
            return 2
        path = note_path_for(version)
    code, lines = drive(path)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
