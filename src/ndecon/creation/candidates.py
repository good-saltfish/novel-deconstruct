"""并行候选：同一章一次生成多版正文，全部过闸门但**不落盘**，由读者挑一版提交。

为什么不复用 :func:`ndecon.creation.txn.run_chapter`
----------------------------------------------------
它把「生成 → 闸门 → 摘要 → 提交」写成一条不可分割的事务，提交段直接改写
``manuscripts/chNNN.md``、``summaries/chNNN.json`` 与 ``project.json``（含
``cursor``）。同一章并发跑 N 次，N 条事务会互相覆盖正文与游标——最后留在盘上的
是"某个随机候选"，不是读者选中的那一版。所以候选层只借它三段**只读**前置逻辑：

1. ``_load_summaries`` + ``build_context_pack``（上下文包幂等，N 个候选共享同一份，
   差异只能来自生成器本身）；
2. ``writer.write_chapter(pack)``，每个候选一个独立 writer（温度/随机种子不同）；
3. ``check_chapter(...)``，把闸门判决**如实记下来给人看**，不做自动改写。

产物写到 ``projects/<id>/candidates_chNNN.json``，在读者点「用这一版」之前，
盘上唯一的正文仍是旧稿。选中后的提交由 :func:`select_candidate` 显式重做——
写正文、摘要、``manuscripts``、游标，逐行对齐 ``run_chapter`` 的提交段与回滚段，
两处语义必须一致，否则"生成的章"和"选中的章"会走出两条不同的落盘路径。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from ndecon.creation.chapters import (
    CHAPTER_PROMPT_VERSION,
    FakeChapterWriter,
    OpenAICompatChapterWriter,
)
from ndecon.creation.models import ChapterSelfReview, ManuscriptRecord
from ndecon.creation.summarize import (
    content_hash,
    mechanical_summary,
    summarize_chapter,
)
from ndecon.creation.txn import (
    _build_writer,
    _load_summaries,
    _recent_fingerprints,
    _recent_texts,
    _target_chars,
)
from ndecon.providers.errors import ProviderError
from ndecon.quality.gates import PLACEHOLDER_BANNER, check_chapter
from ndecon.retrieval.context import build_context_pack
from ndecon.workspace.atomic import read_text_with_retry
from ndecon.workspace.models import CreationProject
from ndecon.workspace.store import WorkspaceStore

# 候选数量的上下限。
# 下限 2：一次只生成一版根本谈不上"挑最优"。
# 上限 6：一次请求要同时占 6 条模型连接，而且单章 2600 字，再往上加只会撞插件
# 300 秒代理超时（实测单章直连 27-60 秒，6 路并行仍在预算内）。
MIN_CANDIDATES = 2
MAX_CANDIDATES = 6
DEFAULT_CANDIDATES = 3

# 候选之间的温度区间。0.3 起步保证有明显措辞差异，1.0 封顶——再高不少中转站
# 会开始复读或输出半截 JSON（这是模型自由，不是我们能调准的数）。
_TEMPERATURE_LOW = 0.3
_TEMPERATURE_HIGH = 1.0

# 产物文件名模板；store 的白名单只允许 [a-z0-9_-]，章号补零到三位与正文/摘要对齐。
_ARTIFACT_TEMPLATE = "candidates_ch{order:03d}.json"

# 预览用：给面板显示"这一版开头长什么样"，不必把整章塞进列表行。
PREVIEW_CHARS = 160


def candidate_artifact_name(order: int) -> str:
    """返回某章候选产物的文件名（``candidates_ch003.json``）。"""
    return _ARTIFACT_TEMPLATE.format(order=order)


def _preview_text(content: str) -> str:
    """截一段预览，跳过占位横幅与空行。

    横幅（``PLACEHOLDER_BANNER``）每版都一样，留在预览里会把 160 字的额度全
    吃掉——面板上三个候选看起来就成了同一段话，读者没法据此挑。
    """
    lines = [line.strip() for line in content.splitlines()]
    body = [line for line in lines if line and line != PLACEHOLDER_BANNER]
    return "\n".join(body)[:PREVIEW_CHARS]


@dataclass(frozen=True)
class CandidateSpec:
    """一个候选的生成参数：温度与随机种子偏移，二者至少要有一个起作用。"""

    index: int
    temperature: float | None
    seed_offset: int


@dataclass
class CandidateResult:
    """单个候选的生成结果；失败也留一行，让读者看见"这条路挂了"。"""

    spec: CandidateSpec
    status: str = "ok"  # ok | failed
    content: str = ""
    review: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    model_id: str = ""
    prompt_version: str = ""
    content_hash: str = ""
    elapsed_ms: int = 0
    error: str = ""

    def to_payload(self) -> dict:
        return {
            "index": self.spec.index,
            "status": self.status,
            "temperature": self.spec.temperature,
            "seed_offset": self.spec.seed_offset,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "content": self.content,
            "preview": _preview_text(self.content),
            "review": self.review,
            "gate": self.gate,
            "content_hash": self.content_hash,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def _candidate_specs(provider: str, count: int) -> list[CandidateSpec]:
    """给每个候选分配温度/种子。

    AI 路径靠温度拉开措辞；fake 路径对同一份 pack 完全确定（幂等是实现要求，
    测试要能逐字断言），只能靠种子偏移让占位正文各不相同——否则三个候选逐字
    相同，跨章新鲜度还会把自己的兄弟候选判成"换个说法重写"。
    """
    specs: list[CandidateSpec] = []
    span = _TEMPERATURE_HIGH - _TEMPERATURE_LOW
    for index in range(count):
        temperature = None
        if provider != "fake":
            # count >= MIN_CANDIDATES(2)，分母不会是 0。
            temperature = round(_TEMPERATURE_LOW + span * index / (count - 1), 2)
        specs.append(CandidateSpec(index=index, temperature=temperature, seed_offset=index))
    return specs


def _build_candidate_writer(
    provider: str,
    provider_obj: object | None,
    spec: CandidateSpec,
    target_chars: int,
) -> object:
    """按候选参数造一个独立 writer（共享同一个 provider 连接池）。"""
    if provider == "fake":
        return FakeChapterWriter(target_chars=target_chars, seed_offset=spec.seed_offset)
    if provider in ("openai-compat", "ai"):
        if provider_obj is None:
            raise ValueError("AI provider 未配置：请先在设置里填 key 与模型")
        return OpenAICompatChapterWriter(provider_obj, temperature=spec.temperature)  # type: ignore[arg-type]
    raise ValueError(f"未知 provider：{provider}（可选 fake / ai）")


def _gate_payload(report: object) -> dict:
    """把 GateReport 摊平成可落盘的结构（判定要能被人逐条看懂）。"""
    return {
        "verdict": getattr(report, "verdict", ""),
        "passed": bool(getattr(report, "passed", False)),
        "target_chars": getattr(report, "target_chars", 0),
        "cjk_chars": getattr(report, "cjk_chars", 0),
        "hard_failures": [
            {"gate": f.gate, "rule": f.rule, "detail": f.detail}
            for f in getattr(report, "hard_failures", [])
        ],
        "advisories": list(getattr(report, "advisories", [])),
        "soft_scores": dict(getattr(report, "soft_scores", {})),
        "sample_quotes": list(getattr(report, "sample_quotes", [])),
    }


def _run_one(
    spec: CandidateSpec,
    *,
    provider: str,
    provider_obj: object | None,
    pack: object,
    target_chars: int,
    previous_texts: list[str],
    previous_fingerprints: list[dict[str, float]],
    length_gate: bool,
) -> CandidateResult:
    """跑一个候选：生成 → 过闸门 → 记录。异常一律收成 failed，不牵连其它候选。"""
    started = time.monotonic()
    result = CandidateResult(spec=spec)
    try:
        writer = _build_candidate_writer(provider, provider_obj, spec, target_chars)
    except ValueError as exc:
        result.status = "failed"
        result.error = str(exc)
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    result.model_id = getattr(writer, "model_id", "") or ""
    result.prompt_version = getattr(writer, "prompt_version", "") or CHAPTER_PROMPT_VERSION
    try:
        writing = writer.write_chapter(pack)  # type: ignore[attr-defined]
    except ProviderError as exc:
        # 传输/HTTP/响应格式问题：文案已经写清是哪一层，原样递出去。
        result.status = "failed"
        result.error = str(exc)
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
    except Exception as exc:  # noqa: BLE001 - 候选失败不能拖垮整批
        result.status = "failed"
        result.error = f"章节生成失败：{exc}"
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result

    content = writing.content
    report = check_chapter(
        content,
        target_chars=target_chars,
        previous_texts=previous_texts,
        summary_present=True,
        previous_fingerprints=previous_fingerprints,
        length_gate=length_gate,
    )
    review = getattr(writing, "review", None)
    result.status = "ok"
    result.content = content
    result.review = review.model_dump() if hasattr(review, "model_dump") else {}
    result.gate = _gate_payload(report)
    result.content_hash = content_hash(content)
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def generate_candidates(
    store: WorkspaceStore,
    project: CreationProject,
    order: int,
    *,
    provider: str = "fake",
    provider_obj: object | None = None,
    count: int = DEFAULT_CANDIDATES,
    length_gate: bool = True,
) -> dict:
    """并行生成 ``count`` 版正文并落盘候选产物；**不碰正文、摘要与游标**。

    返回的 payload 同时写到 ``candidates_chNNN.json``，面板读同一份文件——
    界面上看到的就是盘上存的，不存在"内存里一套、磁盘上一套"。
    """
    if not 1 <= order <= len(project.draft.chapters):
        raise ValueError(f"第 {order} 章不在细纲范围内（当前共 {len(project.draft.chapters)} 章）")
    if not MIN_CANDIDATES <= count <= MAX_CANDIDATES:
        raise ValueError(f"候选数量必须在 {MIN_CANDIDATES}-{MAX_CANDIDATES} 之间，收到 {count}")

    target_chars = _target_chars(project, order)
    summaries = _load_summaries(store, project.meta.id)
    pack = build_context_pack(project, order, summaries=summaries or None)
    previous_texts = _recent_texts(store, project, order)
    previous_fingerprints = _recent_fingerprints(store, project, order)
    specs = _candidate_specs(provider, count)

    started = time.monotonic()
    results: list[CandidateResult | None] = [None] * count
    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="ndecon-candidate") as pool:
        futures = {
            pool.submit(
                _run_one,
                spec,
                provider=provider,
                provider_obj=provider_obj,
                pack=pack,
                target_chars=target_chars,
                previous_texts=previous_texts,
                previous_fingerprints=previous_fingerprints,
                length_gate=length_gate,
            ): spec.index
            for spec in specs
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - 兜底：线程池本身炸了也要有记录
                results[index] = CandidateResult(
                    spec=specs[index], status="failed", error=f"候选线程异常：{exc}"
                )

    candidates = [item.to_payload() for item in results if item is not None]
    ok_count = sum(1 for item in candidates if item["status"] == "ok")
    passed_count = sum(1 for item in candidates if item["gate"].get("passed"))
    payload = {
        "order": order,
        "title": project.draft.chapters[order - 1].title,
        "generated_at": store.now_iso(),
        "provider": provider,
        "model_id": next((c["model_id"] for c in candidates if c["model_id"]), ""),
        "prompt_version": next((c["prompt_version"] for c in candidates if c["prompt_version"]), ""),
        "target_chars": target_chars,
        "length_gate": length_gate,
        "requested": count,
        "ok_count": ok_count,
        "passed_count": passed_count,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "selected_index": None,
        "selected_at": "",
        "candidates": candidates,
    }
    store.write_project_artifact(project.meta.id, candidate_artifact_name(order), payload)
    return payload


def read_candidates(store: WorkspaceStore, project_id: str, order: int) -> dict | None:
    """读某章的候选产物；没有或损坏时返回 None（面板据此显示"还没有候选"）。"""
    path = store.read_project_artifact(project_id, candidate_artifact_name(order))
    if path is None:
        return None
    try:
        payload = json.loads(read_text_with_retry(path))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def select_candidate(
    store: WorkspaceStore,
    project: CreationProject,
    order: int,
    index: int,
    *,
    provider: str = "fake",
    provider_obj: object | None = None,
    auto_accept: bool = True,
) -> dict:
    """把读者选中的那一版提交成正式正文（写正文 + 摘要 + 记录 + 游标）。

    提交前按**当前**账面重判一次闸门：读者看候选时可能已经提交了前一章，
    跨章新鲜度与字数带都会变。拿旧判决冒充新证据，等于把闸门让给时间差。
    只有 ``reject``（乱码/空正文这类不可入库的硬伤）才拒绝提交——``repair``
    是"可以更好"，人工选中就按 draft 落盘，不许悄悄扣成 auto-accepted。
    """
    payload = read_candidates(store, project.meta.id, order)
    if payload is None:
        raise ValueError(f"第 {order} 章还没有并行候选，先点「并行候选」生成")
    candidates = payload.get("candidates") or []
    chosen = next((item for item in candidates if item.get("index") == index), None)
    if chosen is None:
        raise ValueError(f"候选编号不存在：{index}（这一章共 {len(candidates)} 版）")
    if chosen.get("status") != "ok":
        raise ValueError(f"候选 #{index} 生成失败，没有正文可选：{chosen.get('error') or '未知原因'}")
    content = str(chosen.get("content") or "")
    if not content.strip():
        raise ValueError(f"候选 #{index} 正文为空，无法提交")

    target = project.draft.chapters[order - 1]
    report = check_chapter(
        content,
        target_chars=_target_chars(project, order),
        previous_texts=_recent_texts(store, project, order),
        summary_present=True,
        previous_fingerprints=_recent_fingerprints(store, project, order),
        length_gate=bool(payload.get("length_gate", True)),
    )
    if report.verdict == "reject":
        rules = "、".join(failure.rule for failure in report.hard_failures)
        raise ValueError(f"候选 #{index} 含不可入库的硬伤（{rules}），换一版")

    digest = content_hash(content)
    summary = None
    summary_source = "mechanical"
    try:
        writer = _build_writer(provider, provider_obj)
        summary = summarize_chapter(writer, order, target.title, content, chapter_hash=digest)
    except (ProviderError, ValueError):
        # 摘要失败不能连累已经选定的正文：机械摘要保底，连续性不断档。
        summary = None
    if summary is not None:
        summary_source = "model"
    else:
        summary = mechanical_summary(
            order, target.title, content, core_event=getattr(target, "core_event", "") or ""
        )

    review = None
    raw_review = chosen.get("review")
    if isinstance(raw_review, dict) and raw_review:
        try:
            review = ChapterSelfReview(**raw_review)
        except (ValidationError, TypeError):
            review = None

    accepted = auto_accept and report.verdict == "pass"
    previous = store.read_manuscript(project.meta.id, order)
    try:
        store.write_manuscript(project.meta.id, order, content)
        store.write_summary(project.meta.id, order, summary.model_dump())
        project.manuscripts[f"ch{order}"] = ManuscriptRecord(
            order=order,
            title=target.title,
            status="auto-accepted" if accepted else "draft",
            word_count=report.cjk_chars,
            model_id=str(chosen.get("model_id") or payload.get("model_id") or "unknown"),
            prompt_version=str(chosen.get("prompt_version") or CHAPTER_PROMPT_VERSION),
            updated_at=store.now_iso(),
            review=review,
        )
        project.cursor = max(project.cursor, order)
        store.save_creation(project)
    except Exception as exc:  # noqa: BLE001 - 提交失败必须回滚到选之前那一版
        if previous is not None:
            store.write_manuscript(project.meta.id, order, previous, keep_version=False)
        else:
            Path(store._manuscript_path(project.meta.id, order)).unlink(missing_ok=True)  # noqa: SLF001
        store.delete_summary(project.meta.id, order)
        raise ValueError(f"提交失败并已回滚：{exc}") from exc

    payload["selected_index"] = index
    payload["selected_at"] = store.now_iso()
    payload["selected_verdict"] = report.verdict
    store.write_project_artifact(project.meta.id, candidate_artifact_name(order), payload)

    return {
        "order": order,
        "selected_index": index,
        "verdict": report.verdict,
        "cjk_chars": report.cjk_chars,
        "status": "auto-accepted" if accepted else "draft",
        "summary_source": summary_source,
        "hard_failures": [failure.rule for failure in report.hard_failures],
    }
