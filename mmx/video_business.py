"""Shared video mode checks before resolving inputs or submitting generation."""

from __future__ import annotations


class VideoInputError(ValueError):
    def __init__(self, error: str, hint: str, example: dict):
        super().__init__(error)
        self.payload = {
            "ok": False,
            "error": error,
            "hint": hint,
            "example": example,
            "docs": "https://platform.minimaxi.com/docs/api-reference/video-generation",
        }


def validate_video_mode(first_frame, last_frame, subject_image, model) -> None:
    if last_frame and not first_frame:
        raise VideoInputError(
            "SEF 模式需要同时提供 firstFrame 和 lastFrame",
            "请同时提供 firstFrame（起始帧）和 lastFrame（结束帧）来启用首尾帧插值模式",
            {
                "prompt": "Walk forward",
                "firstFrame": "start.jpg",
                "lastFrame": "end.jpg",
            },
        )
    if (first_frame or last_frame) and subject_image:
        raise VideoInputError(
            "firstFrame/lastFrame 与 subjectImage 不能同时使用",
            "首尾帧插值（SEF）和角色一致性（S2V）是两种独立模式，请选择其一",
            {
                "prompt": "A person walking",
                "firstFrame": "start.jpg",
                "lastFrame": "end.jpg",
            },
        )
    if model and "Fast" in model and not first_frame:
        raise VideoInputError(
            f"{model} 模型需要提供 firstFrame",
            "Fast 模型属于 I2V 模式，必须提供 firstFrame（起始帧图片）",
            {"prompt": "A cat running", "firstFrame": "cat.jpg", "model": model},
        )


def validate_h3_subject(model, subject_image) -> None:
    if model == "MiniMax-H3" and subject_image:
        raise VideoInputError(
            "MiniMax-H3 不支持 subjectImage",
            "V2 模型不支持角色一致性（S2V）。请改用 referenceImages 提供参考图片，或移除 subjectImage。",
            {"prompt": "A detective walking", "model": "MiniMax-H3"},
        )
