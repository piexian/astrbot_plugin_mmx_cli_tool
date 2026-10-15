# 插件协作 API v1

## 原生发现与生命周期

按 metadata 名称发现，不按目录导入内部模块：

```python
meta = context.get_registered_star("astrbot_plugin_mmx_cli_tool")
if meta is None:
    raise RuntimeError("missing")
if not meta.activated:
    raise RuntimeError("disabled")
if meta.star_cls is None:
    raise RuntimeError("no_instance")
getter = getattr(meta.star_cls, "get_service", None)
if not callable(getter):
    raise RuntimeError("unsupported_api")
service = getter(api_version=1)  # 不兼容版本异常的 code=unsupported_version
status = service.get_status()
```

`get_service(api_version: int = 1)` 同次加载返回同一个门面，仅接受真正的整数 `1`（拒绝 `True`）。构造后即可取得；轻量 `initialize()` 完成后，有 Key 为 `ready`，无 Key 为 `unavailable` / `missing_api_key`。消费者不要在自己的 `initialize()` 阻塞等待其他插件，应按需在插件加载/卸载钩子重新绑定。

- `get_status() -> dict`：`api_version, instance_id, state, ready, reason`；state 为 `initializing/ready/unavailable/closing/closed`。
- `capabilities() -> dict`：`api_version=1, features=[...]`。
- `await wait_ready(timeout=None) -> dict`：成功返回状态快照，超时抛 `TimeoutError`，关闭抛带 `code=service_closed` 的 `RuntimeError` 子类；调用者应显式设置有界超时。

状态和能力是纯内存快照，不读文件/KV、不轮转 Key、不联网、不返回凭据或客户端。ready 不保证远端余额、权限、网络或模型可用。能力声明表示实现支持，不表示账号授权。

features：`image.generate/video.generate/video.tasks/audio.synthesize/audio.transcribe/audio.voices/web.search/image.describe/file.upload/file.list/file.delete/quota.query`。

卸载开始立即拒收。关闭不可逆，旧服务不能被重新初始化；重载后取得新 `instance_id`。消费者不得关闭共享客户端。卸载会取消已受理的 SDK 调用并等待这些调用退出，不等待消费者后续逻辑。**不代表远端取消或退款**，没有公开 cancel 方法。关闭后仅状态与能力仍可查询。

## 真实业务签名

下列方法均为 async；结果为 JSON 可序列化字典，不会发送消息或唤醒 Agent。

```python
generate_image(prompt, *, subject_ref=None, **options) -> dict
generate_video(prompt, *, subject_image=None, **options) -> dict
get_video_task(task_id, model=None) -> dict
wait_video_task(task_id, *, poll_interval=None, timeout=None, model=None) -> dict
download_video(file_id, *, out=None, video_url=None) -> dict
synthesize_speech(text, **options) -> dict
list_voices() -> dict
transcribe(file, *, model=None, response_format="json", language=None,
           timestamp_level=None, stream=False, out=None) -> dict
search(query) -> dict
describe_image(image=None, prompt="Describe the image.", file_id=None) -> dict
get_quota() -> dict
upload_file(file_path, purpose="retrieval") -> dict
list_files() -> dict
delete_file(file_id) -> dict
```

`**options` 仅接受以下 snake_case 参数，不接受任意端点、client 或 trusted-local 开关。模型省略/空值沿用插件配置；其他默认值如下：

| 方法 | 可选参数（未标默认值的参数缺省为 None） |
|---|---|
| generate_image | model, n=1, aspect_ratio, width, height, response_format="url", prompt_optimizer=True, aigc_watermark=False, subject_reference, seed |
| generate_video | model, first_frame_image, last_frame_image, subject_reference, callback_url, reference_images, reference_videos, reference_audios, duration, ratio |
| synthesize_speech | model, voice（配置默认或 English_expressive_narrator）, speed, volume, pitch, emotion, text_normalization=False, latex_read=False, audio_format="mp3", sample_rate=32000, bitrate=128000, channels=1, language, subtitles=False, pronunciation |

图片与语音合成返回原始 API 字典，不自动保存。视频创建只提交一次、返回原始响应；查询/等待复用既有 API，MiniMax-H3 查询归一化 `task` 字段，等待完成时增加 `video_url`。`wait_video_task` 缺省读取配置轮询间隔和超时，不重新创建。`download_video` 返回 `{ok: True, file_path: ...}`；H3 可传空 file_id 和已查询到的 video_url。

搜索、视觉、音色、文件方法返回原始响应。`get_quota` 查询配置中的所有 Key，返回 `{"accounts": [{"index": 1, "data": 原始额度响应}, ...]}`，不返回 Key；失败不吞成空成功。同步业务保留原异常和上游错误码，不自动重试计费请求。

## 输入与输出边界

- 本地图片、音频、上传文件仅可读插件既有数据目录和配置允许的 AstrBot 临时目录，复用既有解析与符号链接约束；不把 SDK 参数当作受信任聊天附件。
- 转写 `file` 不接受手写远端 URL；`out` 必须为插件数据目录内相对路径。字幕/详细 JSON/长文本缺省保存临时目录。结果为 `{ok, response_format, data, file_path?, warning?}`；保存失败保留文字，不重做请求。
- 视频 `out` 仅允许数据目录内相对文件路径，缺省随机命名保存临时目录。
- 首尾帧和角色参考互斥；H3 不支持角色一致性，参考媒体/duration/ratio 仅用于 H3；参考视频/音频仅 URL 或 `mm_file://`。
- 文件 SDK 是受信任插件协作接口，不代替消费者自己的用户权限检查。原命令/Tool 的管理员限制未改变。
