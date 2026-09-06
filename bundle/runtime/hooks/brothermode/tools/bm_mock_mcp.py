#!/usr/bin/env python3
"""A local, MCP-shaped JSON-RPC mock server, one per vendor surface.

WHY THIS EXISTS: bm_connectors.py's catalog carries the RESEARCHED, verified
tool names for each connector, but every real endpoint needs a founder-only
credential (OAuth consent, an app registration, a linked workspace). This
mock lets the connector layer's conformance logic exercise the real wire
shape, tools/list -> tools/call, tonight, with zero keys and zero enterprise
exposure: every response is synthetic fixture data invented from whole
cloth and labeled synthetic, never a real name or a real row.

PROTOCOL: MCP's stdio transport is newline-delimited JSON-RPC (one message
per line, no Content-Length framing). This script reads request lines from
stdin and writes response lines to stdout, nothing else. It never imports
`socket` and never binds a port: stdio is the only transport, on purpose,
so it can never be mistaken for a live network-facing server.

METHODS: initialize, tools/list, tools/call. tools/list always serves the
catalog's own tool-name list for the profile (imported from bm_connectors,
never re-typed), so the mock and the catalog cannot silently drift apart:
a test that removes one name from a mock's declared set would need to
duplicate the list here to do it, which is exactly the drift this design
prevents by construction.

CALIBRATION FLAGS, for driving conformance checks backwards on purpose:
  --misbehave acl                 a tool call leaks a restricted fixture
                                   item that a declared policy should have
                                   trimmed.
  --misbehave stale-delete        a deleted fixture item keeps reappearing
                                   on the next list-shaped call instead of
                                   staying gone; also the deletion-
                                   convergence canary's forced state.
  --misbehave no-acl-propagation  an acl_set call reports ok=True but never
                                   takes effect: acl_view keeps reporting
                                   the OLD state forever. The acl-
                                   propagation canary's forced state.
  --misbehave identity-mismatch   identity_resolve returns a DIFFERENT
                                   fixture principal's vault mapping instead
                                   of the caller's own. The identity-mapping
                                   canary's forced state.
  --misbehave watermark-drift     watermark_advance bumps the content
                                   watermark but leaves the acl watermark
                                   behind, so the two stop advancing
                                   together. The watermark-coupling
                                   canary's forced state.
None is on by default; a plain mock passes every canary at once.

Python 3.9 floor, standard library only.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bm_connectors import BY_ID  # noqa: E402

# The mock's --profile names read naturally for the surface being faked
# (Databricks' MCP tools are the Genie tools specifically), which is why this
# differs from the catalog's own "databricks" id; conformance in
# bm_connectors.py owns the mapping back to the catalog id.
PROFILE_TO_CATALOG_ID = {
    "snowflake": "snowflake",
    "databricks-genie": "databricks",
    "github": "github",
    "bitbucket": "bitbucket",
    "teams": "teams",
}

SYNTHETIC_ITEM = {
    "id": "fixture-001",
    "label": "synthetic fixture row, invented for this mock",
    "synthetic": True,
}
RESTRICTED_ITEM = {
    "id": "fixture-restricted-002",
    "label": "synthetic restricted row a declared policy should trim",
    "synthetic": True,
    "restricted": True,
}

# Fixture identity map for the identity-mapping canary: a synthetic source
# principal from the connector's own directory resolving to the vault
# principal that should own its actions. bm_connectors.py's canary check
# hardcodes the SAME pairing (SOURCE_PRINCIPAL / EXPECTED_VAULT_PRINCIPAL)
# rather than importing this dict, to avoid a circular import (this module
# already imports BY_ID from bm_connectors); a drift test in
# test_bm_connectors.py keeps the two pairings honest.
IDENTITY_MAP = {
    "src-user-1": "vault-user-1",
    "src-user-2": "vault-user-2",
}
# The wrong mapping identity-mismatch returns: a real, differently-owned
# principal, never "unknown", because a wrong live-looking answer is the
# failure mode worth catching (an "unknown" response would fail loudly on
# its own and needs no canary).
WRONG_IDENTITY_MAP = {
    "src-user-1": "vault-user-2",
    "src-user-2": "vault-user-1",
}


def tool_names_for(profile):
    return list(BY_ID[PROFILE_TO_CATALOG_ID[profile]]["tools"])


class MockState(object):
    """Per-session fixture state: one deletable item, one ACL-restricted
    item, one togglable ACL gate, an identity directory, and a pair of
    freshness watermarks. Well-behaved by default; --misbehave flips one
    rule at a time so a conformance or canary check has exactly one thing
    to catch per run."""

    def __init__(self, profile, misbehave):
        self.profile = profile
        self.misbehave = misbehave
        self.deleted = False
        # acl-propagation canary: starts restricted, like a real ACL gate
        # defaults to deny until an explicit grant opens it.
        self.acl_restricted = True
        # watermark-coupling canary: content and acl cursors start in sync.
        self.content_watermark = 0
        self.acl_watermark = 0

    def call(self, tool_name, arguments):
        arguments = arguments or {}
        action = arguments.get("action", "list")
        if action == "delete":
            self.deleted = True
            return {"tool": tool_name, "profile": self.profile, "synthetic": True,
                     "ok": True, "deleted_id": SYNTHETIC_ITEM["id"]}
        if action == "acl_set":
            restricted = bool(arguments.get("restricted", True))
            if self.misbehave != "no-acl-propagation":
                self.acl_restricted = restricted
            # A misbehaving gate still ACKS the call; the failure is that
            # the state underneath never moved, exactly like a real ACL
            # write that returns 200 into a cache nobody invalidated.
            return {"tool": tool_name, "profile": self.profile, "synthetic": True,
                     "ok": True, "restricted": self.acl_restricted}
        if action == "acl_view":
            return {"tool": tool_name, "profile": self.profile, "synthetic": True,
                     "restricted": self.acl_restricted}
        if action == "identity_resolve":
            source = arguments.get("source_principal")
            table = WRONG_IDENTITY_MAP if self.misbehave == "identity-mismatch" else IDENTITY_MAP
            return {"tool": tool_name, "profile": self.profile, "synthetic": True,
                     "source_principal": source,
                     "vault_principal": table.get(source, "unknown")}
        if action == "watermark_advance":
            self.content_watermark += 1
            if self.misbehave != "watermark-drift":
                self.acl_watermark += 1
            return {"tool": tool_name, "profile": self.profile, "synthetic": True,
                     "content_watermark": self.content_watermark,
                     "acl_watermark": self.acl_watermark}
        if action == "watermark_read":
            return {"tool": tool_name, "profile": self.profile, "synthetic": True,
                     "content_watermark": self.content_watermark,
                     "acl_watermark": self.acl_watermark}
        # A well-behaved mock drops the item once deleted; the stale-delete
        # misbehavior keeps serving it anyway.
        items = []
        if not self.deleted or self.misbehave == "stale-delete":
            items.append(dict(SYNTHETIC_ITEM))
        # A well-behaved mock never serves the restricted item at all,
        # because a declared policy would have trimmed it upstream; the acl
        # misbehavior leaks it through untrimmed.
        if self.misbehave == "acl":
            items.append(dict(RESTRICTED_ITEM))
        return {"tool": tool_name, "profile": self.profile, "synthetic": True,
                 "items": items}


def _reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def handle(req, profile, tools, state):
    method = req.get("method")
    rid = req.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _reply(rid, {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "bm-mock-%s" % profile, "version": "0.1.0",
                           "synthetic": True},
            "capabilities": {"tools": {}},
        })
    if method == "tools/list":
        return _reply(rid, {"tools": [
            {"name": n, "description": "synthetic mock tool for %s" % profile,
             "inputSchema": {"type": "object", "properties": {}}}
            for n in tools
        ]})
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        if name not in tools:
            return _reply(rid, error={"code": -32602,
                                       "message": "unknown tool %r" % name})
        result = state.call(name, params.get("arguments"))
        return _reply(rid, {"content": [{"type": "text", "text": json.dumps(result)}]})
    return _reply(rid, error={"code": -32601, "message": "method not found: %s" % method})


def serve(profile, misbehave, stdin=sys.stdin, stdout=sys.stdout):
    tools = tool_names_for(profile)
    state = MockState(profile, misbehave)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:  # sbe: allow-silent test double for a JSON-RPC server; a malformed input line from the test harness is simply not a request, no reply expected
            continue
        resp = handle(req, profile, tools, state)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True, choices=sorted(PROFILE_TO_CATALOG_ID))
    ap.add_argument("--misbehave", choices=("acl", "stale-delete",
                                            "no-acl-propagation",
                                            "identity-mismatch",
                                            "watermark-drift"))
    args = ap.parse_args(argv)
    serve(args.profile, args.misbehave)
    return 0


if __name__ == "__main__":
    sys.exit(main())
