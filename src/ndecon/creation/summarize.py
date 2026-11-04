"""章节摘要闭环：把"写后摘要"从 panel 私有方法下沉为公共模块。

修的是两个地基问题：

- B3：原来只有 ``panel/server.py`` 里的私有方法能产出摘要，CLI 与其它出口拿不到，
  "已写章节"这一块永远进不了索引，``context`` 因此长期为空。这里一份实现、多个出口。
- B9：原来用内置 ``hash()``，受 ``PYTHONHASHSEED`` 影响，同一份正文在两个进程里
  得到不同的 ``chapter_hash``，摘要证据链与幂等判断全部失效。改用 SHA-256 前缀。

设计边界：
1. 本模块只产出**结构化摘要**（gist + 情节点 + 角色名），不产出、不保存正文；
2. 摘要失败一律返回 ``None``，绝不让摘要故障污染已经生成的正文；
3. 不支持的 writer 类型返回 ``None``，不静默降级成别的模型（否则证据链就断了）。
"""

from __future__ import annotations

import hashlib

from ndecon.creation.chapters import FakeChapterWriter, OpenAICompatChapterWriter
from ndecon.domain.models import ChapterSummary
from ndecon.providers.errors import ProviderError
from ndecon.providers.fake import FakeProvider
from ndecon.providers.openai_compat import OpenAICompatProvider

# 正文哈希取 SHA-256 前 16 位：足够区分同项目内的章节版本，又不至于把 run record 撑大。
CHAPTER_HASH_LEN = 16


def content_hash(text: str) -> str:
    """正文稳定哈希：跨进程、跨机器一致（B9）。

    不用内置 ``hash()``：它在 str 上加了随机种子，重启一次就变，任何靠它做的
    缓存命中、版本去重、证据回指都会在下次启动时错位。
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:CHAPTER_HASH_LEN]


def provider_for_writer(writer: object) -> object | None:
    """从章节生成器反查它背后的摘要 Provider。

    只认两种已知 writer：Fake 走离线 Provider（确定性、无网络），
    OpenAICompat 走已配置 key 的那一个。其它类型返回 None——
    宁可这一章没有摘要（用户看得见"缺摘要"），也不要拿别的模型伪造摘要。
    """
    if isinstance(writer, FakeChapterWriter):
        return FakeProvider()
    if isinstance(writer, OpenAICompatChapterWriter):
        inner = getattr(writer, "_provider", None)
        if isinstance(inner, OpenAICompatProvider):
            return inner
        return None
    return None


def summarize_chapter(
    writer: object,
    order: int,
    title: str,
    chapter_text: str,
    *,
    chapter_hash: str | None = None,
) -> ChapterSummary | None:
    """产出本章结构化摘要；失败返回 None（不抛错、不影响正文落库）。

    ``chapter_hash`` 缺省时按正文算稳定哈希；显式传入是为了让调用方能复用
    已经算过的值（run record 里记的必须与实际摘要用的完全一致）。
    """
    provider = provider_for_writer(writer)
    if provider is None:
        return None
    digest = chapter_hash or content_hash(chapter_text)
    try:
        return provider.summarize_chapter(order, title, chapter_text, digest)  # type: ignore[attr-defined]
    except ProviderError:
        return None


def mechanical_summary(
    order: int,
    title: str,
    chapter_text: str,
    *,
    core_event: str = "",
) -> ChapterSummary:
    """摘要的机械兜底：不调模型，直接从正文与细纲拼一个最低限度摘要。

    为什么必须有这个兜底：摘要不是"附带的元数据"，它是 ``build_context_pack``
    里 ``previous_summary`` 的唯一来源 —— 也就是模型"记得上一章发生了什么"的
    唯一通道。实测一次 10 章连写里模型摘要失败了 5 章（ch001/ch006/ch007/
    ch008/ch009），那 5 章仍被标成 committed，于是后续章节的上下文包直接断档，
    模型开始遗忘前情，而闸门完全不检查这一项。

    原来"失败就返回 None、正文照常提交"这个判断是错的：它让遗忘静默发生。
    现在的策略是三层 —— 模型摘要成功最好；失败则用这里的机械摘要保证连续性；
    连机械摘要都拿不到（正文为空）才真的缺。

    机械摘要不含 plot_points（没有证据定位就不能伪造证据），gist 取"细纲核心
    事件 + 正文结尾一句"，足够让下一章知道"上一章写到哪了"。
    """
    text = chapter_text.strip()
    # 结尾一句：优先取最后一个非空行，截到 80 字。它是"上一章停在哪"的最直接线索。
    tail = ""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line:
            tail = line[:80]
            break
    pieces = [part for part in (core_event.strip(), tail) if part]
    gist = "；".join(pieces)[:200] if pieces else f"第 {order} 章 {title}"
    return ChapterSummary(
        model_id="rule:mechanical-summary",
        prompt_version="ms-v1",
        chapter_id=f"CH{order:04d}",
        order=order,
        title=title,
        gist=gist,
        plot_points=[],
        characters=[],
    )
