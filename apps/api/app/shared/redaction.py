"""Minimal deterministic redaction before text is sent to an LLM provider."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_CN_ID_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
_CN_PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_INTL_PHONE_RE = re.compile(r"(?<!\w)\+\d(?:[ -]?\d){7,14}(?!\d)")
_PAYMENT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def redact_direct_identifiers(text: str, *, max_chars: int) -> str:
    """Redact common direct identifiers before a bounded secondary use.

    This helper is intentionally not model-specific: the same minimal
    redaction floor is also used for staff handoff summaries.  It is not a
    substitute for a production data-classification or retention programme.
    """

    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = _CN_ID_RE.sub("[REDACTED_CN_ID]", redacted)
    redacted = _CN_PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = _INTL_PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    redacted = _PAYMENT_CARD_RE.sub("[REDACTED_PAYMENT_CARD]", redacted)
    return redacted[:max_chars]


def sanitize_for_model(text: str, *, max_chars: int) -> str:
    """Redact common direct identifiers and enforce a provider input limit."""

    return redact_direct_identifiers(text, max_chars=max_chars)
