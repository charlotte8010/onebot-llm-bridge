from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from .napcat import NapCatClient, NapCatError


class ImageResolutionError(RuntimeError):
    """Raised when an OneBot image cannot be converted to model input."""


def _read_response(response: Any, limit: int) -> bytes:
    try:
        data = response.read(limit + 1)
    except TypeError:
        data = response.read()
    if len(data) > limit:
        raise ImageResolutionError("image is too large")
    return data


def _mime_type(name: str, data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    guessed, _ = mimetypes.guess_type(name)
    return guessed if guessed and guessed.startswith("image/") else "application/octet-stream"


def _data_url(data: bytes, name: str = "") -> str:
    mime = _mime_type(name, data)
    if not mime.startswith("image/"):
        raise ImageResolutionError("resolved file is not a supported image")
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


class ImageResolver:
    """Resolve common NapCat image segments into bounded data URLs.

    NapCat and different OneBot implementations expose different image fields.
    This resolver tries the local path, URL, and file identifier forms, then
    asks NapCat's ``get_image`` action when only an identifier is available.
    """

    def __init__(
        self,
        napcat: NapCatClient,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.napcat = napcat
        self.max_bytes = max_bytes
        self.opener = opener

    def resolve_segments(self, segments: Iterable[Mapping[str, Any]]) -> list[str]:
        resolved: list[str] = []
        for segment in segments:
            if not isinstance(segment, Mapping) or segment.get("type") != "image":
                continue
            try:
                image = self.resolve_segment(segment)
            except (ImageResolutionError, NapCatError) as exc:
                print(f"[image] resolve failed: {type(exc).__name__}")
                continue
            if image and image not in resolved:
                resolved.append(image)
        return resolved

    def resolve_segment(self, segment: Mapping[str, Any]) -> str:
        data = segment.get("data")
        if not isinstance(data, Mapping):
            raise ImageResolutionError("image segment has no data")

        # Some adapters already provide a complete data URL.
        for key in ("url", "data"):
            candidate = data.get(key)
            if isinstance(candidate, str) and candidate.startswith("data:image/"):
                return self._validate_data_url(candidate)
        raw_base64 = data.get("base64")
        if isinstance(raw_base64, str) and raw_base64:
            return self._from_raw_base64(raw_base64, str(data.get("file", "")))

        for key in ("url", "path", "file"):
            candidate = data.get(key)
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            try:
                return self._resolve_reference(candidate)
            except ImageResolutionError:
                continue

        file_id = data.get("file")
        if isinstance(file_id, str) and file_id:
            response = self.napcat.call("get_image", {"file": file_id})
            result = response.get("data")
            if isinstance(result, Mapping):
                for key in ("base64", "url", "path", "file"):
                    candidate = result.get(key)
                    if isinstance(candidate, str) and candidate:
                        if key == "base64":
                            return self._from_raw_base64(candidate, file_id)
                        return self._resolve_reference(candidate, allow_api_lookup=False)
        raise ImageResolutionError("image has no usable URL, path, or file identifier")

    def _validate_data_url(self, value: str) -> str:
        header, separator, encoded = value.partition(",")
        if not separator or ";base64" not in header:
            raise ImageResolutionError("image data URL is not base64 encoded")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageResolutionError("image data URL is invalid") from exc
        if len(data) > self.max_bytes:
            raise ImageResolutionError("image is too large")
        return _data_url(data, header[5:].split(";", 1)[0])

    def _from_raw_base64(self, value: str, name: str = "") -> str:
        encoded = value.partition(",")[2] if value.startswith("data:") else value
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageResolutionError("image base64 is invalid") from exc
        if len(data) > self.max_bytes:
            raise ImageResolutionError("image is too large")
        return _data_url(data, name)

    def _resolve_reference(self, reference: str, *, allow_api_lookup: bool = True) -> str:
        if reference.startswith("data:image/"):
            return self._validate_data_url(reference)
        parsed = urlparse(reference)
        if parsed.scheme in {"http", "https"}:
            return self._download(reference)
        if parsed.scheme == "file":
            return self._read_path(Path(unquote(parsed.path)))
        path = Path(reference)
        if path.is_file():
            return self._read_path(path)
        if allow_api_lookup:
            response = self.napcat.call("get_image", {"file": reference})
            result = response.get("data")
            if isinstance(result, Mapping):
                for key in ("base64", "url", "path", "file"):
                    candidate = result.get(key)
                    if isinstance(candidate, str) and candidate and candidate != reference:
                        return self._resolve_reference(candidate, allow_api_lookup=False)
        raise ImageResolutionError("image reference could not be resolved")

    def _read_path(self, path: Path) -> str:
        try:
            with path.open("rb") as handle:
                data = handle.read(self.max_bytes + 1)
        except OSError as exc:
            raise ImageResolutionError("image path could not be read") from exc
        if len(data) > self.max_bytes:
            raise ImageResolutionError("image is too large")
        return _data_url(data, path.name)

    def _download(self, url: str) -> str:
        request = Request(url, headers={"User-Agent": "onebot-llm-bridge/0.1"}, method="GET")
        try:
            with self.opener(request, timeout=15.0) as response:
                data = _read_response(response, self.max_bytes)
        except HTTPError as exc:
            raise ImageResolutionError(f"image URL returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ImageResolutionError("image URL could not be downloaded") from exc
        return _data_url(data, urlparse(url).path)
