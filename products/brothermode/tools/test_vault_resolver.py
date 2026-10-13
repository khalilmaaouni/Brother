#!/usr/bin/env python3
"""VR1 (1.1.3): one vault resolver, so capture and recall read the same vault or say why not.

WHY THIS SUITE EXISTS. Capture (scripts/brother_run.py) reads BROTHERMODE_VAULT_ROOT; recall
(bm_vault.py _default_vault) read only BM_VAULT_ROOT and BROTHERMODE_VAULT. A user who set the
two to different folders wrote lessons to one vault and recalled them from another, and nothing
said so. resolve_vault_root is the one answer now: PASS with a root, NO-DATA when nothing is
configured (never a guessed path, D01), FAIL with no root when two names disagree.

The user's real vault is never read or written here: the resolver is driven with an explicit
env mapping, and every subprocess runs in a moved HOME over a temporary vault.

Run: python3 -B products/brothermode/tools/test_vault_resolver.py
"""
import datetime
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# E100: one sandbox for every temp tree this process makes, removed at exit (the sibling
# shape of test_vault_index_refresh.py).
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../scripts"))
try:
    import tmp_sandbox as _e100_tmp
    _e100_tmp.install()
except ImportError:
    sys.stderr.write("tmp_sandbox absent: %s leaves its temp trees behind\n"
                     % os.path.basename(__file__))

HERE =os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "bm_vault.py")
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_spec = importlib.util.spec_from_file_location("bm_vault_vr1", TOOL)
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

ALL_NAMES = ("BROTHERMODE_VAULT_ROOT", "BM_VAULT_ROOT", "BROTHERMODE_VAULT")

NOTE = """---
name: %s
description: %s
type: project
---
%s
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _clean_env(home, **extra):
    """A child environment with HOME moved and every vault name removed, then `extra` set."""
    env = {k: v for k, v in os.environ.items()
           if k not in ALL_NAMES and not k.startswith("BM_VAULT")}
    env["HOME"] = home
    store = os.path.join(home, "store-root")
    os.makedirs(store, exist_ok=True)
    env["BROTHERMODE_ROOT"] = store  # no real correction rules leak into the corpus
    env.update(extra)
    return env


def _run(argv, env):
    p = subprocess.run([sys.executable, "-B", TOOL] + argv, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="vault-resolver-"))
        self.a = os.path.join(self.tmp, "vault-a")
        self.b = os.path.join(self.tmp, "vault-b")
        os.makedirs(self.a)
        os.makedirs(self.b)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TheResolver(_Tmp):
    """REQ-VR1-01 to 05 and the edges: driven in process with an explicit env mapping."""

    def test_01_each_name_alone_resolves_to_its_folder(self):
        for name in ALL_NAMES:
            r = bm.resolve_vault_root(None, {name: self.a}, {})
            self.assertEqual((r["verdict"], r["root"], r["source"]), ("PASS", self.a, name), r)
            self.assertEqual(r["set"], [[name, self.a]], r)

    def test_02_same_folder_by_different_spellings_agrees(self):
        r = bm.resolve_vault_root(None, {"BROTHERMODE_VAULT_ROOT": self.a,
                                         "BM_VAULT_ROOT": self.a + "/"}, {})
        self.assertEqual((r["verdict"], r["root"]), ("PASS", self.a), r)
        link = os.path.join(self.tmp, "link-to-a")
        os.symlink(self.a, link)
        r = bm.resolve_vault_root(None, {"BROTHERMODE_VAULT_ROOT": self.a,
                                         "BROTHERMODE_VAULT": link}, {})
        self.assertEqual((r["verdict"], r["root"]), ("PASS", self.a), r)

    def test_03_two_names_on_two_folders_fail_and_choose_nothing(self):
        for alias in ("BM_VAULT_ROOT", "BROTHERMODE_VAULT"):
            r = bm.resolve_vault_root(None, {"BROTHERMODE_VAULT_ROOT": self.a, alias: self.b}, {})
            self.assertEqual(r["verdict"], "FAIL", r)
            self.assertIsNone(r["root"], r)
            for word in ("BROTHERMODE_VAULT_ROOT", alias, self.a, self.b,
                         "the vault was not chosen for you"):
                self.assertIn(word, r["detail"], r)
        # Two aliases disagreeing with no canonical name are still a disagreement.
        r = bm.resolve_vault_root(None, {"BM_VAULT_ROOT": self.a, "BROTHERMODE_VAULT": self.b}, {})
        self.assertEqual((r["verdict"], r["root"]), ("FAIL", None), r)

    def test_04_nothing_configured_is_no_data_not_a_guess(self):
        for env, cfg in (({}, {}), ({"BM_VAULT_ROOT": ""}, {"vault": ""}), ({}, {"vault": 5}),
                         ({}, ["not", "an", "object"])):
            r = bm.resolve_vault_root(None, env, cfg)
            self.assertEqual((r["verdict"], r["root"]), ("NO-DATA", None), (env, cfg, r))
            self.assertIn("BROTHERMODE_VAULT_ROOT", r["detail"], r)

    def test_05_precedence_explicit_then_env_then_config(self):
        # explicit beats even a disagreeing environment, and is not a conflict
        r = bm.resolve_vault_root(self.b, {"BROTHERMODE_VAULT_ROOT": self.a,
                                           "BM_VAULT_ROOT": os.path.join(self.tmp, "c")}, {})
        self.assertEqual((r["verdict"], r["root"], r["source"]), ("PASS", self.b, "--vault"), r)
        # the environment beats the config; a differing config is mentioned, not a conflict
        r = bm.resolve_vault_root(None, {"BM_VAULT_ROOT": self.a}, {"vault": self.b})
        self.assertEqual((r["verdict"], r["root"], r["source"]), ("PASS", self.a, "BM_VAULT_ROOT"), r)
        self.assertIn(self.b, r["detail"], r)
        # the canonical name outranks its aliases when they agree
        r = bm.resolve_vault_root(None, {"BM_VAULT_ROOT": self.a + "/",
                                         "BROTHERMODE_VAULT_ROOT": self.a}, {})
        self.assertEqual(r["source"], "BROTHERMODE_VAULT_ROOT", r)
        # the config alone is used
        r = bm.resolve_vault_root(None, {}, {"vault": self.b})
        self.assertEqual((r["verdict"], r["root"], r["source"]), ("PASS", self.b, "config"), r)

    def test_06_all_four_sources_agreeing_pass_and_twice_is_the_same(self):
        env = {"BROTHERMODE_VAULT_ROOT": self.a, "BM_VAULT_ROOT": self.a + "/",
               "BROTHERMODE_VAULT": self.a}
        first = bm.resolve_vault_root(None, env, {"vault": self.a})
        self.assertEqual((first["verdict"], first["root"], first["detail"]), ("PASS", self.a, ""))
        self.assertEqual(len(first["set"]), 3, first)
        self.assertEqual(bm.resolve_vault_root(None, env, {"vault": self.a}), first)

    def test_07_a_nul_byte_fails_naming_the_name_never_raises(self):
        r = bm.resolve_vault_root(None, {"BM_VAULT_ROOT": "/x\0y"}, {})
        self.assertEqual((r["verdict"], r["root"]), ("FAIL", None), r)
        self.assertIn("BM_VAULT_ROOT", r["detail"], r)

    def test_08_an_unreadable_path_beside_a_good_one_is_a_disagreement(self):
        r = bm.resolve_vault_root(None, {"BROTHERMODE_VAULT_ROOT": self.a,
                                         "BM_VAULT_ROOT": os.path.join(self.tmp, "missing")}, {})
        self.assertEqual((r["verdict"], r["root"]), ("FAIL", None), r)

    def test_09_a_value_that_is_not_a_directory_still_resolves(self):
        r = bm.resolve_vault_root(None, {"BROTHERMODE_VAULT_ROOT": os.path.join(self.tmp, "nope")}, {})
        self.assertEqual(r["verdict"], "PASS", r)
        self.assertEqual(r["detail"], "", r)


class TheDefaultVault(_Tmp):
    """REQ-VR1-06 and 08: _default_vault keeps its contract and refuses on FAIL."""

    def test_10_signature_contract_and_the_stub_pattern(self):
        self.assertEqual(list(inspect.signature(bm._default_vault).parameters), [])
        saved = bm._default_vault
        try:
            bm._default_vault = lambda: None  # the stub assignment other suites use
            out = io.StringIO()
            old, sys.stdout = sys.stdout, out
            try:
                self.assertEqual(bm.cmd_index({}), 2)
            finally:
                sys.stdout = old
        finally:
            bm._default_vault = saved

    def test_11_fail_prints_once_per_process_and_returns_none(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = _clean_env(home, BROTHERMODE_VAULT_ROOT=self.a, BM_VAULT_ROOT=self.b)
        code = ("import sys; sys.path.insert(0, %r); import bm_vault as m; "
                "print(m._default_vault()); print(m._default_vault())" % HERE)
        p = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.stdout.decode(), p.stderr.decode()
        self.assertEqual(out.split(), ["None", "None"], out + err)
        self.assertEqual(err.count("the vault was not chosen for you"), 1, err)
        # and a PASS returns the root, printing nothing
        env = _clean_env(home, BROTHERMODE_VAULT_ROOT=self.a)
        p = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.stdout.decode().split(), [self.a, self.a], p.stderr.decode())
        self.assertNotIn("FAIL", p.stderr.decode())

    def test_12_index_refuses_with_two_disagreeing_names(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        _write(os.path.join(self.a, "n.md"), NOTE % ("n", "d", "body"))
        env = _clean_env(home, BROTHERMODE_VAULT_ROOT=self.a, BM_VAULT_ROOT=self.b)
        code, out, err = _run(["index"], env)
        self.assertEqual(code, 2, out + err)
        self.assertIn("the vault was not chosen for you", err)
        self.assertFalse(os.path.exists(os.path.join(home, ".claude", "bm_vault_index.sqlite3")),
                         "an index was written although no vault was chosen")


class TheResolveCommandAndRecall(_Tmp):
    """REQ-VR1-07 and the resolve subcommand, in a moved HOME."""

    def test_13_resolve_prints_json_and_exits_by_verdict(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        code, out, err = _run(["resolve"], _clean_env(home, BROTHERMODE_VAULT_ROOT=self.a))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["root"], self.a)
        code, out, err = _run(["resolve"], _clean_env(home))
        self.assertEqual((code, json.loads(out)["verdict"]), (2, "NO-DATA"), out + err)
        code, out, err = _run(["resolve"], _clean_env(home, BROTHERMODE_VAULT_ROOT=self.a,
                                                      BROTHERMODE_VAULT=self.b))
        self.assertEqual((code, json.loads(out)["verdict"]), (2, "FAIL"), out + err)

    def test_14_the_canonical_name_alone_makes_recall_work(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        _write(os.path.join(self.a, "a-leaky-file-handle.md"),
               NOTE % ("a-leaky-file-handle", "handles never bound to a name leak",
                       "Use with-open around the zebracorn reader. See reader.py."))
        env = _clean_env(home, BROTHERMODE_VAULT_ROOT=self.a)
        code, out, err = _run(["index"], env)
        self.assertEqual(code, 0, out + err)
        code, out, err = _run(["recall", "--query", "zebracorn reader leak", "--fast"], env)
        self.assertEqual(code, 0, out + err)
        self.assertIn("a-leaky-file-handle", out, out + err)


# ---------------------------------------------------------------------------
# VR1.b: the advisory footer, five hostile lesson shapes through the real indexer and
# recall, and the ratchet on direct readers of BM_VAULT_ROOT.

PROMOTIONS = os.path.join(HERE, "bm_vault_promotions.py")


def _hostile_vault(vault):
    """Five shapes, each pinned below on what the existing machinery already does."""
    today = datetime.date.today().isoformat()
    # (1) a stale declared authority. Its body repeats the query words, so WITHOUT the
    # staleness demotion it would outrank the fresh note; only the demotion puts it second.
    _write(os.path.join(vault, "stale-decision.md"), """---
name: stale-decision
description: the quokkafire rollout uses blue green quokkafire rollout
type: decision
authority: source_of_record
verified_at: 2020-01-01
---
The quokkafire rollout uses blue green deploys. quokkafire rollout quokkafire rollout.
""")
    _write(os.path.join(vault, "fresh-decision.md"), """---
name: fresh-decision
description: the rollout uses canary
type: decision
authority: source_of_record
verified_at: %s
---
The quokkafire rollout uses canary deploys.
""" % today)
    # (2) one lesson id filed twice with different bodies (the harvest-folder poisoning shape)
    for name, body in (("dup-one", "Flush the plimsoll cache hourly."),
                       ("dup-two", "Never flush the plimsoll cache.")):
        _write(os.path.join(vault, name + ".md"), """---
name: %s
description: plimsoll cache flush rule
type: failure
lesson_id: plimsoll-flush
---
%s
""" % (name, body))
    # (3) the same text under two projects: the project is a boost, never a filter
    for proj in ("alpha", "beta"):
        _write(os.path.join(vault, "proj-%s.md" % proj), """---
name: proj-%s
description: the vorpal queue drains slowly
project: %s
---
The vorpal queue drains slowly under load.
""" % (proj, proj))
    # (4) a declared contradiction
    _write(os.path.join(vault, "contra-a.md"), """---
name: contra-a
description: the gribble timeout is ten seconds
contradicts: [[contra-b]]
---
The gribble timeout is ten seconds.
""")
    _write(os.path.join(vault, "contra-b.md"), """---
name: contra-b
description: the gribble timeout is thirty seconds
---
The gribble timeout is thirty seconds.
""")
    # (5) a candidate lesson with an author of record, for the separation of duties rule
    _write(os.path.join(vault, "promo-note.md"), """---
name: promo-note
description: the snarkle index rebuilds nightly
type: decision
author: agent-session
promotion: candidate
---
The snarkle index rebuilds nightly.
""")
    # an edge riding along: broken frontmatter is indexed as plain text, never a crash
    _write(os.path.join(vault, "broken-front.md"), "---\nname: [unclosed\nno end fence here\n")


def _order(out, *names):
    """Each name's first served-hit line index in stdout, or -1 when it is not served."""
    lines = out.splitlines()
    pos = []
    for n in names:
        hit = [i for i, l in enumerate(lines) if l.startswith("  %s  [" % n)]
        pos.append(hit[0] if hit else -1)
    return pos


class _HostileVault(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="vault-hostile-"))
        cls.vault = os.path.join(cls.tmp, "vault")
        cls.home = os.path.join(cls.tmp, "home")
        os.makedirs(cls.vault)
        os.makedirs(cls.home)
        _hostile_vault(cls.vault)
        cls.env = _clean_env(cls.home, BROTHERMODE_VAULT_ROOT=cls.vault)
        code, out, err = _run(["index"], cls.env)
        assert code == 0, out + err

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def recall(self, query, *extra, **kw):
        code, out, err = _run(["recall", "--query", query, "--fast"] + list(extra), self.env)
        self.assertEqual(code, kw.get("code", 0), out + err)  # a NO-DATA recall exits 1
        return out, err


class TheAdvisoryFooter(_HostileVault):
    """REQ-VR1-09 and 10."""

    def test_15_a_shown_recall_carries_the_advisory_on_stderr_only(self):
        for query in ("quokkafire rollout", "gribble timeout", "vorpal queue drains"):
            out, err = self.recall(query)
            self.assertEqual(err.count(bm.RECALL_ADVISORY), 1, query + "\n" + err)
            self.assertNotIn("ADVISORY", out, "the footer reached stdout, which the recall "
                             "hook parses:\n" + out)
        # after the hits: the ranking's own stderr line (the staleness demotion) comes first
        out, err = self.recall("quokkafire rollout")
        self.assertLess(err.index("authority demoted"), err.index(bm.RECALL_ADVISORY), err)

    def test_16_no_data_and_all_withheld_answers_carry_no_advisory(self):
        out, err = self.recall("xylophonequartz nothing matches this", code=1)
        self.assertIn("NO-DATA", out + err)
        self.assertNotIn(bm.RECALL_ADVISORY, out + err)
        out, err = self.recall("plimsoll cache flush rule")  # both sides withheld, none shown
        self.assertNotIn(bm.RECALL_ADVISORY, out + err, out + err)

    def test_17_an_empty_vault_is_no_data_without_the_advisory(self):
        empty = os.path.join(self.tmp, "empty-vault")
        home = os.path.join(self.tmp, "empty-home")
        os.makedirs(empty)
        os.makedirs(home)
        env = _clean_env(home, BROTHERMODE_VAULT_ROOT=empty)
        _run(["index"], env)
        code, out, err = _run(["recall", "--query", "anything at all", "--fast"], env)
        self.assertIn("NO-DATA", out + err)
        self.assertNotIn(bm.RECALL_ADVISORY, out + err)


class TheHostileLessons(_HostileVault):
    """REQ-VR1-11 to 14: each property measured on the base first, then pinned."""

    def test_18_a_stale_authority_is_demoted_and_said(self):
        out, err = self.recall("quokkafire rollout")
        self.assertIn("authority demoted, unverified since 2020-01-01", err)
        fresh, stale = _order(out, "fresh-decision", "stale-decision")
        self.assertTrue(0 <= fresh < stale, "the fresh note did not outrank the stale one:\n" + out)

    def test_19_a_duplicate_lesson_id_is_found_and_neither_side_is_served(self):
        out, err = self.recall("plimsoll cache flush rule")
        self.assertEqual(_order(out, "dup-one", "dup-two"), [-1, -1], out)
        self.assertEqual(out.count("WITHHELD (refused)"), 2, out)
        self.assertIn("duplicates %s" % os.path.join(self.vault, "dup-one.md"), out)
        self.assertIn("duplicates %s" % os.path.join(self.vault, "dup-two.md"), out)

    def test_20_a_foreign_project_ranks_below_and_is_still_shown(self):
        for mine, other in (("alpha", "beta"), ("beta", "alpha")):
            out, _ = self.recall("vorpal queue drains", "--project", mine)
            a, b = _order(out, "proj-" + mine, "proj-" + other)
            self.assertTrue(0 <= a < b, "project %s: own note first, foreign note still "
                            "shown (rank, not filter):\n%s" % (mine, out))

    def test_21_a_contradictory_pair_is_surfaced_not_silently_ranked(self):
        out, _ = self.recall("gribble timeout")
        self.assertIn("CONTRADICTS: contra-a", out)
        self.assertIn("CONTRADICTS: contra-b", out)

    def test_22_an_author_cannot_promote_their_own_lesson(self):
        path = os.path.join(self.vault, "promo-note.md")
        with io.open(path, encoding="utf-8") as fh:
            before = fh.read()
        argv = [sys.executable, "-B", PROMOTIONS, "promote", "--vault", self.vault,
                "--id", "promo-note.md", "--to", "validated"]
        p = subprocess.run(argv + ["--by", "agent-session", "--apply"], env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(b"separation of duties", p.stdout)
        with io.open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before, "a refused promotion wrote the note")
        # the rule is about WHO, not a blanket refusal: another reviewer may (dry run)
        p = subprocess.run(argv + ["--by", "reviewer"], env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_23_a_corrupt_index_exits_non_zero_with_a_message(self):
        home = os.path.join(self.tmp, "corrupt-home")
        os.makedirs(os.path.join(home, ".claude"))
        _write(os.path.join(home, ".claude", "bm_vault_index.sqlite3"), "not a database")
        env = _clean_env(home, BROTHERMODE_VAULT_ROOT=self.vault)
        code, out, err = _run(["recall", "--query", "gribble", "--fast"], env)
        self.assertNotEqual(code, 0, out + err)
        self.assertIn("not a database", err)
        self.assertNotIn(bm.RECALL_ADVISORY, err)


# The ratchet: every non test .py under products/ that reads BM_VAULT_ROOT straight from the
# environment, measured when VR1 landed. Such a file bypasses resolve_vault_root, so it
# cannot see a disagreement. The list may shrink; it may never grow. Moving a tool onto the
# resolver is the way down; adding a name here to get green is the defect this refuses.
DIRECT_READER_RE = re.compile(
    r"""os\.(?:environ\.get|getenv)\(\s*["']BM_VAULT_ROOT["']"""
    r"""|os\.environ\[\s*["']BM_VAULT_ROOT["']\s*\]""")
PRODUCTS = os.path.dirname(os.path.dirname(HERE))
RECORDED_READERS = (
    "brotherds/vault_bridge.py",
) + tuple("brothermode/tools/bm_vault_%s.py" % n for n in (
    "asof", "assertions", "attribute_provenance", "attributes", "authority", "catalog",
    "census_ext", "cite", "cli", "closure", "compose", "contract", "crosswalk", "curate",
    "digest", "distill", "entity", "graph", "heat_temporal", "hierarchy_req", "identity",
    "ids", "labels", "lifecycle", "lineage", "lint", "lock", "notify", "pack", "plugins",
    "policy", "posture", "principals", "promotions", "provenance", "retention", "route",
    "shapes", "staleness", "survivorship", "temporal", "triage"))
READER_CEILING = 43


def direct_env_readers(tools_dir):
    """Sorted paths (relative to tools_dir, forward slashes) of non test .py files under it
    whose source reads BM_VAULT_ROOT directly from the environment."""
    found = []
    for dirpath, dirnames, filenames in os.walk(tools_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "__"))]
        for fn in filenames:
            if not fn.endswith(".py") or fn.startswith("test_"):
                continue
            full = os.path.join(dirpath, fn)
            with io.open(full, encoding="utf-8", errors="replace") as fh:
                if DIRECT_READER_RE.search(fh.read()):
                    found.append(os.path.relpath(full, tools_dir).replace(os.sep, "/"))
    return sorted(found)


def ratchet_problems(found):
    problems = []
    if len(found) > READER_CEILING:
        problems.append("%d direct readers, ceiling %d" % (len(found), READER_CEILING))
    new = sorted(set(found) - set(RECORDED_READERS))
    if new:
        problems.append("new direct reader(s) of BM_VAULT_ROOT: %s; read the vault through "
                        "bm_vault.resolve_vault_root instead" % ", ".join(new))
    return problems


class TheDirectReaderRatchet(unittest.TestCase):
    """REQ-VR1-15."""

    def test_24_the_tree_is_within_the_ratchet(self):
        found = direct_env_readers(PRODUCTS)
        self.assertEqual(ratchet_problems(found), [], found)
        self.assertNotIn("brothermode/tools/bm_vault.py", found,
                         "bm_vault.py reads the vault through resolve_vault_root")

    def test_25_the_ceiling_is_the_count_recorded_at_landing(self):
        self.assertEqual(READER_CEILING, len(set(RECORDED_READERS)))

    def test_26_a_new_direct_reader_in_a_scratch_copy_goes_red(self):
        scratch = os.path.realpath(tempfile.mkdtemp(prefix="vault-ratchet-"))
        try:
            for rel in RECORDED_READERS:
                _write(os.path.join(scratch, rel), 'v = os.environ.get("BM_VAULT_ROOT")\n')
            self.assertEqual(ratchet_problems(direct_env_readers(scratch)), [])
            _write(os.path.join(scratch, "brothermode/tools/bm_vault_newtool.py"),
                   'ap.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT"))\n')
            _write(os.path.join(scratch, "brothermode/tools/test_ignored.py"),
                   'os.environ.get("BM_VAULT_ROOT")\n')
            problems = ratchet_problems(direct_env_readers(scratch))
            self.assertEqual(len(problems), 2, problems)  # over the ceiling AND a new name
            self.assertIn("bm_vault_newtool.py", problems[1])
            self.assertNotIn("test_ignored", " ".join(problems))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
