# Native mobile workflow

These support tools run within the existing Brother execution route. They
work from a checkout or the shipped runtime; no additional public command,
MCP server, paid service or model call is required. Python 3.9+ on macOS and
Xcode are needed for the native runner. The local board helper needs Python;
its media probe additionally needs an available ffprobe.

## Reference and simulator run

First collect a device application observation JSON and a release record that
associates its version/build with source. Use the retained archive app, not a
random product found in DerivedData. The clean reference checkout must equal
the full source revision supplied below. Evidence goes outside the repository.

```sh
python3 scripts/mobile_workflow.py doctor --repo /path/to/app --out /path/to/evidence/doctor.json
python3 scripts/mobile_workflow.py reference --repo /path/to/app --app /path/to/archive/Products/Applications/App.app --observation /path/to/installed-apps.json --mapping /path/to/release-record.md --source-revision FULL_40_CHARACTER_SHA --out /path/to/evidence/reference.json
python3 scripts/mobile_workflow.py check-reference --repo /path/to/app --reference /path/to/evidence/reference.json
python3 scripts/mobile_workflow.py run --repo /path/to/app --profile /path/to/profile.json --reference /path/to/evidence/reference.json --out /path/to/evidence/fresh-run
```

The observation contains exactly one object with matching bundleIdentifier,
version and bundleVersion, such as devicectl's installed-app output. A supplied
release record is hashed and explicitly described as a recorded association.
It does not become an embedded source attestation. A candidate can descend
from the reference but still change its behavior; test that behavior.

Profile example, replace every project-specific value and exact test URI:

```json
{
  "schema": "brother-mobile-profile-v1",
  "project": "App.xcodeproj",
  "scheme": "App",
  "target": "App",
  "configuration": "Debug",
  "simulator_id": "00000000-0000-0000-0000-000000000000",
  "bundle_id": "org.example.App",
  "version": "1.0",
  "build": "1",
  "expected_tests": ["test://com.apple.xcode/App/AppTests/FlowTests/testReturn"],
  "only_testing": ["AppTests/FlowTests/testReturn"],
  "timeout_seconds": 1800
}
```

Use actual nodeIdentifierURL values from xcresulttool, including the test
target. Optional build_wrapper is a repository-relative executable that
accepts xcodebuild arguments. Use the app's existing serial build gate when
present. Brother also owns a per-user workflow lease; unrelated tools must
honor the app's build gate to share that protection. No DerivedData override
is added. A timed-out native command kills its process group on macOS before
the caller releases the lease.

Optional environment supplies string values for the build and launch. Do not
place secrets in it. The receipt retains key names and a sanitized profile,
not values. Restore required environment values separately when rerunning a
handoff. Optional source_stamp has build_setting and plist_key; it requires
the compiled plist value to equal the candidate SHA. Omitting it reports
embedded source proof as NO-DATA.

The runner executes tests first, resolves the exact target's product through
build settings, checks bundle/version/build, installs that product on the
selected simulator, verifies installed plist and executable hashes, launches,
and captures a PNG. Capture validity is not visual acceptance. The receipt
keeps each command, exit code and log; source and input hashes are checked
again at the end. Test filtering, skipped tests, changed inputs and the wrong
installed app cannot pass. It never installs on a physical phone.

## Research boards and creative media

A board is explicitly curated, not a mirror of a vendor's catalog. Screens
carry stable local IDs, app, title, flow, integer step, elements, source URL,
ISO observed_at date and notes. Optional media is a native_evidence hash record
(path, size, sha256). Missing media remains NO-DATA for visual evidence.
Examples in this document are synthetic, not research observations.

```json
{"schema":"brother-mobile-board-v1","screens":[
 {"id":"example-return","app":"Example","title":"Return to room","flow":"reflection","step":2,"elements":["button"],"source":"https://example.org/reference","observed_at":"2026-09-12","notes":"Synthetic schema example, replace with observed evidence"}
]}
```

```sh
python3 scripts/mobile_design.py search --board /path/to/board.json --flow reflection --element button --out /path/to/research.json
python3 scripts/mobile_design.py brief --board /path/to/board.json --query room --outcome 'Finish a reflection and return' --out /path/to/brief.json
python3 scripts/mobile_design.py media --asset /path/to/clip.mp4 --role marketing --max-bytes 15000000 --max-duration 20 --out /path/to/media.json
```

Search is case-insensitive token matching and exact filters. It retains flow
order and sources. A generated brief leaves design decisions NO-DATA until
they are made and reviewed. Media output records ffprobe metadata, source
hash, command and explicit byte/duration budget checks. It does not certify
licenses, motion quality, accessibility or runtime frame rate. Keep native
interaction recordings separate from promotional renders.

## Handoff and checks

```sh
python3 scripts/mobile_workflow.py pack --run /path/to/evidence/fresh-run --out /path/to/handoff.zip
python3 scripts/mobile_workflow.py verify-pack --archive /path/to/handoff.zip
python3 scripts/test_native_evidence.py -v
python3 scripts/test_mobile_workflow.py -v
python3 scripts/test_mobile_design.py -v
```

The archive includes result bundles, logs, receipt, capture, sanitized profile
and reference record. It rejects linked files and unsafe or duplicate archive
members. Verification checks the inventory and every file hash without
extracting. This is integrity, not an authenticity signature. Original paths
remain in original receipts. To rerun on another computer, check out the source,
rebind available reference evidence and destination, restore required environment
and run a fresh profile. Archive contents are private by default; inspect before
sharing. Visual observation, motion playback, physical audio/haptics, VoiceOver,
store processing and the final human decision each require their own evidence.
