"""The one reader of the loop's stop controls (review item 1, 2026-09-26).

Two files stop spending. ~/.claude/evidence/LOOP-PAUSE.txt is a pause (stop_loop.sh --pause writes it, --resume removes
it). ~/.claude/evidence/LOOP-HOLD.txt is the owner's HOLD (removed only on the owner's word). Until this module, only the
driver read the pause, before a new pass: a runner already seated kept buying rounds, and at 07:04 on 2026-09-26 a paused
loop still had a probe wave with two paid calls in flight. Every route that buys model calls now asks here first.

A control file that exists but cannot be read (a directory in its place, a permission error) HOLDS: a stop that cannot
be read is still a stop, never a go. Nothing here imports a model seam, so a caller can ask before loading its own.
EVERY HOLD OR PAUSE SEEN IS RECORDED (U11, B5-03, objection 14). A run the owner paused must never read as unattended,
and until 2026-09-27 nothing wrote down that a control was seen. When reason() returns a reason and this run's proof
history exists ($BROTHER_RUN_DIR/proof/events.jsonl), it appends one hold-observed row (kind, detail, where, pid,
observed_at). It never creates a missing history, and nothing is appended once the end snapshot was taken (the history
ends with its end marker, or proof/evidence.json exists): acceptance hashes those bytes. An append that fails creates
$BROTHER_RUN_DIR/proof-events-failed (a second file in a second directory, read as NO-DATA) and writes one stderr line.
Recording never changes the answer: a control that holds still holds.
usage: reason(ev_dir=None, where="") -> None, or the one line that says why the loop is held
       gate(ev_dir=None, where="") -> returns when not held; prints "HELD ..." and exits 5 when held
       python3 loop_hold.py [WHERE]  -> prints the reason or "not held"; exit 5 when held, 0 when not
"""
import datetime, json, os, sys

HELD_EXIT = 5
FILES = (("LOOP-HOLD.txt", "HOLD"), ("LOOP-PAUSE.txt", "PAUSE"))


def _record(why, where):
    """Append one hold-observed row to this run's proof history. See the module docstring for the refusals."""
    run = os.environ.get("BROTHER_RUN_DIR")
    if not run:
        return
    proof = os.path.join(run, "proof")
    events = os.path.join(proof, "events.jsonl")
    if os.path.lexists(os.path.join(proof, "evidence.json")):
        return
    row = {"kind": "hold-observed", "detail": why, "where": where or "unknown", "pid": os.getpid(),
           "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        try:
            fh = open(events, "r+", encoding="utf-8")                # r+: a missing history is never created here
        except FileNotFoundError:
            return                                                   # no proof history (not a proof run, or not started)
        with fh:
            last = [l for l in fh.read().splitlines() if l.strip()][-1:]
            if last and json.loads(last[0]).get("kind") == "end-marker":
                return
            fh.seek(0, 2)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except (OSError, ValueError, AttributeError) as exc:
        try:
            with open(os.path.join(run, "proof-events-failed"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(row, error=type(exc).__name__)) + "\n")
        except OSError:  # sbe: allow-silent both records failed (the residual edge design section 6 names); the stderr line below still says so
            pass
        sys.stderr.write("NO-DATA: a %s was seen and could not be recorded in %s (%s); proof-events-failed marks the run\n"
                         % (why.split(":")[0], events, type(exc).__name__))


def reason(ev_dir=None, where=""):
    why = _reason(ev_dir)
    if why is not None:
        _record(why, where)
    return why


def _reason(ev_dir=None):
    ev = ev_dir or os.path.expanduser("~/.claude/evidence")
    # A folder path that exists as an entry but is not a searchable folder (a link to nothing) would make every control
    # file below read as "not found", so a corrupt control root would read as not held (DeepSeek adversary 2026-09-26,
    # or-lanes/b4f-phold-ds2.md F1). A path with no entry at all keeps its pinned meaning (no folder, no control file).
    if os.path.lexists(ev) and not os.path.isdir(ev):
        return "CONTROL: %s exists but is not a searchable folder (a dangling link or not a folder), so it holds" % ev
    for name, word in FILES:
        path = os.path.join(ev, name)
        # ONLY "not found" is absent. os.path.lexists() also answers False when the folder cannot be searched, and a
        # stop that cannot be looked at is still a stop (port attack 2026-09-26, port-inventory/port-muse.md item 2).
        try:
            os.lstat(path)
        except FileNotFoundError:  # sbe: allow-silent a control file that is not found is absent by definition; every other error holds below
            continue
        except OSError as exc:
            return "%s: %s cannot be looked at (%s), so it holds" % (word, path, type(exc).__name__)
        try:
            with open(path, "rb") as f:
                first = f.read(400).decode("utf-8", "replace").splitlines()
        except OSError as exc:
            return "%s: %s exists and cannot be read (%s), so it holds" % (word, path, type(exc).__name__)
        return "%s: %s" % (word, (first[0].strip() if first else "(empty file)")[:200])
    return None


def gate(ev_dir=None, where=""):
    why = reason(ev_dir, where)
    if why is None:
        return
    print("HELD%s: %s; nothing new is bought, outputs already on disk are kept" % ((" at " + where) if where else "", why), flush=True)
    sys.exit(HELD_EXIT)


if __name__ == "__main__":
    why = reason(where=sys.argv[1] if len(sys.argv) > 1 else "loop_hold.py")
    print(why or "not held")
    sys.exit(HELD_EXIT if why else 0)
