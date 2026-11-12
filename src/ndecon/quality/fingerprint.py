"""AI 指纹扫描器（只读度量，绝不改写）。

把 docs/ai-writing-workflow.md 第 5.3 节的可量化阈值做成确定性扫描：
输入一章正文，输出每条规则的实测值与阈值对比，**只报告，不给改后的文本**。

阈值来源为四级信源（开源工程实践 novel-writing-framework，500+ 章实战），
已在文档中标注"需自验"；本模块因此把每条规则都做成**可配置上限**，
作者可据自己的语感校准（改常量即可，扫描逻辑不变）。

fp-v2（2026-11-12）把阈值重新标定到本机语料实测分位，并修掉三个会误导人的口径：
1. **段落口径与 style.py 统一**：旧版 ``split_paragraphs`` 按空行切块，而真实网文
   txt 多为单换行分段，于是"一整章"被当成一段，``period_per_paragraph`` 的实测值
   与它自己的阈值表差 17 倍（人类 p95 实测 69，表里写 4）。现在统一用
   ``style.paragraphs_of``（一行一段、未收尾的行并入下一行），两条判据才同一坐标系。
2. **``scene_rotation`` 不再判罚**：它的判据是"场景标记 >= 5"，方向与人类实际相反
   （人类中位 0，即大多数章节一个转场词都没有）。它曾让 ``passed`` 在人类章节上
   只有 0.1% 为真，是最典型的假指标。现在降为 info，只展示计数。
3. **``passed`` 从"全部规则都要过"改成"最多允许 1 条越界"**：单章上"像 AI"与
   "像人"的分布本来就重叠（见下方标定表），任何一条越界都不足以判死；≥2 条同时
   越界才是有意义的信号。旧口径等于要求一章同时满足 5 条左右的窄阈值，人类作品
   自然全被判红。

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

from ndecon.quality.style import paragraphs_of

AI_FINGERPRINT_VERSION = "fp-v2"

# ---- 关于「这些阈值能不能当硬门」的实测结论（勿凭直觉改） ----
# fp-v1（2026-11-04）用 99 本真实网文（3956 章）与 10 章 deepseek-flash 对比，
# 结论是「单章上这些规则全部重叠，没有一条能用单一阈值把 AI 与人类切开」。
# fp-v2（2026-11-12）用本机语料重标定（dev 20 本 899 章 / hold 19 本 854 章，
# 单章 CJK 2000-5000 字，段落口径 = style.paragraphs_of）：
#
#   规则                     人类p50  人类p75  人类p90  人类p95  人类p99  max
#   not_x_but_y  (次/章)        1        2        3        3        5      7
#   em_dash      (次/章)        0        1        4        9       28     75
#   new_long_rate(超长段占比)   0.000    0.012    0.073    0.143    0.289  0.465
#   simile_dense_rate           0.000    0.007    0.017    0.023    0.042  0.155
#   scene_new    (标记数)       0        1        2        3        4      5
#
# 阈值就取在人类 p90-p95 之间，容许"作者的个人风格"但拦住系统性密集。
# 单条越界的误杀率都压到 1%-9%（旧口径是 25%-99.9%），详见各常量注释。
#
# 仍然不能当硬门：``passed=False`` 在人类章节上仍有 4.7%-8.0% 的误杀率，
# 只够"建议看一眼"，不够"判定不能用"。真要硬门得靠跨章聚合
# （``ndecon/quality/drift.py``），那才是唯一验证过误杀率的判据。
#
# ---- 阈值（按作者语感可校准，改常量即可） ----
# "不是 X，是 Y" 句式：人类 p95=3、p99=5。取 3 时误杀 dev 4.8% / hold 3.0%
NOT_X_BUT_Y_PER_CHAPTER = 3
# 中文破折号：人类 p90=4、p95=9（有作者整章 75 个，属个人风格）。取 5 时误杀 dev 8.8% / hold 4.0%
DASH_PER_CHAPTER = 5
# 超长段（段内句号 > PERIOD_PER_PARAGRAPH 的段）占全部段落的比例。
# 旧口径"段内句号最大值 <= 2"误杀 34%-57%（人类 p95=6、max=24）；
# 换成占比后取 0.15 误杀 dev 4.9% / hold 0.0%。
LONG_PARAGRAPH_RATE = 0.15
# 单段判"超长"的句号数（只用来算占比，不再直接判罚）
PERIOD_PER_PARAGRAPH = 3
# 比喻密度：人类 simile_chapter p90=10、p95=12；超标段占比 p95=0.023、p99=0.042。
# 旧口径是"整章<=3 且 每段<=1"双重合取，误杀 34.3%；现在只保留章级上限 + 占比。
SIMILE_PER_CHAPTER = 10
SIMILE_DENSE_RATE = 0.05
# 连续"的"：3 个及以上（"的的的"）判违规。人类 p99=1，误杀 dev 1.5% / hold 3.0%
DE_REPEAT_ALLOWED = 2
# 场景轮换：**不参与判定**，只展示计数。人类中位 0、p95=3、max=5，
# 旧判据" >= 5 才算过"方向反了（等于要求每章都有 5 处转场词），单条误杀 99.9%。
# 保留常量是为了报告里能显示"启发式计数"，不代表这是个合格门槛。
SCENE_ROTATION_MIN = 5
# 单章正文字数下限
CJK_PER_CHAPTER_MIN = 2000
# 累计越界口径：warn 规则里最多允许几条越界。人类章节 >=2 条越界的比例
# dev 8.0% / hold 4.7%，与 style.py 五条联合误杀率 4.3% 同量级。
MAX_WARN_VIOLATIONS = 1
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
    """按空行切段；无空行时退化为按行切（保证单段文本也能被度量）。

    历史口径，**已不再用于判罚**：真实网文 txt 多为单换行分段，按空行切会把
    一整章当成一段（实测人类章 p95 从 6 涨到 117）。保留它只是为了兼容旧的
    调用方与测试；``scan_chapter`` 已改用 ``ndecon.quality.style.paragraphs_of``。
    """
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
    passed: bool = Field(
        description=(
            f"warn 规则越界条数是否 ≤ {MAX_WARN_VIOLATIONS}"
            "（单条越界只提示，累计越多越可疑）"
        )
    )


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
    paragraphs = paragraphs_of(text)
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

    # 3. 超长段占比（段内句号数超过 PERIOD_PER_PARAGRAPH 的段，占全部段落的比例）
    long_count = 0
    max_periods = 0
    for para in paragraphs:
        n = len(_PERIOD_RE.findall(para))
        max_periods = max(max_periods, n)
        if n > PERIOD_PER_PARAGRAPH:
            long_count += 1
    long_rate = (long_count / len(paragraphs)) if paragraphs else 0.0
    findings.append(
        FingerprintFinding(
            rule="period_per_paragraph",
            label=(
                f"超长段占比 ≤ {LONG_PARAGRAPH_RATE:.0%}"
                f"（单段句号 > {PERIOD_PER_PARAGRAPH} 视为超长）"
            ),
            value=round(long_rate, 4),
            limit=float(LONG_PARAGRAPH_RATE),
            detail=(f"超长段 {long_count}/{len(paragraphs)}，最长段 {max_periods} 句"),
            passed=long_rate <= LONG_PARAGRAPH_RATE,
        )
    )

    # 4. 比喻密度（章级上限 + 密集段占比）
    para_similes = [len(_SIMILE_RE.findall(para)) for para in paragraphs]
    chapter_similes = sum(para_similes)
    dense_paras = sum(1 for n in para_similes if n > 1)
    dense_rate = (dense_paras / len(paragraphs)) if paragraphs else 0.0
    findings.append(
        FingerprintFinding(
            rule="simile_density",
            label=(
                f"比喻：整章 ≤ {SIMILE_PER_CHAPTER} 处，"
                f"且密集段（一段 ≥2 处）占比 ≤ {SIMILE_DENSE_RATE:.0%}"
            ),
            value=float(chapter_similes),
            limit=float(SIMILE_PER_CHAPTER),
            detail=(f"密集段 {dense_paras}/{len(paragraphs)}"),
            passed=(chapter_similes <= SIMILE_PER_CHAPTER and dense_rate <= SIMILE_DENSE_RATE),
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
            label="场景轮换计数（启发式，不判定）",
            value=float(rotations),
            limit=float(SCENE_ROTATION_MIN),
            detail=(
                "按段首时空/地点切换词与转场连接词计数，非语义理解；"
                "人类中位 0、p95=3，故只展示不判罚"
            ),
            severity="info",
            passed=True,
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

    violations = sum(
        1 for finding in findings if finding.severity == "warn" and not finding.passed
    )
    return FingerprintReport(
        cjk_chars=cjk,
        paragraph_count=len(paragraphs),
        scene_rotations=rotations,
        findings=findings,
        passed=violations <= MAX_WARN_VIOLATIONS,
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
