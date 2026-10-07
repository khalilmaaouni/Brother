#!/usr/bin/env python3
"""The Claude native worker (owner order 2026-10-02: "The way you give briefing and tasks to Claude cannot be the same as
deepseek", then "wire the Claude-native worker into the loop"). A blind worker is handed a 150 KB paste and must answer
with exact find strings; a native worker is a Claude Code session with real tools (Read, Edit, Write, Grep, Glob, Bash)
in a checkout of the tree the grader grades, briefed with the sub unit's own spec section and the grader's contract. It
runs its own tests, then native_adapter turns the checkout into the build JSON the grader already accepts, so grading,
probes and landing are unchanged.

usage: native_worker.py <jobs.json> --repo DIR --base REV --timeout S [--seats N] [--results FILE]
       native_worker.py refresh      one plain unsandboxed call that lets the CLI refresh this machine's login
       native_worker.py seat-probe   one real minimal call through a scratch seat (the intake's seat proof)
  jobs: [{"id", "model" (a registry name on the claude transport), "prompt_file", "out"}]. Each job waits for a free SEAT
  (BROTHER_NATIVE_SEATS, default 2, one machine wide file lock each: a seat is a whole agent session running tests, so
  seats bound the machine, never the number of jobs), resets that seat's worktree to REV, runs one session for at most
  S seconds under sandbox-exec (writes only to the seat and Claude's own state, credential folders
  unreadable, the user's settings and hooks not loaded; the profile is PROFILE, inline), and writes the build to the job's "out", or nothing.
  Every session is a row in the Claude call ledger (claude_ledger START before the child, terminal row in a finally).
  Exit 0 when every job wrote a build, 1 otherwise; --results gets one row per job.
Footprint: one worktree per seat, reused and reset (checkout -f, clean -fdx), never a checkout per call. refresh writes only its two ledger rows; seat-probe one scratch seat folder, removed after the call.
Test: python3 -B scripts/loop/test_native_worker.py
"""
import contextlib, fcntl, hashlib, json, os, re, shutil, stat, subprocess, sys, tempfile, threading, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import native_adapter as NA  # noqa: E402
import model_router as R  # noqa: E402
import model_call as MC  # noqa: E402
import claude_ledger as CL  # noqa: E402
import grade_build as G  # noqa: E402  SECRET_ENV: the one credential-shaped name rule, shared with the grader
import loop_switches as SW  # noqa: E402
import brother_login as BL  # noqa: E402  the loop's own Claude login, read on the token lane only

#: The sandbox, inline (sandbox-exec -p) so it ships inside this module and can never go missing beside it: writes only to
#: the seat worktree, its temp folder, the Claude state the CLI needs and the system temp; credential folders unreadable.
#: Params WT, TMP, CLAUDE_STATE, LIBRARY come from -D. Proven by the prototype trials of 2026-10-02 (D2.5, RL3.a, R4.3).
PROFILE = r"""(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath (param "WT")) (subpath (param "TMP")) (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/") (regex #"^/private/var/folders/"))
(deny file-read* (regex #"^/Users/[^/]+/\.(ssh|aws|gnupg|netrc|docker|kube)(/|$)") (regex #"^/Users/[^/]+/\.brothersbe-private-names$"))
(deny file-write* (subpath (string-append (param "HOME") "/.claude/bin")) (subpath (string-append (param "HOME") "/.claude/hooks"))
 (subpath (string-append (param "HOME") "/.claude/rules")) (subpath (string-append (param "HOME") "/.claude/skills"))
 (subpath (string-append (param "HOME") "/.claude/plugins")) (subpath (string-append (param "HOME") "/.claude/agents"))
 (subpath (string-append (param "HOME") "/.claude/commands")) (literal (string-append (param "HOME") "/.claude/settings.json"))
 (literal (string-append (param "HOME") "/.claude/settings.local.json")) (literal (string-append (param "HOME") "/.claude/CLAUDE.md"))
 (literal (string-append (param "HOME") "/.claude/spend-guard.json")) (subpath (string-append (param "HOME") "/Library/LaunchAgents")))
(deny file-read* (subpath (string-append (param "HOME") "/.config/gh")) (literal (string-append (param "HOME") "/.git-credentials")))
(deny process-exec (subpath (string-append (param "HOME") "/.claude/bin")))
(deny process-exec (regex #"/git-credential-osxkeychain$") (regex #"/gh$"))
(allow file-write* (subpath (param "TOOLDIR")) (regex #"^/private/tmp/claude-[0-9a-f]+-cwd$"))
(deny network-outbound (regex #"^/private/tmp/com\.apple\.launchd\.[^/]+/Listeners$"))
(deny file-read-data (subpath (param "HOME")))
(allow file-read-data (subpath (param "WT")) (subpath (param "TMP")) (subpath (param "TOOLDIR"))
 (subpath (string-append (param "HOME") "/Library/Application Support/Claude/claude-code"))
 (subpath (string-append (param "HOME") "/Library/Keychains")) (subpath (string-append (param "HOME") "/.local"))
 (literal (string-append (param "HOME") "/.claude.json")) (subpath (param "COMMON"))
 (literal (string-append (param "HOME") "/.gitconfig")) (literal (string-append (param "HOME") "/.config/git/config")) (literal (string-append (param "HOME") "/.config/git/ignore")) (literal (string-append (param "HOME") "/.config/git/attributes"))
(subpath (string-append (param "HOME") "/.nvm")) (subpath (string-append (param "HOME") "/.volta")) (subpath (string-append (param "HOME") "/.fnm")) (subpath (string-append (param "HOME") "/.bun")) (subpath (string-append (param "HOME") "/.pyenv")) (subpath (string-append (param "HOME") "/.cargo")) (subpath (string-append (param "HOME") "/.rustup")) (subpath (string-append (param "HOME") "/.conda")) (subpath (string-append (param "HOME") "/miniconda3")) (subpath (string-append (param "HOME") "/anaconda3")) (subpath (string-append (param "HOME") "/go")) (subpath (string-append (param "HOME") "/.npm-global")))
(deny file-read-data (literal (string-append (param "HOME") "/.cargo/credentials.toml")) (literal (string-append (param "HOME") "/.cargo/credentials")) (literal (string-append (param "HOME") "/.npm-global/etc/npmrc")) (literal (string-append (param "HOME") "/.config/git/credentials")) (literal (string-append (param "HOME") "/.conda/.condarc")) (subpath (string-append (param "HOME") "/.local/share/keyrings")) (subpath (string-append (param "HOME") "/.local/share/gh")))
(deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript") (literal "/usr/bin/lsappinfo") (literal "/usr/bin/shortcuts") (literal "/usr/bin/automator"))
(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd") (global-name "com.apple.coreservices.quarantine-resolver")
 (global-name-regex #"^com\.apple\.lsd\.") (global-name "com.apple.coreservices.appleevents") (global-name "com.apple.coreservices.appleevents.aeserver")
 (global-name "com.apple.coreservices.uiagent"))"""
#: THE LOOP TOKEN LANE SHUTS THE KEYCHAIN (D10 of docs/decisions/native-run-2026-10-02.json, closed 2026-10-03): with
#: BROTHER_LOOP_TOKEN=on the session runs on the login brother-login saved, handed to that one child on a pipe (see
#: below), so the seat no longer needs /usr/bin/security or securityd (measured 2026-10-03: a token on the descriptor
#: reaches the API under these denies, a fake one answers 401; without one the CLI says "Not logged in", as D10 recorded).
#: So these lines are appended ONLY on that lane: the Keychain tool cannot run, the Keychain daemon cannot be asked,
#: and the keychain files cannot be read (same op as the allow above, so the later deny wins). Off, D10's time boxed
#: exposure stands exactly as before. The login item itself is written by the loop's own reader (brother_login.py),
#: so /usr/bin/security does not open it silently on any lane.
#: THE LOGIN NEVER SITS IN AN ENVIRONMENT (review 2026-10-03, F1, reproduced: from inside the token lane profile, sysctl
#: KERN_PROCARGS2 on the running claude returned its whole environment, the login included; seatbelt has no operation
#: that denies that call, measured: process-info*, sysctl-read and a deny of every sysctl all left it readable). So the
#: login goes to the child on a pipe, named by CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR (brother_login.FD_VAR), which the
#: CLI reads once at start; the pipe then reads empty, and the environment carries only a descriptor number.
#: THE CLI HANDS ITS OWN ENVIRONMENT TO ITS TOOL CHILDREN unless CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is truthy (review
#: 2026-10-03, read in the CLI and proven live: a Bash tool child printed a marker variable without it and nothing
#: with it). On the token lane that variable is set in the child's environment, and because the CLI then forces the
#: permission mode to default, the session's tools are declared with --allowedTools so they still run.
TOKEN_DENY = r"""
(deny process-exec (subpath (param "HOME")))
(allow process-exec (subpath (param "WT")) (subpath (param "TMP")) (subpath (param "TOOLDIR")) (subpath (param "COMMON"))
 (subpath (string-append (param "HOME") "/Library/Application Support/Claude/claude-code")) (subpath (string-append (param "HOME") "/.local"))
 (subpath (string-append (param "HOME") "/.nvm")) (subpath (string-append (param "HOME") "/.volta")) (subpath (string-append (param "HOME") "/.fnm")) (subpath (string-append (param "HOME") "/.bun")) (subpath (string-append (param "HOME") "/.pyenv")) (subpath (string-append (param "HOME") "/.cargo")) (subpath (string-append (param "HOME") "/.rustup")) (subpath (string-append (param "HOME") "/.conda")) (subpath (string-append (param "HOME") "/miniconda3")) (subpath (string-append (param "HOME") "/anaconda3")) (subpath (string-append (param "HOME") "/go")) (subpath (string-append (param "HOME") "/.npm-global")))
(deny process-exec (regex #"/git-credential-osxkeychain$") (regex #"/gh$"))
(deny process-exec (literal "/usr/bin/security"))
(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd"))
(deny file-read-data (subpath (string-append (param "HOME") "/Library/Keychains")))"""
#: A READ DENY DOES NOT STOP EXEC (review 2026-10-03, F2, reproduced: a seat on the off lane ran a program stored in an
#: unreadable HOME folder, the loop's Keychain reader among them, and read the login silently). ~/.claude/bin, where the
#: reader lives, is unrunnable on BOTH lanes (PROFILE). On the TOKEN lane alone (TOKEN_DENY) running any program under
#: HOME is denied except where reading it is allowed (the seat, its temp and tool folder, the git dir, the Claude
#: program's folder, ~/.local and TOOL_ROOTS): review 4 (2026-10-03) measured that deny breaking off lane seat tools under
#: ~/.asdf, ~/.rbenv, ~/.deno, ~/Library/Python and ~/bin, so the off lane runs them as before. The git credential
#: helper and gh are denied again after the token lane's allow, wherever they live.
#: NO APP LAUNCH, NO APPLE EVENTS (review 4, F-A, CRITICAL, reproduced): a seat wrote a two file .app into its worktree
#: and ran open -g -n on it; LaunchServices started it OUTSIDE the sandbox, where it ran the reader and wrote the login
#: into the worktree. On both lanes the front ends (open, osascript, lsappinfo, shortcuts, automator) cannot run, and
#: the services the launch needs are refused. Measured 2026-10-03 on Darwin 27: denying launchservicesd alone, or
#: quarantine-resolver alone, or com.apple.lsd.* still launched a re-signed copy of open; launchservicesd AND
#: quarantine-resolver together refused it (-10827). The rest of the list (lsd.*, appleevents, aeserver, uiagent) is
#: refused for the routes not proven here (Apple Events to a running app). Measured with it: the real CLI, python3, git
#: and node still run in a seat on the off lane, and the CLI reaches the API on the token lane. launchctl submit was
#: already refused (rc 1, nothing ran).
#: The CLI's Bash tool also writes /private/tmp/claude-<hex>-cwd to track its directory (measured: exit 1 on every call
#: without it). The name is random per process, so the pattern also matches other sessions' tracking files; low impact.
#: It still reports ~/.bash_profile as not permitted: harmless, and kept shut because shell profiles export keys.
#: CREDENTIAL STORES INSIDE AN OPENED ROOT STAY SHUT (fifth review 2026-10-02, reproduced: ~/.config/git/credentials,
#: ~/.cargo/credentials.toml and ~/.npm-global/etc/npmrc were readable once their folders were opened): ~/.config/git is
#: opened file by file (config, ignore, attributes), and CREDENTIAL_STORES are denied after the allow with the SAME operation,
#: file-read-data (measured: a later deny file-read* does not override an allow file-read-data; only the same op wins).
#: WHERE TOOLS LIVE STAYS OPEN, THE REST OF HOME STAYS SHUT (fourth review 2026-10-02, decision D9 under the owner's
#: delegation): a locked home killed every claude, node or python installed in it, and an unreadable ~/.gitconfig took
#: git's identity, so the repository's own committing tests went red in a seat. ~/.gitconfig and ~/.config/git/{config,ignore,attributes} are read
#: (identity, ignores), and so are the standard tool roots in TOOL_ROOTS. Documents, other projects and client data stay
#: unreadable, because the network stays open. A Claude program installed elsewhere in HOME is refused BEFORE the session
#: (CONFIG_WAIT, its path named), never killed silently and re-seated forever.
#: NO SECRET LEAVES A SEAT (second review 2026-10-02, reproduced: ~/.gitconfig, ~/.npmrc and a token file were readable
#: with the network open). The last block denies reading any file's contents under HOME, then allows back only what a
#: session needs, each measured live: its seat and temp, the Claude program and its state (the same login the session
#: runs under), the keychain folder (each item keeps its own access list, so another program's token still asks that
#: program), and ~/.local (python3). TOOLDIR is the one folder the CLI's Bash tool writes outside the seat,
#: /private/tmp/claude-<uid>/<seat path with / and . as ->, created before the session; other sessions' folders there
#: stay unwritable. The ssh agent's socket is unreachable and SSH_AUTH_SOCK is removed.
#: THE SANDBOX GUARDS WHAT RUNS IT (Fable review 2026-10-02 13:1x): the session runs with bypassPermissions and no user
#: hooks, and the first profile let it write anywhere under ~/.claude and ~/Library, the installed loop in ~/.claude/bin
#: among them. The last block above denies the installed loop, the hooks, rules, skills, plugins, settings, CLAUDE.md,
#: the spend guard and LaunchAgents (a later SBPL rule wins over the earlier allow), and the GitHub CLI token and git
#: credential files. Session state (projects, shell snapshots, evidence) stays writable.
#: NO PUSH FROM A SEAT: every https, ssh and git@ push URL is rewritten to a target that cannot exist, and the credential
#: helper is emptied, so neither git nor the keychain can carry a push; GH_TOKEN and GITHUB_TOKEN are removed.
PUSH_BLOCK = ["GIT_CONFIG_COUNT=5",
              "GIT_CONFIG_KEY_0=url.file:///dev/null/push-blocked/.pushInsteadOf", "GIT_CONFIG_VALUE_0=https://",
              "GIT_CONFIG_KEY_1=url.file:///dev/null/push-blocked/.pushInsteadOf", "GIT_CONFIG_VALUE_1=git@",
              "GIT_CONFIG_KEY_2=url.file:///dev/null/push-blocked/.pushInsteadOf", "GIT_CONFIG_VALUE_2=ssh://",
              "GIT_CONFIG_KEY_3=credential.helper", "GIT_CONFIG_VALUE_3=",
              "GIT_CONFIG_KEY_4=core.excludesFile", "GIT_CONFIG_VALUE_4=/dev/null"]
CREDENTIAL_STORES = ('/.cargo/credentials.toml', '/.cargo/credentials', '/.npm-global/etc/npmrc', '/.config/git/credentials', '/.conda/.condarc', '/.local/share/keyrings', '/.local/share/gh')   # denied even inside an opened root
TOOL_ROOTS = ('.nvm', '.volta', '.fnm', '.bun', '.pyenv', '.cargo', '.rustup', '.conda', 'miniconda3', 'anaconda3', 'go', '.npm-global')   # read and run inside a seat
TOOLS = "Read,Edit,Write,Grep,Glob,Bash"
SCRUB_VAR = "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"
DEFAULT_SEATS = 2
#: A FAULT OF THIS MACHINE'S SETUP, not of Claude (second review 2026-10-02): a logged out CLI, a disabled permission
#: mode, a sandbox profile the system refuses, a missing program. Every later call would fail the same way, so the round
#: ends CONFIG_WAIT (parked, the cause named) instead of re-seating forever as a Claude outage.
HOST_FAULT_RE = re.compile(r"not logged in|/login|invalid api key|bypasspermissions mode is disabled"
                           r"|sandbox-exec:|profile parse error|^env: [^\n]*no such file or directory|claude: command not found"
                           r"|unknown option|argument [^\n]* is invalid|credit balance|has been disabled", re.I | re.M)
#: A rejected login (measured 2026-10-03 with a synthetic token: "Failed to authenticate. API Error: 401 OAuth access
#: token is invalid"): on the token lane the round parks and names brother-login, never re-seats on a dead token.
LOGIN_FAULT_RE = re.compile(r"oauth access token is invalid|failed to authenticate|not logged in", re.I)
CLAUDE_ERROR = "NO-DATA claude error"   # every Claude side failure run() reports starts with these words
SEAT_WAIT = "NO-DATA no native seat free"   # and every session that never got a seat with these
#: One native session's cap, seconds (owner 2026-10-02, "a longer cap"): 1.5 times the 1800 s call timeout FX-13.4's
#: session died at. Longer is not free: a proof run refuses a call that would outlive its deadline, so no session starts
#: in a run's last DEFAULT_SESSION_S. BROTHER_NATIVE_SESSION_S moves it within SESSION_RANGE.
DEFAULT_SESSION_S = 2700
SESSION_RANGE = (600, 7200)


def spec_section(spec_text, sub):
    """The heading of sub unit `sub` and everything under it, up to the next heading of the same or a higher level, or
    None when no heading names it (the caller refuses: a brief with no spec section asks a worker to guess)."""
    if not isinstance(spec_text, str) or not isinstance(sub, str) or not sub:
        return None
    lines, out, level = spec_text.splitlines(True), None, 0
    head = re.compile(r"^(#{2,6})\s+%s(?![\w.])" % re.escape(sub))
    for line in lines:
        m = re.match(r"^(#{1,6})\s", line)
        if out is None:
            h = head.match(line)
            if h:
                out, level = [line], len(h.group(1))
            continue
        if m and len(m.group(1)) <= level:
            break
        out.append(line)
    return "".join(out) if out else None


def brief(sub, section, contract, note=""):
    """The whole brief a native session gets: how to work, what to leave in .brother/, the grader's contract, the
    grader's lines about the previous round (note), and the spec section. Never the blind worker's file paste."""
    repair = ("\nWHAT THE GRADER SAID ABOUT THE PREVIOUS ROUND (fix exactly these):\n%s\n" % note) if note else ""
    return """You are working in a git checkout of the repository. Implement sub unit %s exactly as its specification below says.

How to work:
- Read the files the specification names yourself (Read, Grep, Glob). Change only the files it says you own.
- Before changing any function, grep every caller and keep each one working; list them in notes.json.
- Find every EXISTING test that loads a file you change (grep -rlE '<module name>' --include='test_*.py' scripts plugin
  products) and run each one: all must stay green, or the landing refuses your build as a red neighbour. Some suites
  copy a module beside hand written stubs of the modules it calls (for example STUBS in
  scripts/test_runner_straggler_settles.py): when your change calls a function a stub does not offer, add it to the stub.
- Write the tests it asks for, beside the module they test. Run them with python3 and with /usr/bin/python3 (Python 3.9)
  until they pass, and make sure each new test FAILS when your change is removed.
- Do not commit, do not touch git history, do not use the network, do not delete or rename files, do not edit anything
  under .brother/ except the three files below.
- The git index is read only here: git add, commit, stash and checkout -- fail by design. To prove a test fails
  without your change: git diff > .tmp/change.diff, git apply -R .tmp/change.diff, run it, git apply .tmp/change.diff.
- Your home folder is read only and mostly unreadable: a test builds its own HOME in a temp folder and never writes
  under ~/.claude. The Bash tool printing that ~/.bash_profile is not permitted is expected and harmless.

When the work passes its tests AND every mutation check is done and its file restored, write exactly these files,
as your LAST action (a session cut at its cap is graded on what is on disk):
- .brother/done_check.txt: ONE command, run from the repository root, that runs the tests proving this sub unit.
- .brother/mutations.json: a JSON list of 4 or more objects {"name", "path", "find", "replace", "caught_by"}. Each one
  breaks one guard your change adds; "find" must occur EXACTLY ONCE in that file after your edits; "caught_by" names the
  test that fails when it is applied. Check each one by applying it, running the test, and restoring the file.
- .brother/notes.json: {"callers_checked": [files you checked that call what you changed], "unknowns": [anything the
  specification left open]}.
Then reply with one line: DONE, or BLOCKED followed by the reason.

GRADER CONTRACT (the grader that judges your result enforces every line below; they were written for a builder that
returns JSON, so wherever they describe the JSON, apply them to the files and the .brother/ files you write instead)
%s
%s
SPECIFICATION
%s""" % (sub, contract, repair, section)


def contract_text(runners=()):
    """brief_head.md, the landing fuzz's own hostile input contract and the grader's own generated screen, so the native
    brief, the landing fuzz and the grader cannot drift apart."""
    import grade_build as G
    import land_apply as LA
    with open(os.path.join(HERE, "brief_head.md"), encoding="utf-8") as fh:
        head = fh.read()
    return head + "\n" + LA.hostile_contract() + "\n" + G.screen_rules(list(runners))


def seats(env=None):
    """BROTHER_NATIVE_SEATS as a positive int; unset means DEFAULT_SEATS; anything else refuses (ValueError)."""
    raw = str((os.environ if env is None else env).get("BROTHER_NATIVE_SEATS", "")).strip()
    if not raw:
        return DEFAULT_SEATS
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError("BROTHER_NATIVE_SEATS must be a positive whole number, not %r" % raw)
    return int(raw)


def session_seconds(env=None):
    """BROTHER_NATIVE_SESSION_S as whole seconds within SESSION_RANGE; unset means DEFAULT_SESSION_S; anything else
    refuses (ValueError): a cap nobody can read is never quietly replaced by another."""
    raw = str((os.environ if env is None else env).get("BROTHER_NATIVE_SESSION_S", "")).strip()
    if not raw:
        return DEFAULT_SESSION_S
    if not raw.isdigit() or not SESSION_RANGE[0] <= int(raw) <= SESSION_RANGE[1]:
        raise ValueError("BROTHER_NATIVE_SESSION_S must be whole seconds from %d to %d, not %r" % (SESSION_RANGE + (raw,)))
    return int(raw)


#: An after turn spending limit per native session (2026-10-03, the delegated Sonnet trial): BROTHER_NATIVE_BUDGET_USD, read
#: from the runner's own environment like the session cap. Unset: no flag, the behaviour before this line. The CLI checks
#: it after a turn, so a session can overshoot it by one turn; the session then ends normally WITH its cost record, while
#: a timeout or kill still ends uncosted (NO-DATA), which this limit does not and cannot change.
BUDGET_RANGE = (0.05, 100.0)


def budget_usd(env=None):
    """The --max-budget-usd value as text, or None when BROTHER_NATIVE_BUDGET_USD is unset; a value that is not a plain
    decimal inside BUDGET_RANGE refuses (ValueError): an unreadable limit is never quietly replaced by none."""
    raw = str((os.environ if env is None else env).get("BROTHER_NATIVE_BUDGET_USD", "")).strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        v = None
    if v is None or raw.lower() in ("nan", "inf", "-inf", "infinity") or not BUDGET_RANGE[0] <= v <= BUDGET_RANGE[1]:
        raise ValueError("BROTHER_NATIVE_BUDGET_USD must be a number from %g to %g, not %r" % (BUDGET_RANGE + (raw,)))
    return raw


def seat_root():
    return os.path.expanduser(os.environ.get("BROTHER_NATIVE_SEAT_ROOT") or "~/.claude/brother-scratch/native-seats")


@contextlib.contextmanager
def seat(n, wait_s):
    """One of n machine wide seats, held by a file lock for the whole session; yields its index, or raises TimeoutError
    when none frees within wait_s (NO-DATA for that job, never a pass). A crashed holder frees its seat with its lock."""
    root = seat_root()
    os.makedirs(root, exist_ok=True)
    deadline, held = time.monotonic() + wait_s, None
    while held is None:
        for i in range(n):
            fh = open(os.path.join(root, "seat-%d.lock" % i), "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = (i, fh)
                break
            except OSError:
                fh.close()
        if held is None:
            if time.monotonic() > deadline:
                raise TimeoutError("%s within %d s" % (SEAT_WAIT[len("NO-DATA "):], wait_s))
            time.sleep(2)
    try:
        yield held[0]
    finally:
        held[1].close()


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(NA.SAFE_GIT + list(args), cwd=cwd, capture_output=True, text=True, env=env)


def _common(repo):
    """The repository's common git directory, resolved, or "" when git cannot say."""
    try:
        r = _git(repo, "rev-parse", "--git-common-dir")
    except OSError:   # a missing or unreadable folder: unknown, never a seat
        return ""
    return os.path.realpath(os.path.join(repo, r.stdout.strip())) if r.returncode == 0 and r.stdout.strip() else ""


def seat_dir(repo, i):
    """Seat i's worktree for THIS repository (Fable review 2026-10-02: seats keyed by index alone made a second project
    on the same Mac fail every call), or None when the repository cannot be read. The seat LOCK stays machine wide."""
    common = _common(repo)
    if not common:
        return None
    return os.path.join(seat_root(), hashlib.sha256(common.encode()).hexdigest()[:8], "seat-%d" % i)


def seat_link_ok(repo, wt):
    """True only when wt/.git is the plain one line file git wrote, naming a worktree record inside this repository's
    own git directory. Anything else (a directory, a symlink, another target) is a seat the session rewrote."""
    link = os.path.join(wt, ".git")
    try:
        if not stat.S_ISREG(os.lstat(link).st_mode):   # a symlink, a directory, a FIFO that would block the read
            return False
    except OSError:
        return False
    try:
        with open(link, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, ValueError):
        return False
    if not text.startswith("gitdir: ") or text.rstrip("\n").count("\n"):
        return False
    common = _common(repo)
    target = os.path.realpath(os.path.join(wt, text[len("gitdir: "):].strip()))
    if not (common and target.startswith(os.path.join(common, "worktrees") + os.sep) and os.path.isdir(target)):
        return False
    # THE RECORD MUST BE THIS SEAT'S (second review 2026-10-02, reproduced): a link naming ANOTHER worktree's record, the
    # live loop's among them, passed, and the next reset ran checkout -f through it and detached the loop's branch.
    try:
        with open(os.path.join(target, "gitdir"), encoding="utf-8") as fh:
            back = fh.read().strip()
    except (OSError, ValueError):
        return False
    return os.path.realpath(os.path.join(target, back)) == os.path.realpath(link)   # worktree.useRelativePaths writes it relative


def _wipe(wt):
    """Remove a seat whatever a session left in it: immutable flags and unwritable folders are undone first, never
    through a link. True when the seat is gone."""
    for d, dirs, files in os.walk(wt, topdown=True, followlinks=False):
        for p in [d] + [os.path.join(d, n) for n in dirs + files]:
            try:
                os.chflags(p, 0, follow_symlinks=False)
                if not os.path.islink(p) and os.path.isdir(p):
                    os.chmod(p, 0o700)
            except OSError:  # sbe: allow-silent best effort; the rmtree and the lexists check below decide
                pass   # best effort: rmtree below and the lexists check decide
    shutil.rmtree(wt, ignore_errors=True)
    return not os.path.lexists(wt)


def prepare(repo, wt, base, _again=True):
    """Make wt a worktree of repo at base with no change and no untracked file, plus empty .brother/ and .tmp/ (the
    adapter skips both, so nothing is written into the repository's shared info/exclude). Returns "" or why not.
    A seat whose .git is not the link git wrote is REMOVED and rebuilt, never used: git run against a git directory a
    session planted would run its hooks out here (reproduced by the review)."""
    root = os.path.realpath(seat_root())
    if not os.path.realpath(wt).startswith(root + os.sep):
        return "%s is not under the seat root; nothing was touched" % wt
    if os.path.lexists(wt) and not seat_link_ok(repo, wt):
        shutil.rmtree(wt, ignore_errors=True)
        if os.path.lexists(wt):
            return "a seat that failed its .git check could not be removed"
    if not os.path.exists(os.path.join(wt, ".git")):
        _git(repo, "worktree", "prune")
        r = _git(repo, "worktree", "add", "-q", "--detach", "-f", wt, base)
        if r.returncode != 0:
            return "worktree add failed: %s" % r.stderr.strip()[:160]
    top = _git(wt, "rev-parse", "--show-toplevel").stdout.strip()
    if not top or os.path.realpath(top) != os.path.realpath(wt):
        return "%s is not its own worktree; nothing was reset" % wt
    for args in (("checkout", "-q", "--detach", "-f", base), ("clean", "-q", "-f", "-d", "-x")):
        r = _git(wt, *args)
        if r.returncode != 0:
            why = "git %s failed: %s" % (args[0], r.stderr.strip()[:160])
            # eighth review 2026-10-02: a locked file or an unwritable folder a session left made every later reset
            # fail, and the seat burned a round per call until its units parked; rebuild the seat once instead
            if _again and _wipe(wt):
                return prepare(repo, wt, base, _again=False)
            return why
    for d in (".brother", ".tmp"):
        os.makedirs(os.path.join(wt, d), exist_ok=True)
    return ""


def program_outside_sandbox(program, home=None):
    """'' when the sandbox can run this program, else why not: a program installed in HOME outside the opened folders
    (TOOL_ROOTS, ~/.local, the Claude app's own folder) would be killed by the kernel with no output at all."""
    home = os.path.realpath(home or os.path.expanduser("~"))
    real = os.path.realpath(program)
    if not real.startswith(home + os.sep):
        return ""
    opened = [os.path.join(home, r) for r in TOOL_ROOTS + (".local", "Library/Application Support/Claude/claude-code")]
    if any(real == o or real.startswith(o + os.sep) for o in opened):
        return ""
    return ("the Claude program %s is installed in a home folder the native sandbox does not open (opened: %s); "
            "install it in one of those, or set BROTHER_CLAUDE_NATIVE=off" % (real, ", ".join(TOOL_ROOTS + (".local",))))


def tool_dir(wt):
    """The folder the Claude CLI's Bash tool writes for a session whose cwd is wt (measured 2026-10-02: EPERM on
    mkdir of exactly this path when it was not allowed)."""
    return "/private/tmp/claude-%d/%s" % (os.getuid(), re.sub(r"[/.]", "-", os.path.realpath(wt)))


def profile(token_on=False):
    """The seat's sandbox profile: PROFILE, plus TOKEN_DENY on the loop token lane."""
    return PROFILE + TOKEN_DENY if token_on else PROFILE


def session_argv(model_id, effort, wt, program, common="", env=None, token_on=False):
    """sandbox-exec around `claude -p` with real tools; the prompt goes on stdin. common: the repository's git dir, the
    one path outside the seat a session reads so git works in its worktree (status, diff, log; writes stay refused).
    Every path is resolved first: sandbox rules match resolved paths (third review: a symlinked ~/.claude made the
    seat unreadable and unwritable). env: the environment the child will run under (default: this process's); on the
    token lane (token_on) it is the scrubbed copy naming the login's descriptor (brother_login.FD_VAR), the ONE
    credential-shaped name the scrub below leaves alone: its value is a descriptor number this process opened a moment
    ago, never a secret and never a parent's."""
    wt = os.path.realpath(wt)
    tmp = os.path.join(wt, ".tmp")
    env = os.environ if env is None else env
    # sixth review 2026-10-02: the session inherited every variable of the loop; strip each credential-shaped name, the
    # grader's own rule (grade_build.SECRET_ENV), so no key in the environment reaches a session with the network open
    unset = [a for k in sorted(env) if G.SECRET_ENV.search(k) and not (token_on and k == BL.FD_VAR) for a in ("-u", k)]
    allowed = ["--allowedTools", TOOLS] if token_on else []   # the scrub forces default mode; declared tools still run
    return (["sandbox-exec", "-p", profile(token_on), "-D", "WT=" + wt, "-D", "TMP=" + tmp,
             "-D", "HOME=" + os.path.realpath(os.path.expanduser("~")),   # rules match resolved paths
             "-D", "TOOLDIR=" + tool_dir(wt), "-D", "COMMON=" + (os.path.realpath(common) if common else wt),
             "env", "-u", "GH_TOKEN", "-u", "GITHUB_TOKEN", "-u", "SSH_AUTH_SOCK"] + unset + ["TMPDIR=" + tmp] + PUSH_BLOCK + [program, "-p", "--model", model_id] + MC.CLAUDE_TRIM
            + ["--tools", TOOLS] + allowed + ["--permission-mode", "bypassPermissions"] + MC.CLAUDE_IO + ["--effort", effort]
            + (["--max-budget-usd", budget_usd()] if budget_usd() is not None else []))


def _seat_session(m, effort, wt, common, held_fds, program=None):
    """(argv, effort, child_env, login_fd, "") for one seat session, or (None, None, None, None, why) with why a
    CONFIG_WAIT. THE ONE PLACE A SEAT'S COMMAND LINE IS BUILT (2026-10-05): run() and seat_probe() both come through
    here, so the intake's seat proof exercises exactly the program, sandbox, effort and login lane a seat runs.
    program: the program to run, default the one every production call resolves (model_router.claude_bin)."""
    # THE ROUND'S EFFORT ARM REACHES THE SESSION (second review: an A/B run compared two arms that both ran at the
    # floor), through the ONE Claude effort rule, the adapter's (FX-31.5 review gap 2): never below the model's
    # floor, and an arm that names no level parks the round CONFIG_WAIT, the same refusal a one shot call gets
    program = program or R.claude_bin()
    why = program_outside_sandbox(program)
    if why:
        return None, None, None, None, "%s: %s" % (MC.CONFIG_WAIT, why)
    try:
        effort = MC.claude_effort(env={"BROTHER_CLAUDE_EFFORT": effort or os.environ.get("BROTHER_CLAUDE_EFFORT", "")},
                                  model_id=m["id"])
    except R.Refused as exc:
        return None, None, None, None, "%s: %s" % (MC.CONFIG_WAIT, exc)
    # THE LOOP TOKEN LANE (BROTHER_LOOP_TOKEN=on, 2026-10-03): the Keychain login brother-login saved, read fresh
    # here, goes to this one child on a pipe (login_fd, named by FD_VAR in its scrubbed environment), never in
    # any environment; the parent's environment is never touched and nothing logs it. A missing or unreadable
    # login parks the round CONFIG_WAIT before the ledger row, never a silent fallback.
    token_on = SW.on("BROTHER_LOOP_TOKEN")
    child_env = login_fd = None
    if token_on:
        try:
            child_env, login_fd = BL.with_login({k: v for k, v in os.environ.items() if not G.SECRET_ENV.search(k)})
        except BL.Failure as exc:
            return None, None, None, None, "%s: %s" % (MC.CONFIG_WAIT, exc)
        held_fds.append(login_fd)
        child_env[SCRUB_VAR] = "1"   # the CLI's own tool children never inherit its environment
    os.makedirs(tool_dir(wt), exist_ok=True)   # outside the sandbox, so the session never needs its parent
    return session_argv(m["id"], effort, wt, program, common, env=child_env, token_on=token_on), effort, child_env, login_fd, ""


def _seat_go(runner, wt, child_env, login_fd):
    """The child runner for one seat session: runner(argv, stdin, timeout, cwd[, env=]) in tests, else MC._run_in."""
    if child_env is None:   # off: the call shapes of today, byte for byte
        return (lambda a, s, t: runner(a, s, t, wt)) if runner else (lambda a, s, t: MC._run_in(a, s, t, wt))
    return ((lambda a, s, t: runner(a, s, t, wt, env=child_env)) if runner
            else (lambda a, s, t: MC._run_in(a, s, t, wt, env=child_env, pass_fds=(login_fd,))))


#: THE LOGIN IS KEPT FRESH OUTSIDE THE SEATS (owner ruling A, 2026-10-05, record seat-login-refresh.json). Measured: a
#: seat may READ the login in ~/Library/Keychains but not write it, so a Claude inside a seat cannot refresh an expired
#: access token and answers 401 "OAuth access token has expired"; every native seat of proof pair RB then parked
#: CONFIG_WAIT "Not logged in" and the run ended UNPRODUCTIVE in 4.5 minutes. One ordinary unsandboxed call of the same
#: program refreshes it (the CLI does that itself), after which the identical seat call answers. The driver runs
#: refresh() before every pass (loop_until.sh); the intake runs seat_probe() so READY means a seat answered.
PING_PROMPT = "Reply with the single word ok and nothing else. Use no tools."
REFRESH_TIMEOUT_S = 90
SEAT_PROBE_TIMEOUT_S = 180
REFRESH_HINT = ("a native seat cannot refresh an expired Claude login from inside its sandbox: refresh it with one plain "
                "call (python3 ~/.claude/bin/native_worker.py refresh, which the driver also runs before every pass), "
                "then prepare again; if that refresh fails too, sign in to Claude Code on this machine")


def cheapest_claude(reg):
    """The registry name of the cheapest model on the claude transport (by its cost field), or None."""
    rows = sorted((float(m.get("cost", float("inf"))), n) for n, m in reg.items()
                  if isinstance(m, dict) and m.get("transport") == "claude")
    return rows[0][1] if rows else None


def call_class(r, why):
    """The class of a failed Claude call, never its text: login (an expired, invalid or absent login), error-result,
    no-result. A token can never reach a log line through this, because only these words leave it."""
    text = "%s\n%s\n%s" % (why, (r or {}).get("stderr") or "", (r or {}).get("stdout") or "")
    if LOGIN_FAULT_RE.search(text) or re.search(r"\b401\b|token has expired|/login", text, re.I):
        return "login"
    return "error-result" if why.startswith("claude returned an error result") else "no-result"


def _ledgered(model_id, effort, call, argv, stdin, timeout_s, go):
    """(r, "") from one Claude call registered in the call ledger like every other, or (None, class)."""
    path = CL.ledger_path()
    try:
        started = CL.start(path, {"model": model_id, "effort": effort, "prompt_chars": len(stdin),
                                  "call": "%s-%s" % (call, uuid.uuid4().hex[:12]), "kind": call}, timeout_s)
    except CL.proof_ledger.DrainRefused:
        return None, "draining"   # the run is ending: no call, and no seat starts either
    except (ValueError, OSError) as exc:
        return None, "ledger-refused (%s)" % type(exc).__name__
    try:
        return MC._claude_run(path, started, list(argv), stdin, timeout_s, go), ""
    except MC.SpawnFailed:
        return None, "not-started"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as exc:
        return None, "no-result (%s)" % type(exc).__name__


def refresh(reg=None, runner=None, env=None, timeout_s=REFRESH_TIMEOUT_S):
    """("OK" | "SKIPPED" | "FAILED", detail): one tiny UNSANDBOXED call of the program every Claude call resolves
    (model_call.build_invocation, the production command line), on the cheapest Claude model, so the CLI refreshes an
    expired login where it may write the Keychain. NEVER inside a seat and never handed to one: it runs in model_call's
    scratch, passes nothing to any seat, and the seat sandbox is untouched. A failure names its class only (call_class).
    SKIPPED when no seat runs (native off), or on the loop token lane, where seats run on the saved login, not this
    machine's, and model_call refuses any unsandboxed call. runner(argv, stdin, timeout) replaces the child for tests.
    Cost: one minimal call per pass."""
    env = os.environ if env is None else env
    if not SW.native_on(env):
        return "SKIPPED", "native seats are off, so no seat needs the login"
    if SW.on("BROTHER_LOOP_TOKEN"):
        return "SKIPPED", "the loop token lane is on: seats run on the saved login, not this machine's"
    reg = reg or R.registry()
    name = cheapest_claude(reg)
    if not name:
        return "FAILED", "no-model (the registry names no Claude model)"
    try:
        inv = MC.build_invocation(name, PING_PROMPT, timeout_s, reg)
    except R.Refused:
        return "FAILED", "refused (the command line for %s could not be built)" % name
    r, cls = _ledgered(inv.model_id, "", "refresh", inv.argv, inv.stdin, timeout_s, runner or MC._run)
    if cls == "draining":
        return "SKIPPED", "the run is draining: no seat starts, so no call is spent"
    if cls:
        return "FAILED", cls
    _answer, why = MC._claude_result(r.get("stdout"))
    if why:
        return "FAILED", call_class(r, why)
    return "OK", "%s answered through %s" % (name, inv.program)


def seat_probe(reg=None, program=None, runner=None, timeout_s=SEAT_PROBE_TIMEOUT_S, root=None):
    """("OK", detail) or ("REFUSED", why): one real minimal call through a SEAT, built by _seat_session exactly as run()
    builds one (sandbox-exec around the program, the same login lane), in a scratch seat folder removed afterwards.
    The intake's Claude reach check runs it, so READY means a seat answered, not only the program outside the sandbox.
    program: the program the intake proved (default: the one production resolves). runner(argv, stdin, timeout, cwd[,
    env=]) replaces the child for tests."""
    reg = reg or R.registry()
    name = cheapest_claude(reg)
    if not name:
        return "REFUSED", "the registry names no Claude model to run a seat with"
    root = root or os.environ.get("BROTHER_SCRATCH") or os.path.expanduser("~/.claude/brother-scratch")
    held_fds = []
    wt = None
    try:
        os.makedirs(root, exist_ok=True)
        wt = os.path.realpath(tempfile.mkdtemp(prefix="seat-probe-", dir=root))
        os.makedirs(os.path.join(wt, ".tmp"), exist_ok=True)
        argv, effort, child_env, login_fd, why = _seat_session(reg[name], None, wt, "", held_fds, program=program)
        if why:
            return "REFUSED", why
        r, cls = _ledgered(reg[name]["id"], effort, "seat-probe", argv, PING_PROMPT, timeout_s,
                           _seat_go(runner, wt, child_env, login_fd))
        if cls:
            return "REFUSED", "the seat call did not answer: %s" % cls
        _answer, why = MC._claude_result(r.get("stdout"))
        if why:
            return "REFUSED", "the seat call failed: %s" % call_class(r, why)
        return "OK", "a sandboxed native seat answered (%s)" % name
    except (OSError, R.Refused) as exc:
        return "REFUSED", "the seat could not be prepared (%s)" % type(exc).__name__
    finally:
        for fd in held_fds:
            os.close(fd)
        if wt:
            shutil.rmtree(tool_dir(wt), ignore_errors=True)   # sbe: allow-silent a scratch seat that cannot be removed never changes the verdict
            shutil.rmtree(wt, ignore_errors=True)   # sbe: allow-silent same


#: THE BUILD SEAT RUNS THE LANDING FUZZ (owner ruling 2026-10-05, build quality, option A). Of 1678 finished loop runs, 285
#: were QUARANTINE and 51 of those were grader PASS builds dropped at landing by the landing fuzz (FX-31.8's
#: adapter_conformance.main(0) and MG1.a's merge_gate.main(0): TypeError, 'int' object is not iterable). The seat now runs
#: THAT fuzz (land_apply.fuzz_new_modules, never a second fuzzer) on the build's new modules before handing it back, and a
#: crash goes back into the same seat as a fix session: FIX_ROUNDS of them, each capped at FIX_SESSION_S (and at the
#: session cap). A crash still standing after them is written with the build, which the landing then drops as before.
FIX_ROUNDS = 1
FIX_SESSION_S = 900
_TIMED_OUT = "NO-DATA the session ended without a result: TimeoutExpired"


def seat_fuzz(wt, build):
    """(must_fix, lines): the landing's own fuzz over the build's NEW modules (land_apply.new_modules), run against the
    seat checkout wt with a scratch home as the only place the child may write, so nothing it writes rides into the build.
    must_fix is True when a call crashed or a new module would not import, the two ways the landing fuzz drops a build. A
    fuzz that could not run (no sandbox, no child) is NO-DATA: its lines say so, must_fix stays False, the landing decides."""
    import land_apply as LA   # only when a build exists: the lander's module, never a copy of its fuzz
    mods = LA.new_modules(build)
    if not mods:
        return False, []
    home = tempfile.mkdtemp(prefix="seat-fuzz-")
    said = []
    try:
        os.makedirs(os.path.join(home, "tmp"))
        crashes, _returned, _bad = LA.fuzz_new_modules(wt, mods, LA.suite_env(os.environ, home), home,
                                                       say=said.append, write_root=home)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    return bool(crashes) or any(l.startswith("FUZZ import failed") for l in said), said


def fix_note(lines):
    """The fix session's instruction: the fuzz's own lines, verbatim, then what to do."""
    return ("\n\nTHE LANDING FUZZ CRASHED ON YOUR BUILD IN THIS SEAT. It is the same fuzz the landing runs, and any crash drops "
            "the WHOLE build there. Its own lines:\n%s\nYour work so far is still in this checkout. Fix each crash at its source "
            "(check the argument's type before it is iterated, indexed, converted or opened), add a test feeding these exact "
            "values, keep every other test green, then rewrite the three .brother/ files and reply DONE.\n" % "\n".join(lines[:40]))


def _session(m, effort, wt, repo, held_fds, runner, sub, text, cap, log_dir, tag=""):
    """One Claude session in the seat as it stands (never reset here): (answer, "") or (None, why). why is _TIMED_OUT
    when the session hit its cap; the ledger's terminal row is written either way."""
    held = MC.config_admit("claude", m["id"])
    if held:
        return None, "%s: %s" % (MC.CONFIG_WAIT, held)
    argv, effort, child_env, login_fd, why = _seat_session(m, effort, wt, _common(repo), held_fds)
    if why:
        return None, why
    token_on = child_env is not None
    path = CL.ledger_path()
    try:
        started = CL.start(path, {"model": m["id"], "effort": effort, "prompt_chars": len(text),
                                  "call": "native-%s-%s%s" % (sub, tag, uuid.uuid4().hex[:12]), "kind": "native"}, cap)
    except (CL.proof_ledger.DrainRefused, ValueError, OSError) as exc:
        return None, "REFUSED the Claude call ledger did not register it (%s: %s)" % (type(exc).__name__, exc)
    go = _seat_go(runner, wt, child_env, login_fd)
    try:
        r = MC._claude_run(path, started, argv, text, cap, go)
    except MC.SpawnFailed as exc:
        return None, "NO-DATA the session did not start: %s" % exc
    except subprocess.TimeoutExpired:   # the ledger's terminal row is already written
        return None, _TIMED_OUT
    except OSError as exc:
        return None, "NO-DATA the session ended without a result: %s" % type(exc).__name__
    with open(os.path.join(log_dir, "claude%s.json" % tag.rstrip("-")), "w", encoding="utf-8") as fh:
        fh.write(r.get("stdout") or "")
    with open(os.path.join(log_dir, "claude%s.stderr" % tag.rstrip("-")), "w", encoding="utf-8") as fh:
        fh.write(r.get("stderr") or "")
    answer, why = MC._claude_result(r.get("stdout"))
    if why:
        # A CLAUDE SIDE FAILURE IS NAMED AS ONE: an error result (a usage limit, an outage) or an exit with no
        # result at all (a logged out CLI) is a Claude error, re-seated, never a spent round; stderr says which.
        said = next((l.strip() for l in (r.get("stderr") or "").splitlines() if l.strip()), "")
        silent = r.get("returncode") != 0 and not (r.get("stdout") or "").strip()
        # a logged out CLI answers an ERROR RESULT ("Not logged in"), measured live; a broken profile is silent
        if token_on and LOGIN_FAULT_RE.search(why) and why.startswith("claude returned an error result"):
            # the saved login expired or was revoked: park, the person signs in again (token lane only)
            return None, "%s: Brother needs you to sign in again (run brother-login): %s" % (MC.CONFIG_WAIT, (said or why)[:200])
        if HOST_FAULT_RE.search((r.get("stderr") or "") + "\n" + why) and (silent or why.startswith("claude returned an error result")):
            return None, "%s: the Claude program cannot run on this machine as set up: %s" % (MC.CONFIG_WAIT, (said or why)[:200])
        side = why.startswith("claude returned an error result") or silent
        return None, ("%s: %s" % (CLAUDE_ERROR, why) if side else "NO-DATA %s" % why) + (" | stderr: %s" % said[:160] if said else "")
    return answer, ""


def run(sub, brief_text, base_rev, model, timeout_s, out, repo, log_dir, seat_count=None, runner=None, effort=None):
    """One native build: (True, "") with the build written to out, or (False, why) with nothing written.
    runner(argv, stdin, timeout, cwd) replaces the child for tests; the ledger rows are written either way.
    After the session the seat runs the landing fuzz (seat_fuzz) and sends a crash back into the same seat (FIX_ROUNDS)."""
    reg = R.registry()
    m = reg.get(model)
    if not m or m.get("transport") != "claude":
        return False, "REFUSED model %r is not a Claude transport model" % (model,)
    if not isinstance(brief_text, str) or not brief_text.strip():
        return False, "REFUSED an empty brief"
    os.makedirs(log_dir, exist_ok=True)
    held_fds = []   # the login's pipe on the token lane: closed on every way out of this call
    try:
        n = seats() if seat_count is None else seat_count
        with seat(n, timeout_s) as i:
            wt = seat_dir(repo, i)
            if wt is None:
                return False, "NO-DATA the repository %s cannot be read by git" % repo
            why = prepare(repo, wt, base_rev)
            if why:
                return False, "NO-DATA seat %d: %s" % (i, why)
            answer, why = _session(m, effort, wt, repo, held_fds, runner, sub, brief_text, timeout_s, log_dir)
            if why == _TIMED_OUT:
                # THE CAP IS NOT A VERDICT (review 2026-10-02): a session cut at its cap may have finished the work and
                # only not said so; a seat that passes its .git check and adapts is graded, the grader decides.
                try:
                    if not seat_link_ok(repo, wt):
                        return False, why
                    build = NA.build_from_checkout(wt, base_rev)
                except NA.Refused:
                    return False, why   # sbe: allow-silent the timeout is the reason; an unadaptable seat adds nothing
            elif why:
                return False, why
            else:
                if not seat_link_ok(repo, wt):
                    return False, "REFUSED the session rewrote its seat's .git; nothing from that seat is trusted"
                try:
                    build = NA.build_from_checkout(wt, base_rev)
                except NA.Refused as exc:
                    return False, "ADAPTER REFUSED: %s (session said: %s)" % (exc, answer.strip()[-160:])
            for k in range(FIX_ROUNDS + 1):
                must_fix, lines = seat_fuzz(wt, build)
                with open(os.path.join(log_dir, "seat-fuzz-%d.txt" % k), "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines) + ("\n" if lines else ""))
                if not must_fix or k == FIX_ROUNDS:
                    break   # clean, NO-DATA, or crashes still standing after the last fix: the landing decides
                answer, why = _session(m, effort, wt, repo, held_fds, runner, sub, brief_text + fix_note(lines),
                                       min(timeout_s, FIX_SESSION_S), log_dir, tag="fix%d-" % (k + 1))
                if why and why != _TIMED_OUT:
                    break   # the fix session failed: the build already in hand is written, never lost
                if not seat_link_ok(repo, wt):
                    return False, "REFUSED the fix session rewrote its seat's .git; nothing from that seat is trusted"
                try:
                    build = NA.build_from_checkout(wt, base_rev)
                except NA.Refused:
                    break   # sbe: allow-silent the fix left the seat unadaptable: the build already in hand is written
            _write_build(build, out)
            return True, ""
    except TimeoutError as exc:
        return False, "NO-DATA %s" % exc
    except ValueError as exc:
        return False, "REFUSED %s" % exc
    finally:
        for fd in held_fds:
            os.close(fd)


def _write_build(build, out):
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(build, fh, indent=1)
    os.replace(tmp, out)   # whole or absent: the round's cut globs the folder while sessions still run


def fanout(jobs, repo, base, timeout_s, results=None, seat_count=None, runner=None):
    """Run every job on its own thread (each waits for a seat). Returns the rows, one per job, in job order."""
    rows = [None] * len(jobs)

    def one(k, job):
        t0 = time.time()
        try:
            with open(job["prompt_file"], encoding="utf-8") as fh:
                text = fh.read()
            sub = job["id"].rsplit("-r", 1)[0]
            log_dir = os.path.join(os.path.dirname(os.path.abspath(job["out"])), "native-" + job["id"])
            ok, why = run(sub, text, base, job["model"], timeout_s, job["out"], repo, log_dir, seat_count, runner, job.get("effort"))
        except (OSError, KeyError, TypeError, AttributeError) as exc:
            ok, why = False, "REFUSED a malformed job: %s" % exc
        rows[k] = {"id": job.get("id") if isinstance(job, dict) else None, "model": job.get("model") if isinstance(job, dict) else None,
                   "ok": ok, "error": "" if ok else why,   # the fan out's own keys: fanout_verdict reads CONFIG_WAIT and drains
                   # a Claude error result (a usage limit or an outage): fanout_verdict.all_claude_errors re-seats the round
                   "claude_error": (not ok) and why.startswith(CLAUDE_ERROR),
                   "seat_wait": (not ok) and why.startswith(SEAT_WAIT),   # nothing ran: fanout_verdict.all_unseated
                   "status": "OK" if ok else why.split(" ", 1)[0].rstrip(":"), "seconds": round(time.time() - t0, 1)}
        print("NATIVE  %s %s %s" % (rows[k]["id"], rows[k]["status"], (why or "")[:200]), flush=True)

    threads = [threading.Thread(target=one, args=(k, j)) for k, j in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if results:
        with open(results, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
    return rows


def main(argv):
    if argv[:1] == ["refresh"]:   # the driver's one line before every pass (loop_until.sh): OK, SKIPPED or FAILED <class>
        try:
            state, detail = refresh()
        except Exception as exc:   # sbe: allow-silent named on the line: a refresh that cannot run is FAILED, never silent
            state, detail = "FAILED", "no-answer (%s)" % type(exc).__name__
        print("REFRESH %s %s" % (state, detail), flush=True)
        return 0 if state in ("OK", "SKIPPED") else 1
    if argv[:1] == ["seat-probe"]:   # the intake's seat proof, by hand
        state, detail = seat_probe()
        print("SEAT %s %s" % (state, detail), flush=True)
        return 0 if state == "OK" else 1
    def arg(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default
    if not argv or argv[0].startswith("-") or not all(arg(f) for f in ("--repo", "--base", "--timeout")):
        print(__doc__)
        return 2
    try:
        with open(argv[0], encoding="utf-8") as fh:
            jobs = json.load(fh)
        timeout_s = int(arg("--timeout"))
        seat_count = int(arg("--seats")) if arg("--seats") else None
        if not isinstance(jobs, list) or timeout_s < 1:
            raise ValueError("jobs must be a list and the timeout positive")
    except (OSError, ValueError) as exc:
        print("NATIVE  REFUSED %s" % exc)
        return 2
    rows = fanout(jobs, arg("--repo"), arg("--base"), timeout_s, arg("--results"), seat_count)
    return 0 if rows and all(r["status"] == "OK" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
