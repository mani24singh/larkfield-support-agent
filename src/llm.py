"""Provider-agnostic chat client for an OpenAI-compatible endpoint.

Uses the Cadra proxy configured through ``Settings`` (base URL and key from the
environment), temperature 0, JSON response mode, a bounded timeout and three
retries with exponential backoff. Token usage always comes from the provider's
``usage`` block — never estimated — and cost is priced from the config table.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, Sequence

import httpx

from src.config import Settings
from src.money import parse_money
from src.models import Ticket

RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
JSON_MODE = {"type": "json_object"}
_MTOK = Decimal("1000000")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class LlmError(RuntimeError):
    """Raised when the provider call or its payload is unusable."""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message in a chat completion request."""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatResult:
    """A completion plus the provider-reported usage and measured cost."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ChatClient(Protocol):
    """Provider-agnostic chat interface used by the reply and extraction steps."""

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Return a completion and its measured usage."""


def token_cost_usd(
    input_tokens: int, output_tokens: int, *, model: str, settings: Settings
) -> float:
    """Price a call from ``Settings.price_table`` (USD per million tokens)."""
    price_in, price_out = settings.price_table.get(
        model, (Decimal("0.00"), Decimal("0.00"))
    )
    cost = (
        Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out
    ) / _MTOK
    return float(cost.quantize(Decimal("0.000001")))


class CadraClient:
    """HTTP chat client for the configured OpenAI-compatible proxy."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.cadra_proxy_url.rstrip("/")
        self._token = settings.cadra_token.get_secret_value()
        self._client = httpx.Client(
            timeout=settings.cadra_timeout_seconds,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        """True when a key and base URL are present."""
        return bool(self._token) and bool(self._base_url)

    def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        if not self.configured:
            raise LlmError("CADRA_TOKEN is not configured")

        payload = {
            "model": self._settings.cadra_model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens or 900,
            "response_format": response_format or JSON_MODE,
        }
        body = self._post_with_retries(payload)

        try:
            content = body["choices"][0]["message"]["content"]
            usage = body["usage"]
            input_tokens = int(usage["prompt_tokens"])
            output_tokens = int(usage["completion_tokens"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmError("provider response is missing content or usage") from exc

        if not isinstance(content, str) or not content.strip():
            raise LlmError("provider returned an empty completion")

        model = str(body.get("model") or self._settings.cadra_model)
        return ChatResult(
            text=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=token_cost_usd(
                input_tokens, output_tokens, model=model, settings=self._settings
            ),
        )

    def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        attempts = self._settings.cadra_max_retries + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = self._client.post(url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            else:
                if response.status_code in RETRY_STATUS:
                    last_error = LlmError(
                        f"provider returned HTTP {response.status_code}"
                    )
                elif response.status_code >= 400:
                    raise LlmError(
                        f"provider returned HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                else:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise LlmError("provider returned invalid JSON") from exc

            if attempt < attempts - 1:
                delay = self._settings.cadra_retry_backoff_seconds * (2**attempt)
                if delay > 0:
                    time.sleep(delay)

        raise LlmError(
            f"provider call failed after {attempts} attempts: {last_error}"
        )

    def close(self) -> None:
        self._client.close()


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model reply, tolerating code fences."""
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LlmError("model reply is not a JSON object") from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LlmError("model reply is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LlmError("model reply is not a JSON object")
    return payload


@dataclass(frozen=True, slots=True)
class Extraction:
    """Advisory facts read from an email; never authoritative."""

    order_reference: str | None = None
    product_names: tuple[str, ...] = ()
    requested_action: str | None = None
    damaged: bool = False
    faulty: bool = False
    wrong_item: bool = False
    non_delivery: bool = False
    claimed_amount_usd: Decimal | None = None
    shipping_requested: bool = False
    whole_order_requested: bool = False
    quantity: int | None = None
    override_attempt: bool = False
    override_quote: str | None = None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "yes", "1"}


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_optional_int(value: object) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def extract_facts(
    client: ChatClient, ticket: Ticket, *, settings: Settings | None = None
) -> Extraction:
    """Ask the model for advisory facts about an email.

    The result is evidence only: the canonical order id and every monetary
    figure still come from the deterministic policy layer.
    """
    from src.prompts import build_extraction_messages

    result = client.complete(
        build_extraction_messages(ticket),
        temperature=0.0,
        response_format=JSON_MODE,
    )
    payload = extract_json_object(result.text)

    names = payload.get("product_names") or []
    if not isinstance(names, list):
        names = []

    claimed_raw = _as_optional_str(payload.get("claimed_amount"))
    claimed: Decimal | None = None
    if claimed_raw is not None:
        try:
            claimed = parse_money(claimed_raw)
        except ValueError:
            claimed = None

    return Extraction(
        order_reference=_as_optional_str(payload.get("order_reference")),
        product_names=tuple(str(name).strip() for name in names if str(name).strip()),
        requested_action=_as_optional_str(payload.get("requested_action")),
        damaged=_as_bool(payload.get("damaged")),
        faulty=_as_bool(payload.get("faulty")),
        wrong_item=_as_bool(payload.get("wrong_item")),
        non_delivery=_as_bool(payload.get("non_delivery")),
        claimed_amount_usd=claimed,
        shipping_requested=_as_bool(payload.get("shipping_requested")),
        whole_order_requested=_as_bool(payload.get("whole_order_requested")),
        quantity=_as_optional_int(payload.get("quantity")),
        override_attempt=_as_bool(payload.get("override_attempt")),
        override_quote=_as_optional_str(payload.get("override_quote")),
    )
