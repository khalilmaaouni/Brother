"""Brother Core state root path resolution and layout management."""

import os


def state_root(base_dir=None):
    """Return the Brother state root directory path.

    Args:
        base_dir: Optional base directory; defaults to current working directory.
                 Exists purely so tests do not touch the real cwd.

    Returns:
        str: Path to the .brother directory.
    """
    return os.path.join(base_dir or os.getcwd(), ".brother")


def run_dir(run_id, base_dir=None):
    """Return the path to a specific run directory.

    Args:
        run_id: Identifier for the run.
        base_dir: Optional base directory; defaults to current working directory.

    Returns:
        str: Path to the run directory under state_root/runs/<run_id>.
    """
    return os.path.join(state_root(base_dir), "runs", run_id)


def ensure_state_layout(base_dir=None):
    """Create the Brother state directory layout.

    This is the ONLY function in this module allowed to touch the filesystem.
    Creates state_root and its subdirectories (config, state, runs, vault, cache, locks).

    Args:
        base_dir: Optional base directory; defaults to current working directory.
    """
    root = state_root(base_dir)

    # Create all required subdirectories
    subdirs = [
        os.path.join(root, "config"),
        os.path.join(root, "state"),
        os.path.join(root, "runs"),
        os.path.join(root, "vault"),
        os.path.join(root, "cache"),
        os.path.join(root, "locks"),
    ]

    for subdir in subdirs:
        os.makedirs(subdir, exist_ok=True)
