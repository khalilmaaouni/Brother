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

Exit 0 when every check is OK. Exit 1 when any check REFUSED. Exit 2 when
nothing refused but something could not be read (NO-DATA). cut.py blocks on
anything but 0: a cut that cannot read its own preflight is not ready.

HONEST LIMIT. The virgin gate runs on this machine's operating system; the
public runner is Linux. A red that needs Linux to appear is not caught here.
"""
import argparse
import contextlib
import datetime
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


def _run(cmd, cwd, runner=None, env=None, timeout=None):
    """One subprocess, never raising: a missing binary reads as exit 127 and
    a timeout as exit 124."""
    runner = runner or subprocess.run
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
    proc = _run(["git", "ls-remote", remote] + refs, root, runner)
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
    flat = " ".join(note_text.split()).lower()
    if not flat or any(mark in flat for mark in GENERATED_NOTE_MARKS):
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


def export_tree_builder(root):
    """build(dest): the exporter's own function, so the tree is the one the
    public repository receives and never this module's idea of it."""
    import export_public
    # THE ALLOWLIST IS ROOT'S, not this module's (D13, 2026-10-02): the landing runs this builder from a frozen copy of the
    # base commit against the landing tree, and which files the public repository receives is that tree's own law. When
    # root is this checkout the path is the exporter's default, byte for byte.
    allow, top = getattr(export_public, "DEFAULT_ALLOWLIST", None), getattr(export_public, "ROOT", None)
    allowlist = os.path.join(root, os.path.relpath(allow, top)) if isinstance(allow, str) and isinstance(top, str) else None

    def build(dest):
        # The exporter narrates on stdout; a verdict stream stays verdicts.
        with contextlib.redirect_stdout(sys.stderr), no_git_location():
            export_public.build_orphan_commit(dest, export_public.load_allowlist(allowlist),
                                              root)
        if not os.path.isfile(os.path.join(dest, EXPORT_TREE_MARKER)):
            raise RuntimeError("the built tree has no %s, so it is not an "
                               "export tree" % EXPORT_TREE_MARKER)
    return build


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


def check_virgin_gate(root, runner=None, build=None):
    """P5: required_fast.sh on an export shaped tree, HOME empty."""
    # R1.3: hostile API input is refused before a temp tree or a temp HOME is
    # made, so the refusal is this module's own and never a TypeError from
    # inside the run.
    _require_text(root, "root")
    if runner is not None and not callable(runner):
        raise ValueError("runner must be callable or None, got %r" % (runner,))
    if build is not None and not callable(build):
        raise ValueError("build must be callable or None, got %r" % (build,))
    build = build or export_tree_builder(root)
    tree = tempfile.mkdtemp(prefix="cut-preflight-export-")
    home = tempfile.mkdtemp(prefix="cut-preflight-home-")
    try:
        try:
            build(tree)
        except Exception as exc:  # sbe: allow-silent reported as NO-DATA below, never swallowed
            return NODATA, "virgin-gate", "the export tree could not be built: %s" % exc
        env = runner_env(home)
        proc = _run(["sh", os.path.join(tree, "scripts", "required_fast.sh")],
                    tree, runner, env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
        # The tree is deleted below, so the whole output is kept beside it.
        keep = os.path.join(tempfile.gettempdir(),
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


def run_all(root, version, horizon_hours=DEFAULT_HORIZON_HOURS, remote=None,
            runner=None, now=None, virgin=True, build=None, progress=None):
    """Every check, cheapest first; the virgin gate (minutes) runs only when
    nothing cheaper already refused, so a one second red costs one second."""
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
    # R1.2 cheapest first: the local commit text read runs before the network
    # read of the public remote, so a fraction of a second red costs that, and
    # the minutes long export tree gate below still runs only when nothing
    # cheaper refused.
    results = [
        check_exceptions(root, at, runner),
        check_pack_clock(root, horizon_hours, runner),
        check_readiness_rows(root, at, runner),
        check_release_plan(root, runner),
        check_changelog_text(root, previous_cut_commit(root, version), runner),
        check_public_remote(root, version, remote, runner),
    ]
    # P6: about twenty seconds (one export build, one shallow fetch), so it
    # waits for the one second checks and still runs before the minutes long
    # gate. cut.py asks it again after the bump, on the tree that ships.
    if any(r[0] == REFUSED for r in results):
        results.append((NODATA, "plugin-bumps", "not run: a cheaper check already refused"))
    else:
        import plugin_bump_gate
        results.append(plugin_bump_gate.check_plugin_bumps(root, version, remote,
                                                           runner, build))
    if virgin and not any(r[0] == REFUSED for r in results):
        if progress:
            progress("virgin-gate: required_fast.sh on an export shaped tree "
                     "under an empty HOME; minutes, and silent until it ends")
        results.append(check_virgin_gate(root, runner, build))
    elif virgin:
        results.append((NODATA, "virgin-gate", "not run: a cheaper check already refused"))
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
    ap.add_argument("--no-virgin-gate", action="store_true",
                    help="skip the minutes long export tree fast gate; the cheap "
                         "checks only. cut.py never passes this")
    args = ap.parse_args(argv)
    results = run_all(ROOT, args.version, args.horizon_hours, args.remote,
                      virgin=not args.no_virgin_gate,
                      progress=lambda line: print(line, flush=True))
    for verdict, name, detail in results:
        print("%-8s %-16s %s" % (verdict, name, detail))
    code = exit_code(results)
    print("cut-preflight: %s" % {EXIT_OK: "CLEAR", EXIT_REFUSED: "REFUSED",
                                 EXIT_NODATA: "NO-DATA"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
