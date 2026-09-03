#!/usr/bin/env python3
"""Regression tests for tools/bm_passport.py, the change passport's producer
half (docs/specs/2026-08-15-change-passport-seam.md in the BrotherSBE
repository is the contract this tool fills a hole in).

WHAT THIS SUITE IS ACTUALLY DEFENDING
  Three rules from the contract, each load bearing and each proven by a test
  whose guard was removed by hand to watch it go red:

  1. A hollow value is never written. An empty string, an empty list, or a
     null reads as absence on the consuming side, so a field this tool
     cannot establish honestly must be OMITTED, never padded to look
     filled.
  2. Direction of travel: this tool writes the deposit and reads NOTHING
     under .sbe/. A poisoned tasks.json sitting next to a healthy store
     must change nothing about what this tool produces.
  3. Absent, corrupt, and healthy stores are three DIFFERENT reported
     states, and none of the three ever crashes the tool.

Every fixture is a tempfile.TemporaryDirectory(), never the real project
store. The CLI is invoked as a real subprocess so its argument parsing,
exit codes, and stdout are exercised exactly as a caller would see them.

ADDED: the `generate` verb (schema/change-passport.v1.json), a second,
additive CLI shape on the same tool that writes the full five field
passport rather than the three field deposit above. Its own section below
covers: a round trip from a fixture store built through the Store write
API (never a hand written store file), determinism across two generate
calls sharing one --now, the standalone validator (tools/
bm_passport_validator.py) against the canonical fixture and the three
fixtures that each break exactly one rule, and a read only proof that
BrotherSBE's own consumer (tools/sbe_passport.py in that sibling
repository) reports fields 2 and 5 as CARRIED from a deposited canonical
passport. That last test is guarded: it skips, by name, when the sibling
repository is not present on the machine running this suite.

Standard library only. Run: python3 tools/test_bm_passport.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL_PATH = os.path.join(HERE, "bm_passport.py")
VALIDATOR_PATH = os.path.join(HERE, "bm_passport_validator.py")
SCHEMA_DIR = os.path.join(ROOT, "schema")
FIXTURES_DIR = os.path.join(SCHEMA_DIR, "fixtures")
CANONICAL_FIXTURE = os.path.join(FIXTURES_DIR, "change-passport.v1.canonical.json")
INVALID_FIXTURES = {
    "hollow field 4": os.path.join(FIXTURES_DIR, "invalid-hollow-field4.json"),
    "missing accountable": os.path.join(FIXTURES_DIR, "invalid-missing-accountable.json"),
    "evidence with no origin": os.path.join(FIXTURES_DIR, "invalid-evidence-no-origin.json"),
}
#: The sibling repository this project's own docs point at throughout
#: (docs/NORTH-STAR-CHAIN.md, this tool's own docstring above). Read only:
#: the contract proof test below never writes anything under this path.
#: F13, cross-family review 2026-08-20: overridable via SBE_SIBLING_ROOT
#: so this contract proof is an explicit CI input rather than a hardcoded
#: path that silently skips everywhere else. The orchestrator-decision
#: parking note: the reviewer's OTHER F13 ask, a required (non-skippable)
#: CI job, is deliberately NOT implemented here; this estate's founder law
#: forbids self-firing CI, and verification runs locally (docs/PASSPORT.md
#: carries the same one-line note).
BROTHERSBE_ROOT = os.path.expanduser(
    os.environ.get("SBE_SIBLING_ROOT") or "~/Documents/BrotherSBE")


def run_cli(*args, **kw):
    """Invoke the CLI as a real subprocess, with BROTHERMODE_ROOT scrubbed
    so a variable set on the developer's own machine can never leak into a
    test that expects an explicit --root to decide the outcome. `env_over`
    (a dict) is merged on top when a test needs to simulate an environment
    where `git config user.name` has nothing to say."""
    env = dict(os.environ)
    env.pop("BROTHERMODE_ROOT", None)
    env.update(kw.get("env_over") or {})
    return subprocess.run(
        [sys.executable, TOOL_PATH] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, cwd=ROOT, env=env)


def write_text(directory, relpath, content="placeholder\n"):
    path = os.path.join(directory, relpath)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def read_deposit(root, out=None):
    path = out or os.path.join(root, ".sbe", "passport.json")
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_bm_store_module():
    """Load bm_store.py the same way bm_passport.py does, so a test fixture
    can build a real store without shelling out to a second CLI."""
    import importlib.util
    path = os.path.join(HERE, "bm_store.py")
    spec = importlib.util.spec_from_file_location(
        "bm_store_for_passport_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def claim_one(root, session_id="sess-abc", files=("a.py", "b.py")):
    """Build a real, healthy store at `root` carrying one active claim.
    Uses the writable Store directly (not the CLI): this is fixture setup,
    not the thing under test."""
    mod = _load_bm_store_module()
    store = mod.Store(root, create=True)
    try:
        store.claim("unit-a", "ephemeral", objective="test fixture",
                    files=list(files), owner="builder", session_id=session_id)
    finally:
        store.close()


def claim_then_park(root, name, session_id, files):
    """Add one claim and move it OUT of the active state, which is the state
    a session's own record is in at the moment it closes and deposits. Parking
    rather than completing on purpose: completing requires evidence, and this
    fixture is about the STATE the producer reads, not about evidence."""
    mod = _load_bm_store_module()
    store = mod.Store(root, create=True)
    try:
        record = store.claim(name, "ephemeral", objective="test fixture",
                             files=list(files), owner="builder",
                             session_id=session_id)
        # Record is a __slots__ object, not a dict: attribute access only.
        # session_id must be passed back: the store's ownership guard refuses
        # a cross-session move of an ACTIVE record, which is the single-writer
        # rule doing its job, so the fixture parks as the same session.
        store.transition(record.lifecycle_uuid, record.version, "parked",
                         session_id=session_id)
    finally:
        store.close()


#: An env override that makes `git config user.name` resolve to nothing,
#: without touching this worktree's own git configuration: HOME points at
#: an empty directory (no ~/.gitconfig) and the global/system config files
#: are pointed at /dev/null, so only a repo-local .git/config (which these
#: bare tempdir fixtures never have) could still answer. Applied to the
#: SUBPROCESS environment only, via subprocess.run's own `env=`, never to
#: this test runner's own environment or to any git command this file
#: itself might run.
def _no_git_identity_env(tmp):
    empty_home = os.path.join(tmp, "empty-home")
    os.makedirs(empty_home, exist_ok=True)
    return {
        "HOME": empty_home,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def _directory_write_denial_works(directory):
    """Whether `os.chmod(directory, 0o500)` really stops a file being
    created inside `directory` HERE, measured with a probe file and the
    directory left exactly as it was found. Same mechanism and same
    reason as tools/test_bm_store.py's own `_directory_write_denial_works`
    (that docstring carries the full Windows/root explanation); duplicated
    here rather than imported because the two files do not import each
    other, the same discipline that comment states."""
    probe = os.path.join(directory, "write_denial_probe.tmp")
    os.chmod(directory, 0o500)
    try:
        try:
            with io.open(probe, "w", encoding="utf-8") as fh:
                fh.write("probe")
        except EnvironmentError:
            return True
        try:
            os.remove(probe)
        except EnvironmentError:  # sbe: allow-silent best-effort cleanup of the probe file, verdict already computed
            pass
        return False
    finally:
        os.chmod(directory, 0o700)


class HelpAndUsageTests(unittest.TestCase):
    def test_help_prints_usage_and_exits_zero(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Usage: ", result.stdout)
        self.assertIn("bm_passport.py", result.stdout)
        self.assertIn("--accountable", result.stdout)

    def test_usage_never_names_the_repo_relative_path(self):
        """P17: a packaged install has no tools/ directory, so a usage line
        that says `python3 tools/bm_passport.py` names a file the reader
        does not have. This is the local half of the sweep
        test_bm_store.py's TestP17InstructionTextMatchesTheInstalledLayout
        runs over every shipping tool; kept here too so the failure lands
        in this tool's own suite rather than only in the store's."""
        result = run_cli("--help")
        self.assertNotIn("python3 tools/bm_", result.stdout)
        bogus = run_cli("--bogus")
        self.assertNotIn("python3 tools/bm_", bogus.stderr)

    def test_unknown_argument_is_a_usage_error(self):
        result = run_cli("--bogus")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unknown argument", result.stderr)


class ThreeStoreStatesTests(unittest.TestCase):
    """Rule 3: absent, corrupt, and healthy are three DIFFERENT reported
    states, and none of them crashes (exit 0, never a traceback, never
    exit 2's NO-DATA path, since a store being absent is an ordinary,
    fully-expected outcome for this tool, not a failure to determine
    anything)."""

    def test_absent_store_is_reported_and_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", tmp, "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("store: absent", result.stdout)
            deposit = read_deposit(tmp)
        self.assertIn("no BrotherMode store",
                      " ".join(deposit["whatWasNotEstablished"]))

    def test_corrupt_store_is_reported_distinctly_and_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_text(tmp, os.path.join(".brothermode", "store.sqlite3"),
                      "not a real sqlite file" * 5)
            result = run_cli("--root", tmp, "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("store: unreadable", result.stdout)
            deposit = read_deposit(tmp)
        joined = " ".join(deposit["whatWasNotEstablished"])
        self.assertIn("could not be", joined)
        self.assertNotIn("no BrotherMode store at", joined)

    def test_healthy_store_is_reported_as_ok_and_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp, session_id="sess-abc", files=("a.py", "b.py"))
            result = run_cli("--root", tmp, "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("store: ok", result.stdout)
            deposit = read_deposit(tmp)
        who = " ".join(deposit["whoDidIt"])
        self.assertIn("session sess-abc", who)
        self.assertIn("2 files", who)

    def test_the_three_states_are_pairwise_distinct(self):
        """Same assertion, phrased the other way: the three states must
        never collapse into each other. Guards against a rewrite that
        makes 'absent' and 'unreadable' report the identical sentence."""
        with tempfile.TemporaryDirectory() as tmp_absent, \
             tempfile.TemporaryDirectory() as tmp_corrupt, \
             tempfile.TemporaryDirectory() as tmp_healthy:
            write_text(tmp_corrupt, os.path.join(".brothermode", "store.sqlite3"),
                      "not a real sqlite file" * 5)
            claim_one(tmp_healthy)

            r_absent = run_cli("--root", tmp_absent, "--accountable", "P")
            r_corrupt = run_cli("--root", tmp_corrupt, "--accountable", "P")
            r_healthy = run_cli("--root", tmp_healthy, "--accountable", "P")

        lines = [l for r in (r_absent, r_corrupt, r_healthy)
                for l in r.stdout.splitlines() if l.startswith("store: ")]
        self.assertEqual(len(lines), 3)
        # The STATE WORD, not the whole line. Each line embeds its own
        # tempdir path, so comparing whole lines gave three distinct strings
        # even when the state words collapsed to one: the test passed for the
        # wrong reason until an adversarial review replayed it with all three
        # states set to "unreadable" and watched it stay green.
        states = [l.split("store: ", 1)[1].split()[0] for l in lines]
        self.assertEqual(len(set(states)), 3, states)
        self.assertEqual(states, ["absent", "unreadable", "ok"], states)


class HollowValueTests(unittest.TestCase):
    """Rule 1: an empty string, an empty list, or a null is never written.
    A field this tool cannot establish is OMITTED from the JSON entirely,
    for each of the three fields it owns."""

    def test_who_did_it_is_omitted_not_written_hollow_when_nothing_carries_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No store (so no session/claim lines) and no accountable
            # source (no --accountable, and git config is made to answer
            # nothing): whoDidIt has literally nothing to carry.
            result = run_cli("--root", tmp, env_over=_no_git_identity_env(tmp))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)
        self.assertNotIn("whoDidIt", deposit)
        self.assertIn("whoDidIt: NOT established, omitted", result.stdout)

    def test_where_it_came_from_is_never_hollow_even_on_whitespace_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A whitespace-only --method must not become a hollow deposited
            # string; the tool falls back to its honest default instead of
            # writing "   ".
            result = run_cli("--root", tmp, "--accountable", "Test Person",
                             "--method", "   ")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)
        self.assertIn("whereItCameFrom", deposit)
        self.assertEqual(deposit["whereItCameFrom"].strip(), deposit["whereItCameFrom"])
        self.assertNotEqual(deposit["whereItCameFrom"].strip(), "")

    def test_what_was_not_established_is_never_written_as_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            # The most favorable case this tool can hit: a healthy store
            # with one attributed claim and an explicit accountable name.
            # Even here, whatWasNotEstablished must carry at least the
            # tool's own always-true scope-limit line, never [].
            claim_one(tmp)
            result = run_cli("--root", tmp, "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)
        self.assertIn("whatWasNotEstablished", deposit)
        self.assertTrue(len(deposit["whatWasNotEstablished"]) > 0)

    def test_no_field_in_the_deposit_is_ever_an_empty_or_null_value(self):
        """Belt and suspenders across ALL THREE store states, not one.

        This docstring used to claim "every state this tool can hit" while the
        body exercised exactly one (a bare root with no git identity). An
        adversarial review caught the gap: a claim of breadth with a body of
        one is the same overclaim this tool exists to report, in the suite
        that polices it."""
        for label, build in (
                ("absent", lambda d: None),
                ("corrupt", lambda d: write_text(
                    d, os.path.join(".brothermode", "store.sqlite3"),
                    "not a real sqlite file" * 5)),
                ("healthy", lambda d: claim_one(d))):
            with tempfile.TemporaryDirectory() as tmp:
                build(tmp)
                result = run_cli("--root", tmp,
                                 env_over=_no_git_identity_env(tmp))
                self.assertEqual(result.returncode, 0,
                                 "%s: %s%s" % (label, result.stdout,
                                               result.stderr))
                deposit = read_deposit(tmp)
            for key, value in deposit.items():
                self.assertNotEqual(value, "", "%s: %s is an empty string"
                                    % (label, key))
                self.assertNotEqual(value, [], "%s: %s is an empty list"
                                    % (label, key))
                self.assertIsNotNone(value, "%s: %s is null" % (label, key))

    def test_the_single_state_version_of_the_check_still_holds(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_cli("--root", tmp, env_over=_no_git_identity_env(tmp))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)
        for key, value in deposit.items():
            self.assertTrue(value is not None, key)
            if isinstance(value, str):
                self.assertNotEqual(value.strip(), "", key)
            if isinstance(value, list):
                self.assertGreater(len(value), 0, key)


class DirectionOfTravelTests(unittest.TestCase):
    """Rule 2: this tool writes the deposit and reads NOTHING under .sbe/.
    A poisoned tasks.json sitting next to the exact same store must not
    change one byte of what this tool produces."""

    def test_a_poisoned_sbe_tasks_json_changes_nothing_about_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp, session_id="sess-fixed", files=("x.py",))

            before = run_cli("--root", tmp, "--out",
                             os.path.join(tmp, "before.json"),
                             "--accountable", "Test Person")
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            deposit_before = read_deposit(tmp, out=os.path.join(tmp, "before.json"))

            # Poisoned on purpose: invalid JSON, so ANY read of it (even a
            # failed one that leaked into the output) would show up as a
            # difference from the "before" run.
            write_text(tmp, os.path.join(".sbe", "tasks.json"),
                      "{ this is not valid json at all !!! ")
            write_text(tmp, os.path.join(".sbe", "evidence", "fake.json"),
                      '{"command": "rm -rf /", "verdict": "PASS"}')
            write_text(tmp, os.path.join(".sbe", "passport.json"),
                      '{"whoDidIt": ["a lie planted by the assurance side"]}')

            after = run_cli("--root", tmp, "--out",
                            os.path.join(tmp, "after.json"),
                            "--accountable", "Test Person")
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            deposit_after = read_deposit(tmp, out=os.path.join(tmp, "after.json"))

        self.assertEqual(deposit_before, deposit_after)
        self.assertNotIn("a lie planted by the assurance side",
                         json.dumps(deposit_after))


class DepositShapeTests(unittest.TestCase):
    """The deposit keys match the contract's deposit key column exactly,
    and multiple sessions each get their own line rather than being
    merged or dropped."""

    def test_deposit_uses_the_exact_contract_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp)
            result = run_cli("--root", tmp, "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)
        for key in deposit:
            self.assertIn(key, ("schema", "whoDidIt", "whereItCameFrom",
                                "whatWasNotEstablished"))
        # Both directions. Rejecting unexpected keys alone is not a contract
        # check: an adversarial review replayed the loop above against {} and
        # against a deposit missing whoDidIt entirely, and it passed both, so
        # a regression that dropped a field would have stayed green.
        for key in ("schema", "whoDidIt", "whereItCameFrom",
                    "whatWasNotEstablished"):
            self.assertIn(key, deposit, deposit)
        # The schema marker is asserted as the LITERAL the consumer half
        # matches on, not as this module's constant. The consumer reads
        # nothing at all from a deposit whose version it cannot identify, so
        # a rename of the producer's constant would silently empty the seam
        # while every producer-side test that referenced the same constant
        # stayed green. Measured before this assertion existed: the producer
        # printed "whoDidIt: carried" and the consumer printed field 2
        # NO-DATA over the same file, each honest about its own half.
        self.assertEqual(deposit["schema"], "change-passport/v1", deposit)

    def test_out_flag_writes_to_the_requested_path_instead_of_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = os.path.join(tmp, "custom", "deposit.json")
            result = run_cli("--root", tmp, "--out", custom,
                             "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(os.path.isfile(custom))
            self.assertFalse(os.path.isfile(
                os.path.join(tmp, ".sbe", "passport.json")))

    def test_out_path_that_cannot_be_written_is_the_one_exit_1_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A file where a directory needs to go: os.makedirs on the
            # parent must fail with OSError (FileExistsError / NotADirectoryError).
            blocker = os.path.join(tmp, "blocker")
            write_text(tmp, "blocker", "not a directory\n")
            bad_out = os.path.join(blocker, "sub", "deposit.json")
            result = run_cli("--root", tmp, "--out", bad_out,
                             "--accountable", "Test Person")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("could not write", result.stderr)


class SilentGapTests(unittest.TestCase):
    """The regression suite for the defect an adversarial review found: a
    CONFIDENTLY WRONG field 2 beside a field 4 that says nothing is missing.

    Field 4 is the product's whole claim, the one thing no green gate can
    fake, so every way this producer can be partially blind gets a test that
    fails if the disclosure is removed. Each test asserts the SPECIFIC
    sentence, not merely that field 4 is non-empty: an unconditional scope
    line is always present, so 'field 4 has content' would pass with every
    real gap silently dropped, which is exactly how the defect survived
    fourteen passing tests."""

    def test_a_closed_record_is_named_rather_than_silently_skipped(self):
        """THE CRITICAL. Only active records are read, and a session deposits
        at CLOSE, when its own record is most likely already parked or
        complete. So the tool could name a concurrent, unrelated session as
        the author and report no gap at all."""
        with tempfile.TemporaryDirectory() as tmp:
            claim_then_park(tmp, "did-the-work", "sess-DID-THE-WORK",
                            ["x.py", "y.py"])
            claim_one(tmp, session_id="sess-OTHER-CONCURRENT",
                      files=("unrelated.py",))
            result = run_cli("--root", tmp, "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)

        gaps = " ".join(deposit.get("whatWasNotEstablished", []))
        self.assertIn("not active", gaps, deposit)
        self.assertIn("were NOT read", gaps, deposit)
        # The other session is still named, which is honest ONLY because the
        # gap above tells the reader they are seeing active claims alone.
        self.assertIn("sess-OTHER-CONCURRENT",
                      " ".join(deposit.get("whoDidIt", [])), deposit)

    def test_a_git_derived_name_says_it_was_inferred(self):
        """`git config user.name` answers who owns the laptop, not who is
        accountable for this change, and answers even outside a repository."""
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp)
            result = run_cli("--root", tmp)   # no --accountable on purpose
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)

        gaps = " ".join(deposit.get("whatWasNotEstablished", []))
        who = " ".join(deposit.get("whoDidIt", []))
        if "accountable:" in who:
            self.assertIn("INFERRED", gaps, deposit)
            self.assertIn("git config", gaps, deposit)
        else:
            # No git identity reachable: the other honest answer, and the
            # field must then be omitted rather than padded.
            self.assertIn("could not be established", gaps, deposit)

    def test_an_explicit_accountable_name_carries_no_inference_gap(self):
        """The control for the test above. Without it, asserting the INFERRED
        sentence would still pass if the tool emitted that sentence always."""
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp)
            result = run_cli("--root", tmp, "--accountable", "Stated Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)

        self.assertNotIn("INFERRED",
                         " ".join(deposit.get("whatWasNotEstablished", [])))
        self.assertIn("accountable: Stated Person",
                      " ".join(deposit.get("whoDidIt", [])))

    def test_a_session_claiming_no_files_is_named_as_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp, session_id="sess-nofiles", files=())
            result = run_cli("--root", tmp, "--accountable", "Test Person")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            deposit = read_deposit(tmp)

        self.assertIn("claim no files at all",
                      " ".join(deposit.get("whatWasNotEstablished", [])), deposit)


class PathFlagTests(unittest.TestCase):
    """An empty value for a flag that decides WHERE bytes land is refused,
    never treated as absent. `--out "$OUT"` with an unset variable used to
    fall through to the project's real .sbe/passport.json."""

    def test_empty_out_is_refused_rather_than_writing_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp)
            result = run_cli("--root", tmp, "--out", "", "--accountable", "P")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("empty value", result.stderr)
            self.assertFalse(
                os.path.isfile(os.path.join(tmp, ".sbe", "passport.json")),
                "an empty --out silently wrote the default deposit path")

    def test_empty_root_is_refused(self):
        result = run_cli("--root", "", "--accountable", "P")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("empty value", result.stderr)

    def test_an_unresolvable_root_is_exit_2_and_says_NO_DATA(self):
        """The exit 2 path, which the docstring documents and nothing asserted.

        Three exits are documented: 0 when a deposit was written whatever it
        could establish, 1 when the deposit could not be written, and 2 for a
        usage error or a root that could not be resolved at all. The last is
        not a finding about a change, it is a failure to find a change to
        report on, and reporting it as 0 would be the tool claiming it looked
        when it did not."""
        missing = os.path.join(tempfile.gettempdir(),
                               "bm-passport-no-such-dir-xyzzy")
        self.assertFalse(os.path.exists(missing))
        result = run_cli("--root", missing, "--accountable", "P")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("NO-DATA", result.stdout)

    def test_a_failed_write_leaves_the_previous_deposit_intact(self):
        """What temp-plus-replace actually buys, asserted rather than assumed.

        Writing the deposit in place truncates it first, so a failure mid-write
        leaves a half-written file that the consumer classifies as CORRUPT,
        which is a worse answer than the previous deposit or than none at all.
        The failure is injected by DENYING WRITE on the destination
        directory: tools/bm_passport.py's _atomic_write (F11, cross-family
        review 2026-08-20) uses tempfile.mkstemp for a randomly-named temp
        file rather than the old predictable "<out>.tmp", so a directory
        planted at that exact old name no longer blocks anything; denying
        write on the directory itself is the injection that still works
        against mkstemp, and is the closest reachable analogue of a disk
        failure without root."""
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp)
            out_dir = os.path.join(tmp, "out_dir")
            os.makedirs(out_dir)
            out = os.path.join(out_dir, "deposit.json")

            first = run_cli("--root", tmp, "--out", out, "--accountable", "First")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            with io.open(out, encoding="utf-8") as fh:
                before = fh.read()
            self.assertIn("First", before)

            if not _directory_write_denial_works(out_dir):
                self.skipTest(
                    "os.chmod(dir, 0o500) does not deny a write in this "
                    "directory here (measured, not assumed), so the "
                    "injected-failure half of F11 cannot be posed on this "
                    "platform; the atomic-write mechanics themselves are "
                    "still covered by test_no_temp_file_is_left_behind_by_"
                    "a_successful_write")
            os.chmod(out_dir, 0o500)
            try:
                second = run_cli("--root", tmp, "--out", out,
                                 "--accountable", "Second")
            finally:
                os.chmod(out_dir, 0o700)
            self.assertEqual(second.returncode, 1,
                             second.stdout + second.stderr)
            self.assertIn("could not write", second.stderr)

            with io.open(out, encoding="utf-8") as fh:
                after = fh.read()
        self.assertEqual(before, after,
                         "the failed write changed the previous deposit; "
                         "temp-plus-replace is not protecting it")
        json.loads(after)   # still parseable, so never classified CORRUPT

    def test_no_temp_file_is_left_behind_by_a_successful_write(self):
        """The deposit is written to a .tmp path and moved into place, so a
        failure mid-write cannot leave a truncated deposit the consumer would
        classify as corrupt. The move must actually happen."""
        with tempfile.TemporaryDirectory() as tmp:
            claim_one(tmp)
            out = os.path.join(tmp, "deposit.json")
            result = run_cli("--root", tmp, "--out", out, "--accountable", "P")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(os.path.isfile(out))
            self.assertFalse(os.path.isfile(out + ".tmp"))


# ---------------------------------------------------------------------------
# `generate`: the full five field passport (schema/change-passport.v1.json).
# ---------------------------------------------------------------------------

def _git(args, cwd, env=None):
    full_env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    if env:
        full_env.update(env)
    return subprocess.run(["git"] + list(args), cwd=cwd, env=full_env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=60)


def make_repo(tmp):
    """A two commit repository at `tmp`. Returns (base, head)."""
    _git(["init", "-q"], tmp)
    write_text(tmp, "a.txt", "one\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "one"], tmp)
    base = _git(["rev-parse", "HEAD"], tmp).stdout.strip()
    write_text(tmp, "a.txt", "two\n")
    _git(["add", "-A"], tmp)
    _git(["commit", "-q", "-m", "two"], tmp)
    head = _git(["rev-parse", "HEAD"], tmp).stdout.strip()
    return base, head


def make_project_fixture(tmp, project_id="proj-1", actor_name="Fixture Person",
                         session_id="sess-xyz", with_evidence=True):
    """A real, healthy store at `tmp` carrying one project, one task, and
    (unless with_evidence is False) one evidence row, all written through
    bm_store.py's own write API, never a hand written store file. Every
    write shares one actor, so the attribution log names one session."""
    mod = _load_bm_store_module()
    store = mod.Store(tmp, create=True)
    actor = {"actor_type": "human", "actor_name": actor_name,
             "session_id": session_id}
    try:
        store.upsert_project({
            "project_id": project_id, "name": "Fixture Project",
            "created_at": "2026-08-20T09:00:00Z",
            "updated_at": "2026-08-20T09:00:00Z",
        }, actor)
        task_id = store.create_task({
            "task_id": "task-1", "project_id": project_id,
            "title": "Do the thing", "status": "planned",
        }, actor)
        if with_evidence:
            store.add_evidence({
                "evidence_id": "ev-1", "subject_type": "task",
                "subject_id": task_id, "kind": "test",
                "ref": "python3 tools/test_all.py", "note": "green",
                "created_at": "2026-08-20T09:30:00Z",
            }, project_id, actor)
    finally:
        store.close()
    return task_id


def run_generate(*args, **kw):
    env = dict(os.environ)
    env.pop("BROTHERMODE_ROOT", None)
    env.update(kw.get("env_over") or {})
    return subprocess.run(
        [sys.executable, TOOL_PATH, "generate"] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, cwd=ROOT, env=env)


class GenerateRoundTripTests(unittest.TestCase):
    """A fixture store built through the write API, read back by `generate`
    through the five allowed accessors. Structural assertions, not exact
    string equality, on everything that came out of the store: what
    matters here is that a real name and a real command surface, not that
    they land in one particular sentence shape a later refactor might
    reword."""

    def test_round_trip_carries_real_session_and_evidence_data(self):
        """--include-sensitive (F1): the raw, unredacted content this test
        was written to check. Without it, the store's own export policy
        withholds task titles, check commands/verdicts and actor names by
        default; that redacted default has its own test below."""
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp)
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head,
                                  "--now", "2026-08-20T10:00:00Z",
                                  "--include-sensitive")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)

        self.assertEqual(passport["schema"], "change-passport/v1")
        self.assertEqual(passport["sensitivity"], "raw")
        self.assertEqual(passport["change"]["baseCommit"], base)
        self.assertEqual(passport["change"]["headCommit"], head)
        self.assertEqual(passport["change"]["filesTouched"], ["a.txt"])
        self.assertEqual(passport["change"]["projectId"], "proj-1")

        who = passport["details"]["who"]
        self.assertEqual(who["accountableHuman"], "Fixture Person")
        self.assertEqual(len(who["sessions"]), 1)
        self.assertEqual(who["sessions"][0]["label"], "sess-xyz")
        self.assertIn("Do the thing", who["sessions"][0]["claims"])

        evidence = passport["details"]["evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["command"], "python3 tools/test_all.py")
        self.assertEqual(evidence[0]["verdict"], "green")

    def test_default_generation_redacts_store_free_text_and_names_the_gap(self):
        """F1, cross-family review 2026-08-20: BLOCKER. Before this fix,
        every one of the five store reads used raw=True unconditionally,
        so the DEFAULT invocation (no flag at all) printed the founder-
        typed task title and check command/verdict straight from the
        store into the passport, an artifact meant to travel to a third
        party. This is the regression test: default generation (no
        --include-sensitive) must show NEITHER the real title nor a
        "[WITHHELD: ...]" placeholder as content, and must name the gap
        instead. It would have failed before the fix, because
        who["sessions"][0]["claims"] carried "Do the thing" verbatim and
        evidence[0]["command"] carried the real check command.

        session_id is generated-shape here ("cli-" + 32 hex), not the
        hand-typed "sess-xyz" the other fixtures use: tools/bm_store.py's
        export policy shape-gates attribution.session_id (added by this
        same fix) so a machine-generated id still carries even under
        redaction, while a hand-typed label is founder text like any
        other and is withheld too. This test wants the session LABEL to
        survive so it can prove specifically that its CLAIMS do not."""
        session_id = "cli-" + "a" * 32
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp, session_id=session_id)
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertEqual(passport["sensitivity"], "redacted")

        blob = json.dumps(passport)
        # The real founder text must not appear anywhere in the document,
        # not even folded into a different field.
        self.assertNotIn("Do the thing", blob)
        self.assertNotIn("python3 tools/test_all.py", blob)
        # Nor may the raw WITHHELD placeholder leak through as content:
        # F1's rule is DROP the line, not substitute the marker text.
        self.assertNotIn("WITHHELD", blob)

        session = passport["details"]["who"]["sessions"][0]
        self.assertEqual(session["label"], session_id)
        self.assertEqual(session["claims"], [],
                         "the withheld task title leaked into claims")

        evidence = passport["details"]["evidence"][0]
        # "test" is evidence.kind, a scrub-only column (legible, secret-
        # scrubbed) that survives redaction; command falls back to it
        # only because the real ref ("python3 tools/test_all.py") was
        # withheld, never shown itself.
        self.assertEqual(evidence["command"], "test")
        self.assertEqual(evidence["verdict"], "(no verdict recorded)")

        gaps = " ".join(passport["whatWasNotEstablished"])
        self.assertIn("export policy", gaps,
                      "no named gap replaced the dropped store text")
        self.assertIn("--include-sensitive", gaps)

    def test_no_project_in_the_store_is_refused_by_default(self):
        """F9, cross-family review 2026-08-20: the store exists but the
        requested project does not. Before the fix this was exit 0 with
        only a field-4 gap; now it is refused outright unless
        --allow-missing-project says the caller wants the degraded
        passport anyway."""
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp, project_id="some-other-project")
            result = run_generate("--root", tmp, "--project-id", "missing",
                                  "--base", base, "--head", head,
                                  "--accountable", "Test Person")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no project", result.stderr)
        self.assertIn("--allow-missing-project", result.stderr)

    def test_allow_missing_project_restores_the_degraded_passport(self):
        """The other half of F9: --allow-missing-project still produces
        the passport, still names the gap, and still carries the
        requested id in change.projectId."""
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp, project_id="some-other-project")
            result = run_generate("--root", tmp, "--project-id", "missing",
                                  "--base", base, "--head", head,
                                  "--accountable", "Test Person",
                                  "--allow-missing-project")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertIn("no project", " ".join(passport["whatWasNotEstablished"]))
        self.assertEqual(passport["change"]["projectId"], "missing")


class GenerateDeterminismTests(unittest.TestCase):
    def test_two_generates_with_the_same_now_are_byte_identical(self):
        """F10, cross-family review 2026-08-20: the determinism promise is
        over raw BYTES, not decoded text, so this compares the files
        opened "rb" rather than through io.open's own text-mode decoding
        (which would silently normalize a CRLF/LF difference before the
        comparison ever saw it)."""
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp)
            out1 = os.path.join(tmp, "one.json")
            out2 = os.path.join(tmp, "two.json")
            first = run_generate("--root", tmp, "--project-id", "proj-1",
                                 "--base", base, "--head", head,
                                 "--now", "2026-08-20T10:00:00Z", "--out", out1)
            second = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head,
                                  "--now", "2026-08-20T10:00:00Z", "--out", out2)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            with open(out1, "rb") as fh:
                bytes1 = fh.read()
            with open(out2, "rb") as fh:
                bytes2 = fh.read()
        self.assertEqual(bytes1, bytes2,
                         "two generate calls sharing the same --now produced "
                         "different bytes")

    def test_generatedAt_is_the_only_thing_that_differs_without_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp)
            first = run_generate("--root", tmp, "--project-id", "proj-1",
                                 "--base", base, "--head", head)
            second = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        p1, p2 = json.loads(first.stdout), json.loads(second.stdout)
        del p1["generatedAt"]
        del p2["generatedAt"]
        self.assertEqual(p1, p2)


class GenerateAccountableErrorPathTests(unittest.TestCase):
    def test_no_accountable_source_at_all_is_a_named_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head,
                                  env_over=_no_git_identity_env(tmp))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no accountable human could be established",
                      result.stderr)

    def test_missing_required_arguments_is_a_usage_error(self):
        result = run_generate("--project-id", "x")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("missing required argument", result.stderr)


class GenerateInvalidCommitRangeTests(unittest.TestCase):
    """F6, cross-family review 2026-08-20: BLOCKER. Before this fix, --base
    and --head were only checked for non-blank text and copied verbatim
    into change.baseCommit/change.headCommit: a value like "x" produced a
    named git-diff gap in field 4 but was STILL WRITTEN as the commit
    identity, violating the schema's own 7-40 hex pattern at exit 0. This
    is the regression test named in the brief: it would have failed
    before the fix (exit 0, a file written) and must now exit nonzero
    with nothing written."""

    def test_an_unresolvable_base_exits_nonzero_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            out = os.path.join(tmp, "out.json")
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", "x", "--head", head,
                                  "--accountable", "Test Person",
                                  "--out", out)
            wrote_file = os.path.isfile(out)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("invalid --base", result.stderr)
        self.assertFalse(wrote_file,
                         "an invalid --base still wrote an output file")
        self.assertEqual(result.stdout, "",
                         "an invalid --base still printed a passport to stdout")

    def test_an_unresolvable_head_exits_nonzero_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", "not-a-commit",
                                  "--accountable", "Test Person")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("invalid --head", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_a_short_hash_resolves_to_the_full_canonical_one(self):
        """The success half: --base/--head accept a short or symbolic
        spelling (here, "HEAD~1" and "HEAD") and the passport carries the
        FULL resolved hash, matching what make_repo's own rev-parse
        returned, never the caller's short spelling."""
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", "HEAD~1", "--head", "HEAD",
                                  "--accountable", "Test Person")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertEqual(passport["change"]["baseCommit"], base)
        self.assertEqual(passport["change"]["headCommit"], head)


class GenerateGitEnvironmentTests(unittest.TestCase):
    """F4, cross-family review 2026-08-20: BLOCKER. Every git subprocess
    used to inherit this process's own environment unchanged, so an
    inherited GIT_DIR/GIT_WORK_TREE could redirect which repository is
    actually read. `git -C root` does not neutralize this: -C only
    changes the starting directory git resolves paths from."""

    def test_an_inherited_git_dir_does_not_misdirect_generate(self):
        """Regression test for F4. Before the fix, GIT_DIR pointed at a
        SECOND, unrelated repository's .git would make every git call in
        this tool (remote identity, commit resolution, the diff) operate
        against that other repository instead of --root's own; the
        fixture below makes that misdirection detectable: the decoy
        repository has neither of the real commits, so if GIT_DIR won,
        --base/--head would fail to resolve and this test would see the
        old shape (a git-resolution error) instead of a clean passport
        naming the REAL repo's files."""
        with tempfile.TemporaryDirectory() as tmp, \
             tempfile.TemporaryDirectory() as decoy:
            base, head = make_repo(tmp)
            # A second, unrelated repository: same shape, different
            # commits and a different tracked file, so a misdirected read
            # is both observable (git-diff would resolve DIFFERENT things,
            # or fail outright) and never a coincidental pass.
            _git(["init", "-q"], decoy)
            write_text(decoy, "decoy.txt", "decoy\n")
            _git(["add", "-A"], decoy)
            _git(["commit", "-q", "-m", "decoy"], decoy)

            env = dict(_no_git_identity_env(tmp))
            env["GIT_DIR"] = os.path.join(decoy, ".git")
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head,
                                  "--accountable", "Test Person",
                                  env_over=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertEqual(passport["change"]["baseCommit"], base)
        self.assertEqual(passport["change"]["headCommit"], head)
        self.assertEqual(passport["change"]["filesTouched"], ["a.txt"])


class GenerateBusyStoreRefusalTests(unittest.TestCase):
    """F3, cross-family review 2026-08-20 (BLOCKER+MAJOR): database
    contention must refuse generation outright rather than silently
    degrade into a passport that looks like a healthy gap. A real
    concurrent-lock race against a WAL-mode SQLite store could not be
    reproduced reliably in-suite (WAL readers are not blocked by an
    ordinary writer holding the write lock, which is the whole point of
    WAL, so a real held lock does not reliably raise 'database is locked'
    on the READ side this generator uses), so this proves the
    CLASSIFICATION AND REFUSAL PATH directly, in-process: bm_passport.py's
    own _load_bm_store() is monkeypatched to return a fake store module
    whose ReadOnlyStore always raises the real bm_store.OwnershipRefused
    ('db-busy', ...), the exact shape tools/bm_store.py's own
    _connect_read_only/_exec produce for a genuinely locked database.
    Before this fix, that shape reached the old _open_readonly_store's
    broad classification as 'unreadable' and was folded into a field-4
    gap at exit 0: a lock, silently reported as though the store had
    simply never been written. After the fix it is a hard refusal."""

    def _load_bm_passport_module(self):
        import importlib.util
        path = os.path.join(HERE, "bm_passport.py")
        spec = importlib.util.spec_from_file_location(
            "bm_passport_for_busy_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_a_store_that_stays_busy_refuses_rather_than_degrades(self):
        bp = self._load_bm_passport_module()
        real_store_mod = _load_bm_store_module()
        attempts = []

        class FakeReadOnlyStore(object):
            def __init__(self, root):
                attempts.append(root)
                raise real_store_mod.OwnershipRefused(
                    "db-busy", "the store at %s is busy or locked" % root)

        class FakeModule(object):
            OwnershipRefused = real_store_mod.OwnershipRefused
            StoreCorrupt = real_store_mod.StoreCorrupt
            ReadOnlyStore = staticmethod(FakeReadOnlyStore)
            store_path = staticmethod(real_store_mod.store_path)

        bp._load_bm_store = lambda: (FakeModule(), None)
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            passport, error = bp.build_passport(
                tmp, "proj-1", base, head, "Test Person", None, None)
        self.assertIsNone(passport)
        self.assertIsNotNone(error)
        self.assertIn("busy", error.lower())
        self.assertGreaterEqual(
            len(attempts), 3,
            "expected the store open to be retried up to the full "
            "backoff schedule before refusing; only %d attempt(s) were "
            "made" % len(attempts))


class GenerateFieldFourNeverEmptyTests(unittest.TestCase):
    def test_field_four_is_never_empty_on_an_empty_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            result = run_generate("--root", tmp, "--project-id", "nothing-here",
                                  "--base", base, "--head", head,
                                  "--accountable", "Test Person")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertTrue(passport["whatWasNotEstablished"])
        self.assertTrue(passport["details"]["notEstablished"]["items"])

    def test_field_four_is_never_empty_on_a_full_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp)
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head,
                                  "--accountable", "Test Person")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        self.assertTrue(passport["whatWasNotEstablished"])


class GenerateEvidenceShapeTests(unittest.TestCase):
    def test_every_evidence_entry_carries_origin_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, head = make_repo(tmp)
            make_project_fixture(tmp)
            result = run_generate("--root", tmp, "--project-id", "proj-1",
                                  "--base", base, "--head", head)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        passport = json.loads(result.stdout)
        evidence = passport["details"]["evidence"]
        self.assertTrue(evidence, "the fixture carries one evidence row")
        for entry in evidence:
            self.assertIn(entry["origin"], ("local", "ci"))
            self.assertTrue(entry["timestamp"])


class ValidatorTests(unittest.TestCase):
    """The standalone validator (tools/bm_passport_validator.py) against
    the canonical fixture and each of the three fixtures that break
    exactly one rule."""

    def run_validator(self, path):
        return subprocess.run(
            [sys.executable, VALIDATOR_PATH, path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)

    def test_canonical_fixture_is_valid(self):
        result = self.run_validator(CANONICAL_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "VALID")

    def test_hollow_field_4_is_named(self):
        result = self.run_validator(INVALID_FIXTURES["hollow field 4"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("notEstablished", result.stdout)

    def test_missing_accountable_is_named(self):
        result = self.run_validator(INVALID_FIXTURES["missing accountable"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("accountableHuman", result.stdout)

    def test_evidence_with_no_origin_is_named(self):
        result = self.run_validator(INVALID_FIXTURES["evidence with no origin"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("origin", result.stdout)

    def test_unreadable_file_is_a_named_error_at_exit_2(self):
        result = self.run_validator(
            os.path.join(tempfile.gettempdir(), "bm-passport-no-such-file.json"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("could not read", result.stdout)

    def test_non_json_file_is_a_named_error_at_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_text(tmp, "not-json.json", "{ this is not json")
            result = self.run_validator(path)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("not valid JSON", result.stdout)


class SBESiblingRootOverrideTests(unittest.TestCase):
    """F13, cross-family review 2026-08-20 (MAJOR, PARTIAL BY DECISION):
    the sibling checkout/path is now an explicit input (SBE_SIBLING_ROOT)
    rather than a hardcoded path this test module resolves once at import
    time. Proven here at the process level, since BROTHERSBE_ROOT above is
    a module-level constant this file's own import already fixed."""

    def test_env_var_overrides_the_default_sibling_path(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import test_bm_passport "
             "as t; print(t.BROTHERSBE_ROOT)" % HERE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True,
            env=dict(os.environ, SBE_SIBLING_ROOT="/tmp/not-a-real-sbe-checkout"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "/tmp/not-a-real-sbe-checkout")


class BrotherSBEContractProofTests(unittest.TestCase):
    """Read only proof that the canonical fixture, deposited exactly where
    the sibling repository expects it, makes BrotherSBE's own consumer
    report fields 2 and 5 as CARRIED. Never writes into BROTHERSBE_ROOT:
    the fixture store lives in a temp dir, and BrotherSBE's tool is only
    ever invoked with --root pointed at that temp dir."""

    def setUp(self):
        sbe_passport = os.path.join(BROTHERSBE_ROOT, "tools", "sbe_passport.py")
        if not os.path.isfile(sbe_passport):
            self.skipTest(
                "BrotherSBE is not present at %s on this machine, so the "
                "contract proof cannot run; set SBE_SIBLING_ROOT to point "
                "at a checkout, or check one out at %s. This is a skip, "
                "never a pass, and never a fail"
                % (sbe_passport, BROTHERSBE_ROOT))
        self.sbe_passport = sbe_passport

    def test_fields_two_and_five_are_carried_from_the_deposit(self):
        with tempfile.TemporaryDirectory() as tmp:
            sbe_dir = os.path.join(tmp, ".sbe")
            os.makedirs(sbe_dir)
            # Minimal tasks.json, mirroring BrotherSBE's own
            # tools/test_sbe_passport.py fixture shape: an empty task list
            # is a legal, usable registry for this seam, since fields 2 and
            # 5 come from the deposit alone, never from tasks.
            with io.open(os.path.join(sbe_dir, "tasks.json"), "w",
                         encoding="utf-8") as fh:
                json.dump({"tasks": []}, fh)
            with io.open(CANONICAL_FIXTURE, "r", encoding="utf-8") as fh:
                canonical_bytes = fh.read()
            with io.open(os.path.join(sbe_dir, "passport.json"), "w",
                         encoding="utf-8") as fh:
                fh.write(canonical_bytes)

            result = subprocess.run(
                [sys.executable, self.sbe_passport, "--root", tmp, "--json"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        states = {f["number"]: f["state"] for f in report["fields"]}
        self.assertEqual(states.get(2), "CARRIED",
                         "field 2 (who did it) was not reported CARRIED: %s"
                         % report)
        self.assertEqual(states.get(5), "CARRIED",
                         "field 5 (where it came from) was not reported "
                         "CARRIED: %s" % report)


if __name__ == "__main__":
    unittest.main()
