#!/usr/bin/env python3
"""bm_vault_pane.py: the approval pane, pending promotions and curation
accepts, click-driven (WBS row VB11-05).

Extends tools/bm_vault_serve.py's own pattern -- the estate's one HTTP
server, and the only precedent for a pane transport this tree has: binds
127.0.0.1, refuses a non-loopback bind without a shared-secret token file,
started deliberately, never wired into a hook. This module adds a second
route pair to that same posture rather than inventing a fresh one.

WHY THIS EXISTS. bm_vault_promotions.py records a promotion; bm_vault_curate.py
records a curation accept or reject. Neither had a click surface: a promotion
or a curation candidate could only move by someone typing the CLI command by
hand. This module never reimplements either write. Every Approve or Reject
click runs the REAL command (imported, called directly, its own stdout
captured and returned) under the CLICKING PRINCIPAL's own name, exactly the
--by / --as a human typing the command would have supplied.

ROLE REQUIREMENT, ANSWERED TODAY THROUGH THE REGISTRY SEAM. A revoked
principal's click is refused via bm_vault_principals.status_of, the
registry's own semantics (mirrors bm_vault.py's own --as revocation check,
same refusal string shape: "principal %r is revoked"). An unregistered name
(no registry file, or a name the registry has never heard of) is allowed,
the same opt-in-per-identity stance every consult of this registry already
takes. ENTRA BINDS THIS AT SERVICE MODE (the approved VB10-05 contract, not
built here): this file claims nothing mechanical about Entra. On this
machine the registry file is the whole answer, and it is a JSON file with no
write control of its own -- see bm_vault_principals.py's own TRUST BOUNDARY
paragraph, which applies here unchanged.

ACTION TOKENS. GET /pending mints one HMAC token per (kind, id, decision)
triple, keyed to a random secret generated once when this process starts
(never persisted, never logged, never derived from the optional bearer
token). POST /act must echo the exact token that accompanied the item and
decision it names; a wrong, mismatched or missing token is refused before
anything is read from or written to the vault or the curation queue. This
guards against a crafted request naming an id or decision this server never
actually offered; it is not an authorization system on its own -- the
bearer token (identical, optional, localhost-open-by-default posture as
bm_vault_serve.py) and the principal registry carry the actual answer to who
may act.

Endpoints:
  GET  /pending  {"vault": ..., "promotions": [...], "curation": [...]},
                 each entry carrying action_tokens for "approve" and
                 "reject". Read-only: walks the vault and reads the curation
                 queue file, writes nothing, ever. An empty result still
                 names the vault it looked in rather than a bare empty list.
  POST /act      {"kind": "promotion"|"curation", "id": "...",
                 "decision": "approve"|"reject", "principal": "...",
                 "action_token": "..."} runs the real ceremony command under
                 principal and reports its own exit code and printed text.
                 The outcome (principal, and on success the affected id)
                 lands in bm_vault_audit's access audit, the one audit this
                 estate already has that records who acted.

Auth: identical posture to bm_vault_serve.py, reusing its own read_token and
LOOPBACK rather than a second copy: binds 127.0.0.1 by default; --bind on
any other interface refuses to start without --token-file. This file adds no
TLS of its own -- that transport question belongs to bm_vault_serve.py and
sits outside this row's scope.

No em or en dashes anywhere in this file.
"""
import argparse
import contextlib
import datetime
import hashlib
import hmac
import io
import json
import os
import secrets
import sys
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bm_vault_serve as sv           # noqa: E402  (LOOPBACK, read_token: reused, not copied)
import bm_vault_lifecycle as lifecycle  # noqa: E402
import bm_vault_promotions as promotions  # noqa: E402
import bm_vault_curate as curate      # noqa: E402
import bm_vault_principals as principals_mod  # noqa: E402
import bm_vault_audit as audit        # noqa: E402

MAX_BODY = 65536

#: One secret per server process. Never persisted, never logged, never
#: echoed back by any endpoint. Signs (kind, id, decision) triples so a
#: POST /act can only ever name an item this exact process offered through
#: a prior GET /pending.
_SECRET = secrets.token_bytes(32)


def _sign(kind, item_id, decision):
    msg = ("%s|%s|%s" % (kind, item_id, decision)).encode("utf-8")
    return hmac.new(_SECRET, msg, hashlib.sha256).hexdigest()


def _verify(kind, item_id, decision, token):
    if not isinstance(token, str) or not token:
        return False
    return secrets.compare_digest(_sign(kind, item_id, decision), token)


def _default_vault():
    """Same env/config resolution as every sibling tool, imported rather
    than recomputed: a NO-DATA reason string when nothing is configured,
    never a guessed path."""
    try:
        import bm_vault
        return bm_vault._default_vault()
    except Exception as e:  # pragma: no cover
        sys.stderr.write("bm_vault_pane: cannot import bm_vault: %s\n" % e)
        return None


def _pending_promotions(vault):
    """Every note in "candidate" or "validated" state: the two states with a
    legal forward move (bm_vault_lifecycle.LEGAL). "rejected", "canonical"
    and "legacy" notes carry nothing to approve and are left out."""
    items = []
    for path in lifecycle.walk(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:  # sbe: allow-silent an unreadable note is excluded from the pending list, never a crash for the whole scan
            continue
        state, _record, _problems = lifecycle.read_promotion(text)
        if state not in ("candidate", "validated"):
            continue
        target = "validated" if state == "candidate" else "canonical"
        items.append({
            "id": rel, "path": rel, "state": state, "approve_target": target,
            "action_tokens": {"approve": _sign("promotion", rel, "approve"),
                              "reject": _sign("promotion", rel, "reject")},
        })
    return items


def _curation_item_id(pair):
    return "%s|%s" % (pair[0], pair[1])


def _pending_curation(queue_path):
    """(items, error). error is a NO-DATA string, never raised, when the
    queue file exists but cannot be parsed; a MISSING queue file is not an
    error, curate._load_queue's own opt-in shape, just an empty list."""
    try:
        data = curate._load_queue(queue_path)
    except RuntimeError as e:
        return [], "NO-DATA: %s" % e
    items = []
    for e in data.get("queue", []):
        pair = e.get("pair")
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        item_id = _curation_item_id(pair)
        # ponytail: the edge type is guessed from which finder(s) surfaced the
        # pair (duplicate implies supersedes, everything else relates) rather
        # than offered as a separate choice in the pane; add an explicit edge
        # picker if a real approver ever needs to override this guess.
        edge = "supersedes" if "duplicate" in (e.get("finders") or {}) else "relates"
        items.append({
            "id": item_id, "pair": pair, "edge": edge,
            "finders": e.get("finders", {}), "combined": e.get("combined"),
            "action_tokens": {"approve": _sign("curation", item_id, "approve"),
                              "reject": _sign("curation", item_id, "reject")},
        })
    return items, None


def _principal_refusal(vault, principal):
    """The registry's own refusal string, or None when the principal may
    act (unregistered, active, or the registry itself is absent -- the same
    opt-in stance bm_vault.py's own --as consult already takes). A broken
    registry file is reported as a refusal too: a malformed access-control
    file must never silently become "everyone may act"."""
    rpath = principals_mod.registry_path(vault)
    registry, problems = principals_mod.load(rpath)
    if problems:
        return "principal registry unreadable: %s" % "; ".join(problems)
    if principals_mod.status_of(registry, principal) == "revoked":
        return "principal %r is revoked" % principal
    return None


def _pick_edge(queue_path, pair):
    try:
        data = curate._load_queue(queue_path)
    except RuntimeError:
        return "relates"
    entry = curate._find_entry(data.get("queue", []), pair)
    if entry and "duplicate" in (entry.get("finders") or {}):
        return "supersedes"
    return "relates"


def _do_promotion(vault, rel, decision, principal):
    """(rc, output). rc mirrors bm_vault_promotions.cmd_promote's own exit
    codes: 0 recorded, 1 refused (illegal move or no frontmatter), 2
    NO-DATA (the id resolves nowhere)."""
    if not os.path.isfile(os.path.join(vault, rel)):
        return 2, "NO-DATA: %r resolves to no note under %s" % (rel, vault)
    with open(os.path.join(vault, rel), encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    state, _record, _problems = lifecycle.read_promotion(text)
    if decision == "approve":
        target = {"candidate": "validated", "validated": "canonical"}.get(state)
    else:
        target = "rejected"
    if target is None:
        return 1, ("REFUSED: %s is in state %s, which has no legal forward "
                    "move" % (rel, state))
    at = datetime.date.today().isoformat()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = promotions.cmd_promote(vault, rel, target, principal, at, True)
    return rc, buf.getvalue()


def _do_curation(vault, queue_path, item_id, decision, principal):
    parts = item_id.split("|", 1)
    if len(parts) != 2 or not all(parts):
        return 2, "NO-DATA: malformed curation id %r" % item_id
    pair = list(parts)
    ns = types.SimpleNamespace(vault=vault, queue=queue_path,
                               pair="%s,%s" % (pair[0], pair[1]), by=principal,
                               apply=True)
    buf = io.StringIO()
    if decision == "approve":
        ns.edge = _pick_edge(queue_path, pair)
        with contextlib.redirect_stdout(buf):
            rc = curate.cmd_accept(ns)
    else:
        with contextlib.redirect_stdout(buf):
            rc = curate.cmd_reject(ns)
    return rc, buf.getvalue()


def make_handler(token, queue_path):
    class Handler(BaseHTTPRequestHandler):
        server_version = "bm_vault_pane"

        def _send(self, code, obj):
            body = json.dumps(obj, indent=1).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # Every response, GET and POST alike: a stale cached pane page
            # showing a since-decided item as still pending is exactly the
            # bug this header exists to prevent.
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            if token is None:
                return True
            header = self.headers.get("Authorization", "")
            expected = "Bearer " + token
            return secrets.compare_digest(header.encode("utf-8"),
                                          expected.encode("utf-8"))

        def do_GET(self):
            if not self._authorized():
                return self._send(401, {"error": "bearer token required"})
            if self.path != "/pending":
                return self._send(404, {"error": "unknown path %s" % self.path})
            vault = _default_vault()
            if not vault or not os.path.isdir(vault):
                return self._send(200, {
                    "vault": "NO-DATA: no vault configured (BM_VAULT_ROOT, "
                             "BROTHERMODE_VAULT or ~/.claude/bm_vault.json)",
                    "promotions": [], "curation": [],
                    "no_data": "no vault configured",
                })
            promos = _pending_promotions(vault)
            curation, cur_err = _pending_curation(queue_path)
            resp = {"vault": vault, "promotions": promos, "curation": curation}
            if cur_err:
                resp["curation_error"] = cur_err
            if not promos and not curation:
                resp["no_data"] = "no pending promotions or curation candidates in %s" % vault
            self._send(200, resp)

        def do_POST(self):
            if not self._authorized():
                return self._send(401, {"error": "bearer token required"})
            if self.path != "/act":
                return self._send(404, {"error": "unknown path %s" % self.path})
            try:
                length = min(int(self.headers.get("Content-Length", 0)), MAX_BODY)
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(req, dict):
                    raise ValueError("body must be a JSON object")
            except (ValueError, OSError) as e:
                return self._send(400, {"error": "bad request body: %s" % e})
            kind = req.get("kind")
            item_id = req.get("id")
            decision = req.get("decision")
            principal = req.get("principal")
            action_token = req.get("action_token")
            if kind not in ("promotion", "curation"):
                return self._send(400, {"error": "\"kind\" must be promotion or curation"})
            if decision not in ("approve", "reject"):
                return self._send(400, {"error": "\"decision\" must be approve or reject"})
            if not isinstance(item_id, str) or not item_id:
                return self._send(400, {"error": "\"id\" must be a non-empty string"})
            if not isinstance(principal, str) or not principal.strip():
                return self._send(400, {"error": "\"principal\" must be a non-empty string"})
            # Backwards check FIRST: nothing below this line runs, nothing is
            # read from or written to the vault or the queue, on a wrong,
            # stale or missing token.
            if not _verify(kind, item_id, decision, action_token):
                return self._send(400, {"error": "invalid or missing action token"})
            vault = _default_vault()
            if not vault or not os.path.isdir(vault):
                return self._send(404, {"error": "NO-DATA: no vault configured"})
            refusal = _principal_refusal(vault, principal)
            if refusal:
                event_id = audit.new_event_id()
                audit.append(principal=principal,
                            query="pane:%s:%s id=%s" % (kind, decision, item_id),
                            served_ids=[], withheld_count=0, event_id=event_id,
                            refused=refusal)
                return self._send(403, {"error": refusal, "event_id": event_id})
            if kind == "promotion":
                rc, output = _do_promotion(vault, item_id, decision, principal)
            else:
                rc, output = _do_curation(vault, queue_path, item_id, decision, principal)
            event_id = audit.new_event_id()
            audit.append(principal=principal,
                        query="pane:%s:%s id=%s" % (kind, decision, item_id),
                        served_ids=[item_id] if rc == 0 else [],
                        withheld_count=0, event_id=event_id)
            status_code = 200 if rc == 0 else (404 if rc == 2 else 409)
            self._send(status_code, {
                "kind": kind, "id": item_id, "decision": decision,
                "principal": principal, "rc": rc, "output": output,
                "event_id": event_id,
            })

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8378)
    ap.add_argument("--token-file", default=None,
                    help="file holding the shared bearer secret; required for "
                         "any non-loopback --bind")
    ap.add_argument("--queue", default=curate.DEFAULT_QUEUE,
                    help="curation queue file (bm_vault_curate.py's own)")
    args = ap.parse_args(argv)
    token = None
    if args.token_file:
        try:
            token = sv.read_token(args.token_file)
        except (OSError, ValueError) as e:
            sys.stderr.write("bm_vault_pane: REFUSING to start: %s\n" % e)
            return 2
    if args.bind not in sv.LOOPBACK and token is None:
        sys.stderr.write(
            "bm_vault_pane: REFUSING to start: --bind %s is not loopback and "
            "no --token-file is set; an approval pane open on a real "
            "interface lets any stranger click Approve\n" % args.bind)
        return 2
    srv = ThreadingHTTPServer((args.bind, args.port),
                              make_handler(token, args.queue))
    sys.stderr.write("bm_vault_pane: serving on %s:%d (%s)\n"
                     % (args.bind, srv.server_address[1],
                        "bearer auth" if token else "localhost open"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:  # sbe: allow-silent Ctrl+C is the normal way to stop this server, finally below still closes it cleanly
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
