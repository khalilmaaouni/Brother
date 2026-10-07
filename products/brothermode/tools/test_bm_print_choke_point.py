#!/usr/bin/env python3
"""The print choke point for every BrotherMode tool, as a class-level lint.

WHY THIS EXISTS. A BrotherMode tool prints report lines that people read and
that other tools and CI greps parse: NO-DATA lines, verdicts, paths, counts.
Those lines interpolate values nobody in this repository wrote: a vault note
path, a store row, an exception message, a model's answer. Measured 2026-09-26
over tools/*.py: 1,189 print() sites outside the one converted file failed the
rule below, 1,032 of them real interpolations. bm_vault.py printed
`"    %s" % row["path"]` raw, so a note path carrying a newline printed a second
line of its author's choosing, which reads exactly like a real one.

WHY A LINT AND NOT A LIST OF FIXES. Wrapping values one at a time is a list of
instances, and the next value nobody wrapped reopens the channel. BrotherSBE
closed the same class this way (products/brothersbe/evals/test_no_data_class.py,
unflattened_report_prints): a report tool may print only a bare constant or a
WHOLE line that passed through one choke point after formatting. This file is
that rule for BrotherMode, built from this product's own primitive
(bm_learning.one_line and bm_learning.say) rather than importing the other
product's tools, per the ADR that no session in either product runs the
other's tools.

WHAT A PRINT MAY CARRY. No argument; one expression this lint can prove holds
no text from outside: a constant, a name every binding of which in the file is
a string constant, a %-format whose every conversion is numeric, a call to
one_line() or safe_display(), a json.dumps() that keeps ensure_ascii on (its
output is printable ASCII by construction); and no keyword except file= and
flush=. Everything else is an interpolation some future value can climb
through, flagged whether or not anyone has typed that value yet.

THE TWO LISTS, AND WHY BOTH FAIL WHEN THEY EXCUSE NOTHING.
PRINT_EXEMPT names files REVIEWED as not printing report lines, each with its
reason. PRINT_DEBT is the ratchet for files not yet converted: each entry is
the EXACT count of raw prints the file still owes. A count that goes up is a
new raw print and fails; a count that goes down fails until the entry is
lowered, so the ratchet can only be tightened on purpose and never drifts
loose; an entry at zero is dead and must be removed. An exemption that
excuses nothing is dead too. Neither list can outlive what it names.

THE SIBLING CHANNEL, STREAM WRITES. sys.stdout.write(x) and
sys.stderr.write(x) print the same report lines by another road: measured
2026-09-26, 322 non-constant write sites in 81 files, 308 of them refused by
the rule below. A write is held to the SAME predicate as a print (one
function, _safe_expr, so the two channels cannot drift apart), with exactly
one positional argument and no keyword. The line end a write carries on
purpose goes OUTSIDE the choke point: sys.stdout.write(one_line(x) + "\\n"),
or bm_learning.say(x, file=sys.stderr). one_line("x %s\\n" % v) passes this
lint but flattens its own line end into a visible escape, so the next write
lands on the same line; that is a conversion mistake this lint cannot see.
WRITE_EXEMPT and WRITE_DEBT are the same two lists for this channel, under the
same reconcile.

WHAT THIS DOES NOT COVER, named rather than implied, each measured 2026-09-26:
sys.stdout.buffer.write (1 site, bm_passport.py, a JSON document as UTF-8
bytes, not a report line); a stream reached through another name, such as a
parameter defaulting to sys.stdout (1 site, bm_mock_mcp.py, writing
json.dumps() output); a write method bound to a name, sys.__stdout__ and
sys.__stderr__, and getattr(sys, "stdout") (0 sites each); and a local
wrapper that prints through anything but one_line(). Each is invisible here,
exactly as it is to the BrotherSBE lint this mirrors.

Python 3.9, standard library only. No em or en dashes anywhere in this file.
"""
import ast
import contextlib
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.append(os.path.join(HERE, "../../../scripts"))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % os.path.basename(__file__))


def _load(name):
    """Load a sibling module by PATH, the same way bm_learn.py loads it."""
    spec = importlib.util.spec_from_file_location(
        name + "_for_print_lint", os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _load("bm_learning")


# Files reviewed as printing no report line at all. Empty on purpose until a
# file earns an entry; the reconcile below fails any entry that excuses nothing.
PRINT_EXEMPT = {}

# The ratchet: files not yet converted, each with the EXACT number of raw
# prints it still owes. Generated from this lint's own output on the day it
# landed, never typed from memory. Convert a file and remove its line; convert
# part of one and lower its number.
PRINT_DEBT = {
    "attempt_ledger.py": 10,
    "bm_autosave.py": 10,
    "bm_bench.py": 4,
    "bm_connectors.py": 29,
    "bm_consolidate.py": 3,
    "bm_embed_warm.py": 1,
    "bm_escalate.py": 14,
    "bm_freshness.py": 9,
    "bm_gate.py": 12,
    "bm_learn.py": 2,
    "bm_ledger.py": 9,
    "bm_memory_budget.py": 5,
    "bm_playbook.py": 2,
    "bm_private_scan.py": 4,
    "bm_project.py": 2,
    "bm_project_facts.py": 3,
    "bm_queue_numbers.py": 9,
    "bm_recurrence.py": 4,
    "bm_repair.py": 1,
    "bm_repomap.py": 1,
    "bm_score.py": 2,
    "bm_vault_asof.py": 8,
    "bm_vault_assertions.py": 12,
    "bm_vault_attribute_provenance.py": 15,
    "bm_vault_attributes.py": 17,
    "bm_vault_audit.py": 8,
    "bm_vault_authority.py": 5,
    "bm_vault_catalog.py": 4,
    "bm_vault_census_ext.py": 8,
    "bm_vault_cite.py": 7,
    "bm_vault_cli.py": 12,
    "bm_vault_closure.py": 15,
    "bm_vault_compose.py": 26,
    "bm_vault_contract.py": 10,
    "bm_vault_contradiction.py": 4,
    "bm_vault_crosswalk.py": 9,
    "bm_vault_curate.py": 24,
    "bm_vault_decay.py": 3,
    "bm_vault_digest.py": 1,
    "bm_vault_distill.py": 12,
    "bm_vault_enrich.py": 6,
    "bm_vault_enrich_gate.py": 8,
    "bm_vault_enrich_index.py": 4,
    "bm_vault_entity.py": 13,
    "bm_vault_events.py": 5,
    "bm_vault_exchange.py": 15,
    "bm_vault_export.py": 13,
    "bm_vault_graph.py": 20,
    "bm_vault_heat_temporal.py": 7,
    "bm_vault_hierarchy_req.py": 41,
    "bm_vault_identity.py": 27,
    "bm_vault_ids.py": 10,
    "bm_vault_intake.py": 10,
    "bm_vault_interchange.py": 14,
    "bm_vault_jbench.py": 5,
    "bm_vault_labels.py": 2,
    "bm_vault_ledger.py": 20,
    "bm_vault_lifecycle.py": 4,
    "bm_vault_lineage.py": 12,
    "bm_vault_lint.py": 7,
    "bm_vault_lock.py": 8,
    "bm_vault_notify.py": 20,
    "bm_vault_plugins.py": 5,
    "bm_vault_policy.py": 5,
    "bm_vault_posture.py": 4,
    "bm_vault_principals.py": 23,
    "bm_vault_promote.py": 2,
    "bm_vault_promotions.py": 16,
    "bm_vault_provenance.py": 10,
    "bm_vault_read_audit.py": 5,
    "bm_vault_realdata.py": 4,
    "bm_vault_retention.py": 38,
    "bm_vault_retier.py": 3,
    "bm_vault_route.py": 5,
    "bm_vault_shapes.py": 18,
    "bm_vault_staleness.py": 8,
    "bm_vault_survivorship.py": 6,
    "bm_vault_temporal.py": 5,
    "bm_vault_tiers.py": 1,
    "bm_vault_triage.py": 4,
    "bm_vault_web_ui.py": 1,
    "bm_verify.py": 1,
    "bm_worker_spawn.py": 1,
    "brother_paths.py": 1,
    "find_out.py": 4,
    "pattern_note.py": 8,
}

# The same two lists for sys.stdout.write and sys.stderr.write, under the same
# rules: reviewed exemptions, and the exact count of raw writes each file owes.
WRITE_EXEMPT = {}

WRITE_DEBT = {
    "attempt_hook.py": 3,
    "bm_autonomy.py": 2,
    "bm_autosave.py": 1,
    "bm_bash_audit.py": 1,
    "bm_bbstatus.py": 2,
    "bm_bench.py": 4,
    "bm_brother_canary.py": 2,
    "bm_clock_guard.py": 3,
    "bm_consolidate.py": 1,
    "bm_continue.py": 2,
    "bm_controller.py": 2,
    "bm_cursor.py": 2,
    "bm_cursor_hook.py": 2,
    "bm_docs.py": 2,
    "bm_docs_export.py": 2,
    "bm_effects.py": 2,
    "bm_embed_bge.py": 1,
    "bm_embed_warm.py": 5,
    "bm_fence_hook.py": 2,
    "bm_forecast.py": 30,
    "bm_freshness.py": 1,
    "bm_gate.py": 3,
    "bm_handover.py": 2,
    "bm_hookbench.py": 5,
    "bm_hookchain.py": 4,
    "bm_idle.py": 6,
    "bm_lead.py": 2,
    "bm_learn.py": 2,
    "bm_lint_walltime.py": 4,
    "bm_packs.py": 2,
    "bm_passport.py": 16,
    "bm_passport_validator.py": 4,
    "bm_plan.py": 2,
    "bm_profile.py": 1,
    "bm_progress_check.py": 4,
    "bm_project.py": 2,
    "bm_reality.py": 37,
    "bm_reconcile.py": 2,
    "bm_reconcile_worktrees.py": 2,
    "bm_release_invariant.py": 4,
    "bm_repo_scope.py": 5,
    "bm_runtimes.py": 2,
    "bm_sentinel.py": 2,
    "bm_session_cap.py": 1,
    "bm_sessionstart.py": 1,
    "bm_stall.py": 2,
    "bm_statusline.py": 1,
    "bm_summary.py": 2,
    "bm_threads.py": 3,
    "bm_toolkit.py": 8,
    "bm_vault.py": 17,
    "bm_vault_assertions.py": 11,
    "bm_vault_audit.py": 4,
    "bm_vault_catalog.py": 5,
    "bm_vault_cite.py": 8,
    "bm_vault_cli.py": 11,
    "bm_vault_contract.py": 1,
    "bm_vault_distill.py": 1,
    "bm_vault_enrich.py": 1,
    "bm_vault_events.py": 1,
    "bm_vault_graph.py": 1,
    "bm_vault_heat_temporal.py": 4,
    "bm_vault_hierarchy_req.py": 1,
    "bm_vault_intake.py": 1,
    "bm_vault_labels.py": 2,
    "bm_vault_ledger.py": 4,
    "bm_vault_lint.py": 2,
    "bm_vault_lock.py": 2,
    "bm_vault_pane.py": 4,
    "bm_vault_policy.py": 1,
    "bm_vault_posture.py": 1,
    "bm_vault_promote.py": 1,
    "bm_vault_provenance.py": 3,
    "bm_vault_read_audit.py": 3,
    "bm_vault_retention.py": 1,
    "bm_vault_retier.py": 1,
    "bm_vault_serve.py": 8,
    "bm_vault_temporal.py": 1,
    "bm_vault_tiers.py": 1,
    "bm_view.py": 2,
    "brothermode_cli.py": 2,
    "vault_client.py": 1,
    "vault_recall_hook.py": 3,
}


SANITIZERS = ("one_line", "safe_display")
PRINT_KEYWORDS = ("file", "flush")
STREAMS = ("stdout", "stderr")
NUMERIC_CONVERSIONS = set("diouxXeEfFgG%")
_CONVERSION_RE = re.compile(
    r"%(?:\([^)]*\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?(.)")
WHY = ("prints a line that never passed through bm_learning.one_line(); route "
       "the whole formatted line through bm_learning.say() (or print a bare "
       "constant)")
WRITE_WHY = ("writes a line that never passed through bm_learning.one_line(); "
             "write one_line(line) + \"\\n\" with the line end outside it, or "
             "call bm_learning.say(line, file=sys.stderr)")


def _numeric_only(fmt):
    """True when every %-conversion in fmt is numeric, so no value can add text
    of its own: %d of a string raises rather than printing it. %c is refused
    (an int becomes any character, a line break included), and so are %s, %r
    and %a."""
    return all(c in NUMERIC_CONVERSIONS for c in _CONVERSION_RE.findall(fmt))


def _const_str(node):
    """A string literal, a + chain of string literals, or a literal formatted
    with numeric conversions only (a help text carrying a window size)."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _const_str(node.left) and _const_str(node.right)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
                and _numeric_only(node.left.value))
    return False


def _constant_names(tree):
    """Names every binding of which, anywhere in this file, assigns a string
    constant. Scope is deliberately ignored: if no binding of the name in the
    whole file can carry outside text, no read of it can either. A parameter,
    a loop target, an import alias, an except alias, a def name, an augmented
    or unpacked assignment of the same name all disqualify it."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    good, bad = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            parent = parents.get(node)
            if (isinstance(parent, ast.Assign) and node in parent.targets
                    and _const_str(parent.value)):
                good.add(node.id)
            elif (isinstance(parent, ast.AnnAssign) and parent.target is node
                    and parent.value is not None and _const_str(parent.value)):
                good.add(node.id)
            else:
                bad.add(node.id)
        elif isinstance(node, ast.arg):
            bad.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bad.add(node.name)
        elif isinstance(node, ast.alias):
            bad.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bad.add(node.name)
    return good - bad, "__doc__" not in (good | bad)


def _callee(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _safe_expr(node, names, doc_ok):
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in names or (node.id == "__doc__" and doc_ok)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _safe_expr(node.left, names, doc_ok) and _safe_expr(node.right, names, doc_ok)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)
                and _numeric_only(node.left.value))
    if isinstance(node, ast.IfExp):
        return _safe_expr(node.body, names, doc_ok) and _safe_expr(node.orelse, names, doc_ok)
    if isinstance(node, ast.Call):
        name = _callee(node.func)
        if name in SANITIZERS:
            return True
        # ascii() of a %-formatted string: the operand is always a str, whose
        # repr escapes every line break and control character and ascii()
        # every non-ASCII one, so the result is one printable ASCII line. The
        # guarded say() a partial deployment degrades to prints this way. Any
        # other operand could be an object whose own __repr__ returns a break.
        if (isinstance(node.func, ast.Name) and node.func.id == "ascii"
                and len(node.args) == 1 and not node.keywords
                and isinstance(node.args[0], ast.BinOp)
                and isinstance(node.args[0].op, ast.Mod)
                and isinstance(node.args[0].left, ast.Constant)
                and isinstance(node.args[0].left.value, str)):
            return True
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "dumps"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "json"):
            for kw in node.keywords:
                if kw.arg is None:
                    return False
                if kw.arg == "ensure_ascii" and not (
                        isinstance(kw.value, ast.Constant) and kw.value.value is True):
                    return False
            return True
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr in ("strip", "rstrip", "lstrip")
                and not node.args and not node.keywords):
            return _safe_expr(node.func.value, names, doc_ok)
    return False


def _print_ok(node, names, doc_ok):
    if any(kw.arg not in PRINT_KEYWORDS for kw in node.keywords):
        return False
    if not node.args:
        return True
    if len(node.args) != 1 or isinstance(node.args[0], ast.Starred):
        return False
    return _safe_expr(node.args[0], names, doc_ok)


def _print_refused(node, names, doc_ok):
    return (isinstance(node.func, ast.Name) and node.func.id == "print"
            and not _print_ok(node, names, doc_ok))


def _stream_write(node):
    """sys.stdout.write(...) or sys.stderr.write(...), spelled that way."""
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr == "write"
            and isinstance(f.value, ast.Attribute) and f.value.attr in STREAMS
            and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys")


def _write_refused(node, names, doc_ok):
    """A stream write carries exactly one positional argument, held to the
    print predicate itself: the line end it adds on purpose is a constant, so
    one_line(x) + "\\n" passes and "%s\\n" % x does not."""
    return _stream_write(node) and not (
        len(node.args) == 1 and not node.keywords
        and _safe_expr(node.args[0], names, doc_ok))


def flagged_prints(tools_dir=HERE):
    """Every print() in a shipping module of tools_dir that the rule refuses.

    Returns (findings, unparsed, scanned): findings is [(file, line, why)],
    unparsed is [(file, why)] for a module this lint could not read (never a
    pass: its prints are invisible), scanned is how many modules were read."""
    return _flagged(tools_dir, _print_refused, WHY)


def flagged_writes(tools_dir=HERE):
    """Every sys.stdout.write or sys.stderr.write the rule refuses, in the same
    shape as flagged_prints."""
    return _flagged(tools_dir, _write_refused, WRITE_WHY)


def _flagged(tools_dir, refused, why_text):
    findings, unparsed, scanned = [], [], 0
    for fn in sorted(os.listdir(tools_dir)):
        if not fn.endswith(".py") or fn.startswith("test_"):
            continue
        try:
            with io.open(os.path.join(tools_dir, fn), encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=fn)
        except (SyntaxError, OSError, ValueError) as e:
            unparsed.append((fn, "could not be parsed (%s: %s), so its print "
                                 "and write sites are invisible to this lint"
                             % (type(e).__name__, e)))
            continue
        scanned += 1
        names, doc_ok = _constant_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and refused(node, names, doc_ok):
                findings.append((fn, node.lineno, why_text))
    return sorted(findings), unparsed, scanned


def reconcile(findings, unparsed, scanned, exempt, debt, kind="print"):
    """Every failure the lint owes, as sentences. An empty list is the only pass.

    kind names the channel ("print" or "write") and so the lists a sentence
    tells the reader to edit."""
    lists = kind.upper()
    failures = []
    if scanned == 0:
        failures.append("the lint opened no module, which is NO-DATA and never a "
                        "clean result")
    for fn, why in unparsed:
        failures.append("%s %s" % (fn, why))
    counts = {}
    for fn, lineno, why in findings:
        counts[fn] = counts.get(fn, 0) + 1
        if fn not in exempt and fn not in debt:
            failures.append("%s:%s %s" % (fn, lineno, why))
    for fn in sorted(set(exempt) & set(debt)):
        failures.append("%s is both exempt and owed; a reviewed file owes nothing, "
                        "so keep one entry" % fn)
    for fn in sorted(exempt):
        if counts.get(fn, 0) == 0:
            failures.append("%s_EXEMPT names %s but no %s there needed "
                            "excusing; a review that excuses nothing is dead, "
                            "remove the entry" % (lists, fn, kind))
    for fn, owed in sorted(debt.items()):
        actual = counts.get(fn, 0)
        if actual == 0:
            failures.append("%s_DEBT names %s but it owes no raw %s any more; "
                            "remove the entry" % (lists, fn, kind))
        elif actual > owed:
            failures.append("%s now has %d raw %ss against a ratchet of %d; a "
                            "new %s must go through bm_learning.one_line()"
                            % (fn, actual, kind, owed, kind))
        elif actual < owed:
            failures.append("%s has %d raw %ss against a ratchet of %d; lower "
                            "%s_DEBT[%r] to %d so the ratchet stays tight"
                            % (fn, actual, kind, owed, lists, fn, actual))
    return failures


class RealTree(unittest.TestCase):
    """The subject is the real tools directory, never a fixture."""

    def test_every_print_is_flat_or_owed(self):
        findings, unparsed, scanned = flagged_prints()
        self.assertGreater(scanned, 100, "the lint read %d modules" % scanned)
        self.assertEqual([], reconcile(findings, unparsed, scanned,
                                       PRINT_EXEMPT, PRINT_DEBT))

    def test_every_stream_write_is_flat_or_owed(self):
        findings, unparsed, scanned = flagged_writes()
        self.assertGreater(scanned, 100, "the lint read %d modules" % scanned)
        self.assertEqual([], reconcile(findings, unparsed, scanned,
                                       WRITE_EXEMPT, WRITE_DEBT, kind="write"))


class LintShapes(unittest.TestCase):
    """Each fixture isolates ONE condition the rule decides on."""

    def flagged(self, source):
        with tempfile.TemporaryDirectory() as d:
            with io.open(os.path.join(d, "bm_fixture.py"), "w", encoding="utf-8") as fh:
                fh.write(source)
            findings, unparsed, scanned = flagged_prints(d)
        self.assertEqual((1, []), (scanned, unparsed))
        return len(findings)

    def test_raw_interpolation_is_flagged(self):
        self.assertEqual(1, self.flagged('import sys\nprint("path %s" % sys.argv[1])\n'))

    def test_bare_constant_passes(self):
        self.assertEqual(0, self.flagged('print("header")\nprint()\n'))

    def test_one_line_call_passes(self):
        self.assertEqual(0, self.flagged('import bm_learning as L\nprint(L.one_line("a %s" % 1))\n'))

    def test_safe_display_call_passes(self):
        self.assertEqual(0, self.flagged('import bm_learning as L\nprint(L.safe_display(x, 2000))\n'))

    def test_numeric_only_format_passes(self):
        self.assertEqual(0, self.flagged('n = 3\nprint("found %d notes, %.1f%%" % (n, 2.0))\n'))

    def test_char_conversion_is_flagged(self):
        self.assertEqual(1, self.flagged('print("%c" % 10)\n'))

    def test_repr_conversion_is_flagged(self):
        self.assertEqual(1, self.flagged('print("%r" % (x,))\n'))

    def test_file_keyword_passes(self):
        self.assertEqual(0, self.flagged('import sys\nprint("x", file=sys.stderr)\n'))

    def test_sep_keyword_is_flagged(self):
        self.assertEqual(1, self.flagged('print("x", sep="\\n")\n'))

    def test_two_arguments_are_flagged(self):
        self.assertEqual(1, self.flagged('print("a", "b")\n'))

    def test_json_dumps_passes(self):
        self.assertEqual(0, self.flagged('import json\nprint(json.dumps({"a": x}, indent=2))\n'))

    def test_json_dumps_without_ascii_is_flagged(self):
        self.assertEqual(1, self.flagged('import json\nprint(json.dumps(x, ensure_ascii=False))\n'))

    def test_json_dumps_with_unpacked_kwargs_is_flagged(self):
        self.assertEqual(1, self.flagged('import json\nprint(json.dumps(x, **opts))\n'))

    def test_constant_name_passes(self):
        self.assertEqual(0, self.flagged('HELP = "usage: x"\nprint(HELP)\nprint(__doc__)\n'))

    def test_name_bound_to_a_numeric_format_passes(self):
        self.assertEqual(0, self.flagged('N = 4\nHELP = """usage\n  last %d runs\n""" % N\nprint(HELP)\n'))

    def test_name_bound_to_a_text_format_is_flagged(self):
        self.assertEqual(1, self.flagged('HELP = "usage for %s" % sys.argv[0]\nprint(HELP)\n'))

    def test_rebound_name_is_flagged(self):
        self.assertEqual(1, self.flagged('HELP = "usage"\ndef f(HELP):\n    print(HELP)\n'))

    def test_name_bound_by_a_loop_is_flagged(self):
        self.assertEqual(1, self.flagged('MSG = "a"\nfor MSG in rows:\n    pass\nprint(MSG)\n'))

    def test_rebound_doc_is_flagged(self):
        self.assertEqual(1, self.flagged('__doc__ = sys.argv[1]\nprint(__doc__)\n'))

    def test_strip_of_a_constant_passes(self):
        self.assertEqual(0, self.flagged('print(__doc__.strip())\n'))

    def test_strip_of_a_value_is_flagged(self):
        self.assertEqual(1, self.flagged('print(text.strip())\n'))

    def test_ascii_of_a_formatted_string_passes(self):
        self.assertEqual(0, self.flagged('print(ascii("%s" % (line,)), file=f)\n'))

    def test_ascii_of_an_arbitrary_object_is_flagged(self):
        self.assertEqual(1, self.flagged('print(ascii(obj))\n'))

    def test_conditional_of_constants_passes(self):
        self.assertEqual(0, self.flagged('print("a" if ok else "b")\n'))

    def test_conditional_with_a_value_is_flagged(self):
        self.assertEqual(1, self.flagged('print(msg if ok else "b")\n'))

    def test_constant_concatenation_passes_and_value_concatenation_does_not(self):
        self.assertEqual(0, self.flagged('print("a" + "b")\n'))
        self.assertEqual(1, self.flagged('print("a" + msg)\n'))

    def test_test_files_are_not_scanned(self):
        with tempfile.TemporaryDirectory() as d:
            with io.open(os.path.join(d, "test_x.py"), "w", encoding="utf-8") as fh:
                fh.write('print("%s" % x)\n')
            self.assertEqual(([], [], 0), flagged_prints(d))

    def test_an_unparseable_module_is_reported_not_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            with io.open(os.path.join(d, "bm_broken.py"), "w", encoding="utf-8") as fh:
                fh.write("def (:\n")
            findings, unparsed, scanned = flagged_prints(d)
        self.assertEqual(0, scanned)
        self.assertEqual(["bm_broken.py"], [fn for fn, _ in unparsed])


class WriteShapes(unittest.TestCase):
    """The stream write channel. Each fixture isolates ONE condition: a shape
    the shared predicate accepts or refuses, or one part of the receiver match
    that alone decides whether a call is a stream write at all."""

    def flagged(self, source):
        with tempfile.TemporaryDirectory() as d:
            with io.open(os.path.join(d, "bm_fixture.py"), "w", encoding="utf-8") as fh:
                fh.write("import json\nimport sys\nimport bm_learning as L\n" + source)
            findings, unparsed, scanned = flagged_writes(d)
        self.assertEqual((1, []), (scanned, unparsed))
        return len(findings)

    # Accepted: the whole line through one_line(), the line end outside it.
    def test_constant_passes(self):
        self.assertEqual(0, self.flagged('sys.stdout.write("done\\n")\n'))

    def test_one_line_plus_line_end_passes(self):
        self.assertEqual(0, self.flagged('sys.stdout.write(L.one_line("a %s" % x) + "\\n")\n'))

    def test_safe_display_plus_line_end_passes(self):
        self.assertEqual(0, self.flagged('sys.stderr.write(L.safe_display(x) + "\\n")\n'))

    def test_numeric_only_format_passes(self):
        self.assertEqual(0, self.flagged('sys.stderr.write("found %d notes\\n" % n)\n'))

    def test_constant_name_passes(self):
        self.assertEqual(0, self.flagged('USAGE = "usage: x\\n"\nsys.stderr.write(USAGE)\n'))

    def test_json_dumps_plus_line_end_passes(self):
        self.assertEqual(0, self.flagged('sys.stdout.write(json.dumps(x) + "\\n")\n'))

    def test_conditional_of_constants_passes(self):
        self.assertEqual(0, self.flagged('sys.stdout.write("a\\n" if ok else "b\\n")\n'))

    def test_say_to_stderr_is_not_a_write(self):
        self.assertEqual(0, self.flagged('L.say("x %s" % v, file=sys.stderr)\n'))

    # Refused: one per AST shape measured in the real tree.
    def test_text_format_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stderr.write("could not read %s\\n" % path)\n'))

    def test_value_concatenation_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write(msg + "\\n")\n'))

    def test_bare_value_name_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write(text)\n'))

    def test_conditional_with_a_value_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write(msg if ok else "b\\n")\n'))

    def test_call_that_is_no_sanitizer_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write(str(e))\n'))

    def test_attribute_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write(args.text)\n'))

    def test_one_line_inside_a_text_format_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write("%s\\n" % L.one_line(x))\n'))

    # The call itself: argument count and keywords.
    def test_two_arguments_are_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write("a", "b")\n'))

    def test_a_keyword_is_flagged(self):
        self.assertEqual(1, self.flagged('sys.stdout.write("a", end="")\n'))

    # The receiver: only sys.stdout.write and sys.stderr.write are read.
    def test_stdout_is_read(self):
        self.assertEqual(1, self.flagged('sys.stdout.write(x)\n'))

    def test_stderr_is_read(self):
        self.assertEqual(1, self.flagged('sys.stderr.write(x)\n'))

    def test_another_method_on_a_stream_is_not_a_write(self):
        self.assertEqual(0, self.flagged('sys.stdout.flush()\n'))

    def test_a_file_handle_write_is_not_a_stream_write(self):
        self.assertEqual(0, self.flagged('fh.write("%s\\n" % x)\n'))

    def test_another_sys_attribute_is_not_a_stream(self):
        self.assertEqual(0, self.flagged('sys.stdin.write(x)\n'))

    def test_another_objects_stdout_is_not_sys(self):
        self.assertEqual(0, self.flagged('log.stdout.write(x)\n'))

    def test_a_deeper_receiver_is_not_sys(self):
        self.assertEqual(0, self.flagged('ctx.env.stdout.write(x)\n'))


class Reconcile(unittest.TestCase):
    """Each list entry fails the moment it stops describing the tree."""

    F = [("a.py", 3, WHY), ("a.py", 9, WHY), ("b.py", 1, WHY)]

    def test_owed_counts_that_match_pass(self):
        self.assertEqual([], reconcile(self.F, [], 2, {}, {"a.py": 2, "b.py": 1}))

    def test_unlisted_raw_print_fails(self):
        out = reconcile(self.F, [], 2, {}, {"a.py": 2})
        self.assertEqual(1, len(out))
        self.assertIn("b.py:1", out[0])

    def test_a_new_raw_print_in_an_owing_file_fails(self):
        out = reconcile(self.F, [], 2, {}, {"a.py": 1, "b.py": 1})
        self.assertEqual(1, len(out))
        self.assertIn("now has 2 raw prints against a ratchet of 1", out[0])

    def test_a_loose_ratchet_fails(self):
        out = reconcile(self.F, [], 2, {}, {"a.py": 5, "b.py": 1})
        self.assertEqual(1, len(out))
        self.assertIn("lower PRINT_DEBT['a.py'] to 2", out[0])

    def test_a_paid_debt_entry_is_dead(self):
        out = reconcile(self.F, [], 2, {}, {"a.py": 2, "b.py": 1, "c.py": 4})
        self.assertEqual(1, len(out))
        self.assertIn("PRINT_DEBT names c.py", out[0])

    def test_an_exemption_that_excuses_something_passes(self):
        self.assertEqual([], reconcile(self.F, [], 2, {"a.py": "reviewed"}, {"b.py": 1}))

    def test_an_exemption_that_excuses_nothing_is_dead(self):
        out = reconcile(self.F, [], 2, {"z.py": "reviewed"}, {"a.py": 2, "b.py": 1})
        self.assertEqual(1, len(out))
        self.assertIn("PRINT_EXEMPT names z.py", out[0])

    def test_a_file_both_exempt_and_owed_fails(self):
        out = reconcile(self.F, [], 2, {"a.py": "reviewed"}, {"a.py": 2, "b.py": 1})
        self.assertIn("a.py is both exempt and owed", " ".join(out))

    def test_no_module_read_is_no_data_never_a_pass(self):
        self.assertIn("NO-DATA", " ".join(reconcile([], [], 0, {}, {})))

    def test_an_unparsed_module_fails(self):
        out = reconcile([], [("a.py", "could not be parsed")], 1, {}, {})
        self.assertEqual(["a.py could not be parsed"], out)

    # The write channel names its own lists, so a reader edits the right one.
    def test_write_rising_count_names_writes(self):
        out = reconcile(self.F, [], 2, {}, {"a.py": 1, "b.py": 1}, kind="write")
        self.assertEqual(1, len(out))
        self.assertIn("now has 2 raw writes against a ratchet of 1", out[0])

    def test_write_loose_ratchet_names_write_debt(self):
        out = reconcile(self.F, [], 2, {}, {"a.py": 5, "b.py": 1}, kind="write")
        self.assertEqual(1, len(out))
        self.assertIn("lower WRITE_DEBT['a.py'] to 2", out[0])

    def test_write_paid_debt_names_write_debt(self):
        out = reconcile(self.F, [], 2, {}, {"a.py": 2, "b.py": 1, "c.py": 4}, kind="write")
        self.assertEqual(1, len(out))
        self.assertIn("WRITE_DEBT names c.py but it owes no raw write", out[0])

    def test_write_dead_exemption_names_write_exempt(self):
        out = reconcile(self.F, [], 2, {"z.py": "reviewed"}, {"a.py": 2, "b.py": 1},
                        kind="write")
        self.assertEqual(1, len(out))
        self.assertIn("WRITE_EXEMPT names z.py but no write there", out[0])


class OneLine(unittest.TestCase):
    """bm_learning.one_line: the whole formatted line, flattened, nothing lost."""

    def test_every_line_boundary_becomes_a_visible_break(self):
        for brk in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029":
            out = L.one_line("path" + brk + "PASS forged")
            self.assertEqual(1, len(out.splitlines()), repr(brk))
            self.assertEqual("path \\n PASS forged", out, repr(brk))

    def test_a_run_of_breaks_is_one_visible_break(self):
        self.assertEqual("a \\n b", L.one_line("a\r\n\nb"))

    def test_c1_csi_is_escaped_visibly(self):
        self.assertEqual("x\\x9b1Ay", L.one_line("x\x9b1Ay"))

    def test_escape_is_escaped_visibly(self):
        self.assertEqual("x\\x1b[1Fy", L.one_line("x\x1b[1Fy"))

    def test_bidi_override_is_escaped_visibly(self):
        self.assertEqual("x\\u202ey", L.one_line("x\u202ey"))

    def test_lone_surrogate_is_escaped_so_print_cannot_raise(self):
        out = L.one_line("bad\udcffname")
        self.assertEqual("bad\\udcffname", out)
        out.encode("utf-8")

    def test_astral_format_character_uses_the_long_escape(self):
        self.assertEqual("x\\U000e0001y", L.one_line("x\U000e0001y"))

    def test_tab_becomes_a_space(self):
        self.assertEqual("a b", L.one_line("a\tb"))

    def test_indentation_and_column_padding_survive(self):
        self.assertEqual("    note.md      12", L.one_line("    %-12s %2d" % ("note.md", 12)))

    def test_ordinary_text_is_unchanged(self):
        s = "NO-DATA: could not read /tmp/x (\u65e5\u672c, caf\u00e9, 100%)"
        self.assertEqual(s, L.one_line(s))

    def test_a_non_string_is_printed_as_print_would(self):
        self.assertEqual("None", L.one_line(None))
        self.assertEqual("42", L.one_line(42))


class Say(unittest.TestCase):
    """bm_learning.say: one print, one line, to the stream asked for."""

    def test_say_writes_exactly_one_line_to_stdout(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            L.say("    %s" % "notes/a.md\nNO-DATA forged")
        self.assertEqual("    notes/a.md \\n NO-DATA forged\n", buf.getvalue())

    def test_say_honours_file(self):
        buf = io.StringIO()
        L.say("err: %s" % "x\ry", file=buf)
        self.assertEqual("err: x \\n y\n", buf.getvalue())

    def test_say_on_a_console_that_cannot_encode_still_prints(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="ascii", errors="strict")
        L.say("caf\u00e9 done", file=stream)
        stream.flush()
        self.assertEqual(b"caf\\xe9 done\n", raw.getvalue())


class ConvertedModulesHoldTheLine(unittest.TestCase):
    """End to end, per converted module: a hostile value from the store or the
    disk reaches the real print path and still prints as ONE line, with the
    break visible. The lint proves every print routes through say(); these
    prove say() is the real choke point there and not a degraded copy."""

    FORGED = "NO-DATA forged under the real line"

    def assert_no_forged_line(self, out):
        for line in out.splitlines():
            self.assertFalse(line.lstrip().startswith(self.FORGED), out)
        self.assertNotIn("\x1b", out)
        self.assertIn("\\n " + self.FORGED, out)

    def test_bm_vault_recall_prints_a_hostile_note_path_on_one_line(self):
        import sqlite3
        vault = _load("bm_vault")
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        vault._schema(con)
        con.execute(
            "INSERT INTO notes (id, path, title, descr, source, kind, mtime, body) "
            "VALUES (1, ?, ?, 'a note', 'vault', 'lesson', 0, 'body')",
            ("notes/a.md\n" + self.FORGED, "title\x1b[1F"))
        con.commit()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                vault._print_hits(con, [(1, 1.0)], {1: ["symptom"]}, "HEADER:")
        finally:
            con.close()
        self.assert_no_forged_line(buf.getvalue())
        self.assertEqual("bm_learning.py", os.path.basename(vault.say.__code__.co_filename),
                         "bm_vault.say is the degraded copy, not bm_learning.say")


    def test_bm_telemetry_compact_hint_holds_a_hostile_cwd_and_brief(self):
        """The SessionStart compact hint is injected into a model's context: a
        cwd from the hook payload and a resume brief another session wrote
        both reach it."""
        saved = os.environ.get("BROTHERMODE_VAULT")
        with tempfile.TemporaryDirectory() as vault_dir:
            os.environ["BROTHERMODE_VAULT"] = vault_dir
            try:
                tel = _load("bm_telemetry")
                cwd = os.path.join(vault_dir, "repo\n" + self.FORGED)
                rp = tel._resume_path(cwd)
                os.makedirs(os.path.dirname(rp), exist_ok=True)
                with io.open(rp, "w", encoding="utf-8") as fh:
                    fh.write("brief line one\n\x1b[2Jcleared the screen\nbrief line three\n")
                payload = '{"source": "compact", "cwd": %s, "session_id": "s1"}' % (
                    __import__("json").dumps(cwd),)
                buf = io.StringIO()
                saved_stdin = sys.stdin
                sys.stdin = io.StringIO(payload)
                try:
                    with contextlib.redirect_stdout(buf):
                        tel.cmd_compact_hint()
                finally:
                    sys.stdin = saved_stdin
            finally:
                if saved is None:
                    os.environ.pop("BROTHERMODE_VAULT", None)
                else:
                    os.environ["BROTHERMODE_VAULT"] = saved
        out = buf.getvalue()
        self.assert_no_forged_line(out)
        self.assertIn("brief line one\n", out)
        self.assertIn("\n\\x1b[2Jcleared the screen\nbrief line three\n", out)


class SafeDisplayWidened(unittest.TestCase):
    """safe_display strips C1 controls, format characters and surrogates too,
    while keeping its old contract: one line, collapsed spaces, capped."""

    def test_c1_csi_is_stripped(self):
        self.assertEqual("x1Ay", L.safe_display("x\x9b1Ay"))

    def test_bidi_override_is_stripped(self):
        self.assertEqual("xy", L.safe_display("x\u202ey"))

    def test_zero_width_and_bom_are_stripped(self):
        self.assertEqual("ab", L.safe_display("\ufeffa\u200bb"))

    def test_lone_surrogate_is_stripped(self):
        self.assertEqual("ab", L.safe_display("a\udcffb"))

    def test_nel_still_becomes_a_space(self):
        self.assertEqual("a b", L.safe_display("a\x85b"))

    def test_old_contract_holds(self):
        self.assertEqual("a b c", L.safe_display("  a\n\tb\x1b  c  "))
        self.assertEqual("x" * 7 + "...", L.safe_display("x" * 20, 10))
        self.assertEqual("", L.safe_display(None))

    def test_ordinary_non_ascii_survives(self):
        self.assertEqual("\u65e5\u672c caf\u00e9", L.safe_display("\u65e5\u672c caf\u00e9"))


if __name__ == "__main__":
    unittest.main()
