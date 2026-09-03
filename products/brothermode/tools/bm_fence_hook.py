#!/usr/bin/env python3
"""BrotherMode PreToolUse fence hook: the point where "one writer per file"
stops being a ledger entry and becomes an enforcement boundary.

WHY THIS EXISTS (audit finding 8, confirmed by execution)
  BrotherMode's headline promise is that one record owns a set of paths and
  nobody else writes them. Until this file, that promise was recorded and
  never checked: bm_store.py refuses a second CLAIM that overlaps an active
  fence, but nothing sat between an agent and an actual file write. So an
  agent could edit before claiming, claim an incomplete path set and edit
  the rest, edit a path another record owned, or skip BrotherMode entirely.
  The installed hooks were SessionStart, SessionEnd, Stop and PreCompact:
  none of them can refuse anything, because all of them run after or beside
  the write, never in front of it.

  Claude Code's PreToolUse hook is the mechanism that closes this. It
  receives the tool name and its input as JSON on stdin and may return a
  deny decision that stops the call before the tool runs. Contract read from
  https://code.claude.com/docs/en/hooks on 2026-07-27; the exact stdin
  fields and the exact deny shape this file emits are quoted in
  docs/HOOKS.md, which is the document to update if that contract moves.

THE THREE RULES THIS FILE OBEYS
  1. FAIL OPEN, LOUDLY. This hook sits in front of every edit the founder
     makes. A hook that failed closed on its own bug would brick editing
     entirely, so every failure path here (no root, no store, unreadable
     store, corrupt store, unparseable payload, zero claims, any unexpected
     exception) allows the write and prints the reason to stderr. A refusal
     must always come from a real ownership conflict, never from this file
     being broken.
  2. STDOUT IS THE DECISION CHANNEL AND NOTHING ELSE. Claude Code parses
     stdout as JSON. Every diagnostic goes to stderr. That is why this file
     has exactly two output funnels (_out, _warn) and no bare print.
  3. CANONICAL PATHS ONLY. A comparison between unresolved path strings is
     bypassed by '..', by a symlink, by a relative path typed from a
     subdirectory, or by case on a case-insensitive filesystem. Every target
     path is realpath'd and expressed root-relative before it is compared,
     and the comparison itself is bm_store.paths_overlap, the same function
     the store uses to admit a claim, so the fence the hook enforces and the
     fence the store granted can never drift apart.

IDENTITY (audit finding 8B)
  See docs/HOOKS.md for the full statement including the residual limit. In
  short: a session's secret token lives in an owner-only file inside
  .brothermode and is never printed anywhere; the PUBLIC session label that
  STATE.md shows is a one-way hash of that token. Ownership is
  record.session_id == hash(my token). Reading the public label out of
  STATE.md and passing it as your own session therefore buys nothing: the
  hook derives your label from the token you can actually open, not from the
  string you claim to be.

Python 3.9, standard library only, cross-platform. No em or en dashes
anywhere in this file, its comments, or its output.
"""
import hashlib
import io
import json
import os
import posixpath
import secrets
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Loaded by path rather than by package import: tools/ is not a package, and
# the hook is invoked by Claude Code with an arbitrary cwd, so a plain
# `import bm_store` would resolve against sys.path and could pick up a
# different checkout. Deferred into a function so an import failure is a
# FAIL-OPEN, printed to stderr, rather than a traceback that Claude Code
# would surface as a broken hook in front of every edit.
_BS = None
_BS_ERROR = None


def _load_store_module():
    """Import tools/bm_store.py beside this file, or return None and record
    why. Never raises: an unimportable store is a fail-open condition."""
    global _BS, _BS_ERROR
    if _BS is not None or _BS_ERROR is not None:
        return _BS
    try:
        import importlib.util
        path = os.path.join(HERE, "bm_store.py")
        spec = importlib.util.spec_from_file_location("bm_store", path)
        if spec is None or spec.loader is None:
            _BS_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("bm_store", mod)
        _BS = mod
        return _BS
    except Exception as e:
        _BS_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# Output funnels. Two of them, on purpose (same discipline bm_store.py
# enforces structurally): stdout carries the decision JSON and nothing else,
# stderr carries every diagnostic. Anything printed to stdout that is not the
# decision object corrupts the hook protocol.
# ---------------------------------------------------------------------------

def _out(s):
    sys.stdout.write(s)
    sys.stdout.flush()


def _warn(s):
    sys.stderr.write(s if s.endswith("\n") else s + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# The tool surface this hook governs.
# ---------------------------------------------------------------------------

#: Tools that write a file through a structured, parseable path argument.
#: Bash is DELIBERATELY ABSENT from this set: a shell command can write any
#: file, and no reliable parse of arbitrary shell exists, so pretending to
#: gate it would be a guarantee this file cannot keep (stated as a known gap
#: in docs/HOOKS.md rather than papered over here). ONE Bash shape is gated
#: anyway, in decide() rather than here: the apply_patch envelope, a
#: structured write wearing a Bash costume, which is how EVERY file write
#: arrives under the OpenAI Codex CLI (measured 2026-08-05 on codex-cli
#: 0.146.0, recorded in docs/RUNTIMES.md). Arbitrary shell that writes files
#: any other way remains the stated gap.
WRITE_TOOLS = frozenset((
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "CreateDirectory",
    "Delete",
))

#: The apply_patch envelope grammar this hook reads, and all of it. Between
#: "*** Begin Patch" and "*** End Patch", file directives are lines
#: "*** Add File: <path>", "*** Update File: <path>", "*** Delete File:
#: <path>", and a rename's "*** Move to: <path>", whose TARGET is also a
#: written path. Grammar taken from the payload captured off a real Codex
#: 0.146 session on 2026-08-05, not from memory. Codex fires PreToolUse and
#: never PostToolUse for apply_patch (measured twice), so the decision made
#: here is the WHOLE decision: there is no second look after the write.
#:
#: TWO STRUCTURAL FACTS the parser must respect, both found by an adversarial
#: refuter on 2026-08-06 (docs/program/absolute-lead/evidence/L06):
#:   1. A real directive sits at COLUMN 0. A line that begins with a space,
#:      "+" or "-" is hunk body (context, added, removed), i.e. CONTENT of
#:      the file being written, NEVER a directive. Stripping leading
#:      whitespace before matching turned a context line that quotes the
#:      grammar into a phantom directive and fenced a file the patch never
#:      touched. So directives are matched at column 0 only.
#:   2. The envelope is a WRITE only when apply_patch is the program that
#:      actually receives it. The marker text appears at the start of lines
#:      inside "cat > doc <<EOF ...", inside a "git commit" message, inside
#:      this very file: none of those invoke apply_patch. So the parser reads
#:      the heredoc that carries the envelope and only treats it as a write
#:      when the command owning that heredoc is apply_patch.
APPLY_PATCH_BEGIN = "*** Begin Patch"
APPLY_PATCH_DIRECTIVES = (
    "*** Add File:",
    "*** Update File:",
    "*** Delete File:",
    "*** Move to:",
)
#: The one token that has to be present for any of the heredoc work to run.
#: A command without it cannot be an apply_patch write, so this is the fast
#: path out taken before any parsing, on EVERY Bash call under Claude Code
#: once the matcher widens.
APPLY_PATCH_CMD = "apply_patch"
#: Hunk-body line openers: a line starting with any of these is content, not
#: a directive. Column 0 is the whole rule; these are named for the reader.
_HUNK_BODY_OPENERS = (" ", "+", "-")

#: The deny for an envelope whose file directives could not be read. A
#: LITERAL, same rule as _FAIL_REASONS: at the moment it is emitted nothing
#: has been verified, so it quotes no payload content.
_UNREADABLE_PATCH_REASON = (
    "BrotherMode fence: this Bash command carries an apply_patch envelope "
    "but none of its file directives could be read, so the fence cannot "
    "certify which files it would write. The write is refused: a fence that "
    "shrugs at an unreadable patch is no fence at all. Re-issue the patch "
    "with well formed directive lines (Add File, Update File, Delete File, "
    "Move to), or make the change through a structured write tool such as "
    "Edit or Write.")

#: Keys inside tool_input that carry a filesystem path. Collected across the
#: built-in write tools listed in the hooks documentation. Unknown keys are
#: ignored; an unknown WRITE tool that carries none of these keys produces
#: zero targets, which is a fail-open (see decide()), not a silent allow.
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath", "target_file")

FENCE_DIRNAME = "fence"
TOKEN_SUFFIX = ".token"
LABEL_PREFIX = "bm1-"
_SLOT_DOMAIN = "bm-fence-slot-v1|"
_LABEL_DOMAIN = "bm-fence-label-v1|"
_TOKEN_BYTES = 32
_LABEL_HEX = 24


# ---------------------------------------------------------------------------
# Session identity.
# ---------------------------------------------------------------------------

def _sha256_hex(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fence_dir(root):
    """Beside the store, inside the same .brothermode directory, so the
    token inherits whatever containment and gitignore treatment the store
    already has. The directory name comes from bm_store.STORE_DIRNAME rather
    than a second literal: one of the two would eventually be changed alone."""
    bs = _load_store_module()
    store_dirname = bs.STORE_DIRNAME if bs is not None else ".brothermode"
    return os.path.join(root, store_dirname, FENCE_DIRNAME)


def token_slot(session_id):
    """The FILENAME a session's token lives under, derived from the harness
    session id. Derived rather than stored so two concurrent sessions never
    race over one file, and hashed rather than used literally so a session id
    containing a path separator or a reserved Windows character cannot escape
    the fence directory."""
    return _sha256_hex(_SLOT_DOMAIN + session_id)[:32]


def token_path(root, session_id):
    return os.path.join(fence_dir(root), token_slot(session_id) + TOKEN_SUFFIX)


def label_for_token(token):
    """The PUBLIC session label: a one-way hash of the secret token.

    This is the whole identity design in one line. The label is safe to print
    into STATE.md, safe to paste into a thread file, safe to read out of any
    generated view, because deriving the token from it would require
    inverting SHA-256. And an ownership check compares the label ON THE
    RECORD against the label DERIVED FROM THE TOKEN THIS PROCESS COULD OPEN,
    so copying a label out of STATE.md and presenting it as your own session
    id gains nothing: the hook never takes your word for which label is
    yours, it computes it."""
    return LABEL_PREFIX + _sha256_hex(_LABEL_DOMAIN + token)[:_LABEL_HEX]


def _valid_token_text(t):
    if not isinstance(t, str):
        return False
    t = t.strip()
    if len(t) != _TOKEN_BYTES * 2:
        return False
    try:
        int(t, 16)
    except ValueError:
        return False
    return True


def ensure_token(root, session_id):
    """Return this session's secret token, creating it on first use.

    Created with O_CREAT|O_EXCL at mode 0600 inside a 0700 directory, so two
    processes that somehow share a session id cannot both write it and the
    loser simply reads the winner's file. The 0600 is a real guarantee on
    POSIX and a courtesy on Windows, where os.chmod can only toggle the
    read-only bit and the actual protection is the ACL the directory
    inherits; that difference is stated in docs/HOOKS.md rather than glossed,
    because a permission claim the platform will not keep is worse than none.

    Raises on any filesystem failure. Every caller treats that as fail-open.
    """
    bs = _load_store_module()
    d = fence_dir(root)
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
        if bs is not None:
            bs._chmod_best_effort(d, 0o700)
        else:
            try:
                os.chmod(d, 0o700)
            except OSError:  # sbe: allow-silent best-effort permission tightening, same stance as bs._chmod_best_effort it stands in for
                pass
    p = token_path(root, session_id)
    # Same containment rule bm_store applies to the store file itself: refuse
    # to follow a symlink that would put a secret outside the project root,
    # or let an attacker point the token file at a file they can read.
    if bs is not None:
        bs._refuse_if_symlink_escape(d)
        bs._refuse_if_symlink_escape(p)
    elif os.path.lexists(p) and os.path.realpath(p) != p:
        raise OSError("token path %s is a symlink resolving to %s"
                      % (p, os.path.realpath(p)))
    if os.path.exists(p):
        with io.open(p, "r", encoding="utf-8") as f:
            existing = f.read()
        if _valid_token_text(existing):
            return existing.strip()
        raise ValueError("token file %s does not hold a %d-character hex token"
                         % (p, _TOKEN_BYTES * 2))
    token = secrets.token_hex(_TOKEN_BYTES)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(p, flags, 0o600)
    except FileExistsError:
        # Lost the create race. The winner's token is the session's token.
        with io.open(p, "r", encoding="utf-8") as f:
            existing = f.read()
        if _valid_token_text(existing):
            return existing.strip()
        raise ValueError("token file %s does not hold a %d-character hex token"
                         % (p, _TOKEN_BYTES * 2))
    try:
        with io.open(fd, "w", encoding="utf-8", closefd=True) as f:
            f.write(token + "\n")
    except Exception:
        try:
            os.close(fd)
        except Exception:  # sbe: allow-silent best-effort fd close on the failure path, io.open's own close on exit may already have closed it; original exception re-raised regardless
            pass
        raise
    if bs is not None:
        bs._chmod_best_effort(p, 0o600)
    return token


def session_label(root, session_id):
    """The public label for a session, materializing its token if needed."""
    return label_for_token(ensure_token(root, session_id))


# ---------------------------------------------------------------------------
# Path canonicalization.
# ---------------------------------------------------------------------------

def canonical_target(root, raw, cwd=None):
    """A tool's target path as a root-relative POSIX string, symlinks
    resolved, or None when it falls outside the project root.

    Deliberately mirrors bm_store._resolve_against_root rather than calling
    bm_store.canonicalize_path: that function treats '*', '?' and '[' as
    wildcard segments, which is correct for a DECLARED CLAIM and wrong for a
    CONCRETE FILE a tool is about to write. os.path.realpath resolves
    symlinks in whatever prefix of the path already exists and leaves a
    nonexistent trailing component literal, so this works identically for
    Edit on an existing file and Write creating a new one.

    None means "not this project's business" (a different drive, a path
    above the root, an unusable string) and every caller treats that as
    allow: BrotherMode fences a project, not the filesystem."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    root_real = os.path.realpath(root)
    base_dir = cwd if cwd else root_real
    try:
        if os.path.isabs(raw):
            base = raw
        else:
            base = os.path.join(base_dir, raw)
        abs_real = os.path.realpath(base)
        rel = os.path.relpath(abs_real, root_real)
    except (ValueError, OSError):
        # Windows raises ValueError from relpath across drives, which is
        # definitionally outside the root.
        return None
    rel_posix = rel.replace(os.sep, "/")
    if rel_posix == ".." or rel_posix.startswith("../"):
        return None
    rel_posix = posixpath.normpath(rel_posix)
    # H1: a write reaching a file through a linked worktree checkout is a
    # write to the same logical file, so the fence compares its
    # worktree-relative spelling, the one a claim on the plain path
    # stores. Store module unimportable is the documented fail-open class
    # and leaves the historical spelling untouched.
    bs_mod = _load_store_module()
    if bs_mod is not None and hasattr(bs_mod, "strip_worktree_segments"):
        rel_posix = bs_mod.strip_worktree_segments(root_real, rel_posix)
    return rel_posix


def extract_targets(tool_input):
    """Every path a write tool's input names, in input order, de-duplicated.

    Walks nested lists of dicts too, so a MultiEdit-shaped payload whose
    per-edit entries carry their own file_path is not silently reduced to the
    top-level path. Bounded depth: a hook that recursed without limit on a
    hostile payload would hang in front of every edit."""
    out = []
    seen = set()

    def visit(node, depth):
        if depth > 6:
            return
        if isinstance(node, dict):
            for k in PATH_KEYS:
                v = node.get(k)
                if isinstance(v, str) and v.strip() and v not in seen:
                    seen.add(v)
                    out.append(v)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    visit(v, depth + 1)
        elif isinstance(node, list):
            for v in node[:64]:
                visit(v, depth + 1)

    visit(tool_input, 0)
    return out


def extract_patch_targets(body):
    """Every path an apply_patch envelope BODY names, in input order,
    de-duplicated, plus whether an envelope marker opened a line at all.
    Returns (targets, envelope_seen).

    `body` is the text apply_patch actually receives (a heredoc body, tabs
    already stripped for the "<<-" form by the caller). Directives are
    matched at COLUMN 0 and nowhere else: a line beginning with a space, "+"
    or "-" is hunk body, i.e. content of the file being written, and is
    skipped. This is the FD-1 fix; the old code stripped leading whitespace
    first and harvested a context line as a phantom directive. Directives are
    honored wherever they open a line, inside a well formed Begin/End pair or
    not, because a malformed patch that still names its files must be checked
    on what it names: the parseable part is authoritative. The caller treats
    an envelope with NO readable directive as a write whose targets are
    unknowable and refuses to certify it."""
    targets = []
    seen = set()
    envelope_seen = False
    for line in body.split("\n"):
        if line.startswith(_HUNK_BODY_OPENERS):
            # Hunk body (context / added / removed). Never a directive, even
            # when it quotes the grammar verbatim. This one guard is the FD-1
            # fix, stated once.
            continue
        if line.startswith(APPLY_PATCH_BEGIN):
            envelope_seen = True
            continue
        for prefix in APPLY_PATCH_DIRECTIVES:
            if line.startswith(prefix):
                path = line[len(prefix):].strip()
                if path and path not in seen:
                    seen.add(path)
                    targets.append(path)
                break
    return targets, envelope_seen


def _is_shell_identifier(s):
    """True for a bash name that could precede '=' in an environment
    assignment (FOO=bar), so the command-word finder can skip it."""
    if not s or (s[0].isdigit()):
        return False
    return all(c.isalnum() or c == "_" for c in s)


def _command_word(prefix):
    """The program word of the simple command that a heredoc on this line
    belongs to. `prefix` is the text on the operator line BEFORE the '<<'.

    Isolates the segment after the last shell separator (';', '&', '|', which
    together cover ';', '&', '|', '&&', '||' and pipelines), then returns the
    basename of the first token that is not a leading NAME=value environment
    assignment and not the shell's `command` builtin. So 'apply_patch ' gives
    apply_patch, 'FOO=bar apply_patch ' skips the assignment and gives
    apply_patch, 'cat > doc ' gives cat, 'true | apply_patch ' gives
    apply_patch, and 'command apply_patch ' (with or without option tokens
    such as -p) gives apply_patch, because `command` runs the word after it:
    H2 first shape, cross-family review 2026-08-08. `command -v X` names X
    without running it; treating it as X errs toward a refusal, never toward
    an unread write. An empty or unparseable prefix gives '', which matches
    no gated command and therefore allows."""
    cut = 0
    for i, ch in enumerate(prefix):
        if ch in ";&|":
            cut = i + 1
    behind_command_builtin = False
    for tok in prefix[cut:].split():
        eq = tok.find("=")
        if eq > 0 and _is_shell_identifier(tok[:eq]):
            continue
        if tok == "command":
            behind_command_builtin = True
            continue
        if behind_command_builtin and tok.startswith("-"):
            continue
        return os.path.basename(tok)
    return ""


def _find_heredoc_ops(line):
    """Every heredoc operator on one physical line, in order, as
    (command_word, strips_tabs, delimiter).

    Recognizes '<<DELIM', '<< DELIM', '<<-DELIM', "<<'DELIM'", '<<"DELIM"'
    and the tab-stripping '<<-' variants. Skips the '<<<' here-string
    operator, which is not a heredoc. Hand-written rather than via re, to keep
    this file's imports unchanged; the grammar is small and fixed."""
    ops = []
    n = len(line)
    idx = 0
    while True:
        pos = line.find("<<", idx)
        if pos == -1:
            break
        # '<<<' is a here-string, not a heredoc: skip the whole run of '<'.
        if pos > 0 and line[pos - 1] == "<":
            idx = pos + 1
            continue
        after = pos + 2
        if after < n and line[after] == "<":
            idx = after + 1
            continue
        j = after
        strips_tabs = False
        if j < n and line[j] == "-":
            strips_tabs = True
            j += 1
        while j < n and line[j] in " \t":
            j += 1
        quote = ""
        if j < n and line[j] in "'\"":
            quote = line[j]
            j += 1
        start = j
        if quote:
            # Quoted delimiter: any character up to the matching quote is
            # legal, including hyphens ('PATCH-END' was H2's second bypass
            # shape, cross-family review 2026-08-08: the old alnum-only scan
            # stopped at the hyphen, read the quote as unbalanced, and
            # dropped the operator, so the body was never read).
            while j < n and line[j] != quote:
                j += 1
        else:
            # Unquoted: the word characters a real delimiter uses. Hyphen,
            # dot and plus are legal in shell words and appear in the wild.
            while j < n and (line[j].isalnum() or line[j] in "_-.+"):
                j += 1
        delim = line[start:j]
        if quote:
            if j < n and line[j] == quote:
                j += 1
            else:
                # Unbalanced quote: not a heredoc operator we can trust.
                idx = after
                continue
        if delim:
            ops.append((_command_word(line[:pos]), strips_tabs, delim))
        idx = j if j > pos else pos + 2
    return ops


def apply_patch_bodies(command):
    """Every heredoc body in `command` that is fed to an apply_patch command,
    in order, as a list of strings.

    This is the heredoc-aware half of the FD-2 / FD-3 fix: the envelope is a
    write only when apply_patch is the program receiving it. A heredoc owned
    by cat, tee, git or anything else, even one whose body quotes the whole
    grammar, is not an apply_patch write and never appears here. Bodies are
    returned with leading tabs stripped for the '<<-' form, exactly as the
    shell would hand them to apply_patch, so a tab-indented directive under
    '<<-' is seen at column 0 while a space-indented line under a plain '<<'
    stays hunk body."""
    lines = command.split("\n")
    n = len(lines)
    bodies = []
    i = 0
    while i < n:
        ops = _find_heredoc_ops(lines[i])
        i += 1
        for cmdword, strips_tabs, delim in ops:
            collected = []
            while i < n:
                raw = lines[i]
                probe = raw.lstrip("\t") if strips_tabs else raw
                if probe == delim:
                    i += 1
                    break
                collected.append(probe)
                i += 1
            if os.path.basename(cmdword) == APPLY_PATCH_CMD:
                bodies.append("\n".join(collected))
    return bodies


# ---------------------------------------------------------------------------
# Store lookup.
# ---------------------------------------------------------------------------

class _FailOpen(Exception):
    """Raised anywhere a decision cannot be made SAFELY. Caught at the top of
    decide(). In the default advisory mode it produces an allow plus a stderr
    line; in enforced mode (see enforced_mode below) it produces a DENY. A
    named exception rather than a returned sentinel so a new code path cannot
    forget to check the sentinel and accidentally deny.

    `code` selects the enforced-mode wording from _FAIL_REASONS and defaults
    to 'unknown', which is deliberately a DENYING code: a raise added later
    that forgets to pass one refuses rather than quietly reopening the hole
    this class was audited for."""

    def __init__(self, message, code="unknown"):
        Exception.__init__(self, message)
        self.code = code if code in _FAIL_REASONS else "unknown"


MODE_ADVISORY = "advisory"
MODE_ENFORCED = "enforced"
ENFORCED = MODE_ENFORCED
ADVISORY = MODE_ADVISORY

#: Codes that ALLOW even under enforcement. Exactly one, and it is the case
#: where there is nothing to enforce: no BROTHERMODE_ROOT, no .brothermode
#: ancestor, no .git ancestor, so this is not a BrotherMode project at all.
#: Denying here would stop editing in every unrelated directory on the machine
#: the moment somebody exported the variable, which is the brick this file
#: exists to avoid.
_ALLOW_EVEN_WHEN_ENFORCED = frozenset(("no-root",))

#: Enforced-mode deny copy, keyed by the code carried on _FailOpen. Every
#: value is a LITERAL, and that is a security property rather than a style
#: choice. The stderr note is read by the operator and may name paths; this
#: reason is read by the model and lands in a transcript, and at the moment it
#: is produced NOTHING has been verified, so nothing verified justifies
#: quoting a path, a record name, a claim label or any payload content. The
#: operator gets the detail on stderr; the model gets a category and a remedy.
_FAIL_REASONS = {
    "bad-payload": (
        "the hook payload did not have the shape the PreToolUse contract "
        "documents, so the target of this write could not be established",
        "check the hook command in settings.json against docs/HOOKS.md"),
    "no-session-id": (
        "the payload carried no session id, so this session has no verifiable "
        "identity and no ownership could be established",
        "check the hook wiring in settings.json against docs/HOOKS.md"),
    "no-target-path": (
        "a write tool arrived with no path key this hook recognizes, so there "
        "was nothing to check against the fence",
        "use Edit or Write with file_path, or declare the path through "
        "scripts/bm_shell.py"),
    "no-store": (
        "this project has no BrotherMode store, so there is no fence to check "
        "against",
        "run `python3 tools/bm_store.py init` in the project root"),
    "store-unreadable": (
        "the BrotherMode store could not be opened read-only: it is missing, "
        "empty, busy or corrupt",
        "run `python3 scripts/doctor.py`, then repair or re-init the store"),
    "store-unqueryable": (
        "the BrotherMode store opened but its claims could not be read",
        "run `python3 scripts/doctor.py`, then repair or re-init the store"),
    "no-active-claims": (
        "the BrotherMode store holds no active claims, so no fence exists, "
        "and enforced mode requires a claim before an edit",
        "claim the paths first with `python3 tools/bm_store.py claim`"),
    "no-identity": (
        "this session's fence token could not be read or created, so its "
        "ownership label could not be derived",
        "check that the .brothermode/fence directory is writable, then run "
        "`python3 tools/bm_fence_hook.py whoami`"),
    "store-unimportable": (
        "the fence could not load bm_store.py, so no fence check ran at all",
        "check that the hook command in settings.json points at a complete "
        "BrotherMode tools directory"),
    "internal-error": (
        "the fence hit an unexpected internal error and could not reach a "
        "decision",
        "read the bm_fence_hook line on stderr for the error type, and file "
        "it as a defect"),
    "no-root": (
        "no BrotherMode project root was found, so there is no fence here",
        "run `python3 tools/bm_store.py init` if this should be a project"),
    "battery-unreadable": (
        "a test gate may be running against this checkout and its state "
        "could not be established, so the write could invalidate a live "
        "baseline run",
        "read the bm_fence_hook line on stderr for the lock path, check the "
        "lock by hand, then retry"),
    "unknown": (
        "the fence could not complete its check",
        "read the bm_fence_hook line on stderr for the reason"),
}


_GATE = None
_GATE_ERROR = None


def _load_gate_module():
    """Import tools/test_all.py beside this file, or return None and record
    why. Same construction as _load_store_module, same reason. The gate
    lock's format has exactly one owner (test_all.py, which writes it), so
    this hook never parses the lock itself: lock_path and lock_state are
    asked, never re-derived, and an unloadable owner means the battery fence
    is reported as unchecked rather than guessed at."""
    global _GATE, _GATE_ERROR
    if _GATE is not None or _GATE_ERROR is not None:
        return _GATE
    try:
        import importlib.util
        path = os.path.join(HERE, "test_all.py")
        spec = importlib.util.spec_from_file_location("bm_test_all", path)
        if spec is None or spec.loader is None:
            _GATE_ERROR = "no import spec for %s" % path
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault("bm_test_all", mod)
        _GATE = mod
        return _GATE
    except Exception as e:
        _GATE_ERROR = "%s: %s" % (type(e).__name__, e)
        return None


def _is_tracked(root, rel):
    """True, False, or None when git could not answer (no repository, no git
    on PATH, a timeout). None is NO-DATA and the caller reports it; it is
    never the same answer as False."""
    try:
        r = subprocess.run(
            ["git", "-C", root, "ls-files", "--error-unmatch", "--", rel],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30,
            env={k: v for k, v in os.environ.items()
                 if not k.startswith("GIT_")})
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 0:
        return True
    # Exit 1 is git's own answer for "not tracked"; anything else (128 for
    # not-a-repository, and so on) means the question could not be asked.
    if r.returncode == 1:
        return False
    return None


def _battery_decision(root, raw_targets, cwd, notes):
    """A deny payload when a LIVE gate lock covers a git-TRACKED target,
    else None, with every unestablishable state reported into `notes`.

    Battery fence (incident 2026-08-17): one session edited a tracked file
    nineteen minutes into another session's `tools/test_all.py` baseline
    run, invalidating it; nothing had announced the run. The gate lock
    test_all.py has always taken now doubles as that announcement, and this
    hook refuses tracked-file writes while it is live, for EVERY session
    including the one that started the gate, because a same-session edit
    invalidates the baseline identically (the recorded failure class of
    measuring a tree you are still editing).

    Handled inline rather than through _FailOpen: a broken battery check
    must degrade to a loud note while the FENCE check still runs; raising
    would skip the fence too. In enforced mode an unestablishable battery
    state refuses, C-01's direction, through the battery-unreadable copy."""
    switch = os.environ.get("BM_FENCE_BATTERY", "").strip().lower()
    if switch in ("off", "0"):
        notes.append(
            "bm_fence_hook: BM_FENCE_BATTERY=%s, so the battery fence was "
            "NOT checked and this write may invalidate a running gate. "
            "Unset it to restore the check." % switch)
        return None

    def nodata(detail):
        if enforced_mode():
            summary, remedy = _FAIL_REASONS["battery-unreadable"]
            notes.append(
                "bm_fence_hook: FAILING CLOSED, the write is refused "
                "because the battery state could not be checked. Reason: %s"
                % detail)
            return deny_payload(
                "BrotherMode is in enforced mode and refused this write "
                "because %s. To fix it, %s. To go back to warning only, set "
                "BM_FENCE_MODE=advisory." % (summary, remedy))
        notes.append(
            "bm_fence_hook: the battery fence was NOT checked (%s); the "
            "write is allowed. A running gate, if any, may be invalidated "
            "by it." % detail)
        return None

    gate = _load_gate_module()
    if gate is None:
        return nodata("test_all.py beside this hook could not be loaded "
                      "(%s)" % _GATE_ERROR)
    try:
        path = gate.lock_path(os.path.join(root, "tools"))
        if not os.path.exists(path):
            return None
        state, holder = gate.lock_state(path)
    except Exception as e:
        return nodata("%s: %s" % (type(e).__name__, e))
    if state == "absent":
        return None
    if state == "stale":
        notes.append(
            "bm_fence_hook: a gate lock exists at %s but its holder is "
            "provably gone (%s). It is ignored; the next gate run cleans "
            "it, or delete it by hand." % (path, holder))
        return None
    if state == "unreadable":
        return nodata("the gate lock at %s exists but could not be read; "
                      "an unreadable lock is reported, never silently "
                      "ignored" % path)
    for raw in raw_targets:
        rel = canonical_target(root, raw, cwd)
        if rel is None:
            continue
        tracked = _is_tracked(root, rel)
        if tracked is None:
            refused = nodata("the gate is running but git could not say "
                             "whether %s is tracked" % rel)
            if refused is not None:
                return refused
            continue
        if not tracked:
            continue
        return deny_payload(
            "BrotherMode battery fence: the test gate is running against "
            "this checkout right now (%s, lock %s), and %s is tracked by "
            "git here, so writing it would invalidate the run's baseline. "
            "That is the recorded failure class of measuring a tree you "
            "are still editing, and it holds for EVERY session, including "
            "the one that started the gate.\n"
            "What proceeds, and nothing else does:\n"
            "  1. Wait for the gate to finish; the lock disappears when it "
            "does.\n"
            "  2. Write an untracked scratch path instead; only tracked "
            "files are fenced.\n"
            "  3. A crashed run reads as stale and is ignored "
            "automatically; a LIVE lock means its pid answered a liveness "
            "probe just now.\n"
            "  4. A deliberate override is BM_FENCE_BATTERY=off, announced "
            "on stderr on every write while it is set."
            % (holder, path, rel))
    return None


def fence_mode(env=None):
    """Return (mode, warning_or_None) for BM_FENCE_MODE.

    Unset, empty or 'advisory' is the DEFAULT and is today's behaviour
    exactly. 'enforced' fails closed. Anything else runs ADVISORY and returns
    a warning, which is the deliberate middle: denying on a typo would brick
    editing over a missing letter, and silently accepting one would let
    somebody who asked for enforcement quietly not get it. The warning prints
    on every gated call, so it cannot be scrolled past once."""
    env = os.environ if env is None else env
    raw = (env.get("BM_FENCE_MODE") or "").strip().lower()
    if raw in ("", MODE_ADVISORY):
        return MODE_ADVISORY, None
    if raw == MODE_ENFORCED:
        return MODE_ENFORCED, None
    return MODE_ADVISORY, (
        "bm_fence_hook: BM_FENCE_MODE=%r is not a recognized mode, so the "
        "fence is running ADVISORY (fail open). Set it to 'enforced' or "
        "'advisory'." % raw)


def enforced_mode(env=None):
    """True when the operator has opted this machine into fail-closed.

    C-01 (2026-08-03). Until now every failure path in this hook produced an
    allow, and an adversarial probe confirmed it: nine separate conditions
    (store missing, store corrupt, store zero bytes, store present with ZERO
    active claims, five shapes of malformed payload, an unrecognized path key,
    an internal exception mid-decision, bm_store.py unimportable, an
    underivable session identity) each allowed a write across another
    session's active fence, exit 0. BM_FENCE_STRICT did not help, because it
    is read AFTER every one of those raises and is a complete no-op on a
    zero-claims store, which is the state a fresh project sits in.

    Fail-open remains the DEFAULT and is unchanged, deliberately: a hook that
    starts refusing edits on a machine whose owner never asked for it is a
    worse failure than an unenforced fence, and that judgement is recorded in
    this file's own history. Enforcement is therefore opt-in, one variable,
    and it is the only thing that changes behaviour here.

    BM_FENCE_MODE is read rather than reusing BM_FENCE_STRICT because the two
    mean different things and conflating them would silently widen what
    existing users already set: STRICT tightens which PATHS are covered when
    the fence is working, while MODE decides what happens when it CANNOT work.
    """
    if env is None:
        env = os.environ
    return env.get("BM_FENCE_MODE", "").strip().lower() == ENFORCED


def active_claims(root):
    """Every (path, record) pair an ACTIVE record currently fences.

    Read-only by construction: ReadOnlyStore has no path to the code that
    quarantines or moves anything, which matters because this runs in front
    of every edit and a transient disk hiccup must never let a hook relocate
    the founder's database."""
    bs = _load_store_module()
    if bs is None:
        raise _FailOpen("bm_store.py could not be imported (%s)" % _BS_ERROR, "store-unimportable")
    path = bs.store_path(root)
    if not os.path.isfile(path):
        raise _FailOpen("no store at %s (run `python3 tools/bm_store.py init`)"
                        % path, "no-store")
    try:
        store = bs.ReadOnlyStore(root)
    except Exception as e:
        raise _FailOpen("store at %s could not be opened read-only (%s: %s)"
                        % (path, type(e).__name__, e), "store-unreadable")
    try:
        rows = bs._exec(store,
            "SELECT c.path AS path, r.name AS name, "
            "r.lifecycle_uuid AS lifecycle_uuid, r.version AS version, "
            "r.session_id AS session_id "
            "FROM claims c JOIN records r ON r.lifecycle_uuid = c.lifecycle_uuid "
            "WHERE r.state='active'").fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        raise _FailOpen("store at %s could not be read (%s: %s)"
                        % (path, type(e).__name__, e), "store-unqueryable")
    finally:
        store.close()


def _safe_display(text):
    """Founder-typed text on its way into a deny message, redacted the same
    way bm_store._raise_overlap redacts an overlap refusal. A record name and
    a claim path are free text and can hold a secret shape; the deny reason
    is read by the model and lands in a transcript. Best effort on purpose:
    an unavailable redactor must degrade the MESSAGE, never the REFUSAL."""
    bs = _load_store_module()
    if bs is None:
        return "(redacted: unavailable)"
    try:
        return bs._sanitize_for_display(bs.redact_text(text))
    except Exception:
        return "(redacted: unavailable)"


def _takeover_command(row, my_label):
    """The exact command that moves the fence to this session. `adopt` with
    --adopt-from-live-session is bm_store's own documented door for taking a
    record that is active under a different live session; naming anything
    else here would send the reader to a command that refuses."""
    return ("python3 tools/bm_store.py adopt %s --version %s --session %s "
            "--adopt-from-live-session"
            % (row["lifecycle_uuid"], row["version"], my_label))


# ---------------------------------------------------------------------------
# The decision.
# ---------------------------------------------------------------------------

def deny_payload(reason):
    """The deny object, shaped exactly as the PreToolUse contract requires.
    Read from https://code.claude.com/docs/en/hooks on 2026-07-27:

      {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                              "permissionDecision": "deny",
                              "permissionDecisionReason": "..."}}

    Emitted on stdout with exit code 0. Exit 2 would also block, feeding
    stderr back as the reason, but it is the wrong instrument here: exit 2
    means "the hook itself failed", and this hook reserves nonzero-shaped
    failure semantics for nothing at all, because every failure it has is a
    fail-open."""
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def decide(payload):
    """Return (decision_object_or_None, stderr_lines).

    None means "no decision": Claude Code applies the normal permission flow.
    That is both the ALLOW answer and the FAIL-OPEN answer, deliberately the
    same value, so no failure path can produce a deny by accident."""
    notes = []
    try:
        if not isinstance(payload, dict):
            raise _FailOpen("hook payload was not a JSON object", "bad-payload")
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or (
                tool_name not in WRITE_TOOLS and tool_name != "Bash"):
            # Not a file-writing tool. Silent, not loud: this is the common
            # case, and a stderr line per Read or Grep would be noise.
            return None, []
        tool_input = payload.get("tool_input")
        raw_targets = None
        if tool_name == "Bash":
            # Bash is gated for exactly one shape: an apply_patch heredoc,
            # which is how every file write arrives under the Codex CLI. The
            # token test is the fast path out, taken BEFORE identity and store
            # work: this branch runs in front of EVERY shell command once the
            # matcher widens, and a plain command must leave as fast and
            # silently as it did when Bash was not matched at all. A command
            # that does not even mention apply_patch cannot be one.
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            if not isinstance(command, str) or APPLY_PATCH_CMD not in command:
                return None, []
            # Only heredocs OWNED by apply_patch count. A cat/tee/git heredoc
            # that merely quotes the grammar, or the word apply_patch sitting
            # in a comment or a commit message, yields no bodies here and is
            # allowed. This is the FD-2 / FD-3 fix.
            bodies = apply_patch_bodies(command)
            if not bodies:
                return None, []
            raw_targets = []
            envelope_seen = False
            for body in bodies:
                found, seen = extract_patch_targets(body)
                envelope_seen = envelope_seen or seen
                for path in found:
                    if path not in raw_targets:
                        raw_targets.append(path)
            if not raw_targets:
                if envelope_seen:
                    # An apply_patch heredoc with an envelope marker but no
                    # readable file directive. DENIED, not failed open, and
                    # unconditionally, before any store or identity work: the
                    # write is real, its targets are unknowable, and a fence
                    # that shrugs at an unparseable write is the silent no-op
                    # this matcher exists to end.
                    notes.append(
                        "bm_fence_hook: DENY, an apply_patch envelope was "
                        "present but no file directive could be read from it")
                    return deny_payload(_UNREADABLE_PATCH_REASON), notes
                # apply_patch received a heredoc, but its body carried no
                # envelope at all (for example a here-doc of plain data): not
                # a patch we can read as a write, and not one to refuse.
                return None, []
        if not isinstance(tool_input, dict):
            raise _FailOpen("tool_input for %s was not a JSON object" % tool_name, "bad-payload")
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise _FailOpen("hook payload carried no session_id, so this "
                            "session has no verifiable identity", "no-session-id")
        session_id = session_id.strip()

        if raw_targets is None:
            raw_targets = extract_targets(tool_input)
            if not raw_targets:
                raise _FailOpen("no target path found in tool_input for %s "
                                "(keys: %s)" % (tool_name, ", ".join(sorted(tool_input))), "no-target-path")

        bs = _load_store_module()
        if bs is None:
            raise _FailOpen("bm_store.py could not be imported (%s)" % _BS_ERROR, "store-unimportable")

        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            cwd = None
        root, _source = bs.resolve_root(cwd or None,
                                        env_must_contain_start=True)
        if root is None:
            raise _FailOpen("no BrotherMode project root found from %s"
                            % (cwd or os.getcwd()), "no-root")
        # H4: fold case where this project's filesystem folds it, not
        # where sys.platform guesses it does.
        bs.note_fs_case(root)

        # Battery fence, BEFORE the claims load on purpose: a store with
        # zero claims raises no-active-claims and fails open below, and a
        # running gate deserves its refusal even in a project with no
        # fences declared at all.
        battery = _battery_decision(root, raw_targets, cwd, notes)
        if battery is not None:
            return battery, notes

        rows = active_claims(root)
        if not rows:
            raise _FailOpen("the store at %s holds no active claims, so there "
                            "is no fence to enforce" % bs.store_path(root), "no-active-claims")

        try:
            my_label = session_label(root, session_id)
        except Exception as e:
            raise _FailOpen("this session's fence token could not be read or "
                            "created (%s: %s)" % (type(e).__name__, e), "no-identity")

        strict = os.environ.get("BM_FENCE_STRICT", "").strip() not in ("", "0")

        for raw in raw_targets:
            rel = canonical_target(root, raw, cwd)
            if rel is None:
                # Outside the project root. BrotherMode fences a project,
                # not the machine.
                continue
            covering = [r for r in rows if bs.paths_overlap(rel, r["path"])]
            foreign = [r for r in covering if r["session_id"] != my_label]
            if foreign:
                r = foreign[0]
                reason = (
                    "BrotherMode fence: %s is inside the fence of the active "
                    "record %s (lifecycle %s, version %s), which is owned by "
                    "session %s. This session is %s, so it is not the writer "
                    "for that path. Claimed as: %s.\n"
                    "One writer per file is structural, not advisory: do not "
                    "edit across the fence. Report the needed change to the "
                    "owner instead. To take the fence over deliberately, run:\n"
                    "  %s\n"
                    "then re-claim the paths you need with `bm_store.py claim "
                    "... --session %s`."
                    % (rel, _safe_display(r["name"]), r["lifecycle_uuid"],
                       r["version"], r["session_id"] or "(none)", my_label,
                       _safe_display(r["path"]),
                       _takeover_command(r, my_label), my_label))
                return deny_payload(reason), notes
            if strict and not covering:
                reason = (
                    "BrotherMode fence (strict mode): %s is not inside any "
                    "active record's fence, and BM_FENCE_STRICT is set, so "
                    "claim-before-edit is being enforced. Claim it first:\n"
                    "  python3 tools/bm_store.py claim <name> --lifetime "
                    "ephemeral --objective '...' --files %s --session %s"
                    % (rel, rel, my_label))
                return deny_payload(reason), notes
        return None, notes
    except _FailOpen as e:
        code = getattr(e, "code", "unknown")
        if enforced_mode() and code not in _ALLOW_EVEN_WHEN_ENFORCED:
            summary, remedy = _FAIL_REASONS.get(code, _FAIL_REASONS["unknown"])
            notes.append(
                "bm_fence_hook: FAILING CLOSED, the write is refused because "
                "the fence could not be checked. Reason: %s" % e)
            # Literal copy only. The stderr line above carries the detail for
            # the operator, including paths; this reason is read by the model
            # and lands in a transcript, and nothing has been verified at this
            # point, so it names no path, record, label or payload content.
            return deny_payload(
                "BrotherMode is in enforced mode and refused this write "
                "because %s. To fix it, %s. To go back to warning only, set "
                "BM_FENCE_MODE=advisory." % (summary, remedy)), notes
        notes.append(
            "bm_fence_hook: FAILING OPEN, the write is allowed and the fence "
            "was NOT checked. Reason: %s" % e)
        return None, notes
    except Exception as e:
        # The blanket catch is the point, not laziness: an unforeseen bug in
        # this file must never become a refusal in front of the founder's
        # editing. Type and message, no traceback, so the stderr line stays
        # one readable sentence.
        #
        # In enforced mode that judgement inverts (C-01): somebody who asked
        # for fail-closed has said they would rather be stopped by a bug in
        # this hook than have it wave a write through unchecked. The message
        # still carries no traceback and no file content.
        if enforced_mode():
            summary, remedy = _FAIL_REASONS["internal-error"]
            notes.append(
                "bm_fence_hook: FAILING CLOSED after an unexpected error, the "
                "write is refused. Reason: %s: %s" % (type(e).__name__, e))
            return deny_payload(
                "BrotherMode is in enforced mode and refused this write "
                "because %s. To fix it, %s. To go back to warning only, set "
                "BM_FENCE_MODE=advisory." % (summary, remedy)), notes
        notes.append(
            "bm_fence_hook: FAILING OPEN after an unexpected error, the write "
            "is allowed and the fence was NOT checked. Reason: %s: %s"
            % (type(e).__name__, e))
        return None, notes


# ---------------------------------------------------------------------------
# Entry points.
# ---------------------------------------------------------------------------

def _read_stdin_json():
    try:
        raw = sys.stdin.read()
    except Exception as e:
        return None, "stdin could not be read (%s: %s)" % (type(e).__name__, e)
    if not raw or not raw.strip():
        return None, "stdin was empty"
    try:
        return json.loads(raw), None
    except Exception as e:
        return None, "stdin was not valid JSON (%s: %s)" % (type(e).__name__, e)


def cmd_hook(argv):
    payload, err = _read_stdin_json()
    if err is not None:
        # FIXED 2026-08-10. This branch used to fail OPEN unconditionally, and
        # that made C-01's closure claim false on its own terms. That entry
        # says enforced mode denies all nine failure conditions "because every
        # one of them funnels through _FailOpen or the blanket catch", and the
        # register names a malformed payload as one of the nine. It does not
        # funnel through either: both live inside decide(), and an unparseable
        # payload returns from HERE without ever calling decide(). So the one
        # condition an attacker most directly controls, the bytes on stdin, was
        # the one condition enforced mode could not refuse.
        #
        # Found by following an independent Codex audit finding about the
        # unattended preflight, and reproduced before the fix:
        #   printf 'not json' | BM_FENCE_MODE=enforced python3 tools/bm_fence_hook.py
        # printed FAILING OPEN in both modes.
        #
        # The deny copy is the same literal-only shape decide() uses, and for
        # the same reason: nothing has been verified at this point, so the
        # model-visible reason names no path, record, label or payload content.
        # The operator gets the detail on stderr.
        if enforced_mode():
            summary, remedy = _FAIL_REASONS["bad-payload"]
            _warn("bm_fence_hook: FAILING CLOSED, the write is refused "
                  "because the fence could not be checked. Reason: %s" % err)
            _out(json.dumps(deny_payload(
                "BrotherMode is in enforced mode and refused this write "
                "because %s. To fix it, %s. To go back to warning only, set "
                "BM_FENCE_MODE=advisory." % (summary, remedy))))
            return 0
        _warn("bm_fence_hook: FAILING OPEN, the write is allowed and the "
              "fence was NOT checked. Reason: %s" % err)
        return 0
    decision, notes = decide(payload)
    for n in notes:
        _warn(n)
    if decision is not None:
        _out(json.dumps(decision))
    return 0


def cmd_session_label(argv):
    """Print this session's PUBLIC fence label, for use as
    `bm_store.py claim ... --session "$(bm_fence_hook.py session-label ...)"`.

    The session id comes from --session-id, then BM_FENCE_SESSION_ID, then a
    JSON payload on stdin (so this can also be wired as a SessionStart hook,
    which receives session_id in the same field). Never invents one: a made
    up session id would mint a token nobody else could reproduce and quietly
    lock the session out of its own records."""
    session_id = None
    for i, a in enumerate(argv):
        if a == "--session-id" and i + 1 < len(argv):
            session_id = argv[i + 1]
        elif a.startswith("--session-id="):
            session_id = a.split("=", 1)[1]
    if not session_id:
        session_id = os.environ.get("BM_FENCE_SESSION_ID", "")
    if not session_id and not sys.stdin.isatty():
        payload, _err = _read_stdin_json()
        if isinstance(payload, dict):
            session_id = payload.get("session_id") or ""
    session_id = (session_id or "").strip()
    if not session_id:
        _warn("bm_fence_hook: no session id. Pass --session-id X, set "
              "BM_FENCE_SESSION_ID, or feed a hook payload on stdin.")
        return 2
    bs = _load_store_module()
    if bs is None:
        _warn("bm_fence_hook: bm_store.py could not be imported (%s)" % _BS_ERROR)
        return 2
    root, _source = bs.resolve_root()
    if root is None:
        _warn("bm_fence_hook: no BrotherMode project root found from %s"
              % os.getcwd())
        return 2
    try:
        _out(session_label(root, session_id) + "\n")
    except Exception as e:
        _warn("bm_fence_hook: could not read or create this session's token "
              "(%s: %s)" % (type(e).__name__, e))
        return 2
    return 0


def cmd_whoami(argv):
    """Diagnostics on stderr, label on stdout. Prints where the token lives
    and never prints the token itself."""
    bs = _load_store_module()
    if bs is None:
        _warn("bm_store.py unimportable: %s" % _BS_ERROR)
        return 2
    root, source = bs.resolve_root()
    if root is None:
        _warn("no BrotherMode project root found from %s" % os.getcwd())
        return 2
    _warn("root: %s (via %s)" % (root, source))
    _warn("store: %s (exists: %s)"
          % (bs.store_path(root), os.path.isfile(bs.store_path(root))))
    _warn("fence dir: %s" % fence_dir(root))
    rc = cmd_session_label(argv)
    if rc == 0:
        _warn("the label above is PUBLIC and safe to paste; the token behind "
              "it is not printed by any command in this file.")
    return rc


def cmd_query(argv):
    """Would the fence allow writing PATH right now? C3 (the real M11): a
    query verb callable from the command line, for anyone who wants the
    hook's answer without staging an actual edit for Claude Code to catch.

    Answers by building the same PreToolUse-shaped payload the real hook
    would receive for an Edit of PATH, under the given session, and handing
    it to decide() -- the exact function cmd_hook() calls. This is a thin
    wrapper around that one decision, never a second implementation of the
    fence rule: any drift between what this prints and what the real hook
    does would mean the two calls ran different code, which they cannot.

    Usage: bm_fence_hook.py query PATH [--session-id ID]
    Session id lookup order matches session-label: --session-id, then
    BM_FENCE_SESSION_ID.

    Prints ALLOW or DENY (with the reason) on stdout; exits 0 for ALLOW
    (including a fail-open, which is an allow with the reason on stderr,
    same as the real hook), 1 for DENY, 2 for a usage error."""
    path = None
    session_id = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--session-id" and i + 1 < len(argv):
            session_id = argv[i + 1]
            i += 2
            continue
        if a.startswith("--session-id="):
            session_id = a.split("=", 1)[1]
            i += 1
            continue
        if path is None:
            path = a
        i += 1
    if not path:
        _warn("bm_fence_hook: query needs a PATH: bm_fence_hook.py query "
              "PATH [--session-id ID]")
        return 2
    if not session_id:
        session_id = os.environ.get("BM_FENCE_SESSION_ID", "")
    session_id = (session_id or "").strip()
    if not session_id:
        _warn("bm_fence_hook: no session id. Pass --session-id X or set "
              "BM_FENCE_SESSION_ID.")
        return 2
    p = {
        "session_id": session_id,
        "cwd": os.getcwd(),
        "hook_event_name": "PreToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": path},
    }
    decision, notes = decide(p)
    for n in notes:
        _warn(n)
    if decision is None:
        _out("ALLOW %s\n" % path)
        return 0
    reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
    _out("DENY %s: %s\n" % (path, reason))
    return 1


_COMMANDS = {
    "hook": cmd_hook,
    "session-label": cmd_session_label,
    "whoami": cmd_whoami,
    "query": cmd_query,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _COMMANDS:
        return _COMMANDS[argv[0]](argv[1:])
    if argv and argv[0].startswith("-") is False:
        _warn("bm_fence_hook: unknown command %r; expected one of: %s"
              % (argv[0], ", ".join(sorted(_COMMANDS))))
        return 2
    # No subcommand is the HOOK invocation, because that is how Claude Code
    # calls it: a bare command with a JSON payload on stdin.
    return cmd_hook(argv)


if __name__ == "__main__":
    sys.exit(main())
