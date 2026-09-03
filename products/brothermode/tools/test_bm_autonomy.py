#!/usr/bin/env python3
"""Regression tests for tools/bm_autonomy.py, the U1 autonomy contract CLI
(docs/AUTONOMY.md; design docs/superpowers/specs/2026-08-05-u1-autonomy-
contract-design.md).

Every test drives tools/bm_autonomy.py as a real SUBPROCESS against a fresh
tempfile.TemporaryDirectory() root (BROTHERMODE_ROOT), never against this
repo's own store, exactly the discipline tools/test_bm_project.py already
uses. HOME and BROTHERMODE_VAULT are ALSO pointed at fresh temp locations in
every subprocess env, so a test can never read or write the real founder's
home directory or vault even if some code path this file does not exercise
today starts consulting them.

Python 3.9, standard library only. Run: python3 tools/test_bm_autonomy.py

No em or en dashes anywhere in this file, its comments, or its output.
"""
import ast
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "bm_store", os.path.join(HERE, "bm_store.py"))
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)
sys.modules["bm_store"] = bs

AUTONOMY_CLI = os.path.join(HERE, "bm_autonomy.py")
PROJECT_CLI = os.path.join(HERE, "bm_project.py")
STORE_CLI = os.path.join(HERE, "bm_store.py")

ACTOR = ("--actor-name", "tester")


def _env(root):
    """A fresh environment for one subprocess call: BROTHERMODE_ROOT,
    BROTHERMODE_VAULT and HOME are all pointed at directories under this
    same throwaway root, so nothing this suite runs can touch the real
    founder's home directory or vault."""
    home = os.path.join(root, "home")
    vault = os.path.join(root, "vault")
    os.makedirs(home, exist_ok=True)
    e = dict(os.environ)
    for key in ("BROTHERMODE_ROOT", "BROTHERMODE_VAULT", "HOME"):
        e.pop(key, None)
    e["BROTHERMODE_ROOT"] = root
    e["BROTHERMODE_VAULT"] = vault
    e["HOME"] = home
    return e


def _run(args, root, extra_env=None):
    """Drive tools/bm_autonomy.py as a real subprocess against `root`."""
    e = _env(root)
    if extra_env:
        e.update(extra_env)
    return subprocess.run([sys.executable, AUTONOMY_CLI] + list(args),
                          cwd=root, capture_output=True, text=True, env=e)


def _init(root):
    e = _env(root)
    r = subprocess.run([sys.executable, STORE_CLI, "init"],
                       cwd=root, capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise AssertionError("bm_store.py init failed: %s" % r.stderr)
    return r


def _start_project(root, project_id="p1"):
    e = _env(root)
    r = subprocess.run(
        [sys.executable, PROJECT_CLI, "start", "--project-id", project_id,
         "--name", "Test Project"] + list(ACTOR),
        cwd=root, capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise AssertionError("bm_project.py start failed: %s" % r.stderr)
    return r


def _bootstrap(root, project_id="p1"):
    """A ready-to-sign store: initialized, one project started."""
    _init(root)
    _start_project(root, project_id)


def _sign(root, project_id="p1", signed_by="Khalil Maaouni", extra=()):
    args = ["sign", "--project", project_id, "--outcome", "ship it",
           "--done-definition", "tests pass", "--signed-by", signed_by]
    args += list(ACTOR)
    args += list(extra)
    r = _run(args, root)
    if r.returncode != 0:
        raise AssertionError("sign failed: %s / %s" % (r.stdout, r.stderr))
    return r


def _raw_dump(root):
    """The unredacted store contents, for asserting what actually landed at
    the row level, never for driving assertions about what a founder would
    SEE (that is what the CLI's own stdout is for)."""
    store = bs.Store(root, create=False)
    try:
        return store.dump(raw=True)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# The structural no-SQL guard, copied from tools/test_bm_project.py's
# TestNoSQLGuard and pointed at tools/bm_autonomy.py instead.
# ---------------------------------------------------------------------------

def _docstring_constant_ids(tree):
    """id() of every ast.Constant node ast.get_docstring would return the
    value of: the first statement of a Module/FunctionDef/
    AsyncFunctionDef/ClassDef body, when it is a bare string expression.
    Verbatim structural copy of tools/test_bm_project.py's own helper."""
    ids = set()
    doc_holders = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                  ast.ClassDef)
    for node in ast.walk(tree):
        if isinstance(node, doc_holders) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                ids.add(id(first.value))
    return ids


class TestNoSQLGuard(unittest.TestCase):
    """bm_autonomy.py's own module docstring states the same D-1 flip
    condition tools/bm_project.py's does: the moment this file needs a
    query the store does not already offer, it has become a second writer
    and must be folded back into bm_store.py instead of growing one here.
    Copied structurally from tools/test_bm_project.py's TestNoSQLGuard,
    including the calibration test that proves the AST approach actually
    earns its complexity over a naive regex."""

    _SQL_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|PRAGMA)\b",
                         re.IGNORECASE)

    def test_bm_autonomy_never_issues_its_own_sql(self):
        with io.open(AUTONOMY_CLI, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename="bm_autonomy.py")
        skip = _docstring_constant_ids(tree)
        hits = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in skip
                    and self._SQL_RE.search(node.value)):
                hits.append((node.lineno, node.value))
        self.assertEqual(
            hits, [],
            "bm_autonomy.py must never contain a string literal shaped "
            "like SQL (found %r); a query the store does not already "
            "offer means this file has become a second writer and the "
            "query belongs in bm_store.py instead." % hits)

    def test_bm_autonomy_never_imports_sqlite3(self):
        with io.open(AUTONOMY_CLI, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, filename="bm_autonomy.py")
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders.extend(a.name for a in node.names
                                 if a.name.split(".")[0] == "sqlite3")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "sqlite3":
                    offenders.append(node.module)
        self.assertEqual(
            offenders, [],
            "bm_autonomy.py must never import sqlite3 directly (found "
            "%r); every query it needs already goes through bs, the "
            "loaded bm_store module." % offenders)

    def test_calibrated_old_guard_missed_a_lowercase_or_split_bypass(self):
        """CALIBRATION. Reproduces the pre-fix guard tools/test_bm_project.py
        itself replaced (a bare uppercase-only regex over raw text) against a
        synthetic snippet standing in for what a change to bm_autonomy.py
        could slip in, proving it would have said nothing was wrong, then
        proves the current AST-based guard catches both bypasses in the
        same snippet."""
        bypass_snippet = (
            'query = "sel" "ect * from autonomy_contracts"\n'
            'other = "update autonomy_contracts set state=?"\n'
        )
        old_style_hits = re.findall(
            r"\b(SELECT|INSERT|UPDATE|DELETE)\b", bypass_snippet)
        self.assertEqual(
            old_style_hits, [],
            "calibration sanity: the OLD guard must find nothing in this "
            "snippet, or it does not actually demonstrate the bypass")
        tree = ast.parse(bypass_snippet)
        hits = [n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and self._SQL_RE.search(n.value)]
        self.assertEqual(
            len(hits), 2,
            "the AST-based guard must catch both the split literal and "
            "the lowercase keyword the old regex missed (got %r)" % hits)


# ---------------------------------------------------------------------------
# sign
# ---------------------------------------------------------------------------

class TestSign(unittest.TestCase):

    def test_sign_writes_a_live_revision_one_contract(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            self.assertIn("revision 1", r.stdout)
            self.assertIn("live", r.stdout)
            raw = _raw_dump(root)
            rows = raw["autonomy_contracts"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["state"], "live")
            self.assertEqual(rows[0]["revision"], 1)

    def test_sign_prints_a_gate_check_invocation_naming_no_repo_path(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _sign(root)
            self.assertIn("gate-check", r.stdout)
            line = [l for l in r.stdout.splitlines()
                   if l.startswith("check authorisation")][0]
            invocation = line.split(": ", 1)[1].split(" gate-check")[0]
            # bs.invocation() returns either the bare console-script name
            # ("bm-autonomy", when running from an environment that owns
            # it) or "python3 " plus an ABSOLUTE path (every subprocess
            # this test suite spawns, since none of them run from an
            # installed console-script environment). Never a bare,
            # cwd-relative "tools/bm_autonomy.py": that string legitimately
            # appears as the TAIL of the absolute-path form, which is the
            # portable, correct case this test must not reject.
            self.assertTrue(
                invocation == "bm-autonomy"
                or (invocation.startswith("python3 ")
                    and os.path.isabs(invocation.split(" ", 1)[1])),
                "unexpected invocation form: %r" % invocation)

    def test_sign_without_project_refuses_and_names_the_start_command(self):
        with tempfile.TemporaryDirectory() as root:
            _init(root)
            r = _run(["sign", "--project", "ghost", "--outcome", "x",
                     "--done-definition", "y", "--signed-by", "A Human"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no project", r.stderr)
            self.assertIn("start", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_contracts"], [])

    def test_sign_a_second_time_without_supersede_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o2",
                     "--done-definition", "d2", "--signed-by", "Khalil"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("already has a live contract", r.stderr)
            self.assertIn("supersede", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(len(raw["autonomy_contracts"]), 1)

    def test_sign_with_supersede_amends_to_revision_two(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o2",
                     "--done-definition", "d2", "--signed-by", "Khalil",
                     "--supersede"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("revision 2", r.stdout)
            raw = _raw_dump(root)
            self.assertEqual(len(raw["autonomy_contracts"]), 2)

    def test_a_dotdot_allowed_path_refuses_path_escape_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                     "--done-definition", "d", "--signed-by", "Khalil",
                     "--allowed-path", "../outside"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("resolves outside the project root", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_contracts"], [])

    def test_a_floor_named_as_a_risk_class_refuses_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                     "--done-definition", "d", "--signed-by", "Khalil",
                     "--risk-class", "payment"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("safety floor", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_contracts"], [])

    def test_an_unrecognized_risk_class_refuses_naming_the_allowed_set(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                     "--done-definition", "d", "--signed-by", "Khalil",
                     "--risk-class", "time-travel"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("time-travel", r.stderr)
            for c in bs.AUTONOMY_RISK_CLASSES:
                self.assertIn(c, r.stderr)

    def test_negative_token_ceiling_is_a_usage_error_not_a_refusal(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                     "--done-definition", "d", "--signed-by", "Khalil",
                     "--token-ceiling", "-1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("sign", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_contracts"], [])

    def test_a_non_numeric_minutes_ceiling_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                     "--done-definition", "d", "--signed-by", "Khalil",
                     "--minutes-ceiling", "soon"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)

    def test_missing_outcome_is_a_usage_error_naming_sign(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--done-definition", "d",
                     "--signed-by", "Khalil"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("sign", r.stderr)
            self.assertIn("outcome", r.stderr)


class TestSignerHonesty(unittest.TestCase):
    """Invariant I1, THE HONESTY TEST. The denylist is a speed bump against
    the accidental case, not an authenticity check, and a test that only
    proves the happy denylist teaches the next reader that the check is
    stronger than it is: this class asserts BOTH halves in one place, six
    refusals and two documented bypasses that are ACCEPTED on purpose."""

    MODEL_NAMES = ("Claude", "claude-opus-5", "GPT-5.6", "Fable 5",
                  "the assistant", "AI Agent")

    def test_six_model_names_all_refuse_and_write_nothing(self):
        for name in self.MODEL_NAMES:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as root:
                    _bootstrap(root)
                    r = _run(["sign", "--project", "p1", "--outcome", "o",
                             "--done-definition", "d", "--signed-by", name]
                            + list(ACTOR), root)
                    self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                    self.assertIn("model name", r.stderr)
                    raw = _raw_dump(root)
                    self.assertEqual(raw["autonomy_contracts"], [])

    def test_two_documented_bypasses_are_accepted_the_check_is_not_over_read(self):
        # "K. Maaouni" and "cl4ude" are the design's own named limits of the
        # denylist (a real person's initial, and a deliberate misspelling):
        # both must be ACCEPTED, or this test would be asserting a stronger
        # guarantee than the check actually provides.
        for name in ("K. Maaouni", "cl4ude"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as root:
                    _bootstrap(root)
                    r = _run(["sign", "--project", "p1", "--outcome", "o",
                             "--done-definition", "d", "--signed-by", name]
                            + list(ACTOR), root)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    raw = _raw_dump(root)
                    self.assertEqual(len(raw["autonomy_contracts"]), 1)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

class TestShow(unittest.TestCase):

    def test_show_without_a_contract_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["show", "--project", "p1"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no live contract", r.stderr)
            self.assertIn("sign", r.stderr)

    def test_show_prints_the_live_contract(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["show", "--project", "p1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("revision 1", r.stdout)
            self.assertIn("live", r.stdout)

    def test_show_all_lists_the_whole_revision_chain(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            _run(["pause", "--project", "p1", "--reason", "r"]
                + list(ACTOR), root)
            r = _run(["show", "--project", "p1", "--all"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("rev 1", r.stdout)
            self.assertIn("rev 2", r.stdout)

    def test_show_json_is_redacted_by_default_and_raw_reveals_prose(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--outcome", "very secret goal"))
            redacted = _run(["show", "--project", "p1", "--json"], root)
            self.assertEqual(redacted.returncode, 0)
            self.assertNotIn("very secret goal", redacted.stdout)
            raw = _run(["show", "--project", "p1", "--json", "--raw"], root)
            self.assertEqual(raw.returncode, 0)
            self.assertIn("very secret goal", raw.stdout)


# ---------------------------------------------------------------------------
# gate-check
# ---------------------------------------------------------------------------

class TestGateCheck(unittest.TestCase):

    def test_allowed_exits_zero_and_names_the_revision(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            r = _run(["gate-check", "--project", "p1", "--action-class",
                     "file-edit"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ALLOWED", r.stdout)
            self.assertIn("revision 1", r.stdout)

    def test_a_refusal_exits_one_never_zero(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "build",
                               "--allowed-path", "."))
            r = _run(["gate-check", "--project", "p1", "--action-class",
                     "file-edit"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("REFUSED-CLASS", r.stdout)

    def test_a_revoked_contract_refuses_authorization(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            _run(["revoke", "--project", "p1", "--reason", "done"]
                + list(ACTOR), root)
            r = _run(["gate-check", "--project", "p1", "--action-class",
                     "file-edit"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("REFUSED-STATE", r.stdout)
            self.assertIn("revoked", r.stdout)

    def test_a_floor_action_class_refuses_even_with_no_contract_at_all(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["gate-check", "--project", "p1", "--action-class",
                     "payment"], root)
            self.assertEqual(r.returncode, 1)
            # No contract exists at all, so this proves check 1
            # (REFUSED-NO-CONTRACT) runs before the floor is even reached,
            # which is fine: either refusal is correct, but the floor
            # itself must never appear as ALLOWED regardless of ordering.
            self.assertNotIn("ALLOWED", r.stdout)

    def test_json_output_carries_the_revision_for_the_u2_staleness_protocol(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            r = _run(["gate-check", "--project", "p1", "--action-class",
                     "file-edit", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["revision"], 1)
            self.assertEqual(payload["verdict"], "ALLOWED")


# ---------------------------------------------------------------------------
# assume / interrupt
# ---------------------------------------------------------------------------

class TestAssumeAndInterrupt(unittest.TestCase):

    def test_assume_without_a_live_contract_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["assume", "--project", "p1", "--text", "x"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("no live contract", r.stderr)

    def test_assume_records_a_row(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["assume", "--project", "p1", "--text",
                     "the API will not change", "--reversal", "revert"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(len(raw["autonomy_assumptions"]), 1)

    def test_interrupt_with_an_out_of_policy_condition_refuses_naming_all_four(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["interrupt", "--project", "p1", "--condition",
                     "seems-important", "--question", "ok?"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            for c in bs.AUTONOMY_CONDITIONS:
                self.assertIn(c, r.stderr)
            self.assertIn("run assume instead", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_interruptions"], [])

    def test_interrupt_with_a_real_condition_records_and_counts_open_ones(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["interrupt", "--project", "p1", "--condition",
                     "contradiction", "--question", "which wins?"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("target: zero to three", r.stdout)


# ---------------------------------------------------------------------------
# spend
# ---------------------------------------------------------------------------

class TestSpend(unittest.TestCase):

    def test_spend_requires_at_least_one_amount(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["spend", "--project", "p1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)

    def test_negative_tokens_is_a_usage_error_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["spend", "--project", "p1", "--tokens", "-500"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("-500", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_spend"], [])

    def test_all_zero_amounts_together_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["spend", "--project", "p1", "--tokens", "0",
                     "--minutes", "0"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_spend"], [])

    def test_astronomical_tokens_is_a_clean_usage_refusal_not_an_overflow(self):
        # A refuter found that a spend at or beyond the signed 64 bit maximum
        # raised a raw OverflowError past the CLI's handlers instead of a clean
        # refusal. The bound now catches it as a usage typo, exit 2, and writes
        # nothing.
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            huge = str(1 << 63)  # one past the signed 64 bit maximum
            r = _run(["spend", "--project", "p1", "--tokens", huge]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertNotIn("OverflowError", r.stderr)
            self.assertIn("signed 64 bit", r.stderr)
            raw = _raw_dump(root)
            self.assertEqual(raw["autonomy_spend"], [])

    def test_astronomical_ceiling_is_a_clean_usage_refusal(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            huge = str(1 << 63)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                     "--done-definition", "d", "--signed-by", "Khalil",
                     "--token-ceiling", huge] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("signed 64 bit", r.stderr)

    def test_missing_ceiling_prints_no_data_never_a_percentage_or_unlimited(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)  # no ceilings given
            r = _run(["spend", "--project", "p1", "--tokens", "10000000"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("NO-DATA", r.stdout)
            self.assertNotIn("%", r.stdout.split("minutes:")[0])
            self.assertNotIn("unlimited", r.stdout.lower())

    def test_eighty_percent_crossing_prints_soft_stop_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--token-ceiling", "1000"))
            r = _run(["spend", "--project", "p1", "--tokens", "850"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("soft-stop", r.stdout)

    def test_one_hundred_percent_crossing_prints_hard_stop_and_gate_check_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--token-ceiling", "1000", "--risk-class",
                              "file-edit", "--allowed-path", "."))
            r = _run(["spend", "--project", "p1", "--tokens", "1000"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("hard-stop", r.stdout)
            gc = _run(["gate-check", "--project", "p1", "--action-class",
                      "file-edit"], root)
            self.assertEqual(gc.returncode, 1)
            self.assertIn("REFUSED-BREAKER", gc.stdout)


# ---------------------------------------------------------------------------
# pause / resume / stop / revoke
# ---------------------------------------------------------------------------

class TestStateChanges(unittest.TestCase):

    def test_pause_blocks_gate_check_but_status_still_works(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            r = _run(["pause", "--project", "p1", "--reason", "lunch"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            gc = _run(["gate-check", "--project", "p1", "--action-class",
                      "file-edit"], root)
            self.assertEqual(gc.returncode, 1)
            self.assertIn("REFUSED-STATE", gc.stdout)
            st = _run(["status", "--project", "p1"], root)
            self.assertEqual(st.returncode, 0, st.stderr)
            self.assertIn("paused", st.stdout)

    def test_resume_restores_the_same_authorisation(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            _run(["pause", "--project", "p1", "--reason", "r"]
                + list(ACTOR), root)
            r = _run(["resume", "--project", "p1", "--reason", "back"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            gc = _run(["gate-check", "--project", "p1", "--action-class",
                      "file-edit"], root)
            self.assertEqual(gc.returncode, 0, gc.stderr)
            self.assertIn("ALLOWED", gc.stdout)

    def test_stop_is_idempotent_through_the_cli(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            first = _run(["stop", "--project", "p1"] + list(ACTOR), root)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = _run(["stop", "--project", "p1"] + list(ACTOR), root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already", second.stdout)
            third = _run(["stop", "--project", "p1"] + list(ACTOR), root)
            self.assertEqual(third.returncode, 0, third.stderr)
            raw = _raw_dump(root)
            # Exactly one sign row plus exactly one stop row: the two
            # repeats after the first stop wrote nothing.
            self.assertEqual(len(raw["autonomy_contracts"]), 2)

    def test_stop_needs_no_reason(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["stop", "--project", "p1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_pause_without_a_reason_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["pause", "--project", "p1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 2)

    def test_revoke_is_terminal_and_resume_after_revoke_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["revoke", "--project", "p1", "--reason", "done"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            resume = _run(["resume", "--project", "p1", "--reason", "x"]
                          + list(ACTOR), root)
            self.assertEqual(resume.returncode, 1)
            self.assertIn("revoked", resume.stderr)

    def test_revoked_contract_refuses_authorization_through_gate_check(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            _run(["revoke", "--project", "p1", "--reason", "done"]
                + list(ACTOR), root)
            gc = _run(["gate-check", "--project", "p1", "--action-class",
                      "file-edit"], root)
            self.assertEqual(gc.returncode, 1)
            self.assertIn("REFUSED-STATE", gc.stdout)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

class TestStatus(unittest.TestCase):

    def test_status_without_a_contract_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["status", "--project", "p1"], root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("sign", r.stderr)

    def test_status_reports_no_data_ceilings_and_the_verdict(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["status", "--project", "p1"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("NO-DATA", r.stdout)
            self.assertIn("verdict: no-data", r.stdout)

    def test_status_json_matches_the_text_facts(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--token-ceiling", "1000"))
            r = _run(["status", "--project", "p1", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stderr)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["contract"]["state"], "live")
            self.assertEqual(payload["spend"]["token_ceiling"], 1000)


# ---------------------------------------------------------------------------
# queue-human-step / human-steps
# ---------------------------------------------------------------------------

class TestHumanSteps(unittest.TestCase):

    def test_queue_human_step_needs_a_contract(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["queue-human-step", "--project", "p1", "--what",
                     "click deploy"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)

    def test_queue_and_list_round_trips(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            q = _run(["queue-human-step", "--project", "p1", "--what",
                     "click deploy", "--lane", "release", "--floor",
                     "publish-release"] + list(ACTOR), root)
            self.assertEqual(q.returncode, 0, q.stderr)
            listing = _run(["human-steps", "--project", "p1"], root)
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertIn("release", listing.stdout)
            self.assertIn("click deploy", listing.stdout)

    def test_an_open_step_in_one_lane_leaves_another_lane_unaffected(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            _run(["queue-human-step", "--project", "p1", "--what",
                 "click deploy", "--lane", "release"] + list(ACTOR), root)
            # Nothing about the store's own gate-check consults human
            # steps at all (invariant I12): the OTHER lane's authorisation
            # is completely unaffected by the open step above.
            gc = _run(["gate-check", "--project", "p1", "--action-class",
                      "file-edit"], root)
            self.assertEqual(gc.returncode, 0, gc.stderr)
            self.assertIn("ALLOWED", gc.stdout)

    def test_resolve_marks_a_step_done_and_a_second_resolve_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            q = _run(["queue-human-step", "--project", "p1", "--what",
                     "click deploy"] + list(ACTOR), root)
            step_id = q.stdout.split()[3]
            first = _run(["human-steps", "--project", "p1", "--resolve",
                        step_id, "--resolution", "clicked it"]
                       + list(ACTOR), root)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = _run(["human-steps", "--project", "p1", "--resolve",
                         step_id, "--resolution", "again"]
                        + list(ACTOR), root)
            self.assertEqual(second.returncode, 1)
            self.assertIn("already", second.stderr)

    def test_blocks_naming_a_nonexistent_task_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["queue-human-step", "--project", "p1", "--what",
                     "click deploy", "--blocks", "ghost-task"]
                    + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("ghost-task", r.stderr)


# ---------------------------------------------------------------------------
# checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint(unittest.TestCase):

    def test_checkpoint_needs_a_contract(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["checkpoint", "--project", "p1", "--controller-id",
                     "c1"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1)

    def test_checkpoint_records_and_status_reports_it(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root)
            r = _run(["checkpoint", "--project", "p1", "--controller-id",
                     "c1", "--kind", "phase-boundary"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 0, r.stderr)
            st = _run(["status", "--project", "p1"], root)
            self.assertIn("c1", st.stdout)
            self.assertIn("ago", st.stdout)


# ---------------------------------------------------------------------------
# usage errors, generically
# ---------------------------------------------------------------------------

class TestUsageErrors(unittest.TestCase):

    def test_an_unrecognized_flag_exits_two(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--not-a-real-flag", "x"], root)
            self.assertEqual(r.returncode, 2)

    def test_an_unknown_command_exits_two_and_names_it(self):
        with tempfile.TemporaryDirectory() as root:
            r = _run(["not-a-command"], root)
            self.assertEqual(r.returncode, 2)
            self.assertIn("not-a-command", r.stderr)

    def test_a_bad_actor_type_exits_two(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["assume", "--project", "p1", "--text", "x",
                     "--actor-name", "tester", "--actor-type", "robot"],
                    root)
            self.assertEqual(r.returncode, 2)

    def test_bare_invocation_prints_help_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as root:
            r = _run([], root)
            self.assertEqual(r.returncode, 0)
            self.assertIn("commands:", r.stdout)


# ---------------------------------------------------------------------------
# L09 (2026-08-06): the founder-ratified authorisation narrowings, seen
# through the shipped CLI. GAP 1: the sixth floor (writes to .brothermode,
# .git and .claude/settings.json are un-authorisable). GAP 3: a writing
# contract with no --allowed-path refuses at signing.
# ---------------------------------------------------------------------------

class TestSixthFloorThroughTheCLI(unittest.TestCase):
    """GAP 1 at the two shipped doors: sign refuses an allowance naming a
    protected surface, and gate-check refuses a protected candidate under
    a whole-project contract, exit 1 both times."""

    def test_sign_refuses_an_allowance_naming_git_config(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                      "--done-definition", "d",
                      "--signed-by", "Khalil Maaouni",
                      "--risk-class", "file-edit",
                      "--allowed-path", ".git/config"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("floor", (r.stdout + r.stderr).lower())

    def test_gate_check_refuses_a_protected_candidate_under_a_dot_contract(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            for candidate in (".git/config", ".brothermode/store.sqlite3",
                              ".claude/settings.json"):
                r = _run(["gate-check", "--project", "p1",
                          "--action-class", "file-edit",
                          "--path", candidate, "--json"], root)
                self.assertEqual(r.returncode, 1,
                                 "%s must be refused: %s"
                                 % (candidate, r.stdout + r.stderr))
                verdict = json.loads(r.stdout)
                self.assertEqual(verdict["verdict"], "REFUSED-FLOOR")
                self.assertEqual(verdict["floor"], "governance-write")

    def test_gate_check_still_allows_an_ordinary_file_under_a_dot_contract(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            _sign(root, extra=("--risk-class", "file-edit",
                               "--allowed-path", "."))
            r = _run(["gate-check", "--project", "p1",
                      "--action-class", "file-edit",
                      "--path", "src/a.py", "--json"], root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["verdict"], "ALLOWED")


class TestEmptyScopeSignRefusalThroughTheCLI(unittest.TestCase):
    """GAP 3 at the shipped door: `sign` granting a writing class with no
    --allowed-path is exit 1 with a message naming what to declare and
    the read-only route; a read-only sign with no paths stays exit 0."""

    def test_a_writing_sign_with_no_allowed_path_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                      "--done-definition", "d",
                      "--signed-by", "Khalil Maaouni",
                      "--risk-class", "file-edit"] + list(ACTOR), root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            out = r.stdout + r.stderr
            self.assertIn("allowed_paths", out)
            self.assertIn("read-only-inspect", out)

    def test_a_read_only_sign_with_no_allowed_path_still_signs(self):
        with tempfile.TemporaryDirectory() as root:
            _bootstrap(root)
            r = _run(["sign", "--project", "p1", "--outcome", "o",
                      "--done-definition", "d",
                      "--signed-by", "Khalil Maaouni",
                      "--risk-class", "read-only-inspect"] + list(ACTOR),
                     root)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
