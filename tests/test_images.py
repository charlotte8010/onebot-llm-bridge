import base64
import tempfile
import unittest
from pathlib import Path

from onebot_llm_bridge.images import ImageResolver


PNG = b"\x89PNG\r\n\x1a\nminimal-test-image"


class FakeNapCat:
    def __init__(self, path: str):
        self.path = path
        self.calls = []

    def call(self, action, params):
        self.calls.append((action, params))
        return {"status": "ok", "retcode": 0, "data": {"path": self.path}}


class ImageResolverTests(unittest.TestCase):
    def test_data_url_is_preserved_as_bounded_model_input(self) -> None:
        encoded = base64.b64encode(PNG).decode("ascii")
        resolver = ImageResolver(FakeNapCat("unused"))
        result = resolver.resolve_segments(
            [{"type": "image", "data": {"url": f"data:image/png;base64,{encoded}"}}]
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].startswith("data:image/png;base64,"))

    def test_file_identifier_uses_napcat_get_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "photo.png"
            path.write_bytes(PNG)
            napcat = FakeNapCat(str(path))
            result = ImageResolver(napcat).resolve_segments(
                [{"type": "image", "data": {"file": "napcat-file-id"}}]
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(napcat.calls, [("get_image", {"file": "napcat-file-id"})])

    def test_invalid_image_is_skipped_without_failing_the_batch(self) -> None:
        resolver = ImageResolver(FakeNapCat("unused"))
        self.assertEqual(
            resolver.resolve_segments([{"type": "image", "data": {"url": "data:image/png;base64,no"}}]),
            [],
        )
