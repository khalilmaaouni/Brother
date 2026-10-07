"""Brother Core: run identity, evidence envelope, receipt, acceptance, state paths, product identity.

Built by WBS unit U2 as four parallel pieces (run, evidence, receipt plus
acceptance, state plus product), merged here into one package surface so a
caller imports from brother.core rather than reaching into a specific file.
"""

from plugin.runtime.brother.core.run import (
    new_run_id,
    new_child_id,
    outcome_contract,
)
from plugin.runtime.brother.core.evidence import (
    Evidence,
    is_pass,
    is_fail,
    is_no_data,
    ALLOWED_DOMAINS,
    ALLOWED_STATUSES,
)
from plugin.runtime.brother.core.receipt import (
    build_receipt,
    assert_closeout_sound,
)
from plugin.runtime.brother.core.acceptance import (
    record_acceptance,
)
from plugin.runtime.brother.core.state import (
    state_root,
    run_dir,
    ensure_state_layout,
)
from plugin.runtime.brother.core.product import (
    product_identity,
)

__all__ = [
    "new_run_id", "new_child_id", "outcome_contract",
    "Evidence", "is_pass", "is_fail", "is_no_data",
    "ALLOWED_DOMAINS", "ALLOWED_STATUSES",
    "build_receipt", "assert_closeout_sound",
    "record_acceptance",
    "state_root", "run_dir", "ensure_state_layout",
    "product_identity",
]
