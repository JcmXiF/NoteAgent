"""Where API keys live: the operating system's credential store.

The configuration file is a document a person can read, copy or put in a
backup, so it never holds a secret. Keys go to the credential store the
operating system already protects, under one service name with a stable key per
entry.

A store that cannot be reached is a refusal, not a reason to fall back to a
plaintext file: a key written somewhere unprotected is worse than a save that
did not happen. Environment variables remain usable, because the person who set
them made that choice knowingly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from noteagent.util.refusal import Refusal

#: One service name for every entry this app writes.
KEYCHAIN_SERVICE = "noteagent"

#: The external search key is shared by every external provider.
SEARCH_KEY = "search:external"


def llm_key(profile_id: str) -> str:
    """The credential-store entry for one connection profile."""
    return f"llm:{profile_id}"


class CredentialStoreUnavailable(Refusal):
    """The operating system credential store cannot be used."""

    code = "settings.keychainUnavailable"
    status = 503

    def __init__(self, message: str = "the operating system credential store is unavailable") -> None:
        super().__init__(self.code, message, status=self.status)


@runtime_checkable
class CredentialStore(Protocol):
    """The few operations this app needs from a credential store."""

    def available(self) -> bool: ...

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class KeyringCredentialStore:
    """``keyring``, imported only when a key is actually read or written.

    The desktop runtime may be started before a backend is ready, and a missing
    backend must surface as a refusal rather than as an import failure at module
    load. The module is injectable so tests do not touch the real store.
    """

    def __init__(self, service: str = KEYCHAIN_SERVICE, module: Any = None) -> None:
        self._service = service
        self._module = module

    def _store(self) -> Any:
        module = self._module
        if module is None:
            try:
                import keyring as module
            except Exception as err:  # pragma: no cover - depends on the machine
                raise CredentialStoreUnavailable(f"keyring is not installed: {err}") from err
            self._module = module
        return module

    def available(self) -> bool:
        """Whether a usable backend exists, without prompting for anything."""
        try:
            module = self._store()
            backend = module.get_keyring()
        except Exception:
            return False
        if backend is None:
            return False
        try:
            from keyring.backends import fail

            if isinstance(backend, fail.Keyring):
                return False
        except Exception:  # pragma: no cover - keyring layout is stable
            pass
        return True

    def require(self) -> None:
        if not self.available():
            raise CredentialStoreUnavailable()

    def get(self, key: str) -> str | None:
        module = self._store()
        try:
            return module.get_password(self._service, key)
        except Exception as err:
            raise CredentialStoreUnavailable(str(err)) from err

    def set(self, key: str, value: str) -> None:
        module = self._store()
        try:
            module.set_password(self._service, key, value)
        except Exception as err:
            raise CredentialStoreUnavailable(str(err)) from err

    def delete(self, key: str) -> None:
        module = self._store()
        try:
            module.delete_password(self._service, key)
        except Exception as err:
            # Deleting a key that is not there is the state the caller asked for.
            errors = getattr(module, "errors", None)
            password_delete = getattr(errors, "PasswordDeleteError", None)
            if password_delete is not None and isinstance(err, password_delete):
                return
            raise CredentialStoreUnavailable(str(err)) from err


def keyring_store() -> CredentialStore:
    """The store this installation uses. Tests replace this function."""
    return KeyringCredentialStore()


__all__ = [
    "KEYCHAIN_SERVICE",
    "SEARCH_KEY",
    "CredentialStore",
    "CredentialStoreUnavailable",
    "KeyringCredentialStore",
    "keyring_store",
    "llm_key",
]
