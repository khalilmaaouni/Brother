#!/usr/bin/env python3
"""journal: one append-only causal log per run, fed by the writers that
already exist, changing none of them.

WHY THIS EXISTS. The navigator inventory of 2026-09-03 measured the state
this engine keeps and found three id families that never reference each
other, no parent on any event, a run log rewritten whole on every line
(brother_run.RunLog._flush), eight writers rewriting the Work document in
place, and therefore no projection that can be rebuilt from a log, because
there is no log. The founder ruled route A the same evening
(docs/decisions/canonical-state-journal-first-2026-09-03.json): one journal
per run first, the projections proven against it second, absorption into
the product store third.

WHAT IT IS, AND WHAT IT DELIBERATELY IS NOT. It is a record of transactions
that already happened, written BESIDE the writer's own file, never instead
of it: the claim store still owns claims.json, the Work document is still
the scheduler's truth, the run log still carries the engine's narration.
Delete <run_dir>/journal.jsonl and every existing reader still works, which
is the reversibility criterion the ruling was scored on. Nothing in this
estate reads the journal as authority yet, and nothing may until a
projection rebuilt from it is diffed against the record it claims to
reproduce.

THE ATOMIC APPEND, copied from products/brothermode/tools/bm_vault_audit.py
rather than invented here: os.open with O_APPEND|O_CREAT and one os.write of
a whole line. POSIX makes an O_APPEND write of at most PIPE_BUF bytes atomic
against other appenders, so two writers in the same run (the lanes of one
round, running in threads) interleave whole lines and never half of one.
That bound is the reason _line() below truncates a fat payload instead of
letting the line grow: a line that cannot be atomic is the one thing this
file exists to prevent. PIPE_BUF is 512 bytes on some platforms this estate
runs on, and the identity fields spend about half of it, so a caller keeps
its payload to counts, ids, exit codes and short reasons; anything longer
already has a home (the run log, the attempt trace, the claim's evidence)
and the journal points at it rather than copying it.

AVAILABILITY OVER BOOKKEEPING, the same stance bm_vault_audit.append takes
and the same one RunLog._flush takes: an unwritable directory, a full disk
or a payload that will not serialize must never fail the run being
recorded. Every failure path here prints one line naming what it lost and
returns None; a caller never has to wrap a call to this module, and none of
the twenty call sites does.

NO RUN DIRECTORY IS NOT A FAILURE. append("") writes nothing and returns
None, because several of the modules calling in here are also used outside
a run (test_integrate drives integrate_one directly, acceptance_6 and
acceptance_7 drive it against a throwaway repository). A library with no run
around it has no journal to write to, and inventing one somewhere would put
a stray file in somebody's tree.

PARENTS. `parent_ids` is a list, always present, empty for a root. A caller
that holds the real causal predecessor's event id passes it; a caller that
does not passes previous(run_dir), the last event THIS PROCESS wrote for
this run, which is a run-level ordering edge rather than a semantic one and
is documented as such on previous() itself.

Python 3, standard library only. No network.

PRODUCER: this module is the sole producer of <run_dir>/journal.jsonl. Every
record is written by append() (around line 150), whose actual write is the
os.write(fd, line) inside it; nothing else in this estate opens that file
for writing.
"""
import datetime
import json
import os
import select
import sys
import uuid

NODATA = "NO-DATA"

#: One file per run, beside the run's Work document and its claims.
JOURNAL_FILENAME = "journal.jsonl"

#: How a module with no run directory in its own hands finds one:
#: brother_run.main() exports this once the run directory is settled, the
#: same way it already exports integrate.RUN_ID_ENV_VAR and
#: integrate.HARNESS_ENV_VAR for the merge trailers. Read at call time, never
#: cached, so a second run in the same process reads its own value.
RUN_DIR_ENV_VAR = "BROTHER_RUN_DIR"

#: The atomicity bound. POSIX guarantees an O_APPEND write of at most
#: PIPE_BUF bytes is not interleaved with another appender's; the getattr
#: fallback is the POSIX minimum, used only if a platform's select module
#: does not publish the constant.
MAX_LINE_BYTES = getattr(select, "PIPE_BUF", 512)

#: In-process memory of the last event id written per run directory, read by
#: previous(). Deliberately NOT read from the file: a parent must be an event
#: this process actually wrote, and re-reading the tail would happily hand a
#: caller a concurrent writer's event as its parent.
_LAST = {}


def _key(run_dir):
    return os.path.abspath(str(run_dir))


def previous(run_dir):
    """(event_id,) of the last event this PROCESS appended for this run, or
    () when it has appended none.

    THE RUN-LEVEL PREDECESSOR, NOT A SEMANTIC PARENT, and the difference
    matters to anyone reading a chain: several of the twenty call sites (a
    claim being acquired, a lane being cleaned, a screen being rendered) do
    not hold the event that caused them, and this estate's rule is to state
    what a record actually is rather than to dress an ordering edge up as
    causality. A site that DOES hold its cause passes that event id instead
    and says so at the call.

    ponytail: process-local and unsynchronized, which is what makes it a
    plain dict rather than a lock. Two lanes of one round appending
    concurrently will each parent onto whichever event landed last, so a
    round's edges are an ordering, not a tree. Per-thread parentage is the
    upgrade if a reader ever needs the tree.
    """
    last = _LAST.get(_key(run_dir))
    return (last,) if last else ()


def run_dir_from_env(env=None):
    """The run directory brother_run exported, or "" when this code is not
    running inside a run at all (a library under its own test, an acceptance
    harness driving one function). "" is what append() reads as "nothing to
    journal", never as an error."""
    env = os.environ if env is None else env
    return (env.get(RUN_DIR_ENV_VAR) or "").strip()


def _line(event):
    """`event` as one newline-terminated UTF-8 line, kept under
    MAX_LINE_BYTES by shrinking its PAYLOAD when it does not fit.

    The payload is the only field allowed to be big and the only one dropped:
    the identity fields (ids, run, unit, time, type) are what a chain is
    rebuilt from, so a line keeps them whatever it costs. The dropped part is
    named in place (never silently gone) and a head of it is kept when any
    room is left, halved until it fits, because escaping means the encoded
    length cannot be computed from the raw length. A line that is still too
    long with no payload at all (a pathological unit id) is written ANYWAY:
    losing the event would be worse than losing its atomicity, and this
    returns it rather than pretending the event never happened."""
    line = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    if len(line) <= MAX_LINE_BYTES:
        return line
    body = json.dumps(event.get("payload"), sort_keys=True)
    reason = ("%s: the payload was %d characters, over the %d byte line "
              "bound that keeps an append atomic, so it was truncated here"
              % (NODATA, len(body), MAX_LINE_BYTES))
    head = body
    while head:
        candidate = (json.dumps(dict(event, payload={
            "payload_truncated": reason, "payload_head": head}),
            sort_keys=True) + "\n").encode("utf-8")
        if len(candidate) <= MAX_LINE_BYTES:
            return candidate
        head = head[:len(head) // 2]
    return (json.dumps(dict(event, payload={"payload_truncated": reason}),
                       sort_keys=True) + "\n").encode("utf-8")


def append(run_dir, type, parent_ids=(), unit_id=None, session_id=None,
           payload=None):
    """One event on this run's journal. Returns its event_id, or None when
    nothing was written (no run directory, or a write that failed and said
    so). Never raises: see the module docstring's availability rule.

    The eight fields every event carries, so a reader never has to ask which
    shape it is holding: event_id (uuid4 hex, this event's own name),
    parent_ids (a list, empty for a root), run_id (the run directory's own
    basename, the id the merge trailers and the run log already use),
    session_id (null when nobody recorded one), unit_id (null when the event
    is about the run rather than one unit), at (ISO 8601, UTC, with its
    offset), type, payload."""
    run_dir = str(run_dir or "").strip()
    if not run_dir:
        return None
    event_id = uuid.uuid4().hex
    event = {
        "event_id": event_id,
        # A None parent DROPS OUT rather than becoming the string "None":
        # every site passes whatever append() handed it last, and an append
        # that failed (an unwritable run directory, one stderr line) returns
        # None. A chain with a missing link says so by being a root, never
        # by naming a parent that does not exist.
        "parent_ids": [str(p) for p in (parent_ids or ()) if p],
        "run_id": os.path.basename(os.path.normpath(run_dir)),
        "session_id": session_id,
        "unit_id": None if unit_id is None else str(unit_id),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "type": str(type),
        "payload": payload if payload is not None else {},
    }
    try:
        line = _line(event)
        fd = os.open(os.path.join(run_dir, JOURNAL_FILENAME),
                     os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError) as exc:
        # ONE LINE, THEN THE RUN GOES ON. A boundary failure named at the
        # boundary: the run being recorded must not die because its own
        # record could not be written, and the event that was lost is named
        # so a gap in a chain can be explained rather than guessed at.
        sys.stderr.write("journal: could not append a %s event to %s (%s); "
                         "the run continues without it\n"
                         % (event["type"], run_dir, exc))
        return None
    _LAST[_key(run_dir)] = event_id
    return event_id


def read(run_dir):
    """Every event this run's journal holds, in file order, or None when
    there is no journal at all.

    NONE IS NO-DATA, [] IS A MEASURED EMPTY, and this estate does not fold
    the two: a missing run directory or a run that never appended anything
    is "nothing was recorded", while an existing file with no readable line
    is "recorded, and it holds nothing". A caller that treats None as an
    empty list has turned an unmeasured run into a clean one.

    A TORN LINE IS SKIPPED, NEVER FATAL. A crash mid-append (or a line that
    exceeded the atomicity bound and interleaved) leaves text that is not
    JSON; it is named on stderr with its line number and dropped, exactly as
    bm_vault_audit._read_rows already does for the same failure.

    ponytail: reads the whole file into memory. One run's journal is a few
    dozen lines; stream it if a run ever writes enough to notice."""
    path = os.path.join(str(run_dir or ""), JOURNAL_FILENAME)
    if not os.path.isfile(path):
        return None
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError as exc:
        sys.stderr.write("journal: could not read %s (%s)\n" % (path, exc))
        return None
    for number, text in enumerate(lines, 1):
        text = text.strip()
        if not text:
            continue
        try:
            rows.append(json.loads(text))
        except ValueError as exc:
            sys.stderr.write("journal: skipping torn line %d of %s (%s)\n"
                             % (number, path, exc))
    return rows
