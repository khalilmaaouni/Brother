#!/usr/bin/env python3
"""Fixtures for `--cwd`, centralized across every `sbe` subcommand.

Run: python3 tools/test_sbe_cwd.py

WHY THIS EXISTS. `--cwd <repo>` was accepted by `sbe work start`, `sbe work
finish`, `sbe task open` and `sbe evidence run`, and refused by nearly
everything else: `./bin/sbe status --cwd .` printed `unrecognized arguments:
--cwd`. The fix centralizes the flag in `brothersbe.cwd` (`add_cwd_argument`,
`resolve`, `resolve_relative`, `split`) rather than re-wiring it once per
command, so this file enumerates the commands from the PARSERS THEMSELVES
(`cli.COMMANDS`, and each nested module's own `_parser()`) rather than a
hand-typed list: a command added later without `--cwd` cannot quietly dodge
this fixture the way most of the CLI dodged it before this fix.

TWO CLASSES, two different claims:

  - `TestEveryCommandAcceptsCwd`: `--cwd` PARSES on every subcommand, at
    every depth (`sbe work lease acquire` is two levels deep). Two DIFFERENT
    probes back this, deliberately, because the obvious single probe
    (`<subcommand path...> --cwd <path> -h`) turned out to prove nothing:
    argparse's help action fires the instant it sees `-h` REGARDLESS of
    whether some OTHER token earlier in the same argv was unrecognized, so
    a parser missing `--cwd` entirely still exits 0 and prints usage when
    `-h` rides along with it. That false-negative was caught by this
    file's own calibration (below) after the first version of this test
    passed even with `--cwd` fully removed from `sbe status`'s argument
    definition. The fix is two real probes, matched to how each command
    family actually parses:

    - Every subcommand backed by a real `argparse` parser (every command
      NOT in `cli.PASSTHROUGH`, plus every PASSTHROUGH command routed to a
      package module rather than a `tools/*.py` script: `task`, `work`,
      `evidence`, `handover`, `map`, `book`, `pr`, `protections`, `explain`,
      `lineage`) is checked by READING THE PARSER OBJECT ITSELF: `--cwd` in
      one of its own actions' `option_strings`. No subprocess, no `-h`, and
      nothing to be fooled by, because this reads the exact object that
      would parse a real invocation rather than inferring its behavior from
      one flag's side effect.
    - The remainder of `cli.PASSTHROUGH` hands its whole argv to a
      `tools/*.py` script this package cannot edit (`design`, `gate`,
      `score`, `intake`, `decide`, `fences`, `plan`, `instruction-surface`,
      `scope`); there is no parser object to read, so these are checked
      BEHAVIORALLY instead, with `-h` deliberately LEFT OUT of the probe:
      `<name> --cwd <path>` alone, and the assertion is that the OLD refusal
      text (`unrecognized flag`, `not an option`, `unrecognized argument`)
      never appears. Every one of these scripts is read-only against
      whatever it is pointed at ("writes: nothing", each one's own usage
      text says so), so running the real thing rather than `-h` costs
      nothing and actually exercises the flag-parsing path this defect
      lived in.

  - `TestObservableCommandsHonourCwd`: for the subcommands the founder's
    brief names as observable (`status`, `review`, `handover prepare`,
    `plan`, `evidence run`, `task open`, `work start`), the command actually
    reads the NAMED repository, never the directory the caller happens to be
    standing in. Every test runs from `self.foreign`, a plain temp directory
    that is neither `self.target` nor this project's own checkout, so a
    command that silently fell back to `os.getcwd()` lands somewhere with
    none of `self.target`'s content, distinguishable by construction. None
    of these use `-h` either, for the identical reason.

CALIBRATION (done by hand, not re-run here: re-injecting a defect belongs in
a throwaway edit, never in the fixture that is supposed to catch it for
good). Two rounds, because the first probe design was itself the first
thing calibration caught:

  round 1: `--cwd` was removed from `sbe status`'s own argument definition
  in `cli.py` (the exact repro the brief quotes). With the ORIGINAL
  `--cwd ... -h` probe, `TestEveryCommandAcceptsCwd` stayed GREEN, a false
  negative: `sbe status --cwd . -h` still exits 0 because `-h` answers
  before the unrecognized `--cwd` is ever reported. `TestObservableCommandsHonourCwd`
  DID go red on `test_status_reads_the_cwd_repo_not_the_callers`, with the
  exact repro text (`unrecognized arguments: --cwd`) in its failure message.
  The change was reverted and the observable test passed again.

  round 2, after rewriting the probe to the static-inspection design above:
  `--cwd` was removed from `sbe status` again. This time
  `TestEveryCommandAcceptsCwd` also went red (`declares_cwd` false for the
  `status` leaf parser), alongside the same observable-test failure. The
  change was reverted and both classes passed again, green.

See the session's own return message for the exact commands and their output.
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SBE = os.path.join(ROOT, "bin", "sbe")
SRC = os.path.join(ROOT, "src")


def git(cwd, *args):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, out.stderr))
    return out.stdout.strip()


def write(cwd, rel, body):
    path = os.path.join(cwd, rel)
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


def run_sbe(argv, cwd):
    """Run the real `bin/sbe` as a subprocess with its OS-level working
    directory set to `cwd`: never this test file's own repository, and never
    a fixture's target repository either, so a command that silently fell
    back to its own process cwd instead of reading `--cwd` is caught rather
    than accidentally landing on the right answer by coincidence."""
    return subprocess.run([sys.executable, SBE] + list(argv), cwd=cwd,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)


def new_repo():
    """A fresh git repository, in its own temp directory, with one commit."""
    repo = tempfile.mkdtemp()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "fixture@example.invalid")
    git(repo, "config", "user.name", "fixture")
    write(repo, "README.md", "base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def cli_module():
    """Import `brothersbe.cli` fresh and return it, the same way
    `TestHelpMeansHelpOnEveryCommand` in test_sbe.py already does, so
    `COMMANDS` is read from the CLI's own source of truth rather than
    retyped here."""
    sys.path.insert(0, SRC)
    try:
        import brothersbe.cli as cli
        return cli
    finally:
        sys.path.remove(SRC)


def module_parser(dotted, attr):
    """Import `brothersbe.<dotted>` fresh and return the parser `attr()`
    (that module's own parser-building function) constructs: the same
    introspection point that module's own `main()` calls."""
    sys.path.insert(0, SRC)
    try:
        mod = __import__("brothersbe.%s" % dotted, fromlist=["*"])
        return getattr(mod, attr)()
    finally:
        sys.path.remove(SRC)


def declares_cwd(parser):
    """True when `parser` ITSELF (not a parent, not a child) declares
    `--cwd` as one of its own actions. The one direct, unfoolable read of
    "does this surface accept --cwd": no subprocess, no `-h`, nothing for a
    help action firing early to mask."""
    return any("--cwd" in a.option_strings for a in parser._actions)


def leaf_parsers(parser, prefix=()):
    """Every `(subcommand prefix, leaf parser)` under `parser`, walking
    argparse subparsers recursively (`work lease acquire` is two levels
    deep), read from the parser itself rather than a hand-typed list: a
    subcommand added later, at any depth, is walked the same way without
    this file changing. A parser with no subparsers of its own IS the leaf,
    at whatever prefix reached it."""
    sub_action = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub_action = action
            break
    if sub_action is None or not sub_action.choices:
        return [(prefix, parser)]
    out = []
    for name, child in sub_action.choices.items():
        out.extend(leaf_parsers(child, prefix + (name,)))
    return out


#: Top-level `sbe` commands that route to a PACKAGE MODULE with its own real
#: `argparse` parser, rather than to a `tools/*.py` script this package
#: cannot edit: `("module", "parser-function-name")`. `cli.py`'s own
#: `build_parser()` stops at ONE level for every `PASSTHROUGH` name (real
#: dispatch for those bypasses that parser entirely -- see PASSTHROUGH's own
#: docstring in cli.py), so reading the REAL parser for one of these means
#: importing the owning module and calling its own parser-building function,
#: never `cli.build_parser()`.
NESTED = {
    "task": ("tasks", "_parser"),
    "work": ("work", "_parser"),
    "evidence": ("evidence", "_parser"),
    "handover": ("handover", "_parser"),
    "map": ("mapgen", "_parser"),
    "book": ("book", "_parser"),
    "explain": ("decisions", "_explain_parser"),
    "lineage": ("decisions", "_lineage_parser"),
}

#: `pr` and `protections` each check ONE fixed subcommand name by hand
#: (`rest[0] != "verify"`) rather than building an argparse subparser for
#: it, so each is ONE flat parser reached the same way `NESTED` entries are,
#: just with no subparsers beneath it (`leaf_parsers` returns one leaf: `()`).
NESTED["pr"] = ("prverify", "_parser")
NESTED["protections"] = ("protections", "_parser")


class TestEveryCommandAcceptsCwd(unittest.TestCase):
    """`--cwd` must parse on every subcommand this CLI exposes, at every
    depth, enumerated from the parsers themselves. See the module docstring
    for why this needs TWO different probes rather than one."""

    def setUp(self):
        self.foreign = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.foreign, True)
        self.target = new_repo()
        self.addCleanup(shutil.rmtree, self.target, True)

    def test_every_argparse_backed_command_declares_cwd_on_its_own_parser(self):
        """Static inspection: every command NOT in `cli.PASSTHROUGH`, plus
        every `NESTED` module's own real parser, walked to every leaf."""
        cli = cli_module()
        top = cli.build_parser()
        sub_action = next(a for a in top._actions
                          if isinstance(a, argparse._SubParsersAction))
        seen = 0
        for name, _help, _run in cli.COMMANDS:
            if name in NESTED:
                modname, attr = NESTED[name]
                parser = module_parser(modname, attr)
            elif name in cli.PASSTHROUGH:
                # Backed by a raw `tools/*.py` script: covered by the
                # behavioral test below instead, there being no parser
                # object here to read.
                continue
            else:
                parser = sub_action.choices[name]
            for prefix, leaf in leaf_parsers(parser):
                self.assertTrue(declares_cwd(leaf),
                                "sbe %s%s's own parser does not declare --cwd"
                                % (name, ((" " + " ".join(prefix)) if prefix else "")))
                seen += 1
        # A floor, not a ceiling: this counts roughly two dozen leaves today
        # across the non-PASSTHROUGH commands and NESTED's modules. This only
        # guards against that shrinking to near-zero (an import silently
        # failing, `cli.COMMANDS` emptying out), which would make every
        # assertion above vacuous.
        self.assertGreater(seen, 20, "only %d leaf parser(s) were inspected; cli.COMMANDS "
                                     "or a NESTED module import may have silently produced "
                                     "far fewer than expected" % seen)

    def test_every_raw_argv_delegate_accepts_cwd_without_being_misread_as_a_flag(self):
        """Behavioral: the commands whose whole argv is handed to a
        `tools/*.py` script (`cli.PASSTHROUGH` minus `NESTED`'s keys). No
        `-h`: see the module docstring for why, and for why running these
        read-only tools for real is safe."""
        cli = cli_module()
        raw_argv_names = sorted(set(cli.PASSTHROUGH) - set(NESTED))
        self.assertGreater(len(raw_argv_names), 5,
                           "cli.PASSTHROUGH minus NESTED reads suspiciously small: %r"
                           % raw_argv_names)
        for name in raw_argv_names:
            out = run_sbe([name, "--cwd", self.target], self.foreign)
            combined = (out.stdout + out.stderr).lower()
            for old_refusal in ("unrecognized flag", "not an option", "unrecognized argument"):
                self.assertNotIn(old_refusal, combined,
                                 "sbe %s --cwd %r was refused (%r): %r"
                                 % (name, self.target, old_refusal, combined))


class TestObservableCommandsHonourCwd(unittest.TestCase):
    """The subcommands the brief names as observable actually read the named
    repository, not the caller's own working directory. See the module
    docstring for why every test here runs from `self.foreign`."""

    def setUp(self):
        self.foreign = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.foreign, True)
        self.target = new_repo()
        self.addCleanup(shutil.rmtree, self.target, True)

    def test_status_reads_the_cwd_repo_not_the_callers(self):
        out = run_sbe(["status", "--cwd", self.target, "--json"], self.foreign)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data["root"], os.path.abspath(self.target),
                         "sbe status --cwd read root %r, not the --cwd target %r"
                         % (data["root"], self.target))

    def test_review_examines_the_cwd_repo_not_the_callers(self):
        """`sbe review` runs `sbe_score.py`'s source lint over its target,
        which names that target BY PATH in its own NO-DATA line the moment
        the target holds no lintable source (a fresh repo with only
        README.md never does): "lint root <root> holds no scannable
        source...". `self.target`'s own path showing up there is this
        test's evidence that `review` read `--cwd`, not `self.foreign`.
        """
        out = run_sbe(["review", "--cwd", self.target], self.foreign)
        combined = out.stdout + out.stderr
        self.assertIn(os.path.abspath(self.target), combined,
                     "sbe review --cwd %r never named that path anywhere in its output, "
                     "so nothing here proves it examined the --cwd target rather than "
                     "%r: %r" % (self.target, self.foreign, combined))

    def test_plan_resolves_a_relative_dossier_against_cwd_not_the_callers(self):
        """A dossier positional given RELATIVE (`design/x`) has no meaning at
        all in `self.foreign` (nothing there is named `design`); it only
        resolves at all if `--cwd` anchored it at `self.target`."""
        head = git(self.target, "rev-parse", "HEAD")
        write(self.target, "design/x/00-intake.json", json.dumps({
            "answers": {"changes_contract": "n", "crosses_boundary": "n",
                       "reversible_under_hour": "y", "touches_sensitive": "n",
                       "consumers": "none"},
            "binding": {"head": head},
        }))
        out = run_sbe(["plan", "design/x", "--cwd", self.target], self.foreign)
        combined = out.stdout + out.stderr
        self.assertNotIn("is neither a check name", combined,
                         "sbe plan design/x --cwd %r did not recognize design/x as a "
                         "directory, so it was not resolved against --cwd: %r"
                         % (self.target, combined))
        self.assertIn(out.returncode, (0, 1),
                      "sbe plan design/x --cwd %r exited %d (usage error), not a plan "
                      "verdict: %r" % (self.target, out.returncode, combined))

    def test_evidence_run_binds_the_receipt_to_the_cwd_repos_head_not_the_callers(self):
        target_head = git(self.target, "rev-parse", "HEAD")
        receipt_path = os.path.join(self.foreign, "receipt.json")
        out = run_sbe(["evidence", "run", "--cwd", self.target, "--out", receipt_path,
                       "--", sys.executable, "-c", "print('ok')"], self.foreign)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        with io.open(receipt_path, encoding="utf-8") as fh:
            receipt = json.load(fh)
        self.assertEqual(receipt.get("headCommit"), target_head,
                         "sbe evidence run --cwd %r bound the receipt to head %r, not the "
                         "--cwd repo's own head %r"
                         % (self.target, receipt.get("headCommit"), target_head))

    def test_task_open_writes_the_registry_at_cwd_not_a_foreign_directory(self):
        head = git(self.target, "rev-parse", "HEAD")
        out = run_sbe(["task", "open", "--id", "w1", "--agent", "alpha", "--role", "writer",
                       "--base", head, "--verify", "true", "--cwd", self.target], self.foreign)
        combined = out.stdout + out.stderr
        self.assertNotIn("is not inside a git repository", combined,
                         "sbe task open --cwd %r looked for a repository somewhere other "
                         "than the --cwd target: %r" % (self.target, combined))
        self.assertEqual(out.returncode, 0, combined)
        self.assertTrue(os.path.exists(os.path.join(self.target, ".sbe", "tasks.json")),
                        "sbe task open --cwd %r did not write the registry under the "
                        "--cwd repo" % self.target)
        self.assertFalse(os.path.exists(os.path.join(self.foreign, ".sbe")),
                         "sbe task open --cwd %r wrote a registry under the caller's own "
                         "directory instead" % self.target)

    def test_work_start_resolves_the_repo_at_cwd_not_a_foreign_directory(self):
        """No valid plan is built here on purpose: `cmd_start` resolves the
        repository FIRST (`repo_root_of`), before it ever opens `--plan`, so
        the message reaching stderr already proves which directory was
        checked, whatever happens to the (deliberately missing) plan file
        next.
        """
        missing_plan = os.path.join(self.foreign, "nonexistent-08-plan.json")
        out = run_sbe(["work", "start", "w1", "--plan", missing_plan,
                       "--cwd", self.target], self.foreign)
        combined = out.stdout + out.stderr
        self.assertNotIn("is not inside a git repository", combined,
                         "sbe work start --cwd %r looked for a repository somewhere "
                         "other than the --cwd target: %r" % (self.target, combined))
        self.assertIn("no plan file at", combined,
                      "sbe work start --cwd %r did not get past repository resolution "
                      "to the (deliberately missing) plan file: %r" % (self.target, combined))

    def test_handover_prepare_anchors_a_relative_dossier_at_cwd_not_the_callers(self):
        """A relative dossier (`design/x`, not created here) has to resolve
        SOMEWHERE for the refusal to name it; if `--cwd` were ignored it
        would resolve under `self.foreign` instead, and this assertion would
        see that path instead of `self.target`'s."""
        out = run_sbe(["handover", "prepare", "design/x", "--outgoing", "a@example.invalid",
                       "--receiver", "b@example.invalid", "--cwd", self.target], self.foreign)
        combined = out.stdout + out.stderr
        expected = os.path.realpath(os.path.join(self.target, "design", "x"))
        self.assertIn(expected, combined,
                     "sbe handover prepare design/x --cwd %r did not anchor the relative "
                     "dossier at the --cwd target (expected %r somewhere in the output): %r"
                     % (self.target, expected, combined))


if __name__ == "__main__":
    unittest.main()
