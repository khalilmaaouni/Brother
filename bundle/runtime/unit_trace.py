"""unit_trace: one line per dispatched unit, keyed by the claim that ran it.

THE GAP THIS EXISTS FOR, in the founder's own ruling. On 2026-09-10 at about
21:50 JST he chose "A: Observe first (Recommended)" in the question UI, after
three independent readers scored the same gap first: Brother can say a row is
DONE and Brother can say what a session spent, and there is no join between
them. Three readers ranked it first or second and none of them could compute
the one number that would make the trade visible, which is what a delivered row
cost. The deck's closing slide put the order as observe, evaluate, optimise, and
this row is the observe step.

WHAT MAKES THIS A TRACE RATHER THAN A LOG. A log is written for a human to read
later and can therefore omit whatever the writer found uninteresting. This is
written to be JOINED: the key is the claim id that scripts/claim_store.py already
mints for every dispatched unit, so a line can be matched to the unit that
produced it and to the row that unit delivered, rather than to whatever ran most
recently. A record with no claim id cannot be joined to anything, so it is
REFUSED rather than stored under a blank key.

NO-DATA IS A VALUE, NEVER A ZERO. A host that cannot report a token count has
not reported zero tokens; it has reported nothing, and the difference matters,
because a zero summed into an average is a lie with a well formed shape. Every
field this module cannot be given is stored as the string "NO-DATA". A caller
that genuinely passes 0 keeps its 0, which is why the sentinel is None and not
falsiness.

ONE WRITE, SO A CONCURRENT WRITER CANNOT LOSE A LINE. The append opens with
O_APPEND and issues exactly one os.write of a complete line. POSIX makes that
combination atomic with respect to the file offset, so two processes appending at
once interleave whole lines rather than overwriting each other's bytes. This is
the same reasoning scripts/claim_store.py uses when it reaches for O_CREAT|O_EXCL
instead of a threading lock, and for the same reason: the failure being prevented
is two SESSIONS, not two threads.

BORROWED, each doing real work rather than decorating this docstring:

  STRUCTURED LOGGING, one record per event, machine first and human second. The
  writer never decides what a reader will want, because it cannot know.

  THE JOIN KEY, from relational design. An event stream without a stable foreign
  key is a pile of text; the claim id is what turns it into a table.

  TRIPLE-STATE LOGIC, from SQL. TRUE, FALSE and UNKNOWN are three states, and
  collapsing UNKNOWN into FALSE is the classic bug. Tokens known, tokens zero and
  tokens unknown are likewise three states, and only one of them is "NO-DATA".

Python 3, standard library only. No network.

PRODUCER: this module is the sole producer of its own record. The write happens
inside _append() only, reached from record(). Nothing else in the tree writes a
trace line, so a second writer appearing here would be a defect rather than a
feature.
"""
import json
import os
import socket
import sys
import time

#: The string every field this host could not supply carries. Never a zero and
#: never an empty string: an empty string reads as a formatting accident, while
#: NO-DATA reads as the measurement it is.
NODATA = "NO-DATA"

#: The eight fields a trace line carries, in the order the board's done-check
#: names them. Kept as data so a test can assert the whole set rather than
#: trusting a hand written string.
FIELDS = ("claim_id", "tier", "effort", "tokens_in", "tokens_out",
          "cache_read", "wall_ms", "verdict")

#: Where the trace lands by default. Overridable by argument or by the
#: environment, because a test must never write into the real trace and a
#: packager must never hardcode one machine's home directory.
DEFAULT_STORE = os.path.join(os.path.expanduser("~"), ".claude",
                             "unit-trace.jsonl")


class UnitTraceError(Exception):
    """A record that cannot be joined is not stored."""


def _store(path=None):
    if path:
        return path
    from_env = os.environ.get("BROTHER_UNIT_TRACE")
    return from_env or DEFAULT_STORE


def _value(v):
    """None and the empty string both mean the host supplied nothing."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return NODATA
    return v


def _append(row, path):
    """One O_APPEND fd, one write, one complete line."""
    p = os.path.abspath(path)
    d = os.path.dirname(p) or "."
    os.makedirs(d, exist_ok=True)
    line = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def record(claim_id, tier=None, effort=None, tokens_in=None, tokens_out=None,
           cache_read=None, wall_ms=None, verdict=None, host=None, path=None,
           clock=None):
    """Append exactly one line. (row, problem); problem is "" on success.

    A claim id is not optional and is not defaulted. A unit nobody claimed is a
    unit this estate's own law says must not start, so there is no legitimate
    trace line for one, and inventing a placeholder key here would create exactly
    the unjoinable row this module exists to prevent."""
    if not isinstance(claim_id, str) or not claim_id.strip():
        return None, ("a trace line without a claim_id cannot be joined to the "
                      "unit that produced it, so it is refused rather than "
                      "stored under a blank key")
    row = {
        "claim_id": claim_id.strip(),
        "tier": _value(tier),
        "effort": _value(effort),
        "tokens_in": _value(tokens_in),
        "tokens_out": _value(tokens_out),
        "cache_read": _value(cache_read),
        "wall_ms": _value(wall_ms),
        "verdict": _value(verdict),
        "host": _value(host) if host is not None else socket.gethostname(),
        "at": _value(int(clock() if clock else time.time())),
    }
    try:
        _append(row, _store(path))
    except OSError as exc:
        return None, "the trace line could not be written: %s" % exc
    return row, ""


def read_all(path=None):
    """(rows, problem). A malformed line is reported, never silently dropped.

    A trace is only worth having if a reader can trust that what it does not
    show is absent rather than unparseable, so this returns the problem instead
    of skipping."""
    p = _store(path)
    if not os.path.exists(p):
        return [], ""
    rows = []
    try:
        with open(p, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    return None, "line %d of %s is not JSON: %s" % (n, p, exc)
    except OSError as exc:
        return None, "the trace could not be read: %s" % exc
    return rows, ""


def _main(argv):
    if len(argv) < 2 or argv[1] not in ("record", "count"):
        sys.stderr.write("usage: unit_trace.py record --claim-id ID | count\n")
        return 2
    if argv[1] == "count":
        rows, problem = read_all()
        if problem:
            sys.stderr.write("unit_trace: %s\n" % problem)
            return 1
        print(len(rows))
        return 0
    opts = {}
    i = 2
    while i < len(argv):
        if not argv[i].startswith("--") or i + 1 >= len(argv):
            sys.stderr.write("unit_trace: expected --key value, got %r\n"
                             % argv[i])
            return 2
        opts[argv[i][2:].replace("-", "_")] = argv[i + 1]
        i += 2
    row, problem = record(**opts)
    if problem:
        sys.stderr.write("unit_trace: %s\n" % problem)
        return 1
    print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
