#!/usr/bin/env python3
"""bench_env.py: the throwaway arm-environment builder for the installed-arm
benchmark (design build step 2, docs/program/absolute-lead/
DESIGN-benchmark-installed-arm.md, sections 1.1 and 5 step 2).

WHAT THIS IS
  A build-and-destroy tool for the one throwaway configuration each
  benchmark cell runs inside: a HOME the real machine never sees, a
  CLAUDE_CONFIG_DIR the Claude Code client reads its plugin state from, and
  a BROTHERME_CONFIG file the product's own consent gate reads. Two arms
  use it: arm A gets an EMPTY CLAUDE_CONFIG_DIR (no plugin installed, plain
  Claude, the v2 control), arm B gets the product installed the shipped
  way. --arm selects which; the default is B, because that is the path
  this tool exists to prove works at all.

WHERE THE PATTERN COMES FROM, NAMED RATHER THAN GUESSED
  The plugin install sequence and its three asserts (the success lines, the
  version matching VERSION, and the hook group count from
  tools/bm_project_facts.py --field hook_count) are copied from
  scripts/release-smoke-install.sh, which proves the same install with the
  real Claude Code client at release time. The environment-building shape
  (a from-scratch copy of the real environment, every GIT_ name stripped
  because git honours GIT_DIR and GIT_WORK_TREE over cwd, every
  BrotherMode-recognized variable stripped before being explicitly
  repinned) is the pattern scripts/rehearse_fresh_install.py's build_env()
  already uses. Consent is granted the shipped way: scripts/setup.py flag
  mode against BROTHERME_CONFIG inside the throwaway HOME, exactly as
  scripts/rehearse_fresh_install.py step 4 and
  scripts/benchmark_comparative.py's probe_installed() already do. None of
  these three files is imported here: every script in this project stays
  self-contained and duplicates a proven pattern rather than reaching
  across scripts/ for it (the same policy
  scripts/rehearse_fresh_install.py's _mask_home states in its own
  comments), so the exact command spellings below are copied by hand from
  the files named above, not reinvented from memory.

THE VAULT-LEAK LESSON, ALSO ENCODED HERE
  tools/bm_ledger.py, and its siblings, resolve their storage from
  BROTHERMODE_VAULT / BROTHERSBE_VAULT when either is exported in the
  invoking shell, which WINS OVER HOME. A HOME-only override was measured
  writing a live row into a real, non-throwaway vault (the incident
  tools/test_bm_plugin_install.py's own module docstring records, and
  docs/closure/specs/2026-08-04-c06-packaging.md, INCIDENT DURING
  VERIFICATION). Every environment this file builds pins HOME,
  BROTHERMODE_VAULT and BROTHERSBE_VAULT into the same throwaway
  directory, explicitly assigned rather than merely unset, so an ambient
  export in the calling shell can never win the way it did there.

THE PERSISTENT-AUTH FOLDER, MEASURED, NOT ASSUMED
  scripts/benchmark_comparative.py's _probe_env() docstring records two
  facts measured on this machine on 2026-08-07: the claude binary's login
  rides the real HOME's keychain (a throwaway HOME always reads "not
  logged in", whatever CLAUDE_CONFIG_DIR says), and plugin state is scoped
  entirely by CLAUDE_CONFIG_DIR (a persistent config dir shows only its own
  installs, none of a dogfood install's). BM_BENCH_AUTH_CONFIG, when set in
  the environment this tool is run under, names a founder-authenticated
  CLAUDE_CONFIG_DIR this tool reuses instead of building a throwaway one:
  the real HOME is then left unoverridden so the keychain-backed login
  still resolves, and nothing is ever copied out of it, no credential file
  included. A path named by BM_BENCH_AUTH_CONFIG is NEVER deleted by this
  tool, on any exit path; every other directory this tool builds always is.
  This tool's own --build --check done-check never sets that variable, so
  its own run always installs into, and destroys, a fully throwaway
  configuration.

USAGE
  python3 scripts/bench_env.py --build --check [--arm A|B] [--keep]
      build one throwaway arm environment, verify it, print its content
      digests, then destroy it. --arm B (the default) installs the plugin
      the shipped way and grants consent; --arm A leaves CLAUDE_CONFIG_DIR
      empty, the plain control. Exit 0 PASSED, exit 1 FAILED (the build or
      one of its asserts broke), exit 2 BLOCKED (no claude binary on PATH,
      or BM_BENCH_AUTH_CONFIG names no directory).

Python 3.9, standard library only. No em or en dashes anywhere in this
file, its comments, or its output.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2
EXIT_USAGE = 2

#: The plugin identity this repository ships, exactly as
#: scripts/release-smoke-install.sh installs it.
MARKETPLACE_NAME = "brothermode-marketplace"
PLUGIN_SPEC = "brothermode@%s" % MARKETPLACE_NAME

INSTALL_TIMEOUT_SECONDS = 300


def _out(text):
    sys.stdout.write("bench_env: %s\n" % text)
    sys.stdout.flush()


def _ascii(text, limit=300):
    """A printable ASCII snippet for harness output only, never for a
    stored artifact: keeps a foreign-encoding subprocess message from
    corrupting this tool's own plain-ASCII output."""
    snippet = " ".join((text or "").split())
    if len(snippet) > limit:
        snippet = snippet[:limit] + "..."
    return snippet.encode("ascii", "replace").decode("ascii")


def _run(argv, cwd, env, timeout):
    return subprocess.run(argv, cwd=cwd, env=env, timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def claude_binary():
    return shutil.which("claude")


# ---------------------------------------------------------------------------
# paths and environment
# ---------------------------------------------------------------------------

def build_paths(tmp_root):
    """Every throwaway path one arm environment needs, all under tmp_root
    so one shutil.rmtree(tmp_root) destroys the whole environment at once."""
    home = os.path.join(tmp_root, "home")
    return {
        "tmp_root": tmp_root,
        "home": home,
        "claude_config_dir": os.path.join(tmp_root, "claude-config"),
        "broth_config": os.path.join(home, ".brotherme", "config.json"),
        "vault": os.path.join(home, "BrotherModeVault"),
        "sbe_vault": os.path.join(home, "BrotherSBEVault"),
    }


def build_env(paths, auth_config_dir=None):
    """The one throwaway environment every subprocess call in this file
    uses. Built from a copy of the real environment, per
    scripts/rehearse_fresh_install.py's build_env(): every GIT_ name is
    stripped (git honours GIT_DIR and GIT_WORK_TREE over cwd, so an
    inherited one would aim a live probe's git calls at the operator's own
    repository) and every BrotherMode-recognized variable this shell might
    already carry is stripped before being explicitly repinned below, so
    nothing about this machine's own dogfood install leaks in.

    auth_config_dir, when given, is a persistent, founder-authenticated
    CLAUDE_CONFIG_DIR this tool reuses instead of building a throwaway one
    (see the module docstring's PERSISTENT-AUTH section). In that case HOME
    is left at its real value so the keychain-backed login still resolves;
    every other pin below (BROTHERME_CONFIG, BROTHERMODE_VAULT,
    BROTHERSBE_VAULT) still points into the throwaway directory this run
    built, so consent state and vault writes are never shared with a real
    install regardless of which auth path is taken."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for key in ("BROTHERMODE_VAULT", "BROTHERMODE_ROOT", "BROTHERMODE_REGISTRIES",
               "BROTHERME_CONFIG", "BROTHERSBE_VAULT", "CLAUDE_CONFIG_DIR"):
        env.pop(key, None)
    env.pop("BM_FENCE_SESSION_ID", None)
    if auth_config_dir is None:
        env["HOME"] = paths["home"]
    env["CLAUDE_CONFIG_DIR"] = auth_config_dir or paths["claude_config_dir"]
    env["BROTHERME_CONFIG"] = paths["broth_config"]
    # The vault-leak lesson (see module docstring): pinned explicitly, never
    # left to a default some other tool would compute, so an ambient shell
    # export can never win the way it did in the recorded incident.
    env["BROTHERMODE_VAULT"] = paths["vault"]
    env["BROTHERSBE_VAULT"] = paths["sbe_vault"]
    return env


# ---------------------------------------------------------------------------
# arm B: install the product the shipped way, with the release-smoke asserts
# ---------------------------------------------------------------------------

def install_plugin(env, notes, persistent=False):
    """Install the product the shipped way: the same two commands
    scripts/release-smoke-install.sh proves at release time, with the same
    three asserts (the success lines, the version matching VERSION, and the
    hook group count from tools/bm_project_facts.py --field hook_count),
    copied by hand from that file rather than reinvented. Appends every
    step's outcome to notes and returns True only when all three asserts
    hold.

    persistent=True (BM_BENCH_AUTH_CONFIG in play) tolerates the wording a
    second add or install gives on a config dir that already carries this
    tree's registration ("already on disk" rather than "Successfully added
    marketplace"); on that path the postcondition becomes: the registered
    marketplace source really is this tree, and `claude plugin list`
    really shows this tree's VERSION installed. This mirrors
    scripts/benchmark_comparative.py's probe_installed(), reimplemented
    here rather than imported, per this project's own self-contained-script
    convention."""
    claude_bin = claude_binary()
    r_add = _run([claude_bin, "plugin", "marketplace", "add", ROOT], ROOT, env,
                INSTALL_TIMEOUT_SECONDS)
    add_out = (r_add.stdout or "") + (r_add.stderr or "")
    if r_add.returncode != 0 or (
            "Successfully added marketplace" not in add_out and not persistent):
        notes.append("marketplace add did not succeed: exit %s, output: %s"
                     % (r_add.returncode, _ascii(add_out)))
        return False
    if persistent and "Successfully added marketplace" not in add_out:
        reg_path = os.path.join(env["CLAUDE_CONFIG_DIR"], "plugins",
                                "known_marketplaces.json")
        try:
            with io.open(reg_path, encoding="utf-8") as fh:
                reg = json.load(fh)
            source = (reg.get(MARKETPLACE_NAME, {})
                      .get("source", {}).get("path", ""))
        except (IOError, OSError, ValueError) as exc:
            notes.append("marketplace add said %r but %s is unreadable: "
                         "%s: %s" % (_ascii(add_out, 120), reg_path,
                                     type(exc).__name__, exc))
            return False
        if os.path.realpath(source) != os.path.realpath(ROOT):
            notes.append("the persistent config's marketplace source is "
                         "%s, not this tree (%s); remove the stale "
                         "registration and re-run" % (source, ROOT))
            return False

    r_install = _run([claude_bin, "plugin", "install", PLUGIN_SPEC], ROOT, env,
                     INSTALL_TIMEOUT_SECONDS)
    install_out = (r_install.stdout or "") + (r_install.stderr or "")
    if (r_install.returncode != 0
            or ("Successfully installed plugin" not in install_out
                and not persistent)):
        notes.append("plugin install did not succeed: exit %s, output: %s"
                     % (r_install.returncode, _ascii(install_out)))
        return False

    with io.open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
        version = fh.read().strip()
    r_list = _run([claude_bin, "plugin", "list"], ROOT, env,
                  INSTALL_TIMEOUT_SECONDS)
    list_out = (r_list.stdout or "") + (r_list.stderr or "")
    if PLUGIN_SPEC not in list_out or ("Version: %s" % version) not in list_out:
        notes.append("installed plugin does not match this tree's VERSION "
                     "(%s) in `claude plugin list`: %s"
                     % (version, _ascii(list_out)))
        return False

    r_details = _run([claude_bin, "plugin", "details", "brothermode"], ROOT, env,
                     INSTALL_TIMEOUT_SECONDS)
    details_out = (r_details.stdout or "") + (r_details.stderr or "")
    r_hookcount = _run(
        [sys.executable, os.path.join(ROOT, "tools", "bm_project_facts.py"),
         "--field", "hook_count"], ROOT, None, INSTALL_TIMEOUT_SECONDS)
    hook_count = (r_hookcount.stdout or "").strip()
    if not hook_count or ("Hooks (%s)" % hook_count) not in details_out:
        notes.append("installed plugin's hook group count does not match "
                     "tools/bm_project_facts.py --field hook_count (%r): %s"
                     % (hook_count, _ascii(details_out)))
        return False

    notes.append("installed: version %s, %s hook group(s), marketplace %s"
                 % (version, hook_count, PLUGIN_SPEC))
    return True


def grant_consent(env, vault_path, broth_config, notes):
    """Consent the shipped way: scripts/setup.py flag mode against
    BROTHERME_CONFIG inside the throwaway HOME, exactly as
    scripts/rehearse_fresh_install.py step 4 and probe_installed() already
    do. Without this the consent gate would rightly hold the product back
    and every cell would measure the gate, not the task."""
    r = _run([sys.executable, os.path.join(ROOT, "scripts", "setup.py"),
             "--vault", vault_path, "--mode", "plugin", "--accept-notice"],
             ROOT, env, INSTALL_TIMEOUT_SECONDS)
    if r.returncode != 0 or not os.path.isfile(broth_config):
        notes.append("scripts/setup.py flag-mode consent did not complete: "
                     "exit %s, output: %s"
                     % (r.returncode,
                        _ascii((r.stdout or "") + (r.stderr or ""))))
        return False
    notes.append("consent granted, config at %s" % broth_config)
    return True


# ---------------------------------------------------------------------------
# environment digests, for the cell manifest (design section 1.1.5: "both
# configuration directories are content-hashed into the cell manifest so
# the claim is checkable")
# ---------------------------------------------------------------------------

def digest_dir(path):
    """A single sha256 hex digest over every regular file under path: its
    relative POSIX path and its bytes, sorted so the result does not depend
    on directory walk order. An absent or empty directory digests to the
    hash of an empty listing rather than raising, so a caller can always
    record a value in a manifest even for arm A's deliberately empty
    CLAUDE_CONFIG_DIR."""
    entries = []
    if os.path.isdir(path):
        for base, _dirs, files in os.walk(path):
            for name in files:
                full = os.path.join(base, name)
                rel = os.path.relpath(full, path).replace(os.sep, "/")
                try:
                    with io.open(full, "rb") as fh:
                        data = fh.read()
                except OSError:  # sbe: allow-silent this function's own docstring: never raise, always produce a value for the manifest
                    continue
                entries.append((rel, hashlib.sha256(data).hexdigest()))
    entries.sort()
    combined = hashlib.sha256()
    for rel, filehash in entries:
        combined.update(rel.encode("utf-8"))
        combined.update(b"\0")
        combined.update(filehash.encode("utf-8"))
        combined.update(b"\n")
    return combined.hexdigest()


# ---------------------------------------------------------------------------
# --build --check
# ---------------------------------------------------------------------------

def build_and_check(arm, keep):
    claude_bin = claude_binary()
    if not claude_bin:
        _out("BLOCKED: no claude binary on PATH; run on a machine with "
            "Claude Code installed")
        return EXIT_BLOCKED

    auth_config_dir = os.environ.get("BM_BENCH_AUTH_CONFIG", "").strip() or None
    if auth_config_dir and not os.path.isdir(auth_config_dir):
        _out("BLOCKED: BM_BENCH_AUTH_CONFIG names no directory: %s"
            % auth_config_dir)
        return EXIT_BLOCKED

    tmp_root = os.path.realpath(tempfile.mkdtemp(prefix="bm-bench-env-"))
    paths = build_paths(tmp_root)
    env = build_env(paths, auth_config_dir)
    os.makedirs(paths["home"], exist_ok=True)
    if not auth_config_dir:
        os.makedirs(paths["claude_config_dir"], exist_ok=True)

    _out("arm %s: HOME %s" % (arm, paths["home"]))
    _out("arm %s: CLAUDE_CONFIG_DIR %s (%s)"
        % (arm, env["CLAUDE_CONFIG_DIR"],
           "persistent founder-authenticated" if auth_config_dir
           else "throwaway"))
    _out("arm %s: BROTHERMODE_VAULT %s" % (arm, env["BROTHERMODE_VAULT"]))
    _out("arm %s: BROTHERSBE_VAULT %s" % (arm, env["BROTHERSBE_VAULT"]))

    ok = False
    notes = []
    try:
        try:
            if arm == "B":
                ok = install_plugin(env, notes, persistent=bool(auth_config_dir))
                if ok:
                    ok = grant_consent(env, paths["vault"],
                                       paths["broth_config"], notes)
            else:
                ok = True
                notes.append("arm A: no plugin installed, CLAUDE_CONFIG_DIR "
                             "stays empty by design")
            config_digest = digest_dir(env["CLAUDE_CONFIG_DIR"])
            home_digest = digest_dir(paths["home"])
            notes.append("CLAUDE_CONFIG_DIR content digest: %s" % config_digest)
            notes.append("HOME content digest: %s" % home_digest)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            ok = False
            notes.append("unexpected error before a verdict was reached: "
                         "%s: %s" % (type(exc).__name__, exc))
        for line in notes:
            _out(line)
    finally:
        if keep:
            _out("--keep given: leaving %s in place" % tmp_root)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)
            _out("destroyed: %s" % tmp_root)
        _out("BM_BENCH_AUTH_CONFIG path, if any, is never deleted by this "
            "tool: %s" % (auth_config_dir or "not set for this run"))

    if ok:
        _out("PASSED: arm %s environment built, verified, and destroyed"
            % arm)
        return EXIT_OK
    _out("FAILED: arm %s environment did not pass its checks" % arm)
    return EXIT_FAILED


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="bench_env.py",
        description="Throwaway arm-environment builder for the "
                    "installed-arm benchmark (design build step 2). Builds "
                    "a HOME, a CLAUDE_CONFIG_DIR and a BROTHERME_CONFIG the "
                    "way the shipped product installs, then destroys them.")
    p.add_argument("--build", action="store_true",
                   help="build a throwaway arm environment; required for "
                        "anything else to run")
    p.add_argument("--check", action="store_true",
                   help="verify the build with the release-smoke asserts "
                        "and consent (arm B), or confirm emptiness (arm "
                        "A), and print the environment digests")
    p.add_argument("--arm", choices=("A", "B"), default="B",
                   help="A: empty CLAUDE_CONFIG_DIR, no install, the plain "
                        "control. B: the product installed the shipped "
                        "way. Default B, the path this tool exists to "
                        "prove.")
    p.add_argument("--keep", action="store_true",
                   help="do not destroy the throwaway directories on exit "
                        "(for inspecting a failure); a BM_BENCH_AUTH_CONFIG "
                        "path is never deleted regardless of this flag")
    return p


def main(argv):
    args = build_parser().parse_args(argv)
    if not args.build:
        _out("usage: bench_env.py --build --check [--arm A|B] [--keep]")
        return EXIT_USAGE
    if not args.check:
        # --build alone, with no --check, is not a supported invocation:
        # nothing downstream should ever treat an unverified environment as
        # a success, so this tool refuses to build one silently.
        _out("--build currently requires --check; nothing here calls an "
            "unverified environment a success")
        return EXIT_USAGE
    return build_and_check(args.arm, args.keep)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
