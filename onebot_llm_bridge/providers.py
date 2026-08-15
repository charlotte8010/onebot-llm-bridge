from __future__ import annotations

import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """A model provider returned an unusable response."""


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        return "".join(pieces)
    return ""


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 1024,
        timeout: float = 60.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.opener = opener

    def complete(self, messages: list[dict[str, Any]], images: list[str] | None = None) -> str:
        request_messages = [dict(message) for message in messages]
        if images:
            last = dict(request_messages[-1])
            text = str(last.get("content", ""))
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            content.extend({"type": "image_url", "image_url": {"url": image}} for image in images)
            last["content"] = content
            request_messages[-1] = last
        payload = {
            "model": self.model,
            "messages": request_messages,
            "max_tokens": self.max_tokens,
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise ProviderError(f"model provider returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"model provider request failed: {type(exc).__name__}") from exc
        try:
            data = json.loads(raw)
            choices = data["choices"]
            message = choices[0]["message"]
            result = _content(message.get("content"))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("model provider returned an invalid response") from exc
        if not result.strip():
            raise ProviderError("model provider returned an empty reply")
        return result.strip()

