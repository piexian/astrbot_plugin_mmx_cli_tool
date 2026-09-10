"""MiniMax 音色性别推断 — 供 AI 选择音色时参考。"""

from __future__ import annotations

import re

# 词首边界匹配；女性关键词优先于男性（"woman" 含 "man"）。
_FEMALE_KEYWORDS = (
    "female", "girl", "woman", "women", "lady", "miss", "queen", "princess",
    "maid", "bestie", "sister", "auntie", "antie", "goddess", "hostess",
    "heroine", "schoolgirl", "shaonv", "yujie", "tianmei", "mengmei",
    "xuemei", "xuejie", "jiejie", "meimei", "xiaoling",
)
_MALE_KEYWORDS = (
    "male", "boy", "man", "gentleman", "bloke", "knight", "king", "prince",
    "butler", "uncle", "brother", "nanyou", "didi", "xuedi", "xiongzhang",
    "shaoye", "gege", "guy", "husband", "godfather", "santa", "playboy",
    "sorcerer",
)

_FEMALE_RE = re.compile("|".join(rf"(?<![a-z]){kw}" for kw in _FEMALE_KEYWORDS))
_MALE_RE = re.compile("|".join(rf"(?<![a-z]){kw}" for kw in _MALE_KEYWORDS))


def infer_voice_gender(voice_id: object, voice_name: object = "") -> str | None:
    """根据 voice_id / voice_name 推断性别：'male'、'female' 或 None（未知/中性角色）。"""
    name = str(voice_name or "")
    if "女" in name:
        return "female"
    if "男" in name:
        return "male"
    text = f"{voice_id or ''} {name}".lower()
    if _FEMALE_RE.search(text):
        return "female"
    if _MALE_RE.search(text):
        return "male"
    return None
