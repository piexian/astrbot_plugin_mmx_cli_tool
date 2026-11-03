# 开发者文档

这份文档放的是技术细节（安全规则、内部机制等）。普通使用请看 [README](../README.md)，项目结构、插件协作 SDK 和开发验证也在 README 里；其他插件调用本插件的完整说明见 [plugin-api.md](plugin-api.md)。

## 参数命名

- 直接指令使用 kebab-case（短横线）参数，如 `--aspect-ratio`、`--sample-rate`。图片、语音、视频指令的解析器同时接受对应的 camelCase 写法（如 `--aspectRatio`）。
- LLM 工具参数使用 JSON Tool 声明里的名称，如 `aspectRatio`、`sampleRate`、`responseFormat`、`timestampLevel`。具体以各工具的声明为准。

## 智能错误提示

插件对 LLM 的参数错误做了自动纠错反馈。当 LLM 传入的参数缺失、类型错误或模式冲突（例如视频的首尾帧与角色参考互斥）时，工具不会直接抛异常，而是返回结构化 JSON：

- `ok`：`false`，表示调用失败。
- `error`：明确的错误原因。
- `hint`：给 AI 的参数纠正建议。
- `example`：正确的传参 JSON 示例。
- `docs`：MiniMax 官方 API 文档链接。

## 安全设计

- **API Key 脱敏**：错误日志中自动隐藏 API Key，仅显示前 4 位和后 4 位。
- **文件名安全**：自动保存使用时间戳或随机 ID 命名，避免路径注入。
- **文件管理权限**：`/mmx file upload|list|delete` 与 `mmx_file_upload`、`mmx_file_list`、`mmx_file_delete` 仅管理员可用。
- **可读目录**：LLM 工具和直接指令里手写的图片、音频路径，以及 `mmx_file_upload` 的上传文件，仅允许位于插件数据目录（`data/plugin_data/astrbot_plugin_mmx_cli_tool/`）或 AstrBot 临时目录内，避免模型诱导读取任意宿主文件。当前消息和引用消息中的附件仍由 AstrBot 解析后提交。
- **临时目录信任面**：AstrBot 临时目录由所有插件共享（聊天附件、其他插件的临时文件都在其中）。放行该目录意味着 LLM 工具可读取其中的任意文件，并非「仅聊天图片」。配置项 `allow_astrbot_temp_dir` 关闭后只允许插件数据目录，代价是生成结果无法再作为输入。
- **可写目录**：生成的媒体和转写文件默认保存到 AstrBot 临时目录。`mmx_video_download` 以及转写指令的 `out` 仅允许写入插件数据目录，并拒绝绝对路径和 `..` 穿越。
- **转写附件下载**：仅下载平台解析出的附件 URL，不携带 MiniMax Key，限制 50 MB、60 秒并拒绝重定向；附件来源沿用平台的信任边界。
- **参数验证**：工具入口处校验关键参数。

## MiniMax Code 对话通道

- `minimax_code` 适配器类型会注册进 AstrBot，WebUI「新增供应商」始终可以选择 MiniMax Code。
- 对话使用 Claude Messages 协议直连 `agent.minimax.cn` / `agent.minimax.io`。请求区域只跟随 `/mmx login` 的登录区域，提供商源配置里不再需要 `region`（旧配置里的该字段被忽略，未登录时兜底国内站）。
- 提供商源的 API Base URL 与 API Key 只是占位提醒，实际请求由插件注入 OAuth 令牌；令牌到期或遇到 401 时自动刷新并重试。
- 「获取模型列表」按钮实时拉取云端公开目录 `/mavis/api/v1/models`，无需登录。
- 两种方式（自动创建、手动添加）同时使用时，手动创建的源 ID 可能带后缀。同 ID 且类型为 `minimax_code` 的手动源视为用户自管，不会被接管，也不算冲突。源 ID `mmx_minimax_code` 若被其他类型的配置占用，插件跳过写入并在工作台弹出告警，同时回退为运行时注册。
- AstrBot 的 `conversation_id` 会稳定转写为 Mavis 会话 ID，令牌刷新后保持不变。登录态带有持久化的 login epoch：令牌刷新时继承，换账号或换区域后由插件后端替换，并关闭旧的运行时供应商实例，因此换账号不会复用旧会话。
- 出站请求按白名单头重建，只发送 Authorization、MiniMaxAgent 与 X-Mavis 头；AstrBot 头、SDK 遥测头和提供商源里的 `custom_headers` 都不会透传。
- 卸载插件前必须先删除所有 `minimax_code` 类型的提供商源（不仅是 `mmx_minimax_code`）及其模型条目，否则 AstrBot 重启时会找不到 `minimax_code` 适配器。
- `auto_signin` 开启后每 30 分钟巡检一次，调用签到接口领取当日积分；上游按天幂等，重复领取不会叠加。

## Agent Matrix 网关

搜图、以图搜图、AI 音乐、AI 音效（`mmx_search_images`、`mmx_image_reverse_search`、`mmx_generate_music`、`mmx_generate_sound_effect` 及对应 `/mmx` 指令）走 MiniMax Agent Matrix 网关，而不是开放平台。凭据优先使用 `/mmx login` 得到的 OAuth 令牌，没有令牌时回退为配置里的第一个 API Key（区域取配置项 `region`）。网关返回 401/403 时，如果有令牌会强制刷新后重试一次，仍失败则提示重新登录；返回 code 402 表示账号额度不足。

## MCode 工作台内部约定

- 视图在沙箱 iframe 中运行，路由沿用 AstrBot 控制台的登录鉴权。
- 扫码登录使用 OAuth 设备授权，与 `/mmx login` 共用插件数据目录下的同一份 `auth.json`。面板里的「我已完成授权，立即同步」会立刻查询一次授权结果，仅在后端确认完成后才绑定。
- 无限画布的文档落盘在 `data/plugin_data/astrbot_plugin_mmx_cli_tool/canvas/`，变更应用具备幂等性；批注提交给视觉理解模型。
- 终端事件流通过 SSE 实时推送，只包含由工作台发起的后端调用（额度、登录、画布、搜图与音乐/音效等）；聊天侧的 LLM 工具与指令调用不在其中。
- 下载仅允许 AstrBot 临时目录内的文件（画布资产额外允许插件数据目录），拒绝绝对路径与 `..` 穿越。以图搜图的上传文件限定为图片扩展名，临时文件用后即删。
- 搜图与音乐/音效返回的 `local_path` 是 AstrBot 临时目录内的文件名，可直接作为画布节点的路径引用，并被下载、画布批注与以图搜图复用。
- 「团队方案（Team Plan）」看板始终是前端演示数据，本插件没有对应的后端接口。离线预览（页面未连上后端）时才会出现的演示内容包括：终端示例日志与「+ 模拟实时事件」、画布「+ 新建规格便签」按钮、媒体实验室的示例搜图结果、溯源来源和音轨。
- 顶部状态栏的推理强度档位写入配置项 `thinking_effort`，只是偏好记录，不改变本插件发给 MiniMax 的请求参数。
