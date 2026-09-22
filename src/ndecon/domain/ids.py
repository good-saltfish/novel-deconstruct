"""稳定 ID 与内容指纹。

- 章节/段落 ID 只依赖章号与段号，不依赖易变的章题；
- 内容指纹使用归一化后的 SHA-256，供"原文未变则产物可缓存/可校验"；
- 证据引用靠 quote + 偏移 + hash 回到原文，而不是只存一个章号。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata


def chapter_id(order: int) -> str:
    """由章节序号生成固定宽度章节 ID，例如 1 -> 'CH0001'。"""
    if order < 1:
        raise ValueError("章节序号必须从 1 开始")
    return f"CH{order:04d}"


def normalize_for_hash(text: str) -> str:
    """归一化文本以计算指纹：NFKC、统一换行、逐行折叠空白、压缩多余空行。

    仅用于"内容是否变化"的判定；原文本身不被改写存储。
    """
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t　]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def content_hash(text: str) -> str:
    """计算归一化文本的 SHA-256 十六进制指纹。"""
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()
