"""memory_lift: did the memory fix actually change anything, measured on real work.

WHY THIS EXISTS. An outside benchmark on 2026-08-29 cut this estate's memory
scores and was right to: the architecture measured 25 memory notes, 187 vault
failure notes and 48 recorded lessons, against ZERO recall calls in a session
that then repeated a lesson it had already written down. A store nobody reads is
not memory, it is an archive.

The mechanical cause was found the same day. The repeat guard matched a lesson
against the tool name plus the FILE PATH, while most lessons describe the
CONTENT being written, so a lesson about what a line of code does could never
match a payload that only carried where it was going. The fix folds the content
into the matched text.

The benchmark asked for a memory ON versus OFF experiment on real engineering
work. That is the right experiment and it is slow. This is the cheap one that
can run tonight and answers a narrower question honestly: on THIS session's real
payloads, how many lessons match now that could not match before.

IT IS A LIFT, NOT A PREVENTION RATE. A lesson that matches is a lesson SHOWN.
Whether being shown prevented anything is a different measurement needing a
control, and this file does not pretend to it. Conflating the two is how a
memory system gets scored on its filing rather than its effect.

Python 3, standard library only. No network.
"""
import argparse
import importlib.util
import json
import os
import sys

GUARD = os.path.expanduser("~/.claude/hooks/repeat_guard.py")


def load_guard(path=GUARD):
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("repeat_guard", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # noqa: BLE001  # sbe: allow-silent caller reports NO-DATA
        return None
    return mod if hasattr(mod, "matching_lessons") else None


def payloads(transcript):
    """(tool, path, body) for every write this session actually made.

    Real payloads, not invented ones: a measurement on fabricated input measures
    the fabricator."""
    out = []
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            try:
                rec = json.loads(line)
            except ValueError:
                # A transcript can carry a partially-written trailing line; skip
                # it rather than lose the whole measurement, but say so, since a
                # silently shrunk sample is how a lift number gets read as
                # solid when it is actually missing rows.
                print("memory_lift: %s:%d is not valid JSON, skipping"
                      % (transcript, n), file=sys.stderr)
                continue
            msg = rec.get("message") or {}
            for block in (msg.get("content") or []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name") or ""
                if name not in ("Write", "Edit", "NotebookEdit"):
                    continue
                ti = block.get("input") or {}
                body = str(ti.get("content") or ti.get("new_string") or "")
                out.append((name, str(ti.get("file_path") or ""), body[:8000]))
    return out


def measure(guard, rows):
    """(before, after, lifted). Before is what the OLD matcher saw, tool plus
    path. After is what it sees now, with the content folded in."""
    before = after = 0
    lifted = []
    for tool, path, body in rows:
        old_text = "%s %s" % (tool, path)
        new_text = old_text + "\n" + body if body else old_text
        b = len(guard.matching_lessons(old_text))
        a = len(guard.matching_lessons(new_text))
        before += b
        after += a
        if a > b:
            lifted.append((path, a - b))
    return before, after, lifted


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript", help="a session .jsonl")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    guard = load_guard()
    if guard is None:
        print("NO-DATA: the repeat guard could not be loaded from %s, so nothing "
              "was measured. That is not a zero" % GUARD, file=sys.stderr)
        return 2
    if not os.path.isfile(args.transcript):
        print("NO-DATA: %s is not a file" % args.transcript, file=sys.stderr)
        return 2

    rows = payloads(args.transcript)
    if not rows:
        print("NO-DATA: no Write or Edit payload was found in that transcript, "
              "so there was nothing to match against", file=sys.stderr)
        return 2

    before, after, lifted = measure(guard, rows)
    result = {"writes": len(rows), "matches_path_only": before,
              "matches_with_content": after, "lift": after - before,
              "writes_that_gained": len(lifted)}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print("%d write(s) in this transcript" % len(rows))
    print("  matched on tool plus path only : %d" % before)
    print("  matched with content folded in : %d" % after)
    print("  lift                           : %+d, across %d write(s)"
          % (after - before, len(lifted)))
    for path, gain in lifted[:8]:
        print("      +%d  %s" % (gain, path))
    print("")
    print("A lesson that matches is a lesson SHOWN. Whether it prevented "
          "anything is a different measurement needing a control, and this is "
          "not it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
