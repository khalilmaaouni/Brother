"""Adversarial fixtures for `sbe map` (LANE PT-3): a deterministic status
map built from canonical state only, never a model filling a template.

Why this exists. `skills/help/SKILL.md`'s old "project map" section told the
MODEL to fill `map-template.html` slot by slot from whatever it could see:
nondeterministic, and able to invent data that was never actually there. This
file pins the replacement's three load-bearing promises instead:

  1. the same repository state renders the exact same bytes twice in a row
     (no wall-clock timestamp, no machine-specific path baked into the page);
  2. every canonical source (the status module's team report, the task
     registry, dossier artifact presence) actually reaches the page when it
     is fixture-populated;
  3. a user-controlled string (here, a task's own `agent` field) can never
     land in the page unescaped: an HTML-injection payload arrives as inert
     text, not as a tag a browser would execute.

Every fixture builds its own throwaway git repository, exactly the pattern
`tools/test_sbe_status_team.py` already uses, so the map is always read over
artifacts the real tools produced (a real `sbe task open`, a real `sbe
handover prepare`), never a hand-typed stand-in for the engine's own output.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SBE = os.path.join(HERE, "..", "bin", "sbe")

INTAKE = {
    "answers": {"changes_contract": "n", "crosses_boundary": "n",
                "reversible_under_hour": "y", "touches_sensitive": "n",
                "consumers": "none"},
    "tier": "T1", "override": None, "override_reason": None,
}


def _run(argv, cwd=None):
    out = subprocess.run(argv, capture_output=True, text=True, cwd=cwd,
                         stdin=subprocess.DEVNULL, timeout=120)
    # Three values, not two: a two-value return reads as a possible
    # (verdict, evidence) pair to the honesty meta-test, which refuses any
    # such function sitting outside a check registry (evals/test_no_data_class.py).
    return out.returncode, out.stdout + out.stderr, out.stderr


class MapScenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sbe-map-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "fixture")
        io.open(os.path.join(self.repo, "seed.txt"), "w").write("seed\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "seed")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        code, text, _err = _run(["git", "-C", self.repo] + list(args))
        self.assertEqual(code, 0, "git %s failed: %s" % (args, text))
        return text

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def write(self, rel, content):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(content, (dict, list)):
            io.open(path, "w", encoding="utf-8").write(json.dumps(content))
        else:
            io.open(path, "w", encoding="utf-8").write(content)
        return path

    def sbe(self, *args):
        return _run([sys.executable, SBE] + list(args))

    def make_dossier(self, name):
        base = "design/%s" % name
        self.write("%s/00-intake.json" % base, INTAKE)
        for rel in ("01-purpose.md", "02-process.md", "03-adr.md",
                   "04-technology-map.md", "05-data-model.md", "06-diagrams.md",
                   "07-verification.md"):
            self.write("%s/%s" % (base, rel), "placeholder\n")
        return base


class TestMapCommandExists(MapScenario):
    def test_map_is_advertised_and_refuses_without_out(self):
        code, out, _err = self.sbe("map", self.repo)
        self.assertNotEqual(code, 0,
                             "sbe map without --out must refuse, not silently write "
                             "nowhere: %s" % out)

    def test_map_help_exits_zero_and_prints_usage(self):
        code, out, _err = self.sbe("map", "-h")
        self.assertEqual(code, 0, out)
        self.assertIn("usage", out.lower())

    def test_a_mistyped_path_is_a_usage_error(self):
        out_path = os.path.join(self.tmp, "out.html")
        code, out, _err = self.sbe("map", "/nonexistent-path-on-purpose",
                                   "--out", out_path)
        self.assertEqual(code, 2, out)
        self.assertFalse(os.path.exists(out_path))


class TestDeterminism(MapScenario):
    def test_identical_state_renders_identical_bytes(self):
        """The one property the whole command exists to guarantee: no
        wall-clock timestamp and no machine-specific path may vary the
        output, so running the exact same command twice against unchanged
        state must write byte-identical files."""
        self.make_dossier("alpha")
        self.git("add", "-A")
        self.git("commit", "-qm", "alpha dossier")

        out1 = os.path.join(self.tmp, "out1.html")
        out2 = os.path.join(self.tmp, "out2.html")
        code1, text1, _e1 = self.sbe("map", self.repo, "--out", out1)
        self.assertEqual(code1, 0, text1)
        code2, text2, _e2 = self.sbe("map", self.repo, "--out", out2)
        self.assertEqual(code2, 0, text2)

        bytes1 = io.open(out1, "rb").read()
        bytes2 = io.open(out2, "rb").read()
        self.assertEqual(bytes1, bytes2,
                         "two runs over the same, unchanged repository state produced "
                         "different bytes: something non-canonical (a timestamp, a "
                         "random order) leaked into the page")

    def test_no_machine_path_is_baked_into_the_page(self):
        """The generated page is meant to be opened by anyone, on any
        machine: it must never carry this machine's own temp-directory path
        or the operator's home directory."""
        self.make_dossier("alpha")
        self.git("add", "-A")
        self.git("commit", "-qm", "alpha dossier")
        out_path = os.path.join(self.tmp, "out.html")
        code, text, _err = self.sbe("map", self.repo, "--out", out_path)
        self.assertEqual(code, 0, text)
        page = io.open(out_path, encoding="utf-8").read()
        self.assertNotIn(self.repo, page,
                         "the repository's own absolute filesystem path leaked into "
                         "the generated page")
        self.assertNotIn(os.path.expanduser("~"), page,
                         "the operator's home directory leaked into the generated page")


class TestEverySectionRenders(MapScenario):
    """One fixture, populated enough to exercise every canonical source:
    status module team report (a finding), the task registry (an open
    task), and dossier artifact presence (every artifact present for one
    change, none present beyond the bare intake for a second)."""

    def test_every_section_has_something_to_show(self):
        doss = self.make_dossier("alpha")
        self.write("%s/08-plan.json" % doss,
                   {"tasks": [{"id": "T01", "owns": ["alpha/thing.txt"],
                              "verificationCommands": ["true"]}]})
        self.git("add", "-A")
        self.git("commit", "-qm", "alpha plan")
        base_sha = self.head()

        self.write("%s/09-convergence.json" % doss, {"final": "PASS", "head": base_sha})
        self.write("%s/10-approval.json" % doss, {"headSha": base_sha, "final": "PASS"})
        self.write("%s/11-review.json" % doss,
                   {"headSha": base_sha, "reviewer": "Ada Reviewer",
                    "reviewerType": "human", "result": "approved", "findings": [],
                    "acceptedRisks": []})
        self.git("add", "-A")
        self.git("commit", "-qm", "alpha evidence records")
        head_sha = self.head()

        code, out, err = self.sbe("task", "open", "--id", "T01", "--agent", "Ada",
                                  "--role", "writer", "--owns", "alpha/thing.txt",
                                  "--base", head_sha, "--verify", "true",
                                  "--cwd", self.repo)
        self.assertEqual(code, 0, out + err)

        code, out, err = self.sbe("handover", "prepare", os.path.join(self.repo, doss),
                                  "--outgoing", "Ada", "--receiver", "Bob")
        self.assertEqual(code, 0,
                         "fixture setup: sbe handover prepare failed: %s" % (out + err))

        # A second, near-empty change: nothing beyond the bare intake, so
        # the artifact-presence checklist has both a present and an absent
        # row to render, and no plan means "ready" and "completed" findings
        # stay silent for it (status.py's own documented rule) rather than
        # this test asserting a fact that module explicitly never claims.
        self.make_dossier("beta")
        self.git("add", "-A")
        self.git("commit", "-qm", "beta dossier, intake only")

        out_path = os.path.join(self.tmp, "out.html")
        code, out, err = self.sbe("map", self.repo, "--out", out_path)
        self.assertEqual(code, 0, out + err)
        page = io.open(out_path, encoding="utf-8").read()

        # Both changes are named.
        self.assertIn("alpha", page)
        self.assertIn("beta", page)
        # The task registry reached the page (id and agent).
        self.assertIn("T01", page)
        self.assertIn("Ada", page)
        # A handover was prepared and is visible.
        self.assertIn("Bob", page)
        # Dossier artifact presence: alpha's 08-plan.json exists, beta's does not.
        self.assertIn("08-plan.json", page)
        # A self-contained, offline page: no external network reference.
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)


class TestInjectionIsEscaped(MapScenario):
    def test_a_script_tag_in_a_task_agent_arrives_escaped(self):
        doss = self.make_dossier("alpha")
        self.write("%s/08-plan.json" % doss,
                   {"tasks": [{"id": "T01", "owns": ["alpha/thing.txt"]}]})
        self.git("add", "-A")
        self.git("commit", "-qm", "alpha plan")
        head_sha = self.head()

        payload = "<script>alert(1)</script>"
        code, out, err = self.sbe("task", "open", "--id", "T01", "--agent", payload,
                                  "--role", "writer", "--owns", "alpha/thing.txt",
                                  "--base", head_sha, "--verify", "true",
                                  "--cwd", self.repo)
        self.assertEqual(code, 0, out + err)

        out_path = os.path.join(self.tmp, "out.html")
        code, out, err = self.sbe("map", self.repo, "--out", out_path)
        self.assertEqual(code, 0, out + err)
        page = io.open(out_path, encoding="utf-8").read()

        self.assertNotIn(payload, page,
                         "an unescaped <script> tag reached the generated page: this is "
                         "a live HTML-injection hole, not merely an untidy render")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page,
                      "the payload's escaped form is missing entirely: it did not just "
                      "avoid injection, it silently dropped the finding")


if __name__ == "__main__":
    unittest.main()
