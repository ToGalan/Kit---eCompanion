"""Test-wide isolation for the stores.

The vault and the signal databases default to paths inside the repository, so a suite
run used to write real notes and real rows into the working tree and, from there, into
git. 6.7 says signal stores are not source control. Redirecting them here means running
the tests leaves the repository untouched.

This must happen at import time: the API module builds its Vault when it is imported,
which collection does before any fixture runs.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="kit-tests-"))

os.environ["KIT_VAULT_PATH"] = str(_ROOT / "vault")
os.environ["KIT_DB_PATH"] = str(_ROOT / "kit.db")
os.environ["KIT_TOUCHPOINT_DB_PATH"] = str(_ROOT / "touchpoints.db")
os.environ["KIT_METRICS_DB_PATH"] = str(_ROOT / "exposure.db")
