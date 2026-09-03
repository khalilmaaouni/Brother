"""`sbe map`: a deterministic, offline HTML status page, built from canonical
state and nothing else.

WHY THIS EXISTS. `skills/help/SKILL.md`'s old "project map" section told the
MODEL to probe `sbe status`, `sbe fences` and the dossier directory, then fill
`skills/help/map-template.html` slot by slot from whatever it happened to see.
That is nondeterministic (the same repository can render two different pages
depending on what the model chose to read and how it phrased what it found)
and it can invent data that was never actually there. This module replaces
the model with code: three canonical, already-truthful sources, read the same
way every time, rendered by a pure function with no judgement calls left for
an agent to make.

THE THREE SOURCES, AND ONLY THESE THREE:
  1. `brothersbe.status.build_team_report`, imported the same way `cli.py`'s
     own `_cmd_status` imports it, never re-derived and never parsed out of
     anyone's rendered text. It already reads the evidence store, the task
     registry, and every dossier's `09-convergence.json`, `10-approval.json`,
     `11-review.json` and `12-handover.json`, sorted and judged; this module
     does not repeat that work.
  2. `brothersbe.tasks.load_registry` / `open_tasks`, the same task registry
     `build_team_report` already reads, read here a second time only to list
     the open claims themselves (id, agent, role, owned paths), a level of
     detail the team report's findings summarize but do not lay out as a
     table.
  3. Dossier artifact PRESENCE: for every change `build_team_report` already
     discovered, whether each of the thirteen dossier-lifecycle files exists.
     Presence only, a boolean per file, never the file's content: this module
     never opens `01-purpose.md`, `03-adr.md` or `06-diagrams.md` to quote or
     summarize what is inside them. Doing that would mean rendering prose
     nobody has fingerprinted the meaning of, exactly the "fill the slot with
     whatever you found" defect this module exists to stop. A reader who
     wants that content opens the file; this page tells them the file is
     there.

WHY THIS DOES NOT REUSE `skills/help/map-template.html`. That template's
richer slots (a mermaid process diagram copied from `06-diagrams.md`, a data
model summary from `05-data-model.md`, a decision list from `03-adr.md`, a
code guide from `01-purpose.md`) all need dossier file CONTENT, not presence,
and reading that content back out is exactly the "parse rendered prose" this
lane exists to stop being the model's job. Nothing here backfills that with
model judgement either: it stays out of scope, honestly, rather than being
approximated. This module generates its own minimal, clean, self-contained
page instead, built only from what `collect_state` actually returns.

DETERMINISM. No wall-clock timestamp is read anywhere in this file (contrast
`status.build_report`, which stamps `generatedAt`; this module calls
`build_team_report`, which never does). No absolute, machine-specific
filesystem path is ever written into the page: `_relativize` strips this
run's own absolute repository root out of every string `build_team_report`
and the task registry hand back before anything is rendered, and the page
otherwise carries only relative dossier-relative paths, task ids, agent
names, commit shas and booleans. The same repository state, mapped twice,
byte-identical output; `tools/test_sbe_map.py` proves it directly.

ESCAPING. Every string this module did not itself write (a change name, a
task id, an agent name, a finding's own free-text detail, anything read from
the task registry or the status report) passes through `_esc` before it
reaches the page: `html.escape(..., quote=True)`. An operator's own agent
name or a dossier's own directory name is exactly as untrusted as any other
repository content this project already treats as data, never instruction
(see `docs/THREAT_MODEL.md`'s trust classes); a `<script>` tag typed into
either one must arrive on the page as inert text, not as a tag a browser
would execute. `tools/test_sbe_map.py::TestInjectionIsEscaped` pins this.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only. No em or en dashes anywhere in this file, its comments, or its
output.
"""
import argparse
import html
import io
import os
import sys

from . import cwd as cwd_mod
from . import status as status_mod
from . import tasks as tasks_mod

#: The dossier lifecycle files this module checks for PRESENCE, in the order
#: the lifecycle itself moves through: intake, the six design documents
#: `templates/dossier` ships, then the four artifacts later stages of the
#: pipeline write. Named once, here, so a reader of this file and a reader of
#: the generated page see the same thirteen names in the same order.
DOSSIER_ARTIFACTS = (
    ("00-intake.json", "intake"),
    ("01-purpose.md", "purpose"),
    ("02-process.md", "process"),
    ("03-adr.md", "decision record"),
    ("04-technology-map.md", "technology map"),
    ("05-data-model.md", "data model"),
    ("06-diagrams.md", "diagrams"),
    ("07-verification.md", "verification plan"),
    ("08-plan.json", "task plan"),
    ("09-convergence.json", "convergence report"),
    ("10-approval.json", "approval report"),
    ("11-review.json", "review record"),
    ("12-handover.json", "handover record"),
)


def _esc(value):
    """`value` as text, HTML-escaped with quotes turned into entities too:
    the one function every piece of state passes through on its way into the
    page. `None` renders as the empty string rather than the literal text
    "None", because an absent field is absence, not a word."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _relativize(value, root):
    """`value` with every occurrence of this run's own absolute `root` path
    stripped out, recursively through dicts and lists, leaving every other
    string untouched.

    WHY THIS EXISTS. `status.build_team_report` and the task registry both
    hand back some strings built from `os.path.join(root, ...)` (an evidence
    path, a registry path, a "run `sbe plan <dossier> --write`" suggestion
    naming the dossier's own absolute directory): honest and correct for a
    human reading a terminal, but a machine-specific filesystem path is
    exactly what this module promises never to bake into a page meant to be
    opened by anyone, on any machine, and compared byte-for-byte across two
    runs on two different checkouts of the same state. The longer, more
    specific `root + separator` prefix is replaced first, so an evidence
    path under `root` loses only its machine-specific head and keeps its
    project-relative tail; a bare, exact `root` (the rarer case: a path that
    names the repository root itself, with nothing after it) is replaced by
    a plain ".".
    """
    if isinstance(value, dict):
        return dict((k, _relativize(v, root)) for k, v in value.items())
    if isinstance(value, list):
        return [_relativize(v, root) for v in value]
    if isinstance(value, str) and value:
        prefix = root + os.sep
        if value.startswith(prefix):
            value = value[len(prefix):]
        elif value == root:
            value = "."
        return value.replace(prefix, "").replace(root, ".")
    return value


def collect_state(root):
    """Every canonical fact the page renders, gathered ONCE, from exactly the
    three sources this module's own docstring names. Raises nothing: a
    registry that cannot be read is carried forward as `registryProblem`
    text rather than an exception, the same "a store this run could not
    read is reported, never silently treated as empty" law
    `brothersbe.status` already holds every one of its own readers to.
    """
    root = os.path.abspath(root)
    team = status_mod.build_team_report(root)

    # Reused rather than re-derived: `build_team_report` already calls this
    # exact function to discover which dossiers exist before it ever builds
    # a finding, so calling it again here (to learn WHERE each dossier is,
    # something the team report's own return value never carries past the
    # bare change name) is the same discovery, not a second copy of it.
    changes, _refusals = status_mod._team_changes(root)

    artifacts = {}
    for name, doss in changes:
        artifacts[name] = [
            (filename, label, os.path.isfile(os.path.join(doss, filename)))
            for filename, label in DOSSIER_ARTIFACTS
        ]

    registry_problem = None
    open_records = []
    try:
        registry = tasks_mod.load_registry(root)
        open_records = tasks_mod.open_tasks(registry)
    except tasks_mod.RegistryUnusable as exc:
        registry_problem = str(exc)

    state = {
        "projectName": os.path.basename(root.rstrip(os.sep)) or root,
        "headCommit": team["headCommit"],
        "changes": team["changes"],
        "findings": team["findings"],
        "handover": team["handover"],
        "basisLegend": team["basisLegend"],
        "artifacts": artifacts,
        "openTasks": [
            {"id": r.get("id"), "agent": r.get("agent"), "role": r.get("role"),
             "ownedPaths": list(r.get("ownedPaths") or []),
             "baseCommit": r.get("baseCommit")}
            for r in open_records
        ],
        "registryProblem": registry_problem,
    }
    return _relativize(state, root)


# ---------------------------------------------------------------------------
# Rendering. Every function below is pure: state in, an HTML string out, no
# file I/O, no clock, no environment read. `render_html` is the only public
# entry point; the rest are internal composition.
# ---------------------------------------------------------------------------

_STYLE = """
  :root {
    --color-page: #FAF9F6; --color-ink: #1F2933; --color-muted: #52606D;
    --color-accent: #0F766E; --color-amber: #B45309; --color-red: #B91C1C;
    --font-display: "Iowan Old Style", Palatino, Georgia, serif;
    --font-body: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --font-mono: ui-monospace, "SF Mono", Menlo, monospace;
    --border: color-mix(in srgb, var(--color-ink) 14%, transparent);
    --card-bg: color-mix(in srgb, var(--color-ink) 4%, var(--color-page));
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --color-page: #161B22; --color-ink: #E6EDF3; --color-muted: #9BA7B4;
      --color-accent: #2DD4BF; --color-amber: #F59E0B; --color-red: #F87171;
    }
  }
  :root[data-theme="dark"] {
    --color-page: #161B22; --color-ink: #E6EDF3; --color-muted: #9BA7B4;
    --color-accent: #2DD4BF; --color-amber: #F59E0B; --color-red: #F87171;
  }
  :root[data-theme="light"] {
    --color-page: #FAF9F6; --color-ink: #1F2933; --color-muted: #52606D;
    --color-accent: #0F766E; --color-amber: #B45309; --color-red: #B91C1C;
  }
  *, *::before, *::after { box-sizing: border-box; }
  html { font-size: 17px; }
  body {
    margin: 0; background: var(--color-page); color: var(--color-ink);
    font-family: var(--font-body); line-height: 1.6;
    display: flex; flex-direction: column; align-items: center;
    padding: 2.5rem 1rem;
  }
  .page { width: 100%; max-width: 960px; display: flex; flex-direction: column; gap: 2.5rem; }
  h1, h2 { font-family: var(--font-display); font-weight: 600; margin: 0; }
  h1 { font-size: 1.95rem; }
  h2 { font-size: 1.4rem; margin-bottom: 0.75rem; }
  p { margin: 0; }
  .eyebrow {
    font-size: 0.8rem; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--color-muted); margin: 0 0 0.25rem;
  }
  .muted { color: var(--color-muted); }
  section { border-top: 1px solid var(--border); padding-top: 1.5rem; }
  table { border-collapse: collapse; width: 100%; overflow-x: auto; display: block; }
  th, td {
    text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border);
    font-size: 0.92rem; vertical-align: top;
  }
  th { color: var(--color-muted); font-weight: 600; }
  code {
    font-family: var(--font-mono); background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 4px; padding: 0.05em 0.35em; font-size: 0.9em;
  }
  .yes { color: var(--color-accent); }
  .no { color: var(--color-muted); }
  .verdict-FAIL, .verdict-REVIEW-REQUIRED { color: var(--color-red); font-weight: 600; }
  .verdict-NO-DATA { color: var(--color-amber); }
  .verdict-PASS { color: var(--color-accent); font-weight: 600; }
  footer { border-top: 1px solid var(--border); padding-top: 1.5rem; color: var(--color-muted);
          font-size: 0.8rem; }
"""


def _verdict_class(verdict):
    v = str(verdict or "")
    if v in ("FAIL", "REVIEW-REQUIRED", "NO-DATA", "PASS"):
        return "verdict-%s" % v
    return ""


def _render_header(state):
    head = state["headCommit"]
    head_text = (head[:12] + "...") if head else "unresolved (no HEAD commit)"
    return (
        '<header>\n'
        '  <p class="eyebrow">Project status map, generated deterministically</p>\n'
        '  <h1>%s</h1>\n'
        '  <p class="muted">Built from repository state at head <code>%s</code>. '
        'This page is a snapshot: regenerate it with <code>sbe map</code> to see '
        'anything that changed since.</p>\n'
        '</header>\n'
        % (_esc(state["projectName"]), _esc(head_text))
    )


def _render_findings(state):
    findings = state["findings"]
    out = ['<section id="findings">\n  <h2>Findings, most severe first</h2>\n']
    if not findings:
        out.append('  <p>No dossier under <code>design/</code> has anything recorded to '
                   'read. This is the honest starting state, not an error.</p>\n')
        out.append('</section>\n')
        return "".join(out)
    out.append('  <p class="muted">%s</p>\n' % _esc(state["basisLegend"]))
    out.append('  <table>\n    <thead><tr><th>Change</th><th>Verdict</th>'
               '<th>Detail</th><th>Next action</th><th>Owner</th></tr></thead>\n    <tbody>\n')
    for f in findings:
        out.append(
            '      <tr class="%s"><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>\n'
            % (_verdict_class(f.get("verdict")), _esc(f.get("change")),
               _esc(f.get("verdict")), _esc(f.get("detail")), _esc(f.get("nextAction")),
               _esc(f.get("owner")) or "(none)"))
    out.append('    </tbody>\n  </table>\n</section>\n')
    return "".join(out)


def _render_artifacts(state):
    artifacts = state["artifacts"]
    out = ['<section id="artifacts">\n  <h2>Dossier artifacts on disk</h2>\n']
    if not artifacts:
        out.append('  <p>No dossier was discovered under <code>design/</code>, so there is '
                   'nothing to check for artifact presence.</p>\n')
        out.append('</section>\n')
        return "".join(out)
    out.append('  <table>\n    <thead><tr><th>Change</th>')
    for filename, _label in DOSSIER_ARTIFACTS:
        out.append('<th title="%s">%s</th>' % (_esc(_label_for(filename)), _esc(filename)))
    out.append('</tr></thead>\n    <tbody>\n')
    for name in state["changes"]:
        rows = artifacts.get(name, [])
        out.append('      <tr><td>%s</td>' % _esc(name))
        for _filename, _label, present in rows:
            out.append('<td class="%s">%s</td>' % ("yes" if present else "no",
                                                    "yes" if present else "no"))
        out.append('</tr>\n')
    out.append('    </tbody>\n  </table>\n</section>\n')
    return "".join(out)


def _label_for(filename):
    for fname, label in DOSSIER_ARTIFACTS:
        if fname == filename:
            return label
    return filename


def _render_tasks(state):
    out = ['<section id="tasks">\n  <h2>Open task registry claims</h2>\n']
    if state["registryProblem"]:
        out.append('  <p>The task registry could not be read: %s</p>\n'
                   % _esc(state["registryProblem"]))
        out.append('</section>\n')
        return "".join(out)
    tasks = state["openTasks"]
    if not tasks:
        out.append('  <p>No task is currently open.</p>\n</section>\n')
        return "".join(out)
    out.append('  <table>\n    <thead><tr><th>Id</th><th>Agent</th><th>Role</th>'
               '<th>Owned paths</th><th>Base commit</th></tr></thead>\n    <tbody>\n')
    for t in tasks:
        owned = ", ".join(_esc(p) for p in t["ownedPaths"]) or "(none)"
        base = _esc((t["baseCommit"] or "")[:12]) or "(none)"
        out.append('      <tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td><code>%s</code>'
                   '</td></tr>\n'
                   % (_esc(t["id"]), _esc(t["agent"]), _esc(t["role"]), owned, base))
    out.append('    </tbody>\n  </table>\n</section>\n')
    return "".join(out)


def _render_handover(state):
    entries = [h for h in state["handover"] if h.get("status") != "none"]
    out = ['<section id="handover">\n  <h2>Ownership handover</h2>\n']
    if not entries:
        out.append('  <p>No handover is recorded for any change: absence is a fact, not '
                   'an accusation, and never a block when ownership is not moving.</p>\n')
        out.append('</section>\n')
        return "".join(out)
    out.append('  <table>\n    <thead><tr><th>Change</th><th>Status</th>'
               '<th>Detail</th></tr></thead>\n    <tbody>\n')
    for h in entries:
        out.append('      <tr><td>%s</td><td>%s</td><td>%s</td></tr>\n'
                   % (_esc(h.get("change")), _esc(h.get("status")), _esc(h.get("detail"))))
    out.append('    </tbody>\n  </table>\n</section>\n')
    return "".join(out)


def render_html(state):
    """The complete, self-contained HTML document for `state`. Pure: the
    exact same `state` value always renders the exact same string, and
    nothing outside `state` (the clock, the environment, the filesystem) is
    ever consulted here."""
    parts = [
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
        '<title>%s: project status map</title>\n' % _esc(state["projectName"]),
        '<meta name="description" content="A generated, offline, deterministic status '
        'map of one BrotherSBE project.">\n',
        '<style>%s</style>\n</head>\n<body>\n<div class="page">\n' % _STYLE,
        _render_header(state),
        _render_findings(state),
        _render_artifacts(state),
        _render_tasks(state),
        _render_handover(state),
        '<footer>\n  <p>Generated by <code>sbe map</code> from canonical state only: the '
        'status module\'s team report, the task registry, and dossier artifact presence. '
        'No model filled a slot from prose; nothing here was invented. This page is '
        'self-contained (no network request, no external script) and carries no '
        'generation timestamp or machine-specific path, so the same repository state '
        'always renders the same bytes.</p>\n</footer>\n',
        '</div>\n</body>\n</html>\n',
    ]
    return "".join(parts)


# ---------------------------------------------------------------------------
# CLI surface: `sbe map [path] --out FILE`, routed from `cli.py` exactly the
# way `evidence`, `task`, `work`, `handover`, `pr`, `explain` and `lineage`
# already route to their own module's `main`.
# ---------------------------------------------------------------------------

def _parser():
    parser = argparse.ArgumentParser(
        prog="sbe map",
        description="Build a deterministic, offline HTML status map from canonical state "
                    "only (the status module's team report, the task registry, and "
                    "dossier artifact presence). Never a model filling a template: an "
                    "absent source renders as absent, and the same repository state "
                    "always renders the same bytes.")
    parser.add_argument("path", nargs="?", default=None,
                        help="the repository to map (default: the current one, or --cwd "
                             "when given)")
    cwd_mod.add_cwd_argument(parser)
    parser.add_argument("--out", required=True,
                        help="the file to write the generated HTML page to")
    return parser


def main(rest, exit_ok=0, exit_failed=1, exit_usage=2):
    rest = list(rest)
    if rest and rest[0] in ("-h", "--help"):
        # Help is not an error: printed and exits 0, the same convention
        # `prverify.main` and every other passthrough module in this package
        # already follows.
        _parser().print_help(sys.stdout)
        return exit_ok
    parser = _parser()
    try:
        args = parser.parse_args(rest)
    except SystemExit as exc:
        return exit_ok if exc.code in (0, None) else exit_usage

    root = os.path.abspath(cwd_mod.resolve(args.path, args.cwd))
    if not os.path.isdir(root):
        sys.stderr.write("sbe map: '%s' is not a directory. A mistyped path must not "
                         "read as a clean scan.\n" % root)
        return exit_usage

    state = collect_state(root)
    page = render_html(state)
    out_path = os.path.abspath(args.out)
    try:
        with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(page)
    except OSError as exc:
        sys.stderr.write("sbe map: could not write %s: %s\n" % (out_path, exc))
        return exit_failed

    sys.stdout.write(
        "sbe map: wrote a deterministic status map for %d change(s) to %s. Open it in any "
        "browser; it needs no network connection and no server.\n"
        % (len(state["changes"]), out_path))
    return exit_ok
