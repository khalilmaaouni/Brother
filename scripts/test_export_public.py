"""Driven-backwards tests for the allowlist exporter, docs/plan/HUB-
MIGRATION-PLAN-2026-08-30.md step 4.

Every private term used here is FAKE, per this estate's own rule for these
scanners' test fixtures (see scripts/test_private_terms_scan.py's own
docstring): a term real enough to matter, committed alongside the tool that
looks for it, publishes exactly what the tool exists to stop.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_public as EP  # noqa: E402
import edition_guard as EG  # noqa: E402

REAL_CLEANSE = os.path.join(EP.ROOT, "scripts", "cleanse.sh")
EXPORTER_CLI = os.path.join(EP.ROOT, "scripts", "export_public.py")
#: A real product checksums.sh, borrowed as the fixture's own generator so
#: TheExportedProductManifestDescribesTheExportedBytes below runs the exact
#: script production uses, never a test-only stand-in.
REAL_PRODUCT_CHECKSUMS_SH = os.path.join(
    EP.ROOT, "products", "brothersbe", "scripts", "checksums.sh")
#: Its sibling verifier, borrowed for the same reason by
#: ATagRefusesAnExportTreeItsOwnProductsCannotVerify below.
REAL_PRODUCT_VERIFY_INSTALL_SH = os.path.join(
    EP.ROOT, "products", "brothersbe", "scripts", "verify-install.sh")


def _make_fake_root(root, files):
    """`files`: {relative_path: content}. Always seeds scripts/cleanse.sh
    with a real copy, so cleanse runs inside the candidate tree exactly as
    it does in production, whenever "scripts" is on the test's allowlist."""
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    shutil.copy2(REAL_CLEANSE, os.path.join(root, "scripts", "cleanse.sh"))
    for rel, content in files.items():
        path = os.path.join(root, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)


def _write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _run_cli(args, env):
    return subprocess.run([sys.executable, EXPORTER_CLI] + args,
                           capture_output=True, text=True, env=env,
                           timeout=60)


def _seed_bare_remote(remote_dir):
    """A local, no-network stand-in for the real public remote: gives
    identity_guard.py a real origin/HEAD to compare against so it can
    actually PASS, without depending on reaching github.com. Same shape
    as TheExportersOwnInvocationPasses's own fixture below."""
    with tempfile.TemporaryDirectory() as seed_dir:
        subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                        check=True)
        subprocess.run(["git", "init", "-q", seed_dir], check=True)
        subprocess.run(["git", "-C", seed_dir, "config", "user.name",
                         "Seed"], check=True)
        subprocess.run(["git", "-C", seed_dir, "config", "user.email",
                         "seed@example.com"], check=True)
        with open(os.path.join(seed_dir, "README.md"), "w",
                  encoding="utf-8") as fh:
            fh.write("seed\n")
        subprocess.run(["git", "-C", seed_dir, "add", "-A"], check=True)
        subprocess.run(["git", "-C", seed_dir, "commit", "-q", "-m", "seed"],
                        check=True)
        subprocess.run(["git", "-C", seed_dir, "branch", "-M", "main"],
                        check=True)
        subprocess.run(["git", "-C", seed_dir, "push", remote_dir, "main"],
                        check=True)


def _gate_exit(stdout, name):
    """The exit code run_gate() printed for gate `name`, from a line like
    'cleanse: exit 0, PASS: ...'. None if that gate's line never printed."""
    m = re.search(r"^%s: exit (\d+)," % re.escape(name), stdout, re.M)
    return int(m.group(1)) if m else None


#: E34's real list, outside every repository. Read at run time only, never
#: copied into a literal here: this source file must not carry what it
#: exists to test the refusal of.
PRIVATE_NAMES_FILE = os.path.expanduser("~/.brothersbe-private-names")


def _real_short_term():
    """The first term of five characters or fewer in the estate's real
    private-term list, in its own stored spelling. None when the list is
    absent or holds no term that short, which the caller must treat as
    NO-DATA and skip, never as "nothing to test"."""
    if not os.path.isfile(PRIVATE_NAMES_FILE):
        return None
    with open(PRIVATE_NAMES_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and len(line) <= 5:
                return line
    return None


class AShortTermsLowercaseSpellingAtTheExportBoundary(unittest.TestCase):
    """E34: a short (<=5 char) client term's LOWERCASE spelling passed the
    gates that matched short terms case-sensitively, and a product test
    file carrying it reached the public repository eight times. Driven
    against the ACTUAL production list (see _real_short_term above), never
    a fake stand-in, because this is the exact real-world shape the fix
    must hold against; the fake-term coverage for the underlying length
    and case rule already lives in test_cleanse.py's CleanseCalibration.

    Runs through export_public.py's own dry-run boundary (never --push),
    mirroring APrivateTermInAnAllowlistedFileRefusesQuotingTheVerdict and
    ThePrivateTermInANonAllowlistedFileNeverAppears above, which is the
    nearest sibling coverage of this same boundary.
    """

    def setUp(self):
        term = _real_short_term()
        if term is None:
            self.skipTest("NO-DATA: no term of 5 characters or fewer in "
                           "~/.brothersbe-private-names")
        self._term = term

    def test_a_lowercase_spelling_in_an_allowlisted_file_is_refused(self):
        lower = self._term.lower()
        with tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "leaky.md": "the record mentions %s in passing\n" % lower,
            })
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "leaky.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), [self._term])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            proc = _run_cli(["--allowlist", allowlist_path, "--root", root,
                              "--dry-run"], env)
            self.assertEqual(proc.returncode, EP.EXIT_REFUSED,
                              proc.stdout + proc.stderr)
            self.assertIn("REFUSED", proc.stdout)

    def test_b_an_ordinary_word_containing_the_letters_is_allowed(self):
        # The false-positive class the whole-word branch exists to avoid:
        # the term's letters glued inside a longer ordinary-looking token,
        # built at run time the same way as the term itself.
        word = "h" + self._term.lower()
        with tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "plain.md": "an ordinary %s appears in this note\n" % word,
            })
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "plain.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), [self._term])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            proc = _run_cli(["--allowlist", allowlist_path, "--root", root,
                              "--dry-run"], env)
            self.assertEqual(proc.returncode, EP.EXIT_OK,
                              proc.stdout + proc.stderr)
            self.assertIn("CLEAR", proc.stdout)


class ThePrivateTermInANonAllowlistedFileNeverAppears(unittest.TestCase):
    """docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 4's first
    driven-backwards case."""

    def test_a_non_allowlisted_directory_is_never_copied(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            _make_fake_root(root, {
                "public.md": "hello, nothing private here\n",
                "secretzone/private.md": "leak: FAKETERM-XYZ lives here\n",
            })
            allowlist = ["scripts", "public.md"]  # secretzone/ never listed
            copied = EP.build_export_tree(dest, allowlist, root=root)
            self.assertIn("public.md", copied)
            self.assertNotIn("secretzone", copied)
            self.assertFalse(os.path.exists(os.path.join(dest, "secretzone")))
            for base, _dirs, names in os.walk(dest):
                for name in names:
                    with open(os.path.join(base, name), encoding="utf-8",
                              errors="ignore") as fh:
                        self.assertNotIn("FAKETERM-XYZ", fh.read())

    def test_the_full_exporter_clears_when_the_term_is_not_allowlisted(self):
        """Not just the copy step: the whole CLI, end to end, must report
        CLEAR when the only place the term lives was never listed."""
        with tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "public.md": "hello, nothing private here\n",
                "secretzone/private.md": "leak: FAKETERM-XYZ lives here\n",
            })
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "public.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-XYZ"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            proc = _run_cli(["--allowlist", allowlist_path, "--root", root,
                              "--dry-run"], env)
            self.assertEqual(proc.returncode, EP.EXIT_OK,
                              proc.stdout + proc.stderr)
            self.assertIn("CLEAR", proc.stdout)
            self.assertNotIn("FAKETERM-XYZ", proc.stdout)


class APrivateTermInAnAllowlistedFileRefusesQuotingTheVerdict(unittest.TestCase):
    """docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 4's second
    driven-backwards case."""

    def test_the_exporter_refuses_and_names_the_gate_that_found_it(self):
        with tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "leaky.md": "our plan mentions FAKETERM-XYZ by name\n",
            })
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "leaky.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-XYZ"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            proc = _run_cli(["--allowlist", allowlist_path, "--root", root,
                              "--dry-run"], env)
            self.assertEqual(proc.returncode, EP.EXIT_REFUSED,
                              proc.stdout + proc.stderr)
            self.assertIn("private_terms_scan", proc.stdout)
            self.assertIn("REFUSED", proc.stdout)
            self.assertIn("REFUSED: at least one gate did not clear",
                          proc.stdout)
            # the finding is named, the term itself never is
            self.assertNotIn("FAKETERM-XYZ", proc.stdout)
            self.assertNotIn("FAKETERM-XYZ", proc.stderr)


class EachGateOfRunGatesIsIndividuallyLoadBearing(unittest.TestCase):
    """run_gates() composes three checks into one all_ok with a plain
    `and`. An external failure-injection drill neutered
    private_terms_scan's contribution (`all_ok and (ok or name ==
    "private_terms_scan")`) and every existing test stayed green, because
    the only candidate test plants a term that cleanse.sh also catches, so
    a redundant sibling silently carried the neutered gate.

    Each case below seeds a violation that exactly ONE of the three gates
    can detect, so the drill's own move (force that one gate's ok to True)
    must flip that one test's assertion, not merely leave the composition
    unaffected because a sibling gate was going to refuse anyway."""

    def test_a_dash_with_no_private_term_refuses_on_cleanse_alone(self):
        """cleanse.sh is the only one of the three that scans for a
        typographic dash; neither identity_guard.py nor
        private_terms_scan.py look for one at all."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            _make_fake_root(root, {
                "dashy.md": "the tree carries no private term \u2014 only "
                            "this one em dash\n",   # the escape keeps the
                            # character out of THIS file's bytes, so the dash
                            # gate cannot refuse the test that proves it works
            })
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "dashy.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            proc = _run_cli(["--allowlist", allowlist_path, "--root", root,
                              "--remote", remote_dir, "--branch", "main",
                              "--dry-run"], env)
            self.assertEqual(proc.returncode, EP.EXIT_REFUSED,
                              proc.stdout + proc.stderr)
            self.assertEqual(_gate_exit(proc.stdout, "cleanse"), 1,
                              proc.stdout)
            self.assertEqual(_gate_exit(proc.stdout, "identity_guard"), 0,
                              proc.stdout)
            self.assertEqual(_gate_exit(proc.stdout, "private_terms_scan"),
                              0, proc.stdout)

    def test_a_term_inside_the_detector_exemption_refuses_on_scan_alone(self):
        """RE-SEATED 2026-09-03. This case used to plant a term inside
        scripts/pre_push_gate.py, relying on cleanse.sh's since-removed
        DETECTORS exemption to let it clear cleanse.sh while
        private_terms_scan.py still caught it. That exemption is gone
        (2026-09-03, "the scanner must never contain what it forbids"),
        so the premise no longer holds and the case is re-seated on the
        auditor's own bigger finding instead: inside a CANDIDATE EXPORT
        TREE, docs/plan/EXPORT-ALLOWLIST.txt is never itself exported, so
        cleanse.sh always finds it absent there. A missing allowlist must
        mean scan every file git sees, never scan none, so a term planted
        under products/ in the fixture root, with no allowlist file
        anywhere in this tree (this fixture's ordinary shape, same as
        every sibling case above), must still refuse on cleanse.sh alone.
        private_terms_scan.py independently catches it too here (it scans
        the whole outgoing diff with no path exclusions, so a products/
        path was never invisible to it the way scripts/pre_push_gate.py
        used to be to cleanse.sh); both gates refusing the same violation
        is the correct, observed shape now, not a weaker test."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            _make_fake_root(root, {
                "products/sample/tool.py": "# FAKETERM-DETECTOR-XYZ\n",
            })
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"),
                ["scripts", "products/sample"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-DETECTOR-XYZ"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            proc = _run_cli(["--allowlist", allowlist_path, "--root", root,
                              "--remote", remote_dir, "--branch", "main",
                              "--dry-run"], env)
            self.assertEqual(proc.returncode, EP.EXIT_REFUSED,
                              proc.stdout + proc.stderr)
            self.assertEqual(_gate_exit(proc.stdout, "cleanse"), 1,
                              proc.stdout)
            self.assertEqual(_gate_exit(proc.stdout, "identity_guard"), 0,
                              proc.stdout)
            self.assertEqual(_gate_exit(proc.stdout, "private_terms_scan"),
                              1, proc.stdout)
            self.assertNotIn("FAKETERM-DETECTOR-XYZ", proc.stdout)
            self.assertNotIn("FAKETERM-DETECTOR-XYZ", proc.stderr)

    def test_a_config_identity_term_refuses_on_identity_guard_alone(self):
        """identity_guard.py is the only one of the three that inspects a
        commit's author/committer identity (git config), rather than file
        content or diff text; a term sitting only in a git config email
        domain is invisible to cleanse.sh's and private_terms_scan.py's
        file/diff scans.

        Exercised directly against export_public.run_gates(), the exact
        composition point the drill's finding neutered: export_public.py's
        own build_identity_check_dir() always writes the shielded export
        identity (a fixed noreply address), by design, so a real export
        can never carry a private domain there, and there is no CLI flag
        that could plant a term in it without weakening that shield. A
        hand built identity_dir, passed straight to run_gates() alongside
        a normal, clean export_dir, tests the same composition logic
        without touching that design or export_public.py itself."""
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as identity_dir, \
             tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "public.md": "hello, nothing private here\n"})
            EP.build_orphan_commit(export_dir, ["scripts", "public.md"],
                                    root=root)

            subprocess.run(["git", "init", "-q"], cwd=identity_dir,
                            check=True)
            subprocess.run(["git", "config", "user.name", "Some Body"],
                            cwd=identity_dir, check=True)
            subprocess.run(["git", "config", "user.email",
                             "person@fakebrothertermxyz.example"],
                            cwd=identity_dir, check=True)

            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["fakebrothertermxyz"])
            with mock.patch.dict(os.environ,
                                  {"BROTHER_PRIVATE_TERMS": terms_path}):
                all_ok, lines = EP.run_gates(export_dir, identity_dir)

            self.assertFalse(all_ok, lines)
            cleanse_line = next(l for l in lines if l.startswith("cleanse:"))
            identity_line = next(l for l in lines
                                  if l.startswith("identity_guard:"))
            terms_line = next(l for l in lines
                               if l.startswith("private_terms_scan:"))
            self.assertIn("exit 0,", cleanse_line, lines)
            self.assertIn("exit 0,", terms_line, lines)
            self.assertNotIn("exit 0,", identity_line, lines)
            self.assertNotIn("fakebrothertermxyz", "\n".join(lines))


class OneTermListFeedsEveryGate(unittest.TestCase):
    """E37, 2026-09-03. The exporter used to default its gates to
    ~/.claude/private-terms.txt while the estate's law, bm_private_scan.py
    and the assurance product's history test all read
    ~/.brothersbe-private-names: two copies of one list, nothing keeping
    them equal. Now there is one default, every gate is handed the same
    file through BROTHER_PRIVATE_TERMS, and an unusable list (absent, empty
    or comment-only) makes the export REFUSE on NO-DATA rather than CLEAR.
    Fixture lists only, never the real one."""

    def test_the_default_list_is_the_one_the_law_names(self):
        self.assertEqual(EP.DEFAULT_TERMS_FILE,
                         os.path.expanduser("~/.brothersbe-private-names"))

    def _gates_over_a_clean_tree(self, env_patch):
        """run_gates over a clean candidate tree with os.environ patched by
        env_patch (a dict; a value of None pops the key). Returns (all_ok,
        lines)."""
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as identity_dir, \
             tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "public.md": "hello, nothing private here\n"})
            EP.build_orphan_commit(export_dir, ["scripts", "public.md"],
                                    root=root)
            subprocess.run(["git", "init", "-q"], cwd=identity_dir,
                            check=True)
            with mock.patch.dict(os.environ):
                for key, value in env_patch.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
                return EP.run_gates(export_dir, identity_dir)

    def _assert_no_data_everywhere(self, all_ok, lines):
        self.assertFalse(all_ok, lines)
        cleanse_line = next(l for l in lines if l.startswith("cleanse:"))
        terms_line = next(l for l in lines
                           if l.startswith("private_terms_scan:"))
        self.assertNotIn("exit 0,", cleanse_line, lines)
        self.assertNotIn("exit 0,", terms_line, lines)
        self.assertIn("NO-DATA", cleanse_line, lines)
        self.assertIn("NO-DATA", terms_line, lines)
        self.assertNotIn("PASS", cleanse_line, lines)
        self.assertNotIn("PASS", terms_line, lines)

    def test_a_comment_only_list_named_by_the_environment_is_NO_DATA(self):
        with tempfile.TemporaryDirectory() as d:
            terms_path = _write_lines(os.path.join(d, "terms.txt"),
                                      ["# a comment, no term"])
            all_ok, lines = self._gates_over_a_clean_tree(
                {"BROTHER_PRIVATE_TERMS": terms_path})
        self._assert_no_data_everywhere(all_ok, lines)

    def test_the_default_file_reaches_every_gate_when_the_environment_is_unset(self):
        """The plumbing itself: with no BROTHER_PRIVATE_TERMS in the
        environment, run_gates reads DEFAULT_TERMS_FILE at call time and
        hands it to the subprocess gates, so cleanse.sh (which only ever
        reads the variable) reports NO-DATA on the empty default rather
        than falling back to a default of its own."""
        with tempfile.TemporaryDirectory() as d:
            empty_path = _write_lines(os.path.join(d, "terms.txt"), [])
            with mock.patch.object(EP, "DEFAULT_TERMS_FILE", empty_path):
                all_ok, lines = self._gates_over_a_clean_tree(
                    {"BROTHER_PRIVATE_TERMS": None})
        self._assert_no_data_everywhere(all_ok, lines)


class TheHelpTextTellsTheTruthAboutTheDryRun(unittest.TestCase):
    """E37 item 3. --help used to say that without --push the exporter
    never contacts a remote, while build_identity_check_dir runs before
    the --push branch and fetches the remote's tip so identity_guard.py
    can compare against origin/<branch>. The code is right (a dry run
    that cannot see the remote cannot check the identity), so the text
    now says what the code does, and this pins both halves."""

    def test_help_names_the_fetch_and_a_dry_run_really_fetches(self):
        proc = _run_cli(["--help"], dict(os.environ))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("never contacts", proc.stdout)
        self.assertIn("fetches", proc.stdout.lower())
        self.assertIn("nothing is pushed", proc.stdout)

        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            _make_fake_root(root, {"clean.md": "nothing private here\n"})
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "clean.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            dry = _run_cli(["--allowlist", allowlist_path, "--root", root,
                             "--remote", remote_dir, "--branch", "main"], env)
            self.assertEqual(dry.returncode, EP.EXIT_OK,
                              dry.stdout + dry.stderr)
            self.assertIn("DRY-RUN", dry.stdout)
            # identity_guard can only PASS against a fetched origin/main,
            # so exit 0 here is the proof that the dry run fetched.
            self.assertEqual(_gate_exit(dry.stdout, "identity_guard"), 0,
                              dry.stdout)
            # and the remote itself gained nothing: read only
            log = subprocess.run(
                ["git", "-C", remote_dir, "log", "--oneline", "main"],
                capture_output=True, text=True, check=True)
            self.assertEqual(len(log.stdout.strip().splitlines()), 1,
                              log.stdout)


class NoAllowlistRefusesEverything(unittest.TestCase):
    def test_a_missing_allowlist_is_NO_DATA_not_a_silent_empty_export(self):
        env = dict(os.environ)
        proc = _run_cli(["--allowlist", "/no/such/allowlist.txt"], env)
        self.assertEqual(proc.returncode, EP.EXIT_NODATA)
        self.assertIn("NO-DATA", proc.stdout)


class TheHardBoundaryHoldsEvenIfListed(unittest.TestCase):
    """editions/ and .brother-edition are excluded by the exporter's own
    code, never merely by omission from the allowlist."""

    def test_editions_and_the_marker_file_are_never_copied(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            _make_fake_root(root, {
                "editions/personal/secret.md": "private client stuff\n",
                ".brother-edition": "edition: public-core\nvault: none\n",
            })
            allowlist = ["scripts", "editions", ".brother-edition"]
            copied = EP.build_export_tree(dest, allowlist, root=root)
            self.assertNotIn("editions", copied)
            self.assertNotIn(".brother-edition", copied)
            self.assertFalse(os.path.exists(os.path.join(dest, "editions")))
            self.assertFalse(
                os.path.exists(os.path.join(dest, ".brother-edition")))


class TheExportedProductManifestDescribesTheExportedBytes(unittest.TestCase):
    """readiness row E29: a product's CHECKSUMS.sha256 is generated
    (docs/RELEASE.md step 4) over the FULL hub tree for that product,
    including paths (like ci/) the allowlist deliberately withholds. Copied
    verbatim, that manifest would name a file this export never ships.
    regenerate_product_manifests() must instead rewrite it, in the export
    tree, over exactly the files that actually landed there."""

    def _hash(self, path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            h.update(fh.read())
        return h.hexdigest()

    def _parse_manifest(self, path):
        """Each line is <64 hex chars><two spaces><path>, the format both
        checksums.sh and verify-install.sh (products/brothersbe/scripts/
        verify-install.sh, "Manifest lines are...") use."""
        entries = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                entries[line[66:]] = line[:64]
        return entries

    def _seed_fixture(self, root):
        """A miniature stand-in for products/brothersbe: its own real
        scripts/checksums.sh (so the manifest keeps that product's exact
        format), a file the allowlist below keeps ("kept.md"), a file
        standing in for a withheld directory like ci/ ("ci/internal.md",
        never allowlisted), and a STALE CHECKSUMS.sha256 shaped exactly
        like the one docs/RELEASE.md step 4 generates over the whole hub
        tree: it names both files, and gives the kept one a placeholder
        hash so the test proves a real recomputation, never merely a
        filter over the old manifest's lines."""
        _make_fake_root(root, {
            "myproduct/kept.md": "kept content, on the allowlist\n",
            "myproduct/ci/internal.md":
                "internal QA content the allowlist never lists\n",
        })
        os.makedirs(os.path.join(root, "myproduct", "scripts"),
                    exist_ok=True)
        shutil.copy2(REAL_PRODUCT_CHECKSUMS_SH,
                      os.path.join(root, "myproduct", "scripts",
                                   "checksums.sh"))
        placeholder_hash = "0" * 64  # wrong on purpose: proves rehashing
        _write_lines(os.path.join(root, "myproduct", "CHECKSUMS.sha256"), [
            "%s  kept.md" % placeholder_hash,
            "%s  ci/internal.md" % placeholder_hash,
        ])

    def test_the_regenerated_manifest_drops_the_withheld_file_and_rehashes(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as export_dir:
            self._seed_fixture(root)
            # myproduct/ci is never listed, exactly how the real
            # EXPORT-ALLOWLIST.txt withholds products/brothersbe/ci: each
            # kept path is named individually, never the product wholesale.
            allowlist = ["scripts", "myproduct/scripts", "myproduct/kept.md",
                         "myproduct/CHECKSUMS.sha256"]

            # DRIVEN BACKWARDS: the untouched source still carries the
            # withheld entry verbatim. CHECKSUMS.sha256 is itself just
            # another allowlisted file, so this is exactly what a plain
            # copy (the pre-fix behaviour) would have shipped unchanged.
            source_manifest = self._parse_manifest(
                os.path.join(root, "myproduct", "CHECKSUMS.sha256"))
            self.assertIn("ci/internal.md", source_manifest)

            copied, committed = EP.build_orphan_commit(
                export_dir, allowlist, root=root)
            self.assertTrue(committed, copied)

            exported_manifest_path = os.path.join(
                export_dir, "myproduct", "CHECKSUMS.sha256")
            self.assertTrue(os.path.isfile(exported_manifest_path))
            exported = self._parse_manifest(exported_manifest_path)

            # only what actually shipped is named: kept.md and the
            # generator script itself, never the withheld ci/ file
            self.assertEqual(sorted(exported),
                              ["kept.md", "scripts/checksums.sh"])
            self.assertNotIn("ci/internal.md", exported)

            # every remaining entry's hash is a REAL recomputation over
            # the exported bytes, never the stale placeholder
            for rel_path, manifest_hash in exported.items():
                real_hash = self._hash(
                    os.path.join(export_dir, "myproduct", rel_path))
                self.assertEqual(manifest_hash, real_hash, rel_path)
            self.assertNotEqual(exported["kept.md"], "0" * 64)


class APushTowardThePublicRemoteFromAnEditionIsRefused(unittest.TestCase):
    """docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 5's driven-backwards
    case, exercised against the REAL editions/personal in this checkout."""

    def test_editions_personal_cannot_push_to_the_public_remote(self):
        code, msg = EG.check_push(
            "https://github.com/khalilmaaouni/Brother",
            cwd=os.path.join(EP.ROOT, "editions", "personal"), env={})
        self.assertEqual(code, EG.EXIT_REFUSED)
        self.assertIn("personal", msg)
        self.assertIn("read-only export target", msg)


class TheExportersOwnInvocationPasses(unittest.TestCase):
    """docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 4 and step 5's
    'passes' cases together: a real --push, to a real (local) git remote,
    appends one commit under the shielded identity, and prints the marker
    that is the single allow past the edition guard's own law."""

    def test_dry_run_then_real_push_to_a_local_bare_remote(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as seed_dir, \
             tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            subprocess.run(["git", "init", "-q", seed_dir], check=True)
            subprocess.run(["git", "-C", seed_dir, "config", "user.name",
                             "Seed"], check=True)
            subprocess.run(["git", "-C", seed_dir, "config", "user.email",
                             "seed@example.com"], check=True)
            with open(os.path.join(seed_dir, "README.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("seed\n")
            subprocess.run(["git", "-C", seed_dir, "add", "-A"], check=True)
            subprocess.run(["git", "-C", seed_dir, "commit", "-q", "-m",
                             "seed"], check=True)
            subprocess.run(["git", "-C", seed_dir, "branch", "-M", "main"],
                            check=True)
            subprocess.run(["git", "-C", seed_dir, "push", remote_dir,
                             "main"], check=True)

            _make_fake_root(root, {"clean.md": "nothing private here\n"})
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "clean.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path

            dry = _run_cli(["--allowlist", allowlist_path, "--root", root,
                             "--remote", remote_dir, "--branch", "main",
                             "--dry-run"], env)
            self.assertEqual(dry.returncode, EP.EXIT_OK,
                              dry.stdout + dry.stderr)
            self.assertIn("CLEAR", dry.stdout)

            pushed = _run_cli(["--allowlist", allowlist_path, "--root", root,
                                "--remote", remote_dir, "--branch", "main",
                                "--push"], env)
            self.assertEqual(pushed.returncode, EP.EXIT_OK,
                              pushed.stdout + pushed.stderr)
            self.assertIn(
                "%s=%s set for this push" % (EG.EXPORT_ENV, EG.EXPORT_MARK),
                pushed.stdout)
            self.assertIn("PUSHED", pushed.stdout)

            log = subprocess.run(
                ["git", "-C", remote_dir, "log", "--oneline", "main"],
                capture_output=True, text=True, check=True)
            # exactly two commits: the seed and the one export, an APPEND
            self.assertEqual(len(log.stdout.strip().splitlines()), 2,
                              log.stdout)
            author = subprocess.run(
                ["git", "-C", remote_dir, "log", "-1", "--format=%an %ae",
                 "main"], capture_output=True, text=True, check=True)
            self.assertIn(EP.AUTHOR_EMAIL, author.stdout)

    def test_a_second_export_run_appends_rather_than_rewrites(self):
        """The plan's own law: appends, never rewrites. A second export
        with new content adds a THIRD commit; the first two stay exactly
        where they were.

        The remote is seeded with a commit before either export, matching
        the real public repository (which already has history, never a
        zero-commit repo): identity_guard's own outgoing-range check needs
        a real origin/HEAD to compare against, and a genuinely empty
        remote has none to offer yet, which is the first-ever-export case
        this test is not exercising."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as seed_dir, \
             tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            subprocess.run(["git", "init", "-q", seed_dir], check=True)
            subprocess.run(["git", "-C", seed_dir, "config", "user.name",
                             "Seed"], check=True)
            subprocess.run(["git", "-C", seed_dir, "config", "user.email",
                             "seed@example.com"], check=True)
            with open(os.path.join(seed_dir, "README.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("seed\n")
            subprocess.run(["git", "-C", seed_dir, "add", "-A"], check=True)
            subprocess.run(["git", "-C", seed_dir, "commit", "-q", "-m",
                             "seed"], check=True)
            subprocess.run(["git", "-C", seed_dir, "branch", "-M", "main"],
                            check=True)
            subprocess.run(["git", "-C", seed_dir, "push", remote_dir,
                             "main"], check=True)

            _make_fake_root(root, {"clean.md": "first export\n"})
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "clean.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path

            first = _run_cli(["--allowlist", allowlist_path, "--root", root,
                               "--remote", remote_dir, "--branch", "main",
                               "--push"], env)
            self.assertEqual(first.returncode, EP.EXIT_OK,
                              first.stdout + first.stderr)
            first_log = subprocess.run(
                ["git", "-C", remote_dir, "log", "--format=%H", "main"],
                capture_output=True, text=True, check=True)
            first_shas = first_log.stdout.split()
            self.assertEqual(len(first_shas), 2)  # seed + first export

            with open(os.path.join(root, "clean.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("second export, changed\n")
            second = _run_cli(["--allowlist", allowlist_path, "--root", root,
                                "--remote", remote_dir, "--branch", "main",
                                "--push"], env)
            self.assertEqual(second.returncode, EP.EXIT_OK,
                              second.stdout + second.stderr)
            second_log = subprocess.run(
                ["git", "-C", remote_dir, "log", "--format=%H", "main"],
                capture_output=True, text=True, check=True)
            second_shas = second_log.stdout.split()
            self.assertEqual(len(second_shas), 3)  # seed + first + second
            # the first two commits are UNCHANGED, still there, at the bottom
            self.assertEqual(second_shas[-2:], first_shas)


class ABrandNewRemoteIsStartedOnlyWithBootstrap(unittest.TestCase):
    """docs/decisions/client-terms-in-public-history-2026-09-03.json rules
    that the public repository is replaced by a FRESH one with a clean
    history. push_appended's fetch of the tip fails against a repository
    with no branch at all, and its refusal ("never starts unrelated
    history") is right for every populated remote and wrong for exactly
    that one. --bootstrap opens that one case and nothing else. An EMPTY
    bare remote here stands in for the fresh public repository, the way
    TheExportersOwnInvocationPasses's seeded one stands in for the
    populated one. Cases a to d call push_appended directly for its own
    contract, as TheReleaseRecordShipsItsOwnSourceRevision does; e and f
    drive the whole route through main() as a subprocess, which only
    works because build_identity_check_dir seeds identity_guard an honest
    outgoing range against a remote that has nothing to fetch (g pins that
    shape: origin/HEAD set, origin/main..HEAD exactly one commit)."""

    ALLOWLIST = ["scripts", "clean.md"]

    def _export_root(self, root):
        _make_fake_root(root, {"clean.md": "first export, clean\n"})

    def _count(self, remote_dir, branch):
        return subprocess.run(
            ["git", "-C", remote_dir, "rev-list", "--count", branch],
            capture_output=True, text=True, check=True).stdout.strip()

    def _refs(self, remote_dir):
        return subprocess.run(
            ["git", "-C", remote_dir, "for-each-ref",
             "--format=%(refname) %(objectname)"],
            capture_output=True, text=True, check=True).stdout

    def test_a_an_empty_remote_without_bootstrap_still_refuses(self):
        """Pins the pre-existing refusal: without the flag an empty remote
        is refused exactly as before, and gains no ref of any kind."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            self._export_root(root)
            code, lines = EP.push_appended(self.ALLOWLIST, remote_dir,
                                           "main", root=root)
            self.assertEqual(code, EP.EXIT_REFUSED, lines)
            self.assertTrue(any(
                l.startswith("REFUSED: could not fetch main from")
                and "this exporter never starts unrelated history on the "
                    "public repository" in l
                for l in lines), lines)
            self.assertFalse(any(l.startswith("BOOTSTRAP") for l in lines),
                             lines)
            self.assertEqual(self._refs(remote_dir), "")

    def test_b_an_empty_remote_with_bootstrap_gets_the_export_as_its_first_commit(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as expected_dir:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            self._export_root(root)
            code, lines = EP.push_appended(self.ALLOWLIST, remote_dir,
                                           "main", root=root, tag="v9.9.9",
                                           bootstrap=True)
            self.assertEqual(code, EP.EXIT_OK, lines)
            self.assertIn("BOOTSTRAP: %s has no branch; this export becomes "
                          "the first commit of main" % remote_dir, lines)
            self.assertTrue(any(l.startswith("PUSHED") for l in lines), lines)
            # exactly ONE commit, under the shielded identity
            self.assertEqual(self._count(remote_dir, "main"), "1")
            author = subprocess.run(
                ["git", "-C", remote_dir, "log", "-1", "--format=%an %ae",
                 "main"], capture_output=True, text=True, check=True)
            self.assertIn(EP.AUTHOR_EMAIL, author.stdout)
            # whose tree is the export tree, file for file, byte for byte
            EP.build_export_tree(expected_dir, self.ALLOWLIST, root=root)
            expected = sorted(
                os.path.relpath(os.path.join(base, name), expected_dir)
                for base, _dirs, names in os.walk(expected_dir)
                for name in names)
            self.assertIn("clean.md", expected)
            shipped = subprocess.run(
                ["git", "-C", remote_dir, "ls-tree", "-r", "--name-only",
                 "main"], capture_output=True, text=True, check=True)
            self.assertEqual(sorted(shipped.stdout.split()), expected)
            for rel in expected:
                blob = subprocess.run(
                    ["git", "-C", remote_dir, "show", "main:%s" % rel],
                    capture_output=True, check=True).stdout
                with open(os.path.join(expected_dir, rel), "rb") as fh:
                    self.assertEqual(blob, fh.read(), rel)
            # and the tag landed on the remote, pointing at that commit
            tag = subprocess.run(
                ["git", "-C", remote_dir, "rev-parse", "v9.9.9^{}", "main"],
                capture_output=True, text=True, check=True)
            self.assertEqual(len(set(tag.stdout.split())), 1, tag.stdout)

    def test_c_a_remote_with_any_branch_refuses_bootstrap_and_is_left_alone(self):
        """The flag is "start only from nothing", never "append if you
        can": a remote that already has a branch refuses whether the
        branch asked for is that one or another name, and its refs and
        history are exactly as they were."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)  # one seed commit on main
            self._export_root(root)
            before_refs = self._refs(remote_dir)
            self.assertIn("refs/heads/main", before_refs)
            self.assertEqual(self._count(remote_dir, "main"), "1")
            for branch in ("main", "release"):
                code, lines = EP.push_appended(self.ALLOWLIST, remote_dir,
                                               branch, root=root,
                                               bootstrap=True)
                self.assertEqual(code, EP.EXIT_REFUSED, (branch, lines))
                self.assertIn("REFUSED: --bootstrap is only for a "
                              "repository with no branch at all; %s "
                              "already has: main" % remote_dir, lines)
                self.assertFalse(
                    any(l.startswith(("BOOTSTRAP", "PUSHED")) for l in lines),
                    (branch, lines))
            self.assertEqual(self._refs(remote_dir), before_refs)
            self.assertEqual(self._count(remote_dir, "main"), "1")

    def test_d_bootstrap_without_push_is_refused_exactly_like_tag(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._export_root(root)
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), self.ALLOWLIST)
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            common = ["--allowlist", allowlist_path, "--root", root,
                      "--remote", remote_dir, "--branch", "main"]
            tagged = _run_cli(common + ["--tag", "v9.9.9"], env)
            self.assertEqual(tagged.returncode, EP.EXIT_REFUSED,
                              tagged.stdout + tagged.stderr)
            self.assertIn("REFUSED: --tag only means something with --push",
                          tagged.stdout)
            boot = _run_cli(common + ["--bootstrap"], env)
            self.assertEqual(boot.returncode, tagged.returncode,
                              boot.stdout + boot.stderr)
            self.assertIn("REFUSED: --bootstrap only means something with "
                          "--push", boot.stdout)
            self.assertNotIn("BOOTSTRAP:", boot.stdout)
            # neither dry run touched the seeded remote
            self.assertEqual(self._count(remote_dir, "main"), "1")


    def _cli(self, root, remote_dir):
        """(env, common args) for _run_cli against the fixture root and the
        given remote: the same shape test_d and the CLI tests above use."""
        allowlist_path = _write_lines(
            os.path.join(root, "ALLOWLIST.txt"), self.ALLOWLIST)
        terms_path = _write_lines(
            os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
        env = dict(os.environ)
        env["BROTHER_PRIVATE_TERMS"] = terms_path
        return env, ["--allowlist", allowlist_path, "--root", root,
                     "--remote", remote_dir, "--branch", "main"]

    def test_e_the_whole_route_runs_through_the_cli_against_an_empty_remote(self):
        """The clean-extraction command itself, end to end through main():
        the dry-run gates clear against a remote with no branch (identity
        check dir seeded, identity_guard reads a real PASS over the one
        candidate commit), then push_appended bootstraps, pushes and tags."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            self._export_root(root)
            env, common = self._cli(root, remote_dir)
            proc = _run_cli(common + ["--push", "--bootstrap",
                                      "--tag", "v9.9.9"], env)
            out = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, EP.EXIT_OK, out)
            self.assertIn("identity check: %s has no branch; the whole "
                          "candidate is the outgoing range" % remote_dir,
                          proc.stdout)
            self.assertEqual(_gate_exit(proc.stdout, "identity_guard"), 0,
                              out)
            identity_line = next(l for l in proc.stdout.splitlines()
                                 if l.startswith("identity_guard:"))
            self.assertIn("PASS: identity clean", identity_line)
            self.assertIn("origin/main..HEAD", identity_line)
            self.assertIn("CLEAR", proc.stdout)
            self.assertIn("BOOTSTRAP: %s has no branch; this export becomes "
                          "the first commit of main" % remote_dir,
                          proc.stdout)
            self.assertIn("PUSHED", proc.stdout)
            self.assertIn("TAGGED: v9.9.9", proc.stdout)
            self.assertEqual(self._count(remote_dir, "main"), "1")
            tags = subprocess.run(
                ["git", "-C", remote_dir, "tag", "-l"],
                capture_output=True, text=True, check=True).stdout.split()
            self.assertEqual(tags, ["v9.9.9"])

    def test_f_the_cli_without_bootstrap_still_refuses_the_empty_remote(self):
        """The negative twin of e: the same command, no --bootstrap. The
        gates clear the same way, and push_appended's own pre-existing
        refusal is what stops it, with nothing pushed and nothing tagged."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            self._export_root(root)
            env, common = self._cli(root, remote_dir)
            proc = _run_cli(common + ["--push", "--tag", "v9.9.9"], env)
            out = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, EP.EXIT_REFUSED, out)
            self.assertEqual(_gate_exit(proc.stdout, "identity_guard"), 0,
                              out)
            self.assertIn("REFUSED: could not fetch main from %s"
                          % remote_dir, proc.stdout)
            self.assertIn("this exporter never starts unrelated history on "
                          "the public repository", proc.stdout)
            self.assertNotIn("BOOTSTRAP", proc.stdout)
            self.assertNotIn("PUSHED", proc.stdout)
            self.assertNotIn("TAGGED", proc.stdout)
            self.assertEqual(self._refs(remote_dir), "")

    def test_g_the_identity_check_dir_ranges_over_exactly_the_one_candidate_commit(self):
        """The seeded shape, directly: what identity_guard.py's own range
        resolution reads (symbolic-ref --short refs/remotes/origin/HEAD,
        then origin/main..HEAD) must name the candidate commit alone, the
        seed itself must be empty and outside the range, and the remote
        must have been read only."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as identity_dir:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            self._export_root(root)
            EP.build_identity_check_dir(identity_dir, self.ALLOWLIST,
                                        remote_dir, "main", root=root)
            head_ref = subprocess.run(
                ["git", "-C", identity_dir, "symbolic-ref", "--short",
                 "refs/remotes/origin/HEAD"],
                capture_output=True, text=True).stdout.strip()
            self.assertEqual(head_ref, "origin/main")
            count = subprocess.run(
                ["git", "-C", identity_dir, "rev-list", "--count",
                 "origin/main..HEAD"],
                capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(count, "1")
            seed_tree = subprocess.run(
                ["git", "-C", identity_dir, "ls-tree", "origin/main"],
                capture_output=True, text=True, check=True).stdout
            self.assertEqual(seed_tree, "")
            self.assertEqual(self._refs(remote_dir), "")


class ATagRefusesAnExportTreeItsOwnProductsCannotVerify(unittest.TestCase):
    """An enterprise critic's one blocking finding, 2026-09-03: at the
    public tag v0.9.11 each product's CHECKSUMS.sha256 was the hub's
    manifest, not the export's, so `scripts/verify-install.sh` inside the
    tag tree printed FAILED (304 match, 492 missing; 451 match, 980
    missing). regenerate_product_manifests now rebuilds the manifests over
    the export tree, and tag_time_checks is what PROVES it on the
    candidate before a tag is pushed: every product's own verifier must
    pass there, and the release note must be really stamped. Same
    fixtures as the push tests above (a seeded local bare remote, a fake
    root) plus a miniature product borrowing brothersbe's real
    checksums.sh and verify-install.sh, never test-only stand-ins."""

    ALLOWLIST = ["scripts", "products/myproduct"]

    def _seed_product(self, root):
        _make_fake_root(root, {
            "products/myproduct/kept.md": "kept content, shipped\n",
        })
        scripts = os.path.join(root, "products", "myproduct", "scripts")
        os.makedirs(scripts, exist_ok=True)
        shutil.copy2(REAL_PRODUCT_CHECKSUMS_SH,
                     os.path.join(scripts, "checksums.sh"))
        shutil.copy2(REAL_PRODUCT_VERIFY_INSTALL_SH,
                     os.path.join(scripts, "verify-install.sh"))
        # the hub's shape of the manifest: it names a file this export
        # never carries, so a verbatim copy would FAIL its own verifier
        _write_lines(
            os.path.join(root, "products", "myproduct", "CHECKSUMS.sha256"),
            ["%s  kept.md" % ("0" * 64), "%s  ci/internal.md" % ("0" * 64)])

    def _remote_state(self, remote_dir):
        """(commit count on main, [tags]) of the bare remote."""
        count = subprocess.run(
            ["git", "-C", remote_dir, "rev-list", "--count", "main"],
            capture_output=True, text=True, check=True).stdout.strip()
        tags = subprocess.run(
            ["git", "-C", remote_dir, "tag", "-l"],
            capture_output=True, text=True, check=True).stdout.split()
        return count, tags

    def test_e_a_stale_product_manifest_refuses_the_tagged_push(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._seed_product(root)
            real_build = EP.build_export_tree

            def stale_build(dest, allowlist, root):
                copied = real_build(dest, allowlist, root)
                # one entry the export tree does not carry, appended AFTER
                # the regeneration the exporter itself just performed
                manifest = os.path.join(dest, "products", "myproduct",
                                        "CHECKSUMS.sha256")
                with open(manifest, "a", encoding="utf-8") as fh:
                    fh.write("%s  ghost.md\n" % ("0" * 64))
                return copied

            with mock.patch.object(EP, "build_export_tree",
                                   side_effect=stale_build):
                code, lines = EP.push_appended(
                    self.ALLOWLIST, remote_dir, "main", root=root,
                    tag="v9.9.9")
            self.assertEqual(code, EP.EXIT_REFUSED, lines)
            self.assertTrue(any(l.startswith(
                "REFUSED: products/myproduct's own install verifier fails "
                "on the export tree: ") for l in lines), lines)
            self.assertFalse(any(l.startswith(("PUSHED", "TAGGED",
                                               "verified:"))
                                 for l in lines), lines)
            # nothing pushed, nothing tagged: the seed commit stands alone
            self.assertEqual(self._remote_state(remote_dir), ("1", []))

    def test_f_intact_manifests_pass_and_the_verified_lines_are_printed(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._seed_product(root)
            code, lines = EP.push_appended(
                self.ALLOWLIST, remote_dir, "main", root=root, tag="v9.9.9")
            self.assertEqual(code, EP.EXIT_OK, lines)
            verified = [l for l in lines if l.startswith("verified: ")]
            self.assertEqual(len(verified), 1, lines)
            m = re.match(r"verified: products/myproduct install verifier "
                         r"PASS on the export tree, (\d+) file\(s\)$",
                         verified[0])
            self.assertIsNotNone(m, verified[0])
            # the count is the verifier's own, and it is the SHIPPED
            # manifest's line count, which no longer names the withheld file
            shipped = subprocess.run(
                ["git", "-C", remote_dir, "show",
                 "main:products/myproduct/CHECKSUMS.sha256"],
                capture_output=True, text=True, check=True).stdout
            self.assertEqual(int(m.group(1)),
                             len(shipped.strip().splitlines()))
            self.assertNotIn("ci/internal.md", shipped)
            self.assertTrue(any(l.startswith("TAGGED") for l in lines), lines)
            self.assertEqual(self._remote_state(remote_dir), ("2", ["v9.9.9"]))

    def test_g_a_placeholder_release_note_refuses_the_tagged_push(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            _make_fake_root(root, {
                "docs/releases/9.9.9.md":
                    "# Brother 9.9.9\n\n%s\n\n%s\n\n"
                    "## What this release carries\n\nstuff.\n"
                    % (EP.SOURCE_REVISION_HEADER,
                       EP.SOURCE_REVISION_PLACEHOLDER),
            })
            # root is deliberately NOT a git repository, so hub_head_rev
            # finds nothing and the stamp cannot fire: the exact 0.9.11 shape
            code, lines = EP.push_appended(
                ["scripts", "docs"], remote_dir, "main", root=root,
                tag="v9.9.9")
            self.assertEqual(code, EP.EXIT_REFUSED, lines)
            self.assertIn("REFUSED: docs/releases/9.9.9.md still carries the "
                          "placeholder source revision; a tag must carry the "
                          "hub revision it was cut from", lines)
            self.assertFalse(any(l.startswith(("PUSHED", "TAGGED"))
                                 for l in lines), lines)
            self.assertEqual(self._remote_state(remote_dir), ("1", []))

    def test_h_a_note_that_only_quotes_the_placeholder_is_not_refused(self):
        """The block match, not a substring: docs/releases/1.0.0.md quotes
        the placeholder in prose while explaining why 0.9.11's note is
        unusable, and is itself stamped with a real hub commit. Same
        reasoning, same block as scripts/release_notes_stamped.py."""
        with tempfile.TemporaryDirectory() as export_dir:
            releases = os.path.join(export_dir, "docs", "releases")
            os.makedirs(releases)
            with open(os.path.join(releases, "9.9.9.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("# Brother 9.9.9\n\n%s\n\nCut from hub commit "
                         "`abc123`.\n\nThe 0.9.11 note still reads \"%s\" "
                         "and is unusable.\n"
                         % (EP.SOURCE_REVISION_HEADER,
                            EP.SOURCE_REVISION_PLACEHOLDER))
            ok, lines = EP.tag_time_checks(export_dir, "9.9.9")
            self.assertTrue(ok, lines)
            self.assertEqual(lines, [])  # no product, nothing to verify


class TheReleaseRecordShipsItsOwnSourceRevision(unittest.TestCase):
    """E6.1a: an external release-integrity reviewer confirmed
    reproduce_export.py rebuilds the export byte for byte from a hub
    revision, but that revision shipped nowhere, forcing private
    archaeology. A tagged push must stamp docs/releases/<version>.md, in
    the hub's own working tree AND in what actually gets pushed, with the
    hub commit the release was cut from."""

    def _git(self, *args, cwd):
        subprocess.run(["git"] + list(args), cwd=cwd, check=True,
                        capture_output=True, text=True)

    def test_a_tagged_push_stamps_the_source_revision_both_places(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as seed_dir, \
             tempfile.TemporaryDirectory() as root:
            subprocess.run(["git", "init", "-q", "--bare", remote_dir],
                            check=True)
            self._git("init", "-q", cwd=seed_dir)
            self._git("config", "user.name", "Seed", cwd=seed_dir)
            self._git("config", "user.email", "seed@example.com",
                       cwd=seed_dir)
            with open(os.path.join(seed_dir, "README.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("seed\n")
            self._git("add", "-A", cwd=seed_dir)
            self._git("commit", "-q", "-m", "seed", cwd=seed_dir)
            self._git("branch", "-M", "main", cwd=seed_dir)
            self._git("push", remote_dir, "main", cwd=seed_dir)

            _make_fake_root(root, {
                "docs/releases/9.9.9.md": "# Brother 9.9.9\n\nnotes.\n",
            })
            self._git("init", "-q", cwd=root)
            self._git("config", "user.name", "Hub", cwd=root)
            self._git("config", "user.email", "hub@example.com", cwd=root)
            self._git("add", "-A", cwd=root)
            self._git("commit", "-q", "-m", "hub state", cwd=root)
            head = subprocess.run(
                ["git", "-C", root, "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True).stdout.strip()

            code, lines = EP.push_appended(
                ["scripts", "docs"], remote_dir, "main", root=root,
                tag="v9.9.9")
            self.assertEqual(code, EP.EXIT_OK, lines)
            self.assertTrue(
                any("stamped" in l and head in l for l in lines), lines)

            with open(os.path.join(root, "docs", "releases", "9.9.9.md"),
                      encoding="utf-8") as fh:
                hub_copy = fh.read()
            self.assertIn(EP.SOURCE_REVISION_HEADER, hub_copy)
            self.assertIn(head, hub_copy)

            pushed = subprocess.run(
                ["git", "-C", remote_dir, "show",
                 "main:docs/releases/9.9.9.md"],
                capture_output=True, text=True, check=True)
            self.assertIn(head, pushed.stdout)

    def test_a_second_stamp_never_overwrites_the_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "9.9.9.md")
            with open(notes, "w", encoding="utf-8") as fh:
                fh.write("# Brother 9.9.9\n\nnotes.\n")
            self.assertTrue(EP.stamp_source_revision(notes, "9.9.9", "abc123"))
            self.assertFalse(EP.stamp_source_revision(notes, "9.9.9", "def456"))
            with open(notes, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("abc123", text)
            self.assertNotIn("def456", text)

    def test_a_placeholder_stamped_note_gets_the_real_revision(self):
        """The exporter seeds release notes with SOURCE_REVISION_PLACEHOLDER
        under SOURCE_REVISION_HEADER before a real stamp lands. That header
        being present must not read as already-stamped: the placeholder
        gets replaced with a real revision block."""
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "9.9.9.md")
            with open(notes, "w", encoding="utf-8") as fh:
                fh.write(
                    "# Brother 9.9.9\n\n## Source revision\n\n%s\n\n"
                    "## What this release carries\n\nstuff.\n"
                    % EP.SOURCE_REVISION_PLACEHOLDER)
            self.assertTrue(EP.stamp_source_revision(notes, "9.9.9", "abc123"))
            with open(notes, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn(EP.SOURCE_REVISION_PLACEHOLDER, text)
            self.assertIn("abc123", text)

    def test_a_note_with_a_real_revision_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes = os.path.join(tmp, "9.9.9.md")
            original = (
                "# Brother 9.9.9\n\n## Source revision\n\n"
                "Cut from hub commit `abc123`. Reproduce the export byte "
                "for byte with:\n\n"
                "    python3 scripts/reproduce_export.py --source-rev "
                "abc123 --tag v9.9.9 --public <local checkout of this "
                "repository>\n\n## What this release carries\n\nstuff.\n"
            )
            with open(notes, "w", encoding="utf-8") as fh:
                fh.write(original)
            self.assertFalse(EP.stamp_source_revision(notes, "9.9.9", "def456"))
            with open(notes, encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(text, original)


if __name__ == "__main__":
    unittest.main()
