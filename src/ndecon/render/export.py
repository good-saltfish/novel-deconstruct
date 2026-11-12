"""导出：把已写章节打成 docx / epub（零第三方依赖）。

## 为什么自己拼 XML 而不是引 python-docx / ebooklib

引擎的依赖面只有 ``pydantic`` / ``typer`` / ``httpx``（见 ``pyproject.toml``）。
为了让一个「另存为」功能引入两个重量级包，会把安装体积和供应链风险一起放大，
而需要写出来的只是两种格式的**最小合法子集**：一个书名、一串段落、一份章节
清单。用 stdlib 的 ``zipfile`` + 手写 XML 就能确定性地产出来，而且每个字节都
可测——测试直接解包校验结构，不需要真的打开 Word 或阅读器。

## 两条纪律

1. **只导出盘上真有正文的章**：以 ``store.read_manuscript`` 非空为准，不看
   ``project.manuscripts`` 的键。理由与 ``WorkspaceStore._written_cursor``
   相同——历史工作区里出现过「键没了文件还在」和「键在文件被回滚删掉」两种
   情况，正文文件才是事实。
2. **占位正文照常导出，但点名**：``PLACEHOLDER_BANNER`` 开头的章会被记进
   ``ExportResult.placeholder_orders``，由调用方决定怎么提示。静默丢掉会让
   「为什么我导出的书少了三章」变成一个没法回答的谜；静默收下则会让用户
   把占位文本当成真正文发出去——两种都不行，所以导出、并如实报告。
"""

from __future__ import annotations

import html
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from ndecon.quality.gates import PLACEHOLDER_BANNER
from ndecon.workspace.models import CreationProject
from ndecon.workspace.store import WorkspaceStore

#: 支持的导出格式。新增格式时同步 ``build_export`` 的分派与 CLI 的 help 文本。
EXPORT_FORMATS = ("docx", "epub")

#: 段落分隔：连续空行。用 ``\n\s*\n`` 而不是 ``splitlines``——中文稿件里
#: 段落本来就是靠空行分的，逐行切会把一句话断成十几个「段」。
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")

#: XML 1.0 不允许的控制字符（除 \t \n \r 外全部剔除）。
#: 不处理的话，一份从别处粘来的稿子会让 Word 直接报「文件已损坏」，
#: 而用户完全看不出是哪一章的哪个字符的问题。
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: 文件名里不能出现的字符（Windows 上最严：<>:"/\|?* 加控制字符）。
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ExportError(ValueError):
    """导出无法进行（没有正文、格式不支持等），消息直接面向用户。"""


@dataclass(frozen=True)
class ExportChapter:
    """导出用的单章：章号 + 标题 + 已切好的段落。"""

    order: int
    title: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class ExportResult:
    """一次导出的结果：字节内容 + 如实报告做了什么。"""

    content: bytes
    format: str
    filename: str
    chapter_count: int
    #: 正文是占位文本的章号——导出包含它们，但用户需要知道。
    placeholder_orders: list[int] = field(default_factory=list)


def paragraphs_of(text: str) -> tuple[str, ...]:
    """把正文切成段落：按空行分，去掉首尾空白与空段。"""
    cleaned = _INVALID_XML.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    parts = (part.strip() for part in _PARAGRAPH_SPLIT.split(cleaned))
    return tuple(part for part in parts if part)


def collect_chapters(store: WorkspaceStore, project: CreationProject) -> list[ExportChapter]:
    """按章号升序收集盘上真有正文的章。

    标题优先取细纲里的 ``title``；细纲里没有这一章（改过大纲、或早期数据）
    就退回 ``第N章``——导出不该因为缺标题就整本失败。
    """
    titles = {outline.order: outline.title for outline in project.draft.chapters}
    chapters: list[ExportChapter] = []
    for order in range(1, project.cursor + 1):
        content = store.read_manuscript(project.meta.id, order)
        if not content or not content.strip():
            continue
        chapters.append(
            ExportChapter(
                order=order,
                title=titles.get(order) or f"第{order}章",
                paragraphs=paragraphs_of(content),
            )
        )
    return chapters


def safe_filename(title: str, fmt: str) -> str:
    """生成安全文件名：抹掉路径非法字符，空标题退回 ``未命名``。"""
    stem = _UNSAFE_FILENAME.sub("-", title).strip(" .-")[:60] or "未命名"
    return f"{stem}.{fmt}"


def _placeholder_orders(chapters: list[ExportChapter]) -> list[int]:
    return [
        chapter.order
        for chapter in chapters
        if chapter.paragraphs and chapter.paragraphs[0].startswith(PLACEHOLDER_BANNER)
    ]


# ---- docx（WordprocessingML 的最小合法子集） ----

_DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" \
ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" \
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" \
Target="word/document.xml"/>
</Relationships>
"""

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx_run(text: str, *, bold: bool = False, size: int = 0) -> str:
    """一个文本 run；``size`` 是半点（Word 的单位，12pt = 24）。"""
    props: list[str] = []
    if bold:
        props.append("<w:b/>")
    if size:
        props.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    rpr = f"<w:rPr>{''.join(props)}</w:rPr>" if props else ""
    # xml:space="preserve" 保住行首空格：中文稿里常有全角缩进。
    return f'<w:r>{rpr}<w:t xml:space="preserve">{html.escape(text)}</w:t></w:r>'


def _docx_para(text: str = "", *, bold: bool = False, size: int = 0, align: str = "") -> str:
    ppr: list[str] = []
    if align:
        ppr.append(f'<w:jc w:val="{align}"/>')
    # 1.5 倍行距 + 段后 6pt：中文长文默认单倍行距几乎没法读。
    ppr.append('<w:spacing w:line="360" w:lineRule="auto" w:after="120"/>')
    props = f"<w:pPr>{''.join(ppr)}</w:pPr>"
    run = _docx_run(text, bold=bold, size=size) if text else ""
    return f"<w:p>{props}{run}</w:p>"


def build_docx(chapters: list[ExportChapter], *, title: str) -> bytes:
    """把章节打成一个 .docx（zip 容器 + 单份 document.xml）。"""
    body: list[str] = [
        _docx_para(title, bold=True, size=36, align="center"),
        # 分页符：书名页独占一页，正文从第二页起。
        '<w:p><w:r><w:br w:type="page"/></w:r></w:p>',
    ]
    for index, chapter in enumerate(chapters):
        if index:
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        body.append(_docx_para(chapter.title, bold=True, size=28))
        body.extend(_docx_para(paragraph) for paragraph in chapter.paragraphs)
    body.append('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_W_NS}"><w:body>{"".join(body)}</w:body></w:document>'
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


# ---- epub（EPUB 3 的最小合法子集） ----

_EPUB_CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles>
<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles>
</container>
"""

_EPUB_CSS = """body { margin: 1.2em; line-height: 1.7; }
h1 { font-size: 1.4em; margin: 1.5em 0 1em; }
h2 { font-size: 1.15em; margin: 1.2em 0 0.8em; }
p { margin: 0 0 0.9em; text-indent: 2em; }
.title-page p { text-align: center; text-indent: 0; font-size: 1.6em; }
"""


def _xhtml_page(title: str, body: str) -> str:
    """一页 XHTML；标题已转义，body 由调用方保证是合法标记。"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" \
xml:lang="zh-CN" lang="zh-CN">
<head><meta charset="utf-8"/><title>{html.escape(title)}</title>\
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
{body}
</body>
</html>
"""


def _chapter_body(chapter: ExportChapter) -> str:
    paras = "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in chapter.paragraphs)
    return f"<h2>{html.escape(chapter.title)}</h2>\n{paras}"


def _book_id(title: str) -> str:
    """确定性的书号：同一本书同一批章节永远得到同一个 uuid。

    用随机 uuid 会让「同一份稿子导出两次」变成两个不同的书，阅读器里
    会显示成两本；确定性 id 还能让测试直接比对。
    """
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'ndecon:{title}')}"


def build_epub(
    chapters: list[ExportChapter],
    *,
    title: str,
    author: str = "",
    modified: str | None = None,
) -> bytes:
    """把章节打成一个 EPUB 3 文件。

    ``modified`` 参与 OPF 的 ``dcterms:modified``（EPUB 3 必填项）。默认取当前
    UTC 时间，传入固定值可以让产物在测试里逐字节可复现。
    """
    stamp = modified or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = [f"ch{chapter.order:03d}.xhtml" for chapter in chapters]

    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine: list[str] = []
    # 书名页单独一页：阅读器打开的第一屏就是书名，而不是「第一章」。
    manifest.append('<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="titlepage"/>')
    for chapter, href in zip(chapters, files, strict=True):
        manifest.append(
            f'<item id="ch{chapter.order:03d}" href="{href}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="ch{chapter.order:03d}"/>')

    creator = f"<dc:creator>{html.escape(author)}</dc:creator>" if author else ""
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{_book_id(title)}</dc:identifier>
<dc:title>{html.escape(title)}</dc:title>
<dc:language>zh-CN</dc:language>{creator}
<meta property="dcterms:modified">{stamp}</meta>
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine>
{chr(10).join(spine)}
</spine>
</package>
"""

    toc_items = "\n".join(
        f'<li><a href="ch{chapter.order:03d}.xhtml">{html.escape(chapter.title)}</a></li>'
        for chapter in chapters
    )
    nav = _xhtml_page(
        title,
        "<nav epub:type=\"toc\" id=\"toc\"><h2>目录</h2>\n"
        f"<ol>\n{toc_items}\n</ol></nav>",
    )
    titlepage = _xhtml_page(title, f'<div class="title-page"><p>{html.escape(title)}</p></div>')

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # mimetype 必须是第一个条目且不压缩，否则严格的阅读器直接判为非法。
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        archive.writestr(info, "application/epub+zip")
        archive.writestr("META-INF/container.xml", _EPUB_CONTAINER, zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/nav.xhtml", nav, zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/titlepage.xhtml", titlepage, zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/style.css", _EPUB_CSS, zipfile.ZIP_DEFLATED)
        for chapter, href in zip(chapters, files, strict=True):
            page = _xhtml_page(chapter.title, _chapter_body(chapter))
            archive.writestr(f"OEBPS/{href}", page, zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


# ---- 统一入口 ----


def build_export(
    store: WorkspaceStore,
    project: CreationProject,
    *,
    fmt: str,
    author: str = "",
    modified: str | None = None,
) -> ExportResult:
    """按格式收集章节并产出字节；没有正文时抛 :class:`ExportError`。"""
    normalized = fmt.strip().lower().lstrip(".")
    if normalized not in EXPORT_FORMATS:
        raise ExportError(f"不支持的导出格式：{fmt!r}（可选 {' / '.join(EXPORT_FORMATS)}）")
    chapters = collect_chapters(store, project)
    if not chapters:
        raise ExportError(
            f"「{project.meta.title}」还没有任何正文可以导出：先把章节写出来再导出。"
        )
    title = project.meta.title
    if normalized == "docx":
        content = build_docx(chapters, title=title)
    else:
        content = build_epub(chapters, title=title, author=author, modified=modified)
    return ExportResult(
        content=content,
        format=normalized,
        filename=safe_filename(title, normalized),
        chapter_count=len(chapters),
        placeholder_orders=_placeholder_orders(chapters),
    )


def write_export(result: ExportResult, out_path: Path) -> Path:
    """把导出结果落盘（父目录自动创建），返回实际路径。"""
    # 复用原子写：Windows 上目标被阅读器/索引器打开时普通 write_bytes 会直接抛
    # PermissionError，而这里只是重试一下就能成功的事；也顺便保证不会留下半截
    # 文件被 Word 判成「损坏」。
    from ndecon.workspace.atomic import atomic_write_bytes

    atomic_write_bytes(out_path, result.content)
    return out_path
