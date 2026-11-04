"""单章事务：把"生成一章"做成一个要么全成、要么全不成的原子步骤。

为什么必须有它（B5）：

原来的 `_generate_chapter` 是"边生成边落盘"——正文先写进 manuscripts/，
再算摘要、再存 project.json。中间任何一步失败（进程被杀、provider 报错、
摘要抛错），工作区就停在半提交状态：有正文但没摘要、有正文但 manuscripts
字典里没记录、有记录但 run 账里没账。300 章连跑里这种半提交会累积成
"有的章进索引、有的章不进"，最终表现为模型莫名其妙忘掉某几章。

本模块的顺序（写死）：

1. 先写 run 记录（status=in_progress）——进程被杀时它就是断点；
2. 算上下文包、生成正文、跑闸门、必要时带证据重写（≤MAX_REPAIR_ATTEMPTS 次）；
3. 产摘要（失败不影响正文，但会在 run 记录里标 summary_present=False）；
4. **判定通过后**才写正文文件与 project.json（游标、manuscripts、summaries 一起）；
5. 写 run 记录终态（committed / rolled_back）。

第 4 步内部也不允许"写了一半"：正文先写，project.json 写失败就把正文回滚成旧内容。

纪律：不自动改稿。repair 是"带着失败证据重新生成一版"，不是把原文剪掉重拼。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ndecon.creation.chapters import FakeChapterWriter, OpenAICompatChapterWriter
from ndecon.creation.models import ManuscriptRecord
from ndecon.creation.runlog import ChapterRun, RunLog, new_run_id, now_iso
from ndecon.creation.summarize import content_hash, mechanical_summary, summarize_chapter
from ndecon.quality.fingerprint import scan_chapter
from ndecon.quality.gates import CHAPTER_TARGET_CHARS_DEFAULT, GateReport, check_chapter
from ndecon.retrieval.context import build_context_pack
from ndecon.workspace.models import ChapterSummary, CreationProject
from ndecon.workspace.store import WorkspaceStore

# 修复重写上限：两次。全自动连跑里"无限重试"是灾难——它会让一本书卡在一章上
# 烧光预算；两次之后接受现状并标异常，交给人（或整本暂停）。
MAX_REPAIR_ATTEMPTS = 2
# 连续多少章被降级接受后自动暂停整本：3 章说明系统性问题（provider/提示词/设定崩了）
CONSECUTIVE_DEGRADE_LIMIT = 3


@dataclass
class ChapterOutcome:
    """一章事务的结果（调用方据此决定继续/暂停/终止）。"""

    order: int
    status: str  # committed / rolled_back / failed
    verdict: str = ""
    attempts: int = 0
    cjk_chars: int = 0
    summary_present: bool = False
    # model / mechanical：机械摘要不调模型，是从正文与细纲拼出来的兜底。
    # 界面据此区分"模型真读过这一章"和"只是没断档"。
    summary_source: str = ""
    hard_failures: list[str] = field(default_factory=list)
    # 与 hard_failures 同序的可读说明（含实测值），例："偏短：实测 1200 字，目标 2600 字"
    hard_failure_details: list[str] = field(default_factory=list)
    sample_quotes: list[str] = field(default_factory=list)
    cost_tokens: int = 0
    error: str = ""
    run_id: str = ""

    @property
    def committed(self) -> bool:
        """正文与元数据是否已落盘。"""
        return self.status == "committed"


def _target_chars(project: CreationProject, order: int) -> int:
    """单章字数目标：优先取细纲里显式写的目标，缺省用默认值。

    细纲的 `ChapterOutline` 目前没有 `target_chars` 字段，所以这里用
    `getattr` 留出扩展位；缺省值必须与 `build_context_pack` 写进 prompt 的
    那个数字同源（都读 `CHAPTER_TARGET_CHARS_DEFAULT`），否则闸门按 2600 判、
    prompt 却说别的数字，模型永远判"偏短"。
    """
    return getattr(project.draft.chapters[order - 1], "target_chars", None) or (
        CHAPTER_TARGET_CHARS_DEFAULT
    )


def _recent_fingerprints(
    store: WorkspaceStore,
    project: CreationProject,
    order: int,
    window: int = 12,
) -> list[dict[str, float]]:
    """取前 window 章每条指纹规则的实测值，供跨章漂移判据使用。

    只取有正文的章：写了一半没落盘的章不该参与统计。
    返回顺序与章号一致（早 -> 晚），闸门按这个顺序取最后 DRIFT_WINDOW 章。
    """
    series: list[dict[str, float]] = []
    for previous in range(max(1, order - window), order):
        content = store.read_manuscript(project.meta.id, previous)
        if not content:
            continue
        report = scan_chapter(content)
        series.append({finding.rule: finding.value for finding in report.findings})
    return series


def _recent_texts(store: WorkspaceStore, project: CreationProject, order: int, window: int = 20) -> list[str]:
    """取近 window 章正文，用于新鲜度软门（跨章重复检测）。"""
    texts: list[str] = []
    for previous in range(max(1, order - window), order):
        content = store.read_manuscript(project.meta.id, previous)
        if content:
            texts.append(content)
    return texts


def _load_summaries(store: WorkspaceStore, project_id: str) -> list[ChapterSummary]:
    """从 summaries/ 目录读取全部章节摘要（按章号升序）。

    摘要外置后，project.json 不再持有整本摘要，读上下文时必须显式加载。
    单章摘要文件损坏只跳过该章，不影响其余章节的上下文组装。
    """
    items: list[ChapterSummary] = []
    for payload in store.list_summaries(project_id):
        try:
            items.append(ChapterSummary(**payload))
        except (TypeError, ValueError):
            continue
    return items


def _build_writer(provider: str, provider_obj: object | None) -> object:
    """按 provider 名构造章节生成器；未知 provider 抛 ValueError（由调用方转 400）。"""
    if provider == "fake":
        return FakeChapterWriter()
    if provider in ("openai-compat", "ai"):
        if provider_obj is None:
            raise ValueError("AI provider 未配置：请先在设置里填 key 与模型")
        return OpenAICompatChapterWriter(provider_obj)  # type: ignore[arg-type]
    raise ValueError(f"未知 provider：{provider}（可选 fake / ai）")


def run_chapter(
    store: WorkspaceStore,
    project: CreationProject,
    order: int,
    *,
    provider: str = "fake",
    provider_obj: object | None = None,
    run_id: str | None = None,
    auto_accept: bool = True,
    max_repairs: int = MAX_REPAIR_ATTEMPTS,
) -> ChapterOutcome:
    """跑一章的事务：生成 → 闸门 → （必要时重写）→ 摘要 → 提交。

    ``auto_accept=True``：闸门判定 pass 的章节直接落 ``auto-accepted``，
    不等人确认——这正是全自动连跑的前提（B2 修复的另一半）。
    ``auto_accept=False``：仍落 ``draft``，由人工在面板确认（A3 逐章确认模式）。
    """
    if not 1 <= order <= len(project.draft.chapters):
        raise ValueError(f"第 {order} 章不在细纲范围内（当前共 {len(project.draft.chapters)} 章）")

    writer = _build_writer(provider, provider_obj)
    target = project.draft.chapters[order - 1]
    run = RunLog(store.projects_dir / project.meta.id, run_id or new_run_id())
    record = ChapterRun(
        run_id=run.run_id,
        project_id=project.meta.id,
        order=order,
        provider=provider,
        model=getattr(writer, "model_id", ""),
        prompt_version=getattr(writer, "prompt_version", ""),
        started_at=now_iso(),
    )
    run.write(record)  # 1) 断点先落盘

    target_chars = _target_chars(project, order)
    # 摘要已外置到 summaries/chNNN.json：这里只读已写章节的摘要，
    # 不再把整本摘要塞在 project.json 里反复反序列化。
    summaries = _load_summaries(store, project.meta.id)
    pack = build_context_pack(project, order, summaries=summaries or None)
    record.context_hash = content_hash(
        pack.model_dump_json() if hasattr(pack, "model_dump_json") else repr(pack)
    )

    attempt = 0
    report: GateReport | None = None
    content = ""
    error = ""
    while attempt < max_repairs + 1:
        attempt += 1
        try:
            writing = writer.write_chapter(pack)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - provider 异常必须转成记录，不能让进程死
            error = f"章节生成失败：{exc}"
            break
        content = writing.content
        report = check_chapter(
            content,
            target_chars=target_chars,
            previous_texts=_recent_texts(store, project, order),
            summary_present=True,  # 摘要还没跑，先不判这一项
            # 跨章 AI 味漂移：单章阈值不能当硬门（人类小说与 AI 在单章上完全重叠），
            # 只有连续多章的章级中位数持续偏高才阻断，所以必须带上历史实测值。
            previous_fingerprints=_recent_fingerprints(store, project, order),
        )
        if report.verdict == "pass":
            break
        if report.verdict == "reject":
            error = "闸门判定不可修复：" + "、".join(f.rule for f in report.hard_failures)
            break
        # repair：把失败证据塞回 prompt，让模型带着具体问题重写一版
        pack = pack.model_copy(
            update={
                "repair_feedback": [
                    f"{failure.rule}：{failure.detail}" for failure in report.hard_failures
                ]
            }
        )

    if report is None or report.verdict != "pass":
        record.attempts = attempt
        record.status = "rolled_back" if not error else "failed"
        record.ended_at = now_iso()
        record.cjk_chars = report.cjk_chars if report else 0
        # 如实记录闸门给出的判定：repair 耗尽仍是 repair（可修但没修好），
        # 不能因为"这次放弃"就改写成 reject——那是两种不同的事故。
        record.gate_verdict = report.verdict if report else "reject"
        record.gate_failures = [f.rule for f in (report.hard_failures if report else [])]
        # 只记规则名等于没记：用户看到「第 1 章 repair」既不知道差多少字，
        # 也不知道该换模型还是改细纲。detail 里带着实测值（"实测 1200 字，
        # 目标 2600 字"），这才是能指导下一步的证据。
        record.gate_details = [f.detail for f in (report.hard_failures if report else [])]
        record.error = error or "超过修复重试上限"
        record.output_hash = content_hash(content) if content else ""
        run.write(record)
        return ChapterOutcome(
            order=order,
            status=record.status,
            verdict=record.gate_verdict,
            attempts=attempt,
            cjk_chars=report.cjk_chars if report else 0,
            hard_failures=record.gate_failures,
            hard_failure_details=record.gate_details,
            sample_quotes=report.sample_quotes if report else [],
            error=record.error,
            run_id=run.run_id,
        )

    # 3) 摘要：模型摘要失败就退到机械摘要，绝不让连续性断档
    #
    # 原实现是"失败就返回 None，正文照常提交，只在 run 记录里标
    # summary_present=False"。实测一次 10 章连写里这样缺了 5 章摘要，而摘要
    # 是 previous_summary 的唯一来源 —— 缺了就等于模型忘了那几章，闸门却完全
    # 不检查。现在退到机械摘要（不调模型，直接从细纲核心事件 + 正文结尾拼），
    # 保证下一章至少知道"上一章写到哪"。
    digest = content_hash(content)
    summary = summarize_chapter(writer, order, target.title, content, chapter_hash=digest)
    if summary is not None:
        summary_source = "model"
    else:
        summary = mechanical_summary(
            order,
            target.title,
            content,
            core_event=getattr(target, "core_event", "") or "",
        )
        summary_source = "mechanical"
    summary_present = True

    # 4) 提交：正文 + 摘要 + project.json 一起落，失败则回滚正文
    previous = store.read_manuscript(project.meta.id, order)
    try:
        store.write_manuscript(project.meta.id, order, content)
        if summary is not None:
            store.write_summary(project.meta.id, order, summary.model_dump())
        project.manuscripts[f"ch{order}"] = ManuscriptRecord(
            order=order,
            title=target.title,
            status="auto-accepted" if auto_accept else "draft",
            word_count=report.cjk_chars,
            model_id=getattr(writer, "model_id", ""),
            prompt_version=getattr(writer, "prompt_version", ""),
            updated_at=store.now_iso(),
            review=getattr(writing, "review", None),
        )
        project.cursor = max(project.cursor, order)
        store.save_creation(project)
    except Exception as exc:  # noqa: BLE001 - 提交失败必须回滚正文，不能留半提交
        if previous is not None:
            store.write_manuscript(project.meta.id, order, previous, keep_version=False)
        else:
            path = store._manuscript_path(project.meta.id, order)  # noqa: SLF001
            Path(path).unlink(missing_ok=True)
        store.delete_summary(project.meta.id, order)
        record.attempts = attempt
        record.status = "failed"
        record.ended_at = now_iso()
        record.error = f"提交失败并已回滚：{exc}"
        record.output_hash = digest
        run.write(record)
        return ChapterOutcome(
            order=order,
            status="failed",
            attempts=attempt,
            error=record.error,
            run_id=run.run_id,
        )

    # 5) 终态记录
    record.attempts = attempt
    record.output_hash = digest
    record.gate_verdict = report.verdict
    record.soft_scores = report.soft_scores
    record.cjk_chars = report.cjk_chars
    record.summary_present = summary_present
    record.summary_source = summary_source
    record.status = "committed"
    record.ended_at = now_iso()
    run.write(record)
    return ChapterOutcome(
        order=order,
        status="committed",
        verdict=report.verdict,
        attempts=attempt,
        cjk_chars=report.cjk_chars,
        summary_present=summary_present,
        summary_source=summary_source,
        run_id=run.run_id,
    )
