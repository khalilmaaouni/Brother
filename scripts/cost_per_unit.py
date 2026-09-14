#!/usr/bin/env python3
"""cost_per_unit.py: tokens spent per git commit landed, over a trailing window.

WHY: an MVP proxy for "cost per resolved unit" (Microsoft's Foundry deck's own
FinOps principle: report cost per resolved task, not raw token counts). "Git
commit" is the unit here, deliberately not a roadmap-row concept: this repo
carries multiple competing roadmap-of-record files with no agreed canonical
one, and a commit is unambiguous and needs no resolution of that separate
problem.

Token totals come from Token Shield's own measurement module
(~/SaveClaudeTokens/scripts/measure_tokens.py), imported directly rather than
re-implemented: this script calls its collect()/summarize() over Claude
Code's own local transcripts under config.ROOT. Commit counts come from
`git rev-list --count --since=...` on this repo, read directly, never
estimated.

Token Shield distinguishes MEASURED usage (input/output/cache tokens read
straight off transcripts) from an ESTIMATED normalized figure (cache TTL
classes folded onto one basis, only available when every session carried the
split). This script never adds the two together: tokens-per-commit divides
the MEASURED total only; the normalized/estimated figure is printed alongside
for reference, labeled as such.

USAGE
  python3 scripts/cost_per_unit.py --days 30
"""
import argparse
import os
import subprocess
import sys

TOKEN_SHIELD_SCRIPTS = os.path.expanduser("~/SaveClaudeTokens/scripts")
REPO = "/Users/khalil.maaouni/Brother"
NODATA = "NO-DATA"

#: Claude Code slugifies a session's cwd by replacing "/" with "-", so
#: /Users/khalil.maaouni/Brother becomes this exact directory name under
#: ~/.claude/projects, and a worktree nested under it (Brother/.claude/
#: worktrees/x) becomes this name plus a suffix. cfg.ROOT is the WHOLE
#: ~/.claude/projects tree -- every project on this machine (a client mobile
#: app, a client data-analysis engagement, brother-hub, BrotherSBE, a chat
#: archive tool, ...) lives as a sibling directory there. Passing cfg.ROOT
#: straight to collect() (the first cut of
#: this script did exactly that) sums ALL of them into the numerator while
#: commit_count() below counts commits in ONE repo only -- a scope mismatch
#: that silently inflates tokens_per_commit by roughly two orders of
#: magnitude. This prefix restricts collection to this repo's own project
#: directory and its own worktrees.
#: KNOWN CEILING: a worktree's own commits that have not yet merged onto
#: this repo's HEAD are still counted in the numerator (the session ran)
#: but not in commit_count()'s denominator (`git rev-list HEAD`), so an
#: in-flight worktree lane understates tokens_per_commit until it merges.
#: Upgrade path: switch commit_count to `--all` if that gap starts to
#: matter, or reconcile per-worktree once the multi-board roadmap question
#: this script's own docstring defers is settled.
BROTHER_PROJECT_PREFIX = "-Users-khalil-maaouni-Brother"


def _load_measure_tokens():
    """Import token-shield's own measurement module. Returns (mt, cfg), or
    (None, None) if token-shield is not installed at the expected path --
    never a raise, since this script's job on a miss is to report NO-DATA."""
    if TOKEN_SHIELD_SCRIPTS not in sys.path:
        sys.path.insert(0, TOKEN_SHIELD_SCRIPTS)
    try:
        import measure_tokens as mt  # noqa: E402
        import config as cfg  # noqa: E402
    except ImportError:
        return None, None
    return mt, cfg


def _brother_project_dirs(projects_root):
    """Subdirectories of `projects_root` that are this repo's own project
    slug or one of its worktrees, per BROTHER_PROJECT_PREFIX. Returns []
    (never raises) if `projects_root` does not exist or is not a directory,
    which token_totals() below reports as NO-DATA rather than a crash."""
    try:
        names = os.listdir(projects_root)
    except OSError:
        return []
    return [os.path.join(projects_root, n) for n in names
            if n == BROTHER_PROJECT_PREFIX
            or n.startswith(BROTHER_PROJECT_PREFIX + "-")]


def token_totals(days):
    """(measured_total, normalized_estimate, reason) for the trailing `days`
    window, read from token-shield's own summarize(), SCOPED to this repo's
    own project directory and its worktrees (see BROTHER_PROJECT_PREFIX) --
    never the whole ~/.claude/projects tree, which spans every project on
    this machine. measured_total sums every real usage counter (input +
    output + cache read + cache write), never mixed with the
    normalized/estimated figure. Returns (None, None, reason) when
    token-shield is missing, no Brother-scoped project directory exists, or
    none of them carry usage counters."""
    mt, cfg = _load_measure_tokens()
    if mt is None:
        return None, None, "token-shield not found at %s" % TOKEN_SHIELD_SCRIPTS
    dirs = _brother_project_dirs(cfg.ROOT)
    if not dirs:
        return None, None, ("no project directory matching %r found under %s"
                            % (BROTHER_PROJECT_PREFIX, cfg.ROOT))
    sessions = []
    for d in dirs:
        sessions.extend(mt.collect(d, days))
    sm = mt.summarize(sessions)
    if not sm:
        return None, None, ("no transcripts under this repo's %d matched "
                            "project director%s carried usage counters"
                            % (len(dirs), "y" if len(dirs) == 1 else "ies"))
    measured = (sm["input_total"] + sm["output_total"] + sm["read_total"]
                + sm["write_5m_total"] + sm["write_1h_total"])
    return measured, sm["normalized_input_total"], None


def commit_count(repo, days):
    """Commits reachable from HEAD in the trailing `days` window, counted by
    git itself (git rev-list --count --since=...), never estimated. Returns
    None on any git failure (not a repo, no HEAD yet, git missing)."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-list", "--count",
             "--since=%d days ago" % days, "HEAD"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.strip()
    return int(text) if text.isdigit() else None


def tokens_per_commit(measured_total, commits):
    """measured_total / commits, or None when either input is zero or
    missing -- never a raise, never a guess."""
    if not measured_total or not commits:
        return None
    return measured_total / commits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args(argv)

    measured, normalized, reason = token_totals(args.days)
    if measured is None:
        print("%s: %s" % (NODATA, reason))
        return 2

    commits = commit_count(args.repo, args.days)
    if commits is None:
        print("%s: could not count commits in %s over the last %d days"
              % (NODATA, args.repo, args.days))
        return 2
    if commits == 0:
        print("%s: no commits landed in %s over the last %d days"
              % (NODATA, args.repo, args.days))
        return 2

    per_commit = tokens_per_commit(measured, commits)
    if per_commit is None:
        print("%s: tokens_measured or commits computed as zero "
              "(tokens_measured=%s commits=%s)" % (NODATA, measured, commits))
        return 2

    normalized_str = ("%d" % round(normalized)) if normalized is not None else NODATA
    print("days=%d tokens_measured=%d tokens_normalized_estimated=%s "
          "commits=%d tokens_per_commit=%.1f"
          % (args.days, measured, normalized_str, commits, per_commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
