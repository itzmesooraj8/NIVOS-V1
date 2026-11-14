"""Direct cloud API router — bypasses the engine system entirely.

Reads API keys from the process environment, with a legacy
~/.nivos/cloud-keys.env fallback for non-desktop/manual setups. Uses
httpx directly so no cloud SDK packages are required.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any, Sequence

import httpx

from nivos.core.paths import get_config_dir
from nivos.core.types import Message

# ---------------------------------------------------------------------------
# Key / provider detection
# ---------------------------------------------------------------------------

_CLOUD_ENV_FILE = get_config_dir() / "cloud-keys.env"
_APPROVALS_FILE = get_config_dir() / "cloud_approvals.json"

SENSITIVE_TAG_OPEN = "<nivos-sensitive"
SENSITIVE_TAG_CLOSE = "</nivos-sensitive>"
_SENSITIVE_TAG_RE = re.compile(
    r"<nivos-sensitive(?:\s+[^>]*)?>[\s\S]*?</nivos-sensitive>", re.IGNORECASE
)


def wrap_sensitive_content(text: str, source: str = "vault") -> str:
    """Wrap private vault, memory, or me.md content in a sensitivity tag."""
    if not text:
        return ""
    return f'<nivos-sensitive source="{source}">\n{text}\n</nivos-sensitive>'


def contains_sensitive_content(text: str) -> bool:
    """Check whether a string contains sensitivity tags."""
    if not text:
        return False
    return SENSITIVE_TAG_OPEN in text and SENSITIVE_TAG_CLOSE in text


def is_cloud_dispatch_auto_approved(
    provider_id: str, messages: Sequence[Message]
) -> bool:
    """Check if dispatch to provider is auto-approved under 'Allow always'.

    Requirements:
    1. Provider must be in always_allowed list.
    2. NONE of the messages can contain tagged sensitive content (me.md, memory, vault).
       If tagged content is present, explicit user confirmation is always required.
    """
    if not is_provider_always_allowed(provider_id):
        return False

    for m in messages:
        c = m.content or ""
        if contains_sensitive_content(c):
            return False

    return True

_OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-")
_ANTHROPIC_PREFIXES = ("claude-",)
_GOOGLE_PREFIXES = ("gemini-",)
_MINIMAX_PREFIXES = ("MiniMax-",)
# Atlas Cloud IDs are already "vendor/model", so they must be recognised by
# their routing prefix before the generic '"/" means OpenRouter' fallback
# below — otherwise "atlascloud/openai/gpt-4.1-mini" is sent to OpenRouter.
_ATLASCLOUD_PREFIX = "atlascloud/"

# HuggingFace orgs that host local-only quantised models — never route to cloud.
_LOCAL_HF_ORGS = (
    "mlx-community/",
    "bartowski/",
    "unsloth/",
    "lmstudio-community/",
)


def validate_key_format(key_name: str, key_value: str) -> tuple[bool, str]:
    """Validate API key format (e.g. AIza... for Google AI Studio)."""
    val = (key_value or "").strip()
    if not val:
        return False, "API key cannot be empty."

    k_clean = key_name.upper()
    if "GEMINI" in k_clean or "GOOGLE" in k_clean:
        if not val.startswith("AIza"):
            return False, "Unexpected key format: Google AI Studio keys typically start with 'AIza...'"
    elif "ANTHROPIC" in k_clean:
        if not val.startswith("sk-ant-"):
            return False, "Unexpected key format: Anthropic keys typically start with 'sk-ant-...'"
    elif "OPENAI" in k_clean:
        if not val.startswith("sk-"):
            return False, "Unexpected key format: OpenAI keys typically start with 'sk-...'"

    return True, ""


def probe_cloud_key(key_name: str, key_value: str) -> dict[str, Any]:
    """Make a minimal probe request to verify key validity, returning status and plain words."""
    val = (key_value or "").strip()
    valid, format_reason = validate_key_format(key_name, val)
    if not valid:
        return {"status": "invalid_format", "message": format_reason}

    k_clean = key_name.upper()
    try:
        if "GEMINI" in k_clean or "GOOGLE" in k_clean:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={val}"
            resp = httpx.get(url, timeout=8.0)
            if resp.status_code == 200:
                return {"status": "ok", "message": "Key is valid and working."}
            elif resp.status_code in (400, 403) or "API_KEY_INVALID" in resp.text:
                return {"status": "invalid_key", "message": "Invalid API key."}
            elif resp.status_code == 429 or "RESOURCE_EXHAUSTED" in resp.text:
                return {"status": "rate_limit", "message": "Rate limit exceeded on this API key."}
            else:
                return {"status": "error", "message": f"Provider error (HTTP {resp.status_code})"}

        elif "OPENAI" in k_clean:
            resp = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {val}"}, timeout=8.0)
            if resp.status_code == 200:
                return {"status": "ok", "message": "Key is valid and working."}
            elif resp.status_code in (401, 403):
                return {"status": "invalid_key", "message": "Invalid API key."}
            elif resp.status_code == 429:
                return {"status": "rate_limit", "message": "Rate limit exceeded on this API key."}
            else:
                return {"status": "error", "message": f"Provider error (HTTP {resp.status_code})"}

        elif "ANTHROPIC" in k_clean:
            resp = httpx.get("https://api.anthropic.com/v1/models", headers={"x-api-key": val, "anthropic-version": "2023-06-01"}, timeout=8.0)
            if resp.status_code == 200:
                return {"status": "ok", "message": "Key is valid and working."}
            elif resp.status_code in (401, 403):
                return {"status": "invalid_key", "message": "Invalid API key."}
            elif resp.status_code == 429:
                return {"status": "rate_limit", "message": "Rate limit exceeded on this API key."}
            else:
                return {"status": "error", "message": f"Provider error (HTTP {resp.status_code})"}

        else:
            return {"status": "ok", "message": "Key format accepted."}

    except Exception as exc:
        return {"status": "error", "message": f"Connection error: {exc}"}


def _load_keys() -> dict[str, str]:
    """Read available cloud keys every call so live updates are picked up."""
    keys: dict[str, str] = {}
    # File first, then fall back to process environment
    if _CLOUD_ENV_FILE.exists():
        for raw in _CLOUD_ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    # Process env can override (e.g. during testing)
    for name in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "MINIMAX_API_KEY",
        "ATLASCLOUD_API_KEY",
    ):
        val = os.environ.get(name)
        if val:
            keys[name] = val
    return keys


def get_available_cloud_provider() -> tuple[str, str, str] | None:
    """Return the preferred available cloud provider, its display name, and default model.

    Priority:
      1. Google Gemini (free tier first): gemini-2.5-flash
      2. OpenAI: gpt-4o
      3. Anthropic: claude-sonnet-4-20250514
      4. OpenRouter: openrouter/auto
      5. Atlas Cloud: atlascloud/openai/gpt-4.1-mini
      6. MiniMax: MiniMax-M2.5
    """
    keys = _load_keys()
    if keys.get("GEMINI_API_KEY") or keys.get("GOOGLE_API_KEY"):
        return ("google", "Google Gemini", "gemini-2.5-flash")
    if keys.get("OPENAI_API_KEY"):
        return ("openai", "OpenAI", "gpt-4o")
    if keys.get("ANTHROPIC_API_KEY"):
        return ("anthropic", "Anthropic", "claude-sonnet-4-20250514")
    if keys.get("OPENROUTER_API_KEY"):
        return ("openrouter", "OpenRouter", "openrouter/auto")
    if keys.get("ATLASCLOUD_API_KEY"):
        return ("atlascloud", "Atlas Cloud", "atlascloud/openai/gpt-4.1-mini")
    if keys.get("MINIMAX_API_KEY"):
        return ("minimax", "MiniMax", "MiniMax-M2.5")
    return None


def is_provider_always_allowed(provider_id: str) -> bool:
    """Check if the user has chosen 'Allow always' for this cloud provider."""
    if not _APPROVALS_FILE.exists():
        return False
    try:
        data = json.loads(_APPROVALS_FILE.read_text(encoding="utf-8"))
        return provider_id.lower() in [p.lower() for p in data.get("always_allowed", [])]
    except Exception:
        return False


def set_provider_always_allowed(provider_id: str) -> None:
    """Record that the user chose 'Allow always' for this cloud provider."""
    data = {}
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    if _APPROVALS_FILE.exists():
        try:
            data = json.loads(_APPROVALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    allowed = set(data.get("always_allowed", []))
    allowed.add(provider_id.lower())
    data["always_allowed"] = sorted(list(allowed))
    _APPROVALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def clear_provider_approvals() -> None:
    """Clear all saved provider approvals (useful for testing or resetting)."""
    if _APPROVALS_FILE.exists():
        try:
            _APPROVALS_FILE.unlink()
        except Exception:
            pass


def sanitize_messages_for_cloud(messages: Sequence[Message]) -> list[Message]:
    """Sanitize message history by strictly stripping tagged sensitive content."""
    sanitized: list[Message] = []
    for m in messages:
        content = m.content or ""
        role = m.role.value if hasattr(m.role, "value") else str(m.role)

        # 1. Strip tagged sensitive blocks cleanly
        if contains_sensitive_content(content):
            content = _SENSITIVE_TAG_RE.sub("", content).strip()

        # 2. Legacy fallback for system messages only (never strip user messages by keyword)
        if role == "system":
            lower_c = content.lower()
            if "## user profile (from me.md)" in lower_c or "[vault note:" in lower_c:
                lines = content.splitlines()
                cleaned_lines = []
                skip = False
                for line in lines:
                    l_lower = line.lower()
                    if "## user profile" in l_lower or "[vault note:" in l_lower:
                        skip = True
                        continue
                    if skip and line.startswith("#"):
                        skip = False
                    if not skip:
                        cleaned_lines.append(line)
                content = "\n".join(cleaned_lines).strip()

        if not content and role == "system":
            continue

        sanitized.append(Message(role=role, content=content))
    return sanitized


def get_provider(model: str) -> str | None:
    """Return the provider for a model name, or None if it's a local model."""
    # Bare GPT-OSS names and Ollama tags are local, despite the "gpt-" prefix.
    # Provider-qualified IDs (e.g. "openai/gpt-oss-20b") still route to cloud.
    if model == "gpt-oss" or model.startswith(("gpt-oss:", "gpt-oss-")):
        return None
    if any(model.startswith(p) for p in _OPENAI_PREFIXES):
        return "openai"
    if any(model.startswith(p) for p in _ANTHROPIC_PREFIXES):
        return "anthropic"
    if any(model.startswith(p) for p in _GOOGLE_PREFIXES):
        return "google"
    if any(model.startswith(p) for p in _MINIMAX_PREFIXES):
        return "minimax"
    if model.startswith(_ATLASCLOUD_PREFIX):
        return "atlascloud"
    if any(model.startswith(org) for org in _LOCAL_HF_ORGS):
        return None  # local model, never route to cloud
    if "/" in model:  # openrouter format: "meta-llama/llama-3-8b"
        return "openrouter"
    return None


def is_cloud_model(model: str) -> bool:
    """Return True if the model is served by a cloud provider."""
    return get_provider(model) is not None


def _openrouter_model_id(model: str) -> str:
    """Return the provider-facing ID for an OpenRouter model."""
    prefix = "openrouter/"
    candidate = model.removeprefix(prefix)
    # OpenRouter owns IDs such as "openrouter/auto" itself. Only remove the
    # LiteLLM routing prefix when the remainder is still a provider/model ID.
    if model.startswith(prefix) and "/" in candidate:
        return candidate
    return model


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------


def _to_openai_msgs(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        out.append({"role": role, "content": m.content or ""})
    return out


def _to_anthropic_msgs(
    messages: Sequence[Message],
) -> tuple[str, list[dict[str, Any]]]:
    """Return (system_text, chat_messages) in Anthropic format."""
    system_text = ""
    chat: list[dict[str, Any]] = []
    for m in messages:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        if role == "system":
            system_text = m.content or ""
        else:
            # Anthropic only allows "user" and "assistant"
            ar = "user" if role != "assistant" else "assistant"
            chat.append({"role": ar, "content": m.content or ""})
    return system_text, chat


def _to_google_contents(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert to Google Gemini content format."""
    contents = []
    for m in messages:
        role = m.role.value if hasattr(m.role, "value") else str(m.role)
        if role == "system":
            # Gemini doesn't have a system role in the contents array;
            # prepend as a user message.
            contents.append({"role": "user", "parts": [{"text": m.content or ""}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": m.content or ""}]})
        else:
            contents.append({"role": "user", "parts": [{"text": m.content or ""}]})
    return contents


# ---------------------------------------------------------------------------
# Streaming generators
# ---------------------------------------------------------------------------


async def _stream_openai(
    model: str,
    messages: Sequence[Message],
    temperature: float,
    max_tokens: int,
    base_url: str = "https://api.openai.com/v1",
    api_key_name: str = "OPENAI_API_KEY",
) -> AsyncIterator[str]:
    keys = _load_keys()
    api_key = keys.get(api_key_name, "")
    if not api_key:
        raise ValueError(f"{api_key_name} not set — add it in the Cloud Models tab")

    payload = {
        "model": model,
        "messages": _to_openai_msgs(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content") or ""
                    if delta:
                        yield delta
                except Exception:
                    pass


async def _stream_anthropic(
    model: str,
    messages: Sequence[Message],
    temperature: float,
    max_tokens: int,
) -> AsyncIterator[str]:
    keys = _load_keys()
    api_key = keys.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set — add it in the Cloud Models tab")

    system_text, chat_msgs = _to_anthropic_msgs(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": chat_msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if system_text:
        payload["system"] = system_text

    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                try:
                    event = json.loads(data)
                    if event.get("type") == "content_block_delta":
                        text = event.get("delta", {}).get("text", "")
                        if text:
                            yield text
                except Exception:
                    pass


async def _stream_google(
    model: str,
    messages: Sequence[Message],
    temperature: float,
    max_tokens: int,
) -> AsyncIterator[str]:
    keys = _load_keys()
    api_key = keys.get("GEMINI_API_KEY") or keys.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set — add it in the Cloud Models tab")

    contents = _to_google_contents(messages)
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        },
    }

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:streamGenerateContent?alt=sse&key={api_key}"
    )

    async with httpx.AsyncClient(timeout=180) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                try:
                    chunk = json.loads(data)
                    parts = (
                        chunk.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [])
                    )
                    for part in parts:
                        text = part.get("text", "")
                        if text:
                            yield text
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Local (Ollama) direct streaming — bypasses engine routing entirely
# ---------------------------------------------------------------------------


def _ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


async def stream_local(
    model: str,
    messages: Sequence[Message],
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> AsyncIterator[str]:
    """Stream tokens directly from Ollama, bypassing the engine system."""
    payload = {
        "model": model,
        "messages": _to_openai_msgs(messages),
        "stream": True,
        "keep_alive": "30m",
        # Disable extended thinking (Qwen3.5 etc.) — when enabled all tokens
        # go into the 'thinking' field and 'content' stays empty.
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": 4096,
        },
    }
    host = _ollama_host()
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", f"{host}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if data.get("done"):
                        break
                except Exception:
                    pass


async def list_local_models() -> list[str]:
    """Return Ollama model names directly from the Ollama API."""
    hosts = [_ollama_host(), "http://127.0.0.1:11434", "http://localhost:11434"]
    seen = set()
    for host in hosts:
        normalized = host.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{normalized}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                return [m["name"] for m in data.get("models", []) if "name" in m]
        except Exception:
            continue
    return []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def stream_cloud(
    model: str,
    messages: Sequence[Message],
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> AsyncIterator[str]:
    """Stream tokens from a cloud provider for the given model."""
    provider = get_provider(model)

    if provider == "openai":
        async for token in _stream_openai(model, messages, temperature, max_tokens):
            yield token

    elif provider == "anthropic":
        async for token in _stream_anthropic(model, messages, temperature, max_tokens):
            yield token

    elif provider == "google":
        async for token in _stream_google(model, messages, temperature, max_tokens):
            yield token

    elif provider == "openrouter":
        keys = _load_keys()
        api_key = keys.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise ValueError(
                "OPENROUTER_API_KEY not set — add it in the Cloud Models tab"
            )
        async for token in _stream_openai(
            _openrouter_model_id(model),
            messages,
            temperature,
            max_tokens,
            base_url="https://openrouter.ai/api/v1",
            api_key_name="OPENROUTER_API_KEY",
        ):
            yield token

    elif provider == "atlascloud":
        keys = _load_keys()
        api_key = keys.get("ATLASCLOUD_API_KEY", "")
        if not api_key:
            raise ValueError(
                "ATLASCLOUD_API_KEY not set — add it in the Cloud Models tab"
            )
        async for token in _stream_openai(
            model.removeprefix(_ATLASCLOUD_PREFIX),
            messages,
            temperature,
            max_tokens,
            base_url="https://api.atlascloud.ai/v1",
            api_key_name="ATLASCLOUD_API_KEY",
        ):
            yield token

    elif provider == "minimax":
        keys = _load_keys()
        api_key = keys.get("MINIMAX_API_KEY", "")
        if not api_key:
            raise ValueError("MINIMAX_API_KEY not set — add it in the Cloud Models tab")
        async for token in _stream_openai(
            model,
            messages,
            temperature,
            max_tokens,
            base_url="https://api.minimax.io/v1",
            api_key_name="MINIMAX_API_KEY",
        ):
            yield token

    else:
        raise ValueError(f"Unknown cloud provider for model: {model!r}")


async def stream_cloud_with_fallback(
    model: str,
    messages: Sequence[Message],
    temperature: float = 0.7,
    max_tokens: int = 1024,
    fallback_local_model: str = "qwen3.5:9b",
) -> AsyncIterator[str]:
    """Stream from a cloud provider, handling 429 rate limit errors with a plain warning and local fallback."""
    provider = get_provider(model)
    prov_name = "Google Gemini" if provider == "google" else (provider.capitalize() if provider else "Cloud")
    safe_msgs = sanitize_messages_for_cloud(messages)

    try:
        async for token in stream_cloud(model, safe_msgs, temperature, max_tokens):
            yield token
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            notice = (
                f"Rate limit error (429: Too Many Requests) received from {prov_name}.\n"
                f"Falling back to local model ({fallback_local_model}) on your computer...\n\n"
            )
            yield notice
            async for token in stream_local(fallback_local_model, safe_msgs, temperature, max_tokens):
                yield token
        else:
            raise

