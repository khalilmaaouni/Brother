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
docs/maintainer/BROTHER-MAINTAINER-VERBS.md "cut"; CV1.e, ruling
2026-10-03: the session rehearses and runs --check, and the owner runs the
real cut from his own terminal, where its one answer is typed; a real cut
refuses --answer-file and a standard input that is not a terminal):

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
        plugin_bump_gate.py on that bumped tree (a plugin whose shipped
        files changed since the previous public tag must carry a new
        version), required_fast.sh, refresh_cut.py --check and
        release_invariant.py, prints one
        summary, asks one question naming the version, the full HEAD and
        the rehearsal record that covers it (ending "[y/N]"), and
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
import collections.abc
import hashlib
import importlib.util
import json
import math
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
# CV1.b: the rehearsal record a real cut reads before it claims the fence.
import cut_rehearsal as CR  # noqa: E402
# Review item (2026-09-19): --release-notes-file goes straight to a public
# GitHub Release, so it gets the SAME scans the push gates already run
# (private terms, em/en dashes, attribution lines) -- reused from
# scripts/outgoing_scan.py, never a second, independently-typed copy of
# the matching rules (short-term case sensitivity, the length cutoff).
import outgoing_scan as OS  # noqa: E402
# C0.5: the shadow comparison parses every gate line with gate_order's own
# RESULT_RE (one parser, both ends) instead of a second, typed pattern. A
# parser that cannot be loaded is never a pass: GO stays None and every
# parse REFUSES (see _shadow_verdicts).
try:
    import gate_order as GO  # noqa: E402
except ImportError:  # pragma: no cover - a missing parser refuses
    GO = None


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

# CV1.a (2026-10-03): version_skip_refusal reads the tag state through
# next_cut.tag_state, never by running the script. A tree without it cannot
# say which version is in flight, so every real cut refuses (NO-DATA).
try:
    import next_cut as NC  # noqa: E402
except ImportError:
    NC = None

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
    """The approve prompt answered by a file instead of a keyboard. CV1.e
    (ruling 2026-10-03): a real cut never uses it, main refuses
    --answer-file; it stays for the scripted tests that build their own
    `ask`. No answer within the timeout reads as a decline."""
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


def _run(cmd, root, runner=None, stdin_text=None, timeout=None, env=None):
    """One subprocess, its own exit code read before anything else runs.
    Never raises on a missing binary or a timeout: a CompletedProcess-
    shaped failure (127 missing binary, 124 timed out, the error in
    stderr) so every caller reads one shape. `timeout=None` (every
    caller today) behaves exactly as before: subprocess.run treats
    timeout=None as no bound; a future caller needing a bound passes it
    directly."""
    runner = runner or subprocess.run
    # C0.5: env=None keeps the current environment; the shadow runs pass a
    # whole mapping so the pinned and unpinned runs differ by exactly one
    # variable (REQUIRED_FAST_ORDER_PIN) and by nothing else.
    kwargs = dict(cwd=root, capture_output=True, text=True,
                  input=stdin_text, timeout=timeout)
    if env is not None:
        kwargs["env"] = env
    try:
        return runner(cmd, **kwargs)
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
    # CV1.c: the U8 catalog step edits the Cursor catalog and reads the
    # agents one, so the fence covers every catalog the step touches.
    paths = ["scripts/", "bundle/", "docs/releases/", ".claude-plugin/",
             ".cursor-plugin/", ".agents/plugins/"]
    products = os.path.join(root, "products")
    if os.path.isdir(products):
        for name in sorted(os.listdir(products)):
            for rel in ("CHECKSUMS.sha256", ".claude-plugin/"):
                candidate = os.path.join(products, name, rel)
                if os.path.exists(candidate):
                    paths.append("products/%s/%s" % (name, rel))
    return paths


def read_next_version(root, runner=None):
    """(version, lines): the version next_cut.py prints, or (None, why).
    CV1.a: always --version-only, so the version comes from the one version
    source and never depends on the release policy naming a weekday."""
    proc = _run([sys.executable, os.path.join(root, "scripts", "next_cut.py"),
                 "--version-only"], root, runner)
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


def declared_exceptions(root, runner=None):
    """(ok, lines): scripts/battery_exception_audit.py over this tree, read
    in about a second, BEFORE any minutes-long step. Measured 2026-09-20 on
    the 1.0.21 cut: two declared exceptions had expired the day before, the
    export tree's readiness gate refused on them at step 2b2, and that step
    sits behind a release note measurement of about 35 minutes. The refusal
    was knowable at second one; it cost a 41 minute round instead. Anything
    but the audit's exit 0 blocks, its NO-DATA (exit 2) and a missing script
    (127) included: a cut that cannot read its own exceptions is not ready."""
    script = os.path.join(root, "scripts", "battery_exception_audit.py")
    proc = _run([sys.executable, script], root, runner)
    out = [l for l in _text(proc).splitlines() if l.strip()]
    if proc.returncode == 0:
        return True, ["declared exceptions: %s" % (out[-1] if out else "OK")]
    return False, (["REFUSED: battery_exception_audit.py exit %d, renew or "
                    "remove every exception it names before cutting (the "
                    "export tree's readiness gate refuses on the same "
                    "entries, 40 minutes later):" % proc.returncode]
                   + ["  " + l for l in out[-12:]])


def preflight(root, version, runner=None):
    """(ok, lines): scripts/cut_preflight.py, every gate that can refuse the
    cut LATE, asked now. Measured 2026-09-20: the 1.0.21 cut was refused six
    times, each behind an 84 minute chain or on the public runner, and each
    knowable in minutes: clock gates that lapse mid cut, a leftover public
    release branch, a secret shape in the changelog text, and tests that
    pass only in the author's tree or the author's home folder. Anything but
    exit 0 blocks, NO-DATA (2) and a missing script (127) included."""
    proc = _stream("cut_preflight.py",
                   [sys.executable, os.path.join(root, "scripts",
                                                 "cut_preflight.py"),
                    "--version", version], root, runner)
    if proc.returncode == 0:
        return True, ["preflight: CLEAR"]
    return False, ["REFUSED: cut_preflight.py exit %d; clear every line it "
                   "marked REFUSED or NO-DATA above before cutting"
                   % proc.returncode]


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
    ok, lines = declared_exceptions(root, runner)
    _print(lines)
    if not ok:
        reasons.append("declared battery exception(s) not current")
    if not (reasons and stop_early):
        ok, lines = preflight(root, version, runner)
        _print(lines)
        if not ok:
            reasons.append("cut preflight did not clear")
    if (reasons and stop_early) or not include_fast:
        return reasons
    ok, lines = required_fast(root, version, runner)
    _print(lines)
    if not ok:
        reasons.append("required_fast.sh did not pass")
    return reasons


def _record(passed, label, lines):
    """A step that says NOT APPLICABLE is n/a, never a pass."""
    if any("NOT APPLICABLE" in line for line in lines):
        print("%s: n/a" % label)
    else:
        passed.append(label)


def chain(root, version, runner=None, stop_early=True, resume=False,
         heartbeat=None):
    """The release chain in the order it can be true: cut_v1.0.0.sh first
    (bump, regenerate, commit, manifest and note, all LOCAL: it pushes
    nothing), then plugin_bump_gate.py and required_fast.sh on that bumped
    tree, then refresh_cut.py --check, release_invariant.py and
    donecheck_u8.py (the catalogs publish one plugin, F10). Before 2026-09-17 the
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
        print("resume: cut_v1.0.0.sh and retire_catalogs --apply skipped, "
              "their commits are the checked HEAD")
    else:
        # CV1.c: the U8 catalog edit runs BEFORE the cut script, so its
        # `git add -A` commit carries the edit and the version bump rewrites
        # the ref once, on a single entry.
        ok, lines = step("retire_catalogs --apply",
                         [sys.executable,
                          os.path.join(root, "scripts", "retire_catalogs.py"),
                          "--apply", "--version", version],
                         root, runner, heartbeat=heartbeat)
        _print(lines)
        if not ok:
            return passed, ["retire_catalogs --apply"]
        _record(passed, "retire_catalogs --apply", lines)
        ok, lines = step("cut_v1.0.0.sh",
                         ["sh", os.path.join(root, "scripts", "cut_v1.0.0.sh"),
                          version], root, runner, heartbeat=heartbeat)
        _print(lines)
        if not ok:
            return passed, ["cut_v1.0.0.sh"]
        passed.append("cut_v1.0.0.sh")
    # Seconds, so it runs BEFORE the minutes-long gate, on --resume too, and
    # on the bumped tree: cut_v1.0.0.sh itself writes into a product
    # directory, which the preflight's read of the unbumped tree cannot see.
    # `claude plugin update` compares the version string only (measured
    # 2026-09-20: 1.0.21 shipped 24 and 12 changed product files under
    # unchanged versions and the updater said "already at the latest").
    ok, lines = step("plugin_bump_gate",
                     [sys.executable,
                      os.path.join(root, "scripts", "plugin_bump_gate.py"),
                      "--version", version], root, runner, heartbeat=heartbeat)
    _print(lines)
    if ok:
        passed.append("plugin_bump_gate")
    else:
        failed.append("plugin_bump_gate")
        if stop_early:
            return passed, failed
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
        # F10 (architecture review 2026-09-30): U8 retires the brothermode,
        # brothersbe and brotherds marketplace entries AT the 1.1.0 cut, and
        # until this step nothing in the chain read a catalog, so a cut
        # could ship four plugins while the plan said one. donecheck_u8.py
        # exits 1 while any entry but brother is listed and 2 when a
        # catalog cannot be read; either refuses the cut here, on the
        # rehearsed tree, before anything irreversible. Handed the TREE it
        # enumerates every marketplace.json in it (H1, attack 2026-09-30:
        # reading one catalog let the Cursor catalog ship three plugins).
        # The catalog edit itself stays the owner's (U8), never this
        # script's.
        ("donecheck_u8 (one plugin)", [sys.executable,
                                       os.path.join(root, "scripts",
                                                    "donecheck_u8.py"),
                                       root]),
        # CV1.c: the kept entry names this version and the catalogs are in
        # the one plugin end state, refused here before the owner is asked.
        ("retire_catalogs --check", [sys.executable,
                                     os.path.join(root, "scripts",
                                                  "retire_catalogs.py"),
                                     "--check", "--version", version]),
    ):
        ok, lines = step(label, cmd, root, runner, heartbeat=heartbeat)
        _print(lines)
        if ok:
            _record(passed, label, lines)
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


def rehearsed_exit_refusal(root, rehearsal, version, runner=None):
    """'' when the rule the real cut asks after its chain (run_chain, through
    covering_record's resume rule) accepts what the chain just committed in
    the rehearsal tree, else why. 2026-10-07: the first real cut under the
    rehearsal rule passed a green chain and stopped before its question,
    because the chain's own re-pin was a change the exit rule refused and no
    rehearsal had ever asked it. READY now means the real cut reaches its
    question."""
    base = _run(["git", "rev-parse", "HEAD"], root, runner)
    tip = _run(["git", "rev-parse", "HEAD"], rehearsal, runner)
    base_sha = (base.stdout or "").strip()
    tip_sha = (tip.stdout or "").strip()
    if (base.returncode != 0 or tip.returncode != 0
            or not CR.SHA_RE.fullmatch(base_sha)
            or not CR.SHA_RE.fullmatch(tip_sha)):
        return "HEAD cannot be read in the checkout or in the rehearsal tree"
    return CR.resume_chain_problem(rehearsal, base_sha, tip_sha, version,
                                   _cut_own_paths(rehearsal), runner)


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
            why = rehearsed_exit_refusal(root, rehearsal, version, runner)
            if why:
                print("exit rule: %s" % why)
                failures.append("the cut's exit rule would refuse before the "
                                "question: %s" % why)
            else:
                print("exit rule: the commits the chain made are the cut's own")
            # the real cut refuses to push an uncommitted change (pre_push_recheck
            # asks dirty_paths after the answer): ask it here, on the same tree
            dirty, why = dirty_paths(rehearsal, runner)
            if dirty is None:
                failures.append("the rehearsal tree's status cannot be read "
                                "after the chain: %s" % why)
            elif dirty:
                failures.append("the chain left uncommitted changes the cut "
                                "would refuse to push: %s"
                                % ", ".join(l.strip() for l in dirty[:5]))
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
          "passed cut_v1.0.0.sh, required_fast, refresh_cut --check, "
          "release_invariant, donecheck_u8 (one plugin) and the "
          "retire_catalogs apply and check" % version)
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
    # CV1.e: the question names the exact commit the chain left (its own
    # commits included) and the READY rehearsal that still covers it; a HEAD
    # that cannot be read or a record that no longer covers it stops here.
    now = _run(["git", "rev-parse", "HEAD"], root, runner)
    cut_head = (now.stdout or "").strip()
    if now.returncode != 0 or not CR.SHA_RE.fullmatch(cut_head):
        return EXIT_REFUSED, None, (
            "refused before the question: HEAD cannot be read after the "
            "chain (%s)" % (_text(now) or "no output"))
    record, why = _covering_record(root, version, resume_sha or cut_head,
                                   runner)
    if why or not record:
        reason = why or "no rehearsal record covers %s" % cut_head
        print("refused before the question, nothing pushed: %s" % reason)
        return EXIT_REFUSED, None, (
            "refused before the question: %s" % reason)
    if not yes:
        try:
            answer = ask(question_text(version, cut_head, record,
                                       release_clause))
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
    # CV1.e: nothing moves between the answer and the push. The answer can
    # wait up to its timeout, and a commit, an edit or a catalog change made
    # in that window was never rehearsed, so it is never pushed.
    why = pre_push_recheck(root, version, cut_head, record,
                           resume_sha=resume_sha, runner=runner)
    if why:
        print(why)
        return EXIT_REFUSED, None, ("refused before the push, nothing "
                                    "pushed: %s" % why)
    push_ok, lines, local_commit = export_public_push(
        root, version, runner, heartbeat=heartbeat)
    _print(lines)
    if not push_ok:
        # CV1.e: a failed push is a STOP with a record, never a retry. A
        # refusal shape, or any text not positively a network error, is the
        # host refusing, and that record blocks every later cut of this
        # version until the owner deletes it.
        detail = "\n".join(lines)
        kind = classify_push_failure(detail)
        try:
            where = "stop record %s" % write_stop_record(
                CR.evidence_dir(), version, cut_head, kind, detail)
        except (OSError, ValueError) as exc:
            # cut_gates proved the folder writable before the fence, so this
            # is a folder that broke during the run: the stop still stands,
            # loudly, and the owner reads the push output above himself.
            stop = ("STOP: export_public.py --push failed (%s) and the stop "
                    "record could NOT be written (%s); the push is not "
                    "retried or routed around, and nothing on disk blocks the "
                    "next cut of v%s, so do not run it again before reading "
                    "the push output above" % (kind, exc, version))
            print(stop)
            return EXIT_REFUSED, None, stop
        if kind == STOP_HOST_REFUSED:
            after = ("every later cut of v%s is refused until the owner "
                     "reads the record and deletes it" % version)
        else:
            after = ("a positively identified network failure: the owner "
                     "may run the cut again from his terminal")
        stop = ("STOP: export_public.py --push failed (%s); %s; the push is "
                "not retried or routed around; %s" % (kind, where, after))
        print(stop)
        return EXIT_REFUSED, None, stop
    if not local_commit:
        # m1 (opus re-review round 2, 2026-09-19): export_public.py can
        # exit 0 under --tag while its output carries no TAGGED: line at
        # all. When this was written the reachable case was its "nothing to
        # push" branch returning before tag creation; that branch now
        # resumes into the tag block, and since 2026-09-26 an existing tag
        # refuses before anything is pushed and a push that creates no new
        # tag refuses too, so no known path reaches here. It stays as a
        # defensive check on the output contract: a parser mismatch or a
        # future exporter change must BLOCK, never proceed. Before
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


# --- CV1.a: the pre fence gates of a real cut -------------------------------

#: A real cut never reads a redirected evidence folder (R-CVE-08): either
#: variable present in its environment refuses it.
CUT_REDIRECT_ENV = ("BROTHER_CUT_EVIDENCE_DIR", "BROTHER_CUT_TEST")
CUT_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def env_redirect_refusal(env=None):
    """'' when the environment (default os.environ) names neither variable of
    CUT_REDIRECT_ENV; otherwise the refusal in words. An environment that is
    not a mapping cannot be read and refuses."""
    env = os.environ if env is None else env
    if not isinstance(env, collections.abc.Mapping):
        return ("REFUSED: the cut's environment is a %s, not a mapping, so a "
                "redirected evidence folder cannot be ruled out"
                % type(env).__name__)
    named = [name for name in CUT_REDIRECT_ENV if name in env]
    if named:
        return ("REFUSED: %s is set; a real cut never reads a redirected "
                "evidence folder, unset it" % " and ".join(named))
    return ""


def version_skip_refusal(root, version, remote="", runner=None):
    """'' only when `version` equals what next_cut.derive_next answers for this
    tree (CV1.e re-base, R-CVE-07); otherwise the refusal in words, carrying
    derive_next's own basis line. `remote` defaults to
    export_public.DEFAULT_REMOTE. So an explicit version below the highest
    public tag, on a drifted carrier, on an unreadable source or remote, or on
    a local-only tag is refused exactly as the derived path is. Reads through
    next_cut's functions, never by running the script. A REFUSED line names
    another version than the next cut, a NO-DATA line could not tell."""
    if not isinstance(version, str) or not CUT_VERSION_RE.fullmatch(version):
        return "NO-DATA: the cut version %r is not X.Y.Z" % (version,)
    if NC is None:
        return ("NO-DATA: scripts/next_cut.py could not be loaded, so whether "
                "%s is the next cut is unknown" % version)
    if remote == "":
        remote = EP.DEFAULT_REMOTE
    try:
        derived, basis = NC.derive_next(root, remote, runner)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
        derived, basis = None, "NO-DATA: derive_next could not run (%s)" % exc
    if derived is None:
        if not str(basis).startswith("NO-DATA"):
            basis = "NO-DATA: %s" % basis
        return ("%s; whether %s is the next cut is unknown, nothing is "
                "guessed" % (basis, version))
    if version != derived:
        return ("REFUSED: --version %s skips v%s or goes behind it: v%s is "
                "the next cut (%s); omit --version" % (version, derived,
                                                       derived, basis))
    return ""


def _cut_own_paths(root):
    """The files the cut's own two commits may write: release_paths plus the
    version carriers (the written ones and the per product ones)."""
    import pathlib
    import version_source
    names = [c.label.replace(" ", ":").split(":")[0]
             for c, _want in version_source.product_carriers(
                 pathlib.Path(root))]
    return tuple(release_paths(root)) + tuple(version_source.CARRIER_FILES_REL) + tuple(
        n for n in names if n.startswith("products/"))


def rehearsal_refusal(root, version, resume_sha=None, runner=None):
    """CV1.b: '' when a READY rehearsal record covers this commit."""
    return CR.rehearsal_refusal(root, version, resume_sha or "", runner=runner, allowed_paths=_cut_own_paths(root))


# --- CV1.e: the owner's terminal is the only seam ---------------------------
#
# Ruling 2026-10-03 (owner decision 5): the owner runs the real cut from his
# own terminal and types its one answer there; a session rehearses and runs
# --check. A terminal is where his hand is, not an authentication: what this
# code proves is that a real cut refuses --answer-file and a standard input
# that is not a terminal, that --yes needs a terminal, that the question
# names the commit and the rehearsal, that nothing moves between the answer
# and the push, and that a failed push is a recorded stop. The terminal check
# stops an accidental run from a shell without a terminal, never a deliberate
# one (a pseudo terminal, or main called in process with the seam patched):
# what stops a deliberate session is the host's refusal of a session's push
# and the tag and branch protection on the public repository.

REFUSAL_RE = re.compile(
    r"(?i)blocked|classifier|denied|not permitted|forbidden|\b403\b|"
    r"protected branch")
NETWORK_FAILURE_RE = re.compile(
    r"(?i)could not resolve host|timed out|connection reset|rpc failed|"
    r"early eof")
STOP_SCHEMA = "cv1.stop.1"
STOP_HOST_REFUSED = "host-refused"
STOP_PUSH_FAILED = "push-failed"
STOP_KINDS = (STOP_HOST_REFUSED, STOP_PUSH_FAILED)
STOP_DETAIL_CHARS = 400


def _stdin_is_tty():
    """sys.stdin.isatty(); a false answer on any error. The one seam tests
    patch."""
    try:
        return sys.stdin.isatty() is True
    except Exception:  # noqa: BLE001 - any error reads as no terminal
        return False


def yes_refusal(yes, stdin_is_tty):
    """'' when the flag is absent or a person is at a terminal; otherwise the
    refusal in words. Anything but a True terminal answer refuses."""
    if not yes or stdin_is_tty is True:
        return ""
    return ("NO-DATA: --yes needs a person at a terminal; standard input is "
            "not one, so the question is not skipped (the owner runs the cut "
            "from his own terminal; a seam against an accidental run, not "
            "authentication)")


def answer_file_refusal(answer_file, dry_run, stdin_is_tty):
    """'' when the run is a dry run (--check or --shadow: nothing is pushed
    and nothing is asked), or no answer file is named and a person is at a
    terminal; otherwise the refusal in words (a real cut reads its answer from
    the owner's terminal only, ruling 2026-10-03). A real cut whose stdin is
    not a terminal refuses too. Only a True dry_run counts as a dry run."""
    if dry_run is True:
        return ""
    if answer_file is not None:
        return ("NO-DATA: a real cut takes no --answer-file: the answer is "
                "typed on the owner's terminal (ruling 2026-10-03); a session "
                "rehearses and runs --check, the owner runs the cut")
    if stdin_is_tty is not True:
        return ("NO-DATA: a real cut reads its one answer from a terminal and "
                "standard input is not one: the answer is typed on the "
                "owner's terminal")
    return ""


def question_text(version, head, rehearsal, release_clause):
    """The one question: the version, the full HEAD, the rehearsal record and
    the release clause, ending [y/N]."""
    return ("Approve cut v%s at commit %s, rehearsed by %s: push tag v%s, "
            "%s? [y/N] " % (version, head, rehearsal, version,
                            release_clause))


def classify_push_failure(text):
    """'push-failed' only when REFUSAL_RE does not match and
    NETWORK_FAILURE_RE does; every other text, empty or unreadable included,
    is 'host-refused'. Either way the run stops."""
    if not isinstance(text, str) or REFUSAL_RE.search(text):
        return STOP_HOST_REFUSED
    if NETWORK_FAILURE_RE.search(text):
        return STOP_PUSH_FAILED
    return STOP_HOST_REFUSED


def write_stop_record(directory, version, sha, kind, detail, now=None):
    """Atomic write of stop-<version>-<sha12>.json; returns the path. Raises
    ValueError on a sha that is not 40 hex or a kind outside the two (and on
    any other malformed field); an unwritable folder raises OSError."""
    if not isinstance(directory, str) or not directory:
        raise ValueError("the evidence folder %r is not a path" % (directory,))
    if not isinstance(version, str) or not CUT_VERSION_RE.fullmatch(version):
        raise ValueError("version %r is not X.Y.Z" % (version,))
    if not isinstance(sha, str) or not CR.SHA_RE.fullmatch(sha):
        raise ValueError("sha %r is not 40 hex" % (sha,))
    if kind not in STOP_KINDS or not isinstance(kind, str):
        raise ValueError("kind %r is not one of %s" % (kind,
                                                      ", ".join(STOP_KINDS)))
    if not isinstance(detail, str):
        raise ValueError("detail is not text")
    if now is not None and not (isinstance(now, (int, float))
                                and not isinstance(now, bool)
                                and math.isfinite(now)):
        raise ValueError("now %r is not a finite number" % (now,))
    stamp = time.time() if now is None else float(now)
    os.makedirs(directory, exist_ok=True)
    record = {"schema": STOP_SCHEMA, "version": version, "sha": sha,
              "kind": kind, "detail": detail[:STOP_DETAIL_CHARS],
              "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime(stamp))}
    path = os.path.join(directory, "stop-%s-%s.json" % (version, sha[:12]))
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), uuid.uuid4().hex[:6])
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=1, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path


def stop_record_refusal(directory, version):
    """'' unless a host-refused record for this version exists at any sha (a
    corrupt record refuses too). It reads stop-<version>-*.json and never
    HEAD, so a moved HEAD never clears it: only the owner deleting the file
    does. A push-failed record does not block. An absent folder holds no
    record; an unlistable one refuses."""
    if not isinstance(version, str) or not CUT_VERSION_RE.fullmatch(version):
        return "NO-DATA: the cut version %r is not X.Y.Z" % (version,)
    if not isinstance(directory, str) or not directory:
        return ("REFUSED: the evidence folder %r is not a path, so a stop "
                "record cannot be ruled out" % (directory,))
    if not os.path.lexists(directory):
        return ""
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        return ("REFUSED: the evidence folder %s cannot be listed (%s), so a "
                "stop record for v%s cannot be ruled out" % (directory, exc,
                                                             version))
    prefix = "stop-%s-" % version
    for name in names:
        if not (name.startswith(prefix) and name.endswith(".json")):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            return ("REFUSED: the stop record %s is corrupt or unreadable "
                    "(%s); a cut of v%s stays refused until the owner reads "
                    "it and deletes it" % (path, exc, version))
        if (not isinstance(data, dict) or data.get("schema") != STOP_SCHEMA
                or data.get("version") != version
                or data.get("kind") not in STOP_KINDS):
            return ("REFUSED: the stop record %s is malformed; a cut of v%s "
                    "stays refused until the owner reads it and deletes it"
                    % (path, version))
        if data["kind"] == STOP_HOST_REFUSED:
            return ("REFUSED: STOP record %s: the host refused the push of v%s "
                    "at %s; the push is never retried or routed around, and "
                    "a moved HEAD does not clear it. Only the owner clears "
                    "it, by deleting that file once he has read why"
                    % (path, version, str(data.get("sha"))[:12]))
    return ""


def evidence_write_refusal(directory):
    """'' when a stop record could be written into `directory` now: the folder
    is created if missing, then a uniquely named probe file is created and
    removed. Any OSError, or a directory that is not a path, refuses with the
    reason, so a real cut never starts while its stop record could not be
    filed."""
    if not isinstance(directory, str) or not directory:
        return ("REFUSED: the evidence folder %r is not a path, so a stop "
                "record could not be written" % (directory,))
    probe = os.path.join(directory, ".write-probe-%d-%s"
                         % (os.getpid(), uuid.uuid4().hex))
    try:
        os.makedirs(directory, exist_ok=True)
        os.close(os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        os.unlink(probe)
    except OSError as exc:
        return ("REFUSED: the evidence folder %s is not writable (%s), so a "
                "stop record could not be filed if the push failed; fix the "
                "folder before cutting" % (directory, exc))
    return ""


def _covering_record(root, version, resume_sha, runner=None):
    """(record_path, reason) from cut_rehearsal.covering_record for HEAD after
    the chain, through the resume rule: every commit after the rehearsed one
    must be the cut's own."""
    return CR.covering_record(root, version, resume_sha=resume_sha,
                              runner=runner,
                              allowed_paths=_cut_own_paths(root))


def pre_push_recheck(root, version, head, record_path, resume_sha=None,
                     runner=None):
    """'' when HEAD still equals `head`, the working tree is clean (the
    existing dirty_paths(root), so an uncommitted edit to a script the push
    runs cannot land during the answer wait), covering_record still returns
    `record_path` (through the post-chain rule of resume_chain_problem), and
    retire_catalogs --check still exits 0 (or is not applicable); otherwise
    the difference in words. Unreadable input refuses."""
    if not isinstance(head, str) or not CR.SHA_RE.fullmatch(head):
        return ("REFUSED: the commit the question named (%r) is not 40 hex"
                % (head,))
    if not isinstance(record_path, str) or not record_path:
        return ("REFUSED: no rehearsal record was named by the question, so "
                "nothing can be compared before the push")
    proc = _run(["git", "rev-parse", "HEAD"], root, runner)
    now = (proc.stdout or "").strip()
    if proc.returncode != 0 or now != head:
        return ("REFUSED: HEAD is %s, not %s, the commit the question named; "
                "a commit made during the answer wait was never rehearsed"
                % (now or "unreadable", head))
    dirty, why = dirty_paths(root, runner)
    if dirty is None:
        return "REFUSED: %s" % why
    if dirty:
        return ("REFUSED: the working tree changed during the answer wait "
                "(%s); an uncommitted edit is never pushed"
                % ", ".join(l.strip() for l in dirty[:5]))
    path, why = _covering_record(root, version, resume_sha or head, runner)
    if why or path != record_path:
        return ("REFUSED: the rehearsal record %s no longer covers %s (%s)"
                % (record_path, head, why or "the covering record is now %s"
                   % (path or "none")))
    proc = _run([sys.executable,
                 os.path.join(root, "scripts", "retire_catalogs.py"),
                 "--check", "--version", version], root, runner)
    if proc.returncode != 0:
        return ("REFUSED: retire_catalogs --check exited %d after the answer: "
                "%s" % (proc.returncode, _text(proc) or "no output"))
    return ""


def cut_gates(root, version, resume_sha=None, remote="", runner=None,
              env=None):
    """The first refusal before the fence is claimed, or ''. Order:
    env_redirect_refusal (BROTHER_CUT_EVIDENCE_DIR or BROTHER_CUT_TEST present
    in `env`, default os.environ: refused, a real cut never reads a redirected
    evidence folder), then version_skip_refusal, then (CV1.b)
    rehearsal_refusal, then (CV1.e) stop_record_refusal and
    evidence_write_refusal (the folder a stop record would go to is
    writable now)."""
    refusal = env_redirect_refusal(env)
    if refusal:
        return refusal
    refusal = version_skip_refusal(root, version, remote, runner)
    if refusal:
        return refusal
    refusal = rehearsal_refusal(root, version, resume_sha=resume_sha,
                                runner=runner)
    if refusal:
        return refusal
    refusal = stop_record_refusal(CR.evidence_dir(), version)
    if refusal:
        return refusal
    return evidence_write_refusal(CR.evidence_dir())


def cut_mode(root, version, bm, paths, force, yes, ask, runner=None,
             resume_sha=None, notes_file=None, notes_sha256=None):
    refusal = cut_gates(root, version, resume_sha=resume_sha, runner=runner)
    if refusal:
        # CV1.a: refused before anything is claimed, parked or written.
        print(refusal)
        if str(refusal).startswith("NO-DATA"):
            return EXIT_NODATA
        return EXIT_REFUSED
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


# --- C0.4b: dedupe, reuse, limited cache, limited resume -----------------
#
# Three seams, every one of them fail closed. note_suite_step measures every
# DISTINCT suite once and fans that one result back to every naming row.
# cached_run_check answers from the store only on an exact content key with
# both the flake gate and the clock gate clear, and RUNS the check on every
# other path, because a miss means run. resume_guard rehashes every release
# path, the notes digest, HEAD and the clean tree before cut_v1.0.0.sh may be
# skipped. Unknown, corrupt or missing input is never a hit: it is a MISS, a
# NO-DATA line, or a refusal.

CACHE_DIR_DEFAULT = "~/.brother/cut-cache"
CACHE_SCHEMA_VERSION = "c0.cache.1"
CACHE_VERDICT_PASS = "PASS"
#: R-STAT-03 minima. Below either one the flake rate is not measurable and is
#: taken as 1.0, which blocks the cache for that gate outright.
FLAKE_MIN_RUNS = 20
FLAKE_MIN_REPEATED = 5
FLAKE_MAX = 0.05
#: R-STAT-06: the deadline is NOT part of the key, it is re evaluated here.
CUT_DEADLINE_ENV = "CUT_DEADLINE_UNIX"

_C04B_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_C04B_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class VerdictKeyRefused(Exception):
    """Raised by verdict_key when a keyed input is unknown, missing or
    unreadable. Unknown input is a MISS, never a hit, so cached_run_check
    catches this and runs the check."""


def _c04b_text(value):
    """True only for a str that carries something after its whitespace."""
    return isinstance(value, str) and value.strip() != ""


def _c04b_hex64(value):
    return isinstance(value, str) and bool(_C04B_HEX64_RE.match(value))


def _c04b_file_sha256(path):
    """(hex_digest, why). ONE read of path's bytes, through this module's
    own _read_file_bytes so there is one read discipline in this file. A
    missing file, a directory, an unreadable file or a wrongly typed path is
    (None, why): never an exception and never a digest."""
    if not _c04b_text(path):
        return None, "path must be a non-empty str"
    raw, why = _read_file_bytes(path)
    if raw is None:
        return None, why
    return hashlib.sha256(raw).hexdigest(), None


def _c04b_canonical(value):
    """One spelling for every canonical object hashed here: sorted keys, no
    insignificant whitespace, ASCII, UTF-8 bytes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def _c04b_gate_files(root, check_cmd):
    """Every element of check_cmd naming an existing regular file UNDER
    root, as a path relative to root. Order kept, duplicates dropped. An
    element outside the tree (the interpreter, a flag, a shell word)
    contributes no keyed input."""
    root_abs = os.path.abspath(root)
    found = []
    for item in check_cmd:
        if not _c04b_text(item):
            continue
        candidate = item if os.path.isabs(item) else os.path.join(root, item)
        if not os.path.isfile(candidate):
            continue
        absolute = os.path.abspath(candidate)
        if absolute != root_abs and not absolute.startswith(root_abs + os.sep):
            continue
        relative = os.path.relpath(absolute, root_abs)
        if relative not in found:
            found.append(relative)
    return found


def _c04b_env_allow():
    """R-CACHE-01's env_allow: every BROTHER_ variable plus PATH, as a
    sorted list of {name, value}, so a changed seam state directory or a
    changed PATH is part of the key."""
    allow = [{"name": "PATH", "value": os.environ.get("PATH", "")}]
    for name in os.environ:
        if isinstance(name, str) and name.startswith("BROTHER_"):
            allow.append({"name": name, "value": os.environ[name]})
    return sorted(allow, key=lambda item: item["name"])


def verdict_key(root, check_name, check_cmd, policy_sha256):
    """R-CACHE-01: the sha256 of a canonical JSON object carrying the gate
    name, its argv, the sha256 of every file under root that the gate names,
    the code_hash over those digests plus scripts/required_fast.sh, this
    module's own source digest and the interpreter version, every BROTHER_
    variable plus PATH, the policy digest and the schema number.

    Wrongly typed arguments are refused with ValueError. A keyed file that is
    missing, a directory or unreadable is UNKNOWN input, not an empty one:
    VerdictKeyRefused, which cached_run_check reads as a MISS."""
    if not _c04b_text(root):
        raise ValueError("root must be a non-empty str")
    if not _c04b_text(check_name):
        raise ValueError("check_name must be a non-empty str")
    if not isinstance(check_cmd, list) or not check_cmd:
        raise ValueError("check_cmd must be a non-empty list of strings")
    for item in check_cmd:
        if not _c04b_text(item):
            raise ValueError("check_cmd must hold only non-empty strings")
    if policy_sha256 is not None and not _c04b_hex64(policy_sha256):
        raise ValueError("policy_sha256 must be a 64 hex digest or None")
    inputs = []
    for relative in _c04b_gate_files(root, check_cmd):
        digest, why = _c04b_file_sha256(os.path.join(root, relative))
        if digest is None:
            raise VerdictKeyRefused("keyed input %s is unknown: %s"
                                    % (relative, why))
        inputs.append({"path": relative, "sha256": digest})
    inputs.sort(key=lambda item: (item["path"], item["sha256"]))
    anchor, why = _c04b_file_sha256(os.path.join(root, "scripts",
                                                 "required_fast.sh"))
    if anchor is None:
        raise VerdictKeyRefused(
            "scripts/required_fast.sh, one of the keyed gate files, is "
            "unknown under this root: %s" % why)
    code_hash = hashlib.sha256(
        ("".join(item["sha256"] for item in inputs) + anchor)
        .encode("ascii")).hexdigest()
    script, why = _c04b_file_sha256(os.path.abspath(__file__))
    if script is None:
        raise VerdictKeyRefused("this module's own source is unknown: %s"
                                % why)
    canonical = {
        "schema": 1,
        "gate_name": check_name,
        "gate_argv": list(check_cmd),
        "inputs": inputs,
        "code_hash": code_hash,
        "tools": {"python": "%d.%d.%d" % sys.version_info[:3],
                  "script_sha256": script},
        "env_allow": _c04b_env_allow(),
        "policy_sha256": policy_sha256,
    }
    return hashlib.sha256(_c04b_canonical(canonical)).hexdigest()


def _c04b_entry_ok(entry, key=None):
    """One validation, and both cache_record and cache_lookup route through
    it: the exact key, a recorded PASS with exit_code 0, a finite non
    negative wall time, a receipt naming tree, check and time, and a
    recorded_at stamp. A bool where a number belongs, a NaN, an infinity or a
    mistyped field is not a green and never becomes one."""
    if not isinstance(entry, dict):
        return False
    if entry.get("schema_version") != CACHE_SCHEMA_VERSION:
        return False
    recorded_key = entry.get("key")
    if not _c04b_hex64(recorded_key):
        return False
    if key is not None and recorded_key != key:
        return False
    if entry.get("verdict") != CACHE_VERDICT_PASS:
        return False
    code = entry.get("exit_code")
    if isinstance(code, bool) or not isinstance(code, int) or code != 0:
        return False
    seconds = entry.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return False
    try:
        if not math.isfinite(seconds) or seconds < 0:
            return False
    except TypeError:
        return False
    receipt = entry.get("receipt_id")
    if not _c04b_text(receipt):
        return False
    parts = receipt.split(":", 2)
    if (len(parts) != 3 or not _C04B_HEX40_RE.match(parts[0])
            or not parts[1] or not parts[2]):
        return False
    if not _c04b_text(entry.get("recorded_at")):
        return False
    return True


def cache_lookup(store, key):
    """The recorded CacheEntry for key, or None. None is the refusal value
    and it is also the safe direction: no entry means the check runs."""
    if not _c04b_text(store):
        return None
    if not _c04b_hex64(key):
        return None
    directory = os.path.expanduser(store)
    path = os.path.join(directory, key + ".json")
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(directory):
        return None
    raw, _why = _read_file_bytes(path)
    if raw is None:
        return None
    try:
        entry = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not _c04b_entry_ok(entry, key):
        return None
    return entry


def cache_record(store, key, entry):
    """Write one CacheEntry under <store>/<key>.json, creating the store if
    it does not exist. A record that is not a strict green is refused with
    ValueError and nothing is written: only verdict PASS with exit_code 0 is
    ever stored, so a prior NO-DATA can never serve as green proof."""
    if not _c04b_text(store):
        raise ValueError("store must be a non-empty str")
    if not _c04b_hex64(key):
        raise ValueError("key must be a 64 hex digest")
    if not _c04b_entry_ok(entry, key):
        raise ValueError("entry is not a green CacheEntry for this key")
    directory = os.path.expanduser(store)
    path = os.path.join(directory, key + ".json")
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(directory):
        raise ValueError("refusing to write outside the store")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(entry, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _c04b_ledger_path(root):
    """R-LEDGER-03: GATE_LEDGER_PATH wins, else the tree local ledger. The
    gate ledger itself is owned by C0.3; this only ever READS it."""
    override = os.environ.get("GATE_LEDGER_PATH")
    if _c04b_text(override):
        return override
    return os.path.join(root, "logs", "gate-ledger.jsonl")


def _c04b_ledger_records(path):
    """(records, why). Every JSON object line in the gate ledger at path,
    read as BYTES. A ledger that is missing, unreadable, not UTF-8, or
    carries a line that is not a JSON object is UNKNOWN evidence, and unknown
    evidence never clears a gate."""
    raw, why = _read_file_bytes(path)
    if raw is None:
        return None, "gate ledger %s could not be read: %s" % (path, why)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "gate ledger %s is not UTF-8: %s" % (path, exc)
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            return None, ("gate ledger %s line %d is not JSON: %s"
                          % (path, number, exc))
        if not isinstance(record, dict):
            return None, ("gate ledger %s line %d is not a JSON object"
                          % (path, number))
        records.append(record)
    return records, ""


def _c04b_flake(records, gate_name):
    """R-STAT-03's flake rate for one gate: group that gate's runs by the
    pair (code_hash, inputs_hash), count every group with two or more runs as
    repeated and every such group whose runs reached more than one status as
    mixed, and return mixed/repeated. Below n>=20 runs or repeated>=5 the rate
    is not measurable and is 1.0, which blocks the cache for that gate. Fail
    closed, never the safe case."""
    runs = 0
    groups = {}
    for record in records:
        if record.get("gate_name") != gate_name:
            continue
        runs += 1
        inputs = record.get("inputs_hash")
        if not _c04b_hex64(inputs):
            inputs = None
        code = record.get("code_hash")
        if not _c04b_hex64(code):
            code = None
        status = record.get("status")
        if status not in ("PASS", "FAIL", "NO-DATA"):
            status = None
        group = groups.setdefault((code, inputs), [0, set()])
        group[0] += 1
        if status is not None:
            group[1].add(status)
    repeated = sum(1 for group in groups.values() if group[0] >= 2)
    mixed = sum(1 for group in groups.values()
                if group[0] >= 2 and len(group[1]) > 1)
    if runs < FLAKE_MIN_RUNS or repeated < FLAKE_MIN_REPEATED:
        return 1.0
    return mixed / float(repeated)


def _c04b_flake_blocks(root, gate_name):
    """True when the flake gate does NOT clear for this gate. A ledger that
    cannot be read blocks exactly like a measured flake."""
    records, _why = _c04b_ledger_records(_c04b_ledger_path(root))
    if records is None:
        return True
    return _c04b_flake(records, gate_name) >= FLAKE_MAX


def _c04b_clock_lapsed():
    """True when a hit may not be taken on clock grounds (R-STAT-06: the
    deadline is not in the key and is re evaluated here). No deadline on
    record is UNKNOWN, and UNKNOWN blocks a hit: the caller declares its
    budget through CUT_DEADLINE_ENV, and the refusal that follows belongs to
    the clock gate in C0.2."""
    raw = os.environ.get(CUT_DEADLINE_ENV)
    if not _c04b_text(raw):
        return True
    try:
        deadline = float(raw)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(deadline):
        return True
    return time.time() >= deadline


def _c04b_tree_sha(root, runner=None):
    """The 40 hex commit this tree stands at, or None. None is unknown, and
    unknown never becomes a cache receipt."""
    proc = _run(["git", "rev-parse", "HEAD"], root, runner)
    sha = (proc.stdout or "").strip()
    if proc.returncode != 0 or not _C04B_HEX40_RE.match(sha):
        return None
    return sha


def _c04b_record_green(root, name, key, store, runner, seconds):
    """Best effort: a store that cannot be written must never change the
    check's own verdict, so only OSError and ValueError are swallowed here and
    they change nothing about what the check returned. An unknown tree sha is
    not written at all: a receipt that cannot name the tree proves nothing."""
    tree_sha = _c04b_tree_sha(root, runner)
    if tree_sha is None:
        return
    recorded_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        cache_record(store, key, {
            "schema_version": CACHE_SCHEMA_VERSION,
            "key": key,
            "verdict": CACHE_VERDICT_PASS,
            "exit_code": 0,
            "seconds": seconds,
            "receipt_id": "%s:%s:%s" % (tree_sha, name, recorded_at),
            "recorded_at": recorded_at,
        })
    except (OSError, ValueError):
        # A cache that cannot be written is a slower cut, never a wrong
        # verdict: the check's own exit code is returned unchanged.
        return


def cached_run_check(root, name, cmd, store, policy_sha256, runner=None,
                     heartbeat=None):
    """(exit_code, note). The note is None when the check itself ran, and a
    one line citation when the store answered instead.

    A hit needs all of: a content key that could be computed at all, an exact
    key match in the store with a recorded green, the flake gate clear
    (R-STAT-03: below its minima the gate reads 1.0 and blocks), and the clock
    gate clear (R-STAT-06). Every other path RUNS the check, and a run that
    exits 0 records its own green for this exact key."""
    if not _c04b_text(root):
        raise ValueError("root must be a non-empty str")
    if not _c04b_text(name):
        raise ValueError("name must be a non-empty str")
    if not isinstance(cmd, list) or not cmd:
        raise ValueError("cmd must be a non-empty list of strings")
    for item in cmd:
        if not _c04b_text(item):
            raise ValueError("cmd must hold only non-empty strings")
    if not _c04b_text(store):
        raise ValueError("store must be a non-empty str")
    if policy_sha256 is not None and not _c04b_hex64(policy_sha256):
        raise ValueError("policy_sha256 must be a 64 hex digest or None")
    if runner is not None and not callable(runner):
        raise ValueError("runner must be callable or None")
    if heartbeat is not None and not callable(getattr(heartbeat, "maybe",
                                                      None)):
        raise ValueError("heartbeat must expose maybe(label, force=False) "
                         "or be None")
    try:
        key = verdict_key(root, name, cmd, policy_sha256)
    except VerdictKeyRefused:
        key = None
    if key is not None:
        entry = cache_lookup(store, key)
        if (entry is not None and not _c04b_clock_lapsed()
                and not _c04b_flake_blocks(root, name)):
            return 0, "HIT %s receipt %s" % (key, entry["receipt_id"])
    if heartbeat is not None:
        heartbeat.maybe("check %s" % name, force=True)
    started = time.monotonic()
    proc = _run(cmd, root, runner)
    elapsed = time.monotonic() - started
    if elapsed < 0:
        elapsed = 0.0
    if proc.returncode == 0 and key is not None:
        _c04b_record_green(root, name, key, store, runner, elapsed)
    return proc.returncode, None


def _c04b_suite_command(suite):
    """The one command that measures a suite: a name ending in .py is a
    script this interpreter runs directly, anything else is a dotted unittest
    module path, the two shapes this estate's own done checks already use."""
    if suite.endswith(".py"):
        return [sys.executable, "-B", suite]
    return [sys.executable, "-B", "-m", "unittest", suite]


def note_suite_step(root, version, report, runner=None, heartbeat=None):
    """(ok, lines). `report` is exactly the pair
    release_note_from_tree.measured_file_rows(claim_suites, perturb=None)
    returns, (rows, problem), where every row is (claim, source_file,
    suite_or_None). Every DISTINCT suite is measured ONCE and that one result
    is fanned back to every row that named it, so the lines carry one line per
    input row in the input order and a suite is never measured twice.

    Anything else is NO-DATA (ok False) and never a skip: a report that is not
    that pair, a problem string, a row that is not a three item row, a row
    that names no suite, or no distinct suite at all is reported, never
    dropped."""
    if not _c04b_text(root):
        raise ValueError("root must be a non-empty str")
    if not _c04b_text(version):
        raise ValueError("version must be a non-empty str")
    if runner is not None and not callable(runner):
        raise ValueError("runner must be callable or None")
    if heartbeat is not None and not callable(getattr(heartbeat, "maybe",
                                                      None)):
        raise ValueError("heartbeat must expose maybe(label, force=False) "
                         "or be None")
    if not (isinstance(report, (tuple, list)) and len(report) == 2):
        return False, ["NO-DATA: the release note report is not the "
                       "(rows, problem) pair measured_file_rows returns "
                       "(got %s); nothing is measured and nothing is "
                       "skipped" % type(report).__name__]
    rows, problem = report
    if problem:
        return False, ["NO-DATA: measured_file_rows reported a problem: %s"
                       % problem]
    if not isinstance(rows, (list, tuple)) or not rows:
        return False, ["NO-DATA: measured_file_rows returned no rows; an "
                       "empty measurement is NO-DATA, never a skip"]
    normalized = []
    for index, row in enumerate(rows):
        if not (isinstance(row, (list, tuple)) and len(row) == 3):
            return False, ["NO-DATA: row %d is not a (claim, source_file, "
                           "suite) triple" % index]
        claim, source_file, suite = row
        if not _c04b_text(claim):
            return False, ["NO-DATA: row %d names no claim" % index]
        if suite is not None and not _c04b_text(suite):
            return False, ["NO-DATA: row %d names a suite that is neither "
                           "None nor a name" % index]
        normalized.append((claim, source_file, suite))
    suites = []
    for _claim, _source, suite in normalized:
        if suite is not None and suite not in suites:
            suites.append(suite)
    if not suites:
        return False, ["NO-DATA: no row names a suite; an empty distinct set "
                       "is NO-DATA, never a skip"]
    verdicts = {}
    lines = []
    for suite in suites:
        proc = _stream("suite %s" % suite, _c04b_suite_command(suite), root,
                       runner, heartbeat=heartbeat)
        if proc.returncode == 0:
            verdicts[suite] = "PASS"
        elif proc.returncode == 2:
            verdicts[suite] = "NO-DATA"
        else:
            verdicts[suite] = "FAIL"
        lines.append("suite %s: exit %d (%s), measured once for %d row(s)"
                     % (suite, proc.returncode, verdicts[suite],
                        sum(1 for row in normalized if row[2] == suite)))
    ok = all(status == "PASS" for status in verdicts.values())
    for index, (claim, source_file, suite) in enumerate(normalized):
        verdict = "NO-DATA" if suite is None else verdicts[suite]
        lines.append("row %d: %s -> %s -> %s"
                     % (index, claim,
                        source_file if _c04b_text(source_file) else "?",
                        verdict))
    return ok, lines


def _c04b_release_digests(root):
    """(digests, why). {relative path: sha256} for every regular file under
    every path release_paths(root) names. A path this tree does not carry is
    skipped, not an error; a file that exists but cannot be read is UNKNOWN
    and (None, why), never an empty digest."""
    digests = {}
    for entry in release_paths(root):
        base = os.path.join(root, entry)
        if os.path.isfile(base):
            candidates = [base]
        elif os.path.isdir(base):
            candidates = []
            for dirpath, _dirnames, filenames in os.walk(base):
                for name in sorted(filenames):
                    candidates.append(os.path.join(dirpath, name))
        else:
            continue
        for path in candidates:
            digest, why = _c04b_file_sha256(path)
            if digest is None:
                return None, ("release path %s could not be rehashed (%s)"
                              % (os.path.relpath(path, root), why))
            digests[os.path.relpath(path, root)] = digest
    return digests, ""


def resume_guard(root, resume_sha, notes_sha256):
    """(ok, why). True only when skipping cut_v1.0.0.sh is provably safe.

    Every check is fail closed and any mismatch means a full restart: HEAD
    must be exactly resume_sha and git status must be clean; every file under
    every path release_paths() names must rehash from its bytes right now, and
    a path that exists but cannot be read is UNKNOWN, which refuses; and when
    notes_sha256 is given, those rehashed bytes must still carry it, so the
    notes the earlier stage scanned and hashed are still on disk unchanged.

    Wrongly typed input is refused here rather than raised: the caller's only
    safe reaction to a bad value is the same as its reaction to a mismatch, a
    full restart. Nothing is written and no fence is touched."""
    if not _c04b_text(root):
        return False, ("REFUSED: root must be a non-empty str, full restart"
                       )
    if not isinstance(resume_sha, str) or not _C04B_HEX40_RE.match(
            resume_sha):
        return False, ("REFUSED: resume_sha must be the full 40 hex commit "
                       "the checked cut left at HEAD, full restart")
    if notes_sha256 is not None and not _c04b_hex64(notes_sha256):
        return False, ("REFUSED: notes_sha256 must be a 64 hex digest or "
                       "None, full restart")
    proc = _run(["git", "rev-parse", "HEAD"], root, None)
    head = (proc.stdout or "").strip()
    if proc.returncode != 0 or not _C04B_HEX40_RE.match(head):
        return False, ("REFUSED: HEAD could not be read (%s), full restart"
                       % (head or "exit %d" % proc.returncode))
    if head != resume_sha:
        return False, ("REFUSED: HEAD is %s, not the checked commit %s, "
                       "full restart" % (head, resume_sha))
    dirty, why = dirty_paths(root)
    if dirty is None:
        return False, ("REFUSED: git status could not be read (%s), full "
                       "restart" % why)
    if dirty:
        return False, ("REFUSED: the tree is dirty, %d path(s) changed "
                       "(first: %s), full restart"
                       % (len(dirty), dirty[0]))
    digests, why = _c04b_release_digests(root)
    if digests is None:
        return False, ("REFUSED: %s, full restart" % why)
    if notes_sha256 is not None and notes_sha256 not in set(digests.values()):
        return False, ("REFUSED: the notes bytes hashed %s are no longer "
                       "present unchanged under the release paths, full "
                       "restart" % notes_sha256)
    return True, ("resume allowed: HEAD %s, clean tree, %d release path(s) "
                  "rehashed unchanged" % (resume_sha, len(digests)))


# --- C0.5: shadow equivalence and the one flag back ---------------------
#
# The fast path is never promoted on hope. shadow_run() runs
# required_fast.sh twice on the SAME tree, once with the order pin and once
# without, both under full chain discipline (no cache read, no dedupe:
# neither exists in this module), and records whether the two runs reached
# the same verdict on every check. shadow_gate() answers whether the last N
# written records were all equal. Unknown, corrupt or missing input REFUSES
# everywhere: shadow_run's refusal value is a record whose "equal" is False,
# shadow_gate's is False, and neither ever reports agreement it cannot show.

SHADOW_SCHEMA_VERSION = "c0.shadow.1"
SHADOW_RECORD_PREFIX = "C0-shadow-"
SHADOW_RECORD_SUFFIX = ".json"
SHADOW_STATUSES = ("PASS", "FAIL", "NO-DATA")
#: A version is one filename component here, so it is validated as one:
#: no separator, no "..", no leading dot, nothing absolute, never empty.
SHADOW_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
SHADOW_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
SHADOW_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _shadow_version_ok(value):
    """True only for a plain version string that is safe as one path
    component: a non empty str with no separator, no "..", no leading dot."""
    return isinstance(value, str) and bool(SHADOW_VERSION_RE.match(value))


def _shadow_seconds_ok(value):
    """True only for a measured wall time: a real number, not a bool, not
    NaN, not infinite, not negative."""
    return (not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0)


def _shadow_status_map(value):
    """True only for a non empty {check name: verdict} map whose every
    verdict is one this estate uses (PASS, FAIL, NO-DATA)."""
    if not isinstance(value, dict) or not value:
        return False
    for name, status in value.items():
        if not isinstance(name, str) or not name:
            return False
        if status not in SHADOW_STATUSES:
            return False
    return True


def _shadow_verdicts(text):
    """{check name: [status, exit code]} for one required_fast.sh run,
    parsed ONLY with scripts/gate_order.py's own RESULT_RE (status, exit N,
    name, seconds, summary), never a second, independently typed pattern.

    Every matched line must carry the whole shape INCLUDING its summary: a
    line without one is corrupt input, and this raises rather than quietly
    dropping a check out of the comparison. Raises ValueError when the
    parser itself is unavailable: a control with nothing to compare against
    must never report agreement."""
    if GO is None or getattr(GO, "RESULT_RE", None) is None:
        raise ValueError("gate_order.RESULT_RE is unavailable, so no gate "
                         "line can be parsed and no two runs can be compared")
    out = {}
    for match in GO.RESULT_RE.finditer(text or ""):
        groups = match.groups()
        if len(groups) < 5 or any(g is None for g in groups[:5]):
            raise ValueError("gate line without the full status/exit/name/"
                             "seconds/summary shape: %r" % (match.group(0),))
        status, exit_text, name, _seconds, summary = groups[:5]
        if status not in SHADOW_STATUSES:
            raise ValueError("gate line with unknown status %r: %r"
                             % (status, match.group(0)))
        if not str(summary).strip():
            raise ValueError("gate line for %r carries no summary: %r"
                             % (name, match.group(0)))
        try:
            exit_code = int(exit_text)
        except (TypeError, ValueError):
            raise ValueError("gate line for %r carries no integer exit "
                             "code: %r" % (name, match.group(0)))
        out[str(name)] = [status, exit_code]
    return out


def shadow_run(root, version, pin_path=None, runner=None):
    """Run required_fast.sh twice on this tree, once with the order pin and
    once without, and return the ShadowRecord for the pair.

    Arguments that are not what this function needs REFUSE here (ValueError)
    before any subprocess runs and before any filename is built. The two
    runs differ by exactly one environment variable, both run the whole
    gate, and `equal` is True only when both printed the same non empty per
    check map AND exited with the same code on the same 40 hex tree. A tree
    sha that cannot be read, a pin that cannot be read, or output that
    cannot be parsed is NOT equal: unknown input never becomes a pass."""
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty str")
    if not _shadow_version_ok(version):
        raise ValueError("version must be a plain version string with no "
                         "path separator and no '..': %r" % (version,))
    if pin_path is not None and not (isinstance(pin_path, str) and pin_path):
        raise ValueError("pin_path must be a non-empty str or None")
    if runner is not None and not callable(runner):
        raise ValueError("runner must be callable or None")
    head = _run(["git", "rev-parse", "HEAD"], root, runner)
    tree_sha = (head.stdout or "").strip()
    if head.returncode != 0 or not SHADOW_HEX40_RE.match(tree_sha):
        tree_sha = "UNKNOWN"
    pin_sha256 = hashlib.sha256(b"").hexdigest()
    pin_error = None
    if pin_path is not None:
        raw, why = _read_file_bytes(pin_path)
        if raw is None:
            pin_error = "pin file %s could not be read (%s)" % (pin_path,
                                                                 why)
        else:
            pin_sha256 = hashlib.sha256(raw).hexdigest()
    runs = []
    for label, with_pin in (("fast", True), ("full", False)):
        env = None
        if with_pin and pin_path:
            env = dict(os.environ)
            env["REQUIRED_FAST_ORDER_PIN"] = pin_path
        elif "REQUIRED_FAST_ORDER_PIN" in os.environ:
            env = dict(os.environ)
            env.pop("REQUIRED_FAST_ORDER_PIN", None)
        started = time.monotonic()
        proc = _run(["sh", os.path.join(root, "scripts", "required_fast.sh")],
                    root, runner, env=env)
        elapsed = time.monotonic() - started
        try:
            verdicts = _shadow_verdicts((proc.stdout or "") +
                                        (proc.stderr or ""))
            parse_error = None
        except ValueError as exc:
            verdicts = {}
            parse_error = "%s" % exc
        runs.append({"label": label, "returncode": proc.returncode,
                     "seconds": elapsed, "verdicts": verdicts,
                     "parse_error": parse_error})
    fast, full = runs
    equal = bool(tree_sha != "UNKNOWN"
                 and pin_error is None
                 and fast["parse_error"] is None
                 and full["parse_error"] is None
                 and fast["verdicts"] and full["verdicts"]
                 and fast["verdicts"] == full["verdicts"]
                 and fast["returncode"] == full["returncode"])
    record = {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "version": version,
        "tree_sha": tree_sha,
        "fast_verdicts": dict((k, v[0]) for k, v in fast["verdicts"].items()),
        "full_verdicts": dict((k, v[0]) for k, v in full["verdicts"].items()),
        "equal": equal,
        "fast_seconds": float(fast["seconds"]),
        "full_seconds": float(full["seconds"]),
        "pin_sha256": pin_sha256,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not equal:
        why = []
        if tree_sha == "UNKNOWN":
            why.append("tree sha unreadable")
        if pin_error:
            why.append(pin_error)
        for label, run in (("fast", fast), ("full", full)):
            if run["parse_error"]:
                why.append("%s run: %s" % (label, run["parse_error"]))
        if not (fast["verdicts"] and full["verdicts"]) and not why:
            why.append("no per check line parsed")
        if fast["returncode"] != full["returncode"]:
            why.append("exit %d against exit %d"
                       % (fast["returncode"], full["returncode"]))
        print("shadow %s: NOT equal (%s)"
              % (version, "; ".join(why) or "verdict maps differ"))
    out_dir = os.path.join(root, "docs", "plan", "specs")
    out_path = os.path.join(out_dir, "%s%s%s"
                            % (SHADOW_RECORD_PREFIX, version,
                               SHADOW_RECORD_SUFFIX))
    if os.path.dirname(os.path.abspath(out_path)) != os.path.abspath(out_dir):
        record["equal"] = False
        print("shadow: refused to write outside %s" % out_dir)
        return record
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        record["equal"] = False
        print("shadow: could not write %s (%s); the record is not on disk "
              "and the gate can never credit it" % (out_path, exc))
    return record


def _shadow_record_ok(data):
    """True only for a ShadowRecord that can vouch for itself: every field
    the c0.shadow.1 contract names, of the right type and shape, and an
    `equal` its own two maps actually support. Everything else is corrupt
    input, and corrupt input never approves the fast path."""
    if not isinstance(data, dict):
        return False
    if data.get("schema_version") != SHADOW_SCHEMA_VERSION:
        return False
    if not _shadow_version_ok(data.get("version")):
        return False
    tree_sha = data.get("tree_sha")
    if not isinstance(tree_sha, str) or not SHADOW_HEX40_RE.match(tree_sha):
        return False
    pin_sha256 = data.get("pin_sha256")
    if not isinstance(pin_sha256, str) \
            or not SHADOW_HEX64_RE.match(pin_sha256):
        return False
    for field in ("fast_verdicts", "full_verdicts"):
        if not _shadow_status_map(data.get(field)):
            return False
    equal = data.get("equal")
    if not isinstance(equal, bool):
        return False
    if equal and data["fast_verdicts"] != data["full_verdicts"]:
        return False
    for field in ("fast_seconds", "full_seconds"):
        if not _shadow_seconds_ok(data.get(field)):
            return False
    recorded_at = data.get("recorded_at")
    if not isinstance(recorded_at, str) or not recorded_at.strip():
        return False
    return True


def shadow_gate(records_dir, min_consecutive=2):
    """True only when the last `min_consecutive` ShadowRecords under
    `records_dir` are each well formed and each `equal` True.

    Fail closed everywhere: a missing directory, an unreadable or non UTF-8
    file, a directory where a record should be, a record with a missing,
    mistyped or impossible field (a negative, bool, string, NaN or infinite
    wall time, a short or non hex digest, a verdict map that is not a non
    empty map of PASS/FAIL/NO-DATA, an `equal` its own maps contradict, a
    duplicate version whose tree or pin digest disagrees with the same
    version's other record) all return False, never an exception and never
    a True. Two records for one version that disagree about the tree or the
    pin are counts that cannot all be true: corrupt input, refused."""
    if isinstance(min_consecutive, bool) \
            or not isinstance(min_consecutive, int) or min_consecutive < 1:
        return False
    if not isinstance(records_dir, str) or not records_dir:
        return False
    if not os.path.isdir(records_dir):
        return False
    try:
        names = sorted(os.listdir(records_dir))
    except OSError:
        return False
    records = []
    for name in names:
        if not (name.startswith(SHADOW_RECORD_PREFIX)
                and name.endswith(SHADOW_RECORD_SUFFIX)):
            continue
        raw, _why = _read_file_bytes(os.path.join(records_dir, name))
        if raw is None:
            return False
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        if not _shadow_record_ok(data):
            return False
        records.append(data)
    if len(records) < min_consecutive:
        return False
    seen = {}
    for data in records:
        pair = (data["tree_sha"], data["pin_sha256"])
        if seen.setdefault(data["version"], pair) != pair:
            return False
    ordered = sorted(records, key=lambda item: item["recorded_at"])
    window = ordered[-min_consecutive:]
    return all(item["equal"] for item in window)


def main(argv=None, root=None, runner=None, ask=None, terms_file=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default=None,
                     help="the release to cut (default: the 'next cut "
                          "version' line scripts/next_cut.py --version-only "
                          "prints); a real cut accepts an explicit one only "
                          "when it equals what next_cut.derive_next "
                          "answers")
    ap.add_argument("--check", action="store_true",
                     help="readiness only: scan fences, read the tree, run "
                          "the gates; claim nothing, write nothing, never "
                          "prompt")
    ap.add_argument("--yes", action="store_true",
                     help="skip the approve prompt before the push; refused "
                          "unless a person is at a terminal (standard input "
                          "is one), so an accidental run from a shell "
                          "without one cannot skip the question")
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
                     help="REFUSED for a real cut (ruling 2026-10-03): its "
                          "one answer is typed on the owner's terminal; "
                          "accepted and unused with --check or --shadow, "
                          "which push nothing and never ask")
    ap.add_argument("--release-notes-file", metavar="PATH", default=None,
                     help="R4: the body file for `gh release create "
                          "--notes-file`, run only after a successful push. "
                          "REQUIRED for a real cut unless --no-release-page "
                          "says why not")
    ap.add_argument("--no-release-page", metavar="REASON", default=None,
                     help="cut and tag WITHOUT publishing a GitHub Release "
                          "page, on purpose, for the stated reason")
    ap.add_argument("--shadow", action="store_true",
                     help="C0.5: run required_fast.sh twice on this tree, "
                          "once with the order pin and once without, write "
                          "the ShadowRecord under docs/plan/specs/, and exit")
    ap.add_argument("--fast", action="store_true",
                     help="request the fast cut path; refused until the "
                          "shadow gate says the last records were all equal")
    ap.add_argument("--full-chain", action="store_true",
                     help="run the full serial chain, every check once, no "
                          "cache and no dedupe")
    ap.add_argument("--allow-unsafe", action="store_true",
                     help="with --fast, allow the fast path before the "
                          "shadow gate is true; requires --reason")
    ap.add_argument("--reason", metavar="REASON", default=None,
                     help="one-line reason for --allow-unsafe")
    if argv is None:
        argv_list = list(sys.argv[1:])
    elif isinstance(argv, (list, tuple)):
        argv_list = [a for a in argv]
    else:
        print("REFUSED: argv must be a list of argument strings, not %s"
              % type(argv).__name__)
        return EXIT_REFUSED
    try:
        args = ap.parse_args(argv_list)
    except SystemExit as exc:
        # argparse's own deliberate exit (--help, a bad flag) becomes this
        # module's return value, the way every other path here returns a
        # code: the printed text and the exit code are unchanged, and no
        # caller sees a raw interpreter exception.
        return exc.code if isinstance(exc.code, int) else EXIT_NODATA
    # CV1.e: first, before any step: --yes needs a person at a terminal.
    refusal = yes_refusal(args.yes, _stdin_is_tty())
    if refusal:
        print(refusal)
        return EXIT_NODATA
    root = root or ROOT
    if args.shadow:
        version = args.version
        if not version:
            version, lines = read_next_version(root, runner)
            _print(lines)
            if not version:
                return EXIT_NODATA
        pin_path = os.environ.get("REQUIRED_FAST_ORDER_PIN") or None
        try:
            record = shadow_run(root, version, pin_path, runner)
        except ValueError as exc:
            print("REFUSED: --shadow input refused: %s" % exc)
            return EXIT_REFUSED
        print("shadow: equal=%s fast_seconds=%.3f full_seconds=%.3f"
              % (record["equal"], record["fast_seconds"],
                 record["full_seconds"]))
        return EXIT_OK if record["equal"] else EXIT_REFUSED
    # CV1.e: a real cut takes no answer file and reads its one answer from
    # the owner's terminal; --check (the dry run) pushes nothing and never
    # asks, so the rehearsal's own --check without a terminal still runs.
    refusal = answer_file_refusal(args.answer_file, args.check,
                                  _stdin_is_tty())
    if refusal:
        print(refusal)
        return EXIT_NODATA
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
    if not notes_file and not args.check and not (args.no_release_page
                                                  or "").strip():
        # Measured 2026-09-20 on the 1.0.21 cut: started with no release
        # notes file, it tagged the public repository and published no
        # Release page. The owner opened the releases page, saw the
        # previous version still marked latest and a tag with no
        # documentation, and nothing had refused. The only mention was a
        # clause inside the approve question, two hours in. Knowable at
        # second one, so refused at second one. (This flag's old help text
        # promised a default body that the code never applied.)
        print("REFUSED: no --release-notes-file was given, so this cut "
              "would push tag v<version> and publish NO GitHub Release "
              "page: the releases page keeps showing the previous version "
              "as latest. Give --release-notes-file PATH (scanned here, "
              "published after the push), or say on purpose "
              "--no-release-page \"reason\".")
        return EXIT_REFUSED
    if args.no_release_page and notes_file:
        print("NO-DATA: --no-release-page and --release-notes-file "
              "contradict each other; pass one")
        return EXIT_NODATA
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
    if args.fast and not args.full_chain:
        if not shadow_gate(os.path.join(root, "docs", "plan", "specs")):
            if not args.allow_unsafe or not (args.reason or "").strip():
                print("REFUSED: --fast needs a true shadow gate (the last "
                      "records under docs/plan/specs/ all equal True) or "
                      "--allow-unsafe plus a one-line --reason; the full "
                      "serial chain is what runs by default, pass "
                      "--full-chain to say so")
                return EXIT_REFUSED
            print("--fast allowed before the shadow gate is true, reason: %s"
                  % args.reason.strip())
        else:
            print("--fast: the shadow gate is true")
    elif args.fast:
        print("--full-chain and --fast: the full serial chain runs, no "
              "check is skipped")
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
