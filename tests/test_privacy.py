"""
Privacy guarantees: PII never reaches a search backend, secrets never reach
a log line or a user-facing error, and offline mode makes no network calls.
"""
from __future__ import annotations
import logging

import pytest

from tools import web_search as ws
from tools.privacy import redact, scrub_text


# ── Scrubbing ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, kind",
    [
        ("email me at ray.hunter@gmail.com thanks", "email"),
        ("call 512-555-0134", "phone number"),
        ("call (512) 555-0134", "phone number"),
        ("call +1 512.555.0134", "phone number"),
        ("1600 Pennsylvania Ave, Washington DC", "street address"),
        ("742 Evergreen Terrace Apt 3B", "street address"),
    ],
)
def test_scrub_removes_pii(raw, kind):
    scrubbed, removed = scrub_text(raw)
    assert kind in removed
    assert "@" not in scrubbed
    assert "555-0134" not in scrubbed and "5550134" not in scrubbed
    assert "Pennsylvania Ave" not in scrubbed and "Evergreen Terrace" not in scrubbed


@pytest.mark.parametrize(
    "locale",
    ["Austin, TX", "94102", "Seattle, WA 98101", "Brooklyn, NY"],
)
def test_scrub_preserves_usable_locales(locale):
    """City/state/zip must survive — they are what makes the search work."""
    scrubbed, removed = scrub_text(locale)
    assert scrubbed == locale
    assert removed == []


@pytest.mark.parametrize(
    "item",
    ["cherry tomatoes", "2 lb bag of rice", "12 oz coffee", "eggs, 18 count"],
)
def test_scrub_leaves_grocery_items_alone(item):
    assert scrub_text(item) == (item, [])


def test_scrub_keeps_city_when_dropping_street():
    scrubbed, removed = scrub_text("123 Elm Street, Austin, TX")
    assert "street address" in removed
    assert "Austin, TX" in scrubbed
    assert "Elm" not in scrubbed


# ── Redaction ──────────────────────────────────────────────────────────────

def test_redact_masks_env_secret(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "supersecretkey12345")
    assert "supersecretkey12345" not in redact("failed with key supersecretkey12345")


def test_redact_masks_api_key_url_param():
    """The real leak path: httpx exceptions embed the full request URL."""
    err = "GET https://serpapi.com/search?engine=google&api_key=abcd1234secret&num=5 failed"
    out = redact(err)
    assert "abcd1234secret" not in out
    assert "api_key=***" in out


def test_redact_masks_bearer_and_sk_keys():
    assert "abc123def456" not in redact("Authorization: Bearer abc123def456")
    assert "sk-livekey9999" not in redact("bad key sk-livekey9999")


def test_redact_ignores_short_values(monkeypatch):
    """A short env value must not blank innocent text."""
    monkeypatch.setenv("SERPAPI_KEY", "abc")
    assert redact("abc is fine here") == "abc is fine here"


# ── Search-layer enforcement ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_offline():
    ws.set_offline_mode(False)
    yield
    ws._OFFLINE_OVERRIDE = None


async def test_offline_mode_makes_no_network_call(monkeypatch):
    called = False

    async def boom(*a, **k):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(ws, "_ddgs_search", boom)
    monkeypatch.setattr(ws, "_serpapi_search", boom)
    monkeypatch.setattr(ws, "_ddg_html_scrape", boom)

    ws.set_offline_mode(True)
    assert await ws.web_search("Kroger cherry tomatoes Austin") == []
    assert not called


async def test_offline_mode_via_env(monkeypatch):
    monkeypatch.setenv("GROCERY_OFFLINE", "1")
    ws._OFFLINE_OVERRIDE = None
    assert ws.is_offline()


async def test_web_search_scrubs_query_before_sending(monkeypatch):
    seen: list[str] = []

    async def capture(query, max_results):
        seen.append(query)
        return [{"title": "t", "url": "u", "snippet": "s"}]

    monkeypatch.setattr(ws, "_ddgs_search", capture)
    monkeypatch.setattr(ws, "_PACING_DELAY_SECONDS", 0)

    await ws.web_search("tomatoes near 123 Elm Street call 512-555-0134 ray@x.com")

    assert len(seen) == 1
    sent = seen[0]
    assert "ray@x.com" not in sent
    assert "512-555-0134" not in sent
    assert "Elm" not in sent
    assert "tomatoes" in sent


async def test_scrub_logs_categories_not_values(monkeypatch, caplog):
    async def fake(query, max_results):
        return []

    monkeypatch.setattr(ws, "_ddgs_search", fake)
    monkeypatch.setattr(ws, "_ddg_html_scrape", fake)
    monkeypatch.setattr(ws, "_PACING_DELAY_SECONDS", 0)

    with caplog.at_level(logging.INFO, logger=ws.logger.name):
        await ws.web_search("tomatoes ray@x.com")

    text = caplog.text
    assert "email" in text
    assert "ray@x.com" not in text
