"""Cadra/OpenAI-compatible chat client.

Only classification, extraction and drafting may call a model. Every call is
measured (model name, token counts, cost) so ``trace`` can report it. Absence
of a token must degrade gracefully to rule-based behaviour, never crash.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from src.config import Settings


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message in a chat completion request."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatResult:
    """A completion plus the token usage needed for ``trace``."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ChatClient(Protocol):
    """Provider-agnostic chat interface."""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """Return a completion and its measured usage."""


class CadraClient:
    """HTTP client for the Cadra proxy configured via ``Settings``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ChatResult:
        raise NotImplementedError("model calls are implemented in the next milestone")


def token_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """Return the measured cost for a call using the configured price table."""
    raise NotImplementedError
