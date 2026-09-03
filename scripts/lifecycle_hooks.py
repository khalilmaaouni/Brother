"""lifecycle_hooks: the undeclared-write refusal, at the layer that can stop it.

F12. Every other control in this estate is a rule a model may or may not follow.
This one is a PreToolUse hook: the host calls it BEFORE a write happens, reads
its exit code, and refuses the tool call on a 2. That is the difference between
a rule and a control, and it is the difference this estate keeps writing
UNENFORCED next to its own laws.

WHAT IT REFUSES: a write to a path that no open task declares in its ownedPaths.
Not because undeclared writing is untidy, but because the whole scheduler rests
on declared write sets. graph_loop.py decides what may run beside what by
comparing them, and a write outside a declaration silently makes that decision
wrong: two agents can be told they are disjoint while editing the same file. The
scheduler already refuses to dispatch a node whose scope is undeclared; this
refuses the write itself, which is the half that was missing.

THE PRECEDENCE PROBLEM, and why this file declares one rather than adding to a
pile. Instructions, rules, skills, hooks and memories are five overlapping
policy systems, and an estate that keeps adding enforcement without saying which
layer wins ends up with rules nobody can reason about. The order is stated once,
in ORDER below, and this module refuses to be a second enforcement layer for a
rule that already has one.

FAIL OPEN, DELIBERATELY. Every unexpected condition allows the write: malformed
input, an unreadable registry, a path shape this cannot parse. A hook that fails
closed on its own bugs stops all work on the machine and gets deleted within the
hour, which enforces nothing. The cost is that a genuine violation slips through
when this module is broken, and that is the correct trade for a control whose
alternative is not existing.

WHAT IT DOES NOT DO: it never decides that a declaration is WISE, only that one
exists. Judging scope is a person's job and a second-guessing hook is a hook
that gets switched off.

Python 3, standard library only. No network.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ALLOW = 0
REFUSE = 2

#: THE ONE PRECEDENCE ORDER. Written here because it is the thing this module
#: exists not to muddle, and because a precedence order kept in prose is a
#: precedence order nobody applies the same way twice.
#:
#:   1. SAFETY AND FOUNDER GATES. Credentials, destructive acts, publication,
#:      spend. Nothing below may permit what these refuse, and no hook may
#:      grant an exception to them.
#:   2. MECHANICAL HOOKS, this file among them. They refuse at the tool layer
#:      and their refusal is final for that call.
#:   3. TOOL-LEVEL CHECKS, run in a battery. They decide green, not permission.
#:   4. WRITTEN RULES, in the estate's instruction files. They bind conduct and
#:      cannot stop a call.
#:   5. RECALLED MEMORY. Context and warning, never authority.
#:
#: A rule enforced at one layer is NOT re-enforced at another. Adding a second
#: layer for the same rule is how precedence stops being knowable.
ORDER = ("safety-and-founder-gates", "mechanical-hooks", "tool-checks",
         "written-rules", "recalled-memory")

#: Tool names that write. Read from the payload rather than guessed at: a tool
#: this does not know about is allowed, because refusing an unknown tool would
#: block the estate the first time the host adds one.
WRITING_TOOLS = ("Write", "Edit", "NotebookEdit")


def _paths_from(payload):
    """Every path this tool call would write, or an empty list.

    Empty means "nothing to judge", which ALLOWS. That is the fail-open rule
    applied to parsing: a payload shape this cannot read must not become a
    refusal, because the refusal would be about this parser rather than about
    the write."""
    tool = (payload or {}).get("tool_name") or ""
    if tool not in WRITING_TOOLS:
        return []
    inp = (payload or {}).get("tool_input") or {}
    path = inp.get("file_path") or inp.get("notebook_path")
    return [path] if isinstance(path, str) and path.strip() else []


def _relative(path, root):
    """The path as the registry spells it, or None when it is outside root.

    Outside the repository is not this hook's business: a declaration is
    repository relative, so a write to a temp directory or a home file has
    nothing to be compared against and is allowed."""
    try:
        full = os.path.realpath(path)
        base = os.path.realpath(root)
    except (OSError, ValueError):
        return None
    if full == base or not full.startswith(base + os.sep):
        return None
    return full[len(base) + 1:]


def declared_by_any(rel, tasks, owns):
    """Whether some open task declares this path. `owns` is injected so the
    real matcher is reused rather than reimplemented: prefix for directories,
    exact for files, which is the reading the authority guard already uses."""
    return any(owns(t, rel) for t in tasks or [])


def decide(payload, root, tasks, owns):
    """(exit_code, message). Pure, so every branch is testable without a host."""
    paths = _paths_from(payload)
    if not paths:
        return ALLOW, ""
    if tasks is None:
        # NO-DATA on the registry. Allowed, and SAID: a control that cannot read
        # its own inputs must not pretend it checked. Silence here would be
        # indistinguishable from a clean pass.
        return ALLOW, ("lifecycle-hooks: NO-DATA, the task registry could not be "
                       "read, so no declaration was checked. This is not a pass, "
                       "it is an unchecked write")
    for path in paths:
        rel = _relative(path, root)
        if rel is None:
            continue
        if not declared_by_any(rel, tasks, owns):
            open_ids = ", ".join(str(t.get("id") or "?") for t in tasks[:6]) or "none"
            return REFUSE, (
                "lifecycle-hooks REFUSED: %s is not declared by any open task "
                "(open: %s). The scheduler decides what may run beside what by "
                "comparing declared write sets, so a write outside a declaration "
                "makes that decision wrong: two agents can be told they are "
                "disjoint while editing this same file. Declare it with "
                "`sbe task open --owns %s`, or work inside a task that already "
                "covers it." % (rel, open_ids, rel))
    return ALLOW, ""


def main(argv=None):
    """Reads the host's JSON payload on stdin. Exit 2 refuses the call."""
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        # sbe: allow-silent fail open, see the module docstring
        return ALLOW
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return ALLOW          # malformed input allows; see fail open
    if not isinstance(payload, dict):
        return ALLOW

    root = os.environ.get("BROTHER_HOOK_ROOT") or os.getcwd()
    try:
        import task_watchdog as tw
        tasks, owns = tw.read_registry(), tw.owns
    except Exception as exc:  # noqa: BLE001
        # sbe: allow-silent the reason is printed below, then it allows
        print("lifecycle-hooks: NO-DATA, could not load the registry reader "
              "(%s); nothing was checked" % exc, file=sys.stderr)
        return ALLOW

    code, message = decide(payload, root, tasks, owns)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
