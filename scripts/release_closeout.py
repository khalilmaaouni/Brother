#!/usr/bin/env python3
"""release_closeout: the post-cut closeout matrix, one gate per subcommand.

Board rows X1 to X8. The question it answers is the one a tag does not:
after a version is cut, does a machine that has never seen Brother actually
INSTALL it, UPGRADE it, REINSTALL it without drift, UNINSTALL it cleanly,
REFUSE the four things it should refuse, still pass the Claude side, and
carry a public artifact whose own checksums verify.

EVERY GATE PRINTS ONE OF THREE WORDS, never a fourth: PASS, FAIL or NO-DATA.
NO-DATA is never a pass. X8 needs a signed-in Codex session, which no script
on this machine can hold, so it reports FOUNDER and names its runbook.

EVIDENCE IS KEPT WHOLE. Every command's complete output goes to
--evidence-dir (default ~/.claude/evidence/closeout-<version>/<gate>/), and
only the decisive lines are printed. A trim applied at capture time always
looks reasonable while it is being typed, and this estate has lost an
afternoon to one.

ISOLATION. Every Codex invocation runs with CODEX_HOME and HOME pointed
inside a throwaway directory, never the founder's. The isolation is measured,
not asserted: codex_smoke.founder_witness() hashes the parts of ~/.codex a
plugin or hook install would change, before and after, and both hashes are
printed. A gate whose witness moved FAILS.

Usage:
  python3 scripts/release_closeout.py all --marketplace <url-or-path> [--ref TAG]
  python3 scripts/release_closeout.py virgin-codex --marketplace .
  python3 scripts/release_closeout.py virgin --version 1.0.4 --grant PATH [--dispatch]
  python3 scripts/release_closeout.py --help

THE `virgin` VERB is the S4 row: one dispatch of the public repository's
dispatch-only .github/workflows/virgin-install.yml per release candidate,
authorised by a per-tag grant file (default ~/.claude/actions-grant.json,
the same file github_cost_wall.py already reads for `gh workflow run`,
carrying an added "tag" field). Without --dispatch it only prints the two
commands a founder or orchestrator would run and reports NO-DATA; with
--dispatch it requires a grant naming THIS tag, unexpired, then runs and
polls `gh` for the run id and conclusion. It is never wired into `all`:
X6 depends on it (S4 depends_on X6, E77) and quotes the run once one exists.

Exit codes for `all`: 0 when every REQUIRED gate is PASS, 1 otherwise.
Exit codes for a single gate: 0 PASS, 1 FAIL, 2 NO-DATA, 0 FOUNDER.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import codex_smoke  # noqa: E402  (its sh, isolation witness and stub provider)

DEFAULT_CODEX = codex_smoke.DEFAULT_CODEX
PUBLIC_URL = "https://github.com/khalilmaaouni/Brother"
RUNBOOK = "docs/codex/SMOKE-RUNBOOK.md"
VALIDATOR = os.path.expanduser(
    "~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py")

#: The gates, in board order. `required` gates must be PASS for `all` to
#: exit 0; a FOUNDER gate is reported and never counted against the matrix.
GATE_ORDER = [
    ("X1", "virgin-codex", "virgin Codex install from a marketplace", True),
    ("X2", "upgrade-codex", "Codex upgrade from the previous public release",
     True),
    ("X3", "reinstall-idempotent", "idempotent reinstall at the same version",
     True),
    ("X4", "uninstall-reinstall", "uninstall then reinstall to the same tree",
     True),
    ("X5", "negatives", "four refusals that must not read as a pass", True),
    ("X6", "claude-side", "the Claude client, invariants and export", True),
    ("X7", "public-artifact", "the published tag's own tree", True),
    ("X8", "founder", "the credentialled Codex session", False),
]


# ---------------------------------------------------------------------------
# Evidence, verdicts, and the table. These four are pure enough to drive in a
# test without a network or a Codex binary, which is what
# scripts/test_release_closeout.py does.
# ---------------------------------------------------------------------------

class Gate(object):
    """One row of the matrix: its verdict, and the lines that justify it."""

    def __init__(self, gid, name, title, required=True):
        self.id = gid
        self.name = name
        self.title = title
        self.required = required
        self.verdict = "NO-DATA"
        self.why = "not run"
        self.revision = "NO-DATA"
        self.lines = []

    def say(self, text):
        self.lines.append(text)

    def settle(self, verdict, why):
        if verdict not in ("PASS", "FAIL", "NO-DATA", "FOUNDER"):
            raise ValueError("release_closeout: not a verdict: %r" % verdict)
        self.verdict = verdict
        self.why = why
        return self

    def exit_code(self):
        return {"PASS": 0, "FOUNDER": 0, "FAIL": 1, "NO-DATA": 2}[self.verdict]


def verdict_table(gates):
    """(text, exit_code) for `all`.

    THE ONE RULE THIS ENCODES: the matrix is green only when every REQUIRED
    gate says PASS. A NO-DATA required gate leaves the matrix red, because a
    gate that could not run has not said the release is installable, and a
    population of NO-DATA composing into a PASS is a failure this estate has
    already paid for once.
    """
    width = max([len(g.name) for g in gates] + [4])
    rows = ["  %-3s %-*s  %-8s %s" % ("id", width, "gate", "verdict", "why")]
    for gate in gates:
        rows.append("  %-3s %-*s  %-8s %s" % (
            gate.id, width, gate.name, gate.verdict, gate.why))
    required = [g for g in gates if g.required]
    missed = [g for g in required if g.verdict != "PASS"]
    rows.append("")
    rows.append("  required gates: %d   PASS: %d   not PASS: %s" % (
        len(required), len(required) - len(missed),
        ", ".join("%s(%s)" % (g.id, g.verdict) for g in missed) or "none"))
    founder = [g for g in gates if g.verdict == "FOUNDER"]
    for gate in founder:
        rows.append("  %s is FOUNDER: %s" % (gate.id, gate.why))
    if missed:
        rows.append("")
        rows.append("CLOSEOUT NOT COMPLETE: %d required gate(s) are not PASS. "
                    "NO-DATA is not a pass." % len(missed))
        return "\n".join(rows), 1
    rows.append("")
    rows.append("CLOSEOUT COMPLETE: every required gate is PASS.")
    return "\n".join(rows), 0


def tree_hash(root):
    """A sha256 over a directory's relative paths and file contents, or None
    when the directory is not there. Used to answer "did this reinstall
    change anything" with a number instead of an impression."""
    if not os.path.isdir(root):
        return None
    digest = hashlib.sha256()
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            digest.update(rel.encode("utf-8"))
            try:
                with open(path, "rb") as fh:
                    digest.update(hashlib.sha256(fh.read()).hexdigest()
                                  .encode("utf-8"))
            except OSError as exc:
                digest.update(("UNREADABLE:%s" % exc).encode("utf-8"))
    return digest.hexdigest()


def previous_public_tag(checkout, current_tag):
    """The public tag immediately before `current_tag`, by version order, or
    (None, why). Reads the checkout's own tags, never a hand-typed list."""
    proc = codex_smoke.sh(["git", "-C", checkout, "tag", "--list", "v*"],
                          timeout=60)
    if proc.returncode != 0:
        return None, "git tag failed in %s: %s" % (checkout,
                                                   (proc.stderr or "").strip())
    def key(tag):
        parts = tag.lstrip("v").split("-")[0].split(".")
        out = []
        for part in parts:
            try:
                out.append(int(part))
            except ValueError:
                out.append(0)
        return out
    tags = sorted([t.strip() for t in (proc.stdout or "").split() if t.strip()],
                  key=key)
    if current_tag in tags:
        tags = tags[:tags.index(current_tag)]
    else:
        tags = [t for t in tags if key(t) < key(current_tag)]
    if not tags:
        return None, "no public tag older than %s in %s" % (current_tag,
                                                            checkout)
    return tags[-1], ""


def tag_carries_codex_package(checkout, tag):
    """(True/False, detail): does that tag's own tree ship the Codex package?

    Measured with git ls-tree against the tag, never assumed. v1.0.1 does not
    carry one, and inventing an upgrade from a release that had no Codex
    package would be a fabricated gate.
    """
    proc = codex_smoke.sh(["git", "-C", checkout, "ls-tree", "-r",
                           "--name-only", tag], timeout=120)
    if proc.returncode != 0:
        return None, "git ls-tree %s failed: %s" % (
            tag, (proc.stderr or "").strip())
    names = (proc.stdout or "").splitlines()
    wanted = ["bundle/.codex-plugin/plugin.json",
              ".agents/plugins/marketplace.json"]
    present = [w for w in wanted if w in names]
    if len(present) == len(wanted):
        return True, "%s carries %s" % (tag, " and ".join(wanted))
    missing = [w for w in wanted if w not in present]
    return False, "%s does not carry %s" % (tag, " and ".join(missing))


# ---------------------------------------------------------------------------
# Running commands, keeping every byte.
# ---------------------------------------------------------------------------

class Evidence(object):
    def __init__(self, root):
        self.root = root
        self.count = 0

    def keep(self, gate_id, step, proc):
        self.count += 1
        folder = os.path.join(self.root, gate_id)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            print("  (could not create %s: %s)" % (folder, exc))
            return ""
        slug = "".join(c if c.isalnum() else "-" for c in step)
        path = os.path.join(folder, "%02d-%s.log" % (self.count, slug))
        args = proc.args if isinstance(proc.args, list) else [str(proc.args)]
        body = "$ %s\nexit %d\n\n%s%s" % (
            " ".join(args), proc.returncode, proc.stdout or "",
            proc.stderr or "")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
        except OSError as exc:
            print("  (could not keep %s: %s)" % (path, exc))
            return ""
        return path


def decisive(proc, needles=(), tail=3):
    """The lines a reader needs: every line matching a needle, else the tail.
    Never the whole body, which is on disk instead."""
    body = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln.rstrip() for ln in body.splitlines() if ln.strip()]
    hits = [ln for ln in lines if any(n in ln for n in needles)] if needles \
        else []
    chosen = hits[:tail] if hits else lines[-tail:]
    return [ln[:220] for ln in chosen]


def step(gate, ev, label, args, env=None, cwd=None, timeout=900, needles=()):
    """Run one command, keep its whole output, print its command line, exit
    code and decisive lines onto the gate's own record."""
    proc = codex_smoke.sh(args, env=env, cwd=cwd, timeout=timeout)
    kept = ev.keep(gate.id, label, proc)
    shown = args if isinstance(args, list) else [str(args)]
    gate.say("$ %s" % " ".join(shown))
    for line in decisive(proc, needles):
        gate.say("    %s" % line)
    gate.say("  exit %d   [%s]   full output: %s" % (
        proc.returncode, label, kept or "NOT KEPT"))
    return proc


# ---------------------------------------------------------------------------
# The isolated Codex home, shared by X1 to X5.
# ---------------------------------------------------------------------------

class Isolated(object):
    """A throwaway CODEX_HOME and HOME, plus the founder witness taken around
    everything that runs inside it."""

    def __init__(self, work):
        self.work = work
        self.codex_home = os.path.join(work, "codex-home")
        self.home = os.path.join(work, "home")
        for path in (self.codex_home, self.home):
            os.makedirs(path, exist_ok=True)
        self.before, self.witness_desc = codex_smoke.founder_witness()

    def env(self):
        return codex_smoke.codex_env(os.environ, self.codex_home, self.home)

    def plugins_root(self):
        return os.path.join(self.codex_home, "plugins")

    def check_isolation(self, gate):
        after, _ = codex_smoke.founder_witness()
        gate.say("founder ~/.codex witness before: %s" % (self.before or
                                                          "NO-DATA"))
        gate.say("founder ~/.codex witness after:  %s" % (after or "NO-DATA"))
        if self.before is None or after is None:
            return "the founder's ~/.codex could not be witnessed (%s), so " \
                   "isolation is unproven" % self.witness_desc
        if self.before != after:
            return "the founder's ~/.codex CHANGED during this gate"
        gate.say("isolation holds: the witness is byte-identical before and "
                 "after.")
        return ""


def source_revision(source, ref):
    """What was actually installed, said in one line. A gate that cannot name
    its revision is a gate nobody can reproduce."""
    if ref:
        return "%s at ref %s" % (source, ref)
    if os.path.isdir(source):
        proc = codex_smoke.sh(["git", "-C", source, "rev-parse", "HEAD"],
                              timeout=60)
        if proc.returncode == 0:
            return "%s at %s (no ref pinned)" % (source,
                                                 (proc.stdout or "").strip())
        return "%s (not a git checkout)" % source
    return "%s (no ref pinned)" % source


#: The upgrade route README.md documents, in the runner's own words, so X2
#: drives what a reader is actually told to run. Measured 2026-09-05 against
#: the app-bundled codex in isolated homes: `plugin marketplace add` of a name
#: already configured from another ref is refused ("marketplace 'brother' is
#: already added from a different source; remove it before adding this
#: source", exit 1), and `plugin marketplace upgrade brother` exits 0 while
#: leaving the installed version and its hashes exactly where they were,
#: because it only refreshes the snapshot at the ref already configured.
#: Removing the configured marketplace first is what makes an upgrade move.
#: test_release_closeout.py holds this equal to the README block, so the page
#: and the gate cannot drift apart.
UPGRADE_ROUTE = (
    ("plugin", "marketplace", "remove", "brother"),
    ("plugin", "marketplace", "add", "{source}", "--ref", "{ref}"),
    ("plugin", "add", "brother@brother", "--json"),
)

#: What each route step is called on the gate's own record, in route order.
UPGRADE_ROUTE_LABELS = ("marketplace remove", "marketplace add target",
                        "plugin add target")

#: The lines worth lifting out of each route step's output onto the record.
UPGRADE_ROUTE_NEEDLES = ("Removed marketplace", "Added marketplace",
                         "Installed marketplace root", "installedPath",
                         "version", "error", "Error")


def upgrade_route_steps(source, ref):
    """The documented route with its two placeholders filled in."""
    return [[part.format(source=source, ref=ref) for part in tail]
            for tail in UPGRADE_ROUTE]


def upgrade_route_shell(source, ref):
    """The route as one shell line, exactly as README.md prints it."""
    return " && ".join("codex " + " ".join(tail)
                       for tail in upgrade_route_steps(source, ref))


def marketplace_add(gate, ev, iso, source, ref, label="marketplace add",
                    env=None):
    args = [gate.codex_bin, "plugin", "marketplace", "add", source]
    if ref:
        args += ["--ref", ref]
    return step(gate, ev, label, args, env=env or iso.env(),
                needles=("Added marketplace", "Installed marketplace root",
                         "error", "Error"))


def plugin_add(gate, ev, iso, label="plugin add", env=None,
               selector="brother@brother"):
    return step(gate, ev, label,
                [gate.codex_bin, "plugin", "add", selector, "--json"],
                env=env or iso.env(),
                needles=("installedPath", "version", "marketplaceName",
                         "error", "Error"))


def installed_path_from(proc):
    """The installedPath the tool itself reported, read from its --json, never
    guessed from a directory walk."""
    body = proc.stdout or ""
    try:
        parsed = json.loads(body)
    except ValueError:
        for line in body.splitlines():
            if '"installedPath"' in line:
                bits = line.split('"')
                if len(bits) >= 4:
                    return bits[-2], ""
        return None, "plugin add printed no parseable installedPath"
    if isinstance(parsed, dict):
        path = parsed.get("installedPath")
        if path:
            return path, ""
    return None, "plugin add's JSON carries no installedPath"


# ---------------------------------------------------------------------------
# X1: the virgin Codex install.
# ---------------------------------------------------------------------------

def gate_virgin_codex(args, ev, gate):
    iso = Isolated(os.path.join(args.work, "x1"))
    gate.revision = source_revision(args.marketplace, args.ref)
    gate.say("codex binary: %s" % gate.codex_bin)

    add = marketplace_add(gate, ev, iso, args.marketplace, args.ref)
    if add.returncode != 0:
        return gate.settle("FAIL", "marketplace add exited %d" %
                           add.returncode)

    got = plugin_add(gate, ev, iso)
    if got.returncode != 0:
        return gate.settle("FAIL", "plugin add exited %d" % got.returncode)
    installed, why = installed_path_from(got)
    if installed is None:
        return gate.settle("FAIL", why)
    gate.say("installedPath: %s" % installed)

    listed = step(gate, ev, "plugin list available",
                  [gate.codex_bin, "plugin", "list", "--available", "--json"],
                  env=iso.env(), needles=("pluginId", "version"))
    body = listed.stdout or ""
    if listed.returncode != 0 or '"brother@brother"' not in body:
        return gate.settle("FAIL", "plugin list --available --json did not "
                                   "name pluginId brother@brother")
    gate.say("pluginId brother@brother present in plugin list --available")

    # Skill discovery: the SKILL.md files the SOURCE ships must all be in the
    # installed tree. The expected list is read off the source, never typed.
    src_skills = os.path.join(args.marketplace, "bundle", "skills") \
        if os.path.isdir(args.marketplace) else ""
    expected = []
    if src_skills and os.path.isdir(src_skills):
        for base, dirs, files in os.walk(src_skills):
            dirs.sort()
            for name in sorted(files):
                if name == "SKILL.md":
                    expected.append(os.path.relpath(
                        os.path.join(base, name), src_skills))
    inst_skills = os.path.join(installed, "skills")
    if not os.path.isdir(inst_skills):
        return gate.settle("FAIL", "the installed plugin has no skills "
                                   "directory at %s" % inst_skills)
    found = []
    for base, dirs, files in os.walk(inst_skills):
        dirs.sort()
        for name in sorted(files):
            if name == "SKILL.md":
                found.append(os.path.relpath(os.path.join(base, name),
                                             inst_skills))
    gate.say("skills directory %s holds %d SKILL.md file(s): %s" % (
        inst_skills, len(found), ", ".join(sorted(found)) or "(none)"))
    if not found:
        return gate.settle("FAIL", "the installed plugin ships no SKILL.md")
    if expected:
        missing = sorted(set(expected) - set(found))
        if missing:
            return gate.settle("FAIL", "the install is missing SKILL.md "
                                       "file(s) the source ships: %s" %
                               ", ".join(missing))
        gate.say("every SKILL.md the source ships (%d) is present in the "
                 "install" % len(expected))
    else:
        gate.say("NOTE: the marketplace source is remote, so the expected "
                 "SKILL.md list could not be read off it; only presence was "
                 "checked.")

    # One invocation through the stub provider, producing a receipt. The stub
    # route is codex_smoke's, reused rather than re-implemented.
    toy = os.path.join(iso.work, "toy")
    why = codex_smoke.build_toy(toy)
    if why:
        return gate.settle("FAIL", why)
    stubs = os.path.join(iso.work, "stubs")
    # Under $TMPDIR, never under a caller-supplied --work. A workspace-write
    # turn grants exactly "[workdir, /tmp, $TMPDIR]", and the engine keeps its
    # run directory outside the tree it integrates into, so a --work somewhere
    # else would put that directory outside every granted root and Codex would
    # drop the tool call silently, at exit 0, with no receipt.
    runs_root = os.path.join(tempfile.gettempdir(), "closeout-c7-runs")
    if os.path.isdir(runs_root):
        shutil.rmtree(runs_root)
    for path in (stubs, runs_root):
        os.makedirs(path, exist_ok=True)
    hooks = step(gate, ev, "codex hooks install",
                 [sys.executable, os.path.join(HERE, "codex_hooks_install.py"),
                  "--codex-home", iso.codex_home, "--trust", "--cwd", toy,
                  "--codex-bin", gate.codex_bin],
                 env=iso.env(), needles=("PASS", "FAIL", "NO-DATA"))
    if hooks.returncode != 0:
        return gate.settle("FAIL", "codex_hooks_install exited %d" %
                           hooks.returncode)
    stub_env = iso.env()
    stub_env["DOOR_MODEL_CMD"] = "%s %s" % (
        sys.executable, codex_smoke.write_stub(
            os.path.join(stubs, "decomposer.py"),
            codex_smoke.DECOMPOSER_STUB))
    stub_env["MODEL_WORKER_CMD"] = "%s %s" % (
        sys.executable, codex_smoke.write_stub(
            os.path.join(stubs, "model.py"), codex_smoke.MODEL_STUB))
    command = ("%s %s 'make add() refuse non-numeric input' --cwd %s "
               "--runs-root %s --quiet" % (
                   sys.executable, os.path.join(HERE, "brother_run.py"), toy,
                   runs_root))
    turn = codex_smoke.stub_turn(gate.codex_bin, stub_env, toy, command)
    kept = ev.keep(gate.id, "codex exec through the stub provider", turn)
    gate.say("$ codex exec %s (stub provider on 127.0.0.1) -C %s"
             % (" ".join(codex_smoke.sandbox_flags(toy)), toy))
    for line in decisive(turn, ("brother", "receipt"), tail=4):
        gate.say("    %s" % line)
    gate.say("  exit %d   [stub codex turn]   full output: %s" % (
        turn.returncode, kept or "NOT KEPT"))
    if turn.returncode != 0:
        return gate.settle("FAIL", "the stub codex turn exited %d" %
                           turn.returncode)
    run_dir = codex_smoke.newest_run_dir(
        os.path.join(runs_root, "docs", "plan", "runs")) \
        or codex_smoke.newest_run_dir(runs_root)
    if run_dir is None:
        return gate.settle("FAIL", "the run left no run directory under %s" %
                           runs_root)
    found_receipt, why = codex_smoke.receipt_lines(run_dir)
    if found_receipt is None:
        return gate.settle("FAIL", "no usable receipt: %s" % why)
    path, lines = found_receipt
    gate.say("receipt: %s" % path)
    for line in lines:
        gate.say("    %s" % line)

    gate.say("the CREDENTIALLED invocation is X8, not this gate: an isolated "
             "home holds no credentials, and %s is the runbook that closes "
             "it." % RUNBOOK)
    gate.say("the turn above carried the runbook's own sandbox flags. Codex "
             "defaults to read-only, which refuses every patch, and plain "
             "workspace-write still refuses the .git write Brother's unit "
             "isolation needs: %s" % RUNBOOK)
    broke = iso.check_isolation(gate)
    if broke:
        return gate.settle("FAIL", broke)
    return gate.settle("PASS", "install, skills and one stubbed invocation "
                               "with a receipt, in an isolated home")


# ---------------------------------------------------------------------------
# X2: the upgrade from the previous public release.
# ---------------------------------------------------------------------------

def gate_upgrade_codex(args, ev, gate):
    checkout = args.public_checkout
    tag = "v%s" % args.version
    # D3 (Codex lane, 2026-09-05): the upgrade itself has always honoured
    # --ref (`ref = args.ref or tag` below), but every line this gate PRINTED
    # named the tag, so a `--ref main` matrix reported "v1.0.2 upgraded to
    # v1.0.3" while actually upgrading to main. A gate that misnames what it
    # tested is a gate nobody can cite.
    ref = args.ref or tag
    gate.revision = "%s upgraded to %s" % ("<previous>", ref)
    previous, why = previous_public_tag(checkout, tag)
    if previous is None:
        gate.say(why)
        return gate.settle("NO-DATA", why)
    gate.say("previous public tag: %s" % previous)
    carries, detail = tag_carries_codex_package(checkout, previous)
    gate.say(detail)
    if carries is None:
        return gate.settle("NO-DATA", detail)
    if not carries:
        gate.revision = "%s to %s" % (previous, ref)
        return gate.settle(
            "NO-DATA",
            "%s ships no Codex package, so there is no Codex install to "
            "upgrade FROM; inventing one would be a fabricated gate" % detail)

    iso = Isolated(os.path.join(args.work, "x2"))
    gate.revision = "%s upgraded to %s" % (previous, ref)
    old = marketplace_add(gate, ev, iso, args.marketplace, previous,
                          label="marketplace add previous")
    if old.returncode != 0:
        return gate.settle("FAIL", "marketplace add at %s exited %d" %
                           (previous, old.returncode))
    got = plugin_add(gate, ev, iso, label="plugin add previous")
    if got.returncode != 0:
        return gate.settle("FAIL", "plugin add at %s exited %d" %
                           (previous, got.returncode))
    installed, detail = installed_path_from(got)
    if installed is None:
        return gate.settle("FAIL", detail)
    before_hash = tree_hash(installed)
    gate.say("installed %s at %s, tree sha256 %s" % (previous, installed,
                                                     before_hash))

    # Seeded state under the isolated home: a hooks file, a config entry and a
    # receipt. If an upgrade silently wipes any of these, a user loses work.
    seeds = {
        os.path.join(iso.codex_home, "hooks.json"):
            '{"closeout_seed": true}\n',
        os.path.join(iso.codex_home, "config.toml"):
            'closeout_seed = "x2"\n',
        os.path.join(iso.codex_home, "closeout-receipt.json"):
            '{"seeded_by": "release_closeout X2"}\n',
    }
    for path, body in seeds.items():
        mode = "a" if path.endswith("config.toml") and os.path.exists(path) \
            else "w"
        try:
            with open(path, mode, encoding="utf-8") as fh:
                fh.write(body)
        except OSError as exc:
            return gate.settle("FAIL", "could not seed %s: %s" % (path, exc))
    gate.say("seeded state under the isolated home: %s" %
             ", ".join(sorted(os.path.basename(p) for p in seeds)))

    # The upgrade itself, run as README.md documents it and in that order.
    gate.say("the documented upgrade route: %s" %
             upgrade_route_shell(args.marketplace, ref))
    got2 = None
    for tail, label in zip(upgrade_route_steps(args.marketplace, ref),
                           UPGRADE_ROUTE_LABELS):
        got2 = step(gate, ev, label, [gate.codex_bin] + tail, env=iso.env(),
                    needles=UPGRADE_ROUTE_NEEDLES)
        if got2.returncode != 0:
            return gate.settle("FAIL", "`%s` exited %d on the way to %s" %
                               (" ".join(["codex"] + tail), got2.returncode,
                                tag))
    if got2 is None:
        return gate.settle("NO-DATA", "the upgrade route is empty, so "
                                      "nothing was run")
    installed2, detail = installed_path_from(got2)
    if installed2 is None:
        return gate.settle("FAIL", detail)
    after_hash = tree_hash(installed2)
    gate.say("installed %s at %s, tree sha256 %s" % (tag, installed2,
                                                     after_hash))

    survivors = []
    for path in sorted(seeds):
        if os.path.exists(path):
            survivors.append(os.path.basename(path))
    gate.say("seeded state surviving the upgrade: %s" %
             (", ".join(survivors) or "(none)"))
    if len(survivors) != len(seeds):
        return gate.settle("FAIL", "the upgrade destroyed seeded state: %s "
                                   "did not survive" %
                           ", ".join(sorted(set(os.path.basename(p) for p in
                                                seeds) - set(survivors))))
    if installed == installed2:
        return gate.settle("FAIL", "the plugin version did not move: both "
                                   "installs resolved to %s" % installed)
    if before_hash == after_hash:
        return gate.settle("FAIL", "the installed artifact hashes did not "
                                   "move across the upgrade (%s)" %
                           before_hash)
    gate.say("version moved (%s to %s) and artifact hashes moved" % (
        os.path.basename(installed), os.path.basename(installed2)))
    broke = iso.check_isolation(gate)
    if broke:
        return gate.settle("FAIL", broke)
    return gate.settle("PASS", "upgrade %s to %s kept seeded state and moved "
                               "both the version and the hashes" %
                       (previous, tag))


# ---------------------------------------------------------------------------
# X3: the idempotent reinstall.
# ---------------------------------------------------------------------------

def gate_reinstall_idempotent(args, ev, gate):
    iso = Isolated(os.path.join(args.work, "x3"))
    gate.revision = source_revision(args.marketplace, args.ref)
    add = marketplace_add(gate, ev, iso, args.marketplace, args.ref)
    if add.returncode != 0:
        return gate.settle("FAIL", "marketplace add exited %d" %
                           add.returncode)
    first = plugin_add(gate, ev, iso, label="plugin add first")
    if first.returncode != 0:
        return gate.settle("FAIL", "the first plugin add exited %d" %
                           first.returncode)
    installed, why = installed_path_from(first)
    if installed is None:
        return gate.settle("FAIL", why)
    before = tree_hash(installed)
    gate.say("after the first add: %s   tree sha256 %s" % (installed, before))
    second = plugin_add(gate, ev, iso, label="plugin add second")
    gate.say("the second add exited %d (a refusal to re-add is an acceptable "
             "idempotent answer; a CHANGED tree is not)" % second.returncode)
    after = tree_hash(installed)
    gate.say("after the second add: %s   tree sha256 %s" % (installed, after))
    if before is None or after is None:
        return gate.settle("NO-DATA", "the installed tree at %s could not be "
                                      "hashed, so drift is unmeasured" %
                           installed)
    if before != after:
        return gate.settle("FAIL", "the second add CHANGED the installed "
                                   "tree: %s became %s" % (before, after))
    broke = iso.check_isolation(gate)
    if broke:
        return gate.settle("FAIL", broke)
    return gate.settle("PASS", "the second add left the installed tree byte "
                               "identical (%s)" % before[:16])


# ---------------------------------------------------------------------------
# X4: uninstall then reinstall.
# ---------------------------------------------------------------------------

def gate_uninstall_reinstall(args, ev, gate):
    iso = Isolated(os.path.join(args.work, "x4"))
    gate.revision = source_revision(args.marketplace, args.ref)
    add = marketplace_add(gate, ev, iso, args.marketplace, args.ref)
    if add.returncode != 0:
        return gate.settle("FAIL", "marketplace add exited %d" %
                           add.returncode)
    first = plugin_add(gate, ev, iso, label="plugin add first")
    if first.returncode != 0:
        return gate.settle("FAIL", "the first plugin add exited %d" %
                           first.returncode)
    installed, why = installed_path_from(first)
    if installed is None:
        return gate.settle("FAIL", why)
    before = tree_hash(installed)
    gate.say("first install: %s   tree sha256 %s" % (installed, before))

    rm = step(gate, ev, "plugin remove",
              [gate.codex_bin, "plugin", "remove", "brother@brother"],
              env=iso.env(), needles=("Removed", "removed", "error", "Error"))
    if rm.returncode != 0:
        return gate.settle("FAIL", "plugin remove exited %d" % rm.returncode)
    mrm = step(gate, ev, "marketplace remove",
               [gate.codex_bin, "plugin", "marketplace", "remove", "brother"],
               env=iso.env(),
               needles=("Removed", "removed", "error", "Error"))
    if mrm.returncode != 0:
        return gate.settle("FAIL", "marketplace remove exited %d" %
                           mrm.returncode)
    if os.path.isdir(installed):
        left = tree_hash(installed)
        return gate.settle("FAIL", "the installed tree survived the uninstall "
                                   "at %s (sha256 %s)" % (installed, left))
    gate.say("the installed tree is gone: %s no longer exists" % installed)

    add2 = marketplace_add(gate, ev, iso, args.marketplace, args.ref,
                           label="marketplace add again")
    if add2.returncode != 0:
        return gate.settle("FAIL", "the second marketplace add exited %d" %
                           add2.returncode)
    again = plugin_add(gate, ev, iso, label="plugin add again")
    if again.returncode != 0:
        return gate.settle("FAIL", "the reinstall exited %d" %
                           again.returncode)
    installed2, why = installed_path_from(again)
    if installed2 is None:
        return gate.settle("FAIL", why)
    after = tree_hash(installed2)
    gate.say("reinstall: %s   tree sha256 %s" % (installed2, after))
    if before is None or after is None:
        return gate.settle("NO-DATA", "one of the two installed trees could "
                                      "not be hashed")
    if before != after:
        return gate.settle("FAIL", "the reinstalled tree differs from the "
                                   "first: %s became %s" % (before, after))
    broke = iso.check_isolation(gate)
    if broke:
        return gate.settle("FAIL", broke)
    return gate.settle("PASS", "uninstall emptied the tree and the reinstall "
                               "reproduced it byte for byte (%s)" %
                       before[:16])


# ---------------------------------------------------------------------------
# X5: the four negatives. Each must produce an honest FAIL or NO-DATA from the
# tool under test, and never a pass.
# ---------------------------------------------------------------------------

#: The four, in the order gate_negatives drives them. Named so the gate's own
#: PASS line says WHICH four were refused, rather than asserting a count.
NEGATIVES = ("missing marketplace", "malformed plugin.json",
             "unsupported hooks key", "offline")

def local_base(args, gate, ev):
    """(path, why): a LOCAL directory holding the marketplace source, so the
    two corruption negatives have something to corrupt.

    WHY THIS EXISTS. Both defect cases mutate a copy of the package and hand
    the result to the tool. When --marketplace is a Git URL, the old code had
    nothing local to copy and reported NO-DATA twice, which meant the release
    ACTUALLY UNDER TEST (a public tag, always a URL) was the one case where
    half the negatives never ran. A gate that drives its negatives only
    against the source nobody ships proves nothing about the thing shipped.

    So: clone the ref once, into the gate's own work directory, and corrupt
    THAT. The clone is the same bytes the remote serves, which is exactly the
    package a user installs. The other two negatives (a missing marketplace
    name and the blackholed proxy) keep running against the remote, because
    those two are about the TRANSPORT and a local copy would prove nothing.
    """
    if os.path.isdir(args.marketplace):
        return args.marketplace, ""
    dest = os.path.join(args.work, "x5-src")
    if os.path.isdir(dest):
        return dest, ""
    cmd = ["git", "clone", "--depth", "1"]
    if args.ref:
        cmd += ["--branch", args.ref]
    cmd += [args.marketplace, dest]
    proc = step(gate, ev, "clone the marketplace source to corrupt", cmd,
                timeout=900, needles=("Cloning", "fatal", "error", "warning"))
    if proc.returncode != 0:
        return None, "the remote marketplace %s could not be cloned, so " \
                     "there is no local copy to corrupt (git clone exited " \
                     "%d)" % (args.marketplace, proc.returncode)
    if not os.path.isdir(dest):
        return None, "git clone exited 0 but %s is not there" % dest
    return dest, ""


def _corrupt_copy(source, work, mutate):
    """A temp copy of a LOCAL marketplace with one deliberate defect. Returns
    (path, why): a source that is not a directory cannot be copied, and that
    is NO-DATA."""
    if source is None or not os.path.isdir(source):
        return None, "there is no local copy of the marketplace source to " \
                     "corrupt"
    dest = os.path.join(work, "copy")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    try:
        shutil.copytree(source, dest,
                        ignore=shutil.ignore_patterns(".git", "node_modules"))
    except (OSError, shutil.Error) as exc:
        return None, "could not copy the marketplace source: %s" % exc
    try:
        mutate(dest)
    except (OSError, ValueError) as exc:
        return None, "could not apply the defect: %s" % exc
    return dest, ""


def gate_negatives(args, ev, gate):
    gate.revision = source_revision(args.marketplace, args.ref)
    iso = Isolated(os.path.join(args.work, "x5"))
    verdicts = []

    # The two corruption negatives need a local copy. A Git source is cloned
    # once here so a REMOTE release gets its negatives driven too.
    base, base_why = local_base(args, gate, ev)
    gate.say("local base for the corruption negatives: %s" %
             (base or "NO-DATA: %s" % base_why))

    # 1. A marketplace name that was never added.
    miss = step(gate, ev, "negative missing marketplace",
                [gate.codex_bin, "plugin", "add",
                 "brother@no-such-marketplace-xyz", "--json"],
                env=iso.env(), needles=("error", "Error", "not found",
                                        "Unknown", "unknown"))
    if miss.returncode == 0:
        verdicts.append(("FAIL", "a missing marketplace name was ACCEPTED "
                                 "(exit 0)"))
    else:
        verdicts.append(("PASS", "a missing marketplace name was refused at "
                                 "exit %d" % miss.returncode))

    # 2. A malformed plugin.json in a temp copy.
    def break_json(root):
        path = os.path.join(root, "bundle", ".codex-plugin", "plugin.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
    bad, why = _corrupt_copy(base, os.path.join(args.work, "x5-malformed"),
                             break_json)
    if bad is None:
        verdicts.append(("NO-DATA", "malformed plugin.json not driven: %s" %
                                    (why or base_why)))
    else:
        iso2 = Isolated(os.path.join(args.work, "x5-malformed-home"))
        add = step(gate, ev, "negative malformed marketplace add",
                   [gate.codex_bin, "plugin", "marketplace", "add", bad],
                   env=iso2.env(), needles=("error", "Error", "invalid",
                                            "Invalid", "parse"))
        got = step(gate, ev, "negative malformed plugin add",
                   [gate.codex_bin, "plugin", "add", "brother@brother",
                    "--json"], env=iso2.env(),
                   needles=("error", "Error", "invalid", "Invalid", "parse"))
        if add.returncode == 0 and got.returncode == 0:
            verdicts.append(("FAIL", "a malformed plugin.json installed "
                                     "cleanly (both commands exit 0)"))
        else:
            verdicts.append(("PASS", "a malformed plugin.json was refused "
                                     "(marketplace add exit %d, plugin add "
                                     "exit %d)" % (add.returncode,
                                                   got.returncode)))

    # 3. A `hooks` key in plugin.json, which the canonical validator rejects
    #    (docs/codex/HOOKS-MAPPING.md). Driven against the validator itself,
    #    because that is the tool whose verdict the package gate uses.
    if not os.path.isfile(VALIDATOR):
        verdicts.append(("NO-DATA", "no canonical validator at %s, so the "
                                    "hooks-key refusal is unproven" %
                         VALIDATOR))
    elif not shutil.which("uv"):
        verdicts.append(("NO-DATA", "no uv on PATH, and the validator imports "
                                    "yaml which the bare interpreter lacks"))
    else:
        def add_hooks(root):
            path = os.path.join(root, "bundle", ".codex-plugin", "plugin.json")
            with open(path, "r", encoding="utf-8") as fh:
                body = json.load(fh)
            body["hooks"] = "./hooks.json"
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(body, fh, indent=2)
        hooked, why = _corrupt_copy(base, os.path.join(args.work, "x5-hooks"),
                                    add_hooks)
        if hooked is None:
            verdicts.append(("NO-DATA", "hooks-key case not driven: %s" %
                                        (why or base_why)))
        else:
            # The positive control validates the UNMODIFIED package from the
            # same base the defect was cut from, so the two runs differ by
            # the hooks key and nothing else.
            clean = step(gate, ev, "validator positive control",
                         ["uv", "run", "--with", "pyyaml", "python3",
                          VALIDATOR, os.path.join(base, "bundle")],
                         needles=("PASS", "FAIL", "error", "hooks"),
                         timeout=600)
            hooked_run = step(gate, ev, "negative hooks key",
                              ["uv", "run", "--with", "pyyaml", "python3",
                               VALIDATOR, os.path.join(hooked, "bundle")],
                              needles=("hooks", "PASS", "FAIL", "error"),
                              timeout=600)
            gate.say("positive control: the unmodified package validates at "
                     "exit %d" % clean.returncode)
            if clean.returncode != 0:
                verdicts.append(("NO-DATA", "the validator rejects the "
                                            "UNMODIFIED package too (exit "
                                            "%d), so its refusal of the hooks "
                                            "key proves nothing" %
                                 clean.returncode))
            elif hooked_run.returncode == 0:
                verdicts.append(("FAIL", "the validator ACCEPTED a plugin.json "
                                         "carrying an unsupported hooks key"))
            else:
                verdicts.append(("PASS", "the validator refused the hooks key "
                                         "at exit %d" % hooked_run.returncode))

    # 4. Offline. Codex has no offline switch of its own (`codex plugin
    #    marketplace add --help` lists source, --ref, --sparse, --json and the
    #    generic config flags, and nothing else), so the honest substitute is
    #    a blackholed HTTPS proxy against a GIT source, which is the transport
    #    a remote marketplace actually uses. A LOCAL path source reads no
    #    network at all, so pointing the proxy at one would prove nothing and
    #    is reported as NO-DATA instead of dressed up as a pass.
    offline_source = args.marketplace if not os.path.isdir(args.marketplace) \
        else PUBLIC_URL
    if os.path.isdir(args.marketplace):
        gate.say("offline is driven against %s, not the local marketplace "
                 "path: a local source reads no network, so blackholing a "
                 "proxy in front of it would prove nothing." % PUBLIC_URL)
    off_env = Isolated(os.path.join(args.work, "x5-offline")).env()
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        off_env[key] = "http://127.0.0.1:1"
    off_env["NO_PROXY"] = ""
    off_env["no_proxy"] = ""
    off = step(gate, ev, "negative offline",
               [gate.codex_bin, "plugin", "marketplace", "add",
                offline_source] + (["--ref", args.ref] if args.ref else []),
               env=off_env, needles=("error", "Error", "proxy", "Could not",
                                     "failed", "unable"), timeout=300)
    if off.returncode == 0:
        verdicts.append(("FAIL", "a marketplace add through a blackholed "
                                 "HTTPS proxy SUCCEEDED (exit 0), so the "
                                 "offline path is not what it appears"))
    else:
        verdicts.append(("PASS", "a marketplace add through a blackholed "
                                 "HTTPS proxy was refused at exit %d" %
                         off.returncode))

    # Each of the four cases above appends exactly one verdict, in order, so
    # the names line up. The length is CHECKED rather than trusted: a future
    # case that appends twice would otherwise mislabel every row after it,
    # and a PASS line naming the wrong negative is worse than an unnamed one.
    if len(verdicts) == len(NEGATIVES):
        named = list(zip(NEGATIVES, verdicts))
    else:
        named = [("negative %d" % (i + 1), v) for i, v in enumerate(verdicts)]
        gate.say("NOTE: %d verdict(s) for %d named negatives, so the rows are "
                 "numbered instead of named." % (len(verdicts),
                                                 len(NEGATIVES)))
    for name, (verdict, why) in named:
        gate.say("  %-8s %-22s %s" % (verdict, name, why))
    broke = iso.check_isolation(gate)
    if broke:
        return gate.settle("FAIL", broke)
    fails = [w for v, w in verdicts if v == "FAIL"]
    if fails:
        return gate.settle("FAIL", "; ".join(fails))
    nodata = [w for v, w in verdicts if v == "NO-DATA"]
    if nodata:
        return gate.settle("NO-DATA", "; ".join(nodata))
    return gate.settle("PASS", "all four negatives were refused: %s" %
                       ", ".join(n for n, _ in named))


# ---------------------------------------------------------------------------
# X6: the Claude side.
# ---------------------------------------------------------------------------

#: The runners law, quoted so a NO-DATA on either platform leg says what
#: forbids the obvious "just add a runner" answer. Verbatim from
#: docs/plan/ACTIONS-POLICY-2026-09-01.md, the guardrails section.
RUNNERS_LAW = ("RUNNERS: ubuntu-latest only. macOS and Windows runners never "
               "(the Mac is local and macOS bills 10x).")

#: The estate's own mandatory gate, run on this Mac. Its own header calls it
#: "the cheap mandatory pre-merge contract. Every merge into main runs this
#: locally first", and ACTIONS-POLICY calls the Mac "already the certifying
#: instrument". So this IS the macOS gate: not a new suite invented for the
#: closeout, the one the estate already refuses to merge without.
#:
#: MEASURED, and the reason it is not the ubuntu workflow's own command:
#: `python3 -m unittest discover -s scripts` takes 2540s here (2638 tests) and
#: was still running when a 1800s closeout leg gave up, reporting a timeout as
#: if it were a macOS defect. It also carries three reds that have nothing to
#: do with the platform. A gate that takes 42 minutes and goes red for
#: unrelated reasons is a gate nobody will read.
MACOS_GATE = ["sh", os.path.join("scripts", "required_fast.sh")]

#: The documented local Windows check. products/brothersbe/docs/WINDOWS-CHECK.md
#: says of it: it "simulates Windows conditions and should pass everywhere".
WINDOWS_SIM = os.path.join("products", "brothersbe", "tools",
                           "test_sbe_windows_sim.py")

#: The protocol a real Windows machine follows, for the half no simulation
#: reaches. Named by the leg so nobody reads a green simulation as a platform.
WINDOWS_PROTOCOL = os.path.join("products", "brothersbe", "docs",
                                "WINDOWS-CHECK.md")


def macos_leg(gate, ev):
    """(verdict, why) for the macOS half of item 6.

    THE OLD ANSWER WAS WRONG ABOUT ITS OWN HOST. It reported NO-DATA saying
    the macOS gate needs a macOS Actions runner, on a machine that IS macOS.
    The estate's own policy says the opposite in as many words: "The local Mac
    is the default test machine for everything it can prove", and the ubuntu
    workflow exists only "to confirm the host-neutral scripts on the one
    platform the Mac cannot be". So on a Darwin host the macOS leg is the
    estate's own mandatory gate, run here. On any other host it stays NO-DATA
    and NAMES THE HOST, because a Linux box cannot speak for macOS either.

    It runs against REPO, beside release_invariant.py and export_public.py,
    which is where X6 already puts its process checks. Against the tag
    checkout the same gate reports four reds that are all properties of the
    EXPORT (paths and files the allowlist deliberately drops), so pointing it
    there would measure the allowlist, not the platform.
    """
    host = platform.system()
    gate.say("platform.system() on this machine: %s (%s)" %
             (host, platform.platform()))
    if host != "Darwin":
        return ("NO-DATA", "the macOS leg did not run: platform.system() is "
                           "%s, not Darwin, and a macOS Actions runner is not "
                           "the route (%s)" % (host, RUNNERS_LAW))
    proc = step(gate, ev, "macos estate gate", MACOS_GATE, cwd=REPO,
                timeout=1800,
                needles=("pass ", "fail ", "no-data", "FAILED", "PASS"))
    gate.say("NOTE: this gate's readiness check REGENERATES "
             "docs/plan/READINESS-BOARD.html in %s, stamping this machine's "
             "own paths into it. Check `git status` after a closeout and "
             "revert that file unless the regeneration was wanted." % REPO)
    if proc.returncode != 0:
        return ("FAIL", "the estate's own mandatory gate %s exited %d on this "
                        "macOS host" % (" ".join(MACOS_GATE),
                                        proc.returncode))
    return ("PASS", "the estate's own mandatory gate %s exited 0 on macOS "
                    "(%s), no runner needed" % (" ".join(MACOS_GATE), host))


def windows_leg(gate, ev):
    """(verdict, why) for the Windows half of item 6.

    A Windows runner is forbidden and a Windows machine is not here, so the
    question is what CAN be run. products/brothersbe/docs/WINDOWS-CHECK.md
    answers it: tools/test_sbe_windows_sim.py "recreates the conditions that
    make Windows different (separator handling, encoding, newline rewriting,
    hook shape) on whatever machine runs it", and "should pass everywhere".
    That is a real Windows check that runs on this host, so it runs.

    It runs against REPO, not the tag checkout, on the same reasoning that
    already puts release_invariant.py and export_public.py there: its
    assertions are about the REPOSITORY (the .gitattributes that pins line
    endings, the templates the installer offers), and the export deliberately
    drops some of those. What it can never cover is the half that needs the
    Windows kernel, so the leg says so rather than letting a green simulation
    read as a proven platform.
    """
    script = os.path.join(REPO, WINDOWS_SIM)
    if not os.path.isfile(script):
        return ("NO-DATA", "no local Windows check on disk at %s, and a "
                           "Windows Actions runner is not the route (%s)" %
                (script, RUNNERS_LAW))
    proc = step(gate, ev, "windows simulation suite", [sys.executable, script],
                cwd=REPO, timeout=900,
                needles=("Ran ", "OK", "FAILED", "FAIL:"))
    gate.say("the Windows simulation covers separator handling, encoding, "
             "newline rewriting and hook shape. It cannot reach anything "
             "needing the Windows kernel (open handles, path length limits, "
             "process cost), which stays the hand-run protocol in %s." %
             WINDOWS_PROTOCOL)
    if proc.returncode != 0:
        return ("FAIL", "the documented Windows simulation %s exited %d" %
                (WINDOWS_SIM, proc.returncode))
    return ("PASS", "the documented Windows simulation %s exited 0 (a "
                    "simulation, never a real Windows machine)" % WINDOWS_SIM)


def hub_versus_tag(gate, ev, tree):
    """Print how far THIS checkout has drifted from the tag. Never a verdict.

    The drift is real and worth reading: a post-tag merge that touches
    bundle/runtime does break the "no shipped-runtime mutation under the same
    version" rule for the NEXT cut. But the gate that owns that complaint is
    the estate's own battery (scripts/required_fast.sh runs release_invariant
    against this tree, and X6 runs that battery in its macOS leg), not a
    post-tag closeout gate whose subject is the released artifact. So this
    runs, prints, and decides nothing.
    """
    hub_tool = os.path.join(REPO, "scripts", "release_invariant.py")
    if os.path.realpath(tree) == os.path.realpath(REPO):
        gate.say("hub main versus the tag: NO-DATA (the tree under test IS "
                 "this checkout, so there is no second tree to compare)")
        return
    if not os.path.isfile(hub_tool):
        gate.say("hub main versus the tag: NO-DATA (no %s on disk)" % hub_tool)
        return
    proc = step(gate, ev, "hub main versus the tag (informational)",
                [sys.executable, hub_tool, "--public-checkout", tree],
                cwd=REPO, timeout=900,
                needles=("OK:", "CONTRADICTS", "NO-DATA", "FAIL"))
    reading = {0: "agrees", 2: "NO-DATA"}.get(proc.returncode, "DRIFTED")
    gate.say("hub main versus the tag: %s (this checkout's own "
             "release_invariant.py exited %d in %s). INFORMATIONAL ONLY: X6 "
             "judges the tagged artifact, and this line judges the working "
             "checkout, which is the estate battery's own row." %
             (reading, proc.returncode, REPO))


def invariant_leg(gate, ev, tree):
    """(verdict, why) for the release-identity half of item 6.

    THE SUBJECT IS THE TAGGED ARTIFACT. This leg used to run the HUB's
    scripts/release_invariant.py, whose ROOT is the hub, with
    --public-checkout pointed at the tag clone: that compares the HUB's
    working bundle against the tag, so the first post-tag merge touching
    bundle/runtime turned X6 red. Measured on 1.0.3 the night PR 256 landed:
    "shipped runtime content has changed since tag v1.0.3 but the version
    string is still 1.0.3; 2 of 34 shipped runtime file(s) differ", with
    every other X6 leg green.

    docs/plan/RELEASE-POLICY.md item 6 says the subject of a post-tag gate is
    the released artifact after the final tag, so the leg now runs the TAG
    CLONE'S OWN copy of the tool, inside the tag clone, pointed at itself:
    the tag's tree must agree with itself and with its own links. The hub's
    drift is printed beside the verdict by hub_versus_tag, never inside it.
    """
    subject = os.path.join(tree, "scripts", "release_invariant.py")
    if not os.path.isfile(subject):
        hub_versus_tag(gate, ev, tree)
        return ("NO-DATA", "the tree under test ships no scripts/"
                           "release_invariant.py at %s, so the released "
                           "artifact cannot check its own identity" % subject)
    inv = step(gate, ev, "release invariant (inside the tagged artifact)",
               [sys.executable, subject, "--public-checkout", tree], cwd=tree,
               timeout=900, needles=("OK:", "CONTRADICTS", "NO-DATA", "FAIL"))
    hub_versus_tag(gate, ev, tree)
    if inv.returncode == 2:
        return ("NO-DATA", "release_invariant.py exited 2 inside %s" % tree)
    if inv.returncode != 0:
        return ("FAIL", "the tagged artifact's own release_invariant.py "
                        "exited %d inside %s: the released tree contradicts "
                        "itself" % (inv.returncode, tree))
    return ("PASS", "release_invariant.py exited 0 inside the tagged "
                    "artifact at %s" % tree)


def gate_claude_side(args, ev, gate):
    if not args.tag_checkout:
        # X6's subject is the released artifact, so this gate needs the tag
        # checkout. Under `all` X7 runs first and hands one over; run on its
        # own, X6 clones the tag itself rather than quietly falling back to
        # the working tree and reporting on the wrong thing.
        checkout, why = fetch_tag(args.work, "v%s" % args.version,
                                  args.public_url, gate, ev)
        if checkout is None:
            gate.say("no tag checkout: %s" % why)
        else:
            args.tag_checkout = checkout
    tree = args.tag_checkout or REPO
    gate.revision = ("the tag checkout at %s" % tree) if args.tag_checkout \
        else ("the working tree at %s (NO public tag checkout: X7 is the gate "
              "that produces one)" % tree)
    verdicts = []

    if args.tag_checkout:
        e2e = step(gate, ev, "clean install e2e",
                   ["sh", os.path.join(tree, "scripts", "clean_install_e2e.sh")],
                   cwd=tree, timeout=1800,
                   needles=("pass,", "fail,", "no-data", "BLOCKED"))
        if e2e.returncode == 2:
            verdicts.append(("NO-DATA", "clean_install_e2e.sh reported "
                                        "BLOCKED (exit 2)"))
        elif e2e.returncode != 0:
            verdicts.append(("FAIL", "clean_install_e2e.sh exited %d" %
                             e2e.returncode))
        else:
            verdicts.append(("PASS", "clean_install_e2e.sh exited 0 against "
                                     "the tag checkout"))
    else:
        verdicts.append(("NO-DATA", "clean_install_e2e.sh takes no ref flag, "
                                    "so running it against the public tag "
                                    "needs the tag checkout X7 clones, and "
                                    "there is none"))

    for product in ("brothermode", "brothersbe"):
        script = os.path.join(tree, "products", product, "scripts",
                              "verify-install.sh")
        if not os.path.isfile(script):
            verdicts.append(("NO-DATA", "no verify-install.sh for %s at %s" %
                             (product, script)))
            continue
        proc = step(gate, ev, "verify install %s" % product, ["sh", script],
                    cwd=os.path.join(tree, "products", product), timeout=900,
                    needles=("OK", "FAIL", "PASS", "NO-DATA", "mismatch"))
        if proc.returncode == 2:
            verdicts.append(("NO-DATA", "%s verify-install.sh exited 2" %
                             product))
        elif proc.returncode != 0:
            verdicts.append(("FAIL", "%s verify-install.sh exited %d" %
                             (product, proc.returncode)))
        else:
            verdicts.append(("PASS", "%s verify-install.sh exited 0" %
                             product))

    verdicts.append(invariant_leg(gate, ev, tree))

    exp = step(gate, ev, "export dry run",
               [sys.executable, os.path.join(REPO, "scripts",
                                             "export_public.py"), "--dry-run"],
               timeout=1800, needles=("PASS", "FAIL", "NO-DATA", "would",
                                      "REFUSED"))
    if exp.returncode == 2:
        verdicts.append(("NO-DATA", "export_public.py --dry-run exited 2"))
    elif exp.returncode != 0:
        verdicts.append(("FAIL", "export_public.py --dry-run exited %d" %
                         exp.returncode))
    else:
        verdicts.append(("PASS", "export_public.py --dry-run exited 0"))

    verdicts.append(macos_leg(gate, ev))
    verdicts.append(windows_leg(gate, ev))
    if args.actions_run_id:
        gate.say("Linux Actions run reported by the orchestrator: %s" %
                 args.actions_run_id)
        verdicts.append(("PASS", "the Linux Actions run is the orchestrator's "
                                 "dispatch, id %s" % args.actions_run_id))
    else:
        verdicts.append(("NO-DATA", "no --actions-run-id given, so the Linux "
                                    "Actions run is unreported here"))

    for verdict, why in verdicts:
        gate.say("  %-8s %s" % (verdict, why))
    fails = [w for v, w in verdicts if v == "FAIL"]
    if fails:
        return gate.settle("FAIL", "; ".join(fails))
    nodata = [w for v, w in verdicts if v == "NO-DATA"]
    if nodata:
        return gate.settle("NO-DATA", "%d leg(s) could not run: %s" %
                           (len(nodata), nodata[0]))
    return gate.settle("PASS", "every Claude-side leg exited 0")


# ---------------------------------------------------------------------------
# X7: the public artifact itself.
# ---------------------------------------------------------------------------

def fetch_tag(work, tag, url, gate, ev):
    """(checkout, why). git clone --branch <tag> --depth 1 into a temp dir.
    A tag that does not exist on the remote is NO-DATA, not a failure of the
    release: it means the cut has not been published yet."""
    dest = os.path.join(work, "tag")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    proc = step(gate, ev, "clone the public tag",
                ["git", "clone", "--branch", tag, "--depth", "1", url, dest],
                timeout=900, needles=("Cloning", "warning", "fatal", "error"))
    if proc.returncode != 0:
        return None, "git clone --branch %s --depth 1 %s exited %d" % (
            tag, url, proc.returncode)
    return dest, ""


#: The line the release note generator writes for the private source commit.
#: Read, never typed: a hand-copied revision is a second place to be wrong.
SOURCE_REV_RE = re.compile(r"Cut from hub commit `([0-9a-f]{7,40})`")


def source_rev_from_note(note):
    """(rev, why): the hub revision the tag's own release note names."""
    try:
        with open(note, "r", encoding="utf-8") as fh:
            body = fh.read()
    except OSError as exc:
        return None, "could not read %s: %s" % (note, exc)
    found = SOURCE_REV_RE.search(body)
    if not found:
        return None, "%s names no source revision in the form the generator " \
                     "writes, so there is no revision to rebuild from" % note
    return found.group(1), ""


def manifest_self_consistency(args, ev, gate, checkout):
    """(verdict, why): SIDE ONE. Does the published tag agree with the
    manifest it ships?

    THE LEG THIS REPLACES COMPARED THE WRONG TWO THINGS. It hashed
    bundle/runtime/RUNTIME-MANIFEST.json and looked for THAT digest in the
    release note, found nothing, and concluded the note "carries no manifest
    digest ... so the comparison has no second side". The note does carry one:
    it states the digest of docs/releases/<version>.export-manifest.txt, over
    every exported file, and the tag ships both the manifest and a reader for
    it. The second side was there the whole time under a different name.

    So this side runs the tag's OWN reader, from the clone, exactly as the
    note tells a stranger to: it checks the manifest's digest against the one
    the note states, then re-hashes every file the manifest names. It needs no
    hub access and no network, which is what makes it the half anyone can
    reproduce.
    """
    reader = os.path.join(checkout, "scripts", "reproduce_export.py")
    if not os.path.isfile(reader):
        return ("NO-DATA", "the tag ships no scripts/reproduce_export.py, so "
                           "the manifest it ships has no reader and this side "
                           "cannot run")
    proc = step(gate, ev, "tag manifest self consistency",
                [sys.executable, reader, "--verify-tree", "--tag",
                 "v%s" % args.version],
                cwd=checkout, timeout=1800,
                needles=("PASS", "FAIL", "NO-DATA", "MISMATCH", "MISSING"))
    if proc.returncode == 2:
        return ("NO-DATA", "reproduce_export.py --verify-tree exited 2 in the "
                           "published tag, so the manifest or the stated "
                           "digest is not there to read")
    if proc.returncode != 0:
        return ("FAIL", "the published tag does NOT match the export manifest "
                        "it ships: reproduce_export.py --verify-tree exited "
                        "%d in the clone" % proc.returncode)
    return ("PASS", "every file docs/releases/%s.export-manifest.txt names "
                    "hashes to the value it names, and the manifest's own "
                    "digest matches the release note" % args.version)


def manifest_against_source(args, ev, gate, checkout):
    """(verdict, why): SIDE TWO. Does the published tag agree with the hub
    revision its own note says it was cut from?

    Side one can only prove the tag is INTERNALLY consistent: a manifest
    generated from the same wrong bytes would agree with them. This side is
    the outside reference. It regenerates the allowlisted export from the
    revision the note names and compares byte for byte against what the tag
    carries, which is the only check that can catch bytes changing between
    the cut and the push.

    It needs the private hub, so it is NO-DATA anywhere the revision does not
    resolve, and says which revision and which checkout, never a bare "could
    not run".
    """
    note = os.path.join(checkout, "docs", "releases", "%s.md" % args.version)
    if not os.path.isfile(note):
        return ("NO-DATA", "the tag ships no docs/releases/%s.md, so no "
                           "source revision is named to rebuild from" %
                args.version)
    rev, why = source_rev_from_note(note)
    if rev is None:
        return ("NO-DATA", why)
    gate.say("source revision named by docs/releases/%s.md: %s" %
             (args.version, rev))
    seen = codex_smoke.sh(["git", "-C", REPO, "cat-file", "-t", rev],
                          timeout=120)
    if seen.returncode != 0 or (seen.stdout or "").strip() != "commit":
        return ("NO-DATA", "%s does not resolve %s (git cat-file exited %d), "
                           "so the export cannot be rebuilt from the revision "
                           "the note names; this side needs the private hub" %
                (REPO, rev, seen.returncode))
    reader = os.path.join(REPO, "scripts", "reproduce_export.py")
    if not os.path.isfile(reader):
        return ("NO-DATA", "no scripts/reproduce_export.py in %s, so nothing "
                           "here can rebuild the export" % REPO)
    proc = step(gate, ev, "tag rebuilt from its source revision",
                [sys.executable, reader, "--source-rev", rev, "--tag",
                 "v%s" % args.version, "--public", checkout],
                cwd=REPO, timeout=1800,
                needles=("reproduce-export", "MISMATCH", "MISSING", "PASS",
                         "FAIL", "NO-DATA"))
    if proc.returncode == 2:
        return ("NO-DATA", "reproduce_export.py --source-rev %s exited 2, so "
                           "the source tree, the allowlist or the tag tree "
                           "could not be read" % rev)
    if proc.returncode != 0:
        return ("FAIL", "the published tag does NOT reproduce from the hub "
                        "revision its note names (%s): reproduce_export.py "
                        "exited %d" % (rev, proc.returncode))
    return ("PASS", "every allowlisted path in the tag reproduces byte for "
                    "byte from hub revision %s, the one docs/releases/%s.md "
                    "names" % (rev, args.version))


def tag_signature_verified(gate, ev, checkout, tag):
    """(verdict, why): does the published tag carry a GPG signature that
    verifies? Row S5 (roadmap) is founder gated: the signing key is his
    alone, so a tag with no signature at all is NO-DATA, never a FAIL. FAIL
    is reserved for a signature that IS present but does not check out,
    which is the only shape that means something actually went wrong.

    THIS IS AN INFORMATIONAL READING, NOT A LEG OF X7, since 1.0.5. It was
    folded in as a required leg, and because no key exists the leg could not
    run, so X7 settled NO-DATA on it and the release matrix read NOT
    COMPLETE with every other gate PASS. A gate cannot decide on a leg whose
    only possible answer is "the founder has not done his own step yet":
    that is what row S5 is for. X7 decides on REPRODUCTION alone, and this
    line says what the signature looks like beside it, the same shape as the
    hub drift line in X6."""
    proc = step(gate, ev, "tag signature", ["git", "tag", "-v", tag],
                cwd=checkout, timeout=120,
                needles=("gpg", "Good signature", "BAD", "error",
                         "no signature"))
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if proc.returncode == 0:
        return ("PASS", "git tag -v %s verifies a good signature" % tag)
    if "bad signature" in combined:
        return ("FAIL", "git tag -v %s reports a BAD signature, not "
                        "merely an absent one" % tag)
    last = combined.strip().splitlines()[-1] if combined.strip() else \
        "no output"
    return ("NO-DATA", "tag signing: NO-DATA: no signing key configured "
                       "(S5, founder); git tag -v %s found no signature "
                       "(%s)" % (tag, last))


def gate_public_artifact(args, ev, gate):
    tag = "v%s" % args.version
    url = args.public_url
    gate.revision = "%s at %s" % (url, tag)

    ls = step(gate, ev, "does the tag exist on the remote",
              ["git", "ls-remote", "--tags", url, "refs/tags/%s" % tag],
              timeout=300, needles=(tag,))
    if ls.returncode != 0:
        return gate.settle("NO-DATA", "git ls-remote against %s exited %d, so "
                                      "the published artifact could not be "
                                      "read" % (url, ls.returncode))
    if tag not in (ls.stdout or ""):
        return gate.settle("NO-DATA", "%s carries no tag %s yet, so there is "
                                      "no published artifact to verify" %
                           (url, tag))

    checkout, why = fetch_tag(args.work, tag, url, gate, ev)
    if checkout is None:
        return gate.settle("NO-DATA", why)
    args.tag_checkout = checkout
    gate.say("tag checkout: %s" % checkout)
    verdicts = []

    for product in ("brothermode", "brothersbe"):
        root = os.path.join(checkout, "products", product)
        script = os.path.join(root, "scripts", "verify-install.sh")
        if not os.path.isfile(script):
            verdicts.append(("NO-DATA", "the tag ships no %s" %
                             os.path.relpath(script, checkout)))
            continue
        proc = step(gate, ev, "tag verify install %s" % product,
                    ["sh", script], cwd=root, timeout=900,
                    needles=("OK", "FAIL", "mismatch", "PASS", "NO-DATA"))
        if proc.returncode != 0:
            verdicts.append(("FAIL", "%s CHECKSUMS do not verify in the "
                                     "published tag (exit %d)" %
                             (product, proc.returncode)))
        else:
            verdicts.append(("PASS", "%s CHECKSUMS verify in the published "
                                     "tag" % product))

    runtime = os.path.join(checkout, "bundle", "runtime", "verify_runtime.py")
    if not os.path.isfile(runtime):
        verdicts.append(("NO-DATA", "the tag ships no bundle/runtime/"
                                    "verify_runtime.py"))
    else:
        proc = step(gate, ev, "tag verify runtime", [sys.executable, runtime],
                    cwd=os.path.join(checkout, "bundle", "runtime"),
                    timeout=600,
                    needles=("PASS", "FAIL", "NO-DATA", "verify_runtime"))
        if proc.returncode == 2:
            verdicts.append(("NO-DATA", "verify_runtime.py exited 2 in the "
                                        "published tag"))
        elif proc.returncode != 0:
            verdicts.append(("FAIL", "verify_runtime.py exited %d in the "
                                     "published tag" % proc.returncode))
        else:
            verdicts.append(("PASS", "bundle/runtime verifies in the "
                                     "published tag"))

    verdicts.append(manifest_self_consistency(args, ev, gate, checkout))
    verdicts.append(manifest_against_source(args, ev, gate, checkout))

    for verdict, why in verdicts:
        gate.say("  %-8s %s" % (verdict, why))
    # INFORMATIONAL ONLY, never a leg: see tag_signature_verified for why a
    # founder-gated signing key cannot decide a reproduction gate.
    sig_verdict, sig_why = tag_signature_verified(gate, ev, checkout, tag)
    gate.say("  tag signing: %s (INFORMATIONAL ONLY: row S5 is the founder's "
             "own gate, so X7 decides on reproduction alone). %s"
             % (sig_verdict, sig_why))
    fails = [w for v, w in verdicts if v == "FAIL"]
    if fails:
        return gate.settle("FAIL", "; ".join(fails))
    nodata = [w for v, w in verdicts if v == "NO-DATA"]
    if nodata:
        return gate.settle("NO-DATA", "%d leg(s) could not run: %s" %
                           (len(nodata), nodata[0]))
    return gate.settle("PASS", "the published tag's own tree verifies")


# ---------------------------------------------------------------------------
# X8: the founder's half.
# ---------------------------------------------------------------------------

def gate_founder(args, ev, gate):
    runbook = os.path.join(REPO, RUNBOOK)
    gate.revision = "v%s" % args.version
    gate.say("runbook: %s" % runbook)
    if not os.path.isfile(runbook):
        return gate.settle("NO-DATA", "the runbook %s is not on disk, so the "
                                      "founder has nothing to run" % runbook)
    gate.say("An isolated Codex home holds no credentials, so no script here "
             "can drive a real signed-in turn. This gate is the founder's "
             "hand, and the runbook above is exactly what he runs.")
    gate.say("The command it ends on, defined once in codex_smoke and "
             "mirrored by the runbook and by C7:")
    gate.say("  %s" % codex_smoke.documented_shell_command())
    gate.say("Both flags are this gate's own 2026-09-04 failure, not a "
             "Brother failure: Codex defaults to the read-only sandbox, which "
             "refuses every patch, and plain workspace-write still refuses "
             "the .git write Brother's unit isolation needs.")
    return gate.settle("FOUNDER", "needs a signed-in Codex session; runbook "
                                  "%s" % runbook)


# ---------------------------------------------------------------------------
# S4: the virgin-install workflow dispatch, per release candidate.
# ---------------------------------------------------------------------------

#: The dispatch-only workflow in the PUBLIC repository (.github/workflows/
#: virgin-install.yml), never the hub. It fires only on workflow_dispatch,
#: per docs/plan/ACTIONS-POLICY-2026-09-01.md.
VIRGIN_WORKFLOW = "virgin-install.yml"

#: Same TTL the cost wall already enforces on the same grant file, so one
#: number governs both: ~/.claude/hooks/github_cost_wall.py, GRANT_TTL_HOURS.
VIRGIN_GRANT_TTL_HOURS = 24

#: The wall's own grant path (github_cost_wall.py, GRANT). One file serves
#: both: the wall checks repo and workflow appear in the `gh workflow run`
#: command it is about to allow, and this gate additionally requires the
#: grant to name THIS tag, so a grant minted for v1.0.3 cannot silently
#: authorise a v1.0.4 dispatch.
DEFAULT_VIRGIN_GRANT = os.path.expanduser("~/.claude/actions-grant.json")


def repo_slug_from_url(url):
    """"https://github.com/owner/name" -> "owner/name", read off PUBLIC_URL
    rather than typed a second time."""
    return url.rstrip("/").split("github.com/")[-1]


def read_virgin_grant(path):
    """(grant_dict, why). why is set only when grant_dict is None."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
    except OSError as exc:
        return None, "no grant at %s (%s)" % (path, exc)
    try:
        grant = json.loads(body)
    except ValueError as exc:
        return None, "the grant at %s is not valid JSON: %s" % (path, exc)
    if not isinstance(grant, dict):
        return None, "the grant at %s is not a JSON object" % path
    return grant, ""


def virgin_grant_ok(grant, tag, now=None):
    """(ok, why): does this grant authorise a virgin-install dispatch for
    exactly this tag, right now? Mirrors github_cost_wall.py's own age check
    on the same "issued" field, plus the tag match this gate adds."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    grant_tag = str(grant.get("tag") or "")
    if grant_tag != tag:
        return False, "the grant names tag %s, not %s" % (
            grant_tag or "(none)", tag)
    try:
        issued = datetime.datetime.fromisoformat(
            str(grant.get("issued", "")).replace("Z", "+00:00"))
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return False, "the grant carries no readable 'issued' timestamp, so " \
                      "its age cannot be checked"
    age_h = (now - issued).total_seconds() / 3600.0
    if age_h > VIRGIN_GRANT_TTL_HOURS or age_h < 0:
        return False, "the grant is %.1f hours old (limit %d), so it has " \
                      "expired" % (age_h, VIRGIN_GRANT_TTL_HOURS)
    return True, "grant for %s issued %.1fh ago, within the %dh limit" % (
        tag, age_h, VIRGIN_GRANT_TTL_HOURS)


def gate_virgin_install(args, ev, gate):
    tag = "v%s" % args.version
    gate.revision = "%s workflow %s at %s" % (args.repo, VIRGIN_WORKFLOW, tag)

    dispatch_cmd = ["gh", "workflow", "run", VIRGIN_WORKFLOW, "--ref", tag]
    list_cmd = ["gh", "run", "list", "--workflow", VIRGIN_WORKFLOW,
                "--branch", tag]
    gate.say("dispatch command: %s" % " ".join(dispatch_cmd))
    gate.say("list command: %s" % " ".join(list_cmd))

    grant, why = read_virgin_grant(args.grant)
    if grant is None:
        gate.say(why)
        return gate.settle("NO-DATA", "%s; the matrix stays BLOCKED without "
                                      "a per-tag founder grant" % why)
    ok, detail = virgin_grant_ok(grant, tag)
    gate.say(detail)
    if not ok:
        return gate.settle("NO-DATA", detail)

    if not args.dispatch:
        return gate.settle("NO-DATA", "grant for %s is valid; pass "
                                      "--dispatch to actually run the two "
                                      "commands above" % tag)

    env = dict(os.environ)
    env["GH_REPO"] = args.repo
    disp = step(gate, ev, "gh workflow run virgin-install", dispatch_cmd,
                env=env, needles=("error", "Error"))
    if disp.returncode != 0:
        return gate.settle("FAIL", "gh workflow run exited %d" %
                           disp.returncode)

    poll_cmd = list_cmd + ["--json", "databaseId,status,conclusion",
                           "--limit", "5"]
    deadline = time.monotonic() + args.poll_timeout
    run_id, status, conclusion = None, None, None
    while True:
        poll = step(gate, ev, "gh run list poll", poll_cmd, env=env,
                    needles=("databaseId", "status", "conclusion"))
        if poll.returncode == 0:
            try:
                runs = json.loads(poll.stdout or "[]")
            except ValueError:
                runs = []
            if runs:
                run_id = runs[0].get("databaseId")
                status = runs[0].get("status")
                conclusion = runs[0].get("conclusion")
                if status == "completed":
                    break
        if time.monotonic() >= deadline:
            break
        time.sleep(args.poll_interval)

    gate.say("run id: %s   status: %s   conclusion: %s   tag: %s" %
             (run_id, status, conclusion, tag))
    if run_id is None:
        return gate.settle("NO-DATA", "gh run list never reported a run for "
                                      "%s on %s" % (VIRGIN_WORKFLOW, tag))
    if status != "completed":
        return gate.settle("NO-DATA", "run %s did not complete within %ss "
                                      "(last status %s)" %
                           (run_id, args.poll_timeout, status))
    if conclusion != "success":
        return gate.settle("FAIL", "run %s concluded %s for %s" %
                           (run_id, conclusion, tag))
    return gate.settle("PASS", "run %s concluded success for %s" %
                       (run_id, tag))


GATE_FUNCS = {
    "virgin-codex": gate_virgin_codex,
    "upgrade-codex": gate_upgrade_codex,
    "reinstall-idempotent": gate_reinstall_idempotent,
    "uninstall-reinstall": gate_uninstall_reinstall,
    "negatives": gate_negatives,
    "claude-side": gate_claude_side,
    "public-artifact": gate_public_artifact,
    "founder": gate_founder,
}

#: Gates that must have a working Codex binary. The others do not.
NEEDS_CODEX = {"virgin-codex", "upgrade-codex", "reinstall-idempotent",
               "uninstall-reinstall", "negatives"}


def declared_version(root=REPO):
    """The version this tree declares, read from the same place
    release_invariant.py reads it, never typed here."""
    try:
        import release_invariant
    except ImportError as exc:
        return None, "could not import release_invariant: %s" % exc
    try:
        bundle_version, marketplace_version = \
            release_invariant.declared_versions(root)
    except (OSError, ValueError) as exc:
        return None, "release_invariant.declared_versions failed: %s" % exc
    version = bundle_version or marketplace_version
    if not version:
        return None, "neither version site in %s declares a version" % root
    return version, ""


def print_gate(gate):
    print()
    print("== %s %s   %s" % (gate.id, gate.name, gate.verdict))
    print("   %s" % gate.title)
    print("   revision under test: %s" % gate.revision)
    for line in gate.lines:
        print("   %s" % line)
    print("   %s: %s" % (gate.verdict, gate.why))


def run_gate(name, args, ev):
    gid, _, title, required = [g for g in GATE_ORDER if g[1] == name][0]
    gate = Gate(gid, name, title, required)
    gate.codex_bin = args.codex_bin
    if name in NEEDS_CODEX and not (os.path.isfile(args.codex_bin) and
                                    os.access(args.codex_bin, os.X_OK)):
        gate.settle("NO-DATA", "no executable Codex binary at %s, so nothing "
                               "was installed" % args.codex_bin)
        return gate
    try:
        GATE_FUNCS[name](args, ev, gate)
    except (OSError, ValueError, KeyError) as exc:
        gate.settle("FAIL", "the gate itself raised %s: %s" %
                    (type(exc).__name__, exc))
    return gate


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gate", choices=sorted(GATE_FUNCS) + ["all", "virgin"],
                    help="which gate to run, `all` for the whole matrix, or "
                         "`virgin` for the S4 virgin-install dispatch")
    ap.add_argument("--marketplace", default=PUBLIC_URL,
                    help="the marketplace source: a local path or an HTTPS "
                         "Git URL (default: the public repository)")
    ap.add_argument("--ref", default=None,
                    help="git ref for a Git marketplace source, normally the "
                         "release tag")
    ap.add_argument("--codex-bin", default=DEFAULT_CODEX,
                    help="the Codex binary to drive (default: the "
                         "app-bundled one)")
    ap.add_argument("--evidence-dir", default=None,
                    help="where every command's WHOLE output is kept "
                         "(default: ~/.claude/evidence/closeout-<version>)")
    ap.add_argument("--work", default=None,
                    help="throwaway directory for isolated homes and "
                         "checkouts (default: a mkdtemp, wiped on exit)")
    ap.add_argument("--version", default=None,
                    help="the version under closeout (default: read from the "
                         "tree's own manifests)")
    ap.add_argument("--public-url", default=PUBLIC_URL,
                    help="the public repository the tag is published to")
    ap.add_argument("--public-checkout", default=REPO,
                    help="a local checkout whose tags name the release "
                         "history (default: this checkout)")
    ap.add_argument("--actions-run-id", default=None,
                    help="the Linux GitHub Actions run id the orchestrator "
                         "dispatched, reported verbatim in X6")
    ap.add_argument("--grant", default=DEFAULT_VIRGIN_GRANT,
                    help="verb `virgin` only: path to the per-tag "
                         "virgin-install dispatch grant (default: the same "
                         "file github_cost_wall.py reads)")
    ap.add_argument("--dispatch", action="store_true",
                    help="verb `virgin` only: actually run gh workflow run "
                         "and wait for it; without this flag the two "
                         "commands are only printed")
    ap.add_argument("--repo", default=None,
                    help="verb `virgin` only: owner/name for the "
                         "virgin-install workflow (default: read off "
                         "--public-url)")
    ap.add_argument("--poll-timeout", type=float, default=1200.0,
                    help="verb `virgin` only: seconds to wait for the "
                         "dispatched run to complete")
    ap.add_argument("--poll-interval", type=float, default=15.0,
                    help="verb `virgin` only: seconds between gh run list "
                         "polls")
    args = ap.parse_args(argv)
    if not args.repo:
        args.repo = repo_slug_from_url(args.public_url)

    if not args.version:
        version, why = declared_version()
        if version is None:
            print("NO-DATA: %s, so there is no version to close out." % why)
            return 2
        args.version = version
    args.tag_checkout = None

    if not args.evidence_dir:
        args.evidence_dir = os.path.expanduser(
            "~/.claude/evidence/closeout-%s" % args.version)
    try:
        os.makedirs(args.evidence_dir, exist_ok=True)
    except OSError as exc:
        print("NO-DATA: could not create the evidence directory %s: %s" %
              (args.evidence_dir, exc))
        return 2
    ev = Evidence(args.evidence_dir)

    temp_work = None
    if not args.work:
        temp_work = tempfile.mkdtemp(prefix="brother-closeout.")
        args.work = temp_work
    # REALPATH, and this line is load bearing. On macOS mkdtemp hands back a
    # path under /var/folders, and /var is a symlink to /private/var. Codex
    # canonicalizes CODEX_HOME, so a hooks.json written under the /var
    # spelling is read back under the /private/var one: `codex hooks list`
    # then reports 0 hooks from a file holding 18, and the trust state comes
    # back `untrusted`. Measured here on 2026-09-04: X1 FAILED under
    # /var/folders and PASSED byte for byte under the same directory's
    # realpath, with nothing else changed.
    args.work = os.path.realpath(args.work)
    try:
        os.makedirs(args.work, exist_ok=True)
    except OSError as exc:
        print("NO-DATA: could not create the work directory %s: %s" %
              (args.work, exc))
        return 2

    print("release_closeout: version %s" % args.version)
    print("marketplace: %s%s" % (args.marketplace,
                                 (" ref %s" % args.ref) if args.ref
                                 else " (no ref)"))
    print("codex binary: %s" % args.codex_bin)
    print("evidence (whole output of every command): %s" % args.evidence_dir)

    try:
        if args.gate == "virgin":
            gate = Gate("S4", "virgin-install",
                        "virgin-install workflow dispatched per release "
                        "candidate, quoted into X6", True)
            try:
                gate_virgin_install(args, ev, gate)
            except (OSError, ValueError, KeyError) as exc:
                gate.settle("FAIL", "the gate itself raised %s: %s" %
                            (type(exc).__name__, exc))
            print_gate(gate)
            return gate.exit_code()
        if args.gate != "all":
            gate = run_gate(args.gate, args, ev)
            print_gate(gate)
            return gate.exit_code()
        gates = []
        # X7 runs before X6 on purpose: it produces the tag checkout X6 needs
        # to run the Claude side against the published artifact rather than
        # the working tree.
        order = ["virgin-codex", "upgrade-codex", "reinstall-idempotent",
                 "uninstall-reinstall", "negatives", "public-artifact",
                 "claude-side", "founder"]
        by_name = {}
        for name in order:
            gate = run_gate(name, args, ev)
            print_gate(gate)
            by_name[name] = gate
        gates = [by_name[g[1]] for g in GATE_ORDER]
        text, code = verdict_table(gates)
        print()
        print("THE CLOSEOUT MATRIX, version %s" % args.version)
        print(text)
        print()
        print("evidence: %s" % args.evidence_dir)
        return code
    finally:
        if temp_work and os.path.isdir(temp_work):
            try:
                shutil.rmtree(temp_work)
            except OSError as exc:
                print("(could not remove the work directory %s: %s)" %
                      (temp_work, exc))


if __name__ == "__main__":
    sys.exit(main())
