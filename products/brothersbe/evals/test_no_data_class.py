#!/usr/bin/env python3
"""The honesty meta-test: no check in this project may report PASS over evidence
it never examined.

This exists because fixing the instances did not fix the CLASS, twice. Six named
cases of "a PASS whose evidence asserts something the tool never inspected" were
fixed by hand; a re-review found the identical defect alive in four more places
inside the same two files; a third review found it alive in six more. A seventh
hand fix would have been the same mistake a seventh time.

The reason it kept surviving is precise and worth stating, because it is the
whole design of this file. Every earlier round modelled absent evidence as an
absent FILE. The surviving holes were absent VALUES. A manifest that exists,
parses, carries every required key and holds "" in all of them sailed through,
and the check then printed "zero drift" over two empty strings. So this test does
not enumerate shapes of missing files. It takes the WORKING example each check
declares and hollows it out, mechanically, in every way a value can be empty:

  a  a completely empty directory                      -> NO-DATA, never PASS
  a2 a dossier-shaped directory with a zero-byte anchor, so that a check whose
     tool has a no-target fallback is genuinely INVOKED at least once
  b  evidence that exists and declares zero items      -> the declared empty state
  c  valid JSON of the wrong shape (list, string, ...) -> FAIL, a verdict line is
                                                          still printed, and no
                                                          traceback escapes
  d  malformed JSON                                    -> FAIL
  full the declared full_fixture                       -> PASS, which is what
                                                          proves the check ran at
                                                          all and that the fixture
                                                          is a real positive
  e  every value in it replaced by ""                  -> never PASS
  f  every value replaced by whitespace                -> never PASS
  g  every value replaced by null                      -> never PASS
  e/f/g are also applied to every SUBTREE and every single LEAF of the fixture,
     one at a time. Emptying the whole receipt is the easy case and no shipped
     defect had that shape: the live ones were a single emptied pair inside an
     otherwise complete receipt (`rerun.primary` and `rerun.secondary`, or
     `row_counts.before` and `row_counts.after_reverse`), which is exactly the
     subtree sweep, and a single whitespace value (`rehearsal_run_id: " "`),
     which is exactly the leaf sweep.
  h  every item in a list replaced by an empty object  -> never PASS
  j  every list emptied                                -> never PASS
  i  the files exist with the right names and are zero bytes -> never PASS
  s  for a markdown artifact: every heading kept, the body under one heading
     dropped, one heading at a time. This is the text-shaped version of "the keys
     are all there and the values are all empty".
  t  every BOOLEAN leaf replaced by a well-formed word meaning the OPPOSITE
     ("no"/"false" for a true, "yes"/"true" for a false) -> never PASS. The one
     axis here that is not emptiness at all: see OPPOSITE_WORDS below.

Booleans are preserved by the whole-fixture and subtree sweeps and emptied only
by the leaf sweep, on purpose. In a receipt a boolean is the CLAIM ("the reverse
ran against a restore") and a string or a number is the EVIDENCE for it. Emptying
the evidence while leaving the claim standing is the precise shape a fabricated
receipt has, and it is the shape three rounds of review kept finding.

Registries are DISCOVERED, not listed. Every Python module anywhere under
tools/ is imported, at any depth, and every module-level dict of Checks in it is
a registry. A registry this test does not know how to invoke is a FAILURE, not a
skip, so a fourth tool added next year, or a package added under an existing one,
cannot be silently uncovered. A source-level lint additionally requires that
every function anywhere in tools/ that can return the literal verdict "PASS" is
registered in one of those registries, so a verdict-producing code path cannot
live outside the walk.

Run: python3 evals/test_no_data_class.py
     python3 evals/test_no_data_class.py --tools <dir>
     python3 evals/test_no_data_class.py --quiet     (summary and failures only)

The second form runs the SAME scenarios against a different copy of the tools,
which is how the before-list was measured against the tree that shipped these
defects: the scenarios and the expected verdicts are data held here, the code
under test is whatever --tools points at. A test that can only be run against the
fixed code proves nothing about what it caught.
"""
import ast, copy, datetime, json, os, random, stat, subprocess, sys, tempfile
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.abspath(os.path.join(HERE, "..", "tools"))
sys.path.insert(0, TOOLS_DIR)

# The registries (the declarations) always come from this working tree. The code
# actually invoked can be pointed elsewhere, which is how the before-list is
# measured against the tree that shipped the defects.
RUN_DIR = TOOLS_DIR
if "--tools" in sys.argv:
    RUN_DIR = os.path.abspath(sys.argv[sys.argv.index("--tools") + 1])
QUIET = "--quiet" in sys.argv
# Seeded generative mode: each `--seed N` adds one random composition sweep on
# top of the fixed one (see seeded_cases). No seeds means no random scenarios,
# so the default run and its printed counts are byte-stable.
SEEDS = []
for _i, _a in enumerate(sys.argv):
    if _a == "--seed" and _i + 1 < len(sys.argv):
        SEEDS.append(int(sys.argv[_i + 1]))

from sbe_checks import Check, Pruner  # noqa: E402

_telemetry = SourceFileLoader("sbe_telemetry",
                              os.path.join(TOOLS_DIR, "sbe_telemetry.py")).load_module()

# The evidence sentence sbe_design.py prints for a check it did NOT open a file
# for, because no dossier was found. It is matched here so a scenario cannot be
# counted as covering a check that never ran. The phrase is load-bearing: see the
# comment beside it in tools/sbe_design.py.
NOT_INVOKED = "so this check opened no file"

EMPTY_VALUES = [("e", "empty-string", ""), ("f", "whitespace", "   "), ("g", "null", None)]
# Round four of the same defect, as a scenario shape. The value is PRESENT, it is
# NON-EMPTY, and it records nothing: `snapshot_id: "TODO"` with both rerun values
# `"pending"` produced "1 figure(s) each with a pinned, independently re-derived,
# zero-drift check" and `--strict` exit 0. `stated()` is not a test for this and
# nothing anywhere was asking "is this field an ANSWER".
#
# Three tokens, not the whole vacuity list, and each is here for a different
# reason a substitution can go wrong: TODO is upper case (so a case-sensitive
# membership test fails this scenario), n/a carries punctuation (so a naive
# isalnum-style normalisation fails it), and "-" is a single character that a
# length check would wave through. Every token in sbe_checks.VACUOUS_VALUES is
# covered by construction, because they all resolve through one predicate; these
# three prove the predicate is actually being CALLED, which is what a scenario
# can prove and a list cannot.
VACUOUS_VALUES = [("v", "todo", "TODO"), ("v", "n-a", "n/a"), ("v", "dash", "-"),
                  # The copula remnant: "is still unknown" is not blank, is not
                  # a whole-string token, and the one-shot copula stripper left
                  # "still unknown", which is not in a list that holds
                  # "unknown". This token proves the vacuity test re-reads its
                  # own reduction's output to a fixpoint.
                  ("v", "copula-remnant", "is still unknown"),
                  # The invisible-wearing placeholder: a zero-width space, a
                  # soft hyphen, a fullwidth spelling. Each renders as TODO on
                  # every screen, and each defeated a normalization that
                  # stripped a hand-typed five-code-point list instead of the
                  # class: the value was present, non-empty, and said nothing
                  # a reader can see. These three prove the vacuity test reads
                  # the text as it renders, not as it is encoded.
                  ("v", "todo-zero-width", "TODO\u200b"),
                  ("v", "todo-soft-hyphen", "T\u00adODO"),
                  ("v", "todo-fullwidth", "\uff34\uff2f\uff24\uff2f"),
                  # The small-capital spelling: U+1D1B U+1D0F U+1D05 U+1D0F
                  # renders as TODO and has neither a Unicode decomposition nor
                  # a confusable-table row, so it survived every reduction the
                  # vacuity test ran: the sixth disguise of one placeholder,
                  # and the unlisted class member the round-10 law asks each
                  # scenario list to probe. The Unicode-name fold (a rule over
                  # the character database, not a table) reads it as TODO; a
                  # Latin value no fold reduces at all is refused by the
                  # residue rule the same way. TBD in the same block proves the
                  # member nobody listed also folds through the rule.
                  ("v", "todo-small-caps", "\u1d1b\u1d0f\u1d05\u1d0f"),
                  ("v", "tbd-small-caps", "\u1d1b\u0299\u1d05"),
                  # The shape-wearing placeholder: `[TBD]` is none of the
                  # tokens and cleared the money gate with its strongest
                  # sentence. The vacuity test now folds SHAPES (wrapping,
                  # trailing owners, dotted initialisms) to a fixpoint, and
                  # this token proves the folding is CALLED everywhere, with a
                  # member the fix's own examples never listed per leaf.
                  ("v", "bracketed-tbd", "[TBD]")]

# Values that are numbers and still record no measurement. json.load accepts a
# bare NaN, `NaN <= 0` is False, and a hand-rolled duration check accepted one
# while its sentence asserted "a nonzero duration": the evidence sentence was
# false even where the verdict logic was arguably defensible. Swept like the
# empty values, over every leaf and subtree, but only through JSON-shaped
# fixtures: NaN has no text form a markdown artifact would carry.
# NaN AND Infinity: json.load accepts both bare, and the axis shipped
# half-declared, with NaN swept here and Infinity swept nowhere, so a
# `cache_read: Infinity` bought "1/1 sessions >= 90% warm-read" (the ratio is
# NaN and `NaN < floor` is False) while the string "inf" was correctly refused
# one call site over. A value that is a float and not a measurement is one
# class, and the sweep now carries both members.
NOT_MEASUREMENTS = [("m", "nan", float("nan")), ("m", "infinity", float("inf"))]

# Round six, and the first axis about ORDER rather than about content. Every
# sweep above replaces a value with one that says nothing WHEN READ RAW. These
# replace it with one that says nothing only AFTER the tool reduces it, which is
# the shape the whole sweep was blind to because the tools tested for vacuity
# first and reduced afterwards. `second_derivation: "#"` is not blank, is not a
# vacuity token, survives answered(), and folds to the empty string, and the gate
# then compared "" against a real query, found them different, and printed "a
# second derivation whose text differs beyond case, whitespace and comments,
# re-run to zero drift" over a derivation that computes nothing. Every reduction
# this project performs (strip comments, strip a currency symbol, parse a number)
# can manufacture the same hole, so the order is now a scenario and not a habit.
#
# These carry no letter and no digit, so they are a non-answer under any reading
# and can be swept per leaf. The word-bearing case is below, and can only be
# swept whole, because "-- rerun by hand" is a perfectly good LABEL and only a
# derivation-shaped field reduces it to nothing.
REDUCES_TO_NOTHING = [("n", "hash-comment", "#"), ("n", "block-comment", "/* */"),
                      ("n", "punctuation", ";")]
# Applied to the whole fixture at once, for the same reason.
COMMENT_ONLY = [("n", "sql-comment-with-words", "-- rerun on 2026-07-26 by hand, same query"),
                ("n", "not-a-measurement", "inf"),
                # A number where text belongs: `"query": 0` skipped the whole
                # reduce-then-test fix, because the reduction was applied only
                # to strings, and two integers bought "a second derivation whose
                # text differs beyond case, whitespace and comments". Whole
                # fixture only: 0 is an honest answer for many single leaves (a
                # zero exit code), so a per-leaf sweep of it would assert
                # falsehoods.
                ("n", "number-in-a-text-field", 0)]

# Round five, and the first one that is not about emptiness at all. Every sweep
# above replaces a value with a value that says NOTHING. This one replaces a
# boolean with a well-formed word that says the OPPOSITE, which is the shape the
# whole sweep was blind to: the intake asks its questions as "(y/n)", read the
# answers for Python truthiness, and so an answer of "no" to "is this reversible
# in under an hour" computed the LOWEST tier, T0 required no artifact, every
# design check reported NO-DATA, and the re-derivation that exists to catch a
# hand-edited tier recomputed the same wrong tier from the same unread string and
# agreed with the file. Nothing in this test could have caught that, because
# every scenario it generated was a form of blank.
#
# The rule: a boolean in a worked positive example is a CLAIM. Replacing it with
# a word meaning the opposite of that claim must not leave the verdict at PASS.
# The same-meaning direction is deliberately NOT swept here: the gates hold that
# only the JSON boolean true is the claim (SKILL.md L14), so "yes" correctly
# fails there while it correctly reads as a yes in the intake, and one sweep
# cannot assert both. That direction is covered by named cases in
# evals/run_evals.py instead.
OPPOSITE_WORDS = {True: ("no", "false"), False: ("yes", "true")}


# ---------------------------------------------------------------------------
# Registry discovery
# ---------------------------------------------------------------------------

def tool_sources():
    """Every Python file ANYWHERE under tools/, as a path relative to it.

    NOT `sbe_*.py`. The prefix filter was a hole and it was proved: a registry
    shipped as `tools/quality.py` holding a check whose body was
    `return "PASS", "quality is fine (examined nothing at all)"` was invisible
    to the registry walk AND to the source lint, and this file printed
    "20 checks discovered ... 0 failure(s)" over twenty-one checks and exited 0.
    Discovery must not depend on what somebody named a file.

    And then it depended on what somebody named a DIRECTORY. `os.listdir` is one
    level deep, so the same shadow check moved into `tools/quality/extra.py` was
    invisible to both mechanisms again, while the summary line still read
    "20 checks discovered ... 0 failure(s)" over twenty-one. Adding a package is
    the ordinary way a tools directory grows, and the printed coverage count is
    exactly what made the gap silent. This walks.
    """
    out = []
    pruner = Pruner()
    # onerror: os.walk swallows a refused directory by default, so one chmod 000
    # under tools/ dropped a module from the walk, the count fell from 8 to 7,
    # and the guarantee sentence returned to "0 failure(s)" over a tree this test
    # could not see into. A directory the walk cannot enter is a failure below.
    for dp, dns, fns in os.walk(TOOLS_DIR, onerror=pruner.onerror):
        dns[:] = pruner(dp, dns)
        for fn in fns:
            if fn.endswith(".py"):
                rel = os.path.relpath(os.path.join(dp, fn), TOOLS_DIR)
                # A .py inside a __pycache__ directory is not source: the
                # interpreter OWNS that name (it is where CPython writes
                # bytecode), so this is the one directory-name rule that is a
                # language contract rather than a user convention. Importing
                # one EXECUTED it: a planted module under tools/__pycache__/
                # ran at suite start, was counted in "N module(s)", and the
                # install verifier's exclusion list kept it invisible on disk.
                # It is refused here and named as a failure in main(), never
                # imported, never counted as coverage.
                if "__pycache__" in rel.split(os.sep):
                    PLANTED_SOURCE.append(rel)
                    continue
                # A module that is not a REGULAR file is refused by name and
                # never imported. `find -type f` never returns a symlink, so a
                # planted tools/backdoor.py pointing at code outside the tree
                # was invisible to the install verifier, and this suite
                # imported and EXECUTED it while the module count moved with
                # no comment. The rule covers the members nobody has planted
                # yet: symlink, FIFO, socket, device are all "not a regular
                # file", named loudly, never walked as source.
                full = os.path.join(dp, fn)
                try:
                    regular = not os.path.islink(full) and stat.S_ISREG(os.lstat(full).st_mode)
                except OSError:
                    regular = False
                if not regular:
                    NONREGULAR_SOURCE.append(rel)
                    continue
                out.append(rel)
    hidden, uninspected = pruner.hidden(lambda f: f.endswith(".py"))
    if hidden or uninspected:
        # A pruned directory holding Python, or one the inspection budget never
        # reached, is a directory this test's coverage claim does not cover, and
        # the claim is printed on every run. Naming it in the sources list would
        # import somebody else's code; naming it here is how the count stops
        # being a completeness sentence over a truncated set. main() turns this
        # into a failure.
        PRUNED_WITH_SOURCE.extend(hidden)
        PRUNED_WITH_SOURCE.extend(uninspected)
    DENIED_DIRS.extend(pruner.denied)
    return sorted(out)


PRUNED_WITH_SOURCE = []
DENIED_DIRS = []
# Python files found inside a bytecode-cache directory: refused, never
# imported, reported as failures by name. See tool_sources().
PLANTED_SOURCE = []
# Python files under tools/ that are not regular files (a symlink, FIFO,
# socket, device): refused, never imported, reported as failures by name.
NONREGULAR_SOURCE = []
# Scenarios a declared exemption matched, printed in the summary line so a
# waiver cannot hide inside an unchanged scenario total.
WAIVED = [0]
# Raised when checks are added, never lowered: the count of discovered checks
# may only grow, and a shrink is a failure this file reports itself rather than
# leaving to a doc guard elsewhere.
FLOOR_CHECKS = 21
# The exemption budget, counted in SCENARIOS and not in keys, because one key
# waives every scenario sharing its sid prefix: the three-key cap in the Check
# constructor was satisfied while a single JSON-leaf key waived that leaf's
# whole value sweep (14 scenarios and growing), which is exactly the breadth a
# check needs to hide a real regression on that leaf behind one
# plausible-sounding sentence, with the printed scenario total unchanged. The
# cap sits above the widest honest exemption this suite has ever needed (a
# markdown section, 2 scenarios) and below what any single JSON leaf's sweep
# generates, so a leaf of a receipt cannot be exempted at all: narrow the PASS
# sentence instead. Lowered never, raised only with a reason written here.
WAIVED_SCENARIO_CAP = 8


def load_tool_modules():
    """Import every tool module. An import that fails is a FAILURE, not a skip.

    Returns (modules, failures). A module this test cannot import is a module
    whose registries it cannot walk, which is the same blindness as a registry
    it cannot invoke, so it is reported by name rather than passed over.
    """
    mods, failures = {}, []
    for fn in tool_sources():
        if os.path.basename(fn) == "sbe_checks.py":
            # Reloading it would rebind sbe_checks.Check to a second, unequal
            # class, and every isinstance(x, Check) below would then be false
            # against registries that had already imported the first one. This is
            # the ONE exclusion, it is named, and the file holds no registry: the
            # lint below still reads it, so a check hiding there is still caught.
            continue
        try:
            # The module name carries the subdirectory, so tools/a/x.py and
            # tools/b/x.py are two modules here rather than one shadowing the other.
            modname = fn[:-3].replace(os.sep, ".")
            mods[fn] = SourceFileLoader(modname, os.path.join(TOOLS_DIR, fn)).load_module()
        except Exception as e:
            failures.append("tools/%s could not be imported (%s: %s), so any registry in it went "
                            "unwalked. A module this test cannot reach is a failure, never a skip"
                            % (fn, type(e).__name__, e))
    return mods, failures


def discover_registries(mods):
    """Every module-level dict holding Checks, plus the ones that hold something else.

    A dict with any Check in it is a registry; a registry with a bare function in
    it is a defect, because this test cannot cover what does not declare its
    evidence. Nothing here is written down by hand.
    """
    found, defects = [], []
    for fn, mod in mods.items():
        for attr, val in sorted(vars(mod).items()):
            if attr.startswith("_") or not isinstance(val, dict) or not val:
                continue
            if not any(isinstance(v, Check) for v in val.values()):
                continue
            bad = sorted(k for k, v in val.items() if not isinstance(v, Check))
            if bad:
                defects.append("%s: %s.%s holds %s registered as bare values, not Checks "
                               "declaring their evidence, so this test cannot cover them"
                               % (fn, fn[:-3], attr, ", ".join(bad)))
            found.append((fn, attr, val))
    return found, defects


def _pass_valued_names(tree):
    """Names bound to the string "PASS" anywhere in a module.

    The lint matched the literal spelling only, so `VERDICT_OK = "PASS"` at
    module level and `return VERDICT_OK, "shadow gate: examined nothing"` in the
    body walked straight through it, and the run still printed 0 failures. A
    verdict is a verdict whatever it was spelled through.
    """
    names = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        direct = isinstance(value, ast.Constant) and value.value == "PASS"
        # `_ok = lambda: "PASS"` binds a name to the verdict as surely as
        # `_ok = "PASS"` does, and a Lambda is not a FunctionDef so the walk below
        # never inspects one. The name is a verdict name either way.
        if isinstance(value, ast.Lambda) and any(
                isinstance(n, ast.Constant) and _spells_pass(n.value) for n in ast.walk(value)):
            direct = True
        # A container holding the verdict counts too, because the name is then a
        # verdict table and `TABLE["ok"]` is a way of spelling "PASS".
        container = isinstance(value, (ast.Dict, ast.List, ast.Tuple, ast.Set)) and any(
            isinstance(n, ast.Constant) and n.value == "PASS" for n in ast.walk(value))
        if not (direct or container):
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                names.add(t.id)
    return names


def _folded_str(node):
    """The string this expression evaluates to, or None if it is not a constant one.

    Only string constants and `+` between them. `"PA" + "SS"` is deliberate
    obfuscation and weighted lightly, but the lint that claims to find every path
    that can return PASS has to be able to say so, and folding two constants is
    cheaper than arguing about whether anyone would write it.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded_str(node.left), _folded_str(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _spells_pass(text):
    return isinstance(text, str) and text.strip().casefold() == "pass"


def _mentions_pass(node, pass_names):
    """True when the verdict PASS is reachable anywhere inside this expression.

    Deliberately generous. Enumerating node types one at a time is the
    hand-fix-the-instance loop this project exists to break, and it lost five
    times over: an f-string is a JoinedStr and not a Constant, a name bound by
    `from sbe_checks import VERDICTS` is bound by no Assign, a lambda is not a
    FunctionDef, and `"pass".upper()` and `"".join(["PA", "SS"])` are Calls. So
    this asks the opposite question: is there ANY constant, name or call in here
    through which the string could arrive.
    """
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and _spells_pass(n.value):
            return True
        if isinstance(n, ast.Name) and n.id in pass_names:
            return True
        if isinstance(n, ast.Attribute) and n.attr in ("upper", "casefold", "lower", "title",
                                                       "strip", "format", "join", "replace"):
            # A string method applied to something that spells pass in any case.
            if any(isinstance(c, ast.Constant) and _spells_pass(c.value)
                   for c in ast.walk(n)):
                return True
    return False


def _returns_pass(head, pass_names):
    """True when this expression can be the verdict PASS."""
    if head is None:
        return False
    if isinstance(head, ast.Constant) and head.value == "PASS":
        return True
    if isinstance(head, ast.Name) and head.id in pass_names:
        return True
    if _folded_str(head) == "PASS":
        return True
    return _mentions_pass(head, pass_names)


# Functions in tools/ that return a 2-tuple and are NOT verdict producers. The
# lint below is INVERTED: a 2-tuple return whose head this lint cannot prove is
# always one of FAIL or NO-DATA counts as a possible verdict, because proving a
# negative about an expression is the only way to stop playing whack-a-mole with
# spellings of "PASS". Everything genuinely not a verdict is named here with the
# reason, and the list IS printed on every run (under "declared exemptions from
# the verdict-source lint", with the function and line each entry resolved to),
# so the exemption is reviewable rather than a silent gap. The key is
# reconciled, not trusted: an entry naming no function fails, and an entry
# resolving to two functions of that name fails, because the reviewed exemption
# belongs to one body and a second function of the same name would inherit it
# unreviewed.
NOT_A_VERDICT = {
    ("sbe_checks.py", "run_guarded"): "the shared runner; it returns whatever the check it wrapped "
                                      "returned, and every check it can wrap is itself registered",
    ("sbe_checks.py", "distinct"): "returns (items, duplicate count), not a verdict",
    ("sbe_checks.py", "hidden"): "returns (pruned trees holding wanted files, trees the inspection "
                                 "budget left uninspected), not a verdict",
    ("sbe_checks.py", "answered_as"): "returns a reduced value, never a verdict",
    ("sbe_checks.py", "_char_reading"): "returns (rendering kind, reading) for one character, "
                                        "not a verdict",
    ("sbe_checks.py", "glob_with_denials"): "returns (glob hits, directories it could not enter), "
                                            "not a verdict",
    ("test_sbe_fieldbook.py", "_run"): "runs the book generator in a subprocess and returns "
                                      "(the interpreter exit code, its combined stdout and "
                                      "stderr), which is process output and never a verdict",
    ("sbe_profile.py", "read_text"): "returns (file text, read problem), not a verdict",
    ("sbe_profile.py", "_pick"): "returns (table header, rows) for the first table whose header "
                                 "matches, not a verdict",
    ("sbe_profile.py", "core_rows"): "returns (routing-table rows, read problem), not a verdict",
    ("sbe_profile.py", "module_rows"): "returns (module-table rows, read problem), not a verdict",
    ("sbe_profile.py", "module_ids"): "returns (module ids, read problem), not a verdict",
    ("sbe_profile.py", "load_when"): "returns (the LOAD WHEN trigger line, read problem), "
                                     "not a verdict",
    ("sbe_gate.py", "load_receipt"): "returns (parsed receipt, parse error), not a verdict",
    ("sbe_dispatch.py", "_blocking_owed_items"): "returns (blocking owed items, parse "
                                                 "problems), not a verdict",
    ("sbe_dispatch.py", "_read_json_arg"): "returns (parsed JSON value, read problem), "
                                           "not a verdict",
    ("sbe_gate.py", "_items"): "returns (item list, why it is empty), not a verdict",
    ("sbe_gate.py", "find"): "returns (paths found, scope and pruning note), not a verdict",
    ("sbe_gate.py", "git_trailers"): "returns (commit body, signature state, identities, "
                                     "author/committer/signer meta), not a verdict",
    ("sbe_gate.py", "_identity_parts"): "returns (canonical emails, ordered name word-tuples), "
                                        "not a verdict",
    ("sbe_gate.py", "_rendered_identity_parts"): "returns (canonical emails, ordered name "
                                                 "word-tuples) with unreadable characters kept, "
                                                 "not a verdict",
    ("sbe_gate.py", "_parts"): "the shared identity parser behind _identity_parts and "
                               "_rendered_identity_parts; returns parts, not a verdict",
    ("sbe_gate.py", "_canonical_email"): "returns one canonicalized mailbox string, not a verdict",
    ("sbe_gate.py", "_wrote"): "gate_approval's local collector; returns (emails, names) for a "
                               "list of identity strings, not a verdict",
    ("sbe_gate.py", "_same"): "gate_approval's local sameness probe; returns (shared emails, "
                              "shared name), not a verdict",
    ("sbe_checks.py", "_script_family"): "returns the Unicode script family of one character, "
                                         "not a verdict",
    ("sbe_design.py", "_entity_bullets"): "returns (the entity set, whether a heading declared it), "
                                          "not a verdict",
    ("sbe_design.py", "_rejected_alternatives"): "returns (collected alternatives, chosen table "
                                                 "rows), not a verdict",
    ("sbe_design.py", "_table_alternatives"): "returns (table-row alternatives, chosen row "
                                              "count), not a verdict",
    ("sbe_design.py", "_coherence_problem"): "returns a problem string, or an (unmeasured, why) "
                                             "pair naming a script limit; never a verdict",
    ("sbe_design.py", "_behaviour_rows"): "returns (parsed rows, malformed rows) from the fixed "
                                          "ID | Starting point | Trigger | Required outcome | "
                                          "Proof table, not a verdict",
    ("sbe_testkit.py", "_split_blocks"): "returns (CASE and CHARTER blocks, headers that carried "
                                         "no recognisable id), not a verdict",
    ("sbe_testkit.py", "_collect_findings"): "returns (findings with text, blocks this could not "
                                             "read), not a verdict",
    ("sbe_qc_scaffold.py", "render_cases"): "returns (the rendered lines of the `cases` command, "
                                            "its exit code), not a verdict",
    # The one entry below is not simply a mis-shaped pair, and it is said plainly
    # here rather than filed among the others as though it were. add_row DOES
    # return the literal word PASS. It is the result of a WRITE (the row reached
    # disk), consumed at this tool's own CLI for one stdout line and the process
    # exit code, and it reaches no gate or score registry, so nothing parses it
    # as a report verdict and no report can inherit it. It is exempt on that
    # basis and on no other.
    #
    # The naming collision is a real wart rather than a quibble: this project
    # reserves PASS, FAIL and NO-DATA for gate verdicts, and an operation result
    # borrowing that vocabulary is how a reader comes to believe a check ran.
    # Renaming the pair to a written/refused shape is the correct repair. It is
    # deliberately NOT done here, because it changes a shipped command's output
    # and this change is already the one getting the battery green. Recorded as
    # owed rather than quietly accepted.
    ("sbe_qc_scaffold.py", "add_row"): "returns (the result of appending one row to the behaviour "
                                       "table, a note), used only for this CLI's own stdout line "
                                       "and exit code; registered in no gate or score registry, "
                                       "so no report line can carry it",
    ("sbe_intake.py", "read_answers"): "returns (answers read, answers refused), not a verdict",
    ("sbe_score.py", "_rel"): "returns a path",
    ("sbe_decide.py", "load_table"): "returns (parsed table, load error), not a verdict",
    ("sbe_telemetry.py", "redact"): "returns (masked text, how many masks), not a verdict",
    ("sbe_telemetry.py", "read_jsonl_counted"): "returns (parsed rows, unparseable lines), "
                                                "not a verdict",
    ("sbe_telemetry.py", "prediction_counts"): "returns a count table",
    ("test_sbe_handover.py", "_receipt"): "LT-301's own fixture helper; returns (exit code, "
                                          "combined stdout+stderr) from running `sbe evidence "
                                          "run`, not a verdict",
    ("test_sbe_handover.py", "_registry"): "LT-301's own fixture helper; returns (registry "
                                           "file path, parsed registry dict), not a verdict",
    ("test_sbe_evidence.py", "_run_sbe"): "this suite's own fixture helper; returns (exit code, "
                                          "combined stdout+stderr) from running the CLI in a "
                                          "subprocess, not a verdict",
    ("test_sbe_evidence.py", "_run_sbe_at"): "the same fixture helper in "
                                             "TestReceiptFreshnessAcrossCommitsAndScopes, which "
                                             "adds a cwd override; returns (exit code, combined "
                                             "stdout+stderr) from running the CLI in a "
                                             "subprocess, not a verdict. It was named _run_sbe "
                                             "too until this reconciliation, and one exemption "
                                             "cannot be reviewed for two bodies, so it carries "
                                             "its own name and its own entry",
    ("install.py", "check_origin_url"): "validates a remote.origin.url read out of git config "
                                        "and returns (ok, refusal reason or None), a boolean and "
                                        "an operator sentence, never a verdict string",
    ("sbe_repo_scope.py", "installation_is_scoped"): "returns (scoped, diagnostic or None) for "
                                                     "the scope marker file, a boolean and a "
                                                     "reason the marker could not be read, not a "
                                                     "verdict",
    ("test_sbe_doctor_wiring.py", "_wiring_under"): "this suite's own fixture helper; runs `sbe "
                                                    "doctor --json` in a subprocess and returns "
                                                    "(the hooks-wiring check object parsed out of "
                                                    "the doctor's own JSON, stdout plus stderr), "
                                                    "so the verdict inside that object is the "
                                                    "doctor's and this helper neither forms nor "
                                                    "alters one; the same shape as "
                                                    "_doctor_json_with_installed_plugins_record "
                                                    "beside it",
    ("test_sbe_bash_guard.py", "_make_other_project"): "this suite's own fixture helper; "
                                                       "returns (the standin other project's "
                                                       "root path, the tool script path "
                                                       "created inside it), not a verdict",
    ("sbe_bash_write_guard.py", "_exec_target_raw"): "returns (the raw path a simple command "
                                                     "executes, the word immediately after it, "
                                                     "for a CLI tool's subcommand), not a "
                                                     "verdict; feeds the cross-project execution "
                                                     "check's read-only-verb exemption, amendment "
                                                     "2026-08-29",
    ("sbe_passport.py", "read_deposit"): "returns (the producer deposit or None, a note naming "
                                         "whether it was absent, unreadable or read), not a "
                                         "verdict; the same shape as sbe_gate.py's load_receipt",
    ("sbe_passport.py", "read_receipt"): "returns (parsed receipt, why it could not be read), "
                                         "not a verdict",
    ("sbe_passport.py", "unbound_receipts"): "returns (lines for receipts no task claims, the "
                                             "commits they bind to), not a verdict",
    ("sbe_passport.py", "build_fields"): "returns (the five passport fields, the deposit note). "
                                         "A field's state is CARRIED or NO-DATA, which is a "
                                         "statement about what the seam carries and never a "
                                         "verdict about a change; this tool reports and cannot "
                                         "block, so it has no PASS to give",
    ("sbe_fence_hook.py", "_plugin_pretooluse_commands"): "returns (PreToolUse hook command "
                                          "strings collected from every enabled plugin's own "
                                          "cached hooks/hooks.json, whether installed_plugins.json "
                                          "itself could be read), not a verdict; the companion "
                                          "detection this feeds (detect_companion) never treats a "
                                          "collected command list as PASS/FAIL/NO-DATA on its own, "
                                          "only whether it contains the companion's hook basename",
    ("sbe_autosave.py", "_git"): "returns (git's exit code, its stdout) and never raises; a "
                                 "git that cannot run at all reads as (127, ''), not a "
                                 "verdict",
    ("sbe_design.py", "_owner_expiry"): "returns (suffix to append to the exemption reason, "
                                        "problem), two strings for the report line; the "
                                        "report-side mirror of sbe_gate.py::_owner_expiry, "
                                        "not a verdict",
    ("sbe_gate.py", "_owner_expiry"): "returns (suffix to append to the exemption reason, "
                                      "problem), two strings for the waiver line; the "
                                      "load-bearing copy sbe_design.py mirrors, not a verdict",
    ("sbe_gate.py", "_git_version"): "returns the (major, minor) of the git on PATH as two "
                                     "ints, or None when it cannot be read; version digits, "
                                     "not a verdict",
    ("test_sbe_bbstatus.py", "__call__"): "the recording fake transport; returns the (HTTP "
                                          "status, error) pair it was constructed with, so "
                                          "the test controls both halves, not a verdict",
    ("sbe_gatelock.py", "battery_conflict"): "returns (deny_reason_or_None, notes): the fence "
                                             "decision the two battery hooks share, where the "
                                             "first element is a refusal string or None, never "
                                             "a PASS/FAIL/NO-DATA verdict",
    ("sbe_gatelock.py", "marker_state"): "returns (state, holder) where state is one of "
                                         "absent/live/stale/unreadable: a battery marker's "
                                         "liveness, not a gate verdict",
    ("test_sbe_discover.py", "run"): "this suite's own fixture helper; returns (exit code, "
                                     "stdout) from running the discover tool in a subprocess, "
                                     "not a verdict",
    ("test_sbe_design_fingerprint.py", "run_check"): "this suite's own fixture helper; returns "
                                                     "(exit code, stdout plus stderr) from "
                                                     "running sbe_design behaviour-check in a "
                                                     "subprocess, so the verdict word the test "
                                                     "then reads out of that text is the tool's, "
                                                     "never this helper's",
    ("test_sbe_design_fingerprint.py", "run_accept"): "the same helper for behaviour-accept; "
                                                      "returns (exit code, stdout plus stderr) "
                                                      "from the subprocess, not a verdict",
    ("test_sbe_doctor_wiring.py", "_doctor_json_with_installed_plugins_record"):
        "this suite's own fixture helper; runs `sbe doctor --json` in a subprocess against a "
        "fixture installed-plugins record and returns (the hooks-wiring check object parsed "
        "out of the doctor's own JSON, stdout plus stderr), so the verdict inside that object "
        "is the doctor's and the helper neither forms nor alters one; the same shape as "
        "test_sbe_design_fingerprint.py's run_check, which reads its verdict word out of "
        "captured text",

    # ---------------------------------------------------------------------
    # Added 2026-09-04 (row E82). The hub ran this product's own battery for
    # the first time and 05-honesty-meta read one residual failure: fifteen
    # functions across eight tools/ files "can return the verdict PASS but sit
    # in no registry". Each of the fifteen was READ, one at a time, and none
    # of them forms, alters or forwards a verdict: the population is exactly
    # three shapes, the same three every entry above already belongs to.
    #
    #   (1) a parser or reader returning (value, problems), where the head is
    #       rows, text, counts or a flag and the tail names what could not be
    #       read;
    #   (2) a subprocess or captured-output wrapper returning (exit code,
    #       combined output), where any verdict word in that text is the
    #       TOOL'S, printed by the process under test, never this helper's;
    #   (3) a test fixture builder returning (path, path).
    #
    # This is a widening of a reviewed allowlist, so it is worth saying what
    # was NOT done: no lint rule was loosened, no head was made "provable" by
    # rewriting a helper to satisfy the scanner, and nothing was added because
    # it happened to be red. A function that genuinely emitted a verdict from
    # outside a registry would have been fixed in its own file instead, which
    # is the whole reason this lint is inverted (see pass_returning_functions'
    # docstring: proving the negative is the only way out of whack-a-mole).
    # Each entry below names its return shape, so a reviewer can disprove it
    # by opening one function.
    ("sbe_approval_concentration.py", "git_log_records"):
        "returns (raw `git log` stdout, a short reason string when git could not be read at "
        "all), so the head is commit text and the tail is a read problem, never a verdict",
    ("sbe_approval_concentration.py", "parse_records"):
        "returns (approver identity -> signed commit count, one warning per record the walk "
        "could not use), a tally and its skipped rows, never a verdict",
    ("sbe_design.py", "_rows_under"):
        "the shared markdown table engine; returns (one dict per readable data row, the raw "
        "text of every row whose cell count did not match the header), never a verdict",
    ("sbe_design.py", "_clarify_rows"):
        "the open-questions table through _rows_under, forwarding its (rows, malformed) pair "
        "unchanged; a parser with a second caller, never a verdict",
    ("sbe_design.py", "_revision_log_rows"):
        "the revision-log table through the same _rows_under engine, forwarding (rows, "
        "malformed) unchanged; never a verdict",
    ("sbe_repo_scope.py", "_read_config"):
        "returns (whether the opt-out line is present, a diagnostic naming why an existing "
        "config file could not be read), a configuration flag and its read problem, never a "
        "verdict; the False head on the failure path is the safe direction (hooks stay "
        "active), not a PASS",
    ("sbe_repo_scope.py", "_extract"):
        "returns (cwd, session id) best-effort out of a hook payload that may be a dict, a "
        "raw JSON string, or None; two payload fields, never a verdict",
    ("sbe_session_reconcile.py", "repeat_verdict"):
        "named for the verdict it SUPPRESSES, not one it forms: returns (whether this exact "
        "finding set was already reported in full this session and is still inside the "
        "cooldown, the note explaining that suppression), a boolean and its note. The verdict "
        "itself is formed by the caller from the violations, and is unchanged by this",
    ("sbe_tier_outcome.py", "defect_fix_texts"):
        "returns (every non-placeholder origin.fixes text found under design/, one warning "
        "per dossier whose 00-intake.json could not be parsed), evidence and its read "
        "problems, never a verdict",
    ("test_sbe_bash_guard.py", "_make_product"):
        "this suite's fixture builder; creates a product tree on disk and returns (the "
        "product directory, the tool file inside it), two paths and never a verdict",
    ("test_sbe_behaviour_revision_log.py", "run_check"):
        "runs sbe_design behaviour in a subprocess and returns (the interpreter exit code, "
        "stdout plus stderr), process output; any verdict word in that text is the tool's",
    ("test_sbe_behaviour_revision_log.py", "run_accept"):
        "the same helper for behaviour-accept; returns (exit code, stdout plus stderr) from "
        "the subprocess, not a verdict",
    ("test_sbe_decision_record.py", "record"):
        "runs the decision-record tool in a subprocess and returns (exit code, stdout plus "
        "stderr), process output and never a verdict",
    ("test_sbe_decision_record.py", "load"):
        "reads the two JSON files the tool wrote and returns (the packet, the decision), two "
        "parsed documents, never a verdict",
    ("test_sbe_intake.py", "run_report"):
        "captures stdout around run_budget_report and returns (its return code, the captured "
        "text), process-shaped output; the report's own verdict lines live inside that text "
        "and this helper neither forms nor alters one",
}


# Where each NOT_A_VERDICT entry resolved on the last walk: {(file, fn): [line]}.
# The comment beside the allowlist promised the list "is printed on every run,
# so the exemption is reviewable rather than a silent gap", and nothing printed
# it: the file referenced the name exactly twice, both in code. It is printed
# now, with what it exempted this run, exactly as the empty-value sweep's
# exemptions already are.
EXEMPTION_HITS = {}


def pass_returning_functions(mods=None):
    """Source-level: every function whose 2-tuple return could be the verdict PASS.

    A registry walk covers what is registered. This covers what EXISTS, so a
    verdict-producing path that was never registered is caught by name rather
    than by being quietly outside the walk. Over every .py file in tools/, not
    over `sbe_*.py`: see tool_sources().

    Round six inverted it. The lint used to look FOR the string PASS and five
    ordinary spellings walked past it, including the exact file and body this
    file's own docstring cites as its proof case. It now looks for a
    (verdict, evidence) shape and demands that the head be PROVABLY never PASS:
    a constant FAIL or NO-DATA, or a conditional between them. Anything else,
    including anything it cannot read, is a candidate, and the way out is the
    named allowlist above rather than another node type.

    Returns [(file, function, why it was flagged)]; every NOT_A_VERDICT entry
    this walk resolved is recorded in EXEMPTION_HITS for the caller to print
    and reconcile.
    """
    out = []
    EXEMPTION_HITS.clear()
    for fn in tool_sources():
        # Guarded for the same reason load_tool_modules guards its import: a
        # module this lint cannot parse (a newer syntax on an older Python, an
        # unreadable file) is a module whose verdict paths it cannot see, which
        # is a named failure and never a traceback or a skip.
        try:
            tree = ast.parse(open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read(),
                             filename=fn)
        except (SyntaxError, OSError, ValueError) as e:
            out.append((fn, "<module>", "could not be parsed (%s: %s), so any verdict-producing "
                                        "function in it is invisible to this lint" % (type(e).__name__, e)))
            continue
        pass_names = _pass_valued_names(tree)
        # Functions in this module that return a 2-tuple anywhere, including
        # through a conditional expression: a `return helper()` forwarding one
        # of these forwards its verdict too.
        pair_fns = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Return) and sub.value is not None
                            and _pair_shaped(sub.value)):
                        pair_fns.add(node.name)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if (fn, name) in NOT_A_VERDICT:
                # Recorded, not merely skipped. The exemption key is (file,
                # function name), which is a name the author controls: a
                # SECOND function of the same name in the same file inherited
                # the exemption reviewed for the first one, so a body that is
                # literally `return "PASS", "examined nothing at all"` was
                # invisible to the lint whose whole job is catching an
                # unregistered verdict path. Every resolution is counted here
                # and reconciled by the caller: an entry that names no
                # function is dead and fails, and an entry that resolves more
                # than once is a shadowed key and fails, so the exemption
                # binds to the function that was reviewed and not to a
                # spelling anyone can reuse.
                EXEMPTION_HITS.setdefault((fn, name), []).append(node.lineno)
                continue
            returns = []
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    returns.append(sub.value)
            flagged = None
            for value in returns:
                for head in _verdict_heads(value, pass_names, pair_fns):
                    if head is UNKNOWN_HEAD:
                        flagged = ("returns a (verdict, evidence) pair whose verdict this "
                                   "lint cannot prove is never PASS")
                    elif _returns_pass(head, pass_names):
                        flagged = "returns the verdict PASS"
                        break
                    elif not _provably_not_pass(head, pass_names):
                        flagged = ("returns a (verdict, evidence) pair whose verdict this "
                                   "lint cannot prove is never PASS")
                if flagged == "returns the verdict PASS":
                    break
            if flagged:
                out.append((fn, name, flagged))
    return sorted(set(out))


# The modules whose stdout this suite (and any CI grep, and any human) parses
# for verdict lines. Their printed report IS the interface under test, so every
# print in them is held to the choke-point rule below.
# Files whose prints are REVIEWED as not being report lines. The lint below
# used to run over a three-filename tuple, which was itself the
# list-of-instances failure it polices: sbe_decide.py printed decision
# verdicts through raw interpolation, sat outside the tuple, and a decision
# table's option name forged Recommendation and Alternatives lines. The lint
# now derives its file set from tool_sources(), the same walk that discovers
# registries, so a tool added next year is covered on the day it is added;
# what remains named is the EXCEPTION, each with its reason, reconciled on
# every run (an entry naming a file with no print the lint would flag is dead
# and fails, so the exemption cannot outlive what it excuses).
REPORT_PRINT_EXEMPT = {
    "sbe_intake.py": "an interactive interview: its prompts, echoes and refusals are dialogue "
                     "with the operator, and nothing machine-parses them as verdict lines",
    "sbe_telemetry.py": "operator status lines and hook JSON, never parsed as gate verdicts; "
                        "its ledger writes are data, not report lines",
}


def unflattened_report_prints():
    """Source-level: every print in a report tool that skips the one_line choke point.

    The verdict-forgery channel was closed value by value: evidence fields
    went through one_line(), and the round-10 probe walked past every wrapped
    value by hiding in one nobody wrapped (a dossier DIRECTORY NAME carrying a
    newline wrote a byte-perfect verdict line into the report, and
    verdict_and_evidence, this suite's own reader, believed it). Wrapping
    values is a list of instances. The rule is the class: a report tool may
    print a bare string constant (its own fixed header), or a line that
    passed through one_line()/say() AS A WHOLE after formatting. Any other
    print is an interpolation some future value can climb through, and it is
    flagged here whether or not anyone has typed that value yet.

    Returns ([(file, line, why)], exempt_hits, dead_exemptions): exempt_hits
    maps each REPORT_PRINT_EXEMPT entry to the flagged-print count it excused
    this run, and an entry that excused nothing is returned as dead, so the
    caller fails it rather than letting a review outlive its subject.
    """
    out = []
    exempt_hits = {k: 0 for k in REPORT_PRINT_EXEMPT}
    for fn in tool_sources():
        exempt = os.path.basename(fn) if os.path.basename(fn) in REPORT_PRINT_EXEMPT else ""
        try:
            tree = ast.parse(open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read(),
                             filename=fn)
        except (SyntaxError, OSError, ValueError) as e:
            out.append((fn, 0, "could not be parsed (%s: %s), so its print sites are invisible "
                               "to this lint" % (type(e).__name__, e)))
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print"):
                continue
            ok = False
            if not node.args and not node.keywords:
                ok = True
            elif len(node.args) == 1 and not node.keywords:
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    ok = True
                elif (isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                        and a.func.id in ("one_line", "say")):
                    ok = True
            if not ok:
                if exempt:
                    exempt_hits[exempt] += 1
                    continue
                out.append((fn, node.lineno,
                            "prints a line the one_line() choke point never flattened; a report "
                            "line is one line by construction, so route it through say() (or "
                            "print a bare constant)"))
    dead = sorted(k for k, n in exempt_hits.items() if n == 0)
    return sorted(out), exempt_hits, dead


UNKNOWN_HEAD = object()   # a verdict position this lint cannot read at all


def _pair_shaped(value):
    """True when this return value is a (verdict, evidence)-shaped pair."""
    if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == 2:
        return True
    if isinstance(value, ast.IfExp):
        return _pair_shaped(value.body) or _pair_shaped(value.orelse)
    return False


def _verdict_heads(value, pass_names, pair_fns):
    """Every verdict-position expression this return value can produce.

    The four shapes an audit found the old single-shape reader missing, each an
    ordinary way to write the same return: `return ("PASS", ev) if x else
    (...)` (an IfExp OVER tuples rather than inside one), `return _helper()`
    (forwarding another function's pair), `return ["PASS", ev]` (a list is a
    pair too), and `return (*PAIR,)` (a starred rebuild, unreadable, so it is
    UNKNOWN rather than skipped). Yields UNKNOWN_HEAD for anything it cannot
    read, which the caller flags, because the safe direction is a line in the
    allowlist and never a silent skip.
    """
    if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == 2:
        if any(isinstance(e, ast.Starred) for e in value.elts):
            yield UNKNOWN_HEAD
        else:
            yield value.elts[0]
    elif isinstance(value, (ast.Tuple, ast.List)) and any(
            isinstance(e, ast.Starred) for e in value.elts):
        yield UNKNOWN_HEAD
    elif isinstance(value, ast.IfExp):
        for branch in (value.body, value.orelse):
            for h in _verdict_heads(branch, pass_names, pair_fns):
                yield h
    elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
            and value.func.id in pair_fns:
        yield UNKNOWN_HEAD
    elif isinstance(value, ast.Name) and value.id in pass_names:
        yield value


def _provably_not_pass(head, pass_names):
    """True only when this head is certainly one of the non-PASS verdicts.

    A string constant that is FAIL or NO-DATA, or a conditional or boolean
    expression built out of nothing but those. Everything else is a candidate,
    which is the safe direction: a false alarm costs one line in an allowlist and
    a miss costs the guarantee this whole file exists to make.
    """
    if isinstance(head, ast.Constant):
        return head.value in ("FAIL", "NO-DATA")
    if isinstance(head, (ast.IfExp, ast.BoolOp)):
        parts = ([head.body, head.orelse] if isinstance(head, ast.IfExp) else list(head.values))
        return all(_provably_not_pass(p, pass_names) for p in parts)
    return False


# ---------------------------------------------------------------------------
# Fixture substitution and hollowing
# ---------------------------------------------------------------------------

def subst(value, d):
    # One clock. %(today)s is the UTC date so that it agrees with the UTC
    # timestamp in %(now)s: deriving them from different clocks made a fixture
    # fail for nine hours a day in one timezone and pass in another.
    utc = datetime.datetime.now(datetime.timezone.utc)
    table = {"dir": d, "now": utc.isoformat().replace("+00:00", "Z"),
             "today": utc.date().isoformat()}
    if isinstance(value, str):
        for k, v in table.items():
            value = value.replace("%(" + k + ")s", v)
        return value
    if isinstance(value, dict):
        return {subst(k, d): subst(v, d) for k, v in value.items()}
    if isinstance(value, list):
        return [subst(v, d) for v in value]
    return value


def leaf_paths(obj, prefix=()):
    """Every path to a scalar inside the fixture."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            for p in leaf_paths(v, prefix + (k,)):
                yield p
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for p in leaf_paths(v, prefix + (i,)):
                yield p
    else:
        yield prefix


def container_paths(obj, prefix=()):
    """Every path to a dict or list inside the fixture, root last."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            for p in container_paths(v, prefix + (k,)):
                yield p
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            for p in container_paths(v, prefix + (i,)):
                yield p
    if isinstance(obj, (dict, list)):
        yield prefix


def render(path):
    out = ""
    for part in path:
        out += ("[%d]" % part) if isinstance(part, int) else (("." if out else "") + str(part))
    return out or "<root>"


def at(obj, path):
    for part in path:
        obj = obj[part]
    return obj


def replace_at(obj, path, new):
    if not path:
        return new
    out = copy.deepcopy(obj)
    node = out
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = new
    return out


def hollowed(node, value, keep_booleans):
    if isinstance(node, dict):
        return {k: hollowed(v, value, keep_booleans) for k, v in node.items()}
    if isinstance(node, list):
        return [hollowed(v, value, keep_booleans) for v in node]
    if keep_booleans and isinstance(node, bool):
        return node
    return value


def heading_sections(text):
    """(heading title, line indices of its body) for a markdown document."""
    out, current = [], None
    for i, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("#"):
            current = (line.strip("# ").strip(), [])
            out.append(current)
        elif current is not None:
            current[1].append(i)
    return [(title, body) for title, body in out if body]


def drop_section(text, title):
    lines = text.splitlines()
    keep, current = [], None
    for line in lines:
        if line.lstrip().startswith("#"):
            current = line.strip("# ").strip()
            keep.append(line)
            continue
        if current == title:
            continue
        keep.append(line)
    return "\n".join(keep) + "\n"


# ---------------------------------------------------------------------------
# Tool adapters: how a registry is invoked from the command line
# ---------------------------------------------------------------------------

def run(script, args, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    # Timeboxed, because the ACCESS scenarios place FIFOs where receipts are
    # expected and a tool that opens one blocks forever. A suite that can hang
    # proves nothing; a tool that did not finish is reported as producing no
    # verdict at all, which is the defect the timeout exists to expose.
    try:
        return subprocess.run([sys.executable, os.path.join(RUN_DIR, script)] + args,
                              capture_output=True, text=True, env=env, timeout=90)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            e.cmd, 124, stdout=(e.output or ""),
            stderr="the tool did not finish within 90s; a check that hangs prints no verdict "
                   "in either mode, which is worse than a crash")


def verdict_and_evidence(stdout, name):
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == name:
            return parts[1], line
    return "NO-VERDICT-LINE", ""


class DirTool:
    """sbe_gate.py and sbe_design.py: evidence is a file in the target directory."""

    def __init__(self, script, env=None, has_fallback=False):
        self.script = script
        self.env = env or {}
        # True only for a tool that can answer WITHOUT running the check (the
        # design tool's no-dossier fallback). The invoked heuristic below greps
        # for that fallback's load-bearing phrase, and for a tool that has no
        # such fallback the grep is constant-true and measures nothing, so the
        # accounting is only claimed where it means something.
        self.has_fallback = has_fallback

    def root(self, d):
        return d

    def place(self, d, relpath, content):
        write_file(os.path.join(d, relpath), content)

    def invoke(self, d, name, extra_env):
        env = dict(self.env)
        env.update(extra_env)
        return run(self.script, [name, d], env)

    def relpath(self, p):
        return p


class ScoreTool:
    """sbe_score.py: evidence lives under the vault, or behind an env var."""

    has_fallback = False    # every scorer check runs unconditionally; see DirTool

    def __init__(self, script):
        self.script = script
        self.env = {}

    def root(self, d):
        return d

    def place(self, d, relpath, content):
        write_file(os.path.join(d, relpath), content)

    def invoke(self, d, name, extra_env):
        env = {"BROTHERSBE_VAULT": d, "BROTHERSBE_REGISTRIES": "", "SBE_LINT_ROOT": "",
               "SBE_CITATION_ROOT": ""}
        env.update(extra_env)
        return run(self.script, [], env)

    def relpath(self, p):
        # The registry stores absolute paths resolved against the default vault;
        # the test re-roots them into a throwaway one.
        #
        # The vault PREFIX is tested before os.path.isabs, and that order is
        # load-bearing. With no vault configured the vault is the sentinel
        # `\\BROTHERSBE-NO-VAULT-CONFIGURED`, and on Windows under Python 3.13
        # and later ntpath.isabs no longer calls a rooted path with no drive
        # absolute. isabs was therefore False, this returned the path unchanged,
        # and os.path.join(fixture_dir, "\\...") reset to the DRIVE ROOT: the
        # fixture landed outside the fixture directory, the check found nothing
        # where it was told to look, and reported NO-DATA for a scenario that
        # demanded FAIL. Twenty-seven scenarios were being answered by a path
        # bug rather than by the check under test.
        vault = _telemetry.VAULT
        if p == vault or p.startswith(vault + os.sep):
            return os.path.relpath(p, vault)
        return os.path.relpath(p, vault) if os.path.isabs(p) else p


class InvariantTool:
    """sbe_release_invariant.py: one hardcoded check, no check-name argument.

    Its CLI is `sbe_release_invariant.py [directory] [base-ref] [--strict]`
    (tools/sbe_release_invariant.py:177, main() at :188), and a positional that
    is not a directory is read as a base ref (tools/sbe_release_invariant.py:
    204-207). DirTool's [name, d] pattern would hand the check name to that
    parser as a base ref, silently changing what gets diffed instead of naming
    a check nothing here needs named. This adapter passes only the directory
    and lets base default to origin/main (tools/sbe_release_invariant.py:57,
    217), a ref that never resolves inside a throwaway scenario directory, and
    lets an empty directory read as either "not a git working tree" or an
    unborn HEAD. Both are NO-DATA by the check's own design (tools/
    sbe_release_invariant.py:133-147), never a crash and never a PASS.
    """

    has_fallback = False    # the check body always runs; see DirTool

    def __init__(self, script):
        self.script = script
        self.env = {}

    def root(self, d):
        return d

    def place(self, d, relpath, content):
        write_file(os.path.join(d, relpath), content)

    def invoke(self, d, name, extra_env):
        env = dict(self.env)
        env.update(extra_env)
        return run(self.script, [d], env)

    def relpath(self, p):
        return p


ADAPTERS = {
    ("sbe_gate.py", "GATES"): DirTool("sbe_gate.py"),
    ("sbe_design.py", "CHECKS"): DirTool("sbe_design.py", env={"SBE_DOSSIER_ROOT": ""},
                                         has_fallback=True),
    ("sbe_score.py", "CHECKS"): ScoreTool("sbe_score.py"),
    ("sbe_plan.py", "PLAN_CHECKS"): DirTool("sbe_plan.py"),
    ("sbe_release_invariant.py", "CHECKS"): InvariantTool("sbe_release_invariant.py"),
    ("sbe_instruction_surface.py", "CHECKS"): InvariantTool("sbe_instruction_surface.py"),
}


def write_file(path, content):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if isinstance(content, dict):
        body = json.dumps(content)
    elif isinstance(content, list):
        body = "".join(json.dumps(row) + "\n" for row in content)
    else:
        body = content
    # Binary mode, not "w": text mode on Windows rewrites every \n to \r\n on
    # the way to disk, so a fixture whose bytes a check hashes (sbe_plan.py's
    # freshness gate pins _FX_ADR_SHA against the in-memory string) lands on
    # disk with different bytes than the hash was computed against, and the
    # gate FAILs on a fixture the harness itself produced honestly. Binary
    # mode cannot translate a newline on any platform; see
    # _check_write_file_no_translation below, which pins this two ways so an
    # edit back to text mode goes red everywhere, not only on Windows.
    with open(path, "wb") as f:
        f.write(body.encode("utf-8"))


def _check_write_file_no_translation():
    """Pin the Windows fixture-corruption defect two ways, like every other
    structural check in this file that a scenario cannot reach (see
    PRUNED_WITH_SOURCE, PLANTED_SOURCE, NONREGULAR_SOURCE below): a behavior
    half and a mechanism half, so a regression is caught on every platform,
    not only the one that exposed it.

    Half one is behavioral: it calls the real harness writer with "a\\nb\\n"
    and asserts the bytes landing on disk are exactly b"a\\nb\\n". On Linux
    and macOS this passes whether write_file uses "w" or "wb", because
    neither platform's text mode rewrites \\n; it is here so the assertion
    that on-disk bytes must equal the given bytes exists at all, and it is
    what actually goes red in CI on Windows if the translating mode returns.

    Half two is mechanism, not behavior: it reads this file's own source,
    finds the write_file function, and asserts the open() call inside it
    uses the literal mode "wb". A bug that reintroduces text mode ("w") is
    invisible to half one on Linux and macOS (no translation happens there
    either way), so half two is what turns that same edit red on every
    platform the suite runs on, not only Windows.
    """
    failures = []
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "probe.txt")
        write_file(target, "a\nb\n")
        with open(target, "rb") as f:
            got = f.read()
        if got != b"a\nb\n":
            failures.append("write_file: wrote %r for input \"a\\nb\\n\", want b'a\\nb\\n'. A "
                            "fixture write that does not round-trip its own newlines byte-for-byte "
                            "is the exact defect that moved sbe_plan.py's pinned freshness hash "
                            "out from under it on Windows" % (got,))

    tree = ast.parse(open(__file__, "rb").read(), filename=__file__)
    write_file_def = next((n for n in ast.walk(tree)
                           if isinstance(n, ast.FunctionDef) and n.name == "write_file"), None)
    if write_file_def is None:
        failures.append("write_file: could not find the write_file function definition in %s to "
                        "confirm its open() mode; the mechanism half of this pin has nothing to "
                        "check" % __file__)
    else:
        opens = [n for n in ast.walk(write_file_def)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"]
        modes = []
        for call in opens:
            mode_arg = call.args[1] if len(call.args) > 1 else next(
                (kw.value for kw in call.keywords if kw.arg == "mode"), None)
            modes.append(_folded_str(mode_arg) if mode_arg is not None else None)
        if not opens:
            failures.append("write_file: found no open() call inside the function body, so the "
                            "mechanism half of this pin cannot confirm binary mode at all")
        elif "wb" not in modes:
            failures.append("write_file: open() mode is %r, want \"wb\". Text mode (\"w\") lets "
                            "the interpreter rewrite \\n to \\r\\n on Windows, moving the bytes on "
                            "disk away from the bytes a pinned hash (sbe_plan.py's freshness gate) "
                            "was computed against; this must stay binary on every platform" % modes)
    return failures


# ---------------------------------------------------------------------------
# Scenario generation
# ---------------------------------------------------------------------------

WRONG_SHAPES = [
    ("c-json-is-a-list", "[{\"figures\": []}]"),
    ("c-json-is-a-string", "\"a string\""),
    ("c-json-is-a-number", "42"),
]
MALFORMED = ("d-malformed-json", "{not json\n")
WRONG_SHAPE_LINES = [
    ("c-jsonl-line-is-a-string", "\"a string\"\n"),
    ("c-jsonl-line-is-a-list", "[1, 2, 3]\n"),
]


class Scenario:
    """One run: files to write, env to set, the verdict that would be a defect."""

    def __init__(self, sid, label, files, env=None, expect=None, forbid_pass=True,
                 expect_invoked=True, setup=None, exemptible=False):
        self.sid = sid
        self.label = label
        self.files = files
        self.env = env or {}
        self.expect = expect          # an exact verdict, or None for "anything but PASS"
        self.forbid_pass = forbid_pass
        self.expect_invoked = expect_invoked
        # Only a HOLLOWING scenario may be excused by a declared exemption.
        # The exemption key space and the derived scenario-id space used to be
        # the same string space, so a check whose fixture held a file named
        # `access` could write a constructor-legal key that waived the whole
        # ACCESS axis: five scenarios, under the cap, with the printed total
        # unchanged. The rule is structural now: scenarios from the legacy,
        # access and declared-reads classes are non-exemptible by
        # construction, whatever any key spells, because "no PASS over
        # evidence this tool could not open" is not a sentence any check's
        # exemption may narrow.
        self.exemptible = exemptible
        # A callable run against the scenario directory after the files are
        # written: the ACCESS scenarios use it to chmod evidence to 000, replace
        # it with a broken symlink or a FIFO, or plant a symlink loop. The
        # runner restores read access before the temp directory is cleaned up.
        self.setup = setup
        # True when the fixture's declared git state must be applied: the "full"
        # scenario always applies it; an access scenario that asserts the full
        # verdict (loop-beside-evidence) needs the same commit or it would be
        # asserting the wrong baseline.
        self.use_git = False


def legacy_cases(tool, check):
    """The four original shapes: absent file, declared-empty, wrong type, malformed."""
    out = [Scenario("a-empty-directory", "a", {}, expect="NO-DATA",
                    expect_invoked=(tool.script != "sbe_design.py"))]
    if tool.script == "sbe_design.py":
        # A directory that IS a dossier, so the tool cannot answer from its
        # no-target fallback and the check itself has to produce the verdict.
        out.append(Scenario("a2-dossier-with-a-zero-byte-intake", "a2",
                            {"00-intake.json": ""}))
    if check.kind == "tree":
        fx = check.empty_fixture or {}
        out.append(Scenario("b-declares-nothing", "b", fx.get("files", {}),
                            env={check.reads[0]: fx.get("value", "%(dir)s")},
                            expect=check.empty_expect))
        return out
    path = tool.relpath(check.reads[0])
    if check.kind == "json":
        body = json.dumps({check.item_key: []}) if check.item_key else "{}"
    elif check.kind == "jsonl":
        body = ""
    elif check.kind == "git":
        body = check.empty_fixture or ""
    else:
        body = ""
    out.append(Scenario("b-declares-nothing", "b", {path: body}, expect=check.empty_expect))
    if check.kind == "json":
        for cid, content in WRONG_SHAPES:
            out.append(Scenario(cid, "c", {path: content}, expect="FAIL"))
        out.append(Scenario(MALFORMED[0], "d", {path: MALFORMED[1]}, expect="FAIL"))
    elif check.kind == "jsonl":
        for cid, content in WRONG_SHAPE_LINES:
            out.append(Scenario(cid, "c", {path: content}, expect="FAIL"))
        out.append(Scenario(MALFORMED[0], "d", {path: MALFORMED[1] + "\n"}, expect="FAIL"))
    return out


def hollow_cases(tool, check):
    """The empty-VALUE sweep, derived from the check's own worked example."""
    fx = check.full_fixture
    if fx is None:
        return []
    files, env = fx["files"], fx.get("env", {})
    out = [Scenario("full", "full", files, env=env, expect=check.full_expect,
                    forbid_pass=(check.full_expect != "PASS"))]

    def variant(sid, label, rel, content):
        # A hollowing that changed nothing is not a scenario. Emptying a subtree
        # whose only leaf is a preserved boolean reproduces the fixture exactly,
        # and asserting "this must not PASS" about the untouched positive example
        # would be asserting the opposite of the sanity case two lines above.
        if content == files[rel]:
            return
        merged = dict(files)
        merged[rel] = content
        out.append(Scenario(sid, label, merged, env=env, exemptible=True))

    for rel, content in files.items():
        if isinstance(content, (dict, list)):
            for tag, vname, value in COMMENT_ONLY:
                variant("%s::*|%s" % (rel, vname), tag, rel,
                        hollowed(content, value, keep_booleans=True))
            for tag, vname, value in (EMPTY_VALUES + VACUOUS_VALUES + REDUCES_TO_NOTHING
                                      + NOT_MEASUREMENTS):
                variant("%s::*|%s" % (rel, vname), tag, rel,
                        hollowed(content, value, keep_booleans=True))
                for path in container_paths(content):
                    if not path:
                        continue
                    variant("%s::%s/*|%s" % (rel, render(path), vname), tag, rel,
                            replace_at(content, path,
                                       hollowed(at(content, path), value, keep_booleans=True)))
                for path in leaf_paths(content):
                    variant("%s::%s|%s" % (rel, render(path), vname), tag, rel,
                            replace_at(content, path, value))
            for path in leaf_paths(content):
                leaf = at(content, path)
                if not isinstance(leaf, bool):
                    continue
                for word in OPPOSITE_WORDS[leaf]:
                    variant("%s::%s|says-%s" % (rel, render(path), word), "t", rel,
                            replace_at(content, path, word))
            for path in container_paths(content):
                if not isinstance(at(content, path), list):
                    continue
                items = at(content, path)
                variant("%s::%s=[{}]" % (rel, render(path)), "h", rel,
                        replace_at(content, path, [{} for _ in items]))
                variant("%s::%s=[]" % (rel, render(path)), "j", rel,
                        replace_at(content, path, []))
        else:
            variant("%s::whitespace" % rel, "f", rel, "   \n")
            for _tag, vname, value in VACUOUS_VALUES:
                # The text-shaped vacuity: an artifact whose whole body is the
                # word TODO is present, non-empty, and says nothing.
                variant("%s::vacuous-%s" % (rel, vname), "v", rel, "%s\n" % value)
                # And the same one level in: every heading kept, every body
                # replaced by a placeholder rather than emptied. This is the
                # markdown shape of "the keys are all there and the values all
                # say TODO".
                if heading_sections(content):
                    filled = "".join("%s\n%s\n" % (l, value) for l in content.splitlines()
                                     if l.lstrip().startswith("#"))
                    variant("%s::every-section-%s" % (rel, vname), "v", rel, filled)
            for title, _body in heading_sections(content):
                variant("%s##%s" % (rel, title), "s", rel, drop_section(content, title))
            # The comment-shaped hollowing, which no sweep had: an author does
            # not empty a section, they comment it out, and a section that
            # renders as empty was scaffolding to four readers and content to
            # four others in the same file. Whole file first, then one heading's
            # body at a time. Markdown only: an HTML comment is a markdown
            # notion, and a source file holding one is just text to the lint.
            if rel.endswith(".md"):
                variant("%s::commented-out" % rel, "k", rel,
                        "<!-- moved to the wiki\n%s\n-->\n" % content)
                for title, _body in heading_sections(content):
                    gutted = drop_section(content, title).replace(
                        "# %s" % title,
                        "# %s\n<!-- the %s section moved to the wiki -->" % (title, title), 1)
                    # The "|" keeps the sid's exemption key identical to the
                    # dropped-section sweep's: a declared optional leaf covers
                    # the same section whichever way it was emptied.
                    variant("%s##%s|commented" % (rel, title), "k", rel, gutted)
        variant("%s::zero-bytes" % rel, "i", rel, "")

    # (i) in its purest form: every file the check DECLARES it reads exists with
    # the right name and holds nothing at all.
    named = {tool.relpath(p): "" for p in check.reads if os.sep in p or p.endswith(
        (".json", ".jsonl", ".md", ".txt")) or p == "APPROVAL"}
    if named:
        out.append(Scenario("declared-reads::zero-bytes", "i", named, env=env))
    return out


def seeded_cases(tool, check, seeds, per_seed=8):
    """A seeded random composition of the hollowing operations above.

    The fixed sweep is nine mutations per fixture, so it finds only the
    emptiness shapes somebody already imagined. This mode composes those SAME
    operations (no new emptiness values are invented here) at random depths and
    in random combinations of one to three per scenario, over the check's own
    declared worked example, and asserts the same invariant: never PASS. It is
    a generator over evidence shapes, not a state machine over command
    sequences, because these tools are pure checkers over trees. Every scenario
    id carries its seed, so a finding reproduces with the same --seed N.
    Mutations that would land on a declared optional leaf are skipped, because
    a PASS there is the exemption working, not the invariant breaking; and a
    composition that reproduces the fixture unchanged is not a scenario."""
    fx = check.full_fixture
    if fx is None or check.full_expect != "PASS" or not seeds:
        return []
    files, env = fx["files"], fx.get("env", {})
    # The pools mirror the fixed sweep's own scoping exactly: COMMENT_ONLY
    # tokens carry real words and say nothing only after a tool REDUCES them,
    # so the fixed sweep applies them to whole fixtures and never to a single
    # leaf (a lone id field holding words is odd, not empty). The first seeded
    # run violated that scoping and manufactured a false finding; the pools
    # below are the fix, not a softening of the invariant.
    leaf_values = EMPTY_VALUES + VACUOUS_VALUES + REDUCES_TO_NOTHING + NOT_MEASUREMENTS
    whole_values = leaf_values + COMMENT_ONLY
    text_values = [v for v in (EMPTY_VALUES + VACUOUS_VALUES) if v[2] is not None]
    out = []
    for seed in seeds:
        rng = random.Random(seed)
        for n in range(per_seed):
            merged = dict(files)
            ops = []
            for _ in range(rng.randint(1, 3)):
                rel = rng.choice(sorted(merged))
                content = merged[rel]
                if isinstance(content, (dict, list)):
                    space = ([("whole", None)]
                             + [("subtree", p) for p in container_paths(content) if p]
                             + [("leaf", p) for p in leaf_paths(content)])
                    kind, path = space[rng.randrange(len(space))]
                    pool = whole_values if kind == "whole" else leaf_values
                    tag, vname, value = pool[rng.randrange(len(pool))]
                    if kind == "leaf":
                        if "%s::%s" % (rel, render(path)) in check.optional_leaves:
                            continue
                        merged[rel] = replace_at(content, path, value)
                        ops.append("%s:%s=%s" % (rel, render(path), vname))
                    elif kind == "subtree":
                        merged[rel] = replace_at(
                            content, path,
                            hollowed(at(content, path), value, keep_booleans=True))
                        ops.append("%s:%s/*=%s" % (rel, render(path), vname))
                    else:
                        merged[rel] = hollowed(content, value, keep_booleans=True)
                        ops.append("%s:*=%s" % (rel, vname))
                else:
                    sections = [t for t, _b in heading_sections(content)
                                if "%s##%s" % (rel, t) not in check.optional_leaves]
                    if sections and rng.random() < 0.5:
                        title = sections[rng.randrange(len(sections))]
                        merged[rel] = drop_section(content, title)
                        ops.append("%s:drop##%s" % (rel, title))
                    else:
                        _tag, vname, value = text_values[rng.randrange(len(text_values))]
                        merged[rel] = "%s\n" % value
                        ops.append("%s:body=%s" % (rel, vname))
            if not ops or merged == dict(files):
                continue
            out.append(Scenario("seeded|s=%d#%d|%s" % (seed, n, ";".join(ops))[:200],
                                "z", merged, env=env))
    return out


# The ACCESS axis. Seven rounds of scenarios probed what the evidence SAYS: this
# is the first set that probes whether the tool could OPEN it. The suite held
# zero occurrences of chmod, symlink or mkfifo in 4200 lines of test code, so no
# scenario had ever distinguished a check that read clean evidence from a check
# that read nothing: a chmod 000 source file vanished from a lint count, chmod
# 000 registry files turned two FAILs into two PASSes saying "none", and a FIFO
# hung all three tools with no verdict, in both modes, forever.
ACCESS_APPLIES = os.name == "posix" and os.geteuid() != 0

def _chmod_000(rel):
    def setup(d):
        os.chmod(os.path.join(d, rel), 0)
    return setup


def _chmod_000_parent(rel):
    # The axis one level up: the evidence file untouched, its PARENT directory
    # unenterable. Discovery (a glob, a walk) returns fewer paths rather than
    # an error over one of these, so the per-file ACCESS check was never asked
    # and a FAIL became a PASS whose sentence said "none".
    def setup(d):
        os.chmod(os.path.dirname(os.path.join(d, rel)) or d, 0)
    return setup


def _broken_symlink(rel):
    def setup(d):
        p = os.path.join(d, rel)
        os.remove(p)
        os.symlink(os.path.join(d, "no-such-target"), p)
    return setup


def _symlink_loop(rel):
    def setup(d):
        p = os.path.join(d, rel)
        os.remove(p)
        os.symlink(os.path.basename(p), p)      # a link that resolves to itself
    return setup


def _fifo(rel):
    def setup(d):
        p = os.path.join(d, rel)
        os.remove(p)
        os.mkfifo(p)
    return setup


def _loop_beside(d):
    os.symlink(".", os.path.join(d, "selfloop-dir"))


def restore_access(top):
    """Give everything under the scenario directory its read bits back.

    tempfile.TemporaryDirectory cannot delete a chmod 000 entry, so a scenario
    that took access away gives it back before cleanup. Top-down, so a 000
    directory is reopened before the walk needs to enter it.
    """
    try:
        os.chmod(top, 0o755)
    except OSError:
        return
    for dp, dns, fns in os.walk(top):
        for n in dns + fns:
            p = os.path.join(dp, n)
            if os.path.islink(p):
                continue
            try:
                os.chmod(p, 0o755 if n in dns else 0o644)
            except OSError:  # sbe: allow-silent cleanup only; the temp dir is deleted right after and an entry this cannot reopen fails loudly there
                pass


def access_cases(tool, check, force=False):
    """Evidence that exists and cannot be opened must never read as clean or absent.

    Four shapes per check, each derived from the check's own worked example: the
    evidence file unreadable (chmod 000), replaced by a broken symlink, replaced
    by a symlink loop, and replaced by a FIFO (which blocks any open() forever,
    so it also proves the tool terminates). A fifth plants a self-referential
    directory symlink beside intact evidence and demands the walk terminates
    without changing the verdict. Not applicable when running as root (chmod
    cannot take access away from root) and declared so rather than skipped.

    force=True bypasses the ACCESS_APPLIES platform gate while still returning
    [] when the check itself has no full_fixture (that condition is about the
    check's own data, not the host, so it always applies). Used only by
    counts() below: the suite's shape (how many scenarios exist to be walked)
    is a property of the checks and registries, not of which host happens to
    run it, so the doc-truth figure counts() feeds to shipped-doc comparisons
    must not shrink on a host where chmod/symlink/mkfifo are unavailable. The
    real run in main() calls this without force and correctly runs, and
    discloses, fewer scenarios on such a host (see ACCESS_APPLIES's own
    "notapplicable" note in main()); only the platform-INDEPENDENT total this
    function computes is being widened here, not what main() actually does.
    """
    fx = check.full_fixture
    if (not force and not ACCESS_APPLIES) or fx is None:
        return []
    files, env = fx["files"], fx.get("env", {})
    declared = tool.relpath(check.reads[0])
    target = declared if declared in files else sorted(files)[0]
    out = [
        Scenario("access::%s|chmod-000" % target, "x", files, env=env,
                 setup=_chmod_000(target)),
        Scenario("access::%s|broken-symlink" % target, "x", files, env=env,
                 setup=_broken_symlink(target)),
        Scenario("access::%s|symlink-loop" % target, "x", files, env=env,
                 setup=_symlink_loop(target)),
        Scenario("access::%s|fifo" % target, "x", files, env=env,
                 setup=_fifo(target)),
        # Evidence intact, a loop planted beside it: the walk must terminate and
        # the verdict must be exactly what the worked example produces.
        Scenario("access::loop-beside-evidence", "x", files, env=env,
                 setup=_loop_beside, expect=check.full_expect,
                 forbid_pass=(check.full_expect != "PASS")),
        # Evidence intact, its PARENT unenterable. expect_invoked=False: a tool
        # whose dossier walk is refused at the door cannot reach the check body,
        # and answering from the fallback WITH THE DENIAL NAMED is the honest
        # verdict; what this scenario forbids is a PASS, or a clean-sounding
        # absence produced by discovery silently returning fewer paths.
        Scenario("access::%s|parent-chmod-000" % target, "x", files, env=env,
                 setup=_chmod_000_parent(target), expect_invoked=False),
    ]
    out[-2].use_git = True
    return out


# ---------------------------------------------------------------------------

def counts():
    """(checks, registries, modules, scenarios, waived) without running anything.

    Exists so a doc that prints this test's summary line can be checked against
    it mechanically. Three shipped docs printed an eval count the suite had not
    produced for a whole wave, which is the same defect as a stale evidence
    string: a number asserted where nothing recomputed it. `waived` counts the
    scenarios a declared exemption matches, computed the same way the run
    counts them, so the summary line's waived figure is checkable too. `modules`
    is the same `len(mods)` main() prints in "N module(s)": windows-porting-lane
    round 2 found the doc-consistency eval that reads this function (dc2, in
    evals/run_evals.py) captured that figure with its own regex and then never
    compared it to anything, so a shipped doc's module count could go, and did
    go, stale with nothing to catch it. `modules` is returned here so dc2 can
    derive its expectation from the SAME discovery main() runs (load_tool_
    modules(), called once, right below) rather than from a number baked into
    either file.

    access_cases(..., force=True): the ACCESS axis (chmod, broken symlink,
    symlink loop, FIFO) does not run on a host where ACCESS_APPLIES is False
    (Windows, or POSIX as root), and main() correctly runs fewer scenarios
    there, disclosed by its own "notapplicable" note. A shipped doc's pasted
    summary line is not that: it is a fixed figure asserted once about the
    suite's SHAPE, and the checks and registries this counts do not change
    from one host to another, so the scenario total this function reports
    must not either. Without force=True here, a Windows run of this same
    function returned a count 192 lower than the POSIX figure every shipped
    doc quotes (32 checks * 6 ACCESS scenarios each), and the doc-consistency
    eval that reads this function (evals/run_evals.py's dc2) read that as the
    docs being stale on Windows specifically, when nothing about the tree
    the docs describe had changed.
    """
    mods, _failed = load_tool_modules()
    registries, _ = discover_registries(mods)
    checks = scenarios = regs = waived = 0
    for fn, attr, reg in registries:
        tool = ADAPTERS.get((fn, attr))
        if tool is None:
            continue
        regs += 1
        for _name, check in reg.items():
            checks += 1
            scs = (legacy_cases(tool, check) + hollow_cases(tool, check)
                   + access_cases(tool, check, force=True))
            scenarios += len(scs)
            waived += sum(1 for sc in scs
                          if sc.exemptible and check.optional_leaves.get(sc.sid.split("|")[0]))
    return checks, regs, len(mods), scenarios, waived


def main():
    mods, import_failures = load_tool_modules()
    del PRUNED_WITH_SOURCE[len(set(PRUNED_WITH_SOURCE)):]
    registries, defects = discover_registries(mods)
    failures = list(import_failures) + list(defects)
    checked = ran = 0
    notapplicable, exemptions, unregistered = [], [], []
    lint_exemptions = []

    print("BROTHERSBE HONESTY META-TEST: no check may report PASS over evidence it never examined")
    print("tools under test: %s" % RUN_DIR)

    # Registries are discovered. One this test cannot invoke is a failure, never a skip.
    known = {}
    for fn, attr, reg in registries:
        adapter = ADAPTERS.get((fn, attr))
        if adapter is None:
            failures.append("%s.%s is a registry of %d Check(s) that this test does not know how "
                            "to invoke. Add an adapter in ADAPTERS; a registry nobody walks is a "
                            "check nobody covers" % (fn, attr, len(reg)))
            continue
        known[(fn, attr)] = (adapter, reg)

    # Every function anywhere in tools/ that can return PASS must be registered.
    registered_fns = {c.fn for _, _, reg in registries for c in reg.values()
                      if isinstance(c, Check)}
    registered_pairs = {(getattr(f, "__module__", "") + ".py", f.__name__) for f in registered_fns}
    for fn, name, why in pass_returning_functions(mods):
        if (fn, name) not in registered_pairs:
            unregistered.append("%s:%s (%s)" % (fn, name, why))
    if unregistered:
        failures.append("function(s) that can return the verdict PASS but sit in no registry, so "
                        "no scenario in this file reaches them: %s" % ", ".join(unregistered))
    # Every print in a report tool goes through the one_line choke point, so no
    # interpolated value (a path, a root, an error repr) can write a second
    # report line: the class behind the directory-name verdict forgery. The
    # linted set is derived from tool_sources(), never a filename list; the
    # reviewed exceptions are reconciled here and printed below.
    unflattened, print_exempt_hits, dead_print_exemptions = unflattened_report_prints()
    for fn, lineno, why in unflattened:
        failures.append("%s:%s %s" % (fn, lineno, why))
    for fn in dead_print_exemptions:
        failures.append("REPORT_PRINT_EXEMPT names %s but no print there needed excusing this "
                        "run; a review that excuses nothing is dead, remove the entry" % fn)
    # The source lint's own allowlist, reconciled against what it exempted.
    for key, why in sorted(NOT_A_VERDICT.items()):
        hits = EXEMPTION_HITS.get(key, [])
        if not hits:
            failures.append("NOT_A_VERDICT[%r] names no function in that file, so it exempts "
                            "nothing while reading as a reviewed exemption; remove it or fix "
                            "the name" % (key,))
        elif len(hits) > 1:
            failures.append("NOT_A_VERDICT[%r] resolved to %d functions of that name (lines %s). "
                            "The exemption was reviewed for ONE body, and a second function of "
                            "the same name inherits it unreviewed, which is how a "
                            "`return \"PASS\"` body becomes invisible to this lint; give the "
                            "shadowing function a distinct name"
                            % (key, len(hits), ", ".join(str(l) for l in hits)))
        else:
            lint_exemptions.append("%s:%s (line %d): %s" % (key[0], key[1], hits[0], why))
    # The harness writer itself, not a discovered tool check: pinned here,
    # two ways, so a Windows-only fixture-corruption regression is caught on
    # every platform this suite runs on. See _check_write_file_no_translation.
    failures.extend(_check_write_file_no_translation())
    for path in sorted(set(PRUNED_WITH_SOURCE)):
        failures.append("%s was pruned from the walk and holds Python source, so any registry or "
                        "verdict-producing function in it is outside this test's coverage while "
                        "the summary line below still counts as if it were not" % path)
    for path in sorted(set(PLANTED_SOURCE)):
        failures.append("tools/%s is a .py file inside a bytecode cache directory. Source does not "
                        "live in __pycache__ (the interpreter owns that name), so this is either a "
                        "planted file or a misplaced module; it was NOT imported, it is NOT counted "
                        "in the module total, and this run fails until it is removed or moved where "
                        "the walk and the install manifest can see it" % path)
    for path in sorted(set(NONREGULAR_SOURCE)):
        failures.append("tools/%s is a .py path that is not a regular file (a symlink, FIFO, "
                        "socket or device). A manifest cannot hash what it points at and this "
                        "suite will not import it: a symlinked module is invisible to `find "
                        "-type f` and to the install verifier, so it was NOT imported, NOT "
                        "counted, and this run fails until it is a regular file the walk and the "
                        "manifest can both vouch for" % path)
    for path in sorted(set(DENIED_DIRS)):
        failures.append("%s could not be entered (permission or I/O error), so any registry or "
                        "verdict-producing function in it is outside this test's coverage while "
                        "the summary line below still counts as if it were not" % path)
    if not ACCESS_APPLIES:
        notapplicable.append("the ACCESS scenarios (chmod, broken symlink, symlink loop, FIFO) do "
                             "not apply on this host: running as root, or not POSIX, so taking "
                             "read access away is not possible; declared rather than skipped")

    for (fn, attr), (tool, reg) in sorted(known.items()):
        print("\n%s.%s  (%d checks discovered)" % (fn, attr, len(reg)))
        for name, check in reg.items():
            checked += 1
            if check.kind not in ("json", "jsonl"):
                notapplicable.append("%s %s: evidence is %s, so the wrong-shape and malformed-JSON "
                                     "cases do not apply" % (fn, name, check.kind))
            if check.full_fixture is None:
                notapplicable.append("%s %s: no positive fixture to hollow, declared reason: %s"
                                     % (fn, name, check.no_full_fixture))
            if check.full_expect != "PASS":
                notapplicable.append("%s %s: its worked example asserts %s rather than PASS, "
                                     "declared reason: %s"
                                     % (fn, name, check.full_expect, check.full_expect_reason))
            scenarios = (legacy_cases(tool, check) + hollow_cases(tool, check)
                         + access_cases(tool, check)
                         + seeded_cases(tool, check, SEEDS))
            invoked_count = 0
            saw_full = False
            matched_by_key = {}
            excused_by_key = {}
            matched_exempt = 0
            for sc in scenarios:
                with tempfile.TemporaryDirectory() as d:
                    for rel, content in subst(sc.files, d).items():
                        tool.place(d, rel, content)
                    git = ((check.full_fixture or {}).get("git")
                           if (sc.label == "full" or sc.use_git) else None)
                    if git:
                        git_commit(d, git["message"])
                    if sc.setup:
                        sc.setup(d)
                    out = tool.invoke(d, name, subst(sc.env, d))
                    ran += 1
                    restore_access(d)
                got, line = verdict_and_evidence(out.stdout, name)
                # The invoked heuristic greps for the design tool's no-target
                # fallback phrase. For a tool with no such fallback the grep is
                # constant-true and would measure nothing, so it is only applied,
                # and only reported, where a fallback exists to fall into.
                invoked = (NOT_INVOKED not in line) if tool.has_fallback else True
                invoked_count += invoked
                saw_full = saw_full or (sc.label == "full" and got == check.full_expect)
                bad = []
                key = sc.sid.split("|")[0]
                exempt = check.optional_leaves.get(key) if sc.exemptible else None
                if exempt:
                    matched_by_key[key] = matched_by_key.get(key, 0) + 1
                    matched_exempt += 1
                    WAIVED[0] += 1
                    if got == "PASS":
                        excused_by_key[key] = excused_by_key.get(key, 0) + 1
                if got == "PASS" and sc.forbid_pass and not exempt:
                    bad.append("a PASS over evidence that declares nothing is the defect this "
                               "whole project exists to prevent")
                elif sc.expect and got != sc.expect and not (exempt and got == "PASS"):
                    bad.append("want %s got %s" % (sc.expect, got))
                elif got not in ("PASS", "FAIL", "NO-DATA"):
                    bad.append("no verdict line for this check at all (%s)" % got)
                if "Traceback" in out.stderr:
                    bad.append("a traceback escaped to stderr")
                if out.returncode == 124:
                    bad.append("the tool hung: %s" % out.stderr)
                elif out.returncode != 0:
                    bad.append("advisory mode exited %d" % out.returncode)
                if sc.expect_invoked and tool.has_fallback and not invoked:
                    bad.append("the scenario never reached the check: the tool answered from its "
                               "no-target fallback")
                mark = "ok" if not bad else ("FAILED: " + "; ".join(bad))
                if exempt and got == "PASS":
                    mark = "ok (declared exemption)"
                if bad:
                    failures.append("%s %s [%s] %s" % (fn, name, sc.sid, "; ".join(bad)))
                if not QUIET or bad:
                    print("  %-26s %-3s %-52s %-9s %s"
                          % (name, sc.label, sc.sid[:52], got, mark))
            if check.full_fixture is not None and not saw_full:
                failures.append("%s %s: the declared full_fixture did not produce %s, so nothing "
                                "here proves the check body ran or that its worked example is a "
                                "real one" % (fn, name, check.full_expect))
            if matched_exempt > WAIVED_SCENARIO_CAP:
                failures.append("%s %s: its declared exemptions matched %d scenario(s) of this "
                                "sweep, over the cap of %d. An exemption is a per-leaf instrument, "
                                "and a key wide enough to waive this much of the sweep is wide "
                                "enough to hide a regression behind an unchanged scenario total; "
                                "narrow the PASS sentence instead of exempting the evidence"
                                % (fn, name, matched_exempt, WAIVED_SCENARIO_CAP))
            for path, why in sorted(check.optional_leaves.items()):
                # The listing carries what the exemption actually DID this
                # run, beside what it claims, so a reader can tell a live
                # waiver from a decorative one without re-deriving anything.
                exemptions.append("%s %s [%s]: %s (matched %d scenario(s), excused %d PASS(es) "
                                  "this run)" % (fn, name, path, why,
                                                 matched_by_key.get(path, 0),
                                                 excused_by_key.get(path, 0)))
            for dead in sorted(set(check.optional_leaves) - set(matched_by_key)):
                # An exemption no scenario matches exempts nothing and reads as
                # if it did: it either names a section the fixture no longer
                # has, or was written against a sid that never existed. Either
                # way it is a claim of coverage nothing exercises.
                failures.append("%s %s: optional_leaves[%r] matched no scenario in this sweep, so "
                                "it exempts nothing while reading as if it did; remove it or fix "
                                "its key" % (fn, name, dead))
            for dead in sorted(k for k in matched_by_key if not excused_by_key.get(k)):
                # The dishonest sibling of the unmatched key: an exemption
                # whose every matched scenario satisfies the law WITHOUT it
                # shields nothing today, and it is armed: if the rule it
                # secretly duplicates ever regresses, exactly these scenarios
                # start PASSing under a pre-approved mark with no failure
                # anywhere. Two of the four shipped waived scenarios were this
                # shape, and the one summary number a reader had ("4 waived")
                # counted them as if they excused something. A waiver is alive
                # (it excuses real PASSes) or it is red.
                failures.append("%s %s: optional_leaves[%r] excused nothing: every scenario it "
                                "matches already satisfies the law without it, so the waiver "
                                "shields nothing while reading as if it did, and it would hide "
                                "a future regression on exactly those scenarios; remove it"
                                % (fn, name, dead))
            if tool.has_fallback:
                print("  %-26s %s" % (name, "-> %d/%d scenarios reached the check body"
                                      % (invoked_count, len(scenarios))))
            else:
                print("  %-26s %s" % (name, "-> invocation accounting n/a: this tool runs every "
                                            "check unconditionally, so there is no fallback to "
                                            "fall into"))

    print("\ndeclared not-applicable (printed, never skipped silently):")
    for n in notapplicable:
        print("  %s" % n)
    print("\ndeclared exemptions from the empty-value sweep (each states its reason):")
    for e in exemptions or ["none"]:
        print("  %s" % e)
    print("\ndeclared exemptions from the verdict-source lint (each names the function it "
          "resolved to this run):")
    for e in lint_exemptions or ["none"]:
        print("  %s" % e)
    print("\ndeclared exemptions from the report-print lint (the linted set is every file "
          "tool_sources() walks; each entry states its reason and what it excused this run):")
    for fn in sorted(print_exempt_hits) or ["none"]:
        print("  %s: excused %d print(s); %s" % (fn, print_exempt_hits[fn],
                                                 REPORT_PRINT_EXEMPT[fn]))
    if checked < FLOOR_CHECKS:
        failures.append("only %d check(s) discovered, below the floor of %d. Deleting a check "
                        "used to shrink this count in silence, with the anti-shrinkage guarantee "
                        "living only in the eval suite and a number in a doc; the floor is raised "
                        "when checks are added and never lowered" % (checked, FLOOR_CHECKS))
    print("\n%d checks discovered from %d registries in %d module(s), %d scenarios run, "
          "%d waived by declared exemption, %d failure(s)."
          % (checked, len(known), len(mods), ran, WAIVED[0], len(failures)))
    for f in failures:
        print("  FAIL %s" % f)
    sys.exit(1 if failures else 0)


def git_commit(d, message):
    for cmd in (["init", "-q"], ["config", "user.email", "e@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", "-C", d] + cmd, check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", message], check=True, capture_output=True)


if __name__ == "__main__":
    main()
