import pytest

from token_pulse import i18n


@pytest.fixture(autouse=True)
def deterministic_language():
    """Existing Chinese assertions must not depend on the test host's locale."""
    i18n.set_language("zh_CN")
    yield
    i18n.set_language("zh_CN")
