#!/usr/bin/env python3
"""bm_vault_exchange: vault-to-vault exchange over an age-encrypted relay. WBS VB8-02.

WHY THIS EXISTS. Two vaults need to trade notes without a shared filesystem and
without a live network service: a relay directory (a shared drive, a git repo, a
USB stick) that carries CIPHERTEXT ONLY. This is that exchange: pack a selection
of notes, encrypt them to the receiving vault's age public key, and land them in
the relay; on the other end, verify the ciphertext has not been tampered with,
decrypt it, and admit every note THROUGH bm_vault_intake.py's own admit gates
(credential shape, deny-list terms, dirt classification, quarantine, echo
detection), never through a second, weaker door.

  export --vault V --recipient AGE_PUBKEY... --out RELAY_DIR [--notes ID... | --since DATE]
  import --vault V --identity AGE_KEYFILE --bundle PATH [--by ACTOR] [--restricted] [--deny-list PATH]

ENCRYPTION IS NEVER REIMPLEMENTED HERE. Both directions shell out to the real
`age` binary (subprocess only), because rolling age's own crypto in Python is
exactly the kind of thing that looks fine and is not. age_encrypt/age_decrypt
are the two seam functions: production code calls the real binary; a test that
cannot install age on this machine replaces these two names with a fake in
process, which is a legitimate substitute for the same reason bm_vault_intake's
own tests copy tools/ aside rather than mutate this checkout. `age` absent on
PATH is NO-DATA at exit 2, naming `brew install age`, never a silent skip.

RELAY_DIR NEVER SEES PLAINTEXT. export writes exactly two files there: the
ciphertext (<bundle-id>.age) and a manifest stub (<bundle-id>.manifest.json)
holding only the bundle id, created_at, note count, the SHA-256 of the
CIPHERTEXT bytes, and sha256_manifest (a second hash folding the other
manifest fields together, see TAMPER REFUSAL) -- never a note title, a body,
or a plaintext file list. A scan of RELAY_DIR for any source note's title or
body text must find nothing.

TAMPER REFUSAL, all of it before calling age at all. bundle_id must match the
exact shape cmd_export mints (xchg-<16 hex>); anything else refuses at exit 1
with class=malformed-manifest, because bundle_id flows straight into a raw
provenance_source frontmatter line at admit time and an unvalidated value
there could inject arbitrary YAML keys. sha256_manifest, folding bundle_id,
created_at, count and ciphertext_file together, is recomputed and compared;
a mismatch refuses at exit 1 with class=manifest-tamper. Only then does
import recompute the ciphertext's own SHA-256 and compare it to the manifest
stub; a mismatch (corruption, or a byte changed in transit) refuses at exit 1
with class=ciphertext-tamper. Any of the three refusals decrypts nothing,
imports nothing.

NO PRIVATE KEY EVER TOUCHES THIS TOOL'S OWN STATE. --identity takes a file
PATH and passes it straight to the age binary; this module never reads,
generates, stores, logs, or prints its contents. An --identity value that
itself LOOKS like a literal secret key (starts with the age secret-key prefix)
is refused outright, before anything else runs, so a key can never end up in
argv history through this tool: pass a keyfile path instead (`age-keygen -o
identity.txt` writes one).

SENDER-CLAIMED IDENTITY IS NEVER TRUSTED. bm_vault_intake.admit mints a FRESH
note id for every admitted file and never reads or reuses the file's own
embedded `id:` field (see that module's _admit_one); a sending vault's note ids
are therefore inert on arrival, and any apparent duplicate is caught by
intake's own title-overlap duplicate-suspect finder, the same rule the front
door already enforces for every other admission path.

CRASH HYGIENE, BEST EFFORT ONLY. import decrypts into a `tempfile.mkdtemp`
workdir cleaned up in a `finally` and also registered with `atexit`, so a
normal exit or a caught exception always removes it. Neither covers a hard
kill (SIGKILL) or a power loss: a decrypted plaintext copy can be left
sitting under the OS temp dir until that OS's own reboot-time temp cleanup
removes it. This is a stated limit, not a guarantee.

Exit 0: the command completed and (for import) every admitted file cleared
intake's own gates. Exit 1: a real rejection (a bundle tampered with, or
intake rejected at least one file). Exit 2: NO-DATA (unreadable vault, the
age binary is absent, a required sibling module is missing, an unreadable
bundle or manifest). Python 3.9, standard library plus the age binary via
subprocess only, no network.
"""
import argparse
import atexit
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
AGE_BIN = "age"
AGE_SECRET_PREFIX = "AGE-SECRET-KEY-"
AGE_INSTALL_HINT = "brew install age"

# The exact shape cmd_export mints: "xchg-" + 16 lowercase hex chars
# (uuid4().hex[:16]). Any manifest bundle_id that does not match this is
# refused before any decrypt: it is either corrupted or a hostile field
# meant to inject content (e.g. a newline) into the provenance_source line
# built downstream by bm_vault_intake._build_note.
BUNDLE_ID_RE = re.compile(r"^xchg-[0-9a-f]{16}\Z")

# The manifest fields whose canonical bytes are folded into sha256_manifest
# (excludes sha256_manifest itself, which cannot hash its own value).
MANIFEST_CANON_KEYS = ("bundle_id", "created_at", "count", "ciphertext_file", "sha256_ciphertext")


def _load_sibling(name):
    """tools/<name>.py loaded BY PATH, the same guarded pattern every sibling
    bm_vault_* module already uses (bm_vault_intake, bm_vault_cli). Returns the
    module, or None when the file is absent or fails to import, so a missing
    contract module is a named NO-DATA at the call site rather than a silent pass."""
    path = os.path.join(HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # sbe: allow-silent documented above: None is a named NO-DATA at the call site when the sibling module is absent or fails to import
        return None


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_integrity_hash(manifest):
    """sha256 over the manifest's own core fields (MANIFEST_CANON_KEYS) as
    canonical sorted-key JSON. The stub hash used to cover only the
    ciphertext bytes, which left every OTHER manifest field (bundle_id,
    created_at, count) unauthenticated: a relay-hop edit to bundle_id alone
    would still pass the ciphertext check untouched. Folding those fields in
    means a tampered manifest field now fails the same way a flipped
    ciphertext byte already does. This is still a checksum, not a MAC: it
    catches corruption and casual tampering, not a forger who can also
    recompute this same public formula, exactly the limit sha256_ciphertext
    already carried."""
    core = {k: manifest.get(k) for k in MANIFEST_CANON_KEYS}
    canonical = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def looks_like_literal_age_key(value):
    """True when `value` is itself a secret key rather than a file path, so the
    caller can refuse it before it ever reaches argv history or the age binary."""
    return isinstance(value, str) and value.strip().startswith(AGE_SECRET_PREFIX)


# --------------------------------------------------------------- age seam
#
# THE INJECTED SEAM. Both functions shell out to the real `age` binary and
# nothing else; a test with no age on the machine replaces these two module
# attributes with a fake in-process pair before calling cmd_export/cmd_import
# directly. Production code (main()) never sees the difference.

def age_encrypt(recipients, data, out_path):
    """(ok, error_or_None). Encrypts `data` bytes to every recipient, writing
    binary ciphertext to out_path. error_or_None starts with "NO-DATA" when
    the age binary itself could not be found or run."""
    if shutil.which(AGE_BIN) is None:
        return False, ("NO-DATA: the `age` binary was not found on PATH; "
                        "install it with `%s`" % AGE_INSTALL_HINT)
    cmd = [AGE_BIN]
    for r in recipients:
        cmd += ["-r", r]
    cmd += ["-o", out_path]
    try:
        proc = subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return False, "NO-DATA: could not run age (%s)" % exc
    if proc.returncode != 0:
        return False, "age encrypt failed: %s" % proc.stderr.decode("utf-8", "replace").strip()
    return True, None


def age_decrypt(identity_path, in_path, out_path):
    """(ok, error_or_None). Decrypts in_path with the identity at identity_path,
    writing plaintext bytes to out_path. Same NO-DATA contract as age_encrypt."""
    if shutil.which(AGE_BIN) is None:
        return False, ("NO-DATA: the `age` binary was not found on PATH; "
                        "install it with `%s`" % AGE_INSTALL_HINT)
    cmd = [AGE_BIN, "-d", "-i", identity_path, "-o", out_path, in_path]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return False, "NO-DATA: could not run age (%s)" % exc
    if proc.returncode != 0:
        return False, "age decrypt failed: %s" % proc.stderr.decode("utf-8", "replace").strip()
    return True, None


def _age_exit_code(message):
    """NO-DATA degradations exit 2 (an environment problem); an actual age
    failure -- wrong recipient, wrong identity, corrupt ciphertext age itself
    rejects -- exits 1, a real refusal rather than a missing capability."""
    return 2 if message and message.startswith("NO-DATA") else 1


# --------------------------------------------------------------- selection

def select_notes(vault, ids_mod, notes, since):
    """[relpath, ...] for the requested selection. `notes` resolves each id
    through ids_mod.resolve (no filename fallback: an unresolved id is simply
    not selected, named in the returned missing list). `since` walks every
    note and keeps one whose mtime is on or after that date. Exactly one of
    notes/since is expected; the caller's argparse group enforces that."""
    selected = []
    missing = []
    if notes:
        for ident in notes:
            rel = ids_mod.resolve(vault, ident, allow_stem=False)
            if rel is None:
                missing.append(ident)
            else:
                selected.append(rel)
    else:
        threshold = datetime.datetime.combine(since, datetime.time.min).timestamp()
        for path in ids_mod.walk(vault):
            try:
                mtime = os.path.getmtime(path)
            except OSError:  # sbe: allow-silent vault walk skips a file whose mtime it cannot read, same convention as the id/scan walks in bm_vault_provenance.py
                continue
            if mtime >= threshold:
                selected.append(os.path.relpath(path, vault))
    return sorted(set(selected)), missing


def _parse_date(raw):
    try:
        return datetime.date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------- packing

def build_pack(vault, relpaths):
    """The tar bytes of every selected note, arcname-relative to the vault
    root so the receiving side extracts a plain flat set of files rather than
    a path back into the sender's own tree."""
    import io
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel in relpaths:
            tar.add(os.path.join(vault, rel), arcname=rel)
    return buf.getvalue()


def _safe_extract(tar, dest):
    """Extracts every member of `tar` into `dest`, refusing any member whose
    name is absolute or escapes dest via `..` -- a tar from an untrusted relay
    must never be allowed to write outside the extraction directory. Returns
    the list of extracted file paths."""
    out = []
    for member in tar.getmembers():
        if not member.isfile():
            continue
        name = member.name
        if os.path.isabs(name) or name.startswith(".." ) or "/../" in ("/" + name):
            raise ValueError("REFUSE: bundle member %r escapes the extraction directory" % name)
        target = os.path.normpath(os.path.join(dest, name))
        if not (target == dest or target.startswith(dest + os.sep)):
            raise ValueError("REFUSE: bundle member %r escapes the extraction directory" % name)
        os.makedirs(os.path.dirname(target) or dest, exist_ok=True)
        fh = tar.extractfile(member)
        if fh is None:
            continue
        with open(target, "wb") as out_fh:
            out_fh.write(fh.read())
        out.append(target)
    return out


# --------------------------------------------------------------- export

def cmd_export(args):
    vault = args.vault
    if not vault or not os.path.isdir(vault):
        print("bm_vault_exchange: NO-DATA, no readable vault at %r" % vault, file=sys.stderr)
        return 2
    ids_mod = _load_sibling("bm_vault_ids")
    if ids_mod is None:
        print("bm_vault_exchange: NO-DATA, bm_vault_ids.py not found or not importable",
              file=sys.stderr)
        return 2

    since = None
    if args.since:
        since = _parse_date(args.since)
        if since is None:
            print("bm_vault_exchange: --since needs YYYY-MM-DD, got %r" % args.since,
                  file=sys.stderr)
            return 2

    relpaths, missing = select_notes(vault, ids_mod, args.notes, since)
    if missing:
        print("bm_vault_exchange: NO-DATA, %d note id(s) did not resolve: %s"
              % (len(missing), ", ".join(missing)), file=sys.stderr)
        return 2
    if not relpaths:
        print("bm_vault_exchange: REJECT export, no notes matched the selection", file=sys.stderr)
        return 1

    data = build_pack(vault, relpaths)
    bundle_id = "xchg-" + uuid.uuid4().hex[:16]
    os.makedirs(args.out, exist_ok=True)
    ciphertext_name = "%s.age" % bundle_id
    ciphertext_path = os.path.join(args.out, ciphertext_name)

    ok, err = age_encrypt(args.recipient, data, ciphertext_path)
    if not ok:
        print("bm_vault_exchange: %s" % err, file=sys.stderr)
        return _age_exit_code(err)

    manifest = {
        "bundle_id": bundle_id,
        "created_at": _now_iso(),
        "count": len(relpaths),
        "sha256_ciphertext": _sha256_file(ciphertext_path),
        "ciphertext_file": ciphertext_name,
    }
    manifest["sha256_manifest"] = _manifest_integrity_hash(manifest)
    manifest_path = os.path.join(args.out, "%s.manifest.json" % bundle_id)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    print("EXPORTED bundle=%s notes=%d ciphertext=%s manifest=%s"
          % (bundle_id, len(relpaths), ciphertext_path, manifest_path))
    return 0


# --------------------------------------------------------------- import

def cmd_import(args):
    vault = args.vault
    if not vault or not os.path.isdir(vault):
        print("bm_vault_exchange: NO-DATA, no readable vault at %r" % vault, file=sys.stderr)
        return 2
    if looks_like_literal_age_key(args.identity):
        print("bm_vault_exchange: REFUSE, --identity looks like a literal age secret key, "
              "not a file path. Save it to a file and pass the file path instead "
              "(age-keygen -o identity.txt writes one); this keeps key material out of "
              "argv and shell history.", file=sys.stderr)
        return 2
    if not os.path.isfile(args.identity):
        print("bm_vault_exchange: NO-DATA, identity keyfile not found at %r" % args.identity,
              file=sys.stderr)
        return 2
    if not os.path.isfile(args.bundle):
        print("bm_vault_exchange: NO-DATA, bundle not found at %r" % args.bundle, file=sys.stderr)
        return 2

    stem = args.bundle[:-4] if args.bundle.endswith(".age") else args.bundle
    manifest_path = stem + ".manifest.json"
    if not os.path.isfile(manifest_path):
        print("bm_vault_exchange: NO-DATA, manifest stub not found at %r" % manifest_path,
              file=sys.stderr)
        return 2
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        print("bm_vault_exchange: NO-DATA, manifest unreadable (%s)" % exc, file=sys.stderr)
        return 2
    expected = manifest.get("sha256_ciphertext")
    if not expected:
        print("bm_vault_exchange: NO-DATA, manifest carries no sha256_ciphertext field",
              file=sys.stderr)
        return 2

    # class=malformed-manifest, refused before any decrypt: bundle_id must
    # match the exact shape cmd_export mints. An unauthenticated manifest
    # field cannot be trusted otherwise, and this one flows straight into a
    # raw frontmatter line in bm_vault_intake._build_note (provenance_source)
    # -- a newline-bearing bundle_id would inject arbitrary YAML keys there.
    bundle_id = manifest.get("bundle_id")
    if not bundle_id or not BUNDLE_ID_RE.match(bundle_id):
        print("bm_vault_exchange: REJECT import, class=malformed-manifest "
              "(bundle_id %r does not match the expected xchg-<16 hex> shape)"
              % bundle_id, file=sys.stderr)
        return 1

    # class=manifest-tamper, also before any decrypt: sha256_ciphertext alone
    # only authenticates the ciphertext bytes, leaving bundle_id/created_at/
    # count free to be edited in transit without tripping that check. Fold
    # them into one recorded hash so any of those fields changing refuses
    # the same way a flipped ciphertext byte already does.
    expected_manifest_hash = manifest.get("sha256_manifest")
    if not expected_manifest_hash:
        print("bm_vault_exchange: NO-DATA, manifest carries no sha256_manifest field",
              file=sys.stderr)
        return 2
    if _manifest_integrity_hash(manifest) != expected_manifest_hash:
        print("bm_vault_exchange: REJECT import, class=manifest-tamper "
              "(manifest fields do not match the recorded sha256_manifest); "
              "refusing to decrypt", file=sys.stderr)
        return 1

    actual = _sha256_file(args.bundle)
    if actual != expected:
        print("bm_vault_exchange: REJECT import, class=ciphertext-tamper "
              "(expected sha256=%s, got=%s); refusing to decrypt" % (expected, actual),
              file=sys.stderr)
        return 1

    intake_mod = _load_sibling("bm_vault_intake")
    if intake_mod is None:
        print("bm_vault_exchange: NO-DATA, bm_vault_intake.py not found or not importable; "
              "refusing to admit without the front door", file=sys.stderr)
        return 2

    workdir = tempfile.mkdtemp(prefix="bm-vault-exchange-")
    # Best-effort: `finally` below cleans up on any normal exit or exception;
    # this atexit registration also fires on a hard kill (SIGTERM/os._exit
    # skips finally too, but a normal process-exit atexit chain still runs
    # this). Neither covers SIGKILL or a power loss, so a hard-killed run can
    # still leave decrypted plaintext under the OS temp dir until the next
    # reboot's own temp cleanup; that is a stated limit, not a guarantee.
    atexit.register(shutil.rmtree, workdir, ignore_errors=True)
    try:
        tar_path = os.path.join(workdir, "pack.tar")
        ok, err = age_decrypt(args.identity, args.bundle, tar_path)
        if not ok:
            print("bm_vault_exchange: %s" % err, file=sys.stderr)
            return _age_exit_code(err)

        extract_dir = os.path.join(workdir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            with tarfile.open(tar_path, mode="r") as tar:
                extracted = _safe_extract(tar, extract_dir)
        except (tarfile.TarError, ValueError) as exc:
            print("bm_vault_exchange: REJECT import, %s" % exc, file=sys.stderr)
            return 1
        if not extracted:
            print("bm_vault_exchange: REJECT import, bundle carried no files", file=sys.stderr)
            return 1

        admit_args = argparse.Namespace(
            vault=vault,
            source="exchange:%s" % bundle_id,
            by=args.by,
            restricted=args.restricted,
            deny_list=args.deny_list,
            locale=None,
            as_of=None,
            encoding=None,
            files=extracted,
        )
        rc = intake_mod.cmd_admit(admit_args)
        print("IMPORTED bundle=%s from=%s" % (bundle_id, args.bundle))
        return rc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------- CLI

def _build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    pe = sub.add_parser("export", help="pack and encrypt notes into a relay directory")
    pe.add_argument("--vault", required=True)
    pe.add_argument("--recipient", required=True, action="append",
                     help="an age recipient public key; repeatable")
    pe.add_argument("--out", required=True, help="the relay directory to write the bundle into")
    sel = pe.add_mutually_exclusive_group(required=True)
    sel.add_argument("--notes", nargs="+", help="note ids to export")
    sel.add_argument("--since", help="export every note modified on or after this YYYY-MM-DD")

    pi = sub.add_parser("import", help="verify, decrypt and admit a relay bundle")
    pi.add_argument("--vault", required=True)
    pi.add_argument("--identity", required=True, help="path to an age identity keyfile")
    pi.add_argument("--bundle", required=True, help="path to the bundle's .age ciphertext file")
    pi.add_argument("--by", default=None, help="the receiving actor; NO-DATA if omitted")
    pi.add_argument("--restricted", action="store_true", help="quarantine every admitted note")
    pi.add_argument("--deny-list", default=None, help="path to a deny-list terms file")
    return p


def main(argv=None):
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return cmd_export(args) if args.command == "export" else cmd_import(args)


if __name__ == "__main__":
    sys.exit(main())
