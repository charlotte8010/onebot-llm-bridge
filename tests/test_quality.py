import unittest

from onebot_llm_bridge.quality import sanitize_bubbles


class ReplyQualityTests(unittest.TestCase):
    def test_quality_cleaner_keeps_bubbles_separate_and_caps_noise(self) -> None:
        bubbles, warnings = sanitize_bubbles(["第一行\n第二行", "！！！！！", "", "ok"])
        self.assertEqual(bubbles, ["第一行 第二行", "！！！", "ok"])
        self.assertEqual(warnings, [])

    def test_quality_cleaner_caps_count_and_length(self) -> None:
        bubbles, warnings = sanitize_bubbles(["x" * 20] * 3, max_bubbles=2, max_chars=5)
        self.assertEqual(bubbles, ["xxxxx", "xxxxx"])
        self.assertEqual(warnings, ["bubble_truncated", "too_many_bubbles"])
