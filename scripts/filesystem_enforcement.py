#!/usr/bin/env python3
"""DOM-40.02: a pure decision function for filesystem write/read enforcement.

WHY THIS EXISTS. A builder session declares where it may read, where it
may write, which trees are immutable, and which are scratch space. This
module is the one place that answers "may this path be touched" given
that declaration. It decides; it never acts. No file is ever written,
moved, or deleted by anything in this module: decide() and the CLI both
only classify a path and report a verdict.

THE DECIDING PROPERTY. Every escape attempt is refused: parent-directory
traversal, a symlink pointing out of the write root, a hard link, a path
inside a git submodule or an alternate worktree of the same repository, a
path assembled at runtime from parts, the system temp directory (unless
it was itself declared), a hidden dotfile, all of it. Permission to write
is granted ONLY by a positive match against a declared write root (or a
declared temporary root) after full filesystem resolution; nothing is
granted by a path merely failing to look suspicious.

FAIL DIRECTION, the whole point of this module: a path that cannot be
resolved, a declared root that does not exist, or any error encountered
while deciding is REFUSED, never allowed. Ambiguity always resolves to
REFUSE. The one exception, spelled out below, is where ambiguity would
make the module useless (ordinary directories look "hard linked" under
POSIX for reasons that have nothing to do with an attack): that specific
case is scoped narrowly rather than exempted broadly.

ROOTS. A `roots` argument is a mapping with up to four keys, each an
iterable of absolute directory paths:
    read       readable; not writable.
    write      readable and writable.
    immutable  readable; writable is always refused here even if a less
               specific root would otherwise allow it.
    temporary  readable and writable, scratch space (declared explicitly,
               never inferred from the path merely looking like a temp
               directory).
A missing key is treated as an empty list.

MATCHING. Every declared root is resolved to its real, absolute path.
Roots that cannot be resolved (do not exist, or are not directories) are
excluded from matching, never treated as a match by omission. Among the
roots that DO contain the resolved target, the LONGEST (most specific)
one wins, across all four categories at once. This is what makes "two
roots where one contains the other" and "a write root inside a read-only
root" resolve correctly with one mechanism instead of a special case per
combination: nest an immutable carve-out inside a write root, or a write
root inside an otherwise read-only tree, and the more specific
declaration wins either way.

CONTINGENCY AND EDGES, named here because the docstring is where the next
reader looks before extending this module, not after it breaks:

  empty roots declaration     no root declared in any category at all.
                              Refused outright, before any resolution is
                              attempted: "refuse everything" is not a
                              fallback path here, it is the first check.
  every declared root missing every declared root exists syntactically
                              but none resolves to a real directory on
                              disk. Refused and named distinctly from the
                              case above and from "resolved but the path
                              is not under any of them", because a
                              misconfigured declaration is a different
                              fact than a correct declaration this path
                              simply falls outside of.
  a relative path             `path` is not absolute. Refused rather than
                              silently resolved against the process's
                              current working directory, because the cwd
                              is exactly the kind of ambient state this
                              estate's own rules forbid trusting without
                              a check: the same relative string means a
                              different real location depending on where
                              the caller happens to be standing.
  a path that does not exist  a file about to be created. realpath()
  yet                         resolves every existing ancestor and leaves
                              a nonexistent tail alone rather than
                              raising, so this is the ordinary case, not
                              an error: creation is a write, and a write
                              is exactly what this module is deciding.
  a path whose parent is a    resolved the same way any other symlink is:
  symlink                     fully, before the containment check runs,
                              so a parent symlink that stays inside the
                              matched root is allowed and one that
                              escapes it is refused, on the same rule.
  a write root inside a       see MATCHING above: longest match wins.
  read-only root
  two roots where one         see MATCHING above: longest match wins,
  contains the other          same mechanism regardless of which
                              category nests inside which.

WHAT THIS MODULE DELIBERATELY DOES NOT COVER, named rather than left
implicit: it has no stored state and nothing else can act on its
declaration between calls, so "a concurrent second actor", "already
done", "partially done", and "expired or stale" do not apply to a pure
function with no side effects. It also cannot stop a process that never
calls decide() at all and instead opens the file directly: like
orchestrator_boundary.py's own honesty requirement, this is a mechanical
check in front of code that chooses to call it, not an OS-level sandbox.

THE ONE NARROWLY SCOPED EXCEPTION TO "AMBIGUITY REFUSES": the hard-link
and git-submodule/worktree vetoes below run only for a WRITE decision.
Reading through a hard link or reading a file that happens to live under
a submodule or a linked worktree is ordinary and harmless; it is writing
through them that can corrupt a file, or a repository, outside the
caller's own lane. Scoping the veto to writes is a deliberate choice,
not an oversight, and it is named here so a future reader does not
"fix" it into refusing reads too and call that a tightening.
# ponytail: st_nlink > 1 refuses ANY regular file with more than one
# directory entry, even a benign one unrelated to this attack, because
# this module cannot tell which OTHER name refers to the same inode
# without a filesystem-wide scan it does not have. Tighten this to an
# inode allowlist if false positives show up in real use.

Python 3 standard library only. No network, no subprocess, no file
mutation of any kind.
"""
import argparse
import json
import os
import stat
import sys

ALLOW = "ALLOW"
REFUSE = "REFUSE"

_CATEGORIES = ("read", "write", "immutable", "temporary")


class Verdict(object):
    """The whole return value of decide(): which way it went, which named
    rule decided it, and a human-readable reason. Equality and repr exist
    so a test can assert on the tuple shape without reaching into private
    fields."""

    __slots__ = ("decision", "rule", "reason")

    def __init__(self, decision, rule, reason):
        assert decision in (ALLOW, REFUSE)
        self.decision = decision
        self.rule = rule
        self.reason = reason

    @property
    def allowed(self):
        return self.decision == ALLOW

    def __eq__(self, other):
        if not isinstance(other, Verdict):
            return NotImplemented
        return (self.decision, self.rule) == (other.decision, other.rule)

    def __repr__(self):
        return "Verdict(%s, rule=%r, reason=%r)" % (
            self.decision, self.rule, self.reason)


def _refuse(rule, reason):
    return Verdict(REFUSE, rule, reason)


def _allow(rule, reason):
    return Verdict(ALLOW, rule, reason)


def _normalize_roots(roots):
    roots = roots or {}
    return {cat: list(roots.get(cat) or []) for cat in _CATEGORIES}


def _resolve_declared_root(root):
    """The real, absolute path of a declared root, or None when it
    cannot be used: not absolute, does not exist, or is not a directory.
    None means "excluded from matching", never "matches everything"."""
    try:
        if not isinstance(root, str) or not os.path.isabs(root):
            return None
        real = os.path.realpath(root)
        if not os.path.isdir(real):
            return None
        return real
    except (OSError, ValueError):
        return None


def _resolve_target(path):
    """(lexical, real) for `path`. `lexical` collapses '..' purely as
    string manipulation (os.path.normpath), touching no filesystem entry.
    `real` follows every symlink realpath can find, tolerating a
    nonexistent tail. When the two differ, some ancestor was a genuine
    symlink: '..' alone can never produce that difference, since normpath
    already collapses it exactly the way a symlink-free realpath would.
    That comparison is what tells a parent-directory traversal apart
    from a symlink escape without walking the path by hand."""
    lexical = os.path.normpath(path)
    real = os.path.realpath(path)
    return lexical, real


def _find_git_link(real_path):
    """The path to a regular-file `.git` (a submodule or a linked
    worktree marker; an ordinary repository's `.git` is a directory and
    never matches os.path.isfile here) among `real_path` and its
    ancestors, or None. Capped at 64 levels against a pathological loop;
    an unreadable ancestor is skipped, never treated as a hit or a
    reason to abort the whole decision."""
    current = real_path if os.path.isdir(real_path) else os.path.dirname(real_path)
    seen = 0
    while current and seen < 64:
        candidate = os.path.join(current, ".git")
        try:
            if os.path.isfile(candidate):
                return candidate
        except OSError:
            pass
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        seen += 1
    return None


def _git_link_kind(git_file_path):
    """'git-submodule', 'git-worktree', or the generic 'git-link' when the
    marker's own gitdir line does not say which. Unreadable content is
    still a hit (generic), never a pass-through: finding the marker at
    all is the refusal, reading its contents only sharpens the reason."""
    try:
        with open(git_file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return "git-link"
    if "/modules/" in content:
        return "git-submodule"
    if "/worktrees/" in content:
        return "git-worktree"
    return "git-link"


def decide(path, roots, action="write"):
    """ALLOW or REFUSE `path` for `action` ('write', the default, or
    'read') against `roots`. Never raises: every error this function can
    anticipate is caught and turned into a REFUSE verdict naming the
    rule, and an outer catch-all does the same for anything it did not
    anticipate, so "any error while deciding is REFUSED" holds even for
    a defect in this function itself."""
    try:
        if action not in ("read", "write"):
            return _refuse("unknown-action",
                            "action %r is neither 'read' nor 'write'" % (action,))
        normalized = _normalize_roots(roots)
        if sum(len(v) for v in normalized.values()) == 0:
            return _refuse(
                "empty-roots-declaration",
                "no read, write, immutable, or temporary root was declared "
                "at all; an empty declaration refuses everything rather "
                "than defaulting to permitted")
        if not isinstance(path, str) or not os.path.isabs(path):
            return _refuse(
                "relative-path",
                "path %r is not absolute; resolving it against this "
                "process's current working directory would make the same "
                "string mean a different location depending on where the "
                "caller happens to be standing, so it is refused instead" % (path,))

        try:
            lexical, real = _resolve_target(path)
        except (OSError, ValueError) as exc:
            return _refuse("unresolvable",
                            "could not resolve %r: %s" % (path, exc))
        had_symlink = (lexical != real)

        resolved_roots = {}
        unusable = []
        for category in _CATEGORIES:
            resolved_roots[category] = []
            for root in normalized[category]:
                resolved_root = _resolve_declared_root(root)
                if resolved_root is None:
                    unusable.append((category, root))
                else:
                    resolved_roots[category].append(resolved_root)
        usable_count = sum(len(v) for v in resolved_roots.values())
        if usable_count == 0:
            return _refuse(
                "no-usable-roots",
                "every declared root (%s) failed to resolve to a real "
                "directory; a root that does not exist grants nothing, "
                "it is never treated as absent-and-therefore-fine" %
                (", ".join("%s=%s" % (c, r) for c, r in unusable) or "none"))

        # Vetoes below run only for a write decision: see the module
        # docstring's "ONE NARROWLY SCOPED EXCEPTION" for why reads are
        # exempt from both.
        if action == "write":
            try:
                exists = os.path.exists(real)
                target_stat = os.stat(real) if exists else None
            except OSError as exc:
                return _refuse("unresolvable",
                                "could not stat %r: %s" % (real, exc))
            if (target_stat is not None
                    and stat.S_ISREG(target_stat.st_mode)
                    and target_stat.st_nlink > 1):
                return _refuse(
                    "hard-link",
                    "%r has %d directory entries (st_nlink); this module "
                    "cannot verify that its other name(s) stay inside the "
                    "declared roots, so a write through any of them is "
                    "refused rather than assumed safe" %
                    (real, target_stat.st_nlink))

            git_link = _find_git_link(real)
            if git_link is not None:
                kind = _git_link_kind(git_link)
                return _refuse(
                    kind,
                    "%r sits under %s, a linked git directory found at "
                    "%r; writing here would mutate another repository's "
                    "own administration, which this module always "
                    "refuses regardless of the declared write roots" %
                    (real, kind.replace("git-", ""), git_link))

        candidates = []
        for category in _CATEGORIES:
            for resolved_root in resolved_roots[category]:
                if real == resolved_root or real.startswith(resolved_root + os.sep):
                    candidates.append((len(resolved_root), category, resolved_root))
        if not candidates:
            note = ""
            if unusable:
                note = " (ignored non-existent root(s): %s)" % (
                    ", ".join("%s=%s" % (c, r) for c, r in unusable))
            return _refuse(
                "outside-declared-roots",
                "%r resolves to %r, which is not under any declared "
                "root for a %s action%s%s" %
                (path, real, action, note,
                 " (a symlink was involved in resolving it)" if had_symlink else ""))

        candidates.sort(key=lambda c: c[0])
        _, category, matched_root = candidates[-1]

        if category == "immutable":
            if action == "write":
                return _refuse(
                    "immutable-root",
                    "%r is under the immutable root %r; writes there are "
                    "always refused here even though a less specific "
                    "declaration might otherwise allow it" % (real, matched_root))
            return _allow("immutable-root",
                          "%r is under the immutable root %r; reading is fine" %
                          (real, matched_root))
        if category == "read":
            if action == "write":
                return _refuse(
                    "read-only-root",
                    "%r is only under the read root %r, never a write root" %
                    (real, matched_root))
            return _allow("read-root",
                          "%r is under the declared read root %r" % (real, matched_root))
        if category == "temporary":
            return _allow("temporary-root",
                          "%r is under the declared temporary root %r" %
                          (real, matched_root))
        # category == "write"
        return _allow("write-root",
                      "%r is under the declared write root %r" % (real, matched_root))
    except Exception as exc:  # noqa: BLE001 - last-resort fail-closed net
        return _refuse("internal-error",
                        "unexpected error while deciding %r: %s" % (path, exc))


def _load_roots(roots_json_path):
    with open(roots_json_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", required=True, help="the path to decide")
    ap.add_argument("--roots-json", required=True,
                    help="path to a JSON file with read/write/immutable/"
                         "temporary lists of absolute root paths")
    ap.add_argument("--action", choices=("read", "write"), default="write")
    args = ap.parse_args(argv)

    try:
        roots = _load_roots(args.roots_json)
    except (OSError, ValueError) as exc:
        print("filesystem_enforcement: CANNOT-DECIDE, could not read roots "
              "from %r: %s" % (args.roots_json, exc))
        return 2
    if not isinstance(roots, dict):
        print("filesystem_enforcement: CANNOT-DECIDE, %r did not contain a "
              "JSON object" % (args.roots_json,))
        return 2

    verdict = decide(args.path, roots, action=args.action)
    print("filesystem_enforcement: %s, rule=%s: %s" %
          (verdict.decision, verdict.rule, verdict.reason))
    return 0 if verdict.decision == ALLOW else 1


if __name__ == "__main__":
    sys.exit(main())
