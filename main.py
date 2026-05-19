"""MiniMax 多模态工具 — AstrBot 插件入口。

提供 LLM 工具和指令，覆盖 MiniMax 图片/视频/音乐生成、联网搜索、视觉理解和额度查询。

"""

from __future__ import annotations

import time as _time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.message.components import Record, Video
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .mmx import MiniMaxClient
from .mmx.apis.image import ImageAPI
from .mmx.apis.video import VideoAPI
from .mmx.apis.music import MusicAPI
from .mmx.apis.search import SearchAPI
from .mmx.apis.vision import VisionAPI
from .mmx.apis.quota import QuotaAPI
from .mmx.errors import MiniMaxError, friendly_message
from .mmx.keypool import KeyPool
from .tools import (
    GenerateImageTool,
    GenerateVideoTool,
    GenerateMusicTool,
    WebSearchTool,
    DescribeImageTool,
    CheckQuotaTool,
)


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

        # 注册 LLM 工具
        context.add_llm_tools(
            GenerateImageTool(self._image),
            GenerateVideoTool(
                self._video, self._video_poll_interval, self._video_timeout
            ),
            GenerateMusicTool(self._music),
            WebSearchTool(self._search),
            DescribeImageTool(self._vision),
            CheckQuotaTool(self._quota),
        )

    async def terminate(self) -> None:
        if self._client:
            await self._client.close()

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

        saved = None
        if urls:
            try:
                saved = await self._image.save(
                    result, out_dir=str(self._plugin_data_dir)
                )
            except Exception:
                pass

        lines = ["✅ 图片生成完成"]
        for i, u in enumerate(urls, 1):
            lines.append(f"{i}. {u}")
        yield event.plain_result("\n".join(lines))
        if saved:
            yield event.image_result(saved[0])
        elif urls:
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
            yield event.plain_result(
                f"🎬 视频任务已提交\n\ntask_id: {task_id}\n状态: {result.get('status', 'Queueing')}"
            )
            if task_id:
                yield event.plain_result(
                    f"⏳ 等待生成完成（最长 {self._video_timeout}s）..."
                )
                try:
                    final = await self._video.wait_for_completion(
                        task_id,
                        poll_interval=self._video_poll_interval,
                        timeout=self._video_timeout,
                    )
                    fid = final.get("file_id", "")
                    yield event.plain_result(
                        f"✅ 视频生成完成\n\nfile_id: {fid}\ntask_id: {task_id}"
                    )
                    if fid:
                        try:
                            video_path = str(
                                self._plugin_data_dir / f"mmx_video_{task_id}.mp4"
                            )
                            saved = await self._video.download(fid, video_path)
                            yield event.chain_result([Video(file=saved)])
                        except Exception as e:
                            logger.warning(f"[mmx] 视频下载失败: {e}")
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
        """生成音乐。用法: /mmx music <描述> [--instrumental]"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        prompt = prompt or (parts[1] if len(parts) > 1 else "")
        if not prompt:
            yield event.plain_result(
                "用法: /mmx music <描述>\n例如: /mmx music warm acoustic guitar, folk style\n加 --instrumental 生成纯器乐"
            )
            return
        try:
            result = await self._music.generate(
                prompt=prompt,
                is_instrumental="--instrumental" in event.message_str,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Music error: {e}")
            yield event.plain_result(f"音乐生成失败: {e}")
            return

        lines = ["🎵 音乐生成完成"]
        saved_path = None
        out_path = self._plugin_data_dir / f"mmx_music_{int(_time.time() * 1000)}.mp3"
        try:
            saved_path = self._music.save(result, str(out_path))
            lines.append(f"已保存到: {saved_path}")
        except Exception:
            pass

        yield event.plain_result("\n".join(lines))
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
                title = item.get("title", f"结果 {i}")
                url = item.get("url", "")
                snippet = item.get("snippet", "")
                lines.append(f"\n{i}. **{title}**")
                if url:
                    lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet[:200]}")
        yield event.plain_result("\n".join(lines))

    @mmx_group.command("vision")
    async def mmx_vision(self, event: AstrMessageEvent, *, prompt: str = ""):
        """图片理解。用法: /mmx vision（需要引用一张图片）"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        prompt = prompt or (parts[1] if len(parts) > 1 else "Describe the image.")

        # 从消息链中提取图片
        message_chain = event.get_messages()
        image_url = None
        for comp in message_chain:
            ct = getattr(comp, "type", "")
            if ct.lower() in ("image",):
                image_url = getattr(comp, "url", None) or getattr(comp, "file", None)
                break

        if not image_url:
            yield event.plain_result("请引用一张图片并附带 /mmx vision 指令。")
            return

        try:
            result = await self._vision.describe(image=image_url, prompt=prompt)
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Vision error: {e}")
            yield event.plain_result(f"图片理解失败: {e}")
            return

        desc = result.get("description", result.get("text", ""))
        choices = result.get("choices", [])
        if not desc and choices:
            desc = choices[0].get("message", {}).get("content", "")
        if not desc:
            desc = str(result)[:2000]
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
