"""L5b.3 flag_rules: pre-mortem lint rules over boundary calls.

Rule ids emitted by flag_call:
    L5B-GRANT-FORGERY, L5B-PATH-UNCONFINED, L5B-COST-FALLBACK,
    L5B-CLEANUP-NONMASK, L5B-CLI-BOUNDS, L5B-LEASE-ROOT,
    L5B-DECODE-NARROW, L5B-CEILING-GLOBAL, L5B-PROPAGATE-SAFE.

Unknown, corrupt or missing input BLOCKS with a deliberate ValueError,
never a raw interpreter error and never a silent accept.
"""
import ast
from collections import namedtuple

BoundaryCall = namedtuple(
    "BoundaryCall",
    ["file", "line", "column", "symbol", "kind", "snippet"],
)
LintHit = namedtuple(
    "LintHit",
    ["rule_id", "file", "line", "column", "symbol", "reason"],
)

_PROPAGATE_MARKER = "# l5b: PROPAGATE_SAFE:"
_MIN_PROPAGATE_REASON = 12


def _require_fn(fn):
    if not isinstance(fn, ast.AST):
        raise ValueError("fn must be an ast.AST, got %s" % type(fn).__name__)


def _require_call(call):
    if not isinstance(call, BoundaryCall):
        raise ValueError("call must be a BoundaryCall, got %s" % type(call).__name__)
    if not isinstance(call.file, str) or not call.file:
        raise ValueError("call.file must be a non-empty string")
    if isinstance(call.line, bool) or not isinstance(call.line, int) or call.line < 1:
        raise ValueError("call.line must be a positive integer")
    if isinstance(call.column, bool) or not isinstance(call.column, int) or call.column < 1:
        raise ValueError("call.column must be a positive integer")
    if not isinstance(call.symbol, str) or not call.symbol:
        raise ValueError("call.symbol must be a non-empty string")
    if not isinstance(call.kind, str) or not call.kind:
        raise ValueError("call.kind must be a non-empty string")
    if not isinstance(call.snippet, str):
        raise ValueError("call.snippet must be a string")


def _require_both(fn, call):
    _require_fn(fn)
    _require_call(call)


def _require_source(source):
    if not isinstance(source, str):
        raise ValueError("source must be a string, got %s" % type(source).__name__)


def _string_constants(fn):
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def _name_ids(fn):
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
    return out


def _has_any_name(fn, wanted):
    ids = _name_ids(fn)
    for candidate in wanted:
        if candidate in ids:
            return True
    return False


def _contains_string(fn, needle):
    low = needle.lower()
    for value in _string_constants(fn):
        if low in value.lower():
            return True
    return False


def _handler_is_broad(handler):
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in ("Exception", "BaseException")
    return False


def _handler_lacks_raise(handler):
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return False
    return True


def rule_grant_forgery(fn, call):
    _require_both(fn, call)
    if not _contains_string(fn, "grant"):
        return False
    return not _has_any_name(fn, ("check_authority", "has_authority", "is_authorized"))


def rule_path_unconfined(fn, call):
    _require_both(fn, call)
    if call.kind != "file_io":
        return False
    return not _has_any_name(fn, ("safepath", "safe_join", "confine_path"))


def rule_cost_fallback_stated(fn, call):
    _require_both(fn, call)
    if not _contains_string(fn, "cost"):
        return False
    return not _has_any_name(fn, ("log_cost_fallback", "record_cost_fallback"))


def rule_cleanup_nonmask(fn, call):
    _require_both(fn, call)
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _handler_is_broad(handler):
                continue
            if _handler_lacks_raise(handler):
                return True
    return False


def rule_cli_bounds(fn, call):
    _require_both(fn, call)
    if not _has_any_name(fn, ("ArgumentParser", "add_argument")):
        return False
    return not _has_any_name(fn, ("bounded_int", "clamp"))


def rule_lease_root(fn, call):
    _require_both(fn, call)
    if not _contains_string(fn, "lease"):
        return False
    return not _has_any_name(fn, ("assert_root", "pin_root"))


def rule_decode_narrow(fn, call):
    _require_both(fn, call)
    if call.kind != "json_decode":
        return False
    for node in ast.walk(fn):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if _handler_is_broad(node):
            return True
    return False


def rule_ceiling_global(fn, call):
    _require_both(fn, call)
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "ceiling" in target.id.lower():
                    return True
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and "ceiling" in target.id.lower():
                return True
    return False


_RULES = (
    ("L5B-GRANT-FORGERY", rule_grant_forgery),
    ("L5B-PATH-UNCONFINED", rule_path_unconfined),
    ("L5B-COST-FALLBACK", rule_cost_fallback_stated),
    ("L5B-CLEANUP-NONMASK", rule_cleanup_nonmask),
    ("L5B-CLI-BOUNDS", rule_cli_bounds),
    ("L5B-LEASE-ROOT", rule_lease_root),
    ("L5B-DECODE-NARROW", rule_decode_narrow),
    ("L5B-CEILING-GLOBAL", rule_ceiling_global),
)


def _prior_line_annotation(source, call):
    _require_source(source)
    _require_call(call)
    lines = source.splitlines()
    idx = call.line - 2
    if idx < 0 or idx >= len(lines):
        return ""
    prior = lines[idx]
    pos = prior.find(_PROPAGATE_MARKER)
    if pos < 0:
        return ""
    return prior[pos + len(_PROPAGATE_MARKER):].strip()


def flag_call(source, call):
    _require_source(source)
    _require_call(call)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        raise ValueError("source does not parse")
    hits = []
    for rule_id, rule in _RULES:
        if rule(tree, call):
            hits.append(LintHit(rule_id, call.file, call.line, call.column,
                                call.symbol, "rule %s fired" % rule_id))
    reason = _prior_line_annotation(source, call)
    if len(reason) < _MIN_PROPAGATE_REASON:
        hits.append(LintHit("L5B-PROPAGATE-SAFE", call.file, call.line,
                            call.column, call.symbol,
                            "prior line needs '# l5b: PROPAGATE_SAFE: <12+ chars>'"))
    return tuple(hits)
