# 更新日志

## 0.2.6 - 2026-06-04

- 文件管理入口收紧为管理员可用：`/mmx file upload|list|delete` 与 `mmx_file_upload/list/delete` 均会拒绝非管理员调用。
- 收紧本地媒体路径：LLM 工具与直接指令中手写的图片/音频路径仅允许读取插件数据目录内文件，避免通过 `subjectRef`、视频首尾帧/角色图、视觉理解或翻唱 `audioFile` 读取任意宿主文件。
- 收紧视频下载路径：`mmx_video_download` 的 `out` 仅允许写入插件数据目录内，拒绝绝对路径和 `..` 穿越。

## 0.2.5 - 2026-06-03

- 同步本地 `mmx-cli@1.0.16` 的 Token Plan 额度查询逻辑，优先使用 `/v1/api/openplatform/coding_plan/remains`，并保留旧端点回退。
- 额度展示区分普通模型与视频额度：普通模型显示已用百分比，视频额度显示已用/剩余（上限），当前周期文案改为五小时额度，有限额度附带重置倒计时；无限额度直接显示 `∞`，有明确计数字段时优先按计数字段计算百分比。`/mmx quota` 多 Key 默认逐 Key 分页展示，每页最多 3 个，新增 `/mmx quota page <页码>` 翻页；LLM 工具继续返回合并额度并提示 Key 数量。
- 音乐生成默认模型更新为 `music-2.6`，显式 `model` 参数有效值同步为 `music-2.6`、`music-2.5+`、`music-2.5`。
- 音乐翻唱默认模型更新为 `music-cover`，显式 `model` 参数有效值同步为 `music-cover`。
- `/mmx speech` 与 `mmx_speech_synthesize` 新增 `pronunciation` 支持，自动转换为 MiniMax `pronunciation_dict`。
- 新增 `mmx_file_upload`、`mmx_file_list`、`mmx_file_delete` LLM 工具，以及 `/mmx file upload|list|delete` 直接指令。
- 文件上传改为复用共享 `MiniMaxClient` 鉴权与多 Key 选择；LLM 文件上传限制在插件数据目录内，避免任意宿主文件读取风险。

## 0.2.4 - 2026-06-01

- 补齐 `/mmx image`、`/mmx video`、`/mmx speech` 直接指令的常用本地 `mmx` CLI 参数。
- `mmx_generate_image` 新增 `aigcWatermark` 与 `responseFormat`，并支持 `type=character,image=...` 形式的 `subjectRef`。
- `mmx_speech_synthesize` 新增 `sampleRate`、`bitrate`、`channels`、`subtitles` 参数。
- `mmx_music_cover` 新增 `model`、`lyricsFile`、`format`、`sampleRate`、`bitrate`、`channel` 参数，并补 `/mmx music cover` 直接指令。
- 图片、视频与翻唱直接指令支持从当前消息或引用消息中解析图片/音频附件。
- 图片、视频、语音、音乐与翻唱默认模型统一读取插件配置，不在代码里硬编码模型默认值。

## 0.2.3 - 2026-06-01

- 补齐 `/mmx music` 直接指令参数，对齐本地 `mmx music generate` 的 `--lyrics`、`--lyrics-optimizer`、`--instrumental` 以及风格/音频控制参数。
- `mmx_generate_music` 新增 `aigcWatermark` 参数，并透传为 MiniMax 请求中的 `aigc_watermark`。

## 0.2.2 - 2026-06-01

- 兼容 AstrBot v4.25.x 中 `ToolExecResult` 变为联合类型别名的行为，LLM 工具统一返回普通 JSON 字符串，修复 `'types.UnionType' object is not callable`。
- 新增 `mmx_background_task_get` 统一后台任务查询工具，`mmx_generate_music` 与 `mmx_music_cover` 会返回插件内部 `task_id`、`query_tool`、`max_wait_seconds` 和 `poll_after_seconds`，避免 AI 通过 sleep 或读日志猜测任务状态。
- 音乐生成与翻唱结果不再返回 MiniMax 原始 `audio` hex 或 `raw_data`，改为保存到插件数据目录并仅返回 `file_path` 或 `audio_url`。
- 音乐生成与翻唱改为插件后台任务，避免长时间生成（可能数分钟）阻塞对话或触发工具超时。

## 0.2.1 - 2026-05-30

- 修复 `mmx_describe_image` 工具声明中使用 `oneOf` 导致部分 OpenAI 兼容层转发 Gemini 时请求 400 的问题。
- 统一 LLM 工具参数 Schema 为更保守的跨 provider 公共子集，移除工具声明中的 `default` 与空 `required`，复杂互斥校验保留在 Python `call()` 运行时执行。
- 新增工具 Schema 单元测试，防止后续重新引入 `oneOf`、`default`、空 `required` 或未声明 required 字段。

## 0.2.0 - 2026-05-22

- **新增语音合成 (TTS) 工具**：
  - `mmx_speech_synthesize`：将文本合成语音，支持 30+ 种系统音色与语速/音量/音高微调，生成并直接返回本地音频文件。
  - `mmx_speech_voices`：查询并列出 MiniMax TTS 当前支持的可用音色列表。
- **新增视频生成异步查询与下载工具**：
  - `mmx_video_task_get`：查询视频生成任务状态与进度，防止长时间生成超时导致的失败。
  - `mmx_video_download`：通过任务或文件 ID 下载已生成的视频到本地。
- **新增音乐翻唱工具**：
  - `mmx_music_cover`：基于参考音频（支持 URL 或本地文件路径）以及风格描述生成翻唱。
- **新增 `/mmx speech` 快捷命令**：用户可以通过命令快捷进行语音合成并由机器人直接发送语音消息。
- **官方 `mmx` CLI 参数对齐**：
  - 全面采用官方一致的 camelCase 风格参数，移除冗余别名（不再向下兼容旧别名）：
    - 图片生成：使用 `aspectRatio`, `promptOptimizer`, `subjectRef`, `seed`, `width`, `height`, `model`。
    - 视频生成：使用 `firstFrame`, `lastFrame`, `subjectImage`, `noWait`，支持首尾帧模式与角色一致性模式下自动匹配 `Hailuo-02` 和 `S2V-01` 模型。
    - 音乐生成：使用 `instrumental`, `lyricsOptimizer`, `bpm`, `avoid`, `useCase`, `structure`, `references`, `extra` 等精细控制参数。
    - 联网搜索：主参数改为 `q`。
    - 视觉理解：主参数改为 `image`，并支持指定 `fileId`。
- **引入智能错误提示系统**：
  - 当工具调用遇到参数缺失、选项冲突（如歌词与纯乐器冲突）或校验失败时，不抛出异常，而是返回结构化的 JSON 错误信息，内含 `hint`（AI 纠正建议，引导 LLM 自动纠正参数重新调用）、`example`（正确传参示例）和 `docs` 官方文档链接。

## 0.1.2 - 2026-05-20

- 修复 MiniMax 客户端对完整 API URL 重复拼接 `base_url` 的问题，解决除额度查询外的接口可能报 `Name or service not known` 的错误。
- 优化 `/mmx vision` 的 QQ 图片处理，当前消息和引用图片会优先解析并落地为本地临时文件，再提交给 MiniMax 视觉接口。
- 优化 `/mmx vision` 和 `/mmx search` 的直接指令输出，避免把 API 原始 JSON 或 HTML 片段原样发送到聊天。
- 优化 `/mmx image`、`/mmx video` 和 `/mmx music` 的直接指令输出，生成成功后优先只发送媒体文件，不再同时暴露临时链接、`file_id` 或本地保存路径。
- 兼容 AstrBot v4.24.5 的 FunctionTool 调用方式，将工具处理入口更新为 `call()`，修复 `Tool must have a valid handler or override 'run' method`。
- 补充 `file://`、`base64://` 和 `data:image/...` 图片输入兼容，降低外链不可访问时的失败概率。

## 0.1.1 - 2026-05-19

- 修复 `/mmx vision` 在 QQ 场景下优先依赖图片外链导致的解析失败问题，改为优先使用 AstrBot 图片组件转换出的本地文件路径。
- 修复 `/mmx vision` 只能读取当前消息图片、无法读取引用消息图片的问题，现已兼容当前消息带图和引用图片两种输入方式。
- 同步更新 README 中 `vision` 指令说明，避免文档与实际行为不一致。
