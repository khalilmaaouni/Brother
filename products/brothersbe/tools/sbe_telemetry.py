#!/usr/bin/env python3
"""BrotherSBE telemetry: the mechanical half of the learning loop. Schema 2.

Subcommands:
  outcomes-append   SessionEnd hook target. Reads hook JSON on stdin, parses the
                    session transcript, appends ONE atomic JSONL line to the ledger
                    (schema 2: GenAI semconv token names) and scans the MAIN
                    transcript's short operator messages for correction candidates.
  migrate           One-time ledger migration to schema 2 (idempotent, temp+rename,
                    line-count recheck; never backfills absent values).
  scorecard         Renders the 9-metric dashboard from the ledger + companion files.
  rate              Records a operator felt-outcome rating (1-5).
  review-mark       Records that a weekly review ran (feeds the overdue nag).
  startup-nags      One-line nags + yesterday's spend for SessionStart injection.
  stop-warn         Stop hook target, WARN ONLY (never blocks): systemMessage JSON
                    when a qualifying session has no vault session log today.
                    Cheap short-circuits only; never parses the transcript.
  registry-check    Flags fence-registry lines that look live in a stale file.
  fence-lint        Dispatch aid: prints live fences from the project's STATE.md
                    and the BROTHERSBE_REGISTRIES globs before a writer launches.
  prediction-audit  Counts sealed predictions in the operator model ledger.
  check-update      OFFLINE check of your installed copy: warns when an already
                    fetched update is waiting, when the copy has gone stale, and
                    once when the law itself changed under you. Reads git refs as
                    plain files: no network call, no subprocess, ever.
  intent            Write-ahead intent log. `intent "next: X, because Y"` appends
                    one line to a per-project log BEFORE a risky/long action, so a
                    death always leaves a forward-looking record (the write-ahead
                    log pattern: log the intent before the action, like a database).
                    The resume brief surfaces the last intent first.
  precompact-brief  PreCompact hook helper. Category `transcript`, opt-in like
                    every other category (BROTHERSBE_TELEMETRY_TRANSCRIPT=1): off
                    (the default) writes NOTHING and names the switch once on
                    stderr; on, it reads the TAIL of the dying session's transcript
                    (last instruction, recent decisions, recent commands) and
                    writes a resume brief to the vault, so a resumed session
                    recovers the THREAD, not just the files the git autosave saved.
                    Pure: reads/writes files only.
  compact-hint      SessionStart(source=compact) helper. Reads the hook JSON on
                    stdin; if the session just resumed from a compaction, prints a
                    one-line pointer to the autosave recovery command. Pure: reads
                    stdin and prints, no subprocess, no network.
  handoff           One shareable, SECRET-REDACTED markdown for handing a project
                    to a small team: overview + open items + latest session + recent
                    outcomes, assembled from the vault. Pure: reads vault files,
                    redacts, writes one file. No subprocess, no network.
  purge-corrections Deletes captured correction candidates (excerpts of your own
                    messages, secret-redacted and owner-only where the platform
                    enforces that mode, but still yours to delete). Shows the
                    count first; --yes to confirm.
  data-show         Every file this tool can write, by name: what it holds, how
                    many records are in it, its mode, and which capture switch
                    governs it. Reads; never writes. -h/--help anywhere in its
                    argv prints usage and exits 0 instead of running.
  data-export       One JSON bundle of everything stored, owner-only where the
                    platform enforces that mode, so the stored data can be read
                    somewhere other than the vault. -h/--help anywhere in its
                    argv prints usage and exits 0 without touching the vault or
                    writing a bundle.
  data-purge        Deletes what is stored. Shows the inventory first; --yes to
                    confirm; re-checks the filesystem after and reports what
                    survived. --category narrows it to one category. -h/--help
                    anywhere in its argv prints usage and exits 0 without
                    deleting anything.
  speed             Wall-clock view for rubric metric 3: last 7d vs prior 7d,
                    span-hours per OUTCOMES line (labeled proxy, never invented).
  dedup             One-time cleanup of duplicate hook flushes (backup + count
                    check; keeps last flush per identical metric set).

Law: this tool NEVER blocks work. Every hook and automatic path exits 0. The
three data-* commands are the one deliberate exception: a bad flag on one of
them refuses with usage and a nonzero exit rather than run with the flag
silently ignored, because a mistyped flag on a command that reads or deletes
the vault must not be allowed to run as if nothing were wrong. Numbers are
parsed or absent; nothing is invented. Token counts are labeled as-flushed (the
transcript may lag the final turn). Old (schema 1) and new lines are both
readable: use fld() everywhere.
"""
import json, os, sys, glob, re, stat, datetime, hashlib, time, contextlib
try:
    import fcntl
except ImportError:          # every non-POSIX platform (Windows: use msvcrt below)
    fcntl = None
try:
    import msvcrt
except ImportError:          # every POSIX platform (fcntl above covers it instead)
    msvcrt = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import (derivation_fold, evidence_problem, without_comments,
                        answered, glob_with_denials)

# ---------------------------------------------------------------------------
# Configuration. The vault is the durable memory folder every ledger lives in.
# Point BROTHERSBE_VAULT at it. Layout, created on demand by atomic_append:
#   <vault>/99-System/telemetry/outcomes.jsonl     one line per real session
#   <vault>/99-System/telemetry/ratings.jsonl      operator felt-outcome scores
#   <vault>/99-System/telemetry/reviews.jsonl      weekly review markers
#   <vault>/99-System/telemetry/corrections.jsonl  correction candidates
#   <vault>/50-Reference/operator-model.md          prediction ledger (optional)
#   <vault>/10-Projects/<name>/Sessions/*.md       human session logs
#
# NO_VAULT_SENTINEL, not the operator's real home directory. This used to
# default to os.path.expanduser("~/BrotherSBEVault") when BROTHERSBE_VAULT
# was unset, so every reader of VAULT (this module and tools/sbe_score.py,
# which imports it directly) reached into the operator's ACTUAL $HOME by
# default, stat-ing and glob-ing a real, absolute path outside whatever
# repository a repo-scoped `sbe verify`/`sbe review` was actually checking.
# `sbe doctor`'s own line already told the truth ("BROTHERSBE_VAULT is
# unset, so telemetry, session logs and resume briefs have nowhere durable
# to go"); the default here contradicted it. The sentinel is a path no real
# vault can ever be (no home directory, no relative component, an all-caps
# marker segment under the filesystem root), so every os.path.exists,
# os.path.isfile and glob.glob call against a path built from it fails fast
# on a single absent lookup and never touches a file the operator did not
# name.
# ---------------------------------------------------------------------------
NO_VAULT_SENTINEL = os.path.join(os.sep, "BROTHERSBE-NO-VAULT-CONFIGURED")

#: True exactly when BROTHERSBE_VAULT names a non-empty value in the
#: environment this process saw at import time. Readers that want to know
#: "is a vault configured at all" ask this, rather than comparing VAULT to
#: NO_VAULT_SENTINEL by string equality, which a deliberately-named real
#: vault could in principle collide with. An explicitly empty
#: BROTHERSBE_VAULT ("") is treated the same as unset, matching
#: `brothersbe.cli._doctor_checks`'s own vault line, which already reads an
#: empty value as NO-DATA rather than as a configured path.
VAULT_CONFIGURED = bool(os.environ.get("BROTHERSBE_VAULT", "").strip())

VAULT = os.environ.get("BROTHERSBE_VAULT", "").strip() or NO_VAULT_SENTINEL
TEL_DIR = os.path.join(VAULT, "99-System", "telemetry")
LEDGER = os.path.join(TEL_DIR, "outcomes.jsonl")
RATINGS = os.path.join(TEL_DIR, "ratings.jsonl")
REVIEWS = os.path.join(TEL_DIR, "reviews.jsonl")
CORRECTIONS = os.path.join(TEL_DIR, "corrections.jsonl")
OPERATOR_MODEL = os.path.join(VAULT, "50-Reference", "operator-model.md")
SESSIONS_GLOB = os.path.join(VAULT, "10-Projects", "*", "Sessions", "*.md")

# ---------------------------------------------------------------------------
# CAPTURE POLICY. Nothing here is captured unless a switch for that CATEGORY is
# set, and a category that is off is named in the output rather than silently
# skipped.
#
# The defect this closes: this tool parsed the session transcript and stored
# excerpts of the operator's own messages BY DEFAULT, with best-effort redaction
# standing between a customer name, a partner term or an unreleased design and a
# file on disk. Best effort is the right engineering for a redactor and the
# wrong basis for a default: a repository holding customer, partner, security or
# company-confidential material must be able to install this and have it capture
# nothing at all until somebody decides otherwise, per category.
#
# Three categories, three switches, each independent of the others:
#   metrics      the per-session row in outcomes.jsonl. Counts, model names,
#                duration, and the basename of the working directory. No message
#                text, but a directory basename can itself be a client's name,
#                which is why this is opt-in too.
#   transcript   the transcript-derived TEXT in the resume brief: the operator's
#                last message, recent assistant reasoning, recent tool commands.
#   corrections  the correction candidates in corrections.jsonl: excerpts of the
#                operator's own messages.
#
# The organization override forces all three off and no local switch can turn
# one back on. It is read from a policy FILE (default /etc/brothersbe/telemetry-
# policy.conf, a path an ordinary user cannot write on a managed machine) and
# from an environment variable. Its honest limit, stated here and in
# docs/KNOWN-LIMITS.md: this is a policy control on a cooperating machine, not
# an enforcement boundary. Anyone who can edit that file, or run a patched copy
# of this script, is past it.
#
# A policy file that exists and cannot be read, or that carries a directive this
# version does not recognize, FAILS CLOSED: capture is off and the reason says
# which file and which line. A policy nobody could read must never be the reason
# capture was allowed.
# ---------------------------------------------------------------------------
CAPTURE_SWITCH = {
    "metrics": "BROTHERSBE_TELEMETRY_METRICS",
    "transcript": "BROTHERSBE_TELEMETRY_TRANSCRIPT",
    "corrections": "BROTHERSBE_TELEMETRY_CORRECTIONS",
}
CAPTURE_CATEGORIES = tuple(sorted(CAPTURE_SWITCH))
ORG_DISABLE_ENV = "BROTHERSBE_TELEMETRY_DISABLE"
ORG_POLICY_ENV = "BROTHERSBE_TELEMETRY_POLICY"
DEFAULT_ORG_POLICY = "/etc/brothersbe/telemetry-policy.conf"
TRUE_WORDS = ("1", "true", "yes", "on")
POLICY_KEYS = ("capture", "disable")

MIN_API_MSGS = 5
MIN_TOOL_CALLS = 1
STOPWARN_MIN_BYTES = 200_000   # transcript smaller than this = trivial, stay silent
# Hook self-test end_reasons: real work never uses these. Excluded from every
# aggregate so the learning loop scores on real sessions only (weekly-review
# 2026-07-20 finding: 2 smoke-test lines polluted session counts and token sums).
TEST_END_REASONS = {"simulated-test", "chained-smoke-test", "smoke-test", "test"}

OUT_KEYS = ("gen_ai.usage.output_tokens", "out_tokens")
IN_KEYS = ("gen_ai.usage.input_tokens",)
CORRECTION_RE = re.compile(
    r"\b(no[,.]? (that|this|the)|not what i|wrong|you did not|you didn'?t|not even"
    r"|i said|stop doing|never do|always use|from now on|instead of|do better)\b",
    re.I)


def _makedirs_telemetry():
    """Create TEL_DIR, or refuse outright when no vault is configured.

    NOT `os.makedirs(TEL_DIR, exist_ok=True)`. With no vault configured, VAULT
    is NO_VAULT_SENTINEL, and the comment above says a path built from it
    "fails fast on a single absent lookup and never touches a file the operator
    did not name". On POSIX that is true: the sentinel is at the filesystem
    root and an ordinary user cannot create it. On Windows a leading separator
    means the ROOT OF THE CURRENT DRIVE, and that root is writable, so the
    sentinel becomes a real directory and telemetry with nowhere durable to go
    is silently persisted there. That was measured, not supposed.

    Refusing here restores the stated intent on both platforms, and every
    caller already handles OSError as "the miss surfaces as absent data, never
    as a false pass"."""
    # Keyed on WHERE THE WRITE WOULD LAND, not on the import-time
    # VAULT_CONFIGURED flag: a caller may legitimately point TEL_DIR at a real
    # directory after import, and refusing that would be wrong. The only thing
    # refused is a write that would land under the sentinel itself.
    sentinel = os.path.abspath(NO_VAULT_SENTINEL)
    target = os.path.abspath(TEL_DIR)
    if target == sentinel or target.startswith(sentinel + os.sep):
        raise OSError("no vault is configured, so telemetry has nowhere durable "
                      "to go and nothing was written")
    os.makedirs(TEL_DIR, exist_ok=True)


def vault_label():
    """VAULT as a sentence a reader can trust: the real configured path, or
    an honest "none configured" line naming the switch, never the internal
    NO_VAULT_SENTINEL printed as though it were a real location on disk."""
    if VAULT_CONFIGURED:
        return VAULT
    return "none configured (BROTHERSBE_VAULT is unset)"


def fld(rec, keys, default=0):
    for k in keys:
        if k in rec:
            return rec[k]
    return default


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truthy(value):
    return str(value or "").strip().lower() in TRUE_WORDS


def org_override_reason():
    """One sentence naming the file or variable that forces capture off, or "".

    One value rather than a (bool, why) pair, so the reason and the decision
    cannot disagree: the sentence IS the decision, and an empty sentence is the
    only way to say nothing forced anything.

    Reads the policy file first, then the environment override. Either one
    forcing capture off wins over every local switch; neither one can turn a
    category ON, because an organization switch exists to withhold capture, not
    to impose it on a machine whose operator did not ask for it.
    """
    path = os.environ.get(ORG_POLICY_ENV) or DEFAULT_ORG_POLICY
    if os.path.exists(path):
        try:
            body = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError as e:
            return ("the organization policy %s exists and could not be read (%s), so "
                    "capture fails closed" % (path, e.strerror or e))
        for i, raw in enumerate(body.splitlines(), 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if "=" not in line:
                return ("the organization policy %s line %d (%r) is not a key = value "
                        "directive, so capture fails closed" % (path, i, raw.strip()[:60]))
            key, value = line.split("=", 1)
            key, value = key.strip().lower(), value.strip().lower()
            if key not in POLICY_KEYS:
                return ("the organization policy %s line %d names %r, which this version "
                        "does not recognize, so capture fails closed" % (path, i, key))
            if key == "capture" and value not in ("off", "on"):
                return ("the organization policy %s line %d sets capture to %r, which is "
                        "neither on nor off, so capture fails closed" % (path, i, value))
            if (key == "capture" and value == "off") or (key == "disable" and _truthy(value)):
                return ("the organization policy %s line %d forces every capture category off"
                        % (path, i))
    if _truthy(os.environ.get(ORG_DISABLE_ENV)):
        return ("%s is set in the environment, which forces every capture category off"
                % ORG_DISABLE_ENV)
    return ""


def capture_off_reason(category):
    """One sentence naming what was read to keep this category off, or "".

    Truthy means off, and the truthy value is the reason, so no caller can
    report that nothing was captured without being able to say why.
    """
    var = CAPTURE_SWITCH[category]
    forced = org_override_reason()
    if forced:
        return "%s capture is off: %s" % (category, forced)
    raw = os.environ.get(var)
    if _truthy(raw):
        return ""
    if raw is None:
        return ("%s capture is off: %s is not set, and every category is off by default"
                % (category, var))
    return ("%s capture is off: %s is set to %r, which is not one of %s"
            % (category, var, raw, ", ".join(TRUE_WORDS)))


def capture_state_line(category):
    """The one sentence `data-show` and the export print for a category,
    whichever way it went."""
    off = capture_off_reason(category)
    if off:
        return off
    return "%s capture is on: %s=%s in the environment" % (
        category, CAPTURE_SWITCH[category], os.environ.get(CAPTURE_SWITCH[category]))


# WRITER SERIALIZATION. There used to be no lock anywhere in this repository,
# and the maintenance commands are read-modify-write over the whole ledger: a
# row appended between migrate's read and its rename was destroyed by the
# rename, the loss guard compared two counts taken from the pre-append world
# and agreed with itself, and the operator read "count ok" over a ledger
# missing a live session's row. Two maintenance commands also shared one
# literal ".tmp" path, so one renamed the other's half-written file out from
# under it. The rule: every writer to a ledger file holds this exclusive lock
# (appends briefly, maintenance for its whole read-rewrite-rename-recount),
# the temp path is per-process, and the loss guard recounts the REAL file
# after the rename, under the same lock, so nothing can change between what
# it wrote and what it counted.
@contextlib.contextmanager
def _writer_lock(path, timeout_s=15.0):
    """Exclusive advisory lock for writers of `path`. Yields True when held.

    Bounded, never hangs: on timeout it yields False and the caller decides
    (an append proceeds unlocked so a telemetry row is never dropped, which
    reopens the loss window only if a lock holder is stuck past the timeout,
    and says so here; a maintenance command REFUSES to rewrite). On a
    platform with neither fcntl nor msvcrt the same policy applies and
    maintenance names it.

    Portable by primitive, not by platform name: fcntl.flock where it exists
    (every POSIX build), msvcrt.locking where it does not (never both in one
    interpreter), both raised on contention, retried the same way. msvcrt's
    byte-range reset (see the lock call below) is not repeated before
    UNLOCK: calibration proved a second reset there cannot matter (nothing
    moves the fd between lock and unlock) and it was removed (test_sbe.py,
    TestWriterLockByteRangeCalibration). None of this runs on fcntl.
    """
    if fcntl is None and msvcrt is None:
        yield False
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path + ".lock", os.O_WRONLY | os.O_CREAT, 0o644)
    except OSError as e:
        # The sidecar exists only to protect the row, so failing to OPEN it
        # takes the same path as failing to TAKE it: yield False and let the
        # caller decide (an append proceeds unlocked so the row is never
        # dropped; maintenance refuses). This used to raise out of the
        # context manager into the top-level swallow, so a chmodded or
        # mkdir-shadowed .lock file dropped the very row the lock protects,
        # with a Python repr for a message and exit 0.
        print("sbe_telemetry: could not open the lock sidecar %s.lock (%s); proceeding as if "
              "the lock timed out: an append goes ahead unlocked, maintenance refuses"
              % (path, e.strerror or e), file=sys.stderr)
        yield False
        return
    got = False
    try:
        deadline = time.time() + timeout_s
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    # msvcrt.locking's region is anchored at the CURRENT file
                    # position, not the whole file: two openers of a freshly
                    # created (0-byte) sidecar would otherwise lock two
                    # different byte ranges (whichever happened to pad the
                    # file first ends up one byte further along) and never
                    # contend with each other at all. Padding to >=1 byte
                    # (cheap to repeat: a no-op once the first opener has
                    # done it) and seeking back to 0 before every lock call
                    # makes every opener target the same [0, 1) range
                    # regardless of who created the file. A failure padding
                    # or seeking here needs no except of its own: it falls
                    # into the same except below as a failed lock, which is
                    # exactly what it is, and the retry/timeout policy
                    # already covers that uniformly.
                    if os.fstat(fd).st_size < 1:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                got = True
                break
            except OSError:
                if time.time() >= deadline:
                    break
                time.sleep(0.05)
        yield got
    finally:
        if got:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    # No lseek(0, os.SEEK_SET) here, unlike the LOCK call
                    # above: a matching reset used to sit before this call
                    # too, on the theory that anything might have moved the
                    # fd between lock and unlock. TestWriterLockByteRange
                    # Calibration (tools/test_sbe.py) could not make that
                    # reset fail under any mutation, because nothing in this
                    # function's own control flow touches the fd's position
                    # between the successful LOCK above and this UNLOCK: the
                    # caller's work inside `yield got` runs against whatever
                    # file it opened, never this lock sidecar. An assertion
                    # that can never fail is not a guard, so the reset was
                    # removed rather than kept for the look of symmetry.
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:  # sbe: allow-silent closing the fd below releases the lock regardless
                pass
        os.close(fd)


def _write_all(fd, data):
    """os.write until every byte is written. A short write (possible on any
    POSIX write) silently truncated a row before this loop existed, and a
    truncated JSONL row is a corrupt line the readers then have to disclose."""
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def atomic_append(path, obj, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
    with _writer_lock(path) as held:
        # Locked or not, the append itself is O_APPEND (atomic against other
        # appends); the lock is what keeps it out of a maintenance rewrite's
        # read-to-rename window.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode)
        try:
            _write_all(fd, line)
        finally:
            os.close(fd)
        if not held:
            # The fallback RECORDS itself, so a rewrite can see that an
            # unlocked append happened at all: the round-11 loss was invisible
            # precisely because the fallback left no trace anywhere. Best
            # effort by design; the row above is already safe on disk.
            try:
                nfd = os.open(path + ".unlocked-appends",
                              os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                try:
                    _write_all(nfd, (json.dumps({"ts": now_iso(), "pid": os.getpid()},
                                                separators=(",", ":")) + "\n").encode())
                finally:
                    os.close(nfd)
            except OSError:  # sbe: allow-silent the note is advisory; the row itself is written, and a hook must never fail over its trace
                pass


def parse_transcript(path, collect_user_texts=False):
    agg = {"out": 0, "cache_w": 0, "cache_r": 0, "in": 0, "api_msgs": 0,
           "tool_calls": 0, "agent_spawns": 0, "workflow_calls": 0,
           "human_msgs": 0, "models": {}, "first_ts": None, "last_ts": None,
           "user_texts": []}
    per_msg = {}
    anon = 0
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return agg
    with f:
        for raw in f:
            try:
                o = json.loads(raw)
            except Exception:
                continue  # sbe: allow-silent aggregate reader over a live transcript; a corrupt line must not lose the rest, and nothing here rewrites the file
            ts = o.get("timestamp")
            if isinstance(ts, str) and len(ts) >= 19:
                if agg["first_ts"] is None or ts < agg["first_ts"]:
                    agg["first_ts"] = ts
                if agg["last_ts"] is None or ts > agg["last_ts"]:
                    agg["last_ts"] = ts
            t = o.get("type")
            if t == "user" and not o.get("isMeta"):
                c = (o.get("message") or {}).get("content")
                txt = None
                if isinstance(c, str):
                    txt = c
                elif isinstance(c, list):
                    if not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                        txt = "\n".join(b.get("text", "") for b in c
                                        if isinstance(b, dict) and b.get("type") == "text")
                if txt and txt.strip() and "<system-reminder>" not in txt:
                    agg["human_msgs"] += 1
                    if collect_user_texts:
                        agg["user_texts"].append(txt.strip())
            if t != "assistant":
                continue
            m = o.get("message") or {}
            model = m.get("model")
            if model:
                agg["models"][model] = agg["models"].get(model, 0) + 1
            u = m.get("usage")
            if u:
                mid = m.get("id")
                if not mid:
                    mid = "anon%d" % anon
                    anon += 1
                slot = per_msg.setdefault(mid, {})
                for k in ("input_tokens", "output_tokens",
                          "cache_creation_input_tokens", "cache_read_input_tokens"):
                    v = u.get(k) or 0
                    if v > slot.get(k, 0):
                        slot[k] = v
            for b in (m.get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    agg["tool_calls"] += 1
                    name = b.get("name", "")
                    if name in ("Task", "Agent"):
                        agg["agent_spawns"] += 1
                    elif name == "Workflow":
                        agg["workflow_calls"] += 1
    agg["api_msgs"] = len(per_msg)
    for s in per_msg.values():
        agg["out"] += s.get("output_tokens", 0)
        agg["in"] += s.get("input_tokens", 0)
        agg["cache_w"] += s.get("cache_creation_input_tokens", 0)
        agg["cache_r"] += s.get("cache_read_input_tokens", 0)
    return agg


# Correction candidates are excerpts of YOUR OWN messages, so they can carry
# secrets you typed. Redact secret-shaped substrings before anything is written,
# and keep the file owner-only. This is best-effort pattern matching, not a
# guarantee: treat the file as sensitive and purge it when you no longer need it
# (tools/sbe_telemetry.py purge-corrections).
SECRET_PATTERNS = [
    re.compile(r"\b(sk|rk)[-_][A-Za-z0-9_-]{12,}", re.I),           # api keys
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{16,}"),                     # github tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                            # aws key id
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}", re.I),             # slack
    re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),      # jwt
    # key=value and key: value where the key names a secret
    # key=value, key: value, and the natural-language "the password is hunter2".
    # Over-redaction is deliberate here: a masked non-secret costs nothing, a
    # stored secret costs a lot.
    # Leading [A-Za-z0-9_]* so PROD_DB_PASSWORD= matches as well as password=.
    re.compile(r"(?i)[A-Za-z0-9_]*(?:pass(?:word|wd|phrase)?|secret|token"
               r"|api[_-]?key|access[_-]?key|private[_-]?key|credential)s?"
               r"\s*(?:[:=]|\s+(?:is|was)\s+)\s*\S+"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                          # us ssn shape
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),                          # card-ish digits
]


def redact(text):
    """Mask secret-shaped substrings. Returns (clean_text, n_redactions)."""
    n = 0
    for pat in SECRET_PATTERNS:
        text, k = pat.subn("[REDACTED]", text)
        n += k
    return text, n


def scan_corrections(sid, project, user_texts):
    """Correction CANDIDATES from short operator messages in the MAIN transcript
    only (subagent transcripts quote law text and would flood this). Human filter
    at the weekly review decides what becomes law; cap 5 per session.
    Dedup guard (interim-score fix 2026-07-23): the SessionEnd hook can fire more
    than once per session; a (session_id, text) already in the file is never
    re-appended.

    Opt-in guard, second copy on purpose: the caller already refuses to collect
    the texts this reads, and this function writes the file, so the switch is
    tested where the write happens as well. A control that lives only in the
    caller is one refactor away from being no control."""
    if capture_off_reason("corrections"):
        return 0
    seen = {(c.get("session_id"), c.get("text")) for c in read_jsonl(CORRECTIONS)}
    found = 0
    for txt in user_texts:
        if found >= 5:
            break
        if len(txt) > 400:
            continue
        if CORRECTION_RE.search(txt):
            clean, nred = redact(txt[:400])
            if (sid, clean) in seen:
                continue
            rec = {"ts": now_iso(), "session_id": sid,
                   "project": project, "text": clean}
            if nred:
                rec["redactions"] = nred
            atomic_append(CORRECTIONS, rec, mode=0o600)
            seen.add((sid, clean))
            found += 1
    return found


def cmd_outcomes_append():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("sbe_telemetry: no valid hook payload on stdin; nothing recorded")
        return
    tp = payload.get("transcript_path") or ""
    sid = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or ""
    if not tp or not os.path.isfile(tp):
        print("sbe_telemetry: transcript not found; nothing recorded")
        return
    # THE TRANSCRIPT IS NOT OPENED UNTIL A CATEGORY THAT NEEDS IT IS ON. Both
    # off is the default installation, and the default installation must read
    # nothing out of the session at all, not read it and decline to keep it.
    metrics_why = capture_off_reason("metrics")
    corrections_why = capture_off_reason("corrections")
    metrics_on, corrections_on = not metrics_why, not corrections_why
    if not metrics_on and not corrections_on:
        print("sbe_telemetry: nothing captured from %s; %s; %s"
              % (os.path.basename(tp), metrics_why, corrections_why))
        return
    main = parse_transcript(tp, collect_user_texts=corrections_on)
    sub = {"out": 0, "files": 0}
    subroot = os.path.join(os.path.dirname(tp), sid, "subagents")
    if metrics_on and os.path.isdir(subroot):
        for sf in glob.glob(os.path.join(subroot, "**", "*.jsonl"), recursive=True):
            sub["files"] += 1
            sa = parse_transcript(sf)
            sub["out"] += sa["out"]
    if main["api_msgs"] < MIN_API_MSGS or main["tool_calls"] < MIN_TOOL_CALLS:
        print("sbe_telemetry: below activity floor (%d api msgs, %d tool calls); not recorded"
              % (main["api_msgs"], main["tool_calls"]))
        return
    project = os.path.basename(cwd) or cwd
    if not metrics_on:
        # Corrections only. The row is what carries the project basename and the
        # per-session counts, so with metrics off no row exists and the sentence
        # below names the switch rather than reporting a recorded session.
        ncorr = scan_corrections(sid, project, main["user_texts"])
        print("sbe_telemetry: %s; %d correction candidate(s) captured from %s"
              % (metrics_why, ncorr, os.path.basename(tp)))
        return
    hours = 0.0
    if main["first_ts"] and main["last_ts"]:
        try:
            a = datetime.datetime.fromisoformat(main["first_ts"].replace("Z", "+00:00"))
            b = datetime.datetime.fromisoformat(main["last_ts"].replace("Z", "+00:00"))
            hours = round((b - a).total_seconds() / 3600, 2)
        except Exception:  # sbe: allow-silent a hook must never crash a session; the miss is visible as absent telemetry
            pass
    rec = {
        "schema": 2, "ts": now_iso(), "session_id": sid, "project": project,
        "end_reason": payload.get("reason") or "",
        "gen_ai.usage.output_tokens": main["out"],
        "gen_ai.usage.input_tokens": main["in"],
        "sub_out_tokens": sub["out"],
        "cache_write": main["cache_w"], "cache_read": main["cache_r"],
        "api_msgs": main["api_msgs"], "human_msgs": main["human_msgs"],
        "tool_calls": main["tool_calls"], "agent_spawns": main["agent_spawns"],
        "workflow_calls": main["workflow_calls"], "subagent_files": sub["files"],
        "models": sorted(main["models"].keys()), "duration_h": hours,
        "token_basis": "as-flushed",
    }
    # Duplicate-flush guard (interim-score fix 2026-07-23): the SessionEnd hook
    # can fire repeatedly for one session; an identical metric set (everything
    # but ts) is the same flush and is not recorded twice. A changed metric set
    # is real progress and still appends (real_sessions keeps the last flush).
    comparable = {k: v for k, v in rec.items() if k != "ts"}
    for prev in read_jsonl(LEDGER):
        if prev.get("session_id") == sid and {k: v for k, v in prev.items() if k != "ts"} == comparable:
            print("sbe_telemetry: duplicate flush for %s (identical metrics); not recorded" % sid[:8])
            return
    atomic_append(LEDGER, rec)
    ncorr = scan_corrections(sid, project, main["user_texts"])
    print("sbe_telemetry: recorded %s (%dk out, %d tools, %.1fh, %d correction candidates)"
          % (sid[:8], main["out"] // 1000, main["tool_calls"], hours, ncorr))
    if not corrections_on:
        print("sbe_telemetry: %s" % corrections_why)


def cmd_migrate():
    if not os.path.isfile(LEDGER):
        print("migrate: no ledger yet")
        return
    with _writer_lock(LEDGER) as held:
        if not held:
            print("migrate: could not take or open the writer lock (%s.lock) within its "
                  "timeout; another writer may be mid-rewrite, the sidecar may be unopenable, "
                  "or this platform has neither fcntl nor msvcrt to lock with. "
                  "Nothing was touched; run again when the other writer finishes"
                  % os.path.basename(LEDGER))
            return
        _cmd_migrate_locked()


def _bak_name(path, kind):
    # Unique per RUN, never per day: `if not exists(bak)` kept the FIRST
    # backup of a calendar day, so the second run of the same day printed a
    # backup name that by construction did not contain the rows that second
    # run dropped, under a comment promising "the original bytes exist".
    return "%s.bak-%s%s-%d" % (path, kind,
                               datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),
                               os.getpid())


def _rewrite_locked(path, out_lines, read_size, label):
    """The one rename step every ledger rewrite uses. Returns (rows on disk
    after the rename, rows passed through) or None after printing a refusal.

    A loss guard must compare against the real post-write world, never
    against its own output: the old recount ran AFTER the rename, so it
    counted the file the rewrite had just written and compared it to itself,
    an identity that can never fail, while the row a fallback append had
    written to the OLD inode was destroyed unseen. The guard here runs
    BEFORE the rename, against the live file: the temp file is written
    first, then the file being replaced is re-measured, any bytes appended
    since the read are carried into the temp file verbatim (an appended row
    is a row, whatever command happens to be rewriting), and only a file
    that measures exactly as read is renamed over. A file that shrinks or
    keeps growing under the writer lock is never replaced. The instant
    between the final measurement and the rename remains, is disclosed in
    KNOWN-LIMITS, and is covered post-hoc: the append fallback records
    itself in the <ledger>.unlocked-appends note file, and any record from
    inside this rewrite's window is reported after the rename.
    """
    t0 = time.time()
    passthrough = 0
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        for line in out_lines:
            f.write(line + "\n")
    written = len(out_lines)
    renamed = False
    for _ in range(6):
        try:
            size_now = os.stat(path).st_size
        except OSError as e:
            os.unlink(tmp)
            print("%s: cannot measure %s before the rename (%s); nothing was replaced"
                  % (label, os.path.basename(path), e.strerror or e))
            return None
        if size_now == read_size:
            # os.replace, not os.rename: Windows rename refuses an existing
            # target (FileExistsError 17, measured on the windows-latest leg,
            # run 31039904060); replace overwrites atomically on every platform.
            os.replace(tmp, path)
            renamed = True
            break
        if size_now < read_size:
            os.unlink(tmp)
            print("%s: %s SHRANK while this rewrite held the writer lock (%d bytes read, %d on "
                  "disk); a second writer is ignoring the lock, so nothing was replaced"
                  % (label, os.path.basename(path), read_size, size_now))
            return None
        with open(path, "rb") as src:
            src.seek(read_size)
            tail = src.read(size_now - read_size)
        tail_lines = [l for l in tail.decode(errors="replace").splitlines() if l.strip()]
        with open(tmp, "a") as f:
            for line in tail_lines:
                f.write(line + "\n")
        written += len(tail_lines)
        passthrough += len(tail_lines)
        read_size = size_now
    if not renamed:
        os.unlink(tmp)
        print("%s: %s kept growing through 6 freshness checks under the writer lock; refusing "
              "to replace a file that is still being appended to. Nothing was replaced; run "
              "again when the appends stop" % (label, os.path.basename(path)))
        return None
    n_after = len([l for l in open(path, encoding="utf-8", errors="replace").read().splitlines() if l.strip()])
    count_ok = (n_after == written)
    if not count_ok:
        print("%s: LINE COUNT WRONG after the rename (%d written, %d on disk); restore from "
              "the backup and investigate!" % (label, written, n_after))
    note = path + ".unlocked-appends"
    late = 0
    try:
        if os.path.isfile(note) and os.path.getmtime(note) >= t0:
            for raw in open(note, encoding="utf-8", errors="replace"):
                try:
                    if json.loads(raw).get("ts", "") >= datetime.datetime.fromtimestamp(
                            t0, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"):
                        late += 1
                except ValueError:
                    late += 1
    except OSError:  # sbe: allow-silent the note is advisory post-hoc coverage; an unreadable note must not fail a completed rewrite, and the freshness guard above already ran
        late = 0
    if late:
        print("%s: %d unlocked append(s) were recorded during this rewrite's window (%s); every "
              "byte present at the final freshness check was carried over, but an append landing "
              "in the instant between that check and the rename would be in neither file. Compare "
              "the backup against %s"
              % (label, late, os.path.basename(note), os.path.basename(path)))
    return n_after, passthrough, count_ok


def _cmd_migrate_locked():
    # RAW lines, not parsed rows. The before count, the backup and the rewrite
    # all used to flow through the lossy parser, so an unparseable line was
    # deleted from the ledger AND from the backup while the loss guard compared
    # two counts produced by the same reader and printed "count ok". The guard
    # can only see a loss it measures on the raw file. The whole
    # read-rewrite-freshness-rename runs under the writer lock (see
    # _writer_lock and _rewrite_locked): the bytes are read ONCE, and the
    # freshness guard compares the live file against that read before the
    # rename, so a row appended by the unlocked fallback is carried, not
    # destroyed.
    data = open(LEDGER, "rb").read()
    read_size = len(data)
    raw_lines = [l for l in data.decode(errors="replace").splitlines() if l.strip()]
    n_before = len(raw_lines)
    # The backup is a BYTE COPY of the bytes this run READ, under a name
    # unique to this run, so whatever the rewrite does the original bytes
    # exist and the printed name is never a stale earlier run's file.
    bak = _bak_name(LEDGER, "migrate")
    with open(bak, "wb") as f:
        f.write(data)
    changed = 0
    preserved = []
    out = []
    for i, raw in enumerate(raw_lines, 1):
        try:
            r = json.loads(raw)
        except ValueError:
            # Preserved verbatim, in place, and named below: a line this tool
            # cannot parse is a line it has no business deleting.
            preserved.append(i)
            out.append(raw)
            continue
        if not isinstance(r, dict):
            # Parses, is not a row. Same treatment and the same disclosure:
            # "1 migrated to schema 2, count ok (2)" read as though the other
            # line was already schema 2 when it was the integer 5.
            preserved.append(i)
            out.append(raw)
            continue
        if isinstance(r, dict) and r.get("schema") != 2:
            r = dict(r)
            if "out_tokens" in r:
                r["gen_ai.usage.output_tokens"] = r.pop("out_tokens")
            r["schema"] = 2
            # input tokens were never recorded pre-schema-2: stays ABSENT, never invented
            changed += 1
        out.append(json.dumps(r, separators=(",", ":")) if not isinstance(r, str) else raw)
    # The rename, freshness guard and recount live in _rewrite_locked, shared
    # with dedup, because "rewriting a file other processes append to" is one
    # problem, not a property of whichever command met it first.
    done = _rewrite_locked(LEDGER, out, read_size, "migrate")
    if done is None:
        return
    n_after, passthrough, count_ok = done
    if not count_ok:
        return
    note = ("" if not preserved else
            ", %d line(s) this tool could not read as a ledger row preserved verbatim (line %s; "
            "each is either unparseable JSON or valid JSON that is not an object; fix or remove "
            "them by hand, they are in the backup too)"
            % (len(preserved), ", ".join(str(i) for i in preserved[:4])))
    carried = ("" if not passthrough else
               ", %d row(s) appended during the rewrite carried over verbatim" % passthrough)
    print("migrate: %d lines, %d migrated to schema 2%s%s, count ok (%d)"
          % (n_before, changed, note, carried, n_after))


def cmd_dedup():
    """One-time cleanup of duplicate hook flushes already in the files. Backup
    first, temp+rename, count check printed. Outcomes: drop rows whose metric
    set (everything but ts) matches an already-kept row for the same session,
    keeping the LAST occurrence (matches real_sessions semantics). Corrections:
    drop duplicate (session_id, text), keeping the first."""
    for path, keyfn, keep in (
        (LEDGER, lambda r: (r.get("session_id"),
                            json.dumps({k: v for k, v in r.items() if k != "ts"},
                                       sort_keys=True)), "last"),
        (CORRECTIONS, lambda r: (r.get("session_id"), r.get("text")), "first"),
    ):
        with _writer_lock(path) as held:
            if not held:
                print("dedup: could not take or open the writer lock (%s.lock) within its "
                      "timeout; another writer may be mid-rewrite, the sidecar may be "
                      "unopenable, or this platform has neither fcntl nor msvcrt to lock with. "
                      "%s was not touched" % (os.path.basename(path), os.path.basename(path)))
                continue
            _dedup_one_locked(path, keyfn, keep)


def _dedup_one_locked(path, keyfn, keep):
        # The bytes are read ONCE, so the parse, the backup and the freshness
        # guard all describe the same file state; the printed counts used to
        # be computed before the rename from the pre-append world, with no
        # recount of any kind, which is the exact guard shape the migrate
        # comment calls the defect.
        try:
            data = open(path, "rb").read()
        except OSError:
            print("dedup: %s empty or missing, skipped" % os.path.basename(path))
            return
        read_size = len(data)
        rows, bad = [], []
        for i, raw in enumerate(data.decode(errors="replace").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                r = json.loads(raw)
            except ValueError:
                bad.append((i, raw))
                continue
            if isinstance(r, dict):
                rows.append(r)
            else:
                bad.append((i, raw))
        if bad:
            # A rewrite from parsed rows deletes every line the parser dropped.
            # This tool will not rewrite a file it cannot fully read: the
            # broken lines are named instead, and nothing is touched.
            print("dedup: %s has %d line(s) this tool cannot read as a ledger row (line %s; "
                  "unparseable JSON, or valid JSON that is not an object); refusing to rewrite a "
                  "file this tool cannot fully read. Fix or remove those lines first"
                  % (os.path.basename(path), len(bad),
                     ", ".join(str(i) for i, _ in bad[:4])))
            return
        if not rows:
            print("dedup: %s empty or missing, skipped" % os.path.basename(path))
            return
        if keep == "last":
            kept_rev, seen = [], set()
            for r in reversed(rows):
                k = keyfn(r)
                if k in seen:
                    continue
                seen.add(k)
                kept_rev.append(r)
            kept = list(reversed(kept_rev))
        else:
            kept, seen = [], set()
            for r in rows:
                k = keyfn(r)
                if k in seen:
                    continue
                seen.add(k)
                kept.append(r)
        if len(kept) == len(rows):
            print("dedup: %s already clean (%d lines)" % (os.path.basename(path), len(rows)))
            return
        # A BYTE copy of the bytes this run read, under a per-run name; the
        # old daily name kept the FIRST backup of a calendar day, so a second
        # run's sentence named a file that did not hold its dropped rows.
        bak = _bak_name(path, "dedup")
        with open(bak, "wb") as f:
            f.write(data)
        out = [json.dumps(r, separators=(",", ":")) for r in kept]
        done = _rewrite_locked(path, out, read_size, "dedup")
        if done is None:
            return
        n_after, passthrough, count_ok = done
        if not count_ok:
            return
        carried = ("" if not passthrough else
                   "; %d row(s) appended during the rewrite carried over verbatim" % passthrough)
        print("dedup: %s %d -> %d lines (%d duplicate flushes dropped%s; backup %s; %d on disk)"
              % (os.path.basename(path), len(rows), len(kept),
                 len(rows) - len(kept), carried, os.path.basename(bak), n_after))


def outcomes_lines_in_window(min_age_d, max_age_d):
    """Dated human lines in the vault OUTCOMES ledgers (one line per recorded
    run; the closest thing to 'shipped surface' the system records)."""
    n = 0
    today = datetime.date.today()
    for path in glob.glob(os.path.join(VAULT, "10-Projects", "*", "OUTCOMES.md")):
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"^(\d{4}-\d{2}-\d{2}) \|", line)
            if not m:
                continue
            try:
                age = (today - datetime.date.fromisoformat(m.group(1))).days
            except ValueError:
                continue  # sbe: allow-silent a malformed date row is not a dated run; this count is a labeled proxy and nothing here rewrites the file
            if min_age_d <= age < max_age_d:
                n += 1
    return n


def cmd_speed():
    """Rubric metric 3 feed. HONEST LABELS: duration_h is the span from first to
    last message (includes idle), and OUTCOMES lines are human-recorded runs, a
    PROXY for shipped surfaces. No proxy, no number."""
    rows = real_sessions(read_jsonl(LEDGER))
    def window(lo, hi):
        w = [r for r in rows if lo <= (age_days(r.get("ts", "")) or 999) < hi]
        return {"sessions": len(w),
                "hours": round(sum(r.get("duration_h", 0) for r in w), 1),
                "outk": sum(fld(r, OUT_KEYS) + r.get("sub_out_tokens", 0) for r in w) // 1000}
    cur, prev = window(0, 7), window(7, 14)
    cur_runs, prev_runs = outcomes_lines_in_window(0, 7), outcomes_lines_in_window(7, 14)
    print("BROTHERSBE SPEED (metric 3 feed; span-hours include idle, runs are OUTCOMES lines)")
    for label, w, runs in (("last 7d ", cur, cur_runs), ("prior 7d", prev, prev_runs)):
        per = ("%.1f span-h/run" % (w["hours"] / runs)) if runs else "no runs recorded"
        print("  %s: %d sessions, %.1f span-h, %dk out, %d recorded runs -> %s"
              % (label, w["sessions"], w["hours"], w["outk"], runs, per))
    if not (cur_runs and prev_runs):
        print("  trend: NO-DATA (both windows need recorded runs; nothing is invented)")


def read_jsonl(path):
    """Rows only, for aggregate readers that tolerate a corrupt line.

    Callers that REWRITE the file must not use this: a rewrite from the parsed
    rows deletes every line the parser dropped, silently. Those go through
    read_jsonl_counted, which hands back the dropped lines by name.
    """
    return read_jsonl_counted(path)[0]


def read_jsonl_counted(path):
    """(rows, [(line number, raw line)] this reader could not read AS A ROW).

    The dropped lines are RETURNED, not swallowed: read_jsonl used to skip
    them with a bare continue, and cmd_migrate then computed its before and
    after counts through the same lossy reader, so the loss guard compared
    two numbers that could never differ, the backup was written without the
    dropped lines, and the tool printed "count ok" over a ledger it had just
    shrunk. A hook may tolerate a corrupt line; a rewriter must carry it.

    "Could not read as a row" is the whole class, not just a parse error: a
    ledger row is a JSON OBJECT, and a line holding a bare int, string, list
    or null parses perfectly and is no row at all. Scoping the disclosure to
    json.loads failures let those lines walk past the sentence that counts
    them.
    """
    rows, bad = [], []
    if not os.path.isfile(path):
        return rows, bad
    for i, raw in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            bad.append((i, raw))
            continue
        if not isinstance(r, dict):
            # A LINE THAT PARSES IS NOT A ROW. A ledger row is a JSON object,
            # and `5` or `"a string"` parses fine and answers no .get: the
            # disclosure channel was scoped to json.loads failures, so a
            # non-object walked past it and "1 migrated to schema 2, count ok
            # (2)" read as "the other line was already schema 2" about the
            # integer 5, while dedup swallowed an AttributeError and exited 0
            # with the duplicates still in the file. Unreadable is unreadable
            # whichever way the line fails to be a row, so both go back to the
            # caller by name, in one channel.
            bad.append((i, raw))
            continue
        rows.append(r)
    return rows, bad


def real_sessions(rows):
    """One row per real session: drop hook self-tests, dedup by session_id
    (last flush wins). Both the scorecard and the startup nag use this so
    'sessions' means the same thing in every output.

    A row with NO usable session_id is not deduplicated at all: every such row
    used to collapse into one bucket keyed "?", so the reduction deleted broken
    rows BEFORE any check could count them, and a disclosure that said "1
    session row(s) carry no readable timestamp" was printed over a file
    holding two. Reduce after the test, or do not reduce what the test needs
    to see: rows the dedupe key cannot read pass through one for one."""
    by = {}
    keyless = []
    for r in rows:
        if r.get("end_reason") in TEST_END_REASONS:
            continue
        sid = r.get("session_id")
        if isinstance(sid, str) and sid.strip():
            by[sid] = r
        else:
            keyless.append(r)
    return list(by.values()) + keyless


def age_days(iso):
    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 86400
    except Exception:
        return None  # sbe: allow-silent None IS the disclosure path; every checker turns an unreadable age into a named undated FAIL


def cmd_scorecard():
    led = read_jsonl(LEDGER)
    rows = real_sessions(led)
    ratings = read_jsonl(RATINGS)
    reviews = read_jsonl(REVIEWS)
    corrections = read_jsonl(CORRECTIONS)
    preds = prediction_counts()
    recent = [r for r in rows if (age_days(r.get("ts", "")) or 99) <= 7]
    out7 = sum(fld(r, OUT_KEYS) + r.get("sub_out_tokens", 0) for r in recent)
    cr = sum(r.get("cache_read", 0) for r in recent)
    cw = sum(r.get("cache_write", 0) for r in recent)
    ratio = (100.0 * cr / (cr + cw)) if (cr + cw) else None
    rated = [x for x in ratings if isinstance(x.get("score"), (int, float))]
    avg_rating = round(sum(x["score"] for x in rated) / len(rated), 2) if rated else None
    last_review = max((x.get("ts", "") for x in reviews), default=None)
    lr_age = age_days(last_review) if last_review else None
    print("BROTHERSBE SCORECARD  (mechanical fields computed; judgment fields scored at weekly review)")
    print("ledger: %d sessions, %d last 7d, %dk out last 7d, %d correction candidates pending"
          % (len(rows), len(recent), out7 // 1000, len(corrections)))
    print("1 self-learning : reviews=%d, last=%s, sealed predictions=%d (10: 0 gaps 14d + 2 reviews + >=5 predictions)"
          % (len(reviews), ("%.1fd" % lr_age) if lr_age is not None else "never", preds["sealed"]))
    print("2 token economy : measured=%d/%d, out 7d=%dk, budget-vs-tier: sbe_score check (10: 0 unmeasured + budgets enforced + trend down)"
          % (len(rows), len(rows), out7 // 1000))
    print("3 speed         : run 'sbe_telemetry.py speed' for the metric 3 feed; trend judged at weekly review")
    print("4 alignment     : ratings=%d avg=%s, prediction hits=%s/%s, corrections captured=%d (10: avg>=4.5 + 0 repeats 2wk)"
          % (len(rated), avg_rating, preds["hits"], preds["scored"], len(corrections)))
    print("5 memory        : canonical=%s; registry + vault hygiene judged weekly" % LEDGER)
    print("6 honesty       : floor gate; evidence blocks in fence closes judged weekly")
    print("7 coordination  : floor gate; collisions=0, baton drops=0, resume-not-respawn judged weekly")
    print("8 delivery      : shipped surfaces vs spend, check: fields on fences, judged weekly")
    print("9 cache economy : warm-read ratio 7d=%s (10: sustained >=90%% + zero broken-prefix incidents 2wk)"
          % (("%.1f%%" % ratio) if ratio is not None else "no data"))


def prediction_counts():
    """Sealed, scored and hit predictions, counting each DISTINCT row once.

    Five identical rows are one prediction written five times, and they cleared
    the "at least 5 sealed" threshold. The rule is sbe_checks.distinct(), shared
    with gate_numbers and the other two thresholds that did not have it.
    """
    out = {"sealed": 0, "scored": 0, "hits": 0, "note": ""}
    # ACCESS before open(): a FIFO at this path would hang the scorecard forever,
    # and an unreadable file would read as an absent ledger. The counts stay
    # zero and the note says why; the scorer's own check FAILs on the same test.
    problem = evidence_problem(OPERATOR_MODEL)
    if problem:
        out["note"] = "; %s is %s, so no prediction row was read" % (OPERATOR_MODEL, problem)
        return out
    if not os.path.isfile(OPERATOR_MODEL):
        return out
    in_ledger = False
    rows = []
    # Rendered text: a prediction ledger inside an HTML comment renders as
    # nothing, and its rows used to count as sealed predictions anyway.
    for line in without_comments(open(OPERATOR_MODEL, encoding="utf-8", errors="replace").read()).splitlines():
        if line.startswith("## Prediction ledger"):
            in_ledger = True
            continue
        if in_ledger and line.startswith("## "):
            break
        if in_ledger and line.strip().startswith("20") and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            # BOTH load-bearing columns go through the shared answered(), not
            # a third private two-token list: "TBD" and "- " cleared a
            # hardcoded ("n/a", "") test and five TBD seals counted as "5
            # sealed"; and then the rule reached the SEAL column and not the
            # PREDICTION, so five sealed rows whose prediction column said TBD
            # counted as five predictions. A sealed non-prediction is not a
            # prediction; the rows are counted below and disclosed.
            if len(parts) >= 5:
                rows.append(parts)
    # Deduplicated by the PREDICTION, not by the whole row: the date column made
    # every row unique, so the fold this function's own docstring promises could
    # never fold anything, and one prediction written five times on five dates
    # cleared the "at least 5 sealed" threshold.
    # derivation_fold, not fold: check_adr's dedupe already learned that
    # "Nightly batch reconciliation" and "nightly batch reconciliation." are
    # one sentence, and this sibling had the weaker reduction, so one
    # prediction written five times with five trailing punctuation marks
    # cleared the "at least 5 sealed" threshold.
    seen, unique, dupes, empty_prediction = set(), [], 0, 0
    for parts in rows:
        if answered(parts[1]) is None or answered(parts[2]) is None:
            if answered(parts[1]) is None:
                empty_prediction += 1
            continue
        key = derivation_fold(parts[1])
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        unique.append(parts)
    for parts in unique:
        out["sealed"] += 1
        if parts[4].lower().startswith(("yes", "hit", "no", "miss")):
            out["scored"] += 1
            if parts[4].lower().startswith(("yes", "hit")):
                out["hits"] += 1
    notes = []
    if dupes:
        notes.append("%d repeated prediction(s) counted once" % dupes)
    if empty_prediction:
        notes.append("%d row(s) whose prediction column records no prediction were not counted"
                     % empty_prediction)
    if notes:
        out["note"] = "; " + "; ".join(notes)
    return out


def cmd_rate(argv):
    score, task, note = None, "", ""
    it = iter(argv)
    for a in it:
        if a == "--score":
            score = float(next(it, "0"))
        elif a == "--task":
            task = next(it, "")
        elif a == "--note":
            note = next(it, "")
    if score is None or not (1 <= score <= 5):
        print("usage: rate --score 1..5 --task \"...\" [--note \"...\"]")
        return
    atomic_append(RATINGS, {"ts": now_iso(), "score": score, "task": task, "note": note})
    print("sbe_telemetry: rating %.1f recorded for '%s'" % (score, task))


def cmd_review_mark(argv):
    atomic_append(REVIEWS, {"ts": now_iso(), "note": " ".join(argv)})
    print("sbe_telemetry: weekly review marked")


def cmd_startup_nags():
    reviews = read_jsonl(REVIEWS)
    # min over parsed AGES, never max over raw timestamp strings: one row whose
    # ts sorts above every date lexically used to win max() and erase the nag.
    ages = [age_days(x.get("ts", "")) for x in reviews]
    ages = [x for x in ages if x is not None]
    a = min(ages) if ages else None
    if a is None:
        print("BROTHERSBE NAG: weekly review has NEVER run; run tools/WEEKLY-REVIEW.md this week.")
    elif a > 7:
        print("BROTHERSBE NAG: weekly review overdue (%.0f days); run tools/WEEKLY-REVIEW.md." % a)
    led = real_sessions(read_jsonl(LEDGER))
    y = [r for r in led if 0.0 <= (age_days(r.get("ts", "")) or 99) <= 1.0]
    if y:
        yk = sum(fld(r, OUT_KEYS) + r.get("sub_out_tokens", 0) for r in y)
        print("BROTHERSBE SPEND: last 24h %dk out tokens across %d sessions." % (yk // 1000, len(y)))
    recent = [r for r in led if (age_days(r.get("ts", "")) or 99) <= 2]
    if led and not recent:
        print("BROTHERSBE NAG: telemetry heartbeat silent 48h; check the SessionEnd hook.")
    # Session-log-per-active-day nag (interim-score fix 2026-07-23): same failure
    # mode as pre-hook token telemetry, same cure. A log counts by filename date
    # OR mtime date, so late backfills clear the nag. Today is exempt (in flight).
    log_days = set()
    for f in glob.glob(SESSIONS_GLOB):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", os.path.basename(f))
        if m:
            log_days.add(m.group(1))
        try:
            log_days.add(datetime.date.fromtimestamp(os.path.getmtime(f)).isoformat())
        except OSError:
            continue  # sbe: allow-silent a nag path that never blocks; a file whose mtime vanished contributes no date
    today = datetime.date.today().isoformat()
    active = {r.get("ts", "")[:10] for r in led if 0 < (age_days(r.get("ts", "")) or 99) <= 3}
    missing = sorted(d for d in active if d and d not in log_days and d != today)
    if missing:
        print("BROTHERSBE NAG: active day(s) %s have no vault session log; backfill or waive with a note."
              % ", ".join(missing))


def cmd_stop_warn():
    """Stop hook, fires EVERY assistant turn machine-wide: must be near-free.
    Short-circuits in order; NEVER parses the transcript; warn at most once per
    session via a marker file; warn-only (exit 0 cannot block)."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    sid = payload.get("session_id") or ""
    tp = payload.get("transcript_path") or ""
    if not sid or not tp:
        return
    # Marker lives under the vault, not /tmp: a predictable /tmp name on a
    # shared machine is a symlink hazard, and the vault is already ours.
    marker = os.path.join(TEL_DIR, ".stopwarn-%s" % re.sub(r"[^A-Za-z0-9_-]", "", sid))
    if os.path.exists(marker):
        return
    try:
        if os.path.getsize(tp) < STOPWARN_MIN_BYTES:
            return
    except OSError:
        return
    today = datetime.date.today()
    for f in glob.glob(SESSIONS_GLOB):
        try:
            if datetime.date.fromtimestamp(os.path.getmtime(f)) == today:
                return
        except OSError:
            continue  # sbe: allow-silent warn-only hook that must stay near-free; a vanished file is simply not today's log
    try:
        _makedirs_telemetry()
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
    except OSError:
        return
    print(json.dumps({"systemMessage":
        "BrotherSBE: substantial session, no vault session log written today. "
        "Write the log before closing (vault law), or note why it is not needed."}))


def cmd_registry_check(argv):
    paths = argv or []
    if not paths:
        print("registry-check: pass STATE.md paths to check")
        return
    for p in paths:
        if not os.path.isfile(p):
            print("%s: missing" % p)
            continue
        age = (datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 86400
        live = []
        for line in open(p, encoding="utf-8", errors="replace"):
            s = line.strip()
            if s.startswith("- ") and "agent" in s.lower() and "LANDED" not in s and "ADOPTED" not in s:
                live.append(s[:90])
        if live and age > 2:
            print("%s: %d live-looking fences in a %.1f-day-old file:" % (p, len(live), age))
            for l in live:
                print("   " + l)
        else:
            print("%s: clean (%d live-looking fences, %.1fd old)" % (p, len(live), age))


def cmd_fence_lint(argv):
    """PreToolUse dispatch aid: show live fences near cwd so no writer launches
    into an occupied file set. Cheap, read-only, exit 0 always. Reads the hook
    payload from stdin when no path argument is given."""
    cwd = argv[0] if argv else ""
    if not cwd and not sys.stdin.isatty():
        try:
            cwd = (json.load(sys.stdin) or {}).get("cwd", "")
        except Exception:
            cwd = ""
    cwd = cwd or os.getcwd()
    hits = []
    unread = []
    # The project's own STATE.md plus any registries the user declared via
    # BROTHERSBE_REGISTRIES (colon-separated glob patterns).
    #
    # Discovery accounts for what it could not see: a raw glob returns FEWER
    # paths, not an error, over a directory it cannot enter, so a denied
    # registry directory silently shrank the fence list and two writers ended
    # up in one file set. This aid's one job is that no writer launches into
    # an occupied file set, and an occupied file set it cannot READ has to be
    # treated as occupied, not as empty. Same rule, same shared helper, as the
    # sibling scorer check that already adopted it.
    pats = [os.path.join(cwd, "STATE.md")]
    # os.pathsep, not a literal colon: a Windows path starts `C:\`, so a colon
    # split tears the drive letter off and the registry is never opened. Same
    # correction as tools/sbe_fence_hook.py; tools/sbe_score.py already had it.
    pats += [p.strip() for p in os.environ.get("BROTHERSBE_REGISTRIES", "").split(os.pathsep) if p.strip()]
    for pat in pats:
        paths, denied = glob_with_denials(pat)
        unread.extend(denied)
        for p in paths:
            try:
                lines = list(open(p, encoding="utf-8", errors="replace"))
            except OSError:
                unread.append(p)
                continue
            for line in lines:
                s = line.strip()
                if s.startswith("- ") and "agent" in s.lower() and "LANDED" not in s and "ADOPTED" not in s:
                    tier = "" if re.search(r"\btier T[123]\b", s) else " [NO-TIER: declare T1/T2/T3]"
                    hits.append("%s: %s%s" % (os.path.basename(p), s[:100], tier))
    if hits:
        print("LIVE FENCES (fence-then-dispatch; overlap means queue):")
        for h in hits[:8]:
            print("  " + h)
    if unread:
        u = sorted(set(unread))
        print("fence-lint: %d registry path(s) could not be read (%s); any fence in them is "
              "INVISIBLE to this list, so treat those file sets as occupied rather than free"
              % (len(u), ", ".join(u[:4])))
    if not hits and not unread:
        print("fence-lint: no live fences found under %s" % cwd)


# ---------------------------------------------------------------------------
# Update awareness. This skill is a LAW: when it changes, the change has to be
# read, not silently absorbed. So the notifier does two jobs: tell you an update
# exists, and tell you once when your installed law actually changed.
#
# HARD CONSTRAINT: no network call and no subprocess, ever. Both properties are
# audited and are claimed publicly in SECURITY.md. So this reads git refs as
# ordinary files and never runs git itself. The cost is that "an update exists"
# is only known after something else has fetched; the staleness warning covers
# the rest, by telling you when your copy is simply old.
# ---------------------------------------------------------------------------
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STALE_AFTER_DAYS = 30
#: NAMESPACED TO THIS TOOL, and the name is the whole point. This file records
#: which commit of THIS skill the operator last saw, so the next run can say
#: "your installed law changed, read the diff". The bare name
#: "installed-skill-version" is not unique to this tool: the sibling
#: BrotherModeUp keeps the same state, under the same basename, in the same
#: <vault>/99-System/telemetry directory (PARITY.md names the mechanisms the two
#: share). The vault path is the operator's own choice and nothing here reserves
#: it, so pointing both tools at ONE vault is a supported setup, not a misuse.
#: Under the bare name each tool then overwrites the other's stamp every session,
#: and both report a version change on the next start, forever, reading the other
#: tool's commit hash as their own drift. The one mechanism meant to catch a real
#: change becomes a permanent false alarm, which is how a reader learns to ignore
#: it. Observed on a real machine 2026-07-31, the day both vaults were pointed at
#: one directory. Held by
#: TestVersionMark::test_the_version_marker_is_not_shared_with_the_sibling_skill.
VERSION_MARK = os.path.join(TEL_DIR, "installed-skill-version-brothersbe")


def _read_first_line(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return ""


def _git_ref(git_dir, ref):
    """Resolve a ref to a sha by reading files only: loose ref first, then
    packed-refs. Returns "" when the ref cannot be resolved."""
    sha = _read_first_line(os.path.join(git_dir, ref))
    if sha and not sha.startswith("ref:"):
        return sha
    packed = os.path.join(git_dir, "packed-refs")
    try:
        for line in open(packed, "r", encoding="utf-8", errors="replace"):
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    except OSError:  # sbe: allow-silent optional filesystem read; absence handled by the caller
        pass
    return ""


def _resolve_git_dirs(skill_dir):
    """Return (head_dir, refs_dir, why_not) for skill_dir's git checkout.

    THREE values, never two, and deliberately so: this file's own honesty
    meta-test reads any 2-tuple returned by a module under tools/ as a
    (verdict, evidence) pair and refuses one it cannot prove never says
    PASS. This helper resolves paths, it grades nothing, and the third
    value carries the reason the first two are empty so a caller can say
    what it could not read instead of going quiet.

    A normal clone's `.git` is a directory and serves as both: HEAD and
    refs/* live together there. A LINKED WORKTREE's top-level `.git` is a
    FILE containing one line, `gitdir: <path>` (absolute, or relative to
    skill_dir), pointing at a directory under the main repo's
    `.git/worktrees/<name>` that holds that worktree's own HEAD, but does
    NOT duplicate refs/heads or refs/remotes: those stay in the COMMON dir,
    named by a `commondir` file inside the per-worktree directory (typically
    `../..`). The old check here was `os.path.isdir(skill_dir/.git)`, true
    only for a normal clone and false for every linked worktree, so the
    whole update command returned in total silence there: exit 0, no
    output, no marker written, no warning. Reproduced this session against
    a real `git worktree add` checkout, not a guess."""
    git_path = os.path.join(skill_dir, ".git")
    if os.path.isdir(git_path):
        return git_path, git_path, ""
    if os.path.isfile(git_path):
        line = _read_first_line(git_path)
        if line.startswith("gitdir:"):
            target = line[len("gitdir:"):].strip()
            if target and not os.path.isabs(target):
                target = os.path.normpath(os.path.join(skill_dir, target))
            if target and os.path.isdir(target):
                common = _read_first_line(os.path.join(target, "commondir"))
                refs_dir = target
                if common:
                    common_dir = common if os.path.isabs(common) \
                        else os.path.normpath(os.path.join(target, common))
                    if os.path.isdir(common_dir):
                        refs_dir = common_dir
                return target, refs_dir, ""
            return "", "", ("%s names a gitdir at %s that is not a directory"
                             % (git_path, target))
        return "", "", "%s is a file but carries no gitdir: line" % git_path
    return "", "", "no .git directory and no .git file at %s" % skill_dir


def cmd_check_update():
    head_dir, refs_dir, why_not = _resolve_git_dirs(SKILL_DIR)
    if not head_dir:
        # Genuinely not a git checkout (or a worktree pointer this could not
        # follow): SAY so, once, rather than return in total silence. The
        # old behavior here was indistinguishable from "everything is up to
        # date" and from "the update check never ran at all".
        print("BROTHERSBE UPDATE: the update check is skipped: %s" % why_not)
        return
    head = _read_first_line(os.path.join(head_dir, "HEAD"))
    branch = head[5:].strip() if head.startswith("ref:") else ""
    local = _git_ref(refs_dir, branch) if branch else head
    if not local:
        return
    remote = ""
    if branch.startswith("refs/heads/"):
        name = branch[len("refs/heads/"):]
        remote = _git_ref(refs_dir, "refs/remotes/origin/" + name)

    # (a) an update is already fetched and waiting
    if remote and remote != local:
        # Direction is not knowable without reading git objects, so the message
        # and the command are both symmetric: left-right marks which side each
        # commit is on, and works whether you are behind, ahead, or diverged.
        print("BROTHERSBE UPDATE: installed copy (%s) and fetched origin (%s) "
              "differ. See which side each commit is on, then update:"
              % (local[:7], remote[:7]))
        print("  git -C %s log --oneline --left-right %s...%s"
              % (SKILL_DIR, local[:7], remote[:7]))
        print("  git -C %s pull    # then re-read the sections that changed" % SKILL_DIR)
    else:
        # (b) no fetched update, but the copy may simply be old
        try:
            age = (datetime.datetime.now().timestamp()
                   - os.path.getmtime(os.path.join(SKILL_DIR, "SKILL.md"))) / 86400
        except OSError:
            age = 0
        if age > STALE_AFTER_DAYS:
            print("BROTHERSBE UPDATE: your copy of the law is %d days old. Check "
                  "for updates: git -C %s pull" % (age, SKILL_DIR))

    # (c) the law changed under you since the last session that looked: say so
    # ONCE, because a changed law must be read, not silently inherited.
    known = _read_first_line(VERSION_MARK)
    if known and known != local:
        print("BROTHERSBE: the skill changed since your last session (%s -> %s). "
              "Read the diff before relying on it:" % (known[:7], local[:7]))
        print("  git -C %s log --oneline %s..%s" % (SKILL_DIR, known[:7], local[:7]))
    if known != local:
        try:
            _makedirs_telemetry()
            with open(VERSION_MARK, "w") as f:
                f.write(local + "\n")
        except OSError:  # sbe: allow-silent boundary handler in a non-blocking hook; the miss surfaces as absent data, never as a false pass
            pass


def _intent_path(cwd):
    return os.path.join(TEL_DIR, "intent-%s.log" % _project_of(cwd))


def cmd_intent(argv):
    """Append one write-ahead intent line. Called by the session BEFORE a risky or
    long action so that if the session dies mid-action, the intent is already on
    disk. Pure: appends to a file, nothing else."""
    text = " ".join(argv).strip()
    if not text:
        print("usage: intent \"next: <what>, because <why>\"")
        return
    atomic_append_text(_intent_path(os.getcwd()), now_iso() + "  " + text)
    print("intent logged")


def atomic_append_text(path, line):
    # A line-delimited RECORD is one line by construction, the same rule the
    # report tools enforce with one_line(): stripping only a TRAILING newline
    # let a caller-supplied newline write a second timestamped record the tool
    # never stamped, and the last-line reader then quoted the forged record as
    # the operator's own intent. Every internal break renders as a visible
    # escape instead, so the record stays one line and the injection stays
    # readable as an injection.
    flat = re.sub("[\r\n\v\f\x85\u2028\u2029]+", "\\\\n", str(line).rstrip("\r\n"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = (flat + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)


def _last_intent(cwd):
    try:
        lines = [l.rstrip("\n") for l in open(_intent_path(cwd), encoding="utf-8", errors="replace") if l.strip()]
        return lines[-1] if lines else ""
    except OSError:
        return ""


def _project_of(cwd):
    # basename ALONE collides across ~/client-a/backend and ~/client-b/backend, which
    # would let one project's resume/intent files be read as another's. Suffix a short
    # hash of the absolute path so the identity is unique per repository root.
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(cwd.rstrip("/"))) or "session"
    h = hashlib.sha1(os.path.abspath(cwd).encode("utf-8", "replace")).hexdigest()[:6]
    return "%s-%s" % (base, h)


def _resume_path(cwd):
    return os.path.join(TEL_DIR, "last-resume-%s.md" % _project_of(cwd))


def _brief_header(last_intent):
    """The part of the brief that carries no transcript text: the intent log
    is a separate write-ahead mechanism, not gated by the transcript switch,
    so it still opens this document whenever the brief is written at all."""
    return ["# Resume brief (auto-written before compaction)",
            "",
            "A compaction just erased the working context. The git autosave holds your",
            "files (refs/brothersbe/autosave/, one ref per worktree; `python3 tools/sbe_autosave.py recover`",
            "prints yours); this holds the THREAD. Read it, re-read",
            "STATE.md, then continue where this leaves off.",
            "",
            "## Next intent (write-ahead: what this session was about to do)",
            (last_intent or "(no write-ahead intent was logged)"),
            ""]


def _write_brief(cwd, lines):
    """Write the brief owner-only: this is sensitive recent context, like
    corrections.jsonl. Owner-only means REQUESTED at mode 600; on platforms
    that ignore POSIX modes (Windows writes no ACL for this) it is a request,
    not enforcement, which docs/KNOWN-LIMITS.md records as a decision. Same
    boundary handler this code has always carried."""
    try:
        _makedirs_telemetry()
        rp = _resume_path(cwd)
        fd = os.open(rp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            _write_all(fd, ("\n".join(lines) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:  # sbe: allow-silent boundary handler in a non-blocking hook; the miss surfaces as absent data, never as a false pass
        pass


def cmd_precompact_brief():
    """Distill the dying session into a forward-looking resume brief. The git
    autosave preserves WHAT you had (files); this preserves WHERE you were and
    WHY, which is the part a resumed model would otherwise have to re-derive.
    Mechanical extraction, not synthesis: it hands the next session the raw recent
    history so the model reconstructs the thread from complete material instead of
    a stale note. Pure python: reads the transcript file, writes one markdown file;
    no subprocess, no network."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    tp = payload.get("transcript_path") or ""
    cwd = payload.get("cwd") or os.getcwd()
    if not tp or not os.path.isfile(tp):
        return
    # OPT-IN, MATCHING WAVE 6. This used to write a brief either way: real
    # content when the switch was on, a "[REDACTED]" placeholder in its place
    # when off, so a resumed session always found the same document in the
    # same spot. That made the resume brief the one capture path still writing
    # a file by default, unlike metrics and corrections, which write nothing
    # until switched on. Flip decision (founder, 2026-07-29): off now means no
    # file at all. An absent file must never read as a quiet session, so the
    # one thing this path still does when the switch is off is name the switch,
    # once, on stderr: the same "who kept this off and why" sentence the other
    # categories print, routed to stderr because PreCompact fires as the
    # context that would carry a stdout line is being erased.
    why = capture_off_reason("transcript")
    if why:
        print("sbe_telemetry: no resume brief written: %s" % why, file=sys.stderr)
        return
    last_user = ""
    assistant_text = []   # recent reasoning/decision snippets
    tools = []            # recent tool actions, as short descriptors
    try:
        f = open(tp, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for raw in f:
            try:
                o = json.loads(raw)
            except Exception:
                continue  # sbe: allow-silent aggregate reader over a live transcript; a corrupt line must not lose the rest, and nothing here rewrites the file
            t = o.get("type")
            m = o.get("message") or {}
            if t == "user" and not o.get("isMeta"):
                c = m.get("content")
                txt = c if isinstance(c, str) else None
                if isinstance(c, list):
                    if not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                        txt = "\n".join(b.get("text", "") for b in c
                                        if isinstance(b, dict) and b.get("type") == "text")
                if txt and txt.strip() and "<system-reminder>" not in txt:
                    last_user = txt.strip()
            elif t == "assistant":
                for b in (m.get("content") or []):
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        assistant_text.append(b["text"].strip())
                    elif b.get("type") == "tool_use":
                        name = b.get("name", "")
                        inp = b.get("input") or {}
                        if name == "Bash":
                            desc = "Bash: " + (inp.get("command", "")[:100])
                        elif name in ("Edit", "Write", "Read"):
                            desc = name + ": " + str(inp.get("file_path", ""))
                        else:
                            desc = name
                        tools.append(desc)
    # Keep only the recent tail of each stream, and REDACT every stream: this file
    # holds your own recent words and commands, which can carry a pasted secret, a
    # token in a command, or a customer name. Same discipline as corrections.jsonl;
    # the earlier fix there had to be applied here too (a fix in one place must be
    # grepped for everywhere with the same shape).
    assistant_text = [redact(a)[0] for a in assistant_text[-4:]]
    tools = [redact(d)[0] for d in tools[-10:]]
    last_user = redact(last_user)[0]
    last_intent = redact(_last_intent(cwd))[0]
    lines = _brief_header(last_intent) + [
        "## The last thing the operator asked",
        (last_user[:600] or "(none captured)"),
        "",
        "## What was being done (recent reasoning)"]
    for a in assistant_text:
        lines.append("- " + a[:300].replace("\n", " "))
    lines.append("")
    lines.append("## Recent actions (last %d)" % len(tools))
    for d in tools:
        lines.append("- " + d)
    lines.append("")
    _write_brief(cwd, lines)


def cmd_compact_hint():
    """Read the SessionStart hook payload from stdin. When the session resumed
    from a context compaction (source == "compact"), the model has just lost the
    detail of what it was doing, so point it at the autosave and at STATE.md. This
    is the READ side of work-preservation; tools/sbe_autosave.py is the write side. Pure
    by design: this never runs git (that would break the audited no-subprocess
    property); it only prints the recovery command for the model or human to run."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if (payload or {}).get("source") != "compact":
        return
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print("BROTHERSBE: resumed after a compaction. Your files are autosaved "
          "(python3 %s/tools/sbe_autosave.py recover) and the thread is below. Read it, "
          "re-read STATE.md and git status, then continue." % skill_dir)
    # The thread: the resume brief written at the moment of death (the WHY/where),
    # which the git snapshot does not carry.
    rp = _resume_path((payload or {}).get("cwd") or os.getcwd())
    try:
        with open(rp, "r", encoding="utf-8", errors="replace") as f:
            print("")
            print(f.read().strip())
    except OSError:  # sbe: allow-silent boundary handler in a non-blocking hook; the miss surfaces as absent data, never as a false pass
        pass


def _read_head(path, limit_chars=6000):
    try:
        return open(path, encoding="utf-8", errors="replace").read()[:limit_chars]
    except OSError:
        return ""


def cmd_handoff(argv):
    """Assemble a single shareable markdown for the solo operator's occasional
    hand-off to a small teammate. It is teammate-facing, so EVERY section is run
    through redact() before it is written: a handoff is exactly where a stray
    secret would travel furthest. Pure python (reads vault files, writes one file);
    no subprocess, no network, preserving the audited properties."""
    projects_dir = os.path.join(VAULT, "10-Projects")
    name = argv[0] if argv else ""
    if not name:
        try:
            avail = sorted(d for d in os.listdir(projects_dir)
                           if os.path.isdir(os.path.join(projects_dir, d)))
        except OSError:
            avail = []
        print("usage: handoff <project>. available: %s" % (", ".join(avail) or "(none)"))
        return
    base = os.path.join(projects_dir, name)
    if not os.path.isdir(base):
        print("handoff: no vault project named %r under %s" % (name, projects_dir))
        return
    parts = ["# Handoff: %s" % name, "",
             "A shareable summary of this project for a teammate. Assembled from the",
             "vault and secret-redacted. It is a snapshot, not the living record.", ""]
    overview = _read_head(os.path.join(base, "Overview.md"))
    if overview:
        parts += ["## Overview", redact(overview)[0], ""]
    openitems = _read_head(os.path.join(base, "Open-Items.md"))
    if openitems:
        parts += ["## Open items", redact(openitems)[0], ""]
    # latest session log by mtime
    sessions = glob.glob(os.path.join(base, "Sessions", "*.md"))
    if sessions:
        latest = max(sessions, key=lambda f: os.path.getmtime(f))
        parts += ["## Most recent session (%s)" % os.path.basename(latest),
                  redact(_read_head(latest, 4000))[0], ""]
    # last few outcome lines
    oc = _read_head(os.path.join(base, "OUTCOMES.md"), 20000)
    if oc:
        tail = [l for l in oc.splitlines() if l.strip()][-6:]
        parts += ["## Recent outcomes", redact("\n".join(tail))[0], ""]
    out = os.path.join(os.getcwd(), "handoff-%s.md" % re.sub(r"[^A-Za-z0-9_.-]", "_", name))
    try:
        with open(out, "w") as f:
            f.write("\n".join(parts) + "\n")
        print("handoff written (secret-redacted): %s" % out)
        print("review it before sharing; it is a snapshot, redaction is best-effort.")
    except OSError as e:
        print("handoff: could not write (%r)" % (e,))


def cmd_purge_corrections(argv):
    """Delete captured correction candidates. They are excerpts of your own
    messages; purge them once the weekly review has distilled what it needs, or
    at any time. Prints exactly what it will remove before removing it."""
    rows = read_jsonl(CORRECTIONS)
    if not rows:
        print("purge-corrections: nothing to purge (%s)" % CORRECTIONS)
        return
    if "--yes" not in argv:
        print("purge-corrections: %d candidate(s) in %s" % (len(rows), CORRECTIONS))
        print("  oldest: %s   newest: %s" % (rows[0].get("ts", "?"), rows[-1].get("ts", "?")))
        print("  re-run with --yes to delete the file.")
        return
    try:
        os.remove(CORRECTIONS)
        print("purge-corrections: removed %d candidate(s) from %s" % (len(rows), CORRECTIONS))
    except OSError as e:
        print("purge-corrections: could not remove (%r)" % (e,))


# ---------------------------------------------------------------------------
# WHAT IS STORED, WHERE, AND HOW TO GET RID OF IT. A capture switch is only half
# an answer: the other half is being able to see exactly what is on disk, take a
# copy of it, and delete it. All three read the SAME inventory, so a file that
# `data-show` lists is a file `data-export` copies and `data-purge` removes, and
# none of the three can quietly know about a file the others do not.
# ---------------------------------------------------------------------------
def stored_inventory():
    """[(category, path, what that file can hold)] for every file this tool writes.

    Built from this module's own path constants and from a glob of the telemetry
    directory, rather than from a hand-kept list: a per-project resume brief or
    intent log is named after the project, so a fixed list would report the two
    projects somebody remembered and miss the rest.
    """
    items = [
        ("metrics", LEDGER,
         "one row per recorded session: token counts, message and tool counts, model "
         "names, duration, end reason, and the basename of the working directory"),
        ("metrics", RATINGS, "felt-outcome ratings you typed with `rate`: score, task, note"),
        ("metrics", REVIEWS, "weekly review markers you typed with `review-mark`"),
        ("corrections", CORRECTIONS,
         "excerpts of your own messages that matched the correction pattern, capped at "
         "400 characters and 5 per session, secret-redacted, owner-only where "
         "the platform enforces that mode"),
        ("housekeeping", VERSION_MARK, "the git sha of the installed skill at the last check"),
    ]
    patterns = (
        ("transcript", "last-resume-*.md",
         "resume brief: your last message, recent assistant reasoning and recent tool "
         "commands, secret-redacted"),
        ("operator-entered", "intent-*.log", "write-ahead intent lines you typed with `intent`"),
        ("metrics", "*.unlocked-appends",
         "timestamp and process id of an append that ran without the writer lock"),
        ("autosave", "autosave.log", "one line per autosave snapshot, skip or lock event"),
        ("autosave", "autosave-exclusions.log",
         "paths the autosave content scan kept out of a snapshot, and why (paths and "
         "reasons only, never the matched content)"),
    )
    for category, pattern, what in patterns:
        for p in sorted(glob.glob(os.path.join(TEL_DIR, pattern))):
            items.append((category, p, what))
    return items


def _stored_measure(path):
    """(exists, bytes, records, mode string, note). Never raises: a file that
    cannot be measured is reported as unmeasured by name, because an inventory
    that drops what it could not open is the absent-evidence defect."""
    if not os.path.exists(path):
        return False, 0, 0, "", ""
    try:
        st = os.stat(path)
        mode = "%03o" % stat.S_IMODE(st.st_mode)
        records = 0
        for line in open(path, encoding="utf-8", errors="replace"):
            if line.strip():
                records += 1
        return True, st.st_size, records, mode, ""
    except OSError as e:
        return True, 0, 0, "", "could not be measured (%s)" % (e.strerror or e)


DATA_SHOW_USAGE = (
    "usage: data-show\n"
    "  reads: every file this tool can write, under <vault>/99-System/telemetry\n"
    "    plus the operator-model file, to report what is stored.\n"
    "  writes: nothing.\n"
    "  flags:\n"
    "    -h, --help        print this and exit 0"
)

DATA_EXPORT_USAGE = (
    "usage: data-export [--out PATH]\n"
    "  reads: the same inventory data-show reports, file content included.\n"
    "  writes: one JSON bundle holding that content, requested at mode 600\n"
    "    (owner-only where the platform enforces that mode), default\n"
    "    ./brothersbe-telemetry-export.json, or PATH. Nothing under the\n"
    "    vault is touched.\n"
    "  flags:\n"
    "    --out PATH        write the bundle to PATH instead of the default\n"
    "    -h, --help        print this and exit 0 without writing a bundle"
)

DATA_PURGE_USAGE = (
    "usage: data-purge [--category NAME] [--yes]\n"
    "  reads: the same inventory data-show reports, to find what exists.\n"
    "  writes: nothing without --yes; with --yes, deletes the files found.\n"
    "  flags:\n"
    "    --category NAME   limit to one category (see data-show for the list)\n"
    "    --yes             actually delete; omit for a dry run naming what would go\n"
    "    -h, --help        print this and exit 0 without deleting anything"
)


def _data_flags(subcmd, argv, usage, known):
    """-h/--help and unknown-flag handling shared by the three data-* commands,
    run before any of them reads or writes anything under the vault.

    `known` is the set of flags (besides -h/--help) that subcmd accepts. Any
    other token starting with "-" is refused rather than silently ignored,
    because an ignored typo on data-export or data-purge is how `--help` ran
    a real export and how a mistyped `--category` could purge everything
    instead of the one category the operator named.

    Returns an exit code (0 for --help, 2 for an unrecognized flag) the
    caller should stop and return right away, or None if argv passed and the
    caller should carry on with its own parsing.
    """
    if "-h" in argv or "--help" in argv:
        print(usage)
        return 0
    for a in argv:
        if a.startswith("-") and a not in known:
            print(usage)
            print("%s: unrecognized flag %r; refusing rather than running past it"
                  % (subcmd, a))
            return 2
    return None


def cmd_data_show(argv):
    """Print the capture policy in force and every file it can write."""
    code = _data_flags("data-show", argv, DATA_SHOW_USAGE, set())
    if code is not None:
        return code
    print("BROTHERSBE STORED DATA (vault %s)" % vault_label())
    for category in CAPTURE_CATEGORIES:
        print("  policy: %s" % capture_state_line(category))
    read = absent = unmeasured = 0
    for category, path, what in stored_inventory():
        exists, size, records, mode, note = _stored_measure(path)
        if not exists:
            absent += 1
            print("  [%s] %s: absent, so nothing is stored at this path" % (category, path))
            continue
        if note:
            unmeasured += 1
            print("  [%s] %s: %s" % (category, path, note))
            continue
        read += 1
        print("  [%s] %s: %d record(s), %d bytes, mode %s -- %s"
              % (category, path, records, size, mode, what))
    print("read %d file(s), %d path(s) absent, %d that could not be measured, under %s."
          % (read, absent, unmeasured, TEL_DIR))
    print("This lists this vault only. A backup, a mirror or a sync client may hold copies "
          "of any of it, and nothing here can see those.")


def cmd_data_export(argv):
    """Write one JSON bundle of everything stored, requested at mode 600
    (owner-only where the platform enforces that mode, a request elsewhere;
    the printed line below says the same)."""
    code = _data_flags("data-export", argv, DATA_EXPORT_USAGE, {"--out"})
    if code is not None:
        return code
    out = "brothersbe-telemetry-export.json"
    it = iter(argv)
    for a in it:
        if a == "--out":
            out = next(it, out)
    out = os.path.abspath(out)
    bundle = {"generated": now_iso(), "vault": vault_label(), "telemetryDir": TEL_DIR,
              "policy": {c: capture_state_line(c) for c in CAPTURE_CATEGORIES},
              "files": []}
    read = unread = 0
    for category, path, what in stored_inventory():
        exists, size, records, mode, note = _stored_measure(path)
        entry = {"path": path, "category": category, "holds": what, "exists": exists,
                 "bytes": size, "records": records, "mode": mode}
        if not exists:
            bundle["files"].append(entry)
            continue
        try:
            entry["content"] = open(path, encoding="utf-8", errors="replace").read()
            read += 1
        except OSError as e:
            entry["unreadable"] = e.strerror or str(e)
            unread += 1
        bundle["files"].append(entry)
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            _write_all(fd, (json.dumps(bundle, indent=2, sort_keys=True) + "\n").encode())
        finally:
            os.close(fd)
    except OSError as e:
        print("data-export: could not write %s (%s); nothing was exported"
              % (out, e.strerror or e))
        return
    try:
        out_mode = "%03o" % stat.S_IMODE(os.stat(out).st_mode)
    except OSError:
        out_mode = "unknown"
    print("data-export: wrote %s (mode %s: owner-only intended, but this reports what the "
          "platform gave, it does not promise enforcement on platforms that ignore POSIX "
          "modes) from %d file(s) under %s; %d could not be read; %d path(s) recorded as absent"
          % (out, out_mode, read, TEL_DIR, unread,
             len([f for f in bundle["files"] if not f["exists"]])))
    print("data-export: this file holds the stored data itself, so treat it as sensitive; "
          "redaction was applied at capture time and is best effort.")


def cmd_data_purge(argv):
    """Delete what is stored. Names it first, deletes on --yes, then re-checks
    the filesystem and reports anything that survived, because a purge that
    reports success from its own intention has proven nothing."""
    code = _data_flags("data-purge", argv, DATA_PURGE_USAGE, {"--category", "--yes"})
    if code is not None:
        return code
    only = ""
    it = iter(argv)
    for a in it:
        if a == "--category":
            only = next(it, "")
    items = [(c, p, w) for (c, p, w) in stored_inventory() if not only or c == only]
    if only and not items:
        print("data-purge: no category named %r; categories are %s"
              % (only, ", ".join(sorted({c for c, _p, _w in stored_inventory()}))))
        return
    present = []
    for category, path, _what in items:
        exists, size, records, _mode, note = _stored_measure(path)
        if exists:
            present.append((category, path, records, size, note))
    if not present:
        print("data-purge: nothing to purge; %d path(s) checked under %s and none exist"
              % (len(items), TEL_DIR))
        return
    if "--yes" not in argv:
        for category, path, records, size, note in present:
            print("  [%s] %s: %d record(s), %d bytes%s"
                  % (category, path, records, size, ", " + note if note else ""))
        print("data-purge: %d file(s) above would be deleted. Re-run with --yes." % len(present))
        return
    removed = failed = survived = 0
    for category, path, records, _size, _note in present:
        try:
            os.remove(path)
        except OSError as e:
            failed += 1
            print("  FAILED [%s] %s: %s" % (category, path, e.strerror or e))
            continue
        if os.path.exists(path):
            survived += 1
            print("  STILL PRESENT [%s] %s: the remove reported success and the file is still "
                  "on disk" % (category, path))
            continue
        removed += 1
        print("  removed [%s] %s (%d record(s))" % (category, path, records))
    print("data-purge: %d file(s) removed, %d failed, %d still present, of %d checked under %s."
          % (removed, failed, survived, len(items), TEL_DIR))
    if failed or survived:
        print("data-purge: this run did NOT delete everything it named; the lines above say "
              "which files and why.")


def cmd_prediction_audit():
    c = prediction_counts()
    print("prediction ledger: %d sealed, %d scored, %d hits (%s)"
          % (c["sealed"], c["scored"], c["hits"], OPERATOR_MODEL))
    if c["sealed"] == 0:
        print("AUDIT FLAG: zero sealed predictions; section 14 requires sealing BEFORE recommendations.")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    argv = sys.argv[2:]
    try:
        if cmd == "outcomes-append":
            cmd_outcomes_append()
        elif cmd == "migrate":
            cmd_migrate()
        elif cmd == "scorecard":
            cmd_scorecard()
        elif cmd == "rate":
            cmd_rate(argv)
        elif cmd == "review-mark":
            cmd_review_mark(argv)
        elif cmd == "startup-nags":
            cmd_startup_nags()
        elif cmd == "stop-warn":
            cmd_stop_warn()
        elif cmd == "registry-check":
            cmd_registry_check(argv)
        elif cmd == "fence-lint":
            cmd_fence_lint(argv)
        elif cmd == "intent":
            cmd_intent(argv)
        elif cmd == "precompact-brief":
            cmd_precompact_brief()
        elif cmd == "compact-hint":
            cmd_compact_hint()
        elif cmd == "check-update":
            cmd_check_update()
        elif cmd == "handoff":
            cmd_handoff(argv)
        elif cmd == "purge-corrections":
            cmd_purge_corrections(argv)
        elif cmd == "data-show":
            sys.exit(cmd_data_show(argv) or 0)
        elif cmd == "data-export":
            sys.exit(cmd_data_export(argv) or 0)
        elif cmd == "data-purge":
            sys.exit(cmd_data_purge(argv) or 0)
        elif cmd == "prediction-audit":
            cmd_prediction_audit()
        elif cmd == "speed":
            cmd_speed()
        elif cmd == "dedup":
            cmd_dedup()
        else:
            print(__doc__.strip())
    except Exception as e:
        print("sbe_telemetry: swallowed error (never blocks): %r" % (e,))
    sys.exit(0)


if __name__ == "__main__":
    main()
