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


def sh(args, cwd=None, env=None, timeout=180):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def stub_env(tmp, decomposer_body, model_body):
    """DOOR_MODEL_CMD/MODEL_WORKER_CMD pointed at throwaway scripts in `tmp`,
    the identical seam scripts/product_acceptance.py uses."""
    decomposer = tbr.write_stub(tmp, "decomposer.py", decomposer_body)
    model = tbr.write_stub(tmp, "model.py", model_body)
    env = dict(os.environ)
    env["DOOR_MODEL_CMD"] = "%s %s" % (sys.executable, decomposer)
    env["MODEL_WORKER_CMD"] = "%s %s" % (sys.executable, model)
    return env


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
    if proc.returncode != 0:
        result["stderr_tail"] = proc.stderr[-400:]
    return result


def measure():
    out = []
    for fn in (case_docs, case_code):
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
