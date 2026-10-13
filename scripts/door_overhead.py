#!/usr/bin/env python3
"""UX1.a: the door's overhead measured (docs/plan/specs/UX1.md).

Three measures on fixed fixtures, each n times with min, median and max: the session start output in bytes and its
latency, and the time to the first useful action (`brother_run.py --continue` against a temporary runs root). The
simple task bypass answer (fast_path.eligible on a fixture trivial unit) is recorded beside them.

SAMPLE RULES. A run that exits non zero or times out is a sample of None and makes its whole block NO-DATA: a failed
sample is never dropped to improve the median. Fewer than MIN_SAMPLES samples are reported but not claimable, and
compare() gives no percentage from them. Latency is wall time on this machine (time.monotonic around the subprocess,
interpreter start included) and is not comparable across machines.

ISOLATION. Every fixture lives under the caller's temporary root with its own HOME; the engine is always given
--runs-root, so nothing is written into the repository. Python 3.9 compatible, standard library only.
"""
import argparse
import datetime
import io
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SESSIONSTART = os.path.join(REPO, "products", "brothermode", "tools", "bm_sessionstart.py")
BROTHER_RUN = os.path.join(HERE, "brother_run.py")

MIN_SAMPLES = 5
REFERENCE_START_BYTES = 281      # README.md line 63, the 1.1.1 fixture figure
START_CEILING_BYTES = 512        # scripts/test_quiet_start_bm.py test_quiet_is_quiet_in_bytes
NO_RUN_SENTENCE = "no unfinished run found"
RUN_TIMEOUT = 300
TIMING = "wall time in ms, time.monotonic around the subprocess, interpreter start included; this machine only"


class _NoData(Exception):
    """A fixture or a run that could not be made: the block is NO-DATA, never a guess."""


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def summarize(samples):
    """{"n", "min", "median", "max", "samples", "claimable"}. A non list, an empty list, a non number or a negative
    value is refused with ValueError."""
    if not isinstance(samples, (list, tuple)):
        raise ValueError("samples must be a list of numbers, got %s" % type(samples).__name__)
    if not samples:
        raise ValueError("samples is empty")
    for s in samples:
        if not _is_number(s):
            raise ValueError("sample %r is not a finite number" % (s,))
        if s < 0:
            raise ValueError("sample %r is negative" % (s,))
    vals = list(samples)
    return {"n": len(vals), "min": min(vals), "median": statistics.median(vals), "max": max(vals),
            "samples": vals, "claimable": len(vals) >= MIN_SAMPLES}


def _check_args(n, tmp_root):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("n must be a positive int, got %r" % (n,))
    if not isinstance(tmp_root, str) or not os.path.isabs(tmp_root) or not os.path.isdir(tmp_root):
        raise ValueError("tmp_root must be an existing absolute folder, got %r" % (tmp_root,))


def _run_cmd(argv, cwd, env, stdin_bytes=None):
    """(exit code or None on a timeout or a start failure, stdout bytes, elapsed ms). Patched by the tests."""
    start = time.monotonic()
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, input=stdin_bytes if stdin_bytes is not None else b"",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=RUN_TIMEOUT)
        code, out = r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        code, out = None, b""
    return code, out, (time.monotonic() - start) * 1000.0


def _git_env(home):
    return {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def _git(cwd, home, verb):
    """One fixture git step; argv are constants, the place goes through cwd and env."""
    if verb == "init":
        argv = ["git", "init", "-q"]
    elif verb == "add":
        argv = ["git", "add", "-A"]
    else:
        argv = ["git", "commit", "-q", "-m", "seed"]
    code, _, _ = _run_cmd(argv, cwd, _git_env(home))
    if code != 0:
        raise _NoData("git %s failed in the fixture %s" % (verb, cwd))


def _fixture(tmp_root):
    """(home, project) built as RealEstablishedProject.setUp does, under a fresh folder of tmp_root."""
    base = tempfile.mkdtemp(prefix="door-overhead-", dir=tmp_root)
    home = os.path.join(base, "home")
    project = os.path.join(base, "project")
    os.makedirs(os.path.join(home, ".brotherme"))
    os.makedirs(os.path.join(project, "docs", "plan"))
    with io.open(os.path.join(home, ".brotherme", "config.json"), "w", encoding="utf-8") as fh:
        json.dump({"setup_complete": True, "vault_path": os.path.join(home, "Vault"),
                   "privacy_notice_version": "2026-08-01", "installation_mode": "clone",
                   "security_mode": "standard"}, fh)
    with io.open(os.path.join(project, "docs", "plan", "QUEUE.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    _git(project, home, "init")
    return home, project


def _min_env(home):
    return {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"}


def _no_data(why, **raw):
    block = {"status": "NO-DATA", "why": why, "timing": TIMING}
    block.update(raw)
    return block


def measure_start(n, tmp_root):
    """The real session start, n times in the quiet fixture: stdout bytes and wall time per run."""
    _check_args(n, tmp_root)
    if not os.path.isfile(SESSIONSTART):
        return _no_data("session start script missing: %s" % SESSIONSTART, reference_bytes=REFERENCE_START_BYTES)
    try:
        home, project = _fixture(tmp_root)
    except (_NoData, OSError) as exc:
        return _no_data(str(exc), reference_bytes=REFERENCE_START_BYTES)
    sizes, times = [], []
    for _ in range(n):
        code, out, ms = _run_cmd([sys.executable, SESSIONSTART], project, _min_env(home), b"{}")
        ok = code == 0
        sizes.append(len(out) if ok else None)
        times.append(ms if ok else None)
    if None in sizes:
        return _no_data("%d of %d runs exited non zero or timed out" % (sizes.count(None), n),
                        bytes_samples=sizes, latency_samples=times, reference_bytes=REFERENCE_START_BYTES)
    return {"status": "OK", "bytes": summarize(sizes), "latency_ms": summarize(times),
            "reference_bytes": REFERENCE_START_BYTES, "timing": TIMING}


def measure_first_action(n, tmp_root):
    """`brother_run.py --continue` n times against a temporary runs root: wall time and the printed sentence."""
    _check_args(n, tmp_root)
    if not os.path.isfile(BROTHER_RUN):
        return _no_data("engine missing: %s" % BROTHER_RUN)
    try:
        home, project = _fixture(tmp_root)
        runs_root = tempfile.mkdtemp(prefix="runs-", dir=os.path.dirname(project))
    except (_NoData, OSError) as exc:
        return _no_data(str(exc))
    times, seen = [], []
    for _ in range(n):
        argv = [sys.executable, BROTHER_RUN, "--continue", "--cwd", project, "--runs-root", runs_root]
        code, out, ms = _run_cmd(argv, project, _min_env(home))
        said = code == 0 and NO_RUN_SENTENCE in out.decode("utf-8", "replace")
        seen.append(said)
        times.append(ms if said else None)
    if not all(seen):
        return _no_data("%d of %d runs did not print %r" % (seen.count(False), n, NO_RUN_SENTENCE),
                        latency_samples=times, sentence_seen=seen)
    return {"status": "OK", "latency_ms": summarize(times), "sentence_seen": seen, "timing": TIMING}


def _bypass_fixture(tmp_root):
    """A clean git repository with one existing owned file and a done check that does not yet pass."""
    base = tempfile.mkdtemp(prefix="door-bypass-", dir=tmp_root)
    home = os.path.join(base, "home")
    repo = os.path.join(base, "repo")
    os.makedirs(home)
    os.makedirs(os.path.join(repo, "scripts"))
    with io.open(os.path.join(repo, "widget.py"), "w", encoding="utf-8") as fh:
        fh.write("def width():\n    return 2\n")
    with io.open(os.path.join(repo, "scripts", "test_widget.py"), "w", encoding="utf-8") as fh:
        fh.write("import os, sys\n"
                 "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
                 "import widget\n"
                 "assert widget.width() == 3, 'not fixed yet'\n")
    for verb in ("init", "add", "commit"):
        _git(repo, home, verb)
    unit = {"id": "T1", "objective": "make the existing test pass",
            "done_check": "python3 scripts/test_widget.py", "owns": ["widget.py"]}
    return unit, repo


def bypass_unchanged(tmp_root):
    """{"status", "eligible", "reason"} from the real fast_path.eligible on the fixture trivial unit."""
    if not isinstance(tmp_root, str) or not os.path.isabs(tmp_root) or not os.path.isdir(tmp_root):
        raise ValueError("tmp_root must be an existing absolute folder, got %r" % (tmp_root,))
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import fast_path
    except ImportError as exc:
        return {"status": "NO-DATA", "eligible": None, "reason": "fast_path cannot be imported: %s" % exc}
    try:
        unit, repo = _bypass_fixture(tmp_root)
    except (_NoData, OSError) as exc:
        return {"status": "NO-DATA", "eligible": None, "reason": str(exc)}
    ok, why = fast_path.eligible("door overhead bypass fixture", unit, repo)
    if not isinstance(ok, bool):
        return {"status": "NO-DATA", "eligible": None, "reason": "eligible answered %r, not a bool" % (ok,)}
    return {"status": "OK", "eligible": ok, "reason": str(why)}


def _summary_ok(block, name):
    if not isinstance(block, dict):
        raise ValueError("%s must be a summary dict" % name)
    for key in ("n", "min", "median", "max"):
        if not _is_number(block.get(key)):
            raise ValueError("%s lacks a numeric %s" % (name, key))
    if isinstance(block["n"], float):
        raise ValueError("%s n is not an int" % name)
    return block


def compare(before, after):
    """The relative change of medians, only when both blocks hold at least MIN_SAMPLES samples. Overlapping min to
    max ranges claim no difference."""
    b = _summary_ok(before, "before")
    a = _summary_ok(after, "after")
    if b["n"] < MIN_SAMPLES or a["n"] < MIN_SAMPLES:
        return {"percent": None, "why": "fewer than %d samples" % MIN_SAMPLES}
    overlap = b["min"] <= a["max"] and a["min"] <= b["max"]
    if b["median"] == 0:
        return {"percent": None, "overlap": overlap, "why": "the before median is zero"}
    percent = (a["median"] - b["median"]) * 100.0 / b["median"]
    if overlap:
        return {"percent": percent, "overlap": True, "sentence": "no difference is claimed"}
    return {"percent": percent, "overlap": False,
            "sentence": "the median changed by %.1f%% with separated ranges" % percent}


_REQUIRED = ("commit", "date", "machine", "n", "timing", "start", "first_action", "bypass")


def _summary_problems(name, s, n):
    if not isinstance(s, dict):
        return ["%s is not a summary" % name]
    out = []
    if s.get("n") != n or isinstance(s.get("n"), bool):
        out.append("%s n is %r, the document n is %r" % (name, s.get("n"), n))
    samples = s.get("samples")
    if not isinstance(samples, list) or len(samples) != s.get("n"):
        out.append("%s samples do not hold n values" % name)
    claimable = s.get("claimable")
    if not isinstance(claimable, bool):
        out.append("%s claimable is missing" % name)
    elif s.get("percent") is not None and not claimable:
        out.append("%s carries a percentage while claimable is false" % name)
    return out


def check_result(doc):
    """Problems with a result document (a dict or its JSON text); an empty list means it holds."""
    if isinstance(doc, str):
        try:
            doc = json.loads(doc)
        except ValueError:
            return ["the result is not JSON"]
    if not isinstance(doc, dict):
        return ["the result is not a JSON object"]
    missing = [k for k in _REQUIRED if k not in doc]
    if missing:
        return ["missing key: %s" % k for k in missing]
    n = doc["n"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return ["n is %r, not a positive int" % (n,)]
    problems = []
    start, first, bypass = doc["start"], doc["first_action"], doc["bypass"]
    if not isinstance(start, dict) or start.get("reference_bytes") != REFERENCE_START_BYTES:
        problems.append("start lacks reference_bytes %d" % REFERENCE_START_BYTES)
    for name, block, keys in (("start", start, ("bytes", "latency_ms")), ("first_action", first, ("latency_ms",))):
        if not isinstance(block, dict) or block.get("status") not in ("OK", "NO-DATA"):
            problems.append("%s has no status" % name)
        elif block["status"] == "OK":
            for key in keys:
                problems += _summary_problems("%s.%s" % (name, key), block.get(key), n)
    if not isinstance(bypass, dict) or not (bypass.get("eligible") is None or isinstance(bypass.get("eligible"), bool)):
        problems.append("bypass has no eligible answer")
    return problems


def _line(label, s, tail=""):
    return "%s n=%d min=%s median=%s max=%s%s" % (label, s["n"], _fmt(s["min"]), _fmt(s["median"]), _fmt(s["max"]), tail)


def _fmt(x):
    return str(x) if isinstance(x, int) else "%.1f" % x


def _commit():
    code, out, _ = _run_cmd(["git", "rev-parse", "HEAD"], REPO, {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    return out.decode("utf-8", "replace").strip() if code == 0 else "UNKNOWN"


def _positive(text):
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a whole number" % text)
    if n < 1:
        raise argparse.ArgumentTypeError("--n must be at least 1, got %d" % n)
    return n


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    elif not isinstance(argv, list) or any(not isinstance(a, str) for a in argv):
        raise SystemExit(2)
    ap = argparse.ArgumentParser(prog="door_overhead.py", description="Measure the door's overhead.")
    ap.add_argument("--n", type=_positive, default=MIN_SAMPLES)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(list(argv))
    tmp_root = tempfile.mkdtemp(prefix="door-overhead-root-")
    try:
        start = measure_start(args.n, tmp_root)
        first = measure_first_action(args.n, tmp_root)
        bypass = bypass_unchanged(tmp_root)
    finally:
        shutil.rmtree(tmp_root, True)
    u = platform.uname()
    doc = {"commit": _commit(), "date": datetime.datetime.now().isoformat(timespec="seconds"),
           "machine": "%s %s %s" % (u.system, u.release, u.machine), "n": args.n, "timing": TIMING,
           "start": start, "first_action": first, "bypass": bypass}
    if start["status"] == "OK":
        print(_line("start bytes", start["bytes"], " (reference %d)" % REFERENCE_START_BYTES))
        print(_line("start latency_ms", start["latency_ms"], " (%s)" % TIMING))
    else:
        print("start NO-DATA: %s" % start["why"])
    if first["status"] == "OK":
        print(_line("first action latency_ms", first["latency_ms"], " (%s)" % TIMING))
    else:
        print("first action NO-DATA: %s" % first["why"])
    print("bypass %s eligible=%s (%s)" % (bypass["status"], bypass["eligible"], bypass["reason"]))
    if args.out:
        with io.open(args.out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
    return 0 if all(b["status"] == "OK" for b in (start, first, bypass)) else 2


if __name__ == "__main__":
    sys.exit(main())
