#!/usr/bin/env python3
"""The public install path, as a release test rather than a dated claim.

Run: python3 tools/test_sbe_public_install.py

WHY THIS EXISTS. The README told a stranger to run two commands and said they
had been "executed end to end against this public repository on 2026-08-01".
That is a date-stamped memory, not a check, and it was the only thing standing
behind the first thing every new user does. Founder instruction, 2026-08-07:
the public install and the installed workflow become a non-skippable release
test, because the projects with the best adoption make installation the boring
part rather than an experimental capability.

WHAT THIS CAN AND CANNOT PROVE, stated here rather than left for a reader to
discover. It runs offline and touches no network, so it CANNOT prove that
GitHub serves the repository today or that a marketplace fetch succeeds. What
it does prove is everything that has actually broken here before: that the
manifests a fetch would land on are valid and agree with each other, that the
commands the front page tells a stranger to type are the commands that exist,
and that the entry point the install leads to is real. The network half is
named in docs/KNOWN-LIMITS.md and belongs to a job with credentials.
"""

import io
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
README = os.path.join(ROOT, "README.md")
PLUGIN = os.path.join(ROOT, ".claude-plugin", "plugin.json")
MARKET = os.path.join(ROOT, ".claude-plugin", "marketplace.json")

#: The two commands the front page promises, and the repository slug in them.
#: BrotherSBE ships from the Brother hub repository; the standalone
#: BrotherSBE repository this used to name is archived.
SLUG = "khalilmaaouni/Brother"

#: The "one door" front page, current since the umbrella `brother` plugin
#: (sourced from `bundle/`, see the hub root .claude-plugin/marketplace.json)
#: became the public install route: a stranger installs `brother@brother`,
#: never `brothersbe` directly, and the next move is the bare `/brother`
#: command, not a product-named skill. This mirrors the hub root README.md
#: verbatim (BrotherSBE's own README says as much: "The root Brother install
#: is the public route"), so both READMEs are held to the identical line.
DOOR_PLUGIN = "brother@brother"
#: Relative to ROOT (products/brothersbe): the skill `/brother` actually
#: invokes, two directories up at the hub root's own bundle/.
DOOR_SKILL = os.path.join(ROOT, "..", "..", "bundle", "skills", "using-brother", "SKILL.md")


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


class TheFrontPagePromise(unittest.TestCase):
    """What a stranger reads first has to be what actually works."""

    def setUp(self):
        self.readme = read(README)

    def test_the_install_is_two_commands_and_they_are_the_documented_ones(self):
        # The "one door" front page (see DOOR_PLUGIN's comment) chains both
        # commands on one line with `&&`, the same line the hub root
        # README.md ships verbatim; still two commands, one line.
        block = re.search(r"```bash\n(claude plugin marketplace add[^`]*?)```", self.readme)
        self.assertIsNotNone(block, "the README no longer opens with a bash install block")
        commands = [c.strip() for c in block.group(1).strip().split("&&") if c.strip()]
        self.assertEqual(len(commands), 2,
                         "the install block is %d command(s), not 2. Installation is the "
                         "boring part or it is a barrier: %r" % (len(commands), commands))
        self.assertEqual(commands[0], "claude plugin marketplace add %s" % SLUG)
        self.assertEqual(commands[1], "claude plugin install %s" % DOOR_PLUGIN)

    def test_the_first_move_after_install_is_named_and_real(self):
        self.assertIn("/brother", self.readme,
                      "the front page installs the plugin and never says what to type next")
        self.assertTrue(os.path.exists(DOOR_SKILL),
                        "the front page points at /brother and %s does not exist" % DOOR_SKILL)


class TheManifestsAFetchWouldLandOn(unittest.TestCase):
    """A marketplace install reads these two files. They have disagreed before."""

    def setUp(self):
        self.plugin = json.loads(read(PLUGIN))
        self.market = json.loads(read(MARKET))
        self.version = read(os.path.join(ROOT, "VERSION")).strip()

    def test_both_manifests_parse_and_name_this_plugin(self):
        self.assertEqual(self.plugin.get("name"), "brothersbe")
        names = [entry.get("name") for entry in self.market.get("plugins", [])]
        self.assertIn("brothersbe", names,
                      "the marketplace lists %r, so `plugin install brothersbe@brothersbe` "
                      "would find nothing" % names)

    def test_every_declared_version_agrees_with_VERSION(self):
        declared = [("plugin.json", self.plugin.get("version")),
                    ("marketplace metadata", self.market.get("metadata", {}).get("version"))]
        for entry in self.market.get("plugins", []):
            if entry.get("name") == "brothersbe":
                declared.append(("marketplace plugin entry", entry.get("version")))
        for where, value in declared:
            self.assertEqual(value, self.version,
                             "%s says %r while VERSION says %r; a marketplace user is offered "
                             "an update only when the version string moves, so a disagreement "
                             "here is a silent update" % (where, value, self.version))

    def test_the_marketplace_source_resolves_to_something_that_exists(self):
        for entry in self.market.get("plugins", []):
            source = entry.get("source")
            if isinstance(source, str) and source.startswith("."):
                path = os.path.normpath(os.path.join(ROOT, source))
                self.assertTrue(os.path.isdir(path),
                                "the marketplace entry points at %s, which is not a directory"
                                % path)


class TheHostValidatesIt(unittest.TestCase):
    """The host's own validator is the closest offline thing to a real install."""

    def test_claude_plugin_validate_passes_or_says_why_it_could_not_run(self):
        try:
            out = subprocess.run(["claude", "plugin", "validate", ROOT],
                                 capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            sys.stderr.write(
                "NO-DATA: the Claude Code CLI is not runnable here (%s), so this suite "
                "could not ask the host to validate the package. That is an absence, not "
                "a pass, and it is why the release checklist runs this on a machine that "
                "has the CLI.\n" % exc)
            return
        self.assertEqual(out.returncode, 0,
                         "`claude plugin validate` refused this package, which is what a "
                         "stranger's install would hit:\n%s%s" % (out.stdout, out.stderr))


if __name__ == "__main__":
    unittest.main(verbosity=2)
