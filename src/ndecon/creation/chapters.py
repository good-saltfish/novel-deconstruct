"""章节正文生成器（#16）：Fake 占位 + OpenAI 兼容真实生成。

上下文纪律（ADR-0004 / #17）：
- 生成器唯一输入是 :class:`ContextPack`——本书定位/人设/金手指、本章细纲、
  前章摘要、BM25 相关回顾、开放伏笔；
- ContextPack 在结构上不含参考书 ID、源路径与任何原文 quote，
  因此生成 prompt 不可能携带版权内容；
- 正文产出与结构化自评（钩子/信息密度/细纲偏差）同时返回，自评只报告不改写。
"""

from __future__ import annotations

import json
import random
import re
from typing import Protocol

from ndecon.creation.models import ChapterSelfReview, ChapterWriting
from ndecon.providers.errors import ProviderResponseError
from ndecon.providers.openai_compat import OpenAICompatProvider
from ndecon.quality.gates import CHAPTER_TARGET_CHARS_DEFAULT, PLACEHOLDER_BANNER, word_band
from ndecon.retrieval.context import ContextPack

CHAPTER_PROMPT_VERSION = "cw-v1"

# 占位正文的按章随机种子：固定基数 + 每章步进，同章可复现、异章不撞车。
CHAPTER_SCENE_SEED = 20261101
CHAPTER_SCENE_SEED_STEP = 7919
# 并行候选之间的种子间隔。必须明显大于章内场景步长（12），又小于章间步长
# （7919）：6 个候选 0..5000 全部落在本章区间内，不会串到邻章的场景序列上。
CANDIDATE_SEED_STRIDE = 1000

CHAPTER_SYSTEM_MESSAGE = """你是一名资深中文网络小说写手，为主笔作者撰写章节正文草稿。

硬性要求：
1. 只输出一个 JSON 对象，不输出任何额外文字或 markdown 代码块。
2. 严格按"本章细纲"推进：核心事件必须发生，开篇钩与章尾钩必须落到正文里；
   若为了叙事合理需要偏离细纲，必须在 review.deviations 中逐条说明。
3. 正文使用中文网文白话：场景具体、对话推动、钩子前置；不写大纲、不写写作说明。
4. 上下文只包含作者本书的资料，严禁虚构与设定冲突的能力/角色/规则。
5. **字数必须达到用户消息里给出的「本章目标字数」**（允许 ±10%）。字数是硬要求，
   不是建议：写不够会被打回重写。宁可多写场景与对话，也不要用一句话概括
   （"他想了想，决定动手"）把情节赶完——那会同时拉低信息密度与字数。
6. 同时输出结构化自评：钩子强度、信息密度（1-5）、是否贴合细纲与偏差点。
"""


def context_brief(pack: ContextPack) -> str:
    """把 ContextPack 渲染为只含本书信息的 prompt 文本（无参考书、无源路径）。"""
    lines = ["【本书已确认设定】"]
    if pack.positioning is not None:
        p = pack.positioning
        lines.append(
            f"题材：{p.genre}；读者：{p.audience}；卖点：{p.core_premise}；"
            f"目标基调：{p.tone_target}"
        )
        if p.selling_points:
            lines.append("差异化卖点：" + "；".join(p.selling_points))
    if pack.protagonist is not None:
        hero = pack.protagonist
        lines.append(
            f"主角：{hero.name}（{hero.personality}）；欲望：{hero.desire}；"
            f"缺陷：{hero.flaw}；手段：{hero.signature_ability}；出身：{hero.background}"
        )
    if pack.golden_finger is not None:
        finger = pack.golden_finger
        lines.append(
            f"金手指：{finger.name}（{finger.form}）——{finger.ability}；"
            f"限制代价：{finger.cost_limitation}；成长：{finger.growth_path}"
        )

    target = pack.chapter_outline
    lines.append(
        f"\n【本章细纲·第{target.order}章《{target.title}》】"
        f"\n核心事件：{target.core_event}"
        f"\n开篇钩：{target.opening_hook or '（细纲未指定）'}"
        f"\n章尾钩：{target.ending_hook or '（细纲未指定）'}"
    )
    if pack.target_chars > 0:
        # 字数要求必须显式写进 prompt：闸门按 target_chars 的 0.85-1.15 判硬门，
        # prompt 不说的话模型只会按自己的习惯写（实测真实模型 1000-1500 字），
        # 于是每次都被判"偏短"，重写两遍还是偏短——用户看到的是"第 1 章 repair"。
        #
        # 区间必须取自 gates.word_band，不能在这里另算一套：原来这里写 ±10%
        # （2340-2860），闸门判 0.85/1.15（2210-2989）——模型被要求写 2340 以上，
        # 而闸门 2210 就放行。两套带宽意味着 prompt 在骗模型，判据只能有一处。
        low, high = word_band(pack.target_chars)
        lines.append(
            f"\n【本章目标字数】{pack.target_chars} 个汉字（可接受 {low}-{high} 字）。"
            f"这是硬要求：不足 {low} 字会被打回重写。"
        )

    lines.append(style_card(pack.target_chars))

    if pack.previous_summary:
        lines.append(f"\n【前一章摘要】（来源：{pack.previous_summary_source}）\n{pack.previous_summary}")
    if pack.retrieved:
        lines.append("\n【本书相关回顾】")
        for item in pack.retrieved:
            lines.append(f"- [{item.doc_type}] {item.title}：{item.snippet}".rstrip("："))
    if pack.open_foreshadows:
        lines.append("\n【尚未回收的伏笔（写作时保持一致，勿遗忘）】")
        for debt in pack.open_foreshadows:
            lines.append(f"- 第{debt.opened_order}章埋下：{debt.description}")
    if pack.repair_feedback:
        # 带证据的修复重写：把上一版没过硬门的具体原因回灌，
        # 而不是让它再写一遍——重写不给理由，只会换个方式犯同样的错。
        lines.append("\n【上一版未通过硬门，必须逐条修正】")
        for reason in pack.repair_feedback:
            lines.append(f"- {reason}")
    return "\n".join(lines)


def style_card(target_chars: int = 0) -> str:
    """笔法约束卡：只写有实测分布的几条，每条附一对反例。

    为什么需要它：本函数加入之前，``context_brief`` 给模型的全部信息是「设定 + 细纲
    + 字数 + 前情 + 伏笔」，**没有一条关于怎么写**。模型只能退回训练分布的中心——
    统计意义上最"安全"的文学腔，也就是读者说的 AI 味。

    为什么是这几条：把本机 67 本真实网文 5829 章与 AI 章节逐项对比（按书取中位，
    再比人类跨书 SD），只有下列指标能把两边分开且误杀人类 ≤5%；「句子太短」「长句
    太少」「比喻太多」在按书复核后都不成立（人类作者句均字从 14.6 到 35.6 都有人写），
    所以这里一条都不提。密度类判据的单位是「每千汉字」，所以这里必须按
    ``target_chars`` 折算成整章预算再说给模型——直接写 2.85 它不知道在说什么。
    """
    limits = _style_limits()
    budget = target_chars if target_chars > 0 else 2600

    def total(metric: str, floor: int = 1) -> int | None:
        """把「每千字密度上限」折成整章允许的绝对次数。"""
        per_k = limits.get(metric)
        if per_k is None:
            return None
        return max(floor, int(round(per_k * budget / 1000)))

    dash = total("破折号密度")
    body = total("身体外化密度")
    clause = limits.get("分句均字")
    single = limits.get("单句段占比")
    para = limits.get("段均句数")

    lines = ["\n【笔法约束（数字来自 67 本真实网文 5829 章的实测分布，不是口味偏好）】"]
    if single is not None:
        lines.append(
            f"1. 分段：至少 {single:.0%} 的段落只有一句话，一句话就换行。"
            "真实网文靠空行制造停顿；把三四句挤进一段会把节奏压平。"
        )
    if dash is not None:
        lines.append(
            f"2. 破折号「——」：整章不超过 {dash} 个。"
            "破折号不是「补充说明」的万能工具，用多了会把动作和解释焊在一起。"
        )
    if body is not None:
        lines.append(
            f"3. 情绪：优先直接说（「他有点发怵」），"
            f"胸口/喉咙/指尖这类身体反应整章不超过 {body} 处。"
        )
    if clause is not None:
        lines.append(
            f"4. 断句：逗号之间的平均字数不低于 {clause:.0f} 字。"
            "别把一句话切成一串四个字的碎片——那是均匀的鼓点，不是人说话。"
        )
    if para is not None:
        lines.append(
            "5. 结尾：用动作或对话收尾，不要总结、升华、点题。"
            f"（顺带：单段平均句数别超过 {para:.1f} 句。）"
        )
    lines.append(
        "6. 允许「平」的句子存在：推进动作和信息时不要每句都加比喻、身体反应或"
        "精确读数。一章里必须有一部分句子是老实交代，全章都在描写就已经是 AI 味。"
    )
    lines.append(
        "7. 比喻最多 3 个，且不要都取痛觉与器械（钝刀、针、冰水、磨砂玻璃、"
        "橡皮擦）——真实作者会用「像扛一根扁担」这种生活化的喻体。"
    )
    lines.append(
        "\n反例（左边是最容易写出来的那种，右边是同一个意思的人类写法）：\n"
        "- 差：他笑了一下——那种没什么温度的笑，嘴角动了，眼睛没动。\n"
        "  好：他笑了一下，眼睛没动。\n"
        "- 差：喉咙发紧，指尖发凉，胸口像压了块石头。\n"
        "  好：他有点发怵，捏着合同没说话。\n"
        "- 差：他低头。看着合同。月薪四千。试用期三个月。违约金三十万。\n"
        "  好：他低头把合同翻到第二页，月薪四千，试用期三个月，违约金三十万。\n"
        "- 差：他站起来，走到窗边，看着楼下的车流，心里忽然明白了什么。这座城市"
        "从来不缺努力的人，缺的是被人看见的机会。\n"
        "  好：他站起来走到窗边，楼下的车流一动不动。「签不签？」王振在背后问。"
    )
    return "\n".join(lines)



def _style_limits() -> dict[str, float]:
    """从笔法基线读「人类典型值」；读不到就返回空字典（prompt 少一段也不能炸）。"""
    try:
        from ndecon.quality.style import rule_limits

        return rule_limits("prompt")
    except Exception:  # noqa: BLE001 - 基线文件缺失不该毁掉整章生成
        return {}


def _chapter_output_spec() -> dict:
    """返回章节生成 JSON 的结构说明（消息的一部分，不参与类型校验）。"""
    return {
        "content": "本章正文，多段中文，段落间用 \\n\\n 分隔；不要输出章题行",
        "review": {
            "hook_strength": "1-5 整数",
            "info_density": "1-5 整数",
            "outline_followed": "布尔值",
            "deviations": ["与细纲不符之处；完全贴合时为空数组"],
            "notes": "一句评语",
        },
    }


def chapter_user_message(pack: ContextPack) -> str:
    """组装章节生成的用户消息。"""
    return (
        f"{context_brief(pack)}\n\n"
        f"请为第 {pack.chapter_order} 章撰写正文草稿并输出 JSON：\n"
        + json.dumps(_chapter_output_spec(), ensure_ascii=False)
    )


class ChapterWriter(Protocol):
    """章节正文生成器协议。"""

    model_id: str
    prompt_version: str

    def write_chapter(self, pack: ContextPack) -> ChapterWriting:
        """基于上下文包产出一章正文候选与结构化自评。"""
        ...


class FakeChapterWriter:
    """离线确定性章节占位器：把细纲要素拼成示意正文，明确标记不可直接使用。

    它必须能**产出足够长的正文**（默认对齐 ``CHAPTER_TARGET_CHARS_DEFAULT``）：
    否则离线路径永远过不了字数硬门，整条"生成→闸门→摘要→提交"链路就只能
    靠真实 API 验证——既贵又不可复现。占位正文按固定句式池确定性展开，
    同一章每次生成完全相同（可做断言），不同章之间句式错位（可测新鲜度）。
    """

    model_id = "rule:fake-chapter"
    prompt_version = CHAPTER_PROMPT_VERSION

    # 确定性句式池。**每个池的长度必须互不相同**：句式周期是各池长度的最小公倍数，
    # 池长全相同会让场景句以该长度循环，直接撞上 repeat_span 硬门（12 字片段 ≥3 次）。
    #
    # 池子还必须**足够大**：跨章新鲜度现在是硬门（与任一近章 Jaccard < 0.30，
    # 近 20 章并集覆盖率 < 0.45）。原先 12/13/17/19/23/11 的小池实测相邻章
    # Jaccard = 0.71、覆盖率 = 0.83，离线连写第二十章就会被自己的闸门拦下。
    # 扩到 30/30/34/32/30/15 后实测 Jaccard = 0.036、覆盖率 = 0.069。
    _SUBJECTS = (
        "他", "她", "老人", "少年", "掌柜", "差役", "更夫", "船工", "药童", "妇人",
        "伙计", "书生", "铁匠", "挑夫", "脚夫", "驿卒", "绣娘", "镖师", "道人", "乞丐",
        "游侠", "匠人", "寡妇", "医者", "盐商", "捕快", "书吏", "童仆", "账房", "货郎",
    )
    _VERBS = (
        "推开", "掩上", "搁下", "收起", "拂开", "扯紧", "踩过", "绕开", "扶起", "叠好",
        "挑亮", "按灭", "递过", "掀开", "抵住", "翻检", "拍净", "塞进", "拖走", "架起",
        "钉死", "拧断", "裹紧", "铺开", "挑开", "按平", "抱走", "扛起", "抬过", "塞回",
    )
    _OBJECTS = (
        "半扇木门", "铜制台灯", "一卷旧图", "三枚铜钱", "檐下灯笼", "生锈钥匙",
        "粗糙麻绳", "褪色信笺", "半页账册", "一只陶碗", "油纸伞", "竹编提篮",
        "青布包袱", "铁皮匣子", "断齿木梳", "缺口瓷碟", "发潮账本", "锈铁锁",
        "裂口陶罐", "旧铜镜", "残破匾额", "湿草席", "褪色旗", "断柄锄",
        "磨秃的笔", "卷边地图", "缺角印章", "半截蜡烛", "竹节烟杆", "破渔网",
        "枯枝扫帚", "斑驳算盘", "松动的门闩", "带泥的靴",
    )
    _PLACES = (
        "走廊尽头", "靠窗矮桌", "结霜台阶", "后院井边", "青苔墙根", "阁楼梯口",
        "药铺柜台", "渡口石阶", "灶间角落", "柴房门口", "戏台后台", "河堤柳下",
        "祠堂偏厅", "当铺里屋", "磨坊门前", "井台边上", "晒谷场角", "廊下栏杆",
        "柴垛背后", "驿道拐角", "山神庙前", "旧宅天井", "酒肆后厨", "铁匠铺炉边",
        "码头木栈", "荒田田埂", "县衙照壁", "钟楼下", "破庙廊下", "老槐树底",
        "塘边石埠", "染坊后院",
    )
    _DETAILS = (
        "灯芯爆响", "铜面模糊", "边角起毛", "指腹留印", "光被晃碎", "落灰很厚",
        "薄锈泛黄", "字迹淡去", "木纹裂开", "水汽凝结", "绳索发脆", "漆皮剥落",
        "釉面缺角", "墨迹洇开", "铁器发涩", "布面起皱", "纸角卷起", "石面冰凉",
        "木屑落了一地", "霜花结了一层", "铜绿沾手", "线头散开", "边沿磕掉一块",
        "蛛网挂着灰", "苔痕爬上砖缝", "麻绳勒出沟", "炭灰积成堆", "裂痕顺着木纹走",
        "油渍印在袖口", "碎瓦散在脚边",
    )
    _TAILS = (
        "他没说话", "她也没回头", "屋里静了片刻", "外面起了风",
        "远处有人应了一声", "谁都没有再看它", "灯火又暗了一格", "檐水漏得更急",
        "天光压得很低", "门轴响了一下", "空气里有股腥味", "脚步声停在门外",
        "窗纸抖了一下", "烛芯偏了半寸", "风声灌进廊下",
    )
    _TEMPLATES = (
        "{s}{v}{o}，{p}{d}。",
        "{s}在{p}{v}{o}，{d}。",
        "{p}的{o}被{s}{v}，{d}。",
        "{s}{v}{o}。{d}，{t}。",
        "{s}{v}了{o}以后，{p}{d}。",
        "{d}，{s}把{o}{v}。",
        "{s}把{o}{v}到{p}，{d}。",
        "{p}那头，{s}{v}{o}，{t}。",
        "{s}没{v}那{o}，只{d}。",
        "{o}还搁在{p}，{d}，{t}。",
        "{s}绕过{p}的{o}，{d}。",
        "{p}，{o}被{s}{v}，{d}，{t}。",
    )

    def __init__(
        self,
        target_chars: int = CHAPTER_TARGET_CHARS_DEFAULT,
        seed_offset: int = 0,
    ) -> None:
        """``target_chars`` 只影响占位正文长度，不改变结构与自评。

        ``seed_offset`` 给并行候选留的口子：同一份上下文包下本生成器是确定性的
        （测试要逐字断言），不加偏移时 N 个候选会逐字相同，"挑最优"就成了摆设，
        而且它们互为近章，跨章新鲜度会把兄弟候选判成「换个说法重写一遍」。
        偏移只错开场景句序列，开篇钩/核心事件/章尾钩仍忠于细纲。
        """
        if target_chars < 1:
            raise ValueError("target_chars 必须为正整数")
        if seed_offset < 0:
            raise ValueError("seed_offset 不能为负")
        self.target_chars = target_chars
        self.seed_offset = seed_offset

    @staticmethod
    def _strip_wrappers(text: str) -> str:
        """剥掉摘要里递归套娃的结构前缀。

        摘要由 provider 从正文开头截取，而正文开头又是「（前情承接）上一章：…」，
        于是每章的摘要都把上一章的摘要整段套进去，第 N 章攒出 N-1 层前缀。
        这既是重复片段（撞 repeat_span），也会让上下文越滚越长。
        真模型回显包装时同理，所以在写入侧就剥掉。
        """
        cleaned = text
        for _ in range(20):
            before = cleaned
            cleaned = re.sub(r"^（(?:前情承接|开篇钩|核心事件|伏笔照应|章尾钩)）", "", cleaned)
            cleaned = re.sub(r"^上一章[:：]", "", cleaned)
            cleaned = cleaned.lstrip("：: 　\n")
            if cleaned == before:
                break
        return cleaned

    def _scene_sentences(self, count: int, seed: int) -> list[str]:
        """确定性展开场景句：按章播种的独立序列，保证跨章不雷同。

        早期实现用 `position = index + (order-1)*37` 做线性错位，但一章要展开
        一百多句，37 的步长让相邻两章大半句子落在同一区间——实测相邻章 Jaccard
        0.71、近 20 章并集覆盖率 0.83，直接撞上新鲜度硬门。步长调大只是把冲突
        推到十几章之后（池周期有限），治不了根。

        改成每章一个独立伪随机序列：同章每次生成逐字节相同（可断言），异章之间
        几乎不重合。代价是不再"错位可预测"，但那是测试的偏好，不是正确性要求。
        """
        rng = random.Random(seed)
        out: list[str] = []
        for _ in range(count):
            template = self._TEMPLATES[rng.randrange(len(self._TEMPLATES))]
            out.append(
                template.format(
                    s=self._SUBJECTS[rng.randrange(len(self._SUBJECTS))],
                    v=self._VERBS[rng.randrange(len(self._VERBS))],
                    o=self._OBJECTS[rng.randrange(len(self._OBJECTS))],
                    p=self._PLACES[rng.randrange(len(self._PLACES))],
                    d=self._DETAILS[rng.randrange(len(self._DETAILS))],
                    t=self._TAILS[rng.randrange(len(self._TAILS))],
                )
            )
        return out

    def write_chapter(self, pack: ContextPack) -> ChapterWriting:
        """按开篇钩→核心事件→场景展开→章尾钩的结构生成确定性占位正文。"""
        target = pack.chapter_outline
        paragraphs = [
            PLACEHOLDER_BANNER,
        ]
        if pack.previous_summary:
            paragraphs.append(
                f"（前情承接）上一章：{self._strip_wrappers(pack.previous_summary)}。本章紧接其后展开。"
            )
        paragraphs.extend(
            [
                f"（开篇钩）{target.opening_hook or '本章以一个反常场景开场。'}"
                "主角在熟悉的日常里被猛地推了一把，局势不容他慢慢理解。",
                f"（核心事件）{target.core_event}。"
                "他按照自己一贯的方式先算代价再动手，局面在试探与反击之间来回翻转。",
            ]
        )
        if pack.open_foreshadows:
            # 去重后再拼接：细纲章尾钩若被模板复用（多章共用一句），台账里就会有
            # 多条同文记录，原样连列会立刻撞 repeat_span（同一 12 字片段 ≥3 次）。
            # 去重是写入侧的正当防御，不是放宽闸门。
            seen: list[str] = []
            for item in pack.open_foreshadows:
                description = item.description.strip()
                if description and description not in seen:
                    seen.append(description)
                if len(seen) >= 5:
                    break
            if seen:
                debt = "；".join(seen)
                paragraphs.append(f"（伏笔照应）行文没有丢下旧线：{debt}。")

        # 场景展开：补足字数到目标带内（CJK 计数与闸门同口径）
        # seed_offset 按 CANDIDATE_SEED_STRIDE 错开，不用 ±1 那种小步：
        # 同一章内部自己也会以 12 为步长往上加，小步会和自己的场景序列重叠，
        # 候选之间又回到"逐字相同"。1000 的间隔让 6 个候选（0..5000）全部落在
        # 相邻章步长 CHAPTER_SCENE_SEED_STEP(7919) 之内，不会撞到别的章。
        offset = (
            CHAPTER_SCENE_SEED
            + (target.order - 1) * CHAPTER_SCENE_SEED_STEP
            + self.seed_offset * CANDIDATE_SEED_STRIDE
        )
        cjk_now = len(re.findall(r"[\u4e00-\u9fff]", "\n\n".join(paragraphs)))
        need = max(0, self.target_chars - cjk_now)
        scene_count = 0
        while need > 0 and scene_count < 400:
            batch = self._scene_sentences(12, offset + scene_count)
            scene_count += 12
            paragraphs.append("".join(batch))
            need -= len(re.findall(r"[\u4e00-\u9fff]", "".join(batch)))

        paragraphs.append(
            f"（章尾钩）{target.ending_hook or '新的异常在结尾处露头。'}"
            "问题只解决了一半，更大的压力已经到了门口。"
        )
        content = "\n\n".join(paragraphs)
        review = ChapterSelfReview(
            hook_strength=3,
            info_density=3,
            outline_followed=True,
            deviations=[],
            notes="Fake 占位文本：结构对齐细纲，语言与细节需作者重写。",
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )
        return ChapterWriting(content=content, review=review)


class OpenAICompatChapterWriter:
    """真实章节生成器：复用 OpenAICompatProvider 的 JSON 通道、重试与错误分类。"""

    model_id: str
    prompt_version = CHAPTER_PROMPT_VERSION

    def __init__(
        self,
        provider: OpenAICompatProvider,
        temperature: float | None = None,
    ) -> None:
        """包装已配置 key 的拆书 provider（与骨架生成共用鉴权/传输栈）。

        ``temperature`` 只在并行候选里传（每候选一档，拉开措辞）；留空沿用
        provider 的默认 0.2，重复生成才可复现。
        """
        self._provider = provider
        self._temperature = temperature
        self.model_id = f"openai-compat:{provider.model}"

    def write_chapter(self, pack: ContextPack) -> ChapterWriting:
        """请求章节 JSON 并严格校验正文与自评；非法结构按响应错误处理。"""
        messages = [
            {"role": "system", "content": CHAPTER_SYSTEM_MESSAGE},
            {"role": "user", "content": chapter_user_message(pack)},
        ]
        # 同包复用内部 JSON 通道，避免重复造传输栈。
        # **不设 max_tokens**：推理型模型会把预算全花在 reasoning 上，正文一个字
        # 都留不下（实测 sensenova 与 aliyun 各 6224 token 全进 reasoning_tokens、
        # text_tokens=0、finish_reason=length）。预算越大推理也越长，这是模型的
        # 自由，算不准；不设上限时上游按模型默认放行，推理完还有空间写正文。
        data = self._provider._chat_json(  # noqa: SLF001
            messages, temperature=self._temperature
        )
        try:
            content = str(data["content"]).strip()
            if not content:
                raise ValueError("content 为空")
            review_data = dict(data["review"])
            # 来源字段以本地 writer 为准，丢弃模型可能回传的同名字段防冲突
            review_data.pop("model_id", None)
            review_data.pop("prompt_version", None)
            review = ChapterSelfReview(
                **review_data,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError(f"章节生成 JSON 不合法：{exc}") from exc
        return ChapterWriting(content=content, review=review)
