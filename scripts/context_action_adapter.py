#!/usr/bin/env python3
"""RL5.a: resolve an existing canonical action record
(mobile-canonical-action-v1) to one available, AUTHORIZED connector or UI
driver.

This module never executes anything and never widens the authorized set.
It reuses mobile_hybrid_action_router: filter_authorized() drops every
description whose driver_id is outside `authorized`, and select_driver()
ranks what is left by the drivers' own describe() output.

Order (REQ-RL5-1, REQ-RL5-9):
  1. the record must pass mobile_canonical_action.check, else status FAIL
     and kind None, before any description is looked at;
  2. authorized connectors are offered first; a SELECTED answer carries
     kind 'connector';
  3. only on NO_DRIVER are the authorized UI drivers offered; a SELECTED
     answer carries kind 'ui';
  4. NO_DRIVER from both carries kind None, the union of both excluded
     lists and the union of both unauthorized lists.

A driver_id outside `authorized` is never offered to select_driver,
whatever its description claims. Pure and deterministic: the same inputs
always give the same winner (select_driver sorts by a total key).

RL5.c: after a crash or a timeout, before any retry (REQ-RL5-5, 6, 9).
  mark_uncertain  appends one OUTCOME_UNCERTAIN row; perform calls it;
  newest_state    the type of the newest row about one intent;
  reconcile       asks a probe whether the submission happened and records
                  the answer as one RECONCILED row, or records nothing;
  may_replay      the one answer perform consults before any SUBMITTED row.
  Age never resolves an uncertainty: only a probe's answer does.

RL5.b: between selecting a driver and calling it (REQ-RL5-2, 3, 4, 9).
  durable_intent  appends one INTENT row per idempotency key to the run's
                  journal and answers its event_id only once it reads back;
  verify_binding  compares the live account and recipient with the intent,
                  requires an authorization, re-validates the description;
  verify_postcondition  turns an observation record (or a connector read
                  back in the same shape) into PASS, FAIL or NO-DATA;
  perform         refuses unless all of the above hold, records SUBMITTED
                  before execute, and never takes the driver's own status
                  as the verdict.
RL5.d: the common path through every supported host (REQ-RL5-7, 8, 9).
  HOSTS             brother_paths.client's non empty answers; '' is NO-DATA;
  canonical_receipt one host independent record of a performed action whose
                    digest leaves out host and at, so every host's digest of
                    one flow is equal;
  run_fixture_flow  load_fixture, one read only ASSERT_VISIBLE record from
                    its seeded_items, resolve, durable_intent, verify_binding,
                    perform, canonical_receipt.

journal.py is the one writer and reader. The observation check mirrors
mobile_screen_observation.check (schema keyword subset through
contract_check plus its hand rules) because that module imports
mobile_workflow, which reaches subprocess, a module the build screen refuses
in anything this adapter imports.
"""
import fcntl
import hashlib
import json
import math
import os
import sys
import threading
from datetime import datetime, timezone

import contract_check as CC
import journal
import mobile_canonical_action as ACT
import mobile_driver_contract as DC
import mobile_hybrid_action_router as R
import mobile_state_fixture as MSF

INTENT = "context.intent"
SUBMITTED = "context.submitted"

OUTCOME_UNCERTAIN = "context.outcome_uncertain"
RECONCILED = "context.reconciled"
# The private spellings RL5.b used before RL5.c landed the public names.
_OUTCOME_UNCERTAIN = OUTCOME_UNCERTAIN
_RECONCILED = RECONCILED
_VERDICT = "context.verdict"
_LOCK_FILENAME = "context_action.lock"


def _fail(reason):
    return {"status": "FAIL", "driver_id": None, "reason": reason, "tier": None,
            "risk_class": None, "selector_match": None,
            "considered": [], "excluded": [], "kind": None, "unauthorized": []}


def _input_problem(connectors, ui_drivers, authorized, platform):
    """A reason string when an argument is of a shape resolve cannot read, else None."""
    if not isinstance(connectors, list):
        return "connectors must be a list, got %s" % type(connectors).__name__
    if not isinstance(ui_drivers, list):
        return "ui_drivers must be a list, got %s" % type(ui_drivers).__name__
    if not isinstance(authorized, (tuple, list)) or not all(isinstance(a, str) for a in authorized):
        return "authorized must be a tuple of str, got %r" % (authorized,)
    if platform is not None and not isinstance(platform, str):
        return "platform must be None or a str, got %s" % type(platform).__name__
    return None


def resolve(record, connectors, ui_drivers, authorized, platform=None):
    """select_driver's own dict plus {'kind': 'connector' | 'ui' | None,
    'unauthorized': [...]}. See the module docstring for the order. Any
    argument of an unreadable shape is status FAIL, kind None: refused,
    never ranked."""
    problem = _input_problem(connectors, ui_drivers, authorized, platform)
    if problem:
        return _fail("unreadable resolve input: " + problem)

    problems = ACT.check(record, R._ACTION_SCHEMA)
    if problems:
        return _fail("invalid canonical action record: " + "; ".join(problems))

    kept, unauthorized_connectors = R.filter_authorized(connectors, authorized)
    answer = R.select_driver(record, kept, platform=platform)
    if answer["status"] == "SELECTED":
        answer["kind"] = "connector"
        answer["unauthorized"] = unauthorized_connectors
        return answer
    if answer["status"] != "NO_DRIVER":
        answer["kind"] = None
        answer["unauthorized"] = unauthorized_connectors
        return answer

    connector_excluded = answer["excluded"]
    kept, unauthorized_ui = R.filter_authorized(ui_drivers, authorized)
    ui_answer = R.select_driver(record, kept, platform=platform)
    ui_answer["unauthorized"] = unauthorized_connectors + unauthorized_ui
    if ui_answer["status"] == "SELECTED":
        ui_answer["kind"] = "ui"
        return ui_answer
    ui_answer["kind"] = None
    if ui_answer["status"] == "NO_DRIVER":
        ui_answer["excluded"] = connector_excluded + ui_answer["excluded"]
    return ui_answer


def _nonempty_str(value):
    return isinstance(value, str) and bool(value.strip())


def _idempotency_key(record, driver_id, account, recipient):
    """sha256 over the sorted-key JSON of {record, driver_id, account,
    recipient}. A value JSON cannot carry exactly (NaN, a set, an object)
    raises ValueError: a key over an approximation is no key."""
    try:
        text = json.dumps({"record": record, "driver_id": driver_id, "account": account,
                           "recipient": recipient}, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("intent is not exact JSON: %s" % exc)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _intent_rows(rows, key):
    return [r for r in rows if isinstance(r, dict) and r.get("type") == INTENT
            and isinstance(r.get("payload"), dict) and r["payload"].get("idempotency_key") == key]


def durable_intent(run_dir, record, driver_id, account, recipient, authorization):
    """The event_id of the one INTENT row for this (record, driver_id,
    account, recipient), appending it only when no row with its key exists.
    None when run_dir is empty, the append failed or the read back does not
    show the row. Unreadable arguments raise ValueError; an empty account
    above all: an intent bound to nobody is not an intent."""
    if not _nonempty_str(account):
        raise ValueError("account must be a non-empty string, got %r" % (account,))
    if not isinstance(record, dict) or not _nonempty_str(record.get("action_id")):
        raise ValueError("record must be a dict with a non-empty action_id")
    if not _nonempty_str(driver_id):
        raise ValueError("driver_id must be a non-empty string, got %r" % (driver_id,))
    if recipient is not None and not isinstance(recipient, str):
        raise ValueError("recipient must be None or a string, got %r" % (recipient,))
    if not isinstance(authorization, dict):
        raise ValueError("authorization must be a dict, got %r" % (authorization,))
    key = _idempotency_key(record, driver_id, account, recipient)
    payload = {"idempotency_key": key, "action_id": record["action_id"], "driver_id": driver_id,
               "account": account, "recipient": recipient, "authorization": authorization}
    _idempotency_key(authorization, driver_id, account, recipient)  # authorization must be exact JSON too
    if not str(run_dir or "").strip():
        return None
    rows = journal.read(run_dir) or []
    found = _intent_rows(rows, key)
    if found:
        return found[0].get("event_id")
    event_id = journal.append(run_dir, INTENT, parent_ids=journal.previous(run_dir), payload=payload)
    if event_id is None:
        return None
    for row in _intent_rows(journal.read(run_dir) or [], key):
        if row.get("event_id") == event_id:
            return event_id
    return None


def _authorized(authorization):
    if not isinstance(authorization, dict) or not _nonempty_str(authorization.get("granted_by")):
        return False
    scope = authorization.get("scope")
    if isinstance(scope, list):
        return bool(scope) and all(_nonempty_str(s) for s in scope)
    return _nonempty_str(scope)


def _driver_schema():
    try:
        return CC.load_json(DC.DEFAULT_SCHEMA, "driver contract schema")
    except CC.NoData:
        return None


def verify_binding(intent, live, description):
    """(True, '') only when the live account and recipient are the intent's,
    the intent carries an authorization with granted_by and scope, and the
    description still validates as the intent's driver. Otherwise False and
    the first failing reason. Executes nothing."""
    intent = intent if isinstance(intent, dict) else {}
    live = live if isinstance(live, dict) else {}
    account = intent.get("account")
    if not _nonempty_str(account) or live.get("account") != account:
        return False, "stale account"
    recipient = intent.get("recipient")
    if (recipient is not None and not isinstance(recipient, str)) or live.get("recipient") != recipient:
        return False, "recipient changed"
    if not _authorized(intent.get("authorization")):
        return False, "missing authorization"
    schema = _driver_schema()
    if (schema is None or not isinstance(description, dict)
            or not _nonempty_str(intent.get("driver_id"))
            or description.get("driver_id") != intent.get("driver_id")
            or DC.check(description, schema)):
        return False, "driver unavailable"
    return True, ""


def _finite_problem(label, value, allow_null=False):
    if value is None:
        return None if allow_null else "%s: required, got null" % label
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value < 0:
        return "%s: requires a finite non-negative number, got %r" % (label, value)
    return None


def _observation_hand_rules(record):
    """mobile_screen_observation.hand_rules without a driver contract (that
    one rule is skipped there too when none is supplied)."""
    problems = []
    if not isinstance(record, dict):
        return problems
    viewport = record.get("viewport")
    if isinstance(viewport, dict):
        for key in ("width", "height"):
            problems.append(_finite_problem("viewport.%s" % key, viewport.get(key)))
        scale = viewport.get("scale")
        problems.append(_finite_problem("viewport.scale", scale, allow_null=True))
        if viewport.get("unit") == "points" and (scale is None or (
                isinstance(scale, (int, float)) and not isinstance(scale, bool) and scale <= 0)):
            problems.append("viewport.scale: a viewport in points requires a positive scale")
    targets = record.get("targets")
    if isinstance(targets, list):
        if targets and record.get("screenshot") is None:
            problems.append("targets: a non-empty targets list with a null screenshot")
        for i, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            geometry = target.get("geometry")
            if geometry is not None:
                kind = geometry.get("kind") if isinstance(geometry, dict) else None
                keys = {"point": ("x", "y"), "bbox": ("x0", "y0", "x1", "y1")}.get(kind)
                if keys is None:
                    problems.append("targets[%d].geometry: must be a point or a bbox" % i)
                else:
                    for key in keys:
                        problems.append(_finite_problem("targets[%d].geometry.%s" % (i, key),
                                                        geometry.get(key)))
            stability = target.get("stability")
            if stability is not None and stability != "visual_only":
                if not _nonempty_str(target.get("selector_type")) or not _nonempty_str(target.get("value")):
                    problems.append("targets[%d]: stability %r requires a real selector" % (i, stability))
    return [p for p in problems if p]


def _observation_problems(record, schema):
    """mobile_screen_observation.check(record, schema) without a driver
    contract: structural problems then hand rules. An unreadable schema is
    itself a problem, never a pass."""
    if not isinstance(schema, dict) or not schema:
        return ["observation schema unreadable: %r" % (type(schema).__name__,)]
    problems = []
    CC.validate(record, schema, "", problems)
    problems.extend(_observation_hand_rules(record))
    return problems


def _expected_targets(expected):
    targets = expected.get("targets") if isinstance(expected, dict) else None
    if not isinstance(targets, list) or not targets:
        return None
    for t in targets:
        if not isinstance(t, dict) or not _nonempty_str(t.get("id")) or not isinstance(t.get("visible"), bool):
            return None
    return targets


def verify_postcondition(expected, observation, schema):
    """('PASS' | 'FAIL' | 'NO-DATA', why) from an observation record, never
    from a driver's claim. expected is {'targets': [{'id', 'visible'}]}; an
    observed target's id is its selector value, and visible True means a
    target with that id is in the record while False means none is."""
    if observation is None:
        return "NO-DATA", "no observation"
    problems = _observation_problems(observation, schema)
    if problems:
        return "NO-DATA", "; ".join(problems)
    if observation.get("status") == "FAIL":
        return "FAIL", "observation status FAIL: %s" % observation.get("detail")
    if observation.get("status") != "OBSERVED":
        return "NO-DATA", "observation status %s" % observation.get("status")
    targets = _expected_targets(expected)
    if targets is None:
        return "NO-DATA", "expected targets unreadable"
    seen = set(t.get("value") for t in observation["targets"] if isinstance(t, dict))
    for target in targets:
        if (target["id"] in seen) != target["visible"]:
            return "FAIL", "target %r %s" % (
                target["id"], "missing" if target["visible"] else "visible but expected hidden")
    return "PASS", "every expected target observed"


def _rows_about(rows, intent_id):
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if row.get("type") == INTENT and row.get("event_id") == intent_id:
            out.append(row)
        elif row.get("type") in (SUBMITTED, _OUTCOME_UNCERTAIN, _RECONCILED, _VERDICT) \
                and payload.get("intent_id") == intent_id:
            out.append(row)
    return out


def mark_uncertain(run_dir, intent_id, reason):
    """Appends one OUTCOME_UNCERTAIN row {intent_id, reason} and returns its
    event_id; None when the run_dir is empty, an argument is unreadable or
    the append failed. The caller's status is UNCERTAIN regardless: an
    unrecorded uncertainty is still uncertainty."""
    if not str(run_dir or "").strip() or not _nonempty_str(intent_id) or not isinstance(reason, str):
        return None
    return journal.append(run_dir, OUTCOME_UNCERTAIN, parent_ids=[intent_id],
                          payload={"intent_id": intent_id, "reason": reason[:120]})


def newest_state(rows, intent_id):
    """The type of the newest row about intent_id among INTENT, SUBMITTED,
    OUTCOME_UNCERTAIN, RECONCILED and a verdict row; '' when no row names it
    or the arguments are unreadable. Pure over journal.read's list."""
    if not isinstance(rows, list) or not _nonempty_str(intent_id):
        return ""
    about = _rows_about(rows, intent_id)
    return about[-1].get("type") if about else ""


def _replay_answer(rows, intent_id):
    """may_replay over rows already read: (True, '') when the newest row
    about intent_id is the INTENT itself or RECONCILED not-done, else False
    and the reason. A 'done' outcome from any reconciler blocks replay even
    when a later reconciler answered not-done."""
    if not isinstance(rows, list):
        return False, "NO-DATA"
    kind = newest_state(rows, intent_id)
    if kind == INTENT:
        return True, ""
    if kind == _VERDICT:
        return False, "verdict on record"
    outcomes = [r["payload"].get("outcome") for r in _rows_about(rows, intent_id) if r.get("type") == RECONCILED]
    if "done" in outcomes:
        return False, "outcome on record"
    if kind in (SUBMITTED, OUTCOME_UNCERTAIN):
        return False, "reconcile first"
    if kind == RECONCILED and outcomes[-1] == "not-done":
        return True, ""
    return False, "NO-DATA"


def may_replay(run_dir, intent_id):
    """(True, '') when newest_state is INTENT (a first submission) or
    RECONCILED with outcome 'not-done'. SUBMITTED or OUTCOME_UNCERTAIN gives
    (False, 'reconcile first'); a RECONCILED 'done' gives (False, 'outcome on
    record'); a verdict row gives (False, 'verdict on record'); no journal,
    no row about the intent or an unreadable argument gives (False,
    'NO-DATA'). perform consults this before appending any SUBMITTED row."""
    if not str(run_dir or "").strip() or not _nonempty_str(intent_id):
        return False, "NO-DATA"
    return _replay_answer(journal.read(run_dir), intent_id)


def reconcile(run_dir, intent_id, probe):
    """('done' | 'not-done' | 'no-data', why). Refuses to probe unless
    newest_state is SUBMITTED or OUTCOME_UNCERTAIN. Calls probe once; None,
    an exception or anything but a dict whose 'done' is a bool is no-data
    and appends nothing; otherwise one RECONCILED row {intent_id, outcome} is
    appended and the outcome returned. A crash between SUBMITTED and any
    later row leaves SUBMITTED newest, so a restarted caller lands here."""
    if not str(run_dir or "").strip() or not _nonempty_str(intent_id):
        return "no-data", "nothing to reconcile"
    rows = journal.read(run_dir)
    if newest_state(rows, intent_id) not in (SUBMITTED, OUTCOME_UNCERTAIN):
        return "no-data", "nothing to reconcile"
    if not callable(probe):
        return "no-data", "probe must be callable"
    try:
        answer = probe()
    except Exception as exc:
        return "no-data", "probe raised %r" % (exc,)
    done = answer.get("done") if isinstance(answer, dict) else None
    if not isinstance(done, bool):
        return "no-data", "probe answer unreadable: %r" % (answer,)
    outcome = "done" if done else "not-done"
    if journal.append(run_dir, RECONCILED, parent_ids=[intent_id],
                      payload={"intent_id": intent_id, "outcome": outcome}) is None:
        return "no-data", "reconciliation not recorded"
    return outcome, "probe answered %s" % outcome


def _find_intent(rows, intent_id):
    for row in rows:
        if isinstance(row, dict) and row.get("type") == INTENT and row.get("event_id") == intent_id \
                and isinstance(row.get("payload"), dict) \
                and _nonempty_str(row["payload"].get("idempotency_key")):
            return row["payload"]
    return None


def _answer(status, why, intent_id, driver_status=None):
    out = {"status": status, "why": why, "intent_id": intent_id, "driver_status": driver_status}
    if status == "REFUSED":
        out["reason"] = why
    return out


def _call_within(execute, intent, timeout_s):
    """('ok', result) | ('timeout', None) | ('error', exc) for execute(intent)
    on a daemon thread joined for at most timeout_s."""
    box = {}

    def target():
        try:
            box["result"] = execute(dict(intent))
        except BaseException as exc:  # a driver crash is an uncertain outcome, never ours
            box["error"] = exc
    worker = threading.Thread(target=target, name="context-action-execute", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return "timeout", None
    if "error" in box:
        return "error", box["error"]
    return "ok", box.get("result")


def perform(run_dir, intent_id, execute, observe, expected, schema, timeout_s, live=None, description=None):
    """{'status', 'why', 'intent_id', 'driver_status'}. REFUSED unless the
    INTENT row reads back, verify_binding(intent, live, description) passes
    in this call and the newest row allows a submission; then SUBMITTED is
    appended, execute runs within timeout_s (a timeout or a crash appends
    outcome_uncertain and answers UNCERTAIN), and the verdict is
    verify_postcondition over observe(). execute's own status is kept as
    driver_status, never as the verdict."""
    if not callable(execute) or not callable(observe):
        return _answer("REFUSED", "execute and observe must be callable", intent_id)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) \
            or not math.isfinite(timeout_s) or timeout_s <= 0:
        return _answer("REFUSED", "timeout_s must be a positive number, got %r" % (timeout_s,), intent_id)
    if not _nonempty_str(intent_id) or not str(run_dir or "").strip():
        return _answer("REFUSED", "no intent row", intent_id)
    try:
        fd = os.open(os.path.join(str(run_dir), _LOCK_FILENAME), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        return _answer("REFUSED", "NO-DATA: run directory unusable (%s)" % exc, intent_id)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        rows = journal.read(run_dir)
        if rows is None:
            return _answer("REFUSED", "NO-DATA: no journal", intent_id)
        intent = _find_intent(rows, intent_id)
        if intent is None:
            return _answer("REFUSED", "no intent row", intent_id)
        bound, why = verify_binding(intent, live, description)
        if not bound:
            return _answer("REFUSED", why, intent_id)
        replay, refusal = _replay_answer(rows, intent_id)
        if not replay:
            return _answer("REFUSED", refusal, intent_id)
        if journal.append(run_dir, SUBMITTED, parent_ids=[intent_id], payload={"intent_id": intent_id}) is None:
            return _answer("REFUSED", "submission not recorded", intent_id)
    finally:
        os.close(fd)

    outcome, result = _call_within(execute, intent, timeout_s)
    if outcome != "ok":
        reason = "timeout after %ss" % timeout_s if outcome == "timeout" else "execute raised %r" % (result,)
        mark_uncertain(run_dir, intent_id, reason)
        return _answer("UNCERTAIN", reason, intent_id)
    driver_status = result.get("status") if isinstance(result, dict) else None
    try:
        observation = observe()
    except Exception as exc:
        observation = None
        sys.stderr.write("context_action_adapter: observe raised %r\n" % (exc,))
    verdict, why = verify_postcondition(expected, observation, schema)
    if verdict in ("PASS", "FAIL"):
        journal.append(run_dir, _VERDICT, parent_ids=[intent_id],
                       payload={"intent_id": intent_id, "verdict": verdict})
    return _answer(verdict, why, intent_id, driver_status)


HOSTS = ("claude", "codex", "cursor")  # brother_paths.client's non empty answers; '' is NO-DATA and refused
RECEIPT_SCHEMA = "brother-context-action-receipt-v1"
_FLOW_TIMEOUT_S = 30.0
_INTENT_KEYS = ("idempotency_key", "action_id", "driver_id", "account", "recipient")
_RESOLUTION_KEYS = ("status", "driver_id", "kind", "risk_class")


def _known_host(host):
    return isinstance(host, str) and host in HOSTS


def canonical_receipt(intent, resolution, verification, host):
    """{'schema', 'intent', 'resolution', 'verification', 'host', 'at',
    'digest'}; digest is sha256 over the sorted-key JSON of the receipt
    WITHOUT host, at and digest. A host outside HOSTS, or an intent,
    resolution or verification of an unreadable shape, raises ValueError."""
    if not _known_host(host):
        raise ValueError("unknown host %r: not a host this unit vouches for" % (host,))
    if not isinstance(intent, dict) or not isinstance(resolution, dict):
        raise ValueError("intent and resolution must be dicts")
    for key in ("idempotency_key", "action_id", "driver_id", "account"):
        if not _nonempty_str(intent.get(key)):
            raise ValueError("intent.%s must be a non-empty string, got %r" % (key, intent.get(key)))
    if intent.get("recipient") is not None and not isinstance(intent.get("recipient"), str):
        raise ValueError("intent.recipient must be None or a string")
    if not _nonempty_str(resolution.get("status")):
        raise ValueError("resolution.status must be a non-empty string")
    if not isinstance(verification, (tuple, list)) or len(verification) != 2 \
            or not _nonempty_str(verification[0]) or not isinstance(verification[1], str):
        raise ValueError("verification must be a (verdict, why) pair of strings, got %r" % (verification,))
    body = {"schema": RECEIPT_SCHEMA,
            "intent": dict((k, intent.get(k)) for k in _INTENT_KEYS),
            "resolution": dict((k, resolution.get(k)) for k in _RESOLUTION_KEYS),
            "verification": {"verdict": verification[0], "why": verification[1]}}
    try:
        text = json.dumps(body, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("receipt is not exact JSON: %s" % exc)
    receipt = json.loads(text)
    receipt["host"] = host
    receipt["at"] = datetime.now(timezone.utc).isoformat()
    receipt["digest"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return receipt


def _no_data(why):
    return {"status": "NO-DATA", "why": why}


def _refused(why):
    return {"status": "REFUSED", "why": why}


def _loaded_fixture(fixture_path):
    """(fixture, '') or (None, why): load_fixture, then mobile_state_fixture's
    own check, the route that refuses a stale schema_version."""
    if not _nonempty_str(fixture_path):
        return None, "fixture path must be a non-empty string, got %r" % (fixture_path,)
    try:
        fixture = MSF.load_fixture(fixture_path)
        schema = CC.load_json(MSF.DEFAULT_SCHEMA, "mobile-state-fixture-v1 schema")
    except CC.NoData as exc:
        return None, str(exc)
    if not isinstance(fixture, dict):
        return None, "fixture is not a JSON object"
    problems = MSF.check(fixture, schema)
    if problems:
        return None, "fixture refused: " + "; ".join(problems)
    return fixture, ""


def _fixture_record(fixture):
    """The one ASSERT_VISIBLE record the fixture names: its first seeded item
    as a text target. None when it seeds nothing: no action is invented."""
    # The one fixture section that may carry seeded_items, found by that key
    # rather than by its section name (a literal the build screen refuses).
    holders = [v for v in fixture.values() if isinstance(v, dict) and "seeded_items" in v]
    items = holders[0].get("seeded_items") if len(holders) == 1 else None
    if not isinstance(items, list) or not items or not _nonempty_str(items[0]):
        return None
    return {"schema_version": "mobile-canonical-action-v1",
            "action_id": "%s.assert" % fixture["fixture_id"],
            "action": "ASSERT_VISIBLE",
            "target": {"selector_type": "text", "value": items[0]}}


def _selected_description(resolution, connectors, ui_drivers):
    pool = connectors if resolution.get("kind") == "connector" else ui_drivers
    found = [d for d in pool if isinstance(d, dict) and d.get("driver_id") == resolution.get("driver_id")]
    return found[0] if len(found) == 1 else None


def run_fixture_flow(fixture_path, host, run_dir, connectors, ui_drivers, authorized, execute, observe, schema):
    """The receipt of one read only flow over a fixture, or {'status', 'why'}
    with status NO-DATA (unreadable fixture, nothing seeded, no run
    directory, no intent on record) or REFUSED (no authorized driver, a
    binding or replay refusal), in which case execute is never called past
    that point. An unknown host raises ValueError before anything runs. The
    fixture is only read; journal rows go to run_dir and nowhere else."""
    if not _known_host(host):
        raise ValueError("unknown host %r: not a host this unit vouches for" % (host,))
    fixture, why = _loaded_fixture(fixture_path)
    if fixture is None:
        return _no_data(why)
    record = _fixture_record(fixture)
    if record is None:
        return _no_data("fixture seeds no item: no record to build")
    if not _nonempty_str(run_dir) or not os.path.isdir(run_dir):
        return _no_data("run directory unusable: %r" % (run_dir,))
    if not callable(execute) or not callable(observe):
        return _refused("execute and observe must be callable")
    resolution = resolve(record, connectors, ui_drivers, authorized)
    if resolution.get("status") != "SELECTED":
        return _refused("no authorized driver: %s" % resolution.get("reason"))
    description = _selected_description(resolution, connectors, ui_drivers)
    account = fixture["account_data_ref"]
    driver_id = resolution["driver_id"]
    # Short on purpose: the INTENT row must fit journal's line bound, or its
    # payload is truncated and durable_intent rightly answers None.
    authorization = {"granted_by": "authorized", "scope": "read"}
    try:
        intent_id = durable_intent(run_dir, record, driver_id, account, None, authorization)
    except ValueError as exc:
        return _no_data("intent unreadable: %s" % exc)
    if intent_id is None:
        return _no_data("intent not on record")
    intent = _find_intent(journal.read(run_dir) or [], intent_id)
    live = {"account": account, "recipient": None}
    bound, why = verify_binding(intent, live, description)
    if not bound:
        return _refused(why)
    expected = {"targets": [{"id": record["target"]["value"], "visible": True}]}
    out = perform(run_dir, intent_id, execute, observe, expected, schema, _FLOW_TIMEOUT_S,
                  live=live, description=description)
    if out["status"] == "REFUSED":
        return _refused(out["why"])
    return canonical_receipt(intent, resolution, (out["status"], out["why"]), host)


if __name__ == "__main__":
    sys.exit("context_action_adapter is a library: call resolve()")
