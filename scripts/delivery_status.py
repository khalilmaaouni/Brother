#!/usr/bin/env python3
"""delivery_status: the ONE goal for this session, as named steps with a
clock each.

FOUNDER RULING 2026-09-07, question UI, "A: The delivery line": the founder
read a busy night as no progress because the board's percentage counts rows,
and rows get added as fast as they close. What he needs instead: one goal
per session shown as named steps with a clock each, live at the top of the
board and first in the status command, a blocker as its own card, every step
linking its proof, and a deadline with a done-versus-remaining rule. No
numeric ETA or percent for the goal itself: event times and elapsed minutes
instead.

THE COUNTING RULE mirrors board_status.py's tick contract exactly: a step
that says DONE and does not carry BOTH `ended_at` and `proof` is a CLAIM, not
a DONE, and printing one is a failure to fix (exit 1), the same rule
board_status already enforces for the readiness rows.

Reads the newest docs/plan/delivery/DELIVERY-*.json by default, or one named
with --file. Python 3.9 floor, standard library only, no network.
"""
import argparse
import datetime
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DELIVERY_DIR = os.path.join(ROOT, "docs", "plan", "delivery")
NODATA = "NO-DATA"

#: Offsets actually used on this machine. Anything else prints the raw
#: offset rather than guessing a name for it.
_TZ_ABBREV = {"+09:00": "JST", "+00:00": "UTC", "-00:00": "UTC"}

#: Delivery-card state -> the readiness board's own tag colour class, so the
#: new card uses the estate's existing palette instead of inventing one.
STATE_TAG_CLASS = {
    "DONE": "st-done", "IN PROGRESS": "st-flight", "NOT STARTED": "st-sched",
    "BLOCKED": "st-blocked", "CLAIM": "st-blocked",
}


def _parse_iso(s):
    """Same tolerance as board_status.py's own _parse_iso: a trailing Z
    (fromisoformat rejects one before Python 3.11, and this estate is pinned
    to 3.9), and a naive value assumed UTC. Returns None rather than
    raising, so one malformed timestamp degrades that line only."""
    if not isinstance(s, str) or not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _fmt_clock(dt):
    return dt.strftime("%H:%M")


def _tz_label(dt):
    off = dt.utcoffset()
    if off is None:
        return ""
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    key = "%s%02d:%02d" % (sign, total // 3600, (total % 3600) // 60)
    return _TZ_ABBREV.get(key, key)


def _minutes_between(a, b):
    return int(round((b - a).total_seconds() / 60.0))


def age_minutes(since, now):
    """Minutes from an ISO `since` string to `now`, or None if it does not
    parse. Shared by the CLI's NEEDS YOU lines and the board's NEEDS YOU
    cards so the two can never report a different age for the same row."""
    dt = _parse_iso(since)
    return _minutes_between(dt, now) if dt else None


def newest_file(directory=None):
    """Path to the newest DELIVERY-*.json under `directory` by mtime, or
    None when the directory holds none."""
    directory = directory if directory is not None else DELIVERY_DIR
    files = glob.glob(os.path.join(directory, "DELIVERY-*.json"))
    if not files:
        return None
    return max(files, key=lambda p: (os.path.getmtime(p), p))


def load(path=None):
    """(doc_or_None, error_or_None). `path=None` resolves the newest file at
    call time, never bound into a default, so a caller can point this at a
    fixture."""
    if path is None:
        path = newest_file()
    if not path:
        return None, "no docs/plan/delivery/DELIVERY-*.json file"
    if not os.path.isfile(path):
        return None, "no such file: %s" % path
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "expected a JSON object, got %s" % type(data).__name__
    return data, None


def step_view(step, now):
    """The one structured fact-set for a step, consumed by both the CLI line
    and the readiness board's chain card so the two surfaces cannot drift
    apart on the same numbers.

    'ok' is False only for a CLAIM: a DONE that is missing `ended_at` or
    `proof`. Every other state is 'ok' because it is telling the truth about
    itself; only a false DONE is a failure to fix.
    """
    sid = step.get("id", "?")
    name = step.get("name", "")
    state = str(step.get("state") or "").upper().strip()
    expected = step.get("expected_minutes")
    proof = step.get("proof")
    started = _parse_iso(step.get("started_at"))
    ended = _parse_iso(step.get("ended_at"))

    view = {"id": sid, "name": name, "state": state or NODATA,
            "expected_minutes": expected, "proof": proof,
            "elapsed_minutes": None, "duration_minutes": None,
            "ended_clock": None, "ok": True, "claim_reason": None}

    if state == "DONE":
        if ended and proof:
            view["ended_clock"] = _fmt_clock(ended)
            if started:
                view["duration_minutes"] = _minutes_between(started, ended)
        else:
            view["ok"] = False
            view["claim_reason"] = "proof" if not proof else "ended_at"
    elif state == "IN PROGRESS" and started:
        view["elapsed_minutes"] = _minutes_between(started, now)

    return view


def step_line(view):
    """The CLI text for one step_view, in the shapes the ruling names:
    `S3  NOT STARTED  <name>  (expected 12 min)`
    `S2  IN PROGRESS  <name> (31 min elapsed of 30 expected)`
    `S1  DONE at 19:41 (25 min)  proof: <path>`
    """
    sid, name, state = view["id"], view["name"], view["state"]

    if state == "DONE":
        if view["ok"]:
            dur = (" (%d min)" % view["duration_minutes"]
                   if view["duration_minutes"] is not None else "")
            return "%s  DONE at %s%s  proof: %s" % (sid, view["ended_clock"], dur, view["proof"])
        return "%s  CLAIM  %s  (says DONE but missing %s)" % (sid, name, view["claim_reason"])

    if state == "IN PROGRESS":
        exp = view["expected_minutes"]
        if view["elapsed_minutes"] is not None:
            tail = ((" (%d min elapsed of %d expected)" % (view["elapsed_minutes"], exp))
                    if exp is not None else " (%d min elapsed)" % view["elapsed_minutes"])
        else:
            tail = " (elapsed unknown, no started_at)"
        return "%s  IN PROGRESS  %s%s" % (sid, name, tail)

    if state == "NOT STARTED":
        tail = " (expected %d min)" % view["expected_minutes"] if view["expected_minutes"] is not None else ""
        return "%s  NOT STARTED  %s%s" % (sid, name, tail)

    if state == "BLOCKED":
        return "%s  BLOCKED  %s" % (sid, name)

    return "%s  %s  %s" % (sid, state, name)


def needs_you_lines(doc, now):
    """One 'NEEDS YOU:' line per blocker and per decision, each carrying its
    age in minutes. Empty list, never a placeholder line: the caller decides
    what an empty queue prints."""
    lines = []
    for b in doc.get("blockers") or []:
        age = age_minutes(b.get("since"), now)
        age_txt = ("%d min" % age) if age is not None else NODATA
        lines.append("NEEDS YOU: blocker: %s (owner %s, %s since %s)"
                     % (b.get("what", ""), b.get("who", NODATA), age_txt,
                        b.get("since", NODATA)))
    for d in doc.get("decisions_needed") or []:
        age = age_minutes(d.get("since"), now)
        age_txt = ("%d min" % age) if age is not None else NODATA
        lines.append("NEEDS YOU: decision: %s (screen %s, %s since %s)"
                     % (d.get("question", ""), d.get("screen_path", NODATA), age_txt,
                        d.get("since", NODATA)))
    return lines


def deadline_view(doc, now):
    """{'text', 'final_half_hour', 'clock', 'tz', 'left_minutes'}.

    `left_minutes` is signed: negative means past the deadline, which still
    counts as inside the final half hour (it is later than the last thirty
    minutes, not outside them).
    """
    deadline = _parse_iso(doc.get("deadline"))
    if not deadline:
        return {"text": "deadline: %s" % NODATA, "final_half_hour": False,
                "clock": None, "tz": None, "left_minutes": None}
    left_minutes = (deadline - now).total_seconds() / 60.0
    clock = _fmt_clock(deadline)
    tz = _tz_label(deadline)
    total_m = int(round(abs(left_minutes)))
    h, m = divmod(total_m, 60)
    if left_minutes >= 0:
        text = "deadline: %s %s, %dh %dm left" % (clock, tz, h, m)
    else:
        text = "deadline: %s %s, overdue by %dh %dm" % (clock, tz, h, m)
    return {"text": text, "final_half_hour": left_minutes <= 30,
            "clock": clock, "tz": tz, "left_minutes": left_minutes}


def render_lines(doc, now=None):
    """(lines, ok). `now=None` resolves at call time, never bound into a
    default. `ok` is False when any step is a CLAIM, the same signal
    board_status uses to fail its own build on an unevidenced DONE."""
    now = now if now is not None else datetime.datetime.now(datetime.timezone.utc)

    lines = ["GOAL: %s" % doc.get("goal", NODATA)]
    if doc.get("owner"):
        lines.append("owner: %s" % doc["owner"])

    ok = True
    for step in doc.get("steps") or []:
        view = step_view(step, now)
        ok = ok and view["ok"]
        lines.append(step_line(view))

    needs = needs_you_lines(doc, now)
    lines.extend(needs if needs else ["NEEDS YOU: nothing"])

    lines.append("next event: %s" % doc.get("next_event", NODATA))

    dview = deadline_view(doc, now)
    lines.append(dview["text"])
    if dview["final_half_hour"]:
        lines.append("FINAL HALF HOUR: no new work starts; done versus remaining is written")

    return lines, ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", help="delivery JSON path; default: newest under docs/plan/delivery/")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    doc, err = load(args.file)
    if doc is None:
        print("%s: %s" % (NODATA, err), file=sys.stderr)
        return 2

    lines, ok = render_lines(doc)
    for line in lines:
        print(line)
    if not ok:
        print("", file=sys.stderr)
        print("A step claims DONE without both ended_at and proof, printed above as "
              "CLAIM. That is not progress until the evidence is filed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
