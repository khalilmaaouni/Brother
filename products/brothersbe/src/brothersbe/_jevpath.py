"""The one place the main repo's `scripts/` (home of jev_seam.py and
jev_checks.py) gets mounted onto `sys.path`, for review-route's own
wave-2 Jev seams (J049/J050).

WHY THIS IS FAIL-OPEN, NOT A HARD DEPENDENCY. `products/brothersbe` is
installed standalone at `~/.claude/plugins/cache/brother/brothersbe/<ver>/`,
decoupled from the main repo's `scripts/` directory -- a real, verified fact
(2026-09-19), not an assumption: that installed copy has no `scripts/`
sibling at all. `mount()` therefore tries a short list of real candidate
locations a MONOREPO DEV CHECKOUT would have, and returns False, mounting
nothing, when none exist -- exactly the same shape as every other optional
jev_checks/jev_seam import in this estate tonight. The seams that depend on
this are shadow-only and off by default regardless, so an installed,
standalone brothersbe loses nothing it had; it simply cannot yet run
J049/J050's calibration audit outside a dev checkout.

Eager use, right after the stdlib imports:

    from ._jevpath import mount
    if mount():
        try:
            import jev_checks, jev_seam
        except Exception:
            jev_checks = jev_seam = None
    else:
        jev_checks = jev_seam = None

`mount()` is idempotent, matching `_toolspath.mount()`'s own contract.
"""
import os
import sys

#: Two real candidate locations for a monorepo checkout's `scripts/`, tried
#: in order. `src/brothersbe/_jevpath.py` -> `src/` -> `brothersbe/` ->
#: `products/` -> repo root -> `scripts/` is the co-located case (this file
#: and scripts/ share one checkout); the expanduser fallback covers the
#: founder's own named dev checkout when brothersbe is loaded from its
#: installed plugin-cache copy instead.
_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "..", "..", "scripts"),
    os.path.expanduser("~/Brother/scripts"),
)


def mount():
    """True and mounted, or False with nothing changed. Never raises."""
    for candidate in _CANDIDATES:
        try:
            path = os.path.abspath(candidate)
        except Exception:  # noqa: BLE001
            continue
        if os.path.isdir(path) and os.path.isfile(os.path.join(path, "jev_seam.py")):
            if path not in sys.path:
                sys.path.insert(0, path)
            return True
    return False
