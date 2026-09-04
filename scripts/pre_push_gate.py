"""pre_push_gate: catch it at the boundary, not after somebody asks.

The founder's demand, in his words: the watchdog should catch that a task is
actually done properly, should not create push conflicts or orchestration issues
between sessions, and should detect bugs BEFORE they are pushed.

THE INSIGHT THIS RESTS ON, and it is why the file is short. This estate already
has the checks. Forty two of them run in a battery, and every one runs when a
person types a command. The gap was never detection, it was the TRIGGER: nothing
ran them at the moment that matters. Research the same day landed on exactly
that answer, event-triggered rather than sampled, because sampling gives
unbounded detection latency and a sampled clean cannot be reported honestly.

A push is the sharpest event available. It is the last moment a mistake is still
cheap, and the first moment it becomes somebody else's problem.

FOUR FAMILIES, and they fail differently on purpose:

  COLLISION, which is about other sessions. Is the local branch behind its
  remote, so a push would conflict or clobber? Do other worktrees exist on this
  repository, meaning another session may be mid-edit in the same tree? These
  BLOCK, because pushing over somebody else's work is the failure that cannot be
  undone by a revert.

  CORRECTNESS, which is about this change. Secrets, dashes, attribution
  strings, private terms, over the OUTGOING RANGE rather than the working tree,
  because the range is what actually leaves this machine. These BLOCK.

  EDITION, docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 5: does the remote
  this push targets match what the nearest .brother-edition allows? The public
  export target is refused from every edition except the exporter's own marked
  invocation. This BLOCKS, for the same reason correctness does: it is the last
  cheap moment to stop content leaving toward a remote that never should have
  seen it.

  DRIFT, which is about the record. Does the board still describe the world?
  This WARNS rather than blocks: a stale record is a real defect and it is not a
  reason to refuse a correct push, and a gate that blocks on everything gets
  bypassed on everything.

NO-DATA IS NEVER A PASS. A check that could not run says so and the gate refuses,
because "I could not tell" and "it is fine" are the two sentences this estate
keeps confusing, and a push is exactly where confusing them is expensive.

Python 3, standard library only. No network beyond git's own fetch.
"""
import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edition_guard  # noqa: E402

BLOCK, WARN, OK, NODATA = "BLOCK", "WARN", "OK", "NO-DATA"
EXIT_OK, EXIT_BLOCKED, EXIT_NODATA = 0, 1, 2

#: Patterns that must never leave this machine. Shapes rather than bare words:
#: a loose "sk-" matches the middle of "task-id", which produced four false
#: refusals in this estate before the pattern was tightened.
SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
)
#: Values that match a SECRET_SHAPE but are public by construction: the one
#: entry is the example access key id AWS prints in its own documentation,
#: which the products' credential-detection fixtures, a changelog and a
#: teaching page reproduce on purpose (a scanner must contain what it
#: forbids, or its tests cannot exist). Every scan strips exactly these
#: strings before the shape search; any other value of the same shape still
#: blocks. A VALUE allowlist, never a path one (an exemption by file name in
#: a scanner is a recorded leak path). scripts/export_public.py imports this
#: tuple so the two gates never drift apart.
#: Written by CONCATENATION on purpose: this file's own check_correctness
#: scans the outgoing DIFF text for these same SECRET_SHAPES, and a
#: contiguous "AKIAIOSFODNN7EXAMPLE" literal sitting in the source is
#: exactly the shape its own AKIA[0-9A-Z]{16} pattern matches. A scanner
#: must not contain what it forbids (the same trap the private-terms
#: scanner hit and solved by keeping its list outside every repository);
#: here the fix is cheaper, since the value only needs to not read as one
#: unbroken token on disk.
KNOWN_PUBLIC_EXAMPLE_VALUES = ("AKIA" + "IOSFODNN7" + "EXAMPLE",)


def strip_public_examples(text):
    """The text with every KNOWN_PUBLIC_EXAMPLE_VALUES entry removed, so the
    shape search that follows cannot match a documented example."""
    for example in KNOWN_PUBLIC_EXAMPLE_VALUES:
        text = text.replace(example, "")
    return text
#: A SCANNER MUST NOT CONTAIN WHAT IT FORBIDS, which is the same trap the
#: private-terms scanner hit this morning and solved by keeping its list outside
#: every repository. The first version of this file wrote the attribution
#: trailer and the dash characters as literals, and this estate's own push gate
#: refused the file for carrying them. Both are assembled from parts here, so
#: the pattern exists at runtime and the characters never appear in the source.
_TRAILER = "Co-" + "Authored" + "-By"
_VENDOR = "no" + "reply@" + "anthropic"
ATTRIBUTION = re.compile(
    r"%s: (Claude|Opus|Sonnet|Haiku|Fable)|%s" % (_TRAILER, _VENDOR), re.I)

#: Built from code points for the same reason: writing them literally puts them
#: in a file whose whole job is refusing them.
DASHES = (chr(0x2014), chr(0x2013))


def _git(args, cwd=ROOT, runner=None):
    # 300 seconds, not 60: `diff <branch> --not --remotes` over two hundred
    # remote refs took 54 to 63 seconds on a loaded machine (2026-09-04), and a
    # timeout here reads as NO-DATA, which refuses every push on the estate.
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=300))
    try:
        return runner(["git"] + list(args))
    except Exception:  # noqa: BLE001
        return None    # sbe: allow-silent the caller turns this into NO-DATA


def _worktree_warn(cwd=ROOT, runner=None):
    trees = _git(["worktree", "list"], cwd, runner)
    if trees is None or trees.returncode != 0:
        return []
    extra = [l for l in (trees.stdout or "").splitlines()[1:] if l.strip()]
    if not extra:
        return []
    return [(WARN, "collision",
             "%d other worktree(s) exist on this repository, so another "
             "session may be mid-edit in the same history. This warns rather "
             "than blocks: a worktree is normal, and an ABANDONED one is the "
             "hazard" % len(extra))]


def _collision_from_updates(updates, cwd=ROOT, runner=None):
    """The refs THIS push updates, judged against the sha git read from the
    remote itself moments before calling the hook. Needs no branch and no
    fetch, so it works on a detached HEAD, which is the whole point (E105)."""
    out = []
    for local_ref, local_sha, remote_ref, remote_sha in updates:
        if _is_zero(local_sha) or _is_zero(remote_sha):
            continue          # a deletion, or a ref the remote does not have
        have = _git(["cat-file", "-e", remote_sha + "^{commit}"], cwd, runner)
        if have is None or have.returncode != 0:
            out.append((BLOCK, "collision",
                        "%s on the remote is at %s, a commit this checkout "
                        "does not have. Pushing now either fails or clobbers "
                        "work somebody else landed: fetch first"
                        % (remote_ref, remote_sha[:12])))
            continue
        ff = _git(["merge-base", "--is-ancestor", remote_sha, local_sha],
                  cwd, runner)
        if ff is None or ff.returncode not in (0, 1):
            out.append((NODATA, "collision",
                        "could not tell whether this push fast-forwards %s, "
                        "so the collision check did not run. That is not a "
                        "pass" % remote_ref))
        elif ff.returncode == 1:
            out.append((BLOCK, "collision",
                        "%s is at %s, which is not an ancestor of what this "
                        "push sends it, so the push would discard commits "
                        "somebody else landed: pull first"
                        % (remote_ref, remote_sha[:12])))
    if not out:
        out.append((OK, "collision",
                    "every ref in this push fast-forwards its remote"))
    return out


def check_collision(cwd=ROOT, runner=None, stdin_text=None):
    """Other sessions. Blocks, because pushing over somebody's work is the one
    failure a revert does not undo.

    Reads the pushed refs when git gave us any (E105): they describe the push
    exactly, on a detached HEAD as well as an attached branch. Only with no
    ref lines at all (a manual run of this gate, no push in flight) does it
    fall back to judging the checked-out branch."""
    updates = _pushed_updates(stdin_text)
    if updates:
        return _collision_from_updates(updates, cwd, runner) \
            + _worktree_warn(cwd, runner)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, runner)
    if branch is None or branch.returncode != 0:
        return [(NODATA, "collision", "could not read the current branch")]
    name = (branch.stdout or "").strip()
    if name == "HEAD":
        # A DETACHED HEAD, the same pinned-battery-worktree shape
        # check_correctness (below) already special-cases (E31, battery
        # round 6). Before this, "HEAD" was taken as a branch name and
        # compared against origin/HEAD, itself a real ref (the remote's
        # default-branch symref): a battery run whose pinned SHA fell behind
        # a moving origin/main read that gap as "N commit(s) exist on
        # origin/HEAD" and BLOCKED a checkout that structurally cannot push
        # at all. Found 2026-09-03 during the E78 hardening pass.
        return [(NODATA, "collision",
                 "HEAD is detached, so this checkout has no branch to "
                 "compare against a remote. A detached head never pushes: "
                 "nothing pushes from a worktree pinned at one SHA")]
    out = []
    _git(["fetch", "-q", "origin"], cwd, runner)

    counts = _git(["rev-list", "--left-right", "--count",
                   "origin/%s...%s" % (name, name)], cwd, runner)
    if counts is None or counts.returncode != 0:
        out.append((OK, "collision",
                    "no remote copy of %s yet, so nothing to conflict with" % name))
    else:
        parts = (counts.stdout or "").split()
        behind = int(parts[0]) if parts else 0
        if behind:
            out.append((BLOCK, "collision",
                        "%d commit(s) exist on origin/%s that this branch does "
                        "not have. Pushing now either fails or clobbers work "
                        "somebody else landed: pull first" % (behind, name)))

    out.extend(_worktree_warn(cwd, runner))
    if not out:
        out.append((OK, "collision", "level with the remote, no other worktrees"))
    return out


def _default_branch(cwd=ROOT, runner=None):
    """Resolve the remote's default branch, never hardcoded. Local
    symbolic-ref first, which is free; falling back to asking the remote
    directly (git's own network call, the same allowance check_collision
    already uses for its fetch) when origin/HEAD was never set locally."""
    sym = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
               cwd, runner)
    if sym is not None and sym.returncode == 0 and (sym.stdout or "").strip():
        name = sym.stdout.strip()
        return name.split("/", 1)[1] if "/" in name else name
    remote = _git(["ls-remote", "--symref", "origin", "HEAD"], cwd, runner)
    if remote is not None and remote.returncode == 0:
        for line in (remote.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "ref:" and \
                    parts[1].startswith("refs/heads/"):
                return parts[1][len("refs/heads/"):]
    return None


def _is_zero(sha):
    """git spells "this ref does not exist on that side" as an all-zero sha
    (40 hex zeros today, 64 under sha256), never as an empty field."""
    sha = (sha or "").strip()
    return not sha or set(sha) == {"0"}


def _pushed_updates(stdin_text):
    """git's pre-push hook protocol feeds one line per ref update on stdin:
    '<local ref> <local sha1> <remote ref> <remote sha1>'. Returns those four
    fields per line, which is the ONLY authoritative description of what a
    push actually sends.

    THIS IS THE FIX FOR E105, measured 2026-09-04 21:10: every check below
    used to read the CHECKOUT's HEAD instead. A push from a worktree with a
    detached HEAD (git push hub <sha>:refs/heads/<branch>) therefore printed
    "NO-DATA collision", "NO-DATA correctness" and "HEAD is detached (a
    pinned worktree); nothing pushes from here", and the push LANDED
    unscanned. The checkout's HEAD is a guess about the push; stdin is the
    push."""
    updates = []
    for line in (stdin_text or "").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            updates.append((parts[0], parts[1], parts[2], parts[3]))
    return updates


def _pushed_refs(stdin_text):
    """The set of REMOTE ref names this push would actually update."""
    return {u[2] for u in _pushed_updates(stdin_text)}


def check_handback(cwd=ROOT, runner=None, stdin_text=None):
    """THE LAW, founder order 2026-08-30: a sub-session finishing work NEVER
    pushes the default branch itself. It pushes its feature branch only,
    then hands back to the orchestrating session, which reviews, runs the
    push gates, and merges by pull request. A PR merge is a server side act
    (gh pr merge), never a local push, so it never trips this check.

    ESCAPE HATCH, mirroring this estate's intake-gate style: BROTHER_MAIN_PUSH
    =allow skips the refusal, loudly, for the bootstrap-first-push case and
    an explicit founder order. It is never silent."""
    if os.environ.get("BROTHER_MAIN_PUSH") == "allow":
        return [(OK, "handback",
                 "BROTHER_MAIN_PUSH=allow: the default-branch push guard was "
                 "SKIPPED on purpose")]

    default = _default_branch(cwd, runner)
    if default is None:
        return [(NODATA, "handback",
                 "could not resolve the remote's default branch, so the "
                 "handback guard could not run. That is not a pass")]

    refs = _pushed_refs(stdin_text)
    if not refs:
        # No pre-push ref lines. Git's own hook protocol ALWAYS feeds one
        # line per ref update for a real push, so an empty set means this is
        # not a push at all: the battery running the gate as a plain command,
        # or a push with nothing to send. Judging the checked-out branch here
        # was the 2026-08-30 defect: the battery ran on a main checkout with
        # no push in flight and the guard blocked it, which is a false
        # refusal, the failure mode that teaches bypass. Nothing pushed,
        # nothing to judge.
        return [(OK, "handback",
                 "no push in flight (no pre-push ref lines), so no ref "
                 "updates %s" % default)]

    target = "refs/heads/%s" % default
    if target in refs:
        return [(BLOCK, "handback",
                 "this push updates %s, the default branch. THE LAW: a "
                 "sub-session finishing work never pushes the default "
                 "branch; it pushes its FEATURE BRANCH only, then hands "
                 "back to the orchestrating session, which reviews, runs "
                 "the push gates, and merges by pull request. ROUTE: push "
                 "your branch, open a PR, the main session merges. To skip "
                 "once, deliberately: BROTHER_MAIN_PUSH=allow" % default)]
    return [(OK, "handback", "no ref in this push updates %s" % default)]


def _imported_roots(cwd=ROOT, runner=None):
    """The imported product history tips from IMPORTED-HISTORY-ROOTS.txt that
    resolve in this clone (D9). Missing file or unresolvable tip means fewer
    exclusions, never more: the scan only ever gets stricter."""
    path = os.path.join(cwd, "docs", "plan", "IMPORTED-HISTORY-ROOTS.txt")
    roots = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                sha = line.split()[0] if line.split() else ""
                if not sha or sha.startswith("#"):
                    continue
                probe = _git(["cat-file", "-e", sha], cwd, runner)
                if probe is not None and probe.returncode == 0:
                    roots.append(sha)
    except OSError:
        return []
    return roots


def _ranges_from_updates(updates, cwd=ROOT, runner=None):
    """(range, shown) per ref this push updates, plus a NO-DATA finding for
    any update whose range could not be computed. E105: this is what makes a
    detached-HEAD push scannable, because the range comes from the pushed sha
    rather than from a branch the checkout may not have."""
    ranges, problems = [], []
    for local_ref, local_sha, remote_ref, remote_sha in updates:
        if _is_zero(local_sha):
            continue          # a deletion sends no objects, so nothing to scan
        probe = _git(["cat-file", "-e", local_sha + "^{commit}"], cwd, runner)
        if probe is None or probe.returncode != 0:
            problems.append((NODATA, "correctness",
                             "the sha this push sends to %s does not resolve "
                             "in this repository, so its outgoing range could "
                             "not be computed and nothing was scanned. That is "
                             "not a pass" % (remote_ref or local_ref or "?")))
            continue
        if _is_zero(remote_sha):
            # A ref the remote does not have yet: outgoing is everything under
            # this sha that no remote already carries, the same shape (and the
            # same --not toggling care) as a brand new branch below.
            ranges.append(([local_sha, "--not", "--remotes"],
                           "%s not already on any remote"
                           % (remote_ref or local_sha[:12])))
        else:
            ranges.append((["%s..%s" % (remote_sha, local_sha)],
                           "%s..%s" % (remote_sha[:12], local_sha[:12])))
    return ranges, problems


def check_correctness(cwd=ROOT, runner=None, stdin_text=None):
    """This change, over the OUTGOING RANGE rather than the working tree,
    because the range is what actually leaves the machine.

    E105, measured 2026-09-04: the range comes from git's own pre-push ref
    lines whenever there are any. Reading the checkout's HEAD instead let a
    push from a detached-HEAD worktree report NO-DATA and land unscanned. With
    no ref lines there is no push in flight, and only then does the checked
    out branch stand in."""
    updates = _pushed_updates(stdin_text)
    if updates:
        ranges, problems = _ranges_from_updates(updates, cwd, runner)
        if not ranges:
            # Every update was a deletion, or none resolved. Deletions send no
            # objects; an unresolvable sha already produced its own NO-DATA.
            return problems or [(OK, "correctness",
                                 "this push sends no commits (ref deletions "
                                 "only), so there is nothing to scan")]
        out = list(problems)
        for rng, shown in ranges:
            out.extend(_scan_range(rng, shown, cwd, runner))
        return out
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, runner)
    if branch is None or branch.returncode != 0:
        return [(NODATA, "correctness", "could not read the current branch")]
    name = (branch.stdout or "").strip()
    if name == "HEAD":
        # A DETACHED HEAD, exactly what a battery run against a worktree
        # pinned at one SHA carries. git prints the literal string HEAD
        # here, and origin/HEAD is itself a real ref (the remote's
        # default-branch symref), so the probe just below would resolve and
        # the old code took HEAD as a branch name, scanning
        # origin/HEAD..HEAD: the whole branch, from a checkout that will
        # never push. HEAD is detached, so this checkout has no outgoing
        # range: a detached head never pushes, and the gate that decides a
        # push runs on the checkout that actually pushes, not a worktree
        # pinned at one SHA. Found by battery round 6, readiness row E31.
        return [(NODATA, "correctness",
                 "HEAD is detached, so this checkout has no outgoing range. "
                 "A detached head never pushes: the gate that decides a "
                 "push runs on the checkout that actually pushes, not a "
                 "worktree pinned at one SHA")]
    probe = _git(["rev-parse", "--verify", "--quiet", "origin/%s" % name],
                 cwd, runner)
    if probe and probe.stdout.strip():
        rng = ["origin/%s..%s" % (name, name)]
        shown = rng[0]
    else:
        # A BRAND NEW BRANCH HAS NO REMOTE COUNTERPART, and this line used to
        # fall back to the bare branch name, which git reads as ALL history
        # reachable from it, back to the root commit. The gate then scanned
        # every commit this repository ever made, found every historical dash
        # and attribution line, and refused. That is a FALSE REFUSAL on the
        # first push of every new branch, and a false refusal is the worst
        # failure mode a gate has: it teaches people to pass --no-verify, and
        # then the gate protects nothing. Found 2026-08-29 by driving the hook
        # backwards on the very branch that carries it.
        rng = [name, "--not", "--remotes"]
        shown = "%s not already on any remote" % name
    return _scan_range(rng, shown, cwd, runner)


def _scan_range(rng, shown, cwd=ROOT, runner=None):
    """The four families over one outgoing range. Every caller supplies the
    range; this function never decides what is outgoing."""
    # D9, founder-approved 2026-08-31 in the question UI (record:
    # docs/decisions/2026-08-31-scanner-scope-after-subtree-imports.html).
    # Commits reachable from the imported product tips in
    # docs/plan/IMPORTED-HISTORY-ROOTS.txt were authored in the products'
    # original repositories and can never be rewritten; excluding them keeps
    # every hub-authored commit fully scanned while the immutable imports
    # stop refusing every push. HOW THE TIPS ARE APPENDED IS LOAD-BEARING,
    # measured 2026-09-01 on the first new branch pushed after D9 landed:
    # git's `--not` TOGGLES (it reverses the meaning of ^ for subsequent
    # revs, up to the next --not), so appending a SECOND --not to the
    # new-branch shape (`name --not --remotes --not tips`) turned the tips
    # POSITIVE, the scan swallowed 565 commits of immutable imported history,
    # and the gate false-refused that history's own attribution trailers as
    # if they were outgoing. `^tip` under an active --not inverts the same
    # way. The form that stays negated is the BARE tip list while --not is
    # already active, and `--not tips` when it is not; measured directly with
    # rev-list before this line was written. A tip that does not resolve is
    # skipped, which only makes the scan stricter.
    roots = _imported_roots(cwd, runner)
    exclusions = (roots if "--not" in rng else ["--not"] + roots) if roots else []
    diff = _git(["log", "-p", "--no-color"] + rng + exclusions, cwd, runner)
    # TWO SCOPES, because the families persist differently and one scope is
    # wrong for one of them.
    #
    # A SECRET OR AN ATTRIBUTION TRAILER added in one commit and removed in the
    # next is STILL PUSHED: it lives in the objects forever, and deleting the
    # line does not remove it. Those must be read from the PATCH LOG above.
    #
    # A TYPOGRAPHIC DASH is a rule about what the tree SAYS. A dash added and
    # then fixed inside the same range lands nowhere, and refusing the push
    # would demand rewriting history to satisfy a copy rule, which this estate's
    # own law forbids. Found 2026-08-29 merging a peer's handover: 32 dashes in
    # the patch log, 0 in the net result, because the peer had already scrubbed
    # them in a following commit. Refusing there would have been a false
    # refusal that could only be cleared by destroying somebody else's history.
    # D9 again for the NET dash scope: a tree diff cannot exclude commits, so
    # the imported files are excluded by PATH instead. products/ still ships
    # from the original repositories until the M6 cutover, and editing those
    # files here pre-cutover would diverge the subtrees from their sources;
    # cleanse.sh's working-tree dash scan carries the identical exclusion.
    # Removed at M6 when the shippable halves are cleaned deliberately.
    net = _git(["diff", "--no-color"] + ([rng[0].replace("..", "...")]
                                         if ".." in rng[0] else rng)
               + ["--", ".", ":(exclude)products/"], cwd, runner)
    if diff is None or diff.returncode != 0:
        return [(NODATA, "correctness",
                 "could not read the outgoing range %s, so nothing was scanned. "
                 "That is not a pass" % shown)]
    text = strip_public_examples(diff.stdout or "")
    out = []
    hits = [p.pattern for p in SECRET_SHAPES if p.search(text)]
    if hits:
        out.append((BLOCK, "correctness",
                    "%d secret-shaped value(s) in the outgoing range. The "
                    "pattern is not printed here, because printing it puts it "
                    "in a terminal and a transcript" % len(hits)))
    if ATTRIBUTION.search(text):
        out.append((BLOCK, "correctness",
                    "an attribution trailer is in the outgoing range"))
    if net is None or net.returncode != 0:
        out.append((NODATA, "correctness",
                    "the net result of %s could not be read, so the dash rule "
                    "was not applied. That is not a pass" % shown))
        n = 0
    else:
        n = sum((net.stdout or "").count(d) for d in DASHES)
    if n:
        out.append((BLOCK, "correctness",
                    "%d em or en dash(es) in the outgoing range" % n))
    if not out:
        out.append((OK, "correctness", "range %s carries none of the four" % shown))
    return out


def check_edition(cwd=ROOT, remote_url=None, env=None):
    """docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 5: would this push's
    remote be refused by the edition guard? Skipped (OK, not NODATA) only
    when the hook was run with no remote URL at all (a manual, non-push
    invocation of this gate, e.g. the battery): there is then no push to
    judge, exactly like check_handback's own no-ref-lines case."""
    if not remote_url:
        return [(OK, "edition",
                 "no remote URL given (not a push in flight), nothing to "
                 "judge")]
    code, msg = edition_guard.check_push(remote_url, cwd=cwd, env=env)
    if code == edition_guard.EXIT_OK:
        return [(OK, "edition", msg)]
    if code == edition_guard.EXIT_NODATA:
        return [(NODATA, "edition", msg)]
    return [(BLOCK, "edition", msg)]


def check_drift(cwd=ROOT, runner=None):
    """The record against the world. WARNS: a stale record is real and is not a
    reason to refuse a correct push, and a gate that blocks on everything gets
    bypassed on everything."""
    script = os.path.join(ROOT, "scripts", "record_drift.py")
    if not os.path.isfile(script):
        return [(NODATA, "drift", "record_drift.py is not present")]
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=90))
    try:
        proc = runner([sys.executable, script])
    except Exception as exc:  # noqa: BLE001
        return [(NODATA, "drift", "could not run the drift check: %s" % exc)]
    if proc.returncode == 0:
        return [(OK, "drift", "the board still matches the world")]
    return [(WARN, "drift",
             "the board has drifted from the world: %s"
             % (proc.stderr or proc.stdout or "").strip().splitlines()[0][:160])]



def check_remote_rules(cwd=ROOT, runner=None):
    """Would the REMOTE refuse this push, whatever the local state says?

    Added after the gate reported "clear" for a repository that is eight commits
    ahead of a main it cannot push to. A ruleset there requires a status check,
    and the check can never report because Actions are disabled by this estate's
    own cost law, so every route to main is closed while the local view looks
    perfectly healthy. That is the sharpest possible case for asking the remote
    rather than inferring from here: nothing local knows it.

    NO-DATA when the host cannot be asked, never OK: an unanswered question about
    whether a push will be refused is not the same as a push that will succeed.
    """
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=30))
    try:
        slug = runner(["gh", "repo", "view", "--json", "nameWithOwner",
                       "-q", ".nameWithOwner"])
    except Exception:  # noqa: BLE001
        return [(NODATA, "remote-rules", "the host could not be asked")]
    if slug.returncode != 0 or not (slug.stdout or "").strip():
        return [(NODATA, "remote-rules",
                 "could not identify the remote repository, so its rules were "
                 "not read. That is not a pass")]
    name = slug.stdout.strip()
    try:
        rules = runner(["gh", "api", "repos/%s/rules/branches/main" % name,
                        "--jq", "[.[].type]"])
    except Exception:  # noqa: BLE001
        return [(NODATA, "remote-rules", "the rules endpoint could not be read")]
    if rules.returncode != 0:
        return [(OK, "remote-rules", "no ruleset on main")]
    types = (rules.stdout or "").strip()
    if "required_status_checks" not in types:
        return [(OK, "remote-rules", "main carries no required-check rule")]
    try:
        acts = runner(["gh", "api", "repos/%s/actions/permissions" % name,
                       "--jq", ".enabled"])
        enabled = (acts.stdout or "").strip() == "true"
    except Exception:  # noqa: BLE001
        return [(NODATA, "remote-rules",
                 "main requires a status check and Actions state is unknown")]
    if not enabled:
        return [(BLOCK, "remote-rules",
                 "main requires a status check AND Actions are disabled on this "
                 "repository, so the check can never report and every route to "
                 "main is closed. This is a configuration deadlock, not a "
                 "problem with the change: it needs the rule dropped or Actions "
                 "enabled, and both are the owner's decision")]
    return [(OK, "remote-rules", "main requires a check and Actions can run it")]


def gate(cwd=ROOT, runner=None, stdin_text=None, remote_url=None, env=None):
    """Every finding, worst first."""
    found = (check_handback(cwd, runner, stdin_text)
             + check_collision(cwd, runner, stdin_text)
             + check_correctness(cwd, runner, stdin_text)
             + check_edition(cwd, remote_url, env)
             + check_remote_rules(cwd, runner) + check_drift(cwd, runner))
    rank = {BLOCK: 0, NODATA: 1, WARN: 2, OK: 3}
    found.sort(key=lambda f: rank.get(f[0], 9))
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cwd", default=ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--remote-name", default="",
                     help="git's pre-push $1: the remote's short name "
                          "(unused directly; kept for parity with the "
                          "hook's own argv)")
    ap.add_argument("--remote-url", default="",
                     help="git's pre-push $2: the remote URL this push "
                          "targets, fed to the edition guard")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    # git's pre-push hook feeds ref updates on stdin. Read them when present
    # (a real hook invocation, or a test piping them in); an interactive
    # terminal is never blocked on, since it has nothing queued.
    stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()

    found = gate(args.cwd, stdin_text=stdin_text,
                 remote_url=args.remote_url or None)
    if args.json:
        print(json.dumps([{"level": a, "family": b, "detail": c}
                          for a, b, c in found], indent=2))
    else:
        for level, family, detail in found:
            stream = sys.stderr if level in (BLOCK, NODATA) else sys.stdout
            print("%-8s %-12s %s" % (level, family, detail), file=stream)

    if any(f[0] == BLOCK for f in found):
        print("pre-push: REFUSED", file=sys.stderr)
        return EXIT_BLOCKED
    if any(f[0] == NODATA for f in found):
        # A DETACHED HEAD (a battery worktree pinned at one SHA) never
        # pushes, so check_collision's and check_correctness's NO-DATA for
        # it are not "something about a real push could not be checked",
        # they are the correct, structural answer for a checkout with no
        # outgoing range at all. Exiting NO-DATA for that is what forced
        # docs/plan/BATTERY-EXPECTATIONS.json to carry a known_no_data
        # declaration for this gate; exiting clear here removes the need
        # for the workaround at its source. An ATTACHED branch whose real
        # state could not be read still refuses below, exactly as before.
        # E105, measured 2026-09-04 21:10: this escape used to fire on the
        # checkout's HEAD alone, so a REAL push from a detached-HEAD worktree
        # (git push hub <sha>:refs/heads/<branch>) printed exactly this line
        # and landed unscanned. A detached HEAD legitimately pushes nothing
        # only when git handed the hook NO REF LINES at all.
        head = _git(["rev-parse", "--abbrev-ref", "HEAD"], args.cwd)
        if not _pushed_updates(stdin_text) \
                and head is not None and head.returncode == 0 \
                and (head.stdout or "").strip() == "HEAD":
            print("pre-push: HEAD is detached and no ref lines arrived (a "
                  "pinned worktree, no push in flight); nothing pushes from "
                  "here", file=sys.stderr)
            return EXIT_OK
        print("pre-push: NO-DATA, something could not be checked, which is not "
              "a pass", file=sys.stderr)
        return EXIT_NODATA
    print("pre-push: clear")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
