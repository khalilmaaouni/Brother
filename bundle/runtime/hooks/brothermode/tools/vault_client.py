#!/usr/bin/env python3
"""vault_client.py: a minimal, dependency-free Python client for the vault's
served HTTP API (bm_vault_serve.py, WBS row VB3-09).

Stdlib only, no import from this repository: this ONE file is meant to be
lifted onto a second machine, with no checkout and no folklore, and still
talk to a running bm_vault_serve.py.

Usage as a library:
    from vault_client import VaultClient, VaultError
    c = VaultClient("http://127.0.0.1:8377")
    c.health()
    c.recall("what did we decide about X", limit=5)

Usage from the shell:
    python3 vault_client.py health --base-url http://127.0.0.1:8377
    python3 vault_client.py recall "query text" --limit 5 --token s3cret

Every route is called at its versioned path (/v1/health, /v1/recall) by
default; pass legacy=True (or --legacy on the shell) to use the pre-version
unversioned alias instead, for a server too old to have grown /v1/ yet.

Errors: a non-2xx response raises VaultError, carrying the server's own
structured error body (error, code, request_id, missing) as attributes, so
a caller can branch on `.code` (the declared vocabulary bm_vault_serve.py
documents) rather than parsing prose.

No em or en dashes anywhere in this file.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


class VaultError(Exception):
    """Raised for a non-2xx response, or a connection that never got one.
    .status is 0 for the latter case (no HTTP response at all): a caller
    that only checks .status != 200 still sees a failure, never a silent
    None. .error, .code, .request_id and .missing come straight off the
    server's structured error body when there is one, else None."""

    def __init__(self, status, body):
        self.status = status
        self.body = body if isinstance(body, dict) else {}
        self.error = self.body.get("error", "request failed")
        self.code = self.body.get("code")
        self.request_id = self.body.get("request_id")
        self.missing = self.body.get("missing")
        super().__init__("HTTP %s: %s (code=%s, request_id=%s)"
                         % (self.status, self.error, self.code,
                            self.request_id))


class VaultClient:
    """A stranger's client for bm_vault_serve.py. `base_url` carries no
    trailing slash requirement (a trailing slash is stripped), for example
    "http://127.0.0.1:8377" or "https://vault.example:8443"."""

    def __init__(self, base_url, token=None, timeout=30, legacy=False):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.prefix = "" if legacy else "/v1"

    def _request(self, method, path, data=None):
        url = self.base_url + self.prefix + path
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                parsed = json.loads(e.read().decode("utf-8"))
            except (ValueError, OSError):
                parsed = {}
            raise VaultError(e.code, parsed)
        except OSError as e:
            # A connection that never reached the server (refused, DNS,
            # timeout): no HTTP status exists, so status is 0, never a
            # guessed 200 or a bare exception a caller has to catch by a
            # different type than the one every other failure raises.
            raise VaultError(0, {"error": str(e), "code": "connection_error"})

    def health(self):
        """GET /v1/health (or /health with legacy=True) -> the server's
        config-state dict: vault path or NO-DATA, note count, auth posture."""
        return self._request("GET", "/health")

    def recall(self, query, limit=6, identity=None, tenant=None):
        """POST /v1/recall (or /recall with legacy=True) -> the server's
        recall response dict (rows, raw, request_id, ...). Raises
        VaultError on any non-2xx response, the structured shape attached."""
        payload = {"query": query, "limit": limit}
        if identity is not None:
            payload["identity"] = identity
        if tenant is not None:
            payload["tenant"] = tenant
        return self._request("POST", "/recall", data=payload)


def main(argv=None):
    ap = argparse.ArgumentParser(description="minimal vault HTTP client")
    ap.add_argument("--base-url", default="http://127.0.0.1:8377")
    ap.add_argument("--token", default=None)
    ap.add_argument("--legacy", action="store_true",
                    help="use the pre-version unversioned paths")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    rp = sub.add_parser("recall")
    rp.add_argument("query")
    rp.add_argument("--limit", type=int, default=6)
    rp.add_argument("--identity", default=None)
    rp.add_argument("--tenant", default=None)
    args = ap.parse_args(argv)
    client = VaultClient(args.base_url, token=args.token, legacy=args.legacy)
    try:
        if args.cmd == "health":
            result = client.health()
        else:
            result = client.recall(args.query, limit=args.limit,
                                   identity=args.identity, tenant=args.tenant)
    except VaultError as e:
        sys.stderr.write("vault_client: %s\n" % e)
        return 1
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
