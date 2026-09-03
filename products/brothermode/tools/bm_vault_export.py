#!/usr/bin/env python3
"""bm_vault_export: the secure clearing house. WBS VB8-04.

WHY THIS EXISTS. The connector debate locked Iceberg as the eventual primary
export target, but pyiceberg is not on this machine's floor and a catalog with
its own KMS is a deployment decision, not something this tool can stand up.
This module is the interim clearing house: the two table files are
order-deterministic (same input vault, same rows in the same order), while
MANIFEST.json is unique per run (bundle_id and generated_at differ every
time even over an unchanged vault) -- "deterministic" below describes the
tables, never the manifest. It is self-describing and tamper-evident: any
consumer can read it with the standard library alone, today, while the
warehouse side (its own KMS, its own catalog) is built separately. When that
warehouse exists, it materializes FROM these same JSONL tables; this tool
never hand-rolls column or file encryption the way bm_vault_events.py's own
encryption clause already forbids for the Iceberg path -- the analogous rule
here is that when a bundle carries recipients, the WHOLE bundle is handed to
`age` (through bm_vault_exchange's own seam) rather than any field or file
inside it being encrypted piecemeal. The hash chain (sha256_manifest over the
plaintext table hashes) DETECTS TAMPERING, it does not prove AUTHORSHIP: it
is a checksum, not a signature, and mirrors bm_vault_exchange's own
sha256_manifest, which catches corruption and casual tampering, not a forger
who can recompute the same public formula.

  bundle --vault V --out DIR [--recipient AGE_PUBKEY...] [--include-restricted]
                             [--events PATH]
  verify --bundle DIR

THE TWO TABLES, one JSONL file each, one JSON object per line, keys sorted:

  assertions.jsonl   one row per `claim: ... [evidence: ...]` line found by
                      bm_vault_provenance's own claim syntax, restricted to
                      notes that carry a stable id (bm_vault_ids.py's
                      `id: n-<16 hex>`) -- a claim on a note with no id has
                      nothing to key the row on and is counted, never
                      silently dropped, in the command's own stderr summary.
                      Columns: note_id, relpath, content_hash (sha256 of the
                      note's full text, so a bundle proves which VERSION of
                      the note the claim came from), tenant (a placeholder;
                      this vault is single-tenant today and the column exists
                      so a future multi-tenant vault does not need a schema
                      migration to add it), valid_from, valid_to, observed_at,
                      ingested_at, verified_at (bm_vault_temporal.py's five
                      fields, ISO date or "" when the note is unmigrated),
                      authority (bm_vault_authority.py's level, or
                      "unrankable" for a declared-but-unknown value),
                      lifecycle (bm_vault_lifecycle.py's promotion state, or
                      "legacy" undeclared / "unrankable" unknown), sensitivity
                      ("restricted" when the note's own `restricted: true`
                      frontmatter is set -- the same field bm_vault_intake.py
                      writes at quarantine time -- else "standard"),
                      evidence_locator (the claim's own locator string,
                      opaque to this exporter -- VB3-07 added query_id/
                      document_span/capture locator kinds alongside the
                      original path/id/commit/url four, and this column
                      carries any of the seven verbatim without a schema
                      change, exactly like it already did before VB3-07),
                      claim_text (the note's own claim PROSE, verbatim --
                      this is exactly why a restricted note's rows are
                      EXCLUDED BY DEFAULT: claim_text is not a hash or a
                      pointer, it is the sentence itself, and a bundle is an
                      outbound artifact).

RESTRICTED NOTES, default-excluded: a note whose frontmatter carries
`restricted: true` contributes NO rows to assertions.jsonl unless
`--include-restricted` is passed explicitly. The default (exclude) path
always prints the excluded count to stderr, even when it is zero, so a
forgotten flag can never ship restricted prose silently. When
`--include-restricted` is used, both the printed BUNDLED line and
MANIFEST.json carry `restricted_included: true` (folded into
sha256_manifest, so flipping it after the fact is caught like any other
tampered field) -- the default run carries `restricted_included: false` in
both places for the same reason. The old `--exclude-restricted` flag is
REMOVED (it named the previous, unsafe default; nothing else in this repo
referenced it, grepped before removal).
  events.jsonl        the bm_vault_events.py stream, REUSED BY IMPORT (fold()
                      and the load_events/parse_lines/_validate chain that
                      backs it -- never re-parsed here), filtered to events
                      whose `ref` is one of the notes actually selected into
                      this bundle. Payload-free by that module's own
                      validation: a row here can never carry note content.
                      SOURCE CONVENTION: `--events PATH` names one JSONL
                      event-log file explicitly; with no flag, `<vault>/
                      .vault/events.jsonl` is used if it exists. Neither
                      existing is not an error -- a vault that keeps no event
                      log yet exports a clean, empty events table (0 live, 0
                      tombstoned is still a clean fold per that module's own
                      contract), never a fabricated one.

MANIFEST.json, written alongside the tables, plaintext always (even in
encrypted mode -- exactly like bm_vault_exchange's own manifest stub, which
carries counts and hashes but never content): bundle_id, schema_version,
generated_at, encrypted (bool), restricted_included (bool, see RESTRICTED NOTES above),
counts (assertions/events row counts), files (sha256 of each PLAINTEXT table
file, recorded before encryption so a decrypted bundle can be checked
against the same hashes), and, only when encrypted, ciphertext_file plus
sha256_ciphertext (the ciphertext bytes) and sha256_manifest (a fold over
the other manifest fields, the same tamper net bm_vault_exchange.py already
runs, so an edited manifest field -- including restricted_included -- fails
exactly like a flipped ciphertext byte).

BUNDLE ID SHAPE: `bundle-` + 16 lowercase hex, DELIBERATELY not the exchange
lane's `xchg-` shape -- a clearing-house export is not a vault-to-vault
exchange bundle, and giving it a different prefix means the two can never be
confused by a tool that only checks the id's shape.

TWO MODES.
  Trusted-channel (no --recipient): assertions.jsonl, events.jsonl and
  MANIFEST.json land in DIR as plaintext. This is for a local handoff over an
  already-trusted channel (a filesystem the receiver already controls); DIR
  itself carries no access control of its own.
  Encrypted (--recipient given, repeatable): the two table files are packed
  into a tar, handed whole to bm_vault_exchange.age_encrypt (never
  reimplemented here), and only the ciphertext (<bundle_id>.age) plus
  MANIFEST.json land in DIR -- no plaintext table file is ever written to
  DIR in this mode. `age` absent on PATH surfaces that seam's own NO-DATA
  message (naming `brew install age`) at exit 2.

`verify --bundle DIR` never decrypts (no identity is asked for): it recomputes
every hash MANIFEST.json records -- sha256_manifest first, then either the
plaintext table files' hashes (trusted-channel mode) or the ciphertext's own
hash (encrypted mode) -- and refuses at exit 1, NAMING the file, on the first
mismatch it finds. A missing MANIFEST.json is NO-DATA at exit 2.

Exit 0 clean. Exit 1 a real refusal (tamper detected, or a bundle command hit
a REJECT condition). Exit 2 NO-DATA (unreadable vault, missing manifest, age
absent). Python 3.9 floor, standard library plus the `age` binary via
bm_vault_exchange's own subprocess seam only.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bm_vault_events as events_mod           # noqa: E402
import bm_vault_exchange as exch               # noqa: E402
import bm_vault_provenance as prov             # noqa: E402
import bm_vault_temporal as temporal           # noqa: E402
import bm_vault_authority as authority         # noqa: E402
import bm_vault_lifecycle as lifecycle         # noqa: E402

import re
import uuid

SCHEMA_VERSION = 1
BUNDLE_ID_RE = re.compile(r"^bundle-[0-9a-f]{16}\Z")
RESTRICTED_RE = re.compile(r"^restricted:\s*(\S+)\s*$", re.M)
TENANT_PLACEHOLDER = "unassigned"

# The manifest fields folded into sha256_manifest, same tamper-net shape as
# bm_vault_exchange._manifest_integrity_hash (excludes sha256_manifest itself).
MANIFEST_CANON_KEYS = ("bundle_id", "schema_version", "generated_at", "encrypted",
                        "restricted_included", "counts", "files", "ciphertext_file",
                        "sha256_ciphertext")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text):
    return _sha256_bytes(text.encode("utf-8"))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_integrity_hash(manifest):
    core = {k: manifest.get(k) for k in MANIFEST_CANON_KEYS}
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _iso_date(d):
    return d.isoformat() if d else ""


def read_restricted(text):
    """True when the note's own frontmatter carries `restricted: true` --
    the exact field bm_vault_intake._build_note writes at quarantine time."""
    m = RESTRICTED_RE.search(prov._frontmatter(text))
    if not m:
        return False
    return m.group(1).strip().strip('"').strip("'").lower() in ("true", "yes", "1")


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            fh.write("\n")


def build_assertions(vault, include_restricted):
    """([row, ...], notes_without_id_claims_skipped, selected_note_ids,
    skipped_unreadable, skipped_restricted_notes). Restricted notes
    (frontmatter `restricted: true`) are dropped UNLESS include_restricted
    is True -- claim_text is verbatim note prose, so the safe default is
    exclusion, never inclusion by omission."""
    rows = []
    skipped_no_id = 0
    skipped_unreadable = 0
    skipped_restricted_notes = 0
    selected_ids = set()
    for path in prov.walk(vault):
        rel = os.path.relpath(path, vault)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            skipped_unreadable += 1
            continue
        claims = prov.find_claims(text)
        if not claims:
            continue
        restricted = read_restricted(text)
        if restricted and not include_restricted:
            skipped_restricted_notes += 1
            continue
        m = prov.ID_RE.search(prov._frontmatter(text))
        note_id = None
        if m:
            candidate = m.group(1).strip().strip('"').strip("'")
            if prov.ID_VALUE_RE.match(candidate):
                note_id = candidate
        if note_id is None:
            skipped_no_id += len(claims)
            continue
        selected_ids.add(note_id)
        content_hash = _sha256_text(text)
        window, _problems = temporal.parse(text)
        authority_level, _authority_problem = authority.read_authority(text)
        promotion_state, _record, _promo_problems = lifecycle.read_promotion(text)
        for claim_text, locator in claims:
            rows.append({
                "note_id": note_id,
                "relpath": rel,
                "content_hash": content_hash,
                "tenant": TENANT_PLACEHOLDER,
                "valid_from": _iso_date(window.get("valid_from")),
                "valid_to": _iso_date(window.get("valid_to")),
                "observed_at": _iso_date(window.get("observed_at")),
                "ingested_at": _iso_date(window.get("ingested_at")),
                "verified_at": _iso_date(window.get("verified_at")),
                "authority": authority_level or "unrankable",
                "lifecycle": promotion_state or "unrankable",
                "sensitivity": "restricted" if restricted else "standard",
                "evidence_locator": locator,
                "claim_text": claim_text,
            })
    rows.sort(key=lambda r: (r["note_id"], r["evidence_locator"], r["claim_text"]))
    return rows, skipped_no_id, selected_ids, skipped_unreadable, skipped_restricted_notes


def build_events(vault, events_path, selected_ids):
    """[row, ...], reusing bm_vault_events.load_events (validation) and
    bm_vault_events.fold (the replay contract) by IMPORT -- never re-parsed.
    An absent event log is not an error: it folds to an empty table."""
    if events_path is None:
        default = os.path.join(vault, ".vault", "events.jsonl")
        events_path = default if os.path.isfile(default) else None
    if events_path is None:
        return []
    raw = events_mod.load_events([events_path])
    state = events_mod.fold(raw)
    rows = []
    for r in state["live"]:
        if r["ref"] in selected_ids:
            row = dict(r)
            row["status"] = "live"
            rows.append(row)
    for r in state["tombstoned"]:
        if r["ref"] in selected_ids:
            row = dict(r)
            row["status"] = "tombstoned"
            rows.append(row)
    rows.sort(key=lambda r: (r["ref"], r["status"]))
    return rows


def _tar_bytes(file_map):
    """file_map: {arcname: bytes}. Returns the tar bytes holding exactly
    those members -- the payload handed whole to age_encrypt in encrypted
    mode, mirroring bm_vault_exchange.build_pack's own shape."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for arcname, data in file_map.items():
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def cmd_bundle(args):
    vault = args.vault
    if not vault or not os.path.isdir(vault):
        print("bm_vault_export: NO-DATA, no readable vault at %r" % vault, file=sys.stderr)
        return 2

    include_restricted = bool(args.include_restricted)
    (assertions, skipped_no_id, selected_ids, skipped_unreadable,
     skipped_restricted_notes) = build_assertions(vault, include_restricted)
    events_rows = build_events(vault, args.events, selected_ids)

    os.makedirs(args.out, exist_ok=True)
    bundle_id = "bundle-" + uuid.uuid4().hex[:16]

    assertions_bytes = ("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":"))
                                   for r in assertions) + ("\n" if assertions else "")).encode("utf-8")
    events_bytes = ("\n".join(json.dumps(r, sort_keys=True, separators=(",", ":"))
                              for r in events_rows) + ("\n" if events_rows else "")).encode("utf-8")

    manifest = {
        "bundle_id": bundle_id,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "encrypted": bool(args.recipient),
        "restricted_included": include_restricted,
        "counts": {"assertions": len(assertions), "events": len(events_rows)},
        "files": {
            "assertions.jsonl": _sha256_bytes(assertions_bytes),
            "events.jsonl": _sha256_bytes(events_bytes),
        },
    }

    if args.recipient:
        tar_data = _tar_bytes({"assertions.jsonl": assertions_bytes, "events.jsonl": events_bytes})
        ciphertext_name = "%s.age" % bundle_id
        ciphertext_path = os.path.join(args.out, ciphertext_name)
        ok, err = exch.age_encrypt(args.recipient, tar_data, ciphertext_path)
        if not ok:
            print("bm_vault_export: %s" % err, file=sys.stderr)
            return exch._age_exit_code(err)
        manifest["ciphertext_file"] = ciphertext_name
        manifest["sha256_ciphertext"] = _sha256_file(ciphertext_path)
        manifest["sha256_manifest"] = _manifest_integrity_hash(manifest)
    else:
        with open(os.path.join(args.out, "assertions.jsonl"), "wb") as fh:
            fh.write(assertions_bytes)
        with open(os.path.join(args.out, "events.jsonl"), "wb") as fh:
            fh.write(events_bytes)
        manifest["sha256_manifest"] = _manifest_integrity_hash(manifest)

    manifest_path = os.path.join(args.out, "MANIFEST.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    if skipped_no_id:
        print("bm_vault_export: %d claim(s) skipped, note carries no stable id" % skipped_no_id,
              file=sys.stderr)
    if skipped_unreadable:
        print("bm_vault_export: skipped_unreadable: %d" % skipped_unreadable, file=sys.stderr)
    if not include_restricted:
        print("bm_vault_export: %d restricted note(s) excluded (default policy; "
              "pass --include-restricted to opt in)" % skipped_restricted_notes,
              file=sys.stderr)
    print("BUNDLED bundle=%s assertions=%d events=%d encrypted=%s restricted_included=%s out=%s"
          % (bundle_id, len(assertions), len(events_rows), bool(args.recipient),
             include_restricted, args.out))
    return 0


def cmd_verify(args):
    d = args.bundle
    manifest_path = os.path.join(d, "MANIFEST.json")
    if not os.path.isfile(manifest_path):
        print("bm_vault_export: NO-DATA, no MANIFEST.json at %r" % d, file=sys.stderr)
        return 2
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        print("bm_vault_export: NO-DATA, MANIFEST.json unreadable (%s)" % exc, file=sys.stderr)
        return 2

    bundle_id = manifest.get("bundle_id")
    if not bundle_id or not BUNDLE_ID_RE.match(bundle_id):
        print("bm_vault_export: REFUSE MANIFEST.json (bundle_id %r does not match "
              "the expected bundle-<16 hex> shape)" % bundle_id, file=sys.stderr)
        return 1

    expected_manifest_hash = manifest.get("sha256_manifest")
    if not expected_manifest_hash:
        print("bm_vault_export: NO-DATA, MANIFEST.json carries no sha256_manifest field",
              file=sys.stderr)
        return 2
    if _manifest_integrity_hash(manifest) != expected_manifest_hash:
        print("bm_vault_export: REFUSE MANIFEST.json, manifest fields do not match "
              "the recorded sha256_manifest", file=sys.stderr)
        return 1

    if manifest.get("encrypted"):
        ciphertext_name = manifest.get("ciphertext_file")
        expected = manifest.get("sha256_ciphertext")
        if not ciphertext_name or not expected:
            print("bm_vault_export: NO-DATA, MANIFEST.json missing ciphertext fields",
                  file=sys.stderr)
            return 2
        ciphertext_path = os.path.join(d, ciphertext_name)
        if not os.path.isfile(ciphertext_path):
            print("bm_vault_export: NO-DATA, ciphertext file %r not found" % ciphertext_name,
                  file=sys.stderr)
            return 2
        actual = _sha256_file(ciphertext_path)
        if actual != expected:
            print("bm_vault_export: REFUSE %s (expected sha256=%s, got=%s)"
                  % (ciphertext_name, expected, actual), file=sys.stderr)
            return 1
        print("VERIFIED bundle=%s encrypted=True file=%s" % (bundle_id, ciphertext_name))
        return 0

    files = manifest.get("files") or {}
    for name in ("assertions.jsonl", "events.jsonl"):
        expected = files.get(name)
        path = os.path.join(d, name)
        if not expected:
            print("bm_vault_export: NO-DATA, MANIFEST.json carries no hash for %s" % name,
                  file=sys.stderr)
            return 2
        if not os.path.isfile(path):
            print("bm_vault_export: REFUSE %s not found" % name, file=sys.stderr)
            return 1
        actual = _sha256_file(path)
        if actual != expected:
            print("bm_vault_export: REFUSE %s (expected sha256=%s, got=%s)"
                  % (name, expected, actual), file=sys.stderr)
            return 1
    print("VERIFIED bundle=%s encrypted=False files=assertions.jsonl,events.jsonl" % bundle_id)
    return 0


def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pb = sub.add_parser("bundle", help="export the assertions/events JSONL tables plus manifest")
    pb.add_argument("--vault", required=True)
    pb.add_argument("--out", required=True)
    pb.add_argument("--recipient", action="append", default=[],
                     help="an age recipient public key; repeatable. Omit for a plaintext, "
                          "trusted-channel bundle.")
    pb.add_argument("--include-restricted", action="store_true",
                     help="include notes whose frontmatter carries restricted: true "
                          "(excluded by default; claim_text is verbatim note prose)")
    pb.add_argument("--events", default=None,
                     help="path to a bm_vault_events JSONL stream; default "
                          "<vault>/.vault/events.jsonl if it exists, else an empty table")

    pv = sub.add_parser("verify", help="recompute every hash MANIFEST.json records")
    pv.add_argument("--bundle", required=True)
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return cmd_bundle(args) if args.command == "bundle" else cmd_verify(args)


if __name__ == "__main__":
    sys.exit(main())
