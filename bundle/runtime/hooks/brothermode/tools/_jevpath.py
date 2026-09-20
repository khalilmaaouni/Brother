"""The one place `products/brothermode/tools/` (bm_learning.py, bm_vault_staleness.py)
gets the main repo's `scripts/` (home of jev_seam.py and jev_checks.py) mounted onto
sys.path, for the Vault surface's own wave-1 Jev seams (J083, J089).

Same shape as products/brothersbe/src/brothersbe/_jevpath.py, solved once already
tonight for a different product: a short list of real candidate locations a monorepo
dev checkout would have, tried in order, mounting nothing and returning False when
none exist. The seams that depend on this are shadow-only and off by default
regardless (data/jev-seams.json), so a copy of tools/ with no scripts/ sibling loses
nothing it had; it simply cannot run J083/J089's calibration audit outside a dev
checkout.

Eager use, right after the stdlib imports:

    from _jevpath import mount
    if mount():
        try:
            import jev_checks, jev_seam
        except Exception:
            jev_checks = jev_seam = None
    else:
        jev_checks = jev_seam = None

`mount()` is idempotent.
"""
import os
import sys

#: Two real candidate locations, tried in order. `tools/_jevpath.py` -> `tools/` ->
#: `brothermode/` -> `products/` -> repo root -> `scripts/` is the co-located case
#: (this file and scripts/ share one checkout); the expanduser fallback covers the
#: founder's own named dev checkout when this file is loaded from an installed
#: plugin-cache copy instead.
_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "..", "scripts"),
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
