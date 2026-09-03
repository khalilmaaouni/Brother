Status: CURRENT.

# Getting BrotherMode and BrotherSBE listed

Written 2026-08-12 from the official documentation, read that day, plus checks
run against the live directories and a clean install from a throwaway config.

---

## 1. The thing to understand before anything else

**"Officially listed by Claude" is not something you can apply for.**

Anthropic runs two public marketplaces and they work differently:

| | `claude-plugins-official` | `claude-community` |
|---|---|---|
| What it is | Curated set maintained by Anthropic | Public marketplace where third-party submissions land after review |
| How you get in | **No application process.** Anthropic decides at its discretion | Submit through an in-app form, then review |
| Registered by | Claude Code automatically on first interactive start | Users add it themselves |

The documentation states it directly: "The official marketplace,
`claude-plugins-official`, is curated separately. Anthropic decides which
plugins to include at its discretion. There is no application process, and the
submission form does not add plugins to the official marketplace."

So the realistic goal is **`claude-community`**, which is the reviewed, public,
installable directory. Nobody can do more than that, including Anthropic
employees filling in the same form.

## 2. What is already true today, verified

**Both plugins are already public and installable by anyone.** This was tested
from a throwaway config, exactly as a stranger would:

```bash
claude plugin marketplace add khalilmaaouni/BrotherModeUp
claude plugin install brothermode@brothermode-marketplace

claude plugin marketplace add khalilmaaouni/Brothersbe
claude plugin install brothersbe@brothersbe
```

Both reported `✔ Successfully installed` and both appeared enabled. **Your team,
your friends and the public can use these right now**, with no directory, no
review and no waiting. Send them the four lines above.

Listing in `claude-community` adds discovery, not availability. It is worth
having and it is not a blocker for anyone using these tomorrow.

## 3. Current state

CORRECTED 2026-08-12, and the correction matters more than the original claim.
An earlier version of this page said neither plugin had ever been submitted.
That was wrong. It was inferred from zero hits in the two public catalogs, and
those catalogs list only APPROVED plugins, so a pending submission is invisible
in them. Absence from a catalog was never evidence that nothing was submitted.

THE REAL STATE, from the founder's own submissions view: BrotherMode and
BrotherSBE were submitted on 4 August 2026 and Token Shield on 12 August 2026.
All three read "Submitted and pending review".

## 3b. Checked against the live directories

| Check | BrotherMode | BrotherSBE |
|---|---|---|
| Listed in `claude-plugins-official` | NO | NO |
| Listed in `claude-plugins-community` | NO | NO |
| Repository public | yes | yes |
| Licence | MIT | MIT |
| `claude plugin validate` | passed | passed |
| `claude plugin validate --strict` | passed | passed |
| Public install works from a clean config | yes | yes |
| Version the public actually gets | 3.3.0 | **3.2.0** |

The official directory holds 286 plugins and neither of yours is among them. A
code search for `brothermode`, `brothersbe` and `maaouni` across both Anthropic
repositories returned zero hits. That means NOT YET APPROVED. It does not mean
not submitted: see section 3.

**One defect worth fixing before you submit.** BrotherSBE's local manifest says
3.3.0, but the public gets 3.2.0, because the 3.3.0 manifest sits on the
`feature/fortnight-plan-and-floor-audit` branch and remote `main` is at an older
commit. A reviewer pins the submission to a commit SHA on the default branch, so
they would review 3.2.0. Merge to `main` first if 3.3.0 is what you want
reviewed.

## 4. The submission, step by step

Two forms exist. Pick by which account you have:

- **Console (individual authors):** https://platform.claude.com/plugins/submit
- **claude.ai (organizations):**
  https://claude.ai/admin-settings/directory/submissions/plugins/new
  Requires a Team or Enterprise organization AND directory management access.
  Organization Owners have it by default.

**Use the Console form** unless you are submitting under a Team or Enterprise
organization.

Both require you to be signed in. That step is yours: no assistant should ever
be typing your credentials, and this one will not.

### What happens after you submit

1. Automated validation runs `claude plugin validate`, the same check that
   already passes on both of yours, plus automated safety screening.
2. On approval, the plugin is pinned to a specific commit SHA in the
   `anthropics/claude-plugins-community` catalog.
3. CI bumps that pin automatically as you push new commits.
4. The public catalog syncs nightly, so there is a delay between approval and
   the plugin appearing.
5. Check whether it is live by searching for the name in
   https://github.com/anthropics/claude-plugins-community/blob/main/.claude-plugin/marketplace.json

Once listed, users install with `@claude-community`.

## 5. Field values, ready to paste

Exact names matter. These are read from the manifests, not typed from memory.

### BrotherMode

- Repository: `https://github.com/khalilmaaouni/BrotherModeUp`
- Plugin name: `brothermode`
- Marketplace name: `brothermode-marketplace`
- Version: `3.3.0`
- Author: Khalil Maaouni
- Licence: MIT
- Category: productivity
- Keywords: orchestration, project-management, delivery, forecasting,
  beginner-friendly
- Description: Turn an idea into a verified result with a guided start, honest
  forecasts, clear status, and a checked delivery. Verified on Claude Code.

### BrotherSBE

- Repository: `https://github.com/khalilmaaouni/Brothersbe`
- Plugin name: `brothersbe`
- Marketplace name: `brothersbe`
- Version: `3.2.0` on the default branch today, `3.3.0` once merged
- Author: Khalil Maaouni
- Licence: MIT

## 6. Honest risks

- **Review is not guaranteed.** Both pass validation, which is necessary and not
  sufficient. Safety screening is separate and its criteria are not published.
- **No external security review** of either plugin has been carried out. If the
  screening asks, that is the honest answer.
- **BrotherMode installs hooks** at user-global scope. That is a legitimate
  design and it is the kind of thing a safety screen looks at closely.
  `docs/KNOWN-LIMITS.md` already states what the fence does and does not
  contain, which is the right posture going in.
- **Submitting the stale BrotherSBE version** means a reviewer pins 3.2.0.

## 7. What to do next, in order

All three are already submitted and pending, so nothing below is a submission
step.

1. Merge BrotherSBE's 3.3.0 work to `main`. CI bumps the pinned commit
   automatically once a plugin is approved, so the version reviewers and users
   land on should be the one you want.
2. Send your team the commands in section 2 today. They do not need the
   directory, and waiting for it costs you nothing but also gains you nothing.
3. Check for approval by searching the community catalog rather than by waiting
   for a notification:
   https://github.com/anthropics/claude-plugins-community/blob/main/.claude-plugin/marketplace.json

## 8. How long review takes

UNVERIFIED, and stated that way on purpose. No published service level for
review time was found in the Claude Code documentation or on either Anthropic
repository. What IS documented: automated validation and safety screening run
on submission, approved plugins are pinned to a commit SHA, and the public
catalog syncs nightly, so there is an additional delay of up to a day between
approval and appearing.

The only real data point available is your own: BrotherMode and BrotherSBE have
been pending since 4 August. Treat that as one observation, not as a norm.
