#!/usr/bin/env python3
"""Tiny-task cost: what a one line change really costs through the door.

WHY THIS EXISTS. docs/plan/FLOOR-2026-09-05.json scores Brother 0.26 on
Tiny-task friction against 1.00 for the fastest measured competitor, a gap of
0.74 on a MUST MATCH capability, and the number behind that cell is a single
elapsed column from one head-to-head round. A cell that big deserves an
instrument rather than a memory of a race, so this script MEASURES the price
of a genuinely tiny task through the product's own public entry point,
scripts/brother_run.py, driven exactly as a user drives it: one command, one
outcome sentence, a repository to work in.

WHAT IT MEASURES, and the honest name of each number:

  wall_clock_seconds     the whole run, timed around the one command. Under
                         the stub seam this is the ENGINE's own cost and
                         nothing else, which is precisely the point: it
                         separates what this code costs from what a model
                         session costs, the split docs/decisions/light-path-
                         for-small-changes-2026-09-04.json rests on.
  user_steps             how many commands a person issues to get the change
                         landed. COUNTED by this harness, from the commands it
                         actually runs against the product, never estimated.
  files_written          what the run left behind, split into the target
                         repository (the change the person asked for) and the
                         runs root (the engine's own bookkeeping). Both are
                         read off the filesystem after the run.
  price_said_up_front    whether the run stated its price BEFORE any work, the
                         founder's ruling of 2026-09-04 on row E90. Read from
                         run.log by POSITION: the price paragraph must appear
                         before the first worker line, because a price printed
                         after the wait is a receipt, not a price.

  Every number a stubbed run cannot honestly produce reads NO-DATA and names
  why. In particular MODEL LATENCY IS NEVER MEASURED HERE: the stub seam
  replaces the model, so this script must never be quoted as the wall clock a
  person with a real model waits. The t7 report of 2026-09-04 (568.03s)
  remains the only measurement of that, and README.md's limits section quotes
  it.

THE TWO CASES, both genuinely tiny, both in a throwaway git repository:
  docs  a one line documentation fix: one unit, one file, a grep as its check.
  code  a one line code fix with an EXISTING test: the repository is seeded
        with the module and its test, the test FAILS before the run, and the
        unit's own done_check is that same test command, so the run is graded
        by a check that was already there rather than by one it wrote itself.

Standard library only, no network. Exit 0 when both cases ran and their
numbers were recorded, 1 when a case failed to land its change, 2 when the
harness could not run at all (no product entry point to drive).
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BROTHER_RUN = os.path.join(HERE, "brother_run.py")
NODATA = "NO-DATA"

sys.path.insert(0, HERE)
import test_brother_run as tbr  # noqa: E402  # make_repo/write_stub, the same stub seam brother_run's own suite and scripts/product_acceptance.py already use
import brother_run as _br  # noqa: E402  # ATTEMPTS_DIRNAME, CLAIMS_FILENAME: retry_evidence() reads the engine's own attempt trace, never a second copy of those names

#: The decomposer stub for the docs case: ONE unit, one file. The smallest
#: outcome the door can be handed that still asks for a real change.
#: THE done_check IS `test -f ... && grep ...` AND NOT A BARE grep, and the
#: reason was measured here rather than guessed: a bare `grep -q written
#: NOTES.md` on a repository where NOTES.md does not yet exist exits 2, and
#: receipt_door reads exit 2 with that stderr as "this check cannot run at
#: all", so the door refuses the unit before any worker starts. That refusal
#: is the product behaving correctly (a check that cannot run cannot prove
#: anything), so the fixture is what had to change: a tiny task's check must
#: FAIL cleanly before the work, not fail to execute.
DOCS_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "D1", "objective": "add the missing line to the notes file",
         "done_check": "test -f NOTES.md && grep -q written NOTES.md",
         "writes": ["NOTES.md"], "deps": []},
    ]))
"""

#: The code case: one unit whose done_check is the repository's OWN existing
#: test command, not a check the run invented for itself.
CODE_DECOMPOSER = """
    import json, sys
    sys.stdin.read()
    print(json.dumps([
        {"id": "C1", "objective": "make the existing test pass",
         "done_check": "python3 test_widget.py",
         "writes": ["widget.py"], "deps": []},
    ]))
"""

#: The docs worker: writes whatever the prompt declared, the same shape
#: test_brother_run.WRITER_MODEL uses.
DOCS_MODEL = tbr.WRITER_MODEL

#: The code worker: writes the one line that makes the seeded test pass.
CODE_MODEL = """
    import re, sys
    prompt = sys.argv[-1] if len(sys.argv) > 1 else ""
    m = re.search(r"Declared write scope: ([^\\n]+)", prompt)
    for path in (p.strip() for p in (m.group(1).split(",") if m else [])):
        if path.endswith("widget.py"):
            with open(path, "w") as fh:
                fh.write("def width():\\n    return 3\\n")
    print("stub model wrote: %s" % (m.group(1) if m else "(nothing declared)"))
"""

#: The seeded test of the code case. It FAILS before the run (widget.width()
#: returns 2) and passes after, which is what makes the case a fix rather
#: than a file that appeared.
SEEDED_TEST = """import widget
assert widget.width() == 3, "width is %r, not 3" % widget.width()
print("OK")
"""

SEEDED_MODULE = "def width():\n    return 2\n"

#: FAST-0 (night run 2026-09-09): the two ELIGIBLE fixtures below, added
#: beside the two normal-route controls above. Both name their own
#: existing file and their own existing check in the outcome text,
#: exactly the shape docs/decisions/light-path-for-small-changes-
#: 2026-09-04.json's option A (scripts/brother_run.fast_route_eligibility)
#: recognizes, so brother_run.py never asks a model to plan them at all.
#:
#: POISON_DECOMPOSER stands in for DOOR_MODEL_CMD on both: if the fast
#: route ever regresses into calling it anyway, it exits nonzero and the
#: case FAILS loudly, rather than the fixture silently measuring the
#: normal route.
POISON_DECOMPOSER = """
    import sys
    sys.stderr.write(
        "FAIL: the decomposer was invoked; the fast route must skip "
        "it entirely\\n")
    sys.exit(3)
"""

#: The docs-eligible fixture's own pre-existing check (seeded,
#: committed, FAILING before the run): unittest, not a bare assert
#: script, because fast_route_eligibility only recognizes an existing
#: scripts/test_*.py MODULE, run as `python3 -m unittest <dotted
#: module>`.
SEEDED_NOTES_TEST = """import unittest


class NotesSaysWritten(unittest.TestCase):
    def test_written(self):
        with open("NOTES.md", encoding="utf-8") as fh:
            self.assertIn("written", fh.read())
"""

#: The code-eligible fixture's own pre-existing check, the same seeded
#: bug as SEEDED_MODULE/SEEDED_TEST above but addressed as a unittest
#: module under scripts/, again because that is the one shape the
#: predicate recognizes as "its own existing check".
SEEDED_TEST_MODULE = """import unittest
import widget


class WidgetWidth(unittest.TestCase):
    def test_width(self):
        self.assertEqual(widget.width(), 3)
"""


def sh(args, cwd=None, env=None, timeout=180):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


#: Where stub_env tells each stub script to record that it actually ran,
#: so a session count is READ, never estimated: sys.argv[0] under
#: `python3 -m unittest` is rewritten by unittest/__main__.py itself
#: (CPython's own behavior, made for its --help text) to the literal
#: string "<python> -m unittest", which breaks a naive path built from
#: it; counting via a sentinel file the stub itself appends to sidesteps
#: that entirely.
SESSIONS_LOG_ENV = "TINY_TASK_SESSIONS_LOG"


def _count_prelude(kind):
    """Prepended (not appended) to a stub body, so the count is recorded
    even when the rest of the body exits early (POISON_DECOMPOSER's
    sys.exit(3)). Four-space indented to match every existing stub body
    string here, so write_stub's own textwrap.dedent(body) still finds
    one common indent across the combined text."""
    lines = [
        "",
        "    import os as _cnt_os",
        "    _cnt_log = _cnt_os.environ.get(%r)" % SESSIONS_LOG_ENV,
        "    if _cnt_log:",
        "        with open(_cnt_log, 'a', encoding='utf-8') as _cnt_fh:",
        "            _cnt_fh.write(%r + chr(10))" % kind,
        "",
    ]
    return "\n".join(lines)


def stub_env(tmp, decomposer_body, model_body):
    """DOOR_MODEL_CMD/MODEL_WORKER_CMD pointed at throwaway scripts in `tmp`,
    the identical seam scripts/product_acceptance.py uses. Each stub also
    records, in `tmp`/sessions.log, that it actually ran (see
    _count_prelude): _drive() below reads that file back rather than
    guessing how many sessions a case opened."""
    decomposer = tbr.write_stub(
        tmp, "decomposer.py", _count_prelude("planner") + decomposer_body)
    model = tbr.write_stub(
        tmp, "model.py", _count_prelude("worker") + model_body)
    env = dict(os.environ)
    # shlex.quote, not a plain "%s %s" join: `python3 -m unittest`
    # rewrites sys.argv[0] to the literal string "<python> -m
    # unittest" (CPython's own behavior), which tmp_sandbox.install()
    # folds into ITS OWN temp-dir prefix, so an unquoted path built
    # from it can carry a real space; door.py's resolve_cmd reads this
    # value back with shlex.split, so it must be built with shlex.quote
    # to round-trip.
    env["DOOR_MODEL_CMD"] = "%s %s" % (shlex.quote(sys.executable),
                                        shlex.quote(decomposer))
    env["MODEL_WORKER_CMD"] = "%s %s" % (shlex.quote(sys.executable),
                                          shlex.quote(model))
    env[SESSIONS_LOG_ENV] = os.path.join(tmp, "sessions.log")
    return env


def _read_sessions(tmp):
    """(planner_sessions, worker_sessions), counted from the sentinel
    file every stub prepends a write to (see _count_prelude): a line per
    actual subprocess invocation, never an estimate."""
    path = os.path.join(tmp, "sessions.log")
    if not os.path.isfile(path):
        return 0, 0
    with open(path, encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    return lines.count("planner"), lines.count("worker")


#: TEST HARDENING (night run 2026-09-09): a worker that times out and gets
#: retried by the bounded-repair drain (brother_run.py's own MAX_UNIT_ATTEMPTS
#: rule, docstring lines 44-51) reruns MODEL_WORKER_CMD, so
#: worker_sessions above can honestly read 2 even on a case that only ever
#: declares ONE unit of work -- measured live, 2 of 4 isolated runs on a
#: loaded machine. That is not a flake in this instrument, it is the drain
#: doing exactly what its own docstring promises; the finding it surfaced
#: is that nothing PROVED the retry was recorded rather than merely
#: guessed at. retry_evidence() answers that from the run's own files,
#: never estimated: ATTEMPTS_DIRNAME (brother_run.py, T2) holds one
#: subdirectory per attempt, never overwritten, and CLAIMS_FILENAME holds
#: the claim store's own attempt counter (claim_store.acquire bumps it on
#: every reclaim).
def _run_dir_from_runs_root(runs_root):
    """The same run directory _run_log finds, without the run.log
    filename, or None when no run directory was ever written."""
    log_path = _run_log(runs_root)
    return os.path.dirname(log_path) if log_path else None


def _sole_claim_id(run_dir):
    """The one unit id claims.json names, or None when the file is
    missing, unreadable, or (a shape this instrument's own fixtures never
    produce) does not hold exactly one. Every case this script drives
    declares a single unit, so "the sole id" is the run's own unambiguous
    answer to "which unit does a session count belong to", read rather
    than assumed."""
    claims_path = os.path.join(run_dir, _br.CLAIMS_FILENAME)
    if not os.path.isfile(claims_path):
        return None
    try:
        with open(claims_path, encoding="utf-8") as fh:
            claims = json.load(fh)
    except (OSError, ValueError):
        return None
    ids = list(claims.keys())
    return ids[0] if len(ids) == 1 else None


def retry_evidence(run_dir, uid):
    """(attempt_states, claim_attempt) for `uid`, read from the run's own
    record, never estimated:

    attempt_states  the ordered list of each attempt-N/claim.json's own
                    "state" field under ATTEMPTS_DIRNAME (e.g.
                    ["failed", "done"] for a worker that timed out once
                    and landed on retry). One entry per attempt actually
                    traced to disk; empty when the run wrote no trace at
                    all for this unit.
    claim_attempt   CLAIMS_FILENAME's own "attempt" counter for `uid` (the
                    claim store's count of how many times it was
                    acquired), or None when the file or the unit is
                    absent.

    A worker_sessions count greater than 1 with an empty attempt_states
    here is exactly the SILENT retry this instrument now refuses to
    accept quietly: the caller decides what that means, this function
    only reports what it found."""
    safe = _br._safe_uid_segment(uid)
    unit_dir = os.path.join(run_dir, _br.ATTEMPTS_DIRNAME, safe)
    attempt_states = []
    if os.path.isdir(unit_dir):
        names = sorted(
            (d for d in os.listdir(unit_dir) if d.startswith("attempt-")),
            key=lambda d: int(d.rsplit("-", 1)[-1]))
        for name in names:
            claim_path = os.path.join(unit_dir, name, "claim.json")
            if os.path.isfile(claim_path):
                with open(claim_path, encoding="utf-8") as fh:
                    attempt_states.append(json.load(fh).get("state"))
    claim_attempt = None
    claims_path = os.path.join(run_dir, _br.CLAIMS_FILENAME)
    if os.path.isfile(claims_path):
        with open(claims_path, encoding="utf-8") as fh:
            claims = json.load(fh)
        claim = claims.get(uid) or {}
        claim_attempt = claim.get("attempt")
    return attempt_states, claim_attempt


def retry_record_for(runs_root):
    """{"unit_id", "attempt_states", "claim_attempt"} for the sole unit a
    _drive() case declared, or None when there is no run directory or no
    single unambiguous unit id to key it by. Folded into every result dict
    _drive() returns (see below) so a test never has to re-derive
    run_dir/uid itself to ask "was this retry recorded"."""
    run_dir = _run_dir_from_runs_root(runs_root)
    if not run_dir:
        return None
    uid = _sole_claim_id(run_dir)
    if not uid:
        return None
    attempt_states, claim_attempt = retry_evidence(run_dir, uid)
    return {"unit_id": uid, "attempt_states": attempt_states,
            "claim_attempt": claim_attempt}


def _dirty_paths(repo):
    out = sh(["git", "status", "--porcelain"], cwd=repo)
    return sorted(line[3:].strip() for line in out.stdout.splitlines()
                  if line.strip())


def _committed_since(repo, base):
    out = sh(["git", "diff", "--name-only", base, "HEAD"], cwd=repo)
    return sorted(p for p in out.stdout.splitlines() if p.strip())


def _runs_root_files(runs_root):
    runs_dir = os.path.join(runs_root, "docs", "plan", "runs")
    found = []
    for dirpath, _dirnames, filenames in os.walk(runs_dir):
        for name in filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name),
                                         runs_root))
    return sorted(found)


def _run_log(runs_root):
    """The newest run.log under `runs_root`, or None."""
    runs_dir = os.path.join(runs_root, "docs", "plan", "runs")
    if not os.path.isdir(runs_dir):
        return None
    for name in sorted(os.listdir(runs_dir), reverse=True):
        path = os.path.join(runs_dir, name, "run.log")
        if os.path.isfile(path):
            return path
    return None


#: The price paragraph's own opening words, from brother_run.price_paragraph.
#: Matched as text on purpose: this instrument reads what a PERSON reads, not
#: an internal structure, so a refactor that keeps the function and loses the
#: sentence still fails here.
PRICE_OPENING = "Price, before anything is claimed or run:"

#: How the first worker line is recognised in the log. The price must come
#: before it. BOTH MARKS ARE THE ENGINE'S OWN WORDS, not invented here:
#: "brother_run: loop_bridge round 1" is the marker
#: test_brother_run.ThePriceIsSaidBeforeTheWait already uses for exactly this
#: question, and "CLAIMED (" is the earlier line the drain prints when a unit
#: is first claimed, which is the real moment the work starts. Taking the
#: FIRST of the two is the stricter reading, and the stricter one is the one
#: the ruling asked for.
WORKER_MARKS = ("claimed (", "brother_run: loop_bridge round")


def read_price(log_path):
    """(said_up_front, line_number, paragraph, first_worker_line).

    said_up_front is True only when the price paragraph appears in the log
    AND appears before the first worker line. A price printed after the work
    started is a receipt, and the ruling asked for a price."""
    if not log_path or not os.path.isfile(log_path):
        return False, None, NODATA + ": no run log was written", None
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    price_at = None
    worker_at = None
    for i, line in enumerate(lines):
        low = line.lower()
        if price_at is None and PRICE_OPENING in line:
            price_at = i
        if worker_at is None and any(mark in low for mark in WORKER_MARKS):
            worker_at = i
    if price_at is None:
        return (False, None,
                NODATA + ": the run log carries no price paragraph", worker_at)
    said = worker_at is None or price_at < worker_at
    return said, price_at + 1, lines[price_at].strip(), worker_at


#: The two shapes a stated wait can take, matched on the price paragraph's
#: own words. THIS TOOL READS THE SENTENCE, NOT A FIELD, because row S18 asks
#: whether the person is told the wait, and a person reads the sentence.
WAIT_FROM_THIS_TARGET = "really took"
WAIT_FROM_ELSEWHERE = "what the wait has been"


def price_wait_figure(paragraph):
    """What the price said about the WAIT, which is the half row S18 asks for.

    Three readings, never two: a median derived from this target's own
    finished runs, a figure this estate timed elsewhere and quotes with its
    instrument named, or NO-DATA. The middle one exists because a FIRST run
    against a repository has no history of its own, which is exactly the
    situation a one line change is in, and answering it with NO-DATA alone
    tells the person nothing at the only moment they can still walk away."""
    if not paragraph:
        return NODATA
    if WAIT_FROM_THIS_TARGET in paragraph:
        return "measured on this target"
    if WAIT_FROM_ELSEWHERE in paragraph:
        return "quoted from a timed run elsewhere"
    return NODATA


def case_docs(tmp):
    repo = tbr.make_repo(tmp)
    env = stub_env(tmp, DOCS_DECOMPOSER, DOCS_MODEL)
    return _drive("docs", repo, tmp, env,
                  "the notes file carries the one line it is missing",
                  lambda: os.path.isfile(os.path.join(repo, "NOTES.md")))


def case_code(tmp):
    repo = tbr.make_repo(tmp)
    with open(os.path.join(repo, "widget.py"), "w", encoding="utf-8") as fh:
        fh.write(SEEDED_MODULE)
    with open(os.path.join(repo, "test_widget.py"), "w", encoding="utf-8") as fh:
        fh.write(SEEDED_TEST)
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "seed the failing test"], cwd=repo)
    before = sh([sys.executable, "test_widget.py"], cwd=repo)
    if before.returncode == 0:
        return {"case": "code", "verdict": NODATA,
                "why": "the seeded test passed BEFORE the run, so this case "
                       "proves nothing: a check that already passes before "
                       "the work is not evidence the work happened"}

    def landed():
        after = sh([sys.executable, "test_widget.py"], cwd=repo)
        return after.returncode == 0

    result = _drive("code", repo, tmp,
                    stub_env(tmp, CODE_DECOMPOSER, CODE_MODEL),
                    "the existing widget test passes", landed)
    result["seeded_test_failed_before"] = True
    return result


def case_docs_eligible(tmp):
    """FAST-0: names its own existing file (NOTES.md) and its own
    existing check (scripts/test_notes.py) in the outcome text, so
    fast_route_eligibility takes it and brother_run.py never opens a
    planning session at all. POISON_DECOMPOSER stands in for
    DOOR_MODEL_CMD: if it is ever invoked, this case fails loudly."""
    repo = tbr.make_repo(tmp)
    scripts_dir = os.path.join(repo, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    with open(os.path.join(repo, "NOTES.md"), "w", encoding="utf-8") as fh:
        fh.write("notes\n")
    with open(os.path.join(scripts_dir, "test_notes.py"), "w",
              encoding="utf-8") as fh:
        fh.write(SEEDED_NOTES_TEST)
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "seed the failing notes check"],
       cwd=repo)
    before = sh([sys.executable, "-m", "unittest", "scripts.test_notes"],
               cwd=repo)
    if before.returncode == 0:
        return {"case": "docs-eligible", "verdict": NODATA,
                "why": "the seeded check passed BEFORE the run, so this "
                       "case proves nothing"}

    def landed():
        with open(os.path.join(repo, "NOTES.md"), encoding="utf-8") as fh:
            return "written" in fh.read()

    env = stub_env(tmp, POISON_DECOMPOSER, DOCS_MODEL)
    result = _drive(
        "docs-eligible", repo, tmp, env,
        "edit NOTES.md and prove it with scripts/test_notes.py", landed)
    result["seeded_test_failed_before"] = True
    return result


def case_code_eligible(tmp):
    """FAST-0's other eligible fixture: names widget.py and its own
    existing scripts/test_widget.py module. Same POISON_DECOMPOSER
    guard as the docs-eligible case above."""
    repo = tbr.make_repo(tmp)
    scripts_dir = os.path.join(repo, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    with open(os.path.join(repo, "widget.py"), "w", encoding="utf-8") as fh:
        fh.write(SEEDED_MODULE)
    with open(os.path.join(scripts_dir, "test_widget.py"), "w",
              encoding="utf-8") as fh:
        fh.write(SEEDED_TEST_MODULE)
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "commit", "-q", "-m", "seed the failing test module"],
       cwd=repo)
    before = sh([sys.executable, "-m", "unittest", "scripts.test_widget"],
               cwd=repo)
    if before.returncode == 0:
        return {"case": "code-eligible", "verdict": NODATA,
                "why": "the seeded test passed BEFORE the run, so this "
                       "case proves nothing"}

    def landed():
        after = sh([sys.executable, "-m", "unittest", "scripts.test_widget"],
                   cwd=repo)
        return after.returncode == 0

    env = stub_env(tmp, POISON_DECOMPOSER, CODE_MODEL)
    result = _drive(
        "code-eligible", repo, tmp, env,
        "fix widget.py and prove it with scripts/test_widget.py", landed)
    result["seeded_test_failed_before"] = True
    return result


def _drive(name, repo, tmp, env, outcome, landed):
    """One tiny task through the public entry point, timed. `user_steps` is
    COUNTED here: this harness issues exactly the commands a person issues,
    and the count is the length of that list, never a judgement about it."""
    runs_root = os.path.join(tmp, "runs-" + name)
    os.makedirs(runs_root, exist_ok=True)
    base = sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()

    steps = ['python3 scripts/brother_run.py "%s" --cwd <repo> '
             '--runs-root <runs>' % outcome]
    start = time.time()
    proc = sh([sys.executable, BROTHER_RUN, outcome,
               "--cwd", repo, "--runs-root", runs_root], cwd=repo, env=env)
    elapsed = time.time() - start

    log_path = _run_log(runs_root)
    said, line_no, paragraph, worker_at = read_price(log_path)
    changed = _committed_since(repo, base) if base else []
    result = {
        "case": name,
        "outcome": outcome,
        "exit_code": proc.returncode,
        "verdict": "PASS" if (proc.returncode == 0 and landed()) else "FAIL",
        "wall_clock_seconds": round(elapsed, 2),
        "wall_clock_note": "the ENGINE only: both model calls are stubbed at "
                           "the DOOR_MODEL_CMD and MODEL_WORKER_CMD seam, so "
                           "model latency is " + NODATA + " here and this "
                           "figure is never the wait a person with a real "
                           "model pays",
        "user_steps": len(steps),
        "user_step_commands": steps,
        "files_written_in_repo": changed or _dirty_paths(repo),
        "files_written_in_runs_root": _runs_root_files(runs_root),
        "price_said_up_front": said,
        "price_line_number": line_no if line_no else NODATA,
        "price_paragraph": paragraph,
        "price_states_a_wait": price_wait_figure(paragraph),
        "first_worker_log_line": (worker_at + 1) if worker_at is not None
                                 else NODATA + ": no worker line in the log",
    }
    # THE INSTRUMENT (night run 2026-09-09): sessions actually opened,
    # counted from the stub seam itself (_read_sessions), never
    # estimated -- the number PROOF step 2 quotes.
    planner_sessions, worker_sessions = _read_sessions(tmp)
    result["planner_sessions"] = planner_sessions
    result["worker_sessions"] = worker_sessions
    result["runs_root"] = runs_root
    # TEST HARDENING (night run 2026-09-09): whatever the run's own record
    # says happened to the one unit each case declares, so a session count
    # above the naive 1 is never taken on faith -- see retry_record_for.
    result["retry_record"] = retry_record_for(runs_root)
    if proc.returncode != 0:
        result["stderr_tail"] = proc.stderr[-400:]
    return result


def measure():
    out = []
    for fn in (case_docs, case_code, case_docs_eligible,
              case_code_eligible):
        tmp = tempfile.mkdtemp(prefix="tiny-task-cost-")
        out.append(fn(tmp))
    return out


def report(cases):
    lines = ["TINY-TASK COST, measured through scripts/brother_run.py", ""]
    for c in cases:
        lines.append("  case %s: %s (exit %s)"
                     % (c["case"], c["verdict"], c.get("exit_code", NODATA)))
        if c["verdict"] == NODATA:
            lines.append("    %s" % c.get("why", ""))
            continue
        lines.append("    price: wall %ss (engine only, model latency %s)"
                     % (c["wall_clock_seconds"], NODATA))
        lines.append("    price: user steps %d, files written %d in the repo "
                     "and %d in the runs root"
                     % (c["user_steps"], len(c["files_written_in_repo"]),
                        len(c["files_written_in_runs_root"])))
        lines.append("    price said up front: %s (run.log line %s, first "
                     "worker line %s), wait figure: %s"
                     % (c["price_said_up_front"], c["price_line_number"],
                        c["first_worker_log_line"], c["price_states_a_wait"]))
        lines.append("    sessions: planner %d worker %d"
                     % (c.get("planner_sessions", 0),
                        c.get("worker_sessions", 0)))
        # TEST HARDENING (night run 2026-09-09): a worker count above 1 is
        # a retry, and a retry is never silent -- print what the run's own
        # record (retry_record_for, above) says happened, never an
        # estimate. Omitted below 2 workers: nothing retried, nothing to
        # explain.
        if c.get("worker_sessions", 0) > 1:
            retry = c.get("retry_record") or {}
            states = retry.get("attempt_states") or []
            lines.append(
                "    retry: unit %s attempted %s time(s) on disk (claim "
                "store attempt counter %s), each attempt's own recorded "
                "state: %s"
                % (retry.get("unit_id", NODATA),
                   len(states) if states else NODATA,
                   retry.get("claim_attempt", NODATA),
                   states if states else NODATA + ": no attempt trace "
                   "found for this unit"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=None,
                    help="write the measurement to this JSON file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if not os.path.isfile(BROTHER_RUN):
        print("%s: %s is absent, so there is no product path to drive"
              % (NODATA, BROTHER_RUN), file=sys.stderr)
        return 2

    cases = measure()
    doc = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "instrument": "scripts/tiny_task_cost.py",
        "driven": "scripts/brother_run.py, one command per case, the stub "
                  "model seam scripts/product_acceptance.py uses",
        "limits": "model latency is " + NODATA + " under the stub seam. The "
                  "only measurement of a real tiny task's wall clock this "
                  "estate holds is the t7 report of 2026-09-04, 568.03s, "
                  "quoted in README.md's limits section.",
        "cases": cases,
    }
    if args.json:
        print(json.dumps(doc, indent=1, sort_keys=True))
    else:
        print(report(cases))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("")
        print("written: %s" % args.out)
    return 1 if any(c["verdict"] == "FAIL" for c in cases) else 0


if __name__ == "__main__":
    sys.exit(main())
