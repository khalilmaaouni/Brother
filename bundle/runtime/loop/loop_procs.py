"""Which processes the loop owns: the one rule stop_loop.sh signals by (finding 1b, REVIEW-STOP-OWNERSHIP-2026-09-26).

The old stop pattern named `claude -p --model`, `codex exec -m`, `bin/or_ask.py` and `core.or_fanout` outright, so a stop
could signal any session's model CLI on this machine, the orchestrator's own OpenRouter calls included. Now, over ONE
snapshot of (pid, parent, process group, command):
  - a loop-only tool (RUNNERS_RX, DRIVER_RX) is selected by its name AND only when it belongs to a run of THIS loop: its
    program lives in this loop's tool directory (loop_bin(), $HOME/.claude/bin, where every run launches its tools
    from), or its own environment names a run under this loop's runs root (BROTHER_RUN_DIR, which the driver exports to
    everything it starts; the code root helpers, scripts/diag_round.py, run from outside the bin). 2026-09-29 18:00:
    the deadline stop read a test's fixture copy of unit_runner.py (under <HOME>/.claude/brother-scratch/straggler-*/bin)
    and the or_fanout it started as live runners, printed STOP INCOMPLETE, and the driver skipped the finisher;
  - a generic model process (GENERIC_RX) is selected only when its parent chain reaches a selected tool, or its process
    group is one a selected tool leads (the runner's child exited and the CLI was reparented to 1);
  - anything else, an unknown owner included, is never selected. A parent pid now held by an unrelated command (pid
    reuse) proves nothing, because the snapshot shows that command, not the tool that once held the number.
A shell whose command text merely names a tool is not the tool (2026-09-22: the orchestrator's `bash -c "... loop_until.sh"`
was counted as a live driver three times).
CLI: loop_procs.py <driver|runners|all> [--only TEXT] [--self PID]  prints "pid command" per owned process, exit 0;
exit 2 when the process table cannot be read, so the caller never reads "nothing owned" from a failed snapshot.
     loop_procs.py unit-runners  prints the number of live unit runners (exit 0), or exits 2 on an unreadable table.

NAMED LIMIT, NOT FIXED (SO28, review finding 1, ~/.claude/evidence/loop-remediation-0926/so24-deepseek-review.md):
a generic model CLI whose runner or driver has already died has no provable owner in this snapshot alone. Once the
runner's own pid is gone, ppid and pgid both age out (ppid becomes 1 or a reused pid, the pgid membership rule above
covers only a CLI reparented to 1 while its OWN process group still equals the runner's old pid, which does not help
once that number itself falls out of `roots`). The rule can only ever prove ownership FROM a live selected tool in
the SAME snapshot; a runner that is already gone leaves nothing this module can still call proof, and the CLI is
correctly left unselected rather than guessed at. CANDIDATE FIX, not built: stamp an environment marker on every
generic CLI this loop spawns (a `BROTHER_LOOP_OWNER=<run id>` env var), and read it back per pid via `ps -E` (or
`ps -o command,label=` equivalents where -E is unavailable) instead of relying on parent chain or process group
alone. Until that lands, an orphaned generic CLI is a known, accepted gap, not a silent one. run_dir_of() below reads
that marker today, for loop TOOLS outside the bin only; ps shows no environment for a platform binary (/bin/bash),
which is why a tool's location, not its environment, is the first evidence. or_fanout inherits BROTHER_RUN_DIR from
its runner, so the same read could close this gap later (follow-up, not built).

NAMED LIMITS OF THE RUN IDENTITY (adversarial review 2026-09-30, ~/.claude/evidence/stop-run-identity-0929/deepseek/):
  - HOME defines the loop: a stop run under another HOME (sudo, cron, another account) serves that HOME's loop and sees
    this one's tools as strangers. Every production stop runs as the account that started the loop.
  - a hand stop without the driver's BROTHER_RUNS_ROOT reads the default root, so a code root helper of a run under a
    CUSTOM root is a stranger to it; loop_until.sh and proof_pair.sh use the default root, and the driver's own stop
    inherits its export.
  - a stranger that INHERITS BROTHER_RUN_DIR under this loop's runs root (a test launched by a hermetic run that did
    not clear it) is owned; scripts/test_runner_straggler_settles.py clears every BROTHER_* variable for its fixtures,
    cut_preflight.runner_env() clears GIT_* only (follow-up).
  - `ps -E` is macOS ps; on a ps that rejects it every stop reads NO-DATA. This module ships in no public export
    (absent from origin/main, verified 2026-09-30).
"""
import os, re, subprocess, sys

DRIVER_RX = re.compile(r"bin/loop_until\.sh")
RUNNERS_RX = re.compile(r"bin/unit_runner\.py|bin/grade_lane\.sh|bin/probe_wave\.py|bin/check_wave\.py|bin/probe_build\.py"
                        r"|bin/grade_build\.py|(bin|scripts)/probe_round\.py|(bin|scripts)/diag_round\.py|bin/repair_wave\.py")
# THE LIVE RUNNER COUNT (X2 finding 7, 2026-09-27): loop_done.py and loop_pass.sh count unit runners by this same rule,
# the program position, never an argument; they read it through live_runner_count() and the `unit-runners` verb.
UNIT_RUNNER_RX = re.compile(r"bin/unit_runner\.py")
TOOLS_RX = re.compile(DRIVER_RX.pattern + "|" + RUNNERS_RX.pattern)
RUN_MARK = "BROTHER_RUN_DIR="
GENERIC_RX = re.compile(r"core\.or_fanout|or_ask\.py|claude -p\b|codex exec\b")
SHELL_RX = re.compile(r"^(/bin/|/usr/bin/)?(ba|z)?sh -c ")
SELF_RX = re.compile(r"stop_loop\.sh|loop_procs\.py")
# The interpreters the loop's tools run under (sys.executable shows as python3, python3.9 or the framework's Python;
# the .sh tools as bash or sh). A tool under any other program is not reached by a stop: add its interpreter here.
INTERPRETER_RX = re.compile(r"(^|/)(python[0-9.]*|Python|bash|sh|zsh|dash)$")
OPTION_WITH_VALUE = ("-W", "-X", "-o", "+o")   # python -W/-X and bash -o/+o take the next word as their value


def programs(command):
    """The words of command that name what it RUNS: the executable, and when that is an interpreter, its script (the
    first word that is not an option or an option's value; after -m that word is the module). -c is inline code and
    names no script.
    RR lane C, 2026-09-27: a tool was matched anywhere in the command line, so a viewer whose ARGUMENT was
    bin/unit_runner.py was selected and killed by a stop. An argument is data, never the program.
    NAMED LIMIT: ps prints argv joined by spaces, so a script path holding a space splits and is not matched."""
    words = command.split()
    if not words:
        return []
    out = [words[0]]
    if not INTERPRETER_RX.search(words[0]):
        return out
    i = 1
    while i < len(words):
        w = words[i]
        if w == "-c":
            break
        if w in OPTION_WITH_VALUE:
            i += 2; continue
        if w.startswith(("-", "+")):
            i += 1; continue
        out.append(w); break
    return out


def tool_word(command, rx):
    """The word of programs(command) naming a tool rx matches, or None."""
    return next((w for w in programs(command) if rx.search(w)), None)


def loop_bin():
    """This loop's tool directory. Every run launches its tools from ~/.claude/bin (runner_pool, diag_apply, grade_one.sh,
    probe_round and the driver's own stop all name it), so HOME, never where this file sits, decides which loop a stop
    serves: a checkout's copy of stop_loop.sh (loop_intake's advice) still stops the deployed loop."""
    return os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude", "bin"))


def runs_root():
    """Where this loop's run directories live: the default loop_until.sh and proof_pair.sh use, or BROTHER_RUNS_ROOT."""
    return os.path.realpath(os.environ.get("BROTHER_RUNS_ROOT") or os.path.join(os.path.expanduser("~"), ".claude", "evidence", "loop-runs"))


def in_loop_bin(word, bin_dir):
    """word names a program in this loop's bin. A RELATIVE word never does (adversarial review finding 1, 2026-09-30: it
    resolved against the STOP's own cwd, so a stranger's `python3 bin/unit_runner.py` was owned whenever the stop ran
    from $HOME/.claude). Every production launch names the bin absolutely (runner_pool, diag_apply: expanduser); a
    relative word falls to the run marker alone."""
    return os.path.isabs(word) and os.path.realpath(os.path.dirname(word)) == bin_dir


def owned(snap, rx, only="", self_pid=None, generic=True):
    """pids the loop owns in snap (dicts with pid, ppid, pgid, command, and run_dir where snapshot() read one). rx names
    the loop tools to start from, and it is matched against programs(command) only, never against an argument. A tool
    is a root only when it belongs to a run of this loop: in loop_bin(), or carrying a run_dir under runs_root(). The
    runs root itself, and a sibling that merely shares its prefix, name no run."""
    rx = re.compile(rx) if isinstance(rx, str) else rx
    by_pid = {r["pid"]: r for r in snap}
    ok = lambda r: r["pid"] != self_pid and not SHELL_RX.search(r["command"]) and not SELF_RX.search(r["command"])
    bin_dir, runs = loop_bin(), runs_root()

    def ours(r):
        w = tool_word(r["command"], rx)
        if w is None:
            return False
        run = r.get("run_dir") or ""
        return in_loop_bin(w, bin_dir) or (run != "" and os.path.realpath(run).startswith(runs + os.sep))
    roots = {r["pid"] for r in snap if ok(r) and ours(r) and (not only or only in r["command"])}
    if not generic:
        return roots
    out = set(roots)
    for r in snap:
        if r["pid"] in out or not ok(r) or not GENERIC_RX.search(r["command"]):
            continue
        # SO28 finding 3 (review 2026-09-26): r["pgid"] in roots names a root's
        # PID, never proof that root actually LEADS a group by that number. A
        # selected tool whose own pgid differs from its own pid does not lead
        # any group at all; group id r["pgid"] naming it is then a numeric
        # coincidence with some OTHER, unrelated group, and a generic CLI in
        # that group is not the tool's child. Confirmed by re-reading the
        # root's OWN row in this same snapshot: its pgid must equal its pid.
        root = by_pid.get(r["pgid"])
        if r["pgid"] in roots and root is not None and root["pgid"] == root["pid"]:
            out.add(r["pid"]); continue
        seen, p = set(), r["ppid"]
        while p in by_pid and p not in seen and p > 1:   # walk the parent chain inside this one snapshot
            if p in roots:
                out.add(r["pid"]); break
            seen.add(p); p = by_pid[p]["ppid"]
    return out


def snapshot(errors="strict"):
    """One `ps` read; None when it cannot be read (never an empty table standing in for a failed read), and None when
    ANY row cannot be read. RR lane C, 2026-09-27: a malformed row among valid ones was skipped, so the process behind
    it vanished from the table and stop_loop.sh printed STOPPED while that runner lived. A partial table is not a
    smaller truth; it is an unknown, and an unknown never reads as nothing owned.
    errors: how an undecodable byte is decoded. The stop (main) and the count both pass "replace", since one odd byte in
    an unrelated command line is not an unreadable table (loop_done's rule since finding 9); main then refuses, NO-DATA,
    only when a row it would signal holds a replaced byte. "strict" raises on any such byte."""
    try:
        # SO28 finding 7: -ww asks for unlimited width, so a long command is
        # never truncated here differently than stop_loop.sh's own re-check
        # (which asks for it too), the exact mismatch that broke the
        # same-command comparison on a command long enough to hit either
        # ps's own truncation.
        r = subprocess.run(["ps", "-ax", "-ww", "-o", "pid=,ppid=,pgid=,command="],
                           capture_output=True, text=True, errors=errors, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    rows = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        f = line.split(None, 3)
        if not (len(f) == 4 and f[0].isdigit() and f[1].isdigit() and f[2].isdigit()):
            return None
        rows.append(dict(pid=int(f[0]), ppid=int(f[1]), pgid=int(f[2]), command=f[3].strip()))
    # A loop tool outside the loop's bin belongs to a run only by the run its environment names (owned()); read that for
    # each such row now, in this same read. One that cannot be read makes the whole table unknown, as a bad row does.
    bin_dir = loop_bin()
    for row in rows:
        w = tool_word(row["command"], TOOLS_RX)
        if w is None or in_loop_bin(w, bin_dir):
            continue
        row["run_dir"] = run_dir_of(row["pid"], row["command"])
        if row["run_dir"] is None:
            return None
    return rows or None


def run_dir_of(pid, command):
    """The BROTHER_RUN_DIR in pid's environment, the run that started it. "" when it carries none, when ps shows no
    environment for it (a platform binary such as /bin/bash: every shell tool of the loop lives in its bin, so none is
    judged by this), when pid has exited, or when it now runs another command (the row's process is gone). None when
    ps could not be read for it, never "no run": a stop then refuses instead of leaving a runner unsignalled.
    The environment is decoded with replace, always (adversarial review finding 10, 2026-09-30: decoded with the stop's
    strict mode, one invalid byte in a STRANGER's environment raised, and the whole stop read NO-DATA, the finisher
    skipped: the incident's consequence again). Only the BROTHER_RUN_DIR token is read here, and the command prefix
    was already decoded strictly by the table read, so a replaced byte can neither forge a run nor hide the command.
    NAMED LIMIT: ps prints the environment joined by spaces after the arguments, so a run directory holding a space is
    not read, and the tool is a stranger."""
    try:
        r = subprocess.run(["ps", "-E", "-ww", "-o", "command=", "-p", str(pid)],
                           capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    if r.returncode != 0:
        return "" if not (out or r.stderr.strip()) else None   # gone: nothing on any stream (alive_same's rule)
    if not out.startswith(command):
        return ""
    return next((w[len(RUN_MARK):] for w in out[len(command):].split() if w.startswith(RUN_MARK)), "")


def live_runner_count(snap=None):
    """How many unit runners are alive, by the ownership rule a stop uses; None when the table cannot be read, which
    every caller reads as NO-DATA, never as zero."""
    snap = snapshot(errors="replace") if snap is None else snap
    return None if snap is None else len(owned(snap, UNIT_RUNNER_RX, generic=False))


def main(argv):
    kinds = {"driver": (DRIVER_RX, False), "runners": (RUNNERS_RX, True),
             "all": (TOOLS_RX, True)}
    if argv[:1] == ["unit-runners"]:     # the live runner count loop_pass.sh prints; exit 2 when the table is unreadable
        n = live_runner_count()
        if n is None:
            print("NO-DATA: the process table could not be read", file=sys.stderr); return 2
        print(n); return 0
    if not argv or argv[0] not in kinds:
        print("usage: loop_procs.py <driver|runners|all|unit-runners> [--only TEXT] [--self PID]", file=sys.stderr); return 2
    only, self_pid = "", None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1] if argv.index("--only") + 1 < len(argv) else ""
    if "--self" in argv and argv.index("--self") + 1 < len(argv) and argv[argv.index("--self") + 1].isdigit():
        self_pid = int(argv[argv.index("--self") + 1])
    # REPLACE, THEN REFUSE ONLY WHAT THE STOP WOULD SIGNAL (attack on 65ffa97b4, finding 4, 2026-09-30): a strict read
    # raised on one byte that is not UTF-8 in ANY command line, so a stranger could turn every stop into NO-DATA, and
    # the driver then skips its finisher (the 2026-09-29 incident's consequence). A replaced byte in a row the stop would
    # signal still refuses: stop_loop.sh re-checks each pid by comparing its command text, which never matches a
    # replaced byte, so that process would be left alive under a STOPPED line.
    snap = snapshot(errors="replace")
    if snap is None:
        print("NO-DATA: the process table could not be read", file=sys.stderr); return 2
    rx, generic = kinds[argv[0]]
    cmd = {r["pid"]: r["command"] for r in snap}
    mine = sorted(owned(snap, rx, only=only, self_pid=self_pid, generic=generic))
    odd = [p for p in mine if "\ufffd" in cmd[p]]
    if odd:
        print("NO-DATA: %d process(es) of the loop carry a byte that is not UTF-8 in their command line (pid %s); "
              "the stop's same-command re-check cannot match them" % (len(odd), ", ".join(map(str, odd[:5]))), file=sys.stderr)
        return 2
    for p in mine:
        print("%d %s" % (p, cmd[p]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
