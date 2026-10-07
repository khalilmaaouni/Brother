"""Brother Core: repository relative path resolution shared by every
file that needs to reach scripts/loop/model_router.py without depending
on some OTHER caller having already put it on sys.path first.

Root cause this module fixes (2026-09-26, found twice, the second time
by the H9 closure check): or_fanout._router() and
openrouter_dispatch._capability_canary() each carried their OWN copy of
a "walk up from this file to find scripts/loop" computation. One copy
was fixed after a real empty-HOME failure; the other still carried the
original off-by-one dirname count. That second copy only ever worked
in a real process because or_fanout's own (already fixed) copy had
usually run first in the same process and left the correct directory
on sys.path already: run openrouter_dispatch.require_capable() alone,
in a fresh process, with nothing else imported first, and it failed
closed with a spurious ModuleNotFoundError based quarantine. One
shared helper, called by both files, cannot drift out of sync with
itself and cannot depend on import order: whichever file calls it
first computes the same answer as any other.
"""

import os


def repo_loop_dir(start_file):
    """scripts/loop under this repository's own root, found by walking
    up from start_file until a directory that actually holds
    model_router.py turns up, or None within a bounded number of hops
    (never past the filesystem root). start_file is normally the
    caller's own __file__, so the walk starts from wherever THAT file
    actually lives on disk, not from the current working directory or a
    hand counted number of parent hops that can silently go stale if a
    file ever moves a level deeper or shallower."""
    directory = os.path.dirname(os.path.abspath(start_file))
    for _ in range(10):
        candidate = os.path.join(directory, "scripts", "loop")
        if os.path.isfile(os.path.join(candidate, "model_router.py")):
            return candidate
        parent = os.path.dirname(directory)
        if parent == directory:  # reached the filesystem root; give up
            return None
        directory = parent
    return None
