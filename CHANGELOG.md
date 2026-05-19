# 更新日志

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
