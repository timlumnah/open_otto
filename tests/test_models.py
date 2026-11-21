import pytest

from agent import models
from agent.models import AnthropicModel, FakeModel, ModelError, OllamaModel, OpenAICompatModel


class SpyPost:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, url, headers, payload):
        self.calls.append((url, headers, payload))
        return self.response


# ---- FakeModel ----

def test_fake_model_string_callable_and_list():
    assert FakeModel("hi").complete("s", "u").text == "hi"
    assert FakeModel(lambda s, u: u.upper()).complete("s", "abc").text == "ABC"
    seq = FakeModel(["a", "b"])
    assert [seq.complete("s", "u").text for _ in range(3)] == ["a", "b", "b"]


def test_fake_model_records_calls():
    m = FakeModel("x")
    m.complete("sys", "usr")
    assert m.calls == [("sys", "usr")]


# ---- Anthropic ----

def test_anthropic_request_shape_and_text_join():
    spy = SpyPost({"content": [{"type": "text", "text": "Hel"}, {"type": "text", "text": "lo"}]})
    m = AnthropicModel("claude-haiku-5-5", api_key="sk-test", post=spy)
    resp = m.complete("SYS", "USER")
    url, headers, payload = spy.calls[0]
    assert url == models.ANTHROPIC_URL
    assert headers["x-api-key"] == "sk-test" and headers["anthropic-version"] == "2023-06-01"
    assert payload["model"] == "claude-haiku-5-5" and payload["system"] == "SYS"
    assert payload["messages"] == [{"role": "user", "content": "USER"}]
    assert (resp.text, resp.model) == ("Hello", "anthropic:claude-haiku-5-5")


def test_anthropic_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ModelError, match="ANTHROPIC_API_KEY"):
        AnthropicModel("m")


def test_anthropic_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    spy = SpyPost({"content": [{"type": "text", "text": "ok"}]})
    AnthropicModel("m", post=spy).complete("s", "u")
    assert spy.calls[0][1]["x-api-key"] == "from-env"


@pytest.mark.parametrize("bad", [{}, {"content": "str"}, {"content": [{"type": "text"}]}, {"content": []}])
def test_anthropic_bad_response_shapes(bad):
    with pytest.raises(ModelError):
        AnthropicModel("m", api_key="k", post=SpyPost(bad)).complete("s", "u")


# ---- Ollama ----

def test_ollama_request_shape(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    spy = SpyPost({"message": {"content": "pong"}})
    resp = OllamaModel("llama3.1", post=spy).complete("SYS", "USER")
    url, headers, payload = spy.calls[0]
    assert url == "http://localhost:11434/api/chat"
    assert payload["stream"] is False
    assert payload["messages"][0] == {"role": "system", "content": "SYS"}
    assert (resp.text, resp.model) == ("pong", "ollama:llama3.1")


def test_ollama_host_from_env_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://box:9999/")
    spy = SpyPost({"message": {"content": "x"}})
    OllamaModel("m", post=spy).complete("s", "u")
    assert spy.calls[0][0] == "http://box:9999/api/chat"


@pytest.mark.parametrize("bad", [{}, {"message": {}}, {"message": {"content": "  "}}])
def test_ollama_bad_response_shapes(bad):
    with pytest.raises(ModelError):
        OllamaModel("m", post=SpyPost(bad)).complete("s", "u")


# ---- OpenAI-compatible (OpenRouter and friends) ----

OK = {"choices": [{"message": {"content": "hello"}}]}


def test_openrouter_request_shape(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    m = models.get_model("openrouter:qwen/qwen3-8b:free")
    assert m.name == "openrouter:qwen/qwen3-8b:free"  # slashes and colons in the id survive
    spy = SpyPost(OK)
    m._post = spy
    resp = m.complete("SYS", "USER")
    url, headers, payload = spy.calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers == {"authorization": "Bearer or-key"}
    assert payload["model"] == "qwen/qwen3-8b:free"
    assert payload["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert "response_format" not in payload
    assert resp.text == "hello"


def test_openai_provider_uses_base_url_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1/")
    m = models.get_model("openai:my-model")
    m._post = spy = SpyPost(OK)
    m.complete("s", "u")
    assert spy.calls[0][0] == "http://localhost:8000/v1/chat/completions"


def test_openai_compat_sends_schema_as_response_format():
    spy = SpyPost(OK)
    OpenAICompatModel("openrouter", "m", "http://x", "k", post=spy).complete("s", "u", json_schema=SCHEMA)
    rf = spy.calls[0][2]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == SCHEMA


def test_openai_compat_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ModelError, match="OPENROUTER_API_KEY"):
        models.get_model("openrouter:x/y")


@pytest.mark.parametrize("bad", [
    {}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
    {"choices": [{"message": {"content": "  "}}]},
    {"error": {"message": "upstream down"}},
])
def test_openai_compat_bad_response_shapes(bad):
    with pytest.raises(ModelError):
        OpenAICompatModel("openrouter", "m", "http://x", "k", post=SpyPost(bad)).complete("s", "u")


def test_openai_compat_error_does_not_leak_key():
    m = OpenAICompatModel("openrouter", "m", "http://x", "sk-secret-999",
                          post=SpyPost({"error": {"message": "rate limited"}}))
    with pytest.raises(ModelError) as exc:
        m.complete("s", "u")
    assert "sk-secret-999" not in str(exc.value)


# ---- get_model ----

def test_get_model_dispatch(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert isinstance(models.get_model("anthropic:claude-haiku-5-5"), AnthropicModel)
    assert isinstance(models.get_model("ollama:llama3.1"), OllamaModel)


def test_get_model_uses_env_default(monkeypatch):
    monkeypatch.setenv("OPEN_OTTO_MODEL", "ollama:qwen")
    assert models.get_model().name == "ollama:qwen"


def test_get_model_errors(monkeypatch):
    monkeypatch.delenv("OPEN_OTTO_MODEL", raising=False)
    with pytest.raises(ModelError, match="no model configured"):
        models.get_model()
    with pytest.raises(ModelError, match="Use provider:model"):
        models.get_model("anthropic")
    with pytest.raises(ModelError, match="unknown provider"):
        models.get_model("mystery:x")


def test_http_error_does_not_leak_api_key(monkeypatch):
    import io
    import urllib.error

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"bad key"}'))

    monkeypatch.setattr(models.urllib.request, "urlopen", boom)
    with pytest.raises(ModelError) as exc:
        models._post_json("https://x.test", {"x-api-key": "sk-secret-123"}, {})
    assert "401" in str(exc.value) and "sk-secret-123" not in str(exc.value)


def test_network_failure_is_a_model_error(monkeypatch):
    import urllib.error

    def down(req, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(models.urllib.request, "urlopen", down)
    with pytest.raises(ModelError, match="could not reach"):
        models._post_json("https://x.test", {}, {})


# ---- constrained JSON output ----

SCHEMA = {"type": "object", "properties": {"body": {"type": "string"}}, "required": ["body"]}


def test_ollama_sends_schema_as_format_only_when_given():
    spy = SpyPost({"message": {"content": "{}"}})
    OllamaModel("m", post=spy).complete("s", "u", json_schema=SCHEMA)
    assert spy.calls[0][2]["format"] == SCHEMA
    spy2 = SpyPost({"message": {"content": "x"}})
    OllamaModel("m", post=spy2).complete("s", "u")
    assert "format" not in spy2.calls[0][2]


def test_anthropic_accepts_and_ignores_schema():
    spy = SpyPost({"content": [{"type": "text", "text": "ok"}]})
    AnthropicModel("m", api_key="k", post=spy).complete("s", "u", json_schema=SCHEMA)
    assert "format" not in spy.calls[0][2] and "json_schema" not in spy.calls[0][2]


def test_fake_model_records_schema():
    m = FakeModel("x")
    m.complete("s", "u", json_schema=SCHEMA)
    m.complete("s", "u")
    assert m.schemas == [SCHEMA, None]


def test_request_timeout_comes_from_env(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake_urlopen(req, timeout):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(models.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("OPEN_OTTO_TIMEOUT", raising=False)
    models._post_json("https://x.test", {}, {})
    assert seen["timeout"] == 60.0
    monkeypatch.setenv("OPEN_OTTO_TIMEOUT", "300")
    models._post_json("https://x.test", {}, {})
    assert seen["timeout"] == 300.0
