"""资料库扫描、建库与检索（工作区 kb/ 产物）。

产物两个文件：
- kb/index.json：复用 retrieval.BM25Index 的序列化格式，语料单元为知识块；
- kb/manifest.json：审计清单——每个扫描文件的收录/排除原因、块数、解析错误。
索引与 manifest 均只含解析后的知识文本，不复制源文件本体。
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ndecon.kb.docreader import chunk_text, read_document
from ndecon.kb.exclusion import decide
from ndecon.retrieval.bm25 import INDEX_VERSION, BM25Index, IndexedDocument

KB_DIRNAME = "kb"
INDEX_FILENAME = "index.json"
MANIFEST_FILENAME = "manifest.json"
KNOWLEDGE_PROMPT_VERSION = "kb-v1"
# 注入骨架 prompt 的片段上限与单片段长度（token 预算守卫）
DEFAULT_KB_TOP_K = 5
KB_SNIPPET_CHARS = 400


@dataclass
class ManifestEntry:
    """manifest 中一个文件的审计记录。"""

    relative_path: str
    included: bool
    reason: str
    chunks: int = 0
    error: str = ""


class KnowledgeSnippet(BaseModel):
    """一条命中的方法论知识（供骨架生成 prompt 使用）。"""

    source: str = Field(min_length=1, description="来源文件相对路径")
    heading: str = ""
    text: str = Field(min_length=1)
    score: float


def kb_dir(workspace: str | Path) -> Path:
    """返回工作区知识库目录路径。"""
    return Path(workspace) / KB_DIRNAME


def iter_candidate_files(roots: list[Path]) -> list[Path]:
    """递归列出目录下**全部文件**（含将被排除的类型），供 manifest 逐项审计，路径排序确定。"""
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                candidates.append(path)
    return sorted(candidates, key=lambda p: str(p).lower())


def _stable_doc_id(relative_path: str, chunk_index: int) -> str:
    """由相对路径+块序号生成稳定 doc_id（路径含中文也安全、确定）。"""
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12]
    return f"kb-{digest}-{chunk_index:04d}"


def build_knowledge_index(
    roots: list[str | Path],
) -> tuple[BM25Index, list[ManifestEntry]]:
    """扫描多个资料根目录，返回 BM25 索引与逐文件审计清单。"""
    root_paths = [Path(root).expanduser() for root in roots]
    documents: list[IndexedDocument] = []
    manifest: list[ManifestEntry] = []

    for path in iter_candidate_files(root_paths):
        # 相对路径取"最匹配的根"，用于审计展示；找不到则用绝对路径
        try:
            relative = next(
                str(path.relative_to(root)).replace(os.sep, "/")
                for root in root_paths
                if path.is_relative_to(root)
            )
        except StopIteration:  # pragma: no cover - 候选文件必然来自某个根
            relative = str(path)

        decision = decide(path)
        if not decision.included:
            manifest.append(ManifestEntry(relative, False, decision.reason))
            continue
        try:
            text = read_document(path)
        except (OSError, KeyError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            manifest.append(ManifestEntry(relative, False, "解析失败", error=str(exc)))
            continue

        chunks = chunk_text(text)
        if not chunks:
            manifest.append(ManifestEntry(relative, False, "解析后无有效文本"))
            continue
        for chunk in chunks:
            documents.append(
                IndexedDocument(
                    doc_id=_stable_doc_id(relative, chunk.index),
                    doc_type="kb_chunk",
                    title=chunk.heading or path.stem,
                    text=chunk.text,
                    meta={"source": relative, "heading": chunk.heading, "chunk": chunk.index},
                )
            )
        manifest.append(ManifestEntry(relative, True, "收录", chunks=len(chunks)))

    return BM25Index(documents), manifest


def write_knowledge_artifacts(
    workspace: str | Path,
    roots: list[str | Path],
    index: BM25Index,
    manifest: list[ManifestEntry],
) -> Path:
    """原子落盘 index.json 与 manifest.json 到工作区 kb/ 目录，返回该目录。"""
    directory = kb_dir(workspace)
    directory.mkdir(parents=True, exist_ok=True)

    index_payload = index.to_dict()
    manifest_payload = {
        "version": INDEX_VERSION,
        "prompt_version": KNOWLEDGE_PROMPT_VERSION,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "roots": [str(Path(root).expanduser()) for root in roots],
        "included_count": sum(1 for item in manifest if item.included),
        "excluded_count": sum(1 for item in manifest if not item.included),
        "files": [asdict(item) for item in manifest],
    }
    for filename, payload in (
        (INDEX_FILENAME, index_payload),
        (MANIFEST_FILENAME, manifest_payload),
    ):
        target = directory / filename
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    return directory


def load_knowledge_index(workspace: str | Path) -> BM25Index | None:
    """加载工作区知识库索引；尚未建库时返回 None（生成链路据此降级）。"""
    path = kb_dir(workspace) / INDEX_FILENAME
    if not path.is_file():
        return None
    return BM25Index.load_json(path)


def search_knowledge(
    workspace: str | Path,
    query: str,
    *,
    k: int = DEFAULT_KB_TOP_K,
) -> list[KnowledgeSnippet]:
    """在已建知识库中检索方法论片段；无索引或无命中返回空列表。"""
    index = load_knowledge_index(workspace)
    if index is None:
        return []
    snippets: list[KnowledgeSnippet] = []
    for hit in index.search(query, k=k):
        doc = index.get(hit.doc_id)
        if doc is None:
            continue
        snippets.append(
            KnowledgeSnippet(
                source=str(doc.meta.get("source", doc.doc_id)),
                heading=str(doc.meta.get("heading", "")),
                text=doc.text[:KB_SNIPPET_CHARS],
                score=hit.score,
            )
        )
    return snippets


def knowledge_brief(snippets: list[dict]) -> str:
    """把命中片段（dict 形态）渲染成骨架 prompt 的"写作方法论参考"区块文本（带来源标注）。"""
    if not snippets:
        return ""
    lines = ["【写作方法论参考】（来自作者本地知识库，借鉴手法与结构，严禁照抄具体作品情节）："]
    for i, snippet in enumerate(snippets, start=1):
        heading = snippet.get("heading") or ""
        title = f"《{heading}》" if heading else ""
        lines.append(f"{i}. 来源 {snippet.get('source', '?')} {title}\n   {snippet.get('text', '')}")
    return "\n".join(lines)
