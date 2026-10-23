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


class WebResponse:
    """astrbot.api.web 响应工厂的测试替身，记录内容便于断言。"""

    def __init__(
        self,
        content=None,
        status_code=200,
        *,
        path=None,
        filename=None,
        media_type=None,
        headers=None,
        stream=None,
    ):
        self.content = content
        self.status_code = status_code
        self.path = path
        self.filename = filename
        self.media_type = media_type
        self.headers = dict(headers or {})
        self.stream = stream


class UploadFile:
    """multipart 上传文件替身。"""

    def __init__(self, name, payload=b"image-bytes", *, error=None):
        self.filename = name
        self.payload = payload
        self.error = error
        self.saved = None

    async def save(self, destination, *, max_bytes=None):
        if self.error is not None:
            raise self.error
        if max_bytes is not None and len(self.payload) > max_bytes:
            raise OverflowError("upload too large")
        self.saved = Path(destination)
        self.saved.parent.mkdir(parents=True, exist_ok=True)
        self.saved.write_bytes(self.payload)
        return len(self.payload)


class WebRequest:
    """插件 Web API 处理函数读取的请求上下文替身。"""

    def __init__(self):
        self.query = {}
        self.body = {}
        self.uploads = {}
        self.username = "tester"

    def reset(self, *, query=None, body=None, uploads=None):
        self.query = dict(query or {})
        self.body = dict(body or {})
        self.uploads = dict(uploads or {})
        return self

    async def json(self, default=None):
        return self.body if isinstance(self.body, dict) else default

    async def files(self):
        return self.uploads


web_request = WebRequest()


def json_response(data=None, *, status_code=200, headers=None):
    return WebResponse(data, status_code, headers=headers)


def error_response(message, *, status_code=400, data=None, headers=None):
    return WebResponse(
        {"status": "error", "message": message, "data": data},
        status_code,
        headers=headers,
    )


def file_response(path, *, filename=None, content_type=None, headers=None):
    return WebResponse(
        None,
        200,
        path=str(path),
        filename=filename or Path(path).name,
        media_type=content_type,
        headers=headers,
    )


def stream_response(content, *, content_type="text/event-stream", status_code=200, headers=None):
    return WebResponse(
        None, status_code, media_type=content_type, headers=headers, stream=content
    )


class WebApiContext:
    """支持 register_web_api / registered_web_apis 的 Context 替身。"""

    def __init__(self):
        self.registered_web_apis = []
        self.llm_tools = []

    def add_llm_tools(self, *tools):
        self.llm_tools.extend(tools)

    def register_web_api(self, route, view_handler, methods, desc):
        for index, api in enumerate(self.registered_web_apis):
            if api[0] == route and methods == api[2]:
                self.registered_web_apis[index] = (route, view_handler, methods, desc)
                return
        self.registered_web_apis.append((route, view_handler, methods, desc))

    def handler(self, route):
        for api in self.registered_web_apis:
            if api[0] == route:
                return api[1]
        raise KeyError(route)

@contextmanager
def plugin_modules(root: Path):
    names = (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.util", "astrbot.core",
        "astrbot.core.agent", "astrbot.core.agent.run_context", "astrbot.core.agent.tool",
        "astrbot.core.astr_agent_context", "astrbot.core.message",
        "astrbot.api.web",
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
    api_util = modules["astrbot.api.util"]
    api_util.SessionController = object
    api_util.SessionWaiter = object
    api_util.session_waiter = lambda *a, **k: decorator
    modules["astrbot.core.agent.run_context"].ContextWrapper = object
    modules["astrbot.core.agent.tool"].ToolExecResult = str
    modules["astrbot.core.astr_agent_context"].AstrAgentContext = object
    components = modules["astrbot.core.message.components"]
    components.Record, components.File, components.Reply = Record, File, Reply
    components.Video, components.Image = Component, Component
    paths = modules["astrbot.core.utils.astrbot_path"]
    paths.get_astrbot_data_path = lambda: str(root)
    paths.get_astrbot_temp_path = lambda: str(root / "temp")
    web = modules["astrbot.api.web"]
    web.request = web_request
    web.json_response = json_response
    web.error_response = error_response
    web.file_response = file_response
    web.stream_response = stream_response
    web_request.reset()
    with patch.dict(sys.modules, modules):
        main = importlib.import_module("astrbot_plugin_mmx_cli_tool.main")
        tools = importlib.import_module("astrbot_plugin_mmx_cli_tool.tools")
        yield main, tools
