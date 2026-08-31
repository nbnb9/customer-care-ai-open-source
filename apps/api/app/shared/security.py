"""Small, deterministic security classifiers used at trust boundaries.

These checks are deliberately narrow.  They provide a fail-closed response for
known instruction-override patterns, but they are not a claim that keyword
matching can detect every prompt-injection attempt.  Authorization, evidence
selection, tool policy and output validation remain the primary controls.
"""

from __future__ import annotations

import re
from typing import Final

_WHITESPACE_RE: Final = re.compile(r"\s+")
_PROMPT_INJECTION_PATTERNS: Final = (
    re.compile(
        r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?"
        r"(?:previous|prior|earlier)\s+(?:instructions?|rules?)\b"
    ),
    re.compile(
        r"\b(?:reveal|show|print|leak)\s+(?:the\s+)?"
        r"(?:(?:system|developer)\s+)?(?:prompt|instructions?|message)\b"
    ),
    re.compile(r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\b"),
    re.compile(r"\b(?:jailbreak|prompt[\s-]?injection)\b"),
    re.compile(
        r"\b(?:bypass|override)\s+(?:safety|guardrails?|approval|policy|rules?)\b"
    ),
    re.compile(r"(?:忽略|无视|绕过).{0,24}(?:指令|规则|提示词|安全|审批)"),
    re.compile(r"(?:系统|开发者).{0,8}(?:提示词|指令|消息)"),
    re.compile(r"(?:泄漏|泄露|显示|展示).{0,16}(?:提示词|系统指令|开发者消息)"),
    re.compile(r"越狱"),
)


def contains_prompt_injection(text: str) -> bool:
    """Return whether text matches a known instruction-override pattern.

    Whitespace normalization lets the guard keep working when an attacker
    inserts line breaks or repeated spaces into otherwise explicit commands.
    """

    normalized = _WHITESPACE_RE.sub(" ", text.casefold()).strip()
    return any(
        pattern.search(normalized) is not None for pattern in _PROMPT_INJECTION_PATTERNS
    )
