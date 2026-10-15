"""perturb_pool.py (C0.4a): the release note's perturbation rows, each run in a
THROWAWAY GIT CLONE, several at a time, with the same verdict as the serial
measurement in the live tree.

WHY. release_note_from_tree.measured_file_rows breaks each subject file in the
LIVE tree, runs its whole suite, restores the file, one row after another.
Measured 2026-09-20 on the 1.0.21 cut: 34 rows, 17 of them on one suite that
takes 116 to 128 seconds per perturbed run, about 50 of the chain's 83
minutes. The live tree is also the source of most of the hazards the C0.1 red
team found (a child writing after the restore, a server alive across it, the
restore ledger race): in a copy that is thrown away there is no restore.

RULES (docs/plan/specs/C0.md W1 to W6, each from a measurement):
  W1 a copy is a real git clone at a named commit. A plain file copy fails
     four brother_run tests on a clean baseline.
  W2 no copy path and no TMPDIR contains "/tmp": a brother_run test asserts so.
  W3 the baseline wall is given by the caller from a quiet run, never the
     300 second default: four clean baselines timed out at load 36.
  W4 the worker count is capped by measured load, not by core count.
  W5 a copy is removed by its exact path, and only if this module made it.
  W6 no copy is made under the disk floor.
A copy that cannot be made, or a baseline that is not green, is NO-DATA (None)
for every row of that worker: never a skip, never a pass (H2).

The verdict function is release_note_perturb.covers, unchanged, with
root=<copy>. This module adds no verdict logic.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "loop"))   # the one load rule ships with the loop tools
from load_rule import load_reading, machine_busy  # noqa: E402,F401  (the estate's one load rule, REQ-LA5-B-03)

HERE = os.path.dirname(os.path.abspath(__file__))
#: Written BESIDE the copy, never inside it: an untracked file inside would
#: make the clone's own git status dirty, and a suite may look at that.
MARKER_SUFFIX = ".made-by-perturb-pool"
DISK_FLOOR_GIB = 15
FORBIDDEN_PATH_TEXT = "/tmp"


class CopyFailed(Exception):
    pass


def clean_env(extra=None):
    """The child's environment: no GIT_ variable (a hook launched parent must
    not aim the child's git at the real repository), plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(extra or {})
    return env


def _git(args, cwd):
    proc = subprocess.run(["git"] + args, cwd=cwd, env=clean_env(),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise CopyFailed("git %s failed: %s" % (" ".join(args[:2]),
                                                (proc.stderr or proc.stdout).strip()[-200:]))
    return proc.stdout.strip()


def free_gib(path):
    return shutil.disk_usage(path).free / (1024 ** 3)


def make_copy(source_root, dest, floor_gib=DISK_FLOOR_GIB, free=free_gib):
    """W1, W2, W6. Returns the commit the copy stands at. `dest` must not exist."""
    if FORBIDDEN_PATH_TEXT in dest:
        raise CopyFailed("W2: %r contains %r" % (dest, FORBIDDEN_PATH_TEXT))
    parent = os.path.dirname(dest.rstrip(os.sep))
    have = free(parent)
    if have < floor_gib:
        raise CopyFailed("W6: %.1f GiB free under %s, floor is %d" % (have, parent, floor_gib))
    sha = _git(["rev-parse", "HEAD"], source_root)
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], source_root)
    _git(["clone", "-q", "--local", "--no-checkout", common, dest], parent)
    _git(["checkout", "-q", "--detach", sha], dest)
    with open(dest.rstrip(os.sep) + MARKER_SUFFIX, "w", encoding="utf-8") as fh:
        fh.write(sha + "\n")
    return sha


def floor_problem(path, floor_gib=DISK_FLOOR_GIB, free=None):
    """FR1.d2: '' when `path` has at least `floor_gib` GiB free, else the NO-DATA reason. An unreadable reading blocks.
    Asked by the caller before any baseline runs, so a refusal costs no minutes (make_copy keeps its own W6 check)."""
    free = free or free_gib
    try:
        have = free(path)
    except (OSError, ValueError, TypeError) as exc:
        return "NO-DATA: the free space under %s cannot be read (%s), floor is %d, read before any baseline" % (
            path, exc, floor_gib)
    if have < floor_gib:
        return "NO-DATA: %.1f GiB free under %s, floor is %d, read before any baseline" % (have, path, floor_gib)
    return ""


def remove_copy(path):
    """W5: exact path, and only a directory this module made (its marker
    file stands beside it)."""
    marker = path.rstrip(os.sep) + MARKER_SUFFIX
    if not os.path.isfile(marker):
        raise CopyFailed("W5: no %s beside %r, so this module did not make it" % (MARKER_SUFFIX, path))
    shutil.rmtree(path)
    os.remove(marker)


def capped_workers(requested, load1=None, ncpu=None):
    """W4. Below one runnable process per core the request stands (up to the
    core count); at or above it, one worker. The one comparison is machine_busy's,
    the estate's own load rule, so this cap and the loop pool's load hold can never
    disagree at the boundary (REQ-LA5-B-03)."""
    ncpu = ncpu or os.cpu_count() or 1
    if machine_busy(load1, ncpu):
        return 1
    return max(1, min(int(requested), ncpu))


def default_workers(ncpu=None):
    """CF1.a: cores minus one, so one core stays free for the rest of the
    machine. A missing, bool, non-int or sub-one count reads as 1 core, which
    only ever means fewer workers (slower, never wrong). The settle still
    caps the request in run_rows."""
    if ncpu is None:
        ncpu = os.cpu_count() or 1
    if isinstance(ncpu, bool) or not isinstance(ncpu, int) or ncpu < 1:
        ncpu = 1
    return max(1, ncpu - 1)


#: W4b (2026-10-12): the live baselines that run just before the pool push the
#: load over the core count, so one reading taken at that instant capped two
#: rehearsals to ONE worker for all 42 rows (7,081 s and 7,248 s for step 2b).
#: The load average decays within a minute once they end, so the count is read
#: again for up to this many seconds before it is fixed. It only ever waits
#: while the reading is below what an idle machine would get; it never raises
#: the count above capped_workers' own answer.
LOAD_WAIT_S = 120


def settled_workers(requested, wait_s=LOAD_WAIT_S, cap=None, sleep=None, clock=None):
    import time
    cap = cap or capped_workers
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    want = cap(requested, load1=0.0)
    n = cap(requested)
    end = clock() + wait_s
    while n < want and clock() < end:
        sleep(5)
        n = cap(requested)
    return n


def run_worker(copy, rows, baseline_wall, out_path):
    """Inside the copy: one baseline per suite, then covers() per row."""
    sys.path.insert(0, os.path.join(copy, "scripts"))
    import release_note_perturb as P
    baselines, results = {}, []
    for file_rel, suite_rel in rows:
        if suite_rel not in baselines:
            baselines[suite_rel] = P.run_suite(suite_rel, root=copy, timeout=baseline_wall)
        rc, tail = baselines[suite_rel]
        if rc != 0:
            results.append([file_rel, suite_rel, None,
                            "baseline in the copy is not green (%s): %s" % (rc, tail)])
        else:
            verdict, detail = P.covers(suite_rel, file_rel, root=copy, baseline=rc)
            results.append([file_rel, suite_rel, verdict, detail])
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(results, fh)


#: FR1.f (C7): how often the row watcher reads the workers' answer files.
ROW_POLL_S = 5.0
_WORD = {True: "red", False: "NOT red", None: "NO-DATA"}


def _say(text):
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _watch_rows(watched, total, say, stop, poll_s=None, clock=None):
    """FR1.f: say one line per newly answered row, read from each worker's
    out<i>.json (a torn read is skipped until the next poll). One last read
    runs after `stop` is set, so a worker that ended between polls is still
    heard. `watched` is a list of out paths that run_rows appends to. A
    line is information only: nothing reads it."""
    clock = clock or time.monotonic
    start, seen = clock(), set()
    poll_s = ROW_POLL_S if poll_s is None else poll_s
    while True:
        ended = stop.wait(poll_s)
        for path in list(watched):
            try:
                with open(path, encoding="utf-8") as fh:
                    got = json.load(fh)
                items = [(r[0], r[1], r[2]) for r in got]
            except (OSError, ValueError, IndexError, TypeError):
                continue
            for file_rel, suite_rel, verdict in items:
                if (file_rel, suite_rel) in seen:
                    continue
                seen.add((file_rel, suite_rel))
                say("row %d/%d %s -> %s %s after %d s"
                    % (len(seen), total, file_rel, suite_rel,
                       _WORD.get(verdict, "NO-DATA"), int(clock() - start)))
        if ended:
            return


def _load_text():
    try:
        return "%.1f" % os.getloadavg()[0]
    except (OSError, AttributeError):
        return "unknown"


def run_rows(rows, source_root, baseline_wall, workers=2, base=None,
             spawn=None, copier=make_copy, cap=None, say=None):
    """[(file_rel, suite_rel, verdict, detail)] in the order of `rows`.
    verdict is covers()'s own True / False / None.

    FR1.d2: `slots` slices are made round robin; the first wave admits as many
    as settled_workers allows, and each time a worker ends the cap is read
    again (`cap` or capped_workers, never above the request) and the free
    places start the waiting slices, merged round robin when fewer places are
    free than slices wait. The cap only ever admits; it never stops a worker."""
    rows = [tuple(r) for r in rows]
    if not rows:
        return []
    say = say or _say
    cap = cap or capped_workers
    requested = max(1, int(workers))
    slots = min(requested, len(rows))
    waiting = [rows[i::slots] for i in range(slots)]
    own_base = base is None
    base = base or tempfile.mkdtemp(prefix="perturb-pool-")
    first = max(1, min(int(settled_workers(workers)), slots))
    watched, stop = [], threading.Event()
    watcher = threading.Thread(target=_watch_rows, daemon=True,
                               args=(watched, len(rows), say, stop))
    try:
        watcher.start()
    except RuntimeError as exc:
        watcher = None
        sys.stderr.write("perturb_pool: no row watcher: %s\n" % exc)
    spawn = spawn or (lambda argv, env: subprocess.Popen(argv, env=env,
                                                         stdout=subprocess.DEVNULL,
                                                         stderr=subprocess.PIPE))
    answers, running, made = {}, [], [0]

    def admit(limit):
        """Start waiting slices until `limit` workers run; a slice whose copy
        cannot be made is answered NO-DATA and takes no place."""
        started = 0
        while waiting and started < limit:
            mine = waiting.pop(0)
            i = made[0]
            made[0] += 1
            copy = os.path.join(base, "copy%d" % i)
            scratch = os.path.join(base, "scratch%d" % i)
            try:
                copier(source_root, copy)
                os.makedirs(scratch)
            except (CopyFailed, OSError) as exc:
                for file_rel, suite_rel in mine:
                    answers[(file_rel, suite_rel)] = (None, "no work copy: %s" % exc)
                continue
            rows_path = os.path.join(base, "rows%d.json" % i)
            out_path = os.path.join(base, "out%d.json" % i)
            with open(rows_path, "w", encoding="utf-8") as fh:
                json.dump(mine, fh)
            proc = spawn([sys.executable, os.path.abspath(__file__), "--worker", copy,
                          rows_path, str(baseline_wall), out_path],
                         clean_env({"TMPDIR": scratch}))
            running.append((mine, copy, out_path, proc))
            watched.append(out_path)
            started += 1
        say("workers: %d requested, %d admitted, load %s" % (requested, len(running), _load_text()))

    try:
        admit(first)
        while running:
            mine, copy, out_path, proc = running.pop(0)
            err = proc.communicate()[1]
            done = {}
            if os.path.isfile(out_path):
                with open(out_path, encoding="utf-8") as fh:
                    done = {(r[0], r[1]): (r[2], r[3]) for r in json.load(fh)}
            for key in mine:
                answers[key] = done.get(key, (None, "the worker ended before this row (exit %s): %s"
                                              % (proc.returncode, (err or b"").decode("utf-8", "replace")[-200:])))
            try:
                remove_copy(copy)
            except (CopyFailed, OSError) as exc:
                sys.stderr.write("perturb_pool: left behind %s: %s\n" % (copy, exc))
            if not waiting:
                continue
            free = max(1, min(int(cap(workers)), slots)) - len(running)
            if not running:
                free = max(free, 1)
            if free <= 0:
                continue
            if free < len(waiting):
                was = len(waiting)
                waiting[:] = [[r for part in waiting[j::free] for r in part] for j in range(free)]
                say("workers: %d waiting slice(s) merged into %d worker(s)" % (was, free))
            admit(len(waiting))
        return [(f, s) + answers[(f, s)] for f, s in rows]
    finally:
        stop.set()
        if watcher is not None:
            watcher.join(2.0)
        if own_base:
            leftovers = [e for e in os.listdir(base)
                         if e.startswith("copy") and not e.endswith(MARKER_SUFFIX)]
            if not leftovers:
                shutil.rmtree(base, ignore_errors=True)


#: The openings of the details run_rows itself writes when a row could not be
#: measured for an infrastructure reason (no copy, a dead worker, a copy whose
#: baseline is red). A None with any other detail is the suite's own answer.
INFRA_PREFIXES = ("no work copy", "the worker ended before this row",
                  "baseline in the copy is not green")
SOURCE_ROOT = os.path.dirname(HERE)
USAGE = ("usage: perturb_pool.py --worker <copy> <rows.json> <baseline wall s> <out.json>\n"
         "       perturb_pool.py --measure <note.md> --workers <n> --baseline-wall <s> [--out <path>]")


def table_rows(note_path):
    """[(file_rel, suite_rel)] of the note's table rows that name a suite, in
    order, each once. A note that cannot be read or has no table gives []."""
    sys.path.insert(0, HERE)
    import release_note_perturb as P  # imported here: a worker imports nothing at load
    try:
        with open(note_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    rows = []
    for _claim, source, suite in P.parse_table(text):
        if suite is not None and (source, suite) not in rows:
            rows.append((source, suite))
    return rows


def measure_table(note_path, source_root, workers, baseline_wall,
                  runner=None, clock=None, load=None):
    """One measured pool run over the note's table: wall, workers used, load
    before and after, verdict counts (infra Nones apart from data Nones) and
    the commit measured. Counts and times only."""
    import time
    runner = runner or run_rows
    clock = clock or time.monotonic
    load = load or (lambda: os.getloadavg()[0])
    rows = table_rows(note_path)
    ncpu = os.cpu_count() or 1
    load_before = load()
    used = capped_workers(workers, load1=load_before, ncpu=ncpu)
    try:
        commit = _git(["rev-parse", "HEAD"], source_root)
    except (CopyFailed, OSError) as exc:
        commit = "NO-DATA: %s" % exc
    result = {"rows": len(rows), "workers_requested": int(workers), "workers_used": used,
              "baseline_wall": float(baseline_wall), "wall_seconds": 0, "ncpu": ncpu,
              "load1_before": load_before, "load1_after": load_before,
              "covered": 0, "uncovered": 0, "no_data": 0, "infra": 0, "commit": commit}
    if not rows:
        return result
    started = clock()
    # run_rows caps again on its own reading; a cap only ever lowers the count.
    answers = runner(rows, source_root, baseline_wall, used)
    result["wall_seconds"] = round(clock() - started, 3)
    result["load1_after"] = load()
    for _f, _s, verdict, detail in answers:
        if verdict is True:
            result["covered"] += 1
        elif verdict is False:
            result["uncovered"] += 1
        elif str(detail).startswith(INFRA_PREFIXES):
            result["infra"] += 1
        else:
            result["no_data"] += 1
    return result


def _write_atomic(path, text):
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".",
                               dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def main(argv):
    if len(argv) == 5 and argv[0] == "--worker":
        with open(argv[2], encoding="utf-8") as fh:
            run_worker(argv[1], [tuple(r) for r in json.load(fh)], float(argv[3]), argv[4])
        return 0
    if len(argv) not in (6, 8) or argv[0] != "--measure" or argv[2:6:2] != ["--workers", "--baseline-wall"] \
            or (len(argv) == 8 and argv[6] != "--out"):
        sys.exit(USAGE)
    try:
        workers, wall = int(argv[3]), float(argv[5])
    except ValueError:
        sys.exit(USAGE)
    out = argv[7] if len(argv) == 8 else None
    try:
        result = measure_table(argv[1], SOURCE_ROOT, workers, wall, runner=run_rows)
    except Exception as exc:  # any pool trouble: nothing is recorded
        print("NO-DATA: the measurement failed: %s: %s" % (type(exc).__name__, exc))
        return 2
    if not result["rows"]:
        print("NO-DATA: %s names no suite row to measure" % argv[1])
        return 2
    line = json.dumps(result, sort_keys=True)
    if out:
        _write_atomic(out, line + "\n")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
