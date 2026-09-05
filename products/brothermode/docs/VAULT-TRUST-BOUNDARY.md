# The vault trust boundary

Linked from `bm_vault_cli.py doctor`'s agent-rules block. This page states,
plainly, what the vault's access controls actually cover and what they do
not, so nobody reads a policy file, a lock file, or an audit log as a
security boundary it was never built to be.

## The vault is files on disk

`~/Documents/Kay Vault` (or whatever `BM_VAULT_ROOT` / `BROTHERMODE_VAULT`
resolves to) is a folder of plain Markdown files, an Obsidian workspace, and
a SQLite index built from them. That is the whole thing. There is no
database server, no per-file permission bit beyond the operating system's
own, and no gate a reader has to pass through to open a file.

## What bypasses every vault control, completely

Obsidian itself, a shell (`cat`, `grep`, `less`, an editor), any script, or
any process running as the machine's user opens these files exactly like
any other file on disk. None of that traffic goes anywhere near
`bm_vault.py recall`, `bm_vault_policy.py`, `bm_vault_audit.py`, or
`bm_vault_serve.py`. So:

- **The access policy** (`bm_vault_policy.py`, `99-System/access-policy.json`)
  trims what `recall` returns. It does not, and cannot, stop a direct file
  open. A note the policy denies to some identity is still an ordinary
  Markdown file, fully readable by anything that reads files.
- **The access audit** (`bm_vault_audit.py`) records who called `recall`
  and what it served or withheld. It has no visibility into a file opened
  outside `recall`, so it is a log of served-path traffic, never a
  complete access record for the vault.
- **The quarantine and lifecycle rules** (`bm_vault_lifecycle.py` and
  related demotion/supersession logic) change how `recall` ranks or
  labels a note. A quarantined note is still sitting on disk, at its same
  path, fully readable by anything that is not `recall`.
- **The principal registry** (in flight, not yet built) will let `recall`
  attach an authenticated identity to a request instead of the
  client-declared string `bm_vault_audit.py` accepts today. That still
  only strengthens who `recall` believes it is talking to. It changes
  nothing about a direct file read, because a registry lookup is itself
  part of the served path.

None of this is a defect to fix later. A policy engine, an audit log, and a
registry all live at the query layer by construction; asking them to also
police the filesystem is asking the wrong module to do a different job.

A community plugin is the same bypass, running inside the application
instead of beside it. Obsidian loads a community plugin's JavaScript into
its own process, with the same filesystem access Obsidian itself has;
the plugin API does not sandbox a plugin's file reach to a folder, a file
type, or a declared scope. So a plugin sits on the near side of every
control named above: it is not a caller of `recall`, `bm_vault_policy.py`,
or `bm_vault_audit.py`, and none of them can see, trim, or log what a
plugin does with a file it opens directly. `docs/VAULT-PLUGIN-POLICY.md`
treats this as the operating assumption rather than a gap to close: a
community plugin is a full, in-process crossing of this same boundary,
and the policy decides which crossings are worth accepting, not whether
the crossing itself can be narrowed by this repository's tooling.

## What the controls actually bind: the served path

"The served path" means two things, and only two things:

1. **The recall CLI** (`bm_vault.py recall`, and everything that calls it:
   `bm_vault_cli.py recall`, hooks, agents that shell out to it).
2. **The serve layer** (`bm_vault_serve.py`'s HTTP front: `GET /health`,
   `POST /recall`, each also reachable at a versioned `/v1/` prefix per
   VB3-09; the two forms answer identically, the unversioned form marked
   deprecated in its response headers).

Policy trimming, the audit log, authority ranking, and (once it lands) the
principal registry all bind these two entry points. That is a real,
useful boundary: it is what stands between a query and the notes it can
see, and it is honestly a lot of the surface anyone actually deliberately
programs against. It is just not a filesystem boundary, and it was never
meant to be one.

## Single-machine mode: the machine boundary is the trust boundary

Today's deployment is one user, one machine, files under that user's home
directory. In that shape, the real security boundary is the same one that
protects every other file the user owns: OS-level file permissions, disk
encryption, and who can log into the machine at all. The vault's own
tooling adds convenience and query-time shaping on top of that, not a
second, independent wall. Anyone who can read the user's other files can
read the vault. This is expected, not a gap this WBS row exists to close.

## Service mode: scoped, not built

A shared deployment (an enterprise service, more than one person or
process reading the vault under different identities) is a real future
shape, and it needs more than the served path currently has. What it needs,
named so a founder decision can gate it deliberately rather than have it
arrive piecemeal:

- **Transport identity or authentication in front of the serve layer.**
  `bm_vault_serve.py` already refuses to bind a non-localhost interface
  without a token file, and checks every request's bearer token with
  `secrets.compare_digest`. That is the seed of this, not the finished
  thing: a real service needs per-caller identity, not one shared secret
  for every caller.
- **TLS or a tunnel.** `bm_vault_serve.py` (VB8-03) now refuses to bind any
  non-loopback address unless both `--tls-cert` and `--tls-key` are given,
  and wraps the listening socket with `ssl.SSLContext` server-side TLS when
  they are, pinned to a TLSv1.2 floor (`ssl.TLSVersion.TLSv1_2`); that
  closes the plaintext-on-the-wire gap for a bind on the local network. It
  is still not the finished thing: there is no client
  certificate check here (mTLS stays scoped to service mode, below), and a
  real cross-machine hop (a tunnel, a tailnet) beyond this process's own
  listening socket is still the founder's network decision, not this
  module's to make.
- **The access audit, merged.** `bm_vault_audit.py` (VB7-04) already
  records every `recall` call with its principal, served ids, and
  withheld count. This piece exists today.
- **The immutable read-audit trail, merged (V5).** `bm_vault_read_audit.py`
  records one hash-chained line per note actually shown, at
  `bm_vault.LEDGER_PATH`'s own directory, file `bm_vault_read_audit.jsonl`
  (config dir, not `99-System`; see the module docstring for why it
  follows the ledger's location rather than opening a second one). Each
  line chains to the one before it: it carries `prev_hash` (the previous
  line's own `hash`) and `hash` (sha256 over its own fields plus
  `prev_hash`), so `bm_vault_read_audit.py verify <dir>` can prove the log
  has not been edited or trimmed since it was written, naming the first
  broken line rather than merely reporting a clean-looking file. It does
  NOT prove a note was never read outside Brother's own tools: it records
  reads made by the recall hook and `bm_vault recall`, never a note opened
  directly by Obsidian, a shell, or any other program, which is exactly
  the same "served path only" limit the access audit above already has.
- **The approval chain that gates who may act.**
  `docs/VAULT-ENTRA-APPROVAL-CONTRACT.md` (VB10-05) names the sequential
  tiers (requester, owner, optional reviewer, named approver users or
  Entra groups, nested groups explicitly unsupported) a service-mode
  deployment implements against Entra ID, mapped onto promotions,
  registry mutations, and restricted-note access. It is a contract only;
  nothing in it is built until the founder gates service mode.
- **The principal registry, in flight.** Today `bm_vault_audit.py` accepts
  whatever identity string a caller declares, and says so out loud:
  "principal values are client-declared, not authenticated identities."
  Service mode needs that identity to come from something the service
  itself verifies, not from the caller's own claim.
- **A vault filesystem readable only by the service account.** Even with
  every item above in place, the machine boundary above still applies:
  if the vault's files sit somewhere a second user account, a second
  process, or a second identity's shell can open directly, the served-path
  controls are exactly as bypassable as they are today. Service mode only
  closes that gap by narrowing who can read the files at all, at the OS
  or container level, to the service account running the served path.

The principal registry (99-System/principals.json) sits on the same
surface: anything with vault write access can hand-edit its own status
back to active, leaving no recorded act. The consult path treats entries
missing their recorded fields as tamper-suspect and fails them closed,
which detects clumsy edits, not careful ones; real integrity for the
registry arrives only with service mode's write boundary.

**Export encryption (VB8-03).** The interchange boundary in
`tools/bm_vault_events.py` carries its own clause for anything exported
across it: Iceberg-spec-v3 table encryption through a catalog-integrated
KMS, or Parquet modular encryption for a direct (no-catalog) export;
either way the KMS must be FIPS 140-3 validated, and customer-managed keys
(Tri-Secret Secure, Unity Catalog CMK, preview status as of 2026-08-30)
are the enterprise expectation for who holds that key. The vault side
never hand-rolls column or file encryption itself; it hands exported
bytes to the table format and catalog and lets them enforce it.

This is a founder-gated deployment decision, not a checklist a session
works through unasked. Nothing above is built by this page; it is named so
the decision, when it is made, is made with the real shape of the gap in
view.
