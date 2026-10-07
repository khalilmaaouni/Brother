#!/usr/bin/env python3
"""A sub unit's proven diagnosis, kept as brief input (plan E step 2b, 2026-10-01). Standard library only.

One file per unit, <notes>/<unit>.txt, one "<sub>: <fact>" line per sub unit. diag_apply writes it, unit_runner reads its
sub unit's line into the brief, runner_pool counts the file's modification time as a fact. The file is rewritten ONLY
when its content changes, so that time is a content signal: a new fact re-admits a parked unit once, an identical fact
re-admits nothing, and no fact ever restarts a runner by itself.
"""
import os

NOTES = os.path.expanduser("~/.claude/evidence/diag-notes")


def write_note(unit, sub, text, notes=None):
    """Put one sub unit's proven fact in <notes>/<unit>.txt, one "<sub>: <fact>" line per sub unit, and return "written" or
    "unchanged". The file is rewritten ONLY when its content changes, so its modification time is a content signal the
    pool can read: a new fact re-admits a parked unit once, an identical one re-admits nothing."""
    for name, v in (("unit", unit), ("sub", sub), ("text", text)):
        if not isinstance(v, str) or not v.strip():
            raise ValueError("%s must be a non-empty string" % name)
    path = note_path(unit, notes)
    line = ("%s: %s" % (sub, " ".join(text.split())))[:900]
    try:
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    except FileNotFoundError:
        old = ""
    kept = [l for l in old.splitlines() if l and not l.startswith(sub + ": ")]
    new = "\n".join(kept + [line]) + "\n"
    if new == old:
        return "unchanged"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(new)
    os.replace(tmp, path)
    return "written"


def note_path(unit, notes=None):
    """Where the unit's note lives; a unit id that is not a plain name is refused."""
    if not isinstance(unit, str) or not unit.strip() or "/" in unit or unit.startswith("."):
        raise ValueError("unit must be a plain id, not a path: %r" % (unit,))
    return os.path.join(notes or NOTES, unit + ".txt")


def note_for(unit, sub, notes=None):
    """The proven fact recorded for this sub unit, or "" when there is none or the note cannot be read."""
    try:
        with open(note_path(unit, notes), encoding="utf-8") as fh:
            return next((l[len(sub) + 2:] for l in fh.read().splitlines() if l.startswith(sub + ": ")), "")
    except (OSError, ValueError):   # sbe: allow-silent an unreadable note adds nothing to the brief; it never blocks a run
        return ""




if __name__ == "__main__":
    import sys
    print(note_for(sys.argv[1], sys.argv[2]) if len(sys.argv) == 3 else "usage: diag_notes.py <unit> <sub>")
