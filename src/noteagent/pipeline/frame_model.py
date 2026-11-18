"""The model's side of framing: openings, expansions, and a list of frames.

The first request shows every block's opening and nothing more. The model may
ask for a few blocks' full text, and after enough of that it must answer with
frames; the caller bounds both and falls back to the deterministic split when
the model cannot or does not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from noteagent.ingest.topic_map import extract_json_object
from noteagent.pipeline.materials import PREVIEW_CHARS, preview_text

#: How many model calls one material may take.
DEFAULT_ROUNDS = 12

#: How many characters of block text one material may be shown.
DEFAULT_CHARS = 120_000

#: How many frames one material may have.
MAX_FRAMES = 8

FRAME_SYSTEM_PROMPT = (
    "You are choosing which passages of one source belong together as material "
    "for a wiki. You will see every block's id, tag and opening. If a block's "
    "full text is needed to decide, ask for it; otherwise return the frames. "
    "Reply with one JSON object only, and nothing else: "
    '{"expand": ["id", ...]} to read more, or '
    '{"frames": [["id", ...], ...]} to finish. '
    "A frame is one topic, in reading order; at most "
    f"{MAX_FRAMES} frames. Use only ids that were shown."
)

FINALIZE_PROMPT = (
    "The reading budget is spent. Return the frames now from the openings and "
    'texts you have; reply with {"frames": [[...]]} only, and do not ask for '
    "more text."
)


@dataclass(frozen=True, slots=True)
class ModelFrames:
    """What one model framing produced, and what it cost."""

    frames: tuple[tuple[str, ...], ...] = ()
    calls: int = 0
    chars: int = 0
    omitted: int = 0
    failed: bool = False


@dataclass(slots=True)
class ModelFraming:
    """The bounded disclosure loop for one material at a time."""

    ask: Callable[[Sequence[tuple[str, str]]], str]
    rounds: int = DEFAULT_ROUNDS
    chars: int = DEFAULT_CHARS
    preview_chars: int = PREVIEW_CHARS
    max_frames: int = MAX_FRAMES

    def frames(
        self,
        *,
        material: str,
        title: str,
        kind: str,
        blocks: Sequence[Mapping[str, object]],
    ) -> ModelFrames:
        known: dict[str, Mapping[str, object]] = {}
        for block in blocks:
            block_id = str(block.get("id") or "").strip()
            if block_id:
                known[block_id] = block
        if not known:
            return ModelFrames()

        messages: list[tuple[str, str]] = [
            ("system", FRAME_SYSTEM_PROMPT),
            ("user", self._opening(material, title, kind, known)),
        ]
        expanded: set[str] = set()
        read = 0
        calls = 0
        for _ in range(self.rounds):
            calls += 1
            try:
                reply = self.ask(list(messages))
            except Exception:  # noqa: BLE001 - a model failure is a fallback
                return ModelFrames(calls=calls, chars=read, failed=True)
            try:
                data = extract_json_object(reply)
            except ValueError:
                return ModelFrames(calls=calls, chars=read, failed=True)

            answer = self._answer(data.get("frames"), known, calls, read)
            if answer is not None:
                return answer

            wanted = self._wanted(data.get("expand"), known, expanded)
            if not wanted:
                messages.append(("assistant", reply))
                return self._finalize(messages, known, calls, read)
            pieces: list[str] = []
            for block_id in wanted:
                text = str(known[block_id].get("text") or "")
                if read + len(text) > self.chars:
                    break
                expanded.add(block_id)
                read += len(text)
                pieces.append(f"[{block_id}] {text}")
            if not pieces:
                return self._finalize(messages, known, calls, read)
            messages.append(("assistant", reply))
            messages.append(("user", "Full text:\n" + "\n\n".join(pieces)))
            if read >= self.chars:
                return self._finalize(messages, known, calls, read)
        return self._finalize(messages, known, calls, read)

    def _answer(
        self,
        raw: object,
        known: Mapping[str, Mapping[str, object]],
        calls: int,
        read: int,
    ) -> ModelFrames | None:
        if not isinstance(raw, list):
            return None
        frames = self._clean(raw, known)
        if not frames:
            return None
        omitted = max(0, len(frames) - self.max_frames)
        return ModelFrames(
            tuple(frames[: self.max_frames]), calls, read, omitted
        )

    def _finalize(
        self,
        messages: Sequence[tuple[str, str]],
        known: Mapping[str, Mapping[str, object]],
        calls: int,
        read: int,
    ) -> ModelFrames:
        """Spend one last call on frames, from what the model has already read."""
        calls += 1
        final_messages = list(messages)
        final_messages.append(("user", FINALIZE_PROMPT))
        try:
            reply = self.ask(final_messages)
        except Exception:  # noqa: BLE001 - a model failure is a fallback
            return ModelFrames(calls=calls, chars=read, failed=True)
        try:
            data = extract_json_object(reply)
        except ValueError:
            return ModelFrames(calls=calls, chars=read, failed=True)
        answer = self._answer(data.get("frames"), known, calls, read)
        if answer is not None:
            return answer
        return ModelFrames(calls=calls, chars=read, failed=True)

    def _opening(
        self,
        material: str,
        title: str,
        kind: str,
        known: Mapping[str, Mapping[str, object]],
    ) -> str:
        lines = [
            f"material: {material}",
            f"title: {title}",
            f"kind: {kind}",
            f"blocks: {len(known)}",
            "",
        ]
        for block_id, block in known.items():
            tag = str(block.get("tag") or "")
            opening = preview_text(
                str(block.get("text") or ""), self.preview_chars
            )
            lines.append(f"{block_id} · {tag} · {opening}")
        return "\n".join(lines)

    def _clean(
        self,
        raw: list[object],
        known: Mapping[str, Mapping[str, object]],
    ) -> list[tuple[str, ...]]:
        frames: list[tuple[str, ...]] = []
        for item in raw:
            if not isinstance(item, list):
                continue
            ids = tuple(
                str(block_id)
                for block_id in item
                if isinstance(block_id, str) and block_id in known
            )
            if ids:
                frames.append(ids)
        return frames

    def _wanted(
        self,
        raw: object,
        known: Mapping[str, Mapping[str, object]],
        expanded: set[str],
    ) -> list[str]:
        if not isinstance(raw, list):
            return []
        wanted: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            if item in known and item not in expanded and item not in wanted:
                wanted.append(item)
        return wanted


def complete_frame_messages(messages: Sequence[tuple[str, str]]) -> str:
    """Ask the model one round, as the production completion it is."""
    # Imported here: a run that never frames should not load the model stack.
    from noteagent.llm.client import ChatMessage, LlmClient
    from noteagent.llm.config import load_llm_config

    client = LlmClient(load_llm_config())
    return client.chat(
        [
            ChatMessage(role=role, content=content)
            for role, content in messages
        ]
    )


def default_frame_model(
    *, rounds: int = DEFAULT_ROUNDS, chars: int = DEFAULT_CHARS
) -> ModelFraming:
    return ModelFraming(
        ask=complete_frame_messages, rounds=rounds, chars=chars
    )


__all__ = [
    "DEFAULT_CHARS",
    "DEFAULT_ROUNDS",
    "FRAME_SYSTEM_PROMPT",
    "MAX_FRAMES",
    "ModelFrames",
    "ModelFraming",
    "PREVIEW_CHARS",
    "complete_frame_messages",
    "default_frame_model",
]
