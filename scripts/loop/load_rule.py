#!/usr/bin/env python3
"""The estate's ONE load rule (REQ-LA5-B-03), in a file that imports only the standard library.

perturb_pool.capped_workers and the loop pool's load hold both ask machine_busy, so the two can never disagree
at the boundary. It lives here, not in perturb_pool.py, because the pool loads it by file path and the deploy
freeze follows that load: loading perturb_pool.py dragged the release note tooling (and cut.py) into the loop's
deploy closure, which the freeze refused (2026-10-14)."""
import os
from typing import Optional, Tuple


def load_reading() -> Tuple[float, int]:
    """(the one minute load average, the core count or 1). An OSError from the
    platform propagates: a machine that cannot be read is NO-DATA for the caller,
    never a free machine."""
    return (float(os.getloadavg()[0]), os.cpu_count() or 1)


def machine_busy(load1: Optional[float] = None, ncpu: Optional[int] = None) -> bool:
    """True when the one minute load is at or above the core count. THE estate's one
    load comparison (REQ-LA5-B-03): load1 and ncpu are filled from load_reading when
    None, and capped_workers and the loop pool both ask this, so the two callers can
    never disagree at the boundary. A bool, a non number, NaN, an infinity or a
    negative load1 is refused, and so is a bool or a count under 1 for ncpu: a
    hostile value is a refusal, never read as a busy or a free machine."""
    if load1 is None or ncpu is None:
        got_load, got_cpu = load_reading()
        if load1 is None:
            load1 = got_load
        if ncpu is None:
            ncpu = got_cpu
    if isinstance(load1, bool) or not isinstance(load1, (int, float)):
        raise ValueError("load1 must be a real number, not %s" % type(load1).__name__)
    if load1 != load1 or load1 == float("inf") or load1 == float("-inf"):
        raise ValueError("load1 must be finite, not %r" % (load1,))
    if load1 < 0:
        raise ValueError("load1 must not be negative, got %r" % (load1,))
    if isinstance(ncpu, bool) or not isinstance(ncpu, int) or ncpu < 1:
        raise ValueError("ncpu must be a whole number of at least 1, not %r" % (ncpu,))
    return float(load1) >= ncpu
