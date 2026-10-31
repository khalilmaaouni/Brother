#!/usr/bin/env python3
"""The shipped surface: the door plus six generated verb skills, one moved
command per 1.1.0 name, and every 1.1.0 name routed (release 1.1.1, U1).

Until 1.1.1 this suite pinned 48 generated product aliases plus two mirrored
Cursor skills under bundle/skills. The owner withdrew C3 and C4 on
2026-10-10 (one plugin only; the 1.1.0 names are no longer frozen), and the
orchestrator chose option B on 2026-10-11 (a supported entry point is not
broken without the owner's explicit exception). The contract pinned here:
seven skills on disk, six of them generated from codex_surface.VERBS; one
door command plus 48 moved commands, one per RETIRED name and nothing else;
a generated table routing all 48; the two Cursor bodies shipped verbatim as
references; no retired name shipping as a skill directory."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_surface  # noqa: E402

VERBS = ("start", "status", "next", "review", "deliver", "help")
BUNDLE = ROOT / "bundle"
#: The stop command the start body must teach: the bundle's bm_controller, the stop subcommand, and the
#: four flags a live run needs, --session-id among them, in this order.
STOP_COMMAND_RE = (r'python3 "\$\{BROTHER_PLUGIN_ROOT\}/runtime/hooks/brothermode/tools/bm_controller\.py" stop '
                   r'--project <[^>]+> --controller-id <[^>]+> --session-id <[^>]+> --actor-name <[^>]+>')


class CodexSurface(unittest.TestCase):
    def test_generated_files_are_current(self):
        problems = codex_surface.check(ROOT)
        self.assertEqual(problems, [], "run python3 scripts/codex_surface.py: " + "; ".join(problems))

    def test_the_bundle_ships_exactly_seven_skills(self):
        skills = sorted(p.parent.name for p in (BUNDLE / "skills").glob("*/SKILL.md"))
        self.assertEqual(skills, sorted(["using-brother"] + ["brother-%s" % v for v in VERBS]))

    def test_every_verb_skill_is_generated_with_codex_frontmatter(self):
        expected = codex_surface.expected(ROOT)
        self.assertEqual(sorted(k for k in expected if k.endswith("SKILL.md")),
                         sorted("skills/brother-%s/SKILL.md" % v for v in VERBS))
        for verb in VERBS:
            text = (BUNDLE / "skills" / ("brother-%s" % verb) / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\nname: brother-%s\n" % verb))
            self.assertNotIn("disable-model-invocation", text)
            self.assertIn(codex_surface.MARKER, text)
            self.assertIn("## 1.1.0 names that route here", text)

    def test_every_retired_name_routes_to_a_verb_skill_in_the_table(self):
        self.assertEqual(len(codex_surface.RETIRED), 48)
        table = (BUNDLE / codex_surface.RETIRED_TABLE).read_text(encoding="utf-8")
        for name, verb in codex_surface.RETIRED:
            self.assertIn(verb, VERBS, name)
            self.assertIn("| %s | `/brother %s` (`brother-%s`) |" % (name, codex_surface.retired_route(name), verb),
                          table, name)
            self.assertIn("- `%s`:" % name,
                          (BUNDLE / "skills" / ("brother-%s" % verb) / "SKILL.md").read_text(encoding="utf-8"))
            self.assertFalse((BUNDLE / "skills" / name / "SKILL.md").exists(),
                             "%s still ships as a skill" % name)

    def test_no_moved_command_ships(self):
        """DOOR1.a: the door shows six verbs and nothing else.

        Measured on the 1.1.7 install and again on this tree before the change: 49 files in
        bundle/commands, 48 of them Moved stubs, one per retired 1.1.0 name, and the retired names
        were typed 0 times in 60 days. The owner approved removing them (AC4, 2026-10-23); the
        charter's C4 surface freeze is his call and he made it.

        RETIRED itself stays, because the routing table and the verb skill footers read it, and an
        old name typed AS AN ARGUMENT must still route. That is
        test_every_retired_name_routes_to_a_verb_skill_in_the_table's job, not this one's.
        """
        commands = sorted(p.name[:-3] for p in (BUNDLE / "commands").glob("*.md"))
        self.assertEqual(["brother"], commands,
                         "bundle/commands ships %d file(s) and only the door may ship: %s"
                         % (len(commands), " ".join(n for n in commands if n != "brother")))
        retired = sorted(name for name, _verb in codex_surface.RETIRED)
        self.assertEqual(len(retired), len(set(retired)), "a name is listed twice")
        self.assertTrue(retired, "RETIRED is empty, so the routing table has nothing to route")
        self.assertNotIn(codex_surface.MARKER,
                         (BUNDLE / "commands" / "brother.md").read_text(encoding="utf-8"),
                         "the door carries the generated-stub marker, so it looks like a stub")

    def _copy_tree(self):
        tmp = Path(tempfile.mkdtemp(prefix="codex-surface-"))
        shutil.copytree(BUNDLE / "skills", tmp / "bundle" / "skills")
        shutil.copytree(BUNDLE / "commands", tmp / "bundle" / "commands")
        for product in ("brothermode", "brothersbe"):
            shutil.copytree(ROOT / "products" / product / "skills", tmp / "products" / product / "skills")
        return tmp

    def test_a_retired_name_still_shipping_as_a_skill_is_drift(self):
        tmp = self._copy_tree()
        try:
            self.assertEqual(codex_surface.check(tmp), [])
            stale = tmp / "bundle" / "skills" / "brothermode-start"
            stale.mkdir()
            (stale / "SKILL.md").write_text("---\nname: brothermode-start\ndescription: x\n---\n", encoding="utf-8")
            problems = codex_surface.check(tmp)
            self.assertTrue(any("brothermode-start" in p and "1.1.0 name" in p for p in problems), problems)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_deleted_or_foreign_moved_command_is_drift(self):
        tmp = self._copy_tree()
        try:
            # A GENERATED subject, which bundle/commands no longer has: DOOR1.a removed every
            # generated command, and commands/brother.md is HAND WRITTEN (codex_surface.py says so
            # where it explains that the door carries no marker), so deleting the door was never
            # reported as drift and never could be. The verb skills are generated, so one of those
            # is what a deletion drifts now.
            verb_skill = tmp / "bundle" / "skills" / "brother-start" / "SKILL.md"
            verb_skill.unlink()
            problems = codex_surface.check(tmp)
            self.assertEqual(problems, ["bundle/skills/brother-start/SKILL.md: missing"])
            codex_surface.generate(tmp)
            self.assertEqual(codex_surface.check(tmp), [])
            (tmp / "bundle" / "commands" / "brothermode-nothing.md").write_text(
                "---\ndescription: x\n---\n%s\n" % codex_surface.MARKER, encoding="utf-8")
            self.assertEqual(codex_surface.check(tmp), ["bundle/commands/brothermode-nothing.md: stale generated file"])
            codex_surface.generate(tmp)
            self.assertFalse((tmp / "bundle" / "commands" / "brothermode-nothing.md").exists())
            self.assertTrue((tmp / "bundle" / "commands" / "brother.md").is_file(), "the door is never swept")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _verb_body(verb):
        text = (BUNDLE / "skills" / ("brother-%s" % verb) / "SKILL.md").read_text(encoding="utf-8")
        return text.split("## 1.1.0 names that route here", 1)[0]

    def test_each_real_content_reference_is_named_by_its_verb_body_and_its_moved_command(self):
        """Review B1 (2026-10-11): brothermode-cursor-dispatch routed to start, whose body never named
        references/cursor-dispatch.md, so the capability was reachable only through the retired list. The
        verb a real content name routes to must teach the reference in its own body, and the moved command
        for that name must name it too."""
        for stem, (product, skill_dir) in codex_surface.REAL_CONTENT_REFERENCES.items():
            name = "%s-%s" % (product, skill_dir)
            verb = codex_surface.retired_verb(name)
            self.assertIn("references/%s.md" % stem, self._verb_body(verb), "%s does not teach %s" % (verb, stem))
            # The moved-command half of this case went with DOOR1.a: no stub ships, so the verb body
            # is now the ONLY place that can teach the reference, which makes this assertion carry
            # more weight than it did, not less.

    def test_the_stop_names_teach_stopping(self):
        """Review B2 (2026-10-11): brotherme-stop and brothermode-stop pointed at `/brother start`, which
        resumes work. The pointer is `/brother start stop`, and the start body names the real stop command
        and what stopping does."""
        table = (BUNDLE / codex_surface.RETIRED_TABLE).read_text(encoding="utf-8")
        for name in ("brotherme-stop", "brothermode-stop"):
            self.assertEqual(codex_surface.retired_route(name), "start stop")
            self.assertIn("| %s | `/brother start stop` (`brother-start`) |" % name, table)
            # The stub assertion that stood here went with DOOR1.a: no moved command ships, so the
            # table row above and the start body below are where this property now lives, and both
            # are still checked.
        body = self._verb_body("start")
        self.assertIn("`/brother start stop`", body)
        self.assertRegex(body, r"releas\w+ every (held )?claim")
        # Round 2 (2026-10-11): the taught command must be the one that stops a LIVE run. A stop
        # without --session-id speaks as a fresh session and is refused ("a run has one driver").
        self.assertRegex(body, STOP_COMMAND_RE, "the start body does not teach the full stop command")
        self.assertIn("status --project <project id> --json --raw", body, "where the driver session id is read")
        self.assertIn("`run.session_id`", body)
        self.assertRegex(body, r"adopt --project <project id> --session-id <your session id> --actor-name <your name>")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}` under Claude Code", body, "the Claude Code mapping for bm_controller")

    def test_the_taught_stop_command_stops_a_live_run(self):
        """Round 2 (2026-10-11), executed, not prose: in a throwaway project the bundle's own
        bm_controller starts a run as driver session S1; the stop command exactly as the start body
        teaches it (placeholders filled, the plugin root the bundle) moves that run NEW to STOPPED; and
        the driver's session id is readable where the body says, `status --json --raw`, run.session_id."""
        body = self._verb_body("start")
        taught = re.search(STOP_COMMAND_RE, body)
        self.assertIsNotNone(taught, "no taught stop command in the start body")
        tools = BUNDLE / "runtime" / "hooks" / "brothermode" / "tools"
        root = Path(tempfile.mkdtemp(prefix="u1-stop-"))
        try:
            env = dict(os.environ)
            for key in ("BROTHERMODE_ROOT", "BROTHERMODE_VAULT", "HOME", "USERPROFILE"):
                env.pop(key, None)
            (root / "home").mkdir()
            env.update(BROTHERMODE_ROOT=str(root), BROTHERMODE_VAULT=str(root / "vault"),
                       HOME=str(root / "home"), USERPROFILE=str(root / "home"), BROTHER_PLUGIN_ROOT=str(BUNDLE))

            def run(tool, args):
                proc = subprocess.run([sys.executable, "-B", str(tools / tool)] + args, cwd=str(root),
                                      capture_output=True, text=True, env=env)
                return proc.returncode, proc.stdout + proc.stderr

            actor = ["--actor-name", "tester", "--session-id", "S1"]
            passes = '%s -c pass' % sys.executable
            for tool, args in (("bm_store.py", ["init"]),
                               ("bm_project.py", ["start", "--project-id", "p1", "--name", "Test Project"] + actor),
                               ("bm_autonomy.py", ["sign", "--project", "p1", "--outcome", "ship it",
                                                   "--done-definition", passes, "--signed-by", "Khalil Maaouni",
                                                   "--allowed-path", ".", "--risk-class", "file-edit"] + actor),
                               ("bm_controller.py", ["start", "--project", "p1", "--outcome", "ship it",
                                                     "--done-definition", passes, "--controller-id", "c1"] + actor)):
                code, out = run(tool, args)
                self.assertEqual(code, 0, "%s %s: %s" % (tool, args[0], out))
            code, out = run("bm_controller.py", ["status", "--project", "p1", "--json", "--raw"])
            self.assertEqual(code, 0, out)
            self.assertEqual(json.loads(out)["run"]["session_id"], "S1", "the documented place does not show the driver")
            # The taught command, verbatim from the skill, with its placeholders filled by the flag they follow.
            command = taught.group(0).replace("${BROTHER_PLUGIN_ROOT}", str(BUNDLE))
            tokens = shlex.split(re.sub(r"<[^>]+>", "PLACEHOLDER", command))
            fill = {"--project": "p1", "--controller-id": "c1", "--session-id": "S1", "--actor-name": "tester"}
            argv = []
            for index, token in enumerate(tokens):
                if token == "PLACEHOLDER":
                    flag = tokens[index - 1]
                    self.assertIn(flag, fill, "a placeholder follows an unknown flag %r" % flag)
                    token = fill[flag]
                argv.append(token)
            self.assertEqual(argv[:2], ["python3", str(tools / "bm_controller.py")], argv)
            proc = subprocess.run([sys.executable, "-B"] + argv[1:], cwd=str(root), capture_output=True, text=True, env=env)
            out = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, "the taught stop command was refused: " + out)
            self.assertIn("NEW -> STOPPED", out, out)
            code, status = run("bm_controller.py", ["status", "--project", "p1", "--json", "--raw"])
            self.assertEqual(json.loads(status)["run"]["state"], "STOPPED", status)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cursor_dispatch_replaces_the_engine_run(self):
        """Round 2 (2026-10-11): brothermode-cursor-dispatch routes as `/brother start dispatch`, and the
        start body says dispatching writes the Cursor packet and does not run brother_run.py."""
        self.assertEqual(codex_surface.retired_route("brothermode-cursor-dispatch"), "start dispatch")
        table = (BUNDLE / codex_surface.RETIRED_TABLE).read_text(encoding="utf-8")
        self.assertIn("| brothermode-cursor-dispatch | `/brother start dispatch` (`brother-start`) |", table)
        body = self._verb_body("start")
        self.assertIn("`/brother start dispatch`", body)
        self.assertRegex(body, r"`/brother start dispatch` writes a packet for Cursor[^`]*`[^`]*cursor-dispatch\.md`;"
                               r" it REPLACES the engine run for that work and does not run brother_run\.py\.")
        # The stub assertion that stood here went with DOOR1.a. The route survives in the table row
        # and the start body, both asserted above, and the dispatch instructions still ship as
        # references/cursor-dispatch.md, which test_real_content_references_mirror_the_product_skill
        # covers.

    def test_the_verb_skills_name_only_the_hosts_without_slash_commands(self):
        """Review note 6: Cursor loads ./commands, so only Codex and Antigravity lack slash commands."""
        for verb in VERBS:
            body = self._verb_body(verb)
            self.assertIn("Codex and Antigravity have no slash command surface", body, verb)
            self.assertNotRegex(body, r"Cursor[^.]*no slash command", verb)

    def test_the_weaker_routes_name_their_tools(self):
        """Review note 7: a verb that absorbed a 1.1.0 skill names the tool or the act that skill ran."""
        self.assertIn("bm_view.py", self._verb_body("status"))
        self.assertIn("bm_handover.py", self._verb_body("deliver"))
        self.assertIn("brothermode_cli.py", self._verb_body("help"))
        self.assertIn("doctor", self._verb_body("help"))
        self.assertIn("design", self._verb_body("review"))
        self.assertIn("specification", self._verb_body("start"))

    def test_the_routing_table_does_not_promise_a_moved_command(self):
        """The table must not offer a route that no longer ships.

        REVIEW NOTE 9 IS RETIRED HERE, WITH ITS REASON. Its requirement was that a MOVED COMMAND
        name the verb skill from the plugin root with the vendor neutral variable beside it. DOOR1.a
        removes every moved command, so that requirement's subject no longer exists. The first
        attempt at this case re-pointed it at the retired-names table, and the table does not name
        the plugin root and never did: keeping a test alive by inventing a requirement for a file
        that never carried it is its own dishonesty, so it was dropped instead. The surviving
        routing guarantee is test_every_retired_name_routes_to_a_verb_skill_in_the_table's, and it
        is untouched.

        WHAT THIS CASE IS INSTEAD, found by that failure. The table's own prose told the reader a
        retired name works when "typed as it was under Claude Code (`/brother:<old name>`, a moved
        command that prints the pointer and routes)". With no moved command shipping, that sentence
        is a claim the product does not honour, which is exactly what this release exists to stop.
        """
        table = (BUNDLE / "skills" / "using-brother" / "references" / "retired-names.md").read_text(
            encoding="utf-8")
        self.assertNotIn("a moved command that prints the pointer and routes", table,
                         "the routing table still promises a moved command, and none ships")
        self.assertNotIn("/brother:", table,
                         "the routing table still offers the `/brother:<old name>` form, which no "
                         "longer resolves to anything")
        #: The route that DOES survive must still be offered, or the table removed a promise and
        #: replaced it with nothing.
        self.assertIn("as an argument to the door", table,
                      "the table no longer tells the reader the one route that still works")

    def test_an_unexpected_skill_is_drift(self):
        """Review note 3: --check must catch any skill directory outside the seven, marked or not."""
        tmp = self._copy_tree()
        try:
            extra = tmp / "bundle" / "skills" / "extra"
            extra.mkdir()
            (extra / "SKILL.md").write_text("---\nname: extra\ndescription: x\n---\nbody\n", encoding="utf-8")
            self.assertEqual(codex_surface.check(tmp),
                             ["bundle/skills/extra/SKILL.md: not one of the seven shipped skills"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_real_content_references_mirror_the_product_skill(self):
        """The two 1.1.0 Cursor skills carried the real mailbox harness
        instructions; they ship verbatim as references beside the door."""
        self.assertEqual(set(codex_surface.REAL_CONTENT_REFERENCES), {"cursor-execute", "cursor-dispatch"})
        for stem, (product, skill_dir) in codex_surface.REAL_CONTENT_REFERENCES.items():
            text = (BUNDLE / codex_surface.REFERENCES / ("%s.md" % stem)).read_text(encoding="utf-8")
            self.assertNotIn(codex_surface.MARKER, text)
            self.assertNotIn("brother_run.py", text)
            self.assertIn("--project", text)
            self.assertIn("claim-next" if skill_dir == "cursor-execute" else "dispatch", text)
            source_text = (ROOT / "products" / product / "skills" / skill_dir / "SKILL.md").read_text(encoding="utf-8")
            _, source_body = codex_surface.split_frontmatter(source_text)
            self.assertIn(source_body, text)

    def test_the_tables_refuse_a_bad_entry(self):
        saved = codex_surface.RETIRED
        try:
            codex_surface.RETIRED = saved + (("brothermode-start", "start"),)
            with self.assertRaises(ValueError):
                codex_surface.expected(ROOT)
            codex_surface.RETIRED = saved + (("brothermode-nothing", "verify"),)
            with self.assertRaises(ValueError):
                codex_surface.expected(ROOT)
        finally:
            codex_surface.RETIRED = saved

    def test_bundle_manifest_points_at_the_visible_skill_directory(self):
        for host in (".codex-plugin", ".cursor-plugin"):
            manifest = json.loads((BUNDLE / host / "plugin.json").read_text())
            self.assertIn("./skills/", json.dumps(manifest.get("skills")), host)
        self.assertTrue((BUNDLE / "skills/brother-review/SKILL.md").is_file())
        self.assertTrue((BUNDLE / "skills/using-brother/SKILL.md").is_file())

    def test_runtime_mirror_is_current(self):
        proc = subprocess.run(
            [sys.executable, "scripts/codex_skills.py", "--check"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
