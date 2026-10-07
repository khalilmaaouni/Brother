#!/usr/bin/env python3
"""land_batch.unwind: a landing commit that cannot reach hub must not block every later landing.

Measured 2026-09-21: one landing committed, its hermetic check failed, the commit stayed local, and preflight then
refused 15 landings in a row on parity. This drives unwind() against REAL git repositories built in a temp dir (a
bare hub and a clone), because the property is about what git's HEAD and refs say afterwards, not about a return
value. Run: python3 scripts/test_land_batch_unwind.py
"""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "loop"))   # the versioned copy, named, never whatever sys.path offers
import land_batch as LB  # noqa: E402

ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
ENV.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
           GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, env=ENV)


def fixture(extra_commits=1, dirty=False):
    """A clone one landing commit (or more) ahead of its hub, with a READY status file for the build."""
    d = tempfile.mkdtemp(prefix="unwind-"); hub = os.path.join(d, "hub.git"); repo = os.path.join(d, "repo")
    subprocess.run(["git", "init", "-q", "--bare", hub], env=ENV); subprocess.run(["git", "init", "-q", repo], env=ENV)
    git(repo, "checkout", "-q", "-b", "main"); open(os.path.join(repo, "a.txt"), "w").write("base\n")
    git(repo, "add", "a.txt"); git(repo, "commit", "-q", "-m", "base"); git(repo, "remote", "add", "hub", hub); git(repo, "push", "-q", "hub", "main")
    started = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    for i in range(extra_commits):
        open(os.path.join(repo, "a.txt"), "a").write("landing %d\n" % i); git(repo, "commit", "-q", "-am", "landing %d" % i)
    if dirty:
        open(os.path.join(repo, "stray.txt"), "w").write("not from this landing\n")
    st = os.path.join(d, "STATUS"); open(st, "w").write("READY /x/D1.1-r0-build.json (round 0)\n")
    return repo, started, st


def call(repo, started, st, why="hermetic check exit 1"):
    rev = lambda r: git(repo, "rev-parse", "--short", r).stdout.strip()
    run = lambda cmd, log, timeout=60, env=None: subprocess.run(["git", "-C", repo] + cmd[1:], capture_output=True, text=True, env=ENV)
    changed = lambda: [l[3:] for l in git(repo, "status", "--porcelain").stdout.splitlines()]
    return LB.unwind(started, why, [st], None, rev, run=run, changed=changed), rev


def main():
    # each refusal is asserted by ITS OWN reason text: a fixture that another guard would also refuse proves neither
    cases = [("an unknown start refuses, and says so", "started from is unknown" in LB.unwind_refusal("abc", "", [])),
             ("an unreadable parent refuses, and says so", "parent could not be read" in LB.unwind_refusal("", "abc", [])),
             ("a parent that is not the start refuses", "is not" in LB.unwind_refusal("abc", "def", [])),
             ("a dirty tree refuses", "not clean" in LB.unwind_refusal("abc", "abc", ["x"])),
             ("the landing commit on a clean tree may be unwound", LB.unwind_refusal("abc", "abc", []) == "")]
    repo, started, st = fixture()
    landing = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    line, rev = call(repo, started, st)
    kept = git(repo, "rev-parse", "--short", "refs/brother/unlanded/%s" % landing).stdout.strip()
    ahead = git(repo, "rev-list", "--count", "hub/main..HEAD").stdout.strip()
    cases += [("HEAD is back on the hub head this landing started from", rev("HEAD") == started and ahead == "0"),
              ("the commit is kept under refs/brother/unlanded", kept == landing),
              ("the build is quarantined with the reason and its previous status", open(st).read().startswith("QUARANTINE refused after the commit, hermetic check exit 1") and "previous: READY" in open(st).read()),
              ("the line says UNWOUND and names the commit", line.startswith("UNWOUND") and landing in line)]
    repo2, started2, st2 = fixture(extra_commits=2)      # two commits ahead: the parent of HEAD is NOT where this landing started
    head2 = git(repo2, "rev-parse", "--short", "HEAD").stdout.strip()
    line2, rev2 = call(repo2, started2, st2)
    cases += [("a commit this run did not make is never reset away", line2.startswith("UNWIND REFUSED") and rev2("HEAD") == head2),
              ("and its build keeps its status", open(st2).read().startswith("READY"))]
    repo3, started3, st3 = fixture(dirty=True)
    head3 = git(repo3, "rev-parse", "--short", "HEAD").stdout.strip()
    line3, rev3 = call(repo3, started3, st3)
    cases += [("stray work in the tree stops the reset", line3.startswith("UNWIND REFUSED") and rev3("HEAD") == head3 and os.path.isfile(os.path.join(repo3, "stray.txt")))]
    src = open(os.path.join(HERE, "loop", "land_batch.py"), encoding="utf-8").read()
    cases += [("main calls unwind at BOTH places a commit can fail to reach hub", src.count("print(unwind(head, ") == 2),
              # 520a42828 (2026-09-24 18:29) moved the refusal onto the push verdict; this anchor still named the old
              # line, so the suite raised on every tree since and the landing quarantined H6.c's READY build for it.
              # The LANDED emission is either the literal print or H6.c's landed_line() helper (22:38 the same night,
              # the build that adds blended USD to that line was quarantined for renaming the literal this case read).
              ("LANDED is printed only after the push check", 0 <= src.find('if verdict in ("NOT-LANDED", "DIVERGED"):')
               < min([i for i in (src.find('print("LANDED  %s'), src.find("print(landed_line(")) if i >= 0] or [-1]))]
    bad = [n for n, good in cases if not good]
    print("selftest: %d cases, %s" % (len(cases), "OK" if not bad else "FAILED: " + ", ".join(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
