#!/usr/bin/env python3
"""bm_vault_notify: one renderer, three outputs, two adapters (WBS row VB11-06).

WHY THIS EXISTS. bm_vault_digest.py (VB11-04) already builds the per-principal digest
page and writes it to the vault. Nothing turns that page into anything a principal
receives without opening the vault themselves. This module is the delivery layer: it
reads an already-written digest page, parses it ONCE into a shared facts dict (never
re-deriving a finding bm_vault_digest already computed), and renders that ONE dict
three ways: the page itself (reconstructed from the same facts, so it is provably the
same source as the other two, never a second copy drifting from the first), an HTML
email carrying a text/plain alternative part, and a Teams Adaptive Card. `send` then
pushes the email or card to a resolved recipient through one of two adapters.

THE SHARED SOURCE. facts_from_digest() parses bm_vault_digest.render_digest()'s own
output shape (frontmatter "type: digest", "## <folder> (<n> item(s))" headings, "###
item <n>" sub-headings, and the "key: value" card lines _render_card() already
writes). fact_strings() flattens that into the exact list of atomic fact strings
("principal: ...", "date: ...", "<folder> (n item(s))" verbatim as the page's own
heading text, and every "key: value"
card line verbatim). Every one of the three render_* functions below is fed the SAME
facts dict, so a finding cannot appear correctly on the page and wrong (or missing)
in the email or the card: there is nowhere in this file a second definition of what a
finding says. render_page() reconstructs bm_vault_digest's own markdown shape from
facts instead of copying the file's bytes on purpose: a page rendered this way is
proof the facts dict round-trips, not merely an assumption that it would.

CONSISTENCY IS CHECKED, NOT ASSUMED. verify_consistency() (the `verify` subcommand)
independently RE-EXTRACTS the fact set from each of the three finished outputs (the
page through the same parser, the email through an HTML-tag-stripping,
entity-unescaping text extraction, the card through a walk of every Adaptive Card
TextBlock/FactSet string) and diffs each against the page's own fact set. This is the
"driven backwards" half of the done-check: a doctored copy of any one output is
caught because its extracted fact set no longer matches the page's.

TWO ADAPTERS.

  email    Reads a mailbox credential from the macOS keychain via
           `security find-generic-password -s brother-mailbox -w`, AT CALL TIME
           only (see _read_mailbox_credential): never stored in a variable that
           outlives the one send, never echoed, never logged, never in argv. The
           credential's own shape is this module's private convention, since
           nothing upstream defines one: "host|port|username|password", read as
           one opaque string and split once. An absent or malformed credential is
           NO-DATA naming the keychain item by name (brother-mailbox), never a
           guess, never a stack trace; every OTHER channel keeps working in the
           same run. The actual SMTP conversation lives behind _send_smtp(), the
           ONE seam a test replaces to prove the send path is exercised without
           ever opening a socket (rule f: no network in tests).

  teams    Fixture-mode until tenant consent is granted (see
           docs/TEAMS-CONSENT-REQUEST.md): `--mock-sink FILE` is required and
           records the exact card payload and recipient identity that would have
           been POSTed to Microsoft Graph. Omitting --mock-sink is NO-DATA naming
           the missing consent, never a silent no-op and never a real call to a
           tenant nobody has approved yet.

CONTACT FIELDS live on the principal registry, not here: tools/bm_vault_principals.py
gained a `contact` subcommand (VB11-06) recording --email/--teams-identity as a
normal recorded mutation, the same dry-run/--by/no-op/NO-DATA shape every other
mutation on that registry already has. `send` resolves --to through that SAME
registry (bm_vault_principals.load/registry_path/_find_key, imported by path, never
re-implemented), so a name normalizes and refuses exactly the way every other
consumer of that registry already does.

AUDIT. Every actual send (never a NO-DATA refusal, which sent nothing) appends one
row to bm_vault_audit.py's own append-only log via bm_vault_audit.append(), with the
resolved principal as the `principal` field and a `query` string naming the channel
and the digest file. No second audit store: the existing one already answers "who
read/received what", and a send is exactly that question for a different verb.

LIVE DOCUMENTATION, fetched 2026-08-30, both facts pinned rather than recalled:

  Adaptive Card schema version Teams renders: learn.microsoft.com/en-us/
  microsoftteams/platform/task-modules-and-cards/cards/cards-reference (fetched
  2026-08-30) states plainly: "Microsoft Teams mobile app supports Adaptive Cards
  up to version 1.6. Cards that use schema versions later than 1.6 might not
  render correctly or might have limited or inconsistent functionality on mobile
  devices." ADAPTIVE_CARD_VERSION below is pinned to "1.6" for exactly this
  reason: the newest version every current Teams client is documented to render.
  $schema is "http://adaptivecards.io/schemas/adaptive-card.json", the value
  Microsoft's own worked example on that same page uses.

  Graph scope to send a channel message: learn.microsoft.com/en-us/graph/api/
  channel-post-messages?view=graph-rest-1.0 (fetched 2026-08-30), POST
  /teams/{team-id}/channels/{channel-id}/messages. Its own Permissions table:
  least-privileged delegated permission "ChannelMessage.Send" (a higher-privileged
  alternative, "Group.ReadWrite.All", is named "supported only for backward
  compatibility" and explicitly NOT recommended for new use); the only
  application permission listed, "Teamwork.Migrate.All", is scoped to message
  MIGRATION, not an ordinary send, and is not requested here. Recorded verbatim
  in docs/TEAMS-CONSENT-REQUEST.md for IT rather than only here.

Exit 0: the command ran (a render, a verify that found nothing wrong, or a send that
delivered). Exit 1: verify found a real mismatch. Exit 2: NO-DATA (unreadable/
unparseable digest, unknown principal, missing contact field, missing keychain
credential, missing --mock-sink). Python 3.9, standard library only. This module is
a NAMED exception to the estate's no-subprocess/no-network rule (SECURITY.md and
tools/test_bm.py's allowed-imports table): `subprocess` for the one local, no-network
`security` keychain read, and `smtplib` for the deliberate network send the email
adapter makes on EXPLICIT invocation only, never from a hook.

No em or en dashes anywhere in this file.
"""
import argparse
import datetime
import html as html_lib
import json
import os
import re
import smtplib
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import email.charset

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_principals as principals   # noqa: E402
import bm_vault_audit as audit             # noqa: E402

# See the module docstring: pinned from Microsoft's own live documentation on
# 2026-08-30, never recalled. Teams renders no later than 1.6 correctly on
# every current client, so the card this module emits never asks for more.
ADAPTIVE_CARD_VERSION = "1.6"
ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"

# The keychain item this estate's mailbox credential lives under. Named here,
# once, so _read_mailbox_credential's NO-DATA message and this module's own
# docstring can never drift apart on the name a founder has to go create.
MAILBOX_KEYCHAIN_ITEM = "brother-mailbox"

# Estate design tokens (see the row: petrol/paper/slate, Iowan Old
# Style/Newsreader display over Seravek/Inter body). One place, so the email
# renderer and any future HTML output in this module share one palette.
PETROL = "#0E7A6F"
PAPER = "#F7F8F6"
SLATE = "#141B22"
DISPLAY_FONT = "'Iowan Old Style', 'Newsreader', Georgia, serif"
BODY_FONT = "Seravek, Inter, -apple-system, Helvetica, Arial, sans-serif"


# ---------------------------------------------------------------------------
# The shared source: parse a VB11-04 digest page into ONE facts dict, and
# flatten that dict into the exact list of atomic fact strings every render_*
# function below (and verify_consistency's re-extraction) works from.
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
_GROUP_RE = re.compile(r"^## (.+) \((\d+) item\(s\)\)$", re.M)
_ITEM_HEADER_RE = re.compile(r"^### item (\d+)$", re.M)


def facts_from_digest(text):
    """(facts, None) or (None, reason). facts: {"principal", "date",
    "groups": [{"folder", "count", "items": [[line, ...], ...]}]}. Each item
    is the exact list of "key: value" card lines bm_vault_digest._render_card
    wrote, read back verbatim rather than re-parsed field by field: this
    module never needs to know what a card field MEANS to keep it consistent
    across three outputs, only that the same lines reach all three."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None, "no frontmatter found; not a digest page"
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    if fm.get("type") != "digest":
        return None, "not a type: digest page (frontmatter type=%r)" % fm.get("type")
    if "principal" not in fm or "date" not in fm:
        return None, "digest frontmatter is missing principal or date"
    body = text[m.end():]
    groups = []
    group_matches = list(_GROUP_RE.finditer(body))
    for gi, gm in enumerate(group_matches):
        folder, count = gm.group(1), int(gm.group(2))
        start = gm.end()
        end = group_matches[gi + 1].start() if gi + 1 < len(group_matches) else len(body)
        section = body[start:end]
        items = []
        item_matches = list(_ITEM_HEADER_RE.finditer(section))
        for ii, im in enumerate(item_matches):
            istart = im.end()
            iend = (item_matches[ii + 1].start() if ii + 1 < len(item_matches)
                    else len(section))
            item_lines = [ln.strip() for ln in section[istart:iend].splitlines()
                          if ln.strip()]
            items.append(item_lines)
        groups.append({"folder": folder, "count": count, "items": items})
    return {"principal": fm["principal"], "date": fm["date"], "groups": groups}, None


def fact_strings(facts):
    """The flat, ordered list of every atomic fact this digest carries. The
    ONE definition every render_* function and verify_consistency's
    re-extraction both measure against."""
    out = ["principal: %s" % facts["principal"], "date: %s" % facts["date"]]
    for g in facts["groups"]:
        # No "folder:" label: this must match the page's own "## %s (%d
        # item(s))" heading verbatim (minus the markdown "## "), which is
        # what render_page reconstructs and what bm_vault_digest.py itself
        # already writes. A "folder:" prefix here would never appear in the
        # page text, which is exactly the mismatch this comment replaces.
        out.append("%s (%d item(s))" % (g["folder"], g["count"]))
        for item in g["items"]:
            out.extend(item)
    return out


# ---------------------------------------------------------------------------
# render_page: reconstructs bm_vault_digest.render_digest's own markdown
# shape FROM facts, so a page produced here is proof the facts dict
# round-trips rather than an assumption that it would.
# ---------------------------------------------------------------------------

def render_page(facts):
    lines = ["---", "type: digest", "principal: %s" % facts["principal"],
              "date: %s" % facts["date"], "---", "",
              "# Digest for %s, %s" % (facts["principal"], facts["date"]), ""]
    for g in facts["groups"]:
        lines.append("## %s (%d item(s))" % (g["folder"], g["count"]))
        for i, item in enumerate(g["items"], 1):
            lines.append("")
            lines.append("### item %d" % i)
            lines.extend(item)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# render_email: one MIMEMultipart("alternative") message, from the SAME
# facts, with an explicit quoted-printable charset so plain ASCII content
# stays 7bit and human (and substring-checkable) in the raw MIME source
# rather than base64, which Python's default utf-8 charset would otherwise
# pick for both parts.
# ---------------------------------------------------------------------------

def render_email_text(facts):
    # The first two lines are the exact fact strings fact_strings() emits
    # ("principal: ...", "date: ..."), not merely a human-phrased header:
    # this is what makes them a literal substring of the finished output
    # for verify_consistency to find, rather than an assumption that a
    # friendlier phrasing would still "cover" them.
    lines = ["principal: %s" % facts["principal"], "date: %s" % facts["date"],
              "Brother digest for %s, %s" % (facts["principal"], facts["date"]), ""]
    for g in facts["groups"]:
        lines.append("%s (%d item(s))" % (g["folder"], g["count"]))
        for i, item in enumerate(g["items"], 1):
            lines.append("  item %d" % i)
            for ln in item:
                lines.append("    %s" % ln)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _html_escape(s):
    return html_lib.escape(s, quote=False)


def render_email_html_body(facts):
    parts = [
        '<div style="max-width:640px;margin:0 auto;padding:24px;'
        'background:%s;color:%s;font-family:%s;">' % (PAPER, SLATE, BODY_FONT),
        '<h1 style="font-family:%s;color:%s;font-size:20px;margin:0 0 4px;">'
        'Brother digest</h1>' % (DISPLAY_FONT, PETROL),
        # Literal fact strings, same reasoning as render_email_text above:
        # these two lines are what verify_consistency actually finds.
        '<p style="margin:0;">principal: %s</p>' % _html_escape(facts["principal"]),
        '<p style="margin:0 0 20px;">date: %s</p>' % _html_escape(facts["date"]),
    ]
    for g in facts["groups"]:
        parts.append(
            '<h2 style="font-family:%s;color:%s;font-size:16px;'
            'border-bottom:1px solid %s;padding-bottom:4px;">%s (%d item(s))</h2>'
            % (DISPLAY_FONT, PETROL, PETROL, _html_escape(g["folder"]), g["count"]))
        for i, item in enumerate(g["items"], 1):
            parts.append(
                '<div style="margin:0 0 16px;padding:12px;background:#ffffff;'
                'border:1px solid #d8ddd9;border-radius:6px;">')
            parts.append('<p style="margin:0 0 4px;font-weight:bold;">item %d</p>' % i)
            for ln in item:
                parts.append('<p style="margin:0 0 2px;">%s</p>' % _html_escape(ln))
            parts.append('</div>')
    parts.append('</div>')
    return "\n".join(parts)


def _qp_part(text, subtype):
    """A MIMEText part with explicit quoted-printable body encoding, so plain
    ASCII content (every field this module ever renders) survives as 7bit
    and human-readable in the raw MIME source, never base64 (Python's
    default for a bare "utf-8" charset). See the module docstring: this is
    what keeps a fact string a plain substring of the finished output for
    verify_consistency to find."""
    charset = email.charset.Charset("utf-8")
    charset.body_encoding = email.charset.QP
    part = MIMEText(text, subtype, None)
    part.set_charset(charset)
    return part


def render_email(facts):
    """A MIMEMultipart("alternative") message: plain text part first, HTML
    part second (the order every mail client expects for a graceful
    fallback), Subject set, To left for send() to fill in since render()
    has no recipient yet."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Brother digest for %s, %s" % (facts["principal"], facts["date"])
    msg.attach(_qp_part(render_email_text(facts), "plain"))
    html_doc = ('<html><body style="margin:0;padding:0;background:%s;">%s'
                '</body></html>') % (PAPER, render_email_html_body(facts))
    msg.attach(_qp_part(html_doc, "html"))
    return msg


# ---------------------------------------------------------------------------
# render_card: the Teams Adaptive Card, from the SAME facts. Every "key:
# value" card line becomes one FactSet fact (title="key:", value="value"),
# which is exactly how bm_vault_digest's own card lines are shaped, so
# nothing here re-decides what a fact means.
# ---------------------------------------------------------------------------

def render_card(facts):
    body = [
        {"type": "TextBlock", "weight": "bolder", "size": "medium", "wrap": True,
         "text": "Brother digest for %s, %s" % (facts["principal"], facts["date"])},
        # Literal fact strings, same reasoning as the email renderer above.
        {"type": "TextBlock", "wrap": True, "text": "principal: %s" % facts["principal"]},
        {"type": "TextBlock", "wrap": True, "text": "date: %s" % facts["date"]},
    ]
    for g in facts["groups"]:
        body.append({"type": "TextBlock", "weight": "bolder", "wrap": True,
                      "text": "%s (%d item(s))" % (g["folder"], g["count"])})
        for i, item in enumerate(g["items"], 1):
            body.append({"type": "TextBlock", "wrap": True, "text": "item %d" % i})
            fset, extra = [], []
            for line in item:
                if ":" in line:
                    k, v = line.split(":", 1)
                    fset.append({"title": "%s:" % k.strip(), "value": v.strip()})
                else:
                    extra.append(line)
            if fset:
                body.append({"type": "FactSet", "facts": fset})
            for ln in extra:
                body.append({"type": "TextBlock", "wrap": True, "text": ln})
    return {
        "$schema": ADAPTIVE_CARD_SCHEMA,
        "type": "AdaptiveCard",
        "version": ADAPTIVE_CARD_VERSION,
        "body": body,
    }


# ---------------------------------------------------------------------------
# verify_consistency: re-extract the fact set from each FINISHED output,
# independently of how it was built, and diff against the page's own set.
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _visible_text_from_html(text):
    """A crude, stdlib-only HTML-to-text: strip tags, unescape entities.
    Enough to recover the exact fact strings render_email_html_body wrote
    (it escapes with html.escape, this reverses it); not a general HTML
    parser and never claims to be one."""
    return html_lib.unescape(_TAG_RE.sub(" ", text))


def _facts_from_card_json(card_obj):
    """Every TextBlock "text" and every FactSet fact's "title: value",
    reconstructed as "title value" so a FactSet-carried fact reads back as
    the same "key: value" string it was split from in render_card. One flat
    text blob, newline-joined, for a substring check."""
    chunks = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "TextBlock" and "text" in node:
                chunks.append(node["text"])
            if node.get("type") == "FactSet":
                for f in node.get("facts", []):
                    chunks.append("%s %s" % (f.get("title", ""), f.get("value", "")))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(card_obj)
    return "\n".join(chunks)


def verify_consistency(page_text, email_text, card_text):
    """(ok, problems). problems is a list of "MISSING in <output>: <fact>"
    strings, empty when every fact string extracted from the page also
    appears, verbatim, in the email's visible text and the card's text
    blob. NO-DATA (None, [reason]) when the page itself cannot be parsed;
    a malformed email or card is instead reported as every fact missing
    from it, which is the honest reading of "this output does not carry
    what the page says it carries"."""
    facts, err = facts_from_digest(page_text)
    if err:
        return None, ["NO-DATA: page could not be parsed as a digest (%s)" % err]
    wanted = fact_strings(facts)
    email_visible = _visible_text_from_html(email_text)
    try:
        card_obj = json.loads(card_text)
        card_blob = _facts_from_card_json(card_obj)
    except ValueError as e:
        card_blob = ""
        problems_json = ["card JSON did not parse: %s" % e]
    else:
        problems_json = []
    problems = list(problems_json)
    for fact in wanted:
        if fact not in email_visible:
            problems.append("MISSING in email: %s" % fact)
        if fact not in card_blob:
            problems.append("MISSING in card: %s" % fact)
    return (not problems), problems


# ---------------------------------------------------------------------------
# The email adapter. Credential read and the SMTP conversation are two
# separate seams (rule f): tests replace _send_smtp only, and exercise the
# real, local, no-network _read_mailbox_credential against whatever the
# actual keychain holds (nothing on a CI/sandbox machine, by construction).
# ---------------------------------------------------------------------------

def _read_mailbox_credential():
    """(credential, None) or (None, reason). Reads the mailbox credential
    from the macOS keychain AT CALL TIME only: not stored past this one
    call, never echoed, never logged, never passed as an argv value to
    anything else. Local-only, no-network `security` invocation (the one
    named subprocess exception this module carries, see the module
    docstring). Credential shape is this module's own convention (see the
    docstring): "host|port|username|password"."""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", MAILBOX_KEYCHAIN_ITEM, "-w"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return None, "NO-DATA: could not read the %s keychain item (%s)" % (
            MAILBOX_KEYCHAIN_ITEM, e)
    value = (proc.stdout or "").strip("\n")
    if proc.returncode != 0 or not value:
        return None, "NO-DATA: no %s credential in the keychain" % MAILBOX_KEYCHAIN_ITEM
    if value.count("|") != 3:
        return None, ("NO-DATA: the %s keychain item is not in host|port|username|"
                       "password shape" % MAILBOX_KEYCHAIN_ITEM)
    return value, None


def _send_smtp(credential, to_addr, mime_message):
    """The ONE seam a test replaces (rule f: no network in tests, never a
    real send). Never called with a real credential outside a deliberate,
    explicit `send --channel email` invocation."""
    host, port, username, password = credential.split("|", 3)
    mime_message["From"] = username
    mime_message["To"] = to_addr
    with smtplib.SMTP(host, int(port), timeout=20) as conn:
        conn.starttls()
        conn.login(username, password)
        conn.sendmail(username, [to_addr], mime_message.as_string())


# ---------------------------------------------------------------------------
# Recipient resolution: the SAME principal registry bm_vault_principals.py
# owns, never a second store of who anyone is.
# ---------------------------------------------------------------------------

def _resolve_contact(vault, registry_override, name):
    """(key, rec, None) or (None, None, reason). key is the registry's own
    stored spelling (see bm_vault_principals._find_key); rec is that
    principal's full record dict."""
    path = principals.registry_path(vault, registry_override)
    if not path:
        return None, None, "NO-DATA: no vault and no --registry override"
    registry, problems = principals.load(path)
    if problems:
        return None, None, "NO-DATA: %s" % "; ".join(problems)
    principals_map = registry.get("principals", {}) if registry else {}
    key = principals._find_key(principals_map, name)
    if key is None:
        return None, None, "NO-DATA: %r is not a registered principal" % name
    return key, principals_map[key], None


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _out_paths(digest_path, out_dir):
    stem = os.path.splitext(os.path.basename(digest_path))[0]
    directory = out_dir or os.path.dirname(os.path.abspath(digest_path))
    return (os.path.join(directory, stem + ".page.md"),
            os.path.join(directory, stem + ".email.eml"),
            os.path.join(directory, stem + ".card.json"))


def cmd_render(args):
    try:
        with open(args.digest, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print("NO-DATA: could not read %s (%s)" % (args.digest, e))
        return 2
    facts, err = facts_from_digest(text)
    if err:
        print("NO-DATA: %s" % err)
        return 2
    page_out, email_out, card_out = _out_paths(args.digest, args.out_dir)
    page_text = render_page(facts)
    email_text = render_email(facts).as_string()
    card_text = json.dumps(render_card(facts), indent=2, sort_keys=True) + "\n"
    for path, text_out in ((page_out, page_text), (email_out, email_text),
                            (card_out, card_text)):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text_out)
    print("PAGE %s" % page_out)
    print("EMAIL %s" % email_out)
    print("CARD %s" % card_out)
    ok, problems = verify_consistency(page_text, email_text, card_text)
    if ok:
        print("CONSISTENT: %d fact(s) match across page, email and card"
              % len(fact_strings(facts)))
    else:
        for p in problems:
            print("FINDING: %s" % p)
        return 1
    return 0


def cmd_verify(args):
    def _read(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read(), None
        except OSError as e:
            return None, "NO-DATA: could not read %s (%s)" % (path, e)

    page_text, err = _read(args.page)
    if err:
        print(err)
        return 2
    email_text, err = _read(args.email)
    if err:
        print(err)
        return 2
    card_text, err = _read(args.card)
    if err:
        print(err)
        return 2
    ok, problems = verify_consistency(page_text, email_text, card_text)
    if ok is None:
        print(problems[0])
        return 2
    if ok:
        print("OK: consistent across page, email and card")
        return 0
    for p in problems:
        print("FINDING: %s" % p)
    return 1


def cmd_send(args):
    key, rec, err = _resolve_contact(args.vault, args.registry, args.to)
    if err:
        print(err)
        return 2
    try:
        with open(args.digest, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        print("NO-DATA: could not read %s (%s)" % (args.digest, e))
        return 2
    facts, ferr = facts_from_digest(text)
    if ferr:
        print("NO-DATA: %s" % ferr)
        return 2

    if args.channel == "email":
        addr = rec.get("email")
        if not addr:
            print("NO-DATA: %s has no email on file; record one with "
                  "bm_vault_principals.py contact --email first" % key)
            return 2
        credential, cred_err = _read_mailbox_credential()
        if credential is None:
            print(cred_err)
            return 2
        msg = render_email(facts)
        _send_smtp(credential, addr, msg)
        event_id = audit.new_event_id()
        audit.append(key, "notify send channel=email digest=%s to=%s"
                      % (args.digest, addr), [], 0, event_id)
        print("SENT email to %s for %s" % (addr, key))
        return 0

    if args.channel == "teams":
        identity = rec.get("teams_identity")
        if not identity:
            print("NO-DATA: %s has no teams_identity on file; record one with "
                  "bm_vault_principals.py contact --teams-identity first" % key)
            return 2
        if not args.mock_sink:
            print("NO-DATA: teams is fixture-mode only pending tenant consent "
                  "(see docs/TEAMS-CONSENT-REQUEST.md); pass --mock-sink FILE")
            return 2
        card = render_card(facts)
        payload = {"to_teams_identity": identity, "card": card}
        os.makedirs(os.path.dirname(os.path.abspath(args.mock_sink)) or ".",
                    exist_ok=True)
        with open(args.mock_sink, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        event_id = audit.new_event_id()
        audit.append(key, "notify send channel=teams digest=%s to=%s"
                      % (args.digest, identity), [], 0, event_id)
        print("SENT (mock) teams to %s for %s" % (identity, key))
        return 0

    print("NO-DATA: unknown channel %r" % args.channel)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command")

    p_render = sub.add_parser("render")
    p_render.add_argument("--digest", required=True)
    p_render.add_argument("--out-dir", default=None)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--page", required=True)
    p_verify.add_argument("--email", required=True)
    p_verify.add_argument("--card", required=True)

    p_send = sub.add_parser("send")
    p_send.add_argument("--channel", choices=("email", "teams"), required=True)
    p_send.add_argument("--digest", required=True)
    p_send.add_argument("--to", required=True, help="a registered principal name")
    p_send.add_argument("--vault", default=os.environ.get("BM_VAULT_ROOT")
                         or os.environ.get("BROTHERMODE_VAULT"))
    p_send.add_argument("--registry", default=None)
    p_send.add_argument("--mock-sink", dest="mock_sink", default=None,
                         help="teams only: file to record the would-be send to")

    args = ap.parse_args(argv)
    if args.command == "render":
        return cmd_render(args)
    if args.command == "verify":
        return cmd_verify(args)
    if args.command == "send":
        return cmd_send(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
