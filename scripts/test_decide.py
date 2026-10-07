"""What the decision screen must keep true.

The point of this file is that a recommendation engine is exactly the kind of
tool that can look right while being wrong. A page with a confident 8.4 on it
reads as arithmetic whether or not any arithmetic happened, and a code excerpt
reads as current whether or not the file still says that. So these tests drive
the four things the module claims and refuses to take any of them on trust.
"""
import html
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decide as D  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SPEC = {
    "title": "T",
    "criteria": [{"key": "a", "label": "A", "weight": 0.5},
                 {"key": "b", "label": "B", "weight": 0.5}],
    "options": [
        {"id": "x", "name": "X", "scores": {"a": 10, "b": 10}},
        {"id": "y", "name": "Y", "scores": {"a": 2, "b": 2}},
    ],
}


def clone(**over):
    s = json.loads(json.dumps(SPEC))
    s.update(over)
    return s


class TheScoreIsComputedNotTyped(unittest.TestCase):
    """A weighted total written by hand is an opinion wearing arithmetic's
    clothes. These prove the number moves when the inputs move."""

    def test_the_total_is_the_weighted_sum(self):
        _c, _n, scored, _close = D.rank(SPEC)
        self.assertAlmostEqual(scored[0]["total"], 10.0)
        self.assertAlmostEqual(scored[1]["total"], 2.0)

    def test_changing_one_mark_changes_the_total(self):
        s = clone()
        s["options"][0]["scores"]["a"] = 0
        _c, _n, scored, _close = D.rank(s)
        self.assertAlmostEqual(scored[0]["total"], 5.0)

    def test_changing_a_weight_changes_the_ranking(self):
        """The founder is invited to argue with a number rather than a verdict,
        so the verdict has to actually follow the numbers."""
        s = clone()
        s["options"][0]["scores"] = {"a": 10, "b": 0}
        s["options"][1]["scores"] = {"a": 0, "b": 10}
        self.assertEqual(D.rank(s)[2][0]["option"]["name"], "X")
        s["criteria"][1]["weight"] = 5.0          # B now dominates
        self.assertEqual(D.rank(s)[2][0]["option"]["name"], "Y")

    def test_weights_that_do_not_sum_to_one_are_normalised_and_SAID_SO(self):
        s = clone()
        s["criteria"] = [{"key": "a", "label": "A", "weight": 3},
                         {"key": "b", "label": "B", "weight": 1}]
        _c, note, scored, _close = D.rank(s)
        self.assertIn("4.00", note)
        self.assertAlmostEqual(scored[0]["total"], 10.0)

    def test_all_zero_weights_is_NO_DATA_rather_than_a_ranking(self):
        s = clone()
        for c in s["criteria"]:
            c["weight"] = 0
        note = D.rank(s)[1]
        self.assertIn(D.NODATA, note)


class AnUnmarkedCriterionIsNotAZero(unittest.TestCase):
    """Defaulting an unknown to zero, or to the middle, invents an opinion
    nobody held. It has to contribute nothing AND be named."""

    def test_it_contributes_nothing(self):
        s = clone()
        del s["options"][0]["scores"]["b"]
        self.assertAlmostEqual(D.rank(s)[2][0]["total"], 5.0)

    def test_it_is_named_rather_than_hidden(self):
        s = clone()
        del s["options"][0]["scores"]["b"]
        top = [x for x in D.rank(s)[2] if x["option"]["name"] == "X"][0]
        self.assertEqual(top["unmarked"], ["B"])

    def test_the_page_prints_the_unmarked_warning(self):
        s = clone()
        del s["options"][0]["scores"]["b"]
        self.assertIn("never marked on", D.render(s))


class ACloseCallIsNamedAsClose(unittest.TestCase):
    """Presenting 8.4 against 8.2 as a winner is how a recommendation engine
    starts lying politely."""

    def test_a_near_tie_is_flagged(self):
        s = clone()
        s["options"][1]["scores"] = {"a": 9.8, "b": 9.8}
        self.assertTrue(D.rank(s)[3])
        self.assertIn("does not separate the top two", D.render(s))

    def test_a_clear_winner_is_not_flagged(self):
        self.assertFalse(D.rank(SPEC)[3])
        self.assertNotIn("does not separate", D.render(SPEC))

    def test_the_page_never_claims_the_score_decides(self):
        page = D.render(SPEC)
        self.assertIn("The choice is yours", page)


class TheCodeIsReadFromTheLiveFile(unittest.TestCase):
    """A page promising look at the code while showing a stale copy is worse
    than one showing none."""

    def test_a_real_range_is_read_and_numbered(self):
        text, note = D.excerpt({"path": "scripts/decide.py", "lines": "1-3"})
        self.assertEqual(note, "")
        self.assertTrue(text.lstrip().startswith("1"))

    def test_a_missing_file_is_NO_DATA_not_silence(self):
        text, note = D.excerpt({"path": "scripts/nope_xyz.py", "lines": "1-3"})
        self.assertIsNone(text)
        self.assertIn(D.NODATA, note)

    def test_a_range_past_the_end_says_the_file_MOVED(self):
        """The rot case: the file is still there, the lines are not."""
        text, note = D.excerpt({"path": "scripts/decide.py", "lines": "999999"})
        self.assertIsNone(text)
        self.assertIn("moved under this page", note)

    def test_the_NO_DATA_reaches_the_rendered_page(self):
        s = clone()
        s["options"][0]["code"] = [{"path": "scripts/nope_xyz.py", "lines": "1"}]
        self.assertIn("is not present", D.render(s))


class ACodeAnchorCannotLeaveTheRepository(unittest.TestCase):
    """A code anchor comes from a model authored spec, and the rendered page is
    committed under docs/decisions/. An anchor that resolves outside the
    repository would copy that file (an .env, a key) into a tracked page, so
    each way out is refused on its own fixture: the file outside EXISTS in
    every one, so only the confinement can be what refuses it."""

    SECRET = "outside-the-repo-" + "S3cr3t" * 3

    @staticmethod
    def git(*args, cwd):
        """git with the hook's location variables dropped, so a run from a
        pre-push hook cannot aim this at the real repository."""
        import subprocess
        import tmp_sandbox
        env = dict(os.environ)
        tmp_sandbox.drop_git_location(env)
        return subprocess.run(("git",) + args, cwd=cwd, env=env,
                              capture_output=True, text=True)

    def setUp(self):
        import shutil
        self.root = tempfile.mkdtemp()
        self.away = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, self.away, True)
        made = self.git("init", "-q", cwd=self.root)
        self.assertEqual(made.returncode, 0, made.stderr)
        self.outside = os.path.join(self.away, "secret.env")
        with open(self.outside, "w", encoding="utf-8") as fh:
            fh.write(self.SECRET + "\n")
        with open(os.path.join(self.root, "inside.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("inside line one\ninside line two\n")
        real_root = os.path.realpath(self.root)
        self.assertFalse(os.path.realpath(self.outside).startswith(
            real_root + os.sep), "fixture broken: the outside file is inside")
        self.saved_root = D.ROOT
        D.ROOT = self.root
        self.addCleanup(setattr, D, "ROOT", self.saved_root)

    def assertRefused(self, path):
        text, note = D.excerpt({"path": path, "lines": "1"})
        self.assertIsNone(text, "read a file outside the repository")
        self.assertIn(D.NODATA, note)
        self.assertIn("outside this repository", note)
        self.assertNotIn(self.SECRET, note)

    def test_an_absolute_path_outside_is_refused(self):
        self.assertTrue(os.path.isabs(self.outside))
        self.assertRefused(self.outside)

    def test_a_dot_dot_path_outside_is_refused(self):
        rel = os.path.relpath(self.outside, self.root)
        self.assertTrue(rel.startswith(".."), rel)
        self.assertRefused(rel)

    def test_a_symlink_inside_pointing_outside_is_refused(self):
        os.symlink(self.outside, os.path.join(self.root, "link.env"))
        self.assertRefused("link.env")

    def test_an_ordinary_path_inside_is_still_read(self):
        text, note = D.excerpt({"path": "inside.py", "lines": "2"})
        self.assertEqual(note, "")
        self.assertIn("inside line two", text)

    def test_a_symlink_inside_pointing_inside_is_still_read(self):
        os.symlink(os.path.join(self.root, "inside.py"),
                   os.path.join(self.root, "alias.py"))
        text, note = D.excerpt({"path": "alias.py", "lines": "1"})
        self.assertEqual(note, "")
        self.assertIn("inside line one", text)

    def write_ignored_secret(self):
        with open(os.path.join(self.root, ".gitignore"), "w",
                  encoding="utf-8") as fh:
            fh.write("*.local\n")
        with open(os.path.join(self.root, "secret.local"), "w",
                  encoding="utf-8") as fh:
            fh.write(self.SECRET + "\n")

    def test_a_git_ignored_file_inside_is_refused(self):
        """Inside the repository, so only the ignore check can refuse it."""
        self.write_ignored_secret()
        text, note = D.excerpt({"path": "secret.local", "lines": "1"})
        self.assertIsNone(text, "read a file git ignores")
        self.assertIn(D.NODATA, note)
        self.assertIn("git ignores", note)
        self.assertNotIn(self.SECRET, note)

    def test_a_symlink_inside_pointing_at_an_ignored_file_is_refused(self):
        """The link itself is not ignored; what it resolves to is."""
        self.write_ignored_secret()
        os.symlink(os.path.join(self.root, "secret.local"),
                   os.path.join(self.root, "notes.txt"))
        text, note = D.excerpt({"path": "notes.txt", "lines": "1"})
        self.assertIsNone(text, "read an ignored file through a link")
        self.assertIn("git ignores", note)

    def test_a_path_into_dot_git_is_refused(self):
        """git check-ignore answers "not ignored" for .git/config (measured),
        so only the .git rule can refuse this one."""
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".git",
                                                    "config")))
        text, note = D.excerpt({"path": ".git/config", "lines": "1"})
        self.assertIsNone(text, "read git's own metadata")
        self.assertIn(D.NODATA, note)
        self.assertIn(".git", note)

    def test_a_hooks_GIT_DIR_does_not_redirect_the_ignore_check(self):
        """The pre-push gate runs suites inside a git hook, which exports
        GIT_DIR. Inherited, it would aim check-ignore at another repository."""
        saved = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = os.path.join(self.away, "no-such.git")
        self.addCleanup(lambda: os.environ.pop("GIT_DIR", None) if saved is None
                        else os.environ.__setitem__("GIT_DIR", saved))
        text, note = D.excerpt({"path": "inside.py", "lines": "1"})
        self.assertEqual(note, "")
        self.assertIn("inside line one", text)

    def test_when_git_cannot_answer_nothing_is_read(self):
        """Fail closed: a tree git cannot see into is not assumed clean."""
        import shutil
        plain = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plain, True)
        with open(os.path.join(plain, "inside.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("plain line one\n")
        probe = self.git("rev-parse", "--git-dir", cwd=plain)
        self.assertNotEqual(probe.returncode, 0,
                            "fixture broken: the plain folder is inside a repo")
        D.ROOT = plain
        text, note = D.excerpt({"path": "inside.py", "lines": "1"})
        self.assertIsNone(text, "read a file git could not vouch for")
        self.assertIn(D.NODATA, note)
        self.assertIn("could not ask git", note)

    def test_a_path_that_cannot_be_resolved_is_NO_DATA_not_a_crash(self):
        text, note = D.excerpt({"path": "inside\x00.py", "lines": "1"})
        self.assertIsNone(text)
        self.assertIn(D.NODATA, note)

    def test_the_rendered_page_carries_the_refusal_not_the_file(self):
        """The entry point: render() builds the committed page."""
        D.ROOT = self.saved_root
        s = clone()
        s["options"][0]["code"] = [{"path": self.outside, "lines": "1"}]
        page = D.render(s)
        self.assertNotIn(self.SECRET, page)
        self.assertIn("outside this repository", page)


class ThePageIsSafeAndComplete(unittest.TestCase):
    def test_content_is_escaped(self):
        s = clone(title="<script>alert(1)</script>")
        page = D.render(s)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_both_themes_are_defined(self):
        page = D.render(SPEC)
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn('[data-theme="dark"]', page)

    def test_the_flow_is_kept_as_source_and_says_so(self):
        """E48, run 5 critic 1, section 5, 2026-09-03: the page used to fetch
        mermaid from a CDN so a plain browser could draw the flow. The flow
        source stays, as text a reader can render elsewhere, and the page
        says that is what it is looking at."""
        s = clone()
        s["options"][0]["flow_mermaid"] = "flowchart LR\n  A --> B"
        page = D.render(s)
        self.assertIn('<pre class="mermaid">', page)
        self.assertIn("flowchart LR", page)
        self.assertIn("flow source shown as text; render it with any "
                      "mermaid viewer", page)

    def test_the_page_fetches_nothing_when_it_is_opened(self):
        """The whole of E48, driven at the page: no script anywhere, so no
        src to point off the machine, and no http of any kind in the markup
        the generator wrote. A decision page opens with the network down."""
        s = clone()
        s["options"][0]["flow_mermaid"] = "flowchart LR\n  A --> B"
        page = D.render(s)
        # No script at all, so no script src; and no src of any kind, which
        # is what an image, a frame or a font would fetch. A link's href is
        # not a fetch: nothing is requested until a reader clicks it.
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("cdn", page.lower())
        self.assertNotIn("src=", page.lower())

    def test_a_web_source_renders_its_link(self):
        """SOURCE_KEYS accepts a web citation {title, url, what}, so the page
        must show where it points: until 2026-09-26 the url and title were
        dropped without a word. No title falls back to the url text."""
        s = clone()
        s["options"][0]["sources"] = [
            {"title": "Install guide", "what": "how it installs",
             "url": "https://example.com/install?a=1&b=2"},
            {"what": "no title", "url": "https://example.com/bare"}]
        page = D.render(s)
        self.assertIn('<a href="https://example.com/install?a=1&amp;b=2" '
                      'rel="noreferrer">Install guide</a>', page)
        self.assertIn('<a href="https://example.com/bare" rel="noreferrer">'
                      'https://example.com/bare</a>', page)

    def test_a_url_that_is_not_http_is_never_an_href(self):
        """E() stops a quote leaving href, not a javascript: or data: scheme,
        which runs when the reader clicks. repos, docs and sources all go
        through one guard, so all three are driven here, and one https link
        per list proves the guard does not simply refuse everything."""
        hostile = ["javascript:alert(1)", " JavaScript:alert(1)",
                   "data:text/html,<b>x</b>", "vbscript:x"]
        good = "https://example.com/ok"
        s = clone()
        o = s["options"][0]
        o["sources"] = [{"what": "w", "title": "src%d" % i, "url": u}
                        for i, u in enumerate(hostile + [good])]
        o["repos"] = [{"name": "repo%d" % i, "url": u}
                      for i, u in enumerate(hostile + [good])]
        o["docs"] = [{"title": "doc%d" % i, "url": u}
                     for i, u in enumerate(hostile + [good])]
        page = D.render(s)
        for h in re.findall(r'href="([^"]*)"', page):
            self.assertTrue(html.unescape(h).startswith(("#", "https://", "http://")),
                            "a clickable non http(s) href: %r" % h)
        self.assertEqual(page.count('href="%s"' % good), 3)
        for i in range(len(hostile)):
            for name in ("src", "repo", "doc"):
                self.assertIn("%s%d" % (name, i), page)

    def test_a_source_shows_the_date_it_was_checked(self):
        """SOURCE_KEYS accepts checked, the audit stamp on a citation, so the
        page must show it: until 2026-09-26 it was dropped without a word on
        all 20 option sources in docs/decisions that carry one."""
        s = clone()
        s["options"][0]["sources"] = [
            {"what": "the inventory", "where": "file",
             "found_in": "docs/plan/X.md", "checked": "2026-08-31<b>"}]
        page = D.render(s)
        self.assertIn('<span class="checked">checked 2026-08-31&lt;b&gt;</span>',
                      page)

    def test_an_unreadable_spec_is_NO_DATA_not_a_crash(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{ not json")
            path = fh.name
        try:
            self.assertEqual(D.main([path]), 2)
        finally:
            os.unlink(path)


class TheRealDecisionStillResolves(unittest.TestCase):
    """The regression that matters. Every code anchor in the shipped decision is
    a line number in a file somebody will edit, so this fails the day the page
    starts showing the wrong code, rather than the day somebody notices."""

    #: EVERY shipped decision, not one named file. Generalised the moment a
    #: second decision existed, because a guard that covers only the first
    #: decision silently stops guarding the moment the capability is used
    #: again, which is exactly when it starts to matter.
    DECISIONS = os.path.join(ROOT, "docs", "decisions")

    def specs(self):
        """Every decide.py-shaped spec under docs/decisions/, not every JSON
        file there. That directory also holds outcome contracts, model/lane
        decisions, and architecture rulings (their own schemas, none of them
        decide.py's) -- an "options" key is the one thing every real
        decide.py spec has and none of the others do, checked against the
        whole directory before this filter was added: 5 non-decide.py files,
        every one of them missing "options", every decide.py file carrying
        it. Without this, a new architecture record in that directory fails
        THIS module's own tests for a shape it was never in.

        THE KEY ALONE STOPPED BEING ENOUGH: jev-competitive-edge-2026-09-19.json,
        a WBS-linked competitive-edge ruling with its own unrelated "options"
        list of plain id strings, tripped this filter the day it landed and
        failed every downstream test written for a real decide.py option
        object. Checked against the whole directory when this line was added:
        every one of the 83 real decide.py specs has "options" as a list of
        DICTS; this is the only file where it is a list of strings. That is
        the added distinguishing signal, not just the key's presence."""
        found = []
        for name in sorted(os.listdir(self.DECISIONS)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(self.DECISIONS, name), encoding="utf-8") as fh:
                spec = json.load(fh)
            opts = spec.get("options")
            if not isinstance(opts, list) or not opts:
                continue
            if not all(isinstance(o, dict) for o in opts):
                continue
            found.append((name, spec))
        return found

    def load(self):
        """The first spec, for the tests that only need one."""
        return self.specs()[0][1]

    def test_at_least_one_shipped_spec_exists_and_all_parse(self):
        found = self.specs()
        self.assertTrue(found, "no decision spec is shipped at all")
        for name, spec in found:
            self.assertTrue(spec.get("options"), name)

    def test_every_code_anchor_still_resolves(self):
        broken = []
        for name, spec in self.specs():
          for opt in spec["options"]:
            for c in opt.get("code") or []:
                text, note = D.excerpt(c)
                if text is None:
                    broken.append("%s: %s" % (opt["name"], note))
        self.assertEqual(broken, [], "code anchors have rotted: %s" % broken)

    #: A source can honestly live OUTSIDE this repository. A decision scored
    #: against the founder's own machine level files (his hooks, his global
    #: instructions, his spend guard) cites them, and copying those files into
    #: the tree is forbidden by the privacy rules, so the citation is the only
    #: honest form. Such a source declares scope "machine" and is skipped for
    #: existence. This test NEVER expands ~ and never reads the machine it runs
    #: on: a verdict that depends on which machine ran it is a recorded failure
    #: class here, and it would read green on the founder's laptop while every
    #: clone read red.
    MACHINE_SCOPE = "machine"

    #: A source can also honestly cite nothing that was ever a file: a
    #: session's own report to the founder, a command's typed argument text,
    #: "this repo, tonight's own work". Inventing a path for these would be
    #: fabrication (there is no file to name), and refusing to cite them at
    #: all would throw away the evidence. Such a source declares scope
    #: "narrative" and is exempt from file-existence checking, exactly like
    #: "machine" is -- but the flag is not a mute button here either: a
    #: narrative source must not ALSO look like a file path (a "/" segment
    #: ending in a short extension, e.g. "docs/plan/journal.jsonl"), because
    #: that shape means the citation was probably meant as a real file and
    #: the author mistyped or forgot the path, which "narrative" must never
    #: paper over. Checked in both directions, same as machine scope.
    NARRATIVE_SCOPE = "narrative"
    _LOOKS_LIKE_A_FILE = re.compile(r"/[^\s/]+\.[A-Za-z0-9]{1,6}\b")

    #: The only keys a source may carry. decide.py renders what (the bold
    #: label), where and found_in; this test reads scope; checked is an audit
    #: stamp the page shows as "checked <date>"; receipt is read by
    #: scripts/receipt_check.py, whose default
    #: record shape is this one. Any other key is dropped by the page without
    #: a word, and a file named under it is never existence checked:
    #: mutation M5b (2026-09-26) put {"label", "path": <missing file>} in a
    #: record, this test stayed
    #: green, and the page rendered an empty label. A closed set, not a list
    #: of file-looking names, because "path" was one spelling of the defect
    #: and the next one will be another. title and url are a web citation: a
    #: URL is not a file, so it is exempt from existence the way machine scope
    #: is, but it must still carry what, it is counted, and
    #: test_every_outside_link_was_actually_checked holds it to https and a
    #: checked date like every other outside link.
    SOURCE_KEYS = frozenset({"what", "where", "found_in", "scope", "checked",
                             "receipt", "title", "url"})

    def test_every_source_names_a_file_that_exists(self):
        """The flag is not a mute button, so it is checked in both directions: a
        machine level source must name a path that is outside this tree by
        construction, an outside path carrying no flag still fails, and a
        narrative source must not be shaped like a file citation in disguise.
        Before any of that, a source must be in the shape decide.py reads: no
        key outside SOURCE_KEYS, and a what, the label the page shows. Every
        offender is COLLECTED and reported together, never truncated at the
        first one: a source guard that stops at the first offender hid every
        sibling behind it."""
        missing = []
        machine = []
        narrative = []
        web = []
        unread = []
        unlabeled = []
        machine_misscoped = []
        unflagged_outside = []
        narrative_misscoped = []
        narrative_file_shaped = []
        for name, spec in self.specs():
          for opt in spec["options"]:
            for s in opt.get("sources") or []:
                at = "%s option %s" % (name, opt.get("id"))
                if not isinstance(s, dict):
                    unread.append("%s: a source that is not an object: %r" % (at, s))
                    continue
                extra = sorted(set(s) - self.SOURCE_KEYS)
                if extra:
                    unread.append("%s: %s outside SOURCE_KEYS in %r"
                                  % (at, extra, s))
                if not s.get("what"):
                    unlabeled.append("%s: %r" % (at, s))
                if "url" in s:
                    web.append(str(s["url"]))
                p = s.get("found_in")
                if not p:
                    continue
                outside = p.startswith("~") or os.path.isabs(p)
                scope = s.get("scope")
                if scope == self.MACHINE_SCOPE:
                    if not outside:
                        machine_misscoped.append(
                            "%s: %r is declared machine level but is a repo relative "
                            "path, so it must exist in this tree" % (opt["name"], p))
                        continue
                    machine.append(p)
                    continue
                if scope == self.NARRATIVE_SCOPE:
                    if outside:
                        narrative_misscoped.append(
                            "%s: %r is declared narrative but is shaped like a "
                            "machine path, so it should declare scope %r instead"
                            % (opt["name"], p, self.MACHINE_SCOPE))
                        continue
                    if self._LOOKS_LIKE_A_FILE.search(p):
                        narrative_file_shaped.append(
                            "%s: %r is declared narrative but is shaped like a file "
                            "citation, so it must be a real repo-relative path (or "
                            "scope %r) instead of scope %r"
                            % (opt["name"], p, self.MACHINE_SCOPE, self.NARRATIVE_SCOPE))
                        continue
                    narrative.append(p)
                    continue
                if outside:
                    unflagged_outside.append(
                        "%s: %r points outside this tree, so it must declare scope "
                        "%r; this test never expands ~ and never reads the machine "
                        "it runs on" % (opt["name"], p, self.MACHINE_SCOPE))
                    continue
                if not os.path.isfile(os.path.join(ROOT, p)):
                    missing.append(p)
        if machine:
            print("%s: %d source(s) are machine level, so their existence was "
                  "not checked: %s" % (D.NODATA, len(machine), ", ".join(machine)))
        if narrative:
            print("%s: %d source(s) are narrative (no file ever existed), so "
                  "their existence was not checked: %s"
                  % (D.NODATA, len(narrative), ", ".join(narrative)))
        if web:
            print("%s: %d source(s) are web pages, not files, so their "
                  "existence was not checked: %s"
                  % (D.NODATA, len(web), ", ".join(web)))
        self.assertEqual(unread, [],
                          "sources carry keys the page never shows, so a file "
                          "named there is never checked: %s" % unread)
        self.assertEqual(unlabeled, [],
                          "sources with no what render as an empty label: %s"
                          % unlabeled)
        self.assertEqual(machine_misscoped, [],
                          "machine scoped sources name a repo relative path: %s"
                          % machine_misscoped)
        self.assertEqual(unflagged_outside, [],
                          "sources point outside this tree without declaring "
                          "machine scope: %s" % unflagged_outside)
        self.assertEqual(narrative_misscoped, [],
                          "narrative scoped sources are shaped like a machine "
                          "path: %s" % narrative_misscoped)
        self.assertEqual(narrative_file_shaped, [],
                          "narrative scoped sources are shaped like a file "
                          "citation: %s" % narrative_file_shaped)
        self.assertEqual(missing, [], "sources cite missing files: %s" % missing)

    def assert_unscored_record(self, name, spec):
        """A prose decision records costs and evidence without inventing
        numeric marks. Missing criteria alone must never exempt a broken
        scored screen from its coverage checks."""
        for key in ("id", "created", "scope", "title", "status", "description",
                    "evidence", "flip_condition", "owned_by", "recommendation"):
            self.assertTrue(spec.get(key), "%s: record lacks %s" % (name, key))
        self.assertIsInstance(spec["evidence"], dict, name)
        self.assertNotIn("criteria", spec, name)
        self.assertTrue(spec.get("options"), name)
        for opt in spec["options"]:
            for key in ("id", "label", "pros", "cons", "cost_of_doing",
                        "cost_of_not_doing"):
                self.assertTrue(opt.get(key), "%s: option lacks %s" % (name, key))
            self.assertNotIn("scores", opt, name)
            self.assertNotIn("score_basis", opt, name)

    def test_missing_criteria_cannot_exempt_a_scored_screen(self):
        broken = clone()
        del broken["criteria"]
        with self.assertRaises(AssertionError):
            self.assert_unscored_record("broken-screen", broken)

    def test_unscored_record_cannot_hide_numeric_marks(self):
        for name, spec in self.specs():
            if "criteria" in spec:
                continue
            self.assert_unscored_record(name, spec)
            mixed = json.loads(json.dumps(spec))
            mixed["options"][0]["scores"] = {"unsupported": 10}
            with self.assertRaises(AssertionError):
                self.assert_unscored_record(name, mixed)

    def test_every_option_is_marked_on_every_criterion(self):
        """Not required by the module, required of a SHIPPED decision: an
        unmarked criterion would mean a real option was quietly under scored."""
        for name, spec in self.specs():
            if "criteria" not in spec:
                self.assert_unscored_record(name, spec)
                continue
            keys = {c["key"] for c in spec["criteria"]}
            for opt in spec["options"]:
                self.assertEqual(set(opt.get("scores") or {}), keys,
                                 "%s: %s" % (name, opt["name"]))

    def test_every_outside_link_was_actually_checked(self):
        """The guard against a plausible URL pasted from memory. A link the
        founder clicks that 404s is worse than no link, so every one carries the
        date it was resolved and this fails if any does not. A web citation in
        sources is an outside link too, held to the same rule."""
        unchecked = []
        for name, spec in self.specs():
          for opt in spec["options"]:
            web = [s for s in opt.get("sources") or []
                   if isinstance(s, dict) and "url" in s]
            for r in (opt.get("repos") or []) + (opt.get("docs") or []) + web:
                if not r.get("checked"):
                    unchecked.append(r.get("url", "?"))
                if not str(r.get("url", "")).startswith("https://"):
                    unchecked.append("not https: %s" % r.get("url"))
        self.assertEqual(unchecked, [], "links not verified: %s" % unchecked)

    def test_the_chosen_option_is_one_that_exists(self):
        """A decision record naming an option that is not on the page would
        render a banner with nothing marked, which reads as no decision.
        A "+"-joined choice ("D+B") bundles two real options into one
        founder decision; every part must still be a real option id, or the
        same nothing-marked failure happens for a part of it."""
        spec = self.load()
        for name, spec in self.specs():
            if "decided" not in spec:
                continue
            decided = spec["decided"]
            # A bare boolean reads as a verdict to a person and as nothing to
            # the renderer: true crashed decide.py, false slipped past this
            # check, and three records shipped that way (2026-09-21/22). No
            # decision is recorded by leaving the key out, never by false.
            self.assertIsInstance(decided, dict, name)
            ids = {o.get("id") for o in spec["options"]}
            choice = decided.get("choice") or ""
            for part in choice.split("+"):
                self.assertIn(part, ids, name)

    def test_every_option_carries_pros_cons_a_diagram_and_a_source(self):
        for name, spec in self.specs():
            if "criteria" not in spec:
                self.assert_unscored_record(name, spec)
                continue
            for opt in spec["options"]:
                self.assertTrue(opt.get("pros"), opt["name"])
                self.assertTrue(opt.get("cons"), opt["name"])
                self.assertTrue(opt.get("flow_mermaid"), opt["name"])
                self.assertTrue(opt.get("sources"), opt["name"])
                self.assertTrue(opt.get("score_basis"), opt["name"])


class TheStampLandsWhereTheGateReadsIt(unittest.TestCase):
    """The sentinel is half of a two part control, and the halves drifted.

    The intake gate refuses a founder facing question unless a screen was
    rendered BY THAT SESSION, and since its 2026-09-15 per session fix it
    reads ~/.claude/decision-screens/<session id>.json. decide.py never
    followed: it kept stamping the old single global file, which nothing
    reads any more. The visible result on 2026-09-18 was a session that
    rendered two screens, published both, and was still refused, which is
    the state that teaches people to reach for the escape hatch. These
    tests drive the path the gate actually opens, not the path the writer
    happens to like.
    """

    @staticmethod
    def stamp_env(home):
        """The caller's environment with HOME moved to the test's own and
        every variable that can move the stamp cleared: brother_paths reads
        BROTHER_CONFIG_DIR, then CLAUDE_CONFIG_DIR, then CODEX_HOME before
        HOME, so a suite run from a session that sets one of them would
        otherwise write that session's live stamp."""
        env = dict(os.environ)
        env["HOME"] = home
        for name in ("BROTHER_DECISION_SENTINEL", "BROTHER_CONFIG_DIR",
                     "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            env.pop(name, None)
        return env

    def run_decide(self, home, session_id):
        import subprocess
        tmp = tempfile.mkdtemp()
        spec_path = os.path.join(tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(clone(title="Gate contract"), fh)
        env = self.stamp_env(home)
        if session_id is None:
            env.pop("CLAUDE_CODE_SESSION_ID", None)
        else:
            env["CLAUDE_CODE_SESSION_ID"] = session_id
        out = os.path.join(tmp, "screen.html")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "decide.py"),
             spec_path, "-o", out],
            env=env, capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        return out

    def test_the_stamp_is_written_per_session_where_the_gate_looks(self):
        home = tempfile.mkdtemp()
        session = "42fbcd3b-c243-4dc2-b7b6-5fa615c68ee3"
        out = self.run_decide(home, session)
        expected = os.path.join(home, ".claude", "decision-screens",
                                "%s.json" % session)
        self.assertTrue(
            os.path.exists(expected),
            "the intake gate reads %s and nothing else; rendering a screen "
            "must stamp exactly that path, or every session that follows "
            "the ceremony is still refused. Wrote instead: %r"
            % (expected, sorted(os.listdir(os.path.join(home, ".claude")))
               if os.path.isdir(os.path.join(home, ".claude")) else "nothing"))
        with open(expected, encoding="utf-8") as fh:
            stamp = json.load(fh)
        self.assertEqual(os.path.abspath(out), stamp.get("path"))
        self.assertEqual("Gate contract", stamp.get("title"))
        self.assertGreater(float(stamp.get("written_at_epoch", 0)), 0)
        self.assertIsNone(stamp.get("used_at_epoch"),
                          "a fresh stamp is unconsumed; the gate is what "
                          "marks it used")

    def test_a_session_id_with_separators_cannot_escape_the_state_dir(self):
        home = tempfile.mkdtemp()
        self.run_decide(home, "../../etc/evil id")
        state = os.path.join(home, ".claude", "decision-screens")
        self.assertTrue(os.path.isdir(state))
        names = os.listdir(state)
        self.assertEqual(1, len(names), names)
        self.assertNotIn("/", names[0])
        self.assertFalse(os.path.exists(os.path.join(home, "etc")))

    def test_no_session_id_still_stamps_under_the_gates_own_default_name(self):
        home = tempfile.mkdtemp()
        out = self.run_decide(home, None)
        self.assertTrue(os.path.exists(out))
        expected = os.path.join(home, ".claude", "decision-screens",
                                "no-session.json")
        self.assertTrue(
            os.path.exists(expected),
            "the gate names an absent session 'no-session', so the writer "
            "uses that same name rather than dropping the stamp")

    def test_the_test_override_still_wins_so_a_suite_never_stamps_the_real_gate(self):
        import subprocess
        home = tempfile.mkdtemp()
        tmp = tempfile.mkdtemp()
        spec_path = os.path.join(tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(clone(), fh)
        sentinel = os.path.join(tmp, "sentinel.json")
        env = self.stamp_env(home)
        env["BROTHER_DECISION_SENTINEL"] = sentinel
        env["CLAUDE_CODE_SESSION_ID"] = "s1"
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "decide.py"),
             spec_path, "-o", os.path.join(tmp, "o.html")],
            env=env, capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(sentinel))
        self.assertFalse(
            os.path.exists(os.path.join(home, ".claude", "decision-screens",
                                        "s1.json")),
            "with the override set, a suite must not write the real per "
            "session stamp")

    def test_an_explicit_session_id_flag_names_the_stamp_over_the_environment(self):
        import subprocess
        home = tempfile.mkdtemp()
        tmp = tempfile.mkdtemp()
        spec_path = os.path.join(tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(clone(), fh)
        env = self.stamp_env(home)
        env["CLAUDE_CODE_SESSION_ID"] = "from-env"
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "decide.py"),
             spec_path, "-o", os.path.join(tmp, "o.html"),
             "--session-id", "from/flag"],
            env=env, capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        state = os.path.join(home, ".claude", "decision-screens")
        self.assertEqual(["from_flag.json"], sorted(os.listdir(state)),
                         "--session-id names the stamp, sanitised by the "
                         "gate's own rule, and the environment id is not "
                         "also written")


class ASecretInTheSpecIsRedactedBeforeAnyWrite(unittest.TestCase):
    """The spec is written by a model session, and the screen lands under
    docs/decisions/, inside the tree git commits. The title also goes to the
    intake sentinel and the top option's name to stdout. So every string in
    the spec passes through bm_store.redact_text once, right after it is
    loaded, and all three sinks receive the redacted text. Built by
    concatenation so no scanner reads a live-looking key here."""

    SECRET = "gh" + "p_" + "A1b2" * 5

    def run_decide(self, spec, script_dir=None):
        import subprocess
        tmp = tempfile.mkdtemp()
        spec_path = os.path.join(tmp, "spec.json")
        with open(spec_path, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        env = dict(os.environ)
        env["BROTHER_DECISION_SENTINEL"] = os.path.join(tmp, "sentinel.json")
        out = os.path.join(tmp, "screen.html")
        r = subprocess.run(
            [sys.executable,
             os.path.join(script_dir or os.path.join(ROOT, "scripts"),
                          "decide.py"),
             spec_path, "-o", out],
            env=env, capture_output=True, text=True)
        return r, out, env["BROTHER_DECISION_SENTINEL"]

    def secret_spec(self):
        s = clone(title="Rotate %s now" % self.SECRET)
        s["options"][0]["name"] = "Keep %s" % self.SECRET
        return s

    def test_the_secret_reaches_neither_the_screen_nor_the_sentinel(self):
        r, out, sentinel = self.run_decide(self.secret_spec())
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        with open(out, encoding="utf-8") as fh:
            page = fh.read()
        self.assertNotIn(self.SECRET, page)
        self.assertIn("Rotate [REDACTED] now", page)
        self.assertIn("Keep [REDACTED]", page)
        with open(sentinel, encoding="utf-8") as fh:
            stamp = json.load(fh)
        self.assertNotIn(self.SECRET, stamp.get("title", ""))
        self.assertEqual("Rotate [REDACTED] now", stamp.get("title"))
        self.assertNotIn(self.SECRET, r.stdout + r.stderr)

    def test_a_secret_shaped_criterion_key_still_matches_its_marks(self):
        """Keys are redacted with the values, so a criterion key and the
        `scores` entry naming it are rewritten alike and the option stays
        marked on it. Redacting only the value would leave the mark
        unmatched and quietly drop the criterion from every total."""
        s = clone()
        s["criteria"][0]["key"] = self.SECRET
        for opt in s["options"]:
            opt["scores"][self.SECRET] = opt["scores"].pop("a")
        r, out, _ = self.run_decide(s)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        with open(out, encoding="utf-8") as fh:
            page = fh.read()
        self.assertNotIn(self.SECRET, page)
        self.assertNotIn("was never marked on", page)
        self.assertIn("top is X at 10.00", r.stdout)

    def test_no_redactor_means_no_screen_and_no_stamp(self):
        """A copy of decide.py with neither bm_store.py candidate beside it
        cannot redact, so it refuses: nothing is written, the exit is
        nonzero, and the refusal says NO-DATA. Rendering raw would be the
        unsafe direction."""
        import shutil
        lone = tempfile.mkdtemp()
        for name in ("decide.py", "brother_paths.py", "annotations_store.py",
                     "brother_state.py", "tmp_sandbox.py"):
            shutil.copy(os.path.join(ROOT, "scripts", name), lone)
        r, out, sentinel = self.run_decide(self.secret_spec(), script_dir=lone)
        self.assertNotEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn(D.NODATA, r.stderr)
        self.assertFalse(os.path.exists(out))
        self.assertFalse(os.path.exists(sentinel))
        self.assertNotIn(self.SECRET, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
