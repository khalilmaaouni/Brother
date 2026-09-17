#!/usr/bin/env python3
"""The release cut as ONE command: readiness, precedence, the chain, one
approval, the push, and the fence released on every exit.

WHY THIS EXISTS (founder complaint, 2026-09-17, target 1.0.19). The cut
was "dramatically complicated": next_cut.py, required_fast.sh,
cut_v1.0.0.sh, refresh_cut.py, release_invariant.py, reproduce_export.py
and export_public.py each had to be run by hand, in the right order, and
nothing checked whether another live session was mid-write on the same
release paths or gave the cut precedence over that work. This script calls
that existing chain in the order docs/plan/RELEASE-POLICY.md documents
under "The release chain" and adds only what was missing: the conflict
scan, the cut's own fence, the single approve prompt, and the guarantee
that the fence is released whichever way the process leaves.

THE TWO INVOCATIONS, from the hub root (nobody types either: in Claude Code
the founder says "cut 1.0.19" and the /brother door runs them, per
docs/maintainer/BROTHER-MAINTAINER-VERBS.md "cut"; --answer-file PATH lets
that session relay his answer from its question UI while the fence is held):

    python3 scripts/cut.py --check [--version 1.0.19]
        readiness only. Scans live fences for overlap with the release
        paths and reads the tree's git status here, then REHEARSES the
        whole chain (cut_v1.0.0.sh, required_fast.sh, refresh_cut.py
        --check, release_invariant.py) in a throwaway detached worktree it
        removes afterwards, and prints one report. Claims nothing, writes
        nothing to this checkout, never prompts.

    python3 scripts/cut.py [--version 1.0.19] [--yes] [--force-conflicts R]
        the cut. Same scan, then it claims a release-cut fence over the
        release paths (so the fence hook refuses other sessions' edits
        there for the duration), runs cut_v1.0.0.sh (local commits only),
        required_fast.sh on that bumped tree, refresh_cut.py --check and
        release_invariant.py, prints one
        summary, asks "Approve cut and push tag v<version>? [y/N]", and
        only on yes runs export_public.py --push --tag --prove-required-
        fast. reproduce_export.py runs AFTER the push: it compares the
        tag's tree against a rebuild, and refuses NO-DATA until the tag
        exists on this checkout (measured 2026-09-17: exit 2 on a tag not
        yet cut), so it cannot run earlier without lying.

WHAT IS NOT REPEATED. cut_v1.0.0.sh already runs refresh_cut.py --version
(its step 2b) and commits the note; running the WRITE form of refresh_cut
again afterwards would restamp the note with the note commit itself and
dirty the tree, which is the self-naming loop reproduce_export.py's own
docstring describes. So after the cut script this runs refresh_cut.py
--check only: a read that says whether the manifest in the tree describes
the tree.

THE FENCE. The cut's own claim is a bm_store.py record named
release-cut-<version>, lifetime ephemeral, fencing scripts/, bundle/,
docs/releases/, .claude-plugin/ and every product's CHECKSUMS.sha256 and
.claude-plugin/. Before claiming, every ACTIVE record's declared paths (the
read-only `bm_store.py dump`, joined records to claims by lifecycle_uuid)
are compared against that set with bm_store.paths_overlap, the same
comparison the fence hook itself runs; any overlap refuses unless
--force-conflicts REASON is passed. Forced, the cut takes precedence for
real: each overlapping record is PARKED first (`bm_store.py park <uuid>
--version <n> --note "<reason>, superseded by release-cut-<version>"`,
uuid and version straight from the same dump), the estate's own
non-destructive handoff, so that session's next write is refused by the
fence hook until it re-claims and sees the cut. Their files are never
touched. One park failing stops the cut before its own claim, nothing
half-parked proceeds. Without the flag nothing is parked, ever. The reason
is also folded into the fence's evidence. --check never parks (it writes
nothing); it names what a real forced cut would park. Once claimed, EVERY
exit path releases the cut's fence: complete with
evidence on a genuine pushed tag, park with a note on anything else
(refusal, failure, decline, crash, Ctrl-C). --session is deliberately not
passed: bm_store.py resolves the same hook-derived label the fence hook
would compute for this harness session, else a fresh per-process id, and
park/complete never check it.

Exit codes, this estate's three: 0 pushed, or declined at the prompt, or
--check ready; 1 refused or failed; 2 NO-DATA (no version could be read,
or bm_store.py could not be loaded for a real cut). NO-DATA is never a
pass. Python 3, standard library only.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NODATA = 2

#: How long --answer-file waits for the founder's answer before declining.
#: The fence is held while waiting, so the wait is bounded, never forever.
ANSWER_TIMEOUT_S = 1800
ANSWER_POLL_S = 2


def file_asker(path, timeout_s=None, poll_s=None, clock=None, sleep=None):
    """The approve prompt answered by a file instead of a keyboard, so a
    Claude Code session can put the question to the founder in its question
    UI and write his answer here while this process still holds the fence.
    No answer within the timeout reads as a decline."""
    timeout_s = ANSWER_TIMEOUT_S if timeout_s is None else timeout_s
    poll_s = ANSWER_POLL_S if poll_s is None else poll_s
    clock = clock or time.monotonic
    sleep = sleep or time.sleep

    def ask(prompt):
        print(prompt.rstrip())
        print("WAITING FOR ANSWER: write y or n into %s (declines after %ds)"
              % (path, timeout_s), flush=True)
        deadline = clock() + timeout_s
        while clock() < deadline:
            try:
                with open(path) as f:
                    text = f.read().strip()
            except OSError:
                text = ""
            if text:
                print("answer read from %s: %s" % (path, text.splitlines()[0]))
                return text.splitlines()[0]
            sleep(poll_s)
        print("no answer within %ds, declining" % timeout_s)
        return ""
    return ask


#: The shape scripts/next_cut.py prints (read 2026-09-17, its main()).
NEXT_VERSION_RE = re.compile(r"^next cut version: (\S+)$", re.MULTILINE)
#: The shape bm_store.py's claim prints, the same regex scripts/
#: cursor_smoke.py already keys on: "claimed '<name>' as lifecycle <32 hex>
#: (version N, session <label>)".
LIFECYCLE_RE = re.compile(r"as lifecycle ([0-9a-f]{32})")
CLAIM_VERSION_RE = re.compile(r"\(version (\d+),")


def new_run_session():
    """ONE identity for this whole cut run, passed as --session to every
    bm_store mutation (claim, adopt, park, complete). bm_store's own
    contract (_default_cli_session_id): an omitted --session gets a fresh
    per-process id, and a multi-step CLI workflow must pass the SAME
    --session across its invocations, because park and complete check
    ownership. Measured 2026-09-17 on 1.0.19: the cut claimed under one
    generated id and released under another, and the release was refused
    ("not-owner"), stranding the fence. Minted fresh per run in bm_store's
    own `cli-<uuid4 hex>` shape; never copied from an existing record."""
    return "cli-" + uuid.uuid4().hex
#: The condensed lines required_fast.sh prints at its end.
FAST_SUMMARY_RE = re.compile(r"^(pass \d+ +fail \d+ +no-data \d+|FAILED:.*|"
                             r"NO-DATA:.*)$", re.MULTILINE)


def _run(cmd, root, runner=None, stdin_text=None):
    """One subprocess, its own exit code read before anything else runs.
    Never raises on a missing binary: a CompletedProcess-shaped failure
    (code 127, the error in stderr) so every caller reads one shape."""
    runner = runner or subprocess.run
    try:
        return runner(cmd, cwd=root, capture_output=True, text=True,
                      input=stdin_text)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _clock():
    return time.strftime("%H:%M:%S")


def _stream(label, cmd, root, runner=None, emit=None, clock=None):
    """Run one long chain step and show it LIVE. Measured 2026-09-17: the
    1.0.19 rehearsal printed nothing for more than 50 minutes because every
    step was captured whole and printed only when the chain ended, so the
    founder saw an empty log and read the cut as stuck. Now each step prints
    a start line (clock), every output line as it arrives (flushed, ANSI
    stripped, prefixed with the step label), and an end line with the step's
    own exit code and elapsed seconds. The full text is still returned in a
    CompletedProcess so every caller parses what it parsed before. A test
    `runner` replaces the process but the start and end lines still print."""
    emit = emit or (lambda line: print(line, flush=True))
    clock = clock or time.monotonic
    start = clock()
    emit("[cut %s] start %s" % (_clock(), label))
    if runner is not None:
        proc = _run(cmd, root, runner)
        for line in _text(proc).splitlines():
            emit("  %s | %s" % (label, ANSI_RE.sub("", line)))
    else:
        lines = []
        # A Python grandchild writing to a pipe block-buffers, which would
        # hide its progress inside the step; unbuffered keeps it live.
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        try:
            with subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  bufsize=1, errors="replace",
                                  env=env) as child:
                for raw in child.stdout:
                    line = ANSI_RE.sub("", raw.rstrip("\n"))
                    lines.append(line)
                    emit("  %s | %s" % (label, line))
                code = child.wait()
            proc = subprocess.CompletedProcess(cmd, code, "\n".join(lines),
                                               "")
        except OSError as exc:
            proc = subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)
    emit("[cut %s] end %s: exit %d after %ds"
         % (_clock(), label, proc.returncode, int(clock() - start)))
    return proc


def _text(proc):
    return ((proc.stdout or "") + (proc.stderr or "")).rstrip()


def release_paths(root):
    """The paths a cut writes or ships, relative to root, as the fence
    stores them. Products are read from the tree, never listed by hand, so
    a product added later is fenced without editing this file."""
    paths = ["scripts/", "bundle/", "docs/releases/", ".claude-plugin/"]
    products = os.path.join(root, "products")
    if os.path.isdir(products):
        for name in sorted(os.listdir(products)):
            for rel in ("CHECKSUMS.sha256", ".claude-plugin/"):
                candidate = os.path.join(products, name, rel)
                if os.path.exists(candidate):
                    paths.append("products/%s/%s" % (name, rel))
    return paths


def read_next_version(root, runner=None):
    """(version, lines): the version next_cut.py prints, or (None, why)."""
    proc = _run([sys.executable, os.path.join(root, "scripts", "next_cut.py")],
                root, runner)
    if proc.returncode != 0:
        return None, ["NO-DATA: scripts/next_cut.py exited %d, pass "
                      "--version explicitly: %s" % (proc.returncode,
                                                    _text(proc))]
    m = NEXT_VERSION_RE.search(proc.stdout or "")
    if not m:
        return None, ["NO-DATA: scripts/next_cut.py printed no 'next cut "
                      "version:' line, pass --version explicitly: %s"
                      % _text(proc)]
    return m.group(1), ["version %s (from scripts/next_cut.py)" % m.group(1)]


#: bm_store.py loaded by path once and cached as (module, path, why_not),
#: the technique scripts/integrate.py's _load_bm_store already uses: tools/
#: is not a package and a plain `import bm_store` could resolve against a
#: different checkout on sys.path.
_BM_STORE_CACHE = []


def _load_bm_store(root):
    if _BM_STORE_CACHE:
        return _BM_STORE_CACHE[0]
    for candidate in (
        # hub dev layout: scripts/cut.py beside products/brothermode/tools/
        os.path.join(root, "products", "brothermode", "tools", "bm_store.py"),
        # installed bundle layout: bundle/runtime/cut.py beside
        # bundle/runtime/hooks/brothermode/tools/bm_store.py
        os.path.join(HERE, "hooks", "brothermode", "tools", "bm_store.py"),
    ):
        if not os.path.isfile(candidate):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "bm_store_for_cut", candidate)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _BM_STORE_CACHE.append((mod, candidate, None))
        except Exception as exc:  # noqa: BLE001
            _BM_STORE_CACHE.append((None, candidate,
                                    "%s: %s" % (type(exc).__name__, exc)))
        return _BM_STORE_CACHE[0]
    _BM_STORE_CACHE.append((None, None, "no bm_store.py found under %s or %s"
                            % (root, HERE)))
    return _BM_STORE_CACHE[0]


def _bm_cwd(root, runner=None):
    """The checkout whose .brothermode/store.sqlite3 the fence hook reads.
    bm_store resolves its store by walking up from its cwd, so a cut run
    from a linked worktree (as docs/maintainer/BROTHER-MAINTAINER-VERBS.md
    "cut" asks) found no store and read NO-DATA (measured 2026-09-17 from
    ~/Brother-wt/cut-1019). The main checkout is the parent of the common
    git dir; anything unreadable falls back to root."""
    proc = _run(["git", "rev-parse", "--path-format=absolute",
                 "--git-common-dir"], root, runner)
    common = (proc.stdout or "").strip()
    if proc.returncode == 0 and common.endswith("/.git"):
        return os.path.dirname(common)
    return root


def live_conflicts(root, bm_mod, bm_path, paths, runner=None,
                   exclude_uuid=None):
    """(conflicts, why): every ACTIVE fence record whose declared paths
    overlap `paths`, each as a dict (name, lifecycle_uuid, session_id,
    objective, paths). (None, why) when the dump could not be read; an
    unreadable store is not a measured empty one. `bm_store.py dump` is the
    read-only diagnostic (dashboard writes STATE.md); its default export
    WITHHOLDS objective and session_id, so those print as the store
    exports them, and the record name plus lifecycle uuid identify the
    lane."""
    proc = _run([sys.executable, bm_path, "dump"], _bm_cwd(root, runner),
                runner)
    if proc.returncode != 0:
        return None, ("NO-DATA: bm_store.py dump exited %d: %s"
                      % (proc.returncode, _text(proc)))
    try:
        data = json.loads(proc.stdout or "")
        records = data["records"]
        claims = data["claims"]
    except (ValueError, KeyError, TypeError) as exc:
        return None, ("NO-DATA: bm_store.py dump printed no records/claims "
                      "JSON (%s)" % exc)
    by_uuid = {}
    for rec in records:
        # The cut's own fence overlaps its own paths by construction; after
        # its claim, counting it read as "claimed meanwhile" and refused
        # every cut (measured 2026-09-17 on 1.0.19).
        if rec.get("lifecycle_uuid") == exclude_uuid:
            continue
        if rec.get("state") == "active":
            by_uuid[rec.get("lifecycle_uuid")] = dict(rec, paths=[])
    for claim in claims:
        rec = by_uuid.get(claim.get("lifecycle_uuid"))
        if rec is not None and claim.get("path"):
            rec["paths"].append(claim["path"])
    conflicts = []
    for rec in by_uuid.values():
        hits = sorted(p for p in rec["paths"]
                      if any(bm_mod.paths_overlap(p, r) for r in paths))
        if hits:
            conflicts.append({
                "name": rec.get("name"),
                "lifecycle_uuid": rec.get("lifecycle_uuid"),
                "version": rec.get("version"),
                "session_id": rec.get("session_id"),
                "objective": rec.get("objective"),
                "paths": hits,
            })
    return sorted(conflicts, key=lambda c: c["name"] or ""), ""


def conflict_lines(conflicts):
    out = []
    for c in conflicts:
        out.append("CONFLICT: '%s' (lifecycle %s, session %s) is live on %s"
                   % (c["name"], c["lifecycle_uuid"], c["session_id"],
                      ", ".join(c["paths"])))
        out.append("  objective: %s" % c["objective"])
    return out


#: bm_stall.py finding kinds that mean "the owner is DEAD". Its sweep emits
#: nothing for a LIVE or UNKNOWN owner, so absence is never read as dead.
DEAD_OWNER_KINDS = ("stale-fence", "dead-watchdog")
ADOPTED_VERSION_RE = re.compile(r"is now adopted at version (\d+)")


def dead_owner_uuids(root, bm_path, runner=None, now=None):
    """(uuids, why): lifecycle uuids whose owner the estate's own liveness
    oracle (tools/bm_stall.py sweep, read-only) judges DEAD, or (None, why)
    when the sweep could not be read. bm_store cannot tell a dead owner from
    a live one; bm_stall can, from heartbeat age, controller registration
    and pid, so precedence is only ever taken over what it names."""
    stall = os.path.join(os.path.dirname(bm_path), "bm_stall.py")
    cmd = [sys.executable, stall, "sweep", "--json"]
    if now:
        cmd += ["--now", now]
    proc = _run(cmd, _bm_cwd(root, runner), runner)
    # bm_stall's own convention (its _run): 0 no finding, 1 findings printed,
    # 2 NO-DATA. Reading every nonzero exit as NO-DATA made precedence
    # impossible on the real store (found by the real-store test below).
    if proc.returncode not in (0, 1):
        return None, ("NO-DATA: bm_stall.py sweep exited %d: %s"
                      % (proc.returncode, _text(proc)))
    try:
        findings = json.loads(proc.stdout or "")["findings"]
    except (ValueError, KeyError, TypeError) as exc:
        return None, "NO-DATA: bm_stall.py sweep printed no findings JSON (%s)" % exc
    return {f.get("lifecycle_uuid") for f in findings
            if f.get("kind") in DEAD_OWNER_KINDS}, ""


def park_conflicts(root, bm_path, conflicts, version, reason, runner=None,
                   dead=None, session=None):
    """(ok, lines): take precedence over every conflicting record so the
    cut's own claim can land. Measured 2026-09-17 against the real store:
    a plain `park` of another session's ACTIVE record is refused
    ("not-owner ... adoption is the exception for a dead session's
    record"), so the earlier park-only path could never work; its tests ran
    on a fake store. Now, per record: `adopt --adopt-from-live-session`
    (the store's own dead-owner path, which it cannot judge itself) then
    `park` at the version the adopt printed. Files are never touched.

    ALL-OR-NOTHING PREFLIGHT, before any write: every conflict must carry a
    version and be in `dead` (bm_stall's DEAD verdicts). One live or
    unknown owner refuses the whole pass with nothing adopted or parked.
    A write failing midway (a version race, a refusal) stops at once and
    says which record is left in which state; the caller claims nothing."""
    dead = set(dead or ())
    for c in conflicts:
        if c.get("version") is None:
            return False, [
                "PRECEDENCE REFUSED: '%s' (lifecycle %s) carries no version "
                "in the dump; nothing adopted or parked"
                % (c["name"], c["lifecycle_uuid"])]
        if c["lifecycle_uuid"] not in dead:
            return False, [
                "PRECEDENCE REFUSED: '%s' (lifecycle %s) is not judged DEAD by "
                "bm_stall.py (live or unknown owner); the cut never takes "
                "over a live session's lock. Nothing adopted or parked."
                % (c["name"], c["lifecycle_uuid"])]
    lines = []
    for c in conflicts:
        note = "%s, superseded by release-cut-%s" % (reason, version)
        cmd = [sys.executable, bm_path, "adopt", c["lifecycle_uuid"],
               "--version", str(c["version"]), "--adopt-from-live-session",
               "--note", note] + (["--session", session] if session else [])
        proc = _run(cmd, _bm_cwd(root, runner), runner)
        text = _text(proc)
        m = ADOPTED_VERSION_RE.search(proc.stdout or "")
        if proc.returncode != 0 or not m:
            return False, lines + [
                "ADOPT FAILED: '%s' (lifecycle %s) exited %d, still as it "
                "was; nothing further adopted or parked, the cut claims "
                "nothing: %s" % (c["name"], c["lifecycle_uuid"],
                                 proc.returncode, text)]
        lines.append("adopted '%s' (lifecycle %s): %s"
                     % (c["name"], c["lifecycle_uuid"], text.strip()))
        cmd = [sys.executable, bm_path, "park", c["lifecycle_uuid"],
               "--version", m.group(1), "--note", note] + \
            (["--session", session] if session else [])
        proc = _run(cmd, _bm_cwd(root, runner), runner)
        text = _text(proc)
        if proc.returncode != 0:
            return False, lines + [
                "PARK FAILED: '%s' (lifecycle %s) exited %d and is left "
                "ADOPTED (no live writer, any session may park or resume "
                "it); nothing further adopted or parked, the cut claims "
                "nothing: %s" % (c["name"], c["lifecycle_uuid"],
                                 proc.returncode, text)]
        lines.append("parked '%s' (lifecycle %s): %s"
                     % (c["name"], c["lifecycle_uuid"],
                        text.strip() or "(bm_store printed nothing)"))
    return True, lines


def dirty_paths(root, runner=None):
    """(paths, why): every path `git status --porcelain` names, or
    (None, why) when the status itself could not be read."""
    proc = _run(["git", "status", "--porcelain"], root, runner)
    if proc.returncode != 0:
        return None, ("NO-DATA: git status --porcelain exited %d: %s"
                      % (proc.returncode, _text(proc)))
    return [l for l in (proc.stdout or "").splitlines() if l.strip()], ""


def tree_lines(root, runner=None):
    """Read-only: the branch and commit this cut would land on, printed so
    a cut on the wrong checkout is visible before anything is written."""
    branch = _run(["git", "branch", "--show-current"], root, runner)
    head = _run(["git", "rev-parse", "--short", "HEAD"], root, runner)
    return ["branch %s at %s" % ((branch.stdout or "?").strip(),
                                 (head.stdout or "?").strip())]


def required_fast(root, version, runner=None):
    """(ok, lines). The whole gate output is kept in a file keyed the way
    required_fast.sh keys its own captures (worktree plus pid); the lines
    returned are its condensed summary plus any FAILED:/NO-DATA: line."""
    proc = _stream("required_fast.sh",
                   ["sh", os.path.join(root, "scripts", "required_fast.sh")],
                   root, runner)
    keep = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                        "cut-required-fast-%s-%s-%d.txt"
                        % (version, os.path.basename(root), os.getpid()))
    try:
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write(_text(proc) + "\n")
        where = keep
    except OSError as exc:
        where = "(could not save: %s)" % exc
    summary = FAST_SUMMARY_RE.findall(proc.stdout or "")
    lines = ["required_fast.sh exit %d  [full: %s]" % (proc.returncode, where)]
    lines.extend(summary or ["(no summary line printed: NO-DATA, never a "
                             "pass)"])
    return proc.returncode == 0, lines


def step(label, cmd, root, runner=None):
    """(ok, lines) for one chain command; on failure the tool's own output
    is the report, on success only its last line. The output itself was
    already shown live by _stream."""
    proc = _stream(label, cmd, root, runner)
    text = _text(proc)
    if proc.returncode != 0:
        return False, ["%s: exit %d" % (label, proc.returncode)] + \
            text.splitlines()
    tail = text.splitlines()[-1] if text else "(no output)"
    return True, ["%s: exit 0  %s" % (label, tail)]


def claim_fence(root, bm_path, version, paths, runner=None, session=None):
    """(uuid, store_version, lines): the cut's own fence, claimed under the
    run's own `session`, or (None, None, lines) with bm_store's own output
    when the claim was refused."""
    cmd = [sys.executable, bm_path, "claim", "release-cut-%s" % version,
           "--lifetime", "ephemeral", "--objective",
           "release cut %s, orchestrated by scripts/cut.py" % version,
           ] + (["--session", session] if session else []) + \
        ["--files"] + list(paths)
    proc = _run(cmd, _bm_cwd(root, runner), runner)
    text = _text(proc)
    m = LIFECYCLE_RE.search(proc.stdout or "")
    v = CLAIM_VERSION_RE.search(proc.stdout or "")
    if proc.returncode != 0 or not m or not v:
        return None, None, ["claim refused (exit %d), nothing was claimed:"
                            % proc.returncode] + text.splitlines()
    return m.group(1), int(v.group(1)), [text.strip()]


def release_fence(root, bm_path, uuid, store_version, evidence, note,
                  runner=None, session=None):
    """complete on evidence, park otherwise. Returns the exit code of the
    bm_store command so a caller can shout when the release itself failed;
    it never raises."""
    if evidence:
        cmd = [sys.executable, bm_path, "complete", uuid,
               "--version", str(store_version), "--evidence", evidence]
    else:
        cmd = [sys.executable, bm_path, "park", uuid,
               "--version", str(store_version), "--note", note]
    if session:
        cmd += ["--session", session]
    proc = _run(cmd, _bm_cwd(root, runner), runner)
    print(_text(proc) or "(bm_store printed nothing)")
    if proc.returncode != 0:
        print("FENCE NOT RELEASED: %s exited %d; release it by hand: %s"
              % (cmd[2], proc.returncode, " ".join(cmd)))
    return proc.returncode


def _print(lines):
    for line in lines:
        print(line)


def readiness(root, version, bm, paths, force, runner=None,
              stop_early=True, include_fast=True, own_uuid=None):
    """The read-only steps shared by both modes: fences, tree, required
    fast. Returns the list of blocking reasons, empty when ready.
    Conflicts block unless forced; a dirty tree blocks; a red gate blocks.
    A real cut stops at the first block (no minutes-long gate for a cut
    already refused); --check reads every step so one report carries
    everything the founder has to clear."""
    bm_mod, bm_path, why = bm
    reasons = []
    _print(tree_lines(root, runner))
    if bm_mod is None:
        print("NO-DATA: live fence scan skipped, bm_store.py not loaded "
              "(%s)" % why)
    else:
        conflicts, why = live_conflicts(root, bm_mod, bm_path, paths, runner,
                                        exclude_uuid=own_uuid)
        if conflicts is None:
            print(why)
            reasons.append("live fence scan could not be read")
        elif conflicts:
            _print(conflict_lines(conflicts))
            if force is None:
                print("REFUSED: %d live fence(s) overlap the release paths; "
                      "wait for them to park, or pass --force-conflicts "
                      "\"reason\"" % len(conflicts))
                reasons.append("%d live fence(s) overlap" % len(conflicts))
            elif not stop_early:
                # --check writes nothing: it names what a forced cut parks.
                print("force-conflicts: a real cut would adopt then park "
                      "these %d fence(s) before claiming, and only if "
                      "bm_stall.py judges every owner DEAD, reason: %s"
                      % (len(conflicts), force))
            else:
                # A real cut parked every conflict before its claim; one
                # still live here was claimed in between, never proceed.
                print("REFUSED: %d live fence(s) still overlap after the "
                      "force-conflicts park pass, claimed meanwhile"
                      % len(conflicts))
                reasons.append("%d live fence(s) claimed after the park pass"
                               % len(conflicts))
        else:
            print("fences: no live claim overlaps the release paths")
    if reasons and stop_early:
        return reasons
    dirty, why = dirty_paths(root, runner)
    if dirty is None:
        print(why)
        reasons.append("git status could not be read")
    elif dirty:
        print("REFUSED: the tree is dirty, commit or stash first "
              "(refresh_cut.py refuses the same way):")
        _print(["  " + l for l in dirty])
        reasons.append("%d uncommitted path(s)" % len(dirty))
    else:
        print("tree: clean")
    if (reasons and stop_early) or not include_fast:
        return reasons
    ok, lines = required_fast(root, version, runner)
    _print(lines)
    if not ok:
        reasons.append("required_fast.sh did not pass")
    return reasons


def chain(root, version, runner=None, stop_early=True):
    """The release chain in the order it can be true: cut_v1.0.0.sh first
    (bump, regenerate, commit, manifest and note, all LOCAL: it pushes
    nothing), then required_fast.sh on that bumped tree, then
    refresh_cut.py --check and release_invariant.py. Before 2026-09-17 the
    gate ran BEFORE the bump, and release_invariant (inside required_fast's
    readiness-gate) refuses any runtime change still carrying the old
    version, so every release that changed the runtime was refused before
    it began (measured on 1.0.19). required_fast.sh still runs before the
    one irreversible step, now against the exact tree that ships. Returns
    (passed_labels, failed_labels)."""
    passed, failed = [], []
    ok, lines = step("cut_v1.0.0.sh",
                     ["sh", os.path.join(root, "scripts", "cut_v1.0.0.sh"),
                      version], root, runner)
    _print(lines)
    if not ok:
        return passed, ["cut_v1.0.0.sh"]
    passed.append("cut_v1.0.0.sh")
    ok, lines = required_fast(root, version, runner)
    _print(lines)
    if ok:
        passed.append("required_fast")
    else:
        failed.append("required_fast.sh did not pass")
        if stop_early:
            return passed, failed
    for label, cmd in (
        ("refresh_cut --check", [sys.executable,
                                 os.path.join(root, "scripts", "refresh_cut.py"),
                                 "--version", version, "--check"]),
        ("release_invariant", [sys.executable,
                               os.path.join(root, "scripts",
                                            "release_invariant.py")]),
    ):
        ok, lines = step(label, cmd, root, runner)
        _print(lines)
        if ok:
            passed.append(label)
        else:
            failed.append(label)
            if stop_early:
                return passed, failed
    return passed, failed


def check_mode(root, version, bm, paths, force, runner=None):
    """Readiness report with no fence and no write to this checkout: the
    fence scan and tree status here, then the WHOLE chain rehearsed in a
    throwaway detached worktree of this HEAD, removed afterwards. A check
    that ran the gates on the unbumped tree could never read READY for a
    release that changes the runtime; the rehearsal answers the question
    the cut will actually ask."""
    print("cut --check: %s" % version)
    failures = readiness(root, version, bm, paths, force, runner,
                         stop_early=False, include_fast=False)
    rehearsal = tempfile.mkdtemp(prefix="cut-rehearsal-%s-" % version)
    add = _run(["git", "worktree", "add", "--detach", rehearsal, "HEAD"],
               root, runner)
    if add.returncode != 0:
        print("NO-DATA: rehearsal worktree could not be created: %s"
              % _text(add))
        failures.append("rehearsal worktree could not be created")
    else:
        print("rehearsal: the chain runs in %s (detached, removed after)"
              % rehearsal)
        try:
            _, failed = chain(rehearsal, version, runner, stop_early=False)
            failures.extend(failed)
        finally:
            rm = _run(["git", "worktree", "remove", "--force", rehearsal],
                      root, runner)
            if rm.returncode != 0:
                print("rehearsal worktree NOT removed (exit %d): %s"
                      % (rm.returncode, _text(rm)))
    print("reproduce_export: NO-DATA before the push, it compares tag "
          "v%s against a rebuild and the tag does not exist yet" % version)
    if failures:
        print("NOT READY for %s: %s" % (version, "; ".join(failures)))
        return EXIT_REFUSED
    print("READY for %s: fences clear, tree clean, and the rehearsed cut "
          "passed cut_v1.0.0.sh, required_fast, refresh_cut --check and "
          "release_invariant" % version)
    return EXIT_OK


def run_chain(root, version, bm, paths, force, yes, ask, runner=None,
              own_uuid=None):
    """Everything between the claim and the release of the fence. Returns
    (exit_code, evidence_or_None, park_note): evidence only when the tag
    was pushed and reproduced."""
    reasons = readiness(root, version, bm, paths, force, runner,
                        include_fast=False, own_uuid=own_uuid)
    if reasons:
        return EXIT_REFUSED, None, ("refused before any write: %s"
                                    % "; ".join(reasons))
    start = _run(["git", "rev-parse", "HEAD"], root, runner)
    print("cut starts from %s; the chain's commits stay local until the "
          "push" % ((start.stdout or "?").strip()))
    passed, failed = chain(root, version, runner)
    if failed:
        return EXIT_REFUSED, None, "%s failed" % failed[0]
    print()
    print("cut %s ready: %s passed; branch as printed above" % (
        version, ", ".join(passed)))
    if not yes:
        try:
            answer = ask("Approve cut and push tag v%s? [y/N] " % version)
        except EOFError:
            # No keyboard (a Claude Code shell, a pipe): never a yes.
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("declined, nothing pushed")
            return EXIT_OK, None, "declined by operator before push"
    push_ok, lines = step(
        "export_public --push",
        [sys.executable, os.path.join(root, "scripts", "export_public.py"),
         "--push", "--tag", "v%s" % version, "--prove-required-fast"],
        root, runner)
    _print(lines)
    if not push_ok:
        return EXIT_REFUSED, None, "export_public.py --push failed"
    passed.append("export_public --push --tag v%s" % version)
    repro_ok, lines = step(
        "reproduce_export",
        [sys.executable, os.path.join(root, "scripts", "reproduce_export.py"),
         "--tag", "v%s" % version], root, runner)
    _print(lines)
    if not repro_ok:
        print("TAG v%s IS ALREADY PUBLIC but reproduce_export.py did not "
              "pass; read its output above before anything else" % version)
        return EXIT_REFUSED, None, ("pushed tag v%s but reproduce_export "
                                    "failed" % version)
    passed.append("reproduce_export")
    evidence = "cut %s pushed: %s" % (version, ", ".join(passed))
    if force is not None:
        evidence += "; forced over live fences: %s" % force
    print("DONE: %s" % evidence)
    return EXIT_OK, evidence, None


def cut_mode(root, version, bm, paths, force, yes, ask, runner=None):
    bm_mod, bm_path, why = bm
    if bm_mod is None:
        print("NO-DATA: bm_store.py could not be loaded (%s); a cut without "
              "the fence layer is refused, run --check instead" % why)
        return EXIT_NODATA
    print("cut: %s" % version)
    session = new_run_session()
    print("cut run identity: %s (every store change this run makes uses it)"
          % session)
    if force is not None:
        # The cut takes precedence: park every live overlap BEFORE the
        # cut's own claim (bm_store refuses an overlapping claim). Only
        # ever with the operator's explicit --force-conflicts REASON.
        conflicts, why = live_conflicts(root, bm_mod, bm_path, paths, runner)
        if conflicts is None:
            print(why)
            print("REFUSED: cannot park what cannot be read, nothing claimed")
            return EXIT_REFUSED
        if conflicts:
            _print(conflict_lines(conflicts))
            dead, why = dead_owner_uuids(root, bm_path, runner)
            if dead is None:
                print(why)
                print("REFUSED: owner liveness could not be read, nothing "
                      "adopted, parked or claimed")
                return EXIT_REFUSED
            ok, lines = park_conflicts(root, bm_path, conflicts, version,
                                       force, runner, dead=dead,
                                       session=session)
            _print(lines)
            if not ok:
                return EXIT_REFUSED
            print("force-conflicts: adopted and parked %d dead-owner "
                  "fence(s), reason: %s"
                  % (len(conflicts), force))
        else:
            print("force-conflicts: no live fence overlaps, nothing to park")
    uuid, store_version, lines = claim_fence(root, bm_path, version, paths,
                                             runner, session=session)
    _print(lines)
    if uuid is None:
        return EXIT_REFUSED
    code = EXIT_REFUSED
    evidence = None
    note = "cut.py left without recording why (crash)"
    try:
        code, evidence, note = run_chain(root, version, bm, paths, force,
                                         yes, ask, runner, own_uuid=uuid)
    except KeyboardInterrupt:
        code, evidence, note = 130, None, "interrupted mid-chain"
        print("interrupted")
    finally:
        # Every exit path releases the fence; a failing release is shouted
        # by release_fence but never changes the exit code of the cut.
        release_fence(root, bm_path, uuid, store_version, evidence,
                      note or "no reason recorded", runner, session=session)
    return code


def main(argv=None, root=None, runner=None, ask=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default=None,
                     help="the release to cut (default: the 'next cut "
                          "version' line scripts/next_cut.py prints)")
    ap.add_argument("--check", action="store_true",
                     help="readiness only: scan fences, read the tree, run "
                          "the gates; claim nothing, write nothing, never "
                          "prompt")
    ap.add_argument("--yes", action="store_true",
                     help="skip the approve prompt before the push, for "
                          "scripted use")
    ap.add_argument("--force-conflicts", metavar="REASON", default=None,
                     help="the cut takes precedence: park every live fence "
                          "overlapping the release paths (their files are "
                          "untouched, only the claim is released) before "
                          "claiming; requires a one-line reason, written "
                          "into each park note and the fence's evidence")
    ap.add_argument("--answer-file", metavar="PATH", default=None,
                     help="answer the approve prompt by writing y or n into "
                          "PATH instead of typing it (how a Claude Code "
                          "session relays the founder's answer); must not "
                          "exist yet; no answer in %d s declines"
                          % ANSWER_TIMEOUT_S)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    root = root or ROOT
    if args.answer_file is not None and not args.check:
        if args.yes:
            print("NO-DATA: --yes and --answer-file contradict each other; "
                  "pass one")
            return EXIT_NODATA
        if os.path.exists(args.answer_file):
            # A leftover answer from an earlier run would approve this push
            # before anyone saw it.
            print("NO-DATA: answer file %s already exists; a stale answer "
                  "must never approve a push, use a fresh path"
                  % args.answer_file)
            return EXIT_NODATA
        ask = ask or file_asker(args.answer_file)
    ask = ask or input
    if args.force_conflicts is not None and not args.force_conflicts.strip():
        print("NO-DATA: --force-conflicts needs a one-line reason, e.g. "
              "--force-conflicts \"lane X is parked in all but name\"")
        return EXIT_NODATA
    version = args.version
    if not version:
        version, lines = read_next_version(root, runner)
        _print(lines)
        if not version:
            return EXIT_NODATA
    paths = release_paths(root)
    bm = _load_bm_store(root)
    if args.check:
        return check_mode(root, version, bm, paths, args.force_conflicts,
                          runner)
    return cut_mode(root, version, bm, paths, args.force_conflicts,
                    args.yes, ask, runner)


if __name__ == "__main__":
    sys.exit(main())
