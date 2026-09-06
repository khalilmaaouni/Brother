#!/usr/bin/env python3
"""BrotherSBE PreToolUse Bash guard: the early half of the write boundary
(release blocker 1, BR-1014).

WHY THIS EXISTS
  `tools/sbe_authority_hook.py` and `tools/sbe_fence_hook.py` sit in front of
  Edit, Write, MultiEdit and NotebookEdit, which name their target in a
  structured field. Bash names nothing. Until this file, these four commands
  walked straight past both guards:

      printf '%s\\n' 'replacement' > CLAUDE.md
      python3 -c "open('.claude/settings.json', 'w').write('...')"
      sed -i 's/old/new/' hooks/hooks.json
      rm -f .sbe/tasks.json

  `tools/sbe_fence_hook.py::WRITE_TOOLS` says so in its own comment, and
  `docs/BYPASS-COVERAGE.md` row 20 recorded it as UNCOVERED until this file and
  `tools/sbe_session_reconcile.py` landed together. This file covers the obvious
  half of it and says plainly which half that is. Row 19 (a hook disabled) is
  unchanged: `BROTHERSBE_BASH_GUARD_OFF` is the same cooperating-control
  weakness every hook here has, and it announces itself rather than being
  silent.

WHAT THIS FILE IS, AND WHAT IT IS NOT
  It is an EARLY WARNING and an immediate blocker for a positively identified
  protected write. It is NOT the authoritative control, and it must never be
  described as one. Arbitrary shell can write through a generated script, a
  compiled binary, an interpreter this file does not know, a here-document, a
  process substitution, or a command whose target is computed at run time, and
  no parse of the command TEXT can see any of that. The authoritative control
  is `tools/sbe_session_reconcile.py`, the Stop hook, which inspects the
  repository changes that actually survived rather than the command that was
  typed.

  So the two honest sentences about an ALLOW from this file are:

      This command was not positively identified as a protected write.
      This command was NOT proven to be read only.

  Nothing in this file, its stderr, or its decision JSON ever says the second
  thing differently. That is spec item 1.3 rule 8, and it is the reason the
  allow path here is silent rather than reassuring.

THE THREE RULES THIS FILE OBEYS, INHERITED VERBATIM FROM ITS SIBLINGS
  1. FAIL OPEN, LOUDLY, EXCEPT FOR THE ONE REFUSAL IT EXISTS TO MAKE. An
     unparseable command, an unterminated quote, an oversize command line, an
     unimportable helper, an unreadable task registry, any unexpected
     exception: all of them ALLOW and print why to stderr. The single
     exception is the whole point of the file: a candidate path that is
     POSITIVELY IDENTIFIED as a protected family, reached by a write
     construct this file understands, and not covered by an open task's
     declared `ownedPaths`, is REFUSED.
  2. STDOUT IS THE DECISION CHANNEL AND NOTHING ELSE. Two output funnels
     (`_out`, `_warn`) and no bare `print` anywhere, exactly as
     `tools/sbe_fence_hook.py` and `tools/sbe_authority_hook.py` are built.
  3. ONE PARSE, NEVER A SECOND COPY. Path canonicalization
     (`canonical_target`), scope overlap with its case-fold confirmation
     (`paths_overlap`, `_same_entry_case_insensitive`) and the nine detected
     authority families (`_matched_surface`, reached through
     `sbe_authority_hook.confirmed_surface` so the case-fold handling comes
     with it) are IMPORTED from the modules that already own them. This file
     adds exactly one list of its own, `CONTROL_PLANE_PATTERNS`, for the
     control-plane families the spec names that no existing module owns yet.

Python 3.9, standard library only, cross-platform, no network, no subprocess.
No em or en dashes anywhere in this file, its comments, or its output.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(HERE)


# ---------------------------------------------------------------------------
# Output funnels. Stdout carries the decision JSON and nothing else; every
# diagnostic goes to stderr. Mirrors tools/sbe_fence_hook.py exactly.
# ---------------------------------------------------------------------------


def _is_inplace_flag(flag):
    """True when a short flag CLUSTER carries in-place editing.

    Found by a hostile refuter of this guard: the test used to be
    `flag.startswith("-i")`, which reads `sed -i` and misses `perl -pi -e`,
    because there the `i` sits inside a cluster (`-pi`). Short flags cluster,
    so membership in the cluster is the question, not its first letter. Long
    flags are matched by name instead, since `--include` is not `--in-place`.
    """
    if flag.startswith("--"):
        return flag == "--in-place" or flag.startswith("--in-place=")
    if not flag.startswith("-") or len(flag) < 2:
        return False
    # A suffix like -i.bak is an in-place flag with a backup extension; only
    # the cluster before any punctuation is a set of short flags.
    cluster = flag[1:].split(".")[0].split("=")[0]
    return "i" in cluster

def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def _warn(s):
    sys.stderr.write(s if s.endswith("\n") else s + "\n")
    sys.stderr.flush()


class OpenFail(Exception):
    """Raised anywhere a decision cannot be made SAFELY. Always caught at the
    top of decide(); always produces an ALLOW plus a stderr line naming the
    reason. A named exception rather than a returned sentinel, so a new code
    path cannot forget to check the sentinel and accidentally deny."""


class Unclassifiable(Exception):
    """The command text could not be split into commands and words at all, so
    nothing about it was examined. Distinct from OpenFail only so the stderr
    line can say which of the two happened; both allow."""


# ---------------------------------------------------------------------------
# Shared helpers, loaded by path. Never a bare `import`: tools/ is not a
# package and this hook runs with an arbitrary cwd, so `import
# sbe_fence_hook` could resolve against a different checkout entirely. Same
# construction, same reason, as tools/sbe_authority_hook.py.
# ---------------------------------------------------------------------------

class _LoadResult(object):
    """One sibling module, or the reason it could not be loaded. An object
    rather than a pair, because this project's honesty meta-test reads a
    2-tuple return as a possible verdict source and a module-or-reason is not
    one."""

    def __init__(self, mod, error):
        self.mod = mod
        self.error = error


_LOADED = {}


def _load_by_path(alias, path):
    """One `.py` file loaded under a private module name so it can never
    collide with a same-named module already on sys.path. Cached. Never
    raises."""
    if alias in _LOADED:
        return _LOADED[alias]
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(alias, path)
        if spec is None or spec.loader is None:
            _LOADED[alias] = _LoadResult(None, "no import spec for %s" % path)
            return _LOADED[alias]
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault(alias, mod)
        _LOADED[alias] = _LoadResult(mod, None)
    except Exception as e:
        _LOADED[alias] = _LoadResult(None, "%s: %s" % (type(e).__name__, e))
    return _LOADED[alias]


def require_module(alias, path, what):
    result = _load_by_path(alias, path)
    if result.mod is None:
        raise OpenFail(
            "%s could not be imported (%s), and this guard holds no private copy of "
            "%s on purpose, so it cannot decide" % (path, result.error, what))
    return result.mod


def require_fence_module():
    return require_module("sbe_fence_hook_for_bash_guard",
                          os.path.join(HERE, "sbe_fence_hook.py"),
                          "the path canonicalization and scope-overlap rules")


def require_surface_module():
    return require_module("sbe_instruction_surface_for_bash_guard",
                          os.path.join(HERE, "sbe_instruction_surface.py"),
                          "the authority-surface family list")


def require_authority_module():
    return require_module("sbe_authority_hook_for_bash_guard",
                          os.path.join(HERE, "sbe_authority_hook.py"),
                          "the case-fold-confirmed authority-surface match")


def load_repo_scope_module():
    """`tools/sbe_repo_scope.py` (E76 per-repository hook scoping), the same
    cached _load_by_path shape as the require_*_module functions above, but
    NEVER raising: an unreadable gate module degrades to active (default)
    rather than taking this guard down with it."""
    result = _load_by_path("sbe_repo_scope_for_bash_guard",
                           os.path.join(HERE, "sbe_repo_scope.py"))
    return result.mod


def require_tasks_module():
    return require_module("brothersbe_tasks_for_bash_guard",
                          os.path.join(ROOT_DIR, "src", "brothersbe", "tasks.py"),
                          "the task-registry reader")


# ---------------------------------------------------------------------------
# The protected surface.
#
# The nine detected authority families (CLAUDE.md, .claude/**, .mcp.json,
# .claude-plugin/**, hooks/**, agents/*.md, skills/*/SKILL.md, CODEOWNERS,
# .github/workflows/**) are NOT re-typed here. They come from
# `sbe_instruction_surface._matched_surface` through
# `sbe_authority_hook.confirmed_surface`, which adds the filesystem-confirmed
# case fold. The list below is the remainder the spec names: the control
# plane proper, which no existing module owns.
#
# `.sbe/tasks.json` and `STATE.md` are here beyond the spec's literal list
# because spec fixture 5 is "a command that deletes .sbe/tasks.json is
# caught", and because 1.4 says in as many words: do not let the agent delete
# the baseline, task registry, fence registry or policy and then treat the
# missing control as permission. A registry that can be removed is not a
# control.
#
# `.sbe/policy.yml`, `.sbe/checks.yml` and `src/brothersbe/policy.py` do not
# exist in this repository yet (they land with blockers 3 and 4). They are
# listed anyway: a pattern that protects a file from the moment it is created
# is the point, and a glob over an absent file matches nothing and costs
# nothing.
# ---------------------------------------------------------------------------

CONTROL_PLANE_PATTERNS = (
    ".sbe/policy.yml",
    ".sbe/checks.yml",
    ".sbe/tasks.json",
    # The grant files. Found by a hostile refuter of this guard: break-glass is
    # the one record that clears any protected change, and it was protected by
    # nothing, so an agent could write itself the exemption through the very
    # channel this file exists to close. Approvals, waivers and decisions grant
    # the same way and belong here for the same reason.
    ".sbe/break-glass.json",
    ".sbe/approvals.json",
    ".sbe/waivers.json",
    ".sbe/decisions/*",
    "src/brothersbe/evidence.py",
    "src/brothersbe/policy.py",
    "tools/sbe_*guard*.py",
    "tools/sbe_*hook*.py",
    "STATE.md",
)

#: What a target of "." or the repository root itself covers. A destructive
#: command aimed at the root ("rm -rf .") reaches every family above without
#: naming any of them, and a comparison that only ever matched spellings
#: would wave it through.
ROOT_FAMILY = "the repository root, which contains every protected family"

#: Set, and the guard says so on stderr on every Bash call it would otherwise
#: have checked, so the bypass is never silent. Same shape and same name style
#: as BROTHERSBE_FENCE_HOOK_OFF and BROTHERSBE_AUTHORITY_HOOK_OFF.
DISABLE_ENV = "BROTHERSBE_BASH_GUARD_OFF"

#: Beyond these, the command is not parsed at all and the guard fails open
#: with a stderr line. A hook in front of every Bash call must not become a
#: place where a hostile command line can burn unbounded time.
MAX_COMMAND_CHARS = 20000
MAX_TOKENS = 4000
MAX_DEPTH = 3


def authority_patterns(surface_mod):
    """The nine authority families as PATTERNS, derived from the tuples
    `_matched_surface` is itself built from rather than re-typed.

    `_matched_surface` answers "is this path a member of a family", which is
    the right question for a file. It says no to `.claude` itself, because a
    directory is not a member of the tree below it, and `rm -rf .claude`
    would have walked past a check that only ever asked the membership
    question. `paths_overlap` asks the containment question in both
    directions, so these patterns catch the directory that holds the family
    as well as the files inside it."""
    pats = []
    for d in surface_mod.AUTHORITY_TOP_DIRS:
        pats.append(d + "/**")
    pats.extend(surface_mod.AUTHORITY_ROOT_FILES)
    pats.extend(surface_mod.AUTHORITY_CODEOWNERS_PATHS)
    pats.extend(("CLAUDE.md", "agents/*.md", "skills/*/SKILL.md",
                 ".github/workflows/**"))
    return tuple(pats)


def protected_family(fence_mod, surface_mod, authority_mod, root, rel):
    """The protected family `rel` belongs to, or "" for none.

    Authority families first, through the sibling that already knows how to
    confirm a case fold against the filesystem rather than trusting a folded
    string. Then every family as a containment pattern, through
    `paths_overlap`, which carries the identical confirmation for globs,
    directory prefixes and case."""
    if rel in (".", ""):
        return ROOT_FAMILY
    surface = authority_mod.confirmed_surface(fence_mod, surface_mod, root, rel)
    if surface:
        return surface
    for pattern in authority_patterns(surface_mod) + CONTROL_PLANE_PATTERNS:
        if fence_mod.paths_overlap(rel, pattern, root):
            return pattern
    return ""


# ---------------------------------------------------------------------------
# The shell scanner.
#
# THIS IS NOT A SHELL PARSER AND MUST NOT BE MISTAKEN FOR ONE. It knows quoting
# well enough that a '>' inside quotes is text and a '>' outside them is a
# redirection, and it knows enough operators to split a line into simple
# commands. Everything it cannot read raises Unclassifiable, which allows.
# ---------------------------------------------------------------------------

class Token(object):
    """One word or one operator from a command line. `quoted` records whether
    any part of a word arrived inside quotes, which is the whole reason this
    scanner exists rather than `shlex.split`: `grep ">" CLAUDE.md` must not
    read as a redirection, and shlex.split drops exactly the fact that
    answers it."""

    def __init__(self, text, quoted, is_op):
        self.text = text
        self.quoted = quoted
        self.is_op = is_op


#: Operators that end one simple command and start another. Redirections are
#: deliberately NOT here: they belong to the command they are attached to.
SEPARATORS = frozenset((";", "&", "&&", "|", "||", "\n"))

#: Redirection operators whose next word is a file this command WRITES.
WRITE_REDIRECTS = frozenset((">", ">>", ">|"))


#: A Windows drive-letter path, the one shape no POSIX command line carries.
_WINDOWS_DRIVE_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']*")

#: A Git Bash drive path: a single-letter first component, `/c/Users/...`.
_MSYS_DRIVE_PATH = re.compile(r"^/([A-Za-z])/(.*)$")


def _windows_paths_to_slashes(command):
    r"""`command` with the separators of any Windows drive-letter path turned
    into forward slashes. Windows only; a POSIX run gets the string back
    unchanged.

    WHY. This file tokenizes with POSIX rules, where a backslash ESCAPES the
    next character. A Windows command line carries paths like
    `C:\other\bin\tool`, and POSIX tokenization eats every separator, leaving
    the single word `C:otherbintool`. `_exec_target_raw` then sees no path at
    all, the cross-project check never runs, and a command that must be refused
    is ALLOWED. That was measured here, not supposed.

    Only a drive-letter prefix is rewritten, a shape no POSIX command line
    carries, and only on Windows, so nothing about a POSIX run changes.
    Forward slashes are what the rest of this file already resolves correctly
    and Windows accepts them as separators everywhere.

    This can only make MORE paths visible, never fewer, so it cannot turn a
    refusal into an allow."""
    if os.name != "nt" or not isinstance(command, str):
        return command
    return _WINDOWS_DRIVE_PATH.sub(lambda m: m.group(0).replace("\\", "/"), command)


def _msys_path_to_windows(raw):
    r"""A Git Bash `/c/...` path as its native `C:\...` form. Windows only.

    WHY. `os.path.isabs("/c/BrotherCert/x")` is True, so this file treats it as
    absolute, but `os.path.realpath` resolves it against the CURRENT DRIVE as
    `C:\c\BrotherCert\x`, which does not exist. The marker walk then finds no
    project root above a path that is not there, and the guard takes its
    "cannot tell, allow" branch. A cross-project command written the way Git
    Bash writes paths was therefore allowed.

    On POSIX `/c/...` is an ordinary absolute path and must be left exactly as
    it is, so this does nothing there."""
    if os.name != "nt" or not isinstance(raw, str):
        return raw
    m = _MSYS_DRIVE_PATH.match(raw)
    if not m:
        return raw
    return "%s:\\%s" % (m.group(1).upper(), m.group(2).replace("/", "\\"))


def tokenize(command):
    """The command line as Tokens, or Unclassifiable naming what stopped it."""
    if not isinstance(command, str):
        raise Unclassifiable("the command was not a string")
    command = _windows_paths_to_slashes(command)
    if len(command) > MAX_COMMAND_CHARS:
        raise Unclassifiable(
            "the command is %d characters, past this guard's %d character ceiling"
            % (len(command), MAX_COMMAND_CHARS))
    tokens = []
    buf = []
    quoted = [False]
    i, n = 0, len(command)

    def flush():
        if buf:
            tokens.append(Token("".join(buf), quoted[0], False))
            del buf[:]
            quoted[0] = False

    while i < n:
        if len(tokens) > MAX_TOKENS:
            raise Unclassifiable(
                "the command holds more than %d tokens, past this guard's ceiling"
                % MAX_TOKENS)
        ch = command[i]
        if ch == "\\":
            if i + 1 >= n:
                raise Unclassifiable("the command ends in a trailing backslash")
            buf.append(command[i + 1])
            quoted[0] = True
            i += 2
            continue
        if ch == "'":
            j = command.find("'", i + 1)
            if j < 0:
                raise Unclassifiable("the command carries an unterminated single quote")
            buf.append(command[i + 1:j])
            quoted[0] = True
            i = j + 1
            continue
        if ch == '"':
            j, parts = i + 1, []
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n:
                    parts.append(command[j + 1])
                    j += 2
                    continue
                parts.append(command[j])
                j += 1
            if j >= n:
                raise Unclassifiable("the command carries an unterminated double quote")
            buf.append("".join(parts))
            quoted[0] = True
            i = j + 1
            continue
        if ch in " \t":
            flush()
            i += 1
            continue
        if ch in ";&|<>\n()":
            if ch == ">" and buf and "".join(buf).isdigit():
                # `2>file`: the digits are the file descriptor, not a word.
                del buf[:]
                quoted[0] = False
            else:
                flush()
            op = ch
            if ch in "&|<>" and i + 1 < n and command[i + 1] == ch:
                op = ch * 2
                i += 1
            elif ch == ">" and i + 1 < n and command[i + 1] == "|":
                op = ">|"
                i += 1
            tokens.append(Token(op, False, True))
            i += 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return tokens


def split_commands(tokens):
    """The token stream as a list of simple commands, split on the operators
    that end one. An empty segment (`;;`, a trailing `&&`) is dropped rather
    than reported: an empty command writes nothing."""
    segments, current = [], []
    for t in tokens:
        if t.is_op and t.text in SEPARATORS:
            if current:
                segments.append(current)
            current = []
            continue
        current.append(t)
    if current:
        segments.append(current)
    return segments


# ---------------------------------------------------------------------------
# What a simple command writes.
# ---------------------------------------------------------------------------

class Candidate(object):
    """One path this command may write, and the construct that says so.
    `write` is False for a path that is merely MENTIONED, which is never a
    refusal and is the source of the honest fail-open warning."""

    def __init__(self, raw, why, write):
        self.raw = raw
        self.why = why
        self.write = write


_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_flag(text):
    return text.startswith("-") and text != "-"


def _words_after_name(segment):
    """The non-flag argument words of a simple command, in order, with the
    leading environment assignments and the command name removed."""
    words = [t for t in segment if not t.is_op]
    k = 0
    while k < len(words) and _ASSIGNMENT.match(words[k].text):
        k += 1
    if k >= len(words):
        return []
    return [t.text for t in words[k + 1:] if not _is_flag(t.text)]


def _flags(segment):
    words = [t for t in segment if not t.is_op]
    return [t.text for t in words if _is_flag(t.text)]


def command_name(segment):
    """The basename of the executable a simple command runs, or "" when the
    segment names none (a bare redirection, or an assignment only)."""
    words = [t for t in segment if not t.is_op]
    k = 0
    while k < len(words) and _ASSIGNMENT.match(words[k].text):
        k += 1
    if k >= len(words):
        return ""
    return os.path.basename(words[k].text.strip())


def _rest(words, skip):
    return words[skip:] if len(words) > skip else []


def _sed_targets(words, flags):
    """The files an in-place sed rewrites, or none when it is not in place.

    In place is `-i`, `-i.bak`, or `--in-place`. The script is the first
    non-flag word UNLESS `-e`/`-f` already consumed it, which is why both
    shapes are handled rather than the common one only."""
    inplace = any(f == "--in-place" or f.startswith("--in-place=")
                  or _is_inplace_flag(f) for f in flags)
    if not inplace:
        return []
    scripted = any(f in ("-e", "-f", "--expression", "--file")
                   or f.startswith("--expression=") or f.startswith("--file=")
                   for f in flags)
    return list(words) if scripted else _rest(words, 1)


def _dd_targets(segment):
    out = []
    for t in segment:
        if not t.is_op and t.text.startswith("of="):
            out.append(t.text[3:])
    return out


def _git_targets(words):
    """The paths a git subcommand writes into the working tree. Only the
    subcommands that touch tracked files directly; `git commit` and friends
    write history, which the Stop reconciler judges from the changes
    themselves."""
    if not words:
        return []
    sub = words[0]
    if sub in ("rm", "mv", "restore", "checkout", "clean", "apply", "stash"):
        return _rest(words, 1)
    return []


#: name -> a function from (non-flag words, flags) to the paths it writes.
#: Every entry is a command whose write target is READABLE FROM ITS ARGUMENTS.
#: A command that writes through a path it computes at run time is outside
#: this table by construction, which is the limit stated in the module
#: docstring rather than a gap nobody mentioned.
WRITE_COMMANDS = {
    "rm": lambda w, f: list(w),
    "unlink": lambda w, f: list(w),
    "shred": lambda w, f: list(w),
    "mv": lambda w, f: list(w),          # both sides: a rename writes both names
    "cp": lambda w, f: _rest(w, 1),
    "install": lambda w, f: _rest(w, 1),
    "tee": lambda w, f: list(w),
    "truncate": lambda w, f: list(w),
    "touch": lambda w, f: list(w),
    "mkdir": lambda w, f: list(w),
    "rmdir": lambda w, f: list(w),
    "ln": lambda w, f: list(w),          # the link name and its target both
    "chmod": lambda w, f: _rest(w, 1),
    "chown": lambda w, f: _rest(w, 1),
    "chgrp": lambda w, f: _rest(w, 1),
    "sed": _sed_targets,
    "gsed": _sed_targets,
}

#: Interpreters whose `-c` (or `-e`) argument is SHELL, so the scanner runs
#: again on it rather than guessing.
SHELL_INTERPRETERS = frozenset(("sh", "bash", "zsh", "dash", "ksh"))

#: Interpreters whose `-c`/`-e` argument is code in another language. Their
#: payload is scanned for a write marker plus string literals, which is a
#: weaker instrument and is described as one.
CODE_INTERPRETERS = frozenset((
    "python", "python2", "python3", "perl", "ruby", "node", "php"))

#: Substrings that mean the payload of a code interpreter writes something.
#: Deliberately generous: this list only decides whether the payload's string
#: literals become CANDIDATES, and a candidate still has to canonicalize onto
#: a protected family before anything is refused.
WRITE_MARKERS = (
    ".write(", "write_text", "write_bytes", "writefile", "writefilesync",
    "os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir", "os.truncate",
    "shutil.", ".unlink(", ".rename(", ".truncate(", "file.write",
    "io.write", "fileutils", ".puts", "file_put_contents", "unlink(",
)

#: `open(path, 'w')` and its relatives. A bare `open(` is NOT a write marker
#: and must not be: `print(open('CLAUDE.md').read())` reads a protected file
#: and writes nothing, and refusing it would teach the operator that this
#: guard cannot tell a read from a write, which is the fastest way to get a
#: guard switched off. The MODE is what says write, so the mode is what is
#: matched.
_WRITE_MODE_OPEN = re.compile(
    r"open\s*\([^)]*,\s*['\"][^'\"]*[wax+][^'\"]*['\"]", re.I)

_STRING_LITERAL = re.compile(r"'([^']*)'|\"([^\"]*)\"")


def _payload_of(segment, flag_names):
    """The argument of the first of `flag_names` in this simple command, or
    "" when the flag is absent or carries nothing."""
    words = [t for t in segment if not t.is_op]
    for idx, t in enumerate(words):
        if t.text in flag_names and idx + 1 < len(words):
            return words[idx + 1].text
    return ""


def _code_payload_candidates(payload, why):
    """Every string literal in a code interpreter's payload, as candidates,
    when the payload carries a write marker. Without a marker the literals
    are mentions only.

    This is the weakest instrument in the file and it is the one the spec
    asks for by name (fixture 2, `python3 -c open(...)`). It cannot see a
    path built by concatenation, read from an environment variable, or
    decoded at run time. That is stated here and again in the module
    docstring; the Stop reconciler is what actually covers those."""
    low = payload.lower()
    writing = any(m in low for m in WRITE_MARKERS) or bool(_WRITE_MODE_OPEN.search(payload))
    out = []
    for m in _STRING_LITERAL.finditer(payload):
        text = m.group(1) if m.group(1) is not None else m.group(2)
        if text and text.strip():
            out.append(Candidate(text.strip(), why, writing))
    return out


def segment_candidates(segment, depth):
    """Every path this simple command may write, plus every path it merely
    mentions. Recurses into `sh -c`, bounded by MAX_DEPTH."""
    out = []
    # Redirections first: they belong to whatever command they are attached to,
    # including a command this file has never heard of, which is precisely how
    # `printf ... > CLAUDE.md` is caught without knowing anything about printf.
    tokens = list(segment)
    for idx, t in enumerate(tokens):
        if t.is_op and t.text in WRITE_REDIRECTS and idx + 1 < len(tokens):
            nxt = tokens[idx + 1]
            if not nxt.is_op:
                out.append(Candidate(nxt.text, "shell redirection %s" % t.text, True))

    name = command_name(segment)
    words = _words_after_name(segment)
    flags = _flags(segment)

    handler = WRITE_COMMANDS.get(name)
    if handler is not None:
        for p in handler(words, flags):
            out.append(Candidate(p, "%s, whose arguments name what it writes" % name, True))
    elif name == "dd":
        for p in _dd_targets(segment):
            out.append(Candidate(p, "dd of=", True))
    elif name == "git":
        for p in _git_targets(words):
            out.append(Candidate(p, "git %s" % (words[0] if words else ""), True))
    elif name in SHELL_INTERPRETERS and depth < MAX_DEPTH:
        payload = _payload_of(segment, ("-c",))
        if payload:
            try:
                for seg in split_commands(tokenize(payload)):
                    out.extend(segment_candidates(seg, depth + 1))
            except Unclassifiable as e:
                raise Unclassifiable(
                    "the %s -c payload could not be read (%s)" % (name, e))
    elif name in CODE_INTERPRETERS:
        payload = _payload_of(segment, ("-c", "-e"))
        if payload:
            out.extend(_code_payload_candidates(
                payload, "a %s -c/-e payload carrying a write marker" % name))
        inplace = any(_is_inplace_flag(f) for f in flags)
        if inplace:
            for p in words:
                out.append(Candidate(p, "%s -i, an in-place rewrite" % name, True))

    # Everything else the command names is a MENTION. Not a refusal; the source
    # of the honest warning that this guard saw a protected path go by inside a
    # command it did not classify as a write.
    written = set(c.raw for c in out if c.write)
    for t in segment:
        if t.is_op or t.text in written or _ASSIGNMENT.match(t.text):
            continue
        out.append(Candidate(t.text, "mentioned in the command", False))
    return out


# ---------------------------------------------------------------------------
# Cross-project execution detection (ADR 2026-08-12, change 3: "no session in
# either project runs the other's tools").
#
# WHY THIS EXISTS. Writing a protected family is not the only bypass the rest
# of this file cannot see: a command can EXECUTE a tool that belongs to a
# DIFFERENT project on this machine, and that is invisible to the write-family
# check above, because the target is never edited HERE at all. That is exactly
# the incident the ADR names: a session closing this project reached for a
# companion product's ceremony tool, and that tool wrote its output into the
# companion's own repository, mixing two projects. Nothing enforced the rule
# "a session never runs another project's tools" until this section.
#
# PROJECT ROOT MARKER, chosen and its weakness stated plainly. A directory
# counts as a project root when it directly holds ALL THREE of: a `.git`
# entry (a full checkout's directory or a worktree's file; both are checked
# the same way on purpose, since a worktree is still that project), a
# `.claude-plugin/plugin.json`, and a `PROJECT.md`. This project and the
# companion the ADR names both carry all three.
#
# Amendment 2026-08-29 (docs/adr/2026-08-12-where-the-shared-machinery-lives.md):
# this originally required only ONE of the three, reasoned as catching a
# future companion that ships with just one marker. Measured against every
# plugin installed on the machine this shipped on: 239 directories carry
# `.claude-plugin/plugin.json` alone, because every installed plugin has one;
# 32 of those also carry a `.git` a git-backed marketplace install left
# behind; only 2 -- brothermode and brothersbe, the actual companions this
# ADR is about -- carry all three together. "Any one" was refusing execution
# of 237 unrelated installed plugins' tools for a rule that was never about
# them. Requiring all three still catches both real companions (neither lost
# a marker) and drops the rest. The weakness this trades for: a future
# companion that ships without one of the three (most plausibly no
# `.claude-plugin/plugin.json`, for a product never packaged as a Claude Code
# plugin) is invisible to this check until it gains all three. That is the
# same "cannot tell, allow" rule this file already applies to a folder with
# NONE of the three (a plain archive or handover snapshot that was never a
# git repository and never got its own PROJECT.md, of which several exist
# under this machine's Documents folder) -- extended, on measurement, to a
# folder with only one or two. Running a tool out of such a folder is NOT
# refused. It is why this section is a control against NAMED, MARKED
# projects, not a sweep of the whole filesystem.
#
# WHAT COUNTS AS EXECUTING, text only, the same limit the rest of this file
# states about itself. Two shapes: the command NAME is itself a path (it
# contains a separator: `./bin/z`, `bin/z`, `/other/project/bin/z`), or a
# known interpreter (SHELL_INTERPRETERS or CODE_INTERPRETERS) is given a
# script argument rather than an inline `-c`/`-e` payload. A path that is
# merely an ARGUMENT to an ordinary command (`cat /other/project/README.md`,
# `grep -r x /other/project`, `git -C /other/project log`) is not executed by
# that command and is never a candidate here, which is the property that
# keeps reading another project's files ordinary. A command reached only
# through PATH lookup with no separator in its own name (`node`, `env`), or an
# interpreter wrapped in another launcher this file does not know, is outside
# what text matching can see and is allowed, exactly like every other limit
# this file states about itself rather than hides.
#
# READ-ONLY VERBS, a narrow exemption, amendment 2026-08-29. A command that
# executes another project's tool is still refused unconditionally UNLESS the
# tool is named in `_READ_ONLY_VERBS_BY_BASENAME` below, the marker root's own
# `.claude-plugin/plugin.json` declares the exact plugin name that entry
# expects, AND the command's first argument (its subcommand) is one of the
# verbs listed for it. That table is small and explicit on purpose: it is
# companion-specific knowledge living in this guard, the exact coupling the
# ADR's "one implementation per behaviour" criterion warns about, kept
# deliberately tiny (one tool, two verbs) rather than a general read/write
# classifier of another project's CLI. Every write verb (`start`, `deliver`,
# `task *`, `forecast add`, `review`, `receipt add`, `alert raise`/`resolve`,
# `export`, `purge`) stays refused exactly as ratified; only the two verbs
# proven read-only in the companion's own source (bm_project.py:298,
# `_read_store()` builds a `bs.ReadOnlyStore` specifically so these "cannot
# rewrite the store" they read) are exempted.
#
# THE IDENTITY CHECK, second pass after independent review. Checking only the
# executed file's basename and the marker root's shape let this file's own
# test prove the gap: a file merely NAMED bm_project.py, written into any
# directory carrying all three markers, got exempted with nothing checked
# about what it actually was. `_plugin_declared_name` closes that specific
# hole by requiring the root's own manifest to say it is the expected plugin.
# What it does NOT close, named rather than hidden: a genuine but STALE copy
# of the real companion (an old backup checkout, of which several already
# exist on this machine's Documents folder) still passes the name check and
# is not proven to be running the 2026-08-11 read-only fix this exemption
# otherwise rests on. Accepted for now because the exposure is narrow: the
# worst case is that specific tool's OWN store migrating on its OWN root, the
# same effect a legitimate session rooted there would produce running it
# directly, never a write landing in the CALLING project. Revisit if a
# version-checkable marker becomes cheap to add on the companion's side.
# ---------------------------------------------------------------------------

#: Markers checked directly inside a candidate directory. All three must be
#: present together (amendment 2026-08-29); see the "PROJECT ROOT MARKER"
#: paragraph above for why.
PROJECT_MARKERS = (".git", os.path.join(".claude-plugin", "plugin.json"), "PROJECT.md")

#: Every interpreter this file already recognizes, shell and code alike, so
#: the exec-target detector reuses the same two sets rather than keeping a
#: third list that could drift from them.
_ALL_INTERPRETERS = SHELL_INTERPRETERS | CODE_INTERPRETERS

#: A walk this deep covers any real path on this machine many times over; the
#: cap exists so a pathological input cannot spin, the same role MAX_DEPTH
#: plays for the `sh -c` recursion above.
MAX_MARKER_WALK_DEPTH = 64


def _first_word_raw(segment):
    """The verbatim text of a simple command's first real word (the
    executable), with any leading environment assignments skipped, or None for
    a segment that names none. Unlike `command_name`, this keeps the original
    path spelling rather than its basename, because that spelling is exactly
    what has to be resolved against `cwd` to know what the word points at."""
    words = [t for t in segment if not t.is_op]
    k = 0
    while k < len(words) and _ASSIGNMENT.match(words[k].text):
        k += 1
    if k >= len(words):
        return None
    return words[k].text


def _exec_target_raw(segment):
    """The raw path this simple command EXECUTES, paired with the word
    immediately after it (its subcommand, for a CLI tool that reads one as
    argv[1], e.g. bm_project.py's `next`/`start`), as a (raw, subcommand)
    pair. Both are None when the segment names no executed path. See the
    section docstring above for the two shapes this reads; the subcommand
    sits at a different index in each shape, mirrored here rather than
    re-derived by a second walk over the same words."""
    raw0 = _first_word_raw(segment)
    if raw0 is None:
        return None, None
    if "/" in raw0 or "\\" in raw0:
        after = _words_after_name(segment)
        return raw0, (after[0] if after else None)
    if os.path.basename(raw0) in _ALL_INTERPRETERS:
        flags = _flags(segment)
        if any(f in ("-c", "-e", "-m") for f in flags):
            # `-c`/`-e` carry an inline payload, not a script file. `-m` names
            # an importable MODULE (`python3 -m pytest`), not a path on disk,
            # and treating its argument as a file would be a false positive
            # this file's own bias says to avoid: `pytest` read as a relative
            # path could resolve into whatever project the working directory
            # happens to be in and be refused as "another project's tool"
            # when nothing at that path was ever going to run.
            return None, None
        words = _words_after_name(segment)
        if words:
            return words[0], (words[1] if len(words) > 1 else None)
    return None, None


def _resolve_abs_path(raw, cwd):
    """The realpath of a possibly-relative word, resolved against `cwd`
    exactly as `sbe_fence_hook.canonical_target` resolves a write target, or
    None when the word is unusable. Symlinks are resolved so a link inside
    this project pointing at a file inside this project reads as this
    project's own, and a nonexistent trailing component is left literal so a
    script that has not been written yet still resolves."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = _msys_path_to_windows(raw)
    base = raw if os.path.isabs(raw) else os.path.join(cwd, raw)
    try:
        return os.path.realpath(base)
    except (ValueError, OSError):
        return None


def _project_marker_root(start_dir):
    """The realpath of the nearest ancestor of `start_dir` (inclusive) that
    carries EVERY PROJECT_MARKERS entry at once, or None when no such
    ancestor is found within MAX_MARKER_WALK_DEPTH levels or the filesystem
    root is reached first. See the section docstring's "PROJECT ROOT MARKER"
    paragraph for why all three are required rather than any one.

    None is the NO-DATA answer, not a negative one: it means this walk could
    not tell whether `start_dir` belongs to a marked project, and the caller
    treats that exactly like every other undeterminable case in this file, by
    allowing."""
    d = os.path.realpath(start_dir)
    for _ in range(MAX_MARKER_WALK_DEPTH):
        if all(os.path.exists(os.path.join(d, marker)) for marker in PROJECT_MARKERS):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def _own_plugin_root():
    """The realpath of the root of the installation THIS guard belongs to: two
    levels up from this file, since the guard ships at
    `<plugin root>/tools/sbe_bash_write_guard.py`. Resolves correctly whether
    this guard was installed through the plugin cache or from a local
    marketplace source, because it is derived from `__file__` at run time
    rather than from a fixed cache path."""
    try:
        return os.path.realpath(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except (ValueError, OSError):
        return None


def _installed_plugin_roots():
    """The roots under which a tool is part of THIS GUARD'S OWN installed
    plugin, as a tuple with the unresolvable case dropped (so at most one
    element).

    WHY THIS EXEMPTION EXISTS AT ALL. This guard's own plugin carries a
    `.claude-plugin/plugin.json`, which is one of PROJECT_MARKERS, so the
    marker walk used to read this plugin's own `bin/sbe` as a DIFFERENT
    project's tool and refuse it. Every step of this plugin's entry commands
    runs through that binary, so without this exemption the front door dies
    on a clean install.

    Amendment 2026-08-29 (second pass, after independent review, live-tested):
    this used to also exempt the entire plugin cache directory
    (`$CLAUDE_CONFIG_DIR/plugins/cache`), reasoning that "plugin hooks judge
    every command and not only their own plugin's" -- and a SIBLING plugin's
    tools, not only this one's, would otherwise be refused. Measured live:
    with that half in place, `bm_project.py start`, invoked from a foreign
    project via its real installed plugin-cache path exactly as brotherme's
    own SKILL.md documents, was ALLOWED, because the cache-root check ran
    before the marker walk and the read-only-verb table ever saw it. That is
    not a narrow gap; it is this guard's ADR-change-3 protection going dead
    for every companion product actually installed as a plugin, which is the
    normal way one is installed. The cache-wide half is removed. A sibling
    plugin OTHER than a genuine companion (i.e. one that does not carry all
    three PROJECT_MARKERS together) is unaffected: `_project_marker_root`
    already answers NO-DATA for it and this file allows what it cannot tell,
    the same property that used to require the cache-wide half is now
    produced by the marker-strictness fix in the same amendment. A sibling
    that IS a genuine companion (brothermode today) goes back through the
    marker walk and the read-only-verb table exactly like any other
    cross-project execution, which is the ADR's own rule working as ratified.

    WHAT IT STILL REFUSES, which is the point: any tool that is not this
    guard's own. A sibling companion's write verb, a real checkout with a
    `.git`, and a directory carrying a `plugin.json` that was never installed
    are all still refused (or, for the last one, undeterminable and allowed
    on that basis, never on this one)."""
    root = _own_plugin_root()
    return (root,) if root else ()


def cross_project_reason(raw_exec, abs_path, other_root, command):
    return (
        "BrotherSBE Bash write guard: this command executes %s, which resolves to "
        "%s, a tool that belongs to a different project rooted at %s. This "
        "project's rule (docs/adr/2026-08-12-where-the-shared-machinery-lives.md, "
        "change 3: no session in either project runs the other's tools) exists "
        "because a session closing this project once reached for a companion "
        "product's ceremony tool, and that tool wrote its output into the "
        "companion's own repository, mixing the two.\n"
        "The command, verbatim:\n"
        "  %s\n"
        "Do this instead: open a session in %s and run the tool from there. A "
        "ceremony tool belongs to the project whose repository it writes into.\n"
        "This guard did NOT prove anything about the rest of the command."
        % (raw_exec, abs_path, other_root, command[:400], other_root))


class CrossProjectHit(object):
    """One command segment caught executing another project's tool, or the
    NO-DATA note explaining why nothing was decided. An object rather than a
    tuple for the same reason `Decision` and `_LoadResult` are objects in this
    file: a 2-or-3-tuple return reads as a possible verdict source to this
    project's honesty meta-test, and this is not one."""

    def __init__(self, raw, abs_path, other_root, no_data_note):
        self.raw = raw
        self.abs_path = abs_path
        self.other_root = other_root
        self.no_data_note = no_data_note


#: Amendment 2026-08-29. Tool basename -> (the `.claude-plugin/plugin.json`
#: `name` the marker root must declare, the subcommands proven read-only in
#: that tool's OWN source, never guessed here): each one opens a read-only
#: store handle and cannot write. See the "READ-ONLY VERBS" paragraph in the
#: section docstring above for why this stays this small rather than growing
#: into a general classifier of another project's CLI. Second pass, after
#: independent review: this used to key on basename alone, and this file's
#: own test proved a file merely NAMED bm_project.py, anywhere under a
#: directory carrying all three markers, got exempted with nothing checked
#: about what it actually was. `_plugin_declared_name` below reads the
#: marker root's own manifest so the exemption requires the plugin to say
#: who it is, not merely a file to share a name with the real tool.
_READ_ONLY_VERBS_BY_BASENAME = {
    # bm_project.py:298 `_read_store()` builds a `bs.ReadOnlyStore`, added
    # 2026-08-11 specifically so these two "cannot rewrite the store" they
    # read. Every other bm_project.py verb (start, deliver, task *, forecast
    # add, review, receipt add, alert raise/resolve, export, purge) opens the
    # write-capable `_store()` and stays refused.
    "bm_project.py": ("brothermode", frozenset(("status", "next"))),
}


def _plugin_declared_name(marker_dir):
    """The `name` field of `marker_dir`'s own `.claude-plugin/plugin.json`,
    or None on any read or parse problem, or when the field is missing or not
    a string. Used only to confirm a tool granted the read-only-verb
    exemption actually belongs to the plugin it claims to be, not merely a
    file sharing its basename under a marked root: an unreadable or
    unparseable manifest, or a name mismatch, falls back to refusing the
    command like any other unrecognized tool, never to exempting it. This is
    a stricter default than the rest of this file's "cannot tell, allow"
    rule, deliberately: that rule governs whether a command is a candidate at
    all, this one governs whether an already-identified candidate earns a
    narrower refusal, and a read failure here must not silently widen it."""
    path = os.path.join(marker_dir, ".claude-plugin", "plugin.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def cross_project_candidate(segment, root_real, cwd):
    """The CrossProjectHit for this segment, or None when it executes nothing
    outside `root_real`, INCLUDING when it executes a verb named in
    `_READ_ONLY_VERBS_BY_BASENAME` for the target tool. `root_real` is
    trusted as this project's own root without re-walking for its markers: it
    is the boundary every other check in this file already treats as
    authoritative, and re-deriving it here would be a second copy of a
    decision this file makes exactly once."""
    raw, subcommand = _exec_target_raw(segment)
    if raw is None:
        return None
    abs_path = _resolve_abs_path(raw, cwd)
    if abs_path is None:
        return None
    if abs_path == root_real or abs_path.startswith(root_real + os.sep):
        return None
    for install_root in _installed_plugin_roots():
        if abs_path == install_root or abs_path.startswith(install_root + os.sep):
            return None
    marker_dir = _project_marker_root(os.path.dirname(abs_path))
    if marker_dir is None:
        return CrossProjectHit(raw, abs_path, None, (
            "sbe_bash_write_guard: %s resolves to %s, outside this project, and none of "
            "its ancestors carries all three project markers together (.git, "
            ".claude-plugin/plugin.json, PROJECT.md) -- some may carry one or two, which "
            "is not enough -- so this guard could not tell whether it belongs to another "
            "project. Allowed." % (raw, abs_path)))
    if marker_dir == root_real:
        return None
    exemption = _READ_ONLY_VERBS_BY_BASENAME.get(os.path.basename(abs_path))
    if exemption is not None:
        expected_name, safe_verbs = exemption
        if subcommand in safe_verbs and _plugin_declared_name(marker_dir) == expected_name:
            return None
    return CrossProjectHit(raw, abs_path, marker_dir, None)


# ---------------------------------------------------------------------------
# The decision.
# ---------------------------------------------------------------------------

def deny_payload(reason):
    """The deny object, shaped exactly as the PreToolUse contract requires and
    exactly as both sibling hooks already emit it, because all three share one
    wire contract."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def refusal_reason(rel, family, why, command, live_tasks):
    if live_tasks:
        names = ", ".join(sorted(str(t.get("id") or "(unnamed task)") for t in live_tasks))
        task_state = ("%d open task(s) exist (%s) and none declares %s in its ownedPaths"
                      % (len(live_tasks), names, rel))
    else:
        task_state = "no task is open, so nothing declares this path"
    return (
        "BrotherSBE Bash write guard: this command writes %s, which is a protected "
        "control-plane or authority path (%s). It was identified through %s. %s.\n"
        "The command, verbatim:\n"
        "  %s\n"
        "Writing a protected file through Bash is the bypass this guard exists to "
        "stop: the Edit and Write hooks never see it, so the change would land with "
        "no declaration behind it.\n"
        "Any of these lets the work proceed, and nothing else does:\n"
        "  1. Declare the path first: sbe task open --id <id> --agent <agent> --role "
        "writer --base $(git rev-parse HEAD) --verify <command> --owns %s\n"
        "  2. Make the change through Edit or Write instead, so the authority guard "
        "and the fence hook can judge it against the same declaration.\n"
        "  3. If this is a deliberate, reviewed exception, record it in "
        ".sbe/break-glass.json with a reason, an owner, an expiry and an approver, "
        "which the Stop reconciler reads and names in its report.\n"
        "This guard did NOT prove anything about the rest of the command."
        % (rel, family, why, task_state, command[:400], rel))


class Decision(object):
    """The hook's answer. An object, not a (verdict, evidence) pair, for the
    reason both sibling hooks state on their own Decision: this project's
    honesty meta-test reads a 2-tuple return as a possible verdict source, and
    a permission decision is not a check verdict.

    `payload` is None for ALLOW and for FAIL-OPEN, deliberately the same
    value, so no failure path can produce a deny by accident."""

    def __init__(self, payload, notes):
        self.payload = payload
        self.notes = notes


def extract_command(tool_input):
    """The command string out of a Bash tool_input, or "" when there is none.
    `command` is the documented field; `cmd` is accepted too because a payload
    that carries the shell under another name is still a shell command, and
    reading none of it would be a silent allow."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "cmd"):
        v = tool_input.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def decide(payload):
    """Return a Decision. `payload` is the parsed PreToolUse hook JSON."""
    notes = []
    try:
        if not isinstance(payload, dict):
            raise OpenFail("hook payload was not a JSON object")
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or tool_name != "Bash":
            # Not the tool this guard governs. Silent, matching both sibling
            # hooks: a stderr line per call would be noise the operator learns
            # to ignore.
            return Decision(None, [])

        if os.environ.get(DISABLE_ENV, "").strip() not in ("", "0"):
            return Decision(None, [
                "sbe_bash_write_guard: %s is set, so this Bash command was NOT checked "
                "and is allowed. Unset it to restore the write boundary." % DISABLE_ENV])

        command = extract_command(payload.get("tool_input"))
        if not command:
            raise OpenFail(
                "no command string found in tool_input for Bash (keys present: %s)"
                % (", ".join(sorted(str(k) for k in payload.get("tool_input") or {}))
                   or "none"))

        cwd = payload.get("cwd")
        cwd = cwd.strip() if isinstance(cwd, str) and cwd.strip() else os.getcwd()
        root = payload.get("project_dir")
        root = root.strip() if isinstance(root, str) and root.strip() else cwd

        fence_mod = require_fence_module()
        surface_mod = require_surface_module()
        authority_mod = require_authority_module()
        tasks_mod = require_tasks_module()

        try:
            data = tasks_mod.load_registry(root)
        except tasks_mod.RegistryUnusable as e:
            raise OpenFail(
                "the task registry could not be read (%s), so this session's declared "
                "scope is unknown" % e)
        live = tasks_mod.open_tasks(data)

        try:
            segments = split_commands(tokenize(command))
        except Unclassifiable as e:
            raise OpenFail(
                "this Bash command could not be split into commands and words (%s), so "
                "NOTHING about it was examined and it is allowed unexamined" % e)

        # Cross-project execution check, ahead of the write-family check: it is
        # a categorical refusal by name, not a scope question the task
        # registry's ownedPaths could ever answer, since ownedPaths declares
        # paths inside THIS project and another project's tool is never one.
        root_real = os.path.realpath(root)
        for seg in segments:
            hit = cross_project_candidate(seg, root_real, cwd)
            if hit is None:
                continue
            if hit.other_root is None:
                notes.append(hit.no_data_note)
                continue
            return Decision(
                deny_payload(cross_project_reason(
                    hit.raw, hit.abs_path, hit.other_root, command)),
                notes)

        candidates = []
        for seg in segments:
            try:
                candidates.extend(segment_candidates(seg, 0))
            except Unclassifiable as e:
                notes.append(
                    "sbe_bash_write_guard: part of this command could not be read (%s), "
                    "so that part was NOT examined and is allowed unexamined." % e)

        mentioned = []
        for c in candidates:
            rel = fence_mod.canonical_target(root, c.raw, cwd)
            if rel is None:
                # Outside the project root. This guard governs a project, not
                # the filesystem, exactly as both sibling hooks do.
                continue
            family = protected_family(fence_mod, surface_mod, authority_mod, root, rel)
            if not family:
                continue
            declared = any(
                fence_mod.paths_overlap(rel, claim, root)
                for t in live for claim in (t.get("ownedPaths") or []))
            if declared:
                notes.append(
                    "sbe_bash_write_guard: %s is protected (%s) and IS declared in an "
                    "open task's ownedPaths, so this command is allowed. The Stop "
                    "reconciler still judges what the command actually changed."
                    % (rel, family))
                continue
            if c.write:
                return Decision(
                    deny_payload(refusal_reason(rel, family, c.why, command, live)),
                    notes)
            mentioned.append((rel, family))

        for rel, family in mentioned:
            # Spec 1.3 rules 6, 7 and 8 in one line: this is the fail-open path,
            # it is loud, and it never claims the command was read only.
            notes.append(
                "sbe_bash_write_guard: %s is a protected path (%s) and appears in a "
                "Bash command this guard did NOT classify as a write, so the command "
                "is ALLOWED. That is not a claim that the command is read only: this "
                "guard reads command text, and text cannot show what a script, a "
                "compiled tool or a computed path will do. The Stop reconciler "
                "(tools/sbe_session_reconcile.py) is the authoritative check."
                % (rel, family))
        return Decision(None, notes)
    except OpenFail as e:
        notes.append(
            "sbe_bash_write_guard: FAILING OPEN, the command is allowed and the write "
            "boundary was NOT checked. This is not a claim that the command is read "
            "only. Reason: %s" % e)
        return Decision(None, notes)
    except Exception as e:
        # The blanket catch is the point, not laziness: an unforeseen bug in
        # this file must never become a refusal in front of every Bash call.
        notes.append(
            "sbe_bash_write_guard: FAILING OPEN after an unexpected error, the command "
            "is allowed and the write boundary was NOT checked. Reason: %s: %s"
            % (type(e).__name__, e))
        return Decision(None, notes)


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------

class StdinPayload(object):
    """The parsed hook payload, or the reason there is none. An object rather
    than a pair, for the reason stated on Decision."""

    def __init__(self, payload, error):
        self.payload = payload
        self.error = error


def read_stdin_json():
    """A StdinPayload. Never raises: unreadable stdin is a fail-open, not a
    crash in front of every Bash call."""
    try:
        raw = sys.stdin.read()
    except Exception as e:
        return StdinPayload(
            None, "stdin could not be read (%s: %s)" % (type(e).__name__, e))
    if not raw or not raw.strip():
        return StdinPayload(None, "stdin was empty")
    try:
        return StdinPayload(json.loads(raw), None)
    except ValueError as e:
        return StdinPayload(
            None, "stdin was not valid JSON (%s: %s)" % (type(e).__name__, e))


def cmd_hook(argv):
    parsed = read_stdin_json()
    if parsed.error is not None:
        _warn("sbe_bash_write_guard: FAILING OPEN, the command is allowed and the write "
              "boundary was NOT checked. Reason: %s" % parsed.error)
        return 0
    _rs = load_repo_scope_module()
    # write_guard=True: this hook decides whether a write happens, and
    # .brother/config arrives with the repository, so it may not switch this
    # one off (security review 2026-09-04, Major; sbe_repo_scope's
    # WRITE_GUARD_HOOKS). Still called rather than skipped, so the
    # once-per-session notice and the garbage-config diagnostic are printed.
    if _rs is not None and _rs.hooks_off(payload=parsed.payload,
                                         write_guard=True):
        return 0
    decision = decide(parsed.payload)
    for n in decision.notes:
        _warn(n)
    if decision.payload is not None:
        _out(json.dumps(decision.payload))
    return 0


def cmd_classify(argv):
    """Diagnostics: what this guard reads out of one command line, on stderr
    only, matching the `fences` and `surfaces` subcommands of the sibling
    hooks. Nothing on stdout, because stdout is the decision channel."""
    if not argv or argv[0].startswith("-"):
        _warn(BASH_GUARD_USAGE)
        _warn("sbe_bash_write_guard classify: give one command line to classify")
        return 2
    command = argv[0]
    root = os.path.abspath(argv[1]) if len(argv) > 1 else os.getcwd()
    try:
        fence_mod = require_fence_module()
        surface_mod = require_surface_module()
        authority_mod = require_authority_module()
    except OpenFail as e:
        _warn("this guard cannot classify anything here, so every Bash command would be "
              "ALLOWED. Reason: %s" % e)
        return 0
    try:
        segments = split_commands(tokenize(command))
    except Unclassifiable as e:
        _warn("unclassifiable (%s); the command would be ALLOWED unexamined" % e)
        return 0
    seen = 0
    for seg in segments:
        try:
            cands = segment_candidates(seg, 0)
        except Unclassifiable as e:
            _warn("a segment could not be read (%s)" % e)
            continue
        for c in cands:
            rel = fence_mod.canonical_target(root, c.raw, root)
            if rel is None:
                continue
            family = protected_family(fence_mod, surface_mod, authority_mod, root, rel)
            seen += 1
            _warn("%s %s | %s | protected: %s"
                  % ("WRITE " if c.write else "mention", rel, c.why, family or "no"))
    _warn("%d candidate path(s) read from the command under %s" % (seen, root))
    return 0


_COMMANDS = {
    "hook": cmd_hook,
    "classify": cmd_classify,
}

BASH_GUARD_USAGE = (
    "usage: sbe_bash_write_guard.py [hook|classify <command> [root]]\n"
    "  hook (or no subcommand): the Claude Code PreToolUse hook for Bash; reads one\n"
    "    JSON payload from stdin, prints its decision to stdout, and FAILS OPEN except\n"
    "    for the one refusal this file exists to make.\n"
    "  classify <command> [root]: diagnostics on stderr; the write targets and\n"
    "    mentions this guard reads out of one command line.\n"
    "  flags:\n"
    "    -h, --help        print this and exit 0 without reading stdin"
)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        # Help is not an error, exits 0 without reading stdin, and goes to
        # stderr because stdout is the decision channel.
        _warn(BASH_GUARD_USAGE)
        return 0
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    if argv and not argv[0].startswith("-"):
        _warn("sbe_bash_write_guard: unknown command %r; expected one of: %s"
              % (argv[0], ", ".join(sorted(_COMMANDS))))
        return 2
    if argv:
        # A flag this surface does not know is refused, never dropped and read
        # as a bare hook invocation with a stray argument.
        _warn(BASH_GUARD_USAGE)
        _warn("sbe_bash_write_guard: unrecognized flag %r; refusing rather than reading "
              "it as a hook invocation" % argv[0])
        return 2
    # No subcommand is the HOOK invocation, because that is how Claude Code
    # calls it: a bare command with a JSON payload on stdin.
    return cmd_hook(argv)


if __name__ == "__main__":
    sys.exit(main())
