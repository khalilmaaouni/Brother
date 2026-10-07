#!/usr/bin/env python3
"""The run line is never a PR head that moves under a pin: frozen-head pull requests, pin checks, the protection read
and the one terminal prompt (MG1.d).

WHY THIS EXISTS. A pull request whose head IS the run line branch breaks its pin whenever the loop lands: the head
moves, `gh pr merge --match-head-commit` refuses, and the batch stops. Ruled D7 on 2026-10-03: a MOVING head (default
pattern `loop/run-*`) is merged only through a FROZEN-HEAD PULL REQUEST. `freeze_pin` pushes `merge-pin/pr<N>-<sha12>`
to the hub at the pinned sha, never with force; `open_frozen_pr` opens a pull request from that branch to main naming
the original PR and the sha; merge_verified.sh then merges the FROZEN pull request with the same
`gh pr merge --merge --match-head-commit <sha>` every static PR gets (one merge path, this tool never pushes main). The
loop's later commits are not merged; they belong to the next batch.

WHAT IT PROVES, AND WHAT IT NEVER DOES. `check_pins` runs before any prompt and refuses only a `pin=` branch that does
not resolve on the remote to the pinned sha, a `frozen=` pull request whose head is not that branch, a pinned sha that
is no longer an ancestor of the moving ref's current head (a force push happened), and any remote read failure
(NO-DATA blocks); a moving entry with no pin yet is PIN-PENDING, printed as `will freeze`, never a refusal.
`main_protected` answers True only when hub main requires a pull request and enforces that on administrators too (one
shared admin account: a bypassable protection reads False), False when it does not, None when the answer cannot be
read; merge_verified.sh refuses a real run on False or None before its first write. `owner_confirmed` reads
ONLY the terminal: no environment, file or flag stands in for the typed count, and a stdin that is not a terminal is
False. It is an accident stop, not authentication (D7): the owner and a session share one account.

STDLIB ONLY, Python 3.9 floor. This module runs no process of its own: every git and gh call goes through the runner
the caller injects, and the command line injects subprocess.run with stdin closed. The write commands (freeze, open,
note) and the prompt refuse a steered environment the way every other real run of this unit does (R-MG-8).

Usage (check, open and protected take `--gh <absolute path>` first: the gh merge_verified.sh pinned before it reset
PATH; without it `gh` is looked up on the caller's PATH, which a real run never allows):
  merge_pins.py moving <ref>                                      exit 0 when the ref is a moving head, 1 when not
  merge_pins.py check [--gh P] <clone> <remote> <pr> <sha> <head_ref> [pin=<branch>] [frozen=<pr>]
  merge_pins.py freeze <clone> <remote> <pr> <sha>                push the frozen branch at the sha, print its name
  merge_pins.py open [--gh P] <repo> <pr> <sha> <branch>          open the frozen-head pull request, print its number
  merge_pins.py note <list> <pr> [pin=<branch>] [frozen=<pr>]     rewrite that list line in place with the pin fields
  merge_pins.py protected [--gh P] <repo> [branch]                exit 0 required, 1 not required, 2 cannot be read
  merge_pins.py confirm <count>                                   exit 0 only when the terminal typed exactly <count>
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import merge_gate  # noqa: E402

REMOTE = "hub"
BRANCH = "main"
MOVING_ENV = "MERGE_PINS_MOVING"
DEFAULT_MOVING = ("loop/run-*",)
PIN_PREFIX = "merge-pin/"
HEADS = "refs/heads/"
USAGE = ("usage: merge_pins.py moving <ref> | check [--gh P] <clone> <remote> <pr> <sha> <head_ref> [pin=<branch>] "
         "[frozen=<pr>] | freeze <clone> <remote> <pr> <sha> | open [--gh P] <repo> <pr> <sha> <branch> | "
         "note <list> <pr> [pin=<branch>] [frozen=<pr>] | protected [--gh P] <repo> [branch] | confirm <count>")

MergeGateError = merge_gate.MergeGateError
MergeNoData = merge_gate.MergeNoData
_as_str = merge_gate._as_str
_as_env = merge_gate._as_env
SHA_RE = merge_gate.TREE_RE


class PinRefused(MergeGateError):
    """A pin this module must never accept: a moved branch, a rewritten head, a wrong answer (exit 1)."""


def default_runner(argv, cwd=None, env=None):
    """subprocess.run with stdin closed and text output; the one process path of this module."""
    return subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          errors="replace")


def _runner(runner):
    if runner is None:
        return default_runner
    if not callable(runner):
        raise MergeGateError("runner must be callable, not %s" % type(runner).__name__)
    return runner


def gh_runner(gh_path):
    """The default runner with `gh` pinned to one absolute executable: merge_verified.sh resolves gh ONCE, before it
    resets PATH to the system's, and hands that path here, so this module never re-reads PATH for it. A path that is
    not an absolute executable file is NO-DATA."""
    gh_path = _as_str(gh_path, "gh_path")
    if not os.path.isabs(gh_path) or not os.path.isfile(gh_path) or not os.access(gh_path, os.X_OK):
        raise MergeNoData("the gh at %r is not an absolute executable file" % gh_path[:200])

    def run(argv, cwd=None, env=None):
        if argv and argv[0] == "gh":
            argv = [gh_path] + list(argv[1:])
        return default_runner(argv, cwd=cwd, env=env)
    return run


def _call(runner, argv, cwd=None, env=None):
    return merge_gate._call(runner, argv, cwd, env)


def _pr_number(value, what="pr"):
    value = _as_str(value, what).strip()
    if not value.isdigit():
        raise MergeGateError("%s is a pull request number, digits, not %r" % (what, value[:40]))
    return value


def _sha(value, what="sha"):
    value = _as_str(value, what).strip()
    if not SHA_RE.match(value):
        raise MergeGateError("%s must be a 40 hex sha, not %r" % (what, value[:60]))
    return value


def _ref_name(value, what):
    """A branch name without its refs/heads/ prefix; one that is empty or carries whitespace is refused."""
    value = _as_str(value, what).strip()
    if value.startswith(HEADS):
        value = value[len(HEADS):]
    if not value or any(ch.isspace() for ch in value) or ".." in value or value.startswith("-"):
        raise MergeGateError("%s is not a branch name: %r" % (what, value[:60]))
    return value


# ---------------------------------------------------------------------------------------------------------------------
# moving heads
# ---------------------------------------------------------------------------------------------------------------------
def moving_patterns(env=None):
    """The default pattern plus any the environment ADDS (MERGE_PINS_MOVING, comma separated). The default is never
    removed: a value can widen what is frozen, never narrow it, so no variable can turn a moving head static."""
    environment = _as_env(env)
    extra = environment.get(MOVING_ENV, "")
    if not isinstance(extra, str):
        raise MergeGateError("%s must be a string" % MOVING_ENV)
    patterns = list(DEFAULT_MOVING)
    for item in extra.split(","):
        item = item.strip()
        if item and item not in patterns:
            patterns.append(item)
    return patterns


def is_moving_ref(ref, patterns=None):
    """True when the head ref matches a moving pattern. The default `loop/run-*` always applies; `patterns` can only
    add to it."""
    ref = _ref_name(ref, "ref")
    if patterns is None:
        patterns = []
    if isinstance(patterns, (str, bytes)) or not isinstance(patterns, (list, tuple)):
        raise MergeGateError("patterns must be a list of strings, not %s" % type(patterns).__name__)
    for pattern in patterns:
        if isinstance(pattern, bool) or not isinstance(pattern, str) or not pattern.strip():
            raise MergeGateError("every pattern is a non empty string")
    for pattern in list(DEFAULT_MOVING) + list(patterns):
        if fnmatch.fnmatchcase(ref, pattern.strip()):
            return True
    return False


def pin_branch(pr, sha):
    """`merge-pin/pr<N>-<sha12>`: the frozen branch name for one pin."""
    return "%spr%s-%s" % (PIN_PREFIX, _pr_number(pr), _sha(sha)[:12])


# ---------------------------------------------------------------------------------------------------------------------
# the remote, read through git
# ---------------------------------------------------------------------------------------------------------------------
def _ls_remote(runner, clone, remote, ref):
    """The sha the remote holds for refs/heads/<ref>, or "" when it holds none. Any other answer is NO-DATA."""
    full = HEADS + ref
    code, out, err = _call(runner, ["git", "ls-remote", "--exit-code", remote, full], clone)
    if code == 2:
        return ""
    if code != 0:
        raise MergeNoData("cannot read %s on %s (git ls-remote exited %d: %s)" % (ref, remote, code,
                                                                                  (err or out).strip()[:200]))
    found = ""
    for line in out.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[1] != full:
            continue
        if not SHA_RE.match(fields[0]) or (found and found != fields[0]):
            raise MergeNoData("%s on %s answers no single sha" % (ref, remote))
        found = fields[0]
    if not found:
        raise MergeNoData("%s on %s: git ls-remote printed no row for it" % (ref, remote))
    return found


def _have_commit(runner, clone, remote, sha):
    """The commit is in the clone, fetched from the remote when it is not; a sha the remote will not serve is NO-DATA."""
    code, _out, _err = _call(runner, ["git", "cat-file", "-e", sha + "^{commit}"], clone)
    if code == 0:
        return
    code, out, err = _call(runner, ["git", "fetch", "--quiet", remote, sha], clone)
    if code != 0:
        raise MergeNoData("%s is not in the clone and %s will not serve it (fetch the head ref first): %s"
                          % (sha[:12], remote, (err or out).strip()[:200]))


def _is_ancestor(runner, clone, remote, sha, tip):
    """True or False from `git merge-base --is-ancestor`; any other exit is NO-DATA, never an answer."""
    _have_commit(runner, clone, remote, tip)
    code, out, err = _call(runner, ["git", "merge-base", "--is-ancestor", sha, tip], clone)
    if code == 0:
        return True
    if code == 1:
        return False
    raise MergeNoData("git merge-base --is-ancestor exited %d: %s" % (code, (err or out).strip()[:200]))


def _repo_of(runner, clone, remote):
    """owner/repo from the clone's remote URL, the way merge_verified.sh derives it; a path stays a path."""
    url = merge_gate._git_text(runner, clone, ["config", "--get", "remote.%s.url" % remote]).strip()
    if not url:
        raise MergeNoData("the %s remote of %s has no url" % (remote, clone))
    for marker in ("github.com:", "github.com/"):
        if marker in url:
            url = url.split(marker, 1)[1]
            break
    return url[:-4] if url.endswith(".git") else url


# ---------------------------------------------------------------------------------------------------------------------
# the writes: the frozen branch and its pull request
# ---------------------------------------------------------------------------------------------------------------------
def freeze_pin(clone, pr, sha, remote=REMOTE, runner=None):
    """Push `merge-pin/pr<N>-<sha12>` to the remote at the pinned sha, WITHOUT force, and return its name. A branch
    already there at that sha is the same actor again (returned, nothing pushed); one at another sha is a rewrite
    and is refused, never overwritten. The push is proven by reading the remote back."""
    clone = _as_str(clone, "clone")
    pr = _pr_number(pr)
    sha = _sha(sha)
    remote = _ref_name(remote, "remote")
    runner = _runner(runner)
    branch = pin_branch(pr, sha)
    have = _ls_remote(runner, clone, remote, branch)
    if have == sha:
        return branch
    if have:
        raise PinRefused("#%s: %s already exists on %s at %s, not %s: a moved pin is a rewrite, never overwritten"
                         % (pr, branch, remote, have[:12], sha[:12]))
    _have_commit(runner, clone, remote, sha)
    code, out, err = _call(runner, ["git", "push", "--quiet", remote, "%s:%s%s" % (sha, HEADS, branch)], clone)
    if code != 0:
        raise PinRefused("#%s: cannot push %s to %s (git push exited %d: %s)"
                         % (pr, branch, remote, code, (err or out).strip()[:200]))
    now = _ls_remote(runner, clone, remote, branch)
    if now != sha:
        raise MergeNoData("#%s: %s was pushed but %s now holds %s for it, not %s"
                          % (pr, branch, remote, now[:12] or "nothing", sha[:12]))
    return branch


def _pr_rows(out, what):
    try:
        rows = json.loads(out)
    except ValueError:
        raise MergeNoData("%s printed no JSON" % what)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise MergeNoData("%s printed no list of pull requests" % what)
    return rows


def open_frozen_pr(repo, pr, sha, branch, base=BRANCH, runner=None):
    """Open the pull request from the frozen branch to `base`, titled and bodied with the original PR and the pinned
    sha, and return its number as gh printed it. One already open from that branch at that head is returned (the
    same actor again); one at another head is refused; a gh failure, or a gh that prints no number, is NO-DATA:
    never a guessed number."""
    repo = _as_str(repo, "repo")
    pr = _pr_number(pr)
    sha = _sha(sha)
    branch = _ref_name(branch, "branch")
    base = _ref_name(base, "base")
    runner = _runner(runner)
    code, out, err = _call(runner, ["gh", "pr", "list", "-R", repo, "--head", branch, "--state", "open",
                                    "--json", "number,headRefName,headRefOid"])
    if code != 0:
        raise MergeNoData("cannot list the pull requests from %s (gh exited %d: %s)"
                          % (branch, code, (err or out).strip()[:200]))
    for row in _pr_rows(out, "gh pr list"):
        number, head_ref, head = row.get("number"), row.get("headRefName"), row.get("headRefOid")
        if isinstance(number, bool) or not isinstance(number, int) or head_ref != branch:
            continue
        if head == sha:
            return str(number)
        raise PinRefused("#%s: pull request #%d from %s is open at %s, not the pinned %s"
                         % (pr, number, branch, str(head)[:12], sha[:12]))
    title = "Frozen head of #%s at %s" % (pr, sha[:12])
    body = ("Pinned sha %s of pull request #%s, frozen on branch %s so the head cannot move under the pin (MG1.d). "
            "It is merged with gh pr merge --merge --match-head-commit %s; the moving branch's later commits "
            "belong to the next batch." % (sha, pr, branch, sha))
    code, out, err = _call(runner, ["gh", "pr", "create", "-R", repo, "--base", base, "--head", branch,
                                    "--title", title, "--body", body])
    if code != 0:
        raise MergeNoData("#%s: gh pr create from %s exited %d: %s" % (pr, branch, code, (err or out).strip()[:200]))
    number = out.strip().rstrip("/").rsplit("/", 1)[-1]
    if not number.isdigit():
        raise MergeNoData("#%s: gh pr create printed no pull request number (%r); the number is never guessed"
                          % (pr, out.strip()[:80]))
    return number


# ---------------------------------------------------------------------------------------------------------------------
# the checks: before any prompt, refusing only what cannot be frozen or merged as pinned
# ---------------------------------------------------------------------------------------------------------------------
def _entry(entry, index):
    if not isinstance(entry, dict):
        raise MergeGateError("entry %d must be an object with pr, sha and head_ref" % index)
    out = {"pr": _pr_number(entry.get("pr"), "entry %d pr" % index),
           "sha": _sha(entry.get("sha"), "entry %d sha" % index),
           "head_ref": _ref_name(entry.get("head_ref"), "entry %d head_ref" % index)}
    for key in ("pin", "frozen"):
        value = entry.get(key)
        if value in (None, ""):
            out[key] = ""
        elif key == "pin":
            out[key] = _ref_name(value, "entry %d pin" % index)
        else:
            out[key] = _pr_number(value, "entry %d frozen" % index)
    return out


STATIC, WILL_FREEZE, PINNED, FROZEN = "STATIC", "WILL-FREEZE", "PINNED", "FROZEN"


def check_pins(clone, entries, remote=REMOTE, runner=None, patterns=None):
    """One line per entry, starting with ONE exact token the shell classifies on: `STATIC #N (<ref>)`,
    `WILL-FREEZE #N (<ref>)` (PIN-PENDING, not a refusal), `PINNED #N <branch>, pull request pending`, or
    `FROZEN #N <branch> #M (<state>)`. Refuses (PinRefused) a pin= that is not this entry's own pin_branch, a pin
    branch that does not resolve on the remote to the pinned sha, a frozen pull request whose head is not that branch
    at that sha, and a pinned sha that is not an ancestor of the moving ref's current head (a force push happened).
    A remote the tool cannot read is NO-DATA and blocks. `patterns` extends the moving patterns (moving_patterns)."""
    clone = _as_str(clone, "clone")
    remote = _ref_name(remote, "remote")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, (list, tuple)):
        raise MergeGateError("entries must be a list, not %s" % type(entries).__name__)
    entries = [_entry(entry, index) for index, entry in enumerate(entries)]
    runner = _runner(runner)
    lines = []
    repo = None
    for entry in entries:
        pr, sha, head_ref, pin, frozen = (entry["pr"], entry["sha"], entry["head_ref"], entry["pin"],
                                          entry["frozen"])
        if not is_moving_ref(head_ref, patterns) and not pin and not frozen:
            lines.append("%s #%s (%s)" % (STATIC, pr, head_ref))
            continue
        tip = _ls_remote(runner, clone, remote, head_ref)
        if not tip:
            raise MergeNoData("#%s: %s has no branch %s, so the pin cannot be checked against its head"
                              % (pr, remote, head_ref))
        if tip != sha and not _is_ancestor(runner, clone, remote, sha, tip):
            raise PinRefused("#%s: the pinned %s is not an ancestor of %s's current head %s: a force push happened"
                             % (pr, sha[:12], head_ref, tip[:12]))
        if not pin:
            if frozen:
                raise PinRefused("#%s: frozen=%s without a pin= branch; the list line is malformed" % (pr, frozen))
            lines.append("%s #%s (%s)" % (WILL_FREEZE, pr, head_ref))
            continue
        if pin != pin_branch(pr, sha):
            raise PinRefused("#%s: pin %s is not this entry's own frozen branch %s; no other ref is a pin"
                             % (pr, pin, pin_branch(pr, sha)))
        have = _ls_remote(runner, clone, remote, pin)
        if have != sha:
            raise PinRefused("#%s: pin %s resolves to %s on %s, not the pinned %s"
                             % (pr, pin, have[:12] or "nothing", remote, sha[:12]))
        if not frozen:
            lines.append("%s #%s %s, pull request pending" % (PINNED, pr, pin))
            continue
        if repo is None:
            repo = _repo_of(runner, clone, remote)
        code, out, err = _call(runner, ["gh", "pr", "view", frozen, "-R", repo, "--json",
                                        "state,headRefName,headRefOid"], clone)
        if code != 0:
            raise MergeNoData("#%s: frozen pull request #%s cannot be read (gh exited %d: %s)"
                              % (pr, frozen, code, (err or out).strip()[:200]))
        try:
            info = json.loads(out)
        except ValueError:
            raise MergeNoData("#%s: gh printed no JSON for #%s" % (pr, frozen))
        if not isinstance(info, dict):
            raise MergeNoData("#%s: gh printed no object for #%s" % (pr, frozen))
        if info.get("headRefName") != pin or info.get("headRefOid") != sha:
            raise PinRefused("#%s: frozen pull request #%s has head %s at %s, not %s at %s"
                             % (pr, frozen, str(info.get("headRefName"))[:60], str(info.get("headRefOid"))[:12],
                                pin, sha[:12]))
        lines.append("%s #%s %s #%s (%s)" % (FROZEN, pr, pin, frozen, str(info.get("state"))[:20]))
    return lines


def note_pin(list_path, pr, pin=None, frozen=None):
    """Rewrite the one list line whose first token is `pr` with the given pin= and frozen= tokens (earlier ones
    replaced, every other token kept), through a sibling temp file and os.replace, so a rerun finds them. No such
    line, or more than one, refuses."""
    list_path = _as_str(list_path, "list_path")
    pr = _pr_number(pr)
    pin = "" if pin in (None, "") else _ref_name(pin, "pin")
    frozen = "" if frozen in (None, "") else _pr_number(frozen, "frozen")
    try:
        with open(list_path, "rb") as handle:
            text = handle.read().decode("utf-8")
    except OSError as exc:
        raise MergeNoData("the list at %s cannot be read (%s)" % (list_path, exc))
    except UnicodeDecodeError:
        raise MergeNoData("the list at %s is not utf-8" % list_path)
    lines = text.splitlines(True)
    hits = [index for index, line in enumerate(lines)
            if line.split() and not line.lstrip().startswith("#") and line.split()[0] == pr]
    if len(hits) != 1:
        raise MergeNoData("the list at %s names #%s on %d line(s), not one" % (list_path, pr, len(hits)))
    tokens = [token for token in lines[hits[0]].split() if not token.startswith(("pin=", "frozen="))]
    if pin:
        tokens.append("pin=" + pin)
    if frozen:
        tokens.append("frozen=" + frozen)
    lines[hits[0]] = " ".join(tokens) + "\n"
    directory = os.path.dirname(os.path.abspath(list_path))
    try:
        fd, tmp = tempfile.mkstemp(prefix=".merge-list-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("".join(lines))
        os.replace(tmp, list_path)
    except OSError as exc:
        raise MergeNoData("the list at %s cannot be rewritten (%s)" % (list_path, exc))
    return lines[hits[0]].rstrip("\n")


# ---------------------------------------------------------------------------------------------------------------------
# the protection read and the one prompt
# ---------------------------------------------------------------------------------------------------------------------
def main_protected(repo, runner=None, branch=BRANCH):
    """True only when the branch's protection requires a pull request (`required_pull_request_reviews` present) AND
    enforces it on administrators (`enforce_admins.enabled` true: the owner and every session share one admin
    account, so a protection an admin may bypass protects nothing and reads False, fail closed). False when either is
    missing or GitHub says the branch is not protected at all; None when the answer cannot be read (gh failed, a 403
    such as a plan that does not expose protection, or output that is not the protection object). Stated limit:
    this reads classic branch protection through `gh api`; a repository ruleset is not read here."""
    repo = _as_str(repo, "repo")
    branch = _ref_name(branch, "branch")
    runner = _runner(runner)
    try:
        code, out, _err = _call(runner, ["gh", "api", "repos/%s/branches/%s/protection" % (repo, branch)])
    except MergeGateError:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if code != 0:
        if isinstance(data, dict) and data.get("message") == "Branch not protected":
            return False
        return None
    if not isinstance(data, dict) or "message" in data:
        return None
    admins = data.get("enforce_admins")
    return isinstance(data.get("required_pull_request_reviews"), dict) and isinstance(admins, dict) \
        and admins.get("enabled") is True


def owner_confirmed(expected, stdin=None, stdout=None):
    """True only when `stdin` is a terminal and the line typed on it is exactly `expected` (the count of pull
    requests this run will merge). It takes no environment, file or flag; a stdin that is not a terminal, a closed
    one, an unreadable one, or an end of file is False. An accident stop, never authentication (D7)."""
    expected = _as_str(expected, "expected").strip()
    if not expected.isdigit():
        raise MergeGateError("expected is the count of pull requests, digits, not %r" % expected[:40])
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        if not stdin.isatty():
            return False
    except (AttributeError, ValueError, OSError):
        return False
    try:
        stdout.write("This run will merge how many pull requests? Type the count to continue: ")
        stdout.flush()
        answer = stdin.readline()
    except (AttributeError, ValueError, OSError):
        return False
    if not isinstance(answer, str) or not answer.endswith("\n"):
        return False
    return answer.strip() == expected


# ---------------------------------------------------------------------------------------------------------------------
# the command line
# ---------------------------------------------------------------------------------------------------------------------
def _refuse_env():
    try:
        reason = merge_gate.refuse_steered_env(dict(os.environ), False)
    except MergeGateError as exc:
        reason = "the environment could not be read: %s" % exc
    if reason:
        sys.stderr.write("NO-DATA: %s\n" % reason)
        return 2
    return 0


def _pin_fields(tokens):
    pin = frozen = ""
    for token in tokens:
        if token.startswith("pin="):
            pin = token[4:]
        elif token.startswith("frozen="):
            frozen = token[7:]
        else:
            raise MergeGateError("unknown field %r: only pin=<branch> and frozen=<pr> are read" % token[:40])
    return pin, frozen


def main(argv=None, runner=None):
    # THE PUBLIC ARGV CONTRACT, the same as merge_gate.main: None or a list or tuple of str, else a ValueError
    if argv is not None and not (isinstance(argv, (list, tuple)) and all(isinstance(a, str) for a in argv)):
        raise ValueError("main: argv must be None or a list or tuple of str, got %s" % type(argv).__name__)
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write(USAGE + "\n")
        return 2
    command, rest = args[0], args[1:]
    if command not in ("moving", "check", "freeze", "open", "note", "protected", "confirm"):
        sys.stderr.write(USAGE + "\n")
        return 2
    if command in ("freeze", "open", "note", "confirm"):
        code = _refuse_env()
        if code:
            return code
    try:
        if command in ("check", "open", "protected") and rest[:1] == ["--gh"]:
            if len(rest) < 2:
                sys.stderr.write(USAGE + "\n")
                return 2
            if runner is None:
                runner = gh_runner(rest[1])
            rest = rest[2:]
        if command == "moving":
            if len(rest) != 1:
                sys.stderr.write(USAGE + "\n")
                return 2
            moving = is_moving_ref(rest[0], moving_patterns())
            sys.stdout.write("%s %s\n" % ("moving" if moving else "static", rest[0]))
            return 0 if moving else 1
        if command == "check":
            if len(rest) < 5 or len(rest) > 7:
                sys.stderr.write(USAGE + "\n")
                return 2
            pin, frozen = _pin_fields(rest[5:])
            lines = check_pins(rest[0], [{"pr": rest[2], "sha": rest[3], "head_ref": rest[4], "pin": pin,
                                          "frozen": frozen}], remote=rest[1], runner=runner,
                               patterns=moving_patterns())
            sys.stdout.write("\n".join(lines) + "\n")
            return 0
        if command == "freeze":
            if len(rest) != 4:
                sys.stderr.write(USAGE + "\n")
                return 2
            sys.stdout.write(freeze_pin(rest[0], rest[2], rest[3], remote=rest[1], runner=runner) + "\n")
            return 0
        if command == "open":
            if len(rest) != 4:
                sys.stderr.write(USAGE + "\n")
                return 2
            sys.stdout.write(open_frozen_pr(rest[0], rest[1], rest[2], rest[3], runner=runner) + "\n")
            return 0
        if command == "note":
            if len(rest) < 3 or len(rest) > 4:
                sys.stderr.write(USAGE + "\n")
                return 2
            pin, frozen = _pin_fields(rest[2:])
            sys.stdout.write(note_pin(rest[0], rest[1], pin, frozen) + "\n")
            return 0
        if command == "protected":
            if len(rest) not in (1, 2):
                sys.stderr.write(USAGE + "\n")
                return 2
            branch = rest[1] if len(rest) == 2 else BRANCH
            answer = main_protected(rest[0], runner=runner, branch=branch)
            if answer is True:
                sys.stdout.write("PROTECTED: %s requires a pull request\n" % branch)
                return 0
            if answer is False:
                sys.stdout.write("NOT-PROTECTED: %s does not require a pull request\n" % branch)
                return 1
            sys.stdout.write("NO-DATA: %s's branch protection cannot be read\n" % branch)
            return 2
        if len(rest) != 1:
            sys.stderr.write(USAGE + "\n")
            return 2
        if owner_confirmed(rest[0]):
            return 0
        sys.stdout.write("\nSTOP: no terminal typed the count %s, so nothing is written\n" % rest[0])
        return 1
    except PinRefused as exc:
        # check's answer, refusal included, is ONE stdout stream: the shell captures stdout alone, so a stderr line
        # from a tool underneath (git's own cache warning in a sandbox) can never become the classified answer
        (sys.stdout if command == "check" else sys.stderr).write("STOP: %s\n" % exc)
        return 1
    except MergeGateError as exc:
        (sys.stdout if command == "check" else sys.stderr).write("NO-DATA: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
