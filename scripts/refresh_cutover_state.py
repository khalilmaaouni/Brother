#!/usr/bin/env python3
"""The receipt-time gate that keeps a cutover pack honest.

WHY THIS EXISTS. A handover pack is a snapshot. The estate keeps moving after
the snapshot is taken: branches land, pull requests merge, decisions close. A
receiving session that reads the pack's narrative files and acts on them is
acting on the state of the world at BUILD time, not at READ time. Both sides of
the 2026-09-10 approach debate named this as the single biggest failure mode of
this handover, and the evidence was already on the ground when they said it:
docs/plan/LIVE-STATE.json carried generated_at 2026-08-28 while the decisions
governing the cut were dated 2026-09-10.

So the pack does not ship a snapshot alone. It ships this, the instrument that
took the snapshot, and the receiving session runs it FIRST. It re-measures the
same fields live and refuses to agree with the pack where they differ.

THE RULE THIS ENFORCES: no release action from prose. Narrative files in the
pack are hints. Live git and the live pull request list are truth. Where they
disagree, the live reading wins and this script says so by name.

Exit contract, this estate's house style:
  0  live state matches the pack's control plane: the pack is still true
  1  DRIFT: the pack is stale in a named way, each difference printed
  2  NO-DATA: live state could not be measured at all, never a pass

Deliberately self contained: it runs from inside an unzipped pack, which may
sit on a machine that has no checkout of this repository, so it imports
nothing from the repository and shells out to git and gh directly.

No em or en dashes anywhere.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def _run(args, cwd=None):
    """Run a command, return (exit_code, stdout). Never raises on a bad exit:
    a missing tool or a failing command is DATA about the environment, not a
    crash, and the caller decides whether it is NO-DATA or a plain absence."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=120)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "could not run %s: %s" % (" ".join(args), exc)


def measure_repo(path):
    """One repository's live state. Every field is read, never inferred."""
    if _run(["git", "-C", path, "rev-parse", "--git-dir"])[0] != 0:
        return {"error": "NO-DATA: %s is not a git checkout" % path}
    rc_head, head = _run(["git", "-C", path, "rev-parse", "HEAD"])
    if rc_head != 0:
        return {"error": "NO-DATA: cannot read HEAD in %s" % path}
    _, branch = _run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"])
    _, dirty = _run(["git", "-C", path, "status", "--porcelain"])
    _run(["git", "-C", path, "fetch", "-q", "origin"])
    _, behind = _run(["git", "-C", path, "rev-list", "--count",
                      "HEAD..origin/main"])
    _, tags = _run(["git", "-C", path, "tag", "--sort=-creatordate"])
    return {
        "head": head,
        "branch": branch,
        "dirty_count": len([l for l in dirty.splitlines() if l.strip()]),
        "behind_origin_main": behind if behind.isdigit() else "NO-DATA",
        "tags_newest": tags.splitlines()[:5],
    }


def measure_prs(path):
    """Open pull requests. gh absent is NO-DATA, never an empty list, because
    an empty list and an unreadable list look identical to a reader and mean
    opposite things."""
    rc, out = _run(["gh", "pr", "list", "--limit", "50",
                    "--json", "number,title,headRefName,isDraft"], cwd=path)
    if rc != 0:
        return {"error": "NO-DATA: gh could not list pull requests: %s"
                         % out[:200]}
    try:
        return {"open": json.loads(out or "[]")}
    except json.JSONDecodeError:
        return {"error": "NO-DATA: gh returned unparseable JSON"}


def measure(repos):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repos": {r: measure_repo(r) for r in repos},
        "pull_requests": measure_prs(repos[0]) if repos else
                         {"error": "NO-DATA: no repository given"},
    }


def diff_against(pack_plane, live):
    """Name every difference that changes what the receiver should do.

    Only load bearing fields are compared. A changed timestamp is not drift;
    a changed HEAD, a newly merged pull request, or a tag that appeared is."""
    drift = []
    p_repos = pack_plane.get("repos", {})
    l_repos = live.get("repos", {})
    for name, p in p_repos.items():
        l = l_repos.get(name)
        if l is None:
            drift.append("repo %s was in the pack and is not on this machine"
                         % name)
            continue
        if "error" in l:
            drift.append("repo %s: %s" % (name, l["error"]))
            continue
        if isinstance(p, dict) and p.get("head") and p["head"] != l.get("head"):
            drift.append("repo %s HEAD moved: pack %s, live %s"
                         % (name, p["head"][:12], (l.get("head") or "?")[:12]))
        p_tags = set((p or {}).get("tags_newest") or [])
        l_tags = set(l.get("tags_newest") or [])
        for t in sorted(l_tags - p_tags):
            drift.append("tag %s exists live and was not in the pack" % t)
        for t in sorted(p_tags - l_tags):
            drift.append("tag %s was in the pack and is not live" % t)

    p_pr = {int(x["number"]) for x in
            (pack_plane.get("pull_requests", {}) or {}).get("open", [])}
    live_pr_block = live.get("pull_requests", {}) or {}
    if "error" in live_pr_block:
        drift.append(live_pr_block["error"])
    else:
        l_pr = {int(x["number"]) for x in live_pr_block.get("open", [])}
        for n in sorted(p_pr - l_pr):
            drift.append("pull request %d was open at pack build and is now "
                         "closed or merged: its content is either in or lost, "
                         "check before assuming" % n)
        for n in sorted(l_pr - p_pr):
            drift.append("pull request %d is open now and was not in the "
                         "pack: it landed after the pack was built" % n)
    return drift


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    # The gate ships flat, beside the control plane it checks.
    default_plane = os.path.join(here, "03-LIVE-CONTROL-PLANE.json")
    ap = argparse.ArgumentParser(
        description="Re-measure live state and refuse a stale cutover pack.")
    ap.add_argument("--plane", default=default_plane,
                    help="the pack's control plane JSON (default: %(default)s)")
    ap.add_argument("--repo", action="append", default=[],
                    help="repository path to measure; repeatable. Default: "
                         "the repos named in the control plane.")
    ap.add_argument("--write", metavar="PATH",
                    help="write the fresh measurement here as JSON")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.plane):
        print("NO-DATA: no control plane at %s" % args.plane)
        return 2
    try:
        with open(args.plane, encoding="utf-8") as f:
            plane = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print("NO-DATA: control plane unreadable: %s" % exc)
        return 2

    repos = args.repo or sorted(plane.get("repos", {}))
    repos = [r for r in repos if os.path.isdir(r)]
    if not repos:
        print("NO-DATA: none of the pack's repositories exist on this machine")
        return 2

    live = measure(repos)
    if args.write:
        with open(args.write, "w", encoding="utf-8") as f:
            json.dump(live, f, indent=2, sort_keys=True)

    if all("error" in (v or {}) for v in live["repos"].values()):
        print("NO-DATA: no repository could be measured")
        return 2

    drift = diff_against(plane, live)
    print("pack built at:  %s" % plane.get("generated_at", "unknown"))
    print("measured now:   %s" % live["generated_at"])
    print("repositories:   %d measured" % len(repos))
    if not drift:
        print("\nPASS: live state matches the pack. The pack is still true.")
        return 0
    print("\nDRIFT: the pack is stale in %d way(s). Live state wins on every "
          "line below.\n" % len(drift))
    for d in drift:
        print("  - %s" % d)
    print("\nDo not take a release action from the pack's prose until each "
          "line above is reconciled.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
