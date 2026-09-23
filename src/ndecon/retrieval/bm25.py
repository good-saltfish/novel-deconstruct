"""确定性 Okapi BM25 检索器（L1，ADR-0004）。

零依赖纯 Python：
- 参数 k1/b 固定为经典默认值并随索引一起序列化，保证跨机器/跨版本逐位重算；
- 受控词表（基调/主题标签）在查询侧乘固定权重 CONTROLLED_TERM_BOOST；
- 索引只持久化语料（IndexedDocument 列表）与参数，词频/逆文档频率等统计量
  在加载时确定性重算，不缓存浮点中间态，避免序列化漂移；
- 平分时按文档加入索引的先后次序打破并列，排序结果完全确定。
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from ndecon.domain.enums import ThemeTag, Tone
from ndecon.retrieval.tokenize import tokenize

# 经典 Okapi BM25 参数；写死而非暴露配置，杜绝"同索引不同分"
BM25_K1 = 1.5
BM25_B = 0.75
# 受控词表术语的查询侧加权：标签比普通 bigram 更具主题区分度
CONTROLLED_TERM_BOOST = 2.0
INDEX_VERSION = 1


def controlled_terms() -> dict[str, float]:
    """构造受控词表 -> 查询权重的映射（基调 + 主题标签，全部为双字 bigram）。"""
    terms: dict[str, float] = {}
    for enum_cls in (Tone, ThemeTag):
        for member in enum_cls:
            terms[member.value] = CONTROLLED_TERM_BOOST
    return terms


class IndexedDocument(BaseModel):
    """索引中的最小语料单元。"""

    doc_id: str = Field(min_length=1)
    doc_type: str = Field(min_length=1, description="文档类型，如 chapter_outline/chapter_summary")
    order: int | None = Field(default=None, description="章号；非章节文档为空")
    title: str = ""
    text: str = ""
    meta: dict = Field(default_factory=dict, description="附加元数据，不参与打分但随结果返回")


class ScoredDoc(BaseModel):
    """一次检索命中的文档及其分数。"""

    doc_id: str
    score: float


class BM25Index:
    """内存中的 BM25 索引；语料经 :func:`tokenize` 切词后构建统计量。"""

    def __init__(
        self,
        documents: list[IndexedDocument],
        *,
        k1: float = BM25_K1,
        b: float = BM25_B,
        term_boost: dict[str, float] | None = None,
    ) -> None:
        """记录语料与参数，并立即确定性计算词频、文档频率、平均长度等统计量。"""
        self.documents = list(documents)
        self.k1 = k1
        self.b = b
        self.term_boost = controlled_terms() if term_boost is None else dict(term_boost)
        # 标题与正文一起切词：标题命中与正文命中走同一套统计
        self._tokens = [tokenize(f"{doc.title}\n{doc.text}") for doc in self.documents]
        self._tf = [Counter(tokens) for tokens in self._tokens]
        self._lengths = [len(tokens) for tokens in self._tokens]
        self._avgdl = (sum(self._lengths) / len(self._lengths)) if self._lengths else 0.0
        document_frequency: Counter[str] = Counter()
        for tf in self._tf:
            document_frequency.update(tf.keys())
        self._df = dict(document_frequency)
        self._n_docs = len(self.documents)
        self._idf: dict[str, float] = {}
        for term, df in self._df.items():
            # BM25+ 风格的恒正 IDF，避免常见词出现负权重
            self._idf[term] = math.log(1.0 + (self._n_docs - df + 0.5) / (df + 0.5))

    def _score_one(self, doc_pos: int, query_terms: list[str], weights: dict[str, float]) -> float:
        """计算单个文档对查询的 BM25 加权分数。"""
        tf = self._tf[doc_pos]
        length = self._lengths[doc_pos]
        denom_norm = 1.0 - self.b + self.b * (length / self._avgdl) if self._avgdl else 1.0
        score = 0.0
        for term in query_terms:
            freq = tf.get(term, 0)
            if freq == 0:
                continue
            numerator = freq * (self.k1 + 1.0)
            denominator = freq + self.k1 * denom_norm
            score += self._idf.get(term, 0.0) * weights.get(term, 1.0) * (numerator / denominator)
        return score

    def search(
        self,
        query: str | list[str],
        *,
        k: int = 10,
        exclude_ids: set[str] | None = None,
    ) -> list[ScoredDoc]:
        """对自然语言查询或已切词查询返回 top-k；零分文档不返回，分数相同按入索引顺序。"""
        query_terms = tokenize(query) if isinstance(query, str) else list(query)
        if not query_terms or not self.documents:
            return []
        weights = self.term_boost
        excluded = exclude_ids or set()
        ranked: list[tuple[float, int]] = []
        for pos, doc in enumerate(self.documents):
            if doc.doc_id in excluded:
                continue
            score = self._score_one(pos, query_terms, weights)
            if score > 0:
                ranked.append((score, pos))
        # 主排序分数降序；并列时入索引顺序升序，保证确定性
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            ScoredDoc(doc_id=self.documents[pos].doc_id, score=score)
            for score, pos in ranked[:k]
        ]

    def get(self, doc_id: str) -> IndexedDocument | None:
        """按 doc_id 取回文档；不存在返回 None。"""
        for doc in self.documents:
            if doc.doc_id == doc_id:
                return doc
        return None

    def to_dict(self) -> dict:
        """把索引序列化为可 JSON 化的普通字典（仅语料 + 参数，统计量重算）。"""
        return {
            "version": INDEX_VERSION,
            "k1": self.k1,
            "b": self.b,
            "term_boost": dict(sorted(self.term_boost.items())),
            "doc_count": len(self.documents),
            "documents": [doc.model_dump() for doc in self.documents],
        }

    def dump_json(self, path: str | Path) -> None:
        """把索引写入 UTF-8 JSON 文件（调用方负责原子写或落盘位置）。"""
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, payload: dict) -> BM25Index:
        """从字典还原索引并校验版本；统计量在构造时重算。"""
        if payload.get("version") != INDEX_VERSION:
            raise ValueError(f"不支持的索引版本：{payload.get('version')!r}，当前版本 {INDEX_VERSION}")
        documents = [IndexedDocument(**item) for item in payload["documents"]]
        return cls(documents, k1=payload["k1"], b=payload["b"], term_boost=payload.get("term_boost"))

    @classmethod
    def load_json(cls, path: str | Path) -> BM25Index:
        """从 UTF-8 JSON 文件加载索引。"""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
