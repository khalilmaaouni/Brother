#!/usr/bin/env python3
"""jev_g1_seam_cache: NOT a foundation module. Owned only by this wave's
own call sites (board_status.py, export_public.py, doc_assurance.py,
intake_score.py, mobile_hybrid_action_router.py, receipt_door.py); no
foundation module (jev_seam.py, jev_registry.py, jev_decide.py,
jev_cascade.py, jev_calibration.py, jev_canary.py) imports this, and this
module never imports jev_seam at its own top level (lazily only, on a
cache miss) so importing THIS module costs nothing beyond a sys.modules
lookup after the first time.

WHY. Rule 9 of the wave-1 seam brief (wave2/seam-brief-common.md, added
after a measured 52x off-mode slowdown): "when the mode is off, return
the caller's answer immediately: no filesystem access, no import, no
registry load." jev_seam.load_seams_config() itself already satisfies
"no filesystem access" in its own steady state (a per-process mtime
cache), but it still returns a fresh copy.deepcopy() of that cached
dict on EVERY call (main's own item-4 fix, so a mutating caller can
never corrupt the shared cache) -- correct on main's own terms, but it
means a guarded block calling it directly still pays that deepcopy on
every single off-mode call. Measured (opus-review-seams-g1-g3.md, minor
item 2): 3.08 microseconds per off-mode call against main's own 0.36.
This module is the fix: cache the per-entry "is this seam off right
now" answer for the SAME freshness window jev_seam's own config cache
already uses, so a guarded block whose seam is off, the production
default for every entry_id in data/jev-seams.json today, never even
imports jev_seam on a cache hit, let alone calls load_seams_config().

STALENESS IS SAFE, NEVER WRONG. See jev_seam.consult()'s own contract:
off, shadow and advise all return the caller's own local answer
unchanged regardless of what jev says (and the C1 fix applied to every
guarded block in this wave means the RETURN VALUE of consult() is never
even read). A cache entry here can only delay how soon a live mode flip
in data/jev-seams.json is noticed by this wave's call sites, by at most
one extra refresh window on top of jev_seam's own -- it can never make a
call site's answer wrong, only slower to start asking Jev's opinion.
"""
import time

#: Same freshness window jev_seam.py's own config cache uses
#: (jev_seam._CONFIG_CACHE_INTERVAL_S): re-checking more often than this
#: would just re-introduce the cost this module exists to avoid, and
#: less often would make a mode flip in data/jev-seams.json take longer
#: to reach this wave's call sites than it takes to reach jev_seam's own.
_CACHE_INTERVAL_S = 1.0

#: entry_id -> (is_off: bool, checked_at: float). Module-level, shared by
#: every call site in this process: a mode check for J025 a moment ago
#: tells a later J094 call nothing (different entry_id, different key),
#: but two J025 calls within the window share one answer.
_cache = {}


def is_off(entry_id):
    """True when `entry_id` currently resolves to jev_seam.OFF, cached
    per _CACHE_INTERVAL_S (see module docstring). Lazily imports
    jev_seam ONLY on a genuine cache miss: the whole point of this
    module is that a cache HIT touches jev_seam not at all. Never
    raises: any failure resolving the real mode (a missing config file,
    a jev_seam import failure) is treated as "off" for this cache entry
    -- a call site's own guarded block still wraps its consult() call in
    its own try/except regardless, so this is belt, not suspenders, but
    it keeps a transient resolution failure from being cached as
    anything other than the safe default."""
    now = time.monotonic()
    cached = _cache.get(entry_id)
    if cached is not None and (now - cached[1]) < _CACHE_INTERVAL_S:
        return cached[0]
    try:
        import jev_seam
        cfg = jev_seam.load_seams_config()
        off = jev_seam.resolve_mode(cfg, entry_id) == jev_seam.OFF
    except Exception:
        off = True
    _cache[entry_id] = (off, now)
    return off


def reset():
    """Drops every cached entry, so the next is_off() call for any
    entry_id is a guaranteed miss. Production code never calls this;
    tests call it before patching jev_seam.load_seams_config so the
    patched value is what the next is_off() actually reads, rather than
    a still-fresh cache entry from an earlier test or an earlier real
    off-mode call in the same process."""
    _cache.clear()
