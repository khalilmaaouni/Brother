#!/usr/bin/env python3
"""The MCP connector catalog for the Brother estate, as code.

WHY THIS EXISTS (founder order 2026-08-30, his words: "you need all the
connectors"). The estate reaches six external platforms: Snowflake with
Cortex, Databricks with Genie, Azure, Microsoft Teams, Bitbucket Cloud and
GitHub. Each has an MCP story, and every one of those stories was web-verified
on 2026-08-30 by a researcher who read the vendor pages. This module freezes
those researched facts as data, so a session six weeks from now wires the
connector the research found rather than the one its training memory invents.
Training memory is exactly how the deprecated Snowflake-Labs server or the
archived Azure repo would get wired: both look right and both are wrong, so
they live in this catalog as named traps rather than as absences.

WHAT EACH SUBCOMMAND IS:

  list       the catalog: every connector with its verified facts.
  check      LIVE reachability probes using only credentials this machine
             already holds (gh auth, the pinned Bitbucket SSH key, az login,
             a local Azurite storage emulator if one happens to be running).
             A probe that cannot run reports NO-DATA with the reason, never
             a silent skip, and NO-DATA is never a pass. Connectors whose
             setup is a founder-only action (OAuth consents, app
             registrations) are NO-DATA always, each naming the exact steps
             that are HIS.
  print-add  prints the wiring the founder runs BY HAND. It never executes
             anything: MCP config applies on restart and changing it is a
             founder action by estate law, and this module proves that by
             never importing a subprocess call into that code path.
  conformance runs the drift-and-canary checks against a LOCAL MOCK
             (tools/bm_mock_mcp.py, --via mock, the default) so the
             connector layer's wire shape can be exercised tonight with
             zero keys and zero enterprise exposure: tools/list must match
             this catalog's own tool names, a tools/call must round-trip,
             and two injected misbehaviors (an ACL leak, a stale delete)
             must be CAUGHT. --via live is NO-DATA per connector until a
             real endpoint is wired, and names the open-resource path
             (never enterprise access) the runbook recommends next.
  canary     the four CONTINUOUS canaries VB3-14 adds on top of the
             one-shot conformance check above: acl-propagation (an ACL
             change becomes visible within a declared bound),
             deletion-convergence (a delete converges to absent within a
             bound), identity-mapping (a source principal resolves to the
             right vault principal), watermark-coupling (content and ACL
             freshness watermarks advance together). Each is calibrated by
             forcing its own failure state in tools/bm_mock_mcp.py (see
             CANARY_FORCED_MISBEHAVE below) so a canary that always passes
             is caught by the suite, not trusted on faith. --profile names
             one connector id (the flag name matches the row's own
             wording; it is the catalog connector id, not
             bm_mock_mcp.py's internal --profile string); --all runs every
             catalog connector instead. --via live is NO-DATA per
             connector, same as conformance. A connector with no mock
             profile wired (azure today) is NO-DATA naming the setup step:
             add it to CATALOG_ID_TO_PROFILE and bm_mock_mcp.py's
             PROFILE_TO_CATALOG_ID before its canaries can run.

OPEN-RESOURCE TESTING, added 2026-08-30 alongside the mock layer: every
connector's `open_test_path` field names the cheapest real path a founder
without enterprise access can actually reach (a free trial, a free-tier
workspace, a local emulator, or "mock-only" when nothing free-tier exists
yet). Azure additionally gets a REAL local probe: the Azurite storage
emulator, reached over loopback with the well-known PUBLIC devstoreaccount1
account (Microsoft Learn, checked 2026-08-30; it is a published constant
for local testing, never a secret, and never proves anything about a real
Azure subscription). The probe never installs or starts Azurite itself; a
silent port is NO-DATA naming the one command that would start it.

THE BITBUCKET PROBE DISTINGUISHES TWO THINGS on purpose: the SSH transport
being alive (the pinned key answering) is not the Atlassian MCP server being
usable, which additionally needs the workspace linked to an Atlassian
organization and an org admin enabling API-token auth in Admin Hub. The probe
reports the transport verdict AND a separate NO-DATA for the MCP half, so a
green SSH line can never be read as "Bitbucket MCP works".

SINGLE-SOURCED research facts carry their flag in the record and in every
output that repeats them, so nothing here reads as more certain than the
research was. Credential names are named; credential VALUES are never read,
printed or invented here.

Exit 0 every probe that ran came back clean or NO-DATA. Exit 1 a probe ran
and FAILED. Exit 2 the command itself could not run (unknown connector).
Python 3.9 floor, standard library only.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

LIVE = "LIVE"
PASS = "PASS"
FAIL = "FAIL"
NO_DATA = "NO-DATA"

# The four canary classes VB3-14 adds, and the mock's own forced-failure
# name for each (tools/bm_mock_mcp.py's --misbehave choices). A canary
# class run with its OWN forced state must FAIL by name; run with any
# other class's forced state (or none) it must PASS, which is exactly what
# test_bm_connectors.py drives in both directions.
CANARY_CLASSES = ("acl-propagation", "deletion-convergence",
                  "identity-mapping", "watermark-coupling")
CANARY_FORCED_MISBEHAVE = {
    "acl-propagation": "no-acl-propagation",
    "deletion-convergence": "stale-delete",
    "identity-mapping": "identity-mismatch",
    "watermark-coupling": "watermark-drift",
}
# Must match bm_mock_mcp.py's IDENTITY_MAP exactly; kept as a literal here
# rather than imported to avoid a circular import (bm_mock_mcp.py already
# imports BY_ID from this module). A drift test in test_bm_connectors.py
# proves the two pairings never separate silently.
IDENTITY_SOURCE_PRINCIPAL = "src-user-1"
IDENTITY_EXPECTED_VAULT_PRINCIPAL = "vault-user-1"

REQUIRED_FIELDS = (
    "id", "vendor", "official", "status", "transport", "endpoint", "auth",
    "credentials", "prerequisites", "cost", "doc", "single_sourced", "traps",
    "tools", "open_test_path",
)

HERE = os.path.dirname(os.path.abspath(__file__))
MOCK_SCRIPT = os.path.join(HERE, "bm_mock_mcp.py")

# The mock's own --profile names (tools/bm_mock_mcp.py) read naturally for
# the surface being faked, which for Databricks is specifically Genie; this
# is the one place that mapping back to the catalog id lives.
CATALOG_ID_TO_PROFILE = {
    "snowflake": "snowflake",
    "databricks": "databricks-genie",
    "github": "github",
    "bitbucket": "bitbucket",
    "teams": "teams",
}

# Published, non-secret Azurite constant (Microsoft Learn, checked
# 2026-08-30): "Azurite accepts the same well-known account and key used by
# the legacy Azure Storage Emulator." It exists purely for local testing and
# proves nothing about a real Azure subscription.
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_ACCOUNT_KEY = ("Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2"
                        "UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==")

CATALOG = (
    {
        "id": "snowflake",
        "vendor": "Snowflake (includes Cortex)",
        "official": True,
        "status": "GA",
        "transport": "remote HTTP",
        "endpoint": ("https://<account_url>/api/v2/databases/{db}/schemas/"
                     "{schema}/mcp-servers/{name}"),
        "auth": ("OAuth 2.0 ONLY (Snowflake OAuth or External OAuth); PAT and "
                 "key-pair are NOT supported on this endpoint"),
        "credentials": ["OAuth client registration, founder-only"],
        "prerequisites": ["an MCP server object created in the target schema",
                          "OAuth integration configured by an admin"],
        "cost": "runs inside the Snowflake account; queries bill as warehouse usage",
        "doc": "https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp",
        "single_sourced": [],
        "traps": [("github.com/Snowflake-Labs/mcp (PyPI snowflake-labs-mcp) is "
                   "DEPRECATED by its own README; never wire it")],
        "tools": ["CORTEX_AGENT_RUN", "CORTEX_ANALYST_MESSAGE",
                  "CORTEX_SEARCH_SERVICE_QUERY", "SYSTEM_EXECUTE_SQL", "GENERIC"],
        "open_test_path": ("a Snowflake trial account (signup.snowflake.com), "
                           "30 days, $400 credit, Enterprise features, no card "
                           "at signup, a card only to convert; whether Cortex "
                           "and the managed MCP server work on a trial account "
                           "is single-sourced and unverified"),
    },
    {
        "id": "databricks",
        "vendor": "Databricks (includes Genie)",
        "official": True,
        "status": "beta",
        "transport": "remote HTTP",
        "endpoint": ("https://<workspace-hostname>/api/2.0/mcp/genie (and "
                     "/api/2.0/mcp/<service> for ai-search, sql, "
                     "unity-catalog functions)"),
        "auth": "OAuth on-behalf-of-user with per-service scopes",
        "credentials": ["OAuth on-behalf-of-user consent, founder-only"],
        "prerequisites": ["a workspace admin enables the preview from the Previews page"],
        "cost": "Genie and SQL calls bill as Databricks compute",
        "doc": "https://docs.databricks.com/aws/en/agents/mcp-tools/genie-mcp",
        "single_sourced": [],
        "traps": [],
        "tools": ["genie_ask", "genie_poll_response", "genie_get_query_result",
                  "genie_cancel_response", "view_ask"],
        "open_test_path": ("Databricks Free Edition, persistent (not a trial "
                           "clock), serverless SQL plus Genie included; whether "
                           "it needs a card is NO-DATA (not stated on official "
                           "pages), and Genie MCP reachability on Free Edition "
                           "is unverified"),
    },
    {
        "id": "azure",
        "vendor": "Microsoft Azure",
        "official": True,
        "status": "GA",
        "transport": "local stdio (npx)",
        "endpoint": "npx -y @azure/mcp@latest server start",
        "auth": ("Azure CLI login by default, or AZURE_TENANT_ID/"
                 "AZURE_CLIENT_ID/AZURE_CLIENT_SECRET, or managed identity"),
        "credentials": ["az login session", "AZURE_TENANT_ID", "AZURE_CLIENT_ID",
                        "AZURE_CLIENT_SECRET"],
        "prerequisites": ["Node.js for npx", "an Azure subscription"],
        "cost": "the server is free; the Azure resources it touches bill normally",
        "doc": "https://github.com/microsoft/mcp/tree/main/servers/Azure.Mcp.Server",
        "single_sourced": ["a 3.0.0-beta track exists on npm (single-sourced)"],
        "traps": [("the old Azure/azure-mcp-server repo was ARCHIVED 2025-08-25; "
                   "the live home is microsoft/mcp path servers/Azure.Mcp.Server")],
        "tools": ["40+ service namespaces"],
        "open_test_path": ("today, zero cost, zero account: the Azurite storage "
                           "emulator (probed live below) and the Cosmos DB "
                           "vNext emulator, GA 2026-06-02; the official Azure "
                           "MCP server itself still targets live subscriptions, "
                           "so these probe the storage wire protocol directly "
                           "rather than the MCP server. A free Azure account "
                           "($200, 30 days, card required for identity only) "
                           "is the next rung up"),
    },
    {
        "id": "teams",
        "vendor": "Microsoft Teams",
        "official": False,
        "status": "community",
        "transport": "local stdio",
        "endpoint": "SurgeEnterpriseAI/teams-mcp-server (early-stage, 2 stars)",
        "auth": ("Entra app registration with TEAMS_CLIENT_ID/"
                 "TEAMS_CLIENT_SECRET/TEAMS_AUTHORITY"),
        "credentials": ["TEAMS_CLIENT_ID", "TEAMS_CLIENT_SECRET", "TEAMS_AUTHORITY"],
        "prerequisites": ["an Entra app registration, founder-only"],
        "cost": "free server; Graph API calls are free at this scale",
        "doc": "https://github.com/SurgeEnterpriseAI/teams-mcp-server",
        "single_sourced": [("the first-party data path (Work IQ Teams, Agent 365) "
                            "is preview and reportedly needs a Microsoft 365 "
                            "Copilot license (single-sourced, non-Microsoft page; "
                            "unverified)")],
        "traps": [("NO first-party client-side server exists: Microsoft's Teams "
                   "MCP doc is the opposite direction, your bot as a server")],
        "tools": ["11 tools incl teams_list_teams, teams_read_channel_messages, "
                  "teams_send_channel_message"],
        "open_test_path": ("mock-only this week: the M365 developer tenant "
                           "route is still gated to Visual Studio subscribers "
                           "or partners, so there is no free-tier real path yet"),
    },
    {
        "id": "bitbucket",
        "vendor": "Atlassian (Bitbucket Cloud)",
        "official": True,
        "status": "GA",
        "transport": "remote HTTP",
        "endpoint": "https://mcp.atlassian.com/v1/mcp/authv2",
        "auth": ("API token auth. Atlassian verbatim: \"Bitbucket Cloud tools "
                 "rely on API token auth for now. OAuth support is not yet "
                 "available.\""),
        "credentials": ["Atlassian API token, founder-placed (keychain or env)"],
        "prerequisites": [("the workspace must be linked to an Atlassian "
                           "organization and an org admin enables API-token auth "
                           "under Admin Hub > Rovo > Rovo MCP Server")],
        "cost": ("the kmaaouni workspace is the disposable test estate on the "
                 "FREE plan; never propose a paid upgrade"),
        "doc": "https://github.com/atlassian/atlassian-mcp-server",
        "single_sourced": [],
        "traps": [],
        "tools": ["repos/branches/files browse", "PR lifecycle", "pipelines",
                  "commits"],
        "open_test_path": ("already live for transport: the pinned SSH key "
                           "against the disposable kmaaouni workspace; the MCP "
                           "half needs that workspace linked to an Atlassian "
                           "organization with API-token auth enabled under "
                           "Admin Hub > Rovo, a founder-only step, no cost"),
    },
    {
        "id": "github",
        "vendor": "GitHub",
        "official": True,
        "status": "GA",
        "transport": "remote HTTP (OAuth) or local stdio via docker",
        "endpoint": "https://api.githubcopilot.com/mcp/",
        "auth": "OAuth for the remote server; GITHUB_PERSONAL_ACCESS_TOKEN for docker",
        "credentials": ["GITHUB_PERSONAL_ACCESS_TOKEN (docker route)"],
        "prerequisites": ["docker for the local route"],
        "cost": ("free; estate laws bind: GitHub Actions stay OFF, nothing "
                 "metered gets enabled, any workflow dispatch is founder-gated"),
        "doc": "https://github.com/github/github-mcp-server",
        "single_sourced": [],
        "traps": [],
        "tools": ["repos", "issues", "pull_requests", "actions", "code_security",
                  "and more"],
        "open_test_path": ("already live: this machine's own `gh auth` OAuth "
                           "session against a personal GitHub account and "
                           "repo, no new signup needed"),
    },
)

BY_ID = {c["id"]: c for c in CATALOG}


def _run(argv, runner):
    """One probe subprocess. Returns (exit_code, stdout+stderr) or (None, why)
    when the binary is absent, because a missing binary is NO-DATA with a
    reason, never a crash and never a silent pass."""
    if shutil.which(argv[0]) is None:
        return None, "%s is not installed" % argv[0]
    try:
        p = runner(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   timeout=30)
    except subprocess.TimeoutExpired:  # sbe: allow-silent per docstring: timeout is NO-DATA with a reason, never a crash
        return None, "%s timed out after 30s" % argv[0]
    out = p.stdout.decode("utf-8", "replace").strip() if p.stdout else ""
    return p.returncode, out


def probe_azurite(host="127.0.0.1", port=10000, timeout=2, opener=None):
    """A REAL, credential-free probe: no founder action, no enterprise
    access, no subscription. It only proves an Azurite process is on the
    port and identifies itself as Azurite (the "Server: Azurite-Blob/x.y.z"
    header every Azurite response carries, per Azurite's own README); it
    does not sign a request with AZURITE_ACCOUNT_KEY, so LIVE here means
    "an Azurite emulator answered", not "an authenticated call succeeded".
    ponytail: signing every call would add real cryptographic risk (a wrong
    canonicalization silently reads as a bad key) for a probe whose whole
    job is reachability; upgrade to a signed call if a conformance check
    ever needs to read actual container data back.
    """
    opener = opener or urllib.request.urlopen
    url = "http://%s:%d/%s?comp=list&restype=account" % (host, port, AZURITE_ACCOUNT)
    try:
        with opener(url, timeout=timeout) as resp:
            headers = resp.headers
    except urllib.error.HTTPError as exc:
        headers = exc.headers
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return (NO_DATA, "azurite: nothing answered %s:%d (%s); start it "
                         "with `npx azurite` (this probe starts nothing "
                         "itself); the well-known dev account %s is ready "
                         "the moment it is" % (host, port, exc, AZURITE_ACCOUNT))
    server = headers.get("Server", "") if headers else ""
    if "azurite" in server.lower():
        return (LIVE, "azurite: %s:%d answered as %s" % (host, port, server))
    return (FAIL, "azurite: %s:%d answered but not as Azurite (Server: %r)"
                  % (host, port, server))


def probe(cid, runner=subprocess.run):
    """The live probe for one connector. Returns a list of
    (verdict, line) tuples; verdict is LIVE, FAIL or NO-DATA.

    The runner is injected so the calibration suite can prove the verdict
    mapping both ways without a network or a credential.
    """
    if cid == "github":
        code, out = _run(["gh", "auth", "status"], runner)
        if code is None:
            return [(NO_DATA, "github: %s; install gh and run gh auth login" % out)]
        if code != 0:
            return [(FAIL, "github: gh auth status exit %d" % code)]
        code2, _ = _run(["gh", "api", "rate_limit"], runner)
        if code2 == 0:
            return [(LIVE, "github: gh auth status exit 0 and gh api rate_limit exit 0")]
        return [(FAIL, "github: gh api rate_limit exit %s" % code2)]
    if cid == "bitbucket":
        code, out = _run(["ssh", "-T", "-o", "BatchMode=yes",
                          "-o", "ConnectTimeout=10", "git@bitbucket.org"], runner)
        if code is None:
            results = [(NO_DATA, "bitbucket transport: %s" % out)]
        elif code == 0:
            results = [(LIVE, "bitbucket transport: ssh -T git@bitbucket.org exit 0 "
                              "(the pinned key answered)")]
        else:
            results = [(FAIL, "bitbucket transport: ssh -T exit %d" % code)]
        results.append((NO_DATA,
                        "bitbucket mcp: transport-alive is not MCP-enabled; the "
                        "workspace must be linked to an Atlassian organization "
                        "and an org admin must enable API-token auth under "
                        "Admin Hub > Rovo > Rovo MCP Server, both founder-only"))
        return results
    if cid == "azure":
        code, out = _run(["az", "account", "show"], runner)
        if code is None:
            az_verdict = (NO_DATA, "azure: %s; install with brew install "
                                   "azure-cli, then the founder runs az login" % out)
        elif code == 0:
            az_verdict = (LIVE, "azure: az account show exit 0")
        else:
            az_verdict = (FAIL, "azure: az account show exit %d (likely not "
                                "logged in; az login is a founder action)" % code)
        return [az_verdict, probe_azurite()]
    if cid == "snowflake":
        return [(NO_DATA,
                 "snowflake: OAuth 2.0 is the only supported auth on the managed "
                 "MCP endpoint and the OAuth integration plus consent are "
                 "founder-only steps (create the MCP server object in the schema, "
                 "configure Snowflake or External OAuth, complete the consent)")]
    if cid == "databricks":
        return [(NO_DATA,
                 "databricks: the per-workspace MCP servers are a beta a workspace "
                 "admin enables from the Previews page, and the OAuth "
                 "on-behalf-of-user consent is founder-only")]
    if cid == "teams":
        return [(NO_DATA,
                 "teams: no first-party client-side server exists; the community "
                 "route needs an Entra app registration (TEAMS_CLIENT_ID, "
                 "TEAMS_CLIENT_SECRET, TEAMS_AUTHORITY), a founder-only action")]
    return [(NO_DATA, "%s: unknown connector" % cid)]


ADD_LINES = {
    "github": [
        "claude mcp add github -e GITHUB_PERSONAL_ACCESS_TOKEN=$GITHUB_PAT -- "
        "docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN "
        "ghcr.io/github/github-mcp-server",
        "# or the remote OAuth route:",
        "claude mcp add --transport http github https://api.githubcopilot.com/mcp/",
    ],
    "bitbucket": [
        "claude mcp add --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2",
    ],
    "azure": [
        "claude mcp add azure -- npx -y @azure/mcp@latest server start",
    ],
    "snowflake": [
        "claude mcp add --transport http snowflake "
        "\"https://<account_url>/api/v2/databases/<db>/schemas/<schema>/mcp-servers/<name>\"",
    ],
    "databricks": [
        "claude mcp add --transport http databricks-genie "
        "\"https://<workspace-hostname>/api/2.0/mcp/genie\"",
    ],
    "teams": [
        "# community server, early-stage (2 stars); clone and register as stdio:",
        "claude mcp add teams -e TEAMS_CLIENT_ID=... -e TEAMS_CLIENT_SECRET=... "
        "-e TEAMS_AUTHORITY=... -- node <clone of SurgeEnterpriseAI/teams-mcp-server>",
    ],
}


def print_add(cid, out=sys.stdout):
    """Print the wiring the founder runs by hand. NEVER executes anything:
    MCP config applies on restart and is a founder action by estate law."""
    c = BY_ID.get(cid)
    if c is None:
        print("NO-DATA: unknown connector %r" % cid, file=sys.stderr)
        return 2
    print("# %s (%s, %s)" % (c["vendor"], c["status"], c["transport"]), file=out)
    print("# auth: %s" % c["auth"], file=out)
    for p in c["prerequisites"]:
        print("# prerequisite: %s" % p, file=out)
    for line in ADD_LINES[cid]:
        print(line, file=out)
    print("# Credential placement: keychain (security add-generic-password "
          "-w, no echo) or env at launch, per estate law. Never a file in a "
          "repo, never typed by a session; the founder places values himself.", file=out)
    for s in c["single_sourced"]:
        print("# single-sourced: %s" % s, file=out)
    for t in c["traps"]:
        print("# TRAP: %s" % t, file=out)
    print("# Run the add line yourself and restart the session: MCP config "
          "applies on restart.", file=out)
    return 0


def cmd_list():
    """Catalog facts plus the live verdict word. The verdict comes from the
    same probe() the check command uses, so list and check can never disagree."""
    for c in CATALOG:
        live = probe(c["id"])[0][0]
        print("%-11s %-34s official=%-5s status=%-9s live=%s" %
              (c["id"], c["vendor"], c["official"], c["status"], live))
        print("  transport: %s" % c["transport"])
        print("  endpoint:  %s" % c["endpoint"])
        print("  auth:      %s" % c["auth"])
        print("  cost:      %s" % c["cost"])
        print("  doc:       %s" % c["doc"])
        for s in c["single_sourced"]:
            print("  single-sourced: %s" % s)
        for t in c["traps"]:
            print("  TRAP: %s" % t)
    return 0


def cmd_check(cid=None):
    ids = [cid] if cid else [c["id"] for c in CATALOG]
    if cid and cid not in BY_ID:
        print("NO-DATA: unknown connector %r" % cid, file=sys.stderr)
        return 2
    failed = False
    for one in ids:
        for verdict, line in probe(one):
            print("%-7s %s" % (verdict, line))
            if verdict == FAIL:
                failed = True
    return 1 if failed else 0


def _jsonrpc(rid, method, params):
    return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}


def _spawn_mock(cid, requests, misbehave, runner, out):
    """Spawn tools/bm_mock_mcp.py for cid, feed it requests (a list of
    JSON-RPC dicts), return {id: response} or None when it already printed
    a NO-DATA line to out (missing profile handled by the caller, since
    conformance and canary word that NO-DATA differently)."""
    profile = CATALOG_ID_TO_PROFILE[cid]
    stdin_text = "\n".join(json.dumps(r) for r in requests) + "\n"
    argv = [sys.executable, MOCK_SCRIPT, "--profile", profile]
    if misbehave:
        argv += ["--misbehave", misbehave]
    try:
        proc = runner(argv, input=stdin_text, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("NO-DATA: %s mock could not start: %s" % (cid, exc), file=out)
        return None
    responses = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            responses[msg["id"]] = msg
        except (ValueError, KeyError):
            print("NO-DATA: %s mock produced unparsable output: %r"
                  % (cid, line), file=out)
            return None
    r1 = responses.get(1)
    if not r1 or "result" not in r1:
        print("NO-DATA: %s mock did not answer initialize (stderr: %s)"
              % (cid, proc.stderr.strip()), file=out)
        return None
    return responses


def cmd_conformance(cid, via="mock", misbehave=None, out=sys.stdout,
                     err=sys.stderr, runner=subprocess.run):
    """Drift-and-canary checks against the local mock (--via mock, default)
    or a statement of what --via live still needs (always NO-DATA today).

    Exit 0 conformant, 1 findings named, 2 NO-DATA (the mock itself could
    not start or answer, or --via live has no real endpoint wired)."""
    if cid not in BY_ID:
        print("NO-DATA: unknown connector %r" % cid, file=err)
        return 2
    if via == "live":
        print("NO-DATA: %s conformance --via live: no real endpoint wired "
              "yet; the open-resource path the runbook names next is %s"
              % (cid, BY_ID[cid]["open_test_path"]), file=out)
        return 2
    profile = CATALOG_ID_TO_PROFILE.get(cid)
    if profile is None:
        print("NO-DATA: %s has no mock profile" % cid, file=err)
        return 2
    catalog_tools = list(BY_ID[cid]["tools"])
    probe_tool = catalog_tools[0]
    requests = [
        _jsonrpc(1, "initialize", {}),
        _jsonrpc(2, "tools/list", {}),
        _jsonrpc(3, "tools/call", {"name": probe_tool, "arguments": {"action": "list"}}),
        _jsonrpc(4, "tools/call", {"name": probe_tool, "arguments": {"action": "delete"}}),
        _jsonrpc(5, "tools/call", {"name": probe_tool, "arguments": {"action": "list"}}),
    ]
    responses = _spawn_mock(cid, requests, misbehave, runner, out)
    if responses is None:
        return 2

    findings = []
    r2 = responses.get(2, {})
    mock_tools = sorted(t["name"] for t in r2.get("result", {}).get("tools", []))
    if mock_tools != sorted(catalog_tools):
        findings.append("tools/list drift: mock=%s catalog=%s"
                         % (mock_tools, sorted(catalog_tools)))

    def _items(rid):
        r = responses.get(rid, {})
        if "result" not in r:
            return None
        return json.loads(r["result"]["content"][0]["text"]).get("items", [])

    items3 = _items(3)
    if items3 is None:
        findings.append("round-trip: tools/call %s returned no result" % probe_tool)
    elif any(item.get("restricted") for item in items3):
        findings.append("acl-leak: %s returned restricted content a declared "
                         "policy should have trimmed" % probe_tool)

    if responses.get(4, {}).get("result") is None:
        findings.append("round-trip: delete call on %s returned no result" % probe_tool)

    items5 = _items(5)
    if items5 and any(not item.get("restricted") for item in items5):
        findings.append("stale-delete: deleted item still appears from %s" % probe_tool)

    for f in findings:
        print("FAIL    %s: %s" % (cid, f), file=out)
    if findings:
        return 1
    print("LIVE    %s: mock conformant (tools/list matches catalog, "
          "round-trip ok, acl trimmed, delete not stale)" % cid, file=out)
    return 0


def _canary_requests(probe_tool, klass):
    """The JSON-RPC requests (ids 3+) that exercise one canary class,
    scripted against the shared probe_tool (same one conformance uses).
    Semantics live in the `action` field, not in the tool name, so this
    works identically whatever vendor tool happens to be first in the
    catalog."""
    if klass == "acl-propagation":
        return [
            _jsonrpc(3, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "acl_set", "restricted": True}}),
            _jsonrpc(4, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "acl_view"}}),
            _jsonrpc(5, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "acl_set", "restricted": False}}),
            _jsonrpc(6, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "acl_view"}}),
        ]
    if klass == "deletion-convergence":
        return [
            _jsonrpc(3, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "delete"}}),
            _jsonrpc(4, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "list"}}),
        ]
    if klass == "identity-mapping":
        return [
            _jsonrpc(3, "tools/call", {"name": probe_tool, "arguments": {
                     "action": "identity_resolve",
                     "source_principal": IDENTITY_SOURCE_PRINCIPAL}}),
        ]
    if klass == "watermark-coupling":
        return [
            _jsonrpc(3, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "watermark_advance"}}),
            _jsonrpc(4, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "watermark_advance"}}),
            _jsonrpc(5, "tools/call", {"name": probe_tool,
                     "arguments": {"action": "watermark_read"}}),
        ]
    raise ValueError("unknown canary class %r" % klass)


def _canary_result(cid, klass, responses):
    """(verdict, line) for one canary class given its responses dict.
    verdict is PASS, FAIL or NO-DATA; FAIL always names the forced state
    that caught it, so a FAIL line is never ambiguous about which canary
    class or which fixture behavior produced it."""
    def _payload(rid):
        r = responses.get(rid, {})
        if "result" not in r:
            return None
        return json.loads(r["result"]["content"][0]["text"])

    if klass == "acl-propagation":
        before, after = _payload(4), _payload(6)
        if before is None or after is None:
            return (NO_DATA, "%s acl-propagation: round-trip returned no result" % cid)
        if after.get("restricted") is not False:
            return (FAIL, "%s acl-propagation: forced state no-acl-propagation "
                          "caught: acl_set(restricted=False) did not propagate "
                          "within 1 call (acl_view still reports restricted=%r)"
                          % (cid, after.get("restricted")))
        return (PASS, "%s acl-propagation: ACL open change visible within "
                      "1 call (bound=1 tools/call after the change)" % cid)

    if klass == "deletion-convergence":
        after = _payload(4)
        if after is None:
            return (NO_DATA, "%s deletion-convergence: round-trip returned no result" % cid)
        if after.get("items"):
            return (FAIL, "%s deletion-convergence: forced state stale-delete "
                          "caught: the deleted item is still visible within "
                          "1 call (resurrected)" % cid)
        return (PASS, "%s deletion-convergence: deletion visible as absent "
                      "within 1 call (bound=1 tools/call after delete)" % cid)

    if klass == "identity-mapping":
        result = _payload(3)
        if result is None:
            return (NO_DATA, "%s identity-mapping: round-trip returned no result" % cid)
        resolved = result.get("vault_principal")
        if resolved != IDENTITY_EXPECTED_VAULT_PRINCIPAL:
            return (FAIL, "%s identity-mapping: forced state identity-mismatch "
                          "caught: %s resolved to %s, expected %s"
                          % (cid, IDENTITY_SOURCE_PRINCIPAL, resolved,
                             IDENTITY_EXPECTED_VAULT_PRINCIPAL))
        return (PASS, "%s identity-mapping: %s resolved to %s (bound=1 call)"
                      % (cid, IDENTITY_SOURCE_PRINCIPAL, resolved))

    if klass == "watermark-coupling":
        result = _payload(5)
        if result is None:
            return (NO_DATA, "%s watermark-coupling: round-trip returned no result" % cid)
        content_wm = result.get("content_watermark")
        acl_wm = result.get("acl_watermark")
        if content_wm != acl_wm:
            return (FAIL, "%s watermark-coupling: forced state watermark-drift "
                          "caught: content watermark at %r, acl watermark at "
                          "%r, drift=%r" % (cid, content_wm, acl_wm,
                                            content_wm - acl_wm))
        return (PASS, "%s watermark-coupling: content and acl watermarks "
                      "both at %r after 2 updates (bound=0 drift)"
                      % (cid, content_wm))

    raise ValueError("unknown canary class %r" % klass)


def cmd_canary(cid, via="mock", misbehave=None, out=sys.stdout, err=sys.stderr,
               runner=subprocess.run):
    """Run all four canary classes for one connector against the mock (or
    state the --via live NO-DATA, same shape as conformance).

    Exit 0 every class PASSed, 1 at least one class FAILed by name, 2
    NO-DATA (unknown connector, no mock profile wired, --via live, or the
    mock itself could not answer)."""
    if cid not in BY_ID:
        print("NO-DATA: unknown connector %r" % cid, file=err)
        return 2
    if via == "live":
        print("NO-DATA: %s canary --via live: no real endpoint wired yet; "
              "the open-resource path the runbook names next is %s"
              % (cid, BY_ID[cid]["open_test_path"]), file=out)
        return 2
    profile = CATALOG_ID_TO_PROFILE.get(cid)
    if profile is None:
        print("NO-DATA: %s canary: no mock profile wired for this connector; "
              "add it to CATALOG_ID_TO_PROFILE (tools/bm_connectors.py) and "
              "bm_mock_mcp.py's PROFILE_TO_CATALOG_ID before its canaries "
              "can run" % cid, file=out)
        return 2
    catalog_tools = list(BY_ID[cid]["tools"])
    probe_tool = catalog_tools[0]
    worst = 0
    for klass in CANARY_CLASSES:
        requests = ([_jsonrpc(1, "initialize", {}), _jsonrpc(2, "tools/list", {})]
                    + _canary_requests(probe_tool, klass))
        responses = _spawn_mock(cid, requests, misbehave, runner, out)
        if responses is None:
            worst = max(worst, 2)
            continue
        verdict, line = _canary_result(cid, klass, responses)
        print("%-7s %s" % (verdict, line), file=out)
        worst = max(worst, {PASS: 0, FAIL: 1, NO_DATA: 2}[verdict])
    return worst


def cmd_canary_all(via="mock", misbehave=None, out=sys.stdout, err=sys.stderr,
                    runner=subprocess.run):
    """canary --all: every catalog connector in turn, worst exit code wins."""
    worst = 0
    for c in CATALOG:
        worst = max(worst, cmd_canary(c["id"], via, misbehave, out, err, runner))
    return worst


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("list", "check", "print-add",
                                        "conformance", "canary"))
    ap.add_argument("--connector", help="connector id")
    # --profile is canary's own name for the same connector id, per the
    # row's own wording ("canary --profile P"); it is NOT bm_mock_mcp.py's
    # internal --profile string (that one is "databricks-genie" etc, this
    # one is the catalog id "databricks").
    ap.add_argument("--profile", help="canary: connector id (same values as --connector)")
    ap.add_argument("--all", action="store_true", help="canary: run every catalog connector")
    ap.add_argument("--via", choices=("mock", "live"), default="mock")
    ap.add_argument("--misbehave", choices=("acl", "stale-delete",
                                            "no-acl-propagation",
                                            "identity-mismatch",
                                            "watermark-drift"))
    args = ap.parse_args(argv)
    if args.command == "conformance":
        if not args.connector:
            print("NO-DATA: conformance needs --connector", file=sys.stderr)
            return 2
        return cmd_conformance(args.connector, args.via, args.misbehave)
    if args.command == "canary":
        if args.all:
            return cmd_canary_all(args.via, args.misbehave)
        cid = args.profile or args.connector
        if not cid:
            print("NO-DATA: canary needs --profile (a connector id) or --all",
                  file=sys.stderr)
            return 2
        return cmd_canary(cid, args.via, args.misbehave)
    if args.command == "list":
        return cmd_list()
    if args.command == "check":
        return cmd_check(args.connector)
    if not args.connector:
        print("NO-DATA: print-add needs --connector", file=sys.stderr)
        return 2
    return print_add(args.connector)


if __name__ == "__main__":
    sys.exit(main())
