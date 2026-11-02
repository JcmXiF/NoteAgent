"""Small stand-ins shared by the settings tests.

Not named ``test_*``, so discovery treats it as a helper rather than a suite.
"""

from __future__ import annotations

from noteagent.settings.credentials import CredentialStoreUnavailable


class FakeCredentials:
    """A credential store in memory, including its ability to be missing."""

    def __init__(self, available: bool = True) -> None:
        self.values: dict[str, str] = {}
        self.available_flag = available

    def available(self) -> bool:
        return self.available_flag

    def get(self, key: str) -> str | None:
        if not self.available_flag:
            raise CredentialStoreUnavailable()
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        if not self.available_flag:
            raise CredentialStoreUnavailable()
        self.values[key] = value

    def delete(self, key: str) -> None:
        if not self.available_flag:
            raise CredentialStoreUnavailable()
        self.values.pop(key, None)
