"""单章笔法体检：把「AI 味」量成 `novel-project` 语料实测的人类分位（只读度量）。

与 :mod:`ndecon.quality.fingerprint` 的分工
------------------------------------------
``fingerprint`` 数的是**可疑词句的出现次数**（破折号几个、句号几个），阈值来自
第三方工程实践的博客，作者要自己校准。这里的阈值不同：它们是从本机
``novel-project`` 里 67 本 5829 章真实网文实测出来的**分位线**，每条都带留出集
（20 本未参与定阈值的书）回放出来的误杀率。数据在 ``style_baseline.json``，
再生成脚本见仓库 ``scripts/`` 或调参记录 ``docs/``。

为什么这些判据只做提示、不做硬门
--------------------------------
留出集 6770 章的实测：单条判据的误杀率 2.2%–5.1%，五条联合「越界 >1 条」误杀
4.3%。对**建议重写**而言这个精度可用；对**判定这一章不能要**而言完全不够——
``fingerprint`` 模块头部当年就是因为「人类小说里 em_dash=30、段落句号=329 的章节
真实存在」才把单章阈值全部降级成 warn。这里沿用同一条纪律：
:meth:`StyleReport.feedback` 只把越界项作为**上一版的具体毛病**回灌 prompt，
让模型定向重写一次；判定仍由 :func:`ndecon.quality.gates.check_chapter` 的硬门决定。

为什么不做词表替换
------------------
StoryScope（arXiv:2604.03136，61,608 篇）实测：专业润色系统 LAMP 清掉全部
cliché 与紫散文后，AI 检测率只从 95.5% 降到 93.9%。改词无效，改结构才有效，
所以这里测的全是节奏量（分句长度、段落句数、单句成段比例），不测禁用词。

调用方
------
- :mod:`ndecon.creation.txn`：一章通过硬门后跑一次，越界项写进下一轮的
  ``repair_feedback``；
- :mod:`ndecon.panel.server`：``GET /api/projects/{id}/chapters/{n}/style``；
- CLI：``python -m ndecon.cli style <章文件>``。
"""

from __future__ import annotations

import json
import re
import statistics as st
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

STYLE_VERSION = "style-v1"

# 判据口径与建基线脚本必须逐字一致，否则阈值和实测值不在同一个坐标系。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SENT_RE = re.compile(r"[^。！？\n]+[。！？]?")
_QUOTE_RE = re.compile(r"[「“]([^」”]*)[」”]")
_CLAUSE_RE = re.compile(r"[，、；：。！？\n]")
_SIMILE_RE = re.compile(r"像|仿佛|宛如|如同|似的|好比|像是")
_PSYCH_RE = re.compile(
    r"觉得|感到|意识到|明白|知道|想起|记得|心里|心中|心底|内心|情绪|心情|念头"
)
_PARTICLE_RE = re.compile(r"[啊吧呢嘛呗哦呀哈嘿唉哎嘶嘞]")
_DASH_RE = re.compile(r"——|—|―")
_ELLIPSIS_RE = re.compile(r"……|\.\.\.")
_EMO_RE = re.compile(
    r"害怕|怕|生气|恼|怒|高兴|开心|难过|悲|伤心|紧张|慌|急|委屈|尴尬|羞愧|愧疚|"
    r"兴奋|激动|失望|绝望|不甘|烦躁|厌烦|无奈|踏实|安心|发怵|心虚|得意|痛快"
)
_BODY_RE = re.compile(
    r"胸口|喉咙|嗓子|后颈|后背|胃里|太阳穴|指尖|指节|手心|掌心|脊背|头皮|鼻腔|"
    r"眼眶|牙关|下颌|肋骨|膝盖|肩胛"
)
_BANG_RE = re.compile(r"[！!]")
_NUM_READ_RE = re.compile(r"[零一二三四五六七八九十百千万亿]{2,}|[0-9]+(?:\.[0-9]+)?")
_EXPLAIN_RE = re.compile(r"是因为|之所以|原来|这意味着|也就是说|换句话说|仿佛预示|注定")
_TERMINAL_RE = re.compile(r"[。！？…”」』）)!?~～]$")
_NOISE_RE = re.compile(r"^[=\-_*#~\s]{4,}$")

# —— 对白（言语）口径 ——
# 这些正则与 ``_scratch/dialogue_probe.py`` 逐字一致：阈值是按同一套口径量出来的，
# 差一个字符，阈值和实测值就不在同一个坐标系里。
_DLG_EXPLAIN_RE = re.compile(r"是因为|之所以|原来|这意味着|也就是说|换句话说|仿佛预示|注定|其实|所以|因为")
_DLG_POLITE_RE = re.compile(r"请|麻烦|谢谢|不好意思|对不起|劳驾")
_DLG_ADDRESS_RE = re.compile(r"哥|姐|叔|姨|爷|奶|老板|老大|老师|师父|师兄|师弟|队长|教练")
_DLG_PRONOUN2_RE = re.compile(r"你|您|你们")
_DLG_TAIL_RE = re.compile(r"[。！？…—」”]$")
_DLG_SAY_RE = re.compile(r"说|问|道|答|喊|叫|嚷|嘟囔|低声|开口")

# 对白指标只在「这一章确实在写对话」时才有分布意义。四条门槛与建基线脚本一致；
# 不达标的章不参与分布，也**不因此判通过**——没写对白的章本来就不该被对白判据评价。
_DIALOGUE_MIN_PARAS = 8
_DIALOGUE_MIN_QUOTES = 4
_DIALOGUE_MIN_CJK = 500
_DIALOGUE_MIN_DIALOGUE_CJK = 100

# 太短的文本量不出稳定分布：一段 200 字的对话，分句均字和段均句数都没意义。
MIN_STYLE_CHARS = 300
# 超过这个长度的「段」多半是章节切分失败或整段没分段，段均句数会被它拖爆。
_MAX_PARA_CHARS = 400


@dataclass(slots=True)
class StyleFinding:
    """一条越界：指标、实测值、人类分位线、证据例句。"""

    metric: str
    value: float
    limit: float
    bound: str
    direction: str
    percentile: float
    evidence: str = ""

    def as_detail(self) -> str:
        """给 prompt 用的一句话，必须带具体数字和改法。"""
        human = (
            f"人类中位 {self.percentile:.1f} 分位"
            if self.percentile
            else ""
        )
        arrow = "高于" if self.direction == "hi" else "低于"
        return (
            f"{self.metric} {self.value:.2f} {arrow}人类 {self.bound} 线 {self.limit:.2f}"
            f"（{human}）"
        )


@dataclass(slots=True)
class StyleReport:
    """一章的笔法体检结果。只报告，不改写。"""

    metrics: dict[str, float] = field(default_factory=dict)
    findings: list[StyleFinding] = field(default_factory=list)
    cjk_chars: int = 0
    version: str = STYLE_VERSION
    # 对白判据只在「这一章确实在写对话」时才参与比对（见 measure_dialogue）。
    # 界面必须能分清「对白三条都过了」和「这章没对白、压根没比」——
    # 否则没写对话的章看起来比写了对话的章更干净。
    dialogue_checked: bool = False
    dialogue_metrics: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """没有越界项。注意：``passed=False`` 不代表不能发布，只代表建议重写。"""
        return not self.findings

    @property
    def rule_count(self) -> int:
        """本次体检比对的判据条数（0 表示文本太短、根本没比）。"""
        return self._rule_count

    @property
    def score(self) -> float:
        """越界项占比（0-1），越小越接近人类分布。"""
        if not self._rule_count:
            return 0.0
        return round(len(self.findings) / self._rule_count, 4)

    _rule_count: int = 0

    def feedback(self) -> list[str]:
        """回灌给作者的修复清单（可直接塞进 ``ContextPack.repair_feedback``）。"""
        return [f.as_detail() for f in self.findings]


@lru_cache(maxsize=1)
def _baseline() -> dict[str, object]:
    path = resources.files("ndecon.quality").joinpath("style_baseline.json")
    return json.loads(path.read_text(encoding="utf-8"))


def baseline_meta() -> dict[str, object]:
    """基线元信息：书数/章数/版本，供文档与接口如实标注来源。

    ``dialogue`` 子块单独报家门：它的语料口径与节奏不同（只统计写了对白的章），
    混进主表会让「本基线来自 67 本 5829 章」这句话在对白判据上变成假话。
    """
    data = _baseline()
    dialogue = data.get("dialogue") if isinstance(data.get("dialogue"), dict) else {}
    return {
        "version": data.get("version"),
        "books": data.get("books"),
        "chapters": data.get("chapters"),
        "holdout_books": data.get("holdout_books"),
        "holdout_chapters": data.get("holdout_chapters"),
        "rules": list(getattr(data.get("rules"), "keys", lambda: [])()),  # type: ignore[union-attr]
        "dialogue": {
            "version": dialogue.get("version"),
            "books": dialogue.get("books"),
            "chapters": dialogue.get("chapters"),
            "holdout_books": dialogue.get("holdout_books"),
            "holdout_chapters": dialogue.get("holdout_chapters"),
            "ai_chapters": dialogue.get("ai_chapters"),
            "rules": list(getattr(dialogue.get("rules"), "keys", lambda: [])()),  # type: ignore[union-attr]
        }
        if dialogue
        else None,
    }


def dialogue_rule_limits(mode: str = "judge") -> dict[str, float]:
    """对白判据的两档刻度，语义与 :func:`rule_limits` 相同。"""
    rules = _dialogue_rules()
    key = "prompt" if mode == "prompt" else "limit"
    out: dict[str, float] = {}
    for metric, rule in rules.items():
        if key in rule:
            out[metric] = float(rule[key])
        elif "limit" in rule:
            out[metric] = float(rule["limit"])
    return out


def rule_limits(mode: str = "judge") -> dict[str, float]:
    """判据阈值表：``{指标: 数值}``。

    ``mode="judge"`` 给运行时体检用——取判罚线（人类 p95 / p10 / p5 这类外侧分位），
    越界才算「这一版有明显毛病」。

    ``mode="prompt"`` 给 :func:`ndecon.creation.chapters.style_card` 用——取人类
    **典型值**（p75 / p50），让模型瞄准常态而不是紧贴判罚线。

    两个刻度来自同一份基线：prompt 瞄准常态、判罚留在外侧，这叫「瞄准紧、判罚松」。
    如果 prompt 直接写判罚线，模型会贴着上限写，体检必然命中——那就成了自己制造违规。
    """
    rules = _baseline()["rules"]
    if not isinstance(rules, dict):
        return {}
    key = "prompt" if mode == "prompt" else "limit"
    out: dict[str, float] = {}
    for metric, rule in rules.items():
        if key in rule:
            out[metric] = float(rule[key])
        elif "limit" in rule:
            out[metric] = float(rule["limit"])
    return out




def cjk_count(text: str) -> int:
    return len(_CJK_RE.findall(text))


def paragraphs_of(text: str) -> list[str]:
    """一行一段，未以句末标点收尾的行并入下一行（兼容被硬折行的 txt）。

    不能按空行切：大量网文 txt 是单换行分段，按空行切会把一整章切成一坨，
    段均句数、单句段占比两个判据当场失效——建基线时踩过这个坑。
    """
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln and not _NOISE_RE.match(ln)]
    out: list[str] = []
    buf = ""
    for line in lines:
        buf = f"{buf}{line}" if buf else line
        if _TERMINAL_RE.search(buf) or len(buf) >= _MAX_PARA_CHARS:
            out.append(buf)
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _quantile(xs: list[float], t: float) -> float:
    ordered = sorted(xs)
    idx = min(len(ordered) - 1, max(0, int(round(t * (len(ordered) - 1)))))
    return float(ordered[idx])


def measure(text: str) -> dict[str, float]:
    """算出全部 22 项指标。文本太短时返回空字典（量出来也是噪声）。"""
    cjk = cjk_count(text)
    if cjk < MIN_STYLE_CHARS:
        return {}
    paras = paragraphs_of(text)
    if len(paras) < 3:
        return {}
    sents: list[str] = []
    for para in paras:
        for sent in _SENT_RE.findall(para):
            sent = sent.strip()
            if cjk_count(sent) >= 2:
                sents.append(sent)
    if len(sents) < 5:
        return {}

    slen = [cjk_count(s) for s in sents]
    plen = [cjk_count(p) for p in paras]
    psent = [len([s for s in _SENT_RE.findall(p) if cjk_count(s) >= 2]) for p in paras]
    clen = [cjk_count(c) for c in _CLAUSE_RE.split(text) if cjk_count(c) >= 2]
    quotes = _QUOTE_RE.findall(text)
    talking = sum(1 for p in paras if _QUOTE_RE.search(p))

    def per_k(pattern: re.Pattern[str]) -> float:
        return len(pattern.findall(text)) / cjk * 1000

    mean_s = st.mean(slen)
    long_rate = sum(1 for n in slen if n > 30) / len(slen)
    short_rate = sum(1 for n in slen if n <= 8) / len(slen)
    return {
        "句均字": mean_s,
        "句p90": _quantile([float(n) for n in slen], 0.90),
        "长句率": long_rate,
        "短句率": short_rate,
        "句长CV": (st.pstdev(slen) / mean_s) if mean_s else 0.0,
        "分句均字": st.mean(clen) if clen else 0.0,
        "段均字": st.mean(plen),
        "段p90字": _quantile([float(n) for n in plen], 0.90),
        "段均句数": st.mean(psent),
        "单句段占比": sum(1 for n in psent if n <= 1) / len(paras),
        "对话段占比": talking / len(paras),
        "对话字占比": sum(cjk_count(q) for q in quotes) / cjk,
        "比喻密度": per_k(_SIMILE_RE),
        "破折号密度": per_k(_DASH_RE),
        "心理词密度": per_k(_PSYCH_RE),
        "显式情绪密度": per_k(_EMO_RE),
        "身体外化密度": per_k(_BODY_RE),
        "叹号密度": per_k(_BANG_RE),
        "语气词密度": per_k(_PARTICLE_RE),
        "省略号密度": per_k(_ELLIPSIS_RE),
        "数字读数密度": per_k(_NUM_READ_RE),
        "解释腔密度": per_k(_EXPLAIN_RE),
    }


def measure_dialogue(text: str) -> dict[str, float]:
    """量一章的**对白**指标；这一章不像在写对话时返回空字典。

    为什么要单开一套口径：``measure`` 量的是叙述的节奏（段落几行一句、逗号隔
    多少字），对白根本不在它的坐标系里——一句「滚。」和一句「你他妈再说一遍」
    在段均句数上完全一样。人类与 AI 在**言语**上错位的维度是另一批：
    AI 的台词条条完整、句句抒情、每句都在解释、大量「你……」冲着读者说；
    人类台词多而碎、有被打断的、有废话、有误解、有答非所问。

    门槛（与建基线脚本逐字一致）：段落 ≥8、引号台词 ≥4 条、正文汉字 ≥500、
    台词汉字 ≥100。不达标的章返回空字典——**没写对白的章不参与分布，
    也不因此被判通过**；把「没对白」算成「对白判据全过」会让这套判据
    变成「少写对话就能拿满分」。
    """
    paras = paragraphs_of(text)
    if len(paras) < _DIALOGUE_MIN_PARAS:
        return {}
    quotes = [q.strip() for q in _QUOTE_RE.findall(text) if cjk_count(q) >= 1]
    if len(quotes) < _DIALOGUE_MIN_QUOTES:
        return {}
    total = cjk_count(text)
    if total < _DIALOGUE_MIN_CJK:
        return {}
    dlg_cjk = sum(cjk_count(q) for q in quotes)
    if dlg_cjk < _DIALOGUE_MIN_DIALOGUE_CJK:
        return {}

    q_lens = [cjk_count(q) for q in quotes]
    turns = len(quotes)
    mean_q = st.mean(q_lens)
    narration = _QUOTE_RE.sub("", text)
    n_sents = [s.strip() for s in _SENT_RE.findall(narration) if cjk_count(s) >= 2]
    n_mean = st.mean([cjk_count(s) for s in n_sents]) if n_sents else 0.0
    dlg_blob = "\n".join(quotes)

    def per_1k(pattern: re.Pattern[str]) -> float:
        return len(pattern.findall(dlg_blob)) / dlg_cjk * 1000

    quote_paras = [p for p in paras if _QUOTE_RE.search(p)]
    return {
        "对白条数每千字": turns / total * 1000,
        "对白句均字": mean_q,
        "对白分句均": st.mean(
            [
                len([c for c in _CLAUSE_RE.split(q) if cjk_count(c) >= 1])
                for q in quotes
            ]
        ),
        "对白句长CV": (st.pstdev(q_lens) / mean_q) if mean_q else 0.0,
        "对白极短条占比": sum(1 for n in q_lens if n <= 3) / turns,
        "对白短条占比": sum(1 for n in q_lens if n <= 6) / turns,
        "对白完整句占比": sum(1 for q in quotes if q.endswith("。")) / turns,
        "对白未收尾占比": sum(1 for q in quotes if not _DLG_TAIL_RE.search(q)) / turns,
        "对白问号占比": sum(1 for q in quotes if "？" in q or "?" in q) / turns,
        "对白省略号占比": sum(1 for q in quotes if _ELLIPSIS_RE.search(q)) / turns,
        "对白破折号占比": sum(1 for q in quotes if _DASH_RE.search(q)) / turns,
        "对白语气词密度": per_1k(_PARTICLE_RE),
        "对白解释腔密度": per_1k(_DLG_EXPLAIN_RE),
        "对白客气语密度": per_1k(_DLG_POLITE_RE),
        "对白称呼密度": per_1k(_DLG_ADDRESS_RE),
        "对白第二人称密度": per_1k(_DLG_PRONOUN2_RE),
        "对白叙述句长比": mean_q / n_mean if n_mean else 0.0,
        "对白字占比": dlg_cjk / total,
        "引号句未带说语占比": (
            sum(1 for p in quote_paras if not _DLG_SAY_RE.search(_QUOTE_RE.sub("", p)))
            / len(quote_paras)
            if quote_paras
            else 0.0
        ),
    }


def _dialogue_rules() -> dict[str, dict[str, object]]:
    block = _baseline().get("dialogue")
    rules = block.get("rules") if isinstance(block, dict) else None
    return rules if isinstance(rules, dict) else {}


def _percentile_of(value: float, metric: str, *, block: str = "") -> float:
    """把实测值折算成人类分位（线性内插），只为让报告读起来有刻度。

    ``block="dialogue"`` 时查对白那张分位表——两套指标同名不同源，
    查错表会让「对白条数 25 条/千字」被读成节奏表里的某个分位。
    """
    data = _baseline()
    bounds = data.get(block) if block else data
    percentiles = bounds.get("percentiles") if isinstance(bounds, dict) else None
    entry = percentiles.get(metric) if isinstance(percentiles, dict) else None
    if not entry:
        return 0.0
    points = [
        (entry["p1"], 1.0),
        (entry["p5"], 5.0),
        (entry["p10"], 10.0),
        (entry["p25"], 25.0),
        (entry["p50"], 50.0),
        (entry["p75"], 75.0),
        (entry["p90"], 90.0),
        (entry["p95"], 95.0),
        (entry["p99"], 99.0),
    ]
    if value <= points[0][0]:
        return 1.0
    if value >= points[-1][0]:
        return 99.0
    # points 与 points[1:] 天然差一个长度，这里的严格模式必须是 False。
    for (x0, p0), (x1, p1) in zip(points, points[1:], strict=False):
        if x0 <= value <= x1:
            if x1 <= x0:
                return p0
            return p0 + (p1 - p0) * (value - x0) / (x1 - x0)
    return 50.0


def _evidence(text: str, metric: str) -> str:
    """取一条最能说明问题的原文片段：命中该指标的那一段。"""
    patterns = {
        "破折号密度": _DASH_RE,
        "身体外化密度": _BODY_RE,
        "分句均字": None,
        "单句段占比": None,
        "段均句数": None,
    }
    if metric.startswith("对白"):
        quotes = [q.strip() for q in _QUOTE_RE.findall(text)]
        if metric == "对白破折号占比":
            for q in quotes:
                if _DASH_RE.search(q):
                    return f"「{q}」"[:60]
        if metric == "对白第二人称密度":
            for q in quotes:
                if _DLG_PRONOUN2_RE.search(q):
                    return f"「{q}」"[:60]
        if metric == "对白条数每千字":
            return f"（全章引号台词 {len(quotes)} 条）"
    pattern = patterns.get(metric)
    paras = paragraphs_of(text)
    if pattern is not None:
        for para in paras:
            if pattern.search(para):
                return para[:60]
    if metric == "段均句数":
        for para in paras:
            if len([s for s in _SENT_RE.findall(para) if cjk_count(s) >= 2]) >= 3:
                return para[:60]
    if metric == "段p90字":
        # 这条判据的意思是「长段被删光了」，所以要举最长的那个多句段当反证：
        # 人类章里它通常有六十到一百二十字。
        multi = [
            p
            for p in paras
            if len([s for s in _SENT_RE.findall(p) if cjk_count(s) >= 2]) >= 2
        ]
        if multi:
            longest = max(multi, key=cjk_count)
            return f"（最长多句段只有 {cjk_count(longest)} 字）{longest[:50]}"
        return "（整章没有一个两句话以上的段落，长段被全部删掉）"
    if metric == "单句段占比":
        return "（整章多数段落是多句连排，没有用空行分句）"
    if metric == "分句均字":
        for clause in _CLAUSE_RE.split(text):
            if 2 <= cjk_count(clause) <= 4:
                return clause.strip()[:40]
    return ""


def inspect_style(text: str) -> StyleReport:
    """跑一遍单章笔法体检。文本太短时返回空报告（``cjk_chars`` 仍如实填）。

    两套判据一起比：**节奏**（段落/句子怎么排）与**对白**（台词怎么说）。
    对白那三条只在 ``measure_dialogue`` 有值时才参与——没写对话的章不背对白判据，
    也不因为「没写」而拿满分。
    """
    cjk = cjk_count(text)
    metrics = measure(text)
    if not metrics:
        return StyleReport(metrics={}, findings=[], cjk_chars=cjk, _rule_count=0)

    findings: list[StyleFinding] = []
    rules = _baseline().get("rules")
    if isinstance(rules, dict):
        findings.extend(
            _collect_findings(text, metrics, rules, block="")
        )
    dialogue_metrics = measure_dialogue(text)
    dialogue_rules = _dialogue_rules() if dialogue_metrics else {}
    if dialogue_metrics and dialogue_rules:
        findings.extend(
            _collect_findings(text, dialogue_metrics, dialogue_rules, block="dialogue")
        )
    findings.sort(key=lambda f: (f.direction == "lo", -abs(f.value - f.limit)))
    return StyleReport(
        metrics={k: round(v, 4) for k, v in {**metrics, **dialogue_metrics}.items()},
        findings=findings,
        cjk_chars=cjk,
        _rule_count=(len(rules) if isinstance(rules, dict) else 0)
        + len(dialogue_rules),
        dialogue_checked=bool(dialogue_metrics),
        dialogue_metrics={k: round(v, 4) for k, v in dialogue_metrics.items()},
    )


def _collect_findings(
    text: str,
    metrics: dict[str, float],
    rules: dict[str, dict[str, object]],
    *,
    block: str,
) -> list[StyleFinding]:
    """按一张规则表比一遍。``block`` 只影响分位查哪张表。"""
    out: list[StyleFinding] = []
    for metric, rule in rules.items():
        if metric not in metrics:
            continue
        value = metrics[metric]
        limit = float(rule["limit"])
        direction = str(rule["dir"])
        over = value > limit if direction == "hi" else value < limit
        if not over:
            continue
        out.append(
            StyleFinding(
                metric=metric,
                value=round(value, 4),
                limit=limit,
                bound=str(rule["bound"]),
                direction=direction,
                percentile=_percentile_of(value, metric, block=block),
                evidence=_evidence(text, metric),
            )
        )
    return out
