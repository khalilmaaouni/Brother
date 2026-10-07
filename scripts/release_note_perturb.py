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

#: C0.1: the ownership marker perturb_pool.py writes beside a work copy it
#: made (its own MARKER_SUFFIX constant, duplicated here as a literal on
#: purpose: this module reads the pool's evidence, it does not import the
#: pool, per the build boundary in docs/plan/specs/C0.md).
_C01_MARKER_SUFFIX = ".made-by-perturb-pool"
_C01_DETACHED_HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
#: (realpath(root), suite_rel) -> seconds a PLAIN green run of that suite took
#: in that exact work copy. Separate from _LAST_DURATION on purpose: this is
#: TIMING PROVENANCE keyed by root as well as suite, so one copy can never
#: lend its calibration to another (R-C01-03). It is never fail-fast
#: permission, which is observed afresh in every covers() call. A runner run
#: never writes here, only a plain green one in a verified work copy.
_C01_CALIBRATED = {}
#: Sentinel distinguishing "was absent" from "was None" when snapshotting a
#: dict entry to restore it later.
_C01_MISSING = object()
#: The in-process fail-fast runner, BESIDE this module: the pool worker
#: imports this module from inside its copy, so this is the copy's runner.
#: A missing runner prints no status line and reads as unproved.
_C01_RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "c01_failfast_runner.py")
_C01_STATUS_PREFIX = "C0.1 fail-fast:"
#: The tail run_suite returns for a runner run whose output carries zero or
#: several status lines. _c01_parse_status rejects it.
_C01_STATUS_ABSENT = "C0.1 runner status absent or duplicated"
_C01_STATUS_RE = re.compile(
    r"^C0\.1 fail-fast: (?:(engaged); result: "
    r"(error|failure|unexpected-success|success|unknown)"
    r"|(not-engaged); result: (plain))$")


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
    _C01_CALIBRATED.clear()


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


def _c01_kill_group(pgid):
    """Kill process group `pgid`, using the SAVED value from spawn time, never
    a fresh `os.getpgid(pid)` lookup: once the session leader has already
    exited and been reaped, `os.getpgid` can fail even while a grandchild it
    spawned is still alive in the same group, which would hide exactly the
    orphan this exists to catch (R-C01-08). Returns None when the kill either
    landed or found nothing there (an already-gone group is the ordinary
    case, not a failure); returns a diagnostic string for any other error, so
    the caller can turn that into NO-DATA rather than a verdict."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return None
    except OSError as exc:
        return str(exc)
    return None


def run_suite(rel_path, root=ROOT, timeout=None, *, fail_fast=False):
    """(returncode, tail). A suite that cannot be spawned, or that runs past
    `timeout` seconds, returns (None, why): both are a failure to reach a
    verdict, never a pass and never a red. A perturbed module CAN hang a
    suite (a retry loop that never terminates once its helper raises), so
    this boundary needs a wall, not patience.

    `fail_fast`, C0.1 (default False, today's command unchanged): run the
    SAME suite path through `_C01_RUNNER` (`[sys.executable, _C01_RUNNER,
    path]`), which switches unittest's fail fast on inside the process and
    leaves the suite's own argv exactly `[path]`. Nothing is appended to the
    suite's command and nothing is passed through the environment. The tail
    of such a run is the one output line starting `C0.1 fail-fast:` when
    exactly one exists, else `_C01_STATUS_ABSENT`. A runner run never
    calibrates `wall_for`'s timing or the per-copy calibration (R-C01-06):
    stopping early says nothing about how long the whole suite takes."""
    if timeout is None:
        timeout = wall_for(rel_path)
    path = os.path.join(root, rel_path)
    if not os.path.isfile(path):
        return None, "%s does not exist" % rel_path
    argv = ([sys.executable, _C01_RUNNER, path] if fail_fast
            else [sys.executable, path])
    started = time.monotonic()
    # start_new_session puts the suite in its own process GROUP, and every
    # return path below kills the whole group rather than the one child.
    # subprocess.run's own timeout kills only the direct child, and these
    # suites spawn their subject as a subprocess: an orphaned grandchild
    # outliving the wall, or outliving a suite that simply finished on its
    # own, can write into the tree AFTER this function has restored the
    # perturbed file, which turns a clean run into a dirty one with nothing
    # raised. That is not hypothetical, it is what left scripts/decide.py
    # carrying an injected block on the first full run.
    try:
        proc = subprocess.Popen(argv, cwd=root,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                start_new_session=True)
    except OSError as exc:
        return None, "could not run %s: %s" % (rel_path, exc)
    # start_new_session makes this pid both the session leader and the
    # process group id: saved now, so cleanup never depends on the leader
    # still being alive later to answer os.getpgid().
    pgid = proc.pid
    out_bytes = b""
    comm_error = None
    try:
        out_bytes = proc.communicate(timeout=timeout)[0]
    except subprocess.TimeoutExpired:
        cleanup_error = _c01_kill_group(pgid)
        if cleanup_error:
            return None, ("%s did not finish within %ds and its process group "
                          "could not be killed: %s" % (rel_path, timeout, cleanup_error))
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # sbe: allow-silent documented (None, why) sentinel per this function's own docstring, read by covers() which propagates it as its own NO-DATA verdict
            return None, ("%s did not finish within %ds and did not die when "
                          "its process group was killed" % (rel_path, timeout))
        return None, ("%s did not finish within %ds" % (rel_path, timeout))
    except (OSError, ValueError) as exc:
        comm_error = exc

    # The child already exited (or communicate() itself failed), but a
    # grandchild it spawned and never reaped can outlive it even though
    # proc.returncode is already set: cleanup runs here too, on every
    # completed path, not only the timeout one (R-C01-08).
    cleanup_error = _c01_kill_group(pgid)
    if comm_error is not None:
        reap = ""
        try:
            proc.wait(timeout=30)
        except (subprocess.TimeoutExpired, OSError) as exc:
            reap = "; the suite could not be reaped: %s" % exc
        return None, ("%s: communication with the suite failed: %s%s%s"
                      % (rel_path, comm_error,
                         "; group cleanup failed: %s" % cleanup_error
                         if cleanup_error else "", reap))
    if cleanup_error:
        return None, ("%s finished but its process group could not be "
                      "confirmed clean: %s" % (rel_path, cleanup_error))
    # Only a GREEN, UNFLAGGED run calibrates the wall, and the reason is a
    # runaway this tool actually had: a perturbed run that finished slowly
    # recorded its own duration, which widened the wall for the next
    # perturbation, which was then allowed to run longer still. The
    # brother_run block of this tree's table ran for over an hour on that
    # feedback loop. A perturbed run's duration says nothing about how long
    # the suite takes when it is behaving, a timed out run says nothing at
    # all, and a flagged run stops before the suite has really finished.
    if not fail_fast and proc.returncode == 0:
        duration = time.monotonic() - started
        _LAST_DURATION[rel_path] = duration
        if _c01_work_copy(root):
            _C01_CALIBRATED[(os.path.realpath(root), rel_path)] = duration
    out = out_bytes.decode("utf-8", "replace")
    if fail_fast:
        found = [ln for ln in out.split("\n")
                 if ln.startswith(_C01_STATUS_PREFIX)]
        return proc.returncode, (found[0] if len(found) == 1
                                 else _C01_STATUS_ABSENT)
    tail = [ln for ln in out.strip().split("\n") if ln.strip()]
    return proc.returncode, (tail[-1] if tail else "(no output)")


def _c01_work_copy(root):
    """True when `root` is a genuine perturb_pool work copy: a real, non
    symlink `.git` directory, a detached HEAD, and a sibling ownership marker
    (perturb_pool's own MARKER_SUFFIX) whose recorded commit matches that
    HEAD exactly. Absent, unreadable, malformed or mismatching evidence is
    False, never a guess (R-C01-02). Never compares against this module's own
    ROOT constant: the worker that calls this imports the module from inside
    its own copy, where ROOT legitimately equals `root`."""
    if not root:
        return False
    stripped = root.rstrip(os.sep)
    if os.path.islink(stripped):
        return False
    git_dir = os.path.join(root, ".git")
    if not os.path.isdir(git_dir) or os.path.islink(git_dir):
        return False
    head_path = os.path.join(git_dir, "HEAD")
    if os.path.islink(head_path):
        return False
    try:
        with open(head_path, encoding="utf-8") as fh:
            head = fh.read().strip()
    except (OSError, UnicodeDecodeError):  # non UTF-8 bytes are malformed
        return False
    if not _C01_DETACHED_HEAD_RE.match(head):
        return False  # a symbolic ref (attached HEAD) never matches this
    marker = stripped + _C01_MARKER_SUFFIX
    if os.path.islink(marker):
        return False
    try:
        with open(marker, encoding="utf-8") as fh:
            recorded = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return False
    return bool(recorded) and recorded == head


def _c01_safe_rel(root, rel):
    """The resolved absolute path of `rel` under `root`, or None when `rel`
    is absolute, escapes `root` by `..` traversal, or resolves through a
    symlink to somewhere outside `root`. Only meaningful once
    `_c01_work_copy(root)` has already confirmed `root` itself is genuine;
    this guards one file inside it before any candidate write (R-C01-02)."""
    if not rel or os.path.isabs(rel):
        return None
    real_root = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root, rel))
    if candidate != real_root and not candidate.startswith(real_root + os.sep):
        return None
    return candidate


def _c01_flag_safe(source):
    """A conservative pre-filter before the fail-fast runner is tried, NOT a
    proof of equivalence (R-C01-05). Uses `ast`, never a substring search: a
    comment, docstring or unused string literal that happens to contain
    `-f`, `--failfast` or `sys.argv` (this estate's own fixture-model strings
    do exactly that) does not decline, while a suite that genuinely reads
    `sys.argv` itself, under any import alias, or builds its own argument
    parser, declines to the plain path. Unparseable source declines."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    sys_aliases = {"sys"}
    argv_aliases = set()
    hostile = [False]
    parser_names = {"ArgumentParser", "OptionParser", "getopt", "gnu_getopt"}

    class _ArgvReader(ast.NodeVisitor):
        def visit_Import(self, node):
            for alias in node.names:
                if alias.name == "sys":
                    sys_aliases.add(alias.asname or alias.name)
            self.generic_visit(node)

        def visit_ImportFrom(self, node):
            if node.module == "sys":
                for alias in node.names:
                    if alias.name == "argv":
                        argv_aliases.add(alias.asname or alias.name)
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if (node.attr == "argv" and isinstance(node.value, ast.Name)
                    and node.value.id in sys_aliases):
                hostile[0] = True
            self.generic_visit(node)

        def visit_Name(self, node):
            if node.id in argv_aliases:
                hostile[0] = True
            self.generic_visit(node)

        def visit_Call(self, node):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None)
            if name in parser_names:
                hostile[0] = True
            self.generic_visit(node)

    _ArgvReader().visit(tree)
    return not hostile[0]


class _C01TimingGuard(object):
    """Snapshots `_LAST_DURATION[suite_rel]` and the per-copy calibration
    entry for `(canon_root, suite_rel)` on entry, restoring both on exit
    whatever happened inside, including an exception or a green perturbation
    (R-C01-06). A runner run never writes either dict, but a green plain
    fallback or plain comparison run does, and while a candidate is being
    measured neither may leave a calibration behind."""

    def __init__(self, suite_rel, canon_root):
        self.suite_rel = suite_rel
        self.canon_root = canon_root

    def __enter__(self):
        self._dur = _LAST_DURATION.get(self.suite_rel, _C01_MISSING)
        self._calib = _C01_CALIBRATED.get(
            (self.canon_root, self.suite_rel), _C01_MISSING)
        return self

    def __exit__(self, *exc_info):
        if self._dur is _C01_MISSING:
            _LAST_DURATION.pop(self.suite_rel, None)
        else:
            _LAST_DURATION[self.suite_rel] = self._dur
        key = (self.canon_root, self.suite_rel)
        if self._calib is _C01_MISSING:
            _C01_CALIBRATED.pop(key, None)
        else:
            _C01_CALIBRATED[key] = self._calib
        return False


def _c01_parse_status(tail):
    """(engagement, result) when `tail` is exactly one runner status line in
    the grammar c01_failfast_runner.py writes, else None. The fixed
    absent-or-duplicated tail, a plain run's tail and anything malformed are
    all None."""
    m = _C01_STATUS_RE.fullmatch(tail or "")
    if not m:
        return None
    if m.group(1):
        return m.group(1), m.group(2)
    return m.group(3), m.group(4)


def _c01_wall(canon_root, suite_rel):
    """wall_for's own floor and factor, applied to this root's calibration."""
    return max(60, int(_C01_CALIBRATED[(canon_root, suite_rel)]
                       * WALL_FACTOR) + 1)


def baseline_probe(suite_rel, root=ROOT, timeout=None):
    """(baseline_rc, flag_accepted, tail), taken on the CLEAN tree before any
    perturbation (R-C01-04). The flagged probe runs only in a verified work
    copy, on a suite path inside it, whose source parses and does not read
    argv itself, with this root's calibration present (its wall is the
    timeout when none is given). flag_accepted is True only for an observed
    integer exit 0 whose status reads `engaged; result: success`. Every other
    outcome (not engaged, another token, nonzero, absent, duplicated or
    malformed status, timeout, spawn failure, missing runner, missing or
    unsafe source) never enables fail fast: the ordinary baseline is taken
    with one plain run and that run's rc is returned with False. Nothing is
    remembered: the next candidate probes again."""
    def plain():
        rc, tail = run_suite(suite_rel, root=root, timeout=timeout)
        return rc, False, tail

    if not _c01_work_copy(root):
        return plain()
    safe_suite = _c01_safe_rel(root, suite_rel)
    if safe_suite is None:
        return plain()
    canon_root = os.path.realpath(root)
    if timeout is None:
        if (canon_root, suite_rel) not in _C01_CALIBRATED:
            return plain()
        timeout = _c01_wall(canon_root, suite_rel)
    try:
        with open(safe_suite, encoding="utf-8") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError):
        return plain()
    if not _c01_flag_safe(source):
        return plain()
    rc, tail = run_suite(suite_rel, root=root, timeout=timeout, fail_fast=True)
    if (type(rc) is int and rc == 0
            and _c01_parse_status(tail) == ("engaged", "success")):
        return 0, True, tail
    return plain()


def _c01_restore(path, file_rel, original, digest):
    """Put the original bytes back and prove it, or raise RestoreFailed."""
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


def _c01_decide(suite_rel, root, wall):
    """R-C01-07's decision table, run while the candidate is perturbed:
    (verdict, detail). Each row is named where it is decided."""
    rc, tail = run_suite(suite_rel, root=root, timeout=wall, fail_fast=True)
    if rc is None:  # row 1: never retried
        return None, tail
    status = _c01_parse_status(tail)
    if rc != 0:
        if status in (("engaged", "failure"), ("engaged", "error"),
                      ("engaged", "unexpected-success")):  # row 3
            return True, "%s exit %s: %s" % (suite_rel, rc, tail)
        if status == ("not-engaged", "plain"):  # row 4: it ran plainly
            return True, "%s exit %s: %s" % (suite_rel, rc, tail)
    # rows 2 and 5: the plain run on the same perturbed bytes decides.
    rc2, tail2 = run_suite(suite_rel, root=root, timeout=wall)
    if rc == 0:  # row 2
        if rc2 == 0:
            return False, "%s stayed green (exit 0: %s)" % (suite_rel, tail2)
        if rc2 is None:
            return None, ("%s: the runner run stayed green (%s) but the plain "
                          "comparison could not reach a verdict: %s"
                          % (suite_rel, tail, tail2))
        return None, ("%s: the runner run stayed green (%s) but the plain "
                      "comparison went red (exit %s: %s), a divergence"
                      % (suite_rel, tail, rc2, tail2))
    if rc2 is None:  # row 5
        return None, tail2
    if rc2 != 0:
        return True, "%s exit %s: %s" % (suite_rel, rc2, tail2)
    return False, "%s stayed green (exit 0: %s)" % (suite_rel, tail2)


def covers(suite_rel, file_rel, root=ROOT, baseline=None):
    """Does `suite_rel` go red when `file_rel`'s behaviour is broken?

    Returns (verdict, detail). verdict is True (covered), False (the suite
    stayed green with the file perturbed) or None (NO-DATA: a file that
    cannot be read or parsed, a suite that cannot be run, a suite already red
    before the perturbation, or, inside a verified work copy, missing
    calibration, an escaping path, or a red or unavailable clean probe). A
    FAILED RESTORE raises RestoreFailed: it is never folded into a verdict.

    C0.1: only inside a verified perturb_pool work copy may a run go through
    the fail-fast runner, and only after baseline_probe accepted it on the
    clean tree in this same call. Outside a copy this is the original plain
    path, unconditionally (R-C01-03)."""
    check_ledger()
    in_copy = _c01_work_copy(root)
    if in_copy and (_c01_safe_rel(root, file_rel) is None
                    or _c01_safe_rel(root, suite_rel) is None):
        return None, ("%s or %s escapes the work copy %s"
                      % (file_rel, suite_rel, root))
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
    if not in_copy:
        try:
            with open(path, "wb") as fh:
                fh.write(new_text.encode("utf-8"))
        except OSError as exc:
            return None, "%s could not be perturbed: %s" % (file_rel, exc)
        try:
            rc, tail = run_suite(suite_rel, root=root)
        finally:
            _c01_restore(path, file_rel, original, digest)
        if rc is None:
            return None, tail
        if rc != 0:
            return True, "%s exit %s: %s" % (suite_rel, rc, tail)
        return False, "%s stayed green (exit 0: %s)" % (suite_rel, tail)

    canon_root = os.path.realpath(root)
    if (canon_root, suite_rel) not in _C01_CALIBRATED:
        return None, ("NO-DATA: no calibration recorded for %s in %s; a "
                      "plain green run of it in this copy supplies one"
                      % (suite_rel, root))
    wall = _c01_wall(canon_root, suite_rel)
    with _C01TimingGuard(suite_rel, canon_root):
        probe_rc, flag_ok, probe_tail = baseline_probe(suite_rel, root=root,
                                                       timeout=wall)
        # Whichever run produced it, a clean baseline that is not integer 0
        # stops here, before the candidate write; the caller's baseline=0
        # never overrides it.
        if type(probe_rc) is not int or probe_rc != 0:
            return None, ("%s is not green on the clean probe (exit %s: %s)"
                          % (suite_rel, probe_rc, probe_tail))
        try:
            with open(path, "wb") as fh:
                fh.write(new_text.encode("utf-8"))
        except OSError as exc:
            return None, "%s could not be perturbed: %s" % (file_rel, exc)
        try:
            if flag_ok:
                return _c01_decide(suite_rel, root, wall)
            rc, tail = run_suite(suite_rel, root=root, timeout=wall)
        finally:
            _c01_restore(path, file_rel, original, digest)
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
