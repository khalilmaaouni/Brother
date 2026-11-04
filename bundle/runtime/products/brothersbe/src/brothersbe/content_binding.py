"""A record binds to a DIGEST OF THE TRACKED CONTENT it covers, not to the
commit identifier that happened to be checked out when it was written.

WHY THIS EXISTS. `sbe handover prepare` and `sbe evidence run` each used to
bind a record to the commit HEAD pointed at when they ran (`headSha` /
`headCommit`), then compare it later by exact equality against the current
HEAD. The record has to be committed to reach a second person at all, and
that commit becomes the new HEAD: one past the commit the record just
named. Exact equality stales the record over the exact commit that carries
it, which defeats sharing it in the first place. See
`docs/adr/2026-08-12-handover-across-two-machines.md` for the walk that
found this, and `docs/plans/2026-08-12-overnight-team-version-and-roadmap.md`
section 2.1 for the founder decision this module implements: bind to a
digest of the content, not to the commit identifier.

THE PROPERTY THIS MUST NOT LOSE. A commit that bundles the record with real
work must still stale the record; that guard is the entire reason binding
exists. This module keeps it by construction rather than by a special case:
`digest_excluding` hashes every tracked path at a commit EXCEPT the paths a
caller names as the record's own (and its lock sidecar, where it has one).
Bundling real work changes a tracked path outside that exemption, so the
digest changes too. Only a commit whose entire diff sits inside the
exemption leaves the digest unchanged, which is exactly the case that
should survive.

WHAT IS COMPARED, and what is not. `content_unchanged` compares the digest
at the record's own bound commit against the digest at the current HEAD.
The two commits need not be ancestor and descendant of one another for this
to answer: a rebase or an amend that reproduces the same tree content reads
as unchanged too, which a pure commit-walk could never say, because the
digest never has to walk between them. This module never reads the working
tree directly (uncommitted edits are a different, already-reported concern:
`working_tree_dirty` and the dossier's own `worktrees` list); every digest
here is computed from a commit's tree exactly as git stored it.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only.
"""
import hashlib
import subprocess

#: The marker used in place of a blob sha when a path a caller asked about
#: is not tracked at the ref being hashed (deleted, renamed away, never
#: existed there). A fixed marker rather than skipping the path silently,
#: so a deletion changes the digest exactly the way a content edit does.
_ABSENT = "0" * 40


def _git(args, cwd):
    out = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def tracked_blobs(cwd, ref):
    """{repo-root-relative forward-slash path: blob sha} for every path
    tracked at `ref`, or None when `ref` does not resolve to a tree at all
    (a bad ref, an unborn branch, a repository unreachable from `cwd`)."""
    code, out, _err = _git(["ls-tree", "-r", "--full-tree", ref], cwd)
    if code != 0:
        return None
    blobs = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        meta, tab, path = line.partition("\t")
        if not tab or not path:
            continue
        fields = meta.split()
        if len(fields) < 3:
            continue
        blobs[path] = fields[2]
    return blobs


def digest_excluding(cwd, ref, exempt_rel):
    """A stable sha256 hex digest over every path `tracked_blobs(cwd, ref)`
    reports, EXCEPT the paths in `exempt_rel` (repo-root-relative,
    forward-slash: a record's own path, and its lock sidecar where it has
    one). Returns None when `ref` does not resolve, so a caller can tell
    "nothing to compare" apart from "compared and matched" -- NO-DATA,
    never a guess."""
    blobs = tracked_blobs(cwd, ref)
    if blobs is None:
        return None
    exempt = frozenset(exempt_rel)
    hasher = hashlib.sha256()
    for path in sorted(blobs):
        if path in exempt:
            continue
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(blobs[path].encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def content_unchanged(cwd, bound_ref, current_ref, exempt_rel):
    """True when the tracked content at `bound_ref`, excluding `exempt_rel`,
    is identical to the tracked content at `current_ref` excluding the same
    paths -- the check that replaces exact commit equality. False whenever
    either ref fails to resolve, `bound_ref` is falsy, or the digests
    differ: the conservative default in every case that is not a positive
    content match, the same posture exact-equality always had for anything
    that was not an exact match."""
    if not bound_ref or not current_ref:
        return False
    bound_digest = digest_excluding(cwd, bound_ref, exempt_rel)
    current_digest = digest_excluding(cwd, current_ref, exempt_rel)
    if bound_digest is None or current_digest is None:
        return False
    return bound_digest == current_digest
