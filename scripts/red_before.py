#!/usr/bin/env python3
"""Prove a new test fails on the old code and passes on the new code.

Exports HEAD twice into temporary directories (git archive, never a second
checkout), puts the --files back to their --base content in the first copy,
and runs the test command in both. The caller's working tree, index and stash
are never touched. Written 2026-09-12 after a night run lost two fixes, one
to `git checkout HEAD --` over uncommitted work and one to a stash that every
worktree of a repository shares. Drafted by Muse Spark 1.2, cross-reviewed by
DeepSeek V4.1 Flash.
"""
import sys
import os
import subprocess
import tempfile
import tarfile
import io
import shutil
import pathlib

TIMEOUT_GIT = 30
TIMEOUT_TEST_DEFAULT = 900

def fail2(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)

def run_git(args, cwd):
    return subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_GIT)

def export_head(dest, cwd):
    proc = subprocess.run(["git", "archive", "HEAD"], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_GIT)
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace")
        raise RuntimeError(f"git archive failed: {err}")
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r") as tf:
            if sys.version_info >= (3, 12):
                tf.extractall(path=dest, filter="data")
            else:
                tf.extractall(path=dest)
    except Exception as e:
        raise RuntimeError(f"tar extract failed: {e}")

def main():
    argv = sys.argv[1:]
    base = None
    files = []
    test_cmd = None
    timeout_test = TIMEOUT_TEST_DEFAULT
    i = 0
    while i < len(argv):
        if argv[i] == "--base":
            if i + 1 >= len(argv):
                fail2("missing value for --base")
            base = argv[i + 1]
            i += 2
        elif argv[i] == "--files":
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                files.append(argv[i])
                i += 1
        elif argv[i] == "--timeout":
            if i + 1 >= len(argv):
                fail2("missing value for --timeout")
            try:
                timeout_test = int(argv[i + 1])
            except ValueError:
                fail2("invalid value for --timeout")
            i += 2
        elif argv[i] == "--":
            test_cmd = argv[i + 1:]
            break
        else:
            print(f"unknown argument {argv[i]}", file=sys.stderr)
            sys.exit(2)
    if base is None or not files or test_cmd is None or len(test_cmd) == 0:
        print("usage: red_before.py --base <ref> --files <paths> -- <cmd>", file=sys.stderr)
        sys.exit(2)

    for path in files:
        if os.path.isabs(path):
            print(f"invalid path {path}", file=sys.stderr)
            sys.exit(2)
        if ".." in pathlib.PurePath(path).parts:
            print(f"invalid path {path}", file=sys.stderr)
            sys.exit(2)

    try:
        toplevel_proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_GIT, text=True)
    except subprocess.TimeoutExpired:
        print("git rev-parse --show-toplevel timed out", file=sys.stderr)
        sys.exit(2)
    except (FileNotFoundError, PermissionError, OSError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    if toplevel_proc.returncode != 0:
        print(toplevel_proc.stderr.strip(), file=sys.stderr)
        sys.exit(2)
    repo_root = toplevel_proc.stdout.strip()
    if not repo_root:
        print("cannot determine repository top level", file=sys.stderr)
        sys.exit(2)

    try:
        ver = run_git(["git", "rev-parse", "--verify", base], cwd=repo_root)
    except subprocess.TimeoutExpired:
        print(f"base not found: {base} (timeout)", file=sys.stderr)
        sys.exit(2)
    except (FileNotFoundError, PermissionError, OSError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(2)
    if ver.returncode != 0:
        print(f"base not found: {base}", file=sys.stderr)
        sys.exit(2)

    red_dir = None
    green_dir = None
    try:
        red_dir = tempfile.mkdtemp(prefix="red_before_red_")
        green_dir = tempfile.mkdtemp(prefix="red_before_green_")

        try:
            export_head(red_dir, repo_root)
        except Exception as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)

        for path in files:
            show = run_git(["git", "show", f"{base}:{path}"], cwd=repo_root)
            target = os.path.join(red_dir, path)
            if show.returncode == 0:
                parent = os.path.dirname(target)
                if parent and not os.path.exists(parent):
                    os.makedirs(parent, exist_ok=True)
                if os.path.lexists(target) and os.path.islink(target):
                    os.remove(target)
                with open(target, "wb") as f:
                    f.write(show.stdout)
            else:
                ls = run_git(["git", "ls-tree", base, "--", path], cwd=repo_root)
                if ls.returncode != 0:
                    print(f"ls-tree failed for {path}", file=sys.stderr)
                    sys.exit(2)
                if ls.stdout.strip() == b"":
                    if os.path.lexists(target):
                        if os.path.islink(target) or os.path.isfile(target):
                            os.remove(target)
                        elif os.path.isdir(target):
                            shutil.rmtree(target)
                else:
                    err = show.stderr.decode(errors="replace")
                    print(f"cannot read file {path} at {base}: {err}", file=sys.stderr)
                    sys.exit(2)

        try:
            export_head(green_dir, repo_root)
        except Exception as e:
            print(str(e), file=sys.stderr)
            sys.exit(2)

        def run_in_dir(cmd, cwd):
            try:
                r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_test, text=True)
                return r.returncode, r.stdout if r.stdout is not None else ""
            except subprocess.TimeoutExpired as e:
                out = ""
                if e.stdout is not None:
                    if isinstance(e.stdout, bytes):
                        out = e.stdout.decode(errors="replace")
                    else:
                        out = str(e.stdout)
                if e.stderr is not None:
                    extra = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else str(e.stderr)
                    out = out + extra
                return 124, out
            except (FileNotFoundError, PermissionError) as e:
                print(str(e), file=sys.stderr)
                sys.exit(2)
            except OSError as e:
                print(str(e), file=sys.stderr)
                sys.exit(2)

        red_exit, red_out = run_in_dir(test_cmd, red_dir)
        green_exit, green_out = run_in_dir(test_cmd, green_dir)

        def last_three(text):
            lines = text.splitlines()
            return lines[-3:] if len(lines) >= 3 else lines

        print("RED run last 3 lines:")
        for line in last_three(red_out):
            print(line)
        print("GREEN run last 3 lines:")
        for line in last_three(green_out):
            print(line)

        proven = (red_exit != 0 and green_exit == 0)
        verdict = "PROVEN" if proven else "NOT PROVEN"
        print(f"red_before: RED {red_exit} GREEN {green_exit} -> {verdict}")

        if proven:
            sys.exit(0)
        else:
            sys.exit(1)

    finally:
        for d in (red_dir, green_dir):
            if d and os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    main()
