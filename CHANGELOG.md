# 更新日志


## 0.7.0 - 2026-10-21

- 新增 MiniMax 账号 OAuth 登录：`/mmx login` 两阶段交互（选择区域 → 设备授权），RFC 8628 Device Authorization Grant + PKCE S256，Token 自动刷新、加锁防并发覆盖，auth.json 原子写入并设置 0600 权限；`/mmx logout` 退出。
- 接入 MiniMax Agent Matrix MCP 网关（`agent.minimax.cn/io` 的 `/mavis/api/v1/mcp/*`），新增 4 个 LLM 工具及对应指令：
- `mmx_search_images` / `/mmx search-image`：搜图并下载到本地临时目录。
- `mmx_image_reverse_search` / `/mmx reverse-image`：以图搜图，本地图片先经 OSS 预签名直传换取链接；返回 Top 3 出处卡片。
- `mmx_generate_music` / `/mmx music`、`mmx_generate_sound_effect` / `/mmx sfx`：Agent 账号体系下的 AI 音乐与音效生成（区别于 0.6.0 移除的开放平台音乐接口），音频下载后以语音组件发送。
- Matrix 工具凭据双轨：优先 OAuth Token，未登录时回退静态 API Key；网关拒绝时提示管理员 `/mmx login`。
- 媒体传输底座：≤600KB 内联 Base64、>600KB OSS 预签名直传（上限 500MB），下载强制限制在 AstrBot 临时目录内。
- 新增 MCode 工作台视图后端接入：`mmx/web_api.py` 通过 `register_web_api` 暴露 17 条路由（状态、Token Plan 双周期额度与账户余额、OAuth 发起/轮询/解绑、推理强度档位、画布读写与批注提交、搜图/以图搜图/音乐/音效、临时文件下载、SSE 终端事件流），`terminate()` 注销本插件路由并停止后台授权轮询。
- 新增 `mmx/canvas_store.py`：CanvasDocumentV1 按会话原子落盘（`data/plugin_data/.../canvas/`），CanvasOperationV1 依 operationId 幂等应用 add_file/update_layout/remove_node/批注增删改，限制节点与批注数量、拒绝绝对路径与 `..` 穿越。
- 新增 `thinking_effort` 配置项与视图 i18n 标题；工作台文件下载限定 AstrBot 临时目录（画布资产另允许插件数据目录），以图搜图上传限定图片扩展名。
- 工作台临时产物改回相对文件名：`tools/image-search` 与音乐/音效返回的 `local_path` 为 AstrBot 临时目录内文件名，可直接作为画布 `relativePath` 并被 `tools/download-temp`、`canvas/download`、`canvas/iterate`、`tools/image-reverse` 复用。
- 画布 `previewUrl` 上限独立为 65536 字符（其余元数据仍为 1024），`remove_annotation` 后剩余批注按顺序重排序号。
- 前端 bridge 仅在未注册路由（`plans/*`）使用演示回退，后端业务错误直接提示并上抛；「我已完成授权，立即同步」改为立即查询一次 `oauth/poll`，仅 `authorized` 才完成绑定。
- 画布 `add_file` 要求 `relativePath` 指向白名单目录（AstrBot 临时目录、插件数据目录）内真实存在的文件，不再持久化悬空引用；试验台预置与离线回退数据不再伪造 `local_path`，仅后端真实缓存的图片/音频可置入画布，文件名与 MIME 按缓存文件扩展名推断。
- bridge 仅对已提示过的错误标记 `bridgeReported`，调用方统一经 `reportError` 出口处理，后端错误只提示一次、离线无回退的错误不再静默；试验台搜图、以图搜图、音乐/音效与置入画布失败不再产生未处理的 Promise 拒绝。
- 修复 `canvas/iterate` 将所有选中节点的批注合并成同一条提示词的问题：现按节点单独构造提示词，视觉模型只收到该图片自己的批注（归一化坐标仅对所属图片有效）；无批注节点不再提交并计入 `skipped`，`results` 每项新增 `file_name`，`prompt_preview` 为各节点提示词以空行拼接。
- 画布工作台展示 `canvas/iterate` 的视觉模型分析结果：侧栏按节点文件名列出分析文本（保留换行），失败节点以错误样式显示；提交按钮改为「提交批注分析」，提示改为实际完成/失败/未提交数量并移除「迭代队列」措辞；离线演示模式不再伪造分析文本，仅提示未调用视觉模型；提交失败统一经 `reportError` 提示，不再产生未处理的 Promise 拒绝。
- 收紧画布变更语义，对齐参考实现：`appliedOperations` 改存 operationId 与内容指纹（SHA-256），相同内容重放幂等返回、不同内容复用同一 operationId 返回 `operation_id_reused`；`add_file` 拒绝已存在的 nodeId 与已在画布中的文件，`remove_node` / `remove_annotation` 目标不存在时报错，`add_annotation` 拒绝重复 id、重复序号与非图片节点，`update_annotation` 拒绝序号冲突（`remove_annotation` 后剩余批注仍自动重排序号）；前端仅对图片扩展名节点开放框选批注。
- 工作台演示内容与在线内容分离：终端示例日志、「+ 模拟实时事件」、画布「+ 新建规格便签」以及媒体实验室的示例搜图结果、示例溯源来源与示例音轨仅在离线预览显示，在线时终端与媒体实验室只列出真实结果并给出空状态提示；Team Plan 卡片标注「演示数据」且审批提示带「（演示）」；音频节点按 mimeType 渲染为播放器而非破图；画布批注/移除/便签/层级调整/布局保存失败统一经 `reportError` 提示（失败的批注保留草稿框）；以图搜图输入框不再默认演示路径；README 更正终端事件流仅包含工作台发起的调用。
- 新增 40 项工作台路由与画布测试（Mock HTTP，覆盖契约字段、幂等、白名单、相对路径联动、画布文件存在性、演示数据不伪造路径与降级），总数 194 项；AstrBot 4.29.1 真实 `register_web_api`、Dashboard 路由匹配、`PluginPageService` 视图发现与 `terminate` 注销已端到端验证。
- 媒体实验室「检索关键词」与「数量」输入经 `usePersistedRef` 持久化到 localStorage，切换标签页或刷新页面不再被默认值覆盖。
- 新增 MiniMax Code 账户能力：`/mmx signin` 手动领取每日签到积分（上游按天幂等），`/mmx usage` 查看积分余额/积分明细/套餐余量/签到进度；账户接口签名（yy/x-signature 公共参数）与信封解析复刻自 new-api minimaxcode 适配的实测实现，401 自动刷新令牌重试一次；新增配置 `auto_signin`（默认关），开启后每 30 分钟巡检自动领取当日签到。
- `/mmx login` 后自动向 AstrBot 注册 LLM 供应商 `mmx_minimax_code`（Claude Messages 协议直连 `agent.minimax.cn/io` 的 `/mavis/api/v1/llm/v1/messages`，请求头与官方客户端一致），在 WebUI 会话/Agent 设置选择后对话走 MiniMax Code 官方通道、消耗订阅额度与签到积分；令牌轮换每请求生效，鉴权失败自动刷新重试；`/mmx logout` 与插件卸载时同步注销。
- 工作台界面修缮：左上角标头精简为「Matrix Studio」并改用插件图标；(OAuth 2.0 PKCE) 一类括号补充说明全部移除；多模态试验台删除「直接调度 /mavis/api/v1/mcp/*」宣传文案；侧栏改接真实账户数据——新增 `account/usage` 路由返回积分余额、积分明细、各模型套餐剩余次数与签到进度（OAuth 登录态），API Key 模式保留原额度视图；Team Plan 面板移除插件自身实现计划的演示内容，无方案时显示空态并禁用「批准分发/要求修订」；终端日志来源下拉修复白底白字。
- 账户用量显示修正：积分余额按「订阅积分 + 赠送积分」双段展示（对齐官方 0 + xxx 形式，明细按 credit_type 拆分），并补充 Coding Plan（OA）5 小时/每周用量窗口，与 API Key 模式共用窗口解析；文本域限制为仅纵向拖拽调整（最小高度 96px），歌词结构默认 6 行；顶栏与侧栏新增移动端（≤768px）自适应布局；`/mmx login` 注册的供应商同时把 `minimax_code` 类型注册进 AstrBot 适配器表——WebUI「模型供应商」页可手动添加该类型（令牌晚绑定到插件登录态），会话/Agent 下拉在登录后即可选。
- 供应商改走提供商源：插件把 `minimax_code` 适配器类型与提供商源 `mmx_minimax_code`（默认模型 `MiniMax-M3.1-Flash` / `MiniMax-M3`，上下文窗口 512K，可加自定义模型）写入 AstrBot 配置，「获取模型列表」实时拉取云端公开目录 `/mavis/api/v1/models`（无需登录，TTL 300s 对齐服务端），「模型供应商」页可见可管理；源 ID 被占用时跳过写入、工作台弹告警并回退运行时注册；API Base URL 与 API Key 字段为占位提醒，实际令牌由登录态注入。
- 审查修复：工作台 UI 登录成功后同步执行提供商源初始化（与 `/mmx login` 一致，修复国际站区域错配）；提供商源加载改为仅在 AstrBot 原生初始化完成后按缺失补齐/按区域重载，避免漏加载与重复实例；`terminate` 无条件清理模块级令牌与模型来源；账户 401 重试及 `identity` 后续请求在区域切换后重算域名；logout 增加默认模型切换提示并以前端真实状态刷新；README 补充卸载前删除提供商源说明；签到 `today_claimed` 按实测 `status=3` 判定已领取（原 `==2` 会误显示可签到）。
## 0.6.0 - 2026-10-15

- 对齐 mmx-cli 1.0.28：上游已公告关闭音乐生成并下线服务端接口，本版本彻底移除音乐生成与翻唱。
- 移除 `mmx_generate_music`、`mmx_music_cover`、`mmx_background_task_get` 工具及 `/mmx music` 指令；删除 `default_music_model`、`default_music_cover_model` 配置项。
- 移除仅为音乐后台任务服务的任务注册表与音频结果调度模块；SDK 同步移除 `submit_music`、`submit_music_cover`、`get_task`、`list_tasks`、`wait_task`，能力清单去掉 `music.generate`、`music.cover`、`media.tasks`。
- 生成媒体本地优先（修复 #12）：`mmx_generate_image` 生成后自动下载到 AstrBot 临时目录，结果新增 `file_paths`，工具描述提示模型发图必须使用本地路径；下载失败才回退 `image_urls`。
- `mmx_generate_video` 同步等待完成后同样先下载到本地并返回 `file_path`，失败才回退 `video_url`/`file_id`；`/mmx video` 指令下载失败时回退发送 V2 远程链接。

## 0.5.2 - 2026-09-23

- 修复音频保存阻塞事件循环：`SpeechAPI.save`、`MusicAPI.save` 的 URL 回退下载改为 `httpx.AsyncClient` 异步实现，全部调用点改为 `await`。

## 0.5.1 - 2026-09-21

- 修正 `mmx/vision_input.py` 日志记录：改用 `astrbot.api` 提供的 logger，不再使用内置 `logging` 模块。

## 0.5.0 - 2026-09-20

- 同步 mmx-cli 1.0.26：新增 `mmx_speech_transcribe`、`/mmx speech transcribe`（别名 `recognize`）及 `default_transcription_model` 配置。
- 转写支持 JSON、说话人与时间戳、SRT/WebVTT 和 SSE 汇总；限制音频大小与本地路径，流异常或中断不返回成功，保存失败保留结果。
- 转写附件拒绝 HTTP 重定向；无法安全解析旧版附件组件时明确提示，不回退到无大小限制的下载。
- 修复默认音色配置未初始化、未传入 LLM 工具的问题。
- 修正额度计数语义、套餐不可用状态、周额度加成和多 Key 汇总；按实际周期展示额度，倒计时支持秒。
- 补齐输入/输出审核及 ASR 大小限制错误分类，新增回归测试与 CI 测试配置。

## 0.4.0 - 2026-09-10

- 对齐 mmx-cli 1.0.25（与官方 1.0.16/1.0.19/1.0.25 包逐一比对打包源码确认）：
- 文件上传端点由 `/v1/files` 修正为 `/v1/files/upload`（CLI 自 1.0.19 起变更）。
- 额度查询主端点由 `/v1/api/openplatform/coding_plan/remains` 调整为 `/v1/token_plan/remains`（CLI 自 1.0.19 起变更），openplatform 与 www 子域路径降级为回退。
- 新增账户余额分支：`sk-api-` 开头的 Key 自动走 `/account/query_balance`，`/mmx quota` 与 `mmx_check_quota` 展示可用余额/现金/代金券/信用额度/欠费；此类 Key 不参与按模型额度汇总。
- 语音 `pronunciation` 改发 CLI 1.0.19+ 的 `{"tone": ["文本/(读音)"]}` 格式（字符串原样透传，旧 {text, tone} 字典输入自动转换）。
- 语音合成新增 `emotion`、`text_normalization`、`latex_read` 参数（LLM 工具与 `/mmx speech` 同步支持），请求体显式携带 `output_format: hex`。
- 新增 `default_speech_voice` 配置项（默认 `English_expressive_narrator`），LLM 工具与 `/mmx speech` 的 `voice` 缺省时读取。
- `mmx_speech_voices` 每个音色条目新增 `gender` 字段（male/female/unknown，按 voice_id 与名称推断），并新增 `gender` 过滤参数，便于 AI 区分男声/女声。
- `mmx_file_upload` 与 `FileAPI.upload` 的 `purpose` 默认值统一为 `retrieval`。
- 说明：mmx-cli 1.0.25 已整体移除音乐生成/翻唱命令，插件仍直连服务端 `/v1/music_generation`，功能保留但上游已弃用，后续服务端可能关闭。
- `MiniMaxClient` 新增公开 `api_key` 属性，`main.py` 不再访问私有成员。
## 0.3.0 - 2026-08-18

- 同步 mmx-cli 1.0.19：音乐生成默认模型升级为 `music-3.0`，有效模型新增 `music-3.0`、`music-2.6-free`；音乐翻唱新增 `music-cover-free` 模型。
- 音乐生成 `instrumental` 模式自动追加 `Style: instrumental, no vocals, pure music`（对齐 CLI buildPrompt）。
- 视频生成新增 MiniMax-H3（Video Generation V2）支持：设 `model=MiniMax-H3` 启用 2K 生成，支持 `duration`（4-15 秒）、`ratio`（adaptive/21:9/16:9/4:3/1:1/3:4/9:16）、`referenceImages`（≤9）、`referenceVideos`（≤3）、`referenceAudios`（≤3，需配合参考图/视频）。
- `mmx_video_task_get` 新增 `model` 参数，V2 任务设为 `MiniMax-H3` 走 V2 查询端点；`mmx_generate_video` 同步等待按模型路由 V1/V2 轮询。
- `/mmx video` 直接指令支持 `--duration`、`--ratio` 与 `--model MiniMax-H3`。

## 0.2.7 - 2026-08-15

- 修复 LLM 工具无法读取 AstrBot 临时目录图片的问题：`mmx_describe_image`、`mmx_generate_image` 的 `subjectRef`、`mmx_generate_video` 的首尾帧/角色图现在允许读取 AstrBot 临时目录（聊天图片下载位置）内的文件，其余本地路径仍被拒绝。
- 生成的媒体文件（图片、视频、音乐、翻唱、语音）改为保存到 AstrBot 临时目录作为缓存，由 AstrBot 统一管理，无需自行清理；仅用户显式指定的持久化输出（`mmx_video_download` 的 `out`）仍写入插件数据目录。
- `mmx_file_upload` 与 `/mmx file upload` 允许读取 AstrBot 临时目录内的文件，生成结果可直接再上传。
- 修复 review 发现的回归：`mmx_music_cover` 的 `audioFile` 与 `/mmx music cover --audio-file` 可读取临时目录内已生成的音频；直接指令中手写的图片/音频路径（`--subject-ref`、视频首尾帧/角色图、`--audio-file`）同样放行 AstrBot 临时目录。
- 新增 `allow_astrbot_temp_dir` 配置项（默认开启）：关闭后 LLM 工具与直接指令不再放行 AstrBot 临时目录读取，供需要收紧信任面的宿主使用。
- 修复 `mmx_music_cover` 选择 wav/pcm 格式时保存文件扩展名仍为 `.mp3` 的问题。
- 修复裸文件名（不含目录）只在插件数据目录查找、不回退到 AstrBot 临时目录的问题：路径解析现在在所有允许的根目录中优先选择真实存在的文件（`mmx/utils.py` 的 `resolve_existing_data_path`），`mmx_file_upload` 与 `/mmx file upload` 同步生效。

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
