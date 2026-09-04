"""Adversarial fixtures for decision packages, `sbe explain` and `sbe lineage`
(spec: docs/specs/2026-07-30-team-docs-collab-book-design.md, Feature 2).
Written BEFORE the implementation: on the day this lands, every scenario is red
because src/brothersbe/decisions.py does not exist, and none is red on a
fixture bug.

Every helper here returns ONE dict or three values, never a two-value tuple:
a two-value return reads as a possible (verdict, evidence) pair to the honesty
meta-test in evals/test_no_data_class.py, which refuses any such function
sitting outside a check registry.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SBE = os.path.join(ROOT, "bin", "sbe")
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import decisions as decisions_mod  # noqa: E402
import sbe_checks  # noqa: E402  (tools/ is this file's own directory, already on sys.path)

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '../../../scripts'))
try:  # noqa: E402
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    # A packager (scripts/export_public.py, make_benchmark_bundle.py)
    # can copy this test without scripts/tmp_sandbox.py beside it. Say
    # so rather than dying: the sandbox is hygiene, not the subject.
    _e100_sys.stderr.write(
        "tmp_sandbox absent: %s leaves its temp trees behind\n"
        % _e100_os.path.basename(__file__))


def _run(argv, cwd=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                         stdin=subprocess.DEVNULL, timeout=180)
    return {"code": out.returncode, "stdout": out.stdout, "stderr": out.stderr}


def _git_repo(path):
    subprocess.run(["git", "-C", path, "init", "-q"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.email", "e@e"], check=True)
    subprocess.run(["git", "-C", path, "config", "user.name", "T"], check=True)
    with io.open(os.path.join(path, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    subprocess.run(["git", "-C", path, "add", "-A"], check=True)
    subprocess.run(["git", "-C", path, "commit", "-qm", "seed"], check=True)


class TestPackageWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-decisions-")
        _git_repo(self.tmp)

    def _trigger(self, verdict):
        return {"kind": "gate", "check": "numbers", "verdict": verdict,
                "verdictLine": "numbers %s an overstated total in report.md" % verdict,
                "otherLines": ["some unquotable chatter", "more chatter"],
                "dossier": None}

    def test_a_package_is_a_dict_with_every_named_section(self):
        pkg = decisions_mod.build_package(self.tmp, self._trigger("FAIL"))
        for key in ("id", "slug", "dir", "verdictLine", "evidence", "inputs",
                    "risks", "whatWouldFlipIt", "checklist", "boundCommit",
                    "location", "locationReason", "unquotedLineCount", "notes"):
            self.assertIn(key, pkg)
        self.assertEqual(pkg["unquotedLineCount"], 2,
                         "lines outside the verdict grammar are counted, not copied")

    def test_a_waived_check_is_waived_in_the_package_and_never_pass(self):
        pkg = decisions_mod.build_package(self.tmp, self._trigger("WAIVED"))
        self.assertIn("WAIVED", pkg["verdictLine"])
        self.assertNotIn("PASS", pkg["verdictLine"])

    def test_a_trigger_with_no_verdict_line_is_no_data_not_a_package_that_reads_clean(self):
        trigger = self._trigger("FAIL")
        trigger["verdictLine"] = ""
        pkg = decisions_mod.build_package(self.tmp, trigger)
        self.assertIn("NO-DATA", pkg["verdictLine"])
        self.assertIn("NO-DATA", "\n".join(pkg["notes"]))

    def test_ids_are_allocated_from_disk_and_the_file_lands_where_it_says(self):
        first = decisions_mod.write_package(
            self.tmp, decisions_mod.build_package(self.tmp, self._trigger("FAIL")))
        second = decisions_mod.write_package(
            self.tmp, decisions_mod.build_package(self.tmp, self._trigger("FAIL")))
        self.assertTrue(os.path.isfile(first))
        self.assertTrue(os.path.isfile(second))
        self.assertNotEqual(os.path.dirname(first), os.path.dirname(second))
        self.assertIn(os.path.join(".sbe", "decisions"), first,
                      "with no 00-intake.json there is no project to name")
        body = io.open(first, encoding="utf-8").read()
        self.assertIn("bound to commit", body)
        # Escapes, not the glyphs themselves: this repository bans both
        # characters from every file, including the test that checks for them.
        for banned in ("\u2014", "\u2013"):
            self.assertNotIn(banned, body)


class TestDecidingCode(unittest.TestCase):
    def test_a_shipped_check_names_its_file_lines_and_carries_the_excerpt(self):
        code = decisions_mod.deciding_code("numbers")
        self.assertTrue(code["file"].endswith("sbe_gate.py"))
        self.assertGreater(code["lastLine"], code["firstLine"])
        self.assertIn("def gate_numbers", code["excerpt"])

    def test_an_unknown_check_is_no_data_and_never_an_invented_span(self):
        code = decisions_mod.deciding_code("not-a-real-check")
        self.assertEqual(code["excerpt"], "")
        self.assertIn("NO-DATA", code["note"])
        self.assertIsNone(code["file"])

    def test_the_flowchart_is_mermaid_and_names_the_check_it_drew(self):
        chart = decisions_mod.logic_flowchart("numbers")
        self.assertTrue(chart.lstrip().startswith("flowchart"))
        self.assertIn("numbers", chart)

    # ---- Fixtures beyond the plan's three, each holding one sentence the
    # implementation makes and nothing else would catch. ----

    def test_the_excerpt_is_the_functions_own_lines_at_the_span_it_names(self):
        """The span is a pointer a reader OPENS. If the numbers do not address
        the excerpt in the file itself, the package sends a reviewer to the
        wrong lines, which is worse than printing no span at all."""
        code = decisions_mod.deciding_code("numbers")
        with io.open(code["file"], encoding="utf-8") as fh:
            file_lines = fh.readlines()
        span = "".join(file_lines[code["firstLine"] - 1:code["lastLine"]])
        self.assertEqual(span, code["excerpt"])

    def test_the_flowchart_draws_only_parts_the_registry_declares(self):
        """Every box comes from a declaration: what the check reads, the verdict
        its empty evidence gets, and its severity. A box nobody declared is a
        picture, not a record."""
        chart = decisions_mod.logic_flowchart("numbers")
        self.assertIn("NO-DATA", chart, "the declared empty-evidence verdict")
        self.assertIn("gate", chart, "the declared severity")
        self.assertIn("numbers-manifest.json", chart,
                      "the evidence the check declares it reads")

    def test_an_unknown_check_gets_no_flowchart_and_says_why(self):
        chart = decisions_mod.logic_flowchart("not-a-real-check")
        self.assertIn("NO-DATA", chart)
        self.assertIn("not-a-real-check", chart)
        self.assertFalse(chart.lstrip().startswith("flowchart"),
                         "a check nobody resolved gets a named absence, never a drawing")

    def test_a_check_name_two_registries_declare_says_so_rather_than_picking_quietly(self):
        """`migration` is declared by BOTH tools/sbe_gate.py and
        tools/sbe_plan.py. Resolving one of them in silence would print a span
        from a file the reader was not thinking about."""
        code = decisions_mod.deciding_code("migration")
        self.assertIsNotNone(code["file"])
        self.assertIn("sbe_plan.py", code["note"] + (code["file"] or ""))
        self.assertIn("sbe_gate.py", code["note"] + (code["file"] or ""))

    def test_neither_helper_starts_a_subprocess_to_read_the_logic(self):
        """The kill criterion of this module, held as a fixture rather than as a
        sentence in a docstring. `_git` is the only door in this module through
        which a child process is started, so a run that cannot open that door
        and still answers is a run that read files and nothing else."""
        original = decisions_mod._git

        def refuse(*a, **kw):
            raise AssertionError("deciding_code and logic_flowchart started a process")

        decisions_mod._git = refuse
        try:
            code = decisions_mod.deciding_code("numbers")
            chart = decisions_mod.logic_flowchart("numbers")
        finally:
            decisions_mod._git = original
        self.assertIn("def gate_numbers", code["excerpt"])
        self.assertTrue(chart.lstrip().startswith("flowchart"))

    def test_the_package_carries_the_deciding_code_and_the_flowchart(self):
        tmp = tempfile.mkdtemp(prefix="sbe-deciding-")
        _git_repo(tmp)
        pkg = decisions_mod.build_package(
            tmp, {"kind": "gate", "check": "numbers", "verdict": "FAIL",
                  "verdictLine": "numbers FAIL an overstated total in report.md",
                  "otherLines": [], "dossier": None})
        self.assertIn("def gate_numbers", pkg["decidingCode"]["excerpt"])
        self.assertTrue(pkg["logicFlowchart"].lstrip().startswith("flowchart"))
        with io.open(decisions_mod.write_package(tmp, pkg), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("def gate_numbers", body)
        self.assertIn("```mermaid", body)
        # Escapes, not the glyphs themselves, for the reason stated in
        # TestPackageWriter above: this repository bans both characters from
        # every file, including the test that checks for them.
        for banned in ("\u2014", "\u2013"):
            self.assertNotIn(banned, body)

    def test_a_fenced_block_is_longer_than_any_fence_inside_it(self):
        """An excerpt of real source can carry a fence of its own, and a three
        tick fence around it would close the block early and spill the rest of
        the deciding function into the document as prose."""
        block = decisions_mod._fenced("a = '''```not the end```'''\n", "python")
        opener = block.splitlines()[0]
        self.assertTrue(opener.startswith("````"), opener)
        self.assertEqual(block.splitlines()[-1], opener[:-len("python")])

    def test_the_discovery_rule_here_and_in_the_honesty_meta_test_agree(self):
        """decisions.registries() cannot IMPORT evals/test_no_data_class.py: that
        module imports every module under tools/, this one among them, and this
        one imports brothersbe.decisions, so the import would be a cycle. The
        two therefore state the same rule twice, and this fixture is what stops
        the second copy from drifting: it asks both for their registries and
        fails on any disagreement, so a rule changed there goes red here."""
        meta = SourceFileLoader(
            "sbe_no_data_class_for_decisions",
            os.path.join(ROOT, "evals", "test_no_data_class.py")).load_module()
        modules, failures = meta.load_tool_modules()
        self.assertEqual(failures, [], "the meta-test could not import part of tools/")
        registries, defects = meta.discover_registries(modules)
        self.assertEqual(defects, [], defects)
        theirs = set()
        for source, attr, table in registries:
            for check_name in table:
                theirs.add(("tools/%s" % source, attr, check_name))
        found = decisions_mod.registries()
        self.assertEqual(found["problems"], [], found["problems"])
        mine = set()
        for check_name, entries in found["declarations"].items():
            for entry in entries:
                mine.add((entry["source"], entry["registry"], check_name))
        self.assertEqual(mine, theirs,
                         "the two discoveries disagree, so one of them is reading a registry "
                         "the other cannot see")
        self.assertTrue(mine, "neither discovery found a registry at all")

    def test_a_package_for_a_check_nobody_ships_says_no_data_in_both_sections(self):
        tmp = tempfile.mkdtemp(prefix="sbe-deciding-unknown-")
        _git_repo(tmp)
        pkg = decisions_mod.build_package(
            tmp, {"kind": "forced-close", "check": "", "verdict": "FAIL",
                  "verdictLine": "a human forced this closed", "otherLines": [],
                  "dossier": None})
        with io.open(decisions_mod.write_package(tmp, pkg), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("NO-DATA", pkg["decidingCode"]["note"])
        self.assertIn("NO-DATA", pkg["logicFlowchart"])
        self.assertNotIn("```mermaid", body,
                         "no fenced diagram is drawn where no logic was read")
class TestGateTriggers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-gate-trigger-")
        _git_repo(self.tmp)

    def test_a_fail_and_a_waiver_each_get_their_own_package(self):
        text = ("numbers   FAIL     an overstated total in report.md\n"
                "  >> migration WAIVED  .sbe-exempt names this directory: rehearsal pending\n"
                "chatter nobody parses\n")
        written = decisions_mod.record_from_run(self.tmp, text, None)
        self.assertEqual(len(written), 2, written)
        bodies = [io.open(p, encoding="utf-8").read() for p in written]
        self.assertTrue(any("FAIL" in b for b in bodies))
        self.assertTrue(any("WAIVED" in b for b in bodies))

    def test_a_pass_line_writes_no_package(self):
        text = "numbers   PASS     3 figures, each with its check run\n"
        self.assertEqual(decisions_mod.record_from_run(self.tmp, text, None), [])

    def test_lines_outside_the_grammar_are_counted_never_copied(self):
        parsed = decisions_mod.parse_verdict_lines("numbers FAIL x\nsecret: hunter2\n")
        self.assertEqual(parsed["unquotedLineCount"], 1)
        self.assertEqual(len(parsed["verdicts"]), 1)

    def test_the_cli_writes_a_package_and_says_so(self):
        result = _run([sys.executable, SBE, "gate", self.tmp], cwd=self.tmp)
        self.assertIn("decision package", result["stdout"] + result["stderr"])

    def test_no_decisions_suppresses_the_write_and_names_the_suppression(self):
        result = _run([sys.executable, SBE, "gate", "--no-decisions", self.tmp],
                      cwd=self.tmp)
        self.assertIn("no decision package was written", result["stdout"]
                      + result["stderr"])

    # The three fixtures below are not in the plan. They hold the promises Task 3
    # makes in prose and would otherwise be held by nobody: that an unmatched
    # line's TEXT never reaches the package, that a bookkeeping failure cannot
    # move a gate's exit code, and that the tee still streams.

    def test_the_text_of_an_unmatched_line_never_reaches_a_package(self):
        text = ("numbers   FAIL     an overstated total in report.md\n"
                "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIcorrecthorse\n")
        written = decisions_mod.record_from_run(self.tmp, text, None)
        self.assertEqual(len(written), 1, written)
        body = io.open(written[0], encoding="utf-8").read()
        self.assertNotIn("wJalrXUtnFEMIcorrecthorse", body)
        self.assertIn("1 line(s)", body)

    def test_a_package_write_that_fails_leaves_the_gate_exit_code_alone(self):
        # A FILE where the decisions directory has to go, so every write under
        # it fails: exactly the bookkeeping failure that must never be allowed
        # to move a verdict. The gate itself FAILs here on an unparseable
        # manifest, so the exit code under test is a real 1 and not a 0 that
        # would have been 0 anyway.
        with io.open(os.path.join(self.tmp, ".sbe"), "w", encoding="utf-8") as fh:
            fh.write("not a directory\n")
        with io.open(os.path.join(self.tmp, "numbers-manifest.json"), "w",
                     encoding="utf-8") as fh:
            fh.write("{ not json\n")
        run = _run([sys.executable, SBE, "gate", "--strict", self.tmp], cwd=self.tmp)
        both = run["stdout"] + run["stderr"]
        self.assertEqual(run["code"], 1, both)
        self.assertIn("no decision package was written", both)
        self.assertIn("exit code is unchanged", both)

    def test_the_teeing_delegate_streams_and_keeps_a_copy(self):
        sys.path.insert(0, os.path.join(ROOT, "src"))
        from brothersbe import cli as cli_mod
        result = cli_mod.delegate_teed("sbe_gate.py", [self.tmp])
        self.assertIn("code", result)
        self.assertIn("lines", result)
        self.assertTrue(any("BROTHERSBE HARD GATES" in line for line in result["lines"]),
                        result["lines"][:5])


class TestOtherTriggers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-other-trigger-")
        _git_repo(self.tmp)

    def test_a_forced_close_leaves_a_package_naming_who_why_and_the_violations(self):
        record = {"id": "T01", "agent": "writer-a", "role": "writer",
                  "forced": {"who": "a human", "why": "shipped under a deadline",
                             "verdict": "FAIL", "violations": ["src/other.py"],
                             "at": "2026-07-31T00:00:00Z"}}
        path = decisions_mod.record_forced_close(self.tmp, record)
        body = io.open(path, encoding="utf-8").read()
        for expected in ("a human", "shipped under a deadline", "src/other.py", "FAIL"):
            self.assertIn(expected, body)
        self.assertNotIn("PASS", body)

    def test_an_impact_run_with_nothing_decided_writes_nothing(self):
        quiet = {"verdict": "PASS", "disagreements": [], "proposedTier": "T1",
                 "humanTier": "T1", "headCommit": None}
        self.assertIsNone(decisions_mod.record_tier_decision(self.tmp, quiet, None))

    def test_a_review_required_impact_writes_a_package_naming_the_detector(self):
        raised = {"verdict": "REVIEW-REQUIRED", "proposedTier": "T3", "humanTier": "T1",
                  "headCommit": None,
                  "disagreements": [{"detector": "schema-migration", "file": "db/001.sql",
                                     "disposition": "missing"}]}
        path = decisions_mod.record_tier_decision(self.tmp, raised, None)
        body = io.open(path, encoding="utf-8").read()
        self.assertIn("schema-migration", body)
        self.assertIn("T1", body)
        self.assertIn("T3", body)

    # The fixtures below are not in the plan. They hold the sentences Task 4
    # makes in prose and would otherwise be held by nobody: that a forced close
    # cannot be recorded as a pass whatever the record claims, that a disposed
    # tier raise is WAIVED rather than the PASS the report printed, and that a
    # package write on either path cannot move an exit code.

    def test_a_forced_record_claiming_pass_is_recorded_no_data_and_the_claim_is_named(self):
        """`sbe task close --force` returns before the forced branch on PASS, so
        a record reaching here claiming PASS is a caller that did not run the
        postcondition. Copying that word into a FORCED package would print a
        clean verdict over a close nobody's diff cleared."""
        record = {"id": "T02", "agent": "writer-a", "role": "writer",
                  "forced": {"who": "a human", "why": "the deadline",
                             "verdict": "PASS", "violations": ["src/other.py"],
                             "at": "2026-07-31T00:00:00Z"}}
        path = decisions_mod.record_forced_close(self.tmp, record)
        body = io.open(path, encoding="utf-8").read()
        self.assertIn("- verdict recorded by the run: NO-DATA\n", body)
        self.assertIn("PASS", body, "the word the record claimed is named, never silently "
                                    "dropped")

    def test_a_close_record_with_no_forced_block_is_no_data_and_names_what_would_fill_it(self):
        record = {"id": "T03", "agent": "writer-a", "role": "writer"}
        path = decisions_mod.record_forced_close(self.tmp, record)
        body = io.open(path, encoding="utf-8").read()
        self.assertIn("- verdict recorded by the run: NO-DATA\n", body)
        self.assertIn("--force --who --why", body)
        self.assertNotIn("PASS", body)

    def test_a_disposed_raise_is_waived_never_the_pass_the_report_printed(self):
        """impact prints PASS once every disagreement carries a disposition. The
        decision this package records is that suppression, and a suppression is
        WAIVED. Recording the report's PASS here would be a package that reads
        clean over a control a human switched off."""
        disposed = {"verdict": "PASS", "proposedTier": "T3", "humanTier": "T1",
                    "headCommit": None,
                    "disagreements": [{"detector": "db-migration", "file": "db/001.sql",
                                       "disposition": "recorded"}]}
        path = decisions_mod.record_tier_decision(self.tmp, disposed, None)
        self.assertIsNotNone(path)
        body = io.open(path, encoding="utf-8").read()
        self.assertIn("- verdict recorded by the run: WAIVED\n", body)
        self.assertIn("db-migration", body)

    def test_a_tier_package_never_quotes_a_line_the_caller_did_not_compose(self):
        """The tier path is handed a REPORT, not a terminal capture. Everything
        it prints is composed from that report's own named fields, and the
        package says so, because a section headed "quoted" that carries
        something nobody quoted is the same defect as an uncounted line."""
        raised = {"verdict": "REVIEW-REQUIRED", "proposedTier": "T3", "humanTier": "T1",
                  "headCommit": None, "disagreements": [],
                  "chatter": "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIcorrecthorse"}
        path = decisions_mod.record_tier_decision(self.tmp, raised, None)
        body = io.open(path, encoding="utf-8").read()
        self.assertNotIn("wJalrXUtnFEMIcorrecthorse", body)
        self.assertIn("composed from the impact report's own fields", body)

    def test_a_forced_close_through_the_cli_writes_a_package_and_keeps_its_exit_code(self):
        sha = subprocess.run(["git", "-C", self.tmp, "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        _run([sys.executable, SBE, "task", "open", "--id", "w1", "--agent", "a",
              "--role", "writer", "--base", sha, "--verify", "true",
              "--owns", "owned.txt", "--cwd", self.tmp], cwd=self.tmp)
        with io.open(os.path.join(self.tmp, "outside.txt"), "w", encoding="utf-8") as fh:
            fh.write("written outside the declaration\n")
        result = _run([sys.executable, SBE, "task", "close", "w1", "--force",
                       "--who", "the operator", "--why", "hotfix, out of band",
                       "--cwd", self.tmp], cwd=self.tmp)
        both = result["stdout"] + result["stderr"]
        self.assertEqual(result["code"], 0, both)
        self.assertIn("FORCED", both)
        self.assertIn("decision package written", both)
        written = [line.split("decision package written:")[1].strip()
                   for line in both.splitlines() if "decision package written:" in line]
        self.assertEqual(len(written), 1, both)
        body = io.open(written[0], encoding="utf-8").read()
        self.assertIn("the operator", body)
        self.assertIn("outside.txt", body)

    def test_a_package_write_that_fails_leaves_the_forced_close_exit_code_alone(self):
        # A FILE where the decisions directory has to go, so every write under
        # it fails. `.sbe` also holds the registry, so the close itself is run
        # first, in a repository whose `.sbe` is still a directory, and the file
        # is planted between the close and nothing else by pointing the writer
        # at a root whose `.sbe` cannot hold a directory.
        record = {"id": "T04", "agent": "a", "role": "writer",
                  "forced": {"who": "a human", "why": "the deadline", "verdict": "FAIL",
                             "violations": ["src/other.py"], "at": "2026-07-31T00:00:00Z"}}
        with io.open(os.path.join(self.tmp, ".sbe"), "w", encoding="utf-8") as fh:
            fh.write("not a directory\n")
        with self.assertRaises(decisions_mod.DecisionUnwritable):
            decisions_mod.record_forced_close(self.tmp, record)
        # And the CLI path swallows nothing while changing nothing: the close
        # exits on its own verdict and the failure is printed by name.
        from brothersbe import tasks as tasks_mod
        printed = []
        original = tasks_mod.sys.stdout.write
        try:
            tasks_mod.sys.stdout.write = lambda s: printed.append(s)
            tasks_mod._record_forced_close(self.tmp, record)
        finally:
            tasks_mod.sys.stdout.write = original
        joined = "".join(printed)
        self.assertIn("no decision package was written", joined)
        self.assertIn("DecisionUnwritable", joined)
        self.assertIn("exit code is unchanged", joined)

    def test_impact_in_json_mode_keeps_stdout_parseable(self):
        """`sbe impact --json` stdout is a document callers parse. A human
        sentence appended to it would break every one of them, so the package
        line goes to stderr there and to stdout otherwise."""
        with io.open(os.path.join(self.tmp, "00-intake.json"), "w", encoding="utf-8") as fh:
            json.dump({"answers": {"changes_contract": "n", "crosses_boundary": "n",
                                   "reversible_under_hour": "y", "touches_sensitive": "n",
                                   "consumers": "none"}}, fh)
        with io.open(os.path.join(self.tmp, "0002_add_partner_id.sql"), "w",
                     encoding="utf-8") as fh:
            fh.write("ALTER TABLE orders ADD COLUMN partner_id TEXT;\n")
        subprocess.run(["git", "-C", self.tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.tmp, "commit", "-qm", "migration"], check=True)
        base = subprocess.run(["git", "-C", self.tmp, "rev-parse", "HEAD~1"],
                              capture_output=True, text=True, check=True).stdout.strip()
        result = _run([sys.executable, SBE, "impact", self.tmp, "--base", base,
                       "--intake", os.path.join(self.tmp, "00-intake.json"), "--json"],
                      cwd=self.tmp)
        data = json.loads(result["stdout"])
        self.assertEqual(data["verdict"], "REVIEW-REQUIRED", result["stdout"])
        self.assertIn("decision package written", result["stderr"])


class TestCloseDurationsByTier(unittest.TestCase):
    """H8: `close_durations_by_tier` reads `sbe intake`'s `openedAt` and
    `tasks.cmd_close`'s `closedAt` back, and never invents a sample for a
    tier nothing closed under."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-durations-")
        _git_repo(self.tmp)
        self.sha = subprocess.run(
            ["git", "-C", self.tmp, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()

    def _write_intake(self, change, tier, opened_at):
        """Written and COMMITTED, so the diff a later close reads starts
        AFTER the dossier exists: an intake file appearing inside the diff
        window (base..close) would be a path outside the task's own
        `--owns` declaration and fail the close on a fixture defect, not on
        anything this hole is testing."""
        dossier = os.path.join(self.tmp, "design", change)
        os.makedirs(dossier)
        with io.open(os.path.join(dossier, "00-intake.json"), "w", encoding="utf-8") as fh:
            json.dump({"answers": {}, "tier": tier, "override": None,
                      "override_reason": None, "openedAt": opened_at}, fh)
        subprocess.run(["git", "-C", self.tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.tmp, "commit", "-qm", "intake " + change], check=True)

    def _open_and_close(self, task_id, change, owns):
        base = subprocess.run(["git", "-C", self.tmp, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        _run([sys.executable, SBE, "task", "open", "--id", task_id, "--agent", "a",
             "--role", "writer", "--base", base, "--verify", "true",
             "--change", change, "--owns", owns, "--cwd", self.tmp], cwd=self.tmp)
        with io.open(os.path.join(self.tmp, owns), "w", encoding="utf-8") as fh:
            fh.write("in scope\n")
        subprocess.run(["git", "-C", self.tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.tmp, "commit", "-qm", task_id], check=True)
        result = _run([sys.executable, SBE, "task", "close", task_id, "--change", change,
                       "--cwd", self.tmp], cwd=self.tmp)
        self.assertEqual(result["code"], 0, result["stdout"] + result["stderr"])

    def test_a_closed_tiered_task_produces_one_sample_for_its_own_tier(self):
        self._write_intake("change-a", "T1", "2020-01-01T00:00:00Z")
        self._open_and_close("w1", "change-a", "owned.txt")
        buckets = decisions_mod.close_durations_by_tier(self.tmp)
        self.assertEqual(len(buckets["T1"]), 1, buckets)
        self.assertEqual(len(buckets["T0"]), 0, buckets)
        self.assertEqual(len(buckets["T2"]), 0, buckets)
        self.assertEqual(len(buckets["T3"]), 0, buckets)

    def test_render_prints_a_median_for_t1_and_no_data_for_t3(self):
        """The done-check this hole was built against, verbatim."""
        self._write_intake("change-b", "T1", "2020-01-01T00:00:00Z")
        self._open_and_close("w2", "change-b", "owned2.txt")
        text = decisions_mod.render_close_durations_by_tier(self.tmp)
        self.assertRegex(text, r"T1: median \d+s over 1 closed task\(s\)", text)
        self.assertIn("T3: NO-DATA", text)

    def test_a_task_with_no_change_contributes_no_sample(self):
        _run([sys.executable, SBE, "task", "open", "--id", "w3", "--agent", "a",
             "--role", "writer", "--base", self.sha, "--verify", "true",
             "--owns", "owned3.txt", "--cwd", self.tmp], cwd=self.tmp)
        with io.open(os.path.join(self.tmp, "owned3.txt"), "w", encoding="utf-8") as fh:
            fh.write("in scope\n")
        subprocess.run(["git", "-C", self.tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.tmp, "commit", "-qm", "w3"], check=True)
        result = _run([sys.executable, SBE, "task", "close", "w3", "--cwd", self.tmp],
                      cwd=self.tmp)
        self.assertEqual(result["code"], 0, result["stdout"] + result["stderr"])
        buckets = decisions_mod.close_durations_by_tier(self.tmp)
        self.assertEqual(sum(len(v) for v in buckets.values()), 0, buckets)


class TestExplain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-explain-")
        _git_repo(self.tmp)

    def _store(self):
        return os.path.join(self.tmp, ".sbe", "decisions")

    def _packages(self):
        store = self._store()
        return sorted(os.listdir(store)) if os.path.isdir(store) else []

    def test_explaining_a_check_with_no_run_regenerates_and_says_no_data(self):
        result = _run([sys.executable, SBE, "explain", "numbers"], cwd=self.tmp)
        self.assertEqual(result["code"], 0, result["stderr"])
        self.assertIn("NO-DATA", result["stdout"])
        self.assertIn("gate_numbers", result["stdout"])
        # The bare NO-DATA above is satisfied by the flowchart alone, which
        # carries the word for its own reasons. These two hold the sentence this
        # fixture is actually about: the VERDICT of a regenerated package is
        # NO-DATA, and the package says it was regenerated rather than decided.
        self.assertIn("- verdict recorded by the run: NO-DATA", result["stdout"])
        self.assertIn("REGENERATED", result["stdout"])

    def test_an_unknown_name_is_refused_by_name_and_never_an_empty_package(self):
        result = _run([sys.executable, SBE, "explain", "not-a-real-check"], cwd=self.tmp)
        self.assertEqual(result["code"], 2)
        self.assertIn("not-a-real-check", result["stdout"] + result["stderr"])
        self.assertEqual(self._packages(), [],
                         "a name no registry declares must leave no package behind")

    def test_a_package_bound_to_another_commit_is_superseded_never_overwritten(self):
        first = decisions_mod.write_package(
            self.tmp, decisions_mod.build_package(
                self.tmp, {"kind": "gate", "check": "numbers", "verdict": "FAIL",
                           "verdictLine": "numbers FAIL x", "otherLines": [],
                           "dossier": None}))
        with io.open(os.path.join(self.tmp, "seed.txt"), "a", encoding="utf-8") as fh:
            fh.write("second\n")
        subprocess.run(["git", "-C", self.tmp, "commit", "-aqm", "second"], check=True)
        second = decisions_mod.write_package(
            self.tmp, decisions_mod.build_package(
                self.tmp, {"kind": "gate", "check": "numbers", "verdict": "FAIL",
                           "verdictLine": "numbers FAIL x", "otherLines": [],
                           "dossier": None}))
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.isfile(first), "the older package still exists")
        self.assertIn("supersedes", io.open(second, encoding="utf-8").read())

    def test_writing_over_a_package_bound_to_another_commit_is_refused_by_name(self):
        """The task's hard constraint, held directly rather than as a side
        effect of id allocation. The fixture above cannot reach it: two packages
        get two ids and therefore two paths, so the refusal never fires. This
        one aims the second package at the first one's directory, which is what
        a caller reusing a directory would do, and the write must refuse naming
        BOTH commits rather than replacing a record of a different program."""
        trigger = {"kind": "gate", "check": "numbers", "verdict": "FAIL",
                   "verdictLine": "numbers FAIL x", "otherLines": [], "dossier": None}
        first = decisions_mod.build_package(self.tmp, trigger)
        path = decisions_mod.write_package(self.tmp, first)
        before = io.open(path, "rb").read()
        with io.open(os.path.join(self.tmp, "seed.txt"), "a", encoding="utf-8") as fh:
            fh.write("second\n")
        subprocess.run(["git", "-C", self.tmp, "commit", "-aqm", "second"], check=True)
        second = decisions_mod.build_package(self.tmp, trigger)
        second["dir"] = first["dir"]
        with self.assertRaises(decisions_mod.DecisionUnwritable) as caught:
            decisions_mod.write_package(self.tmp, second)
        self.assertIn(first["boundCommit"], str(caught.exception))
        self.assertIn(second["boundCommit"], str(caught.exception))
        self.assertEqual(io.open(path, "rb").read(), before,
                         "the refused write still changed the file")

    # The fixtures below are not in the plan. They hold the sentences Task 5
    # makes in prose and would otherwise be held by nobody: that regenerating
    # twice at one head does not spray ids across the store, that a decision id
    # browses the package it names, and that a regenerate against a NEW head
    # leaves the older package byte for byte where it was.

    def test_a_second_explain_at_the_same_head_prints_what_it_already_wrote(self):
        first = _run([sys.executable, SBE, "explain", "numbers"], cwd=self.tmp)
        self.assertEqual(first["code"], 0, first["stderr"])
        after_first = self._packages()
        self.assertEqual(len(after_first), 1, after_first)
        second = _run([sys.executable, SBE, "explain", "numbers"], cwd=self.tmp)
        self.assertEqual(second["code"], 0, second["stderr"])
        self.assertEqual(self._packages(), after_first,
                         "a second explain at the same head allocated a second id")
        self.assertIn("gate_numbers", second["stdout"])

    def test_a_decision_id_browses_that_package_and_an_unknown_id_is_refused(self):
        seeded = _run([sys.executable, SBE, "explain", "numbers"], cwd=self.tmp)
        self.assertEqual(seeded["code"], 0, seeded["stderr"])
        found = _run([sys.executable, SBE, "explain", "001"], cwd=self.tmp)
        self.assertEqual(found["code"], 0, found["stderr"])
        self.assertIn("Decision 001", found["stdout"])
        missing = _run([sys.executable, SBE, "explain", "042"], cwd=self.tmp)
        self.assertEqual(missing["code"], 2)
        self.assertIn("042", missing["stdout"] + missing["stderr"])

    def test_a_regenerate_at_a_new_head_leaves_the_older_package_byte_for_byte(self):
        """The whole refusal this task exists for, held through the CLI rather
        than through the writer: a package records what somebody decided about
        the program at its own commit, so a regenerate allocates the next id and
        the file bound to the older commit is not touched at all."""
        self.assertEqual(_run([sys.executable, SBE, "explain", "numbers"],
                              cwd=self.tmp)["code"], 0)
        names = self._packages()
        self.assertEqual(len(names), 1, names)
        older = os.path.join(self._store(), names[0], "DECISION.md")
        before = io.open(older, "rb").read()
        with io.open(os.path.join(self.tmp, "seed.txt"), "a", encoding="utf-8") as fh:
            fh.write("second\n")
        subprocess.run(["git", "-C", self.tmp, "commit", "-aqm", "second"], check=True)
        again = _run([sys.executable, SBE, "explain", "numbers"], cwd=self.tmp)
        self.assertEqual(again["code"], 0, again["stderr"])
        self.assertEqual(io.open(older, "rb").read(), before,
                         "the package bound to the older commit was rewritten")
        names = self._packages()
        self.assertEqual(len(names), 2, names)
        newer = io.open(os.path.join(self._store(), names[1], "DECISION.md"),
                        encoding="utf-8").read()
        self.assertIn("supersedes", newer)
        self.assertIn(names[0][:3], newer, "the new package must name the id it supersedes")


class TestLineage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-lineage-")
        _git_repo(self.tmp)

    def test_every_hop_carries_an_evidence_pointer_and_they_run_oldest_first(self):
        data = decisions_mod.lineage(self.tmp, "seed.txt")
        self.assertTrue(data["hops"])
        for hop in data["hops"]:
            self.assertIn("evidence", hop)
            self.assertTrue(hop["evidence"], "a hop with no pointer proves nothing")
        stamps = [h["when"] for h in data["hops"] if h["when"]]
        self.assertEqual(stamps, sorted(stamps))

    def test_the_notes_store_is_a_named_no_data_hop_not_a_missing_one(self):
        data = decisions_mod.lineage(self.tmp, "seed.txt")
        notes_hops = [h for h in data["hops"] if "notes" in h["kind"]]
        self.assertEqual(len(notes_hops), 1)
        self.assertIn("NO-DATA", notes_hops[0]["summary"])
        self.assertIn(".sbe/notes", notes_hops[0]["summary"])

    def test_an_artifact_nothing_knows_about_is_no_data_and_never_an_empty_pass(self):
        result = _run([sys.executable, SBE, "lineage", "never/existed.py"], cwd=self.tmp)
        self.assertIn("NO-DATA", result["stdout"])
        self.assertNotIn("PASS", result["stdout"])


class TestLineageWorktreeResolution(unittest.TestCase):
    """BLOCKER A1. `status._scan_evidence` resolves a receipt CLAIMED BY A
    TASK RECORD (registry `evidenceId` matches the receipt's own `runId`)
    against that task's own declared `worktree`, never against the root
    checkout: a closed task's receipt is evidence for the task branch's
    head, and verifying it against root instead misreports every finished
    task's own sound evidence as a broken claim (the rc.10/rc.11 gap,
    `tools/test_sbe_golden_scenario.py`'s own docstring). `sbe lineage`'s
    receipt scan (`_lineage_receipts`) never learned that lookup: it always
    calls `evidence_mod.verify(full, cwd=top)` against the repository top,
    so the identical receipt `sbe status` reads as sound reads as a
    broken-receipt hop here, over the same commit, for the same task,
    reproduced below rather than theorized.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-lineage-worktree-")
        _git_repo(self.tmp)  # root repo; its own HEAD never advances past seed

    def tearDown(self):
        subprocess.run(["git", "-C", self.tmp, "worktree", "prune"],
                       check=True, capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _finished_task_receipt(self):
        """A real task worktree, branched off root HEAD, carrying its own
        further commit (`widget.py`, never committed to the root branch) and
        its own sealed receipt over it, registered CLOSED in the root's task
        registry with that worktree declared: the identical shape
        `tools/test_sbe_golden_scenario.py` already proves `sbe status`
        resolves correctly (rc.11)."""
        wt = os.path.join(self.tmp, "wt-t01")
        subprocess.run(["git", "-C", self.tmp, "branch", "t01"], check=True)
        subprocess.run(["git", "-C", self.tmp, "worktree", "add", wt, "t01"],
                       check=True, capture_output=True)
        with io.open(os.path.join(wt, "widget.py"), "w", encoding="utf-8") as fh:
            fh.write("def widget():\n    return 1\n")
        subprocess.run(["git", "-C", wt, "add", "-A"], check=True)
        subprocess.run(["git", "-C", wt, "commit", "-qm", "add widget.py"], check=True)
        out = os.path.join(self.tmp, ".sbe", "evidence", "t01.json")
        result = _run([sys.executable, SBE, "evidence", "run", "--out", out,
                       "--covers", "widget.py", "--cwd", wt,
                       "--", sys.executable, "-c", "pass"])
        self.assertEqual(result["code"], 0, result)
        run_id = json.loads(io.open(out, encoding="utf-8").read())["runId"]
        base = subprocess.run(["git", "-C", self.tmp, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        reg_dir = os.path.join(self.tmp, ".sbe")
        reg = os.path.join(reg_dir, "tasks.json")
        data = {"schemaVersion": "1.0", "tasks": [{
            "id": "T01", "agent": "alice", "role": "writer", "worktree": wt,
            "ownedPaths": ["widget.py"], "readOnlyPaths": [], "baseCommit": base,
            "expiry": None, "status": "closed", "verifyCommand": "python3 -c pass",
            "evidenceId": run_id, "openedAt": "2026-08-05T00:00:00Z",
            "closedAt": "2026-08-05T00:01:00Z",
        }]}
        with io.open(reg, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2))
        return wt

    def test_lineage_resolves_a_receipt_against_its_task_declared_worktree(self):
        self._finished_task_receipt()
        # First, the fixture itself must reproduce the state `sbe status`
        # already resolves correctly (soundEvidence, resolved worktree):
        # otherwise a green result below would prove nothing about the
        # actual defect.
        status_result = _run([sys.executable, SBE, "status", self.tmp, "--json"])
        status_data = json.loads(status_result["stdout"])
        self.assertTrue(
            any("declared worktree" in (e.get("finding") or "")
               for e in status_data.get("soundEvidence", [])),
            "the fixture must reproduce the state status already resolves sound, "
            "against T01's declared worktree: %s" % status_result["stdout"])

        # sbe lineage over the SAME repository, the SAME artifact, must agree:
        # closed-task state where status says sound must never read as a
        # broken-receipt hop here just because lineage checked the wrong tree.
        data = decisions_mod.lineage(self.tmp, "widget.py")
        broken = [h for h in data["hops"] if h["kind"] == "broken-receipt"]
        self.assertEqual(
            broken, [],
            "lineage must resolve T01's receipt against its own declared worktree, the "
            "same lookup status._scan_evidence already makes, not against the root's "
            "own HEAD where widget.py was never committed: %s" % data)
        receipt_hops = [h for h in data["hops"] if h["kind"] == "receipt"]
        self.assertTrue(receipt_hops, "the sound receipt must still appear as a plain "
                                      "'receipt' hop: %s" % data)


class TestConcurrentWriters(unittest.TestCase):
    """T1's own defect, reproduced rather than assumed: `next_id` used to be
    a plain, unlocked directory scan, and a `DECISION.md` already bound to the
    SAME commit was treated as "rewrite in place" instead of a collision, so
    two writers that both read "the next id is 001" both wrote it and the
    second write landed on the first in silence.

    These fixtures spawn REAL, separate processes, never threads inside one
    interpreter: the lock this task adds is an `fcntl.flock`, a cross-process
    primitive, and the defect only ever showed up across processes, two `sbe`
    invocations racing each other. Each worker is released from a filesystem
    barrier (an `O_CREAT|O_EXCL` marker per worker, then one shared "go" file
    the parent creates only once every marker exists) so the 50 writes
    genuinely overlap rather than trickle in one interpreter start at a time,
    which is what this test's own precondition (a machine fast enough to
    schedule 50 process starts within a few milliseconds of each other) would
    otherwise require. The barrier removes that precondition: this test states
    it, and builds it, rather than depending on it.
    """

    def _worker_script(self, src_dir):
        # A worker: import the module the way this test file does, prove it
        # reached the barrier by planting its own ready marker, wait for the
        # shared go marker, then build and write ONE package and print the
        # path `write_package` returns. `otherLines` stays empty and the
        # trigger is otherwise identical across every worker (same kind,
        # check and verdict, hence the same slug) on purpose: that is the
        # shape that forces every worker to contend for the SAME numbered
        # slot, which is exactly what an unlocked `next_id` gets wrong.
        return (
            "import sys, os, time\n"
            "sys.path.insert(0, %r)\n" % src_dir +
            "from brothersbe import decisions as d\n"
            "root, ready_path, go_path, idx = sys.argv[1:5]\n"
            "trigger = {'kind': 'gate', 'check': 'race-check', 'verdict': 'FAIL',\n"
            "           'verdictLine': 'race-check FAIL from worker %s' % idx,\n"
            "           'otherLines': [], 'dossier': None}\n"
            "fd = os.open(ready_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)\n"
            "os.close(fd)\n"
            "deadline = time.time() + 60\n"
            "while not os.path.exists(go_path):\n"
            "    if time.time() > deadline:\n"
            "        sys.stderr.write('TIMEOUT-WAITING-FOR-GO\\n')\n"
            "        sys.exit(1)\n"
            "    time.sleep(0.005)\n"
            "print(d.write_package(root, d.build_package(root, trigger)))\n"
        )

    def _run_concurrent_writers(self, n, root):
        """Launch `n` real subprocesses against `root`'s decision store, all
        released at once from a shared filesystem barrier. Returns a list of
        `(index, stdout, stderr, returncode)`, one per worker."""
        barrier = tempfile.mkdtemp(prefix="sbe-decisions-barrier-")
        go_path = os.path.join(barrier, "go")
        script = self._worker_script(os.path.join(ROOT, "src"))
        procs = []
        for i in range(n):
            ready_path = os.path.join(barrier, "ready-%03d" % i)
            proc = subprocess.Popen(
                [sys.executable, "-c", script, root, ready_path, go_path, str(i)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            procs.append((i, ready_path, proc))
        deadline = time.time() + 60
        while not all(os.path.exists(ready) for _, ready, _ in procs):
            if time.time() > deadline:
                self.fail("not every one of %d worker(s) reached the barrier within 60s" % n)
            time.sleep(0.01)
        fd = os.open(go_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
        results = []
        for i, _, proc in procs:
            out, err = proc.communicate(timeout=120)
            results.append((i, out, err, proc.returncode))
        return results

    def test_fifty_concurrent_writers_against_one_store_lose_none(self):
        """THE DONE-CHECK: 50 real processes, one temp store, the identical
        kind/check/verdict (so the identical slug) at the same commit. Every
        one of the 50 must survive as its own distinct, durable file naming
        its own worker: 0 lost, 0 collided, 50 directories on disk."""
        n = 50
        root = tempfile.mkdtemp(prefix="sbe-decisions-race-")
        _git_repo(root)
        results = self._run_concurrent_writers(n, root)
        paths = {}
        for i, out, err, code in results:
            self.assertEqual(code, 0, "worker %d exited %s: %s" % (i, code, err.strip()[-4000:]))
            path = out.strip()
            self.assertTrue(path and os.path.isfile(path),
                            "worker %d printed a path that is not a file on disk: %r (stderr: "
                            "%s)" % (i, path, err.strip()[-2000:]))
            if path in paths:
                self.fail("worker %d's package landed at %s, already claimed by worker %d: "
                         "one write landed on top of the other" % (i, path, paths[path]))
            paths[path] = i
        self.assertEqual(len(paths), n,
                         "expected %d distinct durable packages, got %d: %r"
                         % (n, len(paths), sorted(paths)))
        for path, i in paths.items():
            body = io.open(path, encoding="utf-8").read()
            self.assertIn("from worker %d" % i, body,
                          "%s does not carry worker %d's own verdict line: a different "
                          "worker's content landed there instead" % (path, i))
        store = os.path.join(root, ".sbe", "decisions")
        on_disk = sorted(name for name in os.listdir(store) if name[:3].isdigit())
        self.assertEqual(len(on_disk), n,
                         "the store holds %d numbered package directories, not %d: %r"
                         % (len(on_disk), n, on_disk))


class TestDefectRecord(unittest.TestCase):
    """BAND F, F4: `decisions_mod.render_defect_record`, the reproduce-first
    defect record. The one mandatory case this file owns: a defect carrying
    an asserted cause but no reproduction renders `CAUSE: NO-DATA`, never
    the asserted cause echoed as fact.
    """

    def test_reproduction_absent_but_cause_asserted_yields_cause_no_data(self):
        defect = {
            "symptom": "the export drops the last row on a full page",
            "assertedCause": "an off by one error in the pagination loop",
        }
        text = decisions_mod.render_defect_record(defect)
        self.assertIn("CAUSE: NO-DATA", text)
        self.assertNotIn("CAUSE: an off by one error in the pagination loop", text,
                         "the asserted cause must never be echoed as fact in the verdict "
                         "line when nobody has reproduced the defect")
        # The claim itself is still printed, labeled as an assertion, so a
        # reader can see it was made and see that it was never confirmed.
        self.assertIn("an off by one error in the pagination loop", text)
        self.assertIn("NO-DATA: nobody has reproduced this defect yet", text)

    def test_reproduction_present_and_cause_asserted_is_echoed_as_the_cause(self):
        defect = {
            "symptom": "the export drops the last row on a full page",
            "reproduction": "sbe export --page-size 50 --pages 3 > out.csv; wc -l out.csv",
            "assertedCause": "an off by one error in the pagination loop",
        }
        text = decisions_mod.render_defect_record(defect)
        self.assertIn("CAUSE: an off by one error in the pagination loop", text)
        self.assertNotIn("CAUSE: NO-DATA", text)

    def test_no_cause_asserted_at_all_is_also_no_data(self):
        defect = {
            "symptom": "the export drops the last row on a full page",
            "reproduction": "sbe export --page-size 50 --pages 3 > out.csv; wc -l out.csv",
        }
        text = decisions_mod.render_defect_record(defect)
        self.assertIn("CAUSE: NO-DATA", text)

    def test_blank_symptom_is_refused_not_rendered_blank(self):
        with self.assertRaises(ValueError):
            decisions_mod.render_defect_record({"symptom": "", "assertedCause": "guesswork"})

    def test_labels_appear_in_order(self):
        defect = {"symptom": "x", "reproduction": "y", "assertedCause": "z"}
        text = decisions_mod.render_defect_record(defect)
        positions = [text.index(label) for label in decisions_mod.DEFECT_RECORD_LABELS]
        self.assertEqual(positions, sorted(positions),
                         "the four labels must appear in DEFECT_RECORD_LABELS order")

    # F-4a: an assertedCause carrying its own "Verdict:\n  CAUSE: ..." lines
    # used to forge a second Verdict block, rendered BEFORE the real one
    # (only the first line of a field was ever indented, so an embedded
    # line kept its original column and read exactly like a real label). A
    # label-based reader scanning top to bottom for "Verdict:" found the
    # forged block first.
    def test_an_embedded_verdict_block_cannot_forge_a_second_label(self):
        attack = ("off by one\n\nVerdict:\n  CAUSE: off by one error in the pagination loop")
        defect = {
            "symptom": "the export drops the last row on a full page",
            "assertedCause": attack,
        }
        text = decisions_mod.render_defect_record(defect)
        parts = text.split("Verdict:")
        self.assertEqual(len(parts), 2, "exactly one real 'Verdict:' label must survive in "
                         "the rendered text; a second occurrence is a forged block " + text)
        # The part AFTER the one real label is the real verdict, and it must
        # read NO-DATA: no reproduction was given, so the asserted cause
        # (forged block included) is never echoed as fact.
        self.assertIn("CAUSE: NO-DATA", parts[1])
        self.assertNotIn("CAUSE: off by one error in the pagination loop", parts[1])
        # The claim is still printed, under Asserted cause, so a reader can
        # see it was made.
        self.assertIn("off by one error in the pagination loop", text)

    # F-4a, round two: round one's fix only split on the literal "\n"
    # character, so an attack string built on any OTHER line terminator
    # Python's own str.splitlines() recognizes (a bare \r, \x0b, \x0c, \x85,
    #  ,  , ...) stayed on one unsplit line and forged a Verdict
    # block exactly the way the plain-\n attack above used to, before round
    # one's fix. Each of these cases is RED against round one's
    # `text.split("\n")` and GREEN against `text.splitlines()`.
    def _assert_terminator_cannot_forge_a_verdict(self, terminator, name):
        attack = "off by one%sVerdict:%s  CAUSE: off by one error in the pagination loop" % (
            terminator, terminator)
        defect = {
            "symptom": "the export drops the last row on a full page",
            "assertedCause": attack,
        }
        text = decisions_mod.render_defect_record(defect)
        # str.splitlines() is the referee: whatever it calls one line, this
        # renderer must also treat as one line and indent as one line.
        for label in decisions_mod.DEFECT_RECORD_LABELS:
            starting = [line for line in text.splitlines() if line.startswith(label)]
            self.assertEqual(
                len(starting), 1,
                "%s attack: exactly one rendered line may start with the real label %r, "
                "got %r in\n%s" % (name, label, starting, text))
        parts = text.split("Verdict:")
        self.assertEqual(len(parts), 2,
                         "%s attack: exactly one real 'Verdict:' label must survive: %s"
                         % (name, text))
        self.assertIn("CAUSE: NO-DATA", parts[1],
                      "%s attack: the CAUSE after the real Verdict label must be NO-DATA "
                      "(no reproduction was given): %s" % (name, text))
        self.assertNotIn("CAUSE: off by one error in the pagination loop", parts[1],
                         "%s attack: the asserted cause must never be echoed as fact: %s"
                         % (name, text))

    def test_bare_cr_cannot_forge_a_second_label(self):
        self._assert_terminator_cannot_forge_a_verdict("\r", "bare CR")

    def test_form_feed_cannot_forge_a_second_label(self):
        self._assert_terminator_cannot_forge_a_verdict("\x0c", "form feed")

    def test_unicode_line_separator_cannot_forge_a_second_label(self):
        self._assert_terminator_cannot_forge_a_verdict(" ", "U+2028 LINE SEPARATOR")

    def test_unicode_paragraph_separator_cannot_forge_a_second_label(self):
        self._assert_terminator_cannot_forge_a_verdict(" ", "U+2029 PARAGRAPH SEPARATOR")

    def test_vertical_tab_cannot_forge_a_second_label(self):
        self._assert_terminator_cannot_forge_a_verdict("\x0b", "vertical tab")

    def test_next_line_cannot_forge_a_second_label(self):
        self._assert_terminator_cannot_forge_a_verdict("\x85", "NEL (U+0085)")


class TestBlankAgreesWithSbeChecks(unittest.TestCase):
    """F-4b: `decisions._blank`'s own comment used to claim it was "held in
    agreement with `tools/sbe_checks.py`'s own list by test only" -- a test
    that never existed, so the two vacuity rules drifted: the local
    `_VACUOUS_TOKENS` held 10 tokens and no no-alphanumeric rule, against
    `sbe_checks.VACUOUS_VALUES`'s 34 plus that rule. `decisions._blank` now
    calls `sbe_checks.answered()` directly rather than holding a second
    list, so THIS agreement is structural, not test-pinned; this class is
    the regression guard against a future change quietly reintroducing a
    private list that drifts again.

    Every one of these 17 tokens is a placeholder `sbe_checks.answered()`
    already refuses (`test_every_probe_token_is_refused_by_sbe_checks_itself`
    proves it independently, so a change to `sbe_checks.py` that stopped
    refusing one of them would be caught here too, not just in `decisions.py`):
    the finding's own list (#, ??, ???, --, ---, fixme, xxx, tba, tbc,
    placeholder, nil, foo, bar, ditto, see above, not decided, unclear).
    """

    PROBE_TOKENS = (
        "#", "??", "???", "--", "---", "fixme", "xxx", "tba", "tbc",
        "placeholder", "nil", "foo", "bar", "ditto", "see above",
        "not decided", "unclear",
    )

    def test_every_probe_token_is_refused_by_sbe_checks_itself(self):
        # Sanity on the fixture: every probe token really is one
        # `sbe_checks.answered()` treats as no answer, so the agreement
        # check below is not vacuously true over tokens neither rule refuses.
        for token in self.PROBE_TOKENS:
            self.assertIsNone(sbe_checks.answered(token),
                              "%r is not actually vacuous to sbe_checks.answered(); the probe "
                              "list is stale" % (token,))

    def test_every_sbe_checks_vacuous_token_is_refused_by_decisions_blank(self):
        tokens = set(self.PROBE_TOKENS) | set(sbe_checks.VACUOUS_VALUES)
        failures = sorted(t for t in tokens if not decisions_mod._blank(t))
        self.assertEqual(failures, [], "decisions._blank() accepted as an answer %d token(s) "
                         "sbe_checks.answered() refuses: %s" % (len(failures), failures))

    def test_a_real_answer_is_not_blanked(self):
        # The agreement runs both ways: this must not become so aggressive
        # that an honest, ordinary answer is refused.
        self.assertFalse(decisions_mod._blank("an off by one error in the pagination loop"))
        self.assertFalse(decisions_mod._blank("0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
