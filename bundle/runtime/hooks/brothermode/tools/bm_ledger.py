#!/usr/bin/env python3
"""The loop estimate ledger: declare before you work, grade after you finish.

Founder directive 2026-07-31: every planned loop states how long it will take
and how many tokens it will spend, BEFORE execution, and the close records
what actually happened and why, so calibration is a measured fact the weekly
review can grade rather than a feeling. This file is that mechanism.

Three laws, each enforced here rather than remembered:

1. DECLARE BEFORE WORK. A close against a loop nobody declared is refused:
   an estimate written after the fact is a prediction of the past.
2. HISTORY IS APPEND-ONLY. A declared estimate is never edited. Changing your
   mind mid-run is legal and visible: `amend` appends a new row and the close
   grades against the LATEST declaration, with every prior one still on disk.
3. NO GUESSED ACTUALS. Closing requires measured minutes and a why sentence.
   Actual tokens are optional because they are often not measurable from
   where the orchestrator sits: absent means the token variance reads NO-DATA,
   never a number somebody invented, and a supplied count carries its basis
   (measured or estimated) in the row.

Verdicts per dimension: "better" when the actual is under the estimate by
more than the 25 percent band, "on" inside the band, "worse" beyond it, and
"NO-DATA" when the actual was not supplied. The overall verdict is the worst
of the graded dimensions. The band is a named constant, not a vibe.

Storage: <vault>/99-System/telemetry/estimates.jsonl, one JSON object per
line, the same home and shape discipline as the other ledgers
(tools/bm_telemetry.py). Appends are single short lines via O_APPEND.

Exit codes: 0 the command did its job, 1 a law refused it, 2 usage.
"""
import io
import json
import os
import sys
import time

VAULT = os.environ.get("BROTHERMODE_VAULT", os.path.expanduser("~/BrotherModeVault"))
BAND = 0.25  # within 25 percent of the estimate counts as "on"

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 1, 2


def ledger_path():
    return os.path.join(VAULT, "99-System", "telemetry", "estimates.jsonl")


def _iso(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch or time.time()))


def _read_rows():
    """Every row, in file order. An unparseable line is surfaced, never
    skipped: a ledger with an unreadable row is a ledger somebody edited."""
    path = ledger_path()
    if not os.path.exists(path):
        return []
    rows = []
    with io.open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                raise SystemExit("bm_ledger: %s line %d does not parse as JSON. This "
                                 "ledger is append-only history; repair it by hand and "
                                 "say why in the commit, never by this tool guessing."
                                 % (path, n))
    return rows


def _redactor():
    """bm_store.redact_text, or a refusal that says why it could not be found.

    ADDED 2026-07-31 when this file was landed from the live install into the
    public repository, because the repository's own pre-write gate refused it:
    "bm_ledger.py writes files but is not in the reviewed inventory. Review
    whether every text it writes passes through redaction." The review found
    that it did not. Every one of the ten sibling tools already in that
    inventory routes founder-supplied text through `redact_text`, and this one
    stores a founder-written `why` sentence and a loop name, which is exactly
    the shape a secret gets pasted into ("slow because the key was wrong").

    Imported lazily and by path rather than at module scope, so this stays a
    standalone script with no import-time dependency on the store, which is how
    the rest of this file is written.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import bm_store
    except Exception as exc:
        raise SystemExit(
            "bm_ledger: cannot import bm_store for redaction (%s). Refusing to "
            "write founder text unscrubbed; this ledger holds a why sentence "
            "somebody may have pasted a secret into." % exc)
    return bm_store.redact_text


def _scrub_row(row):
    """Scrub every founder-supplied string in the row, leaving structure alone.

    Numbers, verdicts, timestamps and the run and loop identifiers this tool
    generates are not founder prose. The free-text fields are, so they are the
    ones that pass through the scrubber."""
    redact = _redactor()
    out = dict(row)
    for field in ("loop", "why", "note", "basis"):
        if isinstance(out.get(field), str) and out[field]:
            out[field] = redact(out[field])
    return out


def _append(row):
    path = ledger_path()
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    row = _scrub_row(row)
    blob = (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = os.write(fd, blob)
    finally:
        os.close(fd)
    if written != len(blob):
        raise SystemExit("bm_ledger: short write to %s (%d of %d bytes); the row may be "
                         "torn, inspect the tail before appending again"
                         % (path, written, len(blob)))


def _latest_declaration(rows, run, loop):
    decl = None
    for row in rows:
        if row.get("run") == run and row.get("loop") == loop:
            if row.get("kind") in ("declare", "amend"):
                decl = row
    return decl


def _already_closed(rows, run, loop):
    return any(row.get("run") == run and row.get("loop") == loop
               and row.get("kind") == "close" for row in rows)


def _grade(actual, estimate):
    if actual is None:
        return "NO-DATA"
    if estimate <= 0:
        return "NO-DATA"
    delta = (actual - estimate) / float(estimate)
    if delta < -BAND:
        return "better"
    if delta > BAND:
        return "worse"
    return "on"


_RANK = {"worse": 3, "on": 2, "better": 1, "NO-DATA": 0}


def _overall(time_verdict, token_verdict):
    graded = [v for v in (time_verdict, token_verdict) if v != "NO-DATA"]
    if not graded:
        return "NO-DATA"
    return max(graded, key=lambda v: _RANK[v])


def cmd_declare(args, kind="declare"):
    rows = _read_rows()
    if _already_closed(rows, args["run"], args["loop"]):
        print("bm_ledger: refused. %s/%s is already closed; a closed loop's history "
              "is settled. Declare a new loop id instead." % (args["run"], args["loop"]))
        return EXIT_REFUSED
    existing = _latest_declaration(rows, args["run"], args["loop"])
    if existing and kind == "declare":
        print("bm_ledger: refused. %s/%s already carries a declaration (%s at %s). "
              "Changing your mind is legal and visible: use `amend`, which keeps "
              "the old row on disk." % (args["run"], args["loop"],
                                        existing["kind"], existing["ts"]))
        return EXIT_REFUSED
    if not existing and kind == "amend":
        print("bm_ledger: refused. Nothing to amend: %s/%s was never declared."
              % (args["run"], args["loop"]))
        return EXIT_REFUSED
    row = {"kind": kind, "run": args["run"], "loop": args["loop"], "ts": _iso(),
           "estMinutes": args["minutes"], "estTokens": args["tokens"],
           "note": args.get("note") or ""}
    _append(row)
    print("bm_ledger: %s %s/%s, %d min, %d tokens, declared before the work by "
          "construction (this row's timestamp is the proof)."
          % (kind, args["run"], args["loop"], args["minutes"], args["tokens"]))
    return EXIT_OK


def cmd_close(args):
    rows = _read_rows()
    decl = _latest_declaration(rows, args["run"], args["loop"])
    if decl is None:
        print("bm_ledger: refused. %s/%s was never declared, and an estimate written "
              "after the work is a prediction of the past. Nothing recorded."
              % (args["run"], args["loop"]))
        return EXIT_REFUSED
    if _already_closed(rows, args["run"], args["loop"]):
        print("bm_ledger: refused. %s/%s is already closed. History is append-only; "
              "a correction is a new loop id with a note, not a rewrite."
              % (args["run"], args["loop"]))
        return EXIT_REFUSED
    time_verdict = _grade(args["minutes"], decl["estMinutes"])
    token_verdict = _grade(args.get("tokens"), decl["estTokens"])
    row = {"kind": "close", "run": args["run"], "loop": args["loop"], "ts": _iso(),
           "actualMinutes": args["minutes"], "actualTokens": args.get("tokens"),
           "tokensBasis": args.get("basis") if args.get("tokens") is not None else None,
           "why": args["why"],
           "gradedAgainst": decl["ts"],
           "verdict": {"time": time_verdict, "tokens": token_verdict,
                       "overall": _overall(time_verdict, token_verdict)}}
    _append(row)
    print("bm_ledger: close %s/%s. time %s (est %d, actual %d), tokens %s%s. "
          "overall %s. why: %s"
          % (args["run"], args["loop"], time_verdict, decl["estMinutes"],
             args["minutes"], token_verdict,
             ("" if args.get("tokens") is None else
              " (est %d, actual %d, %s)" % (decl["estTokens"], args["tokens"],
                                            args.get("basis"))),
             row["verdict"]["overall"], args["why"]))
    return EXIT_OK


def cmd_report(args):
    rows = _read_rows()
    closes = [r for r in rows if r.get("kind") == "close"
              and (not args.get("run") or r.get("run") == args.get("run"))]
    if not closes:
        print("bm_ledger: NO-DATA. No closed loops%s, so calibration cannot be "
              "reported, and this line is not a clean bill."
              % ((" for run %s" % args["run"]) if args.get("run") else ""))
        return EXIT_OK
    decls = {}
    for r in rows:
        if r.get("kind") in ("declare", "amend"):
            decls[(r["run"], r["loop"])] = r
    time_errs, token_errs = [], []
    counts = {"better": 0, "on": 0, "worse": 0, "NO-DATA": 0}
    for c in closes:
        counts[c["verdict"]["overall"]] += 1
        d = decls.get((c["run"], c["loop"]))
        if d and d.get("estMinutes"):
            time_errs.append((c["actualMinutes"] - d["estMinutes"]) / float(d["estMinutes"]))
        if d and d.get("estTokens") and c.get("actualTokens") is not None:
            token_errs.append((c["actualTokens"] - d["estTokens"]) / float(d["estTokens"]))
    print("bm_ledger report: %d closed loop(s). overall: %d better, %d on, %d worse, "
          "%d NO-DATA (band: %d%%)."
          % (len(closes), counts["better"], counts["on"], counts["worse"],
             counts["NO-DATA"], int(BAND * 100)))
    if time_errs:
        bias = sum(time_errs) / len(time_errs)
        print("time bias %+.0f%%: positive means loops run longer than declared; "
              "mean absolute error %.0f%%."
              % (bias * 100, sum(abs(e) for e in time_errs) / len(time_errs) * 100))
    if token_errs:
        bias = sum(token_errs) / len(token_errs)
        print("token bias %+.0f%% over %d loop(s) with supplied actuals; the %d "
              "without stay NO-DATA rather than imagined."
              % (bias * 100, len(token_errs), len(closes) - len(token_errs)))
    for c in closes[-10:]:
        print("  %s/%s  %s  time est/act %s/%s  why: %s"
              % (c["run"], c["loop"], c["verdict"]["overall"],
                 decls.get((c["run"], c["loop"]), {}).get("estMinutes", "?"),
                 c["actualMinutes"], c["why"][:70]))
    return EXIT_OK


def _int_flag(argv, name, required):
    if name in argv:
        i = argv.index(name)
        try:
            return int(argv[i + 1])
        except (IndexError, ValueError):
            raise SystemExit(2)
    if required:
        raise SystemExit(2)
    return None


def _str_flag(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            raise SystemExit(2)
        return argv[i + 1]
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = ("usage: bm_ledger.py declare <run> <loop> --minutes N --tokens N [--note ...]\n"
             "       bm_ledger.py amend   <run> <loop> --minutes N --tokens N [--note ...]\n"
             "       bm_ledger.py close   <run> <loop> --minutes N --why \"...\" "
             "[--tokens N --basis measured|estimated]\n"
             "       bm_ledger.py report  [--run <run>]\n"
             "Declare before the work, grade after it, history append-only.")
    if not argv or argv[0] in ("-h", "--help"):
        print(usage)
        return EXIT_USAGE
    cmd = argv[0]
    try:
        if cmd in ("declare", "amend"):
            if len(argv) < 3:
                print(usage)
                return EXIT_USAGE
            args = {"run": argv[1], "loop": argv[2],
                    "minutes": _int_flag(argv, "--minutes", True),
                    "tokens": _int_flag(argv, "--tokens", True),
                    "note": _str_flag(argv, "--note")}
            if args["minutes"] <= 0 or args["tokens"] <= 0:
                print("bm_ledger: refused. An estimate of zero or less is not an "
                      "estimate; declare what you actually expect.")
                return EXIT_REFUSED
            return cmd_declare(args, kind=cmd)
        if cmd == "close":
            if len(argv) < 3:
                print(usage)
                return EXIT_USAGE
            why = _str_flag(argv, "--why")
            if not why or len(why.split()) < 3:
                print("bm_ledger: refused. A close without a why sentence (3 words or "
                      "more) grades nothing; say what made the loop faster, slower, "
                      "cheaper or dearer.")
                return EXIT_REFUSED
            tokens = _int_flag(argv, "--tokens", False)
            basis = _str_flag(argv, "--basis")
            if tokens is not None and basis not in ("measured", "estimated"):
                print("bm_ledger: refused. A supplied token actual carries its basis: "
                      "--basis measured or --basis estimated. Absent tokens are "
                      "honest NO-DATA; labeled ones are usable; unlabeled ones are "
                      "neither.")
                return EXIT_REFUSED
            args = {"run": argv[1], "loop": argv[2],
                    "minutes": _int_flag(argv, "--minutes", True),
                    "tokens": tokens, "basis": basis, "why": why}
            if args["minutes"] < 0 or (tokens is not None and tokens < 0):
                print("bm_ledger: refused. Negative actuals are clock or counter "
                      "errors, not history.")
                return EXIT_REFUSED
            return cmd_close(args)
        if cmd == "report":
            return cmd_report({"run": _str_flag(argv, "--run")})
    except SystemExit as exc:
        if exc.code == 2:
            print(usage)
            return EXIT_USAGE
        raise
    print(usage)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
