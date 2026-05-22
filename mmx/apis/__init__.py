"""MiniMax API 模块。"""

from .image import ImageAPI
from .video import VideoAPI
from .music import MusicAPI
from .search import SearchAPI
from .vision import VisionAPI
from .quota import QuotaAPI
from .speech import SpeechAPI

__all__ = ["ImageAPI", "VideoAPI", "MusicAPI", "SearchAPI", "VisionAPI", "QuotaAPI", "SpeechAPI"]
