#!/usr/bin/env python3
"""Documentation consistency tests. Run: python3 tools/test_bm_docs.py

WHY THIS SUITE EXISTS
  Loop 10 of the post-audit plan: active documentation kept carrying facts that
  had already moved. A quickstart told installers to expect a test count that
  was 90 tests out of date, and then told them a mismatch meant a broken
  install. Install pages listed four hooks while five shipped, so the fence,
  the one hook that can refuse a write, was off on every hand-wired install.
  A README declared a defect open that had been closed two days earlier.

  Every one of those was reproducible by reading a file, which means a test can
  catch it. This suite compares the pages against tools/bm_project_facts.py,
  which reads the facts out of the tree instead of out of someone's memory.

WHAT IT DELIBERATELY DOES NOT DO
  It does not require documentation to quote a test count correctly. It
  requires active pages not to quote one at all: counts move with every test
  that lands, and a page pinned to one trains readers to distrust the page.
  Exact counts stay in dated evidence (CHANGELOG.md, the dated files under
  docs/ and its subdirectories), which this suite treats as historical and
  leaves alone, checking only that they say so at the top.

Standard library only. Python 3.9. Reads files, writes none.
No em or en dashes anywhere in this file or its output.
"""
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FACTS_PATH = os.path.join(HERE, "bm_project_facts.py")

_spec = importlib.util.spec_from_file_location("bm_project_facts", FACTS_PATH)
bpf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bpf)
FACTS = bpf.facts()


def _clones_primary_dir(line):
    """True when a `git clone` LINE targets the pinned primary skill
    directory, not the development one. FACTS["dev_skill_dir"] is
    FACTS["primary_skill_dir"] plus a "-dev" suffix, so a bare substring
    check on the primary directory also matches the development line;
    excluding a line that also names the dev directory is what tells the two
    apart."""
    return (FACTS["primary_skill_dir"] in line
            and FACTS["dev_skill_dir"] not in line)

# The eight required pack sections, read from the generator rather than retyped:
# a test that keeps its own copy of the list stops testing the order the moment
# the two disagree.
_pspec = importlib.util.spec_from_file_location(
    "bm_packs_for_docs_tests", os.path.join(HERE, "bm_packs.py"))
_bpk = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(_bpk)
_PACK_SECTIONS = _bpk.SECTIONS

# The drawn vocabulary, loaded for the same reason and by the same technique:
# references/visual-surface.md is prose ABOUT this tuple and this table, so a
# test carrying its own copy of either would stop testing the page the moment
# the two disagreed, which is the exact defect it exists to catch.
_vspec = importlib.util.spec_from_file_location(
    "bm_visual_for_docs_tests", os.path.join(HERE, "bm_visual.py"))
_bv = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(_bv)
VISUAL_REGISTER = os.path.join("references", "visual-surface.md")
VISUAL_DESIGN = os.path.join("docs", "program", "absolute-lead",
                             "DESIGN-visual-surface.md")

# Pages a new installer reads as CURRENT state. Anything not listed here is
# either dated evidence (checked separately, below) or a register with its own
# rules (docs/NOT-FINALIZED.md carries dated numbers on purpose).
ACTIVE_DOCS = (
    "README.md",
    "SKILL.md",
    # THE FIFTEEN COMMAND FILES, added 2026-08-07. They are the surface a user
    # types at, and they were outside this guard while nine documentation
    # pages sat inside it. The cost of that was measured, not theorized: the
    # same stale install sentence that had rotted in the guided skill was
    # ALSO sitting in commands/brotherme-help.md, and it survived the fix of
    # the first one because no check could see the second. A user-facing file
    # this guard cannot read is the defect; the sentence is only its symptom.
    os.path.join("commands", "brotherme-auto-status.md"),
    os.path.join("commands", "brotherme-auto.md"),
    os.path.join("commands", "brotherme-brief.md"),
    os.path.join("commands", "brotherme-decisions.md"),
    os.path.join("commands", "brotherme-deliver.md"),
    os.path.join("commands", "brotherme-handback.md"),
    os.path.join("commands", "brotherme-handover-pack.md"),
    os.path.join("commands", "brotherme-help.md"),
    os.path.join("commands", "brotherme-next.md"),
    os.path.join("commands", "brotherme-review.md"),
    os.path.join("commands", "brotherme-start.md"),
    os.path.join("commands", "brotherme-status.md"),
    os.path.join("commands", "brotherme-stop.md"),
    os.path.join("commands", "brotherme-update.md"),
    os.path.join("commands", "brotherme-view.md"),
    # The GUIDED skill, added 2026-08-07. It is the file a beginner actually
    # reads, and it sat outside this guard while nine less important pages sat
    # inside it: it kept telling users the plugin path had been installed
    # exactly once and that the clone was the verified path, for a whole
    # release after the smoke test made that false. A user-facing file that
    # no truth check can see is the shape of that defect, not an oversight
    # about one sentence.
    os.path.join("skills", "brotherme", "SKILL.md"),
    os.path.join("docs", "QUICKSTART.md"),
    os.path.join("docs", "SETUP.md"),
    os.path.join("docs", "RELEASE.md"),
    os.path.join("docs", "HOOKS.md"),
    os.path.join("docs", "HOW-IT-WORKS.md"),
    os.path.join("docs", "CORRECTION-LEARNING.md"),
    os.path.join("docs", "KNOWN-LIMITS.md"),
    # The continuity page, added 2026-08-08 with Phase C step 4. It states a
    # law a session is meant to obey unattended, which puts it in the same
    # class as the install pages: a stale sentence here is read at three in
    # the morning by something that will act on it.
    os.path.join("docs", "CONTINUITY.md"),
    # The combined-use page, added 2026-08-11 by founder decision. It is the
    # page a second person on a team reads before they read anything else,
    # and it makes claims about both products' command surfaces. A page that
    # tells a newcomer which command to type is exactly the class this guard
    # exists for: the fifteen command files and the guided skill were added
    # for the same reason, after a stale sentence survived in the file no
    # check could see.
    os.path.join("docs", "WORKING-WITH-BROTHERSBE.md"),
)

# The three install pages. Every one of them must describe the same install.
INSTALL_DOCS = ("README.md", os.path.join("docs", "QUICKSTART.md"),
                os.path.join("docs", "SETUP.md"))

# A file under docs/ whose NAME carries a date is dated evidence by convention.
# The scan is recursive: the design specs and plans live in subdirectories, and
# README links straight into one of them, so a marker check that only read the
# top level left the linked page reading as current state.
DATED_NAME = re.compile(r"\d{4}-\d{2}-\d{2}.*\.md$")

# A version claim about the tree the reader is holding, as opposed to a mention
# of an older release in a history section. Only the current-state phrasings.
VERSION_TOKEN = r"[`'\"]?v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)"
VERSION_CLAIM = re.compile(
    r"(?:you are (?:installing|running|on)|"
    r"this (?:is|release is|version is|tree is)|"
    r"the current (?:version|release|candidate|tag)(?: is)?|"
    r"current(?:ly)? at|"
    r"the version (?:is|here is))"
    r"(?:\s+(?:version|release|tag|candidate))?\s+" + VERSION_TOKEN,
    re.IGNORECASE)

# The one alternative to the HISTORICAL marker on a dated page: an explicit
# statement that the page is current. Anchored to the word Status so a passing
# mention of the word "current" in prose does not silently satisfy it.
CURRENT_STATUS = re.compile(r"^Status:\s*CURRENT\b", re.MULTILINE)

# The HISTORICAL marker itself, matched at LINE START only (optionally behind
# markdown bold and leading whitespace), never as a bare substring anywhere in
# the page's head. The old check was `"HISTORICAL" in head`, which trips on
# any MENTION of the word, not just a page DECLARING itself historical.
# Reproduced while writing docs/HANDOVER-2026-07-30.md: a sentence merely
# describing this very convention ("...the doc-consistency suite enforces
# that marking...HISTORICAL...") failed the suite before this fix. A page
# must declare the marker at the start of a line to count.
HISTORICAL_MARKER = re.compile(r"^\s*\**\s*HISTORICAL\b", re.MULTILINE)


def _is_marked_historical(head):
    """True only when `head` DECLARES itself historical at a line start, not
    when it merely mentions the word in prose."""
    return bool(HISTORICAL_MARKER.search(head))

NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight"}


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def dated_docs():
    """Every dated document under docs/, at any depth, repo-relative."""
    out = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "docs")):
        for name in sorted(filenames):
            if DATED_NAME.search(name):
                out.append(os.path.relpath(os.path.join(dirpath, name), ROOT))
    return sorted(out)


class TestGeneratedFacts(unittest.TestCase):
    def test_the_tool_runs_and_prints_the_facts_docs_rely_on(self):
        r = subprocess.run([sys.executable, FACTS_PATH],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        for key in ("version", "is_development", "release_tag",
                    "install_target_tag", "schema_version", "hook_count",
                    "hook_events", "test_suites", "test_suite_files",
                    "gate_command", "supported_python_floor", "default_branch",
                    "retrieval_modes", "repo_url", "primary_skill_dir",
                    "dev_skill_dir", "install_command_pinned",
                    "install_command_dev"):
            self.assertIn(key, data)
        if data["is_development"]:
            self.assertIsNone(
                data["release_tag"],
                "a development identity must not carry a release_tag")
        else:
            self.assertEqual(data["release_tag"], "v" + data["version"])
        self.assertEqual(data["hook_count"], len(data["hook_events"]))
        self.assertEqual(data["test_suites"], len(data["test_suite_files"]))

    def test_it_reports_only_retrieval_modes_the_code_builds(self):
        """bm_learning.py discusses FTS5 as future work. A page that advertised
        it because a docstring mentioned it would be selling a mode nobody
        wrote, so the probe requires the virtual table, not the word."""
        store = read(os.path.join("tools", "bm_store.py"))
        built = bool(re.search(r"USING\s+fts5", store, re.IGNORECASE))
        self.assertEqual("fts5" in FACTS["retrieval_modes"], built)
        self.assertIn("lexical", FACTS["retrieval_modes"])

    def test_every_named_suite_file_exists(self):
        for name in FACTS["test_suite_files"]:
            self.assertTrue(os.path.exists(os.path.join(HERE, name)), name)


class TestNoStaleCurrentNumbers(unittest.TestCase):
    COUNT_PATTERNS = (
        re.compile(r"Ran\s+\d+\s+tests"),
        re.compile(r"\b\d+\s+tests\b"),
        re.compile(r"exactly\s+\d+\s+tests?", re.IGNORECASE),
    )

    def test_no_active_document_pins_a_test_count(self):
        offenders = []
        for rel in ACTIVE_DOCS:
            for i, line in enumerate(read(rel).split("\n"), 1):
                for pat in self.COUNT_PATTERNS:
                    if pat.search(line):
                        offenders.append("%s:%d %s" % (rel, i, line.strip()[:90]))
                        break
        self.assertEqual(
            offenders, [],
            "an active page pins a test count (%s). Counts move with every test "
            "that lands; say to run %s and expect %s instead, and keep exact "
            "counts in dated evidence."
            % ("; ".join(offenders), FACTS["gate_command"],
               FACTS["gate_expectation"]))

    def test_no_active_document_states_a_wrong_hook_count(self):
        """Catches the phrasings that actually went stale, without banning the
        legitimate 'the other four hooks' in docs/HOOKS.md, which counts the
        four non-fence hooks and is correct."""
        current = NUMBER_WORDS[FACTS["hook_count"]]
        # Plural only, and never the singular "the one hook that can refuse a
        # write", which is a description of the fence rather than a count.
        shapes = (r"wire the (\w+) hooks\b", r"through (\w+) hooks\b",
                  r"\bthe (\w+) hooks\b", r"(\w+) hook entries\b",
                  r"a list of (\w+) hooks\b")
        offenders = []
        for rel in ACTIVE_DOCS:
            text = read(rel)
            for shape in shapes:
                for m in re.finditer(shape, text):
                    word = m.group(1).lower()
                    if word in NUMBER_WORDS.values() and word != current:
                        offenders.append("%s: %r" % (rel, m.group(0)))
        self.assertEqual(
            offenders, [],
            "a page states a hook count that is not %d (%s). scripts/install.py "
            "HOOK_EVENTS is the source; run bm_project_facts.py --field "
            "hook_events." % (FACTS["hook_count"], "; ".join(offenders)))

    def test_every_install_page_names_every_hook_event(self):
        """The fence shipped OFF for anyone wiring by hand because PreToolUse
        was in docs/HOOKS.md and in no install page. Naming the event is the
        minimum; leaving one out is how that happened."""
        missing = []
        for rel in INSTALL_DOCS:
            text = read(rel)
            for event in FACTS["hook_events"]:
                if event not in text:
                    missing.append("%s: %s" % (rel, event))
        self.assertEqual(missing, [], "install page omits a hook event: %s"
                         % "; ".join(missing))

    def test_the_hand_wiring_blocks_actually_wire_the_fence(self):
        """Naming the event in prose is not wiring it. The JSON block a reader
        copies has to contain the fence command and its matcher."""
        for rel in (os.path.join("docs", "QUICKSTART.md"),
                    os.path.join("docs", "SETUP.md")):
            text = read(rel)
            self.assertIn("bm_fence_hook.py", text,
                          "%s: the copy-paste hook block has no fence command" % rel)
            self.assertIn("Edit|Write|MultiEdit|NotebookEdit|Bash", text,
                          "%s: the fence entry has no matcher, so writes through "
                          "the unlisted tools are ungated" % rel)


class TestNoTypedCurrentStateIdentity(unittest.TestCase):
    """The 2026-08-07 release-critical class: an active page typed a version
    identity into current-state prose ("currently reads 2.0.0-rc.12.dev1")
    and it survived two releases unnoticed, on two pages. The calibration run
    that built this test found the second page (docs/SETUP.md) before the
    test existed. Current-state phrases plus a version literal on one line is
    the shape; historical entries name their date instead of these phrases,
    which is why RELEASE.md's dated CURRENT STATE records do not trip it."""

    PHRASES = re.compile(
        r"currently reads|currently running|development tree reads|"
        r"current tree reads|version in this checkout|this checkout is|"
        r"current development identity|tree itself currently reads",
        re.IGNORECASE)
    VERSION_LITERAL = re.compile(r"\b\d+\.\d+\.\d+(?:[.-]\w+)*\b")

    # The exact sentences that went stale in real life, byte for byte from
    # the pages they sat on. sbe note for the claim detector exemption list:
    # these are hostile fixtures, present tense on purpose.
    STALE_FIXTURES = (
        "development tree itself currently reads `2.0.0-rc.12.dev1`, a "
        "development identity rather than a tagged release",
        "The development tree itself currently reads `2.0.0-rc.12.dev1`",
    )

    def _offenders_in(self, text):
        hits = []
        for i, line in enumerate(text.split("\n"), 1):
            if self.PHRASES.search(line) and self.VERSION_LITERAL.search(line):
                hits.append("%d: %s" % (i, line.strip()[:90]))
        return hits

    def test_the_original_stale_sentences_are_caught(self):
        for fixture in self.STALE_FIXTURES:
            self.assertNotEqual(
                self._offenders_in(fixture), [],
                "the detector no longer catches the exact sentence that went "
                "stale in real life: %r" % fixture[:60])

    def test_no_active_page_types_an_identity_into_current_state_prose(self):
        offenders = []
        for rel in ACTIVE_DOCS:
            for hit in self._offenders_in(read(rel)):
                offenders.append("%s:%s" % (rel, hit))
        self.assertEqual(
            offenders, [],
            "an active page types a version identity into current-state "
            "prose (%s). Versions come from cat VERSION or "
            "bm_project_facts.py, never typed; a typed identity goes stale "
            "the day after it is written." % "; ".join(offenders))

    def test_no_active_page_describes_the_removed_rename_hazard_as_current(self):
        """The second 2026-08-07 class: docs/QUICKSTART.md explained a
        mid-run module rename that tools/test_all.py's own header says the
        P9 fix round removed. Present-tense rename claims are offenders;
        a line whose surrounding three lines say HISTORICAL, once, or
        removed is history telling the truth about itself."""
        pat = re.compile(r"rename[s]?\s+a\s+module\s+aside")
        # NOT a bare "once": the real stale sentence says "two at once can
        # corrupt each other", which this fixture proved would self-excuse.
        history = re.compile(r"HISTORICAL|once renamed|removed", re.IGNORECASE)
        offenders = []
        for rel in ACTIVE_DOCS:
            lines = read(rel).split("\n")
            for i, line in enumerate(lines):
                if pat.search(line):
                    around = "\n".join(lines[max(0, i - 2):i + 3])
                    if not history.search(around):
                        offenders.append("%s:%d" % (rel, i + 1))
        self.assertEqual(
            offenders, [],
            "an active page describes the removed mid-run module rename as "
            "current behavior (%s); tools/test_all.py's header records its "
            "removal in the P9 fix round." % "; ".join(offenders))
        stale = ("the suites rename a module aside mid-run, so two at once "
                 "can corrupt each other")
        self.assertTrue(
            pat.search(stale) and not history.search(stale),
            "the detector no longer catches the exact rename sentence that "
            "went stale in real life")


class TestBitbucketPipelinesRunsTheDocumentedGate(unittest.TestCase):
    """The Bitbucket CI leg's drift check, added 2026-08-17.

    THE ASYMMETRY THIS CLOSES. The GitHub workflow cannot drift from the
    local gate without being caught: tools/test_all.py's CI inventory check
    reads .github/workflows/tests.yml and REFUSES to run when a suite is in
    one and not the other, and CI runs that same file, so both sides are
    checked by one piece of code. bitbucket-pipelines.yml had nothing at
    all. It could have named a different command, a different session cap,
    or no gate whatsoever, and every test in this repository would have
    stayed green while the adopter team's merge-time enforcement quietly
    became something else.

    So the pipelines file is pinned to the command PROJECT.md documents,
    which is the same source of truth CLAUDE.md's key-commands section and
    every session's own gate run read. Not a copy of the command typed here:
    a comparison between two files this repository already keeps."""

    PIPELINES = "bitbucket-pipelines.yml"
    PROJECT = "PROJECT.md"
    CAP = re.compile(r"BROTHERMODE_SESSION_CAP=(\d+)")

    def _documented_gate(self):
        """The full gate command, read out of PROJECT.md's key-commands
        list. A missing line is a failure rather than a skip: this suite
        cannot check drift against a fact that stopped existing, and
        silently passing would be exactly the hole it was written for."""
        text = read(self.PROJECT)
        match = re.search(r"^- Full gate: `?(.+?)`?$", text, re.MULTILINE)
        self.assertIsNotNone(
            match,
            "PROJECT.md no longer states a `- Full gate:` command, so the "
            "Bitbucket pipeline has nothing to be checked against. Restore "
            "the line rather than deleting this test.")
        return match.group(1).strip()

    def _gate_invocations(self, text):
        """Every YAML sequence entry in the pipelines file that runs the
        gate, with the leading dash removed. Deliberately narrow: this
        parses the one construct the file uses (a list of shell strings)
        rather than pretending to be a YAML parser, and anything it cannot
        read simply does not count as an invocation, which fails closed."""
        found = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("-"):
                continue
            if "tools/test_all.py" not in stripped:
                continue
            found.append(re.sub(r"^-\s*", "", stripped).strip())
        return found

    def test_the_pipeline_runs_the_documented_full_gate_command(self):
        documented = self._documented_gate()
        found = self._gate_invocations(read(self.PIPELINES))
        self.assertTrue(
            found,
            "%s runs no command naming tools/test_all.py at all, so the "
            "Bitbucket leg is not running this project's gate. The two-host "
            "law says every host-facing feature ships both legs or names the "
            "missing one." % self.PIPELINES)
        for invocation in found:
            self.assertEqual(
                invocation, documented,
                "%s runs %r but PROJECT.md documents the full gate as %r. "
                "The adopter team's merge-time enforcement and this "
                "project's own gate must be the same command, or one of "
                "them is testing something nobody wrote down."
                % (self.PIPELINES, invocation, documented))

    def test_the_session_cap_value_matches_the_documented_one(self):
        """Pinned separately from the command string, because the cap is
        the half that can drift while still LOOKING right: 99 and 9 are one
        keystroke apart, and a pipeline capped at 1 would refuse to run the
        gate at all on a runner counting no transcripts."""
        documented = self._documented_gate()
        doc_cap = self.CAP.search(documented)
        self.assertIsNotNone(
            doc_cap, "PROJECT.md's full gate command no longer sets "
            "BROTHERMODE_SESSION_CAP, so there is no value to compare")
        for invocation in self._gate_invocations(read(self.PIPELINES)):
            found = self.CAP.search(invocation)
            self.assertIsNotNone(
                found, "%s runs the gate without BROTHERMODE_SESSION_CAP, "
                "which PROJECT.md's documented command sets" % self.PIPELINES)
            self.assertEqual(
                found.group(1), doc_cap.group(1),
                "%s caps sessions at %s but PROJECT.md documents %s"
                % (self.PIPELINES, found.group(1), doc_cap.group(1)))

    def test_calibrated_a_drifted_pipeline_is_caught(self):
        """The vacuous-pass guard. Both assertions above compare against a
        file on disk, so they would pass just as happily if the parser
        found nothing to compare. This drives the same two comparisons over
        a MUTATED copy of the real file and requires them to fail."""
        real = read(self.PIPELINES)
        documented = self._documented_gate()
        drifted = real.replace("BROTHERMODE_SESSION_CAP=99",
                               "BROTHERMODE_SESSION_CAP=1")
        self.assertNotEqual(drifted, real,
                            "the fixture changed nothing, so it proves "
                            "nothing about the checks above")
        invocations = self._gate_invocations(drifted)
        self.assertTrue(invocations)
        self.assertNotIn(documented, invocations,
                         "a pipeline whose session cap drifted still "
                         "compared equal to the documented command")
        for invocation in invocations:
            found = self.CAP.search(invocation)
            self.assertIsNotNone(found)
            self.assertNotEqual(found.group(1),
                                self.CAP.search(documented).group(1))

    def test_calibrated_a_pipeline_that_runs_no_gate_is_caught(self):
        """The other direction: a file that dropped the gate entirely must
        read as zero invocations, never as agreement."""
        gutted = "\n".join(
            line for line in read(self.PIPELINES).splitlines()
            if "tools/test_all.py" not in line)
        self.assertEqual(self._gate_invocations(gutted), [])


class TestHandWiringBlocksMatchInstaller(unittest.TestCase):
    """The copy-paste JSON blocks shipped five events for weeks while every
    prose check nearby stayed green, because no test ever parsed them: the
    fence test above looks for two substrings, the event test asks only that
    each event NAME appear somewhere on the page. This class parses each
    block with json.loads and compares it, group for group, against
    scripts/install.py's hook_groups(): same events, same matchers, same
    timeouts, same commands, modulo the documented `~` install target versus
    the installer's absolute one. A block that drops an event, a matcher
    group, or a timeout now fails by name instead of shipping a hand-wired
    install with detection switched off."""

    PAGES = (os.path.join("docs", "QUICKSTART.md"),
             os.path.join("docs", "SETUP.md"))
    DOC_TARGET = "~/.claude/skills/brothermode"
    # Safe-character absolute stand-in, so shlex.quote adds no quoting and
    # the only difference between the two shapes is the target string itself.
    ABS_TARGET = "/opt/brothermode-hand-wiring-check"

    @staticmethod
    def _json_blocks(text):
        return re.findall(r"```json\n(.*?)```", text, re.DOTALL)

    @staticmethod
    def _installer():
        spec = importlib.util.spec_from_file_location(
            "bm_install_for_docs_test",
            os.path.join(ROOT, "scripts", "install.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _normalized(groups):
        """Reduce a hooks mapping to the facts that must agree: per event, an
        ordered list of (matcher, [(command, timeout), ...]) with command
        whitespace collapsed, so a wrapped line or a trailing space in the
        documented block cannot manufacture a difference."""
        out = {}
        for event, entries in groups.items():
            out[event] = [
                {"matcher": g.get("matcher"),
                 "hooks": [{"command": " ".join(h.get("command", "").split()),
                            "timeout": h.get("timeout")}
                           for h in g.get("hooks", [])]}
                for g in entries]
        return out

    def test_each_hand_wiring_block_equals_the_installer_shape(self):
        expected = self._normalized(self._installer().hook_groups(
            self.ABS_TARGET))
        for rel in self.PAGES:
            wiring = None
            for block in self._json_blocks(read(rel)):
                try:
                    parsed = json.loads(block)
                except ValueError:  # sbe: allow-silent a non-JSON code block in the doc, not the one being searched for
                    continue
                if isinstance(parsed, dict) and "SessionStart" in str(
                        parsed.get("hooks", "")):
                    wiring = parsed["hooks"]
                    break
            self.assertIsNotNone(
                wiring,
                "%s: no parseable ```json block with a top-level \"hooks\" "
                "mapping that wires SessionStart; the hand-wiring block is "
                "missing or is invalid JSON" % rel)
            substituted = json.loads(
                json.dumps(wiring).replace(self.DOC_TARGET, self.ABS_TARGET))
            actual = self._normalized(substituted)
            self.assertEqual(
                sorted(actual), sorted(expected),
                "%s: the hand-wiring block wires events %s but "
                "scripts/install.py wires %s"
                % (rel, sorted(actual), sorted(expected)))
            for event in sorted(expected):
                self.assertEqual(
                    actual[event], expected[event],
                    "%s: the %s groups in the hand-wiring block do not match "
                    "scripts/install.py hook_groups(); the block is what a "
                    "reader copies, so the block is what has to be true"
                    % (rel, event))


class TestOneInstall(unittest.TestCase):
    """The public default install target is an IMMUTABLE tag, generated from
    one release fact (bm_project_facts.py's install_target_tag), never hand
    typed.

    THE DEFECT THIS CLASS GUARDS: before this fix, three of the four active
    onboarding pages (README.md:54, docs/QUICKSTART.md:15, docs/SETUP.md:19)
    told a reader to clone with no --branch at all, which lands on whatever
    commit the default branch happens to be at the moment of the clone, and
    that code then auto-installs hooks that run in every future session.
    docs/RELEASE.md was the only page that pinned a tag. `git clone
    https://github.com/khalilmaaouni/BrotherModeUp.git
    ~/.claude/skills/brothermode`, with no ref at all, was the reproduction.

    `test_the_plain_install_command_is_identical_everywhere` used to assert
    that unpinned line was identical across the three pages. That invariant
    is retired ON PURPOSE, not weakened: fixing the defect means no page may
    carry that line at all any more, which
    `test_no_active_page_clones_the_primary_skill_directory_unpinned` below
    now asserts directly, and the identical-everywhere claim moves to the
    PINNED command that replaces it."""

    CLONE = re.compile(r"^git clone .*BrotherModeUp\.git.*$", re.MULTILINE)

    def test_no_active_page_clones_the_primary_skill_directory_unpinned(self):
        offenders = []
        for rel in ACTIVE_DOCS:
            for line in self.CLONE.findall(read(rel)):
                if _clones_primary_dir(line) and "--branch" not in line:
                    offenders.append("%s: %s" % (rel, line.strip()))
        self.assertEqual(
            offenders, [],
            "an active page clones the primary skill directory on a moving "
            "ref, no tag pinned, which auto-installs hooks that then run in "
            "every future session with no signal anything changed: %s"
            % "; ".join(offenders))

    def test_the_primary_install_command_is_identical_on_every_install_page(self):
        """The one immutable install command, generated from
        install_target_tag, must be byte identical across README.md,
        docs/QUICKSTART.md and
        docs/SETUP.md, and must match what bm_project_facts.py generates: a
        page that hand types its own version of the pinned tag is exactly
        the drift this loop exists to make impossible."""
        seen = {}
        for rel in INSTALL_DOCS:
            for line in self.CLONE.findall(read(rel)):
                if _clones_primary_dir(line):
                    seen.setdefault(line.strip(), []).append(rel)
        self.assertEqual(
            len(seen), 1,
            "the install pages disagree about the pinned clone command: %s"
            % json.dumps(seen, indent=2, sort_keys=True))
        self.assertEqual(
            list(seen)[0], FACTS["install_command_pinned"],
            "the pinned clone command on the install pages does not match "
            "the one bm_project_facts.py generates; it was hand typed "
            "rather than copied from the tool")

    def test_the_pinned_install_uses_the_public_install_target_tag(self):
        """The pinned tag is install_target_tag, not release_tag: the public
        install target is the tag known to actually resolve in git, which is
        independent of whatever identity VERSION currently claims (a
        development identity has no release_tag at all)."""
        offenders = []
        for rel in INSTALL_DOCS + (os.path.join("docs", "RELEASE.md"),):
            text = read(rel)
            pinned = [l for l in self.CLONE.findall(text) if "--branch" in l
                     and _clones_primary_dir(l)]
            for line in pinned:
                tag = re.search(r"--branch\s+(\S+)", line).group(1)
                if tag != FACTS["install_target_tag"]:
                    offenders.append("%s pins %s" % (rel, tag))
        self.assertEqual(
            offenders, [],
            "a page pins a tag that disagrees with install_target_tag (%s): "
            "%s" % (FACTS["install_target_tag"], "; ".join(offenders)))

    def test_the_development_command_is_kept_separate_and_labeled(self):
        """Requirement 1's second half: a development command must exist on
        every install page, must target a DIFFERENT directory than the
        pinned one, and must be labeled as a moving target so the two can
        never be confused."""
        for rel in INSTALL_DOCS:
            text = read(rel)
            self.assertIn(
                FACTS["dev_skill_dir"], text,
                "%s has no separate development install target" % rel)
            dev_lines = [l for l in self.CLONE.findall(text)
                        if FACTS["dev_skill_dir"] in l]
            self.assertTrue(
                dev_lines,
                "%s: no clone line targets the development directory" % rel)
            for line in dev_lines:
                self.assertFalse(
                    _clones_primary_dir(line),
                    "%s: the development clone line also resolves as a "
                    "pinned-directory target, which is exactly the "
                    "confusion requirement 1 forbids" % rel)
            self.assertIn(
                "changes over time", text,
                "%s: the development command is not labeled as a moving "
                "target" % rel)

    def test_no_active_page_sends_an_installer_to_a_non_default_branch(self):
        offenders = []
        for rel in ACTIVE_DOCS:
            for m in re.finditer(r"git clone[^\n]*--branch\s+(\S+)", read(rel)):
                ref = m.group(1)
                if (ref != FACTS["install_target_tag"]
                        and ref != FACTS["default_branch"]):
                    offenders.append("%s: %s" % (rel, ref))
        self.assertEqual(offenders, [],
                         "a page clones a branch that is neither the default "
                         "branch (%s) nor the public install target tag (%s): "
                         "%s" % (FACTS["default_branch"],
                                 FACTS["install_target_tag"],
                                 "; ".join(offenders)))

    def test_release_md_states_the_live_install_target_tag(self):
        """C4: docs/RELEASE.md's opening 'Loop 0' paragraph once named a
        hardcoded snapshot of install_target_tag (v2.0.0-rc.9, dated
        2026-08-01) as if it were still current, and nothing caught it as
        PUBLIC_INSTALL_TAG moved on to v3.3.1. This pins the sentence that
        states the LIVE value to bm_project_facts.py's install_target_tag
        directly, so the line can never drift again."""
        text = read(os.path.join("docs", "RELEASE.md"))
        m = re.search(
            r"PUBLIC_INSTALL_TAG`? in `?tools/bm_project_facts\.py`?,?\s*"
            r"currently `([^`]+)`", text)
        self.assertIsNotNone(
            m,
            "docs/RELEASE.md no longer states the live PUBLIC_INSTALL_TAG "
            "value in the expected sentence shape")
        self.assertEqual(
            m.group(1), FACTS["install_target_tag"],
            "docs/RELEASE.md's live install-target sentence names %s, but "
            "bm_project_facts.py's PUBLIC_INSTALL_TAG is %s"
            % (m.group(1), FACTS["install_target_tag"]))


class TestPluginMarketplacePin(unittest.TestCase):
    """DEFECT A from the 2026-08-07 external review: the easiest install path
    (the marketplace add) tracked the repository's moving default branch,
    while only the pinned git-clone install was auditable. For code whose
    hooks run on every future session, the easiest path and the most
    auditable path should not be different ones. Anthropic's plugin
    marketplace format resolves an `owner/repo@ref` marketplace source to a
    fixed branch or tag on every add and every later refresh (the CLI
    reference for `claude plugin marketplace add`,
    https://code.claude.com/docs/en/plugin-marketplaces), which is the
    mechanism `docs/RELEASE.md` step 2b and the three install pages now use.

    Mirrors TestOneInstall's shape for the git-clone pin: one test that no
    active page adds the marketplace unpinned, one that the pinned line is
    byte identical everywhere it appears, and one, the requirement this
    class exists for, that FAILS the moment a page's pin disagrees with
    install_target_tag."""

    MARKETPLACE_ADD = re.compile(
        r"^claude plugin marketplace add khalilmaaouni/BrotherModeUp\S*$",
        re.MULTILINE)

    def test_no_active_page_adds_the_marketplace_unpinned(self):
        offenders = []
        for rel in ACTIVE_DOCS:
            for line in self.MARKETPLACE_ADD.findall(read(rel)):
                if "@" not in line:
                    offenders.append("%s: %s" % (rel, line.strip()))
        self.assertEqual(
            offenders, [],
            "an active page adds the marketplace with no ref pinned, which "
            "tracks the repository's moving default branch and auto-installs "
            "hooks that then run in every future session with no signal "
            "anything changed: %s" % "; ".join(offenders))

    def test_the_marketplace_add_line_is_identical_on_every_install_page(self):
        seen = {}
        for rel in INSTALL_DOCS:
            for line in self.MARKETPLACE_ADD.findall(read(rel)):
                seen.setdefault(line.strip(), []).append(rel)
        self.assertEqual(
            len(seen), 1,
            "the install pages disagree about the pinned marketplace add "
            "command: %s" % json.dumps(seen, indent=2, sort_keys=True))

    def test_the_marketplace_pin_matches_install_target_tag(self):
        """THE DEFECT A TEST: a pin that disagrees with install_target_tag
        (the same fact the pinned git-clone command is checked against) must
        fail here, not pass silently. install_target_tag, not VERSION, is
        deliberately the comparison: rule 5 of the version law pins public
        install instructions to the last tag known to actually resolve,
        independent of whatever identity VERSION carries mid-development."""
        offenders = []
        for rel in INSTALL_DOCS:
            for line in self.MARKETPLACE_ADD.findall(read(rel)):
                m = re.search(r"@(\S+)$", line)
                tag = m.group(1) if m else None
                if tag != FACTS["install_target_tag"]:
                    offenders.append("%s pins %r" % (rel, tag))
        self.assertEqual(
            offenders, [],
            "a page pins the marketplace add to a ref that disagrees with "
            "install_target_tag (%s): %s"
            % (FACTS["install_target_tag"], "; ".join(offenders)))

    def test_the_generated_plugin_command_is_on_every_install_page(self):
        """THE DEFECT THIS TEST ADDS A CHECK FOR, 2026-08-08: the two tests
        above hold the PAGES to install_target_tag and to each other, and the
        pinned git clone is held to what bm_project_facts.py generates, but
        nothing held the tool's own install_command_plugin to anything. It
        went through the v3 rename untouched and printed
        `claude plugin install brotherme@brotherme-marketplace` (a plugin id
        that no longer exists) while all three pages had moved, which is the
        worst direction for this drift to run: the tool is what a page or a
        script is supposed to copy from.

        Line by line rather than as one block, because docs/QUICKSTART.md
        deliberately splits the two commands into separate fences with prose
        between them."""
        offenders = []
        for line in FACTS["install_command_plugin"].split("\n"):
            for rel in INSTALL_DOCS:
                if line not in read(rel):
                    offenders.append("%s: %s" % (rel, line))
        self.assertEqual(
            offenders, [],
            "an install page does not carry the plugin install command "
            "bm_project_facts.py generates; one of the two was hand typed: %s"
            % "; ".join(offenders))

    def test_the_generated_plugin_command_names_the_declared_ids(self):
        """install_command_plugin is assembled from product.identity.json, so
        a half-finished rename that moved the register but not the manifests
        cannot leave this command naming a plugin id nothing ships. The
        register-to-manifest agreement itself is
        TestIdentityRegister's job; this asserts the generated command sits on
        the same side of it."""
        identity = json.loads(read(IDENTITY_JSON))
        self.assertIn(
            "claude plugin install %s@%s"
            % (identity["plugin_id"], identity["marketplace_id"]),
            FACTS["install_command_plugin"],
            "the generated plugin install command does not name the ids "
            "product.identity.json declares")
        self.assertIn(
            "@%s" % FACTS["install_target_tag"],
            FACTS["install_command_plugin"].split("\n")[0],
            "the generated marketplace add is not pinned to "
            "install_target_tag")

    def test_release_md_names_the_repin_step(self):
        """docs/RELEASE.md must carry the re-pin step as one of the numbered
        release steps, not only as a fact somewhere else on the page: a step
        a maintainer cannot find is not a step that gets followed."""
        text = read(os.path.join("docs", "RELEASE.md"))
        self.assertIn(
            "Re-pin the plugin marketplace install command", text,
            "docs/RELEASE.md no longer names an explicit re-pin step")
        self.assertIn(
            "PUBLIC_INSTALL_TAG", text,
            "the re-pin step does not name the constant a maintainer must "
            "change")
        self.assertIn(
            "tools/test_bm_docs.py", text,
            "the re-pin step does not name the check that catches a "
            "disagreeing pin")


def _git(*args):
    """Run git read-only against this repository. Returns (returncode,
    stdout, stderr) as text. Never raises: a git failure is a fact a test
    reads and reports, not a crash."""
    r = subprocess.run(["git"] + list(args), cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True)
    return r.returncode, r.stdout, r.stderr


def _git_bytes(*args):
    """Same as _git, but the raw bytes of stdout, never text-decoded. A
    checksum has to be computed over the exact bytes git holds; decoding
    through a text codec first (universal newlines, an encoding guess) would
    silently change a binary file's hash and prove nothing."""
    r = subprocess.run(["git"] + list(args), cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return r.returncode, r.stdout, r.stderr


def _tag_exists(tag):
    code, _out, _err = _git("rev-parse", "--verify", "--quiet",
                            "refs/tags/%s" % tag)
    return code == 0


class TestReleaseTruth(unittest.TestCase):
    """Loop 1, requirement 3: a release-truth test, extended for the
    release-closure program (Loop 0) to protect BOTH identities a tree can
    carry. Fable's attack prompt treats release identity as a supply-chain
    boundary; each check below is its OWN test method with a name that says
    exactly what it protects, so a failing run points at one broken release
    fact rather than at "release truth" in the abstract.

    A development identity (FACTS["is_development"]) claims no release tag
    at all: release_tag is None, and no tag named after the dev version may
    exist. That is what protects against the exact ambiguity rc.10 and
    rc.11 created: a release-cut commit that was tagged after the branch had
    already moved past it. The released-identity protections below (tag
    exists, tag points at the intended commit, checksum manifest matches the
    tagged tree) SKIP with a stated reason while the current identity is a
    development one, and re-arm automatically the moment VERSION next names
    a released version.

    Every check that needs the documented tag to exist in git SKIPS, with a
    stated reason, when it does not, rather than passing on an empty
    comparison. A test that goes green because it found nothing to check
    against is the exact defect class this project has already shipped:
    `v2.0.0-rc.1` was tagged, the branch moved fourteen commits past it with
    no signal, and nothing caught the two claiming the same identity while
    holding different code (docs/RELEASE.md, "v2.0.0-rc.1 is WITHDRAWN")."""

    def setUp(self):
        self.is_dev = FACTS["is_development"]
        self.tag = FACTS["release_tag"]
        self.tag_exists = _tag_exists(self.tag) if self.tag else False

    def _skip_unless_released(self):
        if self.is_dev:
            self.skipTest(
                "current identity (%s) is a development identity, not a "
                "released one; this check re-arms automatically once "
                "VERSION next names a released version" % FACTS["version"])

    # -- development identity protections --------------------------------

    def test_a_development_identity_claims_no_git_tag(self):
        """Protects: a development identity must never claim a git tag.
        release_tag is None for it, and no tag named after its own version
        may exist either; a later development version quietly growing a
        matching tag would recreate the exact two-trees ambiguity rc.10 and
        rc.11 are SUPERSEDED, NEVER TAGGED to avoid."""
        if not self.is_dev:
            self.skipTest("current identity (%s) is not a development "
                          "identity" % FACTS["version"])
        self.assertIsNone(
            self.tag, "a development identity must not carry a release_tag")
        claimed = "v" + FACTS["version"]
        self.assertFalse(
            _tag_exists(claimed),
            "a development tree must not have a git tag named after its "
            "own development version, but %s exists" % claimed)

    def test_the_public_install_target_tag_resolves_in_git(self):
        """Protects: install_target_tag, the tag every install page pins,
        actually resolves. This is a real check now, not a skip, because
        v2.0.0-rc.9 exists; it holds regardless of whether the current
        identity is a development one or a released one."""
        target = FACTS["install_target_tag"]
        self.assertTrue(
            _tag_exists(target),
            "install_target_tag %s does not exist in this repository"
            % target)

    def test_release_md_marks_rc10_and_rc11_superseded_never_tagged(self):
        """Protects: docs/RELEASE.md states, in the exact ratified phrase,
        that rc.10 and rc.11 are both superseded and will never be tagged
        (amendment A1: a late tag would recreate the two-trees ambiguity),
        so a reader cannot mistake either for a release still pending a
        tag."""
        text = read(os.path.join("docs", "RELEASE.md"))
        for candidate in ("2.0.0-rc.10", "2.0.0-rc.11"):
            idx = text.find(candidate)
            self.assertNotEqual(
                idx, -1, "docs/RELEASE.md never mentions %s" % candidate)
            window = text[idx:idx + 200]
            self.assertIn(
                "SUPERSEDED, NEVER TAGGED", window,
                "docs/RELEASE.md does not mark %s as SUPERSEDED, NEVER "
                "TAGGED near its mention" % candidate)

    def test_changelog_headings_mark_rc10_and_rc11_superseded_never_tagged(self):
        """Protects: the CHANGELOG section HEADINGS for rc.10 and rc.11 both
        carry (SUPERSEDED, NEVER TAGGED). The sibling test above pins
        docs/RELEASE.md only, and the CHANGELOG's rc.11 heading drifted
        unprotected for a day: found by the 2026-08-02 Loop 9 preliminary
        review (the second session's identity attacker, reproduced by hand
        before this pin landed). A heading is what a scanning reader trusts;
        prose lower down cannot repair a heading that presents a retired
        candidate as a release."""
        heading = re.compile(r"(?m)^## (2\.0\.0-rc\.1[01])\b(.*)$")
        text = read("CHANGELOG.md")
        matches = heading.findall(text)
        self.assertTrue(
            matches, "CHANGELOG.md has no rc.10 or rc.11 section headings")
        offenders = ["rc heading lacks the marker: ## %s%s" % (v, rest)
                     for v, rest in matches
                     if "(SUPERSEDED, NEVER TAGGED)" not in rest]
        self.assertEqual(
            offenders, [],
            "%s. Amendment A1: neither candidate was ever tagged and neither "
            "ever will be; the heading itself must say so." % "; ".join(offenders))

    def test_all_release_manifests_describe_one_identity(self):
        """Protects: VERSION, pyproject.toml (PEP 440 normalized),
        plugin.json and marketplace.json (both its occurrences) all name
        the same release identity. A manifest quietly left behind while the
        others move on is exactly how a two-trees ambiguity starts."""
        version = FACTS["version"]
        offenders = []

        pep440 = re.search(r'(?m)^version\s*=\s*"([^"]+)"',
                          read("pyproject.toml")).group(1)
        normalized = re.sub(r"(\d)rc(\d)", r"\1-rc.\2", pep440)
        if normalized != version:
            offenders.append("pyproject.toml: %s (normalized %s)"
                             % (pep440, normalized))

        plugin = json.loads(read(os.path.join(".claude-plugin",
                                              "plugin.json")))
        if plugin.get("version") != version:
            offenders.append("plugin.json: %r" % plugin.get("version"))

        marketplace = json.loads(read(os.path.join(".claude-plugin",
                                                    "marketplace.json")))
        market_version = marketplace.get("metadata", {}).get("version")
        if market_version != version:
            offenders.append("marketplace.json metadata.version: %r"
                             % market_version)
        for entry in marketplace.get("plugins", []):
            if entry.get("version") != version:
                offenders.append("marketplace.json plugins[].version: %r"
                                 % entry.get("version"))

        self.assertEqual(
            offenders, [],
            "manifest(s) disagree with VERSION (%s): %s"
            % (version, "; ".join(offenders)))

    # -- released identity protections, re-arm at the final release ------

    def test_the_documented_tag_exists_in_git(self):
        """Protects: a reader following the pinned install command gets a ref
        that actually resolves, not `fatal: Remote branch ... not found`."""
        self._skip_unless_released()
        if not self.tag_exists:
            self.skipTest(
                "tag %s does not exist in this repository yet; cutting it is "
                "a founder-gated step (docs/RELEASE.md, steps 5 to 7). "
                "Nothing to verify against until it exists." % self.tag)
        code, _out, err = _git("rev-parse", "--verify", "--quiet",
                              "refs/tags/%s" % self.tag)
        self.assertEqual(code, 0, "%s no longer resolves: %s" % (self.tag, err))

    def test_the_tag_points_at_the_intended_release_commit(self):
        """Protects: the tag and VERSION describe the SAME commit. 'Intended'
        is read from git's own history, not trusted by name: the commit that
        actually set VERSION to the current value, never a commit chosen by
        this test's own guess."""
        self._skip_unless_released()
        if not self.tag_exists:
            self.skipTest("tag %s does not exist yet; see the previous test"
                          % self.tag)
        code, out, err = _git("log", "-1", "--format=%H", "--", "VERSION")
        self.assertEqual(code, 0, err)
        intended = out.strip()
        self.assertTrue(intended, "VERSION has no commit history at all")
        code, tag_commit, err = _git("rev-list", "-n", "1", self.tag)
        self.assertEqual(code, 0, err)
        self.assertEqual(
            tag_commit.strip(), intended,
            "tag %s points at %s but the commit that last set VERSION to %s "
            "is %s; the tag and VERSION disagree about which commit is the "
            "release" % (self.tag, tag_commit.strip(), FACTS["version"],
                        intended))

    def test_the_primary_install_command_uses_the_tag_not_a_branch(self):
        """Protects: once released, the public default clone in README.md,
        docs/QUICKSTART.md and docs/SETUP.md pins --branch <tag>, never a
        moving branch name such as the default branch, and the tag it pins is
        install_target_tag.

        IT READ release_tag UNTIL 2026-09-03, which contradicted
        `TestOneInstall.test_the_pinned_install_uses_the_public_install
        _target_tag`, the test in this same file that owns the question of
        WHICH tag: that one states, and has always stated, that the
        pinned tag is install_target_tag and NOT release_tag, because the
        public install target is the tag known to actually resolve in git.
        The two agreed only while the two facts happened to be equal, which
        stopped being true when the product moved into the hub at the M6
        cutover: since then a version ships at the HUB's own tag (3.4.4 at
        v0.9.11, CHANGELOG.md names it in each heading), no tag named "v" plus
        VERSION is ever cut, and install_target_tag stays at the last tag that
        resolves. So this test now reads the same fact its sibling does, and
        keeps the half it uniquely owns: the pin exists, and it is not the
        moving default branch."""
        self._skip_unless_released()
        checked_any = False
        for rel in INSTALL_DOCS:
            text = read(rel)
            for line in re.findall(r"^git clone .*BrotherModeUp\.git.*$", text,
                                   re.MULTILINE):
                if not _clones_primary_dir(line):
                    continue
                checked_any = True
                self.assertIn(
                    "--branch", line,
                    "%s: the primary clone command has no --branch pin" % rel)
                tag = re.search(r"--branch\s+(\S+)", line).group(1)
                self.assertNotEqual(
                    tag, FACTS["default_branch"],
                    "%s: the primary install command pins the default "
                    "branch (%s), not a tag" % (rel, FACTS["default_branch"]))
                self.assertEqual(
                    tag, FACTS["install_target_tag"],
                    "%s: the primary install command pins %s, not the public "
                    "install target tag %s"
                    % (rel, tag, FACTS["install_target_tag"]))
        self.assertTrue(checked_any,
                        "no install page carries a clone command targeting "
                        "the primary skill directory at all")

    def test_release_md_does_not_claim_the_tag_is_absent(self):
        """Protects: docs/RELEASE.md's live "CURRENT STATE" claim about
        whether the tag exists must agree with git. Reproduced in this
        repository before this fix: the tag already existed (git tag -l,
        and it resolves on the configured remote), while docs/RELEASE.md's
        CURRENT STATE section still said "NO tag has been cut for it"."""
        self._skip_unless_released()
        text = read(os.path.join("docs", "RELEASE.md"))
        current_state = text.split("CURRENT STATE", 1)[-1].split("\n\n", 1)[0]
        if self.tag_exists:
            self.assertNotIn(
                "NO tag has been cut", current_state,
                "docs/RELEASE.md's live CURRENT STATE section claims no tag "
                "has been cut, but %s exists" % self.tag)
            self.assertNotIn(
                "will not resolve", current_state,
                "docs/RELEASE.md's live CURRENT STATE section still claims "
                "the pinned clone will not resolve, but the tag it pins now "
                "exists")

    def test_the_package_version_matches_version_file(self):
        """Protects: pyproject.toml's version and VERSION describe the same
        release. PEP 440 forbids the hyphenated pre-release form VERSION
        uses (2.0.0-rc.4), so pyproject.toml legitimately spells it
        differently (2.0.0rc4); this normalizes rather than demanding byte
        identity, because demanding byte identity would fail on a spelling
        difference that is not actually a defect. pyproject.toml is outside
        this loop's fence: a real disagreement here is reported, not fixed,
        by whoever owns that file."""
        text = read("pyproject.toml")
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        self.assertTrue(m, "pyproject.toml has no top level version field")
        pep440 = m.group(1)
        normalized = re.sub(r"(\d)rc(\d)", r"\1-rc.\2", pep440)
        self.assertEqual(
            normalized, FACTS["version"],
            "pyproject.toml version %s (normalized to %s) does not match "
            "VERSION (%s)" % (pep440, normalized, FACTS["version"]))

    def test_the_checksum_manifest_matches_the_tagged_tree(self):
        """Protects: CHECKSUMS.sha256 actually describes the bytes at the
        tag, in both directions (every manifested file matches, and no
        regular file at the tag is missing from the manifest), the same
        guarantee scripts/verify-install.sh makes for an installed copy.
        Read entirely through git plumbing against the tag; no checkout, no
        network."""
        self._skip_unless_released()
        if not self.tag_exists:
            self.skipTest("tag %s does not exist yet; nothing to check the "
                          "checksum manifest against" % self.tag)
        code, manifest_text, _err = _git("show",
                                         "%s:CHECKSUMS.sha256" % self.tag)
        if code != 0:
            self.skipTest("no CHECKSUMS.sha256 exists at %s" % self.tag)
        manifested = {}
        for line in manifest_text.splitlines():
            if not line.strip():
                continue
            digest, path = line.split("  ", 1)
            manifested[path] = digest
        code, tree_out, err = _git("ls-tree", "-r", self.tag)
        self.assertEqual(code, 0, err)
        tracked = {}
        for line in tree_out.splitlines():
            meta, path = line.split("\t", 1)
            mode, _kind, blob = meta.split()
            # CHECKSUMS.sha256 is tracked as a regular file but never lists
            # itself: a manifest line records a hash of the manifest's own
            # final bytes, which cannot be known until after that line is
            # written. scripts/checksums.sh excludes it for exactly this
            # reason, so a real manifest is one entry short of the tracked
            # set by design, not by defect.
            if mode in ("100644", "100755") and path != "CHECKSUMS.sha256":
                tracked[path] = blob
        missing = sorted(set(tracked) - set(manifested))
        extra = sorted(set(manifested) - set(tracked))
        self.assertEqual(
            missing, [],
            "regular file(s) at %s missing from CHECKSUMS.sha256: %s"
            % (self.tag, ", ".join(missing)))
        self.assertEqual(
            extra, [],
            "CHECKSUMS.sha256 lists path(s) not tracked as a regular file "
            "at %s: %s" % (self.tag, ", ".join(extra)))
        mismatched = []
        for path, blob in sorted(tracked.items()):
            code, content, err = _git_bytes("cat-file", "-p", blob)
            self.assertEqual(code, 0, err)
            actual = hashlib.sha256(content).hexdigest()
            if actual != manifested.get(path):
                mismatched.append(path)
        self.assertEqual(
            mismatched, [],
            "file(s) whose hash in CHECKSUMS.sha256 does not match their "
            "content at %s: %s" % (self.tag, ", ".join(mismatched)))


class TestVersionAndSchemaAgree(unittest.TestCase):
    def test_release_md_states_the_current_version(self):
        text = read(os.path.join("docs", "RELEASE.md"))
        self.assertIn(FACTS["version"], text,
                      "docs/RELEASE.md never names the current version %s"
                      % FACTS["version"])

    def test_no_active_page_claims_a_version_other_than_the_current_one(self):
        """The defect this suite was written after was docs/RELEASE.md pinning
        rc.2 while VERSION said rc.3. Asserting that RELEASE.md CONTAINS the
        current version does not catch it: a page can name the current version
        in one paragraph and claim a stale one in the next. This forbids the
        claim itself, on every active page.

        It matches current-state phrasings only ("you are installing X", "the
        current release is X"). A history section that names an older tag
        ("v2.0.0-rc.1 IS withdrawn") is dated evidence and stays legal, which
        is why the pattern is anchored to the claim and not to the number."""
        offenders = []
        for rel in ACTIVE_DOCS:
            for i, line in enumerate(read(rel).split("\n"), 1):
                for m in VERSION_CLAIM.finditer(line):
                    if m.group(1) != FACTS["version"]:
                        offenders.append("%s:%d %s" % (rel, i, m.group(0)))
        self.assertEqual(
            offenders, [],
            "an active page claims a version other than %s (%s). VERSION is the "
            "source; run python3 tools/bm_project_facts.py --field version, or "
            "delegate the sentence to it."
            % (FACTS["version"], "; ".join(offenders)))

    def test_no_active_page_states_a_different_schema_version(self):
        """Anchored to the claim, not the number, like the version check
        above: a page's own prose may not state a stale schema version, but a
        VERBATIM QUOTATION of what some older binary prints (text inside
        double quotes or a backtick code span) is dated evidence and stays
        legal. Found the hard way when L02 moved the schema to 14 and this
        check fired on KNOWN-LIMITS quoting an rc.9 console message."""
        offenders = []
        pat = re.compile(r"schema[_ ]version[^\d\n]{0,12}(\d+)", re.IGNORECASE)
        quoted = re.compile(r'"[^"\n]*"|`[^`\n]*`')
        for rel in ACTIVE_DOCS:
            for line in read(rel).split("\n"):
                bare = quoted.sub("", line)
                # A quotation that spans lines leaves its opening quote
                # unpaired on this line; everything after it is quoted text.
                for opener in ('"', "`"):
                    cut = bare.find(opener)
                    if cut != -1:
                        bare = bare[:cut]
                for m in pat.finditer(bare):
                    if int(m.group(1)) != FACTS["schema_version"]:
                        offenders.append("%s: %s" % (rel, m.group(0)))
        self.assertEqual(offenders, [],
                         "a page states a schema version other than %d: %s"
                         % (FACTS["schema_version"], "; ".join(offenders)))

    def test_no_active_page_states_a_different_python_floor(self):
        offenders = []
        pat = re.compile(r"Python\s+3\.(\d+)\s+or newer", re.IGNORECASE)
        for rel in ACTIVE_DOCS:
            for m in pat.finditer(read(rel)):
                if "3." + m.group(1) != FACTS["supported_python_floor"]:
                    offenders.append("%s: %s" % (rel, m.group(0)))
        self.assertEqual(offenders, [],
                         "a page states a Python floor other than %s: %s"
                         % (FACTS["supported_python_floor"], "; ".join(offenders)))


class TestHistoricalDocumentsSaySo(unittest.TestCase):
    def test_every_dated_document_declares_its_status_at_the_top(self):
        """A dated handover that does not say what it is reads as current state
        to anyone who lands on it, which is how a two-day-old branch name and a
        stale test count get treated as today's truth. Recursive on purpose:
        README links into docs/superpowers/specs/, where a spec opens with a DO
        NOT PUBLISH verdict that was resolved days ago.

        TWO legal declarations, not one (2026-07-30). The original rule demanded
        the HISTORICAL marker on every dated page, which left the NEWEST spec
        with no honest way to pass: commit 91d48d8 landed the current gate-pack
        spec and this suite went red, because the only way to satisfy it was to
        call a live document historical. A dated page may now instead declare
        `Status: CURRENT`, which is the true statement in that case. What stays
        forbidden, and is the whole point, is a dated page that declares
        nothing."""
        offenders = []
        for rel in dated_docs():
            head = "\n".join(read(rel).split("\n")[:25])
            if not _is_marked_historical(head) and not CURRENT_STATUS.search(head):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            "dated document(s) that declare no status in the first 25 lines: "
            "%s. Either mark it HISTORICAL with a superseded-by pointer, or "
            "state `Status: CURRENT` if it really is current."
            % ", ".join(offenders))

    def test_the_marker_carries_a_superseded_by_pointer(self):
        offenders = []
        for rel in dated_docs():
            head = "\n".join(read(rel).split("\n")[:25])
            if _is_marked_historical(head) and "uperseded by" not in head:
                offenders.append(rel)
        self.assertEqual(offenders, [],
                         "marked historical but names nothing to read instead: %s"
                         % ", ".join(offenders))


class TestHistoricalMarkerIsAnchored(unittest.TestCase):
    """The marker guard used to be a bare substring search over a page's head
    (`"HISTORICAL" in head`), so ANY mention of the word anywhere in the first
    25 lines tripped it, superseded-by pointer or not. Reproduced while
    writing docs/HANDOVER-2026-07-30.md: a sentence merely describing this
    convention failed the suite. This exercises the two real checks above
    against synthetic pages in a throwaway directory, so the assertion is
    proven independent of whatever this repository's own docs/ currently
    contains."""

    def _check(self, root):
        """The same two predicates test_bm_docs.py runs, against a provided
        root, so this proves the FIX rather than just the helper function."""
        status_offenders, pointer_offenders = [], []
        for dirpath, _dirnames, filenames in os.walk(os.path.join(root, "docs")):
            for name in sorted(filenames):
                if not DATED_NAME.search(name):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                with io.open(os.path.join(root, rel), encoding="utf-8") as fh:
                    head = "\n".join(fh.read().split("\n")[:25])
                if not _is_marked_historical(head) and not CURRENT_STATUS.search(head):
                    status_offenders.append(rel)
                if _is_marked_historical(head) and "uperseded by" not in head:
                    pointer_offenders.append(rel)
        return status_offenders, pointer_offenders

    def _write(self, root, name, text):
        os.makedirs(os.path.join(root, "docs"), exist_ok=True)
        with io.open(os.path.join(root, "docs", name), "w",
                     encoding="utf-8") as fh:
            fh.write(text)

    def test_merely_mentioning_the_marker_word_in_prose_does_not_trip_it(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(
                d, "2026-07-30-example.md",
                "# Example\n\nStatus: CURRENT as of 2026-07-30.\n\n"
                "This page describes how the doc-consistency suite enforces "
                "a HISTORICAL marker on every dated page under docs/.\n")
            status, pointer = self._check(d)
            self.assertEqual(status, [], "a mere mention needs no extra status")
            self.assertEqual(
                pointer, [],
                "a mere mention of the word must not demand a "
                "superseded-by pointer; the old substring check demanded one")

    def test_a_page_that_genuinely_declares_itself_historical_still_trips(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(
                d, "2026-07-01-old.md",
                "# Old page\n\n**HISTORICAL DOCUMENT, dated 2026-07-01. Do "
                "not read as current. Superseded by README.md.**\n")
            status, pointer = self._check(d)
            self.assertEqual(status, [], "marker plus pointer must pass")
            self.assertEqual(pointer, [], "the pointer is present here")

    def test_declaring_historical_with_no_pointer_is_still_caught(self):
        """The guard this fix must not weaken: a page that really is
        historical and gives no superseded-by pointer must still fail."""
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "2026-07-01-old.md",
                       "# Old page\n\n**HISTORICAL DOCUMENT, dated "
                       "2026-07-01.**\n")
            _status, pointer = self._check(d)
            self.assertEqual(
                pointer, [os.path.join("docs", "2026-07-01-old.md")],
                "declaring historical with no pointer must still be caught")


class TestGatePacks(unittest.TestCase):
    """Phase A of docs/superpowers/specs/2026-07-30-documentation-and-gate-packs
    -design.md, section 4.4. Extends this suite rather than adding one, per
    section 8: test_all.py refuses an unregistered suite.

    Every pack here is generated by the real CLI against a real store in a
    throwaway directory. Nothing touches this repository's own files.
    """

    PACKS = os.path.join(HERE, "bm_packs.py")
    CITED = "app/pay.py"
    # The cited module, written into the fixture so a test can MOVE a line in it
    # and prove that generation fails loudly (I11).
    CITED_SOURCE = (
        "\"\"\"a tiny payments module, for the fixture\"\"\"\n"
        "\n"
        "\n"
        "def charge(amount):\n"
        "    \"\"\"take the money once\"\"\"\n"
        "    return amount\n"
        "\n"
        "\n"
        "def refund(amount):\n"
        "    return -amount\n")

    def _bs(self):
        spec = importlib.util.spec_from_file_location(
            "bm_store_for_pack_tests", os.path.join(HERE, "bm_store.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _write(self, root, rel, text):
        full = os.path.join(root, *rel.split("/"))
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def _project(self, d, with_alert=False):
        """A real store, a real work record, a real pending candidate, plus a
        caller, a test and a document so the dependency search has something
        true to find."""
        bs = self._bs()
        self._write(d, self.CITED, self.CITED_SOURCE)
        self._write(d, "app/checkout.py",
                    "from app import pay\n\n\ndef buy(n):\n    return pay.charge(n)\n")
        self._write(d, "app/test_pay.py",
                    "def test_charge():\n    from app.pay import charge\n"
                    "    assert charge(1) == 1\n")
        self._write(d, "README.md", "# demo\n\nThe money path lives in app/pay.py.\n")
        store = bs.Store(d)
        try:
            rec = store.claim("payments", "persistent", objective="build payments",
                              files=[self.CITED], session_id="sessA")
            store.decide(rec.lifecycle_uuid, rec.version, "provider",
                         "we chose the boring provider on purpose")
            cand = store.capture_learning_candidate(
                "manual", trigger="when touching the money path",
                action="always run the payment tests before committing",
                because="we shipped a double charge once", scope_type="project",
                scope_key="demo", record_uuid=rec.lifecycle_uuid)
            if with_alert:
                store.add_note(kind="alert", severity="critical",
                               body="the retry path double charges",
                               author="Dana, backend", author_kind="human",
                               anchor_type="file", anchor_key=self.CITED,
                               anchor_line=4)
        finally:
            store.close()
        return cand["candidate_uuid"][:8]

    def _run(self, d, *args):
        return subprocess.run([sys.executable, self.PACKS] + list(args),
                              cwd=d, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def _pack_text(self, d):
        directory = os.path.join(d, "Documentation", "30-decisions")
        names = sorted(f for f in os.listdir(directory) if f.endswith(".md"))
        self.assertEqual(len(names), 1, names)
        path = os.path.join(directory, names[0])
        with io.open(path, encoding="utf-8") as fh:
            return path, fh.read()

    def _generate(self, d, cid, *extra):
        r = self._run(d, "pack", cid, "--cite", "%s:4-6" % self.CITED, *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    # -- 4.4, first bullet -------------------------------------------------

    def test_a_pack_carries_all_eight_sections_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            _path, text = self._pack_text(d)
            positions = []
            for i, title in enumerate(_PACK_SECTIONS, 1):
                heading = "## %d. %s" % (i, title)
                self.assertIn(heading, text, "missing section %s" % heading)
                positions.append(text.index(heading))
            self.assertEqual(positions, sorted(positions),
                             "the eight sections are out of order")

    def test_every_code_citation_quotes_the_lines_it_claims(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            _path, text = self._pack_text(d)
            source = self.CITED_SOURCE.split("\n")
            for n in (4, 5, 6):
                self.assertIn("%d  %s" % (n, source[n - 1]), text,
                              "line %d is not quoted as it is on disk" % n)
            self.assertIn("app/pay.py` lines 4 to 6", text)

    def test_the_dependency_map_is_discovered_not_asserted(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            _path, text = self._pack_text(d)
            self.assertIn("app/checkout.py", text, "the caller was not discovered")
            self.assertIn("app/test_pay.py", text, "the test was not discovered")
            self.assertIn("README.md", text, "the document was not discovered")
            self.assertIn("charge", text,
                          "the symbol defined in the cited lines was not searched")

    def test_the_diagram_is_a_mermaid_flowchart_of_the_affected_path(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            _path, text = self._pack_text(d)
            self.assertIn("```mermaid", text)
            self.assertIn("flowchart TD", text)
            self.assertIn("app/checkout.py", text.split("```mermaid")[1])

    def test_what_the_store_knows_comes_out_of_the_store(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            _path, text = self._pack_text(d)
            self.assertIn("boring provider", text,
                          "a prior recorded decision is missing from section 6")

    def test_regeneration_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            _path, first = self._pack_text(d)
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            _path, second = self._pack_text(d)
            self.assertEqual(first, second,
                             "regeneration churns the file, so every review "
                             "would show a diff that means nothing")

    # -- 4.4, second bullet: I11 calibrated ------------------------------

    def test_calibrated_moving_a_cited_line_fails_generation_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            _path, before = self._pack_text(d)
            # REINJECT: one inserted line, which is exactly how a citation goes
            # stale in real life.
            self._write(d, self.CITED, "# a new first line\n" + self.CITED_SOURCE)
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("citation-moved", r.stderr)
            self.assertIn("def charge(amount):", r.stderr,
                          "the refusal must name the anchor it lost")
            self.assertIn("--recite", r.stderr,
                          "the refusal must name the remedy")
            _path, after = self._pack_text(d)
            self.assertEqual(before, after,
                             "a refused generation must not touch the pack")
            # RESTORE: green again, and the same document.
            self._write(d, self.CITED, self.CITED_SOURCE)
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_body_change_under_a_stable_anchor_is_also_reported(self):
        """The half a line-number check alone would miss: the anchor still
        matches, and the code inside the excerpt is not what was reviewed."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            self._write(d, self.CITED,
                        self.CITED_SOURCE.replace("    return amount",
                                                  "    return amount * 2"))
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("citation-changed", r.stderr)

    def test_a_citation_past_the_end_of_the_file_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            r = self._run(d, "pack", cid, "--cite", "%s:400-410" % self.CITED)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("citation-out-of-range", r.stderr)

    def test_an_anchor_typed_from_memory_fails_on_the_first_generation(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            r = self._run(d, "pack", cid, "--cite",
                          "%s:4-6@def settle(amount):" % self.CITED)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("citation-moved", r.stderr)

    # -- 4.4, third bullet: alerts with teeth --------------------------

    def test_an_unresolved_critical_alert_is_named_in_the_risks_section(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d, with_alert=True)
            r = self._generate(d, cid)
            self.assertIn("UNRESOLVED CRITICAL ALERT", r.stdout)
            _path, text = self._pack_text(d)
            self.assertIn("REFUSE the approval", text)
            self.assertIn("Dana, backend", text)
            self.assertIn("the retry path double charges", text)

    def test_stakes_generates_nothing_and_names_the_path_a_pack_would_take(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d, with_alert=True)
            r = self._run(d, "stakes", cid)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Documentation/30-decisions/D-1-", r.stdout)
            self.assertIn("UNRESOLVED CRITICAL ALERT", r.stdout)
            self.assertFalse(
                os.path.exists(os.path.join(d, "Documentation")),
                "stakes must generate nothing: the pack is on demand")

    def test_a_recorded_review_lands_in_the_store_and_then_in_the_pack(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            r = self._run(d, "review", cid, "--by", "Dana, backend",
                          "--verdict", "concerns", "--notes",
                          "read the guard and the fingerprint",
                          "--residual", "no test for the racing case")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            bs = self._bs()
            store = bs.Store(d)
            try:
                rows = store.list_notes(kinds=("review",))
            finally:
                store.close()
            self.assertEqual(len(rows), 1, "the review is not in the store")
            self._generate(d, cid)
            _path, text = self._pack_text(d)
            self.assertIn("Recorded reviews: 1", text)
            self.assertIn("no test for the racing case", text)

    def test_a_review_with_no_name_or_no_notes_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            for args in (("--verdict", "approve", "--notes", "looks fine"),
                         ("--by", "Dana", "--verdict", "approve"),
                         ("--by", "Dana", "--verdict", "maybe",
                          "--notes", "looks fine")):
                r = self._run(d, "review", cid, *args)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    # -- I10 calibrated: human text survives ---------------------------

    HUMAN = "Dana: I still think the retry path is the real risk here."

    def _plant_human(self, path):
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        marker = "<!-- bm-human:begin -->\n<!-- bm-human:end -->"
        self.assertIn(marker, text, "the pack has no empty human block to fill")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(
                marker,
                "<!-- bm-human:begin -->\n%s\n<!-- bm-human:end -->" % self.HUMAN,
                1))

    def test_regeneration_preserves_a_planted_human_block_verbatim(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            path, _text = self._pack_text(d)
            self._plant_human(path)
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            _path, text = self._pack_text(d)
            self.assertIn(self.HUMAN, text,
                          "regeneration destroyed human text, which I10 forbids")
            self.assertIn("1 human block(s) preserved", r.stdout)

    def test_calibrated_a_destructive_generator_loses_the_human_block(self):
        """The reinjection that proves the test above is load bearing. A
        generator that ignores the existing file's human blocks is exactly the
        defect I10 names, and this is what it looks like: the planted paragraph
        is gone from the regenerated document."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            path, _text = self._pack_text(d)
            self._plant_human(path)
            spec = importlib.util.spec_from_file_location(
                "bm_packs_destructive", self.PACKS)
            bp = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bp)
            original = bp.read_existing

            def _destructive(p):
                out = original(p)
                out["human"] = []
                return out

            bp.read_existing = _destructive
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bp.main(["pack", cid]), 0)
            finally:
                os.chdir(cwd)
                bp.read_existing = original
            _path, text = self._pack_text(d)
            self.assertNotIn(
                self.HUMAN, text,
                "the destructive variant did NOT lose the human block, so the "
                "preservation test above proves nothing")

    # -- FIX ROUND: the two ways generation still destroyed or obeyed
    # -- human text -----------------------------------------------------

    # Ordinary review prose that the SECRET SCRUBBER rewrites. This is not a
    # credential; it is how an engineer writes a handover note, and
    # bm_telemetry.redact turns "password: ask" into "[REDACTED] ".
    REDACTABLE_HUMAN = ("Dana: the DB password: ask Sam before you touch the "
                        "migration.")

    def _plant_human_text(self, path, body):
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        marker = "<!-- bm-human:begin -->\n<!-- bm-human:end -->"
        self.assertIn(marker, text, "the pack has no empty human block to fill")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(
                marker,
                "<!-- bm-human:begin -->\n%s\n<!-- bm-human:end -->" % body, 1))

    def test_regeneration_keeps_human_prose_the_redactor_would_rewrite(self):
        """THE DEFECT THIS FIXES. The pack is written through the file funnel,
        which ran the secret scrubber over the WHOLE document including the
        reinjected human blocks. The scrubber is pattern based, so a reviewer's
        sentence came back as 'the DB [REDACTED] Sam', with no warning, no copy
        anywhere, and '1 human block(s) preserved' on stdout. Convergent after
        one pass, so a later diff showed nothing."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            path, _text = self._pack_text(d)
            self._plant_human_text(path, self.REDACTABLE_HUMAN)
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            _p, text = self._pack_text(d)
            self.assertIn(self.REDACTABLE_HUMAN, text,
                          "the funnel rewrote human prose, which I10 forbids")
            self.assertNotIn("REDACTED", text)
            self.assertIn("KEPT VERBATIM AND WORTH READING", r.stdout,
                          "preserving it silently is not enough: the founder has "
                          "to be told what it looks like")

    def test_calibrated_a_funnel_that_redacts_everything_eats_the_paragraph(self):
        """The reinjection for the test above: put whole-text redaction back and
        the reviewer's sentence is destroyed on disk."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            path, _text = self._pack_text(d)
            self._plant_human_text(path, self.REDACTABLE_HUMAN)
            bs = self._bs()
            spec = importlib.util.spec_from_file_location(
                "bm_packs_whole_text_redact", self.PACKS)
            bp = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bp)
            original = bp.bs._redact_outside_human_blocks
            bp.bs._redact_outside_human_blocks = (
                lambda text: bp.bs.redact_text(text or ""))
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bp.main(["pack", cid]), 0)
            finally:
                os.chdir(cwd)
                bp.bs._redact_outside_human_blocks = original
            _p, text = self._pack_text(d)
            self.assertNotIn(
                self.REDACTABLE_HUMAN, text,
                "the reinjected whole-text redaction did NOT eat the paragraph, "
                "so the test above proves nothing")
            self.assertIn("REDACTED", text)
            self.assertTrue(bs.human_block_secret_hits(text) is not None)

    CITE_IN_PROSE = ('<!-- bm-cite: path=README.md lines=1-1 sha256=%s '
                     'anchor="# demo" -->' % ("0" * 64))

    def test_a_citation_pasted_into_a_human_block_is_prose_not_configuration(self):
        """A reviewer quoting an excerpt header in the block the file TELLS them
        to write in used to add a citation to the pack. A non-resolving one
        wedged every future regeneration at exit 2, and the remedy the refusal
        printed did not work, so the only way out was editing the human block
        I10 says must be preserved. Reviewers quote code; that has to be safe."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            path, before = self._pack_text(d)
            citations_before = before.count("<!-- bm-cite:")
            self._plant_human_text(
                path, "%s\n%s" % (self.CITE_IN_PROSE, self.HUMAN))
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 0,
                             "prose wedged generation: %s" % (r.stdout + r.stderr))
            _p, after = self._pack_text(d)
            self.assertIn(self.HUMAN, after)
            self.assertIn(self.CITE_IN_PROSE, after,
                          "the pasted line is human text and stays verbatim")
            self.assertEqual(after.count("<!-- bm-cite:"),
                             citations_before + 1,
                             "a citation was adopted out of human prose")
            self.assertNotIn("README.md` lines 1 to 1", after,
                             "human prose produced a generated code excerpt")
            r2 = self._run(d, "pack", cid)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            _p, again = self._pack_text(d)
            self.assertEqual(after, again, "regeneration is no longer idempotent")

    def test_calibrated_the_old_whole_file_scan_adopts_the_pasted_citation(self):
        """The reinjection: scan every line for a citation, as read_existing used
        to, and the pasted prose wedges the pack at exit 2."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            path, _text = self._pack_text(d)
            self._plant_human_text(
                path, "%s\n%s" % (self.CITE_IN_PROSE, self.HUMAN))
            spec = importlib.util.spec_from_file_location(
                "bm_packs_whole_file_cite_scan", self.PACKS)
            bp = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bp)
            original = bp.read_existing

            def _whole_file_scan(p):
                out = original(p)
                with io.open(p, encoding="utf-8") as fh:
                    for line in fh.read().split("\n"):
                        m = bp._CITE_RE.match(line.strip())
                        if m:
                            out["cites"].append({
                                "path": m.group("path"),
                                "start": int(m.group("start")),
                                "end": int(m.group("end")),
                                "sha": m.group("sha"),
                                "anchor": bp._unquote_anchor(m.group("anchor"))})
                return out

            bp.read_existing = _whole_file_scan
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    with contextlib.redirect_stderr(io.StringIO()) as err:
                        code = bp.main(["pack", cid])
            finally:
                os.chdir(cwd)
                bp.read_existing = original
            self.assertEqual(code, 2,
                             "the reinjected whole-file scan did NOT adopt the "
                             "pasted citation, so the test above proves nothing")
            self.assertIn("refused (citation-", err.getvalue())

    def test_a_stray_begin_marker_inside_a_block_keeps_the_paragraph(self):
        """The old marker scan reset its buffer on a second begin marker, so
        everything written before it was dropped: a human block eaten by a line
        the human typed."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            path, _text = self._pack_text(d)
            self._plant_human_text(
                path, "%s\n<!-- bm-human:begin -->\nDana, still here." % self.HUMAN)
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            _p, text = self._pack_text(d)
            self.assertIn(self.HUMAN, text,
                          "a stray begin marker ate the paragraph above it")
            self.assertIn("Dana, still here.", text)

    def test_the_recite_remedy_the_refusal_prints_actually_works(self):
        """I11 promises a loud refusal with a stated remedy. The remedy has to
        work: merge_citations indexed recorded rows by key but appended every
        one, and the recite loop replaced the first row matching the PATH and
        stopped, so a pack citing two ranges of one file could not be recited at
        all."""
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d)
            self._generate(d, cid)
            r = self._run(d, "pack", cid, "--cite", "%s:9-10" % self.CITED)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            source = os.path.join(d, *self.CITED.split("/"))
            with io.open(source, encoding="utf-8") as fh:
                moved = fh.read().replace("    return -amount",
                                          "    return 0 - amount")
            with io.open(source, "w", encoding="utf-8") as fh:
                fh.write(moved)
            r = self._run(d, "pack", cid)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("--recite", r.stderr)
            remedy = r.stderr.split("--recite ", 1)[1].split(" to record")[0]
            r = self._run(d, "pack", cid, "--recite", remedy)
            self.assertEqual(r.returncode, 0,
                             "the printed remedy did not work: %s"
                             % (r.stdout + r.stderr))
            _p, text = self._pack_text(d)
            self.assertIn("lines 4 to 6", text,
                          "reciting one range dropped the other citation")
            self.assertIn("lines 9 to 10", text)

    def test_two_recorded_rows_for_one_range_collapse_to_one(self):
        """The unit-level statement of the same defect, on the function itself."""
        spec = importlib.util.spec_from_file_location(
            "bm_packs_merge", self.PACKS)
        bp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bp)
        twin = {"path": "a.py", "start": 1, "end": 1, "sha": "0" * 64,
                "anchor": "x"}
        merged = bp.merge_citations([dict(twin), dict(twin)], [], [])
        self.assertEqual(len(merged), 1)
        recited = bp.merge_citations(
            [dict(twin), dict(twin)], [],
            [{"path": "a.py", "start": 1, "end": 1, "sha": "", "anchor": "x"}])
        self.assertEqual([c["sha"] for c in recited], [""],
                         "a recite left a stale twin behind, which is what "
                         "wedged the pack forever")

    def test_the_generated_pack_carries_no_em_or_en_dash(self):
        with tempfile.TemporaryDirectory() as d:
            cid = self._project(d, with_alert=True)
            self._generate(d, cid)
            _path, text = self._pack_text(d)
            for i, line in enumerate(text.split("\n"), 1):
                for label, ch in (("en", chr(0x2013)), ("em", chr(0x2014))):
                    self.assertNotIn(ch, line,
                                     "%s dash on line %d of the pack" % (label, i))


class TestDocumentationEngine(unittest.TestCase):
    """Phase B of docs/superpowers/specs/2026-07-30-documentation-and-gate-packs
    -design.md, section 5.7. Extends this suite rather than adding one, per
    section 8: test_all.py refuses an unregistered suite.

    Every folder here is generated by the real CLI against a real store in a
    throwaway directory. Nothing touches this repository's own files.
    """

    DOCS = os.path.join(HERE, "bm_docs.py")
    EXPORT = os.path.join(HERE, "bm_docs_export.py")

    # -- fixtures ----------------------------------------------------------

    def _mod(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _bs(self):
        return self._mod("bm_store_for_docs_tests", os.path.join(HERE, "bm_store.py"))

    def _bd(self, name="bm_docs_for_docs_tests"):
        return self._mod(name, self.DOCS)

    def _write(self, root, rel, text):
        full = os.path.join(root, *rel.split("/"))
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def _project(self, d, records=1, candidates=0, risk=False, second_owner=False):
        """A real store, real source files, and as much recorded work as the
        caller asked for. Every row goes in through bm_store, which is the only
        writer, exactly as a founder's would."""
        bs = self._bs()
        self._write(d, "app/pay.py",
                    '"""the money path"""\nimport json\n\n\ndef charge(n):\n'
                    "    return n\n")
        self._write(d, "app/checkout.py",
                    '"""checkout"""\nfrom app import pay\n\n\ndef buy(n):\n'
                    "    return pay.charge(n)\n")
        self._write(d, "app/test_pay.py",
                    "def test_charge():\n    from app.pay import charge\n"
                    "    assert charge(1) == 1\n")
        self._write(d, "tools/test_all.py", "# the gate\n")
        store = bs.Store(d)
        try:
            first = store.claim("payments", "persistent",
                                objective="build the money path",
                                files=["app/pay.py"], owner="Dana",
                                session_id="sessA")
            store.decide(first.lifecycle_uuid, first.version, "provider",
                         "we chose the boring provider on purpose")
            if records > 1:
                # The store REFUSES a close with no evidence, which is the
                # point of it, so the fixture supplies some rather than
                # working around the guard.
                live = store.get(first.lifecycle_uuid)
                store.transition(first.lifecycle_uuid, live.version,
                                 "complete", session_id="sessA",
                                 evidence="the payment tests passed")
                second = store.claim(
                    "refunds", "persistent", objective="build refunds",
                    files=["app/pay.py", "app/checkout.py"],
                    owner="Sam" if second_owner else "Dana", session_id="sessB")
                store.checkpoint(second.lifecycle_uuid, second.version,
                                 "wire the webhook", blockers="waiting on keys")
            for i in range(candidates):
                store.capture_learning_candidate(
                    "manual", trigger="when touching the money path %d" % i,
                    action="always run the payment tests before committing %d" % i,
                    because="we shipped a double charge once", scope_type="project",
                    scope_key="demo", record_uuid=first.lifecycle_uuid)
            if risk:
                store.add_note(kind="risk", severity="critical",
                               body="the retry path double charges",
                               author="Dana, backend", author_kind="human",
                               anchor_type="file", anchor_key="app/pay.py",
                               anchor_line=5)
        finally:
            store.close()
        return d

    def _run(self, d, *args):
        return subprocess.run([sys.executable, self.DOCS] + list(args), cwd=d,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def _generate(self, d, *extra):
        r = self._run(d, "generate", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def _read_doc(self, d, rel):
        with io.open(os.path.join(d, "Documentation", *rel.split("/")),
                     encoding="utf-8") as fh:
            return fh.read()

    def _tree(self, d):
        """Every generated file with its bytes, for a byte-identity comparison."""
        out = {}
        base = os.path.join(d, "Documentation")
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(dirnames)
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                with io.open(full, "rb") as fh:
                    out[os.path.relpath(full, base)] = fh.read()
        return out

    # -- 5.1 layout --------------------------------------------------------

    def test_the_layout_is_the_numbered_one_the_spec_names(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            bd = self._bd()
            for rel, _tier, _what in bd.FILES:
                self.assertTrue(
                    os.path.isfile(os.path.join(d, "Documentation",
                                                *rel.split("/"))),
                    "tier 3 did not write %s" % rel)
            dirs = sorted(x for x in os.listdir(os.path.join(d, "Documentation"))
                          if os.path.isdir(os.path.join(d, "Documentation", x)))
            self.assertEqual(
                ["10-business", "20-technical", "30-decisions", "40-handover",
                 "90-generated"], dirs)

    def test_reading_order_follows_the_numbers_not_the_tier(self):
        """The numbered directories exist so a stranger reads top to bottom. A
        list sorted by tier would send them 00, 30, 40, then back to 10, which
        is exactly what the numbering is for. Within one directory the order is
        curated (architecture before its data model before its diagrams) and
        START-HERE prints it, so only the directory numbers are asserted."""
        bd = self._bd()
        numbers = []
        for rel, _tier, _what in bd.FILES:
            head = rel.split("/")[0]
            numbers.append(int(head[:2]))
        self.assertEqual(numbers, sorted(numbers),
                         "FILES leaves and re-enters a numbered directory: %s"
                         % [rel for rel, _t, _w in bd.FILES])

    # -- 5.2 projection ----------------------------------------------------

    def test_the_work_breakdown_is_projected_from_the_records(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2)
            self._generate(d, "--tier", "3")
            wbs = self._read_doc(d, "10-business/WBS.md")
            self.assertIn("payments", wbs)
            self.assertIn("refunds", wbs)
            self.assertIn("build the money path", wbs)
            self.assertIn("we chose the boring provider on purpose", wbs)
            self.assertIn("wire the webhook", wbs)

    def test_the_schedule_carries_a_gantt_and_a_derived_dependency(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2)
            self._generate(d, "--tier", "3")
            sched = self._read_doc(d, "10-business/SCHEDULE.md")
            self.assertIn("```mermaid", sched)
            self.assertIn("gantt", sched)
            self.assertIn("dateFormat YYYY-MM-DD", sched)
            self.assertIn("must precede", sched,
                          "two records claim app/pay.py, so an edge must exist")
            self.assertIn("## Critical path", sched)

    def test_the_decision_index_lists_candidates_and_their_lineage(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, candidates=2, risk=True)
            self._generate(d, "--tier", "3")
            index = self._read_doc(d, "30-decisions/INDEX.md")
            self.assertIn("always run the payment tests before committing 0",
                          index)
            self.assertIn("## Lineage", index)
            self.assertIn("the retry path double charges", index,
                          "a note must be rendered at its anchor")
            self.assertIn("Dana, backend", index,
                          "a note must carry its author")

    def test_the_data_model_is_introspected_from_the_live_schema(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            model = self._read_doc(d, "20-technical/DATA-MODEL.md")
            self.assertIn("erDiagram", model)
            for table in ("records", "claims", "decisions", "notes",
                          "learning_candidates"):
                self.assertIn("### `%s`" % table, model,
                              "%s is in the schema but not in the diagram"
                              % table)
            self.assertIn("points at `records.lifecycle_uuid`", model,
                          "a foreign key the schema declares is not drawn")

    def test_the_dependency_graph_comes_from_the_imports(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            deps = self._read_doc(d, "20-technical/DEPENDENCIES.md")
            self.assertIn("`app/checkout.py` imports `app/pay.py`", deps)
            self.assertIn("flowchart LR", deps)

    def test_the_code_map_inventories_modules_and_tests(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            code_map = self._read_doc(d, "20-technical/CODE-MAP.md")
            self.assertIn("### `app/pay.py`", code_map)
            self.assertIn("`app/test_pay.py`: 1 test(s)", code_map)

    # -- 5.7 critical path correctness on a known graph ---------------------

    def test_the_critical_path_matches_a_hand_computed_one(self):
        """A graph with a hand-computed answer.

            a(3) -> b(1) -> d(2)
            a(3) -> c(9) -> d(2)

        By hand: a,b,d is 3+1+2 = 6 and a,c,d is 3+9+2 = 14, so the critical
        path is a,c,d at 14. A longest path that took the first branch it found,
        or that summed edges instead of node durations, would answer 6."""
        bd = self._bd()
        weights = {"a": 3, "b": 1, "c": 9, "d": 2}
        edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]
        path, total = bd.critical_path(weights, edges)
        self.assertEqual(["a", "c", "d"], path)
        self.assertEqual(14, total)

    def test_the_critical_path_of_an_unconnected_graph_is_the_heaviest_node(self):
        bd = self._bd()
        path, total = bd.critical_path({"a": 2, "b": 7}, [])
        self.assertEqual((["b"], 7), (path, total))

    def test_a_cycle_refuses_rather_than_publishing_a_shorter_path(self):
        bd = self._bd()
        with self.assertRaises(bd.DocsError) as ctx:
            bd.critical_path({"a": 1, "b": 1}, [("a", "b"), ("b", "a")])
        self.assertEqual("schedule-cycle", ctx.exception.reason)
        self.assertIn("cycle", str(ctx.exception))

    def test_the_critical_path_is_stable_across_equal_weights(self):
        """Determinism reaches into the graph too: two runs over the same graph
        must pick the same winner, or SCHEDULE.md churns on every generation."""
        bd = self._bd()
        weights = dict((c, 1) for c in "abcdef")
        edges = [("a", "b"), ("c", "d"), ("e", "f")]
        first = bd.critical_path(weights, edges)
        second = bd.critical_path(weights, list(reversed(edges)))
        self.assertEqual(first, second)

    # -- 5.7 determinism ---------------------------------------------------

    def test_two_regenerations_in_a_row_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, candidates=2, risk=True)
            self._generate(d, "--tier", "3")
            before = self._tree(d)
            self._generate(d, "--tier", "3")
            after = self._tree(d)
            self.assertEqual(sorted(before), sorted(after))
            changed = [k for k in before if before[k] != after[k]]
            self.assertEqual([], changed,
                             "regeneration churned these files: %s" % changed)

    def test_regeneration_is_identical_even_when_the_clock_moves(self):
        """The byte-identity test above cannot see a clock read on its own.

        MEASURED, not assumed: a `bs.now_iso()` reinjected into a document body
        passed that test, because two regenerations inside one test run land in
        the same second and the store's timestamps have second precision. So the
        clock is MOVED between the two runs here, in process, and any generation
        time read at all becomes a diff. This is the test that actually holds the
        determinism claim up."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, candidates=2, risk=True)
            self._generate(d, "--tier", "3")
            before = self._tree(d)
            bd = self._bd("bm_docs_clock_moved")
            original = bd.bs.now_iso
            bd.bs.now_iso = lambda: "2031-12-25T13:14:15Z"
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bd.main(["generate", "--tier", "3"]), 0)
            finally:
                os.chdir(cwd)
                bd.bs.now_iso = original
            after = self._tree(d)
            changed = [k for k in sorted(before) if before[k] != after[k]]
            self.assertEqual(
                [], changed,
                "moving the clock changed these files, so a generation "
                "timestamp reached a document body: %s" % changed)
            for rel in sorted(after):
                self.assertNotIn(b"2031-12-25", after[rel],
                                 "%s carries the generation clock" % rel)

    def test_no_generated_body_carries_a_generation_timestamp(self):
        """The reason determinism holds. A "generated at" line is the one field
        that guarantees a diff on every run, and it is the first thing anybody
        adds back."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            for rel in sorted(self._tree(d)):
                if rel.endswith(".json"):
                    continue
                text = self._read_doc(d, rel.replace(os.sep, "/"))
                for line in text.split("\n"):
                    low = line.lower()
                    self.assertNotIn("generated at", low,
                                     "%s carries a generation timestamp" % rel)
                    self.assertNotIn("generated on", low,
                                     "%s carries a generation timestamp" % rel)

    # -- I10: human text survives -----------------------------------------

    HUMAN = "Dana: I still think the retry path is the real risk here."

    def _plant_human(self, d, rel, body=None):
        path = os.path.join(d, "Documentation", *rel.split("/"))
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        marker = "<!-- bm-human:begin -->\n<!-- bm-human:end -->"
        self.assertIn(marker, text, "%s has no empty human block to fill" % rel)
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(
                marker,
                "<!-- bm-human:begin -->\n%s\n<!-- bm-human:end -->"
                % (body if body is not None else self.HUMAN), 1))

    def test_regeneration_preserves_a_planted_human_block_in_every_document(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, candidates=2)
            self._generate(d, "--tier", "3")
            pages = [rel.replace(os.sep, "/") for rel in sorted(self._tree(d))
                     if rel.endswith(".md")]
            self.assertTrue(pages)
            for rel in pages:
                self._plant_human(d, rel)
            r = self._generate(d, "--tier", "3")
            for rel in pages:
                self.assertIn(self.HUMAN, self._read_doc(d, rel),
                              "regeneration destroyed human text in %s, which "
                              "I10 forbids" % rel)
            self.assertIn("%d human block(s) preserved verbatim" % len(pages),
                          r.stdout)

    def test_calibrated_a_destructive_generator_loses_the_human_block(self):
        """The reinjection that proves the test above is load bearing. A
        generator that ignores the existing file's human blocks is exactly the
        defect I10 names, and this is what it looks like: the planted paragraph
        is gone from the regenerated document."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            self._plant_human(d, "40-handover/HANDOVER.md")
            bd = self._bd("bm_docs_destructive")
            original = bd.read_existing

            def _destructive(path):
                out = original(path)
                out["human"] = []
                return out

            bd.read_existing = _destructive
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bd.main(["generate", "--tier", "3"]), 0)
            finally:
                os.chdir(cwd)
                bd.read_existing = original
            self.assertNotIn(
                self.HUMAN, self._read_doc(d, "40-handover/HANDOVER.md"),
                "the destructive variant did NOT lose the human block, so the "
                "preservation test above proves nothing")

    REDACTABLE_HUMAN = ("Dana: the DB password: ask Sam before you touch the "
                        "migration.")

    def test_regeneration_keeps_human_prose_the_redactor_would_rewrite(self):
        """The secret scrubber is pattern based, so ordinary handover prose comes
        back mangled if it is run over a human block. The funnel exempts those
        blocks and reports them instead."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            self._plant_human(d, "40-handover/HANDOVER.md",
                              body=self.REDACTABLE_HUMAN)
            r = self._generate(d, "--tier", "3")
            self.assertIn(self.REDACTABLE_HUMAN,
                          self._read_doc(d, "40-handover/HANDOVER.md"),
                          "the redactor rewrote a human paragraph")
            self.assertIn("KEPT VERBATIM AND WORTH READING", r.stdout,
                          "the founder was not told their paragraph looks "
                          "secret shaped")

    def test_a_prose_marker_pasted_into_a_human_block_is_prose(self):
        """A reviewer quoting a generated header inside the block the file tells
        them to write in must not thereby reconfigure the cache."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            forged = ("Quoting what I saw: <!-- bm-prose: id=handover-what "
                      "sha256=%s -->\nthis is my own paragraph" % ("0" * 64))
            self._plant_human(d, "40-handover/HANDOVER.md", body=forged)
            self._generate(d, "--tier", "3")
            text = self._read_doc(d, "40-handover/HANDOVER.md")
            self.assertIn("this is my own paragraph", text)
            self.assertIn("Quoting what I saw", text)
            self.assertNotIn("sha256=%s" % ("0" * 64),
                             text.split("<!-- bm-human:begin -->")[0],
                             "a pasted marker was adopted as a cache record")

    def test_a_store_field_containing_the_marker_cannot_forge_a_human_block(self):
        """PROBED against a real store before it was believed either way.

        A founder objective holding the literal begin marker would, if a marker
        were matched anywhere in a line, open a fake human block: everything
        after it in the document would be exempt from redaction and would be
        carried through the next regeneration as somebody's prose. It cannot,
        for two reasons that are worth writing down because both must hold. The
        marker is matched only as a WHOLE stripped line, and every store field
        reaches a page through bm_learning.safe_display, which escapes the
        newline a field would need to start a line of its own."""
        with tempfile.TemporaryDirectory() as d:
            bs = self._bs()
            self._project(d)
            store = bs.Store(d)
            try:
                store.claim("sneaky", "persistent",
                            objective="ordinary text <!-- bm-human:begin --> "
                                      "and now I am inside a human block",
                            files=["app/ledger.py"], owner="X",
                            session_id="sessC")
            finally:
                store.close()
            self._generate(d, "--tier", "3")
            bd = self._bd()
            path = os.path.join(d, "Documentation", "10-business", "WBS.md")
            parsed = bd.read_existing(path)
            self.assertEqual(
                [""], parsed["human"],
                "a store field forged a human block: %r" % parsed["human"])
            self.assertIn("and now I am inside a human block",
                          self._read_doc(d, "10-business/WBS.md"),
                          "the objective was silently dropped instead of being "
                          "rendered as the text it is")

    # -- I12: prose is cached against facts --------------------------------

    def _prose_body(self, d, rel, pid):
        bd = self._bd()
        path = os.path.join(d, "Documentation", *rel.split("/"))
        return bd.read_existing(path)["prose"][pid]

    def _rerecord_prose_body(self, d, rel, pid, body):
        """Replace one narrative block's text AND its recorded body checksum, the
        way a legitimate re-record looks.

        A body edit ALONE is a cache miss now (a paragraph must match its own
        checksum or it is written again from the facts), so a reuse test that
        planted text without re-recording would only ever prove the integrity
        check fires. The checksum is recomputed with the engine's own helper
        rather than a second copy of the hashing rule."""
        bd = self._bd()
        path = os.path.join(d, "Documentation", *rel.split("/"))
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        pattern = re.compile(
            r"(<!-- bm-prose: id=%s sha256=[0-9a-f]{64}) body=[0-9a-f]{64}"
            r"( -->\n)(.*?)(\n<!-- bm-prose:end -->)" % re.escape(pid), re.S)
        m = pattern.search(text)
        self.assertIsNotNone(m, "no narrative block %s in %s" % (pid, rel))
        replacement = "%s body=%s%s%s%s" % (m.group(1), bd._body_hash(body),
                                            m.group(2), body, m.group(4))
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text[:m.start()] + replacement + text[m.end():])
        return path

    def test_unchanged_facts_do_not_regenerate_a_narrative_block(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, candidates=2)
            self._generate(d, "--tier", "3")
            r = self._run(d, "generate", "--tier", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            report = json.loads(r.stdout)
            self.assertGreater(report["prose_reused"], 0)
            self.assertEqual(
                0, report["prose_regenerated"],
                "nothing about the facts moved, so no block should have been "
                "written again")

    def test_a_reused_block_is_the_recorded_body_not_a_fresh_one(self):
        """The decisive form of the claim. The body on disk is REPLACED and its
        checksum re-recorded, the facts are left alone, and the replacement must
        survive: a generator that rewrote the paragraph and happened to produce
        the same bytes would pass a counter test and fail this one."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            rel = "40-handover/HANDOVER.md"
            before = self._prose_body(d, rel, "handover-what")
            self._rerecord_prose_body(d, rel, "handover-what",
                                      "MARKED: this body was not rewritten.")
            self._generate(d, "--tier", "3")
            after = self._prose_body(d, rel, "handover-what")
            self.assertEqual("MARKED: this body was not rewritten.",
                             after["body"])
            self.assertEqual(before["sha"], after["sha"])

    def test_a_changed_fact_does_regenerate_the_block(self):
        """The other half of I12. A cache that never refreshes is not a cache,
        it is a stale page, so the hash has to actually move something."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            rel = "40-handover/HANDOVER.md"
            before = self._prose_body(d, rel, "handover-what")
            # Re-recorded, so the ONLY reason this block can be written again is
            # the fact that moves below.
            self._rerecord_prose_body(d, rel, "handover-what", "MARKED: stale.")
            # A new module changes the module count, which the handover
            # narrative describes, so its fact hash must move.
            self._write(d, "app/ledger.py", '"""the ledger"""\n\n\ndef post():\n'
                                            "    return 1\n")
            r = self._run(d, "generate", "--tier", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertGreater(json.loads(r.stdout)["prose_regenerated"], 0)
            after = self._prose_body(d, rel, "handover-what")
            self.assertNotEqual(before["sha"], after["sha"],
                                "the fact hash did not move when a fact moved")
            self.assertNotIn("MARKED: stale.", after["body"])

    def test_calibrated_a_cache_that_ignores_the_hash_rewrites_everything(self):
        """The reinjection for the two tests above. A `prose` that always calls
        its writer is the defect I12 names: the edited body is gone, and the
        cost the cache exists to avoid is paid on every single run."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            rel = "40-handover/HANDOVER.md"
            self._rerecord_prose_body(d, rel, "handover-what",
                                      "MARKED: this body was not rewritten.")
            bd = self._bd("bm_docs_uncached")

            def _always(self_, pid, keys, writer):
                sha = self_.fact_hash(keys)
                self_.regenerated += 1
                body = writer()
                return (["<!-- bm-prose: id=%s sha256=%s body=%s -->"
                         % (pid, sha, bd._body_hash(body))]
                        + body.split("\n") + [bd._PROSE_CLOSE])

            original = bd.Generator.prose
            bd.Generator.prose = _always
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bd.main(["generate", "--tier", "3"]), 0)
            finally:
                os.chdir(cwd)
                bd.Generator.prose = original
            after = self._prose_body(d, rel, "handover-what")
            self.assertNotEqual(
                "MARKED: this body was not rewritten.", after["body"],
                "the uncached variant did NOT rewrite the body, so the reuse "
                "tests above prove nothing")

    FALSE_SENTENCE = ("This project is fully audited and has no open risks. "
                      "Ship it.")

    def _plant_false_prose(self, d, rel, pid):
        """Swap a generated paragraph's text and leave the recorded checksums
        alone, which is what a bad merge, a stale copy or a hand edit looks
        like."""
        bd = self._bd()
        path = os.path.join(d, "Documentation", *rel.split("/"))
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        pattern = re.compile(
            r"(<!-- bm-prose: id=%s sha256=[0-9a-f]{64} body=[0-9a-f]{64} -->\n)"
            r"(.*?)(\n<!-- bm-prose:end -->)" % re.escape(pid), re.S)
        m = pattern.search(text)
        self.assertIsNotNone(m, "no narrative block %s in %s" % (pid, rel))
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text[:m.start(2)] + self.FALSE_SENTENCE + text[m.end(2):])
        self.assertEqual(self.FALSE_SENTENCE,
                         bd.read_existing(path)["prose"][pid]["body"],
                         "the plant did not land")
        return path

    def test_a_generated_paragraph_swapped_under_a_valid_fact_hash_is_rewritten(self):
        """THE THIRD DEFECT THIS FIXES. The fact hash covers the FACTS, never the
        prose, so any text at all could be swapped in under a still-valid hash
        and was then reused on every later run for as long as those facts held
        still. The header of that same file, and the RUNBOOK it generates, both
        tell the reader everything outside the human markers is rewritten. So a
        false sentence planted in HANDOVER.md could not be corrected by
        regenerating, which is the integrity half of I12."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            rel = "40-handover/HANDOVER.md"
            self._plant_false_prose(d, rel, "handover-what")
            r = self._run(d, "generate", "--tier", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            report = json.loads(r.stdout)
            self.assertEqual(1, report["prose_rewritten_unverified"],
                             "the swap was not counted")
            self.assertNotIn(self.FALSE_SENTENCE, self._read_doc(d, rel),
                             "a planted sentence survived regeneration while the "
                             "page claims it rewrites everything outside the "
                             "human markers")

    def test_the_founder_is_told_a_block_failed_its_own_checksum(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            self._plant_false_prose(d, "40-handover/HANDOVER.md",
                                    "handover-what")
            r = self._generate(d, "--tier", "3")
            self.assertIn("REWRITTEN FROM THE FACTS", r.stdout,
                          "a paragraph was silently corrected, and a silent "
                          "correction is one nobody reviews")

    def test_a_swap_is_repaired_and_then_stays_repaired(self):
        """The repair converges: the run after the repair reuses again and the
        bytes stop moving, so the integrity check cannot become the churn the
        determinism test forbids."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            clean = self._tree(d)
            self._plant_false_prose(d, "40-handover/HANDOVER.md",
                                    "handover-what")
            self._generate(d, "--tier", "3")
            repaired = self._tree(d)
            self.assertEqual(
                [], [k for k in sorted(clean) if clean[k] != repaired[k]],
                "the repair did not restore the bytes the facts produce")
            r = self._run(d, "generate", "--tier", "3", "--json")
            self.assertEqual(0, json.loads(r.stdout)["prose_regenerated"],
                             "the repaired block was written again, so the "
                             "checksum does not agree with what was written")

    def test_calibrated_a_cache_that_trusts_the_fact_hash_alone_keeps_the_swap(self):
        """The reinjection for the three tests above, and it is the old `prose`:
        a record whose FACT hash matches is reused, whatever its text now says."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            rel = "40-handover/HANDOVER.md"
            self._plant_false_prose(d, rel, "handover-what")
            bd = self._bd("bm_docs_facts_only_cache")

            def _facts_only(self_, pid, keys, writer):
                sha = self_.fact_hash(keys)
                record = self_.prior["prose"].get(pid)
                if record is not None and record["sha"] == sha:
                    self_.reused += 1
                    body = record["body"]
                else:
                    self_.regenerated += 1
                    body = bd._neutralize_prose_markers(writer())
                return (["<!-- bm-prose: id=%s sha256=%s body=%s -->"
                         % (pid, sha, bd._body_hash(body))]
                        + body.split("\n") + [bd._PROSE_CLOSE])

            original = bd.Generator.prose
            bd.Generator.prose = _facts_only
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bd.main(["generate", "--tier", "3"]), 0)
            finally:
                os.chdir(cwd)
                bd.Generator.prose = original
            self.assertIn(
                self.FALSE_SENTENCE, self._read_doc(d, rel),
                "the fact-hash-only variant did NOT keep the planted sentence, "
                "so the integrity tests above prove nothing")

    def test_a_record_from_before_the_body_checksum_is_rewritten(self):
        """A folder generated by the previous version has no body= field. It must
        heal on the next run rather than be trusted forever."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            stripped = 0
            for rel, _tier, _what in self._bd().FILES:
                path = os.path.join(d, "Documentation", *rel.split("/"))
                if not path.endswith(".md"):
                    continue
                with io.open(path, encoding="utf-8") as fh:
                    text = fh.read()
                old = re.sub(r"(<!-- bm-prose: id=[a-z0-9-]+ "
                             r"sha256=[0-9a-f]{64}) body=[0-9a-f]{64}( -->)",
                             r"\1\2", text)
                if old == text:
                    continue
                stripped += 1
                with io.open(path, "w", encoding="utf-8") as fh:
                    fh.write(old)
            self.assertTrue(stripped, "no body checksum to remove anywhere")
            r = self._run(d, "generate", "--tier", "3", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            report = json.loads(r.stdout)
            self.assertEqual(0, report["prose_reused"],
                             "a record with no body checksum was reused")
            self.assertGreater(report["prose_regenerated"], 0)

    # -- 5.3 tiers, and I13 -----------------------------------------------

    def _tier_json(self, d, *extra):
        r = self._run(d, "tier", "--json", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_a_small_project_gets_tier_one_with_the_reason_printed(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            payload = self._tier_json(d)
            self.assertEqual(1, payload["tier"])
            self.assertEqual("lean", payload["tier_name"])
            self.assertTrue(any("tier 1 because" in reason
                                for reason in payload["tier_reasons"]),
                            payload["tier_reasons"])
            self.assertEqual(
                ["00-START-HERE.md", "30-decisions/INDEX.md",
                 "40-handover/HANDOVER.md", "90-generated/facts.json"],
                sorted(payload["would_emit"]))

    def test_recorded_decisions_raise_it_to_tier_two(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, candidates=3)
            payload = self._tier_json(d)
            self.assertEqual(2, payload["tier"])
            self.assertTrue(any("recorded decisions" in reason
                                for reason in payload["tier_reasons"]),
                            payload["tier_reasons"])

    def test_an_open_risk_flag_raises_it_to_tier_three(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, candidates=3, risk=True)
            payload = self._tier_json(d)
            self.assertEqual(3, payload["tier"])
            self.assertTrue(any("open risk flag" in reason
                                for reason in payload["tier_reasons"]),
                            payload["tier_reasons"])

    def test_two_contributors_raise_it_to_tier_three(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, second_owner=True)
            payload = self._tier_json(d)
            self.assertEqual(3, payload["tier"])
            self.assertTrue(any("contributors" in reason
                                for reason in payload["tier_reasons"]),
                            payload["tier_reasons"])

    def test_the_tier_and_its_signals_are_written_into_start_here(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, candidates=3)
            self._generate(d)
            start = self._read_doc(d, "00-START-HERE.md")
            self.assertIn("**Tier 2 (standard)**", start)
            self.assertIn("recorded decisions", start)
            self.assertIn("| Tracked files |", start)
            self.assertIn("| Recorded gates |", start)

    def test_the_tier_flag_overrides_the_measurement(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            payload = self._tier_json(d, "--tier", "3")
            self.assertEqual(3, payload["tier"])
            self.assertEqual("explicit --tier", payload["tier_source"])

    def test_a_bad_tier_flag_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            r = self._run(d, "generate", "--tier", "9")
            self.assertEqual(2, r.returncode)
            self.assertIn("refused (bad-tier)", r.stderr)
            self.assertFalse(os.path.isdir(os.path.join(d, "Documentation")),
                             "a refused run still wrote something")

    def test_an_automatic_decision_never_lowers_the_tier(self):
        """I13. Depth that quietly got thinner is depth a reader cannot trust to
        still contain what they read last week."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            payload = self._tier_json(d)
            self.assertEqual(3, payload["tier"])
            self.assertEqual(3, payload["recorded_floor"])
            self.assertTrue(any("may only raise depth" in reason
                                for reason in payload["tier_reasons"]),
                            payload["tier_reasons"])

    def test_lowering_takes_an_explicit_flag_and_says_so(self):
        """The fixture carries an open risk flag, so the SIGNALS measure tier 3
        and `--tier 1` is genuinely a lowering. The wording compares the flag
        against the measured tier and not against the recorded floor, because the
        floor is what the previous run wrote and comparing against it made two
        identical runs print two different reasons."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, candidates=2, risk=True)
            self._generate(d, "--tier", "3")
            r = self._generate(d, "--tier", "1")
            self.assertIn("lowered to tier 1 by an explicit --tier 1", r.stdout)
            self.assertIn("LEFT BEHIND and no longer maintained", r.stdout,
                          "a page a deeper tier wrote was neither maintained "
                          "nor named")
            start = " ".join(self._read_doc(d, "00-START-HERE.md").split())
            self.assertIn("NO LONGER MAINTAINED", start,
                          "START-HERE does not warn about the pages the deeper "
                          "tier left behind")
            self.assertIn("20-technical/WHITEPAPER.md", start)

    def test_a_lowered_tier_deletes_nothing(self):
        """A generator that removed the pages a deeper tier wrote could remove a
        paragraph a human put in one of them. It names them instead."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            self._plant_human(d, "20-technical/WHITEPAPER.md")
            self._generate(d, "--tier", "1")
            self.assertIn(self.HUMAN,
                          self._read_doc(d, "20-technical/WHITEPAPER.md"),
                          "lowering the tier destroyed a human block")

    def test_calibrated_without_the_recorded_floor_the_tier_drops(self):
        """The reinjection that proves the raise-only test is load bearing: with
        the floor forgotten, the same project falls from tier 3 back to tier 1."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            bd = self._bd("bm_docs_no_floor")
            original = bd.recorded_floor
            bd.recorded_floor = lambda root: 0
            cwd = os.getcwd()
            os.chdir(d)
            captured = io.StringIO()
            try:
                with contextlib.redirect_stdout(captured):
                    self.assertEqual(bd.main(["tier", "--json"]), 0)
            finally:
                os.chdir(cwd)
                bd.recorded_floor = original
            self.assertEqual(
                1, json.loads(captured.getvalue())["tier"],
                "the tier did NOT drop without the floor, so the raise-only "
                "test above proves nothing")

    def test_an_explicit_tier_above_the_measured_one_does_not_churn(self):
        """THE DEFECT THIS FIXES, found by running the real command twice.

        Run one recorded the chosen tier. Run two read it back as a floor, so
        choose_tier appended a "held at tier 3 ... (I13)" reason AND turned
        "raised to tier 3" into "confirmed at tier 3". Both strings are printed
        into START-HERE and facts.json, so the second identical run rewrote two
        files with nothing moved in the store, the code or the flag, which is
        exactly the churn section 5.7 forbids. It converged only on run three, so
        a probe against an already-converged folder saw nothing."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self.assertEqual(1, self._tier_json(d)["tier"],
                             "the fixture no longer measures tier 1, so --tier 3 "
                             "is no longer above the measured tier")
            self._generate(d, "--tier", "3")
            first = self._tree(d)
            self._generate(d, "--tier", "3")
            second = self._tree(d)
            changed = [k for k in sorted(first) if first[k] != second[k]]
            self.assertEqual(
                [], changed,
                "a second identical `generate --tier 3` churned: %s" % changed)

    def test_calibrated_a_floor_in_the_explicit_path_churns(self):
        """The reinjection that proves the test above is load bearing. This is
        the old choose_tier: the floor is applied first and the explicit flag is
        compared against the held tier rather than against the measured one."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            bd = self._bd("bm_docs_floored_explicit")
            original = bd.choose_tier

            def _floored(sig, floor=0, explicit=None):
                auto = original(sig, floor=floor)
                if explicit is None:
                    return auto
                tier, reasons = auto["tier"], list(auto["reasons"])
                if explicit < tier:
                    reasons.append("lowered to tier %d by an explicit --tier %d"
                                   % (explicit, explicit))
                elif explicit > tier:
                    reasons.append("raised to tier %d by an explicit --tier %d"
                                   % (explicit, explicit))
                else:
                    reasons.append("confirmed at tier %d by an explicit --tier %d"
                                   % (explicit, explicit))
                return {"tier": explicit, "name": bd.TIERS[explicit],
                        "reasons": reasons, "source": "explicit --tier"}

            bd.choose_tier = _floored
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(bd.main(["generate", "--tier", "3"]), 0)
                    first = self._tree(d)
                    self.assertEqual(bd.main(["generate", "--tier", "3"]), 0)
            finally:
                os.chdir(cwd)
                bd.choose_tier = original
            second = self._tree(d)
            changed = [k for k in sorted(first) if first[k] != second[k]]
            self.assertNotEqual(
                [], changed,
                "the reinjected floor did NOT churn, so the determinism test "
                "above proves nothing")

    def test_the_recorded_tier_survives_removing_the_generated_folder(self):
        """THE SECOND DEFECT THIS FIXES. The floor lived only in
        Documentation/90-generated/facts.json, the folder is generated output
        that this project's .gitignore excludes, and the RUNBOOK this engine
        writes names `git rm -r Documentation` as the rollback. Following that
        instruction erased the only copy of the floor, so the next AUTOMATIC run
        of a tier 3 project chose tier 1 with no flag passed, which is the
        lowering I13 reserves for an explicit founder flag."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            report = json.loads(self._run(d, "generate", "--tier", "3",
                                          "--json").stdout)
            self.assertEqual(".brothermode/docs-tier.json",
                             report["tier_recorded_at"])
            self.assertIsNone(report["tier_record_error"])
            shutil.rmtree(os.path.join(d, "Documentation"))
            payload = self._tier_json(d)
            self.assertEqual(3, payload["recorded_floor"],
                             "the floor did not survive the documented rollback")
            self.assertEqual(3, payload["tier"])
            self.assertTrue(any("may only raise depth" in reason
                                for reason in payload["tier_reasons"]),
                            payload["tier_reasons"])

    def test_calibrated_a_floor_kept_only_in_the_generated_folder_is_lost(self):
        """The reinjection for the test above, and it is the old code verbatim:
        `_floor_from_generated_facts` is what `recorded_floor` used to be."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            shutil.rmtree(os.path.join(d, "Documentation"))
            bd = self._bd("bm_docs_facts_only_floor")
            original = bd.recorded_floor
            bd.recorded_floor = bd._floor_from_generated_facts
            cwd = os.getcwd()
            os.chdir(d)
            captured = io.StringIO()
            try:
                with contextlib.redirect_stdout(captured):
                    self.assertEqual(bd.main(["tier", "--json"]), 0)
            finally:
                os.chdir(cwd)
                bd.recorded_floor = original
            self.assertEqual(
                1, json.loads(captured.getvalue())["tier"],
                "the facts-only floor did NOT lose the tier, so the survival "
                "test above proves nothing")

    def test_a_damaged_tier_record_does_not_stop_a_generation(self):
        """A floor that cannot be read must cost depth, never the command. The
        signals still answer, and the run still writes."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            with io.open(os.path.join(d, ".brothermode", "docs-tier.json"), "w",
                         encoding="utf-8") as fh:
                fh.write("{not json at all")
            shutil.rmtree(os.path.join(d, "Documentation"))
            payload = self._tier_json(d)
            self.assertEqual(0, payload["recorded_floor"])
            self.assertEqual(1, payload["tier"])
            self._generate(d)

    # -- 5.5 the handover -------------------------------------------------

    def test_the_handover_is_usable_by_a_human_with_no_ai(self):
        """Section 5.5 names what the page must answer. Each is a heading here,
        and the two that a reader acts on first (the command that proves the
        state, and where the code lives) must carry real content."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, candidates=2, risk=True)
            self._generate(d, "--tier", "3")
            text = self._read_doc(d, "40-handover/HANDOVER.md")
            expected = [
                "## 1. What this project is",
                "## 2. Current state, and the command that proves it",
                "## 3. What is done",
                "## 4. What is in flight",
                "## 5. What is parked, and why",
                "## 6. What is not started",
                "## 7. Where the code lives, module by module",
                "## 8. How to run it and how to test it",
                "## 9. The known traps",
                "## 10. The open decisions, and their packs",
                "## 11. Who to ask",
                "## 12. What this page cannot tell you",
            ]
            positions = []
            for heading in expected:
                self.assertIn(heading, text, "the handover is missing %s"
                              % heading)
                positions.append(text.index(heading))
            self.assertEqual(positions, sorted(positions),
                             "the handover sections are out of order")
            self.assertIn("python3 tools/test_all.py", text,
                          "the handover does not name the command that proves "
                          "the state")
            self.assertIn("`app/pay.py`", text,
                          "the handover does not say where the code lives")
            self.assertIn("the retry path double charges", text,
                          "a recorded trap is missing from the traps section")
            self.assertIn("Dana", text, "the handover names nobody to ask")

    def test_the_handover_says_so_when_there_is_no_gate_command(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            # BOTH, because a tree with test_*.py in it DOES declare a runnable
            # check by convention and the engine is right to find one. The case
            # under test is a project that declares nothing at all.
            os.remove(os.path.join(d, "tools", "test_all.py"))
            os.remove(os.path.join(d, "app", "test_pay.py"))
            self._generate(d)
            text = self._read_doc(d, "40-handover/HANDOVER.md")
            self.assertIn("declares no test command", text)

    # -- 5.6 the optional exporter ----------------------------------------

    def test_the_mandatory_path_never_imports_the_exporter(self):
        """Section 5.6, enforced structurally rather than promised. An optional
        dependency reachable from the mandatory path is a dependency."""
        with io.open(self.DOCS, encoding="utf-8") as fh:
            src = fh.read()
        # The two shapes that would actually create the edge: an import
        # statement, and this project's own path loader. Prose is allowed to
        # name the module, because the docstring has to explain what it is.
        for pattern in (r"^\s*import\s+bm_docs_export",
                        r"^\s*from\s+bm_docs_export",
                        r"_load\(\s*[\"\']bm_docs_export"):
            self.assertIsNone(
                re.search(pattern, src, re.MULTILINE),
                "bm_docs.py reaches for the optional exporter (%s)" % pattern)
        # And the load-time proof, which no grep can be talked out of.
        loaded = self._bd("bm_docs_import_edge_check")
        self.assertTrue(hasattr(loaded, "Generator"))
        self.assertEqual(
            [], [n for n in sys.modules if "bm_docs_export" in n],
            "importing the generator loaded the optional exporter")

    def test_the_exporter_adds_no_dependency_and_states_what_it_cannot_do(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            r = subprocess.run([sys.executable, self.EXPORT, "export", "--json"],
                               cwd=d, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(0, r.returncode, r.stdout + r.stderr)
            report = json.loads(r.stdout)
            formats = set(row["format"] for row in report["produced"]) | \
                set(row["format"] for row in report["refused"])
            self.assertEqual({"pdf", "docx", "xlsx"}, formats,
                             "every format must be accounted for either way")
            for row in report["refused"]:
                self.assertTrue(row["why"].strip(),
                                "%s was refused with no stated reason"
                                % row["format"])

    def test_absent_tooling_degrades_to_a_stated_limitation_not_a_crash(self):
        """The acceptance line in 5.7. Every backend is made absent on purpose,
        which is the state of most machines, and the exporter still exits 0 with
        a reason per format."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            be = self._mod("bm_docs_export_absent", self.EXPORT)
            be._importable = lambda name: False
            report = be.export(d)
            self.assertEqual([], report["produced"])
            self.assertEqual(3, len(report["refused"]))
            for row in report["refused"]:
                self.assertIn(row["format"], row["why"],
                              "a refusal must name the format it refused")
                self.assertIn("install", row["why"],
                              "a refusal must tell the reader what would fix "
                              "it: %s" % row["why"])

    def test_a_named_format_that_cannot_be_produced_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            r = subprocess.run([sys.executable, self.EXPORT, "export",
                                "--format", "docx"], cwd=d,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
            self.assertEqual(1, r.returncode, r.stdout + r.stderr)
            self.assertIn("could NOT produce docx", r.stdout)

    def test_the_exporter_report_runs_with_no_store_at_all(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, self.EXPORT, "report"], cwd=d,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
            self.assertEqual(0, r.returncode, r.stdout + r.stderr)
            self.assertIn("markdown plus mermaid is always produced", r.stdout)

    # -- copy rule ---------------------------------------------------------

    def test_the_generated_folder_carries_no_em_or_en_dash(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d, records=2, candidates=2, risk=True)
            self._generate(d, "--tier", "3")
            for rel in sorted(self._tree(d)):
                text = self._read_doc(d, rel.replace(os.sep, "/"))
                for i, line in enumerate(text.split("\n"), 1):
                    for label, ch in (("en", chr(0x2013)), ("em", chr(0x2014))):
                        self.assertNotIn(
                            ch, line,
                            "%s dash on line %d of %s" % (label, i, rel))



class TestCollaborationLayer(unittest.TestCase):
    """Phase C of docs/superpowers/specs/2026-07-30-documentation-and-gate-packs
    -design.md, section 6: notes rendered AT their anchors, lineage as a query,
    a moved anchor reported rather than dropped, and nothing a generator writes
    deleting a note.

    One real store, one real repository, one real CLI, in a throwaway directory.
    """

    DOCS = os.path.join(HERE, "bm_docs.py")
    PACKS = os.path.join(HERE, "bm_packs.py")
    LEARN = os.path.join(HERE, "bm_learn.py")
    SOURCE = ("\"\"\"the money path\"\"\"\n"
              "import json\n"
              "\n"
              "\n"
              "def charge(n):\n"
              "    return n\n"
              "\n"
              "\n"
              "def refund(n):\n"
              "    return -n\n")

    def _mod(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _bs(self):
        return self._mod("bm_store_for_collab_tests",
                         os.path.join(HERE, "bm_store.py"))

    def _bd(self):
        return self._mod("bm_docs_for_collab_tests", self.DOCS)

    def _write(self, root, rel, text):
        full = os.path.join(root, *rel.split("/"))
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _project(self, d):
        """A record with a decision, a candidate, and one note of every anchor
        type that has an identity: file, record, candidate and decision."""
        bs = self._bs()
        self._write(d, "app/pay.py", self.SOURCE)
        self._write(d, "app/checkout.py",
                    "from app import pay\n\n\ndef buy(n):\n"
                    "    return pay.charge(n)\n")
        self._write(d, "tools/test_all.py", "# the gate\n")
        store = bs.Store(d)
        try:
            rec = store.claim("payments", "persistent",
                              objective="build the money path",
                              files=["app/pay.py"], owner="Dana",
                              session_id="sessA")
            store.decide(rec.lifecycle_uuid, rec.version, "provider",
                         "we chose the boring provider on purpose")
            cand = store.capture_learning_candidate(
                "manual", trigger="when touching the money path",
                action="always run the payment tests before committing",
                because="we shipped a double charge once", scope_type="project",
                scope_key="demo", record_uuid=rec.lifecycle_uuid)
            ids = {"record": rec.lifecycle_uuid,
                   "candidate": cand["candidate_uuid"]}
            ids["file_note"] = store.add_note(
                kind="risk", severity="warning",
                body="the refund path has no idempotency key",
                author="Dana, backend", author_kind="human",
                anchor_type="file", anchor_key="app/pay.py",
                anchor_line=9)["note_uuid"]
            ids["record_note"] = store.add_note(
                kind="insight", body="the fence covers only app/pay.py",
                author="Sam, data", author_kind="human",
                anchor_type="record", anchor_key=rec.lifecycle_uuid)["note_uuid"]
            ids["candidate_note"] = store.add_note(
                kind="question", body="does the client always send a request id",
                author="Sam, data", author_kind="human",
                anchor_type="candidate",
                anchor_key=cand["candidate_uuid"])["note_uuid"]
            ids["decision_note"] = store.add_note(
                kind="review", body="matches the payment gateway contract",
                author="Priya, review", author_kind="human",
                anchor_type="decision",
                anchor_key="%s#1" % rec.lifecycle_uuid[:8])["note_uuid"]
        finally:
            store.close()
        return ids

    def _run(self, d, tool, *args):
        return subprocess.run([sys.executable, tool] + list(args), cwd=d,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def _generate(self, d, *extra):
        r = self._run(d, self.DOCS, "generate", *extra)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def _read_doc(self, d, rel):
        with io.open(os.path.join(d, "Documentation", *rel.split("/")),
                     encoding="utf-8") as fh:
            return fh.read()

    def _facts(self, d):
        return json.loads(self._read_doc(d, "90-generated/facts.json"))

    # -- rendering at the anchor -------------------------------------------

    def test_a_file_anchored_note_renders_beside_that_file_in_the_code_map(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            text = self._read_doc(d, "20-technical/CODE-MAP.md")
            head = text.index("### `app/pay.py`")
            after = text[head + 8:]
            ends = [i for i in (after.find("\n### "), after.find("\n## "))
                    if i != -1]
            section = text[head:head + 8 + (min(ends) if ends else len(after))]
            self.assertIn("Notes anchored here", section)
            self.assertIn("the refund path has no idempotency key", section,
                          "a note about app/pay.py has to appear beside "
                          "app/pay.py, not only in a flat list at the end of "
                          "the decision index")
            self.assertIn("anchored at line 9", section)

    def test_a_record_anchored_note_renders_under_that_record_in_the_wbs(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            text = self._read_doc(d, "10-business/WBS.md")
            self.assertIn("Notes anchored to this record", text)
            self.assertIn("the fence covers only app/pay.py", text)

    def test_a_decision_anchored_note_renders_under_that_decision(self):
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            self._generate(d, "--tier", "3")
            text = self._read_doc(d, "30-decisions/INDEX.md")
            head = text.index("## Decisions recorded against work records")
            section = text[head:text.index("##", head + 4)]
            self.assertIn("matches the payment gateway contract", section)
            self.assertIn(ids["decision_note"][:8], section)

    def test_a_candidate_anchored_note_renders_under_its_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            text = self._read_doc(d, "30-decisions/INDEX.md")
            head = text.index("Notes anchored to D-1")
            section = text[head:text.index("##", head)]
            self.assertIn("does the client always send a request id", section)

    # -- lineage as a query ------------------------------------------------

    def test_lineage_carries_every_author_who_touched_a_decision(self):
        """THE CALIBRATED DEFECT of this phase, found by driving the real CLI
        against a real store while the suite was green: lineage grouped notes by
        the composite anchor string (a full 32 character uuid) and looked them up
        by the eight character short id, so no note ever joined any chain and the
        section rendered the machine events alone."""
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            self._generate(d, "--tier", "3")
            facts = self._facts(d)
            lineage = facts["lineage"]
            cand_key = ids["candidate"][:8]
            dec_key = "%s#1" % ids["record"][:8]
            self.assertIn(cand_key, lineage)
            self.assertIn(dec_key, lineage)
            cand_chain = " | ".join(
                "%s %s" % (e["who"], e["what"]) for e in lineage[cand_key])
            self.assertIn("does the client always send a request id", cand_chain)
            self.assertIn("Sam, data (human)", cand_chain)
            dec_chain = " | ".join(
                "%s %s" % (e["who"], e["what"]) for e in lineage[dec_key])
            self.assertIn("matches the payment gateway contract", dec_chain)
            self.assertIn("Priya, review (human)", dec_chain)
            self.assertIn("the fence covers only app/pay.py", dec_chain,
                          "a note on the record a decision belongs to touched "
                          "that decision")
            for chain in lineage.values():
                self.assertEqual([e["at"] for e in chain],
                                 sorted(e["at"] for e in chain),
                                 "a lineage chain is in order or it is not a "
                                 "lineage")

    def test_lineage_renders_into_the_decision_index(self):
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            self._generate(d, "--tier", "3")
            text = self._read_doc(d, "30-decisions/INDEX.md")
            head = text.index("## Lineage")
            section = text[head:]
            self.assertIn("`%s#1`" % ids["record"][:8], section)
            self.assertIn("matches the payment gateway contract", section)

    # -- a moved anchor is reported ----------------------------------------

    def _move_the_line(self, d):
        self._write(d, "app/pay.py", "# a new first line\n" * 3 + self.SOURCE)

    def test_a_moved_anchor_is_reported_in_the_index_and_on_the_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            self._generate(d, "--tier", "3")
            self.assertIn("- none. 1 file anchor(s)",
                          self._read_doc(d, "30-decisions/INDEX.md"))
            self._move_the_line(d)
            report = self._generate(d, "--tier", "3")
            self.assertIn("ANCHOR MOVED", report.stdout)
            self.assertIn("line 9", report.stdout)
            text = self._read_doc(d, "30-decisions/INDEX.md")
            head = text.index("## Anchors that no longer resolve")
            section = text[head:text.index("## Lineage")]
            self.assertIn(ids["file_note"][:8], section)
            self.assertIn("moved from line 9 to line 12", section)
            self.assertIn("def refund(n):", section)

    def test_a_moved_anchor_is_reported_in_the_facts_and_in_the_handover(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._generate(d, "--tier", "3")
            self._move_the_line(d)
            self._generate(d, "--tier", "3")
            states = [a["state"] for a in self._facts(d)["note_anchors"]]
            self.assertEqual(states, ["moved"])
            self.assertIn("moved from line 9 to line 12",
                          self._read_doc(d, "40-handover/HANDOVER.md"))

    def test_a_gate_pack_reports_the_moved_anchor_beside_the_note(self):
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            self._move_the_line(d)
            r = self._run(d, self.PACKS, "pack", ids["candidate"][:8],
                          "--cite", "app/pay.py:12-13")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            directory = os.path.join(d, "Documentation", "30-decisions")
            name = sorted(f for f in os.listdir(directory)
                          if f.startswith("D-"))[0]
            with io.open(os.path.join(directory, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("ANCHOR MOVED", text)
            self.assertIn("moved from line 9 to line 12", text)

    def test_a_deleted_anchor_line_is_reported_and_the_note_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            self._write(d, "app/pay.py", "def charge(n):\n    return n\n")
            report = self._generate(d, "--tier", "3")
            self.assertIn("ANCHOR GONE", report.stdout)
            text = self._read_doc(d, "30-decisions/INDEX.md")
            self.assertIn(ids["file_note"][:8], text)
            self.assertIn("the refund path has no idempotency key", text,
                          "the note survives the line it pointed at")

    # -- nothing a generator writes deletes a note -------------------------

    def test_generation_deletes_no_note_and_keeps_a_resolved_one_resolved(self):
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            bs = self._bs()
            store = bs.Store(d)
            try:
                store.resolve_note(ids["record_note"][:8], "the fence was widened")
                before = store.list_notes()
            finally:
                store.close()
            self._move_the_line(d)
            self._generate(d, "--tier", "3")
            self._run(d, self.PACKS, "pack", ids["candidate"][:8])
            store = bs.Store(d)
            try:
                after = store.list_notes()
            finally:
                store.close()
            self.assertEqual([n["note_uuid"] for n in before],
                             [n["note_uuid"] for n in after])
            resolved = [n for n in after if n["note_uuid"] == ids["record_note"]]
            self.assertEqual(resolved[0]["resolution"], "the fence was widened")
            self.assertTrue(resolved[0]["resolved_at"])
            self.assertIn("resolved", self._read_doc(d, "10-business/WBS.md"))

    def test_regeneration_over_a_moved_anchor_is_still_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            self._move_the_line(d)
            self._generate(d, "--tier", "3")
            first = self._read_doc(d, "30-decisions/INDEX.md")
            self._generate(d, "--tier", "3")
            self.assertEqual(first, self._read_doc(d, "30-decisions/INDEX.md"))

    # -- I7 on text this project does not write ----------------------------

    EN = chr(0x2013)
    EM = chr(0x2014)

    def _dashed_project(self, d):
        """A project whose SOURCE and whose note body both hold a dash. This is
        the shape the copy rule cannot reach by writing carefully: the body is
        typed by a reviewer and the source line belongs to the project being
        documented."""
        bs = self._bs()
        self._write(d, "app/pay.py",
                    "def charge(a):\n    return a\n"
                    "LABEL = \"total %s net of fees\"\n" % self.EM)
        self._write(d, "tools/test_all.py", "# the gate\n")
        store = bs.Store(d)
        try:
            rec = store.claim("payments", "persistent",
                              objective="build the money path",
                              files=["app/pay.py"], owner="Dana",
                              session_id="sessA")
            note = store.add_note(
                kind="risk", severity="warning",
                body="label copy needs review %s see design" % self.EN,
                author="Dana, backend", author_kind="human",
                anchor_type="file", anchor_key="app/pay.py", anchor_line=3)
        finally:
            store.close()
        return rec, note

    def _generated_pages(self, d):
        root = os.path.join(d, "Documentation")
        for base, _dirs, names in os.walk(root):
            for name in sorted(names):
                full = os.path.join(base, name)
                with io.open(full, encoding="utf-8") as fh:
                    yield os.path.relpath(full, root), fh.read()

    def test_a_dash_from_a_note_or_a_source_line_never_reaches_a_page(self):
        """I7 (spec section 3) on text that came from OUTSIDE this repository.

        The suite's other dash test scans this project's own files and the older
        generated-folder test uses a dash free fixture, so both stayed green
        while a reviewer's en dash and a quoted source line's em dash landed
        verbatim in INDEX.md, HANDOVER.md and CODE-MAP.md. Reproduced against a
        real store and the real CLI before the fix.

        facts.json is scanned by the same walk and passes on its own terms: it is
        the machine record and json.dumps escapes a non-ASCII character, so the
        bytes on disk carry no dash for a human to read."""
        with tempfile.TemporaryDirectory() as d:
            self._dashed_project(d)
            self._write(d, "app/pay.py",
                        "# a\n# b\ndef charge(a):\n    return a\n"
                        "LABEL = \"total %s net of fees\"\n" % self.EM)
            self._generate(d, "--tier", "3")
            seen_body = seen_quote = False
            for rel, text in self._generated_pages(d):
                for i, line in enumerate(text.split("\n"), 1):
                    for label, ch in (("en", self.EN), ("em", self.EM)):
                        self.assertNotIn(
                            ch, line,
                            "%s dash on line %d of %s, carried in from a note "
                            "body or a quoted source line" % (label, i, rel))
                if "label copy needs review - see design" in text:
                    seen_body = True
                if "total - net of fees" in text:
                    seen_quote = True
            self.assertTrue(seen_body,
                            "the note body has to still be rendered, with a "
                            "hyphen, not dropped to satisfy the rule")
            self.assertTrue(seen_quote,
                            "the moved line has to still be quoted, with a "
                            "hyphen, not dropped to satisfy the rule")

    def test_the_store_keeps_the_authors_own_dash_untouched(self):
        """The fix is DISPLAY only. A generator that rewrote the row would be
        editing a human's words, which section 10 forbids outright."""
        with tempfile.TemporaryDirectory() as d:
            _rec, note = self._dashed_project(d)
            self._generate(d, "--tier", "3")
            bs = self._bs()
            store = bs.Store(d)
            try:
                after = [n for n in store.list_notes()
                         if n["note_uuid"] == note["note_uuid"]][0]
            finally:
                store.close()
            self.assertIn(self.EN, after["body"])
            with io.open(os.path.join(d, "app", "pay.py"),
                         encoding="utf-8") as fh:
                self.assertIn(self.EM, fh.read())

    # -- I9 on the digest of an anchored line -------------------------------

    def _learn(self, d, *args):
        r = self._run(d, self.LEARN, *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_the_digest_of_an_anchored_line_is_withheld_from_note_payloads(self):
        """I9, default deny. The column is named anchor_line_hash so that
        export_column withholds it BY SHAPE, and `bm_store dump` does. The CLI
        --json payloads never reached that policy, so `notes --json` handed out
        the unsalted sha256 of the anchored line while facts.json redacted the
        line itself: a guessed value could be confirmed byte for byte.

        Reproduced against a real store on a real .env before the fix."""
        with tempfile.TemporaryDirectory() as d:
            bs = self._bs()
            secret = "SECRET_KEY=sk-live-DEADBEEF-founder-private"
            self._write(d, ".env", "A=1\nB=2\n%s\n" % secret)
            self._write(d, "tools/test_all.py", "# the gate\n")
            store = bs.Store(d)
            try:
                store.claim("config", "persistent", objective="wire the config",
                            files=[".env"], owner="Dana", session_id="sessA")
            finally:
                store.close()
            digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
            written = self._learn(d, "note", "--kind", "insight", "--anchor",
                                  "file:.env", "--line", "3", "--body",
                                  "check this config line", "--author", "Dana",
                                  "--json")
            listed = self._learn(d, "notes", "--json")
            note_id = json.loads(written)["note_uuid"][:8]
            resolved = self._learn(d, "resolve-note", note_id, "--because",
                                   "the key moved to the keychain", "--json")
            for label, payload in (("note --json", written),
                                   ("notes --json", listed),
                                   ("resolve-note --json", resolved)):
                self.assertNotIn(digest, payload,
                                 "%s hands out the sha256 of the anchored line, "
                                 "which confirms a guess at that line byte for "
                                 "byte" % label)
                self.assertIn("WITHHELD", payload,
                              "%s should say the digest was withheld rather "
                              "than drop the field silently" % label)
            # The body the operator just typed still comes back: this fix
            # withholds a digest, it does not withhold the CLI's own output.
            self.assertIn("check this config line", listed)

    def test_a_review_note_payload_goes_through_the_same_withholding(self):
        with tempfile.TemporaryDirectory() as d:
            ids = self._project(d)
            r = self._run(d, self.PACKS, "review", ids["candidate"][:8],
                          "--by", "Priya", "--verdict", "approve",
                          "--notes", "the money path is fine", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            payload = json.loads(r.stdout)
            self.assertIn("anchor_line_hash", payload,
                          "the field stays in the payload; only its value is "
                          "subject to the policy")
            self.assertEqual(payload["anchor_line_hash"], "",
                             "a candidate anchor has no line to fingerprint")

    # -- an anchor nobody could check is not a checked anchor ---------------

    def _blank_line_note(self, d):
        bs = self._bs()
        store = bs.Store(d)
        try:
            return store.add_note(
                kind="risk", severity="warning", body="blank line anchor",
                author="Eve, data", author_kind="human", anchor_type="file",
                anchor_key="app/pay.py", anchor_line=3)["note_uuid"]
        finally:
            store.close()

    def test_an_anchor_with_no_fingerprint_is_never_counted_as_checked(self):
        """Spec section 6 plus honest output. An anchor with no fingerprint
        resolves to state unverifiable with problem False, so it reached no
        human-facing page at all and BOTH the CLI summary and INDEX.md asserted
        it had been checked against the files on disk. Every note carried over by
        the schema 7 to 8 migration is in exactly this state, so on any
        pre-existing store the index claimed every prior anchor was checked when
        none of them could be."""
        with tempfile.TemporaryDirectory() as d:
            self._project(d)
            # Line 3 of SOURCE is blank, so anchor_line_digest returns '' and
            # nothing can ever detect a move of that line.
            note = self._blank_line_note(d)
            report = self._generate(d, "--tier", "3")
            self.assertIn("could not be checked at all", report.stdout)
            self.assertIn("ANCHOR UNVERIFIABLE", report.stdout)
            self.assertIn("1 line anchor(s) checked", report.stdout,
                          "the one real anchor is still counted as checked")
            facts = self._facts(d)
            unchecked = [a for a in facts["note_anchors"]
                         if a["state"] == "unverifiable"]
            self.assertEqual(len(unchecked), 1)
            text = self._read_doc(d, "30-decisions/INDEX.md")
            head = text.index("## Anchors that no longer resolve")
            section = text[head:text.index("## Lineage")]
            self.assertIn("### Anchors that could not be checked at all",
                          section)
            self.assertIn(note[:8], section)
            self.assertIn("NOT CHECKED", section)
            self.assertNotIn("2 file anchor(s) with a line were checked",
                             section,
                             "an anchor that could not be looked at is not an "
                             "anchor that was checked")

    def test_a_store_where_no_anchor_can_be_checked_says_so(self):
        """The migrated-store shape: nothing verifiable at all. The old wording
        would have said none, every anchor checked."""
        with tempfile.TemporaryDirectory() as d:
            bs = self._bs()
            self._write(d, "app/pay.py", "alpha\nbeta\n\ngamma\n")
            self._write(d, "tools/test_all.py", "# the gate\n")
            store = bs.Store(d)
            try:
                store.claim("payments", "persistent", objective="the path",
                            files=["app/pay.py"], owner="Dana",
                            session_id="sessA")
            finally:
                store.close()
            self._blank_line_note(d)
            report = self._generate(d, "--tier", "3")
            self.assertIn("0 line anchor(s) checked", report.stdout)
            section = self._read_doc(d, "30-decisions/INDEX.md")
            self.assertIn("none found, and none could be looked for", section)
            self.assertNotIn("were checked against the files as they are on "
                             "disk", section)


class TestTheAdoptionBook(unittest.TestCase):
    """Phase D of docs/superpowers/specs/2026-07-30-documentation-and-gate-packs
    -design.md, section 7: every claim in the book must be true of the shipped
    code at the tag it documents.

    WHY THIS CLASS EXISTS
      An adversarial pass produced two readings of chapter six. One said the
      citation walkthrough was impossible against the shipped tool; running the
      tool against the file state the chapter itself displays reproduced every
      printed line, including the recorded hash prefix, so that reading was
      wrong. The other said the alert figure promised more than the guard
      delivers; running the guard confirmed it, because the guard is anchored and
      a re-captured correction carries a fresh anchor. Neither could be settled
      by reading the page. Both are settled here, through the real CLIs against
      a real store in a throwaway directory, so the page cannot drift back into
      either error unnoticed.

    A prose assertion in this class is not decoration. Each one pins a sentence
    that the tool behaviour in the same test just proved, so when the behaviour
    changes the failure lands on the page, which is the file that then has to be
    corrected.
    """

    BOOK = os.path.join("docs", "book", "brothermode-for-dummies.html")
    PACKS = os.path.join(HERE, "bm_packs.py")
    LEARN = os.path.join(HERE, "bm_learn.py")

    # The module chapter six quotes, at the state its section three displays:
    # line 7 is blank and `def order_total` sits on line 8. That blank first
    # cited line is the whole reason the chapter prints citation-changed rather
    # than citation-moved, so this fixture may not be tidied into something that
    # reads better.
    PRICING = ('"""Price a tea order. All money is in whole pence."""\n'
               "\n"
               "\n"
               "def line_total(unit_pence, quantity):\n"
               "    return unit_pence * quantity\n"
               "\n"
               "\n"
               "def order_total(lines):\n"
               "    total = 0\n"
               "    for unit, qty in lines:\n"
               "        total += line_total(unit, qty)\n"
               "    return total\n")

    def _mod(self, name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _bs(self):
        return self._mod("bm_store_for_book_tests",
                         os.path.join(HERE, "bm_store.py"))

    def _flat(self, text):
        """One line, single spaces. The claims below are pinned by their words,
        not by where the paragraph happens to wrap."""
        return " ".join(text.split())

    def _write(self, root, rel, text):
        full = os.path.join(root, *rel.split("/"))
        parent = os.path.dirname(full)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with io.open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _run(self, d, tool, *args):
        env = dict(os.environ)
        # Autosave and telemetry read this. A test may not write to the
        # machine's real vault, so it points inside the throwaway tree.
        env["BROTHERMODE_VAULT"] = os.path.join(d, "vault")
        return subprocess.run([sys.executable, tool] + list(args), cwd=d,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, env=env)

    def _both(self, r):
        return r.stdout + r.stderr

    def _capture(self, d, trigger, action, record_uuid=None):
        bs = self._bs()
        store = bs.Store(d)
        try:
            cand = store.capture_learning_candidate(
                "manual", trigger=trigger, action=action,
                because="the totals drifted", scope_type="project",
                scope_key="tealeaf", record_uuid=record_uuid,
                session_id="sessBook")
        finally:
            store.close()
        return cand["candidate_uuid"]

    def _pack_text(self, d):
        folder = os.path.join(d, "Documentation", "30-decisions")
        names = sorted(n for n in os.listdir(folder) if n.startswith("D-"))
        self.assertEqual(len(names), 1, names)
        with io.open(os.path.join(folder, names[0]), encoding="utf-8") as fh:
            return fh.read()

    # -- chapter six, the citation walkthrough -----------------------------

    def test_the_citation_walkthrough_prints_what_the_tool_prints(self):
        book = read(self.BOOK)
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "src/pricing.py", self.PRICING)
            self._write(d, "tests/test_pricing.py",
                        "from src import pricing\n\n\ndef test_order_total():\n"
                        "    assert pricing.order_total([(1, 2)]) == 2\n")
            cand = self._capture(d, "when totalling an order",
                                 "recompute stored order totals in a migration")
            r = self._run(d, self.PACKS, "pack", cand,
                          "--cite", "src/pricing.py:7-12")
            self.assertEqual(r.returncode, 0, self._both(r))
            recorded = [ln for ln in self._pack_text(d).split("\n")
                        if "bm-cite:" in ln]
            self.assertEqual(len(recorded), 1, recorded)
            self.assertTrue('anchor=""' in recorded[0],
                            "the cited first line is blank, so the recorded "
                            "anchor has to be empty, exactly as the chapter "
                            "displays it. The pack recorded: %s"
                            % recorded[0].strip())

            # "Insert two lines at the top of the cited file, then regenerate."
            self._write(d, "src/pricing.py", "import decimal\n\n" + self.PRICING)
            r = self._run(d, self.PACKS, "pack", cand)
            self.assertEqual(r.returncode, 2, self._both(r))
            printed = self._flat(self._both(r))
            self.assertIn("refused (citation-changed): src/pricing.py lines "
                          "7-12 still start with the cited anchor", printed)
            remedy = "--recite src/pricing.py:7-12@return unit_pence * quantity"
            self.assertIn(remedy, printed)
            self.assertTrue(remedy in self._flat(book),
                            "the chapter prints this exact remedy, so the tool "
                            "has to keep printing it. Missing from the page: %r"
                            % remedy)

            # The chapter then runs the remedy it printed, and shows it working.
            r = self._run(d, self.PACKS, "pack", cand, "--recite",
                          "src/pricing.py:7-12@return unit_pence * quantity")
            self.assertEqual(r.returncode, 0, self._both(r))
            self.assertIn("1 citation(s) re-read from disk", self._both(r))

    def test_a_cited_line_with_text_on_it_gives_the_other_refusal(self):
        """The chapter names both refusals, because both are reachable and the
        difference is whether the cited first line has text on it. This is the
        one the chapter does not print in full."""
        self.assertTrue("citation-moved" in read(self.BOOK),
                        "the chapter has to name this refusal, because a reader "
                        "who only ever sees citation-changed reads the guard as "
                        "broken the first time the other one fires")
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "src/pricing.py", self.PRICING)
            cand = self._capture(d, "when totalling an order",
                                 "recompute stored order totals in a migration")
            r = self._run(d, self.PACKS, "pack", cand,
                          "--cite", "src/pricing.py:5-9")
            self.assertEqual(r.returncode, 0, self._both(r))
            self._write(d, "src/pricing.py", "import decimal\n\n" + self.PRICING)
            r = self._run(d, self.PACKS, "pack", cand)
            self.assertEqual(r.returncode, 2, self._both(r))
            printed = self._flat(self._both(r))
            self.assertIn("refused (citation-moved): src/pricing.py line 5 no "
                          "longer starts with the cited anchor", printed)
            self.assertIn("It is now at line 7.", printed)
            self.assertIn("--recite src/pricing.py:7-11@", printed,
                          "the moved refusal names the SHIFTED range, which is "
                          "exactly how it differs from citation-changed")

    # -- chapter six, the alert figure -------------------------------------

    def test_the_alert_figure_claims_only_what_the_anchor_delivers(self):
        book = self._flat(read(self.BOOK))
        # assertIn against a whole book prints the whole book, which buries the
        # finding under the page. Every check below reports the phrase only.
        self.assertFalse(
            "critical alert closes all of them" in book,
            "chapter six claims 'critical alert closes all of them'. The guard "
            "is anchored, so no caption may promise that one alert closes every "
            "route to a rule.")
        for phrase in ("An alert guards the gates it is anchored to, which is "
                       "narrower than every gate",
                       "capture the same correction a second time and you get a "
                       "new candidate with nothing anchored to it, and that "
                       "gate opens",
                       "A file anchor and a record anchor outlive the "
                       "candidate."):
            self.assertTrue(phrase in book,
                            "chapter six no longer states the anchored scope of "
                            "the alert guard. Missing: %r" % phrase)

    def test_a_recaptured_correction_reaches_a_rule_as_the_book_says(self):
        """The narrow half of the claim, proven both ways: the anchored gate
        refuses, and a fresh candidate carrying the same sentence does not.

        IF THE GUARD IS EVER WIDENED to match rule text rather than anchors,
        this test fails, and the fix is to correct the paragraph in chapter six.
        It is not to weaken the assertion.
        """
        bs = self._bs()
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "src/pricing.py", self.PRICING)
            trigger = "when a stored total is recomputed"
            action = "run the recompute migration over live invoices"
            first = self._capture(d, trigger, action)
            store = bs.Store(d)
            try:
                note = store.add_note(
                    kind="alert", severity="critical",
                    body="no dry run mode, this rewrites live invoices",
                    author="Priya", author_kind="human",
                    anchor_type="candidate", anchor_key=first)
            finally:
                store.close()
            r = self._run(d, self.LEARN, "grant-approval", first,
                          "--answer", "yes")
            self.assertEqual(r.returncode, 2, self._both(r))
            self.assertIn("refused (unresolved-critical-alert)", self._both(r))

            second = self._capture(d, trigger, action)
            r = self._run(d, self.LEARN, "grant-approval", second,
                          "--answer", "yes")
            self.assertEqual(r.returncode, 0, self._both(r))
            tokens = re.findall(r"\b[0-9a-f]{40,}\b", self._both(r))
            self.assertTrue(tokens, self._both(r))
            r = self._run(d, self.LEARN, "approve", second,
                          "--receipt", tokens[0], "--ref", "book claim probe")
            self.assertEqual(r.returncode, 0, self._both(r))
            self.assertIn("approved as rule", self._both(r))

            store = bs.Store(d)
            try:
                rows = [n for n in store.list_notes(include_resolved=True)
                        if n["note_uuid"] == note["note_uuid"]]
            finally:
                store.close()
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["resolved_at"],
                             "the alert was never answered, which is the point")
            self.assertFalse(rows[0]["overridden_at"],
                             "and no override was recorded, so the page may not "
                             "claim the founder had to answer the objection")

    def test_a_file_anchored_alert_blocks_a_later_candidate_as_the_book_says(self):
        """The remedy the new paragraph prints: anchor the alert to the file and
        it stands in front of a candidate captured afterwards, whose wording is
        different, purely because of the file that candidate would change."""
        bs = self._bs()
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "src/pricing.py", self.PRICING)
            store = bs.Store(d)
            try:
                rec = store.claim("pricing", "persistent",
                                  objective="fix the totals",
                                  files=["src/pricing.py"], owner="Dana",
                                  session_id="sessBook")
                store.add_note(
                    kind="alert", severity="critical",
                    body="no dry run mode on anything that rewrites this file",
                    author="Priya", author_kind="human",
                    anchor_type="file", anchor_key="src/pricing.py")
            finally:
                store.close()
            cand = self._capture(d, "when VAT changes mid month",
                                 "reprice open invoices in a migration",
                                 record_uuid=rec.lifecycle_uuid)
            r = self._run(d, self.LEARN, "grant-approval", cand,
                          "--answer", "yes")
            self.assertEqual(r.returncode, 2, self._both(r))
            printed = self._flat(self._both(r))
            self.assertIn("refused (unresolved-critical-alert)", printed)
            self.assertIn("about file src/pricing.py", printed)

    # -- the page itself ---------------------------------------------------

    def test_the_book_opens_offline_and_carries_no_foreign_dash(self):
        book = read(self.BOOK)
        offenders = [i for i, line in enumerate(book.split("\n"), 1)
                     if "\u2013" in line or "\u2014" in line]
        self.assertEqual(offenders, [], "em or en dash at line(s) %s"
                         % ", ".join(str(i) for i in offenders))
        for pat in ("src=\"http", "src='http", "@import url(http",
                    "<script src", "<link "):
            self.assertFalse(pat in book,
                             "section 7 requires the page to render offline "
                             "with no external request, and %r is one" % pat)

    def test_part_two_exists_and_teaches_the_current_surface(self):
        """Added 2026-08-01 with part two. The book rotted once: it shipped
        pinned to rc.4 while the product grew a guided layer, a marketplace
        install and a seventh command it never mentioned, and a founder had
        to notice. These pins make that rot loud instead of quiet."""
        book = read(self.BOOK)
        for anchor in ('id="ch13"', 'id="ch14"', 'id="ch15"', 'id="ch16"'):
            self.assertIn(anchor, book,
                          "part two lost its chapter %s" % anchor)
        for cmd in ("/brotherme-start", "/brotherme-status", "/brotherme-next",
                    "/brotherme-review", "/brotherme-deliver",
                    "/brotherme-update", "/brotherme-help"):
            self.assertIn(cmd, book,
                          "the book no longer names %s; the guided layer "
                          "chapter has drifted behind the shipped command "
                          "set" % cmd)
        self.assertIn("/plugin marketplace add khalilmaaouni/BrotherModeUp",
                      book,
                      "chapter two no longer teaches the marketplace "
                      "install path")
        self.assertIn('id="philosophy"', book,
                      "the book lost its philosophy-and-laws section, the "
                      "page the founder asked to be big and visible")
        self.assertEqual(book.count("<strong>%d. " % 10), 1,
                         "the ten-laws list no longer reaches law ten")

    def test_the_book_serves_the_reader_before_the_product(self):
        """The 2026-08-01 usefulness gate, book half. The red team measured
        4,100 words before first success and the guided commands at
        chapter thirteen, and the founder rated the result 1/5. These pins
        hold the inversion: a cheat sheet up front and a task index a
        reader can enter through."""
        book = read(self.BOOK)
        self.assertIn('id="cheatsheet"', book,
                      "the cheat sheet left the front of the book")
        self.assertIn("I want to", book,
                      "the task index left the book; readers navigate by "
                      "their question, not the product's structure")


# ---------------------------------------------------------------------------
# The identity contract and the capability register (positioning loops L1.2
# and L1.3, 2026-08-04). The 2026-08-04 identity survey found five written
# forms of one idea in the tracked tree: BrotherModeUp 165 times in 44 files,
# BrotherMode 641 times in 130 files, BrotherME 131 times in 19 files, and the
# two lowercase namespaces 700 and 466 times. Nothing in the tree said which
# form belonged where, so nothing could go red when a page picked the wrong
# one. These tests are that missing check.
# ---------------------------------------------------------------------------

IDENTITY_JSON = "product.identity.json"
CAPABILITIES_JSON = "capabilities.status.json"
IDENTITY_CONTRACT = os.path.join("docs", "brand", "IDENTITY-CONTRACT.md")

#: The only four states a capability may be in. Anything else is a state
#: somebody invented in a hurry, which is how "mostly works" gets shipped.
CAPABILITY_STATES = ("certified", "beta", "experimental", "unsupported")

#: An evidence pointer that names a path in this repository must name one that
#: exists. Two shapes are recognized: a repo-relative path with a slash, and a
#: shouting root-level document such as README.md.
EVIDENCE_PATH_TOKEN = re.compile(r"[\w.\-]+(?:/[\w.\-]+)+|\b[A-Z][A-Z0-9_.\-]*\.md\b")


def capability_offenders(data, root=ROOT):
    """Every reason `data` is not a usable capability register, as strings.

    Kept out of the TestCase so the crafted-violation fixtures below can run
    the SAME predicate against hand-built data, which is what proves the guard
    bites rather than just agreeing with the file that happens to be here."""
    offenders = []
    entries = data.get("capabilities")
    if not isinstance(entries, list) or not entries:
        return ["capabilities: expected a non-empty list"]
    seen = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            offenders.append("entry %d is not an object" % i)
            continue
        where = entry.get("id") or "entry %d" % i
        for key in ("id", "title", "state", "evidence"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                offenders.append("%s: %s is missing or empty" % (where, key))
        if entry.get("id") in seen:
            offenders.append("%s: duplicate id" % where)
        seen.add(entry.get("id"))
        if entry.get("state") not in CAPABILITY_STATES:
            offenders.append("%s: state %r is not one of %s"
                             % (where, entry.get("state"),
                                ", ".join(CAPABILITY_STATES)))
        evidence = entry.get("evidence")
        if isinstance(evidence, str):
            for token in EVIDENCE_PATH_TOKEN.findall(evidence):
                # A pointer at the end of a sentence carries the sentence's
                # punctuation. The file it names does not.
                token = token.rstrip(".,;:)")
                if not os.path.exists(os.path.join(root, token)):
                    offenders.append("%s: evidence names %s, which is not in "
                                     "the tree" % (where, token))
    return offenders


class TestProductIdentityIsOneRecord(unittest.TestCase):
    """Protects: the product identity is written down once, machine-readably,
    and agrees with the manifests that a package registry and a plugin host
    actually key off. A contract nobody can diff against is a memo."""

    def test_both_identity_files_exist_and_parse(self):
        for rel in (IDENTITY_JSON, CAPABILITIES_JSON, IDENTITY_CONTRACT):
            self.assertTrue(
                os.path.exists(os.path.join(ROOT, rel)),
                "%s is missing; the identity contract has no machine-readable "
                "half without it" % rel)
        for rel in (IDENTITY_JSON, CAPABILITIES_JSON):
            try:
                json.loads(read(rel))
            except ValueError as exc:
                self.fail("%s does not parse as JSON: %s" % (rel, exc))

    def test_the_identity_record_carries_every_agreed_field(self):
        identity = json.loads(read(IDENTITY_JSON))
        missing = [k for k in ("product_name", "persona_name", "persona_scope",
                               "plugin_id", "marketplace_id", "pip_package",
                               "python_package", "command_prefixes",
                               "env_prefixes", "repo_slug", "repo_url",
                               "durable_state_namespace",
                               "code_identity_namespace", "contract_doc")
                   if k not in identity]
        self.assertEqual(missing, [],
                         "%s is missing field(s): %s"
                         % (IDENTITY_JSON, ", ".join(missing)))
        for key in ("command_prefixes", "env_prefixes"):
            self.assertIsInstance(identity[key], list, "%s must be a list" % key)
            self.assertTrue(identity[key], "%s must not be empty" % key)

    def test_the_identity_record_agrees_with_the_shipped_manifests(self):
        """The template for this is test_all_release_manifests_describe_one
        _identity above: read every manifest that carries the fact and refuse
        a disagreement, rather than trusting one of them."""
        identity = json.loads(read(IDENTITY_JSON))
        caps = json.loads(read(CAPABILITIES_JSON))
        plugin = json.loads(read(os.path.join(".claude-plugin", "plugin.json")))
        marketplace = json.loads(read(os.path.join(".claude-plugin",
                                                   "marketplace.json")))
        pip_name = re.search(r'(?m)^name\s*=\s*"([^"]+)"',
                             read("pyproject.toml")).group(1)
        offenders = []

        def same(label, got, want):
            if got != want:
                offenders.append("%s: identity says %r, the tree says %r"
                                 % (label, want, got))

        same("plugin id (.claude-plugin/plugin.json name)",
             plugin.get("name"), identity["plugin_id"])
        same("marketplace id (.claude-plugin/marketplace.json name)",
             marketplace.get("name"), identity["marketplace_id"])
        for entry in marketplace.get("plugins", []):
            same("marketplace plugins[].name", entry.get("name"),
                 identity["plugin_id"])
        same("pip package (pyproject.toml [project] name)", pip_name,
             identity["pip_package"])
        same("repo url (plugin.json homepage)", plugin.get("homepage"),
             identity.get("ships_from_url") or identity["repo_url"])
        # ships_from_url, not repo_url: since the M6 cutover the plugin is
        # installed from the hub whose marketplace source resolves it, while
        # repo_url stays the product's own repository, the one that owns the
        # tags an auditor clones and the one homepage names. A record that
        # declares no ships_from_url is a single-repository product, where
        # the two facts are one string.
        same("ships-from url (plugin.json repository)",
             plugin.get("repository"),
             identity.get("ships_from_url") or identity["repo_url"])
        same("repo url (tools/bm_project_facts.py REPO_URL)",
             FACTS["repo_url"], identity["repo_url"] + ".git")
        same("product name (capabilities.status.json)",
             caps.get("product_name"), identity["product_name"])
        same("contract doc pointer", identity["contract_doc"],
             IDENTITY_CONTRACT.replace(os.sep, "/"))

        if identity["repo_slug"] not in identity["repo_url"]:
            offenders.append("repo slug %r does not appear in repo url %r"
                             % (identity["repo_slug"], identity["repo_url"]))
        if not os.path.exists(os.path.join(ROOT, identity["python_package"],
                                           "__init__.py")):
            offenders.append("python package %r has no __init__.py in the tree"
                             % identity["python_package"])
        if not os.path.exists(os.path.join(ROOT, identity["contract_doc"])):
            offenders.append("contract_doc %r is not in the tree"
                             % identity["contract_doc"])

        self.assertEqual(
            offenders, [],
            "the identity record and the shipped manifests disagree: %s"
            % "; ".join(offenders))

    def test_the_contract_document_declares_itself_current(self):
        head = "\n".join(read(IDENTITY_CONTRACT).split("\n")[:25])
        self.assertTrue(
            CURRENT_STATUS.search(head),
            "%s does not declare `Status: CURRENT` in its first 25 lines, so a "
            "reader cannot tell whether it is the live contract"
            % IDENTITY_CONTRACT)

    def test_no_em_or_en_dash_in_the_identity_files(self):
        """The project's copy rule, on the three files this loop adds. The
        older dash test targets ACTIVE_DOCS and the toolchain, neither of
        which these are."""
        offenders = []
        for rel in (IDENTITY_CONTRACT, IDENTITY_JSON, CAPABILITIES_JSON):
            for i, line in enumerate(read(rel).split("\n"), 1):
                if "\u2013" in line or "\u2014" in line:
                    offenders.append("%s:%d" % (rel, i))
        self.assertEqual(offenders, [], "em or en dash found at %s"
                         % ", ".join(offenders))


class TestCapabilityRegisterIsHonest(unittest.TestCase):
    """Protects: every capability the project claims carries one of four
    states and a pointer to what proves it. The failure this prevents is the
    one the register was written for: a page saying a thing works, with
    nothing behind the sentence and no way for a test to notice."""

    def test_every_entry_carries_a_valid_state_and_real_evidence(self):
        offenders = capability_offenders(json.loads(read(CAPABILITIES_JSON)))
        self.assertEqual(
            offenders, [],
            "%s: %s" % (CAPABILITIES_JSON, "; ".join(offenders)))

    def test_the_register_declares_its_own_provenance(self):
        caps = json.loads(read(CAPABILITIES_JSON))
        for key in ("product_name", "updated", "source_of_truth"):
            self.assertTrue(str(caps.get(key, "")).strip(),
                            "%s: top-level key %s is missing or empty"
                            % (CAPABILITIES_JSON, key))
        self.assertRegex(caps["updated"], r"^\d{4}-\d{2}-\d{2}$",
                         "%s: updated must be an ISO date" % CAPABILITIES_JSON)

    def test_the_register_states_what_is_not_promised(self):
        """A register with no `unsupported` row is a brochure. The non-promises
        are the half a reader cannot get anywhere else."""
        caps = json.loads(read(CAPABILITIES_JSON))
        states = [e.get("state") for e in caps.get("capabilities", [])]
        for state in CAPABILITY_STATES:
            self.assertIn(state, states,
                          "%s carries no capability in state %r"
                          % (CAPABILITIES_JSON, state))

    # -- the guard, proven against crafted violations ----------------------

    def test_an_invented_state_is_caught(self):
        bad = {"capabilities": [{"id": "x", "title": "X",
                                 "state": "mostly works",
                                 "evidence": "tools/test_bm_docs.py"}]}
        self.assertEqual(
            capability_offenders(bad),
            ["x: state 'mostly works' is not one of certified, beta, "
             "experimental, unsupported"])

    def test_an_empty_evidence_field_is_caught(self):
        bad = {"capabilities": [{"id": "x", "title": "X", "state": "beta",
                                 "evidence": "   "}]}
        self.assertEqual(capability_offenders(bad),
                         ["x: evidence is missing or empty"])

    def test_evidence_naming_a_file_that_is_not_there_is_caught(self):
        bad = {"capabilities": [{"id": "x", "title": "X", "state": "certified",
                                 "evidence": "tools/test_bm_imaginary.py"}]}
        self.assertEqual(
            capability_offenders(bad),
            ["x: evidence names tools/test_bm_imaginary.py, which is not in "
             "the tree"])

    def test_an_empty_register_is_caught(self):
        self.assertEqual(capability_offenders({"capabilities": []}),
                         ["capabilities: expected a non-empty list"])


class TestGeneratedCapabilityStatusBlock(unittest.TestCase):
    """Protects: README.md's certified-versus-beta section is RENDERED from
    capabilities.status.json, never retyped beside it.

    The register was landed as the source of truth for what this project
    claims, and the page a reader actually opens is README.md. Nothing
    connected the two, so the register could move while the page kept
    yesterday's promise, which is the same class of defect this whole suite
    exists for. tools/bm_docs.py capability-status renders the block between
    two markers; this refuses a block that is not what a fresh render
    produces."""

    DOCS = os.path.join(HERE, "bm_docs.py")

    def _bm_docs(self):
        spec = importlib.util.spec_from_file_location(
            "bm_docs_for_capability_tests", self.DOCS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _run(self, *args):
        return subprocess.run([sys.executable, self.DOCS] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def _fixture(self, d, block):
        """A throwaway tree holding this repository's real register and a
        README carrying `block` between the markers, so the fixtures exercise
        the same register the page has to agree with."""
        shutil.copyfile(os.path.join(ROOT, CAPABILITIES_JSON),
                        os.path.join(d, CAPABILITIES_JSON))
        with io.open(os.path.join(d, "README.md"), "w",
                     encoding="utf-8") as fh:
            fh.write("# demo\n\nbefore\n\n%s\n\nafter\n" % block)

    # -- the real page -----------------------------------------------------

    def test_the_readme_block_is_what_the_register_renders_today(self):
        bm = self._bm_docs()
        fresh = bm.render_capability_status(bm.load_capability_register(ROOT))
        self.assertEqual(
            bm.extract_capability_status(read("README.md")), fresh,
            "README.md's generated capability block is not what %s renders "
            "today. It is generated output: run python3 tools/bm_docs.py "
            "capability-status --write rather than editing it by hand."
            % CAPABILITIES_JSON)

    def test_every_register_entry_reaches_the_page(self):
        """The render could agree with itself and still drop a row. Every
        title in the register has to appear in the block a reader sees,
        including the unsupported ones, which are the half nobody else
        publishes."""
        block = self._bm_docs().extract_capability_status(read("README.md"))
        missing = [e["title"] for e in json.loads(read(CAPABILITIES_JSON))
                   ["capabilities"] if e["title"] not in block]
        self.assertEqual(missing, [],
                         "capability title(s) in the register that reach no "
                         "line of README.md: %s" % "; ".join(missing))

    def test_the_command_reports_the_page_as_current(self):
        r = self._run("capability-status", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("matches", r.stdout)

    # -- determinism -------------------------------------------------------

    def test_two_renders_of_one_register_are_byte_identical(self):
        """The same guarantee the documentation engine makes: a generator that
        churns makes every review open on a diff that means nothing."""
        bm = self._bm_docs()
        data = bm.load_capability_register(ROOT)
        self.assertEqual(bm.render_capability_status(data),
                         bm.render_capability_status(data))
        first = self._run("capability-status")
        second = self._run("capability-status")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout,
                         "two runs of the renderer disagree")

    # -- the guard, proven against crafted violations ----------------------

    def test_a_stale_block_is_refused_and_the_writer_repairs_it(self):
        """The defect this test exists for: the register moves, the page keeps
        yesterday's promise. Proven end to end, against the real CLI."""
        bm = self._bm_docs()
        with tempfile.TemporaryDirectory() as d:
            fresh = bm.render_capability_status(
                bm.load_capability_register(ROOT))
            stale = fresh.replace("beta means real", "beta means finished", 1)
            self.assertNotEqual(stale, fresh, "the fixture mutated nothing")
            self._fixture(d, stale)
            r = self._run("capability-status", "--check", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("capability-status-stale", r.stderr)
            r = self._run("capability-status", "--write", "--root", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = self._run("capability-status", "--check", "--root", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with io.open(os.path.join(d, "README.md"), encoding="utf-8") as fh:
                repaired = fh.read()
            self.assertEqual(bm.extract_capability_status(repaired), fresh)
            self.assertIn("before\n", repaired)
            self.assertIn("after\n", repaired,
                          "the writer touched text outside the markers")

    def test_a_page_with_no_markers_is_refused_rather_than_guessed_at(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copyfile(os.path.join(ROOT, CAPABILITIES_JSON),
                            os.path.join(d, CAPABILITIES_JSON))
            with io.open(os.path.join(d, "README.md"), "w",
                         encoding="utf-8") as fh:
                fh.write("# demo\n\nno markers here\n")
            r = self._run("capability-status", "--check", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("no-capability-markers", r.stderr)

    def test_a_register_carrying_an_invented_state_is_refused(self):
        """The renderer must not be a second, weaker gate than
        capability_offenders above: an entry nobody could place is a refusal,
        not a row quietly dropped from the page."""
        with tempfile.TemporaryDirectory() as d:
            data = json.loads(read(CAPABILITIES_JSON))
            data["capabilities"][0]["state"] = "mostly works"
            with io.open(os.path.join(d, CAPABILITIES_JSON), "w",
                         encoding="utf-8") as fh:
                fh.write(json.dumps(data))
            r = self._run("capability-status", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("bad-capability-register", r.stderr)
            self.assertIn("mostly works", r.stderr)


# ---------------------------------------------------------------------------
# The evidence-gated roadmap (positioning loop L1.6) and the one verification
# entry point (loop L1.7), both 2026-08-04.
#
# The register says what is OFFERED in four states. A roadmap has to say
# something the register cannot: how far each claim has actually been checked,
# and what is deliberately not on the list at all. docs/ROADMAP.md carries that
# in six proof states, and its status section is GENERATED from the same
# register for the same reason README.md's is: a hand-typed second copy of the
# register drifts from it, and drift is the defect the register exists to stop.
# ---------------------------------------------------------------------------

ROADMAP_DOC = os.path.join("docs", "ROADMAP.md")

#: The six proof states, strongest first. Retyped here rather than imported
#: from bm_docs.py on purpose: a test that imports the constant it is checking
#: agrees with the tool by construction and proves nothing about it.
ROADMAP_PROOF_STATES = ("certified", "verified externally", "verified in CI",
                        "verified locally", "implemented", "planned")

#: A markdown table row, split on the pipes that are not escaped. The renderer
#: escapes a pipe inside a register field, so a naive split on "|" would tear a
#: cell in half the first time an evidence sentence carried one.
_ROW_SPLIT = re.compile(r"(?<!\\)\|")


class _DocsCLI(object):
    """Shared plumbing for the classes below: bm_docs.py as an imported module,
    and bm_docs.py as the real command line. Same two helpers
    TestGeneratedCapabilityStatusBlock defines for itself; that class is left
    exactly as it was rather than retrofitted onto this, because rewriting a
    passing guard to save six lines is how a guard gets weakened by accident."""

    DOCS = os.path.join(HERE, "bm_docs.py")

    def _bm_docs(self):
        spec = importlib.util.spec_from_file_location(
            "bm_docs_for_roadmap_tests", self.DOCS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _run(self, *args):
        return subprocess.run([sys.executable, self.DOCS] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def _feature_rows(self, block):
        """(capability, proof state, evidence) for every three-cell row in
        `block`. A three-cell row is a FEATURE line; the non-goal table below it
        has two cells, which is what keeps the two apart without the parser
        having to know the headings."""
        rows = []
        for line in block.split("\n"):
            line = line.strip()
            if not line.startswith("|") or line.startswith("|---"):
                continue
            cells = [c.strip() for c in _ROW_SPLIT.split(line)[1:-1]]
            if len(cells) != 3 or cells[1] == "Proof state":
                continue
            rows.append(tuple(cells))
        return rows

    def _non_goal_rows(self, block):
        rows = []
        for line in block.split("\n"):
            line = line.strip()
            if not line.startswith("|") or line.startswith("|---"):
                continue
            cells = [c.strip() for c in _ROW_SPLIT.split(line)[1:-1]]
            if len(cells) != 2 or cells[0] == "Not a goal":
                continue
            rows.append(tuple(cells))
        return rows


class TestGeneratedRoadmapStatusBlock(_DocsCLI, unittest.TestCase):
    """Protects: the roadmap's status section is RENDERED from
    capabilities.status.json, never retyped beside it, and every line in it
    carries one of the six proof states.

    Same defect as the README block this mirrors: the register moves, the page
    keeps yesterday's promise. The added risk here is a roadmap's own: a
    roadmap is the one page where a claim can be promoted by wishing, so the
    mapping from register state to proof state is code, and this holds it."""

    def _fixture(self, d, block):
        """A throwaway tree holding this repository's real register and a
        roadmap page carrying `block` between the markers."""
        shutil.copyfile(os.path.join(ROOT, CAPABILITIES_JSON),
                        os.path.join(d, CAPABILITIES_JSON))
        os.makedirs(os.path.join(d, "docs"), exist_ok=True)
        with io.open(os.path.join(d, ROADMAP_DOC), "w",
                     encoding="utf-8") as fh:
            fh.write("# demo\n\nbefore\n\n%s\n\nafter\n" % block)

    # -- the real page -----------------------------------------------------

    def test_the_roadmap_block_is_what_the_register_renders_today(self):
        bm = self._bm_docs()
        fresh = bm.render_roadmap_status(bm.load_capability_register(ROOT))
        self.assertEqual(
            bm.extract_roadmap_status(read(ROADMAP_DOC)), fresh,
            "%s carries a generated roadmap block that is not what %s renders "
            "today. It is generated output: run the roadmap-status subcommand "
            "with --write rather than editing it by hand."
            % (ROADMAP_DOC, CAPABILITIES_JSON))

    def test_every_register_entry_reaches_the_page(self):
        """The render could agree with itself and still drop a row."""
        block = self._bm_docs().extract_roadmap_status(read(ROADMAP_DOC))
        missing = [e["title"] for e in json.loads(read(CAPABILITIES_JSON))
                   ["capabilities"] if e["title"] not in block]
        self.assertEqual(missing, [],
                         "capability title(s) in the register that reach no "
                         "line of %s: %s" % (ROADMAP_DOC, "; ".join(missing)))

    def test_the_command_reports_the_page_as_current(self):
        r = self._run("roadmap-status", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("matches", r.stdout)

    def test_two_renders_of_one_register_are_byte_identical(self):
        bm = self._bm_docs()
        data = bm.load_capability_register(ROOT)
        self.assertEqual(bm.render_roadmap_status(data),
                         bm.render_roadmap_status(data))
        first = self._run("roadmap-status")
        second = self._run("roadmap-status")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout,
                         "two runs of the renderer disagree")

    # -- the proof states --------------------------------------------------

    def test_every_feature_line_carries_exactly_one_of_the_six_proof_states(self):
        """The claim this page is FOR. A feature line with no proof state is a
        promise with nothing behind it, and a line carrying two is a line a
        reader cannot act on.

        Counted in the proof-state CELL rather than across the whole row on
        purpose: the evidence cell is register prose, and a register sentence is
        allowed to contain the word planned without that making the row
        planned."""
        block = self._bm_docs().extract_roadmap_status(read(ROADMAP_DOC))
        rows = self._feature_rows(block)
        self.assertTrue(rows, "the generated block carries no feature line at "
                              "all; the parser or the renderer is broken")
        offenders = []
        for title, state, _evidence in rows:
            hits = [s for s in ROADMAP_PROOF_STATES if s in state]
            if len(hits) != 1 or state not in ROADMAP_PROOF_STATES:
                offenders.append("%s carries %r" % (title, state))
        self.assertEqual(
            offenders, [],
            "roadmap feature line(s) not carrying exactly one of %s: %s"
            % (", ".join(ROADMAP_PROOF_STATES), "; ".join(offenders)))

    def test_the_page_covers_every_register_row_once(self):
        """Every capability is either a feature line or a non-goal, and never
        both. A row that falls out of both tables is a claim the page stopped
        reporting without anyone deciding to stop reporting it."""
        block = self._bm_docs().extract_roadmap_status(read(ROADMAP_DOC))
        entries = json.loads(read(CAPABILITIES_JSON))["capabilities"]
        features = [r[0] for r in self._feature_rows(block)]
        non_goals = [r[0] for r in self._non_goal_rows(block)]
        self.assertEqual(
            sorted(features + non_goals),
            sorted(e["title"] for e in entries),
            "the roadmap's rows and the register's rows are not the same set")
        self.assertEqual(
            sorted(non_goals),
            sorted(e["title"] for e in entries if e["state"] == "unsupported"),
            "the non-goal table must hold exactly the unsupported rows")

    def test_the_state_mapping_is_the_documented_one(self):
        """The mapping, against crafted entries rather than against whatever
        the register happens to hold today. A mapping tested only through the
        real file stops being tested the moment the file changes shape."""
        state_of = self._bm_docs().roadmap_proof_state
        self.assertEqual("certified", state_of(
            {"state": "certified", "evidence": "tools/test_bm_docs.py proves it."}))
        self.assertEqual("verified in CI", state_of(
            {"state": "beta",
             "evidence": "the store job in .github/workflows/tests.yml runs it."}))
        self.assertEqual("verified locally", state_of(
            {"state": "beta", "evidence": "tools/test_bm_plugin_install.py runs it."}))
        self.assertEqual("implemented", state_of(
            {"state": "experimental", "evidence": "docs/BENCHMARK.md states the method."}))
        self.assertEqual("planned", state_of(
            {"state": "experimental", "evidence": "not measured"}))
        self.assertIsNone(state_of(
            {"state": "unsupported", "evidence": "Not offered."}),
            "an unsupported row is a non-goal, not a rung on the ladder")

    def test_an_invented_state_is_refused_by_the_mapping(self):
        bm = self._bm_docs()
        with self.assertRaises(bm.DocsError):
            bm.roadmap_proof_state({"state": "mostly works", "evidence": "x"})

    # -- the guard, proven against crafted violations ----------------------

    def test_a_stale_block_is_refused_and_the_writer_repairs_it(self):
        bm = self._bm_docs()
        with tempfile.TemporaryDirectory() as d:
            fresh = bm.render_roadmap_status(bm.load_capability_register(ROOT))
            stale = fresh.replace("certified", "shipped", 1)
            self.assertNotEqual(stale, fresh, "the fixture mutated nothing")
            self._fixture(d, stale)
            r = self._run("roadmap-status", "--check", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("roadmap-status-stale", r.stderr)
            r = self._run("roadmap-status", "--write", "--root", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = self._run("roadmap-status", "--check", "--root", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with io.open(os.path.join(d, ROADMAP_DOC), encoding="utf-8") as fh:
                repaired = fh.read()
            self.assertEqual(bm.extract_roadmap_status(repaired), fresh)
            self.assertIn("before\n", repaired)
            self.assertIn("after\n", repaired,
                          "the writer touched text outside the markers")

    def test_a_page_with_no_markers_is_refused_rather_than_guessed_at(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copyfile(os.path.join(ROOT, CAPABILITIES_JSON),
                            os.path.join(d, CAPABILITIES_JSON))
            os.makedirs(os.path.join(d, "docs"))
            with io.open(os.path.join(d, ROADMAP_DOC), "w",
                         encoding="utf-8") as fh:
                fh.write("# demo\n\nno markers here\n")
            r = self._run("roadmap-status", "--check", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("no-roadmap-markers", r.stderr)


class TestTheRoadmapPageIsEvidenceGated(unittest.TestCase):
    """Protects the hand-written half of docs/ROADMAP.md: the six proof states
    are DEFINED on the page, the promotion rule is written down, the waves name
    outcomes rather than dates, and the deferrals are stated rather than left
    for a reader to infer from silence."""

    def setUp(self):
        self.text = read(ROADMAP_DOC)

    def test_the_page_declares_itself_current(self):
        head = "\n".join(self.text.split("\n")[:25])
        self.assertTrue(
            CURRENT_STATUS.search(head),
            "%s does not declare `Status: CURRENT` in its first 25 lines, so a "
            "reader cannot tell whether it is the live roadmap" % ROADMAP_DOC)

    def test_this_suite_reads_the_page_as_current_state(self):
        """It has to be inside current_pages(), or the naming rule and the
        banned-absolutes rule never look at it."""
        self.assertIn(ROADMAP_DOC, current_pages())

    def test_the_preamble_defines_all_six_proof_states(self):
        """Read case insensitively: the page defines each rung as a heading, so
        the name is capitalized where it is defined and lowercase where the
        generated table below uses it."""
        preamble = self.text.split(
            "<!-- BEGIN GENERATED ROADMAP STATUS -->")[0].lower()
        missing = [s for s in ROADMAP_PROOF_STATES if s.lower() not in preamble]
        self.assertEqual(missing, [],
                         "%s defines no proof state named: %s"
                         % (ROADMAP_DOC, ", ".join(missing)))

    def test_the_page_states_the_documentation_only_rule(self):
        """The rule that makes the ladder worth anything: a page edit cannot
        promote an item. Asserted on the words, because this is the one
        sentence a later editor is most likely to soften."""
        self.assertRegex(
            self.text,
            r"(?i)no item .{0,80}certified .{0,120}documentation[ \-]only",
            "%s does not state that no item reaches certified through a "
            "documentation-only change" % ROADMAP_DOC)

    def test_the_waves_name_outcomes_and_not_dates(self):
        """A dated roadmap is a promise this project cannot keep and does not
        make. The waves section is checked for a calendar date of any shape: an
        ISO date, a quarter, a bare year, or a month name.

        `may` and `march` stay in the month list on purpose, even though both
        are ordinary English words. The section has to be written around them,
        which costs one rewording and buys a guard that cannot be walked past
        by writing "shipping in May" instead of a date."""
        section = self._section("waves")
        shapes = (re.compile(r"\d{4}-\d{2}-\d{2}"),
                  re.compile(r"(?i)\bQ[1-4]\b"),
                  re.compile(r"\b20\d{2}\b"),
                  re.compile(r"(?i)\b(january|february|march|april|may|june|"
                             r"july|august|september|october|november|december)"
                             r"\b"))
        offenders = [m.group(0) for pat in shapes for m in pat.finditer(section)]
        self.assertEqual(offenders, [],
                         "the waves section of %s names a date or a quarter "
                         "(%s); waves name outcomes"
                         % (ROADMAP_DOC, ", ".join(offenders)))

    def test_the_deferrals_section_names_every_deferral(self):
        section = self._section("deferrals")
        for word in ("mobile", "creative media", "Office", "multi-user"):
            self.assertIn(word.lower(), section.lower(),
                          "the deferrals section of %s never names %s"
                          % (ROADMAP_DOC, word))

    def _section(self, keyword):
        """The text of the numbered heading whose title contains `keyword`, up
        to the next heading of the same level."""
        parts = re.split(r"(?m)^## ", self.text)
        for part in parts[1:]:
            if keyword.lower() in part.split("\n", 1)[0].lower():
                return part
        self.fail("%s carries no `## ` heading naming %r" % (ROADMAP_DOC, keyword))


class TestVerifyDocsIsOneEntryPoint(_DocsCLI, unittest.TestCase):
    """Protects: one command runs every documentation check a page can fail,
    reports one line per lane, and exits nonzero when any lane fails.

    Before this, the checks existed but were spread across two subcommands and
    a suite nobody runs by hand mid-edit. A founder at a gate needs one command
    whose output says which lane failed, not a traceback from the first one."""

    LANES = ("capability-status", "roadmap-status", "identity-manifests",
             "links")

    def _tree(self, d):
        """A throwaway tree that PASSES every lane, built from this
        repository's own real register and manifests so the fixture cannot
        drift into agreeing with a checker that has stopped working."""
        bm = self._bm_docs()
        for rel in (CAPABILITIES_JSON, IDENTITY_JSON, "pyproject.toml"):
            shutil.copyfile(os.path.join(ROOT, rel), os.path.join(d, rel))
        os.makedirs(os.path.join(d, ".claude-plugin"))
        for name in ("plugin.json", "marketplace.json"):
            shutil.copyfile(os.path.join(ROOT, ".claude-plugin", name),
                            os.path.join(d, ".claude-plugin", name))
        identity = json.loads(read(IDENTITY_JSON))
        os.makedirs(os.path.join(d, identity["python_package"]))
        self._write(d, os.path.join(identity["python_package"], "__init__.py"),
                    "")
        os.makedirs(os.path.join(d, "docs", "brand"))
        self._write(d, identity["contract_doc"].replace("/", os.sep),
                    "# contract\n\nStatus: CURRENT as of 2026-08-04.\n")
        data = bm.load_capability_register(d)
        self._write(d, "README.md", "# demo\n\n%s\n\nA link to "
                    "[the roadmap](docs/ROADMAP.md).\n"
                    % bm.render_capability_status(data))
        self._write(d, ROADMAP_DOC, "# demo\n\n%s\n"
                    % bm.render_roadmap_status(data))
        return d

    def _write(self, d, rel, text):
        with io.open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _lanes(self, stdout):
        """{lane: verdict} read out of the printed report."""
        out = {}
        for line in stdout.split("\n"):
            m = re.match(r"^(PASS|FAIL)\s+([\w\-]+):", line.strip())
            if m:
                out[m.group(2)] = m.group(1)
        return out

    # -- the tree as it stands ---------------------------------------------

    def test_this_repository_passes_every_lane(self):
        r = self._run("verify-docs")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lanes = self._lanes(r.stdout)
        for lane in self.LANES:
            self.assertEqual("PASS", lanes.get(lane),
                             "%s did not PASS:\n%s" % (lane, r.stdout))

    def test_every_lane_reports_exactly_once_and_in_order(self):
        r = self._run("verify-docs")
        order = [m.group(1) for m in
                 re.finditer(r"(?m)^(?:PASS|FAIL)\s+([\w\-]+):", r.stdout)]
        self.assertEqual(list(self.LANES), order[:len(self.LANES)],
                         "the lanes did not run in the documented order:\n%s"
                         % r.stdout)
        for lane in self.LANES:
            self.assertEqual(1, order.count(lane),
                             "%s reported %d times" % (lane, order.count(lane)))

    def test_the_fixture_tree_passes_too(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            r = self._run("verify-docs", "--root", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # -- the guard, proven against crafted violations ----------------------

    def test_a_broken_relative_link_fails_the_link_lane(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            self._write(d, os.path.join("docs", "BROKEN.md"),
                        "# broken\n\nStatus: CURRENT as of 2026-08-04.\n\n"
                        "See [the missing page](MISSING-PAGE.md).\n")
            r = self._run("verify-docs", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertEqual("FAIL", self._lanes(r.stdout).get("links"),
                             r.stdout)
            self.assertIn("MISSING-PAGE.md", r.stdout)
            # The other lanes still ran and still reported.
            self.assertEqual("PASS",
                             self._lanes(r.stdout).get("capability-status"))

    def test_a_link_inside_a_fenced_block_is_an_example_and_not_a_promise(self):
        """The false positive this scope had to avoid: a page showing what a
        markdown link looks like is not a page carrying a broken one."""
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            self._write(d, os.path.join("docs", "TEACHING.md"),
                        "# teaching\n\nStatus: CURRENT as of 2026-08-04.\n\n"
                        "Write a link like this:\n\n```\n"
                        "[the missing page](MISSING-PAGE.md)\n```\n")
            r = self._run("verify-docs", "--root", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual("PASS", self._lanes(r.stdout).get("links"),
                             r.stdout)

    def test_a_stale_generated_block_fails_its_own_lane(self):
        bm = self._bm_docs()
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            data = bm.load_capability_register(d)
            stale = bm.render_capability_status(data).replace(
                "beta means real", "beta means finished", 1)
            self._write(d, "README.md", "# demo\n\n%s\n\nA link to "
                        "[the roadmap](docs/ROADMAP.md).\n" % stale)
            r = self._run("verify-docs", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            lanes = self._lanes(r.stdout)
            self.assertEqual("FAIL", lanes.get("capability-status"), r.stdout)
            self.assertEqual("PASS", lanes.get("roadmap-status"), r.stdout)

    def test_a_stale_roadmap_block_fails_its_own_lane(self):
        bm = self._bm_docs()
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            stale = bm.render_roadmap_status(
                bm.load_capability_register(d)).replace("certified", "shipped", 1)
            self._write(d, ROADMAP_DOC, "# demo\n\n%s\n" % stale)
            r = self._run("verify-docs", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertEqual("FAIL", self._lanes(r.stdout).get("roadmap-status"),
                             r.stdout)

    def test_a_manifest_disagreement_fails_the_identity_lane(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            plugin = json.loads(read(os.path.join(".claude-plugin",
                                                  "plugin.json")))
            plugin["name"] = "not-the-plugin-id"
            self._write(d, os.path.join(".claude-plugin", "plugin.json"),
                        json.dumps(plugin))
            r = self._run("verify-docs", "--root", d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertEqual("FAIL",
                             self._lanes(r.stdout).get("identity-manifests"),
                             r.stdout)
            self.assertIn("not-the-plugin-id", r.stdout)

    def test_the_identity_helper_clears_this_repository(self):
        """The helper the lane runs, against the real tree. It is the same
        agreement TestProductIdentityIsOneRecord asserts through its own
        loading; two independent readings of one fact is the point."""
        self.assertEqual([], self._bm_docs().identity_manifest_offenders(ROOT))

    # -- the warning lane ---------------------------------------------------

    def test_a_stale_source_line_warns_and_does_not_fail(self):
        """A page whose evidence is old is a page to revisit, not a broken
        build. `--today` pins the clock so the test cannot rot."""
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            self._write(d, os.path.join("docs", "OLD.md"),
                        "# old\n\nStatus: CURRENT as of 2026-08-04.\n\n"
                        "The peer reading here is a desk assessment as of "
                        "2026-01-01.\n")
            r = self._run("verify-docs", "--root", d, "--today", "2026-08-04")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("WARNING", r.stdout)
            self.assertIn("2026-01-01", r.stdout)
            self.assertIn(os.path.join("docs", "OLD.md").replace(os.sep, "/"),
                          r.stdout.replace(os.sep, "/"))

    def test_a_recent_source_line_does_not_warn(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            self._write(d, os.path.join("docs", "RECENT.md"),
                        "# recent\n\nStatus: CURRENT as of 2026-08-04.\n\n"
                        "Read as of 2026-07-20.\n")
            r = self._run("verify-docs", "--root", d, "--today", "2026-08-04")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("WARNING", r.stdout)

    def test_the_record_directories_are_not_read_at_all(self):
        """A dated record is evidence of what was true on a date. Its links and
        its as-of lines are not the live tree's problem."""
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            for rec in ("closure", "evidence", "superpowers"):
                os.makedirs(os.path.join(d, "docs", rec))
                self._write(d, os.path.join("docs", rec, "OLD-RECORD.md"),
                            "# record\n\nAs of 2020-01-01, see "
                            "[gone](GONE.md).\n")
            self._write(d, os.path.join("docs", "2020-01-01-dated.md"),
                        "# dated\n\nAs of 2020-01-01, see [gone](GONE.md).\n")
            r = self._run("verify-docs", "--root", d, "--today", "2026-08-04")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("WARNING", r.stdout)


#: Directories kept verbatim as the record of what was written on a date.
#: Same class as docs/closure and docs/evidence: rewriting a name inside one
#: falsifies the record, which costs more than the inconsistency it removes.
#: docs/craft was added on 2026-08-04 for the same reason: it holds an outside
#: reviewer's own words (docs/craft/FABLE-PRODUCT-CRAFT-RESEARCH-REVIEW.md
#: writes "BrotherModeUp itself has no frontend" and names BrotherME twice),
#: and editing a quotation of a reviewer is not a naming fix.
#: WIDENED 2026-08-05, and the narrower entry it replaces is the reason. The
#: list held docs/program/absolute-lead/EVIDENCE but not the program designs
#: that sit BESIDE it, so DESIGN-L04.md was read as a page a user lands on and
#: the naming rule fired on the repository slug in its own "Target tree:" line.
#: A design document is program record material of exactly the same class as
#: the evidence it produces: it states what was decided on a date, and
#: rewriting a name inside it falsifies that record. The whole
#: docs/program/absolute-lead tree therefore joins the list, which subsumes the
#: evidence entry, and docs/program/mirrorforge joins for the same reason (it
#: archives an outside program's own words plus the review of it).
#: Deliberately NOT docs/program as a whole: enumerating the subtrees keeps a
#: future user-facing page placed under docs/program covered by the rule
#: instead of silently exempt, which is the failure mode a blanket exemption
#: would create.
RECORD_DIRS = (os.path.join("docs", "closure"),
               os.path.join("docs", "evidence"),
               os.path.join("docs", "superpowers"),
               os.path.join("docs", "craft"),
               os.path.join("docs", "program", "absolute-lead"),
               os.path.join("docs", "program", "mirrorforge"),
               os.path.join("docs", "program", "solo-founder-ic"))

#: Pages this check does NOT read, each with the reason it is out. EMPTY since
#: 2026-08-04 (positioning loop L1.4), and that is the point: it held the two
#: pages the loop that wrote this test could not reach from inside its own
#: write fence, and both were fixed in prose rather than left excluded.
#:
#: docs/brotherme-explained.html presented the persona as the product (a title,
#: a kicker, a lede and a version line all reading BrotherME). The product
#: words are now BrotherMode, the three speaker labels stay BrotherME because
#: that is the persona speaking, and the paragraph above them introduces the
#: persona by name so the allowance below is earned rather than assumed.
#: docs/specs/canonical-project-protocol.md cited the dated source plan by the
#: title it was written under; it now names the file path instead, which is
#: what the contract's historical-marker rule asks a CURRENT page to do.
#:
#: A page added here again needs the same thing this dictionary always needed:
#: a named reason, and a loop that owns the fix.
NAMING_EXCLUSIONS = {}

#: A page whose NAME carries a date is dated evidence, markdown or html.
DATED_PAGE = re.compile(r"\d{4}-\d{2}-\d{2}.*\.(?:md|html)$")

REPO_SLUG_WORD = re.compile(r"BrotherModeUp")
PERSONA_WORD = re.compile(r"(?<![A-Za-z])BrotherME(?![A-Za-z])")

#: Regions where a name is a string a machine reads, not a name a reader is
#: being taught: fenced and inline code, and the repository path itself in a
#: URL or an install command. The slug is the real path on GitHub, so a
#: command carrying it is correct and a command without it is broken.
_MD_FENCE = re.compile(r"(?ms)^[ \t]*```.*?^[ \t]*```")
_MD_INLINE_CODE = re.compile(r"`[^`\n]*`")
_HTML_PRE = re.compile(r"(?is)<pre\b.*?</pre>")
_HTML_INLINE_CODE = re.compile(r"(?is)<code\b.*?</code>")
_SLUG_IN_URL_OR_COMMAND = re.compile(r"khalilmaaouni/BrotherModeUp[\w./\-]*")

#: A line that is talking about the guided beginner surface, where the persona
#: name is the correct word. Anchored so `skills/brothermode` cannot match.
_PERSONA_SCOPE = re.compile(r"skills/brotherme\b|/brotherme-|brotherme-\w|persona",
                            re.IGNORECASE)


def _blanked(match):
    """The matched region with its line structure kept and its text gone, so
    line numbers stay honest."""
    return re.sub(r"[^\n]", " ", match.group(0))


def prose_only(text, is_html):
    """`text` with every code region and every repository-path use removed."""
    for pat in ((_HTML_PRE, _HTML_INLINE_CODE) if is_html
                else (_MD_FENCE, _MD_INLINE_CODE)):
        text = pat.sub(_blanked, text)
    return _SLUG_IN_URL_OR_COMMAND.sub(_blanked, text)


def _read_at(root, rel):
    """Read a page from a fixture root.

    A missing page in a THROWAWAY fixture root is not a defect in the page:
    these fixtures write the handful of files a test cares about, and
    ACTIVE_DOCS grew a nested entry (skills/brotherme/SKILL.md) that older
    fixtures never created. A fixture that lacks a page contributes no prose,
    which is what an empty string means here. The real tree is never read
    through this path with a missing file, because the suite's own inventory
    tests fail first if an ACTIVE_DOCS entry is absent from the repository.
    """
    try:
        with io.open(os.path.join(root, rel), encoding="utf-8") as fh:
            return fh.read()
    except (IOError, OSError):
        if os.path.abspath(root) == os.path.abspath(ROOT):
            raise
        return ""


def current_pages(root=ROOT):
    """Every page a reader lands on as CURRENT state: the pages under docs/
    that are neither a dated record nor inside a record directory, plus the
    two active pages that live at the repository root. CHANGELOG.md is absent
    on purpose: it is a dated ledger, and its old entries keep their old
    names."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(root, "docs")):
        for name in sorted(filenames):
            if not name.endswith((".md", ".html")):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if rel.startswith(RECORD_DIRS) or DATED_PAGE.search(name):
                continue
            out.append(rel)
    out += [p for p in ACTIVE_DOCS if not p.startswith("docs" + os.sep)]
    return sorted(set(out))


def naming_offenders(root=ROOT, pages=None):
    """Every place a current page uses a name the identity contract does not
    allow there. See docs/brand/IDENTITY-CONTRACT.md sections 3 and 4.

    BrotherModeUp is legal in a code region and in the repository path itself;
    it is illegal in prose, where it would read as the name of the product.
    BrotherME is legal in a code region (a path or a filename carries it) and
    on a line about the guided beginner surface, where it is the persona's own
    name; it is illegal anywhere else on a current page. The contract itself is
    not read, because it is the page that quotes both rules in order to state
    them."""
    offenders = []
    pages = current_pages(root) if pages is None else pages
    for rel in pages:
        if rel == IDENTITY_CONTRACT or rel in NAMING_EXCLUSIONS:
            continue
        text = _read_at(root, rel)
        raw = text.split("\n")
        for i, line in enumerate(prose_only(text, rel.endswith(".html")).split("\n"), 1):
            if REPO_SLUG_WORD.search(line):
                offenders.append("%s:%d BrotherModeUp" % (rel, i))
            if PERSONA_WORD.search(line) and not _PERSONA_SCOPE.search(raw[i - 1]):
                offenders.append("%s:%d BrotherME" % (rel, i))
    return offenders


class TestCurrentPagesUseTheCanonicalNames(unittest.TestCase):
    """Protects: the ratified naming decision of 2026-08-04. BrotherMode is
    the product name everywhere a reader treats a page as current. BrotherME
    is a persona voice, not a product name. BrotherModeUp is a repository
    slug, not a name for the thing.

    The check ran strict once, before the allowances below existed, and
    returned 44 hits across 15 pages. Every one was then read: the install
    commands and code fences are correct and stay, and the fixtures below hold
    the line that the allowances did not neuter the rule. The two pages that
    were named exclusions were fixed in prose on 2026-08-04 and the exclusion
    list is now empty, so this test reads every current page in the tree."""

    def test_no_current_page_uses_a_retired_name_as_the_product_name(self):
        offenders = naming_offenders()
        self.assertEqual(
            offenders, [],
            "current page(s) using a name the identity contract does not "
            "allow there: %s" % ", ".join(offenders))

    def test_the_page_set_is_the_current_one_and_excludes_the_records(self):
        pages = current_pages()
        self.assertIn("README.md", pages)
        self.assertIn(os.path.join("docs", "QUICKSTART.md"), pages)
        for rel in pages:
            self.assertFalse(rel.startswith(RECORD_DIRS),
                             "%s is a record, not a current page" % rel)
            self.assertFalse(DATED_PAGE.search(os.path.basename(rel)),
                             "%s is dated evidence, not a current page" % rel)
        self.assertNotIn("CHANGELOG.md", pages)

    # -- the guard, proven against crafted violations ----------------------

    def _offenders_for(self, text, name="example.md"):
        """The real predicate, against a throwaway tree holding one page."""
        d = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(d, "docs"))
            for root_page in ("README.md", "SKILL.md"):
                with io.open(os.path.join(d, root_page), "w",
                             encoding="utf-8") as fh:
                    fh.write("# placeholder\n")
            with io.open(os.path.join(d, "docs", name), "w",
                         encoding="utf-8") as fh:
                fh.write(text)
            return naming_offenders(d)
        finally:
            shutil.rmtree(d)

    def test_the_slug_used_as_a_product_name_in_prose_is_caught(self):
        self.assertEqual(
            self._offenders_for("# Page\n\nBrotherModeUp keeps one writer per "
                                "file.\n"),
            [os.path.join("docs", "example.md") + ":3 BrotherModeUp"])

    def test_the_persona_used_as_a_product_name_in_prose_is_caught(self):
        self.assertEqual(
            self._offenders_for("# Page\n\nBrotherME keeps one writer per "
                                "file.\n"),
            [os.path.join("docs", "example.md") + ":3 BrotherME"])

    def test_the_slug_in_an_install_command_or_a_url_is_allowed(self):
        self.assertEqual(
            self._offenders_for(
                "# Page\n\nRun this:\n\n```\ngit clone https://github.com/"
                "khalilmaaouni/BrotherModeUp.git ~/x\n```\n\nOr read "
                "github.com/khalilmaaouni/BrotherModeUp for the source.\n"),
            [])

    def test_a_name_inside_a_code_fence_or_a_path_is_allowed(self):
        self.assertEqual(
            self._offenders_for(
                "# Page\n\nThe source plan is "
                "`docs/evidence/2026-08-01-source-BrotherME_plan.md`.\n\n"
                "```\ncd /absolute/path/to/BrotherModeUp\n```\n"),
            [])

    def test_the_persona_named_on_a_line_about_the_guided_skill_is_allowed(self):
        self.assertEqual(
            self._offenders_for(
                "# Page\n\nThe persona BrotherME speaks only inside the "
                "guided skill.\n"),
            [])

    def test_an_html_page_is_read_the_same_way(self):
        self.assertEqual(
            self._offenders_for(
                "<h1>Page</h1>\n<pre>git clone x/BrotherModeUp.git</pre>\n"
                "<p>BrotherME is the product.</p>\n", name="page.html"),
            [os.path.join("docs", "page.html") + ":3 BrotherME"])

    def test_a_dated_page_and_a_record_directory_are_not_read_at_all(self):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(d, "docs", "closure"))
            for root_page in ("README.md", "SKILL.md"):
                with io.open(os.path.join(d, root_page), "w",
                             encoding="utf-8") as fh:
                    fh.write("# placeholder\n")
            for rel in (os.path.join("docs", "2026-07-01-old.md"),
                        os.path.join("docs", "closure", "REPORT.md")):
                with io.open(os.path.join(d, rel), "w",
                             encoding="utf-8") as fh:
                    fh.write("# Old\n\nBrotherModeUp did the thing.\n")
            self.assertEqual(naming_offenders(d), [])
        finally:
            shutil.rmtree(d)


#: Words that promise more than any file in this tree can show. Each is
#: banned on a current page unless the same line points at the evidence.
BANNED_ABSOLUTES = (
    re.compile(r"fully supported", re.IGNORECASE),
    re.compile(r"production[ \-]ready", re.IGNORECASE),
    re.compile(r"works everywhere", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])all platforms(?![A-Za-z])", re.IGNORECASE),
)


#: A sentence that DENIES one of the absolutes is the honest use of the words,
#: and this project's pages carry three of them ("Not production ready beyond
#: the tested user and platform scope"). The cue is looked for on the matching
#: line and the two above it, because a denial often opens a sentence that the
#: banned words finish.
_DISCLAIMER = re.compile(
    r"\b(?:not|never|no|none|nor|neither|without|nothing)\b|n't", re.IGNORECASE)

#: A pointer at a file that carries the evidence. Presence is what is checked
#: here; capability_offenders above is what checks a pointer resolves.
_EVIDENCE_CITATION = re.compile(
    r"[\w.\-]+/[\w.\-]+\.(?:md|py|json|ya?ml|toml|html|sh)")

_ABSOLUTE_WINDOW = 2


def absolute_offenders(root=ROOT, pages=None):
    """Every unqualified absolute claim on a current page.

    A claim is legal when the same line cites the file that carries the
    evidence, and a denial is not a claim at all."""
    offenders = []
    if pages is None:
        pages = [p for p in current_pages(root) if p not in NAMING_EXCLUSIONS]
    for rel in pages:
        text = _read_at(root, rel)
        raw = text.split("\n")
        lines = prose_only(text, rel.endswith(".html")).split("\n")
        for i, line in enumerate(lines, 1):
            for pat in BANNED_ABSOLUTES:
                found = pat.search(line)
                if not found:
                    continue
                window = "\n".join(raw[max(0, i - 1 - _ABSOLUTE_WINDOW):i])
                if _DISCLAIMER.search(window):
                    continue
                if _EVIDENCE_CITATION.search(raw[i - 1]):
                    continue
                offenders.append("%s:%d %s" % (rel, i, found.group(0)))
    return offenders


class TestNoUnbackedAbsolutes(unittest.TestCase):
    """Protects: a current page never says a thing is finished in words no
    file can support. The four phrases are the ones a reader cannot check and
    cannot recover from being wrong about."""

    def test_no_current_page_makes_an_unbacked_absolute_claim(self):
        offenders = absolute_offenders()
        self.assertEqual(
            offenders, [],
            "unbacked absolute claim(s) on a current page: %s. Either cite the "
            "file that proves it on the same line, or write what is actually "
            "true." % ", ".join(offenders))

    # -- the guard, proven against crafted violations ----------------------

    def _offenders_for(self, text):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(d, "docs"))
            for root_page in ("README.md", "SKILL.md"):
                with io.open(os.path.join(d, root_page), "w",
                             encoding="utf-8") as fh:
                    fh.write("# placeholder\n")
            with io.open(os.path.join(d, "docs", "example.md"), "w",
                         encoding="utf-8") as fh:
                fh.write(text)
            return absolute_offenders(d)
        finally:
            shutil.rmtree(d)

    def test_a_bare_claim_is_caught(self):
        rel = os.path.join("docs", "example.md")
        self.assertEqual(
            self._offenders_for("# Page\n\nThe installer is production ready.\n"),
            [rel + ":3 production ready"])
        self.assertEqual(
            self._offenders_for("# Page\n\nIt works everywhere.\n"),
            [rel + ":3 works everywhere"])
        self.assertEqual(
            self._offenders_for("# Page\n\nThe suite runs on all platforms.\n"),
            [rel + ":3 all platforms"])
        self.assertEqual(
            self._offenders_for("# Page\n\nWindows is fully supported.\n"),
            [rel + ":3 fully supported"])

    def test_a_claim_that_cites_its_evidence_on_the_same_line_is_allowed(self):
        self.assertEqual(
            self._offenders_for(
                "# Page\n\nThe suite runs on all platforms named in "
                ".github/workflows/tests.yml.\n"),
            [])

    def test_a_denial_is_not_a_claim(self):
        self.assertEqual(
            self._offenders_for(
                "# Page\n\nNot production ready before dogfooding.\n"),
            [])

    def test_a_denial_two_lines_above_still_covers_the_sentence(self):
        """The shape this project already writes: the denial opens the
        sentence and the banned words finish it a line or two later."""
        self.assertEqual(
            self._offenders_for(
                "# Page\n\nIt is deliberately NOT described as autonomous\n"
                "self improvement, as statistical learning, or as\n"
                "production ready.\n"),
            [])

    def test_a_denial_further_up_the_page_does_not_launder_a_claim(self):
        rel = os.path.join("docs", "example.md")
        self.assertEqual(
            self._offenders_for(
                "# Page\n\nThis is not a toy.\n\nline\n\nline\n\n"
                "The installer is production ready.\n"),
            [rel + ":9 production ready"])


# The word a page uses for the shape that would be next, so the sentence
# "an eighth shape is refused where it is built" is pinned to the tuple
# rather than to whoever last counted it.
ORDINAL_WORDS = {5: "fifth", 6: "sixth", 7: "seventh", 8: "eighth",
                 9: "ninth", 10: "tenth", 11: "eleventh", 12: "twelfth"}

SHAPE_HEADING = re.compile(r"^## The ([a-z]+) shapes\b")

# The two sentence shapes that COUNT the drawn vocabulary, as opposed to the
# ones that merely mention it. "the shapes" and "two that each mean one" are
# not counts and must not trip; "Five shapes" and "all five" are, in either
# case, which is why the scan lowercases first. The capitalized form is not
# hypothetical: the comment above SHAPES read "Five shapes" while the tuple
# already held six.
COUNTING_SENTENCES = ((r"\b([a-z]+) shapes\b", "%s shapes"),
                      (r"\ball ([a-z]+)\b", "all %s"))


def miscounted_shapes(text, word):
    """Every line of TEXT that counts the shape vocabulary as anything
    other than WORD, reported as "line N says ...". Empty when the text
    counts correctly or does not count at all; the caller is the one that
    decides whether not counting at all is allowed."""
    counts = set(NUMBER_WORDS.values())
    offenders = []
    for i, line in enumerate(text.split("\n"), 1):
        for pattern, form in COUNTING_SENTENCES:
            for said in re.findall(pattern, line.lower()):
                if said in counts and said != word:
                    offenders.append("line %d says %s" % (i, form % said))
    return offenders


def shape_section(text):
    """The shape section of references/visual-surface.md, as
    (count word in its heading, {shape: its four table cells}, section text).

    Located by its heading and ended by the next one, never by line number:
    an edit anywhere above it must not be able to move what this reads.
    Raises rather than returning empty when the heading is gone, because a
    silently empty parse is a pass on a page that no longer says anything."""
    lines = text.split("\n")
    start, word = None, ""
    for i, line in enumerate(lines):
        match = SHAPE_HEADING.match(line)
        if match:
            start, word = i, match.group(1)
            break
    if start is None:
        raise AssertionError(
            "%s has no '## The <count> shapes' heading, so the shape table "
            "cannot be found" % VISUAL_REGISTER)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    rows = {}
    for line in lines[start:end]:
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")][1:-1]
        if len(cells) != 4 or cells[0] == "Shape" \
                or set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = cells
    return word, rows, "\n".join(lines[start:end])


class TestTheVisualRegisterMatchesTheShapes(unittest.TestCase):
    """references/visual-surface.md against tools/bm_visual.py's own SHAPES
    and CAPS.

    WHY THIS EXISTS. The register said "five shapes" while SHAPES already
    held seven: timeline widened the tuple in one loop and gantt in the
    next, and neither commit touched the page. So the page told every
    reader that a gantt was refused while the product was drawing one on
    its own progress view. The module cannot rot that way any more, because
    its refusal reads the count out of len(SHAPES) and
    tools/test_bm_visual.py pins that. Nothing watched the page. This is
    the watch, and it belongs to this suite rather than to the visual one
    because documentation going stale against the tree is what this suite
    is for.

    It checks the facts a reader would act on, never the prose of a row:
    which shapes exist, how many, what each one's caps are, and what is
    refused. It reads tools/bm_visual.py's own opening paragraph on the
    same terms, because that paragraph had the count wrong too, and being
    in the same file as SHAPES did not save it."""

    def _parsed(self):
        return shape_section(read(VISUAL_REGISTER))

    def test_the_table_carries_one_row_per_shape_in_the_tuple_order(self):
        _word, rows, _section = self._parsed()
        self.assertEqual(
            list(rows), list(_bv.SHAPES),
            "%s's shape table lists %s; tools/bm_visual.py's SHAPES holds "
            "%s. A shape entering or leaving the tuple is a page edit in "
            "the same change."
            % (VISUAL_REGISTER, ", ".join(rows) or "nothing",
               ", ".join(_bv.SHAPES)))

    def test_every_row_states_the_caps_the_code_enforces(self):
        """A cap is the one number in the row a reader plans against, and
        it is the number Diagram() raises on. Every non zero cap must
        appear in its row; a zero one is left out on purpose, the way the
        gate ladder row has never mentioned lanes it cannot have."""
        _word, rows, _section = self._parsed()
        offenders = []
        for shape, cells in rows.items():
            caps = _bv.CAPS.get(shape)
            if caps is None:
                offenders.append(
                    "%s: the page gives it a row and CAPS has no entry for "
                    "it, so nothing here is checked" % shape)
                continue
            stated = set(int(n) for n in re.findall(r"\d+", cells[3]))
            for name, cap in sorted(caps.items()):
                if cap and cap not in stated:
                    offenders.append(
                        "%s: CAPS says %s %d, the row says %r"
                        % (shape, name, cap, cells[3]))
        self.assertEqual(offenders, [],
                         "%s states caps the code does not enforce: %s"
                         % (VISUAL_REGISTER, "; ".join(offenders)))

    def test_every_counting_sentence_states_the_real_count(self):
        """The title, the heading, and the rule line that says how many
        shapes the rules below bite on. This is the sentence that rotted,
        so it is the sentence that is pinned."""
        text = read(VISUAL_REGISTER)
        count = len(_bv.SHAPES)
        word = NUMBER_WORDS.get(count)
        self.assertIsNotNone(
            word, "NUMBER_WORDS has no entry for %d, and this test needs "
                  "one before it can read the page" % count)
        heading_word, _rows, _section = self._parsed()
        self.assertEqual(
            heading_word, word,
            "%s's shape heading says %r and SHAPES holds %d"
            % (VISUAL_REGISTER, heading_word, count))
        self.assertIn(
            "%s shapes" % word, text.split("\n")[0],
            "%s's title must say %r; it reads %r"
            % (VISUAL_REGISTER, "%s shapes" % word, text.split("\n")[0]))
        offenders = miscounted_shapes(text, word)
        self.assertEqual(
            offenders, [],
            "%s counts its own vocabulary wrong (SHAPES holds %d, so the "
            "word is %r): %s. A comparison that is not a count of the "
            "vocabulary is phrased without a number word in front of "
            "'shapes'."
            % (VISUAL_REGISTER, count, word, "; ".join(offenders)))

    def test_the_module_says_how_many_shapes_it_holds_and_is_right(self):
        """tools/bm_visual.py's own opening paragraph, which read "Six
        shapes" while the tuple forty lines below it held seven. Prose in
        the same file as the thing it describes is not safer than prose in
        another file; it is less safe, because nobody re-reads a docstring
        they scrolled past. The paragraph must state the count, so deleting
        the sentence is a failure rather than a pass."""
        count = len(_bv.SHAPES)
        word = NUMBER_WORDS.get(count)
        self.assertIsNotNone(
            word, "NUMBER_WORDS has no entry for %d" % count)
        text = _bv.__doc__ or ""
        offenders = miscounted_shapes(text, word)
        self.assertEqual(
            offenders, [],
            "tools/bm_visual.py's docstring counts the vocabulary wrong "
            "(SHAPES holds %d, so the word is %r): %s"
            % (count, word, "; ".join(offenders)))
        self.assertIn(
            "%s shapes" % word, text.lower(),
            "tools/bm_visual.py's docstring must say how many shapes it "
            "holds, and SHAPES holds %d, so it says %r"
            % (count, "%s shapes" % word))

    def test_the_design_bans_nothing_the_product_draws(self):
        """The contradiction the founder settled on 2026-08-08. The design
        banned gantt and timeline by name, with reasons, while both were in
        SHAPES and the progress view was drawing them, and the register
        says the design outranks the page. Read literally, the shipped
        product was the defect.

        The list is read from its own machine readable line and compared
        ENTRY BY ENTRY rather than by substring, which is what lets "UML
        fork and join bars" stay banned while the decision fork D4 stays
        drawn: they share a word and are not the same thing."""
        text = read(VISUAL_DESIGN)
        match = re.search(r"BANNED SHAPES[^:]*:\n\n(.+?)\n\n", text, re.S)
        self.assertTrue(
            match, "%s no longer carries its machine readable BANNED "
                   "SHAPES line, so nothing can check the ban list against "
                   "SHAPES" % VISUAL_DESIGN)
        banned = [e.strip().lower() for e in match.group(1).split(",")
                  if e.strip()]
        drawn = sorted(set(banned) & set(s.lower() for s in _bv.SHAPES))
        self.assertEqual(
            drawn, [],
            "%s bans %s, and tools/bm_visual.py draws %s. One of the two is "
            "wrong, and the design is the one the register says outranks "
            "the page."
            % (VISUAL_DESIGN, ", ".join(drawn),
               "it" if len(drawn) == 1 else "them"))
        self.assertIn(
            "pie", banned,
            "%s must keep banning the pie; tools/test_bm_visual.py builds "
            "one and expects the ValueError" % VISUAL_DESIGN)

    def test_the_next_shape_is_refused_by_its_real_ordinal(self):
        _word, _rows, section = self._parsed()
        expected = ORDINAL_WORDS.get(len(_bv.SHAPES) + 1)
        self.assertIsNotNone(
            expected, "ORDINAL_WORDS has no entry for %d"
                      % (len(_bv.SHAPES) + 1))
        said = re.findall(r"\b([a-z]+) shape is refused\b", section)
        self.assertEqual(
            said, [expected],
            "%s calls the next shape %s; SHAPES holds %d, which makes the "
            "next one the %s"
            % (VISUAL_REGISTER, ", ".join(said) or "nothing",
               len(_bv.SHAPES), expected))

    def test_what_the_page_refuses_is_not_something_the_code_draws(self):
        """The failure this suite was handed: the page listed gantt and
        timeline as refused while both were in SHAPES. The refusal sentence
        may name anything the code will not draw, and nothing it will."""
        _word, _rows, section = self._parsed()
        match = re.search(r"There is no [^.]*\.", section, re.S)
        self.assertTrue(
            match, "%s no longer states what gets no drawing at all"
                   % VISUAL_REGISTER)
        refused = " ".join(match.group(0).split())
        drawn = [s for s in _bv.SHAPES
                 if re.search(r"\b%s\b" % re.escape(s), refused)]
        self.assertEqual(
            drawn, [],
            "%s says %r, but %s %s in SHAPES and the product draws %s"
            % (VISUAL_REGISTER, refused, ", ".join(drawn),
               "is" if len(drawn) == 1 else "are",
               "it" if len(drawn) == 1 else "them"))
        self.assertIn(
            "pie", refused,
            "%s must keep naming something the code actually refuses; "
            "tools/test_bm_visual.py builds a pie and expects the "
            "ValueError, so the pie is the one example with a test behind "
            "it" % VISUAL_REGISTER)
# The two skills a session enters when it is finishing: the delivery flow and
# the Full-Auto kill switch. Phase C step 2 of the 2026-08-08 finalization plan
# puts the continuity instruction in both, because those are the two places a
# session actually stops.
CLOSING_FLOW_SKILLS = (os.path.join("skills", "deliver", "SKILL.md"),
                       os.path.join("skills", "stop", "SKILL.md"))

# The four phrasings the instruction must carry, each pinned for a reason a
# reviewer can check rather than for style:
#   the plugin form      the path a plugin install resolves, the same funnel
#                        every other mechanical command in these skills uses
#   the clone form       the command a clone install runs, where the variable
#                        is unset; a skill that names only one of the two
#                        leaves half the installed base with a dead command
#   the packaged name    what a user types when the console script is on PATH
#   the dry run          the flag that writes the packet and launches nothing,
#                        which is the only safe way to rehearse a handover
CONTINUE_PLUGIN_FORM = ('"${CLAUDE_PLUGIN_ROOT}/tools/brothermode_cli.py" '
                        'continue')
CONTINUE_CLONE_FORM = "python3 tools/brothermode_cli.py continue"
CONTINUE_PACKAGED_NAME = "brothermode continue"
CONTINUE_DRY_RUN = "--dry-run"

# The condition and the law, in the plan's own words. The condition keeps the
# instruction from reading as "always launch a successor"; the law is the
# sentence that makes stopping without one a stated failure rather than a
# judgement call at three in the morning.
CONTINUITY_CONDITION = "open work remains"
CONTINUITY_LAW = "Silence is the only forbidden outcome"


class TestContinuityIsWiredIntoTheClosingFlows(unittest.TestCase):
    """Protects: Phase C step 2 of PLAN.md (2026-08-08), the founder's
    correction after a session closed its loop, reported, and stopped, leaving
    the program to be restarted by hand.

    `brothermode continue` existing is not the fix. The fix is that the two
    flows a session enters when it is finishing TELL it to run the verb while
    work is still open. This class pins that instruction text so a later edit
    to either skill cannot quietly drop it, and pins the verb against the CLI's
    own verb list so the documented command can never name a verb the runtime
    boundary does not own."""

    def test_both_closing_skills_name_the_continue_command_in_both_forms(self):
        missing = []
        for rel in CLOSING_FLOW_SKILLS:
            text = read(rel)
            for phrase in (CONTINUE_PLUGIN_FORM, CONTINUE_CLONE_FORM,
                           CONTINUE_PACKAGED_NAME, CONTINUE_DRY_RUN):
                if phrase not in text:
                    missing.append("%s is missing %r" % (rel, phrase))
        self.assertEqual(missing, [], "; ".join(missing))

    def test_both_closing_skills_state_the_condition_and_the_law(self):
        missing = []
        for rel in CLOSING_FLOW_SKILLS:
            text = read(rel)
            for phrase in (CONTINUITY_CONDITION, CONTINUITY_LAW):
                if phrase not in text:
                    missing.append("%s is missing %r" % (rel, phrase))
        self.assertEqual(missing, [], "; ".join(missing))

    def test_the_documented_verb_is_one_the_cli_actually_owns(self):
        """The instruction is only as good as the verb behind it. This runs the
        boundary's own help and reads the verb out of it, so renaming or
        dropping `continue` in tools/brothermode_cli.py fails here instead of
        surfacing as a dead command in a closing session."""
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "brothermode_cli.py"),
             "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertIn("continue", r.stdout,
                      "brothermode_cli.py --help does not list the continue "
                      "verb the closing skills tell sessions to run: %s%s"
                      % (r.stdout, r.stderr))

    def test_the_closing_skills_carry_no_em_or_en_dash(self):
        offenders = []
        for rel in CLOSING_FLOW_SKILLS:
            for i, line in enumerate(read(rel).split("\n"), 1):
                if "\u2013" in line or "\u2014" in line:
                    offenders.append("%s:%d" % (rel, i))
        self.assertEqual(offenders, [], "em or en dash found at %s"
                         % ", ".join(offenders))


# Phase C step 4 of the 2026-08-08 finalization plan: the one page that states
# the continuity contract in prose, for a reader who is not about to read
# tools/bm_continue.py. The skills tell a session WHAT to run; this page says
# WHY, what happens when the launch cannot happen, and which half of the
# protocol a machine actually enforces.
CONTINUITY_DOC = os.path.join("docs", "CONTINUITY.md")

# The ladder of last resorts, in the order the plan names it. Order is pinned,
# not just presence: a ladder whose rungs can be reordered is a list, and the
# whole point is that a session takes the FIRST rung it can reach and only
# drops to the next when that one is closed.
CONTINUITY_LADDER = ("Rung 1", "Rung 2", "Rung 3")

# The three verdicts Phase C step 3 actually shipped (tools/bm_continue.py,
# commit 9fe992b). The page describes what the product does, so a verdict this
# page invents, or one it drops after the code gains it, fails here.
LIVENESS_VERDICTS = ("SPOKE", "RUNNING", "GONE")


class TestTheContinuityPageStatesTheContract(unittest.TestCase):
    """Protects: Phase C step 4 of PLAN.md (2026-08-08). Steps 1 to 3 built the
    verb, wired it into the two closing flows, and made the launch prove the
    successor is alive. What none of them produced is a page a human can read
    to learn the contract, which matters most in the case the whole phase
    exists for: a session at three in the morning whose launch was refused and
    which has to decide what to do instead.

    The page is held to three things a later edit cannot quietly drop: the law
    that silence is forbidden, the ladder of last resorts in order, and an
    honest split between what a machine enforces and what is only discipline.
    That last one is why the class exists at all. A continuity protocol that
    reads as fully mechanical is the overclaim this project's own limits
    register was written to prevent."""

    def test_the_page_states_the_law_and_the_condition(self):
        text = read(CONTINUITY_DOC)
        missing = [phrase for phrase in (CONTINUITY_CONDITION, CONTINUITY_LAW)
                   if phrase not in text]
        self.assertEqual(missing, [], "%s is missing %s"
                         % (CONTINUITY_DOC, "; ".join(repr(m) for m in missing)))

    def test_the_ladder_names_its_rungs_in_order(self):
        text = read(CONTINUITY_DOC)
        positions = []
        for rung in CONTINUITY_LADDER:
            self.assertIn(rung, text, "%s does not name %s of the ladder of "
                                      "last resorts" % (CONTINUITY_DOC, rung))
            positions.append(text.index(rung))
        self.assertEqual(positions, sorted(positions),
                         "the rungs are out of order in %s: a session takes "
                         "the first rung it can reach, so the order IS the "
                         "instruction" % CONTINUITY_DOC)

    def test_the_page_carries_the_three_verdicts_the_launch_returns(self):
        text = read(CONTINUITY_DOC)
        missing = [v for v in LIVENESS_VERDICTS if v not in text]
        self.assertEqual(missing, [], "%s does not name the liveness verdict(s) "
                                      "%s that tools/bm_continue.py returns"
                         % (CONTINUITY_DOC, ", ".join(missing)))

    def test_every_verdict_named_here_is_one_the_code_returns(self):
        """The other direction, and the one that catches a page drifting into
        fiction: a verdict word on the page that no longer exists in the tool
        means the page is describing a product that shipped last week."""
        tool = read(os.path.join("tools", "bm_continue.py"))
        for verdict in LIVENESS_VERDICTS:
            self.assertIn('%s = "%s"' % (verdict, verdict), tool,
                          "%s documents the verdict %s, which tools/"
                          "bm_continue.py does not define"
                          % (CONTINUITY_DOC, verdict))

    def test_the_page_separates_what_is_mechanical_from_what_is_discipline(self):
        text = read(CONTINUITY_DOC)
        for phrase in ("Mechanical", "Discipline"):
            self.assertIn(phrase, text,
                          "%s does not say which half of the protocol is %s. A "
                          "protocol that reads as fully enforced is an "
                          "overclaim." % (CONTINUITY_DOC, phrase.lower()))

    def test_the_page_names_the_command_in_the_forms_a_reader_can_run(self):
        text = read(CONTINUITY_DOC)
        missing = [phrase for phrase in (CONTINUE_PLUGIN_FORM,
                                         CONTINUE_CLONE_FORM,
                                         CONTINUE_PACKAGED_NAME,
                                         CONTINUE_DRY_RUN)
                   if phrase not in text]
        self.assertEqual(missing, [], "%s is missing %s"
                         % (CONTINUITY_DOC, "; ".join(repr(m) for m in missing)))

    def test_readme_points_at_the_page_exactly_once(self):
        """Once, because the plan says one pointer, and because a second copy
        of a pointer is the second place that goes stale. A markdown link
        carries the path twice, in its label and in its target."""
        hits = read("README.md").count("docs/CONTINUITY.md")
        self.assertEqual(2, hits,
                         "README.md should carry exactly one markdown link to "
                         "docs/CONTINUITY.md (label plus target is two "
                         "occurrences of the path); found %d" % hits)


class TestNoDashes(unittest.TestCase):
    """The project's own copy rule, enforced on the files this suite governs."""

    def test_no_em_or_en_dash_in_the_active_pages_or_this_toolchain(self):
        offenders = []
        targets = list(ACTIVE_DOCS) + [os.path.join("tools", "bm_project_facts.py"),
                                       os.path.join("tools", "bm_packs.py"),
                                       os.path.join("tools", "bm_docs.py"),
                                       os.path.join("tools", "bm_docs_export.py"),
                                       os.path.join("tools", "test_bm_docs.py"),
                                       os.path.join("references", "visual-surface.md"),
                                       os.path.join("references", "learned-rules.md"),
                                       os.path.join("tools", "bm_visual.py"),
                                       os.path.join("tools", "bm_view.py"),
                                       os.path.join("tools", "test_bm_visual.py"),
                                       os.path.join("tools", "test_bm_view.py"),
                                       os.path.join("scripts", "benchmark_comparative.py"),
                                       os.path.join("docs", "BENCHMARK-COMPARATIVE.md"),
                                       os.path.join("docs", "FEEDBACK.md"),
                                       # The contributor pack, 2026-08-07. A page
                                       # teaching outsiders the repository's rules
                                       # is the last place a banned character may
                                       # sit, and its own review found one before
                                       # publication, which is why it is pinned
                                       # here rather than trusted.
                                       "CONTRIBUTING.md",
                                       os.path.join("docs", "contributing",
                                                    "EXTENSION-CONTRACT.md"),
                                       os.path.join("docs", "contributing",
                                                    "FIRST-CAPABILITY-TUTORIAL.md"),
                                       os.path.join("docs", "program", "absolute-lead",
                                                    "MASTER-PLAN-2026-08-06.md")]
        # The L05 visual surface design (docs/program/absolute-lead/
        # DESIGN-visual-surface.md, section 13.4) puts five files in this list:
        # the register, which shipped with the design, plus the four L05 tools
        # files, added 2026-08-06 when Writers D and E landed them. All four
        # are verified pure ASCII with hostile fixture characters written as
        # backslash-u escapes, which is what lets this guard and those
        # fixtures hold at once.
        for rel in targets:
            for i, line in enumerate(read(rel).split("\n"), 1):
                if "\u2013" in line or "\u2014" in line:
                    offenders.append("%s:%d" % (rel, i))
        self.assertEqual(offenders, [], "em or en dash found at %s"
                         % ", ".join(offenders))


class TestTheComparisonPageDoesNotRotSilently(unittest.TestCase):
    """docs/ECOSYSTEM.md states prices, licences and product names belonging to
    other people. All of it decays, and one of the six tools it covers had
    already been deprecated and replaced under a nearly similar name before the
    page was first written.

    WHY THIS IS A TEST AND NOT A REMINDER. The refresh procedure
    (docs/ECOSYSTEM-REFRESH.md) is a discipline, and the founder's standing law
    is that a rule no file enforces is written down as UNENFORCED or not
    written at all. This file is that enforcement.

    WHY THIRTY DAYS AND NOT SEVEN. The intended cadence is weekly, but a check
    that fails after one missed week is a tripwire: it turns every unrelated
    piece of work into a documentation errand and teaches people to bypass the
    suite. Thirty days means three missed refreshes before anything blocks,
    which is a real backstop rather than a nuisance. The number is a judgement,
    not a measurement, and it should move if the record shows it is wrong.

    This check FAILS WITH THE PASSAGE OF TIME, deliberately and uniquely in
    this suite. That is the point: staleness is the defect."""

    MAX_AGE_DAYS = 30
    STAMP = re.compile(r"^Last checked:\s*(\d{4})-(\d{2})-(\d{2})\s*\.?\s*$",
                       re.MULTILINE)

    def _stamp_date(self, rel):
        path = os.path.join(ROOT, rel)
        self.assertTrue(os.path.exists(path), "%s is missing" % rel)
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
        m = self.STAMP.search(text)
        self.assertIsNotNone(
            m, "%s carries no 'Last checked: YYYY-MM-DD' line. That line is "
               "how a reader judges for themselves how stale the page is, so "
               "a page without one is worse than a page with an old one."
               % rel)
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def test_the_comparison_page_carries_a_last_checked_date(self):
        self._stamp_date("docs/ECOSYSTEM.md")

    def test_the_refresh_procedure_carries_one_too(self):
        self._stamp_date("docs/ECOSYSTEM-REFRESH.md")

    def test_the_comparison_page_has_been_checked_recently_enough(self):
        stamped = self._stamp_date("docs/ECOSYSTEM.md")
        age = (datetime.date.today() - stamped).days
        self.assertLessEqual(
            age, self.MAX_AGE_DAYS,
            "docs/ECOSYSTEM.md was last checked %d days ago, over the %d day "
            "limit. It states other people's prices, licences and product "
            "names, and every one of those decays. Run the pass in "
            "docs/ECOSYSTEM-REFRESH.md, then update its Last checked line. Do "
            "not update the line without running the pass: a fresh date over "
            "stale content is worse than an honest old date."
            % (age, self.MAX_AGE_DAYS))

    def test_a_future_date_is_refused(self):
        """A stamp cannot be in the future. Guards the failure where somebody
        satisfies the check by typing tomorrow rather than by doing the work."""
        stamped = self._stamp_date("docs/ECOSYSTEM.md")
        self.assertLessEqual(
            stamped, datetime.date.today(),
            "docs/ECOSYSTEM.md claims it was last checked in the future")


class TestThePublicSurfaceCarriesNoOperatorIdentity(unittest.TestCase):
    """This repository is published. Twice now a tracked file has carried the
    identity of the machine that wrote it: absolute home paths naming the
    owner's account, and one operator's private memory vault named by name in
    a sandbox deny rule. Both were found by hand, on the way to a public
    release, which is the wrong way to find them.

    WHAT IS SCANNED, and what deliberately is not. The LIVE SURFACE is every
    tracked file except the dated material under docs/ and CHANGELOG.md. This
    suite already treats those as historical and leaves them alone, for the
    reason given at the top of this file, and the reason holds harder here:
    they quote verbatim command output, so rewriting them to look clean would
    falsify the evidence while removing nothing from a public clone, because
    the same bytes are in git history either way. Live code, scripts, config
    and root pages are different. They are read as instructions, they are
    copied by installers, and nothing about them needs an operator's name.

    THE VAULT RULE NAMES NO VAULT. Pinning the leaked name here would publish
    it again from the very check meant to retire it, so the rule is the shape
    instead: on the live surface a vault path is reached through
    BROTHERMODE_VAULT or written as a placeholder, never spelled out.
    """

    HISTORICAL_PREFIXES = ("docs/",)
    HISTORICAL_FILES = ("CHANGELOG.md",)

    # Account names that are obviously nobody: fixtures, examples and doc
    # placeholders. A path under one of these is a teaching aid. A path under
    # any other account name is a real person's machine, which is the defect.
    PLACEHOLDER_ACCOUNTS = frozenset({
        "f", "j", "jane", "jane.doe", "janedoe", "k", "me", "mueller",
        "someone", "user", "x", "you",
    })

    HOME_PATH = re.compile(r"/(?:Users|home)/([A-Za-z][A-Za-z0-9._-]*)/")
    # A vault name may contain a space, and the one that leaked did: the first
    # draft of this rule excluded whitespace and so missed the exact string it
    # was written to catch. Bounded and non greedy, so it stays inside one
    # path and does not swallow a sentence that happens to end in the word.
    VAULT_PATH = re.compile(r"""Documents/[^"'`\n]{0,60}?[Vv]ault""")

    def _live_files(self):
        code, out, err = _git("ls-files")
        self.assertEqual(code, 0, "git ls-files failed: %s" % err)
        live = []
        for rel in out.split("\n"):
            rel = rel.strip()
            if not rel or rel in self.HISTORICAL_FILES:
                continue
            if rel.startswith(self.HISTORICAL_PREFIXES):
                continue
            live.append(rel)
        self.assertTrue(live, "git ls-files named no live files at all")
        return live

    def _lines(self, rel):
        """Text lines of a tracked file, or nothing when it is not text. A
        binary blob carries no account name a reader could act on, and
        guessing an encoding for one would fail the suite on a PDF."""
        try:
            with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                return fh.read().split("\n")
        except (UnicodeDecodeError, OSError):
            return []

    def test_no_real_account_name_sits_in_an_absolute_home_path(self):
        offenders = []
        for rel in self._live_files():
            for i, line in enumerate(self._lines(rel), 1):
                for account in self.HOME_PATH.findall(line):
                    if account not in self.PLACEHOLDER_ACCOUNTS:
                        offenders.append("%s:%d names account %r"
                                         % (rel, i, account))
        self.assertEqual(
            offenders, [],
            "an absolute home path on the live surface names a real account. "
            "Write it $HOME-relative, or use a placeholder account if the "
            "example needs an absolute path. Found: %s"
            % "; ".join(offenders))

    def test_a_vault_is_reached_through_its_variable_and_not_by_name(self):
        offenders = []
        for rel in self._live_files():
            for i, line in enumerate(self._lines(rel), 1):
                if not self.VAULT_PATH.search(line):
                    continue
                if "BROTHERMODE_VAULT" in line or "<" in line:
                    continue
                offenders.append("%s:%d" % (rel, i))
        self.assertEqual(
            offenders, [],
            "a vault path is spelled out on the live surface. Reach it "
            "through BROTHERMODE_VAULT, or write the name as a placeholder. "
            "Found at %s" % ", ".join(offenders))



class TestTheEntraApprovalContractNamesEveryTier(unittest.TestCase):
    """VB10-05's own done-check: docs/VAULT-ENTRA-APPROVAL-CONTRACT.md names
    each of the four sequential tiers, and states plainly that nothing
    mechanical is built. Driven backwards, per this suite's own convention
    (TestTheComparisonPageDoesNotRotSilently, TestNoUnbackedAbsolutes): a
    doctored copy with one tier's name removed must fail the same check a
    real, complete page passes, so this test is proven to actually look."""

    PAGE = os.path.join("docs", "VAULT-ENTRA-APPROVAL-CONTRACT.md")
    TIERS = ("Requester", "Owner", "Reviewer", "Approver")
    NON_CLAIM = "This is a plan, not a build."

    def _problems(self, text):
        problems = []
        for tier in self.TIERS:
            if tier not in text:
                problems.append("missing tier %r" % tier)
        if self.NON_CLAIM not in text:
            problems.append("missing the non-claim sentence")
        return problems

    def test_the_contract_page_exists(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, self.PAGE)),
                         "%s is missing" % self.PAGE)

    def test_the_contract_page_names_every_tier_and_its_non_claim(self):
        problems = self._problems(read(self.PAGE))
        self.assertEqual(
            problems, [],
            "VB10-05's contract page is missing something its own done-check "
            "requires: %s" % "; ".join(problems))

    def test_a_copy_missing_a_tier_fails_the_same_check(self):
        doctored = read(self.PAGE).replace("Approver", "REDACTED-TIER")
        problems = self._problems(doctored)
        self.assertIn("missing tier 'Approver'", problems,
                      "removing a tier's name did not make this check fail, "
                      "so the check was not actually reading the page")

    def test_a_copy_missing_the_non_claim_sentence_fails_too(self):
        doctored = read(self.PAGE).replace(self.NON_CLAIM, "")
        problems = self._problems(doctored)
        self.assertIn("missing the non-claim sentence", problems)


if __name__ == "__main__":
    unittest.main(verbosity=1)
