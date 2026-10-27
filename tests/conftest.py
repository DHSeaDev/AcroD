import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest


@pytest.fixture(autouse=True)
def _reset_actions_auto_fold():
    """The auto-fold flags are module state; a narrow window in one test must not fold the next test's panel."""
    import acrod as H
    H._AUTO.update(narrow=False, open=False)
    yield
    H._AUTO.update(narrow=False, open=False)
