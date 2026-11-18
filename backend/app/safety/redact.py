"""Secret redaction applied to every log line and config value before it is
stored as evidence, written to the audit log or returned by the API."""
from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEY = re.compile(r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key|authorization)", re.I)

PATTERNS = [
    # key=value / key: value pairs where the key looks sensitive
    (re.compile(r"(?i)\b([\w.-]*(?:password|passwd|secret|token|api[_-]?key|credential)[\w.-]*)\s*([=:])\s*([^\s,;\"']+)"), r"\1\2[REDACTED]"),
    # bearer tokens
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]+"), "Bearer [REDACTED]"),
    # credentials embedded in URLs: scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s@/]+)@"), r"\1:[REDACTED]@"),
    # AWS access key ids and generic long hex/base64 secrets prefixed by sk-/ghp_ etc.
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\b(sk|ghp|gho|xox[abp])[-_][A-Za-z0-9_\-]{10,}\b"), "[REDACTED_TOKEN]"),
]


def redact_text(text: str) -> str:
    for pattern, repl in PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact(value: Any, _key: str | None = None) -> Any:
    """Recursively redact a JSON-like structure."""
    if _key is not None and SENSITIVE_KEY.search(_key) and isinstance(value, (str, int, float)):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
