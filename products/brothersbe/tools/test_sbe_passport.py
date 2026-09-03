#!/usr/bin/env python3
"""Tests for the change passport consumer half (tools/sbe_passport.py).

Run: python3 tools/test_sbe_passport.py

The bar, inherited from tools/test_sbe_onepager.py beside it: a suite that only
proves the happy path proves nothing here. The tests that matter are the ones
that hollow the evidence and assert the passport reports LESS rather than
reporting the same thing over nothing.

Three properties are load bearing, and each has a test whose guard can be removed
to watch it go red (calibration by reinjection, the practice this repository
adopted after a regression fixture passed with its guard deleted):

  1. Field 4 is never empty. Not on a full store, not on an empty root, not on a
     store whose every receipt was hollowed out.
  2. A field nothing carries reads NO-DATA and names the side that owes it. It
     never reads as clean and it never blocks: exit is 0 either way.
  3. The producer deposit is READ, not required. Absent, corrupt and present are
     three different reported states, and only one of them fills a field.

The fixtures are built in a temporary directory on purpose. An earlier smoke test
of this tool ran against a real dossier in this repository and a concurrent
session deleted that dossier's evidence store underneath it mid-run, which is
exactly how a suite ends up measuring a tree somebody else is editing.
"""
import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

# F10 (seam review, cross-family, 2026-08-20): the old loader used
# importlib.util.spec_from_file_location + exec_module, which for a plain
# .py path resolves to SourceFileLoader, and SourceFileLoader's own cache
# invalidation is by mtime and size against __pycache__. A mutation harness
# that rewrites sbe_passport.py within the same wall-clock second and keeps
# the byte count unchanged (the common shape of a one-token mutation) is
# invisible to that check: the stale .pyc is reused and the suite silently
# tests yesterday's source. This never touches importlib's file-based
# machinery at all: it reads the source text and compiles it fresh, every
# time, so a mutation harness always reads the bytes actually on disk.
sys.dont_write_bytecode = True
importlib.invalidate_caches()


def load_module_from_source(name, path):
    """Load `path` as module `name` from its SOURCE TEXT, bypassing any
    compiled-bytecode cache entirely. See the F10 note above for why the
    default loader is unsafe for a mutation-testing harness."""
    with io.open(path, encoding="utf-8") as fh:
        source = fh.read()
    module = types.ModuleType(name)
    module.__file__ = path
    sys.modules[name] = module
    exec(compile(source, path, "exec"), module.__dict__)
    return module


# R2-C: load_module_from_source above only ever compiled sbe_passport.py
# itself from source. sbe_passport.py's own top-level `from sbe_onepager
# import ...` and `from sbe_checks import answered` are plain Python import
# statements, so once sbe_passport.py started exec'ing, THOSE two modules
# were resolved through the normal import machinery, which for a plain .py
# path is SourceFileLoader keyed by mtime+size against __pycache__, exactly
# the stale-cache hole F10 fixed for sbe_passport.py alone. A same-second,
# same-size mutation of sbe_onepager.py or sbe_checks.py was invisible to
# this suite. Fixed by loading sbe_checks and sbe_onepager from source FIRST,
# in dependency order (neither imports the other; sbe_passport imports both),
# and registering each in sys.modules under its real name before
# sbe_passport.py execs: its `from sbe_onepager import ...` then finds the
# already-loaded, source-true module in sys.modules and never touches the
# file-based loader or __pycache__ at all.
load_module_from_source("sbe_checks", os.path.join(HERE, "sbe_checks.py"))
load_module_from_source("sbe_onepager", os.path.join(HERE, "sbe_onepager.py"))
pp = load_module_from_source("sbe_passport", os.path.join(HERE, "sbe_passport.py"))

#: The Change Passport v1 canonical fixture, copied byte for byte from the
#: sibling BrotherMode repository (schema/fixtures/change-passport.v1.canonical.json
#: on its main at 1cef65f). The hash below is the cross-repo pin: it is checked
#: against the bytes actually on disk here, not against the sibling repo, because
#: this suite proves what BrotherSBE can consume from a deposit, not that the
#: sibling still has the file.
CANONICAL_FIXTURE = os.path.join(HERE, "fixtures", "change-passport.v1.canonical.json")
CANONICAL_FIXTURE_SHA256 = (
    "e6d68b7622379d5c5c33beb567e1f1901850409cb3daaa9a45cb2825bba377e8")


def write_json(path, obj):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj))


def write_text(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def copy_fixture_into(dest_path):
    """Byte-for-byte copy of CANONICAL_FIXTURE to dest_path, making the parent
    directory first (same shape as write_json/write_text above). Used to put
    the fixture at the exact deposit path sbe_passport.py reads
    (.sbe/passport.json under a root), so consumption goes through the real
    file-based entry point rather than a dict constructed in this test."""
    d = os.path.dirname(dest_path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    shutil.copyfile(CANONICAL_FIXTURE, dest_path)


def minimal_deposit(**overrides):
    """A tiny change-passport/v1 deposit, valid enough to be CONSUMED
    (the `schema` marker present and correct) for a test that exercises one
    field's content rather than the whole producer contract. Callers pass
    whichever top-level keys they care about; nothing else is required by
    THIS reader (the schema's own required-key list is BrotherMode's
    contract to honour when it produces one, not this consumer's to
    enforce, per the module docstring: unknown or missing keys degrade
    honestly rather than crash)."""
    base = {"schema": "change-passport/v1"}
    base.update(overrides)
    return base


class PassportCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sbe-passport-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def sbe(self, *rel):
        return os.path.join(self.root, ".sbe", *rel)

    def task(self, task_id="T01", owned=None):
        return {"id": task_id, "ownedPaths": owned or ["src/thing.py"],
                "evidenceId": "%s-receipt" % task_id}

    def registry(self, tasks):
        return {"tasks": tasks}

    def receipt(self, exit_code=0, ci_run_id=None, head="abc123"):
        data = {"argv": ["pytest", "tools/test_thing.py"], "exitCode": exit_code,
                "headCommit": head}
        if ci_run_id is not None:
            data["ciRunId"] = ci_run_id
        return data

    def full_store(self, ci_run_id=None):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(ci_run_id=ci_run_id))

    def run_cli(self, *extra):
        # G2: every subprocess this suite spawns must carry
        # PYTHONDONTWRITEBYTECODE=1. sys.dont_write_bytecode (set at the top
        # of this file) governs only THIS process; the child process below
        # does not inherit it and, left alone, writes tools/__pycache__
        # entries keyed by mtime and size. A mutation harness that rewrites
        # sbe_onepager.py or sbe_passport.py within the same wall-clock
        # second, keeping the byte count unchanged, is then invisible to a
        # SECOND run_cli call in the same suite run: the stale .pyc is
        # reused and the subprocess silently answers from yesterday's
        # source. Reproduced live: the mutation stayed invisible on a clean
        # tree until this env var was added.
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "sbe_passport.py"), "--root", self.root]
            + list(extra), capture_output=True, text=True, env=env)

    def fields(self):
        found, _note, _disagreement = pp.build_fields(self.root)
        return found

    def field_lines(self, number):
        return self.fields()[number][1]

    def field_state(self, number):
        return self.fields()[number][0]


class FieldFourIsNeverEmpty(PassportCase):
    """Property 1. The guard is the `elif not gaps` and `if not tasks` branches in
    build_fields; delete either and the matching case here goes red."""

    def test_empty_root_still_says_what_was_not_established(self):
        lines = self.field_lines(4)
        self.assertTrue(lines, "field 4 was empty on a root with no store at all")
        self.assertEqual(self.field_state(4), pp.CARRIED)
        self.assertIn("nothing at all was established here", " ".join(lines))

    def test_a_wholly_green_store_still_says_what_it_could_not_see(self):
        self.full_store()
        lines = " ".join(self.field_lines(4))
        self.assertIn("no gap was found", lines)
        self.assertIn("outside what this line can speak about", lines,
                      "a green store reported no limit on its own reach, which is "
                      "the passport claiming nothing is unexamined")

    def test_a_hollowed_receipt_moves_the_task_from_field_three_to_field_four(self):
        """The calibration that matters: same task, evidence hollowed three ways,
        and the task must MOVE rather than vanish from both fields."""
        for label, hollow in (
                ("empty file", lambda p: write_text(p, "")),
                ("no verdict", lambda p: write_json(p, {"argv": ["pytest"]})),
                ("NO-DATA verdict", lambda p: write_json(
                    p, {"argv": ["pytest"], "verdict": "NO-DATA"})),
                ("deleted", os.remove)):
            with self.subTest(hollow=label):
                self.full_store()
                path = self.sbe("evidence", "T01-receipt.json")
                hollow(path)
                ran = " ".join(self.field_lines(3))
                gaps = " ".join(self.field_lines(4))
                self.assertNotIn("T01:", ran,
                                 "%s: the task still reads as run" % label)
                self.assertIn("T01", gaps,
                              "%s: the task vanished from both fields instead of "
                              "moving to what was NOT established" % label)


class AbsenceIsNamedAndNeverBlocks(PassportCase):
    """Property 2."""

    def test_the_two_producer_fields_are_no_data_and_name_their_owner(self):
        self.full_store()
        for number in (2, 5):
            with self.subTest(field=number):
                self.assertEqual(self.field_state(number), pp.NO_DATA)
        text = self.run_cli().stdout
        self.assertIn("owed by execution provenance, through the passport", text)

    def test_a_field_that_is_no_data_is_also_listed_in_field_four(self):
        """The guard against burying an absence in a field nobody reads: fields 2
        and 5 being empty must ALSO appear in field 4."""
        self.full_store()
        gaps = " ".join(self.field_lines(4))
        self.assertIn("field 2, who did it, is not established", gaps)
        self.assertIn("field 5, where it came from, is not established", gaps)

    def test_exit_is_zero_on_a_full_store_and_on_an_empty_one(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.full_store()
        self.assertEqual(self.run_cli().returncode, 0)

    def test_json_and_text_agree_about_what_was_found(self):
        self.full_store()
        payload = json.loads(self.run_cli("--json").stdout)
        self.assertEqual(payload["total"], 5)
        by_number = dict((f["number"], f["state"]) for f in payload["fields"])
        for number, _name, _key, _owner in pp.FIELDS:
            self.assertEqual(by_number[number], self.field_state(number),
                             "field %d disagrees between the JSON and the "
                             "assembled view" % number)


class OriginIsReadFromTheReceipt(PassportCase):
    """Field 3's second half: whether a build system or a laptop produced the
    evidence. Three receipt shapes, three different sentences, and the one that
    must never appear is a confident laptop claim over a receipt that predates the
    field."""

    def test_a_ci_run_id_reads_as_a_build_system(self):
        self.full_store(ci_run_id="run-4711")
        self.assertIn("produced by a build system, run run-4711",
                      " ".join(self.field_lines(3)))

    def test_an_empty_ci_run_id_reads_as_off_a_build_system(self):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"), self.receipt(ci_run_id=""))
        self.assertIn("produced OFF a build system", " ".join(self.field_lines(3)))

    def test_a_receipt_with_no_such_field_reads_as_not_established(self):
        self.full_store()
        line = " ".join(self.field_lines(3))
        self.assertIn("origin NOT established", line)
        self.assertNotIn("produced OFF a build system", line,
                         "a receipt that never carried the field was reported as "
                         "if it had been checked and found to be a laptop")


class TheDepositIsReadNotRequired(PassportCase):
    """Property 3."""

    def test_an_absent_deposit_is_named_as_absent(self):
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertIn("no producer deposit at", note)

    def test_a_corrupt_deposit_is_a_defect_not_an_absence(self):
        write_text(self.sbe("passport.json"), "{not json")
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertIn("not an absent one", note)
        self.assertEqual(self.run_cli().returncode, 0,
                         "a corrupt deposit blocked, and this tool is not a gate")

    def test_a_deposit_fills_the_two_fields_assurance_cannot(self):
        self.full_store()
        write_json(self.sbe("passport.json"), {
            "schema": "change-passport/v1",
            "whoDidIt": ["session 1234, accountable: a named engineer"],
            "whereItCameFrom": "the team's own specification-first flow",
        })
        self.assertEqual(self.field_state(2), pp.CARRIED)
        self.assertEqual(self.field_state(5), pp.CARRIED)
        gaps = " ".join(self.field_lines(4))
        self.assertNotIn("field 2, who did it, is not established", gaps,
                         "field 4 still reported field 2 missing after the "
                         "producer filled it")

    def test_the_deposit_never_invents_a_line_from_a_hollow_value(self):
        for hollow in ({"schema": "change-passport/v1", "whoDidIt": ""},
                       {"schema": "change-passport/v1", "whoDidIt": "   "},
                       {"schema": "change-passport/v1", "whoDidIt": []},
                       {"schema": "change-passport/v1", "whoDidIt": None},
                       {"schema": "change-passport/v1", "whoDidIt": [""]}):
            with self.subTest(hollow=json.dumps(hollow)):
                write_json(self.sbe("passport.json"), hollow)
                self.assertEqual(self.field_state(2), pp.NO_DATA,
                                 "a hollow deposit value filled a field")


class ReceiptsNobodyClaimedAreStillReported(PassportCase):
    """The defect a smoke test against a real dossier found: three receipts sat in
    the store while field 3 read as if nothing had run, because the walk could only
    reach a receipt some task pointed at."""

    def test_a_receipt_with_no_task_is_reported_as_unbound(self):
        write_json(self.sbe("evidence", "gate.json"), self.receipt(ci_run_id="run-9"))
        line = " ".join(self.field_lines(3))
        self.assertIn("UNBOUND receipt", line)
        self.assertIn("answers no required check", line)

    def test_an_unbound_receipt_does_not_read_as_an_empty_store(self):
        write_json(self.sbe("evidence", "gate.json"), self.receipt())
        gaps = " ".join(self.field_lines(4))
        self.assertIn("no task was ever opened here", gaps)
        self.assertNotIn("nothing at all was established here", gaps)

    def test_a_task_bound_receipt_is_not_double_counted_as_unbound(self):
        self.full_store()
        self.assertNotIn("UNBOUND", " ".join(self.field_lines(3)),
                         "the same receipt was reported twice, once through its "
                         "task and once as unclaimed")


class TheOutputPathIsHandled(PassportCase):
    def test_out_writes_the_page_and_prints_nothing(self):
        self.full_store()
        out_path = os.path.join(self.root, "passport.txt")
        proc = self.run_cli("--out", out_path)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        with io.open(out_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("CHANGE PASSPORT (consumer half)", content)
        self.assertIn("SEAM STATE:", content)

    def test_an_unwritable_out_is_the_only_nonzero_exit(self):
        self.full_store()
        blocked = os.path.join(self.root, "afile")
        write_text(blocked, "x")
        proc = self.run_cli("--out", os.path.join(blocked, "nested", "p.txt"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("could not write", proc.stderr)


class ConsumedFromTheCanonicalFixtureBytesAlone(PassportCase):
    """S2, consumer conformance against the Change Passport v1 producer fixture
    that ships from the sibling BrotherMode repository. The pinned hash below is
    the cross-repo contract: BrotherMode's BrotherSBEContractProofTests proves
    fields 2 and 5 CARRIED from these same bytes on its own side, and this class
    proves the identical claim from this repository's own copy, consumed through
    sbe_passport.py's real file-based entry point (a deposit at
    .sbe/passport.json under a root), never through a dict built in this test."""

    def test_the_fixture_bytes_match_the_pinned_cross_repo_hash(self):
        with io.open(CANONICAL_FIXTURE, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(digest, CANONICAL_FIXTURE_SHA256,
                         "tools/fixtures/change-passport.v1.canonical.json no "
                         "longer matches the pinned hash from the sibling "
                         "BrotherMode repository; the cross-repo contract is "
                         "broken until the bytes are re-synced and re-pinned")

    def test_fields_two_and_five_are_carried_from_the_fixture_bytes(self):
        copy_fixture_into(self.sbe("passport.json"))
        self.assertEqual(self.field_state(2), pp.CARRIED)
        self.assertEqual(self.field_state(5), pp.CARRIED)
        # The exact lines, not just the state: this is what the BM-side suite
        # proves from the same bytes, so a match here is the seam actually
        # agreeing rather than two sides independently reaching CARRIED.
        self.assertEqual(self.field_lines(2), [
            "accountable: Fixture Person",
            "session sess-xyz: claims Do the thing, upsert_project",
        ])
        self.assertEqual(self.field_lines(5),
                         ["no method plugin detected; native flow"])

    def test_zero_reach_through_sibling_root_unset_or_nonexistent(self):
        """No producer state should ever come from SBE_SIBLING_ROOT: this
        module never reads that variable (grep of sbe_passport.py and
        sbe_onepager.py finds no reference), and this test proves the
        observable consequence rather than trusting the grep. Consumption
        must read ONLY the fixture file placed in this root's .sbe/, so
        running it with the variable unset and with it pointed at a
        directory that does not exist must produce byte-identical output."""
        copy_fixture_into(self.sbe("passport.json"))
        cli = [sys.executable, os.path.join(HERE, "sbe_passport.py"),
              "--root", self.root, "--json"]

        env_unset = dict(os.environ)
        env_unset.pop("SBE_SIBLING_ROOT", None)
        env_unset["PYTHONDONTWRITEBYTECODE"] = "1"  # G2, see run_cli above
        proc_unset = subprocess.run(cli, capture_output=True, text=True,
                                    env=env_unset)

        env_nonexistent = dict(env_unset)
        env_nonexistent["SBE_SIBLING_ROOT"] = (
            "/nonexistent/definitely-not-here/sbe-passport-reach-through-probe")
        proc_nonexistent = subprocess.run(cli, capture_output=True, text=True,
                                          env=env_nonexistent)

        self.assertEqual(proc_unset.returncode, 0)
        self.assertEqual(proc_nonexistent.returncode, 0)
        self.assertEqual(proc_unset.stdout, proc_nonexistent.stdout,
                         "output changed when SBE_SIBLING_ROOT pointed at a "
                         "nonexistent directory instead of being unset; the "
                         "consumer reached beyond the fixture file it was given")
        # Not a vacuous match: prove the run actually consumed the fixture
        # bytes both times, rather than both sides quietly erroring alike.
        self.assertIn("Fixture Person", proc_unset.stdout)
        self.assertIn("Fixture Person", proc_nonexistent.stdout)


class AbsentPassportDegradesHonestlyAndNeverNags(PassportCase):
    """S3, absent-arm degradation. With no passport deposit at all (which is
    also what an absent BrotherMode installation looks like from this side,
    since the only thing that would ever exist is the deposit it writes),
    fields 2 and 5 must degrade to NO-DATA rather than an invented value, and
    nothing in the output or in the module's own source may suggest installing
    BrotherMode as the remedy.

    PINNING, not red-then-green: reading sbe_passport.py end to end (docstring,
    read_deposit, render_text, render_json) turns up exactly one BrotherMode
    mention, the seam docstring naming which side produces the deposit, and no
    install suggestion anywhere. Current behaviour already matches the goal, so
    these tests report NOT-REPRODUCED (already correct) and exist to keep it
    that way rather than to flip a red case green."""

    INSTALL_NAG_WORDS = ("install", "npm install", "pip install", "brew install",
                         "get brothermode", "download brothermode",
                         "set up brothermode")

    def test_fields_two_and_five_degrade_to_no_data_on_a_bare_root(self):
        self.assertEqual(self.field_state(2), pp.NO_DATA)
        self.assertEqual(self.field_state(5), pp.NO_DATA)
        self.assertEqual(self.field_lines(2), [],
                         "field 2 invented a value on a root with no passport "
                         "and no store at all")
        self.assertEqual(self.field_lines(5), [],
                         "field 5 invented a value on a root with no passport "
                         "and no store at all")

    def test_no_rendered_output_suggests_installing_brothermode(self):
        self.full_store()
        text = self.run_cli().stdout.lower()
        payload = self.run_cli("--json").stdout.lower()
        for surface_name, surface in (("text", text), ("json", payload)):
            for word in self.INSTALL_NAG_WORDS:
                with self.subTest(surface=surface_name, word=word):
                    self.assertNotIn(word, surface,
                                     "%s output over an absent passport "
                                     "suggested installing BrotherMode as the "
                                     "remedy" % surface_name)

    def test_the_source_module_carries_no_install_suggestion(self):
        with io.open(os.path.join(HERE, "sbe_passport.py"),
                    encoding="utf-8") as fh:
            source = fh.read().lower()
        for word in ("install", "npm install", "pip install", "brew install"):
            with self.subTest(word=word):
                self.assertNotIn(word, source,
                                 "sbe_passport.py's own source suggests "
                                 "installing something, which is an install "
                                 "nag naming a remedy for an absent passport")


class HollowMembersNeverReadAsCarried(PassportCase):
    """F1. Before the fix: sbe_passport imported sbe_onepager's blank-only
    `answered`, so a deposit whose members were all hollow tokens
    ("unknown", "TODO", "-", "n/a", "?", "TBD") rendered every field CARRIED
    and the SEAM STATE line counted every one of them; a non-string member
    (an int, a dict, a NaN) rendered as its Python repr counted the same
    way. RED before the fix: field_state read CARRIED and field_lines held
    the literal hollow text or a repr. GREEN after: NO-DATA, empty lines,
    and the reason named in field 4."""

    def test_a_field_of_only_hollow_tokens_is_no_data_not_carried(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["unknown", "TODO", "-", "n/a", "?", "TBD"]))
        self.assertEqual(self.field_state(2), pp.NO_DATA,
                         "a deposit of nothing but hollow tokens read as CARRIED")
        self.assertEqual(self.field_lines(2), [])

    def test_the_seam_state_line_counts_only_honestly_carried_fields(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["unknown"], whatWasRun=["TODO"], whereItCameFrom=["-"]))
        text = self.run_cli().stdout
        # Field 4 is never empty (property 1 of this suite), so it alone
        # carries; the three hollowed fields plus field 1 (nothing deposited
        # or store-derived) must NOT be counted.
        self.assertIn("SEAM STATE: 1 of 5 fields carried, 4 NO-DATA.", text,
                      "the seam state line counted a hollow-token field as "
                      "carried")

    def test_a_non_string_member_is_named_non_string_not_rendered_as_a_repr(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=[1, {"a": 2}, float("nan")]))
        self.assertEqual(self.field_state(2), pp.NO_DATA,
                         "non-string members were counted as carried content")
        self.assertEqual(self.field_lines(2), [])
        gaps = " ".join(self.field_lines(4))
        self.assertIn("non-string", gaps)
        # The repr may appear WITHIN the labelled "is non-string" sentence
        # (a reader needs to see what was rejected); field_lines(2) already
        # proved above that it never reached a CARRIED line of its own.
        self.assertIn("{'a': 2}", gaps,
                      "the non-string member's value was not named beside "
                      "the reason it was rejected")

    def test_a_mix_of_real_and_hollow_members_keeps_only_the_real_one(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["unknown", "accountable: Real Person"]))
        self.assertEqual(self.field_state(2), pp.CARRIED)
        self.assertEqual(self.field_lines(2), ["accountable: Real Person"])
        gaps = " ".join(self.field_lines(4))
        self.assertIn("hollow token", gaps)


class EmptyNotEstablishedIsAClaim(PassportCase):
    """F2. `whatWasNotEstablished: []` is a CLAIM the producer made
    ("nothing was left unestablished"), never silent absence. It is
    honoured only with a real justification at
    details.notEstablished.noneClaimJustification, and flagged as
    suspicious vacuity otherwise; either way it is visible in field 4,
    never silently absorbed into the store-derived gaps as though the key
    had never been written."""

    def test_an_empty_list_with_no_justification_is_flagged_suspicious(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasNotEstablished=[],
            details={"notEstablished": {"items": [],
                                        "noneClaimJustification": None}}))
        gaps = " ".join(self.field_lines(4))
        self.assertIn("claims nothing was left unestablished", gaps)
        self.assertIn("suspicious vacuity", gaps)

    def test_an_empty_list_with_a_real_justification_is_quoted_back(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasNotEstablished=[],
            details={"notEstablished": {
                "items": [],
                "noneClaimJustification": "a two-line diff, read by two reviewers"}}))
        gaps = " ".join(self.field_lines(4))
        self.assertIn("claims nothing was left unestablished", gaps)
        self.assertIn("justified: a two-line diff, read by two reviewers", gaps)
        self.assertNotIn("suspicious vacuity", gaps)

    def test_an_empty_list_with_a_hollow_justification_is_still_suspicious(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasNotEstablished=[],
            details={"notEstablished": {"items": [],
                                        "noneClaimJustification": "TBD"}}))
        gaps = " ".join(self.field_lines(4))
        self.assertIn("suspicious vacuity", gaps,
                      "a hollow-token justification ('TBD') was accepted as "
                      "a real one")

    def test_an_absent_key_is_silence_not_a_claim(self):
        write_json(self.sbe("passport.json"), minimal_deposit())
        gaps = " ".join(self.field_lines(4))
        self.assertNotIn("claims nothing was left unestablished", gaps,
                         "a deposit that never wrote the key was read as "
                         "though it made an empty claim")


class FieldFourCombinesStoreAndProducerGaps(PassportCase):
    """F3. The blind spot the original suite had: nothing asserted field 4's
    shape when a deposit IS present alongside a real store, so a mutation
    making field 4 producer-only (dropping the store's own gaps) would have
    stayed green. This class pins both halves at once, plus the F2 vacuity
    arm firing alongside a real store."""

    def test_field_four_carries_both_the_store_gap_and_the_producer_claim(self):
        self.full_store()
        write_json(self.sbe("evidence", "T01-receipt.json"), {"argv": ["pytest"]})
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasNotEstablished=["no performance measurement was taken"]))
        gaps = " ".join(self.field_lines(4))
        self.assertIn("T01", gaps,
                      "the store's own gap (a receipt missing a verdict) "
                      "vanished once a deposit was present")
        self.assertIn("no performance measurement was taken", gaps,
                      "the producer's declared gap vanished once the store "
                      "had its own gaps too")

    def test_the_vacuity_arm_still_fires_alongside_a_real_store(self):
        self.full_store()
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasNotEstablished=[],
            details={"notEstablished": {"items": [],
                                        "noneClaimJustification": None}}))
        gaps = " ".join(self.field_lines(4))
        self.assertIn("suspicious vacuity", gaps,
                      "F2's vacuity arm did not fire once a real store was "
                      "also present")
        self.assertIn("no gap was found among the", gaps,
                      "the store's own clean-store sentence vanished once a "
                      "deposit was present")


class BindingToTheChange(PassportCase):
    """F4. Before the fix, mangling schema, generatedAt, sensitivity,
    change, and details produced byte-identical output: a passport for
    another repo or another commit read as this root's own provenance,
    because nothing compared the store's own receipt commits against the
    deposit's claimed headCommit, and nothing in the printed note let a
    reader judge what the deposit even claimed to be about."""

    def test_the_note_names_the_deposits_own_claims(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            generatedAt="2026-08-20T10:00:00Z",
            change={"repo": "some-other-repo", "projectId": "proj-xyz",
                   "baseCommit": "1111111", "headCommit": "2222222",
                   "filesTouched": []}))
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertIn("generatedAt 2026-08-20T10:00:00Z", note)
        self.assertIn("change.repo some-other-repo", note)
        self.assertIn("change.projectId proj-xyz", note)
        self.assertIn("commit range 1111111..2222222", note)

    def test_a_missing_claim_is_named_not_stated_not_dropped(self):
        write_json(self.sbe("passport.json"), minimal_deposit())
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertIn("generatedAt not stated", note)
        self.assertIn("commit range not stated", note)

    def test_a_disagreeing_head_commit_prints_an_explicit_line(self):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="storecommit0000000000000000000000000000"))
        write_json(self.sbe("passport.json"), minimal_deposit(
            change={"repo": "r", "projectId": "p", "baseCommit": "1111111",
                   "headCommit": "depositcommit000000000000000000000000",
                   "filesTouched": []}))
        line = " ".join(self.field_lines(1))
        self.assertIn("DISAGREEMENT", line)
        self.assertIn("storecommit0000000000000000000000000000", line)
        self.assertIn("depositcommit000000000000000000000000", line)

    def test_agreeing_head_commits_print_no_disagreement(self):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="samecommit00000000000000000000000000"))
        write_json(self.sbe("passport.json"), minimal_deposit(
            change={"repo": "r", "projectId": "p", "baseCommit": "1111111",
                   "headCommit": "samecommit00000000000000000000000000",
                   "filesTouched": []}))
        line = " ".join(self.field_lines(1))
        self.assertNotIn("DISAGREEMENT", line)

    def test_field_one_labels_which_side_asserted_each_line(self):
        self.full_store()
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasDone=["a producer-authored summary of the change"]))
        lines = self.field_lines(1)
        self.assertTrue(any(l.startswith("store receipt:") for l in lines),
                        "no field-1 line was labeled as coming from the store")
        self.assertTrue(any(l.startswith("producer deposit:") for l in lines),
                        "no field-1 line was labeled as coming from the "
                        "producer deposit")

    def test_a_wrong_head_deposit_carries_a_machine_readable_disagreement_key(self):
        """Hostile-review finding D: before this fix, `--json` on a
        wrong-head deposit emitted top-level keys carried/depositNote/
        fields/root/total with `"carried": 5, "total": 5` and field 1 still
        CARRIED -- the ONLY trace of the conflict was the prose
        "DISAGREEMENT: ..." string sitting inside field 1's `lines`, so a
        programmatic consumer reading the documented machine-readable
        surface got an optimistic answer on exactly the attack the
        disagreement line exists to catch. `disagreement` must now let a
        consumer branch on the conflict WITHOUT parsing that sentence."""
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="storecommit0000000000000000000000000000"))
        write_json(self.sbe("passport.json"), minimal_deposit(
            change={"repo": "r", "projectId": "p", "baseCommit": "1111111",
                   "headCommit": "depositcommit000000000000000000000000",
                   "filesTouched": []}))
        payload = json.loads(self.run_cli("--json").stdout)
        self.assertIn("disagreement", payload,
                      "no top-level `disagreement` key at all: a consumer reading "
                      "carried/total/fields still has nothing to branch on except prose")
        disagreement = payload["disagreement"]
        self.assertIsNotNone(
            disagreement,
            "a wrong-head deposit must not carry a null `disagreement`: %r" % payload)
        # A consumer branches on this without ever touching field 1's prose.
        self.assertIn("storecommit0000000000000000000000000000", disagreement["store"])
        self.assertEqual(disagreement["producerDeposit"],
                         "depositcommit000000000000000000000000")
        # Field 1 (whatWasDone) is still CARRIED despite the conflict: the
        # counts alone read as an unqualified pass on this exact fixture,
        # which is why a consumer needs `disagreement` rather than inferring
        # anything from carried/total.
        by_number = dict((f["number"], f["state"]) for f in payload["fields"])
        self.assertEqual(by_number[1], "CARRIED", payload)

    def test_an_agreeing_deposit_carries_no_disagreement_key_value(self):
        """The negative half of the same fixture: two sides that name the
        SAME commit must never manufacture a disagreement -- `disagreement`
        is null (JSON's own honest "nothing to report" here, matching the
        module's existing None-for-absence convention elsewhere)."""
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="samecommit00000000000000000000000000"))
        write_json(self.sbe("passport.json"), minimal_deposit(
            change={"repo": "r", "projectId": "p", "baseCommit": "1111111",
                   "headCommit": "samecommit00000000000000000000000000",
                   "filesTouched": []}))
        payload = json.loads(self.run_cli("--json").stdout)
        self.assertIn("disagreement", payload,
                      "the key must be present (as null) even on a clean run, so a "
                      "consumer never has to distinguish absent-key from no-conflict")
        self.assertIsNone(payload["disagreement"], payload)

    def test_a_root_with_no_deposit_at_all_carries_no_disagreement(self):
        """No producer deposit, no store claim to conflict with it: the
        clean-fixture case at its simplest, and the one every empty-root
        `--json` call already exercises."""
        self.full_store()
        payload = json.loads(self.run_cli("--json").stdout)
        self.assertIsNone(payload["disagreement"], payload)


class ClaimsNoteRefusesUnvalidatedDeposit(PassportCase):
    """G4. Before the fix, the refused-version branch (R2-D) still routed
    an UNVALIDATED deposit's claims straight into the printed note through
    one_line/str, so a non-string change.repo printed its Python repr and
    an oversized string claim printed in full: the reviewer's fixtures were
    change.repo of {'nested': ['container', 'here']} (a Python repr on the
    page) and a 16,000-character change.repo (a 16k+-character note line).
    The claims note now renders only answered STRINGS (the one vacuity
    rule), truncates any single claim to CLAIM_MAX_LEN, and names a
    non-string claim as unusable rather than repr-ing it."""

    def test_a_dict_valued_change_repo_is_named_unusable_not_repr_d(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            change={"repo": {"nested": ["container", "here"]},
                   "projectId": "p", "baseCommit": "1111111",
                   "headCommit": "2222222", "filesTouched": []}))
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertNotIn("nested", note,
                         "the dict-valued change.repo rendered its own "
                         "content into the note instead of being refused")
        self.assertNotIn("{'nested'", note,
                         "the dict-valued change.repo printed a Python repr")
        self.assertIn("unusable", note)
        self.assertIn("change.repo", note)

    def test_an_oversized_change_repo_is_truncated_visibly(self):
        huge = "r" * 16000
        write_json(self.sbe("passport.json"), minimal_deposit(
            change={"repo": huge, "projectId": "p", "baseCommit": "1111111",
                   "headCommit": "2222222", "filesTouched": []}))
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertLess(len(note), 16000,
                        "an oversized change.repo claim was printed in "
                        "full onto the note line")
        self.assertIn("(truncated)", note)
        self.assertNotIn(huge, note)

    def test_a_normal_length_string_claim_is_not_truncated(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            change={"repo": "an-ordinary-repo-name", "projectId": "p",
                   "baseCommit": "1111111", "headCommit": "2222222",
                   "filesTouched": []}))
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertIn("change.repo an-ordinary-repo-name", note)
        self.assertNotIn("(truncated)", note)

    def test_a_non_string_generated_at_is_named_unusable(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            generatedAt=["not", "a", "string"]))
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertIn("unusable", note)
        self.assertIn("generatedAt", note)

    def test_unusable_still_reaches_the_note_on_a_refused_version(self):
        # R2-D: the note's claims line still prints for a refused-version
        # deposit. G4's refusal must hold there too, not only on a v1
        # deposit, since the finding is specifically about the
        # refused-version branch routing an unvalidated deposit through.
        write_json(self.sbe("passport.json"), {
            "schema": "change-passport/v2",
            "change": {"repo": {"nested": ["container", "here"]}},
        })
        _fields, note, _disagreement = pp.build_fields(self.root)
        self.assertNotIn("{'nested'", note)
        self.assertIn("unusable", note)


class CommitAgreementIsPrefixAware(unittest.TestCase):
    """R2-A, direct unit tests of commitish_agrees. Before the fix,
    commit_disagreement_line compared commitish strings by exact equality,
    but the producer schema's own commitish shape is short or full hex
    (`^[0-9a-fA-F]{7,40}$`), so a store receipt's short hash and the
    deposit's full 40-hex of the SAME commit were never equal as strings,
    and neither was a plain case difference. The reviewer's exact cases."""

    def test_store_short_vs_deposit_full_of_the_same_commit_agrees(self):
        self.assertTrue(pp.commitish_agrees(
            "884269e", "884269e123456789abcdef0123456789abcdef01"))

    def test_store_full_vs_deposit_short_of_the_same_commit_agrees(self):
        self.assertTrue(pp.commitish_agrees(
            "884269e123456789abcdef0123456789abcdef01", "884269e"))

    def test_a_case_difference_alone_still_agrees(self):
        self.assertTrue(pp.commitish_agrees("ABC1234", "abc1234"))

    def test_genuinely_different_commits_do_not_agree(self):
        self.assertFalse(pp.commitish_agrees("1111111", "2222222"))

    def test_a_prefix_shorter_than_seven_hex_chars_is_too_ambiguous_to_agree(self):
        self.assertFalse(pp.commitish_agrees("abc12", "abc12ff"))

    def test_an_absent_side_never_agrees(self):
        self.assertFalse(pp.commitish_agrees("", "abc1234"))
        self.assertFalse(pp.commitish_agrees("abc1234", ""))


class IdenticalClaimsAlwaysAgree(unittest.TestCase):
    """G1, regression fixed. Before the fix, commitish_agrees required the
    SHORTER value to be at least 7 characters before it would even consider
    agreement, so two byte-identical commit strings shorter than 7
    characters returned False: the same fixture printed no disagreement at
    5188f33^ and a disagreement at 5188f33. Equal values must always agree,
    whatever their length or shape; the 7-character hex floor applies only
    to the PREFIX arm, where a short value could accidentally prefix an
    unrelated commit."""

    def test_identical_five_char_values_agree(self):
        self.assertTrue(pp.commitish_agrees("abc12", "abc12"))

    def test_identical_six_char_values_agree(self):
        # The exact shape this suite's own default fixture uses:
        # PassportCase.receipt's default head="abc123" (six characters).
        self.assertTrue(pp.commitish_agrees("abc123", "abc123"))

    def test_identical_non_hex_values_agree(self):
        self.assertTrue(pp.commitish_agrees("main-branch", "main-branch"))

    def test_different_non_hex_values_sharing_a_prefix_disagree(self):
        # G1, second half: the docstring said "at least 7 hex characters"
        # while the code only counted characters, so a non-hex value could
        # ride the prefix arm and suppress a real disagreement.
        self.assertFalse(pp.commitish_agrees("main-branch", "main-branch-2"))

    def test_short_hex_prefixing_a_longer_hex_of_the_same_commit_agrees(self):
        self.assertTrue(pp.commitish_agrees(
            "abc1234", "abc1234ff00112233445566778899aabbccddee"))

    def test_commits_sharing_seven_hex_chars_but_diverging_later_disagree(self):
        self.assertFalse(pp.commitish_agrees(
            "abc1234abc0000000000000000000000000000",
            "abc1234fff0000000000000000000000000000"))


class CommitDisagreementLineIsPrefixAware(PassportCase):
    """R2-A, through the real seam: commit_disagreement_line as it actually
    renders on field 1, for the same cases as above plus the "one side
    absent" case that only makes sense at this level (commitish_agrees takes
    two strings; the seam decides what "absent" means for a whole field)."""

    def _deposit_for(self, head):
        return minimal_deposit(change={
            "repo": "r", "projectId": "p", "baseCommit": "1111111",
            "headCommit": head, "filesTouched": []})

    def test_store_short_vs_deposit_full_prints_no_disagreement(self):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="884269e"))
        write_json(self.sbe("passport.json"), self._deposit_for(
            "884269e123456789abcdef0123456789abcdef01"))
        self.assertNotIn("DISAGREEMENT", " ".join(self.field_lines(1)))

    def test_store_full_vs_deposit_short_prints_no_disagreement(self):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="884269e123456789abcdef0123456789abcdef01"))
        write_json(self.sbe("passport.json"), self._deposit_for("884269e"))
        self.assertNotIn("DISAGREEMENT", " ".join(self.field_lines(1)))

    def test_a_case_difference_prints_no_disagreement(self):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"), self.receipt(head="ABC1234"))
        write_json(self.sbe("passport.json"), self._deposit_for("abc1234"))
        self.assertNotIn("DISAGREEMENT", " ".join(self.field_lines(1)))

    def test_genuinely_different_commits_still_disagree(self):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"), self.receipt(head="1111111"))
        write_json(self.sbe("passport.json"), self._deposit_for("2222222"))
        self.assertIn("DISAGREEMENT", " ".join(self.field_lines(1)))

    def test_the_store_being_silent_is_not_a_disagreement(self):
        write_json(self.sbe("passport.json"), self._deposit_for("abc1234"))
        self.assertNotIn("DISAGREEMENT", " ".join(self.field_lines(1)))

    def test_a_store_with_several_commits_where_one_agrees_prints_no_disagreement(self):
        # G1: several receipts, several store commits, and only ONE of them
        # matches the deposit's claim; that single agreement must be enough
        # to suppress the disagreement line rather than the presence of the
        # other, unrelated commits forcing a false DISAGREEMENT.
        write_json(self.sbe("tasks.json"), self.registry(
            [self.task("T01"), self.task("T02", owned=["src/other.py"])]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="1111111"))
        write_json(self.sbe("evidence", "T02-receipt.json"),
                   self.receipt(head="2222222"))
        write_json(self.sbe("passport.json"), self._deposit_for("1111111"))
        self.assertNotIn("DISAGREEMENT", " ".join(self.field_lines(1)))


class StoreArmAcknowledgesTheDeposit(PassportCase):
    """F5. Before the fix, the empty-store and no-task sentences in field 4
    denied what the page itself displayed: "nothing at all was established
    here ... an empty store rather than a clean change" printed even when
    the deposit carried a green whatWasRun four lines above, and the
    no-task sentence said "nothing states what this change was supposed to
    prove" while whatWasDone was printed right there on the page."""

    def test_the_empty_store_sentence_acknowledges_a_present_deposit(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasRun=["`python3 tools/test_all.py` returned green"]))
        gaps = " ".join(self.field_lines(4))
        self.assertNotIn("nothing at all was established here", gaps,
                         "the empty-store sentence denied the deposit's "
                         "content that is printed on the same page")
        self.assertIn("the deposit above is the only carrier", gaps)

    def test_the_empty_store_sentence_is_unchanged_with_no_deposit_either(self):
        self.assertIn("nothing at all was established here",
                      " ".join(self.field_lines(4)))

    def test_the_no_task_sentence_acknowledges_a_present_deposit(self):
        write_json(self.sbe("evidence", "gate.json"), self.receipt())
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasDone=["a producer-authored account of this change"]))
        gaps = " ".join(self.field_lines(4))
        self.assertNotIn("nothing states what this change was supposed to prove", gaps)
        self.assertIn("carries its own claim about this change", gaps)

    def test_the_no_task_sentence_is_unchanged_with_no_deposit_either(self):
        write_json(self.sbe("evidence", "gate.json"), self.receipt())
        gaps = " ".join(self.field_lines(4))
        self.assertIn("nothing states what this change was supposed to prove", gaps)


class VersionIsRead(PassportCase):
    """F6. Before the fix, `schema` was never read: a v99 deposit or plain
    garbage consumed exactly like a real one, because F1's member
    validation was the only gate and a value that happened to be an
    answered string sailed through regardless of which document shape it
    came from."""

    def test_a_wrong_schema_version_degrades_every_field(self):
        write_json(self.sbe("passport.json"), {
            "schema": "change-passport/v99",
            "whoDidIt": ["accountable: Somebody"]})
        self.assertEqual(self.field_state(2), pp.NO_DATA,
                         "a v99 deposit was consumed as though it were v1")
        note = pp.build_fields(self.root)[1]
        self.assertIn("v99", note)
        self.assertIn("does not recognise it", note)

    def test_an_absent_schema_key_degrades_every_field(self):
        write_json(self.sbe("passport.json"), {
            "whoDidIt": ["accountable: Somebody"]})
        self.assertEqual(self.field_state(2), pp.NO_DATA)
        note = pp.build_fields(self.root)[1]
        self.assertIn("absent", note)

    def test_a_non_string_schema_degrades_every_field_without_crashing(self):
        write_json(self.sbe("passport.json"), {
            "schema": 99, "whoDidIt": ["accountable: Somebody"]})
        self.assertEqual(self.run_cli().returncode, 0,
                         "a non-string schema value crashed this tool")
        self.assertEqual(self.field_state(2), pp.NO_DATA)
        note = pp.build_fields(self.root)[1]
        self.assertIn("non-string", note)

    def test_the_recognised_version_is_consumed_as_now(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["accountable: Somebody"]))
        self.assertEqual(self.field_state(2), pp.CARRIED)

    def test_field_four_names_the_version_mismatch_too(self):
        write_json(self.sbe("passport.json"), {
            "schema": "change-passport/v2", "whoDidIt": ["accountable: X"]})
        gaps = " ".join(self.field_lines(4))
        self.assertIn("version is not recognised", gaps)


class SchemaNullVsAbsentIsDistinguished(PassportCase):
    """R2-E. schema_problem previously reported both a missing `schema` key
    and a key explicitly present with a JSON null as the same word,
    "absent", losing a distinction producer_class already keeps for
    ciRunId (PRODUCER_OFF_BUILD_SYSTEM vs PRODUCER_NOT_ESTABLISHED)."""

    def test_a_present_null_schema_is_named_present_and_null(self):
        write_json(self.sbe("passport.json"), {"schema": None, "whoDidIt": ["x"]})
        note = pp.build_fields(self.root)[1]
        self.assertIn("present and null rather than a string", note)
        self.assertNotIn("field is absent", note,
                         "a present-but-null schema was still called absent")

    def test_a_missing_schema_key_is_still_named_absent(self):
        write_json(self.sbe("passport.json"), {"whoDidIt": ["x"]})
        note = pp.build_fields(self.root)[1]
        self.assertIn("field is absent", note)
        self.assertNotIn("present and null", note,
                         "a genuinely missing key was named as if it had "
                         "been written with a null value")


class SensitivityHandling(PassportCase):
    """F7. Before the fix, `sensitivity` was discarded entirely: a "raw"
    deposit (carrying unredacted store text) and a "redacted" one printed
    the identical note, so a reader had no way to know a deposit needed
    handling like the store itself."""

    def test_raw_sensitivity_carries_the_handling_warning(self):
        write_json(self.sbe("passport.json"), minimal_deposit(sensitivity="raw"))
        note = pp.build_fields(self.root)[1]
        self.assertIn("sensitivity: raw", note)
        self.assertIn("UNREDACTED", note)

    def test_redacted_sensitivity_is_named_quietly(self):
        write_json(self.sbe("passport.json"), minimal_deposit(sensitivity="redacted"))
        note = pp.build_fields(self.root)[1]
        self.assertIn("sensitivity: redacted", note)
        self.assertNotIn("UNREDACTED", note)

    def test_an_unrecognised_sensitivity_value_is_named_as_such(self):
        write_json(self.sbe("passport.json"), minimal_deposit(sensitivity="pinkslip"))
        note = pp.build_fields(self.root)[1]
        self.assertIn("sensitivity: pinkslip", note)
        self.assertIn("unrecognised", note)


class SensitivityIsCaseInsensitive(PassportCase):
    """R2-F. "RAW" and "Raw" previously lost the handling warning because
    the comparison against "raw" was case-sensitive after strip alone. The
    note still quotes the producer's own original spelling, never the
    lowercased form used to judge it."""

    def test_upper_case_raw_still_warns_and_keeps_its_spelling(self):
        write_json(self.sbe("passport.json"), minimal_deposit(sensitivity="RAW"))
        note = pp.build_fields(self.root)[1]
        self.assertIn("UNREDACTED", note)
        self.assertIn("sensitivity: RAW", note,
                      "the producer's original spelling was not preserved")

    def test_mixed_case_raw_still_warns(self):
        write_json(self.sbe("passport.json"), minimal_deposit(sensitivity="Raw"))
        note = pp.build_fields(self.root)[1]
        self.assertIn("UNREDACTED", note)
        self.assertIn("sensitivity: Raw", note)

    def test_mixed_case_redacted_is_still_named_quietly_no_warning(self):
        write_json(self.sbe("passport.json"), minimal_deposit(sensitivity="Redacted"))
        note = pp.build_fields(self.root)[1]
        self.assertNotIn("UNREDACTED", note)
        self.assertIn("sensitivity: Redacted", note)


class RefusedVersionStillWarnsSensitivity(PassportCase):
    """R2-D. Before the fix, build_fields appended only the version-problem
    sentence on the refused-version branch, even though the comment above
    SCHEMA_V1 promises the deposit's claims line is still quoted for an
    unrecognized version. A v2 deposit marked sensitivity: raw is still
    UNREDACTED store text and losing the handling warning just because its
    schema went unrecognised is exactly the false safety this note exists
    to prevent. Field CONTENT stays gated on the recognised schema either
    way; only the note's claims/sensitivity lines are affected."""

    def test_a_refused_version_deposit_still_shows_the_raw_warning(self):
        write_json(self.sbe("passport.json"), {
            "schema": "change-passport/v2",
            "sensitivity": "raw",
            "generatedAt": "2026-08-20T10:00:00Z",
        })
        note = pp.build_fields(self.root)[1]
        self.assertIn("sensitivity: raw", note)
        self.assertIn("UNREDACTED", note)
        self.assertIn("generatedAt 2026-08-20T10:00:00Z", note,
                      "the deposit's claims note was dropped on the "
                      "refused-version branch")
        self.assertIn("v2", note, "the version problem itself must still "
                      "be named")

    def test_field_content_still_stays_gated_on_the_recognised_schema(self):
        write_json(self.sbe("passport.json"), {
            "schema": "change-passport/v2",
            "sensitivity": "raw",
            "whoDidIt": ["accountable: Somebody"],
        })
        self.assertEqual(self.field_state(2), pp.NO_DATA,
                         "a v2 deposit's field content was consumed as v1 "
                         "just because the sensitivity note was restored")


class HeaderForgeryDefanged(PassportCase):
    """F9. A producer or engineer value that is exactly the shape of one of
    this tool's own page headers ("5. WHERE IT CAME FROM [CARRIED]") must
    not print as a second, forged header once it lands among the real ones
    on the flat text page. The JSON surface is unaffected: JSON is
    structured, so the same string sitting in a "lines" array is
    unambiguous data, never mistaken for a header."""

    FORGED_HEADER = "5. WHERE IT CAME FROM [CARRIED]"

    def test_the_text_surface_defangs_a_header_shaped_value(self):
        # Placed in field 2 on purpose: field 5 stays NO-DATA here (its own
        # real header reads "...[NO-DATA]"), so the only way the exact
        # bracketed CARRIED string can appear is as forged content.
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=[self.FORGED_HEADER]))
        text_lines = self.run_cli().stdout.splitlines()
        self.assertNotIn(self.FORGED_HEADER, text_lines,
                         "the forged header still stands as its own line, "
                         "unlabelled, indistinguishable from a real header")
        self.assertIn("(reported value, not a section header) " + self.FORGED_HEADER,
                      text_lines)

    def test_the_json_surface_carries_the_value_unguarded(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=[self.FORGED_HEADER]))
        payload = json.loads(self.run_cli("--json").stdout)
        by_number = dict((f["number"], f) for f in payload["fields"])
        self.assertEqual(by_number[2]["lines"], [self.FORGED_HEADER],
                         "the JSON surface changed a value it should have "
                         "passed through exactly as the producer wrote it")


class SeamLiteralLinesAreDefanged(PassportCase):
    """R2-B. F9's guard only matched this tool's numbered section headers
    ("^\\d+\\. ..."), but tools/sbe_passport.py's render_text also has three
    OTHER literal line shapes of its own: the page title ("CHANGE PASSPORT
    ..."), the "SEAM STATE: N of M fields carried, K NO-DATA." footer, and
    every "NO-DATA: ..." field line. A producer deposit member reading
    exactly like one of those printed verbatim, and a first-match scraper
    reading the first line starting with that prefix could read the forged
    one instead of the real one. Fields 2, 3 and 5 print deposit content
    RAW (no "producer deposit:" prefix the way field 1 gets), so those are
    where the forgery actually lands unguarded before the fix."""

    def test_whatwasdone_carrying_the_seam_state_string_is_the_reviewers_own_fixture(self):
        # The literal reviewer fixture. Field 1's producer lines are always
        # prefixed "producer deposit: " by build_fields, so this specific
        # field never actually reaches the page unprefixed either way; this
        # test pins that field 1 also never grows a second, unlabelled
        # "SEAM STATE:" line, alongside the fields below where it does.
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasDone=["SEAM STATE: 5 of 5 fields carried, 0 NO-DATA."]))
        text_lines = self.run_cli().stdout.splitlines()
        matches = [l for l in text_lines if l.startswith("SEAM STATE:")]
        self.assertEqual(len(matches), 1,
                         "more than one line on the page starts with "
                         "'SEAM STATE:'; a first-match scraper could read "
                         "the forged one instead of the real footer")

    def test_a_forged_seam_state_line_in_who_did_it_is_defanged(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["SEAM STATE: 5 of 5 fields carried, 0 NO-DATA."]))
        text_lines = self.run_cli().stdout.splitlines()
        matches = [l for l in text_lines if l.startswith("SEAM STATE:")]
        self.assertEqual(len(matches), 1)
        self.assertTrue(
            any(l.startswith("(reported value, not a section header) "
                             "SEAM STATE:") for l in text_lines),
            "the forged SEAM STATE content was not defanged in place")

    def test_a_forged_page_title_in_what_was_run_is_defanged(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasRun=["CHANGE PASSPORT (consumer half) for /some/other/root"]))
        text_lines = self.run_cli().stdout.splitlines()
        matches = [l for l in text_lines if l.startswith("CHANGE PASSPORT")]
        self.assertEqual(len(matches), 1,
                         "more than one line on the page starts with "
                         "'CHANGE PASSPORT'; the page title was forgeable")

    def test_a_forged_no_data_sentence_in_where_it_came_from_is_defanged(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whereItCameFrom=["NO-DATA: nothing to see here, move along"]))
        text_lines = self.run_cli().stdout.splitlines()
        forged = [l for l in text_lines if "nothing to see here" in l]
        self.assertEqual(len(forged), 1)
        self.assertTrue(forged[0].startswith("(reported value"),
                        "the forged NO-DATA sentence was not defanged")

    def test_the_json_surface_still_carries_the_value_unguarded(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["SEAM STATE: 5 of 5 fields carried, 0 NO-DATA."]))
        payload = json.loads(self.run_cli("--json").stdout)
        by_number = dict((f["number"], f) for f in payload["fields"])
        self.assertEqual(by_number[2]["lines"],
                         ["SEAM STATE: 5 of 5 fields carried, 0 NO-DATA."],
                         "the JSON surface changed a value it should pass "
                         "through exactly as the producer wrote it")


class DisagreementLiteralLineIsDefanged(PassportCase):
    """G3. R2-B's guard covered SEAM STATE, CHANGE PASSPORT and NO-DATA but
    missed the fourth literal line this module authors, "DISAGREEMENT:"
    (written by commit_disagreement_line). A deposit member string starting
    with DISAGREEMENT: printed unguarded at line start on a page where no
    disagreement was ever computed, exactly the finding's own scenario:
    these fixtures deliberately carry no store commit at all, so the ONLY
    "DISAGREEMENT:"-prefixed line on the page is the forged one."""

    def test_a_forged_disagreement_line_in_who_did_it_is_defanged(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["DISAGREEMENT: the store receipt(s) claim commit(s) "
                     "aaaaaaa, the producer deposit's change.headCommit "
                     "claims bbbbbbb; these do not match"]))
        text_lines = self.run_cli().stdout.splitlines()
        matches = [l for l in text_lines if l.startswith("DISAGREEMENT:")]
        self.assertEqual(matches, [],
                         "a forged DISAGREEMENT line stood unguarded on a "
                         "page where no disagreement was ever computed")
        self.assertTrue(
            any(l.startswith("(reported value, not a section header) "
                             "DISAGREEMENT:") for l in text_lines),
            "the forged DISAGREEMENT content was not defanged in place")

    def test_a_lowercase_forged_disagreement_line_is_also_defanged(self):
        # G3, second half: the prefix test is now case-insensitive, so a
        # lowercase "disagreement:" cannot slip past it either.
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasRun=["disagreement: nothing was actually compared here"]))
        text_lines = self.run_cli().stdout.splitlines()
        self.assertTrue(
            any(l.startswith("(reported value, not a section header) "
                             "disagreement:") for l in text_lines),
            "a lowercase forged 'disagreement:' line was not defanged")

    def test_the_json_surface_still_carries_the_value_unguarded(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["DISAGREEMENT: forged"]))
        payload = json.loads(self.run_cli("--json").stdout)
        by_number = dict((f["number"], f) for f in payload["fields"])
        self.assertEqual(by_number[2]["lines"], ["DISAGREEMENT: forged"],
                         "the JSON surface changed a value it should pass "
                         "through exactly as the producer wrote it")

    def test_a_real_disagreement_still_names_both_commits_on_the_rendered_page(self):
        # The real DISAGREEMENT line is assembled into field 1 alongside
        # store/producer content and goes through the same guard as any
        # other field-1 line, so it is now defanged too when rendered as
        # text; its content is not lost, only decorated, and this pins that
        # both commit ids are still readable on the page.
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="1111111"))
        write_json(self.sbe("passport.json"), minimal_deposit(change={
            "repo": "r", "projectId": "p", "baseCommit": "0000000",
            "headCommit": "2222222", "filesTouched": []}))
        text = self.run_cli().stdout
        self.assertIn("1111111", text)
        self.assertIn("2222222", text)


class TestSourceLoaderIgnoresPycache(unittest.TestCase):
    """F10. This suite's own loader (load_module_from_source, top of this
    file) must always read the bytes on disk, never a stale __pycache__
    entry keyed by mtime and size. Proven directly against the loader
    itself on a scratch module, because reproducing an actual stale-.pyc
    race against the real sbe_passport.py would mean mutating the module
    under test, which this suite must never do to its own subject."""

    def test_a_same_size_mutation_is_read_on_the_very_next_load(self):
        tmp = tempfile.mkdtemp(prefix="sbe-passport-loader-")
        try:
            path = os.path.join(tmp, "scratch_module_under_pycache_test.py")
            write_text(path, "VALUE = 1\n")
            first = load_module_from_source("scratch_module_under_pycache_test", path)
            self.assertEqual(first.VALUE, 1)
            # Same byte count as "VALUE = 1\n" (9 characters, newline
            # included), the exact shape a stale mtime+size cache would miss.
            write_text(path, "VALUE = 2\n")
            second = load_module_from_source("scratch_module_under_pycache_test", path)
            self.assertEqual(second.VALUE, 2,
                             "the loader returned a stale value: a same-size "
                             "mutation was invisible to it")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_dont_write_bytecode_is_set(self):
        self.assertTrue(sys.dont_write_bytecode,
                        "sys.dont_write_bytecode was not set; a mutation "
                        "harness run through this suite could still write a "
                        "fresh .pyc that a later run reads back as cache")

    def test_sbe_passport_resolved_its_imports_from_the_source_loaded_modules(self):
        """R2-C. Before the fix, only sbe_passport.py itself was compiled
        from source here; its own `from sbe_onepager import ...` and `from
        sbe_checks import answered` are plain import statements, so those
        two modules were resolved through the normal, __pycache__-backed
        import machinery instead, and a same-second same-size mutation to
        either was invisible to this suite. Fixed by loading sbe_checks and
        sbe_onepager from source FIRST and registering each in sys.modules
        under its real name before sbe_passport.py execs.

        G2: the `assertIs` pair below alone is a TAUTOLOGY, proven by
        deleting the two `load_module_from_source("sbe_checks", ...)` /
        `("sbe_onepager", ...)` calls above this class and watching this
        test still pass. The reason: sbe_passport.py's own
        `sys.path.insert(0, ...)` line (its own top-level code, run first
        during exec) already makes `tools/` importable, so even with those
        two injection lines gone, sbe_passport.py's `from sbe_onepager
        import ...` statement runs the standard import machinery, which
        registers ITS OWN freshly-imported module under
        sys.modules["sbe_onepager"] and hands sbe_passport the names from
        THAT module. `pp.one_line is sys.modules["sbe_onepager"].one_line`
        then still holds, because both sides are reading the same
        (standard-import, __pycache__-backed) module: the assertion checks
        the two names AGREE with each other, never which loader supplied
        either one.

        The real signal is `__spec__`/`__loader__`. A module built by
        load_module_from_source (`types.ModuleType(name)`, never touched by
        importlib) always has both set to None; a module resolved through
        the standard import machinery always carries a real
        `importlib.machinery.ModuleSpec` backed by a `SourceFileLoader`.
        Only the source-loaded modules registered by THIS file's own
        injection lines can ever pass the assertions below, so deleting
        those lines flips this test red, which is the property a
        regression test for R2-C must actually have."""
        self.assertIs(pp.one_line, sys.modules["sbe_onepager"].one_line,
                      "sbe_passport.one_line is not the source-loaded "
                      "sbe_onepager module's one_line; the import resolved "
                      "through disk instead of the injected sys.modules entry")
        self.assertIs(pp.answered, sys.modules["sbe_checks"].answered,
                      "sbe_passport.answered is not the source-loaded "
                      "sbe_checks module's answered; the import resolved "
                      "through disk instead of the injected sys.modules entry")
        self.assertIsNone(
            sys.modules["sbe_onepager"].__spec__,
            "sbe_onepager carries a real ModuleSpec, so it was resolved "
            "through the standard import machinery (and is subject to its "
            "__pycache__ caching) rather than through the source-loaded "
            "module this file's own injection line registers")
        self.assertIsNone(
            sys.modules["sbe_checks"].__spec__,
            "sbe_checks carries a real ModuleSpec, so it was resolved "
            "through the standard import machinery instead of the "
            "source-loaded module this file's own injection line registers")


class TheToolsOwnDisagreementIsNeverDefanged(PassportCase):
    """H1. Once "DISAGREEMENT:" joined the literal-line guard (G3), the
    guard also decorated the line THIS TOOL computes itself, which failed in
    both directions at once: the decoration reads "(reported value, not a
    section header)" over the tool's own conclusion, which is a false
    provenance label, and it left ZERO lines on the page starting with
    DISAGREEMENT:, so the first-match scraper the guard exists to protect
    read no disagreement on a page that had computed a real one. The
    distinction is made by TYPE (sbe_passport.ToolAuthored, a str subclass
    json.loads can never produce), so remove the isinstance check in
    render_text and the first test here goes red, while the forgery test
    below still holds."""

    def _store_and_deposit(self, deposit_head):
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="1111111"))
        write_json(self.sbe("passport.json"), minimal_deposit(change={
            "repo": "r", "projectId": "p", "baseCommit": "0000000",
            "headCommit": deposit_head, "filesTouched": []}))

    def test_a_real_disagreement_is_findable_by_its_own_prefix(self):
        self._store_and_deposit("2222222")
        text_lines = self.run_cli().stdout.splitlines()
        matches = [l for l in text_lines if l.startswith("DISAGREEMENT:")]
        self.assertEqual(len(matches), 1,
                         "the tool's own computed disagreement is not "
                         "findable by the prefix it is written with; a "
                         "reader scanning for DISAGREEMENT: sees nothing on "
                         "a page that computed one")

    def test_a_real_disagreement_carries_no_reported_value_label(self):
        self._store_and_deposit("2222222")
        text = self.run_cli().stdout
        self.assertNotIn("(reported value, not a section header) DISAGREEMENT:",
                         text,
                         "the tool's own finding is labelled as producer "
                         "reported content, which is false about it")

    def test_a_forged_disagreement_from_a_deposit_is_still_defanged(self):
        write_json(self.sbe("passport.json"), minimal_deposit(
            whoDidIt=["DISAGREEMENT: the store receipt(s) claim commit(s) "
                      "aaaaaaa, the producer deposit's change.headCommit "
                      "claims bbbbbbb; these do not match"]))
        text_lines = self.run_cli().stdout.splitlines()
        self.assertEqual(
            [l for l in text_lines if l.startswith("DISAGREEMENT:")], [],
            "a producer string forged a line reading as this tool's own "
            "disagreement finding")

    def test_the_type_and_not_the_text_is_what_the_renderer_trusts(self):
        self.assertIsInstance(pp.ToolAuthored("x"), str)
        self.assertNotIsInstance(
            "DISAGREEMENT: text a producer could deposit", pp.ToolAuthored)


class EveryDepositValueOnTheNoteLineIsCapped(PassportCase):
    """H2. G4 capped the claims note and left the two sibling call sites
    that print into the SAME note line from the SAME unvalidated deposit:
    sensitivity and schema. A 16,000 character value in either produced
    exactly the page line CLAIM_MAX_LEN's own docstring says a producer
    cannot produce."""

    def _note(self):
        _fields, note, _disagreement = pp.build_fields(self.root)
        return note

    def test_an_oversized_sensitivity_is_truncated(self):
        write_json(self.sbe("passport.json"),
                   minimal_deposit(sensitivity="raw" + "z" * 16000))
        note = self._note()
        self.assertLess(len(note), 2000, "the note carries an uncapped value")
        self.assertIn("(truncated)", note)

    def test_an_oversized_schema_is_truncated(self):
        write_json(self.sbe("passport.json"),
                   minimal_deposit(schema="change-passport/v" + "9" * 16000))
        note = self._note()
        self.assertLess(len(note), 2000, "the note carries an uncapped value")
        self.assertIn("(truncated)", note)

    def test_a_non_string_member_note_is_capped(self):
        # I1: the tool's own sentence about a non-string member embedded the
        # member's repr uncapped, which put 16,000 characters on one page line.
        write_json(self.sbe("passport.json"),
                   minimal_deposit(whoDidIt=[{"k": "X" * 16000}]))
        longest = max(len(l) for l in self.field_lines(4))
        self.assertLess(longest, 2000)

    def test_a_hollow_member_note_is_capped(self):
        # I3: the hollow-token arm interpolates the member's dict KEY, so the
        # fixture must be a dict whose key is oversized and whose value is
        # hollow. A list fixture never reaches the labelled branch and would
        # make this test pass whatever the code did.
        write_json(self.sbe("passport.json"),
                   minimal_deposit(whoDidIt={"K" * 16000: "TODO"},
                                   whatWasDone=["real"]))
        self.assertLess(max(len(l) for l in self.field_lines(4)), 2000)

    def test_an_oversized_none_claim_justification_is_capped(self):
        # I2.
        write_json(self.sbe("passport.json"), minimal_deposit(
            whatWasNotEstablished=[],
            details={"notEstablished": {"noneClaimJustification": "j" * 16000}}))
        self.assertLess(max(len(l) for l in self.field_lines(4)), 2000)

    def test_an_oversized_deposit_head_is_capped_inside_the_tool_line(self):
        # I4: the disagreement line renders UNGUARDED because it is
        # ToolAuthored, so the producer-controlled value inside it is capped.
        write_json(self.sbe("tasks.json"), self.registry([self.task()]))
        write_json(self.sbe("evidence", "T01-receipt.json"),
                   self.receipt(head="1111111"))
        write_json(self.sbe("passport.json"), minimal_deposit(change={
            "repo": "r", "projectId": "p", "baseCommit": "0000000",
            "headCommit": "2" * 16000, "filesTouched": []}))
        line = [l for l in self.field_lines(1) if l.startswith("DISAGREEMENT:")]
        self.assertEqual(len(line), 1)
        self.assertLess(len(line[0]), 2000)
        self.assertIn("(truncated)", line[0])

    def test_the_raw_handling_warning_survives_truncation(self):
        write_json(self.sbe("passport.json"),
                   minimal_deposit(sensitivity="raw"))
        self.assertIn("UNREDACTED", self._note())


if __name__ == "__main__":
    unittest.main(verbosity=1)
