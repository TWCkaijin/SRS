from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def synthetic_inventory(monkeypatch):
    """The suite never seeds hosts from a developer's local inventory."""
    monkeypatch.setenv("SRS_SLURM_CONTROLLER_ID", "101")
    monkeypatch.setenv("SRS_INVENTORY_FILE", str(Path(__file__).parent / "fixtures" / "inventory.yaml"))
