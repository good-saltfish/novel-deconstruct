"""章事务（txn）测试：闸门、修复重写、提交、回滚、断点续跑。

这些测试是"全自动连跑"的地基——它们必须证明：
1. 过硬门的章节能落盘，且摘要一并落盘（否则下一章检索不到）；
2. 不过硬门的章节**不会**污染工作区（不留半提交）；
3. run 记录如实反映尝试次数与判定；
4. resume 只补没提交的章，不重写已提交的章。
"""

from __future__ import annotations

import random
import re
from collections import Counter

from ndecon.creation import txn
from ndecon.creation.chapters import FakeChapterWriter
from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
)
from ndecon.creation.runlog import ChapterRun, RunLog, new_run_id
from ndecon.quality.gates import check_chapter
from ndecon.workspace.models import CreationProject, ProjectMeta
from ndecon.workspace.store import WorkspaceStore


def _store(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path)


def _project(store: WorkspaceStore, chapters: int = 3) -> CreationProject:
    """建一个已通过闸门的创作项目（部件都是 human-confirmed，上下文包非空）。"""
    project = CreationProject(
        meta=ProjectMeta(
            id="creation-txn-1",
            kind="creation",
            title="事务测试",
            created_at=store.now_iso(),
        ),
        genre="末世",
        draft=CreationDraft(
            positioning=Positioning(genre="末世", core_premise="一句话"),
            protagonist=ProtagonistProfile(name="陆沉"),
            golden_finger=GoldenFingerSpec(name="视界", cost_limitation="一日三次"),
            chapters=[
                ChapterOutline(
                    order=i,
                    title=f"第{i}章",
                    core_event=f"第{i}章的核心事件",
                    opening_hook="开局钩",
                    ending_hook="收尾钩",
                )
                for i in range(1, chapters + 1)
            ],
        ),
        confirmed_parts={
            "positioning": "human-confirmed",
            "protagonist": "human-confirmed",
            "golden_finger": "human-confirmed",
            "chapters": "human-confirmed",
        },
    )
    store.save_creation(project)
    return project


_SUBJECTS = ("他", "她", "老人", "少年", "掌柜", "差役", "更夫", "船工", "药童", "妇人", "伙计", "书生", "挑夫", "货郎")
_VERBS = ("推开", "掩上", "搁下", "收起", "拂开", "扯紧", "踩过", "绕开", "扶起", "叠好", "挑亮", "按灭", "卷起", "铺开", "吹熄", "挂上", "递过", "拨开", "挑起", "压住")
_OBJECTS = ("半扇木门", "铜制台灯", "一卷旧图", "三枚铜钱", "檐下灯笼", "生锈钥匙", "粗糙麻绳", "褪色信笺", "半页账册", "一只陶碗", "油纸伞", "竹编提篮", "青布包袱", "铁皮匣子", "断了齿的木梳", "缺口的瓷碟", "发潮的账本", "缠了线的梭子")
_PLACES = ("走廊尽头", "靠窗矮桌", "结霜台阶", "后院井边", "青苔墙根", "阁楼梯口", "药铺柜台", "渡口石阶", "灶间角落", "柴房门口", "戏台后台", "河堤柳下", "祠堂偏厅", "当铺里屋")
_DETAILS = ("灯芯爆响", "铜面模糊", "边角起毛", "指腹留印", "光被晃碎", "落灰很厚", "薄锈泛黄", "字迹淡去", "木纹裂开", "水汽凝结", "绳索发脆", "漆皮剥落", "釉面缺角", "墨迹洇开")
_TAILS = ("他没说话", "她也没回头", "屋里静了片刻", "外面起了风", "远处有人应了一声", "谁都没有再看它", "灯火又暗了一格", "檐水漏得更急")
_TEMPLATES = (
    "{s}{v}{o}，{p}{d}。",
    "{s}在{p}{v}{o}，{d}。",
    "{p}的{o}被{s}{v}，{d}。",
    "{s}{v}{o}。{d}，{t}。",
    "{s}{v}了{o}以后，{p}{d}。",
    "{d}，{s}把{o}{v}。",
    "{s}问起{o}，{t}。",
    "{t}，{s}只{v}{o}。",
)

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _long_text(chars: int = 2600, *, seed: int = 20261101, ngram: int = 12, limit: int = 2) -> str:
    """造一段足够长、且 12 字片段最多出现 ``limit`` 次的正文。

    直接循环固定句子会让 gates 的 ``repeat_span`` 硬门命中（阈值 3），
    那样测到的就不是"过门"路径而是"复读拦截"路径。这里按句子递增拼装，
    每加一句就复核全量 n-gram 计数，超限即丢弃该句——保证夹具自身
    不会成为被测逻辑的噪声源。
    """
    rng = random.Random(seed)
    body = ""
    guard = 0

    def grams(value: str) -> list[str]:
        return [value[i : i + ngram] for i in range(len(value) - ngram + 1)]

    while len(_CJK.findall(body)) < chars and guard < 20000:
        guard += 1
        sentence = rng.choice(_TEMPLATES).format(
            s=rng.choice(_SUBJECTS),
            v=rng.choice(_VERBS),
            o=rng.choice(_OBJECTS),
            p=rng.choice(_PLACES),
            d=rng.choice(_DETAILS),
            t=rng.choice(_TAILS),
        )
        candidate = body + sentence
        if Counter(grams(candidate)).most_common(1)[0][1] > limit:
            continue
        body = candidate
    return body + "。"


class _GoodWriter(FakeChapterWriter):
    """可控正文的假 writer。

    必须继承 ``FakeChapterWriter``：``summarize.provider_for_writer`` 只认
    Fake 与 OpenAICompat 两类 writer，其它类型一律返回 None（不拿别的模型
    伪造摘要）。测试若用一个"自制 writer 类"，摘要闭环就会被跳过，
    测出来的就不是真实连跑路径。
    """

    def __init__(self, text) -> None:
        # text 可以是字符串（各章同一份，仅用于单章测试），
        # 也可以是 ``order -> str`` 的可调用对象（多章连跑必须用后者：
        # 各章正文一模一样会被跨章新鲜度硬门正确拦下）。
        self._text = text
        self.calls = 0

    def _body(self, order: int) -> str:
        return self._text(order) if callable(self._text) else self._text

    def write_chapter(self, pack):  # noqa: ANN001
        self.calls += 1
        from ndecon.creation.models import ChapterSelfReview, ChapterWriting

        return ChapterWriting(
            content=self._body(pack.chapter_order),
            review=ChapterSelfReview(
                hook_strength=3,
                info_density=3,
                outline_followed=True,
                deviations=[],
                notes="",
                model_id=self.model_id,
                prompt_version=self.prompt_version,
            ),
        )


def test_gate_verdicts() -> None:
    """硬门：字数带、截断、复读、工程词、占位符、乱码都要拦；软门只记分。"""
    good = _long_text(2600)
    report = check_chapter(good, target_chars=2600)
    assert report.verdict == "pass"
    assert report.cjk_chars >= 2200
    assert "freshness" in report.soft_scores

    short = check_chapter(_long_text(1200), target_chars=2600)
    assert any(f.rule == "chapter_length" for f in short.hard_failures)
    assert short.verdict == "repair"

    truncated = check_chapter(good.rstrip("。")[:-1], target_chars=2600)
    assert any(f.rule == "truncated" for f in truncated.hard_failures)

    engineering = check_chapter(good + "\n\n本章的细纲要求主角反击。", target_chars=2600)
    assert any(f.rule == "tier1_engineering_words" for f in engineering.hard_failures)

    placeholder = check_chapter(good + "\n\n【待填】", target_chars=2600)
    assert any(f.rule == "placeholder" for f in placeholder.hard_failures)

    mojibake = check_chapter(good + "\ufffd", target_chars=2600)
    assert any(f.rule == "mojibake" for f in mojibake.hard_failures)
    assert mojibake.verdict == "reject"


def test_summary_missing_is_hard_failure() -> None:
    """没有摘要的章节必须被拦：它进不了索引，后续章节会"忘掉"它。"""
    report = check_chapter(_long_text(2600), target_chars=2600, summary_present=False)
    assert any(f.rule == "summary_missing" for f in report.hard_failures)
    assert report.verdict == "repair"


def test_cross_chapter_duplicate_is_blocked() -> None:
    """把上一章原样当新章提交必须被拦：这是长篇连写最致命的崩法。

    阈值来自 167 本真实网文标定（相邻章 Jaccard 最高 0.0185），
    模板化重写 0.72、原样复制 1.0，判别力足够。
    """
    first = _long_text(2600, seed=20261101)
    second = _long_text(2600, seed=20261101 + 7919)

    fresh = check_chapter(second, target_chars=2600, previous_texts=[first])
    assert fresh.verdict == "pass"
    assert not any(f.rule == "cross_chapter_similarity" for f in fresh.hard_failures)

    copied = check_chapter(first, target_chars=2600, previous_texts=[first])
    assert any(f.rule == "cross_chapter_similarity" for f in copied.hard_failures)
    assert copied.verdict == "repair"
    assert copied.soft_scores["cross_chapter_overlap"] >= 0.9

    # 模板化程度只记分不阻断（真实模型输出的分布还没有样本，硬拦会误杀占位正文）
    assert "freshness_containment" in copied.soft_scores


def test_run_chapter_commits_and_records(tmp_path, monkeypatch) -> None:
    """过关的章：正文落盘、摘要落盘、游标推进、run 记录为 committed。"""
    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(2600))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    outcome = txn.run_chapter(store, project, 1, provider="fake")
    assert outcome.status == "committed"
    assert outcome.verdict == "pass"
    assert outcome.summary_present is True
    assert store.read_manuscript(project.meta.id, 1) == writer._body(1)

    reloaded = store.get(project.meta.id)
    assert reloaded.manuscripts["ch1"].status == "auto-accepted"
    # 摘要外置到 summaries/chNNN.json，不再塞进 project.json
    assert reloaded.summaries == {}
    summary_payload = store.read_summary(project.meta.id, 1)
    assert summary_payload is not None
    assert summary_payload["order"] == 1
    assert reloaded.cursor == 1

    run = RunLog(store.projects_dir / project.meta.id, outcome.run_id)
    record = run.read(1)
    assert record is not None
    assert record.status == "committed"
    assert record.attempts == 1
    assert record.summary_present is True
    assert record.output_hash


def test_run_chapter_rolls_back_on_reject(tmp_path, monkeypatch) -> None:
    """不可修复的失败（乱码）必须整章回滚：不留正文、不留记录、不推游标。"""
    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(2600) + "\ufffd")
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    outcome = txn.run_chapter(store, project, 1, provider="fake")
    assert outcome.status == "failed"
    assert store.read_manuscript(project.meta.id, 1) is None

    reloaded = store.get(project.meta.id)
    assert "ch1" not in reloaded.manuscripts
    assert "ch1" not in reloaded.summaries
    assert reloaded.cursor == 0

    run = RunLog(store.projects_dir / project.meta.id, outcome.run_id)
    record = run.read(1)
    assert record is not None and record.status == "failed"


def test_run_chapter_retries_with_evidence_then_degrades(tmp_path, monkeypatch) -> None:
    """一直不过门的章：按上限重试，最终标失败，绝不无限重试。"""
    store = _store(tmp_path)
    project = _project(store)
    writer = _GoodWriter(_long_text(1200))  # 永远偏短
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    outcome = txn.run_chapter(store, project, 1, provider="fake", max_repairs=1)
    assert outcome.status == "rolled_back"  # 生成过但没敢入库，不等于异常失败
    assert outcome.attempts == 2  # 1 次首写 + 1 次修复
    assert writer.calls == 2
    assert any(rule == "chapter_length" for rule in outcome.hard_failures)
    # 只记规则名等于没记：必须带实测字数，否则用户看到「第 1 章 repair」
    # 既不知道差多少，也不知道该换模型还是调目标字数。
    assert outcome.hard_failure_details, "硬门失败必须带可读说明"
    assert any("实测" in detail and "2600" in detail for detail in outcome.hard_failure_details)


def test_repair_feedback_lands_in_prompt() -> None:
    """修复重写必须把失败原因带回 prompt，而不是让模型"再猜一遍"。"""
    from ndecon.creation.chapters import context_brief
    from ndecon.retrieval.context import ContextPack

    pack = ContextPack(
        project_id="p",
        chapter_order=1,
        chapter_outline=ChapterOutline(order=1, title="一"),
        previous_summary_source="none",
        index_doc_count=0,
        repair_feedback=["chapter_length：偏短：实测 1200 字，目标 2600 字"],
    )
    brief = context_brief(pack)
    assert "上一版未通过硬门" in brief
    assert "chapter_length" in brief


def test_prompt_states_target_length() -> None:
    """字数目标必须写进 prompt。

    闸门按 ``target_chars`` ±15% 判硬门，prompt 若不说，模型只会按自己的
    习惯写（真实模型实测 1000-1500 字），于是每次都被判"偏短"，重写也偏短
    ——用户看到的是「连写：写了 0 章，失败 1 章：第 1 章 repair」。
    """
    from ndecon.creation.chapters import context_brief
    from ndecon.retrieval.context import ContextPack

    pack = ContextPack(
        project_id="p",
        chapter_order=1,
        chapter_outline=ChapterOutline(order=1, title="一"),
        previous_summary_source="none",
        index_doc_count=0,
        target_chars=2600,
    )
    brief = context_brief(pack)
    assert "本章目标字数" in brief
    assert "2600" in brief
    # 可接受区间与闸门的 0.85/1.15 不同口径是刻意的：prompt 给更紧的 ±10%，
    # 留出一点余量，模型落在 2340-2860 时闸门必然通过。
    assert "2340" in brief and "2860" in brief


def test_prompt_omits_length_when_target_unknown() -> None:
    """目标字数为 0 时不要凭空编一个数字。"""
    from ndecon.creation.chapters import context_brief
    from ndecon.retrieval.context import ContextPack

    pack = ContextPack(
        project_id="p",
        chapter_order=1,
        chapter_outline=ChapterOutline(order=1, title="一"),
        previous_summary_source="none",
        index_doc_count=0,
        target_chars=0,
    )
    assert "本章目标字数" not in context_brief(pack)


def test_resume_skips_committed(tmp_path, monkeypatch) -> None:
    """resume 只补没提交的章：已 committed 的章不许重写（否则连跑会反复烧钱）。"""
    store = _store(tmp_path)
    project = _project(store, chapters=3)
    # 每章正文必须不同：一模一样会被跨章新鲜度硬门（正确地）拦下。
    writer = _GoodWriter(lambda order: _long_text(2600, seed=20261101 + order * 7919))
    monkeypatch.setattr(txn, "_build_writer", lambda provider, obj: writer)

    run_id = new_run_id()
    first = txn.run_chapter(store, project, 1, provider="fake", run_id=run_id)
    assert first.status == "committed"

    reloaded = store.get(project.meta.id)
    run = RunLog(store.projects_dir / project.meta.id, run_id)
    assert 1 in run.committed_orders()

    second = txn.run_chapter(store, reloaded, 2, provider="fake", run_id=run_id)
    assert second.status == "committed"
    # 第 1 章正文未被覆盖（版本目录里没有它的历史）
    assert store.list_versions(project.meta.id, 1) == []


def test_unsettled_orders_reported(tmp_path) -> None:
    """被杀时停在 in_progress 的章必须能被识别出来（断点判据）。"""
    store = _store(tmp_path)
    project = _project(store)
    run = RunLog(store.projects_dir / project.meta.id, "run-dead")
    run.write(ChapterRun(run_id="run-dead", project_id=project.meta.id, order=1))
    assert run.unsettled_orders() == {1}
    assert run.committed_orders() == set()
