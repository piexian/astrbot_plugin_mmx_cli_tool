"""插件运行时 logger 入口约束测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import validate_runtime_loggers


class LoggerConventionTests(unittest.TestCase):
    def test_project_runtime_modules_pass_guard(self):
        validate_runtime_loggers(Path(__file__).resolve().parents[1])

    def test_accepts_astrbot_api_logger(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text(
                "from astrbot.api import logger\nlogger.info('ok')\n",
                encoding="utf-8",
            )
            validate_runtime_loggers(root)

    def test_rejects_custom_logger_factories(self):
        cases = {
            "logging_module": "import logging\nlogger = logging.getLogger(__name__)",
            "logging_import": "from logging import getLogger\nlogger = getLogger(__name__)",
            "loguru": "from loguru import logger",
            "astrbot_manager": (
                "from astrbot.core.log import LogManager\n"
                "logger = LogManager.GetLogger('mmx')"
            ),
            "direct_logger": "logger = Logger('mmx')",
        }
        for name, source in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "mmx").mkdir()
                (root / "mmx" / "bad.py").write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(AssertionError, "禁止自建 logger"):
                    validate_runtime_loggers(root)


if __name__ == "__main__":
    unittest.main()
