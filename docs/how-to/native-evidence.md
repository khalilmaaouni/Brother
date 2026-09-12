# Record native build and test evidence

Use `scripts/native_evidence.py` as a unit's existing `done_check` when a
native build or test needs a reviewable evidence record. It records integrity,
not application quality or a person's acceptance decision.

The command passed after `--command` must create the exact result-bundle path
named by `--result-bundle`. Pick a fresh path that does not already exist. The
tool refuses a reused bundle before it runs the command, which prevents a past
green result from being presented as this candidate's result.

```sh
python3 scripts/native_evidence.py record \
  --repo /path/to/candidate \
  --out /path/to/evidence.json \
  --result-bundle /path/to/fresh-result.xcresult \
  --expected-test 'test://com.apple.xcode/App/AppTests/FlowTests/testFinish' \
  --artifact screenshot=/path/to/finish.png \
  --requirement physical-device='NO-DATA: device not available' \
  --command /path/to/native-test-wrapper test -resultBundlePath /path/to/fresh-result.xcresult
```

Place `--command` last. Do not add an extra `--` after it. The exact expected
test identity is the `nodeIdentifierURL` from Xcode's result JSON. It includes
the target, so a same-named case in another target cannot satisfy the check.
The tool queries the bundle with:

```sh
xcrun xcresulttool get test-results tests --path /path/to/fresh-result.xcresult --compact
```

It records the command and nonempty log, candidate revision and exact dirty
state before and after the command, actual test leaves and count, result-bundle
hash, raw result JSON hash, and hashes for every claimed artifact. Validation
runs the result query again, reads the bundle and artifacts again, and compares
them with the record:

```sh
python3 scripts/native_evidence.py validate --evidence /path/to/evidence.json
```

The validator returns PASS only when the command exited zero, the candidate did
not change, every expected test leaf passed, at least one test leaf executed,
the live query agrees with the saved result, and every claimed artifact is
nonempty with the recorded hash. A wrong filter, zero tests, failed or skipped
tests, a malformed result, stale candidate, or missing, empty, or changed
capture is FAIL.

A capture hash proves that the referenced bytes stayed the same. It does not
prove that the pixels show the claimed screen or moment. Review the capture
against the named journey separately, and keep that visual judgment distinct
from this record-integrity verdict.

Use a requirement row for an evidence category that is required but currently
unavailable, such as a physical device or instrument. A named `NO-DATA` reason
returns exit status 2 and remains visible. It never becomes PASS. Do not use
this tool's PASS as a claim about visual quality, accessibility semantics,
audio or haptics, physical feel, or human acceptance. Record those observations
separately.
