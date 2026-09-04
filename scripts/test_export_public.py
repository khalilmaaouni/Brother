"""Driven-backwards tests for the allowlist exporter, docs/plan/HUB-
MIGRATION-PLAN-2026-08-30.md step 4.

Every private term used here is FAKE, per this estate's own rule for these
scanners' test fixtures (see scripts/test_private_terms_scan.py's own
docstring): a term real enough to matter, committed alongside the tool that
looks for it, publishes exactly what the tool exists to stop.
"""
import contextlib
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

# E100: one sandbox for every temp tree this process makes, removed at exit.
import os as _e100_os, sys as _e100_sys  # noqa: E402
_e100_sys.path.append(_e100_os.path.join(
    _e100_os.path.dirname(_e100_os.path.abspath(__file__)), '.'))
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


def _git_track_all(root):
    """Stage everything currently on disk under `root` into a fresh (or
    existing) git index, the way the real hub tracks the paths this
    exporter ships: build_export_tree now walks `git ls-files`, never a
    raw filesystem walk (2026-09-03, the __pycache__/.sbe leak), so a
    fixture `root` must be a git repository with its files staged before
    the exporter can see any of them. `-f` mirrors how the two real CSV
    fixtures reached the hub's own index: force-added past whatever the
    fixture's own .gitignore says, same as a test that plants one. Safe to
    call more than once (e.g. after writing more fixture files past the
    first _make_fake_root call): `git add -A -f` just re-stages the delta,
    no commit involved (git ls-files reads the index, not a commit)."""
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "-C", root, "add", "-A", "-f"], check=True)


def _make_fake_root(root, files):
    """`files`: {relative_path: content}. Always seeds scripts/cleanse.sh
    with a real copy, so cleanse runs inside the candidate tree exactly as
    it does in production, whenever "scripts" is on the test's allowlist.
    Tracks everything it just wrote (see _git_track_all) so
    build_export_tree's git-ls-files walk can see it; a caller that writes
    MORE files into `root` after this returns must call
    _git_track_all(root) again before exercising the exporter."""
    os.makedirs(os.path.join(root, "scripts"), exist_ok=True)
    shutil.copy2(REAL_CLEANSE, os.path.join(root, "scripts", "cleanse.sh"))
    for rel, content in files.items():
        path = os.path.join(root, rel)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    _git_track_all(root)


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


#: A stand-in for the GitHub CLI, written onto PATH for the tests that
#: drive the release route. It is a real script, not a mock, because the
#: exporter shells out: `pr create` prints a URL the way gh does, and `pr
#: merge` performs the merge the only way it can be performed against a
#: local bare repository, by pushing the branch's commit onto main. That
#: leaves the remote in the state a merged pull request leaves it in, so
#: the assertions below read the real thing. FAKE_GH_FAIL_CREATE and
#: FAKE_GH_FAIL_MERGE drive the two refusal paths backwards.
FAKE_GH = """#!/bin/sh
set -e
sub="$1 $2"
shift 2
remote=$(git remote get-url origin)
case "$sub" in
  "pr create")
    if [ -n "$FAKE_GH_FAIL_CREATE" ]; then
      echo "fake gh: pull request refused by the ruleset" >&2
      exit 1
    fi
    head=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --head) head="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    echo "https://example.invalid/pull/1 (head $head)"
    ;;
  "pr merge")
    if [ -n "$FAKE_GH_FAIL_MERGE" ]; then
      echo "fake gh: merge refused, the branch is not mergeable" >&2
      exit 1
    fi
    git push --quiet "$remote" "HEAD:refs/heads/main"
    echo "Merged pull request"
    ;;
  *)
    echo "fake gh: unsupported command $sub" >&2
    exit 2
    ;;
esac
"""


@contextlib.contextmanager
def _fake_gh(**extra_env):
    """Put the stand-in gh first on PATH for the duration of the block.
    Patching os.environ covers both routes into the exporter: a direct
    push_appended call (whose subprocesses inherit this process's own
    environment) and a CLI run (whose test builds its env from
    dict(os.environ) inside the block)."""
    bindir = tempfile.mkdtemp(prefix="fake-gh-")
    try:
        gh = os.path.join(bindir, "gh")
        with open(gh, "w", encoding="utf-8") as fh:
            fh.write(FAKE_GH)
        os.chmod(gh, 0o755)
        env = {"PATH": bindir + os.pathsep + os.environ.get("PATH", "")}
        env.update(extra_env)
        with mock.patch.dict(os.environ, env):
            yield bindir
    finally:
        shutil.rmtree(bindir, ignore_errors=True)


#: What a tag-time check needs to find in an export tree, seeded into any
#: fixture that exercises a TAGGED push: the tree's own readiness gate (row
#: E67) and a README naming a prove command that really runs there (row
#: E70). Kept minimal on purpose: the real gate reads this estate's whole
#: evidence set, which a five file fixture cannot carry, and what is under
#: test here is the exporter's refusal, never that gate's own verdict.
READY_GATE_STUB = ("#!/usr/bin/env python3\n"
                   "print('GATE: every critical item is proven')\n")
NOT_READY_GATE_STUB = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "print('GATE: NOT READY. 1 critical item(s) unproven: "
    "Restore drill (NO-DATA)')\n"
    "sys.exit(1)\n")
#: What the REAL gate prints, measured from scripts/readiness_gate.py's own
#: main() on 2026-09-04: the GATE: line is bare, and the items are one
#: "  - Title (VERDICT)" line each underneath. The stub above puts the item
#: on the GATE: line itself, which is why the refusal LOOKED like it named
#: what was unproven while the real refusal named nothing but "NOT READY."
#: and the operator had to rebuild the export tree by hand to find out.
REAL_SHAPE_NOT_READY_GATE_STUB = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "print('GATE: NOT READY.')\n"
    "print('  1 critical item(s) unproven:')\n"
    "print('  - Restore drill (NO-DATA)')\n"
    "sys.exit(1)\n")


def _seed_tag_time_needs(root, gate=READY_GATE_STUB, readme=None,
                         prove_exit=0):
    """The three files a tagged export tree must carry for tag_time_checks
    to reach a verdict: its own readiness gate, a README naming a prove
    command, and that command's script. Written into a fixture ROOT (so the
    allowlist carries them into the export tree) or straight into an export
    tree. A caller writing into a fixture root re-tracks afterwards."""
    scripts = os.path.join(root, "scripts")
    os.makedirs(scripts, exist_ok=True)
    with open(os.path.join(scripts, "readiness_gate.py"), "w",
              encoding="utf-8") as fh:
        fh.write(gate)
    with open(os.path.join(scripts, "test_fixture_prove.py"), "w",
              encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\nimport sys\n"
                 "print('fixture prove')\nsys.exit(%d)\n" % prove_exit)
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(readme if readme is not None else
                 "# Fixture\n\nProve it with "
                 "`python3 scripts/test_fixture_prove.py`.\n")


def _write_tree_manifest(export_dir, version="9.9.9"):
    """Row E110, for a test that hands tag_time_checks an export tree it
    built by hand rather than one the exporter produced: write the manifest
    that describes exactly this directory. Stable, because the manifest
    lives under docs/releases/ and that prefix is outside it."""
    import reproduce_export as RE
    text = RE.manifest_from_dir(export_dir)
    releases = os.path.join(export_dir, "docs", "releases")
    os.makedirs(releases, exist_ok=True)
    with open(os.path.join(releases, "%s.export-manifest.txt" % version),
              "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def _seed_export_manifest(root, allowlist, version="9.9.9"):
    """Row E110: from now on a TAGGED export tree must carry
    docs/releases/<version>.export-manifest.txt describing its own bytes,
    so every fixture whose tagged push is meant to SUCCEED has to seed one.
    Built the way a real cut builds it, by running the exporter's own
    build_export_tree over `root` and hashing the result, never by hand:
    the export tree regenerates each product's CHECKSUMS.sha256, and a
    hand-written manifest would be describing a tree the exporter does not
    produce. Call it LAST, after every other fixture file is written; it
    tracks `root` on the way in (build_export_tree walks git ls-files) and
    again on the way out. Writing the manifest does not change the answer,
    because docs/releases/ is outside the manifest by construction."""
    import reproduce_export as RE
    _git_track_all(root)
    dest = tempfile.mkdtemp(prefix="fixture-export-manifest-")
    try:
        EP.build_export_tree(dest, allowlist, root)
        text = RE.manifest_from_dir(dest)
    finally:
        shutil.rmtree(dest, ignore_errors=True)
    path = os.path.join(root, "docs", "releases",
                        "%s.export-manifest.txt" % version)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    _git_track_all(root)
    return text


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


#: A shape SECRET_SHAPES matches (scripts/pre_push_gate.py: ghp_ + 36
#: alphanumerics). Fake in the same sense as the estate's own private-term
#: fixtures: real enough to match the pattern under test, invented for
#: this file, never a value that ever authenticated anything.
FAKE_GHP_SECRET = "ghp_" + "a" * 36


class AFourthGateCatchesASecretShapedValueTheOtherThreeMiss(unittest.TestCase):
    """The security gap this closes: push_appended pushes through a plain
    temp `git init` with no pre-push hook installed, so
    scripts/pre_push_gate.py's own SECRET_SHAPES check never runs on this
    route at all, and none of cleanse.sh, identity_guard.py or
    private_terms_scan.py look for a credential shape. check_secrets is
    the fourth gate run_gates now composes, over the same SECRET_SHAPES
    patterns pre_push_gate.py already carries, against the candidate
    export tree itself."""

    def test_a_ghp_shaped_value_in_an_allowlisted_file_refuses_and_names_it(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            _make_fake_root(root, {
                "leaky.md": "the token is %s, do not ship it\n"
                            % FAKE_GHP_SECRET,
            })
            allowlist_path = _write_lines(
                os.path.join(root, "ALLOWLIST.txt"), ["scripts", "leaky.md"])
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            proc = _run_cli(["--allowlist", allowlist_path, "--root", root,
                              "--remote", remote_dir, "--branch", "main",
                              "--dry-run"], env)
            self.assertEqual(proc.returncode, EP.EXIT_REFUSED,
                              proc.stdout + proc.stderr)
            self.assertIn("REFUSED: a secret-shaped value was found in "
                          "leaky.md", proc.stdout)
            # the finding names the file; the value itself never appears
            self.assertNotIn(FAKE_GHP_SECRET, proc.stdout)
            self.assertNotIn(FAKE_GHP_SECRET, proc.stderr)

    def test_the_public_aws_example_key_passes_and_any_other_aws_shape_refuses(self):
        # The one value in KNOWN_PUBLIC_EXAMPLE_VALUES is the access key id
        # AWS prints in its own documentation; the products' detection
        # fixtures and a changelog reproduce it on purpose. A VALUE
        # allowlist, never a path one: the same shape with any other
        # value still refuses.
        example = EP.KNOWN_PUBLIC_EXAMPLE_VALUES[0]
        other = "AKIA" + "Q" * 16
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "fixture.md": "the documented example is %s\n" % example})
            EP.build_orphan_commit(export_dir, ["scripts", "fixture.md"],
                                   root=root)
            ok, lines = EP.check_secrets(export_dir)
            self.assertTrue(ok, lines)
            self.assertTrue(any(l.startswith("secrets: 0 hit(s)")
                                for l in lines), lines)
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "leaky.md": "the key is %s\n" % other})
            EP.build_orphan_commit(export_dir, ["scripts", "leaky.md"],
                                   root=root)
            ok, lines = EP.check_secrets(export_dir)
            self.assertFalse(ok, lines)
            self.assertIn("leaky.md", "\n".join(lines))
            self.assertNotIn(other, "\n".join(lines))

    def test_a_clean_tree_prints_the_zero_hit_count(self):
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as identity_dir, \
             tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "public.md": "hello, nothing private here\n"})
            EP.build_orphan_commit(export_dir, ["scripts", "public.md"],
                                   root=root)
            subprocess.run(["git", "init", "-q"], cwd=identity_dir,
                            check=True)
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            with mock.patch.dict(os.environ,
                                  {"BROTHER_PRIVATE_TERMS": terms_path}):
                all_ok, lines = EP.run_gates(export_dir, identity_dir)
            secrets_lines = [l for l in lines if l.startswith("secrets: ")]
            self.assertEqual(len(secrets_lines), 1, lines)
            m = re.match(r"secrets: 0 hit\(s\) over (\d+) file\(s\)$",
                         secrets_lines[0])
            self.assertIsNotNone(m, secrets_lines[0])
            self.assertGreater(int(m.group(1)), 0, secrets_lines[0])

    def test_secret_check_is_load_bearing_in_the_composed_all_ok(self):
        """check_secrets alone must flip all_ok, the same load-bearing
        proof EachGateOfRunGatesIsIndividuallyLoadBearing runs for the
        other three: a fixture where cleanse.sh, identity_guard.py and
        private_terms_scan.py all clear, and only the secret shape
        refuses."""
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as identity_dir, \
             tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "leaky.md": "token: %s\n" % FAKE_GHP_SECRET})
            EP.build_orphan_commit(export_dir, ["scripts", "leaky.md"],
                                   root=root)
            subprocess.run(["git", "init", "-q"], cwd=identity_dir,
                            check=True)
            terms_path = _write_lines(
                os.path.join(root, "terms.txt"), ["FAKETERM-NEVER-PRESENT"])
            with mock.patch.dict(os.environ,
                                  {"BROTHER_PRIVATE_TERMS": terms_path}):
                all_ok, lines = EP.run_gates(export_dir, identity_dir)
            self.assertFalse(all_ok, lines)
            cleanse_line = next(l for l in lines if l.startswith("cleanse:"))
            terms_line = next(l for l in lines
                               if l.startswith("private_terms_scan:"))
            self.assertIn("exit 0,", cleanse_line, lines)
            self.assertIn("exit 0,", terms_line, lines)
            self.assertTrue(
                any(l.startswith("REFUSED: a secret-shaped value")
                    for l in lines), lines)
            self.assertNotIn(FAKE_GHP_SECRET, "\n".join(lines))

    def test_a_file_it_cannot_read_is_a_refusal_naming_that_file(self):
        """The fail-closed OSError branch, shipped in a merge that touched
        no test file at all. This is the publication trust boundary: a file
        the scanner could not open was never scanned, so a silent skip
        would let it leave through the export unchecked. Driven both ways
        in one method, because "it refused" means nothing without the same
        tree passing when the file is readable."""
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as identity_dir, \
             tempfile.TemporaryDirectory() as root:
            _make_fake_root(root, {
                "opaque.md": "nothing secret here at all\n"})
            EP.build_orphan_commit(export_dir, ["scripts", "opaque.md"],
                                   root=root)
            target = os.path.join(export_dir, "opaque.md")
            # the control: readable, and this tree is otherwise clean
            ok, lines = EP.check_secrets(export_dir)
            self.assertTrue(ok, lines)
            os.chmod(target, 0o000)
            try:
                if os.access(target, os.R_OK):
                    self.skipTest(
                        "NO-DATA: this process reads a mode 000 file (root, "
                        "or a filesystem ignoring the mode), so the "
                        "unreadable branch cannot be driven here")
                ok, lines = EP.check_secrets(export_dir)
                self.assertFalse(ok, lines)
                self.assertTrue(
                    any(l.startswith("secrets: NO-DATA, could not read "
                                     "opaque.md") for l in lines), lines)
                self.assertIn("was not fully scanned for secrets",
                              "\n".join(lines))
                # and it is load bearing in the composed verdict, not
                # merely a line printed beside a PASS
                subprocess.run(["git", "init", "-q"], cwd=identity_dir,
                               check=True)
                terms_path = _write_lines(
                    os.path.join(root, "terms.txt"),
                    ["FAKETERM-NEVER-PRESENT"])
                with mock.patch.dict(os.environ,
                                     {"BROTHER_PRIVATE_TERMS": terms_path}):
                    all_ok, lines = EP.run_gates(export_dir, identity_dir)
                self.assertFalse(all_ok, lines)
            finally:
                # restored inside the fixture's own lifetime: an addCleanup
                # would fire after TemporaryDirectory had removed the file
                os.chmod(target, 0o644)


class CheckSecretsScansOnlyLinesAddedSinceTheBaseline(unittest.TestCase):
    """check_secrets(export_dir, baseline_dir): baseline_dir is
    build_baseline_dir's checkout of the public remote's current branch
    tip. The problem this closes: a value allowlist of 21 named fakes is
    the wrong shape for the products' own credential-detection fixtures, a
    changelog reproducing a documented example, and docs that teach the
    scanner, all of which legitimately carry a secret-shaped value the
    public tree already ships. Scoping the scan to lines ADDED since the
    baseline (the same "outgoing range, not the whole tree" reasoning
    pre_push_gate.py already applies to the hub's own push) means a
    long-standing fixture line never refuses merely for still being there,
    while a genuinely new value, in an old file or a brand new one, still
    does."""

    def test_a_baseline_line_repeated_unchanged_in_the_candidate_passes(self):
        fixture_line = ("the token is %s, a long-standing fixture\n"
                         % FAKE_GHP_SECRET)
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as baseline_dir:
            with open(os.path.join(baseline_dir, "leaky.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(fixture_line)
            with open(os.path.join(export_dir, "leaky.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(fixture_line)
            ok, lines = EP.check_secrets(export_dir, baseline_dir)
            self.assertTrue(ok, lines)
            self.assertTrue(any(l.startswith("secrets: 0 hit(s)")
                                for l in lines), lines)

    def test_a_new_line_added_to_an_old_file_refuses_and_names_it(self):
        new_secret = "ghp_" + "c" * 36
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as baseline_dir:
            with open(os.path.join(baseline_dir, "leaky.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("nothing secret here\n")
            with open(os.path.join(export_dir, "leaky.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("nothing secret here\ntoken: %s\n" % new_secret)
            ok, lines = EP.check_secrets(export_dir, baseline_dir)
            self.assertFalse(ok, lines)
            self.assertIn("leaky.md", "\n".join(lines))
            self.assertNotIn(new_secret, "\n".join(lines))

    def test_a_brand_new_file_carrying_a_secret_shape_refuses(self):
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as baseline_dir:
            # baseline_dir carries no file at this path at all: a new file
            # scans whole, exactly like the no-baseline case.
            with open(os.path.join(export_dir, "brand-new.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("token: %s\n" % FAKE_GHP_SECRET)
            ok, lines = EP.check_secrets(export_dir, baseline_dir)
            self.assertFalse(ok, lines)
            self.assertIn("brand-new.md", "\n".join(lines))
            self.assertNotIn(FAKE_GHP_SECRET, "\n".join(lines))

    def test_with_no_baseline_dir_the_whole_candidate_file_is_scanned(self):
        with tempfile.TemporaryDirectory() as export_dir:
            with open(os.path.join(export_dir, "leaky.md"), "w",
                      encoding="utf-8") as fh:
                fh.write("token: %s\n" % FAKE_GHP_SECRET)
            ok, lines = EP.check_secrets(export_dir)
            self.assertFalse(ok, lines)
            self.assertIn("leaky.md", "\n".join(lines))
            self.assertNotIn(FAKE_GHP_SECRET, "\n".join(lines))

    def test_an_already_public_value_on_a_line_changed_for_another_reason_passes(self):
        # Measured 2026-09-04 against the real dry run: a line can carry
        # TWO independent secret-shaped values (here a ghp_ shaped one and
        # an sk- shaped one). Editing only the ghp_ one makes the WHOLE
        # line read as added under a line-level diff, and the untouched
        # sk- value rides along. That sk- value already sat, verbatim, in
        # the baseline; it must not refuse just because its neighbour on
        # the same line changed. A genuinely new ghp_ value on that same
        # line still refuses.
        old_ghp = "ghp_" + "d" * 36
        new_ghp = "ghp_" + "e" * 36
        already_public_sk = "sk-" + "f" * 30
        with tempfile.TemporaryDirectory() as export_dir, \
             tempfile.TemporaryDirectory() as baseline_dir:
            with open(os.path.join(baseline_dir, "fixture.py"), "w",
                      encoding="utf-8") as fh:
                fh.write('PATH = "/x/%s/%s/y"\n' % (already_public_sk, old_ghp))
            with open(os.path.join(export_dir, "fixture.py"), "w",
                      encoding="utf-8") as fh:
                fh.write('PATH = "/x/%s/%s/y"\n' % (already_public_sk, new_ghp))
            ok, lines = EP.check_secrets(export_dir, baseline_dir)
            self.assertFalse(ok, lines)
            self.assertIn("fixture.py", "\n".join(lines))
            self.assertNotIn(new_ghp, "\n".join(lines))


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


class TheAllowlistNamesSbeLeavesNotTheBareDirectory(unittest.TestCase):
    """Security finding, 2026-09-04: a bare `.sbe` line in
    docs/plan/EXPORT-ALLOWLIST.txt swept in whatever the hub happens to
    track under that directory later, with no review at export time. The
    real allowlist now names the tracked leaf paths individually; a new
    file added under .sbe in future must never export until it, too, is
    named."""

    def test_the_real_allowlist_has_no_bare_sbe_entry(self):
        entries = EP.load_allowlist()
        self.assertNotIn(".sbe", entries)
        self.assertTrue(any(e.startswith(".sbe/") for e in entries), entries)

    def test_an_unlisted_new_file_under_sbe_does_not_export(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            _make_fake_root(root, {
                ".sbe/kept.json": "{}\n",
                ".sbe/new-untracked-by-the-allowlist.json": "{}\n",
            })
            allowlist = ["scripts", ".sbe/kept.json"]  # the new file unlisted
            copied = EP.build_export_tree(dest, allowlist, root=root)
            self.assertIn(".sbe/kept.json", copied)
            self.assertTrue(
                os.path.isfile(os.path.join(dest, ".sbe", "kept.json")))
            self.assertFalse(os.path.exists(os.path.join(
                dest, ".sbe", "new-untracked-by-the-allowlist.json")))


class E120TheAllowlistNamesEveryPathExactlyOnce(unittest.TestCase):
    """Row E120, measured by lane MERGE-4 on hub/main: nine paths appeared
    twice in docs/plan/EXPORT-ALLOWLIST.txt, eight of them because the
    2026-09-04 link-closure round added the same eight guide pages a second
    time under a second comment block. A duplicate exports no extra file
    (build_export_tree copies each entry's tracked files over the same
    destination), which is exactly why nobody noticed: the only visible
    effect was the dry run's own 'file/path entries' count, which counts
    contributing ENTRIES and so read nine too high. The cost is to the
    reader, who cannot tell which of two comment blocks is the reason a
    path ships, and to the next person narrowing the list, who deletes one
    copy and believes the path is gone."""

    def _entries_with_line_numbers(self):
        with open(EP.DEFAULT_ALLOWLIST, encoding="utf-8") as fh:
            lines = fh.readlines()
        seen = {}
        for number, line in enumerate(lines, 1):
            text = line.strip()
            if text and not text.startswith("#"):
                seen.setdefault(text, []).append(number)
        return seen

    def test_no_path_is_listed_twice(self):
        seen = self._entries_with_line_numbers()
        duplicates = {path: at for path, at in seen.items() if len(at) > 1}
        self.assertEqual(
            duplicates, {},
            "docs/plan/EXPORT-ALLOWLIST.txt lists %d path(s) more than "
            "once: %s" % (len(duplicates), "; ".join(
                "%s at lines %s" % (path, ", ".join(str(n) for n in at))
                for path, at in sorted(duplicates.items()))))

    def test_the_loaded_allowlist_has_no_repeated_entry(self):
        # load_allowlist is what the exporter actually walks, so the rule is
        # asserted on its output too, not only on the file's text.
        entries = EP.load_allowlist()
        self.assertIsNotNone(entries)
        self.assertEqual(len(entries), len(set(entries)))


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
        _git_track_all(root)  # re-track: checksums.sh and CHECKSUMS.sha256
        # were written after _make_fake_root's own tracking pass above

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


class ThePublishedFiguresDescribeTheExportedTree(unittest.TestCase):
    """readiness row E113: a product's docs/CITATIONS.md and the lint counts
    in its SKILL.md are computed over the WHOLE hub, where design/, program/
    and the internal docs the allowlist withholds do most of the citing.
    Copied verbatim into an export carrying a subset, every entry only a
    withheld document cited becomes a stale entry, and that product's own
    citation-inventory check FAILs at gate severity in every public clone
    (measured on the real tree: 87 of 137 entries, and a SKILL.md quoting
    162 lint-clean files over a tree holding 151). regenerate_published_
    figures must recompute both over the export tree, with the product's
    own code, before the manifests are rebuilt over the result."""

    #: The product's own scorer, borrowed whole: these figures must be
    #: pruned by exactly the code that later reads them, never by a second
    #: implementation of "which URLs does this tree cite" living here.
    REAL_SCORER = os.path.join(EP.ROOT, "products", "brothersbe", "tools",
                               "sbe_score.py")

    KEPT_URL = "https://example.com/kept"
    WITHHELD_URL = "https://example.com/withheld"

    #: Named individually, exactly as the real EXPORT-ALLOWLIST.txt names
    #: each kept path under a product and never the product wholesale, so
    #: products/myproduct/ci is withheld the way products/brothersbe/ci is.
    ALLOWLIST = ["scripts", "products/myproduct/tools",
                 "products/myproduct/scripts", "products/myproduct/SKILL.md",
                 "products/myproduct/kept.md", "products/myproduct/docs",
                 "products/myproduct/CHECKSUMS.sha256"]

    def _entry(self, url, claim):
        return ("## %s\n"
                "- claim: %s\n"
                "- population: the fixture tree this test builds\n"
                "- date: captured 2026-09-04\n"
                "- limit: a fixture, not a measurement of anything real\n\n"
                % (url, claim))

    def _seed(self, root):
        """A miniature product: two markdown documents citing one URL each,
        one of them under the withheld ci/, an inventory covering both, a
        SKILL.md carrying the lint sentence, and enough Python for the lint
        to reach its PASS branch (one clean file, one waived hit) on each
        side of the export boundary."""
        _make_fake_root(root, {
            "products/myproduct/kept.md":
                "The shipped page cites %s for its claim.\n" % self.KEPT_URL,
            "products/myproduct/ci/internal.md":
                "Internal QA notes cite %s and ship to nobody.\n"
                % self.WITHHELD_URL,
            "products/myproduct/docs/CITATIONS.md":
                "# Citation inventory\n\nOne entry per external URL this "
                "fixture's shipped documentation cites.\n\n"
                + self._entry(self.KEPT_URL, "the shipped page rests on it")
                + self._entry(self.WITHHELD_URL,
                              "only the withheld QA note rests on it"),
            "products/myproduct/SKILL.md":
                "# Fixture skill\n\nThis repository's own run has 92 waived "
                "hits and 162 files that were scanned and genuinely found "
                "clean, both recomputed by an eval.\n",
            "products/myproduct/tools/clean_helper.py":
                "def add(a, b):\n    return a + b\n",
            # One waived hit, because the lint prints the two counts this
            # sentence quotes only on the branch where a suppression exists.
            "products/myproduct/tools/waived_helper.py":
                "def read(path):\n"
                "    try:\n"
                "        return open(path).read()\n"
                "    except:  # sbe: allow-silent fixture, reader only\n"
                "        return ''\n",
            "products/myproduct/ci/withheld_helper.py":
                "def sub(a, b):\n    return a - b\n",
        })
        os.makedirs(os.path.join(root, "products", "myproduct", "scripts"),
                    exist_ok=True)
        shutil.copy2(REAL_PRODUCT_CHECKSUMS_SH,
                      os.path.join(root, "products", "myproduct", "scripts",
                                   "checksums.sh"))
        shutil.copy2(self.REAL_SCORER,
                      os.path.join(root, "products", "myproduct", "tools",
                                   "sbe_score.py"))
        # Stale on purpose, exactly the shape docs/RELEASE.md step 4 leaves
        # behind: generated over the whole product, naming the withheld
        # markdown the export never ships.
        _write_lines(os.path.join(root, "products", "myproduct",
                                  "CHECKSUMS.sha256"), [
            "%s  kept.md" % ("0" * 64),
            "%s  ci/internal.md" % ("0" * 64),
            "%s  SKILL.md" % ("0" * 64),
            "%s  docs/CITATIONS.md" % ("0" * 64),
        ])
        _git_track_all(root)

    def _check_inventory(self, module, root):
        """The product's own citation-inventory check, over `root`."""
        saved = os.environ.get("SBE_CITATION_ROOT")
        os.environ["SBE_CITATION_ROOT"] = root
        try:
            return module.check_citation_inventory()
        finally:
            if saved is None:
                os.environ.pop("SBE_CITATION_ROOT", None)
            else:
                os.environ["SBE_CITATION_ROOT"] = saved

    def test_the_exported_inventory_names_only_urls_the_export_cites(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            self._seed(root)
            source_inv = os.path.join(root, "products", "myproduct", "docs",
                                      "CITATIONS.md")
            with open(source_inv, encoding="utf-8") as fh:
                source_text = fh.read()
            self.assertIn(self.WITHHELD_URL, source_text)

            EP.build_export_tree(dest, self.ALLOWLIST, root=root)
            product = os.path.join(dest, "products", "myproduct")
            exported_inv = os.path.join(product, "docs", "CITATIONS.md")
            with open(exported_inv, encoding="utf-8") as fh:
                exported_text = fh.read()
            self.assertIn(self.KEPT_URL, exported_text)
            self.assertNotIn(self.WITHHELD_URL, exported_text)
            # The hub's own copy is untouched: it describes the hub, which
            # is the tree it is true about.
            with open(source_inv, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), source_text)

            module = EP._load_product_module(
                os.path.join(product, "tools", "sbe_score.py"),
                "test_export_scorer")
            self.assertIsNotNone(module)
            verdict, evidence = self._check_inventory(module, product)
            self.assertEqual(verdict, "PASS", evidence)

            # DRIVEN BACKWARDS on the same tree: put the hub's inventory
            # back, which is exactly what a plain copy shipped before this
            # step existed, and the product's own gate refuses.
            with open(exported_inv, "w", encoding="utf-8") as fh:
                fh.write(source_text)
            verdict, evidence = self._check_inventory(module, product)
            self.assertEqual(verdict, "FAIL", evidence)
            self.assertIn(self.WITHHELD_URL, evidence)

    def test_the_exported_skill_states_the_export_trees_own_lint_run(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            self._seed(root)
            EP.build_export_tree(dest, self.ALLOWLIST, root=root)
            product = os.path.join(dest, "products", "myproduct")
            with open(os.path.join(product, "SKILL.md"), encoding="utf-8") as fh:
                exported = fh.read()
            # The export ships one clean file and one waived hit; the hub
            # tree holds a second clean file under the withheld ci/. The
            # pinned 162 was a claim about a population this tree is not.
            self.assertIn("1 waived hits and 1 files that were scanned and "
                          "genuinely found clean", exported)
            self.assertNotIn("162 files", exported)
            with open(os.path.join(root, "products", "myproduct", "SKILL.md"),
                      encoding="utf-8") as fh:
                self.assertIn("162 files", fh.read())

    def test_the_regenerated_manifest_hashes_the_pruned_inventory(self):
        """The manifests are rebuilt AFTER the figures move, or the export
        ships a manifest that disagrees with the bytes beside it, which is
        the same defect one level down."""
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            self._seed(root)
            EP.build_export_tree(dest, self.ALLOWLIST, root=root)
            product = os.path.join(dest, "products", "myproduct")
            named = {}
            with open(os.path.join(product, "CHECKSUMS.sha256"),
                      encoding="utf-8") as fh:
                for line in fh:
                    line = line.rstrip("\n")
                    if line:
                        named[line[66:]] = line[:64]
            for rel in ("docs/CITATIONS.md", "SKILL.md"):
                digest = hashlib.sha256()
                with open(os.path.join(product, rel), "rb") as fh:
                    digest.update(fh.read())
                self.assertEqual(named.get(rel), digest.hexdigest(), rel)

    def test_an_empty_scan_never_empties_the_inventory(self):
        """A scan that opened no citing document is not evidence that every
        entry is stale, and pruning against one would delete scope a reader
        still needs. The refusal is the whole point of the guard."""
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "docs"))
            body = self._entry(self.KEPT_URL, "cited by nothing here")
            with open(os.path.join(base, "docs", "CITATIONS.md"), "w",
                      encoding="utf-8") as fh:
                fh.write(body)
            module = EP._load_product_module(self.REAL_SCORER,
                                             "test_empty_scan_scorer")
            self.assertIsNotNone(module)
            self.assertFalse(EP.prune_citation_inventory(base, module))
            with open(os.path.join(base, "docs", "CITATIONS.md"),
                      encoding="utf-8") as fh:
                self.assertEqual(fh.read(), body)


class TheCommitIsExactlyTheGatedTree(unittest.TestCase):
    """THE ALLOWLIST AND DENYLIST ARE THE ONLY FILTERS (module docstring).
    Measured 2026-09-02 on the public v1.0.0 tag itself: two tracked CSV
    fixtures (products/brothermode/tools/fixtures/queue-export-*.csv,
    force-added past the hub's root .gitignore's `*.csv` line) were copied
    by the allowlist walk, scanned by every gate, and listed in the
    regenerated CHECKSUMS.sha256, then silently left out of the pushed
    commit by a plain `git add -A`, which obeyed that same COPIED
    .gitignore once it landed at the export root. A fresh clone's own
    verify-install.sh then printed "449 file(s) match, 0 mismatched, 2
    missing" and FAILED: the manifest described a tree the commit did not
    ship. Reproduced here with a miniature product and one fixture .csv,
    borrowing the real checksums.sh and verify-install.sh, same fixture
    shape as TheExportedProductManifestDescribesTheExportedBytes and
    ATagRefusesAnExportTreeItsOwnProductsCannotVerify above.

    DRIVEN BACKWARDS: against `git show 5e58ffe7:scripts/export_public.py`
    (the pre-fix baseline, saved to a temp path and run against this exact
    fixture outside this suite, since that file must stay broken forever
    and cannot be imported into a suite that has to pass) this fixture's
    clone lacked tracked.csv and verify-install printed "1 missing" and
    FAILED. Against the fixed export_public.py below, the clone holds the
    file and verify-install PASSES with "0 missing"."""

    ALLOWLIST = ["scripts", ".gitignore", "products/myproduct"]

    def _seed_product(self, root):
        _make_fake_root(root, {
            ".gitignore": "*.csv\n",
            "products/myproduct/kept.md": "kept content, shipped\n",
            "products/myproduct/tracked.csv": "a,b,c\n1,2,3\n",
        })
        scripts = os.path.join(root, "products", "myproduct", "scripts")
        os.makedirs(scripts, exist_ok=True)
        shutil.copy2(REAL_PRODUCT_CHECKSUMS_SH,
                     os.path.join(scripts, "checksums.sh"))
        shutil.copy2(REAL_PRODUCT_VERIFY_INSTALL_SH,
                     os.path.join(scripts, "verify-install.sh"))
        placeholder_hash = "0" * 64
        _write_lines(
            os.path.join(root, "products", "myproduct", "CHECKSUMS.sha256"), [
                "%s  kept.md" % placeholder_hash,
                "%s  tracked.csv" % placeholder_hash,
            ])
        _git_track_all(root)  # re-track: scripts/*.sh and CHECKSUMS.sha256
        # were written after _make_fake_root's own tracking pass above

    def test_a_csv_matched_by_the_copied_gitignore_reaches_the_pushed_commit(self):
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as clone_dir:
            _seed_bare_remote(remote_dir)
            self._seed_product(root)

            # regenerate_product_manifests must have listed tracked.csv,
            # exactly as it did in production, before this test can prove
            # anything about the commit that follows it.
            with tempfile.TemporaryDirectory() as probe:
                EP.build_export_tree(probe, self.ALLOWLIST, root=root)
                manifest_path = os.path.join(
                    probe, "products", "myproduct", "CHECKSUMS.sha256")
                with open(manifest_path, encoding="utf-8") as fh:
                    self.assertIn("tracked.csv", fh.read())

            with _fake_gh():
                code, lines = EP.push_appended(self.ALLOWLIST, remote_dir,
                                               "main", root=root)
            self.assertEqual(code, EP.EXIT_OK, lines)

            subprocess.run(["git", "clone", "-q", remote_dir, clone_dir],
                            check=True, capture_output=True, text=True)
            self.assertTrue(
                os.path.isfile(os.path.join(clone_dir, "products",
                                             "myproduct", "tracked.csv")),
                "tracked.csv did not reach the pushed commit")

            proc = subprocess.run(
                ["bash", "scripts/verify-install.sh"],
                cwd=os.path.join(clone_dir, "products", "myproduct"),
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("0 missing", proc.stdout)
            self.assertIn("PASSED", proc.stdout)

    def test_an_untracked_file_under_an_allowlisted_path_never_ships(self):
        """2026-09-03, the second half of the same root cause: force-adding
        (-f) at the three git-add sites, with no other change, would ship
        every gitignored build artifact and runtime file physically present
        under an allowlisted path (bundle/runtime/__pycache__/*.pyc, .sbe/
        tasks.json and friends), because the old copy step walked the raw
        filesystem, not the hub's own index. build_export_tree now copies
        only what `git ls-files` reports tracked; an untracked file under an
        allowlisted directory, or an untracked file at an allowlisted root
        path, must reach neither the candidate tree nor the pushed commit,
        whether or not `git add -A -f` runs on the far side."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as clone_dir:
            _seed_bare_remote(remote_dir)
            self._seed_product(root)
            allowlist = self.ALLOWLIST + [".sbe"]

            # an untracked build artifact under the allowlisted product dir
            pycache = os.path.join(root, "products", "myproduct",
                                    "__pycache__")
            os.makedirs(pycache, exist_ok=True)
            with open(os.path.join(pycache, "x.pyc"), "wb") as fh:
                fh.write(b"\x00fake bytecode\x00")
            # an untracked runtime file at a newly allowlisted root path
            sbe_dir = os.path.join(root, ".sbe")
            os.makedirs(sbe_dir, exist_ok=True)
            with open(os.path.join(sbe_dir, "tasks.json"), "w",
                      encoding="utf-8") as fh:
                fh.write("{}")
            # neither is git-added: both stay untracked, on purpose

            with tempfile.TemporaryDirectory() as probe:
                EP.build_export_tree(probe, allowlist, root=root)
                self.assertFalse(os.path.exists(os.path.join(
                    probe, "products", "myproduct", "__pycache__")))
                self.assertFalse(os.path.exists(os.path.join(probe, ".sbe")))

            with _fake_gh():
                code, lines = EP.push_appended(allowlist, remote_dir, "main",
                                               root=root)
            self.assertEqual(code, EP.EXIT_OK, lines)

            subprocess.run(["git", "clone", "-q", remote_dir, clone_dir],
                            check=True, capture_output=True, text=True)
            self.assertFalse(os.path.exists(os.path.join(
                clone_dir, "products", "myproduct", "__pycache__")))
            self.assertFalse(os.path.exists(os.path.join(clone_dir, ".sbe")))
            # the tracked csv, unrelated to this test's untracked files,
            # still ships: this is about what stays out, not a new hole
            self.assertTrue(os.path.isfile(os.path.join(
                clone_dir, "products", "myproduct", "tracked.csv")))

    def test_a_tracked_file_the_denylist_names_still_does_not_ship(self):
        """The denylist still withholds a file even though the hub tracks
        it: THE ALLOWLIST AND DENYLIST ARE THE ONLY FILTERS, and this is
        the denylist half. The withheld path is injected by patching
        load_denylist rather than by writing a denylist into the fixture
        root, so this test stays about the withholding itself; WHERE the
        denylist is read from is row E118's subject and is driven in
        E118TheDenylistComesFromTheTreeBeingExported below."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as clone_dir:
            _seed_bare_remote(remote_dir)
            self._seed_product(root)
            withheld = os.path.join(root, "products", "myproduct",
                                    "withheld.md")
            with open(withheld, "w", encoding="utf-8") as fh:
                fh.write("hub tracks this; the denylist withholds it\n")
            _git_track_all(root)

            with _fake_gh(), mock.patch.object(
                    EP, "load_denylist",
                    return_value=["products/myproduct/withheld.md"]):
                code, lines = EP.push_appended(self.ALLOWLIST, remote_dir,
                                               "main", root=root)
            self.assertEqual(code, EP.EXIT_OK, lines)

            subprocess.run(["git", "clone", "-q", remote_dir, clone_dir],
                            check=True, capture_output=True, text=True)
            self.assertFalse(os.path.isfile(os.path.join(
                clone_dir, "products", "myproduct", "withheld.md")))
            # tracked and never denylisted: still ships alongside it
            self.assertTrue(os.path.isfile(os.path.join(
                clone_dir, "products", "myproduct", "tracked.csv")))


class E118TheDenylistComesFromTheTreeBeingExported(unittest.TestCase):
    """Row E118, measured by lane X7-FIX: build_export_tree read the
    allowlist from the tree it was handed and the denylist from whatever
    checkout the module happened to be imported from. The two filters are
    the same kind of input, so a rebuild of an older revision
    (reproduce_export.py --source-rev) applied yesterday's file list with
    today's withholdings: a path denied since the cut was withheld from a
    rebuild of a release that shipped it, and the reproduction read a
    mismatch that was never in the release. Both directions are driven
    here, because only one of them is about the fixture and the other is
    about this repository leaking into a foreign root."""

    ALLOWLIST = ["products/myproduct"]

    def _seed(self, root, denylist_lines=None):
        files = {
            "products/myproduct/tracked.md": "ships\n",
            "products/myproduct/withheld.md": "the fixture withholds this\n",
        }
        if denylist_lines is not None:
            files["docs/plan/EXPORT-DENYLIST.txt"] = (
                "# the fixture's own withholdings\n"
                + "".join(line + "\n" for line in denylist_lines))
        _make_fake_root(root, files)

    def test_the_roots_own_denylist_is_the_one_that_is_applied(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            self._seed(root, ["products/myproduct/withheld.md"])
            EP.build_export_tree(dest, self.ALLOWLIST, root=root)
            self.assertTrue(os.path.isfile(os.path.join(
                dest, "products", "myproduct", "tracked.md")))
            self.assertFalse(
                os.path.exists(os.path.join(
                    dest, "products", "myproduct", "withheld.md")),
                "the fixture root's own EXPORT-DENYLIST.txt named this "
                "path and it shipped anyway, so the denylist came from "
                "somewhere other than the tree being exported")

    def test_a_root_with_no_denylist_withholds_nothing(self):
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            self._seed(root, denylist_lines=None)
            EP.build_export_tree(dest, self.ALLOWLIST, root=root)
            for rel in ("tracked.md", "withheld.md"):
                self.assertTrue(os.path.isfile(os.path.join(
                    dest, "products", "myproduct", rel)), rel)

    def test_this_checkouts_denylist_does_not_reach_a_foreign_root(self):
        """The other direction: a path THIS repository withholds still
        ships out of a root that does not withhold it. Read from the real
        denylist rather than typed here, so the test keeps meaning when
        that file changes."""
        candidates = [rel for rel in EP.load_denylist()
                      if rel.split("/")[0] not in EP.HARD_EXCLUDE
                      and not rel.startswith("editions/")]
        if not candidates:
            self.skipTest(
                "NO-DATA: this checkout's docs/plan/EXPORT-DENYLIST.txt "
                "names no exportable path, so there is nothing that could "
                "leak into a foreign root. NO-DATA is not a pass.")
        rel = candidates[0]
        top = rel.split("/")[0]
        with tempfile.TemporaryDirectory() as root, \
             tempfile.TemporaryDirectory() as dest:
            _make_fake_root(root, {rel: "the fixture never withheld this\n"})
            copied = EP.build_export_tree(dest, [top], root=root)
            self.assertIn(top, copied)
            self.assertTrue(
                os.path.isfile(os.path.join(dest, rel)),
                "%s is withheld by THIS checkout's denylist and the export "
                "of a root that withholds nothing dropped it too" % rel)


class APushTowardThePublicRemoteFromAnEditionIsRefused(unittest.TestCase):
    """docs/plan/HUB-MIGRATION-PLAN-2026-08-30.md step 5's driven-backwards
    case, exercised against the REAL editions/personal in this checkout."""

    def test_editions_personal_cannot_push_to_the_public_remote(self):
        personal_dir = os.path.join(EP.ROOT, "editions", "personal")
        if not os.path.isdir(personal_dir):
            self.skipTest(
                "NO-DATA: no editions/personal in this checkout (a public "
                "clone ships none) so there is nothing to drive backward")
        code, msg = EG.check_push(
            "https://github.com/khalilmaaouni/Brother",
            cwd=personal_dir, env={})
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
            gh_bin = self.enterContext(_fake_gh())
            env = dict(os.environ)
            env["BROTHER_PRIVATE_TERMS"] = terms_path
            self.assertTrue(os.path.isfile(os.path.join(gh_bin, "gh")))

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
            self.enterContext(_fake_gh())
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

    ALLOWLIST = ["scripts", "clean.md", "README.md", "docs"]

    def _export_root(self, root):
        _make_fake_root(root, {"clean.md": "first export, clean\n"})
        # rows E67 and E70: cases b and e tag, and a tagged export tree
        # must carry its own readiness gate and a README whose prove
        # command really runs there
        _seed_tag_time_needs(root)
        _git_track_all(root)
        # row E110: and it must carry a manifest describing its own bytes
        _seed_export_manifest(root, self.ALLOWLIST)

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

    ALLOWLIST = ["scripts", "products/myproduct", "README.md", "docs"]

    def _seed_product(self, root, gate=None, readme=None, prove_exit=0):
        _make_fake_root(root, {
            "products/myproduct/kept.md": "kept content, shipped\n",
        })
        # rows E67 and E70: a TAGGED export tree must carry its own
        # readiness gate and a README whose prove command runs there
        _seed_tag_time_needs(root, gate=gate or READY_GATE_STUB,
                             readme=readme, prove_exit=prove_exit)
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
        _git_track_all(root)  # re-track: scripts/*.sh and CHECKSUMS.sha256
        # were written after _make_fake_root's own tracking pass above
        # row E110: a tagged export tree must also carry a manifest
        # describing its own bytes, so the cases here that expect a PASS
        # need one seeded the way a real cut writes it
        _seed_export_manifest(root, self.ALLOWLIST)

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

            with _fake_gh(), mock.patch.object(EP, "build_export_tree",
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
            with _fake_gh():
                code, lines = EP.push_appended(
                    self.ALLOWLIST, remote_dir, "main", root=root,
                    tag="v9.9.9")
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
            # rows E67 and E70: the three tag-time checks below the product
            # verifiers each print their own summary line, not merely a
            # pass/fail, so a reader can see what was actually proven.
            self.assertIn("readiness: GATE: every critical item is proven",
                          lines)
            self.assertIn("links: 0 resolved, 0 external skipped, 0 dead",
                          lines)
            self.assertIn(
                "prove: python3 scripts/test_fixture_prove.py exit 0", lines)
            self.assertTrue(any(l.startswith("TAGGED") for l in lines), lines)
            self.assertEqual(self._remote_state(remote_dir), ("2", ["v9.9.9"]))

    def _refused_tag(self, remote_dir, root):
        """A tagged push of the seeded fixture, asserted to have refused and
        to have written nothing: the seed commit stands alone and no tag
        exists. Returns the gate lines."""
        with _fake_gh():
            code, lines = EP.push_appended(
                self.ALLOWLIST, remote_dir, "main", root=root, tag="v9.9.9")
        self.assertEqual(code, EP.EXIT_REFUSED, lines)
        self.assertFalse(any(l.startswith(("PUSHED", "TAGGED"))
                             for l in lines), lines)
        self.assertEqual(self._remote_state(remote_dir), ("1", []))
        return lines

    def test_h_a_not_ready_readiness_gate_refuses_the_tagged_push(self):
        """Row E67's refusal, which NOT_READY_GATE_STUB was written for and
        which nothing ever passed to _seed_tag_time_needs: the constant sat
        in this file referenced nowhere, so the negative fixture existed and
        the code never read it. test_f above proves only the READY side, and
        a check_readiness_gate that returned (True, ...) for every tree
        would satisfy it."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._seed_product(root, gate=NOT_READY_GATE_STUB)
            lines = self._refused_tag(remote_dir, root)
            self.assertTrue(any(l.startswith(
                "REFUSED: the export tree's own readiness gate does not "
                "read READY") for l in lines), lines)
            # the gate's own verdict is quoted, so a reader sees WHAT was
            # unproven rather than merely that something was
            self.assertIn("Restore drill (NO-DATA)", "\n".join(lines))
            # fail fast: the later checks never ran
            self.assertFalse(any(l.startswith("prove: ") for l in lines),
                             lines)

    def test_h2_the_refusal_quotes_the_gates_own_failing_item_lines(self):
        """Measured 2026-09-04 on the 1.0.2 cut: the real gate prints a bare
        "GATE: NOT READY." and names its unproven items on separate lines,
        so the refusal quoted a verdict that said nothing about WHICH item
        was unproven, and the blocker had to be reproduced by hand in a
        rebuilt export tree. test_h above never caught it because its stub
        crams the item onto the GATE: line."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._seed_product(root, gate=REAL_SHAPE_NOT_READY_GATE_STUB)
            lines = self._refused_tag(remote_dir, root)
            self.assertTrue(any(l.startswith(
                "REFUSED: the export tree's own readiness gate does not "
                "read READY") for l in lines), lines)
            self.assertIn("  gate item: Restore drill (NO-DATA)", lines)

    def test_i_a_readme_prove_command_that_dies_refuses_the_tagged_push(self):
        """Row E70's exact defect, driven for the first time: a command the
        README names as its own proof, which exits non-zero in a fresh
        clone. `_seed_tag_time_needs`'s prove_exit parameter defaulted to 0
        at all five of its call sites, so this branch had never run."""
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._seed_product(root, prove_exit=1)
            lines = self._refused_tag(remote_dir, root)
            self.assertIn(
                "prove: python3 scripts/test_fixture_prove.py exit 1", lines)
            self.assertTrue(any(l.startswith(
                "REFUSED: the README names python3 "
                "scripts/test_fixture_prove.py as its own proof and it "
                "fails on the export tree") for l in lines), lines)
            # the failing command's own last line, so the refusal names what
            # broke rather than only that something did
            self.assertIn("fixture prove", "\n".join(lines))

    def test_j_a_dead_readme_link_refuses_and_a_live_one_is_counted(self):
        """Row E70's other half. The only assertion on this gate was
        "links: 0 resolved, 0 external skipped, 0 dead", which a
        check_markdown_links returning ("", []) for every tree would satisfy
        exactly: neither the resolve path nor the refusal had ever run. Both
        run here, on the same fixture, differing only in the README."""
        live = "[kept](products/myproduct/kept.md)"
        prove = "Prove it with `python3 scripts/test_fixture_prove.py`.\n"
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._seed_product(
                root, readme="# Fixture\n\n%s and [gone](docs/missing.md)\n\n%s"
                             % (live, prove))
            lines = self._refused_tag(remote_dir, root)
            self.assertIn("dead link: README.md points at docs/missing.md, "
                          "which the export tree does not carry", lines)
            # the resolving link was counted, so the refusal is about the
            # dangling target and not about the gate rejecting every link
            self.assertIn("links: 1 resolved, 0 external skipped, 1 dead",
                          lines)
            self.assertFalse(any(l.startswith("prove: ") for l in lines),
                             lines)
        with tempfile.TemporaryDirectory() as remote_dir, \
             tempfile.TemporaryDirectory() as root:
            _seed_bare_remote(remote_dir)
            self._seed_product(
                root, readme="# Fixture\n\n%s\n\n%s" % (live, prove))
            with _fake_gh():
                code, lines = EP.push_appended(
                    self.ALLOWLIST, remote_dir, "main", root=root,
                    tag="v9.9.9")
            self.assertEqual(code, EP.EXIT_OK, lines)
            self.assertIn("links: 1 resolved, 0 external skipped, 0 dead",
                          lines)
            self.assertEqual(self._remote_state(remote_dir),
                             ("2", ["v9.9.9"]))

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
            _seed_tag_time_needs(root)
            _git_track_all(root)
            # _make_fake_root stages files (git add) so build_export_tree's
            # git-ls-files walk can see them, but never commits: HEAD stays
            # unborn, so hub_head_rev still finds nothing and the stamp
            # still cannot fire, the exact 0.9.11 shape this pins.
            with _fake_gh():
                code, lines = EP.push_appended(
                    ["scripts", "docs", "README.md"], remote_dir, "main",
                    root=root, tag="v9.9.9")
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
            _seed_tag_time_needs(export_dir)
            _write_tree_manifest(export_dir)  # row E110
            ok, lines = EP.tag_time_checks(export_dir, "9.9.9")
            self.assertTrue(ok, lines)
            # no product, so no verified: line; what this pins is that the
            # quoted placeholder never produced a refusal
            self.assertFalse(any(l.startswith("REFUSED") for l in lines),
                             lines)
            self.assertFalse(any(l.startswith("verified:") for l in lines),
                             lines)


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
            _seed_tag_time_needs(root)
            # row E110: a tagged export tree must carry a manifest of its
            # own bytes, and this case's tagged push is meant to succeed
            _seed_export_manifest(root, ["scripts", "docs", "README.md"])
            self._git("init", "-q", cwd=root)
            self._git("config", "user.name", "Hub", cwd=root)
            self._git("config", "user.email", "hub@example.com", cwd=root)
            self._git("add", "-A", cwd=root)
            self._git("commit", "-q", "-m", "hub state", cwd=root)
            head = subprocess.run(
                ["git", "-C", root, "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True).stdout.strip()

            with _fake_gh():
                code, lines = EP.push_appended(
                    ["scripts", "docs", "README.md"], remote_dir, "main",
                    root=root, tag="v9.9.9")
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


class TheRealExportTreeIsWhatTheReadmeSendsAReaderTo(unittest.TestCase):
    """BO2, against the REAL allowlist and the REAL hub tree, because the
    2026-09-04 docs honesty audit found its defects in a fresh clone and
    nothing in this repository looked at the exported tree the way that
    clone did. check_markdown_links and check_readme_prove_commands landed
    with row E70 and had no test of their own, so the only thing that would
    have caught a re-narrowed allowlist was a person cutting a tag.

    Everything here is read from the export tree build_export_tree really
    produces, never from the hub checkout: the hub carries docs/plan and
    docs/for-engineers whether or not they export, which is exactly how the
    v1.0.1 defects survived a green hub."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="export-real-tree-")
        cls.tree = os.path.join(cls.tmp, "tree")
        os.makedirs(cls.tree)
        cls.copied = EP.build_export_tree(cls.tree, EP.load_allowlist())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def readme_link_closure(self, tree=None):
        """Every page reachable from the export tree's README.md by
        following relative markdown links, and every dead target found on
        the way. This is the docs honesty auditor's own scope, the front
        page and the guides it links, walked transitively so a page added
        to the allowlist without its neighbours is caught here rather than
        by the next reader."""
        tree = tree or self.tree
        seen = set()
        dead = []
        queue = ["README.md"]
        while queue:
            rel = queue.pop()
            if rel in seen:
                continue
            seen.add(rel)
            path = os.path.join(tree, rel)
            if not path.endswith(".md") or not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            base = os.path.dirname(path)
            for target in EP.MD_LINK_RE.findall(text):
                if target.startswith(("http://", "https://", "mailto:",
                                      "#")):
                    continue
                target_path = target.split("#", 1)[0]
                if not target_path:
                    continue
                full = os.path.abspath(os.path.join(base, target_path))
                root = os.path.abspath(tree)
                inside = (full == root or full.startswith(root + os.sep))
                if inside and os.path.exists(full):
                    queue.append(os.path.relpath(full, tree))
                else:
                    dead.append("%s points at %s" % (rel, target))
        return sorted(seen), dead

    def test_every_link_reachable_from_the_readme_resolves(self):
        """v1.0.1 shipped three dead links out of README.md's six, and the
        three guides it now carries link onward to eight more pages. The
        whole set has to be in the export, or a reader following the
        "Choose your path" table lands on nothing, which is what the
        2026-09-04 audit did."""
        pages, dead = self.readme_link_closure()
        self.assertEqual(dead, [], "\n".join(dead))
        self.assertGreater(len(pages), 6, pages)

    def test_the_v1_0_1_allowlist_shape_is_still_refused(self):
        """The positive control, so the test above is known to
        discriminate rather than to pass on any tree at all. The public tag
        v1.0.1 carried README.md and the two vault pages and neither guide
        directory, and its "Choose your path" table sent three of four
        readers nowhere. Built here from that same narrower allowlist, over
        the CURRENT README, the walk must still find them dead."""
        narrow = os.path.join(self.tmp, "v101")
        os.makedirs(narrow, exist_ok=True)
        EP.build_export_tree(narrow, ["README.md", "LICENSE",
                                      "docs/explanation/VAULT.md",
                                      "docs/how-to/USE-THE-VAULT.md"])
        _pages, dead = self.readme_link_closure(narrow)
        for target in ("docs/for-engineers/00-START-HERE.md",
                       "docs/for-engineers/STARTUP-WEEK.md",
                       "docs/for-analysts/00-START-HERE.md"):
            self.assertIn("README.md points at %s" % target, dead, dead)

    def test_the_whole_tree_link_check_names_only_pages_outside_that_reach(
            self):
        """check_markdown_links reads EVERY .md the export carries, and
        today it refuses on product documentation whose private siblings
        the M6 allowlist deliberately withholds (30 targets under
        products/, 2026-09-04). That is a real blocker for a tag and it is
        not this row's: what this asserts is that none of those dead links
        sits on a page a reader reaches from the front door, so the two
        problems never get confused for each other."""
        ok, lines = EP.check_markdown_links(self.tree)
        pages, _dead = self.readme_link_closure()
        reachable = set(pages)
        for line in lines:
            if not line.startswith("dead link: "):
                continue
            page = line[len("dead link: "):].split(" points at ")[0]
            self.assertNotIn(
                page, reachable,
                "a page a reader reaches from README.md carries a dead "
                "link: %s" % line)
        if ok:
            self.assertTrue(any(l.startswith("links: ") for l in lines),
                            lines)

    def test_the_readme_paths_the_choose_your_path_table_names_are_carried(
            self):
        """The exact three the auditor clicked, named here so a failure
        says which page went missing rather than only 'a link is dead'."""
        with open(os.path.join(self.tree, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for target in ("docs/for-engineers/00-START-HERE.md",
                       "docs/for-engineers/STARTUP-WEEK.md",
                       "docs/for-analysts/00-START-HERE.md"):
            self.assertIn(target, readme, target)
            self.assertTrue(os.path.isfile(os.path.join(self.tree, target)),
                            "README.md links %s and the export tree does "
                            "not carry it" % target)

    def test_every_readme_prove_command_names_a_script_the_tree_carries(self):
        """The full check runs each suite (minutes); this asserts the
        cheap half of it, that the file exists at all, for every command."""
        with open(os.path.join(self.tree, "README.md"), encoding="utf-8") as fh:
            commands = EP.readme_prove_commands(fh.read())
        self.assertTrue(commands, "README.md names no prove command")
        for command in commands:
            rel = command.split()[-1]
            self.assertTrue(os.path.isfile(os.path.join(self.tree, rel)),
                            "README.md names %s and the export tree does "
                            "not carry %s" % (command, rel))

    def test_the_battery_proof_the_readme_names_really_runs_in_the_export(
            self):
        """Row E70's own defect, driven where it happened: in a fresh clone
        of v1.0.1 this command died with FileNotFoundError on docs/plan/
        BATTERY-EXPECTATIONS.json, which the allowlist did not carry. Run,
        not proxied on the file's presence, because the file being there is
        not the same claim as the README's proof reproducing."""
        rel = os.path.join("scripts", "test_battery_verdict.py")
        self.assertTrue(os.path.isfile(os.path.join(self.tree, rel)), rel)
        proc = EP._run(["python3", rel], self.tree, timeout=600)
        text = ((proc.stdout or "") + (proc.stderr or "")).strip()
        self.assertEqual(proc.returncode, 0,
                         "the README names this as its own proof and it "
                         "fails on the export tree:\n%s"
                         % "\n".join(text.splitlines()[-15:]))


class ProofResidueIsNotShippedContent(unittest.TestCase):
    """Attempt 3 of the 1.0.3 tag: the README proof suites ran inside the
    staged tree and left __pycache__ entries and a fence store behind, and
    check_export_manifest, hashing the tree afterwards, refused with 70
    EXTRA files. The tag ships the staged bytes; a snapshot taken before
    the proofs is what the check must compare against."""

    def test_a_snapshot_taken_before_the_residue_still_reads_clear(self):
        import reproduce_export as RE
        d = tempfile.mkdtemp(prefix="brother-export-residue-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        os.makedirs(os.path.join(d, "docs", "releases"))
        with open(os.path.join(d, "a.py"), "w") as fh:
            fh.write("x = 1\n")
        before = RE.manifest_from_dir(d)
        with open(os.path.join(d, "docs", "releases",
                               "9.9.9.export-manifest.txt"), "w") as fh:
            fh.write(before)
        # the proofs now leave residue behind
        os.makedirs(os.path.join(d, "__pycache__"))
        with open(os.path.join(d, "__pycache__", "a.cpython-313.pyc"), "wb") as fh:
            fh.write(b"\x00")
        ok_now, lines_now = EP.check_export_manifest(d, "9.9.9")
        self.assertFalse(ok_now, lines_now)
        self.assertTrue(any("EXTRA" in l for l in lines_now), lines_now)
        ok, lines = EP.check_export_manifest(d, "9.9.9", built_text=before)
        self.assertTrue(ok, lines)

    def test_tag_time_checks_hashes_before_its_own_proofs_run(self):
        # Attempt 4 of the 1.0.3 tag died with a NameError on the snapshot
        # line before any check ran; the whole tag-time path must run on a
        # bare tree and refuse in words, never raise.
        d = tempfile.mkdtemp(prefix="brother-export-ttc-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with open(os.path.join(d, "README.md"), "w") as fh:
            fh.write("no prove commands here\n")
        ok, lines = EP.tag_time_checks(d, "9.9.9")
        self.assertFalse(ok)
        self.assertTrue(lines and all(isinstance(l, str) for l in lines))



class TheManifestDescribesTheTaggedTree(unittest.TestCase):
    """Row E110, measured on the public tag v1.0.2: it ships a 1198 line
    docs/releases/1.0.2.export-manifest.txt generated at the cut commit,
    and 18 of the files it names hash differently in the tag itself (one
    of them bundle/runtime/worktree_lane.py, fafa0db4... in the tag
    against 91716f91... in the manifest), plus one file shipped that the
    manifest never named. Four commits landed on the cut branch after the
    manifest was written and nothing regenerated it, so the tag's own
    contents claim described a tree that was never published. Nothing at
    tag time compared the two. check_export_manifest is that comparison,
    driven both ways here on a fixture export tree."""

    VERSION = "9.9.9"

    def _tree(self, root, files, manifest_files=None):
        """An export-shaped tree: `files` on disk, plus the manifest the
        exporter would ship beside them. The manifest is written over
        `manifest_files` (defaulting to `files`), so a caller drives
        staleness by passing a DIFFERENT mapping from the one it wrote to
        disk, which is exactly what four post-cut commits did to v1.0.2."""
        import reproduce_export as RE
        for rel, content in files.items():
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        described = files if manifest_files is None else manifest_files
        text = RE.manifest_text(
            (rel, content.encode("utf-8"))
            for rel, content in described.items())
        releases = os.path.join(root, "docs", "releases")
        os.makedirs(releases, exist_ok=True)
        with open(os.path.join(releases,
                               "%s.export-manifest.txt" % self.VERSION),
                  "w", encoding="utf-8") as fh:
            fh.write(text)
        return text

    def test_a_a_manifest_that_matches_the_tree_is_clear(self):
        with tempfile.TemporaryDirectory() as root:
            self._tree(root, {"README.md": "front page\n",
                              "scripts/one.py": "print(1)\n"})
            ok, lines = EP.check_export_manifest(root, self.VERSION)
            self.assertTrue(ok, "\n".join(lines))
            self.assertIn("describes all 2 exported file(s)",
                          "\n".join(lines))

    def test_b_a_file_whose_bytes_moved_after_the_cut_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            self._tree(
                root,
                {"README.md": "front page\n",
                 "scripts/one.py": "print(2)  # the commit after the cut\n"},
                manifest_files={"README.md": "front page\n",
                                "scripts/one.py": "print(1)\n"})
            ok, lines = EP.check_export_manifest(root, self.VERSION)
            self.assertFalse(ok, "\n".join(lines))
            self.assertTrue(lines[0].startswith("MISMATCH: scripts/one.py"),
                            "\n".join(lines))
            self.assertIn("REFUSED: 1 of 2 file(s)", "\n".join(lines))

    def test_c_a_shipped_file_the_manifest_never_named_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            self._tree(root,
                       {"README.md": "front page\n",
                        "scripts/late.py": "arrived after the cut\n"},
                       manifest_files={"README.md": "front page\n"})
            ok, lines = EP.check_export_manifest(root, self.VERSION)
            self.assertFalse(ok, "\n".join(lines))
            self.assertIn("EXTRA: scripts/late.py", "\n".join(lines))

    def test_d_a_named_file_the_tree_does_not_ship_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            self._tree(root,
                       {"README.md": "front page\n"},
                       manifest_files={"README.md": "front page\n",
                                       "scripts/gone.py": "deleted\n"})
            ok, lines = EP.check_export_manifest(root, self.VERSION)
            self.assertFalse(ok, "\n".join(lines))
            self.assertIn("MISSING: scripts/gone.py", "\n".join(lines))

    def test_e_an_absent_manifest_is_no_data_and_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            self._tree(root, {"README.md": "front page\n"})
            os.remove(os.path.join(root, "docs", "releases",
                                   "%s.export-manifest.txt" % self.VERSION))
            ok, lines = EP.check_export_manifest(root, self.VERSION)
            self.assertFalse(ok, "\n".join(lines))
            self.assertTrue(lines[0].startswith("NO-DATA:"),
                            "\n".join(lines))

    def test_f_an_unparseable_manifest_is_no_data_and_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            self._tree(root, {"README.md": "front page\n"})
            with open(os.path.join(root, "docs", "releases",
                                   "%s.export-manifest.txt" % self.VERSION),
                      "w", encoding="utf-8") as fh:
                fh.write("this is not a sha256 line\n")
            ok, lines = EP.check_export_manifest(root, self.VERSION)
            self.assertFalse(ok, "\n".join(lines))
            self.assertTrue(lines[0].startswith("NO-DATA:"),
                            "\n".join(lines))

    def test_g_tag_time_checks_itself_refuses_a_stale_manifest(self):
        """Registration, not just the function: a check nobody calls is a
        claim. This tree clears every other tag-time check (its readiness
        gate reads READY, its README's prove command really runs), so the
        only thing left to refuse it is the manifest, and tag_time_checks
        must be what reaches that verdict."""
        with tempfile.TemporaryDirectory() as root:
            _seed_tag_time_needs(root)
            self._tree(root, {},
                       manifest_files={"scripts/gone.py": "not shipped\n"})
            ok, lines = EP.tag_time_checks(root, self.VERSION)
            self.assertFalse(ok, "\n".join(lines))
            self.assertTrue(
                any(l.startswith("prove: ") for l in lines),
                "tag_time_checks never got as far as the prove commands, so "
                "this case did not reach the manifest check at all:\n%s"
                % "\n".join(lines))
            self.assertTrue(
                any(l.startswith("REFUSED:") and "export-manifest" in l
                    for l in lines), "\n".join(lines))

    def test_i_the_refresh_reports_no_data_rather_than_clear(self):
        """scripts/refresh_cut.py is the operator's half of the same rule.
        With no allowlist there is no export tree to compare a manifest
        against, and this estate never reads a NO-DATA as a pass: the
        refresh must exit 2, not 0."""
        import refresh_cut
        with mock.patch.object(EP, "load_allowlist", return_value=None):
            code, lines = refresh_cut.check(self.VERSION)
        self.assertEqual(code, refresh_cut.EXIT_NODATA, "\n".join(lines))
        self.assertTrue(lines[0].startswith("NO-DATA:"), "\n".join(lines))
class TheAllowlistCarriesEveryTrackedGitattributes(unittest.TestCase):
    """E114: products/brothersbe/.gitattributes was hub-tracked but absent
    from EXPORT-ALLOWLIST.txt (found by lane XM2's Windows leg against the
    public v1.0.2 tag). Without it a Windows clone of the export rewrites
    the committed line endings, and products/brothersbe/CHECKSUMS.sha256 no
    longer matches the rewritten bytes. Read the hub's own tracked set with
    git ls-files (_tracked_files_under, the exporter's own authority),
    never a filesystem walk that would also see an untracked file."""

    def test_every_tracked_gitattributes_is_allowlisted(self):
        tracked = [p for p in EP._tracked_files_under(EP.ROOT, ".")
                   if os.path.basename(p) == ".gitattributes"]
        self.assertTrue(tracked,
                        "NO-DATA: no .gitattributes tracked in the hub")
        allowlist = EP.load_allowlist()
        self.assertIsNotNone(allowlist, "NO-DATA: no export allowlist")
        missing = [p for p in tracked if p not in allowlist]
        self.assertEqual(
            missing, [],
            "tracked .gitattributes missing from EXPORT-ALLOWLIST.txt, so "
            "a Windows clone of the export rewrites its line endings and "
            "the product's CHECKSUMS.sha256 stops matching: "
            + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()


class TheCodexArtifactsShipAndCarryNoMachinePath(unittest.TestCase):
    """Ship gate 6, rows C4 and C6: every Codex artifact reaches the public
    tree, and nothing machine-local rides out with it.

    THE TWO HALVES ARE ONE TEST FOR A REASON. A Codex artifact that does not
    export leaves a Codex user with a package they cannot install; an artifact
    that exports carrying an absolute path under one person's home leaves them
    with a package that only works on that person's machine, and leaks the
    layout of it. Both were live findings when this was written: `AGENTS.md`
    was on the allowlist AND on the denylist, and the denylist wins, so the
    file Codex reads as a project's standing instructions never left the hub;
    and `scripts/test_codex_package.py` named the canonical validator by an
    absolute path under one home, which is why it now resolves it from
    `pathlib.Path.home()`.

    THE EXPORT TREE, NOT THE HUB. Every assertion below reads the tree
    `build_export_tree` actually produces, through the exporter's own code, so
    it measures what would ship rather than a second opinion about the
    allowlist.
    """

    #: Absolute paths already in the tree before this gate existed, each named
    #: individually with what it is. Declared at PATH granularity on purpose:
    #: a suite-level exemption would hide the next leak as well as this one,
    #: which is the whole failure this list is shaped to avoid. It is not a
    #: Codex artifact and does not sit under an agent runtime's configuration
    #: directory; it is outside rows C4 and C6 and is reported as a
    #: pre-existing finding rather than fixed here, because fixing a fixture
    #: belongs to whoever owns that file.
    #:
    #: docs/plan/RESTORE-DRILL-ENTERPRISE-RESULT.json LEFT THIS LIST on
    #: 2026-09-04: it named the worktree the drill ran in, so the gate that
    #: exists to catch machine-local paths was standing aside for the one
    #: file that carried one. scripts/restore_drill_enterprise.py now writes
    #: a repository-relative tools path and no scratch path at all, and the
    #: gate protects the record like every other exported file.
    PRE_EXISTING_ABSOLUTE_PATHS = {
        "products/brothersbe/tools/test_sbe_first_contact_paths.py":
            "a test fixture's vendor path, BrotherSBE's own file",
    }

    #: What a Codex user must find in a clone. Files, never directories: a
    #: directory that exists but is empty would satisfy a looser check.
    CODEX_ARTIFACTS = (
        "AGENTS.md",
        ".agents/plugins/marketplace.json",
        "bundle/.codex-plugin/plugin.json",
        "bundle/codex-skills/STRIPPED.json",
        "docs/codex/PACKAGE-SHAPE.md",
        "docs/codex/HOOKS-MAPPING.md",
    )

    #: A home-anchored configuration directory for either agent runtime. This
    #: is the shape that makes an exported file unusable anywhere but one
    #: machine, so it is refused outright rather than counted.
    #: The placeholder names are excluded here for the same reason they are
    #: excluded below: `/home/user/.claude/projects/...` in
    #: products/brothermode/docs/HOOKS.md is Claude's own documented hook
    #: payload, written for a reader, and refusing it would be refusing the
    #: documentation rather than a leak.
    RUNTIME_CONFIG_PATH = re.compile(
        r"/(?:Users|home)/(?!you\b|user\b|<)"
        r"[A-Za-z0-9._-]+/\.(?:codex|claude)\b")

    #: The home directory of the machine BUILDING the export, taken from the
    #: environment rather than written down. Measured over this tree
    #: 2026-09-04: every other home-shaped string in it is a documentation
    #: placeholder (/Users/j, /Users/jane, /home/runner, /Users/you and a
    #: dozen more), so a pattern matching "any home" would fail on sixteen
    #: kinds of prose while saying nothing about portability. The real
    #: property is narrower and exactly checkable: a file that names THIS
    #: machine's home only works on THIS machine.

    @classmethod
    def setUpClass(cls):
        allowlist = EP.load_allowlist()
        if allowlist is None:
            raise unittest.SkipTest(
                "NO-DATA: no export allowlist, so nothing can be measured")
        cls.dest = tempfile.mkdtemp(prefix="codex-portability-")
        # build_export_tree narrates its manifest regeneration on stdout.
        with contextlib.redirect_stdout(sys.stderr):
            cls.copied = EP.build_export_tree(cls.dest, allowlist)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "dest", ""), ignore_errors=True)

    def exported_text_files(self):
        """(relative posix path, text) for every readable text file in the
        export tree. A file that is not UTF-8 is binary and carries no path
        string to find, so it is skipped rather than guessed at."""
        for dirpath, dirnames, filenames in os.walk(self.dest):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self.dest).replace(os.sep, "/")
                try:
                    with open(full, encoding="utf-8") as fh:
                        yield rel, fh.read()
                except (OSError, UnicodeDecodeError):
                    continue

    def test_every_codex_artifact_reaches_the_exported_tree(self):
        missing = [rel for rel in self.CODEX_ARTIFACTS
                   if not os.path.isfile(os.path.join(self.dest,
                                                      *rel.split("/")))]
        self.assertEqual(
            missing, [],
            "the export drops %s; check EXPORT-ALLOWLIST.txt AND "
            "EXPORT-DENYLIST.txt, since a denylist line wins over an "
            "allowlist one" % ", ".join(missing))

    def test_the_generated_codex_skills_ship_with_their_content(self):
        """The mirror ships as skills, not as an empty directory plus a
        record. bundle/codex-skills/STRIPPED.json alone would satisfy the
        artifact list above while shipping no skill at all."""
        root = os.path.join(self.dest, "bundle", "codex-skills")
        self.assertTrue(os.path.isdir(root),
                        "bundle/codex-skills did not export")
        skills = sorted(
            name for name in os.listdir(root)
            if os.path.isfile(os.path.join(root, name, "SKILL.md")))
        self.assertTrue(
            skills, "bundle/codex-skills exported with no SKILL.md under it")

    def test_no_exported_file_names_a_runtime_config_directory(self):
        """~/.codex and ~/.claude, the two directories that make an exported
        file work on exactly one machine. No exception list: this shape has no
        legitimate reason to ship."""
        hits = []
        for rel, text in self.exported_text_files():
            for line_no, line in enumerate(text.splitlines(), 1):
                if self.RUNTIME_CONFIG_PATH.search(line):
                    hits.append("%s:%d" % (rel, line_no))
        self.assertEqual(
            hits, [],
            "exported file(s) name a home-anchored .codex or .claude path, so "
            "they only work on the machine that wrote them: "
            + ", ".join(hits))

    def test_no_new_exported_file_names_this_machines_home(self):
        """Wider than the case above, and therefore carrying the two named
        pre-existing paths. A file appearing here that is not on that list is
        a NEW leak and fails."""
        home = os.path.expanduser("~")
        if not home or home == "~" or home in ("/", ""):
            self.skipTest("NO-DATA: this machine reports no home directory, "
                          "so there is no path to look for")
        offenders = set()
        for rel, text in self.exported_text_files():
            if home in text:
                offenders.add(rel)
        unexpected = sorted(offenders - set(self.PRE_EXISTING_ABSOLUTE_PATHS))
        self.assertEqual(
            unexpected, [],
            "new exported file(s) carry an absolute path under a real home "
            "directory: " + ", ".join(unexpected))
        # A declared exception that stopped being true is a stale exception,
        # and a reason nobody can point at is a reason to look, not to renew.
        stale = sorted(set(self.PRE_EXISTING_ABSOLUTE_PATHS) - offenders)
        self.assertEqual(
            stale, [],
            "these paths are declared here as pre-existing but no longer "
            "carry a home directory; remove them from the list: "
            + ", ".join(stale))
