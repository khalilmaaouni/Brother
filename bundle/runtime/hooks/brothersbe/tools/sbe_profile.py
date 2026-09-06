#!/usr/bin/env python3
"""The profile: which surfaces load, and the invariants that keep the default small.

Why this file exists. An external assessment scored this project high on rigor and
low on release readiness, and named the cause: everything shipped switched on. A
first-time reader met the vault, the telemetry ledger, team coordination, the policy
control plane and the release controls before they had designed anything. The fix is
not a shorter law file. It is a DECLARATION a user can set, a default that genuinely
loads less, and a check that fails when a module leaks back into that default.

What the default profile is: the safety floor (the ground map, one writer per file,
fence then dispatch, never claim done without a verifying command) plus design,
implementation boundaries and verification. Nothing else. The five optional modules
and their triggers are in `references/modules.md`; the declaration is
`.brothersbe/profile.json`.

What this tool does NOT do, stated rather than implied. It does not gate a law. All
nineteen laws stay reachable from SKILL.md's own routing table at every profile, and
`check` FAILS if one stops being reachable, because a law that vanishes with a
module switched off is worse than a long law file. It gates SURFACES: the reference
files a session loads, and the ONE startup emitter the SessionStart hook gates.

Which modules are actually enforced, stated here and not only in the table. Only
`telemetry` has a mechanism that reads the declaration: the `startup-nags` emitter
in `tools/sbe_sessionstart.py`, guarded by an `enabled telemetry` call to this tool.
`vault`, `team`, `policy` and `release` are DECLARED BUT NOT ENFORCED: they carry
that exact phrase in `references/modules.md`, because nothing reads the declaration
for them and switching them changes nothing a machine can observe.
`check_module_enforcement` below is what keeps that honest, by refusing any
enforcement claim whose named file does not ask this tool about that module.

Why `release` is in that list rather than gating the hook's update check, since a
draft of this lane did gate it. `check-update` prints the notice that the governance
engine itself changed under the user since their last session. Gating it meant an
upgraded install stopped seeing that notice, silently, as a side effect of a size
optimisation: a trust regression, not a size win, and the bytes it saved are
measured in `references/modules.md` rather than asserted. The notice is now
unconditional, the release module's scope excludes it by decision, and this file
therefore names no emitter that module owns.

Verdict shape. The four checks below return a (name, verdict, evidence) triple and
print through `say`. They are deliberately not registered `Check` objects in a
registry: the mechanism that enforces them is `tools/test_sbe_profile.py`, a test
that re-injects each defect and watches the verdict go FAIL. Registering them in
`tools/sbe_score.py` (so the honesty meta-test sweeps them and CI blocks on them
through an existing step) is the named follow-up, and it needs fixtures this lane did
not build.

Absent evidence is NO-DATA, never a pass, exactly as everywhere else here: a routing
table that could not be read, or that holds no rows, is NO-DATA carrying the reason,
and NO-DATA never decides an exit code.

Usage, from anywhere (paths resolve against --root, default: this file's repository):
    python3 tools/sbe_profile.py resolve            # the active profile, in words
    python3 tools/sbe_profile.py modules            # enabled module ids, one per line
    python3 tools/sbe_profile.py enabled telemetry  # exit 0 enabled, 1 not
    python3 tools/sbe_profile.py check [--strict]   # the invariants; --strict exits 2 on FAIL
"""
import io, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sbe_checks import say, one_line

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DECLARATION = os.path.join(".brothersbe", "profile.json")
SKILL = "SKILL.md"
MODULES_INDEX = os.path.join("references", "modules.md")
HOOK = os.path.join("tools", "sbe_sessionstart.py")

# The named profiles. "default" is the whole point of this file: no modules.
# "full" is what this project ran as before the split, kept so an existing user
# gets their surfaces back with one word instead of five.
PROFILES = {"default": (), "full": None}   # None means "every module the index declares"

# Which module owns each GATED startup emitter. The hook is the thing that must ask
# before running one; `check_module_isolation` reads the hook's own text and FAILs if
# an emitter listed here is not guarded by its module.
#
# Two emitters are deliberately absent, and absence here means "runs at every
# profile", not "nobody got round to it". `compact-hint` is the recovery pointer.
# `check-update` is the notice that the skill changed under the user, which is
# safety-adjacent: an install that stops saying so is trusted unread. Adding either
# one to this dict would make an unguarded hook FAIL module-isolation, which is
# exactly backwards for a line that must print at the smallest profile, so the
# decision is recorded here beside the data it governs.
STARTUP_EMITTERS = {"startup-nags": "telemetry"}

# Every law and every phase that must stay reachable from SKILL.md's routing
# table (or be declared in SKILL.md itself, which is where the floor laws live).
LAWS = tuple(range(1, 20))
PHASES = tuple(range(1, 7))


def read_text(path):
    """(text, problem). A file this tool cannot open is named, never skipped:
    a routing table nobody read produces NO-DATA, and a silent skip would
    produce a clean verdict over a table that is not there."""
    try:
        with io.open(path, encoding="utf-8", errors="replace") as f:
            return f.read(), ""
    except (OSError, ValueError) as e:
        return "", "%s could not be read (%s: %s)" % (path, type(e).__name__, e)


class Source(object):
    """WHICH file answered "what profile is this", as an absolute path.

    A verdict line that prints the bare relative string `.brothersbe/profile.json`
    does not say which file was opened, and a resolution that quietly fell back to
    the shipped install while printing that string is the silent-wrong-source class
    this repository blocks on everywhere else. So the resolution carries its own
    provenance and every line that reports a profile prints `note`, which always
    names an absolute path and says plainly when the shipped install answered
    instead of a project.

    kind is one of:
      "project"  a declaration found in the tree being worked on
      "install"  no project declaration was found; the shipped install's own file
                 answered, which is NOT this project's choice
      "none"     no declaration file exists at all; the built-in default answered
    """

    def __init__(self, directory, path, kind, note):
        self.directory = directory
        self.path = path
        self.kind = kind
        self.note = note


def discover(root, start):
    """Walk UP from `start` looking for the declaration, and say what answered.

    The walk stops at the first of three things, and all three are reported rather
    than implied: a directory holding the declaration (that file answers), a
    directory holding `.git` (the project boundary, checked AFTER the declaration
    in the same directory, because a declaration above a repository root belongs to
    a different project), or the filesystem root. `.git` is tested with
    `os.path.exists` rather than `os.path.isdir` so a linked worktree, whose `.git`
    is a file, is a boundary too.

    Before this walk existed, only the working directory itself was consulted, so a
    session started in any subdirectory of a declared project silently got the
    shipped install default while the evidence line still read `.brothersbe/profile.json`.
    """
    origin = os.path.abspath(start)
    here = origin
    depth = 0
    while True:
        path = os.path.join(here, DECLARATION)
        if os.path.isfile(path):
            if depth == 0:
                where = "the working directory %s" % origin
            else:
                where = ("%s, %d directory level(s) above the working directory %s"
                         % (here, depth, origin))
            return Source(here, path, "project",
                          "the project declaration %s, found in %s" % (path, where))
        if os.path.exists(os.path.join(here, ".git")):
            stopped = "the project root %s (it holds .git)" % here
            break
        parent = os.path.dirname(here)
        if parent == here:
            stopped = "the filesystem root %s" % here
            break
        here = parent
        depth += 1
    install_dir = os.path.abspath(root)
    install = os.path.join(install_dir, DECLARATION)
    if os.path.isfile(install):
        return Source(install_dir, install, "install",
                      "NO project declaration was found walking up from %s to %s, so the "
                      "SHIPPED INSTALL DEFAULT %s answered; that is the installed copy's own "
                      "file, not this project's choice" % (origin, stopped, install))
    return Source(install_dir, "", "none",
                  "no declaration was found walking up from %s to %s, and none is shipped at "
                  "%s either, so the built-in default profile answered"
                  % (origin, stopped, install))


def source_for(root, project):
    """A Source for a directory the CALLER named (--root, --project, or a test
    naming its own scratch tree). No walk happens: naming a tree is an answer, and
    walking above it would answer a question nobody asked."""
    base = os.path.abspath(project)
    path = os.path.join(base, DECLARATION)
    if os.path.isfile(path):
        if base == os.path.abspath(root):
            return Source(base, path, "install",
                          "the installed copy's own declaration %s (the tree named on the "
                          "command line is the install)" % path)
        return Source(base, path, "project",
                      "the project declaration %s (the tree named on the command line)" % path)
    return Source(base, "", "none",
                  "no declaration file exists at %s (the tree named on the command line), so "
                  "the built-in default profile answered" % path)


def as_source(root, project):
    """Accept what callers already pass: a Source, a directory string, or None.

    None keeps the old meaning, deliberately: the caller named `root` and nothing
    else, so `root` is the tree. The WALK is never entered from here. It belongs to
    the one caller that has no named tree at all, `main()` with neither --root nor
    --project, which is the SessionStart hook's case. Making None mean "walk from
    the working directory" would let this process's cwd answer a question asked
    about somebody else's tree, which is the same silent-wrong-source failure in a
    new place.
    """
    if isinstance(project, Source):
        return project
    if project is None:
        return source_for(root, root)
    return source_for(root, project)


def collapse(text):
    """The one normalization applied when a routing row and a LOAD WHEN line are
    compared: whitespace collapsed, nothing else. A table cell is one line and a
    LOAD WHEN line wraps across several, so byte equality would fail on files
    that agree word for word; anything looser than this would let them disagree."""
    return " ".join(text.split())


def tables(text):
    """Every markdown pipe table in `text`, as (headers, rows) with cells stripped.

    Rows are kept only when their cell count matches the header's, so a stray
    line beginning with a pipe cannot masquerade as a routing row.
    """
    out, header, rows = [], None, []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.match(r"^:?-{2,}:?$", c) for c in cells if c):
                continue          # the ---|--- separator row
            if header is None:
                header, rows = cells, []
            elif len(cells) == len(header):
                rows.append(cells)
            continue
        if header is not None:
            out.append((header, rows))
            header, rows = None, []
    if header is not None:
        out.append((header, rows))
    return out


def _pick(text, want):
    """The rows of the first table whose header holds every word in `want`."""
    for header, rows in tables(text):
        low = [h.lower() for h in header]
        if all(any(w in h for h in low) for w in want):
            return header, rows
    return None, []


def core_rows(root):
    """SKILL.md's routing table: [(trigger, file, holds)], plus any read problem."""
    text, problem = read_text(os.path.join(root, SKILL))
    if problem:
        return [], problem
    header, rows = _pick(text, ("load when", "read", "holds"))
    if header is None:
        return [], "%s declares no routing table (no header naming load when, read and holds)" % SKILL
    return [(r[0], r[1].strip("` "), r[2]) for r in rows if len(r) == 3], ""


def module_rows(root):
    """references/modules.md's table: [(id, trigger, file, holds, enforced by)].

    The fifth cell is the enforcement point, and it is not decoration: three of the
    five declared modules have nothing anywhere that reads the declaration, so a
    table without this column reads as if a switch exists for all five when it does
    not. `check_module_enforcement` reads this cell and refuses a claim it cannot
    stand up.
    """
    text, problem = read_text(os.path.join(root, MODULES_INDEX))
    if problem:
        return [], problem
    header, rows = _pick(text, ("module", "load when", "read", "holds", "enforced"))
    if header is None:
        return [], ("%s declares no module table (no header naming module, load when, read, "
                    "holds and enforced by)" % MODULES_INDEX)
    return [(r[0].strip("` "), r[1], r[2].strip("` "), r[3], r[4].strip()) for r in rows
            if len(r) == 5], ""


def module_ids(root):
    rows, problem = module_rows(root)
    return [r[0] for r in rows], problem


def load_when(root, rel):
    """(trigger, problem) for one reference file: the LOAD WHEN line, whitespace
    collapsed, read from the file itself rather than from the table that points
    at it, which is the only way the two can be shown to agree."""
    text, problem = read_text(os.path.join(root, rel))
    if problem:
        return "", problem
    m = re.search(r"(?ms)^LOAD WHEN:[ \t]*(.*?)(?:\n[ \t]*\n|\Z)", text)
    if not m:
        return "", "%s has no LOAD WHEN line, so its trigger and its routing row cannot be compared" % rel
    return collapse(m.group(1)), ""


def declared(root, project=None):
    """(profile name, enabled module ids, problems).

    `root` is the installed surface (where SKILL.md and references/ live) and
    supplies the module id space. `project` names the tree being worked in and
    supplies the DECLARATION, because a profile is a property of the work in front
    of the operator, not of the machine: the SessionStart hook runs from the
    project's directory while `$DIR` points at the install. `project` may be a
    Source, a directory, or None; None means nobody named a tree and `discover`
    walks up from the working directory to find one.

    Precedence: SBE_PROFILE and SBE_PROFILE_MODULES in the environment override
    the file, because a session must be able to switch a module on without
    editing a tracked file. A declaration that cannot be parsed is a PROBLEM and
    the resolution falls back to the default profile: the smallest surface is the
    safe direction to fail in, and the reason is always carried out, never eaten.
    """
    src = as_source(root, project)
    problems, name, extra = [], "default", []
    path = src.path
    if path:
        text, problem = read_text(path)
        if problem:
            problems.append(problem)
        else:
            try:
                data = json.loads(text)
            except ValueError as e:
                data = None
                problems.append("%s is not parseable JSON (%s), so the default profile was used"
                                % (path, e))
            if data is not None:
                if not isinstance(data, dict):
                    problems.append("%s holds %s, not an object" % (path, type(data).__name__))
                else:
                    name = data.get("profile", "default")
                    mods = data.get("modules", [])
                    if not isinstance(name, str):
                        problems.append("%s: profile is not a string" % path)
                        name = "default"
                    if isinstance(mods, list) and all(isinstance(m, str) for m in mods):
                        extra = list(mods)
                    else:
                        problems.append("%s: modules is not a list of strings" % path)
    env_name = os.environ.get("SBE_PROFILE", "").strip()
    if env_name:
        name = env_name
    env_mods = os.environ.get("SBE_PROFILE_MODULES", "").strip()
    if env_mods:
        extra += [m.strip() for m in env_mods.split(",") if m.strip()]
    known, problem = module_ids(root)
    if problem:
        problems.append(problem)
    if name not in PROFILES:
        problems.append("profile %r is not one of %s, so the default profile was used"
                        % (name, ", ".join(sorted(PROFILES))))
        name = "default"
    base = list(known) if PROFILES[name] is None else list(PROFILES[name])
    unknown = [m for m in extra if m not in known]
    if unknown:
        problems.append("module(s) %s are not declared in %s (declared: %s), so they switch on "
                        "nothing; a typo that loads nothing looks exactly like a module that is on"
                        % (", ".join(sorted(unknown)), MODULES_INDEX, ", ".join(known) or "none"))
    enabled = sorted(set(base) | set(m for m in extra if m in known))
    return name, enabled, problems


def _numbers(cell, letter):
    """The law or phase numbers a Holds cell names, ranges expanded.

    Mirrors how evals/run_evals.py reads a law citation: "L7 to L10" is a run,
    "L1 and L2" is two endpoints. `letter` is "L" for laws, "" for phases.
    """
    out = set()
    pattern = r"%s(\d+)" % (letter or r"(?:Phases?\s+)?")
    for m in re.finditer(r"%s\s*(?:,|and|to|through|/)\s*%s" % (pattern, pattern), cell):
        a, b = int(m.group(1)), int(m.group(2))
        if re.search(r"\b(to|through)\b", m.group(0)):
            out |= set(range(min(a, b), max(a, b) + 1))
        else:
            out |= {a, b}
    for m in re.finditer(pattern, cell):
        out.add(int(m.group(1)))
    return out


def holds_laws(cell):
    return _numbers(cell, "L")


def holds_phases(cell):
    if "phase" not in cell.lower():
        return set()
    return _numbers(re.sub(r"L\d+", "", cell), "")


def check_declaration(root, project=None):
    """The declaration parses, names a known profile, and names only known modules.

    The evidence names the ABSOLUTE path of the file that answered, and says so when
    the answer came from the shipped install rather than from a project: a verdict
    that prints a bare relative string cannot be checked against the file it read.
    """
    src = as_source(root, project)
    name, enabled, problems = declared(root, src)
    known, _ = module_ids(root)
    if not known:
        return ("profile-declaration", "NO-DATA",
                "%s declares no module, so there is no id space to validate a declaration "
                "against; %s" % (MODULES_INDEX, src.note))
    if problems:
        return ("profile-declaration", "FAIL", "; ".join(problems) + "; answered by: " + src.note)
    return ("profile-declaration", "PASS",
            "profile %r resolves to %d of %d module(s) [%s]; answered by: %s"
            % (name, len(enabled), len(known), ", ".join(enabled) or "none", src.note))


def check_law_routing(root, project=None):
    """Every law and every phase resolves to a file that exists, and every routing
    row's trigger is word for word the LOAD WHEN line of the file it points at."""
    core, problem = core_rows(root)
    if problem:
        return ("profile-law-routing", "NO-DATA", problem)
    if not core:
        return ("profile-law-routing", "NO-DATA",
                "%s's routing table holds no rows, so no law was resolved through it" % SKILL)
    skill_text, problem = read_text(os.path.join(root, SKILL))
    if problem:
        return ("profile-law-routing", "NO-DATA", problem)
    in_file = {int(n) for n in re.findall(r"^###\s+L(\d+)\.", skill_text, re.M)}
    mods, _mod_problem = module_rows(root)
    reachable_laws, reachable_phases, broken = set(in_file), set(), []
    # A broken claim, not an absence: SKILL.md tells the reader to open the module
    # index, so an index that is not there is a pointer nobody can follow and it
    # FAILs here. A module index that EXISTS and declares nothing is the other
    # case, an absence, and it reports NO-DATA from the two checks that read it.
    if MODULES_INDEX in skill_text and not os.path.isfile(os.path.join(root, MODULES_INDEX)):
        broken.append("%s routes the reader to %s, which is not a file, so every optional "
                      "module is unreachable" % (SKILL, MODULES_INDEX))
    # A law counts as reachable ONLY through SKILL.md (a core row, or a law
    # declared in the file itself). A module row's Holds cell is prose about a
    # surface, and crediting it would mean a law could become unreachable the
    # moment somebody switched a module off, which is the failure this check
    # exists to make impossible. Module rows are still walked, for the LOAD WHEN
    # agreement every routing row owes.
    for trigger, rel, holds, is_core in ([(t, f, h, True) for t, f, h in core] +
                                         [(t, f, h, False) for _i, t, f, h, _e in mods]):
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            broken.append("a routing row points at %s, which is not a file" % rel)
            continue
        stated, problem = load_when(root, rel)
        if problem:
            broken.append(problem)
            continue
        if stated != collapse(trigger):
            broken.append("%s's LOAD WHEN disagrees with its routing row (row: %r; file: %r)"
                          % (rel, collapse(trigger), stated))
            continue
        if is_core:
            reachable_laws |= holds_laws(holds)
            reachable_phases |= holds_phases(holds)
    missing_laws = [n for n in LAWS if n not in reachable_laws]
    missing_phases = [n for n in PHASES if n not in reachable_phases]
    if missing_laws:
        broken.append("law(s) %s are reachable from no routing row and declared in no law file, "
                      "so a reader following the table never finds them"
                      % ", ".join("L%d" % n for n in missing_laws))
    if missing_phases:
        broken.append("phase(s) %s are reachable from no routing row"
                      % ", ".join(str(n) for n in missing_phases))
    if broken:
        return ("profile-law-routing", "FAIL",
                "; ".join(broken[:4]) + "; read under %s" % os.path.abspath(root))
    return ("profile-law-routing", "PASS",
            "%d law(s) and %d phase(s) resolve through %d core row(s) and %d module row(s); "
            "every LOAD WHEN line matches its row; read under %s"
            % (len(LAWS), len(PHASES), len(core), len(mods), os.path.abspath(root)))


def check_module_isolation(root, project=None):
    """No module leaks into the default: SKILL.md names no module's own file, the
    default profile resolves to no module file, and every startup emitter in the
    SessionStart hook is guarded by the module that owns it."""
    mods, problem = module_rows(root)
    if problem:
        return ("profile-module-isolation", "NO-DATA", problem)
    if not mods:
        return ("profile-module-isolation", "NO-DATA",
                "%s declares no module, so nothing could leak and nothing was examined"
                % MODULES_INDEX)
    skill_text, problem = read_text(os.path.join(root, SKILL))
    if problem:
        return ("profile-module-isolation", "NO-DATA", problem)
    leaked = sorted({rel for _id, _t, rel, _h, _e in mods if rel in skill_text})
    hook_text, hook_problem = read_text(os.path.join(root, HOOK))
    ungated = []
    if hook_problem:
        ungated.append(hook_problem)
    else:
        lines = hook_text.splitlines()
        for emitter, owner in sorted(STARTUP_EMITTERS.items()):
            for i, line in enumerate(lines):
                if emitter not in line or line.lstrip().startswith("#"):
                    continue
                window = " ".join(lines[max(0, i - 3):i])
                if ("enabled %s" % owner) not in window:
                    ungated.append("%s runs %s at line %d with no `enabled %s` guard within the "
                                   "three lines above it, so a module surface prints at the "
                                   "default profile" % (HOOK, emitter, i + 1, owner))
    src = as_source(root, project)
    _name, enabled, _p = declared(root, src)
    problems = list(ungated)
    if leaked:
        problems.insert(0, "%s names %s, which belong(s) to an optional module; the default "
                           "profile's law file must route to no module file"
                        % (SKILL, ", ".join(leaked)))
    if problems:
        return ("profile-module-isolation", "FAIL",
                "; ".join(problems[:4]) + "; read under %s" % os.path.abspath(root))
    return ("profile-module-isolation", "PASS",
            "%d module file(s) named in %s and none in %s; %d startup emitter(s) each guarded by "
            "their module; the resolved profile enables [%s]; answered by: %s"
            % (len(mods), MODULES_INDEX, SKILL, len(STARTUP_EMITTERS),
               ", ".join(enabled) or "none", src.note))


NOT_ENFORCED = "DECLARED BUT NOT ENFORCED"


def check_module_enforcement(root, project=None):
    """Every module's Enforced by cell is either a real enforcement point or the
    exact words DECLARED BUT NOT ENFORCED.

    Why this check exists rather than a nicer table. Three of the five declared
    modules (vault, team, policy) have no loader, hook, tool or check anywhere that
    reads the declaration, so switching them on or off changes nothing a machine can
    observe. A table that lists them beside telemetry and release, which ARE gated
    in `tools/sbe_sessionstart.py`, tells a reader that five switches exist when two
    do. So a module either names a file that actually asks the profile about it (the
    literal `enabled <id>` query this tool answers), or it carries the exact phrase
    above and nobody is misled. A cell that does neither FAILs by name.
    """
    mods, problem = module_rows(root)
    if problem:
        return ("profile-module-enforcement", "NO-DATA", problem)
    if not mods:
        return ("profile-module-enforcement", "NO-DATA",
                "%s declares no module, so no enforcement claim was examined" % MODULES_INDEX)
    broken, enforced, unenforced = [], [], []
    for mid, _trigger, _rel, _holds, cell in mods:
        flat = collapse(cell)
        if flat.startswith(NOT_ENFORCED):
            unenforced.append(mid)
            continue
        # Only backticked tokens that LOOK like a repository path are read as an
        # enforcement point. A cell also quotes the guard it uses (`enabled team`)
        # and the profile names, and treating those as filenames would fail every
        # honest row for the wrong reason.
        paths = [t for t in re.findall(r"`([^`]+)`", cell)
                 if "/" in t and re.match(r"^[\w./-]+$", t)]
        if not paths:
            broken.append("module %s's Enforced by cell (%r) is neither the exact phrase %r nor a "
                          "backticked repository path, so the claim cannot be checked"
                          % (mid, flat, NOT_ENFORCED))
            continue
        for rel in paths:
            full = os.path.join(root, rel)
            if not os.path.isfile(full):
                broken.append("module %s claims enforcement by %s, which is not a file under %s"
                              % (mid, rel, os.path.abspath(root)))
                continue
            text, read_problem = read_text(full)
            if read_problem:
                broken.append(read_problem)
                continue
            if ("enabled %s" % mid) not in text:
                broken.append("module %s claims enforcement by %s, but that file never asks the "
                              "profile about %s (no `enabled %s`), so switching the module on or "
                              "off changes nothing there" % (mid, rel, mid, mid))
                continue
            enforced.append("%s via %s" % (mid, rel))
    if broken:
        return ("profile-module-enforcement", "FAIL",
                "; ".join(broken[:4]) + "; read under %s" % os.path.abspath(root))
    return ("profile-module-enforcement", "PASS",
            "%d module(s): %d with an enforcement point that queries the profile (%s), %d marked "
            "%s (%s), which is a declaration a reader can act on rather than a switch that does "
            "nothing; read under %s"
            % (len(mods), len(enforced), ", ".join(enforced) or "none", len(unenforced),
               NOT_ENFORCED, ", ".join(unenforced) or "none", os.path.abspath(root)))


CHECKS = (check_declaration, check_law_routing, check_module_isolation,
          check_module_enforcement)


def cmd_check(root, project, strict):
    say("BROTHERSBE PROFILE  (advisory unless --strict; NO-DATA is never a pass, and never a block)")
    fails = 0
    for fn in CHECKS:
        name, verdict, evidence = fn(root, project)
        if verdict == "FAIL":
            fails += 1
        say("  %-26s %-8s %s" % (name, verdict, evidence))
    say("  %d check(s), %d FAIL(s), root %s" % (len(CHECKS), fails, root))
    return 2 if (fails and strict) else 0


def cmd_resolve(root, project):
    src = as_source(root, project)
    name, enabled, problems = declared(root, src)
    mods, problem = module_rows(root)
    say("BROTHERSBE PROFILE: %s" % name)
    say("  answered by: %s (overrides: SBE_PROFILE, SBE_PROFILE_MODULES)" % src.note)
    say("  always loaded: %s, and the reference file its routing table names for the work in hand"
        % SKILL)
    if problem:
        say("  modules: %s" % problem)
    for mid, _trigger, rel, holds, enforced in mods:
        say("  %-10s %-3s %-34s %s [enforcement: %s]"
            % (mid, "ON" if mid in enabled else "off", rel, holds, enforced))
    for p in problems:
        say("  problem: %s" % p)
    return 0


def cmd_modules(root, project):
    _name, enabled, problems = declared(root, as_source(root, project))
    for mid in enabled:
        say(mid)
    for p in problems:
        sys.stderr.write(one_line("sbe_profile: %s" % p) + "\n")
    return 0


def cmd_enabled(root, project, module):
    """Exit 0 when the module is on, 1 when it is not. Used by the SessionStart
    hook, which must never fail a session: any problem resolves to "not enabled",
    the smallest surface, with the reason on stderr where the hook keeps it out
    of the injected context."""
    src = as_source(root, project)
    _name, enabled, problems = declared(root, src)
    for p in problems:
        sys.stderr.write(one_line("sbe_profile: %s" % p) + "\n")
    if problems:
        sys.stderr.write(one_line("sbe_profile: answered by: %s" % src.note) + "\n")
    return 0 if module in enabled else 1


USAGE = ("usage: sbe_profile.py {resolve|modules|enabled <module>|check} [--root DIR] [--project DIR] [--strict]\n"
         "  resolve   the active profile, and every module with the file it routes to\n"
         "  modules   the enabled module ids, one per line, for a hook to read\n"
         "  enabled   exit 0 when that module is on, 1 when it is not\n"
         "  check     the profile invariants; --strict exits 2 on any FAIL")


def main():
    argv = list(sys.argv[1:])
    root, project = ROOT, None
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 >= len(argv):
            say("sbe_profile.py: --root needs a directory")
            return 1
        root = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
        project = root      # an explicit root is self-contained: its own declaration
    if "--project" in argv:
        i = argv.index("--project")
        if i + 1 >= len(argv):
            say("sbe_profile.py: --project needs a directory")
            return 1
        project = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
    if project is None:
        # No explicit root: WALK UP from the working directory to the project that
        # owns it, so a session started in a subdirectory reads its own project's
        # declaration instead of silently getting the shipped install default. This
        # is the SessionStart hook's case, where $DIR is the install and the cwd is
        # the work. The walk carries out which file answered, absolute path and all.
        project = discover(root, os.getcwd())
    else:
        project = source_for(root, project)
    strict = "--strict" in argv
    argv = [a for a in argv if a != "--strict"]
    cmd = argv[0] if argv else "resolve"
    if not os.path.isdir(root):
        say("sbe_profile.py: --root %s is not a directory, so nothing was examined" % root)
        return 1
    if cmd == "check":
        return cmd_check(root, project, strict)
    if cmd == "resolve":
        return cmd_resolve(root, project)
    if cmd == "modules":
        return cmd_modules(root, project)
    if cmd == "enabled":
        if len(argv) < 2:
            say(USAGE)
            return 1
        return cmd_enabled(root, project, argv[1])
    say(USAGE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
