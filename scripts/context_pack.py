#!/usr/bin/env python3
"""TOKEN-02 of the 1.0.20 control plane: one prepared context pack per wave.

MEASURED CAUSE. On 2026-09-18 every agent re-read the worker contract, the
92 KB plan and several whole modules for itself, and each tool round re-sent
that context: a mean of 197,451 tokens per agent. The fix at the source is
to extract ONCE per wave exactly what that wave's units need, and hand every
worker the same small file.

WHAT A PACK HOLDS, in order: the worker contract verbatim; each unit's own
row (objective, owns, done_check, depends_on); for each owned Python file,
its function and class signatures with the first docstring line (by ast,
never by regex); for other owned files, the path and size only.

WHAT IT REFUSES TO HIDE. An owned file that does not exist or cannot be
parsed is NAMED in the pack under MISSING, never skipped, because a worker
told nothing about a file will go and read it whole, which is the cost this
exists to remove. A pack over --max-bytes is refused (exit 1) rather than
truncated, since a truncated pack reads as complete.

Usage: python3 scripts/context_pack.py --wave N [--plan P] [--out F] [--max-bytes 40000]
"""
import argparse
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "docs", "plan", "ORCH-1020-WBS.json")
CONTRACT = os.path.join(ROOT, "docs", "plan", "ORCH-1020-WORKER-CONTRACT.md")


def signatures(path):
    """['def name(args): first docstring line', ...] or raises."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if isinstance(node, ast.ClassDef):
                head = "class %s" % node.name
            else:
                head = "def %s(%s)" % (node.name, ast.unparse(node.args))
            doc = (ast.get_docstring(node) or "").strip().split("\n")[0]
            out.append("%s  # line %d%s" % (head, node.lineno, (": " + doc) if doc else ""))
    return out


def resolve(path, root):
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.join(root, path)


def build(units, contract_text, root):
    lines, missing = ["# CONTEXT PACK", "", "## Worker contract", "", contract_text.strip(), ""], []
    for u in units:
        lines += ["## Unit %s: %s" % (u["id"], u.get("title", "")), "",
                  "objective: %s" % u.get("objective", ""),
                  "depends_on: %s" % ", ".join(u.get("depends_on", [])),
                  "done_check: %s" % (u.get("done_check") or "(none written: the unit must name one)"),
                  "owns:"]
        for rel in u.get("owns", []):
            full = resolve(rel, root)
            if not os.path.exists(full):
                missing.append("%s (unit %s): does not exist yet" % (rel, u["id"]))
                lines.append("  - %s  (NEW FILE)" % rel)
                continue
            if full.endswith(".py"):
                try:
                    sigs = signatures(full)
                except (SyntaxError, ValueError, OSError) as exc:
                    missing.append("%s (unit %s): unparseable, %s" % (rel, u["id"], exc))
                    lines.append("  - %s  (UNPARSEABLE)" % rel)
                    continue
                lines.append("  - %s  (%d bytes, %d definitions)" % (rel, os.path.getsize(full), len(sigs)))
                lines += ["      " + s for s in sigs]
            else:
                lines.append("  - %s  (%d bytes, not summarised)" % (rel, os.path.getsize(full)))
        lines.append("")
    lines += ["## MISSING", ""] + (["- " + m for m in missing] or ["(none)"])
    return "\n".join(lines) + "\n", missing


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wave", type=int, required=True)
    ap.add_argument("--plan", default=PLAN)
    ap.add_argument("--contract", default=CONTRACT)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--out")
    ap.add_argument("--max-bytes", type=int, default=40000)
    a = ap.parse_args(argv)
    try:
        with open(a.plan, encoding="utf-8") as fh:
            units = [u for u in json.load(fh)["units"] if u.get("wave") == a.wave]
        with open(a.contract, encoding="utf-8") as fh:
            contract = fh.read()
    except (OSError, ValueError, KeyError) as exc:
        print("context-pack: NO-DATA, cannot read a source (%s)" % exc)
        return 2
    if not units:
        print("context-pack: NO-DATA, wave %d has no units" % a.wave)
        return 2
    text, missing = build(units, contract, a.root)
    size = len(text.encode("utf-8"))
    if size > a.max_bytes:
        print("context-pack: REFUSED, wave %d pack is %d bytes, over the %d cap; "
              "split the wave rather than truncate it" % (a.wave, size, a.max_bytes))
        return 1
    if a.out:
        tmp = a.out + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, a.out)
        except OSError as exc:
            print("context-pack: REFUSED, cannot write %s (%s)" % (a.out, exc))
            return 2
    else:
        sys.stdout.write(text)
    print("context-pack: wave %d, %d unit(s), %d bytes, %d missing"
          % (a.wave, len(units), size, len(missing)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
