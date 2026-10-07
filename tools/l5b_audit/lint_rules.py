"""L5b.2 silent-failure lint rules."""
from __future__ import annotations

import ast
import posixpath
from dataclasses import dataclass
from typing import Optional, Tuple

from tools.l5b_audit.scanner import BoundaryCall, SUBPROCESS_SYMBOLS

RULE_BARE_EXCEPT = "L5B-BARE-EXCEPT"
RULE_EXCEPT_PASS = "L5B-EXCEPT-PASS"
RULE_DISCARD_RETURN = "L5B-DISCARD-RETURN"
RULE_FORCE_UNWRAP = "L5B-FORCE-UNWRAP"

RULE_IDS = frozenset({
    RULE_BARE_EXCEPT,
    RULE_EXCEPT_PASS,
    RULE_DISCARD_RETURN,
    RULE_FORCE_UNWRAP,
})

KINDS = frozenset({
    "NETWORK",
    "FILE_IO",
    "JSON",
    "SUBPROCESS",
})

SEVERITY_BLOCK = "BLOCK"

returns_checked = frozenset({
    "open",
    "json.load",
    "json.loads",
    "os.open",
    "os.fdopen",
    "tempfile.mkstemp",
    "os.path.exists",
    "os.path.abspath",
    "os.path.join",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
})


@dataclass(frozen=True)
class LintHit:
    hit_id: str
    rule_id: str
    severity: str
    enclosing_function: str


def _validate_rule_id(rule_id: object) -> None:
    if not isinstance(rule_id, str):
        raise ValueError("rule_id must be a str")
    if rule_id not in RULE_IDS:
        raise ValueError("rule_id must be one of the L5b rule ids")


def _validate_file(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("file must be a str")
    if not value:
        raise ValueError("file must not be empty")
    if "\x00" in value:
        raise ValueError("file must not contain NUL")
    if value.startswith("/"):
        raise ValueError("file must be a relative POSIX path")
    if posixpath.isabs(value):
        raise ValueError("file must be a relative POSIX path")
    parts = value.split("/")
    if any(part == ".." for part in parts):
        raise ValueError("file must not contain '..' path segments")
    if value in (".", ".."):
        raise ValueError("file must name a file")


def _validate_call(call: object) -> None:
    if call is None:
        raise ValueError("call must not be None")
    for attr in ("file", "line", "column", "kind", "symbol", "enclosing_function", "callee_defined_here"):
        if not hasattr(call, attr):
            raise ValueError(f"call missing attribute: {attr}")
    _validate_file(getattr(call, "file"))
    line = getattr(call, "line")
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        raise ValueError("line must be an int >= 1")
    column = getattr(call, "column")
    if isinstance(column, bool) or not isinstance(column, int) or column < 0:
        raise ValueError("column must be an int >= 0")
    kind = getattr(call, "kind")
    if not isinstance(kind, str):
        raise ValueError("kind must be a str")
    if kind not in KINDS:
        raise ValueError("kind must be one of the L5b boundary kinds")
    symbol = getattr(call, "symbol")
    if not isinstance(symbol, str):
        raise ValueError("symbol must be a str")
    if not symbol:
        raise ValueError("symbol must not be empty")
    enclosing = getattr(call, "enclosing_function")
    if not isinstance(enclosing, str):
        raise ValueError("enclosing_function must be a str")
    callee_defined_here = getattr(call, "callee_defined_here")
    if not isinstance(callee_defined_here, bool):
        raise ValueError("callee_defined_here must be a bool")


def _validate_fn(fn: object) -> None:
    if not isinstance(fn, ast.AST):
        raise ValueError("fn must be an ast.AST")


def hit_id(call: BoundaryCall, rule_id: str) -> str:
    _validate_call(call)
    _validate_rule_id(rule_id)
    return f"{call.file}:{call.line}:{call.column}:{rule_id}"


def _build_parent_map(root: ast.AST) -> dict:
    parents = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _find_call_node(fn: ast.AST, call: BoundaryCall) -> Optional[ast.Call]:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if node.lineno == call.line and node.col_offset == call.column:
                return node
    return None


def _enclosing_function_name(fn: ast.AST) -> str:
    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return fn.name
    return "<module>"


def _is_in_try_body(try_node: ast.Try, node: ast.AST, parents: dict) -> bool:
    child = node
    parent = parents.get(child)
    while parent is not None:
        if parent is try_node:
            return child in try_node.body
        child = parent
        parent = parents.get(child)
    return False


def rule_bare_except(fn: ast.AST, call: BoundaryCall) -> bool:
    _validate_fn(fn)
    _validate_call(call)
    call_node = _find_call_node(fn, call)
    if call_node is None:
        return False
    parents = _build_parent_map(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            if _is_in_try_body(node, call_node, parents):
                for handler in node.handlers:
                    if handler.type is None:
                        return True
    return False


def rule_except_pass(fn: ast.AST, call: BoundaryCall) -> bool:
    _validate_fn(fn)
    _validate_call(call)
    call_node = _find_call_node(fn, call)
    if call_node is None:
        return False
    parents = _build_parent_map(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            if _is_in_try_body(node, call_node, parents):
                for handler in node.handlers:
                    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                        return True
    return False


def rule_discard_return(fn: ast.AST, call: BoundaryCall) -> bool:
    _validate_fn(fn)
    _validate_call(call)
    call_node = _find_call_node(fn, call)
    if call_node is None:
        return False
    parents = _build_parent_map(fn)
    parent = parents.get(call_node)
    if not isinstance(parent, ast.Expr):
        return False
    if parent.value is not call_node:
        return False
    if call.symbol in SUBPROCESS_SYMBOLS or call.symbol in returns_checked:
        return True
    return False


def rule_force_unwrap(fn: ast.AST, call: BoundaryCall) -> bool:
    _validate_fn(fn)
    _validate_call(call)
    if call.symbol not in ("json.load", "json.loads"):
        return False
    call_node = _find_call_node(fn, call)
    if call_node is None:
        return False
    parents = _build_parent_map(fn)
    parent = parents.get(call_node)

    if isinstance(parent, ast.Subscript) and parent.value is call_node:
        return True
    if isinstance(parent, ast.Attribute) and parent.value is call_node:
        if parent.attr == "get":
            return False
        return True

    var_name = None
    if isinstance(parent, ast.Assign):
        if len(parent.targets) == 1 and isinstance(parent.targets[0], ast.Name):
            var_name = parent.targets[0].id
    elif isinstance(parent, ast.AnnAssign):
        if isinstance(parent.target, ast.Name):
            var_name = parent.target.id

    if var_name is None:
        return False

    assign_line = parent.lineno
    guards = []
    unwraps = []
    for node in ast.walk(fn):
        p = parents.get(node)
        skip = False
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if p is not fn:
                    skip = True
                break
            p = parents.get(p)
        if skip:
            continue
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "isinstance":
                if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == var_name:
                    guards.append(node.lineno)
        if isinstance(node, ast.Compare):
            for elem in (node.left,) + tuple(node.comparators):
                if isinstance(elem, ast.Name) and elem.id == var_name:
                    for op in node.ops:
                        if isinstance(op, ast.In):
                            guards.append(node.lineno)
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == var_name:
                unwraps.append(node.lineno)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id == var_name:
                p2 = parents.get(node)
                if isinstance(p2, ast.Call) and p2.func is node and node.attr == "get":
                    continue
                unwraps.append(node.lineno)

    for u_line in unwraps:
        if u_line <= assign_line:
            continue
        has_guard = any(assign_line < g_line < u_line for g_line in guards)
        if not has_guard:
            return True
    return False


def lint_call(source: str, call: BoundaryCall) -> Tuple[LintHit, ...]:
    if not isinstance(source, str):
        raise ValueError("source must be a str")
    _validate_call(call)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError("source has SyntaxError") from exc

    lines = source.splitlines()
    if call.line >= 2:
        prior_index = call.line - 2
        prior = lines[prior_index] if prior_index < len(lines) else ""
        # an exemption carries a REASON that is read: the marker clears a hit only when at least 12 characters of reason follow it.
        # A bare marker was a hidden waiver of every rule (council attack on the landed unit, 2026-09-20; the spec's own flip condition).
        marker = "# l5b: PROPAGATE_SAFE:"
        if marker in prior and len(prior.split(marker, 1)[1].strip()) >= 12:
            return ()

    parents = _build_parent_map(tree)
    call_node = _find_call_node(tree, call)
    if call_node is None:
        return ()
    fn = call_node
    while fn is not None and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn = parents.get(fn)
    if fn is None:
        fn = tree
    enclosing = _enclosing_function_name(fn)
    hits = []
    for rule_id, rule_fn in (
        (RULE_BARE_EXCEPT, rule_bare_except),
        (RULE_EXCEPT_PASS, rule_except_pass),
        (RULE_DISCARD_RETURN, rule_discard_return),
        (RULE_FORCE_UNWRAP, rule_force_unwrap),
    ):
        if rule_fn(fn, call):
            hits.append(LintHit(
                hit_id=hit_id(call, rule_id),
                rule_id=rule_id,
                severity=SEVERITY_BLOCK,
                enclosing_function=enclosing,
            ))
    return tuple(hits)
