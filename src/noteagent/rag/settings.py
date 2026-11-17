"""The RAG switch and the two service configs it turns on.

The process environment is the input, as it is for the model endpoint; a value
set before the process started wins. The embedding key may also come from the
operating system's credential store. RAG is off unless
``NOTEAGENT_RAG_ENABLED`` says otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from noteagent.rag.config import EmbeddingConfig, load_embedding_config
from noteagent.rag.env import read_boolean
from noteagent.rag.rerank_config import RerankConfig, load_rerank_config
from noteagent.settings.credentials import CredentialStore

__all__ = [
    "ENV_ENABLED",
    "RagConfigError",
    "RagSettings",
    "load_rag_settings",
]

ENV_ENABLED = "NOTEAGENT_RAG_ENABLED"


class RagConfigError(ValueError):
    """The RAG switch or one of its service settings is malformed."""


@dataclass(frozen=True, slots=True)
class RagSettings:
    """Whether hybrid retrieval is on, and the services it needs."""

    enabled: bool = False
    embedding: EmbeddingConfig = EmbeddingConfig()
    rerank: RerankConfig = RerankConfig()


def load_rag_settings(
    *,
    environ: Mapping[str, str] | None = None,
    credential_store: CredentialStore | None = None,
) -> RagSettings:
    source = os.environ if environ is None else environ
    enabled = read_boolean(
        source, ENV_ENABLED, False, error=RagConfigError
    )
    if not enabled:
        # The off switch is also a promise not to touch the credential store.
        return RagSettings(enabled=False)
    return RagSettings(
        enabled=True,
        embedding=load_embedding_config(
            environ=source, credential_store=credential_store
        ),
        rerank=load_rerank_config(
            environ=source, credential_store=credential_store
        ),
    )
