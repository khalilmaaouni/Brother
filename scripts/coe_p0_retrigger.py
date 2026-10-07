#!/usr/bin/env python3
"""P0.4 retrigger on close or time.

RQ-RETRIGGER: rerun council at every unit closed or every 2 hours.
RQ-HERMETIC: record_council_run writes to a temp file, no network.

retrigger_due(last_run_epoch) returns True when now - last_run_epoch >= 2 hours.
A missing or empty or corrupt last run is treated as due (refused toward due).
record_council_run(state_path) atomically writes the current epoch as a
single ASCII integer line and returns that epoch.
"""

import os
import threading
import time

RETRIGGER_WINDOW_SECONDS = 2 * 60 * 60

_RECORD_LOCK = threading.Lock()


def retrigger_due(last_run_epoch: int) -> bool:
    """Return True when a council rerun is due.

    Valid input is an int epoch seconds (bool is refused). A negative
    timestamp is treated as corrupt and refused toward due. A missing or
    empty state is represented by 0, also due.
    """
    if isinstance(last_run_epoch, bool) or not isinstance(last_run_epoch, int):
        raise ValueError(
            "last_run_epoch must be an int, got %r" % (last_run_epoch,)
        )
    if last_run_epoch < 0:
        # Corrupt timestamp: refuse toward due rather than accept.
        return True
    now = int(time.time())
    return (now - last_run_epoch) >= RETRIGGER_WINDOW_SECONDS


def record_council_run(state_path: str) -> int:
    """Write the current epoch to state_path atomically and return it.

    state_path must be a non-empty str and must not be a directory.
    Concurrent calls are serialized by a process-wide lock and the file
    is replaced atomically so a reader never sees a partial write.
    """
    if not isinstance(state_path, str) or not state_path:
        raise ValueError(
            "state_path must be a non-empty str, got %r" % (state_path,)
        )
    if os.path.isdir(state_path):
        raise ValueError(
            "state_path points to a directory, not a file: %r" % (state_path,)
        )
    epoch = int(time.time())
    payload = ("%d\n" % epoch).encode("ascii")
    tmp_path = state_path + ".tmp"
    with _RECORD_LOCK:
        try:
            with open(tmp_path, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, state_path)
        except OSError as exc:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise ValueError(
                "could not record council run at %r: %s" % (state_path, exc)
            )
    return epoch


if __name__ == "__main__":
    pass
