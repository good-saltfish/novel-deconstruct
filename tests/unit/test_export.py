"""导出（epub / docx）单元测试：结构合法性、章节收集、占位正文如实报告。"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from ndecon.creation.models import (
    ChapterOutline,
    CreationDraft,
    ManuscriptRecord,
    Positioning,
)
from ndecon.quality.gates import PLACEHOLDER_BANNER
from ndecon.render.export import (
    ExportChapter,
    ExportError,
    build_docx,
    build_epub,
    build_export,
    collect_chapters,
    paragraphs_of,
    safe_filename,
)
from ndecon.workspace.models import CreationProject, ProjectMeta
from ndecon.workspace.store import WorkspaceStore

_STAMP = "2026-11-13T00:00:00Z"


def _store(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path)


def _project(store: WorkspaceStore, *, chapters: int = 3) -> CreationProject:
    """建一个三章细纲的创作项目（不写正文）。"""
    project_id = store.allocate_project_id("creation", "导出测试")
    project = CreationProject(
        meta=ProjectMeta(
            id=project_id, kind="creation", title="导出测试", created_at=store.now_iso()
        ),
        genre="都市",
        premise="一句话",
        draft=CreationDraft(
            positioning=Positioning(genre="都市", audience="男频", core_premise="一句话"),
            chapters=[
                ChapterOutline(
                    order=order,
                    title=f"第{order}章",
                    core_event="事件",
                    opening_hook="开局",
                    ending_hook="收尾",
                )
                for order in range(1, chapters + 1)
            ],
        ),
    )
    store.save_creation(project)
    return project


def _write_chapters(store: WorkspaceStore, project: CreationProject, orders: list[int]) -> None:
    """直接往盘上写正文（模拟已写好的章节，不推游标）。"""
    for order in orders:
        store.write_manuscript(project.meta.id, order, f"第{order}章正文。\n\n第二段。\n")


def test_paragraphs_split_on_blank_lines() -> None:
    """段落按空行切，去掉首尾空白与空段。"""
    text = "第一段。\n\n  第二段。  \n\n\n第三段。\n"
    assert paragraphs_of(text) == ("第一段。", "第二段。", "第三段。")


def test_paragraphs_strip_invalid_xml_chars() -> None:
    """XML 1.0 不允许的控制字符必须剔除，否则 Word 报文件损坏。"""
    assert paragraphs_of("正常\x00文本\x07。") == ("正常文本。",)


def test_collect_chapters_skips_orders_without_manuscript(tmp_path) -> None:
    """只收盘上真有正文的章：细纲有 3 章、只写了 1 和 3，收两章。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1, 3])

    chapters = collect_chapters(store, store.get(project.meta.id))
    assert [chapter.order for chapter in chapters] == [1, 3]
    assert chapters[0].title == "第1章"
    # 未写正文的第 2 章不出现，也不会凭空造一个空章
    assert all(chapter.paragraphs for chapter in chapters)


def test_collect_chapters_uses_outline_title_and_falls_back(tmp_path) -> None:
    """标题取细纲；细纲里没有这一章时退回「第N章」，不整本失败。"""
    store = _store(tmp_path)
    project = _project(store)
    store.write_manuscript(project.meta.id, 7, "第七章正文。")
    chapters = collect_chapters(store, store.get(project.meta.id))
    assert [chapter.order for chapter in chapters] == [7]
    assert chapters[0].title == "第7章"


def test_safe_filename_strips_path_chars() -> None:
    """文件名抹掉 Windows 非法字符，空标题退回未命名。"""
    assert safe_filename('a/b:c*d?e"f<g>h|i', "epub") == "a-b-c-d-e-f-g-h-i.epub"
    assert safe_filename("   ", "docx") == "未命名.docx"


def test_build_export_rejects_unknown_format(tmp_path) -> None:
    """不支持的格式给出可读错误，而不是写出一个坏文件。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1])
    with pytest.raises(ExportError, match="不支持的导出格式"):
        build_export(store, store.get(project.meta.id), fmt="pdf")


def test_build_export_rejects_project_without_manuscripts(tmp_path) -> None:
    """一章正文都没有时不产出一个空文件——那样用户会以为导出成功了。"""
    store = _store(tmp_path)
    project = _project(store)
    with pytest.raises(ExportError, match="还没有任何正文"):
        build_export(store, store.get(project.meta.id), fmt="epub")


def test_docx_is_well_formed_package(tmp_path) -> None:
    """docx 必须是可解包的 zip，三份 XML 都要能解析。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1, 2])
    result = build_export(store, store.get(project.meta.id), fmt="docx")

    archive = zipfile.ZipFile(io.BytesIO(result.content))
    assert archive.testzip() is None
    assert set(archive.namelist()) == {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
    }
    for name in archive.namelist():
        ET.fromstring(archive.read(name))
    document = archive.read("word/document.xml").decode("utf-8")
    assert "第1章" in document and "第2章" in document
    assert result.filename == "导出测试.docx"
    assert result.chapter_count == 2


def test_docx_escapes_markup_in_text() -> None:
    """正文里的尖括号必须转义，否则整个 document.xml 变成非法 XML。"""
    chapters = [
        ExportChapter(
            order=1, title="<章节>", paragraphs=("a<b>c & d",)
        )
    ]
    document = zipfile.ZipFile(io.BytesIO(build_docx(chapters, title="T"))).read(
        "word/document.xml"
    )
    ET.fromstring(document)  # 不转义这里会抛 ParseError
    assert b"a&lt;b&gt;c &amp; d" in document


def test_epub_mimetype_is_first_and_stored(tmp_path) -> None:
    """EPUB 的硬要求：mimetype 是第一个条目且不压缩，否则阅读器判非法。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1, 2])
    result = build_export(store, store.get(project.meta.id), fmt="epub", modified=_STAMP)

    archive = zipfile.ZipFile(io.BytesIO(result.content))
    first = archive.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED
    assert archive.read("mimetype") == b"application/epub+zip"
    assert result.filename == "导出测试.epub"


def test_epub_metadata_and_spine_cover_every_chapter(tmp_path) -> None:
    """OPF 里有书名与必填的 dcterms:modified，spine 覆盖书名页与每一章。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1, 2, 3])
    result = build_export(
        store, store.get(project.meta.id), fmt="epub", author="作者甲", modified=_STAMP
    )

    archive = zipfile.ZipFile(io.BytesIO(result.content))
    opf = archive.read("OEBPS/content.opf").decode("utf-8")
    ET.fromstring(opf)
    assert "<dc:title>导出测试</dc:title>" in opf
    assert "<dc:creator>作者甲</dc:creator>" in opf
    assert f'<meta property="dcterms:modified">{_STAMP}</meta>' in opf
    itemrefs = opf.count("<itemref ")
    assert itemrefs == 4  # 书名页 + 3 章
    for order in (1, 2, 3):
        assert f'OEBPS/ch{order:03d}.xhtml' in archive.namelist()


def test_epub_book_id_is_deterministic(tmp_path) -> None:
    """同一本书导出两次必须得到同一个 uuid，否则阅读器会当成两本书。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1])
    first = build_export(store, store.get(project.meta.id), fmt="epub", modified=_STAMP)
    second = build_export(store, store.get(project.meta.id), fmt="epub", modified=_STAMP)
    assert first.content == second.content


def test_epub_nav_lists_chapters_in_order(tmp_path) -> None:
    """目录按章号升序，且标题已转义。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1, 2])
    result = build_export(store, store.get(project.meta.id), fmt="epub", modified=_STAMP)

    nav = zipfile.ZipFile(io.BytesIO(result.content)).read("OEBPS/nav.xhtml").decode("utf-8")
    ET.fromstring(nav)
    assert nav.index("第1章") < nav.index("第2章")


def test_placeholder_chapters_are_exported_but_reported(tmp_path) -> None:
    """占位正文照常导出，但章号要被点名——两种静默都不能接受。"""
    store = _store(tmp_path)
    project = _project(store)
    store.write_manuscript(project.meta.id, 1, "真正文。\n")
    store.write_manuscript(project.meta.id, 2, f"{PLACEHOLDER_BANNER}\n\n占位内容。\n")

    result = build_export(store, store.get(project.meta.id), fmt="epub", modified=_STAMP)
    assert result.chapter_count == 2  # 照常导出
    assert result.placeholder_orders == [2]  # 但如实报告


def test_epub_and_docx_include_only_collected_chapters(tmp_path) -> None:
    """两个格式的章数一致，都等于盘上真有正文的章数。"""
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1, 3])
    docx = build_export(store, store.get(project.meta.id), fmt="docx")
    epub = build_export(store, store.get(project.meta.id), fmt="epub", modified=_STAMP)
    assert docx.chapter_count == epub.chapter_count == 2
    epub_names = zipfile.ZipFile(io.BytesIO(epub.content)).namelist()
    assert "OEBPS/ch002.xhtml" not in epub_names


def test_build_epub_accepts_empty_author() -> None:
    """没填作者时不写空的 dc:creator（空标签在部分阅读器里会显示一个空白作者）。"""
    chapters = [
        ExportChapter(
            order=1, title="第1章", paragraphs=("正文。",)
        )
    ]
    opf = zipfile.ZipFile(io.BytesIO(build_epub(chapters, title="T", modified=_STAMP))).read(
        "OEBPS/content.opf"
    )
    assert b"dc:creator" not in opf


def test_export_ignores_manuscript_keys_without_files(tmp_path) -> None:
    """判断依据是正文文件，不是 project.manuscripts 的键。

    历史工作区里出现过「记录在、文件被回滚删掉」，按记录导出会产出一本
    中间空掉几章的书，而用户看不出哪里少了。
    """
    store = _store(tmp_path)
    project = _project(store)
    _write_chapters(store, project, [1])

    def _add_ghost(latest: CreationProject) -> None:
        latest.cursor = 2
        latest.manuscripts["ch2"] = ManuscriptRecord(
            order=2,
            title="第2章",
            status="draft",
            word_count=100,
            model_id="rule:fake-chapter",
            prompt_version="cw-v1",
            updated_at=store.now_iso(),
        )

    store.update_creation(project.meta.id, _add_ghost)
    chapters = collect_chapters(store, store.get(project.meta.id))
    assert [chapter.order for chapter in chapters] == [1]
