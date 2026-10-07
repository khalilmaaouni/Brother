#!/usr/bin/env python3
"""ACC8.a: fixture only suite for rulebook_inventory.py and rulebook_diet.py.

Every fixture here is invented rule text; none of it comes from the owner's
standing rules file, so the suite runs green under an empty HOME and publishes
nothing. Each test names the requirement it holds (REQ-ACC8-A1 to A10).

Python 3.9 compatible, standard library only. No em or en dashes in this file.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rulebook_diet as rd  # noqa: E402
import rulebook_inventory as ri  # noqa: E402

ENFORCER = "scripts/fixture_enforcer.py"
MISSING = "scripts/fixture_missing.py"

#: One unit per class, in file order, with the class each must get.
FIXTURE = "\n".join([
    "## First heading",
    "- Keep this rule: always measure before claiming a number.",
    "- Keep this rule: always measure before claiming a number.",
    "- Superseded by the measurement rule above; kept only as history.",
    "- Enforcement note: ENFORCED by " + MISSING + " which nobody can find.",
    "- Enforcement note: UNENFORCED, nothing fires. Prove: python3 " + ENFORCER + " selftest.",
    "- Enforcement note: ENFORCED. Prove: python3 " + ENFORCER + " prints OK over nine cases.",
    "",
]) + "\n"
EXPECTED = ["KEEP", "DUPLICATE", "SUPERSEDED-TEXT", "DEAD-REF", "UNENFORCED", "ENFORCED-INDEX"]

#: Continuation lines that read as a diff file header or hunk marker once a
#: unified diff prefixes them with "-" or "+" (the spec council's finding).
INJECTION = ["-- dash dash", "++ plus plus", "@@ -1,2 +3,4 @@", "---", "+++", "--", "++"]
#: The unit twice: the second is a DUPLICATE and is removed, so every
#: injection line appears both as context and as a removed line.
INJECTED = "\n".join(["- Injected rule, kept."] + INJECTION + ["- Injected rule, kept."] + INJECTION
                     + ["- Plain rule after it."]) + "\n"


def _tree():
    root = tempfile.mkdtemp(prefix="rulebook-")
    os.makedirs(os.path.join(root, "repo", "scripts"))
    with open(os.path.join(root, "repo", ENFORCER), "w") as handle:
        handle.write("print('ok')\n")
    os.makedirs(os.path.join(root, "home"))
    return root


def _classes(text, home, repo):
    return [u["class"] for u in ri.classify_all(text, home, repo)]


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.root = _tree()
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.root, "repo")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a1_one_class_per_unit(self):
        self.assertEqual(_classes(FIXTURE, self.home, self.repo), EXPECTED)
        self.assertEqual(len(set(EXPECTED)), 6)

    def test_a6_unenforced_wins_over_enforced_index(self):
        text = "- Enforcement note: ENFORCED by " + ENFORCER + " for half, UNENFORCED for the rest.\n"
        self.assertEqual(_classes(text, self.home, self.repo), ["UNENFORCED"])

    def test_a7_negation_and_active_rescind_keep(self):
        text = "\n".join([
            "- This rule is not withdrawn; it stands.",
            "- NEW ORDER (rescinds the old one): do the work directly.",
            "- The old order supersedes nothing and is never superseded.",
        ]) + "\n"
        self.assertEqual(_classes(text, self.home, self.repo), ["KEEP", "KEEP", "KEEP"])

    def test_a7_passive_forms_retire(self):
        text = "\n".join([
            "- This rule is superseded by the newer one.",
            "- Rescinded 2026-01-01, kept for history.",
            "- It was withdrawn after the review.",
        ]) + "\n"
        self.assertEqual(_classes(text, self.home, self.repo), ["SUPERSEDED-TEXT"] * 3)

    def test_a5_dead_reference_with_no_existing_paths(self):
        unit = ri.rule_units("- Prove: python3 " + ENFORCER + " selftest.\n")[0]
        self.assertEqual(ri.classify_unit(unit, {}, []), "DEAD-REF")
        self.assertEqual(ri.classify_unit(unit, {}, [ENFORCER]), "KEEP")

    def test_duplicate_needs_exact_norm(self):
        text = "\n".join([
            "- Hold at 2 GB free disk.",
            "- Hold at 3 GB free disk.",
            "- Hold at 2 GB free disk, always.",
            "-   hold   AT 2 gb FREE disk.",
        ]) + "\n"
        self.assertEqual(_classes(text, self.home, self.repo), ["KEEP", "KEEP", "KEEP", "DUPLICATE"])

    def test_a11_qualified_enforcement_is_never_pointered(self):
        """Owner review 2026-10-03, defect 1: a PARTLY ENFORCED unit naming NOT
        built controls must stay whole, never become an unqualified pointer."""
        text = ("- ENFORCEMENT: PARTLY ENFORCED. Built: python3 " + ENFORCER + " selftest. "
                "NOT built: nothing refuses a close with no survivor count.\n")
        self.assertEqual(_classes(text, self.home, self.repo), ["KEEP"])
        for word in ("stated discipline", "Candidate control: a stop hook", "not built"):
            text = "- ENFORCEMENT: ENFORCED by " + ENFORCER + "; " + word + ".\n"
            self.assertEqual(_classes(text, self.home, self.repo), ["KEEP"], word)
        text = "- ENFORCEMENT: ENFORCED. Prove: python3 " + ENFORCER + " selftest prints OK.\n"
        self.assertEqual(_classes(text, self.home, self.repo), ["ENFORCED-INDEX"])

    def test_a12_superseded_unit_keeps_unrestated_sentences(self):
        """Owner review 2026-10-03, defect 2: a superseded bullet can carry a
        sentence the superseding rule does not restate; that sentence survives."""
        text = "\n".join([
            "- NEW RULE: decide reversible things yourself and record them.",
            "- Superseded by NEW RULE above. Surviving idea: keep the work as a bundle, never a question.",
            "- Superseded by NEW RULE above. Decide reversible things yourself and record them.",
        ]) + "\n"
        units = ri.classify_all(text, self.home, self.repo)
        self.assertEqual([u["class"] for u in units], ["KEEP", "SUPERSEDED-TEXT", "SUPERSEDED-TEXT"])
        self.assertEqual(units[1]["surviving"], ["Surviving idea: keep the work as a bundle, never a question."])
        self.assertEqual(units[2]["surviving"], [])
        after = rd.apply_unified(text, rd.propose_diet(text, self.home, self.repo))
        self.assertEqual(after, "\n".join([
            "- NEW RULE: decide reversible things yourself and record them.",
            "- Surviving idea: keep the work as a bundle, never a question.",
        ]) + "\n")

    def test_a13_pointer_keeps_the_proving_command(self):
        text = ("- ENFORCEMENT: ENFORCED by " + ENFORCER + ", the long story of why. "
                "Prove: `python3 " + ENFORCER + " --selftest` prints OK over nine cases.\n")
        after = rd.apply_unified(text, rd.propose_diet(text, self.home, self.repo))
        self.assertIn("ENFORCED by " + ENFORCER, after)
        self.assertIn("Prove: `python3 " + ENFORCER + " --selftest` prints OK over nine cases.", after)
        self.assertNotIn("long story", after)

    def test_tilde_and_absolute_paths_resolve(self):
        with open(os.path.join(self.home, "hook.py"), "w") as handle:
            handle.write("\n")
        text = "- ENFORCED by ~/hook.py and by " + os.path.join(self.home, "hook.py") + ".\n"
        self.assertEqual(_classes(text, self.home, self.repo), ["ENFORCED-INDEX"])


class SplitTests(unittest.TestCase):
    def test_a8_units_split(self):
        text = "\n".join([
            "- Bullet with no heading above it.",
            "## Heading",
            "- Top bullet",
            "  indented continuation",
            "zero indent continuation",
            "- Second bullet",
            "",
            "prose after a blank line is not a unit",
        ]) + "\n"
        units = ri.rule_units(text)
        self.assertEqual(len(units), 3)
        self.assertIsNone(units[0]["heading_line"])
        self.assertEqual(units[0]["line"], 1)
        self.assertEqual(units[0]["bytes"], len("- Bullet with no heading above it.\n"))
        self.assertEqual(units[1]["heading_line"], 2)
        self.assertEqual((units[1]["line"], units[1]["end_line"]), (3, 5))
        self.assertEqual(units[1]["bytes"], len("- Top bullet\n  indented continuation\nzero indent continuation\n"))
        self.assertEqual((units[2]["line"], units[2]["end_line"]), (6, 6))

    def test_a8_empty_and_single(self):
        self.assertEqual(ri.rule_units(""), [])
        self.assertEqual(len(ri.rule_units("- one\n")), 1)
        self.assertEqual(len(ri.rule_units("- one")), 1)
        with self.assertRaises(ValueError):
            ri.rule_units(None)


class InventoryTests(unittest.TestCase):
    ALLOWED = {"schema", "measured_at", "bytes", "sha256", "lines", "headings", "units", "classes",
               "largest", "dead_ref_lines", "unit_index", "count", "heading_line", "line", "end_line",
               "class"} | set(ri.CLASSES)

    def setUp(self):
        self.root = _tree()
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.root, "repo")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _keys(self, node, found):
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(key)
                self._keys(value, found)
        elif isinstance(node, list):
            for value in node:
                self._keys(value, found)

    def test_a2_inventory_carries_no_rule_text(self):
        result = ri.inventory(FIXTURE, self.home, self.repo)
        found = set()
        self._keys(result, found)
        self.assertTrue(found <= self.ALLOWED, found - self.ALLOWED)
        dumped = json.dumps(result)
        for unit in ri.rule_units(FIXTURE):
            text = unit["text"].strip()
            for start in range(0, max(1, len(text) - 20)):
                self.assertNotIn(text[start:start + 20], dumped)
        self.assertEqual(result["units"], 6)
        self.assertEqual(result["bytes"], len(FIXTURE.encode("utf-8")))
        self.assertEqual(result["classes"]["DUPLICATE"]["count"], 1)
        self.assertEqual(result["dead_ref_lines"], [5])
        self.assertEqual(len(result["largest"]), 6)
        self.assertEqual(sorted(result["largest"][0].keys()), ["bytes", "heading_line", "line"])
        self.assertGreaterEqual(result["largest"][0]["bytes"], result["largest"][-1]["bytes"])

    def test_non_utf8_is_no_data(self):
        rules = os.path.join(self.root, "rules.md")
        with open(rules, "wb") as handle:
            handle.write(b"- rule \xff\xfe\n")
        out = os.path.join(self.root, "out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ri.main(["--rules", rules, "--out", out, "--home", self.home, "--repo", self.repo])
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", buf.getvalue())
        self.assertFalse(os.path.exists(os.path.join(out, ri.INVENTORY_NAME)))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.root = _tree()
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.root, "repo")
        self.rules = os.path.join(self.root, "rules.md")
        self.out = os.path.join(self.root, "out")
        with open(self.rules, "w") as handle:
            handle.write(FIXTURE)
        past = os.stat(self.rules).st_mtime - 100
        os.utime(self.rules, (past, past))
        self._run(ri.main, ["--rules", self.rules, "--out", self.out, "--home", self.home, "--repo", self.repo])
        self._run(rd.main, ["--rules", self.rules, "--out", self.out, "--home", self.home, "--repo", self.repo])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, fn, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fn(argv)
        return code, buf.getvalue()

    def _check(self):
        return self._run(ri.main, ["--rules", self.rules, "--out", self.out, "--check"])

    def test_a10_fresh_passes(self):
        code, out = self._check()
        self.assertEqual(code, 0, out)
        self.assertIn("PASS", out)

    def test_a10_missing_artifact_fails(self):
        os.remove(os.path.join(self.out, ri.DIET_NAME))
        code, out = self._check()
        self.assertEqual(code, 1)
        self.assertIn("missing", out)

    def test_a10_older_artifact_fails(self):
        path = os.path.join(self.out, ri.INVENTORY_NAME)
        past = os.stat(self.rules).st_mtime - 50
        os.utime(path, (past, past))
        code, out = self._check()
        self.assertEqual(code, 1)
        self.assertIn("older", out)

    def test_a10_byte_count_mismatch_fails(self):
        with open(self.rules, "a") as handle:
            handle.write("- one more rule\n")
        past = os.stat(self.rules).st_mtime - 100
        os.utime(self.rules, (past, past))
        code, out = self._check()
        self.assertEqual(code, 1)
        self.assertIn("records", out)

    def test_a10_unreadable_rules_is_2(self):
        os.remove(self.rules)
        code, out = self._check()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_diet_file_is_private_and_headed(self):
        path = os.path.join(self.out, ri.DIET_NAME)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path) as handle:
            first = handle.readline()
        self.assertTrue(ri.DIET_HEADER_RE.match(first), first)


class DietTests(unittest.TestCase):
    def setUp(self):
        self.root = _tree()
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.root, "repo")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a9_diet_touches_only_its_classes(self):
        units = ri.classify_all(FIXTURE, self.home, self.repo)
        diff = rd.propose_diet(FIXTURE, self.home, self.repo)
        after = rd.apply_unified(FIXTURE, diff)
        for unit in units:
            cls = unit["class"]
            text = unit["text"]
            if cls == "DUPLICATE":
                continue  # its text equals the kept unit's; the count check below covers it
            if cls == "SUPERSEDED-TEXT":
                self.assertNotIn(text, after, cls)
            elif cls == "ENFORCED-INDEX":
                self.assertNotIn(text, after, cls)
                self.assertIn("- ENFORCEMENT: ENFORCED by " + ENFORCER, after)
            else:
                self.assertIn(text, after, cls)
        kept = FIXTURE.split("\n")[1] + "\n"
        self.assertEqual(after.count(kept), 1)
        self.assertEqual(len(ri.rule_units(after)), 4)

    def test_a3_proposal_never_edits(self):
        path = os.path.join(self.root, "rules.md")
        with open(path, "w") as handle:
            handle.write(FIXTURE)
        before = os.stat(path)
        with open(path, "rb") as handle:
            raw = handle.read()
        diff = rd.propose_diet(raw.decode("utf-8"), self.home, self.repo)
        self.assertTrue(diff.startswith("--- a/CLAUDE.md"))
        after = os.stat(path)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), raw)
        self.assertEqual(before.st_mtime, after.st_mtime)
        self.assertEqual(before.st_size, after.st_size)

    def test_a4_projected_bytes_matches_patch(self):
        patch = shutil.which("patch")
        self.assertIsNotNone(patch, "patch(1) is required to prove projected_bytes")
        diff = rd.diet_header(FIXTURE) + rd.propose_diet(FIXTURE, self.home, self.repo)
        src = os.path.join(self.root, "CLAUDE.md")
        with open(src, "w") as handle:
            handle.write(FIXTURE)
        diff_path = os.path.join(self.root, "d.diff")
        with open(diff_path, "w") as handle:
            handle.write(diff)
        proc = subprocess.run([patch, "-p1", "-i", diff_path], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(rd.projected_bytes(FIXTURE, diff), os.stat(src).st_size)
        self.assertLess(rd.projected_bytes(FIXTURE, diff), len(FIXTURE.encode("utf-8")))

    def _patch(self, text, diff, name="CLAUDE.md"):
        """Apply diff with patch(1) to text in a scratch tree and return the result."""
        patch = shutil.which("patch")
        self.assertIsNotNone(patch, "patch(1) is required")
        root = tempfile.mkdtemp(prefix="rulebook-patch-", dir=self.root)
        with open(os.path.join(root, name), "w", newline="") as handle:
            handle.write(text)
        with open(os.path.join(root, "d.diff"), "w", newline="") as handle:
            handle.write(rd.diet_header(text) + diff)
        proc = subprocess.run([patch, "-p1", "-i", "d.diff"], cwd=root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(os.path.join(root, name), newline="") as handle:
            return handle.read()

    def test_a14_injection_lines_are_data_to_every_consumer(self):
        """REQ-ACC8-A14: a removed "-- x" line prints as "--- x" and a removed
        "@@" line as "-@@"; the in-memory text is the oracle and every consumer
        (apply_unified, projected_bytes, patch) must reproduce it exactly."""
        units = ri.classify_all(INJECTED, self.home, self.repo)
        self.assertEqual([u["class"] for u in units], ["KEEP", "DUPLICATE", "KEEP"])
        expected = rd.diet_text(INJECTED, self.home, self.repo)
        self.assertEqual(len(ri.rule_units(expected)), 2)
        for line in INJECTION:
            self.assertIn("\n" + line + "\n", expected)
        diff = rd.propose_diet(INJECTED, self.home, self.repo)
        self.assertTrue(diff.startswith("--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ "), diff[:60])
        self.assertIn("\n--- dash dash\n", diff)
        self.assertIn("\n-@@ -1,2 +3,4 @@\n", diff)
        self.assertIn("\n----\n", diff)
        self.assertIn("\n-+++\n", diff)
        self.assertEqual(rd.apply_unified(INJECTED, diff), expected)
        self.assertEqual(rd.projected_bytes(INJECTED, diff), len(expected.encode("utf-8")))
        self.assertEqual(self._patch(INJECTED, diff), expected)

    def test_a14_crlf_round_trips(self):
        crlf = INJECTED.replace("\n", "\r\n")
        expected = rd.diet_text(crlf, self.home, self.repo)
        self.assertEqual(expected, rd.diet_text(INJECTED, self.home, self.repo).replace("\n", "\r\n"))
        diff = rd.propose_diet(crlf, self.home, self.repo)
        self.assertEqual(rd.apply_unified(crlf, diff), expected)
        self.assertEqual(rd.projected_bytes(crlf, diff), len(expected.encode("utf-8")))

    def test_a14_generated_lines_are_bullets(self):
        """Every line the diet adds is a pointer or a surviving sentence, so it
        starts with "- " and can never print as "+++ " or "+@@"."""
        text = FIXTURE + "\n".join([
            "- Superseded by the first rule. ++ survives as a sentence.",
            "- ENFORCEMENT: ENFORCED by " + ENFORCER + ". @@ -1 +1 @@ is in the prose.",
        ]) + "\n"
        diff = rd.propose_diet(text, self.home, self.repo)
        added = [row for row in diff.split("\n") if row.startswith("+") and not row.startswith("+++ b/")]
        self.assertTrue(added)
        for row in added:
            self.assertTrue(row.startswith("+- "), row)

    def test_header_looking_line_inside_hunk_is_data(self):
        old = "- a\n--- not a header\n- b\n"
        new = "- a\n- b\n"
        diff = rd.unified(old, new)
        self.assertIn("\n---- not a header\n", diff)
        self.assertEqual(rd.apply_unified(old, diff), new)
        self.assertEqual(rd.projected_bytes(old, diff), len(new))

    def test_apply_unified_refuses_mismatch(self):
        diff = rd.unified("- a\n- b\n", "- a\n")
        with self.assertRaises(ValueError):
            rd.apply_unified("- a\n- c\n", diff)
        self.assertEqual(rd.apply_unified("- a\n", ""), "- a\n")

    def test_proposed_mode_writes_owner_diff(self):
        rules = os.path.join(self.root, "rules.md")
        proposed = os.path.join(self.root, "proposed.md")
        with open(rules, "w") as handle:
            handle.write(FIXTURE)
        with open(proposed, "w") as handle:
            handle.write("## First heading\n- Keep this rule: always measure before claiming a number.\n")
        out = os.path.join(self.root, "out")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rd.main(["--rules", rules, "--out", out, "--proposed", proposed,
                            "--home", self.home, "--repo", self.repo])
        self.assertEqual(code, 0)
        with open(os.path.join(out, ri.DIET_NAME)) as handle:
            diff = handle.read()
        self.assertEqual(rd.projected_bytes(FIXTURE, diff), os.stat(proposed).st_size)
        self.assertIn("projected_bytes=%d" % os.stat(proposed).st_size, buf.getvalue())


class ApplyTests(unittest.TestCase):
    """REQ-ACC8-A15: --apply writes the in-memory diet, never the diff, and
    refuses anything that differs from what the inventory measured."""

    def setUp(self):
        self.root = _tree()
        self.home = os.path.join(self.root, "home")
        self.repo = os.path.join(self.root, "repo")
        self.rules = os.path.join(self.root, "rules.md")
        self.out = os.path.join(self.root, "out")
        with open(self.rules, "w") as handle:
            handle.write(FIXTURE)
        os.chmod(self.rules, 0o600)
        self.expected = rd.diet_text(FIXTURE, self.home, self.repo)
        self.assertNotEqual(self.expected, FIXTURE)
        self._inventory()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _inventory(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ri.main(["--rules", self.rules, "--out", self.out, "--home", self.home, "--repo", self.repo])
        self.assertEqual(code, 0, buf.getvalue())

    def _apply(self, rules=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = rd.main(["--rules", rules or self.rules, "--out", self.out, "--apply",
                            "--home", self.home, "--repo", self.repo])
        return code, buf.getvalue()

    def _read(self):
        with open(self.rules, "rb") as handle:
            return handle.read().decode("utf-8")

    def _no_temp_left(self):
        self.assertEqual([n for n in os.listdir(self.root) if n.startswith(".rulebook-diet-")], [])

    def test_a15_apply_writes_the_in_memory_diet(self):
        code, out = self._apply()
        self.assertEqual(code, 0, out)
        self.assertIn("applied:", out)
        self.assertEqual(self._read(), self.expected)
        self.assertEqual(os.stat(self.rules).st_mode & 0o777, 0o600)
        self._no_temp_left()

    def test_a15_apply_never_reads_the_diff(self):
        foreign = "--- a/other.md\n+++ b/other.md\n@@ -1 +1 @@\n-x\n+y\n"
        rd.write_private(os.path.join(self.out, ri.DIET_NAME), rd.diet_header(FIXTURE) + foreign)
        code, out = self._apply()
        self.assertEqual(code, 0, out)
        self.assertEqual(self._read(), self.expected)
        with open(self.rules, "w") as handle:
            handle.write(FIXTURE)
        self._inventory()
        os.remove(os.path.join(self.out, ri.DIET_NAME))
        code, out = self._apply()
        self.assertEqual(code, 0, out)
        self.assertEqual(self._read(), self.expected)

    def test_a15_second_apply_is_refused(self):
        self.assertEqual(self._apply()[0], 0)
        code, out = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("changed since the inventory", out)
        self.assertEqual(self._read(), self.expected)

    def test_a15_edited_file_is_refused(self):
        with open(self.rules, "a") as handle:
            handle.write("- one more rule\n")
        edited = self._read()
        code, out = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("re-run the inventory", out)
        self.assertEqual(self._read(), edited)

    def test_a15_changed_outside_units_is_refused(self):
        """Isolates the byte count and sha256 guard: a heading edit leaves the
        unit index identical, so only that guard can refuse it. Same length
        first (sha256 alone), then a longer heading (byte count too)."""
        for heading in ("## First headinG", "## First heading, edited"):
            with open(self.rules, "w") as handle:
                handle.write(FIXTURE.replace("## First heading", heading, 1))
            edited = self._read()
            self.assertEqual(ri.unit_index(ri.classify_all(edited, self.home, self.repo)),
                             ri.unit_index(ri.classify_all(FIXTURE, self.home, self.repo)))
            code, out = self._apply()
            self.assertEqual(code, 1, heading)
            self.assertIn("changed since the inventory", out)
            self.assertEqual(self._read(), edited)

    def test_a15_vanished_enforcer_is_refused(self):
        os.remove(os.path.join(self.repo, ENFORCER))
        code, out = self._apply()
        self.assertEqual(code, 1)
        self.assertIn("unit index differs", out)
        self.assertEqual(self._read(), FIXTURE)

    def test_a15_missing_or_corrupt_inventory_is_refused(self):
        path = os.path.join(self.out, ri.INVENTORY_NAME)
        for content in (None, "not json", "[]", "{}", "{\"bytes\": 1}"):
            if content is None:
                os.remove(path)
            else:
                with open(path, "w") as handle:
                    handle.write(content)
            code, out = self._apply()
            self.assertEqual(code, 1, repr(content))
            self.assertIn("FAIL", out)
            self.assertEqual(self._read(), FIXTURE)

    def test_a15_unreadable_rules_is_no_data(self):
        os.remove(self.rules)
        code, out = self._apply()
        self.assertEqual(code, 2)
        self.assertIn("NO-DATA", out)

    def test_a15_failed_write_leaves_the_file(self):
        real = rd.os.replace

        def refuse(src, dst):
            raise OSError("disk full")

        rd.os.replace = refuse
        try:
            code, out = self._apply()
        finally:
            rd.os.replace = real
        self.assertEqual(code, 1)
        self.assertIn("file left as it was", out)
        self.assertEqual(self._read(), FIXTURE)
        self._no_temp_left()

    def _between_compute_and_write(self, action):
        """Run --apply with action() fired after the diet is computed and
        before the write: the window a concurrent actor can hit."""
        real = rd.diet_lines

        def hooked(text, home, repo_root):
            result = real(text, home, repo_root)
            action()
            return result

        rd.diet_lines = hooked
        try:
            return self._apply()
        finally:
            rd.diet_lines = real

    def test_a15_concurrent_edit_before_replace_is_refused(self):
        """Review gap 1: the file is re-hashed right before os.replace."""
        def edit():
            with open(self.rules, "a") as handle:
                handle.write("- written by another session\n")

        code, out = self._between_compute_and_write(edit)
        self.assertEqual(code, 1, out)
        self.assertIn("changed while the diet was computed", out)
        self.assertEqual(self._read(), FIXTURE + "- written by another session\n")
        self._no_temp_left()

    def test_a15_symlink_retargeted_midway_writes_the_original(self):
        """Review gap 2: the real path is resolved once, so a symlink swapped
        between the read and the write never leads the diet to another file."""
        target = os.path.join(self.root, "target.md")
        other = os.path.join(self.root, "other.md")
        os.rename(self.rules, target)
        os.symlink(target, self.rules)
        with open(other, "w") as handle:
            handle.write("- Another file entirely.\n")

        def retarget():
            os.remove(self.rules)
            os.symlink(other, self.rules)

        code, out = self._between_compute_and_write(retarget)
        self.assertEqual(code, 0, out)
        with open(target) as handle:
            self.assertEqual(handle.read(), self.expected)
        with open(other) as handle:
            self.assertEqual(handle.read(), "- Another file entirely.\n")

    def test_a15_symlink_stays_a_symlink(self):
        target = os.path.join(self.root, "target.md")
        os.rename(self.rules, target)
        os.symlink(target, self.rules)
        code, out = self._apply()
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.islink(self.rules))
        with open(target) as handle:
            self.assertEqual(handle.read(), self.expected)

    def test_a15_nothing_to_apply(self):
        with open(self.rules, "w") as handle:
            handle.write("- One rule, nothing to diet.\n")
        self._inventory()
        before = os.stat(self.rules)
        code, out = self._apply()
        self.assertEqual(code, 0)
        self.assertIn("nothing to apply", out)
        self.assertEqual(os.stat(self.rules).st_mtime, before.st_mtime)


if __name__ == "__main__":
    unittest.main()
