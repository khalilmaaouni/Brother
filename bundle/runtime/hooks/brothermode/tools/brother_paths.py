#!/usr/bin/env python3
"""C3: the one place Brother resolves its plugin root, its config directory,
and which coding client it is running under.

WHY THIS EXISTS. Brother's runtime named Claude Code twice, in two shapes:
`CLAUDE_PLUGIN_ROOT` (the plugin's own directory, which a hook needs to find
its siblings) and `~/.claude` (the config directory its stores live under).
Both were typed literally at every site that needed them, so making Brother
run under a second client meant editing every one of those sites and hoping
none was missed. This module is the single seam instead: one import, three
functions, and a site that reads a path now asks a question rather than
asserting an answer.

WHAT WAS MEASURED, on this machine on 2026-09-04, and what was NOT.

  1. Codex does NOT set a variable called `CODEX_PLUGIN_ROOT`. The string does
     not occur anywhere in the shipped binary
     (/Applications/ChatGPT.app/Contents/Resources/codex, codex-cli
     0.153.0-alpha.5):
       strings -a <that binary> | grep -c CODEX_PLUGIN_ROOT   ->  0
     What DOES occur, in the same embedded blob that carries the hook wire
     schema, is the pair `PLUGIN_ROOT` and `CLAUDE_PLUGIN_ROOT` (beside
     `PLUGIN_DATA` and `CLAUDE_PLUGIN_DATA`), which reads as Codex exporting
     its own unprefixed name and keeping Claude's name as a compatibility
     alias. So the plugin-root rungs below are BROTHER_PLUGIN_ROOT,
     CLAUDE_PLUGIN_ROOT, PLUGIN_ROOT, and only then this file's own location.
     HONEST LIMIT: that is read off a binary's string table, not off a live
     hook run. It costs nothing if it is wrong (an unset variable is simply
     skipped) and it is never the only rung, which is why it is safe to ship
     ahead of a live confirmation. It is NOT quoted anywhere as proof that a
     Brother hook runs under Codex; see docs/codex/HOOKS-MAPPING.md, where the
     answer to that question is NO-DATA for a reason that is measured.

  2. `CODEX_HOME` is real and documented in Codex's own help text
     (`codex exec --help`: "--ignore-user-config  Do not load
     $CODEX_HOME/config.toml; auth still uses CODEX_HOME").

THE ONE DELIBERATE DEVIATION from the C3 brief's literal ordering, and why.
The brief orders config_dir as BROTHER_CONFIG_DIR, CLAUDE_CONFIG_DIR,
CODEX_HOME, then the per-client default. Taken literally, a Claude Code
session on a machine that happens to export CODEX_HOME (anyone who has ever
pointed Codex at a throwaway home in a shell profile) would silently move the
founder's BrotherMode stores out of ~/.claude. Claude behaviour must stay
byte-identical, so CODEX_HOME is honoured only when the client is NOT Claude.
A Claude session therefore resolves exactly what it resolved before this
module existed: CLAUDE_CONFIG_DIR when set, else ~/.claude.

NO-DATA. client() returns "" when nothing identifies the host, and "" is not
a client: it is this module saying it does not know. Callers that must choose
a directory anyway get the Claude default, because that is the pre-existing
behaviour and an unknown host must never relocate a store. Callers that gate a
VERDICT on the client (see products/brothersbe/tools/sbe_hooks_wiring.py) must
treat "" as unknown and "codex" as NO-DATA, never as a pass.

Python 3.9, standard library only. No network, no subprocess. Never raises:
every function here sits under a hook that must not be the reason an edit
fails, so an unreadable environment degrades to a default, never to a
traceback.

No em or en dashes anywhere in this file or its output.
"""

import json
import os
import re
import sys
import time

CLAUDE = "claude"
CODEX = "codex"
CURSOR = "cursor"

#: The explicit override, read first everywhere. Set it and nothing below is
#: consulted; it is how a test drives this module backwards, and how a founder
#: pins a host this module guessed wrong.
CLIENT_ENV = "BROTHER_CLIENT"
PLUGIN_ROOT_ENV = "BROTHER_PLUGIN_ROOT"
CONFIG_DIR_ENV = "BROTHER_CONFIG_DIR"

#: Plugin-root rungs after BROTHER_PLUGIN_ROOT, in order. CLAUDE_PLUGIN_ROOT is
#: what Claude Code exports to every plugin hook process; PLUGIN_ROOT is the
#: unprefixed name found in the Codex binary beside it (see the docstring).
PLUGIN_ROOT_VARS = ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT")

#: Variables whose mere presence identifies the host. CLAUDECODE is set by
#: Claude Code itself (scripts/model_worker.py's docstring already records
#: `env -u CLAUDECODE claude -p ...` as the way to unset it for a child).
#: The CODEX_ names are read from the same binary string table as above.
CLAUDE_MARKER_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
CODEX_MARKER_VARS = ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SANDBOX")

#: The per-client config directory, used only when no variable named one.
CLIENT_CONFIG_DIRNAME = {CLAUDE: ".claude", CODEX: ".codex",
                         CURSOR: ".cursor"}

#: A plugin package's manifest, by client. Used as the last identification
#: rung: a hook running out of a directory carrying .codex-plugin/plugin.json
#: is running out of a Codex package whatever the environment forgot to say.
#: Cursor's .cursor-plugin/plugin.json is the same idea. More than one
#: manifest in one directory is still ambiguous and reads as "".
CLIENT_MANIFEST = {CLAUDE: os.path.join(".claude-plugin", "plugin.json"),
                   CODEX: os.path.join(".codex-plugin", "plugin.json"),
                   CURSOR: os.path.join(".cursor-plugin", "plugin.json")}

#: Directory names this module may live in inside a package, whose PARENT is
#: the package root: <root>/tools/ in a product, <root>/runtime/ in the
#: installed bundle, <root>/scripts/ in this checkout.
_PACKAGE_SUBDIRS = ("tools", "runtime", "scripts")

#: The app-bundled Codex binary, newest location first. Six tools used to
#: declare their own copy of the second path; the app moved the binary and
#: every copy went stale at once (2026-09-26: virgin-unit-proof reported "no
#: executable Codex binary" on every gate). Never the bare "codex" name: PATH
#: held an older shim the model rejected (orchestrators/execution.py, point 1).
CODEX_BIN_ENV = "BROTHER_CODEX_BIN"
CODEX_APP_BINS = ("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
                  "/Applications/ChatGPT.app/Contents/Resources/codex")


def _env(env):
    """The mapping to read. None means the real process environment; a dict
    lets a test drive every rung without touching os.environ."""
    return os.environ if env is None else env


def _get(env, name):
    """A stripped non-empty value, or "". Never raises: a mapping that returns
    a non-string (a test passing {"CODEX_HOME": 5}) degrades to "" rather than
    exploding inside a hook."""
    try:
        raw = _env(env).get(name)
    except (AttributeError, TypeError):
        return ""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def package_root():
    """This module's own package root, derived from __file__.

    The last rung of plugin_root(), and the only one that works when the host
    exported nothing at all. <root>/tools/brother_paths.py answers <root>;
    a copy sitting loose in a directory answers that directory."""
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(here) in _PACKAGE_SUBDIRS:
        return os.path.dirname(here)
    return here


def plugin_root(env=None):
    """The directory of the plugin package this code is running out of.

    BROTHER_PLUGIN_ROOT, then CLAUDE_PLUGIN_ROOT, then PLUGIN_ROOT, then this
    module's own package root. Always a non-empty absolute path: unlike
    client() there is no NO-DATA here, because __file__ always answers."""
    named = _get(env, PLUGIN_ROOT_ENV)
    if not named:
        for var in PLUGIN_ROOT_VARS:
            named = _get(env, var)
            if named:
                break
    if named:
        return os.path.abspath(os.path.expanduser(named))
    return package_root()


def _manifest_client(root):
    """The client whose plugin manifest sits at `root`, or "" when neither or
    both do. Both is ambiguous on purpose: a directory shipping two packages
    identifies nothing, and guessing there is how a store lands in the wrong
    home."""
    found = [name for name, rel in CLIENT_MANIFEST.items()
             if os.path.isfile(os.path.join(root, rel))]
    return found[0] if len(found) == 1 else ""


def client(env=None):
    """Which coding client is running this process: "claude", "codex",
    "cursor", or "" for NO-DATA.

    BROTHER_CLIENT (validated, an unrecognised value is ignored rather than
    trusted), then the host's own marker variables, then the plugin manifest
    beside the resolved plugin root. "" means unknown, and unknown is never a
    pass in any checker that reads it.

    Cursor marker variables are NO-DATA in this tree: no live Cursor Agent
    hook process has been string-tabled the way Codex was. Identification
    is BROTHER_CLIENT=cursor, or a directory that carries only
    .cursor-plugin/plugin.json. Do not invent a CURSOR_* marker.

    THE NEAREST HOST WINS, and that ordering is measured rather than
    preferred (2026-09-05, codex-cli 0.153.0-alpha.5, evidence
    ~/.claude/evidence/lane-codex-door-env-probe.log). A `codex exec` turn
    exports its own markers to every command its model runs, verbatim from a
    probe inside one:
        CODEXVARS ['CODEX_CI', 'CODEX_HOME', 'CODEX_SANDBOX',
                   'CODEX_SESSION_ID', 'CODEX_THREAD_ID']
    but a Codex turn started from inside a Claude Code session ALSO inherits
    that session's CLAUDECODE, and with Claude's markers read first this
    function answered 'claude' inside a real Codex turn, so every caller that
    picks a client's argv from it picked the wrong one. Codex's markers are
    per-TURN and cannot outlive the turn that set them; CLAUDECODE is
    per-SESSION and is inherited by everything that session starts. The Codex
    marker is therefore the more specific evidence and is read first.
    CODEX_HOME is deliberately not one of them, for the reason in the module
    docstring: it is a variable people leave in a shell profile."""
    named = _get(env, CLIENT_ENV).lower()
    if named in (CLAUDE, CODEX, CURSOR):
        return named
    for var in CODEX_MARKER_VARS:
        if _get(env, var):
            return CODEX
    for var in CLAUDE_MARKER_VARS:
        if _get(env, var):
            return CLAUDE
    try:
        return _manifest_client(plugin_root(env))
    except OSError:
        # A plugin root on an unreadable mount identifies nothing; that is
        # NO-DATA, not a crash inside somebody's PreToolUse hook.
        return ""


def config_dir(env=None):
    """The directory Brother's own stores live under.

    BROTHER_CONFIG_DIR, then CLAUDE_CONFIG_DIR, then (only when the client is
    Codex or unknown, never Claude or Cursor) CODEX_HOME, then ~/.claude for Claude and an unknown client,
    ~/.codex for Codex. See the module docstring for why CODEX_HOME is gated
    on the client rather than read unconditionally."""
    named = _get(env, CONFIG_DIR_ENV) or _get(env, "CLAUDE_CONFIG_DIR")
    if named:
        return os.path.abspath(os.path.expanduser(named))
    which = client(env)
    if which in (CODEX, ""):
        codex_home = _get(env, "CODEX_HOME")
        if codex_home:
            return os.path.abspath(os.path.expanduser(codex_home))
    dirname = CLIENT_CONFIG_DIRNAME.get(which) or CLIENT_CONFIG_DIRNAME[CLAUDE]
    return os.path.join(os.path.expanduser("~"), dirname)


def config_path(*parts, **kwargs):
    """config_dir() joined with `parts`. The shape nearly every call site
    wants, so nobody re-types os.path.join around it."""
    return os.path.join(config_dir(kwargs.get("env")), *parts)


def codex_bin(env=None):
    """The absolute Codex binary to call: BROTHER_CODEX_BIN when set, else the
    first CODEX_APP_BINS entry that is an executable file, else the first
    entry, so a caller's own "not found" names where the binary is expected
    now. Read at call time, never cached; never PATH."""
    named = _get(env, CODEX_BIN_ENV)
    if named:
        return named
    proven = recorded_program("codex", env)   # the program the intake proved answers (2026-09-30)
    if proven:
        return proven
    for path in CODEX_APP_BINS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return CODEX_APP_BINS[0]


#: WHERE A CLAUDE OR CODEX PROGRAM MAY BE INSTALLED (2026-09-30). Discovery only: nothing here decides which one runs. The
#: intake's reachability proof (scripts/loop/model_reachability.py) proves candidates newest first and records the one
#: that answered; model_router.claude_bin() and codex_bin() read that record. PATH is enumerated like any other place
#: because a proof, not a location, decides whether a program is usable.
CLAUDE_BIN_ENV = "BROTHER_CLAUDE_BIN"
PROGRAM_RECORD_ENV = "BROTHER_PROGRAM_RECORD"
CLAUDE_DESKTOP_ROOT = os.path.join("~", "Library", "Application Support", "Claude", "claude-code")
PACKAGE_BIN_DIRS = (os.path.join("~", ".local", "bin"), os.path.join("~", ".npm-global", "bin"),
                    "/opt/homebrew/bin", "/usr/local/bin")


def _home_path(env, path):
    """expanduser against the mapping's HOME, so a test's HOME is honoured without touching os.environ."""
    home = _get(env, "HOME")
    if home and (path == "~" or path.startswith("~" + os.sep)):
        return home + path[1:]
    return os.path.expanduser(path)


def _executable(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def program_candidates(name, first=(), env=None):
    """Every executable file named `name` in `first`, the PATH directories and PACKAGE_BIN_DIRS, canonicalized (the
    real path, symlinks resolved) and deduplicated, in discovery order. Order is not preference: callers sort by
    the program's own version."""
    seen, out = set(), []
    dirs = [d for d in _get(env, "PATH").split(os.pathsep) if d] + [_home_path(env, d) for d in PACKAGE_BIN_DIRS]
    for path in list(first) + [os.path.join(d, name) for d in dirs]:
        try:
            if not _executable(path):
                continue
            real = os.path.realpath(path)
        except (OSError, ValueError):
            continue
        if real not in seen:
            seen.add(real); out.append(real)
    return out


def desktop_bundles(root):
    """Every Claude Code binary the desktop app keeps under root, in both layouts it has used: <version>/claude.app/...
    and, since 2.1.286 (2026-10-03), <version>/<build hash>/claude.app/.... Missing ones are filtered by the caller."""
    tail = ("claude.app", "Contents", "MacOS", "claude")
    out = []
    try:
        versions = sorted(os.listdir(root))
    except OSError:
        return out
    for v in versions:
        out.append(os.path.join(root, v, *tail))
        try:
            subs = sorted(os.listdir(os.path.join(root, v)))
        except OSError:
            continue
        out += [os.path.join(root, v, sub, *tail) for sub in subs if sub != "claude.app"]
    return out


def claude_candidates(env=None):
    """Every installed Claude Code CLI: each version the desktop app ships, then PATH and the package directories."""
    return program_candidates("claude", desktop_bundles(_home_path(env, CLAUDE_DESKTOP_ROOT)), env)


def codex_candidates(env=None):
    """Every installed Codex CLI: the app bundled locations, then PATH and the package directories."""
    return program_candidates("codex", CODEX_APP_BINS, env)


def program_fingerprint(path):
    """'realpath|size|mtime_ns|inode', or None: a program replaced behind the same path is a different program."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return "%s|%d|%d|%d" % (os.path.realpath(path), st.st_size, st.st_mtime_ns, st.st_ino)


def program_record_path(env=None):
    """The resolved program record the intake writes (scripts/loop/model_reachability.write_record)."""
    return _get(env, PROGRAM_RECORD_ENV) or _home_path(env, os.path.join("~", ".claude", "evidence", "loop-programs.json"))


def recorded_program(transport, env=None):
    """The program the intake PROVED for this transport, or None: no record, an unreadable one, a program gone, or a
    program whose fingerprint moved since its proof (a replaced binary is an unproven one)."""
    try:
        with open(program_record_path(env), encoding="utf-8") as fh:
            row = (json.load(fh).get("programs") or {}).get(transport)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        return None
    path = row["path"]
    if not _executable(path) or program_fingerprint(path) != row.get("fingerprint"):
        return None
    return path


def decision_sentinel_path(env=None):
    """Where a rendered decision screen is stamped for the intake gate.

    THE CONTRACT, and it has one reader: the intake gate hook opens
    <config dir>/decision-screens/<session id>.json and refuses a founder
    facing question when it is not there. It scoped itself per session on
    2026-09-15, after one global stamp let a consumed screen from session A
    refuse session B days later. The writers were never moved, so from that
    day a session could render a screen, publish it, and still be refused,
    which teaches the escape hatch instead of the ceremony. The session id
    comes from CLAUDE_CODE_SESSION_ID, the same id the hook receives, and an
    absent one takes the hook's own name for it, "no-session", so the stamp
    is never silently dropped. The sanitising rule is the hook's rule: any
    character outside [A-Za-z0-9_-] becomes an underscore, which is also
    what keeps a session id carrying a path separator inside the directory.

    BROTHER_DECISION_SENTINEL overrides the whole path, so a test suite
    never writes over the live stamp of the session running it.
    """
    override = _get(env, "BROTHER_DECISION_SENTINEL")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    session = _get(env, "CLAUDE_CODE_SESSION_ID") or "no-session"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session)[:80] or "no-session"
    return config_path("decision-screens", "%s.json" % safe, env=env)


def stamp_decision_screen(out_path, title, env=None):
    """Record that a screen was rendered, for the intake gate to read.

    Returns the path written, or None when it could not be written, and
    never raises: a screen that rendered must not fail because a stamp
    could not be filed. Both renderers (scripts/decide.py and
    scripts/decide_round.py) route through here, so the contract with the
    gate lives in one place rather than in two copies that can drift apart
    again.
    """
    sentinel = decision_sentinel_path(env)
    try:
        parent = os.path.dirname(sentinel)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(sentinel, "w", encoding="utf-8") as fh:
            json.dump({"path": os.path.abspath(out_path),
                       "title": title or "",
                       "written_at_epoch": int(time.time())}, fh)
        return sentinel
    except OSError:
        return None  # sbe: allow-silent None is the documented could-not-stamp value, and both callers (scripts/decide.py, scripts/decide_round.py) print a could-not-stamp line naming the path on stderr when they get it


def describe(env=None):
    """Everything this module resolved, as a plain dict. The report a checker
    or a bug report quotes, so a wrong path is one command away from being
    seen rather than deduced."""
    which = client(env)
    return {"client": which or "NO-DATA",
            "plugin_root": plugin_root(env),
            "config_dir": config_dir(env),
            "package_root": package_root()}


def main(argv):
    """--json prints describe(); no argument prints the same three lines in
    plain text. Exit 2 (NO-DATA, never a pass) when the client is unknown, so
    a shell caller can gate on the identification itself."""
    facts = describe()
    if "--json" in argv[1:]:
        print(json.dumps(facts, indent=2, sort_keys=True))
    else:
        for key in ("client", "plugin_root", "config_dir", "package_root"):
            print("%-12s %s" % (key, facts[key]))
    return 2 if facts["client"] == "NO-DATA" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
