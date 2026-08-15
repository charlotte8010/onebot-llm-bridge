from __future__ import annotations

import hmac
import json
import re
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
from .formatting import parse_reply_actions, split_bubbles
from .images import ImageResolver
from .memory import SQLiteMemoryStore
from .models import EventError, NormalizedMessage, is_meaningful, json_context
from .policy import decide_reply
from .providers import OpenAICompatibleProvider, ProviderError
from .tools import ToolRegistry, parse_tool_calls


MAX_BODY_BYTES = 1_048_576
_REMEMBER_RE = re.compile(r"^(?:记住|remember)\s*[:：]\s*(.+)$", re.IGNORECASE)
_FORGET_RE = re.compile(r"^(?:忘记|forget)\s*[:：]\s*(.+)$", re.IGNORECASE)


def _topic_terms(text: str) -> set[str]:
    normalized = text.lower().strip()
    terms = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    for chunk in re.findall(r"[\u4e00-\u9fff]+", normalized):
        terms.update(chunk[index : index + 2] for index in range(max(0, len(chunk) - 1)))
    return terms


def _related_topic(current: str, previous: str) -> bool:
    current = current.strip()
    previous = previous.strip()
    if not previous or len(current) <= 8:
        return True
    cue_starts = ("然后", "那", "所以", "真的吗", "对啊", "确实")
    if current.startswith(cue_starts):
        return True
    if any(current.startswith(marker) for marker in ("为什么", "怎么")) and len(current) <= 20:
        return True
    current_terms = _topic_terms(current)
    previous_terms = _topic_terms(previous)
    return bool(current_terms and previous_terms and current_terms & previous_terms)


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
        self.tools = ToolRegistry()

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
            "Use [[BUBBLE]] between separate QQ bubbles when useful. "
            "When a simple QQ reaction is more natural than text, you may append "
            "[[REACTION:emoji_id]] using a numeric QQ emoji id."
        )
        if self.settings.tools_enabled:
            system += " Available allowlisted tools may be requested with [[TOOL:tool_name]]."
        persona = self.persona()
        if persona:
            system += "\n\nUser-provided persona:\n" + persona
        facts = payload.get("facts", [])
        if isinstance(facts, list) and facts:
            system += "\n\nVerified user facts. Do not add or alter facts:\n- " + "\n- ".join(
                str(item).strip() for item in facts if str(item).strip()
            )
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
        tool_calls = parse_tool_calls(content) if self.settings.tools_enabled else []
        if tool_calls:
            results = self.tools.run_allowed(tool_calls, self.settings.tool_allowlist)
            tool_text = "\n".join(f"{item['name']}: {item['result']}" for item in results)
            if not tool_text:
                tool_text = "No requested tool is enabled in the allowlist."
            messages = [
                *messages,
                {"role": "assistant", "content": content},
                {"role": "user", "content": "Tool results:\n" + tool_text + "\nNow answer the original request without tool markers."},
            ]
            content = self.provider.complete(messages, images=[])
        bubbles, reaction_id = parse_reply_actions(content)
        if self.settings.reaction_mode == "off":
            reaction_id = None
        if not bubbles and not reaction_id:
            raise ProviderError("model reply contained no usable bubbles")
        return {"reply": content, "bubbles": bubbles, "reaction_id": reaction_id}


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
        memory_store: SQLiteMemoryStore | None = None,
    ) -> None:
        settings.validate_for_bridge()
        self.settings = settings
        self.napcat = napcat or NapCatClient(
            settings.napcat_api_url,
            settings.napcat_access_token,
        )
        self.image_resolver = image_resolver or ImageResolver(self.napcat)
        self.memory_store = memory_store
        if self.memory_store is None and settings.memory_db and settings.context_messages > 0:
            self.memory_store = SQLiteMemoryStore(
                settings.memory_db,
                max_messages=max(settings.context_messages * 5, settings.context_messages),
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
        self._state_lock = threading.RLock()
        self._pending: dict[str, PendingBatch] = {}
        self._topic_until: dict[str, float] = {}
        self._topic_text: dict[str, str] = {}
        self._loaded_contexts: set[str] = set()
        self._active_timer: threading.Timer | None = None
        self._shutdown = False
        if settings.active_enabled and settings.active_target_id and settings.active_prompt:
            self._schedule_active_message()

    def _context_for(self, key: str) -> deque[NormalizedMessage]:
        context = self._contexts[key]
        if key not in self._loaded_contexts:
            if self.memory_store is not None:
                context.extend(self.memory_store.load(key, self.settings.context_messages))
            self._loaded_contexts.add(key)
        return context

    def _record_message(self, message: NormalizedMessage) -> list[NormalizedMessage]:
        context = self._context_for(message.conversation_key)
        previous = list(context)
        context.append(message)
        if self.memory_store is not None:
            self.memory_store.append(message)
        return previous

    def _decision(self, message: NormalizedMessage):
        active_topic = False
        if message.conversation_type == "group" and self.settings.group_mode == "smart":
            with self._state_lock:
                active_topic = (
                    self._topic_until.get(message.conversation_key, 0.0) > time.time()
                    and _related_topic(message.text, self._topic_text.get(message.conversation_key, ""))
                )
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
                "facts": self.memory_store.load_facts(f"user:{first.sender_id}") if self.memory_store else [],
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
                reaction_id = str(result.get("reaction_id", "")).strip()
                if reaction_id and self.settings.reaction_mode == "like":
                    try:
                        self.napcat.set_msg_emoji_like(first.message_id, reaction_id)
                    except NapCatError as exc:
                        print(f"reaction failed: {type(exc).__name__}")
                    return {"handled": True, "reason": "reaction", "reaction_id": reaction_id}
                return {"handled": False, "reason": "empty_reply"}
            reaction_id = str(result.get("reaction_id", "")).strip()
            if reaction_id and self.settings.reaction_mode == "like":
                try:
                    self.napcat.set_msg_emoji_like(first.message_id, reaction_id)
                except NapCatError as exc:
                    print(f"reaction failed: {type(exc).__name__}")
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
                    self._topic_text[first.conversation_key] = " ".join(item.text for item in messages if item.text)
            return {"handled": True, "reason": "reply", "bubbles": len(bubbles)}

    def _handle_memory_command(self, message: NormalizedMessage, mode: str) -> dict[str, Any] | None:
        if self.memory_store is None:
            return None
        remember = _REMEMBER_RE.match(message.text.strip())
        forget = _FORGET_RE.match(message.text.strip())
        if not remember and not forget:
            return None
        scope = f"user:{message.sender_id}"
        if remember:
            self.memory_store.add_fact(scope, remember.group(1), message.message_id)
            acknowledgement = "记住了"
        else:
            removed = self.memory_store.remove_fact(scope, forget.group(1))
            acknowledgement = "忘掉了" if removed else "我没有记过这个"
        if message.conversation_type == "private":
            self.napcat.send_private(message.conversation_id, acknowledgement)
        elif mode == "quote_reply":
            self.napcat.send_group(message.conversation_id, acknowledgement, reply_to=message.message_id)
        else:
            self.napcat.send_group(message.conversation_id, acknowledgement)
        return {"handled": True, "reason": "memory_updated"}

    def _schedule_active_message(self) -> None:
        if self._active_timer is not None:
            self._active_timer.cancel()
        self._active_timer = threading.Timer(
            self.settings.active_interval_minutes * 60.0,
            self._active_message_tick,
        )
        self._active_timer.daemon = True
        self._active_timer.start()

    def _active_message_tick(self) -> None:
        try:
            payload = {
                "message": self.settings.active_prompt,
                "context": [],
                "conversation": f"{self.settings.active_target_type}:{self.settings.active_target_id}",
                "images": [],
                "facts": [],
            }
            result = self.bot_request(payload)
            bubbles = result.get("bubbles")
            if not isinstance(bubbles, list):
                bubbles = split_bubbles(str(result.get("reply", "")))
            for bubble in (str(item).strip() for item in bubbles):
                if not bubble:
                    continue
                if self.settings.active_target_type == "group":
                    self.napcat.send_group(self.settings.active_target_id, bubble)
                else:
                    self.napcat.send_private(self.settings.active_target_id, bubble)
        except Exception as exc:
            print(f"active message failed: {type(exc).__name__}: {exc}")
        finally:
            if self.settings.active_enabled and not self._shutdown:
                self._schedule_active_message()

    def handle_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        message = NormalizedMessage.from_onebot(event)
        decision = self._decision(message)
        if not is_meaningful(message) or not decision.should_reply:
            return {"handled": False, "reason": decision.reason}
        memory_result = self._handle_memory_command(message, decision.mode)
        if memory_result is not None:
            return memory_result
        with self._state_lock:
            context = self._record_message(message)
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
        memory_result = self._handle_memory_command(message, decision.mode)
        if memory_result is not None:
            return {"accepted": True, **memory_result}
        key = message.conversation_key
        with self._state_lock:
            context = self._record_message(message)
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
        self._shutdown = True
        with self._state_lock:
            batches = list(self._pending.values())
            self._pending.clear()
        for batch in batches:
            if batch.timer is not None:
                batch.timer.cancel()
        if self._active_timer is not None:
            self._active_timer.cancel()
        if self.memory_store is not None:
            self.memory_store.close()


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
