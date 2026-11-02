"""Named connection profiles: the screen's create, edit, delete and active rules.

The document and the credential store already know how to change themselves
(``store.py``). This module is the slice a request may touch: which fields a
profile has, what a save refuses, and when the key that belongs to it moves.

Every operation returns the settings view the screen draws, so the caller never
has to merge a mutation into a document it read before. ``expected_revision`` is
required: a draft that was written over while the reader edited it is refused
with 409 instead of being allowed to win by arriving last.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from noteagent.settings.credentials import CredentialStore
from noteagent.settings.install import clean_base_url, clean_secret, install_view, settings_store
from noteagent.settings.store import PROVIDERS, Profile, set_active_profile
from noteagent.util.refusal import Refusal

__all__ = [
    "activate_profile",
    "create_profile",
    "delete_profile",
    "update_profile",
]

_NAME_LIMIT = 120
_MODEL_LIMIT = 200


def _invalid(field: str, message: str) -> Refusal:
    return Refusal("settings.invalid", message, params={"field": field}, status=400)


def _not_found(profile_id: str) -> Refusal:
    return Refusal(
        "settings.profileNameNotFound",
        f"no connection profile has id {profile_id!r}",
        params={"id": profile_id},
        status=404,
    )


def _revision(body: Mapping[str, Any]) -> int:
    value = body.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid("revision", "revision is not a whole number")
    return value


def _clean_profile_name(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid("name", "the connection name is not text")
    text = value.strip()
    if not text or len(text) > _NAME_LIMIT or any(ch in text for ch in "\r\n\x00"):
        raise _invalid("name", "the connection name is empty or not a single usable line")
    return text


def _clean_provider(value: object) -> str:
    if not isinstance(value, str):
        raise _invalid("provider", "the provider is not text")
    text = value.strip().lower()
    if text not in PROVIDERS:
        raise _invalid("provider", f"the provider must be one of {', '.join(sorted(PROVIDERS))}")
    return text


def _clean_model(value: object) -> str:
    """A model name, or an empty one: the endpoint may not have been chosen yet."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _invalid("model", "the model name is not text")
    text = value.strip()
    if len(text) > _MODEL_LIMIT or any(ch in text for ch in "\r\n\x00"):
        raise _invalid("model", "the model name is not a single usable line")
    return text


def _key_patch(body: Mapping[str, Any]) -> str | None:
    """A replacement key, ``""`` to remove it, or ``None`` to leave it alone."""
    if body.get("apiKey"):
        return clean_secret(body["apiKey"], "apiKey")
    if body.get("clearApiKey"):
        return ""
    return None


def _new_profile_id(profiles: tuple[Profile, ...]) -> str:
    taken = {profile.id for profile in profiles}
    index = 1
    while f"p_{index}" in taken:
        index += 1
    return f"p_{index}"


def create_profile(
    body: Mapping[str, Any],
    *,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> dict[str, Any]:
    """Add a profile. The first one becomes active; later ones do not."""
    store = settings_store(path, credentials)
    expected = _revision(body)
    name = _clean_profile_name(body.get("name"))
    provider = _clean_provider(body.get("provider"))
    base_url = clean_base_url(body.get("baseUrl"))
    model = _clean_model(body.get("model"))
    secret = _key_patch(body)
    if secret:
        store.require_keychain()

    created: list[str] = []

    def mutate(current):
        ident = _new_profile_id(current.profiles)
        created.append(ident)
        return replace(
            current,
            profiles=(
                *current.profiles,
                Profile(
                    id=ident,
                    name=name,
                    provider=provider,
                    base_url=base_url,
                    model=model,
                ),
            ),
            active_profile_id=current.active_profile_id or ident,
        )

    def write_key(_config) -> None:
        if secret:
            store.set_llm_key(created[0], secret)

    store.update(
        mutate,
        expected_revision=expected,
        alongside=write_key if secret else None,
    )
    return install_view(path=path, credentials=credentials)


def update_profile(
    profile_id: str,
    body: Mapping[str, Any],
    *,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> dict[str, Any]:
    """Change the fields a draft actually carries; a blank key keeps the stored one."""
    store = settings_store(path, credentials)
    expected = _revision(body)
    changes: dict[str, str] = {}
    if body.get("name") is not None:
        changes["name"] = _clean_profile_name(body["name"])
    if body.get("provider") is not None:
        changes["provider"] = _clean_provider(body["provider"])
    if body.get("baseUrl") is not None:
        changes["base_url"] = clean_base_url(body["baseUrl"])
    if body.get("model") is not None:
        changes["model"] = _clean_model(body["model"])
    secret = _key_patch(body)
    if secret is not None:
        store.require_keychain()

    def mutate(current):
        found = False
        profiles = []
        for profile in current.profiles:
            if profile.id != profile_id:
                profiles.append(profile)
                continue
            found = True
            profiles.append(replace(profile, **changes))
        if not found:
            raise _not_found(profile_id)
        return replace(current, profiles=tuple(profiles))

    def write_key(_config) -> None:
        if secret:
            store.set_llm_key(profile_id, secret)
        else:
            store.delete_llm_key(profile_id)

    store.update(
        mutate,
        expected_revision=expected,
        force=secret is not None,
        alongside=write_key if secret is not None else None,
    )
    return install_view(path=path, credentials=credentials)


def delete_profile(
    profile_id: str,
    *,
    revision: int,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> dict[str, Any]:
    """Remove a profile, its key, and its claim on the active slot."""
    store = settings_store(path, credentials)
    store.delete_profile(profile_id, expected_revision=revision)
    return install_view(path=path, credentials=credentials)


def activate_profile(
    profile_id: str,
    *,
    revision: int,
    path: Path | None = None,
    credentials: CredentialStore | None = None,
) -> dict[str, Any]:
    """Make one existing profile the active one; nothing else changes."""
    store = settings_store(path, credentials)
    store.update(
        lambda current: set_active_profile(current, profile_id),
        expected_revision=revision,
    )
    return install_view(path=path, credentials=credentials)
