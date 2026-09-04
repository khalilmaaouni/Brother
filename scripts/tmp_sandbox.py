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


def install(prefix=None):
    """Point this process's temp root at a sandbox removed on exit.

    Returns the sandbox path. Calling it twice is a no-op that returns the
    same path, so a test module that imports another test module does not
    nest sandboxes.
    """
    global _root
    if _root is not None:
        return _root
    if prefix is None:
        base = os.path.basename(sys.argv[0] or "python") or "python"
        prefix = "brother-test-" + os.path.splitext(base)[0] + "-"
    _root = tempfile.mkdtemp(prefix=prefix)
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
    root = install(prefix="tmp-sandbox-demo-")
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
