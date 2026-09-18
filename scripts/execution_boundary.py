#!/usr/bin/env python3
"""DOM-40.01: the one provider interface the orchestration core depends on.

WHY THIS EXISTS. Four boundary modules were built tonight, each answering
one narrow question in isolation: scripts/filesystem_enforcement.py (DOM-
40.02, may this path be written), scripts/network_policy.py (DOM-40.03,
may this host be reached), scripts/credential_broker.py (DOM-40.04, may
this caller hold this secret for this purpose), scripts/process_
containment.py (DOM-40.05, spawn a tracked subprocess and later kill its
whole tree). Every one of them was built to be called from somewhere;
nothing outside their own tests called any of them until this module.

THE DECIDING PROPERTY (the unit's own words, from docs/plan/ORCH-1020-
WBS.json): the core depends on ONE provider interface for filesystem,
network, process and credentials, never on one technology, and a provider
that cannot supply a capability says so rather than pretending. This
module is that interface: ExecutionBoundary composes the four modules
above by calling straight through to their own documented functions,
unchanged, never re-deciding anything they already decide. A capability
whose provider was not supplied at construction time raises
CapabilityUnsupported naming which one, rather than returning a default
verdict, an empty result, or silently doing nothing: a caller who checks
nothing and just calls the method gets a loud, typed failure instead of a
quiet false ALLOW.

ON "RESOURCES". docs/plan/ORCH-1020-WBS.json's own deciding_property line
names five words: filesystem, network, process, credentials, and
resources. Four of those five are backed by a real module in this tree
tonight; no fifth "resources" module exists yet. This module's own
CAPABILITIES tuple deliberately holds four entries, not five: inventing a
resources provider with nothing behind it would be exactly the kind of
guessed value the estate's own rule against fabrication forbids, not a
completion of the interface. Add a fifth entry here, to CAPABILITIES and
to the mechanism tables below, the day a real resources module exists to
compose.

WHAT THIS MODULE IS NOT. It does not decide anything about a path, a
host, a credential grant, or a process tree; every one of those decisions
still belongs entirely to the module that makes it, called here unchanged.
It is not a fifth copy of the vocabulary in
scripts/orchestrator_invariants.py either: that module answers what a
unit of ORCHESTRATION WORK may become (task states, verdicts, failure
classes), a different question from which CAPABILITY a given runtime can
supply, so nothing here restates it (the same reuse note
scripts/credential_broker.py's own docstring already makes about itself,
for the same reason).

THE CAPABILITY REPORT. capability_report() answers, for each of the four
capabilities, one of four words:
  unsupported   no provider was supplied for this capability at all.
  advisory      a provider is supplied, and it only decides: it never
                itself performs the real-world effect (true of the
                filesystem and network providers; each says so in its own
                module docstring: filesystem_enforcement "decides; it
                never acts", network_policy "makes no real network call
                anywhere"). This is also the fact
                scripts/host_capability.py's own network_control field
                reads NO-DATA for tonight ("not measured by this
                receipt"): capability_report()["network"] is what could
                fill that field later. This module does not edit that
                file; filling it is a later, separate decision, and
                host_capability.py is read-only from here.
  enforced      a provider is supplied, and its own call performs the
                real, irreversible effect (true of the credential and
                process providers: a real secret value is only ever
                released through credential_broker's own call, and a real
                signal is only ever sent through process_containment's
                own call).
  supported     a provider is supplied that is neither of the four
                built-in modules above (a caller's own stand-in, for a
                test or an unrecognised runtime); this module has no
                documented basis for calling a stranger's provider
                advisory or enforced, so it reports the honest, narrower
                fact instead of guessing either word.

CONTINGENCY AND EDGES, named here rather than after an incident:
  unknown capability name        raised as ValueError wherever a
                                  capability string reaches a check;
                                  never read as the safe, most-permissive
                                  case.
  provider is None                CapabilityUnsupported, naming the
                                  capability, on every call into it;
                                  never a silent no-op and never a
                                  default ALLOW, GRANT, or spawn.
  provider is supplied but        CapabilityUnsupported, naming the
  missing the method this         capability and the missing method, in
  module calls                    place of an AttributeError a caller
                                  would otherwise have to interpret.
  a concurrent second actor,      does not apply: this class holds no
  already done, partially done,   state of its own beyond which provider
  expired or stale                object is wired to which capability,
                                  set once at construction. Each of the
                                  four composed modules already states
                                  its own position on these edges in its
                                  own docstring; nothing here changes it.

Python 3, standard library only (the four composed modules are the only
imports). No network, no filesystem mutation, no subprocess of its own:
every one of those, when it happens at all, happens inside the composed
module that owns the decision.
"""

import credential_broker
import filesystem_enforcement
import network_policy
import process_containment

CAPABILITIES = ("filesystem", "network", "credential", "process")

UNSUPPORTED = "unsupported"
ADVISORY = "advisory"
ENFORCED = "enforced"
SUPPORTED = "supported"

STATUSES = frozenset((UNSUPPORTED, ADVISORY, ENFORCED, SUPPORTED))

#: The real module DOM-40.02 through DOM-40.05 built, keyed by capability.
#: identity ("is"), never equality, is what capability_report() compares
#: a caller's provider against: a lookalike module with the same function
#: names is still a stranger, reported SUPPORTED, not silently trusted as
#: one of these four.
_DEFAULT_PROVIDERS = {
    "filesystem": filesystem_enforcement,
    "network": network_policy,
    "credential": credential_broker,
    "process": process_containment,
}

#: The mechanism each BUILT-IN provider is known to carry, taken straight
#: from its own module docstring's own words: never guessed, and never
#: extended to a provider this module did not build.
_BUILTIN_MECHANISM = {
    "filesystem": ADVISORY,
    "network": ADVISORY,
    "credential": ENFORCED,
    "process": ENFORCED,
}


class CapabilityUnsupported(Exception):
    """A capability method was called but no usable provider backs it:
    either nothing was supplied for it at construction time, or what was
    supplied does not carry the method this module needs to call.
    `.capability` names which of CAPABILITIES; `.reason` says which of
    the two it was, in plain language."""

    def __init__(self, capability, reason):
        self.capability = capability
        self.reason = reason
        super().__init__("%s: %s" % (capability, reason))


class ExecutionBoundary:
    """The one interface the orchestration core is meant to depend on for
    filesystem, network, credential and process decisions, instead of
    importing any of the four composed modules directly. Each provider
    defaults to the real module DOM-40.02 through DOM-40.05 built; pass
    None for a capability a given runtime cannot supply, or a stand-in
    object for a test, and this class never pretends otherwise."""

    def __init__(self, *, filesystem=filesystem_enforcement,
                 network=network_policy, credential=credential_broker,
                 process=process_containment):
        self._providers = {
            "filesystem": filesystem,
            "network": network,
            "credential": credential,
            "process": process,
        }

    def _require(self, capability, method_name):
        """The named provider's own callable `method_name`, or raise.
        Never returns anything else: a caller that gets a return value
        back always has a real, callable method to call."""
        if capability not in CAPABILITIES:
            raise ValueError(
                "unknown capability %r; must be one of %s" %
                (capability, CAPABILITIES))
        provider = self._providers[capability]
        if provider is None:
            raise CapabilityUnsupported(
                capability,
                "no provider was supplied for this capability; refusing "
                "rather than defaulting to an ALLOW, a GRANT, or a spawn")
        method = getattr(provider, method_name, None)
        if not callable(method):
            raise CapabilityUnsupported(
                capability,
                "the supplied provider has no callable %r; this module "
                "never falls back to guessing what it meant" %
                (method_name,))
        return method

    def filesystem_decide(self, path, roots, action="write"):
        """Straight through to the filesystem provider's own decide().
        See scripts/filesystem_enforcement.py for the full rule table;
        this method changes nothing about the arguments or the returned
        Verdict."""
        return self._require("filesystem", "decide")(path, roots, action=action)

    def network_decide(self, host, config, high_autonomy=None):
        """Straight through to the network provider's own decide(). See
        scripts/network_policy.py for the full rule table."""
        return self._require("network", "decide")(
            host, config, high_autonomy=high_autonomy)

    def request_credential(self, store, name, scope, purpose, now):
        """Straight through to the credential provider's own
        request_credential(). See scripts/credential_broker.py; the real
        secret, when granted, still only ever reaches the caller through
        the returned grant's own `.value`, never through this method's
        own return shape."""
        return self._require("credential", "request_credential")(
            store, name, scope, purpose, now)

    def spawn_contained(self, args, **kwargs):
        """Straight through to the process provider's own
        spawn_contained(). See scripts/process_containment.py."""
        return self._require("process", "spawn_contained")(args, **kwargs)

    def cancel_process_tree(self, root_pid, root_pgid=None, grace=2.0,
                             kill_grace=0.5, poll_interval=0.05):
        """Straight through to the process provider's own
        cancel_process_tree(). See scripts/process_containment.py."""
        method = self._require("process", "cancel_process_tree")
        return method(root_pid, root_pgid=root_pgid, grace=grace,
                      kill_grace=kill_grace, poll_interval=poll_interval)

    def capability_report(self):
        """{capability: one of UNSUPPORTED, ADVISORY, ENFORCED, SUPPORTED},
        with exactly the four keys in CAPABILITIES, always, so a caller
        never has to guess whether a missing key means "no" or "never
        asked". See the module docstring's THE CAPABILITY REPORT for what
        each word means and why."""
        report = {}
        for capability in CAPABILITIES:
            provider = self._providers[capability]
            if provider is None:
                report[capability] = UNSUPPORTED
            elif provider is _DEFAULT_PROVIDERS[capability]:
                report[capability] = _BUILTIN_MECHANISM[capability]
            else:
                report[capability] = SUPPORTED
        return report
