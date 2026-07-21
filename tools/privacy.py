"""
Privacy helpers.

Two independent protections, both plain synchronous string work:

- scrub_text / scrub_query — strip PII (emails, phone numbers, street
  addresses) from text before it leaves the process toward a third-party
  search engine or the model API. Deliberately conservative about what it
  keeps: city/state/zip survive, because the locale is what makes a grocery
  search useful; a house number and street name do not help and are dropped.

- redact — mask API keys in text destined for logs, progress lines, or
  user-facing errors. Exception strings can embed full request URLs (httpx
  errors do), which would otherwise leak `api_key=...` query params.
"""
from __future__ import annotations
import os
import re

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# North American 10-digit numbers, with or without +1 / separators / parens.
# Digit boundaries keep it off zip codes, prices, and package sizes.
_PHONE_RE = re.compile(
    r"(?<![\d.])(?:\+?1[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}(?![\d.])"
)

_STREET_SUFFIX = (
    r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|ct|court|"
    r"way|pl|place|ter|terrace|cir|circle|hwy|highway|pkwy|parkway|trl|trail)"
)
# House number + 1-4 street-name words + suffix, plus an optional unit tail.
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+(?:[A-Za-z0-9'.\-]+\s+){{1,4}}{_STREET_SUFFIX}\b\.?"
    rf"(?:\s*,?\s*(?:apt|apartment|unit|suite|ste|#)\s*\.?\s*[\w\-]+)?",
    re.IGNORECASE,
)

_SCRUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", _EMAIL_RE),
    ("street address", _ADDRESS_RE),
    ("phone number", _PHONE_RE),
)


def scrub_text(text: str) -> tuple[str, list[str]]:
    """Remove PII from text. Returns (scrubbed_text, kinds_removed)."""
    removed: list[str] = []
    for kind, pattern in _SCRUB_PATTERNS:
        text, count = pattern.subn(" ", text)
        if count:
            removed.append(kind)
    if removed:
        text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    return text, removed


def scrub_query(text: str) -> str:
    """Scrubbed text only — for call sites that don't report what was removed."""
    return scrub_text(text)[0]


_SECRET_ENV_VARS = ("MOONSHOT_API_KEY", "SERPAPI_KEY")
_KEY_PARAM_RE = re.compile(r"(?i)\b(api_key|apikey|access_token|token|authorization)=[^&\s\"']+")
# Bare key material seen in error bodies even when the env var isn't the source.
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}")
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9._\-]{8,}")


def redact(text: str) -> str:
    """Mask API keys before text reaches a log line or user-facing error."""
    for var in _SECRET_ENV_VARS:
        value = os.environ.get(var)
        # Short values could blank innocent substrings, so only replace
        # anything long enough to plausibly be a real key.
        if value and len(value) >= 8:
            text = text.replace(value, "***")
    text = _KEY_PARAM_RE.sub(r"\1=***", text)
    text = _BEARER_RE.sub("Bearer ***", text)
    return _SK_KEY_RE.sub("sk-***", text)
