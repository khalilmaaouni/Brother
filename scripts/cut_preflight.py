"""cut_preflight.py: everything that can refuse a release cut, asked FIRST.

WHY THIS EXISTS. The 1.0.21 cut was refused six times on 2026-09-20, and every
refusal was cheap to know and expensive to reach: each gate that refused sat
behind cut_v1.0.0.sh's serial chain (measured 6,397s, 5,035s and 5,004s) or
on another machine. The six: a secret shape in the release note's changelog
text (41 minutes in), two battery exceptions one day past review_by (41
minutes), seven duplicated export allowlist lines (127 minutes), two tests
that need a folder the public tree does not ship (after the push had been
approved), a 24 hour handover pack that turned 24.6 hours old during the run
(96 minutes), and four tests that need a private file in the operator's home,
found only by the public repository's own runner. Fixing one red revealed the
next. The root cause was one: nothing reproduced the late gates' conditions at
the start. This module does, in minutes, by COMPOSING the checks that already
exist; it adds no verdict logic of its own beyond two ideas:

  THE HORIZON. Three gates are clocks, not properties of the tree (exception
  review_by dates, the handover pack's 24 hours, the restore drill's 7 days).
  A cut takes hours, so each is read as of NOW PLUS THE HORIZON: a gate that
  would lapse mid cut refuses before the cut starts.

  THE VIRGIN GATE. The fast gate is run where the public runner will run it:
  on an export shaped tree (export_public.build_orphan_commit, the exporter's
  own function) with HOME pointed at an empty directory. A test that passes
  only because of the author's tree or the author's home folder fails here.

  THE BUMPED COPY. The fast gate above reads the tree as it is BEFORE the
  cut's version bump, and the cut asks the same version rule again AFTER the
  bump. check_version_after_bump copies the files that rule reads into a
  scratch directory, applies the cut's own bump to the copy, and runs the
  rule there: seconds, and a refusal names the file and line.

Exit 0 when every check is OK. Exit 1 when any check REFUSED. Exit 2 when
nothing refused but something could not be read (NO-DATA). cut.py blocks on
anything but 0: a cut that cannot read its own preflight is not ready.

HONEST LIMIT. The virgin gate runs on this machine's operating system; the
public runner is Linux. A red that needs Linux to appear is not caught here.
"""
import argparse
import contextlib
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OK, REFUSED, NODATA = "OK", "REFUSED", "NO-DATA"
EXIT_OK, EXIT_REFUSED, EXIT_NODATA = 0, 1, 2

#: Longer than the longest cut measured on 2026-09-20 (chain 6,397s plus the
#: fast gate, the push proof and the public runner), with room for one rerun
#: of the short steps.
DEFAULT_HORIZON_HOURS = 6
PACK_MAX_AGE_HOURS = 24
FAST_SUMMARY = re.compile(r"^pass\s+(\d+)\s+fail\s+(\d+)\s+no-data\s+(\d+)", re.M)

#: N4 (2026-10-11): the version rule asked AFTER the cut's own bump, on a
#: scratch copy of the files it reads. The first 1.1.1 rehearsal read
#: NOT-READY after 55 minutes on a README string that agreed with the
#: manifest before the bump and not after it.
VERSION_SOURCE_REL = os.path.join("scripts", "version_source.py")
VERSION_TRUTH_REL = os.path.join("scripts", "test_version_truth.py")
VERSION_ROW = "version-after-bump"
BUMP_FILES = (".claude-plugin/marketplace.json", ".cursor-plugin/marketplace.json", "README.md",
              "docs/VERSIONING.md", "docs/plan/PROJECT.public.md")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


#: CF1.b (2026-10-14): one bound for every network read of the cut path. The
#: 1.1.3 cut day lost about two hours to a DNS stall in a read with no bound.
NETWORK_TIMEOUT_S = 300
#: git aborts an HTTP transfer slower than LOW_SPEED_LIMIT bytes per second for
#: LOW_SPEED_TIME seconds, and never waits for a password on the terminal.
LOW_SPEED_LIMIT = "1000"
LOW_SPEED_TIME = "30"


def network_bounds():
    """The environment every network git read carries (REQ-CF1-08)."""
    return {"GIT_HTTP_LOW_SPEED_LIMIT": LOW_SPEED_LIMIT,
            "GIT_HTTP_LOW_SPEED_TIME": LOW_SPEED_TIME,
            "GIT_TERMINAL_PROMPT": "0"}


def _run(cmd, cwd, runner=None, env=None, timeout=None):
    """One subprocess, never raising: a missing binary reads as exit 127 and
    a timeout as exit 124. With no injected runner the child gets its own
    session and a timeout kills its whole process group (cut_rehearsal.
    _run_tree), so a helper the child started cannot outlive the bound.
    A cmd that is not a non empty list of non empty strings is refused."""
    if (not isinstance(cmd, (list, tuple)) or not cmd
            or not all(isinstance(a, str) and a for a in cmd)):
        raise ValueError("cmd must be a non-empty list of non-empty strings, got %r" % (cmd,))
    if runner is None:
        import cut_rehearsal  # lazy: it imports cut lazily, so no cycle
        return cut_rehearsal._run_tree(cmd, cwd, env, timeout)
    try:
        return runner(cmd, cwd=cwd, capture_output=True, text=True, env=env,
                      timeout=timeout)
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", "%s" % exc)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out after %ss" % timeout)


def _last(proc):
    lines = [l for l in ((proc.stdout or "") + (proc.stderr or "")).splitlines()
             if l.strip()]
    return lines[-1][:200] if lines else "no output"


def _finite_hours(value):
    """R1.2: a finite number of hours, or a refusal. A bool is refused:
    True would otherwise read as one hour at the horizon."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("horizon hours must be a finite number, got %r" % (value,))
    if value != value or value == float("inf") or value == float("-inf"):
        raise ValueError("horizon hours must be a finite number, got %r" % (value,))
    return value


def _require_text(value, name):
    """R1.3: the one place that refuses a missing, empty, wrong typed or
    falsy text argument, so every late gate of this slice routes its root, its
    commit, its version and its remote through here. Hostile API input becomes
    a ValueError at the door, never a TypeError from os.path.join deeper in,
    and never a silent accept."""
    if not isinstance(value, str) or not value:
        raise ValueError("%s must be a non-empty string, got %r" % (name, value))
    return value


def check_exceptions(root, at, runner=None):
    """P1: no declared battery exception is expired now or at the horizon."""
    if not isinstance(at, datetime.datetime):
        raise ValueError("at must be a datetime, got %r" % (at,))
    proc = _run([sys.executable, os.path.join(root, "scripts",
                                             "battery_exception_audit.py"),
                 "--now", at.date().isoformat()], root, runner)
    if proc.returncode == 0:
        return OK, "exceptions", _last(proc)
    verdict = NODATA if proc.returncode == 2 else REFUSED
    return verdict, "exceptions", ("as of %s: %s" % (at.date(), _last(proc)))


def check_pack_clock(root, horizon_hours, runner=None):
    """P2a: the newest handover pack is still fresh when the cut ends."""
    allowed = max(PACK_MAX_AGE_HOURS - _finite_hours(horizon_hours), 0)
    proc = _run([sys.executable, os.path.join(root, "scripts",
                                             "close_ceremony_check.py"),
                 "--max-age-hours", "%g" % allowed], root, runner)
    if proc.returncode == 0:
        return OK, "pack-clock", _last(proc)
    verdict = NODATA if proc.returncode == 2 else REFUSED
    return verdict, "pack-clock", ("the pack must be under %g hours old so it "
                                   "is still under %d when the cut ends: %s"
                                   % (allowed, PACK_MAX_AGE_HOURS, _last(proc)))


def check_readiness_rows(root, at, runner=None):
    """P2b: every CRITICAL readiness row passes as of the horizon date (the
    restore drill's 7 day age is one of them). The non critical release
    artifact row fails by construction before the bump and is not read."""
    if not isinstance(at, datetime.datetime):
        raise ValueError("at must be a datetime, got %r" % (at,))
    proc = _run([sys.executable, os.path.join(root, "scripts",
                                             "readiness_gate.py"),
                 "--json", "--today", at.date().isoformat()], root, runner)
    try:
        rows = json.loads(proc.stdout or "")["rows"]
    except (ValueError, KeyError, TypeError):
        return NODATA, "readiness-rows", "readiness_gate.py --json unreadable: %s" % _last(proc)
    bad = ["%s (%s)" % (r.get("title"), r.get("verdict")) for r in rows
           if r.get("critical") and r.get("verdict") != "PASS"]
    if bad:
        return REFUSED, "readiness-rows", "as of %s: %s" % (at.date(), "; ".join(bad))
    return OK, "readiness-rows", "%d critical row(s) PASS as of %s" % (
        sum(1 for r in rows if r.get("critical")), at.date())


def check_release_plan(root, runner=None):
    """(verdict, 'release-plan', detail) in the shape run_all returns; OK, REFUSED or NODATA, never a raise (CV1.d).
    Runs gen_release_plan.py --check: exit 0 is OK, 1 is REFUSED with the script's own FAIL line, and 2, any other
    exit, a missing script or a timeout is NO-DATA. Exit 1 without the script's own FAIL line (a traceback) is NO-DATA
    too: a crash is no verdict about the page (review 2026-10-04)."""
    if not isinstance(root, str) or not root:
        return NODATA, "release-plan", "no root to read the release plan from (%r)" % (root,)
    script = os.path.join(root, "scripts", "gen_release_plan.py")
    if not os.path.isfile(script):
        return NODATA, "release-plan", "scripts/gen_release_plan.py is missing"
    proc = _run([sys.executable, script, "--check"], root, runner, timeout=120)
    if proc.returncode == 0:
        return OK, "release-plan", "the release plan covers every open unit and its page is current"
    fail = [l for l in (proc.stdout or "").splitlines() if l.startswith("FAIL: ")]
    if proc.returncode == 1 and fail:
        return REFUSED, "release-plan", fail[-1][:200]
    return NODATA, "release-plan", "gen_release_plan.py --check exit %d: %s" % (proc.returncode, _last(proc))


def check_public_remote(root, version, remote, runner=None):
    """P3: the public remote carries no leftover of this version. The
    exporter never force pushes, so an existing release branch turns its plain
    push into a non fast forward at the very last step."""
    _require_text(root, "root")
    _require_text(version, "version")
    _require_text(remote, "remote")
    refs = ["refs/heads/release/%s" % version, "refs/tags/v%s" % version]
    env = dict(os.environ)
    env.update(network_bounds())
    proc = _run(["git", "ls-remote", remote] + refs, root, runner, env=env,
                timeout=NETWORK_TIMEOUT_S)
    if proc.returncode != 0:
        return NODATA, "public-remote", "git ls-remote failed: %s" % _last(proc)
    found = [l.split()[-1] for l in (proc.stdout or "").splitlines() if l.strip()]
    if found:
        return REFUSED, "public-remote", ("already on %s: %s (close the release "
                                          "pull request and delete the branch, "
                                          "or pick the next version)"
                                          % (remote, ", ".join(found)))
    return OK, "public-remote", "no release/%s branch and no v%s tag on %s" % (
        version, version, remote)


def check_changelog_text(root, since, runner=None):
    """P4: the commit text that will enter the release note carries no secret
    shape. The note is scanned after it is built; its inputs exist now."""
    _require_text(root, "root")
    if since is not None:
        _require_text(since, "since")
    import pre_push_gate
    if not since:
        return NODATA, "changelog-text", "no previous release commit to read from"
    proc = _run(["git", "log", "--format=%s%n%b", "%s..HEAD" % since], root, runner)
    if proc.returncode != 0:
        return NODATA, "changelog-text", "git log %s..HEAD failed: %s" % (since, _last(proc))
    text = pre_push_gate.strip_public_examples(proc.stdout or "")
    hits = sum(1 for p in pre_push_gate.SECRET_SHAPES if p.search(text))
    if hits:
        return REFUSED, "changelog-text", ("%d secret shape(s) in the commit text "
                                           "since %s; the value is never printed"
                                           % (hits, since[:12]))
    return OK, "changelog-text", "no secret shape in the commit text since %s" % since[:12]


#: The words a cut writes into its release note, ahead of the commit it was
#: cut from. A note that carries them in any shape claims a cut.
#: precut_review.py mirrors the full line for the reason its own comment gives.
CUT_COMMIT_MARK = "Cut from hub commit"
CUT_COMMIT_LINE = re.compile(CUT_COMMIT_MARK + r" `([0-9a-f]{7,40})`")
#: What the generator itself writes into every note it produces: the cut
#: words, its Source revision section and its manifest digest line. Lower
#: case and single spaced, because a note is searched folded that way. A note
#: carrying any of them came out of the generator, so it is a release note,
#: damaged or not, and never a hand written draft. Literals on purpose: the
#: predicate below imports nothing and cannot raise. test_cut_preflight.py
#: pins each to the text release_note_from_tree.py, export_public.py and
#: reproduce_export.py really write and read.
GENERATED_NOTE_MARKS = (CUT_COMMIT_MARK.lower(), "## source revision",
                        "export manifest digest")
#: Where the manifests say which version this tree is.
DECLARED_VERSION_REL = os.path.join(".claude-plugin", "marketplace.json")


def generator_mark(note_text):
    """The first of GENERATED_NOTE_MARKS found in `note_text`, folded to lower
    case and single spaces (so a wrapped or re-cased mark still counts), or
    '' when there is none or the note is not text."""
    if not isinstance(note_text, str):
        return ""
    flat = " ".join(note_text.split()).lower()
    return next((mark for mark in GENERATED_NOTE_MARKS if mark in flat), "")


def is_generated_note(note_text):
    """True when the note carries any mark of the generator: it came out of a
    cut, damaged or not. No syntax is read, so a mark quoted in a code block
    of a hand draft counts too (a false refusal costs a re-read of one note,
    a false pass costs a rehearsal)."""
    return bool(generator_mark(note_text))


def is_uncut_draft(root, version, note_text):
    """True only when `note_text`, the text of docs/releases/<version>.md, is
    the uncut draft of the version being cut: `version` is the release the
    caller is cutting, the manifests under `root` already declare that
    version, and the note carries no cut commit line.

    WHY SUCH A NOTE EXISTS. When the manifests are bumped ahead of the tag (as
    1.1.0 was), release_invariant.py requires docs/releases/<that version>.md
    to exist, and precut_review.py forbids it a cut commit line before the
    cut, because the cut writes that line itself. So the file is there,
    written by hand, naming no revision. It is not the note of a release yet.

    ONE PREDICATE, THREE READERS, each of which lacked this fact (measured
    2026-10-05 and 2026-10-06 on the 1.1.0 candidate). previous_cut_commit
    below read the draft as the newest release and found no cut to read, so
    the changelog check was NO-DATA for good. refresh_cut.refuse_if_note_moved
    compared the generator's output against the draft as if it were a cut
    note that moved. reproduce_export.py, rebuilding the tag from the revision
    its note names, found the draft in that revision and compared it with the
    tag's note: a published tag failing its own closeout.

    FAIL DIRECTION: unknown blocks. Anything that is not exactly that draft
    reads False, and False leaves each caller on the path it had before this
    predicate existed (NO-DATA here, REFUSED in refresh_cut, MISMATCH in
    reproduce_export): a root, a version or a note that is not text; a note
    that is empty or only white space, which is a missing note and never a
    draft; a note that carries any mark of the generator (GENERATED_NOTE_MARKS)
    in any case, spacing or line wrapping, so a finished note whose cut line
    was deleted, reshaped or wrapped is still a release note; manifests that
    are missing, unreadable, or declare any other version. Nothing wider: a
    note of any other version that lacks its cut line is no draft of this
    cut. KNOWN CEILING: a finished note stripped by hand of every generator
    mark at once reads as a draft, because nothing in its text then says
    otherwise; precut_review.py compares finished notes against their anchor
    copies and is where that edit is caught."""
    if not isinstance(root, str) or not root:
        # An empty root would read the manifests of the working directory.
        return False
    if not isinstance(note_text, str):
        return False
    if not note_text.split() or is_generated_note(note_text):
        return False
    try:
        with open(os.path.join(root, DECLARED_VERSION_REL), encoding="utf-8") as fh:
            declared = json.load(fh)["metadata"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return isinstance(declared, str) and declared == version


def previous_cut_commit(root, version=None):
    """The hub commit the newest release note says it was cut from. `version`
    is the release being cut: its own uncut draft (is_uncut_draft above) is
    not a release yet, so it is looked past and the newest note that is not
    that draft answers. Nothing else is looked past. With no version, or when
    the newest note is of any other version, a note without its cut line is
    still None, which the caller reads as NO-DATA and the cut blocks on. A
    note that cannot be read (not a file, not UTF-8) is None too, wherever it
    sits among the candidates: an unreadable note is never looked past."""
    _require_text(root, "root")
    if version is not None:
        _require_text(version, "version")
    d = os.path.join(root, "docs", "releases")
    try:
        names = os.listdir(d)
    except OSError:
        return None
    notes = []
    for name in names:
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)\.md$", name)
        if m:
            notes.append((tuple(int(x) for x in m.groups()), name))
    for _key, name in sorted(notes, reverse=True):
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            return None
        if name == "%s.md" % version and is_uncut_draft(root, version, text):
            continue
        m = CUT_COMMIT_LINE.search(text)
        return m.group(1) if m else None
    return None


@contextlib.contextmanager
def no_git_location():
    """This process, for one block, as if no git hook had launched it. git
    exports GIT_DIR to its hooks; with it set, every git command below
    answers for THAT repository whatever its cwd says. Measured 2026-09-20,
    twice: tests wrote core.bare and a fixture identity into the estate's
    repository, and the exporter built its tree from the wrong one."""
    import tmp_sandbox
    saved = {k: os.environ[k] for k in tmp_sandbox.GIT_LOCATION_VARS
             if k in os.environ}
    tmp_sandbox.drop_git_location()
    try:
        yield
    finally:
        os.environ.update(saved)


#: In every export tree. Its absence means the tree is not one.
EXPORT_TREE_MARKER = os.path.join("scripts", "required_fast.sh")


def export_tree_builder(root, cache=False):
    """build(dest): the exporter's own function, so the tree is the one the
    public repository receives and never this module's idea of it. FR1.b: with
    cache=True, dest first becomes a detached worktree of the one export tree
    built per (source tree, exporter, allowlist) by export_tree_cache; any
    doubt there falls through to the build below, so the tree is the same
    either way. Only the cut path asks for the cache."""
    import export_public
    # THE ALLOWLIST IS ROOT'S, not this module's (D13, 2026-10-02): the landing runs this builder from a frozen copy of the
    # base commit against the landing tree, and which files the public repository receives is that tree's own law. When
    # root is this checkout the path is the exporter's default, byte for byte.
    allow, top = getattr(export_public, "DEFAULT_ALLOWLIST", None), getattr(export_public, "ROOT", None)
    allowlist = os.path.join(root, os.path.relpath(allow, top)) if isinstance(allow, str) and isinstance(top, str) else None

    def build(dest):
        if cache:
            import export_tree_cache
            lease, _why = export_tree_cache.lease_into(
                dest, root, build=lambda d: build_plain(d), exporter=getattr(export_public, "__file__", None) or "",
                allowlist=allowlist or "")
            if lease is not None:
                return
        build_plain(dest)

    def build_plain(dest):
        # The exporter narrates on stdout; a verdict stream stays verdicts.
        with contextlib.redirect_stdout(sys.stderr), no_git_location():
            export_public.build_orphan_commit(dest, export_public.load_allowlist(allowlist),
                                              root)
        if not os.path.isfile(os.path.join(dest, EXPORT_TREE_MARKER)):
            raise RuntimeError("the built tree has no %s, so it is not an "
                               "export tree" % EXPORT_TREE_MARKER)
    return build


def export_tree_id(root, build=None):
    """(tree id, '') of the export tree built from `root` (the exporter's own builder), or ('', reason). RC1.b1: the
    git tree id names the bytes only, never the commit, so two builds of the same bytes agree; it is the key a cut
    rehearsal row is recorded under and reused on. A build that raises, a folder with no commit, or a value that is
    not 40 hex is the reason, never an id."""
    _require_text(root, "root")
    if build is not None and not callable(build):
        raise ValueError("build must be callable or None, got %r" % (build,))
    build = build or export_tree_builder(root, cache=True)
    tree = tempfile.mkdtemp(prefix="cut-preflight-tree-id-")
    try:
        try:
            build(tree)
        except Exception as exc:  # sbe: allow-silent returned as the reason, never swallowed
            return "", "the export tree could not be built: %s" % exc
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        proc = _run(["git", "rev-parse", "HEAD^{tree}"], tree, env=env, timeout=120)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", out):
            return "", "the built export tree has no readable commit (git rev-parse exit %d)" % proc.returncode
        return out, ""
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def runner_env(home):
    """The public runner's conditions: HOME empty and no git identity. An
    exported GIT_AUTHOR_NAME outranks the identity a test gives its own
    repository and reads as a red the runner never sees (measured 2026-09-20
    on the integrate suite)."""
    # R1.1: the single conditions entry point refuses corrupt or missing HOME
    # state with ValueError, never a crash and never a silent accept. A HOME
    # that is a file, a missing path, an empty string or a non-string all
    # refuse. A HOME carrying the prior run marker refuses as already used.
    # Stale content from a prior run is cleaned so the new run starts empty.
    if not isinstance(home, str) or not home:
        raise ValueError("home must be a non-empty string, got %r" % (home,))
    if not os.path.isdir(home):
        raise ValueError("home must be an existing directory, got %r" % (home,))
    used = os.path.join(home, ".cut_preflight_used")
    if os.path.exists(used):
        raise ValueError("HOME %r was already used by a prior run" % (home,))
    for name in os.listdir(home):
        path = os.path.join(home, name)
        try:
            if os.path.isdir(path) and not os.path.islink(path):
                os.rmdir(path)
            else:
                os.remove(path)
        except OSError as exc:
            raise ValueError("HOME %r could not be cleaned: %s" % (home, exc))
    # Every GIT_ variable, not the identity alone: git exports GIT_DIR to its
    # hooks, and tests launched from the pre-push hook on 2026-09-20 aimed
    # their `git config` and `git commit` at the REAL repository (core.bare,
    # core.hooksPath and a fixture identity were written into it).
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["HOME"] = home
    return env


def check_virgin_gate(root, runner=None, build=None, keep=None):
    """P5: required_fast.sh on an export shaped tree, HOME empty. `keep` (RC1.b1): where the full output is written,
    instead of the temp folder default."""
    # R1.3: hostile API input is refused before a temp tree or a temp HOME is
    # made, so the refusal is this module's own and never a TypeError from
    # inside the run.
    _require_text(root, "root")
    if runner is not None and not callable(runner):
        raise ValueError("runner must be callable or None, got %r" % (runner,))
    if build is not None and not callable(build):
        raise ValueError("build must be callable or None, got %r" % (build,))
    build = build or export_tree_builder(root, cache=True)
    tree = tempfile.mkdtemp(prefix="cut-preflight-export-")
    home = tempfile.mkdtemp(prefix="cut-preflight-home-")
    try:
        try:
            build(tree)
        except Exception as exc:  # sbe: allow-silent reported as NO-DATA below, never swallowed
            return NODATA, "virgin-gate", "the export tree could not be built: %s" % exc
        env = runner_env(home)
        env["BROTHER_EXPORT_TREE"] = "1"   # the export tree's own run: kernel_closure.py --check SKIPs on it (hub list)
        proc = _run(["sh", os.path.join(tree, "scripts", "required_fast.sh")],
                    tree, runner, env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        # The tree is deleted below, so the whole output is kept beside it.
        keep = keep or os.path.join(tempfile.gettempdir(),
                                    "cut-preflight-virgin-gate-%d.txt" % os.getpid())
        try:
            with open(keep, "w", encoding="utf-8") as fh:
                fh.write(out)
        except OSError as exc:
            keep = "(could not save: %s)" % exc
        m = FAST_SUMMARY.search(out)
        if not m:
            return NODATA, "virgin-gate", ("required_fast.sh printed no pass/fail/"
                                           "no-data line (exit %d): %s"
                                           % (proc.returncode, _last(proc)))
        failed = re.findall(r"^FAILED: (.+)$", out, re.M)
        summary = ("export tree, empty HOME: pass %s fail %s no-data %s"
                   % m.groups()) + "  [full: %s]" % keep
        if int(m.group(2)) or proc.returncode != 0:
            return REFUSED, "virgin-gate", "%s%s" % (
                summary, (" FAILED: " + failed[-1]) if failed else "")
        return OK, "virgin-gate", summary
    finally:
        shutil.rmtree(tree, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)


#: FX2.a (2026-10-11): the exporter's own dry run, its required_fast proof
#: included, under this machine's real HOME. The 1.1.1 cut met a drifted
#: ~/.claude/bin loop in the exporter's loop-tool-parity row only after the y,
#: because the rehearsal and the virgin gate both ran under an empty HOME.
EXPORT_ROW_NAME = "export-real-home"
EXPORT_ROW_TIMEOUT_S = 3600


def real_home_refusal(home=None):
    """'' when `home` (default: this user's home) is an absolute, existing folder holding .claude, else the reason.
    Never raises. A home with no .claude is an empty machine: a run there measures what the virgin gate measures."""
    if home is None:
        home = os.path.expanduser("~")
    if not isinstance(home, str) or not home:
        return "HOME is not a path (%r)" % (home,)
    if not os.path.isabs(home):
        return "HOME %r is not an absolute path" % home
    if not os.path.isdir(home):
        return "HOME %r is not an existing folder" % home
    if not os.path.isdir(os.path.join(home, ".claude")):
        return "HOME %r holds no .claude folder, so there is no machine state to measure" % home
    return ""


def real_home_env(home, environ=None):
    """`environ` (default os.environ) without any GIT_ key (runner_env's measured reason), HOME set to `home`.
    Every other key is kept: the rows that use it exist to see this machine's state."""
    environ = os.environ if environ is None else environ
    env = {k: v for k, v in environ.items() if not k.startswith("GIT_")}
    env["HOME"] = home
    return env


def check_export_real_home(root, remote, runner=None, home=None, tag_time_checks=False, keep=None):
    """FX2.a: export_public.py --dry-run --prove-required-fast from the checkout root under the real HOME, never
    --push. 0 is OK, 1 is REFUSED, every other exit (2, a timeout 124, a missing binary 127) is NO-DATA.
    RC1.b1: `tag_time_checks` adds --tag-time-checks (the cut's one dry run on the tree that ships); `keep` is where
    the full output is written, instead of the temp folder default."""
    _require_text(root, "root")
    _require_text(remote, "remote")
    home = os.path.expanduser("~") if home is None else home
    why = real_home_refusal(home)
    if why:
        return NODATA, EXPORT_ROW_NAME, "not run: %s" % why
    proc = _run([sys.executable, os.path.join(root, "scripts", "export_public.py"), "--dry-run",
                 "--prove-required-fast"] + (["--tag-time-checks"] if tag_time_checks else [])
                + ["--remote", remote], root, runner, env=real_home_env(home),
                timeout=EXPORT_ROW_TIMEOUT_S)
    out = (proc.stdout or "") + (proc.stderr or "")
    keep = keep or os.path.join(tempfile.gettempdir(), "cut-preflight-export-real-home-%d.txt" % os.getpid())
    try:
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write(out)
    except OSError as exc:
        keep = "(could not save: %s)" % exc
    marked = [l.strip() for l in out.splitlines() if l.startswith(("REFUSED:", "FAILED:", "NO-DATA:"))]
    detail = "export_public --dry-run --prove-required-fast, HOME kept: %s  [full: %s]" % (
        "; ".join(marked[:3]) if marked else _last(proc), keep)
    if proc.returncode == 0:
        return OK, EXPORT_ROW_NAME, detail
    if proc.returncode == 1:
        return REFUSED, EXPORT_ROW_NAME, detail
    return NODATA, EXPORT_ROW_NAME, "exit %d: %s" % (proc.returncode, detail)


#: FX2.b (2026-10-11): the required_fast rows that read this machine's own state, run here under the real HOME
#: in the cheap tier. The commands are the ones scripts/required_fast.sh runs for loop-tool-parity, loop-wiring,
#: loop-model-routing, integrate (narrowed to the class that lists the real projects folder) and
#: loop-ownership-race. model-registry proves the registry that wins model_router's load order parses and routes;
#: it does not compare the installed copy with the repository copy. Adding a row is one line here.
MACHINE_ROW_TIMEOUT_S = 600
MACHINE_ROWS = (("loop-tool-parity", ("scripts/test_loop_tool_parity.py",)),
                ("loop-wiring", ("scripts/test_loop_wiring.py", "--max", "4")),
                ("model-registry", ("scripts/loop/model_router.py", "--selftest")),
                ("projects-dir", ("scripts/test_integrate.py", "TranscriptHygieneRetiresOnlyTheVanishedWorktree")),
                ("installed-guard", ("scripts/test_loop_guard_race.py",)))


def check_machine_row(root, name, argv, runner=None, home=None):
    """One machine row: NO-DATA without a process for a home that cannot be this machine; exit 0 with a line
    starting NOT APPLICABLE is NO-DATA (it measured nothing); exit 0 otherwise OK; 2, 124 and 127 NO-DATA; any
    other exit REFUSED."""
    home = os.path.expanduser("~") if home is None else home
    why = real_home_refusal(home)
    if why:
        return NODATA, name, "not run: %s" % why
    proc = _run([sys.executable, os.path.join(root, argv[0])] + list(argv[1:]), root, runner,
                env=real_home_env(home), timeout=MACHINE_ROW_TIMEOUT_S)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        na = [l.strip() for l in out.splitlines() if l.startswith("NOT APPLICABLE")]
        if na:
            return NODATA, name, "measured nothing on this machine: %s" % na[0][:200]
        return OK, name, _last(proc)
    if proc.returncode in (2, 124, 127):
        return NODATA, name, "exit %d: %s" % (proc.returncode, _last(proc))
    return REFUSED, name, "exit %d: %s" % (proc.returncode, _last(proc))


def check_machine_rows(root, runner=None, home=None):
    """The MACHINE_ROWS tuples in table order, one process each, in series."""
    return [check_machine_row(root, name, argv, runner, home) for name, argv in MACHINE_ROWS]


def check_pool_floor(root=None, free=None):
    """CS8.b: the pool's disk floor, read in the cheap tier before any export row, so a 14.9 GiB reading refuses in a
    fraction of a second instead of after the minutes the pool would first spend. `root` is None (the pool's one scratch
    root, perturb_pool.pool_scratch_root) or a path inside it: a roomy path anywhere else is never read in its place.
    `free` is None (the real reading), a callable asked for the root, or the reading itself. An unreadable or invalid
    reading (bool, string, None, NaN, infinity, negative) is NO-DATA, never OK."""
    import perturb_pool
    try:
        top = perturb_pool.pool_scratch_root()
    except (perturb_pool.CopyFailed, OSError):
        return (NODATA, "perturb-floor", "the pool scratch root is unreadable")
    if root is None:
        root = top
    if (not isinstance(root, str) or not root or perturb_pool.FORBIDDEN_PATH_TEXT in root
            or not (os.path.realpath(root) == os.path.realpath(top) or perturb_pool._under(root, top))):
        return (NODATA, "perturb-floor", "the pool scratch root is unreadable")
    try:
        if free is None:
            free = perturb_pool.free_gib
        reading = free(root) if callable(free) else free
    except (OSError, TypeError, ValueError):
        return (NODATA, "perturb-floor", "free space is unreadable")
    if not perturb_pool.valid_reading(reading):
        return (NODATA, "perturb-floor", "the free reading is invalid")
    floor = perturb_pool.DISK_FLOOR_GIB
    named = "" if floor == perturb_pool.DEFAULT_DISK_FLOOR_GIB else (
        " (BROTHER_DISK_FLOOR_GIB=%d, the owner's override of the %d GiB default)" % (floor, perturb_pool.DEFAULT_DISK_FLOOR_GIB))
    if reading < floor:
        return (REFUSED, "perturb-floor", "%.1f GiB free is under the %d GiB floor%s" % (reading, floor, named))
    return (OK, "perturb-floor", "free space is at or above the %d GiB floor%s" % (floor, named))


#: FC1.a (2026-10-11): a NO-DATA row spares the expensive rows exactly as a
#: REFUSED row does. Neither can be turned into OK by a later row, so the exit
#: code is 1 or 2 whatever plugin-bumps and the virgin gate would answer.
SPARED = "not run: a cheaper check did not clear (REFUSED or NO-DATA)"


def _cleared(results):
    """True only when every row's verdict is OK; an unrecognised verdict never clears."""
    return all(r[0] == OK for r in results)


def _bump_copy_list(root):
    """Relative paths (forward slashes, first seen order) of every file the
    version rule reads or the bump writes: BUMP_FILES, the rule's script, every
    bundle host manifest, and every catalog source's plugin manifest. A
    marketplace that is not a JSON object with a list of plugins, or names a
    source outside the tree, raises ValueError; a missing one adds nothing
    (the bump itself reports it)."""
    out = []
    for rel in BUMP_FILES + (VERSION_TRUTH_REL.replace(os.sep, "/"),):
        if rel not in out:
            out.append(rel)
    bundle = os.path.join(root, "bundle")
    if os.path.isdir(bundle):
        for entry in sorted(os.listdir(bundle)):
            if os.path.isdir(os.path.join(bundle, entry)):
                rel = "bundle/%s/plugin.json" % entry
                if rel not in out:
                    out.append(rel)
    try:
        with open(os.path.join(root, BUMP_FILES[0]), encoding="utf-8") as fh:
            market = json.load(fh)
    except FileNotFoundError:
        return out
    if not isinstance(market, dict) or not isinstance(market.get("plugins", []), list):
        raise ValueError("%s is not a catalog object with a list of plugins" % BUMP_FILES[0])
    for entry in market.get("plugins", []):
        source = entry.get("source") if isinstance(entry, dict) else None
        path = source.get("path") if isinstance(source, dict) else None
        if not isinstance(path, str) or not path:
            continue
        norm = os.path.normpath(path)
        if os.path.isabs(path) or norm == ".." or norm.startswith(".." + os.sep):
            raise ValueError("catalog source path %r is outside the tree" % path)
        rel = "%s/.claude-plugin/plugin.json" % norm.replace(os.sep, "/")
        if rel not in out:
            out.append(rel)
    return out


def bumped_scratch(root, version, runner=None):
    """(scratch, None) with a new directory the caller removes, holding the
    files the version rule reads after the ROOT's own bump script ran on that
    copy; or (None, (verdict, VERSION_ROW, detail)) with the scratch already
    removed. A version that is not X.Y.Z refuses before anything runs; a
    missing script, an unreadable catalog, a copy error and every bump exit
    but 0 and 1 are NO-DATA; bump exit 1 refuses. The root is never written."""
    _require_text(root, "root")
    _require_text(version, "version")
    if not SEMVER.fullmatch(version):
        return None, (REFUSED, VERSION_ROW, "version %r is not X.Y.Z, so the cut's bump step "
                                            "would refuse it" % (version,))
    for rel in (VERSION_SOURCE_REL, VERSION_TRUTH_REL):
        if not os.path.isfile(os.path.join(root, rel)):
            return None, (NODATA, VERSION_ROW, "%s is missing" % rel)
    try:
        listed = _bump_copy_list(root)
    except (ValueError, OSError) as exc:
        return None, (NODATA, VERSION_ROW, "cannot list the files the version rule reads: %s" % exc)
    scratch = tempfile.mkdtemp(prefix="cut-preflight-bump-")
    try:
        for rel in listed:
            src = os.path.join(root, rel)
            if not os.path.isfile(src):
                continue
            dest = os.path.join(scratch, rel)
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(src, dest)
            except OSError as exc:
                shutil.rmtree(scratch, ignore_errors=True)
                return None, (NODATA, VERSION_ROW, "cannot copy %s: %s" % (rel, exc))
        if os.path.isdir(os.path.join(root, "products", "brothermode")):
            os.makedirs(os.path.join(scratch, "products", "brothermode"), exist_ok=True)
        proc = _run([sys.executable, os.path.join(root, VERSION_SOURCE_REL), "--write", "--version", version,
                     "--root", scratch], root, runner, timeout=120)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    if proc.returncode == 0:
        return scratch, None
    shutil.rmtree(scratch, ignore_errors=True)
    detail = "the cut's own bump step stops on this tree (exit %d): %s" % (proc.returncode, _last(proc))
    return None, (REFUSED if proc.returncode == 1 else NODATA, VERSION_ROW, detail)


#: N24 (2026-10-11): the pages that describe the present. Frozen records
#: (release notes, plans, decisions, architecture, handovers, product
#: histories) legitimately say what was then planned and stay out. Adding a
#: page is one entry here plus one fixture case.
NO_PROMISE_NAME = "no-promise"
CURRENT_DOCS = ("README.md", "AGENTS.md", "docs/VERSIONING.md", "docs/CHARTER.md", "docs/README.md",
                "docs/how-to/*.md")
_PARAGRAPH_START = re.compile(r"^\s*(?:[-*+]\s|[0-9]+[.)]\s|#|\|)")


def version_tuple(text):
    """(1, 1, 2) for "1.1.2"; None for anything that is not exactly X.Y.Z text."""
    if not isinstance(text, str) or not SEMVER.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split("."))


def declared_version(root):
    """metadata.version of the root's marketplace file when it is X.Y.Z, else None."""
    try:
        with open(os.path.join(root, DECLARED_VERSION_REL), encoding="utf-8") as fh:
            declared = json.load(fh)["metadata"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return declared if version_tuple(declared) else None


def current_docs(root):
    """The existing files CURRENT_DOCS names under root, relative, sorted."""
    found = set()
    for pattern in CURRENT_DOCS:
        for path in glob.glob(os.path.join(root, pattern)):
            if os.path.isfile(path):
                found.add(os.path.relpath(path, root).replace(os.sep, "/"))
    return sorted(found)


def promise_hits(text, version):
    """[(line, rule, sentence)] for every sentence that promises work to `version` (planned-for, is-item) or calls
    an older version the current release (older-current). Read by paragraph, its lines joined by single spaces, so
    a promise split over two lines is found at the line where it starts."""
    want = version_tuple(version)
    v = r"(?<![0-9.])%s(?![0-9]|[.][0-9])" % re.escape(version)
    rules = (("planned-for", re.compile(r"\bplanned\s+for\s+" + v, re.I)),
             ("is-item", re.compile(r"\b(?:is|are)\s+(?:an?\s+)?" + v + r"\s+items?\b", re.I)),
             ("older-current", re.compile(r"\b(\d+\.\d+\.\d+)\b[^.]{0,60}?\bthe\s+current\s+release\b", re.I)))
    paragraphs, current = [], []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or _PARAGRAPH_START.match(line):
            if current:
                paragraphs.append(current)
            current = [(number, line.strip())] if line.strip() else []
        else:
            current.append((number, line.strip()))
    if current:
        paragraphs.append(current)
    hits = []
    for para in paragraphs:
        joined, starts = "", []
        for number, line in para:
            starts.append((len(joined), number))
            joined += line + " "
        for rule, pattern in rules:
            for m in pattern.finditer(joined):
                if rule == "older-current" and not (version_tuple(m.group(1)) and want
                                                    and version_tuple(m.group(1)) < want):
                    continue
                line_no = [n for off, n in starts if off <= m.start()][-1]
                around = " ".join(joined[max(m.start() - 40, 0):m.end() + 40].split())[:160]
                hits.append((line_no, rule, around))
    return sorted(hits)


def check_no_promise(root, version):
    """N24: (verdict, 'no-promise', detail). REFUSED when a current page promises work to `version` or calls an
    older version the current release; NO-DATA when the version is not X.Y.Z, no current page exists, or a page
    cannot be read and nothing refused; OK otherwise."""
    _require_text(root, "root")
    if version_tuple(version) is None:
        return NODATA, NO_PROMISE_NAME, "version %r is not X.Y.Z" % (version,)
    docs = current_docs(root)
    if not docs:
        return NODATA, NO_PROMISE_NAME, "no current page found (%s)" % ", ".join(CURRENT_DOCS)
    hits, unreadable = [], []
    for rel in docs:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            unreadable.append(rel)
            continue
        hits.extend("%s:%d [%s] %s" % (rel, line, rule, sentence)
                    for line, rule, sentence in promise_hits(text, version))
    unread = ("; unreadable: %s" % ", ".join(unreadable)) if unreadable else ""
    if hits:
        return REFUSED, NO_PROMISE_NAME, "%d promise(s) to %s: %s%s" % (len(hits), version, "; ".join(hits[:3]),
                                                                        unread)
    if unreadable:
        return NODATA, NO_PROMISE_NAME, "no promise found, but %s could not be read as UTF-8" % ", ".join(unreadable)
    return OK, NO_PROMISE_NAME, "no current page promises work to %s (%d page(s) read)" % (version, len(docs))


def check_version_after_bump(root, version, runner=None):
    """N4.b: the version rule (scripts/test_version_truth.py) run from the
    bumped scratch copy, inside it. Exit 0 is OK; exit 1 with a FAILED line
    refuses with the rule's first lines (the file and line it names); any
    other answer, a crash included, is NO-DATA. A bump failure returns
    bumped_scratch's own verdict; the scratch is removed on every path."""
    scratch, failed = bumped_scratch(root, version, runner)
    if failed:
        return failed
    try:
        proc = _run([sys.executable, os.path.join(scratch, VERSION_TRUTH_REL)], scratch, runner, timeout=120)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    lines = [l.strip() for l in ((proc.stdout or "") + (proc.stderr or "")).splitlines() if l.strip()]
    if proc.returncode == 0:
        return OK, VERSION_ROW, "the version rule holds after the bump to %s" % version
    if proc.returncode == 1 and any(l.startswith("FAILED") for l in lines):
        return REFUSED, VERSION_ROW, ("after the bump to %s: " % version + " | ".join(lines[:3]))[:300]
    return NODATA, VERSION_ROW, "the version rule exited %d: %s" % (proc.returncode, _last(proc))



#: RDM1 (2026-10-29), the owner's cut gate AC2 of 2026-10-23: three rows that read a tree's records, never run a
#: gate. Every unreadable or unknown input REFUSES; a record that does not exist yet REFUSES too, never passes.
README_ROW = "readme-section"
X01_ROW = "x01-macos"
MEASURE_ROW = "hand-re-measure"
JOURNEYS = ("J1", "J2")
MANIFEST_REL = os.path.join("bundle", "runtime", "RUNTIME-MANIFEST.json")
#: the first release each gate is asked of: the README section rule is the 1.1 line's (docs/releases/1.1.*.md), the
#: journey records and the hand re-measure exist from 1.1.9.
README_SINCE = (1, 1, 0)
JOURNEY_SINCE = (1, 1, 9)


def check_readme_section(root, version):
    """RDM1.a: the README of `root` carries `## What changed in <version>`, by the rule of
    scripts/test_readme_changed_sections.py (a heading inside a code fence or of a longer version does not
    count). The 1.1.7 cut shipped without its section. A README that is missing or not UTF-8 refuses."""
    _require_text(root, "root")
    _require_text(version, "version")
    try:
        import test_readme_changed_sections as rule  # lazy: the rule lives in one place
    except ImportError as exc:
        return NODATA, README_ROW, "the README rule cannot be loaded (%s)" % exc
    try:
        text = rule.read_readme(os.path.join(root, "README.md"))
    except (OSError, UnicodeDecodeError) as exc:
        return REFUSED, README_ROW, "README.md cannot be read as UTF-8 text (%s)" % type(exc).__name__
    if rule.missing_sections(text, [version]):
        return REFUSED, README_ROW, "README.md has no '## What changed in %s' section" % version
    return OK, README_ROW, "README.md carries the '## What changed in %s' section" % version


def candidate_identity(root):
    """The sha256 of root's bundle runtime manifest, the identity go_no_go_119.py and the journey walker bind a
    record by; '' when it cannot be read."""
    import hashlib
    try:
        with open(os.path.join(root, MANIFEST_REL), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def _read_record(path):
    """(dict, '') or (None, why): a JSON object, or the reason there is none."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return None, "%s does not exist" % os.path.basename(path)
    except (OSError, ValueError) as exc:    # UnicodeDecodeError is a ValueError
        return None, "%s cannot be read (%s)" % (os.path.basename(path), type(exc).__name__)
    if not isinstance(doc, dict):
        return None, "%s is not a JSON object" % os.path.basename(path)
    return doc, ""


def claimed_rows(root):
    """{journey: set of row ids} the release plan's journey_rows claims, or None when no plan carries that key.

    D1 and D2 (2026-10-29, the rule go_no_go_119.row_journeys_fresh already applies): the cut line claims the rows
    its kept units own, so a row a slipped unit owns is not judged. Without a readable plan or without the key the
    answer is None and every row is judged: unknown closes the gate, it never opens it."""
    try:
        import release_window_clock_rows as clock
        path = clock.release_plan_path(root)
        if not path:
            return None
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (ImportError, OSError, ValueError, TypeError):
        return None
    claims = plan.get("journey_rows") if isinstance(plan, dict) else None
    if not isinstance(claims, dict):
        return None
    out = {}
    for rid, journey in claims.items():
        if isinstance(rid, str) and isinstance(journey, str):
            out.setdefault(journey, set()).add(rid)
    return out


def _journey_problem(doc, journey, want, claimed=None):
    """The first reason a macOS journey record cannot stand for the candidate `want`, or ''. With `claimed` (the
    row ids the release claims for this journey) only those rows are judged, and a claimed row the record lacks
    refuses; with None every row is judged."""
    if doc.get("journey") != journey:
        return "names journey %r" % (doc.get("journey"),)
    if doc.get("host") != "macos":      # a missing or other host (ubuntu included) never stands in
        return "host is %r, not 'macos'" % (doc.get("host"),)
    if doc.get("identity") != want or doc.get("candidate_identity") != want:
        return "is for another commit (identity %.12s, candidate %.12s, want %.12s)" % (
            doc.get("identity"), doc.get("candidate_identity"), want)
    if doc.get("clean_home") is not True:
        return "does not claim a clean home"
    bins = doc.get("claude_bin")
    if not isinstance(bins, list):
        return "carries no claude_bin list, so a .claude/bin in the walk's HOME is unknown"
    if bins:
        return "its HOME held a .claude/bin (%.80s)" % ", ".join(str(b) for b in bins)
    rows = doc.get("rows")
    if not isinstance(rows, dict) or not rows:
        return "carries no rows"
    if claimed is not None:
        if not claimed:
            return "is claimed for no row by the release plan, so nothing in it can pass X01"
        missing = sorted(r for r in claimed if r not in rows)
        if missing:
            return "lacks claimed row(s): %.120s" % ", ".join(missing)
        rows = {k: v for k, v in rows.items() if k in claimed}
    bad = sorted(str(k) for k, v in rows.items() if v != "PASS")
    if bad:
        return "rows not PASS: %.120s" % ", ".join(bad)
    return ""


def check_x01(root):
    """RDM1.b, the macOS half of X01: J1 and J2 on a clean macOS home, bound to the candidate being cut, every row
    PASS, no .claude/bin. The Linux half is cut_driver's (ubuntu_x01_gate). One refusal names the first reason."""
    _require_text(root, "root")
    want = candidate_identity(root)
    if not want:
        return NODATA, X01_ROW, "%s cannot be read, so the candidate has no identity" % MANIFEST_REL
    claims = claimed_rows(root)
    for journey in JOURNEYS:
        name = "JOURNEY-%s.json" % journey
        doc, why = _read_record(os.path.join(root, "docs", "plan", name))
        if doc is None:
            return REFUSED, X01_ROW, why
        problem = _journey_problem(doc, journey, want, None if claims is None else claims.get(journey, set()))
        if problem:
            return REFUSED, X01_ROW, "%s %s" % (name, problem)
    judged = "every row" if claims is None else "the rows the release plan claims"
    return OK, X01_ROW, "J1 and J2 are PASS on a clean macOS home for candidate %.12s (%s)" % (want, judged)


def remeasure_journeys(root):
    """The journeys the hand re-measure must score: J1 and J2 (X01's two) plus every journey the release plan
    claims a row of and that has a record under docs/plan. The release publishes what it measured of what it
    claims, so a claimed journey with a walk record is re-measured too; without a plan it is J1 and J2."""
    out = list(JOURNEYS)
    for journey in sorted(claimed_rows(root) or {}):
        if journey not in out and os.path.isfile(os.path.join(root, "docs", "plan", "JOURNEY-%s.json" % journey)):
            out.append(journey)
    return out


def remeasure_scores(root, version):
    """({journey: (total, maximum)}, '') derived from the hand re-measure record after the exact validation
    check_re_measure applies, or ({}, why) when it does not stand. THE ONE MEASUREMENT CONTRACT (2026-10-30): the
    published rubric is cells of 0 to 2 per journey row, so a journey's score is the sum of its cells out of two per
    row; this is the only derivation, read by cut_preflight and by go_no_go_119.row_claims_match_measured alike,
    so valid evidence satisfies both and nothing else counts as a score."""
    verdict, _name, detail = check_re_measure(root, version)
    if verdict != OK:
        return {}, detail
    doc, _why = _read_record(os.path.join(root, "docs", "plan", "REMEASURE-%s.json" % version))
    scores = {}
    for journey in remeasure_journeys(root):
        rec, _why = _read_record(os.path.join(root, "docs", "plan", "JOURNEY-%s.json" % journey))
        rows = sorted(rec["rows"])
        total = sum(doc["cells"]["%s/%s" % (journey, rid)]["score"] for rid in rows)
        scores[journey] = (total, 2 * len(rows))
    return scores, ""


def check_re_measure(root, version):
    """RDM1.c: docs/plan/REMEASURE-<version>.json for this candidate names a seat that is not the one that walked
    the journeys, and scores every row of every re-measured journey (remeasure_journeys) with its evidence (score
    0, 1 or 2, an int, never a bool)."""
    _require_text(root, "root")
    _require_text(version, "version")
    want = candidate_identity(root)
    if not want:
        return NODATA, MEASURE_ROW, "%s cannot be read, so the candidate has no identity" % MANIFEST_REL
    name = "REMEASURE-%s.json" % version
    doc, why = _read_record(os.path.join(root, "docs", "plan", name))
    if doc is None:
        return REFUSED, MEASURE_ROW, why
    if doc.get("candidate_identity") != want:
        return REFUSED, MEASURE_ROW, "%s is for another candidate" % name
    seat = doc.get("seat")
    if not isinstance(seat, str) or not seat.strip():
        return REFUSED, MEASURE_ROW, "%s names no seat" % name
    cells = doc.get("cells")
    if not isinstance(cells, dict):
        return REFUSED, MEASURE_ROW, "%s carries no cells" % name
    wanted = []
    for journey in remeasure_journeys(root):
        rec, why = _read_record(os.path.join(root, "docs", "plan", "JOURNEY-%s.json" % journey))
        if rec is None or not isinstance(rec.get("rows"), dict) or not rec["rows"]:
            return REFUSED, MEASURE_ROW, "the J%s record to re-measure cannot be read (%s)" % (journey[1:], why or "no rows")
        walker = rec.get("walked_by")
        if not isinstance(walker, str) or not walker.strip():
            return REFUSED, MEASURE_ROW, "JOURNEY-%s.json names no walked_by, so the seat cannot be told apart" % journey
        if walker.strip().lower() == seat.strip().lower():
            return REFUSED, MEASURE_ROW, "seat %r walked the journeys and may not re-measure them" % seat
        wanted.extend("%s/%s" % (journey, rid) for rid in sorted(rec["rows"]))
    for cell in wanted:
        entry = cells.get(cell)
        score = entry.get("score") if isinstance(entry, dict) else None
        evidence = entry.get("evidence") if isinstance(entry, dict) else None
        if isinstance(score, bool) or not isinstance(score, int) or score not in (0, 1, 2):
            return REFUSED, MEASURE_ROW, "cell %s is not scored 0, 1 or 2" % cell
        if not isinstance(evidence, str) or not evidence.strip():
            return REFUSED, MEASURE_ROW, "cell %s carries no evidence" % cell
    return OK, MEASURE_ROW, "seat %r scored all %d cells of %s with evidence" % (
        seat.strip(), len(wanted), " and ".join(remeasure_journeys(root)))

NO_CUT_COMMIT_NAME = "no-cut-commit"


def check_no_cut_commit(root, version, resume_sha="", runner=None):
    """CR1.a: (verdict, 'no-cut-commit', detail). A candidate whose tree
    already carries the generator's note for `version` holds a cut commit:
    the 1.1.2 cut stopped at the push with the release branch moved onto its
    own two commits, the fix landed on top, and the next rehearsal refused
    the stale note after 3,162 s. REFUSED unless `resume_sha` is exactly
    HEAD (the cut's own commit, adopted on purpose). OK for no note, the
    version's uncut draft, or any readable text with no generator mark.
    NO-DATA for a note that cannot be read or a HEAD that cannot be read:
    unknown is never looked past. One file read and at most one git call."""
    _require_text(root, "root")
    _require_text(version, "version")
    rel = "docs/releases/%s.md" % version
    path = os.path.join(root, "docs", "releases", "%s.md" % version)
    if not os.path.lexists(path):
        return OK, NO_CUT_COMMIT_NAME, "no release note for %s in this tree" % version
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return NODATA, NO_CUT_COMMIT_NAME, "%s cannot be read (%s)" % (rel, type(exc).__name__)
    if is_uncut_draft(root, version, text):
        return OK, NO_CUT_COMMIT_NAME, "%s is an uncut draft" % rel
    mark = generator_mark(text)
    if not mark:
        return OK, NO_CUT_COMMIT_NAME, "%s carries no mark of the generator" % rel
    proc = _run(["git", "rev-parse", "HEAD"], root, runner)
    head = (proc.stdout or "").strip()
    if proc.returncode != 0 or not head:
        return NODATA, NO_CUT_COMMIT_NAME, ("%s is a generated note and HEAD cannot be read (git rev-parse "
                                            "exit %d)" % (rel, proc.returncode))
    if resume_sha and resume_sha == head:
        return OK, NO_CUT_COMMIT_NAME, "%s is the cut's own commit %s, named by --resume" % (rel, head[:12])
    return REFUSED, NO_CUT_COMMIT_NAME, (
        "%s is a generated note (mark %r): HEAD %s already carries a cut commit; rehearse the candidate "
        "from before the cut commits, or name this commit with --resume if this is the adopt"
        % (rel, mark, head[:12]))


def run_all(root, version, horizon_hours=DEFAULT_HORIZON_HOURS, remote=None,
            runner=None, now=None, virgin=True, build=None, progress=None, home=None,
            resume_sha=""):
    """Every check, cheapest first (CR1.a: no-cut-commit, one file read,
    before all, and a refusal there ends the run); plugin-bumps, the export row and the virgin
    gate (minutes) each run only when every cheaper row is OK, so a one second
    red or NO-DATA costs one second."""
    if remote is None:
        import export_public
        remote = export_public.DEFAULT_REMOTE
    # R1.2: corrupt, missing or unrecognized input is REFUSED here, with a
    # ValueError before any check runs; never a silent accept, never a crash.
    if not isinstance(root, str) or not root:
        raise ValueError("root must be a non-empty string, got %r" % (root,))
    if not isinstance(version, str) or not version:
        raise ValueError("version must be a non-empty string, got %r" % (version,))
    horizon_hours = _finite_hours(horizon_hours)
    if now is not None and not isinstance(now, datetime.datetime):
        raise ValueError("now must be a datetime or None, got %r" % (now,))
    now = now or datetime.datetime.now()
    at = now + datetime.timedelta(hours=horizon_hours)
    first = check_no_cut_commit(root, version, resume_sha or "", runner)
    if first[0] != OK:
        return [first]
    # R1.2 cheapest first: the local commit text read runs before the network
    # read of the public remote, so a fraction of a second red costs that, and
    # the minutes long export tree gate below still runs only when nothing
    # cheaper refused.
    results = [
        first,
        check_exceptions(root, at, runner),
        check_pack_clock(root, horizon_hours, runner),
        check_readiness_rows(root, at, runner),
        check_release_plan(root, runner),
        check_no_promise(root, version),
        check_version_after_bump(root, version, runner),
        check_changelog_text(root, previous_cut_commit(root, version), runner),
        check_public_remote(root, version, remote, runner),
    ]
    # RDM1: the README, X01 and re-measure rows are asked of the lines that have their records (a version that does
    # not parse is asked all three: unknown BLOCKS, it is never skipped). Older lines carry no rows, not OK rows.
    asked = version_tuple(version)
    if asked is None or asked >= README_SINCE:
        results.append(check_readme_section(root, version))
    if asked is None or asked >= JOURNEY_SINCE:
        results.append(check_x01(root))
        results.append(check_re_measure(root, version))
    results.extend(check_machine_rows(root, runner, home))
    # CS8.b: the pool runs in step 2b of a full cut. A cheap-only preflight (the one a resumed cut asks, which skips 2b)
    # never uses the pool, so it never refuses a cut for the pool's disk.
    if virgin:
        results.append(check_pool_floor())
    # P6: about twenty seconds (one export build, one shallow fetch), so it
    # waits for the one second checks and still runs before the minutes long
    # gate. cut.py asks it again after the bump, on the tree that ships.
    if not _cleared(results):
        results.append((NODATA, "plugin-bumps", SPARED))
    else:
        import plugin_bump_gate
        results.append(plugin_bump_gate.check_plugin_bumps(root, version, remote,
                                                           runner, build))
    if virgin and _cleared(results):
        if progress:
            progress("export-real-home: export_public.py --dry-run --prove-required-fast under the real "
                     "HOME; minutes, and silent until it ends")
        results.append(check_export_real_home(root, remote, runner, home))
    elif virgin:
        results.append((NODATA, EXPORT_ROW_NAME, SPARED))
    if virgin and _cleared(results):
        if progress:
            progress("virgin-gate: required_fast.sh on an export shaped tree "
                     "under an empty HOME; minutes, and silent until it ends")
        results.append(check_virgin_gate(root, runner, build))
    elif virgin:
        results.append((NODATA, "virgin-gate", SPARED))
    return results


def exit_code(results):
    if any(r[0] == REFUSED for r in results):
        return EXIT_REFUSED
    if any(r[0] == NODATA for r in results):
        return EXIT_NODATA
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ask every gate that can refuse a "
                                 "release cut, before the cut starts.")
    ap.add_argument("--version", required=True, help="the release being cut, e.g. 1.0.22")
    ap.add_argument("--horizon-hours", type=float, default=DEFAULT_HORIZON_HOURS,
                    help="how long the cut may take; clock gates are read as of "
                         "now plus this (default %d)" % DEFAULT_HORIZON_HOURS)
    ap.add_argument("--remote", default=None, help="the public remote (default: "
                    "export_public.DEFAULT_REMOTE)")
    ap.add_argument("--no-virgin-gate", "--cheap-only", dest="no_virgin_gate", action="store_true",
                    help="skip the minutes long export rows (export-real-home and virgin-gate); the cheap "
                         "checks only. cut.py passes --cheap-only only on --resume with a covering "
                         "rehearsal record, whose rows the chain then reuses or runs")
    ap.add_argument("--resume", default="", metavar="SHA",
                    help="the cut's own commit, when this is the adopt: a generated release note of the "
                         "version is allowed only when HEAD is exactly this commit")
    ap.add_argument("--export-tree-id", action="store_true",
                    help="only build the export tree and print EXPORT-TREE: <git tree id> (NO-DATA and exit 2 when "
                         "it cannot be read); --version is required and ignored")
    args = ap.parse_args(argv)
    if args.export_tree_id:
        tree, why = export_tree_id(ROOT)
        print("EXPORT-TREE: %s" % tree if tree else "NO-DATA: %s" % why)
        return EXIT_OK if tree else EXIT_NODATA
    results = run_all(ROOT, args.version, args.horizon_hours, args.remote,
                      virgin=not args.no_virgin_gate,
                      progress=lambda line: print(line, flush=True), resume_sha=args.resume)
    for verdict, name, detail in results:
        print("%-8s %-16s %s" % (verdict, name, detail))
    code = exit_code(results)
    said = {EXIT_OK: "CLEAR", EXIT_REFUSED: "REFUSED", EXIT_NODATA: "NO-DATA"}[code]
    if code == EXIT_OK and args.no_virgin_gate:
        said += " (cheap rows only; export-real-home and virgin-gate were not asked)"
    print("cut-preflight: %s" % said)
    return code


if __name__ == "__main__":
    sys.exit(main())
