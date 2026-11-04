"""AI 指纹扫描器（只读度量，绝不改写）。

把 docs/ai-writing-workflow.md 第 5.3 节的可量化阈值做成确定性扫描：
输入一章正文，输出每条规则的实测值与阈值对比，**只报告，不给改后的文本**。

阈值来源为四级信源（开源工程实践 novel-writing-framework，500+ 章实战），
已在文档中标注"需自验"；本模块因此把每条规则都做成**可配置上限**，
作者可据自己的语感校准（改常量即可，扫描逻辑不变）。

设计纪律：
1. 纯字符串/正则，确定性——同样输入永远同样输出，不调模型、不联网；
2. 只数"可疑密度"，不做"是不是 AI"的判定（检测准确率反证见文档 5.4：
   老舍《林海》被判 99.9% AI，爱伦坡《黑猫》被判"包是 AI 写的"）；
3. 不产出改写文本——改什么由作者决定。

调用方：
- CLI：`python -m ndecon.cli lint <章文件> [--strict]`（--strict 时越界非零退出，可挂 git hook）；
- 面板：GET /api/projects/{id}/chapters/{order}/lint —— 同一个报告，同样只读。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

AI_FINGERPRINT_VERSION = "fp-v1"

# ---- 关于「这些阈值能不能当硬门」的实测结论（2026-11-04，勿凭直觉改） ----
# 用 99 本真实网文（3956 章）与 10 章 deepseek-flash 真实正文做过分布对比：
#
#   规则                    人类p50  人类p95  人类max   AI p50  AI max
#   em_dash                    0        5        30      10      17
#   period_per_paragraph       2        4       329      4       5
#   simile_density             3       10        39     7.5      10
#   scene_rotation             0        2         6      0       2
#
# 结论：**单章上这些规则全部重叠，没有一条能用单一阈值把 AI 与人类切开。**
# 中位数差异明显（em_dash 0 vs 10），但人类小说里 em_dash=30、段落句号=329
# 的章节真实存在（那是作者的个人风格），所以任何「单章超阈值即拒绝」的硬门
# 都会误杀人类写的好书。
#
# 因此这些规则一律保持 severity="warn"（只记分不阻断）。要升级成硬门，
# 需要的是**跨章聚合判据**（例如连续 N 章的章级中位数持续偏高），
# 不是把单章阈值调一调 —— 后者只会把误杀率从 23% 调到另一个数字。
#
# ---- 阈值（可按作者语感校准） ----
# "不是 X，是 Y" 句式：该项目实测 663 次 -> 清洗到 31 次，是全书工作量最大的单项修复
NOT_X_BUT_Y_PER_CHAPTER = 2
# 中文破折号：更严的口径是全书 <= 20
DASH_PER_CHAPTER = 1
# 段落内句号数（过长段落是 AI 的典型形态）
PERIOD_PER_PARAGRAPH = 2
# 比喻密度
SIMILE_PER_PARAGRAPH = 1
SIMILE_PER_CHAPTER = 3
# 连续"的"：3 个及以上（"的的的"）判违规
DE_REPEAT_ALLOWED = 2
# 场景轮换下限（启发式，见 _scene_markers）
SCENE_ROTATION_MIN = 5
# 单章正文字数下限
CJK_PER_CHAPTER_MIN = 2000
# 阈值的校准体量：规则次数按"一章 2000-3500 字"标定，
# 显著超过该体量的文本（如整篇调研报告）绝对次数不可比，CLI 会如实提示
CHAPTER_CALIBRATION_CHARS = 5000

_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_PERIOD_RE = re.compile(r"。")
_DASH_RE = re.compile(r"——")
# "不是 X（，）是/而是 Y" —— AI 最爱用的强调句式
_NOT_X_BUT_Y_RE = re.compile(r"不是[^。！？；\n]{1,30}?[，、]?(?:而是|是)")
# 比喻标记词
_SIMILE_RE = re.compile(r"像|仿佛|宛如|如同|似的|一般般|一般(?=[，。！？])")
# 连续"的"：以"短窗口内聚集"为口径——相邻两个"的"间隔 <=4 个非句读字符
# 且中间没有句读，就视为同一串（"东西的颜色的边缘的温度" 计 3）
_DE_GAP_CHARS = 4
_DE_SPLIT_RE = re.compile(r"[。！？；，、\n]")
# 场景转换标记：段落开头出现的时空切换/地点切换词
_SCENE_START_RE = re.compile(
    r"^\s*(?:与此同时|与此同时，|片刻后|半晌后|不知过了多久|转瞬|须臾|次日|第二天|"
    r"三天后|翌日|当晚|夜幕|凌晨|清晨|黄昏|正午|傍晚|与此同时|另一边|另一头|那一边|"
    r"这边|这边厢|另一处|与此同时|门外的|屋内|屋外|殿内|殿外|车上|车外|楼下|楼上|"
    r"街角|巷口|院中|院外|城门口|大殿上|山坡上|山脚下|河边|林中|此处|那边|远处|近处)"
)
_TRANSITION_RE = re.compile(r"与此同时|另一边|另一头|片刻后|不知过了多久|次日|翌日|第二天")


def count_cjk(text: str) -> int:
    """统计中日韩统一表意文字数量（作为"字数"的稳定口径）。"""
    return len(_CJK_RE.findall(text))


def split_paragraphs(text: str) -> list[str]:
    """按空行切段；无空行时退化为按行切（保证单段文本也能被度量）。"""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) <= 1:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            return lines
    return blocks


def _scene_markers(paragraphs: list[str]) -> list[int]:
    """返回每段"是否开启新场景"的 0/1 序列（启发式）。

    判据：段首命中时空/地点切换词，或段内出现显式转场连接词。
    这是**密度信号**而非段落语义理解——不假装理解场景，只数标记。
    """
    flags: list[int] = []
    for para in paragraphs:
        head = para[:12]
        hit = bool(_SCENE_START_RE.match(head)) or bool(_TRANSITION_RE.search(para[:60]))
        flags.append(1 if hit else 0)
    return flags


class FingerprintFinding(BaseModel):
    """一条规则的实测结果。"""

    rule: str = Field(description="规则标识，稳定不随文案变化")
    label: str = Field(description="人读的规则名")
    value: float = Field(description="实测值")
    limit: float = Field(description="阈值")
    detail: str = Field(default="", description="补充说明（命中位置/样本）")
    severity: str = Field(default="warn", description="warn=越界提示；info=仅告知，不判越界")
    passed: bool = Field(description="是否未越界；info 规则恒为 True")


class FingerprintReport(BaseModel):
    """一章正文的指纹扫描报告。"""

    version: str = Field(default=AI_FINGERPRINT_VERSION, description="规则集版本")
    cjk_chars: int = Field(description="正文字数（CJK）")
    paragraph_count: int = Field(description="段落数")
    scene_rotations: int = Field(description="场景轮换次数（启发式）")
    findings: list[FingerprintFinding] = Field(default_factory=list)
    passed: bool = Field(description="是否所有 warn 规则均未越界")


@dataclass
class _Sample:
    """收集违规样本的轻量容器（最多保留 3 条，避免报告被长正文撑爆）。"""

    items: list[str] = field(default_factory=list)
    limit: int = 3

    def add(self, text: str) -> None:
        if len(self.items) < self.limit:
            self.items.append(text)

    def render(self, prefix: str = "例：") -> str:
        return (prefix + "；".join(self.items)) if self.items else ""


def scan_chapter(text: str) -> FingerprintReport:
    """扫描一章正文，返回只读报告（不修改、不产出替代文本）。"""
    paragraphs = split_paragraphs(text)
    cjk = count_cjk(text)
    findings: list[FingerprintFinding] = []

    # 1. "不是 X，是 Y" 句式密度
    not_x_hits = _NOT_X_BUT_Y_RE.findall(text)
    findings.append(
        FingerprintFinding(
            rule="not_x_but_y",
            label=f"「不是X，是Y」句式 ≤ {NOT_X_BUT_Y_PER_CHAPTER} 次/章",
            value=float(len(not_x_hits)),
            limit=float(NOT_X_BUT_Y_PER_CHAPTER),
            detail=_Sample().render() or _sample_of(not_x_hits),
            passed=len(not_x_hits) <= NOT_X_BUT_Y_PER_CHAPTER,
        )
    )

    # 2. 中文破折号密度
    dashes = _DASH_RE.findall(text)
    findings.append(
        FingerprintFinding(
            rule="em_dash",
            label=f"中文破折号「——」 ≤ {DASH_PER_CHAPTER} 处/章",
            value=float(len(dashes)),
            limit=float(DASH_PER_CHAPTER),
            passed=len(dashes) <= DASH_PER_CHAPTER,
        )
    )

    # 3. 段落内句号数（过长段落）
    long_paras: list[str] = []
    max_periods = 0
    for para in paragraphs:
        n = len(_PERIOD_RE.findall(para))
        max_periods = max(max_periods, n)
        if n > PERIOD_PER_PARAGRAPH:
            long_paras.append(f"第{n}句")
    findings.append(
        FingerprintFinding(
            rule="period_per_paragraph",
            label=f"段落内句号 ≤ {PERIOD_PER_PARAGRAPH} 个",
            value=float(max_periods),
            limit=float(PERIOD_PER_PARAGRAPH),
            detail=(f"超标段数 {len(long_paras)}" if long_paras else ""),
            passed=max_periods <= PERIOD_PER_PARAGRAPH,
        )
    )

    # 4. 比喻密度（段落级 + 章级）
    para_similes = [len(_SIMILE_RE.findall(para)) for para in paragraphs]
    chapter_similes = sum(para_similes)
    dense_paras = sum(1 for n in para_similes if n > SIMILE_PER_PARAGRAPH)
    findings.append(
        FingerprintFinding(
            rule="simile_density",
            label=f"比喻：一段 ≤ {SIMILE_PER_PARAGRAPH}、整章 ≤ {SIMILE_PER_CHAPTER}",
            value=float(chapter_similes),
            limit=float(SIMILE_PER_CHAPTER),
            detail=(f"超标段数 {dense_paras}" if dense_paras else ""),
            passed=chapter_similes <= SIMILE_PER_CHAPTER and dense_paras == 0,
        )
    )

    # 5. 连续"的"（3 个及以上，按"未被句读隔开的短窗口聚集"计）
    de_runs = _de_runs(text)
    findings.append(
        FingerprintFinding(
            rule="de_repeat",
            label=f"连续「的」 ≤ {DE_REPEAT_ALLOWED} 个",
            value=float(max((len(run) for run in de_runs), default=0)),
            limit=float(DE_REPEAT_ALLOWED),
            detail=(f"命中 {len(de_runs)} 串" if de_runs else ""),
            passed=not de_runs,
        )
    )

    # 6. 场景轮换（启发式下限）
    rotations = sum(_scene_markers(paragraphs))
    findings.append(
        FingerprintFinding(
            rule="scene_rotation",
            label=f"场景轮换 ≥ {SCENE_ROTATION_MIN} 个（启发式）",
            value=float(rotations),
            limit=float(SCENE_ROTATION_MIN),
            detail="按段首时空/地点切换词与转场连接词计数，非语义理解",
            passed=rotations >= SCENE_ROTATION_MIN,
        )
    )

    # 7. 字数
    findings.append(
        FingerprintFinding(
            rule="cjk_chars",
            label=f"单章正文字数 ≥ {CJK_PER_CHAPTER_MIN}",
            value=float(cjk),
            limit=float(CJK_PER_CHAPTER_MIN),
            passed=cjk >= CJK_PER_CHAPTER_MIN,
        )
    )

    # 8. 单引号（仅告知：网文作者几乎不用单引号，但引号式引用本身合法）
    single_quotes = text.count("‘") + text.count("’") + text.count("'")
    findings.append(
        FingerprintFinding(
            rule="single_quote",
            label="单引号使用（网文少见，仅提示）",
            value=float(single_quotes),
            limit=0.0,
            detail="非零不代表违规，供作者自行判断",
            severity="info",
            passed=True,
        )
    )

    passed = all(finding.passed for finding in findings if finding.severity == "warn")
    return FingerprintReport(
        cjk_chars=cjk,
        paragraph_count=len(paragraphs),
        scene_rotations=rotations,
        findings=findings,
        passed=passed,
    )


def _de_runs(text: str) -> list[list[int]]:
    """找出"的"的密集串：同一串内相邻"的"之间不超过 _DE_GAP_CHARS 个非句读字符。

    返回每串的位置列表。"东西的颜色的边缘的温度" 三个"的"间隔都很短 -> 一串行 3。
    正常中文写作里"的"很少在这么短的窗口内连出 3 个，故作为密度信号。
    """
    runs: list[list[int]] = []
    current: list[int] = []
    last_pos = -1
    for match in re.finditer(r"的", text):
        pos = match.start()
        if current:
            gap = text[current[-1] + 1 : pos]
            if pos - last_pos - 1 > _DE_GAP_CHARS or _DE_SPLIT_RE.search(gap):
                runs.append(current)
                current = []
        current.append(pos)
        last_pos = pos
    if current:
        runs.append(current)
    return [run for run in runs if len(run) >= 3]


def _sample_of(hits: list[str]) -> str:
    """把命中样本压成短串（最多 3 条、每条截断 20 字）。"""
    if not hits:
        return ""
    parts = [hit.strip()[:20] for hit in hits[:3]]
    return "例：" + "；".join(parts)
