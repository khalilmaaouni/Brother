#!/usr/bin/env python3
"""bm_release_invariant.py: distributable bytes cannot move without VERSION
moving in the same range (BM-A6, porting the concept BrotherSBE already
proves at tools/sbe_release_invariant.py in that sibling project).

WHY THIS EXISTS
  Pull requests #51 and #52 (fix/containment-message-tells-the-truth,
  fix/first-session-says-what-to-do) shipped real changes to
  tools/bm_sessionstart.py and tools/bm_store.py while VERSION stayed at
  3.3.2 across the whole range. A marketplace user is offered an update
  only when the version STRING changes, so those two merges shipped as if
  nothing had changed: the exact failure class this gate exists to catch.
  It is proven against that real range below
  (see test_bm_release_invariant.py's
  test_the_pr_51_and_52_range_is_flagged_for_real).

  This is a PORT OF THE CONCEPT, not the implementation: BrotherSBE's own
  version reads `sbe_checks.py` helpers and a fixture-driven CHECKS
  registry this project does not have. Here the git plumbing is written
  directly, in the shape tools/bm_reconcile.py's own `_run_git` already
  uses in this repository (timeout, OSError degrades to a returncode
  rather than raising), and the CLI/NO-DATA/exit-code contract mirrors
  tools/bm_progress_check.py, this project's own nearest sibling gate.

THE CONTRACT
  Three real verdicts, one line each:

    PASS      distributable file(s) changed in the range AND VERSION
              changed in the same range.                        exit 0
    FAIL      distributable file(s) changed in the range but VERSION
              did NOT change in the same range.                  exit 1
    NO-DATA   nothing distributable changed, OR a changed path could not
              be classified as distributable or not, OR the range itself
              could not be computed (no repo, unborn HEAD, an unresolved
              base ref). NEVER a pass, never treated as a pass by any
              caller. exit 0 unless the exception path below fires.

  Exit codes are load-bearing, and a FAIL blocks with NO flag required.
  An earlier draft of this file made FAIL advisory unless --strict was
  passed. That was wrong for a gate whose entire purpose is to REFUSE the
  case "public bytes changed, version unchanged": a gate that prints FAIL
  while exiting 0 refuses nothing, because every runner that chains on exit
  status reads it as a pass, and the flag that would have saved it is the
  one a hurried releaser omits. The estate's own convention agrees, in
  tools/bm_reconcile.py's last line: any row that is not VALID exits 1,
  with no opt-in. NO-DATA still exits 0, for the opposite reason: it is
  never a pass and never a block, so it must never gate on something
  nobody could measure.

THE DISTRIBUTABLE SET, decided by hand against this repository's real
top level (checked below in TestDistributableScope /
test_every_declared_path_exists_in_this_repository, so a rename fails a
fixture instead of silently narrowing this gate's scope):

  DISTRIBUTABLE (bytes a user runs, or a manifest describing them):
    dirs:  tools, agents, commands, hooks, skills, scripts, mcp,
           brotherme, schema, project-template, vault-template,
           .claude-plugin
    files: SKILL.md (the root skill entry point), pyproject.toml (PEP 621
           packaging metadata: pipx/uv install surface),
           capabilities.status.json and product.identity.json (both read
           at runtime by tools/bm_docs.py and tools/bm_project_facts.py,
           confirmed by grep, not assumed: a change here changes what a
           session tells the founder)

  NON-DISTRIBUTABLE (excluded on purpose, never silently, never by
  omission: named here so a change to this list is a change a diff shows):
    dirs:  docs, evidence, benchmark, references, .github
    files: README.md, LICENSE, CHANGELOG.md, CONTRIBUTING.md,
           SECURITY.md, RUBRIC.md, PRODUCT-DIRECTION.md, INVARIANTS.md,
           PROJECT.md, STATE.template.md, PROGRESS.html, DIGEST.md,
           HANDOFF-PACKET-v3-finalization.md,
           BROTHERMODE_TOTAL_LEADERSHIP_STRATEGY.md, CLAUDE.md,
           .gitignore, azure-pipelines.yml, bitbucket-pipelines.yml,
           CHECKSUMS.sha256 (a manifest of the bytes, not the bytes
           themselves; the orchestrator regenerates it last and this gate
           must never treat its own regeneration as the release)

  TEST-ONLY FILES: any path whose basename matches test_*.py (this
  project's own naming convention, confirmed by `ls tools/test_bm_*.py`)
  is classified non-distributable regardless of which directory it lives
  under, tools/ included. A test file never ships to an end user; it only
  runs in this repository's own harness. Named explicitly here rather
  than left to the directory rule, because tools/ itself IS distributable
  and a directory-only rule would wrongly sweep every test_bm_*.py file
  in with the code it tests.

  VERSION is tracked separately (whether it appears in the changed set),
  never classified as distributable or not.

  UNCLASSIFIED: any top-level path not in one of the two lists above.
  This is the rule this task exists to enforce and BrotherSBE's own
  version does not need: a path this gate cannot place is NEVER folded
  into "not distributable" by default (that would make the check pass by
  looking away). It forces the whole verdict to NO-DATA, naming the path,
  even when a real distributable violation is also present in the same
  range: an answer built partly on a guess is not a safe FAIL either.

EFFECT CLASS: pure_read. Spawns read-only `git` subprocesses only
(rev-parse, diff --name-only); never git add, commit, checkout, or push.
Writes NOT ONE BYTE anywhere.

Python 3.9, standard library only. No network. No em or en dashes
anywhere in this file or its output.

Usage:
  python3 tools/bm_release_invariant.py [check] [--root PATH]
      [--base REF]

`check` is the only verb and is also the default when no verb is given.
`--base` defaults to origin/main (mirrors BrotherSBE's DEFAULT_BASE and
its stated degenerate case: on a direct push to main this is often an
empty range, which reads NO-DATA, never a false PASS).
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_BASE = "origin/main"

#: First-path-segment match only (never substring), mirroring
#: sbe_release_invariant.py's own _is_distributable contract: a sibling
#: directory that merely starts with a distributable name (say
#: "tools-legacy/") must not be swept in.
DISTRIBUTABLE_DIRS = (
    "tools", "agents", "commands", "hooks", "skills", "scripts", "mcp",
    "brotherme", "schema", "project-template", "vault-template",
    ".claude-plugin",
)
DISTRIBUTABLE_FILES = (
    "SKILL.md", "pyproject.toml", "capabilities.status.json",
    "product.identity.json",
)

NON_DISTRIBUTABLE_DIRS = ("docs", "evidence", "benchmark", "references", ".github")
NON_DISTRIBUTABLE_FILES = (
    "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
    "SECURITY.md", "RUBRIC.md", "PRODUCT-DIRECTION.md", "INVARIANTS.md",
    "PROJECT.md", "STATE.template.md", "PROGRESS.html", "DIGEST.md",
    "HANDOFF-PACKET-v3-finalization.md",
    "BROTHERMODE_TOTAL_LEADERSHIP_STRATEGY.md", "CLAUDE.md",
    ".gitignore", "azure-pipelines.yml", "bitbucket-pipelines.yml",
    "CHECKSUMS.sha256",
)

VERSION_FILE = "VERSION"


def classify(path):
    """Return "distributable", "non-distributable", "version", or
    "unclassified" for `path` (a repo-root-relative, forward-slash git
    path). Matched by the FIRST path segment against the dir lists, or by
    exact name against the file lists, never by substring."""
    path = path.strip()
    if not path:
        return "unclassified"
    if path == VERSION_FILE:
        return "version"
    basename = path.rsplit("/", 1)[-1]
    if basename.startswith("test_") and basename.endswith(".py"):
        return "non-distributable"
    if path in DISTRIBUTABLE_FILES:
        return "distributable"
    if path in NON_DISTRIBUTABLE_FILES:
        return "non-distributable"
    head = path.split("/", 1)[0]
    if head in DISTRIBUTABLE_DIRS:
        return "distributable"
    if head in NON_DISTRIBUTABLE_DIRS:
        return "non-distributable"
    return "unclassified"


def _named_few(items, limit=12):
    """A bounded list that states how much it left out, mirroring
    sbe_release_invariant.py's own _named_few contract (same shape)."""
    items = list(items)
    if len(items) <= limit:
        return ", ".join(items)
    return "%s, and %d more" % (", ".join(items[:limit]), len(items) - limit)


def _run_git(root, *args):
    """One git subcommand against `root`. Mirrors tools/bm_reconcile.py's
    own `_run_git`: OSError (no git on PATH) degrades to a
    CompletedProcess with returncode 127 rather than raising, and a hung
    git is bounded by a timeout that degrades to returncode 124, so every
    caller's return-code check works uniformly and nothing here can hang
    a session-start caller forever."""
    try:
        return subprocess.run(["git", "-C", root] + list(args),
                              capture_output=True, text=True, timeout=10)
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(args, 124, "", str(exc))


def check(root, base, head_ref="HEAD"):
    """(verdict, reason) for the release invariant over `root`, `base`..`head_ref`.

    `head_ref` exists so a HISTORICAL range can be pinned at BOTH ends. The
    calibration against pull requests 51 and 52 originally ran base..HEAD, and
    it passed until this repository's own version bump entered that range and
    turned its FAIL into a PASS. A test whose range keeps growing is measuring a
    moving tree, which this estate has already paid for once: a verdict is only
    reproducible if both ends of its range are fixed.

    Four ways in to NO-DATA: `root` is not a git working tree; HEAD does
    not resolve (an unborn checkout, or a pinned head ref that is absent);
    `base` does not resolve here (a
    shallow clone or a mistyped ref); and the two this gate adds on top of
    BrotherSBE's own version: nothing distributable changed (silence about
    the code is not evidence VERSION is right), and a changed path could
    not be classified at all (a guess dressed as an answer is worse than
    no answer, so this beats even a real FAIL found in the same range).
    """
    result = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if result.returncode == 127:
        return "NO-DATA", "git is not available on PATH (%s)" % (result.stderr or "").strip()
    if result.returncode != 0:
        return "NO-DATA", ("%s is not a git working tree here (%s), so no range "
                           "could be compared" % (root, (result.stderr or "git refused").strip()))

    result = _run_git(root, "rev-parse", "--verify", "--quiet", head_ref + "^{commit}")
    head = result.stdout.strip()
    if result.returncode != 0 or not head:
        return "NO-DATA", ("head ref %r does not resolve to a commit in %s (%s); an unborn "
                           "checkout has nothing to compare" % (root, (result.stderr or "no commits yet").strip()))

    result = _run_git(root, "rev-parse", "--verify", "--quiet", base + "^{commit}")
    base_commit = result.stdout.strip()
    if result.returncode != 0 or not base_commit:
        return "NO-DATA", ("base ref %r does not resolve in %s (%s); a shallow checkout "
                           "or a ref this clone never fetched reads as unknown, never as a "
                           "silent pass over a range nobody could compute"
                           % (base, root, (result.stderr or "unknown revision").strip()))

    result = _run_git(root, "diff", "--name-only", base_commit, head)
    if result.returncode != 0:
        return "NO-DATA", ("git diff between %s and %s failed in %s (%s), so the "
                           "changed-file set is unknown"
                           % (base_commit[:12], head[:12], root, (result.stderr or "git refused").strip()))

    changed = sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})
    span = "%s..%s" % (base_commit[:12], head[:12])

    distributable, unclassified = [], []
    version_changed = False
    for path in changed:
        kind = classify(path)
        if kind == "distributable":
            distributable.append(path)
        elif kind == "version":
            version_changed = True
        elif kind == "unclassified":
            unclassified.append(path)
        # "non-distributable" contributes nothing to either list on purpose.

    if unclassified:
        return "NO-DATA", ("%d changed path(s) over %s could not be classified as "
                           "distributable or not (%s); this gate refuses to guess, so "
                           "the verdict is unknown rather than a possibly-wrong PASS or "
                           "FAIL, even though %d distributable file(s) also changed in "
                           "this range" % (len(unclassified), span, _named_few(unclassified),
                                          len(distributable)))

    if not distributable:
        return "NO-DATA", ("no distributable path changed over %s (%d file(s) changed in "
                           "all); absence of a distributable change is not evidence "
                           "VERSION is right, so this is not a PASS either"
                           % (span, len(changed)))

    if version_changed:
        return "PASS", ("%d distributable file(s) changed over %s and VERSION changed in "
                        "the same range (%s)" % (len(distributable), span, _named_few(distributable)))

    return "FAIL", ("%d distributable file(s) changed over %s but VERSION did not move in "
                    "the same range: %s; a marketplace user is offered an update only when "
                    "the version STRING changes, so these bytes would ship as if unchanged"
                    % (len(distributable), span, _named_few(distributable)))


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _parse_argv(argv):
    """Return (root, base, head, error). `error` set means stop with a
    plain usage failure (exit 2, no verdict word printed)."""
    args = list(argv)
    if args and not args[0].startswith("--"):
        verb = args.pop(0)
        if verb != "check":
            return None, None, None, "bm_release_invariant: unknown verb: %s" % verb

    root, base, head = None, None, None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--root", "--base", "--head"):
            if i + 1 >= len(args):
                return None, None, None, ("bm_release_invariant: %s requires a value"
                                          % arg)
            value = args[i + 1]
            if arg == "--root":
                root = value
            elif arg == "--base":
                base = value
            else:
                head = value
            i += 2
        else:
            return None, None, None, ("bm_release_invariant: unknown argument: %s"
                                      % arg)
    return root, base, head, None


def _run(argv):
    """The real body of main, split out so `main` can wrap the whole thing
    in one try/except and guarantee NO-DATA on any unexpected exception,
    mirroring tools/bm_progress_check.py's own `_run`/`main` split."""
    root_arg, base_arg, head_arg, err = _parse_argv(argv)
    if err:
        sys.stderr.write(err + "\n")
        return 2

    root = os.path.realpath(os.path.expanduser(root_arg)) if root_arg else "."
    if not os.path.isdir(root):
        sys.stdout.write("NO-DATA: no such directory: %s\n" % root)
        return 0
    base = base_arg or DEFAULT_BASE

    verdict, reason = check(root, base, head_arg or "HEAD")
    sys.stdout.write("release-invariant %s %s [severity: gate]\n" % (verdict, reason))
    # A FAIL exits NONZERO, with no flag required to make it do so. This row
    # exists to REFUSE the case "public bytes changed, version unchanged", and a
    # gate that prints FAIL while exiting 0 refuses nothing: every script and
    # runner that chains on exit status reads it as a pass. The estate's own
    # convention says the same thing, in tools/bm_reconcile.py's last line: any
    # row that is not VALID exits 1, without an opt-in.
    #
    # NO-DATA still exits 0, deliberately and for the opposite reason: NO-DATA
    # is never a pass and never a block, so it must not gate a merge on
    # something nobody could measure.
    if verdict == "FAIL":
        return 1
    return 0


def main(argv):
    try:
        return _run(argv)
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s: %s\n" % (type(exc).__name__, exc))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
