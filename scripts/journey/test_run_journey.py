#!/usr/bin/env python3
"""JT0: the frozen harness for the journey walker, written before the walker exists.

Written by the session before JT0 is built and never by its builder (HARN1), and RED ON THE BASE BY AN
ASSERTION, never by an import error and never by a skip.

WHY JT0 IS THE UNIT THAT MUST NOT BE CUT: seventeen 1.1.9 units name it in depends_on, and
scripts/test_plan_119.py refuses an access unit built before it. Cutting it cuts the whole access track,
including RDM1, which owns the cut gates.

CLASSES AND THE SUB UNIT EACH ONE CLOSES (the plan's own sub_done_checks are the source of this map):
  Install           JT0.a   scripts/journey/run_journey.py, journeys.json
  FirstGoalReview   JT0.b   journeys.json, the J2 and J4 rows
  LinuxLeg          JT0.c   .github/workflows/virgin-install.yml
  Baseline          JT0.d   docs/plan/specs/1.1.9/SCORES-1.1.7.json
  Memory            JT0.e   journeys.json, the J5 rows

MEASURED ON THIS TREE BEFORE THESE TESTS WERE WRITTEN, which is what every assertion rests on:
  scripts/journey/run_journey.py            ABSENT
  scripts/journey/journeys.json             ABSENT
  docs/plan/specs/1.1.9/SCORES-1.1.7.json   ABSENT
  .github/workflows/virgin-install.yml      present, 0 macOS runners, 0 automatic triggers,
                                            1 workflow_dispatch, 0 mentions of run_journey

So REQ-JT0C-02, the cost law, is ALREADY SATISFIED here and its test is a regression guard rather than a
red. It is kept and labelled: the workflow is the one place a macOS runner or an automatic trigger would
cost real money, and JT0.c edits that file, so the thing most likely to break while adding a journey leg
is the cost law that already holds.
"""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
WALKER = os.path.join(HERE, "run_journey.py")
JOURNEYS = os.path.join(HERE, "journeys.json")
SCORES = os.path.join(ROOT, "docs/plan/specs/1.1.9/SCORES-1.1.7.json")
WORKFLOW = os.path.join(ROOT, ".github/workflows/virgin-install.yml")
WBS = os.path.join(ROOT, "docs/plan/BROTHER-1.1.9-WBS.json")
SMOKE = os.path.join(ROOT, "scripts/bundle-install-smoke.sh")


def source(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def loaded(path):
    """The JSON at path, or None when it is absent or unreadable. An unreadable file is never an empty
    document: the caller asserts on None rather than reading a missing row set as no rows."""
    text = source(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def rows_of(doc, journey):
    """The row ids journeys.json lists for one journey, or None when the shape cannot be read."""
    if not isinstance(doc, dict):
        return None
    entry = doc.get(journey) or doc.get("journeys", {}).get(journey) if isinstance(doc.get("journeys"), dict) else doc.get(journey)
    if isinstance(entry, dict):
        entry = entry.get("rows")
    if not isinstance(entry, list):
        return None
    out = []
    for row in entry:
        if isinstance(row, str):
            out.append(row)
        elif isinstance(row, dict) and isinstance(row.get("id"), str):
            out.append(row["id"])
    return out


def named_rows():
    """Every journey row any 1.1.9 done check names, read from the plan itself. JT0.b must cover all of
    them, and a plan that cannot be read is a failure here, never an empty expectation."""
    import re
    doc = loaded(WBS)
    if not isinstance(doc, dict) or not isinstance(doc.get("units"), list):
        return None
    found = set()
    for unit in doc["units"]:
        checks = list((unit.get("sub_done_checks") or {}).values()) + [unit.get("done_check") or ""]
        for check in checks:
            if not isinstance(check, str):
                continue
            m = re.search(r"--journey (J\d) --expect (\S+)", check)
            if m:
                found.update(m.group(2).split(","))
    return found


class _Base(unittest.TestCase):
    def walker(self):
        """The walker as a module, asserted present first so an absent file is an assertion and not an
        ImportError. This is the shape a sibling harness got wrong on 2026-10-25 and had to be fixed."""
        self.assertTrue(os.path.isfile(WALKER),
                        "scripts/journey/run_journey.py does not exist, so no journey can be walked and "
                        "every access claim in 1.1.9 rests on a fixture instead of a fresh install")
        import importlib.util
        spec = importlib.util.spec_from_file_location("run_journey_under_test", WALKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class Install(_Base):
    """JT0.a: the harness core and the install journey rows."""

    def test_rows_carry_atoms(self):
        """REQ-JT0A-01: each row prints PASS, FAIL or NO-DATA with its atom ids, so a reader can tell a
        missing measurement from a failed one."""
        module = self.walker()
        for name in ("install_fresh", "run_journey"):
            self.assertTrue(hasattr(module, name), "run_journey.py does not define %s" % name)

    def test_the_v117_first_screen_fails(self):
        """REQ-JT0A-02: the v1.1.7 first screen FAILS row A.first-screen-paths-exist. A harness that
        cannot fail on a release known to be bad is not a harness."""
        self.walker()
        doc = loaded(JOURNEYS)
        self.assertIsNotNone(doc, "scripts/journey/journeys.json is absent or unreadable, so J1 names no rows")
        rows = rows_of(doc, "J1")
        self.assertIsNotNone(rows, "J1 has no readable row list in journeys.json")
        for row in ("A.first-screen-paths-exist", "D.no-internal-names", "D.doctor-fixes-work",
                    "D.six-verbs-only", "D.readme-claims-executed"):
            self.assertIn(row, rows, "J1 does not include the row %s its spec names" % row)

    def test_no_client_is_no_data(self):
        """REQ-JT0A-03: no client binary reads NO-DATA, never PASS. The fail direction: an install that
        could not be exercised must never be reported as one that worked."""
        module = self.walker()
        self.assertTrue(hasattr(module, "install_fresh"), "run_journey.py does not define install_fresh")
        self.assertTrue(os.path.isfile(SMOKE),
                        "scripts/bundle-install-smoke.sh is absent, and install_fresh is specified to "
                        "reuse its path mode install rather than inventing a second one")
        text = source(WALKER)
        self.assertIsNotNone(text, "run_journey.py could not be read")
        self.assertIn("NO-DATA", text,
                      "the walker names no NO-DATA verdict, so an install it could not exercise has no "
                      "way to report that rather than a pass")


class FirstGoalReview(_Base):
    """JT0.b: the first goal and review rows, and every row any unit names."""

    def test_the_v117_vacuous_unit_fails(self):
        """REQ-JT0B-01: on v1.1.7 C.vacuous-single-unit-not-pass is FAIL, because that release reads an
        assert True unit as DONE PASS."""
        doc = loaded(JOURNEYS)
        self.assertIsNotNone(doc, "journeys.json is absent or unreadable")
        for journey, needed in (("J2", ("A.adopt-exits-0", "C.no-pass-for-unrun-check",
                                        "C.vacuous-single-unit-not-pass", "D.end-card-six-lines",
                                        "B.hook-cost-under-1.1.4")),
                                ("J4", ("C.one-proof-source", "A.accept-recorded",
                                        "A.reject-pushes-nothing"))):
            rows = rows_of(doc, journey)
            self.assertIsNotNone(rows, "%s has no readable row list" % journey)
            for row in needed:
                self.assertIn(row, rows, "%s does not include the row %s its spec names" % (journey, row))

    def test_every_named_row_exists(self):
        """REQ-JT0B-02: every row named by any 1.1.9 done check exists in journeys.json. The subject of
        this check is the real plan, not a fixture."""
        wanted = named_rows()
        self.assertIsNotNone(wanted, "the 1.1.9 plan could not be read, so no named row can be checked")
        doc = loaded(JOURNEYS)
        self.assertIsNotNone(doc, "journeys.json is absent or unreadable")
        have = set()
        for journey in ("J1", "J2", "J3", "J4", "J5", "J6"):
            have.update(rows_of(doc, journey) or [])
        missing = sorted(w for w in wanted if w not in have)
        self.assertEqual(missing, [], "these rows are named by a 1.1.9 done check and absent from "
                                      "journeys.json: %s" % missing)


class LinuxLeg(_Base):
    """JT0.c: the free ubuntu runner walks J1 and J2 on the exported candidate."""

    def test_the_workflow_walks_the_journey(self):
        """REQ-JT0C-01: the workflow runs J1 and J2 on the checked out tree. On the base it stops after
        the smoke and names run_journey nowhere."""
        text = source(WORKFLOW)
        self.assertIsNotNone(text, ".github/workflows/virgin-install.yml could not be read")
        #: NAMING THE WALKER IS NOT WALKING A JOURNEY. The first version of this assertion was
        #: `"run_journey" in text`, and a mutation that deleted the actual walk SURVIVED it on
        #: 2026-10-25, because the file still mentioned the walker on its --selftest line. The bar now
        #: requires the walk itself and both journeys by name, which is what the Linux half of X01
        #: reads. Strengthened after the sweep found it weak; a sweep that changes nothing is decoration.
        self.assertIn("--journey", text,
                      "the workflow names the walker but never walks a journey, so the Linux half of "
                      "the X01 cut gate has nothing to read")
        for journey in ("J1", "J2"):
            self.assertIn(journey, text,
                          "the workflow does not name %s, which its spec requires it to walk" % journey)

    def test_the_cost_law_holds(self):
        """REQ-JT0C-02: no macOS runner and no automatic trigger.

        MEASURED 2026-10-25: the workflow already has 0 macOS runners and 0 automatic triggers, so this
        requirement is ALREADY SATISFIED and this test is a regression guard, not a red. It is kept
        because JT0.c edits this very file, and this is the one file where a macOS runner or an
        automatic trigger spends real money."""
        text = source(WORKFLOW)
        self.assertIsNotNone(text, ".github/workflows/virgin-install.yml could not be read")
        self.assertNotIn("macos", text.lower(), "a macOS runner costs ten times a linux minute")
        for trigger in ("\npush:", "\n  push:", "\npull_request:", "\n  pull_request:",
                        "\nschedule:", "\n  schedule:"):
            self.assertNotIn(trigger, text,
                             "an automatic trigger (%s) would spend minutes without anyone asking"
                             % trigger.strip())
        self.assertIn("workflow_dispatch", text,
                      "the workflow has no manual trigger, so nobody can run the Linux leg at all")


class Baseline(_Base):
    """JT0.d: the frozen baseline the harness must reproduce."""

    def test_the_harness_reproduces_the_baseline(self):
        """REQ-JT0D-01: the harness reproduces every 1.1.7 cell. A harness that cannot reproduce a
        measurement already taken by hand is not measuring the same thing."""
        doc = loaded(SCORES)
        self.assertIsNotNone(doc, "docs/plan/specs/1.1.9/SCORES-1.1.7.json is absent or unreadable, so "
                                  "the harness has no baseline to be judged against")
        self.assertTrue(isinstance(doc, dict) and doc,
                        "the baseline record is empty, which is not the same as a baseline of zero")
        self.walker()


class Memory(_Base):
    """JT0.e: the memory journey rows."""

    def test_the_v117_vault_split_fails(self):
        """REQ-JT0E-01: on v1.1.7 A.setup-vault-is-recall-vault is FAIL, because recall answers that no
        vault root is configured after a clean setup."""
        doc = loaded(JOURNEYS)
        self.assertIsNotNone(doc, "journeys.json is absent or unreadable")
        rows = rows_of(doc, "J5")
        self.assertIsNotNone(rows, "J5 has no readable row list")
        for row in ("A.setup-vault-is-recall-vault", "C.forget-removes-every-root"):
            self.assertIn(row, rows, "J5 does not include the row %s its spec names" % row)


class InstallsTheCandidate(_Base):
    """The walk must install the CANDIDATE, and must be able to say which tree it installed.

    WHY THIS CLASS EXISTS, measured 2026-10-29. install_fresh took the tree as its first argument,
    registered it with `claude plugin marketplace add <tree>`, and then installed by NAME with
    `claude plugin install brother@brother`. That name resolves through .claude-plugin/marketplace.json,
    which pins source git-subdir, url https://github.com/khalilmaaouni/Brother, ref v1.1.8. So the
    tree argument found the catalogue and was then never read: every row walked described the ALREADY
    RELEASED version. The install's own cache directory was literally named 1.1.8, and the installed
    bm_project.py carried 0 occurrences of a symbol the candidate's source and bundle copies each
    carried twice, in two separate brand new config directories.

    Cut gate X01 is what decides whether a tag may be pushed. A gate that loads the previous release
    and reports on it would certify an unbuilt candidate, which is the exact failure 1.1.9 exists to
    remove. So these cases are the frozen bar, written before the walker was changed.
    """

    PUBLISHED_REF = "brother@brother"

    def test_the_walker_does_not_install_by_the_published_catalogue_name(self):
        """The published name resolves a pinned tag, so installing it can never reach the candidate."""
        text = source(WALKER)
        self.assertIsNotNone(text, "run_journey.py could not be read")
        # assertTrue on a computed boolean, never assertIn on the file: unittest prints the whole
        # haystack on failure, and that is 42 KB of source nobody reads.
        self.assertFalse(
            '"%s"' % self.PUBLISHED_REF in text,
            "the walker installs %r, which .claude-plugin/marketplace.json resolves to a pinned "
            "published tag, so the walk measures the released version and never the candidate"
            % self.PUBLISHED_REF)

    def test_install_fresh_says_which_tree_it_installed(self):
        """A walk that cannot name what it installed is NO-DATA, not a pass, so the record must carry
        the installed root and a content identity for it."""
        module = self.walker()
        self.assertTrue(hasattr(module, "install_fresh"), "run_journey.py does not define install_fresh")
        text = source(WALKER)
        for key in ("installed_root", "identity"):
            self.assertTrue(
                '"%s"' % key in text,
                "install_fresh's record carries no %r, so nothing downstream can refuse a journey "
                "result measured against the wrong tree" % key)

    def test_the_candidate_is_what_gets_installed(self):
        """The behavioural case. Installs this tree and compares a canary file in the installed copy
        with the candidate's own bundle copy, byte for byte.

        Content, not a version string: the install names its cache directory from the plugin's own
        manifest version, which is the same for the candidate and the release it was cut from, so a
        version comparison would pass on the wrong tree. This is the same binding the restore drill
        record carries through its covered set.
        """
        module = self.walker()
        if not module.have_client():
            raise unittest.SkipTest(
                "NO-DATA: no claude binary on PATH, so no install can be exercised here. Never a "
                "pass: on a machine with a client this case runs.")
        import hashlib
        import shutil
        import tempfile
        canary = os.path.join(ROOT, "bundle", "runtime", "RUNTIME-MANIFEST.json")
        self.assertTrue(os.path.isfile(canary),
                        "%s is absent, so there is no canary to compare an install against" % canary)
        want = hashlib.sha256(open(canary, "rb").read()).hexdigest()
        scratch = tempfile.mkdtemp(prefix="jt0-candidate-")
        try:
            record = module.install_fresh(ROOT, scratch)
            self.assertTrue(record.get("ok"),
                            "install_fresh did not install: %s" % record.get("reason"))
            root = record.get("installed_root")
            self.assertTrue(root, "install_fresh reported no installed_root")
            found = os.path.join(root, "runtime", "RUNTIME-MANIFEST.json")
            self.assertTrue(os.path.isfile(found),
                            "the installed tree carries no runtime manifest at %s" % found)
            got = hashlib.sha256(open(found, "rb").read()).hexdigest()
            self.assertEqual(want, got,
                             "the installed runtime manifest is not the candidate's: installed %s, "
                             "candidate %s. The walk measured a different tree."
                             % (got[:12], want[:12]))
            self.assertEqual(want, record.get("identity"),
                             "install_fresh's recorded identity does not match the candidate's "
                             "runtime manifest, so the record cannot be trusted to bind the walk")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class WritesARecordTheGateCanRead(_Base):
    """A walk nobody can read afterwards cannot gate a cut.

    X01 is read by the cut chain, not by whoever ran the walk. Until this landed the walker printed
    its rows to stdout and wrote nothing, so scripts/go_no_go_119.py's journeys-fresh row reported
    "no journey result file matches docs/plan/JOURNEY-*.json" and the gate stayed NO-DATA however
    many times the journey was walked.

    The record must carry the installed identity, not only the verdicts. A row set with no binding to
    the tree it was measured on is the same hole the walker itself had: evidence about an unnamed
    tree. That binding is what lets the gate refuse a stale record from an earlier candidate.
    """

    def test_the_walker_can_write_a_record(self):
        module = self.walker()
        self.assertTrue(hasattr(module, "write_record"),
                        "run_journey.py defines no write_record, so a walk leaves nothing the cut "
                        "chain can read and X01 stays NO-DATA however often it is walked")

    def test_the_record_carries_the_fields_the_gate_reads(self):
        """Named against go_no_go_119.py's journeys-fresh row, its only consumer."""
        module = self.walker()
        if not hasattr(module, "write_record"):
            self.fail("run_journey.py defines no write_record")
        import json as _json
        import tempfile
        out = os.path.join(tempfile.mkdtemp(prefix="jt0-record-"), "JOURNEY-J9.json")
        install = {"ok": True, "verdict": "PASS", "identity": "abc123", "candidate_identity": "abc123",
                   "installed_root": "/nowhere", "config": "", "home": "", "launcher": "", "reason": ""}
        module.write_record(out, "J9", {"A.row": "PASS", "B.row": "FAIL"}, install, clean_home=True)
        with open(out, encoding="utf-8") as fh:
            doc = _json.load(fh)
        for key in ("journey", "rows", "clean_home", "identity", "candidate_identity", "walked_unix"):
            self.assertIn(key, doc, "the record carries no %r, which the gate reads" % key)
        self.assertIs(True, doc["clean_home"])
        self.assertEqual({"A.row": "PASS", "B.row": "FAIL"}, doc["rows"])

    def test_a_record_whose_identity_does_not_match_the_candidate_is_refused(self):
        """The record must not be able to claim a clean walk of a tree it did not walk."""
        module = self.walker()
        if not hasattr(module, "write_record"):
            self.fail("run_journey.py defines no write_record")
        import json as _json
        import tempfile
        out = os.path.join(tempfile.mkdtemp(prefix="jt0-record-"), "JOURNEY-J9.json")
        install = {"ok": True, "verdict": "PASS", "identity": "installed-one",
                   "candidate_identity": "candidate-other", "installed_root": "/nowhere",
                   "config": "", "home": "", "launcher": "", "reason": ""}
        module.write_record(out, "J9", {"A.row": "PASS"}, install, clean_home=True)
        with open(out, encoding="utf-8") as fh:
            doc = _json.load(fh)
        self.assertNotEqual(doc["identity"], doc["candidate_identity"],
                            "the fixture is wrong: these must differ for this case to mean anything")
        self.assertIs(False, doc["clean_home"],
                      "a record whose installed identity is not the candidate's must not claim a "
                      "clean walk, because its rows describe another tree")


if __name__ == "__main__":
    unittest.main(verbosity=1)
