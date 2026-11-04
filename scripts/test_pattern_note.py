"""What the pattern store must keep true.

The gap it closes is measurable: 186 vault notes typed failure, zero typed as a
pattern that worked. The risk in closing it is writing a second archive nobody
can search, so the tests that matter here are the FINDING ones.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pattern_note as P  # noqa: E402

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


def vault():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, P.FOLDER))
    return d


def add(v, name, solves, what="do the thing", ev="it worked once"):
    return P.write(name, solves, what, ev, vault=v)


class FindableByTheProblemNotTheTitle(unittest.TestCase):
    """Nobody searching for help types the name of a technique they have never
    heard of. They type the trouble they are in."""

    def setUp(self):
        self.v = vault()
        add(self.v, "Drive every control backwards",
            "My check passes but I do not know whether it can ever fail")
        add(self.v, "Judge a branch against unchanged main",
            "The suite is red and I cannot tell whether my branch caused it")

    def test_a_problem_query_sharing_no_word_with_the_title_still_finds_it(self):
        hits = P.find("suite is red cannot tell whether my branch caused it", self.v)
        self.assertTrue(hits)
        self.assertIn("unchanged-main", hits[0][1])

    def test_the_better_match_ranks_first(self):
        hits = P.find("my check passes but can it ever fail", self.v)
        self.assertIn("backwards", hits[0][1])

    def test_the_solves_line_comes_back_with_the_hit(self):
        """So a searcher can tell from the result list whether it is their
        problem, without opening anything."""
        self.assertIn("check passes", P.find("check passes fail", self.v)[0][2])

    def test_an_unmatched_problem_is_NO_DATA_rather_than_silence(self):
        self.assertEqual(P.find("quantum tunnelling in a kettle", self.v), [])

    def test_a_query_of_only_short_words_returns_nothing_rather_than_everything(self):
        """Otherwise 'is it a' matches every note and the tool is noise."""
        self.assertEqual(P.find("is it a", self.v), [])

    def test_a_missing_vault_is_None_not_an_empty_result(self):
        """Empty means nothing matched, which is knowable. None means nothing
        was searched, which is not."""
        self.assertIsNone(P.find("anything", "/no/such/vault"))


class ItObeysTheVaultConstitution(unittest.TestCase):
    def test_a_second_write_of_the_same_name_changes_nothing(self):
        v = vault()
        path, first = add(v, "A pattern", "some problem")
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        path2, second = P.write("A pattern", "DIFFERENT problem", "x", "y", vault=v)
        with open(path2, encoding="utf-8") as fh:
            after = fh.read()
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(before, after)

    def test_it_writes_into_the_existing_folder_rather_than_a_new_one(self):
        """Adding a type is not a reorganisation. Adding a folder is."""
        v = vault()
        path, _ = add(v, "A pattern", "some problem")
        self.assertEqual(os.path.basename(os.path.dirname(path)), P.FOLDER)

    def test_a_missing_vault_writes_nothing_and_says_so(self):
        path, written = P.write("n", "s", "w", "e", vault="/no/such/vault")
        self.assertIsNone(path)
        self.assertFalse(written)

    def test_the_index_gains_one_routing_line_per_pattern(self):
        v = vault()
        add(v, "One", "problem one")
        add(v, "Two", "problem two")
        with open(os.path.join(v, P.FOLDER, P.INDEX), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text.count("\n- [["), 2)
        self.assertIn("problem one", text)

    def test_the_index_does_not_duplicate_a_line(self):
        v = vault()
        add(v, "One", "problem one")
        P.write("One", "problem one", "w", "e", vault=v)
        with open(os.path.join(v, P.FOLDER, P.INDEX), encoding="utf-8") as fh:
            self.assertEqual(fh.read().count("\n- [["), 1)


class TheNoteCarriesWhatMakesItUsable(unittest.TestCase):
    def test_it_is_typed_as_a_pattern_so_it_is_distinguishable(self):
        body = P.note_body("n", "s", "w", "e", "all")
        self.assertIn("type: pattern", body)

    def test_it_carries_the_problem_in_its_frontmatter(self):
        """The whole finding mechanism depends on this field existing."""
        self.assertIn("solves: the problem", P.note_body("n", "the problem", "w", "e", "all"))

    def test_it_carries_how_it_is_known_to_work(self):
        self.assertIn("How it is known to work", P.note_body("n", "s", "w", "e", "all"))

    def test_a_pattern_without_evidence_is_still_forced_to_have_the_field(self):
        """An unevidenced pattern is folklore. The section must exist so its
        emptiness is visible rather than absent."""
        self.assertIn("verified-by:", P.note_body("n", "s", "w", "", "all"))


class ItRoutesThroughTheSameGateAdmitUses(unittest.TestCase):
    """V4: a pattern note must go through bm_vault_intake.hard_gate, the same
    gate `admit` and `capture` run, never a direct ungated write."""

    def test_a_credential_shaped_pattern_is_refused_and_not_written(self):
        v = vault()
        path, written = P.write(
            "Leaky pattern", "some problem", "do the thing",
            "AKIA" + "ABCDEFGHIJKLMNOP" + " was live in the log", vault=v)
        self.assertIsNone(path)
        self.assertFalse(written)
        self.assertEqual(os.listdir(os.path.join(v, P.FOLDER)), [])

    def test_an_injected_gate_refusal_writes_nothing_driven_backwards(self):
        """Drive the gate seam backwards: a gate that refuses for any reason
        must stop the write, proven without depending on a real credential
        shape or the real bm_vault_intake module."""
        v = vault()

        def refuse(text, deny_list_path):
            return False, "class=deny-list-term"

        path, written = P.write("Another pattern", "problem", "what", "ev",
                                vault=v, gate=refuse)
        self.assertIsNone(path)
        self.assertFalse(written)
        self.assertEqual(os.listdir(os.path.join(v, P.FOLDER)), [])

    def test_an_injected_gate_that_passes_still_writes(self):
        v = vault()

        def allow(text, deny_list_path):
            return True, None

        path, written = P.write("A clean pattern", "problem", "what", "ev",
                                vault=v, gate=allow)
        self.assertTrue(written)
        self.assertTrue(os.path.exists(path))

    def test_a_gate_that_cannot_load_fails_closed(self):
        """gate_text itself, with a loader that cannot find the module,
        refuses rather than silently skipping the gate."""
        ok, reason = P.gate_text("anything", loader=lambda: None)
        self.assertFalse(ok)
        self.assertIn("NO-DATA", reason)


class TheReceiptTravelsWithTheNote(unittest.TestCase):
    """V4: the note that closes a green run links the receipt it came from."""

    def test_the_receipt_is_in_the_frontmatter_when_given(self):
        body = P.note_body("n", "s", "w", "e", "all",
                           receipt="scripts/test_x.py exit=0")
        self.assertIn("receipt: scripts/test_x.py exit=0", body)

    def test_no_receipt_line_when_none_given(self):
        body = P.note_body("n", "s", "w", "e", "all")
        self.assertNotIn("receipt:", body)

    def test_write_passes_the_receipt_through_to_the_file(self):
        v = vault()
        path, written = P.write("Receipted pattern", "problem", "what", "ev",
                                vault=v, receipt="scripts/foo.py exit=0")
        self.assertTrue(written)
        with open(path, encoding="utf-8") as fh:
            self.assertIn("receipt: scripts/foo.py exit=0", fh.read())


class ThreePatternsAreFindableByOneBroadProblemQuery(unittest.TestCase):
    """The earlier version of this check read the LIVE vault and asserted it
    held three patterns, which coupled a repo gate to machine state: the
    vault's ingestion pipeline rewrote those notes' frontmatter to
    `type: reference` on 2026-08-30 and the gate went red on an untouched
    checkout. The invariant worth keeping is hermetic: three patterns written
    here all come back for one problem-worded query."""

    def test_three_seeded_patterns_all_come_back_together(self):
        v = vault()
        add(v, "Drive every control backwards",
            "My check passes but I do not know whether it can ever fail")
        add(v, "Judge a branch against unchanged main",
            "The suite is red and I cannot tell whether my branch caused it")
        add(v, "Scope a scanner to what can leave the machine",
            "My push gate keeps refusing on files nobody could ever ship")
        hits = P.find("check gate branch scanner suite red fail ship", v)
        self.assertGreaterEqual(len(hits), 3)


class Night0912PatternNote(unittest.TestCase):
    def test_empty_what_does_not_raise(self):
        for what in ("", "   ", "\n\t "):
            body = P.note_body('n', 's', what, 'e', 'all')
            self.assertIsInstance(body, str)
            self.assertIn("description: ", body)


# ---------------------------------------------------------------------------
# KEY1.b harness: no shipped copy carries an owner-specific default vault.
#
# Contract (plan, KEY1.b): "The vault is read from BROTHERMODE_VAULT or the
# configured vault setting; no shipped copy carries an owner-specific default
# folder. With no vault configured the note step reports NO-DATA naming the
# missing setting and the acceptance itself is still recorded. Every shipped
# copy is regenerated, never hand edited."
#
# The one resolver is bm_vault.resolve_vault_root (products/brothermode/tools/
# bm_vault.py): env names, then the installer config's "vault" key, and NO
# guessed path. Every case runs the SHIPPED COPY AS A PROGRAM under a tempdir
# HOME (a subprocess, so import-time state cannot hide a default), and
# compares where the note lands with what the resolver answers for the same
# inputs. The owner's folder is never spelled here: a default is detected by
# SHAPE (an expanduser("~/...") literal in the source) and planted by reading
# that literal back out of the shipped file.
# ---------------------------------------------------------------------------
import json as _json  # noqa: E402
import re as _re  # noqa: E402
import shutil as _shutil  # noqa: E402
import subprocess as _sp  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_REPO, "products", "brothermode", "tools")
SHIPPED_COPIES = (
    "products/brothermode/tools/pattern_note.py",
    "scripts/pattern_note.py",
    "bundle/runtime/pattern_note.py",
    "bundle/runtime/hooks/brothermode/tools/pattern_note.py",
)
_VAULT_ENVS = ("BROTHERMODE_VAULT_ROOT", "BM_VAULT_ROOT", "BROTHERMODE_VAULT")
_HOME_LITERAL = _re.compile(r"expanduser\(\s*[\"'](~/[^\"']*)[\"']\s*\)")


def _resolver():
    sys.path.insert(0, _TOOLS)
    try:
        import bm_vault
    finally:
        sys.path.remove(_TOOLS)
    return bm_vault.resolve_vault_root


def _tree(root):
    out = set()
    for dp, dn, fn in os.walk(root):
        out.update(os.path.join(dp, n) for n in dn + fn)
    return out


class NoOwnerDefault(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="key1b-"))
        self.home = os.path.join(self.tmp, "home")
        self.cfg = os.path.join(self.home, "cfg")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.home, self.cfg, self.work):
            os.makedirs(d)
        self.addCleanup(_shutil.rmtree, self.tmp, True)

    # -- helpers ----------------------------------------------------------
    def _env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in _VAULT_ENVS and k not in (
                   "PATTERN_DATE", "BROTHER_CONFIG_DIR", "CLAUDE_CONFIG_DIR",
                   "BROTHER_RUNS_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT")}
        env.update(HOME=self.home, BROTHER_CONFIG_DIR=self.cfg,
                   CLAUDE_CONFIG_DIR=self.cfg, PATTERN_DATE="2026-11-01")
        env.update(extra)
        return env

    def _make_vault(self, name):
        v = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(v, P.FOLDER))
        return v

    def _set_config(self, vault):
        with open(os.path.join(self.cfg, "bm_vault.json"), "w") as fh:
            _json.dump({"vault": vault}, fh)

    def _plant_shipped_default(self, rel):
        """If the shipped source holds an expanduser("~/...") default, create
        that folder (with the note folder inside) under the tempdir HOME, so
        a copy that still guesses finds a real vault and writes into it.
        Returns the planted folder or None. The literal is read from the
        shipped file, never spelled in this test."""
        with open(os.path.join(_REPO, rel), encoding="utf-8") as fh:
            m = _HOME_LITERAL.search(fh.read())
        if not m:
            return None
        planted = os.path.join(self.home, m.group(1)[2:])
        os.makedirs(os.path.join(planted, P.FOLDER), exist_ok=True)
        return planted

    def _run_write(self, rel, env, name="Key one b pattern"):
        return _sp.run(
            [sys.executable, "-B", os.path.join(_REPO, rel), "write",
             "--name", name, "--solves", "a check that passes with no vault",
             "--what", "do the thing", "--evidence", "it ran once"],
            cwd=self.work, env=env, capture_output=True, text=True,
            timeout=120)

    def _notes_under(self, root):
        return sorted(p for p in _tree(root)
                      if p.endswith(".md") and P.FOLDER in p)

    # -- static shape: no owner default in any shipped copy ----------------
    def test_no_shipped_copy_has_a_home_relative_vault_default(self):
        bad = []
        for rel in SHIPPED_COPIES:
            with open(os.path.join(_REPO, rel), encoding="utf-8") as fh:
                if _HOME_LITERAL.search(fh.read()):
                    bad.append(rel)
        self.assertEqual(bad, [], "these shipped copies still carry an "
                         "expanduser(\"~/...\") default folder: %s" % bad)

    # -- unset: nothing configured, nothing written, NO-DATA names the setting
    def test_unset_writes_nothing_anywhere_under_home(self):
        for rel in SHIPPED_COPIES:
            with self.subTest(copy=rel):
                self._plant_shipped_default(rel)
                before = _tree(self.home)
                proc = self._run_write(rel, self._env())
                self.assertNotEqual(proc.returncode, 0, proc.stdout)
                self.assertEqual(sorted(_tree(self.home) - before), [],
                                 "%s wrote under HOME with no vault "
                                 "configured" % rel)

    def test_unset_reports_no_data_naming_the_missing_setting(self):
        for rel in SHIPPED_COPIES:
            with self.subTest(copy=rel):
                proc = self._run_write(rel, self._env())
                text = proc.stdout + proc.stderr
                self.assertIn("NO-DATA", text, text)
                self.assertIn("BROTHERMODE_VAULT", text,
                              "NO-DATA must name the setting to fix:\n" + text)

    # -- the resolver is the one answer (environment, config, unset) -------
    def test_env_set_note_lands_where_the_resolver_says(self):
        v = self._make_vault("vault-env")
        env = self._env(BROTHERMODE_VAULT=v)
        want = _resolver()(None, env, {})
        self.assertEqual((want["verdict"], want["root"]), ("PASS", v))
        for rel in SHIPPED_COPIES:
            with self.subTest(copy=rel):
                proc = self._run_write(rel, env, "Env %s" % rel)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertTrue(
                    [n for n in self._notes_under(v) if "env-" in n],
                    "no note under the resolver's root %s: %s"
                    % (v, proc.stdout + proc.stderr))

    def test_configured_setting_note_lands_where_the_resolver_says(self):
        v = self._make_vault("vault-config")
        self._set_config(v)
        want = _resolver()(None, self._env(), {"vault": v})
        self.assertEqual((want["verdict"], want["root"], want["source"]),
                         ("PASS", v, "config"))
        for rel in SHIPPED_COPIES:
            with self.subTest(copy=rel):
                proc = self._run_write(rel, self._env(), "Config %s" % rel)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertTrue(
                    [n for n in self._notes_under(v) if "config-" in n],
                    "no note under the configured vault %s: %s"
                    % (v, proc.stdout + proc.stderr))

    def test_env_beats_config_like_the_resolver(self):
        ve = self._make_vault("vault-env2")
        vc = self._make_vault("vault-config2")
        self._set_config(vc)
        env = self._env(BROTHERMODE_VAULT=ve)
        want = _resolver()(None, env, {"vault": vc})
        self.assertEqual(want["root"], ve)
        for rel in SHIPPED_COPIES:
            with self.subTest(copy=rel):
                proc = self._run_write(rel, env, "Both %s" % rel)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertTrue([n for n in self._notes_under(ve)
                                 if "both-" in n])
                self.assertEqual([n for n in self._notes_under(vc)
                                  if "both-" in n], [])

    def test_two_env_names_on_two_folders_writes_nothing_like_the_resolver(self):
        a = self._make_vault("vault-a")
        b = self._make_vault("vault-b")
        env = self._env(BROTHERMODE_VAULT_ROOT=a, BROTHERMODE_VAULT=b)
        want = _resolver()(None, env, {})
        self.assertEqual((want["verdict"], want["root"]), ("FAIL", None))
        for rel in SHIPPED_COPIES:
            with self.subTest(copy=rel):
                self._run_write(rel, env, "Clash %s" % rel)
                self.assertEqual(self._notes_under(a), [])
                self.assertEqual(self._notes_under(b), [])

    # -- the acceptance is the primary act; the note is a side effect ------
    def _accept(self, env, *extra):
        d = os.path.join(self.tmp, "deliveries")
        proc = _sp.run(
            [sys.executable, "-B",
             os.path.join(_REPO, "scripts", "accept_delivery.py"),
             "--name", "key1b delivery", "--ref", "owner/repo#1",
             "--accepted-by", "A Tester", "--accepted-at", "2026-11-01",
             "--recorded-by", "person", "--dir", d, *extra],
            cwd=self.work, env=env, capture_output=True, text=True,
            timeout=120)
        return proc, d

    def test_acceptance_is_recorded_when_the_note_step_is_no_data(self):
        proc, d = self._accept(self._env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        recs = os.listdir(d) if os.path.isdir(d) else []
        self.assertEqual(len(recs), 1, "acceptance not recorded: %s%s"
                         % (proc.stdout, proc.stderr))

    def test_acceptance_with_no_vault_writes_no_note_anywhere_under_home(self):
        self._plant_shipped_default("scripts/pattern_note.py")
        before = _tree(self.home)
        self._accept(self._env())
        self.assertEqual(sorted(_tree(self.home) - before), [],
                         "the note step wrote under HOME with no vault set")

    def test_acceptance_no_data_line_names_the_missing_setting(self):
        proc, _ = self._accept(self._env())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        no_data = [ln for ln in proc.stdout.splitlines() if "NO-DATA" in ln]
        self.assertEqual(len(no_data), 1, proc.stdout)
        self.assertIn("BROTHERMODE_VAULT", no_data[0], no_data[0])

    # GREEN control: with a vault configured the same acceptance writes its
    # pattern there, so a fix cannot pass by never writing a note.
    def test_acceptance_writes_its_pattern_into_the_configured_vault(self):
        v = self._make_vault("vault-accept")
        proc, d = self._accept(self._env(BROTHERMODE_VAULT=v))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue([n for n in self._notes_under(v)
                         if "delivery-" in n], proc.stdout + proc.stderr)
        self.assertEqual(len(os.listdir(d)), 1)

    # GREEN control: an explicit --pattern-root still wins, no vault needed.
    def test_explicit_pattern_root_still_receives_the_pattern(self):
        v = self._make_vault("vault-explicit")
        proc, _ = self._accept(self._env(), "--pattern-root", v)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue([n for n in self._notes_under(v)
                         if "delivery-" in n], proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
