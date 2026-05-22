# 更新日志

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
