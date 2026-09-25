"""知识库文件解析与切块测试：编码回退、docx 提取、切块确定性。"""

import zipfile

from ndecon.kb.docreader import chunk_text, read_docx_text, read_plain_text


def test_utf8_and_bom(tmp_path) -> None:
    """UTF-8 与带 BOM 的文本都正确读取。"""
    path = tmp_path / "a.md"
    path.write_text("卡普曼三角", encoding="utf-8")
    assert read_plain_text(path) == "卡普曼三角"
    path.write_text("卡普曼三角", encoding="utf-8-sig")
    assert read_plain_text(path) == "卡普曼三角"


def test_gbk_fallback(tmp_path) -> None:
    """GBK 编码的老资料在 UTF-8 失败后回退成功。"""
    path = tmp_path / "old.txt"
    path.write_bytes("写作心得：期待感".encode("gbk"))
    assert read_plain_text(path) == "写作心得：期待感"


def _make_docx(path, paragraphs: list[str]) -> None:
    """用最小 docx 结构（zip + document.xml）构造测试文档。"""
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<x/>")
        archive.writestr("word/document.xml", xml)


def test_docx_paragraphs_extracted(tmp_path) -> None:
    """docx 段落文本与标签剥离正确。"""
    path = tmp_path / "心得.docx"
    _make_docx(path, ["第一段：钩子前置", "第二段：金手指带代价"])
    text = read_docx_text(path)
    assert "第一段：钩子前置" in text
    assert "第二段：金手指带代价" in text
    assert "<w:" not in text


def test_chunk_by_headings_and_blank_lines() -> None:
    """切块在 markdown 标题与空行处断开，标题写入块元数据。"""
    text = "# 卡普曼三角\n迫害者与受害者。\n\n# 情绪轨迹\n先抑后扬。"
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0].heading == "卡普曼三角"
    assert "迫害者" in chunks[0].text
    assert chunks[1].heading == "情绪轨迹"
    assert chunks[0].index == 0 and chunks[1].index == 1


def test_chunk_deterministic_and_ordered() -> None:
    """同一文本切块结果逐字段一致且序号连续。"""
    text = "\n".join(f"## 小节{i}\n内容{i} " * 3 for i in range(20))
    first = [(c.index, c.heading, c.text) for c in chunk_text(text)]
    second = [(c.index, c.heading, c.text) for c in chunk_text(text)]
    assert first == second
    assert [c.index for c in chunk_text(text)] == list(range(len(first)))


def test_long_unbroken_text_hard_split_with_overlap() -> None:
    """无空行/标题的超长文本被硬切，块长不超上限且带重叠。"""
    text = "字" * 3000
    chunks = chunk_text(text)
    assert len(chunks) >= 3
    assert all(len(c.text) <= 1200 for c in chunks)
    # 相邻块存在重叠字符
    assert chunks[1].text[:80] in text
