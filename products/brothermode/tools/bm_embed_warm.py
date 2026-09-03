#!/usr/bin/env python3
"""bm_embed_warm.py: the dense embedder held warm in memory (WBS row VB5-06).

WHY. bm_vault.py's dense path pays 7-9 SECONDS on every call to
tools/bm-embed-bge: a fresh subprocess that imports sentence_transformers
and loads BAAI/bge-small-en-v1.5 from scratch every time. VB5-03 measured
this and deliberately did NOT build a daemon then, because "a genuine
long-lived model-server process would need a socket, a lifecycle, and its
own failure handling, none of it measured or built" -- it built a query-text
cache instead, which only helps a REPEAT of the exact same wording. This
file is the daemon VB5-03 left open: a small loopback HTTP server holding
the SentenceTransformer model in memory across calls, so a genuinely new
query still pays the model's per-call encode time but never the 7-9s load.

CONTRACT. POST /embed {"texts": [{"id": int, "text": str, "query": bool}]}
returns {"vecs": [{"id": int, "v": [float]}]}. The rounding and the
instruction prefix are the SAME ones tools/bm_embed_bge.py already uses
(imported, not copied), so a warm vector and a subprocess vector for the
same input are identical, not merely close -- proven by
test_bm_embed_warm.py's identity test.

BIND POSTURE, copied from bm_vault_serve.py's own gate rather than
reinvented: binds 127.0.0.1 by default; --bind on any other interface
REFUSES to start without --token-file (bm_vault_serve.read_token and
bm_vault_serve.LOOPBACK, reused not copied); localhost without a token
stays open. Never wired into a hook; started deliberately with
`python3 bm_embed_warm.py serve`.

CLIENT SEAM. embed_via_warm(pairs, query) is the dense path's new first
hop (see bm_vault.py's _embed_texts): a TCP connect with a ~200ms timeout
against 127.0.0.1:PORT, and on ANY failure -- no daemon, refused, timeout,
malformed reply -- it returns None immediately. A NO-DATA note goes to
stderr only when BM_EMBED_WARM_DEBUG is set, never by default: an absent
daemon is the expected common case, not an error worth narrating on every
call. bm_vault.py's own _embed_texts tries this first and falls back to
_embed_texts_subprocess (the original, unchanged path) whenever it returns
None, so the vault behaves exactly as before when the warm process is
absent.

No em or en dashes anywhere in this file.
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bm_vault_serve as sv          # noqa: E402  (LOOPBACK, read_token: reused, not copied)
from bm_embed_bge import QUERY_PREFIX  # noqa: E402  (identical prefix, not a second copy)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8379
CONNECT_TIMEOUT = 0.2   # seconds: the dense path must never wait long for an absent daemon
REQUEST_TIMEOUT = 60    # seconds: once connected, a real encode call may legitimately take a while
MAX_BODY = 1 << 20


def _load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("BAAI/bge-small-en-v1.5")


def _encode(model, rows):
    """rows: [(id, text, is_query)] -> [{"id":.., "v":[..]}]. Same prefix and
    same round-to-5 rounding as bm_embed_bge.py's own main(), so a warm
    vector and a subprocess vector for identical input compare equal."""
    texts = [(QUERY_PREFIX + t) if q else t for _, t, q in rows]
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=64,
                        show_progress_bar=False)
    return [{"id": rid, "v": [round(float(x), 5) for x in v]}
           for (rid, _, _), v in zip(rows, vecs)]


def make_handler(model, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "bm_embed_warm"

        def _send(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
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
            if self.path != "/health":
                return self._send(404, {"error": "unknown path %s" % self.path})
            self._send(200, {"model": "BAAI/bge-small-en-v1.5", "warm": True,
                             "auth": ("bearer token required on every request"
                                      if token else "no token set: localhost only")})

        def do_POST(self):
            if not self._authorized():
                return self._send(401, {"error": "bearer token required"})
            if self.path != "/embed":
                return self._send(404, {"error": "unknown path %s" % self.path})
            try:
                length = min(int(self.headers.get("Content-Length", 0)), MAX_BODY)
                req = json.loads(self.rfile.read(length).decode("utf-8"))
                rows = [(int(r["id"]), str(r["text"])[:1500], bool(r.get("query")))
                       for r in req["texts"]]
            except (ValueError, KeyError, TypeError, OSError) as e:
                return self._send(400, {"error": "bad request body: %s" % e})
            if not rows:
                return self._send(400, {"error": "\"texts\" must be a non-empty list"})
            try:
                vecs = _encode(model, rows)
            except Exception as e:
                return self._send(500, {"error": "encode failed: %s" % e})
            self._send(200, {"vecs": vecs})

        def log_message(self, fmt, *args):
            pass  # the daemon stays quiet; bm_vault_serve.py's handler is silent the same way

    return Handler


def embed_via_warm(pairs, query=False, host=None, port=None,
                   connect_timeout=CONNECT_TIMEOUT, timeout=REQUEST_TIMEOUT):
    """pairs: [(id, text)]. {id: [float]} on a live warm daemon, else None --
    on ANY failure (no daemon, refused, timeout, malformed reply), never an
    exception the caller has to catch. This is the fallback contract: the
    caller tries this first and, on None, runs the original subprocess path
    unchanged.

    connect_timeout is the tight ~200ms budget for detecting an ABSENT
    daemon fast; timeout is the separate, generous budget for the actual
    encode once connected. HTTPConnection's own `timeout` argument sets the
    socket's timeout for its ENTIRE lifetime (connect, send and recv alike),
    so passing the tight value there would also cap a real encode call and
    misreport a slow-but-alive daemon as absent -- measured directly: a
    request that takes a little over 200ms to encode failed intermittently
    under this bug before the two timeouts were split."""
    host = host or os.environ.get("BM_EMBED_WARM_HOST", DEFAULT_HOST)
    try:
        port = int(port or os.environ.get("BM_EMBED_WARM_PORT", DEFAULT_PORT))
    except (TypeError, ValueError):
        return None
    body = json.dumps({"texts": [{"id": i, "text": t, "query": query}
                                 for i, t in pairs]}).encode("utf-8")
    conn = None
    try:
        conn = HTTPConnection(host, port, timeout=connect_timeout)
        conn.connect()                  # the tight budget applies here only
        conn.sock.settimeout(timeout)   # generous budget for the real work
        conn.request("POST", "/embed", body=body,
                    headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != 200:
            raise ValueError("warm daemon returned %d" % resp.status)
        data = json.loads(raw.decode("utf-8"))
        return {row["id"]: row["v"] for row in data["vecs"]}
    except (OSError, ValueError, KeyError, TypeError) as e:
        if os.environ.get("BM_EMBED_WARM_DEBUG"):
            sys.stderr.write(
                "bm_embed_warm: NO-DATA (falling back to subprocess): %s\n" % e)
        return None
    finally:
        if conn is not None:
            conn.close()


def _daemon_alive(host, port):
    try:
        conn = HTTPConnection(host, port, timeout=CONNECT_TIMEOUT)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status == 200
    except OSError:
        return False


def cmd_serve(args):
    token = None
    if args.token_file:
        try:
            token = sv.read_token(args.token_file)
        except (OSError, ValueError) as e:
            sys.stderr.write("bm_embed_warm: REFUSING to start: %s\n" % e)
            return 2
    if args.bind not in sv.LOOPBACK and token is None:
        sys.stderr.write(
            "bm_embed_warm: REFUSING to start: --bind %s is not loopback and "
            "no --token-file is set; an embedder held warm on a real "
            "interface would let any stranger route their own text through "
            "this machine's model\n" % args.bind)
        return 2
    try:
        model = _load_model()
    except Exception as e:
        sys.stderr.write("bm_embed_warm: model unavailable: %r\n" % (e,))
        return 3
    srv = ThreadingHTTPServer((args.bind, args.port), make_handler(model, token))
    sys.stderr.write("bm_embed_warm: serving on %s:%d (%s)\n"
                     % (args.bind, srv.server_address[1],
                        "bearer auth" if token else "localhost open"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:  # sbe: allow-silent normal Ctrl-C shutdown of a server meant to run until interrupted
        pass
    finally:
        srv.server_close()
    return 0


def cmd_measure(args):
    """Times a repeat-cold dense query via the warm path and via the
    original subprocess path, and prints both with the delta. Never
    invents a number: absent embed machine, an unreachable model, or a
    daemon that never comes up all print a named NO-DATA line instead."""
    host, port = args.bind, args.port
    text = args.query or "how does the warm embedder change the dense query latency"
    pairs = [(0, text)]

    try:
        import bm_vault
    except Exception as e:
        print("NO-DATA: bm_vault.py unavailable (%s)" % e)
        return 1
    if bm_vault._embed_bin() is None:
        print("NO-DATA: no embed machine present (tools/bm-embed-bge or "
              "tools/bm-embed missing); nothing to measure")
        return 1

    own_daemon = None
    if not _daemon_alive(host, port):
        own_daemon = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "serve",
             "--bind", host, "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        startup_timeout = float(os.environ.get("BM_EMBED_WARM_MEASURE_TIMEOUT", "90"))
        deadline = time.time() + startup_timeout
        while time.time() < deadline and not _daemon_alive(host, port):
            if own_daemon.poll() is not None:
                print("NO-DATA: warm daemon exited during startup "
                      "(model unavailable on this machine)")
                return 1
            time.sleep(0.5)
        if not _daemon_alive(host, port):
            own_daemon.terminate()
            print("NO-DATA: warm daemon did not come up within %gs"
                  % startup_timeout)
            return 1

    try:
        t0 = time.perf_counter()
        warm = embed_via_warm(pairs, query=True, host=host, port=port,
                              timeout=REQUEST_TIMEOUT)
        warm_dt = time.perf_counter() - t0
        if warm is None:
            print("NO-DATA: warm daemon reachable but /embed failed")
            return 1

        t1 = time.perf_counter()
        fallback = bm_vault._embed_texts_subprocess(pairs, query=True)
        fallback_dt = time.perf_counter() - t1
        if fallback is None:
            print("NO-DATA: subprocess embed path unavailable (model missing)")
            return 1
    finally:
        if own_daemon is not None:
            own_daemon.terminate()
            try:
                own_daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                own_daemon.kill()

    print("warm path:     %.3fs" % warm_dt)
    print("fallback path: %.3fs" % fallback_dt)
    speedup = (fallback_dt / warm_dt) if warm_dt > 0 else float("inf")
    print("delta:         %.3fs (%.1fx)" % (fallback_dt - warm_dt, speedup))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="hold the embedder warm, serve /embed over loopback")
    sp.add_argument("--bind", default=DEFAULT_HOST)
    sp.add_argument("--port", type=int, default=DEFAULT_PORT)
    sp.add_argument("--token-file", default=None,
                    help="file holding the shared bearer secret; required for "
                         "any non-loopback --bind")

    mp = sub.add_parser("measure", help="time a dense query via warm and via fallback")
    mp.add_argument("--bind", default=DEFAULT_HOST)
    mp.add_argument("--port", type=int, default=DEFAULT_PORT)
    mp.add_argument("--query", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "serve":
        return cmd_serve(args)
    return cmd_measure(args)


if __name__ == "__main__":
    sys.exit(main())
