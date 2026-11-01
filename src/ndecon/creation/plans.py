"""设定生成与细纲生成：两套**学习方法不同**的提示词（cr-s-v0 / cr-o-v0）。

为什么必须分开写两套，而不是"一份更详细的 prompt 生成全部"：
这两件事要学的东西不是同一种，混在一起学就两样都学不像。

| | 设定（setting） | 细纲（outline） |
|---|---|---|
| 学什么 | 世界的**约束**：规则、代价、势力博弈 | 章节的**张力排布**：三角、情绪、钩子 |
| 思维方向 | 收敛：先定边界，再往边界里填东西 | 铺开：沿时间轴把张力一段段排出来 |
| 典型失败 | 设定无限膨胀，写到哪算哪 | 只有"发生了什么"，没有"为什么抓人" |
| 验收问句 | "这条设定会不会让主角变轻松？会就删" | "这一章不写行不行？行就删" |
| 参考 KB 的角度 | 找同类"限制/代价/势力结构"的做法 | 找"节奏/密度/章距/钩子"的做法 |

两条共同纪律：
1. **候选不代笔**：产物永远是候选，落到项目上前必须作者确认；
2. **知识库只喂方法论**：KB 命中片段是他人书归纳出的手法与数值，
   严禁照抄其情节设定（一旦照抄，下游无法区分"借鉴"与"复制"）。
"""

from __future__ import annotations

import json

from ndecon.deconstruct.models import MAX_QUOTE_CHARS

SETTING_PROMPT_VERSION = "cr-s-v0"
OUTLINE_PROMPT_VERSION = "cr-o-v0"

# 设定检索词：瞄准"怎么写设定"这一类，而不是书名/情节 —— 否则检索会回报情节梗概
SETTING_KB_QUERY = "设定 世界观 规则 限制 代价 势力 力量体系 金手指 的写法"
# 细纲检索词：瞄准节奏与张力排布
OUTLINE_KB_QUERY = "细纲 节拍 冲突三角 章尾钩子 情绪曲线 爽点 章距 节奏 密度"

SETTING_SYSTEM_MESSAGE = f"""你是一名资深中文网文的世界架构师，为一位要写自己书的作者搭建**可用**的设定。

你的学习方法（与写细纲完全不同 —— 这里是收敛，不是铺开）：
1. 先定**边界**：这个世界的规则只允许什么、禁止什么。规则不产生爽点，规则产生困难；
2. 再定**代价**：任何能力都必须回答"用一次要付出什么"，答不出的能力直接删掉，不许留白以后补；
3. 最后才有人：角色与势力是规则的产物，不是先有人再给他找事做；
4. 每条设定都要能被**反问**："这条设定会不会让主角变轻松？" 会，就改到它产生新的困难为止。

绝对禁止：
1. 禁止成段摘录原文，单处引用不超过 {MAX_QUOTE_CHARS} 字且必须跟一句点评；
2. 禁止写出可以直接贴进正文的段落（不代笔，只给结构）；
3. 禁止空话套话（"宏大世界观""设定严谨"这类不构成信息）；
4. 禁止"待补""略""同上"这类留白 —— 想不出来就换一个更小的设定，不许挖坑；
5. 禁止照抄知识库片段里的具体设定名。学到的只能是**做法**，例如"每条能力配套一个执行代价"，
   不能是具体的能力或势力。

输出：只输出一个 JSON 对象，不要 markdown 代码围栏、不要前后多余文字。中文简体。
"""

OUTLINE_SYSTEM_MESSAGE = f"""你是一名资深中文网文的细纲编剧，为一位要写自己书的作者排**前 10 章的张力曲线**。

你的学习方法（与写设定完全不同 —— 这里是铺开，不是收敛）：
1. 一章 = 若干节拍，每个节拍必须落到"地点 + 出场人 + 一句**发生了什么**"，
   不接受人物小传与心理描写式的细纲；
2. 每个节拍都要能指出**谁在施压、谁承受、谁解围**（缺一角就写清为什么缺，比配齐更有戏）；
3. 沿时间轴安排情绪的涨落：不要章章都爽，也不要连续压 —— 给出你的配比理由；
4. 章尾钩必须是"下一章不得不开始"的那个未收束的点，不是悬念气体的空问句。

绝对禁止：
1. 禁止成段摘录原文，单处引用不超过 {MAX_QUOTE_CHARS} 字且必须跟一句点评；
2. 禁止写出可以直接贴进正文的段落（不代笔，只给节拍）；
3. 禁止"主角修炼，实力提升"这类没有冲突的推进 —— 每一章都要有人被逼到做选择；
4. 禁止脱离本项目已确认设定：不许引入设定库里没有的能力、势力或规则；
5. 禁止照抄知识库片段里的具体情节。学到的只能是**做法**，例如"每 2-3 章给一次爽点"，
   不能是具体的桥段。

输出：只输出一个 JSON 对象，不要 markdown 代码围栏、不要前后多余文字。中文简体。
"""


def _kb_block(snippets: list[dict], heading: str) -> str:
    """把知识库命中片段渲染成"学到了什么做法"区块（明确禁止照抄）。

    委托 kb/corpus.render_kb_block（全项目唯一 KB 渲染口径，t3 缺陷 #4/#5 修复），
    再统一追一句禁止照抄尾注，与正文/骨架侧"严禁照抄具体作品情节"同义。
    """
    from ndecon.kb.corpus import render_kb_block

    block = render_kb_block(snippets, heading=heading)
    if not block:
        return ""
    return block + "\n（以上只能借鉴做法与数值手感；禁止照抄其中的具体情节、设定名与成句。）"


def _setting_spec() -> dict:
    """设定产物的输出结构说明（随 prompt 发送，不参与运行时强校验）。"""
    return {
        "entries": [
            {
                "entry_type": "world_rule / faction / character / location / item / power_system 之一",
                "name": "名称（≤8 字）",
                "content": "设定内容：它是什么、边界在哪、违反会怎样",
                "tags": ["标签1"],
                "related_chapters": "关联章号数组，如 [1,3]",
            }
        ],
        "golden_finger": {
            "name": "金手指名称",
            "form": "形态（系统/血脉/物品/天赋/规则权能）",
            "ability": "能做什么（一句话，不写数值表）",
            "cost_limitation": "限制与代价（**必答**：使用代价 + 使用上限 + 被谁察觉）",
            "growth_path": "成长路径：每一步升级同步增加什么代价",
        },
        "notes": ["你这次产出遵循/违背了哪条原则；从方法论参考里实际用上了哪一条"],
    }


def _outline_spec(chapter_count: int = 10) -> dict:
    """细纲产物的输出结构说明。"""
    return {
        "chapters": [
            {
                "order": f"1-{chapter_count} 的整数",
                "emotion_track": "本章情绪走向，如 '压→转→爽'",
                "beats": [
                    {
                        "order": "节拍序号（章内 1 起，每章 3-10 个）",
                        "scene": "场景/地点（具体到能拍）",
                        "characters": ["出场角色"],
                        "triangle": "按 [迫害者, 受害者, 拯救者] 顺序给出；缺一角写清为什么缺",
                        "emotion": "本节拍情绪",
                        "event": "发生了什么（一句，必须是动作不是状态）",
                        "plant_foreshadow": "本节拍埋下的伏笔；没有就不给这个字段",
                    }
                ],
            }
        ],
        "notes": ["你的节奏配比依据；从方法论参考里实际用上了哪一条"],
    }


def _project_brief(title: str, genre: str, premise: str) -> str:
    return f"书名：《{title}》\n题材：{genre or '（未指定，由你据设定判断）'}\n一句话设定：{premise}"


def setting_user_message(
    title: str,
    genre: str,
    premise: str,
    knowledge_snippets: list[dict] | None = None,
    *,
    entry_budget: int = 8,
) -> str:
    """组装设定 prompt 的用户消息：收敛式——先边界、再代价、最后人。"""
    knowledge = _kb_block(knowledge_snippets or [], "【设定写法的方法论参考】")
    sections = [
        _project_brief(title, genre, premise),
        knowledge,
        (
            f"请产出 {entry_budget} 条以内的设定条目，覆盖至少 3 个不同类型"
            "（world_rule / faction / character / location / item / power_system）。\n"
            "硬性要求：\n"
            "- 每条 content 必须回答'违反它会怎样'，答不出的条目删掉重写；\n"
            "- golden_finger 的 cost_limitation 必答且要具体到可执行（不要'\u2026代价很大'这种）；\n"
            "- golden_finger 的 growth_path 每升一级必须同步加重代价，禁止只涨能力；\n"
            "- notes 里说明你从方法论参考中实际借鉴了哪一条（没有就写'未使用'）。\n\n"
            + json.dumps(_setting_spec(), ensure_ascii=False)
        ),
    ]
    return "\n\n".join(part for part in sections if part)


def outline_user_message(
    title: str,
    genre: str,
    premise: str,
    confirmed_settings: list[dict] | None = None,
    knowledge_snippets: list[dict] | None = None,
    *,
    golden_finger: dict | None = None,
    chapter_count: int = 10,
) -> str:
    """组装细纲 prompt 的用户消息：铺开式——沿时间轴排张力。

    已确认设定在这里是**硬约束**：细纲阶段不许引入设定库之外的能力/势力/规则，
    否则"先是设定层失控，再是细纲层补不了"，两个阶段的边界就白划了。

    chapter_count：本次要覆盖多少章（默认 10，与旧行为一致；长跑演练可调大）。
    """
    knowledge = _kb_block(knowledge_snippets or [], "【节奏与张力的方法论参考】")
    constraints = ""
    if confirmed_settings or golden_finger:
        lines = ["【本项目已确认设定（细纲不得越界引入新能力/势力/规则）】"]
        for entry in confirmed_settings or []:
            lines.append(
                f"- [{entry.get('entry_type', '?')}] {entry.get('name', '')}：{entry.get('content', '')}"
            )
        if golden_finger:
            lines.append(
                f"- 金手指 {golden_finger.get('name', '')}：{golden_finger.get('ability', '')}"
                f"；代价：{golden_finger.get('cost_limitation', '')}"
            )
        constraints = "\n".join(lines)

    task = (
        f"请产出第 1-{chapter_count} 章的节拍细纲，每章 3-10 个节拍。\n"
        "硬性要求：\n"
        "- 每个节拍的 event 必须是**动作**（'他决定…''她感到…' 不合格，要写他做了什么）；\n"
        "- 每章至少 2 个节拍给出完整三角（迫害者/受害者/拯救者齐全），"
        "其余节拍允许缺角但必须写明原因；\n"
        "- 第 1 章第一拍必须把钩子句落到具体动作上（不许'一个神秘的电话改变了他的一生'）；\n"
        "- 每章结尾都必须留下未收束的线，并把这个钩子落到该章最后一拍的具体动作上；"
        f"- 第 {chapter_count} 章同样要留通向下一卷的口子；\n"
        "- notes 里给出你的节奏配比（例：'第 1-3 章压抑，4 章给反转，7 章再压'）"
        "并说明从方法论参考中借鉴了哪一条（没有就写'未使用'）。\n\n"
        + json.dumps(_outline_spec(chapter_count), ensure_ascii=False)
    )
    sections = [_project_brief(title, genre, premise), constraints, knowledge, task]
    return "\n\n".join(part for part in sections if part)
