#!/usr/bin/env python3
'''bm_vault_web_ui.py: L3b-01 CLI and startup guards.

This sub unit implements only the command line surface and startup guards
for the vault web UI. The router, upstream clients, validators and renderers
are later sub units and are not implemented here.

No em or en dashes anywhere in this file.
'''
import argparse
import ssl
import sys
import urllib.parse
from typing import List, Optional

DEFAULT_PORT = 8378
LOOPBACK = ('127.0.0.1', 'localhost', '::1')


class _StartupRefused(Exception):
    '''Internal refusal carrying the process exit code.'''

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


def _parse_args(argv: List[str]) -> argparse.Namespace:
    '''Parse the web UI's own command line. Hostile input is refused with
    ValueError before argparse sees it, so a non-list or a list holding a
    non-string never reaches argparse's internals as a TypeError.'''
    if not isinstance(argv, list):
        raise ValueError('argv must be a list of strings')
    for item in argv:
        if not isinstance(item, str):
            raise ValueError('argv entries must be strings')
    ap = argparse.ArgumentParser(
        prog='bm_vault_web_ui.py',
        description='vault web UI: read-only client of bm_vault_serve.py')
    ap.add_argument('--upstream', default=None,
                    help='base URL of bm_vault_serve.py, for example '
                         'http://127.0.0.1:8377')
    ap.add_argument('--token-file', default=None,
                    help='file holding the bearer token for --upstream')
    ap.add_argument('--pane-upstream', default=None,
                    help='base URL of bm_vault_pane.py')
    ap.add_argument('--pane-token-file', default=None,
                    help='file holding the bearer token for --pane-upstream')
    ap.add_argument('--bind', default='127.0.0.1',
                    help='web UI bind address (default: 127.0.0.1)')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT,
                    help='web UI port (default: %d)' % DEFAULT_PORT)
    ap.add_argument('--tls-cert', default=None,
                    help='server certificate PEM file for non-loopback --bind')
    ap.add_argument('--tls-key', default=None,
                    help='private key PEM file for non-loopback --bind')
    ap.add_argument('--timeout-seconds', type=int, default=20,
                    help='per upstream call timeout in seconds (default: 20)')
    ap.add_argument('--default-identity', default=None,
                    help='optional identity sent with every recall request')
    return ap.parse_args(argv)


def _load_token(path: str) -> str:
    '''Read a token file as bytes, decode UTF-8, strip, and refuse an empty
    result. A missing, unreadable, non-UTF-8 or whitespace-only file raises
    ValueError or OSError; the caller turns either into exit 2.'''
    if not isinstance(path, str):
        raise ValueError('token path must be a string')
    with open(path, 'rb') as f:
        raw = f.read()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as e:
        raise ValueError('token file %s is not valid UTF-8: %s' % (path, e))
    tok = text.strip()
    if not tok:
        raise ValueError('token file %s is empty after strip' % path)
    return tok


def _host_of_url(url: str) -> str:
    '''Return the hostname from a URL or host:port string. Raises ValueError
    for a non-string, empty or unparsable value.'''
    if not isinstance(url, str):
        raise ValueError('upstream URL must be a string')
    if not url.strip():
        raise ValueError('upstream URL must not be empty')
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    if host is None:
        parsed = urllib.parse.urlparse('//' + url)
        host = parsed.hostname
    if host is None:
        raise ValueError('cannot parse host from upstream URL %r' % url)
    return host


def _startup_guards(args: argparse.Namespace) -> None:
    '''Refuse the process before any socket is bound. Raises _StartupRefused
    with code 2 on every guard failure; main turns that into a returned 2.'''
    if not isinstance(args, argparse.Namespace):
        raise ValueError('args must be an argparse.Namespace')
    bind = getattr(args, 'bind', None)
    if not isinstance(bind, str):
        raise ValueError('--bind must be a string')
    if bind not in LOOPBACK:
        if not (getattr(args, 'tls_cert', None)
                and getattr(args, 'tls_key', None)):
            raise _StartupRefused(
                2, '--bind %s is not loopback and both --tls-cert and '
                   '--tls-key are required' % bind)
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(certfile=args.tls_cert, keyfile=args.tls_key)
        except (ssl.SSLError, OSError) as e:
            raise _StartupRefused(
                2, 'TLS cert/key failed to load: %s' % type(e).__name__)

    if getattr(args, 'token_file', None) is not None:
        try:
            _load_token(args.token_file)
        except (OSError, ValueError) as e:
            raise _StartupRefused(
                2, 'token file %s is unreadable or empty: %s'
                   % (args.token_file, e))

    pane_token = None
    if getattr(args, 'pane_token_file', None) is not None:
        try:
            pane_token = _load_token(args.pane_token_file)
        except (OSError, ValueError) as e:
            raise _StartupRefused(
                2, 'pane token file %s is unreadable or empty: %s'
                   % (args.pane_token_file, e))

    pane_upstream = getattr(args, 'pane_upstream', None)
    if pane_upstream:
        try:
            host = _host_of_url(pane_upstream)
        except ValueError as e:
            raise _StartupRefused(
                2, 'pane upstream %r is invalid: %s' % (pane_upstream, e))
        if host not in LOOPBACK and pane_token is None:
            raise _StartupRefused(
                2, 'pane upstream %s is not loopback and no pane token is set'
                   % pane_upstream)


def main(argv: Optional[List[str]] = None) -> int:
    '''Parse, guard, and return an exit code. L3b-01 stops after the guards:
    the router and server are later sub units.'''
    if argv is None:
        argv = sys.argv[1:]
    if not isinstance(argv, list):
        raise ValueError('main argv must be a list of strings')
    try:
        args = _parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    try:
        _startup_guards(args)
    except _StartupRefused as e:
        print('bm_vault_web_ui: REFUSING to start: %s' % e, file=sys.stderr)
        return e.code
    return 0


if __name__ == '__main__':
    sys.exit(main())
