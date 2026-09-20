from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from support import Event, File, Record, Reply, plugin_modules


class PluginWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main, self.tools = self.enterContext(plugin_modules(self.root))
        self.registered = []
        self.context = SimpleNamespace(add_llm_tools=lambda *tools: self.registered.extend(tools))
        self.plugin = self.main.Main(self.context, {
            "api_key": ["test-key"], "default_speech_voice": "configured-voice",
            "default_speech_model": "speech-2.8-hd",
        })
        self.addAsyncCleanup(self.plugin.terminate)
        self.plugin._speech.synthesize = AsyncMock(return_value={"data": {"audio": "01"}})
        self.plugin._speech.save = Mock(return_value=str(self.root / "speech.mp3"))
        self.plugin._transcription._api.transcribe = AsyncMock(return_value={"text": "转写结果", "duration": 1})
        self.audio = self.plugin._plugin_data_dir / "audio.mp3"
        self.audio.write_bytes(b"audio")

    async def test_default_voice_reaches_direct_and_tool_calls(self):
        results = [item async for item in self.plugin.mmx_speech(Event(), text="你好")]
        self.assertTrue(results)
        self.assertEqual("configured-voice", self.plugin._speech.synthesize.call_args.kwargs["voice"])
        tool = next(t for t in self.registered if t.name == "mmx_speech_synthesize")
        result = json.loads(await tool.call(None, text="你好"))
        self.assertTrue(result["ok"])
        self.assertEqual("configured-voice", self.plugin._speech.synthesize.call_args.kwargs["voice"])

    async def test_explicit_voice_wins(self):
        results = [item async for item in self.plugin.mmx_speech(Event(), text="你好 --voice explicit-voice")]
        self.assertTrue(results)
        self.assertEqual("explicit-voice", self.plugin._speech.synthesize.call_args.kwargs["voice"])
        tool = next(t for t in self.registered if t.name == "mmx_speech_synthesize")
        await tool.call(None, text="你好", voice="tool-voice")
        self.assertEqual("tool-voice", self.plugin._speech.synthesize.call_args.kwargs["voice"])

    async def test_quota_entrypoints_use_shared_semantics(self):
        item = {
            "model_name": "general", "current_interval_total_count": 100,
            "current_interval_usage_count": 80, "current_interval_remaining_percent": 80,
            "remains_time": 59999, "start_time": 0, "end_time": 43200000,
        }
        self.plugin._quota.info = AsyncMock(return_value={"model_remains": [item]})
        result = [r async for r in self.plugin.mmx_quota(Event())]
        self.assertIn("12小时额度: 已用20%（59秒后重置）", result[0])
        tool = next(t for t in self.registered if t.name == "mmx_check_quota")
        summary = json.loads(await tool.call(None))["models"][0]
        self.assertEqual("已用20%", summary["current"])
        self.assertEqual("59秒", summary["current_reset"])
        self.assertEqual("12小时额度", summary["current_window"])

    async def test_transcription_registration_and_command_aliases(self):
        self.assertEqual(16, len(self.registered))
        for action in ("transcribe", "recognize"):
            results = [r async for r in self.plugin.mmx_speech(Event(f"/mmx speech {action} --file audio.mp3"))]
            self.assertEqual(["转写结果"], results)
        self.plugin._speech.synthesize.assert_not_awaited()

    async def test_transcription_tool_arguments_and_schema(self):
        tool = next(t for t in self.registered if t.name == "mmx_speech_transcribe")
        result = json.loads(await tool.call(None, file="audio.mp3", language="zh", stream=True))
        self.assertTrue(result["ok"])
        options = self.plugin._transcription._api.transcribe.call_args.args[2]
        self.assertEqual("zh", options.language)
        self.assertTrue(options.stream)
        self.assertNotIn('"default":', json.dumps(tool.parameters))
        self.assertNotIn('"oneOf":', json.dumps(tool.parameters))
        self.assertNotIn("required", tool.parameters)

    async def test_current_and_quoted_audio_are_resolved(self):
        for messages in ([Record(file=str(self.audio))], [Reply(chain=[File(name="audio.mp3", file=str(self.audio))])]):
            with self.subTest(messages=messages):
                event = Event(messages=messages)
                results = [r async for r in self.plugin.mmx_speech(event, text="transcribe")]
                self.assertEqual(["转写结果"], results)
                tool = next(t for t in self.registered if t.name == "mmx_speech_transcribe")
                context = SimpleNamespace(context=SimpleNamespace(event=event))
                self.assertTrue(json.loads(await tool.call(context))["ok"])

    async def test_transcription_sends_subtitle_file(self):
        self.plugin._transcription._api.transcribe.return_value = "WEBVTT\n\n字幕"
        results = [r async for r in self.plugin.mmx_speech(Event(), text="transcribe --file audio.mp3 --response-format vtt")]
        self.assertIsInstance(results[0][0], File)
        path = Path(results[0][0].file_)
        self.assertEqual(".vtt", path.suffix)
        self.assertEqual("WEBVTT\n\n字幕", path.read_text())

    async def test_invalid_transcription_does_not_call_api_or_tts(self):
        tool = next(t for t in self.registered if t.name == "mmx_speech_transcribe")
        for kwargs in ({}, {"file": "audio.mp3", "stream": True, "responseFormat": "srt"}, {"file": "../secret.mp3"}):
            result = json.loads(await tool.call(None, **kwargs))
            self.assertFalse(result["ok"])
            self.assertIn("hint", result)
        results = [r async for r in self.plugin.mmx_speech(Event(), text="transcribe --file audio.mp3 --stream --out a.json")]
        self.assertIn("不能同时", results[0])
        self.plugin._transcription._api.transcribe.assert_not_awaited()
        self.plugin._speech.synthesize.assert_not_awaited()

    async def test_tts_text_escape_keeps_reserved_words(self):
        results = [r async for r in self.plugin.mmx_speech(Event(), text='--text "transcribe this sentence"')]
        self.assertTrue(results)
        self.assertEqual("transcribe this sentence", self.plugin._speech.synthesize.call_args.kwargs["text"])
        self.plugin._transcription._api.transcribe.assert_not_awaited()
