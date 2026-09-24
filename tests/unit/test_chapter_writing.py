"""章节生成器测试（#16）：Fake 占位、确定性、上下文版权边界、openai-compat 解析。"""

import pytest

from ndecon.creation.chapters import (
    CHAPTER_PROMPT_VERSION,
    FakeChapterWriter,
    OpenAICompatChapterWriter,
    chapter_user_message,
    context_brief,
)
from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    GoldenFingerSpec,
    Positioning,
    ProtagonistProfile,
)
from ndecon.providers.errors import ProviderResponseError
from ndecon.retrieval.context import build_context_pack
from ndecon.workspace.models import CreationProject, ProjectMeta, ReferenceProject
from ndecon.workspace.store import WorkspaceStore


def _creation_project(store: WorkspaceStore, *, reference_id: str | None = None) -> CreationProject:
    """构造已确认骨架（三章）的创作项目，可选挂一本参考书。"""
    draft = CreationDraft(
        positioning=Positioning(genre="都市异能", core_premise="路灯封着夜色", tone_target="紧张"),
        protagonist=ProtagonistProfile(name="林盏", desire="守灯", flaw="过度理性"),
        golden_finger=GoldenFingerSpec(name="铜灯", cost_limitation="一日三燃"),
        chapters=[
            ChapterOutline(order=1, title="停电夜", core_event="发现路灯随心跳明灭", ending_hook="周伯出现"),
            ChapterOutline(order=2, title="新守灯人", core_event="被登记成守灯人", ending_hook="井底传来抓挠声"),
            ChapterOutline(order=3, title="井里的东西", core_event="封住影兽", ending_hook="陈主任冷眼旁观"),
        ],
    )
    return CreationProject(
        meta=ProjectMeta(id="creation-ch16-1", kind="creation", title="灯火守门人", created_at=store.now_iso()),
        draft=draft,
        confirmed_parts={
            "positioning": True,
            "protagonist": True,
            "golden_finger": True,
            "chapters": True,
        },
        reference_ids=[reference_id] if reference_id else [],
    )


class _StubProvider:
    """OpenAICompatProvider 的鸭子替身：捕获 messages，返回预设 JSON。"""

    model = "test-model"

    def __init__(self, response_data: dict) -> None:
        """记录待返回数据；messages 由测试读取断言。"""
        self._response = response_data
        self.captured_messages: list[dict] | None = None

    def _chat_json(self, messages: list[dict]) -> dict:
        """模拟 JSON 通道：捕获请求并返回构造好的响应。"""
        self.captured_messages = messages
        return self._response


def test_fake_writer_shape_and_placeholder_banner(tmp_path) -> None:
    """Fake 正文含占位横幅与细纲三要素；自评为合法结构且标记 rule 来源。"""
    store = WorkspaceStore(tmp_path)
    project = _creation_project(store)
    pack = build_context_pack(project, 2)
    writing = FakeChapterWriter().write_chapter(pack)

    assert FakeChapterWriter.PLACEHOLDER_BANNER in writing.content
    assert "被登记成守灯人" in writing.content  # 本章核心事件
    assert "井底传来抓挠声" in writing.content  # 本章章尾钩
    assert "发现路灯随心跳明灭" in writing.content  # 前章摘要（细纲退化）
    review = writing.review
    assert review.model_id == "rule:fake-chapter"
    assert review.prompt_version == CHAPTER_PROMPT_VERSION
    assert 1 <= review.hook_strength <= 5
    assert review.outline_followed is True


def test_fake_writer_deterministic(tmp_path) -> None:
    """同一上下文包两次生成逐字一致（离线可重放）。"""
    store = WorkspaceStore(tmp_path)
    pack = build_context_pack(_creation_project(store), 3)
    writer = FakeChapterWriter()
    assert writer.write_chapter(pack).model_dump_json() == writer.write_chapter(pack).model_dump_json()


def test_context_pack_contains_open_foreshadow(tmp_path) -> None:
    """写第 3 章时，第 2 章未回收的章尾钩必须出现在上下文里。"""
    store = WorkspaceStore(tmp_path)
    pack = build_context_pack(_creation_project(store), 3)
    brief = context_brief(pack)
    assert "尚未回收的伏笔" in brief
    assert "井底传来抓挠声" in brief


def test_context_brief_has_no_reference_book_data(tmp_path) -> None:
    """即使项目挂了参考书，章节 prompt 也不得出现参考书书名、源路径或 reference_ids。"""
    store = WorkspaceStore(tmp_path)
    secret_title = "某版权参考书密名"
    secret_path = "D:/secret/novel-密.txt"
    reference = ReferenceProject(
        meta=ProjectMeta(id="reference-secret-1", kind="reference", title=secret_title, created_at=store.now_iso()),
        source_path=secret_path,
        chapter_count=40,
        total_plot_points=10,
    )
    store.save_reference(reference)
    project = _creation_project(store, reference_id="reference-secret-1")

    pack = build_context_pack(project, 2)
    brief = chapter_user_message(pack)
    serialized = pack.model_dump_json() + brief

    assert secret_title not in serialized
    assert secret_path not in serialized
    assert "reference" not in serialized
    assert "source_path" not in serialized
    # 本书要素仍然在场
    assert "林盏" in brief and "铜灯" in brief


def test_openai_compat_writer_parses_content_and_review(tmp_path) -> None:
    """openai-compat 生成器正确解析正文+自评，并以本地 writer 标记来源。"""
    store = WorkspaceStore(tmp_path)
    pack = build_context_pack(_creation_project(store), 2)
    stub = _StubProvider(
        {
            "content": "  雨下得很大。\n\n林盏接过了巡夜表。  ",
            "review": {
                "hook_strength": 4,
                "info_density": 3,
                "outline_followed": True,
                "deviations": [],
                "notes": "开篇到位",
                # 模型若回传来源字段，应被本地字段覆盖而非报错
                "model_id": "openai-compat:should-be-overridden",
            },
        }
    )
    writing = OpenAICompatChapterWriter(stub).write_chapter(pack)  # type: ignore[arg-type]
    assert writing.content.startswith("雨下得很大")
    assert writing.content.endswith("巡夜表。")
    assert writing.review.hook_strength == 4
    assert writing.review.model_id == "openai-compat:test-model"
    # 请求消息只含 system + user，且 user 消息是 ContextPack 简报
    assert stub.captured_messages is not None
    assert [m["role"] for m in stub.captured_messages] == ["system", "user"]
    assert "本章细纲" in stub.captured_messages[1]["content"]


def test_openai_compat_writer_rejects_empty_content(tmp_path) -> None:
    """空正文必须抛 ProviderResponseError，不允许空稿件落盘。"""
    store = WorkspaceStore(tmp_path)
    pack = build_context_pack(_creation_project(store), 2)
    stub = _StubProvider({"content": "  ", "review": {}})
    with pytest.raises(ProviderResponseError):
        OpenAICompatChapterWriter(stub).write_chapter(pack)  # type: ignore[arg-type]


def test_openai_compat_writer_rejects_bad_review(tmp_path) -> None:
    """自评分数越界（钩子强度 9）时抛 ProviderResponseError。"""
    store = WorkspaceStore(tmp_path)
    pack = build_context_pack(_creation_project(store), 2)
    stub = _StubProvider(
        {
            "content": "正文内容",
            "review": {"hook_strength": 9, "info_density": 3, "outline_followed": True},
        }
    )
    with pytest.raises(ProviderResponseError):
        OpenAICompatChapterWriter(stub).write_chapter(pack)  # type: ignore[arg-type]
