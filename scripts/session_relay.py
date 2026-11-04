#!/usr/bin/env python3
"""session_relay: cross-session orchestration as one ledger and a handful of refusals (unit XS1.a, owner order
2026-11-01: "make the orchestration cross session a brother 1.2.0 capability").

WHY. On the night of 2026-10-31 one coordinator session ran the night by hand: it started worker sessions, briefed
them, sent each pull request to a reviewer that had not built it, queued every heavy gate on one machine slot, and
handed over to a fresh coordinator when its spend guard stopped it. Six failures were measured that night: a
coordinator merged its own build and the host refused it; a pull request with commits from two sessions stranded
because neither could merge it; a review went stale when the head moved; the spend ceiling hit mid dispatch and the
handover was written after; briefs that led with the task produced work on stale trees; heavy gates from several
sessions thrashed the machine. This module turns those hand rules into checked ones.

WHAT IT OWNS, and nothing else:
  append / read      the relay ledger, one JSON list rewritten whole: written to a temp file, fsynced, then
                     os.replace, under claim_store.Lock (O_CREAT|O_EXCL across processes). A torn line cannot exist,
                     so read() has no repair path: any shape it does not recognise RAISES LedgerUnreadable, and
                     append() never writes over a ledger it cannot read.
  builders           every session that recorded a `built` event for a pull request, as a set. Attribution is by the
                     relay's own event, never by git author: the owner and every session share one forge account
                     (merge_pins.py, D7), so the forge cannot tell builders apart.
  may_review         a builder is never the reviewer.
  may_merge          a session never merges its own build, and a PASS binds to the exact latest head.
  brief              a worker brief leads with MEASURED state; a missing or NO-DATA state field refuses the brief.
  handover_due       CONTINUE, HANDOVER or STOP from spent, ceiling and a measured reserve.
  gate_command       wraps a heavy gate in scripts/heavy_slot.py on ONE slot.
  stage, gate        (unit FLOW1.b) the same ledger also counts every stage of a unit: harness frozen, implementation
                     done, each review round with its verdict, push, merge and an owner wait, each with the tokens,
                     minutes and defects caught it left (a stage that left no number reads NO-DATA), and every gate
                     with its start, end and PASS or FAIL. How many gates ran at once is RECOMPUTED from the intervals
                     (gate_ledger.concurrency_by_gate), never stored, so it cannot depend on recording order; a caller's
                     own figure may ride beside it as claimed_concurrent.
  report             gates per merged unit and merged units per hour over a window, and the STALL rule: the worklist
                     is the plan's READY SET (every dependency landed on the line, read from a plan file and a git
                     log), never the sessions in flight, so units parked on the owner cannot empty it. Plan files merge
                     by id in the order given, so a small overlay file holds a unit for the owner (owner_hold) or marks
                     one landed under a commit that does not name it (landed), as data. STALL (exit 3) when the ready
                     set is not empty, no unit merged inside the window and loop_heartbeat (its one rule: 25 minute
                     staleness, 2 minute clock skew, ended states) does not read the driver's heartbeat as running.
                     Only commit SUBJECT lines of the git log land a unit, parenthesized remarks excluded.

WHAT IT REUSES, never re-implements: claim_store.Lock; orchestrator_authority's epochs (a `handover` event records
the Lease epochs handoff() returned, checks that they step by one, and that each handover continues the last one,
recorded by the session it was handed to); heavy_slot.py; handover_record.py for
the handover record itself (session_id() names the recording session by the same rule).

AUDIT, NOT YET A CONTROL (named on purpose): may_merge refuses through its own CLI, but nothing in the land path calls
it yet. Slice 2 wires it into land_queue.decide_land and records merge intent before the forge call. Until then a
caller who skips it is not stopped by this module.

The host's session API is outside the product: the relay never starts a session. The coordinator starts one with
the brief brief() rendered and records `dispatched`.

CONTINGENCY. Every reader fails toward BLOCK: an unreadable ledger raises, a pull request with no `built` event is
unknown provenance and refuses, a spend reading that is missing, negative, a bool or NaN raises, and the CLI turns
each into exit 2 (NO-DATA), never 0. Rollback: delete the module and its two tests; nothing imports it.
FOOTPRINT: one ledger file plus one transient lock file and one temp file per append; no process, no network.

CLI (exit 0 allowed or written, 1 refused, 2 NO-DATA, 3 STALL from report only):
  session_relay.py record --ledger P --session S --kind K [--field key=value ...]
  session_relay.py may-review --ledger P --pr N --reviewer S
  session_relay.py may-merge --ledger P --pr N --head SHA --actor S
  session_relay.py handover --spent N --ceiling N --reserve N
  session_relay.py gate -- <command> [args...]      runs the command through heavy_slot.py on one slot
  session_relay.py report --ledger P --window-hours H --plan PLAN [--plan OVERLAY ...] --log GITLOG
                          --shas SHALIST --driver-heartbeat FILE [--now UNIX]
                          exit 0 reported, 2 NO-DATA (ledger, plan, line or --now unreadable), 3 STALL

Python 3.9 floor, standard library plus claim_store.
"""
import argparse
import collections
import datetime
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import claim_store  # noqa: E402
import gate_ledger  # noqa: E402

sys.path.insert(0, os.path.join(HERE, "loop"))
import loop_heartbeat  # noqa: E402  (the one driver liveness rule: read it, never copy it)

SCHEMA = 1
NODATA = "NO-DATA"
HEAVY_SLOT = os.path.join(HERE, "heavy_slot.py")
HANDOVER_FRACTION = 0.8
VERDICTS = ("PASS", "FAIL")
_SHA = re.compile(r"^[0-9a-f]{40}$")

#: every event kind and the fields it must carry beside kind, session and seq
KINDS = {
    "dispatched": ("unit", "worker"),
    "built": ("pr", "head"),
    "reviewed": ("pr", "head", "verdict"),
    "merged": ("pr", "head"),
    "handover": ("from_epoch", "to_epoch", "to_session"),
    "stage": ("unit", "stage", "at_unix"),
    "gate": ("unit", "gate", "status", "started_unix", "ended_unix"),
}
#: the stages of a unit, in the order it lives them; owner_wait marks a unit parked on the owner
STAGES = ("harness_frozen", "implementation_done", "review", "push", "merge", "owner_wait")
STALL_EXIT = 3
#: the state a brief must carry, measured, before the task is allowed to follow it
REQUIRED_STATE = ("repo", "line_tip", "open_prs", "disk_free_gib", "measured_utc", "session")

Decision = collections.namedtuple("Decision", "ok reason")


class LedgerUnreadable(Exception):
    """The ledger exists but is not a shape this module wrote. Never read as empty."""


class BriefRefused(Exception):
    """A brief would lead with a state field that was not measured."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty string, not %r" % (name, value))
    return value


def _whole(value, name, low=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ValueError("%s must be an integer of at least %d, not %r" % (name, low, value))
    return value


def _check_event(event):
    """The one shape check, shared by append (before writing) and read (on every stored event)."""
    if not isinstance(event, dict):
        raise ValueError("an event is an object, not %r" % (event,))
    kind = event.get("kind")
    if kind not in KINDS:
        raise ValueError("unknown event kind %r (known: %s)" % (kind, ", ".join(sorted(KINDS))))
    _text(event.get("session"), "session")
    for field in KINDS[kind]:
        if field not in event:
            raise ValueError("a %s event needs %s" % (kind, field))
    if "pr" in KINDS[kind]:
        _whole(event["pr"], "pr")
    if "head" in KINDS[kind] and not (isinstance(event["head"], str) and _SHA.match(event["head"])):
        raise ValueError("head must be a full 40 character sha, not %r" % (event["head"],))
    if kind == "reviewed" and event["verdict"] not in VERDICTS:
        raise ValueError("verdict must be one of %s, not %r" % ("/".join(VERDICTS), event["verdict"]))
    if kind == "dispatched":
        _text(event["unit"], "unit")
        _text(event["worker"], "worker")
    if kind == "stage":
        _text(event["unit"], "unit")
        if event["stage"] not in STAGES:
            raise ValueError("stage must be one of %s, not %r" % ("/".join(STAGES), event["stage"]))
        _reading(event["at_unix"], "at_unix")
        for field in ("tokens", "defects"):
            if field in event:
                _whole(event[field], field, low=0)
        if "minutes" in event:
            _reading(event["minutes"], "minutes")
        if event["stage"] == "review":     # a review round without its number and verdict counts nothing
            _whole(event.get("round"), "round")
            if event.get("verdict") not in VERDICTS:
                raise ValueError("a review stage needs verdict %s, not %r" % ("/".join(VERDICTS), event.get("verdict")))
    if kind == "gate":
        _text(event["unit"], "unit")
        _text(event["gate"], "gate")
        if event["status"] not in VERDICTS:
            raise ValueError("gate status must be one of %s, not %r" % ("/".join(VERDICTS), event["status"]))
        _reading(event["started_unix"], "started_unix")
        _reading(event["ended_unix"], "ended_unix")
        if event["ended_unix"] < event["started_unix"]:
            raise ValueError("ended_unix %r is before started_unix %r" % (event["ended_unix"], event["started_unix"]))
        if "claimed_concurrent" in event:     # a caller's claim, kept beside the figure the report recomputes
            _whole(event["claimed_concurrent"], "claimed_concurrent")
    if kind == "handover":
        _whole(event["from_epoch"], "from_epoch")
        _whole(event["to_epoch"], "to_epoch")
        _text(event["to_session"], "to_session")
        if event["to_epoch"] != event["from_epoch"] + 1:
            raise ValueError("a handover steps the authority epoch by one (orchestrator_authority.handoff), "
                             "not %r to %r" % (event["from_epoch"], event["to_epoch"]))
    return event


def _last_handover(events):
    return next((e for e in reversed(events) if e["kind"] == "handover"), None)


def _check_handover(last, event):
    """A handover continues the last one: from the epoch it handed to, recorded by the session it handed to. Two
    coordinators handing over the same epoch, a gap and a rewind all refuse. The first handover has nothing to
    continue."""
    if event["kind"] != "handover" or last is None:
        return
    if event["from_epoch"] != last["to_epoch"] or event["session"] != last["to_session"]:
        raise ValueError("a handover continues the last one (epoch %d, held by %s), not epoch %r recorded by %r"
                         % (last["to_epoch"], last["to_session"], event["from_epoch"], event["session"]))


def read(path):
    """Every event, in order. A missing ledger is an empty one (no builders known, so nothing can be merged).
    Anything else this module did not write raises LedgerUnreadable: never read as empty or safe."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise LedgerUnreadable("%s cannot be read: %s" % (path, exc))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise LedgerUnreadable("%s is not JSON: %s" % (path, exc))
    if not isinstance(data, list):
        raise LedgerUnreadable("%s is not a list of events" % path)
    last = None
    for i, event in enumerate(data, 1):
        if (not isinstance(event, dict) or isinstance(event.get("schema"), bool) or event.get("schema") != SCHEMA
                or isinstance(event.get("seq"), bool) or event.get("seq") != i):
            raise LedgerUnreadable("%s event %d is not schema %d with seq %d" % (path, i, SCHEMA, i))
        try:
            _check_event(event)
            _check_handover(last, event)
            last = event if event["kind"] == "handover" else last
        except ValueError as exc:
            raise LedgerUnreadable("%s event %d: %s" % (path, i, exc))
    return data


def _write(path, data):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".relay-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append(path, event, *, session):
    """Record one event as `session` and return its seq. Refuses (ValueError) a malformed event before anything is
    written; raises LedgerUnreadable, writing nothing, when the existing ledger cannot be read."""
    record = dict(event)
    record["session"] = session
    if record.get("kind") == "stage" and "at_unix" not in record:
        record["at_unix"] = time.time()
    _check_event(record)
    with claim_store.Lock(path):
        events = read(path)
        _check_handover(_last_handover(events), record)
        record["schema"] = SCHEMA
        record["seq"] = len(events) + 1
        _write(path, events + [record])
    return record["seq"]


def _interval(e):
    return (e["started_unix"], e["ended_unix"])


def builders(events, pr):
    """Every session that built `pr`. A pull request with no built event is unknown provenance: KeyError."""
    found = frozenset(e["session"] for e in events if e["kind"] == "built" and e["pr"] == pr)
    if not found:
        raise KeyError("pull request %r has no built event, so who built it is unknown" % (pr,))
    return found


def may_review(events, pr, reviewer):
    """A builder never reviews its own pull request."""
    if not isinstance(reviewer, str) or not reviewer.strip():
        return Decision(False, "no reviewer named")
    try:
        built_by = builders(events, pr)
    except KeyError as exc:
        return Decision(False, str(exc.args[0]))
    if reviewer in built_by:
        return Decision(False, "%s built pull request %s, so it cannot review it" % (reviewer, pr))
    return Decision(True, "%s did not build pull request %s" % (reviewer, pr))


def may_merge(events, pr, head, actor):
    """A session never merges its own build, and only a PASS from a non-builder at exactly the latest built head
    allows the merge. An AUDIT in this slice: see the module docstring."""
    if not isinstance(actor, str) or not actor.strip():
        return Decision(False, "no merging session named")
    try:
        built_by = builders(events, pr)
    except KeyError as exc:
        return Decision(False, str(exc.args[0]))
    if actor in built_by:
        return Decision(False, "%s built pull request %s, so it cannot merge it" % (actor, pr))
    if any(e["kind"] == "merged" and e["pr"] == pr for e in events):
        return Decision(False, "pull request %s is already recorded as merged" % pr)
    latest = [e["head"] for e in events if e["kind"] == "built" and e["pr"] == pr][-1]
    if head != latest:
        return Decision(False, "head %r is not the latest built head %s of pull request %s" % (head, latest, pr))
    reviews = [e for e in events if e["kind"] == "reviewed" and e["pr"] == pr and e["head"] == head
               and e["session"] not in built_by]
    if not reviews:
        return Decision(False, "no review of pull request %s at %s by a session that did not build it" % (pr, head))
    last = reviews[-1]
    if last["verdict"] != "PASS":
        return Decision(False, "the latest review of %s at %s by %s is %s" % (pr, head, last["session"],
                                                                            last["verdict"]))
    return Decision(True, "%s reviewed pull request %s at %s PASS and did not build it" % (last["session"], pr, head))


def _measured(value):
    return value is not None and value != NODATA and not (isinstance(value, str) and not value.strip())


def brief(state, task):
    """A worker brief that leads with measured state. A missing, None, empty or NO-DATA field refuses the brief
    (BriefRefused naming the field); an empty open pull request list is a measurement ("none"), not a gap."""
    if not isinstance(state, dict):
        raise BriefRefused("the state is not an object")
    if not isinstance(task, str) or not task.strip():
        raise BriefRefused("the task is empty")
    for field in REQUIRED_STATE:
        if field not in state or not _measured(state[field]):
            raise BriefRefused("state field %s was not measured" % field)
    lines = ["STATE (measured %s by %s; re-measure before acting, it may have moved)"
             % (state["measured_utc"], state["session"])]
    for field in REQUIRED_STATE:
        value = state[field]
        if field == "open_prs":
            value = ", ".join(str(v) for v in value) if isinstance(value, (list, tuple)) and value else (
                "none" if isinstance(value, (list, tuple)) else value)
        lines.append("- %s: %s" % (field, value))
    for field in sorted(set(state) - set(REQUIRED_STATE)):
        lines.append("- %s: %s" % (field, state[field]))
    lines += ["", "TASK", task.strip()]
    return "\n".join(lines) + "\n"


def _reading(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("%s must be a finite number of at least 0, not %r" % (name, value))
    return value


def handover_due(spent, ceiling, reserve):
    """STOP at the ceiling, HANDOVER from min(0.8 x ceiling, ceiling - reserve), CONTINUE below. The reserve is the
    measured cost of writing the handover and letting in-flight workers report; it is required, never defaulted."""
    spent = _reading(spent, "spent")
    ceiling = _reading(ceiling, "ceiling")
    reserve = _reading(reserve, "reserve")
    if reserve >= ceiling:  # also refuses a ceiling of 0, since a reading is never negative
        raise ValueError("reserve %r leaves nothing below the ceiling %r" % (reserve, ceiling))
    if spent >= ceiling:
        return "STOP"
    if spent >= min(HANDOVER_FRACTION * ceiling, ceiling - reserve):
        return "HANDOVER"
    return "CONTINUE"


def gate_command(argv, env):
    """The argv and environment that run a heavy gate in line on ONE machine slot. A caller's switch-off is
    dropped (a coordinator's gate always queues); a held slot is kept, so a nested gate runs at once instead of
    waiting behind itself."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) and a for a in argv):
        raise ValueError("the gate command is empty or not a list of words")
    out = dict(env or {})
    out.pop("BROTHER_HEAVY_SLOT", None)
    out["BROTHER_HEAVY_SLOT_CHILD_SLOTS"] = out.get("LOCAL_SLOTS", "")  # the work inside sees the caller's count
    out["LOCAL_SLOTS"] = "1"
    out["BROTHER_HEAVY_SLOT_WEIGHT"] = "1"
    return [sys.executable, HEAVY_SLOT] + list(argv), out


class InputUnreadable(Exception):
    """A plan or line input the report needs cannot be read. NO-DATA, never an empty ready set."""


def load_plan(paths):
    """The plan's units, merged by id across the files in the order given: a later file's fields override an earlier
    file's for the same id, so a small overlay such as {"units": [{"id": "X1.a", "owner_hold": "why"}, {"id": "X2.b",
    "landed": "sha"}]} marks a unit parked on the owner or landed under a commit that does not name it, as DATA.
    Each file is JSON with a "units" and/or "subunits" list. A missing, unparseable or unit-less file raises
    InputUnreadable, and so does a unit that ends up with no depends_on whatever else it carries (an overlay entry
    naming an id no plan file declares, a misspelled id with owner_hold or landed, is NO-DATA: it would otherwise read
    as a ready unit with no dependencies)."""
    units = {}
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise InputUnreadable("plan %s cannot be read: %s" % (path, exc))
        keys = [k for k in ("units", "subunits") if isinstance(data, dict) and k in data]
        if not keys:
            raise InputUnreadable("plan %s has no units or subunits list" % path)
        for key in keys:
            if not isinstance(data[key], list):
                raise InputUnreadable("plan %s: %s is not a list" % (path, key))
            for u in data[key]:
                deps = u.get("depends_on", []) if isinstance(u, dict) else None
                if (not isinstance(u, dict) or not isinstance(u.get("id"), str) or not _UNIT_ID.match(u["id"])
                        or not isinstance(deps, list) or not all(isinstance(d, str) for d in deps)):
                    raise InputUnreadable("plan %s: a unit needs a text id and a depends_on list, got %r" % (path, u))
                units.setdefault(u["id"], {}).update(u)
    for u in units.values():
        if "depends_on" not in u:
            raise InputUnreadable("unit %s is declared by no plan file (an overlay naming an unknown id?)" % u["id"])
    if not units:
        raise InputUnreadable("the plan names no unit")
    return list(units.values())


_UNIT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")    # no space, newline or line separator: an id is printed
_SUBJECT = re.compile(r"^[0-9a-f]{40} (.*)$")
_PARENS = re.compile(r"\([^()]*\)")


def _one_line(text):
    """text on one line: every whitespace run, control character and line separator becomes one space, so a value that
    comes from a file (a heartbeat reason, a gate name) can never start a line of the report with a token a reader
    matches on (STALL:)."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", str(text)).split())


def landed_text(log):
    """The only text that can land a unit, from `git log --format='%H %s%n%b'`: commit SUBJECT lines (a line led by a
    full sha), with every parenthesized remark removed, innermost first. A body line (an owed or follow-up note) and
    text in brackets never land a unit: a title such as "Train H: FLOW1.b ... (follow-up FLOW1.e)" landed FLOW1.b,
    never FLOW1.e. Mirrors the reference reader's rule (subjects only, bracket text ignored)."""
    out = []
    for line in log.split("\n"):    # NOT splitlines(): a body may carry U+2028 or U+0085 then a forged "sha subject"
        m = _SUBJECT.match(line)
        if m:
            text, prev = m.group(1), None
            while prev != text:
                prev, text = text, _PARENS.sub(" ", text)
            out.append(text)
    return "\n".join(out)


def load_line(log_path, shas_path):
    """The landing text of the line's git log (landed_text). The log and the sha list must be readable and non empty,
    and the log must carry at least one commit subject: an empty line would read every unit as unlanded, so it is
    NO-DATA, never 'nothing ready'."""
    texts = []
    for what, path in (("git log", log_path), ("sha list", shas_path)):
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, ValueError) as exc:
            raise InputUnreadable("the %s %s cannot be read: %s" % (what, path, exc))
        if not text.strip():
            raise InputUnreadable("the %s %s is empty" % (what, path))
        texts.append(text)
    landed = landed_text(texts[0])
    if not landed.strip():
        raise InputUnreadable("the git log %s carries no commit subject line (sha then subject)" % log_path)
    return landed


def _named(unit_id, text):
    """unit_id appears in text as a whole token (FLOW1.b does not match FLOW1.bc or XFLOW1.b), any case."""
    return re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(unit_id), text, re.IGNORECASE) is not None


def _landed(units, log):
    return {u["id"] for u in units if u.get("landed") or _named(u["id"], log)}


def ready_set(units, log):
    """The worklist: units not landed, not held for the owner, with every dependency landed on the line. A unit is
    landed when the line's log names it or the plan data says landed. Read from the PLAN, never from sessions in
    flight."""
    landed = _landed(units, log)
    return [u["id"] for u in units if u["id"] not in landed and not u.get("owner_hold") and not u.get("deferred")
            and set(u.get("depends_on", [])) <= landed]


def owner_set(units, log):
    """Units that wait on the owner by plan data (owner_hold) and are not landed: reported on their own OWNER line,
    never ready, never in the wake list, and never what makes the worklist empty."""
    landed = _landed(units, log)
    return [u["id"] for u in units if u["id"] not in landed and u.get("owner_hold")]


def deferred_set(units, log):
    """Units the plan defers to a later release (a `deferred` reason): not ready, reported on their own line."""
    landed = _landed(units, log)
    return [u["id"] for u in units if u["id"] not in landed and u.get("deferred")]


def driver_running(path, now):
    """(running, line): the driver is running only when loop_heartbeat, the one rule, reads PRODUCING or QUIET from
    the heartbeat the driver writes at `path` AND the record is not an ended state. Missing, torn, stale (25 minutes),
    stamped more than 2 minutes ahead of the clock, STARTED too long, or ended (even a normal end) is not running. A
    file that was merely touched carries no stamp and reads ALARM."""
    rec = loop_heartbeat.read(path)
    ref = datetime.datetime.fromtimestamp(now)       # the heartbeat stamps local naive time, so judge it the same way
    state, why = loop_heartbeat.classify(rec, ref=ref)
    ended = bool(rec) and rec.get("state") in loop_heartbeat.TERMINAL
    return state in ("PRODUCING", "QUIET") and not ended, "%s: %s" % ("ENDED" if ended else state, why)


def in_flight(events):
    """Units dispatched and never merged, with those whose latest stage is an owner wait (information only: the
    worklist never comes from here)."""
    merged = {e["unit"] for e in events if e["kind"] == "stage" and e["stage"] == "merge"}
    units = []
    for e in events:
        if e["kind"] == "dispatched" and e["unit"] not in merged and e["unit"] not in units:
            units.append(e["unit"])
    last = {}
    for e in events:
        if e["kind"] == "stage":
            last[e["unit"]] = e["stage"]
    return units, [u for u in units if last.get(u) == "owner_wait"]


def _sum(events, field):
    """The total of a number a stage left. Every event carrying it: the total. None: NO-DATA. Some: NO-DATA with the
    partial figure labeled, never a bare total that reads as the stage's whole cost."""
    values = [e[field] for e in events if field in e]
    if len(values) == len(events) and values:
        return "%g" % sum(values)
    if not values:
        return NODATA
    return "%s (%d of %d events reported, %g so far)" % (NODATA, len(values), len(events), sum(values))


def build_report(events, now, window_hours):
    """(lines, merged) over the window ending at `now`: every stage counted with its tokens, minutes and defects
    (NO-DATA where no event carried one), every gate PASS and FAIL alike with the peak ran at once, gates per merged
    unit and merged units per hour. `merged` is the set of units that merged inside the window."""
    low = now - window_hours * 3600.0
    high = now + window_hours * 3600.0   # a stamp up to one window ahead of `now` is clock skew, counted and noted
    stages = [e for e in events if e["kind"] == "stage" and low <= e["at_unix"] <= high]
    gates = [e for e in events if e["kind"] == "gate" and low <= e["ended_unix"] <= high]  # ONE window for both counts
    merged = {e["unit"] for e in stages if e["stage"] == "merge"}
    lines = ["REPORT window %gh ending %g" % (window_hours, now)]
    ahead = sum(e["at_unix"] > now for e in stages) + sum(e["ended_unix"] > now for e in gates)
    if ahead:
        lines.append("note: %d event(s) are stamped after now (clock skew of up to one window is counted)" % ahead)
    for name in STAGES:
        evs = [e for e in stages if e["stage"] == name]
        line = "stage %s: %d events, tokens %s, minutes %s, defects %s" % (
            name, len(evs), _sum(evs, "tokens"), _sum(evs, "minutes"), _sum(evs, "defects"))
        if name == "review":
            line += ", verdicts PASS %d FAIL %d" % tuple(sum(e["verdict"] == v for e in evs) for v in VERDICTS)
        lines.append(line)
    lines.append("gates: %d ran (PASS %d, FAIL %d), peak at once %d" % (
        len(gates), sum(e["status"] == "PASS" for e in gates), sum(e["status"] == "FAIL" for e in gates),
        gate_ledger.peak_concurrency([_interval(e) for e in gates])))
    for e, n in zip(gates, gate_ledger.concurrency_by_gate([_interval(e) for e in gates])):
        lines.append("gate %s %s %s at once %d" % (_one_line(e["unit"]), _one_line(e["gate"]), e["status"], n))
    per = [e for e in gates if e["unit"] in merged]
    lines.append("merged units: %d" % len(merged))
    lines.append("gates per merged unit: %s" % (
        "%.2f" % (len(per) / len(merged)) if merged else "%s (no unit merged in the window)" % NODATA))
    lines.append("merged units per hour: %.2f" % (len(merged) / window_hours))
    flying, parked = in_flight(events)
    lines.append("in flight: %d (parked on the owner: %d)" % (len(flying), len(parked)))
    return lines, merged


def stalled(ready, merged, driving):
    """The STALL rule: work is ready, nothing merged inside the window, and nothing is driving the train."""
    return bool(ready) and not merged and not driving


def _field(text):
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise ValueError("a field is key=value, not %r" % (text,))
    if key in ("pr", "from_epoch", "to_epoch", "tokens", "defects", "round", "claimed_concurrent") and re.match(
            r"^[0-9]+$", value):
        return key, int(value)
    if key in ("at_unix", "minutes", "started_unix", "ended_unix"):
        try:
            return key, float(value)
        except ValueError:
            pass                           # left as text, so _check_event refuses it by name
    return key, value


def main(argv=None):
    p = argparse.ArgumentParser(prog="session_relay.py")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("record")
    r.add_argument("--ledger", required=True)
    r.add_argument("--session", required=True)
    r.add_argument("--kind", required=True)
    r.add_argument("--field", action="append", default=[])
    v = sub.add_parser("may-review")
    v.add_argument("--ledger", required=True)
    v.add_argument("--pr", type=int, required=True)
    v.add_argument("--reviewer", required=True)
    m = sub.add_parser("may-merge")
    m.add_argument("--ledger", required=True)
    m.add_argument("--pr", type=int, required=True)
    m.add_argument("--head", required=True)
    m.add_argument("--actor", required=True)
    h = sub.add_parser("handover")
    h.add_argument("--spent", type=float, required=True)
    h.add_argument("--ceiling", type=float, required=True)
    h.add_argument("--reserve", type=float, required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--ledger", required=True)
    rp.add_argument("--window-hours", type=float, required=True)
    rp.add_argument("--now", type=float, default=None)
    rp.add_argument("--plan", action="append", required=True)
    rp.add_argument("--log", required=True)
    rp.add_argument("--shas", required=True)
    rp.add_argument("--driver-heartbeat", required=True,
                    help="the heartbeat the driver writes (loop_heartbeat.py write); read by loop_heartbeat's own rule")
    g = sub.add_parser("gate")
    g.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    try:
        if a.cmd == "record":
            event = dict(_field(f) for f in a.field)
            event["kind"] = a.kind
            print("recorded seq %d" % append(a.ledger, event, session=a.session))
            return 0
        if a.cmd in ("may-review", "may-merge"):
            events = read(a.ledger)
            d = (may_review(events, a.pr, a.reviewer) if a.cmd == "may-review"
                 else may_merge(events, a.pr, a.head, a.actor))
            print("%s: %s" % ("ALLOW" if d.ok else "REFUSE", d.reason))
            return 0 if d.ok else 1
        if a.cmd == "report":
            if not math.isfinite(a.window_hours) or a.window_hours <= 0:
                raise ValueError("--window-hours must be a finite number above 0, not %r" % a.window_hours)
            now = time.time() if a.now is None else a.now
            if not math.isfinite(now) or now < 0:
                raise ValueError("--now must be a finite number of at least 0, not %r" % now)
            if not os.path.exists(a.ledger):     # read() calls a missing ledger empty; a mistyped path must not
                raise LedgerUnreadable("the ledger %s does not exist, so nothing can be reported" % a.ledger)
            lines, merged = build_report(read(a.ledger), now, a.window_hours)
            print("\n".join(lines))
            try:
                units = load_plan(a.plan)
                log = load_line(a.log, a.shas)
                ready, owner, deferred = ready_set(units, log), owner_set(units, log), deferred_set(units, log)
            except InputUnreadable as exc:
                print("%s: the ready set cannot be read, so stall is unknown: %s" % (NODATA, exc), file=sys.stderr)
                return 2
            print("ready: %s" % (" ".join(ready) or "none"))
            print("owner: %s" % (" ".join(owner) or "none"))
            print("deferred: %s" % (" ".join(deferred) or "none"))
            running, driver_line = driver_running(a.driver_heartbeat, now)
            print("driver: %s %s" % ("RUNNING" if running else "NOT RUNNING", _one_line(driver_line)))
            if stalled(ready, merged, running):
                print("STALL: ready units %s; no unit merged in the last %gh; the driver is not running. "
                      "WAKE the train driver." % (" ".join(ready), a.window_hours))
                return STALL_EXIT
            print("OK: no stall")
            return 0
        if a.cmd == "handover":
            print(handover_due(a.spent, a.ceiling, a.reserve))
            return 0
        if a.cmd == "gate":
            cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
            gate_argv, gate_env = gate_command(cmd, os.environ)
            return subprocess.call(gate_argv, env=gate_env)
    except LedgerUnreadable as exc:
        print("%s: %s" % (NODATA, exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("%s: %s" % (NODATA if a.cmd in ("handover", "report") else "REFUSE", exc), file=sys.stderr)
        return 2 if a.cmd in ("handover", "report") else 1
    p.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
