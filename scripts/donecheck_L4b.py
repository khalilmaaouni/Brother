#!/usr/bin/env python3
"""L4b parent done check: the pass keep must not change a single verdict of required_fast.sh.

WHY (2026-09-28): every L4b sub unit landed (the pass keep in required_fast.sh and check_all.sh, a
parity probe, a timing probe), but the unit's own done check, a real before and after run of the
battery, is prose that nothing ran, so the unit stayed open. This runs it: the BEFORE script is
today's required_fast.sh with only the L4b.1 pass keep hunk taken back out, the AFTER script is
today's file, both run from the same tree, one after the other, with their own TMPDIR. The counts
line, the FAILED and NO-DATA name lists and every transition line must be identical.

Run it in a scratch worktree, never the launch tree: the battery rewrites generated files, and this
check restores docs/plan/READINESS-BOARD.html afterwards only because it refuses to start when that
file already differs from HEAD.

usage: donecheck_L4b.py [--repo DIR]
exit 0 PASS (identical), 1 FAIL (a verdict moved), 2 NO-DATA (could not run both sides)."""
import argparse, os, re, shutil, subprocess, sys, tempfile

KEEP_HUNK = re.compile(r'    0\) pass=\$\(\(pass\+1\)\);   verdict="PASS   "\n(?:       #[^\n]*\n)*'
                       r'       pass_keep=.*?\n       fi\n       ;;\n', re.S)
PLAIN = '    0) pass=$((pass+1));   verdict="PASS   " ;;\n'
BOARD = os.path.join("docs", "plan", "READINESS-BOARD.html")
TIMEOUT_S = 3600


def verdict_lines(text):
    """What must not move: the counts line, the FAILED and NO-DATA lists, every transition line."""
    keep = []
    for line in text.splitlines():
        line = re.sub(r"\s*\[full: [^\]]*\]", "", line).rstrip()
        if re.match(r"^pass \d+\s+fail \d+\s+no-data \d+$", line) or line.startswith(("FAILED:", "NO-DATA:")) \
                or re.search(r"(?i)transition", line):
            keep.append(line)
    return keep


def run(script, repo, scratch, label):
    tmp = tempfile.mkdtemp(prefix="l4b-%s-" % label, dir=scratch)
    env = dict(os.environ, TMPDIR=tmp)
    try:
        p = subprocess.run(["sh", script], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None, "no answer in %d s" % TIMEOUT_S
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return p.returncode, p.stdout + p.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    repo = os.path.realpath(a.repo)
    # NEVER INSIDE THE LOOP (2026-09-28): the loop's closer ran this in the launch tree, held a pass for fifteen
    # minutes and dirtied the readiness board the landing needs clean. The launch tree answers NO-DATA at once.
    launch = os.environ.get("BROTHER_LAUNCH_WORKTREE")
    if launch and os.path.realpath(launch) == repo:
        print("NO-DATA: two required_fast.sh runs never run in the launch tree; run this in a scratch worktree"); return 2
    after_path = os.path.join(repo, "scripts", "required_fast.sh")
    try:
        after_text = open(after_path, encoding="utf-8").read()
    except OSError as exc:
        print("NO-DATA: cannot read required_fast.sh: %s" % exc); return 2
    before_text, n = KEEP_HUNK.subn(PLAIN, after_text)
    if n != 1:
        print("NO-DATA: the L4b.1 pass keep hunk was found %d times, expected once" % n); return 2
    dirty = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--", BOARD], capture_output=True, text=True)
    if dirty.returncode != 0 or dirty.stdout.strip():
        print("NO-DATA: %s differs from HEAD before the run (or git failed), refusing to overwrite it" % BOARD); return 2
    scratch = os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")
    os.makedirs(scratch, exist_ok=True)
    work = tempfile.mkdtemp(prefix="l4b-donecheck-", dir=scratch)
    # INSIDE the tree's scripts/ (2026-10-03): required_fast.sh starts with cd "$(dirname "$0")/..", so a copy written
    # under the scratch folder ran from there, found no scripts/ and stopped after 0 checks (NO-DATA, no counts line)
    before_path = os.path.join(repo, "scripts", ".required_fast.before.%d.sh" % os.getpid())
    with open(before_path, "w", encoding="utf-8") as fh:
        fh.write(before_text)
    try:
        results = {}
        for label, script in (("before", before_path), ("after", after_path)):
            rc, out = run(script, repo, work, label)
            with open(os.path.join(work, label + ".out"), "w", encoding="utf-8") as fh:
                fh.write(out)
            if rc is None:
                print("NO-DATA: the %s run did not finish: %s" % (label, out)); return 2
            results[label] = (rc, verdict_lines(out))
    finally:
        subprocess.run(["git", "-C", repo, "checkout", "--", BOARD], capture_output=True)
        shutil.copy2(before_path, os.path.join(work, "required_fast.before.sh"))   # kept beside the outputs for reading
        os.remove(before_path)
    (rc_b, lines_b), (rc_a, lines_a) = results["before"], results["after"]
    if not any(l.startswith("pass ") for l in lines_b) or not any(l.startswith("pass ") for l in lines_a):
        print("NO-DATA: a run printed no counts line; outputs kept in %s" % work); return 2
    print("before exit %s: %s" % (rc_b, " | ".join(lines_b)))
    print("after  exit %s: %s" % (rc_a, " | ".join(lines_a)))
    if rc_b != rc_a or lines_b != lines_a:
        print("FAIL: the pass keep moved a verdict; outputs kept in %s" % work); return 1
    print("PASS: counts, FAILED and NO-DATA lists and transition lines are identical before and after the pass keep")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
