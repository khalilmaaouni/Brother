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
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# R4: the one place cut.py needs export_public's own DEFAULT_REMOTE, so the
# GitHub Release step's --repo is read off it rather than typed again (same
# technique scripts/reproduce_export.py already uses for the same constant).
sys.path.insert(0, HERE)
import export_public as EP  # noqa: E402
# Review item (2026-09-19): --release-notes-file goes straight to a public
# GitHub Release, so it gets the SAME scans the push gates already run
# (private terms, em/en dashes, attribution lines) -- reused from
# scripts/outgoing_scan.py, never a second, independently-typed copy of
# the matching rules (short-term case sensitivity, the length cutoff).
import outgoing_scan as OS  # noqa: E402

# J064, wave-1 Jev seam ("Gate/CI/PR log-line classification"): optional,
# fail-open, same discipline as every other jev_checks/jev_seam import in
# this estate (see jev_checks.py's own module docstring). A missing or
# broken jev_checks means the shadow call in required_fast() below is a
# no-op; this script's own (ok, lines) return value and exit code never
# depend on it.
try:
    import jev_checks
    import jev_seam
except Exception:  # noqa: BLE001
    jev_checks = None
    jev_seam = None

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NODATA = 2

#: How long --answer-file waits for the founder's answer before declining.
#: The fence is held while waiting, so the wait is bounded, never forever.
ANSWER_TIMEOUT_S = 1800
ANSWER_POLL_S = 2

#: R3 (a dead cut on the 1.0.20 release carried no pid or heartbeat on its
#: fence, so bm_stall.py judged its owner LIVE for about 4h and precedence
#: refused; the founder parked it by hand). The fence's own objective now
#: records this process's pid and host (see claim_fence, bm_stall.py's
#: parse_owner_tag/owner_process_liveness), and Heartbeat refreshes the
#: fence's updated_at at least at every step boundary and at most this
#: often during one long-running step.
HEARTBEAT_INTERVAL_S = 60


def _this_host():
    try:
        return socket.gethostname() or "unknown-host"
    except OSError:
        return "unknown-host"


def owner_tag(pid=None, host=None):
    """The exact shape bm_stall.py's OWNER_TAG_RE reads back out of a
    fence's objective. One spelling, both ends: cut.py writes it here,
    bm_stall.py's parse_owner_tag parses it, and if either ever drifts the
    other stops seeing the tag at all rather than silently misreading it,
    which is why this is a function instead of two independently-typed
    format strings."""
    return "[owner pid=%d host=%s]" % (pid if pid is not None else os.getpid(),
                                       host or _this_host())


def _release_notes_abspath(path, cwd=None):
    """Absolute, resolved against the CALLER's cwd (os.getcwd() when this
    process runs, or `cwd` for a test), never against `root` (the release
    worktree): a founder or session naming --release-notes-file means
    wherever they ran the command from."""
    cwd = cwd or os.getcwd()
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


#: scripts/outgoing_scan.py exports no constant for this (its own main(),
#: --terms-file's argparse default, hardcodes the identical literal
#: "~/.brothersbe-private-names" inline and expands it with
#: os.path.expanduser right there -- grepped, not guessed: `grep -n
#: brothersbe-private-names scripts/outgoing_scan.py`). Fixed at the
#: source is adding a real constant to that module, but it is outside
#: this fix's file scope (scripts/cut.py, scripts/test_cut.py), so this
#: mirrors outgoing_scan's own literal exactly (the bug this replaces:
#: cut.py referenced OS.DEFAULT_TERMS_FILE, which does not exist, and
#: crashed AttributeError on the very first real CLI run with no
#: --release-notes-file terms override).
DEFAULT_TERMS_FILE_LITERAL = "~/.brothersbe-private-names"


def default_terms_file():
    """os.path.expanduser(DEFAULT_TERMS_FILE_LITERAL), evaluated at CALL
    time, never frozen at import time: HOME can legitimately differ
    between when this module was first imported and when a real cut runs
    (or, in a test, between process start and one test's own HOME
    redirect), and outgoing_scan.py's own default takes the same
    call-time shape (inside its main(), not a module-level constant)."""
    return os.path.expanduser(DEFAULT_TERMS_FILE_LITERAL)


def _hash_file(path):
    """sha256 hex digest of path's bytes, or None if it cannot be read.
    Called ONLY by create_gh_release, which re-hashes right before the one
    place that publishes -- a fresh, INDEPENDENT read is the whole point
    there, so a rewrite between main()'s scan and the actual publish is
    caught. main() itself never calls this (see _read_file_bytes): a
    second read of the same path this soon after the first would just
    reopen the exact race that read was meant to close. Never raises: a
    missing or unreadable file reads as None, same non-raising contract as
    every other boundary read in this module."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _read_file_bytes(path):
    """(bytes_or_None, error_or_None): ONE read of path's raw bytes.
    Exists so scanning a file and hashing what was scanned can share the
    exact same read instead of each opening the file for itself -- two
    opens of the same path leave a window (another process, an editor, a
    docs tool) where the second one observes different bytes than the
    first, and a hash recorded from that second open would then describe
    bytes nobody scanned. See _scan_release_notes_bytes and main(), its
    one production caller."""
    try:
        with open(path, "rb") as fh:
            return fh.read(), None
    except OSError as exc:
        return None, "%s" % exc


def _scan_release_notes_bytes(raw, abspath, terms_file=None):
    """(ok, lines): the scan itself, over bytes the CALLER already read
    (via _read_file_bytes) rather than a path this function would have to
    open again. --release-notes-file goes straight to a public GitHub
    Release, so it is refused BEFORE the fence is ever claimed unless it
    passes the SAME scans the push gates run (scripts/outgoing_scan.py):
    private terms from ~/.brothersbe-private-names, em/en dashes, and
    attribution lines (a co-author trailer naming a model, the vendor's noreply address,
    the Claude Code footer). Reused wholesale -- read_terms/compile_terms/
    find_private_term_hits/ATTRIBUTION_PATTERNS are outgoing_scan's own, so
    the matching rules (a term of 5 characters or fewer is whole-word and
    CASE-SENSITIVE, a longer one is a case-INSENSITIVE substring) are never
    independently retyped here and cannot drift from the push gate's own.

    A hit is reported by LINE NUMBER, COLUMN and LENGTH only; the matched
    text itself is never printed (the same reason outgoing_scan never
    prints it: printing what a scanner exists to catch defeats it). A
    missing or unreadable terms list is a REFUSE, never a pass: a control
    with nothing to check against is not evidence of a clean file."""
    text = raw.decode("utf-8", errors="replace")
    terms, err = OS.read_terms(terms_file or default_terms_file())
    if terms is None:
        return False, ["REFUSED: the private-terms list could not be read "
                       "(%s); a missing or unreadable list is refused, "
                       "never treated as a clean file" % err]
    compiled = OS.compile_terms(terms)
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for _idx, _term, start, end in OS.find_private_term_hits(line, compiled):
            hits.append("  line %d, column %d: private term, length %d"
                        % (i, start + 1, end - start))
        if "\u2014" in line or "\u2013" in line:
            col = next(j for j, ch in enumerate(line)
                      if ch in "\u2014\u2013") + 1
            hits.append("  line %d, column %d: em or en dash" % (i, col))
        for pattern in OS.ATTRIBUTION_PATTERNS:
            m = pattern.search(line)
            if m:
                hits.append("  line %d, column %d: attribution line, "
                            "length %d" % (i, m.start() + 1,
                                          m.end() - m.start()))
    if hits:
        return False, (
            ["REFUSED: %s carries %d issue(s) the push gates would also "
             "refuse (the matched text is never printed):"
             % (abspath, len(hits))] + hits)
    return True, ["%s: clean (%d private term(s) checked, no dash, no "
                  "attribution line)" % (abspath, len(terms))]


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


def _jev_gate_line_shadow(lines):
    """J064: a shadow-only second opinion on `lines` (required_fast.sh's own
    real FAST_SUMMARY_RE matches for this run), fired AFTER required_fast()
    has already decided its (ok, lines) return value from the real exit
    code -- never before, never read back into either. Same discipline as
    reviewroute.py's J049/J050 call site: return value intentionally
    discarded, shadow-only by contract. Never raises; a missing
    jev_checks/jev_seam or an empty `lines` makes this a no-op."""
    if jev_checks is None or jev_seam is None or not lines:
        return
    try:
        jev_checks.check_gate_log_lines(
            lines, seams_config=jev_seam.load_seams_config(),
            registry=jev_seam.load_registry(),
            ledger_dir=jev_seam.DEFAULT_LEDGER_DIR)
        # C1: return value intentionally discarded, shadow-only by contract
    except Exception:  # noqa: BLE001  # sbe: allow-silent this seam is advisory only, never worth risking this script's byte-identical ok/exit code
        pass


def _run(cmd, root, runner=None, stdin_text=None, timeout=None):
    """One subprocess, its own exit code read before anything else runs.
    Never raises on a missing binary or a timeout: a CompletedProcess-
    shaped failure (127 missing binary, 124 timed out, the error in
    stderr) so every caller reads one shape. `timeout=None` (every
    caller today) behaves exactly as before: subprocess.run treats
    timeout=None as no bound; a future caller needing a bound passes it
    directly."""
    runner = runner or subprocess.run
    try:
        return runner(cmd, cwd=root, capture_output=True, text=True,
                      input=stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, "", "%s" % exc)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _clock():
    return time.strftime("%H:%M:%S")


def _stream(label, cmd, root, runner=None, emit=None, clock=None,
           heartbeat=None):
    """Run one long chain step and show it LIVE. Measured 2026-09-17: the
    1.0.19 rehearsal printed nothing for more than 50 minutes because every
    step was captured whole and printed only when the chain ended, so the
    founder saw an empty log and read the cut as stuck. Now each step prints
    a start line (clock), every output line as it arrives (flushed, ANSI
    stripped, prefixed with the step label), and an end line with the step's
    own exit code and elapsed seconds. The full text is still returned in a
    CompletedProcess so every caller parses what it parsed before. A test
    `runner` replaces the process but the start and end lines still print.

    `heartbeat` (R3, a Heartbeat instance or None): beats once, forced, at
    this step's start and end (so every step boundary refreshes the fence,
    the minimum the fix promises), and opportunistically (its own interval)
    on every live output line during a long-running step -- never during a
    test `runner`, which returns whole and has no lines to beat between."""
    emit = emit or (lambda line: print(line, flush=True))
    clock = clock or time.monotonic
    start = clock()
    emit("[cut %s] start %s" % (_clock(), label))
    if heartbeat is not None:
        heartbeat.maybe(label, force=True)
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
                    if heartbeat is not None:
                        heartbeat.maybe(label)
                code = child.wait()
            proc = subprocess.CompletedProcess(cmd, code, "\n".join(lines),
                                               "")
        except OSError as exc:
            proc = subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)
    emit("[cut %s] end %s: exit %d after %ds"
         % (_clock(), label, proc.returncode, int(clock() - start)))
    if heartbeat is not None:
        heartbeat.maybe(label, force=True)
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
    """(dead, why): {lifecycle_uuid: bm_stall's own finding message} for
    every owner the estate's own liveness oracle (tools/bm_stall.py sweep,
    read-only) judges DEAD, or (None, why) when the sweep could not be
    read. bm_store cannot tell a dead owner from a live one; bm_stall can,
    from heartbeat age, controller registration and pid, so precedence is
    only ever taken over what it names. A dict, not a bare set (review
    item, 2026-09-19): `uuid in dead` / `uuid not in dead` still work
    exactly as before (both callers here and in park_conflicts use only
    membership), but a caller that ALSO wants to say WHY -- park_conflicts'
    own refusal message -- no longer has to re-run the sweep to get the
    text bm_stall already computed."""
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
    return {f.get("lifecycle_uuid"): f.get("message") for f in findings
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
    says which record is left in which state; the caller claims nothing.

    A "not judged DEAD" refusal (review item, 2026-09-19) prints the exact
    `bm_store.py adopt ... --adopt-from-live-session` command a human would
    run once THEY have verified the owner is actually gone, so a stuck
    fence is a decision, not a hand investigation to reconstruct that
    command from scratch; bm_stall.py sweep's own absence of a dead-owner
    finding for this uuid is itself its "reason" (its findings are
    problems only, so nothing reported means its verdict is LIVE or
    UNKNOWN, never DEAD)."""
    dead = dict(dead or {})
    for c in conflicts:
        if c.get("version") is None:
            return False, [
                "PRECEDENCE REFUSED: '%s' (lifecycle %s) carries no version "
                "in the dump; nothing adopted or parked"
                % (c["name"], c["lifecycle_uuid"])]
        if c["lifecycle_uuid"] not in dead:
            adopt_cmd = " ".join(
                [sys.executable, bm_path, "adopt", c["lifecycle_uuid"],
                 "--version", str(c["version"]), "--adopt-from-live-session",
                 "--note", "<your reason>"]
                + (["--session", session] if session else []))
            return False, [
                "PRECEDENCE REFUSED: '%s' (lifecycle %s) is not judged DEAD by "
                "bm_stall.py (live or unknown owner); the cut never takes "
                "over a live session's lock. Nothing adopted or parked. "
                "bm_stall.py sweep reports no dead-owner finding for this "
                "fence: its findings are problems only, so nothing reported "
                "means its own verdict is LIVE or UNKNOWN, never DEAD. If "
                "you have independently verified the owner is gone, adopt "
                "it yourself: %s" % (c["name"], c["lifecycle_uuid"],
                                     adopt_cmd)]
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


def required_fast(root, version, runner=None, heartbeat=None):
    """(ok, lines). The whole gate output is kept in a file keyed the way
    required_fast.sh keys its own captures (worktree plus pid); the lines
    returned are its condensed summary plus any FAILED:/NO-DATA: line."""
    proc = _stream("required_fast.sh",
                   ["sh", os.path.join(root, "scripts", "required_fast.sh")],
                   root, runner, heartbeat=heartbeat)
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
    # J064: recorded only, never a vote -- see _jev_gate_line_shadow()'s own
    # docstring. The (ok, lines) return below is computed only from
    # proc.returncode and summary, whatever this call answers.
    _jev_gate_line_shadow(summary)
    lines = ["required_fast.sh exit %d  [full: %s]" % (proc.returncode, where)]
    lines.extend(summary or ["(no summary line printed: NO-DATA, never a "
                             "pass)"])
    return proc.returncode == 0, lines


def step(label, cmd, root, runner=None, heartbeat=None):
    """(ok, lines) for one chain command; on failure the tool's own output
    is the report, on success only its last line. The output itself was
    already shown live by _stream."""
    proc = _stream(label, cmd, root, runner, heartbeat=heartbeat)
    text = _text(proc)
    if proc.returncode != 0:
        return False, ["%s: exit %d" % (label, proc.returncode)] + \
            text.splitlines()
    tail = text.splitlines()[-1] if text else "(no output)"
    return True, ["%s: exit 0  %s" % (label, tail)]


def claim_fence(root, bm_path, version, paths, runner=None, session=None):
    """(uuid, store_version, lines): the cut's own fence, claimed under the
    run's own `session`, or (None, None, lines) with bm_store's own output
    when the claim was refused. R3: the objective carries this process's
    own pid and host (owner_tag), so bm_stall.py can judge this fence's
    owner from real evidence instead of waiting out its staleness window."""
    cmd = [sys.executable, bm_path, "claim", "release-cut-%s" % version,
           "--lifetime", "ephemeral", "--objective",
           "release cut %s, orchestrated by scripts/cut.py %s"
           % (version, owner_tag()),
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


class Heartbeat(object):
    """R3: refreshes the release fence's own liveness evidence while the
    chain runs, so a cut that dies mid-step stops reading LIVE the moment
    its heartbeat goes stale, instead of for up to bm_stall.py's whole
    staleness window (measured on the 1.0.20 cut: about 4h, parked by
    hand). Each beat is `bm_store.py checkpoint <uuid> --version <N>`,
    which bumps the record's own `updated_at` (cmd_checkpoint's own
    contract: the new version is EXACTLY expected_version + 1, never a
    guess, so this tracks it locally rather than re-parsing output).

    BEST-EFFORT BY CONSTRUCTION: a failed beat is printed and never raises,
    because a heartbeat hiccup must never fail the release it exists only
    to help detect the death of. A failure also leaves `version` where it
    was, so a genuine version race (nothing else should ever write this
    session's own fence, but this never assumes that) degrades to no
    further beats landing rather than a wrong version wedging every later
    one -- the fence still ages normally and bm_stall.py still judges it
    correctly once it goes stale. `maybe` still RETURNS whether the beat
    landed (True for "not due yet" too: nothing was owed), which
    run_chain's pre-push check (review item 4) reads and treats as a
    refusal signal -- the one caller that is allowed to stop being best-
    effort about it, because `bm_store.py checkpoint` refuses on a stale
    version exactly when someone else adopted this fence out from under
    a cut waiting at the approve prompt."""

    def __init__(self, root, bm_path, lifecycle_uuid, version, runner=None,
                interval_s=HEARTBEAT_INTERVAL_S, clock=None):
        self.root = root
        self.bm_path = bm_path
        self.uuid = lifecycle_uuid
        self.version = version
        self.runner = runner
        self.interval_s = interval_s
        self.clock = clock or time.monotonic
        # Due immediately: the first call (a step's own start) always beats.
        self.last = self.clock() - interval_s

    def maybe(self, label, force=False):
        """True when this call landed or nothing was owed yet; False only
        when a beat was actually attempted and the store refused or could
        not be reached."""
        if not force and (self.clock() - self.last) < self.interval_s:
            return True
        self.last = self.clock()
        cmd = [sys.executable, self.bm_path, "checkpoint", self.uuid,
               "--version", str(self.version), "--next",
               "cut heartbeat: %s" % label]
        proc = _run(cmd, _bm_cwd(self.root, self.runner), self.runner)
        if proc.returncode == 0:
            self.version += 1
            return True
        print("heartbeat NOT recorded for %r (exit %d), fence version "
              "stays %s: %s" % (label, proc.returncode, self.version,
                                _text(proc).strip() or "(no output)"))
        return False


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


def chain(root, version, runner=None, stop_early=True, resume=False,
         heartbeat=None):
    """The release chain in the order it can be true: cut_v1.0.0.sh first
    (bump, regenerate, commit, manifest and note, all LOCAL: it pushes
    nothing), then required_fast.sh on that bumped tree, then
    refresh_cut.py --check and release_invariant.py. Before 2026-09-17 the
    gate ran BEFORE the bump, and release_invariant (inside required_fast's
    readiness-gate) refuses any runtime change still carrying the old
    version, so every release that changed the runtime was refused before
    it began (measured on 1.0.19). required_fast.sh still runs before the
    one irreversible step, now against the exact tree that ships. Returns
    (passed_labels, failed_labels). `heartbeat` (R3) is None during
    --check's rehearsal (no fence is ever held there) and a real Heartbeat
    for the cut proper; every step below passes it straight through to
    _stream via step()/required_fast()."""
    passed, failed = [], []
    if resume:
        # --resume: cut_v1.0.0.sh already ran and its two commits are the
        # checked HEAD the caller verified; every gate below still runs.
        print("resume: cut_v1.0.0.sh skipped, its commits are the checked HEAD")
    else:
        ok, lines = step("cut_v1.0.0.sh",
                         ["sh", os.path.join(root, "scripts", "cut_v1.0.0.sh"),
                          version], root, runner, heartbeat=heartbeat)
        _print(lines)
        if not ok:
            return passed, ["cut_v1.0.0.sh"]
        passed.append("cut_v1.0.0.sh")
    ok, lines = required_fast(root, version, runner, heartbeat=heartbeat)
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
        ok, lines = step(label, cmd, root, runner, heartbeat=heartbeat)
        _print(lines)
        if ok:
            passed.append(label)
        else:
            failed.append(label)
            if stop_early:
                return passed, failed
    return passed, failed


#: R4 (cut.py never created the GitHub Release; on the 1.0.20 cut it was
#: done by hand with `gh release create` and a Codex-drafted body, per the
#: exact spellings recorded in docs/plan/CUT-RUNBOOK-1.0.13.md and
#: docs/plan/READINESS-ROADMAP-2026-08-29.json's C0 evidence:
#:   gh release create v1.0.13 --repo khalilmaaouni/Brother \
#:     --title "Brother 1.0.13" --notes-file docs/releases/1.0.13.md \
#:     --verify-tag
#: `gh release create --help` (gh 2.96.0, checked 2026-09-18): --repo is
#: the INHERITED [HOST/]OWNER/REPO flag, --title/--notes-file/--verify-tag
#: are its own.
GH_HOST_PREFIXES = ("https://github.com/", "http://github.com/",
                    "git@github.com:")


def gh_repo_slug(remote):
    """OWNER/REPO out of a GitHub remote URL, the shape gh's own --repo
    flag reads. Never a separately-typed guess: `remote` is always
    export_public.DEFAULT_REMOTE here, the one constant export_public.py
    itself pushes to."""
    slug = remote.rstrip("/")
    for prefix in GH_HOST_PREFIXES:
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug


def gh_release_command(version, remote=None, notes_file=None):
    """The exact argv R4 runs after a successful push, or prints without
    running under --check: built in ONE place so the real run and the
    --check preview can never drift apart. `notes_file` defaults to the
    release note the chain itself just refreshed and committed
    (docs/releases/<version>.md, the same path every historical `gh
    release create` for this estate has used), relative so it resolves
    against whatever `root` the caller runs it in."""
    remote = remote or EP.DEFAULT_REMOTE
    notes_file = notes_file or os.path.join("docs", "releases",
                                            "%s.md" % version)
    return ["gh", "release", "create", "v%s" % version,
            "--repo", gh_repo_slug(remote),
            "--title", "Brother %s" % version,
            "--notes-file", notes_file,
            "--verify-tag"]


def create_gh_release(root, version, runner=None, heartbeat=None,
                      remote=None, notes_file=None, expected_sha256=None):
    """(ok, lines): runs gh_release_command for real. Called ONLY after a
    successful push (run_chain's own gate); a failure is reported and
    NEVER retried here or anywhere else -- the one truly irreversible step,
    the pushed tag, already landed, so this step's own failure never
    changes the cut's exit code (see run_chain).

    Review (adversarial, 2026-09-19): main() scanned --release-notes-file
    for private terms, dashes and attribution ONCE, before the fence was
    even claimed; this step runs much later, after the build, the human
    approve prompt and the push, so the file could be rewritten in between
    (an editor, a docs tool) and bytes nobody scanned would still publish.
    This is the one place every publish path routes through (its only
    caller is run_chain, grepped), so the re-check lives here, once: the
    file is re-hashed right now and compared to `expected_sha256`, the
    digest main() recorded at scan time. Missing, unreadable, changed, OR
    an unknown (None) expected hash all refuse to publish -- fail closed,
    never publish on a hash this call cannot confirm -- and fall into the
    same already-handled path as any other gh failure: the tag is already
    public, so this is reported and never turned into a REFUSED cut."""
    if notes_file:
        actual_sha256 = _hash_file(notes_file)
        if expected_sha256 is None or actual_sha256 != expected_sha256:
            if actual_sha256 is None:
                why = "could not be re-read"
            elif expected_sha256 is None:
                why = "has no recorded scan hash to check against"
            else:
                why = "changed since it was scanned"
            manual = " ".join(gh_release_command(version, remote, notes_file))
            return False, [
                "REFUSED: gh release create NOT run: --release-notes-file "
                "%s %s. Publishing bytes that were never scanned is "
                "refused; re-scan and re-run: python3 scripts/cut.py "
                "--release-notes-file %s (plus the cut's usual flags), or "
                "once you trust the file unchanged, publish it by hand: %s"
                % (notes_file, why, notes_file, manual)
            ]
    return step("gh release create", gh_release_command(version, remote,
                                                        notes_file),
               root, runner, heartbeat=heartbeat)


def check_mode(root, version, bm, paths, force, runner=None,
               notes_file=None):
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
    if notes_file:
        print("gh release create: would run this after a successful push, "
              "never here: %s"
              % " ".join(gh_release_command(version, notes_file=notes_file)))
    else:
        print("gh release create: would NOT run even after a successful "
              "push (no --release-notes-file given -- the Release still "
              "needs a body); the command it would run once one is named: "
              "%s" % " ".join(gh_release_command(version)))
    if failures:
        print("NOT READY for %s: %s" % (version, "; ".join(failures)))
        return EXIT_REFUSED
    print("READY for %s: fences clear, tree clean, and the rehearsed cut "
          "passed cut_v1.0.0.sh, required_fast, refresh_cut --check and "
          "release_invariant" % version)
    return EXIT_OK


#: B1/B2 (opus review, 2026-09-19): the earlier fix here asked the remote a
#: SECOND time, right after the push, with `git ls-remote --tags remote
#: refs/tags/<tag>` -- an exact refspec that never returns the peeled
#: `^{}` line for an annotated or signed tag (git only sends it back for a
#: wildcard match), so the "commit" it read was really the TAG OBJECT's own
#: sha. export_public.py always creates an annotated or signed tag, so
#: every real cut compared reproduce_export's correctly peeled commit
#: against that wrong value and refused a tag that was perfectly healthy.
#: On top of that bug, a second read of the SAME remote ref reproduce_
#: export.py fetches seconds later can only ever catch the tag moving on
#: the remote in between the two reads -- it can never catch a defect in
#: the tagging step itself, because a wrong commit tagged by export_
#: public.py is on the remote for both reads, and they would agree.
#:
#: The fix at the source: export_public.py itself, in push_appended, peels
#: the tag to a commit LOCALLY right after creating it and before pushing
#: it (the one place that commit is genuine ground truth, before it is
#: ever irreversible), and names that commit in its own TAGGED: line. This
#: reads that line back out of export_public.py's full captured output
#: (see export_public_push) and hands it to reproduce_export.py as
#: --expect-commit; reproduce_export.py's own fetch_and_resolve_tag then
#: does the ONE real remote comparison this estate needs (a fresh fetch,
#: peeled locally with `git rev-parse <tag>^{commit}`, which handles both
#: annotated and lightweight tags correctly) against that ground truth.
#: No second, independently-typed ls-remote read is kept here: one
#: comparison, in the one place that already does it correctly, is a
#: smaller and more honest fix than two mechanisms that can disagree.
def _tagged_commit_re(version):
    """Matches only a TAGGED: line for THIS version's tag: `.search()`'s
    old behaviour took the FIRST TAGGED: line in the text for ANY tag, so
    a debug or leftover TAGGED: line for a different version sitting
    earlier in export_public.py's captured output would have been read
    as this run's own tagged commit. version is regex-escaped since it
    is dotted decimal, not a pattern."""
    return re.compile(
        r"^TAGGED: v%s .* \(local commit ([0-9a-f]{40})\)"
        % re.escape(version), re.MULTILINE)


def _local_tagged_commit(text, version):
    """The commit export_public.py itself resolved --tag to, LOCALLY,
    right after creating the tag and before pushing it (see its own
    TAGGED: line in push_appended, scripts/export_public.py): the ground
    truth of what this cut just tagged, never a second, independent read
    of the remote. Requires EXACTLY ONE line tagging v<version> in `text`;
    None otherwise (see run_chain and export_public_push, both already
    treat None as unknown and refuse):
      - zero such lines: an export_public.py that predates this, or a
        push that failed before tagging.
      - two or more: even when every one of them names the SAME sha,
        this still returns None rather than collapsing them to that
        one value. A duplicate TAGGED: line for the version this run is
        cutting is not a shape export_public.py's own push_appended is
        meant to ever produce once (see its comment: printed once, right
        after the one tag push), so seeing it twice is itself a signal
        something upstream is already wrong; papering over that by
        trusting the repeated value is the smaller diff but the wrong
        one, since a bug that prints the same line twice today can just
        as easily print two DIFFERENT shas tomorrow. One rule, fail
        closed, covers both."""
    matches = _tagged_commit_re(version).findall(text)
    return matches[0] if len(matches) == 1 else None


def export_public_push(root, version, runner=None, heartbeat=None):
    """(ok, lines, local_commit): runs export_public.py --push --tag
    --prove-required-fast and reads the commit it tagged locally out of
    its OWN full captured output. step()'s usual tail-only summary is not
    enough here: it happens that TAGGED: is export_public.py's last
    printed line on a successful tag+push today, but this reads the whole
    captured text with _local_tagged_commit rather than leaning on that
    position ever staying true. `lines` is the same shape step() would
    have returned (the tail line on success, the full output on failure),
    so every existing caller-facing behavior (what run_chain prints,
    park notes, DONE evidence) is unchanged."""
    cmd = [sys.executable, os.path.join(root, "scripts", "export_public.py"),
          "--push", "--tag", "v%s" % version, "--prove-required-fast"]
    proc = _stream("export_public --push", cmd, root, runner,
                   heartbeat=heartbeat)
    text = _text(proc)
    if proc.returncode != 0:
        return False, (["export_public --push: exit %d" % proc.returncode]
                       + text.splitlines()), None
    tail = text.splitlines()[-1] if text else "(no output)"
    return (True, ["export_public --push: exit 0  %s" % tail],
           _local_tagged_commit(text, version))


#: m1 (opus re-review round 2, 2026-09-19): bounded like the old ls-remote
#: read this replaces as a SOURCE of --expect-commit, but this one is never
#: that: a READ-ONLY status line only, used solely to make the refusal
#: below accurate about whether a tag exists on the remote already.
REMOTE_TAG_EXISTS_TIMEOUT_S = 30


def _remote_tag_exists(tag, remote=None, root=None, runner=None,
                       timeout=REMOTE_TAG_EXISTS_TIMEOUT_S):
    """(True/False/None, line): whether `tag` exists on `remote` RIGHT NOW,
    a plain `git ls-remote --tags`. READ-ONLY DIAGNOSTIC ONLY: this value
    is never compared against anything, never passed as --expect-commit,
    and never used to decide whether to publish -- it exists solely so
    the "no TAGGED: line" refusal below can tell an operator whether a
    tag is already there, rather than leaving that as a guess. See the
    comment above _tagged_commit_re for why a remote read must never be
    the SOURCE of an expectation; this is a status message, not a check.
    None when the read itself could not be confirmed (network, timeout):
    the line says so rather than guessing either way."""
    remote = remote or EP.DEFAULT_REMOTE
    proc = _run(["git", "ls-remote", "--tags", remote,
                "refs/tags/%s" % tag], root, runner, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return None, ("could not confirm, read-only, whether %s exists on "
                      "%s (ls-remote exited %d: %s)"
                      % (tag, remote, proc.returncode, detail))
    exists = bool((proc.stdout or "").strip())
    return exists, ("%s %s exist on %s (read-only check, not this run's "
                    "own confirmation)"
                    % (tag, "does" if exists else "does not", remote))


def run_chain(root, version, bm, paths, force, yes, ask, runner=None,
              own_uuid=None, resume_sha=None, heartbeat=None,
              notes_file=None, notes_sha256=None):
    """Everything between the claim and the release of the fence. Returns
    (exit_code, evidence_or_None, park_note): evidence only when the tag
    was pushed and reproduced. `heartbeat` (R3) is threaded through every
    long step so the fence's own liveness evidence stays fresh for as long
    as this process actually runs."""
    reasons = readiness(root, version, bm, paths, force, runner,
                        include_fast=False, own_uuid=own_uuid)
    if reasons:
        return EXIT_REFUSED, None, ("refused before any write: %s"
                                    % "; ".join(reasons))
    start = _run(["git", "rev-parse", "HEAD"], root, runner)
    head = (start.stdout or "").strip()
    if resume_sha is not None:
        # The checked candidate, exactly: a moved HEAD is a different tree
        # that nothing checked, so resume refuses rather than guesses.
        if start.returncode != 0 or not head or head != resume_sha:
            return EXIT_REFUSED, None, (
                "resume refused: HEAD is %s, not the checked commit %s"
                % (head or "unreadable", resume_sha))
        print("resume from the checked commit %s" % head)
    else:
        print("cut starts from %s; the chain's commits stay local until the "
              "push" % (head or "?"))
    passed, failed = chain(root, version, runner,
                           resume=resume_sha is not None, heartbeat=heartbeat)
    if failed:
        return EXIT_REFUSED, None, "%s failed" % failed[0]
    print()
    print("cut %s ready: %s passed; branch as printed above" % (
        version, ", ".join(passed)))
    # Review item 3: the prompt itself must name the Release and its body
    # file, never just "push tag" -- the gh step below is real work the
    # founder is also approving here, not a detail buried in later output.
    if notes_file:
        release_clause = ("and publish the GitHub Release v%s with body %s"
                          % (version, notes_file))
    else:
        release_clause = ("(no GitHub Release will be published: no "
                          "--release-notes-file was given for v%s)" % version)
    if not yes:
        try:
            answer = ask("Approve cut, push tag v%s, %s? [y/N] "
                        % (version, release_clause))
        except EOFError:
            # No keyboard (a Claude Code shell, a pipe): never a yes.
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("declined, nothing pushed")
            return EXIT_OK, None, "declined by operator before push"
    # Review item 4: nothing heartbeats while `ask` blocks at the prompt
    # above, so a fence that was LIVE when readiness() checked it can have
    # gone stale, been adopted and parked by another --force-conflicts
    # pass in the meantime, all before the founder's "y" is even read. One
    # forced, BLOCKING beat right here, immediately before the one
    # irreversible step, catches that: a failed beat means either the
    # store could not be reached or -- since bm_store.py checkpoint is
    # version-gated -- someone else's write already moved this fence out
    # from under this run, and the push refuses rather than proceeding on
    # a claim that may no longer be this session's to hold.
    if heartbeat is not None and not heartbeat.maybe("pre-push", force=True):
        return EXIT_REFUSED, None, (
            "refused before the push: the fence's heartbeat failed just "
            "before pushing (expected version %s); the store could not be "
            "reached, or this fence's version already moved -- most "
            "likely another session adopted it while this cut waited at "
            "the approve prompt" % heartbeat.version)
    push_ok, lines, local_commit = export_public_push(
        root, version, runner, heartbeat=heartbeat)
    _print(lines)
    if not push_ok:
        return EXIT_REFUSED, None, "export_public.py --push failed"
    if not local_commit:
        # m1 (opus re-review round 2, 2026-09-19): export_public.py can
        # exit 0 under --tag while its output carries no TAGGED: line at
        # all. The reachable case is its own "nothing to push" branch: a
        # re-run after an earlier tag push was rejected, where the export
        # content already landed on that earlier run, so THIS run finds
        # nothing new to append and never reaches tag creation. Before
        # this fix, cut.py still recorded the push as "passed" and, once
        # reproduce_export's fetch then found no matching tag, printed
        # "TAG vX IS ALREADY PUBLIC" -- false, since this run tagged
        # nothing. An unknown case here BLOCKS, never proceeds: no
        # reproduce_export, no gh release create, whether or not some OTHER
        # tag happens to be sitting on the remote (that tag, if any, was
        # not made by this run and is never treated as if it were). The
        # refusal names whether a tag exists on the remote as a READ-ONLY
        # diagnostic for the operator deciding how to resume, never as a
        # source of --expect-commit or any comparison.
        exists, exists_line = _remote_tag_exists(
            "v%s" % version, root=root, runner=runner)
        print(exists_line)
        # The operator's recovery is read-only and reuses the two commands
        # the rest of this module already builds from one place: the peel
        # of whatever tag IS on the remote (never compared against
        # anything here, see _remote_tag_exists), and gh_release_command
        # so the Release command named here can never drift from the one
        # run_chain itself would actually run.
        peel_cmd = "git ls-remote %s refs/tags/v%s^{}" % (
            EP.DEFAULT_REMOTE, version)
        manual_gh = " ".join(gh_release_command(version,
                                                 notes_file=notes_file))
        return EXIT_REFUSED, None, (
            "export_public.py --push --tag v%s exited 0 but its output "
            "carried no TAGGED: line, so this run tagged and published "
            "nothing; %s; read the push output above before retrying. "
            "To recover, read-only: see what commit the remote tag (if "
            "any) actually points at with `%s`; only once you have "
            "confirmed by hand that it is the commit you intended is it "
            "safe to publish the Release this cut would have created: "
            "`%s`" % (version, exists_line, peel_cmd, manual_gh))
    passed.append("export_public --push --tag v%s" % version)
    # B1/B2: the ground truth of what THIS cut pushed is the commit
    # export_public.py itself resolved --tag to LOCALLY, right after
    # creating it and before pushing (its own TAGGED: line, read back by
    # export_public_push above) -- never a second, independently-typed
    # remote read here.
    expect_commit = local_commit
    print("confirmed v%s locally: export_public.py tagged %s before "
          "pushing it (its own TAGGED: line)" % (version, expect_commit))
    # Order (opus review, 2026-09-19): reproduce_export.py runs BEFORE
    # gh release create, so a tag that fails reproduction -- including one
    # that does not point at what this cut just tagged -- never gets a
    # public GitHub Release. Every other ordering (push first, gh release
    # after a passing reproduction) is unchanged.
    repro_cmd = [sys.executable,
                os.path.join(root, "scripts", "reproduce_export.py"),
                "--tag", "v%s" % version]
    if expect_commit:
        repro_cmd += ["--expect-commit", expect_commit]
    repro_ok, lines = step("reproduce_export", repro_cmd, root, runner,
                           heartbeat=heartbeat)
    _print(lines)
    if not repro_ok:
        print("TAG v%s IS ALREADY PUBLIC but reproduce_export.py did not "
              "pass; read its output above before anything else" % version)
        return EXIT_REFUSED, None, ("pushed tag v%s but reproduce_export "
                                    "failed" % version)
    passed.append("reproduce_export")
    # Review item 3: the gh step runs ONLY when a real, reviewed body was
    # named. docs/releases/<version>.md is the generated note, not
    # necessarily what actually gets published (the 1.0.20 Release body
    # was a separate 42-line Codex draft, not the 102-line generated
    # note) -- defaulting to it here would publish a body nobody chose.
    # Best-effort and never retried either way: the tag is already public,
    # so a gh failure is reported and left for the founder, never turned
    # into a REFUSED cut over an already-landed tag (item 9: it is folded
    # into the DONE: evidence line below so it is never easy to miss).
    gh_note = None
    if notes_file:
        gh_ok, gh_lines = create_gh_release(root, version, runner,
                                            heartbeat=heartbeat,
                                            notes_file=notes_file,
                                            expected_sha256=notes_sha256)
        _print(gh_lines)
        if gh_ok:
            passed.append("gh release create v%s" % version)
        else:
            gh_note = ("GH RELEASE NOT CREATED for v%s (exit above); the "
                      "tag is already public, create it by hand: %s"
                      % (version, " ".join(gh_release_command(
                          version, notes_file=notes_file))))
            print(gh_note)
    else:
        gh_note = ("GitHub Release NOT published for v%s: no "
                  "--release-notes-file was given. The tag is public with "
                  "no Release yet; publish one by hand once its body is "
                  "ready: %s" % (version,
                                 " ".join(gh_release_command(version))))
        print(gh_note)
    evidence = "cut %s pushed: %s" % (version, ", ".join(passed))
    if force is not None:
        evidence += "; forced over live fences: %s" % force
    if gh_note:
        evidence += "; %s" % gh_note
    print("DONE: %s" % evidence)
    return EXIT_OK, evidence, None


def cut_mode(root, version, bm, paths, force, yes, ask, runner=None,
             resume_sha=None, notes_file=None, notes_sha256=None):
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
    # R3: one Heartbeat for the whole run, refreshing this fence's own
    # updated_at (bm_stall.py's heartbeat evidence) at every step boundary
    # and at most every HEARTBEAT_INTERVAL_S during a long one. Its own
    # `version` tracks every successful beat, so release_fence below must
    # release against heartbeat.version, never the claim's own stale
    # store_version, once a single beat has landed.
    heartbeat = Heartbeat(root, bm_path, uuid, store_version, runner=runner)
    code = EXIT_REFUSED
    evidence = None
    note = "cut.py left without recording why (crash)"
    try:
        code, evidence, note = run_chain(root, version, bm, paths, force,
                                         yes, ask, runner, own_uuid=uuid,
                                         resume_sha=resume_sha,
                                         heartbeat=heartbeat,
                                         notes_file=notes_file,
                                         notes_sha256=notes_sha256)
    except KeyboardInterrupt:
        code, evidence, note = 130, None, "interrupted mid-chain"
        print("interrupted")
    finally:
        # Every exit path releases the fence; a failing release is shouted
        # by release_fence but never changes the exit code of the cut.
        release_fence(root, bm_path, uuid, heartbeat.version, evidence,
                      note or "no reason recorded", runner, session=session)
    return code


def main(argv=None, root=None, runner=None, ask=None, terms_file=None):
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
    ap.add_argument("--resume", metavar="SHA", default=None,
                     help="resume a cut whose cut_v1.0.0.sh already ran and "
                          "passed: requires HEAD to be exactly this full "
                          "commit and a clean tree, skips only cut_v1.0.0.sh, "
                          "and still runs required_fast.sh, refresh_cut "
                          "--check, release_invariant, the approve question, "
                          "the push and the reproduction")
    ap.add_argument("--answer-file", metavar="PATH", default=None,
                     help="answer the approve prompt by writing y or n into "
                          "PATH instead of typing it (how a Claude Code "
                          "session relays the founder's answer); must not "
                          "exist yet; no answer in %d s declines"
                          % ANSWER_TIMEOUT_S)
    ap.add_argument("--release-notes-file", metavar="PATH", default=None,
                     help="R4: the body file for `gh release create "
                          "--notes-file`, run only after a successful push "
                          "(default: docs/releases/<version>.md, the note "
                          "the chain itself just refreshed and committed)")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    root = root or ROOT
    notes_file = args.release_notes_file
    notes_sha256 = None
    if notes_file:
        # Review item (2026-09-19): BEFORE the fence is claimed, absolute
        # (against the CALLER's cwd, not this release worktree) and
        # scanned with the same gates a push runs. What gets scanned here
        # is exactly what gh_release_command later publishes: every
        # downstream use of `notes_file` from here on is this same
        # resolved absolute path, never the original possibly-relative
        # one, so the two can never read different files.
        notes_file = _release_notes_abspath(notes_file)
        # Item (2026-09-19): read the file's bytes ONCE (_read_file_bytes)
        # and scan (_scan_release_notes_bytes) and hash that same bytes
        # object -- not a path-based scan followed by a separate
        # _hash_file() call, which used to open the path twice. Two opens
        # leave a window between them where a rewrite (an
        # editor, a docs tool) makes the recorded hash describe bytes that
        # were never the ones scanned, even though the scan itself is
        # correct. create_gh_release's own re-hash right before publish
        # (unchanged, still a deliberate SECOND, independent read -- see
        # its docstring) is what catches a rewrite happening AFTER this
        # point; this fix closes the earlier window, between the scan and
        # the hash, that create_gh_release cannot see at all.
        notes_raw, notes_err = _read_file_bytes(notes_file)
        if notes_raw is None:
            print("REFUSED: --release-notes-file %s could not be read: %s"
                  % (notes_file, notes_err))
            return EXIT_REFUSED
        ok, lines = _scan_release_notes_bytes(notes_raw, notes_file,
                                              terms_file)
        _print(lines)
        if not ok:
            return EXIT_REFUSED
        notes_sha256 = hashlib.sha256(notes_raw).hexdigest()
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
    if args.resume is not None and (args.check or
                                    not re.match(r"^[0-9a-f]{40}$",
                                                 args.resume)):
        print("NO-DATA: --resume needs the full 40-hex commit the checked "
              "cut left at HEAD, and cannot be combined with --check")
        return EXIT_NODATA
    if args.check:
        return check_mode(root, version, bm, paths, args.force_conflicts,
                          runner, notes_file=notes_file)
    return cut_mode(root, version, bm, paths, args.force_conflicts,
                    args.yes, ask, runner, resume_sha=args.resume,
                    notes_file=notes_file, notes_sha256=notes_sha256)


if __name__ == "__main__":
    sys.exit(main())
