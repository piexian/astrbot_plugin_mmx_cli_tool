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
    """视频生成端点（V1 legacy）。"""
    return f"{base}/v1/video_generation"


def video_gen_v2_endpoint(base: str) -> str:
    """视频生成端点（V2，MiniMax-H3）。"""
    return f"{base}/v2/video_generation"


def video_task_endpoint(base: str, task_id: str) -> str:
    """视频任务查询端点（V1 legacy）。"""
    return f"{base}/v1/query/video_generation?task_id={task_id}"


def video_task_v2_endpoint(base: str, task_id: str) -> str:
    """视频任务查询端点（V2，MiniMax-H3）。"""
    return f"{base}/v2/query/video_generation/{task_id}"

def music_endpoint(base: str) -> str:
    """音乐生成端点。"""
    return f"{base}/v1/music_generation"


def search_endpoint(base: str) -> str:
    """联网搜索端点。"""
    return f"{base}/v1/coding_plan/search"


def vision_endpoint(base: str) -> str:
    """视觉理解（VLM）端点。"""
    return f"{base}/v1/coding_plan/vlm"


def _api_quota_host(base: str) -> str:
    host = (
        "https://api.minimaxi.com"
        if "minimaxi.com" in base
        else "https://api.minimax.io"
    )
    return host


def _www_quota_host(base: str) -> str:
    host = (
        "https://www.minimaxi.com"
        if "minimaxi.com" in base
        else "https://www.minimax.io"
    )
    return host


def quota_endpoint(base: str) -> str:
    """额度查询端点（同步 mmx-cli 1.0.16 的 Token Plan API）。"""
    return f"{_api_quota_host(base)}/v1/api/openplatform/coding_plan/remains"


def legacy_quota_endpoint(base: str) -> str:
    """旧版额度查询端点，用于兼容仍返回旧路径的部署。"""
    return f"{_www_quota_host(base)}/v1/token_plan/remains"


def legacy_api_quota_endpoint(base: str) -> str:
    """插件早期使用的旧版 api 子域额度端点。"""
    return f"{_api_quota_host(base)}/v1/token_plan/remains"


def quota_endpoints(base: str) -> list[str]:
    """按优先级返回额度查询候选端点。"""
    return [
        quota_endpoint(base),
        legacy_quota_endpoint(base),
        legacy_api_quota_endpoint(base),
    ]


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
