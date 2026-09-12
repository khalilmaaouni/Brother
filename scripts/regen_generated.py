"""Regenerate generated files in order and verify they are current.

The tool exists because generated files went stale on the last edit
to a tree, and because a manifest regenerated over a dirty tree
silently described files that were never committed.
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


GENERATED_EXACT = {
    "SYSTEM.md",
    "products/brothermode/CHECKSUMS.sha256",
    "products/brothersbe/CHECKSUMS.sha256",
}
GENERATED_DIRS = ["bundle"]


def is_generated(p):
    q = p.strip().strip('"').strip("'")
    if q.startswith("./"):
        q = q[2:]
    if q in GENERATED_EXACT:
        return True
    for d in GENERATED_DIRS:
        if q == d or q.startswith(d + "/"):
            return True
    return False


def run_cmd(cmd, cwd, timeout=900):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_porcelain_output(out):
    o = []
    for line in out.splitlines():
        if not line or len(line) < 3:
            continue
        pp = line[3:]
        if " -> " in pp:
            parts = [
                x.strip().strip('"').strip("'") for x in pp.split(" -> ")
            ]
            a = parts[0]
            b = parts[1]
            if not is_generated(a) or not is_generated(b):
                o.append(pp.strip())
        else:
            p = pp.strip().strip('"').strip("'")
            if not is_generated(p):
                o.append(p)
    return o


def step(label, cmd, cwd, timeout=900, non_zero_exit=1, os_error_exit=1, os_error_prefix=""):
    try:
        r = run_cmd(cmd, cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"{label}: exit 1")
        print(f"step failed: {label}")
        sys.exit(1)
    except OSError as e:
        print(f"{label}: exit {os_error_exit}")
        if os_error_prefix:
            print(f"{os_error_prefix}{e}")
        else:
            print(e)
        print(f"step failed: {label}")
        sys.exit(os_error_exit)
    print(f"{label}: exit {r.returncode}")
    if r.stdout:
        print(r.stdout, end="")
    if r.stderr:
        print(r.stderr, end="", file=sys.stderr)
    if r.returncode != 0:
        print(f"step failed: {label}")
        sys.exit(non_zero_exit)
    return r


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=".")
    p.add_argument("--check-only", action="store_true")
    a = p.parse_args()
    repo = Path(a.repo).resolve()
    check_only = a.check_only
    if not check_only:
        lab = "git status --porcelain"
        r = step(
            lab,
            ["git", "status", "--porcelain"],
            repo,
            timeout=900,
            non_zero_exit=2,
            os_error_exit=2,
            os_error_prefix="missing git or failed to run: ",
        )
        out = parse_porcelain_output(r.stdout)
        if out:
            print("refusing: uncommitted changes outside generated set")
            for f in out:
                print(f)
            print("caller must commit them first")
            sys.exit(1)
    if not check_only:
        br = repo / "scripts" / "bundle_runtime.py"
        if not br.is_file():
            print("missing required script: scripts/bundle_runtime.py")
            sys.exit(2)
        step(
            "python3 scripts/bundle_runtime.py",
            [sys.executable, "scripts/bundle_runtime.py"],
            repo,
        )
        for prod in ["products/brothermode", "products/brothersbe"]:
            pd = repo / prod
            sp = pd / "scripts" / "checksums.sh"
            if pd.is_dir() and sp.is_file():
                step(
                    f"sh scripts/checksums.sh CHECKSUMS.sha256 in {prod}",
                    ["sh", "scripts/checksums.sh", "CHECKSUMS.sha256"],
                    pd,
                )
            else:
                print(f"skip checksums for {prod}: no directory or script")
        sd = repo / "scripts" / "system_doc.py"
        if not sd.is_file():
            print("missing required script: scripts/system_doc.py")
            sys.exit(2)
        step(
            "python3 scripts/system_doc.py",
            [sys.executable, "scripts/system_doc.py"],
            repo,
        )
    br = repo / "scripts" / "bundle_runtime.py"
    if not br.is_file():
        print("missing required script: scripts/bundle_runtime.py")
        sys.exit(2)
    step(
        "python3 scripts/bundle_runtime.py --check",
        [sys.executable, "scripts/bundle_runtime.py", "--check"],
        repo,
    )
    sd = repo / "scripts" / "system_doc.py"
    if not sd.is_file():
        print("missing required script: scripts/system_doc.py")
        sys.exit(2)
    step(
        "python3 scripts/system_doc.py --check",
        [sys.executable, "scripts/system_doc.py", "--check"],
        repo,
    )
    r = step(
        "git status --porcelain for generated set",
        [
            "git",
            "status",
            "--porcelain",
            "--",
            "bundle",
            "products/brothermode/CHECKSUMS.sha256",
            "products/brothersbe/CHECKSUMS.sha256",
            "SYSTEM.md",
        ],
        repo,
    )
    files = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        pp = line[3:]
        if " -> " in pp:
            pp = pp.split(" -> ")[-1]
        pth = pp.strip().strip('"').strip("'")
        files.append(pth)
    if files:
        print(f"stage these: git add {' '.join(shlex.quote(f) for f in files)}")
    else:
        print("no generated files changed")
    sys.exit(0)


if __name__ == "__main__":
    main()
