# Evidence families and independence

Senior review needs more than exit code zero. Use only evidence families material to the work and risk.

| Family | Typical question |
| --- | --- |
| Functional | Does the named behavior occur? |
| Contract | Does an API/schema/interface remain compatible? |
| Invariant/property | Does the rule hold across relevant states/inputs? |
| Differential | Does it agree with a trusted independent implementation/baseline? |
| Metamorphic | Does a predictable transformation change/preserve output correctly? |
| Mutation/reversion | Does the check fail when the relevant implementation is removed/broken? |
| Reconciliation | Do independently derived totals/record sets agree? |
| Migration | Are data effects, compatibility, forward/backward paths understood? |
| Dry-run/plan | What would infrastructure/deployment tooling change? |
| Fault/recovery | What happens when a dependency/process/node fails? |
| Performance/resource | Does the change stay inside latency/capacity/cost constraints? |
| Security/privacy | Are permissions, secrets, data handling, and attack surfaces checked? |
| Observability | Can failure be detected and diagnosed after release? |
| Human exploratory | What did a person observe outside scripted assertions? |
| Statistical | Is a data-science claim stable under uncertainty/leakage checks? |

## Independence

A test written by the same model that wrote the implementation can be useful. It is not automatically an independent oracle.

Useful labels/concepts:

- **self-authored**: same agent/system created implementation and check;
- **cross-derived**: expected result comes from a separate contract/requirement/computation;
- **human-specified**: a person supplied the oracle;
- **external**: authoritative external spec/service/benchmark;
- **unverified**: independence unknown.

The goal is not to reject self-authored tests. It is to stop receipts from overstating their epistemic status.
