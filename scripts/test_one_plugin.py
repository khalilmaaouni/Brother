#!/usr/bin/env python3
"""OP1 acceptance: one Brother plugin, end to end. Every class reads the DELIVERABLE (the tree, the generated hooks file,
the guard module run as a process), never a fixture standing in for it. Fixtures below only build the OTHER party a
deliverable meets (a second plugin root, a legacy install, a corrupt manifest). usage: python3 -B scripts/test_one_plugin.py [ClassName]

Reference draft: docs/plan/specs/op1/test_one_plugin.reference.txt. Each OP1 sub unit adds its class here (OP1.b:
DependenciesTest; OP1.c: OneTreeTest; OP1.d: NamespaceTest; OP1.e: CatalogTest; OP1.f: SubmissionTest; OP1.a's
HooksTest is retired for GuardTest, docs/plan/specs/OP1.md 5.1)."""
import glob, json, os, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
BUNDLE = os.path.join(ROOT, "bundle")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _json(path):
    return json.loads(_read(path))


def _hook_commands(path):
    doc = _json(path)
    return [h["command"] for groups in doc["hooks"].values() for g in groups for h in g["hooks"]]


class DependenciesTest(unittest.TestCase):
    """OP1.b: no manifest names another plugin, and the bundle carries what it needs."""

    def setUp(self):
        import bundle_runtime as B
        self.B = B
        self.d = tempfile.mkdtemp(prefix="op1b-")
        # The OTHER party: a bundle carrying every known host manifest, each clean. One test breaks one of them.
        for host in B.KNOWN_HOSTS:
            os.makedirs(os.path.join(self.d, host))
            self._write(host, {"name": "brother", "version": "1.1.0"})

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _write(self, host, doc):
        text = doc if isinstance(doc, str) else json.dumps(doc)
        Path(self.d, host, "plugin.json").write_text(text, encoding="utf-8")

    def test_no_host_manifest_declares_dependencies(self):
        found = sorted(glob.glob(os.path.join(BUNDLE, ".*-plugin", "plugin.json")))
        self.assertGreaterEqual(len(found), 4, found)
        self.assertEqual([p for p in found if "dependencies" in _json(p)], [])

    def test_manifest_problems_refuses_a_dependency_and_an_unreadable_manifest(self):
        B = self.B
        self.assertEqual(B.manifest_problems(self.d), [])
        self._write(".claude-plugin", {"name": "brother", "version": "1.1.0", "dependencies": ["brothermode@^3.4.2"]})
        problems = B.manifest_problems(self.d)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("dependencies", problems[0])
        self.assertIn(".claude-plugin", problems[0])
        self._write(".claude-plugin", "{corrupt")
        problems = B.manifest_problems(self.d)
        self.assertEqual(len(problems), 1, "an unreadable manifest is a problem, never clean: %s" % problems)
        self.assertIn("unreadable", problems[0])
        self._write(".claude-plugin", ["not", "an", "object"])
        problems = B.manifest_problems(self.d)
        self.assertEqual(len(problems), 1, "a manifest that is not an object is a problem: %s" % problems)
        self.assertIn("not a JSON object", problems[0])
        self._write(".claude-plugin", {"name": "brother", "version": "1.1.0"})
        self.assertEqual(B.manifest_problems(self.d), [])
        absent = os.path.join(self.d, "absent")
        self.assertEqual(B.manifest_problems(absent), ["no plugin manifest found under %s" % absent])

    def test_a_deleted_required_host_manifest_is_a_problem(self):
        shutil.rmtree(os.path.join(self.d, ".cursor-plugin"))
        problems = self.B.manifest_problems(self.d)
        self.assertEqual(len(problems), 1, "a deleted required host manifest is reported, not unenumerated: %s" % problems)
        self.assertIn(".cursor-plugin", problems[0])
        self.assertIn("missing", problems[0])

    def test_an_unlisted_host_manifest_directory_is_a_problem_even_at_the_right_version(self):
        # Undotted on purpose: the discovery lists entries, it never globs, so "extra-plugin" and ".extra-plugin" read alike.
        os.makedirs(os.path.join(self.d, "extra-plugin"))
        self._write("extra-plugin", {"name": "brother", "version": "1.1.0"})
        problems = self.B.manifest_problems(self.d)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("extra-plugin", problems[0])
        self.assertIn("KNOWN_HOSTS", problems[0])
        self.assertEqual(self.B.host_manifest_dirs(self.d), sorted(list(self.B.KNOWN_HOSTS) + ["extra-plugin"]))

    def test_the_real_bundle_has_no_manifest_problem(self):
        self.assertEqual(self.B.manifest_problems(BUNDLE), [])
        self.assertEqual(self.B.host_manifest_dirs(BUNDLE), sorted(self.B.KNOWN_HOSTS))

    def test_every_hook_script_resolves_inside_the_bundle(self):
        cmds = _hook_commands(os.path.join(BUNDLE, "hooks", "hooks.json"))
        paths = [m for c in cmds for m in re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/(runtime/[^\s\"']+\.py)", c)]
        self.assertGreaterEqual(len(paths), len(cmds))
        self.assertEqual([p for p in dict.fromkeys(paths) if not os.path.isfile(os.path.join(BUNDLE, p))], [])

    def test_the_engine_modules_the_loop_bridge_imports_ship_in_the_bundle(self):
        tools = os.path.join(BUNDLE, "runtime", "hooks", "brothermode", "tools")
        self.assertEqual([m for m in ("bm_worker_spawn.py", "bm_verify.py", "bm_repair.py") if not os.path.isfile(os.path.join(tools, m))], [])

    def test_every_capability_a_dependency_used_to_supply_is_a_bundle_skill(self):
        skills = {os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(BUNDLE, "skills", "*", "SKILL.md"))}
        for want in ("brothermode-start", "brothersbe-verify", "brotherme-start"):
            self.assertIn(want, skills, "%s was reachable through a dependency and must ship in the one plugin" % want)


class NamespaceTest(unittest.TestCase):
    """OP1.d: the door and the skills speak only /brother: names."""

    def _tree(self, files):
        tmp = tempfile.mkdtemp(prefix="op1d-")
        self.addCleanup(shutil.rmtree, tmp, True)
        for rel, body in files.items():
            p = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            Path(p).write_text(body, encoding="utf-8")
        return tmp

    def test_no_retired_namespace_anywhere_a_user_can_read_it(self):
        import gen_door_table as G
        for want in ("bundle/runtime", "bundle/hooks", "bundle/commands", "bundle/skills"):
            self.assertIn(want, G.USER_SCOPES, "the scan must cover %s" % want)
        hits = G.retired_namespace_hits(ROOT, G.USER_SCOPES)
        self.assertEqual(hits, [], "%d retired-namespace lines, first %s" % (len(hits), hits[:5]))
        # The scanner sees every scope, every text file type, and a bare spelling without the slash.
        tmp = self._tree({"bundle/%s/x%s" % (s, ext): "run brothermode:start now\n"
                          for s in ("runtime", "hooks") for ext in (".md", ".toml", ".dat")})
        planted = G.retired_namespace_hits(tmp, ("bundle/runtime", "bundle/hooks"))
        self.assertEqual(len(planted), 6, planted)
        self.assertEqual(planted[0][1], 1)

    def test_scanner_allowlist_is_exactly_the_record_key_prefix(self):
        import gen_door_table as G
        key = 'x = ("brothermode:outcome-evidence:%s:%s" % (a, b))\n'
        tmp = self._tree({"bundle/runtime/bm_store.py": key + 'print("brothermode:start")\n',
                          "bundle/runtime/other.md": key})
        hits = G.retired_namespace_hits(tmp, ("bundle/runtime",))
        self.assertEqual(sorted((h[0], h[1]) for h in hits),
                         [("bundle/runtime/bm_store.py", 2), ("bundle/runtime/other.md", 1)])
        only_key = self._tree({"bundle/runtime/a.py": key})
        self.assertEqual(G.retired_namespace_hits(only_key, ("bundle/runtime",)), [])
        for ok in ("/brother:start", "products/brothermode:x", "brothermode-start", "brothermode:", "brothermode:1"):
            tree = self._tree({"bundle/runtime/a.md": ok})
            self.assertEqual(G.retired_namespace_hits(tree, ("bundle/runtime",)), [], ok)

    def test_scanner_refuses_hostile_input(self):
        import gen_door_table as G
        for bad in (None, 1, 1.5, float("nan"), True, b"x", ["a"], {"a": 1}):
            with self.assertRaises(ValueError):
                G.retired_namespace_hits(bad, ("bundle",))
            with self.assertRaises(ValueError):
                G.retired_namespace_hits(ROOT, bad)
        for bad in ("bundle", (), ("bundle", None), ([],), ({},), (1,), ("no-such-scope",)):
            with self.assertRaises(ValueError):
                G.retired_namespace_hits(ROOT, bad)
        for bad in (None, 3, True, [], float("nan"), ""):
            self.assertEqual(len(G.check_table(bad)), 1, bad)

    def test_no_skill_description_names_a_retired_command(self):
        import gen_door_table as G
        paths = glob.glob(os.path.join(BUNDLE, "skills", "*", "SKILL.md"))
        self.assertGreater(len(paths), 0)
        bad = []
        for p in paths:
            found = re.search(r"^description:.*$", _read(p), re.M)
            self.assertIsNotNone(found, "%s has no description" % p)
            if re.search(G.RETIRED_NAMESPACE, found.group(0)):
                bad.append(p)
        self.assertEqual(bad, [])
        self.assertTrue(re.search(G.RETIRED_NAMESPACE, "description: Invoke as /brothersbe:verify."))

    def test_the_door_table_is_generated_from_bundle_skills_and_current(self):
        import gen_door_table as G
        self.assertEqual(G.check_table(ROOT), [])
        cells = {}
        for column in G.collect(ROOT).values():
            for inv in column.values():
                cells[inv.split(":", 1)[1]] = inv
        skills = {os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(BUNDLE, "skills", "*", "SKILL.md"))}
        rows = {s for s in skills if G.split_name(s)}
        self.assertEqual(set(cells), rows, "a row for every bundle skill but the aliases and the door")
        self.assertEqual([s for s in skills if s.startswith(("brotherme-", "using-")) and s in cells], [])
        self.assertEqual([k for k, v in cells.items() if v not in ("/brother:" + k, "skill brother:" + k)], [])
        typeless = {k for k in rows if re.search(r"^user-invocable:\s*false\s*$", _read(os.path.join(BUNDLE, "skills", k, "SKILL.md")), re.M | re.I)}
        self.assertEqual({k for k, v in cells.items() if v.startswith("skill ")}, typeless, "a skill the host does not register as typeable never gets a slash cell")
        # Reading products/*/skills again would find a column here; the bundle path finds none.
        tmp = self._tree({"products/brothermode/skills/brothermode-start/SKILL.md": "---\nname: brothermode-start\n---\n"})
        with self.assertRaises(ValueError):
            G.collect(tmp)

    def test_check_table_goes_red_on_a_stale_door_and_on_a_missing_sentinel(self):
        import gen_door_table as G
        tmp = tempfile.mkdtemp(prefix="op1d-")
        try:
            shutil.copytree(BUNDLE, os.path.join(tmp, "bundle"), ignore=shutil.ignore_patterns("runtime", "codex-skills"))
            self.assertEqual(G.check_table(tmp), [])
            door = os.path.join(tmp, "bundle", "commands", "brother.md"); text = _read(door)
            Path(door).write_text(text.replace("/brother:", "/brothermode:", 1))
            self.assertTrue(G.check_table(tmp))
            Path(door).write_text(text.replace("| start |", "| begin |", 1))
            self.assertTrue([d for d in G.check_table(tmp) if "DRIFT" in d], "a stale table cell is drift")
            Path(door).write_text(text.replace(G.BEGIN, ""))
            self.assertTrue(G.check_table(tmp), "no sentinel is a problem, never clean")
            Path(door).write_text(text + "\nTry /brother:no-such-skill-here.\n")
            dead = G.check_table(tmp)
            self.assertTrue([d for d in dead if "no-such-skill-here" in d], dead)
            shutil.rmtree(os.path.join(tmp, "bundle", "skills"))
            self.assertTrue(G.check_table(tmp), "no skills is a problem, never clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_every_brother_colon_name_the_bundle_mentions_resolves(self):
        import gen_door_table as G
        self.assertEqual(G.dead_mentions(ROOT), [])


class OneTreeTest(unittest.TestCase):
    """OP1.c: one directory carries plugin manifests, and every host manifest is version checked."""

    def _manifests_outside_bundle(self):
        return sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "plugin", ".*-plugin", "*.json")))

    def test_no_host_manifest_exists_outside_the_bundle_and_the_retired_product_sources(self):
        found = []
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
            rel = os.path.relpath(dirpath, ROOT)
            if re.search(r"(^|/)\.[a-z]+-plugin$", rel) and "plugin.json" in files and not rel.startswith(("bundle/", "products/")):
                found.append(rel)
        self.assertEqual(sorted(found), [], "a second installable tree exists")

    def test_the_antigravity_adapter_ships_from_the_bundle(self):
        ag = os.path.join(BUNDLE, ".antigravity-plugin")
        for rel in ("plugin.json", "hooks.json", "mcp_config.json", os.path.join("rules", "brother.md"), os.path.join("scripts", "brother_antigravity_hook.py")):
            self.assertTrue(os.path.isfile(os.path.join(ag, rel)), rel)
        self.assertEqual(_read(os.path.join(ag, "skills", "using-brother", "SKILL.md")), _read(os.path.join(BUNDLE, "skills", "using-brother", "SKILL.md")))

    def test_every_bundle_host_manifest_carries_the_umbrella_version(self):
        want = _json(os.path.join(ROOT, ".claude-plugin", "marketplace.json"))["metadata"]["version"]
        got = {p: _json(p)["version"] for p in glob.glob(os.path.join(BUNDLE, ".*-plugin", "plugin.json"))}
        self.assertEqual({p: v for p, v in got.items() if v != want}, {})
        self.assertIn(os.path.join(BUNDLE, ".antigravity-plugin", "plugin.json"), got)

    def _copy_tree(self, tmp):
        for rel in (".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json", "docs/VERSIONING.md"):
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True); shutil.copy(os.path.join(ROOT, rel), os.path.join(tmp, rel))
        for p in glob.glob(os.path.join(BUNDLE, ".*-plugin", "plugin.json")):
            rel = os.path.relpath(p, ROOT); os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True); shutil.copy(p, os.path.join(tmp, rel))

    def test_the_version_check_lists_every_host_manifest_and_goes_red_on_each(self):
        import version_source as V
        tmp = tempfile.mkdtemp(prefix="op1c-")
        try:
            self._copy_tree(tmp)
            names = [os.path.relpath(p, tmp) for p in V.host_manifests(Path(tmp))]
            self.assertEqual(sorted(names), sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(BUNDLE, ".*-plugin", "plugin.json"))))
            for rel in names:
                p = os.path.join(tmp, rel); doc = _json(p); good = json.dumps(doc)
                doc["version"] = "0.0.1"; Path(p).write_text(json.dumps(doc))
                with self.subTest(manifest=rel, case="wrong version"):
                    self.assertEqual(V.run_check(Path(tmp)), 1)
                del doc["version"]; Path(p).write_text(json.dumps(doc))
                with self.subTest(manifest=rel, case="no version key"):
                    self.assertEqual(V.run_check(Path(tmp)), 1, "a versionless manifest is DRIFT, never skipped")
                Path(p).write_text("{not json")
                with self.subTest(manifest=rel, case="not json"):
                    self.assertEqual(V.run_check(Path(tmp)), 1, "an unreadable manifest is DRIFT, never skipped")
                Path(p).write_text(good)
            os.makedirs(os.path.join(tmp, "bundle", ".zed-plugin")); Path(tmp, "bundle", ".zed-plugin", "plugin.json").write_text('{"name":"brother","version":"9.9.9"}')
            self.assertEqual(V.run_check(Path(tmp)), 1, "a fifth host manifest nobody listed must still be read")
            want = _json(os.path.join(tmp, ".claude-plugin", "marketplace.json"))["metadata"]["version"]
            Path(tmp, "bundle", ".zed-plugin", "plugin.json").write_text(json.dumps({"name": "brother", "version": want}))
            self.assertEqual(V.run_check(Path(tmp)), 1, "an unlisted host is refused even at the right version until it is added to the known set and the parity map")
            shutil.rmtree(os.path.join(tmp, "bundle", ".zed-plugin")); os.makedirs(os.path.join(tmp, "bundle", "zed-plugin")); Path(tmp, "bundle", "zed-plugin", "plugin.json").write_text('{"name":"brother","version":"9.9.9"}')
            self.assertEqual(V.run_check(Path(tmp)), 1, "a host directory without a leading dot is discovered too")
            shutil.rmtree(os.path.join(tmp, "bundle"))
            self.assertEqual(V.host_manifests(Path(tmp)), [])
            self.assertEqual(V.run_check(Path(tmp)), 2, "no host manifest at all is NO-DATA, never a pass")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_real_tree_version_check_passes(self):
        import version_source as V
        self.assertEqual(V.run_check(Path(ROOT)), 0)


class GuardTest(unittest.TestCase):
    """OP1.a and OP1.c (docs/plan/specs/OP1.md 5.1): the landed guard and adapter suites run as child processes, so the
    unit's one done check covers them. Each must exit 0 with OK on its last line."""

    def _suite(self, *argv):
        proc = subprocess.run([sys.executable, "-B"] + list(argv), cwd=ROOT, capture_output=True, text=True, timeout=600)
        lines = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
        self.assertEqual(proc.returncode, 0, (proc.stdout + proc.stderr)[-2000:])
        self.assertEqual(lines[-1].strip() if lines else "", "OK", (proc.stdout + proc.stderr)[-2000:])

    def test_the_hook_guard_suite_is_green(self):
        self._suite(os.path.join(HERE, "test_hook_guard.py"))

    def test_the_antigravity_adapter_suite_is_green(self):
        self._suite(os.path.join(HERE, "test_brother_antigravity_hook.py"))

    def test_the_bundle_refuses_hook_drift(self):
        self._suite(os.path.join(HERE, "test_bundle_runtime.py"), "OnePluginHooksRefuseDrift")


class CatalogTest(unittest.TestCase):
    """OP1.e: five catalogs, one plugin, and a path for a user of each old plugin."""

    def _skip_unless_cut(self):
        """The two real tree cases read ROOT and go green only after the 1.1.0 cut has applied the catalog edit.
        Skipped, with the reason printed, while the tree still names a catalog problem or a pending edit, so a
        check that cannot run is NO-DATA, never a pass (H1)."""
        import donecheck_u8 as U
        import retire_catalogs as RC
        try:
            pending = RC.pending(ROOT)
        except (OSError, ValueError) as exc:
            self.fail("the real tree's catalogs could not be read (%s); that is a problem, never a skip" % exc)
        problems = U.catalog_problems(ROOT)
        if pending or problems:
            self.skipTest(
                "the 1.1.0 cut has not applied the catalog edit yet: "
                "retire_catalogs.pending(ROOT) lists %s and donecheck_u8.catalog_problems(ROOT) names %d "
                "problem(s) (first: %s). This test is skipped, never passed early. The red instrument until "
                "the cut is `python3 scripts/retire_catalogs.py --check` (exit 1 today)."
                % (pending or "nothing", len(problems), problems[0] if problems else "none"))

    def test_the_real_tree_lists_exactly_one_plugin_in_every_catalog(self):
        self._skip_unless_cut()
        import donecheck_u8 as U
        self.assertEqual(U.catalog_problems(ROOT), [])

    def test_catalog_problems_names_each_way_a_catalog_can_be_wrong(self):
        import donecheck_u8 as U
        tmp = tempfile.mkdtemp(prefix="op1e-")
        try:
            def fresh():
                shutil.rmtree(tmp, ignore_errors=True)
                os.makedirs(tmp)
                for rel in (".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json",
                            ".agents/plugins/marketplace.json", "bundle/MANIFEST.json",
                            "bundle/.claude-plugin/plugin.json"):
                    os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                    shutil.copy(os.path.join(ROOT, rel), os.path.join(tmp, rel))
                version = _json(os.path.join(tmp, "bundle/.claude-plugin/plugin.json"))["version"]
                for cat in (".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json",
                            ".agents/plugins/marketplace.json"):
                    doc = _json(os.path.join(tmp, cat))
                    doc["plugins"] = [p for p in doc["plugins"] if p["name"] == "brother"]
                    Path(os.path.join(tmp, cat)).write_text(json.dumps(doc))
                doc = _json(os.path.join(tmp, "bundle/MANIFEST.json"))
                doc["shipped_plugins"] = ["brother"]
                Path(os.path.join(tmp, "bundle/MANIFEST.json")).write_text(json.dumps(doc))
                return version

            def edit(rel, fn):
                p = os.path.join(tmp, rel)
                d = _json(p)
                fn(d)
                Path(p).write_text(json.dumps(d))

            cases = {
                "a second plugin in the Claude catalog": (
                    ".claude-plugin/marketplace.json",
                    lambda d: d["plugins"].append(dict(d["plugins"][0], name="brothermode"))),
                "a second plugin in the Cursor catalog": (
                    ".cursor-plugin/marketplace.json",
                    lambda d: d["plugins"].append(dict(d["plugins"][0], name="brothersbe"))),
                "a second plugin in the Codex catalog": (
                    ".agents/plugins/marketplace.json",
                    lambda d: d["plugins"].append(dict(d["plugins"][0], name="brotherds"))),
                "a wrong ref": (
                    ".claude-plugin/marketplace.json",
                    lambda d: d["plugins"][0]["source"].update(ref="v0.9.0")),
                "a stale manifest list": (
                    "bundle/MANIFEST.json",
                    lambda d: d.update(shipped_plugins=["brother", "brothermode"])),
                "a Claude catalog whose own metadata version disagrees": (
                    ".claude-plugin/marketplace.json",
                    lambda d: d["metadata"].update(version="0.9.0")),
                "a Claude entry with the wrong version": (
                    ".claude-plugin/marketplace.json",
                    lambda d: d["plugins"][0].update(version="0.9.0")),
                "a Claude entry that does not point at the bundle": (
                    ".claude-plugin/marketplace.json",
                    lambda d: d["plugins"][0]["source"].update(path="products/brothermode")),
                "a Cursor entry that does not point at the bundle": (
                    ".cursor-plugin/marketplace.json",
                    lambda d: d["plugins"][0].update(source="products/brothermode")),
                "a Cursor entry with no version": (
                    ".cursor-plugin/marketplace.json",
                    lambda d: d["plugins"][0].pop("version")),
                "a Codex entry that does not point at the bundle": (
                    ".agents/plugins/marketplace.json",
                    lambda d: d["plugins"][0]["source"].update(path="./products/brothermode")),
                "a catalog that keeps one plugin under another name": (
                    ".cursor-plugin/marketplace.json",
                    lambda d: d["plugins"][0].update(name="brothermode")),
            }
            for label, (rel, fn) in cases.items():
                fresh()
                base = U.catalog_problems(tmp)
                edit(rel, fn)
                with self.subTest(case=label):
                    self.assertEqual(base, [], "the end state fixture must itself be clean")
                    self.assertTrue(U.catalog_problems(tmp), label)
            # Each of the next conditions ALONE on a clean end state, so only its own rule can report it (review
            # 2026-10-06: the missing catalog case used to run on a tree that already held an extra catalog, and
            # deleting the missing catalog rule changed nothing).
            fresh()
            os.makedirs(os.path.join(tmp, "products", "brothermode", ".claude-plugin"), exist_ok=True)
            Path(os.path.join(tmp, "products", "brothermode", ".claude-plugin", "marketplace.json")).write_text(
                '{"name":"x","plugins":[]}')
            extra = U.catalog_problems(tmp)
            self.assertEqual(len(extra), 1, extra)
            self.assertIn("products/brothermode/.claude-plugin/marketplace.json", extra[0],
                          "a sixth catalog nobody listed is a problem")
            for rel in (".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json",
                        ".agents/plugins/marketplace.json"):
                fresh()
                os.remove(os.path.join(tmp, rel))
                missing = U.catalog_problems(tmp)
                with self.subTest(missing=rel):
                    self.assertTrue([p for p in missing if "missing" in p and rel in p],
                                    "a missing catalog is a problem of its own, never clean: %r" % (missing,))
            # What the reader cannot read is a problem too, each alone on a clean end state: the bundle manifest,
            # the one version source, and a catalog that is not JSON.
            for label, rel in (("a deleted bundle manifest", "bundle/MANIFEST.json"),
                               ("an unreadable version source", "bundle/.claude-plugin/plugin.json")):
                fresh()
                self.assertEqual(U.catalog_problems(tmp), [], "the end state fixture must itself be clean")
                os.remove(os.path.join(tmp, rel))
                with self.subTest(case=label):
                    self.assertTrue([p for p in U.catalog_problems(tmp) if rel in p], label)
            fresh()
            Path(os.path.join(tmp, "bundle/.claude-plugin/plugin.json")).write_text('{"name": "brother"}')
            self.assertTrue([p for p in U.catalog_problems(tmp) if "version" in p],
                            "a version source that names no version is a problem, never a skipped comparison")
            fresh()
            Path(os.path.join(tmp, ".cursor-plugin/marketplace.json")).write_text("{not json")
            self.assertTrue([p for p in U.catalog_problems(tmp) if ".cursor-plugin" in p],
                            "a corrupt catalog is a problem, never clean")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_applier_reaches_the_end_state_the_reader_accepts(self):
        """The cut's ONE applier (retire_catalogs.apply) and the ONE reader (donecheck_u8.catalog_problems) must
        agree on a copy of the real tree's catalogs: before the apply the reader names problems, after it none,
        and that includes bundle/MANIFEST.json, which the applier regenerates with the tree's own tool. Without
        the regeneration the cut would retire three catalog entries and ship a manifest that still lists four."""
        import donecheck_u8 as U
        import retire_catalogs as RC
        # The skip comes BEFORE anything is copied: once the cut has applied the edit the two product catalogs
        # this copies are gone, and the test must skip there, never error (review 2026-10-06).
        if not RC.pending(ROOT):
            self.skipTest("the cut has already applied the catalog edit to this tree, so there is no before state "
                          "to copy; test_the_real_tree_lists_exactly_one_plugin_in_every_catalog judges the real "
                          "tree instead")
        tmp = tempfile.mkdtemp(prefix="op1e-apply-")
        try:
            for rel in (".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json",
                        ".agents/plugins/marketplace.json", "bundle/MANIFEST.json",
                        "bundle/.claude-plugin/plugin.json", "scripts/surface_budget.py",
                        "products/brothermode/.claude-plugin/marketplace.json",
                        "products/brothersbe/.claude-plugin/marketplace.json"):
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                shutil.copy(os.path.join(ROOT, rel), os.path.join(tmp, rel))
            for rel in ("bundle/skills", "bundle/commands"):
                shutil.copytree(os.path.join(ROOT, rel), os.path.join(tmp, rel), symlinks=True)
            version = _json(os.path.join(tmp, "bundle/.claude-plugin/plugin.json"))["version"]
            shutil.copy(os.path.join(HERE, "donecheck_u8.py"), os.path.join(tmp, "scripts", "donecheck_u8.py"))

            class Verdict(object):
                """A check's exit code TOGETHER with its own verdict word, so a crash (a traceback also exits
                nonzero) can never stand in for NOT DONE: returncode is -1 unless the output carries the verdict
                that the exit code claims."""
                def __init__(self, proc, done_word):
                    out = proc.stdout + proc.stderr
                    said_done = done_word in proc.stdout and "NOT DONE" not in out
                    said_not_done = "NOT DONE" in out
                    agrees = (proc.returncode == 0 and said_done) or (proc.returncode == 1 and said_not_done)
                    self.returncode = proc.returncode if agrees else -1
                    self.stdout = out

            def bare_check():
                """The tree's OWN copy of the check, run with no argument: it judges the tree it lives in."""
                return Verdict(subprocess.run([sys.executable, "-B", os.path.join(tmp, "scripts", "donecheck_u8.py")],
                                              capture_output=True, text=True, timeout=120, cwd=tmp), "DONE")

            def chain_check():
                """The command the cut chain runs after the apply, aimed at this tree."""
                return Verdict(subprocess.run([sys.executable, "-B", os.path.join(HERE, "retire_catalogs.py"),
                                               "--check", "--version", version, "--root", tmp],
                                              capture_output=True, text=True, timeout=120, cwd=tmp), "DONE")

            before = U.catalog_problems(tmp)
            self.assertTrue([p for p in before if "MANIFEST.json" in p], before)
            self.assertTrue([p for p in before if ".claude-plugin/marketplace.json" in p], before)
            self.assertTrue(RC.end_state_problems(tmp), "the reader the chain calls names the before state")
            self.assertEqual(bare_check().returncode, 1, "before the apply the bare check is NOT DONE")
            self.assertEqual(chain_check().returncode, 1, "before the apply the chain's check is NOT DONE")
            changed = RC.apply(tmp, version)
            self.assertIn(os.path.join("bundle", "MANIFEST.json"), changed)
            self.assertEqual(_json(os.path.join(tmp, "bundle/MANIFEST.json"))["shipped_plugins"], ["brother"])
            self.assertEqual(U.catalog_problems(tmp), [])
            self.assertEqual(RC.end_state_problems(tmp), [])
            self.assertEqual(RC.apply(tmp, version), [], "a second apply changes nothing")
            done = bare_check()
            self.assertEqual((done.returncode, "DONE" in done.stdout), (0, True), done.stdout[-300:])
            self.assertEqual(chain_check().returncode, 0, "after the apply the chain's check is DONE")
            self.assertEqual(U.main([tmp]), 0, "directory mode agrees on the end state")
            # Three defects, each ALONE on the end state, each must turn the bare check, directory mode and the
            # chain's check red: only the Cursor catalog wrong (a bare check reading only the Claude catalog
            # would pass it), only the manifest stale (a directory mode that skipped the reader, or a chain
            # check that never called it, would pass it), and bundle/ gone (nothing left to vouch for a version).
            cursor = os.path.join(tmp, ".cursor-plugin", "marketplace.json")
            manifest = os.path.join(tmp, "bundle", "MANIFEST.json")
            good_cursor, good_manifest = _read(cursor), _read(manifest)
            doc = _json(cursor)
            doc["plugins"].append(dict(doc["plugins"][0], name="brothermode"))
            Path(cursor).write_text(json.dumps(doc))
            self.assertEqual(bare_check().returncode, 1, "the bare check reads every catalog, never only Claude's")
            self.assertEqual(chain_check().returncode, 1)
            Path(cursor).write_text(good_cursor)
            doc = _json(manifest)
            doc["shipped_plugins"] = ["brother", "brothermode"]
            Path(manifest).write_text(json.dumps(doc))
            self.assertEqual(bare_check().returncode, 1, "a stale manifest alone is NOT DONE for the bare check")
            self.assertEqual(U.main([tmp]), 1, "and for directory mode")
            self.assertEqual(chain_check().returncode, 1, "and for the check the cut chain runs")
            Path(manifest).write_text(good_manifest)
            self.assertEqual(chain_check().returncode, 0, "restored, the chain's check is DONE again")
            shutil.move(os.path.join(tmp, "bundle"), os.path.join(tmp, "bundle-moved-away"))
            self.assertEqual(chain_check().returncode, 1, "a tree with no bundle/ is never the end state")
            shutil.move(os.path.join(tmp, "bundle-moved-away"), os.path.join(tmp, "bundle"))
            # A manifest that exists and cannot be regenerated stops the apply; it never leaves a half end state.
            os.remove(os.path.join(tmp, "scripts", "surface_budget.py"))
            with self.assertRaises(OSError):
                RC.apply(tmp, version)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_retirement_check_command_exits_zero_on_the_end_state(self):
        self._skip_unless_cut()
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "donecheck_u8.py")],
                           capture_output=True, text=True, timeout=120, cwd=ROOT)
        self.assertEqual((r.returncode, "DONE" in r.stdout), (0, True), r.stdout[-300:])

    def test_the_surface_test_agrees_with_one_plugin(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("op1_test_surface", os.path.join(ROOT, "tests", "test_surface.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
        with open(os.devnull, "w") as sink:
            res = unittest.TextTestRunner(stream=sink).run(suite)
        self.assertTrue(res.wasSuccessful() and res.testsRun > 0,
                        [str(f[0]) for f in res.failures + res.errors][:3])

    def test_every_old_plugin_help_names_the_migration_and_the_guide_exists(self):
        for p in ("brothermode", "brothersbe", "brotherds"):
            f = os.path.join(ROOT, "products", p, "skills", "help" if p != "brotherds" else "brotherds", "SKILL.md")
            self.assertIn("install brother@brother", _read(f), f)
        guide = _read(os.path.join(ROOT, "docs", "how-to", "migrate-to-one-plugin.md"))
        for needle in ("brother@brother", "brothermode", "brothersbe", "brotherds"):
            self.assertIn(needle, guide)
        for host in ("Claude Code", "Codex", "Cursor", "Antigravity"):
            self.assertRegex(guide, r"(?m)^#{2,4} .*" + host, "a migration section for " + host)
        self.assertNotRegex(guide, "[\u2013\u2014]")


class SubmissionTest(unittest.TestCase):
    """OP1.f: the readiness bars, one command whose every row is PASS, FAIL or NO-DATA and whose exit is 0 only on an
    all PASS report over the exact bar set (R19 to R22, R24). The host bars are driven with PATH stripped to
    /usr/bin:/bin, so no real install or validator runs here; the live run is the owner's. The bars that read a tree
    are also run on fixture trees that isolate one condition each, since the real tree holds one state at a time."""
    SCRIPT = os.path.join(HERE, "one_plugin_readiness.py")
    CORPUS = os.path.join(ROOT, "docs", "plan", "one-plugin-trigger-corpus.json")
    GUARD = 'python3 "${CLAUDE_PLUGIN_ROOT}/runtime/hooks/hook_guard.py" '

    def _run(self, *args, path=None):
        env = dict(os.environ, PATH=path if path is not None else os.environ.get("PATH", ""),
                   HOME=tempfile.mkdtemp(prefix="op1f-"))
        return subprocess.run([sys.executable, "-B", self.SCRIPT] + list(args),
                              capture_output=True, text=True, timeout=300, env=env, cwd=ROOT)

    def _rows(self, *args, path=None):
        r = self._run(*args, path=path)
        return r.returncode, {x["id"]: x for x in json.loads(r.stdout)}

    def test_static_bars_pass_on_the_real_tree(self):
        """R20: --static names exactly the six static bars; five PASS, and catalogs-one-plugin reads NO-DATA (exit 2)
        while the cut is pending and PASS (exit 0) after it, never 0 while four catalogs remain. The default output
        is the same rows, one JSON object per line."""
        import one_plugin_readiness as R
        import retire_catalogs as RC
        code, rows = self._rows("--static", "--json")
        self.assertEqual(set(rows), set(R.STATIC_BARS), sorted(rows))
        others = {k: v["verdict"] for k, v in rows.items() if k != "catalogs-one-plugin"}
        self.assertEqual(others, dict.fromkeys(others, "PASS"), {k: v["detail"] for k, v in rows.items()})
        catalogs = rows["catalogs-one-plugin"]
        if RC.pending(ROOT):
            self.assertEqual((catalogs["verdict"], code), ("NO-DATA", 2), catalogs)
            self.assertIn("awaiting the 1.1.0 cut", catalogs["detail"])
        else:
            self.assertEqual((catalogs["verdict"], code), ("PASS", 0), catalogs)
        plain = self._run("--static")
        self.assertEqual([json.loads(line)["id"] for line in plain.stdout.splitlines()], list(R.STATIC_BARS))
        self.assertEqual(plain.returncode, code)

    def test_static_bars_name_each_bar(self):
        ids = set(self._rows("--static", "--json")[1])
        self.assertTrue({"one-manifest-dependency-free", "hooks-guarded", "namespace-clean", "versions-agree",
                         "catalogs-one-plugin", "skill-descriptions"} <= ids, ids)

    def test_no_rows_is_no_data_never_a_pass(self):
        """R19, M-OP1F-1, M-OP1F-7: an empty report is 2; an all PASS static or full report is 0; one NO-DATA row is 2;
        a FAIL is 1 whatever else the report holds; a report that is not a list is 2."""
        import one_plugin_readiness as R
        static = [{"id": b, "verdict": "PASS"} for b in R.STATIC_BARS]
        full = [{"id": b, "verdict": "PASS"} for b in R.BARS]
        one_nodata = [dict(x) for x in static]
        one_nodata[-1]["verdict"] = "NO-DATA"
        one_fail = [dict(x) for x in static]
        one_fail[0]["verdict"] = "FAIL"
        fail_and_nodata = [dict(x) for x in one_nodata]
        fail_and_nodata[0]["verdict"] = "FAIL"
        self.assertEqual((R.verdict([]), R.verdict(static), R.verdict(full), R.verdict(one_nodata),
                          R.verdict(one_fail), R.verdict(fail_and_nodata), R.verdict(None)), (2, 0, 0, 2, 1, 1, 2))

    def test_a_missing_bar_is_never_a_pass(self):
        """M-OP1F-8: a required bar left out, an unknown id, a repeated id, a malformed row or a verdict spelled
        wrong reads 2, never 0; a FAIL beside any of them reads 1."""
        import one_plugin_readiness as R
        static = [{"id": b, "verdict": "PASS"} for b in R.STATIC_BARS]
        self.assertEqual(R.verdict(static[:-1]), 2, "a missing bar")
        self.assertEqual(R.verdict(static + [{"id": "not-a-bar", "verdict": "PASS"}]), 2, "an unknown id")
        self.assertEqual(R.verdict(static + [dict(static[0])]), 2, "a repeated id")
        self.assertEqual(R.verdict(static[:-1] + ["PASS"]), 2, "a malformed row")
        self.assertEqual(R.verdict([dict(x, verdict="pass") for x in static]), 2, "a verdict spelled wrong")
        self.assertEqual(R.verdict(static[:-1] + [{"id": "not-a-bar", "verdict": "FAIL"}]), 1, "a FAIL is 1")

    def test_with_no_claude_binary_the_full_run_is_no_data_never_a_pass(self):
        """R19, M-OP1F-1: with PATH stripped to /usr/bin:/bin every host bar reads NO-DATA, the full report names all
        thirteen bars, and the exit is 2, never 0, whatever the static bars say."""
        import one_plugin_readiness as R
        code, rows = self._rows("--json", path="/usr/bin:/bin")
        self.assertEqual(set(rows), set(R.BARS), sorted(rows))
        for bar in R.HOST_BARS:
            self.assertEqual(rows[bar]["verdict"], "NO-DATA", rows[bar])
        self.assertIn("no claude binary", rows["plugin-validate"]["detail"])
        self.assertEqual(code, 2)

    def _record(self, rows):
        f = os.path.join(tempfile.mkdtemp(prefix="op1f-rec-"), "rec.jsonl")
        Path(f).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return f

    def _trigger(self, record, *extra):
        return self._rows("--json", "--record", record, *extra, path="/usr/bin:/bin")[1]["skill-triggering"]

    def test_a_recorded_run_is_judged_on_coverage_provenance_and_match(self):
        """R21, M-OP1F-2, M-OP1F-3: an absent or partial record is NO-DATA; full coverage with zero misroutes is PASS
        and says `owner record, unsigned`; a retired name, one misroute, a missing provenance field, a missing
        invoked value or a present record that cannot be read is FAIL; a `brother:` prefix is normalised away."""
        corpus = _json(self.CORPUS)["asks"]

        def row(c, **k):
            base = {"ask": c["ask"], "expect": c["expect"], "invoked": c["expect"], "host": "claude-code",
                    "claude_version": "x", "ts": "2026-09-30T00:00:00Z"}
            base.update(k)
            return base

        absent = self._trigger(os.path.join(tempfile.mkdtemp(prefix="op1f-"), "absent.jsonl"))
        self.assertEqual(absent["verdict"], "NO-DATA", absent)
        partial = self._trigger(self._record([row(c) for c in corpus[:-1]]))
        self.assertEqual(partial["verdict"], "NO-DATA", "a partial record proves nothing")
        full = self._trigger(self._record([row(c) for c in corpus]))
        self.assertEqual((full["verdict"], "owner record, unsigned" in full["detail"]), ("PASS", True), full)
        prefixed = self._trigger(self._record([row(c, invoked="brother:" + c["expect"]) for c in corpus]))
        self.assertEqual(prefixed["verdict"], "PASS", prefixed)
        without_ts = row(corpus[-1])
        del without_ts["ts"]
        cases = {
            "a retired name invoked": [row(c) for c in corpus[:-1]] + [row(corpus[-1], invoked="brothermode:start")],
            "every ask routed to the wrong skill": [row(c, invoked="using-brother") for c in corpus],
            "exactly one misroute": [row(c) for c in corpus[:-1]] + [row(corpus[-1], invoked="using-brother")],
            "no claude_version": [row(c) for c in corpus[:-1]] + [row(corpus[-1], claude_version=None)],
            "no host": [row(c) for c in corpus[:-1]] + [row(corpus[-1], host="")],
            "no ts": [row(c) for c in corpus[:-1]] + [without_ts],
            "no invoked value": [row(c) for c in corpus[:-1]] + [row(corpus[-1], invoked=None)],
        }
        for label, rows in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self._trigger(self._record(rows))["verdict"], "FAIL", label)
        corrupt = os.path.join(tempfile.mkdtemp(prefix="op1f-"), "corrupt.jsonl")
        Path(corrupt).write_text("{not json\n", encoding="utf-8")
        self.assertEqual(self._trigger(corrupt)["verdict"], "FAIL",
                         "a present record that cannot be read is a defect, never NO-DATA")

    def test_the_smoke_script_proves_one_plugin_and_no_leaf_probe_remains(self):
        """R24, M-OP1F-5: the leaf probe is gone (a one entry marketplace would abort on it), the two assertions that
        replace it are present, and the script still parses."""
        path = os.path.join(HERE, "bundle-install-smoke.sh")
        text = _read(path)
        for gone in ("leaves-published.txt", "excluded_leaf", "git-subdir", "leaves.txt", "EXCLUDED_LEAVES"):
            self.assertNotIn(gone, text, gone)
        self.assertIn("for OLD in brothermode@ brothersbe@ brotherds@", text,
                      "the negative assertion: no retired plugin was pulled in")
        self.assertIn('grep -q "brother@brother" "$WORK/list.log"', text)
        self.assertIn("products/*/.claude-plugin/marketplace.json", text,
                      "the tree level assertion: no product catalog in the installed tree")
        self.assertEqual(subprocess.run(["sh", "-n", path], capture_output=True).returncode, 0)
        self.assertNotRegex(text, "[" + chr(0x2013) + chr(0x2014) + "]")

    def test_every_corpus_expect_names_a_bundle_skill_and_a_bad_corpus_is_a_fail(self):
        """R22, M-OP1F-6: every expect of the real corpus is a bundle/skills directory and the corpus validates; a
        corpus whose expect names no bundle skill or a retired spelling, carries no expect, repeats an ask, is empty,
        is not an object, lacks a door verb or holds too few asks makes the bar FAIL."""
        import one_plugin_readiness as R
        corpus = _json(self.CORPUS)
        skills = {os.path.basename(os.path.dirname(p)) for p in glob.glob(os.path.join(BUNDLE, "skills", "*", "SKILL.md"))}
        self.assertEqual([c for c in corpus["asks"] if c.get("expect") not in skills], [])
        self.assertEqual(R.corpus_problems(corpus, skills), [])
        tmp = tempfile.mkdtemp(prefix="op1f-corpus-")
        rec = os.path.join(tmp, "rec.jsonl")
        Path(rec).write_text("", encoding="utf-8")
        good = corpus["asks"]
        bad_cases = {
            "an expect that names no bundle skill": {"asks": good[:-1] + [dict(good[-1], expect="brothermode-nothing")]},
            "a retired spelling as expect": {"asks": good[:-1] + [dict(good[-1], expect="brothermode:start")]},
            "an ask with no expect": {"asks": good[:-1] + [{"ask": "no expect"}]},
            "an empty corpus": {"asks": []},
            "not an object": [],
            "a repeated ask": {"asks": good + [dict(good[0])]},
            "a door verb dropped": {"asks": [a for a in good if not a["expect"].endswith("-deliver")]},
            "too few asks": {"asks": good[:20]},
        }
        for label, doc in bad_cases.items():
            bad = os.path.join(tmp, "corpus.json")
            Path(bad).write_text(json.dumps(doc), encoding="utf-8")
            with self.subTest(case=label):
                self.assertTrue(R.corpus_problems(doc, skills), label)
                self.assertEqual(self._trigger(rec, "--corpus", bad)["verdict"], "FAIL", label)

    def test_the_corpus_covers_every_door_verb(self):
        """R22, M-OP1F-4: an ask for each door verb of gen_door_table.CORE_ORDER, at least 21 asks, no retired name,
        no repeated ask."""
        import gen_door_table as G
        corpus = _json(self.CORPUS)
        expects = {c["expect"] for c in corpus["asks"]}
        for verb in G.CORE_ORDER:
            self.assertTrue(any(e.endswith("-" + verb) for e in expects), verb)
        self.assertGreaterEqual(len(corpus["asks"]), 21)
        self.assertNotRegex(json.dumps(corpus), G.RETIRED_NAMESPACE)
        self.assertEqual(len({c["ask"] for c in corpus["asks"]}), len(corpus["asks"]), "no ask repeats")

    def test_the_catalogs_bar_is_no_data_before_the_cut_fail_on_any_other_problem_and_pass_after(self):
        """R20, decision 9: on a copy of the real catalogs the bar is NO-DATA while the cut is pending (PASS once it
        applied); a sixth catalog or a foreign name beside brother is FAIL, never NO-DATA; after the applier runs the
        bar is PASS, and a stale bundle/MANIFEST.json is then FAIL, since nothing is pending any more."""
        import one_plugin_readiness as R
        import retire_catalogs as RC
        tmp = tempfile.mkdtemp(prefix="op1f-cat-")
        try:
            for rel in (".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json",
                        ".agents/plugins/marketplace.json", "bundle/MANIFEST.json",
                        "bundle/.claude-plugin/plugin.json", "scripts/surface_budget.py",
                        "products/brothermode/.claude-plugin/marketplace.json",
                        "products/brothersbe/.claude-plugin/marketplace.json"):
                src = os.path.join(ROOT, rel)
                if not os.path.exists(src):
                    continue   # after the cut the two product catalogs are gone from the real tree
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                shutil.copy(src, os.path.join(tmp, rel))
            for rel in ("bundle/skills", "bundle/commands"):
                shutil.copytree(os.path.join(ROOT, rel), os.path.join(tmp, rel), symlinks=True)
            version = _json(os.path.join(tmp, "bundle/.claude-plugin/plugin.json"))["version"]
            before = R.catalogs_row(tmp)
            self.assertEqual(before["verdict"], "NO-DATA" if RC.pending(tmp) else "PASS", before)
            os.makedirs(os.path.join(tmp, "extra"))
            Path(os.path.join(tmp, "extra", "marketplace.json")).write_text('{"name":"x","plugins":[]}', encoding="utf-8")
            self.assertEqual(R.catalogs_row(tmp)["verdict"], "FAIL", "a sixth catalog nobody listed")
            shutil.rmtree(os.path.join(tmp, "extra"))
            cursor = os.path.join(tmp, ".cursor-plugin", "marketplace.json")
            doc = _json(cursor)
            doc["plugins"].append(dict(doc["plugins"][0], name="someone-else"))
            Path(cursor).write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(R.catalogs_row(tmp)["verdict"], "FAIL", "a foreign name beside brother")
            shutil.copy(os.path.join(ROOT, ".cursor-plugin", "marketplace.json"), cursor)
            RC.apply(tmp, version)
            self.assertEqual(RC.pending(tmp), [])
            after = R.catalogs_row(tmp)
            self.assertEqual(after["verdict"], "PASS", after)
            manifest = os.path.join(tmp, "bundle", "MANIFEST.json")
            doc = _json(manifest)
            doc["shipped_plugins"] = ["brother", "brothermode"]
            Path(manifest).write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(R.catalogs_row(tmp)["verdict"], "FAIL", "a stale manifest after the cut is a FAIL")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_hooks_bar_counts_guarded_commands_against_the_products(self):
        """hooks-guarded, on a fixture tree: every bundle command wrapped and equal in number to the products' is
        PASS; one unwrapped command, a dropped command, a corrupt or absent bundle hooks file is FAIL; products that
        register nothing, or a products file that cannot be read, is NO-DATA, never PASS."""
        import one_plugin_readiness as R
        tmp = tempfile.mkdtemp(prefix="op1f-hooks-")
        try:
            def hooks(path, commands):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                Path(path).write_text(json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [
                    {"type": "command", "command": c} for c in commands]}]}}), encoding="utf-8")
            bm = os.path.join(tmp, "products", "brothermode", "hooks", "hooks.json")
            sbe = os.path.join(tmp, "products", "brothersbe", "hooks", "hooks.json")
            bundle = os.path.join(tmp, "bundle", "hooks", "hooks.json")
            hooks(bm, ["python3 a.py"])
            hooks(sbe, ["python3 b.py"])
            hooks(bundle, [self.GUARD + "brothermode Stop python3 a.py", self.GUARD + "brothersbe Stop python3 b.py"])
            self.assertEqual(R.hooks_row(tmp)["verdict"], "PASS", R.hooks_row(tmp))
            hooks(bundle, [self.GUARD + "brothermode Stop python3 a.py", "python3 b.py"])
            self.assertEqual(R.hooks_row(tmp)["verdict"], "FAIL", "one unwrapped command")
            hooks(bundle, [self.GUARD + "brothermode Stop python3 a.py"])
            self.assertEqual(R.hooks_row(tmp)["verdict"], "FAIL", "a dropped command")
            Path(bundle).write_text("{not json", encoding="utf-8")
            self.assertEqual(R.hooks_row(tmp)["verdict"], "FAIL", "a corrupt bundle hooks file")
            os.remove(bundle)
            self.assertEqual(R.hooks_row(tmp)["verdict"], "FAIL", "no bundle hooks file")
            hooks(bm, [])
            hooks(sbe, [])
            hooks(bundle, [])
            self.assertEqual(R.hooks_row(tmp)["verdict"], "NO-DATA", "nothing registered is nothing proven")
            hooks(bm, ["python3 a.py"])
            hooks(bundle, [self.GUARD + "brothermode Stop python3 a.py"])
            os.remove(sbe)
            self.assertEqual(R.hooks_row(tmp)["verdict"], "NO-DATA", "a missing products file leaves the count unknown")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_descriptions_bar_fails_on_a_retired_command_or_a_missing_description(self):
        """skill-descriptions, on a fixture tree: no skills is NO-DATA; a quoted one line and a block scalar
        description both PASS; a retired command in a description, an empty description and a missing name line
        are each FAIL."""
        import one_plugin_readiness as R
        tmp = tempfile.mkdtemp(prefix="op1f-desc-")
        try:
            def skill(name, front):
                d = os.path.join(tmp, "bundle", "skills", name)
                os.makedirs(d, exist_ok=True)
                Path(d, "SKILL.md").write_text("---\n" + front + "\n---\nbody\n", encoding="utf-8")
            self.assertEqual(R.descriptions_row(tmp)["verdict"], "NO-DATA", "no skills, nothing read")
            skill("brothermode-start", 'name: brothermode-start\ndescription: "Start a project"')
            skill("brothersbe-verify", "name: brothersbe-verify\ndescription: >\n  Use when work is about to be called done.")
            self.assertEqual(R.descriptions_row(tmp)["verdict"], "PASS", R.descriptions_row(tmp))
            skill("brothersbe-verify", 'name: brothersbe-verify\ndescription: "Invoke as /brothersbe:verify."')
            self.assertEqual(R.descriptions_row(tmp)["verdict"], "FAIL", "a retired command in a description")
            skill("brothersbe-verify", "name: brothersbe-verify\ndescription:")
            self.assertEqual(R.descriptions_row(tmp)["verdict"], "FAIL", "an empty description")
            skill("brothersbe-verify", "description: fine")
            self.assertEqual(R.descriptions_row(tmp)["verdict"], "FAIL", "no name line")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
