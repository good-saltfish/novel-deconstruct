"""词面信号层：切句 + 开关辞典打标。

**定位：这是一台打标机，不是解析器。**

它只对"这一句里出现了什么词"负责，不对"这句话是什么意思"负责。
冲突三角里"谁是迫害者/受害者/拯救者"属于语义判断，本模块一律不碰。

为什么不再聪明一点（三条理由，写给以后想加规则的人看）：
1. 假装理解会产出**看起来可信的随机错误结构**——作者拿它去改稿，比没有更糟；
2. 辞典是可逐条审阅的：每一条规则都能指着说"这条对/这条太宽"；
   一旦引入权重与推断，错在哪就说不清了；
3. 语义抽取交给 `semantics.py`（真实模型）或作者本人，那里才有可追溯的出处。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 成对引号检测（口语信号）
_QUOTES = re.compile(r"[“\"'‘][^”\"'’]{0,200}[”\"'’]")
# 句末标点（供 split_sentences 判断边界）
_SENTENCE_END = "。！？!?…"


@dataclass(frozen=True)
class Rule:
    """一条开关辞典规则：命中即在句子上打一个标。"""

    name: str
    pattern: re.Pattern[str]
    note: str


# 疑似冲突信号辞典（10 条）。
# 每条 note 是给创作者的**手法提示**，不是对原文的解释——它不引用这本书写了什么。
CLASH_LEXICON: tuple[Rule, ...] = (
    Rule(
        "施压动词",
        re.compile(r"威胁|逼迫|逼着|强迫|勒令|警告道|喝道|怒斥|责令|限期"),
        "外部压力落到具体人身上",
    ),
    Rule(
        "剥夺·损失",
        re.compile(r"剥夺|没收|收回|作废|清零|取消资格|断了|失去|赔光|一无所有"),
        "先失去后夺回，比一路赢更抓人",
    ),
    Rule(
        "冲突升级",
        re.compile(r"动手|出手|一拳|翻脸|爆发|摊牌|撕破脸|开战|宣战|反目"),
        "冲突要有物理/可见的升级动作",
    ),
    Rule(
        "轻视·打脸前置",
        re.compile(r"冷笑|不屑|嘲讽|讥讽|嗤笑|看不起|就凭你|也配"),
        "先压低期待，反转才有落差",
    ),
    Rule(
        "质询逼问",
        re.compile(r"质问|审问|逼问|追问|拷问|为什么|凭什么|怎么回事"),
        "问句把信息缺口摆在读者面前",
    ),
    Rule(
        "陷害·背叛",
        re.compile(r"陷害|栽赃|背叛|出卖|反水|下毒|暗算|设局|算计"),
        "背叛制造的是关系断裂，不只是事件",
    ),
    Rule(
        "救援·解围",
        re.compile(r"赶到|出手相救|挡在|护住|解围|救下|拦下|撑住"),
        "拯救者登场决定这一局的收束方式",
    ),
    Rule(
        "规则惩罚",
        re.compile(r"违规|触犯|惩罚|处罚|代价|反噬|禁止|规则|条例|判定"),
        "规则类冲突不靠强弱，靠代价",
    ),
    Rule(
        "能力释放",
        re.compile(r"觉醒|解锁|突破|进阶|晋升|激活|爆发|开挂|领悟"),
        "能力释放要配代价，否则只是数值变大",
    ),
    Rule(
        "悬念提问",
        re.compile(r"难道|莫非|究竟|到底|是谁|怎么会|忽然意识到|不对劲"),
        "把疑问留成钩子，别在同一段里答完",
    ),
)


def split_sentences(body: str, base_offset: int = 0) -> list[tuple[int, int, str]]:
    """切句，返回 (起始偏移, 结束偏移, 去空白文本) 三元组列表；过滤空句。

    **引号感知**：不在成对引号内部切断句子。
    否则像“这不可能。”这样的对话会被切成“这不可能。/ ”他低声说。两段，
    两段都不含完整引号，导致"对话句占比"这类指标恒为 0——一个看起来能用的假数据。

    代价（诚实声明）：整段纯对话（甲：“…。”乙：“…。”）会被算成一句，
    句长因此偏长；这是刻意取舍——漏判好过系统性归零。
    """
    open_to_close = {"“": "”", "‘": "’", '"': '"', "'": "'"}
    pending_closer = ""

    sentences: list[tuple[int, int, str]] = []
    start = -1
    index = 0
    length = len(body)
    while index < length:
        ch = body[index]
        if start < 0:
            # 句首：跳过空白后设起点，**并且必须继续处理这个首字符本身**
            # （早期版本在这里 continue，导致句首就是引号时左引号没登记，
            #   于是在引号内的句号处被误切：  “这不可能。/ ”他低声说。）
            if ch in " \t\r\n　":
                index += 1
                continue
            start = index
        if pending_closer:
            if ch == pending_closer:
                pending_closer = ""
        elif ch in open_to_close:
            pending_closer = open_to_close[ch]
        elif ch in _SENTENCE_END and not pending_closer:
            text = body[start : index + 1].strip()
            if text:
                sentences.append((base_offset + start, base_offset + index + 1, text))
            start = -1
        index += 1
    if start >= 0:
        text = body[start:].strip()
        if text:
            sentences.append((base_offset + start, base_offset + length, text))
    return sentences


def detect_triggers(sentence: str) -> list[Rule]:
    """在一句文本上跑开关辞典，返回所有命中的规则。"""
    return [rule for rule in CLASH_LEXICON if rule.pattern.search(sentence)]


def has_dialogue(sentence: str) -> bool:
    """句内是否含成对引号（粗略的口语信号）。"""
    return bool(_QUOTES.search(sentence))
