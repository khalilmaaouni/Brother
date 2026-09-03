"""The booklet's own tests.

Every claim in `design/field-book/07-verification.md` that names a test names
one of these. The pattern throughout: each renderer is checked against an
INDEPENDENT reading of its source, not against itself. A test that asks the
renderer what it rendered proves the renderer is consistent with itself, which
is the tautology this project spends its time refusing elsewhere.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from brothersbe import book as book_mod  # noqa: E402

#: The two dash characters, written here once so the sweep below cannot be
#: defeated by somebody editing the sweep to look for something else.
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)


class TestCommandsRenderer(unittest.TestCase):
    def test_commands_renderer_recovers_every_cli_command(self):
        """Independent reading: the CLI's own table, asked directly."""
        from brothersbe import cli as cli_mod
        expected = set(name for name, _, _ in cli_mod.COMMANDS)
        section = book_mod.render_cli_commands(ROOT)
        rendered = set(re.findall(r"<code>sbe ([a-z-]+)</code>", section.html))
        self.assertEqual(expected, rendered,
                         "the rendered command table and brothersbe.cli.COMMANDS disagree")
        self.assertEqual(section.verdict, "PASS")
        self.assertEqual(section.item_count, len(expected))

    def test_the_book_subcommand_is_registered_and_passthrough(self):
        from brothersbe import cli as cli_mod
        self.assertIn("book", set(name for name, _, _ in cli_mod.COMMANDS))
        self.assertIn("book", cli_mod.PASSTHROUGH,
                      "sbe book owns its own parsing, so it must dispatch by hand")

    def test_guided_renderer_names_every_skill_directory(self):
        """Independent reading: the filesystem, not the renderer's own count."""
        skills = os.path.join(ROOT, "skills")
        expected = set(n for n in os.listdir(skills)
                       if os.path.isfile(os.path.join(skills, n, "SKILL.md")))
        section = book_mod.render_guided_commands(ROOT)
        rendered = set(re.findall(r"<code>/brothersbe:([a-z-]+)</code>", section.html))
        self.assertEqual(expected, rendered)


class TestRolesRenderer(unittest.TestCase):
    def test_roles_renderer_names_every_agent_file(self):
        expected = set(n[:-3] for n in os.listdir(os.path.join(ROOT, "agents"))
                       if n.endswith(".md"))
        section = book_mod.render_roles(ROOT)
        rendered = set(re.findall(r"<tr><th><code>([a-z-]+)</code></th>", section.html))
        self.assertEqual(expected, rendered)

    def test_every_reviewer_role_is_read_only_and_only_the_worker_writes(self):
        """The claim act five makes in prose, asserted against the frontmatter.

        This is the test that would have caught the defect the first build
        exposed: the prose said every role was read-only while
        `implementation-worker` shipped Edit and Write.
        """
        agents = os.path.join(ROOT, "agents")
        writers = []
        for name in sorted(os.listdir(agents)):
            if not name.endswith(".md"):
                continue
            text = io.open(os.path.join(agents, name), encoding="utf-8").read()
            tools = ""
            for line in text.split("\n"):
                if line.startswith("tools:"):
                    tools = line
                    break
            if "Write" in tools or "Edit" in tools:
                writers.append(name[:-3])
        self.assertEqual(writers, ["implementation-worker"],
                         "act five states exactly one role may write; the agent "
                         "definitions now say otherwise, so the booklet is wrong")


class TestChecksRenderer(unittest.TestCase):
    def test_checks_renderer_reports_declared_severity(self):
        section = book_mod.render_checks(ROOT)
        self.assertEqual(section.verdict, "PASS")
        severities = re.findall(r'<span class="stamp s-\w+">(gate|soft|\(not declared\))'
                                r"</span>", section.html)
        self.assertTrue(severities, "no severity column was rendered")
        self.assertNotIn("(not declared)", severities,
                         "a check reached the booklet without a declared severity")

    def test_no_check_row_claims_pass_as_its_empty_state(self):
        """NO-DATA is never a pass, asserted over what the booklet prints."""
        section = book_mod.render_checks(ROOT)
        rows = re.findall(r"<tr><th><code>[^<]+</code></th>.*?</tr>", section.html)
        self.assertTrue(rows)
        for row in rows:
            cells = re.findall(r"<td>(.*?)</td>", row)
            self.assertNotIn("PASS", cells[-1],
                             "a check declares PASS as its empty state: %s" % row)

    def test_no_source_path_is_rendered_twice_over(self):
        """The `tools/tools/...` defect an earlier build exposed, pinned."""
        self.assertNotIn("tools/tools/", book_mod.render_checks(ROOT).html)


class TestLimitsRenderer(unittest.TestCase):
    def test_limits_renderer_names_every_heading(self):
        text = io.open(os.path.join(ROOT, "docs", "KNOWN-LIMITS.md"),
                       encoding="utf-8").read()
        expected = len([l for l in text.split("\n") if l.startswith("## ")])
        section = book_mod.render_limits(ROOT)
        self.assertEqual(section.item_count, expected)
        self.assertGreater(expected, 0)


class TestProvenance(unittest.TestCase):
    def test_provenance_accounts_for_every_declared_renderer(self):
        sections = [renderer(ROOT) for _, renderer in book_mod.RENDERERS]
        prov = book_mod.render_provenance(ROOT, sections)
        self.assertEqual(prov.item_count, len(book_mod.RENDERERS))
        for name, _ in book_mod.RENDERERS:
            self.assertIn("<code>%s</code>" % name, prov.html)

    def test_provenance_binds_to_version_so_a_release_forces_a_rebuild(self):
        sections = [renderer(ROOT) for _, renderer in book_mod.RENDERERS]
        prov = book_mod.render_provenance(ROOT, sections)
        self.assertEqual([path for path, _ in prov.sources], ["VERSION"])


class TestDriftCheck(unittest.TestCase):
    """These build their own tree, so nothing here depends on the state of the
    working copy and nothing here writes into it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-booklet-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tree = os.path.join(self.tmp, "repo")
        for rel in ("VERSION", "DIGEST.md", "docs/KNOWN-LIMITS.md"):
            dest = os.path.join(self.tree, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(os.path.join(ROOT, rel), dest)
        for rel in ("src/brothersbe", "agents", "skills", "tools",
                    "docs/fieldbook"):
            shutil.copytree(os.path.join(ROOT, rel), os.path.join(self.tree, rel),
                            ignore=shutil.ignore_patterns("__pycache__"))
        self.version = io.open(os.path.join(self.tree, "VERSION"),
                               encoding="utf-8").read().strip()

    def _run(self, *argv):
        """Run in a SUBPROCESS whose package resolves to the copied tree.

        Not a convenience. Registry discovery mounts `tools/` from wherever
        `brothersbe` was imported, so building in this process would document
        THIS repository's checks while writing them into the copy, and the
        renderer now refuses exactly that. A fresh interpreter with the copy on
        its path makes the tree self-consistent, which is the only honest way to
        test a generator whose whole job is deriving from its own repository.
        """
        env = dict(os.environ, PYTHONPATH=os.path.join(self.tree, "src"))
        env.pop("BROTHERSBE_REGISTRIES", None)
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; from brothersbe import book; sys.exit(book.main(sys.argv[1:]))",
             self.tree] + list(argv),
            capture_output=True, text=True, env=env, cwd=self.tree, timeout=300)
        return out.returncode, out.stdout + out.stderr

    def _build(self):
        code, output = self._run()
        self.assertEqual(code, 0, output)

    def test_check_fails_when_a_bound_source_moves(self):
        self._build()
        agent = os.path.join(self.tree, "agents", "qa-reviewer.md")
        io.open(agent, "a", encoding="utf-8").write("\nA later edit.\n")
        results = book_mod.check(self.tree, self.version)
        fails = [(name, sentence) for verdict, name, sentence in results
                 if verdict == "FAIL"]
        self.assertTrue(fails, "a moved bound source must FAIL, not pass quietly")
        self.assertTrue(any(name == "roles" for name, _ in fails))
        self.assertTrue(any("agents/qa-reviewer.md" in s for _, s in fails),
                        "the FAIL must name the file that moved: %s" % fails)

    def test_a_deleted_bound_source_fails_naming_it(self):
        self._build()
        os.remove(os.path.join(self.tree, "agents", "qa-reviewer.md"))
        fails = [s for v, _, s in book_mod.check(self.tree, self.version) if v == "FAIL"]
        self.assertTrue(any("no longer exist" in s for s in fails), fails)

    def test_a_stale_cover_version_is_no_data_and_never_fail(self):
        self._build()
        cover = os.path.join(self.tree, book_mod.PARTS_REL, "01-cover.html")
        text = io.open(cover, encoding="utf-8").read()
        io.open(cover, "w", encoding="utf-8").write(
            text.replace("<b>Describes</b> v%s" % self.version,
                         "<b>Describes</b> v0.0.1-ancient"))
        verdicts = [(v, s) for v, n, s in book_mod.check(self.tree, self.version)
                    if n == "cover-version"]
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0][0], "NO-DATA",
                         "prose staleness is reported, never enforced")

    def test_an_unparseable_bindings_file_is_fail_not_no_data(self):
        self._build()
        io.open(os.path.join(self.tree, book_mod.BINDINGS_REL), "w",
                encoding="utf-8").write("{not json")
        self.assertEqual(book_mod.check(self.tree, self.version)[0][0], "FAIL",
                         "a broken claim is not an absent one")

    def test_a_missing_bindings_file_is_no_data_not_fail(self):
        self._build()
        os.remove(os.path.join(self.tree, book_mod.BINDINGS_REL))
        self.assertEqual(book_mod.check(self.tree, self.version)[0][0], "NO-DATA")

    def test_strict_exits_nonzero_on_fail_and_zero_on_no_data(self):
        self._build()
        cover = os.path.join(self.tree, book_mod.PARTS_REL, "01-cover.html")
        text = io.open(cover, encoding="utf-8").read()
        io.open(cover, "w", encoding="utf-8").write(
            text.replace("<b>Describes</b> v%s" % self.version,
                         "<b>Describes</b> v0.0.1-ancient"))
        code, output = self._run("--check", "--strict")
        self.assertEqual(code, 0, "NO-DATA must never decide the exit code: %s" % output)
        io.open(os.path.join(self.tree, "agents", "qa-reviewer.md"), "a",
                encoding="utf-8").write("\ndrift\n")
        code, output = self._run("--check", "--strict")
        self.assertEqual(code, 1, output)

    def test_two_builds_produce_byte_identical_output(self):
        self._build()
        html = os.path.join(self.tree, book_mod.BOOK_REL, book_mod.HTML_NAME)
        first_html = io.open(html, encoding="utf-8").read()
        first_bind = io.open(os.path.join(self.tree, book_mod.BINDINGS_REL),
                             encoding="utf-8").read()
        self._build()
        self.assertEqual(first_html, io.open(html, encoding="utf-8").read(),
                         "the build is not deterministic")
        self.assertEqual(first_bind,
                         io.open(os.path.join(self.tree, book_mod.BINDINGS_REL),
                                 encoding="utf-8").read())

    def test_author_prose_survives_a_rebuild(self):
        self._build()
        part = os.path.join(self.tree, book_mod.PARTS_REL, "50-act-five.html")
        sentinel = "\n<p>A sentinel the builder must never touch.</p>\n"
        io.open(part, "a", encoding="utf-8").write(sentinel)
        self._build()
        self.assertIn(sentinel, io.open(part, encoding="utf-8").read())

    def test_a_section_with_no_home_fails_rather_than_rendering_nowhere(self):
        part = os.path.join(self.tree, book_mod.PARTS_REL, "50-act-five.html")
        text = io.open(part, encoding="utf-8").read()
        io.open(part, "w", encoding="utf-8").write(
            text.replace(book_mod.BEGIN_FMT % "roles", "").replace(
                book_mod.END_FMT % "roles", ""))
        code, output = self._run()
        self.assertEqual(code, 1, output)
        self.assertIn("nowhere to", output)
        self.assertIn("roles", output)


class TestEmittedHtml(unittest.TestCase):
    def setUp(self):
        self.html = io.open(os.path.join(ROOT, book_mod.BOOK_REL, book_mod.HTML_NAME),
                            encoding="utf-8").read()

    def test_html_requests_no_external_host(self):
        for attr in ("src=", "href="):
            for match in re.findall(attr + r'"([^"]+)"', self.html):
                self.assertFalse(match.startswith(("http://", "https://", "//")),
                                 "the page must open offline; %s reaches a host" % match)

    def test_every_act_reaches_the_page(self):
        for anchor in ("pain", "sprint", "seats", "fit", "how"):
            self.assertIn('id="%s"' % anchor, self.html)

    def test_every_contents_link_resolves_to_an_anchor_on_the_page(self):
        """A table of contents whose links go nowhere is worse than none."""
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        broken = [href for href in re.findall(r'href="#([^"]+)"', self.html)
                  if href not in ids]
        self.assertEqual(broken, [])

    def test_the_page_names_the_sources_every_generated_table_came_from(self):
        self.assertIn("src/brothersbe/cli.py", self.html)
        self.assertIn("docs/KNOWN-LIMITS.md", self.html)

    def test_the_fragment_and_the_page_carry_the_same_booklet(self):
        fragment = io.open(os.path.join(ROOT, book_mod.BOOK_REL,
                                        book_mod.FRAGMENT_NAME), encoding="utf-8").read()
        self.assertNotIn("<!doctype", fragment.lower())
        for anchor in ("pain", "sprint", "seats", "fit", "how"):
            self.assertIn('id="%s"' % anchor, fragment)

    def test_both_themes_are_defined_and_the_toggle_can_win(self):
        for token in ("prefers-color-scheme: dark", ':root[data-theme="dark"]',
                      ':root[data-theme="light"]'):
            self.assertIn(token, self.html,
                          "the viewer's theme toggle must override the media query")


class TestProseHygiene(unittest.TestCase):
    def test_no_em_or_en_dash_in_any_part(self):
        offenders = []
        for path, text in book_mod.read_parts(ROOT):
            if EM_DASH in text or EN_DASH in text:
                offenders.append(os.path.basename(path))
        self.assertEqual(offenders, [])

    def test_every_svg_figure_carries_a_title_for_a_screen_reader(self):
        untitled = []
        for path, text in book_mod.read_parts(ROOT):
            for svg in re.findall(r"<svg\b.*?</svg>", text, re.DOTALL):
                if "<title" not in svg:
                    untitled.append(os.path.basename(path))
        self.assertEqual(untitled, [],
                         "every figure must be describable without seeing it")

    def test_the_bindings_file_records_every_declared_section(self):
        bindings = json.loads(io.open(os.path.join(ROOT, book_mod.BINDINGS_REL),
                                      encoding="utf-8").read())
        expected = sorted([n for n, _ in book_mod.RENDERERS] + [book_mod.PROVENANCE])
        self.assertEqual(sorted(bindings), expected)


class TestCliSurface(unittest.TestCase):
    def test_sbe_book_check_runs_from_the_command_line(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "sbe"),
                              "book", "--check"],
                             capture_output=True, text=True, cwd=ROOT, timeout=180)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("BOOKLET DRIFT CHECK", out.stdout)


if __name__ == "__main__":
    unittest.main()
