"""知识库文件解析与确定性切块（零第三方依赖）。

- .md/.txt：先 UTF-8（含 BOM），失败回退 GBK（中文 Windows 老资料常见编码）；
- .docx：用 stdlib zipfile 读 word/document.xml，按段落还原纯文本，剥除标签；
- 切块：优先在 markdown 标题或空行处断开，逐块累积到目标长度；
  超长块做硬切并保留少量重叠，防止章节体长文成为一个过大的检索单元。
切块纯函数、顺序固定，同一文件永远得到同样的块序列。
"""

from __future__ import annotations

import html
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

# 切块目标长度与超长硬切参数（按字符计，中文一字一字符）
TARGET_CHUNK_CHARS = 800
MAX_CHUNK_CHARS = 1200
OVERLAP_CHARS = 80

_MD_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_WS_RE = re.compile(r"[ \t　]+")
# docx 段落标签：</w:p> 作为段落分隔
_WP_CLOSE_RE = re.compile(r"</w:p>")
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class TextChunk:
    """文件切出的一个文本块。"""

    index: int
    text: str
    heading: str = ""


def read_plain_text(path: Path) -> str:
    """读取 md/txt：UTF-8（含 BOM）优先，失败回退 GBK；都失败抛 UnicodeDecodeError。"""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="strict")


def read_docx_text(path: Path) -> str:
    """从 .docx（zip 容器）提取段落纯文本；不是合法 docx 时抛错由调用方记录。"""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    # 先在段落结束处插换行，再剥除全部标签并还原实体
    xml = _WP_CLOSE_RE.sub("\n", xml)
    text = _TAG_RE.sub("", xml)
    return html.unescape(text)


def read_document(path: Path) -> str:
    """按扩展名分派读取知识文件文本。"""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return read_docx_text(path)
    return read_plain_text(path)


def _clean_line(line: str) -> str:
    """归一化一行内的空白（不删除换行本身）。"""
    return _WS_RE.sub(" ", line).strip()


def _hard_split(text: str, start: int) -> tuple[str, int]:
    """从 start 起切出一个不超过 MAX_CHUNK_CHARS 的块，返回（块文本, 下一起点含重叠）。"""
    end = min(start + MAX_CHUNK_CHARS, len(text))
    piece = text[start:end]
    if end >= len(text):
        return piece, len(text)
    next_start = max(start + 1, end - OVERLAP_CHARS)
    return piece, next_start


def chunk_text(text: str) -> list[TextChunk]:
    """把文档文本确定性切成检索块：标题/空行优先断块，超长硬切带重叠。"""
    lines = [_clean_line(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    blocks: list[tuple[str, str]] = []  # (块文本, 最近的 markdown 标题)
    buffer: list[str] = []
    current_heading = ""

    def flush() -> None:
        """把缓冲区累积的行存为一个候选块（空缓冲跳过）。"""
        nonlocal buffer
        joined = "\n".join(part for part in buffer if part)
        if joined.strip():
            blocks.append((joined, current_heading))
        buffer = []

    for line in lines:
        if not line:
            flush()
            continue
        if _MD_HEADING_RE.match(line):
            flush()
            current_heading = line.lstrip("#").strip()
        buffer.append(line)
        # 累积已超长时提前成块
        if sum(len(x) + 1 for x in buffer) >= TARGET_CHUNK_CHARS:
            flush()
    flush()

    chunks: list[TextChunk] = []
    for block_text, heading in blocks:
        if len(block_text) <= MAX_CHUNK_CHARS:
            chunks.append(TextChunk(index=len(chunks), text=block_text, heading=heading))
            continue
        # 极少数超长块（无空行/标题的连续文本）硬切
        start = 0
        while start < len(block_text):
            piece, start = _hard_split(block_text, start)
            if piece.strip():
                chunks.append(TextChunk(index=len(chunks), text=piece.strip(), heading=heading))
    return chunks
