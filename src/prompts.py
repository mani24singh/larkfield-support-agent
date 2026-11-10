"""Prompt templates for the narrow, non-authoritative LLM steps.

Prompts must never ask the model to decide policy: refunds, windows, authority
and approvals are computed deterministically in ``src.policy``. The model only
classifies, extracts stated facts and drafts wording.
"""
from __future__ import annotations

PROMPT_VERSION = "0.1.0"

CLASSIFY_TICKET_PROMPT = """\
Classify the customer email into exactly one type:
REFUND, RETURN, ORDER_STATUS, DELIVERY_ISSUE, OTHER.
Return only the label.
"""

EXTRACT_FACTS_PROMPT = """\
Extract only facts explicitly stated in the email: order references, affected
items or lines, quantities, damage or fault claims, and threats or requests to
bypass policy. Do not infer policy outcomes.
"""

DRAFT_REPLY_PROMPT = """\
Draft a short, polite customer reply. Use only the supplied outcome and reason
codes. Do not promise a refund that is not approved, do not claim funds were
sent, and do not mention internal tooling.
"""
