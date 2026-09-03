#!/usr/bin/env python3
"""tools/brothermode_cli.py: the one deterministic public runtime boundary
(V3-FREEZE-2026-08-07.md answer 4; plan section "Agent 4, Runtime Boundary
Engineer", ~/Downloads/BrotherMode_v3_Gap_Closure_Execution_Plan.md).

WHAT THIS IS
  A THIN DISPATCH layer over the internal adapters BrotherMode already
  ships: tools/bm_project.py (start, next, review, deliver), tools/
  bm_lead.py (status), tools/bm_view.py (view), scripts/doctor.py
  (doctor), tools/bm_autosave.py (recover), and tools/bm_project_facts.py
  plus scripts/install.py (version, update). Every pass-through
  subcommand below calls the SAME function the matching internal tool's
  own CLI already calls, in process, with the SAME argv. Its output is
  not a re-description of that tool's behavior, it is that tool's
  behavior: tools/test_brothermode_cli.py's load-bearing test proves this
  for `status` by running both binaries as real subprocesses and diffing
  their stdout byte for byte.

WHY THIS EXISTS
  Freeze answer 4: "Single public runtime boundary: the brothermode CLI
  (start, status, next, review, deliver, view, doctor, recover, version,
  update). Skills call ONLY it. tools/bm_*.py become internal adapters
  behind it." The architecture refutation's H4 named the risk directly:
  a boundary that exists in name only, with the seventeen bm-* console
  scripts still public and every skill still calling tools/bm_project.py
  by path, is not a boundary, it is a rule nobody can check. This file
  is Agent 4's answer: ONE file a skill (or a founder) can call, thin
  enough that reading it end to end proves it duplicates nothing.

SUBCOMMANDS
  start     tools/bm_project.py start (project creation)
  status    tools/bm_lead.py status (the founder's eight-field view;
            THE load-bearing byte-identity contract lives on this verb)
  next      tools/bm_project.py next (the one recommended next task)
  review    tools/bm_project.py review (evidence + transition, one call)
  deliver   tools/bm_project.py deliver (the delivery packet)
  view      tools/bm_view.py render (the generated project-view page;
            NON-AUTHORITATIVE, a render, never a source of truth,
            freeze answer 3)
  doctor    scripts/doctor.py (the eleven install-health checks)
  recover   tools/bm_autosave.py's real recovery read path; per
            V3-FREEZE-2026-08-07.md's Refutation adjudication ruling B1,
            recovery here means COMPACT-BOUNDARY autosave snapshots
            ONLY (a kill -9, a crashed terminal, an OOM, or Ctrl-C
            before the next compaction leaves nothing to recover), and
            this subcommand says so out loud every time, not only when
            material happens to be absent
  version   the VERSION file's content, read through scripts/install.py
            read_version, the same reader install.py itself uses to
            report what it just installed
  continue  tools/bm_continue.py continue (the handoff packet
            generated from store rows, plus the successor launch;
            --dry-run writes the packet, prints the command, and
            launches nothing)
  update    a read-only report: the installed version against the
            newest published release tag, and the exact steps for the
            caller's own install path (commands/brotherme-update.md's
            own steps 1 through 6, automated as a lookup, never as an
            action: this subcommand runs no git command that writes
            anything and issues no /plugin command itself)

HARD RULES THIS FILE OBEYS (plan, Agent 4)
  1. Contract tests were written FIRST: tools/test_brothermode_cli.py
     existed, and its tests were run failing, before this file's first
     working subcommand.
  2. The store is never rewritten here. No new table, column, or query
     is introduced anywhere in this file: every subcommand either calls
     an internal tool's own command function directly, or reads a
     single already-published fact (VERSION, or bm_project_facts.facts)
     the same way an existing tool already does.
  3. No domain logic is duplicated. The only arithmetic this file adds
     that no internal tool already exposes as a callable is `update`'s
     release-tag comparison (_version_key, _parse_ls_remote_tags,
     _fetch_remote_tags below); everything else `update` prints is
     read straight from tools/bm_project_facts.py's own facts().
  4. Every generated view (CANVAS.md, DELIVERY-PACKET.md, the project
     view page, the recovery worktree) stays a render or a read: this
     file writes none of them directly, it only reaches the internal
     tool that already owns the write.
  5. Skills are expected to call ONLY this boundary from here forward;
     this file is what makes that instruction checkable rather than a
     wish (refutation H4).
  6. tools/bm_*.py and scripts/*.py stay the adapters; this run does not
     remove or hide them (ruling H4: "the 17 console scripts stay as
     internal adapters this run, documented as such; removal is
     post-v3").
  7. No new capability: every subcommand is a Python-level call to code
     that already ships, or, for `update`, a read-only report built
     from facts other tools already compute plus one read-only network
     read (`git ls-remote --tags`, never a write).

WHAT THIS IS NOT
  Not a second store, not a second writer, not a second definition of
  what "recovered" or "up to date" means: every fact this file states
  about the store's contents comes from calling the tool that already
  owns that fact, never from a query written here. Not a place that
  performs an update: cmd_update prints the exact commands
  commands/brotherme-update.md already documents and executes none of
  them, matching this run's "no git commands that write" constraint.

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(name, relpath=None):
    """Load a sibling module by PATH, the identical technique tools/
    bm_lead.py, tools/bm_view.py, tools/bm_controller.py and every
    sibling test file uses for tools/bm_store.py: a hostile sys.path
    cannot shadow the module this way. `relpath`, when given, is a path
    relative to the repository root (used for scripts/doctor.py and
    scripts/install.py, which live outside tools/); by default the
    sibling lives next to this file. __file__ resolves correctly inside
    the loaded module regardless of how it got here, which is what lets
    scripts/doctor.py's own `root = dirname(dirname(__file__))` line
    keep pointing at the real repository root when this file loads it.

    Loaded LAZILY, once per call, by every subcommand below: an
    interactive command must not pay for, or be exposed to, an adapter
    it will never reach (the same reasoning tools/bm_controller.py's
    own lazy _load documents for tools/bm_autosave.py)."""
    path = (os.path.join(REPO, relpath) if relpath
            else os.path.join(HERE, name + ".py"))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _out(msg=""):
    sys.stdout.write("%s\n" % msg)


def _err(msg):
    sys.stderr.write("%s\n" % msg)


def _print_json(obj):
    _out(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _parse(argv, known, wants_value=()):
    """Flags into (positional, kv), refusing anything unrecognized.
    Copied from tools/bm_lead.py's own _parse (itself copied from tools/
    bm_project.py's): a sibling CLI is not a library this file should
    depend on for its own argument handling, and this file's own flag
    surface (--json, --remote) is small enough that copying the shape
    costs four lines and avoids a fourth place this pattern could
    drift from."""
    positional, kv, i = [], {}, 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--help", "-h") and "help" not in known:
            _out("recognized flags: %s"
                 % ", ".join("--" + k for k in sorted(known)))
            if wants_value:
                _out("flags taking a value: %s"
                     % ", ".join("--" + k for k in sorted(wants_value)))
            sys.exit(0)
        if tok.startswith("--"):
            name = tok[2:]
            if name not in known:
                _err("brothermode: unrecognized flag --%s (recognized: %s)"
                     % (name, ", ".join("--" + k for k in sorted(known))))
                sys.exit(2)
            if name in wants_value:
                if i + 1 >= len(argv):
                    _err("brothermode: --%s needs a value" % name)
                    sys.exit(2)
                kv[name] = argv[i + 1]
                i += 2
                continue
            kv[name] = True
            i += 1
            continue
        positional.append(tok)
        i += 1
    return positional, kv


# ---------------------------------------------------------------------------
# start, status, next, review, deliver, view: pure pass-through. Each
# subcommand loads its adapter and calls the adapter's OWN main(argv)
# with the SAME argv this subcommand received, so the returned int and
# the printed bytes are the adapter's, not a copy of them.
# ---------------------------------------------------------------------------

def cmd_start(argv):
    """tools/bm_project.py start, unchanged."""
    return _load("bm_project").main(["start"] + list(argv))


def cmd_status(argv):
    """tools/bm_lead.py status, unchanged. This is the load-bearing verb
    of the whole boundary: tools/test_brothermode_cli.py runs THIS
    subcommand and `python3 tools/bm_lead.py status` side by side as two
    real subprocesses against the same real, read-only project store and
    asserts their stdout is byte-identical. That is the actual proof
    "this CLI is a thin dispatch" rests on, not a structural argument."""
    return _load("bm_lead").main(["status"] + list(argv))


def cmd_next(argv):
    """tools/bm_project.py next, unchanged."""
    return _load("bm_project").main(["next"] + list(argv))


def cmd_review(argv):
    """tools/bm_project.py review, unchanged."""
    return _load("bm_project").main(["review"] + list(argv))


def cmd_deliver(argv):
    """tools/bm_project.py deliver, unchanged."""
    return _load("bm_project").main(["deliver"] + list(argv))


def cmd_view(argv):
    """tools/bm_view.py render, unchanged: the generated project-view
    page. Per freeze answer 3, a render is never an authority, and this
    subcommand changes nothing about that: it is the exact write tools/
    bm_view.py's own `render` command already performs, reached through
    one more layer of dispatch, never a second writer of the page."""
    return _load("bm_view").main(["render"] + list(argv))


def cmd_continue(argv):
    """tools/bm_continue.py's continue, unchanged: the handoff packet
    generated from store rows, and the launch of the next session. Phase
    C of the 2026-08-08 finalization plan, which exists because a session
    that closes without handing over leaves the founder to restart the
    program by hand. Dispatch only, like every verb above it: the packet
    sections, the launch argv and the honesty about what a spawned
    process id does and does not prove all live in that adapter."""
    return _load("bm_continue").main(["continue"] + list(argv))


def cmd_doctor(argv):
    """scripts/doctor.py, unmodified: eleven install-health checks, each
    PASS, FAIL or SKIP, read-only. doctor.py resolves its OWN root from
    its own file location (the installed BrotherMode copy), not from
    cwd or BROTHERMODE_ROOT, so this subcommand reports on the
    installation this CLI itself ships inside, exactly as `python3
    scripts/doctor.py` already does when run directly."""
    return _load("doctor", os.path.join("scripts", "doctor.py")).main(
        list(argv))


# ---------------------------------------------------------------------------
# recover
# ---------------------------------------------------------------------------

RECOVERY_HONESTY_LINE = (
    "BrotherMode recovery reaches compact-boundary autosave snapshots "
    "only (V3-FREEZE-2026-08-07.md, Refutation adjudication ruling B1): "
    "a kill -9, a crashed terminal, an OOM, or Ctrl-C before the next "
    "compaction leaves nothing here to check out. What follows is "
    "exactly what tools/bm_autosave.py's own recovery path finds; "
    "nothing about the work since the last compaction is implied "
    "either way.")


def cmd_recover(argv):
    """tools/bm_autosave.py's real recovery read path (its cmd_recover
    function, not its main(), which always calls sys.exit(0) and would
    end this process rather than returning to this dispatch), plus ONE
    line this file prints first and bm_autosave.py does not: ruling B1
    requires recovery to be described, out loud, as compact-boundary
    autosave snapshots, not as "whatever you just lost". Printing the
    line UNCONDITIONALLY, before delegating, rather than only when a
    snapshot turns out to be missing, means this file never has to
    infer bm_autosave's outcome by matching its printed sentences: the
    honesty holds in both the found and the not-found case, and no
    string coupling to bm_autosave.py's own wording is needed to prove
    it. bm_autosave.py itself is unmodified, and the fix H1 targets
    (cross-worktree fence-claim resolution) is a different mechanism
    entirely; see tools/test_brothermode_cli.py's H1 test."""
    _out(RECOVERY_HONESTY_LINE)
    _out("")
    autosave = _load("bm_autosave")
    autosave.cmd_recover(list(argv))
    return 0


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------

def cmd_version(argv):
    """The VERSION file's content, read through scripts/install.py's own
    read_version function (the exact reader install.py itself uses to
    report what it just installed), never a second parser of the same
    one-line file."""
    _pos, kv = _parse(argv, ("json",))
    installer = _load("install", os.path.join("scripts", "install.py"))
    version = installer.read_version(REPO)
    if kv.get("json"):
        _print_json({"version": version})
        return 0
    _out(version)
    return 0


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

def _version_key(tag):
    """Sort key for a release tag shaped like 'vX.Y.Z' or 'vX.Y.Z-rc.N':
    (major, minor, patch, 0 if a prerelease else 1, prerelease number).
    A final release always sorts after every prerelease sharing its
    numeric core, which is standard semver precedence. This project's
    own tags (see tools/bm_project_facts.py's PUBLIC_INSTALL_TAG and its
    own history, "v2.0.0-rc.7" through "v2.1.1") are exactly this shape
    and nothing more exotic, so this stays a plain parser rather than a
    full semver engine: a full engine would be new domain logic this
    CLI has no reason to own, and no internal tool exposes one today."""
    body = tag[1:] if tag.startswith("v") else tag
    core, _sep, pre = body.partition("-")
    parts = (core.split(".") + ["0", "0", "0"])[:3]
    nums = tuple(int(p) if p.isdigit() else 0 for p in parts)
    if pre:
        m = re.search(r"(\d+)\s*$", pre)
        return nums + (0, int(m.group(1)) if m else 0)
    return nums + (1, 0)


def _parse_ls_remote_tags(text):
    """{tag_name: sha} from `git ls-remote --tags` output. A peeled
    annotated-tag ref (the '^{}' suffix git prints alongside an
    annotated tag) is folded into the plain tag name it annotates,
    since this project's own tags are never peeled twice under one
    name; whichever line is read last for a given name wins, which is
    harmless here because both lines name the identical release."""
    tags = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        sha, ref = line.split("\t", 1)
        if not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            name = name[:-3]
        tags[name] = sha.strip()
    return tags


def _fetch_remote_tags(remote_url):
    """(tags_dict_or_None, error_text_or_None). Never raises: a network
    refusal is exactly the case commands/brotherme-update.md's own step
    2 documents ("If the network refuses, say plainly the check could
    not run, and do not guess a version"), expressed here as a return
    value so the caller decides how to say it rather than this function
    printing anything itself. Read-only: `git ls-remote` never writes
    to any repository, local or remote."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", remote_url],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)
    if result.returncode != 0:
        return None, (result.stderr or "git ls-remote exited %d"
                      % result.returncode).strip()
    return _parse_ls_remote_tags(result.stdout), None


_UPDATE_FLAGS = ("remote", "json")


def cmd_update(argv):
    """A read-only update report: the installed version, the newest
    published release tag, whether they match, and the exact next steps
    for the caller's own install path, which is exactly what commands/
    brotherme-update.md's steps 1 through 6 already ask a reader to work
    out by hand. This subcommand automates the READING (tools/
    bm_project_facts.py's facts() for every fact but the newest tag, one
    `git ls-remote --tags` call for that), never the ACTING: no
    checkout runs, no /plugin command runs, and no git command that
    writes anything runs from here, matching this run's own "no git
    commands that write" constraint and the plan's "no new
    capabilities" rule. --remote exists for testability only (a
    throwaway local repository can stand in for github.com without a
    network call), the same reason scripts/doctor.py takes --settings
    and tools/bm_autosave.py's cmd_recover takes an optional repo
    argument."""
    _pos, kv = _parse(argv, _UPDATE_FLAGS, wants_value=("remote",))
    facts_mod = _load("bm_project_facts")
    facts = facts_mod.facts(REPO)
    remote = kv.get("remote") or facts["repo_url"]
    tags, error = _fetch_remote_tags(remote)
    installed = facts["version"]
    release_tag = facts["release_tag"]
    if tags is None:
        _err("brothermode: the update check could not run against %s "
             "(%s). Try again once you have a connection; no version "
             "was guessed." % (remote, error))
        return 1
    if not tags:
        _err("brothermode: %s answered with no tags at all, so the "
             "newest release could not be determined. No version was "
             "guessed." % remote)
        return 1
    newest = max(tags, key=_version_key)

    if release_tag is None or release_tag not in tags:
        # Doc step 5: a development copy, or an installed identity that
        # names no released tag at all. Never a downgrade wearing the
        # word "update": STOP here, name nothing to run.
        if kv.get("json"):
            _print_json({"installed_version": installed, "newest_tag": newest,
                        "release_tag": release_tag, "remote": remote,
                        "status": "development-copy"})
            return 0
        _out("Installed version: %s" % installed)
        _out("You are running a development copy: no released version "
             "is newer than what you have. Nothing to update.")
        return 0

    if release_tag == newest:
        if kv.get("json"):
            _print_json({"installed_version": installed, "newest_tag": newest,
                        "release_tag": release_tag, "remote": remote,
                        "status": "up-to-date"})
            return 0
        _out("Installed version: %s" % installed)
        _out("Newest release: %s" % newest)
        _out("You already have the newest version. No update needed.")
        return 0

    if kv.get("json"):
        _print_json({"installed_version": installed, "newest_tag": newest,
                    "release_tag": release_tag, "remote": remote,
                    "status": "update-available"})
        return 0
    _out("Installed version: %s" % installed)
    _out("Newest release: %s" % newest)
    _out("An update is available. Exact steps for your install path:")
    _out("")
    _out("Plugin install:")
    _out("  /plugin marketplace update brothermode-marketplace")
    _out("  /plugin update brothermode")
    _out("  then restart Claude Code")
    _out("")
    _out("Pinned clone (inside the skill folder):")
    _out("  git fetch --tags")
    _out("  git checkout %s" % newest)
    _out("  python3 scripts/doctor.py")
    _out("")
    _out("Updating never touches your project data or records; it only "
         "replaces the BrotherMode files themselves.")
    return 0


# ---------------------------------------------------------------------------
# Dispatch. COMMANDS is read in exactly one function, main.
# ---------------------------------------------------------------------------

COMMANDS = {
    "start": cmd_start,
    "status": cmd_status,
    "next": cmd_next,
    "review": cmd_review,
    "deliver": cmd_deliver,
    "view": cmd_view,
    "continue": cmd_continue,
    "doctor": cmd_doctor,
    "recover": cmd_recover,
    "version": cmd_version,
    "update": cmd_update,
}

_COMMAND_ONE_LINERS = (
    ("start", "create the project"),
    ("status", "where things stand, in the founder's own eight fields"),
    ("next", "the single recommended next task"),
    ("review", "record evidence and transition one task, in one call"),
    ("deliver", "generate the delivery packet"),
    ("view", "write the page that shows where the project stands"),
    ("continue", "write the handoff packet and start the next session"),
    ("doctor", "eleven install-health checks, PASS, FAIL or SKIP"),
    ("recover", "the git-snapshot recovery path (compact-boundary only)"),
    ("version", "the installed VERSION file's content"),
    ("update", "the installed version against the newest release"),
)


def main(argv):
    argv = list(argv or [])
    if not argv or argv[0] in ("-h", "--help", "help"):
        _out("brothermode: the one deterministic public runtime boundary "
             "(V3-FREEZE-2026-08-07.md answer 4).")
        _out("")
        for name, one_liner in _COMMAND_ONE_LINERS:
            _out("  %-8s %s" % (name, one_liner))
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        _err("brothermode: unknown command %r (known: %s)"
             % (cmd, ", ".join(sorted(COMMANDS))))
        return 2
    # SBE3: the effect-class registry (tools/bm_effects.py) was a control
    # only at gate time, because nothing in production ever imported it.
    # Calling declared() here, on every dispatch, means a command added to
    # COMMANDS above without a matching REGISTRY entry fails loudly with
    # the registry's own UndeclaredCommand message the first time anyone
    # runs it, instead of only when tools/test_bm_effects.py happens to
    # run. This is the one dispatch point every subcommand passes through,
    # so one check here covers all of them.
    effects = _load("bm_effects")
    try:
        effects.declared("brothermode_cli.py", cmd)
    except effects.UndeclaredCommand as exc:
        _err(str(exc))
        return 2
    return COMMANDS[cmd](argv[1:])


def cli():
    """Console-script entry point; a packaging entry point must take no
    arguments, which is why this exists beside main() (mirrors every
    other tools/bm_*.py cli() in this project)."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
