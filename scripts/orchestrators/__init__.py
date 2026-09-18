"""ORCH-06: the orchestrator-adapter family.

Every adapter that invokes an outside model to play an orchestrating role
(deciding DISPATCH, PARK, QUEUE-HUMAN and the rest) lives under this
package and satisfies the one contract in base.py. A package, not a bare
module, because more than one concrete family is expected here (an
advisory family that only recommends, an execution family that actually
issues action envelopes for the loop to apply); base.py is the seam that
keeps them from diverging on retry, deadline and failure-classification
behaviour each time a new family is added.
"""
