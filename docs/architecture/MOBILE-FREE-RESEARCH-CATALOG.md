# Free mobile research catalog

Appllama's paid MCP combines app discovery, market context, screen media and
flow taxonomy. Brother keeps that purpose while removing the account
dependency by composing public sources and local evidence. The result is
reproducible and legal to rerun, but it does not claim Appllama's proprietary
revenue estimates.

## Capability replacement map

| Appllama purpose | Free or self-hosted replacement | Evidence produced | Limitation |
| :-- | :-- | :-- | :-- |
| Search winning apps | Apple App Store public listings and RSS feeds, Google Play public Top Charts, AppBrain public rankings | dated app shortlist with store, country, rank, rating, review count and URL | no private revenue or download estimates |
| App profile and ranking context | iTunes Search API, public store metadata, public reviews, App Store Connect API for Tonari | raw response, query, timestamp and normalized profile | App Store Connect requires Tonari's own developer access |
| Screen and flow inventory | store screenshots and previews, official product sites, local simulator captures, Apple sample apps | local board with stable ref, hash, source, flow step and observation | store media is promotional and may omit intermediate states |
| Semantic screen search | a checked-in JSON or SQLite board, local token and tag search, image hashes and optional CLIP embeddings | query, matched refs, filters and ranking method | semantic quality depends on the local board |
| UI element and pattern taxonomy | Apple Human Interface Guidelines, SwiftUI documentation, Material 3, the local board's controlled tags | element tags, platform rule and decision note | conventions are guidance, not market proof |
| Curated boards | `research/<category>/` folders in the project and Penpot exports | versioned board manifest, notes and asset hashes | curation is manual until a bounded automation is proven |
| Build and simulator loop | Xcode, `xcrun simctl`, XCTest/XCUITest, Fastlane Snapshot, ffmpeg and Brother native workflow | test result bundle, installed identity, screenshots, video, hashes | physical feel, audio and haptics still need a phone observation |

## Recommended no-cost stack for Tonari

1. Start each study with a category and country list. Capture the public App
   Store and Google Play results, preserving the URL, date and locale.
2. Select five to ten references. Store screenshots and preview links only for
   the design question being answered. Record the flow step and the pattern,
   never copy a screen one to one.
3. Use the local board as the Appllama replacement. A board entry is
   `board_id/screen_id`, where `screen_id` is stable and the media hash changes
   when the asset changes.
4. Turn the board into a design brief, then run the native SwiftUI workflow.
   Every build ends in a fresh result bundle and a simulator or phone capture.
5. Record observations in the same passport and receipt. Missing ranking,
   revenue, media or phone evidence is `NO-DATA`.

## Source register

The following sources are public or self-hostable at the time of this plan:

- Apple iTunes Search API: `https://developer.apple.com/library/archive/documentation/AudioVideo/Conceptual/iTuneSearchAPI/`
- Apple App Store Connect API: `https://developer.apple.com/app-store-connect/api/`
- Model Context Protocol Registry: `https://modelcontextprotocol.io/registry/about`
- Self-hostable skill registry: `https://github.com/sarveshtalele/mcp-skills-registry`
- Open source design workspace: `https://penpot.app/self-host`
- Brother's local native and design guards: `scripts/mobile_workflow.py` and
  `scripts/mobile_design.py`

## Adapter rule

The hosted catalog remains an optional adapter. If it is unavailable, the
router selects this catalog and records `source_mode=public-local`. The unit
must state which fields are observed, estimated or unavailable. A public chart
rank can support a shortlist; it cannot support a claim about revenue. No
crawler sweeps a catalog. Research stays task-scoped, stores only the minimum
media needed for the decision, and preserves the source URL and timestamp.

## Quality bar

The free path matches the purpose of Appllama's design skill when the question
is pattern extraction, native implementation, motion review or simulator
verification. It does not match the proprietary market dataset. Brother makes
that gap visible in the passport so a founder can decide whether a paid source
is worth buying for a specific decision.
