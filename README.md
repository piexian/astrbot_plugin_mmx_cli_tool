# MiniMax 多模态工具

为 AstrBot 提供 MiniMax 图片生成、视频生成、音乐生成、联网搜索、视觉理解、额度查询能力。


## 环境要求

| 依赖 | 版本要求 |
|------|----------|
| httpx | >= 0.27.0 |

## 功能

- 6 个 LLM Tool，覆盖 MiniMax 多模态 API
- `/mmx` 命令组，用户可直接通过指令调用
- API 额度查询
- 媒体文件自动保存到本地

## 安装

**方式一**：在 AstrBot 插件市场搜索「MiniMax 多模态工具」，点击安装。

**方式二**：插件界面右下角加号 → 从链接安装，输入：
```
https://github.com/piexian/astrbot_plugin_mmx_cli_tool
```

## 工具列表

| 工具 | 功能 | 权限 |
|------|------|------|
| `mmx_generate_image` | 根据文字描述生成图片 | 无 |
| `mmx_generate_video` | 根据文字描述生成视频 | 无 |
| `mmx_generate_music` | 生成音乐（支持纯器乐/歌词） | 无 |
| `mmx_web_search` | 联网搜索信息 | 无 |
| `mmx_describe_image` | 分析/描述图片内容 | 无 |
| `mmx_check_quota` | 查询 API 剩余额度 | 无 |

## 使用

### 指令

```
/mmx image <描述>              # 生成图片
/mmx video <描述>              # 生成视频
/mmx music <描述> [--instrumental]  # 生成音乐
/mmx search <查询>             # 联网搜索
/mmx vision <描述要求>         # 图片理解（支持当前消息带图或引用图片）
/mmx quota                    # 查询额度
```

### 示例

```
/mmx image a cute cat wearing a hat, watercolor style
/mmx video a sunset over the ocean with gentle waves
/mmx music warm acoustic guitar, folk style --instrumental
/mmx search 今天天气怎么样
/mmx vision 描述这张图片里有什么
/mmx quota
```

### LLM 对话中使用

当 AI 需要生成图片、视频、音乐，或进行搜索、图片理解时，会自动调用对应工具。

例如对 AI 说「帮我生成一张落日海滩的图片」→ 自动调用 `mmx_generate_image`。

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
│       ├── search.py                # SearchAPI
│       ├── vision.py                # VisionAPI
│       └── quota.py                 # QuotaAPI
└── tools/                           # LLM 工具
    ├── __init__.py
    ├── image_tools.py               # GenerateImageTool
    ├── video_tools.py               # GenerateVideoTool
    ├── music_tools.py               # GenerateMusicTool
    ├── search_tools.py              # WebSearchTool
    ├── vision_tools.py              # DescribeImageTool
    └── quota_tools.py               # CheckQuotaTool
```

## 更新日志

详见 [CHANGELOG.md](./CHANGELOG.md)。

## 相关链接

- [AstrBot 文档](https://docs.astrbot.app/)
- [MiniMax Token Plan](https://platform.minimaxi.com/subscribe/token-plan)
- [插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [Issues](https://github.com/piexian/astrbot_plugin_mmx_cli_tool/issues)

## 许可

GPL-3.0 License

<div align="center">

**如果这个插件对你有帮助，请给个 ⭐ Star 支持一下！**

</div>
