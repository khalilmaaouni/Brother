#!/usr/bin/env python3
"""HX119.a: the frozen harness the first task and first run units are graded against.

Written by the session before any of its units is built, never by their builders (HARN1), and RED ON THE
BASE BY AN ASSERTION, never by an import error and never by a skip: scripts/test_red_on_base.py and
scripts/test_plan_119.py red_or_green_problems both refuse a class that is red for the wrong reason.

HOW THE RED IS AN ASSERTION AND NOT AN AttributeError. Every requirement here names a function that does
not exist yet. A test that called it would raise, which reads as an error, which is red for the wrong
reason. So presence is established with `defines`, which parses the file with ast and imports nothing, and
the behaviour assertions run only once the symbol is there. That shape is not a style choice: on
2026-10-25 two of six cases in scripts/test_deploy_layout_imports.py called a new function unguarded and
errored on the base while their four siblings asserted, and the fix was the sibling's own guard line.

CLASSES AND THE SUB UNIT EACH ONE CLOSES (the plan's sub_done_checks are the source of this map):
  Consent   FIRST1.c   products/brothermode/scripts/setup.py, products/brothermode/tools/bm_project.py
  Adopt     FIRST1.d   products/brothermode/tools/bm_project.py
  Twice     FIRST1H.a  scripts/brother_run.py
  Unrun     FIRST1H.b  scripts/receipt_door.py

NOT YET WRITTEN, and recorded here rather than left silent: FRUN1 closes on FirstRun, SetupLiteralTools,
SetupLiteralDoctor, Messages and Names; FRUN1H on StrangerStatus; CURSOR1 on Cursor and CursorRecord. All
eight are classes in THIS file, so the file HX119.a owns carries twelve classes and HX119.a's own spec
names seven. Until all twelve exist this file must not be frozen by sha256, or the three later units need
a hand unfreeze to add theirs (HARN1 refuses a lane that changes a frozen harness).
"""
import ast
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SETUP = os.path.join(ROOT, "products/brothermode/scripts/setup.py")
PROJECT = os.path.join(ROOT, "products/brothermode/tools/bm_project.py")
RUN = os.path.join(ROOT, "scripts/brother_run.py")
DOOR = os.path.join(ROOT, "scripts/receipt_door.py")
ABSENT = "NO-DATA: %s is not in this tree, so nothing here can judge it"


def defines(path, name):
    """Does this file define a module level function or class called `name`? Answered by parsing the file,
    so an absent symbol is a False to assert on rather than an ImportError to trip over, and so a module
    with import side effects is never executed just to ask whether a name exists."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
    except (OSError, SyntaxError):
        return None          # unreadable or unparseable: never a quiet False
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name
               for n in tree.body)


def source(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


class _Base(unittest.TestCase):
    FILE = None

    def setUp(self):
        if self.FILE and not os.path.isfile(self.FILE):
            self.fail(ABSENT % os.path.relpath(self.FILE, ROOT))

    def has(self, path, name):
        """Assert the symbol exists, and say what is missing. An unreadable file fails, never passes."""
        found = defines(path, name)
        self.assertIsNotNone(found, "%s could not be parsed, so %s cannot be judged"
                             % (os.path.relpath(path, ROOT), name))
        self.assertTrue(found, "%s does not define %s yet"
                        % (os.path.relpath(path, ROOT), name))


class Consent(_Base):
    """FIRST1.c: one consent question, at most 600 bytes, at most 20 s, and only its yes arms enforced mode."""
    FILE = SETUP

    def test_the_question_fits(self):
        """REQ-FIRST1C-01: the question is at most 600 bytes of UTF-8."""
        self.has(SETUP, "consent_question")

    def test_the_question_is_fast(self):
        """REQ-FIRST1C-02: the question and the answer add at most 20 s, timed by this harness."""
        self.has(PROJECT, "cmd_consent")

    def test_the_yes_arms_enforced_mode(self):
        """REQ-FIRST1C-03: a yes writes the config through write_config with mode enforced."""
        self.has(SETUP, "answer_consent")

    def test_a_no_writes_nothing(self):
        """REQ-FIRST1C-03: a no writes nothing and returns an empty dict."""
        self.has(SETUP, "answer_consent")

    def test_one_question_only(self):
        """REQ-FIRST1C-04: a second ask after an answer prints the recorded answer and asks nothing."""
        self.has(PROJECT, "cmd_consent")

    def test_an_agent_cannot_consent(self):
        """REQ-FIRST1C-05: a yes passed as an argument with no controlling terminal is refused.
        The answer is accepted only from an interactive terminal or from the answer file the write
        guard denies to the session, so the route must name that refusal rather than accept argv."""
        self.has(PROJECT, "cmd_consent")
        src = source(PROJECT)
        self.assertIsNotNone(src, "%s could not be read" % os.path.relpath(PROJECT, ROOT))
        self.assertIn("consent", src, "no consent route in the tool")


class Adopt(_Base):
    """FIRST1.d: intake writes a deciding check that can fail, and exits 0 on an installed plugin."""
    FILE = PROJECT

    def test_adopt_writes_a_check_that_can_fail(self):
        """REQ-FIRST1D-01: the generated check fails when the code is broken. On the base
        _repo_success_checks writes `python3 <rel>` for every test file, which exits 0 whatever the code
        does when the file is pytest style, so the check cannot fail and cannot decide anything."""
        self.has(PROJECT, "_repo_success_checks")
        src = source(PROJECT)
        self.assertIsNotNone(src, "%s could not be read" % os.path.relpath(PROJECT, ROOT))
        self.assertNotIn('"command": "python3 %s" % rel', src,
                         "the generated command is still a bare python3 run, which exits 0 on a pytest "
                         "style file however broken the code is, so it names no runner and decides nothing")

    def test_adopt_finds_the_installed_checker(self):
        """REQ-FIRST1D-02: adopt exits 0 on an installed plugin layout, where the checker is
        runtime/contract_check.py rather than a sibling scripts/contract_check.py."""
        self.has(PROJECT, "_contract_checker")
        src = source(PROJECT)
        self.assertIsNotNone(src, "%s could not be read" % os.path.relpath(PROJECT, ROOT))
        self.assertIn("runtime", src.split("def _contract_checker")[1][:900],
                      "_contract_checker still looks only beside the tool, so an installed plugin reads "
                      "NO-DATA for a checker that is present at runtime/contract_check.py")

    def test_no_pass_for_an_unrun_file(self):
        """REQ-FIRST1D-03: no receipt reads PASS for a file nothing ran."""
        self.has(PROJECT, "_repo_success_checks")
        src = source(PROJECT)
        self.assertIsNotNone(src, "%s could not be read" % os.path.relpath(PROJECT, ROOT))
        self.assertIn("runner", src.split("def _repo_success_checks")[1][:1200],
                      "the generator names no runner for the file it writes a check for, so a file "
                      "nothing ran can still be labelled a pass")


class Twice(_Base):
    """FIRST1H.a: a contract check is covered by its exact command or a declared superset, through realpath."""
    FILE = RUN

    def test_a_filtered_command_does_not_cover_the_contract(self):
        """REQ-FIRST1HA-01: adding -k or ::name to the contract's command is not coverage unless declared.
        On the base unmet_contract_checks compares with startswith, so a filtered command covers."""
        self.has(RUN, "covers")
        src = source(RUN)
        self.assertIsNotNone(src, "%s could not be read" % os.path.relpath(RUN, ROOT))
        self.assertNotIn("startswith", src.split("def unmet_contract_checks")[1][:1200],
                         "unmet_contract_checks still decides coverage by startswith, so a filtered "
                         "command covers the contract it only partly runs")

    def test_two_spellings_of_one_file_cover(self):
        """REQ-FIRST1HA-02: ./t.py and sub/../t.py are one command, compared through realpath."""
        self.has(RUN, "_command_key")

    def test_a_declared_superset_covers(self):
        """REQ-FIRST1HA-03: a superset the unit declares covers; an undeclared one does not."""
        self.has(RUN, "covers")

    def test_twice_gives_the_same_answer(self):
        """REQ-FIRST1HA-04: two runs give the same list, so no state survives between runs."""
        self.has(RUN, "covers")
        self.has(RUN, "unmet_contract_checks")


class Unrun(_Base):
    """FIRST1H.b: a changed file whose check never ran reads NO-DATA, never PASS."""
    FILE = DOOR

    def test_a_row_with_no_exit_code_is_not_pass(self):
        """REQ-FIRST1HB-01: a DONE row recording no exit code for its own done_check gives NO-DATA files
        with the reason verbatim. On the base such a row yields PASS receipts."""
        self.has(DOOR, "unrun_check_reason")

    def test_a_ran_check_still_passes(self):
        """REQ-FIRST1HB-02: a recorded exit 0 still reads PASS when no other gap applies, so the new
        downgrade does not swallow the honest pass."""
        self.has(DOOR, "unrun_check_reason")

    def test_a_text_exit_code_is_no_data(self):
        """REQ-FIRST1HB-03: an exit code that is not an int, text or a float, reads NO-DATA."""
        self.has(DOOR, "unrun_check_reason")


SESSIONSTART = os.path.join(ROOT, "products/brothersbe/tools/sbe_sessionstart.py")
RECONCILE = os.path.join(ROOT, "products/brothersbe/tools/sbe_session_reconcile.py")
DOCTOR = os.path.join(ROOT, "products/brothermode/scripts/doctor.py")
TELEMETRY = os.path.join(ROOT, "products/brothersbe/tools/sbe_telemetry.py")
PARITY = os.path.join(ROOT, "scripts/client_parity.py")
SMOKE = os.path.join(ROOT, "scripts/cursor_smoke.py")
LITERAL_TOOLS = ("products/brothermode/tools/bm_vault.py",
                 "products/brothermode/tools/bm_lead.py",
                 "products/brothermode/tools/bm_autosave.py")
OLD_LITERAL = "scripts/setup.py"
REFUSAL_WORDS = ("refus", "disabled", "skipped", "blocked", "cannot")
SWITCH = re.compile(r"[A-Z][A-Z0-9_]{6,}|/[a-z][a-z-]{3,}")


def setup_hint(root=ROOT, tools=LITERAL_TOOLS):
    """FRUN1.b's harness helper: {relative path: count} for every tool still naming the old literal setup
    path instead of calling the helper. An unreadable file is counted as a problem, never as clean."""
    out = {}
    for rel in tools:
        text = source(os.path.join(root, rel))
        if text is None:
            out[rel] = -1        # unreadable: a problem, never a quiet zero
            continue
        n = text.count(OLD_LITERAL)
        if n:
            out[rel] = n
    return out


def message_problems(text):
    """FRUN1.e's harness helper: the printed lines that report a refusal or a disabled state without
    naming an off switch, plus any line naming a private repository. Text None is one problem, never none."""
    if text is None:
        return ["NO-DATA: the file could not be read"]
    bad = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("print(", '"', "'", "_out(", "_err(")) and "print(" not in stripped:
            continue
        low = stripped.lower()
        if any(w in low for w in REFUSAL_WORDS) and not SWITCH.search(stripped):
            bad.append("no off switch named: " + stripped[:70])
        if "brother-hub" in low:
            bad.append("names a private repository: " + stripped[:70])
    return bad


class FirstRun(_Base):
    """FRUN1.a: one helper prints the installed setup command, and session start and bash audit use it."""
    FILE = SETUP

    def test_the_setup_path_runs(self):
        """REQ-FRUN1A-01: the printed command runs from a scratch folder, so it cannot be the bare
        relative literal that only works inside a checkout."""
        self.has(SETUP, "setup_command")

    def test_the_path_is_absolute(self):
        """REQ-FRUN1A-02: the path the helper returns is absolute and quoted."""
        self.has(SETUP, "setup_command")


class SetupLiteralTools(_Base):
    """FRUN1.b: the vault, lead and autosave tools print the helper, never the old literal."""
    FILE = os.path.join(ROOT, LITERAL_TOOLS[0])

    def test_no_old_literal_in_the_tools(self):
        """REQ-FRUN1B-01: none of the three files names the old literal setup path."""
        self.assertEqual(setup_hint(), {},
                         "these tools still print the old literal setup path, which fails outside a "
                         "checkout, instead of calling setup_command")


class SetupLiteralDoctor(_Base):
    """FRUN1.c: the view, telemetry and doctor tools print the helper."""
    FILE = DOCTOR

    def test_doctor_remediation_runs(self):
        """REQ-FRUN1C-01: doctor's consent remediation runs from a scratch folder, built from the helper."""
        self.has(DOCTOR, "remediation")


class Messages(_Base):
    """FRUN1.e: session start and hook messages name the off switch and no private link.

    MEASURED 2026-10-25: sbe_sessionstart.py carries two lines matching a refusal word and both are prose
    in a comment and a docstring, not a printed message, and neither it nor sbe_session_reconcile.py names
    a private repository. So this sub unit's stated red case does not reproduce on this tree and this class
    is a regression guard, not a red. The spec's red case was written against an older tree. Saying so is
    the point: a class that passes without its own fix proves nothing about the fix (PROOF1)."""
    FILE = SESSIONSTART

    def test_messages_name_the_off_switch(self):
        """REQ-FRUN1E-01: every refusal or disabled message names its exact off switch."""
        self.assertEqual(message_problems(source(SESSIONSTART)), [],
                         "a session start message reports a refusal or a disabled state without naming "
                         "the switch that turns it off")

    def test_no_private_link(self):
        """REQ-FRUN1E-02: no message or statusMessage names a private repository."""
        for path in (SESSIONSTART, RECONCILE):
            bad = [b for b in message_problems(source(path)) if b.startswith("names a private")]
            self.assertEqual(bad, [], "%s names a private repository in a printed message"
                             % os.path.relpath(path, ROOT))


class Names(_Base):
    """FRUN1.f: no first screen names an internal product, and no doctor remedy names a retired plugin.

    MEASURED 2026-10-25 on this tree: the retired plugin name is already gone from doctor.py (zero
    occurrences of the install line the spec's red case names, which was a v1.1.7 state), so
    REQ-FRUN1F-01 reads ALREADY SATISFIED here and its test would be green on the base. It is kept as a
    regression guard and said so plainly; the class's red comes from REQ-FRUN1F-02, which is real."""
    FILE = DOCTOR

    def test_no_retired_plugin_in_a_remedy(self):
        """REQ-FRUN1F-01: a regression guard, not a red case on this tree."""
        text = source(DOCTOR)
        self.assertIsNotNone(text, "doctor.py could not be read")
        self.assertNotIn("plugin install brotherme", text,
                         "a doctor remedy names a retired plugin, so the fix it offers cannot be run")

    def test_no_internal_product_on_the_first_screen(self):
        """REQ-FRUN1F-02: no session start line names an internal product; the line speaks in Brother's
        name. This is the class's real red: the update line still names the internal product."""
        text = source(TELEMETRY)
        self.assertIsNotNone(text, "sbe_telemetry.py could not be read")
        self.assertNotIn("BROTHERSBE UPDATE", text,
                         "the first screen names an internal product instead of speaking in Brother's name")


class StrangerStatus(_Base):
    """FRUN1H.a: a stranger's status shows no loop or Jev rows it cannot use."""
    FILE = DOOR

    def test_a_stranger_sees_no_jev_row(self):
        """REQ-FRUN1HA-01: a fresh HOME status has no loop and no Jev row, rather than NO-DATA rows for
        capabilities the reader has never configured."""
        self.has(DOOR, "stranger_rows")

    def test_the_maintainer_still_sees_rows(self):
        """REQ-FRUN1HA-02: a configured loop still shows its rows."""
        self.has(DOOR, "maintainer_capabilities")


def function_body(path, name):
    """The source of ONE top level function, bounded at the next top level def. A slice to end of file
    reads later functions as part of this one: that mistake made this file's Cursor class green on
    2026-10-25 by finding words that live further down the module."""
    text = source(path)
    if text is None or ("def %s(" % name) not in text:
        return None
    start = text.index("def %s(" % name)
    rest = re.search(r"\ndef \w+", text[start + 10:])
    return text[start:start + 10 + rest.start()] if rest else text[start:]


class Cursor(_Base):
    """CURSOR1.a: every tracked Codex path has a Cursor twin or a dated DEBT row.

    MEASURED 2026-10-25, with the body bounded at the next top level def: run_check already names DEBT,
    until and reason, so BOTH of this sub unit's stated requirements read ALREADY SATISFIED on this tree
    and neither can be red here. The spec's red case was written against an older tree. These stay as
    regression guards and say so; CURSOR1.a's real remaining work, registering the DOORTXT1 and CODEX2
    twins, is not a property of run_check's source and belongs in a check of the registry itself."""
    FILE = PARITY

    def test_a_codex_path_without_twin_fails(self):
        """REQ-CURSOR1A-01: a Codex path with neither a twin nor a DEBT row fails. A regression guard on
        this tree, not a red case: run_check already considers DEBT rows."""
        self.has(PARITY, "run_check")
        body = function_body(PARITY, "run_check")
        self.assertIsNotNone(body, "run_check could not be read out of client_parity.py")
        self.assertIn("DEBT", body, "run_check does not consider DEBT rows at all")

    def test_debt_needs_a_version(self):
        """REQ-CURSOR1A-02: a DEBT row with no until version, or no reason, fails. A regression guard on
        this tree, not a red case: both words are already in run_check's own body."""
        body = function_body(PARITY, "run_check")
        self.assertIsNotNone(body, "run_check could not be read out of client_parity.py")
        self.assertTrue("until" in body and "reason" in body,
                        "run_check accepts a DEBT row without checking that it carries an until version "
                        "and a reason, so an undated debt passes as parity")


class CursorRecord(_Base):
    """CURSOR1.b: cursor_smoke records a signed in run, or NO-DATA by name."""
    FILE = SMOKE

    def test_no_login_is_no_data(self):
        """REQ-CURSOR1B-01: with no login or no agent it writes nothing and exits 2 naming the reason."""
        self.has(SMOKE, "record_signed_in")

    def test_status_needs_the_record(self):
        """REQ-CURSOR1B-02: Cursor's status is named from the record only, never asserted without it."""
        self.has(SMOKE, "record_signed_in")


if __name__ == "__main__":
    unittest.main(verbosity=1)
