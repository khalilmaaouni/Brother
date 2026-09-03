#!/usr/bin/env python3
"""install.sh's own logic, ported: the one line that installs BrotherSBE the
same way on every machine on the team. Checks the machine, installs the
Claude Code plugin, applies the committed team profile through bin/sbe init,
and closes with bin/sbe doctor's own verdict, never a claim this script
invented itself.

install.sh itself is now a two line shim (`exec python3 tools/install.py
"$@"`) so every documented invocation (`sh install.sh`, `sh install.sh
--dry-run`, `sh install.sh --target <path>`, `sh install.sh
--developer-self-test`) keeps working unchanged. This module carries the real
behaviour; the flags, refusal text, and exit codes below are load bearing and
are proven against by tools/test_sbe_install.py (which drives the shim) and
tools/test_install.py (which drives this module directly).

Flags:
  (none)                 resolves the target project (the directory this was
                          invoked from, or --target below), writes the plugin
                          registration and the sbe local footprint into THAT
                          project, and nothing else
  --dry-run               names every step it would take, including the
                          resolved target and any refusal that target would
                          hit, and writes nothing
  --target <path>         installs into <path> instead of the invoking
                          directory; a relative <path> resolves against the
                          invoking directory, not against this repository
  --developer-self-test   the one way to point the resolved target at this
                          BrotherSBE clone itself; every other invocation
                          refuses that target outright

THE RESOLVED TARGET IS NEVER THIS CLONE UNLESS --developer-self-test SAYS SO
ON PURPOSE (see REFUSED below). Both sides of that comparison are resolved
through a shell `cd ... && pwd`, so a symlink or a trailing slash cannot hide
the match.

SBE_INSTALL_REQUIRE=<name> adds one synthetic requirement ahead of the real
ones, so the missing-prerequisite path can be exercised in a test without
uninstalling a real tool from the machine running it.
"""
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _shell_resolve(path):
    """The same resolution install.sh always used (`cd "$path" && pwd`, via
    the real shell), never Python's own path-resolution: on macOS a shell
    `cd` into a tmp path and a Python `os.path.realpath` disagree (the shell
    leaves /var alone, realpath resolves it through /private/var), and the
    self-install refusal below depends on comparing two paths resolved the
    SAME way."""
    out = subprocess.run(["sh", "-c", 'cd "$1" && pwd', "sh", path],
                          capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _parse_args(argv):
    dry_run = False
    developer_self_test = False
    target_arg = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--dry-run":
            dry_run = True
            i += 1
        elif arg == "--developer-self-test":
            developer_self_test = True
            i += 1
        elif arg == "--target":
            if i + 1 >= len(argv):
                print("install: --target requires a path argument")
                sys.exit(2)
            target_arg = argv[i + 1]
            i += 2
        elif arg.startswith("--target="):
            target_arg = arg[len("--target="):]
            i += 1
        else:
            i += 1
    return dry_run, developer_self_test, target_arg


def _need(name, remedy):
    """Refuses with the exact remedy a person can act on, never a bare "not
    found". Read only (shutil.which), safe in both --dry-run and a real
    install."""
    if shutil.which(name) is None:
        print("install: MISSING %s: %s" % (name, remedy))
        sys.exit(1)


def _run_or_exit(argv, cwd=None):
    """A subprocess call whose failure ends the whole install with its own
    exit code, the same as install.sh running under `set -e`. Output is
    never captured here: it is meant to reach the real terminal."""
    result = subprocess.run(argv, cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def check_prereqs(dry_run):
    require = os.environ.get("SBE_INSTALL_REQUIRE", "")
    if require:
        if dry_run:
            print("would: check %s is on PATH (SBE_INSTALL_REQUIRE, a "
                  "synthetic requirement added for testing the refusal path "
                  "only)" % require)
        _need(require, "install %s and re-run install.sh" % require)

    if dry_run:
        print("would: check git is on PATH")
    _need("git", "install git (for example, Xcode Command Line Tools on "
                 "macOS, or your package manager) and re-run install.sh")

    if dry_run:
        print("would: check python3 is on PATH and is version 3.9 or newer")
    _need("python3", "install Python 3.9 or newer and re-run install.sh")
    version_check = subprocess.run(
        ["python3", "-c",
         "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"])
    if version_check.returncode != 0:
        print("install: MISSING python3: found a Python older than 3.9; "
              "install Python 3.9 or newer and re-run install.sh")
        sys.exit(1)

    if dry_run:
        print("would: check claude is on PATH (the Claude Code CLI)")
    _need("claude", "install the Claude Code CLI and re-run install.sh")


def install_plugin(dry_run, script_dir):
    """Registers this project as a Claude Code plugin. Prefers `claude
    plugin marketplace add` pointed straight at the repository once a
    published, citable tag exists there; otherwise takes the clone fallback
    (~/.claude/skills/brothersbe), so a machine with no published tag yet
    still ends with a working local marketplace source. Both branches finish
    with the same `claude plugin install` call."""
    origin = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                             cwd=script_dir, capture_output=True, text=True)
    origin_url = origin.stdout.strip() if origin.returncode == 0 else ""
    with io.open(os.path.join(script_dir, ".claude-plugin", "plugin.json"),
                 encoding="utf-8") as fh:
        version = json.load(fh)["version"]
    tag = "v%s" % version
    clone_dest = os.path.join(os.environ["HOME"], ".claude", "skills", "brothersbe")

    if dry_run:
        print("would: install the brothersbe plugin: claude plugin "
              "marketplace add %s then claude plugin install "
              "brothersbe@brothersbe, if tag %s is published on %s; "
              "otherwise take the clone fallback (git clone %s %s, or "
              "update it if it is already there, then claude plugin "
              "marketplace add %s, then claude plugin install "
              "brothersbe@brothersbe)"
              % (origin_url, tag, origin_url, origin_url, clone_dest, clone_dest))
        return

    if not origin_url:
        print("install: MISSING origin remote: this clone has no git remote "
              "named origin; add one (git remote add origin <repository-url>) "
              "and re-run install.sh")
        sys.exit(1)

    # A pipeline's `set -e` only watches the LAST command, so a network
    # failure here (ls-remote itself failing) is not fatal: it just produces
    # no match and the clone fallback below is taken, same as install.sh.
    ls_remote = subprocess.run(
        ["git", "ls-remote", "--tags", origin_url, "refs/tags/%s" % tag],
        cwd=script_dir, capture_output=True, text=True)
    published = tag in ls_remote.stdout

    if published:
        print("install: %s is published on %s, adding the marketplace directly"
              % (tag, origin_url))
        source = origin_url
    else:
        print("install: %s is not published on %s yet, taking the clone fallback"
              % (tag, origin_url))
        if os.path.isdir(os.path.join(clone_dest, ".git")):
            print("install: updating the existing clone at %s" % clone_dest)
            _run_or_exit(["git", "-C", clone_dest, "pull", "--ff-only"])
        else:
            print("install: cloning %s to %s" % (origin_url, clone_dest))
            os.makedirs(os.path.dirname(clone_dest), exist_ok=True)
            _run_or_exit(["git", "clone", origin_url, clone_dest], cwd=script_dir)
        source = clone_dest

    _run_or_exit(["claude", "plugin", "marketplace", "add", source], cwd=script_dir)
    _run_or_exit(["claude", "plugin", "install", "brothersbe@brothersbe"], cwd=script_dir)


def _names(values):
    return ", ".join(values) if values else "none"


def _print_team_profile_report(data):
    """The installation report: what the team profile requested, what was
    actually applied, what was rejected by name, and which files were
    written versus already up to date. A direct translation of install.sh's
    embedded PY_TEAM_PROFILE_REPORT, now a plain function instead of a
    string handed to a second python3 process."""
    profile = data.get("teamProfile") or {}
    requested = profile.get("requested", [])
    applied = profile.get("applied", {})
    rejected = profile.get("rejected", [])
    source = profile.get("source")
    path = profile.get("path")

    print("install: team profile requested: %s" % _names(requested))
    if path:
        print("install: team profile source: %s (%s)" % (path, source))
    else:
        print("install: team profile source: none found; built-in defaults used")
    applied_pairs = ["%s=%s" % (key, applied[key]) for key in sorted(applied)]
    print("install: team profile applied: %s" % _names(applied_pairs))
    print("install: team profile rejected by name: %s" % _names(rejected))
    print("install: files written: %s" % _names(data.get("written", [])))
    print("install: files already up to date, skipped: %s"
          % _names(data.get("skipped", [])))
    for warning in data.get("warnings", []):
        print("install: WARNING %s" % warning)


def apply_team_profile(dry_run, target, script_dir):
    """bin/sbe init writes the local footprint (config, dossier directory,
    receipt) into the RESOLVED TARGET, never into this clone.
    .sbe/team-profile.json is the committed, same-for-everyone answer to the
    choices that command would otherwise ask a person to make (dossierRoot,
    vaultPathPattern, ci, codeGuideDepth, schemaVersion); the report names
    every one of those fields as requested, applied or rejected, so a field
    the profile named and init silently ignored is never silent."""
    if dry_run:
        print("would: apply the team profile with python3 bin/sbe init "
              "%s --apply, reading .sbe/team-profile.json (from %s when it "
              "carries one, otherwise this installation's own copy at %s) "
              "for dossierRoot, vaultPathPattern, ci, codeGuideDepth, and "
              "schemaVersion; any field outside that set is rejected by "
              "name in the report below, never silently ignored"
              % (target, target, script_dir))
        return
    result = subprocess.run(
        ["python3", os.path.join(script_dir, "bin", "sbe"), "init", target,
         "--apply", "--json"],
        cwd=script_dir, capture_output=True, text=True)
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        sys.exit(result.returncode)
    data = json.loads(result.stdout)
    _print_team_profile_report(data)


def run_doctor(dry_run, target, script_dir):
    """Closes the install with bin/sbe doctor's own verdict, graded against
    the RESOLVED TARGET, never this clone: doctor is run with its cwd set to
    $TARGET (its git and identity checks read the calling process's cwd),
    while the doctor binary itself is still invoked by its absolute path
    under $SCRIPT_DIR, so its own tool-presence check keeps resolving
    against this installation's own tools/ regardless of which directory
    doctor is graded against."""
    if dry_run:
        print("would: run bin/sbe doctor and confirm it agrees before "
              "printing the PASS line")
        return
    result = subprocess.run([os.path.join(script_dir, "bin", "sbe"), "doctor"],
                             cwd=target)
    if result.returncode == 0:
        print("install: PASS, sbe doctor agrees (graded %s)" % target)
    else:
        print("install: sbe doctor did not agree (graded %s); read what it "
              "printed above for exactly what is missing" % target)
        sys.exit(1)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry_run, developer_self_test, target_arg = _parse_args(argv)

    invoked_from = os.getcwd()
    raw_target = target_arg if target_arg else invoked_from

    if not os.path.isdir(raw_target):
        print("install: MISSING target: '%s' is not a directory; pass an "
              "existing project directory with --target, or run install.sh "
              "from inside one" % raw_target)
        return 2
    target = _shell_resolve(raw_target)

    print("install: resolved target: %s" % target)

    script_dir = _shell_resolve(ROOT)
    if target == script_dir and not developer_self_test:
        print(
            "install: REFUSED: the resolved target (%s) is this BrotherSBE "
            "clone itself, not a project that consumes it. Installing here "
            "would silently rewrite this clone's own working tree instead of "
            "your project's, which is the exact defect this refusal exists "
            "to close. Do one of: run install.sh from inside your own "
            "project's directory instead of this clone, or re-run with "
            "--target /path/to/your-project; if you mean to test install.sh "
            "against this clone on purpose, re-run with "
            "--developer-self-test." % target)
        return 1

    check_prereqs(dry_run)
    install_plugin(dry_run, script_dir)
    apply_team_profile(dry_run, target, script_dir)
    run_doctor(dry_run, target, script_dir)

    if dry_run:
        print("install: dry run, nothing written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
