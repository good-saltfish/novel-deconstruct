"""中文轻量分词：NFKC 归一 + 字符类切分 + 汉字 bigram 滑窗。

设计原则（ADR-0002 的确定性红线在检索层的延续）：
- 零模型、零词典文件、零网络：同输入永远同输出，可逐位重算；
- 连续汉字按字两两滑窗（bigram），词边界无需词典也能匹配"路灯/灯结/结阵"这类片段；
- 英文/数字串整体小写保留，全角字符经 NFKC 归一（全角ＡＢＣ/１２３ → 半角）；
- 标点与空白不作为 token，仅充当片段分隔。

受控词表（基调/主题标签）本身是双字词，bigram 命中后由 BM25 层统一加权，
不在本文件做特殊切分，保持分词器单一职责。
"""

from __future__ import annotations

import re
import unicodedata

# 一个"片段"：要么是一串连续 CJK 统一表意文字，要么是一串连续英文字母/数字
_RUN_RE = re.compile(r"[一-鿿]+|[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]")


def normalize_text(text: str) -> str:
    """对文本做 NFKC 归一（全角转半角、兼容字符合并），不改变字符语义。"""
    return unicodedata.normalize("NFKC", text)


def _cjk_bigrams(run: str) -> list[str]:
    """把一串连续汉字切成 bigram；单字串退化为单字 token，避免短词丢失。"""
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def tokenize(text: str) -> list[str]:
    """把任意文本切为确定性 token 序列：汉字 bigram + 英文/数字串小写。

    例：「第3章 路灯，ＬＩＧＨＴ」→ ['第', '3', '章', '路灯', 'light']
    （全角 LIGHT 经 NFKC 归一后小写；标点被丢弃。）
    """
    normalized = normalize_text(text).lower()
    tokens: list[str] = []
    for run in _RUN_RE.findall(normalized):
        if _CJK_RE.match(run):
            tokens.extend(_cjk_bigrams(run))
        else:
            tokens.append(run)
    return tokens
