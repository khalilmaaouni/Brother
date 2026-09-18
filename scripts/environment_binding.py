#!/usr/bin/env python3
"""ORCH-32 of the 1.0.20 orchestration control plane: pin the execution
inputs that decide a result, not the noise around them.

WHY THIS EXISTS. 40 recorded path and environment incidents on this estate
share one shape: a check ran somewhere other than where its caller believed,
and the green (or red) it produced was read as a fact about the intended
tree when it was actually a fact about a different one. Three named in this
build's brief: a suite run inherited PYTHONPATH from another cwd and tested
that cwd's own copy of the code; unittest rewrites sys.argv[0] and a sandbox
path prefix that happened to contain a space broke a caller that had quoted
it by hand; a green produced from the wrong repository root looked exactly
like a red being cleared, because nothing had recorded which root produced
it.

WHAT THIS PINS, and nothing else: the working directory, the interpreter,
the repository root and the entries actually on PYTHONPATH, each resolved to
a real path with os.path.realpath. That resolution is the point: a symlinked
cwd (macOS's /tmp against /private/tmp is the standing example on this
machine) reads as the one place bind() and verify() agree on, not two
different strings for the same directory. Deliberately NOT pinned: TERM, a
session id, or any other environment variable that legitimately varies
between two correct runs of the same work. Binding more than the inputs that
actually decide a result invites a false mismatch on volatile noise, which
is the failure mode this module exists to avoid on the other side.

HOW IT IS USED. A caller binds once at the start of a unit of work with
bind(), carries the returned dict alongside whatever verdict that work
produces, and calls verify(binding) immediately before recording that
verdict. A mismatch means the verdict was produced somewhere other than
where the caller believes, and must not be recorded as if it were not.
verify() never raises on a mismatch: it returns the mismatch so the caller
decides what drift means for its own verdict. That decision, and the
VERDICTS vocabulary it is expressed in, belongs to
scripts/orchestrator_invariants.py, not to this module: this module answers
one question only ("did the pinned inputs change"), never "is that okay".

Standard library only. Python 3.9 floor.
"""
import json
import os
import subprocess
import sys

#: The exact fields a binding pins. Order matters only for display; equality
#: is checked field by field so one field's identical value never masks
#: another field's drift.
_FIELDS = ("cwd", "interpreter", "repo_root", "pythonpath")


def _realpath(path):
    """os.path.realpath, but never on an empty or None string: both mean
    'nothing to resolve' and must produce None. os.path.realpath("") would
    otherwise silently return the cwd, which is a different value pretending
    to answer the same question."""
    if not path:
        return None
    return os.path.realpath(path)


def _repo_root(start_dir):
    """The repository root containing start_dir, resolved to a real path, or
    None. None covers three different situations on purpose (git is not
    installed, start_dir is not inside a repository, or the command could
    not be run at all): this function answers "is there a pinned root",
    and every one of those is "no". A caller that must tell them apart
    shells out to git itself; folding that distinction in here would make
    this module's own contract (pin the inputs, nothing else) do more than
    it says."""
    try:
        completed = subprocess.run(
            ["git", "-C", start_dir, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    out = completed.stdout.strip()
    if not out:
        return None
    return _realpath(out)


def _pythonpath_entries():
    """Every PYTHONPATH entry, resolved to a real path, empty entries
    dropped. Order is preserved and duplicates are kept: a caller comparing
    two bindings should see a reordering or a duplicate exactly as it is,
    never have this function collapse it into a set and hide the drift."""
    raw = os.environ.get("PYTHONPATH", "")
    return [_realpath(p) for p in raw.split(os.pathsep) if p]


def bind():
    """Capture the execution inputs that decide a result, at this instant,
    resolved to real paths. Returns a plain JSON-serialisable dict with
    exactly the fields in _FIELDS. Nothing here raises: a missing git or an
    unset PYTHONPATH are legitimate values (None, an empty list), not
    exceptions for bind() itself to catch."""
    cwd = _realpath(os.getcwd())
    return {
        "cwd": cwd,
        "interpreter": _realpath(sys.executable),
        "repo_root": _repo_root(cwd),
        "pythonpath": _pythonpath_entries(),
    }


def verify(binding):
    """Re-measure the same inputs now and compare against a prior bind().

    Returns (ok, mismatches): ok is True only when every pinned field is
    identical to its bound value. mismatches is a list of human-readable
    strings, one per field that drifted, each naming the field, the bound
    value and the current one, so a caller can print exactly what changed
    without re-deriving it. Never raises on a mismatch: drift is data for
    the caller to act on, not a failure of verify() itself.

    Raises KeyError if binding is missing a pinned field: a caller handing
    this a dict that was never produced by bind() (or was truncated by a
    lossy round trip through, say, a subset of keys) must see that
    immediately rather than have the missing field silently read as
    "unchanged".
    """
    for field in _FIELDS:
        if field not in binding:
            raise KeyError(
                "environment_binding.verify: missing pinned field %r "
                "(binding was not produced by bind(), or was truncated)"
                % (field,))
    current = bind()
    mismatches = []
    for field in _FIELDS:
        if current[field] != binding[field]:
            mismatches.append(
                "%s: bound %r, now %r" % (field, binding[field], current[field]))
    return (not mismatches, mismatches)


def main():
    """Self-check entry point: bind, verify immediately against itself (must
    agree, nothing has changed between the two calls), print the binding as
    JSON. Exit 0 unless that immediate self-verify disagrees with itself,
    which would be a defect in this module, not a normal drift report. This
    is not a gate: a caller that wants gate semantics (PASS/FAIL/NO-DATA)
    wraps verify()'s (ok, mismatches) result in orchestrator_invariants'
    VERDICTS vocabulary itself."""
    binding = bind()
    ok, mismatches = verify(binding)
    print(json.dumps(binding, indent=1))
    if not ok:
        print("environment_binding: unexpected self-mismatch: %s" % mismatches,
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
