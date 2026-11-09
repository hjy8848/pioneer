"""The installed tau2 is the pinned v1.0.1 and its Retail DB is the expected one."""

import subprocess
from importlib.metadata import version

from pioneer.paths import TAU2_COMMIT, TAU2_DIR


def test_tau2_version_and_commit():
    assert version("tau2") == "1.0.1"
    head = subprocess.run(
        ["git", "-C", str(TAU2_DIR), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == TAU2_COMMIT


def test_retail_db_stats():
    from tau2.domains.retail.data_model import get_db

    stats = get_db().get_statistics()
    assert stats == {
        "num_products": 50,
        "num_users": 500,
        "num_orders": 1000,
        "total_num_items": 591,
    }
