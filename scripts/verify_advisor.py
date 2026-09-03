#!/usr/bin/env python3
"""verify_advisor: a local page for checking work before a commit.

One loopback-only http.server serving a single page with four panes:

  CHANGES     what is different right now (git status -sb plus git diff)
  CHECKS      run this repository's own gates and see their verdicts
  ADVISOR     ask a question; a live Claude session answers through a mailbox
  CANDIDATES  vault notes waiting on a human, per the D12 lifecycle contract

Verdicts come only from the gates' own exit codes, this file never
re-derives them: 0 is PASS, 2 is NO-DATA (the estate convention, see
scripts/check_all.sh), any other exit is FAIL, and a command that could
not run at all is NO-DATA, never a pass.

THE CANDIDATES PANE AND WHY IT NEEDS A TOKEN
---------------------------------------------
The Kay Vault's D12 contract (BrotherModeUp tools/bm_vault_lifecycle.py)
says a note is a "candidate" until a named human validates or rejects it;
nothing ranks above candidate on the strength of a model's own say-so. The
promote tool (BrotherModeUp tools/bm_vault_promotions.py) is the only way
to record that. This pane lists whatever the contract currently reads as
"candidate" and offers Validate/Reject buttons that run
`promote --to validated|rejected --by "Khalil Maaouni, via Verify
Advisor" --apply` for exactly the note clicked.

That --by string is legitimate ONLY because the click is the founder's own
action on his own machine's page. Nothing else may mint it: the server
therefore generates a random session token once, at process start, embeds
it in the page it serves, and refuses any /candidates/validate or
/candidates/reject POST whose body does not carry that exact token. A
stray curl, a script, or a request from anywhere but this page's own
running tab gets refused before the promote tool is even invoked. The
token lives only in this process's memory and in the HTML it hands back
over loopback; it is never written to disk or logged.

THE MAILBOX PROTOCOL (the bridge to a live Claude session)
----------------------------------------------------------
Directory: --mailbox (default ~/.claude/verify-advisor-mailbox).

Question file, written by this server when the user asks:
  q-<epoch>-<rand>.json
  {"question": "<the user's words>",
   "context": {"diff_stat": "<git diff --stat tail>",
               "last_check": {"name":..., "verdict":..., "exit_code":...} or null}}

Answer file, written by the answering Claude session, same id:
  a-<epoch>-<rand>.json
  {"answer": "<plain text answer>"}

The page polls GET /answers; the server returns every a-*.json it finds,
keyed by id (<epoch>-<rand>). The server only ever reads and writes this
one directory; the advisor path sends nothing anywhere else.

HTTP surface: GET / (page), GET /diff (escaped HTML), GET /checks,
POST /run {"name":...}, POST /ask {"question":...}, GET /answers,
GET /candidates (escaped HTML), POST /candidates/validate {"path":,
"token":}, POST /candidates/reject {"path":, "token":}.
Binds 127.0.0.1 only. The check list is fixed server-side, and so is the
promote command line: the page can only ever name which note and which
of the two directions, never a command.

origin: a human running this script's own CLI directly (`python3
scripts/verify_advisor.py`, main() at the bottom of this file, which calls
ThreadingHTTPServer(...).serve_forever()), then, in the browser tab that
page serves, typing a question and clicking ask. That click sends a POST
/ask to this same process, whose handler calls write_question(). Confirmed
by grep: nothing else in scripts or bundle/runtime imports verify_advisor or
references write_question (searched "verify_advisor" across scripts,
bundle/runtime, .claude, and the only hits besides this file are
scripts/test_verify_advisor.py, which exercises the module's functions
directly in tests, never a real server).

PRODUCER: this module is the sole producer of its mailbox question files. The
write happens inside write_question(), above, at the `with open(tmp, "w") as
fh: json.dump(payload, fh, indent=1)` plus `os.replace(tmp, path)` call
(lines 309-311 of this file), a write-to-temp-then-rename so a reader in the
mailbox directory never observes a partially written question file.
"""

import argparse
import html
import json
import os
import random
import secrets
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHECK_TIMEOUT = 600
PROMOTE_TIMEOUT = 30
TAIL_CHARS = 8000
PROMOTER_IDENTITY = "Khalil Maaouni, via Verify Advisor"

# Fixed list. The page can only name one of these; nothing from the
# browser is ever executed as a command.
CHECKS = {
    "check_all": ["sh", "scripts/check_all.sh"],
    "pre_push_gate": ["python3", "scripts/pre_push_gate.py"],
}

STATE = {"last_check": None}


def verdict_for_exit(code):
    """The estate convention: 0 PASS, 2 NO-DATA, anything else FAIL."""
    if code == 0:
        return "PASS"
    if code == 2:
        return "NO-DATA"
    return "FAIL"


def run_check(name, repo, checks=None):
    """Run one configured check, return {name, verdict, exit_code, tail}."""
    checks = CHECKS if checks is None else checks
    if name not in checks:
        return {"name": name, "verdict": "NO-DATA", "exit_code": None,
                "tail": "Unknown check name. Nothing was run."}
    cmd = checks[name]
    try:
        proc = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True,
            timeout=CHECK_TIMEOUT)
    except FileNotFoundError as exc:
        return {"name": name, "verdict": "NO-DATA", "exit_code": None,
                "tail": "Could not run the check: %s" % exc}
    except subprocess.TimeoutExpired:
        return {"name": name, "verdict": "NO-DATA", "exit_code": None,
                "tail": "The check ran longer than %ss and was stopped. "
                        "Not a pass, not a failure." % CHECK_TIMEOUT}
    out = (proc.stdout or "") + (proc.stderr or "")
    result = {"name": name, "verdict": verdict_for_exit(proc.returncode),
              "exit_code": proc.returncode, "tail": out[-TAIL_CHARS:]}
    STATE["last_check"] = {k: result[k] for k in ("name", "verdict", "exit_code")}
    return result


def git_text(repo, args):
    try:
        proc = subprocess.run(["git", "-C", repo] + args,
                              capture_output=True, text=True, timeout=30)
        return proc.stdout + (proc.stderr if proc.returncode != 0 else "")
    except Exception as exc:  # noqa: BLE001, boundary call
        return "git could not run: %s" % exc


def render_diff_html(repo):
    """Escaped HTML fragment: branch line, status, then the diff."""
    status = git_text(repo, ["status", "-sb"])
    diff = git_text(repo, ["diff"])
    if not diff.strip():
        diff = "No uncommitted changes right now."
    return ("<pre>%s</pre><pre>%s</pre>"
            % (html.escape(status), html.escape(diff)))


def load_bm_config():
    """(vault, tools_root), the same resolution order every BrotherMode tool
    uses: BROTHERMODE_VAULT / BM_TOOLS env vars first, then
    ~/.claude/bm_vault.json. Either may come back None if unresolved."""
    vault = os.environ.get("BROTHERMODE_VAULT")
    tools_root = os.environ.get("BM_TOOLS")
    if not (vault and tools_root):
        try:
            with open(os.path.expanduser("~/.claude/bm_vault.json")) as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
        vault = vault or cfg.get("vault")
        tools_root = tools_root or cfg.get("tools")
    return vault, tools_root


def _note_title(text, fallback):
    """The first '# ' heading after any frontmatter block, else fallback."""
    end = text.find("\n---", 3) if text.startswith("---") else -1
    body = text[end + 4:] if end != -1 else text
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def list_candidates(vault, tools_root, _lc=None):
    """{"items": [{"path":, "title":}, ...]} or {"error": "..."}.

    Reuses bm_vault_lifecycle.walk/read_promotion (imported, not
    reimplemented) so this pane can never disagree with the contract about
    what counts as a candidate. `_lc` is a test seam only: production
    always imports the real module."""
    if not vault or not os.path.isdir(vault):
        return {"error": "No readable vault configured. Set BROTHERMODE_VAULT "
                          "or check ~/.claude/bm_vault.json."}
    lc = _lc
    if lc is None:
        if not tools_root:
            return {"error": "No tools root configured. Set BM_TOOLS or "
                              "check ~/.claude/bm_vault.json."}
        tools_dir = os.path.join(tools_root, "tools")
        if not os.path.isfile(os.path.join(tools_dir, "bm_vault_lifecycle.py")):
            return {"error": "No promotions tool found under %s." % tools_root}
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        try:
            import bm_vault_lifecycle as lc  # noqa: E402, test seam above
        except ImportError as exc:
            return {"error": "Could not load the promotions tool: %s" % exc}
    items = []
    for path in lc.walk(vault):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            print("verify_advisor: skipping unreadable vault note %s: %s"
                  % (path, exc), file=sys.stderr)
            continue
        state, _record, _problems = lc.read_promotion(text)
        if state != "candidate":
            continue
        rel = os.path.relpath(path, vault)
        items.append({"path": rel, "title": _note_title(text, os.path.basename(rel))})
    items.sort(key=lambda c: c["path"])
    return {"items": items}


def render_candidates_html(vault, tools_root):
    """Escaped HTML fragment, one row per candidate. Each row carries its
    own vault-relative path in a data attribute so the page's buttons post
    it back without ever building HTML or JS out of untrusted text."""
    result = list_candidates(vault, tools_root)
    if "error" in result:
        return "<pre>%s</pre>" % html.escape(result["error"])
    items = result["items"]
    if not items:
        return "<div class=\"sub\">No candidates waiting on a human right now.</div>"
    rows = []
    for c in items:
        rows.append(
            '<div class="msg cand" data-path="%s">'
            '<div>%s</div><div class="who">%s</div>'
            '<button class="validate">Validate</button>'
            '<button class="reject">Reject</button></div>'
            % (html.escape(c["path"], quote=True), html.escape(c["title"]),
               html.escape(c["path"])))
    return "".join(rows)


def run_promote(vault, tools_root, path, to_state):
    """Run the promotions tool's fixed argv for exactly one note. Same
    NO-DATA-never-a-pass posture as run_check."""
    script = os.path.join(tools_root, "tools", "bm_vault_promotions.py")
    cmd = ["python3", script, "promote", "--vault", vault, "--id", path,
           "--to", to_state, "--by", PROMOTER_IDENTITY, "--apply"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=PROMOTE_TIMEOUT)
    except FileNotFoundError as exc:
        return {"error": "Could not run the promotions tool: %s" % exc}
    except subprocess.TimeoutExpired:
        return {"error": "The promotions tool did not finish in time."}
    out = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "tail": out[-TAIL_CHARS:]}


def candidate_action(to_state, body, token, vault, tools_root):
    """Validate or reject one candidate, or refuse. Refuses (without
    running the promote tool at all) unless body["token"] matches this
    server's own session token, and unless body["path"] is a note the
    contract reads as a live candidate right now."""
    supplied = str((body or {}).get("token") or "")
    if not secrets.compare_digest(supplied, token):
        return {"error": "Wrong or missing session token. Reload the page "
                          "and try again."}
    path = str((body or {}).get("path") or "")
    current = list_candidates(vault, tools_root)
    if "error" in current:
        return current
    valid_paths = {c["path"] for c in current["items"]}
    if path not in valid_paths:
        return {"error": "That note is not a live candidate right now. "
                          "Reload the page and try again."}
    return run_promote(vault, tools_root, path, to_state)


def new_question_id():
    return "%d-%04d" % (int(time.time()), random.randint(0, 9999))


MAILBOX_README = """# Verify Advisor mailbox

This folder is the bridge between the Verify Advisor page and a live
Claude session. The contract:

- The page writes q-<epoch>-<rand>.json:
  {"question": "...", "context": {"diff_stat": "...", "last_check": {...} or null}}
- The answering session watches this folder and writes
  a-<epoch>-<rand>.json (same id): {"answer": "..."}
- The page polls the server, the server serves any answer file found.

Nothing here leaves this machine. Delete old pairs freely once read.
"""


def ensure_mailbox(mailbox):
    os.makedirs(mailbox, exist_ok=True)
    readme = os.path.join(mailbox, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w") as fh:
            fh.write(MAILBOX_README)


def write_question(mailbox, question, repo):
    """Write a q-file, return its id."""
    qid = new_question_id()
    diff_stat = git_text(repo, ["diff", "--stat"])[-2000:]
    payload = {"question": question,
               "context": {"diff_stat": diff_stat,
                           "last_check": STATE["last_check"]}}
    path = os.path.join(mailbox, "q-%s.json" % qid)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, path)
    return qid


def read_answers(mailbox):
    """Every a-*.json in the mailbox, keyed by id."""
    answers = {}
    if not os.path.isdir(mailbox):
        return answers
    for fname in sorted(os.listdir(mailbox)):
        if not (fname.startswith("a-") and fname.endswith(".json")):
            continue
        aid = fname[2:-5]
        try:
            with open(os.path.join(mailbox, fname)) as fh:
                answers[aid] = json.load(fh)
        except (OSError, ValueError):
            continue  # half-written file, the next poll gets it
    return answers


PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>Verify Advisor</title>
<style>
:root { --petrol:#0E7A6F; --paper:#F7F8F6; --ink:#141B22; --card:#ffffff; --line:#d8ddda; }
@media (prefers-color-scheme: dark) {
  :root { --petrol:#3AA893; --paper:#141B22; --ink:#e8ecea; --card:#1c2530; --line:#2c3844; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink);
  font:15px/1.5 Seravek, "Gill Sans", system-ui, sans-serif; }
header { padding:14px 20px; border-bottom:2px solid var(--petrol); }
h1 { font-family:"Iowan Old Style", Georgia, serif; font-size:22px; margin:0; }
h1 span { color:var(--petrol); }
.sub { font-size:13px; opacity:.75; }
main { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:14px; padding:14px 20px; }
.cand button { margin-top:4px; }
section { background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:12px 14px; min-height:300px; }
h2 { font-size:15px; margin:0 0 8px; color:var(--petrol);
  text-transform:uppercase; letter-spacing:.06em; }
pre { white-space:pre-wrap; word-break:break-word; font:12px/1.45 ui-monospace, Menlo, monospace;
  background:transparent; margin:6px 0; max-height:420px; overflow:auto; }
button { background:var(--petrol); color:#fff; border:0; border-radius:6px;
  padding:7px 14px; font:inherit; cursor:pointer; margin:2px 6px 8px 0; }
button:disabled { opacity:.5; }
textarea { width:100%; min-height:70px; font:inherit; border:1px solid var(--line);
  border-radius:6px; background:var(--paper); color:var(--ink); padding:8px; }
.verdict { font-weight:bold; }
.msg { border-left:3px solid var(--petrol); padding:6px 10px; margin:8px 0;
  background:var(--paper); border-radius:0 6px 6px 0; }
.msg.q { border-left-color:var(--line); }
.who { font-size:12px; opacity:.7; }
</style></head><body>
<header><h1><span>Verify</span> Advisor</h1>
<div class="sub">Your changes, this repository's own checks, and a place to ask before you commit.</div></header>
<main>
<section><h2>Changes</h2>
<div class="sub">What is different right now. Refreshes on its own.</div>
<div id="diff">Loading the current changes...</div></section>
<section><h2>Checks</h2>
<div class="sub">Each button runs one of this repository's own gates. The verdict is the gate's own exit code.</div>
<div id="checkbtns"></div><div id="checkout"></div></section>
<section><h2>Advisor</h2>
<div class="sub">Ask anything about the changes. A live Claude session answers here.</div>
<textarea id="q" placeholder="For example: is this change safe to commit?"></textarea>
<button id="ask">Ask</button>
<div id="chat"></div></section>
<section><h2>Candidates</h2>
<div class="sub">Vault notes waiting on a human. Validate or Reject records who and when;
only this page's own open session can click these buttons, so a stray script cannot.</div>
<div id="candidates">Loading candidates...</div></section>
</main>
<script>
var SESSION_TOKEN = "__SESSION_TOKEN__";
var asked = {};
function esc(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
function refreshDiff(){
  fetch('/diff').then(function(r){return r.text();}).then(function(t){
    document.getElementById('diff').innerHTML = t; });
}
refreshDiff(); setInterval(refreshDiff, 5000);
fetch('/checks').then(function(r){return r.json();}).then(function(names){
  var box = document.getElementById('checkbtns');
  names.forEach(function(n){
    var b = document.createElement('button'); b.textContent = 'Run ' + n;
    b.onclick = function(){
      b.disabled = true;
      document.getElementById('checkout').innerHTML = '<pre>Running ' + esc(n) + '...</pre>';
      fetch('/run', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name:n})})
      .then(function(r){return r.json();}).then(function(res){
        b.disabled = false;
        document.getElementById('checkout').innerHTML =
          '<div class="verdict">' + esc(res.name) + ': ' + esc(res.verdict) +
          ' (exit code ' + esc(String(res.exit_code)) + ')</div><pre>' + esc(res.tail) + '</pre>';
      });
    };
    box.appendChild(b);
  });
});
function refreshCandidates(){
  fetch('/candidates').then(function(r){return r.text();}).then(function(t){
    var box = document.getElementById('candidates');
    box.innerHTML = t;
    box.querySelectorAll('.cand').forEach(function(row){
      var path = row.dataset.path;
      function post(kind, btn){
        btn.disabled = true;
        fetch('/candidates/' + kind, {method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({path:path, token:SESSION_TOKEN})})
        .then(function(r){return r.json();}).then(function(out){
          if(out.error){ alert(out.error); btn.disabled = false; return; }
          refreshCandidates();
        });
      }
      row.querySelector('.validate').onclick = function(){ post('validate', this); };
      row.querySelector('.reject').onclick = function(){ post('reject', this); };
    });
  });
}
refreshCandidates(); setInterval(refreshCandidates, 8000);
document.getElementById('ask').onclick = function(){
  var t = document.getElementById('q');
  var text = t.value.trim(); if(!text) return;
  fetch('/ask', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({question:text})})
  .then(function(r){return r.json();}).then(function(res){
    asked[res.id] = true;
    var c = document.getElementById('chat');
    c.insertAdjacentHTML('beforeend',
      '<div class="msg q"><div class="who">You</div>' + esc(text) + '</div>' +
      '<div class="msg" id="a-' + esc(res.id) + '"><div class="who">Advisor</div>Waiting for the live session to answer...</div>');
    t.value = '';
  });
};
setInterval(function(){
  fetch('/answers').then(function(r){return r.json();}).then(function(all){
    Object.keys(all).forEach(function(id){
      var slot = document.getElementById('a-' + id);
      if (slot && slot.dataset.done !== '1') {
        slot.innerHTML = '<div class="who">Advisor</div>' + esc(all[id].answer || '');
        slot.dataset.done = '1';
      }
    });
  });
}, 3000);
</script></body></html>
"""


def make_handler(repo, mailbox, vault, tools_root, token):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body, ctype="application/json"):
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            if self.path == "/":
                self._send(PAGE.replace("__SESSION_TOKEN__", token), "text/html")
            elif self.path == "/diff":
                self._send(render_diff_html(repo), "text/html")
            elif self.path == "/checks":
                self._send(json.dumps(sorted(CHECKS.keys())))
            elif self.path == "/answers":
                self._send(json.dumps(read_answers(mailbox)))
            elif self.path == "/candidates":
                self._send(render_candidates_html(vault, tools_root), "text/html")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path == "/run":
                name = str(self._body_json().get("name", ""))
                self._send(json.dumps(run_check(name, repo)))
            elif self.path == "/ask":
                question = str(self._body_json().get("question", "")).strip()
                if not question:
                    self._send(json.dumps({"error": "empty question"}))
                    return
                qid = write_question(mailbox, question, repo)
                self._send(json.dumps({"id": qid}))
            elif self.path == "/candidates/validate":
                self._send(json.dumps(candidate_action(
                    "validated", self._body_json(), token, vault, tools_root)))
            elif self.path == "/candidates/reject":
                self._send(json.dumps(candidate_action(
                    "rejected", self._body_json(), token, vault, tools_root)))
            else:
                self.send_error(404)

        def log_message(self, fmt, *args):
            pass  # quiet by default; this is a local tool

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repo", default=None,
                        help="repository to watch (default: this script's repo)")
    parser.add_argument("--mailbox",
                        default=os.path.expanduser("~/.claude/verify-advisor-mailbox"))
    args = parser.parse_args()
    repo = args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ensure_mailbox(args.mailbox)
    vault, tools_root = load_bm_config()
    token = secrets.token_hex(16)
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(repo, args.mailbox, vault, tools_root, token))
    print("Verify Advisor is at http://127.0.0.1:%d (repo: %s)" % (args.port, repo))
    print("Mailbox: %s" % args.mailbox)
    print("Vault: %s" % (vault or "NO-DATA (set BROTHERMODE_VAULT or ~/.claude/bm_vault.json)"))
    server.serve_forever()


if __name__ == "__main__":
    main()
