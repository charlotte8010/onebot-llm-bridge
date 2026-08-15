from __future__ import annotations

import hmac
import json
import threading
import time
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
from .images import ImageResolver
from .models import EventError, NormalizedMessage, is_meaningful, json_context
from .policy import decide_reply
from .providers import OpenAICompatibleProvider, ProviderError


MAX_BODY_BYTES = 1_048_576


class PendingBatch:
    def __init__(
        self,
        messages: list[NormalizedMessage],
        context: list[NormalizedMessage],
        mode: str,
        reply_to: str | None,
    ) -> None:
        self.messages = messages
        self.context = context
        self.mode = mode
        self.reply_to = reply_to
        self.timer: threading.Timer | None = None


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
    def __init__(
        self,
        settings: Settings,
        *,
        provider: OpenAICompatibleProvider | None = None,
        vision_provider: OpenAICompatibleProvider | None = None,
    ) -> None:
        settings.validate_for_bot()
        self.settings = settings
        self.provider = provider or OpenAICompatibleProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
        )
        if settings.vision_mode == "separate":
            self.vision_provider = vision_provider or OpenAICompatibleProvider(
                api_key=settings.vision_api_key,
                base_url=settings.vision_base_url,
                model=settings.vision_model,
                max_tokens=settings.vision_max_tokens,
                timeout=settings.vision_timeout_seconds,
            )
        else:
            self.vision_provider = vision_provider

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
        images = [str(item) for item in payload.get("images", []) if isinstance(item, str)]
        vision_note = ""
        if images and self.settings.vision_mode == "separate":
            if self.vision_provider is None:
                raise ProviderError("vision provider is not configured")
            try:
                vision_note = self.vision_provider.complete(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You describe images for a chat assistant. State only visible, useful details. "
                                "Do not guess identities, text, events, or context that cannot be seen."
                            ),
                        },
                        {"role": "user", "content": "Describe the attached image(s) briefly for the next reply."},
                    ],
                    images=images,
                )
            except ProviderError:
                print("[vision] provider failed; continuing without image description")
            if vision_note:
                user_prompt += "\n\nImage understanding from a separate vision model:\n" + vision_note
            images = []
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}]
        model_images = images if self.settings.vision_mode == "direct" else []
        try:
            content = self.provider.complete(messages, images=model_images)
        except ProviderError:
            if not model_images:
                raise
            print("[vision] main model rejected image input; retrying as text")
            content = self.provider.complete(messages, images=[])
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
        image_resolver: ImageResolver | None = None,
    ) -> None:
        settings.validate_for_bridge()
        self.settings = settings
        self.napcat = napcat or NapCatClient(
            settings.napcat_api_url,
            settings.napcat_access_token,
        )
        self.image_resolver = image_resolver or ImageResolver(self.napcat)
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
        self._state_lock = threading.RLock()
        self._pending: dict[str, PendingBatch] = {}
        self._topic_until: dict[str, float] = {}

    def _decision(self, message: NormalizedMessage):
        active_topic = False
        if message.conversation_type == "group" and self.settings.group_mode == "smart":
            with self._state_lock:
                active_topic = self._topic_until.get(message.conversation_key, 0.0) > time.time()
        return decide_reply(
            message,
            group_mode=self.settings.group_mode,
            group_allowlist=self.settings.group_allowlist,
            address_names=self.settings.bot_names,
            bot_qq=self.settings.bot_qq,
            active_topic=active_topic,
        )

    def _set_typing(self, user_id: str, active: bool) -> None:
        if not self.settings.typing_status or not hasattr(self.napcat, "set_input_status"):
            return
        try:
            self.napcat.set_input_status(user_id, active)
        except NapCatError as exc:
            print(f"NapCat input status failed: {type(exc).__name__}")

    def _process_batch(
        self,
        messages: list[NormalizedMessage],
        context: list[NormalizedMessage],
        mode: str = "reply",
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        first = messages[0]
        with self._locks[first.conversation_key]:
            images: list[str] = []
            if self.settings.vision_mode != "off":
                segments = [segment for item in messages for segment in item.segments]
                images = self.image_resolver.resolve_segments(segments)
            payload = {
                "message": "\n".join(item.text for item in messages if item.text),
                "context": json.loads(json_context(context)),
                "conversation": first.conversation_key,
                "images": images,
            }
            self._set_typing(first.sender_id, True)
            try:
                result = self.bot_request(payload)
            finally:
                self._set_typing(first.sender_id, False)
            raw_bubbles = result.get("bubbles")
            bubbles = (
                [str(item).strip() for item in raw_bubbles if str(item).strip()]
                if isinstance(raw_bubbles, list)
                else split_bubbles(str(result.get("reply", "")))
            )
            if not bubbles:
                return {"handled": False, "reason": "empty_reply"}
            for bubble in bubbles:
                if first.conversation_type == "private":
                    if mode == "quote_reply":
                        self.napcat.send_private(first.conversation_id, bubble, reply_to=reply_to)
                    else:
                        self.napcat.send_private(first.conversation_id, bubble)
                else:
                    if mode == "quote_reply":
                        self.napcat.send_group(first.conversation_id, bubble, reply_to=reply_to)
                    else:
                        self.napcat.send_group(first.conversation_id, bubble)
            if first.conversation_type == "group" and self.settings.group_mode == "smart":
                with self._state_lock:
                    self._topic_until[first.conversation_key] = time.time() + self.settings.followup_seconds
            return {"handled": True, "reason": "reply", "bubbles": len(bubbles)}

    def handle_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        message = NormalizedMessage.from_onebot(event)
        decision = self._decision(message)
        if not is_meaningful(message) or not decision.should_reply:
            return {"handled": False, "reason": decision.reason}
        with self._state_lock:
            context = list(self._contexts[message.conversation_key])
            self._contexts[message.conversation_key].append(message)
        result = self._process_batch(
            [message],
            context,
            decision.mode,
            message.reply_to if decision.mode == "quote_reply" else None,
        )
        result["reason"] = decision.reason
        return result

    def enqueue_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Accept an event quickly and process it after the debounce window."""
        message = NormalizedMessage.from_onebot(event)
        decision = self._decision(message)
        if not is_meaningful(message) or not decision.should_reply:
            return {"accepted": False, "reason": decision.reason}
        key = message.conversation_key
        with self._state_lock:
            context = list(self._contexts[key])
            self._contexts[key].append(message)
            batch = self._pending.get(key)
            if batch is None:
                batch = PendingBatch(
                    [message],
                    context,
                    decision.mode,
                    message.reply_to if decision.mode == "quote_reply" else None,
                )
                self._pending[key] = batch
                self._schedule_batch(key, self.settings.debounce_delay())
            else:
                batch.messages.append(message)
                if message.conversation_type == "private":
                    self._schedule_batch(key, self.settings.debounce_delay())
            batch_size = len(batch.messages)
        return {"accepted": True, "reason": decision.reason, "batch_size": batch_size}

    def _schedule_batch(self, key: str, delay: float) -> None:
        batch = self._pending[key]
        if batch.timer is not None:
            batch.timer.cancel()
        timer = threading.Timer(delay, self._flush_batch, args=(key,))
        timer.daemon = True
        batch.timer = timer
        timer.start()

    def _flush_batch(self, key: str) -> None:
        with self._state_lock:
            batch = self._pending.pop(key, None)
        if batch is None:
            return
        try:
            self._process_batch(batch.messages, batch.context, batch.mode, batch.reply_to)
        except Exception as exc:
            print(f"background batch failed: {type(exc).__name__}: {exc}")

    def shutdown(self) -> None:
        with self._state_lock:
            batches = list(self._pending.values())
            self._pending.clear()
        for batch in batches:
            if batch.timer is not None:
                batch.timer.cancel()


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
            result = server.bridge.enqueue_event(self.read_json())
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
