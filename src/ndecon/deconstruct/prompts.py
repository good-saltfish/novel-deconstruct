"""结构化拆书的版本化提示词（bd-p-v1）。

**为什么必须分成"逐章 → 全景"两阶段（而不是一次喂全书）**：
1. 一次喂 40 章原文会超出上下文预算，必然产生幻觉水词；先逐章压缩成
   ChapterDeconstruction，再把这个**我方自己的压缩产物**喂给全景阶段，输入量小一个量级；
2. 全景阶段的输入从此不含任何第三方正文，只含我方 Meta 层的转述/节点——版权红线在物理上成立
   （对比：直接把原文摘要塞进 prompt 时，模型会不由自主地回吐原文句式）。

引用纪律与 mock 模板调研结论一致：现成拆书稿里最长的合法引用是 67 字三句对话，
所以我们把上限写死在 MAX_QUOTE_CHARS（30 字），并且要求"引用必须服务于一句点评"。
"""

from __future__ import annotations

import json

from ndecon.deconstruct.models import MAX_QUOTE_CHARS

PROMPT_VERSION = "bd-p-v1"

# 单章送模型的字符上限：超出即不再拆解并记 warning（不静默截断，避免半章结论伪装成全章）
MAX_CHAPTER_CHARS = 16000

SYSTEM_MESSAGE = f"""你是资深中文网文拆解师，服务对象是一位要写自己书的作者。
任务：把别人已经写成的书写法，拆成可迁移的**零部件**，让作者拿来即用。

绝对禁止（违反即整个输出作废）：
1. 禁止成段摘录原文。一律用第三人称做"情节压缩转述"：谁、为了什么、做了什么、结果如何。
2. 单处直接引用不超过 {MAX_QUOTE_CHARS} 字，且必须紧跟一句"为什么这句有效"的点评。
   允许引用的形态仅限：一句台词、一句规则口号、一句意象性描写。
3. 禁止产出可以直接贴进作者书里的段落（不代笔，只给结构与手法）。
4. 禁止空话套话（"文笔优美""情节紧凑""值得学习"这类不构成信息）。每条都必须带具体所指。

输出格式：只输出一个 JSON 对象，不要 markdown 代码围栏、不要前后多余文字。
输出语言：中文简体。
"""


def _chapter_spec() -> dict:
    """单章拆解的输出结构说明（随 prompt 发送，不参与运行时强校验）。"""
    return {
        "order": "整数，本章章号",
        "title": "本章标题（沿用原标题）",
        "core_event": "本章发生了什么：一段第三人称压缩转述，150-400 字",
        "triangles": [
            {
                "kind": "三角类型：完整/不完整/潜在/抽象/复合/反转/悬念",
                "name": "该三角的一句话命名，如'规则猎杀'",
                "persecutor": "迫害者：谁在施压",
                "victim": "受害者：谁承受后果",
                "savior": "拯救者：谁解围；缺席写'（缺席——为什么缺席）'",
                "effect": "这组配置制造出什么阅读效果（一句话点评）",
            }
        ],
        "end_hook": "章末钩子：本章留下的那根没结束的线",
        "highlights": ["评价/亮点1", "评价/亮点2"],
        "emotion_track": "本章情绪走向，如 '压抑→转机→再压' 或 v/w/ww",
    }


def chapter_user_message(order: int, title: str, chapter_text: str) -> str:
    """组装单章拆解的用户消息（输入是原章正文，输出只要结构化转述）。"""
    return (
        f"待拆解：第 {order} 章《{title}》\n"
        f"正文如下（约 {len(chapter_text)} 字）：\n\n"
        f"----- 正文开始 -----\n{chapter_text}\n----- 正文结束 -----\n\n"
        "请按以下结构输出 3-7 个冲突三角（密度不足 3 个说明你漏看了暗线/规则/自我施压），"
        "highlights 给 3-5 条且每条必须点名具体手法：\n"
        + json.dumps(_chapter_spec(), ensure_ascii=False)
    )


def _chapters_digest(chapters: list[dict]) -> str:
    """把逐章产物压成全景阶段可读的简报（不含任何第三方正文，只含我方转述）。"""
    lines: list[str] = []
    for item in chapters:
        triangles = item.get("triangles") or []
        triangle_text = "；".join(
            f"{t.get('name', '未命名')}（{t.get('kind', '?')}：迫={t.get('persecutor', '?')}"
            f"/受={t.get('victim', '?')}/拯={t.get('savior', '缺席')}）"
            for t in triangles
        )
        lines.append(
            f"第{item.get('order', '?')}章《{item.get('title', '')}》\n"
            f"  核心：{item.get('core_event', '')}\n"
            f"  钩子：{item.get('end_hook', '')}\n"
            f"  情绪：{item.get('emotion_track', '')}\n"
            f"  三角（{len(triangles)}）：{triangle_text}"
        )
    return "\n".join(lines)


def _panorama_spec() -> dict:
    """全景拆书的输出结构说明。"""
    return {
        "genre": "题材/类型",
        "tags": ["标签1", "标签2"],
        "mainline_open": "明线：读者看得见的主目标与推进序列",
        "mainline_hidden": "暗线：作者埋在世界背后的真相/长远局",
        "key_setting": {
            "worldview": "世界的运行规则与最特殊的那个设定",
            "golden_finger": "金手指：形态/收益/**限制与代价**/成长路径（限制代价必答）",
            "power_system": "力量体系与等级阶梯（给出阶梯档位）",
        },
        "main_characters": ["角色名——在结构里的功能一句话"],
        "plot_nodes": [
            {
                "index": 1,
                "node": "节点名（如'开局立威'）",
                "chapters": "覆盖章号范围，如 '1-5'",
                "core_event": "该节点推进了什么",
                "emotion_peak": "该节点的情绪峰值",
            }
        ],
        "core_conflict": "主冲突是什么，以及它怎样逐级升级",
        "driving_force": "驱动力：到底是什么让读者忍不住翻下一章",
        "one_line_outline": {"1": "第1章一句话", "2": "第2章一句话"},
        "highlights": ["亮点提炼1", "亮点提炼2"],
        "reusable_techniques": ["可复用手法1（写'手法'不是写'情节'）"],
    }


def panorama_user_message(
    book_title: str,
    range_label: str,
    chapters: list[dict],
    total_chars: int = 0,
) -> str:
    """组装全景拆书消息：输入只有逐章转述简报，不再含原文。"""
    node_hint = max(2, min(len(chapters) // 2, 30))
    return (
        f"《{book_title}》{range_label}｜共 {len(chapters)} 章、约 {total_chars} 字去空白。\n\n"
        "下面是我已经完成的逐章拆解（第三方正文不再提供，请只依据这份转述做聚合）：\n\n"
        f"{_chapters_digest(chapters)}\n\n"
        f"请在上面的基础上产出全书骨架。硬性要求：\n"
        f"- plot_nodes 给 {node_hint} 个左右（按'节点'聚合，不是按章罗列）；\n"
        f"- reusable_techniques 必须是**可迁移的手法**（例：'每次胜利都同步失去一样筹码'），"
        f"严禁写成情节梗概；\n"
        f"- golden_finger 必须答出限制与代价，答不出就写'本书未交代'，不许编；\n"
        f"- one_line_outline 覆盖每一章，键为章号字符串。\n\n"
        + json.dumps(_panorama_spec(), ensure_ascii=False)
    )
