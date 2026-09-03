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
start. _mark_integrated() (lines 307-324) rewrites the run's own Work
document in place (open(record_path,"w") plus json.dump(doc,...), lines
322-323), marking each unit DONE after real integration. The Work
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
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import decide  # noqa: E402
import door  # noqa: E402
import integrate  # noqa: E402
import loom  # noqa: E402
import loop_bridge  # noqa: E402
import receipt_door  # noqa: E402

NODATA = "NO-DATA"
DOOR = os.path.join(HERE, "door.py")
TARGET_FILENAME = "target.json"
CLAIMS_FILENAME = "claims.json"
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
    CLAIMS_FILENAME, TARGET_FILENAME,
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


def run_loop(plan_path, claims_path, cwd, slots):
    """loop_bridge.main(), in-process, worker left at ITS OWN default
    (model_worker.py, since P0.2 landed there). Returns (code, text).

    The adapter directory is decided HERE (never passed in by the caller):
    every test that stands in for this function uses this exact four-argument
    signature, and the same _source_tools_dir() answer main() reads for the
    intent screen is the one loop_bridge is handed, so the bound a person
    sees and the bound the worker gets come from one loaded module."""
    args = ["--plan", plan_path, "--claims", claims_path, "--cwd", cwd,
            "--owner", "brother-run-%d" % os.getpid()]
    if slots is not None:
        args += ["--slots", str(slots)]
    tools_dir = _source_tools_dir()
    if tools_dir:
        args += ["--tools", tools_dir]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = loop_bridge.main(args)
    return code, out.getvalue() + err.getvalue()


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
COST_FIELDS = ("tokens_in", "tokens_out", "tokens_cached", "turns",
              "wall_clock_seconds", "cache_hit_rate", "failure_category",
              "harness_version", "harness_revision")

#: The fixed vocabulary a failure_category may take. "none" when nothing was
#: refused; the rest name the engine's own refusal shapes (PLAIN_VERDICTS,
#: above) at one word of granularity, never a free-text guess.
FAILURE_CATEGORIES = ("none", "check-failed", "scope-violation", "crashed",
                      "timeout")


def _harness_version(repo=None):
    """`git describe --always --dirty` of the tree that ran this harness
    (REPO_ROOT by default, this tool's own checkout, not the target --cwd):
    the one version string a receipt reader can match back to an exact
    commit, dirty state included. NO-DATA, never a fabricated string, when
    git cannot answer (not a git checkout, git missing, or any other
    failure)."""
    repo = repo or REPO_ROOT
    try:
        proc = subprocess.run(["git", "describe", "--always", "--dirty"],
                              cwd=repo, capture_output=True, text=True,
                              timeout=30)
    except Exception as exc:  # noqa: BLE001
        return "%s: git describe could not run in %s: %s" % (NODATA, repo, exc)
    if proc.returncode != 0:
        return ("%s: git describe exited %d in %s: %s"
               % (NODATA, proc.returncode, repo,
                  (proc.stderr or "").strip()[:200]))
    out = (proc.stdout or "").strip()
    return out if out else "%s: git describe produced no output in %s" % (NODATA, repo)


def _harness_revision(repo=None):
    """`git rev-parse HEAD` of the tree that ran this harness (REPO_ROOT by
    default, this tool's own checkout, not the target --cwd): the exact
    commit sha a receipt reader can match a delivery back to, unlike
    `git describe` (_harness_version, above), which is ambiguous across this
    estate's tag namespaces. NO-DATA, never a fabricated string, when git
    cannot answer (not a git checkout, git missing, or any other failure).
    Deliberately NOT `git describe`: harness-revision-v1 (defect 2, the
    zero-context critic, 2026-09-03) asks for the one string that names the
    exact commit, not the nearest tag."""
    repo = repo or REPO_ROOT
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                              capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return "%s: git rev-parse could not run in %s: %s" % (NODATA, repo, exc)
    if proc.returncode != 0:
        return ("%s: git rev-parse exited %d in %s: %s"
               % (NODATA, proc.returncode, repo,
                  (proc.stderr or "").strip()[:200]))
    out = (proc.stdout or "").strip()
    return out if out else "%s: git rev-parse produced no output in %s" % (NODATA, repo)


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

    THE RATE IS NEVER PRINTED ABOVE ONE. model_worker.py names the claude
    CLI's `input_tokens` as tokens_in, and that count EXCLUDES cache reads
    and cache writes (measured live 2026-09-03: input_tokens 2 beside
    cache_read_input_tokens 22972 and cache_creation_input_tokens 70272 on
    a one word reply), so on a real worker tokens_cached can exceed
    tokens_in and cached over tokens_in is not a rate. A real hit rate
    needs the cache-creation count, which the worker does not forward. So:
    the rate is cached over tokens_in only while that quotient can be a
    share (tokens_cached at most tokens_in), and otherwise NO-DATA naming
    exactly this, with the two real counts still printed above it."""
    if harness_revision is None:
        harness_revision = _harness_revision()
    tokens_in = _sum_usage_field(claims, "tokens_in", usage_gap)
    tokens_out = _sum_usage_field(claims, "tokens_out", usage_gap)
    tokens_cached = _sum_usage_field(claims, "tokens_cached", usage_gap)
    if (isinstance(tokens_in, int) and isinstance(tokens_cached, int)
            and tokens_in > 0 and tokens_cached <= tokens_in):
        cache_hit_rate = round(tokens_cached / tokens_in, 4)
    elif isinstance(tokens_in, int) and isinstance(tokens_cached, int):
        cache_hit_rate = ("%s: tokens_cached (%d) exceeds tokens_in (%d); the "
                          "worker's tokens_in counts only uncached input "
                          "tokens (the claude CLI's input_tokens) and the "
                          "cache-creation count a real rate needs is not "
                          "forwarded, so no rate is printed"
                          % (NODATA, tokens_cached, tokens_in))
    else:
        cache_hit_rate = ("%s: cannot compute a cache hit rate without real "
                          "tokens_in and tokens_cached, which this run did "
                          "not record%s" % (NODATA, ("; " + usage_gap)
                                            if usage_gap else ""))
    turns = sum(int(c.get("attempt") or 0) for c in (claims or {}).values()
               if isinstance(c, dict))
    return {"tokens_in": tokens_in, "tokens_out": tokens_out,
           "tokens_cached": tokens_cached, "turns": turns,
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


def build_report(record, claims, before, after, changed=None,
                 log_path=None, loop_text="", cost_block=None):
    """The delivery report: what happened, named, never inferred from a
    worker's own claim. `record` is the canonical Work document door wrote.

    The harsh EVAD 2026-08-31 found this report proved nothing to a skeptic:
    it named units and revisions but never the files that changed nor what
    verified each unit. It now carries a Changed list (from git, `changed`)
    and, per integrated unit, the done_check command that had to pass, so a
    reader can see WHAT moved and HOW it was checked without trusting a
    worker's self-report.

    `cost_block`, when given (T1), is printed one field per line, in
    COST_FIELDS order, so the eight required fields are always readable in
    the same place a person already reads the rest of the record; omitted
    (None) leaves the existing callers of this function, which predate the
    cost block, byte-for-byte unchanged."""
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
    receipts = receipt_door.receipts_for(record, claims, refused, log_path)
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
    lines.append("")
    lines.append("  " + receipt_door.SCOPING_SENTENCE)
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


def _verify_evidence(claim, row, cwd):
    """(ok, evidence_sentence_or_refusal_reason). Independently re-checks the
    claim store's own account rather than trusting its `state` string, because
    a claim store is just a file: the harsh EVAD 2026-08-31 drove
    _mark_integrated directly and it stamped an integrated sentence onto a row
    whose done_check was literally `false` and whose canonical_rev was the
    unvalidated literal deadbeef. Every one of the checks below closes exactly
    that gap, and any one of them failing is a refusal, never a stamp."""
    evidence = (claim or {}).get("evidence")
    if not isinstance(evidence, dict):
        return False, ("no evidence was recorded for this claim (no check "
                       "command, exit code, output or canonical revision); a "
                       "delivery record refuses to claim integration it "
                       "cannot prove")
    command = str(evidence.get("check_command") or "").strip()
    exit_code = evidence.get("exit_code")
    output = evidence.get("output")
    rev = str(evidence.get("canonical_rev") or "").strip()
    if not command:
        return False, "the claim's evidence names no check command"
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return False, ("the claim's evidence carries no captured exit code "
                       "for %r; the check may never have run" % command)
    if exit_code != 0:
        return False, ("the claim's own evidence shows %r exited %d, not 0"
                       % (command, exit_code))
    if output is None:
        return False, "the claim's evidence carries no captured output at all"
    if not rev:
        return False, "the claim's evidence names no canonical revision"
    kind = _git_object_type(cwd, rev)
    if kind is None:
        return False, ("the recorded canonical revision %r does not resolve "
                       "in %s (git cat-file -t)" % (rev, cwd))
    missing = _missing_artifacts(cwd, rev, row.get("owns") or [])
    if missing:
        return False, ("declared artifact(s) not present in the repository at "
                       "%s: %s" % (rev[:12], ", ".join(missing)))
    reexec_ok, reexec_reason = _reexecute_check(command, cwd, exit_code)
    if not reexec_ok:
        return False, reexec_reason
    note = (" (output truncated to the last 50 lines)"
           if evidence.get("output_truncated") else "")
    body = output if output else "(the check printed nothing)"
    return True, ("integrated on canonical at %s; check %r exited 0%s. "
                 "output: %s" % (rev, command, note, body))


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
    changed = False
    refusals = {}
    touched = False
    for row in (doc.get("rows") or doc.get("units") or []):
        uid = row.get("id")
        if uid not in done_ids or row.get("status") == "DONE":
            continue
        ok, detail = _verify_evidence((claims or {}).get(uid), row, cwd)
        touched = True
        if ok:
            row["status"] = "DONE"
            row["evidence"] = detail
            evidence = ((claims or {}).get(uid) or {}).get("evidence") or {}
            if isinstance(evidence.get("files_changed"), list):
                row["files_changed_by_unit"] = [
                    str(p) for p in evidence["files_changed"]]
            changed = True
        else:
            row["integration_refused"] = detail
            refusals[uid] = detail
    if touched:
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
    return changed, refusals


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
    with open(record_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
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
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
    return stamped


#: How many times a unit whose check _stamp_prechecks marked
#: check_looks_broken is asked about again, once, before falling through to
#: _refuse_broken_precheck_units unchanged. ONE: a check the planner cannot
#: fix with the exact stderr it produced in front of it is reported broken,
#: not argued with round after round.
MAX_CHECK_REWRITE_ATTEMPTS = 1


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
    MAX_CHECK_REWRITE_ATTEMPTS (1) time per unit.

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
        prompt = door.build_check_rewrite_prompt(
            row.get("objective") or row.get("title") or "", original,
            stderr_text)
        log.note("brother_run: asking the planner once for a replacement "
                 "done_check for %s" % uid)
        try:
            proc = door.ask_decomposer(cmd, prompt)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.note("brother_run: the planner could not be asked for a "
                     "replacement done_check for %s: %s; keeping the "
                     "original check" % (uid, exc))
            continue
        try:
            raw = json.loads(door.strip_code_fences(proc.stdout))
        except ValueError as exc:
            log.note("brother_run: %s's replacement done_check could not "
                     "be read as JSON, keeping the original: %s"
                     % (uid, exc))
            continue
        if not isinstance(raw, dict):
            log.note("brother_run: %s's replacement was not a JSON "
                     "object, keeping the original" % uid)
            continue
        new_check = str(raw.get("done_check") or "").strip()
        if not new_check:
            log.note("brother_run: %s's replacement named no done_check, "
                     "keeping the original" % uid)
            continue
        resolved, note = door.resolve_done_check_interpreter(new_check)
        if note:
            new_check = resolved
            log.note("door: %s" % note)
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
    if touched:
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
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
        else:
            kept.append(row)
    if refused:
        doc[key] = kept
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
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
        else:
            kept.append(row)
    if refused:
        doc[key] = kept
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
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
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
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

    TWO KINDS OF DIRT, and only one is refused. A dirty path INSIDE the
    run's write set (it overlaps a not-DONE unit's `owns`) is somebody's
    uncommitted work a merge of that unit would bury: refused in one line
    naming the count, the first three paths with the unit that owns each,
    and what to do; exit 1 is the caller's. Dirt OUTSIDE the write set (the
    acceptance harness's area 6: an unrelated uncommitted edit that must
    survive) is left exactly where it is: the run proceeds and this only
    says so, because integrate.py still refuses to merge over a dirty tree
    and names it, so the person's own edit is never touched and the run
    can be resumed once they commit it. A tree git cannot read is refused,
    never guessed clean."""
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
                "commit or stash them first, nothing was claimed or run"
                % (cwd, len(hits), shown)), ""
    shown = ", ".join(paths[:3]) + (", ..." if len(paths) > 3 else "")
    return "", ("brother_run: the repository at %s is dirty on %d "
                "uncommitted path(s) outside this run's write set (%s); "
                "they are left untouched, and nothing merges into the tree "
                "until they are committed" % (cwd, len(paths), shown))


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
    with open(record_path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)


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
        line = "%s: %s. done_check: `%s`. depends on: %s" % (
            row.get("id"), objective, check, _depends_on_text(row))
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


def _human_moment(log, moment, spec, resolver=None):
    """I3, the screen loom: pause at one of the charter's four human moments
    (MOMENTS, above). `spec` is a decide.py spec whose every option already
    carries a computed weight and a fact-based mark (built by _fact_spec or
    receipt_door.acceptance_spec/release_spec, never typed here); this
    reuses decide.rank() and decide.render() outright rather than forking
    either.

    THE MACHINERY, the full rendered screen, goes to the run log only,
    verbatim, the same rule RunLog already holds for loop_bridge's own
    output (RunLog's own docstring: "THE MACHINERY IS NOT DELETED, it is
    moved"). THE CHAT STREAM gets exactly two lines: one ECHO (a screen was
    posed: how many options, which one the arithmetic recommends and at
    what score) and one PROOF (what was chosen, and how).

    `resolver(moment, spec, scored, close) -> {"choice", "name", "by", ...}`
    is how a choice is recorded, and THE RUN DOES NOT PROCEED PAST THIS
    CALL UNTIL resolver() RETURNS: that is the whole of "blocks" here, the
    same way any ordinary function call blocks its own caller until it
    returns. A resolver built to poll a human's out-of-band answer (the
    live shape _recorded_answer_resolver's caller would use) blocks for
    exactly as long as that takes; `resolver=None` uses _auto_resolver, the
    recorded default, which never blocks at all."""
    _criteria, _note, scored, close = decide.rank(spec)
    # THE MACHINERY, to the log, never the chat stream.
    log.note("---- %s screen ----\n%s" % (moment, decide.render(spec)))
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
    ap.add_argument("--runs-root",
                    help="where the run's Work document and claim store live "
                         "(under docs/plan/runs); defaults to this tool's own "
                         "repository, never the target --cwd, which "
                         "integration requires to stay clean")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    cwd = os.path.abspath(args.cwd)
    runs_root = os.path.abspath(args.runs_root) if args.runs_root else REPO_ROOT
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
            print("brother_run: no unfinished run found for %s" % cwd)
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
    if args.dry_run:
        print("brother_run: --dry-run, stopping before any work is claimed "
              "or run")
        return 0
    if resumed:
        # THE RESUMER, the second field beside the creator's, latest wins;
        # the receipts below name both (build_report).
        doc = _stamp_harness(record["path"], "harness_revision_resumed",
                             harness_revision, overwrite=True)
        created_by = str(doc.get("harness_revision") or NODATA)
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
    intent_choice = _human_moment(log, "intent", _fact_spec(
        title="Proceed with this outcome", eyebrow="Intent",
        plain_summary="This is what this run is about to act on before "
                      "anything is claimed or run, one line per piece of "
                      "work, exactly as the planning model wrote it:\n\n"
                      + "\n".join(unit_lines) + "\n\n" + bounds_line,
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
        }, extra_options=[refuse_option]), resolver=live_resolver)
    if intent_choice.get("choice") == "refuse":
        print("brother_run: refused at the intent screen; nothing was "
              "claimed or run", file=sys.stderr)
        return 1
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
        log.say("brother_run: %d piece(s) of work, none finished yet. How "
                "long this takes is not knowable in advance, so no estimate "
                "is given; each piece reports as it lands. Each piece gets "
                "at most %d attempt(s), and one attempt's worker is stopped "
                "after %d seconds." % (total_units, MAX_UNIT_ATTEMPTS,
                                       limit_seconds))

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
                                                  loop_text=loop_text_all)
    cost_block = build_cost_block(
        claims, refused, loop_text_all,
        (datetime.datetime.now() - run_start).total_seconds(),
        _harness_version(), harness_revision,
        usage_gap=_usage_gap_reason(spawn))
    report, integrated, refused = build_report(record, claims, before,
                                               after, changed,
                                               log_path=log_path,
                                               loop_text=loop_text_all,
                                               cost_block=cost_block)

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
        _human_moment(log, "acceptance",
                     receipt_door.acceptance_spec(record, receipts, before,
                                                  after, log_path),
                     resolver=_recorded_answer_resolver(run_dir, "acceptance"))
        if triggers:
            _human_moment(log, "release",
                         receipt_door.release_spec(record, receipts, triggers,
                                                   before, after, log_path),
                         resolver=_recorded_answer_resolver(run_dir, "release"))

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
    print("brother_run: exit %d: %s" % (exit_code, exit_reason))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
