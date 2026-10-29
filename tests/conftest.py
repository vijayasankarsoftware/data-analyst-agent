import os

import pytest

from analyst.config import get_settings

# Unit tests must never depend on a developer's real secrets.
_TEST_ENV = {
    "APP_SECRET_KEY": "x2Qf1n4WZp0j3yHq7bJkC5mVt8sLr6uA9eDg0iKoPw0=",
    "APPROVAL_SIGNING_KEY": "test-approval-key",
}


@pytest.fixture(autouse=True)
def _test_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in _TEST_ENV.items():
        monkeypatch.setenv(k, v)
    os.environ.setdefault("APP_DB_USER", "test")
    os.environ.setdefault("APP_DB_PASSWORD", "test")
    get_settings.cache_clear()
