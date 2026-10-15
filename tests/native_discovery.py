"""Optional real-host contracts, separate from httpx-only offline CI."""

import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Adds the package's parent for both checkout and isolated execution.
import support  # noqa: F401


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_main_and_context_registry_states(self):
        from astrbot.core.star.context import Context
        from astrbot.core.star.star import StarMetadata

        main = importlib.import_module("astrbot_plugin_mmx_cli_tool.main")
        lookup = Context.get_registered_star
        registered = []
        context = SimpleNamespace(add_llm_tools=lambda *tools: registered.extend(tools))
        name = "astrbot_plugin_mmx_cli_tool"
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(main, "get_astrbot_data_path", return_value=temp),
            patch.object(main, "get_shared_temp_dir", return_value=temp),
        ):
            plugin = main.Main(context, {"api_key": ["fake"]})
            try:
                self.assertEqual(13, len(registered))
                # Only substitute registry data, not the discovery implementation.
                with patch.dict(lookup.__globals__, {"star_registry": []}):
                    self.assertIsNone(lookup(context, name))
                metadata = StarMetadata(name=name, activated=False)
                with patch.dict(lookup.__globals__, {"star_registry": [metadata]}):
                    self.assertFalse(lookup(context, name).activated)
                    metadata.activated = True
                    self.assertIsNone(lookup(context, name).star_cls)
                    metadata.star_cls = object()
                    self.assertFalse(
                        callable(
                            getattr(lookup(context, name).star_cls, "get_service", None)
                        )
                    )
                    metadata.star_cls = plugin
                    service = lookup(context, name).star_cls.get_service(api_version=1)
                    self.assertIs(plugin.get_service(), service)
                    self.assertEqual("initializing", service.get_status()["state"])
                    await plugin.initialize()
                    self.assertTrue(service.get_status()["ready"])
                    with self.assertRaises(RuntimeError) as raised:
                        metadata.star_cls.get_service(2)
                    self.assertEqual("unsupported_version", raised.exception.code)
                    await plugin.terminate()
                    self.assertEqual("closed", service.get_status()["state"])
                    self.assertTrue(Path(temp).is_dir())
            finally:
                await plugin.terminate()
