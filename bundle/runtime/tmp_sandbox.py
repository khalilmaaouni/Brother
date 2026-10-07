"""One call that makes a test process delete every temporary tree it creates.

Measured 2026-09-04 18:05 on this machine: the user temp directory held
85,119 entries and 11 GB of leftover test trees (brother-lane-*, canon-*,
rewrite-check-*, dep-mutation-*, brother-run-*), which drove free disk from
13 GiB to 8 GiB in two hours of lane runs. The cause is not one bad test:
tempfile.mkdtemp leaves its directory behind BY DESIGN, and this tree calls
it from over 850 places, so every call site is a leak unless somebody
remembers the matching rmtree.

Rather than 850 patches, a test module calls install() once, at import, and
gets three things at no further cost:

  * tempfile.tempdir points at one per-process sandbox, so every later
    mkdtemp, NamedTemporaryFile and TemporaryDirectory in this process lands
    inside it, wherever in the call graph it happens;
  * $TMPDIR points at the same sandbox, so every subprocess the test spawns
    (the tools under test included) puts its own temp trees there too;
  * the whole sandbox is removed when the process exits.

A tool that deliberately leaves evidence behind must NOT call this. Those
tools keep their own --keep flag and are named in the E100 report.
"""
import atexit
import os
import shutil
import sys
import tempfile

_root = None

#: What git exports to its hooks to say WHICH repository is in play (git
#: rev-parse --local-env-vars). A test launched from inside a hook inherits
#: them, and then every `git init`, `git config` and `git commit` it aims at
#: a temp repository lands in the REAL one instead. Measured 2026-09-20: a
#: test run from a pre-push hook set core.bare, core.hooksPath and a fixture
#: identity in the estate's own repository, which disabled every hook and
#: broke every worktree at once.
GIT_LOCATION_VARS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
    "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
    "GIT_INTERNAL_SUPER_PREFIX", "GIT_REPLACE_REF_BASE", "GIT_NO_REPLACE_OBJECTS")


def _checked_env(env):
    """The mapping the git location names are removed from: `env`, or this
    process's own environment when env is None.

    Hostile input is REFUSED with this module's own ValueError before a
    single name is removed, never with a raw interpreter exception and never
    by accepting it: a value that is not a mapping of names at all (a str, a
    list, an int, a bool, a float, bytes), a mapping whose keys cannot be
    read (measured 2026-09-24: an environment whose keys raised TypeError
    unhashable type 'list' came back out of this module as that raw
    TypeError), and an unhashable or otherwise non string key all stop here.
    """
    if env is None:
        return os.environ
    try:
        names = list(env.keys())
    except Exception as exc:  # sbe: a block, never a crash and never a pass
        raise ValueError(
            "drop_git_location: env is not a mapping whose keys can be read "
            "(%s)" % type(exc).__name__)
    for name in names:
        if not isinstance(name, str):
            raise ValueError(
                "drop_git_location: every env key must be a string, not %s"
                % type(name).__name__)
    return env


def drop_git_location(env=None):
    """Remove them from `env` (default: this process). Returns the names
    removed, so a caller can say what it found.

    R7: the one implementation lives in git_location_guard, so the sandbox
    installer, the hook batteries and the preflight cannot drift apart. The
    guard is imported on the call, so tmp_sandbox stays importable even if
    the guard imports this module back, and a missing guard is an ImportError,
    never a silent local fallback. The environment is read through
    _checked_env first, and the guard's own answer (the names it removed)
    comes back unchanged. A guard that cannot read the mapping it was handed
    is a refusal here, never a raw exception into the caller.
    """
    environment = _checked_env(env)
    import git_location_guard
    try:
        return git_location_guard.drop_git_location(environment)
    except ValueError:
        raise
    except Exception as exc:  # sbe: a block, never a crash and never a pass
        raise ValueError(
            "drop_git_location: the git location guard refused this env "
            "(%s: %s)" % (type(exc).__name__, exc))


def _bare_prefix(prefix):
    """The sandbox prefix, checked as one bare name inside the temp root.

    Hostile input is REFUSED with this module's own ValueError, never with a
    raw interpreter exception and never by accepting it: a prefix that is not
    a string, and a prefix that is a path or a parent directory reference,
    both stop here. tempfile.mkdtemp joins the prefix onto the temp root, so
    a path prefix puts the sandbox OUTSIDE that root (measured 2026-09-24: a
    prefix of ../x8n2t04 raised a raw PermissionError out of install inside a
    read only grade slot instead of a refusal).
    """
    if prefix is None:
        return None
    if not isinstance(prefix, str):
        raise ValueError(
            "install: prefix must be a string or None, not %s"
            % type(prefix).__name__)
    if os.path.basename(prefix) != prefix or prefix in (".", ".."):
        raise ValueError(
            "install: prefix must be one bare name inside the temp root, "
            "not a path: %r" % (prefix,))
    return prefix


def install(prefix=None):
    """Point this process's temp root at a sandbox removed on exit.

    Returns the sandbox path. Calling it twice is a no-op that returns the
    same path, so a test module that imports another test module does not
    nest sandboxes.

    R6: drop_git_location runs FIRST, before the already installed check and
    before tempfile.tempdir or os.environ["TMPDIR"] move, so a second install
    in a process launched from a git hook still drops the hook's GIT_DIR. R8:
    after install no git location name is set. The prefix is read through
    _bare_prefix, so a corrupt prefix is a refusal here and not a raw error
    out of tempfile.
    """
    global _root
    # R6: git's location variables go FIRST, before the already installed
    # check and before tempfile.tempdir or os.environ["TMPDIR"] move. The
    # real install returned _root before the drop, so a second install in a
    # process launched from a git hook kept the hook's GIT_DIR and aimed its
    # own git commands at the shared repository the hook named instead of at
    # its own sandbox.
    drop_git_location()
    prefix = _bare_prefix(prefix)
    if _root is not None:
        return _root
    if prefix is None:
        base = os.path.basename(sys.argv[0] or "python") or "python"
        prefix = "brother-test-" + os.path.splitext(base)[0] + "-"
    try:
        _root = tempfile.mkdtemp(prefix=prefix)
    except OSError as exc:
        raise ValueError(
            "install: no sandbox could be made under the temp root: %s" % exc)
    tempfile.tempdir = _root
    os.environ["TMPDIR"] = _root
    atexit.register(remove)
    return _root
    tempfile.tempdir = _root
    os.environ["TMPDIR"] = _root
    atexit.register(remove)
    return _root


def remove():
    """Delete the sandbox, reporting on stderr rather than dying.

    Tests create read-only fixture trees on purpose (unreadable-root-*,
    refuse-broken-*), and rmtree cannot descend into a directory without the
    owner write and execute bits, so restore them first. Anything still
    stuck is named on stderr: a cleanup must never fail a finished proof,
    and it must never pretend it succeeded either.
    """
    global _root
    if _root is None or not os.path.isdir(_root):
        _root = None
        return
    for dirpath, dirnames, _filenames in os.walk(_root):
        for name in [dirpath] + [os.path.join(dirpath, d) for d in dirnames]:
            try:
                os.chmod(name, 0o700)
            except OSError as exc:
                sys.stderr.write(
                    "tmp_sandbox: cannot chmod %s: %s\n" % (name, exc))
    try:
        shutil.rmtree(_root)
    except OSError as exc:
        sys.stderr.write(
            "tmp_sandbox: left behind %s: %s\n" % (_root, exc))
    finally:
        _root = None


def _demo():
    """Fails if the sandbox does not actually contain and remove the trees."""
    real = tempfile.gettempdir()
    os.environ["GIT_DIR"] = "/nonexistent/real-repository/.git"
    os.environ["GIT_INDEX_FILE"] = "/nonexistent/index"
    root = install(prefix="tmp-sandbox-demo-")
    assert "GIT_DIR" not in os.environ and "GIT_INDEX_FILE" not in os.environ, \
        "a test launched from a git hook would write into the real repository"
    assert root.startswith(real), (root, real)
    assert os.environ["TMPDIR"] == root, os.environ["TMPDIR"]
    inside = tempfile.mkdtemp(prefix="child-")
    assert os.path.dirname(inside.rstrip(os.sep)) == root.rstrip(os.sep), inside
    locked = os.path.join(inside, "locked")
    os.mkdir(locked)
    with open(os.path.join(locked, "f"), "w") as handle:
        handle.write("x")
    os.chmod(locked, 0o500)
    remove()
    assert not os.path.exists(root), root
    print("PASS tmp_sandbox: sandbox contained and removed %s" % root)


if __name__ == "__main__":
    _demo()
