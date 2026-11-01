"""章节硬门与软门：把"人能判断的质量"翻译成可计算的判据。

设计原则（v3 计划 §2 判断②）：

- 能翻译成判据的，就写死在这里，交给机器判——**判据写不出来，那一步就不能自动化**；
- 判据分两级：**硬门**不通过就重写/丢弃（verdict=repair/reject），
  **软门**只记分不阻断（新鲜度、指纹密度），用于事后度量与调参；
- 本模块**只判定、不改写**：不产出替代文本，避免"自动改稿"把作者痕迹抹平；
- 每条失败都带**可定位的证据**（原文例句），否则人无从复核，机器也就无从改。

判定优先级：reject > repair > pass。任何硬门 reject 直接 reject，不再看修复建议。
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, Field

from ndecon.quality.fingerprint import scan_chapter

# 单章字数目标默认值（网文常见 2000-3500 字的中间带）。真实项目应写进细纲；
# 细纲没有目标时用这个兜底，band 仍按 ±15% 判。
CHAPTER_TARGET_CHARS_DEFAULT = 2600
# 字数容差：低于 85% 说明没写完，高于 115% 说明灌水/跑偏，两头都要拦。
WORD_BAND_LOW = 0.85
WORD_BAND_HIGH = 1.15

# 退化检测阈值（对齐 oh-story 的 check-degeneration.js，数值取其保守一侧）
REPEAT_MIN_LEN = 12
REPEAT_MIN_COUNT = 3
ADJACENT_MIN_LEN = 8

# 工程词 tier1：这些词只该出现在提纲/内部记录里，出现在正文说明模型把
# 自己的任务描述漏进了作品。tier2（本章/下一章/读者/伏笔）太常见，只提示不拦。
ENGINEERING_WORDS_TIER1 = (
    "细纲",
    "情节点",
    "卷纲",
    "功能标签",
    "目标情绪",
    "字数目标",
    "章首钩子",
    "章尾钩子",
    "任务描述",
)

# AI 自指与拒绝语：模型越狱失败或安全策略触发的典型痕迹，只在不含引号的行里判，
# 避免把角色台词里正当的"我不能告诉你"误杀。
SELF_REFERENCE_PATTERNS = (
    re.compile(r"作为(?:一个)?(?:AI|人工智能|语言模型)"),
    re.compile(r"(?:我|本模型)(?:无法|不能)(?:继续|生成|创作|协助)"),
    re.compile(r"(?:很抱歉|抱歉)[，,](?:我|但)"),
)

PLACEHOLDER_PATTERNS = (
    re.compile(r"\{\{?[^}]{1,30}\}\}?"),
    re.compile(r"【(?:待填|占位|TODO|待补)"),
    re.compile(r"(?:此处省略|以下省略|略去)[^。]{0,10}(?:字|内容)"),
)

# 合法收尾字符：正文截断的判据是"最后一个可见字符不在这些标点里"。
CLOSING_CHARS = set("。！？…”』」）)!?.~—:：")


class GateFailure(BaseModel):
    """一条硬门失败：带规则标识与可复核证据。"""

    gate: str = Field(description="硬门标识：word_count/degeneration/engineering/coverage")
    rule: str = Field(description="规则标识，稳定不随文案变化")
    detail: str = Field(description="人读的失败原因")
    evidence: list[str] = Field(default_factory=list, description="原文例句（最多 3 条）")


class GateReport(BaseModel):
    """一章的闸门判定结果。"""

    verdict: str = Field(description="pass=通过；repair=可重写修复；reject=直接丢弃")
    target_chars: int = Field(description="本次使用的单章字数目标")
    cjk_chars: int = Field(description="实测正文字数（CJK）")
    hard_failures: list[GateFailure] = Field(default_factory=list)
    soft_scores: dict[str, float] = Field(default_factory=dict, description="软门只记分，不阻断")
    sample_quotes: list[str] = Field(default_factory=list, description="最值得人看的两三句原文")

    @property
    def passed(self) -> bool:
        """是否可以直接入库（无硬门失败）。"""
        return not self.hard_failures and self.verdict == "pass"


def _cjk_count(text: str) -> int:
    """统计 CJK 字数（与 ManuscriptRecord.word_count 同口径：去空白后计中文字符）。"""
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _evidence(text: str, limit: int = 3, width: int = 40) -> list[str]:
    """截一小段原文做证据：太长没人看，太短定位不到。"""
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    return [clean[:width]]


def _check_word_count(text: str, target: int) -> GateFailure | None:
    """硬门 1：字数必须落在目标的 ±15% 带内。"""
    actual = _cjk_count(text)
    low = int(target * WORD_BAND_LOW)
    high = int(target * WORD_BAND_HIGH)
    if low <= actual <= high:
        return None
    direction = "偏短" if actual < low else "偏长"
    return GateFailure(
        gate="word_count",
        rule="chapter_length",
        detail=f"{direction}：实测 {actual} 字，目标 {target} 字，允许 {low}-{high}",
        evidence=[],
    )


def _check_truncation(text: str) -> GateFailure | None:
    """正文末尾必须落在收尾标点上，否则视为生成被截断。"""
    tail = text.rstrip()
    if not tail:
        return GateFailure(gate="degeneration", rule="truncated", detail="正文为空", evidence=[])
    if tail[-1] not in CLOSING_CHARS:
        return GateFailure(
            gate="degeneration",
            rule="truncated",
            detail=f"末字符是「{tail[-1]}」，不在收尾标点集内，疑似生成被截断",
            evidence=_evidence(tail[-60:]),
        )
    return None


def _check_repeats(text: str) -> GateFailure | None:
    """复读检测：同一段 12 字以上片段出现 ≥3 次，或相邻两行 ≥8 字完全重复。"""
    body = re.sub(r"\s+", "", text)
    if len(body) < REPEAT_MIN_LEN * REPEAT_MIN_COUNT:
        return None
    counter: Counter[str] = Counter()
    for i in range(len(body) - REPEAT_MIN_LEN + 1):
        counter[body[i : i + REPEAT_MIN_LEN]] += 1
    worst, count = counter.most_common(1)[0] if counter else ("", 0)
    if count >= REPEAT_MIN_COUNT:
        return GateFailure(
            gate="degeneration",
            rule="repeat_span",
            detail=f"{REPEAT_MIN_LEN} 字片段重复 {count} 次（阈值 {REPEAT_MIN_COUNT}）",
            evidence=[worst],
        )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for prev, cur in zip(lines, lines[1:], strict=False):
        if len(cur) >= ADJACENT_MIN_LEN and cur == prev:
            return GateFailure(
                gate="degeneration",
                rule="adjacent_duplicate",
                detail="相邻两行完全重复，疑似循环输出",
                evidence=[cur[:40]],
            )
    return None


def _check_self_reference(text: str) -> GateFailure | None:
    """AI 自指/拒绝语：只看不含引号的行，避免误杀角色台词。"""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or "“" in stripped or '"' in stripped:
            continue
        for pattern in SELF_REFERENCE_PATTERNS:
            match = pattern.search(stripped)
            if match:
                return GateFailure(
                    gate="degeneration",
                    rule="self_reference",
                    detail=f"正文出现模型自指/拒绝语：{match.group(0)}",
                    evidence=[stripped[:40]],
                )
    return None


def _check_placeholder(text: str) -> GateFailure | None:
    """占位符与"此处省略"类偷工痕迹。"""
    for pattern in PLACEHOLDER_PATTERNS:
        match = pattern.search(text)
        if match:
            return GateFailure(
                gate="degeneration",
                rule="placeholder",
                detail=f"正文残留占位符：{match.group(0)}",
                evidence=[match.group(0)[:40]],
            )
    return None


def _check_mojibake(text: str) -> GateFailure | None:
    """乱码字符（U+FFFD）说明编码链路有问题，不能入库。"""
    if "\ufffd" in text:
        return GateFailure(
            gate="degeneration",
            rule="mojibake",
            detail="正文含替换字符 U+FFFD，编码链路有损",
            evidence=[],
        )
    return None


def _check_engineering_words(text: str) -> GateFailure | None:
    """硬门 3：正文不许出现工程词 tier1。"""
    hits = [word for word in ENGINEERING_WORDS_TIER1 if word in text]
    if not hits:
        return None
    evidence: list[str] = []
    for word in hits[:3]:
        idx = text.find(word)
        evidence.extend(_evidence(text[max(0, idx - 15) : idx + 25]))
    return GateFailure(
        gate="engineering",
        rule="tier1_engineering_words",
        detail=f"正文出现工程词：{'、'.join(hits)}",
        evidence=evidence,
    )


def _freshness(text: str, previous_texts: list[str], ngram: int = 5) -> float:
    """软门：与近若干章的 5-gram 重复率（1.0=全新，0.0=全旧）。"""
    def grams(value: str) -> set[str]:
        body = re.sub(r"\s+", "", value)
        return {body[i : i + ngram] for i in range(max(0, len(body) - ngram + 1))}

    current = grams(text)
    if not current:
        return 0.0
    previous: set[str] = set()
    for item in previous_texts:
        previous |= grams(item)
    if not previous:
        return 1.0
    overlap = len(current & previous) / len(current)
    return round(1.0 - overlap, 4)


def check_chapter(
    text: str,
    *,
    target_chars: int = CHAPTER_TARGET_CHARS_DEFAULT,
    previous_texts: list[str] | None = None,
    summary_present: bool = True,
    fingerprint_report: object | None = None,
) -> GateReport:
    """对一章正文跑全部硬门与软门，返回判定报告（不改写正文）。

    ``summary_present=False`` 时记一条 coverage 硬门失败：没有摘要的章节进不了
    检索索引，后续章节就会"忘掉"这一章——这正是全自动长篇最常见的断链方式。
    """
    previous_texts = previous_texts or []
    hard: list[GateFailure] = []

    for failure in (
        _check_word_count(text, target_chars),
        _check_truncation(text),
        _check_repeats(text),
        _check_self_reference(text),
        _check_placeholder(text),
        _check_mojibake(text),
        _check_engineering_words(text),
    ):
        if failure is not None:
            hard.append(failure)

    if not summary_present:
        hard.append(
            GateFailure(
                gate="coverage",
                rule="summary_missing",
                detail="本章没有结构化摘要，后续章节无法检索到它（连续性会断）",
                evidence=[],
            )
        )

    report = fingerprint_report if fingerprint_report is not None else scan_chapter(text)
    soft: dict[str, float] = {
        "freshness": _freshness(text, previous_texts),
        "fingerprint_passed": 1.0 if getattr(report, "passed", False) else 0.0,
    }
    for finding in getattr(report, "findings", []) or []:
        if getattr(finding, "severity", "warn") == "warn":
            # 指纹越界只记分：AI 味阈值本来就按 2000-3500 字校准，硬拦会误杀。
            soft[f"fp_{getattr(finding, 'rule', '?')}"] = float(getattr(finding, "value", 0.0))

    sample_quotes = [quote for failure in hard for quote in failure.evidence][:3]

    # reject 只留给"重写也救不回来"的失败：编码链路坏了（乱码）或正文根本为空。
    # 其余硬门失败都是 repair——先让 txn 带着证据重写，超过上限才真正丢弃。
    unrecoverable = {f.rule for f in hard} & {"mojibake"} or (not text.strip())
    if not hard:
        verdict = "pass"
    elif unrecoverable:
        verdict = "reject"
    else:
        verdict = "repair"

    return GateReport(
        verdict=verdict,
        target_chars=target_chars,
        cjk_chars=_cjk_count(text),
        hard_failures=hard,
        soft_scores=soft,
        sample_quotes=sample_quotes,
    )
