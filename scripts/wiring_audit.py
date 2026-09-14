#!/usr/bin/env python3
"""Find capability that exists in the tree but nothing reaches.

A tool is WIRED when something other than itself and its own test names it:
a gate script, a hook registration, the bundle closure, a plugin manifest,
the component registry, a doc a user reads, or another module's import.

A tool that only its own test names is UNWIRED. It was built, it may be
correct, and no user path arrives at it. That is the defect this measures.

NO-DATA is never a pass: a tree with no tools found reports NO-DATA and
exits 2 rather than declaring a clean sweep.

  python3 scripts/wiring_audit.py [--root DIR] [--json]
"""
import argparse
import json
import os
import re
import subprocess
import sys

# Where a reference makes a tool reachable. Order matters only for reporting.
WIRING_SITES = [
    ("gate", ["scripts/check_all.sh", "scripts/required_fast.sh",
              "products/brothermode/scripts/local-gates.sh",
              "products/brothersbe/scripts/local-gates.sh"]),
    # EVERY hooks file, not just the bundled one. The first root set named only
    # bundle/hooks/hooks.json and therefore reported the vault engine as
    # unreachable while products/brothermode/hooks/hooks.json registered it on
    # SessionStart. A root set that misses a real door manufactures dead tools.
    # bundle/hooks/hooks.json was RENAMED to bundle/hooks/union.json on
    # 2026-09-13 (it double-fired every shared hook on Claude Code, since
    # brothermode/brothersbe already register the same events themselves);
    # a missing root is dropped silently by this module's own frontier
    # construction, so leaving the retired name out here is correct, not an
    # oversight.
    ("hooks", ["bundle/hooks/union.json",
               "products/brothermode/hooks/hooks.json",
               "products/brothersbe/hooks/hooks.json"]),
    ("bundle", ["bundle/runtime/RUNTIME-MANIFEST.json"]),
    ("registry", ["docs/plan/KEY-COMPONENTS.json"]),
    ("manifest", [".claude-plugin/marketplace.json"]),
]
TOOL_DIRS = ("scripts", "products/brothermode/tools", "products/brothersbe/tools",
             "products/brothermode/scripts", "products/brothersbe/scripts")


def tracked(root):
    out = subprocess.run(["git", "-C", root, "ls-tree", "-r", "HEAD", "--name-only"],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return out.stdout.splitlines() if out.returncode == 0 else []


def is_tool(path):
    if not path.endswith(".py") or "/test_" in path or os.path.basename(path).startswith("test_"):
        return False
    return any(path.startswith(d + "/") for d in TOOL_DIRS)




def selftest():
    """Drive backwards every defect this audit shipped with on 2026-09-10.

    Each case failed before its fix. A guard whose refusals were never driven
    backwards is the same class of thing this audit exists to find.
    """
    import shutil
    import subprocess as sp
    import tempfile

    root = tempfile.mkdtemp(prefix="wiring-selftest-")
    def write(rel, text):
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)

    # A real tool the gate invokes, a fixture COPY sharing its basename, a
    # module the real tool loads by bare name, a tool named only in a comment,
    # a tool named only by a dead tool, and a tool a product hook registers.
    write("scripts/check_all.sh", 'run_check "live" python3 scripts/live.py\n')
    write("scripts/live.py", 'import helper\nP = "policy.py"\n')
    write("scripts/helper.py", "x = 1\n")
    write("scripts/policy.py", "y = 1\n")
    write("scripts/fixtures/seam/policy.py", "y = 2\n")
    write("scripts/mentioned_in_comment.py", "z = 1\n")
    write("scripts/only_dead_names_me.py", "w = 1\n")
    write("scripts/dead.py", '"x" ; open("scripts/only_dead_names_me.py")\n')
    write("scripts/hooked.py", "h = 1\n")
    write("products/brothermode/hooks/hooks.json",
          '{"hooks": {"SessionStart": ["scripts/hooked.py refresh"]}}\n')
    with open(os.path.join(root, "scripts/check_all.sh"), "a") as fh:
        fh.write("# see also scripts/mentioned_in_comment.py\n")
    sp.run(["git", "init", "-q", root], check=True, stdin=sp.DEVNULL)
    sp.run(["git", "-C", root, "add", "-A"], check=True, stdin=sp.DEVNULL)
    sp.run(["git", "-C", root, "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "t"], check=True, stdin=sp.DEVNULL)

    out = sp.run([sys.executable, os.path.abspath(__file__),
                  "--root", root, "--json"], capture_output=True, text=True,
                 stdin=sp.DEVNULL)
    got = json.loads(out.stdout)
    dead = set(got.get("unwired", [])) + set() if False else (
        set(got.get("unwired", [])) | set(got.get("orphan-cluster", []))
        | set(got.get("docs-only", [])))

    cases = [
        ("a gate-invoked tool is wired", "scripts/live.py", False),
        ("an imported module is wired", "scripts/helper.py", False),
        ("a bare colliding name resolves in the referrer's directory",
         "scripts/policy.py", False),
        ("a hook in a PRODUCT hooks file is a root", "scripts/hooked.py", False),
        ("a mention in a comment is not an edge",
         "scripts/mentioned_in_comment.py", True),
        ("a tool named only by a dead tool is dead",
         "scripts/only_dead_names_me.py", True),
        ("the dead tool itself is dead", "scripts/dead.py", True),
    ]
    bad = 0
    for label, path, want_dead in cases:
        is_dead = path in dead
        ok = is_dead == want_dead
        bad += 0 if ok else 1
        print("%-4s %-58s %s" % ("OK" if ok else "FAIL", label,
                                 "dead" if is_dead else "wired"))
    shutil.rmtree(root, ignore_errors=True)
    if bad:
        print("\n%d of %d case(s) FAILED" % (bad, len(cases)))
        return 1
    print("\nOK over %d case(s)" % len(cases))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/brother-hub"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    files = tracked(args.root)
    tools = [f for f in files if is_tool(f)]
    if not tools:
        print("NO-DATA: no tool files found under %s; nothing was measured, "
              "and that is not a pass" % ", ".join(TOOL_DIRS))
        return 2

    # IDENTITY IS THE FULL PATH, NEVER THE BASENAME. The first version keyed
    # tools by basename, and scripts/fixtures/bmu_vault_seam/ holds five
    # fixture copies whose basenames collide with the five real vault tools.
    # Every mention was credited to the fixture and the real tools read as
    # unreachable. A colliding basename now proves nothing on its own: the
    # reference must carry a path separator that resolves to one file.
    from collections import defaultdict
    by_base = defaultdict(list)
    for t in tools:
        by_base[os.path.basename(t)].append(t)

    # What text counts as naming this tool. A unique basename may stand alone;
    # a colliding one must appear with enough of its directory to disambiguate.
    keys = {}
    ambiguous = {}
    for t in tools:
        base = os.path.basename(t)
        forms = {t}
        parts = t.split("/")
        for i in range(1, len(parts)):
            forms.add("/".join(parts[i:]))
        if len(by_base[base]) > 1:
            # A colliding bare basename is ambiguous, but refusing it outright
            # is a false fail: bm_vault.py loads bm_vault_policy.py by a bare
            # filename joined to its own directory at runtime, and a fixture
            # copy shares the name. Path forms are kept for everyone; the bare
            # name is resolved by the referrer's directory below.
            forms = {f for f in forms if "/" in f}
            ambiguous.setdefault(base, set()).add(t)
        for f in forms:
            keys.setdefault(f, set()).add(t)

    # ONE generic pattern, then dictionary lookups. An alternation over ~1800
    # path forms is quadratic enough to take minutes over this tree, and a
    # guard nobody can afford to run is a guard nobody runs. This finds every
    # path-shaped token once and resolves it by table.
    TOKEN = re.compile(r"[A-Za-z0-9_./-]+\.py")
    # AN IMPORT IS AN EDGE, and it carries no ".py". Missing this made every
    # module a tool imports read as unreachable: bm_vault.py imports
    # bm_vault_policy, and the audit called the policy engine dead while
    # calling its only caller wired.
    IMPORT = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_.]+)", re.M)
    modules = {}
    for t in tools:
        modules.setdefault(os.path.basename(t)[:-3], set()).add(t)

    # Edges run file -> tool over EVERY tracked file, not only tool files, so a
    # dispatcher that is not itself a tool still propagates reachability.
    names_out = defaultdict(set)
    mentions = {t: set() for t in tools}
    for f in files:
        try:
            with open(os.path.join(args.root, f), "r", errors="ignore") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent a tracked file that cannot be read contributes no edges; the audit only reads and never rewrites, and an unreadable file is not evidence either way
            continue
        live = [ln for ln in text.splitlines()
                if not ln.lstrip().startswith(("#", "//"))]
        found = set()
        for tok in TOKEN.findall("\n".join(live)):
            # A token resolves by its longest suffix that names a known tool,
            # so "products/brothermode/tools/bm_vault.py" beats "bm_vault.py"
            # and a colliding bare basename resolves to nothing.
            parts = tok.split("/")
            for i in range(len(parts)):
                cand = "/".join(parts[i:])
                if cand in keys:
                    found.add(cand)
                    break
            else:
                # No path form matched. If the bare name is one of the colliding
                # ones, credit the candidate sitting in the referrer's own
                # directory, which is how these runtime path joins resolve.
                same = [t for t in ambiguous.get(tok, ())
                        if os.path.dirname(t) == os.path.dirname(f)]
                if len(same) == 1:
                    found.add(same[0])
        for mod in IMPORT.findall("\n".join(live)):
            leaf = mod.split(".")[-1]
            for target in modules.get(leaf, ()):
                # An import names a module, not a path, so a colliding leaf is
                # ambiguous. Credit it only when the importer sits in the same
                # directory, which is how these tools actually import.
                if os.path.dirname(target) == os.path.dirname(f):
                    found.add(target)
        for hit in found:
            for target in ({hit} if hit in tools else keys[hit]):
                if target == f:
                    continue                   # a file naming itself proves nothing
                base = os.path.basename(target)[:-3]
                if os.path.basename(f) in ("test_%s.py" % base,
                                           "test_test_%s.py" % base):
                    continue                   # nor does its own test
                mentions[target].add(f)
                names_out[f].add(target)

    # Reachability is computed from the ROOTS a user or a gate enters through,
    # and it propagates: a tool named only by another dead tool is dead too.
    roots = set()
    for _, sites in WIRING_SITES:
        roots.update(sites)
    roots.update(f for f in files
                 if f.endswith(("/bin/sbe", "/bin/brother"))
                 or f.endswith("brothersbe/cli.py")
                 or f.endswith("brothermode/cli.py"))
    fileset = set(files)
    reachable, seen = set(), set()
    frontier = [r for r in roots if r in fileset]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for tool in names_out.get(cur, ()):
            reachable.add(tool)
            frontier.append(tool)

    def classify(tool):
        refs = mentions[tool]
        for label, sites in WIRING_SITES:
            if refs & set(sites):
                return label
        # CODE BEFORE PROSE. Checking docs first misfiles every tool that a
        # guide mentions AND a dispatcher imports: the brothersbe CLI names 50
        # tools, and all 50 read as prose-only under the wrong order.
        if tool in reachable:
            return "code-only"
        code = {r for r in refs if r.endswith((".py", ".sh", ".json", ".toml"))}
        if code:
            return "orphan-cluster"
        if refs:
            return "docs-only"
        return "unwired"

    buckets = {}
    for t in tools:
        buckets.setdefault(classify(t), []).append(t)

    if args.json:
        print(json.dumps({k: sorted(v) for k, v in buckets.items()}, indent=2))
        return 0

    order = ["unwired", "orphan-cluster", "docs-only", "code-only", "registry", "manifest",
             "bundle", "hooks", "gate"]
    print("WIRING AUDIT of %s" % args.root)
    print("%d tool file(s) examined\n" % len(tools))
    for k in order:
        got = buckets.get(k, [])
        if not got:
            continue
        print("%-11s %3d" % (k, len(got)))
        if k in ("unwired", "docs-only", "orphan-cluster"):
            for t in sorted(got):
                print("              %s" % t)
    unwired = len(buckets.get("unwired", []))
    docs = len(buckets.get("docs-only", []))
    orphan = len(buckets.get("orphan-cluster", []))
    print("\n%d tool(s) nothing reaches, %d reachable only from prose, "
          "%d named only by code that is itself unreachable" % (unwired, docs, orphan))
    print("DEAD SURFACE: %d of %d tool(s) no entry point arrives at"
          % (unwired + docs + orphan, len(tools)))
    return 1 if (unwired + orphan) else 0


if __name__ == "__main__":
    sys.exit(main())
