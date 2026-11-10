# MiniMax 多模态工具：指令与参数完整说明

本文列出插件全部 `/mmx` 聊天指令，以及每个参数的写法、默认值和限制。README 的「指令用法」只给最常用的例子，想知道某个参数怎么写、能填什么、填错了会怎样，查这里。

## 目录

- [通用约定](#通用约定)
  - [指令格式](#指令格式)
  - [参数怎么写](#参数怎么写)
  - [图片和音频从哪里来](#图片和音频从哪里来)
  - [权限与凭据标记](#权限与凭据标记)
  - [生成的文件去哪里](#生成的文件去哪里)
- [指令速查表](#指令速查表)
- [/mmx login](#mmx-login)
- [/mmx logout](#mmx-logout)
- [/mmx signin](#mmx-signin)
- [/mmx usage](#mmx-usage)
- [/mmx image](#mmx-image)
- [/mmx video](#mmx-video)
- [/mmx speech](#mmx-speech)
  - [/mmx speech transcribe](#mmx-speech-transcribe)
- [/mmx music](#mmx-music)
- [/mmx sfx](#mmx-sfx)
- [/mmx search](#mmx-search)
- [/mmx vision](#mmx-vision)
- [/mmx search-image](#mmx-search-image)
- [/mmx reverse-image](#mmx-reverse-image)
- [/mmx file](#mmx-file)
  - [/mmx file upload](#mmx-file-upload)
  - [/mmx file list](#mmx-file-list)
  - [/mmx file delete](#mmx-file-delete)
- [/mmx quota](#mmx-quota)
  - [/mmx quota 序号](#mmx-quota-序号)
  - [/mmx quota page](#mmx-quota-page)
- [附录：参数别名对照](#附录参数别名对照)

## 通用约定

### 指令格式

- 所有指令都以 `/mmx` 开头，后面接子指令，例如 `/mmx image 一只猫`。`/` 是 AstrBot 默认的指令前缀，如果你在 AstrBot 里改过前缀，请换成自己的。
- 只发送 `/mmx`，AstrBot 会列出这个指令组下的所有子指令。
- 写法一般是「子指令 + 内容 + 参数」：内容（描述、关键词、文本）写在前面，参数写在后面，中间用空格隔开。
- 参数都以两个短横线 `--` 开头。以 `--` 开头的词一律被当作参数名，写错会回复「不支持的参数: --xxx」和用法。
- 参数写错（数字超范围、缺少必要的值等）时，机器人会直接回复具体原因，例如 `--n 必须在 1 到 9 之间`。
- 下文所有示例里的指令，一行就是一条要发送的消息。

### 参数怎么写

**带值的参数**有两种写法，效果相同：

```
/mmx image 一只猫 --aspect-ratio 16:9
/mmx image 一只猫 --aspect-ratio=16:9
```

- 空格写法：值会一直读到下一个以 `--` 开头的词为止，所以多个词的值不用加引号，例如 `--lyrics 第一句 第二句`。
- 等号写法：只读等号后面紧挨着的那一个词。值里有空格时，要用双引号括起来，例如 `--pronunciation="处理/(chu li)"`。
- **描述要写在所有参数之前。** 如果把描述写在带值的参数后面，它会被当成那个参数的值。想把描述放后面，用 `--prompt`（图片、视频）或 `--text`（语音合成）显式指定；这时命令后面直接写的描述文字会被忽略。
- 同一个参数写了多次，后面的覆盖前面的。只有 `--pronunciation` 例外，见下面「可重复的参数」。

**引号**：

- 只有双引号 `"` 能把带空格的内容括成一个整体。单引号当普通字符，所以英文里的撇号（`cat's`）可以直接写。
- 双引号没有闭合，会回复「参数解析失败」。
- 不在引号里的反斜杠 `\` 会被吃掉：`D:\data\a.mp3` 会变成 `D:dataa.mp3`。路径请用 `/`，或者用双引号括起来。

**开关参数**（不带值，例如 `--aigc-watermark`、`--no-wait`、`--subtitles`）：

- 写出来就是开启。
- 要把一个默认开启的开关关掉，必须用等号写：`--prompt-optimizer=false`。
- 等号后面的值不区分大小写：`true`、`1`、`yes`、`on` 表示开，`false`、`0`、`no`、`off` 表示关；写别的值会回复「布尔参数值无效」。
- 不能写成 `--prompt-optimizer false`：`false` 会被当成描述文字的一部分。

**可重复的参数**：只有语音合成的 `--pronunciation`，写几次就累积几条。

**可以省略值的参数**：图片的 `--subject-ref`，视频的 `--first-frame`、`--last-frame`、`--subject-image`。不写值时，插件会从你发的消息或引用的消息里取图片。“不写值”指它后面紧跟的是下一个以 `--` 开头的参数，或者消息已经结束；如果后面跟着普通文字，那段文字会被当成它的值。所以这几个参数要放在描述的后面，且它们后面只能接其他参数。

**数字参数**：必须是整数或数字，否则会回复「n 必须是整数: abc」之类的提示。负数可以直接写（`--seed -5`）。

**写法别名**：大部分参数同时支持短横线写法（`--aspect-ratio`）和驼峰写法（`--aspectRatio`），大小写必须完全一致，`--Aspect-Ratio` 不行。完整对照见[附录](#附录参数别名对照)。语音转文字指令只支持短横线写法。

### 图片和音频从哪里来

**图片**（识图、以图搜图、图片的 `--subject-ref`、视频的首帧/尾帧/角色参考图）：

- 最省事的做法：在发指令的这条消息里附带图片，或者引用（回复）一条带图片的消息。
- 当前消息和被引用消息里的图片都能找到，按它们出现的顺序使用，用到几张取几张。需要两张图（视频首帧加尾帧）时，建议在同一条消息里发两张。
- 远程图片会先下载到 AstrBot 临时目录再使用。识图和以图搜图时，如果找到了图片却读不出来，机器人会提示「检测到了图片，但当前无法在本地解析」，请改为直接发送图片。
- 也可以在参数里手写：图片链接（`http://` 或 `https://` 开头），或者文件路径。手写的文件路径必须在插件数据目录或 AstrBot 临时目录之内；相对路径依次到这两个目录里找；跳出这两个目录的路径（包括 `..` 跳出去）会被拒绝。
- AstrBot 临时目录只有在配置项 `allow_astrbot_temp_dir` 开启时才能读取（默认开启）。关闭后只剩插件数据目录。
- 从消息里取到的图片是插件自己落盘的，不受上面的目录限制。

**音频**（语音转文字）：

- 在发指令的这条消息里附带语音或音频文件，或者引用一条带语音或文件的消息，就不用写 `--file`。
- 写了 `--file` 时只用 `--file`，不再看消息里的附件。`--file` 只能是插件数据目录或 AstrBot 临时目录里的本地文件，不接受网址。

插件数据目录是 AstrBot 数据目录下的 `plugin_data/astrbot_plugin_mmx_cli_tool/`。

### 权限与凭据标记

下文和速查表里的标记含义：

| 标记 | 含义 |
|------|------|
| 管理员 | 只有 AstrBot 里配置的管理员能用，其他人发送会被拒绝 |
| 需登录 | 必须先由管理员发送 `/mmx login` 登录 MiniMax 账号 |
| 需要 API Key 或登录 | 填了配置项 `api_key` 就用它；没填 `api_key` 时，`/mmx login` 登录成功后插件会自动获取账号绑定的 API Key 并使用。两者都有时，以 `api_key` 为准，登录不会覆盖它。两者都没有，这条指令用不了 |
| 优先登录，没登录用 API Key | 搜图、以图搜图、音乐、音效属于这一类：优先用登录账号，没登录时改用配置的 API Key，但服务器不接受时会提示你先 `/mmx login` |
| 无 | 不需要凭据 |

文中提到的配置项（如 `api_key`、`default_image_model`）见 [README 的配置说明](../README.md#配置说明)。

### 生成的文件去哪里

- 生成的图片、语音、视频、音乐、音效、搜到的图片，都先下载到 AstrBot 临时目录，再作为图片、语音、视频或文件发给你。临时目录由 AstrBot 统一管理，不用自己清理。
- 直接指令不能指定保存位置：图片的 `--out`、`--out-dir`、`--out-prefix`，语音合成的 `--text-file`、`--out`、`--stream`，视频的 `--download`，写了都会被拒绝并给出原因（插件会自动保存并发送结果，不需要这些参数）。
- 唯一能写文件的参数是 `/mmx speech transcribe --out`：只能写到插件数据目录里，写法是相对路径，不允许绝对路径、带冒号的路径（包括网址和 Windows 盘符）、含 `..` 的路径，也不能是目录；中间的文件夹会自动创建。

## 指令速查表

| 指令 | 作用 | 权限 | 凭据 | 对应 LLM 工具 |
|------|------|------|------|------|
| `/mmx login` | 登录 MiniMax 账号，并在没填 `api_key` 时自动获取 API Key | 管理员 | 无 | 无 |
| `/mmx logout` | 退出账号登录，清除本地登录信息 | 管理员 | 无 | 无 |
| `/mmx signin` | 领取今天的 MiniMax Code 签到积分 | 管理员 | 需登录 | 无 |
| `/mmx usage` | 查看积分、套餐余量和签到进度 | 管理员 | 需登录 | 无 |
| `/mmx image` | 按文字描述生成图片 | 所有人 | 需要 API Key 或登录 | `mmx_generate_image` |
| `/mmx video` | 按文字描述（可加首尾帧或角色参考图）生成视频 | 所有人 | 需要 API Key 或登录 | `mmx_generate_video`（查进度、下载用 `mmx_video_task_get`、`mmx_video_download`） |
| `/mmx speech` | 文字转语音 | 所有人 | 需要 API Key 或登录 | `mmx_speech_synthesize`（查音色用 `mmx_speech_voices`） |
| `/mmx speech transcribe`（也可写 `recognize`） | 语音转文字，可出字幕 | 所有人 | 需要 API Key 或登录 | `mmx_speech_transcribe` |
| `/mmx music` | 生成音乐，可带歌词 | 所有人 | 优先登录，没登录用 API Key | `mmx_generate_music` |
| `/mmx sfx` | 生成音效 | 所有人 | 优先登录，没登录用 API Key | `mmx_generate_sound_effect` |
| `/mmx search` | 联网搜索 | 所有人 | 需要 API Key 或登录 | `mmx_web_search` |
| `/mmx vision` | 看图说话，分析图片内容 | 所有人 | 需要 API Key 或登录 | `mmx_describe_image` |
| `/mmx search-image` | 搜真实的网络图片并发送 | 所有人 | 优先登录，没登录用 API Key | `mmx_search_images` |
| `/mmx reverse-image` | 以图搜图，找出处和相似网页 | 所有人 | 优先登录，没登录用 API Key | `mmx_image_reverse_search` |
| `/mmx file upload` | 上传文件到 MiniMax 存储 | 管理员 | 需要 API Key 或登录 | `mmx_file_upload`（工具也只有管理员能用） |
| `/mmx file list` | 列出已上传的文件 | 管理员 | 需要 API Key 或登录 | `mmx_file_list`（同上） |
| `/mmx file delete` | 删除已上传的文件 | 管理员 | 需要 API Key 或登录 | `mmx_file_delete`（同上） |
| `/mmx quota` | 分页查看各个 Key 的额度 | 所有人 | 需要 API Key 或登录 | `mmx_check_quota` |
| `/mmx quota <序号>` | 只看第几个 Key 的额度 | 所有人 | 需要 API Key 或登录 | `mmx_check_quota` |
| `/mmx quota page <页码>`（`page` 可简写为 `p`） | 翻到指定页 | 所有人 | 需要 API Key 或登录 | `mmx_check_quota` |

「对应 LLM 工具」是 AI 在对话里能自动调用的同类功能，参数名是驼峰写法，下面各节会写明差别。账号类指令（登录、退出、签到、用量）没有对应的 AI 工具。

## /mmx login

**用法**：`/mmx login`

登录 MiniMax 账号，解锁搜图、以图搜图、音乐、音效、MiniMax Code 对话和签到。没填 `api_key` 时，登录成功后还会自动获取账号绑定的 API Key。需要管理员。

登录分两步：

1. 机器人问你选区域，请在 60 秒内回复：`1`（也可写 `cn`、`国内`、`国内站`）选国内站，`2`（也可写 `global`、`en`、`国际`、`国际站`、`海外`）选国际站，`c`（也可写 `cancel`、`取消`）取消。回复无效会提示重新输入。群聊里只认发起人的回复。
2. 机器人发来授权链接和验证码，在浏览器打开链接完成授权。机器人会自动等待，最长 10 分钟，授权后自动完成登录。

此指令没有参数。

**限制与注意**

- 登录时选的区域只管账号和自动获取的 API Key，和配置项 `region` 无关。
- 同一个会话里已有登录流程在进行时，再发会回复「当前会话已有进行中的登录流程」。
- 可能的结果：登录成功（回复里会说明 API Key 是否已自动获取、MiniMax Code 提供商源的状态）、授权被拒绝、授权超时（需重新发 `/mmx login`）、60 秒内没选区域（登录超时取消）。
- 登录状态保存在插件数据目录里。

**对应 LLM 工具**：无。

## /mmx logout

**用法**：`/mmx logout`

退出 MiniMax 账号登录：撤销登录凭证，清除本地保存的登录信息和自动获取的 API Key，并注销运行时注册的 MiniMax Code 供应商。需要管理员。没登录时回复「当前未登录 MiniMax 账号」。

此指令没有参数。

**限制与注意**

- 如果默认聊天模型是 `mmx_minimax_code`，退出后请在 WebUI 切换到其他供应商，否则对话会因未登录而失败。
- 没填 `api_key` 的话，退出后图片、视频等也会因为没有 Key 而不可用，直到再次登录。

**对应 LLM 工具**：无。

## /mmx signin

**用法**：`/mmx signin`

领取今天的 MiniMax Code 签到积分。需要管理员，并且要先登录。每天只能领一次，重复发送不会多给。成功时回复「签到成功（第 N 天），积分 +M」，通常还会补充签到周期的天数，并提示可以用 `/mmx usage` 看详情。

此指令没有参数。

**限制与注意**

- 没登录时回复让管理员先发 `/mmx login`。
- 想每天自动领，打开配置项 `auto_signin`：插件每 30 分钟检查一次并领取当天的积分。

**对应 LLM 工具**：无。

## /mmx usage

**用法**：`/mmx usage`

查看 MiniMax Code 账号的积分和套餐余量。需要管理员，并且要先登录。

回复里包含下面几项。各项分别查询，某一项失败不会影响其他项：

- 账号：用户名和邮箱。
- 积分余额，以及「积分兜底」是否开启。
- 积分明细：最多列出前 3 条（剩余、总额、到期日），再给出共几条、剩余合计。
- 套餐余量：最多列出 5 个模型的剩余次数。
- 签到：当前周期共几天，今天是第几天、对应多少积分。

此指令没有参数。

**对应 LLM 工具**：无。

## /mmx image

**用法**：`/mmx image <描述> [参数]`

按文字描述生成图片，生成后发送到当前聊天。路由在提交请求前一次性决定：已登录 MiniMax Code 走桌面网关 H3 Image；显式指定 `--model`、自定义宽高、`--seed`、参考图、加水印或关闭提示词优化时走开放平台。`default_image_model` 或 `--model` 为 `MiniMax-H3-Image` / `MiniMax-H3-Image-Highspeed` 时走开放平台 H3 文生图（`/v1/images/generations`，`--resolution` 映射为 `area`）。超时或部分成功不自动换路重试，部分失败会在日志中记录；`seed` 与参考图仅 image-01 系列支持，H3 模型传入会直接报错。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--prompt` | — | 文本 | — | 描述的显式写法。写了它，命令后面直接写的描述会被忽略 |
| `--model` | — | 文本 | 不写，按登录态自动路由 | 显式指定图片模型将强制走开放平台；不写时 MiniMax Code 登录走 H3 Image，API Key 模式用配置项 `default_image_model`（`image-01` 系列走 `/v1/image_generation`，`MiniMax-H3-Image` / `MiniMax-H3-Image-Highspeed` 走 `/v1/images/generations`） |
| `--aspect-ratio` | `--aspectRatio` | 文本 | 不写，不传给服务器 | 图片比例，如 `1:1`、`16:9` |
| `--resolution` | — | `1K`、`2K`、`4K` | 不写，服务端默认 | H3 Image 路由（登录或 H3 模型）可用，映射为 H3 的 `area`；落到 image-01 时报错 |
| `--n` | — | 整数 1 到 9 | `1` | 一次生成几张。注意：指令只会发送第一张 |
| `--seed` | — | 整数 | 不写 | 随机种子，相同种子更容易得到相似的图 |
| `--width` | — | 整数，512 到 2048，且是 8 的倍数 | 不写 | 自定义宽度，必须和 `--height` 一起写 |
| `--height` | — | 整数，512 到 2048，且是 8 的倍数 | 不写 | 自定义高度，必须和 `--width` 一起写 |
| `--prompt-optimizer` | `--promptOptimizer` | 开关 | 开启 | 让系统帮你优化描述；关闭写 `--prompt-optimizer=false`。H3 模型上关闭时必须同时指定 `--aspect-ratio` 或 `--width/--height`（服务端要求），否则报错 |
| `--aigc-watermark` | `--aigcWatermark` | 开关 | 关闭 | 给图片加「AI 生成」水印 |
| `--subject-ref` | `--subjectRef` | 可省略值，写法见下 | 不用 | 角色参考，让人物保持一致 |
| `--response-format` | `--responseFormat` | `url` 或 `base64` | `url` | 服务器返回图片的方式，一般不用改 |
| `--out`、`--out-dir`、`--out-prefix` | — | 不支持 | — | 写了会被拒绝：「AstrBot 会自动保存并发送图片，不支持 --out 指定服务器路径」（后两个同理） |

`--subject-ref` 的写法：

- `--subject-ref type=character,image=路径或链接`：完整写法。`type` 不写时默认 `character`。
- `--subject-ref 路径或链接`：只写图片，类型用 `character`。
- `--subject-ref`（不写值）：使用当前消息或引用消息里的第一张图片；找不到图片时回复让你补充。
- 图片是链接就直接用；是文件路径时必须在插件数据目录或 AstrBot 临时目录内，见[图片和音频从哪里来](#图片和音频从哪里来)。

**示例**

```
/mmx image 一只戴帽子的猫，水彩风格 --aspect-ratio 16:9
/mmx image 赛博朋克夜景 --width 1024 --height 768 --seed 42 --aigc-watermark
/mmx image 让这个角色在海边散步 --subject-ref
/mmx image 一杯咖啡 --prompt-optimizer=false
```

第 3 条需要同时附带或引用一张角色图片。

**限制与注意**

- `--n` 写多张时，插件会向服务器要求生成多张，但指令只发送第一张。想拿到所有图片，让 AI 调用工具，工具会保存并返回全部图片的路径。
- `--width` 和 `--height` 只写一个会报错「--width 和 --height 必须同时提供」；不在范围内或不是 8 的倍数会报错「--width 必须在 512-2048 之间且为 8 的倍数」。
- 图片先下载到 AstrBot 临时目录再发送；下载失败时，改为直接用服务器返回的远程图片地址发送。

**对应 LLM 工具**：`mmx_generate_image`。参数名是驼峰写法：`prompt`、`model`、`aspectRatio`、`n`、`seed`、`width`、`height`、`promptOptimizer`、`aigcWatermark`、`subjectRef`、`responseFormat`。差别：工具的 `subjectRef` 必须写值，不会自动取消息里的图片；工具会返回所有图片的本地路径。

## /mmx video

**用法**：`/mmx video <描述> [参数]`

按文字描述生成视频。视频生成要等一会儿，默认等做完再发送；加 `--no-wait` 则提交后马上返回任务编号。

用哪种方式生成，由你写的参数决定：

- 只写描述：文字生成视频。
- 加 `--first-frame`：以这张图为起始画面。
- 同时加 `--first-frame` 和 `--last-frame`：首尾帧视频，用配置项 `default_video_sef_model` 的模型（默认 `MiniMax-Hailuo-02`）。
- 加 `--subject-image`：角色参考视频，让人物保持一致，用配置项 `default_video_subject_model` 的模型（默认 `S2V-01`）。
- 其他情况用配置项 `default_video_model` 的模型（默认 `MiniMax-Hailuo-2.3`）。写了 `--model` 则以它为准。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--prompt` | — | 文本 | — | 描述的显式写法。写了它，命令后面直接写的描述会被忽略 |
| `--model` | — | 文本 | 按上面的规则自动选 | 指定视频模型，一般不用写。`MiniMax-H3` 有额外参数，见下 |
| `--first-frame` | `--firstFrame` | 可省略值：图片链接、文件路径 | 不用 | 起始帧图片。不写值则使用消息里的图片 |
| `--last-frame` | `--lastFrame` | 可省略值：图片链接、文件路径 | 不用 | 结束帧图片，必须同时写 `--first-frame`。不写值则使用消息里的图片 |
| `--subject-image` | `--subjectImage` | 可省略值：图片链接、文件路径 | 不用 | 角色参考图，让人物保持一致。不写值则使用消息里的图片；不能和首尾帧一起用 |
| `--callback-url` | `--callbackUrl` | 网址（URL） | 不用 | 视频完成后的回调地址 |
| `--no-wait` | `--async` | 开关 | 关闭 | 提交后马上返回任务编号 `task_id`，不等待完成 |
| `--poll-interval` | `--pollInterval` | 整数（秒） | 配置项 `video_poll_interval`（默认 `5`） | 等待期间每隔几秒查一次进度 |
| `--duration` | — | 整数 4 到 15（秒） | `5` | 视频时长。仅 `MiniMax-H3` 模型有效，其他模型会忽略 |
| `--ratio` | — | `adaptive`、`21:9`、`16:9`、`4:3`、`1:1`、`3:4`、`9:16` | 有图片输入时 `adaptive`，纯文字时 `16:9` | 画面比例。仅 `MiniMax-H3` 模型有效，其他模型会忽略 |
| `--download` | — | 不支持 | — | 写了会被拒绝：「AstrBot 会自动下载并发送视频，不支持 --download 指定服务器路径」 |

关于从消息里取图片：`--first-frame`、`--last-frame`、`--subject-image` 不写值时，按下面的方式取图（图片来源见[图片和音频从哪里来](#图片和音频从哪里来)）：

- 只有一个不写值的参数：取消息里的第一张图。
- `--first-frame` 和 `--last-frame` 都不写值：第一张给首帧，第二张给尾帧，所以消息里要有两张图。
- 一个写了路径、另一个不写值：不写值的那个取消息里的第一张图。

**示例**

```
/mmx video 海边日落，海浪轻轻拍打
/mmx video 让图里的人挥手 --first-frame
/mmx video 从白天到夜晚 --first-frame --last-frame --no-wait
/mmx video 一只猫在弹钢琴 --model MiniMax-H3 --duration 8 --ratio 16:9
```

第 2 条需要同时附带或引用一张图片，第 3 条需要两张。

**限制与注意**

- 同时写 `--subject-image` 和 `--first-frame`/`--last-frame` 会报错；只写 `--last-frame` 不写 `--first-frame` 也会报错。
- `MiniMax-H3` 不支持角色参考，写 `--subject-image` 会提示改用参考图片（参考图片只有 AI 工具支持，指令里没有这个参数）。
- `MiniMax-H3` 的输出固定为 2K。纯文字生成时必须指定具体比例，不能用 `adaptive`；`--ratio` 不在上面的列表里、`--duration` 不在 4 到 15 之间，都会回复「视频生成失败」并说明原因。想用 `MiniMax-H3`，写 `--model MiniMax-H3`，或把配置项 `default_video_model` 改成它。
- 等待时间上限是配置项 `video_timeout`（默认 `600` 秒）。超时只是不再等，视频仍在 MiniMax 后台生成，机器人会告诉你任务编号 `task_id`，并提示前往 MiniMax 控制台查看。
- `--no-wait` 和超时返回的 `task_id` 没有对应的指令来查询，可以让 AI 调用 `mmx_video_task_get` 查进度、`mmx_video_download` 下载；查询 `MiniMax-H3` 的任务时，需要告诉 AI 模型是 `MiniMax-H3`。
- 视频先下载到 AstrBot 临时目录再发送；下载失败时，如果服务器给了视频直链（`MiniMax-H3` 会给），就直接发送这个链接，否则回复「视频生成完成，但下载失败」。

**对应 LLM 工具**：`mmx_generate_video`，另有 `mmx_video_task_get`（查进度）和 `mmx_video_download`（下载）。参数名是驼峰写法：`prompt`、`model`、`firstFrame`、`lastFrame`、`subjectImage`、`callbackUrl`、`noWait`、`pollInterval`、`duration`、`ratio`。差别：工具还支持只给 `MiniMax-H3` 用的 `referenceImages`（最多 9 张）、`referenceVideos`（最多 3 个）、`referenceAudios`（最多 3 个，必须配合参考图或参考视频），指令里没有这些参数；工具的图片参数必须写值，不会自动取消息里的图片。

## /mmx speech

**用法**：`/mmx speech <文本> [参数]`

把文字读出来，作为语音消息发送。如果指令后面的第一个词正好是小写的 `transcribe` 或 `recognize`，会被当成[语音转文字](#mmx-speech-transcribe)；真要合成这样开头的文字，请写成 `/mmx speech --text "transcribe ..."`。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--text` | — | 文本 | — | 要朗读的文字，也可以直接写在指令后面。写了它，命令后面直接写的文字会被忽略 |
| `--model` | — | 文本 | 配置项 `default_speech_model`（默认 `speech-2.8-hd`） | 语音模型，如 `speech-2.8-hd`、`speech-2.6`、`speech-02` |
| `--voice` | — | 音色 ID | 配置项 `default_speech_voice`（默认 `English_expressive_narrator`） | 音色 ID |
| `--speed` | — | 数字 | 不写，不传给服务器 | 语速 |
| `--volume` | — | 数字 | 不写，不传给服务器 | 音量 |
| `--pitch` | — | 数字 | 不写，不传给服务器 | 音高 |
| `--emotion` | — | 文本 | 不写 | 情绪，如 `happy`、`sad`、`angry`、`fearful`、`disgusted`、`surprised`、`calm`、`fluent`、`whisper`；插件原样交给服务器，不做检查 |
| `--text-normalization` | `--textNormalization` | 开关 | 关闭 | 让数字、符号读得更自然 |
| `--latex-read` | `--latexRead` | 开关 | 关闭 | 朗读文字里的 LaTeX 公式 |
| `--format` | — | `mp3`、`pcm`、`flac`、`wav`、`pcmu_raw`、`pcmu_wav`、`opus` | `mp3` | 音频格式，写别的会回复「--format 参数不支持」 |
| `--sample-rate` | `--sampleRate` | 整数 | `32000` | 采样率 |
| `--bitrate` | — | 整数 | `128000` | 码率 |
| `--channels` | — | 整数 | `1` | 声道数 |
| `--language` | — | 文本 | 不写 | 指定语种，读得更准，如 `english`、`chinese`、`korean` |
| `--subtitles` | — | 开关 | 关闭 | 同时向服务器请求字幕时间信息 |
| `--pronunciation` | — | `文本/(读音)`，可重复 | 不写 | 自定义读音，如 `处理/(chu li)`，要多条就写多次 |
| `--text-file`、`--out`、`--stream` | — | 不支持 | — | 写了会被拒绝：不读取服务器上的文本文件、不能指定保存路径、不支持原始音频流模式 |

**示例**

```
/mmx speech 欢迎使用 MiniMax 语音合成 --speed 1.1
/mmx speech 你好，世界 --voice English_expressive_narrator --emotion happy --format wav
/mmx speech 这件事怎么处理 --pronunciation 处理/(chu li) --pronunciation 重/(zhong)
```

**限制与注意**

- 一次合成的文本最长 10000 字符（来自接口说明，插件不会预先检查）。
- 指令只发送合成好的语音；`--subtitles` 虽然会向服务器请求字幕信息，但字幕内容不会显示出来。
- `--speed`、`--volume`、`--pitch` 的取值范围由 MiniMax 服务器决定，插件只检查它们是数字，写成别的会回复「speed 必须是数字: xxx」之类的提示。
- 语音先保存到 AstrBot 临时目录再发送。
- 想知道有哪些音色，让 AI 调用 `mmx_speech_voices`，它会列出音色并标注性别。

**对应 LLM 工具**：`mmx_speech_synthesize`。参数名是驼峰写法：`text`、`voice`、`model`、`speed`、`volume`、`pitch`、`emotion`、`textNormalization`、`latexRead`、`format`、`sampleRate`、`bitrate`、`channels`、`language`、`subtitles`、`pronunciation`（数组）。

### /mmx speech transcribe

**用法**：`/mmx speech transcribe [音频文件] [参数]`，`transcribe` 也可以写成 `recognize`。

把音频转成文字，也能生成 SRT/WebVTT 字幕。音频可以用 `--file` 指定，也可以不写，直接附带或引用一段语音或音频文件（见[图片和音频从哪里来](#图片和音频从哪里来)）。`--file` 可以省略，直接把路径写在后面，但路径和 `--file` 二选一，同时写会报错。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--file` | — | 文件路径 | 取消息或引用消息里的音频 | 音频文件，只能在插件数据目录或 AstrBot 临时目录里，不接受网址 |
| `--model` | — | 文本 | 配置项 `default_transcription_model`（默认 `asr-1.0`） | 转写模型 |
| `--language` | — | 语言标签，如 `zh`、`en`、`zh-CN` | 不写，自动识别 | 语言提示。必须用短横线，`zh_CN` 不行 |
| `--response-format` | — | `json`、`verbose_json`、`srt`、`vtt` | `json` | 结果格式：`json` 是纯文字，`verbose_json` 是详细结果（可含时间戳、说话人），`srt`、`vtt` 是字幕 |
| `--timestamp-level` | — | `sentence` 或 `word` | 不写 | 时间戳粒度：按句或按词；只在详细结果和字幕里有用 |
| `--stream` | — | 开关 | 关闭 | 边转边接收，最后一次性回复；只支持 `json` 格式，不能和 `--out` 一起用 |
| `--out` | — | 相对路径 | 不写 | 把结果存到插件数据目录里的这个文件（同时也会把文件发给你）；规则见[生成的文件去哪里](#生成的文件去哪里) |

**示例**

```
/mmx speech transcribe --language zh
/mmx speech transcribe --file meetings/standup.mp3 --response-format srt
/mmx speech recognize --file interview.wav --response-format verbose_json --timestamp-level word --out transcripts/interview.json
```

第 1 条需要同时附带或引用一段语音。第 2、3 条里的文件要先放进插件数据目录或 AstrBot 临时目录。

**限制与注意**

- 音频文件最大 50 MB，空文件会被拒绝。插件只检查大小；时长（上限 500 秒，来自 AI 工具的说明）和编码由服务器检查。
- 可用的音频格式：MP3、WAV、M4A、FLAC、AAC、Opus、Ogg、AIFF；AMR、Silk、裸 PCM 等格式要先转换。（这份格式清单插件本身不检查，由服务器判定。）
- 结果怎么给你：
  - 默认的 `json` 格式、文字不超过 3500 字：直接回复文字。
  - `verbose_json`、`srt`、`vtt`，或文字超过 3500 字：保存成文件发给你，文件名形如 `mmx_transcript_xxxx.srt`，先放在 AstrBot 临时目录。
  - 写了 `--out`：保存到插件数据目录里的那个路径，并把文件发给你。
  - 保存文件失败时，机器人会说明，同时仍然把文字结果回复出来。
  - 没识别到内容时回复「未识别到语音」。
- 转写是计费请求，出错后插件不会自动重试。出现「转写未完成或状态未确认，请勿自动重复提交计费请求」时，请不要反复重发。
- 音频附件如果是网址，会限时 60 秒下载，不跟随重定向，超过 50 MB 会中止。
- 转写指令只认短横线写法：把参数名写成驼峰（例如 `responseFormat`、`timestampLevel` 的形式）会报「不支持的参数」。
- 参数组合错误会直接回复原因，例如 `--stream` 配 `srt`、`--stream` 配 `--out`。

**对应 LLM 工具**：`mmx_speech_transcribe`。参数名是驼峰写法：`file`、`model`、`language`、`responseFormat`、`timestampLevel`、`stream`、`out`。用法与指令一致：`file` 同样不接受网址，没有 `file` 时同样会取消息里的音频，`out` 同样只能写到插件数据目录里。

## /mmx music

**用法**：`/mmx music <提示词> [--lyrics <歌词>] [--format <mp3 或 wav>]`

按文字描述生成音乐，作为语音消息发送，可以附带歌词。生成可能要几分钟，开始时机器人会先回复「音乐生成中」。建议先登录。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--lyrics` | — | 文本 | 不写，不带歌词 | 歌词 |
| `--format` | — | `mp3` 或 `wav` | `mp3` | 音频格式，写别的会回复「--format 只支持 mp3 或 wav」 |

提示词只能直接写在指令后面，没有专门用来写提示词的参数。

**示例**

```
/mmx music 轻快的夏日民谣
/mmx music 轻快的夏日民谣 --lyrics 阳光洒在海面上 微风吹过你脸庞 --format wav
```

**限制与注意**

- 歌词可以含空格，不用加引号。歌词里的换行在不加引号时会变成空格；想保留换行，用双引号把整段歌词括起来。
- 单次生成最长等待 10 分钟；生成好的音频最大下载 200 MiB。
- 音频先保存到 AstrBot 临时目录再发送；下载失败时回复音频链接。
- 凭据：优先用登录账号，没登录时改用配置的 API Key，服务器不接受时会提示先 `/mmx login`。

**对应 LLM 工具**：`mmx_generate_music`。参数：`prompt`、`lyrics`、`format`。差别：工具遇到不是 `mp3`、`wav` 的格式时会悄悄改用 `mp3`，指令则直接报错。

## /mmx sfx

**用法**：`/mmx sfx <音效描述>`

按文字描述生成音效，作为语音消息发送。生成可能要几分钟，开始时机器人会先回复「音效生成中」。建议先登录。

此指令没有参数：指令后面的整段文字都当作音效描述。音效固定为 mp3 格式。

**示例**

```
/mmx sfx 雨天打雷的声音
```

**限制与注意**

- 单次生成最长等待 10 分钟；生成好的音频最大下载 200 MiB。
- 音频先保存到 AstrBot 临时目录再发送；下载失败时回复音频链接。
- 凭据：优先用登录账号，没登录时改用配置的 API Key。

**对应 LLM 工具**：`mmx_generate_sound_effect`。参数：`prompt`。

## /mmx search

**用法**：`/mmx search <查询>`

用 MiniMax 联网搜索，回复最多 5 条结果。每条包含标题（最多 80 字）、链接和摘要（最多 180 字）。没有结果时回复「未找到相关结果」。

此指令没有参数。

**示例**

```
/mmx search 今天天气怎么样
```

**对应 LLM 工具**：`mmx_web_search`。差别：工具的参数名是 `q`，并把搜索接口的原始结果交给 AI，不做 5 条的截断。

## /mmx vision

**用法**：`/mmx vision [问题]`

看图说话：分析图片并回复描述。需要在同一条消息里附带图片，或引用一条带图片的消息，只取第一张（见[图片和音频从哪里来](#图片和音频从哪里来)）。指令后面写的文字是你对图片的提问；不写则让模型描述这张图。

此指令没有参数。

**示例**

```
/mmx vision 描述这张图片里有什么
```

**限制与注意**

- 没找到图片时回复让你附带或引用图片；找到了但读不出来时，提示改为直接发送图片。
- 回复前会去掉结果里的 HTML 标签。

**对应 LLM 工具**：`mmx_describe_image`。参数：`image`（链接或目录内路径）、`fileId`（已上传文件的 ID，两者二选一）、`prompt`。差别：指令不支持 `fileId`；工具读取链接图片时，超过 50 MB 会被拒绝。

## /mmx search-image

**用法**：`/mmx search-image <关键词> [--n <数量>]`

搜真实的网络图片并发送，比如地点、产品的照片。图片先下载到 AstrBot 临时目录，再逐张发送。建议先登录。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--n` | — | 整数 1 到 5 | `3` | 发送几张图片，超出范围会回复「--n 必须在 1 到 5 之间」 |

关键词可以含空格。

**示例**

```
/mmx search-image 柴犬
/mmx search-image 富士山 日出 --n 5
```

**限制与注意**

- 单次最长等待 10 分钟；每张图片最大下载 200 MiB。
- 没搜到时回复「未找到相关图片」；全部下载失败时，回复图片链接让你自己打开。
- 凭据：优先用登录账号，没登录时改用配置的 API Key，服务器不接受时会提示先 `/mmx login`。

**对应 LLM 工具**：`mmx_search_images`。参数：`query`、`count`。差别：`count` 对应指令的 `--n`，范围同样是 1 到 5；超出范围时工具会取 1 到 5 之间最接近的值，指令则直接报错。

## /mmx reverse-image

**用法**：`/mmx reverse-image [图片链接或路径]`

以图搜图：找图片的出处和相似网页。图片有两种给法：在指令后面写图片链接或目录内的文件路径；或者什么都不写，在这条消息里附带图片，或引用一条带图片的消息。建议先登录。

此指令没有参数。

**示例**

```
/mmx reverse-image https://example.com/photo.jpg
/mmx reverse-image
```

第 2 条需要同时附带或引用一张图片。

**限制与注意**

- 回复前 3 条结果，每条包含标题、来源、链接、缩略图、摘要（摘要最多 200 字）。没有结果时回复「未找到相似图片或出处」。
- 本地图片只接受 `.png`、`.jpg`、`.jpeg`、`.gif`、`.webp`、`.bmp`；会先上传，文件最大 500 MiB。手写的路径必须在插件数据目录或 AstrBot 临时目录内。
- 凭据：优先用登录账号，没登录时改用配置的 API Key，服务器不接受时会提示先 `/mmx login`。

**对应 LLM 工具**：`mmx_image_reverse_search`。参数：`image`（链接或目录内路径，必填）。差别：工具不会自动取消息里的图片，结果最多返回 10 条。

## /mmx file

**用法**：`/mmx file upload|list|delete ...`，具体写法见下面三个子指令。

管理 MiniMax 存储里的文件。需要管理员；对应的 AI 工具也只有管理员能用。子指令名不区分大小写。写错子指令时回复用法；只发 `/mmx file` 也回复用法。

### /mmx file upload

**用法**：`/mmx file upload --file <路径> [--purpose <用途>]`

把本地文件传到 MiniMax 存储。成功时回复 `file_id`、文件名、用途和大小。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--file` | — | 文件路径 | — | 要上传的文件，必填。`--file` 也可以省略，直接把路径写在后面 |
| `--purpose` | — | 文本 | `retrieval` | 用途，例如 `retrieval`、`vision` |

**示例**

```
/mmx file upload --file files/manual.pdf
/mmx file upload --file photos/cat.png --purpose vision
```

**限制与注意**

- 路径只能是插件数据目录或 AstrBot 临时目录里的文件，否则回复允许的目录列表。文件不存在时回复「文件不存在」。
- 没写路径时回复「upload 需要 --file <路径>」。

**对应 LLM 工具**：`mmx_file_upload`（`file`、`purpose`），仅管理员。

### /mmx file list

**用法**：`/mmx file list`

列出已上传的文件，每行是 `file_id | 文件名 | 用途 | 大小`，最多显示 20 个，超出的会说明还有多少个未显示。没有文件时回复「暂无已上传文件」。

此子指令没有参数；后面多写内容会回复「list 不需要额外参数」。

**对应 LLM 工具**：`mmx_file_list`，仅管理员。

### /mmx file delete

**用法**：`/mmx file delete --file-id <id>`

删除一个已上传的文件，成功时回复「文件已删除」。

**参数**

| 参数 | 别名 | 取值/类型 | 默认 | 说明 |
|------|------|------|------|------|
| `--file-id` | `--fileId` | 数字 | — | 要删除的文件 ID，必填，可以从 `/mmx file list` 里看。也可以省略参数名，直接把 ID 写在后面 |

**示例**

```
/mmx file delete --file-id 123456789
```

**限制与注意**

- ID 必须是数字，写成别的会回复「文件操作失败: file_id 必须是数字」。
- 没写 ID 时回复「delete 需要 --file-id <id>」。

**对应 LLM 工具**：`mmx_file_delete`（`fileId`），仅管理员。

## /mmx quota

**用法**：`/mmx quota`，或 `/mmx quota <序号>`，或 `/mmx quota page <页码>`

查询 MiniMax 额度。不带参数时分页显示所有 Key 的额度，每页最多 3 个；只有一个 Key（包括登录后自动获取的 Key）时直接显示这一个。Key 只显示前 4 位和后 4 位。

每个 Key 下按模型列出额度：

- 非视频模型显示当前周期已用百分比；视频模型显示「已用 / 剩余（总数）」次数。
- 当前周期的名称随套餐变化，例如「5小时额度」；每周额度单独显示为「周额度」。
- 有重置时间的会写「（X小时Y分钟后重置）」；每周额度不限量显示「∞」；不在当前套餐的显示「不在当前套餐中」；有加成的会补一句加成后的剩余百分比。
- 以 `sk-api-` 开头的账户余额型 Key 没有按模型额度，改为显示可用余额、现金、代金券、信用额度、欠费，以及余额提醒的开关。
- 查询失败或没有额度信息时，这个 Key 下显示「查询失败或无额度信息」。

此指令没有以 `--` 开头的参数。

**示例**

```
/mmx quota
```

**对应 LLM 工具**：`mmx_check_quota`。没有参数，会把配置的所有 Key 的额度合并成一份汇总；指令则是逐个 Key 分别显示。

### /mmx quota 序号

**用法**：`/mmx quota <序号>`

只看第几个 Key 的额度，序号从 1 开始，就是不带参数时每个 Key 前面方括号里的数字。

**示例**

```
/mmx quota 2
```

**限制与注意**

- 序号超出范围时回复「Key 序号 N 不存在，共 M 个 Key」；不是数字时回复「序号无效」。

### /mmx quota page

**用法**：`/mmx quota page <页码>`，`page` 也可以简写为 `p`。

多个 Key 时翻页查看，页码从 1 开始。不写页码等于第 1 页。多个 Key 时，回复末尾会给出「下一页」「上一页」的写法。

**示例**

```
/mmx quota page 2
/mmx quota p 2
```

**限制与注意**

- 每页最多 3 个 Key。
- 页码必须是大于 0 的数字；超出总页数时回复「页码 N 不存在，共 X 页」；`page` 后面多写内容会回复「用法: /mmx quota page <页码>」。

## 附录：参数别名对照

下表列出同时支持短横线写法和驼峰写法的参数，两种写法效果完全相同。其他参数只有一种写法，没有列在这里。

| 指令 | 短横线写法 | 驼峰写法 |
|------|------|------|
| `/mmx image` | `--aspect-ratio` | `--aspectRatio` |
| `/mmx image` | `--prompt-optimizer` | `--promptOptimizer` |
| `/mmx image` | `--aigc-watermark` | `--aigcWatermark` |
| `/mmx image` | `--subject-ref` | `--subjectRef` |
| `/mmx image` | `--response-format` | `--responseFormat` |
| `/mmx video` | `--first-frame` | `--firstFrame` |
| `/mmx video` | `--last-frame` | `--lastFrame` |
| `/mmx video` | `--subject-image` | `--subjectImage` |
| `/mmx video` | `--callback-url` | `--callbackUrl` |
| `/mmx video` | `--poll-interval` | `--pollInterval` |
| `/mmx speech` | `--text-normalization` | `--textNormalization` |
| `/mmx speech` | `--latex-read` | `--latexRead` |
| `/mmx speech` | `--sample-rate` | `--sampleRate` |
| `/mmx file delete` | `--file-id` | `--fileId` |

另外，`/mmx video` 的 `--no-wait` 还有同义写法 `--async`。`/mmx speech transcribe` 的 `--response-format`、`--timestamp-level` 只支持短横线写法。
