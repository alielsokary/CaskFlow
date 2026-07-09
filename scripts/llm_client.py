"""Provider-agnostic LLM abstraction for cask classification."""
# LLMClient is an abstract base class. Concrete implementations are selected at
# runtime via the `LLM_PROVIDER` env var. Each provider builds the same prompt
# (from prompts.py) and requests strict JSON output via the provider's structured-
# output mode where available; the base class then validates the result against
# the active CategoryCatalog.
#
# Validation failures (invalid IDs, malformed JSON, primary in TRAIT_CATEGORIES)
# raise ClassificationError, which the orchestrator logs and skips — the cask
# remains unmapped until the next pipeline run.
from __future__ import annotations

import json
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from prompts import (
    CategoryCatalog,
    TRAIT_CATEGORIES,
    build_system_prompt,
    build_user_prompt,
)


@dataclass(frozen=True)
class Classification:
    primary: str
    secondary: list[str]
    confidence: float
    reason: str


class ClassificationError(Exception):
    """Raised when an LLM response can't be parsed/validated. Caller skips the cask."""


class LLMClient(ABC):
    """Abstract base. Subclasses override _generate(); base class handles prompt + validation."""

    def __init__(self, catalog: CategoryCatalog):
        """Bind the catalog and precompute the system prompt."""
        self.catalog = catalog
        self.system_prompt = build_system_prompt(catalog)

    def classify(self, cask: dict, homepage_meta: dict | None) -> Classification:
        user_prompt = build_user_prompt(cask, homepage_meta)
        raw = self._generate(self.system_prompt, user_prompt)
        return self._parse_and_validate(raw, cask["token"])

    @abstractmethod
    def _generate(self, system: str, user: str) -> str:
        """Return raw model output (expected to be JSON)."""

    def _parse_and_validate(self, raw: str, token: str) -> Classification:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ClassificationError(f"{token}: invalid JSON ({e})")

        try:
            primary = payload["primary"]
            secondary = payload.get("secondary", []) or []
            confidence = float(payload.get("confidence", 0.0))
            reason = payload.get("reason", "")
        except (KeyError, TypeError) as e:
            raise ClassificationError(f"{token}: missing field ({e})")

        if primary not in self.catalog.primary_ids:
            if primary in TRAIT_CATEGORIES:
                raise ClassificationError(f"{token}: trait `{primary}` cannot be primary")
            raise ClassificationError(f"{token}: unknown primary `{primary}`")

        if not isinstance(secondary, list):
            raise ClassificationError(f"{token}: secondary must be a list")
        for s in secondary:
            if s not in self.catalog.secondary_ids:
                raise ClassificationError(f"{token}: unknown secondary `{s}`")
        if primary in secondary:
            raise ClassificationError(f"{token}: primary `{primary}` duplicated in secondary")
        if len(secondary) > 2:
            raise ClassificationError(f"{token}: too many secondary entries ({len(secondary)})")

        return Classification(
            primary=primary,
            secondary=list(secondary),
            confidence=max(0.0, min(1.0, confidence)),
            reason=reason,
        )

    @classmethod
    def from_env(cls, catalog: CategoryCatalog) -> "LLMClient":
        provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
        try:
            client_cls = _PROVIDERS[provider]
        except KeyError:
            raise ValueError(
                f"Unknown LLM_PROVIDER `{provider}`. Valid: {sorted(_PROVIDERS)}"
            )
        return client_cls(catalog)


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------

def _retry(fn, attempts: int = 3, base_delay: float = 1.0):
    """Exponential backoff with jitter for transient provider errors."""
    for i in range(attempts):
        try:
            return fn()
        except Exception:  # network / rate-limit / 5xx
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** i) + random.SystemRandom().uniform(0, 0.5))


class AnthropicClient(LLMClient):
    """Default — Claude Sonnet 5 with structured outputs via the official anthropic SDK."""

    MODEL = "claude-sonnet-5"

    def __init__(self, catalog: CategoryCatalog):
        """Create the anthropic SDK client. Lazy import keeps unused providers dependency-free."""
        super().__init__(catalog)
        import anthropic  # lazy import

        self._client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY
        # Constrained decoding: the model can only emit valid category IDs, so
        # the VALIDATE failure class shrinks to logic errors (trait-as-primary,
        # duplicates, >2 secondary) which _parse_and_validate still enforces.
        self._output_format = {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "primary": {"type": "string", "enum": sorted(catalog.primary_ids)},
                    "secondary": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(catalog.secondary_ids)},
                    },
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["primary", "secondary", "confidence", "reason"],
                "additionalProperties": False,
            },
        }

    def _generate(self, system: str, user: str) -> str:
        def call():
            resp = self._client.messages.create(
                model=self.MODEL,
                # Adaptive thinking (Sonnet 5 default) shares this budget with
                # the JSON answer, so leave headroom beyond the ~100-token output.
                max_tokens=2048,
                # cache_control: the system prompt is identical across every cask
                # in a run — cached reads cost ~0.1x after the first request.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "medium", "format": self._output_format},
            )
            # With thinking enabled the first block may be a thinking block —
            # the JSON payload is in the text block.
            return next(b.text for b in resp.content if b.type == "text")

        return _retry(call)


class OpenAIClient(LLMClient):
    """GPT-4o-mini with response_format=json_object."""

    MODEL = "gpt-4o-mini"

    def __init__(self, catalog: CategoryCatalog):
        """Create the OpenAI SDK client. Lazy import keeps unused providers dependency-free."""
        super().__init__(catalog)
        from openai import OpenAI  # lazy import

        self._client = OpenAI()  # picks up OPENAI_API_KEY

    def _generate(self, system: str, user: str) -> str:
        def call():
            resp = self._client.chat.completions.create(
                model=self.MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=512,
            )
            return resp.choices[0].message.content or ""

        return _retry(call)


class GroqClient(LLMClient):
    """Groq free tier — Llama 3.3 70B with JSON-mode."""

    MODEL = "llama-3.3-70b-versatile"

    def __init__(self, catalog: CategoryCatalog):
        """Create the Groq SDK client. Lazy import keeps unused providers dependency-free."""
        super().__init__(catalog)
        from groq import Groq  # lazy import

        self._client = Groq()  # picks up GROQ_API_KEY

    def _generate(self, system: str, user: str) -> str:
        def call():
            resp = self._client.chat.completions.create(
                model=self.MODEL,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=512,
            )
            return resp.choices[0].message.content or ""

        return _retry(call)


class CloudflareWorkersAIClient(LLMClient):
    """Cloudflare Workers AI free tier — Llama 3.1 via REST."""

    MODEL = "@cf/meta/llama-3.1-70b-instruct"

    def __init__(self, catalog: CategoryCatalog):
        """Read Cloudflare credentials from the environment. Lazy import keeps unused providers dependency-free."""
        super().__init__(catalog)
        import requests  # lazy import

        self._requests = requests
        self._account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        self._token = os.environ["CLOUDFLARE_API_TOKEN"]

    def _generate(self, system: str, user: str) -> str:
        url = (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{self._account}/ai/run/{self.MODEL}"
        )

        def call():
            r = self._requests.post(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "max_tokens": 512,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["result"]["response"]

        return _retry(call)


class MockClient(LLMClient):
    """Deterministic stub for unit tests and dry-runs."""

    # Picks `primary` based on simple keyword rules so tests have predictable
    # output without burning API credits. Always returns confidence=0.5.

    KEYWORDS: dict[str, list[str]] = {
        "developerTools": ["ide", "code", "git", "docker", "ssh", "terminal", "api"],
        "browsers": ["browser", "chromium", "firefox"],
        "communication": ["chat", "mail", "slack", "discord"],
        "designGraphics": ["design", "draw", "photo", "vector"],
        "audioMusic": ["music", "audio", "synth", "podcast"],
        "videoMedia": ["video", "stream", "obs", "subtitle"],
        "games": ["game", "emulator", "steam"],
        "securityPrivacy": ["vpn", "password", "encrypt", "firewall"],
        "financeCrypto": ["wallet", "crypto", "trading", "tax", "invoice"],
        "officeTools": ["office", "word", "excel", "powerpoint", "libreoffice"],
        "screensaverWallpaper": ["screensaver", "wallpaper"],
    }

    def _generate(self, system: str, user: str) -> str:
        text = user.lower()
        primary = "utilities"
        for cat, kws in self.KEYWORDS.items():
            if any(k in text for k in kws):
                primary = cat
                break
        secondary = ["ai"] if any(k in text for k in ["llm", "chatbot", "gpt", "ai assistant"]) else []
        return json.dumps(
            {
                "primary": primary,
                "secondary": secondary,
                "confidence": 0.5,
                "reason": "mock client: keyword match",
            }
        )


_PROVIDERS: dict[str, type[LLMClient]] = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "groq": GroqClient,
    "cloudflare": CloudflareWorkersAIClient,
    "mock": MockClient,
}
