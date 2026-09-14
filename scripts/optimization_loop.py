#!/usr/bin/env python3
"""optimization_loop.py: sequences Brother's real Observe/Evaluate/Optimise
pieces into one command (docs/plan/BROTHER-OPTIMIZATION-LOOP-DESIGN-2026-09-14.md
section 3, work package 1).

Every piece this script calls already works standalone; this file is
composition only, never new measurement logic:
  - observe:  scripts/cost_per_unit.py's own token_totals()/commit_count(),
              imported in-process (never shells out to itself, same
              discipline scripts/refresh_cost_board_line.py already uses).
  - evaluate: (a) the vault retrieval gate, if it exists (subprocess: it is
              a separate tool with its own exit-code contract), (b)
              products/brothersbe/tools/sbe_score.py's real
              agent-brief-hygiene / agent-brief-cache-order lines
              (subprocess, SBE_LINT_ROOT set to the repo root, stdout
              parsed), (c) docs/plan/brief-optimizer-scores-2026-09-13.json
              read directly if it exists and is fresh.
  - optimise: scripts/decide.py's own rank() (imported, not reimplemented)
              scores candidate next actions built from whichever evaluate
              checks did not PASS.

NAMING NOTE (the task brief's own instruction): the design doc's work
package 2 names the vault gate "scripts/vault_regression_gate.py". By the
time this script was written, a concurrent build had already landed it as
"scripts/vault_retrieval_gate.py" instead. GATE_CANDIDATES below checks the
design doc's name first, then the name actually found on disk, and reports
which one (if either) exists -- never guesses, never hardcodes only one.

CACHING CHOICE (the task brief left this open, documented here): `optimise`
run on its own (not via --all) reads the most recent `evaluate` run's result
from a small JSON cache written by every `evaluate` run, at
brother_paths.config_path("optimization-loop-last-evaluate.json") -- the same
pattern decide.py itself already uses for its intake sentinel (a file under
the config directory, never inside this repo, so it is not a build artifact
and not something git tracks). `--all` never touches the cache file for its
own optimise step: it passes evaluate's in-memory result straight through, so
a --all run's recommendation can never be stale relative to the run that
produced it.

Python 3.9 floor, standard library only.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cost_per_unit  # noqa: E402
import decide  # noqa: E402
import brother_paths  # noqa: E402

NODATA = "NO-DATA"

#: Design doc's name first, then the name a concurrent build actually landed
#: under. See the NAMING NOTE above.
GATE_CANDIDATES = ("vault_regression_gate.py", "vault_retrieval_gate.py")

SBE_SCORE_REL = os.path.join("products", "brothersbe", "tools", "sbe_score.py")
#: Matches both brief-optimizer-scores-2026-09-13.json (pilot 1) and
#: brief-optimizer-2-scores-2026-09-14.json (pilot 2, and any future pilot
#: N). _check_brief_optimizer_freshness always picks the newest match by
#: mtime, never one hardcoded name.
BRIEF_SCORES_JSON_GLOB = "brief-optimizer*scores-*.json"
STALE_DAYS = 3

# sbe_score.py prints "<name>  <PASS|FAIL|NO-DATA>  <evidence> [severity: soft]"
# with variable padding between fields; \s+ absorbs the padding regardless of
# the column width sbe_score.py chose for this run.
_SBE_LINE_RE = re.compile(
    r"^(agent-brief-hygiene|agent-brief-cache-order)\s+(PASS|FAIL|NO-DATA)\s+(.*?)\s*\[severity: \w+\]\s*$")


def find_vault_gate(scripts_dir=None):
    """(name, path) of whichever GATE_CANDIDATES entry exists under
    `scripts_dir`, name-order first-match; (None, None) if neither does.

    `scripts_dir` defaults to the module-level HERE, resolved INSIDE the
    function body (not as the parameter default) so a test that patches
    `optimization_loop.HERE` actually changes what a defaulted call sees --
    a default bound to HERE at def time would freeze the value this module
    had at import time, exactly the class of bug this repo's own repeat-guard
    flags ("a default argument binds at definition time")."""
    if scripts_dir is None:
        scripts_dir = HERE
    for name in GATE_CANDIDATES:
        path = os.path.join(scripts_dir, name)
        if os.path.isfile(path):
            return name, path
    return None, None


# ---------------------------------------------------------------- observe --

def cmd_observe(days=30, repo=None, scripts_dir=None):
    """Prints the cost-per-unit figure (or a named NO-DATA) plus whether a
    vault gate script exists yet for `evaluate` to use. Returns a dict, never
    raises: every failure path here is a NO-DATA line, not an exception."""
    if repo is None:
        repo = REPO
    if scripts_dir is None:
        scripts_dir = HERE
    result = {"cost": None, "vault_gate_script": None}

    measured, normalized, reason = cost_per_unit.token_totals(days)
    if measured is None:
        print("%s: cost-per-unit: %s" % (NODATA, reason))
    else:
        commits = cost_per_unit.commit_count(repo, days)
        per_commit = cost_per_unit.tokens_per_commit(measured, commits)
        if not commits or per_commit is None:
            print("%s: cost-per-unit: tokens_measured=%s commits=%s (could not "
                  "divide)" % (NODATA, measured, commits))
        else:
            print("OBSERVE cost-per-unit: days=%d tokens_measured=%d commits=%d "
                  "tokens_per_commit=%.1f" % (days, measured, commits, per_commit))
            result["cost"] = {"days": days, "tokens_measured": measured,
                              "commits": commits, "tokens_per_commit": per_commit}

    gate_name, gate_path = find_vault_gate(scripts_dir)
    if gate_path:
        print("OBSERVE vault-gate: available for evaluate (%s)" % gate_name)
        result["vault_gate_script"] = gate_name
    else:
        print("%s: vault-gate: neither %s exists yet under scripts/"
              % (NODATA, " nor ".join(GATE_CANDIDATES)))

    return result


# --------------------------------------------------------------- evaluate --

def _run_vault_gate(scripts_dir=None, run=None):
    """One evaluate result for the vault gate. `run(path) -> CompletedProcess`
    defaults to a real subprocess call; tests inject a fake so this never
    touches the live vault."""
    name, path = find_vault_gate(scripts_dir)  # find_vault_gate resolves HERE itself
    if not path:
        return {"name": "vault-retrieval-gate", "status": NODATA,
                "detail": "neither %s exists yet under scripts/" % " nor ".join(GATE_CANDIDATES)}
    runner = run or (lambda p: subprocess.run(
        [sys.executable, p], capture_output=True, text=True, timeout=180))
    try:
        proc = runner(path)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"name": "vault-retrieval-gate", "status": NODATA,
                "detail": "%s did not run: %s" % (name, e)}
    first_line = (proc.stdout or "").strip().splitlines()[:1]
    detail = "%s (exit=%d): %s" % (name, proc.returncode,
                                   first_line[0] if first_line else "(no output)")
    if proc.returncode == 0:
        status = "PASS"
    elif proc.returncode == 1:
        status = "FAIL"
    else:
        status = NODATA  # the gate's own contract: exit 2 is a boundary failure
    return {"name": "vault-retrieval-gate", "status": status, "detail": detail}


def _run_sbe_checks(repo=None, run=None):
    """Two evaluate results: agent-brief-hygiene and agent-brief-cache-order,
    parsed from sbe_score.py's real stdout. `run(argv, env) ->
    CompletedProcess` defaults to a real subprocess call."""
    if repo is None:
        repo = REPO
    names = ("agent-brief-hygiene", "agent-brief-cache-order")
    sbe_path = os.path.join(repo, SBE_SCORE_REL)
    if not os.path.isfile(sbe_path):
        return [{"name": n, "status": NODATA,
                 "detail": "sbe_score.py not found at %s" % sbe_path} for n in names]

    runner = run or (lambda argv, env: subprocess.run(
        argv, capture_output=True, text=True, timeout=180, env=env))
    env = dict(os.environ, SBE_LINT_ROOT=repo)
    try:
        proc = runner([sys.executable, sbe_path], env)
    except (OSError, subprocess.TimeoutExpired) as e:
        msg = "sbe_score.py did not run: %s" % e
        return [{"name": n, "status": NODATA, "detail": msg} for n in names]

    found = {}
    for line in (proc.stdout or "").splitlines():
        m = _SBE_LINE_RE.match(line.strip())
        if m:
            found[m.group(1)] = (m.group(2), m.group(3).strip())

    results = []
    for n in names:
        if n in found:
            status, detail = found[n]
            results.append({"name": n, "status": status, "detail": detail})
        else:
            results.append({"name": n, "status": NODATA,
                            "detail": "no %r line found in sbe_score.py output" % n})
    return results


def _check_brief_optimizer_freshness(repo=None, now=None):
    """One evaluate result read directly from the newest brief_optimizer_score*.py
    run's own JSON -- read, never regenerated (that script takes real
    wall-clock time). Picks the most-recently-modified file matching
    BRIEF_SCORES_JSON_GLOB rather than one hardcoded filename, because a
    hardcoded name (the original bug, found 2026-09-14: BRIEF_SCORES_JSON_NAME
    pointed at the 2026-09-13 pilot-1 file while a fresher, better pilot-2
    file already existed alongside it) goes stale the moment a second pilot
    ships. NO-DATA, naming the staleness, when no matching file exists,
    the newest one is unreadable, or it is older than STALE_DAYS."""
    if repo is None:
        repo = REPO
    name = "brief-optimizer-freshness"
    plan_dir = os.path.join(repo, "docs", "plan")
    candidates = sorted(
        glob.glob(os.path.join(plan_dir, BRIEF_SCORES_JSON_GLOB)),
        key=lambda p: os.path.getmtime(p), reverse=True)
    if not candidates:
        return {"name": name, "status": NODATA,
                "detail": "no file matching %s under %s" % (BRIEF_SCORES_JSON_GLOB, plan_dir)}
    path = candidates[0]

    age_days = ((now() if now else time.time()) - os.path.getmtime(path)) / 86400.0
    if age_days > STALE_DAYS:
        return {"name": name, "status": NODATA,
                "detail": "%s is %.1f days old, older than the %d-day freshness floor"
                          % (path, age_days, STALE_DAYS)}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        return {"name": name, "status": NODATA, "detail": "%s is not readable JSON (%s)" % (path, e)}

    variants = doc.get("variants") or {}
    total = passed = 0
    for variant in variants.values():
        for task in (variant.get("tasks") or {}).values():
            total += 1
            if task.get("pass"):
                passed += 1
    if total == 0:
        return {"name": name, "status": NODATA, "detail": "%s carries no tasks" % path}
    status = "PASS" if passed == total else "FAIL"
    return {"name": name, "status": status,
            "detail": "%d/%d tasks passed across %d variant(s) (age %.1f days)"
                      % (passed, total, len(variants), age_days)}


def _cache_path():
    return brother_paths.config_path("optimization-loop-last-evaluate.json")


def _save_evaluate_cache(results):
    """Best-effort only: a cache write failure must not fail evaluate itself,
    it only means a later standalone `optimise` sees NO-DATA."""
    try:
        with open(_cache_path(), "w", encoding="utf-8") as fh:
            json.dump({"results": results, "written_at_epoch": time.time()}, fh)
    except OSError:  # sbe: allow-silent documented above: best-effort cache, a miss just means NO-DATA later
        pass


def _load_evaluate_cache():
    path = _cache_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc.get("results")


def cmd_evaluate(repo=None, scripts_dir=None, run_gate=None, run_sbe=None, now=None):
    """Runs all three evaluate checks (four printed lines: the gate, the two
    agent-brief lines, and brief-optimizer freshness), prints one
    PASS/FAIL/NO-DATA line per check, caches the result for a later
    standalone `optimise`, and returns (results, exit_code). exit_code is 1
    if any check FAILed, else 0 -- NO-DATA never fails the command, since a
    missing piece is a gap to report, not a broken one."""
    if repo is None:
        repo = REPO
    if scripts_dir is None:
        scripts_dir = HERE
    results = [_run_vault_gate(scripts_dir, run_gate)]
    results.extend(_run_sbe_checks(repo, run_sbe))
    results.append(_check_brief_optimizer_freshness(repo, now))

    for r in results:
        print("%s: %s -- %s" % (r["name"], r["status"], r["detail"]))

    _save_evaluate_cache(results)
    fails = sum(1 for r in results if r["status"] == "FAIL")
    return results, (1 if fails else 0)


# --------------------------------------------------------------- optimise --

#: How much each evaluate check's gap matters if left unresolved, and where a
#: session would go to fix it. Reused as decide.py-style raw marks (0-10):
#: decide.rank() does the actual weighting, this script only supplies marks.
_LEVER = {"vault-retrieval-gate": 9, "agent-brief-hygiene": 6,
          "agent-brief-cache-order": 6, "brief-optimizer-freshness": 5}
_COST_IF_SKIPPED = {"vault-retrieval-gate": 8, "agent-brief-hygiene": 5,
                    "agent-brief-cache-order": 5, "brief-optimizer-freshness": 4}
_FILE_FOR = {
    "vault-retrieval-gate": "scripts/vault_retrieval_gate.py (design doc names it "
                            "scripts/vault_regression_gate.py; neither exists yet if this "
                            "gap says NO-DATA for a missing script)",
    "agent-brief-hygiene": "the agent brief(s) named on the evaluate line above, under */agents/*.md",
    "agent-brief-cache-order": "the agent brief(s) named on the evaluate line above, under */agents/*.md",
    "brief-optimizer-freshness": "docs/plan/brief-optimizer-scores-2026-09-13.json "
                                 "(re-run scripts/brief_optimizer_score.py to refresh it)",
}


def build_recommendation_spec(results):
    """A decide.py-shaped spec (criteria + options), or None when every
    evaluate check PASSed and there is nothing to recommend. Every option
    names the file/gap it addresses (_FILE_FOR), per the task's own
    requirement -- never a recommendation with nowhere to look."""
    options = [
        {
            "id": r["name"],
            "name": "Address %s (%s)" % (r["name"], r["status"]),
            "one_liner": r["detail"],
            "scores": {"lever": _LEVER.get(r["name"], 5),
                      "cost_if_skipped": _COST_IF_SKIPPED.get(r["name"], 5)},
            "score_basis": {
                "lever": "how directly closing this gap unblocks the observe-evaluate-optimise loop",
                "cost_if_skipped": "cost of evaluate re-finding the same gap on every future run",
            },
            "file": _FILE_FOR.get(r["name"], NODATA),
        }
        for r in results if r["status"] != "PASS"
    ]
    if not options:
        return None
    criteria = [
        {"key": "lever", "label": "Lever", "weight": 0.5,
         "why": "how directly fixing this closes the loop"},
        {"key": "cost_if_skipped", "label": "Cost if skipped", "weight": 0.5,
         "why": "cost of leaving this unresolved as evaluate keeps re-finding it"},
    ]
    return {"title": "Optimise: what to fix next", "criteria": criteria, "options": options}


def cmd_optimise(results=None):
    """Prints a ranked recommendation built from `results` (an evaluate run's
    own output), or from the most recent cached `evaluate` run when `results`
    is None. Prints "NO-DATA: no evaluate result found, run evaluate first"
    and returns 0 (never a crash) when neither is available."""
    if results is None:
        results = _load_evaluate_cache()
    if not results:
        print("%s: no evaluate result found, run evaluate first" % NODATA)
        return 0

    spec = build_recommendation_spec(results)
    if spec is None:
        print("PASS: every evaluate check passed; nothing to recommend")
        return 0

    # Reuses decide.py's own scoring/ranking, never reimplemented.
    _criteria, _weight_note, scored, close = decide.rank(spec)
    print("OPTIMISE: recommendations ranked by decide.py's own weighted score%s"
          % (" (top two are a close call, per decide.py's CLOSE_MARGIN)" if close else ""))
    for i, s in enumerate(scored):
        opt = s["option"]
        print("  %d. [%.2f] %s" % (i + 1, s["total"], opt["name"]))
        print("       gap: %s" % opt["one_liner"])
        print("       file: %s" % opt["file"])
    return 0


# -------------------------------------------------------------------- CLI --

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", choices=["observe", "evaluate", "optimise"],
                    default=None)
    ap.add_argument("--all", action="store_true",
                    help="run observe, evaluate, optimise in sequence, chaining "
                         "evaluate's in-memory result straight into optimise")
    ap.add_argument("--days", type=int, default=30,
                    help="trailing window for cost-per-unit (observe only)")
    args = ap.parse_args(argv)

    if args.all:
        cmd_observe(args.days)
        print("")
        results, _code = cmd_evaluate()
        print("")
        return cmd_optimise(results)

    if args.command == "observe":
        cmd_observe(args.days)
        return 0
    if args.command == "evaluate":
        _results, code = cmd_evaluate()
        return code
    if args.command == "optimise":
        return cmd_optimise()

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
