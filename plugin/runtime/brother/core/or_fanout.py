#!/usr/bin/env python3
"""or_fanout: run many gated OpenRouter jobs at once, one results file.

The orchestrator reads files and builds grounded prompts; this runs them
through dispatch() (budget ledger, fallback refusal, Jev type gate, slot
semaphore) in parallel and writes every answer to disk. A failed job is
recorded by name with its reason, never raised and never silently dropped,
and the exit code is nonzero when any job failed.

Jobs file: a JSON list of objects
  {"id": "l5a-ledger", "model": "muse"|"deepseek"|"jev",
   "prompt_file": "path" | "prompt": "text",
   "out": "path/for/answer.md",
   "max": optional (default: the model's full ceiling, MODEL_MAX_TOKENS),
   "estimated_cost": 0.05, "jev_type": "noul",
   "expect": "json" (optional: an answer that does not parse is a failure)}

usage: python3 -m plugin.runtime.brother.core.or_fanout JOBS.json
         [--workers 12] [--results results.json] [--timeout 900] [--retries 2]
         [--deadline SECONDS]

--deadline is the wave's own deadline (default: timeout x (retries + 1)). A
job still running at it gets a STALLED_AFTER_DEADLINE row. With
BROTHER_ONE_DEADLINE=on that job is not abandoned: when it ends, its real
record, marked "late", replaces its stall row, so a paid answer is recorded.
"""
import argparse
import json
import math
import signal
import tempfile
import threading
import os
import re
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from plugin.runtime.brother.core import dream_record, openrouter_dispatch
from plugin.runtime.brother.core import repo_paths
from plugin.runtime.brother.core.or_dispatch_cli import BRIDGE_PATH, REAL_MODEL_IDS
from plugin.runtime.brother.core.openrouter_strict import parse_usage_envelope


def inventory_scope(root: str, commit_sha: str) -> list[str]:
    """L5a-1 review procedure: list sorted absolute paths under the plugin
    brother tree plus the hook file. Raises ValueError on missing root or
    invalid commit SHA; the caller records NO-DATA for all points."""
    if not isinstance(root, str) or not root or not os.path.isdir(root):
        raise ValueError("root must be an existing directory")
    if not isinstance(commit_sha, str) or not re.match(r"^[0-9a-fA-F]{40}$", commit_sha):
        raise ValueError("commit_sha must be 40 hex characters")
    base = os.path.join(root, "plugin", "runtime", "brother")
    paths = []
    if os.path.isdir(base):
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                paths.append(os.path.abspath(os.path.join(dirpath, name)))
    hook = os.path.join(root, "scripts", "brother_antigravity_hook.py")
    if os.path.isfile(hook):
        paths.append(os.path.abspath(hook))
    return sorted(paths)

# L5a-2 REQ-07: one redactor for every log, error and record path, and the
# sweep the review runs over the tree. Its shapes are the ones the review's own
# grep names, so a hit the sweep finds is a hit this redactor removes before
# that text reaches a file, a record, a log line or the console.
REDACTED = "[REDACTED]"

_SECRET_SHAPES = (
    re.compile(r"\bsk-or-[A-Za-z0-9._-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*"
               r"[\"']?[^\s\"']{6,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?"
               r"-----END [A-Z ]*PRIVATE KEY-----"),
)

# A hit is a credential only when a value is attached to the name; a name on
# its own, such as a pattern or a comment in this module, is a mention for the
# reviewer to classify. An empty sweep is a grep exit 1 the caller still has to
# quote, never a pass by absence, and a file that cannot be read is NO-DATA.
_CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)(?:\bsk-or-[A-Za-z0-9]{8,}"
    r"|\bsk-[A-Za-z0-9]{16,}\b"
    r"|(?:api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*"
    r"[\"']?[A-Za-z0-9_\-+/=]{12,})")

_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\r\n]+"
    r"[A-Za-z0-9+/=\s]{64,}-----END")


def redact_text(text, patterns=None):
    """REQ-07 redactor: replace every credential shape in text before that text
    is written to a log, an error, a record or an answer file. Text with no such
    shape comes back unchanged, so this is safe on every existing error path. A
    value that is not a string is refused, never written as the clean case."""
    if not isinstance(text, str):
        raise ValueError("redact_text needs a string, got %s" % type(text).__name__)
    if patterns is None:
        patterns = _SECRET_SHAPES
    if not isinstance(patterns, (list, tuple)):
        raise ValueError("patterns must be a list or tuple, got %s"
                         % type(patterns).__name__)
    out = text
    for pattern in patterns:
        if isinstance(pattern, str):
            try:
                pattern = re.compile(pattern)
            except re.error as exc:
                raise ValueError("pattern %r does not compile: %s" % (pattern, exc))
        if not hasattr(pattern, "sub"):
            raise ValueError("each pattern must be a string or a compiled pattern")
        out = pattern.sub(REDACTED, out)
    return out


def _bits_per_char(value):
    counts = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    total = float(len(value))
    bits = 0.0
    for count in counts.values():
        share = count / total
        bits -= share * math.log(share, 2)
    return bits


def entropy_tokens(text, min_length=24, min_bits=4.0):
    """REQ-07 entropy scan for the log and error paths: the tokens long enough
    to be a key, with their bits per character, so a reviewer can tell a real
    high entropy value from a long English word. Detection only: a token is
    never returned in the clear."""
    if not isinstance(text, str):
        raise ValueError("entropy_tokens needs a string, got %s" % type(text).__name__)
    if isinstance(min_length, bool) or not isinstance(min_length, int) or min_length < 8:
        raise ValueError("min_length must be an integer >= 8, got %r" % (min_length,))
    if isinstance(min_bits, bool) or not isinstance(min_bits, (int, float)) \
            or not math.isfinite(min_bits) or min_bits < 0:
        raise ValueError("min_bits must be a finite number >= 0, got %r" % (min_bits,))
    found = []
    for value in re.findall(r"[A-Za-z0-9+/=_-]{%d,}" % min_length, text):
        bits = _bits_per_char(value)
        if bits >= min_bits:
            found.append({"length": len(value), "bits_per_char": round(bits, 2),
                          "excerpt": REDACTED})
    return found


def _sweep_no_data(path, pattern, reason):
    return {"path": path, "line": 0, "pattern": pattern, "kind": "no_data",
            "excerpt": redact_text(reason)[:200]}


def sweep_secrets(root, patterns):
    """L5a-2 review procedure (REQ-07, REQ-08): read every file under root as
    bytes and return one record per matching line, with the evidence already
    redacted and every hit classified. A root that is missing, a pattern set
    that is not a non-empty list, and a pattern that will not compile are
    refused with ValueError. A link, or a file that cannot be read safely, is
    recorded as NO-DATA and never skipped: an unknown value is never the clean
    case, and an empty result is a grep exit 1 the caller still has to quote."""
    if not isinstance(root, str) or not root or not os.path.isdir(root):
        raise ValueError("root must be an existing directory")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("patterns must be a non-empty list of regex strings")
    compiled = []
    for pattern in patterns:
        if isinstance(pattern, bool) or not isinstance(pattern, str) or not pattern:
            raise ValueError("each pattern must be a non-empty string, got %r"
                             % (pattern,))
        try:
            compiled.append((pattern, re.compile(pattern)))
        except re.error as exc:
            raise ValueError("pattern %r does not compile: %s" % (pattern, exc))
    real_root = os.path.realpath(root)
    records = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            shown = os.path.relpath(path, root)
            if os.path.islink(path):
                records.append(_sweep_no_data(shown, "<symlink>",
                                              "link not followed"))
                continue
            real = os.path.realpath(path)
            if real != real_root and not real.startswith(real_root + os.sep):
                records.append(_sweep_no_data(shown, "<outside-root>", real))
                continue
            try:
                with open(path, "rb") as handle:
                    raw = handle.read()
            except OSError as exc:
                records.append(_sweep_no_data(
                    shown, "<unreadable>", "%s: %s" % (type(exc).__name__, exc)))
                continue
            text = raw.decode("latin-1")
            for number, line in enumerate(text.splitlines(), 1):
                for pattern, regex in compiled:
                    if regex.search(line):
                        records.append({
                            "path": shown, "line": number, "pattern": pattern,
                            "kind": ("credential_value"
                                     if _CREDENTIAL_VALUE_RE.search(line)
                                     else "mention"),
                            "excerpt": redact_text(line)[:200],
                        })
            if _PRIVATE_KEY_BLOCK_RE.search(text):
                marker = text.index("-----BEGIN")
                records.append({
                    "path": shown, "line": text.count("\n", 0, marker) + 1,
                    "pattern": "BEGIN.*PRIVATE KEY", "kind": "credential_value",
                    "excerpt": REDACTED,
                })
    return records


MAX_PROMPT_BYTES = 200_000  # argv carries the prompt; stay far below ARG_MAX

# Founder rule 2026-09-20: give every model its full output ceiling. Values are
# top_provider.max_completion_tokens read from https://openrouter.ai/api/v1/models
# on 2026-09-20; re-read the catalog before trusting them later. A model bills
# what it writes, not what it is allowed, so the ceiling costs nothing unused.
# Worst case at these prices: deepseek 384000 x 0.60/M = 0.23 USD, muse
# 943718 x 0.20/M = 0.19 USD. Jev is a typed decisions endpoint absent from
# the public catalog; its answers are a few tokens, so it keeps the old value.
MODEL_MAX_TOKENS = {"deepseek": 384_000, "muse": 943_718, "jev": 32_000}
DEFAULT_TIMEOUT_S = 900  # a long answer needs the wait; retries double it

# Owner order 2026-09-21, his words: "make sure openrouter models do not return
# empty because of tokens set too low so set them to xhigh". Measured the same
# day over 5,270 dispatched jobs: 5,249 of them set no effort at all, so this
# one default decides the effort of essentially every call the estate makes.
# It was three separate `job.get("effort", DEFAULT_EFFORT)` literals, which is how a
# default drifts apart, so it is one named constant now and the three call
# sites read it.
#
# The empty-answer risk this addresses is real and the mechanism is specific:
# with an effort set, hidden reasoning tokens are drawn from the SAME max_tokens
# budget as the visible answer, so a low max lets reasoning spend the whole
# allowance before a single content token is written, which arrives as an empty
# answer. Raising effort raises that reasoning appetite, so the two must move
# together. They do: `max` here already defaults to the model's full ceiling
# (384k for deepseek, 943k for muse), and for any job that DOES pin a small max
# the bridge raises it to EFFORT_MAX_FLOOR[effort] before the call. A ceiling is
# billed only for tokens actually generated, so an unused one costs nothing.
DEFAULT_EFFORT = "xhigh"

# Founder rule 2026-09-20: a failed OpenRouter call is tried again. Only for
# failures that can differ next time (timeout, empty answer, bridge error, a
# silent model substitution). A refusal by one of our own gates (budget,
# quarantine, Jev type, floors, an oversized prompt) is deterministic: it is
# reported at once and never retried, since a retry cannot change it.
DEFAULT_RETRIES = 2
RETRY_PAUSES_S = (5, 20)
_NEVER_RETRY = ("BudgetExceeded", "QuarantinedCapability", "LedgerError",
                "TimeoutTooLow", "MaxTokensTooLow", "ValueError", "KeyError",
                "TypeError", "FileNotFoundError", "DrainRefused", "Stopped")


def run_job_with_retries(job, timeout, workers, retries=DEFAULT_RETRIES,
                         dispatch=None, sleep=time.sleep, workspace_root=None):
    """run_job, tried again on a transient failure. A timeout is retried with
    twice the wait and the same token budget (more patience, not more load).
    The record names every attempt, so a success on try 3 never hides that
    tries 1 and 2 failed."""
    attempts = []
    decision = _record_choice(job, workspace_root)
    for n in range(retries + 1):
        record = run_job(job, timeout, workers, dispatch=dispatch, workspace_root=workspace_root)
        attempts.append(record.get("error") or "ok")
        # CONFIG_WAIT is never retried and never spends an attempt: the program does not know the model, the breaker for
        # it is open, and a retry would be refused before it was sent (2026-09-30: 78 such calls parked 7 sub units).
        if record["ok"] or record.get("config") or record.get("error", "").split(":")[0] in _NEVER_RETRY \
                or n == retries or openrouter_dispatch.STOPPED.is_set():
            break
        if record["error"].startswith("TimeoutExpired"):
            timeout *= 2
        sleep(RETRY_PAUSES_S[min(n, len(RETRY_PAUSES_S) - 1)])
    record["attempts"] = attempts
    _record_result(decision, record)
    return record


_APPEND_LOCKS = {}
_APPEND_LOCKS_GUARD = threading.Lock()


def _lock_for(path):
    key = os.path.abspath(path)
    with _APPEND_LOCKS_GUARD:
        lock = _APPEND_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _APPEND_LOCKS[key] = lock
        return lock


STALLED = "STALLED_AFTER_DEADLINE"


def one_deadline(env=None):
    """BROTHER_ONE_DEADLINE (FX-10), the SAME rule as scripts/loop/worker_mix.one_deadline (this package must not import
    scripts/loop; test_or_fanout_m51 pins both readers): "on" is True; "off", unset or empty is False; anything else says
    ONE-DEADLINE NO-DATA and is False, today's behaviour, never a guess."""
    v = (os.environ if env is None else env).get("BROTHER_ONE_DEADLINE", "")
    if v == "on":
        return True
    if v not in ("off", "", None):
        print("ONE-DEADLINE NO-DATA: %r is not on or off; today's deadlines stand" % (v,), flush=True)
    return False


def append_record_atomic(path, record):
    _rewrite_atomic(path, record, lambda rows: rows.append(record))


def replace_stalled_record_atomic(path, record):
    """FX-10: a stalled job that ended replaces ITS STALLED_AFTER_DEADLINE row with its real record. A row of that id
    that is not a stall (a real answer on file) is never replaced; with no stall row the record is appended, once:
    the same record already on file is not appended again. Same lock and atomic rewrite as append_record_atomic."""
    def change(rows):
        for i, row in enumerate(rows):
            if isinstance(row, dict) and row.get("id") == record.get("id") and row.get("error") == STALLED:
                rows[i] = record
                return
        if record not in rows:
            rows.append(record)
    _rewrite_atomic(path, record, change)


def _rewrite_atomic(path, record, change):
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    path = os.path.abspath(path)
    lock = _lock_for(path)
    with lock:
        if os.path.isdir(path):
            raise ValueError("results path is a directory: %s" % path)
        rows = []
        if os.path.islink(path):
            rows = []
        elif os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
                rows = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                raise ValueError("existing results file is not valid JSON: %s" % exc)
            if not isinstance(rows, list):
                raise ValueError("existing results file must be a JSON list")
        change(rows)
        directory = os.path.dirname(path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".or-fanout-", dir=directory)
        except OSError as exc:
            raise ValueError("results path is not writable: %s" % exc)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, indent=1)
            os.replace(tmp, path)
        except BaseException:
            try:
                # l5b: PROPAGATE_SAFE: removing the temp file is cleanup on a failed write; the original error is re-raised below
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _shown_model(job):
    """The model a record names, shown escaped when it is not a plain one line string. EVERY record carries it, the stall
    record too (2026-09-27): a stall without its model could not teach the patience estimator anything."""
    m = (job if isinstance(job, dict) else {}).get("model")
    return m if isinstance(m, str) and "\n" not in m else repr(m)[:40]


def _shown_id(job):
    as_dict = job if isinstance(job, dict) else {}
    jid = as_dict.get("id")
    return jid if isinstance(jid, str) and _SAFE_ID_RE.match(jid) else repr(jid)[:80]


def stragglers(jobs, done_ids):
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list")
    if not isinstance(done_ids, set):
        raise ValueError("done_ids must be a set")
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("jobs must contain objects")
        name = _shown_id(job)
        if name not in done_ids:
            out.append(name)
    return out


def _wave_number(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s must be a finite number" % name)
    if allow_zero:
        if value < 0:
            raise ValueError("%s must be >= 0" % name)
    elif value <= 0:
        raise ValueError("%s must be > 0" % name)
    return float(value)


def _wave_int(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer" % name)
    if allow_zero:
        if value < 0:
            raise ValueError("%s must be >= 0" % name)
    elif value <= 0:
        raise ValueError("%s must be > 0" % name)
    return value


def _failed_record(job, exc):
    return {"id": _shown_id(job), "model": _shown_model(job), "ok": False,
            "error": redact_text("%s: %s" % (type(exc).__name__, exc))[:400]}


def _settle_late(fut, job, results_path):
    """A job that outlived the wave's deadline ended: its real record, marked late, replaces its stall row (FX-10). Runs
    in the job's own worker thread, which the interpreter joins before the fan out process exits."""
    if fut.cancelled():
        return
    try:
        rec = fut.result()
    except Exception as exc:  # recorded by name, never raised from a callback
        rec = _failed_record(job, exc)
    if not isinstance(rec, dict):
        rec = {"id": _shown_id(job), "ok": False, "error": "invalid record"}
    rec = dict(rec, late=True)
    try:
        replace_stalled_record_atomic(results_path, rec)
    except ValueError as exc:
        print("LATE NO-DATA %s: its record could not be written (%s)" % (rec.get("id"), exc), flush=True)
        return
    print("LATE %s %s after the wave deadline" % (rec.get("id"), "answered" if rec.get("ok") else "failed"), flush=True)


def run_wave(jobs, timeout, workers, retries, deadline_s, results_path, workspace_root=None):
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list")
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("jobs must contain objects")
    _wave_number(timeout, "timeout")
    _wave_int(workers, "workers")
    _wave_int(retries, "retries", allow_zero=True)
    _wave_number(deadline_s, "deadline_s", allow_zero=True)
    if not isinstance(results_path, str) or not results_path:
        raise ValueError("results_path must be a non-empty string")

    started = time.monotonic()
    records = []
    pending = {}
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        for job in jobs:
            fut = pool.submit(run_job_with_retries, job, timeout, workers, retries,
                              workspace_root=workspace_root)
            pending[fut] = job
        while pending:
            remaining = deadline_s - (time.monotonic() - started)
            if remaining <= 0:
                break
            done, _ = wait(list(pending), timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for fut in done:
                job = pending.pop(fut)
                try:
                    rec = fut.result()
                except Exception as exc:
                    rec = _failed_record(job, exc)
                if not isinstance(rec, dict):
                    rec = {"id": _shown_id(job), "ok": False, "error": "invalid record"}
                records.append(rec)
                append_record_atomic(results_path, rec)
        # THE DEADLINE. Each job still pending gets a stall row. Switch off: cancel(), which only stops a job that has not
        # started; a running one was abandoned, its paid answer written to disk and never recorded (FX-10, row 79).
        # Switch on: a job not yet started is still cancelled (nothing is spent after the deadline), and a running one
        # replaces its stall row with its real record when it ends. The records returned here are unchanged either way.
        late = one_deadline()
        for fut, job in list(pending.items()):
            name = _shown_id(job)
            rec = {"id": name, "model": _shown_model(job), "ok": False,
                   "error": STALLED,
                   "seconds": round(time.monotonic() - started, 1)}
            records.append(rec)
            append_record_atomic(results_path, rec)
            if not fut.cancel() and late:
                fut.add_done_callback(lambda f, job=job: _settle_late(f, job, results_path))
    finally:
        pool.shutdown(wait=False)
    return records


def _record_choice(job, workspace_root=None):
    """Self-learning layer, unit D0: which model a job was sent to, among the
    models that were available. Opt-in (a declared run directory) and never
    allowed to fail or change a job."""
    try:
        run_dir = dream_record.run_dir_from_env()
        if not run_dir:
            return None
        event = dream_record.record_decision(
            run_dir, "openrouter.model",
            observed={"job": str(job.get("id")), "effort": job.get("effort", DEFAULT_EFFORT),
                      "prompt_bytes": len(_load_prompt(job, workspace_root).encode("utf-8"))},
            options=sorted(REAL_MODEL_IDS), chosen=job["model"],
            policy_version="job-declared-v0", unit_id=str(job.get("id")))
        return (run_dir, event) if event else None
    except Exception as exc:  # recording must never take a job down
        sys.stderr.write("or_fanout: decision not recorded (%s)\n" % str(exc)[:200])
        return None


def billed_usd(stderr):
    """The bridge's own billed figure from its "[billed] usd=X attempts=N known=yes" line, as a float, or None when the
    line is absent or says known=no (D2, 2026-09-24: until then every outcome the journal held carried cost None, so the
    per model cost per success the routing loop needs could not be computed). Never an estimate."""
    import re
    m = re.search(r"\[billed\] usd=([0-9.]+) attempts=\d+ known=(yes|no)", stderr or "")
    if not m or m.group(2) != "yes": return None
    try: return float(m.group(1))
    except ValueError: return None


def _record_result(decision, record):
    """D2.6 REQ-FAN-3 (docs/plan/specs/D2.md): ambiguous identity BLOCKS
    learning. A record whose actual_model is None (the bridge's usage
    line was absent, or the call never reached the bridge at all) names
    no one to credit or blame the outcome to, so no outcome is recorded
    for it and record["learned"] is set to False, named on stderr. This
    is a control that PREVENTS a wrong attribution from ever reaching
    the learning ledger, not an audit that reports one after the fact."""
    if not decision:
        return
    if record.get("config"):
        # A CONFIGURATION FAULT TEACHES NOTHING ABOUT THE MODEL: it is left out of every quality denominator, named.
        record["learned"] = False
        sys.stderr.write("or_fanout: outcome not recorded, CONFIG_WAIT is a configuration fault (id=%s)\n" % record.get("id"))
        return
    if record.get("actual_model") is None:
        record["learned"] = False
        sys.stderr.write(
            "or_fanout: outcome not recorded, answering model unknown (id=%s)\n"
            % record.get("id"))
        return
    try:
        dream_record.record_outcome(
            decision[0], decision[1], "PASS" if record["ok"] else "FAIL",
            grader="or_fanout.exit_zero_and_nonempty_answer",
            cost=record.get("cost_usd"),  # D2 (2026-09-24): the bridge's billed figure when known, else None; never the estimate
            unit_id=str(record.get("id")),
            detail={"attempts": len(record.get("attempts", [])),
                    "seconds": record.get("seconds"),
                    "answered_by": record.get("actual_model")})
        record["learned"] = True
    except Exception as exc:
        record["learned"] = False
        sys.stderr.write("or_fanout: outcome not recorded (%s)\n" % str(exc)[:200])


def _pinned_root(workspace_root=None):
    """REQ-01 (L5a-8c): the one root every read and write under this module is
    confined to. The argument wins, else the environment; None from both is a
    refusal (a missing root BLOCKS, it never widens to the whole filesystem).
    The answer is the realpath of an existing directory that is not a
    filesystem root; anything else raises ValueError."""
    if workspace_root is not None and (not isinstance(workspace_root, str) or not workspace_root):
        raise ValueError("workspace_root must be a non-empty string or None, got %r" % (workspace_root,))
    root = workspace_root if workspace_root is not None else _workspace_from_env()
    if root is None:
        raise ValueError("no workspace root is pinned: neither the workspace_root argument "
                         "nor BROTHER_WORKSPACE_ROOT names one")
    root = os.path.realpath(root)
    if not os.path.isdir(root):
        raise ValueError("workspace root is not an existing directory: %r" % (root,))
    if root == os.path.dirname(root):
        raise ValueError("workspace root refused: a filesystem root confines nothing (%r)" % (root,))
    return root


def _load_prompt(job, workspace_root=None):
    if "prompt_file" in job:
        path = resolve_read(job["prompt_file"], _pinned_root(workspace_root))
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return job["prompt"]


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _repo_loop_dir():
    """scripts/loop under this repository's own root. A thin wrapper
    over the ONE shared resolver, repo_paths.repo_loop_dir, that
    openrouter_dispatch.py's F7 capability canary also calls (H9
    closure check, 2026-09-26): the two files used to carry separate
    copies of this same walk, and one copy kept a stale, one dirname
    hop short computation that only worked in practice because THIS
    file's own (already fixed) copy usually ran first in the same
    process and left the correct directory on sys.path already. A
    single shared implementation cannot drift out of sync with itself,
    and cannot depend on which of the two callers runs first."""
    return repo_paths.repo_loop_dir(__file__)


def _router():
    """The model router, or None when it cannot be loaded. Optional ON PURPOSE: or_fanout must keep
    working for the bridge models exactly as before if the router is missing, because this file is
    the funnel every loop job passes through and it may not become a new single point of failure.
    A model the router cannot vouch for is refused by _validate_job, so an absent router narrows
    what is allowed rather than widening it.

    WHICH FILE (2026-09-27). The directories were APPENDED to sys.path, so any earlier entry holding
    a model_router.py won (a PYTHONPATH naming the landing tree's scripts/loop loaded the landing
    copy in a proof) and a candidate missing its router fell through to the live ~/.claude/bin. The
    directory now goes FIRST, for this one import only, and the module loaded must be the file the
    router's own resolver names: code_root()/scripts/loop under a code root or a proof phase, else
    the directory chosen here. Any other file is None, which narrows."""
    import importlib, os as _os, sys as _sys
    try:
        loop = _repo_loop_dir() or _os.path.expanduser("~/.claude/bin")
        _sys.path.insert(0, loop)
        try:
            R = importlib.import_module("model_router")
        finally:
            _sys.path.remove(loop)
        if _os.environ.get("BROTHER_CODE_ROOT") or _os.environ.get("BROTHER_PROOF_PHASE"):
            loop = _os.path.join(R.code_root(), "scripts", "loop")
        if _os.path.realpath(R.__file__) != _os.path.realpath(_os.path.join(loop, "model_router.py")):
            return None
        return R
    except Exception:  # sbe: allow-silent the router is optional here by design: a router that cannot load, refuses (a proof with no code root) or is not the named file returns None, which narrows the accepted models to the bridge ids (_validate_job refuses the rest), never widens them
        return None


_MODEL_CALL_LOCK = threading.Lock()


def _model_call():
    """model_call from the directory the router was pinned to, or ImportError naming why.

    X3 finding 1 (Codex cross lane review, 2026-09-27, blocked RB): _router() stopped leaving scripts/loop on
    sys.path, and the native dispatch below still did a bare `import model_call`, so in a staged candidate every
    Claude and Codex job failed with ModuleNotFoundError before any model started. The rule F2 set for the router
    holds here: the module is that directory's model_call.py or nothing. The directory goes FIRST for this one import
    and a missing file refuses before any search, so a stray copy on sys.path is never imported, not even to be
    refused (model_call adds its own directory to sys.path when it loads, as it always did); a model_call already
    imported from anywhere else refuses. The import name is a literal on purpose: the freeze reads it statically and
    stages model_call into the candidate (a computed file loader is refused there). FAIL DIRECTION: no router, no file
    there, or a foreign module raises, and the job is recorded failed with nothing sent. Loaded under a lock: the fan
    out runs jobs on threads."""
    import importlib
    R = _router()
    if R is None:
        raise ImportError("the model router could not be loaded, so model_call has no pinned directory; nothing was sent")
    loop = os.path.dirname(os.path.realpath(R.__file__))
    want = os.path.realpath(os.path.join(loop, "model_call.py"))
    with _MODEL_CALL_LOCK:
        if "model_call" not in sys.modules and not os.path.isfile(want):
            raise ImportError("the pinned loop directory holds no model_call.py (%s); no other copy is loaded, "
                              "nothing was sent" % want)
        sys.path.insert(0, loop)
        try:
            M = importlib.import_module("model_call")
        finally:
            sys.path.remove(loop)
    got = os.path.realpath(getattr(M, "__file__", None) or "")
    if got != want:
        raise ImportError("the model_call loaded is %s, not the pinned %s; nothing was sent" % (got or "unknown", want))
    return M


def _known_models():
    """Every model this fan out will accept: the dispatch aliases (seated bridge rows), plus the registry's SEATABLE
    names (model_router.seatable_names, the stage gate): a shadow or retired row is refused here like chain() refuses it."""
    names = set(REAL_MODEL_IDS)
    R = _router()
    if R is not None:
        try:
            names |= set(R.seatable_names())
        except Exception:  # sbe: allow-silent an unreadable registry adds no model: the accepted set stays the bridge ids, which narrows and never widens (_validate_job refuses the rest)
            pass
    return names


def _transport_of(model):
    """bridge unless the registry says otherwise. An unknown model reads as bridge, which is the
    behaviour every caller had before this change, and _validate_job has already refused anything
    the registry does not know."""
    R = _router()
    if R is None:
        return "bridge"
    try:
        return R.registry().get(model, {}).get("transport", "bridge")
    except Exception:
        return "bridge"


def _assert_privacy(job, model):
    """Refuse to transmit content of a class this model may not receive.
    Returns the derived kind (build/decide/...), the one place this
    derivation happens, so F7's capability gate at dispatch() reuses the
    exact same kind rather than a second, possibly drifting copy of this
    same "job kind or a model default" rule.

    UNKNOWN SENSITIVITY FAILS CLOSED. A job with no sensitivity key is treated as PRIVATE, not as
    public, because a caller that forgot to label its content is exactly the caller whose content
    should not leave the machine. That default is stricter than the previous behaviour, which
    ignored the key entirely, and it is the right direction: a refused job costs one relaunch and
    a leaked one cannot be recalled.

    When the router cannot be loaded this REFUSES rather than waving the job through. An earlier
    version of this file made the router optional so or_fanout could not become a new single point
    of failure, and that reasoning is right for RANKING and wrong for a SAFETY gate: a gate that
    disappears when its dependency is missing is not a gate."""
    R = _router()
    if R is None:
        raise RuntimeError("the model router could not be loaded, so the privacy class of this "
                           "job cannot be checked; refusing to transmit")
    sensitivity = job.get("sensitivity") or R.PRIVATE
    kind = job.get("kind") or ("decide" if model == "jev" else "build")
    R.assert_may_send(model, sensitivity, kind)
    return kind


def extract_json(answer):
    """(json text, "") for the first complete JSON object or array in a model's answer, or (None, why).

    ONE PARSE FOR EVERY MODEL FAMILY (owner 2026-09-23: "It should adapt to each type of model"). The bridge path
    stripped a code fence and the off-bridge path (Claude, Codex) parsed the raw answer, so arm A of the A/B/C test
    lost 7 of 8 Sonnet builds per round to "Expecting value: line 1 column 1" while DeepSeek passed. Models differ in
    wrapping (a fence, a lead-in sentence, text after the object), never in what counts as the answer: the first
    complete JSON value. Anything that is not one is still refused: a cut-off document stays a failure."""
    text = (answer or "").strip()
    if not text:
        return None, "empty answer"
    dec = json.JSONDecoder()
    candidates = [text]
    candidates += re.findall(r"```(?:json|JSON)?\s*\n(.*?)\n?```", text, flags=re.S)   # fenced anywhere
    first = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if first:
        candidates.append(text[min(first):])                                          # after a lead-in
    why = "no JSON value found"
    for c in candidates:
        c = c.strip()
        try:
            obj, end = dec.raw_decode(c)
        except ValueError as exc:
            why = str(exc)[:120]
            continue
        if isinstance(obj, (dict, list)):
            return c[:end], ""
    return None, why


def _save_rejected(job, answer, workspace_root=None):
    """Keep a refused answer beside its output, so a parse refusal is read, never inferred (A/B/C 18:0x).
    REQ-01: this is an agent-reachable write, so it routes through the same
    resolver run_job uses; a refused resolution keeps the refusal it
    documents rather than leaking a write outside the pinned root."""
    try:
        out = resolve_write(job["out"], _pinned_root(workspace_root))
        # l5b: PROPAGATE_SAFE: a diagnostic copy that cannot be written never changes the refusal it documents
        os.makedirs(os.path.dirname(out), exist_ok=True)   # refusals come before the output's own mkdir
        # l5b: PROPAGATE_SAFE: a diagnostic copy that cannot be written never changes the refusal it documents
        with open(out + ".rejected.txt", "w", encoding="utf-8") as fh:
            fh.write(answer or "")
    except (OSError, KeyError, TypeError, ValueError):
        pass   # sbe: allow-silent a diagnostic copy that cannot be written never changes the refusal it documents


def _run_off_bridge(job, model, prompt, timeout, record, started, workspace_root=None):
    """Run a job on a transport the bridge does not speak: Claude or Codex.

    These do not reserve money from the OpenRouter ledger, because they do not spend OpenRouter
    money. Their cost is real but lives elsewhere, and recording a reservation against the wrong
    ledger would corrupt the one number the burn guard depends on. That is stated rather than
    quietly skipped.

    The privacy gate still applies, and it is model_call that applies it at the wire."""
    model_call = _model_call()
    R = _router()
    kind = job.get("kind") or ("decide" if model == "jev" else "build")
    sensitivity = job.get("sensitivity") or R.PRIVATE   # undeclared is private here as on the bridge path (2026-09-24)
    attempt = model_call.call_one(model, prompt, kind, sensitivity, timeout=timeout)
    record.update(seconds=round(time.time() - started, 1), actual_model=model)
    if not attempt.ok:
        record.update(ok=False, error=attempt.detail[:200])
        if getattr(attempt, "failure", None) == "CONFIG":
            record["config"] = True
        return record
    answer = attempt.answer
    # The SAME guarantees the bridge path gives, never weaker ones: a job that asked for JSON gets
    # its answer parsed before it is written, and the write is a temp file plus an atomic replace,
    # so a symlink planted at `out` is replaced rather than followed and a crash never leaves half
    # an answer on disk. Writing this path more loosely would make the off bridge models the soft
    # underbelly of a control the bridge models pass.
    if job.get("expect") == "json":
        body, why = extract_json(answer)
        if body is None:
            _save_rejected(job, answer, workspace_root)
            record.update(ok=False, error="answer is not valid JSON: %s" % why)
            return record
        answer = body
    out = resolve_write(job["out"], _pinned_root(workspace_root))   # L5a-8c: never an unconfined abspath
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".or-fanout-", dir=os.path.dirname(out))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(answer + "\n")
        os.replace(tmp, out)
    except BaseException:
        try:
            # l5b: PROPAGATE_SAFE: removing the temp file is cleanup on a failed write; the original error is re-raised below
            os.unlink(tmp)
        except OSError:
            pass
        raise
    record.update(ok=True, out=out, bytes=len(answer))
    return record


def _validate_job(job, timeout):
    """The jobs file is a trust boundary; every field is checked before anything
    is dispatched or reserved. Proven 2026-09-20: a job id with a newline became
    an injected log line and holder id, a negative max and a NaN cost were
    passed through, and a negative timeout reached the dispatcher."""
    if not isinstance(job, dict):
        raise ValueError("a job must be an object, got %s" % type(job).__name__)
    jid = job.get("id")
    if not isinstance(jid, str) or not _SAFE_ID_RE.match(jid):
        raise ValueError("job id must be one plain token (letters, digits, . _ : -), got %r" % (jid,))
    model = job.get("model")
    # one guard for every caller: a model value that is not a plain string is
    # refused here, so an unhashable list or dict never reaches the set
    # membership test and never raises TypeError through this boundary.
    # The allowlist is REAL_MODEL_IDS plus whatever the data driven registry declares, so adding a
    # Claude tier or a Codex model is a row in docs/plan/model-registry.json and not an edit here.
    # The type guard stays FIRST and unchanged: a model value that is not a plain string is refused
    # before any set membership test, so an unhashable list never raises TypeError through this
    # boundary. That guard was proved necessary on 2026-09-20 and is not relaxed by this change.
    if not isinstance(model, str) or model not in _known_models():
        raise ValueError("unknown model %r" % (model,))
    if ("prompt" in job) == ("prompt_file" in job):
        raise ValueError("a job needs exactly one of prompt and prompt_file")
    if not isinstance(job.get("out"), str) or not job["out"]:
        raise ValueError("a job needs an out path")
    ceiling = max(MODEL_MAX_TOKENS.values())
    if "max" in job and job["max"] is not None:
        value = job["max"]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= ceiling:
            raise ValueError("max must be an integer in (0, %d], got %r" % (ceiling, value))
    cost = job.get("estimated_cost")
    # M4 sibling contract: None or an absent key both mean "let dispatch
    # derive the reservation" (M4.3's estimator, or the price catalog's
    # worst case when there is no history yet), never a flat number typed
    # here. Only a value that IS given must be a real, finite, non negative
    # number; an explicit garbage value still blocks.
    if cost is not None:
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise ValueError("estimated_cost must be a finite number >= 0, got %r" % (cost,))
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite number > 0, got %r" % (timeout,))
    if job.get("effort", DEFAULT_EFFORT) not in ("minimal", "low", "medium", "high", "xhigh"):
        raise ValueError("unknown effort %r" % (job.get("effort"),))


def _workspace_from_env():
    """REQ-01: the one trusted workspace-root source, read from the process
    environment the caller pins before reading any job. It is never derived
    from job["out"], because the untrusted value under test can never serve
    as its own boundary."""
    value = os.environ.get("BROTHER_WORKSPACE_ROOT")
    if isinstance(value, str) and value.strip():
        return os.path.realpath(os.path.expanduser(value))
    return None


def resolve_read(path, root):
    """REQ-01: resolve a read path under the pinned workspace root. Refuses a
    non-string or empty path, a dot-dot segment, and any realpath that leaves
    root (a symlink escape is never a shortcut). An absolute path is accepted
    only when it resolves inside root (L5a-8a: every production caller writes
    absolute paths, so a blanket refusal meant no caller could pin a root).
    Raises ValueError, which run_job records as a blocked job, never a silent
    accept. A missing or non-string root also refuses: unknown input blocks."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    parts = path.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError("dot-dot segment refused: %r" % (path,))
    real_root = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(real_root, path))
    if candidate != real_root and not candidate.startswith(real_root + os.sep):
        raise ValueError("read path escapes root: %r" % (path,))
    return candidate


def resolve_write(rel, root):
    """REQ-01: resolve a write path under the pinned workspace root. Same
    refusals as resolve_read, and the returned path stays inside root so a
    makedirs or a temp file beside it never escapes. The PARENT is resolved
    and the final name is kept as written (L5a-8a): a link planted at the out
    path is then replaced by run_job's temp file plus os.replace, never
    followed, while a link in the parent chain is still judged by its target."""
    if not isinstance(rel, str) or not rel:
        raise ValueError("rel must be a non-empty string")
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string")
    parts = rel.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError("dot-dot segment refused: %r" % (rel,))
    real_root = os.path.realpath(root)
    joined = os.path.join(real_root, rel)
    parent, name = os.path.split(joined)
    if name in ("", ".", ".."):
        raise ValueError("write path needs a file name: %r" % (rel,))
    real_parent = os.path.realpath(parent)
    if real_parent != real_root and not real_parent.startswith(real_root + os.sep):
        raise ValueError("write path escapes root: %r" % (rel,))
    return os.path.join(real_parent, name)


class ConfigWait(RuntimeError):
    """The configuration breaker holds this model, or the program just said it does not know it: CONFIG_WAIT."""


def run_job(job, timeout, workers, dispatch=None, workspace_root=None):
    dispatch = dispatch or openrouter_dispatch.dispatch
    started = time.time()
    # a job that is not an object must become a recorded failure, never an
    # exception that takes the whole batch down
    as_dict = job if isinstance(job, dict) else {}
    jid = as_dict.get("id")
    # an id that is not a plain token is shown escaped, everywhere it is shown:
    # a raw newline in it would forge a line in the console and the results file
    shown = jid if isinstance(jid, str) and _SAFE_ID_RE.match(jid) else repr(jid)[:80]
    record = {"id": shown, "model": _shown_model(job), "ok": False,
              "effort": as_dict.get("effort", DEFAULT_EFFORT) if isinstance(as_dict.get("effort", DEFAULT_EFFORT), str) else None}
    try:
        _validate_job(job, timeout)
        # L5a-8c: the root is pinned once, inside the try, so a missing root is a
        # recorded failure with ok false, never an exception that takes the batch down
        workspace_root = _pinned_root(workspace_root)
        model = job["model"]
        prompt = _load_prompt(job, workspace_root)
        if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ValueError("prompt exceeds %d bytes" % MAX_PROMPT_BYTES)
        # THE WIRE GATE, ON EVERY PATH. It used to sit only on the off bridge branch below, which
        # is the transport it LEAST needed to guard: the bridge is OpenRouter, the third party that
        # may retain what it is sent. An adversarial review handed a job carrying
        # sensitivity "private" to this function and watched the secret reach the bridge argv, exit
        # ok. The gate was advertised as "enforced at the wire" and was not enforced on the wire
        # that mattered.
        #
        # It is applied HERE, before the branch, so every transport passes it and a future branch
        # cannot be added below it without passing it either.
        kind = _assert_privacy(job, model)
        if openrouter_dispatch.STOPPED.is_set():
            # a job still queued when the fan out got TERM starts nothing (objection 9)
            raise openrouter_dispatch.Stopped("the fan out was told to stop before this job started")
        if _transport_of(model) != "bridge":
            return _run_off_bridge(job, model, prompt, timeout, record, started, workspace_root)
        # THE CONFIGURATION GATE ON THE BRIDGE BRANCH TOO (2026-09-30): the same admission and the same classifier
        # model_call.call_one applies, so no transport bypasses the configuration breaker. The loop's model_call is
        # required here exactly as the router is for the privacy gate above: one that cannot load refuses the job.
        M = _model_call()
        _held = M.config_admit("bridge", REAL_MODEL_IDS[model])
        if _held:
            raise ConfigWait(_held)
        max_tokens = int(job.get("max") or MODEL_MAX_TOKENS.get(model, 32000))
        argv = [sys.executable, BRIDGE_PATH, "--model", model, "--effort",
                job.get("effort", DEFAULT_EFFORT), "--max", str(max_tokens),
                "--timeout", str(timeout)]
        if model == "jev":
            argv.append("--decisions")
        # "--" ends option parsing: a prompt that begins with dashes ("--help",
        # "--model x") is text, never a bridge flag.
        argv += ["--", prompt]
        job_cost = job.get("estimated_cost")
        result, actual = dispatch(
            bridge_argv=argv, requested_model=REAL_MODEL_IDS[model],
            estimated_cost=None if job_cost is None else float(job_cost),
            holder_id=str(job["id"]), timeout_seconds=timeout,
            max_tokens=max_tokens, min_max_tokens=1000,
            jev_question_type=job.get("jev_type") if model == "jev" else None,
            model_alias=model, kind=kind,   # REQ-FAN-1: workers bounds the pool, never the deployment's slot ceiling
            admit=lambda: M.config_admit("bridge", REAL_MODEL_IDS[model]),   # again after the slot wait (R1 F6)
        )
        # D2 / audit issue F34: set identity and usage on the record as soon as
        # they are known, BEFORE any later failure in this function (a bridge
        # nonzero exit, an empty answer, a JSON parse refusal) can raise. A
        # dispatch that answered but was then rejected here must still name
        # who actually answered; both values are JSON safe (a string/None and
        # a plain dict/None), never the UsageObservation object whose type
        # broke json.dump across four reverted landings of this fix.
        record["actual_model"] = actual
        # D2.6 REQ-FAN-2: dispatch() returns a DispatchResult, which unpacks as (itself, actual_model), so `result`
        # carries the settlement. A settle state or reservation id that is missing, or not one the dispatcher
        # writes, is NO-DATA (None), never recorded as present.
        settle_state = getattr(result, "settle_state", None)
        record["settle_state"] = settle_state if settle_state in ("RECONCILED", "RETAINED") else None
        reservation_id = getattr(result, "reservation_id", None)
        record["reservation_id"] = reservation_id if isinstance(reservation_id, str) and reservation_id.strip() else None
        record["usage"] = parse_usage_envelope(result.stderr or "")
        answer = (result.stdout or "").strip()
        record["cost_usd"] = billed_usd(result.stderr or "")   # D2 wiring (2026-09-24): the bridge's own billed figure, or None
        if result.returncode != 0 and M.config_outcome("bridge", REAL_MODEL_IDS[model], result.returncode, result.stdout or "",
                                                       result.stderr or "", "fanout-%s-%s" % (os.getpid(), shown), prompt=prompt):
            raise ConfigWait("the program does not know this model: %s" % next(
                (l for l in M.BR.config_channels("bridge", result.stdout or "", result.stderr or "", None, prompt).splitlines()
                 if M.BR.is_config(l)), "")[:200])
        if result.returncode != 0 or not answer:
            raise RuntimeError("bridge exit %s, %d answer bytes: %s" % (
                result.returncode, len(answer), (result.stderr or "")[-300:]))
        if job.get("expect") == "json":
            # non-empty is not valid: a lane that promised JSON and delivered a
            # cut-off document (seen live 2026-09-20) is a failure, so it is retried
            body, why = extract_json(answer)
            if body is None:
                _save_rejected(job, answer, workspace_root)
                raise RuntimeError("answer is not valid JSON: %s" % why)
            answer = body
        out = resolve_write(job["out"], workspace_root)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        # temp file plus atomic replace: a symlink planted at `out` is
        # replaced, never followed, and a crash never leaves half an answer.
        fd, tmp = tempfile.mkstemp(prefix=".or-fanout-", dir=os.path.dirname(out))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(answer + "\n")
            os.replace(tmp, out)
        except BaseException:
            try:
                # l5b: PROPAGATE_SAFE: removing the temp file is cleanup on a failed write; the original error is re-raised below
                os.unlink(tmp)
            except OSError:
                pass
            raise
        record.update(ok=True, out=out, bytes=len(answer))  # actual_model, usage set above
    except (ConfigWait, openrouter_dispatch.ConfigHeld) as exc:
        record["config"] = True
        record["error"] = redact_text("CONFIG_WAIT: %s" % exc)[:400]
    except Exception as exc:  # recorded by name below, never swallowed
        # capped and redacted: TimeoutExpired's text carries argv, which carries
        # the whole prompt, and a bridge error can echo a key back. L5a-2 REQ-07
        # runs the redactor before this text reaches the results file or a log.
        record["error"] = redact_text(
            "%s: %s" % (type(exc).__name__, exc))[:400]
    record["seconds"] = round(time.time() - started, 1)
    return record


def _defer_term(signum, frame):
    """TERM stops new work and lets calls in flight end (objection 9). The same TERM, sent to the process group,
    reaches each running bridge, whose own handler exits; dispatch then writes that reservation's terminal row and
    the job is recorded. Dying here instead left RESERVE and DISPATCH_START with no terminal row."""
    openrouter_dispatch.STOPPED.set()


def main(argv=None, workspace_root: str = None):
    previous = signal.signal(signal.SIGTERM, _defer_term)
    try:
        code = _main(argv, workspace_root)
    finally:
        signal.signal(signal.SIGTERM, previous)
    # a stopped fan out says so in its exit code, after every record is written
    return 128 + signal.SIGTERM if openrouter_dispatch.STOPPED.is_set() else code


def _argv_tokens(argv):
    """Edge-case law: argparse's own argv contract is a list of strings.
    A generator, a bare string, bytes or any other type is corrupt input and
    BLOCKS with the module's own named refusal and a nonzero code, never a
    raw SystemExit and never a silent accept of a type argparse would coerce."""
    if argv is None:
        return None
    if isinstance(argv, (list, tuple)):
        for token in argv:
            if not isinstance(token, str):
                raise ValueError("argv tokens must be strings, got %s"
                                 % type(token).__name__)
        return list(argv)
    raise ValueError("argv must be a list of strings or None, got %s"
                     % type(argv).__name__)


def _main(argv=None, workspace_root=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("jobs")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--results", default=None)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--deadline", type=float, default=None)
    try:
        argv = _argv_tokens(argv)
    except ValueError as exc:
        print("or_fanout: argv refused: %s" % exc, file=sys.stderr)
        return 2
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse's own exit for --help or a bad flag is a control that
        # REPORTS: its numeric code becomes this function's result, so a
        # caller that runs main() as code never sees a raw SystemExit, and a
        # code argparse leaves absent is corrupt input and BLOCKS.
        code = exc.code
        return code if isinstance(code, int) and not isinstance(code, bool) else 2

    try:
        with open(args.jobs, "rb") as fh:
            raw = fh.read()
        jobs = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print("or_fanout: cannot read jobs file: %s" % exc, file=sys.stderr)
        return 2
    if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
        print("or_fanout: the jobs file must be a list of objects", file=sys.stderr)
        return 2
    if not jobs:
        print("or_fanout: the jobs file must name at least one job", file=sys.stderr)
        return 2
    seen = set()
    for j in jobs:
        jid = j.get("id")
        if not isinstance(jid, str):
            print("or_fanout: every job needs a string id", file=sys.stderr)
            return 2
        if jid in seen:
            print("or_fanout: every job needs a unique id", file=sys.stderr)
            return 2
        seen.add(jid)

    results_path = args.results or (os.path.splitext(args.jobs)[0] + ".results.json")
    # L5a-8c: the root is the caller's (argument, else environment), else the directory
    # holding the jobs file: this process's own argv, which no job field can move
    if workspace_root is None and _workspace_from_env() is None:
        workspace_root = os.path.realpath(os.path.dirname(os.path.abspath(args.jobs)))
    try:
        workspace_root = _pinned_root(workspace_root)
        results_path = resolve_write(results_path, workspace_root)
    except ValueError as exc:
        print("or_fanout: results path refused: %s" % exc, file=sys.stderr)
        return 2
    deadline_s = args.deadline
    if deadline_s is None:
        deadline_s = max(args.timeout * (args.retries + 1), 1.0)
    records = run_wave(jobs, args.timeout, args.workers, args.retries, deadline_s, results_path,
                       workspace_root=workspace_root)
    failed = [r for r in records if not r["ok"]]
    for r in records:
        print("%-4s %-28s %-9s %6.1fs %s" % (
            "OK" if r["ok"] else "FAIL", r["id"], r.get("model", ""), r.get("seconds", 0.0),
            r.get("out") or r.get("error")))
    print("or_fanout: %d ok, %d failed of %d; results: %s" % (
        len(records) - len(failed), len(failed), len(records), results_path))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
