"""MiniMax API 端点 URL 构建器。"""

# API 区域 → 基础地址
REGIONS: dict[str, str] = {
    "global": "https://api.minimax.io",
    "cn": "https://api.minimaxi.com",
}


def chat_endpoint(base: str) -> str:
    """对话端点（Anthropic 兼容格式）。"""
    return f"{base}/anthropic/v1/messages"


def speech_endpoint(base: str) -> str:
    """语音合成端点。"""
    return f"{base}/v1/t2a_v2"


def voices_endpoint(base: str) -> str:
    """音色列表端点。"""
    return f"{base}/v1/get_voice"


def image_endpoint(base: str) -> str:
    """图片生成端点。"""
    return f"{base}/v1/image_generation"


def video_gen_endpoint(base: str) -> str:
    """视频生成端点。"""
    return f"{base}/v1/video_generation"


def video_task_endpoint(base: str, task_id: str) -> str:
    """视频任务查询端点。"""
    return f"{base}/v1/query/video_generation?task_id={task_id}"


def music_endpoint(base: str) -> str:
    """音乐生成端点。"""
    return f"{base}/v1/music_generation"


def search_endpoint(base: str) -> str:
    """联网搜索端点。"""
    return f"{base}/v1/coding_plan/search"


def vision_endpoint(base: str) -> str:
    """视觉理解（VLM）端点。"""
    return f"{base}/v1/coding_plan/vlm"


def quota_endpoint(base: str) -> str:
    """额度查询端点（使用 api 子域）。"""
    host = (
        "https://api.minimaxi.com"
        if "minimaxi.com" in base
        else "https://api.minimax.io"
    )
    return f"{host}/v1/token_plan/remains"


def file_upload_endpoint(base: str) -> str:
    """文件上传端点。"""
    return f"{base}/v1/files"


def file_list_endpoint(base: str) -> str:
    """文件列表端点。"""
    return f"{base}/v1/files/list"


def file_delete_endpoint(base: str) -> str:
    """文件删除端点。"""
    return f"{base}/v1/files/delete"


def file_retrieve_endpoint(base: str, file_id: str) -> str:
    """文件信息检索端点。"""
    return f"{base}/v1/files/retrieve?file_id={file_id}"
