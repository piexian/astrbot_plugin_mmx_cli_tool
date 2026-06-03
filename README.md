# MiniMax 多模态工具

为 AstrBot 提供 MiniMax 图片生成、视频生成、音乐生成、联网搜索、视觉理解、额度查询能力。


## 环境要求

| 依赖 | 版本要求 |
|------|----------|
| httpx | >= 0.27.0 |

## 功能

- 15 个 LLM Tool，全面覆盖 MiniMax 多模态 API（图片/视频/音乐/语音合成/联网搜索/视觉理解/文件管理）
- `/mmx` 命令组，用户可直接通过指令快速调用
- 智能参数校验与纠偏机制，对 AI 友好
- API 多 Key 轮询与额度查询
- 媒体文件自动保存到本地，并通过 AstrBot 多媒体消息格式发送

## 安装

**方式一**：在 AstrBot 插件市场搜索「MiniMax 多模态工具」，点击安装。

**方式二**：插件界面右下角加号 → 从链接安装，输入：
```
https://github.com/piexian/astrbot_plugin_mmx_cli_tool
```

## 工具列表

| 工具 | 功能 | 权限 |
|------|------|------|
| `mmx_generate_image` | 根据文字描述生成图片（支持 aspectRatio、角色参考等） | 无 |
| `mmx_generate_video` | 根据文字描述及首尾帧等生成视频 | 无 |
| `mmx_video_task_get` | 查询视频生成任务的最新状态和进度 | 无 |
| `mmx_video_download` | 通过文件 ID 下载完成的视频到本地 | 无 |
| `mmx_file_upload` | 上传插件数据目录内的文件到 MiniMax 存储 | 无 |
| `mmx_file_list` | 列出已上传到 MiniMax 存储的文件 | 无 |
| `mmx_file_delete` | 删除已上传的 MiniMax 文件 | 无 |
| `mmx_generate_music` | 生成音乐（支持纯器乐、带歌词，及所有精细控制参数） | 无 |
| `mmx_music_cover` | 基于参考音频及描述进行翻唱（支持 URL 和本地音频输入） | 无 |
| `mmx_background_task_get` | 查询音乐生成和翻唱的后台任务状态与结果 | 无 |
| `mmx_speech_synthesize` | 将文本合成语音（TTS），支持 30+ 种音色和语速/音量/音高控制 | 无 |
| `mmx_speech_voices` | 查询可用 TTS 系统音色列表 | 无 |
| `mmx_web_search` | 联网搜索信息 | 无 |
| `mmx_describe_image` | 分析/描述图片内容（视觉理解） | 无 |
| `mmx_check_quota` | 查询 API 剩余额度 | 无 |

## 使用

### 指令

```
/mmx image <描述> [--aspect-ratio 16:9] [--seed 42]  # 生成图片，可用 --subject-ref 引用图片
/mmx video <描述> [--first-frame <图片>] [--no-wait]  # 生成视频，可附带或引用图片
/mmx music <描述> (--lyrics <歌词> | --instrumental | --lyrics-optimizer)  # 生成音乐
/mmx music cover <风格描述> --audio <URL>  # 生成翻唱，可附带或引用音频
/mmx speech <文本> [--voice <音色>] [--format mp3]  # 语音合成
/mmx file upload --file <路径> [--purpose retrieval]  # 上传文件
/mmx file list                 # 列出文件
/mmx file delete --file-id <id> # 删除文件
/mmx search <查询>             # 联网搜索
/mmx vision <描述要求>         # 图片理解（支持当前消息带图或引用图片）
/mmx quota                    # 查询额度（多 Key 默认每页显示 3 个）
/mmx quota page <页码>        # 翻页查看 Key 额度
```

### 示例

```
/mmx image a cute cat wearing a hat, watercolor style --aspect-ratio 1:1 --aigc-watermark
/mmx video a sunset over the ocean with gentle waves --first-frame --no-wait
/mmx music warm acoustic guitar, folk style --instrumental
/mmx music 欢乐电子乐 --lyrics-optimizer
/mmx music --prompt "Upbeat pop" --lyrics "[Verse] La la la"
/mmx music cover indie folk, acoustic guitar, warm male vocal
/mmx speech 欢迎使用 MiniMax 语音合成功能 --speed 1.1 --subtitles --pronunciation MiniMax/minimax
/mmx file list
/mmx search 今天天气怎么样
/mmx vision 描述这张图片里有什么
/mmx quota
/mmx quota page 2
```

直接指令使用 kebab-case，例如 `--lyrics-optimizer`、`--use-case`、`--sample-rate`。LLM 工具参数仍使用导出的 JSON Tool 名称，例如 `lyricsOptimizer`、`useCase`、`sampleRate`。


常用图片指令参数：

| 参数 | 说明 |
|------|------|
| `--aspect-ratio <比例>` | 图片比例，如 `1:1`、`16:9` |
| `--n <数量>` | 生成张数，1-9 |
| `--seed <数字>` | 随机种子 |
| `--width` / `--height` | 自定义尺寸，需同时提供，512-2048 且为 8 的倍数 |
| `--prompt-optimizer` | 启用提示词优化 |
| `--aigc-watermark` | 添加 AI 生成内容水印 |
| `--subject-ref <参数>` | 角色参考，支持 `type=character,image=path-or-url`；省略值时使用当前消息或引用消息中的图片 |
| `--response-format <url\|base64>` | 返回格式，默认 `url` |

常用视频指令参数：

| 参数 | 说明 |
|------|------|
| `--model <模型>` | 指定视频模型；通常可省略并由插件自动选择 |
| `--first-frame <图片>` | 起始帧图片，支持 URL、本地路径、当前消息图片或引用图片 |
| `--last-frame <图片>` | 结束帧图片，需同时提供 `--first-frame`；省略值时可取第二张附件图 |
| `--subject-image <图片>` | 角色一致性参考图；省略值时使用当前消息或引用消息中的图片 |
| `--callback-url <URL>` | 完成回调地址 |
| `--no-wait` / `--async` | 提交任务后立即返回 `task_id` |
| `--poll-interval <秒>` | 等待生成时的轮询间隔 |

常用音乐指令参数：

| 参数 | 说明 |
|------|------|
| `--lyrics <歌词>` | 带歌词歌曲。歌词可带 `[Verse]`、`[Chorus]` 等结构标签 |
| `--lyrics-optimizer` | 根据描述自动生成歌词。不能和 `--lyrics`/`--instrumental` 同时使用 |
| `--instrumental` | 生成纯音乐。不能和 `--lyrics` 同时使用 |
| `--vocals <文本>` | 人声风格，如 `"warm male baritone"` |
| `--genre` / `--mood` / `--instruments` / `--tempo` / `--key` | 精细控制音乐风格 |
| `--bpm <数字>` | 指定 BPM |
| `--avoid` / `--use-case` / `--structure` / `--references` / `--extra` | 更多结构化描述 |
| `--model <模型>` | `music-2.6`、`music-2.5+` 或 `music-2.5` |
| `--output-format <hex\|url>` | 输出格式，默认 `hex` |
| `--format <mp3\|wav\|pcm>` | 音频格式，默认 `mp3` |
| `--sample-rate <数字>` / `--bitrate <数字>` | 音频采样率和码率 |
| `--aigc-watermark` | 添加 AI 生成内容水印 |

常用翻唱指令参数：

| 参数 | 说明 |
|------|------|
| `--audio <URL>` | 参考音频 URL；省略时可从引用消息中的音频附件解析 |
| `--audio-file <路径>` | 本地参考音频路径；省略值时可从引用消息中的音频/文件附件解析 |
| `--lyrics <歌词>` | 翻唱歌词，留空则由接口从参考音频提取 |
| `--model <模型>` | `music-cover` |
| `--format <mp3\|wav\|pcm>` | 音频格式，默认 `mp3` |
| `--sample-rate <数字>` / `--bitrate <数字>` / `--channel <1\|2>` | 音频采样率、码率、声道数 |

常用语音指令参数：

| 参数 | 说明 |
|------|------|
| `--model <模型>` | TTS 模型，如 `speech-2.8-hd` |
| `--voice <音色>` | 音色 ID |
| `--speed` / `--volume` / `--pitch` | 语速、音量、音高 |
| `--format <格式>` | `mp3`、`pcm`、`flac`、`wav`、`pcmu_raw`、`pcmu_wav`、`opus` |
| `--sample-rate <数字>` / `--bitrate <数字>` / `--channels <数字>` | 音频采样率、码率、声道数 |
| `--language <语言>` | 语种增强 |
| `--subtitles` | 请求字幕时间信息 |
| `--pronunciation <文本/读音>` | 自定义读音，可重复传入 |

### LLM 对话中使用

当 AI 需要生成图片、视频、语音、音乐，或进行搜索、图片理解时，会自动调用对应工具。

例如对 AI 说「帮我把这段话合成语音：你好」→ 自动调用 `mmx_speech_synthesize`。
音乐生成与翻唱会先返回 `task_id`，可用 `mmx_background_task_get` 查询结果。


## 智能错误提示与参数规范

### 智能错误提示
本插件实现了一套**面向 LLM 自动纠错**的智能错误反馈机制。当 LLM 传入参数缺失、类型错误或发生模式冲突（如音乐生成中歌词与纯乐器互斥）时，工具不直接崩溃，而是返回包含以下信息的结构化 JSON：
- `ok`: `false`，指示调用失败
- `error`: 明确的错误原因
- `hint`: AI 参数纠正建议
- `example`: 正确的传参 JSON 示例
- `docs`: MiniMax 官方 API 文档链接

---

### 工具参数一览

#### 1. `mmx_generate_image` (图片生成)
- `prompt` (string, 必填): 详细的图片内容描述。
- `aspectRatio` (string): 长宽比（如 `1:1`, `16:9`, `9:16`, `4:3`）。
- `model` (string): 模型选择（如 `image-01` 或 `image-01-live`）。
- `n` (integer): 生成图片张数（1-9）。
- `seed` (integer): 随机种子。
- `width` / `height` (integer): 自定义宽高像素（覆盖 `aspectRatio`）。
- `promptOptimizer` (boolean): 是否自动优化提示词（默认 `true`）。
- `aigcWatermark` (boolean): 是否添加 AI 生成内容水印。
- `responseFormat` (string): 返回格式，`url` 或 `base64`。
- `subjectRef` (string): 角色一致性参考图，支持 URL、本地路径或 `type=character,image=path-or-url`。

#### 2. `mmx_generate_video` (视频生成)
- `prompt` (string, 必填): 视频画面描述。
- `firstFrame` (string): 起始帧图片（URL 或本地路径）。
- `lastFrame` (string): 结束帧图片（需同时提供 `firstFrame`，常用于首尾帧模式）。
- `subjectImage` (string): 角色一致性参考图（自动激活角色保持模式）。
- `noWait` (boolean): `true` 时立即返回 `taskId`，不等待生成完成。
- `callbackUrl` (string): 异步生成的回调地址。
- *注：插件会根据传入参数自动切换模型（如 `Hailuo-02` 适用于首尾帧，`S2V-01` 适用于角色保持）。*

#### 3. `mmx_video_task_get` (查询视频生成状态)
- `taskId` (string, 必填): `mmx_generate_video` 返回的任务 ID。

#### 4. `mmx_video_download` (下载视频到本地)
- `fileId` (string, 必填): 视频生成完成后任务返回的文件 ID。
- `out` (string): 自定义保存路径（选填）。

#### 5. `mmx_generate_music` (音乐生成)
- `prompt` (string): 音乐风格描述。
- `lyrics` (string): 歌词（可带 `[Verse]`, `[Chorus]` 等结构标签）。与 `instrumental` 互斥。
- `lyricsOptimizer` (boolean): `true` 时根据风格自动生成歌词。与 `lyrics`/`instrumental` 互斥。
- `instrumental` (boolean): 是否生成纯器乐无声乐。与 `lyrics` 互斥。
- `bpm` (integer): 目标每分钟节拍数。
- `vocals` (string): 人声风格偏好。
- `genre` / `mood` / `instruments` / `tempo` / `key` / `avoid` / `useCase` / `structure` / `references` / `extra` (string): 精细控制参数。
- `aigcWatermark` (boolean): 是否添加 AI 生成内容水印。

#### 6. `mmx_music_cover` (音乐翻唱)
- `prompt` (string): 翻唱的目标音乐风格或歌声特征描述。
- `audio` (string): 参考音频的 URL 链接（支持 6秒~6分钟，最大 50MB）。与 `audioFile` 选填其一。
- `audioFile` (string): 本地参考音频文件路径。与 `audio` 选填其一。
- `lyrics` (string): 歌词（如留空则通过 ASR 自动从参考音频提取）。
- `seed` (integer): 随机种子。
- `model` (string): 模型名，`music-cover`。
- `lyricsFile` (string): 本地歌词文件路径。与 `lyrics` 互斥。
- `format` / `sampleRate` / `bitrate` / `channel`: 音频格式、采样率、码率、声道数。

#### 7. `mmx_background_task_get` (查询音乐后台任务)
- `taskId` (string, 必填): `mmx_generate_music` 或 `mmx_music_cover` 返回的任务 ID。

#### 8. `mmx_speech_synthesize` (语音合成/TTS)
- `text` (string, 必填): 需要合成的文本（最大 10000 字符）。
- `voice` (string): 音色 ID（默认 `English_expressive_narrator`）。
- `model` (string): 模型名（如 `speech-2.8-hd`, `speech-2.6`, `speech-02`）。
- `speed` / `volume` / `pitch` (number): 语速倍率（0.5-2.0）/ 音量 / 音高微调。
- `format` (string): 导出音频格式（如 `mp3`, `pcm`, `flac`, `wav`, `pcmu_raw`, `pcmu_wav`, `opus`）。
- `sampleRate` / `bitrate` / `channels` (number): 采样率、码率、声道数。
- `language` (string): 语种权重。
- `subtitles` (boolean): 是否请求字幕时间信息。
- `pronunciation` (array): 自定义读音数组，每项格式为 `文本/读音`。

#### 9. `mmx_speech_voices` (列出 TTS 音色)
- `language` (string): 过滤语言（如 `english`, `chinese` 等）。

#### 10. `mmx_web_search` (联网搜索)
- `q` (string, 必填): 搜索查询关键字。

#### 11. `mmx_describe_image` (视觉理解)
- `image` (string, 必填): 图片 URL 或本地绝对路径。
- `prompt` (string): 分析要求描述。
- `fileId` (string): 预先上传的文件 ID。

#### 12. `mmx_file_upload` (文件上传)
- `file` (string, 必填): 插件数据目录内的本地文件路径。LLM 工具会拒绝绝对路径和 `..` 穿越。
- `purpose` (string): 文件用途，默认 `retrieval`。

#### 13. `mmx_file_list` (文件列表)
- 无参数。列出当前 Key 已上传的文件。

#### 14. `mmx_file_delete` (文件删除)
- `fileId` (string, 必填): 要删除的文件 ID。

#### 15. `mmx_check_quota` (额度查询)
- 无参数。查询当前 API Key 的 MiniMax Token Plan 额度；普通模型显示已用百分比，视频额度显示已用/剩余（上限），五小时额度与周额度附带重置倒计时，无限额度显示为 `∞`。

## 安全设计

- **API Key 脱敏**：错误日志中自动隐藏 API Key（仅显示前 4 后 4 字符）
- **文件名安全**：保存文件使用时间戳命名，避免路径注入
- **输出目录限制**：媒体文件仅保存到 AstrBot 插件数据目录（`data/plugin_data/astrbot_plugin_mmx_cli_tool/`）
- **LLM 文件上传限制**：`mmx_file_upload` 仅允许上传插件数据目录内的文件，避免模型诱导读取任意宿主文件
- **参数验证**：工具入口处校验关键参数

## 配置

在 AstrBot 管理面板的插件配置中可设置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `api_key` | string | — | **必填**。从 https://platform.minimaxi.com/user-center/payment/token-plan 获取 |
| `region` | string | `global` | API 区域：global（国际）或 cn（中国） |
| `base_url` | string | — | 自定义 API 地址（留空使用默认） |
| `timeout` | int | `300` | 请求超时（秒） |
| `video_poll_interval` | int | `5` | 视频生成轮询间隔（秒） |
| `video_timeout` | int | `600` | 视频生成超时（秒） |
| `default_image_model` | string | `image-01` | 默认图片生成模型 |
| `default_video_model` | string | `MiniMax-Hailuo-2.3` | 默认视频生成模型 |
| `default_video_sef_model` | string | `MiniMax-Hailuo-02` | 默认首尾帧视频模型 |
| `default_video_subject_model` | string | `S2V-01` | 默认角色一致性视频模型 |
| `default_speech_model` | string | `speech-2.8-hd` | 默认语音合成模型 |
| `default_music_model` | string | `music-2.6` | 默认音乐生成模型 |
| `default_music_cover_model` | string | `music-cover` | 默认音乐翻唱模型 |

## 项目结构

```
astrbot_plugin_mmx_cli_tool/
├── main.py                          # 插件入口（Star 子类）
├── metadata.yaml                    # 插件元数据
├── _conf_schema.json                # 插件配置 Schema
├── requirements.txt                 # Python 依赖
├── README.md                        # 项目说明文档
├── .astrbot-plugin/
│   └── i18n/
│       └── zh-CN.json               # 中文翻译
├── mmx/                             # MiniMax API 客户端层
│   ├── __init__.py
│   ├── client.py                    # MiniMax HTTP 客户端与鉴权
│   ├── endpoints.py                 # API 端点 URL 构建
│   ├── errors.py                    # 错误分类与映射
│   ├── sse.py                       # SSE 流式解析
│   ├── files.py                     # 文件上传、列表、删除与检索
│   ├── model_options.py             # 模型默认值与有效模型定义
│   ├── quota_usage.py               # 额度数据标准化与汇总
│   └── apis/                        # MiniMax API 领域封装
│       ├── image.py                 # 图片生成 API
│       ├── video.py                 # 视频生成与任务轮询 API
│       ├── music.py                 # 音乐生成与翻唱 API
│       ├── speech.py                # 语音合成 API
│       ├── search.py                # 联网搜索 API
│       ├── vision.py                # 视觉理解 API
│       └── quota.py                 # 额度查询 API
└── tools/                           # LLM 工具
    ├── __init__.py
    ├── image_tools.py               # 图片生成工具
    ├── video_tools.py               # 视频生成工具
    ├── video_task_tools.py          # 视频任务查询与下载工具
    ├── music_tools.py               # 音乐生成工具
    ├── music_cover_tools.py         # 音乐翻唱工具
    ├── background_task_tools.py     # 后台任务查询工具
    ├── background_tasks.py          # 后台任务注册表
    ├── audio_result.py              # 音频结果保存与后台调度
    ├── speech_tools.py              # 语音合成与音色列表工具
    ├── file_tools.py                # 文件上传、列表与删除工具
    ├── search_tools.py              # 联网搜索工具
    ├── vision_tools.py              # 视觉理解工具
    └── quota_tools.py               # 额度查询工具
```

## 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)。

## 相关链接

- [AstrBot 文档](https://docs.astrbot.app/)
- [MiniMax Token Plan](https://platform.minimaxi.com/subscribe/token-plan)
- [MiniMax Cli](https://github.com/MiniMax-AI/cli)
- [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [Issues](https://github.com/piexian/astrbot_plugin_mmx_cli_tool/issues)

## 许可

GPL-3.0 License

<div align="center">

**如果这个插件对你有帮助，请给个 ⭐ Star 支持一下！**

</div>
