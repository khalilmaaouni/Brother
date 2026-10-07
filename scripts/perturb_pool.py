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
    core count); at or above it, one worker."""
    ncpu = ncpu or os.cpu_count() or 1
    load1 = os.getloadavg()[0] if load1 is None else load1
    if load1 >= ncpu:
        return 1
    return max(1, min(int(requested), ncpu))


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


def run_rows(rows, source_root, baseline_wall, workers=2, base=None,
             spawn=None, copier=make_copy):
    """[(file_rel, suite_rel, verdict, detail)] in the order of `rows`.
    verdict is covers()'s own True / False / None."""
    rows = [tuple(r) for r in rows]
    own_base = base is None
    base = base or tempfile.mkdtemp(prefix="perturb-pool-")
    n = capped_workers(workers)
    spawn = spawn or (lambda argv, env: subprocess.Popen(argv, env=env,
                                                         stdout=subprocess.DEVNULL,
                                                         stderr=subprocess.PIPE))
    answers, running = {}, []
    try:
        for i in range(n):
            mine = rows[i::n]
            if not mine:
                continue
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
        for mine, copy, out_path, proc in running:
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
        return [(f, s) + answers[(f, s)] for f, s in rows]
    finally:
        if own_base:
            leftovers = [e for e in os.listdir(base)
                         if e.startswith("copy") and not e.endswith(MARKER_SUFFIX)]
            if not leftovers:
                shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--worker":
        with open(sys.argv[3], encoding="utf-8") as fh:
            run_worker(sys.argv[2], [tuple(r) for r in json.load(fh)],
                       float(sys.argv[4]), sys.argv[5])
    else:
        sys.exit("usage: perturb_pool.py --worker <copy> <rows.json> <baseline wall s> <out.json>")
