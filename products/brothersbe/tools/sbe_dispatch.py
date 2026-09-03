#!/usr/bin/env python3
"""BrotherSBE dispatch gate: the mechanical control on how work is launched.

Mirrors tools/sbe_intake.py: objective questions in, one machine-checked
decision out, exit codes 0 (pass), 1 (refused) and 2 (usage error), and every
answer read for MEANING, never for truthiness or for presence alone.

Why this exists (spec: design/phase-0/SPEC.md, Lane B). Persona 3, the agent
orchestrator, has no mechanical protection against the failure that cost this
program 11,017k output tokens in one day: agents launched with no declared
model tier, budget, file list or done-check, and new work opened while owed
work sat unfinished. A rule written into a prompt is not a control. This file
is the control.

Two independent subcommands, each usable in CI and by a hook:

  sbe_dispatch.py brief --file PATH        validate one dispatch brief
  sbe_dispatch.py brief --json -           (or read the brief from stdin)
  sbe_dispatch.py loop-open --state PATH --owed PATH
                                            refuse opening new work when owed
                                            work is unfinished or the loop's
                                            budget is already exhausted

Every problem found is reported, never only the first: an agent fixing one
missing field at a time is an agent making four round trips, and that four
round trip pattern is the same tax the 11M-token failure paid.

The load-bearing honesty rule of this file: a MISSING owed-items file is
NO-DATA and refuses `loop-open`, with that reason stated by name. The absence
of the file that would list unfinished work is never evidence that no
unfinished work exists. See check_loop_open and its docstring.

The same rule covers the budget half of loop-open: a missing state file, an
empty one, a state file with no `budget` block, or a `budget` whose `cap` or
`spent` is not a number is NO-DATA and refuses the loop open exactly as a
missing owed file does, naming precisely which key was absent or
non-numeric. Being unable to confirm that a budget is not exhausted is not
evidence that it is safe to open the loop, so `loop-open` never prints a PASS
line over a budget it never measured. See _budget_problem and its docstring.
"""
import json, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import answered, say

#: The "this file could not be read at all" sentinel. It is a unique object and
#: deliberately NOT None, because a file whose entire content is the valid JSON
#: token `null` parses successfully TO None. Overloading None meant such a file
#: hit the parse-failure path, every downstream check was skipped, and the gate
#: printed a PASS asserting an owed list it had never read and a budget it had
#: never measured. A sentinel that a data file can forge is not a sentinel.
_UNREAD = object()

# A model_tier is a CAPABILITY PROFILE (how much the agent can do), never a
# model version (which model produces the tokens). The two ideas get conflated
# the moment someone writes "sonnet" where "builder" belongs, and the routing
# layer this brief feeds reads model_tier to pick a capability class, not a
# vendor SKU. A tier that names a model version has silently pinned dispatch
# to today's model naming, which breaks the day a model is renamed or retired,
# so it is refused by name rather than accepted and misread downstream.
MODEL_TIERS = ("fast_worker", "builder", "navigator", "reviewer", "researcher",
              "vision_worker")

# Substrings that mark a value as a model version rather than a capability
# profile. Matched case-insensitively so "Claude", "CLAUDE-3" and "opus-4.1"
# are all caught, because a version string is a version string in any case.
MODEL_VERSION_WORDS = ("claude", "gpt", "opus", "sonnet", "haiku", "gemini")

DISPATCH_TIERS = ("T1", "T2", "T3")
# The scaling ceilings this gate enforces, named here once so the refusal text
# and the rule read from the same numbers. T3 carries no ceiling: full-audit
# scale is a legitimate shape, but it is never a default, so T3 is refused
# unless it names, in `tier_reason`, why full-audit scale is warranted here.
AGENT_CEILING = {"T1": 1, "T2": 4}

OWED_FILE_DEFAULT_NOTE = (
    "the absence of the file that would list unfinished work is never evidence "
    "that no unfinished work exists")


def _is_absolute_path(p):
    """True when `p` is an absolute path on ANY platform this tool runs work
    against, not only the host it happens to run on.

    A relative-path fence is only a fence when it rejects every spelling of
    "outside the repository", and this program's own repository is mid Windows
    port: `os.path.isabs` alone reads a Windows drive path (`C:\\Users\\x`) or
    a UNC path (`\\\\host\\share`) as RELATIVE when this check runs on a posix
    host, because posix has no concept of a drive letter. A files[] entry that
    escapes the repository on Windows and is accepted here because the CI
    runner happens to be Linux is a fence with a hole cut in it for exactly
    the platform the files are headed to.
    """
    if os.path.isabs(p):
        return True
    if p.startswith(("/", "\\")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", p):
        return True
    return False


def _positive_int(value):
    """`value` as a positive int, or None when it is not one.

    Booleans are excluded on purpose: `isinstance(True, int)` is True in
    Python, so `token_budget: true` would otherwise read as the budget 1.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def validate_brief(brief):
    """The list of every problem `brief` has, or [] when it passes.

    Every field is checked and every problem named, in the order the spec
    lists the required fields, so a caller fixing them top to bottom fixes
    them in the order this function reports them. Nothing here returns early
    on the first problem: an agent fixing one missing field at a time is an
    agent making four round trips.
    """
    problems = []
    if not isinstance(brief, dict):
        return ["the brief is %s, not a JSON object" % type(brief).__name__]

    objective = brief.get("objective")
    if answered(objective) is None:
        problems.append("objective is missing, empty or whitespace only (got %r); "
                        "state what the agent must achieve" % (objective,))

    tier_val = brief.get("model_tier")
    if answered(tier_val) is None:
        problems.append("model_tier is missing, empty or whitespace only; it must be one of "
                        "%s" % ", ".join(MODEL_TIERS))
    elif not isinstance(tier_val, str):
        problems.append("model_tier is %s, not a string; it must be one of %s"
                        % (type(tier_val).__name__, ", ".join(MODEL_TIERS)))
    else:
        low = tier_val.strip().lower()
        version_hit = next((w for w in MODEL_VERSION_WORDS if w in low), None)
        if version_hit is not None:
            problems.append("model_tier is %r, which names a model version (contains %r), "
                            "not a capability profile; routing is by profile, not by version. "
                            "Use one of %s" % (tier_val, version_hit, ", ".join(MODEL_TIERS)))
        elif tier_val not in MODEL_TIERS:
            problems.append("model_tier is %r, not a recognized capability profile; the "
                            "allowed set is %s" % (tier_val, ", ".join(MODEL_TIERS)))

    budget_val = brief.get("token_budget")
    if _positive_int(budget_val) is None:
        problems.append("token_budget is %r, not a positive integer" % (budget_val,))

    files_val = brief.get("files")
    if not isinstance(files_val, list) or not files_val:
        problems.append("files is %r, not a non-empty list of paths the agent may write"
                        % (files_val,))
    else:
        for i, f in enumerate(files_val):
            if not isinstance(f, str) or answered(f) is None:
                problems.append("files[%d] is %r, not a non-blank path string" % (i, f))
            elif _is_absolute_path(f):
                problems.append("files[%d] is %r, an absolute path; a fence outside the "
                                "repository is not a fence, so every entry must be relative"
                                % (i, f))

    done_check = brief.get("done_check")
    if answered(done_check) is None:
        problems.append("done_check is missing, empty or whitespace only (got %r); it must "
                        "be a runnable command string" % (done_check,))

    dispatch_tier = brief.get("tier")
    if answered(dispatch_tier) is None:
        problems.append("tier is missing, empty or whitespace only; it must be one of %s"
                        % ", ".join(DISPATCH_TIERS))
    elif dispatch_tier not in DISPATCH_TIERS:
        problems.append("tier is %r, not a recognized dispatch tier; the allowed set is %s"
                        % (dispatch_tier, ", ".join(DISPATCH_TIERS)))
    else:
        # The scaling check, the part that closes the 11M-token failure: read
        # agent_count (default 1) and refuse when it exceeds the tier's ceiling.
        raw_count = brief.get("agent_count", 1)
        count = _positive_int(raw_count)
        if count is None:
            problems.append("agent_count is %r, not a positive integer" % (raw_count,))
        elif dispatch_tier in AGENT_CEILING and count > AGENT_CEILING[dispatch_tier]:
            problems.append("tier %s allows at most %d agent(s) (this program's own effort "
                            "scaling law), but agent_count is %d; either lower agent_count or "
                            "move to a tier whose ceiling covers this scale"
                            % (dispatch_tier, AGENT_CEILING[dispatch_tier], count))
        # A separate `if`, not an `elif` chained off the agent_count branches
        # above: an invalid agent_count must not mask a missing tier_reason,
        # so this runs on its own rather than only when the count checks fall
        # through, and every problem in the brief is reported in one pass.
        t3_problem = _t3_tier_reason_problem(dispatch_tier, brief)
        if t3_problem:
            problems.append(t3_problem)
    return problems


def _t3_tier_reason_problem(dispatch_tier, brief):
    """The refusal sentence when `dispatch_tier` is T3 and `brief` carries no
    usable `tier_reason`, or None when the requirement does not apply
    (dispatch_tier is not T3) or is satisfied.

    Kept as its own function, rather than inlined into validate_brief, so the
    T3 requirement can be broken and restored in isolation by a calibrated
    test without disturbing every other field validate_brief checks.
    """
    if dispatch_tier != "T3":
        return None
    if answered(brief.get("tier_reason")) is not None:
        return None
    return ("tier T3 has no agent ceiling, but it requires tier_reason explaining why "
            "full-audit scale is warranted here; tier_reason is missing, empty or "
            "whitespace only (got %r)" % (brief.get("tier_reason"),))


def _blocking_owed_items(owed):
    """(blocking descriptions, parse problems) for one parsed owed-items object.

    An item blocks unless its state is exactly "closed", or it is explicitly
    deferred by the founder AND that deferral carries a non-blank reason. A
    `deferred_by_founder: true` with no `deferral_reason` still blocks: a
    deferral nobody can read the reason for is not a deferral this gate can
    honor, the same rule this project applies to every other waiver.
    """
    blocking, problems = [], []
    items = owed.get("items")
    if not isinstance(items, list):
        problems.append("owed-items file's 'items' is %s, not a list" % type(items).__name__)
        return blocking, problems
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append("owed-items entry %d is %s, not an object" % (i, type(item).__name__))
            continue
        state = item.get("state")
        ident = answered(item.get("id")) or answered(item.get("title")) or "item %d" % i
        if state == "closed":
            continue
        deferred = item.get("deferred_by_founder") is True
        reason = answered(item.get("deferral_reason"))
        if deferred and reason is not None:
            continue
        if deferred and reason is None:
            blocking.append("%s: deferred_by_founder is true but deferral_reason is missing, "
                            "empty or whitespace only, so this deferral cannot be honored "
                            "(state: %r)" % (ident, state))
        else:
            blocking.append("%s: state is %r, not closed, and it is not deferred by the "
                            "founder" % (ident, state))
    return blocking, problems


def _budget_problem(state):
    """The refusal sentence for a parsed loop-state object's budget, or None
    when the budget was actually measured and is clear.

    A budget that cannot be measured refuses the loop open exactly as a
    missing owed file does. Being unable to confirm that a budget is not
    exhausted is not evidence that it is safe to open the loop, so a state
    object with no readable `budget` block, or a `budget` whose `cap` or
    `spent` is not a number, is NO-DATA and returns a refusal naming
    precisely which key was absent or non-numeric. Only a `budget` whose cap
    and spent are both numbers, and whose spent is below the cap, returns
    None: that is the one case this function actually measured and can call
    clear.
    """
    budget = state.get("budget")
    if not isinstance(budget, dict):
        return ("NO-DATA: loop-state records no readable 'budget' object (got %r); budget "
                "spend cannot be confirmed, so it cannot be shown safe to open the loop"
                % (budget,))
    cap = budget.get("cap")
    spent = budget.get("spent")
    # A cap of exactly 0 is a real, if unusual, declared cap (a loop funded
    # with nothing to spend), so it is read as the number 0, not as "unset".
    # Booleans are excluded because isinstance(True, int) is True in Python.
    if isinstance(cap, bool) or not isinstance(cap, (int, float)):
        return ("NO-DATA: loop-state's budget.cap is %r, not a number; budget spend cannot "
                "be confirmed, so it cannot be shown safe to open the loop" % (cap,))
    if isinstance(spent, bool) or not isinstance(spent, (int, float)):
        return ("NO-DATA: loop-state's budget.spent is %r, not a number; budget spend cannot "
                "be confirmed, so it cannot be shown safe to open the loop" % (spent,))
    # json.load parses the bare tokens NaN, Infinity and -Infinity by default,
    # and both are instances of float, so the type guards above accept them.
    # Every comparison against NaN is False, so `spent >= cap` came back False
    # and this function returned "measured and clear" for a budget it had not
    # measured. A number that is not finite is not a measurement.
    #
    # NARROWED TO FLOATS DELIBERATELY. math.isfinite converts its argument to a
    # C double first, and json.load parses integer literals at arbitrary
    # precision, so calling it on a very large int raised OverflowError and
    # tracebacked straight out of this gate. The first version of this guard did
    # exactly that: a check written to refuse an unmeasurable number instead
    # crashed on a perfectly measurable one. A Python int is always finite, so
    # only a float can be nan or inf, and only a float needs asking.
    if isinstance(cap, float) and not math.isfinite(cap):
        return ("NO-DATA: loop-state's budget.cap is %r, which is not a finite number; budget "
                "spend cannot be confirmed, so it cannot be shown safe to open the loop"
                % (cap,))
    if isinstance(spent, float) and not math.isfinite(spent):
        return ("NO-DATA: loop-state's budget.spent is %r, which is not a finite number; budget "
                "spend cannot be confirmed, so it cannot be shown safe to open the loop"
                % (spent,))
    if spent >= cap:
        return ("the loop's declared budget is exhausted: spent %r against a cap of %r"
                % (spent, cap))
    return None


def check_loop_open(state_path, owed_path):
    """The list of every reason `loop-open` refuses, or [] when it passes.

    THE LOAD-BEARING RULE OF THIS FILE. A MISSING owed-items file is NO-DATA,
    named as such, and it refuses the loop open: "the file that would list
    unfinished work is absent" is never read as "there is no unfinished
    work", because the two are indistinguishable from a missing file and this
    gate is built on refusing to guess which one it is looking at. A present
    but unparseable owed file is a BROKEN claim (also refused, worded as such,
    never silently treated as empty).

    The same rule governs the budget half of the check: a MISSING loop-state
    file is also NO-DATA and also refuses, for the identical reason (absence
    of the file that would record spend is never evidence that the budget is
    clear). A present state file is handed to _budget_problem, which applies
    the same refuses-closed rule to an empty state object, a missing budget
    block, or a non-numeric cap or spent value. This function never prints or
    implies a budget verdict it did not actually measure.
    """
    problems = []
    if not os.path.isfile(owed_path):
        problems.append("NO-DATA: owed-items file %s is missing; %s"
                        % (owed_path, OWED_FILE_DEFAULT_NOTE))
    else:
        try:
            with open(owed_path) as f:
                owed = json.load(f)
        except (OSError, ValueError) as e:
            problems.append("owed-items file %s is not readable as JSON (%s); a receipt that "
                            "cannot be read is a broken claim, not an absent one"
                            % (owed_path, type(e).__name__))
            owed = _UNREAD
        if owed is not _UNREAD and not isinstance(owed, dict):
            problems.append("owed-items file %s is a JSON %s, not an object"
                            % (owed_path, type(owed).__name__))
        elif owed is not _UNREAD:
            blocking, parse_problems = _blocking_owed_items(owed)
            problems.extend(parse_problems)
            problems.extend(blocking)

    if not os.path.isfile(state_path):
        problems.append("NO-DATA: loop-state file %s is missing; budget spend cannot be "
                        "confirmed from it, and being unable to confirm a budget is not "
                        "exhausted is not evidence that it is safe to open the loop, so "
                        "loop-open is refused" % state_path)
    else:
        try:
            with open(state_path) as f:
                state = json.load(f)
        except (OSError, ValueError) as e:
            problems.append("loop-state file %s is not readable as JSON (%s); a receipt that "
                            "cannot be read is a broken claim, not an absent one"
                            % (state_path, type(e).__name__))
            state = _UNREAD
        if state is not _UNREAD and not isinstance(state, dict):
            problems.append("loop-state file %s is a JSON %s, not an object"
                            % (state_path, type(state).__name__))
        elif state is not _UNREAD:
            bp = _budget_problem(state)
            if bp:
                problems.append(bp)
    return problems


USAGE = """usage: sbe_dispatch.py brief (--file PATH | --json -)
       sbe_dispatch.py loop-open --state PATH --owed PATH

brief       validate a dispatch brief (a JSON object) against the required
            fields, the model_tier capability-profile rule and the T1/T2/T3
            agent-count scaling law. --file PATH reads the brief from PATH;
            --json - reads it from stdin.
loop-open   refuse opening new work when the owed-items file (--owed) names
            unfinished, undeferred work, or the loop-state file (--state)
            records the declared budget already spent, or is missing, or
            records no measurable budget (a budget that cannot be measured
            is NO-DATA and refuses exactly as a missing owed file does).

Exit codes: 0 pass, 1 refused (every problem is named), 2 usage error."""


def _read_json_arg(args):
    """(parsed brief, error) from a `brief` subcommand's --file/--json flags.

    error is a usage-error sentence (exit 2) when the brief could not even be
    located and read; a brief that was read but fails validation is not an
    error here, it is the ordinary refused case validate_brief reports.
    """
    file_path = None
    json_flag = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--file":
            if i + 1 >= len(args):
                return None, "--file needs a PATH argument"
            file_path = args[i + 1]
            i += 2
        elif a == "--json":
            if i + 1 >= len(args):
                return None, "--json needs an argument (a PATH, or - for stdin)"
            json_flag = args[i + 1]
            i += 2
        else:
            return None, "%r is not a recognized option for brief" % (a,)
    if file_path and json_flag:
        return None, "pass one of --file PATH or --json PATH/-, not both"
    if not file_path and not json_flag:
        return None, "brief needs --file PATH or --json (- for stdin, or a PATH)"
    if file_path:
        source = file_path
        try:
            with open(file_path) as f:
                text = f.read()
        except OSError as e:
            return None, "cannot read %s (%s)" % (file_path, type(e).__name__)
    elif json_flag == "-":
        source = "stdin"
        text = sys.stdin.read()
    else:
        source = json_flag
        try:
            with open(json_flag) as f:
                text = f.read()
        except OSError as e:
            return None, "cannot read %s (%s)" % (json_flag, type(e).__name__)
    try:
        return json.loads(text), None
    except ValueError as e:
        return None, "%s does not parse as JSON (%s)" % (source, type(e).__name__)


def _say_usage():
    # Mirrors sbe_gate.py's help path: the usage text goes out line by line
    # through the say() choke point, because the verdict-source lint reads
    # _say_usage() as an unflattened report line (a NAME is not a bare string
    # constant to a source lint), and it is right: one choke point, no
    # exceptions, is the whole rule.
    for usage_line in USAGE.splitlines():
        say(usage_line)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if any(a in ("-h", "--help") for a in argv):
        _say_usage()
        return 0
    if not argv:
        _say_usage()
        say("sbe_dispatch: a subcommand is required (brief or loop-open).")
        return 2
    cmd, rest = argv[0], argv[1:]

    if cmd == "brief":
        brief, err = _read_json_arg(rest)
        if err:
            _say_usage()
            say("sbe_dispatch: %s" % err)
            return 2
        problems = validate_brief(brief)
        if problems:
            say("sbe_dispatch: brief REFUSED (%d problem(s)):" % len(problems))
            for p in problems:
                say("  - %s" % p)
            return 1
        say("sbe_dispatch: brief PASS: objective, model_tier, token_budget, files, "
            "done_check and tier are all present and valid, and the agent-count scaling "
            "check for tier %s passed." % brief.get("tier"))
        return 0

    if cmd == "loop-open":
        state_path = None
        owed_path = None
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--state":
                if i + 1 >= len(rest):
                    _say_usage()
                    say("sbe_dispatch: --state needs a PATH argument")
                    return 2
                state_path = rest[i + 1]
                i += 2
            elif a == "--owed":
                if i + 1 >= len(rest):
                    _say_usage()
                    say("sbe_dispatch: --owed needs a PATH argument")
                    return 2
                owed_path = rest[i + 1]
                i += 2
            else:
                _say_usage()
                say("sbe_dispatch: %r is not a recognized option for loop-open" % (a,))
                return 2
        if not state_path or not owed_path:
            _say_usage()
            say("sbe_dispatch: loop-open needs both --state PATH and --owed PATH")
            return 2
        problems = check_loop_open(state_path, owed_path)
        if problems:
            say("sbe_dispatch: loop-open REFUSED (%d problem(s)):" % len(problems))
            for p in problems:
                say("  - %s" % p)
            return 1
        say("sbe_dispatch: loop-open PASS: no unfinished, undeferred owed item was found, "
            "and the recorded budget spend was measured against its recorded cap and is "
            "not exhausted.")
        return 0

    _say_usage()
    say("sbe_dispatch: %r is not a recognized subcommand (brief, loop-open)." % (cmd,))
    return 2


if __name__ == "__main__":
    sys.exit(main())
