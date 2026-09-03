#!/usr/bin/env python3
"""Reporting-only CI entry point (loop A3).

The defect this exists for, in one sentence: a gate that can fail a build
teaches everyone to route around it, so CI needs a step that only ever
REPORTS on every change (what was claimed, which command proved it, what is
honestly unmeasured, what is unverified) and can never be the reason a build
goes red.

THE CONTRACT:
- This tool renders the reviewer one-pager from `tools/sbe_onepager.py` by
  IMPORT. It does not re-derive a single verdict, receipt path, or command
  string; `sbe_onepager.render`, `.load_tasks`, and `.evaluate_task` are the
  only source of that content, exactly as they are for `sbe_onepager.py`
  itself. This file adds one thing on top: a one-line SUMMARY that counts
  PASS, FAIL, NO-DATA, and unverified so a CI log's first line already says
  which of those four buckets a change landed in.
- "unverified" here means a gap line that is NOT an explicit NO-DATA verdict:
  a missing receipt, an empty receipt file, a corrupt receipt file, or a
  receipt missing its command or its verdict. NO-DATA is counted separately
  because this project's own vocabulary treats an explicit NO-DATA verdict as
  a distinct, more honest kind of gap than a receipt that is simply absent or
  broken (see `tools/sbe_onepager.py`'s module docstring).
- This tool ALWAYS exits 0. A reporter that can fail a build is not a
  reporter; it is a second gate wearing a reporter's name. Even an internal
  crash (a bug in this tool, a root that is not readable, anything) is caught
  and printed as an honest "could not report because X" line, never as a
  stack trace masquerading as a gate failure and never as a nonzero exit.
- The renderer module is loaded once, at import time, into the module-level
  `onepager` name (not re-imported per call) specifically so a test can
  monkeypatch `onepager.render` to force the crash-containment path without
  reaching into a fresh module object each time. Same reasoning, same shape,
  as `tools/test_sbe_onepager.py` loading `sbe_onepager.py` by file location
  rather than by package import.
- `--html PATH` writes the same packet (SUMMARY plus the A1 one-pager, the
  exact same text `build_report` produces, nothing re-derived) as ONE
  self-contained HTML file: every style inline in a `<style>` block, zero
  external references (no `http`/`https` link, no `src=` off disk, no
  external stylesheet, no `@import`, no `url()`, no remote font, no script).
  It is the artifact a reviewer can forward as their own work: open by
  double-click, network off, nothing missing. `render_html_body` only wraps
  the existing lines in headings (title, numbered sections) and one
  monospace `<pre>` block per section; it never adds or drops a claim.

Usage: python3 tools/sbe_report.py [target-dir] [--out PATH] [--html PATH]
target-dir defaults to the current directory. Exit code is always 0.

Python floor is 3.9: no match statements, no `X | Y` annotations. Standard
library only, mirroring every other tool in this directory.
"""
import argparse
import html as html_lib
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "sbe_onepager_for_report", os.path.join(HERE, "sbe_onepager.py"))
onepager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(onepager)


def one_line(text):
    """Flatten to exactly one physical line. Same choke point as
    sbe_onepager.one_line, used here for the same reason: every value in the
    summary and the crash line is read off disk or off an exception message,
    neither of which is trusted not to carry a newline."""
    return " ".join(str(text).split())


def summarize(root):
    """A single SUMMARY line built ONLY from onepager.load_tasks and
    onepager.evaluate_task: no verdict is computed here, only counted.
    Returns the line as a string."""
    loaded = onepager.load_tasks(root)
    tasks = loaded.tasks
    if not tasks:
        reason = onepager.NO_TASKS_REASON.get(
            loaded.problem, "the task registry carries nothing usable")
        return "SUMMARY: no tasks to report on (%s)." % (reason % onepager.TASKS_REL)
    passed = 0
    failed = 0
    no_data = 0
    unverified = 0
    for task in tasks:
        status, line, verdict = onepager.evaluate_task(root, task)
        if status == "checked":
            if verdict is not None and verdict.strip().upper() == "PASS":
                passed += 1
            else:
                failed += 1
        else:
            if "NO-DATA" in line:
                no_data += 1
            else:
                unverified += 1
    return ("SUMMARY: %d task(s): %d PASS, %d FAIL, %d NO-DATA, %d unverified."
            % (len(tasks), passed, failed, no_data, unverified))


def build_report(root):
    """The full report text: the SUMMARY line, a blank line, then the A1
    one-pager verbatim (via onepager.render, never re-rendered here)."""
    summary = summarize(root)
    page_lines = onepager.render(root)
    lines = [summary, ""] + page_lines
    return "\n".join(one_line(l) if l else "" for l in lines) + "\n"


def could_not_report_text(root, exc):
    """The honest recovery page for when this tool itself breaks. Printed
    instead of a stack trace, and the run still exits 0: a reporter that
    cannot report says so, it does not fail the build over its own bug."""
    detail = one_line("%s: %s" % (type(exc).__name__, exc))
    lines = [
        "BROTHERSBE REPORT for %s" % root,
        "",
        "SUMMARY: could not report because %s." % detail,
        "",
        "This is a failure of the reporter, not a verdict on the change: "
        "this run still exits 0. Nothing above should be read as PASS, "
        "FAIL, NO-DATA, or unverified for any task; none was evaluated.",
    ]
    return "\n".join(one_line(l) if l else "" for l in lines) + "\n"


def safe_report_text(root):
    """The report text for `root`, never raising: `build_report`'s crash is
    caught here exactly as `main` used to catch it inline, so both the plain
    text path and the HTML path (`build_html`, below) share one crash
    boundary instead of two copies of the same try/except."""
    try:
        return build_report(root)
    except Exception as e:  # noqa: intentionally broad, see module docstring
        return could_not_report_text(root, e)


# Lines that name the page itself (the A1 one-pager title, or this file's own
# "could not report" title) become the page <h1>. Numbered section lines
# ("1. WHAT CHANGED", "2. WHAT WAS CHECKED", ...) become <h2>. The SUMMARY
# line becomes a callout paragraph. Every other non-blank line is evidence:
# rendered verbatim, escaped, inside a monospace <pre> block. Nothing here
# reads a verdict or a command off disk; it only wraps text `safe_report_text`
# already produced.
TITLE_LINE_RE = re.compile(r"^BROTHERSBE (ONE-PAGER|REPORT) for ")
SECTION_LINE_RE = re.compile(r"^\d+\.\s+[A-Z]")
SUMMARY_LINE_RE = re.compile(r"^SUMMARY:")

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%(title)s</title>
<style>
body { font-family: Menlo, Consolas, "SF Mono", monospace; background: #f7f8f6; color: #141b22; margin: 2rem auto; max-width: 60rem; padding: 0 1.5rem 3rem; line-height: 1.5; }
h1 { font-size: 1.35rem; border-bottom: 2px solid #141b22; padding-bottom: 0.5rem; }
h2 { font-size: 1.05rem; margin-top: 2rem; }
p.summary { font-weight: bold; background: #eef1ef; border-left: 4px solid #0e7a6f; padding: 0.75rem 1rem; }
pre.evidence { font-family: Menlo, Consolas, "SF Mono", monospace; background: #ffffff; border: 1px solid #d8dbd8; padding: 1rem; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
@media (prefers-color-scheme: dark) {
  body { background: #141b22; color: #f7f8f6; }
  h1 { border-bottom-color: #f7f8f6; }
  p.summary { background: #1d2731; border-left-color: #3aa893; }
  pre.evidence { background: #0e141a; border-color: #2a3742; }
}
</style>
</head>
<body>
%(body)s
</body>
</html>
"""


def render_html_body(lines):
    """Wrap a flat report line list (as `safe_report_text` produces, split on
    newlines) into headings plus monospace evidence blocks. Every non-blank
    line appears exactly once in the output, HTML-escaped: this function adds
    structure, it never adds or drops a claim."""
    parts = []
    buffer = []

    def flush_pre():
        if buffer:
            parts.append("<pre class=\"evidence\">%s</pre>"
                          % "\n".join(buffer))
            buffer[:] = []

    for line in lines:
        if line.strip() == "":
            continue
        escaped = html_lib.escape(line, quote=True)
        if SUMMARY_LINE_RE.match(line):
            flush_pre()
            parts.append("<p class=\"summary\">%s</p>" % escaped)
        elif TITLE_LINE_RE.match(line):
            flush_pre()
            parts.append("<h1>%s</h1>" % escaped)
        elif SECTION_LINE_RE.match(line):
            flush_pre()
            parts.append("<h2>%s</h2>" % escaped)
        else:
            buffer.append(escaped)
    flush_pre()
    return "\n".join(parts)


def build_html(root):
    """The full report as one self-contained HTML file: same text
    `safe_report_text` produces, wrapped in headings and a monospace evidence
    block, every style inline. No external reference of any kind (no `http`
    or `https` URL, no `src=` off disk, no external stylesheet, no
    `@import`, no `url()`, no remote font, no script tag): this is the
    artifact a reviewer opens by double-click with the network off."""
    text = safe_report_text(root)
    body = render_html_body(text.splitlines())
    title = html_lib.escape("BrotherSBE report for %s" % root, quote=True)
    return HTML_TEMPLATE % {"title": title, "body": body}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default=os.getcwd(),
                    help="directory to report on (default: current directory)")
    ap.add_argument("--out", default=None,
                    help="also write the report here (best effort; a write "
                         "problem here is reported, never fails the build)")
    ap.add_argument("--html", default=None,
                    help="also write one self-contained HTML copy here "
                         "(best effort; a write problem here is reported, "
                         "never fails the build)")
    ns = ap.parse_args(argv)
    root = os.path.abspath(ns.target)

    text = safe_report_text(root)

    sys.stdout.write(text)

    if ns.out:
        try:
            d = os.path.dirname(os.path.abspath(ns.out))
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(ns.out, "w") as fh:
                fh.write(text)
        except OSError as e:
            sys.stderr.write("sbe-report: could not write %s (%s), "
                              "report already printed above\n" % (ns.out, e))

    if ns.html:
        try:
            html_text = build_html(root)
            d = os.path.dirname(os.path.abspath(ns.html))
            if d and not os.path.isdir(d):
                os.makedirs(d)
            with open(ns.html, "w") as fh:
                fh.write(html_text)
        except Exception as e:  # noqa: intentionally broad, see module docstring
            sys.stderr.write("sbe-report: could not write html %s (%s), "
                              "report already printed above\n" % (ns.html, e))

    return 0


if __name__ == "__main__":
    sys.exit(main())
