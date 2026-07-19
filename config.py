"""
Shared configuration — single source of truth for the Kimi K3 model.

Importing this module guarantees that load_dotenv() has run,
so MOONSHOT_API_KEY is available before any Agent is constructed.
"""
from __future__ import annotations
import os
from functools import lru_cache
from dotenv import load_dotenv
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

# ── Ensure .env is loaded BEFORE any model creation ─────────────────────────
load_dotenv()

KIMI_BASE_URL = "https://api.moonshot.ai/v1"
KIMI_MODEL_ID = "kimi-k3"


@lru_cache(maxsize=1)
def get_kimi_model() -> OpenAIChatModel:
    """Return a cached Kimi K3 model instance (single shared provider/client)."""
    api_key = os.environ.get("MOONSHOT_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "MOONSHOT_API_KEY environment variable is not set.\n"
            "Get your key at: https://platform.moonshot.ai\n"
            "Then: export MOONSHOT_API_KEY=your_key_here"
        )
    return OpenAIChatModel(
        KIMI_MODEL_ID,
        provider=OpenAIProvider(base_url=KIMI_BASE_URL, api_key=api_key),
    )


def kimi_model_settings() -> OpenAIChatModelSettings | None:
    """
    Model settings shared by all agents.

    KIMI_REASONING_EFFORT env var controls reasoning effort (default "max").
    Set it to "off" to omit the parameter entirely, in case the endpoint
    rejects it.
    """
    effort = os.environ.get("KIMI_REASONING_EFFORT", "max").strip().lower()
    if effort in ("off", "none", ""):
        return None
    return OpenAIChatModelSettings(openai_reasoning_effort=effort)  # type: ignore[typeddict-item]
