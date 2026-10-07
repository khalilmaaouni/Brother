"""precut_review.py: freeze the candidate sha once and key every result to it.

PR1.a of the pre-cut review. One sha is written once, atomically, into a
freeze record under the record directory (default ~/.claude/evidence/precut,
override with --dir). Every later result is keyed to that sha, and a result
for any other sha reads FAIL, never a pass.

Absent, unreadable or malformed input reads NO-DATA. A moved HEAD, a dirty
tree, a re-created base tag, or a release-side commit the candidate does
not carry all read FAIL. Owner ruling 2026-10-04 (release base, option A):
the base is the hub commit the newest release note names (`release_base`),
the shared base is `git merge-base` of it and the candidate, and every
commit on the release side of that shared base must be on the candidate by
patch identity (`git cherry`) or be that release's own version bump
(`release_side`); no readable note, an unknown cut sha or a git that does
not answer is NO-DATA, never a pass. The freeze reads HEAD from the
repository's own git directory and every ref and object through git itself
(one `git cat-file --batch` process per git directory, started on first use,
and `git rev-parse` for refs), so packed objects and worktrees, whose objects
and refs live in the common directory, read the same as loose ones. An
object git cannot read, a git that fails to start or answers in a shape the
reader does not expect, reads as no object, never as a pass.

PR1.b, the BrotherSBE pass (`sbe`): every row runs inside a detached
temporary worktree at the frozen sha, so the moving main checkout cannot
change what is scanned, and the main checkout's HEAD, tree and porcelain
status are read before and after each row; a disturbance downgrades that
row and every later one to FAIL. The two range scanners run with the real
HOME (their terms file lives there); the candidate's own judges run with a
fresh empty HOME and no variable naming the record directory. A scanner's
output is counted, never forwarded into a record.

Owner ruling 2026-10-04 (option A, reviewed baseline and release-range
lint): the `waivers` row judges the marker count against the reviewed
baseline file (WAIVERS_BASELINE) as it stands at its anchor commit, the
release base or, until a release base carries the file, the pinned landing
commit WAIVERS_BASELINE_LANDED; it is never read from the candidate's
worktree, an edit since the anchor is FAIL naming the commit, and every
marker added since the base tag is printed. The `silent-lint` row gates
only on the files changed between the base tag and the frozen sha and
reports the whole tree count beside it. A release's own version bump is
exempt from patch identity only when every changed line differs in version
strings alone (digests and revisions too, inside the generated manifests),
moving upward to the version the subject names, and a plugin manifest
changes nothing but its version and ref fields.

The candidate never decides its own base or its own judge. The release
notes that decide the base are read only at the base tag's tree and at the
hub cut it names (`release_base`), and a note the candidate adds or whose
cut line it edits is FAIL (`note_drift`). The judge (this module and every
JUDGE_FILES entry) runs from the release base's copy when it carries one
(`judge_anchor`, `_dispatch_judge`, `sbe --as-judge`), and a judge file
changed since the release base needs the owner's review record, written by
`accept-judge` into the record directory outside every tree. Bootstrap,
stated plainly and bounded: no release base before the 1.1.0 cut carries
the judge, so a candidate declaring exactly 1.1.0 is judged by its own
code, recorded in the freeze with its blob shas and printed on every
freeze, verify and sbe line; any other candidate without a judge at its
release base is NO-DATA, and once any release tag at or past 1.1.0 exists
bootstrap is refused outright (`release_tags`). The base tag is derived:
the highest release tag, never a constant. Every judge child runs as
`python -I` (no working directory, no PYTHONPATH, no user site on its
path), the lint child runs outside the candidate tree, and the dispatch key
travels over the judge's stdin, never its argv. A dispatched judge copy
answers only through an explicit exit 0, rows marked with the judge, and a
manifest whose HMAC under that key covers every row, with no result file
modified after the judge exited; every git call ignores replace refs,
grafts and every inherited git location variable; an unreadable tree is
NO-DATA, never empty.
"""
from __future__ import annotations

import argparse
import atexit
import datetime
import hashlib
import hmac
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tmp_sandbox  # noqa: E402  the one list of git location variables

BASE_COMMIT = "97691d2ea7985933179d790d761ba436d94bfac7"
#: A public release tag reads vX.Y.Z. The 2.x and 3.x tags are the retired
#: line before the 0.9 renumber (measured 2026-10-04: v3.4.2 was tagged
#: 2026-08-24, before v0.9.2 on 2026-08-29 and v1.0.21 on 2026-09-20), so
#: they are never a release base and never count against bootstrap. The
#: tags live on the export line, not on the hub's history, so they are read
#: from the repository's refs rather than by reachability from the candidate.
_RELEASE_TAG_RE = re.compile(r"\Av(\d+)\.(\d+)\.(\d+)\Z")
RETIRED_MAJORS = (2, 3)
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".claude", "evidence", "precut")

PASS = "PASS"
FAIL = "FAIL"
NO_DATA = "NO-DATA"

#: Every file the candidate is judged by. A candidate can edit any of them, so
#: each is listed when it differs from the base tag (scanner-drift) and bound
#: to the frozen blob on disk (sha_current). Four scanners, the two modules
#: sbe_score.py imports beside it, the two batteries, and every check script
#: this module and the cut preflight run from the candidate tree.
JUDGE_FILES = (
    "scripts/outgoing_scan.py",
    "scripts/private_terms_scan.py",
    "products/brothersbe/tools/sbe_gate.py",
    "products/brothersbe/tools/sbe_score.py",
    "products/brothersbe/tools/sbe_checks.py",
    "products/brothersbe/tools/sbe_telemetry.py",
    "scripts/check_all.sh",
    "scripts/required_fast.sh",
    "scripts/cut_preflight.py",
    "scripts/precut_review.py",
    "scripts/tmp_sandbox.py",
    "scripts/regen_generated.py",
    "scripts/bundle_runtime.py",
    "scripts/system_doc.py",
    "scripts/codex_skills.py",
    "scripts/test_battery_registration.py",
    "products/brothermode/scripts/checksums.sh",
    "products/brothersbe/scripts/checksums.sh",
    "docs/plan/evidence/precut-waivers-baseline.json",
)
#: The reviewed baseline the `waivers` row judges against (owner ruling
#: 2026-10-04, option A): a JSON object carrying `count`, the marker count a
#: person reviewed, plus the sha and date it was taken at. Read from the
#: candidate at the frozen sha, so an edit of it shows in scanner-drift.
WAIVERS_BASELINE = JUDGE_FILES[-1]
#: The path scripts/outgoing_scan.py resolves for its private terms, outside
#: every repository. Read here before the scanners run: the scanner reads an
#: empty file as a valid empty term list and exits 0.
TERMS_FILE = "~/.brothersbe-private-names"
WAIVER_MARKER = "sbe: allow-silent"
SBE_ROWS = ("sbe-gate", "silent-lint", "waivers", "scanner-drift", "scan-range")
ROW_TIMEOUT_S = 1800

_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z")
_CHECK_RE = re.compile(r"\A[A-Za-z0-9_.-]+\Z")
#: Owner ruling 2026-10-04 (release base, option A): the base is the hub
#: commit the newest release note names. The two patterns mirror
#: scripts/cut_preflight.previous_cut_commit (a note is docs/releases/x.y.z.md
#: and its line reads "Cut from hub commit `<sha>`"); mirrored rather than
#: imported because that function reads only the newest note on disk and
#: this reader must also look through the base tag's tree.
_NOTE_NAME_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.md$")
_CUT_LINE_RE = re.compile(r"Cut from hub commit `([0-9a-f]{7,40})`")
_CHERRY_LINE_RE = re.compile(r"^([+-]) ([0-9a-f]{40})$")
#: A release's own version bump is exempt from patch identity when its
#: subject reads "<x.y.z>: the version bump" and every path it touches is a
#: version carrier or a generated manifest: a plugin or marketplace manifest,
#: the runtime and hooks manifests, a product CHECKSUMS.sha256, the project
#: facts module, docs/VERSIONING.md, and the product README, QUICKSTART,
#: RELEASE and SETUP pages that print the version. Nothing else.
_BUMP_SUBJECT_RE = re.compile(r"^(\d+\.\d+\.\d+): the version bump\b")
#: In a plugin or marketplace manifest a bump may change only the plugin's
#: own "version" fields and the "ref" that pins the release; a "source",
#: "url" or "path" value differing refuses naming the line. Everywhere, a
#: version string may change only to the version the subject names, and
#: only upward: a downgrade or a sideways move refuses.
_PLUGIN_JSON_RE = re.compile(r"^(?:[^/]+/)*\.(?:claude|codex|cursor)-plugin/(?:marketplace|plugin)\.json$")
_VERSION_FIELD_RE = re.compile(r'^\s*"(?:version|ref)"\s*:')
_VERSION_CARRIER_RE = re.compile(
    r"^(?:(?:[^/]+/)*\.(?:claude|codex|cursor)-plugin/(?:marketplace|plugin)\.json"
    r"|bundle/runtime/RUNTIME-MANIFEST\.json"
    r"|bundle/runtime/hooks/HOOKS-MANIFEST\.json"
    r"|(?:bundle/runtime/hooks|products)/brothermode/tools/bm_project_facts\.py"
    r"|products/[^/]+/CHECKSUMS\.sha256"
    r"|products/[^/]+/(?:README|docs/(?:QUICKSTART|RELEASE|SETUP))\.md"
    r"|docs/VERSIONING\.md)$")
#: The path allowance above only says where a bump may write; what it may
#: write is judged on content: every removed line must equal its added line
#: once version strings are normalised, and inside the three generated
#: manifests the digests, the source revision and the describe string too.
#: Nothing else may be added or removed, so a bump that repoints a plugin
#: source or edits logic in bm_project_facts.py refuses naming the line.
_GENERATED_MANIFEST_RE = re.compile(
    r"^(?:bundle/runtime/RUNTIME-MANIFEST\.json|bundle/runtime/hooks/HOOKS-MANIFEST\.json"
    r"|products/[^/]+/CHECKSUMS\.sha256)$")
_VERSION_STRING_RE = re.compile(r"\d+\.\d+\.\d+")
_MANIFEST_NOISE_RE = re.compile(r"\b[0-9a-f]{64}\b|\b[0-9a-f]{40}\b|-\d+-g[0-9a-f]{7,40}(?:-dirty)?\b")
#: Owner ruling 2026-10-04, reviewed baseline, anchored outside the
#: candidate's reach: the `waivers` row reads the baseline file as it stands
#: at the release base commit, never from the worktree. While the file does
#: not yet exist at a release base (the 1.1.0 cut is the first to carry it),
#: the anchor is the commit that landed it under the ruling, pinned here;
#: that commit must be an ancestor of the candidate. Any edit of the file
#: between the anchor and the candidate is FAIL naming the editing commit: a
#: baseline is raised only by its own reviewed commit, judged at the next
#: cut, and this pin moves to the release base once a cut carries the file.
WAIVERS_BASELINE_LANDED = "8ea38e0da2169a975f51891b4223b6673e0f1cb2"
#: The three values the candidate's own copy of this module pins. In
#: bootstrap they are the candidate's by necessity; from the first cut that
#: carries the judge on, the judge's copy owns them and a candidate that
#: re-pins any of them is refused unless the owner's review record covers
#: this module at the frozen sha (_pins_agree).
PINS = ("BASE_COMMIT", "WAIVERS_BASELINE_LANDED")
#: The one umbrella version allowed to be judged by its own code: the
#: first cut that carries the judge. Any other candidate whose release base
#: carries no judge is NO-DATA, never judged by itself.
BOOTSTRAP_VERSION = (1, 1, 0)
#: Seconds a read on the object reader's pipe may wait for the next byte
#: before the reader is dropped and the read answers no object.
_READ_TIMEOUT_S = 30


class NoDataError(ValueError):
    """A freeze that cannot be answered: no readable release note, an unknown
    cut sha, or a git that did not answer. Never a pass, exit 2 at the door."""
_GATE_LINE_RE = re.compile(r"^\s*(?:>>\s*)?([a-z]+)\s+(PASS|FAIL|NO-DATA|WAIVED)\b", re.M)
_OUTGOING_SUMMARY_RE = re.compile(r"^outgoing_scan: (\d+) hit\(s\) across \d+ commit\(s\)", re.M)
_PRIVATE_SUMMARY_RE = re.compile(
    r"^(?:REFUSED: (\d+) private term\(s\)|PASS: \d+ term\(s\) checked)", re.M)
_LINT_SNIPPET = ("import sys; sys.argv = ['silent-lint']; sys.path.insert(0, %r); "
                 "import sbe_score; v, e = sbe_score.silent_failure_lints(); "
                 "print(v); print(e)")


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def _git_dir(root):
    """The .git directory for root, or None. Reads the gitdir pointer file."""
    if not isinstance(root, str) or not root:
        return None
    path = os.path.join(root, ".git")
    if os.path.isdir(path):
        return path
    if os.path.isfile(path):
        try:
            with open(path, "rb") as fh:
                raw = fh.read(4096)
        except OSError:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("gitdir:"):
                target = line[len("gitdir:"):].strip()
                if not os.path.isabs(target):
                    target = os.path.normpath(os.path.join(root, target))
                return target if os.path.isdir(target) else None
    return None


def _resolve_ref(git_dir, ref):
    """Resolve a full ref name like refs/heads/main to a 40 hex sha, or None.
    Asked of git, so a ref that lives in a worktree's common directory or in
    packed-refs resolves the same as a loose one."""
    if not isinstance(git_dir, str) or not git_dir:
        return None
    if not isinstance(ref, str) or not ref.startswith("refs/"):
        return None
    proc = _run(["git", "--git-dir=" + git_dir, "rev-parse", "--verify", "--quiet", ref],
                git_dir)
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value if _SHA_RE.match(value) else None


#: One `git cat-file --batch` process per git directory, started on first use
#: and closed at exit, so a walk over thousands of commits costs one spawn.
_READERS = {}
#: Bytes read from a reader's pipe and not yet consumed, per git directory.
_BUFFERS = {}


def _git_env(env=None):
    """The environment every child of this module runs with: every git
    location variable (tmp_sandbox.GIT_LOCATION_VARS: GIT_DIR,
    GIT_OBJECT_DIRECTORY, GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_SHALLOW_FILE
    and the rest) removed, so an inherited one cannot point git at other
    objects; replace refs ignored and the grafts file pointed at nothing, so
    neither can swap the object a sha names (measured 2026-10-04: `git
    replace REAL FAKE` made the note reader return FAKE)."""
    merged = dict(os.environ if env is None else env)
    for name in tmp_sandbox.GIT_LOCATION_VARS:
        merged.pop(name, None)
    merged["GIT_NO_REPLACE_OBJECTS"] = "1"
    merged["GIT_GRAFT_FILE"] = os.devnull
    return merged


def _close_readers():
    """End every reader process; the next read starts a fresh one."""
    for proc in list(_READERS.values()):
        for stream in (proc.stdin, proc.stdout):
            try:
                stream.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()
    _READERS.clear()
    _BUFFERS.clear()


def _more(git_dir, proc):
    """Pull the next chunk from the reader's pipe into its buffer, waiting at
    most _READ_TIMEOUT_S for it. False on a stall, a closed pipe or an error,
    which the caller turns into no object."""
    try:
        ready, _w, _x = select.select([proc.stdout], [], [], _READ_TIMEOUT_S)
        if not ready:
            return False
        chunk = os.read(proc.stdout.fileno(), 65536)
    except (OSError, ValueError):
        return False
    if not chunk:
        return False
    _BUFFERS[git_dir] = _BUFFERS.get(git_dir, b"") + chunk
    return True


def _read_line(git_dir, proc):
    """One line from the reader without its newline, or None."""
    while b"\n" not in _BUFFERS.get(git_dir, b""):
        if not _more(git_dir, proc):
            return None
    line, _nl, rest = _BUFFERS[git_dir].partition(b"\n")
    _BUFFERS[git_dir] = rest
    return line


def _read_exact(git_dir, proc, size):
    """Exactly size bytes from the reader, or None."""
    while len(_BUFFERS.get(git_dir, b"")) < size:
        if not _more(git_dir, proc):
            return None
    data = _BUFFERS[git_dir][:size]
    _BUFFERS[git_dir] = _BUFFERS[git_dir][size:]
    return data


atexit.register(_close_readers)


def _reader(git_dir):
    """The live reader for git_dir, started when there is none, or None when
    git cannot start. --git-dir names the worktree's own directory and git
    resolves the common directory (objects, packs, refs) from it."""
    proc = _READERS.get(git_dir)
    if proc is not None and proc.poll() is None:
        return proc
    try:
        proc = subprocess.Popen(["git", "--git-dir=" + git_dir, "cat-file", "--batch"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=_git_env())
    except (OSError, ValueError):
        return None
    _READERS[git_dir] = proc
    return proc


def _drop_reader(git_dir):
    """Kill and reap the reader for git_dir; a stalled or malformed answer
    never leaves a process behind."""
    _BUFFERS.pop(git_dir, None)
    proc = _READERS.pop(git_dir, None)
    if proc is not None:
        proc.kill()
        proc.wait()


def _read_object(git_dir, sha):
    """(kind, content bytes) for one object, loose or packed, read through
    git; (None, None) when git has no such object, cannot start, exits,
    stalls past _READ_TIMEOUT_S, or answers in any shape other than
    `<sha> <kind> <size>` plus size bytes."""
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        return None, None
    if not isinstance(git_dir, str) or not git_dir:
        return None, None
    proc = _reader(git_dir)
    if proc is None:
        return None, None
    try:
        proc.stdin.write(sha.encode("ascii") + b"\n")
        proc.stdin.flush()
    except (OSError, ValueError):
        _drop_reader(git_dir)
        return None, None
    header = _read_line(git_dir, proc)
    if header is None:
        _drop_reader(git_dir)
        return None, None
    parts = header.split()
    if len(parts) == 2 and parts[0] == sha.encode("ascii") and parts[1] == b"missing":
        return None, None
    if len(parts) != 3 or parts[0] != sha.encode("ascii") or not parts[2].isdigit():
        _drop_reader(git_dir)
        return None, None
    size = int(parts[2])
    body = _read_exact(git_dir, proc, size + 1)
    if body is None or body[-1:] != b"\n":
        _drop_reader(git_dir)
        return None, None
    try:
        kind = parts[1].decode("ascii")
    except UnicodeDecodeError:
        _drop_reader(git_dir)
        return None, None
    return kind, body[:-1]


def _head_sha(root):
    git_dir = _git_dir(root)
    if git_dir is None:
        return None
    try:
        with open(os.path.join(git_dir, "HEAD"), "rb") as fh:
            raw = fh.read(256)
    except OSError:
        return None
    try:
        head = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if _SHA_RE.match(head):
        return head
    if head.startswith("ref:"):
        return _resolve_ref(git_dir, head[4:].strip())
    return None


def _branch(git_dir):
    try:
        with open(os.path.join(git_dir, "HEAD"), "rb") as fh:
            raw = fh.read(256)
    except OSError:
        return None
    try:
        head = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):]
    return None


def _peel_to_commit(git_dir, sha):
    """Follow tag objects until a commit, up to a small bound, or None."""
    for _step in range(8):
        kind, content = _read_object(git_dir, sha)
        if kind == "commit":
            return sha
        if kind != "tag":
            return None
        found = re.search(rb"^object ([0-9a-f]{40})$", content, re.M)
        if not found:
            return None
        sha = found.group(1).decode("ascii")
    return None


def _git(git_dir, args):
    """One git command against git_dir through the runner seam: (exit, stdout)."""
    proc = _run(["git", "--git-dir=" + git_dir] + list(args), git_dir)
    return proc.returncode, proc.stdout or ""


def _note_cut(text):
    found = _CUT_LINE_RE.search(text)
    return found.group(1) if found else None


def _notes_at(git_dir, commit):
    """{version tuple: (path, blob, cut sha or None)} for every
    docs/releases/x.y.z.md in the tree at commit, read through the object
    reader; None when the commit, its tree, any subtree or any note cannot
    be read (never an empty dict: unreadable is not absent)."""
    notes = {}
    tree = _committed_tree(git_dir, commit) if commit else None
    if not tree:
        return None
    try:
        for path, _mode, blob in _tree_entries(git_dir, tree):
            head, _sep, name = path.rpartition("/")
            found = _NOTE_NAME_RE.match(name)
            if head != "docs/releases" or not found:
                continue
            kind, content = _read_object(git_dir, blob)
            if kind != "blob":
                return None
            cut = _note_cut(content.decode("utf-8", "replace"))
            notes[tuple(int(x) for x in found.groups())] = (path, blob, cut)
    except NoDataError:
        return None
    return notes


def _full_commit(git_dir, name):
    code, out = _git(git_dir, ["rev-parse", "--verify", "--quiet", name + "^{commit}"])
    full = out.strip()
    return full if code == 0 and _SHA_RE.match(full) else None


def release_tags(git_dir):
    """[(version tuple, tag name)] of every public release tag, lowest
    first by version (never by text: v1.0.9 sorts below v1.0.21), the
    retired 2.x and 3.x line left out; None when git does not answer."""
    code, out = _git(git_dir, ["for-each-ref", "--format=%(refname)", "refs/tags/"])
    if code != 0:
        return None
    tags = []
    for line in out.splitlines():
        name = line.strip()[len("refs/tags/"):]
        found = _RELEASE_TAG_RE.match(name)
        if found:
            version = tuple(int(part) for part in found.groups())
            if version[0] not in RETIRED_MAJORS:
                tags.append((version, name))
    return sorted(tags)


def derived_base_tag(git_dir):
    """The highest public release tag, or None when there is none or git
    does not answer. Never a constant: the base moves when a release is cut."""
    tags = release_tags(git_dir)
    return tags[-1][1] if tags else None


def release_base(git_dir, base_tag):
    """(hub cut sha, source, base version) from the newest release note that
    names a hub cut commit, read ONLY through the object reader at the base
    tag's tree and then at the tree of the hub cut commit that note names;
    never from the candidate's tree, so no file the candidate adds or edits
    can decide the base. (None, reason, None) when the tag does not resolve,
    no note names a cut, or the sha is unknown to git."""
    ref = _resolve_ref(git_dir, "refs/tags/" + base_tag)
    tag_commit = _peel_to_commit(git_dir, ref) if ref else None
    if tag_commit is None:
        return None, "the base tag %s does not resolve to a commit" % base_tag, None
    at_tag = _notes_at(git_dir, tag_commit)
    if at_tag is None:
        return None, "the release notes at %s could not be read" % base_tag, None
    notes = dict((key, (path, cut, "%s at %s" % (path, base_tag)))
                 for key, (path, _blob, cut) in at_tag.items() if cut)
    if not notes:
        return None, "no release note at %s names a hub cut commit" % base_tag, None
    key = max(notes)
    _path, cut, source = notes[key]
    hub_cut = _full_commit(git_dir, cut)
    if hub_cut is None:
        return None, "the cut commit %s named by %s is unknown to git" % (cut, source), None
    at_hub = _notes_at(git_dir, hub_cut)
    if at_hub is None:
        return None, "the release notes at the hub cut %s could not be read" % hub_cut[:12], None
    for later, (path, _blob, later_cut) in at_hub.items():
        if later > key and later_cut:
            full = _full_commit(git_dir, later_cut)
            if full is None:
                return None, ("the cut commit %s named by %s at the hub cut %s is unknown to git"
                              % (later_cut, path, hub_cut[:12])), None
            key, hub_cut, source = later, full, "%s at the hub cut %s" % (path, hub_cut[:12])
    return hub_cut, source, key


def _blob_at(git_dir, commit, path):
    """The content bytes of path in the tree at commit, or None."""
    code, out = _git(git_dir, ["rev-parse", "--verify", "--quiet", "%s:%s" % (commit, path)])
    blob = out.strip()
    if code != 0 or not _SHA_RE.match(blob):
        return None
    kind, content = _read_object(git_dir, blob)
    return content if kind == "blob" else None


def cut_version(git_dir, sha):
    """The umbrella version the candidate declares (metadata.version in
    .claude-plugin/marketplace.json at sha) as a tuple, or None."""
    content = _blob_at(git_dir, sha, ".claude-plugin/marketplace.json")
    if content is None:
        return None
    try:
        data = json.loads(content.decode("utf-8"))
        version = data["metadata"]["version"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
    found = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version if isinstance(version, str) else "")
    return tuple(int(x) for x in found.groups()) if found else None


def note_drift(git_dir, sha, base_tag, hub_cut, base_version):
    """(verdict, detail) over the candidate's docs/releases/x.y.z.md at sha
    against the two anchor trees (the base tag's commit and the hub cut).
    A note equal to an anchor copy is fine. The note for the version being
    cut (the one .claude-plugin/marketplace.json declares) may exist but must
    name no cut commit: the cut writes that line itself. Any other added
    note, and any edited note whose cut line differs from the anchor copy,
    is FAIL naming it. An edited note whose cut line is unchanged is listed,
    not failed: the cut line is the only thing this reader reads. NO-DATA
    when the candidate declares no readable version."""
    ref = _resolve_ref(git_dir, "refs/tags/" + base_tag)
    tag_commit = _peel_to_commit(git_dir, ref) if ref else None
    anchors = {}
    for commit in (tag_commit, hub_cut):
        at_commit = _notes_at(git_dir, commit)
        if at_commit is None:
            return NO_DATA, "the release notes at %s could not be read" % (commit or base_tag)[:12]
        for key, (path, blob, cut) in at_commit.items():
            anchors.setdefault(key, []).append((blob, cut))
    declared = cut_version(git_dir, sha)
    if declared is None:
        return NO_DATA, "the candidate declares no readable metadata.version in .claude-plugin/marketplace.json"
    at_sha = _notes_at(git_dir, sha)
    if at_sha is None:
        return NO_DATA, "the candidate's release notes at %s could not be read" % sha[:12]
    failed, edited, cutting = [], [], []
    for key, (path, blob, cut) in sorted(at_sha.items()):
        copies = anchors.get(key)
        if copies and any(blob == known for known, _cut in copies):
            continue
        if copies is None:
            if key == declared and cut is None:
                cutting.append(path)
            elif key == declared:
                failed.append("%s names a cut commit before the cut" % path)
            else:
                failed.append("%s is not at %s or the hub cut" % (path, base_tag))
        elif cut not in [known_cut for _blob, known_cut in copies]:
            failed.append("%s names %s, the anchor copy does not" % (path, cut))
        else:
            edited.append(path)
    if failed:
        return FAIL, "release note(s) the candidate may not decide: %s" % "; ".join(failed)
    return PASS, ("base %s, cutting %s; note for the cut: %s; edited since the anchors with the "
                  "same cut line: %s"
                  % (".".join(str(x) for x in base_version), ".".join(str(x) for x in declared),
                     ", ".join(cutting) or "none", ", ".join(edited) or "none"))


def _normalised(path, line):
    text = _VERSION_STRING_RE.sub("<v>", line)
    if _GENERATED_MANIFEST_RE.match(path):
        text = _MANIFEST_NOISE_RE.sub("<h>", text)
    return text


def _version_step(old_line, new_line, target):
    """None when the only difference between a paired line is a version
    string moving up to target; else the reason."""
    before = _VERSION_STRING_RE.findall(old_line)
    after = _VERSION_STRING_RE.findall(new_line)
    for old, new in zip(before, after):
        if old == new:
            continue
        if new != target:
            return "moves a version to %s, not the %s the subject names" % (new, target)
        if tuple(int(x) for x in new.split(".")) <= tuple(int(x) for x in old.split(".")):
            return "moves a version down or sideways from %s to %s" % (old, new)
    return None


_CHECKSUMS_RE = re.compile(r"^products/[^/]+/CHECKSUMS\.sha256$")
_CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  (\S.*)$")
_JSON_MANIFEST_RE = re.compile(
    r"^(?:bundle/runtime/RUNTIME-MANIFEST\.json|bundle/runtime/hooks/HOOKS-MANIFEST\.json)$")


def _json_manifest_regenerated(git_dir, commit, manifest):
    """None when every `files[].sha256` of a runtime or hooks manifest at
    commit is the sha256 of `files[].path` (relative to the manifest's own
    directory) as it stands in the tree at commit; else the first offending
    entry. The digests are noise in the paired-line rule, so they are
    checked against the tree here instead of normalised away."""
    content = _blob_at(git_dir, commit, manifest)
    if content is None:
        return "the manifest could not be read at %s" % commit[:12]
    try:
        data = json.loads(content.decode("utf-8"))
        entries = data["files"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return "the manifest at %s is not a JSON object with a files list" % commit[:12]
    if not isinstance(entries, list):
        return "the manifest at %s holds no files list" % commit[:12]
    base = manifest.rsplit("/", 1)[0]
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) \
                or not isinstance(entry.get("sha256"), str):
            return "unreadable files entry %r" % (entry,)
        blob = _blob_at(git_dir, commit, "%s/%s" % (base, entry["path"]))
        if blob is None:
            return "%s names %s, absent at %s" % (entry["sha256"][:12], entry["path"], commit[:12])
        if hashlib.sha256(blob).hexdigest() != entry["sha256"]:
            return "%s is not the sha256 of %s at %s" % (entry["sha256"][:12], entry["path"], commit[:12])
    return None


def _checksums_regenerated(git_dir, commit, manifest, removed, added):
    """None when every added line of a product CHECKSUMS.sha256 carries the
    sha256 of that file as it stands in the tree at commit, and every line
    removed outright names a file absent there; else the first offending
    line. A manifest the script regenerates can gain or lose entries for
    files the bump never touched, so the lines are checked against the tree
    rather than paired."""
    product = manifest.rsplit("/", 1)[0]
    added_paths = set()
    for line in added:
        found = _CHECKSUM_LINE_RE.match(line)
        if not found:
            return "unreadable line %r" % line[:80]
        digest, rel = found.groups()
        added_paths.add(rel)
        code, out = _git(git_dir, ["rev-parse", "--verify", "--quiet",
                                   "%s:%s/%s" % (commit, product, rel)])
        blob = out.strip()
        kind, content = _read_object(git_dir, blob) if code == 0 and _SHA_RE.match(blob) else (None, None)
        if kind != "blob":
            return "%s names %s, absent at %s" % (line[:80], rel, commit[:12])
        if hashlib.sha256(content).hexdigest() != digest:
            return "%s is not the sha256 of %s at %s" % (line[:80], rel, commit[:12])
    for line in removed:
        found = _CHECKSUM_LINE_RE.match(line)
        if not found:
            return "unreadable line %r" % line[:80]
        rel = found.group(2)
        if rel in added_paths:
            continue
        code, _out = _git(git_dir, ["cat-file", "-e", "%s:%s/%s" % (commit, product, rel)])
        if code == 0:
            return "%s drops %s, which still exists at %s" % (line[:80], rel, commit[:12])
    return None


def _bump_exempt(git_dir, commit):
    """(True, "") when commit is a release's own version bump: subject
    "<x.y.z>: the version bump", one parent, every touched path a version
    carrier or generated manifest, and in every file the removed lines equal
    the added lines once version strings (and, in a generated manifest, the
    digests, revision and describe string) are normalised, so nothing else
    is added or removed. (False, reason naming the path and the first
    offending line) otherwise, (None, reason) when git did not answer."""
    code, out = _git(git_dir, ["log", "-1", "--format=%s%n%P", commit])
    lines = out.splitlines()
    if code != 0 or len(lines) < 2:
        return None, "git log -1 %s did not answer" % commit[:12]
    subject = _BUMP_SUBJECT_RE.match(lines[0])
    if not subject:
        return False, "subject is not a version bump"
    target = subject.group(1)
    if len(lines[1].split()) != 1:
        return False, "a merge commit is never a version bump"
    code, out = _git(git_dir, ["diff", "--no-color", "--unified=0", "--no-renames",
                               commit + "^", commit])
    if code != 0:
        return None, "git diff %s^ %s did not answer" % (commit[:12], commit[:12])
    path = None
    removed, added = {}, {}
    for line in out.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[1] if " b/" in line else None
            if path is None or not _VERSION_CARRIER_RE.match(path):
                return False, "touches %s, not a version carrier" % (path or line[:80])
            removed.setdefault(path, [])
            added.setdefault(path, [])
        elif line.startswith("--- ") or line.startswith("+++ ") or line.startswith("@@"):
            continue
        elif line.startswith("-") and path:
            removed[path].append(line[1:])
        elif line.startswith("+") and path:
            added[path].append(line[1:])
        elif line.startswith("index ") or line.startswith("\\ No newline"):
            continue
        else:
            return False, "unreadable diff line %r" % line[:80]
    if not removed:
        return False, "changes no file"
    for path in removed:
        if _CHECKSUMS_RE.match(path):
            # a regenerated manifest may gain or lose entries for files the
            # bump never touched; each line is checked against the tree
            # instead: an added line carries the sha256 of the file at this
            # commit, a line removed outright names a file absent there
            problem = _checksums_regenerated(git_dir, commit, path, removed[path], added[path])
            if problem:
                return False, "%s is not a regeneration: %s" % (path, problem)
            continue
        if _JSON_MANIFEST_RE.match(path):
            problem = _json_manifest_regenerated(git_dir, commit, path)
            if problem:
                return False, "%s is not a regeneration: %s" % (path, problem)
        old = [_normalised(path, l) for l in removed[path]]
        new = [_normalised(path, l) for l in added[path]]
        for index in range(max(len(old), len(new))):
            offending = (added[path][index] if index < len(new) else removed[path][index])
            if _PLUGIN_JSON_RE.match(path) and not _VERSION_FIELD_RE.match(offending):
                return False, "%s changes a field that is not version or ref at %r" % (path, offending[:80])
            if index >= len(old) or index >= len(new) or old[index] != new[index]:
                return False, "%s changes more than a version string at %r" % (path, offending[:80])
            problem = _version_step(removed[path][index], added[path][index], target)
            if problem:
                return False, "%s %s at %r" % (path, problem, added[path][index][:80])
    return True, ""


def release_side(git_dir, hub_cut, sha):
    """(verdict, detail, shared base): PASS when every commit on the release
    side of the shared base (git merge-base hub_cut sha, then
    rev-list shared..hub_cut) is on the candidate by patch identity (git
    cherry, as the parking triage reads it) or is the release's own version
    bump; FAIL naming each commit that is neither, or when the two share no
    history; NO-DATA when git does not answer or prints a line that is not a
    cherry mark."""
    code, out = _git(git_dir, ["merge-base", hub_cut, sha])
    shared = out.strip()
    if code == 1 and not shared:
        return FAIL, "the candidate %s shares no history with the hub cut %s" % (sha, hub_cut), None
    if code != 0 or not _SHA_RE.match(shared):
        return NO_DATA, "git merge-base %s %s did not answer: exit %d" % (hub_cut, sha, code), None
    code, out = _git(git_dir, ["cherry", sha, hub_cut, shared])
    if code != 0:
        return NO_DATA, "git cherry did not answer: exit %d" % code, shared
    present, exempt, refused = [], [], []
    for line in out.splitlines():
        found = _CHERRY_LINE_RE.match(line)
        if not found:
            return NO_DATA, "unreadable git cherry line: %r" % line[:120], shared
        mark, commit = found.groups()
        if mark == "-":
            present.append(commit)
            continue
        bump, reason = _bump_exempt(git_dir, commit)
        if bump is None:
            return NO_DATA, "the release-side commit %s could not be read: %s" % (commit, reason), shared
        if bump:
            exempt.append(commit)
        else:
            refused.append("%s (%s)" % (commit[:12], reason))
    summary = ("shared base %s; release side %d commit(s): %d present by patch id, %d version bump"
               % (shared[:12], len(present) + len(exempt) + len(refused), len(present), len(exempt)))
    if refused:
        return FAIL, ("%s; not on the candidate by patch id and not a version bump: %s"
                      % (summary, "; ".join(refused))), shared
    return PASS, summary, shared


JUDGE_MODULE = "scripts/precut_review.py"


def judge_anchor(git_dir, hub_cut, sha):
    """(commit, source): the commit whose copy of the judge (this module and
    every JUDGE_FILES entry) judges the candidate. The release base when it
    carries the judge, so a change to the judge is judged by the previous
    judge. Bootstrap, stated rather than pretended: while no release base
    carries the judge (1.1.0 is the first cut with one), the judge is the
    candidate's own at sha, recorded in the freeze with its blob shas so the
    owner's ruling records can name exactly what judged."""
    code, _out = _git(git_dir, ["cat-file", "-e", "%s:%s" % (hub_cut, JUDGE_MODULE)])
    if code == 0:
        return hub_cut, "the release base"
    # bootstrap ends at the first cut that carries the judge: once a release
    # tag at or past that version exists, the candidate's own declaration
    # can never buy it back
    tags = release_tags(git_dir)
    if tags is None:
        return None, "the release tags could not be listed, so bootstrap cannot be ruled out"
    cut = [name for version, name in tags if version >= BOOTSTRAP_VERSION]
    if cut:
        return None, ("no %s at the release base %s and the release tag(s) %s exist: bootstrap "
                      "is over, a judge must come from a release base"
                      % (JUDGE_MODULE, hub_cut[:12], ", ".join(cut)))
    declared = cut_version(git_dir, sha)
    if declared != BOOTSTRAP_VERSION:
        return None, ("no %s at the release base %s and the candidate declares %s, not the "
                      "bootstrap version %s: nothing may judge it"
                      % (JUDGE_MODULE, hub_cut[:12],
                         ".".join(str(x) for x in declared) if declared else "no version",
                         ".".join(str(x) for x in BOOTSTRAP_VERSION)))
    return sha, ("bootstrap: no %s at the release base %s, the candidate's own judge at %s"
                 % (JUDGE_MODULE, hub_cut[:12], sha[:12]))


def judge_files_at(git_dir, commit):
    """{path: blob sha} for every JUDGE_FILES entry present at commit, or
    None when git does not answer."""
    code, out = _git(git_dir, ["ls-tree", "-r", "-z", commit, "--"] + list(JUDGE_FILES))
    if code != 0:
        return None
    files = {}
    for line in out.split("\0"):
        if not line:
            continue
        meta, _tab, path = line.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not _SHA_RE.match(parts[2]) or not path:
            return None
        files[path] = parts[2]
    return files


def _tree_entries(git_dir, tree_sha, prefix=""):
    """Every (path, mode, sha) under tree_sha, recursively. A tree that
    cannot be read, or reads malformed, raises NoDataError: an unreadable
    subtree is never an empty one (that read a FAIL as a PASS once)."""
    kind, content = _read_object(git_dir, tree_sha)
    if kind != "tree":
        raise NoDataError("the tree %s at %s could not be read" % (tree_sha[:12], prefix or "/"))
    pos = 0
    while pos < len(content):
        space = content.find(b" ", pos)
        if space < 0:
            raise NoDataError("the tree %s at %s is malformed" % (tree_sha[:12], prefix or "/"))
        mode = content[pos:space].decode("ascii", "replace")
        nul = content.find(b"\x00", space)
        if nul < 0:
            raise NoDataError("the tree %s at %s is malformed" % (tree_sha[:12], prefix or "/"))
        name = content[space + 1:nul].decode("utf-8", "replace")
        sha = content[nul + 1:nul + 21].hex()
        pos = nul + 21
        full = prefix + name
        if mode in ("40000", "040000"):
            for item in _tree_entries(git_dir, sha, full + "/"):
                yield item
        else:
            yield (full, mode, sha)


def _blob_sha(data):
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def _committed_tree(git_dir, commit_sha):
    kind, content = _read_object(git_dir, commit_sha)
    if kind != "commit":
        return None
    found = re.search(rb"^tree ([0-9a-f]{40})$", content, re.M)
    if not found:
        return None
    return found.group(1).decode("ascii")


def _dirty(root):
    """True when the working tree differs from HEAD, or HEAD is unreadable."""
    git_dir = _git_dir(root)
    if git_dir is None:
        return True
    head = _head_sha(root)
    if head is None:
        return True
    tree_sha = _committed_tree(git_dir, head)
    if tree_sha is None:
        return True
    committed = {}
    try:
        for path, _mode, sha in _tree_entries(git_dir, tree_sha):
            committed[path] = sha
    except NoDataError:
        return True
    working = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath == root:
            # a worktree's .git is a pointer file, not a directory
            filenames = [n for n in filenames if n != ".git"]
        # a symlink to a directory is a committed entry (mode 120000), not a
        # directory to descend into
        linked = [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and d not in linked)
        for name in filenames + linked:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            working[rel] = full
    if set(working) != set(committed):
        return True
    for rel, full in working.items():
        try:
            if os.path.islink(full):
                data = os.fsencode(os.readlink(full))
            else:
                with open(full, "rb") as fh:
                    data = fh.read()
        except OSError:
            return True
        if _blob_sha(data) != committed[rel]:
            return True
    return False


def _load_freeze(rec_dir):
    if not isinstance(rec_dir, str) or not rec_dir:
        return None
    path = os.path.join(rec_dir, "freeze.json")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def frozen_sha(rec_dir):
    """The frozen 40 hex sha, or None when no readable record exists."""
    if not isinstance(rec_dir, str) or not rec_dir:
        return None
    record = _load_freeze(rec_dir)
    if record is None:
        return None
    sha = record.get("sha")
    if isinstance(sha, str) and _SHA_RE.match(sha):
        return sha
    return None


def freeze(root, rec_dir, base_tag=None):
    """Write the candidate sha once, atomically, and refuse anything else.
    base_tag None means the derived one: the highest public release tag."""
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string, got %r" % (root,))
    if not isinstance(rec_dir, str) or not rec_dir:
        raise ValueError("rec_dir must be a non-empty string, got %r" % (rec_dir,))
    if base_tag is not None and (not isinstance(base_tag, str) or not base_tag):
        raise ValueError("base_tag must be a non-empty string, got %r" % (base_tag,))
    git_dir = _git_dir(root)
    if git_dir is None:
        raise ValueError("no git repository at %s" % root)
    if base_tag is None:
        base_tag = derived_base_tag(git_dir)
        if base_tag is None:
            raise NoDataError("NO-DATA: no public release tag vX.Y.Z could be read in %s" % root)
    sha = _head_sha(root)
    if sha is None:
        raise ValueError("HEAD does not resolve in %s" % root)
    if _dirty(root):
        raise ValueError("the working tree at %s is dirty" % root)
    ref = _resolve_ref(git_dir, "refs/tags/" + base_tag)
    if ref is None:
        raise ValueError("base tag %s does not resolve in %s" % (base_tag, root))
    base_commit = _peel_to_commit(git_dir, ref)
    if base_commit != BASE_COMMIT:
        raise ValueError(
            "base tag %s resolves to %s, not the recorded provenance commit %s"
            % (base_tag, base_commit, BASE_COMMIT))
    hub_cut, source, base_version = release_base(git_dir, base_tag)
    if hub_cut is None:
        raise NoDataError("NO-DATA: %s" % source)
    verdict, detail = note_drift(git_dir, sha, base_tag, hub_cut, base_version)
    if verdict == NO_DATA:
        raise NoDataError("NO-DATA: %s" % detail)
    if verdict != PASS:
        raise ValueError(detail)
    verdict, detail, shared = release_side(git_dir, hub_cut, sha)
    if verdict == NO_DATA:
        raise NoDataError("NO-DATA: %s" % detail)
    if verdict != PASS:
        raise ValueError("release base %s (%s): %s" % (hub_cut, source, detail))
    judge, judge_source = judge_anchor(git_dir, hub_cut, sha)
    if judge is None:
        raise NoDataError("NO-DATA: %s" % judge_source)
    judge_files = judge_files_at(git_dir, judge)
    if judge_files is None:
        raise NoDataError("NO-DATA: the judge files at %s could not be listed" % judge[:12])
    record = {
        "sha": sha,
        "tree_sha": _committed_tree(git_dir, sha),
        "branch": _branch(git_dir),
        "base_tag": base_tag,
        "base_commit": BASE_COMMIT,
        "release_base": hub_cut,
        "release_note": source,
        "shared_base": shared,
        "judge": judge,
        "judge_source": judge_source,
        "judge_files": judge_files,
        "dir": os.path.abspath(rec_dir),
        "written_at": _now(),
    }
    os.makedirs(rec_dir, exist_ok=True)
    path = os.path.join(rec_dir, "freeze.json")
    blob = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise FileExistsError(
            "a freeze already exists for %s at %s"
            % (frozen_sha(rec_dir) or "unknown", path))
    try:
        os.write(handle, blob)
    finally:
        os.close(handle)
    return record


def verify_freeze(root, rec_dir):
    """(verdict, detail): PASS only when HEAD equals the recorded sha and the
    tree is clean, FAIL when HEAD moved, the tree is dirty, the base tag
    moved, the release base moved or a release-side commit is missing from
    the candidate, and NO-DATA when no readable record exists, no release
    note names a cut, or HEAD cannot be resolved at all (a git that is
    missing or broken)."""
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string, got %r" % (root,))
    if not isinstance(rec_dir, str) or not rec_dir:
        raise ValueError("rec_dir must be a non-empty string, got %r" % (rec_dir,))
    record = _load_freeze(rec_dir)
    if record is None:
        return NO_DATA, "no readable freeze record under %s" % rec_dir
    sha = record.get("sha")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        return NO_DATA, "the freeze record carries no 40 hex sha"
    recorded_dir = record.get("dir")
    if isinstance(recorded_dir, str) and os.path.abspath(rec_dir) != recorded_dir:
        return FAIL, ("the record directory %s differs from the recorded one %s"
                      % (os.path.abspath(rec_dir), recorded_dir))
    git_dir = _git_dir(root)
    if git_dir is None:
        return FAIL, "no git repository at %s" % root
    head = _head_sha(root)
    if head is None:
        return NO_DATA, "HEAD does not resolve through git in %s" % root
    if head != sha:
        return FAIL, "HEAD is %s, the freeze records %s" % (head, sha)
    base_tag = record.get("base_tag")
    base_commit = record.get("base_commit")
    if not isinstance(base_tag, str) or not base_tag:
        return NO_DATA, "the freeze record carries no base tag"
    if not isinstance(base_commit, str) or not _SHA_RE.match(base_commit):
        return NO_DATA, "the freeze record carries no base commit"
    ref = _resolve_ref(git_dir, "refs/tags/" + base_tag)
    if ref is None:
        return FAIL, "base tag %s no longer resolves" % base_tag
    moved = _peel_to_commit(git_dir, ref)
    if moved != base_commit:
        return FAIL, ("base tag %s resolves to %s, the freeze records %s"
                      % (base_tag, moved, base_commit))
    recorded_base = record.get("release_base")
    if not isinstance(recorded_base, str) or not _SHA_RE.match(recorded_base):
        return NO_DATA, "the freeze record carries no release base"
    hub_cut, source, base_version = release_base(git_dir, base_tag)
    if hub_cut is None:
        return NO_DATA, source
    if hub_cut != recorded_base:
        return FAIL, ("the release base is %s (%s), the freeze records %s"
                      % (hub_cut, source, recorded_base))
    verdict, detail = note_drift(git_dir, sha, base_tag, hub_cut, base_version)
    if verdict != PASS:
        return verdict, detail
    verdict, detail, _shared = release_side(git_dir, hub_cut, sha)
    if verdict != PASS:
        return verdict, "release base %s: %s" % (hub_cut, detail)
    judge, judge_source = judge_anchor(git_dir, hub_cut, sha)
    if judge is None:
        return NO_DATA, judge_source
    if judge != record.get("judge"):
        return FAIL, "the judge anchor is %s, the freeze records %s" % (judge, record.get("judge"))
    if judge_files_at(git_dir, judge) != record.get("judge_files"):
        return FAIL, "the judge files at %s are not the ones the freeze records" % judge[:12]
    if _dirty(root):
        return FAIL, "the tree is dirty, so the freeze is invalid"
    return PASS, "HEAD is %s and the tree is clean%s" % (
        sha, "; judged by the candidate's own code: bootstrap" if judge == sha else
        "; judged by the release base %s" % judge[:12])


def record_result(rec_dir, check, verdict, detail, judged_by=None):
    """Write one result file per check atomically, keyed to the frozen sha;
    judged_by marks the judge commit whose copy recorded it."""
    if not isinstance(rec_dir, str) or not rec_dir:
        raise ValueError("rec_dir must be a non-empty string, got %r" % (rec_dir,))
    if not isinstance(check, str) or not _CHECK_RE.match(check):
        raise ValueError("check must be a short name, got %r" % (check,))
    if not isinstance(verdict, str) or verdict not in (PASS, FAIL, NO_DATA):
        raise ValueError("verdict must be one of PASS, FAIL, NO-DATA, got %r" % (verdict,))
    if not isinstance(detail, str):
        raise ValueError("detail must be a string, got %r" % (detail,))
    sha = frozen_sha(rec_dir)
    if sha is None:
        raise ValueError("no freeze record under %s; refusing to record a result" % rec_dir)
    out_dir = os.path.join(rec_dir, "results")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, check + ".json")
    row = {"check": check, "sha": sha, "verdict": verdict, "detail": detail, "at": _now()}
    if judged_by is not None:
        row["judged_by"] = judged_by
    blob = json.dumps(row, indent=2, sort_keys=True).encode("utf-8")
    handle, temp = tempfile.mkstemp(dir=out_dir, prefix="." + check + ".", suffix=".tmp")
    try:
        os.write(handle, blob)
    finally:
        os.close(handle)
    os.replace(temp, path)
    return path


def read_result(rec_dir, check):
    """(verdict, detail): NO-DATA when missing, FAIL when stale or when the
    stored verdict is not one of PASS, FAIL, NO-DATA."""
    if not isinstance(rec_dir, str) or not rec_dir:
        raise ValueError("rec_dir must be a non-empty string, got %r" % (rec_dir,))
    if not isinstance(check, str) or not _CHECK_RE.match(check):
        raise ValueError("check must be a short name, got %r" % (check,))
    path = os.path.join(rec_dir, "results", check + ".json")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return NO_DATA, "no result recorded for %s" % check
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return NO_DATA, "the result for %s is unreadable" % check
    if not isinstance(record, dict):
        return NO_DATA, "the result for %s is unreadable" % check
    sha = frozen_sha(rec_dir)
    if sha is None:
        return NO_DATA, "no freeze record under %s" % rec_dir
    if record.get("sha") != sha:
        return FAIL, ("the result for %s is stale: stored %s, frozen %s"
                      % (check, record.get("sha"), sha))
    verdict = record.get("verdict")
    if verdict not in (PASS, FAIL, NO_DATA):
        return FAIL, "the result for %s carries verdict %r" % (check, verdict)
    detail = record.get("detail")
    return verdict, detail if isinstance(detail, str) else ""


def _replace_record(rec_dir):
    """Move the old freeze aside as a sha named copy and clear every result."""
    if not isinstance(rec_dir, str) or not rec_dir:
        raise ValueError("rec_dir must be a non-empty string, got %r" % (rec_dir,))
    path = os.path.join(rec_dir, "freeze.json")
    if not os.path.isfile(path):
        return
    old = frozen_sha(rec_dir) or "unknown"
    os.replace(path, os.path.join(rec_dir, "freeze.json." + old))
    for name in ("brief.txt", "review.json", JUDGE_REVIEW):
        extra = os.path.join(rec_dir, name)
        if os.path.exists(extra):
            os.remove(extra)
    results = os.path.join(rec_dir, "results")
    if os.path.isdir(results):
        shutil.rmtree(results)


#: Every Python child of the judge: isolated, so neither its working
#: directory, nor PYTHONPATH, nor a user site directory is on its import
#: path. Measured 2026-10-04: `python -c` put the candidate worktree (its
#: cwd) first, so a candidate module named like a stdlib module sbe_score
#: imports ran inside the judge.
PYTHON = (sys.executable, "-I")


def _run(cmd, cwd, runner=None, env=None, timeout=None, stdin_text=None):
    """One subprocess, never raising: a missing binary reads as exit 127, a
    timeout as exit 124, undecodable output as exit 126. stdin_text, when
    given, is the child's whole stdin; otherwise the child reads nothing."""
    runner = runner or subprocess.run
    extra = {"input": stdin_text} if stdin_text is not None else {"stdin": subprocess.DEVNULL}
    try:
        return runner(cmd, cwd=cwd, capture_output=True, text=True, env=_git_env(env),
                      timeout=timeout, **extra)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out after %ss" % timeout)
    except ValueError as exc:
        return subprocess.CompletedProcess(cmd, 126, "", "%s" % exc)


def _scratch_root():
    """BROTHER_SCRATCH when set and non empty, else ~/.claude/brother-scratch.
    Never the system temp folder."""
    custom = os.environ.get("BROTHER_SCRATCH", "").strip()
    return custom or os.path.join(os.path.expanduser("~"), ".claude", "brother-scratch")


def _fingerprint(root):
    """(HEAD sha, tree sha, porcelain text) of the main checkout, any of them
    None when it cannot be read. Read in process and through git status with
    no optional lock, so the reading never writes the index."""
    head = _head_sha(root)
    git_dir = _git_dir(root)
    tree = _committed_tree(git_dir, head) if (git_dir and head) else None
    proc = _run(["git", "--no-optional-locks", "status", "--porcelain"], root)
    porcelain = proc.stdout if proc.returncode == 0 else None
    return head, tree, porcelain


def _disturbed(root, sha, frozen):
    """None when the main checkout still reads as frozen, else the FAIL detail."""
    now = _fingerprint(root)
    if any(part is None for part in now):
        return "the main checkout could not be read during the pass"
    if now != frozen or now[0] != sha:
        return "tree changed during the pass"
    return None


def _candidate_env(home, rec_dir):
    """The real environment with HOME at a fresh empty directory and no
    variable naming the record directory."""
    marker = os.path.abspath(rec_dir)
    env = dict((k, v) for k, v in os.environ.items() if marker not in v)
    env["HOME"] = home
    return env


def _gate_row(worktree, judge_root, env, runner):
    """The judge's `sbe_gate.py . --strict` run in the worktree: exit 0 PASS
    unless a gate line prints NO-DATA or no gate line prints at all, exit 1
    FAIL, anything else NO-DATA. The script is the judge anchor's copy under
    judge_root; the worktree is only what it reads."""
    script = os.path.join("products", "brothersbe", "tools", "sbe_gate.py")
    if not os.path.isfile(os.path.join(judge_root, script)):
        return NO_DATA, "no %s in the judge" % script
    proc = _run(list(PYTHON) + [os.path.join(judge_root, script), ".", "--strict"], worktree,
                runner, env=env, timeout=ROW_TIMEOUT_S)
    gates = _GATE_LINE_RE.findall(proc.stdout or "")
    summary = "exit %d; gates: %s" % (
        proc.returncode, ", ".join("%s %s" % pair for pair in gates) or "none printed")
    if proc.returncode == 1:
        return FAIL, summary
    if proc.returncode != 0:
        return NO_DATA, summary
    if not gates:
        return NO_DATA, summary + " (an exit 0 that names no gate proves nothing)"
    if any(verdict == NO_DATA for _name, verdict in gates):
        return NO_DATA, summary + " (a gate printed NO-DATA, which is not a pass)"
    return PASS, summary


def _lint_run(outside, judge_root, env, runner, lint_root):
    """silent_failure_lints from the judge's sbe_score.py over lint_root,
    SBE_LINT_ROOT set to it, in an isolated process with a clean argv whose
    working directory is outside, never inside the candidate tree."""
    tools = os.path.join(judge_root, "products", "brothersbe", "tools")
    lint_env = dict(env)
    lint_env["SBE_LINT_ROOT"] = lint_root
    proc = _run(list(PYTHON) + ["-c", _LINT_SNIPPET % tools], outside, runner,
                env=lint_env, timeout=ROW_TIMEOUT_S)
    lines = (proc.stdout or "").splitlines()
    if proc.returncode != 0 or not lines or lines[0] not in (PASS, FAIL, NO_DATA):
        last = ((proc.stderr or "").strip().splitlines() or ["no output"])[-1]
        return NO_DATA, "the lint did not answer: exit %d, %s" % (proc.returncode, last[:200])
    return lines[0], " ".join(line.strip() for line in lines[1:]).strip()


def _changed_files(worktree, base_tag, sha, runner):
    """The paths that differ between the tag and the frozen sha, or None when
    git diff cannot answer or names a path outside the tree."""
    proc = _run(["git", "diff", "--name-only", "-z", base_tag, sha, "--"], worktree, runner)
    if proc.returncode != 0:
        return None
    paths = [p for p in (proc.stdout or "").split("\0") if p]
    for rel in paths:
        if rel.startswith("/") or ".." in rel.split("/"):
            return None
    return paths


def _lint_row(worktree, judge_root, env, base_tag, sha, runner):
    """Owner ruling 2026-10-04 (release-range lint): the row's verdict is the
    lint over a scratch copy of every file changed between the tag and the
    frozen sha; the lint over the whole tree runs too and its count is
    reported in the detail, never gated on. An unreadable range, a changed
    file that cannot be copied, or no scratch root is NO-DATA."""
    tools = os.path.join(judge_root, "products", "brothersbe", "tools")
    if not os.path.isfile(os.path.join(tools, "sbe_score.py")):
        return NO_DATA, "no products/brothersbe/tools/sbe_score.py in the judge"
    changed = _changed_files(worktree, base_tag, sha, runner)
    if changed is None:
        return NO_DATA, "the range %s..%s could not be read: git diff did not answer" % (base_tag, sha)
    # the lint names its tree through SBE_LINT_ROOT; it runs from the
    # directory holding the worktree, which is the pass's own scratch
    outside = os.path.dirname(os.path.abspath(worktree))
    whole_verdict, whole_detail = _lint_run(outside, judge_root, env, runner, worktree)
    scratch = _scratch_root()
    try:
        os.makedirs(scratch, exist_ok=True)
        copy = tempfile.mkdtemp(prefix="precut-lint-", dir=scratch)
    except OSError as exc:
        return NO_DATA, "no scratch root under %s: %s" % (scratch, exc)
    try:
        for rel in changed:
            src = os.path.join(worktree, rel)
            if os.path.islink(src) or not os.path.isfile(src):
                continue  # deleted in the range, or a link
            dst = os.path.join(copy, rel)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
            except OSError as exc:
                return NO_DATA, "changed file %s could not be copied for the lint: %s" % (rel, exc)
        verdict, detail = _lint_run(outside, judge_root, env, runner, copy)
    finally:
        shutil.rmtree(copy, True)
    return verdict, ("range %s..%s (%d changed file(s)): %s; whole tree %s (reported, not gated): %s"
                     % (base_tag, sha[:12], len(changed), detail, whole_verdict, whole_detail))


def waiver_rows(root, runner=None):
    """Every `sbe: allow-silent` marker in the working copy at root, as
    (path, line, reason), read with git grep -n. Raises ValueError when git
    grep cannot answer or prints a line it should not."""
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string, got %r" % (root,))
    proc = _run(["git", "grep", "-n", "-F", WAIVER_MARKER, "--", "."], root, runner)
    if proc.returncode == 1:
        return []
    if proc.returncode != 0:
        raise ValueError("git grep failed in %s: exit %d" % (root, proc.returncode))
    rows = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit() or WAIVER_MARKER not in parts[2]:
            raise ValueError("unreadable git grep line in %s: %r" % (root, line[:120]))
        reason = parts[2].split(WAIVER_MARKER, 1)[1].strip()
        rows.append((parts[0], int(parts[1]), reason))
    return rows


def _baseline_anchor(worktree, release_base, sha, runner):
    """(anchor commit, None) or (None, verdict, detail). The anchor is the
    release base when the baseline file exists there, else the pinned
    landing commit WAIVERS_BASELINE_LANDED, which must be an ancestor of the
    candidate; an edit of the file between the anchor and the candidate is
    FAIL naming the editing commit(s)."""
    if not isinstance(release_base, str) or not _SHA_RE.match(release_base):
        return None, NO_DATA, "the freeze record carries no release base"
    probe = _run(["git", "cat-file", "-e", "%s:%s" % (release_base, WAIVERS_BASELINE)],
                 worktree, runner)
    if probe.returncode == 0:
        anchor, origin = release_base, "the release base"
    else:
        anchor, origin = WAIVERS_BASELINE_LANDED, "the pinned landing commit"
        is_ancestor = _run(["git", "merge-base", "--is-ancestor", anchor, sha], worktree, runner)
        if is_ancestor.returncode != 0:
            return None, NO_DATA, ("no baseline at the release base %s and the pinned landing "
                                   "commit %s is not an ancestor of the candidate (git exit %d)"
                                   % (release_base[:12], anchor[:12], is_ancestor.returncode))
    edits = _run(["git", "log", "--format=%h", "%s..%s" % (anchor, sha), "--", WAIVERS_BASELINE],
                 worktree, runner)
    if edits.returncode != 0:
        return None, NO_DATA, "git log %s..%s could not be read: exit %d" % (
            anchor[:12], sha[:12], edits.returncode)
    editing = [line.strip() for line in (edits.stdout or "").splitlines() if line.strip()]
    if editing:
        return None, FAIL, ("%s was edited after %s %s by %s; a baseline is raised only by its "
                            "own reviewed commit, judged at the next cut"
                            % (WAIVERS_BASELINE, origin, anchor[:12], ", ".join(editing)))
    return anchor, None, origin


def _read_baseline(worktree, anchor, runner):
    """(count, taken at) from the reviewed baseline as it stands at the
    anchor commit, read through git and never from the worktree; None when
    the file is absent there, unreadable, or carries no non-negative count."""
    proc = _run(["git", "show", "%s:%s" % (anchor, WAIVERS_BASELINE)], worktree, runner)
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    count = data.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    taken = data.get("sha")
    return count, taken if isinstance(taken, str) else "unknown"


def _waivers_row(worktree, base_tag, sha, release_base, runner):
    """Owner ruling 2026-10-04 (reviewed baseline): FAIL on an empty or short
    reason, FAIL when the count rises above the reviewed baseline, PASS at or
    below it; every marker added since the tag is printed with path, line and
    reason either way. The baseline is read at its anchor commit (the release
    base, or the pinned landing commit while no release base carries the
    file), never from the candidate's worktree, and an edit of the file since
    that anchor is FAIL naming the commit. NO-DATA when the baseline is
    missing or unreadable at the anchor, or when the tag cannot be read."""
    try:
        rows = waiver_rows(worktree, runner)
    except ValueError as exc:
        return NO_DATA, "%s" % exc
    short = [(path, line, reason) for path, line, reason in rows if len(reason.split()) < 3]
    if short:
        return FAIL, ("%d marker(s) with an empty or short reason: %s"
                      % (len(short), "; ".join("%s:%d %r" % item for item in short)))
    anchor, verdict, detail = _baseline_anchor(worktree, release_base, sha, runner)
    if anchor is None:
        return verdict, detail
    baseline = _read_baseline(worktree, anchor, runner)
    if baseline is None:
        return NO_DATA, "no readable reviewed baseline at %s in %s %s" % (
            WAIVERS_BASELINE, detail, anchor[:12])
    reviewed, taken_at = baseline
    tagged = _run(["git", "grep", "-n", "-F", WAIVER_MARKER, base_tag], worktree, runner)
    at_tag = []
    if tagged.returncode == 0:
        for line in (tagged.stdout or "").splitlines():
            parts = line.split(":", 3)
            if len(parts) != 4 or WAIVER_MARKER not in parts[3]:
                return NO_DATA, "unreadable git grep line at %s: %r" % (base_tag, line[:120])
            at_tag.append((parts[1], parts[3].split(WAIVER_MARKER, 1)[1].strip()))
    elif tagged.returncode != 1:
        return NO_DATA, ("the markers at %s could not be read: git grep exit %d"
                         % (base_tag, tagged.returncode))
    known = set(at_tag)
    new = [item for item in rows if (item[0], item[2]) not in known]
    listed = "; ".join("%s:%d %s" % item for item in new) or "none"
    if len(rows) > reviewed:
        return FAIL, ("%d marker(s) above the reviewed baseline %d (taken at %s), %d at %s; "
                      "new since %s: %s"
                      % (len(rows), reviewed, taken_at, len(at_tag), base_tag, base_tag, listed))
    return PASS, ("%d marker(s), reviewed baseline %d (taken at %s), %d at %s, every reason "
                  "three words or more; new since %s: %s"
                  % (len(rows), reviewed, taken_at, len(at_tag), base_tag, base_tag, listed))


JUDGE_REVIEW = "judge-review.json"


def _judge_review(rec_dir):
    """{path: blob sha} the owner accepted with `accept-judge`, keyed to a
    sha, as (sha, files); None when the record is missing or unreadable."""
    try:
        with open(os.path.join(rec_dir, JUDGE_REVIEW), "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("judge_files"), dict):
        return None
    return data.get("sha"), data["judge_files"]


def _drift_row(worktree, rec_dir, release_base, sha, runner):
    """Every judge file that differs between the release base and the frozen
    sha (added, deleted or edited) must carry the owner's review: its blob
    at sha equals the one recorded by `accept-judge` in the record
    directory, outside the candidate's tree. PASS with the JSON array of the
    reviewed changes ([] when the judge is unchanged), FAIL naming each
    change the record does not cover, NO-DATA when changes exist and no
    readable record does, or git does not answer. Never a silent list."""
    proc = _run(["git", "diff", "--name-only", release_base, sha, "--"] + list(JUDGE_FILES),
                worktree, runner)
    if proc.returncode != 0:
        return NO_DATA, "git diff %s %s failed: exit %d" % (release_base[:12], sha[:12], proc.returncode)
    changed = sorted(set(line.strip() for line in (proc.stdout or "").splitlines()
                         if line.strip()))
    if not changed:
        return PASS, json.dumps(changed)
    at_sha = judge_files_at(_git_dir(worktree) or "", sha)
    if at_sha is None:
        return NO_DATA, "the judge files at %s could not be listed" % sha[:12]
    review = _judge_review(rec_dir)
    if review is None:
        return NO_DATA, ("%d judge file(s) changed since the release base %s and no readable "
                         "%s in the record directory covers them: %s"
                         % (len(changed), release_base[:12], JUDGE_REVIEW, ", ".join(changed)))
    reviewed_sha, reviewed = review
    if reviewed_sha != sha:
        return NO_DATA, ("the %s in the record directory is for %s, not the frozen %s; the owner "
                         "accepts each freeze on its own" % (JUDGE_REVIEW, reviewed_sha, sha[:12]))
    unreviewed = [path for path in changed if reviewed.get(path, "absent") != at_sha.get(path, "absent")]
    if unreviewed:
        return FAIL, ("judge file(s) changed since the release base %s without the owner's "
                      "review record: %s" % (release_base[:12], ", ".join(unreviewed)))
    return PASS, json.dumps(changed)


def _hit_count(pattern, proc):
    """The hit count the scanner's own summary line reports, as text; a
    summary that names no count (a clean private terms run) is 0, and no
    summary at all says so rather than inventing a number."""
    found = pattern.search((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if found is None:
        return "no hit summary printed,"
    return "%s hit(s)," % (found.group(1) or "0")


def range_scans(root, base, sha, runner=None, judge_root=None):
    """(verdict, name, detail) rows for the two range scanners over base..sha,
    run in root with the real HOME. The terms file is read here first: missing,
    unreadable or holding no term is NO-DATA for both rows. The scanners'
    output is captured and counted, never forwarded."""
    for name, value in (("root", root), ("base", base), ("sha", sha)):
        if not isinstance(value, str) or not value:
            raise ValueError("%s must be a non-empty string, got %r" % (name, value))
    names = ("outgoing-scan", "private-terms")
    terms_path = os.path.expanduser(TERMS_FILE)
    try:
        with open(terms_path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return [(NO_DATA, name, "terms file %s: %s" % (terms_path, exc)) for name in names]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return [(NO_DATA, name, "terms file %s is not UTF-8" % terms_path) for name in names]
    terms = [line.strip() for line in text.splitlines()
             if line.strip() and not line.strip().startswith("#")]
    if not terms:
        return [(NO_DATA, name, "terms file %s holds no term (blank or comment lines only)"
                 % terms_path) for name in names]
    rev_range = "%s..%s" % (base, sha)
    plan = (
        (names[0], ["scripts/outgoing_scan.py", "--terms-file", terms_path, rev_range],
         _OUTGOING_SUMMARY_RE),
        (names[1], ["scripts/private_terms_scan.py", "--terms", terms_path, "--range", rev_range],
         _PRIVATE_SUMMARY_RE),
    )
    rows = []
    for name, cmd, pattern in plan:
        script = os.path.join(judge_root or root, cmd[0])
        if not os.path.isfile(script):
            rows.append((NO_DATA, name, "no %s in the judge" % cmd[0]))
            continue
        proc = _run(list(PYTHON) + [script] + cmd[1:], root, runner, timeout=ROW_TIMEOUT_S)
        if proc.returncode == 0:
            verdict = PASS
        elif proc.returncode == 1:
            verdict = FAIL
        else:
            verdict = NO_DATA
        rows.append((verdict, name, "%s exit %d, %s over %s, terms %s"
                     % (cmd[0], proc.returncode, _hit_count(pattern, proc), rev_range,
                        terms_path)))
    return rows


def _scan_row(worktree, judge_root, base_tag, sha, runner):
    rows = range_scans(worktree, base_tag, sha, runner, judge_root)
    verdicts = [row[0] for row in rows]
    verdict = FAIL if FAIL in verdicts else NO_DATA if NO_DATA in verdicts else PASS
    return verdict, "; ".join("%s %s: %s" % (name, v, detail) for v, name, detail in rows)


def _extract_judge(git_dir, judge, dest):
    """Write every JUDGE_FILES entry present at the judge commit under dest,
    read through the object reader; the first path that cannot be written
    or read, else None."""
    files = judge_files_at(git_dir, judge)
    if files is None:
        return "the judge files at %s could not be listed" % judge[:12]
    for path in files:
        content = _blob_at(git_dir, judge, path)
        if content is None:
            return "judge file %s at %s could not be read" % (path, judge[:12])
        full = os.path.join(dest, path)
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(content)
            os.chmod(full, 0o755)
        except OSError as exc:
            return "judge file %s could not be written under %s: %s" % (path, dest, exc)
    return None


def _dispatch_judge(root, rec_dir, judge, runner):
    """Run the judge anchor's own copy of this module (`sbe --as-judge`)
    against the candidate, so the code under review never sets its own
    rules; then read the rows it recorded. A copy that cannot be extracted
    or does not answer records NO-DATA for every row."""
    git_dir = _git_dir(root)
    scratch = _scratch_root()
    try:
        os.makedirs(scratch, exist_ok=True)
        dest = tempfile.mkdtemp(prefix="precut-judge-" + judge[:12] + "-", dir=scratch)
    except OSError as exc:
        return _record_all(rec_dir, NO_DATA, "no scratch root under %s: %s" % (scratch, exc))
    # nothing recorded before this dispatch may be read back as its answer
    shutil.rmtree(os.path.join(rec_dir, "results"), True)
    # the dispatch key goes over stdin only: an argv is readable by any
    # process of the same user (ps), and the key signs the judge's rows
    key = os.urandom(32).hex()
    try:
        problem = _extract_judge(git_dir, judge, dest)
        if problem:
            return _record_all(rec_dir, NO_DATA, problem)
        proc = _run(list(PYTHON) + [os.path.join(dest, JUDGE_MODULE), "--dir", rec_dir,
                                    "--root", root, "sbe", "--as-judge"], root, runner,
                    timeout=ROW_TIMEOUT_S * len(SBE_ROWS), stdin_text=key + "\n")
        ended = time.time()
    finally:
        shutil.rmtree(dest, True)
    if proc.returncode != 0:
        # the judge copy exits 0 only after recording every row and the
        # manifest; a FAIL row is read from the rows, never from the exit,
        # so any other exit (a crash is 1) is no answer
        last = ((proc.stderr or "").strip().splitlines() or ["no output"])[-1]
        return _record_all(rec_dir, NO_DATA, "the judge copy at %s did not answer: exit %d, %s"
                           % (judge[:12], proc.returncode, last[:200]))
    sha = frozen_sha(rec_dir)
    # one read of each file, held in memory: what is checked is what is returned
    manifest, late = _snapshot(os.path.join(rec_dir, "results", JUDGED_MANIFEST), ended)
    if late:
        return _record_all(rec_dir, NO_DATA, late)
    if manifest is None or manifest.get("judge") != judge or manifest.get("sha") != sha \
            or manifest.get("rows") != list(SBE_ROWS):
        return _record_all(rec_dir, NO_DATA, ("the judge copy at %s left no manifest bound to this "
                                              "dispatch" % judge[:12]))
    rows = []
    for name in SBE_ROWS:
        row, late = _snapshot(os.path.join(rec_dir, "results", name + ".json"), ended)
        if late:
            return _record_all(rec_dir, NO_DATA, late)
        if row is None or row.get("judged_by") != judge:
            return _record_all(rec_dir, NO_DATA, ("the row %s was not recorded by the judge copy "
                                                  "at %s" % (name, judge[:12])))
        if row.get("sha") != sha or row.get("verdict") not in (PASS, FAIL, NO_DATA) \
                or not isinstance(row.get("detail"), str):
            return _record_all(rec_dir, NO_DATA, ("the row %s from the judge copy at %s is not a "
                                                  "row for %s" % (name, judge[:12], sha[:12])))
        rows.append((row["verdict"], name, row["detail"]))
    mac = manifest.get("mac")
    if not isinstance(mac, str) or not hmac.compare_digest(mac, _rows_mac(key, sha, judge, rows)):
        return _record_all(rec_dir, NO_DATA, ("the rows under results/ do not carry the judge copy's "
                                              "signature for this dispatch (%s): written by "
                                              "something other than the judge" % judge[:12]))
    return rows


def _rows_mac(key, sha, judge, rows):
    """HMAC-SHA256 under the dispatch key over the sha, the judge and every
    (verdict, name, detail) row, in order. Only the dispatched judge holds
    the key (it arrived on its stdin), so no other process can sign rows."""
    blob = json.dumps({"sha": sha, "judge": judge, "rows": [list(row) for row in rows]},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(bytes.fromhex(key), blob, hashlib.sha256).hexdigest()


def _snapshot(path, ended):
    """(parsed JSON object or None, refusal or None) from one read of path;
    the refusal names a file modified after the judge exited at ended,
    which nothing but a process outliving the judge can have written."""
    try:
        with open(path, "rb") as fh:
            stamp = os.fstat(fh.fileno()).st_mtime
            raw = fh.read()
    except OSError:
        return None, None
    if stamp > ended:
        return None, ("%s was modified after the judge copy exited: written by something other "
                      "than the judge" % os.path.basename(path))
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, None
    return (data if isinstance(data, dict) else None), None


JUDGED_MANIFEST = "judged.json"


def _judged_manifest(rec_dir):
    """The manifest the judge copy writes last, or None."""
    try:
        with open(os.path.join(rec_dir, "results", JUDGED_MANIFEST), "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _row_mark(rec_dir, check):
    """The `judged_by` a recorded row carries, or None."""
    try:
        with open(os.path.join(rec_dir, "results", check + ".json"), "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return data.get("judged_by") if isinstance(data, dict) else None


def _judge_pins(git_dir, judge):
    """{name: value} of the pins the judge's own module carries, read from
    its text at the judge commit; None when the module cannot be read."""
    text = _blob_at(git_dir, judge, JUDGE_MODULE)
    if text is None:
        return None
    pins = {}
    for name in PINS:
        found = re.search(r'^%s = "([^"]*)"' % name, text.decode("utf-8", "replace"), re.M)
        if found:
            pins[name] = found.group(1)
    return pins


def _pins_agree(git_dir, judge, sha, rec_dir):
    """None when the candidate's pins (base tag, base commit, baseline
    landing commit) equal the judge's, or when the owner's review record
    covers this module at sha; else the reason. From 1.1.1 on the base and
    the judge come from the release anchor, and a candidate that re-pins
    them is refused unless the pin change was reviewed."""
    theirs = _judge_pins(git_dir, judge)
    if theirs is None:
        return "the judge's module at %s could not be read" % judge[:12]
    mine = dict((name, globals()[name]) for name in PINS)
    differing = sorted(name for name in PINS if theirs.get(name) != mine[name])
    if not differing:
        return None
    review = _judge_review(rec_dir)
    at_sha = judge_files_at(git_dir, sha) or {}
    if review is not None and review[0] == sha and \
            review[1].get(JUDGE_MODULE) == at_sha.get(JUDGE_MODULE, "absent"):
        return None
    return ("the candidate re-pins %s against the judge at %s and no owner review record covers "
            "%s at %s" % (", ".join(differing), judge[:12], JUDGE_MODULE, sha[:12]))


def accept_judge(root, rec_dir):
    """The owner's hand: record the judge files of the frozen candidate as
    reviewed, in the record directory outside the tree. Refuses unless the
    freeze verifies PASS. Returns (exit code, line)."""
    verdict, detail = verify_freeze(root, rec_dir)
    if verdict != PASS:
        return 2, "the freeze is not PASS, nothing accepted: %s" % detail
    record = _load_freeze(rec_dir) or {}
    sha = record.get("sha")
    files = judge_files_at(_git_dir(root), sha)
    if files is None:
        return 2, "the judge files at %s could not be listed" % sha
    blob = json.dumps({"sha": sha, "judge_files": files, "at": _now()},
                      indent=2, sort_keys=True).encode("utf-8")
    handle, temp = tempfile.mkstemp(dir=rec_dir, prefix=".judge-review.", suffix=".tmp")
    try:
        os.write(handle, blob)
    finally:
        os.close(handle)
    os.replace(temp, os.path.join(rec_dir, JUDGE_REVIEW))
    return 0, "accepted %d judge file(s) at %s" % (len(files), sha)


def _record_all(rec_dir, verdict, detail, judged_by=None):
    rows = []
    for name in SBE_ROWS:
        record_result(rec_dir, name, verdict, detail, judged_by)
        rows.append((verdict, name, detail))
    return rows


def sbe_pass(root, rec_dir, runner=None, as_judge=False):
    """Run the five BrotherSBE rows inside a detached worktree at the frozen
    sha and record each one. Anything but a PASS freeze returns one NO-DATA
    row and writes nothing; a worktree that cannot be created records NO-DATA
    for every row; a main checkout that moves during the pass fails the row
    it moved under and every later one."""
    verdict, detail = verify_freeze(root, rec_dir)
    if verdict != PASS:
        return [(NO_DATA, "sbe", "the freeze is not PASS: %s" % detail)]
    record = _load_freeze(rec_dir) or {}
    sha = record.get("sha")
    base_tag = record.get("base_tag")
    if not isinstance(base_tag, str) or not base_tag:
        return [(NO_DATA, "sbe", "the freeze record names no base tag")]
    frozen = _fingerprint(root)
    if any(part is None for part in frozen) or frozen[0] != sha:
        return [(NO_DATA, "sbe", "the main checkout could not be fingerprinted")]
    judge = record.get("judge") or sha
    if judge != sha and not as_judge:
        disagreement = _pins_agree(_git_dir(root), judge, sha, rec_dir)
        if disagreement:
            return _record_all(rec_dir, NO_DATA, disagreement)
        return _dispatch_judge(root, rec_dir, judge, runner)
    # every row carries who judged it: the anchor's copy, or, in bootstrap,
    # the candidate's own code (the same sha as the candidate)
    mark = judge
    scratch = _scratch_root()
    try:
        os.makedirs(scratch, exist_ok=True)
        work = tempfile.mkdtemp(prefix="precut-" + sha[:12] + "-", dir=scratch)
        home = os.path.join(work, "home")
        os.makedirs(home)
    except OSError as exc:
        return _record_all(rec_dir, NO_DATA, "no scratch root under %s: %s" % (scratch, exc), mark)
    worktree = os.path.join(work, "tree")
    added = _run(["git", "worktree", "add", "--detach", worktree, sha], root, runner)
    if added.returncode != 0:
        shutil.rmtree(work, True)
        return _record_all(rec_dir, NO_DATA, "the worktree could not be created: git exit %d"
                           % added.returncode, mark)
    env = _candidate_env(home, rec_dir)
    # the judge's own scanners run against the worktree; in bootstrap the
    # judge is the candidate, so they are the worktree's
    judge_root = worktree
    if judge != sha:
        judge_root = os.path.join(work, "judge")
        problem = _extract_judge(_git_dir(root), judge, judge_root)
        if problem:
            _run(["git", "worktree", "remove", worktree], root, runner)
            shutil.rmtree(work, True)
            return _record_all(rec_dir, NO_DATA, problem, mark)
    rows = []
    tainted = None
    try:
        for name in SBE_ROWS:
            if tainted is None:
                tainted = _disturbed(root, sha, frozen)
            if tainted is None:
                if name == "sbe-gate":
                    verdict, detail = _gate_row(worktree, judge_root, env, runner)
                elif name == "silent-lint":
                    verdict, detail = _lint_row(worktree, judge_root, env, base_tag, sha, runner)
                elif name == "waivers":
                    verdict, detail = _waivers_row(worktree, base_tag, sha,
                                                   record.get("release_base"), runner)
                elif name == "scanner-drift":
                    verdict, detail = _drift_row(worktree, rec_dir, record.get("release_base") or "",
                                                 sha, runner)
                else:
                    verdict, detail = _scan_row(worktree, judge_root, base_tag, sha, runner)
                tainted = _disturbed(root, sha, frozen)
            if tainted is not None:
                verdict, detail = FAIL, tainted
            record_result(rec_dir, name, verdict, detail, mark)
            rows.append((verdict, name, detail))
    finally:
        removed = _run(["git", "worktree", "remove", worktree], root, runner)
        if removed.returncode == 0:
            shutil.rmtree(work, True)
        else:
            sys.stderr.write("precut-review: the worktree %s was not removed (git exit %d); "
                             "left in place, never forced\n" % (worktree, removed.returncode))
    if as_judge:
        # the manifest is written last and binds these rows to the dispatch
        # that asked for them; the parent refuses rows without it
        # the key itself never reaches a file: the manifest carries only
        # the signature over the rows as recorded
        blob = json.dumps({"mac": _rows_mac(as_judge, sha, judge, rows), "sha": sha,
                           "judge": judge, "rows": list(SBE_ROWS), "at": _now()},
                          indent=2, sort_keys=True).encode("utf-8")
        results = os.path.join(rec_dir, "results")
        os.makedirs(results, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=results, prefix=".judged.", suffix=".tmp")
        try:
            os.write(handle, blob)
        finally:
            os.close(handle)
        os.replace(temp, os.path.join(results, JUDGED_MANIFEST))
    return rows


def sha_current(root, rec_dir, runner=None):
    """PASS when every judge file on disk at root equals its blob at the
    frozen sha, FAIL naming each one that differs (an uncommitted edit of a
    judge, this module included), NO-DATA without a readable freeze or tree."""
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string, got %r" % (root,))
    sha = frozen_sha(rec_dir)
    if sha is None:
        return NO_DATA, "no readable freeze record under %s" % rec_dir
    proc = _run(["git", "ls-tree", "-r", sha, "--"] + list(JUDGE_FILES), root, runner)
    if proc.returncode != 0:
        return NO_DATA, "git ls-tree %s failed: exit %d" % (sha, proc.returncode)
    committed = {}
    for line in (proc.stdout or "").splitlines():
        meta, _tab, path = line.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not _SHA_RE.match(parts[2]) or not path:
            return NO_DATA, "unreadable git ls-tree line: %r" % line[:120]
        committed[path] = parts[2]
    differing = []
    for path in JUDGE_FILES:
        try:
            with open(os.path.join(root, path), "rb") as fh:
                on_disk = _blob_sha(fh.read())
        except OSError:
            on_disk = None
        if on_disk is None and path not in committed:
            continue
        if on_disk != committed.get(path):
            differing.append(path)
    if differing:
        return FAIL, "judge file(s) on disk differ from %s: %s" % (sha, ", ".join(differing))
    return PASS, "%d judge file(s) on disk equal their blobs at %s" % (len(committed), sha)


def _exit_code(rows):
    verdicts = [row[0] for row in rows]
    if FAIL in verdicts:
        return 1
    if NO_DATA in verdicts:
        return 2
    return 0


def main(argv=None):
    """The one entry point. Never raises on its argument."""
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list) or any(not isinstance(a, str) for a in argv):
        sys.stderr.write("precut-review: arguments must be a list of strings\n")
        return 2
    parser = argparse.ArgumentParser(prog="precut_review",
                                     description="the pre-cut review record")
    parser.add_argument("--dir", default=DEFAULT_DIR,
                        help="the record directory (default %(default)s)")
    parser.add_argument("--root", default=None,
                        help="the repository root (default: the working directory)")
    subs = parser.add_subparsers(dest="command")
    frozen = subs.add_parser("freeze", help="write the candidate sha once")
    frozen.add_argument("--replace", action="store_true",
                        help="move the old record aside and clear results first")
    frozen.add_argument("--base-tag", default=None,
                        help="the release tag to judge against (default: the highest public "
                             "release tag)")
    subs.add_parser("verify", help="read the freeze back against the tree")
    sbe = subs.add_parser("sbe", help="run the BrotherSBE rows in a worktree at the frozen sha")
    sbe.add_argument("--as-judge", action="store_true",
                     help="this copy is the judge anchor's own: read the dispatch key from the "
                          "first stdin line, run the rows, write the signed manifest, exit 0 "
                          "only when every row is recorded")
    subs.add_parser("accept-judge",
                    help="the owner's hand: record the frozen candidate's judge files as reviewed")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 2
    root = args.root if args.root else os.getcwd()
    if args.command == "accept-judge":
        code, line = accept_judge(root, args.dir)
        (sys.stdout if code == 0 else sys.stderr).write("precut-review: %s\n" % line)
        return code
    if args.command == "sbe":
        key = None
        if args.as_judge:
            key = sys.stdin.readline().strip()
            if not re.match(r"\A[0-9a-f]{64}\Z", key):
                sys.stderr.write("precut-review: --as-judge reads a 64 hex dispatch key from "
                                 "stdin; none arrived\n")
                return 2
        try:
            rows = sbe_pass(root, args.dir, as_judge=key)
        except ValueError as exc:
            sys.stderr.write("precut-review: %s\n" % exc)
            return 2
        record = _load_freeze(args.dir) or {}
        who = ("judged by the candidate's own code: bootstrap"
               if record.get("judge") == record.get("sha")
               else "judged by the release base %s" % (record.get("judge") or "?")[:12])
        for verdict, name, detail in rows:
            print("%-8s %-14s [%s] %s" % (verdict, name, who, detail))
        if args.as_judge:
            # the explicit success exit: every row recorded and the manifest
            # written; the parent reads the verdicts from the rows. A crash
            # before this line exits 1 and the parent reads no answer.
            return 0 if len(rows) == len(SBE_ROWS) else 2
        return _exit_code(rows)
    if args.command == "freeze":
        if args.replace:
            _replace_record(args.dir)
        try:
            record = freeze(root, args.dir, args.base_tag)
        except FileExistsError as exc:
            sys.stderr.write("precut-review: %s\n" % exc)
            return 1
        except NoDataError as exc:
            sys.stderr.write("precut-review: %s\n" % exc)
            return 2
        except ValueError as exc:
            sys.stderr.write("precut-review: %s\n" % exc)
            return 1
        print("frozen %s; %s" % (record["sha"], (
            "judged by the candidate's own code: bootstrap" if record["judge"] == record["sha"]
            else "judged by the release base %s" % record["judge"][:12])))
        return 0
    if args.command == "verify":
        verdict, detail = verify_freeze(root, args.dir)
        print("%-8s %s" % (verdict, detail))
        if verdict == PASS:
            return 0
        return 1 if verdict == FAIL else 2
    sys.stderr.write("precut-review: no subcommand given; try freeze, verify or sbe\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
