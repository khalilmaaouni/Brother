#!/usr/bin/env python3
"""The loop's plan E switches, read in one place (2026-10-01). Standard library only, no side effect on import.

Each automatic stage plan E removed from the default path is OFF unless its variable says exactly "on". Unset, empty,
unknown or misspelt values read OFF: an unproven stage that runs by accident spends money and time and decides nothing.
"""
import os, re


def probes_on(env=None):
    """True only when BROTHER_PROBES is exactly "on" (case blind). Over every build on this machine that reached landing,
    a CLEAN probe verdict was followed by a landing refusal 136 times in 202 and a DIRTY one 35 times in 66: the
    verdict did not predict the outcome, so automatic probe generation is off by default."""
    e = os.environ if env is None else env
    return (e.get("BROTHER_PROBES") or "").strip().lower() == "on"


def on(name, env=None):
    """True only when the plan E switch <name> is exactly the string "on" (review 2026-10-03: no case folding and no
    trimming, so the documented spelling is the only spelling). The switches and what each one
    turns back on: BROTHER_FINISHER (the detached post run finisher), BROTHER_SALVAGE (promoting an old PASS to READY
    without regrading), BROTHER_TUNING (history based sizing between passes), BROTHER_LOOP_TOKEN (native seats run
    on the Keychain login brother-login saved, and the seat sandbox shuts the Keychain; 2026-10-03). Every one is
    OFF unless asked for. BROTHER_CLAUDE_NATIVE is not one of them: it is standard, see native_on."""
    if name not in SWITCHES:
        raise ValueError("unknown plan E switch: %r" % (name,))
    e = os.environ if env is None else env
    return e.get(name) == "on"


SWITCHES = ("BROTHER_PROBES", "BROTHER_FINISHER", "BROTHER_SALVAGE", "BROTHER_TUNING", "BROTHER_LOOP_TOKEN")


def native_on(env=None, which=None):
    """True when a round whose every arm is on the claude transport runs Claude native sessions (native_worker.py).
    STANDARD since 2026-10-02 (owner: "make native standard with a longer cap", after a 2 h native run landed 6 builds
    where blind Claude rounds had landed 0.52 to 0.94 an hour): on unless BROTHER_CLAUDE_NATIVE is exactly "off" (case
    blind, trimmed), and never on a machine without sandbox-exec (macOS only), where every session would end NO-DATA.
    which: shutil.which or a stand in for tests."""
    import shutil
    e = os.environ if env is None else env
    if (e.get("BROTHER_CLAUDE_NATIVE") or "").strip().lower() == "off":
        return False
    return bool((which or shutil.which)("sandbox-exec", path=e.get("PATH")))


if __name__ == "__main__":
    import sys
    # usage: loop_switches.py <SWITCH>   exits 0 when it is on, 1 when it is off, 2 for an unknown name (shell callers)
    if len(sys.argv) == 2:
        try:
            sys.exit(0 if on(sys.argv[1]) else 1)
        except ValueError as exc:
            print(exc); sys.exit(2)
    print(" ".join("%s=%s" % (n, "on" if on(n) else "off") for n in SWITCHES)
          + " BROTHER_CLAUDE_NATIVE=%s" % ("on" if native_on() else "off"))


#: The run's own choices (model routing, scope, deadlines). A test judging code must never inherit them: on 2026-10-02 a
#: Claude only run (BROTHER_TRANSPORTS=claude) failed FX-11's done check, whose selftests route a bridge model, and every
#: landing suite that touches routing ran under the same refusal. One definition; suite_env and the close both use it.
RUN_KNOBS = frozenset(("BROTHER_TRANSPORTS", "BROTHER_PIN_MODEL", "BROTHER_ADVERSARY_MODEL", "BROTHER_BUILD_PLAN_MODEL",
                       "BROTHER_REPAIR_ADVISOR_MODEL", "BROTHER_FINISHER_MODEL", "BROTHER_DIAG_MODEL", "BROTHER_CHECKER",
                       "BROTHER_CHECKER_MODE", "BROTHER_WORKER_MIX", "BROTHER_SCOPE", "BROTHER_DEADLINE_CAP_S",
                       "FANOUT_TIMEOUT_S", "BROTHER_CLAUDE_EFFORT", "BROTHER_CLAUDE_NATIVE", "BROTHER_NATIVE_SEATS", "BROTHER_NATIVE_SESSION_S", "BROTHER_NATIVE_BUDGET_USD",
                       "BROTHER_PROGRAM_RECORD",   # the run's proven programs: a model_call selftest read the live record (2026-10-02)
                       "BROTHER_EXPECTED_UPSTREAM"))   # the pair's pinned destination: a lander suite's fixture main() read it (review 2026-10-05)


def drop_run_knobs(env):
    """env without the run's own choices (RUN_KNOBS); everything else, program pins and state roots included, is kept."""
    return {k: v for k, v in env.items() if k not in RUN_KNOBS}


#: Credential-shaped variable NAMES, the one rule (grade_build re-exports it): no child that runs a build's code inherits
#: one (2026-10-02, spec gate builder's report: suite_env passed them into every sandboxed suite).
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.I)


def suite_env(base, home):
    """The environment a model written suite runs under at landing: a fresh HOME and TMPDIR (grade_build.run does the
    same in the sandbox; the landing rerun used this machine's HOME until 2026-09-24, security review), no run
    directory, no git location variables. Lives here, a library, so land_apply (a script) and land_batch's sandboxed
    neighbour runs share ONE definition without importing a script (2026-10-02)."""
    # the state roots live under the suite's HOME, as grade_build.run puts them inside its sandbox (2026-10-03: the run's
    # own money root, outside the sandbox's write roots, turned L5a-7's green build into "Operation not permitted" x21)
    env = dict(drop_run_knobs(base), PYTHONDONTWRITEBYTECODE="1", HOME=home, TMPDIR=os.path.join(home, "tmp"),
               BROTHER_OR_STATE_ROOT=os.path.join(home, "or-state"), BROTHER_JEV_STATE_DIR=os.path.join(home, "jev-state"))
    env.pop("BROTHER_RUN_DIR", None)
    for k in [k for k in env if k.startswith("GIT_") or SECRET_ENV.search(k)]: env.pop(k)  # git location variables (its git config calls would land in the live repo) and credential-shaped names
    return env
