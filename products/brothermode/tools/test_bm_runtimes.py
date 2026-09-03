#!/usr/bin/env python3
"""Suite for tools/bm_runtimes.py, the cross runtime adapter generator.

WHAT THIS SUITE IS ACTUALLY DEFENDING
  The generator's output is documentation that other people will act on. The
  failure mode is not a crash: it is a confident sentence that is false. Three
  shapes of false sentence are worth gating, and each has a test below that has
  been shown capable of failing:

    1. An INVENTED COMMAND. The adapter files tell five runtimes which
       BrotherMode commands to run. If one of those names drifts, every adapter
       teaches a command that does not exist, and the agent reading it reports
       a broken install rather than a renamed subcommand.

    2. DRIFT between the registry, the committed adapter files, and the
       capability table in docs/RUNTIMES.md. All three are generated from one
       registry precisely so they cannot disagree, which is only true while
       something checks.

    3. A CONFLATED CAPABILITY CLAIM. "This runtime has PreToolUse" and
       "BrotherMode's fence hook runs in this runtime" are different claims and
       only the first is verified anywhere but Claude Code. An adapter file that
       lost the second half of that sentence would talk a founder into wiring a
       gate that fails open while looking installed.

Python 3.9, standard library only. subprocess is used to drive the real CLI,
which is local execution, not a network call.

No em or en dashes anywhere in this file, its comments, or its output.
"""

import importlib.util
import io
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RT_PATH = os.path.join(HERE, "bm_runtimes.py")

_spec = importlib.util.spec_from_file_location("bm_runtimes_under_test", RT_PATH)
rt_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt_mod)

# Written as escapes on purpose: the no-dash rule applies to this file too, so
# the detector must not be the one place in the repository where a literal em or
# en dash lives. A grep for the character itself should find nothing, including
# here.
DASHES = (chr(0x2014), chr(0x2013))


def _run(args, cwd):
    return subprocess.run([sys.executable, RT_PATH] + list(args), cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def _new_project(d):
    """A throwaway project with a resolvable BrotherMode root, made the way the
    real CLI makes one rather than by hand."""
    subprocess.run(["git", "init", "-q", "."], cwd=d, check=True)
    with io.open(os.path.join(d, ".git", "info", "exclude"), "a") as f:
        f.write("/.brothermode\n")
    subprocess.run([sys.executable, os.path.join(HERE, "bm_store.py"), "init"],
                   cwd=d, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


class TestRegistryIsStructurallySound(unittest.TestCase):

    def test_check_registry_passes_on_the_shipped_registry(self):
        rt_mod._check_registry()

    def test_no_two_runtimes_share_a_staging_filename(self):
        # Several runtimes share a DESTINATION (AGENTS.md is read by Codex,
        # Copilot and Qwen), so the staging names are the only thing keeping
        # one runtime's file from silently overwriting another's.
        names = [r["staging_name"] for r in rt_mod.RUNTIMES]
        self.assertEqual(len(names), len(set(names)), names)

    def test_every_runtime_carries_at_least_one_source_url(self):
        for r in rt_mod.RUNTIMES:
            self.assertTrue(r["sources"], "%s has no source" % r["key"])
            for label, url in r["sources"]:
                self.assertTrue(url.startswith("https://"),
                                "%s: %r is not an https URL" % (r["key"], url))
                self.assertTrue(label.strip(), "%s: source has no label" % r["key"])

    def test_hook_entries_carry_their_own_source(self):
        # The hook facts came off DIFFERENT pages than the instruction file
        # facts for every runtime that has them, so they carry their own URL
        # rather than inheriting one that does not mention hooks at all.
        for r in rt_mod.RUNTIMES:
            if r["hooks"] is None:
                continue
            label, url = r["hooks"]["source"]
            self.assertTrue(url.startswith("https://"), "%s: %r" % (r["key"], url))
            self.assertTrue(r["hooks"]["events"], "%s lists no events" % r["key"])
            self.assertTrue(r["hooks"]["config"].strip())

    def test_calibrated_check_registry_catches_a_duplicate_staging_name(self):
        # CALIBRATION: reinject the defect the guard exists for onto the real
        # product symbol, and prove the guard fails for that reason.
        original = rt_mod.RUNTIMES
        dup = dict(original[1])
        dup["key"] = "clone"
        dup["staging_name"] = original[0]["staging_name"]
        rt_mod.RUNTIMES = original + (dup,)
        try:
            with self.assertRaises(ValueError) as cm:
                rt_mod._check_registry()
            self.assertIn("same staging filename", str(cm.exception))
        finally:
            rt_mod.RUNTIMES = original
        rt_mod._check_registry()  # restored, and passing again

    def test_calibrated_check_registry_catches_an_unverified_entry_with_no_reason(self):
        original = rt_mod.RUNTIMES
        bad = dict(original[0])
        bad["key"] = "mystery"
        bad["staging_name"] = "mystery.md"
        bad["verified"] = False
        bad["reason"] = ""
        rt_mod.RUNTIMES = original + (bad,)
        try:
            with self.assertRaises(ValueError) as cm:
                rt_mod._check_registry()
            self.assertIn("states no reason", str(cm.exception))
        finally:
            rt_mod.RUNTIMES = original
        rt_mod._check_registry()


class TestNoInventedCommandNames(unittest.TestCase):
    """GUARD 1. Every BrotherMode command the adapters teach must be a real
    dispatch key in the module they name."""

    def _assert_surface_is_real(self, surface):
        missing = []
        for mod, _purpose, cmds in surface:
            src = io.open(os.path.join(HERE, mod), encoding="utf-8").read()
            for c in cmds:
                if '"%s"' % c not in src:
                    missing.append("%s: %s" % (mod, c))
        return missing

    def test_every_command_the_adapters_teach_exists_in_its_module(self):
        missing = self._assert_surface_is_real(rt_mod.CLI_SURFACE)
        self.assertEqual(
            missing, [],
            "the adapter files would teach these commands, but they are not "
            "dispatch keys in the named module: %s. Either the command was "
            "renamed (update CLI_SURFACE) or it was invented." % missing)

    def test_the_modules_the_adapters_name_all_exist(self):
        for mod, _p, _c in rt_mod.CLI_SURFACE:
            self.assertTrue(os.path.exists(os.path.join(HERE, mod)),
                            "CLI_SURFACE names %s, which does not exist" % mod)

    def test_calibrated_an_invented_command_is_caught(self):
        # CALIBRATION: put a command that does not exist into the real product
        # symbol's shape and prove the checker reports it. Without this, a
        # checker that silently found nothing would pass forever.
        fake = (("bm_store.py", "made up", ("claim", "definitely-not-a-command")),)
        missing = self._assert_surface_is_real(fake)
        self.assertEqual(missing, ["bm_store.py: definitely-not-a-command"])
        # restore is implicit (the real symbol was never mutated), so re-prove
        # the real surface still passes
        self.assertEqual(self._assert_surface_is_real(rt_mod.CLI_SURFACE), [])


class TestCommittedOutputMatchesTheRegistry(unittest.TestCase):
    """GUARD 2, the drift gate. docs/RUNTIMES.md and docs/runtimes/*.md are
    committed, so a reader sees them without running anything. That is only
    safe while they are what the generator would produce right now."""

    def test_check_passes_against_the_real_repository(self):
        r = _run(["check", "--root", ROOT], ROOT)
        self.assertEqual(r.returncode, 0,
                         "committed adapters or docs/RUNTIMES.md have drifted "
                         "from tools/bm_runtimes.py. Run `python3 "
                         "tools/bm_runtimes.py emit`.\n%s%s" % (r.stdout, r.stderr))
        self.assertIn("check OK", r.stdout)

    def test_calibrated_a_hand_edited_capability_table_is_reported_stale(self):
        # CALIBRATION: reinject the exact defect (a capability table that no
        # longer matches the registry) by making the REAL renderer produce
        # different bytes, and prove `check` names the file and exits nonzero.
        original = rt_mod.render_runtimes_doc
        rt_mod.render_runtimes_doc = lambda tp: original(tp) + "\nhand edited\n"
        try:
            code = rt_mod.cmd_check(["--root", ROOT])
        finally:
            rt_mod.render_runtimes_doc = original
        self.assertEqual(code, 1, "REINJECTION CHECK: with the renderer and the "
                                  "committed file disagreeing, check must fail")
        # RESTORE, and prove it passes again for the right reason.
        self.assertEqual(rt_mod.cmd_check(["--root", ROOT]), 0)

    def test_calibrated_a_missing_adapter_file_is_reported(self):
        original = rt_mod.RUNTIMES
        ghost = dict(original[0])
        ghost["key"] = "ghost"
        ghost["staging_name"] = "ghost.NOT-EMITTED.md"
        rt_mod.RUNTIMES = original + (ghost,)
        try:
            code = rt_mod.cmd_check(["--root", ROOT])
        finally:
            rt_mod.RUNTIMES = original
        self.assertEqual(code, 1, "REINJECTION CHECK: a registry entry with no "
                                  "committed file must be reported MISSING")
        self.assertEqual(rt_mod.cmd_check(["--root", ROOT]), 0)


class TestCapabilityClaimsStaySeparate(unittest.TestCase):
    """GUARD 3. 'has hook points' and 'BrotherMode's hooks work here' are two
    claims, and only the first is verified outside Claude Code."""

    def _render(self, r):
        return rt_mod.render_runtime_file(r, "tools")

    def test_a_runtime_with_hook_points_never_claims_brothermode_hooks_work(self):
        for r in rt_mod.RUNTIMES:
            if r["hooks"] is None:
                continue
            text = self._render(r)
            self.assertIn("UNVERIFIED", text,
                          "%s lists hook points; the file must say BrotherMode "
                          "hook compatibility is unverified" % r["key"])
            self.assertIn("docs/HOOKS.md", text,
                          "%s must point at the contract its hooks would have "
                          "to match" % r["key"])

    def test_a_runtime_without_hook_points_says_the_fence_is_advisory(self):
        for r in rt_mod.RUNTIMES:
            if r["hooks"] is not None:
                continue
            text = self._render(r)
            self.assertIn("advisory", text.lower(),
                          "%s has no hook points, so the file must not let a "
                          "reader believe the fence is enforced there" % r["key"])

    def test_every_capability_table_row_names_a_real_instruction_file(self):
        # P16 regression, and the reason this assertion is per CELL rather than
        # per row: the table cell used to be destinations[0].split(".")[0],
        # which truncates a prose sentence at the dot inside the FILENAME.
        # Copilot's cell rendered EMPTY (its path starts ".github/..."), and
        # AGENTS.md, QWEN.md and IFLOW.md all lost their extension. Every row
        # was present the whole time, so a row count test passed throughout.
        doc = rt_mod.render_runtimes_doc("tools")
        rows = {}
        for line in doc.splitlines():
            if line.startswith("| ") and line.count("|") >= 6:
                cells = [c.strip() for c in line.strip("|").split("|")]
                rows[cells[0]] = cells[1]
        for r in rt_mod.RUNTIMES:
            self.assertIn(r["title"], rows)
            cell = rows[r["title"]]
            self.assertTrue(cell, "%s has an EMPTY instruction file cell; a "
                                  "founder reads that as 'no instruction file'"
                                  % r["title"])
            self.assertIn(r["install_file"], cell,
                          "%s cell %r does not carry the registry's "
                          "install_file %r" % (r["title"], cell,
                                               r["install_file"]))
            self.assertTrue(cell.count(".md") >= 1,
                            "%s cell %r lost its file extension" % (r["title"],
                                                                    cell))

    def test_the_capability_table_has_one_row_per_runtime_plus_claude_code(self):
        doc = rt_mod.render_runtimes_doc("tools")
        for r in rt_mod.RUNTIMES:
            self.assertIn("| %s |" % r["title"], doc,
                          "%s is missing from the capability table" % r["title"])
        self.assertIn("| Claude Code |", doc,
                      "the one runtime with verified hook support must be in "
                      "the table, or the table reads as if nothing works")

    def test_every_generated_file_carries_its_source_url(self):
        for r in rt_mod.RUNTIMES:
            text = self._render(r)
            for _label, url in r["sources"]:
                self.assertIn(url, text,
                              "%s does not cite %s" % (r["key"], url))
            # Each entry renders ITS OWN read date: the registry-wide date
            # for the original entries, a per-entry verified_on for anything
            # read later. Asserting the shared date everywhere would force
            # either an understated new entry or a falsely bumped old one,
            # which is the exact dishonesty the per-entry date removed.
            self.assertIn(rt_mod.verified_on(r), text,
                          "%s does not carry its own source date %s"
                          % (r["key"], rt_mod.verified_on(r)))

    def test_an_unverified_runtime_ships_marked_generic_with_its_reason(self):
        # No shipped runtime is unverified today, so the GENERIC path would
        # otherwise be dead code that nobody notices is broken until the first
        # time it matters.
        synth = dict(rt_mod.RUNTIMES[0])
        synth["key"] = "synthetic"
        synth["title"] = "Synthetic Runtime"
        synth["verified"] = False
        synth["reason"] = "no vendor documentation page could be opened"
        text = rt_mod.render_runtime_file(synth, "tools")
        self.assertIn("STATUS: GENERIC, NOT VERIFIED.", text)
        self.assertIn("no vendor documentation page could be opened", text)
        self.assertIn("Treat the destination below as a guess", text)

    def test_generated_output_contains_no_em_or_en_dashes(self):
        blobs = [rt_mod.render_runtimes_doc("tools")]
        blobs += [self._render(r) for r in rt_mod.RUNTIMES]
        blobs.append(io.open(RT_PATH, encoding="utf-8").read())
        for b in blobs:
            for d in DASHES:
                self.assertNotIn(d, b, "an em or en dash reached generated output")

    def test_generated_files_teach_no_flag_names(self):
        # The adapters name subcommands and tell the agent to run --help. A
        # generated "--files" that got renamed is exactly the invented flag
        # problem this project already shipped once.
        for r in rt_mod.RUNTIMES:
            text = self._render(r)
            for line in text.splitlines():
                for tok in line.split():
                    # `-->` closes the generated HTML header comments and is
                    # not a flag; a flag has a letter after the two dashes.
                    if tok.startswith("--") and len(tok) > 2 and tok[2].isalpha():
                        self.assertIn(
                            tok.strip(".,`"), ("--help", "--runtime"),
                            "%s teaches the flag %s; adapters may only name "
                            "--help and the regeneration command" % (r["key"], tok))


class TestEmitIsNonDestructiveAndRefusesCleanly(unittest.TestCase):

    def test_emit_never_writes_the_destination_paths(self):
        # The install map is advice, not an action. A generator that wrote
        # AGENTS.md at a repository root would destroy real content the first
        # time somebody ran it in a project that already had one.
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            agents = os.path.join(d, "AGENTS.md")
            with io.open(agents, "w", encoding="utf-8") as f:
                f.write("FOUNDER CONTENT THAT MUST SURVIVE\n")
            os.makedirs(os.path.join(d, ".github"))
            copilot = os.path.join(d, ".github", "copilot-instructions.md")
            with io.open(copilot, "w", encoding="utf-8") as f:
                f.write("EXISTING COPILOT RULES\n")
            r = _run(["emit"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(io.open(agents).read(),
                             "FOUNDER CONTENT THAT MUST SURVIVE\n")
            self.assertEqual(io.open(copilot).read(), "EXISTING COPILOT RULES\n")
            for rr in rt_mod.RUNTIMES:
                self.assertTrue(os.path.exists(
                    os.path.join(d, "docs", "runtimes", rr["staging_name"])))

    def test_emit_refuses_to_overwrite_a_hand_written_capability_table(self):
        # P16 regression. cmd_emit computed the capability table path from ROOT
        # unconditionally, so a founder's hand written docs/RUNTIMES.md was
        # destroyed with no backup and no warning, WHILE the banner printed
        # "an existing AGENTS.md or rules file is never clobbered".
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            os.makedirs(os.path.join(d, "docs"))
            doc = os.path.join(d, "docs", "RUNTIMES.md")
            with io.open(doc, "w", encoding="utf-8") as f:
                f.write("FOUNDER HAND WRITTEN RUNTIMES NOTES\n")
            r = _run(["emit"], d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("refused", r.stderr)
            self.assertEqual(io.open(doc, encoding="utf-8").read(),
                             "FOUNDER HAND WRITTEN RUNTIMES NOTES\n",
                             "the founder's file was overwritten")
            self.assertFalse(os.path.exists(os.path.join(d, "docs", "runtimes")),
                             "a refused emit must write nothing at all, not "
                             "part of the staging directory")

    def test_out_keeps_every_written_file_inside_the_named_directory(self):
        # P16 regression. --out redirected the adapter files only; the
        # capability table still went to <root>/docs/RUNTIMES.md, a file
        # OUTSIDE the directory the founder named as the scratch target.
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            staging = os.path.join(d, "scratch")
            os.makedirs(staging)
            r = _run(["emit", "--out", staging], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.exists(os.path.join(staging, "RUNTIMES.md")))
            self.assertFalse(
                os.path.exists(os.path.join(d, "docs", "RUNTIMES.md")),
                "--out must not write anything outside the named directory")
            self.assertEqual(_run(["check", "--out", staging], d).returncode, 0,
                             "check must look where emit actually wrote")

    def test_emit_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            _run(["emit"], d)
            first = {}
            base = os.path.join(d, "docs", "runtimes")
            for n in sorted(os.listdir(base)):
                first[n] = io.open(os.path.join(base, n), encoding="utf-8").read()
            _run(["emit"], d)
            for n, text in first.items():
                self.assertEqual(
                    io.open(os.path.join(base, n), encoding="utf-8").read(), text,
                    "%s changed between two emits; a nondeterministic generator "
                    "makes the drift gate useless" % n)

    def test_emit_leaves_no_temp_files_behind(self):
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            _run(["emit"], d)
            leftovers = [n for n in os.listdir(os.path.join(d, "docs", "runtimes"))
                         if n.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_an_unrecognized_flag_is_refused_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            r = _run(["emit", "--runtimes", "codex"], d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("unrecognized flag", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "docs", "runtimes")))

    def test_an_unknown_runtime_is_refused_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            r = _run(["emit", "--runtime", "not-a-real-runtime"], d)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("unknown runtime", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "docs", "runtimes")))

    def test_a_partial_emit_does_not_rewrite_the_capability_table(self):
        # The table describes EVERY runtime. Rewriting it from a one runtime
        # emit would silently drop five rows.
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            r = _run(["emit", "--runtime", "codex"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "docs", "RUNTIMES.md")))
            self.assertIn("capability table NOT refreshed", r.stdout)

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            r = _run(["emit", "--dry-run"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "docs", "runtimes")))
            self.assertIn("DRY RUN", r.stdout)

    def test_emit_refuses_outside_a_brothermode_project(self):
        with tempfile.TemporaryDirectory() as d:
            env_free = os.path.join(d, "nothing", "here")
            os.makedirs(env_free)
            r = subprocess.run(
                [sys.executable, RT_PATH, "emit"], cwd=env_free,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True,
                env=dict((k, v) for k, v in os.environ.items()
                         if k != "BROTHERMODE_ROOT"))
            # A tmpdir is not a git repository and has no marker, so nothing
            # anchors a project. The refusal must be explicit, not a silent
            # emit into the current directory.
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("refused", r.stderr)

    def test_root_resolution_does_not_depend_on_the_calling_subdirectory(self):
        with tempfile.TemporaryDirectory() as d:
            _new_project(d)
            sub = os.path.join(d, "a", "b")
            os.makedirs(sub)
            top = _run(["emit", "--dry-run"], d)
            nested = _run(["emit", "--dry-run"], sub)
            self.assertEqual(top.stdout.splitlines()[0],
                             nested.stdout.splitlines()[0],
                             "the resolved root must not depend on cwd")


class TestListAndHelp(unittest.TestCase):

    def test_list_json_names_every_runtime_and_its_hook_status(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            r = _run(["list", "--json"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(len(data["runtimes"]), len(rt_mod.RUNTIMES))
            for row in data["runtimes"]:
                self.assertIn(row["brothermode_hooks"],
                              ("unverified", "not applicable"),
                              "no row may claim BrotherMode hooks are verified "
                              "outside Claude Code: %r" % row)

    def test_list_works_with_no_project_at_all(self):
        # `list` is pure registry, so it must answer from anywhere. A founder
        # evaluating BrotherMode has not run init yet.
        with tempfile.TemporaryDirectory() as d:
            r = _run(["list"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Codex", r.stdout)

    def test_help_states_the_real_default_staging_directory(self):
        # P16 regression. main() prints the module docstring verbatim as the
        # primary help surface, and that docstring said the default staging
        # directory was ".brothermode/runtimes" while DEFAULT_STAGING was
        # "docs/runtimes". Those differ in the way that matters most here:
        # .brothermode is git excluded, docs/runtimes is committed and ships.
        doc = rt_mod.__doc__ or ""
        self.assertIn(rt_mod.DEFAULT_STAGING.replace(os.sep, "/"), doc,
                      "the help text must name the directory emit actually "
                      "writes to")
        self.assertNotIn(".brothermode/runtimes", doc,
                         "the help text names a directory emit never uses")
        with tempfile.TemporaryDirectory() as d:
            r = _run(["--help"], d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(rt_mod.DEFAULT_STAGING.replace(os.sep, "/"), r.stdout)

    def test_unknown_command_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            r = _run(["install"], d)
            self.assertEqual(r.returncode, 2)
            self.assertIn("unknown command", r.stderr)


class TestEveryTaughtCommandIsRunnableFromAUsersOwnProject(unittest.TestCase):
    """GUARD 4, and the highest damage defect this generator ever shipped.

    The adapter files are not documentation, they are the installed product: a
    Codex agent reads one and obeys it. The shipped Codex adapter said "Run them
    from the project root. Paths below are relative to that root" and then
    printed `python3 tools/bm_store.py`. `tools/` is relative to the BROTHERMODE
    CHECKOUT, never to the user's own project, so the first instruction the
    agent followed failed:

        $ python3 tools/bm_store.py dashboard
        can't open file '<project>/tools/bm_store.py': [Errno 2] No such file or
        directory

    Reproduced independently by two lanes on 2026-08-05 and again in
    docs/program/absolute-lead/evidence/RED-codex-runtime.txt before this guard
    was written."""

    # Only RUNNABLE instructions are gated. A prose reference to
    # tools/bm_fence_hook.py is not something an agent types.
    RUNNABLE = re.compile(r"python3 (\S*bm_\w+\.py)")
    # The one place a bare relative path is allowed to appear is the labelled
    # counterexample that teaches the agent to RECOGNIZE this failure.
    COUNTEREXAMPLE_MARKERS = ("WRONG", "[Errno 2]")

    def _lines(self, text):
        for line in text.splitlines():
            if any(m in line for m in self.COUNTEREXAMPLE_MARKERS):
                continue
            yield line

    def test_no_adapter_tells_an_agent_to_run_a_checkout_relative_path(self):
        for r in rt_mod.RUNTIMES:
            text = rt_mod.render_runtime_file(r, "tools")
            for line in self._lines(text):
                for path in self.RUNNABLE.findall(line):
                    self.assertTrue(
                        path.startswith("/") or path.startswith(rt_mod.CHECKOUT_TOKEN),
                        "%s tells the agent to run %r, which only resolves "
                        "inside the BrotherMode checkout. In a user's own "
                        "project it fails with [Errno 2]. Line: %s"
                        % (r["key"], path, line.strip()))

    def test_an_absolute_tools_path_is_rendered_as_is(self):
        # When emit runs from a project that is NOT the checkout,
        # tools_reference() already returns an absolute path, and the adapter
        # must then be literally runnable rather than carrying a placeholder.
        text = rt_mod.render_runtime_file(rt_mod.by_key("codex"),
                                          "/opt/brothermode/tools")
        self.assertIn("python3 /opt/brothermode/tools/bm_store.py", text)
        self.assertNotIn(rt_mod.CHECKOUT_TOKEN, text,
                         "an absolute tools path needs no placeholder")

    def test_no_adapter_claims_the_paths_are_relative_to_the_project_root(self):
        for r in rt_mod.RUNTIMES:
            text = rt_mod.render_runtime_file(r, "tools")
            self.assertNotIn("relative to that root", text,
                             "%s repeats the sentence that caused the Errno 2"
                             % r["key"])

    def test_every_adapter_separates_the_checkout_from_the_users_project(self):
        for r in rt_mod.RUNTIMES:
            text = rt_mod.render_runtime_file(r, "tools")
            self.assertIn("BROTHERMODE CHECKOUT", text,
                          "%s never names the checkout, so a reader cannot "
                          "tell which directory the paths belong to" % r["key"])
            self.assertIn("YOUR PROJECT", text, r["key"])
            self.assertIn("[Errno 2]", text,
                          "%s should show the failure it is preventing, so an "
                          "agent that hits it recognizes it" % r["key"])

    def test_every_adapter_shows_an_absolute_path_example(self):
        for r in rt_mod.RUNTIMES:
            text = rt_mod.render_runtime_file(r, "tools")
            self.assertIn(rt_mod.ABSOLUTE_EXAMPLE, text,
                          "%s shows no absolute path example" % r["key"])


class TestCommandListsComeFromTheToolsThemselves(unittest.TestCase):
    """GUARD 5. The adapters used to carry HAND MAINTAINED command tuples, and
    they drifted: on 2026-08-05 the Codex adapter named 11 bm_store commands
    where the tool dispatches 13, 7 bm_learn commands where the tool dispatches
    40, and did not mention bm_project.py at all, hiding the entire beginner
    lifecycle (start, status, next, task, forecast, review).

    Generating the lists is the fix. This class is what keeps it generated."""

    def test_the_surface_is_discovered_not_typed(self):
        for mod, _purpose, cmds in rt_mod.CLI_SURFACE:
            discovered = rt_mod.discover_commands(os.path.join(HERE, mod))
            self.assertEqual(
                tuple(cmds), tuple(discovered),
                "%s: CLI_SURFACE disagrees with the commands the module "
                "actually dispatches" % mod)
            self.assertTrue(discovered, "%s: nothing discovered" % mod)

    def test_the_beginner_lifecycle_is_reachable_from_every_adapter(self):
        mods = [m for m, _p, _c in rt_mod.CLI_SURFACE]
        self.assertIn("bm_project.py", mods,
                      "the project lifecycle tool is missing from the adapter "
                      "surface, so start/status/next/task/forecast/review are "
                      "invisible to anyone who only reads the adapter")
        for r in rt_mod.RUNTIMES:
            text = rt_mod.render_runtime_file(r, "tools")
            listed = self._commands_for(text, "bm_project.py")
            for c in ("start", "status", "next", "task", "forecast", "review"):
                self.assertIn(c, listed, "%s: bm_project %s not named"
                              % (r["key"], c))

    @staticmethod
    def _commands_for(text, module):
        """The rendered `commands:` line that belongs to one module block."""
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if module in line and line.startswith("`python3 "):
                for follow in lines[i + 1:i + 5]:
                    if follow.strip().startswith("commands:"):
                        raw = follow.split("commands:", 1)[1]
                        return [c.strip().split(" ")[0]
                                for c in raw.split(",") if c.strip()]
        return []

    def test_every_adapter_names_every_command_its_tools_dispatch(self):
        for r in rt_mod.RUNTIMES:
            text = rt_mod.render_runtime_file(r, "tools")
            for mod, _p, _c in rt_mod.CLI_SURFACE:
                expected = set(rt_mod.discover_commands(os.path.join(HERE, mod)))
                listed = set(self._commands_for(text, mod))
                self.assertEqual(
                    expected - listed, set(),
                    "%s: the %s block does not name %s, which the tool "
                    "dispatches" % (r["key"], mod, sorted(expected - listed)))

    def test_a_deprecated_command_is_marked_rather_than_taught_as_equal(self):
        # bm_learn.py `relevant` exits 2 with a DEPRECATED banner. An adapter
        # that lists it beside `lookup` teaches a command that is on its way
        # out, which is exactly the drift Lane D found surviving an update.
        deprecated = rt_mod.discover_deprecated(
            os.path.join(HERE, "bm_learn.py"))
        self.assertIn("relevant", deprecated,
                      "bm_learn.py `relevant` is deprecated in its own source "
                      "and the discovery missed it")
        text = rt_mod.render_runtime_file(rt_mod.by_key("codex"), "tools")
        self.assertIn("relevant (deprecated)", text)

    def test_calibrated_a_tool_that_grows_a_command_makes_the_adapters_stale(self):
        # CALIBRATION: this is the whole point of generating. Grow the surface
        # the way a new subcommand would, and prove the committed adapters are
        # reported stale instead of quietly omitting it.
        original = rt_mod.CLI_SURFACE
        grown = []
        for mod, purpose, cmds in original:
            if mod == "bm_store.py":
                cmds = tuple(cmds) + ("a-command-that-just-landed",)
            grown.append((mod, purpose, tuple(cmds)))
        rt_mod.CLI_SURFACE = tuple(grown)
        try:
            code = rt_mod.cmd_check(["--root", ROOT])
        finally:
            rt_mod.CLI_SURFACE = original
        self.assertEqual(code, 1, "REINJECTION CHECK: a tool that grows a "
                                  "command must make the committed adapters "
                                  "stale, not silently omit it")
        self.assertEqual(rt_mod.cmd_check(["--root", ROOT]), 0)

    def test_discovery_reads_both_dispatch_shapes(self):
        # bm_store, bm_learn, bm_threads and bm_project dispatch through a
        # dict; bm_telemetry dispatches through an if/elif chain on `cmd`.
        # Both shapes are real and both must be readable, or bm_telemetry
        # silently falls back to a hand typed list.
        with tempfile.TemporaryDirectory() as d:
            dict_mod = os.path.join(d, "shape_dict.py")
            with io.open(dict_mod, "w", encoding="utf-8") as f:
                f.write("COMMANDS = {'beta': None, 'alpha': None}\n")
            self.assertEqual(rt_mod.discover_commands(dict_mod),
                             ("alpha", "beta"))
            chain_mod = os.path.join(d, "shape_chain.py")
            with io.open(chain_mod, "w", encoding="utf-8") as f:
                f.write("def main():\n"
                        "    cmd = 'x'\n"
                        "    if cmd == 'zebra':\n"
                        "        pass\n"
                        "    elif cmd == 'ant':\n"
                        "        pass\n")
            self.assertEqual(rt_mod.discover_commands(chain_mod),
                             ("ant", "zebra"))
            empty = os.path.join(d, "shape_none.py")
            with io.open(empty, "w", encoding="utf-8") as f:
                f.write("x = 1\n")
            with self.assertRaises(ValueError):
                rt_mod.discover_commands(empty)


class TestTheFirstRunAdviceActuallyRuns(unittest.TestCase):
    """GUARD 6. The adapters told every runtime to start a session with the
    store's `dashboard` and `verify`. On a fresh project both refuse, because
    no store exists yet, and `init` was named only inside a comma separated
    list. Reproduced in RED-codex-runtime.txt, exit 2 in all three project
    shapes. Codex adds a second trap: its default sandbox is read-only, so
    `init` itself dies with PermissionError."""

    def test_every_adapter_says_to_initialize_before_reading(self):
        for r in rt_mod.RUNTIMES:
            text = rt_mod.render_runtime_file(r, "tools")
            self.assertIn(rt_mod.FIRST_RUN_HEADING, text, r["key"])
            block = text.split(rt_mod.FIRST_RUN_HEADING, 1)[1]
            head = block[:1200]
            self.assertIn("init", head,
                          "%s: the first run block never names init" % r["key"])
            self.assertIn("no-store", head,
                          "%s: the first run block does not show the refusal "
                          "the reader will otherwise hit" % r["key"])

    def test_the_codex_adapter_states_the_sandbox_it_needs(self):
        text = rt_mod.render_runtime_file(rt_mod.by_key("codex"), "tools")
        self.assertIn("workspace-write", text,
                      "the Codex adapter never says Codex must run with a "
                      "writable sandbox; on the default read-only sandbox the "
                      "store dies with PermissionError(1, 'Operation not "
                      "permitted')")
        self.assertIn("-s workspace-write", text)
        self.assertIn("read-only", text)

    def test_a_runtime_with_no_stated_requirements_renders_no_empty_section(self):
        plain = rt_mod.by_key("generic")
        self.assertEqual(plain.get("requirements", ()), ())
        text = rt_mod.render_runtime_file(plain, "tools")
        self.assertNotIn(rt_mod.REQUIREMENTS_HEADING, text)


class TestTheFirstRunSequenceActuallyRuns(unittest.TestCase):
    """GUARD 6b, and the reason it exists is worth keeping.

    The first run block was written to fix instructions that could not run. Its
    own ordered list then closed with `bm_project.py start`, which needs flags
    and exits 2 without them:

        usage: start --project-id ID --name NAME ... --actor-name NAME ...
        bm_project: --project-id is required

    So the block that fixes unrunnable instructions ended with an unrunnable
    instruction. It was caught by a reviewer running the list by hand, not by
    this suite, and "we ran it once" is not a property of the product. This
    class makes it one: it extracts every command line the generator emits into
    a first run block and EXECUTES them, in the printed order, in a throwaway
    project that is not the checkout and has never been initialized."""

    # Commands that legitimately cannot run inside the suite go here BY NAME,
    # each with its reason, so a narrowing is a visible decision rather than a
    # quietly shrinking scan. Empty today: everything the block prints runs
    # offline, locally, and without a store. A candidate for this map would be
    # anything interactive or network bound, for example a command that reaches
    # a git remote.
    NOT_RUNNABLE_IN_TEST = {}

    # The whole point is that the list is not trivially short. If somebody
    # empties the block, the execution loop would pass vacuously.
    MINIMUM_COMMANDS = 4

    @staticmethod
    def _first_run_commands(text):
        """Every runnable command line inside the first run block, in order,
        with its trailing parenthetical comment removed.

        Lines that merely QUOTE a command inside a refusal message do not
        start with `python3`, so they are not picked up: the block's quoted
        `refused (no-store): ...` line is prose about a command, not an
        instruction to run one."""
        block = text.split(rt_mod.FIRST_RUN_HEADING, 1)[1]
        block = block.split("\n## ", 1)[0]
        out = []
        for line in block.splitlines():
            s = line.strip()
            if not s.startswith("python3 "):
                continue
            s = re.sub(r"\s{2,}\(.*$", "", s).strip()
            out.append(s)
        return out

    def _fresh_project(self, d):
        """A project that is NOT the BrotherMode checkout and has NEVER been
        initialized, which is the state the block is written for. The ignore
        rule goes in .git/info/exclude, the same way _new_project does it, so
        the store's git containment check is satisfied without the test owning
        a .gitignore the commands might rewrite."""
        subprocess.run(["git", "init", "-q", "."], cwd=d, check=True)
        with io.open(os.path.join(d, ".git", "info", "exclude"), "a") as f:
            f.write("/.brothermode\n")

    def test_every_first_run_command_runs_exactly_as_printed(self):
        # Group by the SEQUENCE rather than by runtime: the block is shared, so
        # six identical sequences are one thing to execute, and a runtime that
        # ever diverges becomes its own group and gets executed too.
        sequences = {}
        for r in rt_mod.RUNTIMES:
            cmds = tuple(self._first_run_commands(
                rt_mod.render_runtime_file(r, "tools")))
            sequences.setdefault(cmds, []).append(r["key"])
        self.assertTrue(sequences, "no runtime rendered a first run block")

        env = dict((k, v) for k, v in os.environ.items()
                   if k != "BROTHERMODE_ROOT")
        for cmds, keys in sorted(sequences.items(), key=lambda kv: kv[1]):
            self.assertGreaterEqual(
                len(cmds), self.MINIMUM_COMMANDS,
                "the first run block for %s prints %d command(s); a block that "
                "short means this guard is passing on nothing" % (keys, len(cmds)))
            with tempfile.TemporaryDirectory() as d:
                self._fresh_project(d)
                for line in cmds:
                    reason = self.NOT_RUNNABLE_IN_TEST.get(line)
                    if reason:
                        continue
                    # SPLIT first, substitute after (2026-08-05 Windows
                    # audit, F11). shlex.split is POSIX-mode by default, so
                    # substituting the checkout root FIRST handed it a
                    # string full of backslashes on Windows and every one
                    # of them was eaten as an escape, leaving a wrong argv.
                    # The printed line itself carries only the <checkout>
                    # placeholder, which has nothing shlex can misread, so
                    # splitting first takes the platform out of the
                    # question rather than branching on it. Fixed because
                    # it is cheap; priority LOW by CI's own shape, since
                    # the `suite` job that runs this file is ubuntu and
                    # macos only (.github/workflows/tests.yml) and this
                    # line has never had a Windows reader. It becomes a
                    # push blocker the day this suite joins the `store`
                    # job.
                    argv = [a.replace(rt_mod.CHECKOUT_TOKEN, ROOT)
                            for a in shlex.split(line)]
                    self.assertEqual(argv[0], "python3", line)
                    argv[0] = sys.executable
                    proc = subprocess.run(argv, cwd=d, env=env,
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE,
                                          universal_newlines=True)
                    self.assertEqual(
                        proc.returncode, 0,
                        "%s: the first run block tells the reader to run\n"
                        "    %s\nand it exits %d in a fresh project. Every line "
                        "in that ordered list must run exactly as printed.\n"
                        "stdout: %s\nstderr: %s"
                        % (keys, line, proc.returncode,
                           proc.stdout.strip()[:400], proc.stderr.strip()[:400]))

    def test_calibrated_an_unrunnable_line_in_the_block_is_caught(self):
        # CALIBRATION: reinject the exact defect a reviewer found, a bare
        # `bm_project.py start`, and prove the executor fails on it. Without
        # this, a scan that quietly matched nothing would pass forever.
        original = rt_mod._first_run_block

        def broken(tools_path):
            lines = original(tools_path)
            return [l.replace("bm_project.py start --help", "bm_project.py start")
                    for l in lines]

        rt_mod._first_run_block = broken
        try:
            with self.assertRaises(AssertionError) as cm:
                self.test_every_first_run_command_runs_exactly_as_printed()
            self.assertIn("must run exactly as printed", str(cm.exception))
        finally:
            rt_mod._first_run_block = original
        # RESTORED, and passing again for the right reason.
        self.test_every_first_run_command_runs_exactly_as_printed()

    def test_an_excluded_command_must_carry_a_reason(self):
        for line, reason in self.NOT_RUNNABLE_IN_TEST.items():
            self.assertTrue(line.startswith("python3 "), line)
            self.assertTrue(reason and reason.strip(),
                            "%s is excluded with no reason" % line)

    def test_the_block_ends_by_pointing_at_the_tools_own_flags(self):
        # The adapters may name no flag but --help (see
        # test_generated_files_teach_no_flag_names), so the ordered list closes
        # on the command that makes the TOOL print the flags `start` needs.
        for r in rt_mod.RUNTIMES:
            cmds = self._first_run_commands(
                rt_mod.render_runtime_file(r, "tools"))
            self.assertTrue(cmds[-1].endswith("bm_project.py start --help"),
                            "%s: %r" % (r["key"], cmds[-1]))


class TestTheCodexHookAnswerIsTheMeasuredOne(unittest.TestCase):
    """GUARD 7. `docs/RUNTIMES.md` said "UNVERIFIED, payload shape not
    captured" for Codex. On 2026-08-05, on codex-cli 0.146.0, Lane C captured
    every payload. The honest replacement is not "yes": Codex reports every
    file write as tool_name "Bash" with an apply_patch heredoc, so the
    matcher shipped that day never fired and bm_fence_hook.py exited 0
    silently. The same lane proved a PreToolUse deny in the right shape DOES
    block a command, so the primitive exists. L06 (2026-08-06) widened the
    matcher to Bash and the hook now parses apply_patch envelopes, proven in
    process against the captured payload; the live wired rehearsal has not
    run, and this guard now holds the record to exactly that split."""

    def test_the_codex_entry_carries_dated_measured_evidence(self):
        m = rt_mod.by_key("codex").get("measured")
        self.assertTrue(m, "Codex carries no measured hook evidence")
        for field in ("captured_on", "runtime_version", "cell", "findings",
                      "evidence"):
            self.assertTrue(m.get(field), "measured.%s is empty" % field)
        self.assertIn("0.146.0", m["runtime_version"])
        self.assertNotIn("|", m["cell"],
                         "the table cell would break the markdown table")

    def test_the_capability_table_no_longer_calls_codex_uncaptured(self):
        doc = rt_mod.render_runtimes_doc("tools")
        row = [l for l in doc.splitlines()
               if l.startswith("| OpenAI Codex CLI |")]
        self.assertEqual(len(row), 1, doc)
        cells = [c.strip() for c in row[0].strip("|").split("|")]
        hooks_cell = cells[-1]
        self.assertNotIn("payload shape not captured", hooks_cell)
        self.assertIn("0.146.0", hooks_cell)
        cli_cell = cells[2]
        self.assertNotEqual(cli_cell, "yes",
                            "the CLI cell hides that Codex needs an absolute "
                            "path and a writable sandbox")
        self.assertIn("absolute", cli_cell.lower())
        self.assertIn("workspace-write", cli_cell)

    def test_the_doc_no_longer_says_no_payload_was_ever_captured(self):
        doc = rt_mod.render_runtimes_doc("tools")
        self.assertNotIn("Nobody has captured a real payload from any other "
                         "runtime", doc)

    def test_the_doc_states_the_cli_caveats_it_used_to_promise_away(self):
        doc = rt_mod.render_runtimes_doc("tools")
        self.assertNotIn("They are ordinary local processes, so they work in "
                         "any runtime that can run a shell command.", doc)
        self.assertIn("PermissionError", doc)
        self.assertIn("[Errno 2]", doc)

    def test_nothing_anywhere_overclaims_the_fence_in_codex(self):
        # Pre-L06 this asserted "does not transfer" outright. L06 landed the
        # apply_patch matcher, so the honest sentence became a split: the
        # 2026-08-05 measurement stays on the record ("did not transfer"),
        # the in-process proof is named, and the live half stays UNVERIFIED
        # until a wired Codex session rehearses the deny. The teeth this
        # guard keeps: the split must be present, and an unqualified success
        # claim is still banned everywhere.
        blobs = [rt_mod.render_runtimes_doc("tools"),
                 rt_mod.render_runtime_file(rt_mod.by_key("codex"), "tools")]
        for b in blobs:
            low = b.lower()
            self.assertIn("did not transfer", low)
            self.assertIn("apply_patch", low)
            self.assertIn("in process", low)
            self.assertIn("UNVERIFIED", b)
            for claim in ("fence is enforced", "fence works in codex",
                          "one writer promise holds",
                          "fence now works", "fully enforced under codex"):
                self.assertNotIn(claim, low)

    def test_the_codex_adapter_says_the_primitive_exists(self):
        text = rt_mod.render_runtime_file(rt_mod.by_key("codex"), "tools")
        self.assertIn("deny", text.lower())
        self.assertIn("UNVERIFIED", text,
                      "five of the eleven events were never observed firing, "
                      "so the file must still carry an unverified half")


if __name__ == "__main__":
    unittest.main(verbosity=1)
