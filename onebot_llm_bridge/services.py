from __future__ import annotations

import hmac
import json
import threading
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .napcat import NapCatClient, NapCatError
from .config import Settings
from .formatting import split_bubbles
from .models import EventError, NormalizedMessage, is_meaningful, json_context
from .policy import decide_reply
from .providers import OpenAICompatibleProvider, ProviderError


MAX_BODY_BYTES = 1_048_576


def _bearer_matches(header: str, expected: str) -> bool:
    if not expected:
        return True
    return hmac.compare_digest(header.strip(), f"Bearer {expected}")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")


class JsonHandler(BaseHTTPRequestHandler):
    server_version = "OneBotLLMBridge/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.address_string()}] {format % args}")

    def write_json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body is too large")
        body = self.rfile.read(length)
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data


class BotService:
    def __init__(self, settings: Settings, *, provider: OpenAICompatibleProvider | None = None) -> None:
        settings.validate_for_bot()
        self.settings = settings
        self.provider = provider or OpenAICompatibleProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
        )

    def persona(self) -> str:
        if not self.settings.persona_file:
            return ""
        path = Path(self.settings.persona_file)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")[:20_000]

    def reply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message", "")).strip()
        if not message:
            raise ValueError("message is required")
        context = payload.get("context", [])
        context_text = context if isinstance(context, str) else json.dumps(context, ensure_ascii=False)
        system = (
            "You are a helpful QQ chat assistant. Reply naturally and concisely. "
            "Do not invent user facts. Return only the reply text. "
            "Use [[BUBBLE]] between separate QQ bubbles when useful."
        )
        persona = self.persona()
        if persona:
            system += "\n\nUser-provided persona:\n" + persona
        user_prompt = f"Recent context:\n{context_text}\n\nNew message:\n{message}"
        content = self.provider.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}],
            images=[str(item) for item in payload.get("images", []) if isinstance(item, str)],
        )
        bubbles = split_bubbles(content)
        if not bubbles:
            raise ProviderError("model reply contained no usable bubbles")
        return {"reply": content, "bubbles": bubbles}


class BotServiceHandler(JsonHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        self.write_json(HTTPStatus.OK, {"ok": True, "service": "bot"})

    def do_POST(self) -> None:
        if self.path != "/reply":
            self.write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        server: BotHTTPServer = self.server  # type: ignore[assignment]
        if not _bearer_matches(self.headers.get("Authorization", ""), server.service_token):
            self.write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        try:
            result = server.service.reply(self.read_json())
        except (ValueError, ProviderError) as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        self.write_json(HTTPStatus.OK, result)


class BotHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], service: BotService, token: str) -> None:
        super().__init__(address, BotServiceHandler)
        self.service = service
        self.service_token = token


def _post_json(url: str, payload: Mapping[str, Any], token: str, timeout: float = 60.0) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=_json_bytes(payload), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"bot service returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"bot service request failed: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("bot service returned an invalid payload")
    if data.get("ok") is False:
        raise RuntimeError(str(data.get("error", "bot service rejected request")))
    return data


class Bridge:
    def __init__(
        self,
        settings: Settings,
        *,
        napcat: NapCatClient | None = None,
        bot_request: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        settings.validate_for_bridge()
        self.settings = settings
        self.napcat = napcat or NapCatClient(
            settings.napcat_api_url,
            settings.napcat_access_token,
        )
        self.bot_request = bot_request or (
            lambda payload: _post_json(
                f"http://{settings.bot_service_host}:{settings.bot_service_port}/reply",
                payload,
                settings.bot_service_token,
                settings.llm_timeout_seconds,
            )
        )
        self._contexts: dict[str, deque[NormalizedMessage]] = defaultdict(
            lambda: deque(maxlen=settings.context_messages)
        )
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def handle_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        message = NormalizedMessage.from_onebot(event)
        decision = decide_reply(
            message,
            group_mode=self.settings.group_mode,
            group_allowlist=self.settings.group_allowlist,
            address_names=("bot",),
        )
        context = self._contexts[message.conversation_key]
        context.append(message)
        if not is_meaningful(message) or not decision.should_reply:
            return {"handled": False, "reason": decision.reason}
        with self._locks[message.conversation_key]:
            payload = {
                "message": message.text,
                "context": json.loads(json_context(list(context))),
                "conversation": message.conversation_key,
                "images": [],
            }
            result = self.bot_request(payload)
            raw_bubbles = result.get("bubbles")
            bubbles = [str(item).strip() for item in raw_bubbles if str(item).strip()] if isinstance(raw_bubbles, list) else split_bubbles(str(result.get("reply", "")))
            if not bubbles:
                return {"handled": False, "reason": "empty_reply"}
            for bubble in bubbles:
                if message.conversation_type == "private":
                    self.napcat.send_private(message.conversation_id, bubble)
                else:
                    self.napcat.send_group(message.conversation_id, bubble)
            return {"handled": True, "reason": decision.reason, "bubbles": len(bubbles)}


class BridgeHandler(JsonHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        self.write_json(HTTPStatus.OK, {"ok": True, "service": "bridge"})

    def do_POST(self) -> None:
        if self.path != "/onebot":
            self.write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        server: BridgeHTTPServer = self.server  # type: ignore[assignment]
        if not _bearer_matches(self.headers.get("Authorization", ""), server.event_token):
            self.write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        try:
            result = server.bridge.handle_event(self.read_json())
        except (EventError, ValueError) as exc:
            self.write_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except (NapCatError, RuntimeError) as exc:
            self.write_json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
            return
        self.write_json(HTTPStatus.OK, result)


class BridgeHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], bridge: Bridge, token: str) -> None:
        super().__init__(address, BridgeHandler)
        self.bridge = bridge
        self.event_token = token


def serve_bot(settings: Settings) -> None:
    server = BotHTTPServer(
        (settings.bot_service_host, settings.bot_service_port),
        BotService(settings),
        settings.bot_service_token,
    )
    print(f"Bot service listening on http://{settings.bot_service_host}:{settings.bot_service_port}")
    server.serve_forever()


def serve_bridge(settings: Settings) -> None:
    server = BridgeHTTPServer(
        (settings.bridge_host, settings.bridge_port),
        Bridge(settings),
        settings.napcat_event_token,
    )
    print(f"Bridge listening on http://{settings.bridge_host}:{settings.bridge_port}/onebot")
    server.serve_forever()
