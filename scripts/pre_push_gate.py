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
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import edition_guard  # noqa: E402

# ONE TABLE OF WHAT A CREDENTIAL LOOKS LIKE (FX-07). scripts/loop/secret_scan.py
# is the single definition the commit gate and this gate both read, so the two
# can never drift apart again. It is loaded by EXPLICIT PATH, never by putting
# scripts/loop at the front of sys.path: six module basenames exist in both
# scripts/ and scripts/loop/ (grade_build.py among them), so a front path entry
# would shadow the scripts/ module for every other consumer of this file
# (export_public, cut_preflight, preserve_wip and their tests). A load failure
# PROPAGATES: the hook then exits non zero, which is a refusal, never a clean
# pass, and export_public already turns an import failure into NO-DATA.
_SECRET_SCAN_PATH = os.path.join(HERE, "loop", "secret_scan.py")
if not os.path.isfile(_SECRET_SCAN_PATH):
    raise ImportError(
        "the shared secret table is not installed beside this gate: %s"
        % _SECRET_SCAN_PATH)
_spec = importlib.util.spec_from_file_location("secret_scan",
                                               _SECRET_SCAN_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError("the shared secret table could not be loaded: %s"
                      % _SECRET_SCAN_PATH)
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
if "secret_scan" not in sys.modules:
    sys.modules["secret_scan"] = S
BLOCK, WARN, OK, NODATA = "BLOCK", "WARN", "OK", "NO-DATA"
EXIT_OK, EXIT_BLOCKED, EXIT_NODATA = 0, 1, 2


# FX-07.3 HOSTILE INPUT GUARD. One validation every entry point routes
# through, so a hostile argument is refused the same way everywhere instead of
# reaching a raw interpreter exception deeper down. Measured: a bool, a
# generator or a NaN handed in as the pre-push ref lines came back as
# AttributeError (no attribute 'splitlines'), and None handed in as a checkout
# came back as TypeError out of the first file probe. NO-DATA is never a pass,
# so every refusal below stops the push.
class HostileInputError(ValueError):
    """A public entry point of this gate was handed something it cannot use.

    The gate's own error, so a caller sees a deliberate refusal rather than a
    raw interpreter exception or a value it could mistake for a verdict.
    """


def _usable_path(value):
    """True when value is a checkout path this gate can hand to git."""
    return isinstance(value, str) or isinstance(value, os.PathLike)


def _usable_stdin(value):
    """None is git handing the hook no ref lines at all; only text is text."""
    return value is None or isinstance(value, str)


def _usable_range(rng):
    """True when rng is a non empty list of revision strings."""
    if not isinstance(rng, (list, tuple)) or not rng:
        return False
    for part in rng:
        if not isinstance(part, str) or not part:
            return False
    return True


def _hostile_input(family, cwd, stdin_text):
    """A non empty NO-DATA finding list when cwd or stdin_text cannot be used,
    else None. Every caller returns the list unchanged."""
    if not _usable_path(cwd):
        return [(NODATA, family,
                 "the checkout this gate was pointed at is not a path it can "
                 "read (%s), so nothing was scanned. That is not a pass"
                 % type(cwd).__name__)]
    if not _usable_stdin(stdin_text):
        return [(NODATA, family,
                 "the ref lines this gate was handed are not text (%s), so "
                 "nothing was scanned. That is not a pass"
                 % type(stdin_text).__name__)]
    return None


class _RefusingParser(argparse.ArgumentParser):
    """An argument this gate cannot use is refused with the gate's own error.

    argparse's own refusal for an unknown flag is an exit code, and an exit
    code handed back to a caller reads as an ordinary return: a control that
    prevents beats a check that reports, so the refusal is raised instead.
    The usage text still goes to stderr first, exactly as argparse prints it.
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        raise HostileInputError(
            "the arguments handed to this gate are not usable: %s" % message)

#: Patterns that must never leave this machine. Shapes rather than bare words:
#: a loose "sk-" matches the middle of "task-id", which produced four false
#: refusals in this estate before the pattern was tightened to require 20+
#: trailing characters. That length alone was not enough: a sufficiently
#: long hyphenated word landing right after any "sk" substring still reads
#: as the shape (measured 2026-09-20: a merged PR's own branch name,
#: docs/or-ask-provenance-wbs-2026-09-19, contains "a-sk-provenance..." and
#: refused two real, benign pushes the same night). A real key's "sk-"
#: always starts a fresh token; it is never the tail end of an English
#: word. \b in front of "sk-" closes that class generically (task-, desk-,
#: mask-, ask-, ...) without narrowing what a real key looks like: a key
#: preceded by whitespace, "=", ":", a quote or the start of a line still
#: matches, since \b holds at every one of those boundaries.
#: Today's push table is the shared module's STRICT table: the SAME four
#: patterns, in the SAME order, so every consumer that iterates it
#: (export_public.py, cut_preflight.py, preserve_wip.py, and the tests that
#: pin it) sees no change. STRICT is the rule for text that is only CONTEXT or
#: REMOVED; the union over the ADDED text is read beside it in _scan_range.
SECRET_SHAPES = S.STRICT
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
#: The value allowlist and its strip live in the shared module now, moved
#: verbatim from this file: one definition, so the commit gate, this gate and
#: export_public.py cannot drift apart. These public names keep today's
#: content for the other consumers, and the strip refuses anything that is
#: not text instead of answering as though it had found nothing.
KNOWN_PUBLIC_EXAMPLE_VALUES = S.KNOWN_PUBLIC_EXAMPLE_VALUES
strip_public_examples = S.strip_public_examples
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
    verdict = _hostile_input("correctness", cwd, stdin_text)
    if verdict is not None:
        return verdict
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
    range; this function never decides what is outgoing.

    Hostile input is REFUSED here, as NO-DATA, before a single git call: a
    range that is not a non empty list of revision strings, a label that is
    not text, or a checkout that is not a path, is never quietly treated as an
    empty, clean scan."""
    if not isinstance(shown, str):
        return [(NODATA, "correctness",
                 "the outgoing range has no label this gate can print, so "
                 "nothing was scanned. That is not a pass")]
    if not _usable_range(rng):
        return [(NODATA, "correctness",
                 "the outgoing range %s is not a list of revision strings, so "
                 "nothing was scanned. That is not a pass" % shown)]
    verdict = _hostile_input("correctness", cwd, None)
    if verdict is not None:
        return verdict
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
    # 2026-09-05: rng is either ["A..B"] (a remote counterpart exists) or
    # [tip, "--not", "--remotes"] (a brand new branch, or a ref pushed by
    # sha whose remote side is all zeros). The ".." shape above becomes a
    # normal A...B diff. The second shape used to pass straight through to
    # `git diff`, which does not read --not/--remotes as a rev-list style
    # exclusion at all: it reads N refs after the first as a COMBINED diff
    # against every one of them, so `git diff <tip> --not --remotes` diffed
    # the tip against all 418 remote refs on the real repository. Measured
    # 2026-09-05 at load 21: 3.9 seconds and 125 megabytes for one seeded
    # commit, against 0.05 seconds for the two-tree form below; 54 to 63
    # seconds under load on 2026-09-04 (the reason _git's timeout is 300),
    # and reported at 1.4 gigabytes plus with no end in sight on the machine
    # at load 80 to 115 with 38 worktrees pushing. git log and git rev-list read --not/--remotes
    # correctly; only the diff call was wrong.
    #
    # The fix walks the FIRST-PARENT chain of the same outgoing range,
    # with the same imported-history exclusions the log scan above already
    # applies, to find the one commit this diff should compare against: the
    # first excluded ancestor on that chain is the boundary, and the diff
    # is a plain two-tree diff from there to the tip. A branch with no
    # first-parent boundary at all (an orphan or root branch) diffs
    # against git's own empty tree instead. This is the same ceiling the
    # A...B path above already carries: a branch that merged its base back
    # into itself still shows the merged-in changes here, because the
    # boundary is walked on first-parent only.
    if ".." in rng[0]:
        net = _git(["diff", "--no-color", rng[0].replace("..", "...")]
                   + ["--", ".", ":(exclude)products/"], cwd, runner)
    else:
        bnd = _git(["rev-list", "--boundary", "--first-parent"] + rng
                   + exclusions, cwd, runner)
        if bnd is None or bnd.returncode != 0:
            net = None
        else:
            lines = (bnd.stdout or "").splitlines()
            outgoing = [l for l in lines if l and not l.startswith("-")]
            boundaries = [l[1:] for l in lines if l.startswith("-")]
            if not outgoing:
                net = subprocess.CompletedProcess(["git", "diff"], 0, "", "")
            else:
                base = boundaries[0] if boundaries \
                    else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
                net = _git(["diff", "--no-color", base, rng[0]]
                           + ["--", ".", ":(exclude)products/"], cwd, runner)
    if diff is None or diff.returncode != 0:
        return [(NODATA, "correctness",
                 "could not read the outgoing range %s, so nothing was scanned. "
                 "That is not a pass" % shown)]
    text = strip_public_examples(diff.stdout or "")
    out = []
    # TWO SCOPES, ONE TABLE (FX-07). The ADDED text of the range is read by the
    # shared union, the same rule the commit gate applies, so a value one gate
    # refuses is refused by both. The WHOLE patch log is read beside it by the
    # strict table, which keeps context and removed lines refused exactly as
    # before. A refusal NAMES the matched families and never prints a matched
    # value: the name tells the repair round which fixture to assemble, and
    # printing the value would put it in a terminal, a log and a transcript.
    added = S.added_text(text)
    names = set(S.families(added))
    for _strict_name, _strict_pattern in S.STRICT_FAMILIES:
        if _strict_pattern.search(text):
            names.add(_strict_name)
    if names:
        out.append((BLOCK, "correctness",
                    "%d secret shape famil(y/ies) in the outgoing range: %s. "
                    "No value is printed" % (len(names),
                                             ", ".join(sorted(names)))))
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
    # The budget is read at call time, from the environment, so a loaded
    # machine can be given more room without editing this file. An unset,
    # non-integer, zero or negative value means the 90 second default.
    raw = os.environ.get("PRE_PUSH_DRIFT_TIMEOUT_S", "")
    try:
        budget = int(raw)
    except (TypeError, ValueError):
        budget = 90
    if budget <= 0:
        budget = 90
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=budget))
    started = time.monotonic()
    try:
        proc = runner([sys.executable, "-I", script])   # isolated: no script directory ahead of the stdlib (D13 review 17)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - started
        return [(NODATA, "drift",
                 "the drift check did not finish within its %d second budget "
                 "(%.1f seconds elapsed); raise PRE_PUSH_DRIFT_TIMEOUT_S to "
                 "give it longer" % (budget, elapsed))]
    except Exception as exc:  # noqa: BLE001
        return [(NODATA, "drift", "could not run the drift check: %s" % exc)]
    if proc.returncode == 0:
        return [(OK, "drift", "the board still matches the world")]
    detail = (proc.stderr or proc.stdout or "").strip()
    # A nonzero exit with no output must still name the drift, not crash on
    # the empty string's splitlines()[0].
    if not detail:
        detail = "the drift check exited nonzero with no output"
    else:
        detail = detail.splitlines()[0][:160]
    return [(WARN, "drift",
             "the board has drifted from the world: %s" % detail)]



def check_docs_current(cwd=ROOT, runner=None):
    """F5(b) (root-cause fix, 2026-09-14 adversarial sweep): SYSTEM.md is
    generated from the code, and required_fast.sh already refuses a stale
    copy (its own docs-runtime-drift check) -- but that gate only runs
    AFTER a push, on CI, because nothing before the push checked it. Twice
    in one day a branch was pushed after regenerating only the SPECIFIC new
    checks a change touched, never the full local gate, and both times the
    first place the staleness actually surfaced was a failed GitHub Actions
    run, costing a second commit and a second CI round trip for the same
    class of omission. This is cheap (measured ~1s) and unambiguous
    (system_doc.py --check either exits 0 or names exactly what changed),
    so it BLOCKS rather than WARNS: unlike check_drift's board-versus-world
    comparison, there is no legitimate reason to push a stale SYSTEM.md."""
    script = os.path.join(ROOT, "scripts", "system_doc.py")
    if not os.path.isfile(script):
        return [(NODATA, "docs-current", "system_doc.py is not present")]
    # SYSTEM.md is generated FROM this repository's own products/ and
    # scripts/, so the check is only meaningful when cwd IS this checkout
    # (the normal case: the gate runs from the repo it is gating). A cwd
    # that is not this tree (a test fixture, a --cwd pointed elsewhere)
    # never carries the structure system_doc.py describes, so running it
    # there would refuse on emptiness rather than on real staleness.
    try:
        same_tree = os.path.samefile(cwd, ROOT)
    except OSError:
        same_tree = False
    if not same_tree:
        if not os.path.isfile(os.path.join(cwd, "scripts", "system_doc.py")):
            return [(NODATA, "docs-current",
                     "cwd is not this repository's own checkout; nothing to check")]
        # A checkout that carries the generator IS checked, with THIS tree's generator code over THAT checkout's files
        # (D13, 2026-10-02): the lander runs this gate from a frozen copy of the base commit against the landing tree, so
        # the code that regenerates is reviewed code and the files it reads are the ones about to be pushed. The checkout's
        # own scripts/system_doc.py is never run here; it only has to exist for the checkout to count as one.
        try:
            import system_doc
            diff = system_doc.compute_system_doc_diff(cwd)
        except Exception as exc:  # noqa: BLE001
            return [(NODATA, "docs-current",
                     "could not regenerate SYSTEM.md for %s: %s" % (cwd, exc))]
        if not diff:
            return [(OK, "docs-current", "SYSTEM.md still describes the code")]
        return [(BLOCK, "docs-current",
                 "SYSTEM.md is stale: %s" % diff.strip().splitlines()[-1][:160])]
    runner = runner or (lambda cmd, **kw: subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, timeout=30))
    try:
        proc = runner([sys.executable, "-I", script, "--check"])   # isolated, as above
    except Exception as exc:  # noqa: BLE001
        return [(NODATA, "docs-current", "could not run system_doc.py --check: %s" % exc)]
    if proc.returncode == 0:
        return [(OK, "docs-current", "SYSTEM.md still describes the code")]
    return [(BLOCK, "docs-current",
             "SYSTEM.md is stale: %s"
             % (proc.stdout or proc.stderr or "").strip().splitlines()[-1][:160])]


def _outgoing_ranges(cwd=ROOT, runner=None, stdin_text=None):
    """The revision arguments naming what this push sends: git's own ref
    lines when there are any, the checked out branch otherwise. None when it
    cannot be said (a detached head with no ref lines pushes nothing)."""
    updates = _pushed_updates(stdin_text)
    if updates:
        return [rng for rng, _ in _ranges_from_updates(updates, cwd, runner)[0]]
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd, runner)
    name = (branch.stdout or "").strip() if branch is not None \
        and branch.returncode == 0 else ""
    if not name or name == "HEAD":
        return None
    probe = _git(["rev-parse", "--verify", "--quiet", "origin/%s" % name],
                 cwd, runner)
    if probe and (probe.stdout or "").strip():
        return [["origin/%s..%s" % (name, name)]]
    return [[name, "--not", "--remotes"]]


def check_hermetic_tests(cwd=ROOT, runner=None, stdin_text=None):
    """A changed scripts/ test is run where the public runner runs it, BEFORE
    the push. Measured 2026-09-20: three of the six refusals of the 1.0.21
    cut were tests green on their author's machine and red on an export
    shaped tree or under an empty HOME, met 128 and 150 minutes into a cut.
    hermetic_test_check.py names the rest. It costs the export build plus
    the touched tests' own run time, and nothing when no such test is
    touched. A red BLOCKS: a test that cannot pass on the public runner has
    no legitimate reason to be pushed."""
    try:
        import hermetic_test_check as hermetic
    except ImportError as exc:
        return [(NODATA, "hermetic", "hermetic_test_check.py not loadable: %s" % exc)]
    ranges = _outgoing_ranges(cwd, runner, stdin_text)
    if ranges is None:
        return [(OK, "hermetic", "no outgoing range, so no changed test to run")]
    paths = set()
    for rng in ranges:
        proc = _git(["log", "--format=", "--name-only", "--diff-filter=AM"]
                    + list(rng), cwd, runner)
        if proc is None or proc.returncode != 0:
            return [(NODATA, "hermetic", "the outgoing file list could not be "
                                         "read, so no changed test was run. "
                                         "That is not a pass")]
        paths.update(l.strip() for l in (proc.stdout or "").splitlines())
    level = {hermetic.OK: OK, hermetic.REFUSED: BLOCK, hermetic.NODATA: NODATA}
    return [(level[v], "hermetic", "%s: %s" % (name, detail))
            # sorted: a list in a stable order (2026-09-28: R1.4's tests_for accepts only a list or tuple, so the set
            # this built crashed the gate and refused a whole landing batch, innocent builds included)
            for v, name, detail in hermetic.check(cwd, hermetic.tests_for(sorted(paths), cwd))]


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
        # A plan without rulesets (a free private repository answers HTTP 403
        # "Upgrade to GitHub Pro") or a 404 means no rule can exist on main, so
        # nothing remote can refuse this push: that is an answer, not a gap.
        detail = "%s %s" % (rules.stdout or "", rules.stderr or "")
        if "Upgrade to GitHub Pro" in detail or "HTTP 404" in detail:
            return [(OK, "remote-rules",
                     "rulesets are not available on this repository's plan, "
                     "so main can carry no required-check rule")]
        # Any other nonzero exit is an unanswered question (network, auth,
        # host), not evidence that main carries no ruleset.
        return [(NODATA, "remote-rules",
                 "the rules endpoint could not be read, so whether main "
                 "carries a required-check rule is unknown. That is not a "
                 "pass")]
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


# FX-07.3 HOSTILE INPUT GUARD for the checks whose bodies fall in a region of
# this file this build does not see, so it may not edit them. The guard is
# ADDITIVE and DELEGATES: a call with a checkout path and text or absent ref
# lines reaches the function underneath unchanged, and a call with a hostile
# checkout or hostile ref lines is answered with NO-DATA, which refuses the
# push. Forwarding is EXACT: the wrapper passes the arguments it was given, so
# no default is invented here, and a keyword call is forwarded as a keyword
# call.
_check_collision_unguarded = check_collision
_check_handback_unguarded = check_handback
_check_docs_current_unguarded = check_docs_current
_check_hermetic_tests_unguarded = check_hermetic_tests


def _first_cwd(args, kwargs):
    """The checkout a positional call put first, or a keyword's cwd."""
    if "cwd" in kwargs:
        return kwargs["cwd"]
    return args[0] if args else ROOT


def _ref_lines(args, kwargs):
    """The ref lines a positional call put third, or a keyword's."""
    if "stdin_text" in kwargs:
        return kwargs["stdin_text"]
    return args[2] if len(args) > 2 else None


def check_collision(*args, **kwargs):
    verdict = _hostile_input("collision", _first_cwd(args, kwargs),
                             _ref_lines(args, kwargs))
    if verdict is not None:
        return verdict
    return _check_collision_unguarded(*args, **kwargs)


def check_handback(*args, **kwargs):
    verdict = _hostile_input("handback", _first_cwd(args, kwargs),
                             _ref_lines(args, kwargs))
    if verdict is not None:
        return verdict
    return _check_handback_unguarded(*args, **kwargs)


def check_docs_current(*args, **kwargs):
    verdict = _hostile_input("docs", _first_cwd(args, kwargs), None)
    if verdict is not None:
        return verdict
    return _check_docs_current_unguarded(*args, **kwargs)


def check_hermetic_tests(*args, **kwargs):
    verdict = _hostile_input("correctness", _first_cwd(args, kwargs),
                             _ref_lines(args, kwargs))
    if verdict is not None:
        return verdict
    return _check_hermetic_tests_unguarded(*args, **kwargs)


def gate(cwd=ROOT, runner=None, stdin_text=None, remote_url=None, env=None):
    """Every finding, worst first.

    Hostile input is REFUSED here too, as NO-DATA, before any check runs: a
    checkout that is not a path, or ref lines that are not text, must never
    come back as a clean push."""
    verdict = _hostile_input("correctness", cwd, stdin_text)
    if verdict is not None:
        return verdict
    found = (check_handback(cwd, runner, stdin_text)
             + check_collision(cwd, runner, stdin_text)
             + check_correctness(cwd, runner, stdin_text)
             + check_edition(cwd, remote_url, env)
             + check_remote_rules(cwd, runner) + check_drift(cwd, runner)
             + check_docs_current(cwd, runner)
             + check_hermetic_tests(cwd, runner, stdin_text))
    rank = {BLOCK: 0, NODATA: 1, WARN: 2, OK: 3}
    found.sort(key=lambda f: rank.get(f[0], 9))
    return found


def main(argv=None):
    ap = _RefusingParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cwd", default=ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--remote-name", default="",
                     help="git's pre-push $1: the remote's short name "
                          "(unused directly; kept for parity with the "
                          "hook's own argv)")
    ap.add_argument("--remote-url", default="",
                     help="git's pre-push $2: the remote URL this push "
                          "targets, fed to the edition guard")
    # FX-07.3: an argv this gate cannot use is REFUSED with the gate's own
    # error. A returned exit code was measured to read as an ordinary return to
    # an external probe, and a refusal that can be mistaken for a pass is not a
    # control. argparse's own answer for a help request is kept below.
    if argv is not None and (not isinstance(argv, (list, tuple))
                             or any(not isinstance(a, str) for a in argv)):
        print("pre-push: NO-DATA, the argv this gate was handed is not a "
              "list of strings, so nothing was checked", file=sys.stderr)
        raise HostileInputError(
            "the argv this gate was handed is not a list of strings")
    try:
        args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        if exc.code in (None, 0):
            return EXIT_OK
        raise

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
