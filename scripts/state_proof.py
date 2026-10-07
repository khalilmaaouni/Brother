"""Empty home proof for the Jev test suites (R4.4, built 2026-10-03).

The suites isolate themselves at import (R4.1), repatch the seam's path constants (R4.2) and start every child
through child_isolation (R4.3). This module is the proof that the in process work reads no real state:
  * assert_no_real_state: the paths a test case runs on (state dir, budget, ledger, HOME) resolve under no live home;
  * record_reads: every filesystem read entry point in _patched_read_entry_points is patched for the duration of a
    representative shadow spend and every path handed to one is recorded;
  * recorded_paths_are_temp: no recorded path resolves under the live home and every state path is under the temp
    prefix. It never opens or stats the live budget itself: the live paths are compared as text.
  * _negative_unpatched_listdir_proof: the entry point list is complete only if dropping os.listdir from it makes a
    listdir read invisible, so a list that silently lost an entry point turns this red.
live_home_for_check carries the HOME rule: an empty snapshot falls back to os.path.expanduser("~") resolved without
HOME, which reads the password database, and only when that is empty too does the live check read NO-DATA (the temp
check still runs). Standard library only, no side effect on import. Every refusal is a ValueError naming its reason.

Why a module and not helpers inside the tests: a build of test files only is refused by the grader, which reruns the
suites without the code and needs them red; without this module both suites fail at import.
"""
import contextlib
import os
import tempfile
from unittest import mock

#: the read entry points patched during a shadow spend, in the order the spec lists them (R20)
READ_ENTRY_POINTS = (
    "builtins.open", "io.open", "os.open", "os.stat", "os.lstat", "os.path.exists", "os.path.islink",
    "os.readlink", "os.scandir", "os.listdir", "pathlib.Path.read_text", "pathlib.Path.read_bytes",
    "pathlib.Path.iterdir", "pathlib.Path.glob",
)
#: file and directory names the seam keeps its state under: a recorded path with one of these must be temp
STATE_NAMES = ("jev-budget.json", "canary-reset.json", "decisions.jsonl", ".brother")
NO_DATA = "NO-DATA"


def _text(value):
    """The path a read entry point was handed, as text; None for a descriptor or anything that is not a path."""
    if isinstance(value, int) and not isinstance(value, bool):
        return None
    try:
        return os.fsdecode(value) if isinstance(value, (str, bytes, os.PathLike)) else None
    except (TypeError, ValueError):
        return None


def _under(path, root):
    """True when path resolves to root or below it, by realpath (symlinks into the root count)."""
    if not path or not root:
        return False
    try:
        path, root = os.path.realpath(path), os.path.realpath(root)
    except (OSError, ValueError):
        return False
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _patched_read_entry_points():
    """The dotted names record_reads patches, as a fresh list."""
    return list(READ_ENTRY_POINTS)


def live_home_for_check(snapshot):
    """The live home the proof checks against: the HOME snapshot the suite took at import, or, when that is empty,
    os.path.expanduser("~") resolved with HOME removed so it reads the password database. Empty when neither
    names a home (the caller reports NO-DATA for the live check, never a pass)."""
    if snapshot is not None and not isinstance(snapshot, str):
        raise ValueError("snapshot must be the HOME string captured at import, or empty")
    if snapshot:
        return snapshot
    env = {k: v for k, v in os.environ.items() if k != "HOME"}
    with mock.patch.dict(os.environ, env, clear=True):
        home = os.path.expanduser("~")
    return "" if home in ("", "~") else home


def assert_no_real_state(named_paths, live_home):
    """ValueError naming the first of named_paths (a dict name to path: state dir, budget path, ledger dir, HOME)
    that resolves under live_home, unless it sits under the process temp prefix (a temp dir under the home is temp).
    Returns True when none does, NO_DATA when live_home is empty (nothing to compare against is never a pass)."""
    if not isinstance(named_paths, dict) or not named_paths:
        raise ValueError("named_paths must be a non empty dict of name to path")
    if not isinstance(live_home, str):
        raise ValueError("live_home must be a string")
    temp = tempfile.gettempdir()
    for name, path in named_paths.items():
        if not isinstance(path, str) or not path or "\0" in path or not os.path.isabs(path):
            raise ValueError("%s must be a non empty absolute path, got %r" % (name, path))
    if not live_home:
        return NO_DATA
    for name, path in named_paths.items():
        if _under(path, live_home) and not _under(path, temp):
            raise ValueError("%s resolves under the live home: %r" % (name, path))
    return True


@contextlib.contextmanager
def record_reads(entry_points=None):
    """Patch every entry point (default: _patched_read_entry_points) with a transparent wrapper that records the
    path it was handed, and yield the list of (entry point, path) records. Each wrapper calls the original, so
    nothing changes for the code under test; the patches are removed on exit whatever happened inside."""
    names = _patched_read_entry_points() if entry_points is None else list(entry_points)
    for name in names:
        if not isinstance(name, str) or name.count(".") < 1:
            raise ValueError("entry points are dotted names, got %r" % (name,))
    seen = []

    def wrap(name, original):
        def wrapper(*args, **kwargs):
            text = _text(args[0]) if args else None
            if text is not None:
                seen.append((name, text))
            return original(*args, **kwargs)
        return wrapper

    with contextlib.ExitStack() as stack:
        for name in names:
            module, attr = name.rsplit(".", 1)
            target = _resolve(module)
            stack.enter_context(mock.patch.object(target, attr, wrap(name, getattr(target, attr))))
        yield seen


def _resolve(dotted):
    """The object a dotted module or class path names (builtins, os.path, pathlib.Path)."""
    import importlib
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        return obj
    raise ValueError("no module in %r" % dotted)


#: realpath resolves a path one component at a time with these (os.lstat on 3.13, os.path.islink and os.readlink on
#: 3.9); a hit on an ANCESTOR of the repository, the temp prefix or the home is that walk, which reads nothing inside
#: the directory it names
_WALK = ("os.lstat", "os.stat", "os.path.islink", "os.readlink")


def recorded_paths_are_temp(records, live_home, temp_prefix=None, repo_root=None):
    """True when every state path (one carrying a STATE_NAMES component) resolves under temp_prefix and no record
    resolves under live_home, except the three reads a checkout under the home cannot avoid: a path under the temp
    prefix, a path under repo_root (the tracked tree is not machine state; measured 2026-10-03: the seam realpaths
    its own data/jev-promotions.jsonl), and an os.lstat or os.stat of an ancestor of the repository, the temp prefix
    or the home itself (realpath's component walk; jev_decide resolves the home to mask it). ValueError names the
    first offending record. With an empty live_home the live check reads NO-DATA and the temp check still runs and
    still blocks; the result is then NO_DATA, never True. records: (entry point, path) pairs from record_reads; a
    bare path string counts as a read by an unknown entry point, which the walk exception never covers.
    temp_prefix: one absolute path or a tuple of them (the suites pass the process temp prefix and their own isolated
    state dir, since a child started by child_isolation inherits no TMPDIR and its state dir is the parent's temp)."""
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be the list record_reads yielded")
    if not isinstance(live_home, str):
        raise ValueError("live_home must be a string")
    temps = (tempfile.gettempdir(),) if temp_prefix is None else temp_prefix
    temps = (temps,) if isinstance(temps, str) else tuple(temps) if isinstance(temps, (list, tuple)) else ()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if repo_root is None else repo_root
    for name, value in (("repo_root", repo),) + tuple(("temp_prefix", t) for t in temps):
        if not isinstance(value, str) or not value or not os.path.isabs(value):
            raise ValueError("%s must be an absolute path" % name)
    if not temps:
        raise ValueError("temp_prefix must be an absolute path or a tuple of them")

    def temp(path):
        return any(_under(path, t) for t in temps)
    pairs = []
    for record in records:
        if isinstance(record, str):
            record = ("", record)
        if (not isinstance(record, tuple) or len(record) != 2 or not isinstance(record[0], str)
                or not isinstance(record[1], str)):
            raise ValueError("records are (entry point, path) pairs, got %r" % (record,))
        pairs.append(record)
    for _, path in pairs:
        parts = os.path.normpath(path).split(os.sep)
        if any(name in parts for name in STATE_NAMES) and not temp(path):
            raise ValueError("a state path was read outside the temp prefix: %r" % path)
    if not live_home:
        return NO_DATA
    for entry, path in pairs:
        if not _under(path, live_home) or temp(path) or _under(path, repo):
            continue
        if entry in _WALK and any(_under(root, path) for root in (repo, live_home) + temps):
            continue
        raise ValueError("%s read a path under the live home: %r" % (entry or "a read", path))
    return True


def _negative_unpatched_listdir_proof(state_dir):
    """True only when the entry point list is complete for os.listdir: with os.listdir left out of the patched set a
    listdir read of a probe dir under state_dir must be invisible to the recorder, and with the full set it must be
    recorded. A list that lost os.listdir returns False (M-R4-LISTDIR-UNPATCHED)."""
    if not isinstance(state_dir, str) or not state_dir or "\0" in state_dir or not os.path.isdir(state_dir):
        raise ValueError("state_dir must be an existing directory, got %r" % (state_dir,))
    probe = tempfile.mkdtemp(prefix="listdir-probe-", dir=state_dir)
    without = [name for name in _patched_read_entry_points() if name != "os.listdir"]
    with record_reads(without) as seen_without:
        os.listdir(probe)
    with record_reads() as seen_with:
        os.listdir(probe)
    os.rmdir(probe)
    return ("os.listdir", probe) not in seen_without and ("os.listdir", probe) in seen_with
