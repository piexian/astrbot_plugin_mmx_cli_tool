from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
        self.plugin._speech.save = AsyncMock(return_value=str(self.root / "speech.mp3"))
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
        self.assertEqual(17, len(self.registered))
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

    async def test_image_tool_saves_to_temp_dir_and_prefers_local_path(self):
        generated = {
            "data": {"image_urls": ["https://example.com/i.png"], "task_id": "t1"}
        }
        self.plugin._image.generate = AsyncMock(return_value=generated)
        saved = str(self.plugin._cache_dir / "mmx_image_1.png")
        self.plugin._image.save = AsyncMock(return_value=[saved])
        tool = next(t for t in self.registered if t.name == "mmx_generate_image")
        result = json.loads(await tool.call(None, prompt="cat"))
        self.assertTrue(result["ok"])
        self.assertEqual([saved], result["file_paths"])
        self.assertIn("本地路径", result["hint"])
        self.assertEqual(generated, self.plugin._image.save.call_args.args[0])
        self.assertEqual(
            str(self.plugin._cache_dir),
            self.plugin._image.save.call_args.kwargs["out_dir"],
        )

    async def test_image_tool_falls_back_to_urls_when_download_fails(self):
        self.plugin._image.generate = AsyncMock(return_value={
            "data": {"image_urls": ["https://example.com/i.png"], "task_id": "t1"}
        })
        self.plugin._image.save = AsyncMock(side_effect=OSError("cdn down"))
        tool = next(t for t in self.registered if t.name == "mmx_generate_image")
        result = json.loads(await tool.call(None, prompt="cat"))
        self.assertTrue(result["ok"])
        self.assertNotIn("file_paths", result)
        self.assertEqual(["https://example.com/i.png"], result["image_urls"])

    async def test_video_tool_downloads_after_wait_then_falls_back(self):
        self.plugin._video.generate = AsyncMock(
            return_value={"task_id": "t1", "status": "Processing"}
        )
        self.plugin._video.wait_for_completion = AsyncMock(
            return_value={"file_id": "f1", "video_url": "https://example.com/v.mp4"}
        )
        saved = str(self.plugin._cache_dir / "mmx_video_t1.mp4")
        self.plugin._video.download = AsyncMock(return_value=saved)
        tool = next(t for t in self.registered if t.name == "mmx_generate_video")
        result = json.loads(await tool.call(None, prompt="scene"))
        self.assertEqual(saved, result["file_path"])
        self.assertIn("本地路径", result["hint"])
        self.plugin._video.generate = AsyncMock(
            return_value={"task_id": "../escape", "status": "Processing"}
        )
        await tool.call(None, prompt="scene")
        self.assertEqual(
            self.plugin._cache_dir,
            Path(self.plugin._video.download.call_args.args[1]).parent,
        )
        self.plugin._video.download = AsyncMock(side_effect=OSError("cdn down"))
        result = json.loads(await tool.call(None, prompt="scene"))
        self.assertNotIn("file_path", result)
        self.assertEqual("https://example.com/v.mp4", result["video_url"])
        tool._default_model = "I2V-Fast"
        fast_err = json.loads(await tool.call(None, prompt="scene"))
        self.assertFalse(fast_err["ok"])
        self.assertIn("firstFrame", fast_err["error"])
        query_tool = next(
            t for t in self.registered if t.name == "mmx_video_task_get"
        )
        query_tool._default_model = "MiniMax-H3"
        self.plugin._video.get_task = AsyncMock(
            return_value={
                "status": "succeeded",
                "content": {"url": "https://example.com/v2.mp4"},
            }
        )
        queried = json.loads(await query_tool.call(None, taskId="t1"))
        self.assertEqual("MiniMax-H3", self.plugin._video.get_task.call_args.kwargs["model"])
        self.assertIn("videoUrl", queried["hint"])
        download_tool = next(
            t for t in self.registered if t.name == "mmx_video_download"
        )
        self.plugin._video.download = AsyncMock(return_value=saved)
        downloaded = json.loads(
            await download_tool.call(None, videoUrl="https://example.com/v2.mp4")
        )
        self.assertEqual(saved, downloaded["file_path"])
        self.assertEqual(
            "https://example.com/v2.mp4",
            self.plugin._video.download.call_args.kwargs["video_url"],
        )

    async def test_image_command_reads_prompt_and_flags_from_message_tail(self):
        # AstrBot 只把首个词交给 str 参数，唤醒前缀已在指令处理前剥掉
        self.plugin._image.generate = AsyncMock(
            return_value={"data": {"image_urls": ["https://example.com/i.png"]}}
        )
        self.plugin._image.save = AsyncMock(return_value=[])
        replies = [
            r
            async for r in self.plugin.mmx_image(
                Event("mmx image 一只猫 --aspect-ratio 16:9"), prompt="一只猫"
            )
        ]
        self.assertEqual("一只猫", self.plugin._image.generate.call_args.kwargs["prompt"])
        self.assertEqual(
            "16:9", self.plugin._image.generate.call_args.kwargs["aspect_ratio"]
        )
        self.assertEqual(["https://example.com/i.png"], replies)

        self.plugin._image.generate.reset_mock()
        prefixed = [
            r
            async for r in self.plugin.mmx_image(
                Event("/mmx image 一只猫 --aspect-ratio 16:9"), prompt="一只猫"
            )
        ]
        self.assertEqual(["https://example.com/i.png"], prefixed)
        self.assertEqual("16:9", self.plugin._image.generate.call_args.kwargs["aspect_ratio"])

        empty = [r async for r in self.plugin.mmx_image(Event("mmx image"), prompt="")]
        self.assertIn("用法", empty[0])
        # 裸指令不能把子指令名 "image" 当描述去生成
        self.assertEqual(1, self.plugin._image.generate.await_count)

    async def test_video_command_reads_prompt_from_message_tail(self):
        self.plugin._video.generate = AsyncMock(return_value={"task_id": "t1"})
        self.plugin._video.wait_for_completion = AsyncMock()
        replies = [
            r
            async for r in self.plugin.mmx_video(
                Event("mmx video 一只猫在跳舞 --no-wait"), prompt="一只猫在跳舞"
            )
        ]
        self.assertEqual(
            "一只猫在跳舞", self.plugin._video.generate.call_args.kwargs["prompt"]
        )
        self.assertIn("task_id=t1", replies[0])
        self.plugin._video.wait_for_completion.assert_not_awaited()

    async def test_search_command_reads_full_query_from_message_tail(self):
        self.plugin._search.query = AsyncMock(return_value={"organic": []})
        replies = [
            r
            async for r in self.plugin.mmx_search(
                Event("mmx search 今天 天气 怎么样"), query="今天"
            )
        ]
        self.assertEqual("今天 天气 怎么样", self.plugin._search.query.call_args.args[0])
        self.assertEqual(["🔍 未找到相关结果。"], replies)

    async def test_vision_command_reads_prompt_from_message_tail(self):
        self.plugin._vision.describe = AsyncMock(return_value={"content": "猫"})
        with patch.object(
            self.main,
            "extract_image_input",
            AsyncMock(return_value=("https://example.com/i.png", False)),
        ):
            replies = [
                r
                async for r in self.plugin.mmx_vision(
                    Event("mmx vision 详细 描述 这张图"), prompt="详细"
                )
            ]
        self.assertEqual(
            "详细 描述 这张图", self.plugin._vision.describe.call_args.kwargs["prompt"]
        )
        self.assertIn("猫", replies[0])

    async def test_file_command_reads_subcommand_args_from_message_tail(self):
        self.plugin._files.upload = AsyncMock(
            return_value={"file": {"file_id": "f1", "filename": "audio.mp3"}}
        )
        replies = [
            r
            async for r in self.plugin.mmx_file(
                Event("mmx file upload --file audio.mp3"), args="upload"
            )
        ]
        uploaded = self.plugin._files.upload.call_args.args[0]
        self.assertTrue(uploaded.endswith("audio.mp3"))
        self.assertEqual("retrieval", self.plugin._files.upload.call_args.kwargs["purpose"])
        self.assertIn("文件上传完成", replies[0])

    async def test_speech_command_reads_full_text_from_message_tail(self):
        replies = [
            r
            async for r in self.plugin.mmx_speech(
                Event("mmx speech 你好 世界 --voice explicit-voice"), text="你好"
            )
        ]
        self.assertTrue(replies)
        self.assertEqual("你好 世界", self.plugin._speech.synthesize.call_args.kwargs["text"])
        self.assertEqual("explicit-voice", self.plugin._speech.synthesize.call_args.kwargs["voice"])

    async def test_quota_command_reads_page_from_message_tail(self):
        self.plugin._quota.info = AsyncMock(return_value={"model_remains": []})
        replies = [
            r async for r in self.plugin.mmx_quota(Event("mmx quota page 2"), index="page")
        ]
        self.assertIn("页码 2 不存在", replies[0])
