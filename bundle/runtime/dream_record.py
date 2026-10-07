#!/usr/bin/env python3
"""dream_record: record a control decision and, later, its outcome (unit D0).

The first half of the self-learning layer (docs/architecture/
DREAM-RSI-SELF-LEARNING-ANALYSIS.md). A policy can only be replayed against
decisions that were recorded with the options that were on the table, the
option taken, the policy version that took it, and what happened next. This
module records exactly that and nothing else; it is useful with no learner.

Storage reuses scripts/journal.py, never a second log. A journal line must
stay under its atomic-append bound, so the full record is written to a
content-addressed artifact under <run_dir>/dream/ and the journal event
carries its reference. An outcome is a child event of its decision.

Failure direction: a malformed record is refused loudly (ValueError) before
anything is written; a write that fails returns None after the journal's own
one-line notice, and the run goes on. An unmeasured cost is None, never 0.
"""
import datetime
import hashlib
import json
import math
import os
import sys
import threading
import uuid
from typing import List

DECISION = "dream.decision"
OUTCOME = "dream.outcome"
STATUSES = ("PASS", "FAIL", "NO-DATA")
ARTIFACT_DIR = "dream"

SCHEMA = 'brother-learning-event-v1'
NODATA = 'NO-DATA'
INTENT = 'dream.intent'

_BASE_KEYS = ('schema', 'type', 'event_id', 'at', 'run_id')
_OPTIONAL_KEYS = ('session_id', 'unit_id', 'parent_ids')
_TYPE_FIELDS = {
    DECISION: {'kind', 'observed', 'options', 'chosen', 'policy_version', 'cost'},
    OUTCOME: {'status', 'grader', 'cost', 'detail'},
    INTENT: {'decision_event_id', 'idempotency_key', 'action_handle', 'policy_version', 'limits_hash', 'cost'},
}


def no_data(reason):
    if not isinstance(reason, str) or not reason.strip():  # a blank reason explains nothing
        raise ValueError('reason must be a non-empty string')
    return {'state': NODATA, 'reason': reason}


def is_no_data(value):
    return isinstance(value, dict) and value.get('state') == NODATA


def validate_event_id(value):
    if not isinstance(value, str) or len(value) != 32:
        raise ValueError('event_id must be 32 lowercase hex characters')
    for ch in value:
        if ch not in '0123456789abcdef':
            raise ValueError('event_id must be 32 lowercase hex characters')
    return value


def validate_ref(value):
    if not isinstance(value, str):
        raise ValueError('ref must be a string')
    prefix = 'learning/events/'
    suffix = '.json'
    if not value.startswith(prefix) or not value.endswith(suffix):
        raise ValueError('ref must be learning/events/<32 lowercase hex>.json')
    validate_event_id(value[len(prefix):-len(suffix)])
    return value


def _is_nonempty_str(value):
    return isinstance(value, str) and bool(value)


def _validate_cost(value):
    if value is None or is_no_data(value):
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('cost must be None, NO-DATA, or a finite number >= 0')


def validate_event(record):
    if not isinstance(record, dict):
        raise ValueError('event must be a dict')
    for key in _BASE_KEYS:
        if key not in record:
            raise ValueError('event is missing required key %s' % key)
    if record['schema'] != SCHEMA:
        raise ValueError('schema must be %s' % SCHEMA)
    if not isinstance(record['type'], str) or record['type'] not in (DECISION, OUTCOME, INTENT):
        raise ValueError('type must be one of %s' % ((DECISION, OUTCOME, INTENT),))
    validate_event_id(record['event_id'])
    if not isinstance(record['at'], str):
        raise ValueError('at must be an ISO 8601 string with a UTC offset')
    import datetime
    text = record['at']
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise ValueError('at must be an ISO 8601 string with a UTC offset')
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('at must include a UTC offset')
    run_id = record['run_id']
    if not _is_nonempty_str(run_id) or os.path.basename(run_id) != run_id or run_id in ('.', '..'):
        raise ValueError('run_id must be the bare name of the run folder, never a path')
    for key in ('session_id', 'unit_id'):  # optional means absent; None is never a contract value
        if key in record and not _is_nonempty_str(record[key]):
            raise ValueError('%s must be a non-empty string when present' % key)
    if 'parent_ids' in record:
        parents = record['parent_ids']
        if not isinstance(parents, (list, tuple)) or isinstance(parents, (str, bytes)):
            raise ValueError('parent_ids must be a list or tuple of event ids')
        for parent in parents:
            validate_event_id(parent)
    allowed = set(_BASE_KEYS) | set(_OPTIONAL_KEYS) | _TYPE_FIELDS.get(record['type'], set())
    for key in record:
        if key not in allowed and not (isinstance(key, str) and key.startswith('x.')):
            raise ValueError('unknown key %r' % (key,))
    _strict_json(record, 'the event')  # NaN, Infinity or a value json cannot encode, anywhere in it
    kind = record['type']
    if kind == DECISION:
        if 'kind' in record and not _is_nonempty_str(record['kind']):
            raise ValueError('kind must be a non-empty string')
        if 'policy_version' in record and not _is_nonempty_str(record['policy_version']):
            raise ValueError('policy_version must be a non-empty string')
        if 'options' in record:
            options = record['options']
            if isinstance(options, (str, bytes)) or not isinstance(options, (list, tuple)) or not options:
                raise ValueError('options must be a non-empty list or tuple')
            if 'chosen' in record and record['chosen'] not in options:
                raise ValueError('chosen must be among options')
        elif 'chosen' in record:
            raise ValueError('chosen requires options')
        if 'cost' in record:
            _validate_cost(record['cost'])
    elif kind == OUTCOME:
        if 'status' in record and record['status'] not in STATUSES:
            raise ValueError('status must be one of %s' % (STATUSES,))
        if 'grader' in record and not _is_nonempty_str(record['grader']):
            raise ValueError('grader must be a non-empty string')
        if 'detail' in record and record['detail'] is not None and not isinstance(record['detail'], dict):
            raise ValueError('detail must be a dict or None')
        if 'cost' in record:
            _validate_cost(record['cost'])
    elif kind == INTENT:
        if 'decision_event_id' in record:
            validate_event_id(record['decision_event_id'])
        for key in ('idempotency_key', 'action_handle', 'policy_version'):
            if key in record and not _is_nonempty_str(record[key]):
                raise ValueError('%s must be a non-empty string' % key)
        if 'limits_hash' in record and record['limits_hash'] is not None and not isinstance(record['limits_hash'], (str, dict)):
            raise ValueError('limits_hash must be a string, dict or None')
        if 'cost' in record:
            _validate_cost(record['cost'])


_JOURNAL_LOCK = threading.Lock()
_JOURNAL = None


def _journal():
    """scripts/journal.py, by a plain `import journal` a static reading can
    follow (B5-04: the frozen candidate's one computed loader was here): the
    checkout's scripts directory, then this file's own (installed flat, the
    journal ships beside it), go first on sys.path. The module imported must be
    the file journal_path() reports, so what this module reports is what it
    runs; a divergence, BROTHER_JOURNAL_PATH naming another file included,
    refuses instead of loading. Published only after the import finished,
    under a lock: fan-out calls this from many threads at once."""
    global _JOURNAL
    if _JOURNAL is not None:
        return _JOURNAL
    with _JOURNAL_LOCK:
        if _JOURNAL is None:
            path = journal_path()
            here = os.path.dirname(os.path.abspath(__file__))
            scripts = os.path.join(os.path.abspath(os.path.join(here, "..", "..", "..", "..")), "scripts")
            for folder in (here, scripts):
                if folder not in sys.path:
                    sys.path.insert(0, folder)
            import journal
            if os.path.realpath(journal.__file__) != os.path.realpath(path):
                raise RuntimeError("journal imported from %s is not the reported journal %s"
                                   % (journal.__file__, path))
            _JOURNAL = journal
    return _JOURNAL


def _cost(value):
    if value is None or is_no_data(value):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value < 0:
        raise ValueError("cost must be None, NO-DATA, or a finite number >= 0, got %r" % (value,))
    return value


def _strict_json(value, name):
    """The one serializer. NaN and Infinity are not JSON, and a value json
    cannot encode is a contract refusal, not a write failure to shrug off."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s must be strict JSON (%s)" % (name, exc)) from None


def _write_artifact(run_dir, record):
    body = _strict_json(record, "the record").encode("utf-8")
    name = hashlib.sha256(body).hexdigest() + ".json"
    folder = os.path.join(run_dir, ARTIFACT_DIR)
    os.makedirs(folder, exist_ok=True)
    final = os.path.join(folder, name)
    if not os.path.exists(final):  # same content, same name: a rewrite is a no-op
        tmp = "%s.%d.tmp" % (final, os.getpid())
        with open(tmp, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
    return os.path.join(ARTIFACT_DIR, name)


def record_decision(run_dir, kind, observed, options, chosen, policy_version,
                    cost=None, parent_ids=(), unit_id=None, session_id=None):
    """Returns the decision's event id, or None when nothing durable was
    written. Replay can only score a policy over options that were recorded,
    so `options` must be non-empty and hold `chosen`."""
    if not isinstance(kind, str) or not kind:
        raise ValueError("kind must be a non-empty string")
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError("policy_version must be a non-empty string")
    if not isinstance(options, (list, tuple)):  # a str would explode into letters
        raise ValueError("options must be a list or tuple of strings, got %s" % type(options).__name__)
    options = list(options)
    if not options:
        raise ValueError("options must be non-empty: a decision with no recorded alternatives cannot be replayed")
    if not all(isinstance(o, str) and o for o in options):
        raise ValueError("every option must be a non-empty string")
    if chosen not in options:
        raise ValueError("chosen %r is not among the recorded options" % (chosen,))
    record = {"schema": "brother-dream-decision-v1", "kind": kind,
              "observed": observed, "options": options, "chosen": chosen,
              "policy_version": policy_version, "cost": _cost(cost)}
    _strict_json(record, "observed")  # refuse before any write; below only real I/O can fail
    _check_line_size(str(run_dir), DECISION,
                     {"kind": kind,
                      "artifact": _artifact_path(str(run_dir), record),
                      "policy_version": policy_version},
                     parent_ids, unit_id, session_id)
    try:
        artifact = _write_artifact(str(run_dir), record)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write("dream_record: could not write the %s decision artifact (%s); "
                         "the run continues without it\n" % (kind, exc))
        return None
    return _journal().append(run_dir, DECISION, parent_ids=parent_ids, unit_id=unit_id,
                             session_id=session_id,
                             payload={"kind": kind, "artifact": artifact,
                                      "policy_version": policy_version})


def record_outcome(run_dir, decision_event_id, status, grader, cost=None,
                   unit_id=None, session_id=None, detail=None):
    """The graded result of one decision, as its child event. `grader` names
    the deterministic check that decided the status; a model never does."""
    if status not in STATUSES:
        raise ValueError("status must be one of %s, got %r" % (STATUSES, status))
    if not decision_event_id:
        raise ValueError("an outcome needs the event id of the decision it grades")
    if not isinstance(grader, str) or not grader:
        raise ValueError("grader must name the check that decided the status")
    if detail is not None and not isinstance(detail, dict):  # evidence is refused, never dropped
        raise ValueError("detail must be a dict or None, got %s" % type(detail).__name__)
    detail = {} if detail is None else detail
    _strict_json(detail, "detail")
    payload = {"status": status, "grader": grader,
               "cost": _cost(cost),
               "detail": detail}
    _check_line_size(run_dir, OUTCOME, payload, parent_ids=(decision_event_id,),
                     unit_id=unit_id, session_id=session_id)
    return _journal().append(run_dir, OUTCOME, parent_ids=(decision_event_id,),
                             unit_id=unit_id, session_id=session_id,
                             payload=payload)


def run_dir_from_env():
    """The declared run directory, or None: recording is opt-in per run."""
    return _journal().run_dir_from_env()


_ARTIFACT_RE = None


def _read_artifact(run_dir, ref):
    """The record a journal line points at, or None. The reference is a trust
    boundary (a journal line can be corrupt or edited): it must be exactly
    dream/<sha256>.json, so it can never name a file outside this run's
    artifact folder, and the bytes must still hash to that name, so a record
    changed after the fact reads as NO record rather than as evidence."""
    import re
    global _ARTIFACT_RE
    if _ARTIFACT_RE is None:
        _ARTIFACT_RE = re.compile(r"^%s/([0-9a-f]{64})\.json$" % re.escape(ARTIFACT_DIR))
    match = _ARTIFACT_RE.match(ref) if isinstance(ref, str) else None
    if not match:
        return None
    try:
        with open(os.path.join(run_dir, ARTIFACT_DIR, match.group(1) + ".json"), "rb") as fh:
            body = fh.read()
    except OSError:
        return None
    if hashlib.sha256(body).hexdigest() != match.group(1):
        return None
    try:
        record = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def read_decisions(run_dir):
    """Every recorded decision joined to its outcomes, in journal order, or
    None when the run has no journal (NO-DATA, not an empty list). A decision
    whose artifact is missing or corrupt keeps record=None; one with no
    outcome is ungraded; one with outcomes that disagree is a conflict and is
    never silently resolved."""
    events = _journal().read(run_dir)
    if events is None:
        return None
    rows, by_id = [], {}
    for event in events:
        if event.get("type") == DECISION:
            payload = event.get("payload") or {}
            if "event_ref" in payload:
                record = _read_event_record(str(run_dir), payload.get("event_ref"), payload.get("digest"))
            else:
                record = _read_artifact(str(run_dir), payload.get("artifact"))
            row = {"event_id": event["event_id"], "at": event.get("at"),
                   "unit_id": event.get("unit_id"), "kind": payload.get("kind"),
                   "policy_version": payload.get("policy_version"),
                   "record": record, "outcomes": []}
            rows.append(row)
            by_id[event["event_id"]] = row
        elif event.get("type") == OUTCOME:
            for parent in event.get("parent_ids") or ():
                if parent in by_id:
                    by_id[parent]["outcomes"].append(event.get("payload") or {})
    for row in rows:
        statuses = {o.get("status") for o in row["outcomes"]}
        row["state"] = ("ungraded" if not statuses else
                        "conflict" if len(statuses) > 1 else "graded")
    return rows


def journal_candidates():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    candidates = []
    env = os.environ.get("BROTHER_JOURNAL_PATH")
    if env:
        candidates.append(env)
    candidates.append(os.path.join(root, "scripts", "journal.py"))
    candidates.append(os.path.join(root, "plugin", "runtime", "brother", "scripts", "journal.py"))
    candidates.append(os.path.join(here, "journal.py"))
    return candidates


def journal_path():
    tried = []
    env = os.environ.get("BROTHER_JOURNAL_PATH")
    for path in journal_candidates():
        tried.append(path)
        if os.path.isfile(path):
            return path
        if env and path == env:
            break  # a journal named by the environment and not there is a refusal, never a quiet fall through
    raise RuntimeError("journal module not found; tried: %s" % ", ".join(tried))


def _artifact_path(run_dir, record):
    body = _strict_json(record, "the record").encode("utf-8")
    name = hashlib.sha256(body).hexdigest() + ".json"
    return os.path.join(ARTIFACT_DIR, name)


def _check_line_size(run_dir, event_type, payload, parent_ids=(), unit_id=None, session_id=None):
    journal = _journal()
    event = {
        "event_id": "0" * 32,
        "parent_ids": [str(p) for p in (parent_ids or ()) if p],
        "run_id": os.path.basename(os.path.normpath(str(run_dir))),
        "session_id": session_id,
        "unit_id": None if unit_id is None else str(unit_id),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "type": str(event_type),
        "payload": payload if payload is not None else {},
    }
    line = journal._line(event)
    if b"payload_truncated" in line:
        raise ValueError("journal payload would be truncated; reduce the event size")


def write_event(run_dir, event_id, record):
    run_dir = str(run_dir or "").strip()
    if not run_dir:
        return None
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    try:
        body = json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("record must be JSON serializable (%s)" % exc) from None
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    folder = os.path.join(run_dir, "learning", "events")
    final = os.path.join(folder, event_id + ".json")
    tmp = None
    try:
        os.makedirs(folder, exist_ok=True)
        if os.path.exists(final):
            with open(final, "rb") as fh:
                if fh.read() == body:
                    return digest
        tmp = "%s.%d.%s.tmp" % (final, os.getpid(), uuid.uuid4().hex)
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, body)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, final)
        try:
            # l5b: PROPAGATE_SAFE: directory fsync is best effort durability; the file itself was fsynced and replaced above
            dir_fd = os.open(folder, os.O_RDONLY)
            try:
                # l5b: PROPAGATE_SAFE: directory fsync is best effort durability; the file itself was fsynced and replaced above
                os.fsync(dir_fd)
            finally:
                # l5b: PROPAGATE_SAFE: directory fsync is best effort durability; the file itself was fsynced and replaced above
                os.close(dir_fd)
        except OSError:
            pass
        return digest
    except OSError as exc:
        if tmp is not None and os.path.exists(tmp):
            try:
                # l5b: PROPAGATE_SAFE: removing the temp file is cleanup on a failed write; the original error is reported to stderr below
                os.unlink(tmp)
            except OSError:
                pass
        sys.stderr.write("dream_record: could not write event %s to %s (%s); the run continues without it\n"
                         % (event_id, run_dir, exc))
        return None


def read_event_bytes(run_dir, event_ref):
    run_dir = str(run_dir or "").strip()
    if not run_dir or not isinstance(event_ref, str) or not event_ref:
        return None
    try:
        base = os.path.abspath(run_dir)
        path = os.path.abspath(os.path.join(base, event_ref))
        if path != base and not path.startswith(base + os.sep):
            return None
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _read_event_record(run_dir, event_ref, digest):
    if not isinstance(event_ref, str) or not isinstance(digest, str):
        return None
    if not digest.startswith("sha256:") or len(digest) != 71:
        return None
    body = read_event_bytes(run_dir, event_ref)
    if body is None:
        return None
    if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        return None
    try:
        record = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    try:
        validate_event(record)
    except ValueError:
        return None
    return record


def _append_reference(run_dir, event_id, event_type, digest, summary,
                      parent_ids=(), unit_id=None, session_id=None):
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError("digest must be sha256:<64 hex>")
    if not isinstance(summary, dict):
        raise ValueError("summary must be a dict")
    if event_type in (DECISION, INTENT):
        if set(summary) != {"kind", "chosen"}:
            raise ValueError("summary must have kind and chosen")
    elif event_type == OUTCOME:
        if set(summary) != {"status", "grader"}:
            raise ValueError("summary must have status and grader")
    else:
        raise ValueError("unknown event_type %r" % (event_type,))
    payload = {"event_ref": "learning/events/%s.json" % event_id,
               "digest": digest,
               "summary": summary}
    return _journal().append(run_dir, event_type, parent_ids=parent_ids,
                             unit_id=unit_id, session_id=session_id,
                             payload=payload, event_id=event_id)


def record_intent(run_dir, decision_event_id, idempotency_key, action_handle,
                  policy_version, limits_hash=None, cost=None,
                  parent_ids=(), unit_id=None, session_id=None):
    if not isinstance(run_dir, str) or not run_dir:
        raise ValueError("run_dir must be a non-empty string")
    validate_event_id(decision_event_id)
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValueError("idempotency_key must be a non-empty string")
    if not isinstance(action_handle, str) or not action_handle:
        raise ValueError("action_handle must be a non-empty string")
    if not isinstance(policy_version, str) or not policy_version:
        raise ValueError("policy_version must be a non-empty string")
    if limits_hash is not None and not isinstance(limits_hash, (str, dict)):
        raise ValueError("limits_hash must be a string, dict or None")
    _cost(cost)
    parents = list(parent_ids or ())
    for parent in parents:
        validate_event_id(parent)
    if decision_event_id not in parents:
        parents.insert(0, decision_event_id)
    journal = _journal()
    events = journal.read(run_dir)
    if events is None:
        raise ValueError("no journal for run %s" % run_dir)
    if not any(ev.get("type") == DECISION and ev.get("event_id") == decision_event_id for ev in events):
        raise ValueError("decision event %s not found in journal" % decision_event_id)
    event_id = uuid.uuid4().hex
    record = {
        "schema": SCHEMA,
        "type": INTENT,
        "event_id": event_id,
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run_id": os.path.basename(os.path.normpath(run_dir)),
        "parent_ids": parents,
        "decision_event_id": decision_event_id,
        "idempotency_key": idempotency_key,
        "action_handle": action_handle,
        "policy_version": policy_version,
        "limits_hash": limits_hash,
        "cost": cost,
    }
    if session_id is not None:
        record["session_id"] = session_id
    if unit_id is not None:
        record["unit_id"] = unit_id
    validate_event(record)
    summary = {"kind": action_handle, "chosen": idempotency_key}
    _check_line_size(run_dir, INTENT,
                     {"event_ref": "learning/events/%s.json" % event_id,
                      "digest": "sha256:" + "0" * 64,
                      "summary": summary},
                     parent_ids=parents, unit_id=unit_id, session_id=session_id)
    digest = write_event(run_dir, event_id, record)
    if digest is None:
        sys.stderr.write("dream_record: could not write intent event %s; not admitted\n" % event_id)
        return None
    if _append_reference(run_dir, event_id, INTENT, digest, summary,
                         parent_ids=parents, unit_id=unit_id, session_id=session_id) is None:
        sys.stderr.write("dream_record: could not append intent reference %s; not admitted\n" % event_id)
        return None
    if intent_is_durable(run_dir, event_id):
        return event_id
    sys.stderr.write("dream_record: intent %s did not read back durable; not admitted\n" % event_id)
    return None


def intent_is_durable(run_dir, intent_event_id):
    try:
        validate_event_id(intent_event_id)
    except ValueError:
        return False
    journal = _journal()
    events = journal.read(run_dir)
    if events is None:
        return False
    intent_event = None
    decision_ids = set()
    for ev in events:
        if ev.get("type") == INTENT and ev.get("event_id") == intent_event_id:
            intent_event = ev
        if ev.get("type") == DECISION:
            decision_ids.add(ev.get("event_id"))
    if intent_event is None:
        return False
    payload = intent_event.get("payload") or {}
    event_ref = payload.get("event_ref")
    digest = payload.get("digest")
    if not isinstance(event_ref, str) or not isinstance(digest, str):
        return False
    if not digest.startswith("sha256:") or len(digest) != 71:
        return False
    body = read_event_bytes(run_dir, event_ref)
    if body is None:
        return False
    if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        return False
    try:
        record = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(record, dict):
        return False
    try:
        validate_event(record)
    except ValueError:
        return False
    if record.get("event_id") != intent_event_id or record.get("type") != INTENT:
        return False
    decision_event_id = record.get("decision_event_id")
    try:
        validate_event_id(decision_event_id)
    except ValueError:
        return False
    return decision_event_id in decision_ids


def admit_learned_action(run_dir, intent_event_id):
    return intent_is_durable(run_dir, intent_event_id)


# ---------------------------------------------------------------------------
# Gate ledger and capability call primitives (unit D4, sub units D4.a/D4.b).
# Additive on purpose: every name above keeps its behaviour, and the write
# path is the existing _write_artifact plus the one journal, unchanged. The
# gate surface sits beside the call surface because the checker needs both in
# one module; dream_gate.py was not shown and is not guessed here.
# ---------------------------------------------------------------------------

GATE = "dream.gate"
CALL = "dream.call"
CALL_VIA = ("compose", "direct", "seam")
CALL_STATUSES = ("PASS", "FAIL")
CALL_SCHEMA = "brother-dream-call-v1"
# A journal line stays under journal.MAX_LINE_BYTES: a very long identity
# field would push the payload over the bound and the row would be truncated,
# losing the artifact reference, so it is refused before any byte is written.
MAX_CALL_FIELD_CHARS = 128


def _gate_code(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("exit_code must be an int, got %s" % type(value).__name__)
    if value < 0 or value > 255:
        raise ValueError("exit_code must be within 0..255, got %r" % (value,))
    return value


def _gate_duration(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("duration_ms must be an int, got %s" % type(value).__name__)
    if value < 0:
        raise ValueError("duration_ms must be >= 0, got %r" % (value,))
    return value


def gate_verdict(exit_code):
    """PASS for 0, NO-DATA for 2, FAIL for any other code in 0..255."""
    code = _gate_code(exit_code)
    if code == 0:
        return "PASS"
    if code == 2:
        return "NO-DATA"
    return "FAIL"


def format_gate_line(check_name, exit_code, duration_ms):
    """One newline terminated TSV gate line. No verdict is stored: a reader
    rederives it from the exit code."""
    if not isinstance(check_name, str) or not check_name:
        raise ValueError("check_name must be a non-empty string")
    if "\t" in check_name or "\n" in check_name or "\r" in check_name:
        raise ValueError("check_name must not carry TAB or a newline")
    code = _gate_code(exit_code)
    duration = _gate_duration(duration_ms)
    return "%s\t%d\t%d\n" % (check_name, code, duration)


def _gate_field(text, label, maximum):
    if not text or any(ch not in "0123456789" for ch in text):
        raise ValueError("%s must be a decimal integer" % label)
    value = int(text)
    if value < 0 or value > maximum:
        raise ValueError("%s must be within 0..%d" % (label, maximum))
    return value


def parse_gate_line(line):
    """The dict one gate line carries, with the verdict REDERIVED from the
    exit code so a stored or edited verdict is never read as truth."""
    if not isinstance(line, str):
        raise ValueError("line must be a string")
    if not line.endswith("\n"):
        raise ValueError("a gate line must end with a newline")
    parts = line[:-1].split("\t")
    if len(parts) != 3:
        raise ValueError("a gate line has exactly three TAB separated fields")
    check_name, code_text, duration_text = parts
    if not check_name:
        raise ValueError("check_name must be a non-empty string")
    if "\n" in check_name or "\r" in check_name:
        raise ValueError("check_name must not carry a newline")
    exit_code = _gate_field(code_text, "exit_code", 255)
    duration_ms = _gate_field(duration_text, "duration_ms", 2 ** 63 - 1)
    return {"check_name": check_name, "exit_code": exit_code,
            "duration_ms": duration_ms, "verdict": gate_verdict(exit_code)}


def read_gates(path):
    """Gate rows in file order, or None when the file is absent (NO-DATA,
    never an empty list). An existing empty file is a measured []. Any
    corrupt, torn or non UTF-8 line blocks with ValueError."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    if not os.path.exists(path):
        return None
    if not os.path.isfile(path):
        raise ValueError("gate ledger is not a regular file: %s" % path)
    try:
        with open(path, "rb") as handle:
            body = handle.read()
    except OSError as exc:
        raise ValueError("gate ledger could not be read: %s" % exc)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("gate ledger is not valid UTF-8: %s" % path)
    if not text:
        return []
    if not text.endswith("\n"):
        raise ValueError("gate ledger must end with a newline: %s" % path)
    return [parse_gate_line(part + "\n") for part in text.split("\n")[:-1]]


def _short_field(label, value, required):
    if value is None:
        if required:
            raise ValueError("%s must be a non-empty string" % label)
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("%s must be a non-empty string" % label)
    if len(value) > MAX_CALL_FIELD_CHARS:
        raise ValueError("%s is longer than %d characters" % (label, MAX_CALL_FIELD_CHARS))
    return value


def _call_record(via, status, duration_ms, domain, name, seam_id, error_type,
                 run_id, unit_id, session_id):
    if not isinstance(via, str) or via not in CALL_VIA:
        raise ValueError("via must be one of %s, got %r" % (CALL_VIA, via))
    if not isinstance(status, str) or status not in CALL_STATUSES:
        raise ValueError("status must be one of %s, got %r" % (CALL_STATUSES, status))
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms < 0:
        raise ValueError("duration_ms must be an int >= 0, got %r" % (duration_ms,))
    has_capability = domain is not None or name is not None
    if has_capability:
        _short_field("domain", domain, True)
        _short_field("name", name, True)
    if seam_id is not None:
        _short_field("seam_id", seam_id, True)
    if error_type is not None:
        _short_field("error_type", error_type, True)
        if not all(ch.isalnum() or ch == "_" for ch in error_type):
            raise ValueError("error_type must be an exception __name__, got %r" % (error_type,))
    _short_field("run_id", run_id, False)
    _short_field("unit_id", unit_id, False)
    _short_field("session_id", session_id, False)
    if via == "seam":
        if seam_id is None:
            raise ValueError("via seam requires a seam_id")
        if has_capability:
            raise ValueError("a seam call carries seam_id only, never domain and name")
        if run_id is not None:
            raise ValueError("run_id is null for a seam call")
    else:
        if not has_capability:
            raise ValueError("via %s requires both domain and name" % via)
        if seam_id is not None:
            raise ValueError("seam_id is null unless via is seam")
        if via == "compose":
            if run_id is None:
                raise ValueError("via compose requires the run id it belongs to")
        elif run_id is not None:
            raise ValueError("run_id is null for a direct call")
    return {"schema": CALL_SCHEMA, "domain": domain, "name": name,
            "seam_id": seam_id, "via": via, "status": status,
            "duration_ms": duration_ms, "error_type": error_type,
            "run_id": run_id}


def record_call(run_dir, via, status, duration_ms, domain=None, name=None,
                seam_id=None, error_type=None, run_id=None, unit_id=None,
                session_id=None):
    """One capability call: the full record in the content addressed artifact
    under <run_dir>/dream/, the reference plus the short denormalized keys on
    one journal line. Every field is validated before a byte is written; a
    write that fails says so once on stderr and returns None, never raising
    into the run being recorded."""
    if not isinstance(run_dir, str) or not run_dir:
        raise ValueError("run_dir must be a non-empty string")
    record = _call_record(via, status, duration_ms, domain, name, seam_id,
                          error_type, run_id, unit_id, session_id)
    try:
        artifact = _write_artifact(run_dir, record)
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write("dream_record: could not write the %s call artifact (%s); "
                         "the run continues without it\n" % (via, exc))
        return None
    payload = {"schema": CALL_SCHEMA, "artifact": artifact, "domain": domain,
               "name": name, "via": via, "status": status}
    try:
        return _journal().append(run_dir, CALL, unit_id=unit_id,
                                 session_id=session_id, payload=payload)
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        sys.stderr.write("dream_record: could not append the %s event (%s); "
                         "the run continues without it\n" % (CALL, exc))
        return None


def read_calls(run_dir):
    """Call rows in journal order joined to the record their artifact holds,
    or None when the run has no journal. A row whose artifact is missing,
    corrupt or edited keeps record None and never counts as covered."""
    if not isinstance(run_dir, str) or not run_dir:
        return None
    events = _journal().read(run_dir)
    if events is None:
        return None
    rows = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != CALL:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        artifact = payload.get("artifact")
        rows.append({
            "event_id": event.get("event_id"),
            "at": event.get("at"),
            "unit_id": event.get("unit_id"),
            "session_id": event.get("session_id"),
            "schema": payload.get("schema"),
            "artifact": artifact,
            "domain": payload.get("domain"),
            "name": payload.get("name"),
            "via": payload.get("via"),
            "status": payload.get("status"),
            "record": _read_artifact(run_dir, artifact),
        })
    return rows


def _required_pairs(value, label):
    if not isinstance(value, (list, tuple)):
        raise ValueError("%s must be a list or tuple of (domain, name) pairs" % label)
    pairs = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("%s must hold (domain, name) pairs" % label)
        domain, name = item
        if not _is_nonempty_str(domain) or not _is_nonempty_str(name):
            raise ValueError("%s must hold two non-empty strings per pair" % label)
        pair = (domain, name)
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def _required_seams(value, label):
    if not isinstance(value, (list, tuple)):
        raise ValueError("%s must be a list or tuple of seam ids" % label)
    seams = []
    for item in value:
        if not _is_nonempty_str(item):
            raise ValueError("%s must hold non-empty strings" % label)
        if item not in seams:
            seams.append(item)
    return seams


def _coverage_state(count):
    return "covered" if count > 0 else "not-covered"


def coverage(run_dir, required_capabilities=(), required_seams=()):
    """Coverage of a handed required list by the recorded call rows alone.
    There is no receipt, headline, verdict or log argument here: a missing
    journal is no-data with every state unknown and every count null."""
    capabilities = _required_pairs(required_capabilities, "required_capabilities")
    seams = _required_seams(required_seams, "required_seams")
    calls = read_calls(run_dir)
    if calls is None:
        return {
            "data": "no-data",
            "capabilities": [{"domain": d, "name": n, "state": "unknown", "calls": None}
                             for d, n in sorted(capabilities)],
            "seams": [{"seam": s, "state": "unknown", "calls": None}
                      for s in sorted(seams)],
        }
    capability_counts = {}
    seam_counts = {}
    for row in calls:
        record = row.get("record")
        if not isinstance(record, dict):
            continue
        if record.get("status") not in CALL_STATUSES:
            continue
        if record.get("via") == "seam":
            seam_id = record.get("seam_id")
            if _is_nonempty_str(seam_id):
                seam_counts[seam_id] = seam_counts.get(seam_id, 0) + 1
            continue
        domain = record.get("domain")
        name = record.get("name")
        if _is_nonempty_str(domain) and _is_nonempty_str(name):
            key = (domain, name)
            capability_counts[key] = capability_counts.get(key, 0) + 1
    return {
        "data": "ok",
        "capabilities": [{"domain": d, "name": n,
                          "state": _coverage_state(capability_counts.get((d, n), 0)),
                          "calls": capability_counts.get((d, n), 0)}
                         for d, n in sorted(capabilities)],
        "seams": [{"seam": s, "state": _coverage_state(seam_counts.get(s, 0)),
                   "calls": seam_counts.get(s, 0)}
                  for s in sorted(seams)],
    }


def verify_d0_run(run_dir: str) -> bool:
    """True only when this run's journal exists, every recorded decision's
    artifact is intact, and every recorded intent reads back durable.

    Fail closed: an empty or non-string run_dir, no journal at all, a corrupt
    or missing artifact, a fake or torn intent, and a journal that cannot be
    loaded all return False. This refuses stale output, it never repairs it.
    """
    if not isinstance(run_dir, str) or not run_dir.strip():
        return False
    try:
        rows = read_decisions(run_dir)
    except (RuntimeError, OSError, ValueError):
        return False
    if rows is None:
        return False
    for row in rows:
        if not isinstance(row, dict) or row.get("record") is None:
            return False
    try:
        events = _journal().read(run_dir)
    except (RuntimeError, OSError, ValueError):
        return False
    if events is None:
        return False
    for event in events:
        if not isinstance(event, dict):
            return False
        if event.get("type") != INTENT:
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not intent_is_durable(run_dir, event_id):
            return False
    return True


def list_d0_rows(run_dir: str) -> List[str]:
    """One line per recorded decision in journal order, then the count of
    outcome events whose parent is not a recorded decision.

    NO-DATA reads as an empty list, never as a clean run: a non-string or
    empty run_dir, a missing journal and a journal that cannot be loaded all
    return []. A corrupt artifact shows as record=NONE in its own row instead
    of hiding the row, and never becomes a graded PASS.
    """
    if not isinstance(run_dir, str) or not run_dir.strip():
        return []
    try:
        rows = read_decisions(run_dir)
    except (RuntimeError, OSError, ValueError):
        return []
    if rows is None:
        return []
    lines = []
    for row in rows:
        record = row.get("record") if isinstance(row, dict) else None
        chosen = record.get("chosen") if isinstance(record, dict) else None
        kind = row.get("kind") if isinstance(row, dict) else None
        if kind is None and isinstance(record, dict):
            kind = record.get("kind")
        outcomes = row.get("outcomes") if isinstance(row, dict) else None
        lines.append("event_id=%s state=%s kind=%s chosen=%s outcomes=%d record=%s"
                     % (row.get("event_id") if isinstance(row, dict) else None,
                        row.get("state") if isinstance(row, dict) else None,
                        kind, chosen, len(outcomes or ()),
                        "present" if record is not None else "NONE"))
    decision_ids = set()
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("event_id"), str):
            decision_ids.add(row["event_id"])
    observed = 0
    try:
        events = _journal().read(run_dir)
    except (RuntimeError, OSError, ValueError):
        events = None
    for event in events or ():
        if not isinstance(event, dict) or event.get("type") != OUTCOME:
            continue
        parents = event.get("parent_ids") or ()
        if isinstance(parents, (str, bytes)):
            parents = (parents,)
        if not any(parent in decision_ids for parent in parents):
            observed += 1
    lines.append("orphan_outcomes=%d" % observed)
    return lines
