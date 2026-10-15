#!/usr/bin/env python3
"""PG1.f: the one command from the closing ceremony's seed to a loop that is building.

    python3 scripts/plan_pipeline.py --seed docs/plan/seeds/1.1.5/SEED.json --version 1.1.5 [--launch]
                                     [--writer-cmd "CMD ... {brief} ..."] [--root DIR]

Stages, in order, each timed and recorded: seed (the seed read and validated, in process), generate
(scripts/plan_gen.py), briefs (scripts/plan_briefs.py, the wave table), launch (scripts/loop/start_run.sh with the
plan as BROTHER_WBS, BROTHER_SPEC_FLOOR=9 and the grant's deadline and budget, only with --launch). The loop admits
each sub unit at its next pass once the pool scores it 9, so the first unit builds while the later ones are still
being written: there is no barrier on the whole plan. A writer command, when given, is started once per brief of
the FIRST wave (an argv list, never a shell, "{brief}" replaced by the brief path) before the launch and joined
after it; later waves are the session's to dispatch from the printed table. Every stage's clocks go to
docs/plan/specs/<V>/PIPELINE.json (schema brother-plan-pipeline/1), which PG1.g reads.

FAIL DIRECTION: the first non zero stage stops the run, and nothing paid starts after it. The launch is refused
before anything is written when the grant's budget or deadline reads NO-DATA (the owner's words are the only source;
nothing is defaulted). A child that cannot start is exit 127, a child over the cap exit 124; neither is a pass.
Exit 0 done, 1 refused, 2 NO-DATA (the seed cannot be read). Nothing here tags, pushes or publishes; start_run.sh and
loop_intake.py are run, never edited. Python 3.9, standard library only. Tests: scripts/test_plan_pipeline.py.
"""
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import calendar
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plan_briefs  # noqa: E402
import plan_seed  # noqa: E402

STAGES = ("seed", "generate", "briefs", "launch")
SCHEMA = "brother-plan-pipeline/1"
SPEC_FLOOR = "9"  # the loop launches on the plan with the floor at 9: the pool is the stream
TIMEOUT_S = 3600  # one child's wall clock cap
STALE_SEED_S = 24 * 3600  # an older seed still launches; its age goes in the record
_LOG_LOCK = threading.Lock()
_WAVES_LINE = re.compile(r"^WAVES: (\d+) width (\d+)\b", re.M)


def _words(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not value.strip().startswith(plan_seed.NO_DATA)


def grant_env(seed: dict) -> Dict[str, str]:
    """The environment the loop launch needs from the seed's grant: the budget and deadline the owner's words name,
    plus BROTHER_SPEC_FLOOR=9 and BROTHER_WBS; a grant field that reads NO-DATA raises ValueError naming it (the
    owner's words are the only source; nothing is defaulted). The keys BROTHER_SPEC_FLOOR and BROTHER_WBS are
    environment variables of the child; '--deadline' and '--budget-usd' are the values of the flags start_run.sh
    reads (it reads no environment variable for either), carried verbatim for the loop's own parser to judge."""
    grant = seed.get("grant") if isinstance(seed, dict) else None
    if not isinstance(grant, dict):
        raise ValueError("the seed carries no grant record")
    missing = [k for k in ("budget", "deadline") if not _words(grant.get(k))]
    if missing:
        raise ValueError("the grant reads NO-DATA for %s: only the owner's words may grant it" % " and ".join(missing))
    version = seed.get("version")
    if not isinstance(version, str) or not plan_seed.VERSION_RE.match(version):
        raise ValueError("the seed version %r is not N.N.N" % (version,))
    return {"BROTHER_SPEC_FLOOR": SPEC_FLOOR, "BROTHER_WBS": "docs/plan/BROTHER-%s-WBS.json" % version,
            "--deadline": grant["deadline"], "--budget-usd": grant["budget"]}


def launch_argv(genv: Dict[str, str], root: str) -> List[str]:
    """The start_run.sh command line for a grant_env result, an argv list."""
    return ["bash", os.path.join(root, "scripts", "loop", "start_run.sh"), "--plan", genv["BROTHER_WBS"],
            "--deadline", genv["--deadline"], "--budget-usd", genv["--budget-usd"]]


def _default_runner(argv: List[str], cwd: Optional[str], env: Optional[Dict[str, str]]) -> Tuple[int, str]:
    full = dict(os.environ)
    full.update(env or {})
    try:
        done = subprocess.run(argv, cwd=cwd, env=full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, "timed out after %d s" % TIMEOUT_S
    return done.returncode, done.stdout.decode("utf-8", "replace")


def run_stage(name: str, argv: List[str], runner: Callable, log: str, cwd: Optional[str] = None,
              env: Optional[Dict[str, str]] = None) -> dict:
    """Runs one stage as a child (runner(argv, cwd, env) returns (exit code, output)), appends its output to `log`,
    and returns {stage, exit, seconds, started, finished} (epoch floats, plus output_tail and error); a child that
    cannot start is exit 127 with the error in the record, and an exit code that is not an integer is 127 too."""
    started, err = time.time(), ""
    try:
        code, out = runner(argv, cwd, env)
        if isinstance(code, bool) or not isinstance(code, int) or not isinstance(out, str):
            raise ValueError("the runner answered %r, not (exit code, output text)" % ((code, out),))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        code, out, err = 127, "", "%s: %s" % (type(exc).__name__, exc)
    finished = time.time()
    rec = {"stage": name, "exit": code, "seconds": round(finished - started, 3), "started": started, "finished": finished,
           "output_head": out[:4000], "output_tail": out[-600:]}
    if err:
        rec["error"] = err
    try:
        with _LOG_LOCK, open(log, "a", encoding="utf-8") as f:
            f.write("== %s exit %s: %s\n%s\n" % (name, code, " ".join(argv), out))
    except OSError as exc:
        rec["log_error"] = type(exc).__name__
    return rec


def _stage_record(name: str, started: float, code: int, detail: str) -> dict:
    finished = time.time()
    return {"stage": name, "exit": code, "seconds": round(finished - started, 3), "started": started, "finished": finished,
            "output_head": detail, "output_tail": detail}


def _seed_age(seed: dict, now: float) -> object:
    try:
        return int(now - calendar.timegm(time.strptime(str(seed.get("generated_at")), "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return plan_seed.NO_DATA


def _check_seed(seed_path: str, version: str, launch: bool, writer_cmd: Optional[str]) -> Tuple[int, str, Optional[dict]]:
    """(exit code, detail, seed): the cheap refusals first, before any stage writes or spends anything."""
    if not isinstance(version, str) or not plan_seed.VERSION_RE.match(version):
        return 1, "version %r is not N.N.N" % (version,), None
    try:
        with open(seed_path, encoding="utf-8") as f:
            seed = json.load(f)
    except OSError as exc:
        return 2, "NO-DATA: the seed %s cannot be read (%s)" % (seed_path, type(exc).__name__), None
    except ValueError as exc:
        return 1, "the seed %s is not JSON (%s)" % (seed_path, exc), None
    if not isinstance(seed, dict) or seed.get("schema") != plan_seed.SEED_SCHEMA:
        return 1, "the seed is not a %s record" % plan_seed.SEED_SCHEMA, None
    if seed.get("version") != version:
        return 1, "the seed is for release %r, not %s" % (seed.get("version"), version), None
    if not isinstance(seed.get("units"), list):
        return 1, "the seed units are not a list", None
    if writer_cmd is not None:
        try:
            if "{brief}" not in shlex.split(writer_cmd):
                return 1, "the writer command holds no {brief} word to substitute", None
        except ValueError as exc:
            return 1, "the writer command does not parse (%s)" % exc, None
    if launch:
        try:
            grant_env(seed)
        except ValueError as exc:
            return 1, "the launch is refused: %s" % exc, None
    return 0, "seed ok", seed


def _move_aside(path: str, now: float) -> None:
    """A PIPELINE.json from an earlier run is kept beside the new one: .corrupt-<epoch> when it cannot be read as a
    record (never read), .prev-<epoch> when it can. Nothing is deleted."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            ok = json.load(f).get("schema") == SCHEMA
    except (OSError, ValueError, AttributeError):
        ok = False
    os.replace(path, "%s.%s-%d" % (path, "prev" if ok else "corrupt", int(now)))


def pipeline(seed_path: str, version: str, launch: bool, runner: Callable, writer_cmd: Optional[str], root: str) -> dict:
    """seed (read and validated), generate (plan_gen.py), briefs (plan_briefs.py, the wave table), launch (start_run.sh
    under grant_env, only with launch=True) in that order, stopping at the first non zero exit; writes
    docs/plan/specs/<V>/PIPELINE.json (schema brother-plan-pipeline/1: the stage records, the width, the arithmetic
    line, the seed's generated_at) and returns it. writer_cmd, when given, is run once per brief of the first wave
    with the brief path substituted; when absent the wave table is printed for the session to dispatch."""
    t0 = time.time()
    rec = {"schema": SCHEMA, "version": version, "seed": seed_path, "launch_requested": bool(launch), "started": t0,
           "stages": [], "writers": [], "width": plan_seed.NO_DATA, "arithmetic": plan_seed.NO_DATA, "launch_line": "",
           "seed_generated_at": plan_seed.NO_DATA, "seed_age_s": plan_seed.NO_DATA, "exit": 0}
    valid = isinstance(version, str) and bool(plan_seed.VERSION_RE.match(version))
    spec_dir = os.path.join("docs", "plan", "specs", version) if valid else None
    record_path = os.path.join(root, spec_dir, "PIPELINE.json") if valid else None
    log = os.path.join(root, spec_dir, "PIPELINE.log") if valid else os.devnull
    if valid:
        os.makedirs(os.path.join(root, spec_dir), exist_ok=True)
        _move_aside(record_path, t0)
    threads, writers = [], []
    try:
        code, detail, seed = _check_seed(seed_path, version, launch, writer_cmd)
        rec["stages"].append(_stage_record("seed", t0, code, detail))
        if code == 0:
            rec["seed_generated_at"] = str(seed.get("generated_at", plan_seed.NO_DATA))
            rec["seed_age_s"] = _seed_age(seed, t0)
            wbs_rel = "docs/plan/BROTHER-%s-WBS.json" % version
            assign_rel = os.path.join(spec_dir, "WRITER-ASSIGNMENTS.json")
            briefs_rel = os.path.join(spec_dir, "briefs")
            py = sys.executable or "python3"
            steps = [("generate", [py, "-B", os.path.join(HERE, "plan_gen.py"), "--seed", seed_path, "--version", version, "--root", root]),
                     ("briefs", [py, "-B", os.path.join(HERE, "plan_briefs.py"), "--assignments", os.path.join(root, assign_rel),
                                 "--out", os.path.join(root, briefs_rel), "--root", root])]
            genv = grant_env(seed) if launch else None
            if launch:
                steps.append(("launch", launch_argv(genv, root)))
            else:
                try:
                    rec["launch_line"] = "BROTHER_SPEC_FLOOR=%s BROTHER_WBS=%s %s" % (
                        SPEC_FLOOR, wbs_rel, " ".join(shlex.quote(a) for a in launch_argv(grant_env(seed), root)))
                except ValueError as exc:
                    rec["launch_line"] = "NO-DATA: the launch would be refused: %s" % exc
            for name, argv in steps:
                env = {k: v for k, v in genv.items() if k.startswith("BROTHER_")} if name == "launch" else None
                if name == "briefs" and _nothing_to_write(seed, os.path.join(root, assign_rel)):
                    # Every unit is CARRIED (its spec exists and scored before): no batch, no writer, and the loop may
                    # launch at once. A plan with any fresh unit still goes through plan_briefs, whose own zero batch
                    # answer stays NO-DATA.
                    rec["stages"].append({"stage": "briefs", "exit": 0, "seconds": 0.0, "argv": [],
                                          "output_head": "WAVES: 0 (every unit is carried: its spec exists, nothing to write)",
                                          "output_tail": ""})
                    continue
                stage = run_stage(name, argv, runner, log, cwd=root, env=env)
                rec["stages"].append(stage)
                if stage["exit"] != 0:
                    break  # the first refusal stops the run: nothing paid starts after it
                if name == "briefs":
                    m = _WAVES_LINE.search(stage["output_head"])
                    if m:
                        rec["width"] = int(m.group(2))
                    arith = [l for l in stage["output_tail"].splitlines() if l.startswith("ARITHMETIC: ")]
                    if arith:
                        rec["arithmetic"] = arith[-1]
                    writers = _first_wave(os.path.join(root, assign_rel), os.path.join(root, briefs_rel)) if writer_cmd else []
                    for bid, path in writers:
                        argv_w = [tok.replace("{brief}", path) for tok in shlex.split(writer_cmd)]
                        slot = {}
                        t = threading.Thread(target=lambda a=argv_w, b=bid, s=slot: s.update(
                            run_stage("writer:" + b, a, runner, log, cwd=root)))
                        t.start()
                        threads.append((t, slot))
    finally:
        for t, slot in threads:
            t.join()
            rec["writers"].append(slot)
        rec["finished"] = time.time()
        bad = [s["exit"] for s in rec["stages"] if s["exit"] != 0]
        rec["exit"] = bad[0] if bad else 0
        if valid:
            with open(record_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(rec, indent=1) + "\n")
    return rec


def _nothing_to_write(seed: dict, assignments: str) -> bool:
    """True only when every seed unit is carried and the generated batch file holds zero batches."""
    units = seed.get("units") if isinstance(seed, dict) else None
    if not isinstance(units, list) or not units or any(not isinstance(u, dict) or u.get("carried") is not True for u in units):
        return False
    try:
        return plan_briefs._load(assignments)["batches"] == []
    except Exception:  # an unreadable batch file is plan_briefs' NO-DATA to report, never a skip
        return False


def _first_wave(assignments: str, briefs_dir: str) -> List[Tuple[str, str]]:
    """(batch id, brief path) for every batch of the first wave, by the same width and order plan_briefs prints."""
    batches = plan_briefs._load(assignments)["batches"]
    table = plan_briefs.waves(batches, plan_briefs.width(len(batches)))
    return [(b["id"], os.path.join(briefs_dir, b["id"] + ".md")) for b in table[0]] if table else []


def main(argv: Optional[List[str]] = None, runner: Optional[Callable] = None) -> int:
    ap = argparse.ArgumentParser(description="PG1.f: seed to generator to briefs to loop launch")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--launch", action="store_true", help="also start the loop (the grant must carry budget and deadline)")
    ap.add_argument("--writer-cmd", default=None)
    ap.add_argument("--root", default=os.getcwd())
    try:
        args = ap.parse_args(argv)
        args.root = os.path.abspath(args.root)  # a relative root broke the lint overlay (2026-10-14)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    rec = pipeline(args.seed, args.version, args.launch, runner or _default_runner, args.writer_cmd, args.root)
    for s in rec["stages"]:
        print("STAGE %s exit %s %.2f s" % (s["stage"], s["exit"], s["seconds"]))
        if s["exit"] != 0 and s.get("output_tail"):
            print(s["output_tail"].rstrip())
    for w in rec["writers"]:
        print("WRITER %s exit %s %.2f s" % (w["stage"], w["exit"], w["seconds"]))
    if rec["exit"] != 0:
        print("PIPELINE STOPPED at %s, exit %s; nothing after it ran" % (rec["stages"][-1]["stage"], rec["exit"]))
        return rec["exit"] if rec["exit"] in (1, 2) else 1
    if not args.launch:
        print("LAUNCH (not started, add --launch): %s" % rec["launch_line"])
    print("PIPELINE: stages %d width %s %s" % (len(rec["stages"]), rec["width"], rec["arithmetic"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
