from __future__ import annotations

import json
import os
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


def _load_dotenv_file() -> None:
    env_paths = [Path.cwd() / ".env", Path.cwd() / ".env.local"]
    for env_path in env_paths:
        if not env_path.exists():
            continue
        try:
            raw_text = env_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in raw_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv_file()


API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

# The newest Flash model the configured key can call. Pro models need a paid quota, and a
# default that answers 429 on every request is a default that does not work.
DEFAULT_MODEL = "gemini-3.8-flash"


class ConfigurationError(RuntimeError):
    """Raised when the Gemini client is used without a configured API key."""


class GeminiAPIError(RuntimeError):
    """A non-success response from the Gemini API, carrying its HTTP status."""

    def __init__(self, status_code: int, message: str):
        super().__init__(f"Gemini API returned {status_code}: {message}")
        self.status_code = status_code


# Transient server-side and rate-limit statuses. Anything else is a real failure and
# is raised immediately rather than retried.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Gemini 3 models are tuned for their default sampling. Google's guidance is to leave
# temperature alone: lowering it causes looping and degraded reasoning, and that failure is
# silent, the answers just get worse. Depth is tuned with effort (thinking level) instead;
# nothing here may add a sampler, under either spelling.
SAMPLING_PARAMETERS = frozenset({"temperature", "topP", "topK", "top_p", "top_k"})

# maxOutputTokens covers thinking plus the visible answer on thinking models, so a small
# ceiling can be spent entirely on thinking and return no text at all.
DEFAULT_MAX_TOKENS = 16000

_STRUCTURED_SYSTEM = (
    "Respond with valid JSON matching the provided schema. "
    "Do not include markdown fences or explanatory prose."
)


# Verify against the operating system's trust store, not httpx's bundled certifi list.
# Antivirus web shields and corporate proxies re-sign TLS with a root they install in the
# OS store; certifi does not carry it, so every request failed certificate verification
# while the same call from a browser succeeded. Verification stays on.
_TLS_CONTEXT = ssl.create_default_context()


def _http_post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> httpx.Response:
    return httpx.post(url, json=json, headers=headers, timeout=timeout, verify=_TLS_CONTEXT)


def _status_of(exc: BaseException) -> int | None:
    """Read the HTTP status off an exception, so anything shaped like one can retry."""
    for attribute in ("status_code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _error_message(response: Any) -> str:
    try:
        return str(response.json()["error"]["message"])
    except Exception:
        return str(getattr(response, "text", "") or "")[:500]


def _text_from_response(data: Any) -> str:
    """Join the answer's text parts, leaving out thought summaries."""
    candidates = (data or {}).get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text") or "" for part in parts if not part.get("thought"))


def _prepare_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalise a turn list into user/assistant turns the API accepts.

    Empty turns are dropped and the list is trimmed to start on a user turn, because a
    recalled conversation can legitimately begin with something Kit said.
    """
    prepared: list[dict[str, Any]] = []
    for entry in messages or []:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role") or "").strip().lower()
        content = str(entry.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        prepared.append({"role": role, "content": content})
    while prepared and prepared[0]["role"] != "user":
        prepared.pop(0)
    return prepared


def _to_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Gemini calls the assistant side "model".
    return [
        {"role": "model" if message["role"] == "assistant" else "user", "parts": [{"text": message["content"]}]}
        for message in messages
    ]


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    body = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
    return "\n".join(body).strip()


@dataclass
class GeminiGateway:
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = 120.0

    def __post_init__(self) -> None:
        if self.model is None:
            self.model = os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        if self.api_key is None:
            self.api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if self.base_url is None:
            self.base_url = os.getenv("GEMINI_BASE_URL") or API_ROOT

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ConfigurationError(
                "Gemini API key is not configured. Set GEMINI_API_KEY or pass api_key to GeminiGateway."
            )
        url = f"{str(self.base_url).rstrip('/')}/models/{self.model}:generateContent"
        # The key travels in a header, not the query string, so it never lands in a URL
        # that an exception message or access log might echo.
        response = _http_post(url, json=body, headers={"x-goog-api-key": self.api_key}, timeout=self.timeout)
        if response.status_code != 200:
            raise GeminiAPIError(response.status_code, _error_message(response))
        return response.json()

    def _request(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int,
        effort: str | None = None,
        json_output: bool = False,
    ) -> dict[str, Any]:
        generation_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
        # Thinking level is the supported way to tune depth on Gemini 3; it replaces the
        # sampling knobs, which stay at the model's defaults.
        if effort:
            generation_config["thinkingConfig"] = {"thinkingLevel": effort}
        if json_output:
            generation_config["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": _to_contents(messages), "generationConfig": generation_config}
        if system is not None:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        last_exc: BaseException | None = None
        for attempt in range(5):
            try:
                return self._post(body)
            except Exception as exc:
                if _status_of(exc) not in RETRYABLE_STATUS or attempt == 4:
                    raise
                last_exc = exc
                time.sleep(2 ** attempt)

        raise RuntimeError("Gemini request failed after 5 attempts.") from last_exc

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = None,
        system: str | None = None,
    ) -> str:
        response = self._request(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            max_tokens=max_tokens,
            effort=effort,
        )
        return _text_from_response(response)

    def converse(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = None,
    ) -> str:
        """Answer within a conversation rather than from a single isolated prompt.

        The API is stateless, so prior turns are sent as turns. Passing them as real
        user/model turns rather than pasting a transcript into one prompt is what lets
        the model treat earlier answers as its own.
        """
        prepared = _prepare_messages(messages)
        if not prepared:
            raise ValueError("converse requires at least one user message.")
        response = self._request(
            messages=prepared,
            system=system,
            max_tokens=max_tokens,
            effort=effort,
        )
        return _text_from_response(response)

    def generate_structured(
        self,
        prompt: str,
        schema: Any,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = None,
    ) -> Any:
        if schema is None:
            raise ValueError("schema is required for structured generation.")
        # The schema is sent with the prompt; asking for "the provided schema" without
        # providing it is what made structured output unreliable. It is not passed as
        # responseSchema because Gemini accepts only a subset of JSON Schema there, and the
        # schemas callers send use keywords outside it.
        content = f"{prompt}\n\nSchema:\n{json.dumps(schema, sort_keys=True, default=str)}"
        response = self._request(
            messages=[{"role": "user", "content": content}],
            system=_STRUCTURED_SYSTEM,
            max_tokens=max_tokens,
            effort=effort,
            json_output=True,
        )
        text = _strip_code_fence(_text_from_response(response))
        if not text:
            raise ValueError("Structured generation returned empty content.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Structured generation response was not valid JSON.") from exc
        return parsed

    def plan(self, task: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "task": task,
            "strategy": "evidence-first, structural, falsifiable",
            "offline": self.api_key is None,
        }
