# Brother verification receipts (experimental v0.1)

Status: EXPERIMENTAL, v0.1. This is not a standards track document. The
wire format may change before v1 and nothing here should be treated as
stable until then.

A Brother verification receipt is a portable, signed, claim level
attestation. It binds four things together:

1. A single claim, written as a short string.
2. A verdict for that claim, one of `PASS`, `FAIL` or `NO-DATA`.
3. The list of evidence items that were inspected when the verdict was
   reached.
4. The exact bytes of the artifacts the claim is about.

The receipt is an in-toto Statement v1 wrapped in a DSSE envelope and
signed with `ssh-keygen -Y sign`. A third party can verify it offline
with only `ssh-keygen`, the envelope and the artifacts, without any
network access and without trusting the tool that produced it.

`NO-DATA` is never a pass. It means the evidence did not discriminate
and the verdict is unknown.

## Statement shape

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {"name": "build.tar", "digest": {"sha256": "<hex>"}}
  ],
  "predicateType": "https://github.com/khalilmaaouni/Brother/blob/main/docs/reference/attestation.md#verification-v0.1",
  "predicate": {
    "claim": "the build is reproducible",
    "verdict": "PASS",
    "obligation": "REQUIRED_FOR_MERGE",
    "evidence": [
      {
        "check": "rebuild",
        "family": "build",
        "independence": "cross-derived",
        "discriminated": "yes",
        "command": "make build",
        "exit_code": 0
      }
    ],
    "source": {
      "repository": "https://github.com/khalilmaaouni/Brother",
      "commit": "0000000000000000000000000000000000000000"
    },
    "produced_by": {
      "tool": "brother receipt_attest",
      "version": "0.1.0"
    },
    "decided_at": "2024-01-01T00:00:00Z",
    "previous_statement_digest": null,
    "related_predicates": [
      "https://in-toto.io/attestation/human-review/v0.1"
    ]
  }
}
```

## Predicate fields

Every field below is required.

- `claim` (string): a single sentence stating what was checked.
- `verdict` (string, enum): one of `PASS`, `FAIL`, `NO-DATA`. `NO-DATA`
  is never a pass.
- `obligation` (string, enum): one of `OPTIONAL`,
  `REQUIRED_FOR_MERGE`, `REQUIRED_FOR_RELEASE`.
- `evidence` (list of objects): each item has:
  - `check` (string): a short name for the check.
  - `family` (string): the broad family the check belongs to
    (for example `build`, `test`, `lint`).
  - `independence` (string, enum): one of `self-authored`,
    `cross-derived`, `human-specified`, `external`, `unverified`.
  - `discriminated` (string, enum): one of `yes`, `no`, `NO-DATA`.
  - `command` (string): the exact command that produced the evidence.
  - `exit_code` (integer): its observed exit code.
- `source` (object):
  - `repository` (string): repository URL or identifier.
  - `commit` (string): exactly 40 lowercase hex characters.
- `produced_by` (object):
  - `tool` (string): must be the literal `brother receipt_attest`.
  - `version` (string): the tool version that produced the receipt.
- `decided_at` (string): RFC 3339 UTC timestamp, ending in `Z`.
- `previous_statement_digest` (string or null): sha256 hex of the
  previous statement's raw bytes when this receipt continues a chain,
  or `null` for the first statement in the chain.
- `related_predicates` (list of strings): predicate type URIs this
  receipt can sit beside. The default is
  `["https://in-toto.io/attestation/human-review/v0.1"]`.

## DSSE envelope

```json
{
  "payloadType": "application/vnd.in-toto+json",
  "payload": "<base64 of the statement bytes>",
  "signatures": [
    {"keyid": "ssh:<fingerprint>", "sig": "<base64 of armored SSH signature>"}
  ]
}
```

The signed bytes are the DSSE v1 Pre-Authentication Encoding:

```
PAE(type, body) = b"DSSEv1" SP LEN(type) SP type SP LEN(body) SP body
```

where `SP` is one space byte and `LEN` is the ASCII decimal byte
length of the argument.

## Signature and keyid convention

SSH signatures are not a standard DSSE signer, so this specification
defines a documented convention on top of the DSSE envelope:

- Signing runs
  `ssh-keygen -Y sign -f KEYFILE -n brother-receipt-v1 FILE` where
  `FILE` holds the PAE bytes. The result is the SSH armored signature
  text that `ssh-keygen` writes to `FILE.sig`.
- The envelope's `sig` field holds the base64 of that armored
  signature text. This is not the same as base64 of the raw signature
  bytes; it is base64 of the ASCII armor.
- The `keyid` field is `"ssh:"` followed by the SHA256 fingerprint
  printed by `ssh-keygen -l -E sha256 -f PUBKEY` (the second whitespace
  separated field). For example:
  `"ssh:SHA256:abc..."`.

The reason this convention exists is portability. `ssh-keygen` is
available by default on every modern Unix-like system, it does not
require a network round trip, and it does not need a PKI or a
transparency log. The cost is that the combined envelope is a Brother
specific convention rather than a stock DSSE envelope that a
Sigstore or GitHub tool could verify out of the box.

## How a third party verifies

A third party needs three files: the envelope, an `allowed_signers`
file listing the principals and public keys it trusts, and the raw
artifacts. With `ssh-keygen` installed, verification is based on
reconstructing two temporary files and running one `ssh-keygen`
command.

1. Rebuild the PAE bytes from the envelope's `payload` field and write
   them to `pae.bin`.
2. Base64 decode the first entry of `signatures[0].sig` and write the
   resulting armored text to `sig.txt`.
3. Run:

   ```
   ssh-keygen -Y verify \
     -f allowed_signers \
     -I IDENTITY \
     -n brother-receipt-v1 \
     -s sig.txt < pae.bin
   ```

   The line for `IDENTITY` in `allowed_signers` is
   `principal keytype base64key`. A return code of `0` from
   `ssh-keygen` means the receipt is authentic for that principal.

For convenience, the same check plus the artifact and chain checks can
be run with:

```
python3 scripts/receipt_attest.py verify \
  --envelope envelope.json \
  --allowed-signers allowed_signers \
  --identity ci@example.com \
  --artifact build.tar \
  --previous previous_statement.json
```

`--artifact` may be repeated. `--previous` is optional; when present,
the tool checks that the statement's `previous_statement_digest`
matches the sha256 of the file's raw bytes.

The tool prints one `PASS` or `FAIL` line per check, then a
`claim verdict` line, then a final `verify: PASS` or
`verify: FAIL (<reasons>)` line. Exit codes:

- `0`: every check passed.
- `1`: one or more checks failed.
- `2` (`NO-DATA`): `ssh-keygen` is not installed, or the envelope
  cannot be parsed. This is not a `PASS`.

A predicate verdict of `NO-DATA` or `FAIL` does not make the signature
check fail. The signature proves who asserted the verdict, not that the
verdict is `PASS`.

## What it proves

A successfully verified receipt proves:

- which principal signed the statement, using `ssh-keygen` and the
  `allowed_signers` file the verifier chose;
- that the signature covers exactly the bytes of the statement;
- which claim and verdict that principal asserted;
- which evidence list and which source commit that principal listed;
- that the artifacts the verifier was given hash to the exact digests
  listed under `subject`;
- if `--previous` is supplied, that the receipt continues a chain
  whose previous statement is the one the verifier supplied.

## What it does not prove

A verified receipt does not prove:

- that the claim is true beyond the evidence listed. It only proves
  who said so and about which bytes;
- that the evidence is complete, correct, or that the verdict was
  reached honestly;
- compliance with any regulation, standard, or internal policy;
- interoperability with Sigstore, GitHub attestations, or any other
  transparency log or DSSE signer, since the signer here is
  `ssh-keygen` and the keyid convention is Brother specific.

## Relationship to other predicates

A Brother verification receipt is intended to sit beside other
predicates in the same bundle. In particular it is designed to
co-exist with:

- the in-toto `https://in-toto.io/attestation/human-review/v0.1`
  predicate, which captures a human review of the same subject;
- a proposed agent-decision predicate (URI not yet fixed) that
  captures the decision an automated agent took about the same
  subject.

Because the receipt names the predicate types it can sit beside in
`related_predicates`, downstream tooling can group them without
guessing.

## Stability

This is v0.1 and is experimental. Field names, the enum vocabularies,
the keyid convention and the exact set of checks performed by
`verify` may all change before v1. Until v1 is declared, do not build
long lived automation that assumes these bytes are frozen.
