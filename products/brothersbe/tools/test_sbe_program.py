"""Tests for `src/brothersbe/program.py`, the program reporter (spec:
design/phase-0/SPEC.md, Lane A).

Two fixture families:

  1. This repository's own `program/` directory, exercised as-is: the parser
     must load every shipped `PROGRAM.yaml` and `program/work-items/*.yaml`
     file with zero parse errors, because those are the real files this
     module has to work against on day one.
  2. Synthetic fixtures under a temp directory, for the edge cases the
     shipped files do not (yet) exercise: mapping-shaped risks, unknown
     statuses, malformed items, and the honesty rule that a status word
     never becomes a percentage.
"""
import io
import json
import os
import re
import sys
import tempfile
import shutil
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import program as program_mod  # noqa: E402

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


def _write(path, text):
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


PROGRAM_YAML_MIN = """\
program: test-program
objective: >
  A short objective spanning
  two lines.
version_target: 9.9.9
product_owner: Someone
status: in_flight
plan: MASTER-PLAN.md
token_budget:
  planned_total: 1000
waves:
  - id: wave-0
    name: Wave zero
"""


def _make_root(tmp, program_yaml=PROGRAM_YAML_MIN, items=None):
    root = os.path.join(tmp, "root_%d" % len(os.listdir(tmp)) if os.path.isdir(tmp) else 0)
    os.makedirs(root)
    _write(os.path.join(root, "program", "PROGRAM.yaml"), program_yaml)
    _write(os.path.join(root, "program", "MASTER-PLAN.md"), "plan\n")
    if items:
        for name, text in items.items():
            _write(os.path.join(root, "program", "work-items", name), text)
    return root


class TestRealLedger(unittest.TestCase):
    """The parser against this repository's own shipped ledger."""

    def test_program_yaml_loads(self):
        data = program_mod.load_program(ROOT)
        self.assertIsInstance(data.program, dict)
        self.assertEqual(data.program.get("program"), "brothersbe-public-release")

    def test_every_shipped_work_item_parses_with_zero_errors(self):
        data = program_mod.load_program(ROOT)
        item_files = [n for n in os.listdir(os.path.join(ROOT, "program", "work-items"))
                     if n.endswith(".yaml")]
        self.assertEqual(
            data.parse_errors, [],
            "the shipped ledger must parse cleanly: %r" % (data.parse_errors,))
        self.assertEqual(len(data.items), len(item_files))

    def test_build_program_report_on_real_ledger(self):
        # SHAPE assertions only, never a hardcoded item count: the live
        # ledger's item count and content grow over time (seven items as of
        # this writing, more as later work items land), and a test pinned
        # to today's count or today's exact measured/unmeasured split is
        # the writer's defect to fix, not the ledger's, per the round-2
        # brief ("assert SHAPE rather than counts").
        report = program_mod.build_program_report(ROOT)
        self.assertEqual(report["schemaVersion"], program_mod.SCHEMA_VERSION)
        self.assertEqual(report["parseErrors"], [])
        self.assertEqual(report["summary"]["total_count"], len(report["items"]))
        self.assertEqual(report["summary"]["excluded_count"], 0)
        measured = [i for i in report["items"] if i["progress_percent"] is not None]
        self.assertEqual(report["summary"]["measured_count"], len(measured))
        for item in report["items"]:
            self.assertIn(item["progress_source"],
                           ("declared", "derived from acceptance", "not measured"))
            if item["progress_source"] == "not measured":
                self.assertIsNone(item["progress_percent"])
            else:
                self.assertIsNotNone(item["progress_percent"])
        if measured:
            self.assertIsNotNone(report["summary"]["aggregate_percent"])
        else:
            self.assertIsNone(report["summary"]["aggregate_percent"])
        # Every shipped item is expected to declare a wave; whichever ones
        # are undeclared today or not is a ledger-data fact this test does
        # not pin down (see TestGanttSections for the behaviour itself,
        # proved against a synthetic fixture per the round-2 brief).
        self.assertIsInstance(report["undeclaredWaves"], list)

    def test_all_shipped_items_load_when_present(self):
        # Confirms the loader has no hardcoded item count of its own: it
        # loads however many *.yaml files exist under program/work-items,
        # whichever plan they carry today. The assertion is against the
        # directory listing itself, never a magic number, so it is already
        # true for any item count.
        data = program_mod.load_program(ROOT)
        item_files = [n for n in os.listdir(os.path.join(ROOT, "program", "work-items"))
                     if n.endswith(".yaml")]
        self.assertEqual(len(data.items), len(item_files))
        self.assertEqual(data.parse_errors, [])

    def test_multiline_plain_scalar_risk_parses(self):
        # A plain (unquoted) scalar risks[] entry that wraps across three
        # lines inside a list item, the exact shape a real shipped item once
        # carried (BR-0201, retired with the seven-wave November plan). Kept
        # as a synthetic fixture so the parser edge case stays pinned after
        # the ledger it was first found in was replaced. It must fold to one
        # sentence with no embedded newline and no parse error.
        tmp = tempfile.mkdtemp(prefix="sbe-program-risk-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        items = {
            "LOOP-X.yaml": (
                "id: LOOP-X\n"
                "title: T\n"
                "status: not_started\n"
                "risks:\n"
                "  - The shipped slice is prompt-guided (skills instructing the model), not\n"
                "    engine-backed. It depends on the model following the skill's wording\n"
                "    each run, not on a deterministic program.\n"
            ),
        }
        root = _make_root(tmp, items=items)
        data = program_mod.load_program(root)
        self.assertEqual(data.parse_errors, [])
        item = data.items[0]
        self.assertEqual(len(item["risks"]), 1)
        risk_text = item["risks"][0]["risk"]
        self.assertNotIn("\n", risk_text)
        self.assertIn("prompt-guided", risk_text)
        self.assertIn("engine-backed", risk_text)

    def test_unknown_key_does_not_raise(self):
        # An unknown key (a real shipped item, BR-0301, retired with the
        # seven-wave November plan, once carried `acceptance_notes`) this
        # module's schema never lists. Reading it must never raise: an
        # unknown key is simply not surfaced in the normalized item, per the
        # schema documented in SPEC.md (only the named fields are
        # extracted). Kept as a synthetic fixture so this edge case stays
        # pinned after the ledger it was first found in was replaced.
        tmp = tempfile.mkdtemp(prefix="sbe-program-unknownkey-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        items = {
            "LOOP-X.yaml": (
                "id: LOOP-X\n"
                "title: T\n"
                "status: partially_done\n"
                "acceptance_notes: >\n"
                "  Acceptance criteria for this item are being finalized.\n"
            ),
        }
        root = _make_root(tmp, items=items)
        data = program_mod.load_program(root)
        self.assertEqual(data.parse_errors, [])
        item = data.items[0]
        self.assertEqual(item["status"], "partially_done")
        self.assertNotIn("acceptance_notes", item)

    def test_four_shipped_status_spellings_all_parse(self):
        # The exact four spellings the round-2 brief named as present in
        # the shipped ledger: "not started", "not_started",
        # "partially done", "in_progress". None may raise, and each must
        # normalize to its canonical form.
        for spelling, canonical in (
            ("not started", "not_started"),
            ("not_started", "not_started"),
            ("partially done", "partially_done"),
            ("in_progress", "in_progress"),
        ):
            tmp = tempfile.mkdtemp(prefix="sbe-program-status-")
            self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
            items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: %s\n" % spelling}
            root = _make_root(tmp, items=items)
            data = program_mod.load_program(root)
            self.assertEqual(data.parse_errors, [], spelling)
            self.assertEqual(data.items[0]["status"], canonical, spelling)

    def test_render_status_md_runs_and_contains_headline(self):
        report = program_mod.build_program_report(ROOT)
        md = program_mod.render_status_md(report)
        self.assertIn("brothersbe-public-release", md)
        self.assertIn("items measured", md)
        self.assertIn("```mermaid", md)
        self.assertIn("gantt", md)


class TestProgressComputation(unittest.TestCase):
    """The load-bearing honesty rule: a status word never becomes a
    percentage."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_in_progress_with_no_percent_and_no_acceptance_met_is_not_measured(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\n"
                "title: In progress with nothing recorded\n"
                "status: in_progress\n"
                "acceptance:\n"
                "  - one thing\n"
                "  - another thing\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        item = report["items"][0]
        self.assertEqual(item["status"], "in_progress")
        self.assertEqual(item["progress_source"], "not measured")
        self.assertIsNone(item["progress_percent"])
        # And it must NOT contribute to the aggregate: one unmeasured item,
        # zero measured, so the aggregate is None and coverage is 0 of 1.
        self.assertIsNone(report["summary"]["aggregate_percent"])
        self.assertEqual(report["summary"]["measured_count"], 0)
        self.assertEqual(report["summary"]["total_count"], 1)

    def test_declared_percent_complete_is_reported_as_declared(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\n"
                "title: Declared progress\n"
                "status: in_progress\n"
                "percent_complete: 40\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        item = report["items"][0]
        self.assertEqual(item["progress_percent"], 40)
        self.assertEqual(item["progress_source"], "declared")
        self.assertEqual(report["summary"]["aggregate_percent"], 40)
        self.assertEqual(report["summary"]["measured_count"], 1)

    def test_acceptance_met_derives_a_percentage(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\n"
                "title: Derived progress\n"
                "status: in_progress\n"
                "acceptance:\n"
                "  - first check\n"
                "  - second check\n"
                "  - third check\n"
                "  - fourth check\n"
                "acceptance_met:\n"
                "  - first check\n"
                "  - second check\n"
                "  - third check\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        item = report["items"][0]
        self.assertEqual(item["progress_source"], "derived from acceptance")
        self.assertEqual(item["progress_percent"], 75)

    def test_aggregate_states_its_coverage(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: Measured\nstatus: done\npercent_complete: 100\n"
            ),
            "BR-B.yaml": (
                "id: BR-B\ntitle: Unmeasured\nstatus: not_started\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        self.assertEqual(report["summary"]["measured_count"], 1)
        self.assertEqual(report["summary"]["total_count"], 2)
        md = program_mod.render_status_md(report)
        self.assertIn("1 of 2 items measured.", md)


class TestRisks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_plain_string_risk_reads_no_mitigation_without_inventing_one(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: done\n"
                "risks:\n"
                "  - a plain string risk with no mitigation\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        risk = report["risks"][0]
        self.assertEqual(risk["risk"], "a plain string risk with no mitigation")
        self.assertIsNone(risk["severity"])
        self.assertEqual(risk["mitigation"], "no mitigation recorded")

    def test_mapping_risk_with_mitigation(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: done\n"
                "risks:\n"
                "  - risk: the vendor might change the API\n"
                "    mitigation: pin the version and add a contract test\n"
                "    severity: high\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        risk = report["risks"][0]
        self.assertEqual(risk["risk"], "the vendor might change the API")
        self.assertEqual(risk["mitigation"], "pin the version and add a contract test")
        self.assertEqual(risk["severity"], "high")

    def test_mapping_risk_missing_risk_key_is_a_parse_error(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: done\n"
                "risks:\n"
                "  - mitigation: a mitigation with no risk named\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        data = program_mod.load_program(root)
        self.assertEqual(data.items, [])
        self.assertEqual(len(data.parse_errors), 1)
        self.assertIn("BR-A.yaml", data.parse_errors[0]["path"])
        self.assertIn("risk", data.parse_errors[0]["reason"])

    def test_rendered_status_md_risk_table_shows_plain_string_risk_verbatim(self):
        # The report-level assertion above proves the dict is right; this
        # proves the SAME fact at the layer the founder actually reads
        # (STATUS.md), which round 1 found completely unasserted: a hard
        # coded "MITIGATION INVENTED" constant in render_status_md left all
        # 32 tests green.
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: done\n"
                "risks:\n"
                "  - a plain string risk with no mitigation\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        md = program_mod.render_status_md(report)
        self.assertIn(
            "| BR-A | a plain string risk with no mitigation | not recorded | no mitigation recorded |",
            md)

    def test_rendered_status_md_risk_table_shows_mapping_mitigation_verbatim(self):
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: done\n"
                "risks:\n"
                "  - risk: the vendor might change the API\n"
                "    mitigation: pin the version and add a contract test\n"
                "    severity: high\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        md = program_mod.render_status_md(report)
        self.assertIn(
            "| BR-A | the vendor might change the API | high | "
            "pin the version and add a contract test |",
            md)

    def test_rendered_status_md_escapes_pipe_in_mitigation_severity_and_item(self):
        # Only the risk cell was escaped before this fix; a `|` in the
        # mitigation text widened the table with extra columns and
        # corrupted the markdown.
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: done\n"
                "risks:\n"
                '  - risk: r\n'
                '    mitigation: "do a | b"\n'
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        md = program_mod.render_status_md(report)
        self.assertIn("do a \\| b", md)
        self.assertNotIn("do a | b |", md)
        row = [line for line in md.splitlines() if line.startswith("| BR-A |")][0]
        # Exactly 5 unescaped pipes bound a 4-cell row (the leading and
        # trailing bar plus 3 internal separators); a leaked raw '|' from
        # the mitigation text would add a 6th.
        self.assertEqual(row.count("|") - row.count("\\|"), 5)


PROGRAM_YAML_WITH_GROUPINGS = """\
program: test-program
objective: >
  short
version_target: 9.9.9
status: in_flight
plan: MASTER-PLAN.md
waves:
  - id: wave-0
    name: Wave zero
plan_waves:
  - id: loop-a
    name: Loop A
delivery_groupings:
  - id: team-x
    name: Team X grouping
"""


class TestGanttSections(unittest.TestCase):
    """The orchestrator's design decision for the round-1 gantt finding:
    sections come from the groupings PROGRAM.yaml declares across `waves`,
    `plan_waves` and `delivery_groupings`, rendered in that order; an item
    whose wave matches none of them still renders, in its own section after
    every declared one, and is named in `undeclaredWaves`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _root(self):
        items = {
            "BR-A.yaml": "id: BR-A\ntitle: A\nstatus: not_started\nwave: wave-0\n",
            "BR-B.yaml": "id: BR-B\ntitle: B\nstatus: not_started\nwave: loop-a\n",
            "BR-C.yaml": "id: BR-C\ntitle: C\nstatus: not_started\nwave: team-x\n",
            "BR-D.yaml": "id: BR-D\ntitle: D\nstatus: not_started\nwave: ghost-wave\n",
            "BR-E.yaml": "id: BR-E\ntitle: E\nstatus: not_started\n",
        }
        return _make_root(self.tmp, program_yaml=PROGRAM_YAML_WITH_GROUPINGS, items=items)

    def test_undeclared_wave_is_not_a_parse_error(self):
        report = program_mod.build_program_report(self._root())
        self.assertEqual(report["parseErrors"], [])
        self.assertEqual(len(report["items"]), 5)

    def test_undeclared_wave_is_named_in_undeclared_waves(self):
        report = program_mod.build_program_report(self._root())
        self.assertEqual(
            report["undeclaredWaves"],
            [{"wave": "ghost-wave", "itemIds": ["BR-D"]}])

    def test_declared_groupings_from_all_three_lists_order_the_gantt_sections(self):
        report = program_mod.build_program_report(self._root())
        gantt = program_mod.render_gantt(report)
        # Declared order: waves, then plan_waves, then delivery_groupings,
        # each using its recorded name; the undeclared wave's own section
        # comes after every declared one, and the no-wave item's section
        # comes last of all.
        wave_zero_at = gantt.find("section Wave zero")
        loop_a_at = gantt.find("section Loop A")
        team_x_at = gantt.find("section Team X grouping")
        ghost_at = gantt.find("section ghost-wave")
        unassigned_at = gantt.find("section unassigned")
        for pos in (wave_zero_at, loop_a_at, team_x_at, ghost_at, unassigned_at):
            self.assertNotEqual(pos, -1)
        self.assertLess(wave_zero_at, loop_a_at)
        self.assertLess(loop_a_at, team_x_at)
        self.assertLess(team_x_at, ghost_at)
        self.assertLess(ghost_at, unassigned_at)

    def test_undeclared_wave_appears_in_status_md_headline(self):
        report = program_mod.build_program_report(self._root())
        md = program_mod.render_status_md(report)
        self.assertIn("Undeclared wave 'ghost-wave': BR-D", md)

    def test_a_grouping_declared_in_more_than_one_list_is_not_duplicated(self):
        # An id repeated across waves/plan_waves/delivery_groupings must
        # not produce two sections; the first declared name wins.
        program_yaml = """\
program: test-program
version_target: 1.0.0
status: x
plan: MASTER-PLAN.md
waves:
  - id: shared
    name: First name wins
plan_waves:
  - id: shared
    name: Second name loses
"""
        items = {"BR-A.yaml": "id: BR-A\ntitle: A\nstatus: not_started\nwave: shared\n"}
        root = _make_root(self.tmp, program_yaml=program_yaml, items=items)
        report = program_mod.build_program_report(root)
        gantt = program_mod.render_gantt(report)
        self.assertIn("section First name wins", gantt)
        self.assertNotIn("Second name loses", gantt)
        self.assertEqual(gantt.count("section "), 1)


class TestGanttTitleSanitization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_colon_in_title_is_sanitized_so_mermaid_does_not_misparse_it(self):
        # Mermaid splits a gantt task line on its FIRST colon (name : meta).
        # A raw colon in the title produces a wrong name and garbage
        # metadata; the fix strips/replaces it before emission.
        items = {
            "BR-A.yaml": (
                'id: BR-A\ntitle: "Ship the thing, then verify: fast"\nstatus: not_started\n'
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        gantt = program_mod.render_gantt(report)
        task_line = [ln for ln in gantt.splitlines() if "Ship the thing" in ln][0]
        self.assertNotIn(
            "Ship the thing, then verify: fast :", task_line,
            "the title's own colon must not survive into the gantt line")
        # Exactly one colon remains: the mandatory name/meta separator.
        self.assertEqual(task_line.count(":"), 1)


class TestNoDataSource(unittest.TestCase):
    """The round-1 Critical finding: an absent `program/work-items`
    directory must read as NO-DATA, never as a clean, empty program."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _root_with_no_items_dir(self):
        # _make_root only creates program/work-items when `items` is
        # truthy, so passing none at all reproduces the exact absent
        # directory the refuter probed.
        return _make_root(self.tmp)

    def test_absent_work_items_directory_is_recorded_as_no_data(self):
        root = self._root_with_no_items_dir()
        self.assertFalse(os.path.isdir(os.path.join(root, "program", "work-items")))
        data = program_mod.load_program(root)
        self.assertEqual(data.items, [])
        self.assertEqual(len(data.source_errors), 1)
        self.assertIn(program_mod.NO_DATA_MARKER, data.source_errors[0]["reason"])
        self.assertEqual(data.source_errors[0]["path"], program_mod.WORK_ITEMS_REL)

    def test_absent_work_items_directory_surfaces_in_report_parse_errors(self):
        root = self._root_with_no_items_dir()
        report = program_mod.build_program_report(root)
        reasons = " ".join(pe["reason"] for pe in report["parseErrors"])
        self.assertIn(program_mod.NO_DATA_MARKER, reasons)
        # It excludes no item (there was none to exclude): the summary must
        # not claim items were excluded when none ever existed.
        self.assertEqual(report["summary"]["excluded_count"], 0)
        self.assertEqual(report["summary"]["total_count"], 0)

    def test_absent_work_items_directory_states_it_in_the_headline(self):
        root = self._root_with_no_items_dir()
        report = program_mod.build_program_report(root)
        md = program_mod.render_status_md(report)
        self.assertIn("NO-DATA", md)
        self.assertIn("work-items", md)

    def test_check_status_md_refuses_when_source_is_no_data_even_if_written(self):
        # This is the exact round-1 Critical failure reproduced end to
        # end: write STATUS.md from the empty ledger, then confirm
        # check_status_md no longer rubber-stamps it as clean.
        root = self._root_with_no_items_dir()
        program_mod.write_status_md(root)
        self.assertFalse(
            program_mod.check_status_md(root),
            "an absent work-items directory must never check out clean, "
            "even when STATUS.md matches a fresh render byte for byte")

    def test_present_but_empty_work_items_directory_is_not_no_data(self):
        # The symmetric guard against over-firing: a directory that EXISTS
        # but simply has no items yet is a legitimate empty program, not a
        # NO-DATA source. Conflating the two would make a brand new,
        # honestly-empty program un-checkable forever.
        root = _make_root(self.tmp)
        os.makedirs(os.path.join(root, "program", "work-items"))
        data = program_mod.load_program(root)
        self.assertEqual(data.items, [])
        self.assertEqual(data.source_errors, [])
        report = program_mod.build_program_report(root)
        self.assertEqual(report["parseErrors"], [])
        program_mod.write_status_md(root)
        self.assertTrue(program_mod.check_status_md(root))


class TestParseErrors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_unknown_status_raises_parse_error_naming_the_file(self):
        items = {
            "BR-A.yaml": "id: BR-A\ntitle: T\nstatus: somehow_almost_done\n",
        }
        root = _make_root(self.tmp, items=items)
        data = program_mod.load_program(root)
        self.assertEqual(data.items, [])
        self.assertEqual(len(data.parse_errors), 1)
        reason = data.parse_errors[0]["reason"]
        self.assertIn("BR-A.yaml", data.parse_errors[0]["path"])
        self.assertIn("somehow_almost_done", reason)

    def test_status_vocabulary_is_case_and_separator_insensitive(self):
        for spelling in ("partially done", "partially_done", "PARTIALLY DONE",
                         "PARTIALLY_DONE"):
            items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: %s\n" % spelling}
            root = _make_root(self.tmp, items=items)
            data = program_mod.load_program(root)
            self.assertEqual(data.parse_errors, [], spelling)
            self.assertEqual(data.items[0]["status"], "partially_done", spelling)

    def test_malformed_item_surfaces_in_parse_errors_while_siblings_still_render(self):
        items = {
            "BR-GOOD.yaml": "id: BR-GOOD\ntitle: A fine item\nstatus: done\n",
            "BR-BAD.yaml": "id: BR-BAD\ntitle: A broken item\nstatus: not_a_real_status\n",
            "BR-ALSO-GOOD.yaml": "id: BR-ALSO-GOOD\ntitle: Another fine item\nstatus: not_started\n",
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        ids = set(i["id"] for i in report["items"])
        self.assertEqual(ids, {"BR-GOOD", "BR-ALSO-GOOD"})
        self.assertEqual(len(report["parseErrors"]), 1)
        self.assertIn("BR-BAD.yaml", report["parseErrors"][0]["path"])
        self.assertEqual(report["summary"]["excluded_count"], 1)

    def test_missing_program_yaml_raises(self):
        root = os.path.join(self.tmp, "no_program")
        os.makedirs(os.path.join(root, "program"))
        with self.assertRaises(program_mod.ProgramParseError):
            program_mod.load_program(root)

    def test_unknown_dependency_is_named_in_parse_errors(self):
        items = {
            "BR-A.yaml": "id: BR-A\ntitle: T\nstatus: not_started\ndepends_on:\n  - BR-GHOST\n",
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        reasons = " ".join(pe["reason"] for pe in report["parseErrors"])
        self.assertIn("BR-GHOST", reasons)
        # An unknown depends_on is reported, but the item that named it DID
        # load and IS included in the aggregate: it must not shrink
        # excluded_count, which counts items that failed to load, not
        # dependency-naming errors on items that loaded fine.
        self.assertEqual(report["summary"]["excluded_count"], 0)
        self.assertEqual(len(report["items"]), 1)

    def test_two_unknown_dependencies_on_one_valid_item_do_not_inflate_excluded_count(self):
        # The exact probe from the round-1 refuter: one valid item naming
        # two ghost dependencies must not read as two excluded items.
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: not_started\n"
                "depends_on:\n  - BR-GHOST-1\n  - BR-GHOST-2\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        self.assertEqual(len(report["items"]), 1)
        self.assertEqual(report["summary"]["excluded_count"], 0)
        self.assertEqual(len(report["parseErrors"]), 2)

    def test_uninterpretable_acceptance_met_entry_is_a_parse_error(self):
        # The exact probe from the round-1 refuter: acceptance=['one',
        # 'two'], acceptance_met=['bogus never listed', 99]. Neither entry
        # is a valid index (0 or 1) nor an exact acceptance string, so
        # letting them through would silently measure the item at a
        # fabricated 0%. The item must be excluded and the file named,
        # exactly like an unknown status.
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: in_progress\n"
                "acceptance:\n  - one\n  - two\n"
                "acceptance_met:\n  - bogus never listed\n  - 99\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        data = program_mod.load_program(root)
        self.assertEqual(data.items, [])
        self.assertEqual(len(data.parse_errors), 1)
        self.assertIn("BR-A.yaml", data.parse_errors[0]["path"])
        self.assertIn("acceptance_met", data.parse_errors[0]["reason"])
        # And the report never derives a percentage from it: the item is
        # simply absent, not measured at a fabricated figure.
        report = program_mod.build_program_report(root)
        self.assertEqual(report["items"], [])
        self.assertIsNone(report["summary"]["aggregate_percent"])

    def test_valid_acceptance_met_indices_and_strings_still_pass(self):
        # Calibration guard for the fix above: valid entries (an in-range
        # index and an exact acceptance string) must NOT be rejected.
        items = {
            "BR-A.yaml": (
                "id: BR-A\ntitle: T\nstatus: in_progress\n"
                "acceptance:\n  - one\n  - two\n"
                "acceptance_met:\n  - 0\n  - two\n"
            ),
        }
        root = _make_root(self.tmp, items=items)
        data = program_mod.load_program(root)
        self.assertEqual(data.parse_errors, [])
        self.assertEqual(len(data.items), 1)


class TestYamlSubset(unittest.TestCase):
    """Direct tests of the parser's edge-case handling."""

    def test_double_quoted_scalar_wrapping_two_lines_folds_to_one_space(self):
        text = (
            'id: BR-A\n'
            'title: T\n'
            'status: done\n'
            'evidence:\n'
            '  - "python3 evals/run_evals.py at commit abc123 (baseline): 521\n'
            '    evals, 0 regressions"\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(
            parsed["evidence"][0],
            "python3 evals/run_evals.py at commit abc123 (baseline): 521 evals, 0 regressions")

    def test_plain_scalar_list_item_wraps_across_lines(self):
        text = (
            'id: BR-A\n'
            'title: T\n'
            'status: done\n'
            'acceptance:\n'
            '  - the four skills each satisfy the one-action\n'
            '    contract on audit\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(
            parsed["acceptance"][0],
            "the four skills each satisfy the one-action contract on audit")

    def test_literal_block_scalar_keeps_newlines(self):
        text = (
            'id: BR-A\n'
            'title: T\n'
            'status: done\n'
            'why: |\n'
            '  line one\n'
            '  line two\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(parsed["why"], "line one\nline two\n")

    def test_folded_block_scalar_joins_with_space(self):
        text = (
            'id: BR-A\n'
            'title: T\n'
            'status: done\n'
            'why: >\n'
            '  line one\n'
            '  line two\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(parsed["why"], "line one line two\n")

    def test_comment_lines_and_trailing_comments_are_stripped(self):
        text = (
            '# a full line comment\n'
            'id: BR-A\n'
            'title: T  # trailing comment\n'
            'status: done\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(parsed["title"], "T")

    def test_hash_inside_quotes_is_not_a_comment(self):
        text = (
            'id: BR-A\n'
            'title: "T # not a comment"\n'
            'status: done\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(parsed["title"], "T # not a comment")

    def test_list_of_mappings(self):
        text = (
            'waves:\n'
            '  - id: wave-0\n'
            '    name: Wave zero\n'
            '  - id: wave-1\n'
            '    name: Wave one\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(len(parsed["waves"]), 2)
        self.assertEqual(parsed["waves"][1]["id"], "wave-1")
        self.assertEqual(parsed["waves"][1]["name"], "Wave one")

    def test_nested_mapping(self):
        text = (
            'token_budget:\n'
            '  planned_total: 1000\n'
            '  hard_ceiling: 2000\n'
        )
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(parsed["token_budget"]["planned_total"], 1000)
        self.assertEqual(parsed["token_budget"]["hard_ceiling"], 2000)

    def test_empty_flow_list(self):
        text = "depends_on: []\n"
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertEqual(parsed["depends_on"], [])

    def test_null_values(self):
        text = "started_at: null\ncompleted_at: ~\nunset:\n"
        parsed = program_mod._yaml_load(text, "<test>")
        self.assertIsNone(parsed["started_at"])
        self.assertIsNone(parsed["completed_at"])
        self.assertIsNone(parsed["unset"])

    def test_non_empty_flow_list_is_refused(self):
        text = "depends_on: [a, b]\n"
        with self.assertRaises(program_mod.ProgramParseError):
            program_mod._yaml_load(text, "<test>")

    def test_bad_indent_is_refused(self):
        text = "id: BR-A\n   bad_indent: true\n"
        with self.assertRaises(program_mod.ProgramParseError):
            program_mod._yaml_load(text, "<test>")


class TestStatusMdMarkers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_regeneration_leaves_prose_outside_markers_byte_identical(self):
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        prose_before = "# Program status page\n\nHand-written prose stays here.\n\n"
        prose_after = "\n\nMore hand-written prose after the block.\n"
        existing = prose_before + program_mod.BEGIN_MARKER + "\nstale content\n" + \
            program_mod.END_MARKER + prose_after
        status_path = os.path.join(root, "program", "STATUS.md")
        _write(status_path, existing)

        program_mod.write_status_md(root)

        with io.open(status_path, encoding="utf-8") as fh:
            new_text = fh.read()
        self.assertTrue(new_text.startswith(prose_before))
        self.assertTrue(new_text.endswith(prose_after))
        self.assertNotIn("stale content", new_text)

    def test_two_consecutive_renders_of_unchanged_input_are_byte_identical(self):
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\npercent_complete: 50\n"}
        root = _make_root(self.tmp, items=items)
        report1 = program_mod.build_program_report(root)
        report2 = program_mod.build_program_report(root)
        md1 = program_mod.render_status_md(report1)
        md2 = program_mod.render_status_md(report2)
        self.assertEqual(md1, md2)
        self.assertEqual(json.dumps(report1, sort_keys=True), json.dumps(report2, sort_keys=True))

    def test_write_then_check_status_md_agree(self):
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        self.assertFalse(program_mod.check_status_md(root))
        program_mod.write_status_md(root)
        self.assertTrue(program_mod.check_status_md(root))

    def test_check_status_md_false_after_ledger_changes(self):
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        program_mod.write_status_md(root)
        self.assertTrue(program_mod.check_status_md(root))
        _write(os.path.join(root, "program", "work-items", "BR-B.yaml"),
              "id: BR-B\ntitle: New item\nstatus: not_started\n")
        self.assertFalse(program_mod.check_status_md(root))


class TestUnreadableAndWrongTypedSource(unittest.TestCase):
    """The round-2 Major: the NO-DATA machinery covered an ABSENT
    work-items directory but not an unreadable one, so `sbe program check`
    raised PermissionError out of the founder-facing gate instead of
    stating a reason. Same source class, reached through a different door."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_unreadable_work_items_directory_is_no_data_not_an_exception(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root: chmod 000 does not deny root, so the "
                          "probe cannot reproduce the condition under test")
        root = _make_root(self.tmp, items={"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"})
        items_dir = os.path.join(root, "program", "work-items")
        os.chmod(items_dir, 0)
        self.addCleanup(os.chmod, items_dir, 0o755)
        # Must not raise: the whole point is that a boundary call gets a
        # failure path rather than escaping to the caller.
        data = program_mod.load_program(root)
        self.assertEqual(data.items, [])
        self.assertEqual(len(data.source_errors), 1)
        self.assertIn(program_mod.NO_DATA_MARKER, data.source_errors[0]["reason"])
        self.assertEqual(data.source_errors[0]["path"], program_mod.WORK_ITEMS_REL)

    def test_check_status_md_refuses_on_an_unreadable_directory(self):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root: chmod 000 does not deny root")
        root = _make_root(self.tmp, items={"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"})
        program_mod.write_status_md(root)
        self.assertTrue(program_mod.check_status_md(root))
        items_dir = os.path.join(root, "program", "work-items")
        os.chmod(items_dir, 0)
        self.addCleanup(os.chmod, items_dir, 0o755)
        # The exact probe the round-2 refuter ran, which raised
        # PermissionError out of the gate before this fix.
        self.assertFalse(program_mod.check_status_md(root))

    def test_work_items_present_as_a_file_says_so_rather_than_absent(self):
        # Refused correctly before, but described inaccurately: "absent" and
        # "present and the wrong kind of thing" are different facts, and a
        # reason that names the wrong one sends a reader to the wrong fix.
        root = _make_root(self.tmp)
        _write(os.path.join(root, "program", "work-items"), "not a directory\n")
        data = program_mod.load_program(root)
        self.assertEqual(len(data.source_errors), 1)
        reason = data.source_errors[0]["reason"]
        self.assertIn(program_mod.NO_DATA_MARKER, reason)
        self.assertIn("not a directory", reason)
        self.assertNotIn("is absent", reason)


class TestStatusMdWriteIsAFixedPoint(unittest.TestCase):
    """The round-2 Major: write_status_md grew STATUS.md by one trailing
    newline on EVERY regeneration, unbounded, and the gate could not see it
    because check_status_md compares only the text between the markers. The
    existing determinism test compared two render return values rather than
    two written files, which is why it missed this."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _read(self, root):
        with io.open(os.path.join(root, "program", "STATUS.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_three_consecutive_writes_produce_byte_identical_files(self):
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\npercent_complete: 100\n"}
        root = _make_root(self.tmp, items=items)
        program_mod.write_status_md(root)
        first = self._read(root)
        program_mod.write_status_md(root)
        second = self._read(root)
        program_mod.write_status_md(root)
        third = self._read(root)
        self.assertEqual(first, second,
                          "regenerating an unchanged ledger must be a fixed point")
        self.assertEqual(second, third)

    def test_markers_out_of_order_are_refused_not_appended_to_forever(self):
        # The cycle-3 verifier's finding: with END before BEGIN, the old code
        # fell into the append branch, and because find() returns the FIRST
        # occurrence of each marker the condition stayed true forever. Five
        # writes measured 839, 1596, 2353, 3110, 3867 bytes. Refusing by name
        # is the only move that neither grows the file nor guesses which pair
        # of markers a corrupted file meant.
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        _write(os.path.join(root, "program", "STATUS.md"),
              "prose\n" + program_mod.END_MARKER + "\nstranded\n" +
              program_mod.BEGIN_MARKER + "\ntail\n")
        with self.assertRaises(program_mod.ProgramParseError) as caught:
            program_mod.write_status_md(root)
        self.assertIn("out of order", str(caught.exception))

    def test_duplicated_markers_converge_rather_than_being_refused(self):
        # Deliberately NOT a refusal. A first version of the disorder guard
        # refused duplicates too and broke the self-healing path below, which
        # legitimately produces a second BEGIN. Duplicates reach a fixed point,
        # so the honest behaviour is to converge, leaving the stale trailing
        # block visible to the reader rather than deleting text nobody asked
        # this module to remove.
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        one = program_mod.BEGIN_MARKER + "\nblock one\n" + program_mod.END_MARKER + "\n"
        _write(os.path.join(root, "program", "STATUS.md"), one + "\nprose\n\n" + one)
        program_mod.write_status_md(root)
        first = self._read(root)
        program_mod.write_status_md(root)
        self.assertEqual(first, self._read(root))

    def test_a_single_missing_marker_still_self_heals_rather_than_refusing(self):
        # The over-fire guard for the two refusals above. A file carrying a
        # BEGIN but no END (a truncated write) must still be recoverable by
        # appending a fresh block, which the verifier measured converging at
        # 808 then 762, 762, 762 bytes. Refusing that case would leave a
        # half-written file permanently un-regenerable.
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        _write(os.path.join(root, "program", "STATUS.md"),
              "prose\n" + program_mod.BEGIN_MARKER + "\ntruncated, no end marker\n")
        program_mod.write_status_md(root)
        first = self._read(root)
        program_mod.write_status_md(root)
        second = self._read(root)
        program_mod.write_status_md(root)
        third = self._read(root)
        # Healing takes TWO writes by design: the first appends a fresh block
        # (leaving the stranded text above it), the second splices from the
        # first BEGIN to the first END and absorbs that stranded text. So the
        # file is a fixed point from the second write onward, which matches the
        # verifier's measurements of 808 then 762, 762, 762 bytes. Asserting
        # first == second would be asserting the wrong thing.
        self.assertNotEqual(first, second,
                             "the second write is the one that absorbs the stranded text")
        self.assertEqual(second, third,
                          "a self-healed file must reach a fixed point")
        self.assertNotIn("truncated, no end marker", third)

    def test_repeated_writes_do_not_grow_the_file_around_hand_written_prose(self):
        items = {"BR-A.yaml": "id: BR-A\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        prose_before = "# Program status\n\nIntro prose.\n\n"
        prose_after = "\n\nClosing prose, with a deliberate blank line above it.\n"
        _write(os.path.join(root, "program", "STATUS.md"),
              prose_before + program_mod.BEGIN_MARKER + "\nstale\n" +
              program_mod.END_MARKER + prose_after)
        program_mod.write_status_md(root)
        first = self._read(root)
        program_mod.write_status_md(root)
        second = self._read(root)
        self.assertEqual(first, second)
        # And the deliberate blank line in the prose below the block is
        # preserved, so the fix normalizes the seam without eating the
        # author's spacing.
        self.assertTrue(first.endswith(prose_after))


class TestBoardHtml(unittest.TestCase):
    """`render_board_html` / `write_program_board`: the board renders ONLY
    the existing program report, refuses to overwrite a file it did not
    itself write, and never carries a network reference."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-program-board-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_an_existing_file_that_cannot_be_read_is_refused_not_crashed_over(self):
        # Both of these used to escape as a raw traceback, which exits 1, the
        # same code this CLI uses for a control that refused. A crash was
        # therefore indistinguishable from a principled refusal to anything
        # reading the exit status. The refusal is correct in both cases: the
        # guard cannot confirm the file is one of ours, so it must not write.
        root = _make_root(self.tmp, items={"LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: done\n"})
        not_utf8 = os.path.join(self.tmp, "notutf8.html")
        with open(not_utf8, "wb") as fh:
            fh.write(b"\xff\xfe\x00\x01binary")
        with self.assertRaises(program_mod.ProgramParseError) as caught:
            program_mod.write_program_board(root, not_utf8)
        self.assertIn("could not be read", str(caught.exception))
        with open(not_utf8, "rb") as fh:
            self.assertEqual(fh.read(), b"\xff\xfe\x00\x01binary",
                             "a refused write must leave the file byte-identical")

    def test_an_unreadable_work_items_source_is_named_not_rendered_as_empty(self):
        # Found by an independent clean-room review, which BLOCKED the release
        # over it. The board filtered parse errors to `kind == "item"` and threw
        # the other two kinds away, so a work-items directory that cannot be
        # read (a `source` error) rendered as a program with nothing in it: `0
        # of 0 items measured`, exit 0, no trace of why. An unreadable program
        # presented as an empty one, on the surface that exists to stop exactly
        # that, while `sbe program status` reported the same fact correctly.
        # Same root guard the two tests above this class already carry, and
        # missing here until 2026-08-25. As uid 0 the chmod below is ignored,
        # the directory reads fine, the board correctly renders the one item it
        # found, and the assertion then fails over a premise that was never
        # built. Nothing reported that, because this suite is wired into no
        # workflow. A skip that names its reason is the honest answer; passing
        # would be worse, since it would claim to have tested an unreadable
        # source while reading it perfectly.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("running as root: chmod 000 does not deny root, so an "
                          "unreadable work-items source cannot be constructed "
                          "and this scenario is unavailable here")
        root = _make_root(self.tmp, items={"LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: done\n"})
        os.chmod(os.path.join(root, "program", "work-items"), 0o000)
        self.addCleanup(os.chmod, os.path.join(root, "program", "work-items"), 0o755)
        report = program_mod.build_program_report(root)
        html = program_mod.render_board_html(report)
        self.assertIn(program_mod.NO_DATA_MARKER, html)
        self.assertIn("work-items", html,
                      "the board must NAME the source it could not read, never "
                      "render its absence as an empty program")

    def test_a_dependency_parse_error_is_named_on_the_board(self):
        # The second kind the filter discarded. A work item depending on an id
        # that does not exist is a `dependency` error: status names it, and the
        # board used to say nothing at all.
        items = {
            "LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: in_progress\n"
                           "depends_on:\n  - GHOST-99\n",
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        html = program_mod.render_board_html(report)
        self.assertIn("GHOST-99", html,
                      "the board must name a dependency it cannot resolve")

    def test_missing_field_renders_no_data_not_blank(self):
        # An item with no acceptance criteria and no evidence recorded must
        # render the literal word NO-DATA in both spots, never a blank
        # (which would read as zero without saying so) and never a guess.
        items = {"LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: not_started\n"}
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        html = program_mod.render_board_html(report)
        self.assertIn(program_mod.NO_DATA_MARKER, html)
        # Specifically: no blank <ul> standing in for the missing data.
        self.assertNotIn('<ul class="board-subitems"></ul>', html)
        self.assertNotIn('<ul class="board-evidence"></ul>', html)

    def test_acceptance_met_absent_is_no_data_not_zero_met(self):
        # acceptance recorded, but acceptance_met never recorded: this must
        # read as "cannot say", not as "recorded and none are met" (which
        # would render every box unticked, a different and false claim).
        items = {
            "LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: in_progress\n"
                           "acceptance:\n  - one\n  - two\n",
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        html = program_mod.render_board_html(report)
        self.assertIn(program_mod.NO_DATA_MARKER, html)
        # The CSS class is always defined in the stylesheet; what must be
        # absent is an actual unticked BOX rendered for a criterion, which
        # would claim "recorded as not met" rather than "not recorded".
        self.assertNotIn('class="board-untick"', html)

    def test_refuses_to_overwrite_a_file_it_did_not_write(self):
        items = {"LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        out_path = os.path.join(self.tmp, "foreign.html")
        _write(out_path, "<html>a file this tool never wrote</html>")
        with self.assertRaises(program_mod.ProgramParseError) as ctx:
            program_mod.write_program_board(root, out_path)
        self.assertIn(program_mod.BOARD_MARKER, str(ctx.exception))
        with io.open(out_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "<html>a file this tool never wrote</html>")

    def test_a_previously_written_board_is_regenerated_not_refused(self):
        items = {"LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: done\n"}
        root = _make_root(self.tmp, items=items)
        out_path = os.path.join(self.tmp, "board.html")
        program_mod.write_program_board(root, out_path)
        # A second call at the same path, generated by this same tool,
        # regenerates rather than refusing.
        html = program_mod.write_program_board(root, out_path)
        self.assertIn(program_mod.BOARD_MARKER, html)

    def test_written_board_carries_no_network_reference(self):
        items = {"LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: in_progress\n"
                                "evidence:\n  - some recorded evidence\n"}
        root = _make_root(self.tmp, items=items)
        out_path = os.path.join(self.tmp, "board.html")
        program_mod.write_program_board(root, out_path)
        with io.open(out_path, encoding="utf-8") as fh:
            html = fh.read()
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_tick_marks_a_completed_sub_item(self):
        # A false pin lived here before: asserting only that the strings
        # "board-tick" and "board-untick" appear anywhere in the file never
        # checks what mark is actually inside either span. Swapping the
        # tick glyph for the empty-box glyph across the whole render (so a
        # met criterion and an unmet one look identical) left both class
        # names present and the assertions green. The fix compares the two
        # criteria's own rendered fragments against each other: a met
        # criterion and an unmet one must render as different marks, and
        # this test must go red if a change makes them the same.
        items = {
            "LOOP-X.yaml": "id: LOOP-X\ntitle: T\nstatus: in_progress\n"
                           "acceptance:\n  - one\n  - two\n"
                           "acceptance_met:\n  - 0\n",
        }
        root = _make_root(self.tmp, items=items)
        report = program_mod.build_program_report(root)
        html = program_mod.render_board_html(report)
        self.assertIn("board-tick", html)
        self.assertIn("board-untick", html)

        met_li = re.search(r"<li>.*?\bone\b.*?</li>", html)
        unmet_li = re.search(r"<li>.*?\btwo\b.*?</li>", html)
        self.assertIsNotNone(met_li, "no <li> found for the met criterion 'one'")
        self.assertIsNotNone(unmet_li, "no <li> found for the unmet criterion 'two'")

        met_mark = re.search(r'<span class="board-tick"[^>]*>(.*?)</span>', met_li.group(0))
        unmet_mark = re.search(r'<span class="board-untick"[^>]*>(.*?)</span>', unmet_li.group(0))
        self.assertIsNotNone(met_mark, "met criterion is not wrapped in board-tick")
        self.assertIsNotNone(unmet_mark, "unmet criterion is not wrapped in board-untick")

        # The load-bearing assertion: the glyph inside a met mark and the
        # glyph inside an unmet mark must differ. If both were replaced
        # with the same glyph (the exact regression under test), this
        # fails even though "board-tick" and "board-untick" both still
        # appear in the file.
        self.assertNotEqual(
            met_mark.group(1), unmet_mark.group(1),
            "a met and an unmet criterion render the identical mark %r; a completed "
            "acceptance criterion must be visually distinguishable from one that is "
            "not" % (met_mark.group(1),))

    def test_defines_dark_mode_colors(self):
        report = program_mod.build_program_report(_make_root(self.tmp))
        html = program_mod.render_board_html(report)
        self.assertIn("prefers-color-scheme: dark", html)
        self.assertIn(":root", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
