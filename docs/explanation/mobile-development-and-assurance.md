# Mobile development and assurance with Brother

Brother helps a mobile team connect a user journey to a build, a test, and
the evidence a reviewer can inspect. It keeps technical proof separate from
visual judgment, accessibility review, physical device behavior, release
state, and human acceptance.

Each capability below says what is real today.

## Describe the mobile project

This is useful when you inherit a mobile project and need to know which
platform, framework, build system, and test system the files actually support.

This is a planning and recording capability. It does not touch a device.

### How to use it

Run `python3 scripts/mobile_project_profile.py path/to/your-app --project-id your-project`.

### Evidence it produces

It prints a `mobile-project-profile-v1` record with the detected facts, the
revision that was inspected, and repository-relative evidence references.
Unsupported facts stay `NO-DATA`.

### What it will not do

It will not build, test, install, or infer a framework from a project name.

## Define and validate a user journey

A journey contract gives a team a shared description of the user outcome,
entry and exit states, supported device classes, locales, interruptions,
privacy constraints, performance budgets, visual references, required native
tests, and human acceptance items.

This is a contract and validation capability. It does not touch a device.

### How to use it

Run `python3 scripts/mobile_journey_contract.py path/to/journey.json`.

### Evidence it produces

The validator checks `mobile-journey-contract-v1` and also checks that its
outcome contract reference exists and validates as the shared outcome
contract.

### What it will not do

It will not run the journey, prove that a build implements it, or decide
whether a person accepts the result.

## Describe and validate mobile actions

The canonical action vocabulary lets a journey name actions such as opening an
app, tapping a target, entering text, swiping, setting a permission or
location, waiting, asserting state, capturing a screen, finishing, or asking
for a human. A driver-independent record makes the intent reusable.

This is a contract and validation capability. It does not touch a device.

### How to use it

Run `python3 scripts/mobile_canonical_action.py path/to/action.json`.

### Evidence it produces

It checks the action schema and the per-action rules, including required
targets, parameters, ranges, and allowed values.

### What it will not do

It will not select a driver, perform an action, or turn a visual coordinate
into a stable selector.

## Advertise and choose a driver

Driver contracts let the router compare real descriptions of supported
actions, platforms, observations, selector support, visual support, and risk.
The router can then choose the best described driver for one canonical action.

This is a planning and routing capability. The router itself does not touch a
device. The selected adapter may do so.

### How to use it

Validate a driver description with `python3 scripts/mobile_driver_contract.py path/to/driver.json`.

Choose a driver with `python3 scripts/mobile_hybrid_action_router.py --record path/to/action.json --driver-describe path/to/driver-description.json --platform ios`.

Repeat `--driver-describe` for each available driver description.

### Evidence it produces

The contract validator checks the advertised shape and one risk entry per
supported action. The router returns the selected driver or a structural
`NO_DRIVER` or `FAIL` result based only on validated descriptions.

### What it will not do

The router does not call the selected adapter. A caller must make the
adapter-specific call, because native simulator, Appium, and visual adapter
calls take different inputs.

## Turn a journey into work and file ownership

The plan compiler turns a valid journey into materialized work units. The
ownership resolver maps those units to files in the target project. This helps
a developer see what should change before editing source files.

This is a planning capability. It does not touch a device or write project
code.

### How to use it

Compile units with `python3 scripts/mobile_plan_compiler.py path/to/journey.json --profile path/to/profile.json --out path/to/plan.json`.

Resolve files with `python3 scripts/mobile_ownership_resolver.py path/to/plan.json path/to/your-app --profile path/to/profile.json --out path/to/ownership.json`.

### Evidence it produces

The compiler records the journey-derived units and the adapter used. The
resolver records each proposed file resolution and its status.

### What it will not do

It will not create files, patch source, select a device, or prove that the
resolved file is the right product decision.

## Bind an installed build to a reference

The full workflow can bind a retained application archive, an installed-app
observation, a release record, and a full source revision. The reference-lock
tool covers the honest partial case when only installed metadata is known.

This is a recording capability. It reads observations and build files, but it
does not install or launch an app.

### How to use it

For the partial case, run `python3 scripts/mobile_reference_lock.py path/to/installed-apps.json your.bundle.id --capture-method devicectl --out path/to/reference-lock.json`.

For the full case, run `python3 scripts/mobile_workflow.py reference --repo path/to/your-app --app path/to/archive/App.app --observation path/to/installed-apps.json --mapping path/to/release-record.md --source-revision FULL_SOURCE_REVISION --out path/to/reference.json`, then run `python3 scripts/mobile_workflow.py check-reference --repo path/to/your-app --reference path/to/reference.json`.

### Evidence it produces

The partial record separates observed bundle, version, and build metadata from
unresolved source provenance. The full record hashes the app, source state,
observation, and recorded mapping, then checks them again.

### What it will not do

Version and build values alone do not prove source-to-artifact identity. This
capability does not submit, sign, or release a build.

## Run a native simulator workflow

The workflow doctor checks the native command-line tools. The run command
tests first, resolves the intended product, checks identity and hashes,
installs it on the selected simulator, launches it, and captures a PNG. It is
the real simulator path in this tree.

This is a real simulator capability. It needs Python 3.9 or newer, the native
platform build tools, and their simulator and result-bundle tools. It never
installs on a physical phone.

### How to use it

Run `python3 scripts/mobile_workflow.py doctor --repo path/to/your-app --out path/to/doctor.json`.

Run `python3 scripts/mobile_workflow.py run --repo path/to/your-app --profile path/to/profile.json --reference path/to/reference.json --out path/to/fresh-run`.

### Evidence it produces

The run directory contains the command records, exit codes, logs, sanitized
profile, source and input hashes, result bundle, installed identity checks,
and capture. It reports technical `PASS`, `FAIL`, or `NO-DATA`.

### What it will not do

It will not prove visual quality, accessibility, audio, haptics, physical feel,
store processing, or human acceptance. A simulator result is not physical
device evidence.

## Pack and verify native evidence

After a run, the workflow can make a handoff archive and verify its inventory
and hashes without extracting it.

This is an evidence packaging capability. It does not touch a device.

### How to use it

Run `python3 scripts/mobile_workflow.py pack --run path/to/fresh-run --out path/to/handoff.zip`, then run `python3 scripts/mobile_workflow.py verify-pack --archive path/to/handoff.zip`.

### Evidence it produces

The archive contains result bundles, logs, receipts, a capture, a sanitized
profile, and the reference record. Verification checks for unsafe, duplicate,
or linked archive members and rechecks file hashes.

### What it will not do

It is an integrity check, not an authenticity signature. Inspect private
paths and contents before sharing the archive.

## Record an exact native test result

The native evidence recorder binds a fresh result bundle to the exact command,
candidate state, expected test identity, log, and claimed artifacts. The
validator reads the result bundle again, so a typed test count cannot create a
pass.

This is a recording and validation capability. It can cover a simulator or a
physical device only when the evidence contains an explicit physical-device
requirement with a real `PASS`.

### How to use it

Run `python3 scripts/native_evidence.py record --repo path/to/your-app --out path/to/evidence.json --result-bundle path/to/fresh-result.xcresult --expected-test 'test://your-target/your-suite/your-test' --artifact screenshot=path/to/screenshot.png --requirement physical-device='NO-DATA: device not available' --command path/to/native-test-wrapper test -resultBundlePath path/to/fresh-result.xcresult`.

Then run `python3 scripts/native_evidence.py validate --evidence path/to/evidence.json`.

Wrap a valid record with `python3 scripts/native_evidence_v2.py wrap --evidence path/to/evidence.json --out path/to/evidence-v2.json`.

### Evidence it produces

It records the candidate before and after, exact command and exit code, log
hash, fresh result-bundle hash, raw result JSON hash, exact test leaves, and
artifact hashes. Version 2 adds an explicit proof scope and preserved
guarantees.

### What it will not do

It will not judge application quality, accessibility semantics, pixels, audio,
haptics, physical feel, or human acceptance. Missing physical equipment stays
`NO-DATA`.

## Keep a curated design reference and media record

The design tool searches a board curated by your team, creates a brief,
records one screen as design evidence, checks staleness, and inspects media
with an optional `ffprobe` installation. Search is token matching, not a
hosted catalog or model inference.

This is a planning and recording capability. It does not touch a device.

### How to use it

Run `python3 scripts/mobile_design.py search --board path/to/board.json --flow your-flow --element button --out path/to/search.json`.

Run `python3 scripts/mobile_design.py brief --board path/to/board.json --query return --outcome 'Finish the journey and return' --out path/to/brief.json`.

Run `python3 scripts/mobile_design.py evidence --board path/to/board.json --id screen-id --out path/to/design-evidence.json`, then `python3 scripts/mobile_design.py check-staleness --evidence path/to/design-evidence.json --board path/to/board.json --out path/to/staleness.json`.

For media, run `python3 scripts/mobile_design.py media --asset path/to/clip.mp4 --role prototype --max-bytes 15000000 --max-duration 20 --out path/to/media.json`.

### Evidence it produces

Records retain stable screen IDs, sources, observation dates, flow order,
media hashes, and explicit staleness or media budget checks. Missing media is
`NO-DATA`.

### What it will not do

It will not certify licenses, motion quality, accessibility, runtime frame
rate, or a UX decision. Keep interaction recordings separate from promotional
renders.

## Drive supported iOS simulator actions

The native iOS adapter translates supported canonical actions into real
simulator command-line calls. It can launch, open links, set permissions and
location, wait, capture, and record bookkeeping actions. It reports
unsupported actions instead of silently doing nothing.

This is a real simulator capability. It needs the native simulator command
line tools and an exact simulator identifier.

### How to use it

Inspect its contract with `python3 scripts/mobile_native_ios_adapter.py describe`.

Run one action with `python3 scripts/mobile_native_ios_adapter.py run --record path/to/action.json --device SIMULATOR_ID --out path/to/action-evidence`.

### Evidence it produces

It records command stages, outputs, status, and captures when an action asks
for one. The adapter contract lists supported actions and risk classes.

### What it will not do

It does not inject taps, long presses, text, swipes, scrolls, home or back
gestures, rotation, network conditions, or state assertions. Those gaps are
reported as unsupported. It does not control a physical device.

## Use the Appium adapter when its dependencies exist

The Appium adapter can translate supported canonical actions through a real
WebDriver session. It prefers accessibility identifiers and supports other
locators only where its code has a verified mapping.

This is a real device or simulator adapter interface, but it runs only when
the Appium Python package, a reachable Appium server, and a matching installed
platform driver are present. Without them it returns `NO-DATA`.

### How to use it

Inspect its advertised contract with `python3 scripts/mobile_appium_adapter.py describe`.

Run an action with `python3 scripts/mobile_appium_adapter.py run --record path/to/action.json --server http://127.0.0.1:4723 --capabilities path/to/capabilities.json --out path/to/appium-evidence`.

### Evidence it produces

It records action results, session or dependency failures, screenshots, and
stage details under the evidence directory. Unsupported locator or action
reasons stay explicit.

### What it will not do

It will not install the Appium package, start a server, install a platform
driver, or guess support for missing locator strategies. It does not make a
release decision.

## Keep visual fallback honest

The visual fallback adapter defines the interface for grounding a target from
a screenshot and optional semantic tree. Its current implementation makes no
model call and returns `NOT_ATTEMPTED` with no target or confidence. It cannot
promote a pixel location into a stable selector.

This is a contract and adapter interface with no live visual driver behind it.

### How to use it

Inspect the contract with `python3 scripts/mobile_visual_fallback_adapter.py describe`.

Its interface can be exercised with `python3 scripts/mobile_visual_fallback_adapter.py run --record path/to/action.json --screenshot path/to/screenshot.png --tree path/to/tree.json --out path/to/visual-evidence`.

### Evidence it produces

It produces a structural grounding record with `stability` fixed to
`visual_only`, plus status, evidence references, and any failure detail.

### What it will not do

It will not call a vision service, choose a target, assign a real confidence,
write a file, or create a stable selector.

## Observe one screen

The screen observation record describes one inspected screen, its screenshot,
semantic tree, viewport, locale, and targets. It checks that files were really
read and keeps visual-only geometry visibly temporary.

This is a recording and validation capability. It does not perceive, decide,
act, or call a model.

### How to use it

Run `python3 scripts/mobile_screen_observation.py path/to/screen-observation.json --driver-contract path/to/driver.json`.

### Evidence it produces

It validates `mobile-screen-observation-v1`, file identities, tree kind,
target geometry, and the screenshot and target consistency rules.

### What it will not do

It will not capture a screen, infer a target, perform an action, or convert a
visual target into a stable selector.

## Check local devices and run a guarded physical-device lifecycle

The device matrix can report local device presence, inspect device details,
processes, and installed apps, and run a guarded reserve, install, launch,
observe, collect, and cleanup lifecycle for one attached device. The farm and
provider adapters are explicit `NO-DATA` stubs.

This is a real physical-device capability for the local adapter. It needs a
paired, available device and the native device command-line tools. It reports
presence separately from app behavior.

### How to use it

Check local presence with `python3 scripts/device_matrix.py local`.

Inspect a device with `python3 scripts/device_matrix.py details --device DEVICE_ID`,
or inspect installed apps with `python3 scripts/device_matrix.py apps --device DEVICE_ID`.

Run the guarded lifecycle with `python3 scripts/device_matrix.py lifecycle --device DEVICE_ID --app path/to/your-app.app --bundle-id your.bundle.id --owner mobile-team --out-dir path/to/device-evidence`.

### Evidence it produces

It records device identity, leases, command responses, installed identity
checks, process and app observations, cleanup, and a per-stage verdict.

### What it will not do

It will not build an app, provide a device farm, infer physical behavior from
a simulator pass, or claim a release from installation alone.

## Track release states separately

The release tracker keeps upload, processing, availability, installation, and
accepted or released as five independent facts. Only installation has a live
local observation path in this tree.

This is a recording capability. It reads a device observation for installation
and needs release-system access for the other states.

### How to use it

Run `python3 scripts/release_state_tracker.py --observation path/to/installed-apps.json --bundle-id your.bundle.id --out path/to/release-state.json`.

### Evidence it produces

It writes one verdict and reason per state. Missing release-system access is
named `NO-DATA`, and an installed app is not treated as uploaded or released.

### What it will not do

It will not upload, process, assign, accept, or release a build. It has no
release-system integration.

## Compose a journey passport

The Journey Passport is a view over evidence already produced by the journey
contract, reference lock, native evidence, design evidence, physical-device
evidence, release tracker, and any separately supplied accessibility,
performance, production, or human-acceptance records.

This is a recording and composition capability. It does not touch a device.

### How to use it

Run `python3 scripts/journey_passport.py --journey-contract path/to/journey.json --reference-lock path/to/reference-lock.json --native-evidence-v2 path/to/evidence-v2.json --physical-device-evidence path/to/device-evidence.json --release path/to/release-state.json --out path/to/passport.json`.

### Evidence it produces

It hashes each supplied input, preserves each dimension's verdict, and reports
completeness. `FAIL` beats `NO-DATA`, and `NO-DATA` beats `PASS` when the view
summarizes missing or failed dimensions.

### What it will not do

It will not create missing evidence, re-run a test, or turn an incomplete
passport into a release approval.

## Register and check mobile product claims

Product claims connect a measurable claim to passport dimensions and, when
provided, a lifecycle record. This gives a product owner a place to check that
a claim is actually referenced by the evidence view.

This is a contract and validation capability. It does not touch a device.

### How to use it

Check a claim with `python3 scripts/mobile_product_claims.py check path/to/claim.json --passport-record path/to/passport.json`.

Register thresholds with `python3 scripts/mobile_product_claims.py register path/to/passport.json path/to/thresholds.json --out path/to/claims.json`.

Gate a passport against claims with `python3 scripts/mobile_product_claims.py gate path/to/passport.json path/to/claims.json`.

### Evidence it produces

It validates claim records, freezes threshold comparisons, and reports which
claims are referenced by which passport dimensions.

### What it will not do

It will not invent a product metric, improve a failing dimension, or make a
release or human acceptance decision.

## Reuse the simulator pool and integration canary

The simulator pool is a building block, not a command-line tool. It adds
selection, lease reuse, readiness, shutdown, erase, and dirty-device handling
over the shared lease store. It calls the native simulator command-line tools
when a caller uses its Python API.

The canary pipeline is also a building block. It runs a synthetic end-to-end
pipeline in its `main()` function so tests can catch mismatches between the
mobile modules. It has no command-line argument parser.

These are integration building blocks. The pool can drive a simulator through
its API. The canary does not drive a real product or device.

### How to use it

Do not invent a command for either file. Import the pool API from
`scripts/mobile_simulator_pool.py`, or run the existing canary test suite.

### Evidence it produces

The pool returns lease, readiness, reset, and quarantine results. The canary
returns the composed passport and an integration mismatch result, while its
tests also check the expected nonzero exit on an adjacent schema break.

### What it will not do

The pool does not replace the shared lease store or prove app quality. The
canary does not replace per-module evidence and does not turn incomplete
evidence into a pass.

## Keep the boundary clear

The mobile tree can drive a simulator today and can run a guarded lifecycle on
a locally attached physical device when the required native tools and hardware
exist. It can also validate contracts, plan work, record observations, and
compose evidence without a device.

Appium remains dependency-gated. Visual fallback remains a no-call interface.
Release state, accessibility, performance, visual meaning, physical feel, and
human acceptance need their own evidence. A missing observation is `NO-DATA`,
not a quiet pass.
