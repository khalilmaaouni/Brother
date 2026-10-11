#!/usr/bin/env python3
"""N24 (docs/plan/specs/N24.md): no shipped page that describes the present
promises work to the version being cut, or calls an older version the
current release.

Measured on the 1.1.1 tree: three shipped sentences did ("a repair is
planned for 1.1.1", "its certification is a 1.1.1 item", "1.1.0, the
current release"), and no check read them, because the version rule only
reads strings that repeat a plugin's version.

RuleTest drives the rule on fixture roots; PreflightRowTest drives the
preflight row through test_cut_preflight's own fixture; LiveTreeTest reads
this tree's own pages for the declared version and every released one.

Exit 0 when every case passes, 1 when one fails, 2 (NO-DATA) when the live
tree's declared version cannot be read: that is never a pass, and never a
unittest skip that exits 0 (plan v5 ruling).

Python 3.9, standard library only. No network. No em or en dashes.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import cut_preflight as P  # noqa: E402

#: Reasons the live tree could not be judged; main() turns any into exit 2.
NO_DATA = []


def versions_to_check(root):
    """The declared version plus every x.y.z with a docs/releases/<x.y.z>.md note, each once, sorted by version."""
    found = set()
    declared = P.declared_version(root)
    if declared:
        found.add(declared)
    notes = os.path.join(root, "docs", "releases")
    if os.path.isdir(notes):
        for name in os.listdir(notes):
            stem = name[:-3] if name.endswith(".md") else ""
            if P.version_tuple(stem):
                found.add(stem)
    return sorted(found, key=P.version_tuple)


def live_problems(root):
    """(no_data_reason, refusals) for every version versions_to_check names: '' and [] when every page is clean."""
    if P.declared_version(root) is None:
        return ("the declared version cannot be read from %s" % P.DECLARED_VERSION_REL), []
    refused, unknown = [], []
    for version in versions_to_check(root):
        verdict, _name, detail = P.check_no_promise(root, version)
        if verdict == P.REFUSED:
            refused.append("%s: %s" % (version, detail))
        elif verdict != P.OK:
            unknown.append("%s: %s" % (version, detail))
    return "; ".join(unknown), refused


def make(test, files):
    root = tempfile.mkdtemp(prefix="test-no-promise-")
    test.addCleanup(shutil.rmtree, root, True)
    for rel, text in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if isinstance(text, bytes) else "w") as fh:
            fh.write(text)
    return root


class RuleTest(unittest.TestCase):

    def verdict(self, text, version="1.1.2", rel="README.md"):
        return P.check_no_promise(make(self, {rel: text}), version)

    def test_a_planned_promise_refuses_naming_path_and_line(self):
        verdict, name, detail = self.verdict("# Title\n\na repair is planned for 1.1.2.\n")
        self.assertEqual((verdict, name), (P.REFUSED, "no-promise"))
        self.assertIn("README.md:3", detail)
        self.assertIn("planned for 1.1.2", detail)
        self.assertEqual(self.verdict("Planned  For 1.1.2\n")[0], P.REFUSED)

    def test_text_that_names_the_version_as_shipped_is_ok(self):
        for text in ("## What changed in 1.1.2\n", "Since 1.1.2, Brother reads it.\n", "1.1.2 ships the fix.\n",
                     "The fix for 1.1.2 shipped.\n"):
            with self.subTest(text=text):
                self.assertEqual(self.verdict(text)[0], P.OK)

    def test_a_promise_to_another_version_is_not_this_cuts(self):
        self.assertEqual(self.verdict("planned for 1.1.3\n")[0], P.OK)
        self.assertEqual(self.verdict("planned for 1.1.1\n")[0], P.OK)

    def test_an_item_promise_refuses_and_a_negation_does_not(self):
        self.assertEqual(self.verdict("its certification is a 1.1.2 item.\n")[0], P.REFUSED)
        self.assertEqual(self.verdict("these are 1.1.2 items.\n")[0], P.REFUSED)
        self.assertEqual(self.verdict("this is not a 1.1.2 item.\n")[0], P.OK)

    def test_a_promise_split_over_two_lines_is_found_at_its_first_line(self):
        verdict, _n, detail = self.verdict("intro\n\na repair is planned\nfor 1.1.2.\n")
        self.assertEqual(verdict, P.REFUSED)
        self.assertIn("README.md:3", detail)

    def test_the_version_inside_a_longer_number_is_not_it(self):
        for text in ("planned for 1.1.10\n", "planned for 11.1.1\n", "planned for 1.1.1.1\n"):
            with self.subTest(text=text):
                self.assertEqual(self.verdict(text, version="1.1.1")[0], P.OK)

    def test_versions_to_check_reads_the_notes_and_the_declared_version(self):
        root = make(self, {"docs/releases/1.1.0.md": "x", "docs/releases/1.1.1.md": "x",
                           "docs/releases/README.md": "x",
                           ".claude-plugin/marketplace.json": json.dumps({"metadata": {"version": "1.1.2"}})})
        self.assertEqual(versions_to_check(root), ["1.1.0", "1.1.1", "1.1.2"])

    def test_an_older_version_called_the_current_release_refuses(self):
        self.assertEqual(self.verdict("This uses 1.1.0, the current release.\n", "1.1.1")[0], P.REFUSED)
        self.assertEqual(self.verdict("This uses 1.1.0, the current release.\n", "1.1.0")[0], P.OK)
        self.assertEqual(self.verdict("This uses 1.1.2, the current release.\n", "1.1.1")[0], P.OK)

    def test_only_the_pages_that_describe_the_present_are_read(self):
        promise = "planned for 1.1.2\n"
        base = {"README.md": "# clean\n"}
        for rel in ("docs/plan/X.md", "docs/releases/1.1.1.md", "products/p/docs/X.md"):
            with self.subTest(ignored=rel):
                self.assertEqual(P.check_no_promise(make(self, dict(base, **{rel: promise})), "1.1.2")[0], P.OK)
        for rel in ("docs/how-to/install.md", "AGENTS.md"):
            with self.subTest(read=rel):
                self.assertEqual(P.check_no_promise(make(self, dict(base, **{rel: promise})), "1.1.2")[0],
                                 P.REFUSED)

    def test_every_doubt_is_no_data_never_ok(self):
        root = make(self, {"README.md": "# clean\n"})
        for bad in ("1.1", "", None, "1.1.2-rc", "1.1.2 "):
            with self.subTest(version=bad):
                self.assertEqual(P.check_no_promise(root, bad)[0], P.NODATA)
        self.assertEqual(P.check_no_promise(make(self, {"docs/plan/X.md": "x"}), "1.1.2")[0], P.NODATA)
        verdict, _n, detail = P.check_no_promise(make(self, {"README.md": b"\xff\xfe planned"}), "1.1.2")
        self.assertEqual(verdict, P.NODATA)
        self.assertIn("README.md", detail)
        verdict, _n, detail = P.check_no_promise(
            make(self, {"README.md": b"\xff\xfe", "AGENTS.md": "planned for 1.1.2\n"}), "1.1.2")
        self.assertEqual(verdict, P.REFUSED)
        self.assertIn("AGENTS.md", detail)
        self.assertIn("README.md", detail)
        with self.assertRaises(ValueError):
            P.check_no_promise("", "1.1.2")


class PreflightRowTest(unittest.TestCase):

    def run_all(self, docs=None):
        # imported here: it reads this tree's own catalog at import, and the live class must reach its NO-DATA
        import test_cut_preflight as TP
        tp = TP.CheapestFirst("test_all_green_is_exit_zero_and_the_virgin_gate_ran")
        self.addCleanup(tp.doCleanups)
        return tp._all({}, version="1.0.21", docs=docs)

    def test_the_row_sits_in_the_cheap_tier_and_a_hit_spares_the_expensive_ones(self):
        res, built = self.run_all(docs={"README.md": "a fix is planned for 1.0.21\n"})
        names = [n for _v, n, _d in res]
        self.assertLess(names.index("release-plan"), names.index("no-promise"))
        self.assertLess(names.index("no-promise"), names.index("changelog-text"))
        self.assertEqual(P.exit_code(res), P.EXIT_REFUSED, res)
        self.assertEqual(built, [])

    def test_the_clean_default_page_is_ok(self):
        res, built = self.run_all()
        rows = dict((n, v) for v, n, _d in res)
        self.assertEqual(rows["no-promise"], P.OK)
        self.assertEqual(P.exit_code(res), P.EXIT_OK, res)
        self.assertEqual(len(built), 2)


class LiveTreeTest(unittest.TestCase):

    def test_no_current_page_promises_work_to_a_released_or_declared_version(self):
        why, refused = live_problems(ROOT)
        if why:
            NO_DATA.append(why)
        self.assertEqual(refused, [])

    def test_the_two_old_sentences_are_gone_and_the_limit_is_still_named(self):
        """Pins what must not come back, not the wording that replaced it (documentation is drafted outside the
        builder and may be redrafted): the README still names the packed-refs lock limit."""
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        with open(os.path.join(ROOT, "docs", "VERSIONING.md"), encoding="utf-8") as fh:
            versioning = fh.read()
        self.assertNotIn("its certification is a 1.1.1 item", versioning)
        self.assertNotIn("a repair is planned for 1.1.1", readme)
        self.assertNotIn("the refusal message does not name the lock file", readme)
        self.assertIn("packed-refs.lock", readme)

    def test_a_promise_to_a_released_version_is_caught_when_the_declared_one_is_newer(self):
        root = make(self, {".claude-plugin/marketplace.json": json.dumps({"metadata": {"version": "1.1.2"}}),
                           "docs/releases/1.1.1.md": "x", "README.md": "a fix is planned for 1.1.1\n"})
        why, refused = live_problems(root)
        self.assertEqual(why, "")
        self.assertEqual(len(refused), 1)
        self.assertTrue(refused[0].startswith("1.1.1: "), refused)

    def test_an_unreadable_declared_version_is_no_data_never_clean(self):
        for name, text in (("missing", None), ("not json", "{"), ("no metadata", "{}"),
                           ("not x.y.z", json.dumps({"metadata": {"version": "1.1"}}))):
            with self.subTest(market=name):
                files = {"README.md": "# clean\n"}
                if text is not None:
                    files[".claude-plugin/marketplace.json"] = text
                why, refused = live_problems(make(self, files))
                self.assertTrue(why.startswith("the declared version cannot be read"), why)
                self.assertEqual(refused, [])

    def test_an_unreadable_declared_version_exits_two_never_zero(self):
        """Plan v5 ruling: the live class run on a tree whose version cannot be read is NO-DATA (exit 2), never a
        skip that exits 0, so a required check never clears on it."""
        import subprocess
        root = make(self, {"README.md": "# clean\n", ".claude-plugin/marketplace.json": "{"})
        os.makedirs(os.path.join(root, "scripts"))
        for name in ("test_no_promise_to_cut_version.py", "cut_preflight.py", "test_cut_preflight.py"):
            shutil.copyfile(os.path.join(HERE, name), os.path.join(root, "scripts", name))
        proc = subprocess.run([sys.executable, "-B", os.path.join(root, "scripts", "test_no_promise_to_cut_version.py"),
                               "LiveTreeTest.test_no_current_page_promises_work_to_a_released_or_declared_version"],
                              cwd=root, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("NO-DATA: the declared version cannot be read", proc.stdout)

    def test_a_planted_promise_to_the_version_being_cut_refuses_the_preflight_row(self):
        """Plan v5 ruling: the matcher is proven on a 1.1.2 promise planted in a copy of the live page, never by
        restoring a 1.1.1 promise into the tree."""
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        root = make(self, {"README.md": readme + "\nA repair is planned for 1.1.2.\n"})
        verdict, _n, detail = P.check_no_promise(root, "1.1.2")
        self.assertEqual(verdict, P.REFUSED, detail)
        self.assertIn("README.md:%d" % (readme.count("\n") + 2), detail)


if __name__ == "__main__":
    result = unittest.main(exit=False).result
    if not result.wasSuccessful():
        sys.exit(1)
    if NO_DATA:
        for reason in NO_DATA:
            print("NO-DATA: %s (never a pass)" % reason)
        sys.exit(2)
    sys.exit(0)
