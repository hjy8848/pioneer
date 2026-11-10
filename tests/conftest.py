import os

from pioneer.paths import TAU2_DATA_DIR

# Point tau2 at the pinned data checkout before tau2 is imported by any test.
os.environ.setdefault("TAU2_DATA_DIR", str(TAU2_DATA_DIR))
# [INDEPENDENT] LiteLLM must not fetch its price map during offline tests.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


import socket
import pytest


@pytest.fixture(autouse=True)
def offline_tests(monkeypatch, request):
    """[INDEPENDENT] Enforce the repository's offline pytest contract."""
    if request.node.get_closest_marker("llm"):
        return

    def denied(*args, **kwargs):
        raise AssertionError("network access is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
