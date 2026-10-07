"""shared_config_check.py: the repository's SHARED config carries none of the
damage a leaked test fixture leaves behind.

WHY. 2026-09-20 16:24 JST: test suites launched from a git hook inherited
GIT_DIR and wrote into the shared config of the estate's repository:
core.bare=true (every worktree stopped: "must be run in a work tree"),
core.hooksPath at a deleted temp directory (every hook stopped, a push went
out ungated) and a fixture identity (a commit was authored t <a@b.c>). The
same identity leak sits in history from 2026-08-30 and nobody saw it for
three weeks. The preventing controls are tmp_sandbox, cut_preflight and the
unset line in the batteries; this is the DETECTIVE one, for the routes they
do not reach (a test run by hand in a shell that exports GIT_DIR).

Reads only. Exit 0 clean, 1 damaged (each finding named, with the command
that reverses it), 2 when the config cannot be read (NO-DATA, never a pass).
"""
import os
import re
import subprocess
import sys

OK, DAMAGED, NODATA = 0, 1, 2
#: Identities that only a test fixture uses. A real operator never has one.
FIXTURE_EMAIL = re.compile(r"^(a@b\.c|[^@]+@example\.(com|org|invalid)|[^@]+@[^@]*\.invalid)$", re.I)


def shared_config_path(root, runner=None):
    runner = runner or subprocess.run
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = runner(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                      cwd=root, capture_output=True, text=True, env=env)
    except OSError as exc:
        return None, "git could not be run: %s" % exc
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None, "not a git repository: %s" % ((proc.stderr or "").strip()[-160:] or root)
    return os.path.join(proc.stdout.strip(), "config"), None


def read_pairs(config_path, runner=None):
    runner = runner or subprocess.run
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = runner(["git", "config", "--file", config_path, "--list"],
                      capture_output=True, text=True, env=env)
    except OSError as exc:
        return None, "git could not be run: %s" % exc
    if proc.returncode != 0:
        return None, "%s unreadable: %s" % (config_path, (proc.stderr or "").strip()[-160:])
    pairs = []
    for line in (proc.stdout or "").splitlines():
        key, _, value = line.partition("=")
        pairs.append((key.strip().lower(), value.strip()))
    return pairs, None


def findings(pairs, config_path):
    """[(what, how to reverse it)]. Pure: the config's pairs in, findings out."""
    fix = "git config --file %s" % config_path
    out = []
    for key, value in pairs:
        if key == "core.bare" and value.lower() == "true":
            out.append(("core.bare=true: every worktree of this repository refuses to work",
                        "%s core.bare false" % fix))
        elif key == "core.hookspath":
            if not os.path.isdir(value):
                out.append(("core.hooksPath points at %s, which does not exist: NO hook runs, "
                            "so every push goes out ungated" % value,
                            "%s --unset core.hooksPath" % fix))
        elif key == "user.email" and FIXTURE_EMAIL.match(value):
            out.append(("user.email=%s is a test fixture's identity: the next commit is "
                        "authored by it" % value,
                        "%s --unset user.email  (and user.name)" % fix))
    return out


def main(argv=None, runner=None):
    root = (argv or sys.argv[1:] or [os.getcwd()])[0]
    path, problem = shared_config_path(root, runner)
    if path is None:
        print("NO-DATA: %s" % problem)
        return NODATA
    pairs, problem = read_pairs(path, runner)
    if pairs is None:
        print("NO-DATA: %s" % problem)
        return NODATA
    found = findings(pairs, path)
    for what, how in found:
        print("DAMAGED: %s\n  reverse it with: %s" % (what, how))
    if found:
        return DAMAGED
    print("OK: %s carries no fixture damage (%d setting(s) read)" % (path, len(pairs)))
    return OK


if __name__ == "__main__":
    sys.exit(main())
