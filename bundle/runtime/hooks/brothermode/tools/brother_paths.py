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
import sys

CLAUDE = "claude"
CODEX = "codex"

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
CLIENT_CONFIG_DIRNAME = {CLAUDE: ".claude", CODEX: ".codex"}

#: A plugin package's manifest, by client. Used as the last identification
#: rung: a hook running out of a directory carrying .codex-plugin/plugin.json
#: is running out of a Codex package whatever the environment forgot to say.
CLIENT_MANIFEST = {CLAUDE: os.path.join(".claude-plugin", "plugin.json"),
                   CODEX: os.path.join(".codex-plugin", "plugin.json")}

#: Directory names this module may live in inside a package, whose PARENT is
#: the package root: <root>/tools/ in a product, <root>/runtime/ in the
#: installed bundle, <root>/scripts/ in this checkout.
_PACKAGE_SUBDIRS = ("tools", "runtime", "scripts")


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
    """Which coding client is running this process: "claude", "codex", or ""
    for NO-DATA.

    BROTHER_CLIENT (validated, an unrecognised value is ignored rather than
    trusted), then the host's own marker variables, then the plugin manifest
    beside the resolved plugin root. "" means unknown, and unknown is never a
    pass in any checker that reads it.

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
    if named in (CLAUDE, CODEX):
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
    not Claude) CODEX_HOME, then ~/.claude for Claude and an unknown client,
    ~/.codex for Codex. See the module docstring for why CODEX_HOME is gated
    on the client rather than read unconditionally."""
    named = _get(env, CONFIG_DIR_ENV) or _get(env, "CLAUDE_CONFIG_DIR")
    if named:
        return os.path.abspath(os.path.expanduser(named))
    which = client(env)
    if which != CLAUDE:
        codex_home = _get(env, "CODEX_HOME")
        if codex_home:
            return os.path.abspath(os.path.expanduser(codex_home))
    dirname = CLIENT_CONFIG_DIRNAME.get(which) or CLIENT_CONFIG_DIRNAME[CLAUDE]
    return os.path.join(os.path.expanduser("~"), dirname)


def config_path(*parts, **kwargs):
    """config_dir() joined with `parts`. The shape nearly every call site
    wants, so nobody re-types os.path.join around it."""
    return os.path.join(config_dir(kwargs.get("env")), *parts)


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
