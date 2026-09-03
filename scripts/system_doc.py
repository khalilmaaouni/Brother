"""system_doc: a description of this system that cannot drift, because it is generated.

TEAM COMPLAINT P12, from a reviewer: fifty designs after a year and none of them
describes the system. Point-in-time designs, no living record.

The tempting answer is to write a good architecture document. That answer is
what produced the fifty. Each of them was a good architecture document on the day
it was written, and each became wrong quietly, and nobody could tell which of the
fifty was still true. Writing a fifty-first is not a fix, it is the disease.

SO THIS IS GENERATED FROM THE CODE AND CHECKED IN CI. `--check` regenerates and
compares; a difference is a FAILURE, which means the document cannot be wrong for
longer than it takes somebody to run the battery. That is the whole design, and
it is the only property that distinguishes this from design fifty-one.

WHAT IT DESCRIBES IS WHAT EXISTS, never what was intended. Each part is named by
its own first docstring line, so the description is written by whoever wrote the
part, in the file, where they will see it again. A part with no docstring is
listed as NO-DATA rather than quietly omitted: a system record that hides the
undocumented corners is worse than none, because it looks complete.

EACH PART IS PAIRED WITH WHAT PROVES IT. A module with a suite wired into the
battery says so; a module with none says so too. That pairing is the thing a
reviewer actually wants and no hand-written design has ever kept current.

BORROWED: docs-as-code, and the living-documentation idea behind architecture
decision records that are indexed and superseded rather than accumulated. The
adaptation is that this estate already refuses claims without evidence
everywhere else, so its system description is held to the same rule.

Python 3, standard library only. No network.
"""
import argparse
import ast
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
OUT = os.path.join(ROOT, "SYSTEM.md")
BATTERY = os.path.join(SCRIPTS, "check_all.sh")
NODATA = "NO-DATA"


def purpose(path):
    """The first docstring line, written by whoever wrote the file."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    doc = ast.get_docstring(tree)
    if not doc:
        return None
    first = doc.strip().splitlines()[0].strip()
    return re.sub(r"^%s[:\s]*" % re.escape(os.path.basename(path)[:-3]), "", first)


def battery_checks(path=None):
    """(name, command) for every check the battery declares.

    RESOLVED AT CALL TIME, not at definition time. Written first as
    `path=BATTERY`, which binds the module constant into the default when the
    function object is created, so overriding BATTERY afterwards silently had no
    effect and the NO-DATA branch below could never be reached. This estate had
    already documented that exact bug in gen_readiness_board.load(), and it was
    repeated here anyway; the test that catches it is the only reason it did not
    ship. 2026-08-29."""
    path = BATTERY if path is None else path
    if not os.path.isfile(path):
        return None
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r'\s*run_check\s+"([^"]+)"\s+(.*)', line)
            if m:
                out.append((m.group(1), m.group(2).strip()))
    return out


def parts(scripts_dir=None):
    """Every module of this system, with what it is for and what proves it."""
    scripts_dir = SCRIPTS if scripts_dir is None else scripts_dir
    checks = battery_checks() or []
    proven = {}
    direct = {}
    for name, cmd in checks:
        for mod in re.findall(r"scripts[./]([a-z0-9_]+)\.py", cmd):
            direct.setdefault(mod.replace("test_", ""), []).append(name)
        for mod in re.findall(r"scripts\.([a-z0-9_]+)", cmd):
            direct.setdefault(mod.replace("test_", ""), []).append(name)
    for mod, names in direct.items():
        proven.setdefault(mod, []).extend(names)
    # TRANSITIVE CREDIT, one level, added 2026-09-01. A runner the battery names can
    # invoke sibling scripts, and until now those siblings were credited to nobody and
    # printed "NO-DATA, nothing in the battery runs it". Eleven capability-area scripts
    # read that way while `python3 scripts/acceptance.py` passed all eleven in a live
    # run, so the generated map and the harness disagreed and nothing could adjudicate.
    # The credit is earned from the RUNNER'S OWN SOURCE, never asserted: the runner has
    # to build the sibling's filename in code. acceptance.py does it with
    # "acceptance_{}.py".format(area["id"]), which no literal-path parser can see, so
    # the prefix form is read as well as the literal one. A module the runner merely
    # mentions in prose is NOT credited, because a comment is not an invocation.
    for mod, names in list(direct.items()):
        src_path = os.path.join(scripts_dir, "%s.py" % mod)
        try:
            with io.open(src_path, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            continue  # sbe: allow-silent a runner outside scripts/ credits nothing transitively, which is the safe direction
        invoked = set(re.findall(r'"([a-z0-9_]+)_\{\}\.py"', src))
        invoked |= set(re.findall(r"'([a-z0-9_]+)_\{\}\.py'", src))
        literal = set(re.findall(r'["\']([a-z0-9_]+)\.py["\']', src))
        for sibling in sorted(os.listdir(scripts_dir)):
            if not sibling.endswith(".py") or sibling.startswith("test_"):
                continue
            smod = sibling[:-3]
            if smod == mod:
                continue
            prefix_hit = any(smod.startswith(p + "_") and smod[len(p) + 1:].isalnum()
                             for p in invoked)
            if prefix_hit or smod in literal:
                proven.setdefault(smod, []).extend(names)
    rows = []
    for fn in sorted(os.listdir(scripts_dir)):
        if not fn.endswith(".py") or fn.startswith("test_"):
            continue
        mod = fn[:-3]
        rows.append({
            "module": mod,
            "purpose": purpose(os.path.join(scripts_dir, fn)),
            "proven_by": sorted(set(proven.get(mod, []))),
            "has_tests": os.path.isfile(os.path.join(scripts_dir, "test_%s.py" % mod)),
        })
    return rows


def render(rows, checks):
    undocumented = [r for r in rows if not r["purpose"]]
    unproven = [r for r in rows if not r["proven_by"]]
    L = []
    A = L.append
    A("# What this system is, right now")
    A("")
    A("GENERATED by `scripts/system_doc.py`. Do not edit this file by hand: the")
    A("next `--check` will overwrite your edit and fail the battery.")
    A("")
    A("It exists because of a reviewer's complaint that there were fifty designs")
    A("after a year and none of them described the system. Writing a fifty first")
    A("good design is what produced the fifty. This one is generated from the")
    A("code and checked, so it cannot be wrong for longer than it takes somebody")
    A("to run the battery.")
    A("")
    A("## The shape, in counts")
    A("")
    A("| | |")
    A("|---|---:|")
    A("| Parts | %d |" % len(rows))
    A("| Parts with a purpose written in the file | %d |" % (len(rows) - len(undocumented)))
    A("| Parts with a suite wired into the battery | %d |" % (len(rows) - len(unproven)))
    A("| Checks in the battery | %d |" % len(checks))
    A("")
    if undocumented:
        A("%d part(s) carry no purpose line and are listed as %s below. They are"
          % (len(undocumented), NODATA))
        A("shown rather than omitted, because a system record that hides its")
        A("undocumented corners looks complete and is not.")
        A("")
    A("## Every part, what it is for, and what proves it")
    A("")
    A("| Part | What it is for | What proves it |")
    A("|---|---|---|")
    for r in rows:
        proof = ", ".join("`%s`" % p for p in r["proven_by"]) if r["proven_by"] else (
            "**%s**, nothing in the battery runs it" % NODATA)
        A("| `%s` | %s | %s |"
          % (r["module"], r["purpose"] or "**%s**, no docstring" % NODATA, proof))
    A("")
    A("## What the battery actually runs")
    A("")
    for name, cmd in checks:
        A("- `%s`: `%s`" % (name, cmd))
    A("")
    return "\n".join(L) + "\n"


def build():
    checks = battery_checks()
    if checks is None:
        return None
    return render(parts(), checks)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="fail if the written file no longer matches the code")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    body = build()
    if body is None:
        print("%s: %s could not be read, so nothing was described. That is not a "
              "pass" % (NODATA, BATTERY), file=sys.stderr)
        return 2

    if args.check:
        if not os.path.isfile(args.out):
            print("%s does not exist. Run this without --check to write it."
                  % args.out, file=sys.stderr)
            return 1
        with open(args.out, encoding="utf-8") as fh:
            current = fh.read()
        if current == body:
            print("SYSTEM.md still describes the code")
            return 0
        print("SYSTEM.md NO LONGER DESCRIBES THE CODE. Something was added, "
              "removed or renamed and the record did not follow. Regenerate it "
              "with: python3 scripts/system_doc.py", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("wrote %s: %d part(s), %d check(s)"
          % (args.out, len(parts()), len(battery_checks() or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
