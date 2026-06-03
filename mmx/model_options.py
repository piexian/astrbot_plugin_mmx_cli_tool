"""MiniMax model defaults and validation options."""

from __future__ import annotations

DEFAULT_MUSIC_MODEL = "music-2.6"
DEFAULT_MUSIC_COVER_MODEL = "music-cover"

MUSIC_MODELS = ("music-2.6", "music-2.5+", "music-2.5")
MUSIC_COVER_MODELS = ("music-cover",)


def model_options_text(models: tuple[str, ...]) -> str:
    return ", ".join(models)

