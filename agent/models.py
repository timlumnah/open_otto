"""Model backends. One tiny interface so the agent never depends on a vendor.

Backends use only the standard library (urllib), so open_otto has no runtime dependencies.
The HTTP call is injectable, which is how the tests cover these without network or keys.

Select a backend with --model or $OPEN_OTTO_MODEL:
    anthropic:claude-haiku-5-5     (needs $ANTHROPIC_API_KEY)
    ollama:llama3.1                (local, needs Ollama running; $OLLAMA_HOST optional)
    openrouter:<vendor>/<model>    (needs $OPENROUTER_API_KEY; one key, hundreds of models)
    openai:gpt-4o-mini             (any OpenAI-style server; $OPENAI_API_KEY, $OPENAI_BASE_URL)

Slow local models: raise the request timeout (seconds) with $OPEN_OTTO_TIMEOUT. Default 60.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
TIMEOUT_SECONDS = 60


class ModelError(Exception):
    """The model backend failed or returned something unusable."""


@dataclass(frozen=True)
class ModelResponse:
    text: str
    model: str


class Model(Protocol):
    name: str

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> ModelResponse: ...


PostFn = Callable[[str, dict, dict], dict]


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"content-type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(req, timeout=float(os.environ.get("OPEN_OTTO_TIMEOUT", TIMEOUT_SECONDS))) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # Never echo request headers: they hold the API key.
        detail = exc.read().decode(errors="replace")[:300]
        raise ModelError(f"HTTP {exc.code} from {url}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ModelError(f"could not reach {url}: {exc}") from None


class FakeModel:
    """Scripted model for tests and offline demos. Records every call."""

    def __init__(self, reply: str | list[str] | Callable[[str, str], str], name: str = "fake"):
        self.name = name
        self._reply = reply
        self.calls: list[tuple[str, str]] = []
        self.schemas: list[dict | None] = []

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> ModelResponse:
        self.calls.append((system, user))
        self.schemas.append(json_schema)
        if callable(self._reply):
            text = self._reply(system, user)
        elif isinstance(self._reply, list):
            text = self._reply[min(len(self.calls), len(self._reply)) - 1]
        else:
            text = self._reply
        return ModelResponse(text=text, model=self.name)


class AnthropicModel:
    def __init__(self, model_id: str, api_key: str | None = None, post: PostFn = _post_json,
                 max_tokens: int = 600):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ModelError("ANTHROPIC_API_KEY is not set")
        self.model_id = model_id
        self.name = f"anthropic:{model_id}"
        self._post = post
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> ModelResponse:
        # json_schema is accepted for interface parity and ignored: Claude follows the
        # JSON-only instruction reliably, and the reply is validated either way.
        data = self._post(
            ANTHROPIC_URL,
            {"x-api-key": self.api_key, "anthropic-version": ANTHROPIC_VERSION},
            {
                "model": self.model_id,
                "max_tokens": self._max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        try:
            blocks = data["content"]
            if not isinstance(blocks, list):
                raise TypeError("content is not a list")
            text = "".join(b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        except (KeyError, TypeError):
            raise ModelError(f"unexpected Anthropic response shape: {str(data)[:200]}") from None
        if not text.strip():
            raise ModelError("Anthropic returned no text")
        return ModelResponse(text=text, model=self.name)


class OllamaModel:
    def __init__(self, model_id: str, host: str | None = None, post: PostFn = _post_json):
        self.model_id = model_id
        self.name = f"ollama:{model_id}"
        self.host = (host or os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).rstrip("/")
        self._post = post

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> ModelResponse:
        payload = {
            "model": self.model_id,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_schema is not None:
            # Constrained decoding: small local models often break JSON formatting otherwise.
            payload["format"] = json_schema
        data = self._post(f"{self.host}/api/chat", {}, payload)
        try:
            text = data["message"]["content"]
        except (KeyError, TypeError):
            raise ModelError(f"unexpected Ollama response shape: {str(data)[:200]}") from None
        if not text.strip():
            raise ModelError("Ollama returned no text")
        return ModelResponse(text=text, model=self.name)


class OpenAICompatModel:
    """Any server that speaks the OpenAI chat-completions format (OpenRouter, OpenAI, vLLM...)."""

    def __init__(self, provider: str, model_id: str, base_url: str, api_key: str | None,
                 post: PostFn = _post_json, max_tokens: int = 1000):
        if not api_key:
            raise ModelError(f"no API key for '{provider}'. Set "
                             f"{'OPENROUTER_API_KEY' if provider == 'openrouter' else 'OPENAI_API_KEY'}")
        self.provider = provider
        self.model_id = model_id
        self.name = f"{provider}:{model_id}"
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self._post = post
        self._max_tokens = max_tokens

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> ModelResponse:
        payload = {
            "model": self.model_id,
            "max_tokens": self._max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_schema is not None:
            # Servers that do not support structured output ignore this; the reply is validated anyway.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "draft", "schema": json_schema},
            }
        data = self._post(f"{self.base_url}/chat/completions", {"authorization": f"Bearer {self._key}"}, payload)
        if isinstance(data, dict) and data.get("error"):
            # OpenRouter can report upstream failures inside an HTTP 200.
            raise ModelError(f"{self.provider} error: {str(data['error'])[:300]}")
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelError(f"unexpected {self.provider} response shape: {str(data)[:200]}") from None
        if not isinstance(text, str) or not text.strip():
            raise ModelError(f"{self.provider} returned no text (a reasoning model may have run out of tokens)")
        return ModelResponse(text=text, model=self.name)


def get_model(spec: str | None = None) -> Model:
    """Build a backend from 'provider:model'. Falls back to $OPEN_OTTO_MODEL."""
    spec = spec or os.environ.get("OPEN_OTTO_MODEL")
    if not spec:
        raise ModelError(
            "no model configured. Pass --model or set OPEN_OTTO_MODEL, "
            "for example anthropic:claude-haiku-5-5, openrouter:qwen/qwen-2.5-72b-instruct or ollama:llama3.1"
        )
    provider, _, model_id = spec.partition(":")
    if not model_id:
        raise ModelError(f"bad model spec '{spec}'. Use provider:model")
    if provider == "anthropic":
        return AnthropicModel(model_id)
    if provider == "ollama":
        return OllamaModel(model_id)
    if provider == "openrouter":
        return OpenAICompatModel(provider, model_id, os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL),
                                 os.environ.get("OPENROUTER_API_KEY"))
    if provider == "openai":
        return OpenAICompatModel(provider, model_id, os.environ.get("OPENAI_BASE_URL", OPENAI_BASE_URL),
                                 os.environ.get("OPENAI_API_KEY"))
    raise ModelError(f"unknown provider '{provider}'. Supported: anthropic, ollama, openrouter, openai")
