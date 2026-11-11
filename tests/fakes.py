"""Small stand-ins shared by the tests.

Not named ``test_*``, so discovery treats it as a helper rather than a suite.
"""

from __future__ import annotations

import json

from noteagent.llm.client import LlmTurn
from noteagent.settings.credentials import CredentialStoreUnavailable

#: The judge's all-pass reply, shared by the quality eval tests.
ALL_PASS = json.dumps(
    {
        "grounding": {"verdict": "pass", "reason": "有依据", "evidence": []},
        "completeness": {"verdict": "pass", "reason": "答全", "evidence": []},
        "uncertainty": {"verdict": "pass", "reason": "不猜测", "evidence": []},
    },
    ensure_ascii=False,
)


class FakeJudge:
    """A judge transport that replays one reply and keeps the requests."""

    def __init__(self, content: str = ALL_PASS) -> None:
        self.content = content
        self.requests: list[list[object]] = []

    def complete(self, messages, **kwargs) -> LlmTurn:
        self.requests.append(list(messages))
        return LlmTurn(content=self.content)


class FakeCredentials:
    """A credential store in memory, including its ability to be missing.

    ``fail_writes`` keeps ``available()`` true while refusing the next write:
    the store disappears between the save's pre-check and its commit, which is
    the window a document must not be written through.
    """

    def __init__(self, available: bool = True, fail_writes: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.available_flag = available
        self.fail_writes = fail_writes

    def available(self) -> bool:
        return self.available_flag

    def get(self, key: str) -> str | None:
        if not self.available_flag:
            raise CredentialStoreUnavailable()
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        if not self.available_flag or self.fail_writes:
            raise CredentialStoreUnavailable()
        self.values[key] = value

    def delete(self, key: str) -> None:
        if not self.available_flag or self.fail_writes:
            raise CredentialStoreUnavailable()
        self.values.pop(key, None)
