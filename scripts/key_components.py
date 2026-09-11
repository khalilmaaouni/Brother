"""key_components: the components this estate built on purpose cannot be lost,
drifted, or reinvented without something going red.

FOUNDER ORDER 2026-09-10, his words: "we need to enforce tools as a skill such
as intake dayboard closing ceremony and other we develop overtime and we save
them there to reference to them over time make this a law and enforce it".

THE NIGHT THAT CAUSED IT, because the law is only as convincing as the failure
behind it. A documentation replacement deleted the maintainer closing ceremony
page, and the only reason anybody noticed was that one unrelated test happened
to assert that page's existence. In the same hour, the intake screen's own
guard, scripts/test_decide.py, was found FAILING on unchanged main, and had
been failing unnoticed because it was never wired into scripts/check_all.sh.
Three more key guards were in the same state. So the estate had built these
components deliberately, written tests for them, and then left the tests
unrun: the tools were protected by nothing but memory.

WHAT THIS REFUSES, each one a way a component quietly stops being real:
  1. a registered tool that is not in the tree (deleted or moved);
  2. a registered guard that is not in the tree;
  3. a registered guard that scripts/check_all.sh never runs, which is the
     exact hole that let a red intake guard sit on main;
  4. a registered artifact path that is not in the tree;
  5. a registry that is missing, unparseable, or empty.

WHAT IT DELIBERATELY DOES NOT DO. It does not RUN the guards. The battery runs
them, which is the point of requiring registration, and a checker that re-ran
eight suites would take twenty minutes and would be skipped. This checks that
the estate's promises about its own components are structurally true, and the
battery checks that they behave.

NO-DATA IS NEVER A PASS: an absent or empty registry exits 2, never 0. An
empty scan reading as success is the failure this estate keeps paying for.

Driven backwards by --selftest over a fixture registry carrying one instance
of each refusal.

Python 3, standard library only. No network.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "docs", "plan", "KEY-COMPONENTS.json")
BATTERY = os.path.join(ROOT, "scripts", "check_all.sh")
#: The subset CI actually gates a pull request on. Registration in the full
#: battery is not enough on its own: the full battery is 259 checks and about
#: 35 minutes, so it is run deliberately and rarely, while this subset runs on
#: every pull request. A guard that lives only in the slow battery can fail for
#: weeks behind a green pull request, which is exactly how the intake screen's
#: own guard came to be red on main on 2026-09-10 with nobody the wiser.
CI_GATE = os.path.join(ROOT, "scripts", "required_fast.sh")

#: A registry entry must carry these, or it is not a protectable promise.
REQUIRED = ("id", "what", "tool", "guard", "contract")


def runs_it(script_text, guard_path):
    """True only when the battery RUNS the guard, never when it merely
    mentions it. A substring match would accept a guard named in a comment,
    and this estate has already paid for reading a check's presence as proof
    of it running: the proof is the line that executes it."""
    pattern = re.compile(
        r"^\s*run_check\b[^\n]*(?<![\w./-])%s(?![\w./-])"
        % re.escape(guard_path), re.M)
    return bool(pattern.search(script_text))


def load_registry(path=None):
    """The registry, or None when it cannot be read. None (never an empty
    list) so a missing law refuses rather than silently checking nothing."""
    path = path or REGISTRY
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    comps = data.get("components")
    if not isinstance(comps, list):
        return None
    return comps


def battery_text(path=None):
    """scripts/check_all.sh's own text, or None when it cannot be read."""
    path = path or BATTERY
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:  # sbe: allow-silent both call sites in check() below
        # explicitly refuse with a NO-DATA exit on None rather than treating
        # it as an empty or absent registration, so an unreadable battery or
        # ci gate file can never fold into a PASS
        return None


def check(root=ROOT, registry_path=None, battery_path=None,
          ci_path=None):
    """Returns (exit_code, lines). 0 PASS, 1 FAIL, 2 NO-DATA."""
    lines = []
    comps = load_registry(registry_path)
    if comps is None:
        return 2, ["NO-DATA: no readable component registry at %s. A missing "
                   "law refuses everything rather than passing. Not a pass."
                   % (registry_path or REGISTRY)]
    if not comps:
        return 2, ["NO-DATA: the registry lists no component, so nothing was "
                   "checked. An empty scan is not a pass."]
    battery = battery_text(battery_path)
    if battery is None:
        return 2, ["NO-DATA: %s could not be read, so guard registration "
                   "cannot be decided. Not a pass."
                   % (battery_path or BATTERY)]
    # Only fetched, and only required to be readable, when some component
    # actually declares ci_gated: an unreadable CI_GATE used to be silently
    # downgraded to "" at the ci_gated check below, which made every
    # ci_gated component FAIL with a wrong reason ("never runs it") instead
    # of the whole check reporting the true NO-DATA.
    ci_text = None
    if any(comp.get("ci_gated") for comp in comps):
        ci_text = battery_text(ci_path or CI_GATE)
        if ci_text is None:
            return 2, ["NO-DATA: %s could not be read, so ci-gated "
                       "registration cannot be decided. Not a pass."
                       % (ci_path or CI_GATE)]

    findings = []
    for comp in comps:
        cid = comp.get("id") or "<unnamed>"
        for field in REQUIRED:
            if not comp.get(field):
                findings.append("%s: registry entry has no %s" % (cid, field))
        tool = comp.get("tool")
        if tool and not os.path.exists(os.path.join(root, tool)):
            findings.append("%s: its tool %s is not in the tree" % (cid, tool))
        guard = comp.get("guard")
        if guard:
            # A guard may carry its own flags, as a selftest mode does.
            guard_path = guard.split()[0]
            if not os.path.exists(os.path.join(root, guard_path)):
                findings.append("%s: its guard %s is not in the tree"
                                % (cid, guard_path))
            elif not runs_it(battery, guard_path):
                findings.append(
                    "%s: its guard %s is in the tree but %s never runs it, so "
                    "it can fail for months without anybody seeing"
                    % (cid, guard_path, os.path.basename(battery_path
                                                         or BATTERY)))
            elif comp.get("ci_gated") and not runs_it(ci_text or "",
                                                       guard_path):
                findings.append(
                    "%s: declares ci_gated but %s never runs %s, so a pull "
                    "request can go green while this component is broken"
                    % (cid, os.path.basename(ci_path or CI_GATE), guard_path))
        for art in (comp.get("artifacts") or "").split(","):
            art = art.strip()
            # Only literal paths are checked; a glob or a prose description
            # of where output lands is not a promise about one file.
            if not art or "*" in art or " " in art:
                continue
            if not os.path.exists(os.path.join(root, art)):
                findings.append("%s: its artifact %s is not in the tree"
                                % (cid, art))

    lines.append("registry: %d component(s)" % len(comps))
    if findings:
        lines.append("FAIL: %d finding(s)" % len(findings))
        lines.extend("  " + f for f in findings)
        return 1, lines
    lines.append("PASS: every component's tool, guard and artifact is in the "
                 "tree, and every guard is wired into the battery.")
    return 0, lines


def selftest():
    """One fixture per refusal. A checker only ever seen green is a claim."""
    import shutil
    import tempfile
    ok = True
    tmp = tempfile.mkdtemp(prefix="key-components-selftest-")
    try:
        os.makedirs(os.path.join(tmp, "scripts"))
        os.makedirs(os.path.join(tmp, "docs", "plan"))
        real_tool = os.path.join("scripts", "real_tool.py")
        real_guard = os.path.join("scripts", "test_real_tool.py")
        for rel in (real_tool, real_guard):
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                fh.write("# fixture\n")
        battery = os.path.join(tmp, "scripts", "check_all.sh")
        with open(battery, "w", encoding="utf-8") as fh:
            fh.write('run_check "real" python3 scripts/test_real_tool.py\n')
        # A battery that only MENTIONS the guard, in a comment, must not count.
        mention_only = os.path.join(tmp, "scripts", "mention_only.sh")
        with open(mention_only, "w", encoding="utf-8") as fh:
            fh.write("# see scripts/test_real_tool.py for the real check\n")
        # A CI gate that runs it, and one that does not.
        ci_runs = os.path.join(tmp, "scripts", "ci_runs.sh")
        with open(ci_runs, "w", encoding="utf-8") as fh:
            fh.write('run_check "real" python3 scripts/test_real_tool.py\n')
        ci_skips = os.path.join(tmp, "scripts", "ci_skips.sh")
        with open(ci_skips, "w", encoding="utf-8") as fh:
            fh.write('run_check "other" python3 scripts/test_other.py\n')
        reg = os.path.join(tmp, "docs", "plan", "KEY-COMPONENTS.json")

        def write(components):
            with open(reg, "w", encoding="utf-8") as fh:
                json.dump({"components": components}, fh)

        good = {"id": "good", "what": "w", "tool": real_tool,
                "guard": real_guard, "contract": "c"}
        cases = [
            ("all good", [dict(good)], 0, "PASS"),
            ("missing tool", [dict(good, tool="scripts/ghost.py")], 1,
             "is not in the tree"),
            ("missing guard", [dict(good, guard="scripts/test_ghost.py")], 1,
             "is not in the tree"),
            ("guard not in battery", [dict(good, guard=real_tool)], 1,
             "never runs it"),
            ("missing field", [{k: v for k, v in good.items()
                                if k != "contract"}], 1, "no contract"),
            ("missing artifact", [dict(good, artifacts="docs/ghost.md")], 1,
             "artifact docs/ghost.md is not in the tree"),
            ("empty registry", [], 2, "NO-DATA"),
        ]
        for name, comps, want_code, needle in cases:
            write(comps)
            code, lines = check(root=tmp, registry_path=reg,
                                battery_path=battery)
            body = "\n".join(lines)
            hit = code == want_code and needle in body
            print("%-22s %s" % (name, "caught" if hit else "MISSED"))
            ok = ok and hit
        # THE TWO CLAUSES ADDED 2026-09-10, each driven backwards.
        write([dict(good)])
        code, lines = check(root=tmp, registry_path=reg,
                            battery_path=mention_only)
        hit = code == 1 and "never runs it" in "\n".join(lines)
        print("%-22s %s" % ("mention is not a run", "caught" if hit else "MISSED"))
        ok = ok and hit

        write([dict(good, ci_gated=True)])
        code, lines = check(root=tmp, registry_path=reg, battery_path=battery,
                            ci_path=ci_skips)
        hit = code == 1 and "can go green while this component is broken" in \
            "\n".join(lines)
        print("%-22s %s" % ("ci gate missing", "caught" if hit else "MISSED"))
        ok = ok and hit

        code, lines = check(root=tmp, registry_path=reg, battery_path=battery,
                            ci_path=ci_runs)
        hit = code == 0
        print("%-22s %s" % ("ci gate present", "caught" if hit else "MISSED"))
        ok = ok and hit

        # An unreadable ci_path must be NO-DATA, never a FAIL naming the
        # wrong reason. Before the fix, battery_text() returning None here
        # was silently downgraded to "" at the ci_gated check, so a missing
        # CI_GATE file reported "never runs it" (a wrong FAIL) instead of the
        # true NO-DATA.
        code, lines = check(root=tmp, registry_path=reg, battery_path=battery,
                            ci_path=os.path.join(tmp, "scripts", "ghost.sh"))
        hit = code == 2 and "NO-DATA" in lines[0]
        print("%-22s %s" % ("ci gate unreadable", "caught" if hit else "MISSED"))
        ok = ok and hit

        # An absent registry is NO-DATA, never a pass.
        os.remove(reg)
        code, lines = check(root=tmp, registry_path=reg, battery_path=battery)
        hit = code == 2 and "NO-DATA" in lines[0]
        print("%-22s %s" % ("absent registry", "caught" if hit else "MISSED"))
        ok = ok and hit
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true",
                    help="drive every refusal backwards over a fixture")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    code, lines = check()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
