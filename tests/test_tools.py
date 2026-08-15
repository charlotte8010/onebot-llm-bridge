import unittest

from onebot_llm_bridge.tools import ToolRegistry, parse_tool_calls


class ToolTests(unittest.TestCase):
    def test_tool_markers_are_deduplicated(self) -> None:
        self.assertEqual(parse_tool_calls("[[TOOL:get_time]][[TOOL:get_time]]"), ["get_time"])

    def test_tools_require_an_explicit_allowlist(self) -> None:
        registry = ToolRegistry({"safe": lambda: "ok"})
        self.assertEqual(registry.run_allowed(["safe"], ()), [])
        self.assertEqual(registry.run_allowed(["safe"], ("safe",))[0]["result"], "ok")
