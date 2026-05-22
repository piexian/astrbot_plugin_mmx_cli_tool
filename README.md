# MiniMax 多模态工具

为 AstrBot 提供 MiniMax 图片生成、视频生成、音乐生成、联网搜索、视觉理解、额度查询能力。


## 环境要求

| 依赖 | 版本要求 |
|------|----------|
| httpx | >= 0.27.0 |

## 功能

- 11 个 LLM Tool，全面覆盖 MiniMax 多模态 API（图片/视频/音乐/语音合成/联网搜索/视觉理解）
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
| `mmx_generate_music` | 生成音乐（支持纯器乐、带歌词，及所有精细控制参数） | 无 |
| `mmx_music_cover` | 基于参考音频及描述进行翻唱（支持 URL 和本地音频输入） | 无 |
| `mmx_speech_synthesize` | 将文本合成语音（TTS），支持 30+ 种音色和语速/音量/音高控制 | 无 |
| `mmx_speech_voices` | 查询可用 TTS 系统音色列表 | 无 |
| `mmx_web_search` | 联网搜索信息 | 无 |
| `mmx_describe_image` | 分析/描述图片内容（视觉理解） | 无 |
| `mmx_check_quota` | 查询 API 剩余额度 | 无 |

## 使用

### 指令

```
/mmx image <描述>              # 生成图片
/mmx video <描述>              # 生成视频
/mmx music <描述> [--instrumental]  # 生成音乐
/mmx speech <文本>             # 语音合成
/mmx search <查询>             # 联网搜索
/mmx vision <描述要求>         # 图片理解（支持当前消息带图或引用图片）
/mmx quota                    # 查询额度
```

### 示例

```
/mmx image a cute cat wearing a hat, watercolor style
/mmx video a sunset over the ocean with gentle waves
/mmx music warm acoustic guitar, folk style --instrumental
/mmx speech 欢迎使用 MiniMax 语音合成功能
/mmx search 今天天气怎么样
/mmx vision 描述这张图片里有什么
/mmx quota
```

### LLM 对话中使用

当 AI 需要生成图片、视频、语音、音乐，或进行搜索、图片理解时，会自动调用对应工具。

例如对 AI 说「帮我把这段话合成语音：你好」→ 自动调用 `mmx_speech_synthesize`。

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
- `subjectRef` (string): 角色一致性参考图（URL 或本地路径）。

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

#### 6. `mmx_music_cover` (音乐翻唱)
- `prompt` (string): 翻唱的目标音乐风格或歌声特征描述。
- `audio` (string): 参考音频的 URL 链接（支持 6秒~6分钟，最大 50MB）。与 `audioFile` 选填其一。
- `audioFile` (string): 本地参考音频文件路径。与 `audio` 选填其一。
- `lyrics` (string): 歌词（如留空则通过 ASR 自动从参考音频提取）。
- `seed` (integer): 随机种子。

#### 7. `mmx_speech_synthesize` (语音合成/TTS)
- `text` (string, 必填): 需要合成的文本（最大 10000 字符）。
- `voice` (string): 音色 ID（默认 `English_expressive_narrator`）。
- `model` (string): 模型名（如 `speech-2.8-hd`, `speech-2.6`, `speech-02`）。
- `speed` / `volume` / `pitch` (number): 语速倍率（0.5-2.0）/ 音量 / 音高微调。
- `format` (string): 导出音频格式（如 `mp3`, `pcm`, `flac`, `wav`, `opus`）。
- `language` (string): 语种权重。

#### 8. `mmx_speech_voices` (列出 TTS 音色)
- `language` (string): 过滤语言（如 `english`, `chinese` 等）。

#### 9. `mmx_web_search` (联网搜索)
- `q` (string, 必填): 搜索查询关键字。

#### 10. `mmx_describe_image` (视觉理解)
- `image` (string, 必填): 图片 URL 或本地绝对路径。
- `prompt` (string): 分析要求描述。
- `fileId` (string): 预先上传的文件 ID。

#### 11. `mmx_check_quota` (额度查询)
- 无参数。查询当前所有 API Key 剩余的总额度。

## 安全设计

- **API Key 脱敏**：错误日志中自动隐藏 API Key（仅显示前 4 后 4 字符）
- **文件名安全**：保存文件使用时间戳命名，避免路径注入
- **输出目录限制**：媒体文件仅保存到 AstrBot 插件数据目录（`data/plugin_data/astrbot_plugin_mmx_cli_tool/`）
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
| `default_music_model` | string | `music-2.6` | 默认音乐生成模型 |

## 项目结构

```
astrbot_plugin_mmx_cli_tool/
├── main.py                          # 插件入口（Star 子类）
├── metadata.yaml                    # 插件元数据
├── _conf_schema.json                # 配置 Schema
├── requirements.txt                 # Python 依赖
├── README.md
├── .astrbot-plugin/
│   └── i18n/
│       └── zh-CN.json               # 中文翻译
├── mmx/                             # MiniMax API 客户端层
│   ├── __init__.py
│   ├── client.py                    # httpx.AsyncClient + 鉴权
│   ├── endpoints.py                 # API URL builder
│   ├── errors.py                    # 错误分类与映射
│   ├── sse.py                       # SSE 流式解析
│   ├── files.py                     # 文件上传/下载
│   └── apis/
│       ├── image.py                 # ImageAPI
│       ├── video.py                 # VideoAPI + 轮询
│       ├── music.py                 # MusicAPI
│       ├── speech.py                # SpeechAPI (TTS)
│       ├── search.py                # SearchAPI
│       ├── vision.py                # VisionAPI
│       └── quota.py                 # QuotaAPI
└── tools/                           # LLM 工具
    ├── __init__.py
    ├── image_tools.py               # GenerateImageTool
    ├── video_tools.py               # GenerateVideoTool
    ├── video_task_tools.py          # QueryVideoTaskTool & DownloadVideoTool
    ├── music_tools.py               # GenerateMusicTool
    ├── music_cover_tools.py         # MusicCoverTool
    ├── speech_tools.py              # SpeechSynthesizeTool & ListVoicesTool
    ├── search_tools.py              # WebSearchTool
    ├── vision_tools.py              # DescribeImageTool
    └── quota_tools.py               # CheckQuotaTool
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
