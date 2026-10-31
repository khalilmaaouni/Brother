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
import itertools
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
    nothing about it was examined. `soft` marks a text that is merely too big
    or ends in a backslash (it may be fine); a hard one holds an unbalanced
    quote (no shell would run it). GUARD1: a hard one is refused, a soft one is
    refused only when its text names a network tool or an interpreter."""

    def __init__(self, message, soft=False):
        Exception.__init__(self, message)
        self.soft = soft


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


def require_common_module():
    """`tools/sbe_guard_common.py`, for `confirmed_surface` and
    `CONTROL_PLANE_PATTERNS`: the small, dependency-free module this guard
    and `tools/sbe_authority_hook.py` share so the Bash channel and the
    structured-write channel can never protect two different lists or apply
    two different case-fold rules, and so this guard no longer pays a full
    sibling exec of sbe_authority_hook.py (750+ lines) just to reach the one
    function it actually used from it."""
    return require_module("sbe_guard_common_for_bash_guard",
                          os.path.join(HERE, "sbe_guard_common.py"),
                          "the case-fold-confirmed authority-surface match and the "
                          "control-plane pattern list")


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

# CONTROL_PLANE_PATTERNS used to be defined here, privately. It moved to
# tools/sbe_guard_common.py (reached through require_common_module() above)
# so this guard and tools/sbe_authority_hook.py can never protect two
# different lists: a hostile refuter proved that gap by granting itself
# CLAUDE.md through a tasks.json rewrite the other guard allowed, back when
# each side kept its own copy. See sbe_guard_common.py's own module
# docstring for the full list and why each entry is there.

#: The other half of what this guard reads, defined further down beside the
#: write-command table because it needs that section's word readers:
#: COMMAND_EFFECTS, the effects a path pattern cannot see at all.

#: What a target of "." or the repository root itself covers. A destructive
#: command aimed at the root ("rm -rf .") reaches every family above without
#: naming any of them, and a comparison that only ever matched spellings
#: would wave it through.
ROOT_FAMILY = "the repository root, which contains every protected family"

#: Repository metadata. A write to anything under `.git/` (the index, HEAD, a
#: hook) changes what the guards read about the repository, and is never a
#: stranger's task either.
GIT_FAMILY = "repository metadata under .git/"

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


def protected_family(fence_mod, surface_mod, common_mod, root, rel, writing=True):
    """The protected family `rel` belongs to, or "" for none.

    Authority families first, through the sibling that already knows how to
    confirm a case fold against the filesystem rather than trusting a folded
    string. Then every family as a containment pattern, through
    `paths_overlap`, which carries the identical confirmation for globs,
    directory prefixes and case."""
    if rel in (".", ""):
        return ROOT_FAMILY
    if writing and any(part.lower() == ".git" for part in rel.split("/")):
        return GIT_FAMILY
    surface = common_mod.confirmed_surface(fence_mod, surface_mod, root, rel)
    if surface:
        return surface
    for pattern in authority_patterns(surface_mod) + common_mod.CONTROL_PLANE_PATTERNS:
        if fence_mod.paths_overlap(rel, pattern, root):
            # The owner's own files are protected only in the Brother source
            # repository (GUARD1.c); in a stranger's repository `src` is a folder.
            if pattern in common_mod.OWNER_PATTERNS and not (
                    writing and common_mod.is_brother_source(root)):
                continue
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
        #: Only on a `<<` operator: the here-document body that follows the
        #: line it sits on. DATA, never split into commands and words.
        self.body = ""
        #: The word with what bash would not expand blanked out: the text of a
        #: single quoted part, and an escaped character, read as nothing. This
        #: is what a command substitution is searched for in.
        self.plain = text
        #: True when the here-document's delimiter was quoted (`<<'EOF'`,
        #: `<<"EOF"`, `<<\EOF`): bash then leaves the body literal.
        self.body_quoted = False


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


_ANSI_C_ESCAPES = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n",
                   "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


class Delimiter(object):
    """A here-document delimiter word after quote removal, and whether it was
    quoted. An object rather than a pair, for the reason Decision states: this
    project's honesty meta-test reads a 2-tuple return as a possible verdict
    source, and neither field here is a check verdict."""

    def __init__(self, word, quoted):
        self.word = word
        self.quoted = quoted


class ShellMode(object):
    """How a shell reads its commands ("c", "script" or "stdin") and, for "c",
    the payload. An object for the same reason as Delimiter."""

    def __init__(self, kind, payload):
        self.kind = kind
        self.payload = payload


def _unmodelled(why):
    return Unclassifiable("a here-document delimiter %s, so where its body ends is unknown" % why)


def _heredoc_tag(command, j):
    """The Delimiter (terminator word, whether it was quoted) of a here-document whose
    delimiter word starts at or after `command[j]`. Bash's quote removal is
    applied to the WHOLE word: every single quote, double quote and backslash
    anywhere in it is dropped, and the word is quoted if any was present, so
    `E'O'F`, `"E"OF`, `E\\OF` and `$'EOF'` are all the terminator EOF, quoted.
    `$'...'` is read as ANSI C text. Quoting this guard cannot model (an
    unterminated quote, an unknown `$'...'` escape) raises Unclassifiable: the
    body would otherwise be read as data from the wrong place."""
    n = len(command)
    while j < n and command[j] in " \t":
        j += 1
    word, quoted = [], False
    while j < n and command[j] not in " \t\n;&|<>()":
        ch = command[j]
        if ch == "\\" and j + 1 < n:
            word.append(command[j + 1])
            quoted = True
            j += 2
        elif ch == "'":
            k = command.find("'", j + 1)
            if k < 0:
                raise _unmodelled("carries an unterminated single quote")
            word.append(command[j + 1:k])
            quoted = True
            j = k + 1
        elif ch == "$" and command[j + 1:j + 2] == "'":
            quoted = True
            j += 2
            while j < n and command[j] != "'":
                if command[j] != "\\":
                    word.append(command[j])
                    j += 1
                    continue
                esc = command[j + 1:j + 2]
                if esc in _ANSI_C_ESCAPES:
                    word.append(_ANSI_C_ESCAPES[esc])
                    j += 2
                    continue
                m = re.compile(r"x([0-9A-Fa-f]{1,2})|([0-7]{1,3})").match(command, j + 1)
                if not m:
                    raise _unmodelled("uses an escape this guard does not model")
                word.append(chr(int(m.group(1) or m.group(2), 16 if m.group(1) else 8) & 255))
                j = m.end()
            if j >= n:
                raise _unmodelled("carries an unterminated $'...' quote")
            j += 1
        elif ch == '"' or (ch == "$" and command[j + 1:j + 2] == '"'):
            j += 1 if ch == '"' else 2
            while j < n and command[j] != '"':
                if command[j] == "\\" and command[j + 1:j + 2] in ('"', "\\", "$", "`"):
                    j += 1
                word.append(command[j])
                j += 1
            if j >= n:
                raise _unmodelled("carries an unterminated double quote")
            quoted = True
            j += 1
        else:
            word.append(ch)
            j += 1
    return Delimiter("".join(word), quoted)


def _take_bodies(command, i, heredocs):
    """Read the body of each pending here-document, starting at `command[i]`
    (the first character after the newline), and return where reading stopped.
    A body runs to the line that is exactly its tag (leading tabs ignored for
    `<<-`), or to the end of the text when that line never comes: a missing
    terminator makes the rest of the text the body, never a stray quote."""
    n = len(command)
    for tok, tag, strip, quoted in heredocs:
        lines = []
        while i < n:
            j = command.find("\n", i)
            line = command[i:j if j >= 0 else n]
            i = j + 1 if j >= 0 else n
            if (line.lstrip("\t") if strip else line) == tag:
                break
            lines.append(line)
        tok.body = "\n".join(lines)
        tok.body_quoted = quoted
    return i


def tokenize(command):
    """The command line as Tokens, or Unclassifiable naming what stopped it.
    A here-document body (the lines after `<<TAG`, `<<-TAG`, `<<'TAG'` or
    `<<"TAG"` up to the terminator) is not tokenized: it is DATA, kept on the
    `<<` token as `body` for the rules that read it."""
    if not isinstance(command, str):
        raise Unclassifiable("the command was not a string")
    command = _windows_paths_to_slashes(command)
    if len(command) > MAX_COMMAND_CHARS and ("<<" not in command
                                             or len(command) > 20 * MAX_COMMAND_CHARS):
        raise Unclassifiable(
            "the command is %d characters, past this guard's %d character ceiling"
            % (len(command), MAX_COMMAND_CHARS), soft=True)
    tokens = []
    buf = []
    pbuf = []
    quoted = [False]
    heredocs = []
    i, n = 0, len(command)

    def flush():
        if buf:
            tokens.append(Token("".join(buf), quoted[0], False))
            tokens[-1].plain = "".join(pbuf)
            del buf[:]
            del pbuf[:]
            quoted[0] = False

    while i < n:
        if len(tokens) > MAX_TOKENS:
            raise Unclassifiable(
                "the command holds more than %d tokens, past this guard's ceiling"
                % MAX_TOKENS, soft=True)
        ch = command[i]
        if ch == "#" and not buf and not quoted[0]:
            # A comment runs to the end of the line and nothing in it is a
            # quote; the newline itself is kept (it ends the command).
            j = command.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "\\":
            if i + 1 >= n:
                raise Unclassifiable("the command ends in a trailing backslash", soft=True)
            buf.append(command[i + 1])
            pbuf.append("\x01")
            quoted[0] = True
            i += 2
            continue
        if ch == "'":
            j = command.find("'", i + 1)
            if j < 0:
                raise Unclassifiable("the command carries an unterminated single quote")
            buf.append(command[i + 1:j])
            pbuf.append("\x01")
            quoted[0] = True
            i = j + 1
            continue
        if ch == '"':
            j, parts, pparts = i + 1, [], []
            while j < n and command[j] != '"':
                if command[j] == "\\" and j + 1 < n:
                    parts.append(command[j + 1])
                    pparts.append("\x01" if command[j + 1] in "$`" else command[j + 1])
                    j += 2
                    continue
                if command[j] == "$" and command[j + 1:j + 2] == "(":
                    end = _scan_substitution(command, j + 2)
                    if end < n:
                        # The whole `$( ... )`, quotes and all: bash reads it as
                        # its own command, so a quote inside it does not end this one.
                        parts.append(command[j:end + 1])
                        pparts.append(command[j:end + 1])
                        j = end + 1
                        continue
                parts.append(command[j])
                pparts.append(command[j])
                j += 1
            if j >= n:
                raise Unclassifiable("the command carries an unterminated double quote")
            buf.append("".join(parts))
            pbuf.append("".join(pparts))
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
                del pbuf[:]
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
            if op == "<<" and ch == "<" and command[i:i + 1] != "<":
                strip = command[i:i + 1] == "-"
                delim = _heredoc_tag(command, i + 1 if strip else i)
                if delim.word or delim.quoted:
                    i += 1 if strip else 0
                    heredocs.append((tokens[-1], delim.word, strip, delim.quoted))
            elif op == "\n" and heredocs:
                i = _take_bodies(command, i, heredocs)
                heredocs = []
            continue
        buf.append(ch)
        pbuf.append(ch)
        i += 1
    flush()
    if len(command) - sum(len(t.body) for t in tokens if t.is_op) > MAX_COMMAND_CHARS:
        raise Unclassifiable(
            "the command is %d characters outside its here-documents, past this "
            "guard's %d character ceiling" % (len(command), MAX_COMMAND_CHARS), soft=True)
    return tokens


#: bash words that stand before the command a segment really runs. `coproc`
#: runs what follows; the rest open or close a compound command. (`time` is a
#: wrapper with options of its own, so `_argv` skips it, not this.)
_RESERVED = frozenset(("if then else elif fi do done while until for in select case esac "
                       "{ } ! coproc function").split())


def _strip_openers(seg):
    """`seg` without what bash reads before the command it runs: a case arm's
    `pattern)` or `(pattern)`, a function definition's `name()` (with or without
    `function`), `time` when a compound command follows it, and leading reserved
    words (`if`, `then`, `do`, `{`, `!`, `coproc` ...), so `then curl ...` is
    judged as `curl ...`. The body of a function or loop is already in the stream
    as ordinary segments, so it is judged as commands."""
    opens = 0
    leading = bool(seg) and seg[0].is_op and seg[0].text == "("
    for j, t in enumerate(seg):
        if t.is_op and t.text == "(":
            opens += 1
        elif t.is_op and t.text == ")":
            head = [u for u in seg[:j] if not u.is_op]
            rest = any(not u.is_op for u in seg[j + 1:])
            if opens == 1 and j >= 1 and seg[j - 1].is_op and seg[j - 1].text == "(":
                if (head and len(head) <= 2 and rest and not leading
                        and not (j >= 2 and seg[j - 2].text == "$")):
                    seg = seg[j + 1:]          # `name()` or `function name()`
                break
            if rest and ((opens == 0 and (len(head) <= 1 or (head and head[0].text == "case")))
                         or (opens == 1 and ((leading and len(head) <= 1)
                                             or (head and head[0].text == "case")))):
                seg = seg[j + 1:]              # a case arm, `x)` or `(x)`
            break
    i = 0
    while i < len(seg) and not seg[i].is_op and not seg[i].quoted:
        word = seg[i].text
        if word in _RESERVED:
            i += 2 if word == "function" else 1
        elif word == "time":
            k = i + 1
            while k < len(seg) and not seg[k].is_op and seg[k].text.startswith("-"):
                k += 1
            if k < len(seg) and not seg[k].is_op and seg[k].text in _RESERVED:
                i = k
            else:
                break
        else:
            break
    return seg[i:]


def split_commands(tokens):
    """The token stream as a list of simple commands, split on the operators
    that end one. An empty segment (`;;`, a trailing `&&`) is dropped rather
    than reported: an empty command writes nothing."""
    segments, current = [], []
    for t in tokens:
        if t.is_op and t.text in SEPARATORS:
            current = _strip_openers(current)
            if current:
                segments.append(current)
            current = []
            continue
        current.append(t)
    current = _strip_openers(current)
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
        #: True for a path a git command names as a pathspec: it may carry git's
        #: own spelling (`:(top)`, a glob) that is resolved before matching.
        self.pathspec = False


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
    # -f/--file name a script file that sed READS, and its word sits in
    # `words`; --file=script attaches it to the flag, and -e's argument is
    # inline script, so only those two spellings mean no word is skipped.
    scripted = any(f in ("-e", "--expression")
                   or f.startswith("--expression=") or f.startswith("--file=")
                   for f in flags)
    return list(words) if scripted else _rest(words, 1)


def _dd_targets(segment):
    out = []
    for t in segment:
        if not t.is_op and t.text.startswith("of="):
            out.append(t.text[3:])
    return out


#: Prefix of a pathspec written relative to the repository top (`:(top)x`,
#: `:/x`); resolved against the real top where the working folder is known.
TOP_MARK = "\x00top:"
#: Prefix of a pathspec that is a FILE of pathspecs (`--pathspec-from-file`, a
#: `--stdin` redirect); read where the working folder is known.
FILE_MARK = "\x00file:"
NUL_MARK = "\x00nul::"

#: Where the Brother source keeps the file whose tracking makes it the source, in
#: its two layouts. Named here only so an index command that rewrites the whole
#: index (read-tree, update-index --index-info) counts as touching them.
_MARKER_TOP_PATHS = ("products/brothersbe/src/brothersbe/policy.py",
                     "src/brothersbe/policy.py")


def _git_pathspec(word, prefix):
    """The path a git pathspec word names, spelled the way this guard resolves
    paths: joined to the `-C` folder when it is relative to the working folder,
    or marked TOP_MARK when it is relative to the repository top. An exclusion
    (`:!x`, `:(exclude)x`) names nothing to write and gives ""."""
    if word.startswith((":!", ":^")):
        return ""
    top = False
    if word.startswith(":/"):
        top, word = True, word[2:]
    elif word.startswith(":("):
        j = word.find(")")
        if j > 0:
            magic = word[2:j].split(",")
            if "exclude" in magic:
                return ""
            top, word = "top" in magic, word[j + 1:]
    if top:
        return TOP_MARK + word
    return os.path.join(prefix, word) if prefix else word


_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}


def _unquote_c(line):
    """A pathspec-file line as git reads it: a line in double quotes is C quoted
    (`\\NNN` octal bytes, `\\n`, `\\"`), anything else is literal. None when the
    quoting is malformed, which the caller treats as naming the marker."""
    line = line.rstrip("\r")
    if not line.startswith('"'):
        return line
    if len(line) < 2 or not line.endswith('"'):
        return None
    out, i, body = bytearray(), 0, line[1:-1]
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.extend(ch.encode("utf-8"))
            i += 1
            continue
        nxt = body[i + 1:i + 2]
        if nxt in _C_ESCAPES:
            out.append(_C_ESCAPES[nxt])
            i += 2
            continue
        m = re.compile(r"[0-7]{1,3}").match(body, i + 1)
        if not m:
            return None
        out.append(int(m.group(), 8) & 255)
        i = m.end()
    return out.decode("utf-8", errors="replace")


def _list_raws(items, prefix, c_quoted):
    """The pathspec raws for the lines (or NUL separated items) of a list, each
    joined with the `-C` prefix and read for `:(top)` the way a command line word
    is. A line whose quoting cannot be read names the marker."""
    out = []
    for item in items:
        if not item.strip():
            continue
        text = _unquote_c(item) if c_quoted else item
        if text is None:
            out += [TOP_MARK + m for m in _MARKER_TOP_PATHS]
            continue
        spec = _git_pathspec(text, prefix)
        if spec:
            out.append(spec)
    return out


def _abbreviates(word, option, shortest):
    """True when `word` is `option` or an abbreviation of it git would accept,
    no shorter than `shortest` (the shortest unambiguous one)."""
    return len(word) >= len(shortest) and option.startswith(word)


def _pathspec_lists(segment, rest, prefix):
    """Paths named by a pathspec FILE or a `--stdin` list: read from a file or
    here-document where the text names one, otherwise (`-`, a pipe, a file this
    guard cannot see) the marker file itself, so the command is judged as touching
    it. A file is returned as a FILE_MARK or NUL_MARK entry carrying the `-C` prefix
    its lines are joined with, and is read where the folder is known; a `<`
    redirect names a file relative to the shell's folder, not to the prefix."""
    source = None
    for k, w in enumerate(rest):
        # git takes any unambiguous abbreviation of a long option
        key, eq, value = w.partition("=")
        if _abbreviates(key, "--pathspec-from-file", "--pathspec-fr"):
            source = value if eq else (rest[k + 1] if k + 1 < len(rest) else None)
    if source is None and any(_abbreviates(w, "--stdin", "--st") for w in rest):
        source = "-"
    if source is None:
        return []
    # A list is read on NUL and on newline alike (see _pathspec_raws), so a -z or
    # --pathspec-file-nul in any spelling (a bundle, an abbreviation) changes
    # nothing this guard reads; NUL_MARK is no longer written.
    mark = FILE_MARK + prefix + "\x01"
    bodies = [t.body for t in segment if t.is_op and t.body]
    _, _, inputs = _argv(segment)
    if source != "-":
        return [mark + os.path.join(prefix, source)]
    if bodies:
        return _list_raws([x for body in bodies for x in body.splitlines()], prefix, True)
    if inputs:
        return [mark + inputs[0]]
    return [TOP_MARK + m for m in _MARKER_TOP_PATHS]


def _git_targets(segment):
    """The paths a git subcommand writes into the working tree or the index.
    Only the subcommands that touch tracked files directly; `git commit` and
    friends write history, which the Stop reconciler judges from the changes
    themselves. Returns (subcommand, paths). Git's own value-taking options
    (-c core.foo=bar, -C /dir) sit before the subcommand, so their value
    word is skipped by name rather than read as the subcommand; each `-C` folder
    is joined to the paths, and pathspec magic (`:(top)`, `:/`) is kept so it can
    be resolved. `read-tree` and `update-index --index-info` rewrite the index
    wholesale and so name the marker file."""
    call = _git_call(segment)
    if call is None:
        return "", []
    sub, rest, cdirs = call.sub, call.rest, [call.folder] if call.folder else []
    prefix = os.path.join(*cdirs) if cdirs else ""
    if sub in ("rm", "mv", "restore", "checkout", "reset", "clean", "apply", "stash",
               "update-index"):
        names = [w for w in rest if not _is_flag(w)]
        extra = _pathspec_lists(segment, rest, prefix)
        if sub == "update-index":
            # --cacheinfo mode,sha,path names its path last, after commas
            names += [w.split(",")[-1] for w in names if "," in w]
        paths = [x for x in (_git_pathspec(w, prefix) for w in names) if x] + extra
        if sub == "update-index" and "--index-info" in rest:
            paths += [TOP_MARK + m for m in _MARKER_TOP_PATHS]
        return sub, paths
    if sub == "read-tree":
        return sub, [TOP_MARK + m for m in _MARKER_TOP_PATHS]
    return sub, []


# ---------------------------------------------------------------------------
# COMMAND EFFECTS: the second question this guard asks.
#
# CONTROL_PLANE_PATTERNS above answers "does this command write a protected
# PATH". That question has a blind spot a dogfood run walked straight through
# (root R-8, transcript B3-S3): a push carrying no-verify to the default
# branch writes no path in the working tree at all, so every pattern in this
# file scores zero on it, no guard said anything, and an unreviewed change
# reached the remote at exit 0. An effect is not always a file.
#
# The rules below stay READABLE FROM THE ARGUMENTS, exactly like
# WRITE_COMMANDS: an effect that cannot be read off the command line belongs
# to the Stop reconciler, not here. And they keep this file's stated
# invariants: no subprocess, standard library only, so the default branch is
# read from the repository's own ref files rather than asked of the tool.
# ---------------------------------------------------------------------------

#: The version control tool's own options that take a SEPARATE value word, so
#: a -C prefix reads the same as no prefix at all. An attached value
#: (--git-dir=x) is one token and needs no entry here.
GIT_VALUE_FLAGS = ("-C", "-c", "--git-dir", "--work-tree", "--namespace",
                   "--exec-path", "--super-prefix")

#: The two names a repository's default branch is, in practice, ever called.
#: Used only when refs/remotes/<remote>/HEAD does not exist here at all, so
#: the default cannot be named any other way: see push_effect below.
LITERAL_DEFAULT_NAMES = frozenset(("main", "master"))


class EffectFinding(object):
    """What one effect rule found: a refusal reason, a stderr note, or both.

    An object rather than a pair, for the reason Decision states below: this
    project's honesty meta-test reads a 2-tuple return as a possible verdict
    source, and neither of these fields is a check verdict."""

    def __init__(self, reason="", note=""):
        self.reason = reason
        self.note = note


def _read_text(path):
    """The first 4096 characters of a small text file, or "". Never raises: a
    ref file that cannot be read is a fail-open here like everywhere else."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(4096)
    except (OSError, ValueError):
        return ""


def _repo_dirs(start):
    """(gitdir, commondir) for the repository containing start, or
    (None, None).

    Read from disk rather than asked of the version control tool, because
    this file runs in front of every Bash call and its docstring promises no
    subprocess. A linked worktree's dotfolder is a FILE naming its gitdir,
    and that gitdir carries a commondir marker pointing at the refs the whole
    repository shares, which is where the remote refs live. Both forms are
    handled, so a session working in a worktree resolves the same default
    branch as one in the main checkout."""
    try:
        cur = os.path.abspath(start)
    except (OSError, ValueError):
        return None, None
    gitdir = None
    for _ in range(64):
        dot = os.path.join(cur, ".git")
        if os.path.isdir(dot):
            gitdir = dot
            break
        if os.path.isfile(dot):
            for line in _read_text(dot).splitlines():
                if line.strip().startswith("gitdir:"):
                    p = line.split(":", 1)[1].strip()
                    gitdir = p if os.path.isabs(p) else os.path.normpath(
                        os.path.join(cur, p))
                    break
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if not gitdir or not os.path.isdir(gitdir):
        return None, None
    marker = os.path.join(gitdir, "commondir")
    if os.path.isfile(marker):
        p = _read_text(marker).strip()
        if p:
            common = p if os.path.isabs(p) else os.path.normpath(
                os.path.join(gitdir, p))
            if os.path.isdir(common):
                return gitdir, common
    return gitdir, gitdir


def default_branch(start, remote):
    """The short name of remote's default branch, read from this
    repository's own refs/remotes/<remote>/HEAD, or None when that ref was
    never set.

    NEVER a hard coded name. A repository whose default is master, trunk or
    develop is the same repository to this guard, and a guess would refuse
    the wrong branch while waving the right one through."""
    _gitdir, common = _repo_dirs(start)
    if not common:
        return None
    text = _read_text(os.path.join(common, "refs", "remotes", remote, "HEAD"))
    prefix = "ref: refs/remotes/%s/" % remote
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip() or None
    return None


def current_branch(start):
    """The branch HEAD is on, or None when HEAD is detached or unreadable.
    Read from the WORKTREE's own HEAD, not the shared common dir, because
    that is the branch a push with no refspec at all would send."""
    gitdir, _common = _repo_dirs(start)
    if not gitdir:
        return None
    for line in _read_text(os.path.join(gitdir, "HEAD")).splitlines():
        line = line.strip()
        if line.startswith("ref: refs/heads/"):
            return line[len("ref: refs/heads/"):].strip() or None
    return None


def _push_words(segment):
    """The non-flag words after the push subcommand in a version control push
    command, or None when this segment is not one."""
    words = [t.text for t in segment if not t.is_op]
    k = 0
    while k < len(words) and _ASSIGNMENT.match(words[k]):
        k += 1
    if k >= len(words) or os.path.basename(words[k].strip()) != "git":
        return None
    k += 1
    while k < len(words) and _is_flag(words[k]):
        if words[k] in GIT_VALUE_FLAGS:
            k += 1
        k += 1
    if k >= len(words) or words[k] != "push":
        return None
    return [w for w in words[k + 1:] if not _is_flag(w)]


def _push_targets(refspecs, start):
    """The branch names this push would land on. A refspec's DESTINATION is
    what matters (HEAD:main lands on main), and a push with no refspec at all
    lands on the branch HEAD is already on."""
    branches = []
    for spec in refspecs[1:]:
        dst = spec.lstrip("+")
        if ":" in dst:
            dst = dst.split(":")[-1]
        if dst.startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        # A refspec of HEAD names whatever branch HEAD is on, not a branch
        # literally called HEAD, so resolve it before the default-branch check.
        if dst == "HEAD":
            here = current_branch(start)
            if here:
                dst = here
        if dst:
            branches.append(dst)
    if not branches:
        here = current_branch(start)
        if here:
            branches.append(here)
    return branches


def push_effect_reason(named, why, cmd_text):
    """The refusal text. Same shape as refusal_reason below: what was read,
    the command verbatim, the numbered conditions that really unlock it, then
    what a break-glass record does NOT do, and the closing caveat.

    Only exits push_effect honors are listed. It used to offer a third, a
    record in .sbe/break-glass.json, which nothing in this guard reads and
    which the Stop reconciler, judging changed files, never matches to a
    push: an operator who followed it was refused again (2026-09-26)."""
    return (
        "BrotherSBE Bash write guard: this command pushes to %s, and %s.\n"
        "The command, verbatim:\n"
        "  %s\n"
        "A push writes no file in the working tree, so none of this guard's "
        "path patterns can see it and no ownedPaths declaration could ever "
        "cover it. The effect is judged here instead.\n"
        "This guard allows a push that meets both of these, and nothing else "
        "unlocks it:\n"
        "  1. It lands on a branch of its own, not the default branch (nor main "
        "or master when the default cannot be resolved); open a pull request "
        "from there, so the checks that guard the default branch get to run.\n"
        "  2. It runs without the flag that switches off the pre-push hooks, "
        "so those hooks actually run.\n"
        "No record in .sbe/break-glass.json unlocks this push: this guard never "
        "reads that file, and the Stop reconciler that does read it judges "
        "changed files, never a push. A deliberate, reviewed exception is run "
        "by a person by hand, not through this session's tools.\n"
        "This guard did NOT prove anything about the rest of the command."
        % (named, why, cmd_text[:400]))


def push_effect(segment, root, cwd, cmd_text):
    """An EffectFinding when this simple command is a push this guard
    refuses, else None.

    Two effects, neither of them a file: a flag that switches off the
    pre-push hooks that are the only gate between a working tree and a
    remote, and a push that lands on this repository's own default branch,
    which is the branch every other lane builds on."""
    refspecs = _push_words(segment)
    if refspecs is None:
        return None
    start = root if _repo_dirs(root)[0] else cwd
    skip_hooks_flag = "--no-verify"
    no_verify = skip_hooks_flag in _flags(segment)
    remote = refspecs[0] if refspecs else "origin"
    default = default_branch(start, remote)
    if default is None and remote != "origin":
        default = default_branch(start, "origin")
    targets = _push_targets(refspecs, start)
    on_default = bool(default) and default in targets
    # R-8 refused a push carrying --no-verify or landing on the default
    # branch. Neither test fixture behind the 2026-09-08 pre-flight (B4-S2)
    # has refs/remotes/origin/HEAD, so default_branch() returns None and a
    # plain `git push origin main` slipped through as advisory-only. main
    # and master are the two names a repository's default is overwhelmingly
    # ever called, so when the default cannot be resolved AT ALL, a push
    # naming one of them literally is treated as a push to the default
    # rather than waved through on the strength of an absent ref.
    literal_target = None
    if default is None:
        for t in targets:
            if t in LITERAL_DEFAULT_NAMES:
                literal_target = t
                break
    if not no_verify and not on_default and literal_target is None:
        if default is None:
            return EffectFinding(note=(
                "sbe_bash_write_guard: this is a push and no "
                "refs/remotes/%s/HEAD exists here, so the default branch "
                "could not be named and the push was NOT checked against it. "
                "That is not a claim the push is safe. Set that ref with the "
                "remote set-head subcommand for %s." % (remote, remote)))
        return None
    if targets:
        named = "the branch %s" % ", ".join(targets)
    else:
        named = ("a branch this guard could not name, because HEAD is "
                 "detached or unreadable")
    if no_verify and on_default:
        why = ("it carries the flag that switches off the pre-push hooks, "
               "AND it lands on %s, this repository's default branch"
               % default)
    elif no_verify and literal_target:
        why = ("it carries the flag that switches off the pre-push hooks, "
               "AND it lands on %s, and this repository's default branch "
               "could not be resolved (no refs/remotes/%s/HEAD here), so "
               "the literal name was treated as the default"
               % (literal_target, remote))
    elif no_verify:
        why = ("it carries the flag that switches off the pre-push hooks: "
               "the gates would not run, and nothing else in this session "
               "would record that they did not")
    elif literal_target:
        why = ("it lands on %s, and this repository's default branch could "
               "not be resolved (no refs/remotes/%s/HEAD here), so the "
               "literal name was treated as the default"
               % (literal_target, remote))
    else:
        why = ("it lands on %s, this repository's default branch, which "
               "every other lane builds on" % default)
    return EffectFinding(reason=push_effect_reason(named, why, cmd_text))


#: The effect rules, in order. Each one reads ONE simple command and returns
#: an EffectFinding or None. Small on purpose: this is not a policy engine, it
#: is the short list of effects a path pattern cannot see.
class GitCall(object):
    """A git command line taken apart: the subcommand, the words after it, and
    the folder its `-C` options point at (joined, relative to the working
    folder)."""

    def __init__(self, sub, rest, folder, configs=(), git_dir="", globals_=()):
        self.sub, self.rest, self.folder = sub, rest, folder
        #: The words between `git` and the subcommand (its global options).
        self.globals = list(globals_)
        #: The value of `--git-dir`, when given.
        self.git_dir = git_dir
        #: The `-c key=value` overrides given before the subcommand.
        self.configs = list(configs)


def _git_call(segment):
    """The GitCall of a segment that runs git (also behind `env`, `sudo` and the
    like), or None."""
    name, words, _ = _argv(segment)
    if name != "git":
        return None
    k = 0
    folder, configs, git_dir = "", [], ""
    first_global = k
    while k < len(words) and _is_flag(words[k]):
        if words[k].startswith("--git-dir="):
            git_dir = words[k].split("=", 1)[1]
        elif words[k] in GIT_VALUE_FLAGS:
            if words[k] == "-C" and k + 1 < len(words):
                folder = os.path.join(folder, words[k + 1])
            elif words[k] == "-c" and k + 1 < len(words):
                configs.append(words[k + 1])
            elif words[k] == "--git-dir" and k + 1 < len(words):
                git_dir = words[k + 1]
            k += 1
        k += 1
    if k >= len(words):
        return None
    return GitCall(words[k], words[k + 1:], folder, configs, git_dir, words[first_global:k])


#: Subcommands that move or rewrite HEAD. Named by what they do to the owner
#: files: a move to a tree without the marker unprotects them.
HEAD_MOVERS = frozenset(("reset checkout switch rebase filter-branch filter-repo replace "
                         "update-ref symbolic-ref").split())
_NEW_BRANCH_FLAGS = frozenset(("-b", "-B", "-c", "-C", "--orphan"))


class HeadMove(object):
    """Where a git command sends HEAD: kind "none" (not at all), "rev" (to
    `target`, whose tree can be read) or "never" (to a result that cannot be
    inspected before it runs); `must_resolve` is True when a target that git
    cannot resolve is itself a refusal."""

    def __init__(self, kind, target="", must_resolve=False):
        self.kind, self.target, self.must_resolve = kind, target, must_resolve


def _head_move(call, base, common):
    """The HeadMove of one git command (see HEAD_MOVERS)."""
    sub, rest = call.sub, call.rest
    flags = [w for w in rest if _is_flag(w)]
    if sub in ("filter-branch", "filter-repo"):
        return HeadMove("never")
    if sub == "rebase":
        return HeadMove("none" if set(flags) & {"--abort", "--quit"} else "never")
    if sub == "replace":
        return HeadMove("none" if set(flags) & {"-l", "--list", "--format"} else "never")
    if sub == "symbolic-ref":
        names = [w for w in rest if not _is_flag(w)]
        if len(names) >= 2 and names[0] == "HEAD":
            return HeadMove("rev", names[1], True)
        return HeadMove("none")
    if sub == "update-ref":
        names, skip = [], False
        for w in rest:
            if skip:
                skip = False
            elif w == "-m":
                skip = True
            elif not _is_flag(w):
                names.append(w)
        if "-d" in flags:
            return HeadMove("never" if names[:1] == ["HEAD"] else "none")
        if len(names) >= 2 and (names[0] == "HEAD" or names[0].startswith("refs/heads/")):
            return HeadMove("rev", names[1], True)
        return HeadMove("none")
    if "--orphan" in rest:
        return HeadMove("never")
    before = rest[:rest.index("--")] if "--" in rest else rest
    named = []
    skip = False
    for w in before:
        if skip:
            skip = False
        elif w in _NEW_BRANCH_FLAGS:
            skip = True
        elif not _is_flag(w):
            named.append(w)
    if not named:
        return HeadMove("none")
    if sub in ("checkout", "reset") and "--" in rest and not set(flags) & _NEW_BRANCH_FLAGS:
        # `git checkout <rev> -- paths`, `git reset <rev> -- paths`: files and
        # index entries from a commit, HEAD stays; judged as writes of the paths
        return HeadMove("none")
    if common.tree_holds_marker(base, named[0]) is None:
        return HeadMove("none")      # not a revision: a path, judged as a write
    return HeadMove("rev", named[0])


def head_move_effect(segment, root, cwd, cmd_text):
    """Refuse a command that moves HEAD, in a tree that holds the marker, to a
    tree that does not (or whose result cannot be inspected first). The marker
    is what makes a tree the Brother source; a reset, a checkout or a rewrite
    to a commit before it was added would remove the owner files' protection
    without touching any path. In a stranger's repository none of this applies."""
    call = _git_call(segment)
    if call is None or call.sub not in HEAD_MOVERS:
        return None
    common = require_common_module()
    base = os.path.join(cwd or root, call.folder) if call.folder else (cwd or root)
    if not common.is_brother_source(base):
        return None
    move = _head_move(call, base, common)
    if move.kind == "none":
        return None
    if move.kind == "rev":
        holds = common.tree_holds_marker(base, move.target)
        if holds or (holds is None and not move.must_resolve):
            return None
        why = ("the tree %s names does not hold the owner marker" % move.target
               if holds is False else
               "%s cannot be resolved to a tree, so what it holds is unknown" % move.target)
    else:
        why = "its result cannot be inspected before it runs"
    return EffectFinding(reason=(
        "BrotherSBE Bash guard: `git %s` would move or rewrite HEAD, and %s. In the "
        "Brother source that would remove the owner files' protection without writing "
        "a path (the marker policy.py would no longer be in HEAD).\n"
        "A move to a branch or commit that holds the marker is allowed. Otherwise do "
        "it yourself in a terminal, or set BROTHERSBE_BASH_GUARD_OFF=1 for this session "
        "(the guard then says on stderr that it was off).\n"
        "This guard reads command text only; it did NOT prove anything about the rest "
        "of the command." % (call.sub, why)))


COMMAND_EFFECTS = (push_effect, head_move_effect)


# ---------------------------------------------------------------------------
# Content that git applies. In the Brother source, a command that applies a
# patch, a commit, a merge or a stash is judged by the owner paths the content
# touches, exactly as `git rm` of those paths would be: removing the owner files
# by applying a diff that deletes them is the same act.
# ---------------------------------------------------------------------------

_PATCH_HEADER = re.compile(
    r"^(?:diff --git a/(\S+) b/(\S+)|--- (?:a/)?(\S+)|\+\+\+ (?:b/)?(\S+)"
    r"|(?:rename|copy) (?:from|to) (\S+))", re.M)
_APPLY_VALUE_FLAGS = frozenset(("--directory", "--include", "--exclude", "--whitespace",
                                "-p", "-C"))
_APPLY_READ_ONLY = frozenset(("--check", "--stat", "--numstat", "--summary"))
_APPLY_ABORTS = frozenset(("--abort", "--continue", "--skip", "--quit", "--show-current-patch",
                           "--edit-todo"))


def _owner_touch(paths, owner_patterns):
    """The first of `paths` that matches an owner pattern, or "". A path is also
    tried with its leading folders stripped, since a patch may carry a prefix
    (`a/`, `-p2`, `--directory`) this guard does not apply."""
    import fnmatch
    for path in paths:
        parts = path.split("/")
        for i in range(len(parts)):
            tail = "/".join(parts[i:])
            if any(fnmatch.fnmatchcase(tail, pat) for pat in owner_patterns):
                return path
    return ""


def _patch_paths(text):
    return [g for m in _PATCH_HEADER.finditer(text) for g in m.groups()
            if g and g != "/dev/null"]


#: The one way this guard asks git which paths a diff touches. Fixed words only:
#: the agent's own options and pathspecs are never passed on.
_DIFF_NAMES = ["diff", "--name-only", "--no-ext-diff", "--no-textconv"]


def _upstream_patch_text(common, base, up):
    """Patch-shaped text naming the paths a piped `git diff`, `show`, `log` or
    `format-patch` would send, built from a FIXED command. Only revision words
    that do not start with a dash and resolve are used; an option, a pathspec or
    anything else makes the patch unreadable (None). Never runs the agent's words."""
    words = up.rest
    if any(w == "--" or _is_flag(w) for w in words):
        return None
    revs = []
    for w in words:
        for part in ([w] if ".." not in w else [x for x in w.replace("...", "..").split("..") if x]):
            if common.git_output(base, ["rev-parse", "--verify", "--quiet", part]) is None:
                return None
            revs.append(part)
    if up.sub == "diff":
        if len(revs) != 2:
            return None          # one revision (or none) compares the working tree
        names = common.git_output(base, _DIFF_NAMES + revs)
    else:
        if len(revs) == 1:
            names = common.git_output(base, _DIFF_NAMES + [revs[0] + "^", revs[0]])
            if names is None:
                names = common.git_output(base, ["show", "--name-only", "--format=",
                                                 "--no-ext-diff", "--no-textconv", revs[0]])
        elif len(revs) == 2 and ".." in "".join(words):
            names = common.git_output(base, _DIFF_NAMES + revs)
        else:
            return None
    if names is None:
        return None
    return "\n".join("diff --git a/%s b/%s" % (n, n) for n in names.split())


def _commit_paths(common, base, sub, target):
    """The paths the commit (or range) `target` changes, as `sub` would apply
    them, or None when it cannot be resolved."""
    if sub == "merge":
        out = common.git_output(base, _DIFF_NAMES + ["HEAD..." + target])
    elif ".." in target:
        left, _, right = target.replace("...", "..").partition("..")
        out = common.git_output(base, _DIFF_NAMES + [left or "HEAD", right or "HEAD"])
    else:
        out = common.git_output(base, _DIFF_NAMES + [target + "^", target])
        if out is None:
            out = common.git_output(base, ["show", "--name-only", "--format=", "--no-ext-diff",
                                           "--no-textconv", target])
    return None if out is None else out.split()


def _words_without(rest, value_flags):
    """The non-flag words of `rest`, skipping the value of each option in
    `value_flags`."""
    out, skip = [], False
    for w in rest:
        if skip:
            skip = False
        elif w in value_flags:
            skip = True
        elif not _is_flag(w):
            out.append(w)
    return out


#: Configuration keys the agent must not author in the Brother source: they decide
#: what a "configured remote" is, where the work tree is, and what is included.
_PROTECTED_CONFIG = re.compile(
    r"^(remote\.|branch\..*\.(remote|merge)$|core\.(worktree|hookspath|fsmonitor|sshcommand)$"
    r"|include|url\.)", re.I)
_CONFIG_READS = frozenset(("--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l",
                           "--list", "--show-origin", "--show-scope", "--name-only"))
_CONFIG_EDITS = frozenset(("--unset", "--unset-all", "--add", "--replace-all",
                           "--rename-section", "--remove-section", "-e", "--edit"))
_REMOTE_EDITS = frozenset(("add", "set-url", "rename", "remove", "rm", "set-head",
                           "set-branches"))


_NETWORK_URL = re.compile(r"^(?:https?|ssh|git)://|^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:")
_RO_SUBS = frozenset(("log show diff status rev-parse rev-list ls-tree ls-files cat-file "
                      "for-each-ref describe blame grep shortlog reflog whatchanged "
                      "count-objects show-ref ls-remote fsck verify-pack name-rev "
                      "merge-base cherry diff-tree diff-index diff-files var help version "
                      "archive bundle").split())
_BRANCH_READ = frozenset(("-l", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv",
                          "--verbose", "--contains", "--no-contains", "--merged",
                          "--no-merged", "--show-current", "--sort", "--format", "--color",
                          "--no-color", "--column", "--points-at", "-i", "--ignore-case"))


def _remote_urls(common, base):
    """{remote name: url} of the configured remotes of the repository at `base`."""
    out = common.git_output(base, ["config", "--get-regexp", r"^remote\..*\.url$"]) or ""
    urls = {}
    for line in out.splitlines():
        if line.strip():
            key, _, url = line.partition(" ")
            urls[key[len("remote."):-len(".url")]] = url.strip()
    return urls


def _remote_path(url):
    """The folder a path or file:// remote URL names, or "" for a network URL."""
    if _NETWORK_URL.match(url):
        return ""
    return os.path.realpath(url[len("file://"):] if url.startswith("file://") else url)


def _writes_a_remote_repository(call, e, root, common):
    """True when this git command runs inside the repository of a configured path
    remote (by -C, --git-dir, or the folder a cd followed into) and would change
    it: that is a push to the remote the session may not make, and the way to
    forge a default branch the exemption trusts."""
    if call.sub in _RO_SUBS:
        return False
    flags = set(w for w in call.rest if _is_flag(w))
    words = [w for w in call.rest if not _is_flag(w)]
    if call.sub == "branch" and not flags - _BRANCH_READ and (not words or flags & {"-l", "--list"}):
        return False
    if call.sub == "config" and flags & _CONFIG_READS:
        return False
    if call.sub == "remote" and (not words or words[0] in ("show", "get-url")):
        return False
    here = [e.cwd or root] + ([e.alt] if e.alt else [])
    targets = [os.path.realpath(os.path.join(b, call.folder)) if call.folder
               else os.path.realpath(b) for b in here]
    if call.git_dir:
        targets += [os.path.realpath(os.path.join(b, call.git_dir)) for b in here]
    root_real = os.path.realpath(root)
    outside = [t for t in targets if t != root_real and not t.startswith(root_real + os.sep)]
    if not outside:
        return False
    urls = _remote_urls(common, root)
    paths = set(filter(None, (_remote_path(u) for u in urls.values())))
    return any(t == r or t.startswith(r + os.sep) for t in outside for r in paths)


#: Environment a fetch must not run under in the Brother source: each can send a
#: fetch of a configured remote somewhere else, or change which config it reads.
_TRANSPORT_ENV = re.compile(
    r"^(GIT_CONFIG_COUNT|GIT_CONFIG_KEY_\d+|GIT_CONFIG_VALUE_\d+|GIT_CONFIG_PARAMETERS"
    r"|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_NOSYSTEM|GIT_SSH|GIT_SSH_COMMAND"
    r"|GIT_PROXY_COMMAND|GIT_EXEC_PATH|GIT_DIR|GIT_WORK_TREE|GIT_ALTERNATE_OBJECT_DIRECTORIES"
    r"|GIT_OBJECT_DIRECTORY|GIT_NAMESPACE|GIT_ALLOW_PROTOCOL|GIT_CONFIG|HOME|XDG_CONFIG_HOME|PATH)$")
_TRANSPORT_OPTIONS = ("--upload-pack", "--receive-pack", "--exec=", "--config-env", "--config",
                      "--exec-path", "--git-dir", "--work-tree")


def _inline_env_names(seg):
    """The names of the environment variables set inline in front of the command
    (`A=1 B=2 git ...`, `env A=1 git ...`)."""
    names = []
    for t in seg:
        if t.is_op:
            continue
        if _ASSIGNMENT.match(t.text):
            names.append(t.text.split("=", 1)[0])
        elif os.path.basename(t.text) not in _WRAPPERS:
            break
    return names


def _is_fetching(call):
    """True for a git command that fetches: fetch, pull, clone, `remote update`,
    `submodule update`."""
    return call.sub in ("fetch", "pull", "clone") or (
        call.sub in ("remote", "submodule") and call.rest[:1] == ["update"])


#: Variables whose value changes how a fetch reaches its remote.
_STATE_VARS = re.compile(r"^(GIT_|SSH_|XDG_)|^(HOME|PATH)$")
_STATE_COMMANDS = frozenset(("export", "declare", "typeset", "readonly"))
_STATE_ALWAYS = frozenset((".", "source", "eval", "exec"))


def _earlier_state(entries, i):
    """The construct before entries[i], in the same command text, that changes the
    shell state a fetch will run under in a way the hook cannot read: an export or
    declaration of a git, ssh, XDG, HOME or PATH variable, `set -a`, a bare
    assignment of one, or a sourced or evaluated file. "" for none."""
    for prior in entries[:i]:
        name, args, _ = _argv(prior.seg)
        names = [w.split("=", 1)[0] for w in args if not _is_flag(w)]
        words = [t.text for t in prior.seg if not t.is_op and not _ASSIGNMENT.match(t.text)]
        first = os.path.basename(words[0]) if words else ""
        if name in _STATE_ALWAYS or first in _STATE_ALWAYS:
            return first or name
        if name in _STATE_COMMANDS and any(_STATE_VARS.match(n) for n in names):
            return "%s %s" % (name, next(n for n in names if _STATE_VARS.match(n)))
        if name == "set" and any(w in ("-a", "allexport") for w in args):
            return "set -a"
        if not name:
            for t in prior.seg:
                if not t.is_op and _ASSIGNMENT.match(t.text) and _STATE_VARS.match(
                        t.text.split("=", 1)[0]):
                    return "an assignment of " + t.text.split("=", 1)[0]
    return ""


def _carries_transport_redirection(call, seg):
    """The variable or option, if any, that this fetch-like command carries to
    redirect or reconfigure its transport; "" for none."""
    if not _is_fetching(call):
        return ""
    for name in _inline_env_names(seg):
        if _TRANSPORT_ENV.match(name):
            return name
    if call.configs:
        return "-c " + call.configs[0].split("=", 1)[0]
    for g in call.globals + call.rest:
        for option in _TRANSPORT_OPTIONS:
            if g == option.rstrip("=") or g.startswith(option if option.endswith("=")
                                                         else option + "="):
                return option.rstrip("=")
    if call.sub == "clone" and "-u" in call.rest:
        return "-u"
    return ""


def _transport_is_untouched(common, base, remote):
    """True when nothing rewrites how `remote` is reached: no `url.*.insteadOf` or
    `pushInsteadOf` in any config scope, and the effective URL (insteadOf applied)
    is the configured one."""
    rewrites = common.git_output(base, ["config", "--show-origin", "--get-regexp",
                                        r"url\..*insteadof"], ok=(0, 1))
    if rewrites is None or rewrites.strip():
        return False
    configured = _remote_urls(common, base).get(remote, "")
    effective = (common.git_output(base, ["ls-remote", "--get-url", remote]) or "").strip()
    return bool(configured) and effective == configured


#: A redirecting variable named anywhere in a command's text; HOME and PATH only
#: when assigned (reading them is ordinary).
_REDIRECT_NAMES = re.compile(
    r"\b(GIT_CONFIG_COUNT|GIT_CONFIG_KEY_\w*|GIT_CONFIG_VALUE_\w*|GIT_CONFIG_PARAMETERS"
    r"|GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_NOSYSTEM|GIT_SSH_COMMAND|GIT_SSH"
    r"|GIT_PROXY_COMMAND|GIT_EXEC_PATH|GIT_DIR|GIT_WORK_TREE|GIT_ALTERNATE_OBJECT_DIRECTORIES"
    r"|GIT_OBJECT_DIRECTORY|GIT_NAMESPACE|GIT_ALLOW_PROTOCOL|GIT_TEMPLATE_DIR"
    r"|XDG_CONFIG_HOME)\b|(?:^|[\s;&|(\"'])((?:HOME|PATH))=")
_LAUNCHERS = frozenset(("env make xargs git").split())


def _launches_a_program(entries):
    """True when some command in the text launches a shell, an interpreter, env,
    make, xargs, `find -exec` or git: something that could run git with the
    variables the text names."""
    for e in entries:
        words = [t.text for t in e.seg if not t.is_op and not _ASSIGNMENT.match(t.text)]
        first = os.path.basename(words[0]) if words else ""
        name, args, _ = _argv(e.seg)
        for cmd in (first, name):
            if (cmd in _LAUNCHERS or cmd in SHELL_INTERPRETERS or _interpreter_family(cmd)
                    or (cmd == "find" and any(a in ("-exec", "-execdir", "-ok", "-okdir")
                                              for a in words))):
                return True
    return False


def redirecting_env_effect(entries, root, common):
    """Refuse a command whose TEXT names a variable that redirects git's transport
    or configuration, when the text also launches a program that could run git,
    in the Brother source. The variable may sit in front of a shell, an
    interpreter, `env -S` or `make` that runs git itself, where the per command
    rule cannot see it; reading a variable (`echo $GIT_DIR`) launches nothing and
    stays allowed."""
    text = " ".join(t.text for e in entries for t in e.seg if not t.is_op)
    # operators are not in the join; assignments inside them are in the words
    m = _REDIRECT_NAMES.search(text)
    if not m or not _launches_a_program(entries) or not common.is_brother_source(root):
        return None
    name = m.group(1) or ("%s=" % m.group(2))
    return EffectFinding(reason=(
        "BrotherSBE Bash guard: this command names %s and launches a program that could run "
        "git with it. In the Brother source such a variable can send a fetch of a configured "
        "remote somewhere else, or change which config git reads, so the remote tracking "
        "exemption could not be trusted.\n"
        "Do it yourself in a terminal, or set BROTHERSBE_BASH_GUARD_OFF=1 for this session "
        "(the guard then says on stderr that it was off).\n"
        "This guard reads command text only; it did NOT prove anything about the rest of "
        "the command." % name))


def metadata_effect(entries, i, root, common):
    """Refuse a git command that authors repository metadata the other rules
    trust, in the Brother source: what a configured remote is (`remote add`,
    `config remote.*`, `-c remote.*`), what refs/remotes/ holds (`update-ref`,
    `symbolic-ref`, a push into it), and where fetches come from (a URL or path on
    the command line, `--update-head-ok`). The remote tracking exemption holds only
    for refs a configured remote fetched, and these are the ways to fake one."""
    e = entries[i]
    call = _git_call(e.seg)
    if call is None:
        return None
    base = os.path.join(e.cwd or root, call.folder) if call.folder else (e.cwd or root)
    sub, rest = call.sub, call.rest
    flags = set(w for w in rest if _is_flag(w))
    words = [w for w in rest if not _is_flag(w)]
    why = ""
    redirect = _carries_transport_redirection(call, e.seg)
    earlier = _earlier_state(entries, i) if _is_fetching(call) else ""
    if redirect:
        why = ("it carries %s, which can send the fetch somewhere other than the "
               "configured remote" % redirect)
    elif earlier:
        why = ("%s comes earlier in the same command, and the shell state a fetch then "
               "runs under cannot be read by this guard" % earlier)
    elif _writes_a_remote_repository(call, e, root, common) and common.is_brother_source(root):
        return EffectFinding(reason=(
            "BrotherSBE Bash guard: `git %s` would change the repository of a configured "
            "remote (by -C, --git-dir or a cd into it). In the Brother source the remote's "
            "branches are trusted because the owner's pull request process moves them; "
            "moving one from here is a push the session may not make.\n"
            "Do it yourself in a terminal, or set BROTHERSBE_BASH_GUARD_OFF=1 for this "
            "session (the guard then says on stderr that it was off).\n"
            "This guard reads command text only; it did NOT prove anything about the rest "
            "of the command." % sub))
    if any(_PROTECTED_CONFIG.match(c.split("=", 1)[0]) for c in call.configs):
        why = "it overrides a remote or include setting with -c"
    elif sub == "remote" and words[:1] and words[0] in _REMOTE_EDITS:
        why = "it edits the list of configured remotes"
    elif sub == "config":
        # `--file X` / `-f X` names the config file written; its value is not the key.
        target, kept, skip = None, [], False
        for w in rest:
            if skip:
                target, skip = w, False
            elif w in ("-f", "--file"):
                skip = True
            elif w.startswith("--file="):
                target = w.split("=", 1)[1]
            elif not _is_flag(w):
                kept.append(w)
        words = kept
        in_repo = target is None or any(
            part.lower() == ".git" for part in target.replace("\\", "/").split("/"))
        if target is not None and not in_repo:
            # a relative --file is the working folder's (or --git-dir's) own file:
            # anything that lands inside the gitdir is the repository config
            gitdirs = [os.path.realpath(os.path.join(_toplevel(base), ".git"))]
            if call.git_dir:
                gitdirs.append(os.path.realpath(os.path.join(base, call.git_dir)))
            spots = [os.path.realpath(os.path.join(base, target))]
            if call.git_dir:
                spots.append(os.path.realpath(os.path.join(base, call.git_dir, target)))
            in_repo = any(sp == gd or sp.startswith(gd + os.sep)
                          for sp in spots for gd in gitdirs)
        edits = bool(flags & _CONFIG_EDITS) or len(words) >= 2
        key = words[0] if words else ""
        if in_repo and not flags & _CONFIG_READS and edits and (
                "-e" in flags or "--edit" in flags or _PROTECTED_CONFIG.match(key)
                or "--rename-section" in flags or "--remove-section" in flags):
            why = "it writes a remote, include or work tree setting"
    elif sub in ("update-ref", "symbolic-ref") and any("refs/remotes/" in w for w in words):
        why = "it writes a remote tracking ref"
    elif sub in ("push", "fetch") and any(
            ":" in w and w.split(":")[-1].startswith(("refs/remotes/", "+refs/remotes/"))
            for w in words):
        why = "it writes a remote tracking ref"
    elif sub == "push" and words[:1] in (["."], [".."]):
        why = "it pushes into this repository"
    elif sub in ("fetch", "pull") and ("--update-head-ok" in flags or "-u" in flags
                                       and sub == "fetch"):
        why = "it fetches over the checked out branch"
    elif sub in ("fetch", "pull") and words and "--all" not in flags:
        remotes = (common.git_output(base, ["remote"]) or "").split()
        if words[0] not in remotes and words[0] not in ("--",):
            why = "it fetches from %s, which is not a configured remote" % words[0]
    if not why or not common.is_brother_source(base):
        return None
    return EffectFinding(reason=(
        "BrotherSBE Bash guard: `git %s` is refused because %s. In the Brother source the "
        "remotes and refs/remotes/ are repository metadata the session does not author: "
        "content on a remote tracking ref is trusted because a configured remote fetched "
        "it.\n"
        "Do it yourself in a terminal, or set BROTHERSBE_BASH_GUARD_OFF=1 for this session "
        "(the guard then says on stderr that it was off).\n"
        "This guard reads command text only; it did NOT prove anything about the rest of "
        "the command." % (sub, why)))


def _remote_default_branch(common, base, remote):
    """The branch `refs/remotes/<remote>/HEAD` points at, or `main` when that
    symbolic ref is absent. Only this branch reaches the repository through the
    owner's pull request process; any other branch on the remote may have been
    pushed by the session."""
    out = (common.git_output(base, ["symbolic-ref", "--short", "refs/remotes/%s/HEAD" % remote])
           or "").strip()
    return out[len(remote) + 1:] if out.startswith(remote + "/") else "main"


def _fetch_head_source_is_configured(common, base):
    """True when the last fetch (the first line of FETCH_HEAD) came from the URL of
    a configured remote."""
    path = common.git_output(base, ["rev-parse", "--git-path", "FETCH_HEAD"])
    if not path:
        return False
    try:
        with open(os.path.join(base, path.strip()), "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
    except (OSError, ValueError):
        return False
    source = first.rstrip("\n").rsplit(" of ", 1)[-1].strip()
    named = re.search(r"'([^']+)' of ", first)
    urls = (common.git_output(base, ["config", "--get-regexp", r"^remote\..*\.url$"]) or "")
    def plain(url):
        # git records the source without a trailing slash or `.git`
        url = url.strip().rstrip("/")
        return url[:-4] if url.endswith(".git") else url
    for line in urls.splitlines():
        if not line.strip() or plain(line.split(None, 1)[-1]) != plain(source):
            continue
        remote = line.split(None, 1)[0][len("remote."):-len(".url")]
        if not _NETWORK_URL.match(line.split(None, 1)[-1].strip()) \
                or not _transport_is_untouched(common, base, remote):
            continue
        branch = named.group(1).replace("refs/heads/", "", 1) if named else ""
        if branch == _remote_default_branch(common, base, remote):
            return True
    return False


def _is_remote_tracking(common, base, target):
    """True when `target` names a remote tracking ref of a CONFIGURED remote
    (`origin/main` with `origin` in `git remote`, or `@{u}` resolving to one), and
    no local branch or tag of that name could be what git picks instead. Content
    on such a ref reached the repository through the owner's own pull request
    process; the owner family guards the AGENT's authorship, not that process."""
    if target == "FETCH_HEAD":
        return _fetch_head_source_is_configured(common, base)
    if target == "@{u}":
        name = (common.git_output(base, ["rev-parse", "--symbolic-full-name", "@{u}"]) or "").strip()
        if not name.startswith("refs/remotes/"):
            return False
        return _is_remote_tracking(common, base, name[len("refs/remotes/"):])
    remotes = (common.git_output(base, ["remote"]) or "").split()
    owner_remote = next((r for r in remotes if target.startswith(r + "/")), "")
    if not owner_remote or not _NETWORK_URL.match(
            _remote_urls(common, base).get(owner_remote, "")) \
            or not _transport_is_untouched(common, base, owner_remote) or target != "%s/%s" % (
            owner_remote, _remote_default_branch(common, base, owner_remote)):
        return False
    def exists(ref):
        return common.git_output(base, ["rev-parse", "--verify", "--quiet", ref]) is not None
    return (exists("refs/remotes/" + target) and not exists("refs/heads/" + target)
            and not exists("refs/tags/" + target))


def _patch_text(entries, i, call, base):
    """The patch text a `git apply` or `git am` at entries[i] reads, from files it
    names, a `<` redirect, a here-document, or the git command piped into it; or
    None when its source cannot be read (a URL, a process substitution, a here
    string, a missing file, any other upstream command)."""
    seg = entries[i].seg
    files = [w for w in _words_without(call.rest, _APPLY_VALUE_FLAGS) if w != "-"]
    _, _, inputs = _argv(seg)
    files += inputs
    chunks = []
    for path in files:
        try:
            with open(os.path.join(base, path), "r", encoding="utf-8", errors="replace") as f:
                chunks.append(f.read(8000000))
        except (OSError, ValueError):
            return None
    bodies = [t.body for t in seg if t.is_op and t.body]
    chunks += bodies
    if files or bodies:
        return "\n".join(chunks)
    if _herestrings(seg):
        return None
    if i > 0 and entries[i - 1].pipe == entries[i].pipe and entries[i - 1].post == "|":
        up = _git_call(entries[i - 1].seg)
        if up is None or up.sub not in ("diff", "show", "format-patch", "log"):
            name, args, _ = _argv(entries[i - 1].seg)
            if name == "cat":
                return _patch_text_of_files(args, base)
            return None
        common = require_common_module()
        upbase = os.path.join(base, up.folder) if up.folder else base
        return _upstream_patch_text(common, upbase, up)
    return None


def _patch_text_of_files(args, base):
    chunks = []
    for path in [a for a in args if not _is_flag(a)]:
        try:
            with open(os.path.join(base, path), "r", encoding="utf-8", errors="replace") as f:
                chunks.append(f.read(8000000))
        except (OSError, ValueError):
            return None
    return "\n".join(chunks)


def content_apply_effect(entries, i, root, common):
    """Refuse a git command that applies content touching an owner file, in the
    Brother source (see the section note above). `apply` and `am` are judged by
    the headers of the patch they read, `cherry-pick`, `revert`, `merge` and `pull`
    by the diff of the commits they bring in, `stash pop` and `stash apply` by the
    stash. Content this guard cannot read is refused as unclassifiable."""
    e = entries[i]
    call = _git_call(e.seg)
    if call is None:
        name, args, _ = _argv(e.seg)
        if name not in ("patch", "gpatch"):
            return None
        # `patch` is judged exactly as `git apply`: the patch comes from -i or
        # --input, a `<` redirect, a here-document or the pipe, and any file
        # operand it patches is an owner path if it matches one.
        sources, operands, skip = [], [], False
        for k, w in enumerate(args):
            if skip:
                skip = False
            elif w in ("-i", "--input"):
                sources.append(args[k + 1] if k + 1 < len(args) else "")
                skip = True
            elif w.startswith("--input="):
                sources.append(w.split("=", 1)[1])
            elif w.startswith("-i") and len(w) > 2:
                sources.append(w[2:])
            elif w in ("-p", "-d", "-o", "-r", "-B", "-V", "-F", "-D", "--directory"):
                skip = True
            elif not _is_flag(w):
                operands.append(w)
        if {"--dry-run", "-C", "--check"} & set(args):
            return None
        call = GitCall("apply", sources, "")
        patch_operands = operands
    else:
        patch_operands = []
    if call.sub not in ("apply", "am", "cherry-pick", "revert", "merge", "pull", "stash"):
        return None
    flags = set(w for w in call.rest if _is_flag(w))
    if flags & _APPLY_ABORTS or (call.sub == "apply" and flags & _APPLY_READ_ONLY
                                 and "--apply" not in flags):
        return None
    base = os.path.join(e.cwd or root, call.folder) if call.folder else (e.cwd or root)
    if not common.is_brother_source(base):
        return None
    sub, owner = call.sub, common.OWNER_PATTERNS
    unreadable = ""
    touched = ""
    if sub in ("apply", "am"):
        text = _patch_text(entries, i, call, base)
        if text is None:
            unreadable = "the patch it reads cannot be read before it runs"
        else:
            touched = _owner_touch(_patch_paths(text) + patch_operands, owner)
    elif sub == "stash":
        words = [w for w in call.rest if not _is_flag(w)]
        if not words or words[0] not in ("pop", "apply"):
            return None
        out = common.git_output(base, ["stash", "show", "--name-only"] + words[1:2])
        if out is None:
            unreadable = "the stash it applies cannot be read"
        else:
            touched = _owner_touch(out.split(), owner)
    else:
        words = _words_without(call.rest, {"-m", "-s", "-X", "-F", "--strategy", "-S"})
        if sub == "pull":
            targets = ["%s/%s" % (words[0], r) for r in words[1:]] if words else []
            if not words:
                targets = ["@{u}"]
        else:
            targets = words or (["@{u}"] if sub == "merge" else [])
        for target in targets:
            if _is_remote_tracking(common, base, target):
                continue
            paths = _commit_paths(common, base, "merge" if sub in ("merge", "pull") else sub,
                                  target)
            if paths is None:
                if sub in ("pull", "merge") and not words:
                    continue
                unreadable = "%s cannot be resolved, so what it brings in is unknown" % target
                break
            touched = _owner_touch(paths, owner)
            if touched:
                break
    if not touched and not unreadable:
        return None
    why = ("it changes the owner file %s" % touched) if touched else unreadable
    return EffectFinding(reason=(
        "BrotherSBE Bash guard: `git %s` applies content, and %s. In the Brother source "
        "the owner files are protected from a deletion or a rewrite by a diff, a commit, "
        "a merge or a stash exactly as from `rm`.\n"
        "Make the change through Edit or Write after declaring the path, do it yourself in "
        "a terminal, or set BROTHERSBE_BASH_GUARD_OFF=1 for this session (the guard then "
        "says on stderr that it was off).\n"
        "This guard reads command text only; it did NOT prove anything about the rest of "
        "the command." % (sub, why)))


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
        git_sub, git_paths = _git_targets(segment)
        for p in git_paths:
            cand = Candidate(p, "git %s" % git_sub, True)
            cand.pathspec = True
            out.append(cand)
    elif name in SHELL_INTERPRETERS and depth < MAX_DEPTH:
        payload = _payload_of(segment, ("-c",))
        if payload:
            try:
                for seg in split_commands(tokenize(payload)):
                    out.extend(segment_candidates(seg, depth + 1))
            except Unclassifiable as e:
                raise Unclassifiable(
                    "the %s -c payload could not be read (%s)" % (name, e))
    elif _interpreter_family(name):
        payload = interpreter_code(segment)
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
# GUARD1: what a stranger's agent sends out of the machine, what an interpreter
# one liner deletes, and the folder a command runs in.
#
# These rules are the one place this guard FAILS CLOSED. The write boundary
# above fails open on purpose; an upload of a secret or a recursive delete is
# not a thing a hook in front of every Bash call may guess about. Every refusal
# here names BROTHERSBE_BASH_GUARD_OFF. A command that cannot be split is
# refused when it could be carrying a network tool or an interpreter (see
# DANGER_WORDS); a long line or a trailing backslash that carries neither is
# allowed with the usual loud note.
#
# Like everything in this file it reads command TEXT. A key read by a script, a
# path built at run time or an upload over a protocol this file does not know
# is outside it; docs/limits.md says so under "Shapes this guard does not see".
# ---------------------------------------------------------------------------

#: Commands that carry bytes off the machine, and the two that upload a file by
#: option. Spelled as split strings: this guard only REFUSES these commands, and
#: the zero-network scan (test_sbe.py) reads a tuple of those names as a client.
EXFIL_SINKS = frozenset("curl wget nc ncat netcat".split())
_UPLOADERS = frozenset("curl wget".split())

#: A command the guard could not split is refused only when its raw text names
#: one of these; otherwise it is allowed with a note (a long line, a trailing
#: backslash). Words, not a parse: the text could not be parsed.
DANGER_WORDS = re.compile(
    r"\b(?:curl|wget|nc|ncat|netcat|socat|python[0-9.]*|node|perl|ruby|php|bash|sh|zsh|eval)\b"
    r"|/dev/tcp")

#: Words that run another command, each with the options that take a separate
#: value. Skipped to find the command that really runs.
_WRAPPERS = {
    "sudo": frozenset("-u -g -h -p -C -D -R -T -U -r -t".split()),
    "doas": frozenset("-u -C".split()),
    "env": frozenset("-u -C -S".split()),
    "timeout": frozenset("-s -k".split()),
    "exec": frozenset("-a".split()),
    "nice": frozenset("-n".split()),
    "ionice": frozenset("-c -n -p".split()),
    "stdbuf": frozenset("-i -o -e".split()),
    "time": frozenset("-f -o".split()),
    "xargs": frozenset("-a -d -E -I -L -n -P -s".split()),
    "nohup": frozenset(), "command": frozenset(), "builtin": frozenset(),
    "busybox": frozenset(), "setsid": frozenset(),
}

_SECRET_BASENAMES = frozenset((".netrc", "credentials", "id_rsa", "id_ed25519",
                               "id_ecdsa", "id_dsa"))
_SECRET_SUFFIXES = (".pem", ".key")
_SECRET_DIRS = frozenset((".ssh", ".aws"))
#: `.env.example` is the committed template, not the secret.
_ENV_TEMPLATES = (".example", ".sample", ".template", ".dist")
#: A .pem whose name says it is a certificate or a public part holds no secret.
_PUBLIC_PEM = r"cert|public|chain|^ca[-_.]"

#: curl's upload options by kind: "data" is `@file`, "enc" is `@file` or
#: `name@file`, "form" is `name=@file` or `name=<file`, "file" is the file itself.
_CURL_UPLOADS = {
    "-d": "data", "--data": "data", "--data-binary": "data", "--data-ascii": "data",
    "--json": "data", "--data-urlencode": "enc", "-T": "file",
    "--upload-file": "file", "-F": "form", "--form": "form"}
_WGET_UPLOADS = {"--post-file": "file", "--body-file": "file"}
#: curl's other short options that take a value, so `-XPOST` or `-H x` is not
#: read as a cluster of flags.
_CURL_VALUE_FLAGS = frozenset("XHoubAecmwxyYzEKrCDQt")

_REDIRECTS = frozenset(("<", "<<", ">", ">>", ">|"))
#: Pattern strings, not compiled objects: most commands never reach a
#: substitution or a delete call, and the guard runs on every Bash call.
_SUBSTITUTION = r"\$\s*\(([^)]*)\)?|`([^`]*)`?"


def secret_shaped(path):
    """True for a path that looks like a credential: .env and .env.* (not the
    .example template), an ssh or tls private key, .netrc, a file named
    credentials, and anything under a .ssh or .aws folder (a public .pub key
    and a certificate .pem excepted)."""
    if not isinstance(path, str):
        return False
    parts = [x for x in path.strip().replace("\\", "/").split("/") if x]
    if not parts:
        return False
    base = parts[-1].lower()
    if base.endswith(".pub") or (base.endswith(".pem") and re.search(_PUBLIC_PEM, base)):
        return False
    if any(x in _SECRET_DIRS for x in parts):
        return True
    if base == ".env" or (base.startswith(".env.") and not base.endswith(_ENV_TEMPLATES)):
        return True
    return base in _SECRET_BASENAMES or base.endswith(_SECRET_SUFFIXES)


def _argv(segment):
    """(command name, its argument words, its input files) for one simple
    command. Redirection targets are not arguments, a here string's word is
    data not a file, and leading assignments and wrappers (sudo -u x, env -u X,
    timeout -s KILL 5) are skipped with their own option values. The input
    files are the `<` targets and an `xargs -a FILE`."""
    words, inputs, pending, prev = [], [], None, ""
    for t in segment:
        if t.is_op:
            pending = t.text if t.text in _REDIRECTS else None
            if pending == "<" and prev == "<<":
                pending = "<<<"
            prev = t.text
            continue
        prev = ""
        if pending is None:
            words.append(t.text)
        elif pending == "<":
            inputs.append(t.text)
        pending = None
    k = 0
    while k < len(words):
        if _ASSIGNMENT.match(words[k]):
            k += 1
            continue
        wrapper = os.path.basename(words[k])
        valued = _WRAPPERS.get(wrapper)
        if valued is None:
            break
        k += 1
        while k < len(words):
            w = words[k]
            if w in valued:
                if wrapper == "xargs" and w == "-a" and k + 1 < len(words):
                    inputs.append(words[k + 1])
                k += 2
            elif w.startswith("-") or _ASSIGNMENT.match(w) or re.match(r"^[0-9.]+[smhd]?$", w):
                k += 1
            else:
                break
    if k >= len(words):
        return "", [], inputs
    return os.path.basename(words[k]), words[k + 1:], inputs


def _upload_file(kind, val):
    """The file one upload option names, or "" when its value is literal text."""
    if kind == "file":
        return val
    if kind == "data":
        return val[1:] if val.startswith("@") else ""
    if kind == "enc":
        head, at, tail = val.partition("@")
        return tail if at and "=" not in head else ""
    _, eq, v = val.partition("=")
    return v[1:].split(";")[0].strip("\"'") if eq and v[:1] in ("@", "<") else ""


def upload_targets(segment):
    """The files one curl or wget segment sends, in the order named. Reads the
    long, attached and clustered spellings (--data-binary @f, -d@f, -sd @f)."""
    name, args, _ = _argv(segment)
    table = _CURL_UPLOADS if name == "curl" else _WGET_UPLOADS if name == "wget" else None
    if table is None:
        return []
    out, i = [], 0
    while i < len(args):
        w = args[i]
        i += 1
        kind = val = None
        if w.startswith("--"):
            key, eq, rest = w.partition("=")
            if key in table:
                kind = table[key]
                val = rest if eq else (args[i] if i < len(args) else "")
                i += 0 if eq else 1
        elif w.startswith("-") and len(w) > 1:
            for j, ch in enumerate(w[1:], 1):
                if "-" + ch in table:
                    kind, rest = table["-" + ch], w[j + 1:]
                    val = rest if rest else (args[i] if i < len(args) else "")
                    i += 0 if rest else 1
                    break
                if name == "curl" and ch in _CURL_VALUE_FLAGS:
                    i += 0 if w[j + 1:] else 1
                    break
        if kind:
            target = _upload_file(kind, val)
            if target:
                out.append(target)
    return out


def _substituted_secret(segment):
    """A secret shaped file named inside a $( ) or backtick substitution of this
    segment, or "". Read from the segment's text because the scanner keeps the
    substitution's words in place, quoted or not."""
    text = " ".join(t.text for t in segment)
    for m in re.finditer(_SUBSTITUTION, text):
        for w in (m.group(1) or m.group(2) or "").split():
            w = w.lstrip("<").strip("\"'")
            if secret_shaped(w):
                return w
    return ""


#: What makes a here-document body secret shaped: a private key block, a cloud
#: or token prefix, or a NAME=value line whose name says secret. The body is
#: read for this and for nothing else.
_SECRET_BODY = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}"
    r"|^\s*(?:export\s+)?[A-Za-z0-9_]*(?:SECRET|TOKEN|PASSW(?:OR)?D|API_?KEY|PRIVATE_?KEY"
    r"|ACCESS_?KEY)[A-Za-z0-9_]*\s*=\s*\S")


def _secret_heredoc(segment):
    """True when a here-document body on this segment looks like a secret."""
    body = "\n".join(t.body for t in segment if t.is_op and t.body)
    return bool(body) and re.search(_SECRET_BODY, body, re.M | re.I) is not None


def _exfil_reason(secret, how):
    return (
        "BrotherSBE Bash guard: this command sends %s, which is secret shaped, off the "
        "machine (%s). Keys, tokens and .env files are not uploaded or piped to the "
        "network by an agent.\n"
        "If this is deliberate, run it yourself in a terminal, send a copy that holds "
        "no secret, or set BROTHERSBE_BASH_GUARD_OFF=1 for this session (the guard then "
        "says on stderr that it was off).\n"
        "This guard reads command text only; it did NOT prove anything about the rest "
        "of the command." % (secret, how))


def exfil_refusal(segments):
    """The refusal for one pipeline (a list of simple commands) that sends a
    secret shaped file to the network, or "" for none. Four shapes: curl or wget
    uploading one (-d @f, -T f, -F x=@f, --post-file f ...); a sink reading one
    through `<` or `xargs -a`; a sink whose argument is a substitution of one;
    and a command naming one piped into a sink anywhere later in the pipeline.
    Commands a shell is handed as text (-c, eval, here strings) are separate
    pipelines here: `analyze` expands them first."""
    reader = ""
    for seg in segments:
        name, args, inputs = _argv(seg)
        sent, how = "", ""
        if name in _UPLOADERS:
            sent = next((f for f in upload_targets(seg) if secret_shaped(f)), "")
            how = "%s uploads it" % name
        if name in EXFIL_SINKS and not sent:
            sent = next((f for f in inputs if secret_shaped(f)), "")
            how = "%s reads it from a redirect" % name
        if name in EXFIL_SINKS and not sent:
            sent = _substituted_secret(seg)
            how = "%s is given its contents through a substitution" % name
        secret_body = _secret_heredoc(seg)
        if name in EXFIL_SINKS and not sent and secret_body:
            sent, how = "a here-document body", "%s is fed it on stdin" % name
        if name in EXFIL_SINKS and not sent and reader:
            sent, how = reader, "it is piped into %s" % name
        if sent:
            return _exfil_reason(sent, how)
        if not reader:
            reader = next((a for a in args + inputs if secret_shaped(a)), "")
            reader = reader or ("a here-document body" if secret_body else "")
    return ""


def unsplittable_reason(why):
    return (
        "BrotherSBE Bash guard: this command could not be read completely (%s), so "
        "part of it was not examined, and it holds an unbalanced quote, a shell nested "
        "too deep, or a network tool or an interpreter, so it is not allowed "
        "unexamined.\n"
        "Rewrite it with balanced quotes, or set BROTHERSBE_BASH_GUARD_OFF=1 for this "
        "session (the guard then says on stderr that it was off)." % why)


#: Python, perl, ruby, node and php one liners: the family each name belongs to
#: (python3.11 is python) and the flag that carries the code.
_INTERPRETER_NAME = re.compile(r"(python|perl|ruby|node|php)[0-9.]*$")
_CODE_FLAG = {"python": r"-[A-Za-z]*c", "perl": r"-[A-Za-z]*e", "ruby": r"-[A-Za-z]*e",
              "node": r"-[a-z]*[ep]|--eval|--print", "php": r"-r"}

#: A recursive delete call in one of those languages. rmSync, rmdirSync and
#: any `.rm(` method (fs.rm, fs.promises.rm) delete a tree only with recursive set; the rest always do.
_TREE_DELETE = (
    r"\b(rmtree|removedirs|remove_tree|rm_rf|rm_r|remove_entry|rimraf|"
    r"rmSync|rmdirSync|(?<=\.)rm)\s*\(")
_RECURSIVE = r"recursive\s*:\s*true|:recursive\s*=>\s*true"
_NEEDS_RECURSIVE = frozenset(("rmSync", "rmdirSync", "rm"))
#: A first argument that names the home folder without a path literal.
_NAMES_HOME = (r"environ\s*\[\s*['\"](?:HOME|USERPROFILE)['\"]\s*\]"
               r"|(?:environ\.get|getenv)\s*\(\s*['\"](?:HOME|USERPROFILE)['\"]"
               r"|expandvars\s*\(\s*['\"][^'\"]*(?:\$\{?HOME\}?|%USERPROFILE%)"
               r"|\bPath\.home\s*\(|process\.env\.(?:HOME|USERPROFILE)|\bhomedir\s*\(")


def _interpreter_family(name):
    m = _INTERPRETER_NAME.match(name or "")
    return m.group(1) if m else ""


def interpreter_code(segment):
    """The code string of a python, node, perl, ruby or php one liner (-c, -e,
    -r, --eval), or "" when the segment is not one."""
    name, args, _ = _argv(segment)
    family = _interpreter_family(name)
    if not family:
        return ""
    flag = re.compile(_CODE_FLAG[family])
    for i, a in enumerate(args):
        if a.startswith("-") and flag.fullmatch(a) and i + 1 < len(args):
            return args[i + 1]
    return ""


def _first_argument(code, start):
    """The text of the first argument of the call whose `(` ends just before
    `start`: up to the first comma or the closing parenthesis at depth zero."""
    depth, quote = 0, ""
    for i in range(start, len(code)):
        ch = code[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return code[start:i]
            depth -= 1
        elif ch == "," and depth == 0:
            return code[start:i]
    return code[start:]


#: Two string literals side by side, joined by nothing, white space, a line
#: continuation or a `+`: one path to the language that runs them.
_ADJACENT_LITERALS = (r"""(?:'([^']*)'|"([^"]*)"|`([^`]*)`)(?:\s*\\?\s*\+?\s*)"""
                      r"""(?:'([^']*)'|"([^"]*)"|`([^`]*)`)""")


def _join_literals(arg):
    """`arg` with every run of adjacent string literals (`'.cl' 'aude'`,
    `'.cl' + 'aude'`, across a line continuation) read as the one literal."""
    def joined(m):
        text = "".join(g for g in m.groups() if g is not None)
        return ("'%s'" if '"' in text else '"%s"') % text
    while True:
        after = re.sub(_ADJACENT_LITERALS, joined, arg)
        if after == arg:
            return arg
        arg = after


def interpreter_refusal(code, judge):
    """The refusal for an interpreter one liner that deletes a tree, or "".
    `judge(path)` is the guard's own rm rule for one path: it returns why the
    path may not be deleted, or "" (so a one liner is never stricter than the
    shell for a path the shell may delete). Every string literal in the call's
    first argument is judged, so `os.path.join('build', 'x')` is judged by
    `build`; an argument with no literal (a variable from mkdtemp) names nothing
    this guard can read and is left to the Stop reconciler. A literal inside
    `[...]` or `.get(` is a dictionary key, never a path; an argument that names
    HOME (`os.environ['HOME']`, `Path.home()`, `process.env.HOME`) deletes HOME."""
    recursive = bool(re.search(_RECURSIVE, code, re.I))
    for m in re.finditer(_TREE_DELETE, code):
        if m.group(1) in _NEEDS_RECURSIVE and not recursive:
            continue
        arg = _join_literals(_first_argument(code, m.end()))
        paths = ["~"] if re.search(_NAMES_HOME, arg) else []
        for lit in re.finditer(r"(['\"`])(.*?)\1", arg):
            before = arg[:lit.start()]
            if (lit.group(2).strip() and "${" not in lit.group(2)
                    and before.count("[") <= before.count("]")
                    and not re.search(r"(?:\.get|getenv)\s*\(\s*$", before)):
                paths.append(lit.group(2))
        for path in paths:
            why = judge(path)
            if why:
                return (
                    "BrotherSBE Bash guard: this one liner deletes a directory tree with "
                    "%s() and %s.\n"
                    "Delete it with rm inside the worktree, or set BROTHERSBE_BASH_GUARD_OFF=1 "
                    "for this session (the guard then says on stderr that it was off).\n"
                    "This guard reads command text only; it did NOT prove anything about "
                    "the rest of the command." % (m.group(1), why))
    return ""


def _under_temp(path):
    """True for a path strictly inside the system temporary folders."""
    for base in (os.environ.get("TMPDIR"), "/tmp", "/var/tmp"):
        if base:
            base = os.path.realpath(base).rstrip(os.sep) + os.sep
            if path.startswith(base) and len(path) > len(base):
                return True
    return False


def _home_protected(path):
    """True for HOME itself, any folder above it, and anything under one of its
    dot folders (.ssh, .claude, .config ...). The temporary folder allowance
    never covers these, whatever temporary folder HOME happens to sit under."""
    home = os.path.realpath(os.path.expanduser("~"))
    if path == home or (home + os.sep).startswith(path.rstrip(os.sep) + os.sep):
        return True
    rel = os.path.relpath(path, home)
    return not rel.startswith("..") and rel.split(os.sep)[0].startswith(".")


# -- HD1.d: the park and consent folders -------------------------------------
#
# A parked call is answered by a file under ~/.brother/park, and a consent by one
# under ~/.brother/consent; the dispatcher trusts an answer only because the
# session cannot write there. So this rule FAILS CLOSED, before any module is
# loaded: a write that lands in either folder (or replaces ~/.brother itself) is
# refused in every spelling of the path this guard reads, and so is any command
# naming them that is not a known reader. Reading them stays ordinary.

_PARK_FOLDERS = ("park", "consent")
#: Commands that only read what they are given; a redirect they carry is still
#: judged as a write. find is a reader only without an action that writes or runs.
_PARK_READERS = frozenset(
    "ls cat head tail less more stat wc grep egrep fgrep test [ realpath readlink du "
    "echo printf diff cmp shasum sha1sum sha256sum md5 md5sum basename dirname cd pushd "
    "popd true find".split())
_FIND_ACTIONS = frozenset("-delete -exec -execdir -ok -okdir -fprint -fprint0 -fprintf "
                          "-fls".split())
#: A path this guard cannot read where it lands: a variable or a substitution.
_UNREAD_PATH = re.compile(r"[$`]")


def _brace_words(word):
    """`word` with its `{a,b}` groups expanded as bash does, without recursion (a
    crash here would fail open). None past 64 forms: the word cannot be read."""
    out, todo = [], [word]
    while todo:
        w = todo.pop()
        m = re.search(r"\{([^{}]*,[^{}]*)\}", w)
        if not m:
            out.append(w)
            continue
        todo.extend(w[:m.start()] + part + w[m.end():] for part in m.group(1).split(","))
        if len(out) + len(todo) > 64:
            return None
    return out


def _park_spot(raw, base):
    """The park or consent folder `raw` (read from `base`) lands in, or
    ~/.brother itself when it names that, or "" for anywhere else. ~, $HOME and
    ${HOME} are HOME; a glob counts for what it matches; case is ignored, since
    the default macOS volume ignores it. A brace expansion too large to read is
    answered as if it reached the folders."""
    import glob
    import itertools
    home = os.path.expanduser("~")
    roots = set(os.path.join(h, ".brother").lower() for h in (home, os.path.realpath(home)))
    words = _brace_words(raw.split("\0")[0])
    if words is None:
        return "a brace expansion too large to read"
    for word in words:
        word = os.path.expanduser(re.sub(r"^\$(?:HOME\b|\{HOME\})", lambda _: home, word))
        spot = os.path.join(base or os.sep, word)
        spots = [spot] + (list(itertools.islice(glob.iglob(spot), 64))
                          if any(c in word for c in "*?[") else [])
        for one in spots:
            low = os.path.realpath(one).lower()
            for top in roots:
                if low == top:
                    return top
                for sub in _PARK_FOLDERS:
                    d = os.path.join(top, sub)
                    if low == d or low.startswith(d + os.sep):
                        return d
    return ""


def _names_park_in_code(code):
    """True when interpreter code spells the two folders in any join of its
    literals: '.brother' beside 'park' or 'consent'."""
    low = re.sub(r"""['"`]\s*[+.,]?\s*['"`]""", "", code).lower()
    return ".brother" in low and any(sub in low for sub in _PARK_FOLDERS)


#: A pseudo terminal, which makes the answer command's terminal check pass.
_PSEUDO_TERMINAL = re.compile(
    r"\b(?:pty\.(?:fork|spawn|openpty)|os\.(?:openpty|forkpty)|openpty|forkpty)\b"
    r"|(?<![\w./-])(?:script|expect|unbuffer|socat)(?![\w.-])")


#: Commands whose arguments are themselves a command this guard reads as one:
#: wrappers, shells, launchers. The arguments of anything else (a commit
#: message, an echo, a grep pattern) are prose and run nothing.
_LAUNCH_WORDS = frozenset(_WRAPPERS) | SHELL_INTERPRETERS | frozenset(
    ("script", "expect", "unbuffer", "socat", "watch", "eval", "exec", "source", "."))


def _command_text(entries):
    """The words in command position: each simple command's first word, every
    word of one run by a wrapper, shell, launcher or interpreter (or of the
    dispatcher run by path), and the code an interpreter reads on stdin.
    Commands handed to a shell (`bash -c`, a here-document fed to bash, a
    substitution) are entries of their own and are read the same way; a
    here-document body that only feeds a file never is."""
    parts = []
    for i, e in enumerate(entries):
        first = command_name(e.seg)
        if first in _LAUNCH_WORDS or _interpreter_family(first) or "brother_hook" in first.lower():
            parts.extend(t.text for t in e.seg if not t.is_op)
        else:
            parts.append(first)
        parts.append(_interpreter_stdin_code(entries, i))
    return "\n".join(parts)


def _answer_refusal(entries):
    """Why this command runs the park's answer command, or "": the dispatcher
    with `--answer` in command position, or a pseudo terminal in command
    position beside `--answer` or the two folders (a word that resolves there,
    or interpreter code that spells them). Prose naming them is allowed."""
    text = _command_text(entries)
    low = re.sub(r"""['"`]\s*[+.,]?\s*['"`]""", "", text).lower()
    answer = "--answer" in low
    if answer and "brother_hook" in low:
        return "this command runs the park's answer command (brother_hook.py --answer)"
    if not _PSEUDO_TERMINAL.search(low):
        return ""
    if answer:
        return "this command opens a pseudo terminal beside --answer"
    if _names_park_in_code(text) or any(
            _park_hint(t.text) and _park_spot(t.text, b)
            for e in entries for t in e.seg if not t.is_op for b in _bases(e)):
        return "this command opens a pseudo terminal beside the park or consent folder"
    return ""


def _park_hint(text):
    low = text.lower()
    return ("brother" in low or "park" in low or "consent" in low or "{" in text
            or any(c in text for c in "*?["))


def park_refusal(entries):
    """The refusal for a command that may write the park or consent folders, or
    "". Judged on the command's own words; no module, no registry."""
    def refuse(what):
        return ("BrotherSBE Bash guard: %s. A parked call is answered by a person outside "
                "the session, and the dispatcher trusts that answer only because the "
                "session cannot write ~/.brother/park or ~/.brother/consent.\n"
                "Answer it yourself in a terminal outside the session, or set "
                "BROTHERSBE_BASH_GUARD_OFF=1 for this session (the guard then says on "
                "stderr that it was off).\n"
                "This guard reads command text only; it did NOT prove anything about the "
                "rest of the command." % what)
    why = _answer_refusal(entries)
    if why:
        return refuse(why)
    touched = any(_park_spot(".", b) for e in entries for b in _bases(e))
    found = []
    for i, e in enumerate(entries):
        name, args, _ = _argv(e.seg)
        code = interpreter_code(e.seg) or _interpreter_stdin_code(entries, i)
        if code and _names_park_in_code(code):
            return refuse("this %s one liner names the park or consent folder" % name)
        try:
            cands = segment_candidates(e.seg, 0)
        except Unclassifiable:
            cands = [Candidate(t.text, "mentioned in the command", True)
                     for t in e.seg if not t.is_op]
        reader = name in _PARK_READERS and not (
            name == "find" and any(a in _FIND_ACTIONS for a in args))
        for t in e.seg:
            if not t.is_op and _ASSIGNMENT.match(t.text) and _park_hint(t.text):
                touched = touched or any(
                    _park_spot(t.text.split("=", 1)[1], b) for b in _bases(e))
        for c in cands:
            if not c.write and not _park_hint(c.raw):
                continue
            for b in _bases(e):
                # an option's value (`--out=PATH`) is a path too
                spot = _park_spot(c.raw, b) or ("=" in c.raw and _park_spot(
                    c.raw.rsplit("=", 1)[1], b))
                if spot:
                    touched = True
                    if c.write or not reader:
                        return refuse("%s (%s) reaches %s" % (c.raw, c.why, spot))
            if c.write and _UNREAD_PATH.search(c.raw):
                found.append(c)
    if touched and found:
        return refuse("this command names the park or consent folder and writes %s, a path "
                      "this guard cannot read" % found[0].raw)
    return ""


# -- a shell out from inside a one liner --------------------------------------

#: A call that runs another program from inside interpreter code.
_SHELL_OUT = re.compile(
    r"\b(?:subprocess|os\.(?:system|popen|exec\w*|spawn\w*|posix_spawn\w*)|pty\.spawn"
    r"|child_process|exec(?:File)?Sync|spawnSync|execFile|spawn|system|exec|popen|Open3|qx)\b"
    r"|%x[({\[]")
#: Code that touches the environment a program it runs inherits.
_ENV_TOUCH = re.compile(
    r"\b(?:environ|putenv|unsetenv)\b|process\.env\b|%ENV\b|\$ENV\s*\{|\bENV\s*\[|\benv\s*[=:]")
_NAMES_GIT = re.compile(r"(?i)(?<![A-Za-z0-9_])git(?![A-Za-z0-9_])")
#: Where a comment starts in each family: at the start, or after white space or
#: `;`, outside a string. Never inside a string, and never perl's `//` operator.
_COMMENT_START = {"python": ("#",), "perl": ("#",), "ruby": ("#",), "php": ("#", "//"),
                  "node": ("//",)}


def _without_comments(code, family):
    """`code` with its comments removed, strings kept, quote aware."""
    starts = _COMMENT_START.get(family, ("#",))
    out, i, n, quote = [], 0, len(code), ""
    while i < n:
        ch = code[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
        elif (i == 0 or code[i - 1] in " \t\n;") and any(code.startswith(s, i) for s in starts):
            j = code.find("\n", i)
            i = n if j < 0 else j
            continue
        elif family == "node" and code.startswith("/*", i) and (i == 0 or code[i - 1] in " \t\n;"):
            j = code.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _interpreter_stdin_code(entries, i):
    """The code an interpreter in entries[i] reads on stdin (`python3 - <<EOF`,
    `python3 <<< '...'`, `echo '...' | node`), or "" when it runs a script, a
    module or a -c/-e payload."""
    name, args, inputs = _argv(entries[i].seg)
    if not _interpreter_family(name) or interpreter_code(entries[i].seg) or inputs:
        return ""
    k = 0
    while k < len(args) and args[k].startswith("-") and args[k] != "-":
        k += 2 if args[k] in ("-W", "-X", "-I") else 1
    if k < len(args) and args[k] != "-":
        return ""
    e = entries[i]
    texts = list(_herestrings(e.seg))
    for p in entries[:i + 1]:
        if p.pipe != e.pipe:
            continue
        texts.extend(t.body for t in p.seg if t.is_op and t.body)
        pname, pargs, _ = _argv(p.seg)
        if pname in ("echo", "printf") and p is not e:
            texts.append(" ".join(a for a in pargs if not a.startswith("-")))
    return "\n".join(texts)


def shell_out_effect(entries, root, common):
    """Refuse, in the Brother source, an interpreter one liner (or code it reads
    on stdin) that runs another program and either names git anywhere outside a
    comment or touches the environment that program inherits. Such a call is a
    git command whose text this guard cannot read (its variables may be built at
    run time, so the transport rules cannot see them), and a fetch like command
    the guard cannot read is refused there. A shell out to anything else, and
    git named only in a comment, stay allowed; a stranger's repository is not
    affected."""
    for i, e in enumerate(entries):
        code = interpreter_code(e.seg) or _interpreter_stdin_code(entries, i)
        if not code:
            continue
        name = _argv(e.seg)[0]
        family = _interpreter_family(name)
        plain = _without_comments(code, family)
        out = _SHELL_OUT.search(code) or (family in ("perl", "ruby") and "`" in plain)
        if not out:
            continue
        joined = re.sub(r"""['"`]\s*[+.]?\s*['"`]""", "", plain)
        why = ("names git" if _NAMES_GIT.search(joined) else
               "sets the environment it runs under" if _ENV_TOUCH.search(code) else "")
        if not why or not common.is_brother_source(e.cwd or root):
            continue
        return EffectFinding(reason=(
            "BrotherSBE Bash guard: this %s one liner runs another program and %s. In the "
            "Brother source that is a git command whose text this guard cannot read (a "
            "variable built at run time can send a fetch of a configured remote somewhere "
            "else), and such a command is refused there.\n"
            "Run git as its own command, or do it yourself in a terminal, or set "
            "BROTHERSBE_BASH_GUARD_OFF=1 for this session (the guard then says on stderr "
            "that it was off).\n"
            "This guard reads command text only; it did NOT prove anything about the rest "
            "of the command." % (name, why)))
    return None


# -- the commands a shell is handed as text ----------------------------------

def _shell_mode(args):
    """How a shell reads its commands, a ShellMode: kind "c" when a -c (or a bundled
    -lc, -euc) sits among its options, the payload being the first word after
    them; kind "script" when it runs a file; "stdin" otherwise. The
    shell's own options are skipped: `-o pipefail`, `+o history`, `--norc`,
    `-x`, and `--`."""
    has_c, i = False, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a in ("--rcfile", "--init-file"):
            i += 2
            continue
        if re.fullmatch(r"[-+][A-Za-z]+", a):
            # A bundle of short options (-lc, -ceu, -eou): a c anywhere in it is
            # the -c, and an o anywhere in it takes the next word as its value.
            has_c = has_c or (a[0] == "-" and "c" in a)
            i += 2 if ("o" in a or "O" in a) else 1
            continue
        if a == "-" or not a.startswith(("-", "+")):
            break
        i += 1
    rest = args[i:]
    if has_c:
        return ShellMode("c", rest[0] if rest else "")
    if rest and rest[0] != "-":
        return ShellMode("script", "")
    return ShellMode("stdin", "")


def _herestrings(segment):
    """The words fed to a command as here strings (`<<< word`)."""
    out, prev2, prev = [], "", ""
    for t in segment:
        if t.is_op:
            prev2, prev = prev, t.text
            continue
        if prev == "<" and prev2 == "<<":
            out.append(t.text)
        prev2 = prev = ""
    return out


def _scan_substitution(text, j):
    """The index of the `)` that closes a `$(` whose body starts at `text[j]`,
    or len(text) when none does. A `)` inside single or double quotes is text,
    a quoted `$(` is a substitution in its own right, and a backslash escapes
    the next character."""
    n, depth = len(text), 0
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "'":
            k = text.find("'", j + 1)
            j = n if k < 0 else k + 1
            continue
        if ch == '"':
            j += 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 2
                elif text[j] == "$" and text[j + 1:j + 2] == "(":
                    j = _scan_substitution(text, j + 2) + 1
                else:
                    j += 1
            j += 1
            continue
        if ch == "$" and text[j + 1:j + 2] == "(":
            j = _scan_substitution(text, j + 2) + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return j
            depth -= 1
        j += 1
    return n


def _substitutions(text):
    """The command texts inside the `$( ... )` and backtick substitutions of
    `text`, parentheses balanced, so `$(echo $(curl x))` gives `echo $(curl x)`
    (the inner one is found when that text is read in turn). A substitution with
    no closing parenthesis runs to the end of the text."""
    out, i, n = [], 0, len(text)
    while i < n:
        m = re.compile(r"\$\s*\(|`").search(text, i)
        if not m:
            break
        if m.group() == "`":
            j = text.find("`", m.end())
            j = n if j < 0 else j
            out.append(text[m.end():j])
            i = j + 1
            continue
        out.append(text[m.end():_scan_substitution(text, m.end())])
        i = m.end()
    return [t for t in out if t.strip()]


def _embedded_commands(entries):
    """(text, folder, lenient) for every command text something in `entries`
    hands to a shell: `sh -lc 'x'`, `eval x`, `bash <<< 'x'`, the text that
    reaches a shell's stdin (`echo 'x' | bash`, `cat <<EOF | sh`, `bash <<EOF`),
    and every `$( )` or backtick substitution in a command or an unquoted
    here-document body. `lenient` marks a substitution: a text cut out of a
    longer word may not parse on its own, and is then refused only when it
    names a tool or an interpreter."""
    for e in entries:
        for t in e.seg:
            if t.is_op and t.body and not t.body_quoted and ("$(" in t.body or "`" in t.body):
                for text in _substitutions(t.body):
                    yield text, e.cwd, True
        joined = " ".join(t.text if t.is_op else t.plain for t in e.seg)
        if "$" in joined or "`" in joined:
            for text in _substitutions(joined):
                yield text, e.cwd, True
    for i, e in enumerate(entries):
        name, args, _ = _argv(e.seg)
        if name == "eval":
            if args:
                yield " ".join(args), e.cwd, False
            continue
        if name not in SHELL_INTERPRETERS:
            continue
        mode = _shell_mode(args)
        payload = mode.payload
        mode = mode.kind
        if mode == "c":
            if payload:
                yield payload, e.cwd, False
            continue
        if mode != "stdin":
            continue
        for text in _herestrings(e.seg):
            yield text, e.cwd, False
        for p in entries[:i + 1]:
            if p.pipe != e.pipe:
                continue
            for t in p.seg:
                if t.is_op and t.body:
                    yield t.body, e.cwd, False
            pname, pargs, _ = _argv(p.seg)
            if pname in ("echo", "printf") and p is not e:
                words = [a for a in pargs if not a.startswith("-")]
                yield " ".join(words[1:] if pname == "printf" else words), e.cwd, False


# -- the folder a command runs in --------------------------------------------

class Entry(object):
    """One simple command with where it sits: the separator before and after
    it, the parenthesis depth at its start, at its command word and at its end,
    which pipeline it belongs to, and the folder it will run in."""

    def __init__(self, seg, pre, post, d0, dcmd, dend, pipe):
        self.seg, self.pre, self.post = seg, pre, post
        self.d0, self.dcmd, self.dend, self.pipe = d0, dcmd, dend, pipe
        self.cwd = None
        #: Where the command would run if the last cd it could not resolve
        #: (a folder that does not exist yet) does succeed; None when there is none.
        self.alt = None


def segment_flow(tokens):
    """The token stream as Entries, in order. Same segments as split_commands,
    with what that drops: the separators and the parentheses."""
    entries, seg, pre = [], [], ""
    depth = d0 = pipe = 0
    dcmd = None
    for t in tokens:
        if t.is_op and t.text in SEPARATORS:
            if seg:
                if _strip_openers(seg):
                    entries.append(Entry(_strip_openers(seg), pre, t.text, d0,
                                         d0 if dcmd is None else dcmd, depth, pipe))
                if t.text != "|":
                    pipe += 1
            seg, dcmd, pre = [], None, t.text
            continue
        if not seg:
            d0 = depth
        seg.append(t)
        if t.is_op and t.text == "(":
            depth += 1
        elif t.is_op and t.text == ")":
            depth = max(0, depth - 1)
        elif not t.is_op and dcmd is None:
            dcmd = depth
    if seg and _strip_openers(seg):
        entries.append(Entry(_strip_openers(seg), pre, "", d0,
                             d0 if dcmd is None else dcmd, depth, pipe))
    return entries


def _cd_step(e, cur, stack):
    """The folder after this segment, or None when it is not known. A cd is
    followed only when it really changes the folder of what comes next: it runs
    in this shell (not in a pipe, not backgrounded), is not conditional (`||`),
    and its target is a directory that exists. A cd that fails, or that cannot
    be read (`cd -`, `cd $X`), leaves the folder unknown."""
    name, args, _ = _argv(e.seg)
    if name not in ("cd", "pushd", "popd"):
        return cur
    if e.pre == "|" or e.post in ("|", "&"):
        return cur
    if e.pre == "||" or e.post == "||":
        return None
    if name == "popd":
        return stack.pop() if stack else None
    dirs = [a for a in args if not a.startswith(("-", "+"))]
    if not dirs:
        return os.path.expanduser("~") if not args and name == "cd" else None
    d = dirs[0]
    if any(c in d for c in "$`*?[{"):
        return None
    d = os.path.expanduser(d)
    if not os.path.isabs(d):
        if cur is None:
            return None
        d = os.path.join(cur, d)
    d = os.path.normpath(d)
    if not os.path.isdir(d):
        return None
    if name == "pushd":
        stack.append(cur)
    return d


def _written_cd_target(e, base):
    """Where a cd this guard could not follow would land if it succeeds: its
    target as written, joined to `base` and normalised, whether or not it
    exists. None when the target is not in the text (`cd -`, `cd $X`)."""
    name, args, _ = _argv(e.seg)
    dirs = [a for a in args if not a.startswith(("-", "+"))]
    if name not in ("cd", "pushd") or not dirs or any(c in dirs[0] for c in "$`*?[{"):
        return None
    d = os.path.expanduser(dirs[0])
    return os.path.normpath(d if os.path.isabs(d) else os.path.join(base, d))


def effective_cwd(segments, start, before=None, alts=None):
    """The folder after `segments` (Entries, or bare segments) have run,
    following `cd <dir>`, `pushd <dir>` and `popd` only where they hold (see
    `_cd_step`) and undoing a cd made inside parentheses. A folder that is not
    known reads as `start`, the folder the session began in. When `before` is a
    list, the folder each segment starts in is appended; when `alts` is a list,
    so is the folder it would start in if the last unresolved cd had succeeded
    (see Entry.alt), so a relative path after `mkdir d && cd d` is judged
    against both."""
    cur, alt, saved, level, stack = start, None, [], 0, []
    for item in segments:
        e = item if isinstance(item, Entry) else Entry(item, "", ";", 0, 0, 0, 0)
        while level > e.dcmd:
            cur, alt = saved.pop()
            level -= 1
        while level < e.dcmd:
            saved.append((cur, alt))
            level += 1
        if before is not None:
            before.append(start if cur is None else cur)
        if alts is not None:
            alts.append(alt)
        base = cur if cur is not None else (alt if alt is not None else start)
        new = _cd_step(e, cur, stack)
        if new is None and _argv(e.seg)[0] in ("cd", "pushd"):
            alt = _written_cd_target(e, base)
        elif new is not None and _argv(e.seg)[0] in ("cd", "pushd", "popd"):
            alt = None
        cur = new
        while level > e.dend:
            cur, alt = saved.pop()
            level -= 1
        while level < e.dend:
            saved.append((cur, alt))
            level += 1
    return start if cur is None else cur


def _toplevel(start):
    """The folder above `start` (or `start` itself) that holds a `.git` entry,
    read from disk; `start` when none does."""
    here = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.abspath(start)
        here = parent


def _pathspec_raws(cand, base, owner_patterns):
    """The paths a git pathspec candidate stands for, resolved the way git does:
    a top relative spelling against the real repository top, and a glob against
    the files it matches plus the owner files its pattern would match (the
    marker may be gone from disk and still in the index)."""
    raw = cand.raw
    top = _toplevel(base)
    if raw.startswith((FILE_MARK, NUL_MARK)):
        # a file of pathspecs: judge each path in it; unreadable counts as the marker
        is_nul = raw.startswith(NUL_MARK)
        prefix, _, listed = raw[len(NUL_MARK if is_nul else FILE_MARK):].partition("\x01")
        path = os.path.join(base, listed)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(1000000)
        except (OSError, ValueError):
            return [os.path.join(top, m) for m in _MARKER_TOP_PATHS]
        # Both readings, whatever the options said: NUL separated items as git reads
        # them with -z (no unquoting), and lines as git reads them without it (C
        # unquoted, each cut at its first NUL, where git's C string ends).
        lines = [line.split("\0")[0] for line in text.splitlines()]
        found = []
        for spec in _list_raws(text.split("\0"), prefix, False) + _list_raws(lines, prefix, True):
            inner = Candidate(spec, cand.why, cand.write)
            inner.pathspec = True
            found.extend(_pathspec_raws(inner, base, owner_patterns))
        return found or [path]
    if raw.startswith(TOP_MARK):
        raw = os.path.join(top, raw[len(TOP_MARK):])
    if not any(ch in raw for ch in "*?["):
        return [raw]
    import fnmatch
    import glob
    spec = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    found = sorted(glob.glob(spec))[:200]
    for pattern in owner_patterns:
        if not any(ch in pattern for ch in "*?["):
            concrete = os.path.normpath(os.path.join(top, pattern))
            if fnmatch.fnmatchcase(concrete, spec):
                found.append(concrete)
    return found or [raw]


def _bases(e):
    """The folders to judge a command's relative paths against: where it runs,
    and where it would run if its last unresolved cd had succeeded."""
    return [e.cwd] + ([e.alt] if e.alt is not None and e.alt != e.cwd else [])


def analyze(tokens, start, notes, depth=0, unit=None):
    """Every simple command to judge, as Entries carrying the folder each runs
    in: the command line's own, then (to MAX_DEPTH) those of every command text
    a shell in it is handed. A text that cannot be split is refused when it
    could carry a tool (an Unclassifiable is raised) and noted otherwise."""
    unit = unit if unit is not None else [0]
    entries = segment_flow(tokens)
    for e in entries:
        e.pipe = (unit[0], e.pipe)
    unit[0] += 1
    cwds, alts = [], []
    effective_cwd(entries, start, before=cwds, alts=alts)
    for e, c, a in zip(entries, cwds, alts):
        e.cwd, e.alt = c, a
    out = list(entries)
    if depth < MAX_DEPTH:
        for text, at, lenient in list(_embedded_commands(entries)):
            try:
                inner = tokenize(text)
            except Unclassifiable as exc:
                if (exc.soft or lenient) and not DANGER_WORDS.search(text):
                    notes.append("sbe_bash_write_guard: a command text handed to a shell "
                                 "could not be read (%s), so it was NOT examined." % exc)
                    continue
                raise
            out.extend(analyze(inner, at, notes, depth + 1, unit))
    elif any(True for _ in _embedded_commands(entries)):
        raise Unclassifiable(
            "a command text is nested more than %d shell levels deep, past the depth "
            "this guard reads, and a command it did not read is not allowed" % MAX_DEPTH)
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
        # Walk the segment's own words (assignments, interpreter name, then
        # its options) so an option taking a separate value word (-X dev,
        # -W error) does not have that value mistaken for the script file.
        value_flags = ("-X", "-W")
        words = [t.text for t in segment if not t.is_op]
        k = 0
        while k < len(words) and _ASSIGNMENT.match(words[k]):
            k += 1
        k += 1  # the interpreter name itself
        after = []
        while k < len(words):
            w = words[k]
            if _is_flag(w):
                k += 2 if w in value_flags else 1
                continue
            after.append(w)
            k += 1
        if after:
            return after[0], (after[1] if len(after) > 1 else None)
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
        # A count, never the ids: a refusal is read by whoever the agent shows
        # it to, and a task id names someone else's work (P8's leak).
        task_state = ("%d open task(s) exist and none declares %s in its ownedPaths"
                      % (len(live_tasks), rel))
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
        "No record in .sbe/break-glass.json unlocks this command: this guard never "
        "reads that file. A valid record listing this path only lets the Stop "
        "reconciler accept a change to it, so a deliberate, reviewed exception is "
        "recorded there and made by a person by hand, not through this session's "
        "tools.\n"
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

        # Fail closed (GUARD1): a command that cannot be split was not examined,
        # and a secret may be leaving in it. Judged before any module is loaded,
        # so an unimportable sibling cannot switch the exfiltration rule off.
        # A hard failure (an unbalanced quote) is refused; a soft one (too long,
        # a trailing backslash) only when its text names a tool or an interpreter.
        try:
            entries = analyze(tokenize(command), cwd, notes)
        except Unclassifiable as e:
            if e.soft and not DANGER_WORDS.search(command):
                raise OpenFail(
                    "this Bash command could not be split into commands and words (%s) "
                    "and names no network tool or interpreter, so it is allowed "
                    "unexamined" % e)
            return Decision(deny_payload(unsplittable_reason(e)), notes)
        for _, group in itertools.groupby(entries, key=lambda e: e.pipe):
            why = exfil_refusal([e.seg for e in group])
            if why:
                return Decision(deny_payload(why), notes)
        why = park_refusal(entries)
        if why:
            return Decision(deny_payload(why), notes)

        fence_mod = require_fence_module()
        surface_mod = require_surface_module()
        common_mod = require_common_module()
        tasks_mod = require_tasks_module()

        try:
            data = tasks_mod.load_registry(root)
        except tasks_mod.RegistryUnusable as e:
            raise OpenFail(
                "the task registry could not be read (%s), so this session's declared "
                "scope is unknown" % e)
        live = tasks_mod.open_tasks(data)

        def tree_delete_judge(at):
            """The guard's rm rule for one path, for an interpreter one liner."""
            def judge(target):
                target = os.path.expanduser(target)
                rel = fence_mod.canonical_target(root, target, at)
                if rel is None:
                    absolute = os.path.realpath(os.path.join(at, target))
                    # A temporary folder is fair game, but never HOME (or what
                    # holds it, or its dot folders) and never the ones the
                    # worktree itself sits in.
                    if _under_temp(absolute) and not _home_protected(absolute) and not (
                            os.path.realpath(root) + os.sep).startswith(
                                absolute.rstrip(os.sep) + os.sep):
                        return ""
                    return "%s is outside the worktree" % target
                family = protected_family(fence_mod, surface_mod, common_mod, root, rel)
                if not family or any(
                        fence_mod.paths_overlap(rel, claim, root)
                        for t in live for claim in (t.get("ownedPaths") or [])):
                    return ""
                return "%s is a protected path (%s)" % (rel, family)
            return judge

        # Cross-project execution check, ahead of the write-family check: it is
        # a categorical refusal by name, not a scope question the task
        # registry's ownedPaths could ever answer, since ownedPaths declares
        # paths inside THIS project and another project's tool is never one.
        root_real = os.path.realpath(root)
        for e in entries:
            hit = cross_project_candidate(e.seg, root_real, e.cwd)
            if hit is None:
                continue
            if hit.other_root is None:
                notes.append(hit.no_data_note)
                continue
            return Decision(
                deny_payload(cross_project_reason(
                    hit.raw, hit.abs_path, hit.other_root, command)),
                notes)

        # Command effects, same priority as the cross-project check above:
        # a categorical refusal read off the command itself, not a path
        # question the write-family loop below could ever ask, since an
        # effect like a push carries no path a task could declare.
        finding = redirecting_env_effect(entries, root, common_mod)
        if finding is not None:
            return Decision(deny_payload(finding.reason), notes)
        finding = shell_out_effect(entries, root, common_mod)
        if finding is not None:
            return Decision(deny_payload(finding.reason), notes)

        for i in range(len(entries)):
            for rule in (metadata_effect, content_apply_effect):
                finding = rule(entries, i, root, common_mod)
                if finding is not None:
                    return Decision(deny_payload(finding.reason), notes)

        for e in entries:
            for effect_rule in COMMAND_EFFECTS:
                finding = effect_rule(e.seg, root, e.cwd, command)
                if finding is None:
                    continue
                if finding.note:
                    notes.append(finding.note)
                if finding.reason:
                    return Decision(deny_payload(finding.reason), notes)

        for e in entries:
            code = interpreter_code(e.seg)
            for base in _bases(e):
                why = interpreter_refusal(code, tree_delete_judge(base)) if code else ""
                if why:
                    return Decision(deny_payload(why), notes)

        candidates = []
        for e in entries:
            try:
                found = segment_candidates(e.seg, 0)
                candidates.extend((c, base) for base in _bases(e) for c in found)
            except Unclassifiable as e:
                notes.append(
                    "sbe_bash_write_guard: part of this command could not be read (%s), "
                    "so that part was NOT examined and is allowed unexamined." % e)

        expanded = []
        for c, c_cwd in candidates:
            if not c.pathspec:
                expanded.append((c, c_cwd))
                continue
            for raw in _pathspec_raws(c, c_cwd, common_mod.OWNER_PATTERNS):
                one = Candidate(raw, c.why, c.write)
                expanded.append((one, c_cwd))

        gitdirs = [os.path.realpath(g) for g in _repo_dirs(root) if g]
        mentioned = []
        for c, c_cwd in expanded:
            rel = fence_mod.canonical_target(root, c.raw, c_cwd)
            if c.write and c.raw and gitdirs:
                # a gitdir reached through a `.git` file's pointer (a linked worktree,
                # a submodule) or core.worktree lies outside the project folder and is
                # the same metadata: map a path inside it to a `.git/` path
                spot = os.path.realpath(os.path.join(c_cwd, os.path.expanduser(c.raw)))
                for gd in gitdirs:
                    if spot == gd or spot.startswith(gd + os.sep):
                        rel = ".git/" + os.path.relpath(spot, gd)
                        break
            if rel is None:
                # Outside the project root. This guard governs a project, not
                # the filesystem, exactly as both sibling hooks do.
                continue
            family = protected_family(fence_mod, surface_mod, common_mod, root, rel,
                                      c.write)
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
        common_mod = require_common_module()
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
            family = protected_family(fence_mod, surface_mod, common_mod, root, rel)
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
