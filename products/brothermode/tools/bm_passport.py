#!/usr/bin/env python3
"""The change passport, producer half: what BrotherMode deposits at the seam.

WHAT THE SEAM IS. The change passport is the single crossing between
execution provenance and assurance (docs/specs/2026-08-15-change-passport-
seam.md in the BrotherSBE repository is the contract). BrotherMode produces
one, BrotherSBE consumes one, and it never travels back. This tool is the
producing end and the only place BrotherMode writes passport data.

THE TWO FIELDS THIS TOOL OWNS. Execution provenance owns exactly two of the
five fields the chain names, because only execution can see them:

  whoDidIt              the session and the claims it holds, plus the
                         accountable person by name (never a role)
  whereItCameFrom        the development method, named and not judged

A third field rides along because execution is also the only side that
knows what IT left undone, which assurance has no way to see:

  whatWasNotEstablished  gaps this producer itself hit while assembling the
                         deposit (a missing store, an unlabeled session, an
                         accountable person nobody named)

DIRECTION OF TRAVEL. This tool writes <root>/.sbe/passport.json and reads
NOTHING under .sbe/: no tasks.json, no evidence/, no existing passport.json.
Reaching across the seam to fill a field is the exact failure the seam
exists to prevent. Its only inputs are tools/bm_store.py's own store (read
only, never STATE.md, which is a generated view and not truth), `git config
user.name`, and the --accountable / --method flags.

A HOLLOW VALUE IS NOT A VALUE. An empty string, a whitespace-only string, an
empty list, or null all read as absence on the consuming side. A field this
tool cannot establish honestly is OMITTED from the deposit, never padded
with an empty placeholder to look filled, and the omission is explained on
stdout.

NOT A GATE. This tool reports and deposits, it never blocks on what it
FINDS: a store it could not read, a session nobody labeled and an
accountable person nobody named are all exit 0 with the gap written into
field 4, never a refusal. Three exits, stated exactly, because a tool whose
own documentation misreports its exit codes is the same overclaim this
product exists to catch:

  0  a deposit was written, whatever it could and could not establish
  1  the deposit itself could not be written to disk (the one case where
     running this tool and getting nothing is itself the finding)
  2  a usage error, or the NO-DATA verdict where no project root could be
     resolved at all, which is not a finding about a change but a failure
     to find a change to report on

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, mirroring every other tool in this directory.

Run it with --help for the usage line, which resolves the command to the
layout the reader actually installed rather than naming a tools/ directory
a packaged install does not have.
"""
import datetime
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

#: Written by the `generate` verb (see the section below build_deposit):
#: the full five-field passport, schema/change-passport.v1.json's own
#: shape, validated standalone by tools/bm_passport_validator.py. Distinct
#: from the older no-verb "deposit" behavior above, which writes only the
#: two fields execution provenance uniquely owns plus its own gap list.
#: Kept side by side rather than merged: the deposit behavior above is
#: already shipped, tested and wired into CI under its own CLI shape, and
#: replacing it outright would drop a working, reviewed producer for an
#: unproven rewrite. `generate` is additive; nothing about the no-verb
#: path below changes.
GENERATE_SCHEMA_MARKER = "change-passport/v1"

#: Fixed by the contract: the producer always deposits here unless --out
#: names a different path (used by this tool's own tests so they never
#: touch a real .sbe/ directory).
DEPOSIT_REL = os.path.join(".sbe", "passport.json")

#: Named and not judged, per the contract. Always true of anything this
#: tool produces a deposit for, so it is never hollow and never guessed;
#: --method overrides it when a session ran a different flow on top.
DEFAULT_METHOD = "BrotherMode"


# ---------------------------------------------------------------------------
# Root resolution. Honors --root when given; otherwise borrows bm_store's
# resolve_root() purely for that lookup. Mirrors tools/bm_idle.py's
# resolve_project_root() exactly, since that file is this tool's structural
# template.
# ---------------------------------------------------------------------------

def _load_bm_store():
    """Load bm_store.py by PATH: this tool is invoked with an arbitrary
    cwd, and a plain `import bm_store` would depend on whichever sys.path
    the caller happened to have. Never raises: an unimportable module
    degrades to an explicit reason string, not a crash."""
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_passport", path)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for bm_store.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _load_validator():
    """Load bm_passport_validator.py by PATH, same reason and same shape
    as _load_bm_store above: this tool's cwd is arbitrary, and generate
    self-validates (F6, cross-family review 2026-08-20) before it will
    write anything, so it needs the validator's own `validate()` loaded
    without assuming sys.path. The validator itself stays import-free of
    this repository (its own docstring: "imports NOTHING from this
    repository"); this file importing IT is the other direction, and adds
    no dependency the validator does not already accept from any caller."""
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_passport_validator.py")
        spec = importlib.util.spec_from_file_location(
            "bm_passport_validator_for_passport", path)
        if spec is None or spec.loader is None:
            return None, "could not build an import spec for bm_passport_validator.py"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def resolve_project_root(explicit_root):
    """Return (root, None) or (None, reason). `explicit_root` (--root)
    always wins and is checked directly against the filesystem; nothing
    else here reads bm_store at all in that case. Otherwise
    bm_store.resolve_root() is borrowed, which returns a (path, source)
    TUPLE that must be unpacked."""
    if explicit_root:
        candidate = os.path.realpath(os.path.expanduser(explicit_root))
        if not os.path.isdir(candidate):
            return None, "no such directory: %s" % candidate
        return candidate, None
    mod, err = _load_bm_store()
    if mod is None:
        return None, ("could not load bm_store.py to resolve the project "
                      "root (%s); pass --root explicitly" % err)
    root, _source = mod.resolve_root()
    if not root:
        return None, ("nothing anchors a BrotherMode project here (no "
                      "BROTHERMODE_ROOT, no marker directory, no git repo "
                      "found); pass --root explicitly")
    return root, None


# ---------------------------------------------------------------------------
# Hollow-value detection. Matches the consuming side's own definition
# (BrotherSBE tools/sbe_onepager.py's answered()) exactly: '' and None carry
# nothing, 0 and False are real answers, an empty list/dict carries nothing.
# Defined here rather than imported, because the producer never reads a
# BrotherSBE file at runtime; matching the CONTRACT'S definition by hand is
# the whole point of a seam.
# ---------------------------------------------------------------------------

def _answered(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0
    return True


#: The one prefix tools/bm_store.py's export policy (export_column,
#: withheld_marker) ever produces for a redacted value: "[WITHHELD: N
#: chars of founder text]" or "[WITHHELD: N-char digest]". Checked by
#: prefix rather than by importing bm_store's marker functions, because
#: this generator's contract is to read PUBLIC record surfaces only
#: (tools/bm_store.py's own ReadOnlyStore accessors) and comparing a
#: fixed, stable string prefix does not need a second import to do that.
_WITHHELD_PREFIX = "[WITHHELD:"


def _is_withheld(value):
    """True when `value` is bm_store.py's redaction placeholder rather
    than real content (F1, cross-family review 2026-08-20). generate's
    default (redacted) mode reads the store's own export policy, which
    withholds founder-typed free text; this generator must never print
    that placeholder AS content (a task title, a check command, a
    session's claim, an accountable name, a method name), only drop the
    affected line and name the gap instead."""
    return isinstance(value, str) and value.startswith(_WITHHELD_PREFIX)


# ---------------------------------------------------------------------------
# whoDidIt: the store's own active records and claims, never STATE.md.
# ---------------------------------------------------------------------------

def _store_snapshot(root):
    """Return (state, payload, store_path). state is one of 'absent',
    'unreadable', 'ok'. payload is a plain-English reason for 'absent' and
    'unreadable', or the dict from Store.dump(raw=True) for 'ok'.
    store_path is bm_store's own path for the store file when it could be
    computed, else None. Never raises: every exception this store can throw
    on open or on dump is caught and turned into 'unreadable' rather than
    left to crash this tool."""
    mod, err = _load_bm_store()
    if mod is None:
        return "unreadable", "bm_store.py could not be loaded (%s)" % err, None
    store_path = mod.store_path(root)
    try:
        store = mod.ReadOnlyStore(root)
    except mod.OwnershipRefused as e:
        if e.reason == "no-store":
            return "absent", "no BrotherMode store at %s" % store_path, store_path
        return ("unreadable",
                "the BrotherMode store at %s could not be opened: %s"
                % (store_path, e), store_path)
    except mod.StoreCorrupt as e:
        return ("unreadable",
                "the BrotherMode store at %s is corrupt: %s" % (store_path, e),
                store_path)
    except Exception as e:
        return ("unreadable",
                "the BrotherMode store at %s could not be opened: %s: %s"
                % (store_path, type(e).__name__, e), store_path)
    try:
        dump = store.dump(raw=True)
    except Exception as e:
        return ("unreadable",
                "the BrotherMode store at %s could not be read: %s: %s"
                % (store_path, type(e).__name__, e), store_path)
    finally:
        try:
            store.close()
        except Exception:  # sbe: allow-silent best-effort store.close() in a finally block; the open/read failures above already returned their own unreadable verdict
            pass
    return "ok", dump, store_path


def _session_claim_lines(dump):
    """Return (lines, total_files, skipped_records).

    One line per distinct session holding an ACTIVE record, each naming the
    files claimed across every active record that session holds. Sorted by
    session label so output is deterministic run to run.

    The two extra return values exist because of what this function DOES NOT
    read, which an adversarial review found could produce a confidently wrong
    field 2 with a clean field 4. `records.state` is one of active, parked,
    complete or adopted, and only active is read here. A session deposits at
    CLOSE, which is exactly when its own record is most likely already
    complete or parked, so a deposit assembled from active claims alone can
    name a concurrent, unrelated session as the author and say nothing was
    missing. Counting the skipped records here is what lets build_deposit say
    so in field 4 rather than stay silent. Same reason for the file total: a
    session line reading "0 files" is not provenance, and the caller cannot
    see that from the formatted strings."""
    records = dump.get("records") or []
    claims = dump.get("claims") or []
    files_by_uuid = {}
    for c in claims:
        u = c.get("lifecycle_uuid")
        files_by_uuid[u] = files_by_uuid.get(u, 0) + 1

    totals = {}  # session label -> [file_count, record_count]
    skipped = 0
    for r in records:
        if r.get("state") != "active":
            skipped += 1
            continue
        sid = (r.get("session_id") or "").strip()
        label = sid if sid else "(no session id recorded)"
        entry = totals.setdefault(label, [0, 0])
        entry[0] += files_by_uuid.get(r.get("lifecycle_uuid"), 0)
        entry[1] += 1

    lines = []
    total_files = 0
    for label in sorted(totals):
        n_files, n_records = totals[label]
        total_files += n_files
        lines.append(
            "session %s, holding claims on %d file%s across %d record%s"
            % (label, n_files, "" if n_files == 1 else "s",
               n_records, "" if n_records == 1 else "s"))
    return lines, total_files, skipped


def _unlabeled_active_count(dump):
    records = dump.get("records") or []
    return sum(1 for r in records
              if r.get("state") == "active"
              and not (r.get("session_id") or "").strip())


# ---------------------------------------------------------------------------
# Every git subprocess this tool runs goes through a SANITIZED environment
# (F4, cross-family review 2026-08-20): an inherited GIT_DIR, GIT_WORK_TREE,
# GIT_CONFIG_* (or the GIT_CONFIG_COUNT/KEY_N/VALUE_N triple), or a machine-
# wide url.*.insteadOf rewrite can redirect which repository, which remote
# URL, or which identity this tool reads, and `git -C root` does not
# neutralize any of them: -C only changes the STARTING directory git
# resolves paths from, it does nothing about which environment variables
# git itself consults afterward.
# ---------------------------------------------------------------------------

def _git_env():
    """A fresh environment for a git subprocess that starts from THIS
    process's own os.environ (PATH and HOME must survive, or git and any
    credential helper it needs cannot run at all), deletes every variable
    whose name starts with GIT_ (GIT_DIR, GIT_WORK_TREE, every GIT_CONFIG_*
    spelling including the COUNT/KEY_N/VALUE_N injection triple), then SETS
    GIT_CONFIG_NOSYSTEM=1 so a machine-wide /etc/gitconfig (an
    insteadOf rewrite, an injected user.name) cannot steer `git remote
    get-url origin`, `git diff` or `git rev-parse` either. LC_ALL and LANG
    are pinned to C so a git failure message this tool parses (_run_git's
    own result.stderr) has one fixed, English shape regardless of the
    caller's shell locale."""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


# ---------------------------------------------------------------------------
# The accountable person: a real source only. A store field never carries a
# person's name (records.owner is a ROLE, e.g. "builder", which is exactly
# what this field exists to replace), so the two honest sources are an
# explicit --accountable flag and `git config user.name`. Neither found
# means the field is omitted, not guessed.
# ---------------------------------------------------------------------------

def _git_user_name(root):
    try:
        result = subprocess.run(
            ["git", "-C", root, "config", "user.name"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", env=_git_env(), timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return name or None


def _accountable_line(root, explicit):
    """Return (line_or_None, gap_reason_or_None).

    A name taken from git STILL carries a gap reason, which is the whole
    point of field 4. `git config user.name` resolves the machine's global
    identity: it answers who owns this laptop, not who is accountable for
    THIS change, and it answers even when `root` is not a repository at all.
    Naming it without saying where it came from is the same overclaim as an
    empty field 4 on a store that could not be read."""
    if explicit and explicit.strip():
        return "accountable: %s" % explicit.strip(), None
    name = _git_user_name(root)
    if name:
        return ("accountable: %s" % name,
                "the accountable person was INFERRED from `git config "
                "user.name` at %s, which resolves the machine's global git "
                "identity; nobody stated an accountable person for this "
                "change (no --accountable flag was given)" % root)
    return None, (
        "the accountable person could not be established: no --accountable "
        "flag was given and `git config user.name` returned nothing at %s"
        % root)


# ---------------------------------------------------------------------------
# Assembling the deposit.
# ---------------------------------------------------------------------------

def build_deposit(root, accountable_flag, method_flag):
    """Return (deposit_dict, not_established_lines, notes_for_stdout)."""
    state, payload, store_path = _store_snapshot(root)

    session_lines = []
    not_established = []
    if state == "ok":
        session_lines, total_files, skipped = _session_claim_lines(payload)
        if not session_lines:
            not_established.append(
                "no active claim was found in the BrotherMode store at %s, "
                "so this producer cannot say which session or files this "
                "change belongs to" % store_path)
        if skipped:
            not_established.append(
                "%d record(s) in the BrotherMode store are not active "
                "(complete, parked or adopted) and were NOT read, so a "
                "session whose record already closed does not appear above; "
                "only claims active at the moment this deposit was written "
                "were read" % skipped)
        if session_lines and total_files == 0:
            not_established.append(
                "the active record(s) claim no files at all, so this deposit "
                "can name the session but cannot say which files the change "
                "touched")
        unlabeled = _unlabeled_active_count(payload)
        if unlabeled:
            not_established.append(
                "%d active record(s) in the BrotherMode store carry no "
                "session id, so their claims cannot be attributed to a "
                "session" % unlabeled)
    elif state == "absent":
        not_established.append(
            "%s, so session and claim history for this change could not be "
            "established" % payload)
    else:
        not_established.append(
            "%s, so session and claim history for this change could not be "
            "read" % payload)

    accountable_line, accountable_gap = _accountable_line(root, accountable_flag)
    if accountable_gap:
        not_established.append(accountable_gap)

    who_did_it = list(session_lines)
    if accountable_line:
        who_did_it.append(accountable_line)

    # Always true of this tool, never fabricated: it is a structural limit
    # of what an execution-side deposit can attest to, and it is the reason
    # whatWasNotEstablished can never legitimately read as empty even on a
    # perfectly healthy store.
    not_established.append(
        "this deposit reports BrotherMode's own store only (session ids "
        "and claimed files); it does not know whether the work was "
        "reviewed, tested, or verified, which is assurance's job to "
        "establish, not execution's")

    method = method_flag.strip() if (method_flag and method_flag.strip()) else DEFAULT_METHOD

    # The deposit names its own version, unconditionally and before any field.
    # The consumer half's contract is to read NOTHING from a deposit whose
    # version it cannot identify, rather than guess at field shapes. So an
    # unversioned deposit made this producer print "whoDidIt: carried" while
    # the consumer printed field 2 NO-DATA over the same file: both halves
    # honest about their own side, neither able to see the disagreement, and
    # the seam carrying nothing. A deposit that establishes no field still
    # carries this marker, because it is a v1 deposit that found nothing,
    # which the consumer can read and report field by field, not an
    # unreadable object it must discard whole.
    deposit = {"schema": GENERATE_SCHEMA_MARKER}
    if _answered(who_did_it):
        deposit["whoDidIt"] = who_did_it
    if _answered(method):
        deposit["whereItCameFrom"] = method
    if _answered(not_established):
        deposit["whatWasNotEstablished"] = not_established

    notes = ["store: %s%s" % (state, "" if state == "ok" else " (%s)" % payload)]
    for field in ("whoDidIt", "whereItCameFrom", "whatWasNotEstablished"):
        notes.append("%s: %s" % (field, "carried" if field in deposit
                                        else "NOT established, omitted"))
    return deposit, not_established, notes


# ---------------------------------------------------------------------------
# `generate`: the full five-field passport (schema/change-passport.v1.json).
#
# Reads ONLY the five ReadOnlyStore accessors named in the task brief
# (get_project, list_tasks, list_evidence, list_attribution,
# open_key_decisions), plus git (subprocess, explicit failure paths) and
# the --accountable / --method / --now flags. Never touches the fence
# tables (records, claims) the no-verb deposit above reads through
# store.dump(); "who did it" is rebuilt here from the attribution log
# instead, which is one of the five allowed surfaces.
# ---------------------------------------------------------------------------

#: Default gap lines, one per keyword. A category is DROPPED only when that
#: keyword (or its space-separated spelling) turns up in a task title or an
#: evidence row's kind/ref/note, i.e. the store shows it was actually
#: examined. This is a heuristic over free text, disclosed as one here and
#: in docs/PASSPORT.md, not a claim of certainty.
_DEFAULT_GAP_KEYWORDS = (
    ("regression", "no regression pass is recorded in the store for this change"),
    ("performance", "no performance measurement is recorded in the store for this change"),
    ("cross-device", "no cross-device check is recorded in the store for this change"),
    ("interface", "no interface behaviour review is recorded in the store for this change"),
    ("translat", "the translated copy was not read by anyone who speaks the "
                 "language, per this record"),
)


def _run_git(root, args):
    """(stdout_or_None, error_or_None). Never raises: a missing git binary,
    a bad ref, or a non-repository root all become a plain-English reason
    string, never a traceback and never a silently empty result. Runs in
    the sanitized environment (F4) and decodes with errors="replace" (F5):
    a POSIX filename or config value is not guaranteed to be valid UTF-8,
    and a decode failure here must degrade to a replacement character,
    never raise past this tool's own explicit-failure-path law."""
    try:
        result = subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", env=_git_env(), timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, "%s: %s" % (type(e).__name__, e)
    if result.returncode != 0:
        return None, (result.stderr.strip() or
                      "git %s exited %d" % (" ".join(args), result.returncode))
    return result.stdout, None


def _repo_identity(root):
    """The origin remote URL, or the directory name when there is none.
    Never raises and never returns an empty string: a directory name is
    always available as the last resort."""
    out, _err = _run_git(root, ["remote", "get-url", "origin"])
    if out and out.strip():
        return out.strip()
    return os.path.basename(os.path.abspath(root).rstrip(os.sep)) or root


def _resolve_commit(root, value):
    """(full_hash_or_None, error_or_None). Resolves `value` to its full,
    canonical commit hash via `git rev-parse --verify <value>^{commit}`
    (F6, cross-family review 2026-08-20). Before this, --base/--head were
    only checked for non-blank text and copied into change.baseCommit/
    change.headCommit verbatim: a typo such as "x" produced a named git-
    diff gap in field 4 but was STILL written as the commit identity,
    which the schema's own 7-40 hex pattern is the only thing standing
    between that typo and a passport later tools trust as a change
    boundary. ^{commit} additionally refuses a tag or a blob that is not
    itself a commit, and the canonical FULL hash this returns (never the
    caller's own short or symbolic spelling) is what goes into the
    passport, so two different spellings of the same commit produce the
    identical document."""
    out, err = _run_git(root, ["rev-parse", "--verify", "%s^{commit}" % value])
    if err is not None:
        return None, err
    full = (out or "").strip()
    if not full:
        return None, "git rev-parse returned nothing for %r" % (value,)
    return full, None


def _files_touched(root, base, head):
    """(sorted_files, gap_reason_or_None). A git failure is reported as a
    NAMED gap and an empty list, never a silent empty list with no
    explanation (the design law this function exists to satisfy)."""
    out, err = _run_git(root, ["diff", "--name-only", "%s..%s" % (base, head)])
    if err is not None:
        return [], ("git diff --name-only %s..%s failed: %s, so the files "
                    "touched by this change could not be established"
                    % (base, head, err))
    files = sorted(set(line.strip() for line in out.splitlines() if line.strip()))
    return files, None


#: F3's "short bounded backoff": three total attempts, waiting between
#: them only (nothing before the first). A held lock that clears inside a
#: couple of seconds is exactly the ordinary case (another BrotherMode
#: command mid-write); a lock that outlives all three is treated as real
#: contention, never as an absent or corrupt store.
_BUSY_RETRY_BACKOFF_SECONDS = (0.2, 0.5)


def _read_store_for_generate(root, project_id, include_sensitive):
    """(tasks, attribution, decisions, evidence_pairs, project, state,
    reason). state is one of 'ok', 'absent', 'unreadable', 'busy'; reason
    is None for 'ok' and a plain-English sentence otherwise. Opens the
    store, takes ONE read snapshot (F2, cross-family review 2026-08-20:
    tools/bm_store.py's ReadOnlyStore.read_snapshot()) and reads all five
    allowed accessors inside it, so a writer committing between, say,
    list_tasks and list_evidence cannot leave this generator reporting two
    different moments of the store as one passport.

    Busy handling is classified separately (F3): a 'database is locked/
    busy' condition surfaces as bm_store.OwnershipRefused('db-busy', ...)
    from either the open or a read inside the snapshot, and is retried up
    to len(_BUSY_RETRY_BACKOFF_SECONDS) + 1 times with the named short
    backoff between tries. Every OTHER open failure (absent, corrupt,
    unreadable) is NOT retried and folds into a field-4 gap exactly as
    before; contention that outlives every retry becomes state='busy',
    which the caller turns into a hard refusal (exit nonzero, nothing
    written) rather than a passport that silently degraded around a
    temporary lock."""
    mod, err = _load_bm_store()
    if mod is None:
        return ([], [], [], [], None, "unreadable",
                "bm_store.py could not be loaded (%s)" % err)
    store_path = mod.store_path(root)
    attempts = len(_BUSY_RETRY_BACKOFF_SECONDS) + 1
    last_busy = None
    for attempt in range(attempts):
        store = None
        try:
            store = mod.ReadOnlyStore(root)
            with store.read_snapshot():
                project = store.get_project(project_id, raw=include_sensitive)
                tasks = store.list_tasks(project_id, raw=include_sensitive)
                attribution = store.list_attribution(
                    project_id, limit=500, raw=include_sensitive)
                decisions = store.open_key_decisions(
                    project_id, raw=include_sensitive)
                evidence_pairs = []
                for task in tasks:
                    for row in store.list_evidence(
                            "task", task["task_id"], raw=include_sensitive):
                        evidence_pairs.append((task, row))
            return tasks, attribution, decisions, evidence_pairs, project, "ok", None
        except mod.OwnershipRefused as e:
            if e.reason == "db-busy":
                last_busy = e
                if attempt + 1 < attempts:
                    time.sleep(_BUSY_RETRY_BACKOFF_SECONDS[attempt])
                    continue
                return ([], [], [], [], None, "busy", (
                    "the BrotherMode store at %s stayed busy or locked "
                    "after %d attempt(s) (%s); generation was refused "
                    "rather than writing a passport that silently "
                    "degraded around a temporary lock"
                    % (store_path, attempts, e)))
            if e.reason == "no-store":
                return ([], [], [], [], None, "absent",
                        "no BrotherMode store at %s" % store_path)
            return ([], [], [], [], None, "unreadable",
                    "the BrotherMode store at %s could not be opened: %s"
                    % (store_path, e))
        except mod.StoreCorrupt as e:
            return ([], [], [], [], None, "unreadable",
                    "the BrotherMode store at %s is corrupt: %s"
                    % (store_path, e))
        except Exception as e:
            return ([], [], [], [], None, "unreadable",
                    "the BrotherMode store at %s could not be read: %s: %s"
                    % (store_path, type(e).__name__, e))
        finally:
            if store is not None:
                try:
                    store.close()
                except Exception:  # sbe: allow-silent best-effort store.close() in a finally block; the open/read failures above already returned their own unreadable verdict
                    pass
    return ([], [], [], [], None, "busy",
            "the BrotherMode store at %s stayed busy or locked (%s)"
            % (store_path, last_busy))


def _session_lines(attribution_rows, tasks_by_id):
    """(sessions, any_withheld). `sessions` is every session named on the
    attribution log for this project, sorted by session id, each with the
    claims (output artifacts, or else the task it touched, or else the
    action name) it can be shown to hold: a list of {"label", "claims"}
    dicts, schema/change-passport.v1.json's own shape for
    details.who.sessions. `any_withheld` is True when at least one claim
    source existed but was redacted by the store's export policy (F1,
    cross-family review 2026-08-20), so the caller can name that gap
    instead of the caller ever seeing the placeholder text as a claim.

    session_id itself is never checked for redaction: tools/bm_store.py's
    export policy shape-gates attribution.session_id (an id this codebase
    generates, not founder prose), so it either passes unchanged or the
    row is caller-typed free text withheld like any other; either way a
    withheld session id groups no claims onto a readable label, which is
    the same "no claim named" outcome an ordinary session with nothing to
    show already produces."""
    groups = {}
    any_withheld = False
    for row in attribution_rows:
        sid = (row.get("session_id") or "").strip()
        if not sid or _is_withheld(sid):
            continue
        claims = groups.setdefault(sid, set())

        artifacts_raw = row.get("output_artifacts")
        if isinstance(artifacts_raw, list):
            artifacts = [a for a in artifacts_raw if a]
        else:
            artifacts = []
            if artifacts_raw:
                # A non-list truthy value is the withheld marker STRING:
                # the whole output_artifacts column was redacted before
                # its JSON could even be decoded back into a list.
                any_withheld = True

        if artifacts:
            for artifact in artifacts:
                text = str(artifact)
                if _is_withheld(text):
                    any_withheld = True
                else:
                    claims.add(text)
        else:
            task_id = row.get("task_id")
            if task_id and task_id in tasks_by_id:
                title = tasks_by_id[task_id].get("title")
                if _is_withheld(title):
                    any_withheld = True
                else:
                    claims.add(title or task_id)
            elif row.get("action"):
                action_text = str(row["action"])
                if _is_withheld(action_text):
                    any_withheld = True
                else:
                    claims.add(action_text)
    sessions = [{"label": sid, "claims": sorted(groups[sid])}
               for sid in sorted(groups)]
    return sessions, any_withheld


def _resolve_accountable(root, explicit, attribution_rows):
    """(name_or_None, gap_reason_or_None, was_withheld). Three sources in
    order: --accountable, the newest human actor on the attribution log
    (already newest-first per list_attribution's own ORDER BY), `git
    config user.name`. None for both means every source came up empty;
    the caller turns that into the hard generation error the design
    names. A store-recorded actor_name that was redacted by the export
    policy (F1) is treated as though the row named no human at all,
    NEVER returned as the accountable name: `was_withheld` tells the
    caller at least one candidate existed but could not be carried."""
    if explicit and explicit.strip():
        return explicit.strip(), None, False
    withheld = False
    for row in attribution_rows:
        name = (row.get("actor_name") or "").strip()
        if row.get("actor_type") != "human" or not name:
            continue
        if _is_withheld(name):
            withheld = True
            continue
        return name, ("the accountable person was INFERRED from the "
                      "newest human attribution event recorded for this "
                      "project; no --accountable flag was given"), withheld
    name = _git_user_name(root)
    if name:
        return name, ("the accountable person was INFERRED from `git "
                      "config user.name` at %s, which resolves the "
                      "machine's global git identity; no --accountable flag "
                      "was given and the store named no human actor" % root), withheld
    return None, None, withheld


def _method_name(method_flag, decision_rows):
    """(name, was_defaulted, was_withheld). --method wins outright.
    Otherwise the newest open DECISION insight whose decision_class is
    "method" (open_key_decisions is already newest-first) supplies the
    name the store itself recorded, from its subject or its claim.
    Either one that was redacted by the export policy (F1) is skipped as
    a candidate, never returned as the method name; only the hard-coded
    default is used when nothing else qualifies."""
    if method_flag and method_flag.strip():
        return method_flag.strip(), False, False
    withheld = False
    for row in decision_rows:
        if (row.get("decision_class") or "").strip().lower() != "method":
            continue
        for candidate in (row.get("subject"), row.get("claim")):
            if not _answered(candidate):
                continue
            if _is_withheld(candidate):
                withheld = True
                continue
            return candidate.strip(), False, withheld
    return "no method plugin detected; native flow", True, withheld


def _evidence_entries(evidence_pairs):
    """(entries, any_withheld). `entries` is the details.evidence list.
    Origin (schema 22, evidence.origin) is read straight off the row: how
    the row came to exist (a check that actually ran, an observation, an
    import, a recovery, a synthetic stand-in, an inference), never
    reassigned here. A row written before schema 22 carries the
    migration's own default, 'local', which build_passport's own caller
    reports honestly rather than upgrading it into a value nobody
    actually recorded. See tools/bm_store.py's origin_satisfies() for
    what a caller may conclude from the value; this function only carries
    it through.

    A command prefers ref, falling to kind, exactly as before; a ref (or a
    note, for verdict) that was redacted by the export policy (F1) is
    treated as absent rather than surfaced as the withheld placeholder,
    falling to kind (evidence.kind is scrub-only, not withheld) or to the
    same "(unspecified check)"/"(no verdict recorded)" text an ordinary
    blank field already produced."""
    entries = []
    any_withheld = False
    for _task, row in evidence_pairs:
        ref, kind, note = row.get("ref"), row.get("kind"), row.get("note")

        command = None
        if _answered(ref) and not _is_withheld(ref):
            command = ref.strip()
        elif _answered(kind) and not _is_withheld(kind):
            command = kind.strip()
        if _answered(ref) and _is_withheld(ref):
            any_withheld = True
        if command is None:
            command = "(unspecified check)"

        if _answered(note) and not _is_withheld(note):
            verdict = note.strip()
        else:
            if _answered(note) and _is_withheld(note):
                any_withheld = True
            verdict = "(no verdict recorded)"

        entries.append({
            "command": command,
            "verdict": verdict,
            "origin": row.get("origin") or "local",
            "timestamp": row.get("created_at") or "",
        })
    return entries, any_withheld


def _examined_text_blob(tasks, evidence_pairs):
    parts = [str(t.get("title") or "") for t in tasks]
    for _task, row in evidence_pairs:
        parts.append(str(row.get("kind") or ""))
        parts.append(str(row.get("ref") or ""))
        parts.append(str(row.get("note") or ""))
    return " ".join(parts).lower()


def _default_gap_lines(tasks, evidence_pairs):
    blob = _examined_text_blob(tasks, evidence_pairs)
    lines = []
    for keyword, sentence in _DEFAULT_GAP_KEYWORDS:
        if keyword in blob or keyword.replace("-", " ") in blob:
            continue
        lines.append(sentence)
    return lines


#: F8, cross-family review 2026-08-20: the generator joins and duplicates
#: store text with no bound, so one enormous founder-typed field could
#: produce one enormous line, an unbounded diagnostic surface for
#: whatever reads this document next. docs/PASSPORT.md states this cap.
_LINE_CAP = 2000
_LINE_TRUNCATION_SUFFIX = " [truncated by generator]"


def _cap_line(text):
    if not isinstance(text, str) or len(text) <= _LINE_CAP:
        return text
    keep = max(0, _LINE_CAP - len(_LINE_TRUNCATION_SUFFIX))
    return text[:keep] + _LINE_TRUNCATION_SUFFIX


def _cap_strings(value):
    """Recursively cap every string leaf of a JSON-able structure at
    _LINE_CAP characters. Applied ONCE to the whole assembled passport
    (build_passport, just before self-validation) rather than at each of
    the dozen places a line is built, so a field added to the passport
    later cannot forget the cap the way a per-site guard could."""
    if isinstance(value, str):
        return _cap_line(value)
    if isinstance(value, list):
        return [_cap_strings(v) for v in value]
    if isinstance(value, dict):
        return {k: _cap_strings(v) for k, v in value.items()}
    return value


def _atomic_write(path, text):
    """Write `text` to `path` atomically (F11, cross-family review
    2026-08-20). Both write paths in this tool used to build the SAME
    predictable "<out>.tmp" name: two concurrent producers targeting one
    destination could truncate and overwrite each other's temp file, one
    could keep writing after the other's os.replace moved it into place,
    and a pre-planted temp symlink at that exact path could redirect the
    write anywhere the symlink pointed.

    tempfile.mkstemp opens a randomly-named file in the SAME directory as
    `path` (so os.replace is guaranteed to be on one filesystem and
    therefore atomic) with O_EXCL|O_CREAT at mode 0o600, before a single
    byte of content exists; no predictable name and no window where the
    file exists writable-by-more-than-owner. flush+fsync run before the
    rename so the bytes are actually on disk, not sitting in a page cache
    the rename could outrace on a crash. The temp file is unlinked on any
    failure, so a half-written temp never survives an unsuccessful call."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(parent):
        os.makedirs(parent)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:  # sbe: allow-silent best-effort temp-file cleanup before the original BaseException is re-raised
            pass
        raise


def _canonical_now(now_arg):
    """(iso_string_or_None, error_or_None). Canonicalises to seconds-
    precision UTC ("...Z") whether the caller passed --now or not, so two
    invocations with the SAME --now produce byte-identical output
    regardless of the offset form given (a trailing Z, a numeric offset,
    or none at all)."""
    if now_arg:
        text = now_arg
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError as e:
            return None, "invalid --now value %r: %s" % (now_arg, e)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        parsed = parsed.astimezone(datetime.timezone.utc)
    else:
        parsed = datetime.datetime.now(datetime.timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ"), None


#: F1's example gap sentence, emitted verbatim once (never per-field) when
#: any of the five reads above hit real, redacted store text: the affected
#: line is DROPPED from its field rather than shown as a placeholder, and
#: this is the named replacement the design brief specifies by example.
_SENSITIVE_REDACTED_GAP = (
    "task titles, check commands and verdicts, session claim artifacts, "
    "attributed actor names and decision text that the store's export "
    "policy withheld are not carried in this passport; regenerate with "
    "--include-sensitive to carry them")

#: F1's --include-sensitive warning, printed once to stderr, naming what
#: it exposes.
_INCLUDE_SENSITIVE_WARNING = (
    "bm-passport generate: --include-sensitive is on: this passport "
    "carries UNREDACTED task titles, check commands and verdicts, "
    "session claim artifacts, attributed actor names and decision text "
    "straight from the BrotherMode store; handle it with the same "
    "sensitivity as the store itself.\n")


def build_passport(root, project_id, base, head, accountable_flag, method_flag,
                   now_arg, include_sensitive=False, allow_missing_project=False):
    """(passport_dict_or_None, error_or_None). error set means generation
    refused outright: the empty-accountable law, an unresolvable --base/
    --head (F6), a store that stayed busy through every retry (F3), or a
    requested project genuinely absent from an otherwise-readable store
    without --allow-missing-project (F9). Every OTHER gap is folded into
    details.notEstablished / whatWasNotEstablished instead, per the
    design's "reports, does not block" rule.

    include_sensitive selects the export policy for all five store reads
    (F1): False (the default) routes every read through the store's own
    redaction policy (raw=False) and turns any withheld content into a
    dropped line plus one named gap, rather than ever showing the
    withheld placeholder as content; True restores the old raw=True
    behaviour, the caller's responsibility to warn about."""
    now_str, now_err = _canonical_now(now_arg)
    if now_err:
        return None, now_err

    base_full, base_err = _resolve_commit(root, base)
    if base_err:
        return None, "invalid --base: %s" % base_err
    head_full, head_err = _resolve_commit(root, head)
    if head_err:
        return None, "invalid --head: %s" % head_err

    repo = _repo_identity(root)
    files, files_gap = _files_touched(root, base_full, head_full)

    (tasks, attribution, decisions, evidence_pairs, project, state,
     reason) = _read_store_for_generate(root, project_id, include_sensitive)
    if state == "busy":
        return None, ("the BrotherMode store could not be read: %s" % reason)
    store_gap = None
    if state == "ok":
        if project is None:
            if not allow_missing_project:
                return None, (
                    "no project %r was found in the BrotherMode store; "
                    "refused rather than writing a passport with only "
                    "generic defaults. Pass --allow-missing-project to "
                    "generate a degraded passport anyway, naming the gap."
                    % project_id)
            store_gap = ("no project %r was found in the BrotherMode "
                         "store, so its tasks, evidence and decisions "
                         "could not be read" % project_id)
    else:
        store_gap = "%s, so session, evidence and decision history for this change could not be read" % reason

    tasks_by_id = {t["task_id"]: t for t in tasks}
    sessions, sessions_withheld = _session_lines(attribution, tasks_by_id)
    accountable_name, accountable_gap, accountable_withheld = _resolve_accountable(
        root, accountable_flag, attribution)
    if accountable_name is None:
        return None, ("no accountable human could be established: no "
                      "--accountable flag was given, the store named no "
                      "human actor, and `git config user.name` returned "
                      "nothing at %s" % root)

    method_name, method_defaulted, method_withheld = _method_name(
        method_flag, decisions)
    evidence_entries, evidence_withheld = _evidence_entries(evidence_pairs)

    items = list(_default_gap_lines(tasks, evidence_pairs))
    for gap in (files_gap, store_gap, accountable_gap):
        if gap:
            items.append(gap)
    # Schema 22: origin is now tracked (see _evidence_entries's own
    # docstring), so the gap this generator can still name honestly is
    # not "origin is untracked", it is "some of the origin recorded
    # cannot satisfy a completion claim on its own" (bs.origin_satisfies,
    # the invariant this passport must never paper over by reporting an
    # unqualified pass). _load_bm_store never raises; an unloadable
    # module degrades to naming that as the gap instead of guessing at
    # satisfaction it cannot compute.
    store_mod, store_mod_err = _load_bm_store()
    if store_mod_err:
        if evidence_entries:
            items.append(
                "evidence origin could not be checked against "
                "tools/bm_store.py's own satisfaction rule (%s); none of "
                "the entries above may be read as satisfying a completion "
                "claim until that check runs" % store_mod_err)
    else:
        non_satisfying = sorted(set(
            e["origin"] for e in evidence_entries
            if not store_mod.origin_satisfies(e["origin"])))
        if non_satisfying:
            items.append(
                "evidence includes origin(s) %s that cannot satisfy a "
                "completion claim on their own (tools/bm_store.py's "
                "origin_satisfies): a check that actually ran or was "
                "directly observed is required, never assumed from a "
                "recovered, imported, synthetic or inferred record"
                % ", ".join(non_satisfying))
    if (not include_sensitive
            and (sessions_withheld or accountable_withheld
                 or method_withheld or evidence_withheld)):
        items.append(_SENSITIVE_REDACTED_GAP)
    justification = None
    if not items:
        justification = ("every default gap category was found examined in "
                         "the store's own task and evidence text, and no "
                         "other gap was detected by this generator")

    what_was_done = ["change in %s, commit range %s..%s"
                     % (repo, base_full, head_full)]
    if files:
        what_was_done.append(
            "%d file(s) touched: %s" % (len(files), ", ".join(files)))
    else:
        what_was_done.append(
            "no files were touched between %s and %s" % (base_full, head_full))

    who_did_it = ["accountable: %s" % accountable_name]
    for session in sessions:
        claims = ", ".join(session["claims"]) if session["claims"] else "(no claim named)"
        who_did_it.append("session %s: claims %s" % (session["label"], claims))

    if evidence_entries:
        what_was_run = [
            "`%s` returned %s (%s, %s)" % (
                e["command"], e["verdict"], e["origin"], e["timestamp"])
            for e in evidence_entries]
    else:
        what_was_run = [
            "no verification evidence is recorded in the BrotherMode store "
            "for project %r" % project_id]

    where_it_came_from = [method_name]
    what_was_not_established = items if items else [justification]

    passport = {
        "schema": GENERATE_SCHEMA_MARKER,
        "generatedAt": now_str,
        "sensitivity": "raw" if include_sensitive else "redacted",
        "whatWasDone": what_was_done,
        "whoDidIt": who_did_it,
        "whatWasRun": what_was_run,
        "whatWasNotEstablished": what_was_not_established,
        "whereItCameFrom": where_it_came_from,
        "change": {
            "repo": repo,
            "baseCommit": base_full,
            "headCommit": head_full,
            "filesTouched": files,
            "projectId": project_id,
        },
        "details": {
            "who": {"sessions": sessions, "accountableHuman": accountable_name},
            "evidence": evidence_entries,
            "method": {"name": method_name},
            "notEstablished": {"items": items, "noneClaimJustification": justification},
        },
    }
    # F8: bound every string leaf before this document exists anywhere
    # else (self-validation, stdout, disk).
    return _cap_strings(passport), None


def _usage_generate():
    cmd = None
    mod, _err = _load_bm_store()
    if mod is not None:
        try:
            cmd = mod.invocation("bm_passport.py", __file__)
        except Exception:
            cmd = None
    if not cmd:
        # F12: shlex.quote, not a bare path interpolation. This branch
        # only runs when bm_store itself could not be loaded, so
        # mod.invocation's own cross-platform quoting (bm_store.py's
        # _quote_path_for_local_shell) is not reachable here; shlex.quote
        # is the POSIX-correct stdlib fallback for the one path this tool
        # can still name on its own.
        cmd = "python3 %s" % shlex.quote(os.path.abspath(__file__))
    return ("Usage: %s generate --project-id ID --base SHA --head SHA\n"
            "         [--accountable NAME] [--method NAME] [--now ISO8601]\n"
            "         [--out PATH] [--root DIR] [--include-sensitive]\n"
            "         [--allow-missing-project]\n\n"
            "Writes the full five-field change passport (schema/change-"
            "passport.v1.json)\nto stdout, or to --out when given. --base "
            "and --head are resolved with\n`git rev-parse --verify` and "
            "written as their full canonical hash.\n\n"
            "By default every store-derived field is REDACTED through "
            "tools/bm_store.py's\nown export policy: founder-typed free "
            "text (task titles, check commands and\nverdicts, session "
            "claim artifacts, actor names, decision text) is dropped and "
            "\nnamed as a gap rather than shown. --include-sensitive "
            "restores the raw,\nunredacted store text and prints a "
            "warning naming what it exposes.\n\n"
            "A requested --project-id absent from the store is refused "
            "by default;\n--allow-missing-project generates a degraded "
            "passport anyway, naming the gap.\n\n"
            "Exit 0 on a written passport, whatever it could and could not "
            "establish.\nExit 1 when no accountable human could be "
            "established, when --base or --head\ndid not resolve to a "
            "commit, when the requested project is missing and\n"
            "--allow-missing-project was not given, when the store stayed "
            "busy through\nevery retry, when the assembled passport failed "
            "its own self-validation, or\nwhen --out could not be written. "
            "Exit 2 for a usage error or an unresolvable\nproject root." % cmd)


def _parse_generate_argv(argv):
    """(args_dict_or_None, error_or_None). error == 'help' means print
    generate usage and exit 0."""
    values = {"project_id": None, "base": None, "head": None,
             "accountable": None, "method": None, "now": None,
             "out": None, "root": None, "include_sensitive": False,
             "allow_missing_project": False}
    flag_to_key = {
        "--project-id": "project_id", "--base": "base", "--head": "head",
        "--accountable": "accountable", "--method": "method",
        "--now": "now", "--out": "out", "--root": "root",
    }
    bool_flag_to_key = {
        "--include-sensitive": "include_sensitive",
        "--allow-missing-project": "allow_missing_project",
    }
    args = list(argv)
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            return None, "help"
        if arg in bool_flag_to_key:
            values[bool_flag_to_key[arg]] = True
            i += 1
        elif arg in flag_to_key:
            if i + 1 >= len(args):
                return None, "bm_passport generate: %s requires a value" % arg
            values[flag_to_key[arg]] = args[i + 1]
            i += 2
        else:
            return None, "bm_passport generate: unknown argument: %s" % arg
    missing = [flag for flag, key in
              (("--project-id", "project_id"), ("--base", "base"), ("--head", "head"))
              if not (values[key] or "").strip()]
    if missing:
        return None, ("bm_passport generate: missing required argument(s): "
                      "%s" % ", ".join(missing))
    return values, None


def _run_generate(argv):
    values, err = _parse_generate_argv(argv)
    if err == "help":
        sys.stdout.write(_usage_generate() + "\n")
        return 0
    if err:
        sys.stderr.write(err + "\n")
        sys.stderr.write(_usage_generate() + "\n")
        return 2

    root, reason = resolve_project_root(values["root"])
    if root is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    if values["include_sensitive"]:
        sys.stderr.write(_INCLUDE_SENSITIVE_WARNING)

    passport, error = build_passport(
        root, values["project_id"], values["base"], values["head"],
        values["accountable"], values["method"], values["now"],
        include_sensitive=values["include_sensitive"],
        allow_missing_project=values["allow_missing_project"])
    if error:
        sys.stderr.write("bm-passport generate: %s\n" % error)
        return 1

    # F6: self-validate before writing anything. A passport that would
    # not itself pass the standalone validator (tools/
    # bm_passport_validator.py) is refused outright, never written to
    # disk or stdout: exit 0 here would be this tool's own overclaim.
    validator_mod, validator_err = _load_validator()
    if validator_mod is None:
        sys.stderr.write(
            "bm-passport generate: could not self-validate: %s\n"
            % validator_err)
        return 1
    problems = validator_mod.validate(passport)
    if problems:
        sys.stderr.write(
            "bm-passport generate: refused to write a passport that "
            "fails its own validator:\n")
        for problem in problems:
            sys.stderr.write("  %s\n" % problem)
        return 1

    text = json.dumps(passport, sort_keys=True, indent=2,
                      ensure_ascii=False) + "\n"
    if not values["out"]:
        # F5: stdout carries this document as UTF-8 bytes explicitly,
        # never through sys.stdout's own text encoding, which follows
        # the platform/locale and can silently transcode or fail on
        # non-ASCII content (a founder-typed accountable name, a decision
        # subject) that this document may legitimately carry.
        sys.stdout.buffer.write(text.encode("utf-8"))
        return 0
    try:
        _atomic_write(values["out"], text)
    except OSError as e:
        sys.stderr.write("bm-passport generate: could not write %s (%s)\n"
                         % (values["out"], e))
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _usage():
    """The command a reader can actually paste, in the layout they have.

    P17 put the shipping commands on PATH, so a hardcoded
    `python3 tools/bm_passport.py` names a directory a packaged install does
    not have. Resolved through bm_store.invocation(), the same resolver
    tools/bm_handover.py and tools/bm_docs.py use, and swept for by
    test_bm_store.py's TestP17InstructionTextMatchesTheInstalledLayout.
    Degrades to this module's own absolute path, which is correct from any
    cwd, when bm_store cannot be loaded: a usage string is not worth a
    traceback."""
    cmd = None
    mod, _err = _load_bm_store()
    if mod is not None:
        try:
            cmd = mod.invocation("bm_passport.py", __file__)
        except Exception:
            cmd = None
    if not cmd:
        # F12: shlex.quote, same reason as _usage_generate's own fallback.
        cmd = "python3 %s" % shlex.quote(os.path.abspath(__file__))
    return ("Usage: %s [--root DIR] [--out PATH]\n"
            "         [--accountable NAME] [--method TEXT]\n\n"
            "Writes the change passport's producer deposit at <root>/%s.\n"
            "Exit 0 when a deposit was written, whatever it could and could "
            "not\nestablish. Exit 1 when the deposit itself could not be "
            "written.\nExit 2 for a usage error, and for a NO-DATA verdict "
            "where no project\nroot could be resolved at all, which is not a "
            "finding about a change\nbut a failure to find one." % (
                cmd, DEPOSIT_REL))


def _parse_argv(argv):
    """Return (root, out, accountable, method, error). `error` set to
    'help' means print usage and exit 0; any other string means a plain
    usage failure (exit 2)."""
    args = list(argv)
    root = None
    out = None
    accountable = None
    method = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            return None, None, None, None, "help"
        # An EMPTY value for the two path flags is refused rather than
        # treated as absent. `--out "$OUT"` with an unset variable used to
        # fall back to the project's real .sbe/passport.json, so a caller
        # scripting a throwaway deposit silently wrote over the live one.
        # The two text flags keep degrading quietly: an empty --accountable
        # omits the field, which is honest, and an empty --method takes the
        # default. Only the flags that decide WHERE bytes land are strict.
        if arg == "--root":
            if i + 1 >= len(args):
                return None, None, None, None, "bm_passport: --root requires a value"
            if not args[i + 1].strip():
                return None, None, None, None, (
                    "bm_passport: --root was given an empty value; omit the "
                    "flag to resolve the project root automatically")
            root = args[i + 1]
            i += 2
        elif arg == "--out":
            if i + 1 >= len(args):
                return None, None, None, None, "bm_passport: --out requires a value"
            if not args[i + 1].strip():
                return None, None, None, None, (
                    "bm_passport: --out was given an empty value; omit the "
                    "flag to write the deposit at <root>/%s" % DEPOSIT_REL)
            out = args[i + 1]
            i += 2
        elif arg == "--accountable":
            if i + 1 >= len(args):
                return None, None, None, None, "bm_passport: --accountable requires a value"
            accountable = args[i + 1]
            i += 2
        elif arg == "--method":
            if i + 1 >= len(args):
                return None, None, None, None, "bm_passport: --method requires a value"
            method = args[i + 1]
            i += 2
        else:
            return None, None, None, None, "bm_passport: unknown argument: %s" % arg
    return root, out, accountable, method, None


def _run(argv):
    root_arg, out_arg, accountable_arg, method_arg, err = _parse_argv(argv)
    if err == "help":
        sys.stdout.write(_usage() + "\n")
        return 0
    if err:
        sys.stderr.write(err + "\n")
        sys.stderr.write(_usage() + "\n")
        return 2

    root, reason = resolve_project_root(root_arg)
    if root is None:
        sys.stdout.write("NO-DATA: %s\n" % reason)
        return 2

    deposit, _not_established, notes = build_deposit(root, accountable_arg, method_arg)
    out_path = out_arg or os.path.join(root, DEPOSIT_REL)

    # Written atomically (F11, cross-family review 2026-08-20): a unique
    # same-directory temp file, never the predictable "<out>.tmp" name
    # this and the generate path both used to share, which let two
    # concurrent producers targeting one destination corrupt or overwrite
    # each other. See _atomic_write's own docstring for the full
    # reasoning. Truncating the real path first (what a plain open("w")
    # would do) means a failure mid-write leaves a half-written deposit,
    # and the consumer classifies a truncated deposit as CORRUPT rather
    # than absent, which is a worse answer than never having written one;
    # the atomic replace is what keeps that from happening.
    text = json.dumps(deposit, indent=2, sort_keys=True) + "\n"
    try:
        _atomic_write(out_path, text)
    except OSError as e:
        sys.stderr.write("bm-passport: could not write %s (%s)\n" % (out_path, e))
        return 1

    sys.stdout.write("deposit written: %s\n" % out_path)
    for line in notes:
        sys.stdout.write(line + "\n")
    return 0


def main(argv):
    try:
        # "generate" is a new, additive verb (see build_passport above): the
        # full five-field passport, a different CLI shape from the no-verb
        # deposit behavior below it. Any OTHER first argument, flag or bare
        # word, falls through to the original parser unchanged, which is
        # what already refuses a stray positional argument as "unknown
        # argument"; no existing invocation shape changes.
        if argv and argv[0] == "generate":
            return _run_generate(argv[1:])
        return _run(argv)
    except Exception as exc:
        sys.stdout.write("NO-DATA: %s: %s\n" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
