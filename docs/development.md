# 开发者文档

这份文档面向要修改或扩展本插件的开发者：说明分层结构、凭据与区域模型、数据落盘位置、请求与错误处理、新增指令与工具的步骤，以及测试和提交约定。普通使用请看 README。

- [README](../README.md)：安装、配置项、使用说明，也有项目结构树、协作 SDK 概览与开发验证摘要。
- [docs/commands.md](commands.md)：`/mmx` 指令的完整参考。
- [docs/plugin-api.md](plugin-api.md)：其他插件调用本插件的协作 SDK v1。
- [CHANGELOG](../CHANGELOG.md)：版本变更记录。

## 目录

1. [总览与分层](#1-总览与分层)
2. [凭据与区域模型](#2-凭据与区域模型)
3. [数据与文件布局](#3-数据与文件布局)
4. [请求与错误处理](#4-请求与错误处理)
5. [直接指令](#5-直接指令)
6. [LLM 工具](#6-llm-工具)
7. [MiniMax Code 对话通道](#7-minimax-code-对话通道)
8. [登录自动获取开放平台 API Key](#8-登录自动获取开放平台-api-key)
9. [Agent Matrix 网关](#9-agent-matrix-网关)
10. [MCode 工作台后端](#10-mcode-工作台后端)
11. [安全设计](#11-安全设计)
12. [测试](#12-测试)
13. [提交、PR 与发布约定](#13-提交pr-与发布约定)
14. [已知限制](#14-已知限制)

## 1. 总览与分层

本节说明请求从聊天入口到 MiniMax 的分层、几条旁路，以及 `mmx/` 和 `tools/` 下每个文件的职责。

```
 聊天用户 (/mmx ...)       LLM Agent (tool call)       工作台前端 (沙箱 iframe)
        |                         |                              |
        v                         v                              v
 [入口层] main.py::Main                                mmx/web_api.py::MCodeWebAPI
          /mmx 指令处理器 + mmx/direct_command_args     18 条路由，直接调用 Main 持有的对象
          17 个 FunctionTool (tools/)                             |
        +-------------------------+------------------------------+
                                  v
 [封装层] mmx/apis/*.py  mmx/files.py     ImageAPI VideoAPI SpeechAPI QuotaAPI ...
                                  |
                                  v
 [传输层] mmx/client.py::MiniMaxClient    Key 选择 / 鉴权头 / 超时 / 错误分类
                                  |
                                  v
 [MiniMax 开放平台] api.minimaxi.com (cn) / api.minimax.io (global)

 旁路（不经过封装层）:
   Matrix 网关   tools 或 main.py -> mmx/matrix.py (+ media_transfer.py) -> MiniMaxClient.request(绝对 URL, OAuth 令牌) -> agent.minimax.cn / .io
   账号接口      main.py 或 web_api -> mmx/account.py -> MiniMaxClient.request(签名头, OAuth 令牌) -> agent.minimax.* / 账号后台
   LLM 供应商    AstrBot 供应商体系 -> mmx/llm_provider.py (自带 httpx 客户端，不经 MiniMaxClient) -> agent.minimax.cn / .io 的 /mavis/api/v1/llm
   工作台后端    web_api 另外直接调用 Matrix、账号接口、QuotaAPI、VisionAPI
   协作 SDK      其他插件 -> mmx/plugin_service.py::MiniMaxService -> 同一批封装对象（见 docs/plugin-api.md）
```

`main.py::Main` 的生命周期：

- `__init__`：读配置；建数据目录和 `MiniMaxClient`（无 Key 用空串，1 个 Key 为固定 Key，多于 1 个用 `KeyPool`）；创建全部封装对象和 17 个工具并 `context.add_llm_tools(*self._tools)`。
- `initialize`：`_sync_login_key()`，`MiniMaxService._initialize()`，注册工作台路由，注册 `minimax_code` 适配器类型，`set_token_source(...)` 绑定登录态，`_sync_provider_source()`，按 `auto_signin` 启动签到循环。
- `terminate`：取消签到循环，注销运行时供应商，清理令牌来源与适配器类型，关闭工作台路由，`_begin_close()`；随后在 `asyncio.shield` 保护的清理任务里 `_drain()` 在途 SDK 调用，关闭 OAuth、Matrix 媒体和主客户端，`_finish_close()`。重复调用等待同一个任务。

`mmx/` 模块表（`mmx/__init__.py` 只做导出）：

| 模块 | 职责 |
|------|------|
| `mmx/client.py` | `MiniMaxClient`：共享 httpx 客户端、Key 选择、鉴权头、超时、错误分类入口 |
| `mmx/errors.py` | `ErrorCategory`、`MiniMaxError`、`classify_error`、`friendly_message` |
| `mmx/keypool.py` | `KeyPool`：多 Key 池，后台按 TTL 刷新额度并按目标模型剩余额度选 Key；`unique_keys` 负责去重 |
| `mmx/endpoints.py` | 全部端点 URL 构造、区域表（`REGIONS`、`ACCOUNT_ORIGINS`、`MATRIX_BASES`）、OAuth 常量 |
| `mmx/oauth.py` | `OAuthClient` 设备授权 + PKCE、`OAuthToken`、`TokenStore`（`auth.json`）、`ApiKeyStore`（`open_api_key.json`） |
| `mmx/account.py` | `MiniMaxCodeAccount`：签名请求，身份、积分、套餐余量、签到，以及取登录账号的 API Key |
| `mmx/matrix.py` | `MatrixAPI`：Matrix 网关工具调用（搜图、以图搜图、音乐、音效、上传地址） |
| `mmx/media_transfer.py` | `MatrixMediaClient`：内联或 OSS 直传上传、流式下载、`MediaTransferError` |
| `mmx/llm_provider.py` | MiniMax Code 对话供应商 `MiniMaxCodeProvider`、适配器类型注册、云端模型目录 |
| `mmx/web_api.py` | `MCodeWebAPI`：工作台 18 条路由、`EventHub` 事件流、异常到 HTTP 状态的映射 |
| `mmx/canvas_store.py` | `CanvasStore`：画布文档按会话落盘，操作幂等 |
| `mmx/plugin_service.py` | `MiniMaxService`：协作 SDK v1 门面与生命周期状态机 |
| `mmx/direct_command_args.py` | `/mmx` 指令参数解析器与参数 dataclass |
| `mmx/files.py` | `FileAPI`：开放平台文件上传、列表、删除、检索 |
| `mmx/transcription.py` | `TranscriptionService`：转写的输入边界、附件下载、结果保存策略 |
| `mmx/video_business.py` | 视频模式校验与 `VideoInputError`（带 `hint`、`example` 的结构化载荷） |
| `mmx/attachment_input.py` | 从消息链解析音频附件（含 OneBot 回退） |
| `mmx/vision_input.py` | 从消息链和引用消息提取图片并落到本地 |
| `mmx/quota_usage.py` | 额度响应的解析、合并与展示 |
| `mmx/sse.py` | `parse_sse`：SSE 解析器（转写流式使用） |
| `mmx/utils.py` | 目录白名单路径解析、图片转 Data URI、命令分词、AstrBot 临时目录 |
| `mmx/voice_gender.py` | 按音色 ID 与名称推断性别 |
| `mmx/apis/image.py` | `ImageAPI`：图片生成与保存 |
| `mmx/apis/video.py` | `VideoAPI`：V1 与 V2（`MiniMax-H3`）视频生成、查询、轮询、下载 |
| `mmx/apis/speech.py` | `SpeechAPI`：语音合成、保存、音色列表 |
| `mmx/apis/transcription.py` | `SpeechToTextAPI`、`TranscriptionOptions`：语音转文字（含 SSE 流收集） |
| `mmx/apis/search.py` | `SearchAPI`：联网搜索 |
| `mmx/apis/vision.py` | `VisionAPI`：看图，本地图片转 Data URI |
| `mmx/apis/quota.py` | `QuotaAPI`：额度查询，多端点回退，`sk-api-` 余额型 Key |

`tools/` 模块表（`tools/__init__.py` 导出全部工具类）：

| 模块 | 职责（工具类与工具名） |
|------|------|
| `tools/schema.py` | 工具参数 schema 的构造与校验辅助 |
| `tools/result.py` | `tool_result`：工具返回值的类型适配 |
| `tools/image_tools.py` | `GenerateImageTool`（`mmx_generate_image`） |
| `tools/video_tools.py` | `GenerateVideoTool`（`mmx_generate_video`） |
| `tools/video_task_tools.py` | `QueryVideoTaskTool`（`mmx_video_task_get`）、`DownloadVideoTool`（`mmx_video_download`） |
| `tools/speech_tools.py` | `SpeechSynthesizeTool`（`mmx_speech_synthesize`）、`ListVoicesTool`（`mmx_speech_voices`） |
| `tools/transcription_tools.py` | `SpeechTranscribeTool`（`mmx_speech_transcribe`），以及它与指令共用的 `transcribe_event` |
| `tools/search_tools.py` | `WebSearchTool`（`mmx_web_search`） |
| `tools/vision_tools.py` | `DescribeImageTool`（`mmx_describe_image`） |
| `tools/file_tools.py` | `UploadFileTool`、`ListFilesTool`、`DeleteFileTool`（`mmx_file_upload`、`mmx_file_list`、`mmx_file_delete`，仅管理员） |
| `tools/quota_tools.py` | `CheckQuotaTool`（`mmx_check_quota`） |
| `tools/image_search_tools.py` | `SearchImagesTool`（`mmx_search_images`），以及它与指令共用的 `search_and_download` |
| `tools/image_reverse_tools.py` | `ImageReverseSearchTool`（`mmx_image_reverse_search`）、`format_reverse_results` |
| `tools/music_tools.py` | `GenerateMusicTool`（`mmx_generate_music`）、`GenerateSoundEffectTool`（`mmx_generate_sound_effect`） |

其他：`web/` 是工作台前端源码（Vue 3 + Vite，`pnpm build` 输出到 `views/mcode_dashboard/`）；构建产物已提交，改了 `web/src` 要重新构建。`.astrbot-plugin/i18n/zh-CN.json` 放视图标题、部分命令说明与错误文案。

## 2. 凭据与区域模型

本节说明插件用到的三类凭据、各自的来源与区域，以及同时存在时的取舍规则。

| 项 | 配置的 API Key（单个或池） | OAuth 令牌 | 登录自动获取的 API Key |
|----|------|------|------|
| 来源 | 插件配置项 `api_key`（列表） | `/mmx login` 或工作台扫码（设备授权 + PKCE，客户端 `mcode-public`） | 未配置 `api_key` 时，登录后由 `mmx/account.py::MiniMaxCodeAccount.fetch_api_key` 从账号后台取得（第 8 节） |
| 存放位置 | AstrBot 插件配置；运行时在 `Main._static_keys`，多于 1 个时由 `KeyPool` 管理 | 数据目录 `auth.json`（0600） | 数据目录 `open_api_key.json`（0600）；生效时写入 `MiniMaxClient` 的固定 Key |
| 用于哪些功能 | 开放平台功能：图片、视频、语音合成、转写、联网搜索、看图、文件、额度（都经 `MiniMaxClient`）；没有 OAuth 令牌时，Matrix 网关兜底用第一个 Key | Matrix 网关、账号接口（身份、积分、套餐余量、签到）、MiniMax Code 对话；开放平台接口不接受它（实测返回 base_resp 1004） | 与配置的 API Key 相同的开放平台功能；不用于 Matrix 网关 |
| 区域取值 | 配置项 `region`（`cn` 或 `global`）；`base_url` 非空时覆盖地址 | 登录时选择的区域，存于令牌的 `region`；换区域需重新登录 | 登录区域对应的 `REGIONS` 地址，不读 `region` 配置；`base_url` 非空时仍以它为准 |
| 刷新与失效 | 不刷新；失效表现为 401/403，归为 `ErrorCategory.AUTH` | 距到期不足 60 秒时取用前刷新；401/403 时强制刷新并重试一次；刷新被服务端明确拒绝才清除登录态，临时故障保留令牌 | 不自动刷新；与 login epoch 绑定，重新登录后重取；登出时删除；中途失效需重新 `/mmx login` |

取舍规则：

- 开放平台功能：配置了 `api_key`（单个或池）就完全不取登录 Key；没配置时才用登录 Key（`Main._sync_login_key`）。
- Matrix 网关和账号接口都经 `Main._matrix_credentials` 取凭据：优先 OAuth 令牌，其次第一个配置的 API Key，都没有就得到空令牌并抛出登录指引。登录获取的 Key 不参与。
- MiniMax Code 对话只用 OAuth 令牌，每次请求现读登录态。Matrix、账号、对话的区域都跟随令牌里的 `region`。

OAuth 登录（`mmx/oauth.py::OAuthClient`）：

- 设备授权 `start_device_authorization`：PKCE S256，scope `agent.default`，audience `agent-backend`；`poll_device_token` 轮询，默认间隔 5 秒，`slow_down` 时加 5 秒，兼容只返回 `user_code`、时间单位为毫秒的账号服务变体。
- `/mmx login` 先让发起人在 60 秒内选区域（`session_waiter`），授权最长等待 600 秒；端点见 `mmx/endpoints.py` 的 `oauth_*_endpoint`。
- 刷新 `refresh`（距到期 60 秒内触发，`OAuthToken.is_expired`）与撤销 `revoke`。只有 `invalid_grant`、`invalid_token`、`access_denied`、`unauthorized_client`（`_REFRESH_FATAL_CODES`）才清除本地令牌。

登录 epoch（`OAuthToken.login_epoch`）是标识"一次登录"的 32 位十六进制随机串：

- 新的设备授权在 `OAuthClient._parse_token_grant` 里生成新 epoch；令牌刷新继承旧 epoch；没有 epoch 的旧登录态由 `ensure_login_epoch` 补一次并持久化。
- 登录 Key 绑定它：`ApiKeyStore.load(login_epoch)` 对不上就视为没有，重新获取。
- 对话会话 ID 由它派生（`mmx/llm_provider.py::stable_session_id`），换账号后不会复用旧会话。
- 对话请求发出前比较"请求开始时的 epoch"与"当前 epoch"，不一致就抛 `MiniMaxError(AUTH, "MiniMax 账号已切换，请重试")`；401 重试也只在同一 epoch 内进行。运行时回退供应商实例在 epoch 或区域变化时被替换（第 7 节）。

## 3. 数据与文件布局

本节列出插件读写的所有位置。`<数据目录>` 是 `Path(get_astrbot_data_path()) / "plugin_data" / 插件名`，默认安装下为 `data/plugin_data/astrbot_plugin_mmx_cli_tool/`；AstrBot 临时目录来自 `mmx/utils.py::get_shared_temp_dir`（AstrBot 不可用时回退到系统临时目录下的 `astrbot_plugin_mmx_cli_tool`）。

| 位置 | 内容 | 写入方 | 权限 | 生命周期 |
|------|------|--------|------|----------|
| `<数据目录>/auth.json` | OAuth 令牌：`access_token`、`refresh_token`、`expires_at`、`region`、`scopes`、`login_epoch` | `TokenStore` | 0600 | 登录写入，刷新覆盖；`/mmx logout`、工作台登出、刷新被服务端明确拒绝时删除 |
| `<数据目录>/open_api_key.json` | `{"login_epoch": ..., "api_key": ...}` | `ApiKeyStore` | 0600 | 取得 Key 时写入；登出时删除；epoch 不符按不存在处理 |
| `<数据目录>/canvas/<session_id>.json` | 工作台画布文档 | `CanvasStore` | 0600 | 工作台操作时写入，不自动清理 |
| `<数据目录>` 下其他文件 | 管理员或用户放入的输入文件；`mmx_video_download`、转写 `out`、SDK `download_video(out=)` 的输出 | 用户或调用方 | 默认 | 不自动清理 |
| AstrBot 临时目录 | 生成的图片、视频、语音、音乐、音效、转写文件，搜图缓存，工作台媒体 | 工具、指令、工作台 | 默认 | 插件不清理，交给 AstrBot；工作台以图搜图上传的图片用后即删 |
| AstrBot 配置（非插件文件） | `provider_sources` 里的 `mmx_minimax_code` 源、`provider` 里的 `mmx_minimax_code/<模型>` 条目；`thinking_effort` | `_ensure_provider_source`、`config/effort` 路由 | 由 AstrBot 管理 | 见第 7 节 |

- 三个 JSON 文件都先写同目录的临时文件（创建时即 0600），再 `os.replace` 原子替换，随后 `chmod 0600`，失败忽略（`mmx/oauth.py::_write_private_json`、`CanvasStore._write`）。
- 自动保存的文件名由时间戳或随机 ID 组成；视频的 `task_id` 只有匹配 `[A-Za-z0-9_-]+` 才用于文件名，否则换成随机 ID。

## 4. 请求与错误处理

本节说明开放平台请求的完整路径、Key 的选择、错误分类，以及三条旁路（Matrix 网关、账号接口、对话供应商）的刷新与重试。

### 请求流程

一次开放平台请求在客户端内的路径如下。

```
封装层方法（如 ImageAPI.generate）
  -> MiniMaxClient.request_json(method, path, body=...)
       -> request(): 组头 -> 选 Key -> 发送
            非 2xx 响应 -> classify_error(status, response, path) -> 抛 MiniMaxError
       -> decode_json_response(): 不是 JSON 对象 / base_resp.status_code != 0 / 顶层 error
            -> classify_error(status, api_body=...) -> 抛 MiniMaxError
  <- 原始 dict（封装层不重试，也不改写响应）
```

- Key 取值顺序（`MiniMaxClient.request`）：`api_key_override`（Matrix、账号接口、逐 Key 查额度用）> `key_getter(model)`（Key 池）> 固定 `api_key`。图片、视频、语音与转写请求会把模型名传给池（`search`、`vision` 请求不带模型名），供按模型额度选择。
- 固定 Key 为 `None` 才抛 `RuntimeError`；`Main` 未配置 Key 时传的是空串，请求会带着空 Bearer 发出并得到 401。
- 超时默认取配置项 `timeout`（默认 300 秒），调用方可用 `timeout=` 覆盖；Matrix、账号接口都显式指定。
- 业务错误可能出现在 HTTP 200 的 `base_resp.status_code` 或顶层 `error` 里，只有 `request_json` 与 `decode_json_response` 会检查；直接用 `request()` 的调用（视频下载、字幕转写）和账号接口（自己解析信封）不走这项检查。

### Key 选择

`mmx/keypool.py::KeyPool.get_key(model)` 的设计规则：

- 给了 `model`：先选该模型剩余额度已知且大于 0 的 Key（取最多者）；没有就在额度未知的 Key 间轮转；全部用尽抛 `RuntimeError`。
- 没给 `model`：有已知额度的 Key 取总剩余最多者，否则在启用的 Key 间轮转。
- 被判定额度耗尽而停用的 Key，`DISABLED_COOLDOWN`（600 秒）后重新启用。

接线（第 4 节之外的实际代码路径）：

- `Main.initialize` 在多 Key 时启动 `_key_quota_loop`，`terminate` 取消；循环每 `QUOTA_CACHE_TTL`（300 秒）调用 `KeyPool.refresh_if_stale(self._quota.info, force=True)` 刷新全部 Key 的额度，刷新失败按 Key 返回说明并由循环记 debug 日志。
- 请求带 `model` 时用 `resolve_quota_model` 把请求模型映射到额度接口的模型名：先找同名，再按 image/video/speech/text 关键字归类，对不上按"额度未知"处理（不做猜测）。
- 非 2xx 响应由 `MiniMaxClient.request` 把 `(key_index, model, error)` 交给 `Main._on_pool_error`：`QUOTA` 把该模型剩余记为 0，`AUTH` 停用该 Key。回写本身失败时把异常挂到原始 `MiniMaxError` 的 note 上，不覆盖它。
- HTTP 200 内的业务额度错误（`base_resp.status_code`）不回写，最多滞后一个刷新周期。

额度类入口（`/mmx quota`、`mmx_check_quota`、SDK `get_quota`、工作台额度）对每个 Key 单独调用 `QuotaAPI.info(key)`：

- `mmx_check_quota` 拿到的是配置里的 Key 列表；列表为空时传 `None`，`QuotaAPI.info` 回落到客户端当前的 Key（登录获取的 Key）。
- 依次尝试 `quota_endpoints`（`/v1/token_plan/remains`、旧 openplatform 路径、旧 www 子域），遇到网络错误或 404/405 才换下一个；`sk-api-` 开头的 Key 改走 `/account/query_balance`，结果带 `kind: account_balance`。

### 错误分类

`mmx/errors.py::classify_error` 把 HTTP 状态和两种业务错误格式（`base_resp`、顶层 `error`）映射为 `MiniMaxError`（含 `category`、`http_status`、`api_code`、`retryable`）。判断顺序：HTTP 401/403，429，402，408/504，`/speech_to_text` 的 413、422，然后是业务码（审核、额度、输出审核、其他），最后才是 5xx 与其余状态。

| 类别 | 产生条件 | `retryable` | `friendly_message` | 工作台 HTTP 状态 |
|------|----------|-------------|--------------------|------------------|
| `AUTH` | HTTP 401/403；Matrix、账号接口、对话供应商在无令牌、刷新后仍失败、账号已切换时也直接构造 | 否 | "API Key 无效，请检查插件配置。" | 401 |
| `QUOTA` | HTTP 429；HTTP 402 或 `error.type == insufficient_balance_error`；业务码 1028、1030、2061；Matrix 返回 `code` 402 | 仅 429 | "额度不足或请求过多，请稍后重试。" | 402 |
| `TIMEOUT` | HTTP 408、504 | 是 | "请求超时，请稍后重试。" | 504 |
| `CONTENT_FILTER` | 业务码 1002、1039、1026（输入审核）；1027 或消息匹配 `output.*sensitive`（输出审核）；`/speech_to_text` 上的 HTTP 422；422 且 `error.type == unprocessable_entity_error`、消息含 `sensitive content` 或 1026 | 否 | 保留原文 | 400 |
| `USAGE` | `/speech_to_text` 上的 HTTP 413（音频超 50 MB） | 否 | 保留原文 | 502（未单独映射） |
| `GENERAL` | 其他业务码；没有业务码的 HTTP 5xx；其他非 2xx；响应不是 JSON 对象；Matrix 的 `code` 非 0 且不是 402；账号接口信封错误 | 仅无业务码的 5xx | 保留原文 | 502 |
| `NETWORK` | 目前没有代码会产生该类别（网络与超时异常是原始 `httpx` 异常，不会被包装） | - | "网络连接失败，请检查网络。" | 502 |

- `friendly_message` 只缩短 `AUTH`、`QUOTA`、`TIMEOUT`、`NETWORK`；审核与参数类错误保留具体原因。Matrix 与账号类指令用 `Main._matrix_error_text`，`AUTH` 时保留含 `/mmx login` 指引的全文。
- `retryable` 只是提示：只有 `mmx_speech_transcribe` 把它返回给模型，没有任何代码据此自动重试。
- 工作台的完整映射在 `mmx/web_api.py::_error_status`：`ServiceError` 503；`OAuthError` 的 `access_denied`、`expired_token` 为 401，其余 502；`CanvasStoreError` 400；`MediaTransferError` 的 `file_too_large` 为 413，其余 400；`ValueError`、`TypeError`、`KeyError` 400；未预期异常 500。

### 结果形态

同一个错误在四个通道里的呈现不同。

| 通道 | 形态 |
|------|------|
| 直接指令 | 文本回复：`MiniMaxError` 经 `friendly_message`；其他异常是 `<动作>失败: <异常文本>`；参数错误是 `DirectCommandError` 的用法文本 |
| LLM 工具 | `tool_result(json.dumps(...))` 的 JSON 字符串，见下 |
| 工作台 | `_guarded` 捕获异常，经 `_error_status` 转成 HTTP 状态和文案，同时写一条终端事件 |
| SDK | 直接抛异常（`ValueError`、`ServiceError`、`MiniMaxError`），见 `docs/plugin-api.md` |

LLM 工具的 JSON 形态：

- 成功：`{"ok": true, ...}`。
- 参数缺失或互斥：`{"ok": false, "error", "hint", "example"}`；视频、语音合成缺 `text`、看图缺图片这几类还带 `docs`（MiniMax 官方文档链接）；视频模式冲突直接返回 `VideoInputError.payload`。
- 调用失败：`{"ok": false, "error": "<动作>失败: <异常文本>"}`，没有 `hint`；转写工具的失败结果另带 `category` 与 `retryable`。非管理员调用文件工具：`error` 为"权限不足"，带 `hint`。
- 工具并不保证永不抛异常：图片、视频工具里解析本地路径的步骤在 `try` 之外，越界路径会以 `ValueError` 抛给 AstrBot。

### 旁路的刷新与重试

| 通道 | 凭据来源 | 触发刷新 | 重试 | 仍失败时 |
|------|----------|----------|------|----------|
| Matrix 网关（`MatrixAPI._call`） | `_matrix_credentials`，令牌临期先刷新 | `AUTH` 错误 | 强制刷新后重试一次 | 抛 `AUTH`："登录凭据已失效。" 加登录指引；无令牌也无兜底 Key 直接抛登录指引；没有其他自动重试 |
| 账号接口（`MiniMaxCodeAccount._signed_request`、`remains`） | 同上 | `AUTH` 错误 | 同上，区域变化时重算域名；单次请求超时 15 秒 | 同上；信封错误（`base_resp.status_code` 或 `statusInfo.code` 非 0）抛 `GENERAL` |
| 对话供应商（`MiniMaxCodeProvider.text_chat`、`text_chat_stream`） | 每次请求读登录态 | anthropic `AuthenticationError`（401） | 同一 epoch 内刷新并重试一次；流式只在首个事件之前重试 | 抛原异常；请求期间 epoch 变化则抛"账号已切换" |

刷新由 `Main._refresh_matrix_token` 完成并受 `_refresh_lock` 保护：只有 `_REFRESH_FATAL_CODES` 里的错误码才清除本地令牌，其他失败只记日志。

## 5. 直接指令

本节说明 `/mmx` 指令从处理器到回复的流程、命名规则，以及新增指令的步骤。

流程：`main.py::Main` 里 `@mmx_group.command("name")` 装饰的异步生成器先调 `mmx/direct_command_args.py` 的 `parse_*_command`（返回 frozen dataclass；参数错误抛 `DirectCommandError`，处理器回复用法文本），再解析图片等输入、调用封装层，最后 `yield event.plain_result` / `image_result` / `chain_result([Record、Video、File])`。生成的媒体先落到 `self._cache_dir`（AstrBot 临时目录）再发送。

参数取自消息原文尾部：AstrBot 只把首个词交给普通 `str` 参数，处理器统一用 `main.py::_command_tail(event, fallback)` 取 `/mmx <子指令>` 之后的完整文本（`/mmx image 一只猫 --aspect-ratio 16:9` 才能拿到整段），新增指令必须照此取参。

| 指令 | 处理器 | 解析器 | 权限 |
|------|--------|--------|------|
| `/mmx image` | `mmx_image` | `parse_image_command` | 所有人 |
| `/mmx video` | `mmx_video` | `parse_video_command` | 所有人 |
| `/mmx speech`（含 `transcribe`、`recognize`） | `mmx_speech`、`_handle_speech_transcribe` | `parse_speech_command`、`parse_transcription_command` | 所有人 |
| `/mmx search`、`/mmx vision` | `mmx_search`、`mmx_vision` | 无（`vision` 的图片取自消息） | 所有人 |
| `/mmx quota` | `mmx_quota` | 内联解析（页码 `page <n>` 或 Key 序号），每页 3 个 Key | 所有人 |
| `/mmx search-image`、`/mmx music` | `mmx_search_image`、`mmx_music` | `parse_image_search_command`、`parse_music_command` | 所有人 |
| `/mmx reverse-image`、`/mmx sfx` | `mmx_reverse_image`、`mmx_sfx` | 无 | 所有人 |
| `/mmx file upload`、`list`、`delete` | `mmx_file` | `parse_file_command` | 仅管理员 |
| `/mmx login`、`logout`、`signin`、`usage` | `mmx_login`、`mmx_logout`、`mmx_signin`、`mmx_usage` | 无 | 仅管理员 |

权限：指令用 `@filter.permission_type(filter.PermissionType.ADMIN)` 叠在 `@mmx_group.command(...)` 之上。LLM 工具没有装饰器，文件工具在 `call` 开头用 `tools/file_tools.py::_admin_only_result` 检查 `event.is_admin()`。

命名规则：

- 直接指令用 kebab-case 参数，如 `--aspect-ratio`、`--sample-rate`。图片、语音、视频解析器的多词参数同时接受 camelCase 别名（如 `--aspectRatio`）；例外是 `--no-wait`，它的别名是 `--async`。
- `file delete` 的 `--file-id` 也接受 `--fileId`；转写（`--response-format`、`--timestamp-level`）、音乐、搜图没有 camelCase 别名。
- LLM 工具参数用工具声明里的名称，一律 camelCase，如 `aspectRatio`、`sampleRate`、`responseFormat`、`timestampLevel`，以各工具的声明为准；SDK 的参数是 snake_case。

参数写法由 `_parse_cli_args` 统一处理：

- 支持 `--flag=value`；布尔参数可写 `--flag=false`；值参数收集到下一个 `--` 之前的所有词。
- `--subject-ref`、`--first-frame`、`--last-frame`、`--subject-image` 不写值得到空串，表示"取消息或引用消息里的图片"。
- `unsupported` 里的参数（如图片的 `--out`）返回明确的拒绝说明。
- 分词只把双引号当引用符（`mmx/utils.py::split_command_tokens`），英文撇号（如 `cat's`）可以直接出现在提示词里。

新增指令的步骤（对照现有实现）：

1. 在 `mmx/direct_command_args.py` 加 dataclass 和 `parse_xxx_command`：用 `_parse_cli_args`，参数用 kebab-case 并按同类指令补 camelCase 别名，服务器路径类参数放进 `unsupported`，数值范围错误抛 `DirectCommandError`。
2. 封装层方法放 `mmx/apis/`（已有就复用），入参用基础类型，返回原始响应。
3. 在 `Main` 加处理器：必要时加管理员装饰器；先解析参数，没有参数时回复用法；`except MiniMaxError` 回复 `friendly_message(e)`（Matrix 类用 `_matrix_error_text`），其他异常记日志并回复失败文本。
4. 参数原文的取法在现有处理器里有几种（取函数参数、取 `event.message_str` 的尾部、先规整再解析），新增时对照同类处理器，并在测试里用带空格、引号的 `Event("/mmx ...")` 覆盖。
5. 涉及本地路径时用 `resolve_image`、`resolve_local_input_path`、`resolve_existing_data_path`，传 `self._plugin_data_dir` 与 `self._extra_dirs`；只有从消息链提取出来的附件才能按 `trusted_local` 处理。
6. 需要登录的指令先检查 `self._token_store.load()`，或依赖 Matrix 的登录指引；与工具共用的逻辑抽成函数（如 `search_and_download`、`transcribe_event`）。
7. 同步文档：README、`docs/commands.md`、`.astrbot-plugin/i18n/zh-CN.json`（如果该命令在其中）、CHANGELOG；要公开给其他插件时再改 `mmx/plugin_service.py` 与 `docs/plugin-api.md`。

## 6. LLM 工具

本节说明工具类的结构、schema 与返回值辅助函数、注册方式，以及新增工具的步骤。工具名与类的对应见第 1 节的 `tools/` 模块表。

结构（以 `tools/image_tools.py::GenerateImageTool` 为例）：

- `@dataclass class XxxTool(FunctionTool)`；`__init__` 调 `super().__init__(name=..., description=..., parameters=object_parameters({...}, required=[...]))`，再保存封装对象、`data_dir`、`cache_dir`、`extra_allowed_dirs`。
- 工具名以 `mmx_` 开头，描述和参数说明目前是英文；参数键是 camelCase；`async def call(self, context, **kwargs)` 返回 `tool_result(json.dumps(..., ensure_ascii=False))`。

辅助函数：

- `tools/schema.py`：`string_param`、`integer_param`、`number_param`、`boolean_param`、`array_param` 构造属性；`object_parameters(properties, required=...)` 组装并调用 `validate_tool_schema`。
- 校验拒绝 `additionalProperties`、`allOf`、`anyOf`、`default`、`dependentRequired`、`dependentSchemas`、`else`、`if`、`not`、`oneOf`、`then`，并要求 `required` 里的键都在 `properties` 中。这些关键字被常见的 LLM 网关拒绝，默认值写进描述文字。
- `tools/result.py::tool_result`：把字符串转成 AstrBot 的 `ToolExecResult` 类型。

注册：

- `Main.__init__` 构造 `self._tools`（17 个）并 `context.add_llm_tools(*self._tools)`。
- 封装对象由 `Main` 创建后注入；工具可读目录是 `data_dir` 加 `extra_allowed_dirs`（`allow_astrbot_temp_dir` 开启时为 AstrBot 临时目录），生成物写入 `cache_dir`。
- 以图搜图工具实例 `Main._reverse_image_tool` 与 `/mmx reverse-image` 共用。

新增工具的步骤：

1. 先有封装层方法（`mmx/apis/` 或 `MatrixAPI`）；工具参数只用 `schema.py` 的辅助函数，不写 `default`、`oneOf` 等被拒绝的关键字。
2. `call` 里先校验必填参数，缺失时返回 `{"ok": false, "error", "hint", "example"}`。
3. 封装层调用放进 `try`：失败 `logger.error` 并返回 `{"ok": false, "error": "<动作>失败: <异常>"}`。解析本地路径的代码最好也放进 `try`，避免像图片、视频工具那样把 `ValueError` 抛给 AstrBot。
4. 产出媒体就保存到 `cache_dir`，返回本地路径，并在 `hint` 里提醒模型用本地路径发送。
5. 仅管理员可用的工具在 `call` 开头调用 `_admin_only_result(context)`。
6. 在 `tools/__init__.py` 导出，在 `Main.__init__` 的 `self._tools` 里实例化；同步 `tests/test_plugin_wiring.py` 里的注册数量断言，以及 README 的工具表和数量；要对外开放时再扩展 SDK。

## 7. MiniMax Code 对话通道

本节说明 `minimax_code` 供应商的注册、提供商源的四种状态、区域与会话映射、请求头白名单，以及运行时回退和卸载注意事项。

注册与协议：

- `mmx/llm_provider.py::register_provider_adapter_type` 在 `Main.initialize` 中把 `minimax_code` 注册进 AstrBot 适配器表（幂等）；`terminate` 时移除。WebUI「新增供应商」的类型列表里始终可以选择它，界面显示的名称就是 `minimax_code`（见 `docs/images/add-provider-minimax-code.png`）。
- `MiniMaxCodeProvider` 继承 AstrBot 的 `ProviderAnthropic`，用 Claude Messages 协议直连 `agent.minimax.cn` / `agent.minimax.io` 的 `/mavis/api/v1/llm`。
- 提供商源里的 API Base URL 与 API Key 只是占位提醒（`API_BASE_PLACEHOLDER`、`API_KEY_NOTICE`），实际令牌由插件注入；令牌到期或遇到 401 时自动刷新并重试（第 4 节）。
- 「获取模型列表」调用 `get_models`：从公开目录 `/mavis/api/v1/models` 拉取（无需登录，超时 10 秒），结果在模块级缓存 300 秒并按区域区分，失败回退 `DEFAULT_MODELS`（`MiniMax-M3.1-Flash`、`MiniMax-M3`）。插件写入的默认模型条目，`max_context_tokens` 为 `DEFAULT_CONTEXT_TOKENS`（512000）。
- 区域：请求区域只跟随 `/mmx login` 的登录区域，提供商源配置里不再需要 `region`（旧配置里的该字段被忽略，未登录时兜底国内站）。实现是出站请求的 httpx 事件钩子 `_rewrite_outgoing_headers`：按当前登录区域改写目标主机，路径和查询不变，所以切换区域无需重载。

提供商源与自动创建开关 `auto_create_provider_source`，状态由 `Main._ensure_provider_source` 判定，存于 `Main._provider_source_state`：

| 状态 | 条件 | 插件行为 | 运行时回退 |
|------|------|----------|------------|
| `managed` | 开关开启，且没有同 ID 源，或同 ID 源带本插件的 `managed_by` 标记 | 写入源 `mmx_minimax_code` 与默认模型条目 `mmx_minimax_code/<模型>`（只补缺，不改已有条目）并落盘；AstrBot 已完成原生供应商初始化时再即时 `load_provider` | 否 |
| `user_managed` | 开关开启，同 ID 源存在、不带标记、类型为 `minimax_code` | 视为用户自管，不接管、不告警 | 否 |
| `conflict` | 开关开启，同 ID 源存在、不带标记、类型不是 `minimax_code` | 不写入，记录告警；工作台状态里的 `provider_source_warning` 触发弹窗 | 是（需已登录） |
| `disabled` | 开关关闭 | 不触碰配置，需在 WebUI 手动新增 | 否 |

- 状态为空串表示尚未判定：供应商 API 不可用（`llm_provider` 导入失败，如离线测试桩）或读取 AstrBot 配置失败时同步直接返回，`/mmx login` 回复"提供商源同步未完成，详见日志"。
- 自动与手动两种方式同时使用时，手动创建的源 ID 可能带后缀；同 ID 且类型为 `minimax_code` 的手动源视为用户自管，不被接管，也不算冲突。
- 同步时机：`Main.initialize`、`/mmx login` 成功后、工作台扫码登录成功后。

会话与请求头：

- `stable_session_id(login_epoch, conversation_id)` 把 AstrBot 的 `conversation_id` 稳定转写为 `mvs_` 加 32 位十六进制的 Mavis 会话 ID（取 SHA-256 前 16 字节，置 UUIDv4 的版本位与变体位），令牌刷新后保持不变；换账号后 epoch 变化，不会复用旧会话。
- `text_chat`、`text_chat_stream` 入口从调用参数取会话键（`conversation_id`、`umo`、`session_id`，或第二个位置参数）；取不到就生成请求级随机键，同一次 401 重试内保持稳定。出站钩子读这个会话键，没有时再看请求扩展和 `x-astrbot-conversation-id` 头。
- 请求头按白名单重建（`_rewrite_outgoing_headers`）：先删除除传输头（`accept-encoding`、`connection`、`content-length`、`host`）以外的全部头，再写入 `User-Agent: MiniMaxAgent`、`X-Mavis-Session-Id`、`X-Mavis-Agent-Id`、`X-Mavis-Timezone-Offset`、`anthropic-version`、`Content-Type`、`Accept`（请求体 `stream` 为真时是 `text/event-stream`）和 `Authorization: Bearer <OAuth 令牌>`。
- 因此 AstrBot 头、SDK 遥测头和提供商源里的 `custom_headers` 都不会透传。

运行时回退、卸载与签到：

- 回退只在 `conflict` 状态发生：`_sync_provider_source` 发现告警后调用 `_register_llm_provider`，把 `MiniMaxCodeProvider` 实例直接放进 `provider_manager.provider_insts` 与 `inst_map["mmx_minimax_code"]`。未登录时不注册；epoch 或区域变化时先 `_unregister_llm_provider`（关闭旧实例）再重建；`/mmx logout` 与 `terminate` 都会注销。
- 其余状态下实例由 AstrBot 按提供商源配置创建，每次请求都读最新的令牌、epoch、区域（`set_token_source` 绑定的模块级来源，`terminate` 时 `clear_token_source`）。
- 卸载插件前必须先删除所有 `minimax_code` 类型的提供商源（不仅是 `mmx_minimax_code`）及其模型条目，否则 AstrBot 重启时会找不到 `minimax_code` 适配器。
- `auto_signin` 开启后，`Main._auto_signin_loop` 启动时先巡检一次，此后每 30 分钟一次：当天已领取（内存里记录）或未登录则跳过，否则调用签到接口领取当日积分，失败只记告警。上游按天幂等，重复领取不会叠加，本地记录只为省调用。

## 8. 登录自动获取开放平台 API Key

本节说明未配置 `api_key` 时，插件如何借登录账号拿到一个开放平台 API Key、何时执行、如何存储，以及失败时的行为。

背景：`/mmx login` 得到的 OAuth 令牌（客户端 `mcode-public`）不被开放平台接口（`api.minimaxi.com` / `api.minimax.io`）接受，实测返回 base_resp 1004「Please carry the API secret key」。图片、视频、语音、转写、搜索、看图、文件、额度这些功能需要真正的 API Key。

```
触发方(initialize / login / logout)   Main._sync_login_key      MiniMaxCodeAccount      账号后台
        |-- 调用 -------------------------->|                          |                    |
        |   配置了 api_key -> 直接返回 True，不取                                           |
        |   未登录 -> 清空客户端 Key，标记未就绪，返回 False                                 |
        |   ApiKeyStore(login_epoch) 命中且非 refetch -> 直接使用                           |
        |                                   |-- fetch_api_key() ------>|                    |
        |                                   |                          |-- GET token_plan_credit (签名, Bearer=OAuth 令牌)
        |                                   |                          |<-- 顶层字符串字段 api_key
        |   ApiKeyStore.save(login_epoch, key)  <---- key -------------|                    |
        |   client.set_credentials(key, 登录区域地址)；service._set_configured(True)
```

来源与时机：

- 请求是 `GET {open_origin}/backend/account/token_plan_credit`，即 `MiniMaxCodeAccount.credit_pool()`：签名请求，Bearer 为 OAuth 令牌，不带公共 query。`open_origin` 取 `_OPEN_ORIGINS`：`cn` 为 `https://www.minimaxi.com`，`global` 为 `https://platform.minimax.io`。
- 响应顶层的字符串字段 `api_key` 是账号绑定的 Token Plan Key（`sk-cp-` 开头；发现该接口时实测可用于开放平台接口，离线无法验证）。这是未公开的后台接口，字段可能随上游变动。
- `Main.initialize` 先调用 `_sync_login_key()` 再进入 `MiniMaxService._initialize`，就绪状态因此反映真实可用性。
- `/mmx login` 与工作台扫码登录完成后用 `refetch=True` 重新获取；`/mmx logout` 与工作台登出先删除 `open_api_key.json`，再调用它把客户端恢复为未配置状态。

存储、区域与优先级：

- `open_api_key.json`（0600）由 `ApiKeyStore` 读写，内容 `{"login_epoch": ..., "api_key": ...}`。文件缺失、损坏或 epoch 与 `auth.json` 里的不一致都按不存在处理并重新获取；每次新登录都会生成新 epoch，所以每次登录取一次，之后重启直接读文件。
- 区域与地址：`base_url` 配置非空时用它，否则取登录区域对应的 `REGIONS`，不看 `region` 配置，因为 Key 与区域都来自本次登录。未登录或获取失败时回到构造时记录的默认地址（`_default_base_url`）。
- 优先级：配置的 `api_key` 与登录获取的 Key 用 `unique_keys` 去重后合并——多于 1 个时建 `KeyPool`（`set_keys` 保留已有 Key 的序号与统计），只剩 1 个时回到固定 Key。配置了 Key 时启动不主动请求账户接口，只有登录动作（`refetch=True`）才获取并并入池；Matrix 网关仍优先使用 OAuth 令牌。
- 就绪状态：`MiniMaxService._set_configured` 在 `ready` 与 `unavailable`（原因 `missing_api_key`）之间切换，不触碰 `initializing`、`closing`、`closed`。
- `Main._has_open_key()` 判断是否有可用的开放平台 Key（Key 池或客户端固定 Key），工作台画布批注分析用它做前置检查。

失败与日志：

- 只捕获 `MiniMaxError` 与 `httpx.HTTPError`，记一条告警（只含异常描述）后继续，启动不会因账户接口不可达而中断；此时清掉内存里的 Key，已落盘的文件不删。
- `/mmx login` 的回复里追加"未能自动获取 API Key"并提示手动填写 `api_key`（`_login_key_notice`）；工作台登录路径把任何异常都只记日志。
- Key 不写入日志、异常信息和状态接口；告警只带 HTTP 状态码、上游错误消息或网络异常描述，不输出响应体。唯一的展示是 `/mmx quota` 沿用的前 4 位加后 4 位掩码。`tests/test_login_api_key.py::LoginKeyTestCase.asyncTearDown` 断言 Key 不出现在任何日志里。

## 9. Agent Matrix 网关

本节说明搜图、以图搜图、AI 音乐、AI 音效如何走 Matrix 网关，包括凭据、端点、重试、错误码和媒体传输的阈值。

- 范围：`mmx_search_images`、`mmx_image_reverse_search`、`mmx_generate_music`、`mmx_generate_sound_effect`、对应的 `/mmx` 指令和工作台媒体路由走 Matrix 网关，而不是开放平台。
- 端点：`POST {MATRIX_BASES[区域]}/mavis/api/v1/mcp/<tool>`（`mmx/endpoints.py::matrix_mcp_endpoint`），工具名为 `images_search_and_download`、`image_reverse_search`、`batch_text_to_music`、`batch_text_to_audio`、`get_upload_url`；请求头 `Authorization: Bearer <令牌>`、`User-Agent: MiniMaxAgent`。
- 凭据：优先 `/mmx login` 得到的 OAuth 令牌；没有令牌时回退为配置里的第一个 API Key（区域取配置项 `region`），服务器可能不认，此时同样提示登录；两者都没有就提示登录。
- 超时（`mmx/matrix.py`）：搜图、以图搜图、音乐、音效 600 秒，默认 120 秒，`get_upload_url` 固定 1500 秒（调用时显式指定）。
- 重试：网关返回 401/403 时，有令牌就强制刷新后重试一次，仍失败则提示重新登录。
- 错误码：响应体 `code` 为 402 表示账号额度不足（`QUOTA`，提示不要重复提交），其他非 0 的 `code` 为 `GENERAL`。音乐、音效取 `success_items` 里第一个带 `output_url` 的条目，否则用 `failed_items` 的 `error_msg` 报错。
- 音乐格式只接受 `mp3`、`wav`：指令解析器拒绝其他值，工具与 `MatrixAPI.batch_text_to_music` 回落为 `mp3`；音效固定 `mp3`。

媒体传输（`mmx/media_transfer.py::MatrixMediaClient`）：

| 项 | 数值与行为 |
|----|------------|
| 内联上限 `INLINE_LIMIT_BYTES` | 600 KiB：不大于它且未指定 `force_remote_url` 时 Base64 内联；否则 `get_upload_url` 预签名直传（1 MiB 分块 PUT），返回 `cdn_url` |
| 上传上限 `MAX_UPLOAD_BYTES` | 500 MiB |
| 下载上限 `MAX_DOWNLOAD_BYTES` | 200 MiB（先看 `Content-Length`，再按实际写入量），256 KiB 分块；目标必须位于 `allowed_dir`，超限或失败删除半成品 |
| HTTP 客户端 | 超时 300 秒，跟随重定向 |

- 以图搜图：本地文件只接受 `IMAGE_EXTENSIONS`（png、jpg、jpeg、gif、webp、bmp），避免 `auth.json` 等敏感文件被注入上传，并强制走 OSS 取 `cdn_url`；从聊天提取的图片按 `trusted_local` 处理，跳过目录校验但仍检查扩展名。指令只展示前 3 条结果，工具最多返回 10 条。
- `MediaTransferError.kind` 的取值：`file_not_found`、`file_too_large`、`upload_failed`、`download_failed`、`path_not_allowed`、`invalid_type`。

## 10. MCode 工作台后端

本节说明工作台视图的后端：路由、事件流、画布存储、文件访问限制与演示数据。`mmx/web_api.py::MCodeWebAPI.register` 通过 `context.register_web_api` 注册路由，宿主不支持时降级为只有聊天指令。

路由前缀是 `/astrbot_plugin_mmx_cli_tool/<endpoint>`，共 18 条：

| 分组 | endpoint | 说明 |
|------|----------|------|
| 状态与额度 | `status`、`quota`、`account/usage` | 插件状态与能力、Token Plan 额度（多 Key 合并）、账户积分与套餐余量（均为 GET） |
| 登录 | `oauth/start`、`oauth/poll`、`oauth/logout` | 发起设备授权、查询授权进度、登出（`poll` 为 GET，其余 POST） |
| 配置 | `config/effort` | 设置推理强度档位（POST） |
| 画布 | `canvas/doc`、`canvas/mutate`、`canvas/iterate`、`canvas/download` | 读文档、应用变更、提交批注给视觉模型、下载资产（`mutate`、`iterate` 为 POST） |
| 媒体 | `tools/image-search`、`tools/image-reverse`、`tools/image-reverse-upload`、`tools/music`、`tools/sfx`、`tools/download-temp` | 搜图、以图搜图、上传图片以图搜图、音乐、音效（均为 POST）；`download-temp` 下载临时媒体（GET） |
| 事件流 | `events` | SSE 终端事件流（GET） |

通用约定：

- 视图在沙箱 iframe 中运行，路由沿用 AstrBot 控制台的登录鉴权。每个处理器经 `_guarded` 包装：关闭后返回 503，异常经 `_error_status` 映射，并写一条终端事件。
- 扫码登录使用 OAuth 设备授权，与 `/mmx login` 共用数据目录下的同一份 `auth.json`；成功后同样同步提供商源并取登录 Key。面板里的「我已完成授权，立即同步」会立刻查询一次授权结果，仅在后端确认完成后才绑定。
- 终端事件流：`EventHub` 保留最近 100 条（`EVENT_HISTORY`）；每个订阅者一个 200 项的队列（`EVENT_QUEUE_SIZE`，满了丢弃该订阅者的新事件）；每 20 秒发一次 keepalive 注释（`SSE_KEEPALIVE_SECONDS`）。新连接只收到连接之后的事件。
- 事件只包含由工作台发起的后端调用（额度、登录、画布、搜图与音乐/音效等）；聊天侧的 LLM 工具与指令调用不在其中。

画布（`mmx/canvas_store.py::CanvasStore`）：

- 文档落盘到 `<数据目录>/canvas/`；变更用 `operationId` 加内容指纹保证幂等，同一 `operationId` 换了内容会被拒绝。变更类型：`add_file`、`update_layout`、`remove_node`、`add_annotation`、`update_annotation`、`remove_annotation`。
- 上限：64 个节点、每节点 32 条批注、每次 64 条变更、保留 64 条操作回执。`relativePath` 必须是相对路径，拒绝 `..`、以 `~` 开头的片段和超过 512 字符的值。
- 只有图片节点支持批注；批注提交给视觉理解模型，一次最多 5 个节点。

文件访问与返回值：

- `canvas/download` 只允许 AstrBot 临时目录内的文件（画布资产额外允许数据目录），`tools/download-temp` 只允许临时目录；都拒绝绝对路径与 `..` 穿越。
- 以图搜图的上传文件限定为图片扩展名且不超过 20 MiB，保存为临时文件，用后即删。
- 搜图与音乐/音效返回的 `local_path` 是 AstrBot 临时目录内的文件名，可直接作为画布节点的路径引用，并被下载、画布批注与以图搜图复用。搜图最多返回 5 张，以图搜图最多返回 10 条匹配。

演示数据与偏好：

- 「团队方案（Team Plan）」看板始终是前端演示数据，本插件没有对应的后端接口。
- 离线预览（页面未连上后端）时才会出现的演示内容：终端示例日志与「+ 模拟实时事件」、画布「+ 新建规格便签」按钮、媒体实验室的示例搜图结果、溯源来源和音轨。
- 顶部状态栏的推理强度档位由 `config/effort` 路由写入配置项 `thinking_effort`（取值限于 `EFFORT_LEVELS`）。它只是偏好记录，不参与本插件发给 MiniMax 的任何请求。

## 11. 安全设计

本节汇总全部安全约束，按主题分组。

密钥与日志：

- 插件自身不把 API Key、OAuth 令牌写进日志或异常文本；需要展示 Key 时脱敏，长度超过 8 位时只显示前 4 位和后 4 位（`/mmx quota`、`KeyPool._mask_key`）。
- `auth.json`、`open_api_key.json` 与画布文档以 0600 创建，原子替换。登录获取的 API Key 不进入状态接口，获取失败的告警不含响应体（第 8 节）。
- 对话请求头按白名单重建，AstrBot 头、SDK 遥测头和 `custom_headers` 不会外发（第 7 节）。
- OAuth 使用 PKCE S256；登录 epoch 隔离账号，换账号后旧 Key、旧会话都不会被复用。

读写目录：

- 文件名安全：自动保存使用时间戳或随机 ID 命名，避免路径注入。
- 可读目录：LLM 工具和直接指令里手写的图片、音频路径，以及 `mmx_file_upload` 的上传文件，仅允许位于插件数据目录（`data/plugin_data/astrbot_plugin_mmx_cli_tool/`）或 AstrBot 临时目录内，避免模型诱导读取任意宿主文件。路径先 `resolve()` 再判断包含关系，指向目录之外的绝对路径、`..` 穿越和指向目录外的符号链接都会被拒绝。当前消息和引用消息中的附件仍由 AstrBot 解析后提交。
- 临时目录信任面：AstrBot 临时目录由所有插件共享（聊天附件、其他插件的临时文件都在其中）。放行该目录意味着 LLM 工具可读取其中的任意文件，并非「仅聊天图片」。配置项 `allow_astrbot_temp_dir` 关闭后只允许插件数据目录，代价是生成结果无法再作为输入。
- 可写目录：生成的媒体和转写文件默认保存到 AstrBot 临时目录。`mmx_video_download` 以及转写指令的 `out` 仅允许写入插件数据目录，越界路径（`..` 穿越、指向数据目录之外的绝对路径）一律拒绝；转写的 `out` 与 SDK 的 `download_video(out=)` 还会直接拒绝任何绝对路径。
- 工作台下载与以图搜图上传的限制见第 10 节；Matrix 下载只能写入调用方指定的 `allowed_dir`，并有 200 MiB 上限（第 9 节）。

权限：

- `/mmx file upload`、`list`、`delete` 与 `mmx_file_upload`、`mmx_file_list`、`mmx_file_delete` 仅管理员可用；`/mmx login`、`logout`、`signin`、`usage` 也仅管理员可用。
- 工作台路由沿用 AstrBot 控制台的登录鉴权。
- SDK 的文件方法是受信任的插件协作接口，不代替消费者自己的用户权限检查（见 `docs/plugin-api.md`）。

网络输入：

- 转写附件下载：仅下载平台解析出的附件 URL，不携带 MiniMax Key（使用独立的 httpx 客户端），限制 50 MB、60 秒并拒绝重定向；带用户名密码或缺少主机名的 URL 被拒绝；附件来源沿用平台的信任边界。转写的 `file` 不接受手写远端 URL。
- 参数验证：工具、指令、SDK 入口处校验关键参数（数值范围、互斥组合、必填项）；工具 schema 由 `validate_tool_schema` 约束，SDK 用 `inspect.signature(...).bind` 拒绝未知参数。
- 响应体解析有上限：SSE 单个事件不超过 1 MiB，转写流累计文本不超过 2 MiB。

## 12. 测试

本节说明如何运行测试、CI 的矩阵、离线桩的作用、为新指令或工具写测试的方法，以及 lint 的现状。

运行：`python -B -m unittest discover -s tests`（加 `-v` 看明细）。只依赖 `httpx`，不需要 AstrBot 和 API Key。`tests/support.py` 把仓库目录的上一级加入 `sys.path`，测试以 `astrbot_plugin_mmx_cli_tool.main` 的包名导入，所以仓库目录名必须是 `astrbot_plugin_mmx_cli_tool`。

CI（`.github/workflows/tests.yml`）：

| 项 | 内容 |
|----|------|
| 触发 | `push`、`pull_request`、`workflow_dispatch` |
| 环境 | `ubuntu-latest`，超时 10 分钟，权限 `contents: read` |
| 矩阵 | Python `3.12`、`3.13` 乘 httpx `0.27.2`、`0.28.1`，`fail-fast: false` |
| 步骤 | 只安装 `httpx==<矩阵值>`，运行 `python -B -m unittest discover -s tests -v`；不运行 ruff、不构建前端、不加载 AstrBot |

离线桩：

- `tests/support.py::plugin_modules(root)`：上下文管理器，往 `sys.modules` 里放最小的 `astrbot.*` 替身（`FunctionTool`、`filter` 装饰器、`AstrBotConfig`、`astrbot.api.web` 的请求与响应工厂；数据目录指向 `root`，临时目录指向 `root/temp`），然后导入 `main` 与 `tools`。同文件还有 `Event`、`File`、`Record`、`Reply`、`WebApiContext`、`UploadFile` 等替身。
- `tests/test_llm_provider.py::provider_stubs`：补齐 `astrbot.core.provider.*` 与 `anthropic` 的桩并重新导入 `mmx.llm_provider`；`make_fake_context()` 返回带供应商管理器桩的 `(context, config, manager)`。涉及登录、提供商源、对话供应商的测试需要它们（`tests/test_login_api_key.py` 同时用了两者）。
- HTTP 用 `httpx.MockTransport`，常见写法是 `client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))`。`ImageAPI.save`、`VideoAPI.download`、`SpeechAPI.save`、`VisionAPI` 取 URL 图片会自己创建 `httpx.AsyncClient`，测试里必须打补丁。
- `tests/native_discovery.py`：可选的真实宿主契约测试，用真实的 `Context.get_registered_star` 与 `StarMetadata` 检查原生发现和生命周期。文件名不匹配 `test*.py`，`discover` 不会收集它，也不在仅依赖 httpx 的 CI 里；在装有 AstrBot 的环境用 `python -m unittest discover -s tests -p native_discovery.py -v` 运行。

给新指令或工具写测试：

1. 指令：`with plugin_modules(root) as (main, tools)` 创建 `main.Main(context, {"api_key": ["k"]})`，把对应封装对象的方法换成 `AsyncMock`，再 `[r async for r in plugin.mmx_xxx(Event("/mmx xxx ..."))]` 断言产出（范例 `tests/test_plugin_wiring.py`）。解析器是纯函数，直接测 `parse_*_command`。
2. 工具：在 `registered` 里按 `name` 找到实例，`json.loads(await tool.call(None, **kwargs))`；同时断言 `tool.parameters` 不含 `default`、`oneOf`；新增或删除工具要同步 `tests/test_plugin_wiring.py` 里的注册数量。管理员工具读取 `context.context.event.is_admin()`，需要自己构造这样的 `context`。
3. 测试一律不得访问网络：所有出站请求走 `MockTransport` 或 `AsyncMock`；涉及密钥的测试按 `LoginKeyTestCase` 的方式断言日志里没有密钥。

lint：ruff 不是 CI 门禁，仓库里也没有 ruff 配置。2026-11-04 用 ruff 0.15.20 的默认规则运行 `ruff check` 没有告警；`ruff format --check` 会改动 62 个文件中的 35 个，说明现有代码并非 ruff format 风格，不要为风格偏好全库格式化。

## 13. 提交、PR 与发布约定

本节整理从 git 历史和已合并 PR 中观察到的做法，不是另立的规则。

提交：

- 主题一行，格式 `type: summary`，英文小写动词开头，不写正文：最近 10 条非合并提交（2026-10-23 起）都没有正文。更早的历史里有带 bullet 正文的提交，2026-06 之前还有中文主题。
- 类型：`fix`（最多）、`feat`、`docs`、`chore`（版本号），偶见 `refactor`；scope 很少用（`feat(webui):` 一例）。`test:` 没有作为提交主题出现，测试和对应代码在同一个提交里。
- 一个提交通常同时带上代码、测试和文档。例如 `fix: MiniMax Code region follows login and add auto-create switch` 同时改了 `main.py`、`mmx/`、测试、README、`_conf_schema.json`、`CHANGELOG.md`、`metadata.yaml`。

PR：

- 目标分支 `master`，来源是 `fix/…`、`feat/…`、`chore/…` 主题分支或 `dev`；合并方式是 `Merge pull request #N from piexian/<branch>` 合并提交。标题沿用提交主题的 `type: summary` 风格。
- 正文以 bullet 列表开头，每行一个 `type: summary`（如 `- fix: ...`、`- test: ...`、`- docs: ...`）；最近两个 PR（#16、#17）接着有 `## Validation` 小节，列出离线单测通过数、对改动的 Python 文件的 Ruff 检查、MockTransport 验证所用的 AstrBot 版本。
- 多数 PR 末尾还有 Sourcery 机器人自动追加的摘要，不是手写的。

接口、配置或用法变化时，历史上同一次提交里会同步更新：

| 变化 | 更新位置 |
|------|----------|
| 指令或用法 | `README.md`、`docs/commands.md`、`.astrbot-plugin/i18n/zh-CN.json`（涉及的命令） |
| 配置项 | `_conf_schema.json`（含 `hint`）、README 的配置表 |
| 内部机制、安全规则 | 本文档 |
| 对外 SDK | `docs/plugin-api.md`、`mmx/plugin_service.py` |
| 发布 | `CHANGELOG.md`（`## <版本> - <日期>` 加要点）、`metadata.yaml` 的 `version` |

版本号：`chore: bump 0.7.1` 一次改了 `CHANGELOG.md`、`metadata.yaml`、`web/package.json`；也有功能提交顺带升版本（如 `feat: ... bump 0.6.0`）。

## 14. 已知限制

本节只列在代码中验证过的限制。

- 登录自动获取 API Key 依赖未公开的账号后台接口（`/backend/account/token_plan_credit`），字段可能随上游变动；取不到时只记告警。
- 0600 权限在 Windows 上没有实际效果（Python 在 Windows 上只识别只读位，`chmod` 的错误还被忽略），`auth.json`、`open_api_key.json` 和画布文档靠操作系统账户隔离保护。
- `/mmx quota` 展示脱敏后的 Key（前 4 位加后 4 位），登录获取的 Key 也会这样显示。
- 认证失败的文案仍写"请检查配置中的 api_key"（`classify_error`）和"API Key 无效，请检查插件配置。"（`friendly_message`），即使 Key 是登录自动获取的，这时应重新 `/mmx login`。
- 工作台额度面板的区域标签读取 `region` 配置（`_quota_payload`），不跟随登录区域；状态栏（`_status_payload`）则用登录区域。
- 未配置 Key 也未登录时，指令没有前置检查，请求带着空 Bearer 发出并得到 401；只有 SDK 用 `not_ready` 拒绝。
- 按模型额度选择依赖额度接口返回的计划级模型名与请求模型能对上（`resolve_quota_model`）；对不上时该 Key 按额度未知参与轮转，不会更差但也没有额度加成。
- 单 Key 模式不走池，额度感知只在多 Key（含登录 Key 合并后多于 1 个）时生效。
- `ErrorCategory.NETWORK` 没有产生方，网络与超时异常以原始 `httpx` 异常的文本回复。
- 工作台登出不会注销运行时回退的供应商实例（`/mmx logout` 会）；实例留着但没有令牌，请求会因 `AUTH` 失败。
- 工作台下载路由始终以 AstrBot 临时目录为根，不受 `allow_astrbot_temp_dir` 影响。
- `tests/native_discovery.py` 断言注册 13 个工具，而 `Main` 现在注册 17 个，在装有 AstrBot 的环境运行前需要同步这个数字。
