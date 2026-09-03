#!/usr/bin/env python3
"""The instruction-and-configuration trust boundary: LT-401.B.

Why this file exists. `docs/THREAT_MODEL.md` names the largest unmitigated row
on that page: repository content read by an agent as though it were an
instruction. Two narrower failures sit inside that row and this check closes
them, nothing wider. First, an agent (or a task brief) can quietly widen its
own authority by editing the very files that grant it: `CLAUDE.md`, a hook, a
plugin manifest, an agent or skill definition, a CODEOWNERS line, a CI
workflow. Second, once such a file changes, the SAME session that changed it
can go on to read it as though the operator had typed it, using its own edit
as its own new instruction. This tool proves neither is happening in silence:
it names every changed file that could grant authority, and requires each one
to have been declared in the task's own stated scope AND bound to an
independent review trailer before it reads as anything but a violation.

What this proves, stated as narrowly as `tools/sbe_release_invariant.py`
states its own one mechanical question: did an authority-bearing file change
in this range, and if so, was that change named up front and is a second
party's review trailer on the commit that did it. It does not read what the
file now SAYS, beyond the one shape check its format allows (JSON parses, or
it does not). It does not detect a natural-language instruction hiding in a
comment, a README, or a receipt field: `docs/THREAT_MODEL.md`'s own row 11
already states that no content sanitizer exists here, and this check does not
change that. What it adds is narrower and mechanical: authority-bearing FILES
cannot move without being named and signed for, the same way
`sbe_release_invariant.py` proves distributable BYTES cannot move without
`VERSION` moving. Two yeses is a legitimate, reviewed configuration change;
silence, or one yes without the other, is the exact defect the trust model's
baseline instruction rule names: a task that changes an instruction surface is
code under security review, never active instruction for the worker that
changed it.

Honesty law inherited from this project's other tooling: absent evidence is
NO-DATA, never PASS. A change that touches none of the nine detected surfaces
reads NO-DATA, not PASS: silence about the configuration is not evidence the
configuration is safe. FAIL is reserved for the two provably wrong shapes: an
authority surface changed outside the declared, reviewed scope, or a control
file this tool tried to read and could not parse or open. A surface that
changed, was declared, was reviewed, and carries a configuration type this
tool has no parser for (there is no YAML parser in the Python standard
library, and a `.github/workflows/*.yml` file is exactly this shape) reads
NO-DATA naming the unmeasured path, never folded silently into a PASS that
never actually read it.

One limit stated rather than left implicit, mirroring
`sbe_release_invariant.py`'s own stated default-base limit: the default base
is `origin/main`, meaningful on a pull request and degenerate on a direct push
to `main`, where the diff against `origin/main` is empty by the time CI checks
it out. NO-DATA never blocks `--strict` here, exactly as everywhere else in
this project.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import Check as _Check, answered, evidence_problem, say
from sbe_release_invariant import DEFAULT_BASE, _git, _named_few

# The nine detected authority-surface families, named once so a change here is
# the only place this gate's scope can drift, mirroring how
# sbe_release_invariant.py names DISTRIBUTABLE_DIRS/DISTRIBUTABLE_FILES once.
# Matched by the FIRST path segment for a directory tree (never a substring:
# `hooks-legacy/` or a JS app's own `src/hooks/` must not collide with the
# top-level `hooks/` this project watches), by exact path for a single file
# this project expects at its repository root, and by basename at ANY depth
# for CLAUDE.md, because Claude Code loads a directory's own CLAUDE.md in
# addition to the repository root's, so a nested one carries the same
# authority and is watched the same way.
AUTHORITY_TOP_DIRS = (".claude", ".claude-plugin", "hooks")
AUTHORITY_ROOT_FILES = (".mcp.json",)
# The three locations GitHub itself reads a CODEOWNERS file from. A file named
# CODEOWNERS anywhere else is not this file to GitHub and is not treated as
# one here either.
AUTHORITY_CODEOWNERS_PATHS = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS")


def _matched_surface(path):
    """The detected authority-surface family `path` belongs to, or "" for none.

    Nine families: CLAUDE.md, .claude/**, .mcp.json, .claude-plugin/**,
    hooks/**, agents/*.md, skills/*/SKILL.md, CODEOWNERS,
    .github/workflows/**. `agents/*.md` and `skills/*/SKILL.md` are matched
    at exactly the depth their own glob spelling names (one file directly
    under agents/, one file named SKILL.md exactly one level under skills/),
    never deeper: a file under `agents/fixtures/old.md` is not an agent
    definition this project ships, and matching it would be the same
    substring-shaped drift sbe_release_invariant.py's own docstring warns
    against.
    """
    path = path.strip().strip("/")
    if not path:
        return ""
    parts = path.split("/")
    base = parts[-1]
    if base == "CLAUDE.md":
        return "CLAUDE.md"
    if path in AUTHORITY_ROOT_FILES:
        return ".mcp.json"
    if path in AUTHORITY_CODEOWNERS_PATHS:
        return "CODEOWNERS"
    head = parts[0]
    if head in AUTHORITY_TOP_DIRS and len(parts) > 1:
        return head + "/**"
    if head == "agents" and len(parts) == 2 and base.endswith(".md"):
        return "agents/*.md"
    if head == "skills" and len(parts) == 3 and base == "SKILL.md":
        return "skills/*/SKILL.md"
    if len(parts) >= 3 and head == ".github" and parts[1] == "workflows":
        return ".github/workflows/**"
    return ""


def _content_kind(path):
    """"json", "text", or "unsupported": what this check knows how to open path as.

    Deliberately narrow, and the narrowness is the honest part. Python 3.9's
    standard library ships no YAML parser, so a changed
    `.github/workflows/*.yml` is a real detected authority surface this tool
    cannot validate the inside of; `check()` reports it as unsupported and
    names it rather than silently treating an unopened file as clean.
    `.json` is parsed with `json.load`. Everything else this tool reads at
    all (a markdown memory, agent or skill file; a plain-text CODEOWNERS)
    has no formal syntax worth checking beyond "can this be opened and
    read", which `evidence_problem` already answers before this function is
    asked anything.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return "json"
    if ext == ".md" or os.path.basename(path) == "CODEOWNERS":
        return "text"
    return "unsupported"


class _DeclarationUnreadable(Exception):
    """Raised, never a made-up return value, when --declared FILE was given
    but could not be read as a plain declaration list. `check()` catches
    this and FAILs by name rather than silently treating a broken control
    file as "nothing declared", which would relabel every changed surface
    as an undeclared violation for the wrong stated reason."""


def _declared_paths(declared_file, declared_env):
    """The declared-path set, unioned from `declared_env` and `declared_file`.

    The declaration contract, stated precisely because nothing upstream of
    this tool enforces it: a path is declared when it appears, one per
    line, in `declared_file` ('#' starts a trailing comment, blank lines
    are ignored), OR in `declared_env` (paths separated by a comma or a
    newline). A line ending in '/' declares every path under that
    directory; anything else must match a changed path's exact
    git-relative spelling. Neither source given means nothing is declared,
    by design: a check whose unconfigured default is "everything is
    declared" would not be a check. Raises _DeclarationUnreadable when
    `declared_file` was given but could not be opened and read.
    """
    declared = set()
    for tok in re.split(r"[,\n]", declared_env or ""):
        tok = tok.strip()
        if tok:
            declared.add(tok)
    if declared_file:
        problem = evidence_problem(declared_file)
        if problem:
            raise _DeclarationUnreadable(
                "the --declared file %r %s" % (declared_file, problem))
        try:
            with open(declared_file, "r") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        declared.add(line)
        except OSError as e:
            raise _DeclarationUnreadable(
                "the --declared file %r could not be opened (%s: %s)"
                % (declared_file, type(e).__name__, e))
    return declared


def _is_declared(path, declared):
    if path in declared:
        return True
    return any(d.endswith("/") and path.startswith(d) for d in declared)


# The two trailer names tools/sbe_gate.py's gate_approval already reads
# (there via git_trailers, alongside the signature and identity fields its
# own self-approval guard needs). This tool reads only the trailer text via
# one `git log -1 --format=%B`, because it asks a narrower question: is a
# review trailer present and non-vacuous, not whether the approver differs
# from the author. That stronger identity proof stays gate_approval's job,
# meant to run ALONGSIDE this check on a money- or partner-path change that
# also happens to touch an authority surface, not duplicated inside it.
_TRAILER_NAMES = ("Approved-by", "Reviewed-in")


def _reviewed(root):
    """The trailer name (Approved-by or Reviewed-in) that HEAD's own commit
    message carries with a non-vacuous value, or "" if neither is present.
    Binding only: proves a review trailer is present on HEAD and says
    something, never that the review was thorough or that the approver
    differs from the author."""
    ok, out, _err = _git(root, "log", "-1", "--format=%B")
    if not ok:
        return ""
    for name in _TRAILER_NAMES:
        m = re.search(r"^%s:\s*(.+)$" % name, out, re.M)
        if m and answered(m.group(1)) is not None:
            return name
    return ""


def check(root, base, declared_file=None, declared_env=None):
    """(verdict, evidence) for the instruction-and-configuration trust
    boundary over `root`, `base`..HEAD.

    The first four branches mirror tools/sbe_release_invariant.py's own
    check() verbatim in shape: not a git working tree, an unborn HEAD, an
    unresolvable base ref, and a `git diff` that itself failed are each a
    NAMED NO-DATA reason, never a crash and never a silent pass over a range
    nobody could compute. Past that point this check asks a different
    question than release-invariant does: not whether any tracked byte
    moved, but whether a file that could grant authority moved, and if one
    did, whether it was declared up front and bound to an independent
    review trailer. It proves exactly that binding and nothing about
    whether the review was thorough or the declaration was honest.
    """
    ok, _out, err = _git(root, "rev-parse", "--is-inside-work-tree")
    if not ok:
        return "NO-DATA", ("%s is not a git working tree here (%s), so no range could be "
                           "compared" % (root, err or "git refused"))
    ok, head_out, err = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    head = head_out.strip()
    if not ok or not head:
        return "NO-DATA", ("HEAD does not resolve to a commit in %s (%s); an unborn checkout "
                           "has nothing to compare" % (root, err or "no commits yet"))
    ok, base_out, err = _git(root, "rev-parse", "--verify", "--quiet", base + "^{commit}")
    base_commit = base_out.strip()
    if not ok or not base_commit:
        return "NO-DATA", ("base ref %r does not resolve in %s (%s); a shallow checkout or a "
                           "ref this clone never fetched reads as unknown, never as a silent "
                           "pass over a range nobody could compute" % (base, root, err or "unknown revision"))
    ok, diff_out, err = _git(root, "diff", "--name-only", base_commit, head)
    if not ok:
        return "NO-DATA", ("git diff between %s and %s failed in %s (%s), so the changed-file "
                           "set is unknown" % (base_commit[:12], head[:12], root, err or "git refused"))
    changed = sorted({line.strip() for line in diff_out.splitlines() if line.strip()})
    surfaces = [p for p in changed if _matched_surface(p)]
    span = "%s..%s" % (base_commit[:12], head[:12])
    if not surfaces:
        return "NO-DATA", ("no changed path over %s matched a detected authority surface "
                           "(CLAUDE.md, .claude/**, .mcp.json, .claude-plugin/**, hooks/**, "
                           "agents/*.md, skills/*/SKILL.md, CODEOWNERS, .github/workflows/**) "
                           "out of %d file(s) changed in all; absence of a changed surface is "
                           "not evidence the change stayed inside its lane, so this is not a "
                           "PASS either" % (span, len(changed)))

    if declared_env is None:
        declared_env = os.environ.get("SBE_INSTRUCTION_SURFACE_DECLARED", "")
    try:
        declared = _declared_paths(declared_file, declared_env)
    except _DeclarationUnreadable as e:
        return "FAIL", ("%s; every one of the %d changed authority surface(s) over %s is "
                        "treated as undeclared until this is fixed, rather than silently read "
                        "as approved: %s" % (e, len(surfaces), span, _named_few(surfaces)))

    undeclared = [p for p in surfaces if not _is_declared(p, declared)]
    malformed, unsupported = [], []
    for p in surfaces:
        full = os.path.join(root, p)
        if not os.path.exists(full):
            continue  # deleted between base and HEAD: named above, nothing left here to read
        problem = evidence_problem(full)
        if problem:
            malformed.append("%s (%s)" % (p, problem))
            continue
        kind = _content_kind(p)
        if kind == "unsupported":
            unsupported.append(p)
            continue
        if kind == "json":
            try:
                with open(full, "r") as f:
                    json.load(f)
            except (OSError, ValueError) as e:
                malformed.append("%s (invalid JSON: %s)" % (p, e))

    if malformed or undeclared:
        parts = []
        if malformed:
            parts.append("%d control file(s) are malformed or unreadable: %s"
                         % (len(malformed), _named_few(malformed)))
        if undeclared:
            parts.append("%d changed authority surface(s) were not in the declared scope: %s"
                         % (len(undeclared), _named_few(undeclared)))
        return "FAIL", ("over %s: %s" % (span, "; ".join(parts)))

    if unsupported:
        return "NO-DATA", ("%d of %d changed, declared authority surface(s) over %s use a "
                           "configuration type this check has no parser for and are "
                           "UNMEASURED: %s; the rest carry no content problem, but this run "
                           "cannot certify the whole set while part of it went unread"
                           % (len(unsupported), len(surfaces), span, _named_few(unsupported)))

    trailer = _reviewed(root)
    names = _named_few(surfaces)
    if not trailer:
        return "NO-DATA", ("%d changed authority surface(s) over %s are declared in scope (%s) "
                           "but HEAD carries no non-vacuous Approved-by: or Reviewed-in: "
                           "trailer, so the required independent review is unresolved"
                           % (len(surfaces), span, names))

    return "PASS", ("%d changed authority surface(s) over %s are declared in scope and HEAD "
                    "carries a %s: trailer (%s); this proves only that the change was declared "
                    "and bound to review, not that the review content was sufficient"
                    % (len(surfaces), span, trailer, names))


CHECKS = {"instruction-surface": _Check(
    check, reads=(".claude-plugin/plugin.json",), kind="git", severity="gate",
    empty_expect="NO-DATA", empty_fixture="",
    full_fixture={"files": {".claude-plugin/plugin.json": "{}\n"}, "git": {"message": "test"}},
    full_expect="NO-DATA",
    full_expect_reason=("the honesty harness's generic fixture builds one commit with no ref to "
                        "diff against, so base..HEAD is always empty here, exactly as it is for "
                        "sbe_release_invariant.py's own full_fixture; this check needs a real "
                        "two-point history to ever reach PASS or FAIL, which "
                        "tools/test_sbe_instruction_surface.py exercises directly with a real "
                        "repository instead"))}

USAGE = (
    "usage: sbe_instruction_surface.py [directory] [base-ref] [--declared FILE] [--strict]\n"
    "  reads: git history under the directory (default .); the file named by --declared, if\n"
    "         given; the SBE_INSTRUCTION_SURFACE_DECLARED environment variable, if set; the\n"
    "         content of any changed CLAUDE.md, .claude/**, .mcp.json, .claude-plugin/**,\n"
    "         hooks/**, agents/*.md, skills/*/SKILL.md, CODEOWNERS or .github/workflows/**\n"
    "         path that still exists at HEAD.\n"
    "  writes: nothing.\n"
    "  directory   the git working tree to check (default: .)\n"
    "  base-ref    the ref to diff against HEAD (default: %s)\n"
    "  flags:\n"
    "    --declared FILE  a text file naming declared paths, one per line ('#' starts a\n"
    "                     comment, blank lines ignored); a line ending in '/' declares a\n"
    "                     whole directory prefix, anything else matches a changed path\n"
    "                     exactly\n"
    "    --strict         make a FAIL block the exit code\n"
    "    -h, --help       print this and exit 0 without examining anything\n"
    "\n"
    "  A path is DECLARED when it appears in --declared FILE or in the\n"
    "  SBE_INSTRUCTION_SURFACE_DECLARED environment variable (paths separated by a comma or\n"
    "  a newline); neither given means nothing is declared.\n"
    "\n"
    "  A change is INDEPENDENTLY REVIEWED when HEAD's own commit message carries a\n"
    "  non-vacuous Approved-by: or Reviewed-in: trailer, the same two trailers\n"
    "  tools/sbe_gate.py's approval gate reads. This check does not itself verify the\n"
    "  approver differs from the author; that remains that gate's own job."
) % DEFAULT_BASE


def main():
    argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        for line in USAGE.splitlines():
            say(line)
        sys.exit(0)
    strict = False
    declared_file = None
    positional = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--strict":
            strict = True
            i += 1
            continue
        if a == "--declared":
            if i + 1 >= len(argv):
                for line in USAGE.splitlines():
                    say(line)
                say("sbe_instruction_surface: --declared needs a file path argument")
                sys.exit(2)
            declared_file = argv[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            for line in USAGE.splitlines():
                say(line)
            say("sbe_instruction_surface: unrecognized flag %r; refusing rather than running "
                "past it" % a)
            sys.exit(2)
        positional.append(a)
        i += 1
    roots, bases = [], []
    for a in positional:
        if os.path.isdir(a):
            roots.append(a)
        else:
            bases.append(a)
    if len(roots) > 1:
        say("sbe_instruction_surface: one directory at a time, got %d (%s)."
            % (len(roots), ", ".join(roots)))
        sys.exit(1)
    if len(bases) > 1:
        say("sbe_instruction_surface: one base ref at a time, got %d (%s)."
            % (len(bases), ", ".join(bases)))
        sys.exit(1)
    root = roots[0] if roots else "."
    base = bases[0] if bases else DEFAULT_BASE
    verdict, evidence = check(root, base, declared_file=declared_file)
    say("BROTHERSBE INSTRUCTION SURFACE  (advisory unless --strict; NO-DATA is never a pass)")
    say("  %-18s %-8s %s [severity: gate]" % ("instruction-surface", verdict, evidence))
    if strict and verdict == "FAIL":
        say("STRICT: an authority surface changed outside declared, reviewed scope; exiting "
            "nonzero to block the merge.")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # A broken gate must not silently pass work. In strict mode a crash blocks,
        # mirroring tools/sbe_release_invariant.py's own top-level guard.
        say("sbe_instruction_surface: error %r" % (e,))
        sys.exit(1 if "--strict" in sys.argv else 0)
