from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from typing import Any


UNSAFE_SCHEMA_KEYS = {
    "additionalProperties",
    "allOf",
    "anyOf",
    "default",
    "dependentRequired",
    "dependentSchemas",
    "else",
    "if",
    "not",
    "oneOf",
    "then",
}


class _FunctionTool:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        **_: Any,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters


class _Logger:
    def error(self, *_: Any, **__: Any) -> None:
        pass

    def warning(self, *_: Any, **__: Any) -> None:
        pass


def _install_astrbot_stubs() -> None:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.FunctionTool = _FunctionTool
    api.logger = _Logger()

    run_context = types.ModuleType("astrbot.core.agent.run_context")
    run_context.ContextWrapper = type(
        "ContextWrapper", (), {"__class_getitem__": classmethod(lambda cls, _: cls)}
    )

    tool = types.ModuleType("astrbot.core.agent.tool")
    tool.ToolExecResult = str

    agent_context = types.ModuleType("astrbot.core.astr_agent_context")
    agent_context.AstrAgentContext = type("AstrAgentContext", (), {})

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules.setdefault("astrbot.api", api)
    sys.modules.setdefault("astrbot.core", types.ModuleType("astrbot.core"))
    sys.modules.setdefault("astrbot.core.agent", types.ModuleType("astrbot.core.agent"))
    sys.modules.setdefault("astrbot.core.agent.run_context", run_context)
    sys.modules.setdefault("astrbot.core.agent.tool", tool)
    sys.modules.setdefault("astrbot.core.astr_agent_context", agent_context)


def _iter_schema_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_schema_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_schema_nodes(value)


class ToolSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _install_astrbot_stubs()
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from astrbot_plugin_mmx_cli_tool.tools import (  # pylint: disable=import-outside-toplevel
            CheckQuotaTool,
            DescribeImageTool,
            DownloadVideoTool,
            GenerateImageTool,
            GenerateMusicTool,
            GenerateVideoTool,
            ListVoicesTool,
            MusicCoverTool,
            QueryVideoTaskTool,
            SpeechSynthesizeTool,
            WebSearchTool,
        )

        class Api:
            pass

        api = Api()
        cls.tools = [
            GenerateImageTool(api),
            GenerateVideoTool(api),
            QueryVideoTaskTool(api),
            DownloadVideoTool(api, "/tmp"),
            GenerateMusicTool(api),
            MusicCoverTool(api),
            WebSearchTool(api),
            DescribeImageTool(api),
            CheckQuotaTool(api),
            SpeechSynthesizeTool(api, "/tmp"),
            ListVoicesTool(api),
        ]

    def test_tool_count_stays_in_sync(self) -> None:
        self.assertEqual(11, len(self.tools))

    def test_schemas_use_provider_neutral_subset(self) -> None:
        for tool in self.tools:
            with self.subTest(tool=tool.name):
                for node in _iter_schema_nodes(tool.parameters):
                    self.assertFalse(UNSAFE_SCHEMA_KEYS.intersection(node))
                    self.assertNotEqual([], node.get("required"))

    def test_required_fields_are_declared_properties(self) -> None:
        for tool in self.tools:
            for node in _iter_schema_nodes(tool.parameters):
                required = node.get("required")
                if required is None:
                    continue
                properties = node.get("properties", {})
                with self.subTest(tool=tool.name, required=required):
                    self.assertIsInstance(required, list)
                    self.assertIsInstance(properties, dict)
                    self.assertTrue(set(required).issubset(properties))


if __name__ == "__main__":
    unittest.main()
