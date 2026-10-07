#!/usr/bin/env python3
"""dream_authorize: D7-C execution authority boundary.

Live recheck, mint, record intent, execute, in that order and nothing else.
Every denial returns allowed=False with handles=() and every named refusal, so
no partial handle set can leave this module. Handles are only ever built by
mint_handle, and every check runs against the live authority argument, never
a cache. Missing, corrupt or unknown authority input BLOCKS or is NO-DATA.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import threading
import uuid
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass

from plugin.runtime.brother.core import dream_policy as _dp


class AuthorizationError(ValueError):
    code: str

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ActionHandle:
    handle_id: str
    action: object
    effective_ceiling: object
    policy_hash: str
    context_hash: str
    issued_at: str
    expires_at: str
    nonce: str
    authority_signature: str


@dataclass(frozen=True)
class ExecutionDecision:
    execution_id: str
    policy_decision_hash: str
    allowed: bool
    reason: str
    handles: tuple
    refusals: tuple


_FRESHNESS_SECONDS = 60.0


def _to_plain(value):
    if hasattr(value, "__dataclass_fields__"):
        return {name: _to_plain(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, MappingABC):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]
    return value


def _hash_object(value):
    try:
        hasher = getattr(_dp, "_hash_obj", None)
        if callable(hasher):
            result = hasher(value)
            if isinstance(result, str) and len(result) == 64:
                return result
    except Exception:
        pass
    try:
        body = json.dumps(_to_plain(value), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except Exception:
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_context_hash(context):
    return _hash_object(context)


def _hex64(value):
    if not isinstance(value, str) or len(value) != 64:
        return False
    for ch in value:
        if ch not in "0123456789abcdef":
            return False
    return True


def _parse_iso_utc(text):
    if not isinstance(text, str) or not text:
        return None
    raw = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        stamp = datetime.datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp


def _fresh(context_now, authority_now):
    left = _parse_iso_utc(context_now)
    right = _parse_iso_utc(authority_now)
    if left is None or right is None:
        return False
    return abs((left - right).total_seconds()) <= _FRESHNESS_SECONDS


def _call_authority(authority, name, *args):
    method = getattr(authority, name, None)
    if not callable(method):
        raise AuthorizationError("NO-AUTHORITY")
    try:
        return method(*args)
    except AuthorizationError:
        raise
    except Exception:
        raise AuthorizationError("NO-AUTHORITY") from None


def _authority_bool(authority, name, *args):
    result = _call_authority(authority, name, *args)
    if result is True:
        return True
    if result is False:
        return False
    raise AuthorizationError("NO-AUTHORITY")


def _param_type_ok(value, expected):
    if expected == "str":
        return isinstance(value, str)
    if expected == "int":
        return type(value) is int
    if expected == "float":
        return type(value) in (int, float)
    if expected == "bool":
        return type(value) is bool
    if expected == "json":
        return True
    return False


def _validate_params(action, schema):
    params = getattr(action, "params", None)
    if not isinstance(params, MappingABC):
        return "INVALID_PARAMS"
    required = getattr(schema, "required_params", ())
    optional = getattr(schema, "optional_params", ())
    max_params = getattr(schema, "max_params", None)
    if type(max_params) is not int or max_params < 0:
        return "INVALID_SCHEMA"
    if len(params) > max_params:
        return "LIMIT_PARAMS"
    for name in required:
        if name not in params:
            return "MISSING_PARAM"
    for name in params:
        if name not in required and name not in optional:
            return "UNKNOWN_PARAM"
    param_types = getattr(schema, "param_types", None)
    if not isinstance(param_types, MappingABC):
        return "INVALID_SCHEMA"
    for name, value in params.items():
        expected = param_types.get(name)
        if expected is None:
            continue
        if not _param_type_ok(value, expected):
            return "PARAM_TYPE"
    return None


def recheck_execution(decision, context, authority, limits):
    if getattr(decision, "allowed", None) is not True:
        return False, ("REFUSED",), {}
    max_actions = getattr(limits, "max_actions", None)
    if type(max_actions) is not int or max_actions < 0:
        return False, ("LIMIT_ACTIONS",), {}
    requests = getattr(decision, "action_requests", None)
    if not isinstance(requests, tuple):
        return False, ("INVALID_ACTION",), {}
    if len(requests) > max_actions:
        return False, ("LIMIT_ACTIONS",), {}
    expected_hash = getattr(decision, "context_hash", None)
    if not _hex64(expected_hash):
        return False, ("CONTEXT_MISMATCH",), {}
    actual_hash = canonical_context_hash(context)
    if actual_hash != expected_hash:
        return False, ("CONTEXT_MISMATCH",), {}
    try:
        authority_now = _call_authority(authority, "now_utc")
    except AuthorizationError as exc:
        return False, (exc.code,), {}
    if not _fresh(getattr(context, "now_utc", None), authority_now):
        return False, ("STALE_CONTEXT",), {}
    context_id = getattr(context, "context_id", None)
    actor_id = getattr(context, "actor_id", None)
    revealed = getattr(decision, "revealed", None)
    revealed_names = tuple(sorted(revealed.keys())) if isinstance(revealed, MappingABC) else ()
    refusals = []
    effective_by_index = {}
    for index, action in enumerate(requests):
        action_refusals = []
        effective = None
        if not isinstance(action, _dp.ActionRequest):
            action_refusals.append("INVALID_ACTION")
        else:
            kind = action.kind
            schemas = getattr(context, "action_schemas", None)
            allowed_kinds = getattr(context, "allowed_action_kinds", None)
            if not isinstance(kind, str) or not kind:
                action_refusals.append("UNKNOWN_ACTION")
            elif not isinstance(schemas, MappingABC) or kind not in schemas:
                action_refusals.append("UNKNOWN_ACTION")
            elif not isinstance(allowed_kinds, tuple) or kind not in allowed_kinds:
                action_refusals.append("UNKNOWN_ACTION")
            else:
                schema = schemas[kind]
                if not isinstance(schema, _dp.ActionSchema):
                    action_refusals.append("INVALID_SCHEMA")
                else:
                    if action.target not in schema.allowed_targets:
                        action_refusals.append("UNKNOWN_TARGET")
                    param_error = _validate_params(action, schema)
                    if param_error is not None:
                        action_refusals.append(param_error)
            if not action_refusals:
                try:
                    if not _authority_bool(authority, "privacy_allowed", context_id, actor_id, revealed_names):
                        action_refusals.append("PRIVACY_DENIED")
                    elif not _authority_bool(authority, "scope_allowed", context_id, actor_id, action.scope):
                        action_refusals.append("SCOPE_DENIED")
                    elif not _authority_bool(authority, "dependencies_satisfied",
                                             tuple(getattr(context, "dependency_ids", ()))):
                        action_refusals.append("DEPENDENCY_MISSING")
                    elif not _authority_bool(authority, "claims_held", tuple(action.claims), actor_id):
                        action_refusals.append("CLAIM_MISSING")
                    else:
                        effective = _call_authority(authority, "effective_ceiling", context_id, kind)
                        effective = _dp.enforce_ceiling(action.requested_ceiling, effective)
                        if not _authority_bool(authority, "founder_authorized",
                                               getattr(context, "founder_authorization_id", None),
                                               actor_id, kind, action.target, action.scope, effective):
                            action_refusals.append("FOUNDER_UNAUTHORIZED")
                        else:
                            done = _call_authority(authority, "is_done", action.idempotency_key)
                            if done is True:
                                action_refusals.append("ALREADY_DONE")
                            elif done is not False:
                                action_refusals.append("NO-AUTHORITY")
                except AuthorizationError as exc:
                    action_refusals.append(exc.code)
                except _dp.PolicyError as exc:
                    action_refusals.append(getattr(exc, "code", None) or "CAP_OVERRIDE")
                except Exception:
                    action_refusals.append("NO-AUTHORITY")
        if action_refusals:
            refusals.extend(action_refusals)
        if effective is not None:
            effective_by_index[index] = effective
    if refusals:
        return False, tuple(refusals), {"effective": effective_by_index}
    return True, (), {"effective": effective_by_index}


def mint_handle(action, context, effective, policy_hash, context_hash, authority):
    if not isinstance(action, _dp.ActionRequest):
        raise AuthorizationError("INVALID_ACTION")
    if not _hex64(policy_hash) or not _hex64(context_hash):
        raise AuthorizationError("INVALID_HASH")
    if not isinstance(effective, _dp.ResourceCeiling):
        raise AuthorizationError("CAP_INVALID")
    now_text = _call_authority(authority, "now_utc")
    now_dt = _parse_iso_utc(now_text)
    if now_dt is None:
        raise AuthorizationError("NO-AUTHORITY")
    issued_at = now_text
    expires_at = (now_dt + datetime.timedelta(seconds=_FRESHNESS_SECONDS)).isoformat()
    handle_id = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    payload = {
        "handle_id": handle_id,
        "action": _to_plain(action),
        "effective_ceiling": _to_plain(effective),
        "policy_hash": policy_hash,
        "context_hash": context_hash,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
    }
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise AuthorizationError("INVALID_ACTION") from None
    signature = _call_authority(authority, "sign_handle", canonical)
    if not isinstance(signature, str) or not signature:
        raise AuthorizationError("NO-AUTHORITY")
    return ActionHandle(handle_id=handle_id, action=action, effective_ceiling=effective,
                        policy_hash=policy_hash, context_hash=context_hash,
                        issued_at=issued_at, expires_at=expires_at, nonce=nonce,
                        authority_signature=signature)


def authorize(decision, context, authority, limits, now_utc=None):
    allowed, refusals, info = recheck_execution(decision, context, authority, limits)
    policy_decision_hash = _hash_object(decision)
    execution_id = uuid.uuid4().hex
    if not allowed:
        reason = refusals[0] if refusals else "REFUSED"
        return ExecutionDecision(execution_id, policy_decision_hash, False, reason,
                                 (), tuple(refusals))
    policy_hash = getattr(decision, "policy_hash", None)
    context_hash = getattr(decision, "context_hash", None)
    if not _hex64(policy_hash) or not _hex64(context_hash):
        return ExecutionDecision(execution_id, policy_decision_hash, False,
                                 "INVALID_HASH", (), ("INVALID_HASH",))
    requests = getattr(decision, "action_requests", ())
    effective_map = info.get("effective", {}) if isinstance(info, dict) else {}
    handles = []
    for index, action in enumerate(requests):
        effective = effective_map.get(index) if isinstance(effective_map, dict) else None
        if effective is None:
            return ExecutionDecision(execution_id, policy_decision_hash, False,
                                     "NO-AUTHORITY", (), ("NO-AUTHORITY",))
        try:
            handles.append(mint_handle(action, context, effective, policy_hash,
                                       context_hash, authority))
        except AuthorizationError as exc:
            return ExecutionDecision(execution_id, policy_decision_hash, False,
                                     exc.code, (), (exc.code,))
        except Exception:
            return ExecutionDecision(execution_id, policy_decision_hash, False,
                                     "NO-AUTHORITY", (), ("NO-AUTHORITY",))
    return ExecutionDecision(execution_id, policy_decision_hash, True, "ALLOWED",
                             tuple(handles), ())


_BRIDGE = None
_BRIDGE_LOCK = threading.Lock()


def _bridge_module():
    global _BRIDGE
    if _BRIDGE is not None:
        return _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dream_bridge.py")
            if not os.path.isfile(path):
                raise AuthorizationError("NO-AUTHORITY")
            spec = importlib.util.spec_from_file_location("d7_scripts_dream_bridge", path)
            if spec is None or spec.loader is None:
                raise AuthorizationError("NO-AUTHORITY")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _BRIDGE = module
    return _BRIDGE


def _recorder():
    return _bridge_module().recorder()


def _refused(prior, reason, code):
    return ExecutionDecision(prior.execution_id, prior.policy_decision_hash, False,
                             reason, (), (code,))


def run(decision, context, authority, limits, run_dir):
    granted = authorize(decision, context, authority, limits)
    if not granted.allowed:
        return granted
    if not isinstance(run_dir, str) or not run_dir:
        return _refused(granted, "RECORD_FAILED", "RECORD_FAILED")
    try:
        recorder = _recorder()
    except Exception:
        return _refused(granted, "RECORD_FAILED", "RECORD_FAILED")
    record_decision = getattr(recorder, "record_decision", None)
    record_outcome = getattr(recorder, "record_outcome", None)
    if not callable(record_decision) or not callable(record_outcome):
        return _refused(granted, "RECORD_FAILED", "RECORD_FAILED")
    for handle in granted.handles:
        action = getattr(handle, "action", None)
        observed = {
            "handle_id": getattr(handle, "handle_id", None),
            "kind": getattr(action, "kind", None),
            "target": getattr(action, "target", None),
            "idempotency_key": getattr(action, "idempotency_key", None),
        }
        try:
            intent_id = record_decision(run_dir, "d7.action", observed=observed,
                                        options=["intent"], chosen="intent",
                                        policy_version=getattr(handle, "policy_hash", None) or "d7")
        except Exception:
            return _refused(granted, "RECORD_FAILED", "RECORD_FAILED")
        if not intent_id:
            return _refused(granted, "RECORD_FAILED", "RECORD_FAILED")
        try:
            _call_authority(authority, "execute", handle)
        except AuthorizationError:
            try:
                record_outcome(run_dir, intent_id, "FAIL", grader="d7.authorize",
                               detail={"error": "EXECUTE_FAILED"})
            except Exception:
                pass
            return _refused(granted, "FAIL", "FAIL")
        try:
            record_outcome(run_dir, intent_id, "PASS", grader="d7.authorize")
        except Exception:
            return _refused(granted, "RECORD_FAILED", "RECORD_FAILED")
    return ExecutionDecision(granted.execution_id, granted.policy_decision_hash, True,
                             "EXECUTED", granted.handles, ())
