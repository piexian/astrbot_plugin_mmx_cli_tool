"""Minimal AstrBot test doubles; these tests do not load a running bot."""

from __future__ import annotations

import importlib
import logging
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class Tool:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Component:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class File(Component):
    def __init__(self, name, file="", url=""):
        super().__init__(name=name, file_=file, url=url)

    async def get_file(self, allow_return_url=False):
        return self.url if allow_return_url and self.url else self.file_


class Record(Component):
    pass


class Reply(Component):
    pass


class Star:
    def __init__(self, context):
        self.context = context


class Event:
    def __init__(self, text="", messages=None):
        self.message_str = text
        self.messages = messages or []

    def get_messages(self):
        return self.messages

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


def decorator(*args, **kwargs):
    return lambda function: function


def command_group(*args):
    def wrap(function):
        function.command = decorator
        return function
    return wrap


@contextmanager
def plugin_modules(root: Path):
    names = (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.core",
        "astrbot.core.agent", "astrbot.core.agent.run_context", "astrbot.core.agent.tool",
        "astrbot.core.astr_agent_context", "astrbot.core.message",
        "astrbot.core.message.components", "astrbot.core.utils", "astrbot.core.utils.astrbot_path",
    )
    modules = {name: ModuleType(name) for name in names}
    api = modules["astrbot.api"]
    api.FunctionTool = Tool
    api.logger = logging.getLogger("mmx-test")
    api.AstrBotConfig = dict
    api.star = SimpleNamespace(Star=Star, Context=object)
    modules["astrbot.api.event"].AstrMessageEvent = Event
    modules["astrbot.api.event"].filter = SimpleNamespace(
        command_group=command_group, permission_type=decorator,
        PermissionType=SimpleNamespace(ADMIN="admin"),
    )
    modules["astrbot.core.agent.run_context"].ContextWrapper = object
    modules["astrbot.core.agent.tool"].ToolExecResult = str
    modules["astrbot.core.astr_agent_context"].AstrAgentContext = object
    components = modules["astrbot.core.message.components"]
    components.Record, components.File, components.Reply = Record, File, Reply
    components.Video, components.Image = Component, Component
    paths = modules["astrbot.core.utils.astrbot_path"]
    paths.get_astrbot_data_path = lambda: str(root)
    paths.get_astrbot_temp_path = lambda: str(root / "temp")
    with patch.dict(sys.modules, modules):
        main = importlib.import_module("astrbot_plugin_mmx_cli_tool.main")
        tools = importlib.import_module("astrbot_plugin_mmx_cli_tool.tools")
        yield main, tools
