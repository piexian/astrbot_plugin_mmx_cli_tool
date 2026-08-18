"""MiniMax model defaults and validation options."""

from __future__ import annotations

DEFAULT_MUSIC_MODEL = "music-3.0"
DEFAULT_MUSIC_COVER_MODEL = "music-cover"

# 对齐 mmx-cli 1.0.19
MUSIC_MODELS = ("music-3.0", "music-2.6", "music-2.6-free", "music-2.5+", "music-2.5")
MUSIC_COVER_MODELS = ("music-cover", "music-cover-free")


def model_options_text(models: tuple[str, ...]) -> str:
    return ", ".join(models)

