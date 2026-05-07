"""OpenAI-compatible LLM client.

Speaks one dialect (OpenAI Chat Completions) and lets `api_base` point to
either OpenAI, Ollama's ``/v1``, Azure, OpenRouter, Gemini's
``generativelanguage.googleapis.com/v1beta/openai/`` endpoint, etc.
For Anthropic (Claude), which has no OpenAI-compatible endpoint, we
dispatch onto the native ``anthropic`` SDK; callers still get the same
``chat_json`` interface.

Design notes:
  - Synchronous. The worker already isolates LLM work in a thread.
  - We require JSON output; caller asks for `response_format={"type":"json_object"}`.
    Some gateways (Ollama, Anthropic) ignore / reject it, so the classifier
    also tolerates surrounding text and extracts the first `{...}`.
  - Retries are on transport errors and 5xx; 4xx other than 429 is a hard
    fail (no point in retrying an auth error).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

log = logging.getLogger("paperprism.llm")

DEFAULT_API_BASE_OPENAI = "https://api.openai.com/v1"
# Providers that need an explicit api_base (no reasonable default exists).
_BASELESS_PROVIDERS = {"openai", "anthropic"}
# Providers dispatched through the native Anthropic SDK.
_ANTHROPIC_PROVIDERS = {"anthropic", "claude"}


class LLMConfigError(Exception):
    pass


class LLMError(Exception):
    """Non-retryable LLM failure (auth, schema, config)."""


class LLMTransientError(Exception):
    """Retry-worthy LLM failure (timeout, 5xx, rate limit exhausted)."""


@dataclass
class LLMConfig:
    version: int
    provider: str
    model: str
    api_base: str | None
    api_key_env: str | None
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    abstract_char_limit: int
    pdf_head_char_limit: int
    pdf_full_char_limit: int = 8000
    auto_tag_on_ingest: bool = True

    @classmethod
    def load(cls, path: Path) -> "LLMConfig":
        if not path.exists():
            raise LLMConfigError(f"LLM config missing: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise LLMConfigError("LLM YAML must be a mapping")
        try:
            return cls(
                version=int(raw.get("version", 1)),
                provider=str(raw.get("provider", "openai")).lower(),
                model=str(raw["model"]),
                api_base=raw.get("api_base") or None,
                api_key_env=raw.get("api_key_env") or None,
                temperature=float(raw.get("temperature", 0.0)),
                max_output_tokens=int(raw.get("max_output_tokens", 600)),
                timeout_seconds=float(raw.get("timeout_seconds", 60)),
                max_retries=int(raw.get("max_retries", 2)),
                abstract_char_limit=int(raw.get("abstract_char_limit", 2000)),
                pdf_head_char_limit=int(raw.get("pdf_head_char_limit", 1500)),
                pdf_full_char_limit=int(raw.get("pdf_full_char_limit", 8000)),
                auto_tag_on_ingest=bool(raw.get("auto_tag_on_ingest", True)),
            )
        except KeyError as exc:
            raise LLMConfigError(f"LLM config missing required key: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise LLMConfigError(f"LLM config malformed: {exc}") from exc

    def resolve_api_key(self) -> str | None:
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None


class LLMClient:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        self._is_anthropic = cfg.provider in _ANTHROPIC_PROVIDERS
        api_key = cfg.resolve_api_key()

        if self._is_anthropic:
            # Native Anthropic SDK: no OpenAI-compatible endpoint.
            if not api_key:
                raise LLMConfigError(
                    f"provider=anthropic but env "
                    f"${cfg.api_key_env or 'ANTHROPIC_API_KEY'} is empty"
                )
            try:
                # Lazy import so installs without `anthropic` still work
                # for OpenAI / Ollama / Gemini-OpenAI-compatible users.
                from anthropic import Anthropic  # type: ignore
            except ImportError as exc:
                raise LLMConfigError(
                    "provider=anthropic requires the `anthropic` package; "
                    "run `pip install anthropic` in the Agent's venv."
                ) from exc
            base_url = cfg.api_base or None  # SDK default is api.anthropic.com
            kwargs: dict = {
                "api_key": api_key,
                "timeout": cfg.timeout_seconds,
                "max_retries": 0,
            }
            if base_url:
                kwargs["base_url"] = base_url
            self._anthropic = Anthropic(**kwargs)
            self._client = None
            self._base_url = base_url or "https://api.anthropic.com"
            log.info(
                "LLM client ready provider=%s base=%s model=%s",
                cfg.provider, self._base_url, cfg.model,
            )
            return

        # OpenAI-compatible path (openai, ollama, deepseek, gemini-oai, ...).
        if cfg.provider == "openai" and not api_key:
            # Fail loudly at construction rather than mid-task.
            raise LLMConfigError(
                f"provider=openai but env ${cfg.api_key_env or 'OPENAI_API_KEY'} is empty"
            )
        # Ollama accepts any non-empty string as the key.
        effective_key = api_key or "not-needed"
        base_url = cfg.api_base or (
            DEFAULT_API_BASE_OPENAI if cfg.provider == "openai" else None
        )
        if base_url is None:
            raise LLMConfigError(
                f"provider={cfg.provider!r} requires an explicit api_base"
            )
        self._client = OpenAI(
            api_key=effective_key,
            base_url=base_url,
            timeout=cfg.timeout_seconds,
            max_retries=0,  # we do our own retries
        )
        self._anthropic = None
        self._base_url = base_url
        log.info(
            "LLM client ready provider=%s base=%s model=%s",
            cfg.provider, base_url, cfg.model,
        )

    def chat_json(
        self,
        *,
        system: str,
        user: str,
    ) -> str:
        """Send a chat request and return the raw assistant text.

        We ask for JSON mode when the backend supports it; callers parse
        the string themselves so they can recover from providers that
        don't honour `response_format` (Ollama, Anthropic).
        """
        if self._is_anthropic:
            return self._chat_json_anthropic(system=system, user=user)
        return self._chat_json_openai(system=system, user=user)

    def _chat_json_openai(self, *, system: str, user: str) -> str:
        assert self._client is not None
        last_err: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 2):  # +1 initial try
            try:
                resp = self._client.chat.completions.create(
                    model=self.cfg.model,
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_output_tokens,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                content = resp.choices[0].message.content or ""
                if not content.strip():
                    raise LLMError("empty completion")
                return content
            except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                last_err = exc
                log.warning(
                    "LLM transient error (attempt %s/%s): %s",
                    attempt, self.cfg.max_retries + 1, exc,
                )
                time.sleep(min(2.0 * attempt, 10.0))
                continue
            except APIStatusError as exc:
                # 5xx retry, 4xx (except 429 handled above) fail fast.
                if 500 <= exc.status_code < 600:
                    last_err = exc
                    log.warning("LLM 5xx (attempt %s): %s", attempt, exc)
                    time.sleep(min(2.0 * attempt, 10.0))
                    continue
                raise LLMError(f"LLM {exc.status_code}: {exc}") from exc
            except APIError as exc:
                raise LLMError(str(exc)) from exc
        raise LLMTransientError(
            f"LLM request exhausted retries: {last_err}"
        )

    def _chat_json_anthropic(self, *, system: str, user: str) -> str:
        """Anthropic Messages API path.

        Claude has no `response_format={type:json_object}`, but our
        system prompt already asks for "Output ONLY the JSON object",
        and `classifier._parse_json` recovers from stray text, so we
        just return the assistant text block as-is.
        """
        # Lazy import keeps environments without anthropic installed from
        # breaking the module import path for OpenAI-only users.
        from anthropic import (  # type: ignore
            APIConnectionError as AnthropicAPIConnectionError,
            APIError as AnthropicAPIError,
            APIStatusError as AnthropicAPIStatusError,
            APITimeoutError as AnthropicAPITimeoutError,
            RateLimitError as AnthropicRateLimitError,
        )

        assert self._anthropic is not None
        last_err: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 2):
            try:
                resp = self._anthropic.messages.create(
                    model=self.cfg.model,
                    max_tokens=self.cfg.max_output_tokens,
                    temperature=self.cfg.temperature,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                # ``content`` is a list of blocks; collect any text blocks.
                parts: list[str] = []
                for block in resp.content or []:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(text)
                content = "".join(parts)
                if not content.strip():
                    raise LLMError("empty completion")
                return content
            except (
                AnthropicAPITimeoutError,
                AnthropicAPIConnectionError,
                AnthropicRateLimitError,
            ) as exc:
                last_err = exc
                log.warning(
                    "LLM (anthropic) transient error (attempt %s/%s): %s",
                    attempt, self.cfg.max_retries + 1, exc,
                )
                time.sleep(min(2.0 * attempt, 10.0))
                continue
            except AnthropicAPIStatusError as exc:
                status = getattr(exc, "status_code", None)
                if status is not None and 500 <= status < 600:
                    last_err = exc
                    log.warning("LLM (anthropic) 5xx (attempt %s): %s", attempt, exc)
                    time.sleep(min(2.0 * attempt, 10.0))
                    continue
                raise LLMError(f"Anthropic {status}: {exc}") from exc
            except AnthropicAPIError as exc:
                raise LLMError(str(exc)) from exc
        raise LLMTransientError(
            f"LLM request exhausted retries: {last_err}"
        )

    @property
    def provider_label(self) -> str:
        return f"{self.cfg.provider}/{self.cfg.model}"
