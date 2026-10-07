# U4 Migration Plan: Absorb BrotherSBE into `brother.assurance`

**Unit:** U4
**Target release:** Brother 1.1.0
**Precedent:** U3 (Brother Mode): thin runtime-resolving facade over the original code, parity test suite, original left untouched until a later unit retires it.
**Status:** Planning. Nothing here is executed yet. Every claim below is marked **CONFIRMED** (read directly, quoted above) or **INFERRED** (reasoned from the confirmed text but not itself read).

---

## 0. Ground truth used by this plan

**CONFIRMED, read directly.** `products/brothersbe/tools/sbe_checks.py` is the "registry contract every BrotherSBE check is registered under." Its docstring states:

- A check is "a function PLUS a mechanical declaration of what it reads, what its empty states are, and a worked positive example of its evidence."
- The declaration fields are, verbatim: `reads, kind, item_key, empty_expect, full_fixture, full_expect, full_expect_reason, optional_leaves`.
- `empty_expect` can never be `PASS`; "The constructor refuses it."
- The declaration is enforced by the constructor; the runtime is tested by `evals/test_no_data_class.py` "over the registries it discovers and the scenarios it derives."
- The motivating defect: "A check that reports PASS over evidence it never examined converts absence into false assurance." Six named instances fixed by hand, four more found alive in the same files.

**CONFIRMED, structure.** `products/brothersbe/` has 20+ `sbe_*.py` files under `tools/`, plus top-level `contracts/`, `evals/`, `evidence/`, `program/`, `src/`, `tables/`, `templates/`. There is no single dominant source file. The floor plan is many small modules, not one large store.

**NOT CONFIRMED, and no plan below should pretend otherwise.** The exact Python names in `sbe_checks.py`: the constructor's name, the registration decorator (if any), the registry container type, the error raised on invalid registration, and whether `VACUOUS_VALUES` and `answered()` exist at all. The quoted docstring does **not** contain those two identifiers. See §2 and §5 for the specific reads that would close this gap.

---

## 1. Recommended first slice

**Recommendation: (a): start with `sbe_checks.py` alone as the PASS/FAIL/NO-DATA vocabulary anchor.**

Reasoning:

1. **The docstring describes one file as the contract every check is registered under.** That is a single, identified chokepoint. It is structurally the *right* anchor even though the surrounding domain is distributed, because the distributed surface is downstream of it: every other `sbe_*.py` check, in order to comply, must be registered through this contract. Fixing the contract and proving parity on it is the highest-leverage first move.

2. **It is the only file we can point at and say "the defect this whole project exists to prevent lives here first."** The "absence into false assurance" failure is *defined* in this file. Brother 1.1.0's job is to make that failure structurally impossible in the merged product. Beginning anywhere else starts assimilation at the edges instead of the root.

3. **It matches U3's discipline in shape, not in scale.** U3 facaded the one big file because it was the risk center. Here the risk center is the small contract file, not because it holds most lines but because it holds the enforcement. The facade pattern is unchanged.

**Honest caveat: where (a) is weaker than U3.** `sbe_checks.py` is plausibly a *small* module (vocabulary constants, a declaration dataclass, a constructor, an error). A facade over a small vocabulary module has little runtime behavior to hide behind importlib. That is fine: the parity test still has real work (does the facade's constructor refuse `empty_expect=PASS` identically? does the registry the facade exposes enumerate the same set?): but it is not the same risk profile as `bm_store.py`. Do not oversell it as the same.

**What would change my mind.** One read: `contracts/`. **CONFIRMED** that `contracts/` exists at the BrotherSBE top level. If `contracts/` contains the machine-readable spec of the PASS/FAIL/NO-DATA contract and `sbe_checks.py` merely *implements* it, then the true anchor is the contract artifact, and the first slice should facade the loader/discovery path that binds `contracts/` to `sbe_checks.py`: not `sbe_checks.py` in isolation. The second read that would change my mind: if `sbe_checks.py` contains no runtime enforcement at all and the constructor the docstring describes actually lives in a sibling file, then (a) is aimed at the wrong module. Both reads are cheap and should happen before Group 0 starts.

**Why not (b): a different single file.** No other file is named by the docstring as a contract. Picking one from the `sbe_*.py` list on vibes would violate the U3 discipline of starting from *identified* risk.

**Why not (c): a genuinely different strategy.** A manifest-driven facade that tracks many small modules at once is the right *eventual* shape (§5 gestures at it), but it is not the right *first* shape. Building the multi-module tracker before proving parity on one module means the tracker itself is unproven when it matters most. Prove the pattern on one, then scale the pattern.

---

## 2. Minimum re-export surface for `brother.assurance`

Below, each name is tagged **STATED** (appears in the quoted docstring) or **INFERRED** (reasoned from the docstring; name is unconfirmed and may differ).

### 2a. Names stated directly by the docstring

| Re-export | Basis | Note |
|---|---|---|
| The three state tokens `PASS`, `FAIL`, `NO-DATA` | STATED | The docstring names the vocabulary by these tokens. Whether they are string constants, an enum, or sentinel objects is unconfirmed. |
| The declaration fields `reads`, `kind`, `item_key`, `empty_expect`, `full_fixture`, `full_expect`, `full_expect_reason`, `optional_leaves` | STATED | Named verbatim as the "mechanical declaration." These are presumably fields on the registration record; do not assume they are module-level names. |
| The constructor that refuses `empty_expect=PASS` | STATED as behavior, name unconfirmed | The docstring: "The constructor refuses it." Re-export whatever callable performs that refusal. |
| A registration entry point | STATED as concept | "the registry contract every check is registered under" implies a callable or decorator that performs registration. Name unconfirmed. |
| The registry itself (the container) | STATED as concept | "the registries it discovers" (from `test_no_data_class.py`) implies one or more registry containers exist. Shape (module dict, class attribute, plugin list) unconfirmed. |

### 2b. Names the question invites me to consider

**INFERRED: not in the quoted docstring.**

- `VACUOUS_VALUES`: plausibly a closed set of literals (`""`, `None`, `[]`, `{}`, maybe `"N/A"`) that, if present in evidence, must map to `NO-DATA` rather than `PASS`. This is *exactly* the mechanism that would make "absence into false assurance" mechanically impossible, but I have not read it. Do not present it as confirmed.
- `answered()`: plausibly a predicate on a declaration or a check result answering "was this evidence actually examined?" The docstring's phrase "evidence it never examined" implies such a predicate must exist somewhere; whether it is called `answered()` is unconfirmed.

### 2c. Where I would stop guessing

To firm up §2 before Group 0, I need to read, in this order:

1. `products/brothersbe/tools/sbe_checks.py`: the whole file. This resolves every INFERRED name above. Without this, §2 is a scaffold, not a spec.
2. `products/brothersbe/evals/test_no_data_class.py`: this is **CONFIRMED to exist** by the docstring's own reference. It shows how registries are *discovered* (attribute scan? import? explicit list?) and how scenarios are *derived*. That discovery mechanism dictates what the facade must preserve: module identity, attribute names, or an explicit registry handoff.
3. `products/brothersbe/contracts/`: **CONFIRMED to exist as a directory**. If it holds a spec artifact, that spec is the re-export contract; `sbe_checks.py` is its implementation.

Until (1) is read, do not write the facade. Write the *plan to read it* into Group 0's first task.

---

## 3. Relationship to Core's `Evidence`

**Question:** is `sbe_checks.py`'s PASS/FAIL/NO-DATA vocabulary and Core's `Evidence.status` closed set the same vocabulary that needs reconciling, or two independent implementations to bridge?

**Answer: two independent implementations of the same idea. Bridge, do not merge.**

Reasoning:

1. **Same intent, different provenance.** Core's `Evidence` is a **CONFIRMED** frozen dataclass enforcing `PASS`/`FAIL`/`NO-DATA` as a closed set: that is the Core-side statement of the same invariant ("absence is not success"). `sbe_checks.py`'s vocabulary is the BrotherSBE-side statement of it, arrived at independently (the docstring's "six fixed by hand, four found alive" is evidence of independent discovery of the defect). Two teams converging on the same three tokens is a *good sign*, not a duplication to collapse.

2. **Different shapes, so "merge" is not even well-defined.** Core's `Evidence` is a value object produced by a run: it has a status and a payload. A registration declaration in `sbe_checks.py` is *metadata about a check*, not a result: it is a contract about what a future run must read and what its empty state must be. Collapsing them would drag check-registration metadata into per-run evidence, or drag per-run evidence into the registry. **INFERRED**, based on the docstring's list of declaration fields: `reads`, `kind`, `item_key`, `empty_expect`, `full_fixture`, `full_expect`, `full_expect_reason`, `optional_leaves`: none of these look like fields of a run result; they look like fields of a declaration.

3. **The retirement clause forbids the merge anyway.** U3's discipline, restated: do not touch the original until a later unit retires it. Merging `sbe_checks.py`'s vocabulary into Core's `Evidence` *is* touching the original. It also changes behavior of every line of code in BrotherSBE that compares against `"PASS"` (or whatever the token is) directly.

4. **The bridge is the facade.** Concretely: `brother.assurance` should expose a small adapter that converts a `sbe_checks` result into a Core `Evidence` and vice versa, using the shared three-token vocabulary as the intersection. The adapter's only job is to prove the two vocabularies coincide on the surface the facade exposes. When BrotherSBE is later retired, that adapter is where the merge happens: or where the decision *not* to merge is recorded.

5. **One risk the bridge must guard against:** the three tokens must be **identity-equal**, not string-equal, if either side uses sentinels or enums. If Core's `Evidence.status` is a Python `Enum` and `sbe_checks`' `PASS` is a `str`, then `Evidence.status.PASS == "PASS"` may be false by default. The parity test in Group 0 must pin this: see §5.

---

## 4. Three risks specific to many distributed files

U3's single-file experience would not have surfaced these.

### Risk 1: Facade coverage drift

With one facade and 20+ small files, the facade must re-export from many modules. There is no single "did we cover it?" test. A file can be absorbed, then a sibling can be added to `tools/` later without the facade noticing, and the parity suite will keep passing while the surface is silently under-covered. **Mitigation:** the facade must enumerate the modules it wraps from an explicit manifest, and a test must assert the manifest matches what `ls products/brothersbe/tools/` actually contains. Drift is detected by set difference, not by inspection.

### Risk 2: Cross-file coupling inside `tools/` that a single-file facade cannot see

In U3, `bm_store.py` was the coupling. Here, the coupling is *between* `sbe_*.py` files. **CONFIRMED** that the file list includes `sbe_decide.py`, `sbe_decision_record.py`, and `sbe_decision_verify.py`. **INFERRED** (from names alone: not read): these three likely form a chain, and `sbe_dispatch.py` is likely an entry point that routes to several of them. A facade over `sbe_checks.py` alone will report green while the *other* files keep importing `sbe_checks` via its original path. If those imports ever need to resolve through the facade, the facade must sit at the import path, not just at the API surface. This is a design decision the facade's first commit must resolve explicitly: **module-identity facade** (rewrite `sys.modules`) versus **API-surface facade** (re-export names). U3's file was imported by few, so this choice did not matter. Here it does.

### Risk 3: Registry discovery through the facade

**CONFIRMED** by the docstring: `evals/test_no_data_class.py` operates "over the registries it discovers." Discovery implies import-scanning of the `products/brothersbe/tools/` directory. If the facade re-exports registered checks under `brother.assurance.*` module paths, the discovery scan may either (i) miss those checks because they no longer live at their original paths, or (ii) double-count them because they now live at both. Either failure is silent and both are catastrophic for the exact invariant this project exists to protect: a check that the runtime *thinks* is registered but that discovery does not see is the same absence-into-assurance defect in a new coat. **Mitigation:** the Group 0 parity test must assert the discovered-registry set is *exactly equal*, not merely non-empty.

---

## 5. Bounded first real deliverable for U4

Analogous to U3's Group 0 (the loader): the smallest slice that proves the pattern works before committing to absorbing all 20+ tools.

### U4 / Group 0: "The vocabulary anchor"

**Scope, in order.**

1. **Read, do not yet write.** Read `products/brothersbe/tools/sbe_checks.py` in full, and `products/brothersbe/evals/test_no_data_class.py` in full. Both are **CONFIRMED to exist**. Confirm the actual names behind §2's INFERRED entries and record them. If §1's mind-change condition (a spec artifact in `contracts/`) fires, stop and re-scope.
2. **Write `brother/assurance/sbe_checks_facade.py`.** A module that runtime-resolves the original `sbe_checks.py` via `importlib`, re-exports §2a's names, and exposes the original registry by identity (not by copy). Original file untouched.
3. **Write `brother/assurance/evidence_bridge.py`.** The adapter from §3: `sbe_checks` PASS/FAIL/NO-DATA ↔ Core `Evidence.status`. Small. One direction and back.
4. **Write the parity test suite.** Five assertions:
   - **Identity.** The facade's `PASS`/`FAIL`/`NO-DATA` are the *same objects* as the original's: `is`, not `==`.
   - **Refusal.** Registering a check with `empty_expect=PASS` raises identically through the facade and through the original, with the same exception type.
   - **Registry parity.** The set of registrations discoverable through the facade equals the set discoverable through the original. Exact set equality, not superset.
   - **Bridge round-trip.** For a fixture check, `bridge_to_evidence(bridge_from_evidence(e)) == e` for each of the three states.
   - **Fixture hollowing.** `full_fixture` hollowed to the empty state yields the declared `empty_expect`, and that declared value is never `PASS`: exercising the docstring's "worked positive example of its evidence."
5. **Do not** absorb any other `sbe_*.py` file. Do not touch `contracts/`, `evals/`, or the original `sbe_checks.py`. Do not extend the manifest.
6. **Definition of done.** The five assertions pass; the original file's SHA256 is unchanged (there is a **CONFIRMED** `CHECKSUMS.sha256` at the BrotherSBE top level: use it to prove non-mutation); the facade's module path is documented as *not yet* a drop-in for `sbe_checks`' import path (that decision is deferred to Group 1, where Risk 2 is resolved).

### What Group 0 deliberately does not prove

Group 0 proves the facade pattern survives contact with *one* small contract file. It does **not** prove the pattern scales to 20+ files (Risk 1), does not resolve the module-identity question (Risk 2), and does not prove discovery survives the facade (Risk 3). Those are Group 1's problem, and they should be scheduled only after Group 0's parity suite is green, because Group 0 is what produces the fixture vocabulary Group 1 will need.

### Reads required before Group 0 starts, restated

- `products/brothersbe/tools/sbe_checks.py`: resolves every INFERRED name in §2.
- `products/brothersbe/evals/test_no_data_class.py`: resolves the discovery mechanism for Risk 3 and the scenario-derivation for the fixture-hollowing assertion.
- `products/brothersbe/contracts/`: resolves §1's mind-change condition.

If any of these three is larger or more entangled than expected, that is itself a Group 0 finding and re-scoping is the correct response. Do not paper over it.
