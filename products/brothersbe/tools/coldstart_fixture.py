"""The throwaway repository a cold-start beginner is dropped into.

Built outside the repository and outside ~/Documents, per the build-output rule:
anything under Documents is synced and backed up, and a sandbox is meant to be
deleted. The subject is DATA, so adding a tier or a second subject is an entry
in SUBJECTS rather than a code change.
"""

import os
import subprocess
import tempfile

T1_BRIEF = """# Brief

Add a `GET /orders/{id}` endpoint to the service in `service/app.py`.

It reads from the `orders` table, returns 404 when the row is absent, and the
two existing callers in `clients/` must keep working unchanged.
"""

T1_APP = """def get_order(order_id):
    raise NotImplementedError
"""

SUBJECTS = {
    # One boundary, some consumers: the smallest subject that still produces
    # design artifacts, hits a gate and needs a review.
    "t1-endpoint": {
        "BRIEF.md": T1_BRIEF,
        "service/app.py": T1_APP,
        "clients/reporting.py": "from service.app import get_order\n",
        "clients/billing.py": "from service.app import get_order\n",
    },
}


def build_sandbox(subject_name):
    files = SUBJECTS[subject_name]
    path = tempfile.mkdtemp(prefix="sbe-coldstart-")
    for rel, content in files.items():
        target = os.path.join(path, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as fh:
            fh.write(content)
    for argv in (["git", "init", "-q"],
                 ["git", "add", "."],
                 ["git", "-c", "user.email=sandbox@example.invalid",
                  "-c", "user.name=sandbox", "commit", "-q", "-m", "subject"]):
        done = subprocess.run(argv, cwd=path, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError("sandbox setup failed at %s: %s" % (argv[1], done.stderr.strip()))
    return path
