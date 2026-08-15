from __future__ import annotations

import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class NapCatError(RuntimeError):
    """A NapCat HTTP action failed."""


class NapCatClient:
    def __init__(
        self,
        base_url: str,
        access_token: str = "",
        *,
        timeout: float = 10.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout
        self.opener = opener

    def call(self, action: str, params: Mapping[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        request = Request(
            f"{self.base_url}/{action.lstrip('/')}",
            data=json.dumps(dict(params), ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise NapCatError(f"NapCat action {action} returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise NapCatError(f"NapCat action {action} failed: {type(exc).__name__}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NapCatError(f"NapCat action {action} returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise NapCatError(f"NapCat action {action} returned an invalid payload")
        if data.get("status") == "failed" or data.get("retcode", 0) not in {0, None}:
            raise NapCatError(f"NapCat action {action} was rejected")
        return data

    def send_private(self, user_id: str, message: str) -> dict[str, Any]:
        return self.call("send_private_msg", {"user_id": user_id, "message": message})

    def send_group(self, group_id: str, message: str) -> dict[str, Any]:
        return self.call("send_group_msg", {"group_id": group_id, "message": message})

    def set_input_status(self, user_id: str, active: bool) -> dict[str, Any]:
        return self.call(
            "set_input_status",
            {"user_id": user_id, "event_type": 1 if active else 0},
        )

