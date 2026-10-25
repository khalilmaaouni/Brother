"""product_manifests: every product's CHECKSUMS.sha256 describes the tree it ships in, checked and regenerated at landing.

WHY IT EXISTS (1.1.8 council, owner ruling 2026-10-23, "Landing regenerates it"): plan_lint refused any spec touching a
product manifest as "a generated file the landing regenerates itself", and the landing never regenerated it. So a loop
build that changed a product file landed with a stale manifest, the brothermode or brothersbe verify-install row went red
on the base, and the next landing that ran the whole gate HELD on a red nobody it built (2026-10-22: I33.a's landing,
repaired by hand in 06f512b06). This file is that missing gate, run by scripts/loop/land_batch.py GATES in the landing
copy: --check is each product's own verify-install.sh, exactly the whole gate's row; without --check each product's own
scripts/checksums.sh rewrites CHECKSUMS.sha256.

WALK MODE ON PURPOSE. In the landing copy a build's new file is on disk but not in git's index, and checksums.sh in git
mode lists only tracked files, so the manifest would miss it ("1 extra" in verify-install). With git hidden
(GIT_DIR to a path that does not exist) checksums.sh walks the tree instead; on a clean tree the two modes write the same
bytes (measured on both products at 366200264, 2026-10-23).

FAIL DIRECTION: a product with a checksums.sh but no verify-install.sh, a tree with no product to check, a script that
cannot start or exits nonzero, all exit 1 (a red gate), never 0. Python 3, standard library only. No network.
"""
import argparse
import os
import subprocess
import sys

PRODUCTS = "products"
GENERATOR = os.path.join("scripts", "checksums.sh")
VERIFIER = os.path.join("scripts", "verify-install.sh")
MANIFEST = "CHECKSUMS.sha256"
#: A FIXED LIST, never a directory scan (security review 2026-10-23): this gate runs the product scripts the build's tree
#: holds, and grade_build protects exactly these two products' checksums.sh and verify-install.sh. A scan would also run
#: a products/<new>/scripts/checksums.sh a build planted, which could rewrite a manifest in this gate's output set.
SHIPPED = ("brothermode", "brothersbe")


def products(root):
    """The SHIPPED products' folders that hold a manifest generator, in order; [] when none does."""
    return [os.path.join(root, PRODUCTS, n) for n in SHIPPED if os.path.isfile(os.path.join(root, PRODUCTS, n, GENERATOR))]


def run(argv, cwd, env=None, timeout=600):
    """(returncode, last output line); a script that cannot start or overruns reads as returncode 1 with the reason."""
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "could not run %s: %s" % (" ".join(argv), exc)
    lines = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
    return r.returncode, (lines[-1] if lines else "no output")[:200]


def check(root):
    found, bad = products(root), 0
    if not found:
        print("NO-DATA: no %s/*/%s under %s, so no manifest was checked" % (PRODUCTS, GENERATOR, root))
        return 1
    for p in found:
        name = os.path.relpath(p, root)
        if not os.path.isfile(os.path.join(p, VERIFIER)):
            print("NO-DATA %s: it has %s and no %s" % (name, GENERATOR, VERIFIER)); bad += 1; continue
        rc, last = run(["sh", VERIFIER], p, env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
        print("%s %s: %s" % ("PASS" if rc == 0 else "FAIL", name, last if rc else "verify-install exit 0"))
        bad += rc != 0
    return 1 if bad else 0


def regenerate(root):
    found, bad = products(root), 0
    if not found:
        print("NO-DATA: no %s/*/%s under %s, so no manifest was written" % (PRODUCTS, GENERATOR, root))
        return 1
    env = dict(os.environ, GIT_DIR=os.path.join(root, ".no-git-walk-mode"), GIT_CEILING_DIRECTORIES=root)
    for p in found:
        rc, last = run(["sh", GENERATOR, MANIFEST], p, env=env)
        print("%s %s: %s" % ("WROTE" if rc == 0 else "FAIL", os.path.relpath(p, root), "%s regenerated" % MANIFEST if rc == 0 else last))
        bad += rc != 0
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="check (--check) or regenerate every product's CHECKSUMS.sha256")
    ap.add_argument("--check", action="store_true", help="run each product's verify-install.sh; exit 1 on any failure")
    ap.add_argument("--root", default=".", help="the tree holding products/ (default: the current directory)")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root)
    return check(root) if args.check else regenerate(root)


if __name__ == "__main__":
    sys.exit(main())
