"""Self test for docs/ba-status.html's pure rendering logic.

The page has no build step, so its logic lives inline in a <script id="logic">
tag as plain functions exported for both node (module.exports) and the
browser (window.BAStatus). This test extracts that script body, runs it
under node against this repo's own real fixture files (design/field-book),
and asserts the parsed and rendered output. Requires node; if node is not on
PATH this test reports NO-DATA rather than a false pass.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "ba-status.html"
FIXTURE_DOSSIER = ROOT / "design" / "field-book"


def extract_logic_script(html_text):
    match = re.search(
        r'<script id="logic">(.*?)</script>', html_text, re.DOTALL
    )
    assert match, "docs/ba-status.html must carry a <script id=\"logic\"> block"
    return match.group(1)


def run_node_check(logic_js, intake_json, behaviour_md):
    harness = """
const BAStatus = (() => {
  const module = { exports: {} };
  %s
  return module.exports;
})();

const intakeText = %s;
const behaviourText = %s;

const intake = BAStatus.parseIntake(intakeText);
const rows = BAStatus.parseBehaviourRows(behaviourText);
if (intake.tier !== "T2") throw new Error("expected tier T2, got " + intake.tier);
if (rows.length !== 4) throw new Error("expected 4 behaviour rows, got " + rows.length);
if (rows[0].id !== "B1") throw new Error("expected first row id B1, got " + rows[0].id);

const rendered = BAStatus.renderReport({
  dossierPath: "field-book",
  intake: intake,
  behaviourRows: rows
});
if (rendered.indexOf("T2") === -1) throw new Error("rendered report missing tier T2");
if (rendered.indexOf("B1") === -1) throw new Error("rendered report missing row id B1");
if (rendered.indexOf("Documents complete") === -1) {
  throw new Error("rendered report missing the documents-complete status line");
}

const missing = BAStatus.renderReport({ dossierPath: "x", intake: null, behaviourRows: null });
if (missing.indexOf("Not started here") === -1) {
  throw new Error("missing-documents case did not report Not started here");
}

console.log("OK");
""" % (logic_js, json.dumps(intake_json), json.dumps(behaviour_md))
    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    return result


def main():
    if shutil.which("node") is None:
        print("NO-DATA: node not found on PATH, cannot execute the page's JS logic")
        return 0
    html_text = PAGE.read_text()
    logic_js = extract_logic_script(html_text)
    intake_json = (FIXTURE_DOSSIER / "00-intake.json").read_text()
    behaviour_md = (FIXTURE_DOSSIER / "08-behaviour.md").read_text()
    result = run_node_check(logic_js, intake_json, behaviour_md)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0 or "OK" not in result.stdout:
        print("FAIL: ba-status.html logic check did not pass")
        return 1
    print("PASS: ba-status.html logic parses and renders the field-book fixture correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
