"""MiniMax 多模态工具 — AstrBot 插件入口。

提供 LLM 工具和指令，覆盖 MiniMax 图片/视频/音乐生成、联网搜索、视觉理解和额度查询。

"""

from __future__ import annotations

import html
import re
import time as _time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.message import components as Comp
from astrbot.core.message.components import Record, Video
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .mmx import MiniMaxClient
from .mmx.apis.image import ImageAPI
from .mmx.apis.video import VideoAPI
from .mmx.apis.music import MusicAPI
from .mmx.apis.search import SearchAPI
from .mmx.apis.vision import VisionAPI
from .mmx.apis.quota import QuotaAPI
from .mmx.apis.speech import SpeechAPI
from .mmx.vision_input import extract_image_input
from .mmx.music_command import MusicCommandError, parse_music_command
from .mmx.errors import MiniMaxError, friendly_message
from .mmx.keypool import KeyPool
from .tools import (
    GenerateImageTool,
    GenerateVideoTool,
    QueryVideoTaskTool,
    DownloadVideoTool,
    GenerateMusicTool,
    MusicCoverTool,
    QueryBackgroundTaskTool,
    WebSearchTool,
    DescribeImageTool,
    CheckQuotaTool,
    SpeechSynthesizeTool,
    ListVoicesTool,
)


def _clean_display_text(value: object, *, limit: int | None = None) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _extract_vision_text(result: dict) -> str:
    for key in ("content", "description", "text"):
        text = _clean_display_text(result.get(key))
        if text:
            return text

    choices = result.get("choices", [])
    if choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        text = _clean_display_text(message.get("content"))
        if text:
            return text

    data = result.get("data")
    if isinstance(data, dict):
        for key in ("content", "description", "text"):
            text = _clean_display_text(data.get(key))
            if text:
                return text

    return _clean_display_text(result, limit=2000)


@filter.command_group("mmx")
def mmx_group() -> None:
    """MiniMax 多模态工具指令组"""


class Main(star.Star):
    """MiniMax multi-modal plugin — image, video, music, search, vision, quota."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)

        # 读取配置
        raw_keys = config.get("api_key", [])
        keys: list[str] = (
            [str(k).strip() for k in raw_keys if str(k).strip()]
            if isinstance(raw_keys, list)
            else []
        )
        region = str(config.get("region", "cn"))
        base_url_override = str(config.get("base_url", "")).strip() or None
        timeout = float(config.get("timeout", 300))
        self._video_poll_interval = int(config.get("video_poll_interval", 5))
        self._video_timeout = int(config.get("video_timeout", 600))
        self._default_music_model = str(config.get("default_music_model", "")).strip()

        # 插件数据目录
        _plugin_name = getattr(self, "name", None) or "astrbot_plugin_mmx_cli_tool"
        self._plugin_data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / _plugin_name
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # 创建客户端 — 支持单 Key 和多 Key 池两种模式
        if not keys:
            logger.warning("[mmx] api_key 未配置，插件将无法调用 API")
            self._key_pool = None
            client_kwargs: dict = {
                "api_key": "",
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }
        elif len(keys) > 1:
            self._key_pool = KeyPool(keys, region)
            logger.info(f"[mmx] 多 Key 模式已启用，共 {len(keys)} 个 Key")
            client_kwargs = {
                "key_getter": self._key_pool.get_key,
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }
        else:
            self._key_pool = None
            client_kwargs = {
                "api_key": keys[0],
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }

        self._client = MiniMaxClient(**client_kwargs)

        self._image = ImageAPI(self._client)
        self._video = VideoAPI(self._client)
        self._music = MusicAPI(self._client)
        self._search = SearchAPI(self._client)
        self._vision = VisionAPI(self._client)
        self._quota = QuotaAPI(self._client)
        self._speech = SpeechAPI(self._client)

        # 插件数据目录路径（字符串）
        _data_dir = str(self._plugin_data_dir)

        # 注册 LLM 工具
        context.add_llm_tools(
            GenerateImageTool(self._image),
            GenerateVideoTool(
                self._video, self._video_poll_interval, self._video_timeout
            ),
            QueryVideoTaskTool(self._video),
            DownloadVideoTool(self._video, _data_dir),
            GenerateMusicTool(self._music, _data_dir, self._default_music_model),
            MusicCoverTool(self._music, _data_dir),
            QueryBackgroundTaskTool(),
            WebSearchTool(self._search),
            DescribeImageTool(self._vision),
            CheckQuotaTool(self._quota),
            SpeechSynthesizeTool(self._speech, _data_dir),
            ListVoicesTool(self._speech),
        )

    async def terminate(self) -> None:
        if self._client:
            await self._client.close()

    @mmx_group.command("speech")
    async def mmx_speech(self, event: AstrMessageEvent, *, text: str = ""):
        """语音合成。用法: /mmx speech <文本>"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        text = text or (parts[1] if len(parts) > 1 else "")
        if not text:
            yield event.plain_result(
                "用法: /mmx speech <文本>\n例如: /mmx speech 你好世界"
            )
            return
        try:
            result = await self._speech.synthesize(text=text)
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Speech error: {e}")
            yield event.plain_result(f"语音合成失败: {e}")
            return

        out_path = self._plugin_data_dir / f"mmx_speech_{int(_time.time() * 1000)}.mp3"
        try:
            saved_path = self._speech.save(result, str(out_path))
        except Exception as e:
            logger.warning(f"[mmx] 语音保存失败: {e}")
            yield event.plain_result("语音合成完成，但保存音频失败。")
            return

        if saved_path:
            yield event.chain_result([Record(file=saved_path)])

    @mmx_group.command("image")
    async def mmx_image(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成图片。用法: /mmx image <描述>"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        prompt = prompt or (parts[1] if len(parts) > 1 else "")
        if not prompt:
            yield event.plain_result(
                "用法: /mmx image <描述>。例如: /mmx image a cute cat"
            )
            return
        try:
            result = await self._image.generate(prompt=prompt)
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Image error: {e}")
            yield event.plain_result(f"图片生成失败: {e}")
            return

        data = result.get("data", {})
        urls = data.get("image_urls", [])
        if not urls:
            yield event.plain_result("图片生成失败：未返回链接。")
            return

        saved = []
        try:
            saved = await self._image.save(result, out_dir=str(self._plugin_data_dir))
        except Exception as e:
            logger.warning(f"[mmx] 图片下载失败，将尝试直接发送远程图片: {e}")

        if saved:
            yield event.image_result(saved[0])
            return

        yield event.image_result(urls[0])

    @mmx_group.command("video")
    async def mmx_video(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成视频。用法: /mmx video <描述>"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        prompt = prompt or (parts[1] if len(parts) > 1 else "")
        if not prompt:
            yield event.plain_result("用法: /mmx video <描述>")
            return
        try:
            result = await self._video.generate(prompt=prompt)
            task_id = result.get("task_id", "")
            if not task_id:
                yield event.plain_result("视频任务提交失败：未返回任务 ID。")
                return

            yield event.plain_result(f"⏳ 视频生成中（最长 {self._video_timeout}s）...")
            try:
                final = await self._video.wait_for_completion(
                    task_id,
                    poll_interval=self._video_poll_interval,
                    timeout=self._video_timeout,
                )
                fid = final.get("file_id", "")
                if not fid:
                    yield event.plain_result("视频生成完成，但未返回可下载文件。")
                    return

                try:
                    video_path = str(self._plugin_data_dir / f"mmx_video_{task_id}.mp4")
                    saved = await self._video.download(fid, video_path)
                    yield event.chain_result([Video(file=saved)])
                except Exception as e:
                    logger.warning(f"[mmx] 视频下载失败: {e}")
                    yield event.plain_result(
                        "视频生成完成，但下载失败，无法直接发送视频。"
                    )
            except TimeoutError:
                yield event.plain_result(
                    f"⏰ 视频生成超时。task_id={task_id}，请前往 MiniMax 控制台查看。"
                )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
        except Exception as e:
            logger.error(f"[mmx] Video error: {e}")
            yield event.plain_result(f"视频生成失败: {e}")

    @mmx_group.command("music")
    async def mmx_music(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成音乐。用法: /mmx music <描述> (--lyrics <歌词> | --instrumental | --lyrics-optimizer)"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        raw_args = prompt or (parts[1] if len(parts) > 1 else "")
        try:
            args = parse_music_command(raw_args)
        except MusicCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx music <描述> (--lyrics <歌词> | --instrumental | --lyrics-optimizer)\n"
                "例如: /mmx music 欢乐电子乐 --lyrics-optimizer\n"
                "例如: /mmx music 电影感管弦乐 --instrumental"
            )
            return
        try:
            result = await self._music.generate(
                prompt=args.prompt,
                lyrics=args.lyrics,
                is_instrumental=args.instrumental,
                lyrics_optimizer=args.lyrics_optimizer,
                vocals=args.vocals,
                genre=args.genre,
                mood=args.mood,
                instruments=args.instruments,
                tempo=args.tempo,
                bpm=args.bpm,
                key=args.key,
                avoid=args.avoid,
                use_case=args.use_case,
                structure=args.structure,
                references=args.references,
                extra=args.extra,
                model=args.model or self._default_music_model,
                output_format=args.output_format,
                audio_format=args.audio_format,
                sample_rate=args.sample_rate,
                bitrate=args.bitrate,
                aigc_watermark=args.aigc_watermark,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Music error: {e}")
            yield event.plain_result(f"音乐生成失败: {e}")
            return

        saved_path = None
        out_path = self._plugin_data_dir / f"mmx_music_{int(_time.time() * 1000)}.mp3"
        try:
            saved_path = self._music.save(result, str(out_path))
        except Exception as e:
            logger.warning(f"[mmx] 音乐保存失败: {e}")
            audio_url = result.get("data", {}).get("audio_url", "")
            if audio_url:
                yield event.chain_result([Record(file=audio_url)])
                return
            yield event.plain_result("音乐生成完成，但未返回可发送音频。")
            return

        if saved_path:
            yield event.chain_result([Record(file=saved_path)])

    @mmx_group.command("search")
    async def mmx_search(self, event: AstrMessageEvent, *, query: str = ""):
        """联网搜索。用法: /mmx search <查询>"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        query = query or (parts[1] if len(parts) > 1 else "")
        if not query:
            yield event.plain_result(
                "用法: /mmx search <查询>。例如: /mmx search 今天天气"
            )
            return
        try:
            result = await self._search.query(query)
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Search error: {e}")
            yield event.plain_result(f"搜索失败: {e}")
            return

        items = result.get("organic", result.get("data", result.get("results", [])))
        if not items:
            yield event.plain_result("🔍 未找到相关结果。")
            return

        lines = ["🔍 搜索结果:"]
        for i, item in enumerate(items[:5], 1):
            if isinstance(item, dict):
                title = _clean_display_text(item.get("title"), limit=80) or f"结果 {i}"
                url = _clean_display_text(item.get("url") or item.get("link"))
                snippet = _clean_display_text(
                    item.get("snippet")
                    or item.get("summary")
                    or item.get("content")
                    or item.get("description"),
                    limit=180,
                )
                lines.append(f"\n{i}. **{title}**")
                if url:
                    lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet[:200]}")
        yield event.plain_result("\n".join(lines))

    @mmx_group.command("vision")
    async def mmx_vision(self, event: AstrMessageEvent, *, prompt: str = ""):
        """图片理解。用法: /mmx vision（支持当前消息带图或引用图片）"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        prompt = prompt or (parts[1] if len(parts) > 1 else "Describe the image.")

        image_input, saw_image = await extract_image_input(
            event.get_messages(),
            image_type=Comp.Image,
            reply_type=Comp.Reply,
            event=event,
        )
        if not image_input:
            if saw_image:
                yield event.plain_result(
                    "检测到了图片，但当前无法在本地解析该图片。请改为直接发送图片，或引用一张仍可访问的图片后重试。"
                )
                return
            yield event.plain_result(
                "请附带一张图片，或引用一张图片后再发送 /mmx vision 指令。"
            )
            return

        try:
            result = await self._vision.describe(image=image_input, prompt=prompt)
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Vision error: {e}")
            yield event.plain_result(f"图片理解失败: {e}")
            return

        desc = _extract_vision_text(result)
        yield event.plain_result(f"🖼️ 图片分析:\n\n{desc}")

    @mmx_group.command("quota")
    async def mmx_quota(self, event: AstrMessageEvent, *, index: str = ""):
        """查询 MiniMax API 额度。用法: /mmx quota [序号]
        不带参数显示所有 Key 统合额度。带序号（如 /mmx quota 1）显示指定 Key 详情。
        """
        # 收集所有 Key
        keys_to_check: list[tuple[int, str]] = []  # (index, key)
        if self._key_pool is not None:
            for s in self._key_pool._states:
                keys_to_check.append((s.index, s.key))
        else:
            keys_to_check.append((0, self._client._api_key or ""))

        # 指定序号则只查该 Key
        if index.strip():
            try:
                idx = int(index.strip()) - 1
            except ValueError:
                yield event.plain_result("序号无效，请输入数字。如 /mmx quota 1")
                return
            filtered = [(i, k) for i, k in keys_to_check if i == idx]
            if not filtered:
                yield event.plain_result(
                    f"Key 序号 {index} 不存在，共 {len(keys_to_check)} 个 Key。"
                )
                return
            keys_to_check = filtered

        # 并发查询所有 Key 的额度
        import asyncio
        from .mmx.endpoints import quota_endpoint

        async def _fetch(api_key: str):
            try:
                import httpx

                async with httpx.AsyncClient(timeout=10) as cl:
                    r = await cl.get(
                        quota_endpoint(self._client.base_url),
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if r.is_success:
                        return r.json().get("model_remains", [])
            except Exception:
                pass
            return []

        tasks = [_fetch(k) for _, k in keys_to_check]
        results = await asyncio.gather(*tasks)

        # 统合模式：按模型聚合所有 Key 的额度
        if not index.strip() and len(keys_to_check) > 1:
            merged: dict[str, dict] = {}
            exhausted_keys: list[int] = []

            for (ki, _), model_remains in zip(keys_to_check, results):
                if not model_remains:
                    exhausted_keys.append(ki + 1)
                    continue
                for m in model_remains:
                    name = m.get("model_name", "unknown")
                    total = m.get("current_interval_total_count", 0)
                    used = m.get("current_interval_usage_count", 0)
                    if name not in merged:
                        merged[name] = {"total": 0, "used": 0, "remaining": 0}
                    merged[name]["total"] += total
                    merged[name]["used"] += used
                    merged[name]["remaining"] += max(total - used, 0)

            lines = [f"💰 MiniMax 统合额度（{len(keys_to_check)} 个 Key）:"]
            if not merged:
                lines.append("所有 Key 均无额度信息。")
            else:
                for name, m in sorted(merged.items()):
                    lines.append(
                        f"- {name}: 已用 {m['used']} / 总计 {m['total']} (剩余 {m['remaining']})"
                    )
            if exhausted_keys:
                lines.append(f"\n⚠️ Key 序号 {exhausted_keys} 无额度信息或查询失败。")
            yield event.plain_result("\n".join(lines))
            return

        # 单 Key 详情模式
        for (ki, key), model_remains in zip(keys_to_check, results):
            masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "***"
            lines = [f"💰 Key [{ki + 1}] {masked} 额度:"]
            if not model_remains:
                lines.append("查询失败或无额度信息。")
            else:
                for m in model_remains:
                    total = m.get("current_interval_total_count", 0)
                    used = m.get("current_interval_usage_count", 0)
                    name = m.get("model_name", "unknown")
                    lines.append(
                        f"- {name}: 已用 {used} / 总计 {total} (剩余 {max(total - used, 0)})"
                    )
            yield event.plain_result("\n".join(lines))
